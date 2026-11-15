"""Chasing Games compatibility surface projected over Collection Radar."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        CHASING_GAMES_VERSION,
        RADAR_DEFAULT_RESULT_LIMIT,
        RADAR_DEFAULT_SOURCE,
        RADAR_WRITE_HEADER,
        _RADAR_LOCK,
    )
    from .database import connect_db
    from .errors import ApiError
    from .radar_criteria import (
        build_radar_search_query,
        default_radar_criteria,
        normalize_radar_id,
        normalize_radar_text,
    )
    from .radar_execution import run_radar_search
    from .radar_results import radar_environment_label, radar_search_results, radar_search_row
    from .radar_scheduler import run_active_radar_searches
    from .radar_searches import delete_radar_search
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import (
        CHASING_GAMES_VERSION,
        RADAR_DEFAULT_RESULT_LIMIT,
        RADAR_DEFAULT_SOURCE,
        RADAR_WRITE_HEADER,
        _RADAR_LOCK,
    )
    from database import connect_db
    from errors import ApiError
    from radar_criteria import (
        build_radar_search_query,
        default_radar_criteria,
        normalize_radar_id,
        normalize_radar_text,
    )
    from radar_execution import run_radar_search
    from radar_results import radar_environment_label, radar_search_results, radar_search_row
    from radar_scheduler import run_active_radar_searches
    from radar_searches import delete_radar_search


def require_chasing_games_write_request(handler: BaseHTTPRequestHandler) -> None:
    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type application/json is required")
    if handler.headers.get("X-Consolas-Chasing-Games") != "1" and handler.headers.get(RADAR_WRITE_HEADER) != "1":
        raise ApiError(HTTPStatus.FORBIDDEN, "Chasing Games action header is required")


# --------------------------------------------------------------------------- #
# Compatibilidad: la superficie previa de Chasing Games proyecta el radar       #
# --------------------------------------------------------------------------- #


def chasing_game_row(row: sqlite3.Row, results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    search = radar_search_row(row, results)
    return {
        "id": search["id"],
        "title": search["name"],
        "platform": search["platform"],
        "searchQuery": search["searchQuery"],
        "source": (search["sources"] or [RADAR_DEFAULT_SOURCE])[0],
        "enabled": search["enabled"],
        "createdAt": search["createdAt"],
        "updatedAt": search["updatedAt"],
        "lastCheckedAt": search["lastCheckedAt"],
        "lastError": search["lastError"],
        "results": [
            {
                "id": item["id"], "title": item["title"], "priceLabel": item["priceLabel"],
                "conditionLabel": item["conditionLabel"], "shippingLabel": item["shippingLabel"],
                "locationLabel": item["locationLabel"], "listingType": item["listingType"],
                "listingUrl": item["listingUrl"], "imageUrl": item["imageUrl"], "lastSeenAt": item["lastSeenAt"],
            }
            for item in search["results"]
        ],
    }


def list_chasing_games(config: AppConfig) -> dict[str, Any]:
    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            """SELECT * FROM radar_searches WHERE deleted_at IS NULL AND status != 'archived'
               ORDER BY status = 'active' DESC, updated_at DESC, name"""
        ).fetchall()
        output = [
            chasing_game_row(row, radar_search_results(conn, str(row["id"]), RADAR_DEFAULT_RESULT_LIMIT))
            for row in rows
        ]
    return {
        "version": CHASING_GAMES_VERSION,
        "source": radar_environment_label(config),
        "environment": config.ebay_environment,
        "items": output,
    }


def create_chasing_game(config: AppConfig, payload: Any) -> dict[str, Any]:
    """Legacy entry point: deterministic id, re-enable on conflict and immediate run."""
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    title = normalize_radar_text(payload.get("title"), "title", 200)
    platform = normalize_radar_text(payload.get("platform"), "platform", 100, required=False)
    criteria = default_radar_criteria()
    search_query = build_radar_search_query(title, platform, criteria, payload.get("searchQuery"))
    chase_id = f"chase-{hashlib.sha1(f'{title}\0{platform}'.lower().encode('utf-8')).hexdigest()[:16]}"
    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        conn.execute(
            """INSERT INTO radar_searches (
                 id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
                 search_query, criteria_json, sources_json, notes, created_at, updated_at
               ) VALUES (?, ?, 'chase', 'active', 'user', 'media', ?, '', '', ?, ?, ?, '', ?, ?)
               ON CONFLICT(id) DO UPDATE SET status = 'active', deleted_at = NULL, updated_at = excluded.updated_at""",
            (
                chase_id, title, platform, search_query,
                json.dumps(criteria, ensure_ascii=False, separators=(",", ":")),
                json.dumps([RADAR_DEFAULT_SOURCE], ensure_ascii=False, separators=(",", ":")),
                now, now,
            ),
        )
    return run_radar_search(config, chase_id)


def set_chasing_game_enabled(config: AppConfig, chase_id: Any, enabled: bool) -> dict[str, Any]:
    target_id = normalize_radar_id(chase_id)
    with _RADAR_LOCK, connect_db(config) as conn:
        row = conn.execute(
            "SELECT id FROM radar_searches WHERE id = ? AND deleted_at IS NULL", (target_id,)
        ).fetchone()
        if row is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Chase not found")
        now = utc_now()
        conn.execute(
            "UPDATE radar_searches SET status = ?, archived_at = NULL, updated_at = ? WHERE id = ?",
            ("active" if enabled else "paused", now, target_id),
        )
    return {"ok": True, "id": target_id, "enabled": enabled}


def run_chasing_game(config: AppConfig, chase_id: Any) -> dict[str, Any]:
    try:
        return run_radar_search(config, chase_id)
    except ApiError as error:
        if error.status == HTTPStatus.NOT_FOUND:
            raise ApiError(HTTPStatus.NOT_FOUND, "Chase not found") from error
        raise


def delete_chasing_game(config: AppConfig, chase_id: Any) -> dict[str, Any]:
    try:
        return delete_radar_search(config, chase_id)
    except ApiError as error:
        if error.status == HTTPStatus.NOT_FOUND:
            raise ApiError(HTTPStatus.NOT_FOUND, "Chase not found") from error
        raise


def run_enabled_chasing_games(config: AppConfig) -> None:
    run_active_radar_searches(config)
