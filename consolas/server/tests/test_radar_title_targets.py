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
from server.radar.valuation import (
    bundle_adjusted_benchmark,
    lot_ceiling_benchmark,
    peer_listing_benchmark,
    reference_from_price_range,
)


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
            "precioCIB": 200,
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
        self.assertEqual(
            targets[0],
            {
                "query": "Juego A zeta",
                "gameId": "juego-a",
                "name": "",
                "aliases": [],
                "maxItemPrice": 30,
                "maxBoxedPrice": None,
            },
        )

    def test_aliases_persist_trimmed_and_without_repeats(self) -> None:
        created = self.wishlist(
            titleTargets=[
                {"query": "Juego A zeta", "gameId": "juego-a", "name": "Juego A", "aliases": [" Juego  Alfa ", "juego alfa", ""]},
            ]
        )
        self.assertEqual(created["criteria"]["titleTargets"][0]["aliases"], ["Juego Alfa"])

    def test_aliases_must_be_a_short_list_of_names(self) -> None:
        with self.assertRaises(ApiError):
            self.wishlist(titleTargets=[{"query": "Juego A zeta", "aliases": "Juego Alfa"}])
        with self.assertRaises(ApiError):
            self.wishlist(titleTargets=[{"query": "Juego A zeta", "aliases": [{"name": "Juego Alfa"}]}])
        with self.assertRaises(ApiError):
            self.wishlist(titleTargets=[{"query": "Juego A zeta", "aliases": [f"Juego {index}" for index in range(7)]}])

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

    def test_another_name_of_a_title_is_filed_under_it(self) -> None:
        """La grafía alternativa se juzga con el techo y el precio de su título."""
        search = self.wishlist(
            titleTargets=[
                {"query": "Juego A zeta", "gameId": "juego-a", "name": "Juego A", "aliases": ["Juego Alfa"], "maxItemPrice": 30},
                {"query": "Juego B zeta", "gameId": "juego-b", "name": "Juego B", "maxItemPrice": 80},
            ]
        )
        self.run_with(
            search,
            {
                "Juego B zeta": [
                    ebay_summary("alfa", "Juego Alfa Zeta cartridge", "20.00"),
                    ebay_summary("alfa-cara", "Juego Alfa Zeta cartridge tested", "45.00"),
                ]
            },
        )
        (result,) = self.results(search["id"])
        self.assertEqual(result["externalId"], "alfa")
        self.assertEqual(result["titleTarget"]["gameId"], "juego-a")
        self.assertEqual(result["valuation"]["benchmark"]["value"], 22)

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

    def test_a_sequel_that_is_not_wished_is_neither_of_its_predecessors(self) -> None:
        self.assertIsNone(self.name_for("Donkey Kong Country 3: Dixie Kong's Double Trouble! SNES"))

    def test_a_series_name_alone_names_none_of_its_games(self) -> None:
        self.assertIsNone(self.name_for("Oddworld PS1 black label"))


class SeriesTitleTargetTests(unittest.TestCase):
    """Varios objetivos de una misma serie, como los God of War de PS3."""

    TARGETS = [
        {"name": name}
        for name in (
            "God of War III",
            "God of War: Ascension",
            "God of War Collection",
            "God of War: Origins Collection",
        )
    ]

    def name_for(self, title: str) -> str | None:
        target = best_title_target(title, self.TARGETS)
        return target["name"] if target else None

    def test_each_game_of_the_series_is_told_apart(self) -> None:
        self.assertEqual(self.name_for("God of War III PS3 Greatest Hits"), "God of War III")
        self.assertEqual(self.name_for("God of War Ascension PS3 complete"), "God of War: Ascension")
        self.assertEqual(self.name_for("God of War Collection PS3 tested"), "God of War Collection")
        self.assertEqual(self.name_for("God of War Origins Collection PS3"), "God of War: Origins Collection")

    def test_a_roman_numeral_and_its_digit_are_the_same_game(self) -> None:
        self.assertEqual(self.name_for("God of War 3 PS3 Black Label"), "God of War III")

    def test_a_game_of_the_series_that_is_not_wished_is_none_of_them(self) -> None:
        self.assertIsNone(self.name_for("God of War Saga PS3 2 discs"))
        self.assertIsNone(self.name_for("God of War PS3 game"))

    def test_the_number_of_the_console_is_not_the_number_of_the_game(self) -> None:
        """El formato de título del catálogo de eBay: «(Sony PlayStation 3, 2013)»."""
        self.assertEqual(
            self.name_for("God of War Ascension (Sony PlayStation 3, 2013) CIB"), "God of War: Ascension"
        )
        self.assertEqual(
            self.name_for("God of War Collection (Sony PlayStation 3, 2009) Complete"), "God of War Collection"
        )
        self.assertEqual(self.name_for("God of War III (Sony PlayStation 3, 2010)"), "God of War III")
        self.assertIsNone(self.name_for("God of War Sony PlayStation 3 game"))

    def test_two_in_one_is_a_compilation_not_a_sequel(self) -> None:
        self.assertEqual(self.name_for("God of War Collection 2 in 1 PS3"), "God of War Collection")


class SequelTitleTargetTests(unittest.TestCase):
    """Un título sin número no nombra su secuela, aunque la secuela no esté en la búsqueda."""

    TARGETS = [
        {"name": name}
        for name in (
            "Dark Souls",
            "BioShock",
            "Sonic the Hedgehog",
            "Castlevania",
            "Super Mario Bros.",
            "Mario Kart 64",
            "Red Dead Redemption",
            "Final Fantasy VII",
            "Tomb Raider II",
            "Kingdom Hearts",
            "Spyro: Year of the Dragon",
            "Star Wars: Knights of the Old Republic",
            "Wario Land: Super Mario Land 3",
        )
    ]

    def name_for(self, title: str) -> str | None:
        target = best_title_target(title, self.TARGETS)
        return target["name"] if target else None

    def test_a_numbered_sequel_is_not_the_original(self) -> None:
        for title in (
            "Dark Souls II PS3",
            "BioShock 2 (Sony PlayStation 3) PS3",
            "Sonic the Hedgehog 2 Genesis",
            "Castlevania II: Simon's Quest NES",
            "Super Mario Bros. 2 Nintendo NES",
            "Red Dead Redemption 2 - Sony PlayStation 4",
            "Red dead redemption 2 keychain",
        ):
            with self.subTest(title=title):
                self.assertIsNone(self.name_for(title))

    def test_the_original_still_names_itself(self) -> None:
        self.assertEqual(self.name_for("Dark Souls PS3 Greatest Hits"), "Dark Souls")
        self.assertEqual(self.name_for("Sonic the Hedgehog Genesis 1991"), "Sonic the Hedgehog")
        self.assertEqual(self.name_for("Red Dead Redemption PS3 2 discs"), "Red Dead Redemption")

    def test_a_number_that_counts_is_not_a_sequel(self) -> None:
        for title, name in (
            ("Mario Kart 64 N64 2 player", "Mario Kart 64"),
            ("Red Dead Redemption 2 discs Game of the Year PS3", "Red Dead Redemption"),
            ("Final Fantasy VII 3 disc set PS1", "Final Fantasy VII"),
            ("Super Mario Bros 5 Screw NES cartridge", "Super Mario Bros."),
            ("Super Mario Bros. 5-Screw, 1st Print, NES Cartridge", "Super Mario Bros."),
            ("PS3 console + Dark Souls 2 controllers", "Dark Souls"),
        ):
            with self.subTest(title=title):
                self.assertEqual(self.name_for(title), name)

    def test_a_console_number_does_not_complete_a_title(self) -> None:
        targets = [{"name": "Resistance 3"}, {"name": "Mario Kart 64"}]
        self.assertIsNone(best_title_target("Resistance 1 & 2 Sony PlayStation 3 PS3 CIB bundle", targets))
        self.assertIsNone(best_title_target("Resistance 2 PS 3 complete", targets))
        self.assertEqual(best_title_target("Resistance 3 (Sony PlayStation 3, 2011)", targets)["name"], "Resistance 3")
        # Nintendo 64 no se toca: los juegos «… 64» son de esa consola.
        self.assertEqual(best_title_target("Mario Kart Nintendo 64 cartridge", targets)["name"], "Mario Kart 64")

    def test_disc_only_is_the_sequels_disc(self) -> None:
        self.assertIsNone(self.name_for("Dark Souls 2 Disc Only PS3"))

    def test_years_multipliers_and_platforms_are_not_sequels(self) -> None:
        for title, name in (
            ("Dark Souls PS3 x2", "Dark Souls"),
            ("Dark Souls PS 3 tested", "Dark Souls"),
            ("Kingdom Hearts PlayStation 2 Greatest Hits", "Kingdom Hearts"),
            ("Kingdom Hearts PS2/N64 lot", "Kingdom Hearts"),
        ):
            with self.subTest(title=title):
                self.assertEqual(self.name_for(title), name)

    def test_the_number_written_twice_is_the_same_game(self) -> None:
        """Medido en las corridas de octubre: «Final Fantasy 7 VII», «Tomb Raider II 2»."""
        self.assertEqual(self.name_for("Final Fantasy 7 VII PS1 Black Label"), "Final Fantasy VII")
        self.assertEqual(self.name_for("Tomb Raider II 2 (Playstation PS1) CIB"), "Tomb Raider II")
        self.assertIsNone(self.name_for("Castlevania 2 II Simon's Quest CIB NES"))

    def test_a_listing_that_also_names_the_original_brings_it(self) -> None:
        self.assertEqual(self.name_for("Dark Souls & Dark Souls II PS3 lot"), "Dark Souls")

    def test_a_subtitle_written_out_names_the_game_despite_its_number(self) -> None:
        self.assertEqual(self.name_for("Spyro 3 Year of the Dragon PS1"), "Spyro: Year of the Dragon")
        self.assertIsNone(self.name_for("Spyro 2 Ripto's Rage PS1"))
        self.assertEqual(
            self.name_for("Wario Land Super Mario Land 3 Game Boy"), "Wario Land: Super Mario Land 3"
        )
        self.assertIsNone(self.name_for("Wario Land 3 (Game Boy Color, 2000) *CARTRIDGE ONLY*"))

    def test_a_number_after_the_whole_title_is_a_sequel_too(self) -> None:
        self.assertIsNone(self.name_for("Star Wars Knights of the Old Republic II The Sith Lords Xbox"))


class AliasTitleTargetTests(unittest.TestCase):
    """Un título que se publica con más de un nombre, como los de PS3."""

    TARGETS = [
        {"name": "God of War Collection"},
        {"name": "God of War: Origins Collection", "aliases": ["God of War Collection Volume II"]},
        {"name": "Metal Gear Solid 4: Guns of the Patriots", "aliases": ["MGS4"]},
        {"name": "MotorStorm Pacific Rift", "aliases": ["Motor Storm Pacific Rift"]},
        {"name": "LittleBigPlanet 2", "aliases": ["Little Big Planet 2"]},
    ]

    def name_for(self, title: str, targets: list[dict] | None = None) -> str | None:
        target = best_title_target(title, self.TARGETS if targets is None else targets)
        return target["name"] if target else None

    def test_another_spelling_names_the_same_title(self) -> None:
        for title, name in (
            ("LittleBigPlanet 2 PS3 complete", "LittleBigPlanet 2"),
            ("Little Big Planet 2 PS3 CIB", "LittleBigPlanet 2"),
            ("Motor Storm Pacific Rift PS3", "MotorStorm Pacific Rift"),
            ("MotorStorm Pacific Rift PS3 Greatest Hits", "MotorStorm Pacific Rift"),
        ):
            with self.subTest(title=title):
                self.assertEqual(self.name_for(title), name)

    def test_the_european_name_goes_to_its_own_game(self) -> None:
        for title in (
            "God of War Collection Volume II PS3 PAL",
            "God of War HD Collection Volume 2 PS3",
            "God of War Origins Collection Volume II PS3",
        ):
            with self.subTest(title=title):
                self.assertEqual(self.name_for(title), "God of War: Origins Collection")
        self.assertEqual(self.name_for("God of War Collection PS3 complete"), "God of War Collection")

    def test_without_the_alias_the_european_name_is_none_of_them(self) -> None:
        """Antes se atribuía a God of War Collection; «Volume» dice que es otra compilación."""
        targets = [{"name": "God of War Collection"}, {"name": "God of War: Origins Collection"}]
        self.assertIsNone(self.name_for("God of War Collection Volume II PS3 PAL", targets))

    def test_an_abbreviation_is_a_name_too(self) -> None:
        self.assertEqual(self.name_for("MGS4 PS3 complete in box"), "Metal Gear Solid 4: Guns of the Patriots")

    def test_an_alias_does_not_name_other_games(self) -> None:
        self.assertIsNone(self.name_for("Little Big Planet 3 PS4"))
        self.assertIsNone(self.name_for("LittleBigPlanet Karting PS3"))
        self.assertIsNone(self.name_for("MotorStorm Apocalypse PS3"))


class CompilationTitleTargetTests(unittest.TestCase):
    """Una compilación no es uno de sus juegos, aunque traiga su nombre entero.

    Los títulos son reales: de las corridas de octubre y, los demás, de eBay el
    2026-10-13.
    """

    TARGETS = [
        {"name": "God of War III"},
        {"name": "God of War Collection"},
        {"name": "God of War: Origins Collection", "aliases": ["God of War Collection Volume II"]},
        {"name": "God of War: Ascension"},
        {"name": "Mass Effect 2"},
        {"name": "inFamous 2"},
        {"name": "Uncharted 2: Among Thieves"},
        {"name": "Dead Space 2"},
        {"name": "Metal Gear Solid"},
        {"name": "Killzone 2"},
        {"name": "Castlevania Lords of Shadow"},
    ]

    def name_for(self, title: str) -> str | None:
        target = best_title_target(title, self.TARGETS)
        return target["name"] if target else None

    def test_a_disc_from_a_compilation_is_not_the_single_game(self) -> None:
        for title in (
            "Mass Effect 2 - Trilogy ( PlayStation 3, PS3) DISC ONLY | NO TRACKING | M3703",
            "Infamous 2 Collection (Sony PlayStation 3) PS3 DISC ONLY",
            "Metal Gear Solid 1 -From The Essential Collection (PS1/PS2) CIB Complete Works",
            "God of War III 3 PS3 Disc 2 Replacement From God of War Saga Collection",
            "Metal Gear Solid: Master Collection Vol.1 - PlayStation 5 - Brand NEW - Sealed",
            "Castlevania: Lords of Shadow Collection PS3 (PlayStation 3, 2013) CIB *TESTED",
        ):
            with self.subTest(title=title):
                self.assertIsNone(self.name_for(title))

    def test_a_compilation_lot_is_none_of_its_games(self) -> None:
        for title in (
            "Uncharted Trilogy 1 2 3 Bundle Lot (Sony PlayStation 3 PS3)",
            "Sony PlayStation 3 PS3 CIB Dead Space 1 2 3 Trilogy Bundle Lot X",
            "God of War Saga and God of War Ascension PS3 Bundle. Mint",
            "Ps3 Lot Killzone 2 3 Farcry Far Cry 2 3 Cib Fps Game Collection Playstation 3",
        ):
            with self.subTest(title=title):
                self.assertIsNone(self.name_for(title))

    def test_another_compilation_is_not_the_target_compilation(self) -> None:
        """Sin el término excluido «saga», God of War Saga caía en Collection o en III."""
        for title in (
            "God of War Saga Collection Sony PS3 PlayStation 3 - CIB Complete",
            "God of War Saga Collection PS3 (Sony Playstation 3, 2012) 2-Discs No Manual.",
            "God of War Saga Dual Pack (Sony PlayStation 3 PS3) 1, 2, 3 - Cleaned & Tested",
            "God of War Saga Sony PlayStation 3 PS3 2012 5 Game Collection BCUS-98299",
        ):
            with self.subTest(title=title):
                self.assertIsNone(self.name_for(title))

    def test_a_compilation_target_keeps_its_own_listings(self) -> None:
        for title, name in (
            ("God of War Collection (PlayStation 3, PS3) I & II Remastered in HD, with Manual", "God of War Collection"),
            ("God of War Collection 2 in 1 PS3", "God of War Collection"),
            ("God of War Origins Collection (Sony PlayStation 3 PS3, 2011) Disc Only", "God of War: Origins Collection"),
            ("God of War Collection Volume II PS3 PAL", "God of War: Origins Collection"),
            ("God of War HD Collection Volume 2 PS3", "God of War: Origins Collection"),
        ):
            with self.subTest(title=title):
                self.assertEqual(self.name_for(title), name)

    def test_the_single_game_still_names_itself(self) -> None:
        """«Series» es la etiqueta de franquicia del catálogo de eBay, y «Personal
        Collection» la colección del vendedor: ninguna nombra una compilación."""
        for title, name in (
            ("Mass Effect 2 Sony PlayStation 3 PS3 CIB Complete In Box Tested And Working", "Mass Effect 2"),
            ("Metal Gear Solid Greatest Hits (Sony PlayStation 1, PS1) - CIB Complete TESTED", "Metal Gear Solid"),
            ("Sony Computer Entertainment inFAMOUS 2 PS3 Disc w/ Case T Infamous Series", "inFamous 2"),
            ("Sony God of War: Ascension PS3 God of War Series Action M NTSC 2013", "God of War: Ascension"),
            ("Killzone 2 PS3 Mint Complete Personal Collection", "Killzone 2"),
            ("Castlevania: Lords of Shadow (Sony PlayStation 3, 2010) Complete Tested & Works", "Castlevania Lords of Shadow"),
        ):
            with self.subTest(title=title):
                self.assertEqual(self.name_for(title), name)


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

    def test_some_issues_is_a_declared_defect(self) -> None:
        verdict = evaluate_match(
            listing("Sony PlayStation 1 Console SCPH-1001 w/ Controller - Tested - Some Issues- READ", 30),
            {"tested": "preferred"},
        )
        self.assertFalse(verdict.matched)

    def test_a_game_named_bad_is_not_a_defect(self) -> None:
        """Sin pregunta de funcionamiento, «Bad» puede ser el nombre de un juego."""
        verdict = evaluate_match(listing("Bad Dudes NES cartridge", 15), {})
        self.assertTrue(verdict.matched)


class LotVerdictTests(RadarCatalogTestCase):
    """Un lote se juzga por lo que cuesta cada pieza, contra el techo por pieza propio."""

    def test_the_reference_is_the_ceiling_times_the_declared_pieces(self) -> None:
        reference = lot_ceiling_benchmark(declared_pieces=22, per_piece_ceiling=4)
        self.assertEqual(reference.value, 88)
        self.assertEqual(reference.source, "lot-ceiling")
        self.assertIn("No es un precio de mercado", reference.notes)

    def test_without_a_count_there_is_no_per_piece_reference(self) -> None:
        self.assertIsNone(lot_ceiling_benchmark(declared_pieces=None, per_piece_ceiling=4))

    def test_a_big_cheap_lot_is_not_expensive_next_to_small_lots(self) -> None:
        search = self.create(
            {
                "name": "Lotes de Zeta",
                "platform": "Zeta",
                "searchType": "lot",
                "searchQuery": "zeta game lot",
                "criteria": {"minLotSize": 4, "maxPricePerPiece": 4, "resultLimit": 20},
            }
        )
        self.run_with(
            search,
            {
                "zeta game lot": [
                    ebay_summary("chico-1", "Zeta game lot of 4 games", "12.00"),
                    ebay_summary("chico-2", "Zeta game lot of 4 games tested", "13.00"),
                    ebay_summary("chico-3", "Zeta game lot of 5 games", "14.00"),
                    ebay_summary("grande", "Zeta game lot of 22 games", "50.00"),
                ]
            },
        )
        (grande,) = [r for r in self.results(search["id"]) if r["externalId"] == "grande"]
        self.assertEqual(grande["valuation"]["benchmark"]["source"], "lot-ceiling")
        self.assertNotEqual(grande["band"], "caro")


class BoxedVerdictTests(RadarCatalogTestCase):
    """Una consola en caja se mide contra el precio en caja, no contra el de una suelta."""

    def run_console(self, title: str, price: str) -> dict:
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
        self.run_with(search, {"consola x console": [ebay_summary("c-1", title, price)]})
        (result,) = self.results(search["id"])
        return result

    def test_a_complete_in_box_console_is_judged_against_the_cib_price(self) -> None:
        result = self.run_console("Consola X Console Complete in Box CIB tested", "190.00")
        self.assertEqual(result["valuation"]["benchmark"]["value"], 200)
        self.assertEqual(result["valuation"]["benchmark"]["completeness"], "cib")
        self.assertNotEqual(result["band"], "caro")

    def test_a_console_with_its_original_box_is_closer_to_cib_than_to_loose(self) -> None:
        result = self.run_console("Consola X Console w Original Box tested", "180.00")
        self.assertEqual(result["valuation"]["benchmark"]["value"], 200)

    def test_a_loose_console_keeps_the_loose_price(self) -> None:
        result = self.run_console("Consola X Console tested", "90.00")
        self.assertEqual(result["valuation"]["benchmark"]["value"], 100)

    def test_without_a_cib_reference_a_cib_is_never_measured_against_a_loose_copy(self) -> None:
        """CIB contra CIB: sin pares CIB no hay veredicto de precio, y el CIB
        declarado queda para revisar la caja y el manual."""
        search = self.wishlist()
        self.run_with(
            search, {"Juego A zeta": [ebay_summary("a-cib", "Juego A Zeta CIB complete in box", "28.00")]}
        )
        (result,) = self.results(search["id"])
        self.assertIsNone(result["valuation"]["benchmark"])
        self.assertEqual(result["objective"]["id"], "game-cib")
        self.assertEqual(result["band"], "revisar")
        self.assertTrue(any("CIB declarado" in note for note in result["valuation"]["verdictNotes"]))


class BoxedCeilingTests(RadarCatalogTestCase):
    """Una copia en caja tiene su propio techo: el del catálogo es de copia suelta."""

    def boxed_wishlist(self) -> dict:
        return self.wishlist(
            titleTargets=[
                {"query": "Juego A zeta", "gameId": "juego-a", "name": "Juego A", "maxItemPrice": 30, "maxBoxedPrice": 100},
                {"query": "Juego B zeta", "gameId": "juego-b", "name": "Juego B", "maxItemPrice": 80},
            ]
        )

    def test_the_source_is_asked_for_the_higher_of_both_ceilings(self) -> None:
        seen = dict(self.run_with(self.boxed_wishlist(), {}))
        self.assertEqual(seen["Juego A zeta"]["maxItemPrice"], 100)
        self.assertEqual(seen["Juego B zeta"]["maxItemPrice"], 80)

    def test_a_boxed_copy_uses_the_boxed_ceiling(self) -> None:
        search = self.boxed_wishlist()
        self.run_with(
            search, {"Juego A zeta": [ebay_summary("a-cib", "Juego A Zeta CIB complete in box", "60.00")]}
        )
        (result,) = self.results(search["id"])
        self.assertEqual(result["titleTarget"]["maxBoxedPrice"], 100)

    def test_a_loose_copy_still_uses_the_loose_ceiling(self) -> None:
        search = self.boxed_wishlist()
        self.run_with(
            search, {"Juego A zeta": [ebay_summary("a-suelto", "Juego A Zeta cartridge only", "60.00")]}
        )
        self.assertEqual(self.results(search["id"]), [])


class ExcludedRegionTests(unittest.TestCase):
    """Hoy el dueño no quiere ediciones japonesas: no puede jugarlas."""

    criteria = {"excludeRegions": ["ntsc-j"]}

    def test_japanese_editions_are_rejected(self) -> None:
        for title in (
            "Nintendo Super Famicom Console Boxed Working Complete With Manuals 2 Controllers",
            "*TESTED* JAPANESE Nintendo 3DS Aqua Blue Console Complete In Box CIB #G65",
            "Mario Golf GB - Japanese - Nintendo Game Boy Color",
            "Final Fantasy 6 / VI - battery tested US ship boxed Japan Super Famicom SFC",
            "Nintendo 64 N64 Console Japanese NTCS-J Complete In Box CIB US Seller",
        ):
            with self.subTest(title=title):
                self.assertFalse(evaluate_match(listing(title), self.criteria).matched)

    def test_made_in_japan_is_not_a_japanese_edition(self) -> None:
        verdict = evaluate_match(
            listing("Sony PlayStation SCPH-5501 NTSC Made in Japan Console Tested"), self.criteria
        )
        self.assertTrue(verdict.matched)

    def test_only_the_title_decides(self) -> None:
        """Una descripción que menciona Japón no convierte en japonesa una consola americana."""
        item = listing("Super Nintendo SNES Console SNS-001 Tested")
        item.description = "Plays US games. Not compatible with Japanese Super Famicom cartridges."
        self.assertTrue(evaluate_match(item, self.criteria).matched)

    def test_shipped_from_japan_counts_as_japanese(self) -> None:
        item = listing("Nintendo 3DS Cosmo Black Handheld Console System Complete in Box Used")
        item.location_label = "JP"
        self.assertFalse(evaluate_match(item, self.criteria).matched)

    def test_ll_is_the_japanese_xl(self) -> None:
        self.assertFalse(
            evaluate_match(listing("Nintendo 3DS LL XL Console Various Colors Region Locked S Rank"), self.criteria).matched
        )

    def test_a_region_free_japanese_unit_is_still_japanese(self) -> None:
        """La excepción del perfil la pide el dueño; el radar no la propone."""
        item = listing("Region-free Sony PS2 Slim console, tested and working, color options available")
        item.location_label = "JP"
        self.assertFalse(evaluate_match(item, self.criteria).matched)

    def test_an_american_console_shipped_from_the_us_passes(self) -> None:
        item = listing("Nintendo 3DS Aqua Blue Handheld Console Charger Tested US Model")
        item.location_label = "US"
        self.assertTrue(evaluate_match(item, self.criteria).matched)

    def test_other_regions_still_pass(self) -> None:
        self.assertTrue(evaluate_match(listing("Sega Mega Drive PAL Europe Console"), self.criteria).matched)


class ExcludedRegionCriteriaTests(RadarCatalogTestCase):
    def test_regions_are_normalized_and_unknown_ones_refused(self) -> None:
        created = self.create({"name": "Sin Japón", "criteria": {"excludeRegions": "NTSC-J, ntsc-j"}})
        self.assertEqual(created["criteria"]["excludeRegions"], ["ntsc-j"])
        with self.assertRaises(ApiError):
            self.create({"name": "Otra", "criteria": {"excludeRegions": ["marte"]}})


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
