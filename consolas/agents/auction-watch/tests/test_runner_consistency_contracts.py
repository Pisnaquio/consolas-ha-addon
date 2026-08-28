from __future__ import annotations

import json
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, AGENT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import scripts.run_watch as run_watch  # noqa: E402


class RunnerConsistencyContractTests(unittest.TestCase):
    @staticmethod
    def write_delivery_run(root: Path, *, snapshot_exists: bool = True) -> tuple[Path, Path, dict]:
        run_dir = root / "runs" / "run-1"
        (run_dir / "logs").mkdir(parents=True)
        snapshot = {
            "runId": "run-1",
            "generatedAt": "2026-08-24T12:00:00-03:00",
            "scanStatus": "success",
            "matches": [],
        }
        snapshot_path = run_dir / run_watch.RUN_SNAPSHOT_FILENAME
        if snapshot_exists:
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        run_watch.write_json(
            run_dir / "run.json",
            {
                "run_id": "run-1",
                "started_at": "2026-08-24T12:00:00-03:00",
                "status": "success",
                "scanStatus": "success",
                "snapshotStatus": "failed",
                "notifications": [],
            },
        )
        run_watch.write_json(
            run_dir / run_watch.DELIVERY_MANIFEST_FILENAME,
            {
                "version": 1,
                "runId": "run-1",
                "runDir": str(run_dir),
                "publicationMode": "local-only",
                "snapshotPath": str(snapshot_path),
                "snapshotHash": run_watch.snapshot_payload_hash(snapshot),
                "scheduleDate": "2026-08-24",
                "scheduleSlots": ["morning"],
                "email": {
                    "version": 1,
                    "runId": "run-1",
                    "enabled": True,
                    "method": "smtp",
                    "recipients": ["owner@example.test"],
                    "subject": "Auction Watch",
                    "body": "body",
                    "htmlBody": "<p>body</p>",
                    "attachments": [],
                    "messageId": "<auction-watch.run-1@consolas.local>",
                },
            },
        )
        return run_dir, snapshot_path, snapshot

    def test_partial_refresh_merges_without_erasing_omitted_matches(self) -> None:
        old_one = {"auction_id": "123", "lot_auction_id": "1", "score": "10"}
        old_two = {"auction_id": "123", "lot_auction_id": "2", "score": "20"}
        refreshed_one = {"auction_id": "123", "lot_auction_id": "1", "score": "99"}
        state = run_watch.AgentState(
            active_bavastro_matches_by_group={"123": [old_one, old_two]}
        )

        partial_rows = run_watch.reconcile_active_match_state(
            state,
            "bavastro",
            [123],
            [refreshed_one],
            [123],
            inventory_authoritative=True,
            refresh_succeeded=True,
            refresh_complete=False,
        )

        self.assertEqual(
            {row["lot_auction_id"] for row in partial_rows},
            {"1", "2"},
        )
        self.assertEqual(
            next(row for row in partial_rows if row["lot_auction_id"] == "1")["score"],
            "99",
        )

        complete_rows = run_watch.reconcile_active_match_state(
            state,
            "bavastro",
            [123],
            [refreshed_one],
            [123],
            inventory_authoritative=True,
            refresh_succeeded=True,
            refresh_complete=True,
        )
        self.assertEqual([row["lot_auction_id"] for row in complete_rows], ["1"])

    def test_partial_registry_refresh_preserves_omitted_sources_and_lots(self) -> None:
        state = run_watch.AgentState(
            active_extra_matches_by_source={
                "prado": [
                    {"source_id": "prado", "lot_id": "p1", "score": "10"},
                    {"source_id": "prado", "lot_id": "p2", "score": "20"},
                ],
                "remotes": [
                    {"source_id": "remotes", "lot_id": "r1", "score": "30"}
                ],
            }
        )

        rows = run_watch.reconcile_extra_match_state(
            state,
            [{"source_id": "prado", "lot_id": "p1", "score": "99"}],
            [
                {"source_id": "prado", "status": "partial"},
                {"source_id": "remotes", "status": "failed"},
            ],
            status_payload_valid=True,
        )

        self.assertEqual(
            {(row["source_id"], row["lot_id"]) for row in rows},
            {("prado", "p1"), ("prado", "p2"), ("remotes", "r1")},
        )
        self.assertEqual(
            next(row for row in rows if row["lot_id"] == "p1")["score"],
            "99",
        )

    def test_success_without_explicit_inventory_authority_preserves_extra_cache(self) -> None:
        state = run_watch.AgentState(
            active_extra_matches_by_source={
                "prado": [{"source_id": "prado", "lot_id": "old", "score": "10"}]
            }
        )

        rows = run_watch.reconcile_extra_match_state(
            state,
            [{"source_id": "prado", "lot_id": "new", "score": "20"}],
            [{"source_id": "prado", "status": "success", "inventory_authoritative": False}],
            status_payload_valid=True,
        )

        self.assertEqual(
            {(row["source_id"], row["lot_id"]) for row in rows},
            {("prado", "old"), ("prado", "new")},
        )

    def test_lifecycle_preserves_first_seen_and_marks_only_authoritative_disappearance(self) -> None:
        state = run_watch.AgentState(
            active_bavastro_matches_by_group={
                "123": [{"auction_id": "123", "lot_auction_id": "lot-1"}]
            }
        )

        first = run_watch.update_opportunity_lifecycle(
            state,
            "run-1",
            "2026-08-27T12:00:00+00:00",
            {"bavastro": [{"auction_id": "123", "lot_auction_id": "lot-1"}]},
            {"bavastro": set()},
        )
        first_record = state.opportunity_lifecycle[next(iter(first["observed"]))]
        self.assertEqual(first_record["firstSeenRunId"], "run-1")
        self.assertEqual(first_record["seenCount"], 1)

        second = run_watch.update_opportunity_lifecycle(
            state,
            "run-2",
            "2026-08-27T13:00:00+00:00",
            {"bavastro": []},
            {"bavastro": {"123"}},
        )
        key = next(iter(second["removed"]))
        record = state.opportunity_lifecycle[key]
        self.assertEqual(record["firstSeenRunId"], "run-1")
        self.assertEqual(record["seenCount"], 1)
        self.assertFalse(record["active"])
        self.assertTrue(record["disappearedAfterAuthoritativeRefresh"])

    def test_identical_authoritative_runs_keep_keys_and_do_not_reannounce_them(self) -> None:
        state = run_watch.AgentState()
        rows = {
            "bavastro": [{"auction_id": "123", "lot_auction_id": "lot-1"}],
            "castells": [{"remate_id": "456", "lot_id": "lot-2"}],
        }

        first = run_watch.update_opportunity_lifecycle(
            state,
            "run-1",
            "2026-08-27T12:00:00+00:00",
            rows,
            {"bavastro": {"123"}, "castells": {"456"}},
        )
        second = run_watch.update_opportunity_lifecycle(
            state,
            "run-2",
            "2026-08-27T13:00:00+00:00",
            rows,
            {"bavastro": {"123"}, "castells": {"456"}},
        )

        self.assertEqual(first["new"], {"bavastro\x1flot-1", "castells\x1flot-2"})
        self.assertEqual(second["new"], set())
        self.assertEqual(second["removed"], set())
        self.assertEqual(
            {key for key, value in state.opportunity_lifecycle.items() if value["active"]},
            {"bavastro\x1flot-1", "castells\x1flot-2"},
        )
        self.assertEqual(state.opportunity_lifecycle["bavastro\x1flot-1"]["seenCount"], 2)
        self.assertEqual(state.opportunity_lifecycle["bavastro\x1flot-1"]["firstSeenRunId"], "run-1")

    @staticmethod
    def make_step(root: Path, name: str, stdout: str) -> run_watch.StepResult:
        logs = root / "logs"
        logs.mkdir(exist_ok=True)
        stdout_path = logs / f"{name}.stdout.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        return run_watch.StepResult(
            name=name,
            command=[],
            stdout_path=str(stdout_path.relative_to(root)),
            stderr_path=str(stdout_path.relative_to(root)),
            exit_code=0,
            status="success",
            started_at="now",
            finished_at="now",
        )

    def test_partial_discovery_preserves_omitted_group_and_does_not_remove_it(self) -> None:
        state = run_watch.AgentState(
            active_bavastro_matches_by_group={
                "101": [{"auction_id": "101", "lot_auction_id": "a-old"}],
                "202": [{"auction_id": "202", "lot_auction_id": "b-old"}],
            }
        )
        prior = run_watch.active_match_rows_for_source(state, "bavastro")
        run_watch.update_opportunity_lifecycle(
            state,
            "run-1",
            "2026-08-27T12:00:00+00:00",
            {"bavastro": prior},
            {"bavastro": {"101", "202"}},
        )

        rows = run_watch.reconcile_active_match_state(
            state,
            "bavastro",
            [101],
            [{"auction_id": "101", "lot_auction_id": "a-old"}],
            [101],
            inventory_authoritative=False,
            refresh_succeeded=True,
            refresh_complete=True,
            completed_group_ids={"101"},
        )
        lifecycle = run_watch.update_opportunity_lifecycle(
            state,
            "run-2",
            "2026-08-27T13:00:00+00:00",
            {"bavastro": [{"auction_id": "101", "lot_auction_id": "a-old"}]},
            {"bavastro": {"101"}},
            prior_source_rows={"bavastro": prior},
        )

        discovery = run_watch.StepResult(
            "discovery", [], "", "", 0, "partial", "", "", inventory_authoritative=False
        )
        matches = run_watch.StepResult(
            "matches", [], "", "", 0, "success", "", "", inventory_authoritative=True
        )
        self.assertFalse(
            run_watch.effective_inventory_authority(discovery, matches, [101], [101])
        )
        self.assertIn("b-old", {row["lot_auction_id"] for row in rows})
        self.assertEqual(lifecycle["removed"], set())

    def test_complete_discovery_removes_group_only_after_full_confirmation(self) -> None:
        state = run_watch.AgentState(
            active_bavastro_matches_by_group={
                "101": [{"auction_id": "101", "lot_auction_id": "a-old"}],
                "202": [{"auction_id": "202", "lot_auction_id": "b-old"}],
            }
        )
        prior = run_watch.active_match_rows_for_source(state, "bavastro")
        run_watch.update_opportunity_lifecycle(
            state,
            "run-1",
            "2026-08-27T12:00:00+00:00",
            {"bavastro": prior},
            {"bavastro": {"101", "202"}},
        )
        rows = run_watch.reconcile_active_match_state(
            state,
            "bavastro",
            [101],
            [{"auction_id": "101", "lot_auction_id": "a-old"}],
            [101],
            inventory_authoritative=True,
            refresh_succeeded=True,
            refresh_complete=True,
            completed_group_ids={"101"},
        )
        lifecycle = run_watch.update_opportunity_lifecycle(
            state,
            "run-2",
            "2026-08-27T13:00:00+00:00",
            {"bavastro": [{"auction_id": "101", "lot_auction_id": "a-old"}]},
            {"bavastro": {"101", "202"}},
            prior_source_rows={"bavastro": prior},
        )

        self.assertNotIn("b-old", {row["lot_auction_id"] for row in rows})
        self.assertEqual(lifecycle["removed"], {"bavastro\x1fb-old"})

    def test_mixed_group_receipts_align_reconciled_inventory_and_lifecycle(self) -> None:
        state = run_watch.AgentState(
            active_bavastro_matches_by_group={
                "101": [
                    {"auction_id": "101", "lot_auction_id": "a-keep"},
                    {"auction_id": "101", "lot_auction_id": "a-remove"},
                ],
                "202": [{"auction_id": "202", "lot_auction_id": "b-keep"}],
            }
        )
        prior = run_watch.active_match_rows_for_source(state, "bavastro")
        run_watch.update_opportunity_lifecycle(
            state,
            "run-1",
            "2026-08-27T12:00:00+00:00",
            {"bavastro": prior},
            {"bavastro": {"101", "202"}},
        )

        rows = run_watch.reconcile_active_match_state(
            state,
            "bavastro",
            [101, 202],
            [{"auction_id": "101", "lot_auction_id": "a-keep"}],
            [101, 202],
            inventory_authoritative=False,
            refresh_succeeded=True,
            refresh_complete=False,
            completed_group_ids={"101"},
        )
        lifecycle = run_watch.update_opportunity_lifecycle(
            state,
            "run-2",
            "2026-08-27T13:00:00+00:00",
            {"bavastro": [{"auction_id": "101", "lot_auction_id": "a-keep"}]},
            {"bavastro": {"101"}},
            prior_source_rows={"bavastro": prior},
        )

        self.assertEqual(
            {row["lot_auction_id"] for row in rows},
            {"a-keep", "b-keep"},
        )
        self.assertEqual(lifecycle["removed"], {"bavastro\x1fa-remove"})
        self.assertTrue(state.opportunity_lifecycle["bavastro\x1fb-keep"]["active"])

    def test_discovery_authority_is_fail_closed_for_partial_and_empty_parser_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            partial = self.make_step(root, "bavastro-discovery", "Subastas existentes: 2\n")
            partial.status = "success"
            partial = run_watch.classify_bavastro_discovery(partial, root)
            self.assertEqual(partial.status, "failed")
            self.assertFalse(partial.inventory_authoritative)

            partial_with_errors = self.make_step(
                root,
                "bavastro-partial-discovery",
                "Subastas activas detectadas: 1\nErrores red/HTTP: 1\n",
            )
            partial_with_errors = run_watch.classify_bavastro_discovery(
                partial_with_errors, root
            )
            self.assertEqual(partial_with_errors.status, "partial")
            self.assertFalse(partial_with_errors.inventory_authoritative)

            castells_path = root / "castells.csv"
            castells_path.write_text("remate_id,name\n", encoding="utf-8")
            empty = self.make_step(root, "castells-discovery", "")
            empty = run_watch.classify_castells_discovery(empty, root, castells_path)
            self.assertEqual(empty.status, "failed")
            self.assertFalse(empty.inventory_authoritative)

            state = run_watch.AgentState(
                active_bavastro_matches_by_group={
                    "101": [{"auction_id": "101", "lot_auction_id": "old"}]
                }
            )
            preserved = run_watch.reconcile_active_match_state(
                state,
                "bavastro",
                [],
                [],
                [],
                inventory_authoritative=False,
                refresh_succeeded=False,
            )
            self.assertEqual(preserved[0]["lot_auction_id"], "old")

    def test_valid_empty_discovery_is_authoritative_when_adapter_proves_completeness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bavastro = self.make_step(
                root,
                "bavastro-discovery",
                "Subastas activas detectadas: 0\nErrores red/HTTP: 0\n",
            )
            bavastro = run_watch.classify_bavastro_discovery(bavastro, root)
            self.assertEqual(bavastro.status, "success")
            self.assertTrue(bavastro.inventory_authoritative)

            castells_path = root / "castells.csv"
            castells_path.write_text("remate_id,name\n", encoding="utf-8")
            castells = self.make_step(
                root,
                "castells-discovery",
                "Remates activos detectados: 0\n",
            )
            castells = run_watch.classify_castells_discovery(castells, root, castells_path)
            self.assertEqual(castells.status, "success")
            self.assertTrue(castells.inventory_authoritative)

            state = run_watch.AgentState(
                active_bavastro_matches_by_group={
                    "101": [{"auction_id": "101", "lot_auction_id": "old"}]
                }
            )
            closed = run_watch.reconcile_active_match_state(
                state,
                "bavastro",
                [],
                [],
                [],
                inventory_authoritative=True,
                refresh_succeeded=False,
            )
            self.assertEqual(closed, [])

    def test_legacy_lifecycle_without_group_id_is_preserved_fail_closed(self) -> None:
        state = run_watch.AgentState(
            opportunity_lifecycle={
                "bavastro\x1fold": {
                    "sourceId": "bavastro",
                    "lotId": "old",
                    "active": True,
                }
            }
        )

        result = run_watch.update_opportunity_lifecycle(
            state,
            "run-2",
            "2026-08-27T13:00:00+00:00",
            {"bavastro": []},
            {"bavastro": {"101"}},
        )

        self.assertEqual(result["removed"], set())
        self.assertTrue(state.opportunity_lifecycle["bavastro\x1fold"]["active"])

    def test_run_scan_queries_processed_groups_even_without_watchlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = root / "runtime"
            runtime.mkdir()
            paths = {
                "RUNS_DIR": runtime / "runs",
                "LATEST_DIR": runtime / "runs" / "latest",
                "LATEST_MATCHES_DIR": runtime / "runs" / "latest-matches",
                "STATE_FILE": runtime / "state.json",
                "DELIVERY_OUTBOX_FILE": runtime / "outbox.json",
                "WATCHLIST_FILE": runtime / "watchlist.json",
                "DISMISSALS_CACHE_FILE": runtime / "dismissals.json",
            }
            paths["RUNS_DIR"].mkdir()
            paths["WATCHLIST_FILE"].write_text("[]\n", encoding="utf-8")
            run_watch.save_state(
                paths["STATE_FILE"],
                run_watch.AgentState(
                    processed_bavastro_auction_ids={101, 202},
                    processed_castells_remate_ids={303, 404},
                ),
            )
            commands: dict[str, list[str]] = {}

            def fake_step(name: str, command: list[str], run_dir: Path) -> run_watch.StepResult:
                commands[name] = command
                (run_dir / "logs").mkdir(exist_ok=True)
                stdout_path = run_dir / "logs" / f"{name}.stdout.log"
                stdout_path.write_text(
                    "Subastas activas detectadas: 2\nSubastas existentes: 2\nErrores red/HTTP: 0\nCoincidencias: 2\n"
                    if name == "bavastro_discovery"
                    else "Remates activos detectados: 2\nLotes escaneados: 0\nErrores: 0\n"
                    if name == "castells_discovery"
                    else "Lotes escaneados: 0\nErrores: 0\n",
                    encoding="utf-8",
                )
                if name == "bavastro_discovery":
                    Path(command[command.index("--csv") + 1]).write_text(
                        "id,name,state,active,end_date,url\n101,A,open,True,,url\n202,B,open,True,,url\n",
                        encoding="utf-8",
                    )
                elif name == "castells_discovery":
                    Path(command[command.index("--discover-output") + 1]).write_text(
                        "remate_id,name\n303,C\n404,D\n", encoding="utf-8"
                    )
                elif name in {"bavastro_matches", "castells_matches"}:
                    receipt_path = Path(command[command.index("--receipt") + 1])
                    receipt_path.write_text(
                        json.dumps(
                            {
                                "inventoryAuthoritative": True,
                                "receipts": [
                                    {
                                        "groupId": group_id,
                                        "status": "complete",
                                        "lotCount": 0,
                                        "errorCount": 0,
                                        "startedAt": "now",
                                        "finishedAt": "now",
                                    }
                                    for group_id in command[command.index("--ids") + 1].split(",")
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    output_flag = "--output"
                    Path(command[command.index(output_flag) + 1]).write_text("\n", encoding="utf-8")
                elif name == "extra_sources":
                    Path(command[command.index("--output-csv") + 1]).write_text("\n", encoding="utf-8")
                    Path(command[command.index("--status-json") + 1]).write_text(
                        json.dumps(
                            {
                                "status": "success",
                                "sources": [
                                    {
                                        "source_id": source_id,
                                        "status": "success",
                                        "inventory_authoritative": True,
                                        "groups": 0,
                                        "lots": 0,
                                        "receipts": [],
                                    }
                                    for source_id in ("remotes", "todoremates", "prado")
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                return run_watch.StepResult(
                    name=name,
                    command=command,
                    stdout_path=str(stdout_path.relative_to(run_dir)),
                    stderr_path=str(stdout_path.relative_to(run_dir)),
                    exit_code=0,
                    status="success",
                    started_at="now",
                    finished_at="now",
                )

            def fake_export(input_dir: Path, output_path: Path) -> tuple[bool, str]:
                metadata = run_watch.read_json_object(input_dir / "run.json")
                output_path.write_text(
                    json.dumps(
                        {
                            "runId": metadata["runId"],
                            "generatedAt": "2026-08-27T12:00:00-03:00",
                            "scanStatus": "success",
                            "matches": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return True, "ok"

            args = argparse.Namespace(
                run_id="full-refresh",
                bavastro_discovery_mode="active",
                bavastro_query="",
                bavastro_window=200,
                bavastro_headroom=40,
                castells_limit=9999,
                keep_runs=30,
                deliver_run=None,
                force_uncertain_email_retry=False,
                schedule_date="",
                schedule_slots="",
                manual_request_id="",
                refresh_active_matches=False,
            )
            with (
                patch.multiple(run_watch, **paths),
                patch.object(run_watch, "run_step", side_effect=fake_step),
                patch.object(run_watch, "load_notification_config", return_value={}),
                patch.object(
                    run_watch,
                    "load_dismissals",
                    return_value=run_watch.DismissalState(frozenset(), tuple(), "none"),
                ),
                patch.object(run_watch, "export_web_snapshot", side_effect=fake_export),
                patch.object(run_watch, "attempt_delivery_for_run", return_value=0),
                patch.object(run_watch, "refresh_latest_matches_mirror"),
                patch.object(run_watch, "prune_runs"),
            ):
                self.assertEqual(run_watch.run_scan(args), 0)

            self.assertEqual(commands["bavastro_matches"][commands["bavastro_matches"].index("--ids") + 1], "101,202")
            self.assertEqual(commands["castells_matches"][commands["castells_matches"].index("--ids") + 1], "303,404")

    def test_complete_registry_refresh_replaces_inventory_and_state_round_trips(self) -> None:
        state = run_watch.AgentState(
            active_extra_matches_by_source={
                "prado": [
                    {"source_id": "prado", "lot_id": "old", "score": "10"}
                ],
                "removed-source": [
                    {"source_id": "removed-source", "lot_id": "stale", "score": "1"}
                ],
            }
        )
        rows = run_watch.reconcile_extra_match_state(
            state,
            [{"source_id": "prado", "lot_id": "new", "score": "50"}],
            [{"source_id": "prado", "status": "success", "inventory_authoritative": True}],
            status_payload_valid=True,
        )
        self.assertEqual(
            [(row["source_id"], row["lot_id"]) for row in rows],
            [("prado", "new")],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            run_watch.save_state(state_path, state)
            restored = run_watch.load_state(state_path)

        self.assertEqual(restored.active_extra_matches_by_source, state.active_extra_matches_by_source)

    def test_scan_status_contract_normalizes_legacy_values(self) -> None:
        self.assertEqual(run_watch.canonical_scan_status("success"), "success")
        self.assertEqual(run_watch.canonical_scan_status("partial_failure"), "partial")
        self.assertEqual(run_watch.canonical_scan_status("partial"), "partial")
        self.assertEqual(run_watch.canonical_scan_status("failure"), "failed")
        self.assertEqual(run_watch.canonical_scan_status("failed"), "failed")
        self.assertEqual(
            run_watch.compute_overall_status("partial", "published", "sent"),
            "degraded",
        )

    def test_missing_immutable_snapshot_is_terminal_and_never_reexported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(
                root, snapshot_exists=False
            )
            outbox_path = root / "outbox.json"
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(run_watch, "export_web_snapshot") as export_snapshot,
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            outbox = json.loads(outbox_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(metadata["overallStatus"], "failed")
        self.assertIn("snapshot_manifest_file_missing", metadata["snapshot"]["detail"])
        self.assertEqual(outbox["items"][0]["status"], "failed")
        export_snapshot.assert_not_called()

    def test_mutated_immutable_snapshot_is_terminal_and_never_reexported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, snapshot_path, snapshot = self.write_delivery_run(root)
            snapshot["matches"] = [{"source": "remotes", "lotId": "unexpected"}]
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            outbox_path = root / "outbox.json"
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(run_watch, "export_web_snapshot") as export_snapshot,
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertIn("snapshot_manifest_hash_mismatch", metadata["snapshot"]["detail"])
        export_snapshot.assert_not_called()

    def test_normal_timeout_result_becomes_uncertain_without_automatic_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(root)
            outbox_path = root / "outbox.json"
            run_watch.record_delivery_outbox(
                "run-1",
                run_dir,
                status="pending",
                detail="delivery_not_attempted",
                path=outbox_path,
            )
            timeout_result = run_watch.NotificationResult(
                "email",
                enabled=True,
                attempted=True,
                sent=False,
                detail="timeout",
            )
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(
                    run_watch,
                    "send_prepared_email",
                    return_value=timeout_result,
                ) as send_mail,
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )
                pending = run_watch.pending_delivery_items(
                    path=outbox_path,
                    due_only=False,
                )

            manifest = json.loads(
                (run_dir / run_watch.DELIVERY_MANIFEST_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            outbox = json.loads(outbox_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(manifest["emailDelivery"]["status"], "uncertain")
        self.assertIn("explicit_retry_may_duplicate", manifest["emailDelivery"]["detail"])
        self.assertEqual(metadata["emailStatus"], "uncertain")
        self.assertEqual(metadata["overallStatus"], "failed")
        self.assertEqual(outbox["items"][0]["status"], "uncertain")
        self.assertEqual(pending, [])
        send_mail.assert_called_once()

    def test_only_definitive_pre_send_or_rejection_failures_are_retryable(self) -> None:
        ambiguous = run_watch.NotificationResult("email", True, True, False, "timeout")
        disconnected = run_watch.NotificationResult(
            "email", True, True, False, "smtp_delivery_uncertain:SMTPServerDisconnected"
        )
        pre_send = run_watch.NotificationResult(
            "email", True, True, False, "smtp_pre_send_failed:SMTPConnectError"
        )
        rejected = run_watch.NotificationResult(
            "email", True, True, False, "sendmail_rejected:exit=67"
        )

        self.assertTrue(run_watch.email_failure_is_ambiguous(ambiguous))
        self.assertTrue(run_watch.email_failure_is_ambiguous(disconnected))
        self.assertFalse(run_watch.email_failure_is_ambiguous(pre_send))
        self.assertFalse(run_watch.email_failure_is_ambiguous(rejected))

    def test_definitive_pre_send_failure_remains_in_retry_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(root)
            outbox_path = root / "outbox.json"
            run_watch.record_delivery_outbox(
                "run-1",
                run_dir,
                status="pending",
                detail="delivery_not_attempted",
                path=outbox_path,
            )
            pre_send_result = run_watch.NotificationResult(
                "email",
                enabled=True,
                attempted=False,
                sent=False,
                detail="missing_smtp_config",
            )
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(
                    run_watch,
                    "send_prepared_email",
                    return_value=pre_send_result,
                ),
            ):
                exit_code = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            outbox = json.loads(outbox_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 2)
        self.assertEqual(metadata["emailStatus"], "pending")
        self.assertEqual(metadata["overallStatus"], "delivery_pending")
        self.assertEqual(outbox["items"][0]["status"], "pending")

    def test_transport_timeouts_and_status_checks_are_tagged_ambiguous(self) -> None:
        with patch.object(run_watch.smtplib, "SMTP") as smtp:
            smtp.return_value.__enter__.return_value.send_message.side_effect = (
                run_watch.smtplib.SMTPServerDisconnected("connection lost")
            )
            smtp_result = run_watch.send_email_via_smtp(
                {
                    "AUCTION_WATCH_SMTP_HOST": "smtp.example.test",
                    "AUCTION_WATCH_SMTP_PORT": "587",
                    "AUCTION_WATCH_SMTP_STARTTLS": "false",
                    "AUCTION_WATCH_EMAIL_FROM": "owner@example.test",
                },
                ["owner@example.test"],
                "subject",
                "body",
            )

        with patch.object(
            run_watch.subprocess,
            "run",
            side_effect=run_watch.subprocess.TimeoutExpired("sendmail", 30),
        ):
            sendmail_result = run_watch.send_email_via_sendmail(
                {},
                ["owner@example.test"],
                "subject",
                "body",
            )

        process_result = type(
            "ProcessResult",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()
        with (
            patch.object(run_watch.subprocess, "run", return_value=process_result),
            patch.object(
                run_watch,
                "mailapp_message_status",
                return_value=(False, "timeout"),
            ),
        ):
            mailapp_result = run_watch.send_email_via_mailapp(
                ["owner@example.test"],
                "subject",
                "body",
            )

        self.assertIn("smtp_delivery_uncertain", smtp_result.detail)
        self.assertIn("sendmail_delivery_uncertain", sendmail_result.detail)
        self.assertIn("mailapp_delivery_uncertain", mailapp_result.detail)
        self.assertTrue(run_watch.email_failure_is_ambiguous(smtp_result))
        self.assertTrue(run_watch.email_failure_is_ambiguous(sendmail_result))
        self.assertTrue(run_watch.email_failure_is_ambiguous(mailapp_result))

    def test_interrupted_email_becomes_uncertain_and_requires_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(root)
            outbox_path = root / "outbox.json"
            run_watch.record_delivery_outbox(
                "run-1",
                run_dir,
                status="pending",
                detail="delivery_not_attempted",
                path=outbox_path,
            )

            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(
                    run_watch,
                    "send_prepared_email",
                    side_effect=RuntimeError("simulated process crash"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated process crash"):
                    run_watch.attempt_delivery_for_run(
                        run_dir,
                        {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                    )

            crashed_outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
            self.assertEqual(crashed_outbox["items"][0]["status"], "sending")
            crashed_outbox["items"][0]["sendingPid"] = 999_999_999
            run_watch.atomic_write_json(outbox_path, crashed_outbox)

            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
            ):
                self.assertEqual(
                    run_watch.pending_delivery_items(path=outbox_path, due_only=False),
                    [],
                )

            recovered_outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
            recovered_metadata = json.loads(
                (run_dir / "run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(recovered_outbox["items"][0]["status"], "uncertain")
            self.assertIn("may_duplicate", recovered_outbox["items"][0]["detail"])
            self.assertEqual(recovered_metadata["emailStatus"], "uncertain")

            sent = run_watch.NotificationResult("email", True, True, True, "sent_via_smtp")
            with (
                patch.object(run_watch, "RUNS_DIR", root / "runs"),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(run_watch, "send_prepared_email", return_value=sent) as send_mail,
            ):
                blocked_exit = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                )
                self.assertEqual(blocked_exit, 1)
                send_mail.assert_not_called()

                forced_exit = run_watch.attempt_delivery_for_run(
                    run_dir,
                    {"AUCTION_WATCH_PUBLICATION_MODE": "local-only"},
                    force_uncertain_email_retry=True,
                )

            final_outbox = json.loads(outbox_path.read_text(encoding="utf-8"))

        self.assertEqual(forced_exit, 0)
        self.assertEqual(final_outbox["items"][0]["status"], "completed")
        send_mail.assert_called_once()

    def test_terminal_outbox_repairs_stale_run_metadata_before_scheduler_ack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir, _snapshot_path, _snapshot = self.write_delivery_run(root)
            manifest_path = run_dir / run_watch.DELIVERY_MANIFEST_FILENAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["publication"] = {
                "mode": "ha-required",
                "status": "published",
                "configured": True,
                "attempted": True,
                "detail": "receipt verified",
                "runId": "run-1",
                "snapshotHash": manifest["snapshotHash"],
                "generatedAt": "2026-08-24T12:00:00-03:00",
                "canonicalVerified": True,
            }
            manifest["emailDelivery"] = {"status": "sent", "detail": "sent_via_smtp"}
            run_watch.write_json(manifest_path, manifest)
            outbox_path = root / "outbox.json"
            run_watch.record_delivery_outbox(
                "run-1",
                run_dir,
                status="completed",
                detail="sent_via_smtp",
                path=outbox_path,
            )

            with (
                patch.object(run_watch, "DELIVERY_OUTBOX_FILE", outbox_path),
                patch.object(run_watch, "LATEST_DIR", root / "runs" / "latest"),
            ):
                run_watch.recover_interrupted_delivery_outbox()
                first_metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
                run_watch.recover_interrupted_delivery_outbox()

            metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(metadata["snapshotStatus"], "published")
        self.assertEqual(metadata["emailStatus"], "sent")
        self.assertEqual(metadata["overallStatus"], "completed")
        self.assertEqual(metadata["delivery"]["status"], "completed")
        self.assertEqual(metadata["completedAt"], first_metadata["completedAt"])

    def test_newer_canonical_snapshot_makes_an_older_publish_retry_terminal(self) -> None:
        class JsonResponse:
            def read(self) -> bytes:
                return json.dumps(
                    {
                        "runId": "run-newer",
                        "sync": {
                            "runId": "run-newer",
                            "status": "stale",
                            "source": "server",
                            "acceptedAt": "2026-08-24T12:00:00Z",
                        },
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        publication = run_watch.PublicationResult(
            mode="ha-required",
            status="failed",
            configured=True,
            attempted=True,
            detail="HTTPError: HTTP Error 409: Conflict",
            run_id="run-old",
        )
        with patch.object(run_watch, "urlopen", return_value=JsonResponse()):
            superseded = run_watch.publication_failure_is_superseded(
                {"AUCTION_WATCH_APP_BASE_URL": "http://app.test"},
                publication,
                "run-old",
            )

        self.assertTrue(superseded)


if __name__ == "__main__":
    unittest.main()
