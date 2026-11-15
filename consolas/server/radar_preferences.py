"""Collection Radar: preferences, destination and monthly bounds."""

from __future__ import annotations

import sqlite3
from datetime import timezone
from http import HTTPStatus
from typing import Any

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import _RADAR_LOCK
    from .database import connect_db
    from .errors import ApiError
    from .radar_criteria import normalize_radar_amount, normalize_radar_count
    from .radar_runs import radar_now_local
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import _RADAR_LOCK
    from database import connect_db
    from errors import ApiError
    from radar_criteria import normalize_radar_amount, normalize_radar_count
    from radar_runs import radar_now_local


# --- Presupuesto mensual (PRD §10.6) ----------------------------------------


# Cuántas búsquedas pueden estar activas a la vez. No es una preferencia
# estética: cada activa corre tres veces por día y aporta resultados al mismo
# feed. Pasado cierto punto el feed deja de ser una lista de decisiones y pasa
# a ser un volcado que nadie lee.
RADAR_DEFAULT_MAX_ACTIVE = 25
# Destino de recepción por defecto: Miami, Florida (PRD §24, decisión cerrada).
# Es el ZIP y nada más — la dirección completa es privada y no vive en el repo.
RADAR_DEFAULT_DESTINATION_COUNTRY = "US"
RADAR_DEFAULT_DESTINATION_ZIP = "33166"
RADAR_MAX_PENDING_DRAFTS = 12


def radar_preferences_row(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "monthlyBudgetUsd": None,
            "currency": "USD",
            "maxActiveSearches": RADAR_DEFAULT_MAX_ACTIVE,
            "destinationCountry": "",
            "destinationZip": "",
            "updatedAt": None,
        }
    keys = row.keys()
    cap = row["max_active_searches"] if "max_active_searches" in keys else None
    country = row["destination_country"] if "destination_country" in keys else None
    zip_code = row["destination_zip"] if "destination_zip" in keys else None
    return {
        "monthlyBudgetUsd": row["monthly_budget_usd"],
        "currency": "USD",
        "maxActiveSearches": int(cap) if cap is not None else RADAR_DEFAULT_MAX_ACTIVE,
        "destinationCountry": str(country or ""),
        "destinationZip": str(zip_code or ""),
        "updatedAt": row["updated_at"],
    }


def normalize_radar_destination_country(value: Any, field: str) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if len(text) != 2 or not text.isalpha():
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a two-letter country code")
    return text


def normalize_radar_destination_zip(value: Any, field: str) -> str:
    """Un ZIP, no una dirección. Se acepta el formato corto de EE.UU. y poco más."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if len(text) > 10 or not all(char.isalnum() or char in {"-", " "} for char in text):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a postal code")
    return text


def radar_destination(config: AppConfig) -> dict[str, str] | None:
    """El destino que las fuentes usan para cotizar envío, o None si no está.

    Devolver None es deliberado: sin destino, una fuente no debe inventar un
    envío ni presentar el precio del artículo como total.
    """
    prefs = get_radar_preferences(config)
    country = str(prefs.get("destinationCountry") or "")
    zip_code = str(prefs.get("destinationZip") or "")
    if not country:
        return None
    return {"country": country, "zip": zip_code}


def get_radar_preferences(config: AppConfig) -> dict[str, Any]:
    with _RADAR_LOCK, connect_db(config) as conn:
        row = conn.execute("SELECT * FROM radar_preferences WHERE id = 'default'").fetchone()
    return radar_preferences_row(row)


def update_radar_preferences(config: AppConfig, payload: Any) -> dict[str, Any]:
    """El monto es una preferencia privada, nunca un campo de colección (PRD §10.6)."""
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    now = utc_now()
    with _RADAR_LOCK, connect_db(config) as conn:
        current = radar_preferences_row(
            conn.execute("SELECT * FROM radar_preferences WHERE id = 'default'").fetchone()
        )
        # Una preferencia que no viene en el payload no se toca. Antes el monto
        # se leía siempre, así que guardar el destino —o cualquier otra cosa—
        # borraba el presupuesto sin que nadie lo pidiera. Mandar la clave con
        # `null` sigue siendo la forma de vaciarlo a propósito.
        monthly_budget = (
            normalize_radar_amount(payload.get("monthlyBudgetUsd"), "monthlyBudgetUsd")
            if "monthlyBudgetUsd" in payload
            else current["monthlyBudgetUsd"]
        )
        # Bajar el techo no apaga nada: sólo frena la próxima activación. Apagar
        # búsquedas por un cambio de preferencia sería decidir por el usuario
        # cuáles dejan de importarle.
        max_active = (
            normalize_radar_count(payload.get("maxActiveSearches"), "maxActiveSearches", 500)
            if "maxActiveSearches" in payload
            else current["maxActiveSearches"]
        )
        country = (
            normalize_radar_destination_country(payload.get("destinationCountry"), "destinationCountry")
            if "destinationCountry" in payload
            else current["destinationCountry"]
        )
        zip_code = (
            normalize_radar_destination_zip(payload.get("destinationZip"), "destinationZip")
            if "destinationZip" in payload
            else current["destinationZip"]
        )
        conn.execute(
            """INSERT INTO radar_preferences
                 (id, monthly_budget_usd, max_active_searches, destination_country, destination_zip, updated_at)
               VALUES ('default', ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 monthly_budget_usd=excluded.monthly_budget_usd,
                 max_active_searches=excluded.max_active_searches,
                 destination_country=excluded.destination_country,
                 destination_zip=excluded.destination_zip,
                 updated_at=excluded.updated_at""",
            (monthly_budget, max_active, country, zip_code, now),
        )
        row = conn.execute("SELECT * FROM radar_preferences WHERE id = 'default'").fetchone()
    return radar_preferences_row(row)


def radar_current_month_bounds() -> tuple[str, str, str]:
    """Devuelve (etiqueta "YYYY-MM", inicio UTC, fin UTC) del mes en curso.

    El mes se cuenta en hora local (`America/Montevideo` por defecto): es
    cuándo el owner compra, no cuándo gira el reloj de UTC.
    """

    local_now = radar_now_local()
    start_local = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_local.month == 12:
        end_local = start_local.replace(year=start_local.year + 1, month=1)
    else:
        end_local = start_local.replace(month=start_local.month + 1)
    label = start_local.strftime("%Y-%m")
    start_utc = start_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = end_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return label, start_utc, end_utc
