"""Cinco objetivos canónicos por plataforma, y cada uno contra sus pares.

Lo que se prueba:

- la taxonomía: lotes, juegos loose, juegos CIB, consolas loose y consolas CIB,
  decididos por la pieza y por el formato de la plataforma, nunca por el nombre
  de una consola;
- la valuación: CIB contra CIB, loose contra loose, lotes por pieza; un CIB sin
  referencia CIB no se mide contra una copia suelta;
- un CIB declarado por el vendedor queda en «Requiere revisión» y nunca es
  «Ganga real» hasta que el dueño comprueba caja y manual;
- la comprobación de contenido se guarda en el radar (columna propia, migración
  idempotente), no en la colección.

Catálogo y precios inventados: nada depende de la colección real.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.app import (
    ApiError,
    connect_db,
    create_radar_search,
    init_db,
    list_radar_feed,
    list_radar_searches,
    run_radar_search,
    valuate_radar_match,
    verify_radar_listing,
    verify_radar_listing_contents,
)
from server.radar.classify import classify_listing_item
from server.radar.matching import evaluate_match
from server.radar.model import MarketplaceListing
from server.radar.taxonomy import OBJECTIVES, assign_objective, taxonomy_payload
from server.radar.valuation import PriceReference, effective_band


def objective_of(title: str, search_type: str, platform: str):
    item = classify_listing_item(title)
    return assign_objective(
        title, search_type=search_type, item_kind=item.kind, item_confident=item.confident, platform=platform
    )


class TaxonomyTests(unittest.TestCase):
    def test_there_are_five_canonical_objectives_in_a_fixed_order(self) -> None:
        self.assertEqual(
            [objective.id for objective in OBJECTIVES],
            ["game-lot", "game-loose", "game-cib", "console-loose", "console-cib"],
        )
        payload = taxonomy_payload()
        self.assertEqual([entry["id"] for entry in payload["objectives"] if entry["canonical"]], [o.id for o in OBJECTIVES])
        self.assertEqual(payload["objectives"][-1]["id"], "other")
        self.assertIn("unconfirmed-quantity", payload["sections"])
        self.assertIn("incomplete", payload["sections"])

    def test_games_are_loose_cib_or_incomplete_by_box_and_manual(self) -> None:
        cases = [
            ("Super Mario 64 N64 Cartridge Tested", "n64", "game-loose", "undeclared"),
            ("Super Mario 64 N64 Cartridge Only", "n64", "game-loose", "loose"),
            ("Super Mario 64 N64 CIB Complete in Box", "n64", "game-cib", "declared"),
            ("Kingdom Hearts PS2 Factory Sealed", "ps2", "game-cib", "declared"),
            ("Metal Gear Solid 2 PS2 with case and manual", "ps2", "game-cib", "declared"),
            ("Super Mario 64 N64 with box no manual", "n64", "game-cib", "incomplete"),
            ("Super Mario 64 N64 w/ Manual", "n64", "game-cib", "incomplete"),
            ("Driver 2 PlayStation 1 PS1 No Manual", "ps1", "game-loose", "loose"),
        ]
        for title, platform, objective, status in cases:
            with self.subTest(title=title):
                assignment = objective_of(title, "chase", platform)
                self.assertEqual((assignment.objective, assignment.status), (objective, status))

    def test_a_case_is_the_box_of_a_disc_and_a_dust_case_on_a_cartridge(self) -> None:
        """«w/ case» en un cartucho retro es un estuche; en un disco, la caja retail."""
        self.assertEqual(objective_of("Pokemon Blue Game Boy Color game w/ case", "chase", "gbc").objective, "game-loose")
        disc = objective_of("Metal Gear Solid 2 PS2 with case", "chase", "ps2")
        self.assertEqual((disc.objective, disc.status), ("game-cib", "incomplete"))

    def test_inserts_are_never_assumed(self) -> None:
        plain = objective_of("Earthbound SNES CIB Complete in Box", "chase", "snes")
        self.assertFalse(plain.inserts)
        self.assertIn("los inserts no se dan por incluidos", plain.note)
        with_inserts = objective_of("Earthbound SNES CIB with inserts and poster", "chase", "snes")
        self.assertTrue(with_inserts.inserts)
        self.assertIn("inserts que anuncia", with_inserts.note)

    def test_consoles_are_cib_only_with_their_retail_box(self) -> None:
        cases = [
            ("Nintendo 64 N64 Console W/ OEM Controller, Jumper Pak, All Cords", "console-loose"),
            ("Nintendo 64 Console Only Tested", "console-loose"),
            ("Nintendo 64 Console Complete in Box", "console-cib"),
            ("SNES Super Nintendo Console Bundle 2 Controllers In Original Box", "console-cib"),
            ("Nintendo 64 Console with repro box", "console-loose"),
            ("SNES Console in Reproduction Box Tested", "console-loose"),
        ]
        for title, objective in cases:
            with self.subTest(title=title):
                self.assertEqual(objective_of(title, "console", "n64").objective, objective)
        self.assertTrue(objective_of("Nintendo 64 Console with repro box", "console", "n64").repro_packaging)

    def test_lots_and_accessories(self) -> None:
        self.assertEqual(objective_of("Lot of 12 PS1 Games", "lot", "ps1").objective, "game-lot")
        self.assertEqual(objective_of("Lot of 12 PS1 Games", "chase", "ps1").objective, "game-lot")
        self.assertEqual(objective_of("PS2 Official Memory Card", "discovery", "ps2").objective, "other")

    def test_the_rule_is_by_format_not_by_console(self) -> None:
        """La misma frase decide igual en cualquier plataforma del mismo formato."""
        for platform in ("nes", "snes", "n64", "genesis", "gb", "gbc"):
            self.assertEqual(objective_of("Some Game cartridge with case", "chase", platform).objective, "game-loose")
        for platform in ("ps1", "ps2", "ps3", "3ds"):
            self.assertEqual(objective_of("Some Game with case", "chase", platform).status, "incomplete")


class MatcherTests(unittest.TestCase):
    def listing(self, title: str, price: float = 30.0) -> MarketplaceListing:
        return MarketplaceListing(
            source_id="ebay-us", external_id=title, title=title, listing_url="https://example.invalid/item",
            price_amount=price, price_currency="USD", shipping_amount=5.0, shipping_currency="USD",
        )

    def test_the_matcher_records_the_objective(self) -> None:
        verdict = evaluate_match(
            self.listing("Zelda Ocarina of Time N64 CIB Complete"), {}, platform="N64", search_type="chase"
        )
        self.assertEqual(verdict.assessment["objective"]["id"], "game-cib")

    def test_a_repro_box_does_not_make_the_cartridge_a_reproduction(self) -> None:
        verdict = evaluate_match(
            self.listing("Zelda Ocarina of Time N64 Authentic Cartridge with Repro Box"),
            {"originalParts": "required"},
            platform="N64",
            search_type="chase",
        )
        self.assertTrue(verdict.matched, verdict.blockers)
        self.assertEqual(verdict.assessment["objective"]["id"], "game-loose")

    def test_cib_is_compared_with_cib_and_never_falls_back_to_loose(self) -> None:
        listing = self.listing("Final Fantasy VII PS1 Complete in Box")
        verdict = evaluate_match(listing, {}, platform="PS1", search_type="chase")
        loose = PriceReference("ff7", "retail", 40.0, completeness="loose", observed_at="2026-10-01")
        card = valuate_radar_match(listing, verdict, {}, [loose], "")
        self.assertIsNone(card.benchmark)
        cib = PriceReference("ff7", "pricecharting", 90.0, completeness="cib", observed_at="2026-10-01")
        card = valuate_radar_match(listing, verdict, {}, [loose, cib], "")
        self.assertEqual(card.benchmark["completeness"], "cib")
        self.assertEqual(card.benchmark["value"], 90.0)

    def test_loose_is_compared_with_loose(self) -> None:
        listing = self.listing("Final Fantasy VII PS1 Game Disc Only")
        verdict = evaluate_match(listing, {}, platform="PS1", search_type="chase")
        refs = [
            PriceReference("ff7", "retail", 40.0, completeness="loose", observed_at="2026-10-01"),
            PriceReference("ff7", "pricecharting", 90.0, completeness="cib", observed_at="2026-10-01"),
        ]
        self.assertEqual(valuate_radar_match(listing, verdict, {}, refs, "").benchmark["value"], 40.0)

    def test_a_declared_cib_is_reviewed_and_never_a_real_bargain(self) -> None:
        listing = self.listing("Final Fantasy VII PS1 Complete in Box", price=20.0)
        verdict = evaluate_match(listing, {}, platform="PS1", search_type="chase")
        cib = PriceReference("ff7", "pricecharting", 90.0, completeness="cib", observed_at="2026-10-01")
        card = valuate_radar_match(listing, verdict, {}, [cib], "")
        valuation = card.to_dict()
        self.assertEqual(card.price_band, "ganga")
        self.assertEqual(card.band, "revisar")
        self.assertIn("cib-unverified", card.review_keys)
        # Verificar sólo la disponibilidad no alcanza.
        self.assertEqual(effective_band(card.band, valuation, True, False), "revisar")
        # Comprobar caja y manual devuelve la banda del precio, sin ganga hasta verificar.
        self.assertEqual(effective_band(card.band, valuation, False, True), "buena")
        self.assertEqual(effective_band(card.band, valuation, True, True), "ganga")

    def test_a_copy_with_box_and_no_manual_has_no_verdict(self) -> None:
        listing = self.listing("Final Fantasy VII PS1 with case no manual", price=10.0)
        verdict = evaluate_match(listing, {}, platform="PS1", search_type="chase")
        refs = [
            PriceReference("ff7", "retail", 40.0, completeness="loose", observed_at="2026-10-01"),
            PriceReference("ff7", "pricecharting", 90.0, completeness="cib", observed_at="2026-10-01"),
        ]
        card = valuate_radar_match(listing, verdict, {}, refs, "")
        self.assertEqual(card.section, "incomplete")
        self.assertIsNone(card.benchmark)
        self.assertEqual(card.band, "sin-referencia")

    def test_other_review_reasons_are_not_cleared_by_checking_the_box(self) -> None:
        valuation = {
            "priceBand": "ganga",
            "gatedBand": "buena",
            "reviewKeys": ["cib-unverified", "authenticity-unknown"],
            "bargainBlockers": [{"key": "availability"}, {"key": "contents"}],
        }
        self.assertEqual(effective_band("revisar", valuation, True, True), "revisar")


# --------------------------------------------------------------------------- #
# Integración                                                                   #
# --------------------------------------------------------------------------- #

CATALOG_CONSOLES = {
    "consolas": [
        {
            "id": "consola-x",
            "nombre": "Consola X",
            "precioPriceChart": 100,
            "precioCIB": 220,
            "precioNotas": {"verificadoEn": "2026-10-01"},
        }
    ]
}
CATALOG_GAMES = {
    "byConsole": {
        "ps1": {
            "juegosCatalogo": [
                {
                    "id": "ff7",
                    "nombre": "Final Fantasy VII",
                    "priceRange": {"low": 20, "mid": 30, "high": 45, "notes": "consultados el 2026-10-01"},
                }
            ]
        }
    }
}


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
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "5.00", "currency": "USD"}}],
        "seller": {"username": "vendedor", "feedbackPercentage": "99.0"},
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
        self.ebay_environment = "production"


class RunTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        catalog = self.config.static_dir / "data"
        catalog.mkdir(parents=True)
        (catalog / "consoles.json").write_text(json.dumps(CATALOG_CONSOLES), encoding="utf-8")
        (catalog / "console-games.json").write_text(json.dumps(CATALOG_GAMES), encoding="utf-8")
        init_db(self.config)

    def run_with(self, search: dict, responses: list[dict]) -> dict:
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=responses):
            run_radar_search(self.config, search["id"])
        return self.find(search["id"])

    def find(self, search_id: str) -> dict:
        return next(item for item in list_radar_searches(self.config)["items"] if item["id"] == search_id)

    def by_title(self, item: dict) -> dict[str, dict]:
        return {result["title"]: result for result in item["results"]}


class ConsoleObjectiveRunTests(RunTestCase):
    def test_a_console_search_feeds_both_console_objectives_each_against_its_own_reference(self) -> None:
        search = create_radar_search(
            self.config,
            {
                "name": "Consola X — consola",
                "platform": "Consola X",
                "searchType": "console",
                "entityType": "console",
                "entityId": "consola-x",
                "searchQuery": "consola x console",
            },
        )["search"]
        item = self.run_with(
            search,
            [
                summary("loose", "Consola X Console Controller Cables Tested", "90.00"),
                summary("cib", "Consola X Console Complete in Box Tested", "200.00"),
                summary("repro", "Consola X Console with Repro Box Tested", "120.00"),
            ],
        )
        results = self.by_title(item)
        loose = results["Consola X Console Controller Cables Tested"]
        cib = results["Consola X Console Complete in Box Tested"]
        repro = results["Consola X Console with Repro Box Tested"]
        self.assertEqual(loose["objective"]["id"], "console-loose")
        self.assertEqual(loose["valuation"]["benchmark"]["value"], 100)
        self.assertEqual(cib["objective"]["id"], "console-cib")
        self.assertEqual(cib["valuation"]["benchmark"]["value"], 220)
        self.assertEqual(cib["band"], "revisar")
        self.assertEqual(repro["objective"]["id"], "console-loose")
        self.assertEqual(repro["valuation"]["benchmark"]["completeness"], "loose")
        self.assertTrue(any("Caja repro" in note for note in repro["valuation"]["verdictNotes"]))

    def test_the_payload_carries_the_taxonomy_for_the_screen(self) -> None:
        payload = list_radar_searches(self.config)
        self.assertEqual(
            [entry["id"] for entry in payload["objectives"]],
            ["game-lot", "game-loose", "game-cib", "console-loose", "console-cib", "other"],
        )
        self.assertEqual(payload["sections"]["incomplete"]["label"], "No llega a CIB")


class GameObjectiveRunTests(RunTestCase):
    def wishlist(self) -> dict:
        return create_radar_search(
            self.config,
            {
                "name": "Deseados PS1",
                "platform": "PS1",
                "searchType": "chase",
                "entityType": "game",
                "entityConsoleId": "ps1",
                "searchQuery": "final fantasy vii ps1",
                "criteria": {
                    "anyTerms": ["ps1"],
                    "resultLimit": 25,
                    "titleTargets": [
                        {"query": "final fantasy vii ps1", "gameId": "ff7", "name": "Final Fantasy VII", "maxItemPrice": 100}
                    ],
                },
            },
        )["search"]

    def test_cib_copies_are_compared_with_cib_peers_and_loose_with_loose(self) -> None:
        cibs = [summary(f"cib{i}", f"Final Fantasy VII PS1 Complete in Box Black Label {i}", f"{60 + i * 4}.00") for i in range(4)]
        louse = [summary(f"loose{i}", f"Final Fantasy VII PS1 Game Disc Only {i}", f"{18 + i}.00") for i in range(4)]
        item = self.run_with(self.wishlist(), [*cibs, *louse])
        every = item["results"]
        cib = next(result for result in every if result["objective"]["id"] == "game-cib")
        loose = next(result for result in every if result["objective"]["id"] == "game-loose")
        # Mediana de los cuatro CIB pedidos, no la referencia suelta del catálogo.
        self.assertEqual(cib["valuation"]["benchmark"]["completeness"], "cib")
        self.assertEqual(cib["valuation"]["benchmark"]["source"], "peer-listings")
        self.assertEqual(cib["valuation"]["benchmark"]["value"], 66.0)
        self.assertEqual(loose["valuation"]["benchmark"]["completeness"], "loose")
        self.assertEqual(loose["valuation"]["benchmark"]["source"], "retail")

    def test_checking_box_and_manual_is_stored_and_lifts_the_review(self) -> None:
        cheap = summary("cheap", "Final Fantasy VII PS1 Complete in Box Black Label", "40.00")
        peers = [summary(f"cib{i}", f"Final Fantasy VII PS1 Complete in Box {i}", f"{90 + i}.00") for i in range(4)]
        item = self.run_with(self.wishlist(), [cheap, *peers])
        result = self.by_title(item)["Final Fantasy VII PS1 Complete in Box Black Label"]
        self.assertEqual(result["band"], "revisar")
        self.assertIsNone(result["verification"]["contentsVerifiedAt"])

        verify_radar_listing_contents(self.config, result["id"])
        checked = self.by_title(self.find(item["id"]))["Final Fantasy VII PS1 Complete in Box Black Label"]
        self.assertIsNotNone(checked["verification"]["contentsVerifiedAt"])
        self.assertEqual(checked["band"], "buena")
        verify_radar_listing(self.config, result["id"])
        # La referencia son pedidos activos: aun comprobado y verificado, no es «Ganga real».
        final = self.by_title(self.find(item["id"]))["Final Fantasy VII PS1 Complete in Box Black Label"]
        self.assertEqual(final["band"], "buena")

    def test_an_incomplete_copy_goes_aside_and_out_of_the_feed(self) -> None:
        item = self.run_with(
            self.wishlist(),
            [
                summary("inc", "Final Fantasy VII PS1 with case no manual", "12.00"),
                summary("ok", "Final Fantasy VII PS1 Game Disc Only", "15.00"),
            ],
        )
        sections = {result["title"]: result["section"] for result in item["results"]}
        self.assertEqual(sections["Final Fantasy VII PS1 with case no manual"], "incomplete")
        self.assertEqual(item["results"][-1]["section"], "incomplete", "lo que no llega a CIB va al final")
        feed = [entry["title"] for entry in list_radar_feed(self.config)["items"]]
        self.assertNotIn("Final Fantasy VII PS1 with case no manual", feed)

    def test_rows_scored_before_the_taxonomy_get_their_objective_when_read(self) -> None:
        item = self.run_with(self.wishlist(), [summary("old", "Final Fantasy VII PS1 CIB Complete", "40.00")])
        with connect_db(self.config) as conn:
            conn.execute("UPDATE radar_search_matches SET valuation_json = '{}'")
        (result,) = self.find(item["id"])["results"]
        self.assertEqual(result["objective"]["id"], "game-cib")
        self.assertEqual(result["searchName"], "Deseados PS1")


class ContentsVerificationStorageTests(RunTestCase):
    def test_the_column_migration_is_idempotent(self) -> None:
        init_db(self.config)
        init_db(self.config)
        with connect_db(self.config) as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(radar_listings)").fetchall()}
        self.assertIn("contents_verified_at", columns)

    def test_checking_a_missing_listing_is_an_error(self) -> None:
        with self.assertRaises(ApiError):
            verify_radar_listing_contents(self.config, "ebay-us-no-existe")


if __name__ == "__main__":
    unittest.main()
