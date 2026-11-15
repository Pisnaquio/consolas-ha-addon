"""Collection Radar: wishlist coverage and master search regeneration."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from radar.master import propose_master_searches
from radar.wishlist import wishlist_coverage

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import RADAR_DEFAULT_SOURCE, _RADAR_LOCK
    from .database import connect_db
    from .radar_catalog import load_console_catalog, load_game_names_by_console
    from .radar_criteria import build_radar_search_query, normalize_radar_criteria
    from .radar_preferences import RADAR_MAX_PENDING_DRAFTS
    from .radar_purchases import radar_targeted_entities
    from .radar_searches import list_radar_searches
    from .state import read_state
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import RADAR_DEFAULT_SOURCE, _RADAR_LOCK
    from database import connect_db
    from radar_catalog import load_console_catalog, load_game_names_by_console
    from radar_criteria import build_radar_search_query, normalize_radar_criteria
    from radar_preferences import RADAR_MAX_PENDING_DRAFTS
    from radar_purchases import radar_targeted_entities
    from radar_searches import list_radar_searches
    from state import read_state


def radar_wishlist_coverage(config: AppConfig) -> dict[str, Any]:
    """Cuántos juegos de Nacho's Wishlist tienen una búsqueda activa, y cuáles no."""
    searches = list_radar_searches(config)["items"]
    return wishlist_coverage(read_state(config), searches, load_game_names_by_console(config))


def radar_master_proposals(config: AppConfig) -> list[dict[str, Any]]:
    # Lo que ya tiene búsqueda no se vuelve a proponer: sin esto el Master
    # devolvía siempre el mismo puñado y una segunda tanda no agregaba nada.
    covered_consoles, covered_lots, covered_games = radar_targeted_entities(config, include_deleted=True)
    return propose_master_searches(
        read_state(config),
        load_console_catalog(config),
        game_names=load_game_names_by_console(config),
        covered_consoles=covered_consoles,
        covered_lots=covered_lots,
        covered_games=covered_games,
    )


def master_search_id(key: str) -> str:
    """Id estable por propuesta: regenerar no duplica ni pisa lo que ya existe."""
    return f"master-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def regenerate_radar_master(config: AppConfig) -> dict[str, Any]:
    """Escribe las propuestas del Master como borradores, sin activar ninguna.

    Es idempotente y conservador: una propuesta que el usuario ya tocó —la
    activó, la pausó, la archivó, la editó o la borró— no se vuelve a crear ni
    se sobrescribe. El Master sugiere una vez; la decisión queda del lado del
    usuario.
    """

    proposals = radar_master_proposals(config)
    now = utc_now()
    created: list[str] = []
    skipped: list[str] = []

    with _RADAR_LOCK, connect_db(config) as conn:
        # El cupo del Master era por tanda, no global: pedirle varias seguidas
        # apilaba borradores sin techo, y aceptarlos en bloque fue como se
        # llegó a ochenta búsquedas activas. Mientras haya una pila sin revisar,
        # no se propone más — el Master sugiere, no acumula.
        pending = int(
            conn.execute(
                "SELECT COUNT(*) FROM radar_searches "
                "WHERE status = 'draft' AND origin = 'master' AND deleted_at IS NULL"
            ).fetchone()[0]
        )
        if pending >= RADAR_MAX_PENDING_DRAFTS:
            return {
                "ok": True,
                "proposed": len(proposals),
                "created": [],
                "skipped": [],
                "pendingDrafts": pending,
                "note": (
                    f"Ya hay {pending} propuestas del Master sin revisar. "
                    "Aceptá o descartá esas antes de pedir más."
                ),
            }
        for proposal in proposals:
            search_id = master_search_id(str(proposal["key"]))
            existing = conn.execute("SELECT status, deleted_at FROM radar_searches WHERE id = ?", (search_id,)).fetchone()
            if existing is not None:
                skipped.append(search_id)
                continue
            criteria = normalize_radar_criteria(proposal.get("criteria"))
            conn.execute(
                """INSERT INTO radar_searches (
                     id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
                     entity_console_id, search_query, criteria_json, sources_json, slots_json, notes,
                     created_at, updated_at
                   ) VALUES (?, ?, ?, 'draft', 'master', ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)""",
                (
                    search_id, str(proposal["name"]), str(proposal["searchType"]), str(proposal["priority"]),
                    str(proposal["platform"]), str(proposal["entityType"]), str(proposal["entityId"]),
                    str(proposal.get("entityConsoleId") or ""),
                    build_radar_search_query(str(proposal["name"]), str(proposal["platform"]), criteria),
                    json.dumps(criteria, ensure_ascii=False, separators=(",", ":")),
                    json.dumps([RADAR_DEFAULT_SOURCE], ensure_ascii=False, separators=(",", ":")),
                    str(proposal["rationale"]), now, now,
                ),
            )
            created.append(search_id)

    return {
        "ok": True,
        "proposed": len(proposals),
        "created": len(created),
        "skipped": len(skipped),
        "proposals": proposals,
    }
