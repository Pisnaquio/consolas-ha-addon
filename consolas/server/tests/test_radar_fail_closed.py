"""Regresiones de la auditoría del 2026-11-01: alertas fail-closed.

Ese día el radar tenía 107 publicaciones que cumplían el objetivo, las 107 con
bloqueos pendientes; 31 lo superaban al sumar el envío a Miami. Un cable de
cargador de Vita figuraba como consola, una Wii con juegos de SNES como SNES, y
201 de los 213 títulos de las búsquedas «Deseados …» salían del sistema viejo
de recomendaciones y no de Nacho's Wishlist. Estos tests fijan cada corrección.

La N64 336847347626 está en `test_radar_functional_cost.py`; los bloqueos
críticos y la referencia de pares, en `test_radar_notifications.py`.
"""

from __future__ import annotations

import unittest
from datetime import date

from server.app import radar_query_plan, target_applies_to, usable_radar_references
from radar.classify import classify_listing_item  # noqa: E402  (server.app arma el path)
from radar.matching import evaluate_match  # noqa: E402
from radar.model import MarketplaceListing  # noqa: E402
from radar.valuation import (  # noqa: E402
    PriceReference,
    auction_charges,
    cost_breakdown,
    evaluate_target,
    score_listing,
)
from radar.wishlist import (  # noqa: E402
    search_intent,
    search_row_intent,
    wished_games,
    wished_keys,
    wishlist_coverage,
)


def listing(title: str, price: float = 50.0, shipping: float | None = 10.0) -> MarketplaceListing:
    return MarketplaceListing(
        source_id="ebay-us", external_id="v1|1|0", title=title, listing_url="https://www.ebay.com/itm/1",
        price_amount=price, price_currency="USD", shipping_amount=shipping,
    )


def console_match(title: str, platform: str):
    return evaluate_match(listing(title), {}, {}, "console", platform=platform, search_type="console")


class FunctionalTargetTests(unittest.TestCase):
    def test_item_plus_shipping_over_the_target_does_not_cross_it(self) -> None:
        # 74,99 + 27,45 = 102,44: el artículo solo cruzaba 100; el costo a Miami no.
        card = score_listing(
            price_amount=74.99, shipping_amount=27.45, benchmark=None, match_confidence=1.0,
            entity_type="game", target_item_price=100,
        )
        self.assertEqual(card.target["functionalCost"], 102.44)
        self.assertFalse(card.target["meets"])

    def test_without_shipping_the_target_is_never_met(self) -> None:
        card = score_listing(
            price_amount=74.99, shipping_amount=None, benchmark=None, match_confidence=1.0,
            entity_type="game", target_item_price=100,
        )
        self.assertIsNone(card.target["meets"])
        self.assertFalse(card.target["costKnown"])
        self.assertIsNone(evaluate_target(74.99, 100)["meets"])


class IdentityTests(unittest.TestCase):
    VITA_CABLE = "*NEW* OEM Authentic Sony PlayStation PS Vita 1000’s OLED Console Charger Cable"
    WII_WITH_SNES = "Nintendo Wii Console Bundle Installed N64 SNES Games w/ OEM controllers TESTED"

    def test_a_vita_charger_cable_is_an_accessory(self) -> None:
        kind = classify_listing_item(self.VITA_CABLE)
        self.assertEqual((kind.kind, kind.confident), ("accessory", True))
        verdict = console_match(self.VITA_CABLE, "PS Vita")
        self.assertFalse(verdict.matched)

    def test_a_vita_charger_cable_gets_no_console_target_or_benchmark(self) -> None:
        kind = classify_listing_item(self.VITA_CABLE)
        self.assertFalse(target_applies_to(kind, "console"))
        console_price = PriceReference(entity_id="ps-vita", source="pricecharting", value=175.0)
        peers = PriceReference(entity_id="x", source="peer-listings", value=20.0)
        self.assertEqual(usable_radar_references([console_price, peers], "console", kind), [peers])

    def test_a_vita_that_comes_with_its_charger_is_still_a_console(self) -> None:
        for title in (
            "Black Sony PS Vita 2000 System w/ Charger - Tested Works - Region Free",
            "Sony PS Vita Slim PCH-2000 Black English Genuine Charger 1-Biz-Day Dispatch",
            "PS Vita OLED console and charger cable",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_listing_item(title).kind, "console")

    def test_a_wii_with_snes_games_does_not_satisfy_a_snes_console_search(self) -> None:
        verdict = console_match(self.WII_WITH_SNES, "Super Nintendo")
        self.assertFalse(verdict.matched)
        self.assertTrue(any("Wii" in blocker for blocker in verdict.blockers), verdict.blockers)

    def test_the_wii_still_satisfies_a_wii_console_search(self) -> None:
        verdict = console_match(self.WII_WITH_SNES, "Wii")
        self.assertFalse(any("no Wii" in blocker for blocker in verdict.blockers), verdict.blockers)

    def test_the_primary_platform_wins_over_bundled_games(self) -> None:
        verdict = console_match("PlayStation 2 PS2 Slim console with PS1 games", "PlayStation 1")
        self.assertFalse(verdict.matched)
        verdict = console_match("PlayStation 2 PS2 Slim console with PS1 games", "PlayStation 2")
        self.assertFalse(any("Es una consola" in blocker for blocker in verdict.blockers))

    def test_a_console_at_a_fraction_of_its_value_is_not_a_confirmed_console(self) -> None:
        card = score_listing(
            price_amount=16.99, shipping_amount=5.0,
            benchmark=PriceReference(entity_id="ps-vita", source="pricecharting", value=175.0,
                                     observed_at=date.today().isoformat()),
            match_confidence=1.0, entity_type="console",
            assessment={"itemKind": "console", "itemConfident": True},
        )
        self.assertIn("identity", [item["key"] for item in card.bargain_blockers])


class MarketCostTests(unittest.TestCase):
    def test_mercado_libre_uruguay_gets_no_vat_or_international_costs(self) -> None:
        cost = cost_breakdown(3000.0, 200.0, "UYU", entity_type="console", market="local")
        self.assertEqual(cost["functionalTotal"], 3200.0)
        self.assertIsNone(cost["courier"])
        self.assertIsNone(cost["importedTotal"])
        self.assertEqual(cost["taxes"], 0.0)

    def test_ebay_gets_no_taxes_the_api_does_not_report(self) -> None:
        cost = cost_breakdown(60.0, 10.0, "USD", entity_type="console", market="import")
        self.assertEqual(cost["taxes"], 0.0)
        self.assertFalse(cost["taxesKnown"])
        self.assertEqual(cost["functionalTotal"], 70.0)

    def test_an_auction_only_adds_published_commission_and_charges(self) -> None:
        # Sin impuesto publicado sobre la comisión, no se asume IVA.
        charges = auction_charges(1000.0, premium_rate=0.10)
        self.assertEqual(charges, [{"label": "comisión 10%", "amount": 100.0}])
        cost = cost_breakdown(1000.0, 0.0, "UYU", entity_type="console", market="auction", charges=charges)
        self.assertEqual(cost["functionalTotal"], 1100.0)
        self.assertIsNone(cost["courier"])


# El estado real del 2026-11-01, reducido a lo que importa: seis deseos de
# juegos (25 juegos), una colección temática y recomendaciones viejas.
WISHES = {
    "international-superstar-soccer-deluxe": ("snes", ["international-superstar-soccer-deluxe"]),
    "todos-los-donkey-kong-de-super-nintendo": (
        "snes",
        ["donkey-kong-country", "donkey-kong-country-2-diddy-s-kong-quest",
         "donkey-kong-country-3-dixie-kong-s-double-trouble"],
    ),
    "todos-los-pokemon-de-game-boy": ("gb-original", ["gb-original-pokemon-blue", "gb-original-pokemon-red",
                                                      "gb-original-pokemon-yellow"]),
    "todos-los-pokemon-de-game-boy-color": ("gb-color", ["pokemon-crystal", "pokemon-gold", "pokemon-silver",
                                                         "pokemon-pinball", "pokemon-trading-card-game",
                                                         "pokemon-puzzle-challenge"]),
    "todos-los-pokemon-de-game-boy-advance": ("gba-sp", ["pokemon-emerald", "pokemon-firered", "pokemon-leafgreen",
                                                         "pokemon-ruby", "pokemon-sapphire",
                                                         "pokemon-mystery-dungeon-red-rescue-team",
                                                         "pokemon-pinball-ruby-sapphire"]),
    "todos-los-pokemon-de-nintendo-64": ("n64", ["n64-pokemon-puzzle-league", "n64-pokemon-snap",
                                                 "n64-pokemon-stadium", "n64-pokemon-stadium-2",
                                                 "n64-hey-you-pikachu"]),
}

STATE = {
    "user": {
        "detailEditsById": {
            "wishlist:personal": {
                "wishesById": {
                    **{
                        wish_id: {"id": wish_id, "kind": "games",
                                  "items": [{"consoleId": console, "gameId": game} for game in games]}
                        for wish_id, (console, games) in WISHES.items()
                    },
                    "coleccion-god-of-war": {"id": "coleccion-god-of-war", "kind": "franchise",
                                             "franchiseId": "god-of-war", "items": []},
                }
            },
            # Sistema viejo: marcados por la calibración, no por el dueño.
            "snes": {"gameEditsById": {"super-mario-world": {"loQuiero": True},
                                       "chrono-trigger": {"keepInWishlist": True}}},
        }
    }
}

FRANCHISES = {
    "god-of-war": {"releases": [
        {"id": "chains-of-olympus-2008-psp-na", "platformId": "psp", "isPrimaryPhysicalTarget": True,
         "catalogRef": {"consoleId": "psp", "gameId": "god-of-war-chains-of-olympus"}},
    ]}
}


class WishlistTests(unittest.TestCase):
    def test_wishlist_searches_come_only_from_the_personal_wishlist(self) -> None:
        games = wished_games(STATE)
        self.assertEqual(len(games), 25)
        ids = {game["gameId"] for game in games}
        self.assertNotIn("super-mario-world", ids)
        self.assertNotIn("chrono-trigger", ids)

    def test_the_25_explicit_games_are_measured_and_the_missing_reported(self) -> None:
        searches = [
            {"status": "active", "entityType": "game", "entityConsoleId": "snes",
             "criteria": {"titleTargets": [{"gameId": "international-superstar-soccer-deluxe"},
                                           {"gameId": "donkey-kong-country"},
                                           {"gameId": "super-mario-world"}]}},
            {"status": "active", "entityType": "game", "entityId": "n64-pokemon-snap", "entityConsoleId": "n64"},
            # Pausada: no busca, no cubre.
            {"status": "paused", "entityType": "game", "entityId": "pokemon-emerald", "entityConsoleId": "gba-sp"},
        ]
        names = {"gba-sp": {"pokemon-emerald": "Pokémon Emerald"}}
        coverage = wishlist_coverage(STATE, searches, names)
        self.assertEqual((coverage["total"], coverage["covered"]), (25, 3))
        self.assertEqual(len(coverage["missing"]), 22)
        emerald = next(game for game in coverage["missing"] if game["gameId"] == "pokemon-emerald")
        self.assertEqual(emerald["name"], "Pokémon Emerald")

    def test_an_old_recommendation_does_not_count_as_a_wish(self) -> None:
        wished = wished_keys(STATE, FRANCHISES)
        self.assertEqual(
            search_intent("chase", "game", "snes", "", wished, {"gameId": "super-mario-world"}), "recommendation"
        )
        self.assertEqual(
            search_intent("chase", "game", "snes", "", wished, {"gameId": "donkey-kong-country"}), "wishlist"
        )
        # Una pieza de la colección temática deseada sí cuenta.
        self.assertEqual(
            search_intent("chase", "game", "psp", "chains-of-olympus-2008-psp-na", wished), "wishlist"
        )

    def test_a_search_made_only_of_recommendations_is_not_a_wishlist_search(self) -> None:
        wished = wished_keys(STATE, FRANCHISES)
        old = {"searchType": "chase", "entityType": "game", "entityConsoleId": "snes",
               "criteria": {"titleTargets": [{"gameId": "super-mario-world"}, {"gameId": "chrono-trigger"}]}}
        self.assertEqual(search_row_intent(old, wished), "recommendation")
        mixed = {**old, "criteria": {"titleTargets": [{"gameId": "super-mario-world"},
                                                      {"gameId": "donkey-kong-country"}]}}
        self.assertEqual(search_row_intent(mixed, wished), "wishlist")
        self.assertEqual(search_row_intent(mixed, wished, strict=True), "recommendation")


class Row(dict):
    """Lo mínimo de una fila de `radar_searches` para planear una corrida."""


def row(name: str, *, search_type: str = "chase", entity_type: str = "", entity_id: str = "",
        console: str = "", queries: int = 1, targets: list[str] | None = None, last: str = "") -> Row:
    criteria = {"queries": [f"{name} {index}" for index in range(1, queries)],
                "titleTargets": [{"gameId": game} for game in targets or []]}
    import json

    return Row(id=name, name=name, search_type=search_type, entity_type=entity_type, entity_id=entity_id,
               entity_console_id=console, criteria_json=json.dumps(criteria), search_query=name, last_checked_at=last)


class QueryBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wished = wished_keys(STATE, FRANCHISES)
        self.rows = [
            row("Recomendados SNES", entity_type="game", console="snes", queries=40,
                targets=["super-mario-world", "chrono-trigger"]),
            row("Lotes N64", search_type="lot", queries=4),
            row("ISS Deluxe", entity_type="game", entity_id="international-superstar-soccer-deluxe", console="snes"),
            row("N64 consola", search_type="console", entity_type="console", entity_id="n64", queries=6),
        ]

    def test_the_wishlist_goes_first_and_recommendations_last(self) -> None:
        planned, deferred = radar_query_plan(self.rows, self.wished, "morning", 12)
        self.assertEqual([item[0]["name"] for item in planned], ["ISS Deluxe", "N64 consola", "Lotes N64"])
        self.assertEqual([item["name"] for item in deferred], ["Recomendados SNES"])
        self.assertIn("presupuesto", deferred[0]["reason"])

    def test_recommendations_do_not_run_outside_the_morning(self) -> None:
        planned, deferred = radar_query_plan(self.rows, self.wished, "afternoon", 500)
        self.assertNotIn("Recomendados SNES", [item[0]["name"] for item in planned])
        self.assertIn("mañana", deferred[0]["reason"])

    def test_a_search_counts_by_its_queries_not_as_one(self) -> None:
        planned, _ = radar_query_plan(self.rows, self.wished, "morning", 51)
        self.assertEqual(len(planned), 4)
        planned, _ = radar_query_plan(self.rows, self.wished, "morning", 50)
        self.assertEqual(len(planned), 3)


if __name__ == "__main__":
    unittest.main()
