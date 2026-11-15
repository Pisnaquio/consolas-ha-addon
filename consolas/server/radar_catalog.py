"""Collection Radar: console catalog, franchises and wished keys from the state."""

from __future__ import annotations

import json
from typing import Any

from radar.wishlist import wished_keys

try:
    from .config import AppConfig
    from .state import read_state
except ImportError:  # Direct execution: `python3 server/app.py`.
    from config import AppConfig
    from state import read_state


def load_console_catalog(config: AppConfig) -> list[dict[str, Any]]:
    """Catálogo base de consolas. Es referencia: nunca decide propiedad."""
    path = config.static_dir / "data" / "consoles.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("consolas") if isinstance(payload, dict) else None
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def load_franchise_collections(config: AppConfig) -> dict[str, Any]:
    """Colecciones temáticas del catálogo versionado, por id."""
    path = config.static_dir / "data" / "franchise-collections.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    franchises = payload.get("franchises") if isinstance(payload, dict) else None
    return franchises if isinstance(franchises, dict) else {}


def radar_wished_keys(config: AppConfig) -> set[tuple[str, str]]:
    """Qué cuenta como deseo real: sólo Nacho's Wishlist (ver radar/wishlist.py)."""
    return wished_keys(read_state(config), load_franchise_collections(config))


def load_game_names_by_console(config: AppConfig) -> dict[str, dict[str, str]]:
    """Nombres de los juegos del catálogo, por consola.

    Un juego de catálogo marcado "lo quiero" guarda sólo el patch —`loQuiero`,
    `keepInWishlist`— porque el nombre ya vive en el catálogo versionado. Sin
    resolverlo contra el catálogo, un deseo de catálogo es indistinguible de
    una entrada vacía, y no se puede ni nombrar ni buscar.
    """

    path = config.static_dir / "data" / "console-games.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    by_console = payload.get("byConsole") if isinstance(payload, dict) else None
    names: dict[str, dict[str, str]] = {}
    for console_id, bucket in (by_console or {}).items():
        entries = (bucket or {}).get("juegosCatalogo") if isinstance(bucket, dict) else None
        names[str(console_id)] = {
            str(entry.get("id")): str(entry.get("nombre") or "")
            for entry in entries or []
            if isinstance(entry, dict) and entry.get("id")
        }
    return names
