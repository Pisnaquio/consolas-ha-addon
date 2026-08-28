#!/usr/bin/env python3
"""Consolas backend for the Home Assistant add-on.

The server uses only Python's standard library so the add-on stays small and
portable inside Home Assistant. It intentionally starts with the same
normalized state contract used by the browser DataStore, then persists it in
SQLite on the HA server.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import posixpath
import shutil
import sqlite3
import threading
import uuid
import urllib.parse
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SERVICE_NAME = "consolas-server"
SERVICE_VERSION = os.getenv("CONSOLAS_APP_VERSION", "0.1.16")
DEFAULT_DATA_DIR = "/data"
DEFAULT_STATIC_DIR = "/app/web"
DATABASE_NAME = "consolas.sqlite"
DEFAULT_PORT = 8788
DEFAULT_BODY_LIMIT = 25 * 1024 * 1024
AUCTION_WATCH_DISMISSALS_VERSION = 1
AUCTION_WATCH_FOLLOWING_VERSION = 1
AUCTION_WATCH_PUBLICATION_LIFECYCLE_VERSION = 1
AUCTION_WATCH_DISMISSAL_GRACE = timedelta(hours=48)
AUCTION_WATCH_PENDING_TIMEOUT = timedelta(minutes=10)
AUCTION_WATCH_RUNNING_TIMEOUT = timedelta(minutes=30)
AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS = 36 * 60 * 60
AUCTION_WATCH_RECEIPT_FILE = "publication-receipt.json"
AUCTION_WATCH_SNAPSHOT_HASH_HEADER = "X-Auction-Watch-Snapshot-Hash"
AUCTION_WATCH_SNAPSHOT_STATUSES = {"skipped", "published", "failed"}
AUCTION_WATCH_EMAIL_STATUSES = {"disabled", "pending", "sent", "failed", "uncertain"}
AUCTION_WATCH_OVERALL_STATUSES = {"completed", "degraded", "delivery_pending", "failed"}
AUCTION_WATCH_PUBLICATION_STATES = {"current", "superseded", "missing"}
CHASING_GAMES_VERSION = 1
CHASING_GAMES_SOURCE = "ebay-us"
CHASING_GAMES_INTERVAL_SECONDS = int(os.getenv("CHASING_GAMES_INTERVAL_SECONDS", "86400"))
EBAY_ENVIRONMENTS = {"sandbox", "production"}
_AUCTION_WATCH_DISMISSALS_LOCK = threading.RLock()
_AUCTION_WATCH_SNAPSHOT_LOCK = threading.RLock()
_AUCTION_WATCH_RUN_LOCK = threading.RLock()
_CHASING_GAMES_LOCK = threading.RLock()


class ApiError(Exception):
    def __init__(self, status: int | HTTPStatus, message: str, details: Any | None = None) -> None:
        self.status = int(status)
        self.message = message
        self.details = details
        super().__init__(message)


class AppConfig:
    def __init__(self) -> None:
        self.host = os.getenv("CONSOLAS_HOST", "0.0.0.0")
        self.port = int(os.getenv("CONSOLAS_PORT", str(DEFAULT_PORT)))
        self.data_dir = Path(os.getenv("CONSOLAS_DATA_DIR", DEFAULT_DATA_DIR))
        self.static_dir = Path(os.getenv("CONSOLAS_STATIC_DIR", DEFAULT_STATIC_DIR))
        self.media_dir = self.data_dir / "media"
        self.auction_watch_dir = self.data_dir / "auction-watch"
        self.auction_watch_stale_after_seconds = max(
            60,
            int(
                os.getenv(
                    "CONSOLAS_AUCTION_WATCH_STALE_AFTER_SECONDS",
                    str(AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS),
                )
            ),
        )
        self.db_path = self.data_dir / DATABASE_NAME
        self.max_body_size = int(os.getenv("CONSOLAS_MAX_BODY_SIZE", str(DEFAULT_BODY_LIMIT)))
        self.ebay_client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
        self.ebay_client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
        requested_ebay_environment = os.getenv("EBAY_ENVIRONMENT", "sandbox").strip().lower()
        self.ebay_environment = requested_ebay_environment if requested_ebay_environment in EBAY_ENVIRONMENTS else "sandbox"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def ensure_directories(config: AppConfig) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.media_dir.mkdir(parents=True, exist_ok=True)
    config.auction_watch_dir.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect_db(config: AppConfig) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(config: AppConfig) -> None:
    ensure_directories(config)
    with connect_db(config) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_state (
              key TEXT PRIMARY KEY,
              state_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS media (
              id TEXT PRIMARY KEY,
              original_file_name TEXT NOT NULL DEFAULT '',
              mime_type TEXT NOT NULL,
              file_name TEXT NOT NULL,
              file_path TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS legacy_import_snapshots (
              id TEXT PRIMARY KEY,
              state_json TEXT NOT NULL,
              imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auction_watch_dismissals (
              source_id TEXT NOT NULL,
              lot_id TEXT NOT NULL,
              group_id TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              lot_url TEXT NOT NULL DEFAULT '',
              image_url TEXT NOT NULL DEFAULT '',
              dismissed_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              missing_since TEXT,
              PRIMARY KEY (source_id, lot_id)
            );

            CREATE TABLE IF NOT EXISTS auction_watch_following (
              source_id TEXT NOT NULL,
              lot_id TEXT NOT NULL,
              group_id TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              lot_url TEXT NOT NULL DEFAULT '',
              followed_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (source_id, lot_id)
            );

            CREATE TABLE IF NOT EXISTS auction_watch_run_requests (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              requested_at TEXT NOT NULL,
              started_at TEXT,
              heartbeat_at TEXT,
              finished_at TEXT,
              detail TEXT NOT NULL DEFAULT '',
              run_id TEXT NOT NULL DEFAULT '',
              snapshot_hash TEXT NOT NULL DEFAULT '',
              snapshot_status TEXT NOT NULL DEFAULT '',
              email_status TEXT NOT NULL DEFAULT '',
              overall_status TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS auction_watch_publications (
              run_id TEXT PRIMARY KEY,
              snapshot_hash TEXT NOT NULL,
              generated_at TEXT NOT NULL,
              accepted_at TEXT NOT NULL,
              matches INTEGER NOT NULL DEFAULT 0,
              recorded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chasing_games (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              platform TEXT NOT NULL DEFAULT '',
              search_query TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT 'ebay-us',
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_checked_at TEXT,
              last_error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS chasing_game_results (
              id TEXT PRIMARY KEY,
              chase_id TEXT NOT NULL,
              external_id TEXT NOT NULL,
              title TEXT NOT NULL,
              price_label TEXT NOT NULL DEFAULT '',
              condition_label TEXT NOT NULL DEFAULT '',
              shipping_label TEXT NOT NULL DEFAULT '',
              location_label TEXT NOT NULL DEFAULT '',
              listing_type TEXT NOT NULL DEFAULT '',
              listing_url TEXT NOT NULL,
              image_url TEXT NOT NULL DEFAULT '',
              is_active INTEGER NOT NULL DEFAULT 1,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              UNIQUE(chase_id, external_id),
              FOREIGN KEY(chase_id) REFERENCES chasing_games(id) ON DELETE CASCADE
            );
            """
        )
        dismissal_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(auction_watch_dismissals)").fetchall()
        }
        if "missing_since" not in dismissal_columns:
            conn.execute("ALTER TABLE auction_watch_dismissals ADD COLUMN missing_since TEXT")
        if "image_url" not in dismissal_columns:
            conn.execute("ALTER TABLE auction_watch_dismissals ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
        run_request_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(auction_watch_run_requests)").fetchall()
        }
        run_request_migrations = {
            "heartbeat_at": "TEXT",
            "run_id": "TEXT NOT NULL DEFAULT ''",
            "snapshot_hash": "TEXT NOT NULL DEFAULT ''",
            "snapshot_status": "TEXT NOT NULL DEFAULT ''",
            "email_status": "TEXT NOT NULL DEFAULT ''",
            "overall_status": "TEXT NOT NULL DEFAULT ''",
        }
        for column_name, column_definition in run_request_migrations.items():
            if column_name not in run_request_columns:
                conn.execute(
                    f"ALTER TABLE auction_watch_run_requests ADD COLUMN {column_name} {column_definition}"
                )
        seed_chasing_games(conn)


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


def bootstrap_media_source_path(config: AppConfig, media_ref: str) -> Path | None:
    relative = str(media_ref or "").strip().removeprefix("./")
    if not relative.startswith("runtime/media/"):
        return None
    candidate = (config.static_dir / relative).resolve()
    root = config.static_dir.resolve()
    if root != candidate and root not in candidate.parents:
        return None
    return candidate


def import_bootstrap_media_file(config: AppConfig, source_path: Path, original_ref: str) -> str:
    relative = str(original_ref or "").strip().removeprefix("./")
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:16]
    suffix = source_path.suffix.lower() if source_path.suffix else ".bin"
    media_id = f"bootstrap_{digest}"
    file_name = f"{media_id}{suffix}"
    target_path = config.media_dir / file_name

    if not target_path.exists():
        shutil.copy2(source_path, target_path)

    created_at = utc_now()
    with connect_db(config) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO media (
              id, original_file_name, mime_type, file_name, file_path, size_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                media_id,
                source_path.name,
                mimetypes.guess_type(str(source_path))[0] or "application/octet-stream",
                file_name,
                str(target_path),
                target_path.stat().st_size,
                created_at,
            ),
        )

    return f"./media/{file_name}"


def migrate_runtime_media_references(
    config: AppConfig,
    value: Any,
    cache: dict[str, str] | None = None,
) -> tuple[Any, bool]:
    replacements = cache if cache is not None else {}

    if isinstance(value, str):
        raw = value.strip()
        if not raw.startswith("./runtime/media/") and not raw.startswith("runtime/media/"):
            return value, False
        if raw in replacements:
            return replacements[raw], replacements[raw] != value
        source_path = bootstrap_media_source_path(config, raw)
        if not source_path or not source_path.exists() or not source_path.is_file():
            return value, False
        migrated_url = import_bootstrap_media_file(config, source_path, raw)
        replacements[raw] = migrated_url
        return migrated_url, migrated_url != value

    if isinstance(value, list):
        changed = False
        next_list = []
        for item in value:
            migrated_item, item_changed = migrate_runtime_media_references(config, item, replacements)
            next_list.append(migrated_item)
            changed = changed or item_changed
        return next_list, changed

    if isinstance(value, dict):
        changed = False
        next_dict: dict[str, Any] = {}
        for key, item in value.items():
            migrated_item, item_changed = migrate_runtime_media_references(config, item, replacements)
            next_dict[key] = migrated_item
            changed = changed or item_changed
        return next_dict, changed

    return value, False


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


def read_json_body(handler: BaseHTTPRequestHandler, config: AppConfig) -> Any:
    transfer_encoding = str(handler.headers.get("Transfer-Encoding") or "").lower()
    if "chunked" in transfer_encoding:
        chunks: list[bytes] = []
        total_size = 0
        while True:
            size_line = handler.rfile.readline()
            if not size_line:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Incomplete chunked request body")
            try:
                chunk_size = int(size_line.split(b";", 1)[0].strip(), 16)
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid chunked request body") from exc
            if chunk_size == 0:
                # Consume optional trailer headers so the next request begins at a clean boundary.
                while True:
                    trailer = handler.rfile.readline()
                    if trailer in {b"", b"\r\n", b"\n"}:
                        break
                break
            total_size += chunk_size
            if total_size > config.max_body_size:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
            chunk = handler.rfile.read(chunk_size)
            if len(chunk) != chunk_size or handler.rfile.read(2) != b"\r\n":
                raise ApiError(HTTPStatus.BAD_REQUEST, "Incomplete chunked request body")
            chunks.append(chunk)
        raw = b"".join(chunks)
    else:
        raw_length = handler.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length") from exc
        if length > config.max_body_size:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid JSON body") from exc


def safe_static_path(static_dir: Path, request_path: str) -> Path:
    clean_path = posixpath.normpath(urllib.parse.unquote(request_path.split("?", 1)[0]))
    if clean_path in ("", ".", "/"):
        clean_path = "/index.html"
    candidate = (static_dir / clean_path.lstrip("/")).resolve()
    root = static_dir.resolve()
    if root != candidate and root not in candidate.parents:
        raise ApiError(HTTPStatus.FORBIDDEN, "Forbidden path")
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate


def decode_data_url(data_url: str) -> tuple[str, bytes]:
    prefix, separator, encoded = data_url.partition(",")
    if not separator or ";base64" not in prefix:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Expected base64 data URL")
    mime_type = prefix.removeprefix("data:").split(";", 1)[0] or "application/octet-stream"
    try:
        return mime_type, base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid base64 media payload") from exc


def media_extension(mime_type: str, original_file_name: str = "") -> str:
    original_suffix = Path(original_file_name).suffix.lower()
    if original_suffix and len(original_suffix) <= 8:
        return original_suffix
    return mimetypes.guess_extension(mime_type) or ".bin"


def save_media(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    data_url = str(payload.get("dataUrl") or "")
    original_file_name = str(payload.get("fileName") or payload.get("originalFileName") or "")
    mime_type, data = decode_data_url(data_url)
    media_id = f"media_{uuid.uuid4().hex}"
    file_name = f"{media_id}{media_extension(mime_type, original_file_name)}"
    file_path = config.media_dir / file_name
    file_path.write_bytes(data)
    created_at = utc_now()
    with connect_db(config) as conn:
        conn.execute(
            """
            INSERT INTO media (
              id, original_file_name, mime_type, file_name, file_path, size_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (media_id, original_file_name, mime_type, file_name, str(file_path), len(data), created_at),
        )
    return {
        "id": media_id,
        "url": f"./media/{file_name}",
        "fileName": file_name,
        "mimeType": mime_type,
        "sizeBytes": len(data),
        "createdAt": created_at,
    }


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


def normalize_public_http_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ApiError(HTTPStatus.BAD_REQUEST, "lotUrl must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ApiError(HTTPStatus.BAD_REQUEST, "lotUrl must not include credentials")
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
        image_url = normalize_public_http_url(raw_item.get("imageUrl") or raw_item.get("image_url"))[:2048]
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


def filter_auction_watch_snapshot(config: AppConfig, snapshot: dict[str, Any]) -> dict[str, Any]:
    dismissal_payload = list_auction_watch_dismissals(config)
    dismissed_keys = {
        (str(item.get("sourceId") or "").lower(), str(item.get("lotId") or ""))
        for item in dismissal_payload["items"]
    }
    if not dismissed_keys:
        return snapshot

    filtered = dict(snapshot)
    raw_matches = snapshot.get("matches") if isinstance(snapshot.get("matches"), list) else []
    visible_matches = [
        item
        for item in raw_matches
        if not isinstance(item, dict)
        or (str(item.get("source") or "").lower(), str(item.get("lotId") or "")) not in dismissed_keys
    ]
    removed_keys = {
        (str(item.get("source") or "").lower(), str(item.get("lotId") or ""))
        for item in raw_matches
        if isinstance(item, dict)
        and (str(item.get("source") or "").lower(), str(item.get("lotId") or "")) in dismissed_keys
    }
    filtered["matches"] = visible_matches

    featured = snapshot.get("featured")
    if isinstance(featured, dict):
        featured_key = (str(featured.get("source") or "").lower(), str(featured.get("lotId") or ""))
        if featured_key in dismissed_keys:
            filtered["featured"] = None
            removed_keys.add(featured_key)

    filtered["dismissalsApplied"] = len(removed_keys)
    raw_counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    counts = dict(raw_counts)
    source_counts: dict[str, int] = {}
    for item in visible_matches:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source") or "").strip().lower()
        if source_id:
            source_counts[source_id] = source_counts.get(source_id, 0) + 1
    detected_matches = int(counts.get("detected_matches") or counts.get("total_matches") or len(raw_matches))
    new_matches = int(
        counts.get("new_matches")
        or sum(
            isinstance(item, dict) and item.get("firstSeenInRun") is True
            for item in raw_matches
        )
    )
    counts.update(
        {
            "detected_matches": detected_matches,
            "dismissed_matches": max(0, detected_matches - len(visible_matches)),
            "total_matches": len(visible_matches),
            "visible_matches": len(visible_matches),
            "new_matches": new_matches,
            "bavastro_matches": source_counts.get("bavastro", 0),
            "castells_matches": source_counts.get("castells", 0),
            "extra_matches": sum(
                match_count
                for source_id, match_count in source_counts.items()
                if source_id not in {"bavastro", "castells"}
            ),
            "extra_matches_by_source": {
                source_id: match_count
                for source_id, match_count in source_counts.items()
                if source_id not in {"bavastro", "castells"}
            },
        }
    )
    filtered["counts"] = counts
    return filtered


def canonical_auction_watch_snapshot_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_auction_watch_timestamp(value: Any, field: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"Auction Watch {field} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"Auction Watch {field} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"Auction Watch {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def normalize_auction_watch_run_id(value: Any) -> str:
    run_id = str(value or "").strip()
    if not run_id or len(run_id) > 160 or any(ord(char) < 32 for char in run_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch runId is invalid")
    return run_id


def normalize_auction_watch_snapshot_hash(value: Any, *, required: bool = True) -> str:
    snapshot_hash = str(value or "").strip().lower()
    if not snapshot_hash and not required:
        return ""
    if len(snapshot_hash) != 64 or any(char not in "0123456789abcdef" for char in snapshot_hash):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch snapshot hash is invalid")
    return snapshot_hash


def read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json_object_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def auction_watch_receipt_path(config: AppConfig) -> Path:
    return config.auction_watch_dir / "export" / AUCTION_WATCH_RECEIPT_FILE


def record_auction_watch_publication_receipt(config: AppConfig, receipt: dict[str, Any]) -> None:
    run_id = normalize_auction_watch_run_id(receipt.get("runId"))
    snapshot_hash = normalize_auction_watch_snapshot_hash(receipt.get("snapshotHash"))
    generated_at = str(receipt.get("generatedAt") or "").strip()
    accepted_at = str(receipt.get("acceptedAt") or "").strip()
    if not generated_at or not accepted_at:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch receipt is incomplete")
    matches = max(0, int(receipt.get("matches") or 0))
    with connect_db(config) as conn:
        existing = conn.execute(
            "SELECT snapshot_hash FROM auction_watch_publications WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing is not None and str(existing["snapshot_hash"]).lower() != snapshot_hash:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch runId was already published with different content",
                {"runId": run_id, "currentSnapshotHash": existing["snapshot_hash"]},
            )
        conn.execute(
            """
            INSERT INTO auction_watch_publications
              (run_id, snapshot_hash, generated_at, accepted_at, matches, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              snapshot_hash = excluded.snapshot_hash,
              generated_at = excluded.generated_at,
              accepted_at = excluded.accepted_at,
              matches = excluded.matches,
              recorded_at = excluded.recorded_at
            """,
            (run_id, snapshot_hash, generated_at, accepted_at, matches, utc_now()),
        )


def auction_watch_publication_history(config: AppConfig, run_id: str) -> dict[str, Any] | None:
    with connect_db(config) as conn:
        row = conn.execute(
            """
            SELECT run_id, snapshot_hash, generated_at, accepted_at, matches, recorded_at
            FROM auction_watch_publications WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def auction_watch_publication_history_hash(config: AppConfig, run_id: str) -> str:
    history = auction_watch_publication_history(config, run_id)
    return str(history.get("snapshot_hash") or "").strip().lower() if history else ""


def current_auction_watch_publication(config: AppConfig) -> dict[str, Any] | None:
    """Return the verified server snapshot identity currently visible to clients."""
    with _AUCTION_WATCH_SNAPSHOT_LOCK:
        payload = read_json_object(config.auction_watch_dir / "export" / "auction-watch.json")
        receipt = read_json_object(auction_watch_receipt_path(config))
    if not isinstance(payload, dict) or not isinstance(receipt, dict):
        return None

    run_id = str(payload.get("runId") or "").strip()
    generated_at = str(payload.get("generatedAt") or "").strip()
    snapshot_hash = canonical_auction_watch_snapshot_hash(payload)
    receipt_valid = (
        bool(run_id)
        and bool(generated_at)
        and str(receipt.get("runId") or "").strip() == run_id
        and str(receipt.get("snapshotHash") or "").strip().lower() == snapshot_hash
        and str(receipt.get("generatedAt") or "").strip() == generated_at
        and bool(str(receipt.get("acceptedAt") or "").strip())
    )
    if not receipt_valid:
        return None
    try:
        generated_datetime = parse_auction_watch_timestamp(generated_at, "generatedAt")
        accepted_at = str(receipt.get("acceptedAt") or "").strip()
        accepted_datetime = parse_auction_watch_timestamp(accepted_at, "acceptedAt")
    except ApiError:
        return None
    return {
        "runId": run_id,
        "snapshotHash": snapshot_hash,
        "generatedAt": generated_at,
        "generatedDatetime": generated_datetime,
        "acceptedAt": accepted_at,
        "acceptedDatetime": accepted_datetime,
    }


def auction_watch_publication_state(
    config: AppConfig,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Classify a terminal published request against current and historical receipts."""
    snapshot_status = str(request.get("snapshotStatus") or "").strip().lower()
    if snapshot_status != "published":
        return {}

    run_id = str(request.get("runId") or "").strip()
    snapshot_hash = str(request.get("snapshotHash") or "").strip().lower()
    current = current_auction_watch_publication(config)
    if current and current["runId"] == run_id and current["snapshotHash"] == snapshot_hash:
        return {"publicationState": "current"}

    history = auction_watch_publication_history(config, run_id) if run_id else None
    historical_valid = False
    history_accepted: datetime | None = None
    if history and str(history.get("snapshot_hash") or "").strip().lower() == snapshot_hash:
        try:
            history_accepted = parse_auction_watch_timestamp(history.get("accepted_at"), "acceptedAt")
            historical_valid = True
        except ApiError:
            historical_valid = False

    if historical_valid and current:
        is_posterior = current["acceptedDatetime"] > history_accepted
        if is_posterior:
            return {
                "publicationState": "superseded",
                "supersededByRunId": current["runId"],
            }
    return {"publicationState": "missing"}


def auction_watch_snapshot_sync(
    config: AppConfig,
    payload: dict[str, Any],
    *,
    source: str,
    receipt: dict[str, Any] | None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    run_id = str(payload.get("runId") or "").strip()
    generated_at = str(payload.get("generatedAt") or "").strip() or None
    snapshot_hash = canonical_auction_watch_snapshot_hash(payload)
    accepted_at = None
    receipt_valid = False
    receipt_present = receipt is not None
    if isinstance(receipt, dict):
        receipt_valid = (
            str(receipt.get("runId") or "").strip() == run_id
            and str(receipt.get("snapshotHash") or "").strip().lower() == snapshot_hash
            and str(receipt.get("generatedAt") or "").strip() == str(generated_at or "")
            and bool(str(receipt.get("acceptedAt") or "").strip())
        )
        if receipt_valid:
            accepted_at = str(receipt.get("acceptedAt") or "").strip() or None

    age_seconds: int | None = None
    generated_datetime: datetime | None = None
    if generated_at:
        try:
            generated_datetime = parse_auction_watch_timestamp(generated_at, "generatedAt")
        except ApiError:
            generated_datetime = None
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    future_timestamp = False
    if generated_datetime is not None:
        future_timestamp = generated_datetime > now + timedelta(minutes=5)
        age_seconds = max(0, int((now - generated_datetime).total_seconds()))

    payload_status = str(payload.get("status") or "").strip().lower()
    if payload_status == "unavailable" or not run_id or generated_datetime is None or future_timestamp:
        sync_status = "unavailable"
    elif source == "server" and receipt_present and not receipt_valid:
        sync_status = "unavailable"
    elif source != "server" or not receipt_valid:
        sync_status = "stale"
    elif age_seconds is None or age_seconds > getattr(
        config,
        "auction_watch_stale_after_seconds",
        AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS,
    ):
        sync_status = "stale"
    else:
        sync_status = "current"

    return {
        "runId": run_id,
        "snapshotHash": snapshot_hash,
        "generatedAt": generated_at,
        "acceptedAt": accepted_at,
        "source": source,
        "ageSeconds": age_seconds,
        "status": sync_status,
    }


def read_auction_watch_snapshot(config: AppConfig) -> dict[str, Any]:
    export_path = config.auction_watch_dir / "export" / "auction-watch.json"
    candidates = [
        (export_path, "server"),
        (config.auction_watch_dir / "latest" / "auction-watch.json", "server-legacy"),
        (config.static_dir / "data" / "auction-watch.json", "static"),
    ]
    receipt_path = auction_watch_receipt_path(config)
    receipt = read_json_object(receipt_path)
    if receipt is None and receipt_path.exists():
        receipt = {}
    for path, source in candidates:
        payload = read_json_object(path)
        if payload is None:
            continue
        candidate_receipt = receipt if path == export_path else None
        sync = auction_watch_snapshot_sync(
            config,
            payload,
            source=source,
            receipt=candidate_receipt,
        )
        filtered = filter_auction_watch_snapshot(config, payload)
        filtered["snapshotHash"] = sync["snapshotHash"]
        filtered["sync"] = sync
        return filtered
    return {
        "generatedAt": None,
        "runId": "",
        "snapshotHash": "",
        "matches": [],
        "status": "unavailable",
        "sync": {
            "runId": "",
            "snapshotHash": "",
            "generatedAt": None,
            "acceptedAt": None,
            "source": "none",
            "ageSeconds": None,
            "status": "unavailable",
        },
    }


def _publish_auction_watch_snapshot_unlocked(
    config: AppConfig,
    payload: Any,
    *,
    expected_hash: str | None = None,
) -> dict[str, Any]:
    """Persist the runner's complete public snapshot before an alert is sent.

    This lives in /data instead of the add-on's static bundle, so the scheduled
    runner can refresh opportunities without rebuilding the add-on on every run.
    """
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch snapshot must be an object")
    run_id = normalize_auction_watch_run_id(payload.get("runId"))
    generated_at = str(payload.get("generatedAt") or "").strip()
    generated_datetime = parse_auction_watch_timestamp(generated_at, "generatedAt")
    if generated_datetime > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch generatedAt is too far in the future")
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch snapshot matches must be a list")
    if len(matches) > 10_000:
        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Auction Watch snapshot has too many matches")

    for item in matches:
        if not isinstance(item, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch snapshot match must be an object")
        normalize_auction_watch_identity(item.get("source"), item.get("lotId"))
    lifecycle = normalize_auction_watch_lifecycle(payload)
    active_image_urls = normalize_active_match_metadata(payload)

    snapshot_hash = canonical_auction_watch_snapshot_hash(payload)
    if expected_hash is not None:
        normalized_expected_hash = normalize_auction_watch_snapshot_hash(expected_hash)
        if normalized_expected_hash != snapshot_hash:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch snapshot hash does not match the request body",
                {"runId": run_id, "computedSnapshotHash": snapshot_hash},
            )
    historical_hash = auction_watch_publication_history_hash(config, run_id)
    if historical_hash and historical_hash != snapshot_hash:
        raise ApiError(
            HTTPStatus.CONFLICT,
            "Auction Watch runId was already published with different content",
            {"runId": run_id, "currentSnapshotHash": historical_hash},
        )

    export_dir = config.auction_watch_dir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / "auction-watch.json"
    current_payload = read_json_object(target)
    current_is_export = current_payload is not None
    if current_payload is None:
        current_payload = read_json_object(config.auction_watch_dir / "latest" / "auction-watch.json")
    current_receipt = read_json_object(auction_watch_receipt_path(config)) if current_is_export else None
    if current_payload is not None:
        current_run_id = str(current_payload.get("runId") or "").strip()
        current_hash = canonical_auction_watch_snapshot_hash(current_payload)
        current_generated_at = str(current_payload.get("generatedAt") or "").strip()
        current_generated_datetime: datetime | None = None
        if current_generated_at:
            try:
                current_generated_datetime = parse_auction_watch_timestamp(
                    current_generated_at,
                    "generatedAt",
                )
            except ApiError:
                current_generated_datetime = None
        if (
            current_generated_datetime is not None
            and current_generated_datetime > datetime.now(timezone.utc) + timedelta(minutes=5)
        ):
            current_generated_datetime = None

        current_receipt_valid = (
            isinstance(current_receipt, dict)
            and str(current_receipt.get("runId") or "").strip() == current_run_id
            and str(current_receipt.get("snapshotHash") or "").strip().lower() == current_hash
            and str(current_receipt.get("generatedAt") or "").strip() == current_generated_at
            and bool(str(current_receipt.get("acceptedAt") or "").strip())
        )

        if current_run_id == run_id and current_hash == snapshot_hash and current_receipt_valid:
            receipt = {
                "runId": run_id,
                "snapshotHash": snapshot_hash,
                "generatedAt": generated_at,
                "acceptedAt": current_receipt.get("acceptedAt"),
                "matches": len(matches),
            }
            record_auction_watch_publication_receipt(config, receipt)
            # Publication is only fully committed once lifecycle reconciliation
            # has also succeeded. Re-running it on an identical publish closes
            # the crash window after the snapshot/receipt renames.
            cleanup = reconcile_auction_watch_dismissals(
                config,
                lifecycle,
                active_image_urls=active_image_urls,
            )
            return {
                "ok": True,
                "receipt": receipt,
                "generatedAt": generated_at,
                "matches": len(matches),
                "idempotent": True,
                "dismissalCleanup": cleanup,
            }
        if current_run_id == run_id and current_hash != snapshot_hash:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch runId was already published with different content",
                {"runId": run_id, "currentSnapshotHash": current_hash},
            )
        if current_generated_datetime is not None and generated_datetime < current_generated_datetime:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch refused an older snapshot",
                {"runId": run_id, "currentRunId": current_run_id, "currentGeneratedAt": current_generated_at},
            )
        if (
            current_generated_datetime is not None
            and generated_datetime == current_generated_datetime
            and current_hash != snapshot_hash
        ):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch refused conflicting snapshots with the same generatedAt",
                {"runId": run_id, "currentRunId": current_run_id, "generatedAt": generated_at},
            )

    accepted_at = utc_now()
    receipt = {
        "runId": run_id,
        "snapshotHash": snapshot_hash,
        "generatedAt": generated_at,
        "acceptedAt": accepted_at,
        "matches": len(matches),
    }
    # The durable receipt history is the admission commit for a run. It is
    # recorded before the derived files so a later publication cannot erase the
    # identity needed by a delayed completion after a crash between renames.
    record_auction_watch_publication_receipt(config, receipt)
    # A mismatched sidecar makes GET unavailable. Writing it before the snapshot
    # therefore fails closed if the process stops between renames.
    write_json_object_atomic(auction_watch_receipt_path(config), receipt)
    write_json_object_atomic(target, payload)

    cleanup = reconcile_auction_watch_dismissals(config, lifecycle, active_image_urls=active_image_urls)

    return {
        "ok": True,
        "receipt": receipt,
        "generatedAt": generated_at,
        "matches": len(matches),
        "dismissalCleanup": cleanup,
    }


def publish_auction_watch_snapshot(
    config: AppConfig,
    payload: Any,
    *,
    expected_hash: str | None = None,
) -> dict[str, Any]:
    with _AUCTION_WATCH_SNAPSHOT_LOCK:
        return _publish_auction_watch_snapshot_unlocked(
            config,
            payload,
            expected_hash=expected_hash,
        )


def auction_watch_run_request_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "requestedAt": row["requested_at"],
        "startedAt": row["started_at"],
        "heartbeatAt": row["heartbeat_at"],
        "finishedAt": row["finished_at"],
        "detail": row["detail"],
        "runId": row["run_id"],
        "snapshotHash": row["snapshot_hash"],
        "snapshotStatus": row["snapshot_status"],
        "emailStatus": row["email_status"],
        "overallStatus": row["overall_status"],
    }


def reconcile_stale_auction_watch_run_requests(
    conn: sqlite3.Connection,
    *,
    observed_at: datetime | None = None,
) -> dict[str, int]:
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    finished_at = now.isoformat().replace("+00:00", "Z")
    pending_before = (now - AUCTION_WATCH_PENDING_TIMEOUT).isoformat().replace("+00:00", "Z")
    running_before = (now - AUCTION_WATCH_RUNNING_TIMEOUT).isoformat().replace("+00:00", "Z")
    pending = conn.execute(
        """
        UPDATE auction_watch_run_requests
        SET status = 'failed', finished_at = ?,
            detail = 'La solicitud venció porque el buscador no estaba disponible.',
            snapshot_status = CASE WHEN snapshot_status = '' THEN 'failed' ELSE snapshot_status END,
            email_status = CASE WHEN email_status = '' THEN 'failed' ELSE email_status END,
            overall_status = 'failed'
        WHERE status = 'pending' AND requested_at < ?
        """,
        (finished_at, pending_before),
    ).rowcount
    running = conn.execute(
        """
        UPDATE auction_watch_run_requests
        SET status = 'failed', finished_at = ?, detail = 'La corrida anterior quedó interrumpida.',
            snapshot_status = CASE WHEN snapshot_status = '' THEN 'failed' ELSE snapshot_status END,
            email_status = CASE WHEN email_status = '' THEN 'uncertain' ELSE email_status END,
            overall_status = 'failed'
        WHERE status = 'running' AND COALESCE(heartbeat_at, started_at) IS NOT NULL
          AND COALESCE(heartbeat_at, started_at) < ?
        """,
        (finished_at, running_before),
    ).rowcount
    return {"pending": pending, "running": running}


def latest_auction_watch_run_request(config: AppConfig) -> dict[str, Any]:
    # Reconcile requests even when the scheduler disappears before claiming
    # them so the web UI cannot remain stuck on a disabled button forever.
    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        reconcile_stale_auction_watch_run_requests(conn)
        row = conn.execute(
            "SELECT * FROM auction_watch_run_requests ORDER BY requested_at DESC LIMIT 1"
        ).fetchone()
    request = auction_watch_run_request_row(row)
    if request is not None:
        request.update(auction_watch_publication_state(config, request))
    return {"ok": True, "request": request}


def enqueue_auction_watch_run(config: AppConfig) -> dict[str, Any]:
    now = utc_now()
    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        reconcile_stale_auction_watch_run_requests(conn)
        existing = conn.execute(
            """
            SELECT * FROM auction_watch_run_requests
            WHERE status IN ('pending', 'running', 'delivery_pending')
            ORDER BY requested_at LIMIT 1
            """
        ).fetchone()
        if existing is not None:
            return {"ok": True, "queued": False, "request": auction_watch_run_request_row(existing)}
        request_id = f"run_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO auction_watch_run_requests (id, status, requested_at) VALUES (?, 'pending', ?)",
            (request_id, now),
        )
        row = conn.execute("SELECT * FROM auction_watch_run_requests WHERE id = ?", (request_id,)).fetchone()
    return {"ok": True, "queued": True, "request": auction_watch_run_request_row(row)}


def claim_auction_watch_run(config: AppConfig) -> dict[str, Any]:
    now = utc_now()
    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        reconcile_stale_auction_watch_run_requests(conn)
        running = conn.execute(
            "SELECT * FROM auction_watch_run_requests WHERE status = 'running' ORDER BY requested_at LIMIT 1"
        ).fetchone()
        if running is not None:
            # The scheduler must not fall through to an automatic scan while a
            # manually requested run owns the lease but has not yet produced an
            # outbox record (for example, after a run-lock collision/crash).
            return {
                "ok": True,
                "request": None,
                "running": auction_watch_run_request_row(running),
            }
        pending = conn.execute(
            "SELECT * FROM auction_watch_run_requests WHERE status = 'pending' ORDER BY requested_at LIMIT 1"
        ).fetchone()
        if pending is None:
            return {"ok": True, "request": None}
        updated = conn.execute(
            """
            UPDATE auction_watch_run_requests
            SET status = 'running', started_at = ?, heartbeat_at = ?, detail = '',
                run_id = '', snapshot_hash = '', snapshot_status = '',
                email_status = '', overall_status = ''
            WHERE id = ? AND status = 'pending'
            """,
            (now, now, pending["id"]),
        ).rowcount
        if updated != 1:
            return {"ok": True, "request": None}
        row = conn.execute("SELECT * FROM auction_watch_run_requests WHERE id = ?", (pending["id"],)).fetchone()
    return {"ok": True, "request": auction_watch_run_request_row(row)}


def heartbeat_auction_watch_run(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Run heartbeat must be an object")
    request_id = str(payload.get("id") or "").strip()
    if not request_id.startswith("run_") or len(request_id) > 80:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Valid run request id is required")
    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute(
            """
            UPDATE auction_watch_run_requests
            SET heartbeat_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (utc_now(), request_id),
        ).rowcount
        row = conn.execute(
            "SELECT * FROM auction_watch_run_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
    if row is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "Run request not found")
    if updated != 1:
        raise ApiError(
            HTTPStatus.CONFLICT,
            "Run request is no longer running",
            {"id": request_id, "status": row["status"]},
        )
    return {"ok": True, "request": auction_watch_run_request_row(row)}


def auction_watch_publication_matches_receipt(
    config: AppConfig,
    run_id: str,
    snapshot_hash: str,
) -> bool:
    historical_hash = auction_watch_publication_history_hash(config, run_id)
    if historical_hash:
        return historical_hash == snapshot_hash
    with _AUCTION_WATCH_SNAPSHOT_LOCK:
        published_payload = read_json_object(
            config.auction_watch_dir / "export" / "auction-watch.json"
        )
        published_receipt = read_json_object(auction_watch_receipt_path(config))
        published_generated_at = (
            str(published_payload.get("generatedAt") or "").strip()
            if isinstance(published_payload, dict)
            else ""
        )
        matches = (
            isinstance(published_payload, dict)
            and isinstance(published_receipt, dict)
            and str(published_payload.get("runId") or "").strip() == run_id
            and canonical_auction_watch_snapshot_hash(published_payload) == snapshot_hash
            and str(published_receipt.get("runId") or "").strip() == run_id
            and str(published_receipt.get("snapshotHash") or "").strip().lower() == snapshot_hash
            and str(published_receipt.get("generatedAt") or "").strip() == published_generated_at
            and bool(str(published_receipt.get("acceptedAt") or "").strip())
        )
        if matches:
            record_auction_watch_publication_receipt(config, published_receipt)
        return matches


def complete_auction_watch_run(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Run completion must be an object")
    request_id = str(payload.get("id") or "").strip()
    if not request_id.startswith("run_") or len(request_id) > 80:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Valid run request id is required")
    success = payload.get("success") is True
    detail = str(payload.get("detail") or "").strip()[:1000]
    run_id_raw = str(payload.get("runId") or "").strip()
    run_id = normalize_auction_watch_run_id(run_id_raw) if run_id_raw else ""
    snapshot_hash = normalize_auction_watch_snapshot_hash(
        payload.get("snapshotHash"),
        required=False,
    )
    snapshot_status = str(payload.get("snapshotStatus") or "").strip().lower()
    email_status = str(payload.get("emailStatus") or "").strip().lower()
    overall_status = str(payload.get("overallStatus") or ("completed" if success else "failed")).strip().lower()
    if snapshot_status and snapshot_status not in AUCTION_WATCH_SNAPSHOT_STATUSES:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Auction Watch snapshotStatus")
    if email_status and email_status not in AUCTION_WATCH_EMAIL_STATUSES:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Auction Watch emailStatus")
    if overall_status not in AUCTION_WATCH_OVERALL_STATUSES:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Auction Watch overallStatus")
    if snapshot_status == "published" and (not run_id or not snapshot_hash):
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "Published Auction Watch runs require runId and snapshotHash",
        )
    status = (
        "delivery_pending"
        if overall_status == "delivery_pending"
        else ("completed" if success else "failed")
    )

    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        before = conn.execute(
            "SELECT * FROM auction_watch_run_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if before is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Run request not found")
        # Validate the publication only for the first transition out of
        # `running`. Later idempotent callbacks or delivery completion retries
        # rely on the identity already stored in this row, even if a newer
        # scheduled snapshot has since become current.
        publication_needs_validation = (
            snapshot_status == "published"
            and (
                str(before["status"]) == "running"
                or str(before["snapshot_status"]) != "published"
                or str(before["run_id"]) != run_id
                or str(before["snapshot_hash"]) != snapshot_hash
            )
        )
        if publication_needs_validation and not auction_watch_publication_matches_receipt(
            config,
            run_id,
            snapshot_hash,
        ):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Published Auction Watch run does not match the server receipt",
                {"runId": run_id, "snapshotHash": snapshot_hash},
            )
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE auction_watch_run_requests
            SET status = ?, finished_at = ?, detail = ?, run_id = ?,
                snapshot_hash = ?, snapshot_status = ?, email_status = ?, overall_status = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                status,
                utc_now(),
                detail,
                run_id,
                snapshot_hash,
                snapshot_status,
                email_status,
                overall_status,
                request_id,
            ),
        )
        if cursor.rowcount != 1:
            existing = conn.execute(
                "SELECT * FROM auction_watch_run_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if existing is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "Run request not found")
            delivery_progress = (
                str(existing["overall_status"]) == "delivery_pending"
                and overall_status == "delivery_pending"
                and str(existing["run_id"]) == run_id
                and str(existing["snapshot_hash"] or "") in {"", snapshot_hash}
            )
            if delivery_progress:
                progressed = conn.execute(
                    """
                    UPDATE auction_watch_run_requests
                    SET status = 'delivery_pending', finished_at = ?, detail = ?,
                        snapshot_hash = ?, snapshot_status = ?, email_status = ?,
                        overall_status = 'delivery_pending'
                    WHERE id = ? AND overall_status = 'delivery_pending' AND run_id = ?
                    """,
                    (
                        utc_now(),
                        detail,
                        snapshot_hash,
                        snapshot_status,
                        email_status,
                        request_id,
                        run_id,
                    ),
                )
                if progressed.rowcount == 1:
                    progressed_row = conn.execute(
                        "SELECT * FROM auction_watch_run_requests WHERE id = ?",
                        (request_id,),
                    ).fetchone()
                    return {
                        "ok": True,
                        "request": auction_watch_run_request_row(progressed_row),
                        "deliveryProgress": True,
                    }
            delivery_transition = (
                str(existing["overall_status"]) == "delivery_pending"
                and overall_status in {"completed", "degraded", "failed"}
                and str(existing["run_id"]) == run_id
                and str(existing["snapshot_hash"] or "") in {"", snapshot_hash}
            )
            if delivery_transition:
                transitioned = conn.execute(
                    """
                    UPDATE auction_watch_run_requests
                    SET status = ?, finished_at = ?, detail = ?, snapshot_hash = ?,
                        snapshot_status = ?, email_status = ?, overall_status = ?
                    WHERE id = ? AND overall_status = 'delivery_pending' AND run_id = ?
                    """,
                    (
                        status,
                        utc_now(),
                        detail,
                        snapshot_hash,
                        snapshot_status,
                        email_status,
                        overall_status,
                        request_id,
                        run_id,
                    ),
                )
                if transitioned.rowcount == 1:
                    transitioned_row = conn.execute(
                        "SELECT * FROM auction_watch_run_requests WHERE id = ?",
                        (request_id,),
                    ).fetchone()
                    return {
                        "ok": True,
                        "request": auction_watch_run_request_row(transitioned_row),
                        "deliveryTransition": True,
                    }
            same_result = (
                str(existing["status"]) == status
                and str(existing["detail"]) == detail
                and str(existing["run_id"]) == run_id
                and str(existing["snapshot_hash"]) == snapshot_hash
                and str(existing["snapshot_status"]) == snapshot_status
                and str(existing["email_status"]) == email_status
                and str(existing["overall_status"]) == overall_status
            )
            if same_result and str(existing["status"]) in {"completed", "failed", "delivery_pending"}:
                return {
                    "ok": True,
                    "request": auction_watch_run_request_row(existing),
                    "idempotent": True,
                }
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Run request is no longer running or completion conflicts with its stored result",
                {"id": request_id, "status": existing["status"]},
            )
        row = conn.execute("SELECT * FROM auction_watch_run_requests WHERE id = ?", (request_id,)).fetchone()
    return {"ok": True, "request": auction_watch_run_request_row(row)}


def seed_chasing_games(conn: sqlite3.Connection) -> None:
    """Create the first explicit chase once, without touching collection state."""
    if conn.execute("SELECT 1 FROM chasing_games LIMIT 1").fetchone():
        return
    now = utc_now()
    conn.execute(
        """
        INSERT INTO chasing_games (id, title, platform, search_query, source, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            "iss-deluxe-snes",
            "International Superstar Soccer Deluxe",
            "SNES",
            "International Superstar Soccer Deluxe SNES",
            CHASING_GAMES_SOURCE,
            now,
            now,
        ),
    )


def normalize_chase_id(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate or len(candidate) > 100 or not all(char.isalnum() or char in {"-", "_"} for char in candidate):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid chase id")
    return candidate


def normalize_chase_text(value: Any, field: str, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} is required")
    if len(text) > limit:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} is too long")
    return text


def require_chasing_games_write_request(handler: BaseHTTPRequestHandler) -> None:
    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type application/json is required")
    if handler.headers.get("X-Consolas-Chasing-Games") != "1":
        raise ApiError(HTTPStatus.FORBIDDEN, "Chasing Games action header is required")


class EbaySearchParser(HTMLParser):
    """Small, dependency-free parser for eBay's server-rendered search cards."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.field = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "")
        if tag == "li" and "s-item" in classes.split():
            self.current = {}
            self.field = ""
            return
        if self.current is None:
            return
        if tag == "a" and "s-item__link" in classes:
            self.current["url"] = values.get("href", "")
        elif tag == "img" and "s-item__image-img" in classes:
            self.current["image"] = values.get("src") or values.get("data-src") or ""
        elif "s-item__title" in classes:
            self.field = "title"
        elif "s-item__price" in classes:
            self.field = "price"
        elif "s-item__shipping" in classes:
            self.field = "shipping"
        elif "s-item__location" in classes:
            self.field = "location"
        elif "s-item__subtitle" in classes:
            self.field = "condition"

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.field:
            self.current[self.field] = f"{self.current.get(self.field, '')} {data}".strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self.current is not None:
            if self.current.get("title") and self.current.get("url"):
                self.items.append(self.current)
            self.current = None
            self.field = ""
        elif tag in {"a", "span", "div", "h3"}:
            self.field = ""


def clean_ebay_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def ebay_external_id(url: str, title: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    for part in reversed(parts):
        if part.isdigit() and len(part) >= 8:
            return part
    return hashlib.sha1(f"{url}\0{title}".encode("utf-8")).hexdigest()[:24]


def fetch_ebay_listings(config: AppConfig, search_query: str, limit: int = 12) -> list[dict[str, str]]:
    if not config.ebay_client_id or not config.ebay_client_secret:
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "Configurá las credenciales de eBay Developers en el add-on")
    credentials = base64.b64encode(f"{config.ebay_client_id}:{config.ebay_client_secret}".encode("utf-8")).decode("ascii")
    ebay_api_host = "api.sandbox.ebay.com" if config.ebay_environment == "sandbox" else "api.ebay.com"
    token_request = urllib.request.Request(
        f"https://{ebay_api_host}/identity/v1/oauth2/token",
        data=b"grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope",
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_request, timeout=20) as response:
            access_token = str(json.loads(response.read().decode("utf-8")).get("access_token") or "")
    except urllib.error.HTTPError as exc:
        error_code = ""
        try:
            error_code = str(json.loads(exc.read().decode("utf-8")).get("error") or "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        detail = f": {error_code}" if error_code else ""
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"eBay rechazó las credenciales (HTTP {exc.code}){detail}") from exc
    except Exception as exc:
        raise ApiError(HTTPStatus.BAD_GATEWAY, "No se pudo obtener el token de eBay") from exc
    if not access_token:
        raise ApiError(HTTPStatus.BAD_GATEWAY, "eBay no devolvió un token de aplicación")

    query = urllib.parse.urlencode({"q": search_query, "limit": str(limit)})
    search_request = urllib.request.Request(
        f"https://{ebay_api_host}/buy/browse/v1/item_summary/search?{query}",
        headers={"Authorization": f"Bearer {access_token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
    )
    try:
        with urllib.request.urlopen(search_request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"La búsqueda de eBay falló (HTTP {exc.code})") from exc
    except Exception as exc:
        raise ApiError(HTTPStatus.BAD_GATEWAY, "eBay no pudo completar la búsqueda") from exc
    listings: list[dict[str, str]] = []
    for raw in payload.get("itemSummaries") or []:
        if not isinstance(raw, dict):
            continue
        price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
        shipping = (raw.get("shippingOptions") or [{}])[0] if isinstance(raw.get("shippingOptions"), list) else {}
        location = raw.get("itemLocation") if isinstance(raw.get("itemLocation"), dict) else {}
        image = raw.get("image") if isinstance(raw.get("image"), dict) else {}
        listings.append({
            "externalId": str(raw.get("itemId") or ""),
            "title": clean_ebay_text(raw.get("title", "")),
            "priceLabel": clean_ebay_text(f"{price.get('currency', '')} {price.get('value', '')}"),
            "conditionLabel": clean_ebay_text(raw.get("condition", "")),
            "shippingLabel": clean_ebay_text(shipping.get("shippingCostType", "")),
            "locationLabel": clean_ebay_text(location.get("country", "")),
            "listingType": "Subasta" if "AUCTION" in (raw.get("buyingOptions") or []) else "Compra directa",
            "listingUrl": normalize_public_http_url(raw.get("itemWebUrl", "")),
            "imageUrl": normalize_public_http_url(image.get("imageUrl", "")) if image.get("imageUrl") else "",
        })
    return [item for item in listings if item["externalId"] and item["title"] and item["listingUrl"]]


def fetch_ebay_listings_legacy(search_query: str, limit: int = 12) -> list[dict[str, str]]:
    query = urllib.parse.urlencode({"_nkw": search_query, "_sacat": "0", "LH_BIN": "1", "rt": "nc"})
    request = urllib.request.Request(
        f"https://www.ebay.com/sch/i.html?{query}",
        headers={"User-Agent": "Mozilla/5.0 (compatible; Consolas-Chasing-Games/1.0)", "Accept-Language": "en-US,en;q=0.8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(2_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"eBay respondió HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = str(exc.reason or "error de conexión").replace("\n", " ")[:120]
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"eBay no respondió: {reason}") from exc
    except Exception as exc:
        raise ApiError(HTTPStatus.BAD_GATEWAY, "eBay no pudo completar la consulta") from exc

    parser = EbaySearchParser()
    parser.feed(body)
    listings: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in parser.items:
        title = clean_ebay_text(raw.get("title", ""))
        url = normalize_public_http_url(raw.get("url", ""))
        if not title or title.lower() == "shop on ebay" or not url:
            continue
        external_id = ebay_external_id(url, title)
        if external_id in seen:
            continue
        seen.add(external_id)
        listings.append(
            {
                "externalId": external_id,
                "title": title,
                "priceLabel": clean_ebay_text(raw.get("price", "")),
                "conditionLabel": clean_ebay_text(raw.get("condition", "")),
                "shippingLabel": clean_ebay_text(raw.get("shipping", "")),
                "locationLabel": clean_ebay_text(raw.get("location", "")),
                "listingType": "Compra directa",
                "listingUrl": url,
                "imageUrl": normalize_public_http_url(raw.get("image", "")) if raw.get("image") else "",
            }
        )
        if len(listings) >= limit:
            break
    return listings


def chasing_game_row(row: sqlite3.Row, results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "platform": row["platform"],
        "searchQuery": row["search_query"],
        "source": row["source"],
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "lastCheckedAt": row["last_checked_at"],
        "lastError": row["last_error"],
        "results": results or [],
    }


def list_chasing_games(config: AppConfig) -> dict[str, Any]:
    with _CHASING_GAMES_LOCK, connect_db(config) as conn:
        rows = conn.execute("SELECT * FROM chasing_games ORDER BY enabled DESC, updated_at DESC, title").fetchall()
        output = []
        for row in rows:
            results = conn.execute(
                """SELECT * FROM chasing_game_results WHERE chase_id = ? AND is_active = 1
                   ORDER BY last_seen_at DESC LIMIT 12""",
                (row["id"],),
            ).fetchall()
            output.append(
                chasing_game_row(
                    row,
                    [
                        {
                            "id": item["id"], "title": item["title"], "priceLabel": item["price_label"],
                            "conditionLabel": item["condition_label"], "shippingLabel": item["shipping_label"],
                            "locationLabel": item["location_label"], "listingType": item["listing_type"],
                            "listingUrl": item["listing_url"], "imageUrl": item["image_url"], "lastSeenAt": item["last_seen_at"],
                        }
                        for item in results
                    ],
                )
            )
    source = "eBay Sandbox · datos de prueba" if config.ebay_environment == "sandbox" else "eBay USA"
    return {"version": CHASING_GAMES_VERSION, "source": source, "environment": config.ebay_environment, "items": output}


def create_chasing_game(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    title = normalize_chase_text(payload.get("title"), "title", 200)
    platform = " ".join(str(payload.get("platform") or "").split())[:100]
    search_query = " ".join(str(payload.get("searchQuery") or f"{title} {platform}").split())[:300]
    chase_id = f"chase-{hashlib.sha1(f'{title}\0{platform}'.lower().encode('utf-8')).hexdigest()[:16]}"
    now = utc_now()
    with _CHASING_GAMES_LOCK, connect_db(config) as conn:
        conn.execute(
            """INSERT INTO chasing_games (id, title, platform, search_query, source, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET enabled = 1, updated_at = excluded.updated_at""",
            (chase_id, title, platform, search_query, CHASING_GAMES_SOURCE, now, now),
        )
    return run_chasing_game(config, chase_id)


def set_chasing_game_enabled(config: AppConfig, chase_id: Any, enabled: bool) -> dict[str, Any]:
    target_id = normalize_chase_id(chase_id)
    with _CHASING_GAMES_LOCK, connect_db(config) as conn:
        cursor = conn.execute("UPDATE chasing_games SET enabled = ?, updated_at = ? WHERE id = ?", (int(enabled), utc_now(), target_id))
    if cursor.rowcount != 1:
        raise ApiError(HTTPStatus.NOT_FOUND, "Chase not found")
    return {"ok": True, "id": target_id, "enabled": enabled}


def run_chasing_game(config: AppConfig, chase_id: Any) -> dict[str, Any]:
    target_id = normalize_chase_id(chase_id)
    with _CHASING_GAMES_LOCK, connect_db(config) as conn:
        row = conn.execute("SELECT * FROM chasing_games WHERE id = ?", (target_id,)).fetchone()
    if row is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "Chase not found")
    now = utc_now()
    try:
        listings = fetch_ebay_listings(config, str(row["search_query"]))
    except ApiError as error:
        with _CHASING_GAMES_LOCK, connect_db(config) as conn:
            conn.execute("UPDATE chasing_games SET last_checked_at = ?, last_error = ?, updated_at = ? WHERE id = ?", (now, error.message, now, target_id))
        raise
    with _CHASING_GAMES_LOCK, connect_db(config) as conn:
        conn.execute("UPDATE chasing_game_results SET is_active = 0 WHERE chase_id = ?", (target_id,))
        for listing in listings:
            external_id = str(listing["externalId"])
            result_id = f"ebay-{hashlib.sha1(f'{target_id}\0{external_id}'.encode('utf-8')).hexdigest()[:20]}"
            conn.execute(
                """INSERT INTO chasing_game_results (
                     id, chase_id, external_id, title, price_label, condition_label, shipping_label, location_label,
                     listing_type, listing_url, image_url, is_active, first_seen_at, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(chase_id, external_id) DO UPDATE SET
                     title=excluded.title, price_label=excluded.price_label, condition_label=excluded.condition_label,
                     shipping_label=excluded.shipping_label, location_label=excluded.location_label,
                     listing_type=excluded.listing_type, listing_url=excluded.listing_url, image_url=excluded.image_url,
                     is_active=1, last_seen_at=excluded.last_seen_at""",
                (result_id, target_id, external_id, listing["title"], listing["priceLabel"], listing["conditionLabel"], listing["shippingLabel"], listing["locationLabel"], listing["listingType"], listing["listingUrl"], listing["imageUrl"], now, now),
            )
        conn.execute("UPDATE chasing_games SET last_checked_at = ?, last_error = '', updated_at = ? WHERE id = ?", (now, now, target_id))
    return {"ok": True, "id": target_id, "results": len(listings), "checkedAt": now}


def run_enabled_chasing_games(config: AppConfig) -> None:
    with _CHASING_GAMES_LOCK, connect_db(config) as conn:
        rows = conn.execute("SELECT id FROM chasing_games WHERE enabled = 1").fetchall()
    for row in rows:
        try:
            run_chasing_game(config, row["id"])
        except ApiError as error:
            print(f"[consolas] Chasing Games error for {row['id']}: {error.message}")


class ChasingGamesScheduler(threading.Thread):
    def __init__(self, config: AppConfig) -> None:
        super().__init__(name="chasing-games", daemon=True)
        self.config = config

    def run(self) -> None:
        while True:
            run_enabled_chasing_games(self.config)
            threading.Event().wait(max(300, CHASING_GAMES_INTERVAL_SECONDS))


def build_health_payload(config: AppConfig) -> dict[str, Any]:
    checks = {
        "database": False,
        "static": config.static_dir.exists() and config.static_dir.is_dir(),
        "auctionWatchStorage": (
            config.auction_watch_dir.exists()
            and config.auction_watch_dir.is_dir()
            and os.access(config.auction_watch_dir, os.W_OK)
        ),
    }
    try:
        with connect_db(config) as conn:
            checks["database"] = conn.execute("SELECT 1").fetchone() is not None
    except sqlite3.Error:
        checks["database"] = False

    unavailable_sync = {
        "runId": "",
        "snapshotHash": "",
        "generatedAt": None,
        "acceptedAt": None,
        "source": "none",
        "ageSeconds": None,
        "status": "unavailable",
    }
    try:
        snapshot = read_auction_watch_snapshot(config)
        sync = snapshot.get("sync") if isinstance(snapshot.get("sync"), dict) else unavailable_sync
    except (OSError, sqlite3.Error):
        sync = unavailable_sync

    ready = all(checks.values())
    return {
        "ok": ready,
        "ready": ready,
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "database": str(config.db_path),
        "mediaDir": str(config.media_dir),
        "staticDir": str(config.static_dir),
        "storageBackend": "server",
        "checks": checks,
        "auctionWatch": sync,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ConsolasServer/0.1"

    def send_json(self, payload: Any, status: int | HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, error: ApiError) -> None:
        payload = {"ok": False, "error": error.message}
        if error.details is not None:
            payload["details"] = error.details
        self.send_json(payload, error.status)

    def config(self) -> AppConfig:
        return self.server.config  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        try:
            self.route_get()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def do_HEAD(self) -> None:
        try:
            path = self.path.split("?", 1)[0]
            if path.startswith("/api/"):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            target = safe_static_path(self.config().static_dir, path)
            if not target.exists() or not target.is_file():
                target = self.config().static_dir / "index.html"
            mime_type, _ = mimetypes.guess_type(str(target))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type or "application/octet-stream")
            self.send_header("Content-Length", str(target.stat().st_size))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def do_POST(self) -> None:
        try:
            self.route_post()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def do_PUT(self) -> None:
        try:
            self.route_put()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def do_DELETE(self) -> None:
        try:
            self.route_delete()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def route_get(self) -> None:
        config = self.config()
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self.send_json(build_health_payload(config))
            return
        if path in {"/api/readiness", "/api/ready"}:
            health = build_health_payload(config)
            self.send_json(
                health,
                HTTPStatus.OK if health["ready"] else HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        if path == "/api/state":
            self.send_json(read_state(config))
            return
        if path == "/api/state/export":
            self.send_json(build_state_export(config))
            return
        if path == "/api/auction-watch":
            self.send_json(read_auction_watch_snapshot(config))
            return
        if path == "/api/auction-watch/dismissals":
            self.send_json(list_auction_watch_dismissals(config))
            return
        if path == "/api/auction-watch/following":
            self.send_json(list_auction_watch_following(config))
            return
        if path == "/api/auction-watch/run-now":
            self.send_json(latest_auction_watch_run_request(config))
            return
        if path == "/api/chasing-games":
            self.send_json(list_chasing_games(config))
            return
        if path.startswith("/media/"):
            self.serve_media(path.removeprefix("/media/"))
            return
        self.serve_static(path)

    def route_put(self) -> None:
        if self.path.split("?", 1)[0] != "/api/state":
            raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        payload = read_json_body(self, self.config())
        self.send_json(write_state(self.config(), payload))

    def route_post(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            payload = read_json_body(self, self.config())
            self.send_json(write_state(self.config(), payload))
            return
        if path == "/api/state/restore":
            payload = read_json_body(self, self.config())
            self.send_json(restore_state(self.config(), payload))
            return
        if path == "/api/media":
            payload = read_json_body(self, self.config())
            self.send_json(save_media(self.config(), payload), HTTPStatus.CREATED)
            return
        if path == "/api/auction-watch/dismissals":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(
                dismiss_auction_watch_lot(self.config(), payload),
                HTTPStatus.CREATED,
            )
            return
        if path == "/api/auction-watch/following":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(follow_auction_watch_lot(self.config(), payload), HTTPStatus.CREATED)
            return
        if path == "/api/auction-watch/snapshot":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            expected_hash = self.headers.get(AUCTION_WATCH_SNAPSHOT_HASH_HEADER)
            if not expected_hash:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    f"{AUCTION_WATCH_SNAPSHOT_HASH_HEADER} header is required",
                )
            self.send_json(
                publish_auction_watch_snapshot(
                    self.config(),
                    payload,
                    expected_hash=expected_hash,
                )
            )
            return
        if path == "/api/auction-watch/run-now":
            require_auction_watch_write_request(self)
            self.send_json(enqueue_auction_watch_run(self.config()), HTTPStatus.ACCEPTED)
            return
        if path == "/api/auction-watch/run-now/claim":
            require_auction_watch_write_request(self)
            self.send_json(claim_auction_watch_run(self.config()))
            return
        if path == "/api/auction-watch/run-now/heartbeat":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(heartbeat_auction_watch_run(self.config(), payload))
            return
        if path == "/api/auction-watch/run-now/complete":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(complete_auction_watch_run(self.config(), payload))
            return
        if path == "/api/chasing-games":
            require_chasing_games_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(create_chasing_game(self.config(), payload), HTTPStatus.CREATED)
            return
        if path.startswith("/api/chasing-games/"):
            require_chasing_games_write_request(self)
            payload = read_json_body(self, self.config())
            parts = path.removeprefix("/api/chasing-games/").split("/")
            if len(parts) != 2:
                raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            chase_id, action = parts
            if action == "run":
                self.send_json(run_chasing_game(self.config(), chase_id))
                return
            if action == "enabled":
                self.send_json(set_chasing_game_enabled(self.config(), chase_id, payload.get("enabled") is True))
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def route_delete(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/chasing-games/"):
            require_chasing_games_write_request(self)
            chase_id = normalize_chase_id(parsed.path.removeprefix("/api/chasing-games/"))
            with _CHASING_GAMES_LOCK, connect_db(self.config()) as conn:
                cursor = conn.execute("DELETE FROM chasing_games WHERE id = ?", (chase_id,))
            if cursor.rowcount != 1:
                raise ApiError(HTTPStatus.NOT_FOUND, "Chase not found")
            self.send_json({"ok": True, "id": chase_id})
            return
        if parsed.path != "/api/auction-watch/dismissals":
            if parsed.path != "/api/auction-watch/following":
                raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        require_auction_watch_write_request(self)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        source_id = (query.get("sourceId") or [""])[0]
        lot_id = (query.get("lotId") or [""])[0]
        if parsed.path == "/api/auction-watch/following":
            self.send_json(unfollow_auction_watch_lot(self.config(), source_id, lot_id))
            return
        self.send_json(restore_auction_watch_lot(self.config(), source_id, lot_id))

    def serve_static(self, path: str) -> None:
        target = safe_static_path(self.config().static_dir, path)
        if not target.exists() or not target.is_file():
            target = self.config().static_dir / "index.html"
        self.send_file(target, deny_frame=target.name == "auction-watch-action.html")

    def serve_media(self, file_name: str) -> None:
        safe_name = Path(file_name).name
        target = (self.config().media_dir / safe_name).resolve()
        if not target.exists() or not target.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "Media not found")
        self.send_file(target, cache_control="public, max-age=31536000, immutable")

    def send_file(
        self,
        path: Path,
        cache_control: str = "no-cache",
        *,
        deny_frame: bool = False,
    ) -> None:
        data = path.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        if deny_frame:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[consolas] {self.address_string()} - {fmt % args}")


class Server(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler: type[Handler], config: AppConfig) -> None:
        self.config = config
        super().__init__(address, handler)


def main() -> int:
    config = AppConfig()
    init_db(config)
    ensure_state_media_migrated(config)
    ChasingGamesScheduler(config).start()
    print(f"[consolas] Starting on {config.host}:{config.port}")
    print(f"[consolas] Persistent data: {config.data_dir}")
    print(f"[consolas] Static web: {config.static_dir}")
    server = Server((config.host, config.port), Handler, config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
