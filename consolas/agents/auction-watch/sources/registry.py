"""Lazy registry for auction source adapters.

Adding a source requires one adapter module and one :class:`SourceSpec` entry in
``CONFIGURED_SOURCES``. Imports stay lazy so a broken optional source cannot
prevent the remaining sources from running.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Iterable, Protocol

import requests

from .model import AuctionLot, SourceScanResult


class AuctionSource(Protocol):
    source_id: str
    label: str

    def collect(
        self,
        session: requests.Session,
        timeout: float = 25,
    ) -> SourceScanResult: ...

    def enrich_lots(
        self,
        session: requests.Session,
        lots: list[AuctionLot],
        timeout: float = 25,
    ) -> list[AuctionLot] | None: ...


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_id: str
    label: str
    adapter_path: str

    def load(self) -> AuctionSource:
        module_name, separator, attribute_name = self.adapter_path.partition(":")
        if not separator or not module_name or not attribute_name:
            raise ValueError(
                f"Invalid adapter path {self.adapter_path!r}; expected 'module:attribute'"
            )

        adapter_factory = getattr(import_module(module_name), attribute_name)
        adapter = adapter_factory()
        adapter_source_id = str(getattr(adapter, "source_id", "") or "")
        if adapter_source_id != self.source_id:
            raise ValueError(
                f"Adapter {self.adapter_path!r} declares source_id "
                f"{adapter_source_id!r}, expected {self.source_id!r}"
            )
        if not callable(getattr(adapter, "collect", None)):
            raise TypeError(f"Adapter {self.adapter_path!r} does not define collect()")
        return adapter


CONFIGURED_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("remotes", "Remotes", "sources.remotes:RemotesSource"),
    SourceSpec("todoremates", "TodoRemates", "sources.todoremates:TodoRematesSource"),
    SourceSpec("prado", "Prado Subastas", "sources.prado:PradoSource"),
)


def configured_sources(source_ids: Iterable[str] | None = None) -> list[SourceSpec]:
    """Return configured source specs, optionally restricted by source id.

    The requested order is preserved and unknown ids fail early. The function
    returns specs rather than loaded adapters so callers can isolate import and
    construction failures per source.
    """

    if source_ids is None:
        return list(CONFIGURED_SOURCES)

    by_id = {spec.source_id: spec for spec in CONFIGURED_SOURCES}
    requested = list(dict.fromkeys(str(item).strip() for item in source_ids if str(item).strip()))
    unknown = [source_id for source_id in requested if source_id not in by_id]
    if unknown:
        raise ValueError(f"Unknown auction source(s): {', '.join(unknown)}")
    return [by_id[source_id] for source_id in requested]


__all__ = [
    "AuctionSource",
    "CONFIGURED_SOURCES",
    "SourceSpec",
    "configured_sources",
]
