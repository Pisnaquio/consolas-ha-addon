"""Costo funcional: lo que de verdad cuesta dejar una pieza lista para usar.

El 2026-11-01 llegó una alerta de «ganga» por una Nintendo 64 de eBay
(336847347626): USD 59,99 + USD 15,85 de envío a Miami, sin fuente de
alimentación, con el stick del control flojo, Jumper Pak estándar y sin
devoluciones. El radar sólo había leído el título y comparaba el objetivo de
100 contra el precio de portada. Estos tests fijan la corrección general: el
veredicto y el aviso salen del costo funcional y de lo que dice la publicación
entera, no del precio del artículo.
"""

from __future__ import annotations

import tempfile
from datetime import date
import unittest
from pathlib import Path
from unittest.mock import patch

from server.app import (
    create_radar_search,
    delete_radar_search,
    init_db,
    is_exceptional,
    list_radar_feed,
    list_radar_searches,
    notify_radar_opportunities,
    radar_email_account,
    run_radar_search,
)
from server.radar.classify import assess_listing_evidence
from server.radar.valuation import (
    PriceReference,
    alert_worthy,
    auction_charges,
    score_listing,
)


REGRESSION_ID = "v1|336847347626|0"
REGRESSION_TITLE = "Nintendo 64 N64 Console Bundle NUS-001 OEM Controller Power AV Jumper Pak Tested"
# Lo que dice la publicación real, en las palabras que usa un vendedor.
REGRESSION_DETAIL = {
    "description": (
        "Nintendo 64 console tested and working. Comes with one OEM controller, AV cable and the standard "
        "Jumper Pak. Power supply is not included. The joystick on the controller is loose. "
        "Please see photos. No returns."
    ),
    "conditionNote": "Used. Controller stick is loose.",
    "returnsAccepted": False,
    "imageCount": 7,
    "taxes": None,
    "shipping": 15.85,
    # Sigue a la venta: lo que la frena es lo que le falta, no la disponibilidad.
    "available": True,
}
CLEAN_DETAIL = {
    "description": "Tested and working with OEM controller, power supply, AV cable and Jumper Pak.",
    "conditionNote": "",
    "returnsAccepted": True,
    "imageCount": 9,
    "taxes": None,
    "shipping": None,
    "available": True,
}

# Una referencia de mercado confiable para la N64. Con ella, lo único que
# puede frenar la alerta es lo que dice la publicación: así se prueba que la
# N64 real no alerta por sus faltantes y defectos, no por falta de referencia.
N64_REFERENCE = PriceReference(
    entity_id="n64", source="pricecharting", value=120.0, completeness="loose", confidence=0.9, independent=True,
    observed_at=date.today().isoformat(),
)


def ebay_summary(item_id: str, title: str, price: str, shipping: str = "15.85") -> dict:
    return {
        "itemId": item_id,
        "title": title,
        "itemWebUrl": f"https://www.ebay.com/itm/{item_id.split('|')[1]}",
        "price": {"value": price, "currency": "USD"},
        "condition": "Used",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"country": "US"},
        "image": {},
        "seller": {"feedbackPercentage": "100.0"},
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": shipping, "currency": "USD"}}],
    }


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
        self.radar_item_details = True
        self.radar_email_mode = "alerts"
        self.radar_email_from = "radar@example.test"
        self.radar_email_to = "owner@example.test"
        self.radar_smtp_host = "smtp.example.test"
        self.radar_smtp_port = 587
        self.radar_smtp_username = "user"
        self.radar_smtp_password = "secret"


class N64RegressionTests(unittest.TestCase):
    """El caso real, de la búsqueda al mail."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)
        delete_radar_search(self.config, "iss-deluxe-snes")
        self.search = create_radar_search(
            self.config,
            {
                "name": "Nintendo 64 — consola",
                "platform": "N64",
                "searchType": "console",
                "entityType": "console",
                "entityId": "n64",
                "searchQuery": "nintendo 64 console tested jumper pak",
                "criteria": {"targetItemPrice": 100},
            },
        )["search"]

    def run_with(self, regression_detail: dict) -> dict:
        # Pares: bundles completos a ~120, la mediana con la que se comparó.
        peers = [
            ebay_summary(f"v1|90{i}|0", "Nintendo 64 N64 Console Bundle OEM Controller Power AV Jumper Pak Tested",
                         f"{118 + i}.99", "0.00")
            for i in range(5)
        ]
        summaries = [ebay_summary(REGRESSION_ID, REGRESSION_TITLE, "59.99"), *peers]

        def detail(_self, _config, external_id, _destination=None):
            return dict(regression_detail if external_id == REGRESSION_ID else CLEAN_DETAIL)

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=summaries), patch(
            "radar.sources.ebay.EbayBrowseSource.item_detail", autospec=True, side_effect=detail
        ), patch("server.app.radar_entity_references", return_value=[N64_REFERENCE]):
            run_radar_search(self.config, self.search["id"])
        search = next(item for item in list_radar_searches(self.config)["items"] if item["id"] == self.search["id"])
        return next(item for item in search["results"] if item["externalId"] == REGRESSION_ID)

    def feed_item(self) -> dict:
        feed = list_radar_feed(self.config, 50)
        items = [*feed["items"], *feed.get("following", [])]
        return next(item for item in items if "336847347626" in item["listingUrl"])

    def test_the_real_n64_is_not_a_bargain_and_does_not_alert(self) -> None:
        result = self.run_with(REGRESSION_DETAIL)
        valuation = result["valuation"]

        self.assertNotIn(result["band"], {"ganga", "buena"})
        self.assertFalse(is_exceptional(self.feed_item()))
        with patch("server.app.send_radar_email") as send:
            outcome = notify_radar_opportunities(self.config)
        self.assertEqual(outcome["alerts"], 0)
        send.assert_not_called()

        # 59,99 + 15,85 + fuente 20 + control a reponer 20 = 115,84: ya no cruza 100.
        self.assertEqual(valuation["cost"]["functionalTotal"], 115.84)
        self.assertFalse(valuation["target"]["meets"])
        self.assertEqual(valuation["target"]["itemPrice"], 59.99)

    def test_the_account_explains_the_sum_and_the_risks(self) -> None:
        self.run_with(REGRESSION_DETAIL)
        account = radar_email_account(self.feed_item())

        self.assertIn("USD 59,99 + USD 15,85 de envío", account)
        self.assertIn("fuente faltante estimado en USD 20,00", account)
        self.assertIn("= USD 115,84 antes del courier", account)
        self.assertIn("stick del control flojo", account.lower())
        self.assertIn("Jumper Pak estándar", account)
        self.assertIn("sin devoluciones", account)
        self.assertIn("no ganga", account)

    def test_the_same_console_read_clean_does_alert(self) -> None:
        clean = {**CLEAN_DETAIL, "description": "Tested, works great. Includes power supply, AV cable, OEM controller."}
        result = self.run_with(clean)

        self.assertEqual(result["valuation"]["cost"]["functionalTotal"], 75.84)
        self.assertTrue(result["valuation"]["target"]["meets"])
        self.assertTrue(is_exceptional(self.feed_item()))

    def test_without_reading_the_listing_it_never_alerts(self) -> None:
        self.config.radar_item_details = False
        result = self.run_with(CLEAN_DETAIL)

        keys = [item["key"] for item in result["valuation"]["bargainBlockers"]]
        self.assertIn("details", keys)
        self.assertFalse(is_exceptional(self.feed_item()))


def reference(value: float) -> PriceReference:
    return PriceReference(
        entity_id="n64", source="pricecharting", value=value, completeness="loose", confidence=0.9, independent=True,
        observed_at=date.today().isoformat(),
    )


READY_KIT = {
    "itemKind": "console",
    "itemConfident": True,
    "kit": {"status": "ready", "present": ["controller", "cables", "jumper-pak"], "missing": [], "issues": []},
}


def score(price: float, shipping: float | None, *, evidence: dict | None = None, assessment: dict | None = None,
          benchmark: float = 120.0, **extra: object):
    return score_listing(
        price_amount=price,
        shipping_amount=shipping,
        benchmark=reference(benchmark),
        match_confidence=1.0,
        match_reasons=["Declara estar probada («tested»)"],
        seller_known=True,
        entity_type="console",
        assessment=assessment if assessment is not None else READY_KIT,
        availability_verified=True,
        evidence=evidence,
        details_expected=True,
        **extra,
    )


def read(description: str, *, returns: bool | None = True, images: int = 8, title: str = "N64 console bundle tested") -> dict:
    evidence = assess_listing_evidence(title, description, "", "n64")
    evidence.update({"read": True, "returnsAccepted": returns, "imageCount": images, "taxes": None})
    return evidence


class FunctionalCostCases(unittest.TestCase):
    def test_a_complete_listing_read_clean_is_a_real_bargain(self) -> None:
        card = score(60.0, 10.0, evidence=read("Tested, includes power supply, AV cable and OEM controller."))
        self.assertEqual(card.cost["functionalTotal"], 70.0)
        self.assertEqual(card.band, "ganga")
        self.assertTrue(alert_worthy(card.to_dict()))

    def test_a_missing_essential_accessory_is_costed_in(self) -> None:
        card = score(60.0, 10.0, evidence=read("Power supply not included."))
        self.assertEqual(card.cost["functionalTotal"], 90.0)
        self.assertIn("power-supply", card.completion["pieces"])
        self.assertIn("fuente faltante estimado en USD 20,00", card.explanation)
        self.assertFalse(alert_worthy(card.to_dict()))

    def test_a_cosmetic_defect_eats_the_bargain(self) -> None:
        card = score(60.0, 10.0, evidence=read("Works great. Heavy scratches on the top shell."))
        self.assertEqual(card.price_band, "ganga")
        self.assertEqual(card.band, "razonable")
        self.assertIn("rayas o raspones", card.risks)
        self.assertFalse(alert_worthy(card.to_dict()))

    def test_a_functional_defect_goes_to_review(self) -> None:
        card = score(60.0, 10.0, evidence=read("Sold as is, does not read games. For parts or repair."))
        self.assertEqual(card.band, "revisar")
        self.assertIn("functional-defect", card.review_keys)

    def test_a_loose_stick_costs_a_replacement_controller(self) -> None:
        card = score(60.0, 10.0, evidence=read("Controller stick is a bit loose."))
        self.assertEqual(card.completion["replaced"], ["controller"])
        self.assertEqual(card.cost["functionalTotal"], 90.0)
        self.assertNotIn(card.band, {"ganga", "buena"})

    def test_negated_defects_are_not_defects(self) -> None:
        evidence = read("No scratches, no stick drift, no issues at all. Tested.")
        self.assertEqual(evidence["defects"], [])

    def test_expensive_shipping_is_part_of_the_total(self) -> None:
        cheap_item = score(55.0, 60.0, evidence=read("Tested, complete."))
        self.assertEqual(cheap_item.cost["functionalTotal"], 115.0)
        self.assertNotIn(cheap_item.band, {"ganga", "buena"})

    def test_no_returns_blocks_the_bargain(self) -> None:
        card = score(60.0, 10.0, evidence=read("Tested, complete.", returns=False))
        self.assertIn("returns", [item["key"] for item in card.bargain_blockers])
        self.assertFalse(alert_worthy(card.to_dict()))

    def test_few_photos_block_the_bargain(self) -> None:
        card = score(60.0, 10.0, evidence=read("Tested, complete.", images=1))
        self.assertIn("photos", [item["key"] for item in card.bargain_blockers])

    def test_generic_accessories_are_not_oem(self) -> None:
        card = score(60.0, 10.0, evidence=read("Tested. Comes with an aftermarket controller."))
        self.assertIn("originality-accessory", [item["key"] for item in card.bargain_blockers])

    def test_a_jumper_pak_is_not_an_expansion_pak(self) -> None:
        jumper = read("Tested with the standard Jumper Pak.")
        expansion = read("Tested with the Expansion Pak installed.")
        self.assertEqual(jumper["memory"], "jumper")
        self.assertEqual(expansion["memory"], "expansion")
        card = score(60.0, 10.0, evidence=jumper)
        self.assertIn("Trae Jumper Pak estándar, no Expansion Pak.", card.verdict_notes)

    def test_visible_taxes_are_added_and_missing_ones_are_not_invented(self) -> None:
        taxed = read("Tested, complete.")
        taxed["taxes"] = 4.90
        card = score(60.0, 10.0, evidence=taxed)
        self.assertEqual(card.cost["functionalTotal"], 74.9)
        untaxed = score(60.0, 10.0, evidence=read("Tested, complete."))
        self.assertEqual(untaxed.cost["taxes"], 0.0)
        self.assertIn("Impuestos: la fuente no los informa.", untaxed.explanation)

    def test_mercado_libre_is_a_local_purchase(self) -> None:
        card = score(3000.0, 200.0, evidence=read("Funciona perfecto, completa."), benchmark=5000.0,
                     market="local", currency="UYU")
        self.assertIsNone(card.cost["courier"])
        self.assertIsNone(card.cost["importedTotal"])
        self.assertEqual(card.cost["functionalTotal"], 3200.0)
        self.assertIn("compra local (sin courier ni IVA de importación)", card.explanation)

    def test_an_auction_adds_commission_its_tax_and_splits_by_unit(self) -> None:
        charges = auction_charges(1000.0, premium_rate=0.12, premium_tax_rate=0.22,
                                  other_charges=[{"label": "manejo", "amount": 50}])
        self.assertEqual([c["amount"] for c in charges], [120.0, 26.4, 50.0])
        card = score(1000.0, 0.0, evidence=read("Lote de 4 consolas funcionando."), benchmark=2000.0,
                     market="auction", charges=charges, quantity=4, currency="UYU")
        self.assertEqual(card.cost["functionalTotal"], 1196.4)
        self.assertEqual(card.cost["unitPrice"], 299.1)
        self.assertIn("de comisión 12%", card.explanation)
        self.assertIn("4 unidades obligatorias", card.explanation)

    def test_the_target_is_measured_on_functional_cost(self) -> None:
        card = score(59.99, 15.85, evidence=read("Power supply not included."), target_item_price=80)
        self.assertEqual(card.target["functionalCost"], 95.84)
        self.assertFalse(card.target["meets"])


if __name__ == "__main__":
    unittest.main()
