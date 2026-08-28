#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import html
import json
import mimetypes
import os
import shutil
import smtplib
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from auction_search_config import (
    HISTORICAL_BAVASTRO_QUERY,
    SHARED_KEYWORDS,
    collect_flags,
    compile_patterns,
    matched_terms,
    normalize_text,
    score_match,
)


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from runtime_paths import bootstrap_runtime, resolve_runtime_paths  # noqa: E402


RUNTIME_PATHS = resolve_runtime_paths(AGENT_DIR)
RUNTIME_ROOT = RUNTIME_PATHS.root
RUNS_DIR = RUNTIME_PATHS.runs
LATEST_DIR = RUNTIME_PATHS.latest
LATEST_MATCHES_DIR = RUNTIME_PATHS.latest_matches
STATE_FILE = RUNTIME_PATHS.state
DELIVERY_OUTBOX_FILE = RUNTIME_PATHS.delivery_outbox
RUN_LOCK_FILE = RUNTIME_PATHS.run_lock
WATCHLIST_FILE = RUNTIME_PATHS.watchlist
DISMISSALS_CACHE_FILE = RUNTIME_PATHS.dismissals_cache
WEB_EXPORT_SCRIPT = AGENT_DIR / "scripts" / "export_web_snapshot.py"
EXTRA_SOURCES_SCRIPT = AGENT_DIR / "scripts" / "scan_extra_sources.py"
# A development checkout has a virtual environment at the repository root.
# The Home Assistant image deliberately does not: it uses the system Python
# provisioned by its Dockerfile.  Prefer the explicit runtime when supplied,
# then the checkout venv, and finally the interpreter running this process.
_checkout_python = REPO_ROOT / ".venv" / "bin" / "python"
PYTHON_BIN = Path(os.environ.get("AUCTION_WATCH_PYTHON") or (
    str(_checkout_python) if _checkout_python.exists() else sys.executable
))
NOTIFICATION_ENV_FILE = AGENT_DIR / "notification.env"
WEB_BAVASTRO_BASE = "https://www.bavastronline.com.uy/auctions"

BAVASTRO_DISCOVERY_SCRIPT = REPO_ROOT / "buscador_bavastro.py"
BAVASTRO_MATCHES_SCRIPT = REPO_ROOT / "buscador_consolas_bavastro.py"
CASTELLS_DISCOVERY_SCRIPT = REPO_ROOT / "buscador_consolas_castells.py"
CASTELLS_MATCHES_SCRIPT = REPO_ROOT / "buscador_consolas_castells.py"
EXTRA_MATCHES_FILENAME = "consolas_extra_matches.csv"
EXTRA_STATUS_FILENAME = "extra_sources_status.json"
RUN_SNAPSHOT_FILENAME = "auction-watch.json"
DELIVERY_MANIFEST_FILENAME = "delivery.json"
STATE_SCHEMA_VERSION = 4
DELIVERY_OUTBOX_VERSION = 1
DELIVERY_BACKOFF_SECONDS = (60, 300, 900, 3600)
PUBLICATION_MODES = {"ha-required", "local-only"}
MATCH_PATTERNS = compile_patterns(SHARED_KEYWORDS)
SOURCE_LABELS = {
    "bavastro": "Bavastro",
    "castells": "Castells",
    "remotes": "Remotes",
    "todoremates": "TodoRemates",
    "prado": "Prado Subastas",
}
WEEKDAY_NAMES_ES = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
MONTH_NAMES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


@dataclass
class StepResult:
    name: str
    command: list[str]
    stdout_path: str
    stderr_path: str
    exit_code: int | None
    status: str
    started_at: str
    finished_at: str
    skipped_reason: str = ""
    inventory_authoritative: bool = False
    receipts: list[dict[str, object]] = field(default_factory=list)


@dataclass
class NotificationResult:
    channel: str
    enabled: bool
    attempted: bool
    sent: bool
    detail: str


@dataclass
class AgentState:
    processed_bavastro_auction_ids: set[int] = field(default_factory=set)
    processed_castells_remate_ids: set[int] = field(default_factory=set)
    active_bavastro_matches_by_group: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    active_castells_matches_by_group: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    active_extra_matches_by_source: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    opportunity_lifecycle: dict[str, dict[str, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicationResult:
    mode: str
    status: str
    configured: bool
    attempted: bool
    detail: str
    run_id: str = ""
    snapshot_hash: str = ""
    generated_at: str = ""
    canonical_verified: bool = False
    canonical_snapshot: dict[str, object] | None = None

    @property
    def published(self) -> bool:
        return self.status == "published"


@dataclass
class WatchHit:
    watch_id: str
    label: str
    source: str
    lot_id: str
    group_id: str
    lot_label: str
    group_label: str
    description: str
    lot_url: str
    group_url: str
    closing_at_iso: str
    closing_at_display: str
    remaining_text: str
    urgency_label: str
    matched_keywords: str
    price_label: str
    image_url: str = ""
    notes: str = ""
    priority: int = 100
    remaining_seconds: int | None = None


@dataclass(frozen=True)
class MatchView:
    source_id: str
    source_label: str
    lot_id: str
    group_id: str
    title: str
    description: str
    lot_url: str
    group_url: str
    image_url: str
    score: int
    matched_keywords: str
    risk_flags: str
    positive_flags: str
    price_label: str
    timing_label: str
    closing_at_raw: str
    closing_at_display: str


@dataclass(frozen=True)
class DismissalState:
    keys: frozenset[tuple[str, str]]
    items: tuple[dict[str, str], ...]
    source: str
    detail: str = ""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_run_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def sanitize_run_id(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
    return cleaned or default_run_id()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ejecuta el agente diario de remates para Bavastro y Castells."
    )
    parser.add_argument("--run-id", help="Identificador manual de corrida.")
    parser.add_argument(
        "--bavastro-discovery-mode",
        choices=["active", "historical"],
        default="active",
        help="`active` para remates activos de hoy; `historical` para barrido por query.",
    )
    parser.add_argument("--bavastro-query", default=HISTORICAL_BAVASTRO_QUERY)
    parser.add_argument("--bavastro-window", type=int, default=200)
    parser.add_argument("--bavastro-headroom", type=int, default=40)
    parser.add_argument("--castells-limit", type=int, default=9999)
    parser.add_argument("--keep-runs", type=int, default=30)
    parser.add_argument(
        "--deliver-run",
        help="Reintenta publicacion/mail de un run existente sin volver a escanear.",
    )
    parser.add_argument(
        "--force-uncertain-email-retry",
        action="store_true",
        help=(
            "Reenvía explícitamente un mail cuyo resultado quedó ambiguo. "
            "Puede duplicar un mensaje ya aceptado por el proveedor."
        ),
    )
    parser.add_argument("--schedule-date", help="Fecha local YYYY-MM-DD asociada al run.")
    parser.add_argument(
        "--schedule-slots",
        default="",
        help="Slots separados por coma que este run puede cumplir al entregarse.",
    )
    parser.add_argument("--manual-request-id", default="")
    parser.add_argument(
        "--refresh-active-matches",
        action="store_true",
        help="Reprocesa todos los remates/subastas activas aunque ya esten en state.json.",
    )
    return parser.parse_args()


def ensure_runtime() -> None:
    if not PYTHON_BIN.exists():
        raise FileNotFoundError(f"No se encontro el runtime esperado: {PYTHON_BIN}")
    bootstrap_runtime(AGENT_DIR)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def csv_has_field(path: Path, field: str) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return field in (csv.DictReader(handle).fieldnames or [])
    except OSError:
        return False


def read_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: object) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def canonical_snapshot_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def snapshot_payload_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()


def parse_iso_datetime(raw: object) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def publication_mode(config: dict[str, str]) -> str:
    configured = config.get("AUCTION_WATCH_PUBLICATION_MODE", "").strip().lower()
    if configured:
        return configured
    if config.get("AUCTION_WATCH_SNAPSHOT_URL", "").strip() or config.get(
        "AUCTION_WATCH_APP_BASE_URL", ""
    ).strip():
        return "ha-required"
    return "local-only"


def effective_app_base_url(config: dict[str, str], mode: str) -> str:
    if mode != "ha-required":
        return ""
    return config.get("AUCTION_WATCH_APP_BASE_URL", "").strip()


def normalize_dismissal_item(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    source_id = str(raw.get("sourceId") or raw.get("source_id") or "").strip().lower()
    lot_id = str(raw.get("lotId") or raw.get("lot_id") or "").strip()
    if not source_id or not lot_id:
        return None
    return {
        "sourceId": source_id,
        "lotId": lot_id,
        "groupId": str(raw.get("groupId") or raw.get("group_id") or "").strip(),
        "title": str(raw.get("title") or "").strip(),
        "lotUrl": str(raw.get("lotUrl") or raw.get("lot_url") or "").strip(),
        "dismissedAt": str(raw.get("dismissedAt") or raw.get("dismissed_at") or "").strip(),
    }


def parse_dismissal_payload(
    payload: object,
    *,
    source: str,
    detail: str = "",
    require_schema: bool = False,
) -> DismissalState:
    if require_schema:
        if not isinstance(payload, dict):
            raise ValueError("dismissals payload must be an object")
        if payload.get("version") != 1:
            raise ValueError("dismissals payload has an unsupported version")
        if not isinstance(payload.get("items"), list):
            raise ValueError("dismissals payload items must be a list")
    raw_items = payload.get("items") if isinstance(payload, dict) else []
    items_by_key: dict[tuple[str, str], dict[str, str]] = {}
    if isinstance(raw_items, list):
        for raw in raw_items:
            item = normalize_dismissal_item(raw)
            if item is None:
                continue
            items_by_key[(item["sourceId"], item["lotId"])] = item
    items = tuple(items_by_key.values())
    return DismissalState(
        keys=frozenset(items_by_key),
        items=items,
        source=source,
        detail=detail,
    )


def write_dismissals_cache(path: Path, items: tuple[dict[str, str], ...]) -> None:
    payload = {"version": 1, "updatedAt": now_iso(), "items": list(items)}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def dismissals_endpoint(config: dict[str, str]) -> str:
    explicit = config.get("AUCTION_WATCH_DISMISSALS_URL", "").strip()
    if explicit:
        return explicit
    app_base_url = config.get("AUCTION_WATCH_APP_BASE_URL", "").strip().rstrip("/")
    return f"{app_base_url}/api/auction-watch/dismissals" if app_base_url else ""


def load_dismissals(
    config: dict[str, str],
    cache_path: Path = DISMISSALS_CACHE_FILE,
) -> DismissalState:
    endpoint = dismissals_endpoint(config)
    if endpoint:
        try:
            request = Request(
                endpoint,
                headers={"Accept": "application/json", "User-Agent": "AuctionWatch/1.0"},
            )
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            state = parse_dismissal_payload(
                payload,
                source="remote",
                detail=endpoint,
                require_schema=True,
            )
            write_dismissals_cache(cache_path, state.items)
            return state
        except Exception as exc:  # keep the last known decisions when HA is unavailable
            remote_error = f"{type(exc).__name__}: {exc}"
    else:
        remote_error = "dismissals endpoint not configured"

    cached = parse_dismissal_payload(read_json_object(cache_path), source="cache", detail=remote_error)
    if cached.items:
        return cached
    return DismissalState(frozenset(), tuple(), "none", remote_error)


def unique_count(rows: list[dict[str, str]], key: str) -> int:
    values = {(row.get(key) or "").strip() for row in rows}
    values.discard("")
    return len(values)


def extract_int_ids(rows: list[dict[str, str]], key: str) -> list[int]:
    ids: set[int] = set()
    for row in rows:
        raw = str(row.get(key) or "").strip()
        if raw.isdigit():
            ids.add(int(raw))
    return sorted(ids)


def load_state(path: Path) -> AgentState:
    if not path.exists():
        return AgentState()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AgentState()

    # Version changes intentionally invalidate only this operational cache so
    # new relevance rules are applied to every currently active legacy group.
    if payload.get("version") != STATE_SCHEMA_VERSION:
        return AgentState()

    def parse_id_set(values: object) -> set[int]:
        parsed: set[int] = set()
        if not isinstance(values, list):
            return parsed
        for value in values:
            raw = str(value).strip()
            if raw.isdigit():
                parsed.add(int(raw))
        return parsed

    def parse_match_groups(values: object) -> dict[str, list[dict[str, str]]]:
        if not isinstance(values, dict):
            return {}
        parsed: dict[str, list[dict[str, str]]] = {}
        for raw_group_id, raw_rows in values.items():
            group_id = str(raw_group_id or "").strip()
            if not group_id or not isinstance(raw_rows, list):
                continue
            rows = [
                {str(key): str(value or "") for key, value in row.items()}
                for row in raw_rows
                if isinstance(row, dict)
            ]
            parsed[group_id] = rows
        return parsed

    return AgentState(
        processed_bavastro_auction_ids=parse_id_set(payload.get("processed_bavastro_auction_ids")),
        processed_castells_remate_ids=parse_id_set(payload.get("processed_castells_remate_ids")),
        active_bavastro_matches_by_group=parse_match_groups(
            payload.get("active_bavastro_matches_by_group")
        ),
        active_castells_matches_by_group=parse_match_groups(
            payload.get("active_castells_matches_by_group")
        ),
        active_extra_matches_by_source=parse_match_groups(
            payload.get("active_extra_matches_by_source")
        ),
        opportunity_lifecycle=(
            {
                str(key): {
                    str(field): value
                    for field, value in value.items()
                    if isinstance(field, str)
                }
                for key, value in (payload.get("opportunity_lifecycle") or {}).items()
                if isinstance(value, dict)
            }
            if isinstance(payload.get("opportunity_lifecycle"), dict)
            else {}
        ),
    )


def save_state(path: Path, state: AgentState) -> None:
    payload = {
        "version": STATE_SCHEMA_VERSION,
        "updated_at": now_iso(),
        "processed_bavastro_auction_ids": sorted(state.processed_bavastro_auction_ids),
        "processed_castells_remate_ids": sorted(state.processed_castells_remate_ids),
        "active_bavastro_matches_by_group": state.active_bavastro_matches_by_group,
        "active_castells_matches_by_group": state.active_castells_matches_by_group,
        "active_extra_matches_by_source": state.active_extra_matches_by_source,
        "opportunity_lifecycle": state.opportunity_lifecycle,
    }
    atomic_write_json(path, payload)


def load_file_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_notification_config() -> dict[str, str]:
    config = load_file_env(NOTIFICATION_ENV_FILE)
    for key, value in os.environ.items():
        if key.startswith("AUCTION_WATCH_"):
            config[key] = value
    return config


def load_watchlist(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, list):
        return []

    items: list[dict[str, object]] = []
    for entry in payload:
        if isinstance(entry, dict):
            items.append(entry)
    return items


def now_local_dt() -> datetime:
    return datetime.now().astimezone()


def parse_source_datetime(raw: str, tzinfo) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tzinfo)
    return parsed.astimezone(tzinfo)


def format_datetime_es(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    weekday = WEEKDAY_NAMES_ES[dt.weekday()]
    month = MONTH_NAMES_ES[dt.month - 1]
    return f"{weekday} {dt.day:02d} {month} {dt.hour:02d}:{dt.minute:02d}"


def format_remaining(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds <= 0:
        return "cerrado"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def watch_urgency_label(seconds: int | None) -> str:
    if seconds is None:
        return "seguimiento"
    if seconds <= 0:
        return "cerrado"
    if seconds <= 6 * 3600:
        return "cierre inminente"
    if seconds <= 24 * 3600:
        return "cierra hoy"
    if seconds <= 72 * 3600:
        return "cierra pronto"
    return "seguimiento"


def format_money(prefix: str, amount: float) -> str:
    currency = (prefix or "").strip() or "$"
    return f"{currency}{amount:,.2f}"


def display_source_name(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized in SOURCE_LABELS:
        return SOURCE_LABELS[normalized]
    return normalized.replace("_", " ").replace("-", " ").strip().title() or "Auction Watch"


def currency_prefix(raw: str) -> str:
    normalized = str(raw or "").strip().upper()
    if normalized in {"UYU", "$UY", "$", "PESOS URUGUAYOS"}:
        return "$"
    if normalized in {"USD", "US$", "$USD"}:
        return "USD "
    return f"{str(raw or '').strip()} " if str(raw or "").strip() else "$"


def extra_price_label(row: dict[str, str]) -> str:
    candidates = [
        ("Proxima c/cargos", "total_next_bid_with_commission"),
        ("Actual c/cargos", "total_current_with_commission"),
        ("Base c/cargos", "total_base_with_commission"),
    ]
    for label, key in candidates:
        amount = row_float(row, key)
        if amount > 0:
            return f"{label}: {format_money(currency_prefix(row.get('currency', '')), amount)}"

    basis = row_float(row, "next_bid") or row_float(row, "current_price") or row_float(row, "base_price")
    if basis <= 0:
        return "Sin precio visible"
    commission = max(0.0, row_float(row, "commission_percent"))
    packaging = max(0.0, row_float(row, "packaging_cost"))
    total = basis * (1 + commission / 100) + packaging
    return f"Estimado c/cargos: {format_money(currency_prefix(row.get('currency', '')), total)}"


def match_view_from_row(source: str, row: dict[str, str]) -> MatchView:
    source_id = str(source or row.get("source_id") or "").strip().lower()
    source_label = str(row.get("source_label") or display_source_name(source_id)).strip()

    if source_id == "bavastro":
        title = str(row.get("description") or row.get("name") or "").strip()
        closing_raw = str(row.get("auction_end_date") or row.get("end_date") or "").strip()
        price_label = f"Monto actual: {format_money(str(row.get('currency_prefix') or '$'), row_float(row, 'final_amount'))}"
        lot_url = str(row.get("lot_web_url") or "").strip()
        group_url = str(row.get("auction_url") or "").strip()
        lot_id = str(row.get("lot_auction_id") or row.get("lot_id") or "").strip()
        group_id = str(row.get("auction_id") or "").strip()
        timing_label = "cierra"
    elif source_id == "castells":
        title = str(row.get("lot_description") or "").strip()
        closing_raw = str(row.get("closing_at") or "").strip()
        price_label = (
            "Proxima puja c/comision: "
            f"{format_money(str(row.get('currency') or '$'), row_float(row, 'next_bid_with_commission'))}"
        )
        lot_url = str(row.get("lot_url") or "").strip()
        group_url = str(row.get("remate_url") or "").strip()
        lot_id = str(row.get("lot_id") or "").strip()
        group_id = str(row.get("remate_id") or "").strip()
        timing_label = "cierra"
    else:
        title = str(row.get("title") or row.get("description") or "").strip()
        description = str(row.get("description") or "").strip()
        if (
            normalize_text(title).startswith("lote ")
            and len(title.split()) <= 3
            and description
            and normalize_text(description) != normalize_text(title)
        ):
            title = f"{title} · {description}"
        closing_raw = str(row.get("closing_at") or row.get("event_at") or "").strip()
        price_label = extra_price_label(row)
        lot_url = str(row.get("lot_url") or "").strip()
        group_url = str(row.get("group_url") or "").strip()
        lot_id = str(row.get("lot_id") or "").strip()
        group_id = str(row.get("group_id") or "").strip()
        timing_label = "cierra" if str(row.get("closing_at") or "").strip() else "remate"

    closing_dt = parse_source_datetime(closing_raw, now_local_dt().tzinfo)
    return MatchView(
        source_id=source_id,
        source_label=source_label or display_source_name(source_id),
        lot_id=lot_id,
        group_id=group_id,
        title=title or "Oportunidad activa",
        description=str(row.get("description") or title).strip(),
        lot_url=lot_url,
        group_url=group_url,
        image_url=str(row.get("image_url") or "").strip(),
        score=row_int(row, "score"),
        matched_keywords=str(row.get("matched_keywords") or "").strip(),
        risk_flags=str(row.get("risk_flags") or "").strip(),
        positive_flags=str(row.get("positive_flags") or "").strip(),
        price_label=price_label,
        timing_label=timing_label,
        closing_at_raw=closing_raw,
        closing_at_display=format_datetime_es(closing_dt) if closing_dt else (closing_raw or "-"),
    )


def build_match_views(
    bavastro_rows: list[dict[str, str]],
    castells_rows: list[dict[str, str]],
    extra_rows: list[dict[str, str]],
) -> list[MatchView]:
    views: list[MatchView] = []
    seen: set[str] = set()
    source_rows = [
        ("bavastro", bavastro_rows),
        ("castells", castells_rows),
        ("", extra_rows),
    ]
    for source, rows in source_rows:
        for row in rows:
            view = match_view_from_row(source or str(row.get("source_id") or ""), row)
            unique_key = f"{view.source_id}:{view.lot_id or view.lot_url}"
            if not view.lot_id and not view.lot_url:
                unique_key = f"{view.source_id}:{view.group_id}:{normalize_text(view.title)}"
            if unique_key in seen:
                continue
            seen.add(unique_key)
            views.append(view)
    return views


def group_match_views(match_views: list[MatchView]) -> list[tuple[str, list[MatchView]]]:
    order: list[str] = []
    grouped: dict[str, list[MatchView]] = {}
    for item in match_views:
        if item.source_id not in grouped:
            order.append(item.source_id)
            grouped[item.source_id] = []
        grouped[item.source_id].append(item)
    return [(source_id, grouped[source_id]) for source_id in order]


def closing_day_label(day, reference_day) -> str:
    weekday = WEEKDAY_NAMES_ES[day.weekday()]
    month = MONTH_NAMES_ES[day.month - 1]
    readable = f"{weekday} {day.day:02d} {month}"
    if day == reference_day:
        return f"Cierra hoy — {readable}"
    if (day - reference_day).days == 1:
        return f"Cierra mañana — {readable}"
    return f"Cierra {readable}"


def group_match_views_by_closing_day(
    match_views: list[MatchView],
    *,
    reference: datetime | None = None,
) -> list[tuple[str, list[MatchView]]]:
    now = reference or now_local_dt()
    timezone_info = now.tzinfo
    dated: dict[object, list[MatchView]] = {}
    undated: list[MatchView] = []
    for item in match_views:
        closing = parse_source_datetime(item.closing_at_raw, timezone_info)
        if closing is None:
            undated.append(item)
            continue
        dated.setdefault(closing.date(), []).append(item)

    groups = [
        (closing_day_label(day, now.date()), dated[day])
        for day in sorted(dated)
    ]
    if undated:
        groups.append(("Sin fecha de cierre confirmada", undated))
    return groups


def match_view_dismissal_key(item: MatchView) -> tuple[str, str] | None:
    source_id = str(item.source_id or "").strip().lower()
    lot_id = str(item.lot_id or "").strip()
    return (source_id, lot_id) if source_id and lot_id else None


def watch_hit_dismissal_key(item: WatchHit) -> tuple[str, str] | None:
    source_id = str(item.source or "").strip().lower()
    lot_id = str(item.lot_id or "").strip()
    return (source_id, lot_id) if source_id and lot_id else None


def match_view_for_watch_hit(
    match_views: list[MatchView],
    hit: WatchHit | None,
) -> MatchView | None:
    if hit is None:
        return None
    key = watch_hit_dismissal_key(hit)
    if key is None:
        return None
    return next((item for item in match_views if match_view_dismissal_key(item) == key), None)


def filter_dismissed_match_views(
    match_views: list[MatchView],
    dismissed_keys: frozenset[tuple[str, str]] | set[tuple[str, str]],
) -> tuple[list[MatchView], list[MatchView]]:
    visible: list[MatchView] = []
    dismissed: list[MatchView] = []
    for item in match_views:
        key = match_view_dismissal_key(item)
        if key is not None and key in dismissed_keys:
            dismissed.append(item)
        else:
            visible.append(item)
    return visible, dismissed


def discard_action_url(app_base_url: str, item: MatchView) -> str:
    base_url = str(app_base_url or "").strip().rstrip("/")
    key = match_view_dismissal_key(item)
    if not base_url or key is None:
        return ""
    query = urlencode(
        {
            "source": key[0],
            "lot": key[1],
        }
    )
    return f"{base_url}/auction-watch-action.html?{query}"


def total_match_count(counts: dict[str, object]) -> int:
    raw = counts.get("total_matches")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return int(counts.get("bavastro_matches") or 0) + int(counts.get("castells_matches") or 0)


def watch_item_matches_row(source: str, item: dict[str, object], row: dict[str, str]) -> bool:
    item_source = str(item.get("source") or "").strip().lower()
    if item_source and item_source != source:
        return False

    description_key = "description" if source == "bavastro" else "lot_description"
    row_description = normalize_text(str(row.get(description_key) or ""))
    row_lot_id = str(
        (row.get("lot_auction_id") or "") if source == "bavastro" else (row.get("lot_id") or "")
    ).strip()
    row_lot_url = str(
        (row.get("lot_web_url") or "") if source == "bavastro" else (row.get("lot_url") or "")
    ).strip()

    checks: list[bool] = []

    item_lot_id = str(item.get("lot_id") or "").strip()
    if item_lot_id:
        checks.append(item_lot_id == row_lot_id)

    item_lot_url = str(item.get("lot_url") or "").strip()
    if item_lot_url:
        checks.append(item_lot_url == row_lot_url)

    description_contains = str(item.get("description_contains") or "").strip()
    if description_contains:
        checks.append(normalize_text(description_contains) in row_description)

    return bool(checks) and any(checks)


def build_watch_hit(
    source: str,
    item: dict[str, object],
    row: dict[str, str],
    now: datetime,
) -> WatchHit:
    if source == "bavastro":
        closing_dt = parse_source_datetime(str(row.get("auction_end_date") or ""), now.tzinfo)
        closing_at_iso = str(row.get("auction_end_date") or "")
        price_label = f"Monto actual: {format_money(str(row.get('currency_prefix') or '$'), row_float(row, 'final_amount'))}"
        lot_label = f"Lote {row.get('lot_number', '-')}"
        group_label = f"Subasta {row.get('auction_id', '-')}"
        lot_url = str(row.get("lot_web_url") or "")
        group_url = str(row.get("auction_url") or "")
        description = str(row.get("description") or "")
        lot_id = str(row.get("lot_auction_id") or row.get("lot_id") or "").strip()
        group_id = str(row.get("auction_id") or "").strip()
    else:
        closing_dt = parse_source_datetime(str(row.get("closing_at") or ""), now.tzinfo)
        closing_at_iso = str(row.get("closing_at") or "")
        price_label = (
            f"Proxima puja c/comision: {format_money(str(row.get('currency') or '$'), row_float(row, 'next_bid_with_commission'))}"
        )
        lot_label = f"Lote {row.get('lot_number', '-')}"
        group_label = f"Remate {row.get('remate_id', '-')}"
        lot_url = str(row.get("lot_url") or "")
        group_url = str(row.get("remate_url") or "")
        description = str(row.get("lot_description") or "")
        lot_id = str(row.get("lot_id") or "").strip()
        group_id = str(row.get("remate_id") or "").strip()

    remaining_seconds = int((closing_dt - now).total_seconds()) if closing_dt else None
    label = str(item.get("label") or "").strip() or shorten_text(description, 72)

    return WatchHit(
        watch_id=str(item.get("id") or label),
        label=label,
        source=source,
        lot_id=lot_id,
        group_id=group_id,
        lot_label=lot_label,
        group_label=group_label,
        description=description,
        lot_url=lot_url,
        group_url=group_url,
        closing_at_iso=closing_at_iso,
        closing_at_display=format_datetime_es(closing_dt),
        remaining_text=format_remaining(remaining_seconds),
        urgency_label=watch_urgency_label(remaining_seconds),
        matched_keywords=str(row.get("matched_keywords") or ""),
        price_label=price_label,
        image_url=str(row.get("image_url") or ""),
        notes=str(item.get("notes") or "").strip(),
        priority=int(item.get("priority") or 100),
        remaining_seconds=remaining_seconds,
    )


def collect_watch_hits(
    watchlist: list[dict[str, object]],
    bavastro_match_rows: list[dict[str, str]],
    castells_rows: list[dict[str, str]],
    now: datetime,
) -> list[WatchHit]:
    hits: list[WatchHit] = []

    for item in watchlist:
        source = str(item.get("source") or "").strip().lower()
        if source == "bavastro":
            rows = bavastro_match_rows
        elif source == "castells":
            rows = castells_rows
        else:
            continue

        for row in rows:
            if watch_item_matches_row(source, item, row):
                hits.append(build_watch_hit(source, item, row, now))
                break

    return sorted(
        hits,
        key=lambda hit: (
            hit.priority,
            hit.remaining_seconds if hit.remaining_seconds is not None else 10**12,
            hit.label.lower(),
        ),
    )


def watchlist_refresh_group_ids(
    source: str,
    watchlist: list[dict[str, object]],
    active_ids: list[int],
    cached_rows: list[dict[str, str]] | None = None,
) -> list[int]:
    source_watchlist = [
        item for item in watchlist if str(item.get("source") or "").strip().lower() == source
    ]
    if not source_watchlist or not active_ids:
        return []

    cached_rows = cached_rows or []
    group_key = match_group_key(source)
    watched_ids: set[int] = set()
    for row in cached_rows:
        for item in source_watchlist:
            if watch_item_matches_row(source, item, row):
                group_id_raw = str(row.get(group_key) or "").strip()
                if group_id_raw.isdigit():
                    watched_ids.add(int(group_id_raw))
                break
    return sorted(watched_ids)


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def markdown_link(label: str, target: str) -> str:
    target = (target or "").strip()
    if not target:
        return "-"
    return f"[{label}]({target})"


def markdown_file_link(label: str, path: Path) -> str:
    return markdown_link(label, str(path.resolve()))


def escape_table_cell(value: object) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text.replace("|", "\\|")


def shorten_text(value: object, max_chars: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def bavastro_public_auction_url(auction_id: str, fallback_url: str) -> str:
    auction_id = (auction_id or "").strip()
    if auction_id.isdigit():
        return f"{WEB_BAVASTRO_BASE}/{auction_id}"
    return fallback_url


def list_run_dirs() -> list[Path]:
    run_dirs = [
        path
        for path in RUNS_DIR.iterdir()
        if path.is_dir()
        and path.name not in {"latest", "latest-matches", ".latest.tmp", ".latest-matches.tmp"}
    ]
    run_dirs.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return run_dirs


def match_csv_filename(source: str) -> str:
    if source == "bavastro":
        return "consolas_bavastro_matches.csv"
    return "consolas_castells_matches.csv"


def match_group_key(source: str) -> str:
    if source == "bavastro":
        return "auction_id"
    return "remate_id"


def match_incremental_key(source: str) -> str:
    if source == "bavastro":
        return "new_bavastro_auction_ids"
    return "new_castells_remate_ids"


def match_unique_key(source: str, row: dict[str, str]) -> str:
    if source == "bavastro":
        return (
            str(row.get("lot_auction_id") or "").strip()
            or str(row.get("lot_web_url") or "").strip()
            or f"{row.get('auction_id', '')}:{row.get('lot_number', '')}"
        )
    return (
        str(row.get("lot_id") or "").strip()
        or str(row.get("lot_url") or "").strip()
        or f"{row.get('remate_id', '')}:{row.get('lot_number', '')}"
    )


def opportunity_lifecycle_key(source: str, row: dict[str, str]) -> str:
    if source == "bavastro":
        lot_id = str(row.get("lot_auction_id") or row.get("lot_id") or "").strip()
    else:
        lot_id = str(
            row.get("lot_id") or row.get("lot_url") or row.get("lot_web_url") or ""
        ).strip()
    return f"{source.lower().strip()}\x1f{lot_id}"


def opportunity_lifecycle_key_from_public_item(item: dict[str, object]) -> str:
    source = str(item.get("source") or "").strip().lower()
    lot_id = str(item.get("lotId") or "").strip()
    return f"{source}\x1f{lot_id}"


def lifecycle_group_id(source: str, row: dict[str, str]) -> str:
    normalized_source = source.strip().lower()
    if normalized_source == "bavastro":
        return str(row.get("auction_id") or "").strip()
    if normalized_source == "castells":
        return str(row.get("remate_id") or "").strip()
    return str(row.get("group_id") or "").strip()


def update_opportunity_lifecycle(
    state: AgentState,
    run_id: str,
    observed_at: str,
    source_rows: dict[str, list[dict[str, str]]],
    authoritative_groups: dict[str, set[str]],
    prior_source_rows: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, set[str]]:
    """Record first/last sightings without letting incremental state limit scans."""

    previously_active = {
        key
        for key, value in state.opportunity_lifecycle.items()
        if value.get("active") is True
    }
    known_group_ids: dict[str, str] = {}
    prior_sources = set(prior_source_rows or {})
    prior_sources.update(state.active_extra_matches_by_source)
    prior_sources.update({"bavastro", "castells"})
    for source in prior_sources:
        if source == "bavastro" or source == "castells":
            cached_rows = active_match_rows_for_source(state, source)
        else:
            cached_rows = state.active_extra_matches_by_source.get(source, [])
        rows = [*(prior_source_rows or {}).get(source, []), *cached_rows]
        for row in rows:
            key = opportunity_lifecycle_key(source, row)
            previously_active.add(key)
            group_id = lifecycle_group_id(source, row)
            if group_id:
                known_group_ids[key] = group_id
    for key in previously_active:
        if key in state.opportunity_lifecycle:
            continue
        source, separator, lot_id = key.partition("\x1f")
        if not separator or not source or not lot_id:
            continue
        state.opportunity_lifecycle[key] = {
            "sourceId": source,
            "lotId": lot_id,
            "groupId": known_group_ids.get(key, ""),
            "firstSeenAt": "",
            "lastSeenAt": "",
            "firstSeenRunId": "",
            "lastSeenRunId": "",
            "seenCount": 0,
            "active": True,
            "firstSeenInRun": False,
            "wasActive": True,
            "disappearedAfterAuthoritativeRefresh": False,
        }

    observed: set[str] = set()
    new_keys: set[str] = set()
    for source, rows in source_rows.items():
        normalized_source = source.strip().lower()
        for row in rows:
            if normalized_source == "bavastro":
                lot_id = str(row.get("lot_auction_id") or row.get("lot_id") or "").strip()
            else:
                lot_id = str(
                    row.get("lot_id") or row.get("lot_url") or row.get("lot_web_url") or ""
                ).strip()
            if not normalized_source or not lot_id:
                continue
            key = opportunity_lifecycle_key(normalized_source, row)
            observed.add(key)
            previous = state.opportunity_lifecycle.get(key)
            was_active = key in previously_active or bool(previous and previous.get("active"))
            if previous is None:
                previous = {}
                new_keys.add(key)
            first_seen_at = str(previous.get("firstSeenAt") or observed_at)
            first_seen_run_id = str(previous.get("firstSeenRunId") or run_id)
            try:
                seen_count = int(previous.get("seenCount") or 0)
            except (TypeError, ValueError):
                seen_count = 0
            state.opportunity_lifecycle[key] = {
                "sourceId": normalized_source,
                "lotId": lot_id,
                "groupId": lifecycle_group_id(normalized_source, row),
                "firstSeenAt": first_seen_at,
                "lastSeenAt": observed_at,
                "firstSeenRunId": first_seen_run_id,
                "lastSeenRunId": run_id,
                "seenCount": seen_count + 1,
                "active": True,
                "firstSeenInRun": first_seen_run_id == run_id,
                "wasActive": was_active,
                "disappearedAfterAuthoritativeRefresh": False,
            }

    removed_keys: set[str] = set()
    for key, previous in state.opportunity_lifecycle.items():
        if key in observed:
            continue
        source = str(previous.get("sourceId") or key.split("\x1f", 1)[0]).strip().lower()
        group_id = str(previous.get("groupId") or "").strip()
        if (
            key in previously_active
            and group_id
            and group_id in authoritative_groups.get(source, set())
        ):
            previous["active"] = False
            previous["wasActive"] = True
            previous["disappearedAfterAuthoritativeRefresh"] = True
            removed_keys.add(key)

    return {"new": new_keys, "removed": removed_keys, "observed": observed}


def lifecycle_rows_for_run(
    state: AgentState,
    keys: set[str],
) -> list[dict[str, object]]:
    return [
        dict(state.opportunity_lifecycle[key])
        for key in sorted(keys)
        if key in state.opportunity_lifecycle
    ]


def row_int(row: dict[str, str], key: str) -> int:
    raw = str(row.get(key) or "").strip()
    try:
        return int(float(raw))
    except ValueError:
        return 0


def row_float(row: dict[str, str], key: str) -> float:
    raw = str(row.get(key) or "").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.0


def refresh_cached_match_row(source: str, row: dict[str, str]) -> dict[str, str] | None:
    description_key = "description" if source == "bavastro" else "lot_description"
    description = str(row.get(description_key) or "").strip()
    if not description:
        return dict(row)

    hits = matched_terms(description, MATCH_PATTERNS)
    if not hits:
        return None

    risk_flags, positive_flags = collect_flags(description)
    market_value_key = "final_amount" if source == "bavastro" else "current_value"
    score = score_match(
        description=description,
        hits=hits,
        risk_flags=risk_flags,
        positive_flags=positive_flags,
        market_value=row_float(row, market_value_key),
        number_of_bids=row_int(row, "number_of_bids"),
    )

    refreshed = dict(row)
    refreshed["matched_keywords"] = ", ".join(hits)
    refreshed["positive_flags"] = ", ".join(positive_flags)
    refreshed["risk_flags"] = ", ".join(risk_flags)
    refreshed["score"] = str(score)
    return refreshed


def sort_match_rows(source: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    group_key = match_group_key(source)
    return sorted(
        rows,
        key=lambda row: (
            -row_int(row, "score"),
            row_int(row, group_key),
            row_int(row, "lot_number"),
        ),
    )


def active_match_groups_for_source(
    state: AgentState,
    source: str,
) -> dict[str, list[dict[str, str]]]:
    if source == "bavastro":
        return state.active_bavastro_matches_by_group
    return state.active_castells_matches_by_group


def active_match_rows_for_source(state: AgentState, source: str) -> list[dict[str, str]]:
    rows = [
        row
        for group_rows in active_match_groups_for_source(state, source).values()
        for row in group_rows
    ]
    return sort_match_rows(source, rows)


def reconcile_active_match_state(
    state: AgentState,
    source: str,
    active_ids: list[int],
    current_rows: list[dict[str, str]],
    refreshed_ids: list[int],
    *,
    inventory_authoritative: bool,
    refresh_succeeded: bool,
    refresh_complete: bool | None = None,
    completed_group_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    groups = active_match_groups_for_source(state, source)
    group_key = match_group_key(source)
    active_keys = {str(item) for item in active_ids}

    if inventory_authoritative:
        for stale_group_id in set(groups) - active_keys:
            groups.pop(stale_group_id, None)

    refreshed_keys = {str(item) for item in refreshed_ids}
    if refresh_succeeded:
        rows_by_group: dict[str, list[dict[str, str]]] = {
            group_id: [] for group_id in refreshed_keys
        }
        for row in current_rows:
            group_id = str(row.get(group_key) or "").strip()
            if group_id in rows_by_group:
                rows_by_group[group_id].append(dict(row))
        for group_id, rows in rows_by_group.items():
            group_is_complete = (
                refresh_complete is not False
                if completed_group_ids is None
                else group_id in completed_group_ids
            )
            if group_is_complete:
                # Only a confirmed-complete refresh is allowed to prove that a
                # previously known lot disappeared from an active group.
                groups[group_id] = rows
                continue

            # A partial response proves only the rows it returned. Merge those
            # rows into the cache and retain omitted inventory until a complete
            # refresh can authoritatively remove it.
            merged_by_key = {
                match_unique_key(source, row): dict(row)
                for row in groups.get(group_id, [])
            }
            for row in rows:
                merged_by_key[match_unique_key(source, row)] = dict(row)
            groups[group_id] = list(merged_by_key.values())

    reconciled: dict[str, list[dict[str, str]]] = {}
    for group_id, rows in groups.items():
        if inventory_authoritative and group_id not in active_keys:
            continue
        valid_rows = [
            refreshed
            for row in rows
            if (refreshed := refresh_cached_match_row(source, row)) is not None
        ]
        reconciled[group_id] = valid_rows

    groups.clear()
    groups.update(reconciled)
    return active_match_rows_for_source(state, source)


def reconcile_extra_match_state(
    state: AgentState,
    current_rows: list[dict[str, str]],
    source_statuses: list[dict[str, object]],
    *,
    status_payload_valid: bool,
) -> list[dict[str, str]]:
    """Keep registry-backed inventory until that source completes a full refresh."""
    groups = state.active_extra_matches_by_source
    rows_by_source: dict[str, list[dict[str, str]]] = {}
    for row in current_rows:
        source_id = str(row.get("source_id") or "").strip().lower()
        if source_id:
            rows_by_source.setdefault(source_id, []).append(dict(row))

    reported_statuses: dict[str, tuple[str, bool]] = {}
    for entry in source_statuses:
        if not isinstance(entry, dict):
            continue
        source_id = str(entry.get("source_id") or "").strip().lower()
        if source_id:
            reported_statuses[source_id] = (
                str(entry.get("status") or "unknown").strip().lower(),
                entry.get("inventory_authoritative") is True,
            )

    # A valid status file enumerates the configured registry. Sources absent
    # from it were deliberately removed; a missing/corrupt status file proves
    # nothing and therefore preserves the whole cache.
    if status_payload_valid:
        for removed_source in set(groups) - set(reported_statuses):
            groups.pop(removed_source, None)

    for source_id, (status, inventory_authoritative) in reported_statuses.items():
        fresh_rows = rows_by_source.pop(source_id, [])
        fresh_by_key = {
            match_unique_key(source_id, row): dict(row)
            for row in fresh_rows
        }
        if status == "success" and inventory_authoritative:
            groups[source_id] = list(fresh_by_key.values())
            continue

        # Partial and failed collections can add/update facts, but they cannot
        # prove that an omitted lot disappeared.
        merged = {
            match_unique_key(source_id, row): dict(row)
            for row in groups.get(source_id, [])
        }
        merged.update(fresh_by_key)
        groups[source_id] = list(merged.values())

    # Preserve usable rows even if a malformed status payload omitted their
    # source entry. Downstream lifecycle health remains non-authoritative.
    for source_id, fresh_rows in rows_by_source.items():
        merged = {
            match_unique_key(source_id, row): dict(row)
            for row in groups.get(source_id, [])
        }
        merged.update(
            {
                match_unique_key(source_id, row): dict(row)
                for row in fresh_rows
            }
        )
        groups[source_id] = list(merged.values())

    return sorted(
        (dict(row) for rows in groups.values() for row in rows),
        key=lambda row: (
            -row_int(row, "score"),
            str(row.get("source_id") or ""),
            str(row.get("group_id") or ""),
            str(row.get("lot_number") or ""),
            str(row.get("lot_id") or row.get("lot_url") or ""),
        ),
    )


def read_cached_match_rows(
    source: str,
    active_ids: list[int],
    exclude_run_dir: Path | None = None,
) -> list[dict[str, str]]:
    filename = match_csv_filename(source)
    group_key = match_group_key(source)
    incremental_key = match_incremental_key(source)
    pending_ids = {str(item) for item in active_ids}
    cached_rows: list[dict[str, str]] = []

    for run_dir in list_run_dirs():
        if not pending_ids:
            break
        if exclude_run_dir and run_dir == exclude_run_dir:
            continue

        metadata_path = run_dir / "run.json"
        if not metadata_path.exists():
            continue
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        processed_ids = {
            str(item)
            for item in (payload.get("incremental") or {}).get(incremental_key, [])
        }
        relevant_ids = pending_ids & processed_ids
        if not relevant_ids:
            continue

        csv_path = run_dir / filename
        rows_by_group: dict[str, list[dict[str, str]]] = {group_id: [] for group_id in relevant_ids}
        if csv_path.exists():
            for row in read_csv_rows(csv_path):
                group_id = str(row.get(group_key) or "").strip()
                if group_id in relevant_ids:
                    refreshed = refresh_cached_match_row(source, row)
                    if refreshed is not None:
                        rows_by_group[group_id].append(refreshed)

        for group_id in relevant_ids:
            cached_rows.extend(rows_by_group.get(group_id) or [])
            pending_ids.discard(group_id)

    return sort_match_rows(source, cached_rows)


def validated_match_rows_for_run(run_dir: Path, source: str) -> list[dict[str, str]]:
    csv_path = run_dir / match_csv_filename(source)
    if not csv_path.exists():
        return []

    validated_rows: list[dict[str, str]] = []
    for row in read_csv_rows(csv_path):
        refreshed = refresh_cached_match_row(source, row)
        if refreshed is not None:
            validated_rows.append(refreshed)
    return sort_match_rows(source, validated_rows)


def merge_active_match_rows(
    source: str,
    active_ids: list[int],
    current_rows: list[dict[str, str]],
    refreshed_ids: list[int],
    exclude_run_dir: Path | None = None,
) -> list[dict[str, str]]:
    merged_rows: list[dict[str, str]] = []
    seen: set[str] = set()
    cached_lookup_ids = [item for item in active_ids if item not in set(refreshed_ids)]
    cached_rows = read_cached_match_rows(source, cached_lookup_ids, exclude_run_dir=exclude_run_dir)

    for row in current_rows + cached_rows:
        unique_key = match_unique_key(source, row)
        if unique_key in seen:
            continue
        seen.add(unique_key)
        merged_rows.append(row)

    return sort_match_rows(source, merged_rows)


def write_csv_dict_rows(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def extract_metric(text: str, label: str) -> int | None:
    prefix = f"{label}:"
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        raw = line.split(":", 1)[1].strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            return int(digits)
    return None


def run_step(name: str, command: list[str], run_dir: Path) -> StepResult:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / f"{name}.stdout.log"
    stderr_path = logs_dir / f"{name}.stderr.log"

    started_at = now_iso()
    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            check=False,
        )
    finished_at = now_iso()

    return StepResult(
        name=name,
        command=command,
        stdout_path=str(stdout_path.relative_to(run_dir)),
        stderr_path=str(stderr_path.relative_to(run_dir)),
        exit_code=result.returncode,
        status="success" if result.returncode == 0 else "failed",
        started_at=started_at,
        finished_at=finished_at,
    )


def skipped_step(name: str, reason: str, run_dir: Path) -> StepResult:
    return StepResult(
        name=name,
        command=[],
        stdout_path=str((run_dir / "logs" / f"{name}.stdout.log").relative_to(run_dir)),
        stderr_path=str((run_dir / "logs" / f"{name}.stderr.log").relative_to(run_dir)),
        exit_code=None,
        status="skipped",
        started_at=now_iso(),
        finished_at=now_iso(),
        skipped_reason=reason,
    )


def load_group_receipt(path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    payload = read_json_object(path)
    raw_receipts = payload.get("receipts")
    receipts = [item for item in raw_receipts if isinstance(item, dict)] if isinstance(raw_receipts, list) else []
    return payload, receipts


def apply_group_receipt(step: StepResult, path: Path, expected_ids: list[int]) -> StepResult:
    payload, receipts = load_group_receipt(path)
    step.receipts = receipts
    expected = {str(item) for item in expected_ids}
    complete = complete_group_ids(step)
    step.inventory_authoritative = bool(
        step.status == "success"
        and payload.get("inventoryAuthoritative") is True
        and len(receipts) == len(expected_ids)
        and all(receipt_has_required_fields(item) for item in receipts)
        and expected == {str(item.get("groupId") or "") for item in receipts}
        and complete == expected
    )
    if step.status == "success" and not step.inventory_authoritative:
        step.status = "partial"
    return step


def receipt_has_required_fields(item: dict[str, object]) -> bool:
    if not all(
        str(item.get(field) or "").strip()
        for field in ("groupId", "startedAt", "finishedAt")
    ):
        return False
    if str(item.get("status") or "").strip().lower() not in {"complete", "partial", "failed"}:
        return False
    try:
        return int(item.get("lotCount") or 0) >= 0 and int(item.get("errorCount") or 0) >= 0
    except (TypeError, ValueError):
        return False


def complete_group_ids(step: StepResult) -> set[str]:
    complete: set[str] = set()
    for item in step.receipts:
        group_id = str(item.get("groupId") or "")
        if not receipt_has_required_fields(item):
            continue
        if (
            group_id
            and str(item.get("status") or "").strip().lower() == "complete"
            and int(item["errorCount"] or 0) == 0
        ):
            complete.add(group_id)
    return complete


def effective_inventory_authority(
    discovery: StepResult,
    matches: StepResult,
    active_ids: list[int],
    queried_ids: list[int],
) -> bool:
    """Calculate source authority once from discovery plus group coverage proof."""

    return bool(
        discovery.inventory_authoritative
        and matches.status in {"success", "skipped"}
        and matches.inventory_authoritative
        and set(active_ids) == set(queried_ids)
    )


def known_group_ids_for_source(state: AgentState, source: str) -> set[str]:
    if source == "bavastro":
        groups = set(state.active_bavastro_matches_by_group)
    elif source == "castells":
        groups = set(state.active_castells_matches_by_group)
    else:
        groups = {
            str(row.get("group_id") or "").strip()
            for row in state.active_extra_matches_by_source.get(source, [])
            if isinstance(row, dict)
            if str(row.get("group_id") or "").strip()
        }
    groups.update(
        str(value.get("groupId") or "").strip()
        for value in state.opportunity_lifecycle.values()
        if str(value.get("sourceId") or "").strip().lower() == source
        and str(value.get("groupId") or "").strip()
    )
    return groups


def coverage_summary(
    run_dir: Path,
    step: StepResult,
    active_ids: list[int],
    queried_ids: list[int],
    rows: list[dict[str, str]],
    new_rows: list[dict[str, str]],
    removed_keys: set[str],
    source: str,
    *,
    discovery_complete: bool,
) -> dict[str, object]:
    expected = {str(item) for item in active_ids}
    complete = {
        str(item.get("groupId") or "")
        for item in step.receipts
        if str(item.get("status") or "").strip().lower() == "complete"
    }
    incomplete = sorted(expected - complete)
    incomplete.extend(
        sorted(
            {
                str(item.get("groupId") or "")
                for item in step.receipts
                if str(item.get("status") or "").strip().lower() != "complete"
                and str(item.get("groupId") or "")
            }
            - set(incomplete)
        )
    )
    observed_lots = extract_metric(
        read_text(run_dir / step.stdout_path),
        "Lotes escaneados",
    )
    return {
        "sourceId": source,
        "discoveryComplete": discovery_complete,
        "groupsDiscovered": len(active_ids),
        "groupsQueried": len(queried_ids),
        "completeGroups": sorted(complete),
        "partialOrFailedGroups": incomplete,
        "lotCount": observed_lots if observed_lots is not None else len(rows),
        "matchesDetected": len(rows),
        "matchesNew": len(new_rows),
        "matchesRemoved": sum(
            key.startswith(f"{source}\x1f") for key in removed_keys
        ),
        "status": (
            "complete"
            if step.inventory_authoritative
            else "failed"
            if step.status == "failed"
            else "partial"
        ),
        "inventoryAuthoritative": step.inventory_authoritative,
        "groupReceipts": step.receipts,
    }


def source_status(step: StepResult, default_skipped_reason: str = "skipped") -> str:
    if step.status == "skipped":
        return step.skipped_reason or default_skipped_reason
    return step.status


def classify_bavastro_discovery(
    step: StepResult,
    run_dir: Path,
    *,
    active_mode: bool = True,
    discovery_path: Path | None = None,
) -> StepResult:
    if step.status != "success":
        return step

    stdout_text = read_text(run_dir / step.stdout_path)
    errors = extract_metric(stdout_text, "Errores red/HTTP")

    # An exit code of zero is not evidence that active discovery succeeded.
    # Require the explicit active-result metric so parser drift fails closed.
    evidence_metric = (
        "Subastas activas detectadas" if active_mode else "Total IDs escaneados"
    )
    if extract_metric(stdout_text, evidence_metric) is None:
        step.status = "failed"
        return step
    if active_mode and errors is None:
        step.status = "failed"
        return step
    if active_mode and discovery_path is not None:
        rows = read_csv_rows(discovery_path)
        row_ids = {
            str(row.get("id") or "").strip()
            for row in rows
            if str(row.get("id") or "").strip()
        }
        if (
            not csv_has_field(discovery_path, "id")
            or len(rows) != len(row_ids)
            or extract_metric(stdout_text, evidence_metric) != len(row_ids)
        ):
            step.status = "failed"
            return step
    if errors:
        step.status = "partial"
    elif active_mode:
        step.inventory_authoritative = True
    return step


def classify_castells_discovery(
    step: StepResult,
    run_dir: Path,
    discovery_path: Path,
) -> StepResult:
    if step.status != "success":
        return step

    stdout_text = read_text(run_dir / step.stdout_path)
    detected = extract_metric(stdout_text, "Remates activos detectados")
    rows = read_csv_rows(discovery_path)
    row_ids = {
        str(row.get("remate_id") or "").strip()
        for row in rows
        if str(row.get("remate_id") or "").strip()
    }
    if (
        not csv_has_field(discovery_path, "remate_id")
        or detected is None
        or detected != len(row_ids)
        or len(rows) != len(row_ids)
    ):
        step.status = "failed"
        return step
    step.inventory_authoritative = True
    return step


def classify_bavastro_matches(
    step: StepResult,
    run_dir: Path,
    receipt_path: Path | None = None,
    expected_ids: list[int] | None = None,
) -> StepResult:
    if step.status != "success":
        return step

    stdout_text = read_text(run_dir / step.stdout_path)
    if "[ERR]" in stdout_text:
        step.status = "partial"
    if receipt_path is not None and expected_ids is not None and receipt_path.exists():
        step = apply_group_receipt(step, receipt_path, expected_ids)
    return step


def classify_castells_matches(
    step: StepResult,
    run_dir: Path,
    receipt_path: Path | None = None,
    expected_ids: list[int] | None = None,
) -> StepResult:
    if step.status != "success":
        return step

    stdout_text = read_text(run_dir / step.stdout_path)
    errors = extract_metric(stdout_text, "Errores")
    scanned_lots = extract_metric(stdout_text, "Lotes escaneados")

    if errors and (scanned_lots or 0) == 0:
        step.status = "failed"
    elif errors:
        step.status = "partial"
    if receipt_path is not None and expected_ids is not None and receipt_path.exists():
        step = apply_group_receipt(step, receipt_path, expected_ids)
    return step


def classify_extra_sources(step: StepResult, status_path: Path) -> StepResult:
    if step.status != "success":
        return step

    payload = read_json_object(status_path)
    reported_status = str(payload.get("status") or "").strip().lower()
    if reported_status == "partial":
        step.status = "partial"
    elif reported_status != "success":
        step.status = "failed"
    return step


def overall_status(steps: list[StepResult]) -> str:
    by_name = {step.name: step for step in steps}
    bavastro_discovery = by_name["bavastro_discovery"]
    bavastro_matches = by_name["bavastro_matches"]
    castells_discovery = by_name["castells_discovery"]
    castells_matches = by_name["castells_matches"]
    extra_sources = by_name.get("extra_sources")

    bavastro_failed = bavastro_discovery.status == "failed" or bavastro_matches.status == "failed"
    castells_failed = castells_discovery.status == "failed" or castells_matches.status == "failed"
    extra_failed = extra_sources is not None and extra_sources.status == "failed"
    has_partial = any(step.status == "partial" for step in steps)
    has_unproven_coverage = any(
        step.name in {"bavastro_matches", "castells_matches", "extra_sources"}
        and step.status == "success"
        and not step.inventory_authoritative
        for step in steps
    )

    if bavastro_failed and castells_failed and (extra_sources is None or extra_failed):
        return "failure"
    if bavastro_failed or castells_failed or extra_failed or has_partial or has_unproven_coverage:
        return "partial_failure"
    return "success"


def canonical_scan_status(status: object) -> str:
    """Normalize legacy runner values to the public scan-status contract."""
    normalized = str(status or "").strip().lower()
    return {
        "success": "success",
        "partial": "partial",
        "partial_failure": "partial",
        "failed": "failed",
        "failure": "failed",
    }.get(normalized, "failed")


def write_json(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


def parse_recipients(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def notification_mode_enabled(mode: str, status: str, total_matches: int) -> bool:
    normalized = (mode or "").strip().lower()
    if normalized in {"", "disabled", "none", "off", "false", "0"}:
        return False
    if normalized == "always":
        return True
    if normalized == "failure":
        return status != "success"
    if normalized == "matches":
        return total_matches > 0
    if normalized in {"matches_or_failure", "matches-or-failure"}:
        return total_matches > 0 or status != "success"
    return False


def apple_script_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_notification_title(status: str, total_matches: int) -> str:
    if status == "success":
        if total_matches > 0:
            return f"Auction Watch: {total_matches} matches"
        return "Auction Watch: sin matches"
    if status == "partial_failure":
        return "Auction Watch: falla parcial"
    return "Auction Watch: corrida fallida"


def build_notification_message(
    status: str,
    counts: dict[str, object],
    summary_path: Path,
) -> str:
    return (
        f"Estado: {status} | "
        f"{total_match_count(counts)} matches activos en todas las fuentes | "
        f"Bavastro {counts.get('bavastro_matches', 0)} | "
        f"Castells {counts.get('castells_matches', 0)} | "
        f"Otras {counts.get('extra_matches', 0)} | "
        f"{summary_path}"
    )


def format_match_closing(source: str, row: dict[str, str]) -> str:
    raw = str(row.get("auction_end_date") if source == "bavastro" else row.get("closing_at") or "")
    parsed = parse_source_datetime(raw, now_local_dt().tzinfo)
    return format_datetime_es(parsed) if parsed else (raw or "-")


def build_email_subject(
    prefix: str,
    status: str,
    total_matches: int,
    watch_hits: list[WatchHit],
) -> str:
    if status == "success" and watch_hits:
        top_hit = watch_hits[0]
        return f"{prefix} 🎯 {top_hit.label} | faltan {top_hit.remaining_text}"
    if status == "success" and total_matches > 0:
        return f"{prefix} 📡 {total_matches} oportunidades activas"
    if status == "success":
        return f"{prefix} 😴 Sin matches activos"
    return f"{prefix} ⚠️ {build_notification_title(status, total_matches)}"


def build_email_body(
    run_id: str,
    status: str,
    counts: dict[str, object],
    summary_path: Path,
    match_views: list[MatchView],
    watch_hits: list[WatchHit],
    *,
    app_base_url: str = "",
) -> str:
    lines = [
        "AUCTION WATCH",
        "=============",
        "",
        "PANORAMA",
        "--------",
    ]

    for _source_id, source_items in group_match_views(match_views):
        if not source_items:
            continue
        lines.append(f"{source_items[0].source_label}: {len(source_items)} matches activos")

    if watch_hits:
        lines.extend(["", "SEGUIMIENTO PRIORITARIO", "----------------------"])
        for idx, hit in enumerate(watch_hits, start=1):
            match_item = match_view_for_watch_hit(match_views, hit)
            discard_url = discard_action_url(app_base_url, match_item) if match_item else ""
            lines.extend(
                [
                    f"{idx}. {hit.label}",
                    f"   Urgencia    : {hit.urgency_label}",
                    f"   Falta       : {hit.remaining_text}",
                    f"   Cierre      : {hit.closing_at_display}",
                    f"   {hit.price_label}",
                    f"   Keywords    : {hit.matched_keywords or '-'}",
                    "   Ver lote",
                    f"   {hit.lot_url}",
                    "   Ver remate",
                    f"   {hit.group_url}",
                    f"   Descripcion : {shorten_text(hit.description, 180)}",
                ]
            )
            if discard_url:
                lines.extend(["   Descartar para próximos mails", f"   {discard_url}"])
            if hit.notes:
                lines.append(f"   Nota        : {hit.notes}")
            lines.append("")

    top_key = watch_hit_dismissal_key(watch_hits[0]) if watch_hits else None
    visible_items = [item for item in match_views if not top_key or match_view_dismissal_key(item) != top_key]
    rendered_count = 0
    for closing_label, closing_items in group_match_views_by_closing_day(visible_items):
        lines.extend(
            [
                "",
                f"{closing_label.upper()} ({len(closing_items)})",
                "--------------------------------",
            ]
        )
        for idx, item in enumerate(closing_items, start=1):
            lines.extend(
                [
                    f"{idx}. {shorten_text(item.title, 160)}",
                    f"   Score   : {item.score} | Keywords: {item.matched_keywords or '-'}",
                    f"   Riesgos : {item.risk_flags or '-'}",
                    f"   Precio  : {item.price_label} | {item.timing_label.title()}: {item.closing_at_display}",
                    f"   Lote    : {item.lot_url}",
                    f"   Remate  : {item.group_url}",
                ]
            )
            discard_url = discard_action_url(app_base_url, item)
            if discard_url:
                lines.extend(["   Descartar para próximos mails", f"   {discard_url}"])
            lines.append("")
            rendered_count += 1

    if rendered_count == 0 and not watch_hits:
        lines.extend(
            [
                "",
                "MATCHES ACTIVOS",
                "---------------",
                "No hubo matches activos en esta corrida.",
            ]
        )

    return "\n".join(lines)


def html_escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def html_button(label: str, target: str, *, secondary: bool = False) -> str:
    href = (target or "").strip()
    if not href:
        return ""

    if secondary:
        background = "#07182d"
        color = "#dceeff"
        border = "#3675a8"
    else:
        background = "#229ddd"
        color = "#f7fbff"
        border = "#55c5ff"

    return (
        f'<a href="{html_escape(href)}" '
        f'style="display:inline-block;padding:10px 14px;'
        f'background:{background};color:{color};text-decoration:none;'
        f'border:1px solid {border};border-radius:12px;'
        f'font-family:Arial, Helvetica, sans-serif;font-size:11px;'
        f'font-weight:700;letter-spacing:0.04em;">'
        f"{html_escape(label)}</a>"
    )


def build_match_card_html(
    item: MatchView,
    *,
    app_base_url: str = "",
) -> str:
    title = shorten_text(item.title, 96)
    price_line = f"{item.price_label} · {item.timing_label} {item.closing_at_display}"
    signal_parts = [item.source_label, item.timing_label]
    if item.matched_keywords:
        signal_parts.append(", ".join(part.strip() for part in item.matched_keywords.split(",")[:2] if part.strip()))
    discard_url = discard_action_url(app_base_url, item)
    actions = html_button("Ver publicación", item.lot_url)
    if discard_url:
        actions += "&nbsp;" + html_button("Descartar", discard_url, secondary=True)
    image_url = str(item.image_url or "").strip()
    if image_url.startswith(("http://", "https://")):
        image_block = (
            '<td width="112" valign="middle" style="width:112px;padding:12px;background:#06182d;">'
            f'<img src="{html_escape(image_url)}" alt="{html_escape(title)}" width="88" '
            'style="display:block;width:88px;max-width:88px;height:auto;max-height:116px;'
            'margin:0 auto;border:0;object-fit:contain;">'
            '</td>'
        )
    else:
        image_block = (
            '<td width="112" valign="middle" style="width:112px;padding:12px;background:#06182d;">'
            '<div style="width:88px;height:88px;margin:0 auto;border:1px solid #315778;border-radius:10px;'
            'font-family:Arial, Helvetica, sans-serif;font-size:11px;line-height:88px;text-align:center;color:#9fc4e4;">'
            'Sin foto</div></td>'
        )

    return (
        '<tr><td style="padding:0 0 12px 0;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="border:1px solid #315778;background:#102f50;border-radius:16px;">'
        f'<tr>{image_block}<td valign="top" style="padding:16px 16px 14px 16px;">'
        f'<div style="font-family:Arial, Helvetica, sans-serif;font-size:11px;'
        'line-height:1.4;color:#9fc4e4;text-transform:uppercase;letter-spacing:0.06em;">'
        f"{html_escape(' · '.join(signal_parts))}</div>"
        f'<div style="padding-top:7px;font-family:Arial, Helvetica, sans-serif;'
        'font-size:19px;line-height:1.24;font-weight:700;color:#f1f7ff;">'
        f"{html_escape(title)}</div>"
        f'<div style="padding-top:10px;font-family:Arial, Helvetica, sans-serif;'
        'font-size:13px;line-height:1.45;color:#d1e4f6;">'
        f"{html_escape(price_line)}</div>"
        + (
            f'<div style="padding-top:7px;font-family:Arial, Helvetica, sans-serif;font-size:12px;'
            f'line-height:1.4;color:#ffc46b;">⚠️ {html_escape(item.risk_flags)}</div>'
            if item.risk_flags
            else ""
        )
        + f'<div style="padding-top:13px;">{actions}</div>'
        '</td></tr></table></td></tr>'
    )


def resolve_mail_image_urls(match_views: list[MatchView]) -> list[MatchView]:
    """Resolve Remotes redirects so Gmail receives the same usable image as the web catalog."""
    resolved: list[MatchView] = []
    for item in match_views:
        image_url = str(item.image_url or "").strip()
        if item.source_id != "remotes" or not image_url.startswith(("http://", "https://")):
            resolved.append(item)
            continue
        try:
            request = Request(image_url, headers={"User-Agent": "AuctionWatch/1.0"}, method="HEAD")
            with urlopen(request, timeout=8) as response:
                final_url = str(response.geturl() or "").strip()
            resolved.append(replace(item, image_url=final_url if final_url.startswith(("http://", "https://")) else image_url))
        except Exception:
            resolved.append(item)
    return resolved


def build_newsletter_html(
    status: str,
    counts: dict[str, object],
    match_views: list[MatchView],
    watch_hits: list[WatchHit],
    *,
    hero_image_src: str = "",
    app_base_url: str = "",
) -> str:
    total_matches = total_match_count(counts)
    extra_blocks: list[str] = []
    for closing_label, closing_items in group_match_views_by_closing_day(match_views):
        extra_blocks.append(
            '<tr><td style="padding:10px 0 10px 0;">'
            '<div style="font-family:Arial, Helvetica, sans-serif;font-size:12px;'
            'line-height:1.5;color:#9fc4e4;text-transform:uppercase;letter-spacing:0.08em;">'
            f'{html_escape(closing_label)} · {len(closing_items)} oportunidades</div></td></tr>'
        )
        for item in closing_items:
            extra_blocks.append(build_match_card_html(item, app_base_url=app_base_url))

    opportunities_block = ""
    if extra_blocks:
        opportunities_block = (
            '<tr><td style="padding:0 24px 16px 24px;">'
            '<div style="font-family:Arial, Helvetica, sans-serif;font-size:13px;'
            'line-height:1.5;color:#9fc4e4;text-transform:uppercase;letter-spacing:0.08em;'
            'padding:0 0 4px 0;">Oportunidades activas, ordenadas por cierre</div>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
            f"{''.join(extra_blocks)}"
            "</table></td></tr>"
        )
    empty_block = (
        '<tr><td style="padding:24px;font-family:Arial, Helvetica, sans-serif;font-size:15px;'
        'line-height:1.5;color:#d1e4f6;">No hay oportunidades activas en esta corrida.</td></tr>'
        if not extra_blocks
        else ""
    )

    return f"""<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auction Watch</title>
  </head>
  <body style="margin:0;padding:0;background:#06182d;color:#f1f7ff;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#06182d;">
      <tr>
        <td align="center" style="padding:18px 10px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;background:#0b223b;border:1px solid #315778;border-radius:18px;overflow:hidden;">
            <tr>
              <td style="padding:22px 24px 18px 24px;background:#102f50;">
                <div style="font-family:Arial, Helvetica, sans-serif;font-size:12px;line-height:1.4;color:#9fc4e4;text-transform:uppercase;letter-spacing:0.08em;">AUCTION WATCH · REMATES ACTIVOS</div>
                <div style="padding-top:7px;font-family:Arial, Helvetica, sans-serif;font-size:30px;line-height:1.1;font-weight:700;color:#f1f7ff;">{total_matches} oportunidades para mirar</div>
                <div style="padding-top:8px;font-family:Arial, Helvetica, sans-serif;font-size:13px;line-height:1.5;color:#d1e4f6;">Ordenadas por día de cierre. Cada card usa la misma imagen que ves en la web.</div>
              </td>
            </tr>
            {opportunities_block}
            {empty_block}
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def rich_paragraph(
    text: str,
    *,
    size: int = 13,
    color: tuple[int, int, int] = (9000, 9000, 9000),
    font: str = "Helvetica Neue",
) -> dict[str, object]:
    return {"text": text, "size": size, "color": color, "font": font}


def build_mailapp_rich_paragraphs(
    run_id: str,
    status: str,
    counts: dict[str, object],
    summary_path: Path,
    match_views: list[MatchView],
    watch_hits: list[WatchHit],
    *,
    app_base_url: str = "",
) -> list[dict[str, object]]:
    accent = (44500, 20500, 4000)
    bronze = (39500, 17500, 3000)
    dark = (8500, 8500, 8500)
    muted = (23000, 23000, 23000)
    soft = (40000, 39000, 38000)
    line = (51000, 49500, 47000)
    total_matches = total_match_count(counts)
    top_hit = watch_hits[0] if watch_hits else None
    top_match = match_view_for_watch_hit(match_views, top_hit)

    paragraphs: list[dict[str, object]] = [
        rich_paragraph("🏛️ AUCTION WATCH", size=24, color=dark, font="Helvetica Neue"),
        rich_paragraph("Remates con historia · oportunidades reales", size=12, color=bronze, font="Helvetica Neue"),
        rich_paragraph("────────────────────────────────────────", size=12, color=line, font="Helvetica Neue"),
    ]

    if top_hit:
        paragraphs.extend(
            [
                rich_paragraph("📌 Lote destacado", size=14, color=accent, font="Helvetica Neue"),
                rich_paragraph(top_hit.label, size=22, color=dark, font="Baskerville"),
                rich_paragraph(
                    f"⏳ Falta: {top_hit.remaining_text}",
                    size=12,
                    color=bronze,
                    font="Helvetica Neue",
                ),
                rich_paragraph(
                    f"🔥 Estado: {top_hit.urgency_label}",
                    size=12,
                    color=accent,
                    font="Helvetica Neue",
                ),
                rich_paragraph(
                    f"💸 {top_hit.price_label}",
                    size=14,
                    color=dark,
                    font="Baskerville",
                ),
                rich_paragraph(
                    f"🗓️ Cierra: {top_hit.closing_at_display}",
                    size=12,
                    color=dark,
                    font="Helvetica Neue",
                ),
                rich_paragraph("🔗 Ver publicación", size=12, color=accent, font="Helvetica Neue"),
                rich_paragraph(top_hit.lot_url, size=10, color=muted, font="Helvetica Neue"),
                *(
                    [
                        rich_paragraph(
                            "⊘ Descartar para próximos mails",
                            size=11,
                            color=bronze,
                            font="Helvetica Neue",
                        ),
                        rich_paragraph(
                            discard_action_url(app_base_url, top_match),
                            size=10,
                            color=muted,
                            font="Helvetica Neue",
                        ),
                    ]
                    if top_match and discard_action_url(app_base_url, top_match)
                    else []
                ),
                rich_paragraph("", size=8, color=soft),
                rich_paragraph(
                    f"📝 Nota: {top_hit.notes or 'seguimiento prioritario por valor de lote y potencial para colección.'}",
                    size=13,
                    color=dark,
                    font="Helvetica Neue",
                ),
            ]
        )
    elif total_matches > 0:
        paragraphs.extend(
            [
                rich_paragraph("📡 Radar del día", size=14, color=accent, font="Helvetica Neue"),
                rich_paragraph(
                    f"Hoy quedaron {total_matches} lotes activos para mirar.",
                    size=18,
                    color=dark,
                    font="Baskerville",
                ),
            ]
        )
    elif status == "success":
        paragraphs.extend(
            [
                rich_paragraph("😴 Sin oportunidades destacadas", size=14, color=accent, font="Helvetica Neue"),
                rich_paragraph(
                    "Hoy no aparecieron matches activos para seguir de cerca.",
                    size=18,
                    color=dark,
                    font="Baskerville",
                ),
            ]
        )
    else:
        paragraphs.extend(
            [
                rich_paragraph("⚠️ Corrida con problemas", size=14, color=accent, font="Helvetica Neue"),
                rich_paragraph(
                    "Conviene revisar el resumen porque hubo una falla parcial o total.",
                    size=18,
                    color=dark,
                    font="Baskerville",
                ),
            ]
        )

    paragraphs.extend(
        [
            rich_paragraph("", size=8, color=soft),
            rich_paragraph("────────────────────────────────────────", size=12, color=line, font="Helvetica Neue"),
            rich_paragraph("👀 Otras oportunidades", size=14, color=accent, font="Helvetica Neue"),
        ]
    )

    top_key = watch_hit_dismissal_key(top_hit) if top_hit else None
    visible_items = [item for item in match_views if not top_key or match_view_dismissal_key(item) != top_key]
    extra_count = len(visible_items)
    for closing_label, closing_items in group_match_views_by_closing_day(visible_items):
        paragraphs.append(
            rich_paragraph(
                f"🗓️ {closing_label} · {len(closing_items)} oportunidades",
                size=12,
                color=bronze,
                font="Helvetica Neue",
            )
        )
        for item in closing_items:
            discard_url = discard_action_url(app_base_url, item)
            paragraphs.extend(
                [
                    rich_paragraph(
                        f"• {shorten_text(item.title, 140)}",
                        size=14,
                        color=dark,
                        font="Baskerville",
                    ),
                    rich_paragraph(
                        f"💸 {item.price_label} · {item.timing_label} {item.closing_at_display}",
                        size=11,
                        color=muted,
                        font="Helvetica Neue",
                    ),
                    *(
                        [
                            rich_paragraph(
                                f"⚠️ {item.risk_flags}",
                                size=11,
                                color=bronze,
                                font="Helvetica Neue",
                            )
                        ]
                        if item.risk_flags
                        else []
                    ),
                    rich_paragraph("🔗 Ver publicación", size=11, color=accent, font="Helvetica Neue"),
                    rich_paragraph(item.lot_url, size=10, color=muted, font="Helvetica Neue"),
                    *(
                        [
                            rich_paragraph(
                                "⊘ Descartar para próximos mails",
                                size=11,
                                color=bronze,
                                font="Helvetica Neue",
                            ),
                            rich_paragraph(discard_url, size=10, color=muted, font="Helvetica Neue"),
                        ]
                        if discard_url
                        else []
                    ),
                    rich_paragraph("", size=7, color=soft),
                ]
            )
    if extra_count == 0:
        paragraphs.extend(
            [
                rich_paragraph("Hoy no hubo otras oportunidades activas.", size=12, color=muted, font="Helvetica Neue"),
                rich_paragraph("", size=7, color=soft),
            ]
        )

    paragraphs.extend(
        [
            rich_paragraph("────────────────────────────────────────", size=12, color=line, font="Helvetica Neue"),
            rich_paragraph("📊 Panorama", size=14, color=accent, font="Helvetica Neue"),
        ]
    )
    for _source_id, source_items in group_match_views(match_views):
        paragraphs.append(
            rich_paragraph(
                f"{source_items[0].source_label}: {len(source_items)} matches activos",
                size=12,
                color=dark,
                font="Helvetica Neue",
            )
        )

    return paragraphs


def apple_script_text_literal(value: str) -> str:
    return f"\"{apple_script_escape(value)}\""


def apple_script_text_join(parts: list[str]) -> str:
    if not parts:
        return "\"\""
    return " & return & ".join(apple_script_text_literal(part) for part in parts)


def apple_script_color(color: tuple[int, int, int]) -> str:
    red, green, blue = color
    return f"{{{red}, {green}, {blue}}}"


def download_watch_images(run_dir: Path, watch_hits: list[WatchHit]) -> list[dict[str, object]]:
    if not watch_hits:
        return []

    assets_dir = run_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    attachment_specs: list[dict[str, object]] = []

    top_hit = watch_hits[0]
    image_url = (top_hit.image_url or "").strip()
    if not image_url:
        return attachment_specs

    parsed = urlparse(image_url)
    suffix = Path(parsed.path).suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        suffix = ".jpg"

    target_path = assets_dir / f"watch-{top_hit.watch_id}{suffix}"
    try:
        request = Request(image_url, headers={"User-Agent": "Mozilla/5.0 AuctionWatch"})
        with urlopen(request, timeout=30) as response:
            target_path.write_bytes(response.read())
    except Exception:
        return attachment_specs

    attachment_specs.append(
        {
            "path": target_path,
            "after_paragraph": 11,
            "cid": "auction-watch-featured-image",
            "preview_src": f"assets/{target_path.name}",
        }
    )
    return attachment_specs


def write_newsletter_preview(path: Path, html_body: str) -> None:
    path.write_text(html_body, encoding="utf-8")


def send_macos_notification(
    config: dict[str, str],
    status: str,
    counts: dict[str, object],
    summary_path: Path,
) -> NotificationResult:
    mode = config.get("AUCTION_WATCH_MACOS_NOTIFY", "disabled")
    total_matches = total_match_count(counts)

    if not notification_mode_enabled(mode, status, total_matches):
        return NotificationResult("macos", enabled=False, attempted=False, sent=False, detail=f"mode={mode}")

    title = build_notification_title(status, total_matches)
    message = build_notification_message(status, counts, summary_path)
    subtitle = config.get("AUCTION_WATCH_MACOS_SUBTITLE", "Remates de Uruguay")

    script = (
        f'display notification "{apple_script_escape(message)}" '
        f'with title "{apple_script_escape(title)}" '
        f'subtitle "{apple_script_escape(subtitle)}"'
    )

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return NotificationResult("macos", enabled=True, attempted=True, sent=False, detail="timeout")
    except OSError as exc:
        return NotificationResult("macos", enabled=True, attempted=True, sent=False, detail=str(exc))

    if result.returncode == 0:
        return NotificationResult("macos", enabled=True, attempted=True, sent=True, detail="sent")

    detail = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
    return NotificationResult("macos", enabled=True, attempted=True, sent=False, detail=detail)


def send_email_via_smtp(
    config: dict[str, str],
    recipients: list[str],
    subject: str,
    body: str,
    html_body: str = "",
    attachment_specs: list[dict[str, object]] | None = None,
    message_id: str = "",
    date_header: str = "",
) -> NotificationResult:
    host = config.get("AUCTION_WATCH_SMTP_HOST", "").strip()
    username = config.get("AUCTION_WATCH_SMTP_USERNAME", "").strip()
    password = config.get("AUCTION_WATCH_SMTP_PASSWORD", "").strip()
    sender = config.get("AUCTION_WATCH_EMAIL_FROM", username).strip()

    if not host or not sender or not recipients:
        return NotificationResult(
            "email",
            enabled=True,
            attempted=False,
            sent=False,
            detail="missing_smtp_config",
        )

    try:
        port = int(config.get("AUCTION_WATCH_SMTP_PORT", "587") or "587")
    except (TypeError, ValueError):
        return NotificationResult(
            "email", enabled=True, attempted=False, sent=False, detail="invalid_smtp_port"
        )
    use_starttls = config.get("AUCTION_WATCH_SMTP_STARTTLS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    if message_id:
        message["Message-ID"] = message_id
    if date_header:
        message["Date"] = date_header
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
        html_part = message.get_payload()[-1]
        for item in attachment_specs or []:
            path = item.get("path")
            cid = str(item.get("cid") or "").strip()
            if not isinstance(path, Path) or not path.exists() or not cid:
                continue
            mime_type, _ = mimetypes.guess_type(str(path))
            if mime_type:
                maintype, subtype = mime_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"
            html_part.add_related(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                cid=f"<{cid}>",
                filename=path.name,
                disposition="inline",
            )

    delivery_started = False
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if use_starttls:
                server.starttls()
                server.ehlo()
            if username and password:
                server.login(username, password)
            delivery_started = True
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001
        if not delivery_started:
            outcome = "smtp_pre_send_failed"
        elif isinstance(
            exc,
            (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError),
        ):
            outcome = "smtp_rejected"
        else:
            outcome = "smtp_delivery_uncertain"
        return NotificationResult(
            "email",
            enabled=True,
            attempted=True,
            sent=False,
            detail=f"{outcome}:{type(exc).__name__}: {exc}",
        )

    return NotificationResult("email", enabled=True, attempted=True, sent=True, detail="sent_via_smtp")


def send_email_via_sendmail(
    config: dict[str, str],
    recipients: list[str],
    subject: str,
    body: str,
    html_body: str = "",
    attachment_specs: list[dict[str, object]] | None = None,
    message_id: str = "",
    date_header: str = "",
) -> NotificationResult:
    if not recipients:
        return NotificationResult("email", enabled=True, attempted=False, sent=False, detail="missing_recipients")

    sender = config.get("AUCTION_WATCH_EMAIL_FROM", "").strip() or recipients[0]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    if message_id:
        message["Message-ID"] = message_id
    if date_header:
        message["Date"] = date_header
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
        html_part = message.get_payload()[-1]
        for item in attachment_specs or []:
            path = item.get("path")
            cid = str(item.get("cid") or "").strip()
            if not isinstance(path, Path) or not path.exists() or not cid:
                continue
            mime_type, _ = mimetypes.guess_type(str(path))
            if mime_type:
                maintype, subtype = mime_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"
            html_part.add_related(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                cid=f"<{cid}>",
                filename=path.name,
                disposition="inline",
            )

    try:
        result = subprocess.run(
            ["/usr/sbin/sendmail", "-t", "-oi"],
            cwd=REPO_ROOT,
            input=message.as_bytes(),
            capture_output=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return NotificationResult(
            "email",
            enabled=True,
            attempted=True,
            sent=False,
            detail="sendmail_delivery_uncertain:timeout",
        )
    except OSError as exc:
        return NotificationResult(
            "email",
            enabled=True,
            attempted=False,
            sent=False,
            detail=f"sendmail_pre_send_failed:{type(exc).__name__}: {exc}",
        )

    if result.returncode == 0:
        return NotificationResult("email", enabled=True, attempted=True, sent=True, detail="sent_via_sendmail")

    raw_detail = result.stderr or result.stdout
    if isinstance(raw_detail, bytes):
        detail = raw_detail.decode("utf-8", errors="replace").strip()
    else:
        detail = str(raw_detail or f"exit={result.returncode}").strip()
    return NotificationResult(
        "email",
        enabled=True,
        attempted=True,
        sent=False,
        detail=f"sendmail_rejected:{detail}",
    )


def send_email_via_mailapp(
    recipients: list[str],
    subject: str,
    body: str,
    rich_paragraphs: list[dict[str, object]] | None = None,
    attachment_specs: list[dict[str, object]] | None = None,
) -> NotificationResult:
    if not recipients:
        return NotificationResult("email", enabled=True, attempted=False, sent=False, detail="missing_recipients")

    recipient_lines = "\n".join(
        [
            f'make new to recipient at end of to recipients with properties {{address:"{apple_script_escape(recipient)}"}}'
            for recipient in recipients
        ]
    )
    paragraph_texts = [str(item.get("text") or "") for item in (rich_paragraphs or [])]
    if paragraph_texts:
        content_expr = apple_script_text_join(paragraph_texts)
    else:
        content_expr = f'"{apple_script_escape(body)}"'

    attachment_lines: list[str] = []
    for item in attachment_specs or []:
        path = item.get("path")
        after_paragraph = int(item.get("after_paragraph") or 1)
        if not isinstance(path, Path):
            continue
        attachment_lines.append(
            "tell content of newMessage\n"
            f'            make new attachment with properties {{file name:(POSIX file "{apple_script_escape(str(path.resolve()))}")}} '
            f"at after paragraph {after_paragraph}\n"
            "        end tell"
        )
    attachment_block = "\n        ".join(attachment_lines)

    script = f'''
tell application "Mail"
    activate
    set newMessage to make new outgoing message with properties {{subject:"{apple_script_escape(subject)}", content:{content_expr}}}
    tell newMessage
        {recipient_lines}
        {attachment_block}
        send
    end tell
end tell
'''.strip()
    mailapp_timeout = min(180, max(30, 15 + len(rich_paragraphs or []) // 4))

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=mailapp_timeout,
        )
    except subprocess.TimeoutExpired:
        return NotificationResult(
            "email",
            enabled=True,
            attempted=True,
            sent=False,
            detail=f"mailapp_delivery_uncertain:timeout_after_{mailapp_timeout}s",
        )
    except OSError as exc:
        return NotificationResult(
            "email",
            enabled=True,
            attempted=False,
            sent=False,
            detail=f"mailapp_pre_send_failed:{type(exc).__name__}: {exc}",
        )

    if result.returncode == 0:
        status_ok, status_detail = mailapp_message_status(subject)
        if not status_ok:
            return NotificationResult(
                "email",
                enabled=True,
                attempted=True,
                sent=False,
                detail=f"mailapp_delivery_uncertain:status_check_failed:{status_detail}",
            )
        if status_detail == "outgoing":
            return NotificationResult(
                "email",
                enabled=True,
                attempted=True,
                sent=False,
                detail="mailapp_delivery_uncertain:stuck_outgoing",
            )
        if status_detail == "drafts":
            return NotificationResult(
                "email",
                enabled=True,
                attempted=True,
                sent=False,
                detail="mailapp_delivery_uncertain:stuck_drafts",
            )
        return NotificationResult("email", enabled=True, attempted=True, sent=True, detail="sent_via_mailapp")

    detail = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
    return NotificationResult(
        "email",
        enabled=True,
        attempted=True,
        sent=False,
        detail=f"mailapp_delivery_uncertain:script_failed:{detail}",
    )


def mailapp_message_status(subject: str) -> tuple[bool, str]:
    time.sleep(3)

    script = f'''
tell application "Mail"
    repeat with m in outgoing messages
        try
            if (subject of m as text) is "{apple_script_escape(subject)}" then return "outgoing"
        end try
    end repeat
    try
        repeat with m in (every message of drafts mailbox)
            if (subject of m as text) is "{apple_script_escape(subject)}" then return "drafts"
        end repeat
    end try
    return "clear"
end tell
'''.strip()

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as exc:
        return False, str(exc)

    if result.returncode != 0:
        return False, (result.stderr or result.stdout or f"exit={result.returncode}").strip()

    return True, (result.stdout or "clear").strip() or "clear"


def prepare_email_delivery(
    config: dict[str, str],
    status: str,
    counts: dict[str, object],
    run_id: str,
    summary_path: Path,
    match_views: list[MatchView],
    watch_hits: list[WatchHit],
    run_dir: Path,
) -> dict[str, object]:
    mode = config.get("AUCTION_WATCH_EMAIL_MODE", "matches_or_failure")
    total_matches = total_match_count(counts)

    if not notification_mode_enabled(mode, status, total_matches):
        return {
            "version": 1,
            "runId": run_id,
            "enabled": False,
            "emailMode": mode,
            "method": config.get("AUCTION_WATCH_EMAIL_METHOD", "smtp").strip().lower(),
            "detail": f"mode={mode}",
        }

    recipients = parse_recipients(config.get("AUCTION_WATCH_EMAIL_TO", ""))
    prefix = config.get("AUCTION_WATCH_EMAIL_SUBJECT_PREFIX", "[Auction Watch]").strip()
    method = config.get("AUCTION_WATCH_EMAIL_METHOD", "smtp").strip().lower()
    app_base_url = config.get("AUCTION_WATCH_APP_BASE_URL", "").strip()

    subject = build_email_subject(prefix, status, total_matches, watch_hits)
    body = build_email_body(
        run_id,
        status,
        counts,
        summary_path,
        match_views,
        watch_hits,
        app_base_url=app_base_url,
    )
    rich_paragraphs = build_mailapp_rich_paragraphs(
        run_id,
        status,
        counts,
        summary_path,
        match_views,
        watch_hits,
        app_base_url=app_base_url,
    )
    attachment_specs = download_watch_images(run_dir, watch_hits) if method == "mailapp" else []
    html_match_views = resolve_mail_image_urls(match_views) if method in {"smtp", "sendmail"} else match_views
    hero_cid = ""
    hero_preview_src = ""
    if attachment_specs:
        hero_cid = str(attachment_specs[0].get("cid") or "").strip()
        hero_preview_src = str(attachment_specs[0].get("preview_src") or "").strip()

    html_body = build_newsletter_html(
        status,
        counts,
        html_match_views,
        watch_hits,
        hero_image_src=f"cid:{hero_cid}" if hero_cid else "",
        app_base_url=app_base_url,
    )
    preview_image_src = hero_preview_src
    if not preview_image_src and watch_hits:
        preview_image_src = (watch_hits[0].image_url or "").strip()
    preview_html = build_newsletter_html(
        status,
        counts,
        html_match_views,
        watch_hits,
        hero_image_src=preview_image_src,
        app_base_url=app_base_url,
    )
    write_newsletter_preview(run_dir / "newsletter-preview.html", preview_html)

    return {
        "version": 1,
        "runId": run_id,
        "enabled": True,
        "emailMode": mode,
        "method": method,
        "recipients": recipients,
        "subject": subject,
        "body": body,
        "htmlBody": html_body,
        "richParagraphs": rich_paragraphs,
        "attachments": [
            {
                **{key: value for key, value in item.items() if key != "path"},
                "path": str(item.get("path") or ""),
            }
            for item in attachment_specs
        ],
        "messageId": f"<auction-watch.{sanitize_run_id(run_id)}@consolas.local>",
        "dateHeader": format_datetime(datetime.now().astimezone()),
        "createdAt": now_iso(),
    }


def send_prepared_email(
    config: dict[str, str],
    prepared: dict[str, object],
) -> NotificationResult:
    if prepared.get("enabled") is not True:
        return NotificationResult(
            "email",
            enabled=False,
            attempted=False,
            sent=False,
            detail=str(prepared.get("detail") or "disabled"),
        )

    method = str(prepared.get("method") or "").strip().lower()
    recipients = [str(item).strip() for item in prepared.get("recipients") or [] if str(item).strip()]
    subject = str(prepared.get("subject") or "")
    body = str(prepared.get("body") or "")
    html_body = str(prepared.get("htmlBody") or "")
    rich_paragraphs = [
        item for item in prepared.get("richParagraphs") or [] if isinstance(item, dict)
    ]
    attachment_specs: list[dict[str, object]] = []
    for raw in prepared.get("attachments") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["path"] = Path(str(raw.get("path") or ""))
        attachment_specs.append(item)
    message_id = str(prepared.get("messageId") or "")
    date_header = str(prepared.get("dateHeader") or "")

    if method == "mailapp":
        return send_email_via_mailapp(
            recipients,
            subject,
            body,
            rich_paragraphs=rich_paragraphs,
            attachment_specs=attachment_specs,
        )
    if method == "sendmail":
        return send_email_via_sendmail(
            config,
            recipients,
            subject,
            body,
            html_body=html_body,
            attachment_specs=attachment_specs,
            message_id=message_id,
            date_header=date_header,
        )
    if method == "smtp":
        return send_email_via_smtp(
            config,
            recipients,
            subject,
            body,
            html_body=html_body,
            attachment_specs=attachment_specs,
            message_id=message_id,
            date_header=date_header,
        )

    return NotificationResult("email", enabled=True, attempted=False, sent=False, detail=f"unknown_method={method}")


def email_failure_is_ambiguous(result: NotificationResult) -> bool:
    """Return true unless a failed send is provably pre-send or rejected."""
    if not result.enabled or result.sent or not result.attempted:
        return False
    detail = result.detail.strip().lower()
    definitive_prefixes = (
        "smtp_pre_send_failed:",
        "smtp_rejected:",
        "sendmail_pre_send_failed:",
        "sendmail_rejected:",
        "mailapp_pre_send_failed:",
        "mailapp_rejected:",
    )
    return not detail.startswith(definitive_prefixes)


def send_email_notification(
    config: dict[str, str],
    status: str,
    counts: dict[str, object],
    run_id: str,
    summary_path: Path,
    match_views: list[MatchView],
    watch_hits: list[WatchHit],
    run_dir: Path,
) -> NotificationResult:
    prepared = prepare_email_delivery(
        config,
        status,
        counts,
        run_id,
        summary_path,
        match_views,
        watch_hits,
        run_dir,
    )
    return send_prepared_email(config, prepared)


def write_notification_log(path: Path, results: list[NotificationResult]) -> None:
    lines = [
        f"{item.channel}: enabled={item.enabled} attempted={item.attempted} sent={item.sent} detail={item.detail}"
        for item in results
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def required_email_delivery_failed(results: list[NotificationResult]) -> bool:
    return any(item.channel == "email" and item.enabled and not item.sent for item in results)


def load_delivery_outbox(path: Path | None = None) -> dict[str, object]:
    path = path or DELIVERY_OUTBOX_FILE
    payload = read_json_object(path)
    if payload.get("version") != DELIVERY_OUTBOX_VERSION or not isinstance(
        payload.get("items"), list
    ):
        return {"version": DELIVERY_OUTBOX_VERSION, "items": []}
    return payload


def save_delivery_outbox(payload: dict[str, object], path: Path | None = None) -> None:
    path = path or DELIVERY_OUTBOX_FILE
    payload["version"] = DELIVERY_OUTBOX_VERSION
    payload["updatedAt"] = now_iso()
    atomic_write_json(path, payload)


def delivery_outbox_item(
    run_id: str,
    path: Path | None = None,
) -> dict[str, object] | None:
    for item in load_delivery_outbox(path).get("items") or []:
        if isinstance(item, dict) and str(item.get("runId") or "") == run_id:
            return item
    return None


def pending_delivery_items(
    *,
    observed_at: datetime | None = None,
    path: Path | None = None,
    due_only: bool = True,
) -> list[dict[str, object]]:
    now = observed_at or datetime.now().astimezone()
    pending: list[dict[str, object]] = []
    for item in recover_interrupted_delivery_outbox(path).get("items") or []:
        if not isinstance(item, dict) or item.get("status") != "pending":
            continue
        next_attempt = parse_iso_datetime(item.get("nextAttemptAt"))
        if due_only and next_attempt is not None and next_attempt > now:
            continue
        pending.append(item)
    return sorted(
        pending,
        key=lambda item: (
            str(item.get("nextAttemptAt") or ""),
            str(item.get("createdAt") or ""),
        ),
    )


def record_delivery_outbox(
    run_id: str,
    run_dir: Path,
    *,
    status: str,
    detail: str,
    schedule_date: str = "",
    schedule_slots: list[str] | None = None,
    manual_request_id: str = "",
    email_message_id: str = "",
    attempted: bool = False,
    path: Path | None = None,
) -> dict[str, object]:
    payload = load_delivery_outbox(path)
    items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    existing = next((item for item in items if item.get("runId") == run_id), None)
    if existing is None:
        existing = {
            "runId": run_id,
            "runDir": str(run_dir),
            "createdAt": now_iso(),
            "attempts": 0,
        }
        items.append(existing)

    attempts = int(existing.get("attempts") or 0) + (1 if attempted else 0)
    timestamp = now_iso()
    existing.update(
        {
            "status": status,
            "detail": detail[:1000],
            "attempts": attempts,
            "lastAttemptAt": now_iso() if attempted else existing.get("lastAttemptAt"),
            "scheduleDate": schedule_date or existing.get("scheduleDate") or "",
            "scheduleSlots": sorted(
                set(schedule_slots or []) | set(existing.get("scheduleSlots") or [])
            ),
            "manualRequestId": manual_request_id or existing.get("manualRequestId") or "",
            "emailMessageId": email_message_id or existing.get("emailMessageId") or "",
        }
    )
    if status == "pending":
        delay = DELIVERY_BACKOFF_SECONDS[min(max(attempts - 1, 0), len(DELIVERY_BACKOFF_SECONDS) - 1)]
        existing["nextAttemptAt"] = (
            datetime.now().astimezone() + timedelta(seconds=delay)
        ).isoformat(timespec="seconds")
        existing.pop("sendingPid", None)
        existing.pop("sendingAt", None)
        existing.pop("completedAt", None)
        existing.pop("uncertainAt", None)
        existing.pop("failedAt", None)
    elif status == "sending":
        existing["nextAttemptAt"] = None
        existing["sendingAt"] = timestamp
        existing["sendingPid"] = os.getpid()
        existing.pop("completedAt", None)
        existing.pop("uncertainAt", None)
        existing.pop("failedAt", None)
    elif status == "uncertain":
        existing["nextAttemptAt"] = None
        existing["uncertainAt"] = timestamp
        existing.pop("sendingPid", None)
        existing.pop("completedAt", None)
        existing.pop("failedAt", None)
    elif status == "completed":
        existing["nextAttemptAt"] = None
        existing["completedAt"] = timestamp
        existing.pop("sendingPid", None)
        existing.pop("sendingAt", None)
        existing.pop("uncertainAt", None)
        existing.pop("failedAt", None)
    else:
        existing["nextAttemptAt"] = None
        existing["failedAt"] = timestamp
        existing.pop("sendingPid", None)
        existing.pop("sendingAt", None)
        existing.pop("completedAt", None)

    payload["items"] = items[-100:]
    save_delivery_outbox(payload, path)
    return existing


def process_is_running(raw_pid: object) -> bool:
    try:
        pid = int(raw_pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def delivery_publication_from_artifacts(
    manifest: dict[str, object],
    metadata: dict[str, object],
) -> PublicationResult:
    publication_payload = (
        manifest.get("publication") if isinstance(manifest.get("publication"), dict) else {}
    )
    snapshot_payload = metadata.get("snapshot") if isinstance(metadata.get("snapshot"), dict) else {}
    return PublicationResult(
        mode=str(
            publication_payload.get("mode")
            or snapshot_payload.get("mode")
            or manifest.get("publicationMode")
            or ""
        ),
        status=str(
            publication_payload.get("status")
            or snapshot_payload.get("status")
            or metadata.get("snapshotStatus")
            or "failed"
        ),
        configured=bool(publication_payload.get("configured") or snapshot_payload.get("configured")),
        attempted=bool(publication_payload.get("attempted") or snapshot_payload.get("attempted")),
        detail=str(
            publication_payload.get("detail")
            or snapshot_payload.get("detail")
            or ""
        ),
        run_id=str(manifest.get("runId") or metadata.get("run_id") or ""),
        snapshot_hash=str(
            publication_payload.get("snapshotHash") or manifest.get("snapshotHash") or ""
        ),
        generated_at=str(publication_payload.get("generatedAt") or ""),
        canonical_verified=bool(publication_payload.get("canonicalVerified")),
    )


def reconcile_terminal_delivery_metadata(payload: dict[str, object]) -> None:
    """Repair run.json after a crash between terminal outbox and metadata writes."""
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        terminal_status = str(item.get("status") or "").strip().lower()
        if terminal_status not in {"completed", "failed", "uncertain"}:
            continue
        run_dir = Path(str(item.get("runDir") or ""))
        metadata = read_json_object(run_dir / "run.json")
        if not metadata:
            continue
        manifest = read_json_object(run_dir / DELIVERY_MANIFEST_FILENAME)
        email_payload = manifest.get("email") if isinstance(manifest.get("email"), dict) else {}
        email_enabled = email_payload.get("enabled") is True
        current_delivery = metadata.get("delivery") if isinstance(metadata.get("delivery"), dict) else {}
        if terminal_status == "uncertain":
            desired_email_status = "uncertain"
        elif terminal_status == "failed":
            desired_email_status = "failed" if email_enabled else "disabled"
        else:
            desired_email_status = "sent" if email_enabled else "disabled"
        if (
            str(current_delivery.get("status") or "") == terminal_status
            and str(metadata.get("emailStatus") or "") == desired_email_status
        ):
            continue
        email_result = NotificationResult(
            "email",
            enabled=email_enabled,
            attempted=True,
            sent=terminal_status == "completed" and email_enabled,
            detail=str(item.get("detail") or ""),
        )
        update_delivery_metadata(
            run_dir,
            delivery_publication_from_artifacts(manifest, metadata),
            email_result,
            pending=False,
            outbox_item=item,
            terminal_error=terminal_status != "completed",
            email_status_override="uncertain" if terminal_status == "uncertain" else "",
        )


def recover_interrupted_delivery_outbox(path: Path | None = None) -> dict[str, object]:
    """Resolve durable `sending` states without ever auto-resending ambiguity."""
    path = path or DELIVERY_OUTBOX_FILE
    payload = load_delivery_outbox(path)
    items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    changed = False
    timestamp = now_iso()
    metadata_updates: list[
        tuple[Path, dict[str, object], dict[str, object], dict[str, object]]
    ] = []
    for item in items:
        if item.get("status") != "sending" or process_is_running(item.get("sendingPid")):
            continue

        run_dir = Path(str(item.get("runDir") or ""))
        manifest = read_json_object(run_dir / DELIVERY_MANIFEST_FILENAME)
        email_delivery = (
            manifest.get("emailDelivery")
            if isinstance(manifest.get("emailDelivery"), dict)
            else {}
        )
        persisted_status = str(email_delivery.get("status") or "").strip().lower()
        if persisted_status == "sent":
            item.update(
                {
                    "status": "completed",
                    "detail": "recovered_confirmed_email_send",
                    "completedAt": str(email_delivery.get("finishedAt") or timestamp),
                    "nextAttemptAt": None,
                }
            )
        elif persisted_status == "failed":
            item.update(
                {
                    "status": "pending",
                    "detail": str(email_delivery.get("detail") or "recovered_failed_email_send")[:1000],
                    "nextAttemptAt": timestamp,
                }
            )
        else:
            item.update(
                {
                    "status": "uncertain",
                    "detail": (
                        "email_send_interrupted_outcome_unknown; "
                        "explicit_retry_may_duplicate"
                    ),
                    "uncertainAt": timestamp,
                    "nextAttemptAt": None,
                }
            )
        item.pop("sendingPid", None)
        metadata_updates.append((run_dir, manifest, email_delivery, item))
        changed = True

    if changed:
        payload["items"] = items
        save_delivery_outbox(payload, path)
        for run_dir, manifest, email_delivery, item in metadata_updates:
            metadata = read_json_object(run_dir / "run.json")
            if not metadata:
                continue
            publication_payload = (
                manifest.get("publication")
                if isinstance(manifest.get("publication"), dict)
                else {}
            )
            snapshot_payload = (
                metadata.get("snapshot")
                if isinstance(metadata.get("snapshot"), dict)
                else {}
            )
            publication = PublicationResult(
                mode=str(
                    publication_payload.get("mode")
                    or snapshot_payload.get("mode")
                    or manifest.get("publicationMode")
                    or ""
                ),
                status=str(
                    publication_payload.get("status")
                    or snapshot_payload.get("status")
                    or metadata.get("snapshotStatus")
                    or "failed"
                ),
                configured=bool(
                    publication_payload.get("configured")
                    or snapshot_payload.get("configured")
                ),
                attempted=bool(
                    publication_payload.get("attempted")
                    or snapshot_payload.get("attempted")
                ),
                detail=str(
                    publication_payload.get("detail")
                    or snapshot_payload.get("detail")
                    or ""
                ),
                run_id=str(manifest.get("runId") or metadata.get("run_id") or ""),
                snapshot_hash=str(
                    publication_payload.get("snapshotHash")
                    or manifest.get("snapshotHash")
                    or ""
                ),
                generated_at=str(publication_payload.get("generatedAt") or ""),
                canonical_verified=bool(publication_payload.get("canonicalVerified")),
            )
            recovered_status = str(item.get("status") or "")
            email_enabled = bool((manifest.get("email") or {}).get("enabled")) if isinstance(
                manifest.get("email"), dict
            ) else True
            email_result = NotificationResult(
                "email",
                enabled=email_enabled,
                attempted=True,
                sent=recovered_status == "completed" and email_enabled,
                detail=str(item.get("detail") or email_delivery.get("detail") or ""),
            )
            if recovered_status == "completed":
                update_delivery_metadata(
                    run_dir,
                    publication,
                    email_result,
                    pending=False,
                    outbox_item=item,
                )
            elif recovered_status == "pending":
                update_delivery_metadata(
                    run_dir,
                    publication,
                    email_result,
                    pending=True,
                    outbox_item=item,
                )
            else:
                update_delivery_metadata(
                    run_dir,
                    publication,
                    email_result,
                    pending=False,
                    outbox_item=item,
                    terminal_error=True,
                    email_status_override="uncertain",
                )
    reconcile_terminal_delivery_metadata(payload)
    return payload


def write_summary(
    path: Path,
    run_id: str,
    started_at: str,
    finished_at: str,
    status: str,
    steps: list[StepResult],
    counts: dict[str, object],
    bavastro_auction_rows: list[dict[str, str]],
    castells_discovery_rows: list[dict[str, str]],
    new_bavastro_auction_ids: list[int],
    new_castells_remate_ids: list[int],
    bavastro_match_rows: list[dict[str, str]],
    castells_rows: list[dict[str, str]],
    match_views: list[MatchView],
    extra_status_payload: dict[str, object],
    watch_hits: list[WatchHit],
    latest_matchful_run_dir: Path | None,
    latest_bavastro_match_run_dir: Path | None,
    latest_castells_match_run_dir: Path | None,
    bavastro_auctions_csv: Path,
    bavastro_matches_csv: Path,
    castells_auctions_csv: Path,
    castells_csv: Path,
    castells_md: Path,
    extra_matches_csv: Path,
    extra_status_json: Path,
    newsletter_preview_html: Path,
) -> None:
    bavastro_match_auction_count = unique_count(bavastro_match_rows, "auction_id")
    castells_match_remate_count = unique_count(castells_rows, "remate_id")
    new_bavastro_ids = set(new_bavastro_auction_ids)
    new_castells_ids = set(new_castells_remate_ids)
    latest_matches_summary_path = latest_matchful_run_dir / "summary.md" if latest_matchful_run_dir else None
    latest_bavastro_summary_path = (
        latest_bavastro_match_run_dir / "summary.md" if latest_bavastro_match_run_dir else None
    )
    latest_castells_summary_path = (
        latest_castells_match_run_dir / "summary.md" if latest_castells_match_run_dir else None
    )

    lines = [
        "# Auction Watch",
        "",
        f"- Run ID: `{run_id}`",
        f"- Started: `{started_at}`",
        f"- Finished: `{finished_at}`",
        f"- Status: `{status}`",
        f"- Output dir: `{path.parent}`",
        "",
        "## Abrir rapido",
        "",
        f"- [Resumen actual]({path.resolve()})",
        (
            f"- {markdown_file_link('Preview mail HTML', newsletter_preview_html)}"
            if newsletter_preview_html.exists()
            else "- Preview mail HTML: no generado en esta corrida"
        ),
        f"- {markdown_file_link('Bavastro activos CSV', bavastro_auctions_csv)}",
        (
            f"- {markdown_file_link('Bavastro matches CSV', bavastro_matches_csv)}"
            if bavastro_matches_csv.exists()
            else "- Bavastro matches CSV: no generado en esta corrida"
        ),
        f"- {markdown_file_link('Castells activos CSV', castells_auctions_csv)}",
        (
            f"- {markdown_file_link('Castells matches CSV', castells_csv)}"
            if castells_csv.exists()
            else "- Castells matches CSV: no generado en esta corrida"
        ),
        (
            f"- {markdown_file_link('Castells markdown legible', castells_md)}"
            if castells_md.exists()
            else "- Castells markdown legible: no generado en esta corrida"
        ),
        (
            f"- {markdown_file_link('Matches de fuentes adicionales', extra_matches_csv)}"
            if extra_matches_csv.exists()
            else "- Matches de fuentes adicionales: no generado en esta corrida"
        ),
        (
            f"- {markdown_file_link('Estado de fuentes adicionales', extra_status_json)}"
            if extra_status_json.exists()
            else "- Estado de fuentes adicionales: no generado en esta corrida"
        ),
    ]

    steps_by_name = {step.name: step for step in steps}
    lines.extend(
        [
            "",
            "## Totals",
            "",
            "| Source | Status | Active groups | New groups | Matches activos | Matches nuevos | Primary output |",
            "|---|---|---:|---:|---:|---:|---|",
            (
                f"| Bavastro discovery | `{source_status(steps_by_name['bavastro_discovery'])}` | "
                f"{counts['bavastro_active_auctions']} | {counts['bavastro_new_auctions']} | "
                f"- | - | `auctions_bavastro_matches.csv` |"
            ),
            (
                f"| Bavastro matches | `{source_status(steps_by_name['bavastro_matches'])}` | "
                f"{bavastro_match_auction_count} | {counts['bavastro_new_auctions']} | "
                f"{counts['bavastro_matches']} | {counts['bavastro_new_matches']} | `consolas_bavastro_matches.csv` |"
            ),
            (
                f"| Castells discovery | `{source_status(steps_by_name['castells_discovery'])}` | "
                f"{counts['castells_active_remates']} | {counts['castells_new_remates']} | "
                f"- | - | `consolas_castells_auctions.csv` |"
            ),
            (
                f"| Castells matches | `{source_status(steps_by_name['castells_matches'])}` | "
                f"{castells_match_remate_count} | {counts['castells_new_remates']} | "
                f"{counts['castells_matches']} | {counts['castells_new_matches']} | `consolas_castells_matches.csv` |"
            ),
        ]
    )

    raw_extra_sources = extra_status_payload.get("sources")
    if isinstance(raw_extra_sources, list):
        for item in raw_extra_sources:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or display_source_name(str(item.get("source_id") or "")))
            lines.append(
                f"| {escape_table_cell(label)} | `{escape_table_cell(item.get('status', 'unknown'))}` | "
                f"{escape_table_cell(item.get('groups', 0))} | - | "
                f"{escape_table_cell(item.get('matches', 0))} | - | `{EXTRA_MATCHES_FILENAME}` |"
            )

    lines.extend(
        [
            "",
            "## Filtro personal",
            "",
            f"- Matches detectados antes del filtro: {counts.get('detected_matches', counts.get('total_matches', 0))}",
            f"- Descartados ocultos en esta corrida: {counts.get('dismissed_matches', 0)}",
            f"- Matches visibles en mail y app: {counts.get('total_matches', 0)}",
        ]
    )

    lines.extend(
        [
            "",
            "## Steps",
            "",
            "| Step | Status | Exit | Stdout | Stderr |",
            "|---|---|---:|---|---|",
        ]
    )

    for step in steps:
        exit_code = "" if step.exit_code is None else str(step.exit_code)
        step_status = step.status
        if step.skipped_reason:
            step_status = f"{step.status} ({step.skipped_reason})"
        lines.append(
            f"| {step.name} | `{step_status}` | {exit_code} | `{step.stdout_path}` | `{step.stderr_path}` |"
        )

    if watch_hits:
        lines.extend(
            [
                "",
                "## Seguimiento prioritario",
                "",
                "| Item | Urgencia | Falta | Cierre | Precio | Lote | Grupo |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        for hit in watch_hits:
            lines.append(
                f"| {escape_table_cell(hit.label)} | "
                f"{escape_table_cell(hit.urgency_label)} | "
                f"{escape_table_cell(hit.remaining_text)} | "
                f"{escape_table_cell(hit.closing_at_display)} | "
                f"{escape_table_cell(hit.price_label)} | "
                f"{markdown_link(hit.lot_label, hit.lot_url)} | "
                f"{markdown_link(hit.group_label, hit.group_url)} |"
            )

    lines.extend(["", "## Matches activos", ""])

    if match_views:
        lines.extend(
            [
                "| Fuente | Score | Keywords | Riesgos | Descripcion | Precio | Fecha | Lote | Remate |",
                "|---|---:|---|---|---|---|---|---|---|",
            ]
        )
        for item in match_views:
            lines.append(
                f"| {escape_table_cell(item.source_label)} | "
                f"{item.score} | "
                f"{escape_table_cell(item.matched_keywords or '-')} | "
                f"{escape_table_cell(item.risk_flags or '-')} | "
                f"{escape_table_cell(shorten_text(item.title, 110))} | "
                f"{escape_table_cell(item.price_label)} | "
                f"{escape_table_cell(item.closing_at_display)} | "
                f"{markdown_link('Ver lote', item.lot_url)} | "
                f"{markdown_link('Ver remate', item.group_url)} |"
            )
    else:
        lines.append("- No se encontraron matches activos en esta corrida.")

    lines.extend(
        [
            "",
            "## Bavastro activos",
            "",
            "| Estado | Cierre | Subasta |",
            "|---|---|---|",
        ]
    )
    for row in bavastro_auction_rows:
        raw_id = str(row.get("id") or "").strip()
        state_label = "nuevo" if raw_id.isdigit() and int(raw_id) in new_bavastro_ids else "ya visto"
        auction_url = bavastro_public_auction_url(raw_id, str(row.get("url") or ""))
        lines.append(
            f"| {state_label} | {escape_table_cell(row.get('end_date', ''))} | "
            f"{markdown_link(shorten_text(row.get('name', raw_id), 80), auction_url)} |"
        )

    lines.extend(
        [
            "",
            "## Castells activos",
            "",
            "| Estado | Cierre | Remate |",
            "|---|---|---|",
        ]
    )
    for row in castells_discovery_rows:
        raw_id = str(row.get("remate_id") or "").strip()
        state_label = "nuevo" if raw_id.isdigit() and int(raw_id) in new_castells_ids else "ya visto"
        lines.append(
            f"| {state_label} | {escape_table_cell(row.get('end_date', ''))} | "
            f"{markdown_link(shorten_text(row.get('name', raw_id), 80), row.get('url', ''))} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sync_latest(run_dir: Path) -> None:
    tmp_dir = RUNS_DIR / ".latest.tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    shutil.copytree(run_dir, tmp_dir)
    if LATEST_DIR.exists():
        shutil.rmtree(LATEST_DIR)
    tmp_dir.rename(LATEST_DIR)


def sync_latest_matches(run_dir: Path) -> None:
    tmp_dir = RUNS_DIR / ".latest-matches.tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    shutil.copytree(run_dir, tmp_dir)
    if LATEST_MATCHES_DIR.exists():
        shutil.rmtree(LATEST_MATCHES_DIR)
    tmp_dir.rename(LATEST_MATCHES_DIR)


def find_latest_matchful_run(
    exclude_run_dir: Path | None = None,
    count_key: str | None = None,
) -> Path | None:
    for run_dir in list_run_dirs():
        if exclude_run_dir and run_dir == exclude_run_dir:
            continue

        if count_key == "bavastro_matches":
            total_matches = len(validated_match_rows_for_run(run_dir, "bavastro"))
        elif count_key == "castells_matches":
            total_matches = len(validated_match_rows_for_run(run_dir, "castells"))
        else:
            total_matches = len(validated_match_rows_for_run(run_dir, "bavastro")) + len(
                validated_match_rows_for_run(run_dir, "castells")
            ) + len(read_csv_rows(run_dir / EXTRA_MATCHES_FILENAME))

        if total_matches > 0:
            return run_dir

    return None


def refresh_latest_matches_mirror(current_run_dir: Path, has_matches: bool, latest_matchful_run_dir: Path | None) -> None:
    if has_matches:
        sync_latest_matches(current_run_dir)
        return

    if latest_matchful_run_dir and latest_matchful_run_dir.exists():
        sync_latest_matches(latest_matchful_run_dir)


def should_export_web_snapshot(status: str, has_matches: bool) -> bool:
    return status == "success" or has_matches


def prune_runs(keep_runs: int) -> None:
    keep_runs = max(1, keep_runs)
    run_dirs = list_run_dirs()
    protected_run_ids = {
        str(item.get("runId") or "")
        for item in pending_delivery_items(due_only=False)
    }
    removable = [
        run_dir
        for run_dir in run_dirs[keep_runs:]
        if run_dir.name not in protected_run_ids
    ]
    for stale_dir in removable:
        shutil.rmtree(stale_dir)


def export_web_snapshot(
    input_dir: Path,
    output_path: Path,
) -> tuple[bool, str]:
    if not WEB_EXPORT_SCRIPT.exists():
        return False, f"missing_export_script:{WEB_EXPORT_SCRIPT}"

    try:
        result = subprocess.run(
            [
                str(PYTHON_BIN),
                str(WEB_EXPORT_SCRIPT),
                "--input-dir",
                str(input_dir),
                "--output",
                str(output_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as exc:
        return False, str(exc)

    if result.returncode == 0:
        if RUNTIME_ROOT != AGENT_DIR:
            detail = (result.stdout or "").strip() or "ok"
            return True, f"{detail}; local web runtime fallback skipped"
        try:
            atomic_write_text(
                REPO_ROOT / "web" / "runtime" / "auction-watch.json",
                output_path.read_text(encoding="utf-8"),
            )
        except OSError as exc:
            return False, f"web_snapshot_sync_failed:{exc}"
        detail = (result.stdout or "").strip() or "ok"
        return True, detail

    detail = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
    return False, detail


def snapshot_endpoint(config: dict[str, str]) -> str:
    explicit = config.get("AUCTION_WATCH_SNAPSHOT_URL", "").strip()
    if explicit:
        return explicit
    app_base_url = config.get("AUCTION_WATCH_APP_BASE_URL", "").strip().rstrip("/")
    return f"{app_base_url}/api/auction-watch/snapshot" if app_base_url else ""


def canonical_snapshot_endpoint(config: dict[str, str]) -> str:
    explicit = config.get("AUCTION_WATCH_CANONICAL_SNAPSHOT_URL", "").strip()
    if explicit:
        return explicit
    app_base_url = config.get("AUCTION_WATCH_APP_BASE_URL", "").strip().rstrip("/")
    if app_base_url:
        return f"{app_base_url}/api/auction-watch"
    publish_endpoint = snapshot_endpoint(config)
    if publish_endpoint.endswith("/snapshot"):
        return publish_endpoint.removesuffix("/snapshot")
    return ""


def publish_web_snapshot(
    config: dict[str, str],
    snapshot_path: Path | None = None,
) -> PublicationResult:
    """Publish one immutable run snapshot and verify the backend receipt."""
    mode = publication_mode(config)
    if mode not in PUBLICATION_MODES:
        return PublicationResult(
            mode=mode,
            status="failed",
            configured=False,
            attempted=False,
            detail=f"invalid_publication_mode:{mode}",
        )

    path = snapshot_path or (REPO_ROOT / "web" / "runtime" / "auction-watch.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return PublicationResult(
            mode=mode,
            status="failed",
            configured=False,
            attempted=False,
            detail=f"snapshot_read_failed:{type(exc).__name__}: {exc}",
        )
    if not isinstance(payload, dict):
        return PublicationResult(mode, "failed", False, False, "snapshot_not_an_object")

    run_id = str(payload.get("runId") or "").strip()
    generated_at = str(payload.get("generatedAt") or "").strip()
    payload_hash = snapshot_payload_hash(payload)
    if mode == "local-only":
        return PublicationResult(
            mode=mode,
            status="skipped",
            configured=False,
            attempted=False,
            detail="local_only",
            run_id=run_id,
            snapshot_hash=payload_hash,
            generated_at=generated_at,
        )

    endpoint = snapshot_endpoint(config)
    if not endpoint:
        return PublicationResult(
            mode=mode,
            status="failed",
            configured=False,
            attempted=False,
            detail="missing_snapshot_endpoint",
            run_id=run_id,
            snapshot_hash=payload_hash,
            generated_at=generated_at,
        )

    try:
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AuctionWatch/1.0",
                "X-Consolas-Auction-Watch": "1",
                "X-Auction-Watch-Snapshot-Hash": payload_hash,
            },
            method="POST",
        )
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        receipt = result.get("receipt") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(receipt, dict):
            raise ValueError("missing_publish_receipt")
        if str(receipt.get("runId") or "") != run_id:
            raise ValueError("receipt_run_id_mismatch")
        if str(receipt.get("snapshotHash") or "") != payload_hash:
            raise ValueError("receipt_snapshot_hash_mismatch")
        if str(receipt.get("generatedAt") or "") != generated_at:
            raise ValueError("receipt_generated_at_mismatch")

        canonical_verified = False
        canonical: dict[str, object] | None = None
        canonical_endpoint = canonical_snapshot_endpoint(config)
        if not canonical_endpoint:
            raise ValueError("missing_canonical_snapshot_endpoint")
        canonical_request = Request(
            canonical_endpoint,
            headers={"Accept": "application/json", "User-Agent": "AuctionWatch/1.0"},
        )
        with urlopen(canonical_request, timeout=15) as response:
            canonical_payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(canonical_payload, dict):
            raise ValueError("canonical_snapshot_not_an_object")
        canonical = canonical_payload
        canonical_sync = (
            canonical.get("sync") if isinstance(canonical.get("sync"), dict) else {}
        )
        canonical_run_id = str(canonical_sync.get("runId") or canonical.get("runId") or "")
        canonical_hash = str(
            canonical_sync.get("snapshotHash") or canonical.get("snapshotHash") or ""
        )
        if canonical_run_id != run_id:
            raise ValueError("canonical_run_id_mismatch")
        if canonical_hash != payload_hash:
            raise ValueError("canonical_snapshot_hash_mismatch")
        if str(canonical_sync.get("status") or "") != "current":
            raise ValueError("canonical_snapshot_not_current")
        canonical_verified = True

        return PublicationResult(
            mode=mode,
            status="published",
            configured=True,
            attempted=True,
            detail=endpoint,
            run_id=run_id,
            snapshot_hash=payload_hash,
            generated_at=generated_at,
            canonical_verified=canonical_verified,
            canonical_snapshot=canonical,
        )
    except Exception as exc:
        return PublicationResult(
            mode=mode,
            status="failed",
            configured=True,
            attempted=True,
            detail=f"{type(exc).__name__}: {exc}",
            run_id=run_id,
            snapshot_hash=payload_hash,
            generated_at=generated_at,
        )


def publication_failure_is_superseded(
    config: dict[str, str],
    publication: PublicationResult,
    run_id: str,
) -> bool:
    """A newer verified publication makes an older retry terminal, not pending."""
    if publication.mode != "ha-required" or publication.status != "failed":
        return False
    if "409" not in publication.detail and "conflict" not in publication.detail.lower():
        return False
    endpoint = canonical_snapshot_endpoint(config)
    if not endpoint:
        return False
    try:
        request = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "AuctionWatch/1.0"})
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        sync = payload.get("sync") if isinstance(payload, dict) and isinstance(payload.get("sync"), dict) else {}
        current_run_id = str(sync.get("runId") or payload.get("runId") or "").strip()
        return bool(
            current_run_id
            and current_run_id != run_id
            and sync.get("status") in {"current", "stale"}
            and sync.get("source") == "server"
            and str(sync.get("acceptedAt") or "").strip()
        )
    except Exception:
        return False


def email_status_from_result(result: NotificationResult, *, pending: bool = False) -> str:
    if not result.enabled:
        return "disabled"
    if result.sent:
        return "sent"
    return "pending" if pending else "failed"


def compute_overall_status(scan_status: str, snapshot_status: str, email_status: str) -> str:
    scan_status = canonical_scan_status(scan_status)
    if snapshot_status == "failed" or email_status == "pending":
        return "delivery_pending"
    if email_status in {"failed", "uncertain"} or scan_status == "failed":
        return "failed"
    if scan_status == "partial":
        return "degraded"
    return "completed"


def sync_latest_if_current(run_dir: Path) -> None:
    latest_metadata = read_json_object(LATEST_DIR / "run.json")
    current_metadata = read_json_object(run_dir / "run.json")
    latest_run_id = str(latest_metadata.get("run_id") or latest_metadata.get("runId") or "")
    current_run_id = str(current_metadata.get("run_id") or current_metadata.get("runId") or "")
    latest_started = str(latest_metadata.get("started_at") or "")
    current_started = str(current_metadata.get("started_at") or "")
    if (
        not latest_run_id
        or latest_run_id == current_run_id
        or (current_started and current_started >= latest_started)
    ):
        sync_latest(run_dir)


def update_delivery_metadata(
    run_dir: Path,
    publication: PublicationResult,
    email_result: NotificationResult,
    *,
    pending: bool,
    outbox_item: dict[str, object] | None,
    terminal_error: bool = False,
    email_status_override: str = "",
) -> int:
    metadata_path = run_dir / "run.json"
    metadata = read_json_object(metadata_path)
    scan_status = canonical_scan_status(
        metadata.get("scanStatus") or metadata.get("status") or "failed"
    )
    email_status = email_status_from_result(email_result, pending=pending)
    if email_status_override:
        email_status = email_status_override
    if terminal_error and not email_status_override and email_result.enabled and not email_result.sent:
        email_status = "failed"
    snapshot_status = publication.status
    overall_status = compute_overall_status(scan_status, snapshot_status, email_status)
    if terminal_error:
        overall_status = "failed"
    if pending:
        exit_code = 2
        completed_at = None
    elif overall_status in {"completed", "degraded"}:
        exit_code = 0
        completed_at = now_iso()
    else:
        exit_code = 1
        completed_at = now_iso()

    notifications = [
        item
        for item in metadata.get("notifications") or []
        if isinstance(item, dict) and item.get("channel") != "email"
    ]
    notifications.append(asdict(email_result))
    metadata.update(
        {
            "scanStatus": scan_status,
            "snapshotStatus": snapshot_status,
            "emailStatus": email_status,
            "overallStatus": overall_status,
            "completedAt": completed_at,
            "exitCode": exit_code,
            "snapshotHash": publication.snapshot_hash,
            "snapshot": {
                "mode": publication.mode,
                "status": publication.status,
                "configured": publication.configured,
                "attempted": publication.attempted,
                "published": publication.published,
                "detail": publication.detail,
                "runId": publication.run_id,
                "snapshotHash": publication.snapshot_hash,
                "generatedAt": publication.generated_at,
                "canonicalVerified": publication.canonical_verified,
            },
            "notifications": notifications,
            "delivery": dict(outbox_item or {}),
        }
    )
    write_json(metadata_path, metadata)
    write_notification_log(run_dir / "logs" / "notifications.log", [
        NotificationResult(
            str(item.get("channel") or ""),
            bool(item.get("enabled")),
            bool(item.get("attempted")),
            bool(item.get("sent")),
            str(item.get("detail") or ""),
        )
        for item in notifications
    ])
    sync_latest_if_current(run_dir)
    return exit_code


def prepare_canonical_email_delivery(
    config: dict[str, str],
    manifest: dict[str, object],
    publication: PublicationResult,
    run_dir: Path,
) -> dict[str, object]:
    """Rebuild mail from the HA-visible subset after receipt verification."""
    prepared = manifest.get("email") if isinstance(manifest.get("email"), dict) else {}
    if publication.mode != "ha-required":
        return dict(prepared)
    if not publication.canonical_verified or not isinstance(publication.canonical_snapshot, dict):
        raise ValueError("canonical_snapshot_not_verified_for_email")

    source = manifest.get("emailSource")
    if not isinstance(source, dict):
        raise ValueError("delivery_manifest_missing_email_source")
    raw_views = source.get("matchViews")
    raw_hits = source.get("watchHits")
    raw_counts = source.get("counts")
    if not isinstance(raw_views, list) or not isinstance(raw_hits, list) or not isinstance(raw_counts, dict):
        raise ValueError("delivery_manifest_invalid_email_source")

    try:
        match_views = [MatchView(**item) for item in raw_views if isinstance(item, dict)]
        watch_hits = [WatchHit(**item) for item in raw_hits if isinstance(item, dict)]
    except TypeError as exc:
        raise ValueError(f"delivery_manifest_invalid_email_items:{exc}") from exc

    canonical_matches = publication.canonical_snapshot.get("matches")
    if not isinstance(canonical_matches, list):
        raise ValueError("canonical_snapshot_matches_not_a_list")
    visible_keys = {
        (
            str(item.get("source") or item.get("sourceId") or "").strip().lower(),
            str(item.get("lotId") or "").strip(),
        )
        for item in canonical_matches
        if isinstance(item, dict)
        and str(item.get("source") or item.get("sourceId") or "").strip()
        and str(item.get("lotId") or "").strip()
    }
    match_views = [
        item for item in match_views if match_view_dismissal_key(item) in visible_keys
    ]
    watch_hits = [
        item for item in watch_hits if watch_hit_dismissal_key(item) in visible_keys
    ]

    counts = dict(raw_counts)
    canonical_counts = publication.canonical_snapshot.get("counts")
    if isinstance(canonical_counts, dict):
        counts.update(canonical_counts)
    counts["total_matches"] = len(match_views)
    source_counts: dict[str, int] = {}
    for item in match_views:
        source_counts[item.source_id] = source_counts.get(item.source_id, 0) + 1
    counts["bavastro_matches"] = source_counts.get("bavastro", 0)
    counts["castells_matches"] = source_counts.get("castells", 0)
    extra_counts = {
        source_id: count
        for source_id, count in source_counts.items()
        if source_id not in {"bavastro", "castells"}
    }
    counts["extra_matches"] = sum(extra_counts.values())
    counts["extra_matches_by_source"] = extra_counts
    detected = int(counts.get("detected_matches") or len(raw_views))
    counts["detected_matches"] = detected
    counts["dismissed_matches"] = max(0, detected - len(match_views))

    summary_path = Path(str(source.get("summaryPath") or run_dir / "summary.md"))
    try:
        summary_path.resolve().relative_to(run_dir.resolve())
    except ValueError as exc:
        raise ValueError("delivery_manifest_summary_outside_run") from exc
    return prepare_email_delivery(
        config,
        str(source.get("status") or "failure"),
        counts,
        str(manifest.get("runId") or ""),
        summary_path,
        match_views,
        watch_hits,
        run_dir,
    )


def attempt_delivery_for_run(
    run_dir: Path,
    config: dict[str, str],
    *,
    force_uncertain_email_retry: bool = False,
) -> int:
    delivery_path = run_dir / DELIVERY_MANIFEST_FILENAME
    manifest = read_json_object(delivery_path)
    metadata = read_json_object(run_dir / "run.json")
    run_id = str(manifest.get("runId") or metadata.get("run_id") or "").strip()
    prepared = manifest.get("email") if isinstance(manifest.get("email"), dict) else {}
    snapshot_path = Path(str(manifest.get("snapshotPath") or run_dir / RUN_SNAPSHOT_FILENAME))
    expected_hash = str(manifest.get("snapshotHash") or "")
    schedule_date = str(manifest.get("scheduleDate") or "")
    schedule_slots = [str(item) for item in manifest.get("scheduleSlots") or []]
    manual_request_id = str(manifest.get("manualRequestId") or "")

    try:
        if not expected_hash:
            raise ValueError("snapshot_manifest_hash_missing")
        if not snapshot_path.exists():
            raise ValueError("snapshot_manifest_file_missing")
        snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(snapshot_payload, dict):
            raise ValueError("snapshot_not_an_object")
        actual_hash = snapshot_payload_hash(snapshot_payload)
        if not expected_hash or actual_hash != expected_hash:
            raise ValueError("snapshot_manifest_hash_mismatch")
        if str(snapshot_payload.get("runId") or "") != run_id:
            raise ValueError("snapshot_manifest_run_id_mismatch")
    except Exception as exc:
        detail = f"delivery_manifest_invalid:{type(exc).__name__}: {exc}"
        publication = PublicationResult(
            mode=str(manifest.get("publicationMode") or ""),
            status="failed",
            configured=False,
            attempted=False,
            detail=detail,
            run_id=run_id,
            snapshot_hash=expected_hash,
        )
        email_result = NotificationResult(
            "email", prepared.get("enabled") is True, False, False, publication.detail
        )
        outbox_item = record_delivery_outbox(
            run_id,
            run_dir,
            status="failed",
            detail=publication.detail,
            schedule_date=schedule_date,
            schedule_slots=schedule_slots,
            manual_request_id=manual_request_id,
            attempted=False,
        )
        return update_delivery_metadata(
            run_dir,
            publication,
            email_result,
            pending=False,
            outbox_item=outbox_item,
            terminal_error=True,
        )

    recover_interrupted_delivery_outbox()
    existing_delivery = delivery_outbox_item(run_id)
    existing_status = str((existing_delivery or {}).get("status") or "")
    persisted_snapshot = metadata.get("snapshot") if isinstance(metadata.get("snapshot"), dict) else {}
    persisted_manifest_publication = (
        manifest.get("publication") if isinstance(manifest.get("publication"), dict) else {}
    )
    persisted_publication = PublicationResult(
        mode=str(
            persisted_manifest_publication.get("mode")
            or persisted_snapshot.get("mode")
            or manifest.get("publicationMode")
            or publication_mode(config)
        ),
        status=str(
            persisted_manifest_publication.get("status")
            or persisted_snapshot.get("status")
            or metadata.get("snapshotStatus")
            or "failed"
        ),
        configured=bool(
            persisted_manifest_publication.get("configured")
            or persisted_snapshot.get("configured")
        ),
        attempted=bool(
            persisted_manifest_publication.get("attempted")
            or persisted_snapshot.get("attempted")
        ),
        detail=str(
            persisted_manifest_publication.get("detail")
            or persisted_snapshot.get("detail")
            or (existing_delivery or {}).get("detail")
            or ""
        ),
        run_id=run_id,
        snapshot_hash=expected_hash,
        generated_at=str(
            persisted_manifest_publication.get("generatedAt")
            or snapshot_payload.get("generatedAt")
            or ""
        ),
        canonical_verified=bool(
            persisted_manifest_publication.get("canonicalVerified")
            or persisted_snapshot.get("canonicalVerified")
        ),
    )
    if existing_status == "completed":
        email_enabled = prepared.get("enabled") is True
        email_result = NotificationResult(
            "email",
            enabled=email_enabled,
            attempted=False,
            sent=email_enabled,
            detail="delivery_already_completed",
        )
        return update_delivery_metadata(
            run_dir,
            persisted_publication,
            email_result,
            pending=False,
            outbox_item=existing_delivery,
        )
    if existing_status == "sending":
        email_result = NotificationResult(
            "email",
            enabled=prepared.get("enabled") is True,
            attempted=False,
            sent=False,
            detail="email_send_already_in_progress",
        )
        return update_delivery_metadata(
            run_dir,
            persisted_publication,
            email_result,
            pending=True,
            outbox_item=existing_delivery,
        )
    if existing_status == "uncertain" and not force_uncertain_email_retry:
        email_result = NotificationResult(
            "email",
            enabled=prepared.get("enabled") is True,
            attempted=False,
            sent=False,
            detail=(
                "email_delivery_uncertain_explicit_retry_required; "
                "retry_may_duplicate"
            ),
        )
        return update_delivery_metadata(
            run_dir,
            persisted_publication,
            email_result,
            pending=False,
            outbox_item=existing_delivery,
            terminal_error=True,
            email_status_override="uncertain",
        )

    delivery_config = dict(config)
    delivery_config["AUCTION_WATCH_PUBLICATION_MODE"] = str(
        manifest.get("publicationMode") or publication_mode(config)
    )
    publication = publish_web_snapshot(delivery_config, snapshot_path)
    if publication.status == "failed":
        superseded = publication_failure_is_superseded(delivery_config, publication, run_id)
        email_result = NotificationResult(
            "email",
            enabled=prepared.get("enabled") is True,
            attempted=False,
            sent=False,
            detail=(
                f"snapshot_superseded:{publication.detail}"
                if superseded
                else f"pending_snapshot:{publication.detail}"
            ),
        )
        outbox_item = record_delivery_outbox(
            run_id,
            run_dir,
            status="failed" if superseded else "pending",
            detail=publication.detail,
            schedule_date=schedule_date,
            schedule_slots=schedule_slots,
            manual_request_id=manual_request_id,
            attempted=not superseded,
        )
        return update_delivery_metadata(
            run_dir,
            publication,
            email_result,
            pending=not superseded,
            outbox_item=outbox_item,
            terminal_error=superseded,
        )

    try:
        prepared = prepare_canonical_email_delivery(
            delivery_config,
            manifest,
            publication,
            run_dir,
        )
        manifest["email"] = prepared
        manifest["canonicalEmailPreparedAt"] = now_iso()
        manifest["publication"] = {
            "mode": publication.mode,
            "status": publication.status,
            "configured": publication.configured,
            "attempted": publication.attempted,
            "detail": publication.detail,
            "runId": publication.run_id,
            "snapshotHash": publication.snapshot_hash,
            "generatedAt": publication.generated_at,
            "canonicalVerified": publication.canonical_verified,
        }
        write_json(delivery_path, manifest)
    except Exception as exc:
        detail = f"pending_canonical_email:{type(exc).__name__}: {exc}"
        email_result = NotificationResult(
            "email",
            enabled=prepared.get("enabled") is True,
            attempted=False,
            sent=False,
            detail=detail,
        )
        outbox_item = record_delivery_outbox(
            run_id,
            run_dir,
            status="pending",
            detail=detail,
            schedule_date=schedule_date,
            schedule_slots=schedule_slots,
            manual_request_id=manual_request_id,
            attempted=True,
        )
        return update_delivery_metadata(
            run_dir,
            publication,
            email_result,
            pending=True,
            outbox_item=outbox_item,
        )

    message_id = str(prepared.get("messageId") or "")
    if prepared.get("enabled") is True:
        sending_at = now_iso()
        manifest["emailDelivery"] = {
            "status": "sending",
            "startedAt": sending_at,
            "messageId": message_id,
        }
        write_json(delivery_path, manifest)
        record_delivery_outbox(
            run_id,
            run_dir,
            status="sending",
            detail="email_send_started",
            schedule_date=schedule_date,
            schedule_slots=schedule_slots,
            manual_request_id=manual_request_id,
            email_message_id=message_id,
            attempted=True,
        )

    # If the process exits inside this call, the durable `sending` state is
    # recovered as `uncertain`; automated scheduling never sends it again.
    email_result = send_prepared_email(delivery_config, prepared)
    if email_result.enabled and not email_result.sent:
        ambiguous = email_failure_is_ambiguous(email_result)
        delivery_detail = email_result.detail
        if ambiguous:
            delivery_detail = (
                f"{delivery_detail}; email_outcome_unknown; "
                "explicit_retry_may_duplicate"
            )
            email_result = replace(email_result, detail=delivery_detail)
        manifest["emailDelivery"] = {
            "status": "uncertain" if ambiguous else "failed",
            "startedAt": str(
                (manifest.get("emailDelivery") or {}).get("startedAt")
                if isinstance(manifest.get("emailDelivery"), dict)
                else ""
            ),
            "finishedAt": now_iso(),
            "messageId": message_id,
            "detail": delivery_detail,
        }
        write_json(delivery_path, manifest)
        outbox_item = record_delivery_outbox(
            run_id,
            run_dir,
            status="uncertain" if ambiguous else "pending",
            detail=delivery_detail,
            schedule_date=schedule_date,
            schedule_slots=schedule_slots,
            manual_request_id=manual_request_id,
            email_message_id=message_id,
            attempted=prepared.get("enabled") is not True,
        )
        return update_delivery_metadata(
            run_dir,
            publication,
            email_result,
            pending=not ambiguous,
            outbox_item=outbox_item,
            terminal_error=ambiguous,
            email_status_override="uncertain" if ambiguous else "",
        )

    manifest["emailDelivery"] = {
        "status": "sent" if email_result.enabled else "disabled",
        "startedAt": str(
            (manifest.get("emailDelivery") or {}).get("startedAt")
            if isinstance(manifest.get("emailDelivery"), dict)
            else ""
        ),
        "finishedAt": now_iso(),
        "messageId": message_id,
        "detail": email_result.detail,
    }
    write_json(delivery_path, manifest)
    outbox_item = record_delivery_outbox(
        run_id,
        run_dir,
        status="completed",
        detail=email_result.detail,
        schedule_date=schedule_date,
        schedule_slots=schedule_slots,
        manual_request_id=manual_request_id,
        email_message_id=message_id,
        attempted=prepared.get("enabled") is not True,
    )
    return update_delivery_metadata(
        run_dir,
        publication,
        email_result,
        pending=False,
        outbox_item=outbox_item,
    )

def run_scan(args: argparse.Namespace) -> int:
    state = load_state(STATE_FILE)
    watchlist = load_watchlist(WATCHLIST_FILE)

    run_id = sanitize_run_id(args.run_id or default_run_id())
    run_dir = RUNS_DIR / run_id
    if run_dir.exists():
        print(f"Run dir ya existe: {run_dir}", file=sys.stderr)
        return 1

    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)

    started_at = now_iso()

    bavastro_auctions_csv = run_dir / "auctions_bavastro_matches.csv"
    bavastro_matches_csv = run_dir / "consolas_bavastro_matches.csv"
    bavastro_receipt_json = run_dir / "bavastro_coverage.json"
    castells_auctions_csv = run_dir / "consolas_castells_auctions.csv"
    castells_csv = run_dir / "consolas_castells_matches.csv"
    castells_receipt_json = run_dir / "castells_coverage.json"
    castells_md = run_dir / "consolas_castells_matches_readable.md"
    extra_matches_csv = run_dir / EXTRA_MATCHES_FILENAME
    extra_status_json = run_dir / EXTRA_STATUS_FILENAME

    bavastro_discovery = run_step(
        "bavastro_discovery",
        (
            [
                str(PYTHON_BIN),
                str(BAVASTRO_DISCOVERY_SCRIPT),
                "--active-only",
                "--csv",
                str(bavastro_auctions_csv),
            ]
            if args.bavastro_discovery_mode == "active"
            else [
                str(PYTHON_BIN),
                str(BAVASTRO_DISCOVERY_SCRIPT),
                "--query",
                args.bavastro_query,
                "--window",
                str(max(1, args.bavastro_window)),
                "--headroom",
                str(max(0, args.bavastro_headroom)),
                "--csv",
                str(bavastro_auctions_csv),
            ]
        ),
        run_dir,
    )
    bavastro_discovery = classify_bavastro_discovery(
        bavastro_discovery,
        run_dir,
        active_mode=args.bavastro_discovery_mode == "active",
        discovery_path=bavastro_auctions_csv,
    )

    bavastro_auction_rows = read_csv_rows(bavastro_auctions_csv)
    bavastro_active_ids = extract_int_ids(bavastro_auction_rows, "id")
    bavastro_watch_refresh_ids = watchlist_refresh_group_ids(
        "bavastro",
        watchlist,
        bavastro_active_ids,
        cached_rows=active_match_rows_for_source(state, "bavastro"),
    )
    if bavastro_discovery.status == "success":
        state.processed_bavastro_auction_ids.intersection_update(bavastro_active_ids)
    if args.bavastro_discovery_mode == "active":
        new_bavastro_auction_ids = [
            auction_id
            for auction_id in bavastro_active_ids
            if auction_id not in state.processed_bavastro_auction_ids
        ]
    else:
        new_bavastro_auction_ids = bavastro_active_ids[:]
    # Processed IDs and watchlist membership are telemetry only. Coverage is
    # controlled by the active discovery result and always scans every group.
    bavastro_scan_ids = bavastro_active_ids[:]

    if bavastro_discovery.status == "failed":
        bavastro_matches = skipped_step("bavastro_matches", "bavastro_discovery_failed", run_dir)
    elif not bavastro_active_ids:
        bavastro_matches = skipped_step("bavastro_matches", "no_active_bavastro_auctions", run_dir)
    elif not bavastro_scan_ids:
        bavastro_matches = skipped_step("bavastro_matches", "no_active_bavastro_auctions", run_dir)
    else:
        bavastro_matches = run_step(
            "bavastro_matches",
            [
                str(PYTHON_BIN),
                str(BAVASTRO_MATCHES_SCRIPT),
                "--ids",
                ",".join(str(auction_id) for auction_id in bavastro_scan_ids),
                "--output",
                str(bavastro_matches_csv),
                "--receipt",
                str(bavastro_receipt_json),
                "--min-score",
                "-999",
            ],
            run_dir,
        )
        bavastro_matches = classify_bavastro_matches(
            bavastro_matches,
            run_dir,
            bavastro_receipt_json,
            bavastro_scan_ids,
        )
        if args.bavastro_discovery_mode == "active" and bavastro_matches.status == "success":
            state.processed_bavastro_auction_ids.update(bavastro_active_ids)

    castells_discovery = run_step(
        "castells_discovery",
        [
            str(PYTHON_BIN),
            str(CASTELLS_DISCOVERY_SCRIPT),
            "--discover-only",
            "--discover-output",
            str(castells_auctions_csv),
        ],
        run_dir,
    )
    castells_discovery = classify_castells_discovery(
        castells_discovery,
        run_dir,
        castells_auctions_csv,
    )

    castells_discovery_rows = read_csv_rows(castells_auctions_csv)
    castells_active_ids = extract_int_ids(castells_discovery_rows, "remate_id")
    castells_watch_refresh_ids = watchlist_refresh_group_ids(
        "castells",
        watchlist,
        castells_active_ids,
        cached_rows=active_match_rows_for_source(state, "castells"),
    )
    if castells_discovery.status == "success":
        state.processed_castells_remate_ids.intersection_update(castells_active_ids)
    new_castells_remate_ids = [
        remate_id
        for remate_id in castells_active_ids
        if remate_id not in state.processed_castells_remate_ids
    ]
    castells_scan_ids = castells_active_ids[:]

    if castells_discovery.status == "failed":
        castells_matches = skipped_step("castells_matches", "castells_discovery_failed", run_dir)
    elif not castells_active_ids:
        castells_matches = skipped_step("castells_matches", "no_active_castells_remates", run_dir)
    elif not castells_scan_ids:
        castells_matches = skipped_step("castells_matches", "no_active_castells_remates", run_dir)
    else:
        castells_matches = run_step(
            "castells_matches",
            [
                str(PYTHON_BIN),
                str(CASTELLS_MATCHES_SCRIPT),
                "--ids",
                ",".join(str(remate_id) for remate_id in castells_scan_ids),
                "--limit",
                str(max(1, args.castells_limit)),
                "--output",
                str(castells_csv),
                "--receipt",
                str(castells_receipt_json),
                "--markdown",
                str(castells_md),
                "--min-score",
                "-999",
            ],
            run_dir,
        )
        castells_matches = classify_castells_matches(
            castells_matches,
            run_dir,
            castells_receipt_json,
            castells_scan_ids,
        )
        if castells_matches.status == "success":
            state.processed_castells_remate_ids.update(castells_active_ids)

    extra_sources = run_step(
        "extra_sources",
        [
            str(PYTHON_BIN),
            str(EXTRA_SOURCES_SCRIPT),
            "--output-csv",
            str(extra_matches_csv),
            "--status-json",
            str(extra_status_json),
        ],
        run_dir,
    )
    extra_sources = classify_extra_sources(extra_sources, extra_status_json)

    bavastro_observed_match_rows = read_csv_rows(bavastro_matches_csv)
    castells_observed_match_rows = read_csv_rows(castells_csv)
    extra_observed_rows = read_csv_rows(extra_matches_csv)
    extra_status_payload = read_json_object(extra_status_json)
    extra_status_payload_valid = (
        isinstance(extra_status_payload, dict)
        and isinstance(extra_status_payload.get("sources"), list)
    )
    extra_source_rows = extra_status_payload.get("sources") if extra_status_payload_valid else []
    prior_lifecycle_rows = {
        "bavastro": active_match_rows_for_source(state, "bavastro"),
        "castells": active_match_rows_for_source(state, "castells"),
        **{
            source_id: [dict(row) for row in rows]
            for source_id, rows in state.active_extra_matches_by_source.items()
        },
    }
    prior_group_ids = {
        source: {
            lifecycle_group_id(source, row)
            for row in rows
            if lifecycle_group_id(source, row)
        }
        for source, rows in prior_lifecycle_rows.items()
    }
    extra_rows = reconcile_extra_match_state(
        state,
        extra_observed_rows,
        extra_source_rows,
        status_payload_valid=extra_status_payload_valid,
    )
    extra_sources.inventory_authoritative = bool(extra_source_rows) and all(
        isinstance(source_entry, dict)
        and source_entry.get("inventory_authoritative") is True
        for source_entry in extra_source_rows
    )

    if not bavastro_scan_ids and bavastro_matches.status == "skipped":
        bavastro_matches.inventory_authoritative = bavastro_discovery.inventory_authoritative
    if not castells_scan_ids and castells_matches.status == "skipped":
        castells_matches.inventory_authoritative = castells_discovery.inventory_authoritative
    bavastro_source_authoritative = effective_inventory_authority(
        bavastro_discovery,
        bavastro_matches,
        bavastro_active_ids,
        bavastro_scan_ids,
    )
    castells_source_authoritative = effective_inventory_authority(
        castells_discovery,
        castells_matches,
        castells_active_ids,
        castells_scan_ids,
    )
    bavastro_matches.inventory_authoritative = bavastro_source_authoritative
    castells_matches.inventory_authoritative = castells_source_authoritative
    # Discovery authority is evidence about the active-group list; the matches
    # step carries the single effective source decision used downstream.

    bavastro_match_rows = reconcile_active_match_state(
        state,
        "bavastro",
        bavastro_active_ids,
        bavastro_observed_match_rows,
        refreshed_ids=bavastro_scan_ids,
        inventory_authoritative=bavastro_matches.inventory_authoritative,
        refresh_succeeded=bavastro_matches.status in {"success", "partial"},
        refresh_complete=bavastro_matches.status == "success",
        completed_group_ids=complete_group_ids(bavastro_matches),
    )
    castells_rows = reconcile_active_match_state(
        state,
        "castells",
        castells_active_ids,
        castells_observed_match_rows,
        refreshed_ids=castells_scan_ids,
        inventory_authoritative=castells_matches.inventory_authoritative,
        refresh_succeeded=castells_matches.status in {"success", "partial"},
        refresh_complete=castells_matches.status == "success",
        completed_group_ids=complete_group_ids(castells_matches),
    )

    authoritative_groups = {
        "bavastro": complete_group_ids(bavastro_matches),
        "castells": complete_group_ids(castells_matches),
    }
    for source, source_authoritative in (
        ("bavastro", bavastro_source_authoritative),
        ("castells", castells_source_authoritative),
    ):
        if source_authoritative:
            authoritative_groups[source].update(prior_group_ids.get(source, set()))
            authoritative_groups[source].update(known_group_ids_for_source(state, source))
    for source_entry in extra_source_rows:
        if not isinstance(source_entry, dict):
            continue
        source_id = str(source_entry.get("source_id") or "").strip().lower()
        if source_id and source_entry.get("inventory_authoritative") is True:
            authoritative_groups[source_id] = known_group_ids_for_source(state, source_id)
            authoritative_groups[source_id].update(
                str(item.get("groupId") or "").strip()
                for item in source_entry.get("receipts", [])
                if isinstance(item, dict)
                and str(item.get("status") or "").strip().lower() == "complete"
                and str(item.get("groupId") or "").strip()
            )
    lifecycle_result = update_opportunity_lifecycle(
        state,
        run_id,
        now_iso(),
        {
            "bavastro": bavastro_observed_match_rows,
            "castells": castells_observed_match_rows,
            **{
                source_id: [
                    row
                    for row in extra_observed_rows
                    if str(row.get("source_id") or "").strip().lower() == source_id
                ]
                for source_id in {
                    str(row.get("source_id") or "").strip().lower()
                    for row in extra_observed_rows
                    if str(row.get("source_id") or "").strip()
                }
            },
        },
        authoritative_groups,
        prior_source_rows=prior_lifecycle_rows,
    )
    new_opportunity_keys = lifecycle_result["new"]
    removed_opportunity_keys = lifecycle_result["removed"]

    def is_new_row(source: str, row: dict[str, str]) -> bool:
        return opportunity_lifecycle_key(source, row) in new_opportunity_keys

    new_bavastro_match_rows = [
        row for row in bavastro_observed_match_rows if is_new_row("bavastro", row)
    ]
    new_castells_match_rows = [
        row for row in castells_observed_match_rows if is_new_row("castells", row)
    ]
    new_extra_rows = [
        row
        for row in extra_observed_rows
        if is_new_row(str(row.get("source_id") or "").strip().lower(), row)
    ]
    extra_coverage: list[dict[str, object]] = []
    for source_entry in extra_source_rows:
        if not isinstance(source_entry, dict):
            continue
        source_id = str(source_entry.get("source_id") or "").strip().lower()
        source_rows = [
            row
            for row in extra_observed_rows
            if str(row.get("source_id") or "").strip().lower() == source_id
        ]
        source_new_rows = [
            row
            for row in new_extra_rows
            if str(row.get("source_id") or "").strip().lower() == source_id
        ]
        receipts = [item for item in source_entry.get("receipts", []) if isinstance(item, dict)]
        extra_coverage.append(
            {
                "sourceId": source_id,
                "discoveryComplete": source_entry.get("discovery_complete") is True,
                "groupsDiscovered": int(source_entry.get("groups") or 0),
                "groupsQueried": int(source_entry.get("groups") or 0),
                "completeGroups": [
                    str(item.get("groupId") or "")
                    for item in receipts
                    if str(item.get("status") or "").lower() == "complete"
                ],
                "partialOrFailedGroups": [
                    str(item.get("groupId") or "")
                    for item in receipts
                    if str(item.get("status") or "").lower() != "complete"
                ],
                "lotCount": int(source_entry.get("lots") or 0),
                "matchesDetected": len(source_rows),
                "matchesNew": len(source_new_rows),
                "matchesRemoved": sum(
                    key.startswith(f"{source_id}\x1f") for key in removed_opportunity_keys
                ),
                "status": (
                    "complete"
                    if source_entry.get("inventory_authoritative") is True
                    else str(source_entry.get("status") or "partial")
                ),
                "inventoryAuthoritative": source_entry.get("inventory_authoritative") is True,
                "groupReceipts": receipts,
            }
        )
    coverage_sources = [
        coverage_summary(
            run_dir,
            bavastro_matches,
            bavastro_active_ids,
            bavastro_scan_ids if bavastro_matches.status != "skipped" else [],
            bavastro_observed_match_rows,
            new_bavastro_match_rows,
            removed_opportunity_keys,
            "bavastro",
            discovery_complete=bavastro_discovery.inventory_authoritative,
        ),
        coverage_summary(
            run_dir,
            castells_matches,
            castells_active_ids,
            castells_scan_ids if castells_matches.status != "skipped" else [],
            castells_observed_match_rows,
            new_castells_match_rows,
            removed_opportunity_keys,
            "castells",
            discovery_complete=castells_discovery.inventory_authoritative,
        ),
        *extra_coverage,
    ]
    coverage_payload = {
        "sources": coverage_sources,
        "groupsDiscovered": sum(int(item.get("groupsDiscovered") or 0) for item in coverage_sources),
        "groupsQueried": sum(int(item.get("groupsQueried") or 0) for item in coverage_sources),
        "lotsObserved": sum(int(item.get("lotCount") or 0) for item in coverage_sources),
        "matchesDetected": len(all_match_views) if "all_match_views" in locals() else 0,
        "matchesNew": len(new_opportunity_keys),
        "matchesRemoved": len(removed_opportunity_keys),
        "inventoryAuthoritative": bool(coverage_sources)
        and all(item.get("inventoryAuthoritative") is True for item in coverage_sources),
    }

    if bavastro_match_rows:
        write_csv_dict_rows(bavastro_matches_csv, bavastro_match_rows)
    if castells_rows:
        write_csv_dict_rows(castells_csv, castells_rows)

    notification_config = load_notification_config()
    resolved_publication_mode = publication_mode(notification_config)
    delivery_config = dict(notification_config)
    delivery_config["AUCTION_WATCH_PUBLICATION_MODE"] = resolved_publication_mode
    delivery_config["AUCTION_WATCH_APP_BASE_URL"] = effective_app_base_url(
        notification_config, resolved_publication_mode
    )
    dismissal_state = load_dismissals(notification_config)

    all_watch_hits = collect_watch_hits(
        watchlist,
        bavastro_match_rows,
        castells_rows,
        now_local_dt(),
    )

    all_match_views = build_match_views(bavastro_match_rows, castells_rows, extra_rows)
    coverage_payload["matchesDetected"] = len(all_match_views)
    match_views, dismissed_match_views = filter_dismissed_match_views(
        all_match_views,
        dismissal_state.keys,
    )
    watch_hits = [
        hit
        for hit in all_watch_hits
        if watch_hit_dismissal_key(hit) not in dismissal_state.keys
    ]
    has_matches = bool(all_match_views)
    raw_counts_by_source: dict[str, int] = {}
    for item in all_match_views:
        raw_counts_by_source[item.source_id] = raw_counts_by_source.get(item.source_id, 0) + 1
    visible_counts_by_source: dict[str, int] = {}
    for item in match_views:
        visible_counts_by_source[item.source_id] = visible_counts_by_source.get(item.source_id, 0) + 1
    extra_counts_by_source: dict[str, int] = {}
    for source_id, match_count in visible_counts_by_source.items():
        if source_id not in {"bavastro", "castells"}:
            extra_counts_by_source[source_id] = match_count
    counts = {
        "bavastro_active_auctions": len(bavastro_active_ids),
        "bavastro_new_auctions": len(new_bavastro_auction_ids),
        "bavastro_matches": visible_counts_by_source.get("bavastro", 0),
        "bavastro_new_matches": len(new_bavastro_match_rows),
        "bavastro_match_auctions": unique_count(bavastro_match_rows, "auction_id"),
        "bavastro_new_match_auctions": unique_count(new_bavastro_match_rows, "auction_id"),
        "castells_active_remates": len(castells_active_ids),
        "castells_new_remates": len(new_castells_remate_ids),
        "castells_matches": visible_counts_by_source.get("castells", 0),
        "castells_new_matches": len(new_castells_match_rows),
        "castells_match_remates": unique_count(castells_rows, "remate_id"),
        "castells_new_match_remates": unique_count(new_castells_match_rows, "remate_id"),
        "extra_matches": sum(extra_counts_by_source.values()),
        "extra_matches_by_source": extra_counts_by_source,
        "total_matches": len(match_views),
        "detected_matches": len(all_match_views),
        "dismissed_matches": len(dismissed_match_views),
        "visible_matches": len(match_views),
        "new_matches": len(new_opportunity_keys),
        "removed_matches": len(removed_opportunity_keys),
    }
    raw_extra_counts_by_source = {
        source_id: match_count
        for source_id, match_count in raw_counts_by_source.items()
        if source_id not in {"bavastro", "castells"}
    }
    publication_counts = {
        **counts,
        "bavastro_matches": raw_counts_by_source.get("bavastro", 0),
        "castells_matches": raw_counts_by_source.get("castells", 0),
        "extra_matches": sum(raw_extra_counts_by_source.values()),
        "extra_matches_by_source": raw_extra_counts_by_source,
        "total_matches": len(all_match_views),
        "detected_matches": len(all_match_views),
        "dismissed_matches": 0,
        "visible_matches": len(all_match_views),
        "new_matches": len(new_opportunity_keys),
        "removed_matches": len(removed_opportunity_keys),
    }

    finished_at = now_iso()
    steps = [bavastro_discovery, bavastro_matches, castells_discovery, castells_matches, extra_sources]
    status = overall_status(steps)

    summary_path = run_dir / "summary.md"
    metadata_path = run_dir / "run.json"
    newsletter_preview_html = run_dir / "newsletter-preview.html"
    latest_matchful_run_dir = find_latest_matchful_run(exclude_run_dir=run_dir)
    latest_bavastro_match_run_dir = find_latest_matchful_run(
        exclude_run_dir=run_dir,
        count_key="bavastro_matches",
    )
    latest_castells_match_run_dir = find_latest_matchful_run(
        exclude_run_dir=run_dir,
        count_key="castells_matches",
    )
    preview_image_src = ""
    if watch_hits:
        preview_image_src = (watch_hits[0].image_url or "").strip()
    write_newsletter_preview(
        newsletter_preview_html,
        build_newsletter_html(
            status,
            counts,
            match_views,
            watch_hits,
            hero_image_src=preview_image_src,
            app_base_url=delivery_config.get("AUCTION_WATCH_APP_BASE_URL", ""),
        ),
    )

    write_summary(
        summary_path,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        steps=steps,
        counts=counts,
        bavastro_auction_rows=bavastro_auction_rows,
        castells_discovery_rows=castells_discovery_rows,
        new_bavastro_auction_ids=new_bavastro_auction_ids,
        new_castells_remate_ids=new_castells_remate_ids,
        bavastro_match_rows=bavastro_match_rows,
        castells_rows=castells_rows,
        match_views=match_views,
        extra_status_payload=extra_status_payload,
        watch_hits=watch_hits,
        latest_matchful_run_dir=latest_matchful_run_dir,
        latest_bavastro_match_run_dir=latest_bavastro_match_run_dir,
        latest_castells_match_run_dir=latest_castells_match_run_dir,
        bavastro_auctions_csv=bavastro_auctions_csv,
        bavastro_matches_csv=bavastro_matches_csv,
        castells_auctions_csv=castells_auctions_csv,
        castells_csv=castells_csv,
        castells_md=castells_md,
        extra_matches_csv=extra_matches_csv,
        extra_status_json=extra_status_json,
        newsletter_preview_html=newsletter_preview_html,
    )

    schedule_slots = sorted(
        {item.strip() for item in str(args.schedule_slots or "").split(",") if item.strip()}
    )
    macos_result = send_macos_notification(notification_config, status, counts, summary_path)
    prepared_email = prepare_email_delivery(
        delivery_config,
        status,
        counts,
        run_id,
        summary_path,
        match_views,
        watch_hits,
        run_dir,
    )
    run_metadata = {
        "run_id": run_id,
        "runId": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "scanCompletedAt": finished_at,
        "completedAt": None,
        "status": status,
        "scanStatus": canonical_scan_status(status),
        "snapshotStatus": "failed",
        "emailStatus": "pending" if prepared_email.get("enabled") is True else "disabled",
        "overallStatus": "delivery_pending",
        "exitCode": None,
        "publicationMode": resolved_publication_mode,
        "repo_root": str(REPO_ROOT),
        "steps": [asdict(step) for step in steps],
        "counts": counts,
        "coverage": coverage_payload,
        "opportunityLifecycle": state.opportunity_lifecycle,
        "newOpportunityLifecycle": lifecycle_rows_for_run(state, new_opportunity_keys),
        "removedOpportunityLifecycle": lifecycle_rows_for_run(state, removed_opportunity_keys),
        "extra_sources": extra_status_payload,
        "dismissals": {
            "source": dismissal_state.source,
            "detail": dismissal_state.detail,
            "configured": len(dismissal_state.items),
            "active_matches_hidden": len(dismissed_match_views),
            "cache_file": str(DISMISSALS_CACHE_FILE),
        },
        "incremental": {
            "state_file": str(STATE_FILE),
            "new_bavastro_auction_ids": new_bavastro_auction_ids,
            "watch_refresh_bavastro_auction_ids": bavastro_watch_refresh_ids,
            "new_castells_remate_ids": new_castells_remate_ids,
            "watch_refresh_castells_remate_ids": castells_watch_refresh_ids,
            "processed_bavastro_total": len(state.processed_bavastro_auction_ids),
            "processed_castells_total": len(state.processed_castells_remate_ids),
            "active_bavastro_groups": len(state.active_bavastro_matches_by_group),
            "active_castells_groups": len(state.active_castells_matches_by_group),
        },
        "schedule": {
            "date": str(args.schedule_date or ""),
            "slots": schedule_slots,
            "manualRequestId": str(args.manual_request_id or ""),
        },
        "watchlist": {
            "file": str(WATCHLIST_FILE),
            "configured_items": len(watchlist),
            # The immutable publication inventory is raw. `visible_hits` is a
            # local/degraded projection only and never decides what HA stores.
            "active_hits": [asdict(hit) for hit in all_watch_hits],
            "visible_hits": [asdict(hit) for hit in watch_hits],
        },
        "notifications": [asdict(macos_result)],
    }
    write_json(metadata_path, run_metadata)

    snapshot_path = run_dir / RUN_SNAPSHOT_FILENAME
    snapshot_ok, snapshot_detail = export_web_snapshot(run_dir, snapshot_path)
    snapshot_hash = ""
    if snapshot_ok:
        snapshot_payload = read_json_object(snapshot_path)
        snapshot_hash = snapshot_payload_hash(snapshot_payload)

    delivery_manifest = {
        "version": 1,
        "runId": run_id,
        "runDir": str(run_dir),
        "createdAt": now_iso(),
        "publicationMode": resolved_publication_mode,
        "snapshotPath": str(snapshot_path),
        "snapshotHash": snapshot_hash,
        "snapshotExported": snapshot_ok,
        "snapshotExportDetail": snapshot_detail,
        "scheduleDate": str(args.schedule_date or ""),
        "scheduleSlots": schedule_slots,
        "manualRequestId": str(args.manual_request_id or ""),
        "email": prepared_email,
        "emailSource": {
            "status": status,
            "scanStatus": canonical_scan_status(status),
            "counts": publication_counts,
            "summaryPath": str(summary_path),
            "matchViews": [asdict(item) for item in all_match_views],
            "watchHits": [asdict(item) for item in all_watch_hits],
        },
    }
    write_json(run_dir / DELIVERY_MANIFEST_FILENAME, delivery_manifest)
    record_delivery_outbox(
        run_id,
        run_dir,
        status="pending",
        detail="delivery_not_attempted",
        schedule_date=str(args.schedule_date or ""),
        schedule_slots=schedule_slots,
        manual_request_id=str(args.manual_request_id or ""),
        attempted=False,
    )
    # Commit the incremental cache only after the scan manifest, immutable
    # snapshot manifest and durable outbox reference all exist. A crash before
    # this point safely reprocesses IDs; a crash after it retries this run.
    save_state(STATE_FILE, state)

    if snapshot_ok:
        exit_code = attempt_delivery_for_run(run_dir, notification_config)
    else:
        publication = PublicationResult(
            mode=resolved_publication_mode,
            status="failed",
            configured=resolved_publication_mode == "ha-required",
            attempted=False,
            detail=f"snapshot_export_failed:{snapshot_detail}",
            run_id=run_id,
        )
        email_result = NotificationResult(
            "email",
            enabled=prepared_email.get("enabled") is True,
            attempted=False,
            sent=False,
            detail=publication.detail,
        )
        outbox_item = record_delivery_outbox(
            run_id,
            run_dir,
            status="failed",
            detail=publication.detail,
            schedule_date=str(args.schedule_date or ""),
            schedule_slots=schedule_slots,
            manual_request_id=str(args.manual_request_id or ""),
            attempted=False,
        )
        exit_code = update_delivery_metadata(
            run_dir,
            publication,
            email_result,
            pending=False,
            outbox_item=outbox_item,
            terminal_error=True,
        )

    refresh_latest_matches_mirror(run_dir, has_matches, latest_matchful_run_dir)
    prune_runs(args.keep_runs)

    final_metadata = read_json_object(metadata_path)
    final_notifications = [
        item for item in final_metadata.get("notifications") or [] if isinstance(item, dict)
    ]

    print("=" * 80)
    print("AUCTION WATCH")
    print("=" * 80)
    print(f"Run ID: {run_id}")
    print(f"Scan status: {status}")
    print(f"Overall status: {final_metadata.get('overallStatus', 'unknown')}")
    print(
        f"Bavastro activos: {counts['bavastro_active_auctions']} | "
        f"nuevos: {counts['bavastro_new_auctions']} | "
        f"matches activos: {counts['bavastro_matches']} | "
        f"matches nuevos: {counts['bavastro_new_matches']}"
    )
    print(
        f"Castells activos: {counts['castells_active_remates']} | "
        f"nuevos: {counts['castells_new_remates']} | "
        f"matches activos: {counts['castells_matches']} | "
        f"matches nuevos: {counts['castells_new_matches']}"
    )
    for source_id, match_count in extra_counts_by_source.items():
        print(f"{display_source_name(source_id)} matches activos: {match_count}")
    for result in final_notifications:
        print(
            f"Notification {result.get('channel')}: "
            f"{'sent' if result.get('sent') else result.get('detail', 'unknown')}"
        )
    print(f"Run snapshot: {'ok' if snapshot_ok else 'warning'}")
    if snapshot_detail:
        print(snapshot_detail)
    print(f"Snapshot status: {final_metadata.get('snapshotStatus', 'unknown')}")
    print(f"Summary: {summary_path}")
    print(f"Latest: {LATEST_DIR / 'summary.md'}")

    return exit_code


def main() -> int:
    args = parse_args()
    ensure_runtime()
    RUN_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOCK_FILE.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Auction Watch runner lock busy; skipping.", file=sys.stderr)
            return 75

        if args.deliver_run:
            run_id = sanitize_run_id(args.deliver_run)
            run_dir = RUNS_DIR / run_id
            if not (run_dir / DELIVERY_MANIFEST_FILENAME).exists():
                print(f"Delivery manifest inexistente: {run_dir}", file=sys.stderr)
                return 1
            return attempt_delivery_for_run(
                run_dir,
                load_notification_config(),
                force_uncertain_email_retry=args.force_uncertain_email_retry,
            )
        return run_scan(args)


if __name__ == "__main__":
    raise SystemExit(main())
