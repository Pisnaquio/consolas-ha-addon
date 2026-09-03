"""Reusable source adapters and their canonical auction data model."""

from .model import AuctionGroup, AuctionLot, SourceScanResult
from .registry import CONFIGURED_SOURCES, SourceSpec, configured_sources

__all__ = [
    "AuctionGroup",
    "AuctionLot",
    "CONFIGURED_SOURCES",
    "SourceScanResult",
    "SourceSpec",
    "configured_sources",
]
