"""Canonical data model shared by every auction source adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass(slots=True)
class AuctionGroup:
    source_id: str
    group_id: str
    title: str
    url: str
    event_at: str = ""
    closing_at: str = ""
    commission_percent: float = 0.0
    currency: str = "UYU"
    location: str = ""
    status: str = "active"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-serializable representation in field order."""

        return asdict(self)


@dataclass(slots=True)
class AuctionLot:
    source_id: str
    source_label: str
    group_id: str
    group_label: str
    group_url: str
    lot_id: str
    lot_number: str
    title: str
    description: str
    lot_url: str
    image_url: str = ""
    currency: str = "UYU"
    base_price: float = 0
    current_price: float = 0
    next_bid: float = 0
    commission_percent: float = 0
    packaging_cost: float = 0
    bid_count: int = 0
    event_at: str = ""
    closing_at: str = ""
    status: str = "active"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-serializable representation in field order."""

        return asdict(self)


@dataclass(slots=True)
class SourceScanResult:
    source_id: str
    label: str
    groups: list[AuctionGroup] = field(default_factory=list)
    lots: list[AuctionLot] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the complete source result without sharing mutable containers."""

        return asdict(self)


AUCTION_GROUP_FIELDS = tuple(item.name for item in fields(AuctionGroup))
AUCTION_LOT_FIELDS = tuple(item.name for item in fields(AuctionLot))
SOURCE_SCAN_RESULT_FIELDS = tuple(item.name for item in fields(SourceScanResult))


__all__ = [
    "AUCTION_GROUP_FIELDS",
    "AUCTION_LOT_FIELDS",
    "SOURCE_SCAN_RESULT_FIELDS",
    "AuctionGroup",
    "AuctionLot",
    "SourceScanResult",
]
