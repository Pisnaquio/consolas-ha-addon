"""Qué es la pieza que publica un vendedor, leído de su propio título.

Hasta ahora el radar asumía que toda publicación que encontraba una búsqueda
era del mismo tipo que la entidad de esa búsqueda. Eso hacía dos daños
medibles: un juego capturado por una búsqueda de consola se costeaba con el
peso de una consola (3 kg de courier sobre un juego de 0,15 kg), y se comparaba
su precio contra el de la consola, así que un accesorio barato parecía una
ganga histórica.

Acá se decide por el título, que es lo único que describe la pieza real. El
título es texto externo no confiable: se usa sólo como dato para clasificar,
nunca como instrucción.

La clasificación distingue dos niveles de certeza a propósito. Con marcadores
explícitos la respuesta es firme y sirve para rechazar una publicación que no
corresponde. Cuando sólo queda un título sin marcadores, la respuesta es
"probablemente un juego": alcanza para estimar un peso mucho mejor que el
anterior, pero no para descartar nada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# El nombre de la plataforma se saca antes de buscar nada más: "Super Nintendo
# Entertainment System" contiene "system", y sin esto cualquier cartucho de
# SNES pasaría por consola.
PLATFORM_NAMES = (
    r"super\s+nintendo\s+entertainment\s+system",
    r"nintendo\s+entertainment\s+system",
    r"video\s+game\s+system",
    r"playstation\s*\d?",
    r"super\s+nintendo",
    r"nintendo\s*64",
    r"game\s*boy(\s+(color|advance))?",
    r"sega\s+dreamcast",
    r"sega\s+genesis",
    r"dreamcast",
    r"genesis",
    r"nintendo",
    r"\bsnes\b",
    r"\bnes\b",
    r"\bps[1235]\b",
    r"\bpsp\b",
    r"\bn64\b",
    r"\bgba\b",
    r"\bgbc\b",
    r"\bsega\b",
    r"\bsony\b",
)

# Piezas que nunca son una consola aunque el título nombre una: estas palabras
# nombran al artículo en venta, no a algo que venga adentro de una caja. Un
# adaptador "para PlayStation 2 Console" sigue siendo un adaptador.
#
# Cables, cargadores y fuentes quedan fuera a propósito: aparecen tanto en un
# cable suelto como en el combo de una consola ("Console Bundle – Controller /
# Cables"), así que se tratan en el nivel de abajo, donde la consola gana.
PART_MARKERS = (
    r"\badapters?\b",
    r"\bconverters?\b",
    r"\bcase\s+protector\b",
    r"\bacrylic\b",
    r"\bempty\s+case\b",
    r"\bstorage\s+case\b",
    r"\bdisplay\b",
    r"\bdust\s+cover\b",
)

LOT_MARKERS = (
    r"\blots?\s+of\b",
    r"\bgames?\s+lot\b",
    r"\blot\s+x?\d+\b",
    r"\byou\s+pick\b",
    r"\bpick\s+your\b",
    r"\bpick\s*&?\s*choose\b",
    r"\bchoose\s+(your|from)\b",
    r"\byour\s+choice\b",
)

CONSOLE_MARKERS = (
    r"\bconsoles?\b",
    r"\bconsolas?\b",
    r"\bscph-?\d{4,5}\b",
    r"\bslim\b",
    r"\bfat\b",
)

# Accesorios que sí pueden venir dentro de un combo de consola: si el título
# también dice "console", manda la consola.
ACCESSORY_MARKERS = (
    r"\bcontrollers?\b",
    r"\bdual\s*shock\b",
    r"\bjoysticks?\b",
    r"\bgamepads?\b",
    r"\bmemory\s+cards?\b",
    r"\bvmu\b",
    r"\bheadsets?\b",
    r"\bmultitap\b",
    r"\blight\s+gun\b",
    r"\bremotes?\b",
    r"\bgame\s+cases\b",
    r"\bcables?\b",
    r"\bcords?\b",
    r"\bchargers?\b",
    r"\bpower\s+supply\b",
)

GAME_MARKERS = (
    r"\bcartridges?\b",
    r"\bcib\b",
    r"\bcomplete\s+in\s+box\b",
    r"\bdisc\s+only\b",
    r"\bcartridge\s+only\b",
    r"\bgame\s+disc\b",
    r"\bblack\s+label\b",
    r"\bgreatest\s+hits\b",
    r"\bcomplete\s+w/?\s*manual\b",
    r"\bvideo\s+games?\b",
    r"\bgames?\b",
)

ITEM_KINDS = ("console", "game", "accessory", "lot")


@dataclass(frozen=True, slots=True)
class ItemKind:
    """Qué parece ser la pieza, y si el título lo dice o lo estamos deduciendo."""

    kind: str
    confident: bool

    @property
    def weighable(self) -> str:
        """El tipo que sirve para estimar peso; vacío cuando no hay con qué."""
        return self.kind if self.kind in {"console", "game", "accessory"} else ""


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _strip_platforms(text: str) -> str:
    for pattern in PLATFORM_NAMES:
        text = re.sub(pattern, " ", text)
    return text


def classify_listing_item(title: str) -> ItemKind:
    """Clasifica una publicación por su título.

    El orden no es casual: una pieza suelta gana sobre la consola que nombra,
    una consola gana sobre los accesorios que trae dentro, y un lote gana sobre
    el juego suelto que se le parezca.
    """

    raw = f" {str(title or '').lower()} "
    residual = _strip_platforms(raw)

    if _matches(PART_MARKERS, residual):
        return ItemKind("accessory", True)
    if _matches(CONSOLE_MARKERS, residual):
        return ItemKind("console", True)
    if _matches(LOT_MARKERS, residual):
        return ItemKind("lot", True)
    if _matches(ACCESSORY_MARKERS, residual):
        return ItemKind("accessory", True)
    if _matches(GAME_MARKERS, residual):
        return ItemKind("game", True)
    # Sacada la plataforma queda un título y ningún marcador: en este catálogo
    # eso es casi siempre un juego. Alcanza para estimar el peso, no para
    # descartar la publicación.
    if re.search(r"[a-z]{3}", residual):
        return ItemKind("game", False)
    return ItemKind("", False)


__all__ = ["ItemKind", "ITEM_KINDS", "classify_listing_item"]
