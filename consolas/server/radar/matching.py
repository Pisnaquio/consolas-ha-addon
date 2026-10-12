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

from .classify import classify_listing_item, detect_lot_size, names_games
from .model import MarketplaceListing


ITEM_KIND_LABELS = {
    "console": "una consola",
    "game": "un juego",
    "accessory": "un accesorio",
    "lot": "un lote",
}


# Señales léxicas. Son heurísticas declaradas, no verdad: por eso alimentan
# razones y confianza, y sólo bloquean cuando la contradicción es explícita.
TESTED_SIGNALS = ("tested", "working", "fully functional", "functional", "probado", "funciona")
UNTESTED_SIGNALS = ("untested", "not tested", "as-is", "as is", "for parts", "parts only", "sin probar")
# Un defecto declarado no es «sin probar»: es probada y con una falla. Medido en
# la primera corrida del portafolio retro: «PS3 Bad HDMI», «GBC Needs LCD».
DEFECT_SIGNALS = (
    "bad", "needs", "defective", "faulty", "dead pixel", "dead pixels", "screen line", "screen lines",
    "no sound", "no audio", "no video", "not reading", "doesn t read", "doesnt read", "won t read",
    "read error", "cracked", "some issues", "has issues",
)
OEM_SIGNALS = ("oem", "original", "genuine", "official")
AFTERMARKET_SIGNALS = ("aftermarket", "third party", "generic", "clone", "replica", "reproduction", "repro")
CIB_SIGNALS = ("cib", "complete in box", "complete with box", "box and manual", "with manual")
BOXED_SIGNALS = ("boxed", "in box", "with box", "w box", "original box", "con caja")
SEALED_SIGNALS = ("sealed", "factory sealed", "new sealed", "brand new sealed")
LOOSE_SIGNALS = ("loose", "cart only", "disc only", "game only", "console only")
CASE_ONLY_SIGNALS = ("case only", "box only", "manual only", "empty case", "no disc", "no game")
REPRODUCTION_SIGNALS = ("reproduction", "repro", "bootleg", "counterfeit", "fake", "pirata")

REGION_SIGNALS = {
    # "jpn" y el nombre japonés de la consola son las dos formas más comunes de
    # declarar una importación japonesa en eBay, y ninguna estaba: un cartucho
    # de Super Famicom pasaba entero por una búsqueda que pedía NTSC-U/C, que es
    # justo lo que el filtro de región existe para evitar — no entra en una
    # consola americana sin adaptador.
    "ntsc-j": (
        "ntsc-j", "japan", "japanese", "jp region", "jpn", "japon", "japonesa",
        "super famicom", "famicom",
    ),
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
    expected_kind: str = "",
    rejects_accessories: bool = False,
) -> MatchVerdict:
    """Aplica los criterios de una búsqueda a una publicación normalizada.

    `expected_kind` es qué tipo de pieza tiene que ser para que esta búsqueda la
    acepte. Quien llama decide si corresponde exigirlo: una búsqueda de lotes o
    de descubrimiento no lo manda, porque justamente busca cosas mezcladas.
    """

    verdict = MatchVerdict()
    capabilities = capabilities or {}
    haystack = normalize(listing.searchable_text())
    item = classify_listing_item(listing.title)
    currency = str(criteria.get("currency") or "USD").upper()
    signals_total = 0
    signals_hit = 0

    # --- Filtros obligatorios: un bloqueo alcanza para descartar ---------------

    # Una búsqueda de consolas no tiene por qué traer juegos sueltos. Sólo
    # bloquea cuando el título lo dice con todas las letras: una deducción por
    # descarte sirve para estimar peso, no para tirar una publicación.
    if expected_kind and item.confident and item.kind != expected_kind:
        verdict.blockers.append(
            f"Es {ITEM_KIND_LABELS.get(item.kind, item.kind)} y esta búsqueda pide "
            f"{ITEM_KIND_LABELS.get(expected_kind, expected_kind)}"
        )

    # Una búsqueda de lotes acepta cosas mezcladas, pero no un lote de fundas o
    # de memory cards: si el título dice accesorio y no nombra juegos, no es.
    if rejects_accessories and item.confident and item.kind == "accessory" and not names_games(listing.title):
        verdict.blockers.append("Es un accesorio, no un lote de juegos")

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

    # Lo que se prefiere sin exigirlo: «con juegos», «2 controles», «translúcida».
    # Nunca bloquea ni queda como pendiente de verificar —que falte no es un
    # problema de la publicación—, pero cuenta como una señal más: una consola
    # que además trae lo preferido sube por encima de una que no.
    prefer_terms = list(criteria.get("preferTerms") or [])
    if prefer_terms:
        signals_total += 1
        preferred = [term for term in prefer_terms if contains_term(haystack, term)]
        if preferred:
            signals_hit += 1
            verdict.matched_terms.extend(preferred)
            verdict.reasons.append(f"Trae lo que preferís: {', '.join(f'«{term}»' for term in preferred)}")

    # Cuántas piezas trae. Es el único criterio cuya evidencia hay que contar en
    # vez de reconocer, y contar leyendo un título es frágil: por eso bloquea
    # sólo contra una cantidad que el vendedor declaró, y un título que no la
    # declara pasa como requisito sin verificar. Descartar por lo que el título
    # no dice tiraría la mitad del mercado de lotes, que se publica enumerando
    # los juegos en vez de contarlos.
    declared = detect_lot_size(listing.title)
    min_lot_size = criteria.get("minLotSize")
    if min_lot_size:
        minimum = int(min_lot_size)
        signals_total += 1
        if declared is None:
            if item.variable_price:
                # "Elegí cuál querés" no es un lote: el precio publicado es el
                # de una pieza a elección, así que no hay cantidad que medir.
                verdict.unverified.append(
                    f"Es un listado «elegí cuál querés»: no hay un lote de {minimum} piezas "
                    "con ese precio"
                )
            else:
                verdict.unverified.append(
                    f"No declara cuántas piezas trae; la búsqueda pide {minimum} o más"
                )
        elif declared < minimum:
            verdict.blockers.append(
                f"Declara {declared} pieza(s) y la búsqueda pide al menos {minimum}"
            )
        else:
            signals_hit += 1
            verdict.reasons.append(f"Declara {declared} piezas, y la búsqueda pide al menos {minimum}")

    # Un lote es barato por lo que cuesta cada pieza, no por su total: 120
    # dólares por veinte juegos es una ganga y por cuatro no. Se calcula sólo
    # sobre una cantidad declarada; sin ella no hay división honesta posible.
    # Si además la búsqueda pide un mínimo de piezas, ese chequeo ya dejó dicho
    # que la cantidad falta, y repetirlo castigaría dos veces lo mismo.
    max_per_piece = criteria.get("maxPricePerPiece")
    if max_per_piece is not None and listing.price_amount is not None:
        ceiling = float(max_per_piece)
        piece_currency = listing.price_currency or currency
        if declared and not item.variable_price:
            signals_total += 1
            per_piece = round(listing.price_amount / declared, 2)
            if per_piece > ceiling:
                verdict.blockers.append(
                    f"Sale {money(per_piece, piece_currency)} por pieza ({declared} declaradas) y la "
                    f"búsqueda acepta hasta {money(ceiling, currency)}"
                )
            else:
                signals_hit += 1
                verdict.reasons.append(
                    f"{money(per_piece, piece_currency)} por pieza ({declared} declaradas)"
                )
        elif not min_lot_size:
            verdict.unverified.append(
                f"No declara cuántas piezas trae: no se puede calcular el precio por pieza "
                f"(la búsqueda acepta hasta {money(ceiling, currency)})"
            )

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
    # Sólo cuando la búsqueda pregunta si funciona: en un juego, «Bad» puede
    # ser parte del nombre.
    defect_signal = any_signal(haystack, DEFECT_SIGNALS) if tested_requirement != "any" else ""
    if defect_signal:
        verdict.blockers.append(f"Declara un defecto («{defect_signal}»)")
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

    # La originalidad se trata al revés que el testeo, y el mercado explica por
    # qué: quien vende un cartucho auténtico rara vez escribe «original», porque
    # se da por sentado; el que sí lo aclara suele ser quien vende una copia.
    # Exigir prueba positiva descartaba el mercado entero — 25 de 25 en la
    # primera calibración real. Así que `required` bloquea ante evidencia de
    # NO originalidad, y la ausencia de declaración queda sin verificar, que es
    # la misma regla que rige al resto del módulo.
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
        else:
            verdict.unverified.append("Originalidad sin declarar: verificar antes de comprar")

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


# Palabras que no distinguen un título de otro: «The Legend of Zelda» se
# reconoce por «legend» y «zelda».
TITLE_STOPWORDS = frozenset({"the", "a", "an", "of", "and", "y", "el", "la", "los", "las", "de"})


def title_tokens(text: str) -> list[str]:
    """Las palabras que identifican un título, sin apóstrofos ni relleno.

    El apóstrofo se borra en vez de separar: «Kirby's» y «Kirbys» tienen que
    ser la misma palabra, y separarlo dejaba una «s» suelta que ninguna
    publicación escribe igual.
    """

    without_apostrophes = re.sub(r"['’`´]", "", str(text or ""))
    return [token for token in normalize(without_apostrophes).split() if token not in TITLE_STOPWORDS]


def best_title_target(title: str, targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Qué título de la búsqueda nombra esta publicación, o `None` si ninguno.

    eBay no devuelve sólo lo que se le pidió: la consulta «Super Mario World
    snes» trae también un Donkey Kong Country barato. Atribuirlo a la consulta
    que lo trajo lo etiquetaba como Super Mario World —con su techo, su precio
    de referencia y su «Registrar compra»—. La publicación se atribuye al
    título que su propio título nombra.

    Un objetivo califica si la publicación trae todas las palabras de la parte
    principal del nombre (antes de los dos puntos), o el subtítulo completo más
    alguna palabra de la parte principal: «Crash Bandicoot Warped» es Crash 3
    aunque no diga «3», y «Zelda Link to the Past» no necesita «Legend».
    Entre los que califican gana el que más palabras del nombre completo
    comparte, y después el de nombre principal más largo: así «Donkey Kong
    Country 2» le gana a «Donkey Kong Country».
    """

    words = set(title_tokens(title))
    best: tuple[int, int] | None = None
    chosen: dict[str, Any] | None = None
    for target in targets:
        name = str(target.get("name") or "")
        if not name:
            continue
        core_text, _, subtitle_text = name.partition(":")
        core = title_tokens(core_text)
        subtitle = title_tokens(subtitle_text)
        if not core:
            continue
        by_core = all(token in words for token in core)
        by_subtitle = (
            bool(subtitle)
            and any(token in words for token in core)
            and all(token in words for token in subtitle)
        )
        if not (by_core or by_subtitle):
            continue
        shared = sum(1 for token in set(core + subtitle) if token in words)
        rank = (shared, len(core))
        if best is None or rank > best:
            best, chosen = rank, target
    return chosen


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
    "best_title_target",
    "contains_term",
    "detect_completeness",
    "detect_region",
    "evaluate_match",
    "normalize",
    "title_tokens",
]
