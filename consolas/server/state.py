"""Normalized user state: read, write, export, restore and media migration."""

from __future__ import annotations

import json
import uuid
from http import HTTPStatus
from typing import Any

try:
    from .clock import utc_now
    from .config import AppConfig
    from .database import connect_db
    from .errors import ApiError
    from .media_migration import migrate_runtime_media_references
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from database import connect_db
    from errors import ApiError
    from media_migration import migrate_runtime_media_references


def default_state() -> dict[str, Any]:
    return {
        "version": 3,
        "user": {
            "overridesById": {},
            "additionsById": {},
            "detailEditsById": {},
        },
        "meta": {
            "migratedLegacy": True,
            "storageBackend": "server",
            "updatedAt": utc_now(),
        },
    }


def normalize_object_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_state(value: Any) -> dict[str, Any]:
    base = default_state()
    raw = value if isinstance(value, dict) else {}
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    return {
        "version": 3,
        "user": {
            "overridesById": normalize_object_map(user.get("overridesById")),
            "additionsById": normalize_object_map(user.get("additionsById")),
            "detailEditsById": normalize_object_map(user.get("detailEditsById")),
        },
        "meta": {
            **base["meta"],
            **meta,
            "storageBackend": "server",
        },
    }


def read_state(config: AppConfig) -> dict[str, Any]:
    with connect_db(config) as conn:
        row = conn.execute("SELECT state_json FROM app_state WHERE key = ?", ("root",)).fetchone()
    if not row:
        return default_state()
    try:
        return normalize_state(json.loads(row["state_json"]))
    except json.JSONDecodeError:
        return default_state()


def write_state(config: AppConfig, payload: Any) -> dict[str, Any]:
    state = normalize_state(payload)
    state, media_migrated = migrate_runtime_media_references(config, state)
    state["meta"]["updatedAt"] = utc_now()
    if media_migrated:
        state["meta"]["mediaMigratedAt"] = utc_now()
    encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    now = utc_now()
    with connect_db(config) as conn:
        conn.execute(
            """
            INSERT INTO app_state (key, state_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              state_json = excluded.state_json,
              updated_at = excluded.updated_at
            """,
            ("root", encoded, now, now),
        )
    return state


def ensure_state_media_migrated(config: AppConfig) -> None:
    state = read_state(config)
    migrated_state, changed = migrate_runtime_media_references(config, state)
    if not changed:
        return
    migrated_state = normalize_state(migrated_state)
    migrated_state["meta"]["mediaMigratedAt"] = utc_now()
    write_state(config, migrated_state)


def build_state_export(config: AppConfig) -> dict[str, Any]:
    state = read_state(config)
    return {
        "exportedAt": utc_now(),
        "app": "consolas",
        "version": state.get("version", 3),
        "source": "server",
        "state": state,
    }


def write_legacy_import_snapshot(config: AppConfig, payload: Any) -> None:
    snapshot_id = f"import_{uuid.uuid4().hex}"
    imported_at = utc_now()
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with connect_db(config) as conn:
        conn.execute(
            """
            INSERT INTO legacy_import_snapshots (id, state_json, imported_at)
            VALUES (?, ?, ?)
            """,
            (snapshot_id, encoded, imported_at),
        )


def restore_state(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    if payload.get("confirmReplace") is not True:
        raise ApiError(HTTPStatus.BAD_REQUEST, "confirmReplace=true is required to restore server state")

    candidate = payload.get("state")
    if candidate is None:
        candidate = payload
    state = normalize_state(candidate)
    state["meta"]["restoredAt"] = utc_now()
    state["meta"]["restoreSource"] = str(payload.get("source") or "api-restore")
    write_legacy_import_snapshot(config, state)
    return write_state(config, state)
