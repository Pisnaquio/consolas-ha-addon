from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from server.app import (
    RADAR_SLOT_KEYS,
    connect_db,
    create_radar_search,
    delete_radar_search,
    init_db,
    list_radar_runs,
    radar_due_slots,
    radar_next_slot,
    run_due_radar_slots,
    run_radar_search,
    set_radar_search_status,
    start_manual_radar_run,
    update_radar_search,
)

MONTEVIDEO = timezone(timedelta(hours=-3))


def ebay_summary(**overrides: object) -> dict:
    summary = {
        "itemId": "v1|123456789012|0",
        "title": "PlayStation 2 Slim SCPH-79001 tested with OEM controller",
        "itemWebUrl": "https://www.ebay.com/itm/123456789012",
        "price": {"value": "149.99", "currency": "USD"},
        "condition": "Pre-owned",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"country": "US"},
        "image": {},
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "12.00", "currency": "USD"}}],
    }
    summary.update(overrides)
    return summary


def patch_ebay(summaries: object = None):
    return patch(
        "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
        return_value=[ebay_summary()] if summaries is None else summaries,
    )


def at(hour: int, minute: int = 0, day: int = 11) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=MONTEVIDEO)


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


class SchedulerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)
        delete_radar_search(self.config, "iss-deluxe-snes")

    def make_search(self, name: str, **payload: object) -> dict:
        return create_radar_search(self.config, {"name": name, **payload})["search"]

    def slot_rows(self) -> dict[str, dict]:
        with connect_db(self.config) as conn:
            return {
                str(row["slot_key"]): {
                    "state": row["state"],
                    "runId": row["fulfilled_by_run_id"],
                    "detail": row["detail"],
                }
                for row in conn.execute("SELECT * FROM radar_schedule_slots").fetchall()
            }

    def runs(self) -> list[dict]:
        return list_radar_runs(self.config)["runs"]


class SlotArithmeticTests(SchedulerTestCase):
    def test_the_three_daily_slots_are_the_ones_the_prd_closed(self) -> None:
        self.assertEqual(RADAR_SLOT_KEYS, ("morning", "afternoon", "night"))
        labels = [slot["label"] for slot in list_radar_runs(self.config)["slots"]]
        self.assertEqual(labels, ["09:00", "16:00", "22:30"])

    def test_only_slots_already_past_are_due(self) -> None:
        self.assertEqual(radar_due_slots(at(8, 59)), [])
        self.assertEqual(radar_due_slots(at(9, 0)), ["morning"])
        self.assertEqual(radar_due_slots(at(16, 30)), ["morning", "afternoon"])
        self.assertEqual(radar_due_slots(at(23, 0)), ["morning", "afternoon", "night"])

    def test_the_next_slot_rolls_over_to_tomorrow_after_the_last_one(self) -> None:
        self.assertEqual(radar_next_slot(at(8, 0))["label"], "09:00")
        self.assertEqual(radar_next_slot(at(10, 0))["label"], "16:00")
        upcoming = radar_next_slot(at(23, 0))
        self.assertEqual(upcoming["label"], "09:00")
        self.assertTrue(upcoming["at"].startswith("2026-09-12"))


class OneSlotOneScanTests(SchedulerTestCase):
    """Contrato heredado de Auction Watch: un slot genera como máximo un scan."""

    def test_a_slot_scans_once_no_matter_how_often_the_scheduler_ticks(self) -> None:
        self.make_search("PS2")
        with patch_ebay() as fetch, patch("server.app.radar_now_local", return_value=at(9, 5)):
            first = run_due_radar_slots(self.config)
            second = run_due_radar_slots(self.config)
            third = run_due_radar_slots(self.config)

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([outcome["slotKey"] for outcome in first], ["morning"])
        self.assertEqual(second, [])
        self.assertEqual(third, [])
        self.assertEqual(len(self.runs()), 1)

    def test_a_restart_does_not_rerun_a_fulfilled_slot(self) -> None:
        self.make_search("PS2")
        with patch_ebay() as fetch, patch("server.app.radar_now_local", return_value=at(9, 5)):
            run_due_radar_slots(self.config)
            init_db(self.config)  # reinicio del add-on sobre la misma base
            run_due_radar_slots(self.config)

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.slot_rows()["morning"]["state"], "fulfilled")

    def test_each_slot_of_the_day_gets_its_own_scan(self) -> None:
        self.make_search("PS2")
        with patch_ebay() as fetch:
            for moment in (at(9, 5), at(16, 5), at(22, 35)):
                with patch("server.app.radar_now_local", return_value=moment):
                    run_due_radar_slots(self.config)

        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(sorted(self.slot_rows()), ["afternoon", "morning", "night"])
        self.assertEqual(len(self.runs()), 3)

    def test_tomorrow_starts_a_fresh_set_of_slots(self) -> None:
        self.make_search("PS2")
        with patch_ebay() as fetch:
            with patch("server.app.radar_now_local", return_value=at(9, 5, day=11)):
                run_due_radar_slots(self.config)
            with patch("server.app.radar_now_local", return_value=at(9, 5, day=12)):
                run_due_radar_slots(self.config)
        self.assertEqual(fetch.call_count, 2)


class MissedSlotTests(SchedulerTestCase):
    """Volver de un apagón no puede disparar tres scans seguidos."""

    def test_only_the_latest_overdue_slot_runs_and_the_rest_are_skipped(self) -> None:
        self.make_search("PS2")
        with patch_ebay() as fetch, patch("server.app.radar_now_local", return_value=at(23, 0)):
            outcomes = run_due_radar_slots(self.config)

        self.assertEqual(fetch.call_count, 1)
        states = self.slot_rows()
        self.assertEqual(states["morning"]["state"], "skipped")
        self.assertEqual(states["afternoon"]["state"], "skipped")
        self.assertEqual(states["night"]["state"], "fulfilled")
        self.assertIn("mientras el add-on no corría", states["morning"]["detail"])
        self.assertEqual([outcome["state"] for outcome in outcomes], ["skipped", "skipped", "fulfilled"])

    def test_a_skipped_slot_is_never_retried_later_in_the_day(self) -> None:
        self.make_search("PS2")
        with patch_ebay() as fetch:
            with patch("server.app.radar_now_local", return_value=at(23, 0)):
                run_due_radar_slots(self.config)
            with patch("server.app.radar_now_local", return_value=at(23, 30)):
                run_due_radar_slots(self.config)
        self.assertEqual(fetch.call_count, 1)


class ManualRunTests(SchedulerTestCase):
    def test_a_manual_run_scans_every_active_search_and_records_a_run(self) -> None:
        self.make_search("PS2")
        self.make_search("Genesis")
        with patch_ebay() as fetch:
            result = start_manual_radar_run(self.config)
            self.wait_for_idle()

        self.assertFalse(result["reused"])
        self.assertEqual(fetch.call_count, 2)
        run = self.runs()[0]
        self.assertEqual(run["kind"], "manual")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["searchesTotal"], 2)
        self.assertEqual(run["searchesOk"], 2)

    def test_a_second_request_reuses_the_run_already_in_flight(self) -> None:
        self.make_search("PS2")
        release = threading.Event()

        def slow_fetch(*_args, **_kwargs):
            release.wait(timeout=5)
            return [ebay_summary()]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", side_effect=slow_fetch):
            first = start_manual_radar_run(self.config)
            second = start_manual_radar_run(self.config)
            release.set()
            self.wait_for_idle()

        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["run"]["id"], second["run"]["id"])
        self.assertEqual(len(self.runs()), 1)

    def finish_manual_run_at(self, moment: datetime) -> None:
        """Fija cuándo terminó la corrida manual, para medir la ventana de frescura."""
        stamp = moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        with connect_db(self.config) as conn:
            conn.execute("UPDATE radar_runs SET finished_at = ? WHERE kind = 'manual'", (stamp,))

    def test_a_fresh_manual_run_satisfies_the_upcoming_slot(self) -> None:
        self.make_search("PS2")
        with patch_ebay() as fetch:
            start_manual_radar_run(self.config)
            self.wait_for_idle()
            manual_calls = fetch.call_count
            # Terminó cinco minutos antes del slot de las 09:00.
            self.finish_manual_run_at(at(8, 55))
            with patch("server.app.radar_now_local", return_value=at(9, 5)):
                run_due_radar_slots(self.config)

        self.assertEqual(fetch.call_count, manual_calls, "el slot no debe volver a escanear")
        slot = self.slot_rows()["morning"]
        self.assertEqual(slot["state"], "fulfilled")
        self.assertIn("corrida manual reciente", slot["detail"])

    def test_a_manual_run_older_than_the_window_does_not_satisfy_the_slot(self) -> None:
        self.make_search("PS2")
        with patch_ebay() as fetch:
            start_manual_radar_run(self.config)
            self.wait_for_idle()
            manual_calls = fetch.call_count
            # Terminó dos horas antes: la ventana de frescura ya venció.
            self.finish_manual_run_at(at(7, 0))
            with patch("server.app.radar_now_local", return_value=at(9, 5)):
                run_due_radar_slots(self.config)

        self.assertGreater(fetch.call_count, manual_calls)
        slot = self.slot_rows()["morning"]
        self.assertEqual(slot["state"], "fulfilled")
        self.assertNotIn("corrida manual reciente", slot["detail"])

    def test_a_manual_run_after_the_slot_does_not_satisfy_it_retroactively(self) -> None:
        self.make_search("PS2")
        with patch_ebay() as fetch:
            start_manual_radar_run(self.config)
            self.wait_for_idle()
            manual_calls = fetch.call_count
            # Terminó después del slot: no puede consumir una ventana ya vencida.
            self.finish_manual_run_at(at(9, 30))
            with patch("server.app.radar_now_local", return_value=at(9, 35)):
                run_due_radar_slots(self.config)

        self.assertGreater(fetch.call_count, manual_calls)

    def wait_for_idle(self, timeout: float = 5.0) -> None:
        for thread in threading.enumerate():
            if thread.name.startswith("radar-run-"):
                thread.join(timeout)


class PerSearchScheduleTests(SchedulerTestCase):
    def test_a_search_runs_only_in_the_slots_it_chose(self) -> None:
        morning_only = self.make_search("Sólo a la mañana", slots=["morning"])
        always = self.make_search("Siempre")

        self.assertEqual(morning_only["slots"], ["morning"])
        self.assertEqual(always["slots"], ["morning", "afternoon", "night"])

        with patch_ebay() as fetch:
            with patch("server.app.radar_now_local", return_value=at(9, 5)):
                run_due_radar_slots(self.config)
            self.assertEqual(fetch.call_count, 2)
            with patch("server.app.radar_now_local", return_value=at(16, 5)):
                run_due_radar_slots(self.config)
            self.assertEqual(fetch.call_count, 3, "a la tarde sólo corre la que eligió todos los slots")

    def test_an_existing_search_keeps_every_slot_after_the_migration(self) -> None:
        search = self.make_search("PS2")
        with connect_db(self.config) as conn:
            conn.execute("UPDATE radar_searches SET slots_json = '' WHERE id = ?", (search["id"],))
        with patch_ebay() as fetch, patch("server.app.radar_now_local", return_value=at(16, 5)):
            run_due_radar_slots(self.config)
        self.assertEqual(fetch.call_count, 1)

    def test_an_unknown_slot_is_rejected(self) -> None:
        with self.assertRaises(Exception) as raised:
            self.make_search("Rara", slots=["midnight"])
        self.assertEqual(getattr(raised.exception, "status", None), 400)

    def test_slots_survive_an_edit_that_does_not_mention_them(self) -> None:
        search = self.make_search("PS2", slots=["night"])
        updated = update_radar_search(self.config, search["id"], {"priority": "alta"})["search"]
        self.assertEqual(updated["slots"], ["night"])


class LifecycleTests(SchedulerTestCase):
    def test_the_scheduler_never_runs_drafts_paused_or_deleted_searches(self) -> None:
        active = self.make_search("Activa")
        self.make_search("Propuesta", status="draft")
        paused = self.make_search("Pausada")
        set_radar_search_status(self.config, paused["id"], "paused")
        removed = self.make_search("Borrada")
        delete_radar_search(self.config, removed["id"])

        with patch_ebay() as fetch, patch("server.app.radar_now_local", return_value=at(9, 5)):
            run_due_radar_slots(self.config)

        self.assertEqual(fetch.call_count, 1)
        run = self.runs()[0]
        self.assertEqual(run["searchesTotal"], 1)
        self.assertEqual(run["receipts"][0]["searchId"], active["id"])


class ReceiptTests(SchedulerTestCase):
    def test_a_run_records_one_receipt_per_search_and_source(self) -> None:
        self.make_search("PS2")
        self.make_search("Genesis")
        with patch_ebay(), patch("server.app.radar_now_local", return_value=at(9, 5)):
            run_due_radar_slots(self.config)

        run = self.runs()[0]
        self.assertEqual(len(run["receipts"]), 2)
        for receipt in run["receipts"]:
            self.assertEqual(receipt["sourceId"], "ebay-us")
            self.assertEqual(receipt["sourceLabel"], "eBay USA")
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["matchedCount"], 1)
            self.assertEqual(receipt["errors"], [])

    def test_a_failing_search_degrades_the_run_without_stopping_the_others(self) -> None:
        self.make_search("Primera")
        self.make_search("Segunda")
        calls = {"n": 0}

        def flaky(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("eBay caído")
            return [ebay_summary()]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", side_effect=flaky):
            with patch("server.app.radar_now_local", return_value=at(9, 5)):
                run_due_radar_slots(self.config)

        run = self.runs()[0]
        self.assertEqual(run["status"], "degraded")
        self.assertEqual(run["searchesOk"], 1)
        self.assertEqual(run["searchesFailed"], 1)
        failed = [receipt for receipt in run["receipts"] if receipt["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("eBay caído", failed[0]["errors"][0])

    def test_a_run_where_every_search_fails_is_reported_as_failed(self) -> None:
        self.make_search("Primera")
        self.make_search("Segunda")
        with patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries",
            side_effect=RuntimeError("eBay caído"),
        ):
            with patch("server.app.radar_now_local", return_value=at(9, 5)):
                run_due_radar_slots(self.config)

        run = self.runs()[0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["searchesOk"], 0)
        self.assertEqual(run["searchesFailed"], 2)

    def test_the_run_feed_reports_today_slots_and_the_next_one(self) -> None:
        self.make_search("PS2")
        with patch_ebay(), patch("server.app.radar_now_local", return_value=at(9, 5)):
            run_due_radar_slots(self.config)
            payload = list_radar_runs(self.config)

        states = {slot["slotKey"]: slot["state"] for slot in payload["slots"]}
        self.assertEqual(states, {"morning": "fulfilled", "afternoon": "pending", "night": "pending"})
        self.assertEqual(payload["nextSlot"]["label"], "16:00")
        self.assertEqual(payload["timezone"], "America/Montevideo")
        self.assertIsNone(payload["current"])


class ConcurrencyTests(SchedulerTestCase):
    def test_a_manual_run_and_the_scheduler_never_scan_the_same_search_twice(self) -> None:
        search = self.make_search("PS2")
        entered = threading.Event()
        release = threading.Event()

        def slow_fetch(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=5)
            return [ebay_summary()]

        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", side_effect=slow_fetch) as fetch:
            first = threading.Thread(target=lambda: run_radar_search(self.config, search["id"]))
            first.start()
            self.assertTrue(entered.wait(timeout=5))

            done = threading.Event()

            def second_run() -> None:
                run_radar_search(self.config, search["id"])
                done.set()

            second = threading.Thread(target=second_run)
            second.start()
            # La segunda corrida queda esperando el lock, no llamando a la fuente.
            self.assertFalse(done.wait(timeout=0.3))
            self.assertEqual(fetch.call_count, 1)

            release.set()
            first.join(timeout=5)
            second.join(timeout=5)

        self.assertEqual(fetch.call_count, 2, "la segunda corrida sigue después, no en paralelo")


if __name__ == "__main__":
    unittest.main()
