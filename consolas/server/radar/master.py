"""Master de colección: propone búsquedas leyendo el estado real del usuario.

El Master no es una consulta gigante sino un portafolio de sub-búsquedas
derivadas de lo que la colección efectivamente tiene y quiere (PRD §7.1).

Dos reglas mandan sobre todo lo demás:

1. **Propone, no activa.** Toda propuesta nace `draft`. Una búsqueda sólo corre
   con consentimiento explícito del usuario.
2. **No inventa propiedad.** Que un juego esté en el catálogo base no significa
   que el usuario lo tenga, ni que lo quiera. La propiedad sale del estado
   persistido y de ningún otro lado.

Precedencia al elegir qué proponer, de mayor a menor:

1. propiedad persistida — lo que ya está registrado no se busca de nuevo;
2. `loQuiero=true` y chases explícitos;
3. prioridad asignada por el usuario;
4. recomendaciones `keepInWishlist` sin prioridad, que son las más débiles:
   "sin prioridad" no quiere decir "prioridad baja".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Una consola sin registrar es candidata; una registrada nunca se propone.
PRIORITY_ORDER = ("alta", "media-alta", "media", "baja")

# Guardrails monetarios del PRD §24, en USD.
CONSOLE_MAX_PRICE = 300.0
LOT_MAX_PRICE = 250.0
GAME_MAX_PRICE = 100.0

# Términos que descartan una publicación rota en cualquier búsqueda del Master.
COMMON_EXCLUDES = ["parts", "repair", "as-is", "broken", "not working"]
LOT_EXCLUDES = COMMON_EXCLUDES + ["case only", "manual only", "demo"]


@dataclass(slots=True)
class MasterProposal:
    """Una sub-búsqueda propuesta, con el motivo por el que existe."""

    key: str
    name: str
    search_type: str
    priority: str
    platform: str
    entity_type: str
    entity_id: str
    rationale: str
    criteria: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "searchType": self.search_type,
            "priority": self.priority,
            "platform": self.platform,
            "entityType": self.entity_type,
            "entityId": self.entity_id,
            "rationale": self.rationale,
            "criteria": dict(self.criteria),
        }


def normalize_ownership(value: Any, owned_flag: Any = None) -> str:
    text = str(value or "").strip().lower()
    if text in {"physical", "digital", "both", "none"}:
        return text
    return "physical" if owned_flag is True else "none"


def console_is_owned(console_id: str, overrides: dict[str, Any], catalog_entry: dict[str, Any]) -> bool:
    """La propiedad sale del estado persistido; el catálogo sólo es referencia."""
    override = overrides.get(console_id)
    if isinstance(override, dict) and isinstance(override.get("tengo"), bool):
        return override["tengo"]
    return catalog_entry.get("tengo") is True


def wanted_consoles(state: dict[str, Any], consoles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overrides = state.get("user", {}).get("overridesById") or {}
    wanted: list[dict[str, Any]] = []
    for entry in consoles:
        console_id = str(entry.get("id") or "")
        if not console_id:
            continue
        if console_is_owned(console_id, overrides, entry):
            continue
        wanted.append(entry)
    return wanted


def wanted_games(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Juegos que el usuario marcó explícitamente, no recomendaciones del catálogo.

    `keepInWishlist` sin `loQuiero` es una recomendación conservada, no un
    objetivo de compra: entra con la prioridad más baja y nunca por delante de
    un chase explícito.
    """

    detail_edits = state.get("user", {}).get("detailEditsById") or {}
    games: list[dict[str, Any]] = []
    for console_id, bucket in detail_edits.items():
        if not isinstance(bucket, dict):
            continue
        for source in ("gameEditsById", "manualGamesById"):
            entries = bucket.get(source)
            if not isinstance(entries, dict):
                continue
            for game_id, game in entries.items():
                if not isinstance(game, dict):
                    continue
                if normalize_ownership(game.get("ownershipType"), game.get("loTengo")) != "none":
                    continue  # ya lo tiene: no se busca
                explicit = game.get("loQuiero") is True
                kept = game.get("keepInWishlist") is True
                if not explicit and not kept:
                    continue
                name = str(game.get("nombre") or game.get("titulo") or "").strip()
                if not name:
                    continue
                games.append(
                    {
                        "consoleId": str(console_id),
                        "gameId": str(game_id),
                        "name": name,
                        "explicit": explicit,
                        "priority": str(game.get("prioridad") or ""),
                    }
                )
    return games


def rank_game(game: dict[str, Any]) -> tuple[int, int, str]:
    """Orden de precedencia: chase explícito, después prioridad, después el resto."""
    explicit_rank = 0 if game["explicit"] else 1
    priority = game.get("priority") or ""
    priority_rank = PRIORITY_ORDER.index(priority) if priority in PRIORITY_ORDER else len(PRIORITY_ORDER)
    return (explicit_rank, priority_rank, game["name"].lower())


def console_proposal(entry: dict[str, Any]) -> MasterProposal:
    console_id = str(entry["id"])
    label = str(entry.get("nombre") or console_id)
    return MasterProposal(
        key=f"console-{console_id}",
        name=f"{label} lista para usar",
        search_type="console",
        priority="media-alta",
        platform=label,
        entity_type="console",
        entity_id=console_id,
        rationale=f"{label} está en tu lista y todavía no la registraste.",
        criteria={
            "includeTerms": [label],
            "excludeTerms": list(COMMON_EXCLUDES),
            "tested": "required",
            "originalParts": "preferred",
            "maxItemPrice": CONSOLE_MAX_PRICE,
        },
    )


def lot_proposal(entry: dict[str, Any]) -> MasterProposal:
    console_id = str(entry["id"])
    label = str(entry.get("nombre") or console_id)
    return MasterProposal(
        key=f"lot-{console_id}",
        name=f"Lote físico de {label}",
        search_type="lot",
        priority="media",
        platform=label,
        entity_type="console",
        entity_id=console_id,
        rationale=f"Un lote puede sumar varios juegos de {label} por poco dinero.",
        criteria={
            "includeTerms": [f"{label} lot"],
            "excludeTerms": list(LOT_EXCLUDES),
            "minLotSize": 6,
            "maxItemPrice": LOT_MAX_PRICE,
        },
    )


def game_proposal(game: dict[str, Any]) -> MasterProposal:
    return MasterProposal(
        key=f"game-{game['consoleId']}-{game['gameId']}",
        name=game["name"],
        search_type="chase",
        priority=game.get("priority") or ("media-alta" if game["explicit"] else "baja"),
        platform="",
        entity_type="game",
        entity_id=game["gameId"],
        rationale=(
            "Lo marcaste como que lo querés."
            if game["explicit"]
            else "Quedó en tu wishlist como recomendación, sin prioridad asignada."
        ),
        criteria={
            "includeTerms": [game["name"]],
            "excludeTerms": list(COMMON_EXCLUDES),
            "maxItemPrice": GAME_MAX_PRICE,
        },
    )


def propose_master_searches(
    state: dict[str, Any],
    consoles: list[dict[str, Any]],
    *,
    max_consoles: int = 4,
    max_lots: int = 2,
    max_games: int = 4,
) -> list[dict[str, Any]]:
    """Portafolio de propuestas derivado del estado real, acotado por cupos.

    Los cupos existen para que el Master proponga un puñado accionable y no
    cuatrocientas búsquedas: la wishlist visible tiene cientos de items y
    tratarlos como objetivos equivalentes sería ruido, no ayuda.
    """

    overrides = state.get("user", {}).get("overridesById") or {}
    proposals: list[MasterProposal] = []

    wanted = wanted_consoles(state, consoles)
    for entry in wanted[:max_consoles]:
        proposals.append(console_proposal(entry))

    # Los lotes se proponen sobre consolas que el usuario **sí** tiene: ahí un
    # lote agranda una biblioteca existente en vez de comprar a ciegas.
    owned = [
        entry
        for entry in consoles
        if str(entry.get("id") or "") and console_is_owned(str(entry["id"]), overrides, entry)
    ]
    for entry in owned[:max_lots]:
        proposals.append(lot_proposal(entry))

    for game in sorted(wanted_games(state), key=rank_game)[:max_games]:
        proposals.append(game_proposal(game))

    return [proposal.to_dict() for proposal in proposals]


__all__ = [
    "MasterProposal",
    "console_is_owned",
    "propose_master_searches",
    "rank_game",
    "wanted_consoles",
    "wanted_games",
]
