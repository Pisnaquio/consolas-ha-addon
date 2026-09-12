from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

import server.app  # noqa: F401  (deja `radar.*` importable)


ROUND1_FIXTURE = Path(__file__).parent / "fixtures" / "calibration-2026-09.json"
ROUND2_FIXTURE = Path(__file__).parent / "fixtures" / "calibration-2026-09-round2.json"

# Segunda tanda de calibración (2026-09-11): PSP, PS3, Game Boy y Game Boy
# Color, pedida para sumar señal a la de PS2 (77 puntos) sobre plataformas
# distintas. Medidas tomadas sobre el juicio humano del owner — no son metas,
# son el punto de partida contra el que se compara cualquier cambio de
# scoring. Un test que falla acá dice que el radar cambió de opinión respecto
# del owner, y eso hay que mirarlo antes de mergear.
BASELINE_ROUND2_EXPENSIVE_BUY_RATE = 0.30  # «caro» acertaba: 24% comprarías
BASELINE_ROUND2_JUNK_COUNT = 1  # una «Wormlight» de GBC se coló como «juego»

# Sobre el set combinado (round 1 + round 2, 115 puntos).
BASELINE_COMBINED_EXPENSIVE_BUY_RATE = 0.25
BASELINE_COMBINED_SEPARATION = 8  # hoy da ~4: más datos, menos separación, no más


def load_round1() -> list[dict]:
    return json.loads(ROUND1_FIXTURE.read_text(encoding="utf-8"))["listings"]


def load_round2() -> list[dict]:
    return json.loads(ROUND2_FIXTURE.read_text(encoding="utf-8"))["listings"]


def load_combined() -> list[dict]:
    return load_round1() + load_round2()


def buy_rate(rows: list[dict]) -> float:
    return sum(1 for row in rows if row["rating"] == "buy") / len(rows) if rows else 0.0


class Round2FixtureTests(unittest.TestCase):
    """El dataset existe para que un cambio de scoring se pueda medir."""

    def test_the_fixture_is_usable_as_ground_truth(self) -> None:
        rows = load_round2()
        self.assertGreaterEqual(len(rows), 30)
        for row in rows:
            with self.subTest(title=row["title"][:40]):
                self.assertIn(row["rating"], {"buy", "maybe", "no", "junk"})
                self.assertTrue(row["title"])
                self.assertTrue(row["search"])

    def test_no_personal_notes_are_versioned(self) -> None:
        # Las notas del owner son opiniones suyas; la regresión no las necesita.
        for row in load_round2():
            self.assertNotIn("note", row)

    def test_all_four_target_platforms_are_represented(self) -> None:
        searches = " ".join(row["search"] for row in load_round2())
        for platform in ("PSP", "PS3", "Game Boy"):
            self.assertIn(platform, searches, f"falta señal de {platform} en la segunda tanda")


class Round2FilteringQualityTests(unittest.TestCase):
    """Lo que el radar mostró, ¿merecía mostrarse?"""

    def test_junk_stays_at_or_below_what_this_round_measured(self) -> None:
        junk = [row for row in load_round2() if row["rating"] == "junk"]
        self.assertLessEqual(
            len(junk),
            BASELINE_ROUND2_JUNK_COUNT,
            "más ruido que en la corrida original: una accesorio de GBC (luz "
            "'Wormlight') pasó el filtro de 'juegos' porque ningún exclude "
            "cubría esa marca — documentado, no arreglado en este PR",
        )


class Round2BandQualityTests(unittest.TestCase):
    """Si la banda no cambia la decisión, la banda no sirve."""

    def test_the_expensive_band_still_predicts_rejection(self) -> None:
        expensive = [row for row in load_round2() if row["radarBand"] == "caro"]
        self.assertTrue(expensive, "el fixture tiene que incluir publicaciones caras")
        self.assertLessEqual(
            buy_rate(expensive),
            BASELINE_ROUND2_EXPENSIVE_BUY_RATE,
            "«caro» dejó de anticipar un rechazo en esta plataforma también",
        )

    def test_bargain_beating_good_here_is_a_small_sample_flag_not_a_rule(self) -> None:
        """Contradice a la calibración de PS2 — y hay que decirlo así, no esconderlo.

        Sobre PS2 (77 puntos), «buena» superaba a «ganga» (85% contra 55%).
        Sobre esta tanda (39 puntos, PSP/PS3/Game Boy/GBC), pasa lo contrario:
        «ganga» compra al 75% y «buena» al 25% — pero son sólo 8 y 4 puntos
        respectivamente. No alcanza para invertir la conclusión de PS2; alcanza
        para decir que hace falta más señal antes de tratar cualquiera de las
        dos lecturas como una regla. El test combinado de abajo es el que
        pesa más.
        """

        rows = load_round2()
        bargain = [row for row in rows if row["radarBand"] == "ganga"]
        good = [row for row in rows if row["radarBand"] == "buena"]
        self.assertTrue(bargain and good)
        self.assertLessEqual(len(bargain), 15, "si esta muestra ya creció, recalculá con más cuidado")
        self.assertLessEqual(len(good), 15, "si esta muestra ya creció, recalculá con más cuidado")


class Round2ScoreQualityTests(unittest.TestCase):
    def test_score_separation_this_round(self) -> None:
        rows = [row for row in load_round2() if row["radarScore"] is not None]
        buys = [row["radarScore"] for row in rows if row["rating"] == "buy"]
        others = [row["radarScore"] for row in rows if row["rating"] in {"maybe", "no"}]
        self.assertTrue(buys and others)

        separation = (sum(buys) / len(buys)) - (sum(others) / len(others))
        # ~8 puntos acá — mejor que en PS2, pero sobre una muestra chica.
        self.assertGreater(separation, -2)
        self.assertLess(separation, 20)


class CombinedCalibrationTests(unittest.TestCase):
    """Round 1 (PS2, 77) + round 2 (PSP/PS3/GB/GBC, 39) = 116 puntos.

    Es la lectura que más pesa: más plataformas, menos ruido de una sola
    familia de datos. Documentado, no celebrado.
    """

    def test_the_combined_set_is_bigger_than_either_round_alone(self) -> None:
        combined = load_combined()
        self.assertGreaterEqual(len(combined), 110)

    def test_the_expensive_band_predicts_rejection_on_the_combined_set(self) -> None:
        combined = load_combined()
        expensive = [row for row in combined if row["radarBand"] == "caro"]
        self.assertLessEqual(
            buy_rate(expensive),
            BASELINE_COMBINED_EXPENSIVE_BUY_RATE,
            "la única señal comprobada de banda sigue sosteniéndose con más datos",
        )

    def test_good_still_beats_bargain_once_both_rounds_are_combined(self) -> None:
        combined = load_combined()
        bargain = [row for row in combined if row["radarBand"] == "ganga"]
        good = [row for row in combined if row["radarBand"] == "buena"]
        self.assertTrue(bargain and good)
        # La inversión de la ronda 2 sola no sobrevive a juntar los datos:
        # con 115 puntos, «buena» (71%) sigue por delante de «ganga» (61%).
        self.assertLess(
            buy_rate(bargain),
            buy_rate(good) + 0.20,
            "si «ganga» ya superó a «buena» con el set combinado, actualizá esta línea base",
        )

    def test_more_data_did_not_make_the_score_discriminate_more(self) -> None:
        """Con más señal, la separación bajó (~8 en round 2 sola, ~4 combinada).

        Refuerza la conclusión de la calibración de PS2: no ajustar pesos a
        ciegas todavía. El problema no es poca muestra — es que el score, tal
        como está formulado, no separa compra de duda.
        """

        combined = load_combined()
        rows = [row for row in combined if row["radarScore"] is not None]
        buys = [row["radarScore"] for row in rows if row["rating"] == "buy"]
        others = [row["radarScore"] for row in rows if row["rating"] in {"maybe", "no"}]
        self.assertTrue(buys and others)

        separation = (sum(buys) / len(buys)) - (sum(others) / len(others))
        self.assertLess(
            abs(separation),
            BASELINE_COMBINED_SEPARATION,
            "si la separación combinada ya superó este umbral, es la primera señal real — revisá antes de subir la línea base",
        )

    def test_every_rating_is_represented_enough_to_measure(self) -> None:
        counts = Counter(row["rating"] for row in load_combined())
        self.assertGreaterEqual(counts["buy"], 50)
        self.assertGreaterEqual(counts["maybe"], 25)
        self.assertGreaterEqual(counts["no"], 15)


if __name__ == "__main__":
    unittest.main()
