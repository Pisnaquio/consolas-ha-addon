from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agents" / "auction-watch"
for import_root in (REPO_ROOT, AGENT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from server.app import (  # noqa: E402
    init_db,
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


if __name__ == "__main__":
    unittest.main()
