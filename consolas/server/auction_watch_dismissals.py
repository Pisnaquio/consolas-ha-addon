"""Auction Watch: write-request checks, lot dismissals and lifecycle reconciliation."""

from __future__ import annotations

import sqlite3
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        AUCTION_WATCH_DISMISSALS_VERSION,
        AUCTION_WATCH_DISMISSAL_GRACE,
        AUCTION_WATCH_PUBLICATION_LIFECYCLE_VERSION,
        _AUCTION_WATCH_DISMISSALS_LOCK,
    )
    from .database import connect_db
    from .errors import ApiError
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import (
        AUCTION_WATCH_DISMISSALS_VERSION,
        AUCTION_WATCH_DISMISSAL_GRACE,
        AUCTION_WATCH_PUBLICATION_LIFECYCLE_VERSION,
        _AUCTION_WATCH_DISMISSALS_LOCK,
    )
    from database import connect_db
    from errors import ApiError


def normalize_auction_watch_identity(source_id: Any, lot_id: Any) -> tuple[str, str]:
    source = str(source_id or "").strip().lower()
    lot = str(lot_id or "").strip()
    if not source or len(source) > 64:
        raise ApiError(HTTPStatus.BAD_REQUEST, "sourceId is required")
    if not lot or len(lot) > 256:
        raise ApiError(HTTPStatus.BAD_REQUEST, "lotId is required")
    if not all(character.isalnum() or character in {"-", "_"} for character in source):
        raise ApiError(HTTPStatus.BAD_REQUEST, "sourceId is invalid")
    return source, lot


def normalize_public_http_url(value: Any, field: str = "lotUrl") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must not include credentials")
    return raw


def require_auction_watch_write_request(handler: BaseHTTPRequestHandler) -> None:
    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type application/json is required")
    if handler.headers.get("X-Consolas-Auction-Watch") != "1":
        raise ApiError(HTTPStatus.FORBIDDEN, "Auction Watch action header is required")


def auction_watch_dismissal_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sourceId": row["source_id"],
        "lotId": row["lot_id"],
        "groupId": row["group_id"],
        "title": row["title"],
        "lotUrl": row["lot_url"],
        "imageUrl": row["image_url"],
        "dismissedAt": row["dismissed_at"],
        "updatedAt": row["updated_at"],
    }


def list_auction_watch_dismissals(config: AppConfig) -> dict[str, Any]:
    with _AUCTION_WATCH_DISMISSALS_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            """
            SELECT source_id, lot_id, group_id, title, lot_url, image_url, dismissed_at, updated_at
            FROM auction_watch_dismissals
            ORDER BY updated_at DESC, source_id, lot_id
            """
        ).fetchall()
    items = [auction_watch_dismissal_row(row) for row in rows]
    return {
        "version": AUCTION_WATCH_DISMISSALS_VERSION,
        "updatedAt": items[0]["updatedAt"] if items else None,
        "items": items,
    }


def dismiss_auction_watch_lot(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    source_id, lot_id = normalize_auction_watch_identity(payload.get("sourceId"), payload.get("lotId"))
    group_id = str(payload.get("groupId") or "").strip()[:256]
    title = str(payload.get("title") or "").strip()[:500]
    lot_url = normalize_public_http_url(payload.get("lotUrl"))[:2048]
    image_url = normalize_public_http_url(payload.get("imageUrl") or payload.get("image_url"))[:2048]
    now = utc_now()
    with _AUCTION_WATCH_DISMISSALS_LOCK, connect_db(config) as conn:
        # A discarded lot should not remain in the user's active follow-up list.
        conn.execute(
            "DELETE FROM auction_watch_following WHERE source_id = ? AND lot_id = ?",
            (source_id, lot_id),
        )
        conn.execute(
            """
            INSERT INTO auction_watch_dismissals (
              source_id, lot_id, group_id, title, lot_url, image_url, dismissed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, lot_id) DO UPDATE SET
              group_id = CASE WHEN excluded.group_id <> '' THEN excluded.group_id ELSE group_id END,
              title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE title END,
              lot_url = CASE WHEN excluded.lot_url <> '' THEN excluded.lot_url ELSE lot_url END,
              image_url = CASE WHEN excluded.image_url <> '' THEN excluded.image_url ELSE image_url END,
              updated_at = excluded.updated_at
            """,
            (source_id, lot_id, group_id, title, lot_url, image_url, now, now),
        )
        row = conn.execute(
            """
            SELECT source_id, lot_id, group_id, title, lot_url, image_url, dismissed_at, updated_at
            FROM auction_watch_dismissals
            WHERE source_id = ? AND lot_id = ?
            """,
            (source_id, lot_id),
        ).fetchone()
    return {"ok": True, "item": auction_watch_dismissal_row(row)}


def restore_auction_watch_lot(config: AppConfig, source_id: Any, lot_id: Any) -> dict[str, Any]:
    source, lot = normalize_auction_watch_identity(source_id, lot_id)
    with _AUCTION_WATCH_DISMISSALS_LOCK, connect_db(config) as conn:
        cursor = conn.execute(
            "DELETE FROM auction_watch_dismissals WHERE source_id = ? AND lot_id = ?",
            (source, lot),
        )
    return {"ok": True, "removed": cursor.rowcount > 0, "sourceId": source, "lotId": lot}


def normalize_auction_watch_lifecycle(
    payload: dict[str, Any],
) -> tuple[set[tuple[str, str]], dict[str, dict[str, Any]]] | None:
    lifecycle = payload.get("publicationLifecycle")
    if not isinstance(lifecycle, dict) or lifecycle.get("version") != AUCTION_WATCH_PUBLICATION_LIFECYCLE_VERSION:
        return None
    raw_keys = lifecycle.get("activeKeys")
    raw_health = lifecycle.get("sourceHealth")
    if not isinstance(raw_keys, list) or len(raw_keys) > 10_000 or not isinstance(raw_health, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch publication lifecycle is invalid")

    active_keys: set[tuple[str, str]] = set()
    for raw_key in raw_keys:
        if not isinstance(raw_key, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch lifecycle key must be an object")
        active_keys.add(normalize_auction_watch_identity(raw_key.get("sourceId"), raw_key.get("lotId")))

    source_health: dict[str, dict[str, Any]] = {}
    for raw_source, raw_status in raw_health.items():
        source_id, _ = normalize_auction_watch_identity(raw_source, "lifecycle")
        if isinstance(raw_status, dict):
            status = str(raw_status.get("status") or "").strip().lower()
            inventory_authoritative = raw_status.get("inventoryAuthoritative") is True
        else:
            # Legacy publishers only reported source availability. Treating a
            # successful incremental check as a complete inventory can expire
            # valid user dismissals, so legacy strings are deliberately safe.
            status = str(raw_status or "").strip().lower()
            inventory_authoritative = False
        if status not in {"success", "partial", "failed", "unknown"}:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch lifecycle source status is invalid")
        source_health[source_id] = {
            "status": status,
            "inventoryAuthoritative": inventory_authoritative,
        }
    return active_keys, source_health


def normalize_active_match_metadata(payload: dict[str, Any]) -> dict[tuple[str, str], str]:
    raw_items = payload.get("activeMatchMetadata")
    if raw_items is None:
        return {}
    if not isinstance(raw_items, list) or len(raw_items) > 10_000:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch active match metadata is invalid")
    metadata: dict[tuple[str, str], str] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch active match metadata item is invalid")
        key = normalize_auction_watch_identity(raw_item.get("sourceId"), raw_item.get("lotId"))
        # imageUrl es metadata opcional y decorativa. Una sola invalida (por ejemplo una ruta
        # relativa que devuelve la fuente) no debe rechazar el snapshot completo y dejar al
        # usuario sin oportunidades: se descarta esa imagen y se publica el resto.
        try:
            image_url = normalize_public_http_url(
                raw_item.get("imageUrl") or raw_item.get("image_url"), "imageUrl"
            )[:2048]
        except ApiError:
            image_url = ""
        if image_url:
            metadata[key] = image_url
    return metadata


def reconcile_auction_watch_dismissals(
    config: AppConfig,
    lifecycle: tuple[set[tuple[str, str]], dict[str, Any]] | None,
    *,
    active_image_urls: dict[tuple[str, str], str] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, int | bool]:
    """Expire dismissals only after a healthy source has missed them for the grace period."""
    if lifecycle is None:
        return {"applied": False, "expired": 0, "tracking": 0, "protected": 0}

    active_keys, source_health = lifecycle
    active_image_urls = active_image_urls or {}
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_value = now.isoformat().replace("+00:00", "Z")
    cutoff_value = (now - AUCTION_WATCH_DISMISSAL_GRACE).isoformat().replace("+00:00", "Z")
    expired = 0
    tracking = 0
    protected = 0

    with _AUCTION_WATCH_DISMISSALS_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            "SELECT source_id, lot_id, image_url, missing_since FROM auction_watch_dismissals"
        ).fetchall()
        for row in rows:
            source_id = str(row["source_id"])
            lot_id = str(row["lot_id"])
            raw_health = source_health.get(source_id)
            if isinstance(raw_health, dict):
                status = str(raw_health.get("status") or "unknown")
                inventory_authoritative = raw_health.get("inventoryAuthoritative") is True
            else:
                status = str(raw_health or "unknown")
                inventory_authoritative = False

            if (source_id, lot_id) in active_keys:
                image_url = active_image_urls.get((source_id, lot_id), "")
                if image_url and image_url != str(row["image_url"] or ""):
                    conn.execute(
                        """
                        UPDATE auction_watch_dismissals
                        SET image_url = ?, updated_at = ?
                        WHERE source_id = ? AND lot_id = ?
                        """,
                        (image_url, now_value, source_id, lot_id),
                    )
                if row["missing_since"]:
                    conn.execute(
                        """
                        UPDATE auction_watch_dismissals
                        SET missing_since = NULL, updated_at = ?
                        WHERE source_id = ? AND lot_id = ?
                        """,
                        (now_value, source_id, lot_id),
                    )
                continue

            if status != "success" or not inventory_authoritative:
                if row["missing_since"]:
                    conn.execute(
                        """
                        UPDATE auction_watch_dismissals
                        SET missing_since = NULL, updated_at = ?
                        WHERE source_id = ? AND lot_id = ?
                        """,
                        (now_value, source_id, lot_id),
                    )
                    protected += 1
                continue

            missing_since = str(row["missing_since"] or "")
            if missing_since and missing_since <= cutoff_value:
                conn.execute(
                    "DELETE FROM auction_watch_dismissals WHERE source_id = ? AND lot_id = ?",
                    (source_id, lot_id),
                )
                expired += 1
            elif not missing_since:
                conn.execute(
                    """
                    UPDATE auction_watch_dismissals
                    SET missing_since = ?, updated_at = ?
                    WHERE source_id = ? AND lot_id = ?
                    """,
                    (now_value, now_value, source_id, lot_id),
                )
                tracking += 1

    return {"applied": True, "expired": expired, "tracking": tracking, "protected": protected}
