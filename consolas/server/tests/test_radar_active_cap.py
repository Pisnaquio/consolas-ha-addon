"""El techo de búsquedas activas.

Se llegó a ochenta activas pidiéndole varias tandas al Master y aceptándolas en
bloque. El cupo del Master era por tanda, no global, así que nada frenaba la
acumulación — y ochenta búsquedas corriendo tres veces por día vuelcan tanto al
feed que deja de servir para detectar nada.

Lo que prueban estos tests no es el número: es que exista un límite, que diga
los números al rechazar, que nunca impida salir, y que se pueda subir a
conciencia.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.app import (
    ApiError,
    RADAR_DEFAULT_MAX_ACTIVE,
    RADAR_MAX_PENDING_DRAFTS,
    connect_db,
    create_radar_search,
    get_radar_preferences,
    init_db,
    regenerate_radar_master,
    set_radar_search_status,
    update_radar_preferences,
)


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


class RadarActiveCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = TestConfig(Path(self._tmp.name))
        init_db(self.config)
        self.addCleanup(self._tmp.cleanup)

    def make(self, name: str, status: str = "active") -> str:
        return create_radar_search(
            self.config, {"name": name, "platform": "PS2", "status": status}
        )["search"]["id"]

    def active_count(self) -> int:
        with connect_db(self.config) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM radar_searches WHERE status = 'active' AND deleted_at IS NULL"
                ).fetchone()[0]
            )

    def fill_to_cap(self) -> None:
        while self.active_count() < RADAR_DEFAULT_MAX_ACTIVE:
            self.make(f"Búsqueda {self.active_count()}")

    def test_the_cap_has_a_default_and_is_readable(self) -> None:
        self.assertEqual(get_radar_preferences(self.config)["maxActiveSearches"], RADAR_DEFAULT_MAX_ACTIVE)

    def test_creating_one_more_active_search_over_the_cap_is_refused(self) -> None:
        self.fill_to_cap()
        with self.assertRaises(ApiError) as caught:
            self.make("La que sobra")
        self.assertEqual(self.active_count(), RADAR_DEFAULT_MAX_ACTIVE)
        # El mensaje tiene que traer los dos números: sin ellos no se sabe si
        # hay que retirar una búsqueda o subir el techo.
        self.assertIn(str(RADAR_DEFAULT_MAX_ACTIVE), str(caught.exception))

    def test_activating_a_draft_over_the_cap_is_refused(self) -> None:
        pending = self.make("Un borrador", status="draft")
        self.fill_to_cap()
        with self.assertRaises(ApiError):
            set_radar_search_status(self.config, pending, "active")

    def test_a_draft_can_still_be_created_when_the_cap_is_full(self) -> None:
        """Guardar una idea no consume el feed: sólo activarla lo hace."""
        self.fill_to_cap()
        search_id = self.make("Idea para después", status="draft")
        self.assertEqual(self.active_count(), RADAR_DEFAULT_MAX_ACTIVE)
        self.assertTrue(search_id)

    def test_leaving_is_never_blocked(self) -> None:
        """Pausar y archivar con el techo lleno tienen que seguir funcionando."""
        self.fill_to_cap()
        with connect_db(self.config) as conn:
            victim = conn.execute(
                "SELECT id FROM radar_searches WHERE status = 'active' LIMIT 1"
            ).fetchone()[0]
        set_radar_search_status(self.config, victim, "paused")
        self.assertEqual(self.active_count(), RADAR_DEFAULT_MAX_ACTIVE - 1)
        set_radar_search_status(self.config, victim, "archived")

    def test_retiring_one_frees_the_slot(self) -> None:
        self.fill_to_cap()
        with connect_db(self.config) as conn:
            victim = conn.execute(
                "SELECT id FROM radar_searches WHERE status = 'active' LIMIT 1"
            ).fetchone()[0]
        set_radar_search_status(self.config, victim, "paused")
        self.make("La que ahora sí entra")
        self.assertEqual(self.active_count(), RADAR_DEFAULT_MAX_ACTIVE)

    def test_raising_the_cap_deliberately_lets_more_through(self) -> None:
        self.fill_to_cap()
        update_radar_preferences(
            self.config, {"monthlyBudgetUsd": None, "maxActiveSearches": RADAR_DEFAULT_MAX_ACTIVE + 2}
        )
        self.make("Una más, a conciencia")
        self.assertEqual(self.active_count(), RADAR_DEFAULT_MAX_ACTIVE + 1)

    def test_lowering_the_cap_never_switches_anything_off(self) -> None:
        """Bajar el techo frena la próxima activación; no apaga lo que ya corre."""
        self.fill_to_cap()
        update_radar_preferences(self.config, {"monthlyBudgetUsd": None, "maxActiveSearches": 3})
        self.assertEqual(self.active_count(), RADAR_DEFAULT_MAX_ACTIVE)

    def test_the_master_stops_proposing_while_a_pile_is_unreviewed(self) -> None:
        for index in range(RADAR_MAX_PENDING_DRAFTS):
            self.make(f"Propuesta {index}", status="draft")
        with connect_db(self.config) as conn:
            conn.execute("UPDATE radar_searches SET origin = 'master' WHERE status = 'draft'")
        result = regenerate_radar_master(self.config)
        self.assertEqual(result["created"], [])
        self.assertIn("sin revisar", result["note"])


if __name__ == "__main__":
    unittest.main()
