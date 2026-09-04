#!/usr/bin/env python3
"""Periodic backup of the Consolas server-side state.

Runs from a polling loop (see backup_state_loop.sh) and is a no-op unless
enough time has passed since the last successful backup. Each backup:

- copies the live SQLite database via the sqlite3 online backup API (safe
  against a running writer, unlike a plain file copy),
- also pulls /api/state/export over HTTP as a human-readable, restorable
  JSON snapshot (see server/app.py's build_state_export / restore_state),
- rotates old backups, keeping only the most recent CONSOLAS_BACKUP_RETENTION.

Mirrors the "if_due" polling pattern already used by
agents/auction-watch/scripts/run_watch_if_due.py so operators only have to
learn one scheduling idiom for this add-on.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DATA_DIR = "/data"
DATABASE_NAME = "consolas.sqlite"
STATE_FILE_NAME = ".backup_state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_backup_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def is_due(state: dict, interval_hours: float) -> bool:
    last_at = state.get("lastBackupAt")
    if not last_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_at)
    except ValueError:
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    return elapsed_hours >= interval_hours


def backup_sqlite(db_path: Path, dest_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        source = sqlite3.connect(str(db_path))
        try:
            dest = sqlite3.connect(str(dest_path))
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        return True
    except (OSError, sqlite3.Error):
        return False


def export_state_json(base_url: str, dest_path: Path) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/state/export", timeout=30) as response:
            payload = response.read()
        dest_path.write_bytes(payload)
    except (OSError, urllib.error.URLError, TimeoutError, ConnectionError):
        return False
    return True


def rotate_backups(backups_dir: Path, retention: int) -> list[str]:
    entries = sorted(
        (p for p in backups_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    removed = []
    while len(entries) > retention:
        oldest = entries.pop(0)
        shutil.rmtree(oldest, ignore_errors=True)
        removed.append(oldest.name)
    return removed


def main() -> int:
    data_dir = Path(os.getenv("CONSOLAS_DATA_DIR", DEFAULT_DATA_DIR))
    db_path = data_dir / DATABASE_NAME
    backups_dir = data_dir / "backups"
    state_path = backups_dir / STATE_FILE_NAME
    interval_hours = float(os.getenv("CONSOLAS_BACKUP_INTERVAL_HOURS", "24"))
    retention = int(os.getenv("CONSOLAS_BACKUP_RETENTION", "14"))
    port = os.getenv("CONSOLAS_PORT", "8788")
    base_url = os.getenv("CONSOLAS_BACKUP_APP_BASE_URL", f"http://127.0.0.1:{port}")

    backups_dir.mkdir(parents=True, exist_ok=True)
    state = load_backup_state(state_path)

    if not is_due(state, interval_hours):
        print(json.dumps({"status": "skipped", "reason": "not_due", "lastBackupAt": state.get("lastBackupAt")}))
        return 0

    if not db_path.exists():
        print(json.dumps({"status": "skipped", "reason": "no_database_yet", "dbPath": str(db_path)}))
        return 0

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = backups_dir / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    db_ok = backup_sqlite(db_path, backup_dir / DATABASE_NAME)
    export_ok = db_ok and export_state_json(base_url, backup_dir / "state-export.json")

    # A backup is only complete when the SQLite snapshot and its matching
    # state export exist together. Do not advance lastBackupAt after a partial
    # attempt: the loop must retry it, especially during server startup.
    if not (db_ok and export_ok):
        shutil.rmtree(backup_dir, ignore_errors=True)
        print(
            json.dumps(
                {
                    "status": "degraded",
                    "dbBackedUp": db_ok,
                    "stateExportBackedUp": export_ok,
                    "reason": "backup_incomplete_will_retry",
                }
            )
        )
        return 1

    removed = rotate_backups(backups_dir, retention)

    state = {
        "lastBackupAt": now_iso(),
        "lastBackupPath": str(backup_dir),
        "lastBackupDbOk": db_ok,
        "lastBackupExportOk": export_ok,
    }
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "backupDir": str(backup_dir),
                "dbBackedUp": db_ok,
                "stateExportBackedUp": export_ok,
                "rotatedOut": removed,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
