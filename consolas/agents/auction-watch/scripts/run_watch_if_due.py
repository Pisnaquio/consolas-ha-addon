#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = Path(__file__).resolve().parents[1]
# See run_watch.py: the add-on image uses its system interpreter rather than
# a checkout-local virtual environment.
_checkout_python = REPO_ROOT / ".venv" / "bin" / "python"
PYTHON_BIN = Path(os.environ.get("AUCTION_WATCH_PYTHON") or (
    str(_checkout_python) if _checkout_python.exists() else sys.executable
))
RUN_WATCH = AGENT_DIR / "scripts" / "run_watch.py"
STATE_FILE = AGENT_DIR / "schedule_state.json"
LOCK_FILE = AGENT_DIR / "schedule.lock"
KEEP_DAYS = 14
SCHEDULE_STATE_VERSION = 2
NOTIFICATION_ENV_FILE = AGENT_DIR / "notification.env"
LOCAL_TIMEZONE = ZoneInfo("America/Montevideo")

try:
    from . import run_watch as run_watch_module
except ImportError:  # direct script execution
    import run_watch as run_watch_module


@dataclass(frozen=True)
class Slot:
    key: str
    hour: int
    minute: int


SCHEDULES = {
    "daily": [Slot("daily", 17, 10)],
    "twice": [Slot("morning", 9, 15), Slot("afternoon", 17, 10)],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ejecuta auction-watch solo si hay una corrida diaria pendiente."
    )
    parser.add_argument("--mode", choices=sorted(SCHEDULES), default="twice")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def now_local() -> datetime:
    return datetime.now(LOCAL_TIMEZONE)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {
            "version": SCHEDULE_STATE_VERSION,
            "days": {},
            "manualCompletions": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "version": SCHEDULE_STATE_VERSION,
            "days": {},
            "manualCompletions": {},
        }
    if not isinstance(payload, dict):
        return {
            "version": SCHEDULE_STATE_VERSION,
            "days": {},
            "manualCompletions": {},
        }
    try:
        current_version = int(payload.get("version") or 0)
    except (TypeError, ValueError):
        current_version = 0
    payload["version"] = max(current_version, SCHEDULE_STATE_VERSION)
    if not isinstance(payload.get("days"), dict):
        payload["days"] = {}
    if not isinstance(payload.get("manualCompletions"), dict):
        payload["manualCompletions"] = {}
    return payload


def save_state(path: Path, state: dict) -> None:
    run_watch_module.atomic_write_json(path, state)


def due_slots(mode: str, now: datetime) -> list[Slot]:
    current = []
    for slot in SCHEDULES[mode]:
        slot_time = now.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
        if now >= slot_time:
            current.append(slot)
    return current


def prune_days(days: dict[str, dict], today: datetime) -> dict[str, dict]:
    cutoff = today.date().toordinal() - KEEP_DAYS
    kept: dict[str, dict] = {}
    for key, value in days.items():
        try:
            ordinal = datetime.fromisoformat(key).date().toordinal()
        except ValueError:
            continue
        if ordinal >= cutoff:
            kept[key] = value
    return kept


def prune_manual_completions(state: dict, today: datetime) -> bool:
    completions = state.setdefault("manualCompletions", {})
    cutoff = today.date().toordinal() - KEEP_DAYS
    kept: dict[str, dict] = {}
    for request_id, value in completions.items():
        if not isinstance(value, dict):
            continue
        if value.get("status") != "completed":
            kept[str(request_id)] = value
            continue
        completed_at = run_watch_module.parse_iso_datetime(value.get("completedAt"))
        if completed_at is None or completed_at.date().toordinal() >= cutoff:
            kept[str(request_id)] = value
    if kept == completions:
        return False
    state["manualCompletions"] = kept
    return True


def run_watch(
    now: datetime,
    run_id: str | None = None,
    *,
    schedule_date: str = "",
    schedule_slots: list[str] | None = None,
    manual_request_id: str = "",
    heartbeat_config: dict[str, str] | None = None,
) -> tuple[int, str]:
    run_id = run_id or f"auto-{now.strftime('%Y%m%d-%H%M%S')}"
    command = [str(PYTHON_BIN), str(RUN_WATCH), "--run-id", run_id]
    if schedule_date:
        command.extend(["--schedule-date", schedule_date])
    if schedule_slots:
        command.extend(["--schedule-slots", ",".join(schedule_slots)])
    if manual_request_id:
        command.extend(["--manual-request-id", manual_request_id])
    process = subprocess.Popen(command, cwd=REPO_ROOT)
    while True:
        try:
            return process.wait(timeout=60), run_id
        except subprocess.TimeoutExpired:
            if manual_request_id and heartbeat_config:
                heartbeat_manual_run(heartbeat_config, manual_request_id)


def deliver_run(run_id: str) -> int:
    result = subprocess.run(
        [str(PYTHON_BIN), str(RUN_WATCH), "--deliver-run", run_id],
        cwd=REPO_ROOT,
        check=False,
    )
    return result.returncode


def load_notification_config() -> dict[str, str]:
    config: dict[str, str] = {}
    if NOTIFICATION_ENV_FILE.exists():
        for raw_line in NOTIFICATION_ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in os.environ.items():
        if key.startswith("AUCTION_WATCH_"):
            config[key] = value
    return config


def run_request_endpoint(config: dict[str, str], action: str = "") -> str:
    base = config.get("AUCTION_WATCH_APP_BASE_URL", "").strip().rstrip("/")
    suffix = f"/{action}" if action else ""
    return f"{base}/api/auction-watch/run-now{suffix}" if base else ""


def post_run_request(config: dict[str, str], action: str, payload: dict | None = None) -> dict:
    endpoint = run_request_endpoint(config, action)
    if not endpoint:
        return {}
    request = Request(
        endpoint,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AuctionWatchScheduler/1.0",
            "X-Consolas-Auction-Watch": "1",
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result if isinstance(result, dict) else {}


def claim_manual_run(config: dict[str, str]) -> dict | None:
    try:
        payload = post_run_request(config, "claim")
    except Exception as exc:
        print(f"manual run queue unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    request = payload.get("request")
    if isinstance(request, dict):
        return request
    running = payload.get("running")
    if isinstance(running, dict):
        return {**running, "_alreadyRunning": True}
    return None


def run_result_detail(run_id: str, exit_code: int) -> str:
    metadata = run_watch_module.read_json_object(
        AGENT_DIR / "runs" / run_id / "run.json"
    )
    return (
        f"run={run_id} exit={exit_code} "
        f"overall={metadata.get('overallStatus', 'unknown')} "
        f"snapshot={metadata.get('snapshotStatus', 'unknown')} "
        f"email={metadata.get('emailStatus', 'unknown')}"
    )


def complete_manual_run(
    config: dict[str, str],
    request_id: str,
    run_id: str,
    exit_code: int,
) -> bool | str:
    metadata = run_watch_module.read_json_object(
        AGENT_DIR / "runs" / run_id / "run.json"
    )
    try:
        response = post_run_request(
            config,
            "complete",
            {
                "id": request_id,
                "success": exit_code == 0,
                "detail": run_result_detail(run_id, exit_code),
                "runId": run_id,
                "snapshotHash": str(metadata.get("snapshotHash") or ""),
                "snapshotStatus": str(metadata.get("snapshotStatus") or "failed"),
                "emailStatus": str(metadata.get("emailStatus") or "failed"),
                "overallStatus": str(metadata.get("overallStatus") or "failed"),
            },
        )
        if response.get("ok") is not True:
            print(
                "manual run completion unavailable: completion endpoint did not acknowledge",
                file=sys.stderr,
            )
            return False
        return True
    except HTTPError as exc:
        if exc.code in {404, 409}:
            print(
                f"manual run completion reached terminal request state: HTTP {exc.code}",
                file=sys.stderr,
            )
            return "terminal"
        print(f"manual run completion unavailable: HTTP {exc.code}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"manual run completion unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def heartbeat_manual_run(config: dict[str, str], request_id: str) -> bool:
    try:
        response = post_run_request(config, "heartbeat", {"id": request_id})
        return response.get("ok") is True
    except Exception as exc:
        print(f"manual run heartbeat unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def queue_manual_completion(
    state: dict,
    *,
    request_id: str,
    run_id: str,
    exit_code: int,
    observed_at: datetime,
) -> bool:
    request_id = request_id.strip()
    run_id = run_id.strip()
    if not request_id or not run_id or exit_code in {2, 75}:
        return False

    completions = state.setdefault("manualCompletions", {})
    existing = completions.get(request_id)
    if isinstance(existing, dict) and existing.get("status") == "completed":
        return False
    if isinstance(existing, dict) and str(existing.get("runId") or "") not in {"", run_id}:
        print(
            f"manual completion conflict for {request_id}: "
            f"stored run={existing.get('runId')} requested run={run_id}",
            file=sys.stderr,
        )
        return False

    normalized = dict(existing) if isinstance(existing, dict) else {}
    normalized.update(
        {
            "requestId": request_id,
            "runId": run_id,
            "exitCode": int(exit_code),
            "status": "pending",
            "createdAt": str(normalized.get("createdAt") or observed_at.isoformat(timespec="seconds")),
            "attempts": int(normalized.get("attempts") or 0),
        }
    )
    if normalized == existing:
        return False
    completions[request_id] = normalized
    return True


def retry_pending_manual_completions(
    state: dict,
    config: dict[str, str],
    observed_at: datetime,
    *,
    request_ids: set[str] | None = None,
) -> bool:
    completions = state.setdefault("manualCompletions", {})
    changed = False
    for request_id in sorted(completions):
        if request_ids is not None and request_id not in request_ids:
            continue
        item = completions.get(request_id)
        if not isinstance(item, dict) or item.get("status") != "pending":
            continue
        run_id = str(item.get("runId") or "")
        try:
            exit_code = int(item.get("exitCode"))
        except (TypeError, ValueError):
            exit_code = 1
        outcome = complete_manual_run(
            config,
            request_id,
            run_id,
            exit_code,
        )
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["lastAttemptAt"] = observed_at.isoformat(timespec="seconds")
        if outcome is True:
            item["status"] = "completed"
            item["completedAt"] = observed_at.isoformat(timespec="seconds")
            item.pop("lastError", None)
        elif outcome == "terminal":
            item["status"] = "dead_letter"
            item["deadLetterAt"] = observed_at.isoformat(timespec="seconds")
            item["lastError"] = "terminal_request_state"
        else:
            item["lastError"] = "completion_not_acknowledged"
        changed = True
    return changed


def pending_manual_completion_ids(state: dict) -> list[str]:
    completions = state.get("manualCompletions")
    if not isinstance(completions, dict):
        return []
    return sorted(
        str(request_id)
        for request_id, item in completions.items()
        if isinstance(item, dict) and item.get("status") == "pending"
    )


def persist_and_retry_manual_completion(
    state: dict,
    config: dict[str, str],
    *,
    request_id: str,
    run_id: str,
    exit_code: int,
    observed_at: datetime,
) -> None:
    queued = queue_manual_completion(
        state,
        request_id=request_id,
        run_id=run_id,
        exit_code=exit_code,
        observed_at=observed_at,
    )
    if queued:
        # The intent is durable before the network call. If the scheduler dies
        # during POST, the same request id is retried idempotently next tick.
        save_state(STATE_FILE, state)
    retried = retry_pending_manual_completions(
        state,
        config,
        observed_at,
        request_ids={request_id},
    )
    if retried:
        save_state(STATE_FILE, state)


def fail_missing_manual_delivery_outbox(
    run_id: str,
    *,
    schedule_date: str,
    schedule_slots: list[str],
    request_id: str,
) -> dict[str, object] | None:
    """Turn a crashed pre-outbox manual worker into an explicit terminal result."""
    run_dir = AGENT_DIR / "runs" / run_id
    metadata = run_watch_module.read_json_object(run_dir / "run.json")
    if not metadata:
        return None
    detail = "runner_exited_before_delivery_outbox"
    publication = run_watch_module.PublicationResult(
        mode=str((metadata.get("snapshot") or {}).get("mode") or ""),
        status="failed",
        configured=False,
        attempted=False,
        detail=detail,
        run_id=run_id,
        snapshot_hash=str(metadata.get("snapshotHash") or ""),
    )
    email_result = run_watch_module.NotificationResult(
        "email",
        enabled=bool(metadata.get("emailStatus") not in {"", "disabled"}),
        attempted=False,
        sent=False,
        detail=detail,
    )
    outbox_item = run_watch_module.record_delivery_outbox(
        run_id,
        run_dir,
        status="failed",
        detail=detail,
        schedule_date=schedule_date,
        schedule_slots=schedule_slots,
        manual_request_id=request_id,
        attempted=False,
    )
    run_watch_module.update_delivery_metadata(
        run_dir,
        publication,
        email_result,
        pending=False,
        outbox_item=outbox_item,
        terminal_error=True,
    )
    return outbox_item


def terminal_delivery_exit_code(item: dict) -> int:
    run_dir_raw = str(item.get("runDir") or "").strip()
    metadata = (
        run_watch_module.read_json_object(Path(run_dir_raw) / "run.json")
        if run_dir_raw
        else {}
    )
    try:
        exit_code = int(metadata.get("exitCode"))
    except (TypeError, ValueError):
        exit_code = 0 if item.get("status") == "completed" else 1
    # A terminal outbox proves the delivery phase crossed its commit point.
    # Preserve metadata when available and infer only from terminal status.
    if exit_code not in {2, 75}:
        return exit_code
    return 0 if item.get("status") == "completed" else 1


def recover_completed_deliveries(
    state: dict,
    *,
    mode: str,
    observed_at: datetime,
) -> bool:
    payload = run_watch_module.load_delivery_outbox()
    changed = False
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or item.get("status") not in {
            "completed",
            "failed",
            "uncertain",
        }:
            continue
        run_id = str(item.get("runId") or "").strip()
        if not run_id:
            continue
        exit_code = terminal_delivery_exit_code(item)
        if item.get("status") in {"completed", "failed", "uncertain"}:
            changed = mark_slots_fulfilled(
                state,
                schedule_date=str(item.get("scheduleDate") or ""),
                mode=mode,
                slots=[str(slot) for slot in item.get("scheduleSlots") or []],
                run_id=run_id,
                observed_at=observed_at,
            ) or changed
        manual_request_id = str(item.get("manualRequestId") or "").strip()
        if manual_request_id:
            changed = queue_manual_completion(
                state,
                request_id=manual_request_id,
                run_id=run_id,
                exit_code=exit_code,
                observed_at=observed_at,
            ) or changed
    return changed


def mark_slots_fulfilled(
    state: dict,
    *,
    schedule_date: str,
    mode: str,
    slots: list[str],
    run_id: str,
    observed_at: datetime,
) -> bool:
    if not schedule_date or not slots:
        return False
    day_state = state.setdefault("days", {}).setdefault(
        schedule_date,
        {"mode": mode, "fulfilled_slots": [], "fulfilledByRunId": {}},
    )
    fulfilled = set(day_state.get("fulfilled_slots") or [])
    fulfilled.update(slots)
    fulfilled_by_run = dict(day_state.get("fulfilledByRunId") or {})
    for slot in slots:
        fulfilled_by_run[slot] = run_id
    effective_mode = str(day_state.get("mode") or mode)
    changed = (
        set(day_state.get("fulfilled_slots") or []) != fulfilled
        or dict(day_state.get("fulfilledByRunId") or {}) != fulfilled_by_run
        or str(day_state.get("mode") or "") != effective_mode
    )
    if not changed:
        return False
    day_state.update(
        {
            "mode": effective_mode,
            "fulfilled_slots": sorted(fulfilled),
            "fulfilledByRunId": fulfilled_by_run,
            "updated_at": observed_at.isoformat(timespec="seconds"),
        }
    )
    return True


def main() -> int:
    args = parse_args()
    if not PYTHON_BIN.exists():
        print(f"Missing runtime: {PYTHON_BIN}", file=sys.stderr)
        return 1

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Scheduler lock busy; skipping.")
            return 0

        now = now_local()
        notification_config = load_notification_config()
        today_key = now.date().isoformat()
        slots_due = due_slots(args.mode, now)
        state = load_state(STATE_FILE)
        # Resolve interrupted `sending` records before deriving fulfilled
        # slots or manual completions. An ambiguous terminal delivery consumes
        # its scan slot; it must never trigger a second automatic scan.
        run_watch_module.recover_interrupted_delivery_outbox()
        previous_days = state.get("days", {})
        state["days"] = prune_days(previous_days, now)
        state_changed = state["days"] != previous_days
        state_changed = prune_manual_completions(state, now) or state_changed
        state_changed = recover_completed_deliveries(
            state,
            mode=args.mode,
            observed_at=now,
        ) or state_changed
        if state_changed and not args.dry_run:
            save_state(STATE_FILE, state)

        if not args.dry_run and retry_pending_manual_completions(
            state,
            notification_config,
            now,
        ):
            save_state(STATE_FILE, state)

        day_state = state["days"].setdefault(
            today_key,
            {"mode": args.mode, "fulfilled_slots": [], "fulfilledByRunId": {}},
        )
        fulfilled = set(day_state.get("fulfilled_slots") or [])
        pending = [slot for slot in slots_due if slot.key not in fulfilled]

        print(
            f"now={now.isoformat(timespec='seconds')} mode={args.mode} "
            f"due={[slot.key for slot in slots_due]} fulfilled={sorted(fulfilled)} pending={[slot.key for slot in pending]}"
        )

        queued_deliveries = run_watch_module.pending_delivery_items(
            observed_at=now,
            due_only=False,
        )
        if queued_deliveries:
            delivery = queued_deliveries[0]
            run_id = str(delivery.get("runId") or "")
            next_attempt = run_watch_module.parse_iso_datetime(delivery.get("nextAttemptAt"))
            if args.dry_run:
                print(f"Dry run: scheduler would prioritize delivery for {run_id}.")
                return 0
            queued_manual_request_id = str(delivery.get("manualRequestId") or "").strip()
            if queued_manual_request_id:
                acknowledged = complete_manual_run(
                    notification_config,
                    queued_manual_request_id,
                    run_id,
                    2,
                )
                if not acknowledged:
                    print(
                        f"Manual delivery-pending report for {queued_manual_request_id} "
                        "was not acknowledged; preserving the correlated outbox."
                    )
            if next_attempt is not None and next_attempt > now:
                print(
                    f"Delivery pending for {run_id}; next attempt "
                    f"{next_attempt.isoformat(timespec='seconds')}."
                )
                return 0

            exit_code = deliver_run(run_id)
            if exit_code == 75:
                return 0
            run_watch_module.recover_interrupted_delivery_outbox()
            refreshed = run_watch_module.delivery_outbox_item(run_id) or delivery
            if refreshed.get("status") in {"completed", "failed", "uncertain"}:
                slots_changed = mark_slots_fulfilled(
                    state,
                    schedule_date=str(refreshed.get("scheduleDate") or ""),
                    mode=args.mode,
                    slots=[str(item) for item in refreshed.get("scheduleSlots") or []],
                    run_id=run_id,
                    observed_at=now,
                )
                if slots_changed:
                    save_state(STATE_FILE, state)
            manual_request_id = str(refreshed.get("manualRequestId") or "").strip()
            if (
                manual_request_id
                and exit_code not in {2, 75}
                and refreshed.get("status") in {"completed", "failed", "uncertain"}
            ):
                persist_and_retry_manual_completion(
                    state,
                    notification_config,
                    request_id=manual_request_id,
                    run_id=run_id,
                    exit_code=exit_code,
                    observed_at=now,
                )
            if exit_code == 2 or refreshed.get("status") == "pending":
                print(
                    f"Delivery remains pending for {run_id}; the durable outbox "
                    "will retry it before any new scan."
                )
                return 0
            return exit_code

        pending_completion_ids = pending_manual_completion_ids(state)
        if not args.dry_run and pending_completion_ids:
            print(
                "Manual run completion is still pending for "
                f"{', '.join(pending_completion_ids)}; scheduler will retry it "
                "before any new claim or scan."
            )
            return 0

        if not args.dry_run:
            manual_request = claim_manual_run(notification_config)
            if manual_request:
                request_id = str(manual_request.get("id") or "")
                run_id = f"manual-{request_id}"
                if manual_request.get("_alreadyRunning") is True:
                    print(
                        f"Resuming manual Auction Watch request: {request_id} "
                        "with its stable run identity."
                    )
                else:
                    print(f"Claimed manual Auction Watch request: {request_id}")
                exit_code, run_id = run_watch(
                    now,
                    run_id=run_id,
                    schedule_date=today_key,
                    schedule_slots=[slot.key for slot in pending],
                    manual_request_id=request_id,
                    heartbeat_config=notification_config,
                )
                if exit_code == 75:
                    heartbeat_manual_run(notification_config, request_id)
                    print(
                        f"Manual Auction Watch request {request_id} is still running; "
                        "scheduler will not mark it failed."
                    )
                    return 0
                run_watch_module.recover_interrupted_delivery_outbox()
                resumed_delivery = run_watch_module.delivery_outbox_item(run_id)
                if isinstance(resumed_delivery, dict) and resumed_delivery.get("status") in {
                    "pending",
                    "sending",
                }:
                    complete_manual_run(
                        notification_config,
                        request_id,
                        run_id,
                        2,
                    )
                    print(
                        f"Manual Auction Watch run {run_id} still has delivery pending; "
                        "scheduler will not complete it as terminal."
                    )
                    return 0
                if resumed_delivery is None and exit_code not in {0, 2, 75}:
                    resumed_delivery = fail_missing_manual_delivery_outbox(
                        run_id,
                        schedule_date=today_key,
                        schedule_slots=[slot.key for slot in pending],
                        request_id=request_id,
                    )
                if exit_code == 2:
                    acknowledged = complete_manual_run(
                        notification_config,
                        request_id,
                        run_id,
                        exit_code,
                    )
                    print(
                        f"Manual Auction Watch run {run_id} has delivery pending; "
                        f"request {request_id} remains correlated with its outbox "
                        f"(backend_ack={str(acknowledged).lower()})."
                    )
                    return 0
                terminal_delivery = run_watch_module.delivery_outbox_item(run_id)
                if isinstance(terminal_delivery, dict) and terminal_delivery.get("status") in {
                    "completed",
                    "failed",
                    "uncertain",
                }:
                    slots_changed = mark_slots_fulfilled(
                        state,
                        schedule_date=today_key,
                        mode=args.mode,
                        slots=[slot.key for slot in pending],
                        run_id=run_id,
                        observed_at=now,
                    )
                    if slots_changed:
                        save_state(STATE_FILE, state)
                persist_and_retry_manual_completion(
                    state,
                    notification_config,
                    request_id=request_id,
                    run_id=run_id,
                    exit_code=exit_code,
                    observed_at=now,
                )
                return exit_code

        if not pending:
            return 0

        if args.dry_run:
            print("Dry run: scheduler would execute auction-watch now.")
            return 0

        exit_code, run_id = run_watch(
            now,
            schedule_date=today_key,
            schedule_slots=[slot.key for slot in pending],
        )
        if exit_code == 75:
            return 0
        run_watch_module.recover_interrupted_delivery_outbox()
        if exit_code == 2:
            print(
                f"Auction Watch run {run_id} has delivery pending; the durable "
                "outbox will retry it before any new scan."
            )
            return 0
        terminal_delivery = run_watch_module.delivery_outbox_item(run_id)
        if isinstance(terminal_delivery, dict) and terminal_delivery.get("status") in {
            "completed",
            "failed",
            "uncertain",
        }:
            mark_slots_fulfilled(
                state,
                schedule_date=today_key,
                mode=args.mode,
                slots=[slot.key for slot in pending],
                run_id=run_id,
                observed_at=now,
            )
            save_state(STATE_FILE, state)
            print(f"Scheduler marked slots as fulfilled by terminal run {run_id}: {[slot.key for slot in pending]}")
            return exit_code

        if exit_code != 0:
            print(f"auction-watch exited with code {exit_code}", file=sys.stderr)
            return exit_code

        mark_slots_fulfilled(
            state,
            schedule_date=today_key,
            mode=args.mode,
            slots=[slot.key for slot in pending],
            run_id=run_id,
            observed_at=now,
        )
        save_state(STATE_FILE, state)
        print(f"Scheduler marked slots as fulfilled by {run_id}: {[slot.key for slot in pending]}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
