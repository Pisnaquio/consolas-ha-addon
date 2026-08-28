from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, AGENT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import scripts.run_watch as run_watch  # noqa: E402
import scripts.run_watch_if_due as scheduler  # noqa: E402


class SchedulerDurabilityTests(unittest.TestCase):
    def test_v1_state_migrates_additively_without_losing_fulfilled_slots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "schedule.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "days": {
                            "2026-08-24": {
                                "mode": "twice",
                                "fulfilled_slots": ["morning"],
                                "fulfilledByRunId": {"morning": "auto-run-1"},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            migrated = scheduler.load_state(state_path)

        self.assertEqual(migrated["version"], 2)
        self.assertEqual(
            migrated["days"]["2026-08-24"]["fulfilled_slots"],
            ["morning"],
        )
        self.assertEqual(
            migrated["days"]["2026-08-24"]["fulfilledByRunId"]["morning"],
            "auto-run-1",
        )
        self.assertEqual(migrated["manualCompletions"], {})

    def test_failed_manual_completion_is_retried_from_durable_state(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("America/Montevideo"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "schedule.json"
            lock_path = root / "schedule.lock"
            run_dir = root / "runs" / "manual-run-1"
            run_dir.mkdir(parents=True)
            run_watch.write_json(
                run_dir / "run.json",
                {
                    "runId": "manual-run-1",
                    "exitCode": 0,
                    "snapshotHash": "a" * 64,
                    "snapshotStatus": "published",
                    "emailStatus": "sent",
                    "overallStatus": "completed",
                },
            )
            completed_delivery = {
                "runId": "manual-run-1",
                "runDir": str(run_dir),
                "status": "completed",
                "scheduleDate": "2026-08-24",
                "scheduleSlots": ["morning"],
                "manualRequestId": "run_request_1",
            }

            with (
                patch.object(scheduler, "AGENT_DIR", root),
                patch.object(scheduler, "STATE_FILE", state_path),
                patch.object(scheduler, "LOCK_FILE", lock_path),
                patch.object(scheduler, "PYTHON_BIN", Path(sys.executable)),
                patch.object(
                    scheduler,
                    "now_local",
                    side_effect=[now, now + timedelta(minutes=1)],
                ),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=False),
                ),
                patch.object(
                    scheduler,
                    "load_notification_config",
                    return_value={"AUCTION_WATCH_APP_BASE_URL": "http://app.test"},
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "load_delivery_outbox",
                    return_value={"version": 1, "items": [completed_delivery]},
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "pending_delivery_items",
                    return_value=[],
                ),
                patch.object(scheduler, "claim_manual_run", return_value=None),
                patch.object(
                    scheduler,
                    "complete_manual_run",
                    side_effect=[False, True],
                ) as complete,
                patch.object(scheduler, "run_watch") as scan,
            ):
                first_exit = scheduler.main()
                first_state = json.loads(state_path.read_text(encoding="utf-8"))
                second_exit = scheduler.main()

            final_state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual((first_exit, second_exit), (0, 0))
        self.assertEqual(
            first_state["manualCompletions"]["run_request_1"]["status"],
            "pending",
        )
        self.assertEqual(
            final_state["manualCompletions"]["run_request_1"]["status"],
            "completed",
        )
        self.assertEqual(
            final_state["manualCompletions"]["run_request_1"]["attempts"],
            2,
        )
        self.assertEqual(complete.call_count, 2)
        scan.assert_not_called()

    def test_completed_outbox_recovers_slot_before_scan_decision(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("America/Montevideo"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "schedule.json"
            lock_path = root / "schedule.lock"
            run_dir = root / "runs" / "auto-run-1"
            run_dir.mkdir(parents=True)
            run_watch.write_json(
                run_dir / "run.json",
                {"runId": "auto-run-1", "exitCode": 0, "overallStatus": "completed"},
            )
            completed_delivery = {
                "runId": "auto-run-1",
                "runDir": str(run_dir),
                "status": "completed",
                "scheduleDate": "2026-08-24",
                "scheduleSlots": ["morning"],
                "manualRequestId": "",
            }

            with (
                patch.object(scheduler, "STATE_FILE", state_path),
                patch.object(scheduler, "LOCK_FILE", lock_path),
                patch.object(scheduler, "PYTHON_BIN", Path(sys.executable)),
                patch.object(scheduler, "now_local", return_value=now),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=False),
                ),
                patch.object(scheduler, "load_notification_config", return_value={}),
                patch.object(
                    scheduler.run_watch_module,
                    "load_delivery_outbox",
                    return_value={"version": 1, "items": [completed_delivery]},
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "pending_delivery_items",
                    return_value=[],
                ),
                patch.object(scheduler, "claim_manual_run", return_value=None),
                patch.object(scheduler, "run_watch") as scan,
            ):
                exit_code = scheduler.main()

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        scan.assert_not_called()
        self.assertEqual(
            state["days"]["2026-08-24"]["fulfilledByRunId"]["morning"],
            "auto-run-1",
        )

    def test_unacknowledged_terminal_manual_completion_blocks_claims_and_scans(self) -> None:
        now = datetime(2026, 8, 24, 18, 0, tzinfo=ZoneInfo("America/Montevideo"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "schedule.json"
            lock_path = root / "schedule.lock"
            run_dir = root / "runs" / "manual-uncertain-1"
            run_dir.mkdir(parents=True)
            run_watch.write_json(
                run_dir / "run.json",
                {
                    "runId": "manual-uncertain-1",
                    "exitCode": 1,
                    "snapshotHash": "b" * 64,
                    "snapshotStatus": "published",
                    "emailStatus": "uncertain",
                    "overallStatus": "failed",
                },
            )
            uncertain_delivery = {
                "runId": "manual-uncertain-1",
                "runDir": str(run_dir),
                "status": "uncertain",
                "scheduleDate": "2026-08-24",
                "scheduleSlots": ["morning", "afternoon"],
                "manualRequestId": "run_request_uncertain",
            }

            with (
                patch.object(scheduler, "AGENT_DIR", root),
                patch.object(scheduler, "STATE_FILE", state_path),
                patch.object(scheduler, "LOCK_FILE", lock_path),
                patch.object(scheduler, "PYTHON_BIN", Path(sys.executable)),
                patch.object(scheduler, "now_local", return_value=now),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=False),
                ),
                patch.object(scheduler, "load_notification_config", return_value={}),
                patch.object(
                    scheduler.run_watch_module,
                    "load_delivery_outbox",
                    return_value={"version": 1, "items": [uncertain_delivery]},
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "pending_delivery_items",
                    return_value=[],
                ),
                patch.object(scheduler, "post_run_request", return_value={}) as post,
                patch.object(scheduler, "claim_manual_run") as claim,
                patch.object(scheduler, "run_watch") as scan,
            ):
                exit_code = scheduler.main()

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(post.call_args.args[1], "complete")
        self.assertEqual(post.call_args.args[2]["runId"], "manual-uncertain-1")
        self.assertFalse(post.call_args.args[2]["success"])
        self.assertEqual(post.call_args.args[2]["emailStatus"], "uncertain")
        self.assertEqual(post.call_args.args[2]["overallStatus"], "failed")
        claim.assert_not_called()
        scan.assert_not_called()
        self.assertEqual(
            state["manualCompletions"]["run_request_uncertain"]["status"],
            "pending",
        )
        self.assertEqual(
            state["manualCompletions"]["run_request_uncertain"]["exitCode"],
            1,
        )
        self.assertEqual(
            state.get("days", {}).get("2026-08-24", {}).get("fulfilled_slots", []),
            ["afternoon", "morning"],
        )

    def test_pending_manual_completion_does_not_block_dry_run_preview(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("America/Montevideo"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "schedule.json"
            lock_path = root / "schedule.lock"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "days": {},
                        "manualCompletions": {
                            "run_request_pending": {
                                "requestId": "run_request_pending",
                                "runId": "manual-run-pending",
                                "exitCode": 1,
                                "status": "pending",
                                "attempts": 1,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            printed: list[str] = []

            with (
                patch.object(scheduler, "STATE_FILE", state_path),
                patch.object(scheduler, "LOCK_FILE", lock_path),
                patch.object(scheduler, "PYTHON_BIN", Path(sys.executable)),
                patch.object(scheduler, "now_local", return_value=now),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=True),
                ),
                patch.object(scheduler, "load_notification_config", return_value={}),
                patch.object(
                    scheduler.run_watch_module,
                    "load_delivery_outbox",
                    return_value={"version": 1, "items": []},
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "pending_delivery_items",
                    return_value=[],
                ),
                patch(
                    "builtins.print",
                    side_effect=lambda *args, **_kwargs: printed.append(
                        " ".join(map(str, args))
                    ),
                ),
                patch.object(
                    scheduler,
                    "complete_manual_run",
                    return_value=True,
                ) as complete,
            ):
                exit_code = scheduler.main()

        self.assertEqual(exit_code, 0)
        complete.assert_not_called()
        self.assertTrue(
            any(
                "Dry run: scheduler would execute auction-watch now." in line
                for line in printed
            )
        )
        self.assertFalse(
            any("Manual run completion is still pending" in line for line in printed)
        )

    def test_existing_manual_lease_retries_the_same_stable_run_after_lock_contention(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("America/Montevideo"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(scheduler, "STATE_FILE", root / "schedule.json"),
                patch.object(scheduler, "LOCK_FILE", root / "schedule.lock"),
                patch.object(scheduler, "PYTHON_BIN", Path(sys.executable)),
                patch.object(scheduler, "now_local", return_value=now),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=False),
                ),
                patch.object(scheduler, "load_notification_config", return_value={}),
                patch.object(
                    scheduler.run_watch_module,
                    "load_delivery_outbox",
                    return_value={"version": 1, "items": []},
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "pending_delivery_items",
                    return_value=[],
                ),
                patch.object(
                    scheduler,
                    "claim_manual_run",
                    return_value={"id": "run_existing", "_alreadyRunning": True},
                ),
                patch.object(
                    scheduler,
                    "run_watch",
                    return_value=(75, "manual-run_existing"),
                ) as scan,
            ):
                exit_code = scheduler.main()

        self.assertEqual(exit_code, 0)
        scan.assert_called_once()
        self.assertEqual(scan.call_args.kwargs["run_id"], "manual-run_existing")

    def test_fresh_successful_manual_publication_satisfies_only_the_next_slot(self) -> None:
        now = datetime(2026, 8, 24, 17, 10, tzinfo=ZoneInfo("America/Montevideo"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "schedule.json"
            lock_path = root / "schedule.lock"
            run_id = "manual-fresh-1659"
            run_dir = root / "runs" / run_id
            run_dir.mkdir(parents=True)
            run_watch.write_json(
                run_dir / "run.json",
                {
                    "runId": run_id,
                    "scanStatus": "success",
                    "snapshotStatus": "published",
                    "overallStatus": "completed",
                    "completedAt": "2026-08-24T16:59:00-03:00",
                },
            )
            state_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "days": {
                            "2026-08-24": {
                                "mode": "twice",
                                "fulfilled_slots": ["morning"],
                                "fulfilledByRunId": {"morning": "auto-morning"},
                            }
                        },
                        "manualCompletions": {
                            "run_request_fresh": {
                                "requestId": "run_request_fresh",
                                "runId": run_id,
                                "status": "completed",
                                "completedAt": "2026-08-24T16:59:00-03:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(scheduler, "AGENT_DIR", root),
                patch.object(scheduler, "STATE_FILE", state_path),
                patch.object(scheduler, "LOCK_FILE", lock_path),
                patch.object(scheduler, "PYTHON_BIN", Path(sys.executable)),
                patch.object(scheduler, "now_local", return_value=now),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=False),
                ),
                patch.object(scheduler, "load_notification_config", return_value={}),
                patch.object(
                    scheduler.run_watch_module,
                    "load_delivery_outbox",
                    return_value={"version": 1, "items": []},
                ),
                patch.object(scheduler.run_watch_module, "pending_delivery_items", return_value=[]),
                patch.object(scheduler, "claim_manual_run", return_value=None),
                patch.object(scheduler, "run_watch") as scan,
            ):
                exit_code = scheduler.main()

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        scan.assert_not_called()
        self.assertEqual(
            state["days"]["2026-08-24"]["fulfilledByRunId"]["afternoon"],
            run_id,
        )
        self.assertEqual(state["days"]["2026-08-24"]["fulfilled_slots"], ["afternoon", "morning"])

    def test_old_manual_publication_does_not_satisfy_the_scheduled_slot(self) -> None:
        now = datetime(2026, 8, 24, 17, 10, tzinfo=ZoneInfo("America/Montevideo"))
        state = {
            "version": 2,
            "days": {},
            "manualCompletions": {
                "run_request_old": {
                    "requestId": "run_request_old",
                    "runId": "manual-old",
                    "status": "completed",
                }
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "runs" / "manual-old"
            run_dir.mkdir(parents=True)
            run_watch.write_json(
                run_dir / "run.json",
                {
                    "runId": "manual-old",
                    "scanStatus": "success",
                    "snapshotStatus": "published",
                    "overallStatus": "completed",
                    "completedAt": "2026-08-24T16:00:00-03:00",
                },
            )
            with patch.object(scheduler, "AGENT_DIR", root):
                changed = scheduler.satisfy_slots_from_fresh_manual(
                    state,
                    mode="twice",
                    schedule_date="2026-08-24",
                    slots=[scheduler.Slot("afternoon", 17, 10)],
                    now=now,
                )

        self.assertFalse(changed)
        self.assertEqual(state["days"], {})

    def test_terminal_completion_conflict_is_dead_lettered_without_freezing_schedule(self) -> None:
        state = {
            "version": 2,
            "days": {},
            "manualCompletions": {
                "run_request_terminal": {
                    "requestId": "run_request_terminal",
                    "runId": "manual-run_request_terminal",
                    "exitCode": 1,
                    "status": "pending",
                    "attempts": 0,
                }
            },
        }
        now = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("America/Montevideo"))
        with patch.object(scheduler, "complete_manual_run", return_value="terminal"):
            changed = scheduler.retry_pending_manual_completions(state, {}, now)

        item = state["manualCompletions"]["run_request_terminal"]
        self.assertTrue(changed)
        self.assertEqual(item["status"], "dead_letter")
        self.assertEqual(scheduler.pending_manual_completion_ids(state), [])

    def test_crash_before_outbox_is_synthesized_as_terminal_before_manual_ack(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("America/Montevideo"))
        run_id = "manual-run_crashed"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "runs" / run_id
            (run_dir / "logs").mkdir(parents=True)
            run_watch.write_json(
                run_dir / "run.json",
                {
                    "runId": run_id,
                    "scanStatus": "failed",
                    "snapshotStatus": "failed",
                    "emailStatus": "pending",
                    "overallStatus": "delivery_pending",
                    "notifications": [],
                },
            )
            outbox_path = root / "outbox.json"
            with (
                patch.object(scheduler, "AGENT_DIR", root),
                patch.object(scheduler, "STATE_FILE", root / "schedule.json"),
                patch.object(scheduler, "LOCK_FILE", root / "schedule.lock"),
                patch.object(scheduler, "PYTHON_BIN", Path(sys.executable)),
                patch.object(scheduler, "now_local", return_value=now),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=False),
                ),
                patch.object(scheduler, "load_notification_config", return_value={}),
                patch.object(scheduler.run_watch_module, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(scheduler.run_watch_module, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(scheduler, "claim_manual_run", return_value={"id": "run_crashed"}),
                patch.object(scheduler, "run_watch", return_value=(1, run_id)),
                patch.object(scheduler, "post_run_request", return_value={"ok": True}) as post,
            ):
                exit_code = scheduler.main()

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            outbox = json.loads(outbox_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(post.call_args.args[2]["overallStatus"], "failed")
        self.assertEqual(metadata["overallStatus"], "failed")
        self.assertEqual(outbox["items"][0]["status"], "failed")

    def test_manual_exit_two_posts_nonterminal_delivery_pending_metadata(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("America/Montevideo"))
        expected_run_id = "manual-run_request_pending"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "schedule.json"
            lock_path = root / "schedule.lock"

            def pending_run(*_args, **kwargs):
                run_id = str(kwargs["run_id"])
                run_dir = root / "runs" / run_id
                run_dir.mkdir(parents=True)
                run_watch.write_json(
                    run_dir / "run.json",
                    {
                        "runId": run_id,
                        "exitCode": 2,
                        "snapshotHash": "c" * 64,
                        "snapshotStatus": "published",
                        "emailStatus": "pending",
                        "overallStatus": "delivery_pending",
                    },
                )
                return 2, run_id

            with (
                patch.object(scheduler, "AGENT_DIR", root),
                patch.object(scheduler, "STATE_FILE", state_path),
                patch.object(scheduler, "LOCK_FILE", lock_path),
                patch.object(scheduler, "PYTHON_BIN", Path(sys.executable)),
                patch.object(scheduler, "now_local", return_value=now),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=False),
                ),
                patch.object(
                    scheduler,
                    "load_notification_config",
                    return_value={"AUCTION_WATCH_APP_BASE_URL": "http://app.test"},
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "load_delivery_outbox",
                    return_value={"version": 1, "items": []},
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "pending_delivery_items",
                    return_value=[],
                ),
                patch.object(
                    scheduler,
                    "claim_manual_run",
                    return_value={"id": "run_request_pending"},
                ),
                patch.object(scheduler, "run_watch", side_effect=pending_run),
                patch.object(
                    scheduler,
                    "post_run_request",
                    return_value={"ok": True},
                ) as post,
            ):
                exit_code = scheduler.main()

            state = scheduler.load_state(state_path)

        self.assertEqual(exit_code, 0)
        self.assertEqual(expected_run_id, post.call_args.args[2]["runId"])
        self.assertEqual(post.call_args.args[1], "complete")
        self.assertFalse(post.call_args.args[2]["success"])
        self.assertEqual(
            post.call_args.args[2]["overallStatus"],
            "delivery_pending",
        )
        self.assertEqual(state["manualCompletions"], {})
        self.assertEqual(
            state.get("days", {}).get("2026-08-24", {}).get("fulfilled_slots", []),
            [],
        )

    def test_manual_delivery_pending_keeps_same_run_and_avoids_second_scan(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("America/Montevideo"))
        expected_run_id = "manual-run_request_pending"
        pending_delivery = {
            "runId": expected_run_id,
            "status": "pending",
            "nextAttemptAt": (now - timedelta(seconds=1)).isoformat(),
            "scheduleDate": "2026-08-24",
            "scheduleSlots": ["morning"],
            "manualRequestId": "run_request_pending",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "schedule.json"
            lock_path = root / "schedule.lock"
            with (
                patch.object(scheduler, "STATE_FILE", state_path),
                patch.object(scheduler, "LOCK_FILE", lock_path),
                patch.object(scheduler, "PYTHON_BIN", Path(sys.executable)),
                patch.object(scheduler, "now_local", side_effect=[now, now]),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=False),
                ),
                patch.object(scheduler, "load_notification_config", return_value={}),
                patch.object(
                    scheduler.run_watch_module,
                    "load_delivery_outbox",
                    return_value={"version": 1, "items": []},
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "pending_delivery_items",
                    side_effect=[[], [pending_delivery]],
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "delivery_outbox_item",
                    return_value=pending_delivery,
                ),
                patch.object(
                    scheduler,
                    "claim_manual_run",
                    return_value={"id": "run_request_pending"},
                ) as claim,
                patch.object(
                    scheduler,
                    "run_watch",
                    return_value=(2, expected_run_id),
                ) as scan,
                patch.object(scheduler, "deliver_run", return_value=2) as deliver,
                patch.object(
                    scheduler,
                    "complete_manual_run",
                    return_value=True,
                ) as complete,
            ):
                first_exit = scheduler.main()
                second_exit = scheduler.main()

            state = scheduler.load_state(state_path)

        self.assertEqual((first_exit, second_exit), (0, 0))
        scan.assert_called_once()
        self.assertEqual(
            scan.call_args.kwargs["manual_request_id"],
            "run_request_pending",
        )
        self.assertEqual(scan.call_args.kwargs["run_id"], expected_run_id)
        deliver.assert_called_once_with(expected_run_id)
        claim.assert_called_once()
        self.assertEqual(complete.call_count, 2)
        self.assertTrue(
            all(call.args[-1] == 2 for call in complete.call_args_list)
        )
        self.assertEqual(state["manualCompletions"], {})
        self.assertEqual(
            state.get("days", {}).get("2026-08-24", {}).get("fulfilled_slots", []),
            [],
        )


if __name__ == "__main__":
    unittest.main()
