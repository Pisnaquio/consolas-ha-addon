"""Collection Radar: search CRUD, lifecycle and active capacity."""

from __future__ import annotations

import json
import sqlite3
import uuid
from http import HTTPStatus
from typing import Any

from radar.taxonomy import taxonomy_payload

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        RADAR_ENTITY_TYPES,
        RADAR_PRIORITIES,
        RADAR_SEARCHES_VERSION,
        RADAR_SEARCH_ORIGINS,
        RADAR_SEARCH_STATUSES,
        RADAR_SEARCH_TYPES,
        _RADAR_LOCK,
    )
    from .database import connect_db
    from .errors import ApiError
    from .radar_criteria import (
        build_radar_search_query,
        normalize_radar_criteria,
        normalize_radar_entity_console_id,
        normalize_radar_enum,
        normalize_radar_id,
        normalize_radar_sources,
        normalize_radar_text,
        radar_json_field,
        radar_sources_payload,
    )
    from .radar_preferences import radar_preferences_row
    from .radar_results import (
        load_radar_search,
        radar_environment_label,
        radar_search_inventory,
        radar_search_row,
    )
    from .radar_runs import normalize_radar_slots
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import (
        RADAR_ENTITY_TYPES,
        RADAR_PRIORITIES,
        RADAR_SEARCHES_VERSION,
        RADAR_SEARCH_ORIGINS,
        RADAR_SEARCH_STATUSES,
        RADAR_SEARCH_TYPES,
        _RADAR_LOCK,
    )
    from database import connect_db
    from errors import ApiError
    from radar_criteria import (
        build_radar_search_query,
        normalize_radar_criteria,
        normalize_radar_entity_console_id,
        normalize_radar_enum,
        normalize_radar_id,
        normalize_radar_sources,
        normalize_radar_text,
        radar_json_field,
        radar_sources_payload,
    )
    from radar_preferences import radar_preferences_row
    from radar_results import (
        load_radar_search,
        radar_environment_label,
        radar_search_inventory,
        radar_search_row,
    )
    from radar_runs import normalize_radar_slots


def list_radar_searches(config: AppConfig) -> dict[str, Any]:
    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            """SELECT * FROM radar_searches WHERE deleted_at IS NULL
               ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'draft' THEN 1 WHEN 'paused' THEN 2 ELSE 3 END,
                        updated_at DESC, name"""
        ).fetchall()
        items = []
        for row in rows:
            criteria = normalize_radar_criteria(radar_json_field(row["criteria_json"], None))
            results, meta = radar_search_inventory(conn, str(row["id"]), int(criteria["resultLimit"]), criteria)
            items.append(radar_search_row(row, results, meta))
    counts = {status: sum(1 for item in items if item["status"] == status) for status in RADAR_SEARCH_STATUSES}
    return {
        "version": RADAR_SEARCHES_VERSION,
        "source": radar_environment_label(config),
        "environment": config.ebay_environment,
        "sources": radar_sources_payload(),
        "counts": counts,
        # Los cinco objetivos canónicos y las secciones aparte: la pantalla los
        # dibuja desde acá, sin saber cuáles son.
        **taxonomy_payload(),
        "items": items,
    }


def radar_search_payload(config: AppConfig, search_id: str) -> dict[str, Any]:
    with _RADAR_LOCK, connect_db(config) as conn:
        row = load_radar_search(conn, search_id)
        criteria = normalize_radar_criteria(radar_json_field(row["criteria_json"], None))
        results, meta = radar_search_inventory(conn, search_id, int(criteria["resultLimit"]), criteria)
        return {"ok": True, "search": radar_search_row(row, results, meta)}


def assert_radar_name_is_free(conn: sqlite3.Connection, name: str, platform: str, exclude_id: str = "") -> None:
    row = conn.execute(
        """SELECT id FROM radar_searches
           WHERE deleted_at IS NULL AND lower(name) = ? AND lower(platform) = ? AND id != ?""",
        (name.lower(), platform.lower(), exclude_id),
    ).fetchone()
    if row is not None:
        raise ApiError(
            HTTPStatus.CONFLICT,
            "Ya existe una búsqueda con ese nombre y plataforma",
            {"id": row["id"]},
        )


def create_radar_search(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    name = normalize_radar_text(payload.get("name") or payload.get("title"), "name", 200)
    platform = normalize_radar_text(payload.get("platform"), "platform", 100, required=False)
    search_type = normalize_radar_enum(payload.get("searchType"), RADAR_SEARCH_TYPES, "chase", "searchType")
    status = normalize_radar_enum(payload.get("status"), RADAR_SEARCH_STATUSES, "active", "status")
    origin = normalize_radar_enum(payload.get("origin"), RADAR_SEARCH_ORIGINS, "user", "origin")
    priority = normalize_radar_enum(payload.get("priority"), RADAR_PRIORITIES, "media", "priority")
    entity_type = normalize_radar_enum(payload.get("entityType"), RADAR_ENTITY_TYPES, "", "entityType")
    entity_id = normalize_radar_text(payload.get("entityId"), "entityId", 120, required=False)
    entity_console_id = normalize_radar_entity_console_id(payload.get("entityConsoleId"), entity_type)
    notes = normalize_radar_text(payload.get("notes"), "notes", 600, required=False)
    criteria = normalize_radar_criteria(payload.get("criteria"))
    sources = normalize_radar_sources(payload.get("sources"))
    slots = normalize_radar_slots(payload.get("slots"))
    custom_query = bool(" ".join(str(payload.get("searchQuery") or "").split()).strip())
    search_query = build_radar_search_query(name, platform, criteria, payload.get("searchQuery"))
    now = utc_now()
    search_id = f"radar-{uuid.uuid4().hex[:16]}"
    with _RADAR_LOCK, connect_db(config) as conn:
        # Una búsqueda que nace activa suma al feed igual que una que se activa
        # después: el techo no puede depender de por qué puerta entró.
        if status == "active":
            assert_radar_active_capacity(conn)
        assert_radar_name_is_free(conn, name, platform)
        conn.execute(
            """INSERT INTO radar_searches (
                 id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
                 entity_console_id, search_query, query_custom, criteria_json, sources_json, slots_json,
                 notes, created_at, updated_at, archived_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                search_id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
                entity_console_id, search_query, int(custom_query),
                json.dumps(criteria, ensure_ascii=False, separators=(",", ":")),
                json.dumps(sources, ensure_ascii=False, separators=(",", ":")),
                json.dumps(slots, ensure_ascii=False, separators=(",", ":")),
                notes, now, now, now if status == "archived" else None,
            ),
        )
    return radar_search_payload(config, search_id)


def update_radar_search(config: AppConfig, search_id: Any, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    target_id = normalize_radar_id(search_id)
    with _RADAR_LOCK, connect_db(config) as conn:
        row = load_radar_search(conn, target_id)
        name = (
            normalize_radar_text(payload.get("name") or payload.get("title"), "name", 200)
            if ("name" in payload or "title" in payload)
            else str(row["name"])
        )
        platform = (
            normalize_radar_text(payload.get("platform"), "platform", 100, required=False)
            if "platform" in payload
            else str(row["platform"])
        )
        search_type = (
            normalize_radar_enum(payload.get("searchType"), RADAR_SEARCH_TYPES, "chase", "searchType")
            if "searchType" in payload
            else str(row["search_type"])
        )
        priority = (
            normalize_radar_enum(payload.get("priority"), RADAR_PRIORITIES, "media", "priority")
            if "priority" in payload
            else str(row["priority"])
        )
        entity_type = (
            normalize_radar_enum(payload.get("entityType"), RADAR_ENTITY_TYPES, "", "entityType")
            if "entityType" in payload
            else str(row["entity_type"])
        )
        entity_id = (
            normalize_radar_text(payload.get("entityId"), "entityId", 120, required=False)
            if "entityId" in payload
            else str(row["entity_id"])
        )
        stored_console_id = row["entity_console_id"] if "entity_console_id" in row.keys() else ""
        entity_console_id = normalize_radar_entity_console_id(
            payload.get("entityConsoleId") if "entityConsoleId" in payload else stored_console_id,
            entity_type,
        )
        notes = (
            normalize_radar_text(payload.get("notes"), "notes", 600, required=False)
            if "notes" in payload
            else str(row["notes"])
        )
        stored_criteria = normalize_radar_criteria(radar_json_field(row["criteria_json"], None))
        criteria = (
            normalize_radar_criteria(payload.get("criteria"), stored_criteria)
            if "criteria" in payload
            else stored_criteria
        )
        sources = (
            normalize_radar_sources(payload.get("sources"))
            if "sources" in payload
            else normalize_radar_sources(radar_json_field(row["sources_json"], []))
        )
        slots = normalize_radar_slots(payload.get("slots")) if "slots" in payload else radar_json_field(row["slots_json"], [])
        was_custom = bool(row["query_custom"]) if "query_custom" in row.keys() else False
        if "searchQuery" in payload:
            explicit = " ".join(str(payload.get("searchQuery") or "").split()).strip()
            search_query = build_radar_search_query(name, platform, criteria, explicit)
            was_custom = bool(explicit)
        elif was_custom:
            # Escrita a mano: se respeta aunque cambien nombre o criterios.
            search_query = str(row["search_query"])
        elif "criteria" in payload or "name" in payload or "title" in payload or "platform" in payload:
            search_query = build_radar_search_query(name, platform, criteria)
        else:
            search_query = str(row["search_query"])
        assert_radar_name_is_free(conn, name, platform, target_id)
        conn.execute(
            """UPDATE radar_searches SET
                 name = ?, search_type = ?, priority = ?, platform = ?, entity_type = ?, entity_id = ?,
                 entity_console_id = ?, search_query = ?, query_custom = ?, criteria_json = ?,
                 sources_json = ?, slots_json = ?, notes = ?, updated_at = ?
               WHERE id = ?""",
            (
                name, search_type, priority, platform, entity_type, entity_id, entity_console_id,
                search_query, int(was_custom),
                json.dumps(criteria, ensure_ascii=False, separators=(",", ":")),
                json.dumps(sources, ensure_ascii=False, separators=(",", ":")),
                json.dumps(slots, ensure_ascii=False, separators=(",", ":")),
                notes, utc_now(), target_id,
            ),
        )
    return radar_search_payload(config, target_id)


def radar_active_search_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM radar_searches WHERE status = 'active' AND deleted_at IS NULL"
        ).fetchone()[0]
    )


def assert_radar_active_capacity(conn: sqlite3.Connection) -> None:
    """Rechaza una activación que pase del techo, diciendo los números.

    El error nombra cuántas hay y cuál es el techo porque la salida no es
    obvia: o se retira una búsqueda que ya no importa, o se sube el techo a
    conciencia. Un «no se puede» sin números obliga a adivinar cuál de las dos.
    """

    row = conn.execute("SELECT * FROM radar_preferences WHERE id = 'default'").fetchone()
    cap = radar_preferences_row(row)["maxActiveSearches"]
    active = radar_active_search_count(conn)
    if active >= cap:
        raise ApiError(
            HTTPStatus.CONFLICT,
            f"Ya hay {active} búsquedas activas y el techo es {cap}. "
            "Retirá alguna que ya no te sirva, o subí el techo en las preferencias del radar.",
        )


def set_radar_search_status(config: AppConfig, search_id: Any, status: Any) -> dict[str, Any]:
    target_id = normalize_radar_id(search_id)
    next_status = str(status or "").strip().lower()
    if next_status not in RADAR_SEARCH_STATUSES:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"status must be one of: {', '.join(RADAR_SEARCH_STATUSES)}")
    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        row = load_radar_search(conn, target_id)
        # Activar es lo único que suma carga al feed, así que es lo único que
        # mira el techo. Pausar, archivar y borrar nunca se bloquean: salir
        # siempre tiene que ser posible.
        if next_status == "active" and str(row["status"]) != "active":
            assert_radar_active_capacity(conn)
        conn.execute(
            "UPDATE radar_searches SET status = ?, archived_at = ?, updated_at = ? WHERE id = ?",
            (next_status, now if next_status == "archived" else None, now, target_id),
        )
    return radar_search_payload(config, target_id)


def duplicate_radar_search(config: AppConfig, search_id: Any) -> dict[str, Any]:
    """A copy always starts as a draft: it never inherits results, history or execution."""
    target_id = normalize_radar_id(search_id)
    now = utc_now()
    new_id = f"radar-{uuid.uuid4().hex[:16]}"
    with _RADAR_LOCK, connect_db(config) as conn:
        row = load_radar_search(conn, target_id)
        platform = str(row["platform"])
        base_name = normalize_radar_text(f"{row['name']} (copia)", "name", 200)
        name = base_name
        attempt = 2
        while conn.execute(
            "SELECT 1 FROM radar_searches WHERE deleted_at IS NULL AND lower(name) = ? AND lower(platform) = ?",
            (name.lower(), platform.lower()),
        ).fetchone() is not None:
            name = normalize_radar_text(f"{row['name']} (copia {attempt})", "name", 200)
            attempt += 1
            if attempt > 50:
                raise ApiError(HTTPStatus.CONFLICT, "Demasiadas copias de esta búsqueda")
        conn.execute(
            """INSERT INTO radar_searches (
                 id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
                 search_query, criteria_json, sources_json, slots_json, notes, created_at, updated_at
               ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id, name, str(row["search_type"]), str(row["origin"]), str(row["priority"]), platform,
                str(row["entity_type"]), str(row["entity_id"]), str(row["search_query"]),
                str(row["criteria_json"]), str(row["sources_json"]), str(row["slots_json"] or ""),
                str(row["notes"]), now, now,
            ),
        )
    return radar_search_payload(config, new_id)


def delete_radar_search(config: AppConfig, search_id: Any) -> dict[str, Any]:
    """Logical delete: the search leaves every list but its history survives for recovery."""
    target_id = normalize_radar_id(search_id)
    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        load_radar_search(conn, target_id)
        conn.execute(
            "UPDATE radar_searches SET deleted_at = ?, status = 'archived', updated_at = ? WHERE id = ?",
            (now, now, target_id),
        )
    return {"ok": True, "id": target_id, "deletedAt": now}
