from __future__ import annotations

import unittest

from server.radar.classify import classify_listing_item


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


if __name__ == "__main__":
    unittest.main()
