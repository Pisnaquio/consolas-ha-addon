"""Una búsqueda con varias consultas, y mirarla sin activarla.

Dos capacidades generales que llegaron juntas porque una sin la otra no sirve:
una búsqueda que persigue una lista de títulos necesita mandar una consulta por
título, y una búsqueda que todavía no está activa necesita poder mostrarse antes
de que el owner decida activarla.

Nada de esto es específico de una consola: los tests usan búsquedas inventadas
salvo los que miran justamente la búsqueda sembrada.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.app import (
    ApiError,
    connect_db,
    create_radar_search,
    delete_radar_search,
    init_db,
    list_radar_listings,
    list_radar_searches,
    preview_radar_search,
    radar_search_queries,
    run_radar_search,
    set_radar_search_status,
    update_radar_search,
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


class RadarQueryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)

    def find(self, search_id: str) -> dict:
        return next(item for item in list_radar_searches(self.config)["items"] if item["id"] == search_id)

    def create(self, payload: dict) -> dict:
        return create_radar_search(self.config, payload)["search"]


class ExtraQueryCriteriaTests(RadarQueryTestCase):
    def test_extra_queries_persist_and_lose_repeats_and_blanks(self) -> None:
        created = self.create(
            {
                "name": "Biblioteca inventada",
                "platform": "Consola inventada",
                "searchQuery": "lote de prueba",
                "criteria": {"queries": ["  Patapon PSP ", "", "patapon psp", "Daxter PSP"]},
            },
        )
        self.assertEqual(created["criteria"]["queries"], ["Patapon PSP", "Daxter PSP"])

    def test_the_main_query_leads_and_never_repeats_itself(self) -> None:
        queries = radar_search_queries("lote de prueba", {"queries": ["Lote De Prueba", "Daxter PSP"]})
        self.assertEqual(queries, ["lote de prueba", "Daxter PSP"])

    def test_a_query_that_is_too_long_is_refused(self) -> None:
        with self.assertRaises(ApiError):
            self.create(
                {"name": "Demasiado larga", "criteria": {"queries": ["x" * 301]}},
            )

    def test_too_many_queries_are_refused(self) -> None:
        with self.assertRaises(ApiError):
            self.create(
                {"name": "Demasiadas", "criteria": {"queries": [f"consulta {index}" for index in range(49)]}},
            )

    def test_editing_other_criteria_does_not_drop_the_queries(self) -> None:
        """Un campo que la UI no manda no se borra: los criterios se mezclan."""
        created = self.create(
            {"name": "Sobreviviente", "criteria": {"queries": ["Daxter PSP"], "maxItemPrice": 40}},
        )
        updated = update_radar_search(self.config, created["id"], {"criteria": {"maxItemPrice": 60}})["search"]
        self.assertEqual(updated["criteria"]["queries"], ["Daxter PSP"])
        self.assertEqual(updated["criteria"]["maxItemPrice"], 60)


class MultiQueryRunTests(RadarQueryTestCase):
    def build(self, **overrides: object) -> dict:
        payload = {
            "name": "Biblioteca inventada",
            "platform": "Consola inventada",
            "searchType": "lot",
            "searchQuery": "lote de prueba",
            "criteria": {
                "queries": ["Daxter PSP", "Patapon PSP"],
                "anyTerms": ["psp"],
                "resultLimit": 10,
            },
        }
        payload.update(overrides)
        return self.create(payload)

    def test_every_query_reaches_the_source_once(self) -> None:
        search = self.build()
        seen: list[str] = []

        def fake(self_source, config, query, criteria):  # noqa: ANN001 - firma del adapter
            seen.append(query)
            return []

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", new=fake):
            run_radar_search(self.config, search["id"])

        self.assertEqual(seen, ["lote de prueba", "Daxter PSP", "Patapon PSP"])

    def test_a_listing_found_by_two_queries_is_stored_once(self) -> None:
        """Dos consultas son dos formas de encontrar lo mismo, no dos compras."""
        search = self.build()
        shared = ebay_summary("v1|1|0", "Daxter PSP complete")

        def fake(self_source, config, query, criteria):  # noqa: ANN001
            return [shared]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", new=fake):
            result = run_radar_search(self.config, search["id"])

        self.assertEqual(result["results"], 1)
        self.assertEqual(len(self.find(search["id"])["results"]), 1)

    def test_one_failing_query_does_not_sink_the_others(self) -> None:
        search = self.build()

        def fake(self_source, config, query, criteria):  # noqa: ANN001
            if query == "Daxter PSP":
                raise RuntimeError("eBay caído")
            return [ebay_summary("v1|2|0", "Patapon PSP complete in box")]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", new=fake):
            result = run_radar_search(self.config, search["id"])

        self.assertEqual(result["results"], 1)
        # Una corrida con una consulta caída no autoriza a retirar inventario.
        self.assertFalse(result["authoritative"])
        failed = [receipt for receipt in result["receipts"] if receipt["status"] == "failed"]
        self.assertEqual([receipt["query"] for receipt in failed], ["Daxter PSP"])

    def test_a_run_where_every_query_fails_is_an_error(self) -> None:
        search = self.build()

        def fake(self_source, config, query, criteria):  # noqa: ANN001
            raise RuntimeError("eBay caído")

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", new=fake):
            with self.assertRaises(ApiError):
                run_radar_search(self.config, search["id"])


class PreviewTests(RadarQueryTestCase):
    def build(self, status: str = "paused") -> dict:
        search = self.create(
            {
                "name": "Biblioteca inventada",
                "platform": "Consola inventada",
                "searchType": "lot",
                "searchQuery": "lote de prueba",
                "criteria": {"queries": ["Daxter PSP"], "anyTerms": ["psp"], "resultLimit": 10},
            }
        )
        if status != "active":
            set_radar_search_status(self.config, search["id"], status)
        return search

    def test_a_paused_search_can_be_previewed_without_activating_it(self) -> None:
        search = self.build()
        with patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
            return_value=[ebay_summary("v1|3|0", "Daxter PSP complete in box")],
        ):
            preview = preview_radar_search(self.config, search["id"])

        self.assertTrue(preview["preview"])
        self.assertFalse(preview["persisted"])
        self.assertEqual(preview["status"], "paused")
        self.assertEqual(len(preview["results"]), 1)
        self.assertEqual(preview["results"][0]["title"], "Daxter PSP complete in box")
        self.assertEqual(preview["results"][0]["listingUrl"], "https://www.ebay.com/itm/v1|3|0")
        # Sigue pausada: mirarla no la activa.
        self.assertEqual(self.find(search["id"])["status"], "paused")

    def test_a_preview_writes_nothing(self) -> None:
        search = self.build()
        with patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
            return_value=[ebay_summary("v1|4|0", "Daxter PSP complete in box")],
        ):
            preview_radar_search(self.config, search["id"])

        after = self.find(search["id"])
        self.assertEqual(after["results"], [])
        self.assertIsNone(after["lastCheckedAt"])
        self.assertEqual(list_radar_listings(self.config)["items"], [])
        with connect_db(self.config) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM radar_search_matches").fetchone()[0], 0
            )
            self.assertEqual(conn.execute("SELECT count(*) FROM radar_runs").fetchone()[0], 0)

    def test_a_preview_never_returns_more_than_the_result_limit(self) -> None:
        search = self.build()
        summaries = [ebay_summary(f"v1|{index}|0", f"Daxter PSP copy {index}") for index in range(25)]
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=summaries):
            preview = preview_radar_search(self.config, search["id"])

        self.assertEqual(preview["matched"], 25)
        self.assertEqual(len(preview["results"]), 10)

    def test_a_preview_result_carries_no_inventory_identity(self) -> None:
        """Sin `id` no hay descarte ni compra: no hay nada guardado que tocar."""
        search = self.build()
        with patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
            return_value=[ebay_summary("v1|5|0", "Daxter PSP complete in box")],
        ):
            preview = preview_radar_search(self.config, search["id"])

        self.assertNotIn("id", preview["results"][0])

    def test_a_preview_keeps_the_seller_identity_out(self) -> None:
        search = self.build()
        with patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
            return_value=[ebay_summary("v1|6|0", "Daxter PSP complete in box")],
        ):
            preview = preview_radar_search(self.config, search["id"])

        self.assertEqual(preview["results"][0]["sellerLabel"], "99.4% de feedback")
        self.assertNotIn("retrogames", str(preview["results"][0]))

    def test_a_draft_can_be_previewed_too(self) -> None:
        search = self.build(status="draft")
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[]):
            preview = preview_radar_search(self.config, search["id"])
        self.assertEqual(preview["status"], "draft")

    def test_a_missing_search_cannot_be_previewed(self) -> None:
        with self.assertRaises(ApiError):
            preview_radar_search(self.config, "radar-does-not-exist")


class PspLibrarySeedTests(RadarQueryTestCase):
    """La búsqueda que el owner pidió: sembrada, suspendida y no activada."""

    def test_the_search_is_seeded_paused_and_never_active(self) -> None:
        search = self.find("radar-psp-library")
        self.assertEqual(search["name"], "Biblioteca PSP — juegos esenciales")
        self.assertEqual(search["status"], "paused")
        self.assertFalse(search["enabled"])
        self.assertFalse(search["canRun"])
        self.assertEqual(search["platform"], "PSP")
        self.assertEqual(search["sources"], ["ebay-us"])
        self.assertEqual(search["slots"], ["morning", "afternoon", "night"])
        self.assertEqual(search["slotLabels"], ["09:00", "16:00", "22:30"])

    def test_a_paused_search_refuses_to_run_but_accepts_a_preview(self) -> None:
        with self.assertRaises(ApiError):
            run_radar_search(self.config, "radar-psp-library")
        self.assertTrue(self.find("radar-psp-library")["canPreview"])

    def test_every_requested_title_has_its_own_query(self) -> None:
        search = self.find("radar-psp-library")
        queries = [search["searchQuery"], *search["criteria"]["queries"]]
        for title in ("God of War Chains of Olympus", "Persona 3 Portable", "Ys Seven", "Jeanne d'Arc"):
            self.assertIn(f"{title} PSP", queries)
        self.assertIn("PSP game lot", queries)
        self.assertEqual(len(queries), len(set(queries)))

    def test_the_search_declares_no_region_lock(self) -> None:
        """Los juegos de PSP no tienen bloqueo regional: PAL y NTSC-J entran."""
        self.assertEqual(self.find("radar-psp-library")["criteria"]["region"], "")

    def test_the_search_asks_for_ten_results(self) -> None:
        self.assertEqual(self.find("radar-psp-library")["criteria"]["resultLimit"], 10)

    def test_the_seed_does_not_run_twice_and_does_not_resurrect_a_deletion(self) -> None:
        init_db(self.config)
        self.assertEqual(len([item for item in self.ids() if item == "radar-psp-library"]), 1)
        delete_radar_search(self.config, "radar-psp-library")
        init_db(self.config)
        self.assertNotIn("radar-psp-library", self.ids())

    def ids(self) -> list[str]:
        return [item["id"] for item in list_radar_searches(self.config)["items"]]


if __name__ == "__main__":
    unittest.main()
