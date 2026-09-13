from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from server.app import (
    connect_db,
    create_radar_search,
    delete_radar_search,
    deserves_notification,
    enqueue_radar_delivery,
    flush_radar_outbox,
    init_db,
    is_exceptional,
    list_radar_feed,
    list_radar_outbox,
    notify_radar_opportunities,
    radar_email_configured,
    record_radar_decision,
    run_radar_search,
)


def ebay_summary(item_id: str = "v1|1|0", price: str = "50.00", title: str | None = None) -> dict:
    return {
        "itemId": item_id,
        "title": title or "PlayStation 2 Slim tested with OEM controller",
        "itemWebUrl": f"https://www.ebay.com/itm/{item_id}",
        "price": {"value": price, "currency": "USD"},
        "condition": "Pre-owned",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"country": "US"},
        "image": {},
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "0.00", "currency": "USD"}}],
    }


class TestConfig:
    def __init__(self, root: Path, mode: str = "digest_and_alerts") -> None:
        self.data_dir = root / "data"
        self.static_dir = root / "web"
        self.media_dir = self.data_dir / "media"
        self.auction_watch_dir = self.data_dir / "auction-watch"
        self.db_path = self.data_dir / "consolas.sqlite"
        self.max_body_size = 1024 * 1024
        self.ebay_client_id = "client"
        self.ebay_client_secret = "secret"
        self.ebay_environment = "production"
        self.radar_email_mode = mode
        self.radar_email_from = "radar@example.test"
        self.radar_email_to = "fio@example.test"
        self.radar_smtp_host = "smtp.example.test"
        self.radar_smtp_port = 587
        self.radar_smtp_username = "user"
        self.radar_smtp_password = "secret"
        self.radar_smtp_starttls = True


class NotificationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        self.config.static_dir.mkdir(parents=True)
        init_db(self.config)
        delete_radar_search(self.config, "iss-deluxe-snes")
        self.search = create_radar_search(self.config, {"name": "PS2"})["search"]

    def seed(self, summaries: list[dict]) -> None:
        with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=summaries):
            run_radar_search(self.config, self.search["id"])

    def outbox(self) -> list[dict]:
        return list_radar_outbox(self.config)["items"]


class ConfigurationTests(NotificationTestCase):
    def test_a_missing_piece_leaves_email_unconfigured(self) -> None:
        self.assertTrue(radar_email_configured(self.config))
        for field in ("radar_smtp_host", "radar_email_from", "radar_email_to"):
            with self.subTest(field=field):
                original = getattr(self.config, field)
                setattr(self.config, field, "")
                self.assertFalse(radar_email_configured(self.config))
                setattr(self.config, field, original)

    def test_disabled_mode_queues_nothing(self) -> None:
        self.config.radar_email_mode = "disabled"
        self.seed([ebay_summary()])
        result = notify_radar_opportunities(self.config)
        self.assertEqual(result["mode"], "disabled")
        self.assertEqual(result["queued"], 0)
        self.assertEqual(self.outbox(), [])


class WhatDeservesAnAlertTests(unittest.TestCase):
    """Avisar de nuevo por lo mismo entrena a ignorar los avisos."""

    def test_something_never_notified_is_news(self) -> None:
        self.assertEqual(deserves_notification({"priceAmount": 50.0}, None), "nueva")

    def test_the_same_listing_at_the_same_price_is_not_news(self) -> None:
        self.assertEqual(deserves_notification({"priceAmount": 50.0}, {"price": 50.0}), "")

    def test_a_material_drop_is_news_again(self) -> None:
        why = deserves_notification({"priceAmount": 40.0}, {"price": 50.0})
        self.assertIn("bajó 20%", why)

    def test_a_small_drop_is_not_worth_another_email(self) -> None:
        self.assertEqual(deserves_notification({"priceAmount": 48.0}, {"price": 50.0}), "")

    def test_a_price_increase_is_never_news(self) -> None:
        self.assertEqual(deserves_notification({"priceAmount": 80.0}, {"price": 50.0}), "")


class ExceptionalTests(unittest.TestCase):
    """La interrupción se apoya en hechos, no en el score.

    La calibración midió que el score casi no discrimina, así que usarlo como
    disparador de un aviso inmediato sería construir sobre lo más flojo.
    """

    def test_a_deep_discount_against_the_benchmark_interrupts(self) -> None:
        self.assertTrue(is_exceptional({"valuation": {"ratio": 0.4}}))

    def test_a_merely_good_price_waits_for_the_digest(self) -> None:
        self.assertFalse(is_exceptional({"valuation": {"ratio": 0.8}}))

    def test_a_high_score_alone_never_interrupts(self) -> None:
        self.assertFalse(is_exceptional({"band": "ganga", "score": 95}))

    def test_a_listing_without_a_benchmark_never_interrupts(self) -> None:
        self.assertFalse(is_exceptional({"band": "sin-referencia", "valuation": {"ratio": None}}))

    def test_a_steep_drop_on_something_followed_interrupts(self) -> None:
        item = {"band": "razonable", "score": 50, "priceDrop": {"ratio": 0.3},
                "decision": {"decision": "following"}}
        self.assertTrue(is_exceptional(item))

    def test_the_same_drop_on_something_not_followed_does_not(self) -> None:
        self.assertFalse(is_exceptional({"band": "razonable", "score": 50, "priceDrop": {"ratio": 0.3}}))


class DeliveryTests(NotificationTestCase):
    def test_nothing_actionable_sends_no_email(self) -> None:
        result = notify_radar_opportunities(self.config)
        self.assertEqual(result["queued"], 0)
        self.assertEqual(self.outbox(), [], "el silencio es la respuesta, no una entrega vacía")

    def test_a_digest_is_queued_and_sent_once(self) -> None:
        self.seed([ebay_summary(item_id=f"v1|{i}|0", price=str(30 + i)) for i in range(4)])
        with patch("server.app.send_radar_email", return_value=("sent", "sent_via_smtp")) as send:
            result = notify_radar_opportunities(self.config)

        self.assertEqual(result["queued"], 1)
        self.assertEqual(send.call_count, 1)
        delivery = self.outbox()[0]
        self.assertEqual(delivery["status"], "sent")
        self.assertEqual(delivery["kind"], "digest")
        self.assertEqual(len(delivery["listingIds"]), 4)

    def test_the_same_listings_are_not_emailed_twice(self) -> None:
        self.seed([ebay_summary(item_id=f"v1|{i}|0", price=str(30 + i)) for i in range(4)])
        with patch("server.app.send_radar_email", return_value=("sent", "sent_via_smtp")) as send:
            notify_radar_opportunities(self.config)
            second = notify_radar_opportunities(self.config)

        self.assertEqual(second["queued"], 0)
        self.assertEqual(send.call_count, 1)

    def test_a_material_drop_earns_a_new_email(self) -> None:
        summaries = [ebay_summary(item_id=f"v1|{i}|0", price=str(100 + i)) for i in range(4)]
        self.seed(summaries)
        with patch("server.app.send_radar_email", return_value=("sent", "sent_via_smtp")):
            notify_radar_opportunities(self.config)

        summaries[0] = ebay_summary(item_id="v1|0|0", price="60.00")
        self.seed(summaries)
        with patch("server.app.send_radar_email", return_value=("sent", "sent_via_smtp")) as send:
            result = notify_radar_opportunities(self.config)

        self.assertEqual(result["queued"], 1)
        self.assertEqual(send.call_count, 1)

    def test_a_digest_never_carries_more_than_ten(self) -> None:
        self.seed([ebay_summary(item_id=f"v1|{i}|0", price=str(30 + i)) for i in range(18)])
        with patch("server.app.send_radar_email", return_value=("sent", "sent_via_smtp")):
            notify_radar_opportunities(self.config)
        self.assertLessEqual(len(self.outbox()[0]["listingIds"]), 10)

    def test_the_email_says_when_it_was_verified_and_links_out(self) -> None:
        self.seed([ebay_summary()])
        captured = {}

        def capture(config, delivery, text, html):
            captured["text"] = text
            captured["html"] = html
            return "sent", "sent_via_smtp"

        with patch("server.app.send_radar_email", side_effect=capture):
            notify_radar_opportunities(self.config)

        self.assertIn("Verificado", captured["text"])
        self.assertIn("https://www.ebay.com/itm/", captured["text"])
        self.assertIn("Para mí", captured["text"])

    def test_external_titles_are_escaped_in_the_html_body(self) -> None:
        self.seed([ebay_summary(title='PS2 <script>alert(1)</script> tested')])
        captured = {}

        def capture(config, delivery, text, html):
            captured["html"] = html
            return "sent", "sent_via_smtp"

        with patch("server.app.send_radar_email", side_effect=capture):
            notify_radar_opportunities(self.config)

        self.assertNotIn("<script>", captured["html"])
        self.assertIn("&lt;script&gt;", captured["html"])


class ReliabilityTests(NotificationTestCase):
    """Contrato heredado de Auction Watch: un reintento no puede duplicar."""

    def test_the_message_id_is_deterministic(self) -> None:
        self.seed([ebay_summary()])
        feed = list_radar_feed(self.config)["items"]
        first = enqueue_radar_delivery(self.config, "digest", feed, "run-1")
        again = enqueue_radar_delivery(self.config, "digest", feed, "run-1")

        self.assertEqual(first["messageId"], again["messageId"])
        self.assertEqual(len(self.outbox()), 1, "la misma entrega no se encola dos veces")

    def test_an_ambiguous_failure_ends_uncertain_and_is_never_retried(self) -> None:
        self.seed([ebay_summary()])
        with patch("server.app.send_radar_email", return_value=("uncertain", "smtp_delivery_uncertain: timeout")):
            notify_radar_opportunities(self.config)

        delivery = self.outbox()[0]
        self.assertEqual(delivery["status"], "uncertain")

        with patch("server.app.send_radar_email") as send:
            flush_radar_outbox(self.config)
        send.assert_not_called()

    def test_a_rejected_delivery_fails_without_marking_the_listing_notified(self) -> None:
        self.seed([ebay_summary()])
        with patch("server.app.send_radar_email", return_value=("failed", "smtp_rejected: no such mailbox")):
            notify_radar_opportunities(self.config)

        self.assertEqual(self.outbox()[0]["status"], "failed")
        with connect_db(self.config) as conn:
            notified = conn.execute("SELECT COUNT(*) AS total FROM radar_notifications").fetchone()
        self.assertEqual(notified["total"], 0, "lo que no salió no puede contar como avisado")

    def test_sending_is_persisted_before_the_transport_is_called(self) -> None:
        self.seed([ebay_summary()])
        observed = {}

        def capture(config, delivery, text, html):
            with connect_db(config) as conn:
                row = conn.execute("SELECT status, attempts FROM radar_outbox WHERE id = ?", (delivery["id"],)).fetchone()
            observed["status"] = row["status"]
            observed["attempts"] = row["attempts"]
            return "sent", "sent_via_smtp"

        with patch("server.app.send_radar_email", side_effect=capture):
            notify_radar_opportunities(self.config)

        self.assertEqual(observed["status"], "sending", "un crash acá no puede devolver la entrega a la cola")
        self.assertEqual(observed["attempts"], 1)

    def test_a_notification_failure_never_breaks_the_run(self) -> None:
        from server.app import run_due_radar_slots

        self.seed([ebay_summary()])
        with patch("server.app.notify_radar_opportunities", side_effect=RuntimeError("SMTP caído")):
            with patch("radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[ebay_summary()]):
                with patch("server.app.radar_now_local",
                           return_value=datetime(2026, 9, 11, 9, 5, tzinfo=timezone(timedelta(hours=-3)))):
                    outcomes = run_due_radar_slots(self.config)

        self.assertTrue(any(outcome["state"] == "fulfilled" for outcome in outcomes))


class AlertTests(NotificationTestCase):
    def test_an_exceptional_opportunity_gets_its_own_email(self) -> None:
        # Precios muy por debajo de sus pares: el barato queda «ganga» con score alto.
        summaries = [ebay_summary(item_id=f"v1|{i}|0", price=str(200 + i)) for i in range(5)]
        summaries[0] = ebay_summary(item_id="v1|0|0", price="20.00")
        self.seed(summaries)

        kinds = []

        def capture(config, delivery, text, html):
            kinds.append(delivery["kind"])
            return "sent", "sent_via_smtp"

        with patch("server.app.send_radar_email", side_effect=capture):
            result = notify_radar_opportunities(self.config)

        self.assertGreaterEqual(result["alerts"], 1)
        self.assertIn("alert", kinds)

    def test_digest_only_mode_never_interrupts(self) -> None:
        self.config.radar_email_mode = "digest"
        summaries = [ebay_summary(item_id=f"v1|{i}|0", price=str(200 + i)) for i in range(5)]
        summaries[0] = ebay_summary(item_id="v1|0|0", price="20.00")
        self.seed(summaries)

        kinds = []
        with patch("server.app.send_radar_email",
                   side_effect=lambda c, d, t, h: (kinds.append(d["kind"]), ("sent", "ok"))[1]):
            result = notify_radar_opportunities(self.config)

        self.assertEqual(result["alerts"], 0)
        self.assertNotIn("alert", kinds)


if __name__ == "__main__":
    unittest.main()


class TargetPriceAlertTests(unittest.TestCase):
    """El objetivo es el único umbral que escribe el propio usuario.

    Por eso interrumpe: cuando dijo "a 80 puestos acá lo compro", cruzar los 80
    es exactamente el aviso que pidió, sin depender de ninguna heurística.
    """

    def item(self, *, landed: float, target: float | None, **extra: object) -> dict:
        valuation: dict = {}
        if target is not None:
            valuation["target"] = {
                "value": target,
                "meets": landed <= target,
                "landedTotal": landed,
                "gap": round(landed - target, 2),
            }
        valuation.update(extra.pop("valuation", {}))
        return {"priceAmount": extra.pop("priceAmount", landed), "valuation": valuation, **extra}

    def test_crossing_the_target_interrupts(self) -> None:
        self.assertTrue(is_exceptional(self.item(landed=78.0, target=80.0)))

    def test_being_above_the_target_does_not_interrupt(self) -> None:
        self.assertFalse(is_exceptional(self.item(landed=95.0, target=80.0)))

    def test_a_lot_without_a_landed_cost_never_claims_to_meet_the_target(self) -> None:
        item = {"priceAmount": 40.0, "valuation": {"target": {"value": 80.0, "meets": None, "landedTotal": None}}}
        self.assertFalse(is_exceptional(item), "sin costo puesto acá no hay veredicto que interrumpa")

    def test_a_small_drop_that_crosses_the_target_notifies_again(self) -> None:
        # De 82 a 79 es 4%: no mueve la aguja general, pero si tu objetivo eran
        # 80 es justo el momento que estabas esperando.
        why = deserves_notification(self.item(landed=79.0, target=80.0, priceAmount=79.0), {"price": 82.0})
        self.assertIn("objetivo", why)

    def test_a_small_drop_that_stays_above_the_target_stays_quiet(self) -> None:
        why = deserves_notification(self.item(landed=95.0, target=80.0, priceAmount=95.0), {"price": 98.0})
        self.assertEqual(why, "")

    def test_something_already_below_target_does_not_re_notify_forever(self) -> None:
        # Ya estaba por debajo cuando se avisó: cruzar es un evento, no un estado.
        why = deserves_notification(self.item(landed=78.0, target=80.0, priceAmount=78.0), {"price": 79.0})
        self.assertEqual(why, "")

    def test_without_a_target_the_old_rules_still_govern(self) -> None:
        self.assertTrue(is_exceptional({"valuation": {"ratio": 0.4}}))
        self.assertFalse(is_exceptional({"valuation": {"ratio": 0.9}}))
