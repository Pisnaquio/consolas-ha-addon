from __future__ import annotations

import json
import unittest
from unittest.mock import patch

# Importar `server.app` deja `server/` en sys.path; el paquete del radar vive
# bajo el nombre `radar.*` en todos los modos de ejecución.
import server.app  # noqa: F401
from radar.model import MarketplaceListing, SourceReceipt
from radar.sources import registry
from radar.sources.ebay import (
    EbayBrowseSource,
    EbayCredentialsMissing,
    build_browse_filters,
    parse_item_summary,
)


class FakeConfig:
    def __init__(self, environment: str = "production", credentials: bool = True) -> None:
        self.ebay_environment = environment
        self.ebay_client_id = "client" if credentials else ""
        self.ebay_client_secret = "secret" if credentials else ""


def summary(**overrides: object) -> dict:
    payload = {
        "itemId": "v1|123|0",
        "title": "PlayStation 2 Slim tested",
        "itemWebUrl": "https://www.ebay.com/itm/123",
        "price": {"value": "149.99", "currency": "USD"},
        "condition": "Pre-owned",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"country": "US"},
        "image": {"imageUrl": "https://i.ebayimg.com/1.jpg"},
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "12.00", "currency": "USD"}}],
        "seller": {"username": "retrogames", "feedbackPercentage": "99.4"},
    }
    payload.update(overrides)
    return payload


class RegistryContractTests(unittest.TestCase):
    """Agregar una fuente debe ser un módulo y una entrada, nada más."""

    def test_every_configured_source_declares_the_full_capability_set(self) -> None:
        required = set(registry.default_capabilities())
        for spec in registry.CONFIGURED_SOURCES:
            with self.subTest(source=spec.source_id):
                self.assertEqual(set(spec.capabilities), required)
                self.assertTrue(spec.label)
                self.assertNotEqual(spec.capabilities["termsMode"], "unknown")

    def test_a_source_without_an_adapter_is_not_executable_and_says_why(self) -> None:
        for spec in registry.CONFIGURED_SOURCES:
            with self.subTest(source=spec.source_id):
                if spec.executable:
                    self.assertFalse(spec.unavailable_reason)
                else:
                    self.assertTrue(spec.unavailable_reason, "una fuente bloqueada debe explicar por qué")

    def test_an_unknown_capability_flag_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            registry.default_capabilities(teleportation=True)

    def test_every_executable_adapter_loads_and_honours_the_contract(self) -> None:
        for spec in registry.CONFIGURED_SOURCES:
            if not spec.executable:
                continue
            with self.subTest(source=spec.source_id):
                adapter = spec.load()
                self.assertEqual(adapter.source_id, spec.source_id)
                self.assertTrue(callable(adapter.search))
                self.assertTrue(callable(adapter.health))

    def test_loading_a_non_executable_source_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            registry.get_source("mercari-us").load()

    def test_executable_source_ids_filters_unknown_and_blocked_sources(self) -> None:
        self.assertEqual(
            registry.executable_source_ids(["ebay-us", "shopgoodwill", "does-not-exist"]),
            ["ebay-us"],
        )

    def test_mercari_is_registered_as_manual_reference_only(self) -> None:
        spec = registry.get_source("mercari-us")
        self.assertFalse(spec.executable)
        self.assertEqual(spec.capabilities["termsMode"], "manual-reference-only")
        self.assertTrue(spec.capabilities["requiresAssistedVerification"])


class EbayFilterTests(unittest.TestCase):
    """Lo que eBay puede filtrar se filtra en eBay, no después."""

    def test_price_condition_shipping_and_returns_become_browse_filters(self) -> None:
        filters = build_browse_filters(
            {
                "currency": "USD",
                "maxItemPrice": 300,
                "condition": "used",
                "freeShippingOnly": True,
                "returnsRequired": True,
            }
        )
        self.assertIn("price:[..300]", filters)
        self.assertIn("priceCurrency:USD", filters)
        self.assertIn("conditions:{USED}", filters)
        self.assertIn("maxDeliveryCost:0", filters)
        self.assertIn("returnsAccepted:true", filters)

    def test_criteria_eBay_cannot_evaluate_are_left_to_the_matcher(self) -> None:
        filters = build_browse_filters(
            {"excludeTerms": ["parts"], "region": "NTSC-U/C", "completeness": "cib", "condition": "any"}
        )
        self.assertEqual(filters, "")


class EbayParsingTests(unittest.TestCase):
    def test_a_summary_becomes_a_normalized_listing(self) -> None:
        listing = parse_item_summary(summary())

        self.assertIsNotNone(listing)
        self.assertEqual(listing.source_id, "ebay-us")
        self.assertEqual(listing.external_id, "v1|123|0")
        self.assertEqual(listing.price_amount, 149.99)
        self.assertEqual(listing.price_currency, "USD")
        self.assertEqual(listing.shipping_amount, 12.0)
        self.assertEqual(listing.total_amount, 161.99)
        self.assertEqual(listing.listing_kind, "fixed_price")
        self.assertEqual(listing.seller_label, "99.4% de feedback")
        self.assertNotIn("retrogames", listing.seller_label, "la identidad del vendedor no se guarda")
        self.assertEqual(listing.availability, "unknown")

    def test_an_auction_is_recognized_as_such(self) -> None:
        listing = parse_item_summary(summary(buyingOptions=["AUCTION", "BEST_OFFER"]))
        self.assertEqual(listing.listing_kind, "auction")

    def test_free_shipping_and_unknown_shipping_are_different_things(self) -> None:
        free = parse_item_summary(
            summary(shippingOptions=[{"shippingCostType": "FIXED", "shippingCost": {"value": "0.00", "currency": "USD"}}])
        )
        self.assertEqual(free.shipping_amount, 0.0)
        self.assertEqual(free.shipping_label, "Envío gratis")
        self.assertTrue(free.shipping_is_known)

        calculated = parse_item_summary(summary(shippingOptions=[{"shippingCostType": "CALCULATED"}]))
        self.assertIsNone(calculated.shipping_amount)
        self.assertFalse(calculated.shipping_is_known)
        # Sin envío confirmado el total no se inventa: queda el importe del artículo.
        self.assertEqual(calculated.total_amount, 149.99)

    def test_a_summary_without_usable_identity_is_discarded(self) -> None:
        self.assertIsNone(parse_item_summary(summary(itemId="")))
        self.assertIsNone(parse_item_summary(summary(title="")))
        self.assertIsNone(parse_item_summary(summary(itemWebUrl="")))
        self.assertIsNone(parse_item_summary("no soy un dict"))

    def test_a_non_http_url_is_refused(self) -> None:
        self.assertIsNone(parse_item_summary(summary(itemWebUrl="javascript:alert(1)")))
        listing = parse_item_summary(summary(image={"imageUrl": "data:image/png;base64,AAA"}))
        self.assertEqual(listing.image_url, "")

    def test_a_missing_price_does_not_become_zero(self) -> None:
        listing = parse_item_summary(summary(price={}))
        self.assertIsNone(listing.price_amount)
        self.assertIsNone(listing.total_amount)
        self.assertEqual(listing.price_label, "")


class EbaySearchTests(unittest.TestCase):
    def test_a_complete_page_produces_an_authoritative_receipt(self) -> None:
        with patch.object(EbayBrowseSource, "fetch_item_summaries", return_value=[summary()]):
            page = EbayBrowseSource().search(FakeConfig(), "PS2", {"resultLimit": 5})

        self.assertEqual(len(page.listings), 1)
        self.assertEqual(page.receipt.status, "complete")
        self.assertTrue(page.receipt.is_authoritative)

    def test_unreadable_items_make_the_coverage_partial_not_empty(self) -> None:
        with patch.object(EbayBrowseSource, "fetch_item_summaries", return_value=[summary(), summary(itemId="")]):
            page = EbayBrowseSource().search(FakeConfig(), "PS2", {"resultLimit": 5})

        self.assertEqual(len(page.listings), 1)
        self.assertEqual(page.receipt.status, "partial")
        self.assertFalse(page.receipt.is_authoritative)

    def test_duplicate_ids_in_one_page_are_collapsed(self) -> None:
        with patch.object(EbayBrowseSource, "fetch_item_summaries", return_value=[summary(), summary()]):
            page = EbayBrowseSource().search(FakeConfig(), "PS2", {"resultLimit": 5})
        self.assertEqual(len(page.listings), 1)

    def test_a_failure_is_a_failed_receipt_and_never_an_empty_inventory(self) -> None:
        with patch.object(EbayBrowseSource, "fetch_item_summaries", side_effect=EbayCredentialsMissing("sin credenciales")):
            page = EbayBrowseSource().search(FakeConfig(credentials=False), "PS2", {})

        self.assertEqual(page.listings, [])
        self.assertEqual(page.receipt.status, "failed")
        self.assertFalse(page.receipt.is_authoritative)
        self.assertIn("sin credenciales", page.receipt.errors[0])

    def test_missing_credentials_are_reported_before_any_request(self) -> None:
        with self.assertRaisesRegex(EbayCredentialsMissing, "credenciales de eBay Developers"):
            EbayBrowseSource().access_token(FakeConfig(credentials=False))


class EbayHealthTests(unittest.TestCase):
    def test_health_distinguishes_unconfigured_sandbox_and_production(self) -> None:
        source = EbayBrowseSource()
        self.assertEqual(source.health(FakeConfig(credentials=False)).status, "unconfigured")
        self.assertEqual(source.health(FakeConfig(environment="sandbox")).status, "degraded")
        self.assertEqual(source.health(FakeConfig(environment="production")).status, "ready")


class ListingModelTests(unittest.TestCase):
    def test_total_is_never_invented_from_an_unknown_price(self) -> None:
        listing = MarketplaceListing(source_id="ebay-us", external_id="1", title="x", listing_url="https://e/1")
        self.assertIsNone(listing.total_amount)
        self.assertFalse(listing.shipping_is_known)

    def test_a_receipt_with_errors_is_never_authoritative(self) -> None:
        receipt = SourceReceipt(source_id="ebay-us", status="complete", query="q", errors=["algo falló"])
        self.assertFalse(receipt.is_authoritative)


class SellerPrivacyTests(unittest.TestCase):
    """La reputación sirve para decidir; la identidad del vendedor no se guarda.

    Es lo que sostiene la declaración de exención ante eBay: la aplicación no
    almacena datos de sus usuarios. Ver docs/EBAY_PRODUCTION_ACCESS.md.
    """

    def test_the_username_never_reaches_the_listing(self) -> None:
        listing = parse_item_summary(summary(seller={"username": "retrogames", "feedbackPercentage": "99.4"}))
        serialized = json.dumps(listing.to_dict(), ensure_ascii=False)
        self.assertNotIn("retrogames", serialized)

    def test_the_reputation_survives_because_the_score_needs_it(self) -> None:
        listing = parse_item_summary(summary(seller={"username": "retrogames", "feedbackPercentage": "99.4"}))
        self.assertIn("99.4", listing.seller_label)

    def test_a_seller_without_feedback_leaves_the_field_empty(self) -> None:
        listing = parse_item_summary(summary(seller={"username": "retrogames"}))
        self.assertEqual(listing.seller_label, "")

    def test_no_other_seller_field_leaks_into_the_listing(self) -> None:
        listing = parse_item_summary(
            summary(seller={"username": "retrogames", "feedbackPercentage": "99.4", "sellerAccountType": "BUSINESS"})
        )
        serialized = json.dumps(listing.to_dict(), ensure_ascii=False)
        for leaked in ("retrogames", "BUSINESS", "sellerAccountType"):
            with self.subTest(value=leaked):
                self.assertNotIn(leaked, serialized)


if __name__ == "__main__":
    unittest.main()
