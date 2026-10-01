"""El radar usa el precio que el catálogo ya tenía.

Durante meses el catálogo guardó `priceRange` con fuente y fecha para cientos
de juegos y el radar no lo miró nunca: juzgaba cada juego contra la mediana de
su propia corrida, que es compararse consigo mismo. Con una búsqueda de
biblioteca —treinta y cinco títulos distintos en la misma corrida— esa mediana
mezcla un Castlevania con un Burnout Legends y no describe a ninguno.

Lo que se prueba acá no es el número: es que la referencia exista, que diga de
dónde sale, que no se use para una pieza que no es ese juego, y que no invente
nada cuando el catálogo no tiene precio.
"""

from __future__ import annotations

import unittest

from server.radar.valuation import (
    PriceReference,
    peer_listing_benchmark,
    pick_benchmark,
    reference_from_price_range,
)


class PriceRangeReferenceTests(unittest.TestCase):
    def test_a_catalog_price_becomes_a_reference_that_names_its_origin(self) -> None:
        ref = reference_from_price_range(
            {"low": 5.99, "mid": 7.99, "high": 9.99, "notes": "consultados el 2026-09-15"},
            entity_id="xbox-360-bioshock",
            observed_at="2026-09-15",
        )
        self.assertIsNotNone(ref)
        self.assertEqual(ref.value, 7.99)
        self.assertEqual(ref.entity_id, "xbox-360-bioshock")
        self.assertEqual(ref.completeness, "loose")
        # La advertencia no es opcional: un precio de tienda está por encima de
        # lo que cierra una compra entre particulares.
        self.assertIn("particulares", ref.notes)
        self.assertIn("5.99", ref.notes)

    def test_without_a_date_the_reference_says_so_and_trusts_itself_less(self) -> None:
        con = reference_from_price_range({"mid": 20}, observed_at="2026-09-15")
        sin = reference_from_price_range({"mid": 20}, observed_at="")
        self.assertGreater(con.confidence, sin.confidence)
        self.assertIn("Sin fecha", sin.notes)

    def test_no_price_means_no_reference(self) -> None:
        """Un juego sin precio no recibe uno inventado."""
        self.assertIsNone(reference_from_price_range({"low": None, "mid": None, "high": None}))
        self.assertIsNone(reference_from_price_range(None))
        # Y un cero tampoco es un precio: `to_amount` lo descarta.
        self.assertIsNone(reference_from_price_range({"mid": 0}))

    def test_the_catalog_beats_the_median_of_the_same_run(self) -> None:
        """Un precio de tienda pesa más que lo que pide cualquiera en esa corrida."""
        catalogo = reference_from_price_range({"mid": 12}, entity_id="g", observed_at="2026-09-15")
        pares = peer_listing_benchmark([10, 20, 30, 40], entity_id="g")
        elegido = pick_benchmark([pares, catalogo], completeness="loose")
        self.assertEqual(elegido.source, "retail")

    def test_the_median_still_serves_when_the_catalog_has_nothing(self) -> None:
        pares = peer_listing_benchmark([10, 20, 30, 40], entity_id="g")
        elegido = pick_benchmark([pares], completeness="loose")
        self.assertEqual(elegido.source, "peer-listings")

    def test_a_sealed_search_does_not_get_judged_by_a_loose_price(self) -> None:
        """La referencia es de copia suelta: no sirve para medir una sellada."""
        catalogo = reference_from_price_range({"mid": 12}, entity_id="g")
        self.assertIsNone(pick_benchmark([catalogo], completeness="sealed"))


if __name__ == "__main__":
    unittest.main()
