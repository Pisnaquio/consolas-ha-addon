from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

import server.app  # noqa: F401  (deja `radar.*` importable)


FIXTURE = Path(__file__).parent / "fixtures" / "calibration-2026-09.json"

# Medidas tomadas sobre el juicio humano del 2026-09-11. No son metas: son el
# punto de partida contra el que se compara cualquier cambio de scoring.
#
# Un test que falla acá no dice "está roto": dice que el radar cambió de opinión
# respecto del owner, y eso hay que mirarlo antes de mergear.
BASELINE_EXPENSIVE_BUY_RATE = 0.25  # la banda «caro» acertaba: 14% comprarías
BASELINE_JUNK_COUNT = 0  # ninguna publicación mostrada resultó ruido


def load() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["listings"]


class CalibrationFixtureTests(unittest.TestCase):
    """El dataset existe para que un cambio de scoring se pueda medir."""

    def test_the_fixture_is_usable_as_ground_truth(self) -> None:
        rows = load()
        self.assertGreaterEqual(len(rows), 70)
        for row in rows:
            with self.subTest(title=row["title"][:40]):
                self.assertIn(row["rating"], {"buy", "maybe", "no", "junk"})
                self.assertTrue(row["title"])
                self.assertTrue(row["search"])

    def test_no_personal_notes_are_versioned(self) -> None:
        # Las notas del owner son opiniones suyas; la regresión no las necesita.
        for row in load():
            self.assertNotIn("note", row)


class FilteringQualityTests(unittest.TestCase):
    """Lo que el radar mostró, ¿merecía mostrarse?"""

    def test_nothing_shown_was_noise(self) -> None:
        junk = [row for row in load() if row["rating"] == "junk"]
        self.assertLessEqual(
            len(junk),
            BASELINE_JUNK_COUNT,
            "una publicación marcada «junk» significa que los filtros la dejaron pasar y no debían",
        )


class BandQualityTests(unittest.TestCase):
    """Si la banda no cambia la decisión, la banda no sirve."""

    def buy_rate(self, rows: list[dict]) -> float:
        return sum(1 for row in rows if row["rating"] == "buy") / len(rows) if rows else 0.0

    def test_the_expensive_band_actually_predicts_rejection(self) -> None:
        expensive = [row for row in load() if row["radarBand"] == "caro"]
        self.assertTrue(expensive, "el fixture tiene que incluir publicaciones caras")
        self.assertLessEqual(
            self.buy_rate(expensive),
            BASELINE_EXPENSIVE_BUY_RATE,
            "«caro» dejó de anticipar un rechazo: la banda perdió su única señal comprobada",
        )

    def test_the_cheapest_band_does_not_outperform_the_rest_yet(self) -> None:
        """Medición incómoda y deliberada: hoy «ganga» NO es la mejor banda.

        Sobre 77 juicios, «buena» supera a «ganga» (85% contra 55%). Lo más
        barato suele estar barato por algo — daño, región equivocada, loose
        frente a CIB — y el benchmark de pares mezcla condiciones, así que una
        pieza dañada parece ganga contra una mediana que incluye impecables.

        Este test documenta el problema para que se note cuando se arregle: si
        «ganga» pasa a ser la mejor banda, hay que subir la expectativa acá.
        """

        rows = load()
        bargain = [row for row in rows if row["radarBand"] == "ganga"]
        good = [row for row in rows if row["radarBand"] == "buena"]
        self.assertTrue(bargain and good)
        self.assertLess(
            self.buy_rate(bargain),
            self.buy_rate(good) + 0.35,
            "si «ganga» ya supera a «buena», actualizá esta línea base",
        )


class ScoreQualityTests(unittest.TestCase):
    def test_the_score_does_not_separate_a_purchase_from_a_hesitation_yet(self) -> None:
        """La medición que justifica no seguir ajustando pesos a ciegas.

        Los tramos 40-54, 55-69 y 70-100 compran 50%, 62% y 59%: el score casi
        no discrimina. Ajustar seis pesos sobre 77 puntos sería sobreajustar.
        Primero hace falta más señal, o una formulación distinta.
        """

        rows = [row for row in load() if row["radarScore"] is not None]
        buys = [row["radarScore"] for row in rows if row["rating"] == "buy"]
        others = [row["radarScore"] for row in rows if row["rating"] in {"maybe", "no"}]
        self.assertTrue(buys and others)

        separation = (sum(buys) / len(buys)) - (sum(others) / len(others))
        # Documentado, no celebrado: hoy la separación es prácticamente nula.
        self.assertLess(
            abs(separation), 12, "si el score empezó a separar de verdad, subí esta línea base"
        )

    def test_every_rating_is_represented_enough_to_measure(self) -> None:
        counts = Counter(row["rating"] for row in load())
        self.assertGreaterEqual(counts["buy"], 20)
        self.assertGreaterEqual(counts["maybe"], 10)


if __name__ == "__main__":
    unittest.main()
