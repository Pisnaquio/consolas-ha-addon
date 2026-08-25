from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, AGENT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from scripts.run_watch import (  # noqa: E402
    MatchView,
    WatchHit,
    build_email_body,
    build_mailapp_rich_paragraphs,
    build_newsletter_html,
    discard_action_url,
    filter_dismissed_match_views,
    group_match_views_by_closing_day,
    load_state,
    match_view_from_row,
    parse_dismissal_payload,
    publish_web_snapshot,
    required_email_delivery_failed,
    resolve_mail_image_urls,
    refresh_latest_matches_mirror,
    should_export_web_snapshot,
    STATE_SCHEMA_VERSION,
)
import scripts.run_watch as run_watch_module  # noqa: E402
from scripts.export_web_snapshot import (  # noqa: E402
    build_publication_lifecycle,
    build_run_issues,
    enrich_featured_identity,
    export_snapshot,
)


class ReportingTests(unittest.TestCase):
    @staticmethod
    def sample_match(*, source_id: str = "remotes", lot_id: str = "lot-42") -> MatchView:
        return MatchView(
            source_id=source_id,
            source_label="Remotes",
            lot_id=lot_id,
            group_id="group-7",
            title="Consola retro con controles",
            description="Consola retro con controles",
            lot_url="https://example.test/lots/42",
            group_url="https://example.test/groups/7",
            image_url="",
            score=31,
            matched_keywords="consola, controles",
            risk_flags="",
            positive_flags="completo",
            price_label="Base c/cargos: $1.250",
            timing_label="cierra",
            closing_at_raw="2026-08-06T18:00:00-03:00",
            closing_at_display="jue 06 ago 18:00",
        )

    def test_all_active_matches_are_rendered_without_a_global_cap(self) -> None:
        matches = [
            MatchView(
                source_id=("remotes", "todoremates", "prado")[index % 3],
                source_label=("Remotes", "TodoRemates", "Prado Subastas")[index % 3],
                lot_id=str(index),
                group_id=f"g-{index % 3}",
                title=f"Oportunidad única {index}",
                description=f"Oportunidad única {index}",
                lot_url=f"https://example.test/lots/{index}",
                group_url=f"https://example.test/groups/{index % 3}",
                image_url="",
                score=20 + index,
                matched_keywords="arcade",
                risk_flags="no probado" if index == 29 else "",
                positive_flags="",
                price_label=f"Base c/cargos: ${1000 + index}",
                timing_label="cierra",
                closing_at_raw="2026-08-06T18:00:00-03:00",
                closing_at_display="jue 06 ago 18:00",
            )
            for index in range(30)
        ]
        counts: dict[str, object] = {"total_matches": len(matches)}

        plain = build_email_body(
            "test-run",
            "success",
            counts,
            Path("/tmp/summary.md"),
            matches,
            [],
        )
        html = build_newsletter_html("success", counts, matches, [])
        rich_text = "\n".join(
            str(item["text"])
            for item in build_mailapp_rich_paragraphs(
                "test-run",
                "success",
                counts,
                Path("/tmp/summary.md"),
                matches,
                [],
            )
        )

        for index in range(30):
            title = f"Oportunidad única {index}"
            self.assertIn(title, plain)
            self.assertIn(title, html)
            self.assertIn(title, rich_text)
        self.assertIn("⚠️ no probado", html)
        self.assertIn("⚠️ no probado", rich_text)

    def test_mail_groups_matches_by_closing_day(self) -> None:
        reference = run_watch_module.parse_source_datetime("2026-08-10T09:00:00-03:00", None)
        today = replace(
            self.sample_match(source_id="remotes", lot_id="today"),
            closing_at_raw="2026-08-10T20:00:00-03:00",
        )
        tomorrow = replace(
            self.sample_match(source_id="prado", lot_id="tomorrow"),
            closing_at_raw="2026-08-11T20:00:00-03:00",
        )
        unknown = replace(self.sample_match(source_id="bavastro", lot_id="unknown"), closing_at_raw="")
        counts: dict[str, object] = {"total_matches": 3}

        groups = group_match_views_by_closing_day([tomorrow, unknown, today], reference=reference)
        self.assertEqual([label for label, _items in groups], [
            "Cierra hoy — lun 10 ago",
            "Cierra mañana — mar 11 ago",
            "Sin fecha de cierre confirmada",
        ])

        with patch.object(run_watch_module, "now_local_dt", return_value=reference):
            html = build_newsletter_html("success", counts, [today, tomorrow, unknown], [])
            rich_text = "\n".join(
                str(paragraph["text"])
                for paragraph in build_mailapp_rich_paragraphs(
                    "test-run", "success", counts, Path("/tmp/summary.md"), [today, tomorrow, unknown], []
                )
            )

        self.assertIn("Cierra hoy — lun 10 ago", html)
        self.assertIn("Cierra mañana — mar 11 ago", html)
        self.assertIn("Sin fecha de cierre confirmada", html)
        self.assertIn("🗓️ Cierra hoy — lun 10 ago", rich_text)

    def test_html_mail_reuses_the_opportunity_image_url(self) -> None:
        item = replace(
            self.sample_match(),
            image_url="https://images.example.test/opportunity.jpg",
        )
        html = build_newsletter_html("success", {"total_matches": 1}, [item], [])

        self.assertIn('src="https://images.example.test/opportunity.jpg"', html)
        self.assertIn("object-fit:contain", html)
        self.assertIn("AUCTION WATCH · REMATES ACTIVOS", html)

    def test_remotes_email_image_uses_redirect_target_when_available(self) -> None:
        item = replace(
            self.sample_match(),
            image_url="https://static3.remotes.com.uy/img/thumb/example.jpg",
        )

        class Response:
            def geturl(self):
                return "https://static3.remotes.com.uy/final-image.jpg"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch.object(run_watch_module, "urlopen", return_value=Response()):
            resolved = resolve_mail_image_urls([item])

        self.assertEqual(resolved[0].image_url, "https://static3.remotes.com.uy/final-image.jpg")

    def test_canonical_row_becomes_a_generic_mail_view(self) -> None:
        item = match_view_from_row(
            "todoremates",
            {
                "source_id": "todoremates",
                "source_label": "TodoRemates",
                "group_id": "10",
                "group_label": "Coleccionables",
                "group_url": "https://example.test/groups/10",
                "lot_id": "99",
                "title": "Cartuchos de videojuegos retro",
                "description": "No se probó",
                "lot_url": "https://example.test/lots/99",
                "currency": "UYU",
                "total_base_with_commission": "125",
                "event_at": "2026-08-06T18:00:00-03:00",
                "score": "18",
                "matched_keywords": "cartuchos, videojuegos",
                "risk_flags": "no se probo",
            },
        )

        self.assertEqual(item.source_label, "TodoRemates")
        self.assertEqual(item.price_label, "Base c/cargos: $125.00")
        self.assertEqual(item.timing_label, "remate")
        self.assertEqual(item.score, 18)

        generic_title = match_view_from_row(
            "todoremates",
            {
                "source_id": "todoremates",
                "lot_id": "100",
                "title": "Lote 100",
                "description": "Nintendo Wii con controles",
                "lot_url": "https://example.test/lots/100",
            },
        )
        self.assertEqual(generic_title.title, "Lote 100 · Nintendo Wii con controles")

    def test_dismissed_matches_are_filtered_by_source_and_lot_identity(self) -> None:
        dismissed = self.sample_match()
        visible = self.sample_match(source_id="prado", lot_id="lot-42")

        active, hidden = filter_dismissed_match_views(
            [dismissed, visible],
            frozenset({("remotes", "lot-42")}),
        )

        self.assertEqual(active, [visible])
        self.assertEqual(hidden, [dismissed])

    def test_discard_action_is_in_every_mail_format_when_app_url_is_configured(self) -> None:
        item = self.sample_match()
        app_base_url = "http://homeassistant.local:8788"
        action_url = discard_action_url(app_base_url, item)
        counts: dict[str, object] = {"total_matches": 1}

        plain = build_email_body(
            "test-run",
            "success",
            counts,
            Path("/tmp/summary.md"),
            [item],
            [],
            app_base_url=app_base_url,
        )
        html = build_newsletter_html(
            "success",
            counts,
            [item],
            [],
            app_base_url=app_base_url,
        )
        rich_text = "\n".join(
            str(paragraph["text"])
            for paragraph in build_mailapp_rich_paragraphs(
                "test-run",
                "success",
                counts,
                Path("/tmp/summary.md"),
                [item],
                [],
                app_base_url=app_base_url,
            )
        )

        self.assertIn("/auction-watch-action.html?", action_url)
        self.assertIn("source=remotes", action_url)
        self.assertIn("lot=lot-42", action_url)
        self.assertIn(action_url, plain)
        self.assertIn(action_url.replace("&", "&amp;"), html)
        self.assertIn(action_url, rich_text)
        self.assertIn("Descartar", html)

    def test_featured_discard_uses_stable_identity_even_if_url_format_differs(self) -> None:
        item = self.sample_match()
        hit = WatchHit(
            watch_id="favorite-42",
            label="Consola retro con controles",
            source="remotes",
            lot_id="lot-42",
            group_id="group-7",
            lot_label="Lote 42",
            group_label="Remate 7",
            description="Consola retro con controles",
            lot_url="https://example.test/lots/42?from=watchlist",
            group_url="https://example.test/groups/7",
            closing_at_iso="2026-08-06T18:00:00-03:00",
            closing_at_display="jue 06 ago 18:00",
            remaining_text="1d 2h",
            urgency_label="cierra pronto",
            matched_keywords="consola, controles",
            price_label="Base c/cargos: $1.250",
        )
        app_base_url = "http://homeassistant.local:8788"
        action_url = discard_action_url(app_base_url, item)

        html = build_newsletter_html(
            "success",
            {"total_matches": 1},
            [item],
            [hit],
            app_base_url=app_base_url,
        )

        self.assertIn(action_url.replace("&", "&amp;"), html)
        self.assertEqual(html.count(">Descartar</a>"), 1)

    def test_dismissal_payload_is_normalized_and_deduplicated(self) -> None:
        state = parse_dismissal_payload(
            {
                "items": [
                    {"sourceId": "REMOTES", "lotId": "42", "title": "Primero"},
                    {"source_id": "remotes", "lot_id": "42", "title": "Actualizado"},
                    {"sourceId": "", "lotId": "sin-fuente"},
                ]
            },
            source="remote",
        )

        self.assertEqual(state.keys, frozenset({("remotes", "42")}))
        self.assertEqual(len(state.items), 1)
        self.assertEqual(state.items[0]["title"], "Actualizado")

        with self.assertRaises(ValueError):
            parse_dismissal_payload({}, source="remote", require_schema=True)

    def test_featured_snapshot_inherits_the_stable_lot_identity(self) -> None:
        featured = enrich_featured_identity(
            {"source": "remotes", "lotUrl": "https://example.test/lots/42"},
            [
                {
                    "id": "remotes-42",
                    "source": "remotes",
                    "groupId": "group-7",
                    "lotId": "lot-42",
                    "lotNumber": "42",
                    "lotUrl": "https://example.test/lots/42",
                }
            ],
        )

        self.assertEqual(featured["lotId"], "lot-42")
        self.assertEqual(featured["groupId"], "group-7")

    def test_extra_source_is_exported_to_the_read_only_web_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "latest"
            output_path = Path(temp_dir) / "auction-watch.json"
            input_dir.mkdir()
            (input_dir / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": "test-run",
                        "finished_at": "2026-08-05T12:00:00-03:00",
                        "status": "success",
                        "counts": {"total_matches": 1},
                    }
                ),
                encoding="utf-8",
            )
            row = {
                "source_id": "prado",
                "source_label": "Prado Subastas",
                "group_id": "g-1",
                "group_label": "Coleccionables",
                "group_url": "https://example.test/groups/g-1",
                "lot_id": "l-1",
                "lot_number": "1",
                "title": "Radofin Tele-Sports",
                "description": "Con controles",
                "lot_url": "https://example.test/lots/l-1",
                "currency": "UYU",
                "score": "26",
                "matched_keywords": "radofin, tele-sports",
                "total_next_bid_with_commission": "1419.60",
                "closing_at": "2026-08-13T19:03:00-03:00",
            }
            with (input_dir / "consolas_extra_matches.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)

            payload = export_snapshot(input_dir, output_path)

            self.assertEqual(len(payload["matches"]), 1)
            self.assertEqual(payload["matches"][0]["source"], "prado")
            self.assertEqual(payload["matches"][0]["priceValue"], 1419.6)
            self.assertEqual(payload["matches"][0]["title"], "Radofin Tele-Sports")
            self.assertEqual(payload["scanStatus"], "success")

            dismissals_path = Path(temp_dir) / "dismissals-cache.json"
            dismissals_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [{"sourceId": "prado", "lotId": "l-1"}],
                    }
                ),
                encoding="utf-8",
            )
            raw = export_snapshot(input_dir, output_path, dismissals_path)
            self.assertEqual(len(raw["matches"]), 1)
            self.assertEqual(raw["counts"]["total_matches"], 1)
            self.assertEqual(raw["counts"]["detected_matches"], 1)
            self.assertEqual(raw["counts"]["dismissed_matches"], 0)
            self.assertEqual(raw["dismissalsApplied"], 0)

    def test_partial_source_failure_is_explained_in_web_snapshot(self) -> None:
        issues = build_run_issues(
            {
                "extra_sources": {
                    "sources": [
                        {
                            "source_id": "prado",
                            "label": "Prado Subastas",
                            "status": "failed",
                            "errors": [
                                "No se pudieron descubrir lotes: ConnectionResetError: Connection reset by peer"
                            ],
                        },
                        {
                            "source_id": "remotes",
                            "label": "Remotes",
                            "status": "success",
                            "errors": [],
                        },
                    ]
                }
            }
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["sourceId"], "prado")
        self.assertEqual(
            issues[0]["summary"],
            "El servidor cortó la conexión mientras se consultaban los lotes.",
        )

    def test_lifecycle_keeps_unfiltered_keys_and_marks_partial_sources_unsafe(self) -> None:
        lifecycle = build_publication_lifecycle(
            {
                "steps": [
                    {"name": "bavastro_discovery", "status": "success", "inventory_authoritative": True},
                    {"name": "bavastro_matches", "status": "skipped", "inventory_authoritative": True},
                    {"name": "castells_discovery", "status": "success", "inventory_authoritative": True},
                    {"name": "castells_matches", "status": "success", "inventory_authoritative": True},
                ],
                "extra_sources": {
                    "sources": [
                        {"source_id": "prado", "status": "failed"},
                        {
                            "source_id": "remotes",
                            "status": "success",
                            "inventory_authoritative": True,
                        },
                    ]
                },
            },
            [
                {"source": "prado", "lotId": "272662"},
                {"source": "remotes", "lotId": "7544:18"},
            ],
        )

        self.assertEqual(lifecycle["version"], 1)
        self.assertIn({"sourceId": "prado", "lotId": "272662"}, lifecycle["activeKeys"])
        self.assertEqual(
            lifecycle["sourceHealth"]["bavastro"],
            {"status": "success", "inventoryAuthoritative": True},
        )
        self.assertEqual(
            lifecycle["sourceHealth"]["prado"],
            {"status": "failed", "inventoryAuthoritative": False},
        )

    def test_legacy_success_status_never_implies_authoritative_inventory(self) -> None:
        lifecycle = build_publication_lifecycle(
            {
                "steps": [
                    {"name": "bavastro_discovery", "status": "success"},
                    {"name": "bavastro_matches", "status": "success"},
                ],
                "extra_sources": {
                    "sources": [{"source_id": "remotes", "status": "success"}]
                },
            },
            [],
        )

        self.assertEqual(
            lifecycle["sourceHealth"]["bavastro"],
            {"status": "success", "inventoryAuthoritative": False},
        )
        self.assertEqual(
            lifecycle["sourceHealth"]["remotes"],
            {"status": "success", "inventoryAuthoritative": False},
        )

    def test_old_incremental_cache_is_invalidated_for_new_search_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": STATE_SCHEMA_VERSION - 1,
                        "processed_bavastro_auction_ids": [123],
                        "processed_castells_remate_ids": [456],
                    }
                ),
                encoding="utf-8",
            )

            state = load_state(state_path)

            self.assertEqual(state.processed_bavastro_auction_ids, set())
            self.assertEqual(state.processed_castells_remate_ids, set())

    def test_failed_run_preserves_the_previous_matchful_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "current"
            previous = root / "previous"
            current.mkdir()
            previous.mkdir()
            (previous / "summary.md").write_text("previous\n", encoding="utf-8")

            with (
                patch.object(run_watch_module, "RUNS_DIR", root),
                patch.object(run_watch_module, "LATEST_MATCHES_DIR", root / "latest-matches"),
            ):
                refresh_latest_matches_mirror(current, False, previous)

            self.assertEqual(
                (root / "latest-matches" / "summary.md").read_text(encoding="utf-8"),
                "previous\n",
            )
            self.assertFalse(should_export_web_snapshot("failure", False))
            self.assertTrue(should_export_web_snapshot("partial_failure", True))
            self.assertTrue(should_export_web_snapshot("success", False))

    def test_snapshot_local_only_is_explicitly_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "auction-watch.json"
            snapshot.write_text(json.dumps({"matches": []}), encoding="utf-8")

            result = publish_web_snapshot(
                {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                snapshot,
            )

        self.assertEqual(result.status, "skipped")
        self.assertFalse(result.published)
        self.assertEqual(result.detail, "local_only")

    def test_snapshot_ha_required_without_endpoint_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "auction-watch.json"
            snapshot.write_text(
                json.dumps({"runId": "run-1", "generatedAt": "2026-08-24T12:00:00-03:00", "matches": []}),
                encoding="utf-8",
            )
            result = publish_web_snapshot(
                {"AUCTION_WATCH_PUBLICATION_MODE": "ha-required"},
                snapshot,
            )

        self.assertEqual(result.status, "failed")
        self.assertFalse(result.published)
        self.assertEqual(result.detail, "missing_snapshot_endpoint")

    def test_required_email_failure_keeps_schedule_pending(self) -> None:
        self.assertTrue(
            required_email_delivery_failed(
                [run_watch_module.NotificationResult("email", True, True, False, "transport_error")]
            )
        )
        self.assertFalse(
            required_email_delivery_failed(
                [run_watch_module.NotificationResult("email", True, True, True, "sent")]
            )
        )


if __name__ == "__main__":
    unittest.main()
