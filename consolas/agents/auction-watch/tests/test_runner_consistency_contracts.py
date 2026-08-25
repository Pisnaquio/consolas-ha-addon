from __future__ import annotations

import json
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

import scripts.run_watch as run_watch  # noqa: E402


class RunnerConsistencyContractTests(unittest.TestCase):
    @staticmethod
    def write_delivery_run(root: Path, *, snapshot_exists: bool = True) -> tuple[Path, Path, dict]:
        run_dir = root / "runs" / "run-1"
        (run_dir / "logs").mkdir(parents=True)
        snapshot = {
            "runId": "run-1",
            "generatedAt": "2026-08-24T12:00:00-03:00",
            "scanStatus": "success",
            "matches": [],
        }
        snapshot_path = run_dir / run_watch.RUN_SNAPSHOT_FILENAME
        if snapshot_exists:
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        run_watch.write_json(
            run_dir / "run.json",
            {
                "run_id": "run-1",
                "started_at": "2026-08-24T12:00:00-03:00",
                "status": "success",
                "scanStatus": "success",
                "snapshotStatus": "failed",
                "notifications": [],
            },
        )
        run_watch.write_json(
            run_dir / run_watch.DELIVERY_MANIFEST_FILENAME,
            {
                "version": 1,
                "runId": "run-1",
                "runDir": str(run_dir),
                "publicationMode": "local-only",
                "snapshotPath": str(snapshot_path),
                "snapshotHash": run_watch.snapshot_payload_hash(snapshot),
                "scheduleDate": "2026-08-24",
                "scheduleSlots": ["morning"],
                "email": {
                    "version": 1,
                    "runId": "run-1",
                    "enabled": True,
                    "method": "smtp",
                    "recipients": ["owner@example.test"],
                    "subject": "Auction Watch",
                    "body": "body",
                    "htmlBody": "<p>body</p>",
                    "attachments": [],
                    "messageId": "<auction-watch.run-1@consolas.local>",
                },
            },
        )
        return run_dir, snapshot_path, snapshot

    def test_partial_refresh_merges_without_erasing_omitted_matches(self) -> None:
        old_one = {"auction_id": "123", "lot_auction_id": "1", "score": "10"}
        old_two = {"auction_id": "123", "lot_auction_id": "2", "score": "20"}
        refreshed_one = {"auction_id": "123", "lot_auction_id": "1", "score": "99"}
        state = run_watch.AgentState(
            active_bavastro_matches_by_group={"123": [old_one, old_two]}
        )

        partial_rows = run_watch.reconcile_active_match_state(
            state,
            "bavastro",
            [123],
            [refreshed_one],
            [123],
            inventory_authoritative=True,
            refresh_succeeded=True,
            refresh_complete=False,
        )

        self.assertEqual(
            {row["lot_auction_id"] for row in partial_rows},
            {"1", "2"},
        )
        self.assertEqual(
            next(row for row in partial_rows if row["lot_auction_id"] == "1")["score"],
            "99",
        )

        complete_rows = run_watch.reconcile_active_match_state(
            state,
            "bavastro",
            [123],
            [refreshed_one],
            [123],
            inventory_authoritative=True,
            refresh_succeeded=True,
            refresh_complete=True,
        )
        self.assertEqual([row["lot_auction_id"] for row in complete_rows], ["1"])

    def test_partial_registry_refresh_preserves_omitted_sources_and_lots(self) -> None:
        state = run_watch.AgentState(
            active_extra_matches_by_source={
                "prado": [
                    {"source_id": "prado", "lot_id": "p1", "score": "10"},
                    {"source_id": "prado", "lot_id": "p2", "score": "20"},
                ],
                "remotes": [
                    {"source_id": "remotes", "lot_id": "r1", "score": "30"}
                ],
            }
        )

        rows = run_watch.reconcile_extra_match_state(
            state,
            [{"source_id": "prado", "lot_id": "p1", "score": "99"}],
            [
                {"source_id": "prado", "status": "partial"},
                {"source_id": "remotes", "status": "failed"},
            ],
            status_payload_valid=True,
        )

        self.assertEqual(
            {(row["source_id"], row["lot_id"]) for row in rows},
            {("prado", "p1"), ("prado", "p2"), ("remotes", "r1")},
        )
        self.assertEqual(
            next(row for row in rows if row["lot_id"] == "p1")["score"],
            "99",
        )

    def test_complete_registry_refresh_replaces_inventory_and_state_round_trips(self) -> None:
        state = run_watch.AgentState(
            active_extra_matches_by_source={
                "prado": [
                    {"source_id": "prado", "lot_id": "old", "score": "10"}
                ],
                "removed-source": [
                    {"source_id": "removed-source", "lot_id": "stale", "score": "1"}
                ],
            }
        )
        rows = run_watch.reconcile_extra_match_state(
            state,
            [{"source_id": "prado", "lot_id": "new", "score": "50"}],
            [{"source_id": "prado", "status": "success"}],
            status_payload_valid=True,
        )
        self.assertEqual(
            [(row["source_id"], row["lot_id"]) for row in rows],
            [("prado", "new")],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            run_watch.save_state(state_path, state)
            restored = run_watch.load_state(state_path)

        self.assertEqual(restored.active_extra_matches_by_source, state.active_extra_matches_by_source)

    def test_scan_status_contract_normalizes_legacy_values(self) -> None:
        self.assertEqual(run_watch.canonical_scan_status("success"), "success")
        self.assertEqual(run_watch.canonical_scan_status("partial_failure"), "partial")
        self.assertEqual(run_watch.canonical_scan_status("partial"), "partial")
        self.assertEqual(run_watch.canonical_scan_status("failure"), "failed")
        self.assertEqual(run_watch.canonical_scan_status("failed"), "failed")
        self.assertEqual(
            run_watch.compute_overall_status("partial", "published", "sent"),
            "degraded",
        )

    def test_missing_immutable_snapshot_is_terminal_and_never_reexported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(
                root, snapshot_exists=False
            )
            outbox_path = root / "outbox.json"
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(run_watch, "export_web_snapshot") as export_snapshot,
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            outbox = json.loads(outbox_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(metadata["overallStatus"], "failed")
        self.assertIn("snapshot_manifest_file_missing", metadata["snapshot"]["detail"])
        self.assertEqual(outbox["items"][0]["status"], "failed")
        export_snapshot.assert_not_called()

    def test_mutated_immutable_snapshot_is_terminal_and_never_reexported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, snapshot_path, snapshot = self.write_delivery_run(root)
            snapshot["matches"] = [{"source": "remotes", "lotId": "unexpected"}]
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            outbox_path = root / "outbox.json"
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(run_watch, "export_web_snapshot") as export_snapshot,
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertIn("snapshot_manifest_hash_mismatch", metadata["snapshot"]["detail"])
        export_snapshot.assert_not_called()

    def test_normal_timeout_result_becomes_uncertain_without_automatic_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(root)
            outbox_path = root / "outbox.json"
            run_watch.record_delivery_outbox(
                "run-1",
                run_dir,
                status="pending",
                detail="delivery_not_attempted",
                path=outbox_path,
            )
            timeout_result = run_watch.NotificationResult(
                "email",
                enabled=True,
                attempted=True,
                sent=False,
                detail="timeout",
            )
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(
                    run_watch,
                    "send_prepared_email",
                    return_value=timeout_result,
                ) as send_mail,
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )
                pending = run_watch.pending_delivery_items(
                    path=outbox_path,
                    due_only=False,
                )

            manifest = json.loads(
                (run_dir / run_watch.DELIVERY_MANIFEST_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            outbox = json.loads(outbox_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(manifest["emailDelivery"]["status"], "uncertain")
        self.assertIn("explicit_retry_may_duplicate", manifest["emailDelivery"]["detail"])
        self.assertEqual(metadata["emailStatus"], "uncertain")
        self.assertEqual(metadata["overallStatus"], "failed")
        self.assertEqual(outbox["items"][0]["status"], "uncertain")
        self.assertEqual(pending, [])
        send_mail.assert_called_once()

    def test_only_definitive_pre_send_or_rejection_failures_are_retryable(self) -> None:
        ambiguous = run_watch.NotificationResult("email", True, True, False, "timeout")
        disconnected = run_watch.NotificationResult(
            "email", True, True, False, "smtp_delivery_uncertain:SMTPServerDisconnected"
        )
        pre_send = run_watch.NotificationResult(
            "email", True, True, False, "smtp_pre_send_failed:SMTPConnectError"
        )
        rejected = run_watch.NotificationResult(
            "email", True, True, False, "sendmail_rejected:exit=67"
        )

        self.assertTrue(run_watch.email_failure_is_ambiguous(ambiguous))
        self.assertTrue(run_watch.email_failure_is_ambiguous(disconnected))
        self.assertFalse(run_watch.email_failure_is_ambiguous(pre_send))
        self.assertFalse(run_watch.email_failure_is_ambiguous(rejected))

    def test_definitive_pre_send_failure_remains_in_retry_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(root)
            outbox_path = root / "outbox.json"
            run_watch.record_delivery_outbox(
                "run-1",
                run_dir,
                status="pending",
                detail="delivery_not_attempted",
                path=outbox_path,
            )
            pre_send_result = run_watch.NotificationResult(
                "email",
                enabled=True,
                attempted=False,
                sent=False,
                detail="missing_smtp_config",
            )
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(
                    run_watch,
                    "send_prepared_email",
                    return_value=pre_send_result,
                ),
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            outbox = json.loads(outbox_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 2)
        self.assertEqual(metadata["emailStatus"], "pending")
        self.assertEqual(metadata["overallStatus"], "delivery_pending")
        self.assertEqual(outbox["items"][0]["status"], "pending")

    def test_transport_timeouts_and_status_checks_are_tagged_ambiguous(self) -> None:
        with patch.object(run_watch.smtplib, "SMTP") as smtp:
            smtp.return_value.__enter__.return_value.send_message.side_effect = (
                run_watch.smtplib.SMTPServerDisconnected("connection lost")
            )
            smtp_result = run_watch.send_email_via_smtp(
                {
                    "AUCTION_WATCH_SMTP_HOST": "smtp.example.test",
                    "AUCTION_WATCH_SMTP_PORT": "587",
                    "AUCTION_WATCH_SMTP_STARTTLS": "false",
                    "AUCTION_WATCH_EMAIL_FROM": "owner@example.test",
                },
                ["owner@example.test"],
                "subject",
                "body",
            )

        with patch.object(
            run_watch.subprocess,
            "run",
            side_effect=run_watch.subprocess.TimeoutExpired("sendmail", 30),
        ):
            sendmail_result = run_watch.send_email_via_sendmail(
                {},
                ["owner@example.test"],
                "subject",
                "body",
            )

        process_result = type(
            "ProcessResult",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()
        with (
            patch.object(run_watch.subprocess, "run", return_value=process_result),
            patch.object(
                run_watch,
                "mailapp_message_status",
                return_value=(False, "timeout"),
            ),
        ):
            mailapp_result = run_watch.send_email_via_mailapp(
                ["owner@example.test"],
                "subject",
                "body",
            )

        self.assertIn("smtp_delivery_uncertain", smtp_result.detail)
        self.assertIn("sendmail_delivery_uncertain", sendmail_result.detail)
        self.assertIn("mailapp_delivery_uncertain", mailapp_result.detail)
        self.assertTrue(run_watch.email_failure_is_ambiguous(smtp_result))
        self.assertTrue(run_watch.email_failure_is_ambiguous(sendmail_result))
        self.assertTrue(run_watch.email_failure_is_ambiguous(mailapp_result))

    def test_interrupted_email_becomes_uncertain_and_requires_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(root)
            outbox_path = root / "outbox.json"
            run_watch.record_delivery_outbox(
                "run-1",
                run_dir,
                status="pending",
                detail="delivery_not_attempted",
                path=outbox_path,
            )

            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(
                    run_watch,
                    "send_prepared_email",
                    side_effect=RuntimeError("simulated process crash"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated process crash"):
                    run_watch.attempt_delivery_for_run(
                        run_dir,
                        {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                    )

            crashed_outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
            self.assertEqual(crashed_outbox["items"][0]["status"], "sending")
            crashed_outbox["items"][0]["sendingPid"] = 999_999_999
            run_watch.atomic_write_json(outbox_path, crashed_outbox)

            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
            ):
                self.assertEqual(
                    run_watch.pending_delivery_items(path=outbox_path, due_only=False),
                    [],
                )

            recovered_outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
            recovered_metadata = json.loads(
                (run_dir / "run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(recovered_outbox["items"][0]["status"], "uncertain")
            self.assertIn("may_duplicate", recovered_outbox["items"][0]["detail"])
            self.assertEqual(recovered_metadata["emailStatus"], "uncertain")

            sent = run_watch.NotificationResult("email", True, True, True, "sent_via_smtp")
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(run_watch, "send_prepared_email", return_value=sent) as send_mail,
            ):
                blocked_exit = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )
                self.assertEqual(blocked_exit, 1)
                send_mail.assert_not_called()

                forced_exit = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                    force_uncertain_email_retry=True,
                )

            final_outbox = json.loads(outbox_path.read_text(encoding="utf-8"))

        self.assertEqual(forced_exit, 0)
        self.assertEqual(final_outbox["items"][0]["status"], "completed")
        send_mail.assert_called_once()

    def test_terminal_outbox_repairs_stale_run_metadata_before_scheduler_ack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(root)
            manifest_path = run_dir / run_watch.DELIVERY_MANIFEST_FILENAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["publication"] = {
                "mode": "ha-required",
                "status": "published",
                "configured": True,
                "attempted": True,
                "detail": "receipt verified",
                "runId": "run-1",
                "snapshotHash": manifest["snapshotHash"],
                "generatedAt": "2026-08-24T12:00:00-03:00",
                "canonicalVerified": True,
            }
            manifest["emailDelivery"] = {"status": "sent", "detail": "sent_via_smtp"}
            run_watch.write_json(manifest_path, manifest)
            outbox_path = root / "outbox.json"
            run_watch.record_delivery_outbox(
                "run-1",
                run_dir,
                status="completed",
                detail="sent_via_smtp",
                path=outbox_path,
            )

            with (
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
            ):
                run_watch.recover_interrupted_delivery_outbox()
                first_metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
                run_watch.recover_interrupted_delivery_outbox()

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(metadata["snapshotStatus"], "published")
        self.assertEqual(metadata["emailStatus"], "sent")
        self.assertEqual(metadata["overallStatus"], "completed")
        self.assertEqual(metadata["delivery"]["status"], "completed")
        self.assertEqual(metadata["completedAt"], first_metadata["completedAt"])

    def test_newer_canonical_snapshot_makes_an_older_publish_retry_terminal(self) -> None:
        class JsonResponse:
            def read(self) -> bytes:
                return json.dumps(
                    {
                        "runId": "run-newer",
                        "sync": {
                            "runId": "run-newer",
                            "status": "stale",
                            "source": "server",
                            "acceptedAt": "2026-08-24T12:00:00Z",
                        },
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        publication = run_watch.PublicationResult(
            mode="ha-required",
            status="failed",
            configured=True,
            attempted=True,
            detail="HTTPError: HTTP Error 409: Conflict",
            run_id="run-old",
        )
        with patch.object(run_watch, "urlopen", return_value=JsonResponse()):
            superseded = run_watch.publication_failure_is_superseded(
                {"AUCTION_WATCH_APP_BASE_URL": "http://app.test"},
                publication,
                "run-old",
            )

        self.assertTrue(superseded)


if __name__ == "__main__":
    unittest.main()
