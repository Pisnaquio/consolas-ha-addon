"""Que «ganga», «lote» y «original» vuelvan a significar algo.

La auditoría del 2026-10-12 sobre el radar de producción encontró 496
publicaciones únicas: 212 «ganga» (43%), ninguna marcada para verificar aunque
las 496 tenían la disponibilidad sin confirmar, cartuchos «compatibles» y
«game card» entre los deseados, pines, libros y publicidades como juegos,
repuestos y clones como consolas, y lotes sorpresa, mezclados o de dos juegos
en búsquedas de quince.

Lo que se prueba:

- la tabla de `fixtures/radar-quality-2026-10.json`: cada título citado como
  problema queda afuera o bloqueado, y los controles positivos siguen entrando;
- una consola sola se compara contra una lista para usar sumando lo que le
  falta, y no le gana a un bundle completo;
- un lote sin cantidad confiable no tiene veredicto y va aparte;
- «Ganga real» exige todo confirmado, y la verificación del dueño caduca;
- una búsqueda de varios títulos muestra primero un resultado por título.

Los datos de catálogo y los precios son inventados: nada depende del estado
real de la colección.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from server.app import (
    connect_db,
    create_radar_search,
    diversify_radar_results,
    init_db,
    list_radar_feed,
    list_radar_searches,
    run_radar_search,
    valuate_radar_match,
    verify_radar_listing,
)
from server.radar.classify import assess_authenticity, assess_console_kit, classify_listing_item
from server.radar.matching import evaluate_match
from server.radar.model import MarketplaceListing
from server.radar.platforms import platform_key, platform_payload, platforms_in_text
from server.radar.valuation import PriceReference, effective_band


FIXTURE = Path(__file__).parent / "fixtures" / "radar-quality-2026-10.json"

# Las tres formas de búsqueda del portafolio retro, con los criterios que
# importan para estas reglas. Ningún término excluido: lo que se prueba es el
# motor, no la lista de exclusiones de cada búsqueda.
SEARCHES = {
    "game": {
        "search_type": "chase",
        "expected_kind": "game",
        "criteria": {"originalParts": "required", "excludeRegions": ["ntsc-j"], "maxItemPrice": 100},
    },
    "console": {
        "search_type": "console",
        "expected_kind": "console",
        "criteria": {
            "originalParts": "preferred",
            "tested": "preferred",
            "excludeRegions": ["ntsc-j"],
            "maxItemPrice": 300,
        },
    },
    "lot": {
        "search_type": "lot",
        "expected_kind": "",
        "criteria": {"originalParts": "required", "excludeRegions": ["ntsc-j"], "maxPricePerPiece": 10},
    },
}


def cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def make_listing(title: str, price: float, shipping: float | None = 5.0) -> MarketplaceListing:
    return MarketplaceListing(
        source_id="ebay-us",
        external_id=title,
        title=title,
        listing_url="https://example.invalid/item",
        price_amount=price,
        price_currency="USD",
        shipping_amount=shipping,
        shipping_currency="USD" if shipping is not None else "",
    )


def judge(case: dict, price: float | None = None):
    shape = SEARCHES[case["search"]]
    criteria = dict(shape["criteria"])
    if case.get("minLotSize"):
        criteria["minLotSize"] = case["minLotSize"]
    listing = make_listing(case["title"], price if price is not None else (30.0 if case["search"] != "lot" else 20.0))
    verdict = evaluate_match(
        listing,
        criteria,
        {},
        shape["expected_kind"],
        rejects_accessories=case["search"] == "lot",
        platform=case["platform"],
        search_type=shape["search_type"],
    )
    reference = PriceReference("ref", "pricecharting", 100.0, observed_at="2026-10-01", confidence=0.8)
    card = (
        valuate_radar_match(
            listing, verdict, criteria, [reference], shape["expected_kind"], search_type=shape["search_type"]
        )
        if verdict.matched
        else None
    )
    return verdict, card


class FixtureTests(unittest.TestCase):
    def test_the_fixture_carries_no_listing_identity(self) -> None:
        raw = FIXTURE.read_text(encoding="utf-8")
        for forbidden in ("ebay.com/itm", "itemId", "seller", "http"):
            self.assertNotIn(forbidden, raw)

    def test_every_case_gets_the_verdict_the_audit_asked_for(self) -> None:
        for case in cases():
            with self.subTest(title=case["title"], why=case["why"]):
                verdict, card = judge(case)
                if case["expect"] == "reject":
                    self.assertFalse(verdict.matched, verdict.reasons)
                    continue
                self.assertTrue(verdict.matched, verdict.blockers)
                if case["expect"] == "review":
                    self.assertEqual(card.band, "revisar", card.verdict_notes)
                elif case["expect"] == "unconfirmed":
                    self.assertEqual(card.section, "unconfirmed-quantity")
                    self.assertEqual(card.band, "sin-referencia")
                else:
                    self.assertEqual(card.section, "primary")
                    self.assertNotEqual(card.band, "revisar", card.verdict_notes)

    def test_nothing_is_a_real_bargain_before_someone_opens_the_listing(self) -> None:
        """Ninguna fuente confirma disponibilidad: la ganga se afirma al verificar."""
        for case in cases():
            verdict, card = judge(case, price=5.0)
            if card is not None:
                with self.subTest(title=case["title"]):
                    self.assertNotEqual(card.band, "ganga")


class AuthenticityTests(unittest.TestCase):
    def test_a_negation_is_not_a_confession(self) -> None:
        self.assertEqual(assess_authenticity("Zelda Ocarina of Time N64 Not a Repro", "n64").status, "declared")
        verdict = evaluate_match(
            make_listing("Zelda Ocarina of Time N64 Not a Repro", 30.0),
            {"originalParts": "required"},
            platform="N64",
            search_type="chase",
        )
        self.assertTrue(verdict.matched, verdict.blockers)

    def test_a_game_card_is_the_official_name_on_a_3ds(self) -> None:
        self.assertEqual(assess_authenticity("Pokemon Y Nintendo 3DS Game Card Only", "3ds").status, "")
        verdict = evaluate_match(
            make_listing("Pokemon Y Nintendo 3DS Game Card Only", 20.0),
            {"originalParts": "required"},
            platform="3DS",
            search_type="chase",
        )
        self.assertTrue(verdict.matched, verdict.blockers)

    def test_an_original_japanese_cartridge_is_not_a_reproduction(self) -> None:
        """Cualquier región es válida si la búsqueda no la excluye; se avisa cómo se juega."""
        verdict = evaluate_match(
            make_listing("Kirby Super Star Super Famicom Japan Cartridge", 20.0),
            {"originalParts": "required"},
            platform="SNES",
            search_type="chase",
        )
        self.assertTrue(verdict.matched, verdict.blockers)
        self.assertTrue(any("NTSC-J" in item and "idioma" in item for item in verdict.unverified))

    def test_a_disc_game_is_not_asked_for_a_cartridge_label(self) -> None:
        verdict, card = judge({"search": "game", "platform": "PS1", "title": "Final Fantasy VII PS1 Game Disc"})
        self.assertNotEqual(card.band, "revisar")
        self.assertNotIn("authenticity", {item["key"] for item in card.bargain_blockers})


class ConsoleCompletenessTests(unittest.TestCase):
    READY = "Nintendo 64 N64 Console W/ OEM Controller, Jumper Pak, All Cords. Tested/Working"
    ALONE = "Nintendo 64 Console Only Tested Working"

    def judge_console(self, title: str, price: float):
        return judge({"search": "console", "platform": "N64", "title": title}, price=price)

    def test_the_kit_is_read_from_the_title(self) -> None:
        self.assertEqual(assess_console_kit(self.READY, "n64").status, "ready")
        alone = assess_console_kit(self.ALONE, "n64")
        self.assertEqual(alone.status, "console-only")
        self.assertEqual(set(alone.missing), {"controller", "cables", "jumper-pak"})
        self.assertEqual(assess_console_kit("Game Boy DMG-01 missing battery cover", "gb").missing, ("battery-cover",))

    def test_a_console_alone_is_compared_with_what_it_costs_to_finish_it(self) -> None:
        _, alone = self.judge_console(self.ALONE, 60.0)
        self.assertEqual(alone.completion["total"], 45.0)
        # 60 + 5 de envío + 45 para completarla, contra 100 de referencia.
        self.assertAlmostEqual(alone.ratio, 1.10, places=2)
        self.assertTrue(any("Le falta" in note for note in alone.verdict_notes))

    def test_a_ready_bundle_outranks_a_cheaper_console_alone(self) -> None:
        _, alone = self.judge_console(self.ALONE, 60.0)
        _, ready = self.judge_console(self.READY, 85.0)
        self.assertGreater(ready.score, alone.score)
        self.assertLess(ready.ratio, alone.ratio)

    def test_ps2_powering_on_is_not_a_working_reader(self) -> None:
        verdict, card = judge(
            {"search": "console", "platform": "PS2", "title": "Sony PlayStation 2 Slim SCPH-79001 Console Powers On"}
        )
        self.assertIn("Sólo dice que enciende: falta confirmar que lea CD y DVD", verdict.unverified)
        self.assertIn("reader", {item["key"] for item in card.bargain_blockers})

    def test_a_ps2_that_says_it_reads_both_is_not_asked_again(self) -> None:
        verdict, _ = judge(
            {"search": "console", "platform": "PS2", "title": "PS2 Slim Console Tested Reads CD and DVD Controller Cables"}
        )
        self.assertFalse(any("lea CD y DVD" in item for item in verdict.unverified))

    def test_declared_damage_is_visible_and_never_a_bargain(self) -> None:
        verdict, card = judge(
            {"search": "console", "platform": "Game Boy", "title": "Nintendo Game Boy DMG-01 Tested Working LCD Lines"},
            price=10.0,
        )
        self.assertEqual(card.band, "revisar")
        self.assertTrue(any("pantalla con líneas" in note for note in card.verdict_notes))


class LotSemanticsTests(unittest.TestCase):
    def test_a_lot_below_the_minimum_says_how_many_it_declares(self) -> None:
        verdict, _ = judge(
            {"search": "lot", "platform": "PS2", "minLotSize": 15,
             "title": "PS2 2 Game Bundle God of War Shadow of the Colossus"}
        )
        self.assertIn("Declara 2 pieza(s) y la búsqueda pide al menos 15", verdict.blockers)

    def test_a_lot_without_a_count_has_no_per_piece_price_and_no_verdict(self) -> None:
        verdict, card = judge(
            {"search": "lot", "platform": "PS2", "minLotSize": 15, "title": "PS2 Game Bundle Kingdom Hearts Final Fantasy X"},
            price=4.0,
        )
        self.assertTrue(verdict.matched)
        self.assertIsNone(card.benchmark)
        self.assertEqual(card.band, "sin-referencia")
        self.assertEqual(card.section, "unconfirmed-quantity")

    def test_a_count_shared_with_controllers_is_not_a_count_of_games(self) -> None:
        verdict, card = judge(
            {"search": "lot", "platform": "N64", "minLotSize": 6, "title": "Lot of 12 Items: N64 Games, Controllers, Memory Cards"}
        )
        self.assertTrue(verdict.matched)
        self.assertEqual(card.section, "unconfirmed-quantity")

    def test_a_lot_that_mixes_one_other_platform_goes_aside(self) -> None:
        _, card = judge({"search": "lot", "platform": "PS1", "minLotSize": 10, "title": "Lot of 12 PS1 PS2 Games"})
        self.assertEqual(card.section, "unconfirmed-quantity")

    def test_works_on_another_console_is_not_a_mix(self) -> None:
        _, card = judge(
            {"search": "lot", "platform": "PS1", "minLotSize": 10, "title": "PS1 Lot of 12 Games Works on PS2"}
        )
        self.assertEqual(card.section, "primary")

    def test_filler_is_worth_less_per_piece(self) -> None:
        _, filler = judge(
            {"search": "lot", "platform": "PS2", "minLotSize": 15, "title": "Lot of 16 PS2 Sports Games Madden NBA Tested"}
        )
        _, plain = judge({"search": "lot", "platform": "PS2", "minLotSize": 15, "title": "Lot of 16 PS2 Games Tested"})
        self.assertEqual(plain.benchmark["value"], 160.0)
        self.assertEqual(filler.benchmark["value"], 80.0)
        self.assertIn("lot-filler", {item["key"] for item in filler.bargain_blockers})

    def test_a_lot_is_never_measured_against_the_total_of_other_lots(self) -> None:
        listing = make_listing("Lot of 20 PS2 Games", 30.0)
        verdict = evaluate_match(listing, {}, platform="PS2", search_type="lot", rejects_accessories=True)
        peers = PriceReference("ref", "peer-listings", 25.0, confidence=0.5)
        card = valuate_radar_match(listing, verdict, {}, [peers], "", search_type="lot")
        self.assertIsNone(card.benchmark)


class BandTests(unittest.TestCase):
    def test_only_an_unverified_listing_is_lifted_to_bargain_by_verifying_it(self) -> None:
        only_availability = {"priceBand": "ganga", "bargainBlockers": [{"key": "availability"}]}
        more = {"priceBand": "ganga", "bargainBlockers": [{"key": "availability"}, {"key": "shipping"}]}
        self.assertEqual(effective_band("buena", only_availability, True), "ganga")
        self.assertEqual(effective_band("buena", only_availability, False), "buena")
        self.assertEqual(effective_band("buena", more, True), "buena")
        self.assertEqual(effective_band("revisar", only_availability, True), "revisar")

    def test_a_reference_made_of_asking_prices_cannot_call_a_bargain(self) -> None:
        listing = make_listing("Final Fantasy VII PS1 Game Disc", 20.0)
        verdict = evaluate_match(listing, {}, platform="PS1", search_type="chase")
        peers = PriceReference("ref", "peer-listings", 60.0, confidence=0.5)
        card = valuate_radar_match(listing, verdict, {}, [peers], "")
        self.assertEqual(card.price_band, "ganga")
        self.assertEqual(card.band, "buena")
        self.assertIn("benchmark", {item["key"] for item in card.bargain_blockers})


class PlatformTests(unittest.TestCase):
    def test_spellings_of_the_same_platform_share_a_key(self) -> None:
        self.assertEqual(platform_key("N64"), platform_key("Nintendo 64"))
        self.assertEqual(platform_key("Game Boy Color"), "gbc")
        self.assertNotEqual(platform_key("Game Boy"), platform_key("Game Boy Color"))
        self.assertEqual(platform_payload("PS1")["label"], "PlayStation 1")
        self.assertEqual(platform_payload("Consola rara")["label"], "Consola rara")

    def test_a_longer_name_is_not_counted_twice(self) -> None:
        self.assertEqual(platforms_in_text("Super Nintendo SNES Game Boy Color lot"), ["snes", "gbc"])

    def test_a_link_cable_is_an_accessory_even_naming_the_console(self) -> None:
        item = classify_listing_item("Genuine Nintendo Game Link Cable for Game Boy Console")
        self.assertEqual((item.kind, item.category), ("accessory", "accessory"))


# --------------------------------------------------------------------------- #
# Integración: lo que la búsqueda guarda y lo que la pantalla lee              #
# --------------------------------------------------------------------------- #

CATALOG_GAMES = {
    "byConsole": {
        "ps1": {
            "juegosCatalogo": [
                {
                    "id": "ff7",
                    "nombre": "Final Fantasy VII",
                    "priceRange": {"low": 25, "mid": 40, "high": 60, "notes": "consultados el 2026-10-01"},
                }
            ]
        }
    }
}
CATALOG_CONSOLES = {
    "consolas": [
        {
            "id": "n64",
            "nombre": "Nintendo 64",
            "precioPriceChart": 100,
            "precioNotas": {"verificadoEn": "2026-10-01"},
        }
    ]
}


def summary(item_id: str, title: str, price: str, shipping: str = "5.00") -> dict:
    return {
        "itemId": item_id,
        "title": title,
        "itemWebUrl": f"https://www.ebay.com/itm/{item_id}",
        "price": {"value": price, "currency": "USD"},
        "condition": "Pre-owned",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"country": "US"},
        "image": {},
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": shipping, "currency": "USD"}}],
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


class RadarRunTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        catalog = self.config.static_dir / "data"
        catalog.mkdir(parents=True)
        (catalog / "console-games.json").write_text(json.dumps(CATALOG_GAMES), encoding="utf-8")
        (catalog / "consoles.json").write_text(json.dumps(CATALOG_CONSOLES), encoding="utf-8")
        init_db(self.config)

    def create(self, payload: dict) -> dict:
        return create_radar_search(self.config, payload)["search"]

    def run_with(self, search: dict, responses: list[dict]) -> None:
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=responses):
            run_radar_search(self.config, search["id"])

    def find(self, search_id: str) -> dict:
        return next(item for item in list_radar_searches(self.config)["items"] if item["id"] == search_id)


class VerificationTests(RadarRunTestCase):
    def wishlist(self) -> dict:
        return self.create(
            {
                "name": "Deseados PS1",
                "platform": "PS1",
                "searchType": "chase",
                "entityType": "game",
                "entityConsoleId": "ps1",
                "searchQuery": "final fantasy vii ps1",
                "criteria": {
                    "anyTerms": ["ps1"],
                    "titleTargets": [
                        {"query": "final fantasy vii ps1", "gameId": "ff7", "name": "Final Fantasy VII", "maxItemPrice": 60}
                    ],
                },
            }
        )

    def test_an_unknown_availability_is_shown_as_pending(self) -> None:
        search = self.wishlist()
        self.run_with(search, [summary("v1|ff7|0", "Final Fantasy VII PS1 Video Game Disc Tested", "20.00")])
        result = self.find(search["id"])["results"][0]
        self.assertEqual(result["availability"], "unknown")
        self.assertTrue(result["requiresVerification"])
        self.assertEqual(result["verification"]["state"], "pending")
        self.assertFalse(result["verification"]["sourceChecksAvailability"])

    def test_verifying_lifts_a_bargain_and_the_verification_expires(self) -> None:
        search = self.wishlist()
        self.run_with(search, [summary("v1|ff7|0", "Final Fantasy VII PS1 Video Game Disc Tested", "20.00")])
        before = self.find(search["id"])["results"][0]
        self.assertEqual(before["valuation"]["priceBand"], "ganga")
        self.assertEqual(before["band"], "buena")
        self.assertEqual([item["key"] for item in before["valuation"]["bargainBlockers"]], ["availability"])

        verify_radar_listing(self.config, before["id"])
        verified = self.find(search["id"])["results"][0]
        self.assertEqual(verified["verification"]["state"], "verified")
        self.assertFalse(verified["requiresVerification"])
        self.assertEqual(verified["band"], "ganga")
        self.assertIsNotNone(verified["verification"]["expiresAt"])

        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        with connect_db(self.config) as conn:
            conn.execute("UPDATE radar_listings SET verified_at = ? WHERE id = ?", (old, before["id"]))
        expired = self.find(search["id"])["results"][0]
        self.assertEqual(expired["verification"]["state"], "expired")
        self.assertTrue(expired["requiresVerification"])
        self.assertEqual(expired["band"], "buena")

    def test_a_price_change_after_verifying_asks_to_verify_again(self) -> None:
        search = self.wishlist()
        self.run_with(search, [summary("v1|ff7|0", "Final Fantasy VII PS1 Video Game Disc Tested", "20.00")])
        listing_id = self.find(search["id"])["results"][0]["id"]
        verify_radar_listing(self.config, listing_id)
        later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        with connect_db(self.config) as conn:
            conn.execute("UPDATE radar_listings SET price_changed_at = ? WHERE id = ?", (later, listing_id))
        result = self.find(search["id"])["results"][0]
        self.assertEqual(result["verification"]["state"], "expired")
        self.assertEqual(result["verification"]["reason"], "price-changed")


class DiversityTests(RadarRunTestCase):
    def test_one_listing_per_title_before_any_second_copy(self) -> None:
        search = self.create(
            {
                "name": "Deseados de prueba",
                "platform": "PS1",
                "searchType": "chase",
                "entityType": "game",
                "entityConsoleId": "ps1",
                "searchQuery": "crash bandicoot ps1",
                "criteria": {
                    "anyTerms": ["ps1"],
                    "resultLimit": 25,
                    "titleTargets": [
                        {"query": "crash bandicoot ps1", "name": "Crash Bandicoot"},
                        {"query": "spyro the dragon ps1", "name": "Spyro the Dragon"},
                        {"query": "tekken 3 ps1", "name": "Tekken 3"},
                    ],
                },
            }
        )
        crashes = [summary(f"v1|crash{index}|0", f"Crash Bandicoot PS1 Game Disc {index}", f"{10 + index}.00") for index in range(6)]
        responses = [
            *crashes,
            summary("v1|crashcib|0", "Crash Bandicoot PS1 CIB Complete in Box Black Label", "30.00"),
            summary("v1|spyro|0", "Spyro the Dragon PS1 Game Disc", "25.00"),
        ]
        self.run_with(search, responses)
        item = self.find(search["id"])
        titles = [result["titleTarget"]["name"] for result in item["results"]]
        self.assertEqual(sorted(titles[:2]), ["Crash Bandicoot", "Spyro the Dragon"])
        self.assertEqual(titles.count("Crash Bandicoot"), 2, "a lo sumo dos copias, y distintas")
        self.assertEqual(item["hiddenCopies"], 5)
        self.assertEqual(item["targetCoverage"], {"total": 3, "covered": 2, "uncovered": ["Tekken 3"]})

    def test_a_search_without_targets_is_left_alone(self) -> None:
        results = [{"title": f"Item {index}", "section": "primary"} for index in range(5)]
        shown, hidden = diversify_radar_results(results, 3)
        self.assertEqual([item["title"] for item in shown], ["Item 0", "Item 1", "Item 2"])
        self.assertEqual(hidden, 0)


class LotSectionTests(RadarRunTestCase):
    def test_unconfirmed_lots_go_last_and_stay_out_of_the_feed(self) -> None:
        search = self.create(
            {
                "name": "Lotes de juegos PS2",
                "platform": "PS2",
                "searchType": "lot",
                "searchQuery": "ps2 game lot",
                "criteria": {"minLotSize": 15, "maxPricePerPiece": 4},
            }
        )
        self.run_with(
            search,
            [
                summary("v1|bundle|0", "PS2 Game Bundle Kingdom Hearts Final Fantasy X", "9.00"),
                summary("v1|lot20|0", "Lot of 20 PS2 Games Tested", "40.00"),
            ],
        )
        item = self.find(search["id"])
        self.assertEqual([result["section"] for result in item["results"]], ["primary", "unconfirmed-quantity"])
        self.assertEqual(item["unconfirmedCount"], 1)
        feed_titles = [entry["title"] for entry in list_radar_feed(self.config)["items"]]
        self.assertIn("Lot of 20 PS2 Games Tested", feed_titles)
        self.assertNotIn("PS2 Game Bundle Kingdom Hearts Final Fantasy X", feed_titles)


class ConsoleRankingTests(RadarRunTestCase):
    def test_a_ready_bundle_is_listed_before_a_cheaper_console_alone(self) -> None:
        search = self.create(
            {
                "name": "Nintendo 64 — consola",
                "platform": "N64",
                "searchType": "console",
                "entityType": "console",
                "entityId": "n64",
                "searchQuery": "nintendo 64 console",
                "criteria": {"tested": "preferred", "originalParts": "preferred"},
            }
        )
        self.run_with(
            search,
            [
                summary("v1|alone|0", "Nintendo 64 Console Only Tested Working", "60.00"),
                summary(
                    "v1|ready|0",
                    "Nintendo 64 N64 Console W/ OEM Controller, Jumper Pak, All Cords. Tested/Working",
                    "85.00",
                ),
            ],
        )
        item = self.find(search["id"])
        self.assertEqual(item["results"][0]["externalId"], "v1|ready|0")
        alone = item["results"][1]
        self.assertEqual(alone["valuation"]["completion"]["total"], 45.0)
        self.assertEqual(item["platformKey"], "n64")
        self.assertEqual(item["platformLabel"], "Nintendo 64")


if __name__ == "__main__":
    unittest.main()
