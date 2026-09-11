"""Decide si una publicación sirve para una búsqueda, y lo explica.

Hasta el slice anterior los criterios se guardaban pero no filtraban nada. Acá
empiezan a decidir: los términos excluidos descartan, los requeridos exigen, el
presupuesto corta y la región contradictoria bloquea.

Tres reglas de producto gobiernan este módulo:

1. **Evidencia visible.** Cada veredicto lista sus razones y sus bloqueos. Nunca
   un número opaco.
2. **Nada se inventa.** Si la fuente no puede confirmar un requisito (por ejemplo
   devolución, cuando no declara `returnPolicy`), no se aprueba ni se descarta:
   queda como requisito sin verificar, explícito en la card.
3. **El texto externo es dato.** Título y descripción se normalizan y se comparan;
   jamás se interpretan como instrucción.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .model import MarketplaceListing


# Señales léxicas. Son heurísticas declaradas, no verdad: por eso alimentan
# razones y confianza, y sólo bloquean cuando la contradicción es explícita.
TESTED_SIGNALS = ("tested", "working", "fully functional", "functional", "probado", "funciona")
UNTESTED_SIGNALS = ("untested", "not tested", "as-is", "as is", "for parts", "parts only", "sin probar")
OEM_SIGNALS = ("oem", "original", "genuine", "official")
AFTERMARKET_SIGNALS = ("aftermarket", "third party", "generic", "clone", "replica", "reproduction", "repro")
CIB_SIGNALS = ("cib", "complete in box", "complete with box", "box and manual", "with manual")
BOXED_SIGNALS = ("boxed", "in box", "with box", "con caja")
SEALED_SIGNALS = ("sealed", "factory sealed", "new sealed", "brand new sealed")
LOOSE_SIGNALS = ("loose", "cart only", "disc only", "game only", "console only")
CASE_ONLY_SIGNALS = ("case only", "box only", "manual only", "empty case", "no disc", "no game")
REPRODUCTION_SIGNALS = ("reproduction", "repro", "bootleg", "counterfeit", "fake", "pirata")

REGION_SIGNALS = {
    "ntsc-j": ("ntsc-j", "japan", "japanese", "jp region", "japon", "japonesa"),
    "pal": ("pal", "europe", "european", "uk region", "australia"),
    "ntsc-u/c": ("ntsc-u", "ntsc u", "usa", "us region", "american", "north america"),
}


def normalize(text: str) -> str:
    """Minúsculas, sin acentos y con separadores unificados, para comparar."""
    lowered = unicodedata.normalize("NFD", str(text or "")).lower()
    stripped = "".join(char for char in lowered if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def contains_term(haystack: str, term: str) -> bool:
    """Coincidencia por palabra completa sobre el texto ya normalizado."""
    needle = normalize(term)
    if not needle:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def any_signal(haystack: str, signals: tuple[str, ...]) -> str:
    for signal in signals:
        if contains_term(haystack, signal):
            return signal
    return ""


def detect_region(haystack: str) -> str:
    for region, signals in REGION_SIGNALS.items():
        if any_signal(haystack, signals):
            return region
    return ""


def money(amount: float | None, currency: str) -> str:
    if amount is None:
        return ""
    return f"{currency or 'USD'} {amount:g}"


@dataclass(slots=True)
class MatchVerdict:
    """Por qué una publicación entra — o no — en una búsqueda."""

    matched: bool = False
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "confidence": round(self.confidence, 3),
            "reasons": list(self.reasons),
            "blockers": list(self.blockers),
            "unverified": list(self.unverified),
            "matchedTerms": list(self.matched_terms),
        }


def evaluate_match(
    listing: MarketplaceListing,
    criteria: dict[str, Any],
    capabilities: dict[str, Any] | None = None,
) -> MatchVerdict:
    """Aplica los criterios de una búsqueda a una publicación normalizada."""

    verdict = MatchVerdict()
    capabilities = capabilities or {}
    haystack = normalize(listing.searchable_text())
    currency = str(criteria.get("currency") or "USD").upper()
    signals_total = 0
    signals_hit = 0

    # --- Filtros obligatorios: un bloqueo alcanza para descartar ---------------

    for term in criteria.get("excludeTerms") or []:
        if contains_term(haystack, term):
            verdict.blockers.append(f"Contiene un término excluido: «{term}»")

    include_terms = list(criteria.get("includeTerms") or [])
    for term in include_terms:
        signals_total += 1
        if contains_term(haystack, term):
            signals_hit += 1
            verdict.matched_terms.append(term)
        else:
            verdict.blockers.append(f"No menciona el término requerido: «{term}»")

    any_terms = list(criteria.get("anyTerms") or [])
    if any_terms:
        signals_total += 1
        hits = [term for term in any_terms if contains_term(haystack, term)]
        if hits:
            signals_hit += 1
            verdict.matched_terms.extend(hits)
            verdict.reasons.append(f"Coincide con {', '.join(f'«{term}»' for term in hits)}")
        else:
            verdict.blockers.append("No coincide con ninguno de los sinónimos aceptados")

    max_item_price = criteria.get("maxItemPrice")
    if max_item_price is not None and listing.price_amount is not None:
        signals_total += 1
        if listing.price_amount > float(max_item_price):
            verdict.blockers.append(
                f"{money(listing.price_amount, listing.price_currency or currency)} supera el máximo "
                f"de {money(float(max_item_price), currency)}"
            )
        else:
            signals_hit += 1
            verdict.reasons.append(f"Dentro del presupuesto: {money(listing.price_amount, listing.price_currency or currency)}")

    max_total = criteria.get("maxTotalUsa")
    if max_total is not None:
        total = listing.total_amount
        if total is None or not listing.shipping_is_known:
            verdict.unverified.append("Costo total sin envío confirmado")
        elif total > float(max_total):
            verdict.blockers.append(
                f"Recibido en USA {money(total, listing.price_currency or currency)} supera "
                f"{money(float(max_total), currency)}"
            )
        else:
            verdict.reasons.append(f"Recibido en USA por {money(total, listing.price_currency or currency)}")

    # --- Condición y completitud ---------------------------------------------

    tested_requirement = str(criteria.get("tested") or "any")
    untested_signal = any_signal(haystack, UNTESTED_SIGNALS)
    tested_signal = "" if untested_signal else any_signal(haystack, TESTED_SIGNALS)
    if tested_requirement != "any":
        signals_total += 1
        if untested_signal:
            message = f"Se declara «{untested_signal}»"
            if tested_requirement == "required":
                verdict.blockers.append(f"{message} y la búsqueda exige una unidad probada")
            else:
                verdict.reasons.append(f"{message}: riesgo a descontar del precio")
        elif tested_signal:
            signals_hit += 1
            verdict.reasons.append(f"Declara estar probada («{tested_signal}»)")
        elif tested_requirement == "required":
            verdict.blockers.append("No declara estar probada y la búsqueda lo exige")
        else:
            verdict.unverified.append("No se puede confirmar si fue probada")
    elif untested_signal:
        verdict.reasons.append(f"Se declara «{untested_signal}»: riesgo explícito")

    original_requirement = str(criteria.get("originalParts") or "any")
    aftermarket_signal = any_signal(haystack, AFTERMARKET_SIGNALS)
    if original_requirement != "any":
        signals_total += 1
        if aftermarket_signal:
            message = f"Declara piezas no originales («{aftermarket_signal}»)"
            if original_requirement == "required":
                verdict.blockers.append(f"{message} y la búsqueda exige originales")
            else:
                verdict.reasons.append(message)
        elif any_signal(haystack, OEM_SIGNALS):
            signals_hit += 1
            verdict.reasons.append("Declara piezas originales/OEM")
        elif original_requirement == "required":
            verdict.blockers.append("No declara piezas originales y la búsqueda lo exige")
        else:
            verdict.unverified.append("Originalidad de las piezas sin confirmar")

    completeness = str(criteria.get("completeness") or "any")
    if completeness != "any":
        signals_total += 1
        detected = detect_completeness(haystack)
        if detected == completeness:
            signals_hit += 1
            verdict.reasons.append(f"Completitud declarada: {completeness.upper()}")
        elif detected:
            verdict.blockers.append(f"Se declara {detected.upper()} y la búsqueda pide {completeness.upper()}")
        else:
            verdict.unverified.append(f"Completitud sin declarar; la búsqueda pide {completeness.upper()}")

    # Una caja vacía nunca satisface una búsqueda de la pieza.
    case_only = any_signal(haystack, CASE_ONLY_SIGNALS)
    if case_only and completeness != "boxed":
        verdict.blockers.append(f"Es sólo el envase: «{case_only}»")

    # Una reproducción no satisface una búsqueda de copia original.
    reproduction = any_signal(haystack, REPRODUCTION_SIGNALS)
    if reproduction:
        if original_requirement == "required":
            verdict.blockers.append(f"Es una reproducción («{reproduction}») y la búsqueda pide original")
        else:
            verdict.reasons.append(f"Posible reproducción («{reproduction}»): verificar antes de comprar")

    # --- Región ---------------------------------------------------------------

    wanted_region = normalize(criteria.get("region") or "")
    if wanted_region:
        signals_total += 1
        detected_region = detect_region(haystack)
        normalized_wanted = detect_region(wanted_region) or wanted_region
        if detected_region and detected_region != normalized_wanted:
            verdict.blockers.append(
                f"Región detectada {detected_region.upper()}, la búsqueda pide {str(criteria['region']).upper()}"
            )
        elif detected_region:
            signals_hit += 1
            verdict.reasons.append(f"Región {detected_region.upper()} como pide la búsqueda")
        else:
            verdict.unverified.append(f"Región sin declarar; la búsqueda pide {str(criteria['region']).upper()}")

    # --- Requisitos que la fuente todavía no puede confirmar -------------------

    if criteria.get("returnsRequired") is True and not capabilities.get("returnPolicy"):
        verdict.unverified.append("Esta fuente todavía no informa la política de devolución")
    if criteria.get("freeShippingOnly") is True and not listing.shipping_is_known:
        verdict.unverified.append("Envío sin importe confirmado")

    # --- Tipo de venta --------------------------------------------------------

    if listing.listing_kind == "auction":
        verdict.reasons.append("Es una subasta: el precio puede subir antes del cierre")

    verdict.matched = not verdict.blockers
    verdict.confidence = confidence_for(signals_hit, signals_total, verdict)
    if verdict.matched and not verdict.reasons:
        verdict.reasons.append("Coincide con la consulta de la búsqueda")
    return verdict


def detect_completeness(haystack: str) -> str:
    """El orden importa: «complete in box» es CIB, no simplemente boxed."""
    if any_signal(haystack, SEALED_SIGNALS):
        return "sealed"
    if any_signal(haystack, CIB_SIGNALS):
        return "cib"
    if any_signal(haystack, LOOSE_SIGNALS):
        return "loose"
    if any_signal(haystack, BOXED_SIGNALS):
        return "boxed"
    return ""


def confidence_for(signals_hit: int, signals_total: int, verdict: MatchVerdict) -> float:
    """Confianza explicable: señales cumplidas, menos lo que quedó sin verificar."""
    if verdict.blockers:
        return 0.0
    base = 0.6 if signals_total == 0 else 0.4 + 0.6 * (signals_hit / signals_total)
    penalty = 0.08 * len(verdict.unverified)
    return max(0.05, min(1.0, round(base - penalty, 3)))


__all__ = [
    "MatchVerdict",
    "contains_term",
    "detect_completeness",
    "detect_region",
    "evaluate_match",
    "normalize",
]
