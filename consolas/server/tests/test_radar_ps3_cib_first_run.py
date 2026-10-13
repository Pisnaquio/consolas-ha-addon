"""Lo que la primera corrida de «Juegos CIB PS3 — 20 prioritarios» dejó pasar.

La corrida del 2026-10-12 (v0.1.72) mostró cuatro huecos del motor, ninguno de
PS3 ni de esa búsqueda:

- dos protectores de caja, una guía de estrategia y una regla promocional
  pasaban como juegos y ocupaban los lugares de sus títulos;
- «No CIB» se leía como CIB declarado;
- «PEGI» no se leía como PAL;
- con la mejor copia suelta, la segunda podía ser otra suelta y tapar un CIB.

La búsqueda los tapó con términos excluidos («protector», «guide», «promo»).
Acá se prueba que el motor los resuelve solo, sin esos términos, y que lo que
se parece pero sí es el juego sigue entrando.

Los títulos son reales (sin vendedor ni enlace); catálogo y precios, inventados.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.app import create_radar_search, diversify_radar_results, init_db, list_radar_searches, run_radar_search
from server.radar.classify import assess_console_kit, classify_listing_item
from server.radar.matching import detect_completeness, detect_region, evaluate_match, normalize
from server.radar.model import MarketplaceListing
from server.radar.taxonomy import assign_objective


def listing(title: str, price: float = 15.0) -> MarketplaceListing:
    return MarketplaceListing(
        source_id="ebay-us",
        external_id=title,
        title=title,
        listing_url="https://example.invalid/item",
        price_amount=price,
        price_currency="USD",
        shipping_amount=4.0,
        shipping_currency="USD",
    )


def game_search_verdict(title: str):
    """Una búsqueda de juegos de PS3 como la de los veinte, sin términos excluidos."""
    return evaluate_match(
        listing(title),
        {"anyTerms": ["ps3", "playstation 3"], "excludeRegions": ["ntsc-j"], "maxItemPrice": 66},
        {},
        "game",
        platform="PS3",
        search_type="chase",
    )


def objective_of(title: str, platform: str = "ps3", search_type: str = "chase"):
    item = classify_listing_item(title)
    return assign_objective(
        title, search_type=search_type, item_kind=item.kind, item_confident=item.confident, platform=platform
    )


class NotTheGameTests(unittest.TestCase):
    """Un protector, una guía o un promocional no son el juego que nombran."""

    FIRST_RUN = (
        ("Protector For Demon's Souls Black Phantom Edition PS3", "accessory"),
        ("Protector For Demon's Souls Black Phantom Edition PS3 PAL", "accessory"),
        ("Protector For Ratchet & Clank A Crack in Time PS3 Collector's Edition PAL", "accessory"),
        ("Heavenly Sword Prima Official Strategy Game Guide PS3", "printed"),
        ("Puppeteer Der Puppenspieler Promo 3D Motion Ruler PS3 Gamescom 2014 Playstation", "merch"),
    )

    def test_the_first_run_pieces_are_confidently_not_a_game(self) -> None:
        for title, category in self.FIRST_RUN:
            with self.subTest(title=title):
                item = classify_listing_item(title)
                self.assertEqual((item.kind, item.confident, item.category), ("accessory", True, category))

    def test_a_game_search_blocks_them_without_excluded_terms(self) -> None:
        for title, _ in self.FIRST_RUN:
            with self.subTest(title=title):
                verdict = game_search_verdict(title)
                self.assertFalse(verdict.matched)
                self.assertTrue(any("esta búsqueda pide un juego" in blocker for blocker in verdict.blockers))

    def test_other_names_of_a_guide_are_printed_too(self) -> None:
        # Las otras formas, de los títulos reales de octubre.
        for title in (
            "Secret Of Mana Official Game Secrets Guide Prima Rusel DeMaria Nintendo SNES",
            "SNES Classic: Ultimate Reference Guide To The Secret of Mana - VERY GOOD",
            "God of War III Official Game Guide PS3",
            "Lot of 10 PSX Playstation Gamer's Guide Cheats, Codes & Strategies",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_listing_item(title).category, "printed")

    def test_other_promotional_pieces_are_merch(self) -> None:
        for title in (
            "NINTENDO DONKEY KONG COUNTRY TROPICAL FREEZE BANANA TOTE BAG PROMO SEALED",
            "Uncharted 4: A Thief's End Gold Pirate Sigil Coin Pre-Order Bonus Promo",
            "Banjo Kazooie Nintendo 64 N64 VHS Promo BLOCKBUSTER EXCLUSIVE RARE",
        ):
            with self.subTest(title=title):
                item = classify_listing_item(title)
                self.assertEqual((item.kind, item.confident, item.category), ("accessory", True, "merch"))

    def test_the_manual_and_the_cover_art_without_the_disc_are_printed(self) -> None:
        # Pasaban como CIB declarado; con la segunda copia prefiriendo una en
        # caja, habrían ocupado ese lugar.
        for title in (
            "Wipeout 3 - PlayStation 1 PS1 Manual and Case Back Artwork",
            "R4: Ridge Racer Type 4 - PlayStation 1 PS1 Manual and Case Back Artwork",
            "Authentic Playstation 2 PS2 Case Cover Art Only - Very Good - You Choose - B2G1",
            "Secret of Mana (SNES) Map only",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_listing_item(title).category, "printed")
        for title in (
            "God of War Ascension - Sony PlayStation 3 DAMAGED CASE ART. NO MANUAL.",
            "Tomb Raider 3 III PS1 W/ Manual - No Rear Art - PlayStation",
            "Wipeout 3 PS1 with case and manual",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_listing_item(title).kind, "game")

    def test_a_game_that_comes_with_a_protector_is_still_the_game(self) -> None:
        for title in (
            "Demon's Souls PS3 CIB with box protector",
            "Demon's Souls PS3 Complete w/ Protector",
            "Demon's Souls PS3 CIB + Box Protector",
            "Demon's Souls PS3 CIB with clear plastic box protector",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_listing_item(title).kind, "game")
                self.assertTrue(game_search_verdict(title).matched, game_search_verdict(title).blockers)

    def test_a_game_with_its_guide_or_a_promo_copy_is_still_the_game(self) -> None:
        for title in (
            "Heavenly Sword PS3 Complete with Strategy Guide",
            "Final Fantasy VII Rebirth (Game-Key Card) - Nintendo Switch 2 (No Promo)",
            "God of War III PS3 Promo Copy Not For Resale",
            "The Hitchhiker's Guide to the Galaxy",
            "Super Mario Land 2: 6 Golden Coins Game Boy Game Only Authentic",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_listing_item(title).kind, "game")


class NegatedCibTests(unittest.TestCase):
    """«No CIB» dice que no está completo, no que traiga caja y manual."""

    def test_no_cib_is_not_a_declared_cib(self) -> None:
        assignment = objective_of("God of War Ascension - Sony PlayStation 3 Tested and works No CIB")
        self.assertEqual((assignment.objective, assignment.status), ("game-loose", "undeclared"))
        self.assertEqual((assignment.box, assignment.manual), ("", ""))
        self.assertIn("no es CIB", assignment.note)

    def test_every_way_of_saying_it(self) -> None:
        for title in (
            "Killzone 2 PS3 not CIB",
            "Killzone 2 PS3 NON-CIB disc tested",
            "Killzone 2 PS3 Non CIB",
            "Killzone 2 PS3 not complete",
            "Killzone 2 PS3 not complete in box",
            "Killzone 2 PS3 new, not sealed",
            "Super Mario 64 N64 cartridge not boxed",
        ):
            with self.subTest(title=title):
                self.assertEqual(objective_of(title).objective, "game-loose")
                self.assertEqual(detect_completeness(normalize(title)), "")

    def test_what_the_rest_of_the_title_says_still_counts(self) -> None:
        with_case = objective_of("Killzone 2 PS3 not CIB, disc with case")
        self.assertEqual((with_case.objective, with_case.status), ("game-cib", "incomplete"))
        opened = objective_of("Killzone 2 PS3 Complete, not sealed")
        self.assertEqual((opened.objective, opened.status), ("game-cib", "declared"))
        self.assertEqual(detect_completeness(normalize("Killzone 2 PS3 Complete in box, not sealed")), "cib")

    def test_a_console_that_says_no_cib_has_no_retail_box(self) -> None:
        self.assertEqual(objective_of("PS3 Slim Console 120GB Tested No CIB", search_type="console").objective, "console-loose")
        self.assertNotEqual(assess_console_kit("PS3 Slim Console 120GB Tested Not CIB", "ps3").status, "cib")
        self.assertEqual(objective_of("PS3 Slim Console 120GB CIB", search_type="console").objective, "console-cib")


class PegiRegionTests(unittest.TestCase):
    def test_pegi_reads_as_pal(self) -> None:
        self.assertEqual(detect_region(normalize("MotorStorm: Pacific Rift (PS3) PEGI 16+ Racing: Off Road")), "pal")

    def test_the_card_asks_to_check_a_pal_copy(self) -> None:
        verdict = game_search_verdict("MotorStorm: Pacific Rift (PS3) PEGI 16+ Racing: Off Road FREE Shipping")
        self.assertTrue(verdict.matched, verdict.blockers)
        self.assertTrue(any(note.startswith("Edición PAL") for note in verdict.unverified), verdict.unverified)

    def test_an_american_rating_is_not_a_region(self) -> None:
        self.assertEqual(detect_region(normalize("Tomb Raider III PlayStation PS1 ESRB Teen Eidos")), "")


def shown(title: str, objective: str, score: int, target: str = "Killzone 2") -> dict:
    return {
        "title": title,
        "score": score,
        "section": "primary",
        "objective": {"id": objective},
        "titleTarget": {"query": f"{target} ps3", "name": target},
    }


class SecondCopyTests(unittest.TestCase):
    """Con la mejor copia suelta, la segunda es la mejor en caja que haya."""

    def test_a_cib_wins_the_second_place_over_another_loose_copy(self) -> None:
        results = [
            shown("Killzone 2 PS3 disc only", "game-loose", 90),
            shown("Killzone 2 PS3 PAL disc", "game-loose", 80),
            shown("Killzone 2 PS3 CIB complete", "game-cib", 60),
        ]
        visible, hidden = diversify_radar_results(results, 10)
        self.assertEqual(
            [item["title"] for item in visible], ["Killzone 2 PS3 disc only", "Killzone 2 PS3 CIB complete"]
        )
        self.assertEqual(hidden, 1)

    def test_a_disc_with_case_and_manual_is_boxed_by_its_objective(self) -> None:
        # «with case and manual» no dice «box»: lo que lo hace CIB es el
        # objetivo, que sabe que en un disco la «case» es la caja.
        results = [
            shown("Killzone 2 PS3 disc only", "game-loose", 90),
            shown("Killzone 2 PS3 PEGI disc", "game-loose", 80),
            shown("Killzone 2 PS3 with case and manual", "game-cib", 60),
        ]
        visible, _ = diversify_radar_results(results, 10)
        self.assertEqual(visible[1]["title"], "Killzone 2 PS3 with case and manual")

    def test_without_a_boxed_copy_the_first_distinct_one_still_goes(self) -> None:
        results = [
            shown("Killzone 2 PS3 disc only", "game-loose", 90),
            shown("Killzone 2 PS3 disc tested", "game-loose", 85),
            shown("Killzone 2 PS3 PAL disc", "game-loose", 80),
        ]
        visible, hidden = diversify_radar_results(results, 10)
        self.assertEqual([item["title"] for item in visible], ["Killzone 2 PS3 disc only", "Killzone 2 PS3 PAL disc"])
        self.assertEqual(hidden, 1)

    def test_a_boxed_first_copy_keeps_the_old_rule(self) -> None:
        results = [
            shown("Killzone 2 PS3 CIB complete", "game-cib", 90),
            shown("Killzone 2 PS3 disc only", "game-loose", 80),
            shown("Killzone 2 PS3 CIB complete PAL", "game-cib", 70),
        ]
        visible, _ = diversify_radar_results(results, 10)
        self.assertEqual(visible[1]["title"], "Killzone 2 PS3 disc only")

    def test_every_title_keeps_its_first_place_before_any_second(self) -> None:
        results = [
            shown("Killzone 2 PS3 disc only", "game-loose", 90),
            shown("Killzone 2 PS3 PAL disc", "game-loose", 85),
            shown("Puppeteer PS3 disc", "game-loose", 70, target="Puppeteer"),
            shown("Killzone 2 PS3 CIB complete", "game-cib", 60),
        ]
        visible, _ = diversify_radar_results(results, 2)
        self.assertEqual([item["title"] for item in visible], ["Killzone 2 PS3 disc only", "Puppeteer PS3 disc"])


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
        self.ebay_environment = "production"


def summary(item_id: str, title: str, price: str) -> dict:
    return {
        "itemId": item_id,
        "title": title,
        "itemWebUrl": f"https://www.ebay.com/itm/{item_id}",
        "price": {"value": price, "currency": "USD"},
        "condition": "Pre-owned",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"country": "US"},
        "image": {},
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "4.00", "currency": "USD"}}],
        "seller": {"username": "vendedor", "feedbackPercentage": "99.0"},
    }


class FirstRunReplayTests(unittest.TestCase):
    """La búsqueda de los veinte, sin «protector», «guide» ni «promo»."""

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.config = TestConfig(Path(temp_dir.name))
        catalog = self.config.static_dir / "data"
        catalog.mkdir(parents=True)
        (catalog / "console-games.json").write_text(json.dumps({"consolas": {}}), encoding="utf-8")
        (catalog / "consoles.json").write_text(json.dumps({"consolas": []}), encoding="utf-8")
        init_db(self.config)

    def test_the_pieces_no_longer_take_the_places_of_their_titles(self) -> None:
        targets = [
            {"query": "Demon's Souls ps3", "name": "Demon's Souls", "maxItemPrice": 36, "maxBoxedPrice": 36},
            {"query": "Heavenly Sword ps3", "name": "Heavenly Sword", "maxItemPrice": 18, "maxBoxedPrice": 18},
            {"query": "Puppeteer ps3", "name": "Puppeteer", "maxItemPrice": 66, "maxBoxedPrice": 66},
            {"query": "God of War Ascension ps3", "name": "God of War: Ascension", "maxItemPrice": 18, "maxBoxedPrice": 18},
        ]
        search = create_radar_search(
            self.config,
            {
                "name": "Juegos CIB PS3 de prueba",
                "platform": "PS3",
                "searchType": "chase",
                "entityType": "game",
                "entityConsoleId": "ps3",
                "searchQuery": "Demon's Souls ps3",
                "criteria": {"anyTerms": ["ps3", "playstation 3"], "excludeRegions": ["ntsc-j"], "titleTargets": targets},
            },
        )["search"]
        responses = [
            summary("v1|p1|0", "Protector For Demon's Souls Black Phantom Edition PS3", "6.00"),
            summary("v1|p2|0", "Protector For Demon's Souls Black Phantom Edition PS3 PAL", "6.00"),
            summary("v1|ds|0", "Demon's Souls PS3 Black Label CIB Complete", "30.00"),
            summary("v1|guide|0", "Heavenly Sword Prima Official Strategy Game Guide PS3", "8.00"),
            summary("v1|ruler|0", "Puppeteer Der Puppenspieler Promo 3D Motion Ruler PS3 Gamescom 2014 Playstation", "9.00"),
            summary("v1|asc|0", "God of War Ascension - Sony PlayStation 3 Tested and works No CIB", "9.00"),
        ]
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=responses):
            run_radar_search(self.config, search["id"])
        item = next(entry for entry in list_radar_searches(self.config)["items"] if entry["id"] == search["id"])
        titles = [result["title"] for result in item["results"]]
        self.assertEqual(
            sorted(titles),
            sorted(["Demon's Souls PS3 Black Label CIB Complete", "God of War Ascension - Sony PlayStation 3 Tested and works No CIB"]),
        )
        ascension = next(result for result in item["results"] if "Ascension" in result["title"])
        self.assertEqual(ascension["objective"]["id"], "game-loose")
        self.assertEqual(item["targetCoverage"]["uncovered"], ["Heavenly Sword", "Puppeteer"])


if __name__ == "__main__":
    unittest.main()
