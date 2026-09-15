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
    r"\breplacement\s+case\b",
    r"\bcase\s+only\b",
    r"\bdisplay\b",
    r"\bdust\s+cover\b",
    # Merchandising y piezas que aparecen buscando un juego por su nombre: una
    # bandera de God of War o la funda de batería de una PSP comparten título
    # con el juego y no tienen ninguna marca que las delate. Medido el
    # 2026-09-15 contra una corrida real: 3 de 11 resultados de «God of War
    # PSP» eran una funda de batería, una bandera y un estuche de metal, y los
    # tres pasaban como «probablemente un juego».
    #
    # `case` a secas sigue afuera a propósito: rompía «game w/ case». Van sólo
    # las variantes que nunca son el juego.
    r"\bbattery\s+cover\b",
    r"\bcarrying\s+case\b",
    r"\bcarry\s+case\b",
    r"\btravel\s+case\b",
    r"\bmetal\s+case\b",
    r"\bfaceplate\b",
    r"\bfront\s+shell\b",
    r"\bdecals?\b",
    r"\bskins?\b",
    r"\bflags?\b",
    r"\bbanners?\b",
    r"\bposters?\b",
    r"\bkeychains?\b",
    r"\blanyards?\b",
    r"\bmousepads?\b",
)

# "Elegí cuál querés": el precio que muestra la publicación es el de la opción
# más barata del listado, no el de una pieza concreta. Sirve para saber que el
# vendedor tiene stock, nunca como precio de algo.
VARIABLE_PRICE_MARKERS = (
    r"\byou\s+pick\b",
    r"\bpick\s+your\b",
    r"\bpick\s*&?\s*choose\b",
    r"\bchoose\s+(your|from)\b",
    r"\byour\s+choice\b",
    r"\beach\b",
    r"\bpick\b",
)

LOT_MARKERS = (
    *VARIABLE_PRICE_MARKERS,
    r"\blots?\s+of\b",
    r"\bgames?\s+lot\b",
    r"\blot\s+x?\d+\b",
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
    r"\bgame\s+cases?\b",
    r"\bcase\s+protectors?\b",
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

# Cuántas piezas declara el vendedor, cuando lo escribe con todas las letras.
#
# Contar piezas leyendo un título es frágil por definición: "Lot of 10 games"
# es inequívoco, pero una enumeración de diez nombres no lo es, y el título que
# no dice nada es el caso más común. Por eso acá sólo se leen declaraciones
# explícitas de cantidad y una duda devuelve `None`, nunca un número
# aproximado: el criterio de mínimo de piezas bloquea una cifra declarada, y lo
# que no está declarado queda sin verificar, que es la regla que ya gobierna al
# resto del radar.
NUMBER_WORDS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty",
)
NUMBER_WORD_VALUES = {word: index + 1 for index, word in enumerate(NUMBER_WORDS)}

# Igual que el máximo que acepta la API para `minLotSize`: por encima de eso el
# número no está contando piezas, está contando otra cosa.
MAX_DECLARED_LOT_SIZE = 500

# Las palabras van de más larga a más corta para que "seventeen" no se lea como
# "seven"; el `\b` del patrón lo cubre igual, pero el orden lo hace evidente.
_COUNT = r"(\d{1,3}|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")"

# Un "3" pegado a la unidad en singular ("6-Game Lot") cuenta; separado
# ("Pro Skater 3 Game") es el nombre del juego, no la cantidad. La unidad en
# plural sí cuenta separada ("Sealed 3 Games").
LOT_SIZE_PATTERNS = (
    rf"\blots?\s+of\s+{_COUNT}\b",
    rf"\blots?\s*[x#]\s*{_COUNT}\b",
    rf"\b(?:bundle|set|pack|group)\s+of\s+{_COUNT}\b",
    rf"\b{_COUNT}\s*-\s*(?:games?|discs?|titles?|pieces?|carts?|cartridges?|pack)\b",
    rf"\b{_COUNT}\s+(?:games|discs|titles|pieces|carts|cartridges|juegos|piezas)\b",
)


ITEM_KINDS = ("console", "game", "accessory", "lot")


@dataclass(frozen=True, slots=True)
class ItemKind:
    """Qué parece ser la pieza, y si el título lo dice o lo estamos deduciendo."""

    kind: str
    confident: bool
    # El precio publicado no es el de una pieza concreta: es el de la opción
    # más barata de un listado "elegí cuál querés".
    variable_price: bool = False

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


CONSOLE_VARIANTS = (
    ("super-slim", (r"\bsuper\s*slim\b",)),
    ("slim", (r"\bslim\b",)),
    ("fat", (r"\bfat\b", r"\bphat\b", r"\boriginal\b")),
)


def detect_console_variant(title: str) -> str:
    """Qué revisión de consola declara el título, o vacío si no lo dice.

    La diferencia no es cosmética: una PS3 Fat pesa 5 kg y una Slim 3,2, que
    son 31 dólares de courier. Guardar una sola por consola obligaba a elegir
    entre subestimar la pesada o encarecer la liviana, y en el mercado real las
    que aparecen son casi todas Slim.

    Sin declaración explícita no se adivina: quien llama decide qué hacer con
    la duda, y lo prudente es asumir la variante más pesada.
    """

    text = f" {str(title or '').lower()} "
    for variant, patterns in CONSOLE_VARIANTS:
        if any(re.search(pattern, text) for pattern in patterns):
            return variant
    return ""


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
        return ItemKind("lot", True, _matches(VARIABLE_PRICE_MARKERS, residual))
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


def detect_lot_size(title: str) -> int | None:
    """Cuántas piezas declara el título, o `None` cuando no lo dice claro.

    Se lee sólo el título: la descripción de una publicación mezcla la pieza en
    venta con el resto del inventario del vendedor, y un número sacado de ahí no
    cuenta lo que se está comprando.

    Dos declaraciones que no coinciden se tratan como ninguna. Si el vendedor
    escribió "Lot of 5" en un lado y "8 games" en el otro, el radar no tiene
    forma de elegir cuál manda, y elegir una sería inventar el dato.
    """

    text = _strip_platforms(f" {str(title or '').lower()} ")
    declared: set[int] = set()
    for pattern in LOT_SIZE_PATTERNS:
        for raw in re.findall(pattern, text):
            value = NUMBER_WORD_VALUES.get(raw) or (int(raw) if raw.isdigit() else 0)
            if 1 <= value <= MAX_DECLARED_LOT_SIZE:
                declared.add(value)
    if len(declared) != 1:
        return None
    return declared.pop()


__all__ = [
    "ItemKind",
    "ITEM_KINDS",
    "MAX_DECLARED_LOT_SIZE",
    "classify_listing_item",
    "detect_console_variant",
    "detect_lot_size",
]
