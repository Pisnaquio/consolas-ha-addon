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

from .platforms import PROFILES, RETRO_CARTRIDGE_KEYS, normalize_platform, platforms_in_text

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

# Lo que nunca es una consola ni un juego aunque el título nombre uno: estas
# palabras nombran al artículo en venta, no algo que venga adentro de una caja.
# Un adaptador "para PlayStation 2 Console" sigue siendo un adaptador.
#
# Se separan en cuatro categorías porque la card tiene que poder decir qué es
# cada cosa, no sólo que no es lo buscado: un material impreso (publicidad,
# libro, caja o manual sueltos), un objeto de merchandising (pin, imán,
# figura), una pieza de reemplazo (carcasa, lente, botón, flex) o un accesorio
# propiamente dicho (adaptador, estuche, kit de limpieza). Para el resto del
# radar las cuatro son `accessory`: no son el producto, no se pesan como una
# consola y no se comparan contra su precio.
#
# Cables, cargadores y fuentes quedan fuera a propósito: aparecen tanto en un
# cable suelto como en el combo de una consola ("Console Bundle – Controller /
# Cables"), así que se tratan más abajo, donde la consola gana.
PRINTED_MARKERS = (
    r"\bposters?\b",
    # Medido el 2026-10-12 en la primera corrida del portafolio retro: buscar
    # un juego por su nombre trae publicidades de revista, láminas, guías y
    # manuales sueltos, y todos pasaban como «ganga» de ese juego.
    r"\badvertisements?\b",
    r"\b(?:original|print|vintage|magazine)\s+(?:\d{4}\s+)?ads?\b",
    # «Castlevania Bloodlines … Vintage Video Game Ad Print».
    r"\bads?\s+prints?\b",
    r"\bpromo(?:tional)?\s+(?:ads?|posters?|flyers?|prints?|sheets?)\b",
    r"\bflyers?\b",
    r"\bbrochures?\b",
    r"\b(?:strategy|official|player'?s?|secrets)\s+guides?\b",
    r"\bguide\s*books?\b",
    # «Book» a secas no: «The Jungle Book» es un juego de SNES, NES y Genesis.
    r"\bpaperbacks?\b",
    r"\bhardcovers?\b",
    r"\bhardback\b",
    r"\bnovels?\b",
    r"\bscholastic\b",
    r"\b(?:comic|coloring|colouring|activity|sticker|story|picture|pop[\s-]?up)\s+books?\b",
    r"\bmagazines?\b",
    r"\blithographs?\b",
    r"\b(?:wall|pixel|box)\s+art\b",
    r"\bart\s+prints?\b",
    r"\b(?:manual|booklet)s?\s+only\b",
    r"\binstruction\s+booklet\b",
    # «Game Boy Color … Box And Inserts Only»: la caja sin la consola.
    r"\b(?:box|boxes|inserts?|tray)\s+only\b",
    r"\b(?:console|system|game|cartridge|disc)\s+not\s+included\b",
    # Medidas: «17 x 5.5» o «2"x3"» es un mapa, un póster o una lámina. Hace
    # falta un decimal o una unidad: «Hardcore 4x4» es el nombre de un juego.
    r"\b\d+\.\d+\s*\"?\s*x\s*\d+",
    r"\b\d+\s*\"?\s*x\s*\d+\.\d+",
    r"\b\d+\s*(?:\"|in\b|inch(?:es)?\b|cm\b)\s*x\s*\d+",
    r"\b\d+\s*x\s*\d+\s*(?:\"|in\b|inch(?:es)?\b|cm\b)",
)

MERCH_MARKERS = (
    # Merchandising que aparece buscando un juego por su nombre: una bandera
    # de God of War comparte título con el juego y no tiene ninguna marca que
    # la delate. Medido el 2026-09-15: 3 de 11 resultados de «God of War PSP»
    # eran una funda, una bandera y un estuche de metal.
    r"\bflags?\b",
    r"\bbanners?\b",
    r"\bkey\s*chains?\b",
    r"\blanyards?\b",
    r"\bmouse\s*pads?\b",
    r"\bmagnets?\b",
    # «Activision Blizzard Pin Set», «Oracle of Seasons … Pin Pinback». «Pin»
    # suelto no: «Pin Bot» es un juego de NES, y un flex de PS2 dice «12 Pin».
    r"\bpin\s*backs?\b",
    r"\bpin\s+sets?\b",
    r"\b(?:enamel|lapel|button|collector'?s?)\s+pins?\b",
    r"\bpins\b",
    r"\bpin\s+badges?\b",
    # Figuras y dioramas que caen en un lote de Genesis. «Figure» en singular
    # no: «Figure Skating» es un juego.
    r"\bfigurines?\b",
    r"\b(?:action|vinyl|collectible|pvc|mini)\s+figures?\b",
    r"\bfigures\b",
    r"\bstatues?\b",
    r"\bdioramas?\b",
    r"\bplush(?:ies|ie)?\b",
    r"\bfunko\b",
    r"\bamiibos?\b",
    r"\bt\s*-?\s*shirts?\b",
    r"\bhoodies?\b",
    r"\bmugs?\b",
    r"\btrading\s+cards?\b",
)

REPLACEMENT_PART_MARKERS = (
    # Repuestos que nombran el modelo de la consola: «Power Reset Button Flex
    # Cable SCPH-50001» se leía como una PS2 de cinco dólares, y «Screen Lens
    # Replacement for the Original Game Boy DMG-01» como un Game Boy.
    r"\bfaceplates?\b",
    r"\bfront\s+shell\b",
    r"\bflex\s+(?:cable|ribbon)s?\b",
    r"\bribbon\s+cables?\b",
    r"\b(?:power|reset|eject|open)\s+(?:reset\s+)?buttons?\b",
    r"\bbattery\s+(?:replacement|pack)\b",
    r"\breplacement\s+battery\b",
    # La tapa de pilas es pieza sólo cuando es lo que se vende: «Game Boy with
    # battery cover» es una consola completa.
    r"\breplacement\s+battery\s+(?:covers?|doors?)\b",
    r"\bbattery\s+(?:covers?|doors?)\s+(?:replacement|only|for)\b",
    r"\bbattery\s+cover\s+(?:psp|3ds|ds)\b",
    r"\breplacement\s+(?:screens?|lens(?:es)?|shells?|housings?|buttons?|parts?|lasers?|lcds?|boards?|"
    r"motherboards?|fans?|doors?|covers?|lids?|hinges?|speakers?|connectors?|slots?|jacks?|power\s+supply)\b",
    r"\b(?:screen|lens|shell|housing|button|laser|lcd|hinge|speaker|connector)s?\s+replacements?\b",
    r"\bscreen\s+lens(?:es)?\b",
    r"\blens(?:es)?\s+(?:for|only)\b",
    r"\bhousings?\b",
    r"\bshells?\s+(?:only|kit|replacement|case|housing)\b",
    r"\b(?:replacement|clear|transparent|custom)\s+shells?\b",
    r"\bips\s+(?:screen\s+|lcd\s+)?kits?\b",
    r"\bbacklight\s+kits?\b",
    r"\b(?:cartridge|cart|game)\s+(?:slot|port)\s+(?:connector|replacement)\b",
    r"\b(?:power|dc)\s+jacks?\b",
    r"\blaser\s+(?:lens|assembly|unit)\b",
    r"\bscrews?\b",
)

ACCESSORY_ITEM_MARKERS = (
    r"\badapters?\b",
    r"\bconverters?\b",
    r"\bcase\s+protectors?\b",
    r"\bacrylic\b",
    r"\bempty\s+case\b",
    r"\bstorage\s+(?:case|cases|box|boxes)\b",
    r"\breplacement\s+case\b",
    r"\bcase\s+only\b",
    r"\bdisplay\b",
    # `case` a secas sigue afuera a propósito: rompía «game w/ case». Van sólo
    # las variantes que nunca son el juego.
    r"\bcarrying\s+case\b",
    r"\bcarry\s+case\b",
    r"\btravel\s+case\b",
    r"\bmetal\s+case\b",
    # Cajas protectoras: un lote de diez estuches para cartuchos de Game Boy no
    # es un lote de juegos de Game Boy.
    r"\bprotective\s+(?:cases?|boxes|box|covers?|sleeves?)\b",
    r"\b(?:cartridge|cart|game|box)\s+protectors?\b",
    r"\b(?:cartridge|cart)\s+cases\b",
    r"\bscreen\s+protectors?\b",
    r"\bdecals?\b",
    r"\bskins?\b",
    # «Genuine Nintendo 2 Player Game Link Cable … for Game Boy Console»: el
    # cable de link no viene adentro de un combo, es lo que se vende.
    r"\blink\s+cables?\b",
    r"\bplug\s*(?:and|&|n)\s*play\b",
    # «SNES System Cleaning Kit In Box» es un kit, no una consola en caja.
    r"\bcleaning\s+(?:kits?|cartridges?|cards?)\b",
    r"\bdemo\s+discs?\b",
    r"\bdust\s+(?:covers?|cases?|sleeves?)\b",
)

# (categoría, marcadores). El orden es el de precedencia.
NOT_THE_PRODUCT = (
    ("printed", PRINTED_MARKERS),
    ("merch", MERCH_MARKERS),
    ("part", REPLACEMENT_PART_MARKERS),
    ("accessory", ACCESSORY_ITEM_MARKERS),
)

# Lo que viene con la pieza no es la pieza: «Console w/ new screen lens» o
# «PS2 Slim with carrying case» describen una consola. Una o dos palabras entre
# medio alcanzan: «with original box», «w/ new shell».
_INCLUDED_WITH = re.compile(
    r"(?:\b(?:with|w|includes?|including|plus)\b|\bw/|\+)\s*(?:[\w'-]+\s+){0,2}$"
)

# Compatibilidad: lo de antes era una sola lista. Se conserva el nombre para
# quien lo importe.
PART_MARKERS = tuple(marker for _, markers in NOT_THE_PRODUCT for marker in markers)

# Un manual sin nada que diga que viene el juego es el manual solo: «Pokemon
# Yellow Game Boy Manual». Con cualquiera de estas palabras al lado, el manual
# es parte de una copia completa.
MANUAL_MARKER = r"\b(?:manuals?|booklets?)\b"
WITH_THE_GAME = (
    # «with manual», «w/ manual» y «no manual» hablan del juego: dicen si el
    # manual viene con él.
    r"\b(?:with|no)\s+manuals?\b",
    r"\bw/\s*manuals?\b",
    r"\bcart(?:ridge)?s?\b",
    r"\bgames?\b",
    r"\bdiscs?\b",
    r"\bcib\b",
    r"\bcomplete\b",
    r"\bbox(?:ed)?\b",
    r"\bcase\b",
    r"\bloose\b",
    r"\bsealed\b",
    r"\btested\b",
    r"\bworking\b",
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
    # El modelo del Game Boy original; los cartuchos usan «DMG-XX-USA».
    r"\bdmg-?01\b",
    r"\bslim\b",
    r"\bfat\b",
)

# «SNES System with 2 controllers and 3 games» es una consola: es la forma más
# común de publicar un combo retro, y sin esto los controles del combo lo
# convertían en accesorio y la búsqueda de consolas lo descartaba. Va aparte
# porque «for … system» nombra para qué sirve un accesorio, no lo que se vende.
SYSTEM_MARKER = r"\bsystems?\b"
FOR_SYSTEM = r"\bfor\s+(?:the\s+|your\s+|a\s+)?(?:\w+\s+){0,2}systems?\b"

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
    # «PS2 2 Game Bundle»: el número pegado a «game lot/bundle» cuenta igual
    # que «2-Game». Un bundle de dos no puede pasar por uno de quince.
    rf"\b{_COUNT}\s+game\s+(?:lot|bundle|set|pack)\b",
    rf"\(\s*{_COUNT}\s*\)\s*(?:games?|discs?|carts?|cartridges?)\b",
)

# Las mismas declaraciones, separadas según qué cuentan. «Lot of 12» cuenta lo
# que haya en el lote; «12 games» cuenta juegos. Si el título también trae
# controles o una consola, sólo la segunda dice cuántos juegos hay.
GAME_COUNT_PATTERNS = (
    rf"\b{_COUNT}\s*-\s*(?:games?|discs?|titles?|carts?|cartridges?)\b",
    rf"\b{_COUNT}\s+(?:games|discs|titles|carts|cartridges|juegos)\b",
    rf"\b{_COUNT}\s+game\s+(?:lot|bundle|set|pack)\b",
    rf"\(\s*{_COUNT}\s*\)\s*(?:games?|discs?|carts?|cartridges?)\b",
)


ITEM_KINDS = ("console", "game", "accessory", "lot")

# Qué clase de «no es el producto» es un `accessory`. La card lo dice con estas
# palabras: «Es material impreso y esta búsqueda pide un juego».
ACCESSORY_CATEGORIES = ("accessory", "printed", "merch", "part")


@dataclass(frozen=True, slots=True)
class ItemKind:
    """Qué parece ser la pieza, y si el título lo dice o lo estamos deduciendo."""

    kind: str
    confident: bool
    # El precio publicado no es el de una pieza concreta: es el de la opción
    # más barata de un listado "elegí cuál querés".
    variable_price: bool = False
    # Para un `accessory`: si es un accesorio, material impreso, merchandising
    # o una pieza de reemplazo. Vacío para el resto.
    category: str = ""

    @property
    def weighable(self) -> str:
        """El tipo que sirve para estimar peso; vacío cuando no hay con qué."""
        return self.kind if self.kind in {"console", "game", "accessory"} else ""


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _names_the_item(patterns: tuple[str, ...], text: str) -> bool:
    """Si algún marcador nombra lo que se vende, y no algo que viene con ello."""
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if not _INCLUDED_WITH.search(text[: match.start()]):
                return True
    return False


def not_the_product_category(title: str) -> str:
    """La categoría de lo que no es ni consola ni juego, o vacío."""
    residual = _strip_platforms(f" {str(title or '').lower()} ")
    for category, markers in NOT_THE_PRODUCT:
        if _names_the_item(markers, residual):
            return category
    return ""


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

    category = not_the_product_category(title)
    if category:
        return ItemKind("accessory", True, category=category)
    if _matches(CONSOLE_MARKERS, residual):
        return ItemKind("console", True)
    if re.search(SYSTEM_MARKER, residual) and not re.search(FOR_SYSTEM, residual):
        return ItemKind("console", True)
    if re.search(MANUAL_MARKER, residual) and not _matches(WITH_THE_GAME, residual):
        return ItemKind("accessory", True, category="printed")
    if _matches(LOT_MARKERS, residual):
        return ItemKind("lot", True, _matches(VARIABLE_PRICE_MARKERS, residual))
    if _matches(ACCESSORY_MARKERS, residual):
        return ItemKind("accessory", True, category="accessory")
    if _matches(GAME_MARKERS, residual):
        return ItemKind("game", True)
    # Sacada la plataforma queda un título y ningún marcador: en este catálogo
    # eso es casi siempre un juego. Alcanza para estimar el peso, no para
    # descartar la publicación.
    if re.search(r"[a-z]{3}", residual):
        return ItemKind("game", False)
    return ItemKind("", False)


# Lo que dice que un lote trae juegos aunque también traiga un estuche o un
# adaptador: «N64 Game Lot + Adapter» es un lote de juegos; «Cartridge Dust
# Cases» no.
GAME_LOT_EVIDENCE = (
    r"\bgames\b",
    r"\bgame\s+(?:cartridge\s+)?(?:lot|bundle)\b",
    r"\bcartridges\b",
    r"\bcarts\b",
    r"\bdiscs\b",
)


def names_games(title: str) -> bool:
    """Si el título dice que la publicación trae juegos, en plural o como lote."""
    return _matches(GAME_LOT_EVIDENCE, _strip_platforms(f" {str(title or '').lower()} "))


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


# --------------------------------------------------------------------------- #
# Evaluaciones que no son «qué es»: si es original, si está completa, si es un  #
# lote de verdad. Todas leen sólo el título, todas devuelven qué señal usaron   #
# y ninguna sabe qué búsquedas existen: reciben la plataforma y deciden por la  #
# familia de hardware, nunca por el nombre de una consola concreta.            #
# --------------------------------------------------------------------------- #

def _norm(title: str) -> str:
    return f" {normalize_platform(title)} "


def _first(patterns: tuple[str, ...], text: str) -> str:
    for pattern in patterns:
        found = re.search(pattern, text)
        if found:
            return found.group(0).strip()
    return ""


# «Not a repro», «no fakes»: una negación no es una confesión. Antes «Not a
# Repro» se leía como «repro» y descartaba justo al vendedor que lo aclaraba.
NEGATED_COPY = (
    r"\b(?:not|no|non|never|zero)\s+(?:a\s+|an\s+)?"
    r"(?:repro(?:duction)?s?|fakes?|bootlegs?|counterfeits?|clones?|copy|copies|knock\s*offs?)\b"
)


# Una caja, un manual o un insert repro dicen algo del envase, no de la pieza:
# un cartucho original con caja repro no es una reproducción, pero tampoco es
# un CIB. Se sacan del texto antes de buscar copias, y `taxonomy` los usa para
# no contar esa caja.
REPRO_PACKAGING_MARKERS = (
    r"\b(?:repro(?:duction)?|replacement|custom|aftermarket|reprint(?:ed)?|reproduced|fake|third\s+party)\s+"
    r"(?:box(?:es)?|cases?|packaging|manuals?|inserts?)\b",
    r"\b(?:box|case|manual|packaging)\s+(?:is\s+)?(?:a\s+)?(?:repro(?:duction)?|reprint(?:ed)?|custom|replacement)\b",
    r"\bnot\s+(?:the\s+)?original\s+(?:box|case|packaging)\b",
)


def _without_negations(text: str) -> tuple[str, bool]:
    text = re.sub("|".join(REPRO_PACKAGING_MARKERS), " ", text)
    stripped, count = re.subn(NEGATED_COPY, " ", text)
    return stripped, count > 0


# Una consola que no es la original: una reedición (mini, classic, plug and
# play) o un clon. «Nintendo NES Classic 2018» no es la NES que busca la
# búsqueda de NES, y «Sega Mega Drive II … Chinese Bootleg Clone» no es una
# Genesis.
REISSUE_PATTERNS = (
    r"\bclassic\s+(?:mini|edition)\b",
    r"\bmini\s+classic\b",
    r"\b(?:nes|snes|super\s+nes|super\s+nintendo|famicom|playstation|ps|genesis|mega\s+drive|sega)\s+"
    r"(?:classic|mini)\b(?!\s+series)",
    r"\bplug\s+(?:and|n)\s+play\b",
    r"\bretro\s*bit\b",
    r"\bretron\b",
    r"\bhyperkin\b",
    r"\bsupaboy\b",
)
CLONE_PATTERNS = (
    r"\bbootlegs?\b",
    r"\bclones?\b",
    r"\bfamiclones?\b",
    r"\bknock\s*offs?\b",
    r"\bcounterfeit\b",
    r"\bfakes?\b",
    r"\breplicas?\b",
    r"\bunofficial\b",
    r"\bcompatible\s+(?:console|system)\b",
    r"\b\d{2,4}\s+in\s+1\b",
    r"\bbuilt\s*in\s+games\b",
)


def detect_hardware_origin(title: str) -> tuple[str, str]:
    """`("reissue" | "clone" | "", señal)`: si la consola no es la original."""
    text, _ = _without_negations(_norm(title))
    signal = _first(REISSUE_PATTERNS, text)
    if signal:
        return "reissue", signal
    signal = _first(CLONE_PATTERNS, text)
    if signal:
        return "clone", signal
    return "", ""


# --- Autenticidad de cartuchos ------------------------------------------------
#
# Un cartucho retro tiene un mercado de reproducciones que un disco no tiene, y
# sus vendedores rara vez escriben «original»: se da por sentado. Por eso hay
# tres respuestas, no dos. Una copia declarada se descarta; una publicación con
# las marcas típicas de un vendedor de copias («Video Game Cartridge», «Game
# Card», «64 Bit», «USA Version») queda bajo sospecha fuerte; y un cartucho que
# no dice nada queda con la autenticidad sin confirmar, que no es lo mismo que
# original. Sólo una declaración explícita («authentic», «genuine», «OEM», «not
# a repro») la da por declarada: el radar no mira fotos, así que nunca puede
# darla por comprobada.

# Cualquier plataforma: lo dice el propio vendedor.
COPY_MARKERS = (
    r"\brepro(?:duction)?s?\b",
    r"\bbootlegs?\b",
    r"\bcounterfeit\b",
    r"\bfakes?\b",
    r"\bpirat[ae]s?\b",
    r"\breplicas?\b",
    r"\bknock\s*offs?\b",
)
# Sólo en cartuchos retro. «Game card» es el nombre oficial de un juego de 3DS
# y la marca de una copia en uno de SNES: «Contra III … GAME CARD USA VERSION».
RETRO_CART_COPY_MARKERS = (
    r"\bgame\s+cards?\b",
    # «Pokémon Stadium 2 Compatible N64 Cartridge».
    r"\bcompatible\s+(?:[a-z0-9]+\s+){0,2}(?:cartridges?|carts?|game\s+cards?|cards?)\b",
    r"\b(?:cartridges?|carts?)\s+compatible\b",
    r"\bcustom\b",
    r"\bfan\s*made\b",
    r"\btranslat(?:ed|ion)\b",
    r"\benglish\s+patch(?:ed)?\b",
    r"\brom\s*hacks?\b",
    r"\bhack(?:ed|s)?\b",
    r"\bhomebrew\b",
    r"\bgold\s+shell\b",
    r"\bholographic\s+labels?\b",
    r"\bmulti\s*carts?\b",
    r"\b\d{2,4}\s+in\s+1\b",
    r"\beverdrive\b",
    r"\bflash\s+carts?\b",
)
# Marcas de quien vende copias nuevas, no de quien vende un cartucho usado.
# Por sí solas no prueban nada; juntas con un título sin declaración, piden
# mirar las fotos antes de seguir. «Super mario 64 STAR ROAD Video Game
# Cartridge» es un hack, y lo único que lo delata en el título es eso.
RETRO_CART_SUSPECT_MARKERS = (
    r"\bvideo\s+game\s+cartridge\b",
    r"\bgame\s+cartridge\s+for\b",
    r"\b(?:cartridge|cart|card)\s+for\b",
    r"\b(?:8|16|32|64)\s*bit\b",
    r"\b(?:us|usa|english|eng|ntsc|north\s+america)\s+version\b",
    r"\bregion\s+free\b",
    r"\bnew\s+(?:cartridge|cart)\b",
    r"\bbrand\s+new\b(?!.*\bsealed\b)",
)
AUTHENTIC_MARKERS = (
    r"\bauthentic\b",
    r"\bgenuine\b",
    r"\boem\b",
    r"\bofficial(?:ly)?\b",
    r"\b100\s*(?:percent\s+)?original\b",
    r"\blegit\b",
    r"\boriginal\s+(?:authentic|cart(?:ridge)?s?)\b",
)


@dataclass(frozen=True, slots=True)
class Authenticity:
    """Qué dice el título sobre la originalidad de la pieza.

    `status`: `copy` (se declara copia), `suspect` (marcas de vendedor de
    copias), `declared` (declara ser original), `unknown` (cartucho retro que no
    dice nada) o vacío cuando la regla no aplica.
    """

    status: str = ""
    signal: str = ""

    @property
    def blocks_bargain(self) -> bool:
        return self.status in {"suspect", "unknown"}


def assess_authenticity(title: str, platform: str = "") -> Authenticity:
    text, negated = _without_negations(_norm(title))
    signal = _first(COPY_MARKERS, text)
    if signal:
        return Authenticity("copy", signal)
    if platform not in RETRO_CARTRIDGE_KEYS:
        declared = negated or bool(_first(AUTHENTIC_MARKERS, text))
        return Authenticity("declared" if declared else "", "")
    signal = _first(RETRO_CART_COPY_MARKERS, text)
    if signal:
        return Authenticity("copy", signal)
    declared = _first(AUTHENTIC_MARKERS, text) or ("not a repro" if negated else "")
    if declared:
        return Authenticity("declared", declared)
    signal = _first(RETRO_CART_SUSPECT_MARKERS, text)
    if signal:
        return Authenticity("suspect", signal)
    return Authenticity("unknown", "")


# --- Completitud de una consola ----------------------------------------------
#
# Una consola suelta es una compra válida si el precio lo refleja. El problema
# era compararla contra la misma vara que un bundle listo para usar: «console
# only» le ganaba en el ranking a una N64 con control OEM, Jumper Pak y cables.
# Acá se lee qué trae, qué le falta y qué daño declara; cuánto cuesta completarla
# lo decide `valuation`.

_KIT_PRESENT = {
    "controller": r"(?:controllers?|controler|dual\s*shock\s*\d?|sixaxis|game\s*pads?|joy\s*pads?|joysticks?|joy\s*cons?)",
    "cables": r"(?:cables?|cords?|hook\s*ups?|power\s+(?:supply|adapter|brick|cord|cable)|ac\s+adapters?|av|a\s+v|hdmi|psu)",
    "jumper-pak": r"(?:jumper|expansion)\s*pa(?:k|ck)s?",
    "battery-cover": r"battery\s+(?:covers?|doors?|lids?)",
    "charger": r"(?:chargers?|charging\s+(?:cables?|cords?|docks?|cradles?)|power\s+(?:adapters?|cords?|cables?|supply)|ac\s+adapters?)",
}
# «No power» es que no enciende, no que le falta el cable: la parte de cables
# que puede faltar se nombra con su sustantivo.
_KIT_MISSING_STEM = {
    **_KIT_PRESENT,
    "cables": r"(?:cables?|cords?|hook\s*ups?|power\s+(?:supply|adapter|brick|cord|cable)|ac\s+adapters?|av\s+cables?|hdmi\s+cables?|psu)",
}
_MISSING_BEFORE = r"\b(?:no|without|missing|w\s+o|minus|lacks?|lacking|needs?)\s+(?:(?:the|a|an|any|its|original|oem|official)\s+)*"
_MISSING_AFTER = r"\s+(?:not\s+included|missing|sold\s+separately|not\s+incl)\b"

KIT_PIECE_LABELS = {
    "controller": "control",
    "cables": "cables de alimentación y video",
    "jumper-pak": "Jumper Pak",
    "battery-cover": "tapa de pilas",
    "charger": "cargador",
}

CONSOLE_CIB_MARKERS = (
    r"\bcib\b",
    r"\bcomplete\s+in\s+(?:the\s+)?(?:original\s+)?box\b",
    r"\bin\s+(?:the\s+)?(?:original\s+)?box\b",
    r"\bwith\s+(?:the\s+)?(?:original\s+)?box\b",
    r"\bw\s+(?:original\s+)?box\b",
    r"\boriginal\s+box\b",
    r"\bboxed\b",
    r"\bnew\s+in\s+box\b",
    r"\bnib\b",
    r"\bsealed\b",
)
CONSOLE_ONLY_MARKERS = (r"\b(?:console|system|unit|handheld)\s+only\b",)
READY_MARKERS = (
    r"\bready\s+to\s+(?:play|go)\b",
    r"\beverything\s+(?:you\s+)?need(?:ed|s)?\b",
    r"\bcomplete\s+(?:set\s*up|setup)\b",
    r"\bplug\s+in\s+and\s+play\b",
)

# Daño que no impide jugar pero baja el valor y hay que mirar: se penaliza a la
# vista en vez de descartar. Lo que impide jugar (no lee, no da video, YLOD) lo
# descarta `matching` como defecto.
CONSOLE_ISSUE_PATTERNS = (
    (r"\b(?:lcd|screen)\s+(?:has\s+)?(?:(?:some|minor|small|vertical|horizontal|dead)\s+)*lines?\b", "pantalla con líneas"),
    (r"\b(?:vertical|horizontal)\s+lines?\b", "pantalla con líneas"),
    (r"\bdead\s+pixels?\b", "píxeles muertos"),
    (r"\bdiscolou?r(?:ed|ation)\b", "decoloración"),
    (r"\byellow(?:ed|ing)\b", "plástico amarillento"),
    (r"\bscratch(?:ed|es)?\s+(?:screen|lens)\b", "pantalla rayada"),
    (r"\b(?:screen|lens)\s+(?:is\s+)?scratch(?:ed|es)?\b", "pantalla rayada"),
    (r"\bloose\s+(?:cartridge\s+|cart\s+)?(?:port|slot)\b", "puerto con juego"),
    (r"\b(?:port|slot|reader|laser|drive)\s+(?:issues?|problems?)\b", "puerto o lector con problemas"),
    (r"\b(?:slow|sometimes)\s+(?:to\s+)?reads?\b", "lector irregular"),
    (r"\bintermittent(?:ly)?\b", "falla intermitente"),
    (r"\bminor\s+issues?\b", "problemas menores declarados"),
    (r"\bcosmetic\s+(?:damage|issues?|wear)\b", "daño cosmético"),
)

# El lector de una consola de disco. «Enciende» no prueba que lea, y en una PS2
# el lector es lo primero que se rompe: el dueño la quiere «probada leyendo CD
# y DVD».
READER_BOTH_MARKERS = (
    r"\breads?\s+(?:all|every|any)\s+(?:discs?|games?|formats?|media)\b",
    r"\breads?\s+everything\b",
    r"\b(?:cds?|blue(?:\s+bottom)?|ps1)\s+(?:discs?\s+|games?\s+)?(?:and|&|\+)\s+(?:dvds?|silver|ps2)\b",
    r"\b(?:dvds?|silver|ps2)\s+(?:discs?\s+|games?\s+)?(?:and|&|\+)\s+(?:cds?|blue|ps1)\b",
)
READER_GENERIC_MARKERS = (
    r"\breads?\s+(?:the\s+)?(?:discs?|games?|cds?|dvds?|blu\s*rays?)\b",
    r"\bplays?\s+(?:all\s+)?(?:games?|discs?|dvds?|cds?|blu\s*rays?)\b",
    r"\btested\s+(?:with|on)\s+(?:a\s+|several\s+|multiple\s+|\d+\s+)?(?:games?|discs?)\b",
    r"\b(?:disc|disk|optical)?\s*(?:drive|laser|reader)\s+(?:works|working|tested|good|reads|is\s+good|fully\s+functional)\b",
    r"\bnew\s+laser\b",
    r"\bloads?\s+games?\b",
    r"\bgame\s+tested\b",
)
POWERS_ONLY_MARKERS = (
    r"\bpowers?\s+(?:on|up)\b",
    r"\bturns?\s+on\b",
    r"\bpower\s+tested\b",
    r"\blights?\s+(?:up|on)\b",
    r"\bboots?\s+(?:up|to)\b",
)


@dataclass(frozen=True, slots=True)
class ConsoleKit:
    """Qué trae una consola y en qué estado, según su título.

    `status`: `cib`, `ready` (lista para usar), `missing` (le falta algo que el
    título declara), `console-only`, `damaged` (daño declarado que no impide
    jugar) o `undeclared` (no dice lo suficiente para saberlo).
    `reader`: `confirmed`, `partial` (lee, pero no dice qué), `powers-only`,
    `unconfirmed` o vacío para lo que no tiene lector.
    """

    status: str
    present: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    undeclared: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    reader: str = ""
    reader_label: str = ""

    @property
    def ready_to_play(self) -> bool:
        return self.status in {"cib", "ready"}


def assess_console_kit(title: str, platform: str = "") -> ConsoleKit:
    profile = PROFILES.get(platform)
    text = _norm(title)
    requires = profile.requires if profile else ("controller", "cables")
    console_only = bool(_first(CONSOLE_ONLY_MARKERS, text))

    present: list[str] = []
    missing: list[str] = []
    undeclared: list[str] = []
    for piece in requires:
        stem = _KIT_MISSING_STEM[piece]
        if re.search(_MISSING_BEFORE + stem + r"\b", text) or re.search(r"\b" + stem + _MISSING_AFTER, text):
            missing.append(piece)
        elif re.search(r"\b" + _KIT_PRESENT[piece] + r"\b", text):
            present.append(piece)
        elif console_only and piece in {"controller", "cables", "charger", "jumper-pak"}:
            missing.append(piece)
        else:
            undeclared.append(piece)
    if undeclared and _first(READY_MARKERS, text):
        present.extend(undeclared)
        undeclared = []

    issues = tuple(dict.fromkeys(label for pattern, label in CONSOLE_ISSUE_PATTERNS if re.search(pattern, text)))

    reader, reader_label = "", ""
    if profile is not None and profile.reader:
        reader_label = profile.reader
        if _first(READER_BOTH_MARKERS, text):
            reader = "confirmed"
        elif _first(READER_GENERIC_MARKERS, text):
            reader = "partial" if len(profile.reader_media) > 1 else "confirmed"
        elif _first(POWERS_ONLY_MARKERS, text):
            reader = "powers-only"
        else:
            reader = "unconfirmed"

    home = profile is None or profile.family == "home"
    # Una caja repro no hace CIB a una consola: el dueño quiere la caja retail.
    retail_box = bool(_first(CONSOLE_CIB_MARKERS, re.sub("|".join(REPRO_PACKAGING_MARKERS), " ", text)))
    if issues:
        status = "damaged"
    elif retail_box and not missing:
        status = "cib"
    elif (console_only and home) or (home and {"controller", "cables"} <= set(missing)):
        status = "console-only"
    elif missing:
        status = "missing"
    elif not undeclared:
        status = "ready"
    else:
        status = "undeclared"
    return ConsoleKit(
        status=status,
        present=tuple(present),
        missing=tuple(missing),
        undeclared=tuple(undeclared),
        issues=issues,
        reader=reader,
        reader_label=reader_label,
    )


# --- Lotes --------------------------------------------------------------------
#
# Un lote se busca por lo que cuesta cada juego. Eso sólo vale cuando se sabe
# cuántos juegos son, cuántos son de la plataforma buscada y que no son veinte
# copias del mismo. Lo que el título no permite saber no se adivina: queda
# «cantidad sin confirmar», que no es «cumple».

MYSTERY_LOT_MARKERS = (
    r"\bmystery\b",
    r"\bblind\s+(?:packs?|box(?:es)?|bags?|lots?)\b",
    r"\bsurprise\b",
    r"\bgrab\s+bags?\b",
    r"\brandom\s+(?:games?|titles?|lot|selection|assortment|picks?)\b",
    r"\b(?:games?|titles?)\s+(?:are\s+)?(?:chosen\s+)?(?:at\s+)?random(?:ly)?\b",
    r"\blucky\s+(?:box|bag|lot|dip)\b",
    r"\bunknown\s+titles?\b",
    r"\byou\s+get\s+what\s+you\s+get\b",
)
# Muchas copias del mismo juego no son una biblioteca: «30X WHOLESALE …
# IMAGINE BABYZ» son treinta Imagine Babyz.
COPIES_LOT_MARKERS = (
    r"^\s*\d{1,3}\s*x\s",
    r"\b\d{1,3}\s+(?:copies|units)\b",
    r"\bx\s*\d{1,3}\s+(?:copies|units)\b",
    r"\bsame\s+(?:game|title)\b",
    r"\bmultiple\s+copies\b",
    r"\bwholesale\b",
)
# Relleno: no se descarta, pero no vale por pieza lo que vale un título útil.
FILLER_LOT_MARKERS = (
    r"\bsports?\b",
    r"\bmadden\b",
    r"\bnfl\b",
    r"\bnba\b",
    r"\bnhl\b",
    r"\bmlb\b",
    r"\bfifa\b",
    r"\bncaa\b",
    r"\bespn\b",
    r"\bpga\b",
    r"\btiger\s+woods\b",
    r"\bnascar\b",
    r"\b2k\d{1,2}\b",
    r"\bbaseball\b",
    r"\bfootball\b",
    r"\bbasketball\b",
    r"\bhockey\b",
    r"\bsoccer\b",
    r"\bimagine\b",
    r"\bpetz\b",
    r"\bbabyz\b",
    r"\bbarbie\b",
    r"\bkaraoke\b",
    r"\bduplicates?\b",
    r"\bbulk\b",
    r"\breseller\b",
    r"\bresale\b",
)
# Lo que no es un juego dentro de un lote: «Lot of 12» con controles adentro no
# dice cuántos juegos son.
LOT_NON_GAME_MARKERS = (
    r"\bconsoles?\b",
    r"\bsystems?\b",
    r"\bcontrollers?\b",
    r"\bmemory\s+cards?\b",
    r"\bcables?\b",
    r"\bcords?\b",
    r"\baccessor(?:y|ies)\b",
    r"\bchargers?\b",
    r"\bmovies?\b",
    r"\bumd\s+videos?\b",
)
# «PS1 games lot, works on PS2»: la PS2 no es otra plataforma del lote.
_COMPATIBILITY_CUE = re.compile(
    r"\b(?:compatible\s+with|works\s+(?:on|with|in)|plays\s+(?:on|in)|playable\s+on|for\s+use\s+(?:on|with)|"
    r"backwards?\s+compatible(?:\s+with)?)\s+(?:[a-z0-9]+\s+){0,4}"
)
_LOT_WORD = r"\b(?:lots?|bundles?)\b"


@dataclass(frozen=True, slots=True)
class LotAnalysis:
    """Qué se puede saber de un lote por su título.

    `quantity`: `confirmed` (declara cuántos juegos son, y son de la plataforma
    buscada), `unconfirmed` (no lo declara, o lo declara junto con otras cosas o
    con otras plataformas) o `single` (es un juego suelto, no un lote).
    """

    pieces: int | None
    quantity: str
    quantity_note: str = ""
    mystery: str = ""
    copies: str = ""
    filler: tuple[str, ...] = ()
    other_platforms: tuple[str, ...] = ()


def analyze_lot(title: str, platform: str = "", item_kind: ItemKind | None = None) -> LotAnalysis:
    text = _norm(title)
    raw_residual = _strip_platforms(f" {str(title or '').lower()} ")
    item = item_kind or classify_listing_item(title)
    pieces = detect_lot_size(title)
    mystery = _first(MYSTERY_LOT_MARKERS, text)
    copies = _first(COPIES_LOT_MARKERS, text)
    filler = tuple(dict.fromkeys(_first((pattern,), text) for pattern in FILLER_LOT_MARKERS if re.search(pattern, text)))

    attributable = _COMPATIBILITY_CUE.sub(" ", text)
    others = tuple(key for key in platforms_in_text(attributable) if platform and key != platform)

    if pieces is None and item.kind == "game" and not names_games(title) and not re.search(_LOT_WORD, raw_residual):
        return LotAnalysis(None, "single", "Es un juego suelto, no un lote", mystery, copies, filler, others)

    if pieces is None:
        quantity, note = "unconfirmed", "No declara cuántos juegos trae"
    else:
        counted_games = any(re.search(pattern, raw_residual) for pattern in GAME_COUNT_PATTERNS)
        non_games = _first(LOT_NON_GAME_MARKERS, raw_residual)
        if non_games and not counted_games:
            quantity = "unconfirmed"
            note = f"Declara {pieces} piezas, pero también trae «{non_games}»: no dice cuántas son juegos"
        elif len(others) == 1:
            label = PROFILES[others[0]].label if others[0] in PROFILES else others[0]
            quantity = "unconfirmed"
            note = f"Mezcla juegos de {label}: no dice cuántos de los {pieces} son de la plataforma buscada"
        else:
            quantity, note = "confirmed", ""
    return LotAnalysis(pieces, quantity, note, mystery, copies, filler, others)


__all__ = [
    "ACCESSORY_CATEGORIES",
    "CONSOLE_CIB_MARKERS",
    "REPRO_PACKAGING_MARKERS",
    "Authenticity",
    "ConsoleKit",
    "ItemKind",
    "ITEM_KINDS",
    "KIT_PIECE_LABELS",
    "LotAnalysis",
    "analyze_lot",
    "assess_authenticity",
    "assess_console_kit",
    "detect_hardware_origin",
    "not_the_product_category",
    "MAX_DECLARED_LOT_SIZE",
    "classify_listing_item",
    "detect_console_variant",
    "detect_lot_size",
    "names_games",
]
