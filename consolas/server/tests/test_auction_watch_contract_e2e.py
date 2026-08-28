from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agents" / "auction-watch"
for import_root in (REPO_ROOT, AGENT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from server.app import (  # noqa: E402
    claim_auction_watch_run,
    canonical_auction_watch_snapshot_hash,
    complete_auction_watch_run,
    enqueue_auction_watch_run,
    init_db,
    latest_auction_watch_run_request,
    publish_auction_watch_snapshot,
    read_auction_watch_snapshot,
)
import scripts.run_watch as run_watch  # noqa: E402


class TestConfig:
    def __init__(self, root: Path) -> None:
        self.data_dir = root / "data"
        self.static_dir = root / "web"
        self.media_dir = self.data_dir / "media"
        self.auction_watch_dir = self.data_dir / "auction-watch"
        self.db_path = self.data_dir / "consolas.sqlite"
        self.max_body_size = 1024 * 1024
        self.auction_watch_stale_after_seconds = 36 * 60 * 60


class AuctionWatchContractE2ETests(unittest.TestCase):
    def test_runner_receipt_and_backend_get_share_one_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = TestConfig(root)
            config.static_dir.mkdir(parents=True)
            init_db(config)

            run_id = "e2e-contract-run"
            payload = {
                "runId": run_id,
                "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "status": "success",
                "counts": {"total_matches": 1, "detected_matches": 1},
                "featured": None,
                "matches": [
                    {
                        "source": "remotes",
                        "lotId": "e2e:1",
                        "title": "Nintendo Game Boy",
                    }
                ],
            }
            snapshot_path = root / "auction-watch.json"
            snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
            base_url = "http://ha.test:8788"

            class JsonResponse:
                def __init__(self, response_payload: object) -> None:
                    self.response_payload = response_payload

                def read(self) -> bytes:
                    return json.dumps(self.response_payload).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            def in_process_backend(request, timeout):
                self.assertEqual(timeout, 15)
                if request.full_url.endswith("/snapshot"):
                    request_payload = json.loads(request.data.decode("utf-8"))
                    return JsonResponse(
                        publish_auction_watch_snapshot(
                            config,
                            request_payload,
                            expected_hash=request.get_header("X-auction-watch-snapshot-hash"),
                        )
                    )
                self.assertEqual(request.full_url, f"{base_url}/api/auction-watch")
                return JsonResponse(read_auction_watch_snapshot(config))

            with patch.object(run_watch, "urlopen", side_effect=in_process_backend):
                publication = run_watch.publish_web_snapshot(
                    {
                        "AUCTION_WATCH_PUBLICATION_MODE": "ha-required",
                        "AUCTION_WATCH_APP_BASE_URL": base_url,
                    },
                    snapshot_path,
                )
            live = read_auction_watch_snapshot(config)

        self.assertEqual(publication.status, "published")
        self.assertTrue(publication.canonical_verified)
        self.assertEqual(publication.run_id, run_id)
        self.assertEqual(live["sync"]["status"], "current")
        self.assertEqual(live["sync"]["runId"], run_id)
        self.assertEqual(live["sync"]["snapshotHash"], publication.snapshot_hash)

    def test_manual_completion_then_automatic_publication_is_superseded_in_get(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = TestConfig(root)
            config.static_dir.mkdir(parents=True)
            init_db(config)

            enqueue_auction_watch_run(config)
            manual_request = claim_auction_watch_run(config)["request"]
            manual_run_id = "manual-e2e-superseded"
            manual_payload = {
                "runId": manual_run_id,
                "generatedAt": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
                "status": "success",
                "matches": [{"source": "remotes", "lotId": "manual:1", "title": "Manual"}],
            }
            manual_hash = canonical_auction_watch_snapshot_hash(manual_payload)
            publish_auction_watch_snapshot(config, manual_payload, expected_hash=manual_hash)
            complete_auction_watch_run(
                config,
                {
                    "id": manual_request["id"],
                    "success": True,
                    "runId": manual_run_id,
                    "snapshotHash": manual_hash,
                    "snapshotStatus": "published",
                    "emailStatus": "sent",
                    "overallStatus": "completed",
                },
            )

            current = latest_auction_watch_run_request(config)["request"]
            self.assertEqual(current["publicationState"], "current")

            automatic_payload = {
                "runId": "auto-e2e-after-manual",
                "generatedAt": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "status": "success",
                "matches": [{"source": "remotes", "lotId": "auto:1", "title": "Automatic"}],
            }
            publish_auction_watch_snapshot(
                config,
                automatic_payload,
                expected_hash=canonical_auction_watch_snapshot_hash(automatic_payload),
            )

            superseded = latest_auction_watch_run_request(config)["request"]
            self.assertEqual(superseded["publicationState"], "superseded")
            self.assertEqual(superseded["supersededByRunId"], automatic_payload["runId"])
            self.assertEqual(read_auction_watch_snapshot(config)["runId"], automatic_payload["runId"])

    def test_published_request_without_current_or_historical_receipt_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = TestConfig(root)
            config.static_dir.mkdir(parents=True)
            init_db(config)
            request = enqueue_auction_watch_run(config)["request"]
            request = claim_auction_watch_run(config)["request"]
            missing_run_id = "manual-e2e-missing"
            missing_hash = "a" * 64
            with sqlite3.connect(config.db_path) as connection:
                connection.execute(
                    """
                    UPDATE auction_watch_run_requests
                    SET status = 'completed', finished_at = ?, run_id = ?, snapshot_hash = ?,
                        snapshot_status = 'published', email_status = 'sent', overall_status = 'completed'
                    WHERE id = ?
                    """,
                    (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), missing_run_id, missing_hash, request["id"]),
                )

            result = latest_auction_watch_run_request(config)["request"]
            self.assertEqual(result["publicationState"], "missing")

    def test_generated_at_alone_cannot_mark_a_run_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = TestConfig(root)
            config.static_dir.mkdir(parents=True)
            init_db(config)
            enqueue_auction_watch_run(config)
            request = claim_auction_watch_run(config)["request"]
            manual_payload = {
                "runId": "manual-accepted-order",
                "generatedAt": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
                "status": "success",
                "matches": [],
            }
            manual_hash = canonical_auction_watch_snapshot_hash(manual_payload)
            publish_auction_watch_snapshot(config, manual_payload, expected_hash=manual_hash)
            complete_auction_watch_run(
                config,
                {
                    "id": request["id"],
                    "success": True,
                    "runId": manual_payload["runId"],
                    "snapshotHash": manual_hash,
                    "snapshotStatus": "published",
                    "emailStatus": "sent",
                    "overallStatus": "completed",
                },
            )
            automatic_payload = {
                "runId": "auto-generated-later-accepted-earlier",
                "generatedAt": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "status": "success",
                "matches": [],
            }
            publish_auction_watch_snapshot(
                config,
                automatic_payload,
                expected_hash=canonical_auction_watch_snapshot_hash(automatic_payload),
            )

            with sqlite3.connect(config.db_path) as connection:
                connection.execute(
                    "UPDATE auction_watch_publications SET accepted_at = ? WHERE run_id = ?",
                    ("2099-01-01T00:00:00Z", manual_payload["runId"]),
                )
            receipt_path = config.auction_watch_dir / "export" / "publication-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["acceptedAt"] = "2000-01-01T00:00:00Z"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            result = latest_auction_watch_run_request(config)["request"]
            self.assertEqual(result["publicationState"], "missing")


if __name__ == "__main__":
    unittest.main()
