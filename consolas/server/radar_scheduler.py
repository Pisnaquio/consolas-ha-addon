"""Collection Radar: due-slot runs, manual runs, purge and the scheduler thread."""

from __future__ import annotations

import threading
from typing import Any

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        RADAR_QUERY_BUDGET,
        RADAR_RUN_HISTORY_LIMIT,
        RADAR_SCHEDULER_INTERVAL_SECONDS,
        RADAR_SEARCHES_VERSION,
        RADAR_SLOT_KEYS,
        RADAR_SLOT_LABELS,
        RADAR_TIMEZONE_NAME,
        _RADAR_LOCK,
        _RADAR_RUN_LOCK,
    )
    from .database import connect_db
    from .errors import ApiError
    from .radar_catalog import radar_wished_keys
    from .radar_execution import run_radar_search
    from .radar_notifications import notify_radar_opportunities
    from .radar_runs import (
        claim_radar_slot,
        finish_radar_run,
        manual_run_satisfies_slot,
        radar_due_slots,
        radar_next_slot,
        radar_now_local,
        radar_query_plan,
        radar_receipt_row,
        radar_run_row,
        radar_search_slots,
        radar_slot_datetime,
        start_radar_run,
    )
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import (
        RADAR_QUERY_BUDGET,
        RADAR_RUN_HISTORY_LIMIT,
        RADAR_SCHEDULER_INTERVAL_SECONDS,
        RADAR_SEARCHES_VERSION,
        RADAR_SLOT_KEYS,
        RADAR_SLOT_LABELS,
        RADAR_TIMEZONE_NAME,
        _RADAR_LOCK,
        _RADAR_RUN_LOCK,
    )
    from database import connect_db
    from errors import ApiError
    from radar_catalog import radar_wished_keys
    from radar_execution import run_radar_search
    from radar_notifications import notify_radar_opportunities
    from radar_runs import (
        claim_radar_slot,
        finish_radar_run,
        manual_run_satisfies_slot,
        radar_due_slots,
        radar_next_slot,
        radar_now_local,
        radar_query_plan,
        radar_receipt_row,
        radar_run_row,
        radar_search_slots,
        radar_slot_datetime,
        start_radar_run,
    )


def run_radar_searches(config: AppConfig, run_id: str, slot_key: str = "") -> dict[str, Any]:
    """Ejecuta las búsquedas activas que corresponden a esta corrida.

    Una búsqueda que falla no detiene a las demás: su falla queda en el recibo y
    en `last_error`, y el resto de la corrida sigue. Lo que no entra en el
    presupuesto de consultas espera a la próxima y queda dicho en el detalle.
    """

    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            "SELECT * FROM radar_searches WHERE status = 'active' AND deleted_at IS NULL ORDER BY priority, name"
        ).fetchall()

    selected = [row for row in rows if not slot_key or slot_key in radar_search_slots(row)]
    budget = int(getattr(config, "radar_query_budget", RADAR_QUERY_BUDGET) or RADAR_QUERY_BUDGET)
    planned, deferred = radar_query_plan(selected, radar_wished_keys(config), slot_key, budget)
    totals = {
        "searchesTotal": len(planned),
        "searchesOk": 0,
        "searchesFailed": 0,
        "listingsMatched": 0,
        "listingsRejected": 0,
        "deferred": deferred,
    }
    for row, _intent in planned:
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
    try:
        # Avisar es parte de la entrega, pero una falla de correo no puede
        # deshacer un scan que salió bien ni volver a poner el slot en juego.
        notify_radar_opportunities(config, run_id)
    except Exception as error:  # noqa: BLE001
        print(f"[consolas] Collection Radar notification error: {error}")
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


def purge_expired_radar_content(config: AppConfig) -> dict[str, Any]:
    """Borra el contenido de publicaciones cuyo TTL de licencia venció.

    Cada fuente declara `contentTtlSeconds` y cada publicación guarda su
    `content_expires_at`. Hasta acá eso se escribía y nunca se leía: el contenido
    quedaba para siempre.

    Una publicación que sigue apareciendo renueva su vencimiento en cada corrida,
    así que lo que se borra es lo que dejó de aparecer y ya no se refresca. Las
    coincidencias caen por `ON DELETE CASCADE` — `PRAGMA foreign_keys` está
    encendido en `connect_db`.
    """

    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        cursor = conn.execute(
            """DELETE FROM radar_listings
                WHERE content_expires_at IS NOT NULL
                  AND content_expires_at != ''
                  AND content_expires_at < ?""",
            (now,),
        )
        purged = cursor.rowcount
    if purged:
        print(f"[consolas] Collection Radar: {purged} publicaciones vencidas purgadas")
    return {"ok": True, "purged": max(0, purged), "purgedAt": now}


def run_active_radar_searches(config: AppConfig) -> dict[str, Any]:
    """Corre todas las búsquedas aprobadas y activas, sin ligarlas a un slot.

    Un borrador espera consentimiento explícito y nunca entra acá.
    """
    return run_radar_searches(config, "")


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
                # El TTL de licencia no espera a que haya una corrida pendiente.
                purge_expired_radar_content(self.config)
                for outcome in run_due_radar_slots(self.config):
                    print(f"[consolas] Collection Radar slot {outcome['slotKey']}: {outcome['state']}")
            except Exception as error:  # el scheduler no puede morirse por una corrida
                print(f"[consolas] Collection Radar scheduler error: {error}")
            threading.Event().wait(RADAR_SCHEDULER_INTERVAL_SECONDS)
