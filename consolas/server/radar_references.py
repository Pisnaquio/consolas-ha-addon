"""Collection Radar: catalog price references, item kinds and shipping weights."""

from __future__ import annotations

import json
import re
from typing import Any

from radar.valuation import reference_from_price_range, references_from_console_entry

try:
    from .config import AppConfig
    from .radar_catalog import load_console_catalog
    from .radar_listings import radar_listing_amount
except ImportError:  # Direct execution: `python3 server/app.py`.
    from config import AppConfig
    from radar_catalog import load_console_catalog
    from radar_listings import radar_listing_amount


# La fecha de verificación de un precio del catálogo vive dentro de la nota en
# prosa («consultados el 2026-09-15»), no en un campo propio. Se lee de ahí
# mientras no exista el campo: sin fecha no se puede saber si el precio
# envejeció, y eso cambia cuánta confianza merece.
_FECHA_EN_NOTA = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def radar_catalog_games(config: AppConfig, console_id: str) -> list[dict[str, Any]]:
    """Los juegos de catálogo de una consola, o lista vacía si no hay."""

    if not console_id:
        return []
    path = config.static_dir / "data" / "console-games.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    bucket = ((payload.get("byConsole") or {}).get(console_id) or {}) if isinstance(payload, dict) else {}
    return [entry for entry in bucket.get("juegosCatalogo") or [] if isinstance(entry, dict)]


def radar_game_price_reference(config: AppConfig, console_id: str, game_id: str) -> Any:
    """El precio que el catálogo ya tiene para ese juego, si lo tiene."""

    if not console_id or not game_id:
        return None
    for entry in radar_catalog_games(config, console_id):
        if str(entry.get("id") or "") != game_id:
            continue
        price_range = entry.get("priceRange")
        notes = str((price_range or {}).get("notes") or "")
        found = _FECHA_EN_NOTA.search(notes)
        return reference_from_price_range(
            price_range, entity_id=game_id, observed_at=found.group(1) if found else ""
        )
    return None


# Un bundle de consola se compara contra la consola más sus juegos, y para eso
# hace falta cuánto vale un juego cualquiera de esa consola. Se usa el cuartil
# bajo del precio mínimo del catálogo: los juegos que vienen con una consola
# usada son casi siempre los más comunes, no los que el catálogo destaca.
RADAR_BUNDLE_MIN_CATALOG_GAMES = 5
# Más allá de esto, el número cuenta un lote entero que viene de regalo, y el
# valor de un lote no se estima sumando piezas que nadie eligió.
RADAR_BUNDLE_MAX_GAMES = 10


def radar_bundle_game_value(config: AppConfig, console_id: str) -> dict[str, Any] | None:
    """Cuánto vale, siendo prudentes, un juego cualquiera que viene con la consola.

    `None` cuando el catálogo de esa consola no tiene suficientes precios para
    decirlo: sin dato, el bundle se compara contra la consola sola y la card lo
    avisa, en vez de inventar cuánto valen sus juegos.
    """

    lows = sorted(
        amount
        for amount in (
            radar_listing_amount((entry.get("priceRange") or {}).get("low"))
            for entry in radar_catalog_games(config, console_id)
            if isinstance(entry.get("priceRange"), dict)
        )
        if amount is not None and amount > 0
    )
    if len(lows) < RADAR_BUNDLE_MIN_CATALOG_GAMES:
        return None
    return {"value": lows[len(lows) // 4], "sample": len(lows)}


def radar_entity_references(
    config: AppConfig, entity_type: str, entity_id: str, entity_console_id: str = ""
) -> list[Any]:
    """Referencias de precio de la entidad vinculada, con su procedencia.

    Una búsqueda sin entidad vinculada se queda sin benchmark y lo dice, en vez
    de compararse contra cualquier cosa.

    Un juego se identifica por el par {consoleId, gameId}: el mismo id existe en
    varias plataformas y un God of War de PS2 no vale lo que uno de PS4.
    """

    if entity_type == "game":
        reference = radar_game_price_reference(config, entity_console_id, entity_id)
        return [reference] if reference else []
    if entity_type != "console" or not entity_id:
        return []
    for entry in load_console_catalog(config):
        if str(entry.get("id") or "") == entity_id:
            return references_from_console_entry(entry)
    return []


def radar_expected_item_kind(search_type: str, entity_type: str) -> str:
    """Qué tipo de pieza puede aceptar esta búsqueda, o vacío si acepta cualquiera.

    Un lote busca justamente cosas mezcladas, y una búsqueda de descubrimiento
    existe para traer lo que no estabas buscando: a esas no se les exige nada.
    El resto, cuando declara a qué entidad del catálogo apunta, sí — una
    búsqueda de consolas traía diez juegos sueltos de catorce resultados.
    """

    if search_type in {"lot", "discovery"}:
        return ""
    if entity_type in {"console", "game", "accessory"}:
        return entity_type
    # Una búsqueda de tipo consola persigue consolas aunque nadie haya vinculado
    # la entidad del catálogo: el tipo ya declara la intención. Sin esto, "PS3
    # consola lista para usar" devolvía un NBA Jam y un control, y ambos
    # cruzaban el objetivo de 160 dólares pensado para la consola.
    return "console" if search_type == "console" else ""


def usable_radar_references(references: list[Any], entity_type: str, item_kind: Any) -> list[Any]:
    """Las referencias curadas valen sólo si la publicación es esa entidad.

    Una búsqueda de Dreamcast trae el precio de la consola Dreamcast. Medir con
    esa vara un VMU de 34 dólares lo convertía en "ganga real" y lo ponía
    primero en el feed. Cuando la pieza no es la entidad de la búsqueda se cae
    a las referencias genéricas (las publicaciones pares de la misma corrida),
    que no dependen de qué entidad se buscaba.
    """

    if not entity_type or not item_kind.confident or item_kind.kind == entity_type:
        return references
    return [reference for reference in references if reference.source == "peer-listings"]


def radar_console_shipping_weight(config: AppConfig, entity_id: str, variant: str = "") -> float | None:
    """Peso de envío declarado para esta consola, o `None` si no hay dato.

    Una consola no pesa lo mismo que otra: la PSP son 0,19 kg y una PS3 Fat 5.
    Con un único número para "consola" el courier se equivocaba en las dos
    direcciones — quince veces de más en la PSP, de menos en la PS3 Fat.

    Sólo aplica cuando la búsqueda apunta a una consola concreta del catálogo.
    Sin esa vinculación queda la estimación genérica, que sigue siendo el
    supuesto prudente de siempre.
    """

    if not entity_id:
        return None
    for entry in load_console_catalog(config):
        if str(entry.get("id") or "") != entity_id:
            continue
        return radar_console_weight_entry(entry.get("pesoEnvio"), variant)
    return None


def radar_console_weight_declaration(config: AppConfig, entity_id: str) -> Any:
    """La declaración de peso de esta consola, sin resolver todavía la revisión.

    Se resuelve una vez por corrida y no por publicación: la revisión la decide
    después el título de cada una.
    """

    if not entity_id:
        return None
    for entry in load_console_catalog(config):
        if str(entry.get("id") or "") == entity_id:
            return entry.get("pesoEnvio")
    return None


def radar_console_weight_entry(peso: Any, variant: str = "") -> float | None:
    """El peso declarado, de la revisión que corresponda.

    Sin revisión declarada en el título queda el peso general de la consola, que
    es el de la variante más pesada: subestimar el courier sorprende con un
    costo mayor al anunciado, y sobreestimarlo sólo posterga una compra.
    """

    if not isinstance(peso, dict):
        return None
    candidate = (peso.get("variantes") or {}).get(variant, {}).get("kg") if variant else None
    if not isinstance(candidate, (int, float)) or candidate <= 0:
        candidate = peso.get("kg")
    return float(candidate) if isinstance(candidate, (int, float)) and candidate > 0 else None


def target_applies_to(item_kind: Any, entity_type: str) -> bool:
    """Si el objetivo de esta búsqueda puede medirse contra esta pieza.

    El objetivo pertenece a la entidad que la búsqueda persigue: 145 dólares es
    lo que pagarías por una PS2, no por un juego de PS2. Sin esta regla, todo
    juego barato que se cuela en una búsqueda de consola cruza el objetivo al
    instante y dispara un aviso — y de los seis primeros que cruzaban en la base
    real, cinco eran juegos o un control metidos en búsquedas de consola.

    Se exige que la pieza parezca del tipo buscado, incluso cuando la
    clasificación es una deducción. Perder el aviso de una consola descrita sin
    la palabra consola es mucho más barato que avisar por cada juego de 40
    dólares: interrumpir de más entrena a ignorar los avisos.
    """

    # Un "elegí cuál querés" publica el precio de su opción más barata: 1,99 en
    # una lista donde lo que buscás sale 40. Cruzaría cualquier objetivo sin
    # que eso signifique nada.
    if item_kind.variable_price:
        return False
    if not entity_type:
        return True
    return item_kind.kind == entity_type
