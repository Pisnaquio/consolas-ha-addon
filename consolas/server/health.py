"""Health payload for /api/health."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

try:
    from .auction_watch_snapshot import read_auction_watch_snapshot
    from .config import AppConfig
    from .constants import SERVICE_NAME
    from .database import connect_db
except ImportError:  # Direct execution: `python3 server/app.py`.
    from auction_watch_snapshot import read_auction_watch_snapshot
    from config import AppConfig
    from constants import SERVICE_NAME
    from database import connect_db

# Bound by server/app.py, which owns SERVICE_VERSION: the release script stamps
# the version there.
SERVICE_VERSION: str


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
