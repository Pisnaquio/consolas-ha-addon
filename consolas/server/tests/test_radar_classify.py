from __future__ import annotations

import unittest

from server.radar.classify import classify_listing_item, detect_lot_size


class ClassifyListingItemTests(unittest.TestCase):
    """Los títulos de acá salen de publicaciones reales de producción.

    Cada uno estaba mal clasificado antes de este módulo, y cada error tenía
    consecuencia: peso de courier equivocado, o una vara de precio que no
    correspondía.
    """

    def assert_kind(self, title: str, expected: str) -> None:
        item = classify_listing_item(title)
        self.assertEqual(item.kind, expected, f"{title!r} -> {item.kind}")

    def test_a_console_is_a_console(self) -> None:
        self.assert_kind("Sony PlayStation 2 PS2 Slim Silver Console Complete", "console")
        self.assert_kind("Sony PlayStation 2 Slim SCPH-70012 Black", "console")

    def test_the_platform_name_alone_never_makes_it_a_console(self) -> None:
        # "Super Nintendo Entertainment System" contiene "system": sin sacar el
        # nombre de la plataforma, todo cartucho de SNES pasaba por consola.
        self.assert_kind("Disney's Aladdin (Super Nintendo Entertainment System, 1993)", "game")
        self.assert_kind("Disney's Aladdin Super Nintendo Entertainment System SNES - Cartridge Only", "game")

    def test_a_console_that_includes_controllers_is_still_a_console(self) -> None:
        self.assert_kind("Sega DreamCast Console - Tested - 2 Controllers - VMU - Hookups", "console")
        self.assert_kind("Playstation 2 PS2 Fat Black Game Console Bundle with 1 OEM Controller", "console")

    def test_a_part_for_a_console_is_not_a_console(self) -> None:
        # Un adaptador que nombra "Game Console" en su título seguía siendo un
        # adaptador; antes ganaba la palabra consola.
        self.assert_kind("For Sony PS1 PS2 to HDMI Adapter Cable Game Console Converter", "accessory")

    def test_an_accessory_is_not_measured_as_the_console_it_belongs_to(self) -> None:
        self.assert_kind("Sega Dreamcast VMU HKT-7000 Tested OEM Memory Card", "accessory")
        self.assert_kind("Playstation 2 PS2 Official OEM Sony Dualshock 2 Controller", "accessory")

    def test_an_empty_case_is_an_accessory_not_the_game(self) -> None:
        self.assert_kind("God of War PS2 EMPTY CASE ONLY no disc", "accessory")
        self.assert_kind("Sony Playstation 3 PS3 Premium Acrylic Game Display UV Case Protector", "accessory")

    def test_a_game_sold_with_its_case_is_still_a_game(self) -> None:
        # El reverso del anterior: "w/ case" describe un juego completo, y una
        # regla ingenua sobre "case" lo mandaba a accesorios.
        self.assert_kind("Pokemon Blue Version for Nintendo Game Boy Color game w/ case (1998)", "game")

    def test_a_console_bundle_that_includes_cables_is_still_a_console(self) -> None:
        # Un cable suelto es un accesorio, pero "Console Bundle - Controller /
        # Cables" describe una consola con cosas adentro.
        self.assert_kind(
            "Sony PlayStation 2 PS2 Slim Black Console Bundle - Controller / Cables / Tested",
            "console",
        )

    def test_a_loose_cable_is_still_an_accessory(self) -> None:
        self.assert_kind("Sony PlayStation 2 PS2 OEM AV Cable Cord", "accessory")

    def test_a_pick_your_game_listing_is_a_lot(self) -> None:
        self.assert_kind("Sony PlayStation 2 PS2 Games Pick Your Game Tested OEM", "lot")
        self.assert_kind("Sony PlayStation 2 PS2 Video Games Lot You Pick & Choose", "lot")
        self.assert_kind("Official OEM Sony PlayStation 3 PS3 Game Cases Lot X10", "lot")

    def test_a_retail_compilation_is_a_game_not_a_lot(self) -> None:
        self.assert_kind("Ratchet & Clank Collection PS3 New Sealed 3 Games Remastered", "game")

    def test_a_plain_title_is_taken_as_a_game_but_not_confidently(self) -> None:
        item = classify_listing_item("Kingdom Hearts (PlayStation 2) PS2 Tested")
        self.assertEqual(item.kind, "game")
        self.assertFalse(item.confident, "deducir por descarte no alcanza para descartar una publicación")

    def test_an_explicit_game_is_confident(self) -> None:
        item = classify_listing_item("Metal Gear Solid 2 Sons Of Liberty Sony Playstation 2 PS2 CIB")
        self.assertEqual(item.kind, "game")
        self.assertTrue(item.confident)

    def test_nothing_to_read_classifies_as_nothing(self) -> None:
        item = classify_listing_item("")
        self.assertEqual(item.kind, "")
        self.assertEqual(item.weighable, "")

    def test_a_lot_is_never_weighable(self) -> None:
        # El peso de un lote depende de cuántas piezas trae, y el título no lo
        # dice: inventarlo sería inventar el renglón más caro del cálculo.
        item = classify_listing_item("Sony PlayStation 2 PS2 Games Pick Your Game")
        self.assertEqual(item.kind, "lot")
        self.assertEqual(item.weighable, "")


class VariablePriceTests(unittest.TestCase):
    """Un "elegí cuál querés" publica el precio de su opción más barata."""

    def test_a_pick_and_choose_is_marked_variable(self) -> None:
        item = classify_listing_item("Sony Playstation 3 PS3 Disc Only Games Pick & Choose")
        self.assertEqual(item.kind, "lot")
        self.assertTrue(item.variable_price)

    def test_a_fixed_lot_is_not_variable(self) -> None:
        # "Lot of 10" tiene un precio real por el conjunto.
        item = classify_listing_item("Sony PlayStation 2 PS2 Video Games Lot of 10")
        self.assertEqual(item.kind, "lot")
        self.assertFalse(item.variable_price)

    def test_a_plain_game_is_never_variable(self) -> None:
        self.assertFalse(classify_listing_item("Red Dead Redemption Greatest Hits PS3").variable_price)

    def test_empty_cases_sold_by_the_piece_are_an_accessory(self) -> None:
        item = classify_listing_item("10 PCS New Original PS3 Game Case, Blu-Ray Logo")
        self.assertEqual(item.kind, "accessory")


if __name__ == "__main__":
    unittest.main()


class ReplacementCaseTests(unittest.TestCase):
    """Una caja de repuesto es una caja, no el juego que iría adentro.

    Apareció en producción cruzando un objetivo de juego a 6,49: el título dice
    "Game Replacement Case" y el marcador de juego se quedaba con la palabra
    "Game".
    """

    def test_a_replacement_case_is_an_accessory(self) -> None:
        item = classify_listing_item("PlayStation 2 (PS2) OEM Authentic Game Replacement Case Read Description")
        self.assertEqual(item.kind, "accessory")

    def test_a_case_only_listing_is_an_accessory(self) -> None:
        self.assertEqual(classify_listing_item("Final Fantasy X PS2 CASE ONLY no disc").kind, "accessory")

    def test_a_game_that_merely_includes_its_case_is_still_a_game(self) -> None:
        self.assertEqual(
            classify_listing_item("Pokemon Blue Version Game Boy Color game w/ case (1998)").kind, "game"
        )

    def test_a_complete_game_is_still_a_game(self) -> None:
        self.assertEqual(
            classify_listing_item("Metal Gear Solid 2 PS2 CIB Complete with case and manual").kind, "game"
        )


class DetectLotSizeTests(unittest.TestCase):
    """Cuántas piezas trae, cuando el vendedor lo escribe.

    Los títulos de acá salen de las corridas reales del radar. El detector
    existe para que el criterio «mínimo de piezas» pueda decidir algo: hasta
    ahora se guardaba, se editaba y se mostraba como chip, pero ninguna
    publicación se descartaba por traer menos piezas que el mínimo.
    """

    def assert_size(self, title: str, expected: int | None) -> None:
        self.assertEqual(detect_lot_size(title), expected, f"{title!r}")

    def test_a_declared_count_is_read(self) -> None:
        self.assert_size("Official OEM Sony PlayStation 3 PS3 Game Cases Lot X10", 10)
        self.assert_size("Lot of 11 Sony PSP Empty Game/Movie Cases No Manuals *NO GAMES! READ", 11)
        self.assert_size("Sony PlayStation 3 PS3 Empty Replacement Game Disc Case HIGH QUALITY - Lot of 5", 5)
        self.assert_size(
            "PS3 6-Game Action Lot - Ninja Gaiden 3, Midnight Club LA, Soulcalibur V, GTA IV", 6
        )
        self.assert_size(
            "Cabela's Hunting Games PlayStation 2 PS2 Bundle N/A Multicolor Good 2-Game Lot", 2
        )

    def test_a_count_written_as_a_word_is_read_too(self) -> None:
        self.assert_size("Sony PSP Portable UMD Video Superhero Movies - Lot of Four - Tested & Working", 4)

    def test_the_platform_number_is_never_the_count(self) -> None:
        # Sin sacar el nombre de la plataforma, "Playstation 2 Games Lot" sería
        # un lote de dos piezas, y "PS3 Games" uno de tres.
        self.assert_size("Sony Playstation 2 Games Lot Tested Working", None)
        self.assert_size("Sony PlayStation 2 PS2 Video Games Lot You Pick & Choose From Great Selection", None)
        self.assert_size("PlayStation 3 PS3 brand new Games Lot Pick And Choice Great Selection", None)

    def test_a_number_in_the_name_of_a_game_is_not_a_count(self) -> None:
        # "3 Game" separado y en singular es el nombre del juego. "3 Games" en
        # plural, o "6-Game" pegado, sí cuentan.
        self.assert_size("Tony Hawk's Pro Skater 3 Game PS2 Complete", None)
        self.assert_size("Ninja Gaiden 3 Razor's Edge PS3 Disc Only", None)
        self.assert_size("💎 Ratchet & Clank Collection PS3 New Sealed 3 Games Remastered PlayStation 3", 3)

    def test_a_lot_that_never_says_how_many_stays_undeclared(self) -> None:
        # Es la mitad del mercado real de lotes: se publica enumerando los
        # juegos, o invitando a elegir, y nunca contándolos.
        self.assert_size("Playstation 2 (PS2) Game Lot! Pick & Choose! Classic Retro Games!", None)
        self.assert_size("PS2 Lot: GTA III, Vice City, San Andreas, Bully", None)
        self.assert_size("", None)

    def test_two_counts_that_disagree_count_as_none(self) -> None:
        # Elegir cuál manda sería inventar el dato.
        self.assert_size("Retro Game Lot of 5 - 8 Games Total Read Description", None)

    def test_an_absurd_count_is_not_a_count(self) -> None:
        self.assert_size("Wholesale Lot of 900 Game Cases Bulk Resale", None)

    def test_no_count_is_invented_over_the_real_corpus(self) -> None:
        # La regresión que importa: el detector puede no ver una cantidad, pero
        # no puede ver una que no está. Sobre los títulos reales de calibración,
        # cada cantidad leída tiene que estar escrita en el título.
        import json
        from pathlib import Path

        fixtures = sorted((Path(__file__).parent / "fixtures").glob("calibration-*.json"))
        self.assertTrue(fixtures, "sin fixtures no hay corpus contra el que medir")
        declared = {}
        for fixture in fixtures:
            for row in json.loads(fixture.read_text(encoding="utf-8"))["listings"]:
                size = detect_lot_size(row["title"])
                if size is not None:
                    declared[row["title"]] = size

        self.assertEqual(
            declared,
            {
                "Official OEM Sony PlayStation 3 PS3 Game Cases Lot X10": 10,
                "Lot of 11 Sony PSP Empty Game/Movie Cases No Manuals *NO GAMES! READ": 11,
                "💎 Ratchet & Clank Collection PS3 New Sealed 3 Games Remastered PlayStation 3": 3,
                "Sony PSP-2001 PlayStation Portable Console w Charger and 2 games Tested Working!": 2,
                "Sony PlayStation 3 PS3 Empty Replacement Game Disc Case HIGH QUALITY - Lot of 5": 5,
                "PS3 6-Game Action Lot - Ninja Gaiden 3, Midnight Club LA, Soulcalibur V, GTA IV": 6,
                "Sony PSP Portable UMD Video Superhero Movies - Lot of Four - Tested & Working": 4,
                "Cabela's Hunting Games PlayStation 2 PS2 Bundle N/A Multicolor Good 2-Game Lot": 2,
            },
            "el detector cambió de opinión sobre el corpus real: revisar antes de mergear",
        )
