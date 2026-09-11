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
import sys
import threading
import uuid
import urllib.parse
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# El add-on arranca `python3 /app/server/app.py` y los tests importan `server.app`.
# Poner este directorio en el path deja un único nombre `radar.*` en los dos modos,
# igual que hace `agents/auction-watch/scripts/run_watch.py` con su propio paquete.
_SERVER_DIR = Path(__file__).resolve().parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from radar.master import propose_master_searches  # noqa: E402
from radar.valuation import pick_benchmark, references_from_console_entry, score_listing  # noqa: E402
from radar.matching import evaluate_match  # noqa: E402
from radar.model import MarketplaceListing  # noqa: E402
from radar.sources import registry as radar_registry  # noqa: E402


SERVICE_NAME = "consolas-server"
SERVICE_VERSION = os.getenv("CONSOLAS_APP_VERSION", "0.1.26")
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

# Collection Radar: modelo general de busqueda persistente. Ver
# docs/COLLECTION_RADAR_IMPLEMENTATION_PLAN.md
RADAR_SEARCHES_VERSION = 1
RADAR_DEFAULT_SOURCE = "ebay-us"
RADAR_WRITE_HEADER = "X-Consolas-Radar"
RADAR_SEARCH_STATUSES = ("draft", "active", "paused", "archived")
RADAR_SEARCH_TYPES = ("chase", "console", "lot", "upgrade", "discovery", "master")
RADAR_SEARCH_ORIGINS = ("user", "master", "suggestion")
RADAR_PRIORITIES = ("alta", "media-alta", "media", "baja")
RADAR_ENTITY_TYPES = ("", "console", "game", "accessory", "manual")
RADAR_CONDITIONS = ("any", "new", "used", "refurbished")
RADAR_COMPLETENESS = ("any", "loose", "boxed", "cib", "sealed")
RADAR_REQUIREMENT_LEVELS = ("any", "preferred", "required")
RADAR_MAX_TERMS = 24
RADAR_MAX_TERM_LENGTH = 80
RADAR_DEFAULT_RESULT_LIMIT = 12
RADAR_MAX_RESULT_LIMIT = 50
RADAR_MIGRATION_CHASING_GAMES = "chasing_games_v1"
RADAR_MIGRATION_LISTINGS = "radar_listings_v1"
RADAR_MIGRATION_SEED = "seed_iss_deluxe_v1"
RADAR_MIGRATION_SELLER_IDENTITY = "drop_seller_identity_v1"

# Scheduler durable. Los horarios están cerrados en el PRD §16: tres corridas
# diarias en la zona del usuario. Un slot genera como máximo un scan.
RADAR_TIMEZONE_NAME = os.getenv("CONSOLAS_RADAR_TIMEZONE", "America/Montevideo")
RADAR_SLOTS: tuple[tuple[str, int, int], ...] = (
    ("morning", 9, 0),
    ("afternoon", 16, 0),
    ("night", 22, 30),
)
RADAR_SLOT_KEYS = tuple(slot[0] for slot in RADAR_SLOTS)
RADAR_SLOT_LABELS = {"morning": "09:00", "afternoon": "16:00", "night": "22:30"}
RADAR_SCHEDULER_INTERVAL_SECONDS = max(5, int(os.getenv("CONSOLAS_RADAR_SCHEDULER_INTERVAL_SECONDS", "60")))
# Una corrida manual reciente y exitosa satisface el slot siguiente, igual que en
# Auction Watch: no se vuelve a escanear lo mismo minutos después.
RADAR_MANUAL_FRESHNESS_MINUTES = max(0, int(os.getenv("CONSOLAS_RADAR_MANUAL_FRESHNESS_MINUTES", "30")))
RADAR_RUN_KINDS = ("scheduled", "manual")
RADAR_RUN_STATUSES = ("running", "completed", "degraded", "failed")
RADAR_SLOT_STATES = ("fulfilled", "skipped")
RADAR_RUN_HISTORY_LIMIT = 20

_AUCTION_WATCH_DISMISSALS_LOCK = threading.RLock()
_AUCTION_WATCH_SNAPSHOT_LOCK = threading.RLock()
_AUCTION_WATCH_RUN_LOCK = threading.RLock()
_RADAR_LOCK = threading.RLock()
_RADAR_RUN_LOCK = threading.RLock()
# Un lock por búsqueda: el scheduler y un "Buscar ahora" no pueden escanear la
# misma búsqueda a la vez, ni siquiera durante la llamada de red.
_RADAR_SEARCH_LOCKS: dict[str, threading.Lock] = {}
# Alias historico: Chasing Games y Collection Radar comparten dominio y lock.
_CHASING_GAMES_LOCK = _RADAR_LOCK


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

            CREATE TABLE IF NOT EXISTS radar_migrations (
              id TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS radar_searches (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              search_type TEXT NOT NULL DEFAULT 'chase',
              status TEXT NOT NULL DEFAULT 'active',
              origin TEXT NOT NULL DEFAULT 'user',
              priority TEXT NOT NULL DEFAULT 'media',
              platform TEXT NOT NULL DEFAULT '',
              entity_type TEXT NOT NULL DEFAULT '',
              entity_id TEXT NOT NULL DEFAULT '',
              search_query TEXT NOT NULL,
              criteria_json TEXT NOT NULL DEFAULT '{}',
              sources_json TEXT NOT NULL DEFAULT '[]',
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_checked_at TEXT,
              last_error TEXT NOT NULL DEFAULT '',
              archived_at TEXT,
              deleted_at TEXT,
              legacy_chase_id TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS radar_search_results (
              id TEXT PRIMARY KEY,
              search_id TEXT NOT NULL,
              source_id TEXT NOT NULL DEFAULT 'ebay-us',
              external_id TEXT NOT NULL,
              title TEXT NOT NULL,
              price_label TEXT NOT NULL DEFAULT '',
              price_amount REAL,
              price_currency TEXT NOT NULL DEFAULT '',
              condition_label TEXT NOT NULL DEFAULT '',
              shipping_label TEXT NOT NULL DEFAULT '',
              location_label TEXT NOT NULL DEFAULT '',
              listing_type TEXT NOT NULL DEFAULT '',
              listing_url TEXT NOT NULL,
              image_url TEXT NOT NULL DEFAULT '',
              is_active INTEGER NOT NULL DEFAULT 1,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              UNIQUE(search_id, source_id, external_id),
              FOREIGN KEY(search_id) REFERENCES radar_searches(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS radar_listings (
              id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              external_id TEXT NOT NULL,
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              listing_url TEXT NOT NULL,
              image_url TEXT NOT NULL DEFAULT '',
              listing_kind TEXT NOT NULL DEFAULT 'unknown',
              price_amount REAL,
              price_currency TEXT NOT NULL DEFAULT '',
              shipping_amount REAL,
              shipping_currency TEXT NOT NULL DEFAULT '',
              total_amount REAL,
              price_label TEXT NOT NULL DEFAULT '',
              shipping_label TEXT NOT NULL DEFAULT '',
              condition_label TEXT NOT NULL DEFAULT '',
              location_label TEXT NOT NULL DEFAULT '',
              seller_label TEXT NOT NULL DEFAULT '',
              availability TEXT NOT NULL DEFAULT 'unknown',
              closes_at TEXT NOT NULL DEFAULT '',
              verified_at TEXT,
              content_expires_at TEXT,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              UNIQUE(source_id, external_id)
            );

            CREATE TABLE IF NOT EXISTS radar_search_matches (
              search_id TEXT NOT NULL,
              listing_id TEXT NOT NULL,
              confidence REAL NOT NULL DEFAULT 0,
              reasons_json TEXT NOT NULL DEFAULT '[]',
              blockers_json TEXT NOT NULL DEFAULT '[]',
              unverified_json TEXT NOT NULL DEFAULT '[]',
              matched_terms_json TEXT NOT NULL DEFAULT '[]',
              is_active INTEGER NOT NULL DEFAULT 1,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              PRIMARY KEY (search_id, listing_id),
              FOREIGN KEY(search_id) REFERENCES radar_searches(id) ON DELETE CASCADE,
              FOREIGN KEY(listing_id) REFERENCES radar_listings(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS radar_runs (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              slot_key TEXT NOT NULL DEFAULT '',
              schedule_date TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              searches_total INTEGER NOT NULL DEFAULT 0,
              searches_ok INTEGER NOT NULL DEFAULT 0,
              searches_failed INTEGER NOT NULL DEFAULT 0,
              listings_matched INTEGER NOT NULL DEFAULT 0,
              listings_rejected INTEGER NOT NULL DEFAULT 0,
              detail TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS radar_run_receipts (
              run_id TEXT NOT NULL,
              search_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              status TEXT NOT NULL,
              listing_count INTEGER NOT NULL DEFAULT 0,
              matched_count INTEGER NOT NULL DEFAULT 0,
              rejected_count INTEGER NOT NULL DEFAULT 0,
              error_count INTEGER NOT NULL DEFAULT 0,
              errors_json TEXT NOT NULL DEFAULT '[]',
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              PRIMARY KEY (run_id, search_id, source_id),
              FOREIGN KEY(run_id) REFERENCES radar_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS radar_schedule_slots (
              schedule_date TEXT NOT NULL,
              slot_key TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'fulfilled',
              fulfilled_by_run_id TEXT NOT NULL DEFAULT '',
              detail TEXT NOT NULL DEFAULT '',
              claimed_at TEXT NOT NULL,
              PRIMARY KEY (schedule_date, slot_key)
            );

            CREATE INDEX IF NOT EXISTS radar_runs_started_idx
              ON radar_runs (started_at DESC);
            CREATE INDEX IF NOT EXISTS radar_searches_status_idx
              ON radar_searches (status, deleted_at);
            CREATE INDEX IF NOT EXISTS radar_search_results_active_idx
              ON radar_search_results (search_id, is_active);
            CREATE INDEX IF NOT EXISTS radar_search_matches_active_idx
              ON radar_search_matches (search_id, is_active);
            CREATE INDEX IF NOT EXISTS radar_search_matches_listing_idx
              ON radar_search_matches (listing_id, is_active);
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
        radar_search_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(radar_searches)").fetchall()
        }
        # Vacío significa "todos los slots": una búsqueda existente no cambia de
        # frecuencia por el hecho de actualizar el add-on.
        if "slots_json" not in radar_search_columns:
            conn.execute("ALTER TABLE radar_searches ADD COLUMN slots_json TEXT NOT NULL DEFAULT ''")
        match_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(radar_search_matches)").fetchall()
        }
        for column_name, column_definition in {
            "score": "INTEGER",
            "band": "TEXT NOT NULL DEFAULT ''",
            "valuation_json": "TEXT NOT NULL DEFAULT '{}'",
        }.items():
            if column_name not in match_columns:
                conn.execute(f"ALTER TABLE radar_search_matches ADD COLUMN {column_name} {column_definition}")
        migrate_chasing_games_to_radar(conn)
        migrate_radar_results_to_listings(conn)
        migrate_drop_seller_identity(conn)
        seed_radar_searches(conn)


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
    allowed_mime = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    suffix = Path(original_file_name).suffix.lower()
    if mime_type not in allowed_mime or suffix not in allowed_suffixes:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Only JPG, PNG, WEBP or GIF images are accepted")
    if len(data) > 8 * 1024 * 1024:
        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Image exceeds the 8 MB limit")
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


def radar_migration_applied(conn: sqlite3.Connection, migration_id: str) -> bool:
    return conn.execute("SELECT 1 FROM radar_migrations WHERE id = ?", (migration_id,)).fetchone() is not None


def mark_radar_migration(conn: sqlite3.Connection, migration_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO radar_migrations (id, applied_at) VALUES (?, ?)",
        (migration_id, utc_now()),
    )


def migrate_chasing_games_to_radar(conn: sqlite3.Connection) -> None:
    """Copy legacy Chasing Games searches into the general radar model, once.

    The marker is written even when there was nothing to copy. Guarding on "the
    radar table is empty" instead would resurrect every deleted search on the
    next restart.
    """

    if radar_migration_applied(conn, RADAR_MIGRATION_CHASING_GAMES):
        return
    for row in conn.execute("SELECT * FROM chasing_games ORDER BY created_at").fetchall():
        search_id = str(row["id"])
        conn.execute(
            """
            INSERT OR IGNORE INTO radar_searches (
              id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
              search_query, criteria_json, sources_json, notes, created_at, updated_at,
              last_checked_at, last_error, legacy_chase_id
            ) VALUES (?, ?, 'chase', ?, 'user', 'media', ?, '', '', ?, ?, ?, '', ?, ?, ?, ?, ?)
            """,
            (
                search_id,
                str(row["title"]),
                "active" if row["enabled"] else "paused",
                str(row["platform"] or ""),
                str(row["search_query"]),
                json.dumps(default_radar_criteria(), ensure_ascii=False, separators=(",", ":")),
                json.dumps([str(row["source"] or RADAR_DEFAULT_SOURCE)], ensure_ascii=False, separators=(",", ":")),
                str(row["created_at"]),
                str(row["updated_at"]),
                row["last_checked_at"],
                str(row["last_error"] or ""),
                search_id,
            ),
        )
        for result in conn.execute(
            "SELECT * FROM chasing_game_results WHERE chase_id = ?", (search_id,)
        ).fetchall():
            conn.execute(
                """
                INSERT OR IGNORE INTO radar_search_results (
                  id, search_id, source_id, external_id, title, price_label, price_amount, price_currency,
                  condition_label, shipping_label, location_label, listing_type, listing_url, image_url,
                  is_active, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(result["id"]),
                    search_id,
                    str(row["source"] or RADAR_DEFAULT_SOURCE),
                    str(result["external_id"]),
                    str(result["title"]),
                    str(result["price_label"] or ""),
                    str(result["condition_label"] or ""),
                    str(result["shipping_label"] or ""),
                    str(result["location_label"] or ""),
                    str(result["listing_type"] or ""),
                    str(result["listing_url"]),
                    str(result["image_url"] or ""),
                    int(result["is_active"]),
                    str(result["first_seen_at"]),
                    str(result["last_seen_at"]),
                ),
            )
    mark_radar_migration(conn, RADAR_MIGRATION_CHASING_GAMES)


def migrate_radar_results_to_listings(conn: sqlite3.Connection) -> None:
    """Divide los resultados por búsqueda en publicaciones y coincidencias.

    `radar_search_results` guardaba una fila por (búsqueda, publicación): la misma
    publicación se duplicaba en cada búsqueda que la encontrara. El modelo del PRD
    separa identidad (`radar_listings`) de intención (`radar_search_matches`), así
    una publicación existe una vez y se muestra una vez, con varias razones.

    Corre una sola vez, con marcador propio: la tabla vieja queda intacta como vía
    de rollback y deja de leerse.
    """

    if radar_migration_applied(conn, RADAR_MIGRATION_LISTINGS):
        return
    for row in conn.execute("SELECT * FROM radar_search_results ORDER BY first_seen_at").fetchall():
        source_id = str(row["source_id"] or RADAR_DEFAULT_SOURCE)
        external_id = str(row["external_id"])
        listing_id = radar_listing_id(source_id, external_id)
        price_amount = row["price_amount"]
        conn.execute(
            """
            INSERT INTO radar_listings (
              id, source_id, external_id, title, description, listing_url, image_url, listing_kind,
              price_amount, price_currency, shipping_amount, shipping_currency, total_amount,
              price_label, shipping_label, condition_label, location_label, seller_label,
              availability, closes_at, content_expires_at, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, NULL, '', ?, ?, ?, ?, ?, '', 'unknown', '', NULL, ?, ?)
            ON CONFLICT(source_id, external_id) DO UPDATE SET
              last_seen_at = MAX(radar_listings.last_seen_at, excluded.last_seen_at)
            """,
            (
                listing_id, source_id, external_id, str(row["title"]), str(row["listing_url"]),
                str(row["image_url"] or ""), radar_listing_kind_from_label(str(row["listing_type"] or "")),
                price_amount, str(row["price_currency"] or ""), price_amount,
                str(row["price_label"] or ""), str(row["shipping_label"] or ""),
                str(row["condition_label"] or ""), str(row["location_label"] or ""),
                str(row["first_seen_at"]), str(row["last_seen_at"]),
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO radar_search_matches (
              search_id, listing_id, confidence, reasons_json, blockers_json, unverified_json,
              matched_terms_json, is_active, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, '[]', '[]', '[]', ?, ?, ?)
            """,
            (
                str(row["search_id"]), listing_id, 0.5,
                json.dumps(["Resultado guardado antes del modelo de coincidencias"], ensure_ascii=False),
                int(row["is_active"]), str(row["first_seen_at"]), str(row["last_seen_at"]),
            ),
        )
    mark_radar_migration(conn, RADAR_MIGRATION_LISTINGS)


def migrate_drop_seller_identity(conn: sqlite3.Connection) -> None:
    """Borra los nombres de usuario de vendedores que se hayan guardado antes.

    Cambiar el adapter evita guardarlos de ahora en más, pero la declaración que
    se le firma a eBay —que la aplicación no almacena datos de sus usuarios—
    tiene que ser cierta también de lo que ya está en disco.

    El formato viejo era `usuario · 99.4%`. Se conserva la reputación, que es lo
    que el score necesita, y se descarta la identidad.
    """

    if radar_migration_applied(conn, RADAR_MIGRATION_SELLER_IDENTITY):
        return
    for row in conn.execute(
        "SELECT id, seller_label FROM radar_listings WHERE seller_label != ''"
    ).fetchall():
        label = str(row["seller_label"])
        if "·" not in label:
            continue
        percentage = label.rsplit("·", 1)[-1].strip().rstrip("%").strip()
        replacement = f"{percentage}% de feedback" if percentage else ""
        conn.execute(
            "UPDATE radar_listings SET seller_label = ? WHERE id = ?", (replacement, str(row["id"]))
        )
    mark_radar_migration(conn, RADAR_MIGRATION_SELLER_IDENTITY)


def seed_radar_searches(conn: sqlite3.Connection) -> None:
    """Create the first explicit chase once, without touching collection state."""
    if radar_migration_applied(conn, RADAR_MIGRATION_SEED):
        return
    mark_radar_migration(conn, RADAR_MIGRATION_SEED)
    if conn.execute("SELECT 1 FROM radar_searches LIMIT 1").fetchone():
        return
    now = utc_now()
    conn.execute(
        """
        INSERT INTO radar_searches (
          id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
          search_query, criteria_json, sources_json, notes, created_at, updated_at
        ) VALUES (?, ?, 'chase', 'active', 'user', 'alta', ?, '', '', ?, ?, ?, '', ?, ?)
        """,
        (
            "iss-deluxe-snes",
            "International Superstar Soccer Deluxe",
            "SNES",
            "International Superstar Soccer Deluxe SNES",
            json.dumps(default_radar_criteria(), ensure_ascii=False, separators=(",", ":")),
            json.dumps([RADAR_DEFAULT_SOURCE], ensure_ascii=False, separators=(",", ":")),
            now,
            now,
        ),
    )


def require_chasing_games_write_request(handler: BaseHTTPRequestHandler) -> None:
    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type application/json is required")
    if handler.headers.get("X-Consolas-Chasing-Games") != "1" and handler.headers.get(RADAR_WRITE_HEADER) != "1":
        raise ApiError(HTTPStatus.FORBIDDEN, "Chasing Games action header is required")


def require_radar_write_request(handler: BaseHTTPRequestHandler) -> None:
    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type application/json is required")
    if handler.headers.get(RADAR_WRITE_HEADER) != "1":
        raise ApiError(HTTPStatus.FORBIDDEN, "Collection Radar action header is required")


def radar_sources_payload() -> list[dict[str, Any]]:
    """Descripción pública de cada fuente registrada y sus capabilities."""
    return radar_registry.sources_payload()


def executable_radar_sources(source_ids: list[str]) -> list[str]:
    return radar_registry.executable_source_ids(source_ids)


def radar_source_capabilities(source_id: str) -> dict[str, Any]:
    spec = radar_registry.get_source(source_id)
    return dict(spec.capabilities) if spec else {}


def radar_source_label(source_id: str) -> str:
    spec = radar_registry.get_source(source_id)
    return spec.label if spec else str(source_id or "")


def default_radar_criteria() -> dict[str, Any]:
    return {
        "includeTerms": [],
        "anyTerms": [],
        "excludeTerms": [],
        "region": "",
        "condition": "any",
        "completeness": "any",
        "tested": "any",
        "originalParts": "any",
        "returnsRequired": False,
        "freeShippingOnly": False,
        "currency": "USD",
        "maxItemPrice": None,
        "maxTotalUsa": None,
        "minLotSize": None,
        "resultLimit": RADAR_DEFAULT_RESULT_LIMIT,
    }


def normalize_radar_id(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate or len(candidate) > 100 or not all(char.isalnum() or char in {"-", "_"} for char in candidate):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid radar search id")
    return candidate


def normalize_radar_text(value: Any, field: str, limit: int, *, required: bool = True) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text and required:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} is required")
    if len(text) > limit:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} is too long")
    return text


def normalize_radar_enum(value: Any, allowed: tuple[str, ...], default: str, field: str) -> str:
    if value is None:
        return default
    candidate = str(value).strip().lower()
    if not candidate:
        return default
    if candidate not in allowed:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be one of: {', '.join(item for item in allowed if item)}")
    return candidate


def normalize_radar_terms(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items: list[Any] = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a list of terms")
    terms: list[str] = []
    for item in raw_items:
        if isinstance(item, (list, dict)):
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a list of terms")
        term = " ".join(str(item or "").split()).strip()
        if not term:
            continue
        if len(term) > RADAR_MAX_TERM_LENGTH:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} has a term that is too long")
        if term.lower() not in {existing.lower() for existing in terms}:
            terms.append(term)
    if len(terms) > RADAR_MAX_TERMS:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} accepts up to {RADAR_MAX_TERMS} terms")
    return terms


def normalize_radar_amount(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a number") from None
    if amount != amount or amount in {float("inf"), float("-inf")}:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a number")
    if amount < 0:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} cannot be negative")
    return round(amount, 2)


def normalize_radar_count(value: Any, field: str, maximum: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a whole number") from None
    if count < 1 or count > maximum:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be between 1 and {maximum}")
    return count


def normalize_radar_criteria(value: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the structured criteria of a search and drop unknown fields."""
    criteria = default_radar_criteria()
    criteria.update(base or {})
    if value is None:
        return criteria
    if not isinstance(value, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "criteria must be an object")
    for field in ("includeTerms", "anyTerms", "excludeTerms"):
        if field in value:
            criteria[field] = normalize_radar_terms(value.get(field), field)
    if "region" in value:
        criteria["region"] = normalize_radar_text(value.get("region"), "region", 60, required=False)
    if "condition" in value:
        criteria["condition"] = normalize_radar_enum(value.get("condition"), RADAR_CONDITIONS, "any", "condition")
    if "completeness" in value:
        criteria["completeness"] = normalize_radar_enum(
            value.get("completeness"), RADAR_COMPLETENESS, "any", "completeness"
        )
    if "tested" in value:
        criteria["tested"] = normalize_radar_enum(value.get("tested"), RADAR_REQUIREMENT_LEVELS, "any", "tested")
    if "originalParts" in value:
        criteria["originalParts"] = normalize_radar_enum(
            value.get("originalParts"), RADAR_REQUIREMENT_LEVELS, "any", "originalParts"
        )
    if "returnsRequired" in value:
        criteria["returnsRequired"] = value.get("returnsRequired") is True
    if "freeShippingOnly" in value:
        criteria["freeShippingOnly"] = value.get("freeShippingOnly") is True
    if "currency" in value:
        currency = normalize_radar_text(value.get("currency"), "currency", 8, required=False).upper()
        criteria["currency"] = currency or "USD"
    if "maxItemPrice" in value:
        criteria["maxItemPrice"] = normalize_radar_amount(value.get("maxItemPrice"), "maxItemPrice")
    if "maxTotalUsa" in value:
        criteria["maxTotalUsa"] = normalize_radar_amount(value.get("maxTotalUsa"), "maxTotalUsa")
    if "minLotSize" in value:
        criteria["minLotSize"] = normalize_radar_count(value.get("minLotSize"), "minLotSize", 500)
    if "resultLimit" in value:
        criteria["resultLimit"] = (
            normalize_radar_count(value.get("resultLimit"), "resultLimit", RADAR_MAX_RESULT_LIMIT)
            or RADAR_DEFAULT_RESULT_LIMIT
        )
    return criteria


def normalize_radar_sources(value: Any) -> list[str]:
    if value is None:
        return [RADAR_DEFAULT_SOURCE]
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ApiError(HTTPStatus.BAD_REQUEST, "sources must be a list of source ids")
    sources: list[str] = []
    for item in raw_items:
        source_id = str(item or "").strip().lower()
        if not source_id:
            continue
        if radar_registry.get_source(source_id) is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"Unknown radar source: {source_id}")
        if source_id not in sources:
            sources.append(source_id)
    return sources or [RADAR_DEFAULT_SOURCE]


def build_radar_search_query(name: str, platform: str, criteria: dict[str, Any], explicit: Any = None) -> str:
    """Explicit query wins; otherwise derive a predictable one from identity and terms."""
    explicit_query = " ".join(str(explicit or "").split()).strip()
    if explicit_query:
        return explicit_query[:300]
    parts = [name, platform]
    for term in criteria.get("includeTerms") or []:
        parts.append(term)
    seen: set[str] = set()
    words: list[str] = []
    for part in parts:
        candidate = " ".join(str(part or "").split()).strip()
        if not candidate or candidate.lower() in seen:
            continue
        seen.add(candidate.lower())
        words.append(candidate)
    return " ".join(words)[:300]


def radar_json_field(raw: Any, fallback: Any) -> Any:
    """Read a persisted JSON column, falling back when the row is unreadable.

    The fallback also declares the expected shape: a value of another type is a
    corrupted row and must not reach the normalizers. Pass ``{}``/``None`` for an
    object column and ``[]`` for an array one.
    """

    expected = dict if fallback is None else type(fallback)
    try:
        parsed = json.loads(str(raw or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, expected) else fallback


def radar_search_row(row: sqlite3.Row, results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    criteria = normalize_radar_criteria(radar_json_field(row["criteria_json"], None))
    raw_sources = radar_json_field(row["sources_json"], [RADAR_DEFAULT_SOURCE])
    sources = [
        str(item) for item in raw_sources if radar_registry.get_source(str(item)) is not None
    ] or [RADAR_DEFAULT_SOURCE]
    status = str(row["status"])
    runnable_sources = executable_radar_sources(sources)
    return {
        "id": row["id"],
        "name": row["name"],
        # Compatibilidad con la superficie previa de Chasing Games.
        "title": row["name"],
        "searchType": row["search_type"],
        "status": status,
        "origin": row["origin"],
        "priority": row["priority"],
        "platform": row["platform"],
        "entityType": row["entity_type"],
        "entityId": row["entity_id"],
        "searchQuery": row["search_query"],
        "criteria": criteria,
        "sources": sources,
        "executableSources": runnable_sources,
        "notes": row["notes"],
        "slots": radar_search_slots(row),
        "slotLabels": [RADAR_SLOT_LABELS[key] for key in radar_search_slots(row)],
        "enabled": status == "active",
        "canRun": status == "active" and bool(runnable_sources),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "lastCheckedAt": row["last_checked_at"],
        "lastError": row["last_error"],
        "archivedAt": row["archived_at"],
        "results": results or [],
        "resultCount": len(results or []),
    }


def radar_result_row(row: sqlite3.Row) -> dict[str, Any]:
    """Una coincidencia vista desde su búsqueda: la publicación más sus razones."""
    reasons = radar_json_field(row["reasons_json"], [])
    unverified = radar_json_field(row["unverified_json"], [])
    matched_terms = radar_json_field(row["matched_terms_json"], [])
    return {
        "id": row["listing_id"],
        "listingId": row["listing_id"],
        "sourceId": row["source_id"],
        "sourceLabel": radar_source_label(str(row["source_id"])),
        "externalId": row["external_id"],
        "title": row["title"],
        "priceLabel": row["price_label"],
        "priceAmount": row["price_amount"],
        "priceCurrency": row["price_currency"],
        "shippingAmount": row["shipping_amount"],
        "totalAmount": row["total_amount"],
        "conditionLabel": row["condition_label"],
        "shippingLabel": row["shipping_label"],
        "locationLabel": row["location_label"],
        "sellerLabel": row["seller_label"],
        "listingKind": row["listing_kind"],
        "listingType": RADAR_LISTING_KIND_LABELS.get(str(row["listing_kind"]), ""),
        "listingUrl": row["listing_url"],
        "imageUrl": row["image_url"],
        "availability": row["availability"],
        "closesAt": row["closes_at"],
        "confidence": row["confidence"],
        "score": row["score"],
        "band": row["band"],
        "valuation": radar_json_field(row["valuation_json"], {}),
        "reasons": reasons,
        "unverified": unverified,
        "matchedTerms": matched_terms,
        "firstSeenAt": row["first_seen_at"],
        "lastSeenAt": row["last_seen_at"],
    }


def load_radar_search(conn: sqlite3.Connection, search_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM radar_searches WHERE id = ? AND deleted_at IS NULL", (search_id,)
    ).fetchone()
    if row is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "Radar search not found")
    return row


RADAR_MATCH_SELECT = """
    SELECT l.*, m.search_id, m.listing_id, m.confidence, m.reasons_json, m.blockers_json,
           m.unverified_json, m.matched_terms_json, m.score, m.band, m.valuation_json,
           m.first_seen_at AS match_first_seen_at, m.last_seen_at AS match_last_seen_at
      FROM radar_search_matches m
      JOIN radar_listings l ON l.id = m.listing_id
"""


def radar_search_results(conn: sqlite3.Connection, search_id: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""{RADAR_MATCH_SELECT}
            WHERE m.search_id = ? AND m.is_active = 1
            ORDER BY COALESCE(m.score, -1) DESC, m.confidence DESC, m.last_seen_at DESC LIMIT ?""",
        (search_id, max(1, min(limit, RADAR_MAX_RESULT_LIMIT))),
    ).fetchall()
    return [radar_result_row(row) for row in rows]


def list_radar_listings(config: AppConfig, limit: int = 100) -> dict[str, Any]:
    """Inventario deduplicado: una publicación, una fila, todas sus búsquedas.

    Es el criterio de aceptación §21.7 del PRD: una publicación que coincide con
    dos búsquedas se guarda una vez y se muestra una vez, con las dos razones.
    """

    capped = max(1, min(int(limit or 100), 500))
    with _RADAR_LOCK, connect_db(config) as conn:
        listing_rows = conn.execute(
            """
            SELECT l.*, MAX(m.confidence) AS best_confidence, COUNT(*) AS match_count,
                   MAX(m.last_seen_at) AS match_last_seen_at
              FROM radar_listings l
              JOIN radar_search_matches m ON m.listing_id = l.id AND m.is_active = 1
              JOIN radar_searches s ON s.id = m.search_id AND s.deleted_at IS NULL
             GROUP BY l.id
             ORDER BY best_confidence DESC, match_last_seen_at DESC
             LIMIT ?
            """,
            (capped,),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in listing_rows:
            matches = conn.execute(
                """
                SELECT m.search_id, m.confidence, m.reasons_json, m.unverified_json,
                       m.matched_terms_json, s.name AS search_name, s.search_type, s.priority
                  FROM radar_search_matches m
                  JOIN radar_searches s ON s.id = m.search_id AND s.deleted_at IS NULL
                 WHERE m.listing_id = ? AND m.is_active = 1
                 ORDER BY m.confidence DESC, s.name
                """,
                (row["id"],),
            ).fetchall()
            items.append(
                {
                    "id": row["id"],
                    "sourceId": row["source_id"],
                    "sourceLabel": radar_source_label(str(row["source_id"])),
                    "externalId": row["external_id"],
                    "title": row["title"],
                    "listingUrl": row["listing_url"],
                    "imageUrl": row["image_url"],
                    "listingKind": row["listing_kind"],
                    "listingType": RADAR_LISTING_KIND_LABELS.get(str(row["listing_kind"]), ""),
                    "priceLabel": row["price_label"],
                    "priceAmount": row["price_amount"],
                    "priceCurrency": row["price_currency"],
                    "shippingAmount": row["shipping_amount"],
                    "shippingLabel": row["shipping_label"],
                    "totalAmount": row["total_amount"],
                    "conditionLabel": row["condition_label"],
                    "locationLabel": row["location_label"],
                    "sellerLabel": row["seller_label"],
                    "availability": row["availability"],
                    "closesAt": row["closes_at"],
                    "confidence": row["best_confidence"],
                    "firstSeenAt": row["first_seen_at"],
                    "lastSeenAt": row["last_seen_at"],
                    "matchCount": row["match_count"],
                    "matches": [
                        {
                            "searchId": match["search_id"],
                            "searchName": match["search_name"],
                            "searchType": match["search_type"],
                            "priority": match["priority"],
                            "confidence": match["confidence"],
                            "reasons": radar_json_field(match["reasons_json"], []),
                            "unverified": radar_json_field(match["unverified_json"], []),
                            "matchedTerms": radar_json_field(match["matched_terms_json"], []),
                        }
                        for match in matches
                    ],
                }
            )
    return {
        "version": RADAR_SEARCHES_VERSION,
        "environment": config.ebay_environment,
        "sources": radar_sources_payload(),
        "count": len(items),
        "items": items,
    }


def radar_environment_label(config: AppConfig) -> str:
    return "eBay Sandbox · datos de prueba" if config.ebay_environment == "sandbox" else "eBay USA"


def list_radar_searches(config: AppConfig) -> dict[str, Any]:
    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            """SELECT * FROM radar_searches WHERE deleted_at IS NULL
               ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'draft' THEN 1 WHEN 'paused' THEN 2 ELSE 3 END,
                        updated_at DESC, name"""
        ).fetchall()
        items = [
            radar_search_row(
                row,
                radar_search_results(
                    conn,
                    str(row["id"]),
                    int(normalize_radar_criteria(radar_json_field(row["criteria_json"], None))["resultLimit"]),
                ),
            )
            for row in rows
        ]
    counts = {status: sum(1 for item in items if item["status"] == status) for status in RADAR_SEARCH_STATUSES}
    return {
        "version": RADAR_SEARCHES_VERSION,
        "source": radar_environment_label(config),
        "environment": config.ebay_environment,
        "sources": radar_sources_payload(),
        "counts": counts,
        "items": items,
    }


def radar_search_payload(config: AppConfig, search_id: str) -> dict[str, Any]:
    with _RADAR_LOCK, connect_db(config) as conn:
        row = load_radar_search(conn, search_id)
        results = radar_search_results(
            conn,
            search_id,
            int(normalize_radar_criteria(radar_json_field(row["criteria_json"], None))["resultLimit"]),
        )
        return {"ok": True, "search": radar_search_row(row, results)}


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
    notes = normalize_radar_text(payload.get("notes"), "notes", 600, required=False)
    criteria = normalize_radar_criteria(payload.get("criteria"))
    sources = normalize_radar_sources(payload.get("sources"))
    slots = normalize_radar_slots(payload.get("slots"))
    search_query = build_radar_search_query(name, platform, criteria, payload.get("searchQuery"))
    now = utc_now()
    search_id = f"radar-{uuid.uuid4().hex[:16]}"
    with _RADAR_LOCK, connect_db(config) as conn:
        assert_radar_name_is_free(conn, name, platform)
        conn.execute(
            """INSERT INTO radar_searches (
                 id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
                 search_query, criteria_json, sources_json, slots_json, notes, created_at, updated_at, archived_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                search_id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
                search_query,
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
        if "searchQuery" in payload:
            search_query = build_radar_search_query(name, platform, criteria, payload.get("searchQuery"))
        elif "criteria" in payload or "name" in payload or "title" in payload or "platform" in payload:
            # Sólo se regenera una consulta derivada; una consulta escrita a mano se respeta.
            derived_before = build_radar_search_query(str(row["name"]), str(row["platform"]), stored_criteria)
            search_query = (
                build_radar_search_query(name, platform, criteria)
                if derived_before == str(row["search_query"])
                else str(row["search_query"])
            )
        else:
            search_query = str(row["search_query"])
        assert_radar_name_is_free(conn, name, platform, target_id)
        conn.execute(
            """UPDATE radar_searches SET
                 name = ?, search_type = ?, priority = ?, platform = ?, entity_type = ?, entity_id = ?,
                 search_query = ?, criteria_json = ?, sources_json = ?, slots_json = ?, notes = ?, updated_at = ?
               WHERE id = ?""",
            (
                name, search_type, priority, platform, entity_type, entity_id, search_query,
                json.dumps(criteria, ensure_ascii=False, separators=(",", ":")),
                json.dumps(sources, ensure_ascii=False, separators=(",", ":")),
                json.dumps(slots, ensure_ascii=False, separators=(",", ":")),
                notes, utc_now(), target_id,
            ),
        )
    return radar_search_payload(config, target_id)


def set_radar_search_status(config: AppConfig, search_id: Any, status: Any) -> dict[str, Any]:
    target_id = normalize_radar_id(search_id)
    next_status = str(status or "").strip().lower()
    if next_status not in RADAR_SEARCH_STATUSES:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"status must be one of: {', '.join(RADAR_SEARCH_STATUSES)}")
    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        load_radar_search(conn, target_id)
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


RADAR_LISTING_KIND_LABELS = {
    "fixed_price": "Compra directa",
    "auction": "Subasta",
    "best_offer": "Acepta ofertas",
    "unknown": "",
}


def collect_radar_source_pages(
    config: AppConfig, query: str, criteria: dict[str, Any], sources: list[str]
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Consulta cada fuente ejecutable de forma aislada.

    Una fuente rota no puede ocultar a las demás ni vaciar el inventario: su falla
    queda en el recibo y el resto de las publicaciones sigue llegando.
    """

    pages: list[Any] = []
    receipts: list[dict[str, Any]] = []
    for source_id in executable_radar_sources(sources):
        spec = radar_registry.get_source(source_id)
        if spec is None:  # pragma: no cover - defensivo
            continue
        started_at = utc_now()
        try:
            adapter = spec.load()
            page = adapter.search(config, query, criteria)
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


def radar_entity_references(config: AppConfig, entity_type: str, entity_id: str) -> list[Any]:
    """Referencias de precio de la entidad vinculada, con su procedencia.

    Hoy sólo hay catálogo de consolas; una búsqueda sin entidad vinculada se
    queda sin benchmark y lo dice, en vez de compararse contra cualquier cosa.
    """

    if entity_type != "console" or not entity_id:
        return []
    for entry in load_console_catalog(config):
        if str(entry.get("id") or "") == entity_id:
            return references_from_console_entry(entry)
    return []


def valuate_radar_match(
    listing: MarketplaceListing, verdict: Any, criteria: dict[str, Any], references: list[Any],
    entity_type: str = "",
) -> Any:
    """Puntúa una coincidencia que ya pasó los filtros obligatorios."""
    completeness = str(criteria.get("completeness") or "any")
    benchmark = pick_benchmark(references, completeness=completeness if completeness != "any" else "loose")
    return score_listing(
        price_amount=listing.price_amount,
        shipping_amount=listing.shipping_amount,
        currency=listing.price_currency or str(criteria.get("currency") or "USD"),
        benchmark=benchmark,
        match_confidence=verdict.confidence,
        match_reasons=verdict.reasons,
        match_unverified=verdict.unverified,
        completeness=completeness if completeness != "any" else "loose",
        seller_known=bool(listing.seller_label),
        # El tipo de entidad decide el peso estimado del courier. Un lote no
        # declara entidad y por eso no recibe un costo importado inventado.
        entity_type=entity_type,
    )


def run_radar_search(config: AppConfig, search_id: Any, run_id: str = "") -> dict[str, Any]:
    """Ejecuta una búsqueda activa: consulta, evalúa y guarda sus coincidencias.

    El lock por búsqueda cubre también la llamada de red: el scheduler y un
    “Buscar ahora” simultáneos no pueden escanear dos veces lo mismo.
    """
    target_id = normalize_radar_id(search_id)
    with radar_search_lock(target_id):
        return execute_radar_search(config, target_id, run_id)


def execute_radar_search(config: AppConfig, target_id: str, run_id: str = "") -> dict[str, Any]:
    with _RADAR_LOCK, connect_db(config) as conn:
        row = load_radar_search(conn, target_id)
    status = str(row["status"])
    if status != "active":
        raise ApiError(
            HTTPStatus.CONFLICT,
            "Sólo una búsqueda activa puede ejecutarse. Activala o reanudala primero.",
            {"id": target_id, "status": status},
        )
    criteria = normalize_radar_criteria(radar_json_field(row["criteria_json"], None))
    sources = normalize_radar_sources(radar_json_field(row["sources_json"], []))
    if not executable_radar_sources(sources):
        raise ApiError(
            HTTPStatus.CONFLICT,
            "Ninguna de las fuentes de esta búsqueda puede ejecutarse todavía.",
            {"id": target_id, "sources": sources},
        )

    now = utc_now()
    pages, receipts = collect_radar_source_pages(config, str(row["search_query"]), criteria, sources)
    failures = [receipt for receipt in receipts if receipt["status"] == "failed"]
    authoritative = bool(receipts) and all(receipt["authoritative"] for receipt in receipts)

    # Ninguna fuente respondió: se conserva el inventario previo y se explica la falla.
    # Una respuesta fallida nunca prueba que una publicación dejó de existir.
    if failures and len(failures) == len(receipts):
        message = " · ".join(
            error for receipt in failures for error in (receipt.get("errors") or ["Falla de la fuente"])
        )
        with _RADAR_LOCK, connect_db(config) as conn:
            conn.execute(
                "UPDATE radar_searches SET last_checked_at = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (now, message, now, target_id),
            )
        record_radar_receipts(config, run_id, target_id, receipts, 0, 0)
        raise ApiError(HTTPStatus.BAD_GATEWAY, message, {"id": target_id, "receipts": receipts})

    capabilities_by_source = {source_id: radar_source_capabilities(source_id) for source_id in sources}
    references = radar_entity_references(config, str(row["entity_type"]), str(row["entity_id"]))
    matched_ids: list[str] = []
    rejected = 0

    with _RADAR_LOCK, connect_db(config) as conn:
        for page in pages:
            for listing in page.listings:
                verdict = evaluate_match(listing, criteria, capabilities_by_source.get(listing.source_id, {}))
                if not verdict.matched:
                    rejected += 1
                    continue
                listing_id = upsert_radar_listing(conn, listing, now)
                card = valuate_radar_match(listing, verdict, criteria, references, str(row["entity_type"]))
                conn.execute(
                    """
                    INSERT INTO radar_search_matches (
                      search_id, listing_id, confidence, reasons_json, blockers_json, unverified_json,
                      matched_terms_json, score, band, valuation_json, is_active, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(search_id, listing_id) DO UPDATE SET
                      confidence=excluded.confidence, reasons_json=excluded.reasons_json,
                      blockers_json=excluded.blockers_json, unverified_json=excluded.unverified_json,
                      matched_terms_json=excluded.matched_terms_json, score=excluded.score,
                      band=excluded.band, valuation_json=excluded.valuation_json, is_active=1,
                      last_seen_at=excluded.last_seen_at
                    """,
                    (
                        target_id, listing_id, verdict.confidence,
                        json.dumps(verdict.reasons, ensure_ascii=False),
                        json.dumps(verdict.blockers, ensure_ascii=False),
                        json.dumps(verdict.unverified, ensure_ascii=False),
                        json.dumps(verdict.matched_terms, ensure_ascii=False),
                        card.score, card.band,
                        json.dumps(card.to_dict(), ensure_ascii=False, separators=(",", ":")),
                        now, now,
                    ),
                )
                matched_ids.append(listing_id)

        # Sólo una cobertura completa autoriza retirar coincidencias previas.
        if authoritative:
            placeholders = ",".join("?" for _ in matched_ids)
            parameters: list[Any] = [target_id]
            query = "UPDATE radar_search_matches SET is_active = 0 WHERE search_id = ? AND is_active = 1"
            if matched_ids:
                query += f" AND listing_id NOT IN ({placeholders})"
                parameters.extend(matched_ids)
            conn.execute(query, parameters)

        last_error = ""
        if failures:
            last_error = " · ".join(
                error for receipt in failures for error in (receipt.get("errors") or ["Falla de la fuente"])
            )
        conn.execute(
            "UPDATE radar_searches SET last_checked_at = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (now, last_error, now, target_id),
        )

    record_radar_receipts(config, run_id, target_id, receipts, len(matched_ids), rejected)
    return {
        "ok": True,
        "id": target_id,
        "results": len(matched_ids),
        "rejected": rejected,
        "checkedAt": now,
        "authoritative": authoritative,
        "receipts": receipts,
        "runId": run_id,
    }


# --------------------------------------------------------------------------- #
# Scheduler durable: slots, corridas y recibos                                  #
# --------------------------------------------------------------------------- #
#
# Contrato heredado de Auction Watch (docs/AUCTION_WATCH_RELIABILITY.md):
#
# - un slot genera como máximo un scan;
# - el slot se reclama de forma atómica antes de escanear, así un reinicio en
#   medio de una corrida no dispara un segundo scan de la misma ventana;
# - una corrida parcial o fallida nunca retira inventario;
# - cada corrida deja recibos por búsqueda y fuente;
# - una corrida manual reciente y exitosa satisface el slot siguiente.


def radar_timezone() -> timezone | Any:
    """Zona del usuario, con fallback explícito si la imagen no trae tzdata.

    Uruguay no tiene horario de verano desde 2015, así que UTC-3 fijo es un
    reemplazo correcto y no una aproximación silenciosa.
    """

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(RADAR_TIMEZONE_NAME)
    except Exception:  # pragma: no cover - sólo sin tzdata en la imagen
        print(f"[consolas] Sin tzdata para {RADAR_TIMEZONE_NAME}: el radar usa UTC-3 fijo")
        return timezone(timedelta(hours=-3))


def radar_now_local() -> datetime:
    return datetime.now(radar_timezone())


def parse_iso_datetime(value: Any) -> datetime | None:
    """Lee un timestamp persistido. Una fila ilegible devuelve `None`, no explota."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def radar_slot_datetime(slot_key: str, reference: datetime) -> datetime | None:
    for key, hour, minute in RADAR_SLOTS:
        if key == slot_key:
            return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return None


def radar_due_slots(now: datetime) -> list[str]:
    """Slots de hoy cuya hora ya pasó, del más viejo al más nuevo."""
    due: list[str] = []
    for key, hour, minute in RADAR_SLOTS:
        if now >= now.replace(hour=hour, minute=minute, second=0, microsecond=0):
            due.append(key)
    return due


def radar_next_slot(now: datetime) -> dict[str, Any]:
    """El próximo slot, hoy o mañana, en ISO local."""
    for key, hour, minute in RADAR_SLOTS:
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot_time > now:
            return {"slotKey": key, "label": RADAR_SLOT_LABELS[key], "at": slot_time.isoformat()}
    first_key, hour, minute = RADAR_SLOTS[0]
    tomorrow = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return {"slotKey": first_key, "label": RADAR_SLOT_LABELS[first_key], "at": tomorrow.isoformat()}


def normalize_radar_slots(value: Any) -> list[str]:
    """Slots elegidos por una búsqueda. Lista vacía = todos."""
    if value is None:
        return []
    if isinstance(value, str):
        raw_items: list[Any] = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ApiError(HTTPStatus.BAD_REQUEST, "slots must be a list of slot keys")
    slots: list[str] = []
    for item in raw_items:
        key = str(item or "").strip().lower()
        if not key:
            continue
        if key not in RADAR_SLOT_KEYS:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, f"slots must be any of: {', '.join(RADAR_SLOT_KEYS)}"
            )
        if key not in slots:
            slots.append(key)
    return [key for key in RADAR_SLOT_KEYS if key in slots]


def radar_search_slots(row: sqlite3.Row) -> list[str]:
    stored = radar_json_field(row["slots_json"] if "slots_json" in row.keys() else "", [])
    slots = [key for key in stored if key in RADAR_SLOT_KEYS]
    return slots or list(RADAR_SLOT_KEYS)


def radar_search_lock(search_id: str) -> threading.Lock:
    with _RADAR_LOCK:
        return _RADAR_SEARCH_LOCKS.setdefault(search_id, threading.Lock())


def radar_run_row(row: sqlite3.Row, receipts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "slotKey": row["slot_key"],
        "slotLabel": RADAR_SLOT_LABELS.get(str(row["slot_key"]), ""),
        "scheduleDate": row["schedule_date"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "searchesTotal": row["searches_total"],
        "searchesOk": row["searches_ok"],
        "searchesFailed": row["searches_failed"],
        "listingsMatched": row["listings_matched"],
        "listingsRejected": row["listings_rejected"],
        "detail": row["detail"],
        "receipts": receipts or [],
    }


def radar_receipt_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "searchId": row["search_id"],
        "sourceId": row["source_id"],
        "sourceLabel": radar_source_label(str(row["source_id"])),
        "status": row["status"],
        "listingCount": row["listing_count"],
        "matchedCount": row["matched_count"],
        "rejectedCount": row["rejected_count"],
        "errorCount": row["error_count"],
        "errors": radar_json_field(row["errors_json"], []),
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
    }


def start_radar_run(config: AppConfig, kind: str, slot_key: str = "", schedule_date: str = "") -> str:
    run_id = f"radar-run-{uuid.uuid4().hex[:16]}"
    with _RADAR_LOCK, connect_db(config) as conn:
        conn.execute(
            """INSERT INTO radar_runs (id, kind, slot_key, schedule_date, status, started_at)
               VALUES (?, ?, ?, ?, 'running', ?)""",
            (run_id, kind, slot_key, schedule_date, utc_now()),
        )
    return run_id


def finish_radar_run(config: AppConfig, run_id: str, totals: dict[str, Any], detail: str = "") -> dict[str, Any]:
    failed = int(totals.get("searchesFailed") or 0)
    total = int(totals.get("searchesTotal") or 0)
    if total and failed == total:
        status = "failed"
    elif failed:
        status = "degraded"
    else:
        status = "completed"
    with _RADAR_LOCK, connect_db(config) as conn:
        conn.execute(
            """UPDATE radar_runs SET status = ?, finished_at = ?, searches_total = ?, searches_ok = ?,
                 searches_failed = ?, listings_matched = ?, listings_rejected = ?, detail = ?
               WHERE id = ?""",
            (
                status, utc_now(), total, int(totals.get("searchesOk") or 0), failed,
                int(totals.get("listingsMatched") or 0), int(totals.get("listingsRejected") or 0),
                detail, run_id,
            ),
        )
        row = conn.execute("SELECT * FROM radar_runs WHERE id = ?", (run_id,)).fetchone()
    return radar_run_row(row)


def record_radar_receipts(config: AppConfig, run_id: str, search_id: str, receipts: list[dict[str, Any]],
                          matched: int, rejected: int) -> None:
    if not run_id:
        return
    with _RADAR_LOCK, connect_db(config) as conn:
        for receipt in receipts:
            conn.execute(
                """INSERT INTO radar_run_receipts (
                     run_id, search_id, source_id, status, listing_count, matched_count, rejected_count,
                     error_count, errors_json, started_at, finished_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id, search_id, source_id) DO UPDATE SET
                     status=excluded.status, listing_count=excluded.listing_count,
                     matched_count=excluded.matched_count, rejected_count=excluded.rejected_count,
                     error_count=excluded.error_count, errors_json=excluded.errors_json,
                     finished_at=excluded.finished_at""",
                (
                    run_id, search_id, str(receipt.get("sourceId") or ""), str(receipt.get("status") or "failed"),
                    int(receipt.get("listingCount") or 0), matched, rejected, int(receipt.get("errorCount") or 0),
                    json.dumps(receipt.get("errors") or [], ensure_ascii=False),
                    str(receipt.get("startedAt") or utc_now()), str(receipt.get("finishedAt") or utc_now()),
                ),
            )


def claim_radar_slot(config: AppConfig, schedule_date: str, slot_key: str, run_id: str,
                     state: str = "fulfilled", detail: str = "") -> bool:
    """Reclama un slot de forma atómica. `False` si ya estaba tomado.

    Se reclama **antes** de escanear: un crash en medio de la corrida pierde ese
    slot, que es preferible a repetir el scan y las llamadas externas.
    """

    with _RADAR_LOCK, connect_db(config) as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO radar_schedule_slots
                 (schedule_date, slot_key, state, fulfilled_by_run_id, detail, claimed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (schedule_date, slot_key, state, run_id, detail, utc_now()),
        )
        return cursor.rowcount == 1


def latest_successful_radar_run(config: AppConfig) -> sqlite3.Row | None:
    with _RADAR_LOCK, connect_db(config) as conn:
        return conn.execute(
            """SELECT * FROM radar_runs WHERE kind = 'manual' AND status IN ('completed', 'degraded')
               ORDER BY finished_at DESC LIMIT 1"""
        ).fetchone()


def manual_run_satisfies_slot(config: AppConfig, slot_time: datetime) -> sqlite3.Row | None:
    """Una corrida manual reciente y exitosa consume el slot que viene.

    Misma regla que `AUCTION_WATCH_MANUAL_FRESHNESS_MINUTES`: no se vuelve a
    escanear lo mismo minutos después de que el usuario ya lo pidió.
    """

    if RADAR_MANUAL_FRESHNESS_MINUTES <= 0:
        return None
    row = latest_successful_radar_run(config)
    if row is None or not row["finished_at"]:
        return None
    finished_at = parse_iso_datetime(str(row["finished_at"]))
    if finished_at is None:
        return None
    window_start = slot_time - timedelta(minutes=RADAR_MANUAL_FRESHNESS_MINUTES)
    finished_local = finished_at.astimezone(slot_time.tzinfo)
    return row if window_start <= finished_local <= slot_time else None


def run_radar_searches(config: AppConfig, run_id: str, slot_key: str = "") -> dict[str, Any]:
    """Ejecuta las búsquedas activas que corresponden a esta corrida.

    Una búsqueda que falla no detiene a las demás: su falla queda en el recibo y
    en `last_error`, y el resto de la corrida sigue.
    """

    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            "SELECT * FROM radar_searches WHERE status = 'active' AND deleted_at IS NULL ORDER BY priority, name"
        ).fetchall()

    selected = [row for row in rows if not slot_key or slot_key in radar_search_slots(row)]
    totals = {
        "searchesTotal": len(selected),
        "searchesOk": 0,
        "searchesFailed": 0,
        "listingsMatched": 0,
        "listingsRejected": 0,
    }
    for row in selected:
        search_id = str(row["id"])
        try:
            result = run_radar_search(config, search_id, run_id=run_id)
        except ApiError as error:
            totals["searchesFailed"] += 1
            print(f"[consolas] Collection Radar error for {search_id}: {error.message}")
            continue
        totals["searchesOk"] += 1
        totals["listingsMatched"] += int(result.get("results") or 0)
        totals["listingsRejected"] += int(result.get("rejected") or 0)
    return totals


def run_due_radar_slots(config: AppConfig) -> list[dict[str, Any]]:
    """Un tick del scheduler: corre el slot vencido que falte, y sólo uno.

    Si el add-on estuvo apagado y hay varios slots vencidos, se ejecuta
    únicamente el más reciente; los anteriores se cierran como `skipped` con su
    motivo. Arrancar disparando tres scans seguidos sería ruido, no recuperación.
    """

    now = radar_now_local()
    schedule_date = now.date().isoformat()
    due = radar_due_slots(now)
    if not due:
        return []

    with _RADAR_LOCK, connect_db(config) as conn:
        taken = {
            str(row["slot_key"])
            for row in conn.execute(
                "SELECT slot_key FROM radar_schedule_slots WHERE schedule_date = ?", (schedule_date,)
            ).fetchall()
        }
    pending = [slot_key for slot_key in due if slot_key not in taken]
    if not pending:
        return []

    outcomes: list[dict[str, Any]] = []
    for slot_key in pending[:-1]:
        if claim_radar_slot(config, schedule_date, slot_key, "", "skipped", "Slot vencido mientras el add-on no corría"):
            outcomes.append({"slotKey": slot_key, "state": "skipped"})

    slot_key = pending[-1]
    slot_time = radar_slot_datetime(slot_key, now) or now
    fresh_manual = manual_run_satisfies_slot(config, slot_time)
    if fresh_manual is not None:
        if claim_radar_slot(
            config, schedule_date, slot_key, str(fresh_manual["id"]), "fulfilled",
            "Satisfecho por una corrida manual reciente",
        ):
            outcomes.append({"slotKey": slot_key, "state": "fulfilled", "runId": str(fresh_manual["id"])})
        return outcomes

    run_id = start_radar_run(config, "scheduled", slot_key, schedule_date)
    if not claim_radar_slot(config, schedule_date, slot_key, run_id):
        # Otro hilo ganó la carrera por el slot: esta corrida no existe.
        with _RADAR_LOCK, connect_db(config) as conn:
            conn.execute("DELETE FROM radar_runs WHERE id = ?", (run_id,))
        return outcomes

    totals = run_radar_searches(config, run_id, slot_key)
    run = finish_radar_run(config, run_id, totals)
    outcomes.append({"slotKey": slot_key, "state": "fulfilled", "runId": run_id, "status": run["status"]})
    return outcomes


def start_manual_radar_run(config: AppConfig) -> dict[str, Any]:
    """Encola un “Buscar ahora” global. Una solicitud repetida reutiliza la activa."""
    with _RADAR_RUN_LOCK:
        with _RADAR_LOCK, connect_db(config) as conn:
            running = conn.execute(
                "SELECT * FROM radar_runs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        if running is not None:
            return {"ok": True, "reused": True, "run": radar_run_row(running)}
        run_id = start_radar_run(config, "manual")

    def worker() -> None:
        try:
            totals = run_radar_searches(config, run_id)
            finish_radar_run(config, run_id, totals)
        except Exception as error:  # pragma: no cover - defensivo
            finish_radar_run(
                config, run_id,
                {"searchesTotal": 1, "searchesOk": 0, "searchesFailed": 1},
                detail=str(error),
            )

    threading.Thread(target=worker, name=f"radar-run-{run_id}", daemon=True).start()
    with _RADAR_LOCK, connect_db(config) as conn:
        row = conn.execute("SELECT * FROM radar_runs WHERE id = ?", (run_id,)).fetchone()
    return {"ok": True, "reused": False, "run": radar_run_row(row)}


def list_radar_runs(config: AppConfig, limit: int = RADAR_RUN_HISTORY_LIMIT) -> dict[str, Any]:
    capped = max(1, min(int(limit or RADAR_RUN_HISTORY_LIMIT), 100))
    now = radar_now_local()
    schedule_date = now.date().isoformat()
    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            "SELECT * FROM radar_runs ORDER BY started_at DESC LIMIT ?", (capped,)
        ).fetchall()
        runs: list[dict[str, Any]] = []
        for row in rows:
            receipts = conn.execute(
                "SELECT * FROM radar_run_receipts WHERE run_id = ? ORDER BY search_id, source_id",
                (str(row["id"]),),
            ).fetchall()
            runs.append(radar_run_row(row, [radar_receipt_row(receipt) for receipt in receipts]))
        today = conn.execute(
            "SELECT * FROM radar_schedule_slots WHERE schedule_date = ? ORDER BY slot_key", (schedule_date,)
        ).fetchall()
    slot_states = {str(row["slot_key"]): row for row in today}
    return {
        "version": RADAR_SEARCHES_VERSION,
        "timezone": RADAR_TIMEZONE_NAME,
        "now": now.isoformat(),
        "nextSlot": radar_next_slot(now),
        "slots": [
            {
                "slotKey": key,
                "label": RADAR_SLOT_LABELS[key],
                "state": str(slot_states[key]["state"]) if key in slot_states else "pending",
                "runId": str(slot_states[key]["fulfilled_by_run_id"]) if key in slot_states else "",
                "detail": str(slot_states[key]["detail"]) if key in slot_states else "",
            }
            for key in RADAR_SLOT_KEYS
        ],
        "current": next((run for run in runs if run["status"] == "running"), None),
        "runs": runs,
    }


def run_active_radar_searches(config: AppConfig) -> dict[str, Any]:
    """Corre todas las búsquedas aprobadas y activas, sin ligarlas a un slot.

    Un borrador espera consentimiento explícito y nunca entra acá.
    """
    return run_radar_searches(config, "")


def load_console_catalog(config: AppConfig) -> list[dict[str, Any]]:
    """Catálogo base de consolas. Es referencia: nunca decide propiedad."""
    path = config.static_dir / "data" / "consoles.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("consolas") if isinstance(payload, dict) else None
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def radar_master_proposals(config: AppConfig) -> list[dict[str, Any]]:
    return propose_master_searches(read_state(config), load_console_catalog(config))


def master_search_id(key: str) -> str:
    """Id estable por propuesta: regenerar no duplica ni pisa lo que ya existe."""
    return f"master-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def regenerate_radar_master(config: AppConfig) -> dict[str, Any]:
    """Escribe las propuestas del Master como borradores, sin activar ninguna.

    Es idempotente y conservador: una propuesta que el usuario ya tocó —la
    activó, la pausó, la archivó, la editó o la borró— no se vuelve a crear ni
    se sobrescribe. El Master sugiere una vez; la decisión queda del lado del
    usuario.
    """

    proposals = radar_master_proposals(config)
    now = utc_now()
    created: list[str] = []
    skipped: list[str] = []

    with _RADAR_LOCK, connect_db(config) as conn:
        for proposal in proposals:
            search_id = master_search_id(str(proposal["key"]))
            existing = conn.execute("SELECT status, deleted_at FROM radar_searches WHERE id = ?", (search_id,)).fetchone()
            if existing is not None:
                skipped.append(search_id)
                continue
            criteria = normalize_radar_criteria(proposal.get("criteria"))
            conn.execute(
                """INSERT INTO radar_searches (
                     id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
                     search_query, criteria_json, sources_json, slots_json, notes, created_at, updated_at
                   ) VALUES (?, ?, ?, 'draft', 'master', ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)""",
                (
                    search_id, str(proposal["name"]), str(proposal["searchType"]), str(proposal["priority"]),
                    str(proposal["platform"]), str(proposal["entityType"]), str(proposal["entityId"]),
                    build_radar_search_query(str(proposal["name"]), str(proposal["platform"]), criteria),
                    json.dumps(criteria, ensure_ascii=False, separators=(",", ":")),
                    json.dumps([RADAR_DEFAULT_SOURCE], ensure_ascii=False, separators=(",", ":")),
                    str(proposal["rationale"]), now, now,
                ),
            )
            created.append(search_id)

    return {
        "ok": True,
        "proposed": len(proposals),
        "created": len(created),
        "skipped": len(skipped),
        "proposals": proposals,
    }


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


class RadarSearchScheduler(threading.Thread):
    """Despierta seguido, escanea poco: sólo cuando vence un slot sin cumplir.

    Reemplaza al hilo que dormía 86.400 segundos desde el arranque, que perdía
    su horario en cada reinicio y no dejaba rastro de lo que había corrido.
    """

    def __init__(self, config: AppConfig) -> None:
        super().__init__(name="collection-radar", daemon=True)
        self.config = config

    def run(self) -> None:
        while True:
            try:
                for outcome in run_due_radar_slots(self.config):
                    print(f"[consolas] Collection Radar slot {outcome['slotKey']}: {outcome['state']}")
            except Exception as error:  # el scheduler no puede morirse por una corrida
                print(f"[consolas] Collection Radar scheduler error: {error}")
            threading.Event().wait(RADAR_SCHEDULER_INTERVAL_SECONDS)


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
        if path == "/api/radar/searches":
            self.send_json(list_radar_searches(config))
            return
        if path == "/api/radar/master":
            self.send_json({"ok": True, "proposals": radar_master_proposals(config)})
            return
        if path == "/api/radar/runs":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            limit = (query.get("limit") or [str(RADAR_RUN_HISTORY_LIMIT)])[0]
            self.send_json(
                list_radar_runs(config, int(limit) if limit.isdigit() else RADAR_RUN_HISTORY_LIMIT)
            )
            return
        if path == "/api/radar/listings":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            limit = (query.get("limit") or ["100"])[0]
            self.send_json(list_radar_listings(config, int(limit) if limit.isdigit() else 100))
            return
        if path.startswith("/api/radar/searches/"):
            search_id = normalize_radar_id(path.removeprefix("/api/radar/searches/"))
            self.send_json(radar_search_payload(config, search_id))
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
        if path == "/api/radar/searches":
            require_radar_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(create_radar_search(self.config(), payload), HTTPStatus.CREATED)
            return
        if path == "/api/radar/master/regenerate":
            require_radar_write_request(self)
            self.send_json(regenerate_radar_master(self.config()), HTTPStatus.CREATED)
            return
        if path == "/api/radar/run-now":
            require_radar_write_request(self)
            self.send_json(start_manual_radar_run(self.config()), HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/radar/searches/"):
            require_radar_write_request(self)
            payload = read_json_body(self, self.config())
            parts = path.removeprefix("/api/radar/searches/").split("/")
            if len(parts) == 1:
                self.send_json(update_radar_search(self.config(), parts[0], payload))
                return
            if len(parts) != 2:
                raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            search_id, action = parts
            if action == "run":
                self.send_json(run_radar_search(self.config(), search_id))
                return
            if action == "status":
                self.send_json(set_radar_search_status(self.config(), search_id, payload.get("status")))
                return
            if action == "duplicate":
                self.send_json(duplicate_radar_search(self.config(), search_id), HTTPStatus.CREATED)
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
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
        if parsed.path.startswith("/api/radar/searches/"):
            require_radar_write_request(self)
            search_id = normalize_radar_id(parsed.path.removeprefix("/api/radar/searches/"))
            self.send_json(delete_radar_search(self.config(), search_id))
            return
        if parsed.path.startswith("/api/chasing-games/"):
            require_chasing_games_write_request(self)
            chase_id = parsed.path.removeprefix("/api/chasing-games/")
            self.send_json(delete_chasing_game(self.config(), chase_id))
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
    RadarSearchScheduler(config).start()
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
