"""Collection Radar: preview/run of a search, manual listings and verification."""

from __future__ import annotations

import hashlib
import json
from http import HTTPStatus
from typing import Any

from radar.matching import evaluate_match
from radar.model import MarketplaceListing
from radar.sources import registry as radar_registry

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        RADAR_DEFAULT_RESULT_LIMIT,
        RADAR_LISTING_KIND_LABELS,
        RADAR_MAX_RESULT_LIMIT,
        _RADAR_LOCK,
    )
    from .database import connect_db
    from .errors import ApiError
    from .radar_criteria import (
        executable_radar_sources,
        normalize_radar_amount,
        normalize_radar_criteria,
        normalize_radar_id,
        normalize_radar_sources,
        normalize_radar_text,
        radar_json_field,
        radar_search_queries,
        radar_source_capabilities,
    )
    from .radar_listings import collect_radar_source_pages, upsert_radar_listing
    from .radar_references import (
        radar_console_weight_declaration,
        radar_entity_references,
        radar_expected_item_kind,
    )
    from .radar_results import load_radar_search
    from .radar_runs import radar_search_lock, record_radar_receipts
    from .radar_scoring import radar_listing_detail_lookup, score_radar_pages, valuate_radar_match
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import (
        RADAR_DEFAULT_RESULT_LIMIT,
        RADAR_LISTING_KIND_LABELS,
        RADAR_MAX_RESULT_LIMIT,
        _RADAR_LOCK,
    )
    from database import connect_db
    from errors import ApiError
    from radar_criteria import (
        executable_radar_sources,
        normalize_radar_amount,
        normalize_radar_criteria,
        normalize_radar_id,
        normalize_radar_sources,
        normalize_radar_text,
        radar_json_field,
        radar_search_queries,
        radar_source_capabilities,
    )
    from radar_listings import collect_radar_source_pages, upsert_radar_listing
    from radar_references import (
        radar_console_weight_declaration,
        radar_entity_references,
        radar_expected_item_kind,
    )
    from radar_results import load_radar_search
    from radar_runs import radar_search_lock, record_radar_receipts
    from radar_scoring import radar_listing_detail_lookup, score_radar_pages, valuate_radar_match


def preview_radar_search(config: AppConfig, search_id: Any) -> dict[str, Any]:
    """Corre una búsqueda sin guardar nada y sin exigir que esté activa.

    Contesta la única pregunta que no se puede contestar leyendo los criterios:
    si esto se activa, ¿qué trae? Una búsqueda en borrador o pausada se puede
    mirar antes de decidir activarla, y mirarla no le cambia el inventario, el
    historial, la última corrida ni el presupuesto. No escribe una sola fila:
    por eso también sirve para probar un criterio nuevo sobre una búsqueda viva
    sin ensuciar lo que ya encontró.
    """

    target_id = normalize_radar_id(search_id)
    with radar_search_lock(target_id):
        with _RADAR_LOCK, connect_db(config) as conn:
            row = load_radar_search(conn, target_id)
        criteria = normalize_radar_criteria(radar_json_field(row["criteria_json"], None))
        sources = normalize_radar_sources(radar_json_field(row["sources_json"], []))
        if not executable_radar_sources(sources):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Ninguna de las fuentes de esta búsqueda puede ejecutarse todavía.",
                {"id": target_id, "sources": sources},
            )
        now = utc_now()
        queries = radar_search_queries(row["search_query"], criteria)
        pages, receipts = collect_radar_source_pages(config, queries, criteria, sources)
        failures = [receipt for receipt in receipts if receipt["status"] == "failed"]
        if failures and len(failures) == len(receipts):
            message = " · ".join(
                error for receipt in failures for error in (receipt.get("errors") or ["Falla de la fuente"])
            )
            raise ApiError(HTTPStatus.BAD_GATEWAY, message, {"id": target_id, "receipts": receipts})
        scored, rejected = score_radar_pages(
            config, row, criteria, sources, pages, now,
            detail_lookup=radar_listing_detail_lookup(config, store=False),
        )

    limit = max(1, min(int(criteria.get("resultLimit") or RADAR_DEFAULT_RESULT_LIMIT), RADAR_MAX_RESULT_LIMIT))
    ranked = sorted(
        scored,
        key=lambda entry: (entry[2].score if entry[2].score is not None else -1, entry[1].confidence),
        reverse=True,
    )
    return {
        "ok": True,
        "id": target_id,
        "status": str(row["status"]),
        "preview": True,
        "persisted": False,
        "queries": queries,
        "matched": len(scored),
        "rejected": rejected,
        "checkedAt": now,
        "receipts": receipts,
        "results": [
            radar_preview_result(listing, verdict, card, target)
            for listing, verdict, card, target in ranked[:limit]
        ],
    }


def radar_preview_result(
    listing: MarketplaceListing, verdict: Any, card: Any, target: dict[str, Any] | None = None
) -> dict[str, Any]:
    """La forma mínima de un resultado que nunca se guardó.

    No lleva `id` de listing ni fechas de inventario a propósito: nada de esto
    existe en la base, y darle la forma exacta de un resultado persistido
    invitaría a la UI a ofrecer acciones (descartar, registrar compra) sobre
    algo que no está guardado.
    """

    return {
        "sourceId": listing.source_id,
        "externalId": listing.external_id,
        "title": listing.title,
        "listingUrl": listing.listing_url,
        "imageUrl": listing.image_url,
        "listingKind": listing.listing_kind,
        "listingType": RADAR_LISTING_KIND_LABELS.get(listing.listing_kind, ""),
        "priceAmount": listing.price_amount,
        "priceCurrency": listing.price_currency,
        "priceLabel": listing.price_label,
        "shippingLabel": listing.shipping_label,
        "totalAmount": listing.total_amount,
        "conditionLabel": listing.condition_label,
        "locationLabel": listing.location_label,
        "sellerLabel": listing.seller_label,
        "closesAt": listing.closes_at,
        "confidence": verdict.confidence,
        "score": card.score,
        "band": card.band,
        "valuation": card.to_dict(),
        "reasons": list(verdict.reasons),
        "unverified": list(verdict.unverified),
        "matchedTerms": list(verdict.matched_terms),
        "titleTarget": target,
    }


def run_radar_search(config: AppConfig, search_id: Any, run_id: str = "") -> dict[str, Any]:
    """Ejecuta una búsqueda activa: consulta, evalúa y guarda sus coincidencias.

    El lock por búsqueda cubre también la llamada de red: el scheduler y un
    “Buscar ahora” simultáneos no pueden escanear dos veces lo mismo.
    """
    target_id = normalize_radar_id(search_id)
    with radar_search_lock(target_id):
        return execute_radar_search(config, target_id, run_id)


def execute_radar_search(config: AppConfig, target_id: str, run_id: str = "") -> dict[str, Any]:
    with _RADAR_LOCK, connect_db(config) as conn:
        row = load_radar_search(conn, target_id)
    status = str(row["status"])
    if status != "active":
        raise ApiError(
            HTTPStatus.CONFLICT,
            "Sólo una búsqueda activa puede ejecutarse. Activala o reanudala primero.",
            {"id": target_id, "status": status},
        )
    criteria = normalize_radar_criteria(radar_json_field(row["criteria_json"], None))
    sources = normalize_radar_sources(radar_json_field(row["sources_json"], []))
    if not executable_radar_sources(sources):
        raise ApiError(
            HTTPStatus.CONFLICT,
            "Ninguna de las fuentes de esta búsqueda puede ejecutarse todavía.",
            {"id": target_id, "sources": sources},
        )

    now = utc_now()
    queries = radar_search_queries(row["search_query"], criteria)
    pages, receipts = collect_radar_source_pages(config, queries, criteria, sources)
    failures = [receipt for receipt in receipts if receipt["status"] == "failed"]
    authoritative = bool(receipts) and all(receipt["authoritative"] for receipt in receipts)

    # Ninguna fuente respondió: se conserva el inventario previo y se explica la falla.
    # Una respuesta fallida nunca prueba que una publicación dejó de existir.
    if failures and len(failures) == len(receipts):
        message = " · ".join(
            error for receipt in failures for error in (receipt.get("errors") or ["Falla de la fuente"])
        )
        with _RADAR_LOCK, connect_db(config) as conn:
            conn.execute(
                "UPDATE radar_searches SET last_checked_at = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (now, message, now, target_id),
            )
        record_radar_receipts(config, run_id, target_id, receipts, 0, 0)
        raise ApiError(HTTPStatus.BAD_GATEWAY, message, {"id": target_id, "receipts": receipts})

    scored, rejected = score_radar_pages(
        config, row, criteria, sources, pages, now, detail_lookup=radar_listing_detail_lookup(config)
    )
    matched_ids: list[str] = []

    with _RADAR_LOCK, connect_db(config) as conn:
        for listing, verdict, card, title_target in scored:
            listing_id = upsert_radar_listing(conn, listing, now)
            conn.execute(
                """
                INSERT INTO radar_search_matches (
                  search_id, listing_id, confidence, reasons_json, blockers_json, unverified_json,
                  matched_terms_json, score, band, valuation_json, target_json, is_active,
                  first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(search_id, listing_id) DO UPDATE SET
                  confidence=excluded.confidence, reasons_json=excluded.reasons_json,
                  blockers_json=excluded.blockers_json, unverified_json=excluded.unverified_json,
                  matched_terms_json=excluded.matched_terms_json, score=excluded.score,
                  band=excluded.band, valuation_json=excluded.valuation_json,
                  target_json=excluded.target_json, is_active=1,
                  last_seen_at=excluded.last_seen_at
                """,
                (
                    target_id, listing_id, verdict.confidence,
                    json.dumps(verdict.reasons, ensure_ascii=False),
                    json.dumps(verdict.blockers, ensure_ascii=False),
                    json.dumps(verdict.unverified, ensure_ascii=False),
                    json.dumps(verdict.matched_terms, ensure_ascii=False),
                    card.score, card.band,
                    json.dumps(card.to_dict(), ensure_ascii=False, separators=(",", ":")),
                    json.dumps(title_target, ensure_ascii=False, separators=(",", ":")) if title_target else "",
                    now, now,
                ),
            )
            matched_ids.append(listing_id)

        # Sólo una cobertura completa autoriza retirar coincidencias previas.
        if authoritative:
            placeholders = ",".join("?" for _ in matched_ids)
            parameters: list[Any] = [target_id]
            query = "UPDATE radar_search_matches SET is_active = 0 WHERE search_id = ? AND is_active = 1"
            if matched_ids:
                query += f" AND listing_id NOT IN ({placeholders})"
                parameters.extend(matched_ids)
            conn.execute(query, parameters)

        last_error = ""
        if failures:
            last_error = " · ".join(
                error for receipt in failures for error in (receipt.get("errors") or ["Falla de la fuente"])
            )
        conn.execute(
            "UPDATE radar_searches SET last_checked_at = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (now, last_error, now, target_id),
        )

    record_radar_receipts(config, run_id, target_id, receipts, len(matched_ids), rejected)
    return {
        "ok": True,
        "id": target_id,
        "results": len(matched_ids),
        "rejected": rejected,
        "checkedAt": now,
        "authoritative": authoritative,
        "receipts": receipts,
        "runId": run_id,
    }


# --- Fuentes de verificación asistida — ShopGoodwill (PRD §12.3, slice 10) --
#
# "No permitido en el diseño: crawler o scraper desatendido." ShopGoodwill se
# integra por Personal Shopper/alertas guardadas: el owner ya vio la
# publicación (por email o abriéndola) y trae los datos a mano. Se evalúa
# contra la misma búsqueda y las mismas reglas que un resultado automático —
# la única diferencia es de dónde vino el dato, no cómo se juzga. Nace
# `requiresVerification` hasta que alguien confirme Add to Cart/Buy Now en la
# publicación real; un snippet nunca alcanza (PRD §12.2, aplicado acá también).


def normalize_radar_manual_source(value: Any) -> str:
    source_id = str(value or "shopgoodwill").strip().lower()
    if radar_registry.get_source(source_id) is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"Unknown source: {source_id}")
    if not radar_source_capabilities(source_id).get("requiresAssistedVerification"):
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "La carga manual es sólo para fuentes de verificación asistida.",
        )
    return source_id


def create_manual_radar_listing(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    search_id = normalize_radar_id(payload.get("searchId"))
    source_id = normalize_radar_manual_source(payload.get("sourceId"))
    title = normalize_radar_text(payload.get("title"), "title", 300, required=True)
    listing_url = normalize_radar_text(payload.get("listingUrl"), "listingUrl", 800, required=True)
    if not listing_url.startswith(("http://", "https://")):
        raise ApiError(HTTPStatus.BAD_REQUEST, "listingUrl must be an http(s) URL")
    price_amount = normalize_radar_amount(payload.get("priceAmount"), "priceAmount")
    if price_amount is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, "priceAmount is required")
    shipping_amount = normalize_radar_amount(payload.get("shippingAmount"), "shippingAmount")
    currency = normalize_radar_text(payload.get("currency"), "currency", 8, required=False).upper() or "USD"
    condition_label = normalize_radar_text(payload.get("conditionLabel"), "conditionLabel", 200, required=False)

    with _RADAR_LOCK, connect_db(config) as conn:
        row = load_radar_search(conn, search_id)
    if str(row["status"]) not in ("active", "paused", "draft"):
        raise ApiError(HTTPStatus.CONFLICT, "Esta búsqueda no admite nuevas coincidencias en su estado actual")
    criteria = normalize_radar_criteria(radar_json_field(row["criteria_json"], None))

    listing = MarketplaceListing(
        source_id=source_id,
        # Estable por URL: cargar el mismo link dos veces actualiza, no duplica.
        external_id=f"manual-{hashlib.sha256(listing_url.encode('utf-8')).hexdigest()[:24]}",
        title=title,
        listing_url=listing_url,
        price_amount=price_amount,
        price_currency=currency,
        shipping_amount=shipping_amount,
        shipping_currency=currency if shipping_amount is not None else "",
        condition_label=condition_label,
        price_label=f"{currency} {price_amount:.2f}",
        shipping_label=f"{currency} {shipping_amount:.2f}" if shipping_amount is not None else "",
        availability="unknown",
    )

    capabilities = radar_source_capabilities(source_id)
    verdict = evaluate_match(
        listing, criteria, capabilities, rejects_accessories=str(row["search_type"]) == "lot",
        platform=str(row["platform"] or ""), search_type=str(row["search_type"] or ""),
    )
    if not verdict.matched:
        raise ApiError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "No cumple los criterios de esta búsqueda todavía.",
            {"blockers": verdict.blockers},
        )

    references = radar_entity_references(
        config, str(row["entity_type"]), str(row["entity_id"]),
        str(row["entity_console_id"] if "entity_console_id" in row.keys() else ""),
    )
    card = valuate_radar_match(
        listing, verdict, criteria, references,
        radar_expected_item_kind(str(row["search_type"]), str(row["entity_type"])),
        radar_console_weight_declaration(config, str(row["entity_id"])),
        sold_query=str(row["search_query"] or ""),
        search_type=str(row["search_type"] or ""),
    )
    now = utc_now()

    with _RADAR_LOCK, connect_db(config) as conn:
        listing_id = upsert_radar_listing(conn, listing, now)
        conn.execute("UPDATE radar_listings SET requires_verification = 1 WHERE id = ?", (listing_id,))
        conn.execute(
            """INSERT INTO radar_search_matches (
                 search_id, listing_id, confidence, reasons_json, blockers_json, unverified_json,
                 matched_terms_json, score, band, valuation_json, is_active, first_seen_at, last_seen_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(search_id, listing_id) DO UPDATE SET
                 confidence=excluded.confidence, reasons_json=excluded.reasons_json,
                 blockers_json=excluded.blockers_json, unverified_json=excluded.unverified_json,
                 matched_terms_json=excluded.matched_terms_json, score=excluded.score,
                 band=excluded.band, valuation_json=excluded.valuation_json, is_active=1,
                 last_seen_at=excluded.last_seen_at""",
            (
                search_id, listing_id, verdict.confidence,
                json.dumps(verdict.reasons, ensure_ascii=False),
                json.dumps(verdict.blockers, ensure_ascii=False),
                json.dumps(verdict.unverified, ensure_ascii=False),
                json.dumps(verdict.matched_terms, ensure_ascii=False),
                card.score, card.band,
                json.dumps(card.to_dict(), ensure_ascii=False, separators=(",", ":")),
                now, now,
            ),
        )

    return {"ok": True, "listingId": listing_id, "searchId": search_id, "requiresVerification": True}


def verify_radar_listing_contents(config: AppConfig, listing_id: Any) -> dict[str, Any]:
    """El dueño miró las fotos y la caja y el manual (o el contenido) están.

    Sin esto, un CIB es lo que declara el vendedor y queda en «Requiere
    revisión». No escribe nada de la colección: es evidencia del radar sobre
    esa publicación, como la verificación de disponibilidad.
    """

    target_id = normalize_radar_id(listing_id)
    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        cursor = conn.execute(
            "UPDATE radar_listings SET contents_verified_at = ? WHERE id = ?", (now, target_id)
        )
        if cursor.rowcount != 1:
            raise ApiError(HTTPStatus.NOT_FOUND, "Listing not found")
    return {"ok": True, "listingId": target_id, "contentsVerifiedAt": now}


def verify_radar_listing(config: AppConfig, listing_id: Any) -> dict[str, Any]:
    """Confirma que alguien abrió la publicación real y probó Add to Cart/Buy Now.

    Un snippet de buscador no alcanza (PRD §12.2) — esto es lo que separa
    "aparece en una alerta" de "está disponible de verdad".
    """

    target_id = normalize_radar_id(listing_id)
    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        cursor = conn.execute(
            "UPDATE radar_listings SET requires_verification = 0, verified_at = ? WHERE id = ?",
            (now, target_id),
        )
        if cursor.rowcount != 1:
            raise ApiError(HTTPStatus.NOT_FOUND, "Listing not found")
    return {"ok": True, "listingId": target_id, "verifiedAt": now}
