from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from server.app import init_db, list_radar_searches, regenerate_radar_master, set_radar_search_status, write_state
from radar.master import propose_master_searches, rank_game, wanted_consoles, wanted_games


CONSOLES = [
    {"id": "ps1", "nombre": "PlayStation 1"},
    {"id": "snes", "nombre": "Super Nintendo"},
    {"id": "genesis", "nombre": "Sega Genesis / Mega Drive"},
    {"id": "n64", "nombre": "Nintendo 64"},
]


def state(**user: object) -> dict:
    return {"version": 3, "user": {"overridesById": {}, "additionsById": {}, "detailEditsById": {}, **user}, "meta": {}}


class OwnershipTests(unittest.TestCase):
    """La propiedad sale del estado persistido, nunca del catálogo."""

    def test_an_owned_console_is_never_proposed(self) -> None:
        owned = state(overridesById={"ps1": {"tengo": True}, "snes": {"tengo": True}})
        names = [entry["id"] for entry in wanted_consoles(owned, CONSOLES)]
        self.assertEqual(names, ["genesis", "n64"])

    def test_a_console_without_an_override_is_treated_as_wanted(self) -> None:
        self.assertEqual(len(wanted_consoles(state(), CONSOLES)), 4)

    def test_the_catalog_alone_never_declares_ownership(self) -> None:
        # Una entrada de catálogo marcada `tengo` sin override persistido no
        # debería inventar propiedad... salvo que el propio catálogo lo diga.
        catalog = [{"id": "ps1", "nombre": "PlayStation 1", "tengo": True}]
        self.assertEqual(wanted_consoles(state(), catalog), [])
        # Y un override del usuario siempre gana sobre el catálogo.
        self.assertEqual(len(wanted_consoles(state(overridesById={"ps1": {"tengo": False}}), catalog)), 1)


class WantedGamesTests(unittest.TestCase):
    def bucket(self, **games: object) -> dict:
        return state(detailEditsById={"snes": {"gameEditsById": games}})

    def test_an_owned_game_is_never_a_target(self) -> None:
        payload = self.bucket(g1={"nombre": "Chrono Trigger", "ownershipType": "physical", "loQuiero": True})
        self.assertEqual(wanted_games(payload), [])

    def test_an_explicit_want_and_a_kept_recommendation_are_distinguished(self) -> None:
        payload = self.bucket(
            g1={"nombre": "Chrono Trigger", "loQuiero": True},
            g2={"nombre": "Super Metroid", "keepInWishlist": True},
        )
        found = {game["name"]: game["explicit"] for game in wanted_games(payload)}
        self.assertEqual(found, {"Chrono Trigger": True, "Super Metroid": False})

    def test_a_catalog_game_with_no_flags_is_not_a_target(self) -> None:
        self.assertEqual(wanted_games(self.bucket(g1={"nombre": "Un juego cualquiera"})), [])

    def test_precedence_puts_explicit_chases_before_assigned_priority(self) -> None:
        games = [
            {"name": "Recomendado", "explicit": False, "priority": "alta"},
            {"name": "Chase sin prioridad", "explicit": True, "priority": ""},
            {"name": "Chase con prioridad", "explicit": True, "priority": "alta"},
        ]
        order = [game["name"] for game in sorted(games, key=rank_game)]
        self.assertEqual(order, ["Chase con prioridad", "Chase sin prioridad", "Recomendado"])

    def test_no_priority_does_not_mean_low_priority(self) -> None:
        games = [
            {"name": "Sin prioridad", "explicit": True, "priority": ""},
            {"name": "Baja", "explicit": True, "priority": "baja"},
        ]
        # "baja" es una decisión del usuario; "" es ausencia de decisión y va después.
        self.assertEqual([g["name"] for g in sorted(games, key=rank_game)], ["Baja", "Sin prioridad"])

    def test_manual_games_count_as_targets_too(self) -> None:
        payload = state(detailEditsById={"snes": {"manualGamesById": {"m1": {"nombre": "Mappy", "loQuiero": True}}}})
        self.assertEqual([game["name"] for game in wanted_games(payload)], ["Mappy"])


class ProposalTests(unittest.TestCase):
    def test_the_portfolio_covers_consoles_lots_and_chases(self) -> None:
        payload = state(
            overridesById={"ps1": {"tengo": True}},
            detailEditsById={"snes": {"gameEditsById": {"g1": {"nombre": "Chrono Trigger", "loQuiero": True}}}},
        )
        kinds = {proposal["searchType"] for proposal in propose_master_searches(payload, CONSOLES)}
        self.assertEqual(kinds, {"console", "lot", "chase"})

    def test_lots_are_proposed_for_consoles_the_user_actually_owns(self) -> None:
        payload = state(overridesById={"ps1": {"tengo": True}})
        lots = [p for p in propose_master_searches(payload, CONSOLES) if p["searchType"] == "lot"]
        self.assertEqual([lot["entityId"] for lot in lots], ["ps1"])

    def test_a_game_proposal_keeps_the_console_the_game_belongs_to(self) -> None:
        # Es el dato que después deja registrar la compra en la biblioteca
        # correcta: el id del juego solo no alcanza para saber la plataforma.
        payload = state(
            detailEditsById={"snes": {"gameEditsById": {"g1": {"nombre": "Chrono Trigger", "loQuiero": True}}}},
        )
        games = [p for p in propose_master_searches(payload, CONSOLES) if p["entityType"] == "game"]
        self.assertTrue(games)
        self.assertEqual(games[0]["entityConsoleId"], "snes")

    def test_a_console_proposal_carries_no_console_id_of_its_own(self) -> None:
        payload = state(overridesById={"ps1": {"tengo": True}})
        for proposal in propose_master_searches(payload, CONSOLES):
            if proposal["entityType"] == "console":
                with self.subTest(proposal=proposal["key"]):
                    self.assertEqual(proposal["entityConsoleId"], "")

    def test_every_proposal_explains_why_it_exists(self) -> None:
        payload = state(overridesById={"ps1": {"tengo": True}})
        for proposal in propose_master_searches(payload, CONSOLES):
            with self.subTest(proposal=proposal["key"]):
                self.assertTrue(proposal["rationale"])
                self.assertTrue(proposal["criteria"].get("excludeTerms"))

    def test_quotas_keep_the_portfolio_actionable(self) -> None:
        many = [{"id": f"c{i}", "nombre": f"Consola {i}"} for i in range(40)]
        proposals = propose_master_searches(state(), many, max_consoles=4, max_lots=2, max_games=4)
        self.assertLessEqual(len([p for p in proposals if p["searchType"] == "console"]), 4)

    def test_an_empty_collection_proposes_nothing_it_cannot_justify(self) -> None:
        self.assertEqual(propose_master_searches(state(), []), [])


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


class RegenerateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = TestConfig(Path(self.temp_dir.name))
        (self.config.static_dir / "data").mkdir(parents=True)
        (self.config.static_dir / "data" / "consoles.json").write_text(
            json.dumps({"consolas": CONSOLES}), encoding="utf-8"
        )
        init_db(self.config)
        write_state(self.config, state(overridesById={"ps1": {"tengo": True}}))

    def searches(self) -> list[dict]:
        return list_radar_searches(self.config)["items"]

    def test_every_proposal_lands_as_a_draft_from_the_master(self) -> None:
        result = regenerate_radar_master(self.config)
        self.assertGreater(result["created"], 0)

        proposed = [item for item in self.searches() if item["origin"] == "master"]
        self.assertEqual(len(proposed), result["created"])
        for item in proposed:
            with self.subTest(name=item["name"]):
                self.assertEqual(item["status"], "draft")
                self.assertFalse(item["canRun"], "una propuesta no puede ejecutarse sola")
                self.assertTrue(item["notes"], "la card muestra el motivo")

    def test_regenerating_is_idempotent(self) -> None:
        first = regenerate_radar_master(self.config)
        second = regenerate_radar_master(self.config)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["skipped"], first["created"])
        self.assertEqual(len(self.searches()), len(self.searches()))

    def test_a_proposal_the_user_activated_is_never_overwritten(self) -> None:
        regenerate_radar_master(self.config)
        target = next(item for item in self.searches() if item["origin"] == "master")
        set_radar_search_status(self.config, target["id"], "active")

        regenerate_radar_master(self.config)

        still = next(item for item in self.searches() if item["id"] == target["id"])
        self.assertEqual(still["status"], "active", "el Master no revierte una decisión del usuario")

    def test_a_proposal_the_user_archived_is_not_proposed_again(self) -> None:
        regenerate_radar_master(self.config)
        target = next(item for item in self.searches() if item["origin"] == "master")
        set_radar_search_status(self.config, target["id"], "archived")

        regenerate_radar_master(self.config)

        matching = [item for item in self.searches() if item["id"] == target["id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["status"], "archived")

    def test_the_master_never_writes_collection_state(self) -> None:
        from server.app import read_state

        before = read_state(self.config)
        regenerate_radar_master(self.config)
        self.assertEqual(read_state(self.config)["user"], before["user"])


if __name__ == "__main__":
    unittest.main()
