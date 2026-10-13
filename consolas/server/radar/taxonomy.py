"""Los cinco objetivos canónicos de compra, y a cuál pertenece cada publicación.

El dueño no busca «resultados de la búsqueda de N64»: busca, para cada consola,
cinco cosas distintas que no se comparan entre sí:

1. lotes de juegos — se juzgan por lo que cuesta cada pieza;
2. juegos loose — el medio solo, sin caja ni manual;
3. juegos CIB — el juego con su caja original y su manual;
4. consolas loose — el hardware sin la caja retail original, traiga o no los
   accesorios para jugar;
5. consolas CIB — la consola en su caja retail original con su contenido
   principal.

La taxonomía vive acá, una sola vez, y la UI la recibe del servidor: la
pantalla no sabe qué es un CIB ni qué búsquedas existen, sólo dibuja lo que le
llega. Una búsqueda de consola alimenta los dos objetivos de consola y una de
deseados los dos de juegos: el objetivo es de la publicación, no de la
búsqueda.

Lo que el título no permite afirmar no se afirma. Un CIB es siempre lo que el
vendedor *declara*: el radar no mira fotos, así que hasta que el dueño
comprueba la caja y el manual queda en «Requiere revisión» (lo decide
`valuation`). Una copia con caja y sin manual no es CIB ni loose: va con los
CIB, aparte, sin veredicto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .classify import CONSOLE_CIB_MARKERS, REPRO_PACKAGING_MARKERS, without_negated_completeness
from .platforms import PROFILES, normalize_platform


@dataclass(frozen=True, slots=True)
class Objective:
    id: str
    label: str
    # Qué pieza agrupa: `lot`, `game`, `console` u `other`.
    kind: str
    # Contra qué completitud se compara: `lot` (por pieza), `loose` o `cib`.
    completeness: str
    canonical: bool = True

    def to_dict(self, order: int) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "completeness": self.completeness,
            "canonical": self.canonical,
            "order": order,
        }


OBJECTIVES: tuple[Objective, ...] = (
    Objective("game-lot", "Lotes de juegos", "lot", "lot"),
    Objective("game-loose", "Juegos loose", "game", "loose"),
    Objective("game-cib", "Juegos CIB", "game", "cib"),
    Objective("console-loose", "Consolas loose", "console", "loose"),
    Objective("console-cib", "Consolas CIB", "console", "cib"),
)
# Lo que no es ninguno de los cinco —un accesorio que trajo una búsqueda de
# descubrimiento— no desaparece: queda aparte y sólo se muestra si existe.
OTHER = Objective("other", "Otras piezas", "other", "", canonical=False)
OBJECTIVES_BY_ID = {objective.id: objective for objective in (*OBJECTIVES, OTHER)}

# Dentro de un objetivo, lo que no cumple su definición no se mezcla con lo que
# sí: va a una sección aparte, sin veredicto.
SECTIONS = {
    "primary": {"label": "", "detail": ""},
    "unconfirmed-quantity": {
        "label": "Cantidad sin confirmar",
        "detail": "No dicen cuántos juegos traen, o lo dicen junto con otra cosa: no cumplen la búsqueda hasta confirmarlo.",
    },
    "incomplete": {
        "label": "No llega a CIB",
        "detail": "Traen caja o manual, no los dos: no son CIB ni loose, y no se comparan con ninguno.",
    },
}


def taxonomy_payload() -> dict[str, Any]:
    """Lo que la UI necesita para dibujar los objetivos sin saber cuáles son."""
    return {
        "objectives": [objective.to_dict(index) for index, objective in enumerate((*OBJECTIVES, OTHER))],
        "sections": {key: dict(value) for key, value in SECTIONS.items() if key != "primary"},
    }


# --- Caja y manual de un juego -------------------------------------------------
#
# Qué cuenta como caja depende del formato, no de la consola: en un disco o una
# tarjeta (PS2, DS, 3DS) la «case» es la caja retail; en un cartucho retro, «w/
# case» es un estuche protector y la caja es «box».

_BOX_WORDS_ANY_MEDIUM = (
    r"\bcib\b",
    r"\bcomplete\s+(?:in|with)\s+(?:the\s+)?(?:original\s+)?box\b",
    r"\bbox\s+(?:and|&|\+)\s+manual\b",
    r"\bmanual\s+(?:and|&|\+)\s+box\b",
    r"\b(?:with|w)\s+(?:the\s+)?(?:original\s+)?box\b",
    r"\bin\s+(?:the\s+)?(?:original\s+)?box\b",
    r"\bboxed\b",
    r"\boriginal\s+box\b",
    r"\bnew\s+in\s+box\b",
    r"\bnib\b",
    r"\bsealed\b",
    r"\bcomplete\b(?!\s+(?:game|cart|cartridge|disc)\s+only)",
)
_CASE_WORDS = (
    r"\b(?:with|w|in)\s+(?:the\s+)?(?:original\s+)?case\b",
    r"\boriginal\s+case\b",
    r"\bcase\s+(?:and|&|\+)\s+manual\b",
    r"\bmanual\s+(?:and|&|\+)\s+case\b",
)
_BOX_ABSENT = (
    r"\bno\s+(?:box|case)\b",
    r"\bwithout\s+(?:the\s+)?(?:box|case)\b",
    r"\b(?:box|case)\s+(?:missing|not\s+included)\b",
    r"\bmissing\s+(?:the\s+)?(?:box|case)\b",
    r"\bloose\b",
    r"\b(?:cart|cartridge|disc|disk|game|card)\s+only\b",
)
_MANUAL_PRESENT = (
    r"\bmanuals?\b",
    r"\bbooklets?\b",
    r"\bcib\b",
    r"\bsealed\b",
    r"\bcomplete\b(?!\s+(?:game|cart|cartridge|disc)\s+only)",
)
_MANUAL_ABSENT = (
    r"\bno\s+(?:manual|booklet)s?\b",
    r"\bwithout\s+(?:the\s+)?(?:manual|booklet)s?\b",
    r"\bmissing\s+(?:the\s+)?(?:manual|booklet)s?\b",
    r"\b(?:manual|booklet)s?\s+(?:missing|not\s+included)\b",
    r"\bincomplete\b",
)
_INSERTS = (r"\binserts?\b", r"\bposters?\b", r"\bmaps?\b", r"\bregistration\s+card\b")


def _hit(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _norm(title: str) -> str:
    return f" {normalize_platform(title)} "


def _without_repro_packaging(text: str) -> tuple[str, bool]:
    stripped, count = re.subn("|".join(REPRO_PACKAGING_MARKERS), " ", text)
    return stripped, count > 0


@dataclass(frozen=True, slots=True)
class ObjectiveAssignment:
    """A qué objetivo pertenece una publicación y qué tan firme es eso.

    `status`:
    - `lot`: un lote (su cantidad la juzga el matcher);
    - `loose`: declara no traer caja ni manual;
    - `undeclared`: no dice nada de caja ni manual, se toma como loose;
    - `declared`: declara CIB (caja original y manual) sin que nadie lo haya
      comprobado;
    - `incomplete`: trae caja o manual, no los dos;
    - `other`: no es un juego, un lote ni una consola.
    """

    objective: str
    status: str
    box: str = ""
    manual: str = ""
    inserts: bool = False
    repro_packaging: bool = False
    note: str = ""

    @property
    def section(self) -> str:
        return "incomplete" if self.status == "incomplete" else "primary"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.objective,
            "status": self.status,
            "box": self.box,
            "manual": self.manual,
            "inserts": self.inserts,
            "reproPackaging": self.repro_packaging,
            "note": self.note,
            "label": OBJECTIVES_BY_ID[self.objective].label,
        }


def console_has_retail_box(title: str) -> tuple[bool, bool]:
    """`(caja retail original declarada, caja repro declarada)`."""
    text, repro = _without_repro_packaging(_norm(title))
    text, _ = without_negated_completeness(text)
    return (not repro and _hit(CONSOLE_CIB_MARKERS, text)), repro


def assign_objective(
    title: str,
    *,
    search_type: str = "",
    item_kind: str = "",
    item_confident: bool = False,
    platform: str = "",
) -> ObjectiveAssignment:
    """El objetivo canónico de una publicación, leído de su título.

    Decide por la pieza y por el formato de la plataforma, nunca por el nombre
    de una consola. Una consola con caja repro es una consola loose; un bundle
    de consola es loose o CIB según su caja; un lote es un lote aunque traiga
    estuches.
    """

    if item_kind == "accessory":
        return ObjectiveAssignment("other", "other")
    if search_type == "lot" or item_kind == "lot":
        return ObjectiveAssignment("game-lot", "lot")

    is_console = item_kind == "console" or (search_type == "console" and not item_confident)
    if is_console:
        boxed, repro = console_has_retail_box(title)
        if boxed:
            return ObjectiveAssignment(
                "console-cib",
                "declared",
                box="declared",
                note="CIB declarado por el vendedor: confirmá caja retail original y contenido en las fotos.",
            )
        return ObjectiveAssignment(
            "console-loose",
            "loose",
            box="repro" if repro else "",
            repro_packaging=repro,
            note="Caja repro: no cuenta como CIB, se compara como loose." if repro else "",
        )

    text, repro = _without_repro_packaging(_norm(title))
    # «No CIB» dice que no está completo, no que traiga caja y manual.
    text, negated = without_negated_completeness(text)
    profile = PROFILES.get(platform)
    box_patterns = _BOX_WORDS_ANY_MEDIUM + (() if profile and profile.medium == "cartridge" else _CASE_WORDS)
    box = "absent" if _hit(_BOX_ABSENT, text) else ("present" if _hit(box_patterns, text) else "")
    manual = "absent" if _hit(_MANUAL_ABSENT, text) else ("present" if _hit(_MANUAL_PRESENT, text) else "")
    inserts = _hit(_INSERTS, text)
    repro_note = " La caja que trae es repro: no cuenta." if repro else ""
    if box or manual:
        shape_note = ""
    elif negated:
        shape_note = "Dice que no es CIB y no aclara qué trae: se compara como loose."
    else:
        shape_note = "No dice si trae caja ni manual: se compara como loose."

    if box == "present" and manual == "present":
        return ObjectiveAssignment(
            "game-cib",
            "declared",
            box="present",
            manual="present",
            inserts=inserts,
            note=(
                "CIB declarado por el vendedor: confirmá caja original y manual en las fotos"
                + (" (y los inserts que anuncia)." if inserts else "; los inserts no se dan por incluidos.")
            ),
        )
    if box == "present" or manual == "present":
        missing = "manual" if box == "present" else "caja"
        return ObjectiveAssignment(
            "game-cib",
            "incomplete",
            box=box,
            manual=manual,
            inserts=inserts,
            note=f"Trae {'caja' if box == 'present' else 'manual'} pero no dice {missing}: no es CIB ni loose.",
        )
    return ObjectiveAssignment(
        "game-loose",
        "loose" if box == "absent" or manual == "absent" else "undeclared",
        box=box,
        manual=manual,
        repro_packaging=repro,
        note=shape_note + repro_note,
    )


__all__ = [
    "OBJECTIVES",
    "OBJECTIVES_BY_ID",
    "OTHER",
    "Objective",
    "ObjectiveAssignment",
    "SECTIONS",
    "assign_objective",
    "console_has_retail_box",
    "taxonomy_payload",
]
