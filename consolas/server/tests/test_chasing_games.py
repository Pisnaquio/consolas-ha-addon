from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.app import ApiError, create_chasing_game, init_db, list_chasing_games, run_chasing_game, set_chasing_game_enabled


class TestConfig:
    def __init__(self, root: Path) -> None:
        self.data_dir = root / "data"
        self.static_dir = root / "web"
        self.media_dir = self.data_dir / "media"
        self.auction_watch_dir = self.data_dir / "auction-watch"
        self.db_path = self.data_dir / "consolas.sqlite"
        self.max_body_size = 1024 * 1024
        self.ebay_client_id = ""
        self.ebay_client_secret = ""
        self.ebay_environment = "sandbox"


class ChasingGamesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)

    def test_seeded_iss_chase_is_separate_and_enabled(self) -> None:
        payload = list_chasing_games(self.config)
        self.assertEqual(payload["source"], "eBay Sandbox · datos de prueba")
        self.assertEqual(payload["environment"], "sandbox")
        self.assertEqual(payload["items"][0]["id"], "iss-deluxe-snes")
        self.assertTrue(payload["items"][0]["enabled"])
        self.assertEqual(payload["items"][0]["platform"], "SNES")

    def test_run_explains_when_official_ebay_credentials_are_missing(self) -> None:
        with self.assertRaisesRegex(ApiError, "credenciales de eBay Developers"):
            run_chasing_game(self.config, "iss-deluxe-snes")

    @patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries")
    def test_manual_run_persists_and_refreshes_ebay_results(self, mock_fetch) -> None:
        mock_fetch.return_value = [{
            "itemId": "v1|123456789012|0",
            "title": "International Superstar Soccer Deluxe SNES Tested",
            "itemWebUrl": "https://www.ebay.com/itm/123456789012",
            "price": {"value": "90.00", "currency": "USD"},
            "condition": "Pre-owned",
            "buyingOptions": ["FIXED_PRICE"],
            "itemLocation": {"country": "US"},
            "image": {"imageUrl": "https://i.ebayimg.com/example.jpg"},
            "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "8.00", "currency": "USD"}}],
        }]

        result = run_chasing_game(self.config, "iss-deluxe-snes")
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"], 1)
        item = list_chasing_games(self.config)["items"][0]
        self.assertEqual(item["results"][0]["priceLabel"], "USD 90.00")
        self.assertTrue(item["lastCheckedAt"])

    @patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[])
    def test_new_chase_can_be_paused(self, _mock_fetch) -> None:
        created = create_chasing_game(self.config, {"title": "Metal Gear Solid", "platform": "PS1"})
        self.assertTrue(created["ok"])
        paused = set_chasing_game_enabled(self.config, created["id"], False)
        self.assertFalse(paused["enabled"])
        item = next(item for item in list_chasing_games(self.config)["items"] if item["id"] == created["id"])
        self.assertFalse(item["enabled"])
