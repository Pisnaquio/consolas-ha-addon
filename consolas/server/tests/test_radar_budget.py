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

    def test_a_game_purchase_records_which_console_it_belongs_to(self) -> None:
        # El mismo juego existe en varias plataformas: sin la consola, el
        # historial no diría cuál se compró y nadie podría aplicarlo después.
        [listing_id] = self.seed([ebay_summary(price="20.00")])

        result = record_radar_purchase(
            self.config,
            {
                "listingId": listing_id, "entityType": "game", "entityId": "god-of-war",
                "entityConsoleId": "ps2", "priceAmount": 18.0,
            },
        )

        self.assertEqual(result["purchase"]["entityType"], "game")
        self.assertEqual(result["purchase"]["entityId"], "god-of-war")
        self.assertEqual(result["purchase"]["entityConsoleId"], "ps2")

    def test_a_game_purchase_without_its_console_is_rejected(self) -> None:
        [listing_id] = self.seed([ebay_summary()])
        with self.assertRaises(ApiError) as raised:
            record_radar_purchase(
                self.config,
                {"listingId": listing_id, "entityType": "game", "entityId": "god-of-war", "priceAmount": 18.0},
            )
        self.assertEqual(raised.exception.status, 400)

    def test_a_console_purchase_rejects_a_console_id_it_does_not_need(self) -> None:
        # Una consola ya es la entidad. Aceptar el campo igual dejaría dos
        # fuentes para el mismo dato, y nada garantiza que coincidan.
        [listing_id] = self.seed([ebay_summary()])
        with self.assertRaises(ApiError) as raised:
            record_radar_purchase(
                self.config,
                {
                    "listingId": listing_id, "entityType": "console", "entityId": "ps2",
                    "entityConsoleId": "ps3", "priceAmount": 55.0,
                },
            )
        self.assertEqual(raised.exception.status, 400)

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


class ShipmentFranchiseTests(RadarBudgetTestCase):
    """La franquicia se mide sobre la mercadería que espera en la casilla.

    Lo comprado no viaja de inmediato: se acumula y se reenvía junto. Mientras
    la mercadería de ese reenvío quede bajo el límite, no paga impuestos.
    """

    def comprar(self, listing_id: str, precio: float) -> None:
        record_radar_purchase(
            self.config,
            {"listingId": listing_id, "entityType": "console", "entityId": "ps2", "priceAmount": precio},
        )

    def test_an_empty_mailbox_offers_the_whole_franchise(self) -> None:
        from server.app import DUTY_FREE_MERCHANDISE_USD, compute_radar_shipment

        envio = compute_radar_shipment(self.config)
        self.assertEqual(envio["merchandise"], 0)
        self.assertEqual(envio["headroom"], DUTY_FREE_MERCHANDISE_USD)
        self.assertFalse(envio["overLimit"])

    def test_purchases_pile_up_and_eat_the_headroom(self) -> None:
        from server.app import compute_radar_shipment

        ids = self.seed([ebay_summary("v1|1|0"), ebay_summary("v1|2|0")])
        self.comprar(ids[0], 120.0)
        self.comprar(ids[1], 45.0)

        envio = compute_radar_shipment(self.config)
        self.assertEqual(envio["merchandise"], 165.0)
        self.assertEqual(envio["headroom"], 35.0)
        self.assertEqual(envio["count"], 2)
        self.assertFalse(envio["overLimit"])

    def test_going_over_the_limit_is_reported_not_blocked(self) -> None:
        # La app no puede impedir una compra que ya se hizo: la declara.
        from server.app import compute_radar_shipment

        ids = self.seed([ebay_summary("v1|1|0"), ebay_summary("v1|2|0")])
        self.comprar(ids[0], 180.0)
        self.comprar(ids[1], 60.0)

        envio = compute_radar_shipment(self.config)
        self.assertTrue(envio["overLimit"])
        self.assertLess(envio["headroom"], 0)

    def test_only_merchandise_counts_never_the_shipping(self) -> None:
        # El envío del vendedor y el courier quedan afuera: la franquicia se
        # mide sobre lo que valen las cosas.
        from server.app import compute_radar_shipment

        [listing_id] = self.seed([ebay_summary("v1|1|0", price="50.00")])
        self.comprar(listing_id, 50.0)
        self.assertEqual(compute_radar_shipment(self.config)["merchandise"], 50.0)

    def test_closing_a_shipment_empties_the_mailbox(self) -> None:
        from server.app import close_radar_shipment, compute_radar_shipment

        ids = self.seed([ebay_summary("v1|1|0"), ebay_summary("v1|2|0")])
        self.comprar(ids[0], 120.0)
        self.comprar(ids[1], 45.0)

        cerrado = close_radar_shipment(self.config)
        self.assertEqual(cerrado["merchandise"], 165.0)
        self.assertEqual(cerrado["count"], 2)

        envio = compute_radar_shipment(self.config)
        self.assertEqual(envio["merchandise"], 0)
        self.assertEqual(envio["count"], 0)

    def test_what_already_shipped_never_comes_back_to_the_pile(self) -> None:
        from server.app import close_radar_shipment, compute_radar_shipment

        ids = self.seed([ebay_summary("v1|1|0"), ebay_summary("v1|2|0")])
        self.comprar(ids[0], 120.0)
        close_radar_shipment(self.config)
        self.comprar(ids[1], 45.0)

        envio = compute_radar_shipment(self.config)
        self.assertEqual(envio["merchandise"], 45.0, "la pila nueva arranca sola")
        self.assertEqual(envio["count"], 1)

    def test_closing_an_empty_mailbox_is_refused(self) -> None:
        from server.app import close_radar_shipment

        with self.assertRaises(ApiError) as raised:
            close_radar_shipment(self.config)
        self.assertEqual(raised.exception.status, 409)

    def test_closing_a_shipment_does_not_touch_the_monthly_budget(self) -> None:
        # Son dos cosas distintas: el presupuesto mide el mes, la franquicia
        # mide el paquete. Reenviar no devuelve plata.
        from server.app import close_radar_shipment

        [listing_id] = self.seed([ebay_summary("v1|1|0")])
        self.comprar(listing_id, 120.0)
        antes = compute_radar_budget(self.config)["spent"]
        close_radar_shipment(self.config)
        self.assertEqual(compute_radar_budget(self.config)["spent"], antes)

    def test_registering_a_purchase_says_how_the_mailbox_looks_after_it(self) -> None:
        [listing_id] = self.seed([ebay_summary("v1|1|0")])
        out = record_radar_purchase(
            self.config,
            {"listingId": listing_id, "entityType": "console", "entityId": "ps2", "priceAmount": 150.0},
        )
        self.assertEqual(out["shipment"]["merchandise"], 150.0)
        self.assertEqual(out["shipment"]["headroom"], 50.0)


class UndoPurchaseTests(RadarBudgetTestCase):
    """Registrar una compra tiene que poder deshacerse.

    Escribe en tres lados —evidencia, presupuesto y colección— y basta un click
    para dispararla. Sin vuelta atrás, un error queda grabado para siempre.
    """

    def comprar(self, precio: float = 99.0) -> str:
        [listing_id] = self.seed([ebay_summary(price=str(precio))])
        out = record_radar_purchase(
            self.config,
            {"listingId": listing_id, "entityType": "console", "entityId": "ps3", "priceAmount": precio},
        )
        return out["purchase"]["id"]

    def test_deleting_a_purchase_takes_it_out_of_the_budget(self) -> None:
        from server.app import delete_radar_purchase

        purchase_id = self.comprar()
        self.assertEqual(compute_radar_budget(self.config)["spent"], 99.0)

        out = delete_radar_purchase(self.config, purchase_id)
        self.assertEqual(out["deleted"]["entityId"], "ps3")
        self.assertEqual(out["budget"]["spent"], 0)
        self.assertEqual(compute_radar_budget(self.config)["spentCount"], 0)

    def test_deleting_a_purchase_empties_the_mailbox_too(self) -> None:
        from server.app import compute_radar_shipment, delete_radar_purchase

        purchase_id = self.comprar()
        self.assertEqual(compute_radar_shipment(self.config)["merchandise"], 99.0)

        delete_radar_purchase(self.config, purchase_id)
        self.assertEqual(compute_radar_shipment(self.config)["count"], 0)

    def test_deleting_one_leaves_the_others_alone(self) -> None:
        from server.app import delete_radar_purchase

        ids = self.seed([ebay_summary("v1|1|0", price="40.00"), ebay_summary("v1|2|0", price="60.00")])
        primera = record_radar_purchase(
            self.config, {"listingId": ids[0], "entityType": "console", "entityId": "ps2", "priceAmount": 40.0}
        )["purchase"]["id"]
        record_radar_purchase(
            self.config, {"listingId": ids[1], "entityType": "console", "entityId": "ps3", "priceAmount": 60.0}
        )

        delete_radar_purchase(self.config, primera)
        self.assertEqual(compute_radar_budget(self.config)["spent"], 60.0)

    def test_deleting_a_purchase_that_does_not_exist_is_refused(self) -> None:
        from server.app import delete_radar_purchase

        with self.assertRaises(ApiError) as raised:
            delete_radar_purchase(self.config, "purchase-noexiste")
        self.assertEqual(raised.exception.status, 404)

    def test_the_decision_is_a_separate_fact(self) -> None:
        # Borrar la compra no toca la decisión sobre la publicación: son dos
        # hechos distintos y se limpian por separado, a propósito.
        from server.app import delete_radar_purchase, list_radar_decisions

        purchase_id = self.comprar()
        delete_radar_purchase(self.config, purchase_id)
        decisiones = list_radar_decisions(self.config)["items"]
        self.assertEqual([d["decision"] for d in decisiones], ["purchased"])

    def test_the_history_lists_what_was_registered(self) -> None:
        from server.app import list_radar_purchases

        self.comprar()
        items = list_radar_purchases(self.config)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["entityId"], "ps3")
        self.assertIsNone(items[0]["shippedAt"])
