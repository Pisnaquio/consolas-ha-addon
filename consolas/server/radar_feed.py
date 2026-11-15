"""Collection Radar: the personal feed and alert annotations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from radar.valuation import alert_clearance, effective_band
from radar.wishlist import ALERTING_INTENTS, search_intent

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        RADAR_FEED_LIMIT,
        RADAR_LISTING_KIND_LABELS,
        RADAR_SEARCHES_VERSION,
        RADAR_SECONDARY_SECTIONS,
        _RADAR_LOCK,
    )
    from .database import connect_db
    from .radar_catalog import radar_wished_keys
    from .radar_criteria import radar_json_field, radar_source_label
    from .radar_decisions import (
        group_radar_rows_by_content,
        radar_cluster_decision,
        radar_cluster_matches,
        radar_cluster_representative,
        radar_decision_row,
        radar_price_drop,
        snooze_is_active,
    )
    from .radar_results import radar_verification_state
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import (
        RADAR_FEED_LIMIT,
        RADAR_LISTING_KIND_LABELS,
        RADAR_SEARCHES_VERSION,
        RADAR_SECONDARY_SECTIONS,
        _RADAR_LOCK,
    )
    from database import connect_db
    from radar_catalog import radar_wished_keys
    from radar_criteria import radar_json_field, radar_source_label
    from radar_decisions import (
        group_radar_rows_by_content,
        radar_cluster_decision,
        radar_cluster_matches,
        radar_cluster_representative,
        radar_decision_row,
        radar_price_drop,
        snooze_is_active,
    )
    from radar_results import radar_verification_state


def radar_feed_item(row: sqlite3.Row, decision: dict[str, Any] | None, matches: list[dict[str, Any]]) -> dict[str, Any]:
    drop = radar_price_drop(row)
    valuation = radar_json_field(row["valuation_json"] if "valuation_json" in row.keys() else "", {})
    verification = radar_verification_state(row)
    stored_band = str(row["best_band"] or "") if "best_band" in row.keys() else ""
    return {
        "id": row["id"],
        "sourceId": row["source_id"],
        "sourceLabel": radar_source_label(str(row["source_id"])),
        "title": row["title"],
        "listingUrl": row["listing_url"],
        # El formato crudo además de su etiqueta: "acepta ofertas" no es
        # decoración, es la única vía por la que el precio de esta publicación
        # puede bajar.
        "listingKind": row["listing_kind"],
        "listingType": RADAR_LISTING_KIND_LABELS.get(str(row["listing_kind"]), ""),
        "priceLabel": row["price_label"],
        "priceAmount": row["price_amount"],
        "priceCurrency": row["price_currency"],
        "shippingLabel": row["shipping_label"],
        "totalAmount": row["total_amount"],
        "conditionLabel": row["condition_label"],
        "sellerLabel": row["seller_label"],
        "closesAt": row["closes_at"],
        "score": row["best_score"] if "best_score" in row.keys() else None,
        "band": effective_band(
            stored_band,
            valuation,
            verification["state"] in {"verified", "available"},
            bool(verification["contentsVerifiedAt"]),
        ),
        "objective": (valuation.get("assessment") or {}).get("objective"),
        "valuation": valuation,
        "priceDrop": drop,
        "decision": decision,
        "matches": matches,
        "verification": verification,
        "requiresVerification": verification["requiresVerification"],
        "verifiedAt": row["verified_at"] if "verified_at" in row.keys() else None,
        "firstSeenAt": row["first_seen_at"],
        "lastSeenAt": row["last_seen_at"],
    }


RADAR_FEED_SELECT = """
    SELECT l.*, MAX(COALESCE(m.score, -1)) AS best_score, m.band AS best_band,
           m.valuation_json AS valuation_json, COUNT(*) AS match_count
      FROM radar_listings l
      JOIN radar_search_matches m ON m.listing_id = l.id AND m.is_active = 1
      JOIN radar_searches s ON s.id = m.search_id AND s.deleted_at IS NULL
     GROUP BY l.id
"""


def read_radar_decisions(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        str(row["listing_id"]): radar_decision_row(row)
        for row in conn.execute("SELECT * FROM radar_decisions").fetchall()
    }


def list_radar_feed(config: AppConfig, limit: int = RADAR_FEED_LIMIT) -> dict[str, Any]:
    """Las oportunidades accionables de hoy, hasta diez.

    Fuera quedan las descartadas y las dormidas — salvo que hayan bajado de
    precio de forma material, porque eso es exactamente el cambio que hace que
    valga la pena volver a mirarlas.
    """

    capped = max(1, min(int(limit or RADAR_FEED_LIMIT), 50))
    now = datetime.now(timezone.utc)
    wished = radar_wished_keys(config)
    with _RADAR_LOCK, connect_db(config) as conn:
        decisions = read_radar_decisions(conn)
        rows = conn.execute(f"{RADAR_FEED_SELECT} ORDER BY best_score DESC, l.last_seen_at DESC").fetchall()
        clusters = group_radar_rows_by_content(rows)

        feed: list[dict[str, Any]] = []
        following: list[dict[str, Any]] = []
        dismissed = 0
        sleeping = 0

        for cluster in clusters:
            # Dos `itemId` del mismo artículo son una sola oportunidad: se
            # eligen la copia más fresca, la decisión que ya se tomó sobre
            # cualquiera de los dos, y la unión de búsquedas que lo encontraron.
            row = radar_cluster_representative(cluster)
            decision = radar_cluster_decision(cluster, decisions)
            drop = radar_price_drop(row)
            item = None

            if decision and decision["decision"] == "dismissed":
                dismissed += 1
                continue
            if decision and snooze_is_active(decision, now):
                # Una baja material despierta lo dormido: es el cambio que pedía.
                if not (drop and drop["material"]):
                    sleeping += 1
                    continue
            if decision and decision["decision"] == "purchased":
                continue
            # Un lote que no confirma cuántos juegos trae no cumple su búsqueda:
            # se ve en la búsqueda, aparte, pero no es una decisión del día.
            best_valuation = radar_json_field(row["valuation_json"] if "valuation_json" in row.keys() else "", {})
            if best_valuation.get("section") in RADAR_SECONDARY_SECTIONS and not (
                decision and decision["decision"] == "following"
            ):
                continue

            item = radar_annotate_alert(radar_feed_item(row, decision, radar_cluster_matches(conn, cluster)), wished)
            if decision and decision["decision"] == "following":
                following.append(item)
            if len(feed) < capped:
                feed.append(item)

    # Lo que bajó de precio va primero: es la novedad, no el ranking de siempre.
    feed.sort(key=lambda entry: (0 if (entry["priceDrop"] or {}).get("material") else 1, -(entry["score"] or 0)))
    return {
        "version": RADAR_SEARCHES_VERSION,
        "environment": config.ebay_environment,
        "generatedAt": utc_now(),
        "counts": {
            "feed": len(feed),
            "following": len(following),
            "dismissed": dismissed,
            "snoozed": sleeping,
        },
        "items": feed,
        "following": following,
    }


def list_radar_decisions(config: AppConfig) -> dict[str, Any]:
    """Historial: qué decidiste y sobre qué, con la publicación al lado."""
    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            """SELECT d.*, l.title, l.listing_url, l.price_amount, l.price_currency, l.price_label,
                      l.previous_price_amount, l.price_changed_at
                 FROM radar_decisions d
                 JOIN radar_listings l ON l.id = d.listing_id
                ORDER BY d.updated_at DESC"""
        ).fetchall()
    items = []
    for row in rows:
        entry = radar_decision_row(row)
        entry.update(
            {
                "title": row["title"],
                "listingUrl": row["listing_url"],
                "priceLabel": row["price_label"],
                "priceAmount": row["price_amount"],
                "priceCurrency": row["price_currency"],
                "priceDrop": radar_price_drop(row),
                "snoozeActive": snooze_is_active(entry),
            }
        )
        items.append(entry)
    return {"version": RADAR_SEARCHES_VERSION, "count": len(items), "items": items}


def radar_item_intents(item: dict[str, Any], wished: set[tuple[str, str]]) -> list[str]:
    """Qué persiguen las búsquedas que encontraron esta publicación."""
    intents = {
        search_intent(
            str(match.get("searchType") or ""),
            str(match.get("entityType") or ""),
            str(match.get("entityConsoleId") or ""),
            str(match.get("entityId") or ""),
            wished,
            match.get("titleTarget") if isinstance(match.get("titleTarget"), dict) else None,
        )
        for match in item.get("matches") or []
    }
    return sorted(intents)


def radar_alert_review(item: dict[str, Any]) -> dict[str, Any]:
    """Si puede interrumpir con un mail y, si no, por qué.

    Lo que el precio presenta como oportunidad —banda de ganga o buena compra,
    o el objetivo cumplido— pero no pasa la compuerta queda como «Candidato
    para revisar»: se ve en la app con sus razones y nunca llega como ganga.
    """

    valuation = item.get("valuation") or {}
    band = str(item.get("band") or "")
    reasons = alert_clearance(valuation, band)
    intents = set(item.get("intents") or [])
    if not intents & ALERTING_INTENTS:
        reasons.append(
            "es una recomendación, no un deseo de Nacho's Wishlist"
            if "recommendation" in intents
            else "la búsqueda no apunta a un deseo, una consola que falta ni un lote elegido"
        )
    target = valuation.get("target") or {}
    priced_like_opportunity = (
        str(valuation.get("priceBand") or "") in {"ganga", "buena"} or target.get("meets") is True
    )
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "candidate": bool(reasons) and priced_like_opportunity,
        "label": "Candidato para revisar" if reasons and priced_like_opportunity else "",
    }


def radar_annotate_alert(item: dict[str, Any], wished: set[tuple[str, str]]) -> dict[str, Any]:
    item["intents"] = radar_item_intents(item, wished)
    item["alertReview"] = radar_alert_review(item)
    return item
