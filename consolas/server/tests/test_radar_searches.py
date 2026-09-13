from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# `server.app` pone `server/` en sys.path, así que el paquete del radar existe
# bajo un único nombre `radar.*` tanto acá como al correr `python3 server/app.py`.
from server.app import (
    ApiError,
    connect_db,
    create_chasing_game,
    create_radar_search,
    delete_radar_search,
    duplicate_radar_search,
    init_db,
    list_chasing_games,
    list_radar_listings,
    list_radar_searches,
    purge_expired_radar_content,
    radar_search_payload,
    read_state,
    record_radar_decision,
    run_active_radar_searches,
    run_radar_search,
    set_radar_search_status,
    update_radar_search,
)


def ebay_summary(**overrides: object) -> dict:
    """Un `itemSummary` con la forma real de la Browse API de eBay."""
    summary = {
        "itemId": "v1|123456789012|0",
        "title": "PlayStation 2 Slim SCPH-79001 tested with OEM controller",
        "itemWebUrl": "https://www.ebay.com/itm/123456789012",
        "price": {"value": "149.99", "currency": "USD"},
        "condition": "Pre-owned",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"country": "US"},
        "image": {"imageUrl": "https://i.ebayimg.com/example.jpg"},
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "12.00", "currency": "USD"}}],
        "seller": {"username": "retrogames", "feedbackPercentage": "99.4"},
    }
    summary.update(overrides)
    return summary


EBAY_LISTING = ebay_summary()


def patch_ebay(summaries: object = None):
    """Intercepta la red en el borde del adapter, no el dominio.

    Así el test ejercita el parseo real del `itemSummary` y el matcher completo.
    """
    return patch(
        "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
        return_value=[EBAY_LISTING] if summaries is None else summaries,
    )


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


class RadarSearchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)

    def find(self, search_id: str) -> dict:
        return next(item for item in list_radar_searches(self.config)["items"] if item["id"] == search_id)

    def ids(self) -> list[str]:
        return [item["id"] for item in list_radar_searches(self.config)["items"]]


class MigrationTests(RadarSearchTestCase):
    """The legacy Chasing Games table must survive the move without losses."""

    def seed_legacy_rows(self) -> None:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.config.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE chasing_games (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, platform TEXT NOT NULL DEFAULT '',
                  search_query TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'ebay-us',
                  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  last_checked_at TEXT, last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE chasing_game_results (
                  id TEXT PRIMARY KEY, chase_id TEXT NOT NULL, external_id TEXT NOT NULL, title TEXT NOT NULL,
                  price_label TEXT NOT NULL DEFAULT '', condition_label TEXT NOT NULL DEFAULT '',
                  shipping_label TEXT NOT NULL DEFAULT '', location_label TEXT NOT NULL DEFAULT '',
                  listing_type TEXT NOT NULL DEFAULT '', listing_url TEXT NOT NULL,
                  image_url TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1,
                  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                  UNIQUE(chase_id, external_id)
                );
                """
            )
            conn.execute(
                "INSERT INTO chasing_games VALUES ('iss-deluxe-snes','International Superstar Soccer Deluxe',"
                "'SNES','ISS Deluxe SNES','ebay-us',1,'2026-01-01T00:00:00Z','2026-02-02T00:00:00Z',"
                "'2026-02-02T00:00:00Z','')"
            )
            conn.execute(
                "INSERT INTO chasing_games VALUES ('chase-paused','Mappy','NES','Mappy NES','ebay-us',0,"
                "'2026-01-03T00:00:00Z','2026-01-04T00:00:00Z',NULL,'eBay respondió HTTP 500')"
            )
            conn.execute(
                "INSERT INTO chasing_game_results VALUES ('res-1','iss-deluxe-snes','999','ISS Deluxe CIB',"
                "'US $150.00','Pre-owned','','','Compra directa','https://www.ebay.com/itm/999','',1,"
                "'2026-01-05T00:00:00Z','2026-02-02T00:00:00Z')"
            )

    def test_legacy_searches_and_results_survive_the_migration(self) -> None:
        self.seed_legacy_rows()
        init_db(self.config)

        migrated = self.find("iss-deluxe-snes")
        self.assertEqual(migrated["name"], "International Superstar Soccer Deluxe")
        self.assertEqual(migrated["platform"], "SNES")
        self.assertEqual(migrated["searchQuery"], "ISS Deluxe SNES")
        self.assertEqual(migrated["status"], "active")
        self.assertEqual(migrated["createdAt"], "2026-01-01T00:00:00Z")
        self.assertEqual(migrated["lastCheckedAt"], "2026-02-02T00:00:00Z")
        self.assertEqual(migrated["sources"], ["ebay-us"])
        self.assertEqual(migrated["resultCount"], 1)
        self.assertEqual(migrated["results"][0]["title"], "ISS Deluxe CIB")
        self.assertEqual(migrated["results"][0]["firstSeenAt"], "2026-01-05T00:00:00Z")

        paused = self.find("chase-paused")
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["lastError"], "eBay respondió HTTP 500")

    def test_migration_is_idempotent(self) -> None:
        self.seed_legacy_rows()
        init_db(self.config)
        first = list_radar_searches(self.config)["items"]
        init_db(self.config)
        init_db(self.config)
        self.assertEqual(list_radar_searches(self.config)["items"], first)

    def test_a_deleted_search_is_not_resurrected_by_a_restart(self) -> None:
        self.seed_legacy_rows()
        init_db(self.config)
        delete_radar_search(self.config, "chase-paused")
        init_db(self.config)
        self.assertNotIn("chase-paused", self.ids())

    def test_a_fresh_database_seeds_the_first_chase_once(self) -> None:
        init_db(self.config)
        self.assertEqual(self.ids(), ["iss-deluxe-snes"])
        delete_radar_search(self.config, "iss-deluxe-snes")
        init_db(self.config)
        self.assertEqual(self.ids(), [])


class RadarSearchLifecycleTests(RadarSearchTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def test_create_persists_structured_criteria_and_derives_the_query(self) -> None:
        created = create_radar_search(
            self.config,
            {
                "name": "PlayStation 2 lista para usar",
                "platform": "PS2",
                "searchType": "console",
                "priority": "alta",
                "entityType": "console",
                "entityId": "ps2",
                "notes": "Slim o Fat confiable con DualShock 2 OEM",
                "criteria": {
                    "includeTerms": ["tested", "OEM controller"],
                    "excludeTerms": ["parts", "repair", "as-is"],
                    "region": "NTSC-U/C",
                    "condition": "used",
                    "tested": "required",
                    "maxItemPrice": 300,
                    "resultLimit": 8,
                },
            },
        )["search"]

        self.assertEqual(created["status"], "active")
        self.assertEqual(created["searchType"], "console")
        self.assertEqual(created["priority"], "alta")
        self.assertEqual(created["entityId"], "ps2")
        self.assertEqual(created["criteria"]["excludeTerms"], ["parts", "repair", "as-is"])
        self.assertEqual(created["criteria"]["region"], "NTSC-U/C")
        self.assertEqual(created["criteria"]["tested"], "required")
        self.assertEqual(created["criteria"]["maxItemPrice"], 300.0)
        self.assertEqual(created["criteria"]["resultLimit"], 8)
        # El nombre de la búsqueda no viaja a la fuente: una etiqueta en castellano
        # no es un término de búsqueda. Mandan los términos requeridos.
        self.assertEqual(created["searchQuery"], "tested OEM controller PS2")
        self.assertNotIn("lista para usar", created["searchQuery"])

        reloaded = self.find(created["id"])
        self.assertEqual(reloaded["criteria"], created["criteria"])

    def test_criteria_survive_a_reload_and_an_edit_keeps_untouched_fields(self) -> None:
        created = create_radar_search(
            self.config,
            {"name": "Lote PS2", "platform": "PS2", "criteria": {"minLotSize": 6, "excludeTerms": ["karaoke"]}},
        )["search"]

        updated = update_radar_search(
            self.config, created["id"], {"priority": "media-alta", "criteria": {"completeness": "cib"}}
        )["search"]

        self.assertEqual(updated["priority"], "media-alta")
        self.assertEqual(updated["criteria"]["completeness"], "cib")
        self.assertEqual(updated["criteria"]["minLotSize"], 6)
        self.assertEqual(updated["criteria"]["excludeTerms"], ["karaoke"])
        self.assertEqual(self.find(created["id"])["criteria"], updated["criteria"])

    def test_a_handwritten_query_is_not_overwritten_by_an_edit(self) -> None:
        created = create_radar_search(
            self.config, {"name": "Mappy", "platform": "NES", "searchQuery": "Mappy Namco cartridge"}
        )["search"]
        self.assertEqual(created["searchQuery"], "Mappy Namco cartridge")

        updated = update_radar_search(self.config, created["id"], {"priority": "baja"})["search"]
        self.assertEqual(updated["searchQuery"], "Mappy Namco cartridge")

        renamed = update_radar_search(self.config, created["id"], {"name": "Mappy original"})["search"]
        self.assertEqual(renamed["searchQuery"], "Mappy Namco cartridge")

    def test_duplicate_starts_as_a_draft_without_results_or_history(self) -> None:
        with patch_ebay():
            created = create_radar_search(self.config, {"name": "Genesis funcional", "platform": "Genesis"})["search"]
            run_radar_search(self.config, created["id"])

        copy = duplicate_radar_search(self.config, created["id"])["search"]
        self.assertNotEqual(copy["id"], created["id"])
        self.assertEqual(copy["name"], "Genesis funcional (copia)")
        self.assertEqual(copy["status"], "draft")
        self.assertEqual(copy["results"], [])
        self.assertIsNone(copy["lastCheckedAt"])
        self.assertEqual(copy["criteria"], created["criteria"])
        self.assertEqual(self.find(created["id"])["resultCount"], 1)

        second = duplicate_radar_search(self.config, created["id"])["search"]
        self.assertEqual(second["name"], "Genesis funcional (copia 2)")

    def test_a_duplicate_name_and_platform_is_rejected(self) -> None:
        create_radar_search(self.config, {"name": "Dreamcast con VMU", "platform": "Dreamcast"})
        with self.assertRaises(ApiError) as raised:
            create_radar_search(self.config, {"name": "dreamcast con vmu", "platform": "DREAMCAST"})
        self.assertEqual(raised.exception.status, 409)

    def test_invalid_criteria_are_rejected_when_saving(self) -> None:
        for criteria in (
            {"condition": "mint"},
            {"completeness": "almost"},
            {"tested": "maybe"},
            {"maxItemPrice": -5},
            {"maxItemPrice": "cheap"},
            {"minLotSize": 0},
            {"includeTerms": [["nested"]]},
        ):
            with self.subTest(criteria=criteria):
                with self.assertRaises(ApiError) as raised:
                    create_radar_search(self.config, {"name": f"Test {criteria}", "criteria": criteria})
                self.assertEqual(raised.exception.status, 400)

    def test_an_unknown_source_is_rejected(self) -> None:
        with self.assertRaises(ApiError) as raised:
            create_radar_search(self.config, {"name": "Con fuente rara", "sources": ["craigslist"]})
        self.assertEqual(raised.exception.status, 400)


class RadarExecutionTests(RadarSearchTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    @patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[EBAY_LISTING])
    def test_an_active_search_runs_and_normalizes_the_price(self, _fetch) -> None:
        created = create_radar_search(self.config, {"name": "PS2 Slim", "platform": "PS2"})["search"]
        result = run_radar_search(self.config, created["id"])

        self.assertEqual(result["results"], 1)
        stored = self.find(created["id"])["results"][0]
        self.assertEqual(stored["sourceId"], "ebay-us")
        self.assertEqual(stored["priceAmount"], 149.99)
        self.assertEqual(stored["priceCurrency"], "USD")
        self.assertEqual(self.find(created["id"])["lastError"], "")

    @patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[EBAY_LISTING])
    def test_pause_blocks_execution_and_resume_keeps_criteria_and_results(self, _fetch) -> None:
        created = create_radar_search(
            self.config, {"name": "SNES con dos controles", "platform": "SNES", "criteria": {"minLotSize": 2}}
        )["search"]
        run_radar_search(self.config, created["id"])

        paused = set_radar_search_status(self.config, created["id"], "paused")["search"]
        self.assertEqual(paused["status"], "paused")
        self.assertFalse(paused["canRun"])
        self.assertEqual(paused["resultCount"], 1)

        with self.assertRaises(ApiError) as raised:
            run_radar_search(self.config, created["id"])
        self.assertEqual(raised.exception.status, 409)

        resumed = set_radar_search_status(self.config, created["id"], "active")["search"]
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(resumed["criteria"]["minLotSize"], 2)
        self.assertEqual(resumed["resultCount"], 1)
        self.assertEqual(resumed["lastCheckedAt"], paused["lastCheckedAt"])

    @patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[EBAY_LISTING])
    def test_draft_and_archived_searches_refuse_to_run(self, _fetch) -> None:
        for status in ("draft", "archived"):
            with self.subTest(status=status):
                created = create_radar_search(self.config, {"name": f"Propuesta {status}", "status": status})["search"]
                self.assertEqual(created["status"], status)
                self.assertFalse(created["canRun"])
                with self.assertRaises(ApiError) as raised:
                    run_radar_search(self.config, created["id"])
                self.assertEqual(raised.exception.status, 409)

    @patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[EBAY_LISTING])
    def test_the_scheduler_only_runs_approved_active_searches(self, fetch) -> None:
        set_radar_search_status(self.config, "iss-deluxe-snes", "paused")
        suggested = create_radar_search(
            self.config, {"name": "Sugerida por el Master", "status": "draft", "origin": "master"}
        )["search"]
        archived = create_radar_search(self.config, {"name": "Terminada", "status": "archived"})["search"]
        deleted = create_radar_search(self.config, {"name": "Borrada"})["search"]
        delete_radar_search(self.config, deleted["id"])
        active = create_radar_search(self.config, {"name": "Xbox 360 lote físico"})["search"]

        run_active_radar_searches(self.config)

        self.assertEqual(fetch.call_count, 1)
        self.assertIsNotNone(self.find(active["id"])["lastCheckedAt"])
        self.assertIsNone(self.find(suggested["id"])["lastCheckedAt"])
        self.assertIsNone(self.find(archived["id"])["lastCheckedAt"])
        self.assertIsNone(self.find("iss-deluxe-snes")["lastCheckedAt"])

    def test_a_source_that_cannot_execute_is_stored_but_never_run(self) -> None:
        created = create_radar_search(
            self.config, {"name": "Lote por ShopGoodwill", "sources": ["shopgoodwill"]}
        )["search"]

        self.assertEqual(created["sources"], ["shopgoodwill"])
        self.assertEqual(created["executableSources"], [])
        self.assertFalse(created["canRun"])
        with self.assertRaises(ApiError) as raised:
            run_radar_search(self.config, created["id"])
        self.assertEqual(raised.exception.status, 409)

    def test_a_missing_credential_is_reported_without_losing_the_search(self) -> None:
        with self.assertRaisesRegex(ApiError, "credenciales de eBay Developers"):
            run_radar_search(self.config, "iss-deluxe-snes")
        stored = self.find("iss-deluxe-snes")
        self.assertIn("credenciales de eBay Developers", stored["lastError"])
        self.assertEqual(stored["status"], "active")

    def test_every_registered_source_declares_its_capabilities(self) -> None:
        sources = {item["id"]: item for item in list_radar_searches(self.config)["sources"]}
        self.assertIn("ebay-us", sources)
        self.assertTrue(sources["ebay-us"]["executable"])
        self.assertTrue(sources["ebay-us"]["capabilities"]["officialApi"])
        self.assertFalse(sources["mercari-us"]["executable"])
        self.assertTrue(sources["mercari-us"]["unavailableReason"])
        self.assertFalse(sources["shopgoodwill"]["executable"])
        self.assertTrue(sources["shopgoodwill"]["capabilities"]["savedSearchAlerts"])


class RadarIsolationTests(RadarSearchTestCase):
    """The radar owns its tables; it never touches the editable collection state."""

    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def test_deleting_a_search_is_logical_and_keeps_its_results(self) -> None:
        with patch_ebay():
            created = create_radar_search(self.config, {"name": "N64 con joystick OEM"})["search"]
            run_radar_search(self.config, created["id"])

        delete_radar_search(self.config, created["id"])

        self.assertNotIn(created["id"], self.ids())
        with self.assertRaises(ApiError) as raised:
            radar_search_payload(self.config, created["id"])
        self.assertEqual(raised.exception.status, 404)
        with connect_db(self.config) as conn:
            kept = conn.execute(
                "SELECT COUNT(*) AS total FROM radar_search_matches WHERE search_id = ?", (created["id"],)
            ).fetchone()
            listings = conn.execute("SELECT COUNT(*) AS total FROM radar_listings").fetchone()
        self.assertEqual(kept["total"], 1)
        self.assertEqual(listings["total"], 1)

    def test_radar_writes_never_change_the_collection_state(self) -> None:
        before = read_state(self.config)
        with patch_ebay():
            created = create_radar_search(self.config, {"name": "Game Boy Color translúcida"})["search"]
            run_radar_search(self.config, created["id"])
            update_radar_search(self.config, created["id"], {"priority": "alta"})
            duplicate_radar_search(self.config, created["id"])
            set_radar_search_status(self.config, created["id"], "archived")
            delete_radar_search(self.config, created["id"])
        after = read_state(self.config)

        self.assertEqual(before["user"], after["user"])
        self.assertEqual(after["user"]["overridesById"], {})
        self.assertEqual(after["user"]["additionsById"], {})
        self.assertEqual(after["user"]["detailEditsById"], {})

    def test_radar_tables_do_not_touch_auction_watch(self) -> None:
        with patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
            side_effect=RuntimeError("eBay caído"),
        ):
            with self.assertRaises(ApiError):
                run_radar_search(self.config, "iss-deluxe-snes")
        with connect_db(self.config) as conn:
            dismissals = conn.execute("SELECT COUNT(*) AS total FROM auction_watch_dismissals").fetchone()
            requests = conn.execute("SELECT COUNT(*) AS total FROM auction_watch_run_requests").fetchone()
        self.assertEqual(dismissals["total"], 0)
        self.assertEqual(requests["total"], 0)



class ListingDedupeTests(RadarSearchTestCase):
    """PRD §21.7: una publicación que coincide con dos búsquedas se guarda y se
    muestra una sola vez, con las razones de ambas."""

    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def test_one_listing_two_searches_one_row_two_reasons(self) -> None:
        shared = ebay_summary(
            itemId="v1|shared|0",
            title="PlayStation 2 Slim tested with OEM controller and 10 game lot",
            itemWebUrl="https://www.ebay.com/itm/shared",
        )
        console = create_radar_search(
            self.config,
            {"name": "PS2 lista para usar", "platform": "PS2", "criteria": {"includeTerms": "tested"}},
        )["search"]
        lot = create_radar_search(
            self.config,
            {"name": "Lote PS2", "platform": "PS2", "searchType": "lot", "criteria": {"includeTerms": "game lot"}},
        )["search"]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[shared]):
            run_radar_search(self.config, console["id"])
            run_radar_search(self.config, lot["id"])

        payload = list_radar_listings(self.config)
        self.assertEqual(payload["count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["externalId"], "v1|shared|0")
        self.assertEqual(item["matchCount"], 2)
        self.assertEqual(
            sorted(match["searchName"] for match in item["matches"]),
            ["Lote PS2", "PS2 lista para usar"],
        )
        self.assertTrue(all(match["reasons"] for match in item["matches"]))

        with connect_db(self.config) as conn:
            listings = conn.execute("SELECT COUNT(*) AS total FROM radar_listings").fetchone()
            matches = conn.execute("SELECT COUNT(*) AS total FROM radar_search_matches").fetchone()
        self.assertEqual(listings["total"], 1)
        self.assertEqual(matches["total"], 2)

    def test_each_search_still_sees_the_listing_with_its_own_reasons(self) -> None:
        shared = ebay_summary(itemId="v1|shared|0", title="PS2 Slim tested japanese import")
        strict = create_radar_search(
            self.config, {"name": "PS2 NTSC-U", "criteria": {"region": "NTSC-U/C"}}
        )["search"]
        loose = create_radar_search(self.config, {"name": "PS2 cualquier región"})["search"]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[shared]):
            strict_run = run_radar_search(self.config, strict["id"])
            run_radar_search(self.config, loose["id"])

        # La región contradictoria descarta para una búsqueda y no para la otra.
        self.assertEqual(strict_run["results"], 0)
        self.assertEqual(strict_run["rejected"], 1)
        self.assertEqual(self.find(strict["id"])["resultCount"], 0)
        self.assertEqual(self.find(loose["id"])["resultCount"], 1)
        self.assertEqual(list_radar_listings(self.config)["count"], 1)

    def test_excluded_terms_now_actually_reject_listings(self) -> None:
        summaries = [
            ebay_summary(itemId="v1|good|0", title="PS2 Slim tested bundle"),
            ebay_summary(itemId="v1|bad|0", title="PS2 console for parts or repair"),
        ]
        search = create_radar_search(
            self.config, {"name": "PS2 sana", "criteria": {"excludeTerms": "parts, repair"}}
        )["search"]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=summaries):
            result = run_radar_search(self.config, search["id"])

        self.assertEqual(result["results"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(self.find(search["id"])["results"][0]["externalId"], "v1|good|0")

    def test_an_authoritative_rerun_retires_a_listing_that_disappeared(self) -> None:
        first = ebay_summary(itemId="v1|a|0", title="PS2 Slim tested")
        second = ebay_summary(itemId="v1|b|0", title="PS2 Fat tested")
        search = create_radar_search(self.config, {"name": "PS2"})["search"]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[first, second]):
            run_radar_search(self.config, search["id"])
        self.assertEqual(self.find(search["id"])["resultCount"], 2)

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[first]):
            rerun = run_radar_search(self.config, search["id"])

        self.assertTrue(rerun["authoritative"])
        self.assertEqual(self.find(search["id"])["resultCount"], 1)
        # La publicación no se borra: deja de estar activa para esa búsqueda.
        with connect_db(self.config) as conn:
            listings = conn.execute("SELECT COUNT(*) AS total FROM radar_listings").fetchone()
        self.assertEqual(listings["total"], 2)

    def test_a_partial_run_never_retires_anything(self) -> None:
        first = ebay_summary(itemId="v1|a|0", title="PS2 Slim tested")
        second = ebay_summary(itemId="v1|b|0", title="PS2 Fat tested")
        search = create_radar_search(self.config, {"name": "PS2"})["search"]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[first, second]):
            run_radar_search(self.config, search["id"])

        # Un ítem ilegible vuelve la cobertura parcial: no prueba que `b` desapareció.
        with patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
            return_value=[first, ebay_summary(itemId="")],
        ):
            rerun = run_radar_search(self.config, search["id"])

        self.assertFalse(rerun["authoritative"])
        self.assertEqual(self.find(search["id"])["resultCount"], 2)

    def test_a_failed_source_keeps_the_previous_inventory(self) -> None:
        search = create_radar_search(self.config, {"name": "PS2"})["search"]
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[EBAY_LISTING]):
            run_radar_search(self.config, search["id"])

        with patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
            side_effect=RuntimeError("eBay caído"),
        ):
            with self.assertRaises(ApiError) as raised:
                run_radar_search(self.config, search["id"])

        self.assertEqual(raised.exception.status, 502)
        self.assertEqual(self.find(search["id"])["resultCount"], 1)
        self.assertIn("eBay caído", self.find(search["id"])["lastError"])

    def test_a_listing_carries_its_normalized_amounts_and_source_label(self) -> None:
        search = create_radar_search(self.config, {"name": "PS2"})["search"]
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[EBAY_LISTING]):
            run_radar_search(self.config, search["id"])

        item = list_radar_listings(self.config)["items"][0]
        self.assertEqual(item["sourceLabel"], "eBay USA")
        self.assertEqual(item["priceAmount"], 149.99)
        self.assertEqual(item["shippingAmount"], 12.0)
        self.assertEqual(item["totalAmount"], 161.99)
        self.assertEqual(item["listingType"], "Compra directa")
        self.assertEqual(item["sellerLabel"], "99.4% de feedback")

    def test_a_deleted_search_drops_out_of_the_listing_feed(self) -> None:
        search = create_radar_search(self.config, {"name": "PS2"})["search"]
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[EBAY_LISTING]):
            run_radar_search(self.config, search["id"])
        self.assertEqual(list_radar_listings(self.config)["count"], 1)

        delete_radar_search(self.config, search["id"])
        self.assertEqual(list_radar_listings(self.config)["count"], 0)


class ChasingGamesCompatibilityTests(RadarSearchTestCase):
    """The previous surface keeps working while the UI migrates."""

    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def test_the_legacy_listing_still_projects_the_seeded_chase(self) -> None:
        payload = list_chasing_games(self.config)
        self.assertEqual(payload["source"], "eBay Sandbox · datos de prueba")
        self.assertEqual(payload["environment"], "sandbox")
        self.assertEqual(payload["items"][0]["id"], "iss-deluxe-snes")
        self.assertEqual(payload["items"][0]["platform"], "SNES")
        self.assertTrue(payload["items"][0]["enabled"])

    @patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[EBAY_LISTING])
    def test_a_legacy_creation_becomes_a_radar_search(self, _fetch) -> None:
        created = create_chasing_game(self.config, {"title": "Metal Gear Solid", "platform": "PS1"})
        search = self.find(created["id"])

        self.assertEqual(search["name"], "Metal Gear Solid")
        self.assertEqual(search["searchType"], "chase")
        self.assertEqual(search["status"], "active")
        self.assertEqual(search["origin"], "user")
        self.assertEqual(search["sources"], ["ebay-us"])
        self.assertEqual(search["resultCount"], 1)

    def test_an_archived_search_is_hidden_from_the_legacy_listing(self) -> None:
        set_radar_search_status(self.config, "iss-deluxe-snes", "archived")
        self.assertEqual(list_chasing_games(self.config)["items"], [])
        self.assertEqual(self.find("iss-deluxe-snes")["status"], "archived")


if __name__ == "__main__":
    unittest.main()


class SellerIdentityMigrationTests(RadarSearchTestCase):
    """Lo que se le declara a eBay tiene que ser cierto también de lo ya guardado."""

    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def stored_labels(self) -> list[str]:
        with connect_db(self.config) as conn:
            return [str(row["seller_label"]) for row in conn.execute("SELECT seller_label FROM radar_listings")]

    def seed_legacy_listing(self, label: str) -> None:
        """Deja una fila como la habría dejado una versión anterior del add-on.

        Se borra el marcador para que el próximo `init_db` sea el arranque que
        estrena la migración, que es el orden real de una actualización.
        """
        with connect_db(self.config) as conn:
            conn.execute(
                """INSERT INTO radar_listings (id, source_id, external_id, title, listing_url, seller_label,
                     first_seen_at, last_seen_at)
                   VALUES (?, 'ebay-us', ?, 'PS2', 'https://www.ebay.com/itm/1', ?, '2026-01-01T00:00:00Z',
                     '2026-01-01T00:00:00Z')""",
                (f"ebay-us-{abs(hash(label))}", label, label),
            )
            conn.execute("DELETE FROM radar_migrations WHERE id = 'drop_seller_identity_v1'")

    def test_a_stored_username_is_scrubbed_and_the_reputation_kept(self) -> None:
        self.seed_legacy_listing("retrogames · 99.4%")
        init_db(self.config)

        labels = self.stored_labels()
        self.assertEqual(labels, ["99.4% de feedback"])
        self.assertNotIn("retrogames", " ".join(labels))

    def test_a_label_without_identity_is_left_alone(self) -> None:
        self.seed_legacy_listing("99.4% de feedback")
        init_db(self.config)
        self.assertEqual(self.stored_labels(), ["99.4% de feedback"])

    def test_the_scrub_runs_once(self) -> None:
        self.seed_legacy_listing("retrogames · 99.4%")
        init_db(self.config)
        first = self.stored_labels()
        # Un reinicio posterior no vuelve a tocar nada.
        init_db(self.config)
        self.assertEqual(self.stored_labels(), first)
        with connect_db(self.config) as conn:
            marks = conn.execute(
                "SELECT COUNT(*) AS total FROM radar_migrations WHERE id = 'drop_seller_identity_v1'"
            ).fetchone()
        self.assertEqual(marks["total"], 1)


class ContentTtlTests(RadarSearchTestCase):
    """El TTL de licencia se aplica; no alcanza con guardarlo."""

    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def listing_count(self) -> int:
        with connect_db(self.config) as conn:
            return conn.execute("SELECT COUNT(*) AS total FROM radar_listings").fetchone()["total"]

    def match_count(self) -> int:
        with connect_db(self.config) as conn:
            return conn.execute("SELECT COUNT(*) AS total FROM radar_search_matches").fetchone()["total"]

    def store_listing(self, external_id: str, expires_at: str | None) -> None:
        with connect_db(self.config) as conn:
            conn.execute(
                """INSERT INTO radar_listings (id, source_id, external_id, title, listing_url,
                     content_expires_at, first_seen_at, last_seen_at)
                   VALUES (?, 'ebay-us', ?, 'PS2', 'https://www.ebay.com/itm/1', ?,
                     '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
                (f"ebay-us-{external_id}", external_id, expires_at),
            )

    def test_expired_content_is_deleted(self) -> None:
        self.store_listing("vieja", "2020-01-01T00:00:00Z")
        self.assertEqual(self.listing_count(), 1)

        result = purge_expired_radar_content(self.config)

        self.assertEqual(result["purged"], 1)
        self.assertEqual(self.listing_count(), 0)

    def test_content_still_within_its_ttl_survives(self) -> None:
        self.store_listing("fresca", "2099-01-01T00:00:00Z")
        purge_expired_radar_content(self.config)
        self.assertEqual(self.listing_count(), 1)

    def test_content_without_a_ttl_is_left_alone(self) -> None:
        # Filas migradas desde el modelo viejo no declaran vencimiento.
        self.store_listing("sin-ttl", None)
        purge_expired_radar_content(self.config)
        self.assertEqual(self.listing_count(), 1)

    def test_purging_a_listing_takes_its_matches_with_it(self) -> None:
        search = create_radar_search(self.config, {"name": "PS2"})["search"]
        self.store_listing("vieja", "2020-01-01T00:00:00Z")
        with connect_db(self.config) as conn:
            conn.execute(
                """INSERT INTO radar_search_matches (search_id, listing_id, first_seen_at, last_seen_at)
                   VALUES (?, 'ebay-us-vieja', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
                (search["id"],),
            )
        self.assertEqual(self.match_count(), 1)

        purge_expired_radar_content(self.config)

        self.assertEqual(self.match_count(), 0, "una coincidencia no puede apuntar a contenido borrado")

    def test_a_listing_that_keeps_appearing_renews_its_ttl(self) -> None:
        search = create_radar_search(self.config, {"name": "PS2"})["search"]
        with patch_ebay():
            run_radar_search(self.config, search["id"])
        with connect_db(self.config) as conn:
            expires = conn.execute("SELECT content_expires_at FROM radar_listings").fetchone()[0]

        self.assertIsNotNone(expires, "la fuente declara contentTtlSeconds y la fila lo guarda")
        purge_expired_radar_content(self.config)
        self.assertEqual(self.listing_count(), 1, "lo que se acaba de ver no está vencido")

    def test_purging_is_safe_to_repeat(self) -> None:
        self.store_listing("vieja", "2020-01-01T00:00:00Z")
        purge_expired_radar_content(self.config)
        self.assertEqual(purge_expired_radar_content(self.config)["purged"], 0)

    def test_a_decision_on_expired_content_is_purged_with_it(self) -> None:
        # Ni «seguida» ni «descartada» sobreviven a un artículo que ya no
        # existe: no hay nada que seguir ni nada que recordar haber rechazado.
        self.store_listing("vieja", "2020-01-01T00:00:00Z")
        record_radar_decision(self.config, {"listingId": "ebay-us-vieja", "decision": "following"})
        with connect_db(self.config) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM radar_decisions").fetchone()[0], 1)

        purge_expired_radar_content(self.config)

        with connect_db(self.config) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM radar_decisions").fetchone()[0], 0)


class ListingFeedValuationTests(RadarSearchTestCase):
    """El feed deduplicado es el que va a consumir la vista «Para mí»."""

    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def test_the_deduplicated_feed_carries_band_and_score(self) -> None:
        summaries = [
            ebay_summary(itemId=f"v1|{i}|0", title=f"Aladdin SNES cartridge {i}",
                         price={"value": str(price), "currency": "USD"})
            for i, price in enumerate([14.99, 16.99, 19.95, 20.99, 24.99])
        ]
        search = create_radar_search(self.config, {"name": "Aladdin", "platform": "SNES"})["search"]
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=summaries):
            run_radar_search(self.config, search["id"])

        items = list_radar_listings(self.config)["items"]
        self.assertEqual(len(items), 5)
        for item in items:
            with self.subTest(title=item["title"]):
                self.assertIsNotNone(item["band"], "sin banda el feed no puede ordenar decisiones")
                self.assertIsNotNone(item["score"])
                self.assertIn("cost", item["valuation"])

    def test_without_a_catalog_price_the_run_still_produces_a_benchmark(self) -> None:
        """Un juego no tiene precio de catálogo: sus pares son la referencia."""
        summaries = [
            ebay_summary(itemId=f"v1|{i}|0", title=f"Aladdin SNES cartridge {i}",
                         price={"value": str(price), "currency": "USD"})
            for i, price in enumerate([14.99, 16.99, 19.95, 20.99, 24.99])
        ]
        search = create_radar_search(self.config, {"name": "Aladdin"})["search"]
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=summaries):
            run_radar_search(self.config, search["id"])

        cheapest = min(list_radar_listings(self.config)["items"], key=lambda item: item["priceAmount"])
        self.assertEqual(cheapest["valuation"]["benchmark"]["source"], "peer-listings")
        self.assertNotEqual(cheapest["band"], "sin-referencia")

    def test_a_single_listing_gets_no_invented_benchmark(self) -> None:
        search = create_radar_search(self.config, {"name": "Rareza"})["search"]
        with patch_ebay():
            run_radar_search(self.config, search["id"])
        item = list_radar_listings(self.config)["items"][0]
        self.assertEqual(item["band"], "sin-referencia", "una sola publicación no es una mediana")


class SearchQueryDerivationTests(RadarSearchTestCase):
    """El nombre es una etiqueta para el usuario; los términos son la consulta."""

    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def test_a_descriptive_name_never_reaches_the_source(self) -> None:
        search = create_radar_search(
            self.config,
            {"name": "PS2 joyas baratas", "platform": "PS2", "criteria": {"includeTerms": "playstation 2"}},
        )["search"]
        self.assertEqual(search["searchQuery"], "playstation 2 PS2")
        self.assertNotIn("joyas", search["searchQuery"])

    def test_without_required_terms_the_name_is_the_best_available_query(self) -> None:
        search = create_radar_search(self.config, {"name": "Mappy", "platform": "NES"})["search"]
        self.assertEqual(search["searchQuery"], "Mappy NES")

    def test_a_handwritten_query_still_wins(self) -> None:
        search = create_radar_search(
            self.config,
            {"name": "PS2 joyas baratas", "searchQuery": "ps2 game lot", "criteria": {"includeTerms": "ps2"}},
        )["search"]
        self.assertEqual(search["searchQuery"], "ps2 game lot")


class DerivedQueryRegenerationTests(RadarSearchTestCase):
    """Una consulta derivada se regenera; una escrita a mano no se toca.

    La distinción no puede salir de comparar strings: cuando cambia la lógica de
    derivación, toda consulta vieja parece escrita a mano y queda congelada. Eso
    dejó dos búsquedas mandándole `joyas baratas` a eBay después de arreglar el
    generador.
    """

    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def test_a_derived_query_follows_a_change_of_criteria(self) -> None:
        search = create_radar_search(
            self.config, {"name": "PS2 joyas baratas", "platform": "PS2", "criteria": {"includeTerms": "ps2"}}
        )["search"]
        # El generador deduplica: «ps2» y la plataforma «PS2» son el mismo token.
        self.assertEqual(search["searchQuery"], "ps2")

        updated = update_radar_search(
            self.config, search["id"], {"criteria": {"includeTerms": "playstation 2"}}
        )["search"]
        self.assertEqual(updated["searchQuery"], "playstation 2 PS2")

    def test_a_derived_query_survives_a_change_in_the_derivation_logic(self) -> None:
        search = create_radar_search(
            self.config, {"name": "PS2 joyas baratas", "platform": "PS2", "criteria": {"includeTerms": "ps2"}}
        )["search"]
        # Simula una consulta guardada por una versión anterior del generador.
        with connect_db(self.config) as conn:
            conn.execute(
                "UPDATE radar_searches SET search_query = 'PS2 joyas baratas PS2 ps2' WHERE id = ?",
                (search["id"],),
            )

        updated = update_radar_search(self.config, search["id"], {"criteria": {"includeTerms": "ps2"}})["search"]

        self.assertEqual(updated["searchQuery"], "ps2")
        self.assertNotIn("joyas", updated["searchQuery"])

    def test_a_handwritten_query_is_never_regenerated(self) -> None:
        search = create_radar_search(
            self.config,
            {"name": "PS2", "platform": "PS2", "searchQuery": "ps2 slim scph-79001",
             "criteria": {"includeTerms": "ps2"}},
        )["search"]
        self.assertEqual(search["searchQuery"], "ps2 slim scph-79001")

        renamed = update_radar_search(self.config, search["id"], {"name": "PS2 Slim"})["search"]
        self.assertEqual(renamed["searchQuery"], "ps2 slim scph-79001")

        recriteria = update_radar_search(
            self.config, search["id"], {"criteria": {"includeTerms": "playstation 2"}}
        )["search"]
        self.assertEqual(recriteria["searchQuery"], "ps2 slim scph-79001")

    def test_clearing_a_handwritten_query_hands_it_back_to_the_generator(self) -> None:
        search = create_radar_search(
            self.config, {"name": "Mappy", "platform": "NES", "searchQuery": "mappy namco"}
        )["search"]
        self.assertEqual(search["searchQuery"], "mappy namco")

        cleared = update_radar_search(self.config, search["id"], {"searchQuery": ""})["search"]
        self.assertEqual(cleared["searchQuery"], "Mappy NES")



class TargetLandedPriceCriterionTests(RadarSearchTestCase):
    """El objetivo se guarda como criterio pero no filtra nada."""

    def setUp(self) -> None:
        super().setUp()
        init_db(self.config)

    def test_it_is_stored_and_returned(self) -> None:
        created = create_radar_search(
            self.config,
            {"name": "PS2 con objetivo", "criteria": {"targetLandedPrice": 80}},
        )["search"]
        self.assertEqual(created["criteria"]["targetLandedPrice"], 80.0)

    def test_a_search_without_one_simply_has_none(self) -> None:
        created = create_radar_search(self.config, {"name": "PS2 sin objetivo"})["search"]
        self.assertIsNone(created["criteria"]["targetLandedPrice"])

    def test_it_never_rejects_a_listing(self) -> None:
        # A diferencia de maxItemPrice, que sí bloquea: una publicación cara
        # tiene que seguir guardándose, porque es la que después puede bajar.
        from radar.matching import evaluate_match
        from radar.model import MarketplaceListing

        listing = MarketplaceListing(
            source_id="ebay-us", external_id="1", title="Sony PlayStation 2 PS2 Console Tested",
            listing_url="https://www.ebay.com/itm/1", price_amount=400.0, price_currency="USD",
        )
        verdict = evaluate_match(listing, {"targetLandedPrice": 80.0, "includeTerms": []})
        self.assertTrue(verdict.matched, verdict.blockers)
