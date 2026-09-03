from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from types import ModuleType
from typing import Any
import unittest

import requests


AGENT_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


# This adapter commit is intentionally standalone. The integrator supplies the
# canonical model in a separate commit; these equivalent test-only dataclasses
# keep this commit independently verifiable before that cherry-pick.
if not (AGENT_DIR / "sources" / "model.py").exists():
    model = ModuleType("sources.model")

    @dataclass(slots=True)
    class AuctionGroup:
        source_id: str
        group_id: str
        title: str
        url: str
        event_at: str = ""
        closing_at: str = ""
        commission_percent: float = 0.0
        currency: str = "UYU"
        location: str = ""
        status: str = "active"
        extra: dict[str, Any] = field(default_factory=dict)

    @dataclass(slots=True)
    class AuctionLot:
        source_id: str
        source_label: str
        group_id: str
        group_label: str
        group_url: str
        lot_id: str
        lot_number: str
        title: str
        description: str
        lot_url: str
        image_url: str = ""
        currency: str = "UYU"
        base_price: float = 0.0
        current_price: float = 0.0
        next_bid: float = 0.0
        commission_percent: float = 0.0
        packaging_cost: float = 0.0
        bid_count: int = 0
        event_at: str = ""
        closing_at: str = ""
        status: str = "active"
        extra: dict[str, Any] = field(default_factory=dict)

    @dataclass(slots=True)
    class SourceScanResult:
        source_id: str
        label: str
        groups: list[AuctionGroup] = field(default_factory=list)
        lots: list[AuctionLot] = field(default_factory=list)
        errors: list[str] = field(default_factory=list)

    model.AuctionGroup = AuctionGroup
    model.AuctionLot = AuctionLot
    model.SourceScanResult = SourceScanResult
    sys.modules["sources.model"] = model


from sources.remotes import (  # noqa: E402
    FEED_URL,
    RemotesSource,
    _number,
)


class FakeResponse:
    def __init__(self, body: bytes | str, status_code: int = 200, chunk_size: int = 31):
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.status_code = status_code
        self.encoding = "utf-8"
        self.chunk_size = chunk_size
        self.closed = False
        self.bytes_yielded = 0

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}",
                response=self,
            )

    def iter_content(self, chunk_size: int = 1):
        del chunk_size
        for offset in range(0, len(self.content), self.chunk_size):
            chunk = self.content[offset : offset + self.chunk_size]
            self.bytes_yielded += len(chunk)
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if url not in self.responses:
            raise AssertionError(f"Unexpected URL: {url}")
        return self.responses[url]


class RemotesSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.feed = (FIXTURES / "remotes_feed.xml").read_bytes()
        cls.metadata_html = (FIXTURES / "remotes_auction.html").read_bytes()

    def collect(self):
        response = FakeResponse(self.feed)
        session = FakeSession({FEED_URL: response})
        source = RemotesSource()
        result = source.collect(session, timeout=7)
        return source, result, session, response

    def test_collect_discovers_all_rss_lots_with_stable_ids(self) -> None:
        _, result, session, response = self.collect()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.source_id, "remotes")
        self.assertTrue(result.discovery_complete)
        self.assertEqual(
            [(receipt.group_id, receipt.status, receipt.lot_count) for receipt in result.receipts],
            [("7544", "complete", 2), ("7440", "complete", 1)],
        )
        self.assertEqual(len(result.groups), 2)
        self.assertEqual(len(result.lots), 3)
        self.assertEqual([lot.lot_id for lot in result.lots], [
            "7544:16",
            "7544:18",
            "7440:14A",
        ])
        self.assertEqual(result.groups[0].event_at, "2026-08-08T21:00:00Z")
        self.assertEqual(result.groups[0].closing_at, "")
        self.assertEqual(result.groups[1].event_at, "")
        self.assertEqual(result.lots[0].event_at, result.groups[0].event_at)
        self.assertEqual(result.lots[0].closing_at, "")
        self.assertEqual(result.lots[0].current_price, 0.0)
        self.assertEqual(result.lots[0].extra["metadata_status"], "not_requested")
        self.assertEqual(
            result.lots[1].image_url,
            "https://www.remotes.com.uy/media/soundic.jpg",
        )
        self.assertEqual(session.calls[0][0], FEED_URL)
        self.assertEqual(session.calls[0][1]["timeout"], 7)
        self.assertTrue(response.closed)

    def test_enrichment_fetches_once_per_candidate_auction(self) -> None:
        source, result, _, _ = self.collect()
        response = FakeResponse(self.metadata_html, chunk_size=19)
        metadata_url = "https://www.remotes.com.uy/participar/remate/7544"
        session = FakeSession({metadata_url: response})

        enriched = source.enrich_lots(session, result.lots[:2], timeout=9)

        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][0], metadata_url)
        self.assertTrue(session.calls[0][1]["stream"])
        self.assertEqual(enriched[0].current_price, 1234.5)
        self.assertEqual(enriched[0].base_price, 0.0)
        self.assertEqual(enriched[0].commission_percent, 20.5)
        self.assertEqual(enriched[0].title, "Cinco cartuchos Atari y compatibles")
        self.assertIn("sin probar", enriched[0].description)
        self.assertEqual(enriched[1].current_price, 400.0)
        self.assertEqual(enriched[1].commission_percent, 20.5)
        self.assertEqual(
            enriched[1].lot_url,
            "https://www.remotes.com.uy/participar/remate/7544?lote=18",
        )
        self.assertEqual(enriched[1].closing_at, "")
        self.assertEqual(enriched[0].extra["metadata_status"], "enriched")
        self.assertEqual(
            enriched[0].extra["packaging_cost_status"],
            "not_exposed_in_safe_metadata",
        )
        self.assertEqual(source.last_enrichment_errors, [])
        self.assertEqual(source.last_enrichment_warnings, [])
        self.assertTrue(response.closed)
        # The reader stops after </head>; the opaque live body is not consumed.
        self.assertLess(response.bytes_yielded, len(response.content))
        serialized_output = repr(enriched)
        for private_value in (
            "runtimeState",
            "nobody@example.invalid",
            "+59800000000",
            "00000000",
            "fake-token-that-must-not-leak",
        ):
            self.assertNotIn(private_value, serialized_output)

    def test_enrichment_failure_preserves_candidate_and_sanitizes_error(self) -> None:
        source, result, _, _ = self.collect()
        metadata_url = "https://www.remotes.com.uy/participar/remate/7440"
        response = FakeResponse("private response body", status_code=503)
        session = FakeSession({metadata_url: response})

        enriched = source.enrich_lots(session, [result.lots[2]])

        self.assertEqual(enriched[0].lot_id, result.lots[2].lot_id)
        self.assertEqual(enriched[0].current_price, 0.0)
        self.assertEqual(enriched[0].extra["metadata_status"], "unavailable")
        self.assertEqual(enriched[0].extra["metadata_error"], "HTTP 503")
        self.assertEqual(source.last_enrichment_errors, ["remate 7440: HTTP 503"])
        self.assertNotIn("private response body", repr(enriched))
        self.assertTrue(response.closed)

    def test_rate_limited_enrichment_is_optional_warning(self) -> None:
        source, result, _, _ = self.collect()
        metadata_url = "https://www.remotes.com.uy/participar/remate/7440"
        response = FakeResponse("private response body", status_code=429)
        session = FakeSession({metadata_url: response})

        enriched = source.enrich_lots(session, [result.lots[2]])

        self.assertEqual(enriched[0].lot_id, result.lots[2].lot_id)
        self.assertEqual(enriched[0].extra["metadata_status"], "unavailable")
        self.assertEqual(enriched[0].extra["metadata_error"], "HTTP 429")
        self.assertEqual(source.last_enrichment_errors, [])
        self.assertEqual(source.last_enrichment_warnings, ["remate 7440: HTTP 429"])
        self.assertNotIn("private response body", repr(enriched))
        self.assertTrue(response.closed)

    def test_missing_optional_products_is_a_warning_not_an_error(self) -> None:
        source, result, _, _ = self.collect()
        metadata_url = "https://www.remotes.com.uy/participar/remate/7544"
        response = FakeResponse(
            """<!doctype html><html><head>
            <meta property="og:description" content="Comisión con impuestos: 20%">
            <script type="application/ld+json">
              {"@context":"https://schema.org/","@type":"ItemList",
               "numberOfItems":0,"itemListElement":[]}
            </script></head><body>opaque runtime data</body></html>""",
            chunk_size=17,
        )
        session = FakeSession({metadata_url: response})

        enriched = source.enrich_lots(session, [result.lots[0]])

        self.assertEqual(enriched[0].lot_id, result.lots[0].lot_id)
        self.assertEqual(enriched[0].current_price, 0.0)
        self.assertEqual(enriched[0].commission_percent, 20.0)
        self.assertEqual(enriched[0].extra["metadata_status"], "lot_not_found")
        self.assertEqual(
            enriched[0].extra["metadata_warnings"],
            ["JSON-LD sin productos"],
        )
        self.assertEqual(source.last_enrichment_errors, [])
        self.assertEqual(
            source.last_enrichment_warnings,
            ["remate 7544: JSON-LD sin productos"],
        )
        self.assertTrue(response.closed)

    def test_invalid_feed_is_reported_without_crashing_other_sources(self) -> None:
        response = FakeResponse("<html>not an RSS feed</html>")
        session = FakeSession({FEED_URL: response})

        result = RemotesSource().collect(session)

        self.assertEqual(result.groups, [])
        self.assertEqual(result.lots, [])
        self.assertEqual(result.errors, ["feed: RSS de Remotes sin canal"])

    def test_locale_number_parser(self) -> None:
        cases = {
            "$ 1.234,50": 1234.5,
            "UYU 2,400": 2400.0,
            "350.000000": 350.0,
            "20,5%": 20.5,
            "1,234.75": 1234.75,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_number(raw), expected)


if __name__ == "__main__":
    unittest.main()
