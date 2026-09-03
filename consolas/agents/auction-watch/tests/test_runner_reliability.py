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


class JsonResponse:
    def __init__(self, payload: object):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class RunnerReliabilityTests(unittest.TestCase):
    def test_overall_status_uses_only_canonical_contract_values(self) -> None:
        statuses = {
            run_watch.compute_overall_status("success", "skipped", "disabled"),
            run_watch.compute_overall_status("partial_failure", "published", "sent"),
            run_watch.compute_overall_status("failure", "published", "sent"),
            run_watch.compute_overall_status("success", "failed", "pending"),
        }

        self.assertEqual(
            statuses,
            {"completed", "degraded", "delivery_pending", "failed"},
        )

    def test_ha_publication_requires_matching_receipt_and_canonical_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "auction-watch.json"
            payload = {
                "runId": "run-42",
                "generatedAt": "2026-08-24T12:00:00-03:00",
                "matches": [],
            }
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
            expected_hash = run_watch.snapshot_payload_hash(payload)
            responses = [
                JsonResponse(
                    {
                        "ok": True,
                        "receipt": {
                            "runId": "run-42",
                            "snapshotHash": expected_hash,
                            "generatedAt": payload["generatedAt"],
                            "acceptedAt": "2026-08-24T12:00:01-03:00",
                            "matches": 0,
                        },
                    }
                ),
                JsonResponse(
                    {
                        "runId": "run-42",
                        "snapshotHash": expected_hash,
                        "matches": [],
                        "sync": {
                            "runId": "run-42",
                            "snapshotHash": expected_hash,
                            "status": "current",
                        },
                    }
                ),
            ]
            requests = []

            def fake_urlopen(request, timeout):
                requests.append((request, timeout))
                return responses.pop(0)

            with patch.object(run_watch, "urlopen", side_effect=fake_urlopen):
                result = run_watch.publish_web_snapshot(
                    {
                        "AUCTION_WATCH_PUBLICATION_MODE": "ha-required",
                        "AUCTION_WATCH_APP_BASE_URL": "http://ha.test:8788",
                    },
                    snapshot,
                )

        self.assertEqual(result.status, "published")
        self.assertTrue(result.canonical_verified)
        self.assertEqual(result.canonical_snapshot["matches"], [])
        self.assertEqual(result.snapshot_hash, expected_hash)
        post_request = requests[0][0]
        self.assertEqual(
            post_request.get_header("X-auction-watch-snapshot-hash"),
            expected_hash,
        )
        self.assertNotIn("snapshotHash", json.loads(post_request.data.decode("utf-8")))

    def test_ha_publication_rejects_receipt_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "auction-watch.json"
            payload = {
                "runId": "run-42",
                "generatedAt": "2026-08-24T12:00:00-03:00",
                "matches": [],
            }
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
            response = JsonResponse(
                {
                    "ok": True,
                    "receipt": {
                        "runId": "run-42",
                        "snapshotHash": "wrong",
                        "generatedAt": payload["generatedAt"],
                    },
                }
            )
            with patch.object(run_watch, "urlopen", return_value=response):
                result = run_watch.publish_web_snapshot(
                    {
                        "AUCTION_WATCH_PUBLICATION_MODE": "ha-required",
                        "AUCTION_WATCH_APP_BASE_URL": "http://ha.test:8788",
                    },
                    snapshot,
                )

        self.assertEqual(result.status, "failed")
        self.assertIn("receipt_snapshot_hash_mismatch", result.detail)

    def test_active_match_cache_survives_without_run_history(self) -> None:
        row = {
            "auction_id": "123",
            "lot_auction_id": "999",
            "auction_end_date": "2099-08-25T12:00:00-03:00",
            "score": "30",
        }
        state = run_watch.AgentState(
            processed_bavastro_auction_ids={123},
            active_bavastro_matches_by_group={"123": [row]},
        )

        reconciled = run_watch.reconcile_active_match_state(
            state,
            "bavastro",
            [123],
            [],
            [],
            inventory_authoritative=True,
            refresh_succeeded=False,
        )

        self.assertEqual([item["lot_auction_id"] for item in reconciled], ["999"])
        self.assertEqual(state.active_bavastro_matches_by_group["123"][0]["score"], "30")

    def test_state_v4_round_trip_keeps_active_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            state = run_watch.AgentState(
                processed_castells_remate_ids={456},
                active_castells_matches_by_group={
                    "456": [{"remate_id": "456", "lot_id": "lot-1"}]
                },
            )
            run_watch.save_state(path, state)
            loaded = run_watch.load_state(path)

        self.assertEqual(loaded.processed_castells_remate_ids, {456})
        self.assertEqual(
            loaded.active_castells_matches_by_group["456"][0]["lot_id"],
            "lot-1",
        )

    def test_delivery_retry_uses_existing_manifest_without_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "logs").mkdir()
            snapshot_payload = {
                "runId": "run-1",
                "generatedAt": "2026-08-24T12:00:00-03:00",
                "matches": [],
            }
            snapshot_path = run_dir / run_watch.RUN_SNAPSHOT_FILENAME
            snapshot_path.write_text(json.dumps(snapshot_payload), encoding="utf-8")
            run_watch.write_json(
                run_dir / "run.json",
                {
                    "run_id": "run-1",
                    "started_at": "2026-08-24T12:00:00-03:00",
                    "status": "success",
                    "scanStatus": "success",
                    "notifications": [],
                },
            )
            run_watch.write_json(
                run_dir / run_watch.DELIVERY_MANIFEST_FILENAME,
                {
                    "runId": "run-1",
                    "publicationMode": "local-only",
                    "snapshotPath": str(snapshot_path),
                    "snapshotHash": run_watch.snapshot_payload_hash(snapshot_payload),
                    "scheduleDate": "2026-08-24",
                    "scheduleSlots": ["morning"],
                    "email": {
                        "enabled": True,
                        "method": "smtp",
                        "recipients": ["owner@example.test"],
                        "messageId": "<auction-watch.run-1@consolas.local>",
                    },
                },
            )
            sent = run_watch.NotificationResult("email", True, True, True, "sent_via_smtp")
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", root / "outbox.json"),
                patch.object(run_watch, "send_prepared_email", return_value=sent) as send_mail,
                patch.object(run_watch, "export_web_snapshot") as export_snapshot,
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            notification_log = (run_dir / "logs" / "notifications.log").read_text(
                encoding="utf-8"
            )
            latest_notification_log = (
                root / "runs" / "latest" / "logs" / "notifications.log"
            ).read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(metadata["snapshotStatus"], "skipped")
        self.assertEqual(metadata["emailStatus"], "sent")
        self.assertEqual(metadata["overallStatus"], "completed")
        self.assertIn("sent_via_smtp", notification_log)
        self.assertEqual(latest_notification_log, notification_log)
        send_mail.assert_called_once()
        export_snapshot.assert_not_called()

    def test_ha_publication_failure_queues_delivery_and_blocks_email(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "runs" / "run-1"
            (run_dir / "logs").mkdir(parents=True)
            snapshot_payload = {
                "runId": "run-1",
                "generatedAt": "2026-08-24T12:00:00-03:00",
                "matches": [],
            }
            snapshot_path = run_dir / run_watch.RUN_SNAPSHOT_FILENAME
            snapshot_path.write_text(json.dumps(snapshot_payload), encoding="utf-8")
            run_watch.write_json(
                run_dir / "run.json",
                {
                    "run_id": "run-1",
                    "started_at": "2026-08-24T12:00:00-03:00",
                    "status": "success",
                    "scanStatus": "success",
                    "notifications": [],
                },
            )
            run_watch.write_json(
                run_dir / run_watch.DELIVERY_MANIFEST_FILENAME,
                {
                    "runId": "run-1",
                    "publicationMode": "ha-required",
                    "snapshotPath": str(snapshot_path),
                    "snapshotHash": run_watch.snapshot_payload_hash(snapshot_payload),
                    "scheduleDate": "2026-08-24",
                    "scheduleSlots": ["morning"],
                    "email": {
                        "enabled": True,
                        "method": "smtp",
                        "recipients": ["owner@example.test"],
                        "messageId": "<auction-watch.run-1@consolas.local>",
                    },
                },
            )
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", root / "outbox.json"),
                patch.object(run_watch, "send_prepared_email") as send_mail,
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "ha-required"},
                )
                outbox = json.loads((root / "outbox.json").read_text(encoding="utf-8"))

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 2)
        self.assertEqual(metadata["snapshotStatus"], "failed")
        self.assertEqual(metadata["emailStatus"], "pending")
        self.assertEqual(metadata["overallStatus"], "delivery_pending")
        self.assertIsNone(metadata["completedAt"])
        self.assertEqual(outbox["items"][0]["status"], "pending")
        send_mail.assert_not_called()

    def test_delivery_manifest_content_never_copies_smtp_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            summary_path = run_dir / "summary.md"
            summary_path.write_text("summary\n", encoding="utf-8")
            prepared = run_watch.prepare_email_delivery(
                {
                    "AUCTION_WATCH_EMAIL_MODE": "always",
                    "AUCTION_WATCH_EMAIL_METHOD": "smtp",
                    "AUCTION_WATCH_EMAIL_TO": "owner@example.test",
                    "AUCTION_WATCH_SMTP_USERNAME": "private-user",
                    "AUCTION_WATCH_SMTP_PASSWORD": "private-password",
                },
                "success",
                {"total_matches": 0},
                "run-1",
                summary_path,
                [],
                [],
                run_dir,
            )

        serialized = json.dumps(prepared)
        self.assertNotIn("private-user", serialized)
        self.assertNotIn("private-password", serialized)
        self.assertNotIn("SMTP", serialized)

    def test_email_is_rebuilt_from_the_ha_visible_match_subset(self) -> None:
        visible = run_watch.MatchView(
            "remotes", "Remotes", "lot-1", "group", "Nintendo", "Nintendo",
            "https://example.test/1", "https://example.test/group", "", 40,
            "nintendo", "", "", "$100", "hoy", "", "",
        )
        dismissed = run_watch.MatchView(
            "remotes", "Remotes", "lot-2", "group", "Switch HDMI", "Switch HDMI",
            "https://example.test/2", "https://example.test/group", "", 10,
            "switch", "", "", "$50", "hoy", "", "",
        )
        visible_hit = run_watch.WatchHit(
            "watch-1", "Nintendo", "remotes", "lot-1", "group", "1", "Remate",
            "Nintendo", "https://example.test/1", "https://example.test/group", "", "",
            "", "seguimiento", "nintendo", "$100",
        )
        dismissed_hit = run_watch.WatchHit(
            "watch-2", "Switch HDMI", "remotes", "lot-2", "group", "2", "Remate",
            "Switch HDMI", "https://example.test/2", "https://example.test/group", "", "",
            "", "seguimiento", "switch", "$50",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            summary = run_dir / "summary.md"
            summary.write_text("summary\n", encoding="utf-8")
            manifest = {
                "runId": "run-canonical-mail",
                "email": {"enabled": True},
                "emailSource": {
                    "status": "success",
                    "counts": {"total_matches": 2, "detected_matches": 2},
                    "summaryPath": str(summary),
                    "matchViews": [run_watch.asdict(visible), run_watch.asdict(dismissed)],
                    "watchHits": [run_watch.asdict(visible_hit), run_watch.asdict(dismissed_hit)],
                },
            }
            publication = run_watch.PublicationResult(
                "ha-required",
                "published",
                True,
                True,
                "ok",
                run_id="run-canonical-mail",
                snapshot_hash="a" * 64,
                canonical_verified=True,
                canonical_snapshot={
                    "matches": [{"source": "remotes", "lotId": "lot-1"}],
                    "counts": {"total_matches": 1, "detected_matches": 2, "dismissed_matches": 1},
                },
            )
            with patch.object(
                run_watch,
                "prepare_email_delivery",
                return_value={"enabled": True, "subject": "canonical"},
            ) as prepare:
                prepared = run_watch.prepare_canonical_email_delivery(
                    {"AUCTION_WATCH_EMAIL_MODE": "matches"},
                    manifest,
                    publication,
                    run_dir,
                )

        self.assertEqual(prepared["subject"], "canonical")
        call = prepare.call_args.args
        self.assertEqual(call[2]["total_matches"], 1)
        self.assertEqual(call[2]["dismissed_matches"], 1)
        self.assertEqual([item.lot_id for item in call[5]], ["lot-1"])
        self.assertEqual([item.lot_id for item in call[6]], ["lot-1"])

    def test_manual_run_emails_canonical_empty_result_after_dismissals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            summary = run_dir / "summary.md"
            summary.write_text("summary\n", encoding="utf-8")
            manifest = {
                "runId": "manual-run-emptied-by-dismissals",
                "manualRequestId": "run_request_1",
                "email": {"enabled": True},
                "emailSource": {
                    "status": "success",
                    "counts": {
                        "total_matches": 4,
                        "detected_matches": 4,
                        "dismissed_matches": 0,
                    },
                    "summaryPath": str(summary),
                    "matchViews": [],
                    "watchHits": [],
                },
            }
            publication = run_watch.PublicationResult(
                "ha-required",
                "published",
                True,
                True,
                "ok",
                run_id="manual-run-emptied-by-dismissals",
                snapshot_hash="b" * 64,
                canonical_verified=True,
                canonical_snapshot={
                    "matches": [],
                    "counts": {
                        "total_matches": 0,
                        "detected_matches": 4,
                        "dismissed_matches": 4,
                    },
                },
            )
            base_config = {
                "AUCTION_WATCH_EMAIL_MODE": "matches_or_failure",
                "AUCTION_WATCH_EMAIL_METHOD": "smtp",
                "AUCTION_WATCH_EMAIL_TO": "owner@example.test",
            }
            prepared = run_watch.prepare_canonical_email_delivery(
                base_config,
                manifest,
                publication,
                run_dir,
            )
            automatic = run_watch.prepare_canonical_email_delivery(
                base_config,
                {**manifest, "manualRequestId": ""},
                publication,
                run_dir,
            )

        self.assertTrue(prepared["enabled"])
        self.assertEqual(prepared["emailMode"], "always")
        self.assertEqual(prepared["recipients"], ["owner@example.test"])
        self.assertFalse(automatic["enabled"])
        self.assertEqual(automatic["emailMode"], "matches_or_failure")

    def test_invalid_smtp_port_is_a_structured_delivery_failure(self) -> None:
        result = run_watch.send_email_via_smtp(
            {
                "AUCTION_WATCH_SMTP_HOST": "smtp.example.test",
                "AUCTION_WATCH_SMTP_PORT": "not-a-number",
                "AUCTION_WATCH_EMAIL_FROM": "sender@example.test",
            },
            ["owner@example.test"],
            "subject",
            "body",
        )

        self.assertTrue(result.enabled)
        self.assertFalse(result.attempted)
        self.assertFalse(result.sent)
        self.assertEqual(result.detail, "invalid_smtp_port")

    def test_manual_completion_uses_the_backend_delivery_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            run_watch.write_json(
                run_dir / "run.json",
                {
                    "run_id": "run-1",
                    "snapshotHash": "abc123",
                    "snapshotStatus": "failed",
                    "emailStatus": "pending",
                    "overallStatus": "delivery_pending",
                },
            )
            with (
                patch.object(scheduler, "AGENT_DIR", root),
                patch.object(scheduler, "post_run_request", return_value={"ok": True}) as post,
            ):
                completed = scheduler.complete_manual_run({}, "request-7", "run-1", 2)

        self.assertTrue(completed)
        self.assertEqual(
            post.call_args.args[2],
            {
                "id": "request-7",
                "success": False,
                "detail": (
                    "run=run-1 exit=2 overall=delivery_pending "
                    "snapshot=failed email=pending"
                ),
                "runId": "run-1",
                "snapshotHash": "abc123",
                "snapshotStatus": "failed",
                "emailStatus": "pending",
                "overallStatus": "delivery_pending",
            },
        )

    def test_scheduler_prioritizes_due_delivery_over_new_scan(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("America/Montevideo"))
        delivery = {
            "runId": "run-1",
            "status": "pending",
            "nextAttemptAt": (now - timedelta(seconds=1)).isoformat(),
            "scheduleDate": "2026-08-24",
            "scheduleSlots": ["morning"],
        }
        completed = {**delivery, "status": "completed"}
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "schedule.json"
            lock_path = Path(temp_dir) / "schedule.lock"
            with (
                patch.object(scheduler, "STATE_FILE", state_path),
                patch.object(scheduler, "LOCK_FILE", lock_path),
                patch.object(scheduler, "now_local", return_value=now),
                patch.object(
                    scheduler,
                    "parse_args",
                    return_value=argparse.Namespace(mode="twice", dry_run=False),
                ),
                patch.object(scheduler, "load_notification_config", return_value={}),
                patch.object(
                    scheduler.run_watch_module,
                    "pending_delivery_items",
                    return_value=[delivery],
                ),
                patch.object(
                    scheduler.run_watch_module,
                    "delivery_outbox_item",
                    return_value=completed,
                ),
                patch.object(scheduler, "deliver_run", return_value=0) as deliver,
                patch.object(scheduler, "run_watch") as scan,
            ):
                exit_code = scheduler.main()

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        deliver.assert_called_once_with("run-1")
        scan.assert_not_called()
        self.assertEqual(
            state["days"]["2026-08-24"]["fulfilledByRunId"]["morning"],
            "run-1",
        )


if __name__ == "__main__":
    unittest.main()
