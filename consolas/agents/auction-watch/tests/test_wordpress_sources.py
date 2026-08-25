from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

import requests


AGENT_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from sources.prado import PradoSource, _parse_product_page as parse_prado_page  # noqa: E402
from sources.todoremates import TodoRematesSource  # noqa: E402


class FakeResponse:
    def __init__(
        self,
        *,
        payload: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.text = text
        self.headers = headers or {}
        self.error = error

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.handler(url, kwargs)


def fixture_json(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TodoRematesTests(unittest.TestCase):
    def test_api_discovery_overrides_incorrect_store_currency(self) -> None:
        fixture = fixture_json("todoremates_api.json")

        def handler(url: str, kwargs: dict[str, Any]) -> FakeResponse:
            if url.endswith("/wp-json/wp/v2/remate"):
                return FakeResponse(
                    payload=fixture["terms"],
                    headers={"X-WP-TotalPages": "1"},
                )
            return FakeResponse(payload=fixture["products"], headers={"X-WP-TotalPages": "1"})

        session = FakeSession(handler)
        result = TodoRematesSource().collect(session, timeout=3)

        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.groups), 1)
        self.assertEqual(len(result.lots), 1)
        lot = result.lots[0]
        self.assertEqual(lot.source_id, "todoremates")
        self.assertEqual(lot.currency, "UYU")
        self.assertEqual(lot.base_price, 3900)
        self.assertEqual(lot.lot_number, "080")
        self.assertEqual(lot.description, "2 Wii originales & accesorios usados. Funcionando.")
        self.assertEqual(lot.extra["api_currency_code"], "USD")
        self.assertTrue(all("/wp-json/" in url for url, _ in session.calls))

    def test_enrichment_reads_custom_auction_attributes(self) -> None:
        fixture = fixture_json("todoremates_api.json")
        api_session = FakeSession(
            lambda url, kwargs: FakeResponse(
                payload=(
                    fixture["terms"]
                    if url.endswith("/remate")
                    else fixture["products"]
                ),
                headers={"X-WP-TotalPages": "1"},
            )
        )
        lot = TodoRematesSource().collect(api_session).lots[0]
        page_session = FakeSession(
            lambda url, kwargs: FakeResponse(text=fixture_text("todoremates_product.html"))
        )

        enriched = TodoRematesSource().enrich_lots(page_session, [lot])[0]

        self.assertEqual(enriched.base_price, 3900)
        self.assertEqual(enriched.current_price, 4300)
        self.assertEqual(enriched.next_bid, 4700)
        self.assertEqual(enriched.event_at, "2026-08-12T21:00:00-03:00")
        self.assertEqual(enriched.closing_at, "2026-08-12T22:19:00-03:00")
        self.assertEqual(enriched.status, "active")
        self.assertFalse(enriched.extra["api_price_unverified"])
        self.assertEqual(len(page_session.calls), 1)

    def test_discovery_failure_is_reported_without_raising(self) -> None:
        session = FakeSession(
            lambda url, kwargs: FakeResponse(error=requests.ConnectionError("offline"))
        )
        result = TodoRematesSource().collect(session)
        self.assertEqual(result.groups, [])
        self.assertEqual(result.lots, [])
        self.assertEqual(len(result.errors), 1)

    def test_enrichment_failure_keeps_candidate_and_reports_partial_error(self) -> None:
        fixture = fixture_json("todoremates_api.json")
        api_session = FakeSession(
            lambda url, kwargs: FakeResponse(
                payload=fixture["terms"] if url.endswith("/remate") else fixture["products"],
                headers={"X-WP-TotalPages": "1"},
            )
        )
        lot = TodoRematesSource().collect(api_session).lots[0]
        source = TodoRematesSource()
        failed = source.enrich_lots(
            FakeSession(
                lambda url, kwargs: FakeResponse(error=requests.ConnectionError("offline"))
            ),
            [lot],
        )

        self.assertEqual(failed[0].lot_id, lot.lot_id)
        self.assertEqual(len(source.last_enrichment_errors), 1)
        self.assertIn("offline", failed[0].extra["enrichment_error"])


class PradoTests(unittest.TestCase):
    def test_store_api_discovers_only_live_auction_products(self) -> None:
        payload = fixture_json("prado_api.json")["products"]
        session = FakeSession(
            lambda url, kwargs: FakeResponse(
                payload=payload,
                headers={"X-WP-TotalPages": "1"},
            )
        )

        result = PradoSource().collect(session, timeout=3)

        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.groups), 1)
        self.assertEqual(len(result.lots), 1)
        lot = result.lots[0]
        self.assertEqual(lot.title, "Lote #056 – Consola Radofin Tele-Sports")
        self.assertEqual(lot.group_label, "Vintage & Coleccionables")
        self.assertEqual(lot.current_price, 1000)
        self.assertEqual(lot.commission_percent, 18.3)
        self.assertTrue(all("/wp-json/" in url for url, _ in session.calls))

    def test_enrichment_ignores_expired_translation_in_javascript(self) -> None:
        parsed = parse_prado_page(fixture_text("prado_product.html"))
        self.assertEqual(parsed["status"], "active")
        self.assertEqual(parsed["current_price"], 1000)
        self.assertEqual(parsed["next_bid"], 1200)
        self.assertEqual(parsed["closing_at"], "2026-08-13T19:03:00-03:00")

    def test_expired_root_class_closes_lot(self) -> None:
        parsed = parse_prado_page(
            fixture_text("prado_product.html").replace(
                "uwa_auction_status_live", "uwa_auction_status_expired"
            )
        )
        self.assertEqual(parsed["status"], "closed")


if __name__ == "__main__":
    unittest.main()
