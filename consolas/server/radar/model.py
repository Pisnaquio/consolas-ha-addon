"""Modelo canónico compartido por todo adapter de marketplace.

Una publicación de eBay, de ShopGoodwill o de un retailer futuro entra al radar
como el mismo `MarketplaceListing`. El dominio no conoce el formato de ninguna
fuente: sólo este contrato.

Ninguna de estas estructuras guarda contenido crudo de la fuente. Se conserva el
subconjunto normalizado necesario para decidir, con su TTL declarado, para no
retener material sujeto a licencia más de lo permitido.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


LISTING_KINDS = ("fixed_price", "auction", "best_offer", "unknown")
AVAILABILITY_STATES = ("unknown", "available", "ended")


@dataclass(slots=True)
class MarketplaceListing:
    """Una publicación normalizada. Identidad: `source_id` + `external_id`."""

    source_id: str
    external_id: str
    title: str
    listing_url: str
    description: str = ""
    image_url: str = ""
    listing_kind: str = "unknown"
    price_amount: float | None = None
    price_currency: str = ""
    shipping_amount: float | None = None
    shipping_currency: str = ""
    price_label: str = ""
    shipping_label: str = ""
    condition_label: str = ""
    location_label: str = ""
    seller_label: str = ""
    region_label: str = ""
    availability: str = "unknown"
    closes_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def total_amount(self) -> float | None:
        """Artículo + envío cuando ambos se conocen; nunca un número inventado."""
        if self.price_amount is None:
            return None
        if self.shipping_amount is None:
            return self.price_amount
        return round(self.price_amount + self.shipping_amount, 2)

    @property
    def shipping_is_known(self) -> bool:
        return self.shipping_amount is not None

    def searchable_text(self) -> str:
        return " ".join(part for part in (self.title, self.description, self.condition_label) if part)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["totalAmount"] = self.total_amount
        return payload


@dataclass(slots=True)
class SourceReceipt:
    """Evidencia de qué consultó una fuente en una corrida.

    Igual que los recibos por grupo de Auction Watch: una respuesta vacía sólo es
    inventario vacío cuando el adapter demuestra que la consulta fue válida y
    completa.
    """

    source_id: str
    status: str  # complete | partial | failed
    query: str
    listing_count: int = 0
    error_count: int = 0
    started_at: str = ""
    finished_at: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def is_authoritative(self) -> bool:
        return self.status == "complete" and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "status": self.status,
            "query": self.query,
            "listingCount": self.listing_count,
            "errorCount": self.error_count,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "errors": list(self.errors),
            "authoritative": self.is_authoritative,
        }


@dataclass(slots=True)
class SourcePage:
    """Resultado de un `search()`: publicaciones más el recibo de cobertura."""

    source_id: str
    listings: list[MarketplaceListing] = field(default_factory=list)
    receipt: SourceReceipt | None = None
    cursor: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "listings": [listing.to_dict() for listing in self.listings],
            "receipt": self.receipt.to_dict() if self.receipt else None,
            "cursor": self.cursor,
        }


@dataclass(slots=True)
class SourceHealth:
    source_id: str
    status: str  # ready | unconfigured | degraded | unavailable
    detail: str = ""
    environment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "status": self.status,
            "detail": self.detail,
            "environment": self.environment,
        }


__all__ = [
    "AVAILABILITY_STATES",
    "LISTING_KINDS",
    "MarketplaceListing",
    "SourceHealth",
    "SourcePage",
    "SourceReceipt",
]
