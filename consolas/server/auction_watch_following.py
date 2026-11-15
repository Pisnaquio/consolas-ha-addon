"""Auction Watch: followed lots."""

from __future__ import annotations

import sqlite3
from http import HTTPStatus
from typing import Any

try:
    from .auction_watch_dismissals import normalize_auction_watch_identity, normalize_public_http_url
    from .clock import utc_now
    from .config import AppConfig
    from .constants import AUCTION_WATCH_FOLLOWING_VERSION, _AUCTION_WATCH_DISMISSALS_LOCK
    from .database import connect_db
    from .errors import ApiError
except ImportError:  # Direct execution: `python3 server/app.py`.
    from auction_watch_dismissals import normalize_auction_watch_identity, normalize_public_http_url
    from clock import utc_now
    from config import AppConfig
    from constants import AUCTION_WATCH_FOLLOWING_VERSION, _AUCTION_WATCH_DISMISSALS_LOCK
    from database import connect_db
    from errors import ApiError


def auction_watch_following_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sourceId": row["source_id"],
        "lotId": row["lot_id"],
        "groupId": row["group_id"],
        "title": row["title"],
        "lotUrl": row["lot_url"],
        "followedAt": row["followed_at"],
        "updatedAt": row["updated_at"],
    }


def list_auction_watch_following(config: AppConfig) -> dict[str, Any]:
    with _AUCTION_WATCH_DISMISSALS_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            """
            SELECT source_id, lot_id, group_id, title, lot_url, followed_at, updated_at
            FROM auction_watch_following
            ORDER BY updated_at DESC, source_id, lot_id
            """
        ).fetchall()
    items = [auction_watch_following_row(row) for row in rows]
    return {
        "version": AUCTION_WATCH_FOLLOWING_VERSION,
        "updatedAt": items[0]["updatedAt"] if items else None,
        "items": items,
    }


def follow_auction_watch_lot(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    source_id, lot_id = normalize_auction_watch_identity(payload.get("sourceId"), payload.get("lotId"))
    group_id = str(payload.get("groupId") or "").strip()[:256]
    title = str(payload.get("title") or "").strip()[:500]
    lot_url = normalize_public_http_url(payload.get("lotUrl"))[:2048]
    now = utc_now()
    with _AUCTION_WATCH_DISMISSALS_LOCK, connect_db(config) as conn:
        # Following is the inverse user decision of dismissal. Keep both
        # first-class tables mutually exclusive in either action direction.
        conn.execute(
            "DELETE FROM auction_watch_dismissals WHERE source_id = ? AND lot_id = ?",
            (source_id, lot_id),
        )
        conn.execute(
            """
            INSERT INTO auction_watch_following (
              source_id, lot_id, group_id, title, lot_url, followed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, lot_id) DO UPDATE SET
              group_id = CASE WHEN excluded.group_id <> '' THEN excluded.group_id ELSE group_id END,
              title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE title END,
              lot_url = CASE WHEN excluded.lot_url <> '' THEN excluded.lot_url ELSE lot_url END,
              updated_at = excluded.updated_at
            """,
            (source_id, lot_id, group_id, title, lot_url, now, now),
        )
        row = conn.execute(
            """
            SELECT source_id, lot_id, group_id, title, lot_url, followed_at, updated_at
            FROM auction_watch_following
            WHERE source_id = ? AND lot_id = ?
            """,
            (source_id, lot_id),
        ).fetchone()
    return {"ok": True, "item": auction_watch_following_row(row)}


def unfollow_auction_watch_lot(config: AppConfig, source_id: Any, lot_id: Any) -> dict[str, Any]:
    source, lot = normalize_auction_watch_identity(source_id, lot_id)
    with _AUCTION_WATCH_DISMISSALS_LOCK, connect_db(config) as conn:
        cursor = conn.execute(
            "DELETE FROM auction_watch_following WHERE source_id = ? AND lot_id = ?",
            (source, lot),
        )
    return {"ok": True, "removed": cursor.rowcount > 0, "sourceId": source, "lotId": lot}
