from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, AGENT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from runtime_paths import bootstrap_runtime, resolve_runtime_paths  # noqa: E402


class AuctionWatchRuntimePersistenceTests(unittest.TestCase):
    def run_runtime_process(self, code: str, runtime_root: Path) -> None:
        env = os.environ.copy()
        env["AUCTION_WATCH_RUNTIME_ROOT"] = str(runtime_root)
        python_path = os.pathsep.join((str(REPO_ROOT), str(AGENT_DIR), env.get("PYTHONPATH", "")))
        env["PYTHONPATH"] = python_path
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")

    def test_unconfigured_runtime_keeps_local_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"AUCTION_WATCH_RUNTIME_ROOT": ""}, clear=False
        ):
            agent_dir = Path(temp_dir) / "agent"
            paths = resolve_runtime_paths(agent_dir)

        self.assertEqual(paths.root, agent_dir)
        self.assertEqual(paths.runs, agent_dir / "runs")
        self.assertEqual(paths.state, agent_dir / "state.json")
        self.assertEqual(paths.schedule_lock, agent_dir / "schedule.lock")

    def test_bootstrap_copies_watchlist_once_and_never_seeds_other_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_dir = root / "agent"
            runtime_root = root / "runtime"
            agent_dir.mkdir()
            packaged_watchlist = b'{"base": "byte-preserved"}\n'
            (agent_dir / "watchlist.json").write_bytes(packaged_watchlist)

            with patch.dict(os.environ, {"AUCTION_WATCH_RUNTIME_ROOT": str(runtime_root)}):
                paths = bootstrap_runtime(agent_dir)
                self.assertEqual(paths.watchlist.read_bytes(), packaged_watchlist)
                paths.watchlist.write_bytes(b'{"user": "edited"}\n')
                bootstrap_runtime(agent_dir)

            self.assertEqual(paths.watchlist.read_bytes(), b'{"user": "edited"}\n')
            self.assertFalse(paths.state.exists())
            self.assertFalse(paths.delivery_outbox.exists())
            self.assertFalse((paths.runs / "latest").exists())

    def test_two_processes_share_runtime_state_schedule_outbox_runs_locks_and_watchlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "auction-watch-runtime"
            local_state_path = AGENT_DIR / "state.json"
            local_state_before = local_state_path.read_bytes() if local_state_path.exists() else None
            first_process = r'''
import json
from pathlib import Path
import scripts.run_watch as runner
import scripts.run_watch_if_due as scheduler
import scripts.manage_watchlist as manager
import scripts.export_web_snapshot as exporter

runner.ensure_runtime()
assert runner.RUNTIME_ROOT == Path(__import__("os").environ["AUCTION_WATCH_RUNTIME_ROOT"])
assert runner.RUNS_DIR == scheduler.runtime_runs_dir()
assert manager.WATCHLIST_FILE == runner.WATCHLIST_FILE
assert exporter.LATEST_DIR == runner.LATEST_DIR
assert exporter.DEFAULT_DISMISSALS == runner.DISMISSALS_CACHE_FILE
assert runner.RUN_LOCK_FILE.parent == runner.STATE_FILE.parent
assert scheduler.LOCK_FILE.parent == runner.STATE_FILE.parent
runner.atomic_write_json(runner.STATE_FILE, {"version": 4, "processed_bavastro_auction_ids": [123], "processed_castells_remate_ids": [456]})
runner.atomic_write_json(runner.DELIVERY_OUTBOX_FILE, {"version": 1, "items": [{"runId": "persisted"}]})
runner.atomic_write_json(scheduler.STATE_FILE, {"version": 2, "days": {}, "manualCompletions": {"request-fresh": {"runId": "persisted-run", "status": "completed"}}})
runner.RUN_LOCK_FILE.touch()
scheduler.LOCK_FILE.touch()
manager.save_watchlist([{"id": "persisted-watch"}])
run_dir = runner.RUNS_DIR / "persisted-run"
run_dir.mkdir(parents=True)
runner.atomic_write_json(run_dir / "run.json", {"runId": "persisted-run", "scanStatus": "success", "snapshotStatus": "published", "overallStatus": "completed", "completedAt": "2026-08-25T16:59:00-03:00"})
'''
            second_process = r'''
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import scripts.run_watch as runner
import scripts.run_watch_if_due as scheduler
import scripts.manage_watchlist as manager

assert runner.RUNS_DIR == scheduler.runtime_runs_dir()
assert runner.load_state(runner.STATE_FILE).processed_bavastro_auction_ids == {123}
assert runner.load_state(runner.STATE_FILE).processed_castells_remate_ids == {456}
assert json.loads(runner.DELIVERY_OUTBOX_FILE.read_text())["items"][0]["runId"] == "persisted"
state = scheduler.load_state(scheduler.STATE_FILE)
changed = scheduler.satisfy_slots_from_fresh_manual(
    state,
    mode="twice",
    schedule_date="2026-08-25",
    slots=[scheduler.Slot("afternoon", 17, 10)],
    now=datetime(2026, 8, 25, 17, 10, tzinfo=ZoneInfo("America/Montevideo")),
)
assert changed
scheduler.save_state(scheduler.STATE_FILE, state)
assert state["days"]["2026-08-25"]["fulfilledByRunId"]["afternoon"] == "persisted-run"
assert (runner.RUNS_DIR / "persisted-run" / "run.json").exists()
assert manager.load_watchlist() == [{"id": "persisted-watch"}]
assert runner.RUN_LOCK_FILE.exists()
assert scheduler.LOCK_FILE.exists()
assert runner.RUNS_DIR != runner.AGENT_DIR / "runs"
'''
            self.run_runtime_process(first_process, runtime_root)
            self.run_runtime_process(second_process, runtime_root)

            self.assertTrue((runtime_root / "run.lock").exists())
            self.assertTrue((runtime_root / "schedule.lock").exists())
            self.assertEqual(
                json.loads((runtime_root / "schedule_state.json").read_text())["days"]["2026-08-25"]["fulfilledByRunId"]["afternoon"],
                "persisted-run",
            )
            self.assertFalse((AGENT_DIR / "runs" / "persisted-run").exists())
            if local_state_before is None:
                self.assertFalse(local_state_path.exists())
            else:
                self.assertEqual(local_state_path.read_bytes(), local_state_before)


if __name__ == "__main__":
    unittest.main()
