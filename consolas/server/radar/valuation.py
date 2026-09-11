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

from dataclasses import dataclass, field
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

# Orden de preferencia de evidencia (PRD §10.2). Menor es mejor.
SOURCE_RANK = {"ebay-sold": 0, "pricecharting": 1, "retail": 2, "manual": 3, "editorial": 4}

SOURCE_LABELS = {
    "ebay-sold": "ventas cerradas en eBay",
    "pricecharting": "PriceCharting",
    "retail": "retail observado",
    "manual": "referencia manual",
    "editorial": "rango editorial",
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "bandLabel": self.band_label,
            "ratio": round(self.ratio, 3) if self.ratio is not None else None,
            "benchmark": self.benchmark,
            "cost": dict(self.cost),
            "contributions": list(self.contributions),
            "penalties": list(self.penalties),
            "caveats": list(self.caveats),
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

    weight = estimate_weight_kg(entity_type, completeness) if include_import else None
    courier = courier_cost(weight, courier_origin, courier_category) if weight is not None else None
    imported = round(subtotal + courier, 2) if (subtotal is not None and courier is not None) else None

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
        "importedTotal": imported,
        "importedEstimated": imported is not None,
        "exact": bool(price_amount is not None and known_shipping),
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
    today: date | None = None,
) -> ScoreCard:
    """Puntúa una publicación que ya pasó los filtros obligatorios."""

    card = ScoreCard()
    card.cost = cost_breakdown(
        price_amount, shipping_amount, currency,
        entity_type=entity_type, completeness=completeness,
        courier_origin=courier_origin, courier_category=courier_category,
    )
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
    if benchmark is not None and comparison_base is not None and benchmark.value > 0:
        card.ratio = comparison_base / benchmark.value
        card.band, card.band_label = decision_band(card.ratio)
        card.benchmark = benchmark.to_dict(today)
        # 25 puntos en una ganga, 0 al llegar a 130% del benchmark.
        price_points = WEIGHTS["price"] * max(0.0, min(1.0, (1.30 - card.ratio) / 0.55))
        add("price", price_points,
            f"{round(card.ratio * 100)}% de {benchmark.value:g} {benchmark.currency} "
            f"({SOURCE_LABELS.get(benchmark.source, benchmark.source)}, {benchmark.completeness.upper()})")
        if not benchmark.independent:
            penalize(6, "El benchmark no es evidencia independiente: copia otra fuente")
            card.caveats.append("La referencia de precio repite otra fuente; no la trates como corroboración.")
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
    declared = any("completitud" in reason.lower() for reason in reasons)
    add("completeness", WEIGHTS["completeness"] * (1.0 if declared else 0.45),
        f"Completitud {'declarada' if declared else 'sin declarar'} ({completeness.upper()})")

    # --- Vendedor y devolución ------------------------------------------------
    add("seller", WEIGHTS["seller"] * (0.7 if seller_known else 0.3),
        "Vendedor identificado" if seller_known else "Vendedor sin reputación conocida")

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

    raw = sum(item["points"] for item in card.contributions) + sum(item["points"] for item in card.penalties)
    card.score = max(0, min(100, round(raw)))
    if not card.band:
        card.band, card.band_label = "sin-referencia", "Sin referencia"
    return card


__all__ = [
    "COURIER_RATES",
    "DECISION_BANDS",
    "billable_weight_kg",
    "courier_cost",
    "estimate_weight_kg",
    "PriceReference",
    "ScoreCard",
    "cost_breakdown",
    "decision_band",
    "pick_benchmark",
    "references_from_console_entry",
    "score_listing",
]
