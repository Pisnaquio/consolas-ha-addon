"""El destino de recepción y el header que eBay necesita para cotizar envío.

eBay documenta `X-EBAY-C-ENDUSERCTX` como opcional, pero para los ítems de
envío CALCULADO el array de opciones de envío **sólo se devuelve si el header
viaja**. Sin él, esas publicaciones llegaban sin envío y el feed mostraba el
precio del artículo como si fuera el total: un total corto parece una
oportunidad, y es el peor error posible en una decisión de compra.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Importar `server.app` deja `server/` en sys.path; el paquete del radar vive
# bajo el nombre `radar.*` en todos los modos de ejecución.
import server.app  # noqa: F401
from radar.sources.ebay import EbayBrowseSource, end_user_context
from radar.valuation import sold_comparables_link
from server.app import (
    ApiError,
    RADAR_DEFAULT_DESTINATION_COUNTRY,
    RADAR_DEFAULT_DESTINATION_ZIP,
    connect_db,
    create_radar_search,
    delete_radar_search,
    get_radar_preferences,
    init_db,
    radar_destination,
    run_radar_search,
    update_radar_preferences,
)


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


class EndUserContextTests(unittest.TestCase):
    def test_the_header_carries_country_and_zip_url_encoded(self) -> None:
        value = end_user_context({"country": "US", "zip": "33166"})
        self.assertEqual(value, "contextualLocation=country%3DUS%2Czip%3D33166")

    def test_without_a_destination_there_is_no_header(self) -> None:
        # No se inventa un destino: sin dato, el envío desconocido se declara
        # desconocido en vez de cotizarse contra un lugar que nadie eligió.
        self.assertEqual(end_user_context(None), "")
        self.assertEqual(end_user_context({}), "")
        self.assertEqual(end_user_context({"zip": "33166"}), "")

    def test_a_country_without_zip_still_travels(self) -> None:
        self.assertEqual(end_user_context({"country": "UY"}), "contextualLocation=country%3DUY")


class DestinationPreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)

    def test_a_fresh_install_is_seeded_with_miami(self) -> None:
        prefs = get_radar_preferences(self.config)
        self.assertEqual(prefs["destinationCountry"], RADAR_DEFAULT_DESTINATION_COUNTRY)
        self.assertEqual(prefs["destinationZip"], RADAR_DEFAULT_DESTINATION_ZIP)
        self.assertEqual(radar_destination(self.config), {"country": "US", "zip": "33166"})

    def test_clearing_the_destination_survives_a_restart(self) -> None:
        """Si el dueño lo borra, no vuelve en el próximo arranque."""
        update_radar_preferences(self.config, {"destinationCountry": "", "destinationZip": ""})
        init_db(self.config)
        prefs = get_radar_preferences(self.config)
        self.assertEqual(prefs["destinationCountry"], "")
        self.assertEqual(prefs["destinationZip"], "")
        self.assertIsNone(radar_destination(self.config))

    def test_a_changed_destination_is_not_overwritten_on_restart(self) -> None:
        update_radar_preferences(self.config, {"destinationCountry": "UY", "destinationZip": "11300"})
        init_db(self.config)
        prefs = get_radar_preferences(self.config)
        self.assertEqual(prefs["destinationCountry"], "UY")
        self.assertEqual(prefs["destinationZip"], "11300")

    def test_the_budget_is_untouched_by_a_destination_change(self) -> None:
        update_radar_preferences(self.config, {"monthlyBudgetUsd": 120})
        update_radar_preferences(self.config, {"destinationZip": "33101"})
        prefs = get_radar_preferences(self.config)
        self.assertEqual(prefs["monthlyBudgetUsd"], 120)
        self.assertEqual(prefs["destinationZip"], "33101")

    def test_a_bogus_country_is_rejected(self) -> None:
        with self.assertRaises(ApiError):
            update_radar_preferences(self.config, {"destinationCountry": "Estados Unidos"})

    def test_an_address_is_not_a_postal_code(self) -> None:
        # El ZIP es un ZIP. La dirección completa es privada y no entra acá.
        with self.assertRaises(ApiError):
            update_radar_preferences(self.config, {"destinationZip": "1234 NW 79th Ave, Miami"})


class DestinationReachesEbayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)
        delete_radar_search(self.config, "iss-deluxe-snes")
        self.search = create_radar_search(self.config, {"name": "PS2"})["search"]

    def run_capturing_destination(self) -> list[object]:
        seen: list[object] = []

        def fake(self_source, config, query, criteria, destination=None):  # noqa: ANN001
            seen.append(destination)
            return []

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", new=fake):
            run_radar_search(self.config, self.search["id"])
        return seen

    def test_the_configured_destination_reaches_the_adapter(self) -> None:
        self.assertEqual(self.run_capturing_destination(), [{"country": "US", "zip": "33166"}])

    def test_without_a_destination_the_adapter_gets_none(self) -> None:
        update_radar_preferences(self.config, {"destinationCountry": ""})
        self.assertEqual(self.run_capturing_destination(), [None])

    def test_the_header_is_actually_sent_on_the_request(self) -> None:
        """De punta a punta: de la preferencia al header HTTP."""
        captured: dict[str, object] = {}

        class FakeResponse:
            def read(self) -> bytes:
                return b'{"itemSummaries": []}'

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            captured["headers"] = dict(request.headers)
            return FakeResponse()

        source = EbayBrowseSource()
        with patch("radar.sources.ebay.EbayBrowseSource.access_token", return_value="tok"), patch(
            "urllib.request.urlopen", new=fake_urlopen
        ):
            source.fetch_item_summaries(self.config, "god of war", {}, {"country": "US", "zip": "33166"})

        headers = {str(k).lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(headers["X-ebay-c-enduserctx".lower()], "contextualLocation=country%3DUS%2Czip%3D33166")

    def test_no_destination_means_no_header_on_the_request(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def read(self) -> bytes:
                return b'{"itemSummaries": []}'

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            captured["headers"] = dict(request.headers)
            return FakeResponse()

        source = EbayBrowseSource()
        with patch("radar.sources.ebay.EbayBrowseSource.access_token", return_value="tok"), patch(
            "urllib.request.urlopen", new=fake_urlopen
        ):
            source.fetch_item_summaries(self.config, "god of war", {}, None)

        headers = {str(k).lower() for k in captured["headers"]}
        self.assertNotIn("x-ebay-c-enduserctx", headers)


if __name__ == "__main__":
    unittest.main()


class SoldComparablesTests(unittest.TestCase):
    """El benchmark que falta, a un click.

    El radar no puede traer ventas cerradas —Marketplace Insights es de acceso
    restringido y scrapear eBay está prohibido— pero sí puede armar el link al
    filtro de vendidos del propio eBay. Es un link, no una integración.
    """

    def test_the_link_asks_ebay_for_completed_sales(self) -> None:
        link = sold_comparables_link("God of War II PS2")
        self.assertIsNotNone(link)
        self.assertIn("LH_Sold=1", link["url"])
        self.assertIn("LH_Complete=1", link["url"])
        self.assertIn("_nkw=God+of+War+II+PS2", link["url"])
        self.assertTrue(link["url"].startswith("https://www.ebay.com/sch/i.html?"))

    def test_it_declares_that_it_needs_the_owner_session(self) -> None:
        # Deslogueado, ese filtro redirige al login: se dice, no se descubre.
        link = sold_comparables_link("Daxter PSP")
        self.assertIn("sesión iniciada", link["note"])

    def test_an_empty_query_gets_no_link_instead_of_a_broken_one(self) -> None:
        self.assertIsNone(sold_comparables_link(""))
        self.assertIsNone(sold_comparables_link("   "))

    def test_the_query_is_escaped_not_concatenated(self) -> None:
        link = sold_comparables_link("god of war & co")
        self.assertIn("%26", link["url"])
        self.assertNotIn(" ", link["url"])


class SoldComparablesReachTheCardTests(DestinationReachesEbayTests):
    def test_a_scored_listing_carries_the_link_for_its_own_search(self) -> None:
        summary = {
            "itemId": "v1|9|0",
            "title": "God of War II PS2 complete",
            "itemWebUrl": "https://www.ebay.com/itm/9",
            "price": {"value": "40.00", "currency": "USD"},
            "condition": "Pre-owned",
            "buyingOptions": ["FIXED_PRICE"],
            "itemLocation": {"country": "US"},
            "image": {},
            "shippingOptions": [
                {"shippingCostType": "FIXED", "shippingCost": {"value": "0.00", "currency": "USD"}}
            ],
        }
        with patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[summary]
        ):
            run_radar_search(self.config, self.search["id"])

        with connect_db(self.config) as conn:
            row = conn.execute("SELECT valuation_json FROM radar_search_matches").fetchone()
        import json as _json

        valuation = _json.loads(row["valuation_json"])
        self.assertIsNotNone(valuation["soldComparables"])
        self.assertIn("LH_Sold=1", valuation["soldComparables"]["url"])
