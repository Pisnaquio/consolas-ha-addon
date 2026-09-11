"""Registry de fuentes de Collection Radar.

Agregar una fuente requiere un módulo adapter y una entrada en
`CONFIGURED_SOURCES`. Nada más: ni el scheduler, ni el scoring, ni la UI
conocen fuentes concretas.

Los imports son lazy, igual que en `agents/auction-watch/sources/registry.py`:
una fuente rota al importar no puede impedir que el resto corra.

Cada fuente declara sus capabilities y su modo de cumplimiento. Una fuente sin
`officialApi` ni una vía autorizada queda `executable=False` con su razón
visible: se puede guardar en una búsqueda, pero nunca se ejecuta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol

from ..model import SourceHealth, SourcePage


def default_capabilities(**overrides: Any) -> dict[str, Any]:
    """Capabilities mínimas del contrato; una fuente sólo declara lo que tiene."""
    capabilities: dict[str, Any] = {
        "officialApi": False,
        "savedSearchAlerts": False,
        "fixedPrice": False,
        "auction": False,
        "bestOffer": False,
        "itemDetail": False,
        "availabilityCheck": False,
        "shippingQuote": False,
        "sellerMetrics": False,
        "returnPolicy": False,
        "soldComparables": False,
        "requiresAccount": False,
        "requiresAssistedVerification": False,
        "contentTtlSeconds": 0,
        "termsMode": "unknown",
    }
    unknown = set(overrides) - set(capabilities)
    if unknown:
        raise ValueError(f"Unknown capability flag(s): {', '.join(sorted(unknown))}")
    capabilities.update(overrides)
    return capabilities


class RadarSource(Protocol):
    source_id: str
    label: str

    def search(self, config: Any, query: str, criteria: dict[str, Any]) -> SourcePage: ...

    def health(self, config: Any) -> SourceHealth: ...


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_id: str
    label: str
    capabilities: dict[str, Any]
    adapter_path: str = ""
    unavailable_reason: str = ""

    @property
    def executable(self) -> bool:
        return bool(self.adapter_path)

    def load(self) -> RadarSource:
        if not self.adapter_path:
            raise ValueError(f"Source {self.source_id!r} declares no adapter and cannot run")
        module_name, separator, attribute_name = self.adapter_path.partition(":")
        if not separator or not module_name or not attribute_name:
            raise ValueError(
                f"Invalid adapter path {self.adapter_path!r}; expected 'module:attribute'"
            )
        adapter_factory = getattr(import_module(module_name, package=__package__), attribute_name)
        adapter = adapter_factory()
        adapter_source_id = str(getattr(adapter, "source_id", "") or "")
        if adapter_source_id != self.source_id:
            raise ValueError(
                f"Adapter {self.adapter_path!r} declares source_id "
                f"{adapter_source_id!r}, expected {self.source_id!r}"
            )
        for required in ("search", "health"):
            if not callable(getattr(adapter, required, None)):
                raise TypeError(f"Adapter {self.adapter_path!r} does not define {required}()")
        return adapter

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.source_id,
            "label": self.label,
            "executable": self.executable,
            "unavailableReason": self.unavailable_reason,
            "capabilities": dict(self.capabilities),
        }


CONFIGURED_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        source_id="ebay-us",
        label="eBay USA",
        adapter_path=".ebay:EbayBrowseSource",
        capabilities=default_capabilities(
            officialApi=True,
            fixedPrice=True,
            auction=True,
            contentTtlSeconds=86400,
            termsMode="official-api",
        ),
    ),
    SourceSpec(
        source_id="shopgoodwill",
        label="ShopGoodwill",
        unavailable_reason=(
            "Sólo por Personal Shopper y alertas oficiales. Todavía no está integrado."
        ),
        capabilities=default_capabilities(
            savedSearchAlerts=True,
            fixedPrice=True,
            auction=True,
            requiresAccount=True,
            requiresAssistedVerification=True,
            termsMode="assisted-official-alerts",
        ),
    ),
    SourceSpec(
        source_id="mercari-us",
        label="Mercari USA",
        unavailable_reason=(
            "Fuera del MVP: no hay cuenta ni medio de pago estadounidense y sus políticas "
            "prohíben el acceso automatizado. Sólo sirve como referencia manual."
        ),
        capabilities=default_capabilities(
            savedSearchAlerts=True,
            fixedPrice=True,
            bestOffer=True,
            requiresAccount=True,
            requiresAssistedVerification=True,
            termsMode="manual-reference-only",
        ),
    ),
)

SOURCES_BY_ID: dict[str, SourceSpec] = {spec.source_id: spec for spec in CONFIGURED_SOURCES}


def get_source(source_id: str) -> SourceSpec | None:
    return SOURCES_BY_ID.get(str(source_id or ""))


def executable_source_ids(source_ids: list[str] | tuple[str, ...]) -> list[str]:
    runnable: list[str] = []
    for source_id in source_ids:
        spec = get_source(source_id)
        if spec is not None and spec.executable:
            runnable.append(source_id)
    return runnable


def sources_payload() -> list[dict[str, Any]]:
    return [spec.to_dict() for spec in CONFIGURED_SOURCES]


__all__ = [
    "CONFIGURED_SOURCES",
    "SOURCES_BY_ID",
    "RadarSource",
    "SourceSpec",
    "default_capabilities",
    "executable_source_ids",
    "get_source",
    "sources_payload",
]
