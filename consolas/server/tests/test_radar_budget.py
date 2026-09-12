from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from server.app import (
    ApiError,
    compute_radar_budget,
    compute_radar_lot_valuation,
    connect_db,
    create_radar_search,
    delete_radar_search,
    get_radar_preferences,
    init_db,
    record_radar_decision,
    record_radar_purchase,
    run_radar_search,
    update_radar_preferences,
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


class RadarBudgetTestCase(unittest.TestCase):
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


class PreferencesTests(RadarBudgetTestCase):
    def test_the_budget_starts_unconfigured(self) -> None:
        prefs = get_radar_preferences(self.config)
        self.assertIsNone(prefs["monthlyBudgetUsd"])

    def test_setting_and_reading_the_budget_back(self) -> None:
        update_radar_preferences(self.config, {"monthlyBudgetUsd": 250})
        self.assertEqual(get_radar_preferences(self.config)["monthlyBudgetUsd"], 250.0)

    def test_clearing_the_budget_goes_back_to_unconfigured(self) -> None:
        update_radar_preferences(self.config, {"monthlyBudgetUsd": 250})
        update_radar_preferences(self.config, {"monthlyBudgetUsd": None})
        self.assertIsNone(get_radar_preferences(self.config)["monthlyBudgetUsd"])

    def test_a_negative_budget_is_rejected(self) -> None:
        with self.assertRaises(ApiError) as raised:
            update_radar_preferences(self.config, {"monthlyBudgetUsd": -10})
        self.assertEqual(raised.exception.status, 400)


class BudgetComputationTests(RadarBudgetTestCase):
    def test_available_is_none_without_a_configured_budget(self) -> None:
        budget = compute_radar_budget(self.config)
        self.assertFalse(budget["configured"])
        self.assertIsNone(budget["available"])
        self.assertEqual(budget["spent"], 0)
        self.assertEqual(budget["reserved"], 0)

    def test_a_registered_purchase_counts_as_spent_this_month(self) -> None:
        update_radar_preferences(self.config, {"monthlyBudgetUsd": 200})
        [listing_id] = self.seed([ebay_summary(price="60.00")])

        record_radar_purchase(
            self.config,
            {"listingId": listing_id, "entityType": "console", "entityId": "ps2", "priceAmount": 60.0},
        )

        budget = compute_radar_budget(self.config)
        self.assertEqual(budget["spent"], 60.0)
        self.assertEqual(budget["spentCount"], 1)
        self.assertEqual(budget["available"], 140.0)

    def test_a_reserved_followed_listing_counts_as_reserved_not_spent(self) -> None:
        update_radar_preferences(self.config, {"monthlyBudgetUsd": 200})
        [listing_id] = self.seed([ebay_summary(price="60.00")])

        record_radar_decision(self.config, {"listingId": listing_id, "decision": "following", "reserved": True})

        budget = compute_radar_budget(self.config)
        self.assertEqual(budget["reserved"], 60.0)
        self.assertEqual(budget["spent"], 0)
        self.assertEqual(budget["available"], 140.0)

    def test_reserving_something_that_is_not_followed_is_rejected(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        with self.assertRaises(ApiError) as raised:
            record_radar_decision(self.config, {"listingId": listing_id, "decision": "dismissed", "reserved": True})
        self.assertEqual(raised.exception.status, 400)

    def test_purchasing_a_reserved_listing_stops_counting_it_as_reserved(self) -> None:
        update_radar_preferences(self.config, {"monthlyBudgetUsd": 200})
        [listing_id] = self.seed([ebay_summary(price="60.00")])
        record_radar_decision(self.config, {"listingId": listing_id, "decision": "following", "reserved": True})
        self.assertEqual(compute_radar_budget(self.config)["reserved"], 60.0)

        record_radar_purchase(
            self.config,
            {"listingId": listing_id, "entityType": "console", "entityId": "ps2", "priceAmount": 55.0},
        )

        budget = compute_radar_budget(self.config)
        self.assertEqual(budget["reserved"], 0, "ya se compró: no puede seguir contando como plan probable")
        self.assertEqual(budget["spent"], 55.0)

    def test_a_purchase_from_last_month_does_not_count_as_spent_this_month(self) -> None:
        update_radar_preferences(self.config, {"monthlyBudgetUsd": 200})
        [listing_id] = self.seed([ebay_summary(price="60.00")])
        last_month = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat().replace("+00:00", "Z")

        record_radar_purchase(
            self.config,
            {
                "listingId": listing_id, "entityType": "console", "entityId": "ps2",
                "priceAmount": 60.0, "purchasedAt": last_month,
            },
        )

        self.assertEqual(compute_radar_budget(self.config)["spent"], 0)

    def test_an_exceptional_purchase_can_still_go_negative_available(self) -> None:
        # El presupuesto avisa, no bloquea (PRD §10.6): una joya excepcional
        # puede superarlo y el número lo refleja, no lo esconde.
        update_radar_preferences(self.config, {"monthlyBudgetUsd": 50})
        [listing_id] = self.seed([ebay_summary(price="90.00")])

        record_radar_purchase(
            self.config,
            {"listingId": listing_id, "entityType": "console", "entityId": "ps2", "priceAmount": 90.0},
        )

        self.assertEqual(compute_radar_budget(self.config)["available"], -40.0)


class RecordPurchaseTests(RadarBudgetTestCase):
    def test_recording_a_purchase_marks_the_decision_as_purchased(self) -> None:
        [listing_id] = self.seed([ebay_summary(price="60.00")])

        result = record_radar_purchase(
            self.config,
            {"listingId": listing_id, "entityType": "console", "entityId": "ps2", "priceAmount": 55.0},
        )

        self.assertEqual(result["decision"]["decision"], "purchased")
        self.assertEqual(result["decision"]["priceAtDecision"], 55.0)
        self.assertEqual(result["purchase"]["entityType"], "console")
        self.assertEqual(result["purchase"]["entityId"], "ps2")

    def test_an_unknown_listing_is_rejected(self) -> None:
        with self.assertRaises(ApiError) as raised:
            record_radar_purchase(
                self.config,
                {"listingId": "ebay-us-noexiste", "entityType": "console", "entityId": "ps2", "priceAmount": 10.0},
            )
        self.assertEqual(raised.exception.status, 404)

    def test_an_invalid_entity_type_is_rejected(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        with self.assertRaises(ApiError) as raised:
            record_radar_purchase(
                self.config,
                {"listingId": listing_id, "entityType": "manual", "entityId": "x", "priceAmount": 10.0},
            )
        self.assertEqual(raised.exception.status, 400)

    def test_a_future_purchase_date_is_rejected(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        with self.assertRaises(ApiError) as raised:
            record_radar_purchase(
                self.config,
                {
                    "listingId": listing_id, "entityType": "console", "entityId": "ps2",
                    "priceAmount": 10.0, "purchasedAt": future,
                },
            )
        self.assertEqual(raised.exception.status, 400)

    def test_this_never_touches_collection_state(self) -> None:
        # "Registrar compra" en el server sólo deja evidencia para presupuesto
        # e historial; la escritura de colección la hace el frontend después,
        # por CollectionRepository — nunca acá.
        from server.app import read_state

        [listing_id] = self.seed([ebay_summary()])
        before = read_state(self.config)

        record_radar_purchase(
            self.config,
            {"listingId": listing_id, "entityType": "console", "entityId": "ps2", "priceAmount": 10.0},
        )

        self.assertEqual(read_state(self.config)["user"], before["user"])


class LotValuationEndpointTests(unittest.TestCase):
    def test_the_endpoint_matches_the_pure_function(self) -> None:
        result = compute_radar_lot_valuation(
            {
                "totalCost": 50.0,
                "pieces": [
                    {"name": "PS2 Slim", "comparableValue": 60.0},
                    {"name": "God of War", "comparableValue": 20.0},
                    {"name": "Deporte que nadie quiere", "comparableValue": 5.0, "wanted": False},
                ],
            }
        )
        self.assertEqual(result["conservativeValue"], 85.0)
        self.assertEqual(result["usefulValue"], 80.0)
        self.assertEqual(result["costPerUsefulPiece"], 25.0)

    def test_pieces_must_be_a_list(self) -> None:
        with self.assertRaises(ApiError) as raised:
            compute_radar_lot_valuation({"pieces": "not a list", "totalCost": 10})
        self.assertEqual(raised.exception.status, 400)

    def test_a_piece_without_a_name_is_rejected(self) -> None:
        with self.assertRaises(ApiError) as raised:
            compute_radar_lot_valuation({"pieces": [{"comparableValue": 10}], "totalCost": 10})
        self.assertEqual(raised.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
