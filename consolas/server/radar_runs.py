"""Collection Radar: time zone, slots, runs, receipts and query plans."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from typing import Any

from radar.wishlist import INTENT_PRIORITY, search_row_intent

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        RADAR_MANUAL_FRESHNESS_MINUTES,
        RADAR_RECOMMENDATION_SLOTS,
        RADAR_SLOTS,
        RADAR_SLOT_KEYS,
        RADAR_SLOT_LABELS,
        RADAR_TIMEZONE_NAME,
        _RADAR_LOCK,
        _RADAR_SEARCH_LOCKS,
    )
    from .database import connect_db
    from .errors import ApiError
    from .radar_criteria import radar_json_field, radar_search_queries, radar_source_label
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import (
        RADAR_MANUAL_FRESHNESS_MINUTES,
        RADAR_RECOMMENDATION_SLOTS,
        RADAR_SLOTS,
        RADAR_SLOT_KEYS,
        RADAR_SLOT_LABELS,
        RADAR_TIMEZONE_NAME,
        _RADAR_LOCK,
        _RADAR_SEARCH_LOCKS,
    )
    from database import connect_db
    from errors import ApiError
    from radar_criteria import radar_json_field, radar_search_queries, radar_source_label


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
    deferred = totals.get("deferred") or []
    if deferred and not detail:
        detail = f"{len(deferred)} búsqueda(s) esperan a otra corrida: " + "; ".join(
            f"{item['name']} ({item['reason']})" for item in deferred[:8]
        )
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


def radar_row_queries(row: Any) -> int:
    criteria = radar_json_field(row["criteria_json"], {}) if "criteria_json" in row.keys() else {}
    return max(1, len(radar_search_queries(row["search_query"], criteria)))


def radar_query_plan(
    rows: list[Any], wished: set[tuple[str, str]], slot_key: str, budget: int
) -> tuple[list[tuple[Any, str]], list[dict[str, Any]]]:
    """Qué búsquedas corren en esta corrida y cuáles esperan, con su motivo.

    Primero la wishlist, después consolas, lotes, recomendaciones y
    descubrimiento; dentro de cada grupo, las más baratas en consultas primero
    para que entren más. Una búsqueda corre entera o no corre: cortarla a la
    mitad la dejaría con resultados de unos títulos y no de otros.
    """

    ranked = []
    for row in rows:
        criteria = radar_json_field(row["criteria_json"], {}) if "criteria_json" in row.keys() else {}
        search = {"searchType": row["search_type"], "entityType": row["entity_type"], "entityId": row["entity_id"],
                  "entityConsoleId": row["entity_console_id"], "criteria": criteria}
        intent = search_row_intent(search, wished, strict=True)
        rank = float(INTENT_PRIORITY.get(intent, len(INTENT_PRIORITY)))
        # «Deseados SNES» con dos deseos entre 47 recomendaciones: va después de
        # los lotes y antes de las recomendaciones puras, y puede correr en
        # cualquier slot porque adentro hay deseos reales.
        if intent == "recommendation" and search_row_intent(search, wished) == "wishlist":
            intent, rank = "mixed", INTENT_PRIORITY["lot"] + 0.5
        last = str(row["last_checked_at"] or "") if "last_checked_at" in row.keys() else ""
        ranked.append((rank, last, radar_row_queries(row), row, intent))
    # Dentro de cada grupo, la que hace más que no corre: lo que no entró hoy
    # entra primero mañana, y ninguna queda postergada para siempre.
    ranked.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    planned: list[tuple[Any, str]] = []
    deferred: list[dict[str, Any]] = []
    remaining = budget
    for _, _, queries, row, intent in ranked:
        if intent in {"recommendation", "discovery"} and slot_key and slot_key not in RADAR_RECOMMENDATION_SLOTS:
            deferred.append({"searchId": str(row["id"]), "name": str(row["name"]), "intent": intent,
                             "queries": queries, "reason": "las recomendaciones corren sólo a la mañana"})
            continue
        if queries > remaining:
            deferred.append({"searchId": str(row["id"]), "name": str(row["name"]), "intent": intent,
                             "queries": queries, "reason": "sin presupuesto de consultas en esta corrida"})
            continue
        remaining -= queries
        planned.append((row, intent))
    return planned, deferred
