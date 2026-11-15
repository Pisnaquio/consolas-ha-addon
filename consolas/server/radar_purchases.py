"""Collection Radar: coverage, shipment, purchases, budget and lot valuation."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from radar.master import rank_game, wanted_games
from radar.valuation import LotPiece, lot_valuation

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import _RADAR_LOCK
    from .database import connect_db
    from .errors import ApiError
    from .radar_catalog import load_console_catalog, load_game_names_by_console
    from .radar_criteria import (
        normalize_radar_amount,
        normalize_radar_entity_console_id,
        normalize_radar_enum,
        normalize_radar_id,
        normalize_radar_text,
        radar_json_field,
    )
    from .radar_decisions import radar_decision_row
    from .radar_preferences import radar_current_month_bounds, radar_preferences_row
    from .radar_runs import parse_iso_datetime
    from .state import read_state
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import _RADAR_LOCK
    from database import connect_db
    from errors import ApiError
    from radar_catalog import load_console_catalog, load_game_names_by_console
    from radar_criteria import (
        normalize_radar_amount,
        normalize_radar_entity_console_id,
        normalize_radar_enum,
        normalize_radar_id,
        normalize_radar_text,
        radar_json_field,
    )
    from radar_decisions import radar_decision_row
    from radar_preferences import radar_current_month_bounds, radar_preferences_row
    from radar_runs import parse_iso_datetime
    from state import read_state


# Franquicia de importación: un reenvío desde la casilla entra sin impuestos
# mientras la **mercadería** quede por debajo de este monto. No cuentan ni el
# envío del vendedor dentro de Estados Unidos ni el courier hasta Uruguay: sólo
# lo que valen las cosas.
#
# El régimen alterno —una compra de hasta 800 pagando IVA— es excluyente con
# éste, así que no se modela: el owner elige uno u otro fuera de la app.
#
# El cupo anual de tres envíos tampoco se lleva acá. El owner puede usar
# franquicias de familiares, así que contarlas daría una restricción que en la
# práctica no existe y avisos que no corresponden.
DUTY_FREE_MERCHANDISE_USD = 200.0


def radar_targeted_entities(
    config: AppConfig, *, active_only: bool = False, include_deleted: bool = False
) -> tuple[set[str], set[str], set[tuple[str, str]]]:
    """Qué ya tiene una búsqueda apuntándole: consolas, lotes y juegos.

    Los tres se separan porque son objetivos distintos: buscar una PS2 y buscar
    un lote de PS2 son cosas diferentes, y tener una no debería tapar la otra.

    Las dos banderas separan preguntas distintas. Para la cobertura interesa lo
    que el radar está mirando **ahora**: una búsqueda archivada no cubre nada.
    Para el Master interesa todo lo que el owner ya vio pasar y decidió —
    archivadas y borradas incluidas. Volver a proponer algo que borró no sólo es
    no escuchar: `regenerate` igual lo saltea por id, así que la propuesta se
    come el cupo de la tanda sin crear nada.
    """

    query = "SELECT search_type, entity_type, entity_id, entity_console_id, criteria_json FROM radar_searches"
    conditions = []
    if not include_deleted:
        conditions.append("deleted_at IS NULL")
    if active_only:
        conditions.append("status = 'active'")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(query).fetchall()

    consoles: set[str] = set()
    lots: set[str] = set()
    games: set[tuple[str, str]] = set()
    for row in rows:
        entity_id = str(row["entity_id"] or "")
        console_id = str((row["entity_console_id"] if "entity_console_id" in row.keys() else "") or "")
        is_console_entity = str(row["entity_type"] or "") == "console" and entity_id
        if str(row["search_type"] or "") == "lot":
            if is_console_entity:
                lots.add(entity_id)
            # Un lote se vincula a su consola sin ser la consola: así «Registrar
            # compra» de un lote nunca marca la consola como comprada.
            elif console_id and not entity_id:
                lots.add(console_id)
        elif is_console_entity:
            consoles.add(entity_id)
        if console_id and entity_id:
            games.add((console_id, entity_id))
        # Una búsqueda de varios títulos persigue cada juego de sus objetivos.
        if console_id:
            criteria = radar_json_field(row["criteria_json"], {}) if "criteria_json" in row.keys() else {}
            for target in criteria.get("titleTargets") or []:
                if isinstance(target, dict) and target.get("gameId"):
                    games.add((console_id, str(target["gameId"])))
    return consoles, lots, games


def compute_radar_coverage(config: AppConfig) -> dict[str, Any]:
    """Qué parte de la colección el radar no está mirando.

    El Master propone, pero nadie audita el hueco: propone de a puñados —los
    cupos existen para no generar cuatrocientas búsquedas— así que lo que queda
    afuera es invisible salvo que alguien lo cuente.

    Una consola está cubierta si alguna búsqueda activa la nombra como entidad,
    sea una búsqueda de esa consola o de un juego suyo. Un juego deseado está
    cubierto sólo si alguna búsqueda persigue ese juego en esa consola: el
    mismo juego existe en varias plataformas, y una búsqueda de SNES no cubre
    la versión de Genesis.
    """

    state = read_state(config)
    catalog = {str(entry.get("id") or ""): entry for entry in load_console_catalog(config)}
    overrides = (state.get("user") or {}).get("overridesById") or {}
    owned = {console_id for console_id, value in overrides.items() if (value or {}).get("tengo") is True}

    consoles_with_search, lots_with_search, games_with_search = radar_targeted_entities(config, active_only=True)
    consoles_with_search = consoles_with_search | lots_with_search

    wanted = wanted_games(state, load_game_names_by_console(config))
    wanted_by_console: dict[str, list[dict[str, Any]]] = {}
    for game in wanted:
        wanted_by_console.setdefault(game["consoleId"], []).append(game)

    consoles: list[dict[str, Any]] = []
    for console_id in sorted(owned | set(wanted_by_console)):
        games = sorted(wanted_by_console.get(console_id, []), key=rank_game)
        uncovered = [game for game in games if (console_id, game["gameId"]) not in games_with_search]
        explicit_uncovered = [game for game in uncovered if game["explicit"]]
        consoles.append(
            {
                "id": console_id,
                "name": str((catalog.get(console_id) or {}).get("nombre") or console_id),
                "owned": console_id in owned,
                "hasSearch": console_id in consoles_with_search,
                "wantedGames": len(games),
                "uncoveredGames": len(uncovered),
                # Marcar "lo quiero" es un objetivo de compra; una recomendación
                # conservada es mucho más débil. Mezclarlas exageraría el hueco.
                "explicitUncovered": len(explicit_uncovered),
                # Unos pocos nombres alcanzan para decidir; la lista entera sería
                # el inventario, que ya vive en la ficha de cada consola.
                "examples": [game["name"] for game in (explicit_uncovered or uncovered)[:3]],
            }
        )

    return {
        "consoles": consoles,
        "ownedWithoutSearch": sorted(console_id for console_id in owned if console_id not in consoles_with_search),
        "wantedGames": len(wanted),
        "uncoveredGames": sum(entry["uncoveredGames"] for entry in consoles),
        "explicitWanted": sum(1 for game in wanted if game["explicit"]),
        "explicitUncovered": sum(entry["explicitUncovered"] for entry in consoles),
    }


def compute_radar_shipment(config: AppConfig) -> dict[str, Any]:
    """Lo que está esperando en la casilla, contra la franquicia.

    Sólo suma mercadería, que es sobre lo que se mide la franquicia. El courier
    se calcula sobre el paquete completo y no cambia si entra o no en el cupo,
    así que no tiene por qué ensuciar este número.
    """

    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            """SELECT p.*, l.title AS listing_title
                 FROM radar_purchases p
                 LEFT JOIN radar_listings l ON l.id = p.listing_id
                WHERE p.shipped_at IS NULL
                ORDER BY p.purchased_at"""
        ).fetchall()

    merchandise = round(sum(float(row["price_amount"] or 0) for row in rows), 2)
    return {
        "currency": "USD",
        "limit": DUTY_FREE_MERCHANDISE_USD,
        "merchandise": merchandise,
        "headroom": round(DUTY_FREE_MERCHANDISE_USD - merchandise, 2),
        "overLimit": merchandise > DUTY_FREE_MERCHANDISE_USD,
        "count": len(rows),
        "items": [
            {
                "id": row["id"],
                "title": radar_purchase_title(row),
                "manual": radar_purchase_is_manual(row),
                "priceAmount": row["price_amount"],
                "purchasedAt": row["purchased_at"],
            }
            for row in rows
        ],
    }


def close_radar_shipment(config: AppConfig) -> dict[str, Any]:
    """Marca como reenviado todo lo que estaba esperando, y abre una pila nueva.

    Es una acción explícita del owner: el radar no puede saber cuándo pidió el
    reenvío, y adivinarlo haría que la pila se vacíe sola en el peor momento.
    """

    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        pending = conn.execute(
            "SELECT COALESCE(SUM(price_amount), 0) AS total, COUNT(*) AS n"
            "  FROM radar_purchases WHERE shipped_at IS NULL"
        ).fetchone()
        if not int(pending["n"] or 0):
            raise ApiError(HTTPStatus.CONFLICT, "No hay compras esperando en la casilla.")
        conn.execute("UPDATE radar_purchases SET shipped_at = ? WHERE shipped_at IS NULL", (now,))

    return {
        "ok": True,
        "shippedAt": now,
        "merchandise": round(float(pending["total"] or 0), 2),
        "count": int(pending["n"] or 0),
        "shipment": compute_radar_shipment(config),
    }


def delete_radar_purchase(config: AppConfig, purchase_id: Any) -> dict[str, Any]:
    """Borra el registro de una compra que no ocurrió.

    "Registrar compra" escribe en tres lados —evidencia, presupuesto y la
    colección— y hasta acá sólo se podía deshacer a mano en dos de ellos. Una
    acción que escribe la colección tiene que poder revertirse, sobre todo
    cuando basta un click para dispararla.

    La decisión sobre la publicación se limpia aparte, con el endpoint que ya
    existe: son dos hechos distintos y borrar uno no implica el otro.
    """

    target_id = normalize_radar_id(purchase_id)
    with _RADAR_LOCK, connect_db(config) as conn:
        row = conn.execute("SELECT * FROM radar_purchases WHERE id = ?", (target_id,)).fetchone()
        if row is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Purchase not found")
        conn.execute("DELETE FROM radar_purchases WHERE id = ?", (target_id,))

    return {
        "ok": True,
        "deleted": {
            "id": target_id,
            "listingId": row["listing_id"] or "",
            "entityType": row["entity_type"],
            "entityId": row["entity_id"],
            "priceAmount": row["price_amount"],
        },
        "budget": compute_radar_budget(config),
        "shipment": compute_radar_shipment(config),
    }


def radar_purchase_title(row: sqlite3.Row) -> str:
    """Qué se compró, aunque la publicación ya no exista.

    El nombre guardado con la compra manda. La publicación sólo vive 24 h
    después de venderse, así que su título es apenas un respaldo para compras
    registradas antes de que existiera ese nombre.
    """
    keys = row.keys()
    own = str(row["title"] or "") if "title" in keys else ""
    listing = str(row["listing_title"] or "") if "listing_title" in keys else ""
    return own or listing or str(row["entity_id"] or "")


def radar_purchase_is_manual(row: sqlite3.Row) -> bool:
    # No alcanza con mirar listing_id: una compra del radar también lo pierde
    # cuando su publicación se purga.
    return bool(row["manual"]) if "manual" in row.keys() else False


def list_radar_purchases(config: AppConfig) -> dict[str, Any]:
    """Historial de compras registradas, lo más reciente primero."""

    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            """SELECT p.*, l.title AS listing_title
                 FROM radar_purchases p
                 LEFT JOIN radar_listings l ON l.id = p.listing_id
                ORDER BY p.purchased_at DESC"""
        ).fetchall()
    return {
        "items": [
            {
                "id": row["id"],
                "listingId": row["listing_id"] or "",
                "title": radar_purchase_title(row),
                "manual": radar_purchase_is_manual(row),
                "entityType": row["entity_type"],
                "entityId": row["entity_id"],
                "entityConsoleId": row["entity_console_id"] if "entity_console_id" in row.keys() else "",
                "priceAmount": row["price_amount"],
                "currency": row["currency"],
                "purchasedAt": row["purchased_at"],
                "shippedAt": row["shipped_at"] if "shipped_at" in row.keys() else None,
            }
            for row in rows
        ]
    }


def compute_radar_budget(config: AppConfig) -> dict[str, Any]:
    """Gastado, reservado, disponible e impacto (PRD §10.6).

    El presupuesto da contexto, nunca oculta nada por sí solo — eso lo decide
    quien mira el feed, no este cálculo.
    """

    month_label, month_start, month_end = radar_current_month_bounds()
    with _RADAR_LOCK, connect_db(config) as conn:
        prefs = radar_preferences_row(conn.execute("SELECT * FROM radar_preferences WHERE id = 'default'").fetchone())
        spent_row = conn.execute(
            """SELECT COALESCE(SUM(price_amount), 0) AS total, COUNT(*) AS n
                 FROM radar_purchases
                WHERE purchased_at >= ? AND purchased_at < ?""",
            (month_start, month_end),
        ).fetchone()
        reserved_row = conn.execute(
            """SELECT COALESCE(SUM(l.total_amount), 0) AS total, COUNT(*) AS n
                 FROM radar_decisions d
                 JOIN radar_listings l ON l.id = d.listing_id
                WHERE d.reserved = 1 AND d.decision = 'following'"""
        ).fetchone()

    spent = round(float(spent_row["total"] or 0), 2)
    reserved = round(float(reserved_row["total"] or 0), 2)
    monthly_budget = prefs["monthlyBudgetUsd"]
    available = round(monthly_budget - spent - reserved, 2) if monthly_budget is not None else None

    return {
        "month": month_label,
        "currency": "USD",
        "monthlyBudgetUsd": monthly_budget,
        "configured": monthly_budget is not None,
        "spent": spent,
        "spentCount": int(spent_row["n"] or 0),
        "reserved": reserved,
        "reservedCount": int(reserved_row["n"] or 0),
        "available": available,
    }


# --- «Registrar compra» (PRD §9, §24, slice 9) ------------------------------
#
# Esto NO escribe la colección. Graba la evidencia del radar — para el
# presupuesto y el historial — y marca la decisión como "purchased". El
# estado real de usuario (tengo/ownershipType/precioPagado) lo escribe el
# frontend después, por `CollectionRepository`, la única autoridad de
# pertenencia. Separar los dos pasos es deliberado: si el segundo paso
# fallara (por ejemplo el navegador se cierra), la compra ya quedó
# registrada para el presupuesto y no hay que perder esa evidencia.

RADAR_PURCHASE_ENTITY_TYPES = ("console", "game", "accessory")


def normalize_radar_lot_piece(raw: Any, index: int) -> LotPiece:
    if not isinstance(raw, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"pieces[{index}] must be an object")
    name = normalize_radar_text(raw.get("name"), f"pieces[{index}].name", 200, required=True)
    return LotPiece(
        name=name,
        comparable_value=normalize_radar_amount(raw.get("comparableValue"), f"pieces[{index}].comparableValue"),
        condition_factor=(
            max(0.0, min(1.0, float(raw["conditionFactor"])))
            if raw.get("conditionFactor") not in (None, "")
            else 1.0
        ),
        wanted=raw.get("wanted") is not False,
        already_owned=raw.get("alreadyOwned") is True,
        is_duplicate=raw.get("isDuplicate") is True,
    )


def compute_radar_lot_valuation(payload: Any) -> dict[str, Any]:
    """Cálculo puro (PRD §10.5): nada se persiste, nada se infiere del texto

    de la publicación. Las piezas las declara quien mira el lote — el radar
    no reconoce fotos ni parsea "PS2 + 8 juegos" de un título.
    """

    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    raw_pieces = payload.get("pieces")
    if not isinstance(raw_pieces, list):
        raise ApiError(HTTPStatus.BAD_REQUEST, "pieces must be an array")
    if len(raw_pieces) > 100:
        raise ApiError(HTTPStatus.BAD_REQUEST, "pieces cannot exceed 100 entries")
    pieces = [normalize_radar_lot_piece(raw, index) for index, raw in enumerate(raw_pieces)]
    total_cost = normalize_radar_amount(payload.get("totalCost"), "totalCost")
    result = lot_valuation(pieces, total_cost)
    return {"ok": True, **result}


def record_radar_purchase(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    # Sin publicación es una compra cargada a mano: algo comprado fuera del
    # radar que igual ocupa la casilla y cuenta como gasto. No hay de dónde
    # sacar qué es, así que el nombre pasa a ser obligatorio, y la entidad, que
    # sólo sirve para escribir la colección desde el feed, es opcional.
    manual = not str(payload.get("listingId") or "").strip()
    listing_id = None if manual else normalize_radar_id(payload.get("listingId"))
    title = normalize_radar_text(payload.get("title"), "title", 200, required=manual)
    entity_type = normalize_radar_enum(payload.get("entityType"), RADAR_PURCHASE_ENTITY_TYPES, "", "entityType")
    if not entity_type and not manual:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"entityType must be one of: {', '.join(RADAR_PURCHASE_ENTITY_TYPES)}"
        )
    entity_id = normalize_radar_text(payload.get("entityId"), "entityId", 120, required=bool(entity_type))
    entity_console_id = normalize_radar_entity_console_id(payload.get("entityConsoleId"), entity_type)
    # Un juego o un accesorio siempre pertenecen a una consola. Registrar la
    # compra sin saber a cuál dejaría una evidencia que nadie puede aplicar
    # después: el historial diría "compraste God of War" sin decir en qué
    # plataforma, y el mismo juego existe en varias.
    if entity_type in {"game", "accessory"} and not entity_console_id:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"entityConsoleId is required when entityType is {entity_type}"
        )
    price_amount = normalize_radar_amount(payload.get("priceAmount"), "priceAmount")
    if price_amount is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, "priceAmount is required")
    currency = normalize_radar_text(payload.get("currency"), "currency", 8, required=False).upper() or "USD"
    now = utc_now()
    # A diferencia de un snooze, una compra puede registrarse con fecha pasada
    # (la comprás hoy y la cargás mañana) — nunca futura.
    raw_purchased_at = payload.get("purchasedAt")
    if raw_purchased_at:
        parsed = parse_iso_datetime(raw_purchased_at)
        if parsed is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "purchasedAt must be an ISO-8601 timestamp")
        if parsed > datetime.now(timezone.utc):
            raise ApiError(HTTPStatus.BAD_REQUEST, "purchasedAt cannot be in the future")
        purchased_at = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    else:
        purchased_at = now

    decision_row = None
    with _RADAR_LOCK, connect_db(config) as conn:
        if not manual:
            listing = conn.execute("SELECT * FROM radar_listings WHERE id = ?", (listing_id,)).fetchone()
            if listing is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "Listing not found")
        purchase_id = f"purchase-{uuid.uuid4().hex[:20]}"
        conn.execute(
            """INSERT INTO radar_purchases (
                 id, listing_id, entity_type, entity_id, entity_console_id, title, manual, price_amount,
                 currency, purchased_at, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                purchase_id, listing_id, entity_type, entity_id, entity_console_id, title, int(manual),
                price_amount, currency, purchased_at, now,
            ),
        )
        if not manual:
            record_purchased_decision(conn, listing_id, price_amount, now)
            decision_row = conn.execute("SELECT * FROM radar_decisions WHERE listing_id = ?", (listing_id,)).fetchone()

    return {
        "ok": True,
        "purchase": {
            "id": purchase_id,
            "listingId": listing_id or "",
            "manual": manual,
            "title": title,
            "entityType": entity_type,
            "entityId": entity_id,
            "entityConsoleId": entity_console_id,
            "priceAmount": price_amount,
            "currency": currency,
            "purchasedAt": purchased_at,
        },
        "decision": radar_decision_row(decision_row) if decision_row is not None else None,
        # Cómo queda la casilla después de esta compra: es el momento en que el
        # dato sirve para decidir si conviene pedir el reenvío antes de seguir.
        "shipment": compute_radar_shipment(config),
    }


def record_purchased_decision(conn: sqlite3.Connection, listing_id: str, price_amount: float, now: str) -> None:
    # Reservado deja de tener sentido una vez comprada: ya no es "plan
    # probable", es gasto real, y contarlo dos veces infla el presupuesto.
    conn.execute(
        """INSERT INTO radar_decisions (
             listing_id, decision, reason, note, snooze_until, price_at_decision, reserved,
             created_at, updated_at
           ) VALUES (?, 'purchased', '', '', NULL, ?, 0, ?, ?)
           ON CONFLICT(listing_id) DO UPDATE SET
             decision='purchased', snooze_until=NULL, reserved=0,
             price_at_decision=excluded.price_at_decision, updated_at=excluded.updated_at""",
        (listing_id, price_amount, now, now),
    )
