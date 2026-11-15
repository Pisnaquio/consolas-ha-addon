"""Collection Radar: match valuation, page scoring and listing detail lookups."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from radar.valuation import (
    bundle_adjusted_benchmark,
    completion_cost,
    lot_ceiling_benchmark,
    lot_peer_benchmark,
    market_for_source,
    peer_listing_benchmark,
    pick_benchmark,
    score_listing,
    sold_comparables_link,
)
from radar.classify import (
    assess_listing_evidence,
    classify_listing_item,
    detect_console_variant,
    detect_lot_size,
)
from radar.matching import (
    best_title_target,
    detect_completeness,
    detect_region,
    evaluate_match,
    normalize,
)
from radar.model import MarketplaceListing
from radar.platforms import platform_key
from radar.sources import registry as radar_registry

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import RADAR_PEERED_OBJECTIVES, _RADAR_LOCK
    from .database import connect_db
    from .radar_criteria import (
        radar_json_field,
        radar_query_criteria,
        radar_query_key,
        radar_source_capabilities,
        radar_title_target,
    )
    from .radar_preferences import radar_destination
    from .radar_references import (
        RADAR_BUNDLE_MAX_GAMES,
        radar_bundle_game_value,
        radar_console_weight_declaration,
        radar_console_weight_entry,
        radar_entity_references,
        radar_expected_item_kind,
        radar_game_price_reference,
        target_applies_to,
        usable_radar_references,
    )
    from .radar_runs import parse_iso_datetime
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import RADAR_PEERED_OBJECTIVES, _RADAR_LOCK
    from database import connect_db
    from radar_criteria import (
        radar_json_field,
        radar_query_criteria,
        radar_query_key,
        radar_source_capabilities,
        radar_title_target,
    )
    from radar_preferences import radar_destination
    from radar_references import (
        RADAR_BUNDLE_MAX_GAMES,
        radar_bundle_game_value,
        radar_console_weight_declaration,
        radar_console_weight_entry,
        radar_entity_references,
        radar_expected_item_kind,
        radar_game_price_reference,
        target_applies_to,
        usable_radar_references,
    )
    from radar_runs import parse_iso_datetime


def radar_objective_completeness(objective: dict[str, Any] | None) -> str:
    """Contra qué completitud se mide una publicación, o vacío si contra ninguna."""
    objective = objective or {}
    if objective.get("id") not in RADAR_PEERED_OBJECTIVES or objective.get("status") == "incomplete":
        return ""
    return "cib" if str(objective["id"]).endswith("-cib") else "loose"


def valuate_radar_match(
    listing: MarketplaceListing, verdict: Any, criteria: dict[str, Any], references: list[Any],
    entity_type: str = "", console_weight: Any = None, sold_query: str = "",
    bundle_game_value: dict[str, Any] | None = None,
    search_type: str = "", lot_peer_per_piece: list[float] | None = None,
    evidence: dict[str, Any] | None = None, details_expected: bool = False,
) -> Any:
    """Puntúa una coincidencia que ya pasó los filtros obligatorios.

    `evidence` es lo leído de la publicación entera (ver
    `radar_listing_evidence`); `details_expected`, si la fuente sabe darlo.
    """
    assessment = dict(getattr(verdict, "assessment", None) or {})
    completeness = str(criteria.get("completeness") or "any")
    # Qué es la pieza lo dice su propio título, no la entidad de la búsqueda que
    # la encontró: un juego capturado por una búsqueda de consola se costeaba
    # con 3 kg de courier encima.
    item_kind = classify_listing_item(listing.title)
    scoped = usable_radar_references(references, entity_type, item_kind)
    # Si la búsqueda acepta cualquier completitud, la vara es la de lo que la
    # publicación declara: una consola en caja medida contra el precio de una
    # suelta salía «cara» siempre, y el dueño quiere justamente ver las cajas.
    # Una caja sin manual se mide como CIB antes que como suelta: está mucho
    # más cerca de una que de la otra. Sin referencia para eso, se cae a la de
    # copia suelta y la card lo avisa.
    objective = assessment.get("objective") or {}
    loose_fallback = False
    if objective.get("id") in RADAR_PEERED_OBJECTIVES:
        # Cada objetivo canónico se compara con sus pares: CIB contra CIB y
        # loose contra loose. Sin una referencia del mismo objetivo no hay
        # veredicto de precio: medir un CIB contra una copia suelta exageraba
        # la ganga, y una suelta contra un CIB la hacía parecer regalada. Lo
        # que no llega a CIB (caja sin manual) no se compara con ninguno.
        completeness = radar_objective_completeness(objective)
        benchmark = pick_benchmark(scoped, completeness=completeness) if completeness else None
        completeness = completeness or "boxed"
    else:
        if completeness == "any":
            declared = detect_completeness(normalize(listing.searchable_text()))
            completeness = declared if declared in {"cib", "boxed", "sealed"} else "loose"
        benchmark = pick_benchmark(scoped, completeness=completeness)
        if benchmark is None and completeness == "boxed":
            benchmark = pick_benchmark(scoped, completeness="cib")
        if benchmark is None and completeness != "loose":
            benchmark = pick_benchmark(scoped, completeness="loose")
            loose_fallback = benchmark is not None
    # Un lote con cantidad declarada se mide por pieza contra el techo propio.
    per_piece_reference = (
        lot_ceiling_benchmark(
            declared_pieces=detect_lot_size(listing.title),
            per_piece_ceiling=criteria.get("maxPricePerPiece"),
            entity_id=listing.external_id,
            currency=str(criteria.get("currency") or "USD"),
        )
        if criteria.get("maxPricePerPiece") is not None and not item_kind.variable_price
        else None
    )
    lot = assessment.get("lot") or {}
    if search_type == "lot" or lot:
        # Un lote se juzga por pieza o no se juzga. Nunca contra la mediana de
        # los totales de otros lotes —un lote de 4 y uno de 22 no se comparan
        # enteros— y nunca sin una cantidad confiable: sin ella no hay división
        # honesta, y el lote queda sin referencia y aparte.
        pieces = lot.get("pieces") if lot.get("quantity") == "confirmed" else None
        benchmark = None
        if pieces and not item_kind.variable_price:
            if criteria.get("maxPricePerPiece") is not None:
                benchmark = lot_ceiling_benchmark(
                    declared_pieces=pieces,
                    per_piece_ceiling=criteria.get("maxPricePerPiece"),
                    entity_id=listing.external_id,
                    currency=str(criteria.get("currency") or "USD"),
                    filler=bool(lot.get("filler")),
                )
            elif lot_peer_per_piece:
                benchmark = lot_peer_benchmark(
                    lot_peer_per_piece, declared_pieces=pieces, entity_id=listing.external_id
                )
    elif per_piece_reference is not None:
        benchmark = per_piece_reference
    # Una consola que trae juegos se compara contra la consola más sus juegos.
    elif item_kind.kind == "console" and bundle_game_value:
        benchmark = bundle_adjusted_benchmark(
            benchmark,
            declared_games=detect_lot_size(listing.title),
            game_value=bundle_game_value.get("value"),
            catalog_sample=int(bundle_game_value.get("sample") or 0),
            max_games=RADAR_BUNDLE_MAX_GAMES,
        )
    card = score_listing(
        price_amount=listing.price_amount,
        shipping_amount=listing.shipping_amount,
        currency=listing.price_currency or str(criteria.get("currency") or "USD"),
        benchmark=benchmark,
        match_confidence=verdict.confidence,
        match_reasons=verdict.reasons,
        match_unverified=verdict.unverified,
        completeness=completeness,
        seller_known=bool(listing.seller_label),
        # Un lote no recibe costo importado inventado: su peso depende de
        # cuántas piezas trae, y eso el título no lo dice.
        entity_type=item_kind.weighable,
        # El peso declarado es el de esa consola: sólo vale si la pieza es una
        # consola. Un juego que cayó en la misma búsqueda pesa lo que pesa un
        # juego, no lo que pesa la consola que se estaba buscando.
        weight_kg=(
            radar_console_weight_entry(console_weight, detect_console_variant(listing.title))
            if item_kind.kind == "console"
            else None
        ),
        target_item_price=(
            criteria.get("targetItemPrice") if target_applies_to(item_kind, entity_type) else None
        ),
        assessment=assessment,
        # Sólo el detalle oficial leído hace menos de un día confirma que siga
        # comprable. Sin él, lo confirma el dueño al verificarla, y eso se lee
        # al mostrarla, no al puntuarla.
        availability_verified=(evidence or {}).get("available") is True,
        evidence=evidence,
        details_expected=details_expected,
        market=market_for_source(listing.source_id),
    )
    # Comparable de verdad: misma completitud y región que la referencia, y no
    # una estimación armada (consola más juegos supuestos). Lo que no lo es
    # puede ordenar el feed, pero no justificar un mail.
    if card.benchmark:
        listing_region = detect_region(normalize(listing.title).replace("made in japan", " "))
        reference_region = str(card.benchmark.get("region") or "")
        adjusted = "Ajustada por bundle" in str(card.benchmark.get("notes") or "")
        foreign = listing_region in {"pal", "ntsc-j"} and reference_region != listing_region
        card.benchmark["comparable"] = not (loose_fallback or foreign or adjusted)
    if loose_fallback:
        card.caveats.append(
            f"Declara {completeness.upper()}, pero la única referencia es de copia suelta: "
            "una copia con caja suele valer más, así que el veredicto exagera lo caro."
        )
    # El benchmark que más falta es el de ventas cerradas, y el radar no lo
    # puede traer. Lo que sí puede es dejarlo a un click.
    card.sold_comparables = sold_comparables_link(sold_query)
    return card


def score_radar_pages(
    config: AppConfig,
    row: sqlite3.Row,
    criteria: dict[str, Any],
    sources: list[str],
    pages: list[Any],
    now: str,
    detail_lookup: Callable[[MarketplaceListing], dict[str, Any] | None] | None = None,
) -> tuple[list[tuple[MarketplaceListing, Any, Any, dict[str, Any] | None]], int]:
    """Evalúa y valúa lo que trajeron las fuentes, sin tocar la base.

    Vive separado de `execute_radar_search` porque la previsualización tiene que
    contestar exactamente lo mismo que contestaría una corrida real: si el
    veredicto de una previa se calculara por otro camino, la previa dejaría de
    ser una previa.

    Cada resultado lleva, además, el objetivo de título que lo encontró (o
    `None`): en una búsqueda de varios títulos, una publicación se juzga con el
    techo y el precio de su juego, no con los de la búsqueda entera.
    """

    capabilities_by_source = {source_id: radar_source_capabilities(source_id) for source_id in sources}
    entity_type = str(row["entity_type"])
    entity_id = str(row["entity_id"])
    entity_console_id = str(row["entity_console_id"] if "entity_console_id" in row.keys() else "")
    expected_kind = radar_expected_item_kind(str(row["search_type"]), entity_type)
    console_weight = radar_console_weight_declaration(config, entity_id)
    references = radar_entity_references(config, entity_type, entity_id, entity_console_id)
    bundle_value = radar_bundle_game_value(config, entity_id) if entity_type == "console" else None
    # Sin catálogo de precios para esta entidad, las publicaciones de esta misma
    # corrida son la única referencia disponible. Entra al final de la lista: la
    # preferencia de fuentes ya la deja por debajo de cualquier curada.
    # La referencia de pares se arma sólo con lo que la búsqueda aceptó, y del
    # tipo que persigue. Antes promediaba todo lo que devolvía la fuente,
    # incluidas las publicaciones que la propia búsqueda había rechazado: una
    # búsqueda de consolas se comparaba contra los juegos sueltos que descartó.
    #
    # Con objetivos de título, los pares se agrupan por título: la mediana de
    # cinco Super Mario World dice algo; la de un Super Mario World con un
    # Chrono Trigger, no.
    #
    # Qué título es cada publicación lo dice su propio título, no la consulta
    # que la trajo: eBay contesta «Super Mario World snes» con cualquier otro
    # juego barato de SNES, y atribuírselo a la consulta lo etiquetaba como
    # Super Mario World. Lo que no nombra ninguno de los títulos perseguidos
    # no es de esta búsqueda.
    title_targets = [target for target in criteria.get("titleTargets") or [] if target.get("name")]
    lot_search = str(row["search_type"]) == "lot"
    matched: list[tuple[MarketplaceListing, Any, dict[str, Any] | None, dict[str, Any], str]] = []
    rejected = 0
    for page in pages:
        page_query = str(page.receipt.query if page.receipt else "")
        for listing in page.listings:
            if title_targets:
                target = best_title_target(listing.title, title_targets)
                if target is None:
                    rejected += 1
                    continue
                query = str(target.get("query") or page_query)
            else:
                target = radar_title_target(criteria, page_query)
                query = page_query
            listing_criteria = radar_query_criteria(
                criteria, query, completeness=detect_completeness(normalize(listing.searchable_text()))
            )
            verdict = evaluate_match(
                listing, listing_criteria, capabilities_by_source.get(listing.source_id, {}), expected_kind,
                rejects_accessories=lot_search,
                platform=str(row["platform"] or ""),
                search_type=str(row["search_type"] or ""),
            )
            if verdict.matched:
                matched.append((listing, verdict, target, listing_criteria, query))
            else:
                rejected += 1

    def group_key(target: dict[str, Any] | None, verdict: Any = None) -> tuple[str, str]:
        # Los pares son del mismo título y del mismo objetivo: un CIB contra
        # CIB, una copia suelta contra sueltas.
        objective = (getattr(verdict, "assessment", None) or {}).get("objective") if verdict else None
        return (radar_query_key(target.get("query")) if target else "", radar_objective_completeness(objective))

    # Los pares se comparan en la misma moneda que la publicación juzgada: una
    # consola a la que le falta el control entra con lo que costaría
    # completarla, y un lote entra por pieza —sólo si declara cuántas trae—.
    peer_prices: dict[tuple[str, str], list[float]] = {}
    lot_peer_per_piece: list[float] = []
    for listing, verdict, target, _, _ in matched:
        if not listing.price_amount:
            continue
        if lot_search:
            lot = verdict.assessment.get("lot") or {}
            if lot.get("quantity") == "confirmed" and lot.get("pieces"):
                lot_peer_per_piece.append(round(listing.price_amount / int(lot["pieces"]), 2))
            continue
        key = group_key(target, verdict)
        if key[1] and (not expected_kind or classify_listing_item(listing.title).kind == expected_kind):
            extra = (completion_cost(verdict.assessment) or {}).get("total") or 0.0
            peer_prices.setdefault(key, []).append(round(listing.price_amount + extra, 2))

    references_by_group: dict[tuple[str, str], list[Any]] = {}

    def group_references(target: dict[str, Any] | None, verdict: Any = None) -> list[Any]:
        key = group_key(target, verdict)
        if key in references_by_group:
            return references_by_group[key]
        base = references
        peer_entity = entity_id or str(row["id"])
        if target:
            game_id = str(target.get("gameId") or "")
            reference = radar_game_price_reference(config, entity_console_id, game_id) if game_id else None
            base = [reference] if reference else []
            peer_entity = game_id or str(target.get("query") or "")
        peers = peer_listing_benchmark(
            peer_prices.get(key, []),
            entity_id=peer_entity,
            completeness=key[1] or str(criteria.get("completeness") or "loose"),
            observed_at=now,
        )
        references_by_group[key] = [*base, peers] if peers is not None else list(base)
        return references_by_group[key]

    def valuate(listing: Any, verdict: Any, target: Any, page_criteria: Any, query: str,
                evidence: dict[str, Any] | None = None) -> Any:
        return valuate_radar_match(
            listing, verdict, page_criteria, group_references(target, verdict), expected_kind, console_weight,
            # Ventas cerradas de lo que encontró esta publicación: en una
            # búsqueda de varios títulos, ese título y no la consulta principal.
            sold_query=query or str(row["search_query"] or ""),
            bundle_game_value=bundle_value,
            search_type=str(row["search_type"] or ""),
            lot_peer_per_piece=lot_peer_per_piece,
            evidence=evidence,
            details_expected=bool(capabilities_by_source.get(listing.source_id, {}).get("itemDetail")),
        )

    cards = [valuate(*entry) for entry in matched]

    # Segunda pasada: las que el precio deja como candidatas se leen enteras.
    # El resumen de búsqueda sólo trae el título, y el título lo escribe el
    # vendedor para vender: lo que falta, lo que está roto y si se puede
    # devolver está en la descripción. Se piden pocas por corrida, las más
    # baratas primero; las demás quedan sin poder afirmar una ganga.
    if detail_lookup is not None:
        candidates = [
            index for index, card in enumerate(cards)
            if capabilities_by_source.get(matched[index][0].source_id, {}).get("itemDetail")
            and (card.price_band in {"ganga", "buena"} or (card.target or {}).get("meets"))
        ]
        candidates.sort(key=lambda index: cards[index].ratio if cards[index].ratio is not None else 9.0)
        for index in candidates[:RADAR_DETAIL_FETCH_LIMIT]:
            listing, verdict = matched[index][0], matched[index][1]
            detail = detail_lookup(listing)
            if not detail:
                continue
            evidence = radar_listing_evidence(listing, detail, str(row["platform"] or ""))
            cards[index] = valuate(*matched[index], evidence=evidence)

    scored = [
        (listing, verdict, card, radar_target_payload(target, entity_console_id))
        for (listing, verdict, target, _, _), card in zip(matched, cards)
    ]
    return scored, rejected


# Cuántas publicaciones por corrida de una búsqueda se leen enteras. La Browse
# API da 5000 llamadas por día; treinta búsquedas, tres veces por día, con doce
# candidatas cada una, quedan bien por debajo aun sin caché.
RADAR_DETAIL_FETCH_LIMIT = 12
# Cuánto vale lo leído de una publicación si el precio no cambió.
RADAR_DETAIL_TTL_HOURS = 24


def radar_listing_evidence(listing: Any, detail: dict[str, Any], platform: str = "") -> dict[str, Any]:
    """Lo leído de la publicación entera, en la forma que espera la valuación."""
    evidence = assess_listing_evidence(
        listing.title,
        str(detail.get("description") or ""),
        " ".join(part for part in (str(detail.get("conditionNote") or ""), listing.condition_label or "") if part),
        platform_key(platform) or platform,
    )
    evidence["read"] = True
    evidence["returnsAccepted"] = detail.get("returnsAccepted")
    evidence["imageCount"] = detail.get("imageCount")
    evidence["taxes"] = detail.get("taxes")
    # Leída en las últimas `RADAR_DETAIL_TTL_HOURS` horas: si eBay dijo que se
    # puede comprar, la disponibilidad está verificada recientemente.
    evidence["available"] = detail.get("available")
    return evidence


def radar_listing_detail_lookup(
    config: AppConfig, *, store: bool = True
) -> Callable[[MarketplaceListing], dict[str, Any] | None]:
    """Lee el detalle de una publicación, de la caché o de la fuente.

    Una previsualización no escribe nada: usa lo que ya hay en caché y pide lo
    que falta sin guardarlo.
    """

    destination = radar_destination(config)
    enabled = getattr(config, "radar_item_details", False) is True

    def lookup(listing: MarketplaceListing) -> dict[str, Any] | None:
        if not enabled:
            return None
        with _RADAR_LOCK, connect_db(config) as conn:
            cached = conn.execute(
                "SELECT * FROM radar_listing_details WHERE source_id = ? AND external_id = ?",
                (listing.source_id, listing.external_id),
            ).fetchone()
        if cached is not None:
            fetched = parse_iso_datetime(cached["fetched_at"])
            fresh = fetched is not None and datetime.now(timezone.utc) - fetched < timedelta(hours=RADAR_DETAIL_TTL_HOURS)
            if fresh and cached["price_amount"] == listing.price_amount:
                return radar_json_field(cached["detail_json"], {}) or None
        spec = radar_registry.get_source(listing.source_id)
        try:
            adapter = spec.load() if spec else None
            detail = adapter.item_detail(config, listing.external_id, destination) if adapter else None
        except Exception as error:  # una falla de detalle nunca tumba la corrida
            print(f"[consolas] Collection Radar: sin detalle de {listing.external_id}: {error}", flush=True)
            return None
        if not detail:
            return None
        if store:
            with _RADAR_LOCK, connect_db(config) as conn:
                conn.execute(
                    """INSERT INTO radar_listing_details (source_id, external_id, price_amount, fetched_at, detail_json)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(source_id, external_id) DO UPDATE SET
                         price_amount = excluded.price_amount, fetched_at = excluded.fetched_at,
                         detail_json = excluded.detail_json""",
                    (listing.source_id, listing.external_id, listing.price_amount, utc_now(),
                     json.dumps(detail, ensure_ascii=False, separators=(",", ":"))),
                )
        return detail

    return lookup


def radar_target_payload(target: dict[str, Any] | None, console_id: str) -> dict[str, Any] | None:
    """El objetivo tal como se guarda junto a la coincidencia y viaja a la UI."""
    if not target:
        return None
    return {
        "query": str(target.get("query") or ""),
        "gameId": str(target.get("gameId") or ""),
        "consoleId": console_id,
        "name": str(target.get("name") or ""),
        "maxItemPrice": target.get("maxItemPrice"),
        "maxBoxedPrice": target.get("maxBoxedPrice"),
    }
