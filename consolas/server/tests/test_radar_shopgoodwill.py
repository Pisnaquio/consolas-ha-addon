from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.app import (
    ApiError,
    create_manual_radar_listing,
    create_radar_search,
    delete_radar_search,
    init_db,
    list_radar_feed,
    list_radar_listings,
    verify_radar_listing,
)


class TestConfig:
    def __init__(self, root: Path) -> None:
        self.data_dir = root / "data"
        self.static_dir = root / "web"
        self.media_dir = self.data_dir / "media"
        self.auction_watch_dir = self.data_dir / "auction-watch"
        self.db_path = self.data_dir / "consolas.sqlite"
        self.max_body_size = 1024 * 1024
        self.ebay_client_id = "client"
        self.ebay_client_secret = "secret"
        self.ebay_environment = "production"


def shopgoodwill_listing(**overrides: object) -> dict:
    payload = {
        "title": "Sony PlayStation 2 Slim tested with controller",
        "listingUrl": "https://shopgoodwill.com/item/123456",
        "priceAmount": 45.0,
        "shippingAmount": 12.0,
        "conditionLabel": "Used",
    }
    payload.update(overrides)
    return payload


class ShopGoodwillTestCase(unittest.TestCase):
    """Slice 10: Personal Shopper / alertas guardadas, nunca un crawler propio."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)
        delete_radar_search(self.config, "iss-deluxe-snes")
        self.search = create_radar_search(self.config, {"name": "PS2"})["search"]


class ManualListingTests(ShopGoodwillTestCase):
    def test_a_manual_listing_that_fits_the_search_is_added(self) -> None:
        result = create_manual_radar_listing(
            self.config, {"searchId": self.search["id"], **shopgoodwill_listing()}
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["requiresVerification"])

        item = list_radar_feed(self.config)["items"][0]
        self.assertTrue(item["requiresVerification"])
        self.assertIsNone(item["verifiedAt"])
        self.assertEqual(item["sourceId"], "shopgoodwill")

    def test_only_assisted_verification_sources_can_be_loaded_manually(self) -> None:
        with self.assertRaises(ApiError) as raised:
            create_manual_radar_listing(
                self.config, {"searchId": self.search["id"], "sourceId": "ebay-us", **shopgoodwill_listing()}
            )
        self.assertEqual(raised.exception.status, 400)

    def test_an_unknown_source_is_rejected(self) -> None:
        with self.assertRaises(ApiError) as raised:
            create_manual_radar_listing(
                self.config, {"searchId": self.search["id"], "sourceId": "not-a-source", **shopgoodwill_listing()}
            )
        self.assertEqual(raised.exception.status, 400)

    def test_a_listing_that_does_not_fit_the_search_is_rejected_with_why(self) -> None:
        excluded = create_radar_search(
            self.config,
            {"name": "PS2 sin repuestos", "criteria": {"excludeTerms": ["repair", "for parts"]}},
        )["search"]

        with self.assertRaises(ApiError) as raised:
            create_manual_radar_listing(
                self.config,
                {"searchId": excluded["id"], **shopgoodwill_listing(title="PS2 for parts, repair only")},
            )
        self.assertEqual(raised.exception.status, 422)
        self.assertTrue(raised.exception.details.get("blockers"))

    def test_an_invalid_url_is_rejected(self) -> None:
        with self.assertRaises(ApiError) as raised:
            create_manual_radar_listing(
                self.config, {"searchId": self.search["id"], **shopgoodwill_listing(listingUrl="not a url")}
            )
        self.assertEqual(raised.exception.status, 400)

    def test_an_unknown_search_is_rejected(self) -> None:
        with self.assertRaises(ApiError) as raised:
            create_manual_radar_listing(self.config, {"searchId": "radar-noexiste", **shopgoodwill_listing()})
        self.assertEqual(raised.exception.status, 404)

    def test_loading_the_same_url_twice_updates_instead_of_duplicating(self) -> None:
        create_manual_radar_listing(self.config, {"searchId": self.search["id"], **shopgoodwill_listing()})
        create_manual_radar_listing(
            self.config, {"searchId": self.search["id"], **shopgoodwill_listing(priceAmount=40.0)}
        )

        payload = list_radar_listings(self.config)
        shopgoodwill_items = [item for item in payload["items"] if item["sourceId"] == "shopgoodwill"]
        self.assertEqual(len(shopgoodwill_items), 1)
        self.assertEqual(shopgoodwill_items[0]["priceAmount"], 40.0)

    def test_it_goes_through_the_same_scoring_as_an_automatic_match(self) -> None:
        create_manual_radar_listing(self.config, {"searchId": self.search["id"], **shopgoodwill_listing()})

        item = list_radar_feed(self.config)["items"][0]
        self.assertIn("cost", item["valuation"])
        self.assertTrue(item["matches"][0]["reasons"])


class VerifyListingTests(ShopGoodwillTestCase):
    def test_verifying_clears_the_flag_and_stamps_the_date(self) -> None:
        create_manual_radar_listing(self.config, {"searchId": self.search["id"], **shopgoodwill_listing()})
        listing_id = list_radar_feed(self.config)["items"][0]["id"]

        result = verify_radar_listing(self.config, listing_id)
        self.assertTrue(result["verifiedAt"])

        item = list_radar_feed(self.config)["items"][0]
        self.assertFalse(item["requiresVerification"])
        self.assertEqual(item["verifiedAt"], result["verifiedAt"])

    def test_verifying_an_unknown_listing_is_rejected(self) -> None:
        with self.assertRaises(ApiError) as raised:
            verify_radar_listing(self.config, "ebay-us-noexiste")
        self.assertEqual(raised.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
