"""Búsquedas de varios títulos que juzgan cada título por lo suyo.

Hasta acá una búsqueda que perseguía cuarenta títulos tenía un solo techo, una
sola referencia —la mediana de toda la corrida— y ninguna forma de decirle a
«Registrar compra» qué juego se compró. Lo que se prueba:

- cada consulta puede declarar qué juego del catálogo persigue y su techo;
- ese techo viaja a la fuente y también se aplica al juzgar, sin aflojar nunca
  el techo general;
- el precio de referencia es el de ese juego, no el de la corrida;
- la coincidencia recuerda qué título la encontró, y el feed ofrece escribir
  exactamente ese juego.

Y tres criterios chicos que el portafolio de octubre necesita: precio por pieza
en lotes, términos preferidos que no filtran, y bundles de consola comparados
contra la consola más sus juegos.

Los datos de catálogo son inventados: nada acá depende de la colección real.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.app import (
    ApiError,
    create_radar_search,
    init_db,
    list_radar_feed,
    list_radar_searches,
    preview_radar_search,
    radar_bundle_game_value,
    radar_targeted_entities,
    run_radar_search,
    update_radar_search,
)
from server.radar.matching import best_title_target, evaluate_match
from server.radar.model import MarketplaceListing
from server.radar.valuation import bundle_adjusted_benchmark, peer_listing_benchmark, reference_from_price_range


def ebay_summary(item_id: str, title: str, price: str = "24.00") -> dict:
    return {
        "itemId": item_id,
        "title": title,
        "itemWebUrl": f"https://www.ebay.com/itm/{item_id}",
        "price": {"value": price, "currency": "USD"},
        "condition": "Pre-owned",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"country": "US"},
        "image": {"imageUrl": "https://i.ebayimg.com/example.jpg"},
        "shippingOptions": [
            {"shippingCostType": "FIXED", "shippingCost": {"value": "5.00", "currency": "USD"}}
        ],
        "seller": {"username": "retrogames", "feedbackPercentage": "99.4"},
    }


def listing(title: str, price: float | None = 20.0) -> MarketplaceListing:
    return MarketplaceListing(
        source_id="ebay-us",
        external_id=title,
        title=title,
        listing_url="https://example.com/item",
        price_amount=price,
        price_currency="USD",
    )


CATALOG_GAMES = {
    "byConsole": {
        "consola-x": {
            "juegosCatalogo": [
                {"id": "juego-a", "nombre": "Juego A", "priceRange": {"low": 15, "mid": 22, "high": 30}},
                {"id": "juego-b", "nombre": "Juego B", "priceRange": {"low": 40, "mid": 60, "high": 80}},
                {"id": "juego-c", "nombre": "Juego C", "priceRange": {"low": 10, "mid": 14, "high": 20}},
                {"id": "juego-d", "nombre": "Juego D", "priceRange": {"low": 12, "mid": 18, "high": 25}},
                {"id": "juego-e", "nombre": "Juego E", "priceRange": {"low": 20, "mid": 28, "high": 35}},
                {"id": "juego-f", "nombre": "Juego F", "priceRange": {"low": 8, "mid": 12, "high": 16}},
            ]
        },
        "consola-escasa": {
            "juegosCatalogo": [
                {"id": "unico", "nombre": "Único", "priceRange": {"low": 30, "mid": 40, "high": 50}},
            ]
        },
    }
}

CATALOG_CONSOLES = {
    "consolas": [
        {
            "id": "consola-x",
            "nombre": "Consola X",
            "precioPriceChart": 100,
            "precioEbaySold": 100,
            "precioNotas": {"verificadoEn": "2026-10-01"},
        }
    ]
}


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


class RadarCatalogTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        catalog_dir = self.config.static_dir / "data"
        catalog_dir.mkdir(parents=True)
        (catalog_dir / "console-games.json").write_text(json.dumps(CATALOG_GAMES), encoding="utf-8")
        (catalog_dir / "consoles.json").write_text(json.dumps(CATALOG_CONSOLES), encoding="utf-8")
        init_db(self.config)

    def create(self, payload: dict) -> dict:
        return create_radar_search(self.config, payload)["search"]

    def wishlist(self, **criteria: object) -> dict:
        base = {
            "queries": ["Juego B zeta"],
            "anyTerms": ["zeta"],
            "maxItemPrice": 100,
            "titleTargets": [
                {"query": "Juego A zeta", "gameId": "juego-a", "name": "Juego A", "maxItemPrice": 30},
                {"query": "Juego B zeta", "gameId": "juego-b", "name": "Juego B", "maxItemPrice": 80},
            ],
            "resultLimit": 20,
        }
        base.update(criteria)
        return self.create(
            {
                "name": "Deseados de Consola X",
                "platform": "Consola X",
                "searchType": "chase",
                "entityType": "game",
                "entityConsoleId": "consola-x",
                "searchQuery": "Juego A zeta",
                "criteria": base,
            }
        )

    def run_with(self, search: dict, responses: dict[str, list[dict]]) -> list[tuple[str, dict]]:
        seen: list[tuple[str, dict]] = []

        def fake(self_source, config, query, criteria, destination=None):  # noqa: ANN001 - firma del adapter
            seen.append((query, dict(criteria)))
            return responses.get(query, [])

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", new=fake):
            run_radar_search(self.config, search["id"])
        return seen

    def results(self, search_id: str) -> list[dict]:
        item = next(entry for entry in list_radar_searches(self.config)["items"] if entry["id"] == search_id)
        return item["results"]


class TitleTargetCriteriaTests(RadarCatalogTestCase):
    def test_targets_persist_without_repeats(self) -> None:
        created = self.wishlist(
            titleTargets=[
                {"query": " Juego A  zeta ", "gameId": "juego-a", "maxItemPrice": 30, "basura": 1},
                {"query": "juego a zeta", "gameId": "otro"},
            ]
        )
        targets = created["criteria"]["titleTargets"]
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0], {"query": "Juego A zeta", "gameId": "juego-a", "name": "", "maxItemPrice": 30})

    def test_targets_must_be_objects(self) -> None:
        with self.assertRaises(ApiError):
            self.wishlist(titleTargets="Juego A")
        with self.assertRaises(ApiError):
            self.wishlist(titleTargets=["Juego A"])

    def test_a_target_without_query_is_refused(self) -> None:
        with self.assertRaises(ApiError):
            self.wishlist(titleTargets=[{"gameId": "juego-a"}])

    def test_too_many_targets_are_refused(self) -> None:
        with self.assertRaises(ApiError):
            self.wishlist(titleTargets=[{"query": f"juego {index}"} for index in range(50)])

    def test_editing_other_criteria_keeps_the_targets(self) -> None:
        """El formulario no manda los objetivos: guardar otra cosa no los borra."""
        created = self.wishlist()
        updated = update_radar_search(self.config, created["id"], {"criteria": {"maxItemPrice": 90}})["search"]
        self.assertEqual(len(updated["criteria"]["titleTargets"]), 2)


class TitleTargetRunTests(RadarCatalogTestCase):
    def test_each_title_reaches_the_source_with_its_own_ceiling(self) -> None:
        search = self.wishlist()
        seen = dict(self.run_with(search, {}))
        self.assertEqual(seen["Juego A zeta"]["maxItemPrice"], 30)
        self.assertEqual(seen["Juego B zeta"]["maxItemPrice"], 80)

    def test_a_title_ceiling_never_loosens_the_general_one(self) -> None:
        search = self.wishlist(maxItemPrice=50)
        seen = dict(self.run_with(search, {}))
        self.assertEqual(seen["Juego A zeta"]["maxItemPrice"], 30)
        self.assertEqual(seen["Juego B zeta"]["maxItemPrice"], 50)

    def test_a_copy_above_its_title_ceiling_is_rejected_even_under_the_general_one(self) -> None:
        search = self.wishlist()
        self.run_with(
            search,
            {"Juego A zeta": [ebay_summary("a-cara", "Juego A Zeta cartridge", "45.00")]},
        )
        self.assertEqual(self.results(search["id"]), [])

    def test_each_title_is_judged_against_its_own_catalog_price(self) -> None:
        search = self.wishlist()
        self.run_with(
            search,
            {
                "Juego A zeta": [ebay_summary("a-1", "Juego A Zeta cartridge", "20.00")],
                "Juego B zeta": [ebay_summary("b-1", "Juego B Zeta cartridge", "50.00")],
            },
        )
        by_title = {result["title"]: result for result in self.results(search["id"])}
        self.assertEqual(by_title["Juego A Zeta cartridge"]["valuation"]["benchmark"]["value"], 22)
        self.assertEqual(by_title["Juego B Zeta cartridge"]["valuation"]["benchmark"]["value"], 60)
        self.assertEqual(by_title["Juego A Zeta cartridge"]["titleTarget"]["gameId"], "juego-a")
        self.assertEqual(by_title["Juego B Zeta cartridge"]["titleTarget"]["consoleId"], "consola-x")

    def test_the_sold_comparables_link_searches_that_title(self) -> None:
        search = self.wishlist()
        self.run_with(
            search, {"Juego B zeta": [ebay_summary("b-1", "Juego B Zeta cartridge", "50.00")]}
        )
        (result,) = self.results(search["id"])
        self.assertIn("Juego+B", result["valuation"]["soldComparables"]["url"])

    def test_the_feed_offers_to_register_exactly_that_game(self) -> None:
        search = self.wishlist()
        self.run_with(
            search, {"Juego B zeta": [ebay_summary("b-1", "Juego B Zeta cartridge", "50.00")]}
        )
        (item,) = list_radar_feed(self.config)["items"]
        (match,) = item["matches"]
        self.assertEqual(match["entityType"], "game")
        self.assertEqual(match["entityId"], "juego-b")
        self.assertEqual(match["entityConsoleId"], "consola-x")
        self.assertEqual(match["entityName"], "Juego B")
        self.assertEqual(match["searchName"], "Deseados de Consola X")

    def test_a_search_without_targets_keeps_its_own_entity(self) -> None:
        search = self.create(
            {
                "name": "Consola X lista",
                "platform": "Consola X",
                "searchType": "console",
                "entityType": "console",
                "entityId": "consola-x",
                "searchQuery": "consola x console",
            }
        )
        self.run_with(search, {"consola x console": [ebay_summary("c-1", "Consola X Console tested", "90.00")]})
        (item,) = list_radar_feed(self.config)["items"]
        (match,) = item["matches"]
        self.assertEqual((match["entityType"], match["entityId"], match["entityName"]), ("console", "consola-x", ""))
        self.assertIsNone(match["titleTarget"])

    def test_the_preview_says_which_title_found_each_result(self) -> None:
        search = self.wishlist()

        def fake(self_source, config, query, criteria, destination=None):  # noqa: ANN001
            if query == "Juego A zeta":
                return [ebay_summary("a-1", "Juego A Zeta cartridge", "20.00")]
            return []

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", new=fake):
            preview = preview_radar_search(self.config, search["id"])
        (result,) = preview["results"]
        self.assertEqual(result["titleTarget"]["name"], "Juego A")


class CoverageTests(RadarCatalogTestCase):
    """Una búsqueda de varios títulos cubre cada juego que persigue."""

    def test_title_targets_count_as_covered_games(self) -> None:
        self.wishlist()
        _, _, games = radar_targeted_entities(self.config, active_only=True)
        self.assertIn(("consola-x", "juego-a"), games)
        self.assertIn(("consola-x", "juego-b"), games)

    def lot(self) -> dict:
        return self.create(
            {
                "name": "Lotes de Zeta",
                "platform": "Zeta",
                "searchType": "lot",
                "entityConsoleId": "consola-x",
                "searchQuery": "zeta game lot",
                "criteria": {"minLotSize": 8, "maxPricePerPiece": 10},
            }
        )

    def test_a_lot_linked_by_console_counts_as_that_consoles_lot(self) -> None:
        self.lot()
        consoles, lots, _ = radar_targeted_entities(self.config, active_only=True)
        self.assertIn("consola-x", lots)
        self.assertNotIn("consola-x", consoles)

    def test_buying_a_lot_never_offers_to_register_the_console(self) -> None:
        search = self.lot()
        self.run_with(search, {"zeta game lot": [ebay_summary("lot-1", "Zeta game lot of 12 games", "96.00")]})
        (item,) = list_radar_feed(self.config)["items"]
        (match,) = item["matches"]
        self.assertEqual((match["entityType"], match["entityId"]), ("", ""))


class TitleAttributionTests(RadarCatalogTestCase):
    """eBay contesta una consulta con otros juegos: cada uno va al título que nombra."""

    def test_a_listing_brought_by_one_query_is_filed_under_the_title_it_names(self) -> None:
        search = self.wishlist()
        self.run_with(
            search, {"Juego A zeta": [ebay_summary("b-1", "Juego B Zeta cartridge", "50.00")]}
        )
        (result,) = self.results(search["id"])
        self.assertEqual(result["titleTarget"]["gameId"], "juego-b")
        self.assertEqual(result["valuation"]["benchmark"]["value"], 60)

    def test_it_is_judged_with_the_ceiling_of_the_title_it_names(self) -> None:
        """Un Juego A de 45 que llega por la consulta de Juego B no pasa: el techo de A es 30."""
        search = self.wishlist()
        self.run_with(
            search, {"Juego B zeta": [ebay_summary("a-cara", "Juego A Zeta cartridge", "45.00")]}
        )
        self.assertEqual(self.results(search["id"]), [])

    def test_a_listing_that_names_no_wished_title_is_not_part_of_the_search(self) -> None:
        search = self.wishlist()
        self.run_with(
            search, {"Juego A zeta": [ebay_summary("otro", "Pelota Zeta cartridge", "10.00")]}
        )
        self.assertEqual(self.results(search["id"]), [])


class BestTitleTargetTests(unittest.TestCase):
    TARGETS = [
        {"name": name}
        for name in (
            "Donkey Kong Country",
            "Donkey Kong Country 2: Diddy's Kong Quest",
            "Crash Bandicoot",
            "Crash Bandicoot 3: Warped",
            "The Legend of Zelda: A Link to the Past",
            "Oddworld: Abe’s Oddysee",
            "Oddworld: Abe’s Exoddus",
            "Kirby's Dream Land 3",
            "Sonic 3 & Knuckles",
        )
    ]

    def name_for(self, title: str) -> str | None:
        target = best_title_target(title, self.TARGETS)
        return target["name"] if target else None

    def test_the_more_specific_title_wins(self) -> None:
        self.assertEqual(self.name_for("Donkey Kong Country 2 SNES authentic"), "Donkey Kong Country 2: Diddy's Kong Quest")
        self.assertEqual(self.name_for("Donkey Kong Country SNES cart"), "Donkey Kong Country")

    def test_a_subtitle_names_the_game_without_its_number(self) -> None:
        self.assertEqual(self.name_for("Crash Bandicoot Warped PS1 Black Label"), "Crash Bandicoot 3: Warped")
        self.assertEqual(self.name_for("Zelda Link to the Past SNES"), "The Legend of Zelda: A Link to the Past")

    def test_twin_titles_are_told_apart_by_their_subtitle(self) -> None:
        self.assertEqual(self.name_for("Oddworld Abes Exoddus PS1"), "Oddworld: Abe’s Exoddus")

    def test_apostrophes_and_ampersands_do_not_matter(self) -> None:
        self.assertEqual(self.name_for("Kirbys Dream Land 3 SNES"), "Kirby's Dream Land 3")
        self.assertEqual(self.name_for("Sonic 3 and Knuckles Genesis"), "Sonic 3 & Knuckles")

    def test_a_different_game_is_none_of_them(self) -> None:
        self.assertIsNone(self.name_for("Sonic & Knuckles Genesis"))
        self.assertIsNone(self.name_for("NBA Jam SNES"))


class PricePerPieceTests(unittest.TestCase):
    def test_a_lot_that_costs_too_much_per_piece_is_rejected(self) -> None:
        verdict = evaluate_match(listing("Consola X lot of 5 games", 60), {"maxPricePerPiece": 8})
        self.assertFalse(verdict.matched)
        self.assertTrue(any("por pieza" in blocker for blocker in verdict.blockers))

    def test_a_cheap_lot_says_what_each_piece_costs(self) -> None:
        verdict = evaluate_match(listing("Consola X lot of 20 games", 120), {"maxPricePerPiece": 8})
        self.assertTrue(verdict.matched)
        self.assertIn("USD 6 por pieza (20 declaradas)", verdict.reasons)

    def test_an_uncounted_lot_is_unverified_not_rejected(self) -> None:
        verdict = evaluate_match(listing("Consola X games bundle", 120), {"maxPricePerPiece": 8})
        self.assertTrue(verdict.matched)
        self.assertTrue(any("precio por pieza" in item for item in verdict.unverified))

    def test_the_missing_count_is_not_punished_twice(self) -> None:
        """Con mínimo de piezas, ese chequeo ya dice que falta la cantidad."""
        verdict = evaluate_match(
            listing("Consola X games bundle", 120), {"maxPricePerPiece": 8, "minLotSize": 10}
        )
        self.assertEqual(len(verdict.unverified), 1)


class LotAccessoryAndDefectTests(unittest.TestCase):
    def test_a_lot_search_rejects_a_lot_of_accessories(self) -> None:
        verdict = evaluate_match(
            listing("LOT OF 9 ORIGINAL GAME BOY CARTRIDGE DUST CASES", 20), {}, rejects_accessories=True
        )
        self.assertFalse(verdict.matched)
        self.assertIn("Es un accesorio, no un lote de juegos", verdict.blockers)

    def test_a_game_lot_with_a_case_is_still_a_game_lot(self) -> None:
        verdict = evaluate_match(
            listing("Nintendo DS & 3DS Game Cartridge Lot with carrying case", 40), {}, rejects_accessories=True
        )
        self.assertTrue(verdict.matched)

    def test_a_declared_defect_rejects_a_unit_that_has_to_work(self) -> None:
        verdict = evaluate_match(
            listing("PS3 CECH-3001A 160GB Console | Bad HDMI, AV Works, Tested", 50), {"tested": "preferred"}
        )
        self.assertFalse(verdict.matched)
        self.assertTrue(any("defecto" in blocker for blocker in verdict.blockers))

    def test_a_game_named_bad_is_not_a_defect(self) -> None:
        """Sin pregunta de funcionamiento, «Bad» puede ser el nombre de un juego."""
        verdict = evaluate_match(listing("Bad Dudes NES cartridge", 15), {})
        self.assertTrue(verdict.matched)


class PreferTermsTests(unittest.TestCase):
    def test_a_preferred_term_never_rejects(self) -> None:
        verdict = evaluate_match(listing("Consola X console tested"), {"preferTerms": ["games"]})
        self.assertTrue(verdict.matched)
        self.assertEqual(verdict.unverified, [])

    def test_bringing_what_is_preferred_ranks_higher(self) -> None:
        criteria = {"preferTerms": ["games", "2 controllers"], "tested": "preferred"}
        con = evaluate_match(listing("Consola X console tested with 2 controllers and 5 games"), criteria)
        sin = evaluate_match(listing("Consola X console tested"), criteria)
        self.assertGreater(con.confidence, sin.confidence)
        self.assertTrue(any("Trae lo que preferís" in reason for reason in con.reasons))


class BundleBenchmarkTests(RadarCatalogTestCase):
    def test_the_game_value_is_the_low_quartile_of_the_catalog(self) -> None:
        # lows ordenados: 8, 10, 12, 15, 20, 40 → índice 6 // 4 = 1 → 10
        self.assertEqual(radar_bundle_game_value(self.config, "consola-x"), {"value": 10, "sample": 6})

    def test_a_thin_catalog_gives_no_game_value(self) -> None:
        self.assertIsNone(radar_bundle_game_value(self.config, "consola-escasa"))
        self.assertIsNone(radar_bundle_game_value(self.config, "no-existe"))

    def test_a_bundle_is_compared_against_console_plus_its_games(self) -> None:
        base = reference_from_price_range({"mid": 100}, entity_id="consola-x", observed_at="2026-10-01")
        adjusted = bundle_adjusted_benchmark(base, declared_games=5, game_value=10, catalog_sample=6)
        self.assertEqual(adjusted.value, 150)
        self.assertLess(adjusted.confidence, base.confidence)
        self.assertIn("5 juego(s)", adjusted.notes)

    def test_the_adjustment_has_a_ceiling(self) -> None:
        base = reference_from_price_range({"mid": 100}, entity_id="consola-x")
        adjusted = bundle_adjusted_benchmark(base, declared_games=40, game_value=10, max_games=10)
        self.assertEqual(adjusted.value, 200)
        self.assertIn("tope", adjusted.notes)

    def test_peer_listings_are_never_adjusted(self) -> None:
        """La mediana de la corrida ya incluye los bundles: sumarle juegos los contaría dos veces."""
        peers = peer_listing_benchmark([90, 100, 110, 120], entity_id="consola-x")
        self.assertIs(bundle_adjusted_benchmark(peers, declared_games=5, game_value=10), peers)

    def test_without_a_declared_count_nothing_changes(self) -> None:
        base = reference_from_price_range({"mid": 100}, entity_id="consola-x")
        self.assertIs(bundle_adjusted_benchmark(base, declared_games=None, game_value=10), base)

    def test_a_console_search_values_its_bundles_end_to_end(self) -> None:
        search = self.create(
            {
                "name": "Consola X lista",
                "platform": "Consola X",
                "searchType": "console",
                "entityType": "console",
                "entityId": "consola-x",
                "searchQuery": "consola x console",
                "criteria": {"resultLimit": 20},
            }
        )

        def fake(self_source, config, query, criteria, destination=None):  # noqa: ANN001
            return [
                ebay_summary("solo", "Consola X Console tested", "100.00"),
                ebay_summary("bundle", "Consola X Console bundle with 5 games", "140.00"),
            ]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", new=fake):
            run_radar_search(self.config, search["id"])
        item = next(entry for entry in list_radar_searches(self.config)["items"] if entry["id"] == search["id"])
        by_id = {result["externalId"]: result for result in item["results"]}
        self.assertEqual(by_id["solo"]["valuation"]["benchmark"]["value"], 100)
        self.assertEqual(by_id["bundle"]["valuation"]["benchmark"]["value"], 150)


if __name__ == "__main__":
    unittest.main()
