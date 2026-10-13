"""Costo real, benchmark comparable y score explicable.

Tres ideas ordenan este módulo, todas del PRD §10:

1. **Costo total, no precio de portada.** Artículo y envío viajan separados y se
   declara cuál es exacto y cuál estimado. Nunca se esconde el envío dentro de
   un solo número.
2. **Condición comparable.** Loose se compara con loose y CIB con CIB. Una
   consola loose al precio de un CIB queda cara, que es justamente lo que hay
   que ver.
3. **Evidencia visible.** El score dice de dónde sale cada punto. Un número
   opaco no ayuda a decidir.

Y una regla heredada de la auditoría de precios: **dos campos que copian la
misma fuente no son dos evidencias.** El catálogo actual guarda `precioEbaySold`
como proxy de PriceCharting; acá eso se detecta y se marca como no
independiente, en vez de contarlo como una corroboración que no existe.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any


# Bandas del PRD §10.3, como cota superior de cada una sobre el benchmark.
DECISION_BANDS = (
    ("ganga", 0.75, "Ganga real"),
    ("buena", 0.90, "Buena compra"),
    ("razonable", 1.10, "Precio razonable"),
    ("premium", 1.30, "Premium justificable"),
)
EXPENSIVE_BAND = ("caro", "Caro")

# Lo que el precio solo no puede decidir. Un cartucho retro sin originalidad
# declarada, una consola con daño declarado o un clon pueden ser baratos, y
# barato no los vuelve buena compra: el veredicto es «revisar», con el precio
# al lado para que se vea qué se pierde si resulta mala.
REVIEW_BAND = ("revisar", "Requiere revisión")
NO_REFERENCE_BAND = ("sin-referencia", "Sin referencia")
POSITIVE_BANDS = frozenset({"ganga", "buena", "razonable"})

# Orden de preferencia de evidencia (PRD §10.2). Menor es mejor.
SOURCE_RANK = {
    "ebay-sold": 0, "pricecharting": 1, "retail": 2, "manual": 3, "peer-listings": 4, "editorial": 5,
    "lot-ceiling": 6,
}

SOURCE_LABELS = {
    "ebay-sold": "ventas cerradas en eBay",
    "pricecharting": "PriceCharting",
    "retail": "retail observado",
    "manual": "referencia manual",
    "peer-listings": "publicaciones activas comparables",
    "editorial": "rango editorial",
    "lot-ceiling": "tu techo por pieza",
}

# Pesos del score (PRD §10.4).
WEIGHTS = {
    "collection": 25,
    "price": 25,
    "condition": 15,
    "completeness": 15,
    "seller": 10,
    "logistics": 10,
}

STALE_AFTER_DAYS = 180

# Cuánto cuesta dejar lista para usar una consola a la que le falta algo, en
# USD de un repuesto OEM usado típico. Son estimaciones declaradas, igual que
# los pesos del courier: la card dice cuánto se sumó y por qué. Del lado
# conservador a propósito, porque subestimar lo que falta es justamente lo que
# hacía que una consola sola le ganara a un bundle completo.
COMPLETION_COSTS_USD = {
    "controller": 20.0,
    "cables": 15.0,
    "jumper-pak": 10.0,
    "battery-cover": 6.0,
    "charger": 10.0,
}
COMPLETION_LABELS = {
    "controller": "control",
    "cables": "cables",
    "jumper-pak": "Jumper Pak",
    "battery-cover": "tapa de pilas",
    "charger": "cargador",
}

# Un lote con mucho relleno —deportivos anuales, shovelware, duplicados— no
# vale por pieza lo que vale uno de títulos útiles: cada pieza se mide contra
# esta fracción del techo por pieza del dueño.
LOT_FILLER_FACTOR = 0.5

# Por debajo de esto la coincidencia es demasiado incierta para afirmar una
# ganga, aunque el precio lo sea.
BARGAIN_MIN_CONFIDENCE = 0.6


@dataclass(slots=True)
class PriceReference:
    """Un precio con su procedencia. Sin procedencia no es evidencia."""

    entity_id: str
    source: str
    value: float
    currency: str = "USD"
    completeness: str = "loose"
    condition: str = "used"
    region: str = ""
    observed_at: str = ""
    confidence: float = 0.6
    independent: bool = True
    notes: str = ""

    def age_days(self, today: date | None = None) -> int | None:
        if not self.observed_at:
            return None
        try:
            observed = date.fromisoformat(self.observed_at[:10])
        except ValueError:
            return None
        return ((today or datetime.now(timezone.utc).date()) - observed).days

    def is_stale(self, today: date | None = None) -> bool:
        age = self.age_days(today)
        return age is not None and age > STALE_AFTER_DAYS

    def to_dict(self, today: date | None = None) -> dict[str, Any]:
        return {
            "entityId": self.entity_id,
            "source": self.source,
            "sourceLabel": SOURCE_LABELS.get(self.source, self.source),
            "value": self.value,
            "currency": self.currency,
            "completeness": self.completeness,
            "condition": self.condition,
            "region": self.region,
            "observedAt": self.observed_at,
            "ageDays": self.age_days(today),
            "stale": self.is_stale(today),
            "confidence": round(self.confidence, 2),
            "independent": self.independent,
            "notes": self.notes,
        }


def to_amount(value: Any) -> float | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount != amount or amount <= 0:
        return None
    return round(amount, 2)


def references_from_console_entry(entry: dict[str, Any]) -> list[PriceReference]:
    """Extrae referencias del catálogo, marcando las que no son independientes.

    `precioObjetivoCompra` no entra: es una regla interna derivada de los otros
    campos, no una observación del mercado. Usarla como benchmark sería
    compararse contra uno mismo.
    """

    entity_id = str(entry.get("id") or "")
    notes = entry.get("precioNotas") if isinstance(entry.get("precioNotas"), dict) else {}
    sources = notes.get("fuentes") if isinstance(notes.get("fuentes"), dict) else {}
    observed_at = str(notes.get("verificadoEn") or "")

    references: list[PriceReference] = []

    pricechart = to_amount(entry.get("precioPriceChart"))
    if pricechart is not None:
        references.append(
            PriceReference(
                entity_id=entity_id, source="pricecharting", value=pricechart, completeness="loose",
                observed_at=observed_at, confidence=0.8, notes=str(sources.get("precioPriceChart") or ""),
            )
        )

    sold = to_amount(entry.get("precioEbaySold"))
    if sold is not None:
        note = str(sources.get("precioEbaySold") or "")
        # Dos señales de que no es evidencia propia: lo dice la nota, o repite el
        # valor de PriceCharting al centavo.
        proxy = "proxy" in note.lower() or (pricechart is not None and abs(sold - pricechart) < 0.01)
        references.append(
            PriceReference(
                entity_id=entity_id, source="ebay-sold", value=sold, completeness="loose",
                observed_at=observed_at, confidence=0.35 if proxy else 0.9,
                independent=not proxy,
                notes=note or ("Copia de PriceCharting: no es evidencia independiente" if proxy else ""),
            )
        )

    cib = to_amount(entry.get("precioCIB"))
    if cib is not None:
        references.append(
            PriceReference(
                entity_id=entity_id, source="pricecharting", value=cib, completeness="cib",
                observed_at=observed_at, confidence=0.8, notes=str(sources.get("precioCIB") or ""),
            )
        )

    retail = to_amount(entry.get("precioGameStop"))
    if retail is not None:
        references.append(
            PriceReference(
                entity_id=entity_id, source="retail", value=retail, completeness="loose", condition="refurbished",
                observed_at=observed_at, confidence=0.6, notes=str(sources.get("precioGameStop") or ""),
            )
        )

    return references


# Mínimo de publicaciones para que una mediana signifique algo. Con menos, el
# precio de una sola publicación rara arrastra la referencia entera.
PEER_BENCHMARK_MINIMUM = 4


def peer_listing_benchmark(
    prices: list[float], entity_id: str = "", completeness: str = "loose", observed_at: str = ""
) -> PriceReference | None:
    """Mediana de las publicaciones activas que trajo la misma búsqueda.

    Fue durante un tiempo la única referencia disponible para juegos, cuando el
    catálogo sólo tenía precios de consolas. Desde que los juegos tienen
    `priceRange` con fuente, convive con esa referencia y queda por debajo de
    ella en el orden de evidencia.

    Es evidencia real y propia —son precios que alguien está pidiendo hoy— pero
    **pedidos, no vendidos**, así que entra con confianza baja y por debajo de
    PriceCharting y del retail observado en el orden de preferencia. Lo que un
    vendedor pide no es lo que el mercado paga.
    """

    usable = sorted(price for price in prices if isinstance(price, (int, float)) and price > 0)
    if len(usable) < PEER_BENCHMARK_MINIMUM:
        return None
    middle = len(usable) // 2
    median = usable[middle] if len(usable) % 2 else (usable[middle - 1] + usable[middle]) / 2
    return PriceReference(
        entity_id=entity_id,
        source="peer-listings",
        value=round(median, 2),
        completeness=completeness if completeness in {"loose", "cib", "boxed", "sealed"} else "loose",
        observed_at=observed_at,
        confidence=0.5,
        independent=True,
        notes=f"Mediana de {len(usable)} publicaciones activas de esta búsqueda; son precios pedidos, no vendidos.",
    )


def reference_from_price_range(
    price_range: dict[str, Any] | None,
    *,
    entity_id: str = "",
    observed_at: str = "",
) -> PriceReference | None:
    """El precio que el catálogo ya tiene para un juego, como referencia.

    `priceRange.mid` es el precio típico de copia suelta que se cargó con
    fuente y fecha en las notas. Hasta ahora el radar no lo miraba: tenía el
    dato en el repo y seguía juzgando cada juego contra la mediana de su propia
    corrida, que es compararse consigo mismo.

    Entra como `retail` porque eso es literalmente lo que es —precio de lista
    de tiendas retro— y por eso pesa más que la mediana de publicaciones
    activas, que son pedidos sueltos de cualquiera. Pero un precio de tienda
    está por encima de lo que cierra una compra entre particulares, y la nota
    lo dice: quien mire el veredicto tiene que poder calibrarlo.

    Sin fecha no se puede saber si envejeció, así que la confianza baja. Es
    preferible a no tener con qué comparar, y se declara.
    """

    if not isinstance(price_range, dict):
        return None
    mid = to_amount(price_range.get("mid"))
    if mid is None:
        return None
    low = to_amount(price_range.get("low"))
    high = to_amount(price_range.get("high"))
    banda = f" Rango cargado: {low}–{high}." if low is not None and high is not None else ""
    return PriceReference(
        entity_id=entity_id,
        source="retail",
        value=mid,
        completeness="loose",
        observed_at=observed_at,
        confidence=0.6 if observed_at else 0.45,
        independent=True,
        notes=(
            "Precio típico del catálogo para copia suelta."
            + banda
            + " Es precio de lista de tiendas retro: una compra entre particulares suele cerrar por debajo."
            + ("" if observed_at else " Sin fecha de verificación en el catálogo, así que no se puede saber si envejeció.")
        ),
    )


def pick_benchmark(
    references: list[PriceReference], *, completeness: str = "loose", today: date | None = None
) -> PriceReference | None:
    """Elige la referencia comparable: misma completitud, mejor evidencia.

    Una referencia no independiente puede usarse, pero sólo si no hay otra:
    es mejor decidir con evidencia débil declarada que con ninguna.
    """

    wanted = completeness if completeness in {"loose", "cib", "boxed", "sealed"} else "loose"
    comparable = [ref for ref in references if ref.completeness == wanted]
    if not comparable:
        return None
    return sorted(
        comparable,
        key=lambda ref: (
            0 if ref.independent else 1,
            1 if ref.is_stale(today) else 0,
            SOURCE_RANK.get(ref.source, 9),
            -ref.confidence,
        ),
    )[0]


def bundle_adjusted_benchmark(
    benchmark: PriceReference | None,
    *,
    declared_games: int | None,
    game_value: float | None,
    catalog_sample: int = 0,
    max_games: int = 10,
) -> PriceReference | None:
    """La referencia de una consola que viene con juegos: consola más sus juegos.

    Comparar un bundle de SNES con dos controles y cinco juegos contra el
    precio de la consola sola lo marcaba «caro» siempre, que es exactamente al
    revés de lo que el dueño quiere ver: si la consola trae juegos, mejor.

    Sólo se suma lo que el título declara con número, a un valor por juego
    prudente que sale del catálogo, y con un tope: el bundle vale lo que vale
    la consola más un puñado de juegos comunes, no la suma teórica de un lote.
    La mediana de publicaciones activas no se ajusta: esos precios ya incluyen
    los bundles que haya en la corrida, y sumarles juegos los contaría dos veces.
    """

    if benchmark is None or not declared_games or not game_value or game_value <= 0:
        return benchmark
    if benchmark.source == "peer-listings":
        return benchmark
    counted = min(int(declared_games), max_games)
    extra = round(counted * game_value, 2)
    capped = " (tope del ajuste)" if declared_games > max_games else ""
    return replace(
        benchmark,
        value=round(benchmark.value + extra, 2),
        # Un número que suma una estimación pesa menos que el dato solo.
        confidence=round(benchmark.confidence * 0.8, 3),
        notes=(
            f"{benchmark.notes} Ajustada por bundle: suma {counted} juego(s){capped} a "
            f"{game_value:g} {benchmark.currency} cada uno, el cuartil bajo del precio mínimo de "
            f"{catalog_sample} juegos del catálogo de esta consola. Qué juegos trae no se sabe."
        ).strip(),
    )


def lot_ceiling_benchmark(
    *,
    declared_pieces: int | None,
    per_piece_ceiling: float | None,
    entity_id: str = "",
    currency: str = "USD",
    filler: bool = False,
) -> PriceReference | None:
    """La vara de un lote con cantidad declarada: tu techo por pieza por las piezas.

    La mediana de los lotes de la misma corrida mezcla lotes de cuatro y de
    veintidós juegos: un lote de 22 juegos de PS2 a 2,27 dólares cada uno salía
    «caro» porque su total superaba al de lotes chicos. Lo que hace barato a un
    lote es lo que cuesta cada pieza, y el umbral de «barato» lo eligió el dueño.

    No es un precio de mercado y la nota lo dice: es el criterio propio puesto
    en el lugar donde el radar compara.
    """

    if not declared_pieces or not per_piece_ceiling or per_piece_ceiling <= 0:
        return None
    per_piece = per_piece_ceiling * (LOT_FILLER_FACTOR if filler else 1.0)
    return PriceReference(
        entity_id=entity_id,
        source="lot-ceiling",
        value=round(declared_pieces * per_piece, 2),
        currency=currency,
        completeness="loose",
        confidence=0.5,
        independent=True,
        notes=(
            f"Tu techo de {per_piece_ceiling:g} {currency} por pieza por las {declared_pieces} piezas que "
            "declara el título. No es un precio de mercado: es el umbral que elegiste para «barato»."
            + (
                f" Trae relleno, así que cada pieza se mide contra {per_piece:g} {currency}."
                if filler
                else ""
            )
        ),
    )


def lot_peer_benchmark(
    per_piece_prices: list[float], *, declared_pieces: int | None, entity_id: str = "", observed_at: str = ""
) -> PriceReference | None:
    """Sin techo por pieza, la vara de un lote es la mediana por pieza de sus pares.

    Nunca la mediana de los totales: un lote de cuatro y uno de veintidós no se
    comparan por lo que cuestan enteros. Sólo entran lotes con cantidad
    confirmada, y la referencia escala a las piezas que declara este.
    """

    reference = peer_listing_benchmark(per_piece_prices, entity_id=entity_id, observed_at=observed_at)
    if reference is None or not declared_pieces:
        return None
    return replace(
        reference,
        value=round(reference.value * declared_pieces, 2),
        notes=(
            f"Mediana por pieza ({reference.value:g}) de {len(per_piece_prices)} lotes con cantidad declarada "
            f"de esta búsqueda, por las {declared_pieces} piezas de este. Son precios pedidos, no vendidos."
        ),
    )


def completion_cost(assessment: dict[str, Any] | None) -> dict[str, Any] | None:
    """Lo que habría que comprar aparte para dejar lista una consola incompleta."""
    kit = (assessment or {}).get("kit") or {}
    missing = [piece for piece in kit.get("missing") or [] if piece in COMPLETION_COSTS_USD]
    if not missing:
        return None
    detail = {piece: COMPLETION_COSTS_USD[piece] for piece in missing}
    return {
        "pieces": missing,
        "detail": detail,
        "total": round(sum(detail.values()), 2),
        "estimated": True,
    }


def decision_band(ratio: float) -> tuple[str, str]:
    for key, ceiling, label in DECISION_BANDS:
        if ratio <= ceiling:
            return key, label
    return EXPENSIVE_BAND


@dataclass(slots=True)
class ScoreCard:
    """Score explicable: cada punto tiene un motivo."""

    score: int = 0
    band: str = ""
    band_label: str = ""
    ratio: float | None = None
    benchmark: dict[str, Any] | None = None
    cost: dict[str, Any] = field(default_factory=dict)
    contributions: list[dict[str, Any]] = field(default_factory=list)
    penalties: list[dict[str, Any]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    target: dict[str, Any] | None = None
    sold_comparables: dict[str, Any] | None = None
    # La banda que daría el precio solo, antes de las compuertas.
    price_band: str = ""
    # Qué le falta para poder llamarse «Ganga real», con clave estable para que
    # la lectura sepa cuál se resuelve al verificar la publicación.
    bargain_blockers: list[dict[str, str]] = field(default_factory=list)
    # Por qué el veredicto es el que es, en frases que la card muestra tal cual.
    verdict_notes: list[str] = field(default_factory=list)
    # `primary` o `unconfirmed-quantity`: un lote que no confirma cuántos
    # juegos trae no se mezcla con los que cumplen.
    section: str = "primary"
    completion: dict[str, Any] | None = None
    assessment: dict[str, Any] = field(default_factory=dict)
    # La banda después de las compuertas de ganga y antes de mandar a
    # revisión: la que corresponde si el dueño despeja lo que había que revisar.
    gated_band: str = ""
    # Por qué fue a revisión, con claves estables. Sólo `cib-unverified` se
    # puede despejar desde la app (comprobando caja y manual).
    review_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "bandLabel": self.band_label,
            "priceBand": self.price_band,
            "ratio": round(self.ratio, 3) if self.ratio is not None else None,
            "benchmark": self.benchmark,
            "cost": dict(self.cost),
            "contributions": list(self.contributions),
            "penalties": list(self.penalties),
            "caveats": list(self.caveats),
            "target": dict(self.target) if self.target else None,
            "soldComparables": dict(self.sold_comparables) if self.sold_comparables else None,
            "bargainBlockers": [dict(item) for item in self.bargain_blockers],
            "verdictNotes": list(self.verdict_notes),
            "section": self.section,
            "completion": dict(self.completion) if self.completion else None,
            "assessment": dict(self.assessment),
            "objective": dict((self.assessment or {}).get("objective") or {}) or None,
            "gatedBand": self.gated_band,
            "reviewKeys": list(self.review_keys),
        }


def sold_comparables_link(query: str) -> dict[str, Any] | None:
    """Link al filtro de ventas cerradas del propio eBay, para esa consulta.

    El radar no tiene benchmark externo: PriceCharting quedó descartado y las
    ventas cerradas viven en Marketplace Insights, de acceso restringido. Lo que
    sí se puede hacer sin pagar ni scrapear nada es **mandar al dueño a mirarlas
    él**: es un link a la interfaz de eBay, no una integración.

    Es la diferencia entre «el radar no tiene con qué comparar» y «el radar te
    deja el benchmark a un click».

    Requiere sesión iniciada en eBay: deslogueado, ese filtro redirige al login.
    Por eso se declara como lo que es y no como un dato que la card ya tiene.
    """

    cleaned = " ".join(str(query or "").split()).strip()
    if not cleaned:
        return None
    parameters = urllib.parse.urlencode(
        {"_nkw": cleaned, "_sacat": "0", "LH_Sold": "1", "LH_Complete": "1"}
    )
    return {
        "url": f"https://www.ebay.com/sch/i.html?{parameters}",
        "query": cleaned,
        "label": "Ver ventas cerradas en eBay",
        "note": "Se abre en eBay y pide tu sesión iniciada. Es lo que se pagó, no lo que se pide.",
    }


def evaluate_target(item_price: float | None, target: float | None) -> dict[str, Any] | None:
    """Compara el precio del artículo contra el precio al que comprarías ya.

    Se mide contra el precio publicado y no contra el costo puesto acá: es el
    número sobre el que el owner decide, y el único que no depende de una
    estimación nuestra. El courier se sigue mostrando en la card como contexto,
    pero no decide a quién interrumpir — apoyar el aviso en un peso estimado
    sería apoyarlo en la parte más floja del cálculo.

    Un lote también tiene precio, así que a diferencia del costo puesto acá acá
    siempre hay veredicto.
    """

    if target is None or target <= 0 or item_price is None:
        return None
    return {
        "value": target,
        "meets": item_price <= target,
        "itemPrice": item_price,
        "gap": round(item_price - target, 2),
    }


# --------------------------------------------------------------------------- #
# Courier: de la dirección de Estados Unidos hasta Uruguay                      #
# --------------------------------------------------------------------------- #
#
# Tarifa vigente del courier del usuario, en USD por kilo. Se fracciona cada
# 100 g con un mínimo de 100 g, e incluye manejo, consolidación y almacenaje.
#
# La categoría "media" es la tarifa reducida de libros, discos y películas.
# **No se aplica sola a los videojuegos:** que un juego venga en disco no
# significa que el courier lo clasifique ahí, y asumirlo barato para después
# pagar el doble sería exactamente la sorpresa que la tarifa promete evitar.
# Por eso el default es la tarifa general y la reducida es una elección
# explícita de quien conoce cómo le clasifican los paquetes.

COURIER_FRACTION_KG = 0.1
COURIER_MINIMUM_KG = 0.1

COURIER_RATES: dict[str, dict[str, float]] = {
    "usa": {"general": 17.50, "media": 10.50},
    "europa": {"general": 21.50, "media": 14.50},
}

# Impuesto de importación en destino (Uruguay). Da cero, pero no porque no
# exista: da cero mientras la mercadería de cada reenvío quede bajo la
# franquicia. Eso no es una propiedad de la publicación sino del paquete en el
# que viaje, y el paquete se arma después de comprar — por eso el impuesto no
# se estima por publicación, y la franquicia se sigue aparte (ver
# `compute_radar_shipment` en app.py). Se deja explícito en el desglose en vez
# de omitirlo, para que no vuelva a leerse como un dato faltante.
IMPORT_TAX_USD = 0.0

# Pesos estimados en kg, con embalaje. Son estimaciones declaradas, no datos de
# la publicación: eBay no informa peso de forma confiable. Se eligen del lado
# alto, porque un costo subestimado es peor que uno prudente.
ESTIMATED_WEIGHTS_KG = {
    ("game", "loose"): 0.15,
    ("game", "boxed"): 0.25,
    ("game", "cib"): 0.30,
    ("game", "sealed"): 0.30,
    ("console", "loose"): 3.0,
    ("console", "boxed"): 4.0,
    ("console", "cib"): 4.5,
    ("console", "sealed"): 4.5,
    ("accessory", "loose"): 0.4,
    ("accessory", "boxed"): 0.6,
    ("accessory", "cib"): 0.6,
    ("accessory", "sealed"): 0.6,
}


def billable_weight_kg(weight_kg: float) -> float:
    """El courier cobra por fracciones de 100 g, con 100 g de mínimo."""
    if weight_kg <= COURIER_MINIMUM_KG:
        return COURIER_MINIMUM_KG
    fractions = -(-round(weight_kg, 4) // COURIER_FRACTION_KG)  # techo
    return round(fractions * COURIER_FRACTION_KG, 2)


def estimate_weight_kg(entity_type: str, completeness: str = "loose") -> float | None:
    """Peso estimado por tipo de pieza. `None` cuando no hay con qué estimar.

    Un lote no se estima: su peso depende de cuántas piezas trae, y adivinarlo
    sería inventar el costo más caro del cálculo.
    """

    kind = entity_type if entity_type in {"game", "console", "accessory"} else ""
    if not kind:
        return None
    shape = completeness if completeness in {"loose", "boxed", "cib", "sealed"} else "loose"
    return ESTIMATED_WEIGHTS_KG.get((kind, shape))


def courier_cost(weight_kg: float | None, origin: str = "usa", category: str = "general") -> float | None:
    rates = COURIER_RATES.get(str(origin or "").lower())
    if rates is None or weight_kg is None:
        return None
    rate = rates.get(str(category or "general").lower())
    if rate is None:
        return None
    return round(billable_weight_kg(weight_kg) * rate, 2)


def cost_breakdown(
    price_amount: float | None,
    shipping_amount: float | None,
    currency: str = "USD",
    *,
    entity_type: str = "",
    completeness: str = "loose",
    courier_origin: str = "usa",
    courier_category: str = "general",
    weight_kg: float | None = None,
    include_import: bool = True,
) -> dict[str, Any]:
    """Artículo, envío interno y courier, cada uno declarando si es exacto.

    El subtotal en Estados Unidos es lo que se paga allá; el costo importado le
    suma el courier hasta Uruguay y **siempre** viaja marcado como estimado,
    porque su peso lo es.
    """

    known_shipping = shipping_amount is not None
    subtotal = None
    if price_amount is not None:
        subtotal = round(price_amount + (shipping_amount or 0), 2)

    # Un peso declarado para esta consola en particular gana sobre la tabla
    # genérica: una PSP pesa 0,19 kg y una PS3 Fat 5 — un solo número para
    # "consola" se equivoca en las dos direcciones.
    if not include_import:
        weight = None
    elif weight_kg is not None:
        weight = weight_kg
    else:
        weight = estimate_weight_kg(entity_type, completeness)
    courier = courier_cost(weight, courier_origin, courier_category) if weight is not None else None
    imported = (
        round(subtotal + courier + IMPORT_TAX_USD, 2) if (subtotal is not None and courier is not None) else None
    )

    return {
        "currency": currency or "USD",
        "item": price_amount,
        "shipping": shipping_amount,
        "shippingKnown": known_shipping,
        "subtotalUsa": subtotal,
        "estimatedWeightKg": weight,
        "billableWeightKg": billable_weight_kg(weight) if weight is not None else None,
        "courier": courier,
        "courierOrigin": courier_origin if courier is not None else "",
        "courierCategory": courier_category if courier is not None else "",
        # Siempre cero, y siempre presente: ver IMPORT_TAX_USD.
        "importTax": IMPORT_TAX_USD,
        "importedTotal": imported,
        "importedEstimated": imported is not None,
        "exact": bool(price_amount is not None and known_shipping),
    }


@dataclass
class LotPiece:
    """Una pieza declarada dentro de un lote, tal como la describe el usuario.

    El radar no puede leer fotos ni parsear "PS2 + 8 juegos + 2 controles" de
    un título de eBay con confianza — eso es reconocimiento asistido (PRD P2,
    todavía no construido). Esta pieza es información que alguien puso a
    mano: nunca se infiere de texto libre.
    """

    name: str
    comparable_value: float | None = None  # valor de mercado de esta pieza sola, en condición comparable
    condition_factor: float = 1.0  # 0..1: castigo por el estado real de ESTA pieza dentro del lote
    wanted: bool = True  # ¿el usuario la quiere para su colección?
    already_owned: bool = False  # ¿ya la tiene?
    is_duplicate: bool = False  # ¿ya apareció otra igual en el mismo lote?

    @property
    def useful(self) -> bool:
        """PRD §10.5: piezas deseadas y no poseídas. Un duplicado no es útil acá

        — puede tener valor de reventa, pero eso es un análisis aparte
        (PRD P2, "reventa opcional y separado") que este cálculo no mezcla.
        """
        return self.wanted and not self.already_owned and not self.is_duplicate


def lot_valuation(pieces: list[LotPiece], total_cost: float | None) -> dict[str, Any]:
    """PRD §10.5, la fórmula tal cual está escrita, sin adornos:

        valor conservador = suma de valores comparables × factor de condición
        valor útil personal = valor conservador de piezas deseadas/no poseídas
        costo por pieza útil = costo total / cantidad de piezas útiles
        descuento del lote = 1 - costo total / valor conservador

    Un lote no es bueno porque su suma teórica sea alta — por eso cada número
    sale con su propia advertencia en vez de fundirse en un solo score.
    """

    priced = [p for p in pieces if p.comparable_value is not None]
    useful = [p for p in pieces if p.useful]
    useful_priced = [p for p in useful if p.comparable_value is not None]

    conservative_value = (
        round(sum(p.comparable_value * p.condition_factor for p in priced), 2) if priced else None
    )
    useful_value = (
        round(sum(p.comparable_value * p.condition_factor for p in useful_priced), 2) if useful_priced else None
    )
    cost_per_useful_piece = (
        round(total_cost / len(useful), 2) if (total_cost is not None and useful) else None
    )
    discount = (
        round(1 - total_cost / conservative_value, 4)
        if (total_cost is not None and conservative_value)
        else None
    )

    duplicate_count = sum(1 for p in pieces if p.is_duplicate)
    unpriced_count = len(pieces) - len(priced)

    caveats: list[str] = []
    if not pieces:
        caveats.append("Sin piezas declaradas: no hay nada que valuar todavía.")
    if duplicate_count:
        caveats.append(
            f"{duplicate_count} pieza(s) duplicada(s): no suman valor útil "
            "para tu colección, aunque puedan revenderse — eso es un análisis aparte."
        )
    if unpriced_count:
        caveats.append(
            f"{unpriced_count} pieza(s) sin referencia de precio propia: el "
            "valor conservador las excluye, no asume que valen cero."
        )
    if pieces and not useful:
        caveats.append("Ninguna pieza del lote es útil para vos: ya las tenés todas o no las querés.")

    return {
        "totalCost": total_cost,
        "pieceCount": len(pieces),
        "usefulPieceCount": len(useful),
        "duplicateCount": duplicate_count,
        "unpricedCount": unpriced_count,
        "conservativeValue": conservative_value,
        "usefulValue": useful_value,
        "costPerUsefulPiece": cost_per_useful_piece,
        "discount": discount,
        "caveats": caveats,
    }


def score_listing(
    *,
    price_amount: float | None,
    shipping_amount: float | None,
    currency: str = "USD",
    benchmark: PriceReference | None,
    match_confidence: float = 0.6,
    match_reasons: list[str] | None = None,
    match_unverified: list[str] | None = None,
    completeness: str = "loose",
    seller_known: bool = False,
    entity_type: str = "",
    courier_origin: str = "usa",
    courier_category: str = "general",
    weight_kg: float | None = None,
    target_item_price: float | None = None,
    today: date | None = None,
    assessment: dict[str, Any] | None = None,
    availability_verified: bool = False,
) -> ScoreCard:
    """Puntúa una publicación que ya pasó los filtros obligatorios.

    `assessment` es lo que el matcher leyó del título con estructura (qué le
    falta a una consola, si un cartucho declara ser original, cuántos juegos
    trae un lote). Sin ella el score es el de siempre; con ella el precio se
    compara contra lo que costaría dejar la pieza lista, y la banda pasa por
    las compuertas de `apply_verdict_gates`.
    """

    card = ScoreCard()
    assessment = dict(assessment or {})
    card.assessment = assessment
    card.completion = completion_cost(assessment)
    extra = card.completion["total"] if card.completion else 0.0
    card.cost = cost_breakdown(
        price_amount, shipping_amount, currency,
        entity_type=entity_type, completeness=completeness,
        courier_origin=courier_origin, courier_category=courier_category,
        weight_kg=weight_kg,
    )
    # El objetivo no puntúa ni bloquea: es tu umbral de "esto lo compro ya", y
    # sirve para decidir a quién interrumpir.
    card.target = evaluate_target(price_amount, target_item_price)
    reasons = match_reasons or []
    unverified = match_unverified or []

    def add(dimension: str, points: float, detail: str) -> None:
        card.contributions.append(
            {"dimension": dimension, "points": round(points), "max": WEIGHTS[dimension], "detail": detail}
        )

    def penalize(points: float, detail: str) -> None:
        card.penalties.append({"points": -round(points), "detail": detail})

    # --- Impacto sobre la colección: qué tan seguro es que esto es lo buscado ---
    add("collection", WEIGHTS["collection"] * max(0.0, min(match_confidence, 1.0)),
        f"Coincide con la búsqueda al {round(match_confidence * 100)}%")

    # --- Precio frente al benchmark comparable -------------------------------
    comparison_base = card.cost["subtotalUsa"] if card.cost["shippingKnown"] else price_amount
    # Una consola a la que le falta algo se compara contra una lista para usar
    # sumando lo que costaría completarla: la consola sola y el bundle dejan de
    # medirse con la misma vara.
    if comparison_base is not None and extra:
        comparison_base = round(comparison_base + extra, 2)
    if benchmark is not None and comparison_base is not None and benchmark.value > 0:
        card.ratio = comparison_base / benchmark.value
        card.band, card.band_label = decision_band(card.ratio)
        card.benchmark = benchmark.to_dict(today)
        # 25 puntos en una ganga, 0 al llegar a 130% del benchmark.
        price_points = WEIGHTS["price"] * max(0.0, min(1.0, (1.30 - card.ratio) / 0.55))
        add("price", price_points,
            f"{round(card.ratio * 100)}% de {benchmark.value:g} {benchmark.currency} "
            f"({SOURCE_LABELS.get(benchmark.source, benchmark.source)}, {benchmark.completeness.upper()})"
            + (f", sumando {extra:g} para completarla" if extra else ""))
        if not benchmark.independent:
            penalize(6, "El benchmark no es evidencia independiente: copia otra fuente")
            card.caveats.append("La referencia de precio repite otra fuente; no la trates como corroboración.")
        if benchmark.source == "peer-listings":
            card.caveats.append(benchmark.notes)
        if benchmark.is_stale(today):
            age = benchmark.age_days(today)
            penalize(5, f"El benchmark tiene {age} días")
            card.caveats.append("Referencia de precio desactualizada.")
    else:
        card.caveats.append("Sin benchmark comparable: el precio no se puede juzgar todavía.")
        penalize(8, "No hay una referencia comparable para esta variante")

    # --- Condición y evidencia funcional -------------------------------------
    tested = any("probada" in reason.lower() for reason in reasons)
    risky = any("untested" in reason.lower() or "sin probar" in reason.lower() for reason in reasons)
    if tested:
        add("condition", WEIGHTS["condition"], "Declara estar probada")
    elif risky:
        add("condition", WEIGHTS["condition"] * 0.15, "Se declara sin probar")
        penalize(10, "Sin probar: exige descuento extraordinario")
    else:
        add("condition", WEIGHTS["condition"] * 0.4, "Condición sin declarar")

    # --- Completitud ----------------------------------------------------------
    kit = assessment.get("kit") or {}
    if kit:
        share, label = KIT_COMPLETENESS.get(str(kit.get("status") or ""), (0.4, "completitud sin declarar"))
        add("completeness", WEIGHTS["completeness"] * share, label[:1].upper() + label[1:])
    else:
        declared = any("completitud" in reason.lower() for reason in reasons)
        add("completeness", WEIGHTS["completeness"] * (1.0 if declared else 0.45),
            f"Completitud {'declarada' if declared else 'sin declarar'} ({completeness.upper()})")

    # --- Vendedor y devolución ------------------------------------------------
    add("seller", WEIGHTS["seller"] * (0.7 if seller_known else 0.3),
        "Reputación del vendedor conocida" if seller_known else "Vendedor sin reputación conocida")

    # --- Logística ------------------------------------------------------------
    if card.cost["shippingKnown"]:
        shipping = shipping_amount or 0.0
        if price_amount and shipping > price_amount * 0.4:
            add("logistics", WEIGHTS["logistics"] * 0.2, "Envío desproporcionado frente al artículo")
            penalize(6, f"El envío es el {round(shipping / price_amount * 100)}% del precio")
        else:
            add("logistics", WEIGHTS["logistics"], "Envío confirmado")
    else:
        add("logistics", WEIGHTS["logistics"] * 0.3, "Envío sin confirmar")

    if card.cost["importedTotal"] is not None:
        card.caveats.append(
            f"Costo importado estimado: {card.cost['importedTotal']:g} {card.cost['currency']} "
            f"({card.cost['billableWeightKg']:g} kg facturables de courier). El peso es una estimación."
        )
    elif entity_type:
        card.caveats.append("Sin peso estimable para esta pieza: el costo importado queda sin calcular.")

    for item in unverified:
        penalize(3, item)

    if not card.band:
        card.band, card.band_label = NO_REFERENCE_BAND
    apply_verdict_gates(
        card,
        match_confidence=match_confidence,
        untested=risky,
        availability_verified=availability_verified,
        penalize=penalize,
    )

    raw = sum(item["points"] for item in card.contributions) + sum(item["points"] for item in card.penalties)
    card.score = max(0, min(100, round(raw)))
    return card


# Cuánto del peso de completitud se lleva una consola según lo que trae.
KIT_COMPLETENESS = {
    "cib": (1.0, "completa en caja"),
    "ready": (1.0, "lista para usar"),
    "undeclared": (0.4, "no dice todo lo que trae"),
    "missing": (0.3, "le faltan piezas"),
    "console-only": (0.2, "consola sola"),
    "damaged": (0.1, "daño declarado"),
}


def _join(items: list[str]) -> str:
    items = [item for item in items if item]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " y " + items[-1]


def apply_verdict_gates(
    card: ScoreCard,
    *,
    match_confidence: float,
    untested: bool = False,
    availability_verified: bool = False,
    penalize: Any = None,
) -> None:
    """Decide cuánto puede afirmar la banda, además de lo que dice el precio.

    El 43% de lo que encontraba el radar salía «ganga», y una palabra que
    aparece en casi la mitad de los resultados deja de decir algo. «Ganga real»
    exige ahora alta confianza en todo lo que el precio no mide: que sea lo
    buscado, que sea original, que esté completo, cuántas piezas trae, cuánto
    cuesta el envío, que la referencia no sea sólo lo que piden otros y que la
    publicación siga comprable. Lo que falta se dice —«por precio sería ganga;
    falta…»— y la banda baja a «buena compra», que sigue siendo una buena
    noticia.

    Tres cosas ni siquiera dejan juzgar por precio, y van a «requiere
    revisión»: un cartucho retro sin originalidad declarada cuando la búsqueda
    la pide, un daño declarado y una consola que no es la original. Un lote que
    no confirma cuántos juegos trae no tiene veredicto: va aparte.
    """

    penalize = penalize or (lambda points, detail: card.penalties.append({"points": -round(points), "detail": detail}))
    assessment = card.assessment or {}
    card.price_band = card.band
    blockers: list[dict[str, str]] = []

    def block(key: str, label: str) -> None:
        if all(item["key"] != key for item in blockers):
            blockers.append({"key": key, "label": label})

    # Lo que manda a «Requiere revisión», y lo que la card dice de eso.
    needs_review = False
    review: list[str] = []

    # --- Identidad -----------------------------------------------------------
    if match_confidence < BARGAIN_MIN_CONFIDENCE or assessment.get("itemConfident") is False:
        block("identity", "la identidad de la pieza no está clara")

    # --- Originalidad --------------------------------------------------------
    authenticity = assessment.get("authenticity") or {}
    status = str(authenticity.get("status") or "")
    original_required = str(assessment.get("originalParts") or "any") != "any"
    if status == "suspect":
        block("authenticity", "originalidad sin confirmar")
        penalize(25, f"Marcas típicas de copia («{authenticity.get('signal')}»)")
        needs_review = True
        card.review_keys.append("authenticity-suspect")
        review.append(
            f"Tiene marcas típicas de copia («{authenticity.get('signal')}»): tratala como sospechosa hasta ver etiqueta y placa."
        )
    elif status == "unknown":
        block("authenticity", "originalidad sin confirmar")
        # Un cartucho suelto se juzga como pieza: sin originalidad declarada, el
        # precio no alcanza para decir nada. Un lote se decide por lo que cuesta
        # cada pieza —y casi ningún lote declara la originalidad de cada
        # cartucho—, así que conserva su banda, pero tampoco puede ser ganga.
        if original_required and assessment.get("itemKind") != "lot" and not assessment.get("lot"):
            # La línea «Originalidad sin declarar: mirá etiqueta…» ya la pone
            # el matcher; acá sólo se decide la banda.
            penalize(4, "Cartucho sin originalidad declarada")
            needs_review = True
            card.review_keys.append("authenticity-unknown")
    elif status == "copy":
        # Sólo llega acá si la búsqueda acepta copias.
        block("authenticity", "se declara copia")
        penalize(25, "Se declara copia")
        needs_review = True
        card.review_keys.append("copy")
        review.append(f"Se declara copia («{authenticity.get('signal')}»).")

    origin = assessment.get("origin") or {}
    if origin.get("status"):
        block("origin", "no es la consola original")
        penalize(20, "No es la consola original")
        needs_review = True
        card.review_keys.append("origin")
        review.append(f"No es la consola original («{origin.get('signal')}»).")

    # --- Consola: qué trae y en qué estado ------------------------------------
    kit = assessment.get("kit") or {}
    kit_status = str(kit.get("status") or "")
    if kit:
        if kit_status not in {"cib", "ready"}:
            block("completeness", "no está confirmada lista para usar")
        if card.completion:
            missing = _join([COMPLETION_LABELS.get(piece, piece) for piece in card.completion["pieces"]])
            penalize(6 if kit_status == "console-only" else 4, f"Le falta {missing}")
            card.verdict_notes.append(
                f"Le falta {missing}: se suman ~{card.completion['total']:g} USD estimados para dejarla lista "
                "y se compara contra una lista para usar."
            )
        if kit_status == "damaged":
            issues = _join(list(kit.get("issues") or []))
            block("condition", "daño declarado")
            penalize(10, f"Daño declarado: {issues}")
            needs_review = True
            card.review_keys.append("damage")
            review.append(f"Daño declarado: {issues}.")
        if kit.get("reader") in {"powers-only", "partial", "unconfirmed"}:
            block("reader", f"falta confirmar que lea {kit.get('readerLabel') or 'discos'}")
    if untested:
        block("condition", "sin probar")

    # --- CIB: lo que declara el vendedor no es lo que se comprobó ---------------
    # Un CIB vale por la caja y el manual. El radar no mira fotos: hasta que el
    # dueño los comprueba, un CIB declarado se mira antes de pagar como CIB y
    # nunca es «Ganga real». Una copia con caja y sin manual no es CIB ni loose:
    # no se compara con ninguno.
    objective = assessment.get("objective") or {}
    if objective.get("status") == "declared":
        what = "caja y contenido" if objective.get("id") == "console-cib" else "caja y manual"
        block("contents", f"{what} sin comprobar")
        needs_review = True
        card.review_keys.append("cib-unverified")
        review.append(str(objective.get("note") or f"CIB declarado por el vendedor: confirmá {what} en las fotos."))
    elif objective.get("status") == "incomplete":
        card.section = "incomplete"
        block("contents", "no está completo")
        card.verdict_notes.append(
            str(objective.get("note") or "No es CIB ni loose")
            + " Va aparte de los CIB y no se compara con ninguno de los dos."
        )
    if objective.get("reproPackaging") and objective.get("id", "").endswith("loose"):
        card.verdict_notes.append("Caja repro: no cuenta como CIB, se compara como loose.")

    # --- Lotes ---------------------------------------------------------------
    lot = assessment.get("lot") or {}
    if lot:
        if lot.get("quantity") != "confirmed":
            card.section = "unconfirmed-quantity"
            block("lot-quantity", "cantidad sin confirmar")
            penalize(15, "Cantidad sin confirmar")
            card.verdict_notes.append(
                (lot.get("note") or "No declara cuántos juegos trae")
                + ": sin precio por pieza no hay veredicto, así que va aparte de los lotes que cumplen."
            )
        filler = [str(item) for item in lot.get("filler") or []]
        if filler:
            block("lot-filler", "trae relleno")
            penalize(8, "Relleno en el lote")
            card.verdict_notes.append(
                f"Trae relleno ({_join([f'«{item}»' for item in filler[:3]])}): cada pieza se mide contra "
                f"{round(LOT_FILLER_FACTOR * 100)}% de tu techo."
            )

    # --- Evidencia de precio y de compra --------------------------------------
    benchmark = card.benchmark or {}
    if benchmark:
        if benchmark.get("source") == "peer-listings":
            block("benchmark", "la referencia es sólo lo que piden otras publicaciones")
        elif benchmark.get("independent") is False or benchmark.get("stale"):
            block("benchmark", "la referencia de precio es débil")
    if not card.cost.get("shippingKnown"):
        block("shipping", "envío sin confirmar")
    if not availability_verified:
        block("availability", "disponibilidad sin verificar")

    card.bargain_blockers = blockers

    # --- Banda final -----------------------------------------------------------
    # Primero las compuertas de ganga; esa es la banda que se recupera si el
    # dueño despeja lo que mandó a revisión. Después, la revisión.
    if card.section in {"unconfirmed-quantity", "incomplete"}:
        card.band, card.band_label = NO_REFERENCE_BAND
    elif card.band == "ganga" and blockers:
        card.band, card.band_label = "buena", BAND_NAMES["buena"]
        if not needs_review:
            card.verdict_notes.append(
                f"Por precio sería ganga ({round((card.ratio or 0) * 100)}% de la referencia). "
                f"Para «Ganga real» falta: {_join([item['label'] for item in blockers])}."
            )
    card.gated_band = card.band
    # Un CIB declarado va a revisión aunque no haya referencia CIB: lo que hay
    # que mirar es la caja, no el precio.
    reviewable = POSITIVE_BANDS | ({"sin-referencia"} if "cib-unverified" in card.review_keys else set())
    if needs_review and card.section == "primary" and card.band in reviewable:
        if card.ratio is not None:
            card.verdict_notes.append(
                f"Por precio sería {BAND_NAMES.get(card.price_band, card.price_band)} "
                f"({round(card.ratio * 100)}% de la referencia), pero hay que revisarla antes."
            )
        card.band, card.band_label = REVIEW_BAND
    card.verdict_notes[:0] = review


BAND_NAMES = {key: label for key, _, label in DECISION_BANDS} | {EXPENSIVE_BAND[0]: EXPENSIVE_BAND[1]}


def effective_band(
    band: str,
    valuation: dict[str, Any] | None,
    availability_confirmed: bool,
    contents_confirmed: bool = False,
) -> str:
    """La banda que corresponde mostrar hoy, sabiendo qué comprobó el dueño.

    Al puntuar, ninguna publicación automática tiene la disponibilidad
    confirmada (la fuente no lo informa) ni un CIB comprobado (el radar no mira
    fotos). Si el dueño comprobó caja y manual y eso era lo único que la mandaba
    a revisión, vuelve a la banda que le daba el precio. Si además confirmó Buy
    It Now hace poco y no falta nada más, vuelve a ser ganga. Cualquier otra
    cosa pendiente la deja donde está.
    """

    valuation = valuation or {}
    keys = {str(item.get("key")) for item in valuation.get("bargainBlockers") or []}
    if contents_confirmed:
        keys.discard("contents")
        review_keys = set(valuation.get("reviewKeys") or [])
        if band == "revisar" and review_keys and review_keys <= {"cib-unverified"}:
            band = str(valuation.get("gatedBand") or band)
    if band != "buena" or not availability_confirmed or valuation.get("priceBand") != "ganga":
        return band
    return "ganga" if keys <= {"availability"} else band


__all__ = [
    "BARGAIN_MIN_CONFIDENCE",
    "COMPLETION_COSTS_USD",
    "LOT_FILLER_FACTOR",
    "REVIEW_BAND",
    "apply_verdict_gates",
    "completion_cost",
    "effective_band",
    "lot_peer_benchmark",
    "COURIER_RATES",
    "IMPORT_TAX_USD",
    "DECISION_BANDS",
    "billable_weight_kg",
    "courier_cost",
    "estimate_weight_kg",
    "PriceReference",
    "ScoreCard",
    "cost_breakdown",
    "peer_listing_benchmark",
    "reference_from_price_range",
    "bundle_adjusted_benchmark",
    "lot_ceiling_benchmark",
    "decision_band",
    "pick_benchmark",
    "references_from_console_entry",
    "score_listing",
    "LotPiece",
    "lot_valuation",
]
