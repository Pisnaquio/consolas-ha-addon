"""Collection Radar: source page collection and listing upserts."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from radar.model import MarketplaceListing
from radar.sources import registry as radar_registry

try:
    from .clock import utc_now
    from .config import AppConfig
    from .radar_criteria import (
        executable_radar_sources,
        radar_query_criteria,
        radar_source_capabilities,
    )
    from .radar_preferences import radar_destination
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from radar_criteria import (
        executable_radar_sources,
        radar_query_criteria,
        radar_source_capabilities,
    )
    from radar_preferences import radar_destination


def radar_listing_amount(value: Any) -> float | None:
    try:
        amount = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if amount != amount or amount in {float("inf"), float("-inf")} or amount < 0:
        return None
    return round(amount, 2)


def radar_listing_id(source_id: str, external_id: str) -> str:
    """Identidad de una publicación: la fuente y su id externo, nada más.

    No incluye la búsqueda: ese es justamente el punto de separar publicaciones
    de coincidencias.
    """
    digest = hashlib.sha1(f"{source_id}\0{external_id}".encode("utf-8")).hexdigest()[:20]
    return f"{source_id}-{digest}"


def radar_listing_kind_from_label(label: str) -> str:
    """Compat: las filas viejas guardaban el tipo como etiqueta en castellano."""
    normalized = str(label or "").strip().lower()
    if normalized.startswith("subasta"):
        return "auction"
    if normalized.startswith("compra"):
        return "fixed_price"
    return "unknown"


def collect_radar_source_pages(
    config: AppConfig, queries: list[str], criteria: dict[str, Any], sources: list[str]
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Consulta cada fuente ejecutable de forma aislada, una vez por consulta.

    Una fuente rota no puede ocultar a las demás ni vaciar el inventario: su falla
    queda en el recibo y el resto de las publicaciones sigue llegando. Lo mismo
    vale consulta por consulta: que «Patapon 2» falle no invalida lo que trajo
    «Patapon», y cada recibo dice a qué consulta corresponde.

    Una publicación que aparece en dos consultas entra una sola vez: dos
    consultas son dos formas de encontrar la misma cosa, no dos oportunidades.

    Cada consulta viaja con sus propios criterios: si su título tiene un techo
    más bajo que el de la búsqueda, la fuente ya filtra por ese techo y no
    devuelve cincuenta copias que se van a descartar después.
    """

    # El destino sale de las preferencias, no de los criterios de la búsqueda:
    # es dónde recibe el dueño, no qué está buscando. Una sola vez por corrida.
    destination = radar_destination(config)
    pages: list[Any] = []
    receipts: list[dict[str, Any]] = []
    for source_id in executable_radar_sources(sources):
        spec = radar_registry.get_source(source_id)
        if spec is None:  # pragma: no cover - defensivo
            continue
        seen: set[str] = set()
        for query in queries:
            started_at = utc_now()
            try:
                adapter = spec.load()
                page = adapter.search(config, query, radar_query_criteria(criteria, query), destination)
            except Exception as error:  # el adapter es código aislado: nunca tumba la corrida
                receipts.append(
                    {
                        "sourceId": source_id,
                        "status": "failed",
                        "query": query,
                        "listingCount": 0,
                        "errorCount": 1,
                        "startedAt": started_at,
                        "finishedAt": utc_now(),
                        "errors": [f"{spec.label}: {error}"],
                        "authoritative": False,
                    }
                )
                continue
            fresh = [listing for listing in page.listings if listing.external_id not in seen]
            seen.update(listing.external_id for listing in fresh)
            page.listings = fresh
            pages.append(page)
            receipt = page.receipt.to_dict() if page.receipt else None
            if receipt is not None:
                receipt["errors"] = [f"{spec.label}: {message}" for message in receipt.get("errors") or []]
                receipts.append(receipt)
    return pages, receipts


def upsert_radar_listing(conn: sqlite3.Connection, listing: MarketplaceListing, now: str) -> str:
    """Guarda la publicación una sola vez, sin importar cuántas búsquedas la vean."""
    listing_id = radar_listing_id(listing.source_id, listing.external_id)
    ttl_seconds = int(radar_source_capabilities(listing.source_id).get("contentTtlSeconds") or 0)
    expires_at = ""
    if ttl_seconds > 0:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat().replace("+00:00", "Z")
    conn.execute(
        """
        INSERT INTO radar_listings (
          id, source_id, external_id, title, description, listing_url, image_url, listing_kind,
          price_amount, price_currency, shipping_amount, shipping_currency, total_amount,
          price_label, shipping_label, condition_label, location_label, seller_label,
          availability, closes_at, content_expires_at, first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, external_id) DO UPDATE SET
          -- Sólo se mueve el precio anterior cuando el precio efectivamente cambió:
          -- una corrida que ve lo mismo no puede borrar la referencia de la baja.
          previous_price_amount = CASE
            WHEN excluded.price_amount IS NOT NULL AND radar_listings.price_amount IS NOT NULL
                 AND excluded.price_amount != radar_listings.price_amount
            THEN radar_listings.price_amount
            ELSE radar_listings.previous_price_amount END,
          price_changed_at = CASE
            WHEN excluded.price_amount IS NOT NULL AND radar_listings.price_amount IS NOT NULL
                 AND excluded.price_amount != radar_listings.price_amount
            THEN excluded.last_seen_at
            ELSE radar_listings.price_changed_at END,
          title=excluded.title, description=excluded.description, listing_url=excluded.listing_url,
          image_url=excluded.image_url, listing_kind=excluded.listing_kind,
          price_amount=excluded.price_amount, price_currency=excluded.price_currency,
          shipping_amount=excluded.shipping_amount, shipping_currency=excluded.shipping_currency,
          total_amount=excluded.total_amount, price_label=excluded.price_label,
          shipping_label=excluded.shipping_label, condition_label=excluded.condition_label,
          location_label=excluded.location_label, seller_label=excluded.seller_label,
          availability=excluded.availability, closes_at=excluded.closes_at,
          content_expires_at=excluded.content_expires_at, last_seen_at=excluded.last_seen_at
        """,
        (
            listing_id, listing.source_id, listing.external_id, listing.title, listing.description,
            listing.listing_url, listing.image_url, listing.listing_kind,
            listing.price_amount, listing.price_currency, listing.shipping_amount, listing.shipping_currency,
            listing.total_amount, listing.price_label, listing.shipping_label, listing.condition_label,
            listing.location_label, listing.seller_label, listing.availability, listing.closes_at,
            expires_at or None, now, now,
        ),
    )
    return listing_id
