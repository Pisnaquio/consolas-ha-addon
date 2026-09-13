from __future__ import annotations

import unittest
from datetime import date

import server.app  # noqa: F401  (deja `radar.*` importable)
from server.app import valuate_radar_match
from radar.valuation import (
    COURIER_RATES,
    LotPiece,
    PriceReference,
    billable_weight_kg,
    courier_cost,
    estimate_weight_kg,
    evaluate_target,
    lot_valuation,
    peer_listing_benchmark,
    cost_breakdown,
    decision_band,
    pick_benchmark,
    references_from_console_entry,
    score_listing,
)


TODAY = date(2026, 9, 11)

# Entrada real del catálogo: `precioEbaySold` repite `precioPriceChart` y su
# propia nota lo declara un proxy.
PS1_ENTRY = {
    "id": "ps1",
    "precioPriceChart": 55,
    "precioGameStop": 90,
    "precioEbaySold": 55,
    "precioCIB": 80,
    "precioObjetivoCompra": 39,
    "precioNotas": {
        "verificadoEn": "2026-03-23",
        "fuentes": {
            "precioPriceChart": "PriceCharting (match validado)",
            "precioEbaySold": "PriceCharting loose (proxy)",
            "precioCIB": "PriceCharting (CIB)",
        },
    },
}


class ProvenanceTests(unittest.TestCase):
    """Dos campos que copian la misma fuente no son dos evidencias."""

    def test_a_proxy_price_is_marked_as_not_independent(self) -> None:
        refs = {(ref.source, ref.completeness): ref for ref in references_from_console_entry(PS1_ENTRY)}
        sold = refs[("ebay-sold", "loose")]
        self.assertFalse(sold.independent)
        self.assertLess(sold.confidence, 0.5)
        self.assertIn("proxy", sold.notes.lower())

    def test_a_genuinely_independent_sold_price_keeps_its_weight(self) -> None:
        entry = {
            "id": "snes",
            "precioPriceChart": 100,
            "precioEbaySold": 118,
            "precioNotas": {"verificadoEn": "2026-09-01", "fuentes": {"precioEbaySold": "Mediana de ventas cerradas"}},
        }
        sold = next(ref for ref in references_from_console_entry(entry) if ref.source == "ebay-sold")
        self.assertTrue(sold.independent)
        self.assertGreater(sold.confidence, 0.8)

    def test_the_internal_target_price_is_never_a_benchmark(self) -> None:
        values = [ref.value for ref in references_from_console_entry(PS1_ENTRY)]
        self.assertNotIn(39, values, "precioObjetivoCompra es una regla interna, no una observación")

    def test_a_reference_reports_its_own_age(self) -> None:
        ref = next(ref for ref in references_from_console_entry(PS1_ENTRY) if ref.source == "pricecharting")
        self.assertEqual(ref.age_days(TODAY), 172)
        self.assertFalse(ref.is_stale(TODAY))
        self.assertTrue(PriceReference("x", "pricecharting", 10, observed_at="2025-01-01").is_stale(TODAY))


class BenchmarkTests(unittest.TestCase):
    def test_loose_is_compared_against_loose_and_cib_against_cib(self) -> None:
        refs = references_from_console_entry(PS1_ENTRY)
        self.assertEqual(pick_benchmark(refs, completeness="loose", today=TODAY).completeness, "loose")
        cib = pick_benchmark(refs, completeness="cib", today=TODAY)
        self.assertEqual(cib.completeness, "cib")
        self.assertEqual(cib.value, 80)

    def test_an_independent_reference_wins_over_a_proxy(self) -> None:
        refs = [
            PriceReference("ps1", "ebay-sold", 55, independent=False, confidence=0.35),
            PriceReference("ps1", "pricecharting", 55, independent=True, confidence=0.8),
        ]
        self.assertEqual(pick_benchmark(refs, today=TODAY).source, "pricecharting")

    def test_a_weak_reference_is_used_when_there_is_no_other(self) -> None:
        refs = [PriceReference("ps1", "ebay-sold", 55, independent=False, confidence=0.35)]
        chosen = pick_benchmark(refs, today=TODAY)
        self.assertIsNotNone(chosen, "mejor evidencia débil declarada que ninguna")
        self.assertFalse(chosen.independent)

    def test_no_comparable_reference_returns_nothing(self) -> None:
        refs = [PriceReference("ps1", "pricecharting", 80, completeness="cib")]
        self.assertIsNone(pick_benchmark(refs, completeness="loose", today=TODAY))


class BandTests(unittest.TestCase):
    def test_the_bands_follow_the_prd(self) -> None:
        self.assertEqual(decision_band(0.60)[0], "ganga")
        self.assertEqual(decision_band(0.75)[0], "ganga")
        self.assertEqual(decision_band(0.85)[0], "buena")
        self.assertEqual(decision_band(1.00)[0], "razonable")
        self.assertEqual(decision_band(1.20)[0], "premium")
        self.assertEqual(decision_band(1.45)[0], "caro")

    def test_a_loose_console_at_the_cib_price_reads_as_expensive(self) -> None:
        """La regla explícita del PRD §24: loose a precio de CIB queda fuera de precio."""
        refs = references_from_console_entry(PS1_ENTRY)
        loose_benchmark = pick_benchmark(refs, completeness="loose", today=TODAY)
        cib_price = next(ref for ref in refs if ref.completeness == "cib").value

        card = score_listing(
            price_amount=cib_price, shipping_amount=0.0, benchmark=loose_benchmark, today=TODAY
        )
        self.assertEqual(card.band, "caro")
        self.assertGreater(card.ratio, 1.30)


class CostTests(unittest.TestCase):
    def test_item_and_shipping_stay_separate(self) -> None:
        cost = cost_breakdown(149.99, 12.0)
        self.assertEqual(cost["item"], 149.99)
        self.assertEqual(cost["shipping"], 12.0)
        self.assertEqual(cost["subtotalUsa"], 161.99)
        self.assertTrue(cost["exact"])

    def test_an_unknown_shipping_is_declared_not_assumed_zero(self) -> None:
        cost = cost_breakdown(149.99, None)
        self.assertFalse(cost["shippingKnown"])
        self.assertFalse(cost["exact"])
        self.assertEqual(cost["subtotalUsa"], 149.99)

    def test_the_imported_cost_is_not_pretended(self) -> None:
        self.assertFalse(cost_breakdown(100.0, 10.0)["importedEstimated"])


class ScoreTests(unittest.TestCase):
    def benchmark(self) -> PriceReference:
        return PriceReference("ps1", "pricecharting", 100.0, observed_at="2026-09-01", confidence=0.8)

    def test_a_bargain_scores_higher_than_a_premium(self) -> None:
        bargain = score_listing(price_amount=60.0, shipping_amount=0.0, benchmark=self.benchmark(), today=TODAY)
        premium = score_listing(price_amount=125.0, shipping_amount=0.0, benchmark=self.benchmark(), today=TODAY)
        self.assertGreater(bargain.score, premium.score)
        self.assertEqual(bargain.band, "ganga")
        self.assertEqual(premium.band, "premium")

    def test_every_score_explains_where_its_points_come_from(self) -> None:
        card = score_listing(price_amount=80.0, shipping_amount=10.0, benchmark=self.benchmark(), today=TODAY)
        dimensions = {item["dimension"] for item in card.contributions}
        self.assertEqual(
            dimensions, {"collection", "price", "condition", "completeness", "seller", "logistics"}
        )
        for item in card.contributions:
            with self.subTest(dimension=item["dimension"]):
                self.assertTrue(item["detail"])
                self.assertLessEqual(item["points"], item["max"])

    def test_a_proxy_benchmark_is_penalized_and_declared(self) -> None:
        proxy = PriceReference("ps1", "ebay-sold", 100.0, independent=False, observed_at="2026-09-01")
        card = score_listing(price_amount=80.0, shipping_amount=5.0, benchmark=proxy, today=TODAY)
        self.assertTrue(any("independiente" in item["detail"] for item in card.penalties))
        self.assertTrue(any("corroboración" in caveat for caveat in card.caveats))

    def test_a_stale_benchmark_is_penalized_and_still_shown(self) -> None:
        stale = PriceReference("ps1", "pricecharting", 100.0, observed_at="2024-01-01")
        card = score_listing(price_amount=80.0, shipping_amount=5.0, benchmark=stale, today=TODAY)
        self.assertTrue(any("días" in item["detail"] for item in card.penalties))
        self.assertIsNotNone(card.benchmark, "una referencia vieja sigue visible como referencia")

    def test_without_a_benchmark_the_price_is_not_judged(self) -> None:
        card = score_listing(price_amount=80.0, shipping_amount=5.0, benchmark=None, today=TODAY)
        self.assertIsNone(card.ratio)
        self.assertEqual(card.band, "sin-referencia")
        self.assertTrue(any("Sin benchmark" in caveat for caveat in card.caveats))

    def test_an_untested_unit_is_penalized(self) -> None:
        tested = score_listing(
            price_amount=80.0, shipping_amount=5.0, benchmark=self.benchmark(),
            match_reasons=["Declara estar probada («tested»)"], today=TODAY,
        )
        untested = score_listing(
            price_amount=80.0, shipping_amount=5.0, benchmark=self.benchmark(),
            match_reasons=["Se declara «untested»: riesgo a descontar del precio"], today=TODAY,
        )
        self.assertGreater(tested.score, untested.score)
        self.assertTrue(any("Sin probar" in item["detail"] for item in untested.penalties))

    def test_disproportionate_shipping_is_penalized(self) -> None:
        card = score_listing(price_amount=40.0, shipping_amount=30.0, benchmark=self.benchmark(), today=TODAY)
        self.assertTrue(any("envío es el" in item["detail"].lower() for item in card.penalties))

    def test_unverified_requirements_cost_points(self) -> None:
        clean = score_listing(price_amount=80.0, shipping_amount=5.0, benchmark=self.benchmark(), today=TODAY)
        murky = score_listing(
            price_amount=80.0, shipping_amount=5.0, benchmark=self.benchmark(),
            match_unverified=["Región sin declarar", "Completitud sin declarar"], today=TODAY,
        )
        self.assertGreater(clean.score, murky.score)
        self.assertEqual(len(murky.penalties), 2)

    def test_the_score_stays_inside_its_bounds(self) -> None:
        best = score_listing(
            price_amount=10.0, shipping_amount=0.0, benchmark=self.benchmark(), match_confidence=1.0,
            match_reasons=["Declara estar probada", "Completitud declarada: CIB"], seller_known=True, today=TODAY,
        )
        worst = score_listing(
            price_amount=400.0, shipping_amount=None, benchmark=None, match_confidence=0.05,
            match_reasons=["Se declara «untested»"], match_unverified=["a", "b", "c", "d", "e"], today=TODAY,
        )
        self.assertLessEqual(best.score, 100)
        self.assertGreaterEqual(worst.score, 0)
        self.assertGreater(best.score, worst.score)


class RunIntegrationTests(unittest.TestCase):
    """La valuación tiene que llegar a la coincidencia guardada, no quedar suelta."""

    def setUp(self) -> None:
        import json
        import tempfile
        from pathlib import Path as _Path
        from unittest.mock import patch

        from server.app import create_radar_search, delete_radar_search, init_db, list_radar_searches, run_radar_search

        self.patch = patch
        self.create = create_radar_search
        self.run = run_radar_search
        self.list = list_radar_searches

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        class Config:
            data_dir = _Path(self.temp_dir.name) / "data"
            static_dir = _Path(self.temp_dir.name) / "web"
            media_dir = data_dir / "media"
            auction_watch_dir = data_dir / "auction-watch"
            db_path = data_dir / "consolas.sqlite"
            max_body_size = 1024 * 1024
            ebay_client_id = "client"
            ebay_client_secret = "secret"
            ebay_environment = "production"

        self.config = Config()
        (self.config.static_dir / "data").mkdir(parents=True)
        (self.config.static_dir / "data" / "consoles.json").write_text(
            json.dumps({"consolas": [PS1_ENTRY]}), encoding="utf-8"
        )
        init_db(self.config)
        delete_radar_search(self.config, "iss-deluxe-snes")

    def summary(self, price: str) -> dict:
        return {
            "itemId": f"v1|{price}|0",
            "title": "Sony PlayStation 1 console tested",
            "itemWebUrl": "https://www.ebay.com/itm/1",
            "price": {"value": price, "currency": "USD"},
            "condition": "Pre-owned",
            "buyingOptions": ["FIXED_PRICE"],
            "itemLocation": {"country": "US"},
            "image": {},
            "shippingOptions": [
                {"shippingCostType": "FIXED", "shippingCost": {"value": "0.00", "currency": "USD"}}
            ],
        }

    def run_with(self, price: str) -> dict:
        search = self.create(
            self.config,
            {"name": f"PS1 {price}", "entityType": "console", "entityId": "ps1", "criteria": {"includeTerms": "tested"}},
        )["search"]
        with self.patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[self.summary(price)]
        ):
            self.run(self.config, search["id"])
        item = next(entry for entry in self.list(self.config)["items"] if entry["id"] == search["id"])
        return item["results"][0]

    def test_a_cheap_listing_lands_in_a_better_band_than_an_expensive_one(self) -> None:
        # El benchmark loose de PS1 en el catálogo es 55.
        bargain = self.run_with("35.00")
        expensive = self.run_with("95.00")

        self.assertEqual(bargain["band"], "ganga")
        self.assertEqual(expensive["band"], "caro")
        self.assertGreater(bargain["score"], expensive["score"])

    def test_the_stored_valuation_explains_itself(self) -> None:
        result = self.run_with("50.00")
        valuation = result["valuation"]

        self.assertEqual(valuation["cost"]["item"], 50.0)
        self.assertTrue(valuation["cost"]["shippingKnown"])
        self.assertTrue(valuation["contributions"])
        self.assertEqual(valuation["benchmark"]["completeness"], "loose")
        # El catálogo de PS1 copia PriceCharting en eBay sold: no puede pasar por
        # evidencia independiente ni siquiera acá.
        self.assertTrue(
            valuation["benchmark"]["independent"] or any("independiente" in item["detail"] for item in valuation["penalties"])
        )

    def test_a_search_without_a_linked_entity_says_it_has_no_benchmark(self) -> None:
        search = self.create(self.config, {"name": "Sin entidad", "criteria": {"includeTerms": "tested"}})["search"]
        with self.patch(
            "radar.sources.ebay.EbayBrowseSource.fetch_item_summaries", return_value=[self.summary("50.00")]
        ):
            self.run(self.config, search["id"])
        result = next(
            entry for entry in self.list(self.config)["items"] if entry["id"] == search["id"]
        )["results"][0]

        self.assertEqual(result["band"], "sin-referencia")
        self.assertIsNone(result["valuation"]["ratio"])
        self.assertTrue(any("Sin benchmark" in caveat for caveat in result["valuation"]["caveats"]))


class CourierTests(unittest.TestCase):
    """Tarifa vigente: fracciones de 100 g, mínimo 100 g, manejo incluido."""

    def test_the_rates_match_the_courier_table(self) -> None:
        self.assertEqual(COURIER_RATES["usa"]["general"], 17.50)
        self.assertEqual(COURIER_RATES["usa"]["media"], 10.50)
        self.assertEqual(COURIER_RATES["europa"]["general"], 21.50)
        self.assertEqual(COURIER_RATES["europa"]["media"], 14.50)

    def test_weight_is_billed_in_whole_hundred_gram_fractions(self) -> None:
        self.assertEqual(billable_weight_kg(0.31), 0.4)
        self.assertEqual(billable_weight_kg(0.40), 0.4)
        self.assertEqual(billable_weight_kg(2.51), 2.6)

    def test_anything_lighter_than_the_minimum_pays_the_minimum(self) -> None:
        self.assertEqual(billable_weight_kg(0.02), 0.1)
        self.assertEqual(courier_cost(0.02, "usa", "general"), round(0.1 * 17.50, 2))

    def test_a_loose_game_from_usa_costs_its_fraction(self) -> None:
        # 0,15 kg → 0,2 kg facturables × 17,50
        self.assertEqual(courier_cost(estimate_weight_kg("game", "loose"), "usa", "general"), 3.5)

    def test_the_reduced_media_rate_is_never_applied_on_its_own(self) -> None:
        general = courier_cost(1.0, "usa", "general")
        media = courier_cost(1.0, "usa", "media")
        self.assertGreater(general, media)
        # El default del desglose es la tarifa general: la reducida exige pedirla.
        self.assertEqual(cost_breakdown(50.0, 5.0, entity_type="game")["courierCategory"], "general")

    def test_an_unknown_origin_or_category_yields_no_invented_cost(self) -> None:
        self.assertIsNone(courier_cost(1.0, "japon", "general"))
        self.assertIsNone(courier_cost(1.0, "usa", "expres"))
        self.assertIsNone(courier_cost(None, "usa", "general"))


class ImportedCostTests(unittest.TestCase):
    def test_a_console_adds_its_courier_on_top_of_the_usa_subtotal(self) -> None:
        cost = cost_breakdown(150.0, 12.0, entity_type="console", completeness="loose")
        self.assertEqual(cost["subtotalUsa"], 162.0)
        self.assertEqual(cost["estimatedWeightKg"], 3.0)
        self.assertEqual(cost["courier"], round(3.0 * 17.50, 2))
        self.assertEqual(cost["importedTotal"], round(162.0 + 3.0 * 17.50, 2))
        self.assertTrue(cost["importedEstimated"])

    def test_a_lot_gets_no_invented_imported_cost(self) -> None:
        # Un lote no declara entidad: su peso depende de cuántas piezas trae.
        cost = cost_breakdown(189.0, 25.0, entity_type="")
        self.assertIsNone(cost["estimatedWeightKg"])
        self.assertIsNone(cost["importedTotal"])
        self.assertFalse(cost["importedEstimated"])

    def test_import_tax_is_explicit_and_zero(self) -> None:
        # Confirmado por el owner: con este courier y estos volúmenes siempre
        # da cero. Se declara en el desglose en vez de omitirse en silencio.
        cost = cost_breakdown(150.0, 12.0, entity_type="console", completeness="loose")
        self.assertEqual(cost["importTax"], 0.0)
        self.assertEqual(cost["importedTotal"], round(162.0 + 3.0 * 17.50 + 0.0, 2))

    def test_the_imported_total_never_passes_as_exact(self) -> None:
        cost = cost_breakdown(100.0, 10.0, entity_type="game", completeness="cib")
        self.assertTrue(cost["exact"], "artículo y envío sí son exactos")
        self.assertTrue(cost["importedEstimated"], "el importado siempre es estimación")

    def test_the_score_declares_the_imported_estimate(self) -> None:
        card = score_listing(
            price_amount=100.0, shipping_amount=10.0,
            benchmark=PriceReference("ps2", "retail", 160.0, observed_at="2026-09-01"),
            entity_type="console", today=TODAY,
        )
        self.assertTrue(any("Costo importado estimado" in caveat for caveat in card.caveats))
        self.assertTrue(any("peso es una estimación" in caveat for caveat in card.caveats))

    def test_a_piece_without_an_estimable_weight_says_so(self) -> None:
        card = score_listing(
            price_amount=100.0, shipping_amount=10.0, benchmark=None, entity_type="manual", today=TODAY
        )
        self.assertTrue(any("sin peso estimable" in caveat.lower() for caveat in card.caveats))


class PeerListingBenchmarkTests(unittest.TestCase):
    """La única referencia disponible para juegos: el catálogo sólo tiene consolas."""

    def test_the_median_of_comparable_listings_becomes_a_reference(self) -> None:
        # Precios reales de una corrida de Aladdin SNES.
        ref = peer_listing_benchmark([14.99, 20.99, 19.95, 19.95, 16.99, 14.99], entity_id="aladdin")
        self.assertEqual(ref.value, 18.47)
        self.assertEqual(ref.source, "peer-listings")

    def test_too_few_listings_produce_no_reference(self) -> None:
        # Con tres publicaciones, una rara arrastra la mediana entera.
        self.assertIsNone(peer_listing_benchmark([15.0, 20.0, 400.0]))

    def test_asking_prices_are_independent_evidence_but_weak(self) -> None:
        ref = peer_listing_benchmark([10.0, 12.0, 14.0, 16.0])
        self.assertTrue(ref.independent, "son precios reales que alguien está pidiendo")
        self.assertLessEqual(ref.confidence, 0.5, "pero pedidos no es vendidos")
        self.assertIn("no vendidos", ref.notes)

    def test_a_curated_reference_always_wins(self) -> None:
        peers = peer_listing_benchmark([10.0, 12.0, 14.0, 16.0], entity_id="ps1")
        for stronger in ("pricecharting", "retail", "ebay-sold"):
            with self.subTest(source=stronger):
                curated = PriceReference("ps1", stronger, 55.0)
                self.assertEqual(pick_benchmark([peers, curated], today=TODAY).source, stronger)

    def test_it_is_used_when_nothing_else_exists(self) -> None:
        peers = peer_listing_benchmark([10.0, 12.0, 14.0, 16.0], entity_id="aladdin")
        self.assertEqual(pick_benchmark([peers], today=TODAY).source, "peer-listings")

    def test_the_card_says_the_reference_came_from_asking_prices(self) -> None:
        peers = peer_listing_benchmark([14.99, 16.99, 19.95, 20.99], entity_id="aladdin")
        card = score_listing(price_amount=14.99, shipping_amount=0.0, benchmark=peers, today=TODAY)
        self.assertEqual(card.band, "buena")
        self.assertTrue(any("no vendidos" in caveat for caveat in card.caveats))

    def test_zero_and_negative_prices_are_ignored(self) -> None:
        ref = peer_listing_benchmark([0, -5, 10.0, 12.0, 14.0, 16.0])
        self.assertEqual(ref.value, 13.0)


class LotValuationTests(unittest.TestCase):
    """PRD §10.5, la fórmula tal cual: valor conservador, valor útil, costo por
    pieza útil y descuento — nunca un score opaco."""

    def test_the_formula_matches_the_prd_exactly(self) -> None:
        pieces = [
            LotPiece("PS2 Slim", comparable_value=60.0, condition_factor=1.0),
            LotPiece("God of War", comparable_value=20.0, condition_factor=1.0),
            LotPiece("Sports game nadie quiere", comparable_value=5.0, condition_factor=1.0, wanted=False),
        ]
        result = lot_valuation(pieces, total_cost=50.0)

        self.assertEqual(result["conservativeValue"], 85.0)  # 60+20+5
        self.assertEqual(result["usefulValue"], 80.0)  # 60+20, el que no se quiere queda afuera
        self.assertEqual(result["usefulPieceCount"], 2)
        self.assertEqual(result["costPerUsefulPiece"], 25.0)  # 50 / 2
        self.assertAlmostEqual(result["discount"], round(1 - 50.0 / 85.0, 4))

    def test_a_piece_already_owned_is_not_useful_even_if_wanted(self) -> None:
        pieces = [LotPiece("Duplicado que ya tengo", comparable_value=30.0, already_owned=True)]
        result = lot_valuation(pieces, total_cost=25.0)

        self.assertEqual(result["usefulPieceCount"], 0)
        self.assertIsNone(result["usefulValue"])
        self.assertIsNone(result["costPerUsefulPiece"], "no hay pieza útil que repartirlo")
        self.assertTrue(any("ya las tenés todas" in c for c in result["caveats"]))

    def test_condition_factor_discounts_a_rough_piece(self) -> None:
        pieces = [LotPiece("Caja rota", comparable_value=40.0, condition_factor=0.5)]
        result = lot_valuation(pieces, total_cost=15.0)

        self.assertEqual(result["conservativeValue"], 20.0)  # 40 * 0.5

    def test_duplicates_are_flagged_and_excluded_from_useful_value(self) -> None:
        pieces = [
            LotPiece("Aladdin #1", comparable_value=15.0),
            LotPiece("Aladdin #2 (duplicado)", comparable_value=15.0, is_duplicate=True),
        ]
        result = lot_valuation(pieces, total_cost=20.0)

        self.assertEqual(result["duplicateCount"], 1)
        self.assertEqual(result["usefulPieceCount"], 1)
        self.assertTrue(any("duplicada" in c for c in result["caveats"]))

    def test_a_piece_with_no_price_reference_is_excluded_not_zeroed(self) -> None:
        pieces = [
            LotPiece("Con referencia", comparable_value=10.0),
            LotPiece("Sin referencia todavía", comparable_value=None),
        ]
        result = lot_valuation(pieces, total_cost=12.0)

        self.assertEqual(result["conservativeValue"], 10.0, "la pieza sin precio no cuenta como cero")
        self.assertEqual(result["unpricedCount"], 1)
        self.assertTrue(any("sin referencia" in c for c in result["caveats"]))

    def test_no_pieces_declared_says_so_instead_of_computing_nothing_as_zero(self) -> None:
        result = lot_valuation([], total_cost=50.0)

        self.assertIsNone(result["conservativeValue"])
        self.assertIsNone(result["discount"])
        self.assertTrue(any("Sin piezas" in c for c in result["caveats"]))

    def test_without_a_total_cost_there_is_no_discount_or_cost_per_piece(self) -> None:
        pieces = [LotPiece("PS2", comparable_value=60.0)]
        result = lot_valuation(pieces, total_cost=None)

        self.assertIsNone(result["discount"])
        self.assertIsNone(result["costPerUsefulPiece"])
        self.assertEqual(result["conservativeValue"], 60.0, "el valor de las piezas no depende del costo")




class TargetLandedPriceTests(unittest.TestCase):
    """El objetivo se mide contra el total puesto acá, no contra el artículo."""

    def test_no_target_means_no_verdict_at_all(self) -> None:
        self.assertIsNone(evaluate_target(90.0, None))
        self.assertIsNone(evaluate_target(90.0, 0))

    def test_it_compares_against_the_landed_total(self) -> None:
        result = evaluate_target(78.0, 80.0)
        self.assertTrue(result["meets"])
        self.assertEqual(result["gap"], -2.0)

    def test_above_the_target_reports_how_far(self) -> None:
        result = evaluate_target(95.5, 80.0)
        self.assertFalse(result["meets"])
        self.assertEqual(result["gap"], 15.5)

    def test_without_a_landed_cost_there_is_no_verdict(self) -> None:
        # Un lote no tiene costo puesto acá: decir que cumple sería inventarlo.
        result = evaluate_target(None, 80.0)
        self.assertIsNone(result["meets"])
        self.assertIsNone(result["landedTotal"])

    def test_the_target_never_changes_the_score(self) -> None:
        common = dict(
            price_amount=40.0, shipping_amount=0.0, benchmark=None,
            match_confidence=1.0, entity_type="game",
        )
        sin_objetivo = score_listing(**common)
        con_objetivo = score_listing(**common, target_landed_price=200.0)
        self.assertEqual(sin_objetivo.score, con_objetivo.score)
        self.assertTrue(con_objetivo.target["meets"], "pero sí queda registrado en la card")


class ListingKindDrivenValuationTests(unittest.TestCase):
    """La pieza se costea y se compara por lo que es, no por lo que se buscaba."""

    def criteria(self) -> dict:
        return {"completeness": "any", "currency": "USD"}

    def verdict(self):
        from radar.matching import MatchVerdict

        v = MatchVerdict()
        v.confidence = 1.0
        return v

    def listing(self, title: str, price: float = 49.99):
        from radar.model import MarketplaceListing

        return MarketplaceListing(
            source_id="ebay-us", external_id="1", title=title,
            listing_url="https://www.ebay.com/itm/1", price_amount=price,
            price_currency="USD", shipping_amount=0.0, shipping_currency="USD",
        )

    def test_a_game_is_not_shipped_at_the_weight_of_a_console(self) -> None:
        # El bug medido en producción: un juego de USD 49,99 capturado por una
        # búsqueda de consola se costeaba con 3 kg y llegaba a USD 102,49.
        card = valuate_radar_match(
            self.listing("Metal Gear Solid 2 Sony Playstation 2 PS2 CIB"),
            self.verdict(), self.criteria(), [], "console",
        )
        cost = card.to_dict()["cost"]
        self.assertLess(cost["billableWeightKg"], 1.0, "un juego suelto no pesa como una consola")
        self.assertLess(cost["courier"], 10.0)

    def test_a_console_still_ships_as_a_console(self) -> None:
        card = valuate_radar_match(
            self.listing("Sony PlayStation 2 PS2 Slim Console Tested"),
            self.verdict(), self.criteria(), [], "console",
        )
        self.assertGreaterEqual(card.to_dict()["cost"]["billableWeightKg"], 3.0)

    def test_a_lot_gets_no_invented_shipping_cost(self) -> None:
        card = valuate_radar_match(
            self.listing("Sony PlayStation 2 PS2 Games Pick Your Game"),
            self.verdict(), self.criteria(), [], "console",
        )
        self.assertIsNone(card.to_dict()["cost"]["courier"])

    def test_an_accessory_is_not_priced_against_the_console(self) -> None:
        # El VMU de Dreamcast a USD 34 salía "ganga real" y primero en el feed
        # porque se comparaba contra los USD 141 de la consola.
        console_reference = PriceReference("dreamcast", "pricecharting", 141.0, confidence=0.8)
        card = valuate_radar_match(
            self.listing("Sega Dreamcast VMU HKT-7000 Tested OEM Memory Card", price=34.49),
            self.verdict(), self.criteria(), [console_reference], "console",
        )
        self.assertIsNone(card.to_dict()["benchmark"], "sin vara propia, mejor ninguna que la equivocada")

    def test_the_console_itself_keeps_its_reference(self) -> None:
        console_reference = PriceReference("ps2", "pricecharting", 120.0, confidence=0.8)
        card = valuate_radar_match(
            self.listing("Sony PlayStation 2 PS2 Slim Console Tested", price=60.0),
            self.verdict(), self.criteria(), [console_reference], "console",
        )
        self.assertEqual(card.to_dict()["benchmark"]["source"], "pricecharting")

    def test_peer_listings_survive_even_when_the_kind_does_not_match(self) -> None:
        # Las publicaciones pares no dependen de qué entidad se buscaba, así que
        # siguen sirviendo de vara para un accesorio.
        console_reference = PriceReference("dreamcast", "pricecharting", 141.0, confidence=0.8)
        peers = PriceReference("dreamcast", "peer-listings", 30.0, confidence=0.3)
        card = valuate_radar_match(
            self.listing("Sega Dreamcast VMU Memory Card", price=34.49),
            self.verdict(), self.criteria(), [console_reference, peers], "console",
        )
        self.assertEqual(card.to_dict()["benchmark"]["source"], "peer-listings")


if __name__ == "__main__":
    unittest.main()
