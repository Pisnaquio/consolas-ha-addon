"""Canonical mutable runtime paths for Auction Watch.

The add-on image contains code and base resources only. Mutable scan state is
kept under AUCTION_WATCH_RUNTIME_ROOT when configured, while local checkouts
fall back to the agent directory for backwards compatibility.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


DEFAULT_WATCHLIST = "[]\n"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    runs: Path
    latest: Path
    latest_matches: Path
    state: Path
    delivery_outbox: Path
    run_lock: Path
    watchlist: Path
    dismissals_cache: Path
    logs: Path
    schedule_state: Path
    schedule_lock: Path


def resolve_runtime_root(agent_dir: Path) -> Path:
    configured = os.environ.get("AUCTION_WATCH_RUNTIME_ROOT", "").strip()
    return Path(configured).expanduser() if configured else agent_dir


def resolve_runtime_paths(agent_dir: Path) -> RuntimePaths:
    root = resolve_runtime_root(agent_dir)
    runs = root / "runs"
    return RuntimePaths(
        root=root,
        runs=runs,
        latest=runs / "latest",
        latest_matches=runs / "latest-matches",
        state=root / "state.json",
        delivery_outbox=root / "delivery-outbox.json",
        run_lock=root / "run.lock",
        watchlist=root / "watchlist.json",
        dismissals_cache=root / "dismissals-cache.json",
        logs=root / "logs",
        schedule_state=root / "schedule_state.json",
        schedule_lock=root / "schedule.lock",
    )


def bootstrap_runtime(agent_dir: Path) -> RuntimePaths:
    """Create mutable directories and bootstrap only the watchlist once."""
    paths = resolve_runtime_paths(agent_dir)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.runs.mkdir(parents=True, exist_ok=True)
    paths.logs.mkdir(parents=True, exist_ok=True)

    if not paths.watchlist.exists():
        packaged_watchlist = agent_dir / "watchlist.json"
        if packaged_watchlist.exists() and packaged_watchlist != paths.watchlist:
            shutil.copyfile(packaged_watchlist, paths.watchlist)
        else:
            paths.watchlist.write_text(DEFAULT_WATCHLIST, encoding="utf-8")
    return paths
