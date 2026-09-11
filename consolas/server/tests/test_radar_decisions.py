from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from server.app import (
    ApiError,
    clear_radar_decision,
    connect_db,
    create_radar_search,
    delete_radar_search,
    init_db,
    list_radar_decisions,
    list_radar_feed,
    read_state,
    record_radar_decision,
    run_radar_search,
)


def ebay_summary(item_id: str = "v1|1|0", price: str = "50.00", **overrides: object) -> dict:
    summary = {
        "itemId": item_id,
        "title": "PlayStation 2 Slim tested with OEM controller",
        "itemWebUrl": f"https://www.ebay.com/itm/{item_id}",
        "price": {"value": price, "currency": "USD"},
        "condition": "Pre-owned",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"country": "US"},
        "image": {},
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "0.00", "currency": "USD"}}],
    }
    summary.update(overrides)
    return summary


def in_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


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


class DecisionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)
        delete_radar_search(self.config, "iss-deluxe-snes")
        self.search = create_radar_search(self.config, {"name": "PS2"})["search"]

    def seed(self, summaries: list[dict]) -> list[str]:
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=summaries):
            run_radar_search(self.config, self.search["id"])
        with connect_db(self.config) as conn:
            return [str(row["id"]) for row in conn.execute("SELECT id FROM radar_listings ORDER BY external_id")]

    def feed_ids(self, **kwargs: object) -> list[str]:
        return [item["id"] for item in list_radar_feed(self.config, **kwargs)["items"]]


class DecisionRecordingTests(DecisionTestCase):
    def test_following_keeps_the_listing_in_the_feed(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        record_radar_decision(self.config, {"listingId": listing_id, "decision": "following"})

        payload = list_radar_feed(self.config)
        self.assertIn(listing_id, [item["id"] for item in payload["items"]])
        self.assertEqual(payload["counts"]["following"], 1)
        self.assertEqual(payload["items"][0]["decision"]["decision"], "following")

    def test_dismissing_removes_it_and_remembers_why(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        record_radar_decision(
            self.config, {"listingId": listing_id, "decision": "dismissed", "reason": "caro", "note": "a ese precio no"}
        )

        payload = list_radar_feed(self.config)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["counts"]["dismissed"], 1)
        history = list_radar_decisions(self.config)["items"][0]
        self.assertEqual(history["reason"], "caro")
        self.assertEqual(history["note"], "a ese precio no")

    def test_a_decision_can_be_changed_and_cleared(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        record_radar_decision(self.config, {"listingId": listing_id, "decision": "dismissed"})
        self.assertEqual(self.feed_ids(), [])

        record_radar_decision(self.config, {"listingId": listing_id, "decision": "following"})
        self.assertEqual(self.feed_ids(), [listing_id])

        clear_radar_decision(self.config, listing_id)
        self.assertEqual(list_radar_decisions(self.config)["count"], 0)
        self.assertEqual(self.feed_ids(), [listing_id])

    def test_the_price_at_the_moment_of_deciding_is_kept(self) -> None:
        [listing_id] = self.seed([ebay_summary(price="50.00")])
        record_radar_decision(self.config, {"listingId": listing_id, "decision": "following"})
        self.assertEqual(list_radar_decisions(self.config)["items"][0]["priceAtDecision"], 50.0)

    def test_an_unknown_listing_is_rejected(self) -> None:
        with self.assertRaises(ApiError) as raised:
            record_radar_decision(self.config, {"listingId": "ebay-us-noexiste", "decision": "following"})
        self.assertEqual(raised.exception.status, 404)

    def test_an_invalid_decision_or_reason_is_rejected(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        for payload in (
            {"listingId": listing_id, "decision": "quizas"},
            {"listingId": listing_id, "decision": "dismissed", "reason": "porque si"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ApiError) as raised:
                    record_radar_decision(self.config, payload)
                self.assertEqual(raised.exception.status, 400)


class SnoozeTests(DecisionTestCase):
    def test_a_snoozed_listing_leaves_the_feed(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        record_radar_decision(
            self.config, {"listingId": listing_id, "decision": "snoozed", "snoozeUntil": in_days(30)}
        )
        payload = list_radar_feed(self.config)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["counts"]["snoozed"], 1)

    def test_snoozing_requires_a_future_date(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        for snooze in ("", in_days(-1), "no es una fecha"):
            with self.subTest(snooze=snooze):
                with self.assertRaises(ApiError) as raised:
                    record_radar_decision(
                        self.config, {"listingId": listing_id, "decision": "snoozed", "snoozeUntil": snooze}
                    )
                self.assertEqual(raised.exception.status, 400)

    def test_an_expired_snooze_brings_the_listing_back(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        record_radar_decision(
            self.config, {"listingId": listing_id, "decision": "snoozed", "snoozeUntil": in_days(1)}
        )
        self.assertEqual(self.feed_ids(), [])

        with connect_db(self.config) as conn:
            conn.execute(
                "UPDATE radar_decisions SET snooze_until = ? WHERE listing_id = ?",
                (in_days(-1), listing_id),
            )
        self.assertEqual(self.feed_ids(), [listing_id])


class PriceDropTests(DecisionTestCase):
    """Un «lo quiero pero está caro» sólo sirve si el radar avisa cuando baja."""

    def test_a_price_drop_is_detected_and_measured(self) -> None:
        [listing_id] = self.seed([ebay_summary(price="100.00")])
        self.seed([ebay_summary(price="85.00")])

        item = next(entry for entry in list_radar_feed(self.config)["items"] if entry["id"] == listing_id)
        drop = item["priceDrop"]
        self.assertEqual(drop["previous"], 100.0)
        self.assertEqual(drop["current"], 85.0)
        self.assertEqual(drop["amount"], 15.0)
        self.assertTrue(drop["material"], "15% supera el umbral del 8%")

    def test_a_small_drop_is_reported_but_not_material(self) -> None:
        [listing_id] = self.seed([ebay_summary(price="100.00")])
        self.seed([ebay_summary(price="97.00")])

        item = next(entry for entry in list_radar_feed(self.config)["items"] if entry["id"] == listing_id)
        self.assertFalse(item["priceDrop"]["material"])

    def test_a_price_increase_is_not_a_drop(self) -> None:
        self.seed([ebay_summary(price="100.00")])
        self.seed([ebay_summary(price="120.00")])
        self.assertIsNone(list_radar_feed(self.config)["items"][0]["priceDrop"])

    def test_an_unchanged_price_does_not_erase_an_earlier_drop(self) -> None:
        self.seed([ebay_summary(price="100.00")])
        self.seed([ebay_summary(price="80.00")])
        self.seed([ebay_summary(price="80.00")])  # una corrida que ve lo mismo

        drop = list_radar_feed(self.config)["items"][0]["priceDrop"]
        self.assertIsNotNone(drop, "la baja sigue siendo la novedad aunque el precio se haya quedado quieto")
        self.assertEqual(drop["previous"], 100.0)

    def test_a_material_drop_wakes_a_snoozed_listing(self) -> None:
        [listing_id] = self.seed([ebay_summary(price="100.00")])
        record_radar_decision(
            self.config, {"listingId": listing_id, "decision": "snoozed", "snoozeUntil": in_days(30)}
        )
        self.assertEqual(self.feed_ids(), [])

        self.seed([ebay_summary(price="70.00")])

        self.assertEqual(self.feed_ids(), [listing_id], "bajó 30%: es exactamente el cambio que esperaba")

    def test_a_small_drop_does_not_wake_a_snoozed_listing(self) -> None:
        [listing_id] = self.seed([ebay_summary(price="100.00")])
        record_radar_decision(
            self.config, {"listingId": listing_id, "decision": "snoozed", "snoozeUntil": in_days(30)}
        )
        self.seed([ebay_summary(price="97.00")])
        self.assertEqual(self.feed_ids(), [])

    def test_a_dismissed_listing_stays_dismissed_even_if_it_drops(self) -> None:
        [listing_id] = self.seed([ebay_summary(price="100.00")])
        record_radar_decision(self.config, {"listingId": listing_id, "decision": "dismissed", "reason": "region"})
        self.seed([ebay_summary(price="40.00")])
        self.assertEqual(self.feed_ids(), [], "descartar es una decisión, no una espera")


class FeedTests(DecisionTestCase):
    def test_the_feed_is_capped_at_ten(self) -> None:
        self.seed([ebay_summary(item_id=f"v1|{i}|0", price=str(20 + i)) for i in range(18)])
        payload = list_radar_feed(self.config)
        self.assertEqual(len(payload["items"]), 10)
        self.assertEqual(payload["counts"]["feed"], 10)

    def test_a_material_drop_leads_the_feed(self) -> None:
        summaries = [ebay_summary(item_id=f"v1|{i}|0", price=str(20 + i)) for i in range(6)]
        self.seed(summaries)
        # La última baja fuerte; sin la baja iría al final por precio.
        summaries[-1] = ebay_summary(item_id="v1|5|0", price="10.00")
        self.seed(summaries)

        first = list_radar_feed(self.config)["items"][0]
        self.assertTrue(first["priceDrop"]["material"])

    def test_every_item_explains_why_it_is_there(self) -> None:
        self.seed([ebay_summary()])
        item = list_radar_feed(self.config)["items"][0]
        self.assertTrue(item["matches"])
        self.assertTrue(item["matches"][0]["searchName"])
        self.assertTrue(item["matches"][0]["reasons"])
        self.assertIn("cost", item["valuation"])

    def test_purchased_listings_leave_the_feed_without_touching_the_collection(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        before = read_state(self.config)

        record_radar_decision(self.config, {"listingId": listing_id, "decision": "purchased"})

        self.assertEqual(self.feed_ids(), [])
        self.assertEqual(read_state(self.config)["user"], before["user"])


if __name__ == "__main__":
    unittest.main()
