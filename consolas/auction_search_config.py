#!/usr/bin/env python3

from __future__ import annotations

import re
import unicodedata
from typing import Iterable


HISTORICAL_BAVASTRO_QUERY = "electronica"

SHARED_KEYWORDS = [
    "nintendo",
    "playstation",
    "ps1",
    "ps one",
    "psx",
    "xbox",
    "sega",
    "atari",
    "game boy",
    "game boy advance",
    "game boy advance sp",
    "game boy color",
    "game boy pocket",
    "game boy micro",
    "game & watch",
    "gba",
    "gbc",
    "gamecube",
    "n64",
    "nintendo 64",
    "super nintendo",
    "family",
    "family computer",
    "famicom",
    "famiclone",
    "family game",
    "polystation",
    "poly station",
    "dynacom",
    "terminator",
    "mega drive",
    "sega genesis",
    "sega cd",
    "mega cd",
    "32x",
    "dreamcast",
    "master system",
    "saturn",
    "game gear",
    "psp",
    "ps vita",
    "ps2",
    "ps3",
    "ps4",
    "ps5",
    "switch",
    "wii",
    "wii u",
    "3ds",
    "2ds",
    "ds",
    "ds lite",
    "dsi",
    "nes",
    "snes",
    "joystick",
    "joysticks",
    "joistick",
    "joisticks",
    "gamepad",
    "control",
    "controles",
    "dualshock",
    "dualsense",
    "joy-con",
    "wiimote",
    "mario",
    "pokemon",
    "zelda",
    "kirby",
    "sonic",
    "donkey kong",
    "metroid",
    "videojuego",
    "videojuegos",
    "cartucho",
    "cartuchos",
    "arcade",
    "pong",
    "tele-sports",
    "telesports",
    "soundic",
    "radofin",
    "neo geo",
    "pc engine",
    "turbografx",
    "colecovision",
    "intellivision",
    "odyssey",
    "vectrex",
    "nintento",
    "nitendo",
    "nintendoo",
    "playsation",
    "playstaton",
    "pley station",
    "gamevoi",
]

STRONG_SIGNAL_TERMS = [
    "nintendo",
    "playstation",
    "ps1",
    "ps one",
    "psx",
    "xbox",
    "sega",
    "atari",
    "game boy",
    "game boy advance",
    "game boy advance sp",
    "game boy color",
    "game boy pocket",
    "game boy micro",
    "game & watch",
    "gba",
    "gbc",
    "gamecube",
    "n64",
    "nintendo 64",
    "super nintendo",
    "family",
    "family computer",
    "famicom",
    "famiclone",
    "family game",
    "polystation",
    "poly station",
    "dynacom",
    "mega drive",
    "sega genesis",
    "sega cd",
    "mega cd",
    "32x",
    "dreamcast",
    "master system",
    "saturn",
    "game gear",
    "psp",
    "ps vita",
    "ps2",
    "ps3",
    "ps4",
    "ps5",
    "switch",
    "wii",
    "wii u",
    "3ds",
    "2ds",
    "ds",
    "ds lite",
    "dsi",
    "nes",
    "snes",
    "videojuego",
    "videojuegos",
    "arcade",
    "pong",
    "tele-sports",
    "telesports",
    "soundic",
    "radofin",
    "neo geo",
    "pc engine",
    "turbografx",
    "colecovision",
    "intellivision",
    "odyssey",
    "vectrex",
    "nintento",
    "nitendo",
    "nintendoo",
    "playsation",
    "playstaton",
    "pley station",
    "gamevoi",
]

FRANCHISE_SIGNAL_TERMS = [
    "mario",
    "pokemon",
    "zelda",
    "kirby",
    "sonic",
    "donkey kong",
    "metroid",
]

GENERIC_SIGNAL_TERMS = [
    "joystick",
    "joysticks",
    "joistick",
    "joisticks",
    "gamepad",
    "control",
    "controles",
    "dualshock",
    "dualsense",
    "joy-con",
    "wiimote",
    "cartucho",
    "cartuchos",
]

RISK_TERMS = [
    "no prende",
    "no funciona",
    "sin probar",
    "sin testear",
    "para repuesto",
    "repuesto",
    "pantalla rota",
    "roto",
    "fall",
    "detalle",
    "sin cargador",
    "sin cables",
    "sin cable",
    "incompleto",
    "faltantes",
    "no se probó",
    "no probado",
    "a la vista",
]

POSITIVE_TERMS = [
    "funciona",
    "funcionando",
    "prende",
    "con cables",
    "con cargador",
    "original",
    "caja",
    "completo",
]

_NORMALIZED_STRONG_TERMS = []
_NORMALIZED_FRANCHISE_TERMS = []
_NORMALIZED_GENERIC_TERMS = []
_NORMALIZED_RISK_TERMS = []
_NORMALIZED_POSITIVE_TERMS = []
_CONTEXTUAL_RESTRICTED_TERMS = {"control", "controles"}
_CARTRIDGE_TERMS = {"cartucho", "cartuchos"}
_FAMILY_TERMS = {"family"}
_SATURN_TERMS = {"saturn"}
_AMBIGUOUS_PLATFORM_TERMS = {"odyssey", "terminator"}
_SPECIFIC_TERMS = {
    "wii u": {"wii"},
    "ds lite": {"ds"},
    "game boy advance": {"game boy"},
    "game boy advance sp": {"game boy", "game boy advance"},
    "game boy color": {"game boy"},
    "game boy pocket": {"game boy"},
    "game boy micro": {"game boy"},
}
_PING_PONG_PATTERN = re.compile(r"\bpin(?:g)?[\s-]+pong\b", re.IGNORECASE)
_NETWORK_SWITCH_TERMS = [
    "switch para red",
    "switch de red",
    "network switch",
    "ethernet",
    "gigabit",
    "fast ethernet",
    "puerto poe",
    "puertos poe",
    "puerto rj45",
    "puertos rj45",
    "router",
    "rack",
    "tp-link",
    "tplink",
    "cisco",
    "ubiquiti",
    "switch hdmi",
    "hdmi switch",
    "selector hdmi",
    "conmutador hdmi",
    "splitter hdmi",
    "hdmi 3x1",
    "hdmi 5x1",
]
_GAMING_CONTEXT_TERMS = [
    "consola",
    "consolas",
    "juego",
    "juegos",
    "video juego",
    "video juegos",
    "videojuego",
    "videojuegos",
    "gaming",
    "retro gaming",
    "atari",
    "nintendo",
    "playstation",
    "play station",
    "xbox",
    "sega",
    "game boy",
    "gameboy",
    "famicom",
    "family game",
    "mega drive",
    "genesis",
    "dreamcast",
    "master system",
    "game gear",
    "game cube",
    "gamecube",
    "n64",
    "nes",
    "snes",
    "ps1",
    "ps2",
    "ps3",
    "ps4",
    "ps5",
    "psp",
    "ps vita",
    "wii",
    "arcade",
    "pong",
    "tele-sports",
    "telesports",
    "soundic",
    "radofin",
    "supersportic",
]
_MARIO_CONTEXT_TERMS = [
    "nintendo",
    "switch",
    "wii",
    "wii u",
    "game boy",
    "gameboy",
    "gba",
    "gbc",
    "game cube",
    "gamecube",
    "n64",
    "super nintendo",
    "family",
    "famicom",
    "3ds",
    "2ds",
    "ds",
    "ds lite",
    "dsi",
    "nes",
    "snes",
    "super mario",
    "mario bros",
    "mario brothers",
    "new super mario",
    "mario kart",
    "paper mario",
    "mario party",
    "mario maker",
    "mario odyssey",
    "mario galaxy",
    "mario world",
    "mario wonder",
    "mario land",
    "mario rpg",
    "mario tennis",
    "mario golf",
    "mario strikers",
    "dr mario",
    "dr. mario",
]
_NORMALIZED_MARIO_CONTEXT_TERMS = []
_NORMALIZED_GAMING_CONTEXT_TERMS = []
_NORMALIZED_NETWORK_SWITCH_TERMS = []


def normalize_text(text: str) -> str:
    text = text or ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def compile_patterns(keywords: Iterable[str]) -> dict[str, re.Pattern[str]]:
    patterns: dict[str, re.Pattern[str]] = {}
    for kw in keywords:
        base = normalize_text(kw)
        if not base:
            continue
        if base in {"playstation", "play station"}:
            patterns[kw] = re.compile(r"\bplay\s*-?\s*station\b|\bplaystation\b", re.IGNORECASE)
        elif base == "xbox":
            patterns[kw] = re.compile(r"\bx\s*-?\s*box\b|\bxbox\b", re.IGNORECASE)
        elif base == "game boy":
            patterns[kw] = re.compile(r"\bgame\s*-?\s*boy\b|\bgameboy\b", re.IGNORECASE)
        elif base in {"game cube", "gamecube"}:
            patterns[kw] = re.compile(r"\bgame\s*-?\s*cube\b|\bgamecube\b", re.IGNORECASE)
        elif base == "ps vita":
            patterns[kw] = re.compile(r"\bps\s*-?\s*vita\b|\bpsvita\b", re.IGNORECASE)
        elif base == "sega genesis":
            patterns[kw] = re.compile(r"\bsega[\s-]+genesis\b", re.IGNORECASE)
        elif base in {"joy-con", "joycon"}:
            patterns[kw] = re.compile(r"\bjoy\s*-?\s*con\b|\bjoycon\b", re.IGNORECASE)
        elif base == "dualshock":
            patterns[kw] = re.compile(r"\bdual\s*-?\s*shock\b|\bdualshock\b", re.IGNORECASE)
        elif len(base) <= 3:
            patterns[kw] = re.compile(rf"\b{re.escape(base)}\b", re.IGNORECASE)
        else:
            escaped = re.escape(base).replace(r"\ ", r"\s+")
            patterns[kw] = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    return patterns


def matched_terms(text: str, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    text_n = normalize_text(text)
    hits = [raw for raw, pattern in patterns.items() if pattern.search(text_n)]
    hits = _filter_contextual_hits(text_n, hits)
    hits = _drop_shadowed_hits(hits)

    other_hits = [
        hit for hit in hits if normalize_text(hit) not in _CONTEXTUAL_RESTRICTED_TERMS
    ]
    if other_hits:
        return hits

    return [
        hit for hit in hits if normalize_text(hit) not in _CONTEXTUAL_RESTRICTED_TERMS
    ]


def _has_mario_context(text_n: str) -> bool:
    _normalize_terms_once()
    return any(_contains_term(text_n, term) for term in _NORMALIZED_MARIO_CONTEXT_TERMS)


def _contains_term(text_n: str, term_n: str) -> bool:
    escaped = re.escape(term_n).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<!\w){escaped}(?!\w)", text_n))


def _has_gaming_context(text_n: str) -> bool:
    _normalize_terms_once()
    return any(_contains_term(text_n, term) for term in _NORMALIZED_GAMING_CONTEXT_TERMS)


def _has_gaming_context_excluding(text_n: str, excluded_terms: set[str]) -> bool:
    _normalize_terms_once()
    return any(
        term not in excluded_terms and _contains_term(text_n, term)
        for term in _NORMALIZED_GAMING_CONTEXT_TERMS
    )


def _looks_like_network_switch(text_n: str) -> bool:
    _normalize_terms_once()
    return any(_contains_term(text_n, term) for term in _NORMALIZED_NETWORK_SWITCH_TERMS)


def _filter_contextual_hits(text_n: str, hits: list[str]) -> list[str]:
    filtered_hits = hits[:]
    normalized_hits = {normalize_text(hit) for hit in filtered_hits}

    if "mario" in normalized_hits and not _has_mario_context(text_n):
        filtered_hits = [hit for hit in filtered_hits if normalize_text(hit) != "mario"]

    if normalized_hits.intersection(_FAMILY_TERMS) and not any(
        _contains_term(text_n, term)
        for term in ("family game", "family computer", "family fc", "famicom", "famiclone", "consola")
    ):
        filtered_hits = [hit for hit in filtered_hits if normalize_text(hit) not in _FAMILY_TERMS]

    if normalized_hits.intersection(_SATURN_TERMS) and not _has_gaming_context_excluding(text_n, _SATURN_TERMS):
        filtered_hits = [hit for hit in filtered_hits if normalize_text(hit) not in _SATURN_TERMS]

    if normalized_hits.intersection(_AMBIGUOUS_PLATFORM_TERMS) and not _has_gaming_context(text_n):
        filtered_hits = [
            hit for hit in filtered_hits if normalize_text(hit) not in _AMBIGUOUS_PLATFORM_TERMS
        ]

    if "switch" in normalized_hits and _looks_like_network_switch(text_n):
        # A gaming signal elsewhere in the same lot wins over networking vocabulary;
        # this keeps descriptions such as "Nintendo Switch con cable ethernet" valid.
        if not _has_gaming_context_excluding(text_n, {"switch"}):
            filtered_hits = [hit for hit in filtered_hits if normalize_text(hit) != "switch"]

    if "ds" in normalized_hits:
        other_hits = normalized_hits - {"ds"}
        if not other_hits and not _has_gaming_context_excluding(text_n, {"ds"}):
            filtered_hits = [hit for hit in filtered_hits if normalize_text(hit) != "ds"]

    if "pong" in normalized_hits and _PING_PONG_PATTERN.search(text_n):
        other_hits = normalized_hits - {"pong"}
        if not other_hits and not _has_gaming_context_excluding(text_n, {"pong"}):
            filtered_hits = [hit for hit in filtered_hits if normalize_text(hit) != "pong"]

    if normalized_hits.intersection(_CARTRIDGE_TERMS) and not _has_gaming_context(text_n):
        filtered_hits = [
            hit for hit in filtered_hits if normalize_text(hit) not in _CARTRIDGE_TERMS
        ]

    return filtered_hits


def _drop_shadowed_hits(hits: list[str]) -> list[str]:
    normalized_hits = {normalize_text(hit) for hit in hits}
    shadowed = {
        parent
        for specific, parents in _SPECIFIC_TERMS.items()
        if specific in normalized_hits
        for parent in parents
    }
    return [hit for hit in hits if normalize_text(hit) not in shadowed]


def _normalize_terms_once() -> None:
    global _NORMALIZED_STRONG_TERMS
    global _NORMALIZED_FRANCHISE_TERMS
    global _NORMALIZED_GENERIC_TERMS
    global _NORMALIZED_RISK_TERMS
    global _NORMALIZED_POSITIVE_TERMS
    global _NORMALIZED_MARIO_CONTEXT_TERMS
    global _NORMALIZED_GAMING_CONTEXT_TERMS
    global _NORMALIZED_NETWORK_SWITCH_TERMS

    if _NORMALIZED_STRONG_TERMS:
        return

    _NORMALIZED_STRONG_TERMS = [normalize_text(term) for term in STRONG_SIGNAL_TERMS]
    _NORMALIZED_FRANCHISE_TERMS = [normalize_text(term) for term in FRANCHISE_SIGNAL_TERMS]
    _NORMALIZED_GENERIC_TERMS = [normalize_text(term) for term in GENERIC_SIGNAL_TERMS]
    _NORMALIZED_RISK_TERMS = [normalize_text(term) for term in RISK_TERMS]
    _NORMALIZED_POSITIVE_TERMS = [normalize_text(term) for term in POSITIVE_TERMS]
    _NORMALIZED_MARIO_CONTEXT_TERMS = [normalize_text(term) for term in _MARIO_CONTEXT_TERMS]
    _NORMALIZED_GAMING_CONTEXT_TERMS = [normalize_text(term) for term in _GAMING_CONTEXT_TERMS]
    _NORMALIZED_NETWORK_SWITCH_TERMS = [normalize_text(term) for term in _NETWORK_SWITCH_TERMS]


def collect_flags(description: str) -> tuple[list[str], list[str]]:
    _normalize_terms_once()
    desc_n = normalize_text(description)
    risk_flags = [term for term, term_n in zip(RISK_TERMS, _NORMALIZED_RISK_TERMS) if term_n in desc_n]
    positive_flags = [term for term, term_n in zip(POSITIVE_TERMS, _NORMALIZED_POSITIVE_TERMS) if term_n in desc_n]
    return risk_flags, positive_flags


def score_match(
    description: str,
    hits: list[str],
    risk_flags: list[str],
    positive_flags: list[str],
    market_value: float = 0.0,
    number_of_bids: int = 0,
) -> int:
    _normalize_terms_once()
    hit_terms = {normalize_text(hit) for hit in hits}

    score = len(set(hits)) * 6
    if any(term in hit_terms for term in _NORMALIZED_STRONG_TERMS):
        score += 12
    if any(term in hit_terms for term in _NORMALIZED_FRANCHISE_TERMS):
        score += 8
    if any(term in hit_terms for term in _NORMALIZED_GENERIC_TERMS):
        score += 4
    if market_value > 0:
        score += 2
    if number_of_bids > 0:
        score += 2
    score += min(len(positive_flags) * 2, 6)
    score -= len(risk_flags) * 8
    return score
