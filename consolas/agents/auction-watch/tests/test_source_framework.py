from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import types
import unittest
from dataclasses import fields
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, AGENT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from auction_search_config import (  # noqa: E402
    SHARED_KEYWORDS,
    collect_flags,
    compile_patterns,
    matched_terms,
    score_match,
)
from scripts.scan_extra_sources import (  # noqa: E402
    CANONICAL_MATCH_FIELDS,
    canonical_match_row,
    find_hits,
    run_scan,
)
from sources.model import AuctionGroup, AuctionLot, SourceScanResult  # noqa: E402
from sources.registry import CONFIGURED_SOURCES, SourceSpec, configured_sources  # noqa: E402


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "sample_lots.json"


def fixture_lots() -> list[AuctionLot]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [AuctionLot(**item) for item in payload]


class ModelTests(unittest.TestCase):
    def test_exact_model_fields_and_detached_serialization(self) -> None:
        self.assertEqual(
            [item.name for item in fields(AuctionGroup)],
            [
                "source_id",
                "group_id",
                "title",
                "url",
                "event_at",
                "closing_at",
                "commission_percent",
                "currency",
                "location",
                "status",
                "extra",
            ],
        )
        self.assertEqual(
            [item.name for item in fields(AuctionLot)],
            [
                "source_id",
                "source_label",
                "group_id",
                "group_label",
                "group_url",
                "lot_id",
                "lot_number",
                "title",
                "description",
                "lot_url",
                "image_url",
                "currency",
                "base_price",
                "current_price",
                "next_bid",
                "commission_percent",
                "packaging_cost",
                "bid_count",
                "event_at",
                "closing_at",
                "status",
                "extra",
            ],
        )
        self.assertEqual(
            [item.name for item in fields(SourceScanResult)],
                [
                    "source_id",
                    "label",
                    "groups",
                    "lots",
                    "errors",
                    "receipts",
                    "discovery_complete",
                ],
        )

        lot = fixture_lots()[0]
        serialized = lot.to_dict()
        serialized["extra"]["api"] = "changed"
        self.assertEqual(lot.extra, {"api": "feed"})

        row = canonical_match_row(lot)
        assert row is not None
        self.assertEqual(tuple(row), CANONICAL_MATCH_FIELDS)
        self.assertEqual(row["extra_json"], '{"api":"feed"}')
        self.assertEqual(row["total_base_with_commission"], 0)


class SearchConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.patterns = compile_patterns(SHARED_KEYWORDS)

    def hits(self, text: str) -> set[str]:
        return set(matched_terms(text, self.patterns))

    def test_contextual_cartridge_and_network_switch_filtering(self) -> None:
        self.assertIn("cartuchos", self.hits("Doce cartuchos Atari y CCE"))
        self.assertFalse(self.hits("Cartuchos de tinta HP para impresora"))
        self.assertFalse(self.hits("Switch para red TP-Link gigabit de 8 puertos"))
        self.assertFalse(self.hits("Switch HDMI 3x1 con control KOLKE"))
        self.assertIn("switch", self.hits("Nintendo Switch con dock y cable ethernet"))
        self.assertIn("switch", self.hits("Nintendo Switch con adaptador HDMI y control"))

    def test_short_ds_and_pong_terms_reject_obvious_non_gaming_uses(self) -> None:
        self.assertFalse(self.hits("Citroën DS 20 clásico"))
        self.assertIn("ds", self.hits("Nintendo DS con cargador"))
        self.assertFalse(self.hits("Mesa de Pin Pong profesional"))
        self.assertFalse(self.hits("Mesa de ping-pong profesional"))
        self.assertFalse(self.hits("Medallas de Ping Pong y volley"))
        self.assertIn("pong", self.hits("Consola Pong vintage con controles"))

    def test_family_is_a_generic_signal_without_claiming_nintendo_originality(self) -> None:
        self.assertIn("family", self.hits("Family FC Compact con 500 juegos, no se probó"))
        self.assertIn("family computer", self.hits("Nintendo Family Computer Famicom original"))
        self.assertNotIn("family", self.hits("Juego de mesa Family Edition"))

    def test_new_platforms_typos_and_aliases_are_detected_once(self) -> None:
        self.assertEqual(
            self.hits("Play Station 1 PS1 PSX con Dual Shock"),
            {"playstation", "ps1", "psx", "dualshock"},
        )
        self.assertEqual(self.hits("Nintendo Game Boy Advance SP"), {"nintendo", "game boy advance sp"})
        self.assertIn("polystation", self.hits("Consola Polystation con dos joysticks"))
        self.assertIn("neo geo", self.hits("Cartucho Neo Geo original"))
        self.assertIn("nintento", self.hits("Consola Nintento con cartuchos"))
        self.assertIn("playsation", self.hits("Playsation 2 con joystick"))

    def test_ambiguous_platform_names_require_gaming_context(self) -> None:
        self.assertFalse(self.hits("Auto Saturn 1996 en excelente estado"))
        self.assertIn("saturn", self.hits("Sega Saturn con dos controles"))
        self.assertFalse(self.hits("Barco Odyssey para restaurar"))
        self.assertIn("odyssey", self.hits("Consola Odyssey con controles"))

    def test_vintage_terms_and_risk_variants_are_scored(self) -> None:
        text = "Radofin Tele-Sports tipo Pong, a la vista y no se probó"
        hits = matched_terms(text, self.patterns)
        risks, positives = collect_flags(text)
        self.assertTrue({"radofin", "tele-sports", "pong"}.issubset(set(hits)))
        self.assertIn("a la vista", risks)
        self.assertIn("no se probó", risks)
        self.assertLess(
            score_match(text, hits, risks, positives),
            score_match(text, hits, [], positives),
        )
        # The generic scanner keeps a relevant risky lot instead of filtering it out.
        lot = fixture_lots()[0]
        row = canonical_match_row(lot)
        assert row is not None
        self.assertGreaterEqual(int(row["score"]), 1)


class RegistryTests(unittest.TestCase):
    def test_configured_registry_is_lazy_and_selectable(self) -> None:
        self.assertEqual(
            [spec.source_id for spec in CONFIGURED_SOURCES],
            ["remotes", "todoremates", "prado"],
        )
        selected = configured_sources(["prado", "remotes", "prado"])
        self.assertEqual([spec.source_id for spec in selected], ["prado", "remotes"])
        with self.assertRaisesRegex(ValueError, "Unknown auction source"):
            configured_sources(["missing"])

    def test_source_spec_loads_and_validates_adapter(self) -> None:
        module_name = "fixture_source_adapter_for_test"
        module = types.ModuleType(module_name)

        class FixtureSource:
            source_id = "fixture"
            label = "Fixture"

            def collect(self, session, timeout=25):  # pragma: no cover - contract only
                return SourceScanResult("fixture", "Fixture")

        module.FixtureSource = FixtureSource
        sys.modules[module_name] = module
        self.addCleanup(sys.modules.pop, module_name, None)
        adapter = SourceSpec(
            "fixture",
            "Fixture",
            f"{module_name}:FixtureSource",
        ).load()
        self.assertEqual(adapter.source_id, "fixture")


class _DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _SuccessfulAdapter:
    source_id = "fixture"
    label = "Fixture"

    def __init__(self) -> None:
        self.enriched_ids: list[str] = []
        self.last_enrichment_errors: list[str] = []

    def collect(self, session, timeout=25) -> SourceScanResult:
        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=[AuctionGroup("fixture", "g-1", "Remate", "https://example.test/g-1")],
            lots=fixture_lots(),
            errors=["one non-fatal feed item was malformed"],
        )

    def enrich_lots(self, session, lots, timeout=25):
        self.enriched_ids = [lot.lot_id for lot in lots]
        self.last_enrichment_errors = ["arcade-1 detail endpoint timed out"]
        enriched: list[AuctionLot] = []
        for lot in lots:
            if lot.lot_id == "soundic-1":
                data = lot.to_dict()
                data["base_price"] = 350
                data["next_bid"] = 350
                enriched.append(AuctionLot(**data))
        # Returning only successful detail fetches must not drop other candidates.
        return enriched


class _SuccessfulSpec:
    source_id = "fixture"
    label = "Fixture"

    def __init__(self, adapter: _SuccessfulAdapter) -> None:
        self.adapter = adapter

    def load(self):
        return self.adapter


class _FailingSpec:
    source_id = "broken"
    label = "Broken"

    def load(self):
        raise RuntimeError("fixture failure")


class ScannerTests(unittest.TestCase):
    def test_generic_group_name_does_not_turn_every_lot_into_a_match(self) -> None:
        lot = AuctionLot(
            source_id="fixture",
            source_label="Fixture",
            group_id="g",
            group_label="Videojuegos y Tecnología",
            group_url="https://example.test/g",
            lot_id="plotter-1",
            lot_number="1",
            title="Plotter de corte",
            description="Equipo industrial funcionando",
            lot_url="https://example.test/plotter-1",
        )
        self.assertEqual(find_hits(lot), [])

    def test_empty_collection_with_errors_is_a_failed_source(self) -> None:
        class EmptyFailedAdapter:
            source_id = "empty"
            label = "Empty"

            def collect(self, session, timeout=25):
                return SourceScanResult("empty", "Empty", errors=["connection unavailable"])

        class EmptyFailedSpec:
            source_id = "empty"
            label = "Empty"

            def load(self):
                return EmptyFailedAdapter()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            result = run_scan(
                [EmptyFailedSpec()],
                output_dir / "matches.csv",
                output_dir / "status.json",
                session_factory=_DummySession,
                output=io.StringIO(),
            )
            status = json.loads((output_dir / "status.json").read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(status["sources"][0]["status"], "failed")
        self.assertEqual(status["status"], "failed")

    def test_enrichment_warnings_are_recorded_without_degrading_coverage(self) -> None:
        class WarningOnlyAdapter:
            source_id = "fixture"
            label = "Fixture"
            last_enrichment_errors: list[str] = []
            last_enrichment_warnings = ["optional metadata unavailable"]

            def collect(self, session, timeout=25):
                return SourceScanResult(
                    self.source_id,
                    self.label,
                    lots=[fixture_lots()[0]],
                )

            def enrich_lots(self, session, lots, timeout=25):
                return lots

        class WarningOnlySpec:
            source_id = "fixture"
            label = "Fixture"

            def load(self):
                return WarningOnlyAdapter()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            status_path = output_dir / "status.json"
            result = run_scan(
                [WarningOnlySpec()],  # type: ignore[list-item]
                output_dir / "matches.csv",
                status_path,
                session_factory=_DummySession,  # type: ignore[arg-type]
                output=io.StringIO(),
            )
            status = json.loads(status_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(status["status"], "success")
        self.assertEqual(status["sources"][0]["status"], "success")
        self.assertEqual(status["sources"][0]["errors"], [])
        self.assertFalse(status["sources"][0]["inventory_authoritative"])
        self.assertFalse(status["inventory_authoritative"])
        self.assertEqual(
            status["sources"][0]["warnings"],
            ["optional metadata unavailable"],
        )

    def test_partial_failures_enrichment_and_canonical_outputs(self) -> None:
        adapter = _SuccessfulAdapter()
        sessions: list[_DummySession] = []

        def session_factory() -> _DummySession:
            session = _DummySession()
            sessions.append(session)
            return session

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            csv_path = output_dir / "matches.csv"
            status_path = output_dir / "source-status.json"
            stdout = io.StringIO()
            exit_code = run_scan(
                [_SuccessfulSpec(adapter), _FailingSpec()],  # type: ignore[list-item]
                csv_path,
                status_path,
                session_factory=session_factory,  # type: ignore[arg-type]
                output=stdout,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(sessions), 1)
            self.assertTrue(sessions[0].closed)
            # Only active first-pass matches are enriched: no network switch,
            # ink cartridge, or already-closed Atari lot.
            self.assertEqual(adapter.enriched_ids, ["soundic-1", "arcade-1"])

            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual({row["lot_id"] for row in rows}, {"soundic-1", "arcade-1"})
            self.assertEqual(tuple(rows[0]), CANONICAL_MATCH_FIELDS)
            soundic = next(row for row in rows if row["lot_id"] == "soundic-1")
            self.assertEqual(soundic["next_bid"], "350")
            self.assertEqual(soundic["total_next_bid_with_commission"], "470.0")

            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "partial")
            self.assertEqual(status["totals"]["successful_sources"], 1)
            self.assertEqual(status["totals"]["failed_sources"], 1)
            self.assertEqual(status["totals"]["matches"], 2)
            self.assertEqual(
                [item["status"] for item in status["sources"]],
                ["partial", "failed"],
            )
            self.assertIn(
                "arcade-1 detail endpoint timed out",
                status["sources"][0]["errors"],
            )
            self.assertIn("[total] status=partial", stdout.getvalue())

    def test_no_global_match_cap_and_all_sources_failed_exit(self) -> None:
        lots = [
            AuctionLot(
                source_id="many",
                source_label="Many",
                group_id="g",
                group_label="Arcades",
                group_url="https://example.test/g",
                lot_id=str(index),
                lot_number=str(index),
                title=f"Arcade número {index}",
                description="Funciona",
                lot_url=f"https://example.test/{index}",
            )
            for index in range(30)
        ]

        class ManyAdapter:
            source_id = "many"
            label = "Many"

            def collect(self, session, timeout=25):
                return SourceScanResult("many", "Many", lots=lots)

        class ManySpec:
            source_id = "many"
            label = "Many"

            def load(self):
                return ManyAdapter()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            matches_path = output_dir / "many.csv"
            status_path = output_dir / "many.json"
            exit_code = run_scan(
                [ManySpec()],  # type: ignore[list-item]
                matches_path,
                status_path,
                session_factory=_DummySession,  # type: ignore[arg-type]
                output=io.StringIO(),
            )
            self.assertEqual(exit_code, 0)
            with matches_path.open("r", encoding="utf-8", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 30)

            failed_csv = output_dir / "failed.csv"
            failed_json = output_dir / "failed.json"
            failed_exit = run_scan(
                [_FailingSpec()],  # type: ignore[list-item]
                failed_csv,
                failed_json,
                session_factory=_DummySession,  # type: ignore[arg-type]
                output=io.StringIO(),
            )
            self.assertEqual(failed_exit, 1)
            with failed_csv.open("r", encoding="utf-8", newline="") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [])


if __name__ == "__main__":
    unittest.main()
