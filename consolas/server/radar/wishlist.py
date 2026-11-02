"""Nacho's Wishlist del lado del radar: qué es un deseo real y qué no.

La única fuente de deseos explícitos es
`detailEditsById["wishlist:personal"].wishesById`. `loQuiero` y
`keepInWishlist` son el sistema viejo de recomendaciones: la calibración del
radar los usó para marcar el top de cada consola, y de ahí salieron 201 de los
213 títulos de las búsquedas «Deseados …». Ninguno de los dos es un deseo del
dueño, así que acá no cuentan.

Cuatro intenciones, que el radar trata distinto:

- `wishlist`: un juego que el dueño agregó a Nacho's Wishlist (o una pieza de
  una colección temática que agregó entera). Puede interrumpir con un mail.
- `console`: una consola que falta. Puede interrumpir.
- `lot`: un lote elegido. Puede interrumpir.
- `manual`: una búsqueda escrita a mano que no apunta a nada del catálogo.
  Corre normalmente, pero no interrumpe: no se sabe qué deseo cumple.
- `recommendation`: juegos del catálogo que no están en la wishlist
  —«Deseados SNES» armado con `loQuiero`, los 20 CIB de PS3—. Se ve en la
  app, nunca interrumpe y corre con menos cupo.
- `discovery`: búsquedas para descubrir; tampoco interrumpen.

Funciones puras: no leen la base ni archivos.
"""

from __future__ import annotations

from typing import Any, Iterable

PERSONAL_WISHLIST_KEY = "wishlist:personal"

INTENTS = ("wishlist", "console", "lot", "manual", "recommendation", "discovery")
# Qué intenciones pueden mandar un mail excepcional.
ALERTING_INTENTS = frozenset({"wishlist", "console", "lot"})
# Orden en el que se reparte el presupuesto de consultas de una corrida.
INTENT_PRIORITY = {intent: index for index, intent in enumerate(INTENTS)}


def personal_wishes(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Los deseos de Nacho's Wishlist, tal como están persistidos."""
    edits = ((state or {}).get("user") or {}).get("detailEditsById") or {}
    bucket = edits.get(PERSONAL_WISHLIST_KEY) if isinstance(edits, dict) else None
    wishes = bucket.get("wishesById") if isinstance(bucket, dict) else None
    return {str(key): value for key, value in (wishes or {}).items() if isinstance(value, dict)}


def wished_games(state: dict[str, Any] | None) -> list[dict[str, str]]:
    """Los juegos con nombre propio de la wishlist, sin repetir, en orden.

    Una colección temática (`franchise`) no entra acá: sus piezas salen del
    tracker de franquicias y se cuentan aparte.
    """

    games: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for wish_id, wish in personal_wishes(state).items():
        if str(wish.get("kind") or "games") != "games":
            continue
        for item in wish.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("consoleId") or ""), str(item.get("gameId") or ""))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            games.append({"consoleId": key[0], "gameId": key[1], "wishId": wish_id,
                          "wishTitle": str(wish.get("title") or "")})
    return games


def wished_franchise_releases(
    state: dict[str, Any] | None, franchises: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Las piezas físicas principales de cada colección temática deseada."""
    releases: list[dict[str, str]] = []
    for wish_id, wish in personal_wishes(state).items():
        if str(wish.get("kind") or "") != "franchise":
            continue
        franchise = (franchises or {}).get(str(wish.get("franchiseId") or "")) or {}
        for release in franchise.get("releases") or []:
            if not isinstance(release, dict) or release.get("isPrimaryPhysicalTarget") is not True:
                continue
            ref = release.get("catalogRef") or {}
            releases.append({
                "releaseId": str(release.get("id") or ""),
                "consoleId": str(ref.get("consoleId") or release.get("platformId") or ""),
                "gameId": str(ref.get("gameId") or ""),
                "wishId": wish_id,
            })
    return releases


def wished_keys(state: dict[str, Any] | None, franchises: dict[str, Any] | None = None) -> set[tuple[str, str]]:
    """Pares (consola, juego o lanzamiento) que cuentan como deseo real."""
    keys = {(game["consoleId"], game["gameId"]) for game in wished_games(state)}
    for release in wished_franchise_releases(state, franchises):
        if release["gameId"]:
            keys.add((release["consoleId"], release["gameId"]))
        if release["releaseId"]:
            keys.add((release["consoleId"], release["releaseId"]))
    return keys


def search_intent(
    search_type: str,
    entity_type: str,
    entity_console_id: str,
    entity_id: str,
    wished: set[tuple[str, str]],
    target: dict[str, Any] | None = None,
) -> str:
    """Qué persigue esta coincidencia: deseo real, consola, lote o recomendación.

    En una búsqueda de varios títulos manda el título que encontró la
    publicación (`target`), no la búsqueda entera: «Deseados SNES» puede
    contener un deseo real entre veinte recomendaciones.
    """

    if search_type == "lot":
        return "lot"
    if search_type == "discovery":
        return "discovery"
    if entity_type == "console" or search_type == "console":
        return "console"
    if target and target.get("gameId"):
        key = (str(target.get("consoleId") or entity_console_id or ""), str(target["gameId"]))
        return "wishlist" if key in wished else "recommendation"
    if entity_type == "game" and entity_id:
        return "wishlist" if (str(entity_console_id or ""), str(entity_id)) in wished else "recommendation"
    return "manual"


def search_row_intent(search: dict[str, Any], wished: set[tuple[str, str]], *, strict: bool = False) -> str:
    """La intención de una búsqueda entera.

    Por defecto, la mejor de sus títulos: «Deseados SNES» con un deseo real
    adentro cuenta como wishlist para la cobertura. Con `strict`, sólo si todos
    sus títulos son deseos: para repartir el presupuesto, una búsqueda de 49
    consultas con dos deseos y 47 recomendaciones es una recomendación.
    """
    search_type = str(search.get("searchType") or search.get("search_type") or "")
    entity_type = str(search.get("entityType") or search.get("entity_type") or "")
    console_id = str(search.get("entityConsoleId") or search.get("entity_console_id") or "")
    entity_id = str(search.get("entityId") or search.get("entity_id") or "")
    criteria = search.get("criteria") or {}
    targets = [target for target in criteria.get("titleTargets") or [] if isinstance(target, dict)
               and target.get("gameId")]
    intent = search_intent(search_type, entity_type, console_id, entity_id, wished)
    if intent not in {"recommendation", "manual"} or not targets:
        return intent
    hits = [
        search_intent(search_type, entity_type, console_id, entity_id, wished, target) == "wishlist"
        for target in targets
    ]
    if hits and (all(hits) if strict else any(hits)):
        return "wishlist"
    return "recommendation"


def _search_covers(search: dict[str, Any]) -> Iterable[tuple[str, str]]:
    console_id = str(search.get("entityConsoleId") or "")
    if str(search.get("entityType") or "") == "game" and search.get("entityId"):
        yield (console_id, str(search["entityId"]))
    for target in (search.get("criteria") or {}).get("titleTargets") or []:
        if isinstance(target, dict) and target.get("gameId"):
            yield (str(target.get("consoleId") or console_id), str(target["gameId"]))


def wishlist_coverage(
    state: dict[str, Any] | None,
    searches: list[dict[str, Any]],
    game_names: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Cuántos juegos explícitos de la wishlist tienen una búsqueda activa.

    Sólo cuentan las búsquedas activas; una pausada o en borrador no busca.
    Los faltantes se informan con nombre, para poder crearlos.
    """

    active = [
        search for search in searches
        if str(search.get("status") or "") == "active" and search.get("enabled", True) is not False
    ]
    covered_keys = {key for search in active for key in _search_covers(search)}
    games = wished_games(state)
    covered: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for game in games:
        name = str(((game_names or {}).get(game["consoleId"]) or {}).get(game["gameId"]) or game["gameId"])
        entry = {**game, "name": name}
        (covered if (game["consoleId"], game["gameId"]) in covered_keys else missing).append(entry)
    return {
        "total": len(games),
        "covered": len(covered),
        "missing": missing,
        "coveredGames": covered,
    }


__all__ = [
    "ALERTING_INTENTS",
    "INTENTS",
    "INTENT_PRIORITY",
    "PERSONAL_WISHLIST_KEY",
    "personal_wishes",
    "search_intent",
    "search_row_intent",
    "wished_franchise_releases",
    "wished_games",
    "wished_keys",
    "wishlist_coverage",
]
