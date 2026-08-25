from __future__ import annotations

import json
import io
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from server.app import (
    ApiError,
    build_health_payload,
    canonical_auction_watch_snapshot_hash,
    dismiss_auction_watch_lot,
    claim_auction_watch_run,
    complete_auction_watch_run,
    enqueue_auction_watch_run,
    follow_auction_watch_lot,
    heartbeat_auction_watch_run,
    init_db,
    list_auction_watch_dismissals,
    list_auction_watch_following,
    latest_auction_watch_run_request,
    publish_auction_watch_snapshot,
    reconcile_auction_watch_dismissals,
    read_auction_watch_snapshot,
    read_json_body,
    require_auction_watch_write_request,
    restore_auction_watch_lot,
    unfollow_auction_watch_lot,
)


class TestConfig:
    def __init__(self, root: Path) -> None:
        self.data_dir = root / "data"
        self.static_dir = root / "web"
        self.media_dir = self.data_dir / "media"
        self.auction_watch_dir = self.data_dir / "auction-watch"
        self.db_path = self.data_dir / "consolas.sqlite"
        self.max_body_size = 1024 * 1024
        self.auction_watch_stale_after_seconds = 36 * 60 * 60


@contextmanager
def sqlite_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class AuctionWatchDismissalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        (self.config.static_dir / "data").mkdir()
        init_db(self.config)

    def write_snapshot(self) -> None:
        payload = {
            "generatedAt": "2026-08-05T12:00:00-03:00",
            "status": "success",
            "counts": {"total_matches": 2, "detected_matches": 2},
            "featured": {
                "source": "remotes",
                "lotId": "g:1",
                "title": "Soundic",
            },
            "matches": [
                {"source": "remotes", "lotId": "g:1", "title": "Soundic"},
                {"source": "prado", "lotId": "2", "title": "Radofin"},
            ],
        }
        (self.config.static_dir / "data" / "auction-watch.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_dismiss_is_idempotent_and_restore_is_reversible(self) -> None:
        first = dismiss_auction_watch_lot(
            self.config,
            {
                "sourceId": "REMOTES",
                "lotId": "g:1",
                "groupId": "g",
                "title": "Soundic",
                "lotUrl": "https://example.test/g?lote=1",
                "imageUrl": "https://images.example.test/soundic.jpg",
            },
        )
        second = dismiss_auction_watch_lot(
            self.config,
            {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic actualizado"},
        )

        self.assertTrue(first["ok"])
        self.assertEqual(second["item"]["title"], "Soundic actualizado")
        self.assertEqual(second["item"]["imageUrl"], "https://images.example.test/soundic.jpg")
        self.assertEqual(len(list_auction_watch_dismissals(self.config)["items"]), 1)

        restored = restore_auction_watch_lot(self.config, "remotes", "g:1")
        self.assertTrue(restored["removed"])
        self.assertEqual(list_auction_watch_dismissals(self.config)["items"], [])

    def test_snapshot_hides_dismissed_match_and_featured(self) -> None:
        self.write_snapshot()
        dismiss_auction_watch_lot(
            self.config,
            {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic"},
        )

        snapshot = read_auction_watch_snapshot(self.config)

        self.assertIsNone(snapshot["featured"])
        self.assertEqual([item["title"] for item in snapshot["matches"]], ["Radofin"])
        self.assertEqual(snapshot["dismissalsApplied"], 1)
        self.assertEqual(snapshot["counts"]["total_matches"], 1)
        self.assertEqual(snapshot["counts"]["dismissed_matches"], 1)

    def test_published_snapshot_becomes_the_live_snapshot(self) -> None:
        payload = {
            "generatedAt": "2026-08-06T12:00:00-03:00",
            "runId": "manual-20260806-120000",
            "status": "success",
            "counts": {"total_matches": 1, "detected_matches": 1},
            "featured": None,
            "matches": [{"source": "bavastro", "lotId": "2162554", "title": "Family"}],
        }

        result = publish_auction_watch_snapshot(self.config, payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["matches"], 1)
        self.assertEqual(read_auction_watch_snapshot(self.config)["matches"], payload["matches"])

    def test_snapshot_receipt_and_get_share_the_raw_snapshot_identity(self) -> None:
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {
            "generatedAt": generated_at,
            "runId": "manual-receipt-1",
            "status": "success",
            "counts": {"total_matches": 1, "detected_matches": 1},
            "featured": None,
            "matches": [{"source": "remotes", "lotId": "g:1", "title": "Soundic"}],
        }
        expected_hash = canonical_auction_watch_snapshot_hash(payload)

        published = publish_auction_watch_snapshot(
            self.config,
            payload,
            expected_hash=expected_hash,
        )
        live = read_auction_watch_snapshot(self.config)

        self.assertEqual(published["receipt"]["runId"], payload["runId"])
        self.assertEqual(published["receipt"]["snapshotHash"], expected_hash)
        self.assertEqual(live["snapshotHash"], expected_hash)
        self.assertEqual(live["sync"]["runId"], payload["runId"])
        self.assertEqual(live["sync"]["snapshotHash"], expected_hash)
        self.assertEqual(live["sync"]["status"], "current")
        self.assertEqual(live["sync"]["source"], "server")

        dismiss_auction_watch_lot(self.config, {"sourceId": "remotes", "lotId": "g:1"})
        filtered = read_auction_watch_snapshot(self.config)
        self.assertEqual(filtered["matches"], [])
        self.assertEqual(filtered["snapshotHash"], expected_hash)

    def test_snapshot_hash_mismatch_is_rejected_without_publishing(self) -> None:
        payload = {
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "runId": "manual-mismatch-1",
            "status": "success",
            "matches": [],
        }

        with self.assertRaises(ApiError) as mismatch:
            publish_auction_watch_snapshot(self.config, payload, expected_hash="0" * 64)

        self.assertEqual(mismatch.exception.status, 409)
        self.assertFalse(
            (self.config.auction_watch_dir / "export" / "auction-watch.json").exists()
        )

    def test_snapshot_publication_is_monotonic_and_idempotent(self) -> None:
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {
            "generatedAt": generated_at,
            "runId": "manual-monotonic-1",
            "status": "success",
            "matches": [],
        }
        snapshot_hash = canonical_auction_watch_snapshot_hash(payload)
        first = publish_auction_watch_snapshot(self.config, payload, expected_hash=snapshot_hash)
        retry = publish_auction_watch_snapshot(self.config, payload, expected_hash=snapshot_hash)

        self.assertTrue(retry["idempotent"])
        self.assertEqual(retry["receipt"]["acceptedAt"], first["receipt"]["acceptedAt"])

        mutated = {**payload, "status": "partial"}
        with self.assertRaises(ApiError) as changed_run:
            publish_auction_watch_snapshot(
                self.config,
                mutated,
                expected_hash=canonical_auction_watch_snapshot_hash(mutated),
            )
        self.assertEqual(changed_run.exception.status, 409)

        older = {
            **payload,
            "runId": "manual-monotonic-older",
            "generatedAt": (
                datetime.fromisoformat(generated_at.replace("Z", "+00:00")) - timedelta(minutes=1)
            ).isoformat().replace("+00:00", "Z"),
        }
        with self.assertRaises(ApiError) as stale:
            publish_auction_watch_snapshot(
                self.config,
                older,
                expected_hash=canonical_auction_watch_snapshot_hash(older),
            )
        self.assertEqual(stale.exception.status, 409)

        conflicting = {**payload, "runId": "manual-monotonic-2"}
        with self.assertRaises(ApiError) as same_time:
            publish_auction_watch_snapshot(
                self.config,
                conflicting,
                expected_hash=canonical_auction_watch_snapshot_hash(conflicting),
            )
        self.assertEqual(same_time.exception.status, 409)

    def test_idempotent_snapshot_retry_reconciles_lifecycle_again(self) -> None:
        payload = {
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "runId": "manual-lifecycle-retry-1",
            "status": "success",
            "matches": [],
            "publicationLifecycle": {
                "version": 1,
                "activeKeys": [],
                "sourceHealth": {
                    "remotes": {"status": "success", "inventoryAuthoritative": True}
                },
            },
        }
        snapshot_hash = canonical_auction_watch_snapshot_hash(payload)
        publish_auction_watch_snapshot(self.config, payload, expected_hash=snapshot_hash)

        with patch("server.app.reconcile_auction_watch_dismissals") as reconcile:
            reconcile.return_value = {
                "applied": True,
                "expired": 0,
                "tracking": 0,
                "protected": 0,
            }
            retry = publish_auction_watch_snapshot(
                self.config,
                payload,
                expected_hash=snapshot_hash,
            )

        self.assertTrue(retry["idempotent"])
        reconcile.assert_called_once()
        self.assertTrue(retry["dismissalCleanup"]["applied"])

    def test_idempotent_snapshot_retry_repairs_a_misaligned_receipt(self) -> None:
        payload = {
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "runId": "manual-receipt-repair-1",
            "status": "success",
            "matches": [],
        }
        snapshot_hash = canonical_auction_watch_snapshot_hash(payload)
        publish_auction_watch_snapshot(self.config, payload, expected_hash=snapshot_hash)
        receipt_path = self.config.auction_watch_dir / "export" / "publication-receipt.json"
        receipt_path.write_text(
            json.dumps(
                {
                    "runId": "another-run",
                    "snapshotHash": snapshot_hash,
                    "generatedAt": payload["generatedAt"],
                    "acceptedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            ),
            encoding="utf-8",
        )

        repaired = publish_auction_watch_snapshot(
            self.config,
            payload,
            expected_hash=snapshot_hash,
        )
        live = read_auction_watch_snapshot(self.config)

        self.assertFalse(repaired.get("idempotent", False))
        self.assertEqual(repaired["receipt"]["runId"], payload["runId"])
        self.assertEqual(live["sync"]["status"], "current")

    def test_snapshot_freshness_and_receipt_integrity_fail_closed(self) -> None:
        self.config.auction_watch_stale_after_seconds = 60
        payload = {
            "generatedAt": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "runId": "manual-stale-1",
            "status": "success",
            "matches": [],
        }
        publish_auction_watch_snapshot(
            self.config,
            payload,
            expected_hash=canonical_auction_watch_snapshot_hash(payload),
        )
        self.assertEqual(read_auction_watch_snapshot(self.config)["sync"]["status"], "stale")

        receipt_path = self.config.auction_watch_dir / "export" / "publication-receipt.json"
        receipt_path.write_text('{"runId":"wrong"}', encoding="utf-8")
        self.assertEqual(read_auction_watch_snapshot(self.config)["sync"]["status"], "unavailable")

    def test_health_is_ready_even_without_an_auction_watch_snapshot(self) -> None:
        health = build_health_payload(self.config)

        self.assertTrue(health["ready"])
        self.assertTrue(health["checks"]["database"])
        self.assertEqual(health["auctionWatch"]["status"], "unavailable")

    def test_dismissal_expires_after_two_days_of_confirmed_absence(self) -> None:
        dismiss_auction_watch_lot(self.config, {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic"})
        lifecycle = (
            set(),
            {"remotes": {"status": "success", "inventoryAuthoritative": True}},
        )
        first_seen_missing = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)

        first = reconcile_auction_watch_dismissals(
            self.config, lifecycle, observed_at=first_seen_missing
        )
        self.assertEqual(first["tracking"], 1)
        self.assertEqual(len(list_auction_watch_dismissals(self.config)["items"]), 1)

        before_grace = reconcile_auction_watch_dismissals(
            self.config, lifecycle, observed_at=first_seen_missing + timedelta(hours=47)
        )
        self.assertEqual(before_grace["expired"], 0)

        expired = reconcile_auction_watch_dismissals(
            self.config, lifecycle, observed_at=first_seen_missing + timedelta(hours=48, seconds=1)
        )
        self.assertEqual(expired["expired"], 1)
        self.assertEqual(list_auction_watch_dismissals(self.config)["items"], [])

    def test_legacy_success_does_not_start_dismissal_expiry(self) -> None:
        dismiss_auction_watch_lot(
            self.config,
            {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic"},
        )

        result = reconcile_auction_watch_dismissals(
            self.config,
            (set(), {"remotes": "success"}),
            observed_at=datetime(2026, 8, 9, 12, tzinfo=timezone.utc),
        )

        self.assertEqual(result["tracking"], 0)
        with sqlite_connection(self.config.db_path) as conn:
            missing_since = conn.execute(
                "SELECT missing_since FROM auction_watch_dismissals WHERE source_id = 'remotes' AND lot_id = 'g:1'"
            ).fetchone()[0]
        self.assertIsNone(missing_since)

    def test_snapshot_backfills_image_for_an_active_legacy_dismissal(self) -> None:
        dismiss_auction_watch_lot(self.config, {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic"})
        reconcile_auction_watch_dismissals(
            self.config,
            ({("remotes", "g:1")}, {"remotes": "success"}),
            active_image_urls={("remotes", "g:1"): "https://images.example.test/soundic.jpg"},
        )
        self.assertEqual(
            list_auction_watch_dismissals(self.config)["items"][0]["imageUrl"],
            "https://images.example.test/soundic.jpg",
        )

    def test_partial_source_resets_missing_timer_without_removing_dismissal(self) -> None:
        dismiss_auction_watch_lot(self.config, {"sourceId": "prado", "lotId": "272662", "title": "Radofin"})
        started = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
        reconcile_auction_watch_dismissals(
            self.config,
            (set(), {"prado": {"status": "success", "inventoryAuthoritative": True}}),
            observed_at=started,
        )

        protected = reconcile_auction_watch_dismissals(
            self.config,
            (set(), {"prado": {"status": "failed", "inventoryAuthoritative": False}}),
            observed_at=started + timedelta(days=3),
        )
        self.assertEqual(protected["expired"], 0)
        self.assertEqual(protected["protected"], 1)

        conn = sqlite3.connect(self.config.db_path)
        try:
            missing_since = conn.execute(
                "SELECT missing_since FROM auction_watch_dismissals WHERE source_id = 'prado' AND lot_id = '272662'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertIsNone(missing_since)

    def test_dismiss_removes_active_follow_up(self) -> None:
        follow_auction_watch_lot(self.config, {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic"})
        dismiss_auction_watch_lot(self.config, {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic"})
        self.assertEqual(list_auction_watch_following(self.config)["items"], [])

    def test_follow_restores_a_dismissed_lot_to_a_consistent_state(self) -> None:
        dismiss_auction_watch_lot(
            self.config,
            {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic"},
        )

        follow_auction_watch_lot(
            self.config,
            {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic"},
        )

        self.assertEqual(list_auction_watch_dismissals(self.config)["items"], [])
        self.assertEqual(len(list_auction_watch_following(self.config)["items"]), 1)

    def test_init_db_migrates_legacy_run_request_rows_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = TestConfig(Path(temp_dir))
            config.data_dir.mkdir(parents=True)
            with sqlite_connection(config.db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE auction_watch_run_requests (
                      id TEXT PRIMARY KEY,
                      status TEXT NOT NULL,
                      requested_at TEXT NOT NULL,
                      started_at TEXT,
                      finished_at TEXT,
                      detail TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO auction_watch_run_requests (id, status, requested_at, detail)
                    VALUES ('run_legacy', 'pending', '2026-08-24T12:00:00Z', 'legacy')
                    """
                )

            init_db(config)

            with sqlite_connection(config.db_path) as conn:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(auction_watch_run_requests)").fetchall()
                }
                row = conn.execute(
                    """
                    SELECT id, detail, run_id, snapshot_hash, snapshot_status,
                           email_status, overall_status
                    FROM auction_watch_run_requests WHERE id = 'run_legacy'
                    """
                ).fetchone()
            self.assertTrue(
                {
                    "run_id",
                    "snapshot_hash",
                    "snapshot_status",
                    "email_status",
                    "overall_status",
                }.issubset(columns)
            )
            self.assertEqual(row, ("run_legacy", "legacy", "", "", "", "", ""))

    def test_rejects_non_web_urls_and_simple_cross_site_posts(self) -> None:
        with self.assertRaises(ApiError) as invalid_url:
            dismiss_auction_watch_lot(
                self.config,
                {"sourceId": "remotes", "lotId": "1", "lotUrl": "javascript:alert(1)"},
            )
        self.assertEqual(invalid_url.exception.status, 400)

        class Request:
            headers = {"Content-Type": "text/plain"}

        with self.assertRaises(ApiError) as invalid_request:
            require_auction_watch_write_request(Request())
        self.assertEqual(invalid_request.exception.status, 415)

        class ValidRequest:
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "X-Consolas-Auction-Watch": "1",
            }

        require_auction_watch_write_request(ValidRequest())

    def test_chunked_ingress_body_is_read_like_a_regular_json_request(self) -> None:
        payload = b'{"sourceId":"remotes","lotId":"7591:81A"}'

        class ChunkedRequest:
            headers = {"Transfer-Encoding": "chunked"}
            rfile = io.BytesIO(
                f"{len(payload):X}\r\n".encode("ascii") + payload + b"\r\n0\r\n\r\n"
            )

        self.assertEqual(
            read_json_body(ChunkedRequest(), self.config),
            {"sourceId": "remotes", "lotId": "7591:81A"},
        )

    def test_follow_is_idempotent_and_reversible(self) -> None:
        first = follow_auction_watch_lot(
            self.config,
            {"sourceId": "remotes", "lotId": "g:1", "title": "Soundic", "lotUrl": "https://example.test/g?lote=1"},
        )
        second = follow_auction_watch_lot(
            self.config,
            {"sourceId": "REMOTES", "lotId": "g:1", "title": "Soundic actualizado"},
        )

        self.assertTrue(first["ok"])
        self.assertEqual(second["item"]["title"], "Soundic actualizado")
        self.assertEqual(len(list_auction_watch_following(self.config)["items"]), 1)

        unfollowed = unfollow_auction_watch_lot(self.config, "remotes", "g:1")
        self.assertTrue(unfollowed["removed"])
        self.assertEqual(list_auction_watch_following(self.config)["items"], [])

    def test_manual_run_request_lifecycle_and_deduplication(self) -> None:
        first = enqueue_auction_watch_run(self.config)
        duplicate = enqueue_auction_watch_run(self.config)
        self.assertTrue(first["queued"])
        self.assertFalse(duplicate["queued"])
        self.assertEqual(first["request"]["id"], duplicate["request"]["id"])

        claimed = claim_auction_watch_run(self.config)["request"]
        self.assertEqual(claimed["status"], "running")
        completed = complete_auction_watch_run(
            self.config,
            {"id": claimed["id"], "success": True, "detail": "mail sent"},
        )["request"]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(latest_auction_watch_run_request(self.config)["request"]["detail"], "mail sent")

    def test_manual_run_persists_structured_result_and_allows_delivery_retry(self) -> None:
        request = enqueue_auction_watch_run(self.config)["request"]
        claimed = claim_auction_watch_run(self.config)["request"]
        self.assertEqual(claimed["id"], request["id"])
        snapshot = {
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "runId": "manual-delivery-1",
            "status": "success",
            "matches": [],
        }
        snapshot_hash = canonical_auction_watch_snapshot_hash(snapshot)
        publish_auction_watch_snapshot(
            self.config,
            snapshot,
            expected_hash=snapshot_hash,
        )

        pending_delivery = complete_auction_watch_run(
            self.config,
            {
                "id": claimed["id"],
                "success": False,
                "detail": "delivery queued",
                "runId": "manual-delivery-1",
                "snapshotHash": snapshot_hash,
                "snapshotStatus": "published",
                "emailStatus": "failed",
                "overallStatus": "delivery_pending",
            },
        )["request"]
        self.assertEqual(pending_delivery["status"], "delivery_pending")
        self.assertEqual(pending_delivery["runId"], "manual-delivery-1")
        self.assertEqual(pending_delivery["snapshotHash"], snapshot_hash)
        self.assertEqual(pending_delivery["overallStatus"], "delivery_pending")
        duplicate = enqueue_auction_watch_run(self.config)
        self.assertFalse(duplicate["queued"])
        self.assertEqual(duplicate["request"]["id"], claimed["id"])

        delivered = complete_auction_watch_run(
            self.config,
            {
                "id": claimed["id"],
                "success": True,
                "detail": "mail sent",
                "runId": "manual-delivery-1",
                "snapshotHash": snapshot_hash,
                "snapshotStatus": "published",
                "emailStatus": "sent",
                "overallStatus": "completed",
            },
        )
        self.assertTrue(delivered["deliveryTransition"])
        self.assertEqual(delivered["request"]["status"], "completed")
        self.assertEqual(delivered["request"]["emailStatus"], "sent")

        newer_snapshot = {
            "generatedAt": (
                datetime.now(timezone.utc) + timedelta(seconds=1)
            ).isoformat().replace("+00:00", "Z"),
            "runId": "scheduled-after-manual-1",
            "status": "success",
            "matches": [],
        }
        publish_auction_watch_snapshot(
            self.config,
            newer_snapshot,
            expected_hash=canonical_auction_watch_snapshot_hash(newer_snapshot),
        )

        idempotent = complete_auction_watch_run(
            self.config,
            {
                "id": claimed["id"],
                "success": True,
                "detail": "mail sent",
                "runId": "manual-delivery-1",
                "snapshotHash": snapshot_hash,
                "snapshotStatus": "published",
                "emailStatus": "sent",
                "overallStatus": "completed",
            },
        )
        self.assertTrue(idempotent["idempotent"])

    def test_manual_run_heartbeat_renews_the_running_lease(self) -> None:
        request = enqueue_auction_watch_run(self.config)["request"]
        claimed = claim_auction_watch_run(self.config)["request"]
        old_started = (
            datetime.now(timezone.utc) - timedelta(minutes=31)
        ).isoformat().replace("+00:00", "Z")
        with sqlite_connection(self.config.db_path) as conn:
            conn.execute(
                "UPDATE auction_watch_run_requests SET started_at = ?, heartbeat_at = ? WHERE id = ?",
                (old_started, old_started, request["id"]),
            )

        heartbeat = heartbeat_auction_watch_run(self.config, {"id": claimed["id"]})
        latest = latest_auction_watch_run_request(self.config)["request"]

        self.assertTrue(heartbeat["ok"])
        self.assertEqual(latest["status"], "running")
        self.assertEqual(latest["startedAt"], old_started)
        self.assertNotEqual(latest["heartbeatAt"], old_started)

    def test_manual_run_cannot_claim_an_unreceipted_snapshot_as_published(self) -> None:
        enqueue_auction_watch_run(self.config)
        claimed = claim_auction_watch_run(self.config)["request"]
        self.assertIsNotNone(claimed)

        with self.assertRaises(ApiError) as conflict:
            complete_auction_watch_run(
                self.config,
                {
                    "id": claimed["id"],
                    "success": False,
                    "detail": "delivery queued",
                    "runId": "manual-unreceipted-1",
                    "snapshotHash": "a" * 64,
                    "snapshotStatus": "published",
                    "emailStatus": "pending",
                    "overallStatus": "delivery_pending",
                },
            )

        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(latest_auction_watch_run_request(self.config)["request"]["status"], "running")

    def test_manual_run_can_wait_for_publication_then_validates_the_transition(self) -> None:
        enqueue_auction_watch_run(self.config)
        claimed = claim_auction_watch_run(self.config)["request"]
        snapshot = {
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "runId": "manual-publication-pending-1",
            "status": "success",
            "matches": [],
        }
        snapshot_hash = canonical_auction_watch_snapshot_hash(snapshot)
        pending = complete_auction_watch_run(
            self.config,
            {
                "id": claimed["id"],
                "success": False,
                "detail": "publication pending",
                "runId": snapshot["runId"],
                "snapshotHash": snapshot_hash,
                "snapshotStatus": "failed",
                "emailStatus": "pending",
                "overallStatus": "delivery_pending",
            },
        )["request"]
        self.assertEqual(pending["status"], "delivery_pending")

        final_payload = {
            "id": claimed["id"],
            "success": True,
            "detail": "publication and mail completed",
            "runId": snapshot["runId"],
            "snapshotHash": snapshot_hash,
            "snapshotStatus": "published",
            "emailStatus": "sent",
            "overallStatus": "completed",
        }
        with self.assertRaises(ApiError) as unreceipted:
            complete_auction_watch_run(self.config, final_payload)
        self.assertEqual(unreceipted.exception.status, 409)

        publish_auction_watch_snapshot(
            self.config,
            snapshot,
            expected_hash=snapshot_hash,
        )
        progress = complete_auction_watch_run(
            self.config,
            {
                **final_payload,
                "success": False,
                "detail": "publication ready; email pending",
                "emailStatus": "pending",
                "overallStatus": "delivery_pending",
            },
        )
        self.assertTrue(progress["deliveryProgress"])
        self.assertEqual(progress["request"]["snapshotStatus"], "published")
        self.assertEqual(progress["request"]["status"], "delivery_pending")

        delivered = complete_auction_watch_run(self.config, final_payload)
        self.assertTrue(delivered["deliveryTransition"])
        self.assertEqual(delivered["request"]["status"], "completed")

    def test_late_manual_completion_uses_the_historical_publication_receipt(self) -> None:
        enqueue_auction_watch_run(self.config)
        claimed = claim_auction_watch_run(self.config)["request"]
        first_snapshot = {
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "runId": "manual-history-a",
            "status": "success",
            "matches": [],
        }
        first_hash = canonical_auction_watch_snapshot_hash(first_snapshot)
        complete_auction_watch_run(
            self.config,
            {
                "id": claimed["id"],
                "success": False,
                "detail": "delivery pending",
                "runId": first_snapshot["runId"],
                "snapshotHash": first_hash,
                "snapshotStatus": "failed",
                "emailStatus": "pending",
                "overallStatus": "delivery_pending",
            },
        )
        publish_auction_watch_snapshot(self.config, first_snapshot, expected_hash=first_hash)

        second_snapshot = {
            "generatedAt": (
                datetime.now(timezone.utc) + timedelta(seconds=2)
            ).isoformat().replace("+00:00", "Z"),
            "runId": "scheduled-history-b",
            "status": "success",
            "matches": [],
        }
        publish_auction_watch_snapshot(
            self.config,
            second_snapshot,
            expected_hash=canonical_auction_watch_snapshot_hash(second_snapshot),
        )

        completed = complete_auction_watch_run(
            self.config,
            {
                "id": claimed["id"],
                "success": True,
                "detail": "late delivery acknowledged",
                "runId": first_snapshot["runId"],
                "snapshotHash": first_hash,
                "snapshotStatus": "published",
                "emailStatus": "sent",
                "overallStatus": "completed",
            },
        )
        self.assertTrue(completed["deliveryTransition"])
        self.assertEqual(completed["request"]["runId"], first_snapshot["runId"])

    def test_manual_run_completion_uses_compare_and_set(self) -> None:
        pending = enqueue_auction_watch_run(self.config)["request"]
        payload = {
            "id": pending["id"],
            "success": False,
            "detail": "should not complete pending",
            "runId": "manual-cas-1",
            "snapshotHash": "b" * 64,
            "snapshotStatus": "failed",
            "emailStatus": "failed",
            "overallStatus": "failed",
        }
        with self.assertRaises(ApiError) as conflict:
            complete_auction_watch_run(self.config, payload)
        self.assertEqual(conflict.exception.status, 409)

        claimed = claim_auction_watch_run(self.config)["request"]
        self.assertEqual(claimed["id"], pending["id"])
        already_running = claim_auction_watch_run(self.config)
        self.assertIsNone(already_running["request"])
        self.assertEqual(already_running["running"]["id"], pending["id"])

    def test_stale_pending_request_expires_and_no_longer_blocks_retry(self) -> None:
        requested_at = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat().replace("+00:00", "Z")
        with sqlite_connection(self.config.db_path) as conn:
            conn.execute(
                "INSERT INTO auction_watch_run_requests (id, status, requested_at) VALUES (?, 'pending', ?)",
                ("run_stale_pending", requested_at),
            )

        stale = latest_auction_watch_run_request(self.config)["request"]
        self.assertEqual(stale["status"], "failed")
        self.assertIn("buscador no estaba disponible", stale["detail"])

        replacement = enqueue_auction_watch_run(self.config)
        self.assertTrue(replacement["queued"])
        self.assertEqual(replacement["request"]["status"], "pending")
        self.assertNotEqual(replacement["request"]["id"], stale["id"])

    def test_stale_running_request_still_expires_as_interrupted(self) -> None:
        requested_at = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat().replace("+00:00", "Z")
        with sqlite_connection(self.config.db_path) as conn:
            conn.execute(
                """
                INSERT INTO auction_watch_run_requests (id, status, requested_at, started_at)
                VALUES (?, 'running', ?, ?)
                """,
                ("run_stale_running", requested_at, requested_at),
            )

        stale = latest_auction_watch_run_request(self.config)["request"]
        self.assertEqual(stale["status"], "failed")
        self.assertIn("interrumpida", stale["detail"])

    def test_claim_reconciles_stale_running_before_claiming_fresh_pending(self) -> None:
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat().replace("+00:00", "Z")
        fresh_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with sqlite_connection(self.config.db_path) as conn:
            conn.execute(
                """
                INSERT INTO auction_watch_run_requests (id, status, requested_at, started_at)
                VALUES (?, 'running', ?, ?)
                """,
                ("run_stale_running", stale_at, stale_at),
            )
            conn.execute(
                "INSERT INTO auction_watch_run_requests (id, status, requested_at) VALUES (?, 'pending', ?)",
                ("run_fresh_pending", fresh_at),
            )

        claimed = claim_auction_watch_run(self.config)["request"]
        self.assertEqual(claimed["id"], "run_fresh_pending")
        self.assertEqual(claimed["status"], "running")

        with sqlite_connection(self.config.db_path) as conn:
            stale_status = conn.execute(
                "SELECT status FROM auction_watch_run_requests WHERE id = 'run_stale_running'"
            ).fetchone()[0]
        self.assertEqual(stale_status, "failed")


if __name__ == "__main__":
    unittest.main()
