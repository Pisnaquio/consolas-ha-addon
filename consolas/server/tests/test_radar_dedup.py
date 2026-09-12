from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.app import (
    connect_db,
    create_radar_search,
    delete_radar_search,
    init_db,
    list_radar_feed,
    list_radar_listings,
    record_radar_decision,
    run_radar_search,
)


def ebay_summary(item_id: str, price: str = "50.00", title: str = "PS2 Slim SCPH-90001 tested", **overrides: object) -> dict:
    summary = {
        "itemId": item_id,
        "title": title,
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


class ContentDedupTestCase(unittest.TestCase):
    """eBay a veces asigna dos `itemId` al mismo artículo (relist, doble publicación).

    Cada `itemId` sigue siendo una fila propia — nunca se pisan — pero mostrarlas
    dos veces en el feed o el inventario es mostrar la misma oportunidad dos veces.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)
        delete_radar_search(self.config, "iss-deluxe-snes")
        self.search = create_radar_search(self.config, {"name": "PS2"})["search"]

    def seed(self, summaries: list[dict]) -> None:
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=summaries):
            run_radar_search(self.config, self.search["id"])

    def listing_count(self) -> int:
        with connect_db(self.config) as conn:
            return conn.execute("SELECT COUNT(*) FROM radar_listings").fetchone()[0]


class FeedDedupTests(ContentDedupTestCase):
    def test_two_item_ids_for_the_same_article_show_once_in_the_feed(self) -> None:
        self.seed([ebay_summary("v1|1|0"), ebay_summary("v1|2|0")])

        # Las dos filas existen — la fuente no se pisa — pero es una sola oportunidad.
        self.assertEqual(self.listing_count(), 2)
        feed = list_radar_feed(self.config)["items"]
        self.assertEqual(len(feed), 1)

    def test_a_different_price_is_not_treated_as_a_duplicate(self) -> None:
        self.seed([ebay_summary("v1|1|0", price="50.00"), ebay_summary("v1|2|0", price="65.00")])

        feed = list_radar_feed(self.config)["items"]
        self.assertEqual(len(feed), 2, "precios distintos son ofertas distintas, aunque el título coincida")

    def test_a_different_title_is_not_treated_as_a_duplicate(self) -> None:
        self.seed([ebay_summary("v1|1|0", title="PS2 Slim SCPH-90001 tested"), ebay_summary("v1|2|0", title="PS2 Fat SCPH-30001 loose")])

        feed = list_radar_feed(self.config)["items"]
        self.assertEqual(len(feed), 2)

    def test_title_casing_and_spacing_do_not_defeat_the_match(self) -> None:
        self.seed([
            ebay_summary("v1|1|0", title="PS2 Slim SCPH-90001 tested"),
            ebay_summary("v1|2|0", title="  ps2  slim scph-90001 TESTED "),
        ])

        feed = list_radar_feed(self.config)["items"]
        self.assertEqual(len(feed), 1)

    def test_dismissing_one_item_id_dismisses_the_duplicate_too(self) -> None:
        self.seed([ebay_summary("v1|1|0"), ebay_summary("v1|2|0")])
        with connect_db(self.config) as conn:
            listing_id = str(conn.execute("SELECT id FROM radar_listings WHERE external_id = 'v1|1|0'").fetchone()[0])

        record_radar_decision(self.config, {"listingId": listing_id, "decision": "dismissed", "reason": "caro"})

        payload = list_radar_feed(self.config)
        self.assertEqual(payload["items"], [], "el relist del mismo artículo no puede reabrir lo que ya se descartó")
        self.assertEqual(payload["counts"]["dismissed"], 1)

    def test_the_matches_shown_are_the_union_of_both_item_ids(self) -> None:
        second_search = create_radar_search(self.config, {"name": "Slim PS2"})["search"]
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[ebay_summary("v1|1|0")]):
            run_radar_search(self.config, self.search["id"])
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[ebay_summary("v1|2|0")]):
            run_radar_search(self.config, second_search["id"])

        item = list_radar_feed(self.config)["items"][0]
        search_names = {match["searchName"] for match in item["matches"]}
        self.assertEqual(search_names, {"PS2", "Slim PS2"}, "las dos búsquedas encontraron el mismo artículo, cada una bajo un itemId")


class ListingsInventoryDedupTests(ContentDedupTestCase):
    def test_two_item_ids_for_the_same_article_are_one_row_in_the_inventory(self) -> None:
        self.seed([ebay_summary("v1|1|0"), ebay_summary("v1|2|0")])

        payload = list_radar_listings(self.config)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["duplicateCount"], 1)

    def test_a_listing_with_no_duplicates_says_so(self) -> None:
        self.seed([ebay_summary("v1|1|0")])

        item = list_radar_listings(self.config)["items"][0]
        self.assertEqual(item["duplicateCount"], 0)


if __name__ == "__main__":
    unittest.main()
