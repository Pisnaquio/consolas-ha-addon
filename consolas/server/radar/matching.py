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

from .classify import (
    KIT_PIECE_LABELS,
    REPRO_PACKAGING_MARKERS,
    analyze_lot,
    assess_authenticity,
    assess_console_kit,
    classify_listing_item,
    detect_hardware_origin,
    detect_lot_size,
    names_games,
    without_negated_completeness,
)
from .model import MarketplaceListing
from .platforms import PROFILES, RETRO_CARTRIDGE_KEYS, fuse_platform_numbers, resolve_platform
from .taxonomy import assign_objective


ITEM_KIND_LABELS = {
    "console": "una consola",
    "game": "un juego",
    "accessory": "un accesorio",
    "lot": "un lote",
}

# Qué clase de «no es el producto» es, dicho como lo va a leer el dueño.
ITEM_CATEGORY_LABELS = {
    "accessory": "un accesorio",
    "printed": "material impreso",
    "merch": "merchandising",
    "part": "una pieza de reemplazo",
}

# Cajas, fundas y protectores «para» juegos: el lote es de estuches aunque el
# título diga «games».
_CASES_FOR_GAMES = re.compile(r"\b(?:cases?|protectors?|sleeves?|covers?|boxes)\s+(?:for|fits)\b")


# Señales léxicas. Son heurísticas declaradas, no verdad: por eso alimentan
# razones y confianza, y sólo bloquean cuando la contradicción es explícita.
TESTED_SIGNALS = ("tested", "working", "fully functional", "functional", "probado", "funciona")
UNTESTED_SIGNALS = ("untested", "not tested", "as-is", "as is", "for parts", "parts only", "sin probar")
# Un defecto declarado no es «sin probar»: es probada y con una falla. Medido en
# la primera corrida del portafolio retro: «PS3 Bad HDMI», «GBC Needs LCD».
#
# Lo que impide usar la unidad descarta; lo que la deja jugable pero dañada
# (líneas en la pantalla, decoloración, un puerto flojo) no descarta: lo lee
# `classify.assess_console_kit` y la valuación lo penaliza a la vista. Un
# «working with issues» sin decir cuáles es una falla de alcance desconocido y
# se trata como «some issues».
DEFECT_SIGNALS = (
    "bad", "needs", "defective", "faulty",
    "no sound", "no audio", "no video", "no display", "no picture", "no signal",
    "not reading", "doesn t read", "doesnt read", "won t read", "read error",
    "cracked", "some issues", "has issues", "with issues",
    # PS3: la luz amarilla y el HDMI muerto son las dos fallas que la dejan inservible.
    "ylod", "yellow light", "yellow light of death", "blod", "rrod", "red ring",
    "no hdmi", "hdmi not working", "hdmi doesn t work", "hdmi doesnt work", "hdmi broken",
    "broken hdmi", "hdmi issue", "hdmi issues", "hdmi port damaged",
)
OEM_SIGNALS = ("oem", "original", "genuine", "official")
AFTERMARKET_SIGNALS = ("aftermarket", "third party", "generic", "clone", "replica", "reproduction", "repro")
CIB_SIGNALS = ("cib", "complete in box", "complete with box", "box and manual", "with manual")
BOXED_SIGNALS = ("boxed", "in box", "with box", "w box", "original box", "con caja")
SEALED_SIGNALS = ("sealed", "factory sealed", "new sealed", "brand new sealed")
LOOSE_SIGNALS = ("loose", "cart only", "disc only", "game only", "console only")
CASE_ONLY_SIGNALS = ("case only", "box only", "manual only", "empty case", "no disc", "no game")
REPRODUCTION_SIGNALS = ("reproduction", "repro", "bootleg", "counterfeit", "fake", "pirata")
NEGATED_COPY_TEXT = (
    r"\b(?:not|no|non|never|zero)\s+(?:a\s+|an\s+)?"
    r"(?:repro(?:duction)?s?|fakes?|bootlegs?|counterfeits?|clones?|copy|copies)\b"
)


def without_copy_noise(haystack: str) -> str:
    """El texto sin negaciones («not a repro») ni envases repro («repro box»).

    Ninguna de las dos dice que la pieza sea una copia: la primera dice que no
    lo es, y la segunda habla de la caja. Que la caja no sea original lo decide
    la taxonomía (no es CIB), no el filtro de reproducciones.
    """

    return re.sub(NEGATED_COPY_TEXT, " ", re.sub("|".join(REPRO_PACKAGING_MARKERS), " ", haystack))

REGION_SIGNALS = {
    # "jpn" y el nombre japonés de la consola son las dos formas más comunes de
    # declarar una importación japonesa en eBay, y ninguna estaba: un cartucho
    # de Super Famicom pasaba entero por una búsqueda que pedía NTSC-U/C, que es
    # justo lo que el filtro de región existe para evitar — no entra en una
    # consola americana sin adaptador.
    "ntsc-j": (
        "ntsc-j", "japan", "japanese", "jp region", "jpn", "japon", "japonesa",
        "super famicom", "famicom", "sfc", "jp",
        # «3DS LL» es el nombre japonés de la 3DS XL.
        "ll",
    ),
    # PEGI es la calificación por edad europea: sólo la lleva una caja PAL.
    # «MotorStorm: Pacific Rift (PS3) PEGI 16+» no avisaba la región.
    "pal": ("pal", "europe", "european", "uk region", "australia", "pegi"),
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
    # Lo que se leyó del título con estructura —plataforma, originalidad, qué
    # trae la consola, cuánto declara el lote— para que la valuación decida
    # con datos y no releyendo frases.
    assessment: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "confidence": round(self.confidence, 3),
            "reasons": list(self.reasons),
            "blockers": list(self.blockers),
            "unverified": list(self.unverified),
            "matchedTerms": list(self.matched_terms),
            "assessment": dict(self.assessment),
        }


def evaluate_match(
    listing: MarketplaceListing,
    criteria: dict[str, Any],
    capabilities: dict[str, Any] | None = None,
    expected_kind: str = "",
    rejects_accessories: bool = False,
    *,
    platform: str = "",
    search_type: str = "",
) -> MatchVerdict:
    """Aplica los criterios de una búsqueda a una publicación normalizada.

    `expected_kind` es qué tipo de pieza tiene que ser para que esta búsqueda la
    acepte. Quien llama decide si corresponde exigirlo: una búsqueda de lotes o
    de descubrimiento no lo manda, porque justamente busca cosas mezcladas.

    `platform` es la de la búsqueda, como la escribió el dueño; sin ella se usa
    la que nombre el título. Decide qué reglas de hardware aplican —un cartucho
    retro, una consola de disco, una portátil—, nunca qué consola es.
    """

    verdict = MatchVerdict()
    capabilities = capabilities or {}
    haystack = normalize(listing.searchable_text())
    item = classify_listing_item(listing.title)
    profile = resolve_platform(platform, listing.title)
    platform_key = profile.key if profile else ""
    lot_search = rejects_accessories or search_type == "lot"
    console_search = expected_kind == "console" or search_type == "console"
    original_requirement = str(criteria.get("originalParts") or "any")
    currency = str(criteria.get("currency") or "USD").upper()
    signals_total = 0
    signals_hit = 0
    verdict.assessment = {
        "platform": platform_key,
        "itemKind": item.kind,
        "itemConfident": item.confident,
        "category": item.category,
        "searchType": search_type or ("lot" if lot_search else ""),
        "originalParts": original_requirement,
    }

    # --- Filtros obligatorios: un bloqueo alcanza para descartar ---------------

    # Una búsqueda de consolas no tiene por qué traer juegos sueltos. Sólo
    # bloquea cuando el título lo dice con todas las letras: una deducción por
    # descarte sirve para estimar peso, no para tirar una publicación.
    if expected_kind and item.confident and item.kind != expected_kind:
        label = (
            ITEM_CATEGORY_LABELS.get(item.category, ITEM_KIND_LABELS["accessory"])
            if item.kind == "accessory"
            else ITEM_KIND_LABELS.get(item.kind, item.kind)
        )
        verdict.blockers.append(
            f"Es {label} y esta búsqueda pide {ITEM_KIND_LABELS.get(expected_kind, expected_kind)}"
        )

    # Una búsqueda de lotes acepta cosas mezcladas, pero no un lote de fundas o
    # de memory cards: si el título dice accesorio y no nombra juegos, no es.
    # Material impreso, merchandising, repuestos y estuches «para» juegos
    # tampoco, aunque digan «games»: «Lot of 10 Cartridge Cases for Game Boy
    # Games» son diez estuches.
    if lot_search and item.confident and item.kind == "accessory" and (
        not names_games(listing.title)
        or item.category in {"printed", "merch", "part"}
        or _CASES_FOR_GAMES.search(listing.title.lower())
    ):
        label = ITEM_CATEGORY_LABELS.get(item.category, ITEM_KIND_LABELS["accessory"])
        verdict.blockers.append(
            "Es un accesorio, no un lote de juegos"
            if item.category in {"", "accessory"}
            else f"Es {label}, no un lote de juegos"
        )

    # --- Objetivo canónico: con quién se compara esta publicación -------------
    # Lote, juego loose o CIB, consola loose o CIB. Es de la publicación, no de
    # la búsqueda: una búsqueda de consola trae consolas sueltas y en caja, y
    # cada una se mide contra sus pares.
    objective = assign_objective(
        listing.title,
        search_type="lot" if lot_search else ("console" if console_search else search_type),
        item_kind=item.kind,
        item_confident=item.confident,
        platform=platform_key,
    )
    verdict.assessment["objective"] = objective.to_dict()

    # --- Lotes: cuántos juegos, de qué plataforma y si son distintos -----------
    lot = None
    if lot_search:
        lot = analyze_lot(listing.title, platform_key, item)
        verdict.assessment["lot"] = {
            "pieces": lot.pieces,
            "quantity": lot.quantity,
            "note": lot.quantity_note,
            "filler": list(lot.filler),
            "otherPlatforms": list(lot.other_platforms),
        }
        if item.confident and item.kind == "console":
            verdict.blockers.append(
                "Es una consola con juegos, no un lote de juegos: la búsqueda de consola de esa plataforma ya la mira"
            )
        elif lot.quantity == "single":
            verdict.blockers.append("Es un juego suelto, no un lote")
        if lot.mystery:
            verdict.blockers.append(f"Es un lote sorpresa («{lot.mystery}»): no se sabe qué juegos trae")
        if lot.copies:
            verdict.blockers.append(f"Son copias repetidas («{lot.copies}»), no una biblioteca")
        if len(lot.other_platforms) >= 2:
            names = ", ".join(PROFILES[key].label if key in PROFILES else key for key in lot.other_platforms)
            wanted = profile.label if profile else "la plataforma buscada"
            verdict.blockers.append(f"Mezcla plataformas ({names}): no se puede atribuir la mayoría a {wanted}")

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
    quantity_noted = False
    if lot is not None and lot.quantity == "unconfirmed" and lot.pieces is not None:
        quantity_noted = True
        # Declara un número, pero no de qué: con una consola adentro o juegos de
        # otra plataforma, el número no cuenta juegos de esta. Un número menor
        # que el mínimo igual descarta: si el lote entero es más chico, sus
        # juegos también.
        if min_lot_size and lot.pieces < int(min_lot_size):
            verdict.blockers.append(
                f"Declara {lot.pieces} pieza(s) y la búsqueda pide al menos {int(min_lot_size)}"
            )
        else:
            verdict.unverified.append(lot.quantity_note)
        declared = None
        min_lot_size = None
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
        elif not min_lot_size and not quantity_noted:
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
    aftermarket_signal = any_signal(without_copy_noise(haystack), AFTERMARKET_SIGNALS)
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
        elif platform_key in RETRO_CARTRIDGE_KEYS and item.kind in {"game", "lot"}:
            # Un cartucho retro tiene mercado de copias: lo que hay que mirar es
            # concreto, y la card lo dice así.
            verdict.unverified.append(
                "Originalidad sin declarar: mirá etiqueta, placa y tornillos en las fotos antes de comprar"
            )
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

    # Una reproducción no satisface una búsqueda de copia original. «Not a
    # repro» es una negación, no una confesión.
    is_console_item = item.kind == "console" or (console_search and not item.confident)
    reproduction = any_signal(without_copy_noise(haystack), REPRODUCTION_SIGNALS)
    authenticity = None
    if not is_console_item and item.kind != "accessory":
        # En un juego o un lote de cartuchos retro la copia también se delata
        # por cómo se publica: «Game Card», «Compatible N64 Cartridge», «Custom».
        authenticity = assess_authenticity(listing.title, platform_key)
        verdict.assessment["authenticity"] = {
            "status": authenticity.status,
            "signal": authenticity.signal,
            "retroCartridge": platform_key in RETRO_CARTRIDGE_KEYS,
        }
        if not reproduction and authenticity.status == "copy":
            reproduction = authenticity.signal
    if reproduction:
        blocks = original_requirement == "required" or (
            original_requirement == "preferred" and not is_console_item and item.kind != "accessory"
        )
        if blocks:
            verdict.blockers.append(f"Es una reproducción («{reproduction}») y la búsqueda pide original")
        else:
            verdict.reasons.append(f"Posible reproducción («{reproduction}»): verificar antes de comprar")
    elif authenticity is not None and authenticity.status == "suspect":
        verdict.unverified.append(
            f"Tiene marcas típicas de copia («{authenticity.signal}»): mirá etiqueta y placa en las fotos"
        )

    # --- Consola: original, completa y con el lector probado -------------------
    if console_search and item.kind in {"console", "game"} and is_console_item:
        origin, origin_signal = detect_hardware_origin(listing.title)
        if origin:
            what = "una reedición" if origin == "reissue" else "un clon"
            verdict.assessment["origin"] = {"status": origin, "signal": origin_signal}
            if original_requirement != "any":
                verdict.blockers.append(f"Es {what} («{origin_signal}»), no la consola original")
            else:
                verdict.reasons.append(f"Es {what} («{origin_signal}»): no es la consola original")
        kit = assess_console_kit(listing.title, platform_key)
        verdict.assessment["kit"] = {
            "status": kit.status,
            "present": list(kit.present),
            "missing": list(kit.missing),
            "undeclared": list(kit.undeclared),
            "issues": list(kit.issues),
            "reader": kit.reader,
            "readerLabel": kit.reader_label,
        }
        labels = lambda pieces: " ni ".join(KIT_PIECE_LABELS.get(piece, piece) for piece in pieces)  # noqa: E731
        if kit.status == "ready":
            verdict.reasons.append("Lista para usar: declara todo lo que hace falta para jugar")
        elif kit.status == "cib":
            verdict.reasons.append("Completa en caja")
        if kit.undeclared and kit.status not in {"console-only"}:
            verdict.unverified.append(f"No dice si trae {labels(kit.undeclared)}")
        if kit.reader == "powers-only":
            verdict.unverified.append(f"Sólo dice que enciende: falta confirmar que lea {kit.reader_label}")
        elif kit.reader == "partial":
            verdict.unverified.append(f"Dice que lee juegos, pero no si lee {kit.reader_label}")
        elif kit.reader == "unconfirmed":
            verdict.unverified.append(f"Falta confirmar que lea {kit.reader_label}")

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

    # Regiones que el dueño no quiere ver, aunque se acepte cualquier otra: hoy
    # las japonesas, que no puede jugar (formato de cartucho e idioma). Se lee
    # sólo el título, y sin «made in Japan»: la mitad de las consolas americanas
    # se fabricaron allá y lo dicen.
    #
    # Lo que se envía desde Japón cuenta como japonés aunque el título no lo
    # diga: medido el 2026-10-12, 23 publicaciones despachadas desde allá
    # —3DS «LL», N64 y PS2— pasaban porque sólo se miraba el título. Las
    # «region free» también quedan afuera: son japonesas modificadas, y la
    # excepción del perfil para una así es algo que el dueño pide, no algo que
    # el radar le propone.
    excluded_regions = [str(region) for region in criteria.get("excludeRegions") or []]
    if excluded_regions:
        region = detect_region(normalize(listing.title).replace("made in japan", " "))
        if not region and str(listing.location_label or "").strip().upper() == "JP":
            region = "ntsc-j"
        if region in excluded_regions:
            verdict.blockers.append(f"Es una edición {region.upper()} y la búsqueda excluye esa región")

    # Cualquier otra región es válida, pero no se juega igual: lo que no es
    # NTSC-U/C pide confirmar compatibilidad e idioma antes de comprar.
    title_region = detect_region(normalize(listing.title).replace("made in japan", " "))
    if title_region in {"pal", "ntsc-j"} and title_region not in excluded_regions and not wanted_region:
        verdict.unverified.append(
            f"Edición {title_region.upper()}: confirmá que corra en tu consola (región y formato) y en qué idioma está"
        )

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

# Un número de secuela se escribe de las dos maneras: «God of War III» y «God
# of War 3» son el mismo juego. Quedan afuera «i» y «x», que en un título casi
# siempre son letras («Pokémon X», «Mega Man X»).
ROMAN_NUMERALS = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9"}

# Un número de secuela, ya leído como arábigo: del 2 al 9. Uno más largo es
# otra cosa —«64», «360», un año— y «x2» ni siquiera es un número suelto.
SEQUEL_NUMBERS = frozenset("23456789")

# Lo que vuelve cantidad al número que lo precede: «Red Dead Redemption 2
# discs», «Mario Kart 64 2 player», «Final Fantasy VII 3 disc set» dicen cuánto
# trae, no qué juego es. «Super Mario Bros 5 screw» es la tirada del cartucho.
QUANTITY_WORDS = frozenset({
    "disc", "discs", "disk", "disks", "cd", "cds", "dvds",
    "player", "players", "games", "controllers", "remotes", "copies",
    "cartridges", "carts", "pack", "packs", "pk", "pcs", "pieces",
    "screw", "screws",
})
# Salvo «disc only», que es como se publica un disco suelto: «Dark Souls 2 disc
# only» es el disco de Dark Souls II, no dos discos.
LOOSE_MEDIA_WORDS = frozenset({"disc", "disk", "cd"})

# Las palabras con que se nombra una compilación, cada una por su forma
# canónica: «Vol. 2» y «Volume II» dicen lo mismo. «Series» no está: el
# catálogo de eBay la usa de etiqueta de franquicia («Kirby Series», «God of
# War Series») en publicaciones de un solo juego.
COMPILATION_WORDS = {
    "collection": "collection", "collections": "collection",
    "trilogy": "trilogy", "trilogies": "trilogy", "quadrilogy": "quadrilogy",
    "saga": "saga",
    "anthology": "anthology", "anthologies": "anthology",
    "compilation": "compilation", "compilations": "compilation",
    "volume": "volume", "volumes": "volume", "vol": "volume",
}
# «From my collection» es la colección del vendedor, no la de un juego.
SELLER_COLLECTION_WORDS = frozenset({"my", "personal", "private"})


def title_tokens(text: str) -> list[str]:
    """Las palabras que identifican un título, sin apóstrofos ni relleno.

    El apóstrofo se borra en vez de separar: «Kirby's» y «Kirbys» tienen que
    ser la misma palabra, y separarlo dejaba una «s» suelta que ninguna
    publicación escribe igual. Los números romanos se leen como arábigos, y el
    de una consola («PlayStation 3») queda pegado a ella: no es el de un juego.
    """

    without_apostrophes = re.sub(r"['’`´]", "", str(text or ""))
    return [
        ROMAN_NUMERALS.get(token, token)
        for token in fuse_platform_numbers(normalize(without_apostrophes)).split()
        if token not in TITLE_STOPWORDS
    ]


def is_sequel_number(tokens: list[str], index: int) -> bool:
    """Si el token en `index` es el número de una secuela y no una cantidad."""

    if index >= len(tokens) or tokens[index] not in SEQUEL_NUMBERS:
        return False
    following = tokens[index + 1 : index + 3]
    if not following:
        return True
    noun = following[0]
    if noun == "in" and following[1:] and following[1].isdigit():
        # «God of War Collection 2 in 1»: una compilación, no una secuela.
        return False
    if noun in QUANTITY_WORDS:
        return noun in LOOSE_MEDIA_WORDS and following[1:] == ["only"]
    return True


def names_a_sequel(tokens: list[str], core: list[str], subtitle: list[str]) -> bool:
    """Si cada vez que la publicación nombra el título le sigue un número que el título no tiene.

    «Dark Souls II», «BioShock 2», «Sonic the Hedgehog 2»: el nombre está
    entero, pero lo que viene después dice que es otro juego. Se mira el nombre
    completo si la publicación trae el subtítulo, y si no, la parte principal:
    «Halo 2» no es «Halo: Combat Evolved», pero «Spyro 3 Year of the Dragon» sí
    es «Spyro: Year of the Dragon» —el subtítulo lo nombra con todas las
    letras— y «Star Wars Knights of the Old Republic II» no es el primero.

    Basta con que una mención quede sin número para que la publicación lo
    nombre: «Dark Souls & Dark Souls II» trae el primero. Y un título que ya
    termina en número no tiene secuela que confundir: lo que le sigue es el
    mismo número escrito otra vez («Final Fantasy VII 7», «Tomb Raider II 2»,
    medido en las corridas de octubre) o una lista («Mass Effect 2/3»).
    """

    words = set(tokens)
    name = core + subtitle if all(token in words for token in subtitle) else core
    if name[-1].isdigit():
        return False
    size = len(name)
    ends = [index + size for index in range(len(tokens) - size + 1) if tokens[index : index + size] == name]
    return bool(ends) and all(is_sequel_number(tokens, end) for end in ends)


def compilation_words(tokens: list[str]) -> set[str]:
    """Qué palabras de compilación trae un título, en su forma canónica."""

    return {
        COMPILATION_WORDS[token]
        for index, token in enumerate(tokens)
        if token in COMPILATION_WORDS
        and not (
            COMPILATION_WORDS[token] == "collection" and index and tokens[index - 1] in SELLER_COLLECTION_WORDS
        )
    }


def names_a_compilation(tokens: list[str], core: list[str], subtitle: list[str]) -> bool:
    """Si la publicación nombra una compilación que el título no es.

    «Mass Effect 2 - Trilogy … DISC ONLY» trae el nombre entero de Mass Effect
    2, pero es el disco de la Trilogy; «Uncharted Trilogy 1 2 3 Bundle Lot» son
    tres juegos. Basta una palabra de compilación que el nombre del objetivo no
    tenga para que sea otro producto. Un objetivo que ya es una compilación
    acepta la suya —«God of War Collection», «God of War: Origins
    Collection»— pero no otra: «God of War Saga» no es Origins Collection, y
    «God of War Collection Volume II» sólo es de quien lo tenga por nombre.
    """

    return bool(compilation_words(tokens) - compilation_words(core + subtitle))


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

    Cuando la parte principal es una serie que otro objetivo de la búsqueda
    también nombra —«God of War: Ascension» junto a «God of War III», o
    Ocarina of Time junto a Majora's Mask— la serie sola no dice qué juego es:
    hace falta además alguna palabra del subtítulo. Si no, «God of War Saga» o
    «Legend of Zelda N64» quedaban etiquetados con el primero de la lista.

    Y un objetivo sin número no nombra su secuela: «Dark Souls II» no es Dark
    Souls aunque traiga sus dos palabras (ver `names_a_sequel`). Sin esto, la
    secuela que la búsqueda no persigue se juzgaba con el techo y el precio del
    original, y «Registrar compra» escribía el juego equivocado.

    Por lo mismo, un objetivo no nombra una compilación que no es: «Mass Effect
    2 - Trilogy» no es Mass Effect 2 ni «God of War Saga» es Origins Collection
    (ver `names_a_compilation`).

    Un objetivo puede tener otros nombres (`aliases`): «Little Big Planet 2»
    para «LittleBigPlanet 2», «God of War Collection Volume II» para la edición
    europea de Origins Collection. Cada uno se lee como el nombre y compite con
    los demás nombres de la búsqueda; si gana, gana su objetivo.
    """

    tokens = title_tokens(title)
    words = set(tokens)
    parsed: list[tuple[dict[str, Any], list[str], list[str]]] = []
    for target in targets:
        if not str(target.get("name") or ""):
            continue
        for name in (target.get("name"), *(target.get("aliases") or [])):
            core_text, _, subtitle_text = str(name or "").partition(":")
            core = title_tokens(core_text)
            if core:
                parsed.append((target, core, title_tokens(subtitle_text)))

    best: tuple[int, int] | None = None
    chosen: dict[str, Any] | None = None
    for target, core, subtitle in parsed:
        core_is_series = bool(subtitle) and any(
            other is not target and set(core) <= set(other_core + other_subtitle)
            for other, other_core, other_subtitle in parsed
        )
        by_core = all(token in words for token in core) and (
            not core_is_series or any(token in words for token in subtitle)
        )
        by_subtitle = (
            bool(subtitle)
            and any(token in words for token in core)
            and all(token in words for token in subtitle)
        )
        if not (by_core or by_subtitle) or names_a_sequel(tokens, core, subtitle):
            continue
        if names_a_compilation(tokens, core, subtitle):
            continue
        shared = sum(1 for token in set(core + subtitle) if token in words)
        rank = (shared, len(core))
        if best is None or rank > best:
            best, chosen = rank, target
    return chosen


def detect_completeness(haystack: str) -> str:
    """El orden importa: «complete in box» es CIB, no simplemente boxed.

    «No CIB» o «not sealed» niegan, no declaran: se sacan antes de leer.
    """
    haystack, _ = without_negated_completeness(haystack)
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
