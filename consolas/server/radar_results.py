"""Collection Radar: search/result rows, verification state and listing views."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from typing import Any

from radar.valuation import effective_band
from radar.classify import classify_listing_item
from radar.matching import detect_completeness, detect_region, normalize
from radar.platforms import platform_key, platform_payload
from radar.taxonomy import assign_objective
from radar.sources import registry as radar_registry

try:
    from .config import AppConfig
    from .constants import (
        RADAR_DEFAULT_SOURCE,
        RADAR_INVENTORY_SCAN_LIMIT,
        RADAR_LISTING_KIND_LABELS,
        RADAR_MAX_COPIES_PER_TITLE,
        RADAR_MAX_RESULT_LIMIT,
        RADAR_PEERED_OBJECTIVES,
        RADAR_SEARCHES_VERSION,
        RADAR_SLOT_LABELS,
        RADAR_VERIFICATION_TTL_HOURS,
        _RADAR_LOCK,
    )
    from .database import connect_db
    from .errors import ApiError
    from .radar_criteria import (
        executable_radar_sources,
        normalize_radar_criteria,
        radar_json_field,
        radar_query_key,
        radar_source_capabilities,
        radar_source_label,
        radar_sources_payload,
    )
    from .radar_decisions import (
        group_radar_rows_by_content,
        radar_cluster_matches_using,
        radar_cluster_representative,
    )
    from .radar_runs import parse_iso_datetime, radar_search_slots
except ImportError:  # Direct execution: `python3 server/app.py`.
    from config import AppConfig
    from constants import (
        RADAR_DEFAULT_SOURCE,
        RADAR_INVENTORY_SCAN_LIMIT,
        RADAR_LISTING_KIND_LABELS,
        RADAR_MAX_COPIES_PER_TITLE,
        RADAR_MAX_RESULT_LIMIT,
        RADAR_PEERED_OBJECTIVES,
        RADAR_SEARCHES_VERSION,
        RADAR_SLOT_LABELS,
        RADAR_VERIFICATION_TTL_HOURS,
        _RADAR_LOCK,
    )
    from database import connect_db
    from errors import ApiError
    from radar_criteria import (
        executable_radar_sources,
        normalize_radar_criteria,
        radar_json_field,
        radar_query_key,
        radar_source_capabilities,
        radar_source_label,
        radar_sources_payload,
    )
    from radar_decisions import (
        group_radar_rows_by_content,
        radar_cluster_matches_using,
        radar_cluster_representative,
    )
    from radar_runs import parse_iso_datetime, radar_search_slots


def radar_search_row(
    row: sqlite3.Row, results: list[dict[str, Any]] | None = None, meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    criteria = normalize_radar_criteria(radar_json_field(row["criteria_json"], None))
    platform = platform_payload(str(row["platform"] or ""))
    meta = meta or {}
    raw_sources = radar_json_field(row["sources_json"], [RADAR_DEFAULT_SOURCE])
    sources = [
        str(item) for item in raw_sources if radar_registry.get_source(str(item)) is not None
    ] or [RADAR_DEFAULT_SOURCE]
    status = str(row["status"])
    runnable_sources = executable_radar_sources(sources)
    return {
        "id": row["id"],
        "name": row["name"],
        # Compatibilidad con la superficie previa de Chasing Games.
        "title": row["name"],
        "searchType": row["search_type"],
        "status": status,
        "origin": row["origin"],
        "priority": row["priority"],
        "platform": row["platform"],
        # Para agrupar en pantalla: «N64» y «Nintendo 64» son la misma.
        "platformKey": platform["key"],
        "platformLabel": platform["label"],
        "platformOrder": platform["order"],
        "entityType": row["entity_type"],
        "entityId": row["entity_id"],
        "entityConsoleId": (row["entity_console_id"] if "entity_console_id" in row.keys() else ""),
        "searchQuery": row["search_query"],
        "criteria": criteria,
        "sources": sources,
        "executableSources": runnable_sources,
        "notes": row["notes"],
        "slots": radar_search_slots(row),
        "slotLabels": [RADAR_SLOT_LABELS[key] for key in radar_search_slots(row)],
        "enabled": status == "active",
        "canRun": status == "active" and bool(runnable_sources),
        # Previsualizar no depende del estado: existe justamente para mirar
        # una búsqueda que todavía no corre. Sólo necesita una fuente viva.
        "canPreview": bool(runnable_sources),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "lastCheckedAt": row["last_checked_at"],
        "lastError": row["last_error"],
        "archivedAt": row["archived_at"],
        "results": results or [],
        "resultCount": len(results or []),
        "activeMatches": meta.get("activeMatches", len(results or [])),
        "hiddenCopies": meta.get("hiddenCopies", 0),
        "unconfirmedCount": meta.get("unconfirmedCount", 0),
        "targetCoverage": meta.get("targetCoverage"),
    }


def radar_verification_state(row: sqlite3.Row, now: datetime | None = None) -> dict[str, Any]:
    """Si la publicación está confirmada como comprable hoy, y desde cuándo.

    Ninguna fuente automática confirma disponibilidad: eBay Browse devuelve lo
    que encontró, no si sigue teniendo Buy It Now (`availabilityCheck` está en
    `false`). Hasta ahora eso quedaba en `availability: unknown` y la card no lo
    decía. Ahora el contrato es explícito:

    - `pending`: se encontró en la última corrida, pero nadie confirmó que se
      pueda comprar;
    - `verified`: el dueño la abrió y confirmó Buy It Now o Add to Cart hace
      menos de `RADAR_VERIFICATION_TTL_HOURS`, y el precio no cambió después;
    - `expired`: se verificó, pero pasó el plazo o cambió el precio;
    - `available`: la propia fuente lo confirma (ninguna hoy);
    - `ended`: la fuente dice que terminó.
    """

    keys = row.keys()
    now = now or datetime.now(timezone.utc)
    source_id = str(row["source_id"] or "")
    capabilities = radar_source_capabilities(source_id) if source_id else {}
    availability = str((row["availability"] if "availability" in keys else "") or "unknown")
    flagged = bool(row["requires_verification"]) if "requires_verification" in keys else False
    verified_at = parse_iso_datetime(row["verified_at"] if "verified_at" in keys else None)
    price_changed = parse_iso_datetime(row["price_changed_at"] if "price_changed_at" in keys else None)
    expires_at = verified_at + timedelta(hours=RADAR_VERIFICATION_TTL_HOURS) if verified_at else None
    contents_verified_at = parse_iso_datetime(row["contents_verified_at"] if "contents_verified_at" in keys else None)
    reason = ""
    if availability == "ended":
        state = "ended"
    elif verified_at and expires_at and expires_at > now and not (price_changed and price_changed > verified_at):
        state = "verified"
    elif verified_at:
        state = "expired"
        reason = "price-changed" if price_changed and price_changed > verified_at else "stale"
    elif capabilities.get("availabilityCheck") and availability == "available" and not flagged:
        state = "available"
    else:
        state = "pending"
    stamp = lambda value: value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None  # noqa: E731
    return {
        "state": state,
        "reason": reason,
        "verifiedAt": stamp(verified_at),
        "expiresAt": stamp(expires_at),
        "ttlHours": RADAR_VERIFICATION_TTL_HOURS,
        "availability": availability,
        "sourceChecksAvailability": bool(capabilities.get("availabilityCheck")),
        "lastSeenAt": row["last_seen_at"] if "last_seen_at" in keys else None,
        "requiresVerification": state not in {"verified", "available"},
        # Caja y manual comprobados por el dueño: no vence, el contenido no cambia.
        "contentsVerifiedAt": stamp(contents_verified_at),
    }


def radar_result_objective(row: sqlite3.Row, valuation: dict[str, Any]) -> dict[str, Any]:
    """El objetivo canónico de una coincidencia.

    Lo guarda la corrida; una coincidencia puntuada antes de que existiera la
    taxonomía lo recibe acá, leído del título con la misma regla, para que la
    pantalla la ubique igual. Su banda se actualiza en la próxima corrida.
    """

    stored = (valuation.get("assessment") or {}).get("objective") or valuation.get("objective")
    if stored:
        return dict(stored)
    keys = row.keys()
    item = classify_listing_item(str(row["title"] or ""))
    return assign_objective(
        str(row["title"] or ""),
        search_type=str(row["match_search_type"] or "") if "match_search_type" in keys else "",
        item_kind=item.kind,
        item_confident=item.confident,
        platform=platform_key(str(row["match_platform"] or "")) if "match_platform" in keys else "",
    ).to_dict()


def radar_result_row(row: sqlite3.Row) -> dict[str, Any]:
    """Una coincidencia vista desde su búsqueda: la publicación más sus razones."""
    reasons = radar_json_field(row["reasons_json"], [])
    unverified = radar_json_field(row["unverified_json"], [])
    matched_terms = radar_json_field(row["matched_terms_json"], [])
    valuation = radar_json_field(row["valuation_json"], {})
    verification = radar_verification_state(row)
    return {
        "id": row["listing_id"],
        "listingId": row["listing_id"],
        "sourceId": row["source_id"],
        "sourceLabel": radar_source_label(str(row["source_id"])),
        "externalId": row["external_id"],
        "title": row["title"],
        "priceLabel": row["price_label"],
        "priceAmount": row["price_amount"],
        "priceCurrency": row["price_currency"],
        "shippingAmount": row["shipping_amount"],
        "totalAmount": row["total_amount"],
        "conditionLabel": row["condition_label"],
        "shippingLabel": row["shipping_label"],
        "locationLabel": row["location_label"],
        "sellerLabel": row["seller_label"],
        "listingKind": row["listing_kind"],
        "listingType": RADAR_LISTING_KIND_LABELS.get(str(row["listing_kind"]), ""),
        "listingUrl": row["listing_url"],
        "imageUrl": row["image_url"],
        "availability": row["availability"],
        "closesAt": row["closes_at"],
        "confidence": row["confidence"],
        "score": row["score"],
        "band": effective_band(
            str(row["band"] or ""),
            valuation,
            verification["state"] in {"verified", "available"},
            bool(verification["contentsVerifiedAt"]),
        ),
        "valuation": valuation,
        "section": str(valuation.get("section") or "primary"),
        "objective": radar_result_objective(row, valuation),
        "searchId": row["search_id"] if "search_id" in row.keys() else None,
        "searchName": row["match_search_name"] if "match_search_name" in row.keys() else "",
        "titleTarget": radar_json_field(row["target_json"], None) if "target_json" in row.keys() else None,
        "reasons": reasons,
        "unverified": unverified,
        "matchedTerms": matched_terms,
        "verification": verification,
        "requiresVerification": verification["requiresVerification"],
        "verifiedAt": row["verified_at"] if "verified_at" in row.keys() else None,
        "firstSeenAt": row["first_seen_at"],
        "lastSeenAt": row["last_seen_at"],
    }


def load_radar_search(conn: sqlite3.Connection, search_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM radar_searches WHERE id = ? AND deleted_at IS NULL", (search_id,)
    ).fetchone()
    if row is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "Radar search not found")
    return row


RADAR_MATCH_SELECT = """
    SELECT l.*, m.search_id, m.listing_id, m.confidence, m.reasons_json, m.blockers_json,
           m.unverified_json, m.matched_terms_json, m.score, m.band, m.valuation_json, m.target_json,
           m.first_seen_at AS match_first_seen_at, m.last_seen_at AS match_last_seen_at,
           s.search_type AS match_search_type, s.platform AS match_platform, s.name AS match_search_name
      FROM radar_search_matches m
      JOIN radar_listings l ON l.id = m.listing_id
      JOIN radar_searches s ON s.id = m.search_id
"""


def radar_search_results(conn: sqlite3.Connection, search_id: str, limit: int) -> list[dict[str, Any]]:
    return radar_search_inventory(conn, search_id, limit)[0]


# Lo que distingue dos copias del mismo título lo suficiente como para mostrar
# las dos: suelta frente a caja, otra región, otra edición.
_RADAR_EDITION_MARKERS = (
    "greatest hits", "players choice", "player s choice", "platinum", "black label",
    "collector", "limited edition", "special edition", "nintendo selects",
)


def radar_result_variant(result: dict[str, Any]) -> tuple[str, str, str]:
    """`(suelta o en caja, región, edición)`.

    Si es suelta o en caja lo dice su objetivo canónico, que ya sabe que en un
    disco «with case» es la caja; sólo sin objetivo se relee el título.
    """
    text = normalize(str(result.get("title") or ""))
    objective = str((result.get("objective") or {}).get("id") or "")
    if objective in RADAR_PEERED_OBJECTIVES:
        boxed = objective.endswith("-cib")
    else:
        boxed = detect_completeness(text) in {"boxed", "cib", "sealed"}
    edition = next((marker for marker in _RADAR_EDITION_MARKERS if f" {marker} " in f" {text} "), "")
    return ("boxed" if boxed else "loose", detect_region(text), edition)


def radar_result_title_key(result: dict[str, Any]) -> str:
    target = result.get("titleTarget") or {}
    return radar_query_key(target.get("gameId") or target.get("query") or "")


def diversify_radar_results(results: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], int]:
    """Primero la mejor copia de cada título; después, segundas copias distintas.

    Una búsqueda de deseados con catorce Tetris entre veinticinco resultados no
    está cubriendo la lista: está repitiendo el título más común. Antes de
    cortar en el límite se toma la mejor copia de cada título perseguido (en el
    orden del score), después una segunda sólo si aporta algo que la primera no
    —caja frente a suelta, otra región u otra edición— y nada más. Lo que no
    entra se cuenta, no se esconde en silencio.

    Si la mejor copia es suelta, la segunda es la mejor en caja que haya,
    aunque otra suelta de otra región puntúe más: en un juego de disco el CIB es
    lo que se busca (perfil §2), y dos sueltas lo tapaban. Medido en la
    búsqueda CIB de PS3 del 2026-10-12. Sin copia en caja, vale la primera
    distinta.

    Los lotes con cantidad sin confirmar van al final, después de los que
    cumplen: la pantalla los muestra aparte.
    """

    primary = [item for item in results if item.get("section", "primary") == "primary"]
    secondary = [item for item in results if item.get("section", "primary") != "primary"]
    hidden = 0
    if any(radar_result_title_key(item) for item in primary):
        firsts: list[dict[str, Any]] = []
        copies: dict[str, list[tuple[int, dict[str, Any], tuple[str, str, str]]]] = {}
        for position, item in enumerate(primary):
            key = radar_result_title_key(item)
            if not key:
                firsts.append(item)
            elif key in copies:
                copies[key].append((position, item, radar_result_variant(item)))
            else:
                copies[key] = [(position, item, radar_result_variant(item))]
                firsts.append(item)
        chosen: list[tuple[int, dict[str, Any]]] = []
        for candidates in copies.values():
            seen = [candidates[0][2]]
            rest = candidates[1:]
            while len(seen) < RADAR_MAX_COPIES_PER_TITLE:
                distinct = [candidate for candidate in rest if candidate[2] not in seen]
                if not distinct:
                    break
                boxed = [candidate for candidate in distinct if candidate[2][0] == "boxed"]
                pick = boxed[0] if boxed and all(variant[0] != "boxed" for variant in seen) else distinct[0]
                seen.append(pick[2])
                chosen.append((pick[0], pick[1]))
                rest.remove(pick)
            hidden += len(candidates) - len(seen)
        primary = firsts + [item for _, item in sorted(chosen, key=lambda entry: entry[0])]
    ordered = primary + secondary
    return ordered[: max(1, limit)], hidden


def radar_target_coverage(criteria: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Cuántos de los títulos perseguidos tienen algo, y cuáles no."""
    targets = [target for target in criteria.get("titleTargets") or [] if target.get("query")]
    if not targets:
        return None
    covered = {radar_result_title_key(item) for item in results if item.get("section", "primary") == "primary"}
    missing = [
        str(target.get("name") or target.get("query"))
        for target in targets
        if radar_query_key(target.get("gameId") or target.get("query")) not in covered
    ]
    return {"total": len(targets), "covered": len(targets) - len(missing), "uncovered": missing}


def radar_search_inventory(
    conn: sqlite3.Connection, search_id: str, limit: int, criteria: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Lo que la búsqueda muestra, y lo que se sabe de lo que no muestra.

    Descartar es definitivo para esta publicación: una caja suelta o algo que
    no era lo buscado no tiene que volver a aparecer en cada corrida. La
    decisión vive en `radar_decisions`, no en el match, así que sobrevive a que
    la búsqueda se vuelva a ejecutar.
    """

    rows = conn.execute(
        f"""{RADAR_MATCH_SELECT}
            LEFT JOIN radar_decisions d ON d.listing_id = l.id
            WHERE m.search_id = ? AND m.is_active = 1
              AND COALESCE(d.decision, '') != 'dismissed'
            ORDER BY COALESCE(m.score, -1) DESC, m.confidence DESC, m.last_seen_at DESC LIMIT ?""",
        (search_id, RADAR_INVENTORY_SCAN_LIMIT),
    ).fetchall()
    every = [radar_result_row(row) for row in rows]
    shown, hidden = diversify_radar_results(every, max(1, min(limit, RADAR_MAX_RESULT_LIMIT)))
    meta = {
        "activeMatches": len(every),
        "hiddenCopies": hidden,
        "unconfirmedCount": sum(1 for item in shown if item.get("section", "primary") != "primary"),
        "targetCoverage": radar_target_coverage(criteria or {}, every),
    }
    return shown, meta


def radar_full_matches(conn: sqlite3.Connection, listing_id: str) -> list[dict[str, Any]]:
    """Matches con la forma completa que usa el inventario de búsquedas."""
    rows = conn.execute(
        """
        SELECT m.search_id, m.confidence, m.reasons_json, m.unverified_json,
               m.matched_terms_json, s.name AS search_name, s.search_type, s.priority
          FROM radar_search_matches m
          JOIN radar_searches s ON s.id = m.search_id AND s.deleted_at IS NULL
         WHERE m.listing_id = ? AND m.is_active = 1
         ORDER BY m.confidence DESC, s.name
        """,
        (listing_id,),
    ).fetchall()
    return [
        {
            "searchId": row["search_id"],
            "searchName": row["search_name"],
            "searchType": row["search_type"],
            "priority": row["priority"],
            "confidence": row["confidence"],
            "reasons": radar_json_field(row["reasons_json"], []),
            "unverified": radar_json_field(row["unverified_json"], []),
            "matchedTerms": radar_json_field(row["matched_terms_json"], []),
        }
        for row in rows
    ]


def list_radar_listings(config: AppConfig, limit: int = 100) -> dict[str, Any]:
    """Inventario deduplicado: una publicación, una fila, todas sus búsquedas.

    Es el criterio de aceptación §21.7 del PRD: una publicación que coincide con
    dos búsquedas se guarda una vez y se muestra una vez, con las dos razones.
    Eso dedupea por `listing_id`. Dos `itemId` distintos que resultan ser el
    mismo artículo se agrupan además por `radar_content_fingerprint`.
    """

    capped = max(1, min(int(limit or 100), 500))
    with _RADAR_LOCK, connect_db(config) as conn:
        listing_rows = conn.execute(
            """
            SELECT l.*, MAX(m.confidence) AS best_confidence, COUNT(*) AS match_count,
                   MAX(m.last_seen_at) AS match_last_seen_at,
                   MAX(COALESCE(m.score, -1)) AS best_score,
                   -- SQLite deja leer las demás columnas de la fila que ganó el MAX.
                   m.band AS best_band, m.valuation_json AS best_valuation_json
              FROM radar_listings l
              JOIN radar_search_matches m ON m.listing_id = l.id AND m.is_active = 1
              JOIN radar_searches s ON s.id = m.search_id AND s.deleted_at IS NULL
             GROUP BY l.id
             ORDER BY best_score DESC, best_confidence DESC, match_last_seen_at DESC
             LIMIT ?
            """,
            (capped,),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for cluster in group_radar_rows_by_content(listing_rows):
            row = radar_cluster_representative(cluster)
            matches = radar_cluster_matches_using(conn, cluster, radar_full_matches)
            verification = radar_verification_state(row)
            items.append(
                {
                    "id": row["id"],
                    "sourceId": row["source_id"],
                    "sourceLabel": radar_source_label(str(row["source_id"])),
                    "externalId": row["external_id"],
                    "title": row["title"],
                    "listingUrl": row["listing_url"],
                    "imageUrl": row["image_url"],
                    "listingKind": row["listing_kind"],
                    "listingType": RADAR_LISTING_KIND_LABELS.get(str(row["listing_kind"]), ""),
                    "priceLabel": row["price_label"],
                    "priceAmount": row["price_amount"],
                    "priceCurrency": row["price_currency"],
                    "shippingAmount": row["shipping_amount"],
                    "shippingLabel": row["shipping_label"],
                    "totalAmount": row["total_amount"],
                    "conditionLabel": row["condition_label"],
                    "locationLabel": row["location_label"],
                    "sellerLabel": row["seller_label"],
                    "availability": row["availability"],
                    "closesAt": row["closes_at"],
                    "confidence": row["best_confidence"],
                    # La banda y el score salen de la coincidencia mejor puntuada:
                    # una misma publicación puede valer distinto según la búsqueda.
                    "score": row["best_score"] if row["best_score"] is not None and row["best_score"] >= 0 else None,
                    "band": effective_band(
                        str(row["best_band"] or ""),
                        radar_json_field(row["best_valuation_json"], {}),
                        verification["state"] in {"verified", "available"},
                        bool(verification["contentsVerifiedAt"]),
                    ),
                    "valuation": radar_json_field(row["best_valuation_json"], {}),
                    "verification": verification,
                    "requiresVerification": verification["requiresVerification"],
                    "verifiedAt": row["verified_at"],
                    "firstSeenAt": row["first_seen_at"],
                    "lastSeenAt": row["last_seen_at"],
                    "matchCount": len(matches),
                    "duplicateCount": len(cluster) - 1,
                    "matches": matches,
                }
            )
    return {
        "version": RADAR_SEARCHES_VERSION,
        "environment": config.ebay_environment,
        "sources": radar_sources_payload(),
        "count": len(items),
        "items": items,
    }


def radar_environment_label(config: AppConfig) -> str:
    return "eBay Sandbox · datos de prueba" if config.ebay_environment == "sandbox" else "eBay USA"
