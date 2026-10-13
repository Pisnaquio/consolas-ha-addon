"""Qué es cada plataforma, dicho una sola vez.

El radar necesita saber tres cosas de una plataforma que ninguna búsqueda
declara y que tampoco dependen de qué búsquedas existan:

- **cómo se la nombra**: «N64», «Nintendo 64» y «nintendo 64» son la misma, y
  la pantalla de búsquedas tiene que agruparlas juntas;
- **en qué formato vienen sus juegos**: un cartucho de SNES tiene un mercado de
  reproducciones que un juego de PS2 no tiene, y «game card» es el nombre
  oficial de un juego de 3DS y la marca de una copia en uno de SNES;
- **qué hace falta para jugarla**: una consola de mesa sin control ni cables no
  vale lo que una lista para usar, una N64 sin Jumper Pak no arranca y a un
  Game Boy se le pierde la tapa de pilas.

Son hechos del hardware, no preferencias del dueño ni datos editables: por eso
viven acá y no en el catálogo ni en la UI. El orden de la tabla es el orden en
que la pantalla muestra las plataformas; las diez del portafolio retro van
primero, en el orden en que el dueño las nombró.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlatformProfile:
    key: str
    label: str
    # "home" o "handheld": una portátil no necesita control ni cables de video.
    family: str
    # "cartridge", "disc" o "card". Sólo los cartuchos retro tienen el mercado
    # de reproducciones que justifica tratar su originalidad aparte.
    medium: str
    # Lo que tiene que traer una unidad para quedar lista para jugar.
    requires: tuple[str, ...] = ()
    # Qué tiene que leer el lector, en palabras del dueño. Vacío sin lector.
    reader: str = ""
    # Cuando hay que confirmar más de un tipo de disco por separado: una PS2
    # puede leer DVD y no CD azules, o al revés.
    reader_media: tuple[str, ...] = ()


HOME = ("controller", "cables")

# (perfil, alias normalizados). Un alias es una frase en minúsculas, sin
# acentos y con un espacio entre palabras: lo que devuelve `normalize_platform`.
_PLATFORMS: tuple[tuple[PlatformProfile, tuple[str, ...]], ...] = (
    (PlatformProfile("snes", "SNES", "home", "cartridge", HOME),
     ("snes", "super nintendo entertainment system", "super nintendo", "super nes", "super famicom", "sfc")),
    (PlatformProfile("nes", "NES", "home", "cartridge", HOME),
     ("nes", "nintendo entertainment system")),
    (PlatformProfile("n64", "Nintendo 64", "home", "cartridge", (*HOME, "jumper-pak")),
     ("n64", "nintendo 64")),
    (PlatformProfile("ps1", "PlayStation 1", "home", "disc", HOME, "CD"),
     ("ps1", "psx", "psone", "ps one", "playstation 1", "playstation one", "playstation1")),
    (PlatformProfile("ps2", "PlayStation 2", "home", "disc", HOME, "CD y DVD", ("cd", "dvd")),
     ("ps2", "playstation 2", "playstation2")),
    (PlatformProfile("ps3", "PlayStation 3", "home", "disc", HOME, "Blu-ray y DVD"),
     ("ps3", "playstation 3", "playstation3")),
    (PlatformProfile("genesis", "Genesis", "home", "cartridge", HOME),
     ("genesis", "sega genesis", "mega drive", "megadrive", "sega mega drive")),
    (PlatformProfile("gb", "Game Boy", "handheld", "cartridge", ("battery-cover",)),
     ("game boy", "gameboy", "dmg 01", "dmg01", "original game boy")),
    (PlatformProfile("gbc", "Game Boy Color", "handheld", "cartridge", ("battery-cover",)),
     ("game boy color", "gameboy color", "gbc")),
    (PlatformProfile("3ds", "Nintendo 3DS", "handheld", "card", ("charger",)),
     ("3ds", "nintendo 3ds", "new 3ds", "3ds xl", "new 3ds xl", "2ds", "2ds xl", "new 2ds xl")),
    (PlatformProfile("gba", "Game Boy Advance", "handheld", "cartridge", ("battery-cover",)),
     ("game boy advance", "gameboy advance", "gba", "gba sp", "game boy advance sp")),
    (PlatformProfile("ds", "Nintendo DS", "handheld", "card", ("charger",)),
     ("nintendo ds", "ds lite", "dsi", "dsi xl", "ds")),
    (PlatformProfile("psp", "PSP", "handheld", "disc", ("charger",), "UMD"),
     ("psp", "playstation portable")),
    (PlatformProfile("vita", "PS Vita", "handheld", "card", ("charger",)),
     ("ps vita", "psvita", "playstation vita", "vita")),
    (PlatformProfile("ps4", "PlayStation 4", "home", "disc", HOME, "Blu-ray"),
     ("ps4", "playstation 4", "playstation4")),
    (PlatformProfile("ps5", "PlayStation 5", "home", "disc", HOME, "Blu-ray"),
     ("ps5", "playstation 5", "playstation5")),
    (PlatformProfile("gamecube", "GameCube", "home", "disc", HOME, "mini DVD"),
     ("gamecube", "game cube", "gcn", "nintendo gamecube")),
    (PlatformProfile("wii", "Wii", "home", "disc", HOME, "DVD"),
     ("wii", "nintendo wii")),
    (PlatformProfile("wiiu", "Wii U", "home", "disc", HOME, "disco"),
     ("wii u", "nintendo wii u")),
    (PlatformProfile("switch", "Nintendo Switch", "home", "card", ()),
     ("nintendo switch", "switch oled", "switch lite")),
    (PlatformProfile("dreamcast", "Dreamcast", "home", "disc", HOME, "GD-ROM"),
     ("dreamcast", "sega dreamcast")),
    (PlatformProfile("saturn", "Sega Saturn", "home", "disc", HOME, "CD"),
     ("sega saturn", "saturn")),
    (PlatformProfile("segacd", "Sega CD", "home", "disc", HOME, "CD"),
     ("sega cd", "mega cd")),
    (PlatformProfile("sms", "Master System", "home", "cartridge", HOME),
     ("master system", "sega master system")),
    (PlatformProfile("gamegear", "Game Gear", "handheld", "cartridge", ()),
     ("game gear", "sega game gear")),
    (PlatformProfile("xbox", "Xbox", "home", "disc", HOME, "DVD"),
     ("xbox", "original xbox")),
    (PlatformProfile("xbox360", "Xbox 360", "home", "disc", HOME, "DVD"),
     ("xbox 360", "x360")),
    (PlatformProfile("xboxone", "Xbox One", "home", "disc", HOME, "Blu-ray"),
     ("xbox one",)),
    (PlatformProfile("atari2600", "Atari 2600", "home", "cartridge", HOME),
     ("atari 2600", "atari vcs")),
)

PLATFORM_ORDER: tuple[str, ...] = tuple(profile.key for profile, _ in _PLATFORMS)
PROFILES: dict[str, PlatformProfile] = {profile.key: profile for profile, _ in _PLATFORMS}

# Más largos primero: «game boy color» tiene que ganarle a «game boy», y
# «super nintendo» a «nintendo».
_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((alias, profile.key) for profile, aliases in _PLATFORMS for alias in aliases),
        key=lambda pair: (-len(pair[0].split()), -len(pair[0])),
    )
)

# Formatos de cartucho con mercado de reproducciones. «Game card» es el nombre
# oficial de un juego de DS o 3DS y no dice nada de su originalidad.
RETRO_CARTRIDGE_KEYS = frozenset(key for key, profile in PROFILES.items() if profile.medium == "cartridge")


def normalize_platform(text: str) -> str:
    """Minúsculas, sin acentos, separadores unificados: igual que `matching.normalize`."""
    lowered = unicodedata.normalize("NFD", str(text or "")).lower()
    stripped = "".join(char for char in lowered if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def _alias_pattern(alias: str) -> str:
    return rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"


def platform_key(value: str) -> str:
    """La clave canónica de lo que alguien escribió como plataforma, o vacío.

    Sirve para el campo `platform` de una búsqueda: «N64», «Nintendo 64» y
    «nintendo 64» son la misma clave. Lo que no se reconoce queda vacío y quien
    llama decide qué hacer: la pantalla lo agrupa por lo que dice el texto.
    """

    text = normalize_platform(value)
    if not text:
        return ""
    for alias, key in _ALIASES:
        if text == alias:
            return key
    found = platforms_in_text(text)
    return found[0] if len(found) == 1 else ""


def platform_profile(value: str) -> PlatformProfile | None:
    return PROFILES.get(platform_key(value)) or PROFILES.get(str(value or ""))


def platforms_in_text(text: str) -> list[str]:
    """Qué plataformas nombra un texto, en orden de aparición y sin repetir.

    Cada alias consume lo que reconoce: «Game Boy Color» cuenta como GBC y no
    además como Game Boy, y «Super Nintendo» no suma una NES.
    """

    remaining = f" {normalize_platform(text)} "
    hits: list[tuple[int, str]] = []
    for alias, key in _ALIASES:
        for match in re.finditer(_alias_pattern(alias), remaining):
            hits.append((match.start(), key))
        remaining = re.sub(_alias_pattern(alias), lambda m: " " * len(m.group(0)), remaining)
    ordered: list[str] = []
    for _, key in sorted(hits):
        if key not in ordered:
            ordered.append(key)
    return ordered


def resolve_platform(search_platform: str, title: str = "") -> PlatformProfile | None:
    """La plataforma de la búsqueda; si no la declara, la que nombre el título."""
    profile = platform_profile(search_platform)
    if profile is not None:
        return profile
    found = platforms_in_text(title)
    return PROFILES.get(found[0]) if found else None


def platform_payload(value: str) -> dict[str, object]:
    """Clave, nombre y orden de una plataforma para la UI.

    Lo que no se reconoce viaja igual, con su texto como clave: una búsqueda de
    una plataforma que esta tabla no conoce sigue apareciendo en su grupo.
    """

    key = platform_key(value)
    if key:
        return {"key": key, "label": PROFILES[key].label, "order": PLATFORM_ORDER.index(key)}
    text = " ".join(str(value or "").split())
    return {"key": normalize_platform(text), "label": text, "order": len(PLATFORM_ORDER)}


__all__ = [
    "PLATFORM_ORDER",
    "PROFILES",
    "PlatformProfile",
    "RETRO_CARTRIDGE_KEYS",
    "normalize_platform",
    "platform_key",
    "platform_payload",
    "platform_profile",
    "platforms_in_text",
    "resolve_platform",
]
