"""Collection Radar: user decisions, content clusters and match reasons."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any, Callable

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        RADAR_DECISIONS,
        RADAR_DISMISS_REASONS,
        RADAR_MATERIAL_DROP,
        _RADAR_LOCK,
    )
    from .database import connect_db
    from .errors import ApiError
    from .radar_criteria import normalize_radar_id, normalize_radar_text, radar_json_field
    from .radar_runs import parse_iso_datetime
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import (
        RADAR_DECISIONS,
        RADAR_DISMISS_REASONS,
        RADAR_MATERIAL_DROP,
        _RADAR_LOCK,
    )
    from database import connect_db
    from errors import ApiError
    from radar_criteria import normalize_radar_id, normalize_radar_text, radar_json_field
    from radar_runs import parse_iso_datetime


# --------------------------------------------------------------------------- #
# Decisiones del usuario y feed «Para mí»                                       #
# --------------------------------------------------------------------------- #
#
# La calibración de 2026-09 mostró que la mayoría de los «tal vez» son «lo
# quiero pero está caro». Eso no es un rechazo: es una lista de seguimiento
# esperando una baja. De ahí el orden de este módulo — seguir es la decisión
# principal, no descartar.
#
# Nada de esto escribe estado de colección. Registrar una compra marca la
# decisión y deja la escritura sobre la colección para una acción explícita.


def normalize_radar_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    if decision not in RADAR_DECISIONS:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"decision must be one of: {', '.join(RADAR_DECISIONS)}")
    return decision


def normalize_dismiss_reason(value: Any) -> str:
    reason = str(value or "").strip().lower()
    if reason not in RADAR_DISMISS_REASONS:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"reason must be one of: {', '.join(r for r in RADAR_DISMISS_REASONS if r)}",
        )
    return reason


def normalize_snooze_until(value: Any) -> str:
    """Una fecha futura. Dormir algo hasta ayer sería no dormirlo."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = parse_iso_datetime(raw)
    if parsed is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, "snoozeUntil must be an ISO-8601 timestamp")
    if parsed <= datetime.now(timezone.utc):
        raise ApiError(HTTPStatus.BAD_REQUEST, "snoozeUntil must be in the future")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_RADAR_FINGERPRINT_NOISE = re.compile(r"[^a-z0-9]+")


def radar_content_fingerprint(row: sqlite3.Row) -> tuple[Any, ...] | None:
    """Identidad del artículo real detrás de la publicación, no del `itemId`.

    eBay a veces asigna dos `itemId` distintos al mismo artículo — un relist,
    o una publicación repetida por el vendedor. Cada `itemId` sigue siendo una
    fila propia en `radar_listings` (nunca se pisan entre sí), pero mostrar
    las dos en el feed o en los resultados de una búsqueda es mostrar la misma
    oportunidad dos veces. El fingerprint agrupa lo que parece ser el mismo
    artículo: misma fuente, mismo vendedor, mismo precio y un título que
    normaliza igual. Es deliberadamente estricto — exige las cuatro señales a
    la vez — para no fusionar dos publicaciones que sólo se parecen.
    """

    title = row["title"] if "title" in row.keys() else None
    normalized_title = _RADAR_FINGERPRINT_NOISE.sub(" ", str(title or "").lower()).strip()
    if not normalized_title:
        return None
    seller = str(row["seller_label"] if "seller_label" in row.keys() else "" or "").strip().lower()
    price = row["price_amount"] if "price_amount" in row.keys() else None
    return (row["source_id"], normalized_title, price, seller)


def group_radar_rows_by_content(rows: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    """Agrupa filas que son, en los hechos, la misma publicación.

    El orden de entrada se preserva: el primer grupo es el que contiene la
    fila mejor rankeada de la consulta original.
    """

    order: list[tuple[Any, ...]] = []
    groups: dict[tuple[Any, ...], list[sqlite3.Row]] = {}
    for index, row in enumerate(rows):
        fingerprint = radar_content_fingerprint(row) or ("__row__", row["id"], index)
        if fingerprint not in groups:
            groups[fingerprint] = []
            order.append(fingerprint)
        groups[fingerprint].append(row)
    return [groups[key] for key in order]


def radar_cluster_representative(cluster: list[sqlite3.Row]) -> sqlite3.Row:
    """La copia más fresca del artículo: la que mejor describe su estado hoy."""
    return max(cluster, key=lambda row: str(row["last_seen_at"] or ""))


def radar_cluster_decision(
    cluster: list[sqlite3.Row], decisions: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Si cualquier duplicado del artículo tiene una decisión, el artículo la tiene.

    Descartar un `itemId` y ver su relist sin decisión al día siguiente
    reabriría exactamente lo que el usuario ya cerró. Con más de una decisión
    en el grupo (raro: pasa si se decide sobre el duplicado antes de que se
    fusionen) gana la más reciente.
    """

    candidates = [decisions[str(row["id"])] for row in cluster if str(row["id"]) in decisions]
    if not candidates:
        return None
    return max(candidates, key=lambda decision: str(decision.get("updatedAt") or ""))


def radar_cluster_matches_using(
    conn: sqlite3.Connection,
    cluster: list[sqlite3.Row],
    fetch_matches: Callable[[sqlite3.Connection, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Unión de las búsquedas que coincidieron con cualquier duplicado del artículo.

    `fetch_matches` trae la forma de match que necesite cada vista (el feed y
    el inventario de búsquedas no serializan los mismos campos); esta función
    sólo fusiona por `searchId`, quedándose con la de mayor confianza.
    """

    merged: dict[str, dict[str, Any]] = {}
    for row in cluster:
        for match in fetch_matches(conn, str(row["id"])):
            key = str(match["searchId"])
            current = merged.get(key)
            if current is None or (match["confidence"] or 0) > (current["confidence"] or 0):
                merged[key] = match
    return sorted(merged.values(), key=lambda match: -(match["confidence"] or 0))


def radar_cluster_matches(conn: sqlite3.Connection, cluster: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return radar_cluster_matches_using(conn, cluster, radar_match_reasons)


def radar_price_drop(row: sqlite3.Row) -> dict[str, Any] | None:
    """Cuánto bajó una publicación desde la última vez que cambió de precio."""
    keys = row.keys()
    if "previous_price_amount" not in keys:
        return None
    previous = row["previous_price_amount"]
    current = row["price_amount"]
    if previous is None or current is None or previous <= 0 or current >= previous:
        return None
    ratio = (previous - current) / previous
    return {
        "previous": previous,
        "current": current,
        "amount": round(previous - current, 2),
        "ratio": round(ratio, 4),
        "material": ratio >= RADAR_MATERIAL_DROP,
        "changedAt": row["price_changed_at"] if "price_changed_at" in keys else None,
    }


def radar_decision_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "listingId": row["listing_id"],
        "decision": row["decision"],
        "reason": row["reason"],
        "note": row["note"],
        "snoozeUntil": row["snooze_until"],
        "priceAtDecision": row["price_at_decision"],
        "reserved": bool(row["reserved"]) if "reserved" in row.keys() else False,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def snooze_is_active(decision: dict[str, Any], now: datetime | None = None) -> bool:
    if decision.get("decision") != "snoozed":
        return False
    until = parse_iso_datetime(decision.get("snoozeUntil"))
    if until is None:
        return False
    return until > (now or datetime.now(timezone.utc))


def normalize_radar_reserved(value: Any, decision: str) -> int:
    """«Reservado» es presupuesto, no seguimiento: sólo tiene sentido sobre

    algo que seguís de verdad. Marcarlo sobre una descartada o dormida sería
    contar contra el presupuesto algo que ya decidiste que no vas a comprar.
    """

    if value is None:
        return 0
    reserved = bool(value)
    if reserved and decision != "following":
        raise ApiError(HTTPStatus.BAD_REQUEST, "reserved only applies to a followed listing")
    return 1 if reserved else 0


def record_radar_decision(config: AppConfig, payload: Any) -> dict[str, Any]:
    """Guarda qué decidiste sobre una publicación. Nunca toca la colección."""
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    listing_id = normalize_radar_id(payload.get("listingId"))
    decision = normalize_radar_decision(payload.get("decision"))
    reason = normalize_dismiss_reason(payload.get("reason"))
    note = normalize_radar_text(payload.get("note"), "note", 400, required=False)
    snooze_until = normalize_snooze_until(payload.get("snoozeUntil"))
    reserved = normalize_radar_reserved(payload.get("reserved"), decision)
    if decision == "snoozed" and not snooze_until:
        raise ApiError(HTTPStatus.BAD_REQUEST, "snoozeUntil is required to snooze a listing")

    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        listing = conn.execute("SELECT * FROM radar_listings WHERE id = ?", (listing_id,)).fetchone()
        if listing is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Listing not found")
        conn.execute(
            """INSERT INTO radar_decisions (
                 listing_id, decision, reason, note, snooze_until, price_at_decision, reserved,
                 created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(listing_id) DO UPDATE SET
                 decision=excluded.decision, reason=excluded.reason, note=excluded.note,
                 snooze_until=excluded.snooze_until, reserved=excluded.reserved, updated_at=excluded.updated_at""",
            (
                listing_id, decision, reason, note, snooze_until or None,
                listing["price_amount"], reserved, now, now,
            ),
        )
        row = conn.execute("SELECT * FROM radar_decisions WHERE listing_id = ?", (listing_id,)).fetchone()
    return {"ok": True, "decision": radar_decision_row(row)}


def clear_radar_decision(config: AppConfig, listing_id: Any) -> dict[str, Any]:
    target_id = normalize_radar_id(listing_id)
    with _RADAR_LOCK, connect_db(config) as conn:
        cursor = conn.execute("DELETE FROM radar_decisions WHERE listing_id = ?", (target_id,))
    if cursor.rowcount != 1:
        raise ApiError(HTTPStatus.NOT_FOUND, "Decision not found")
    return {"ok": True, "listingId": target_id}


def radar_match_reasons(conn: sqlite3.Connection, listing_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT m.search_id, m.confidence, m.reasons_json, m.unverified_json, m.target_json,
                  s.name AS search_name, s.entity_type, s.entity_id, s.entity_console_id, s.search_type
             FROM radar_search_matches m
             JOIN radar_searches s ON s.id = m.search_id AND s.deleted_at IS NULL
            WHERE m.listing_id = ? AND m.is_active = 1
            ORDER BY m.confidence DESC""",
        (listing_id,),
    ).fetchall()
    return [radar_match_reason_row(row) for row in rows]


def radar_match_reason_row(row: sqlite3.Row) -> dict[str, Any]:
    entity_type = row["entity_type"] or ""
    entity_id = row["entity_id"] or ""
    entity_console_id = (row["entity_console_id"] or "") if "entity_console_id" in row.keys() else ""
    entity_name = ""
    # En una búsqueda de varios títulos, la entidad es el juego que encontró
    # esta publicación, no la búsqueda entera: «Deseados de SNES» no es algo
    # que se pueda marcar como comprado, «Super Mario World» sí.
    target = radar_json_field(row["target_json"], None) if "target_json" in row.keys() else None
    if target and target.get("gameId") and target.get("consoleId"):
        entity_type = "game"
        entity_id = str(target["gameId"])
        entity_console_id = str(target["consoleId"])
        entity_name = str(target.get("name") or "")
    return {
        "searchId": row["search_id"],
        "searchName": row["search_name"],
        "searchType": (row["search_type"] or "") if "search_type" in row.keys() else "",
        "confidence": row["confidence"],
        "reasons": radar_json_field(row["reasons_json"], []),
        "unverified": radar_json_field(row["unverified_json"], []),
        # Qué escribiría "Registrar compra" si se confirma esta oportunidad.
        # Vacío cuando la búsqueda no está vinculada a nada del catálogo.
        # Para un juego hace falta el par completo: sin `entityConsoleId`
        # no se sabe a qué biblioteca escribirlo, y la UI no lo ofrece.
        "entityType": entity_type,
        "entityId": entity_id,
        "entityConsoleId": entity_console_id,
        "entityName": entity_name,
        "titleTarget": target,
    }
