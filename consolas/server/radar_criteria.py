"""Collection Radar: write checks, sources and search criteria normalization."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

from radar.sources import registry as radar_registry

try:
    from .constants import (
        RADAR_COMPLETENESS,
        RADAR_CONDITIONS,
        RADAR_DEFAULT_RESULT_LIMIT,
        RADAR_DEFAULT_SOURCE,
        RADAR_MAX_QUERIES,
        RADAR_MAX_QUERY_LENGTH,
        RADAR_MAX_RESULT_LIMIT,
        RADAR_MAX_TERMS,
        RADAR_MAX_TERM_LENGTH,
        RADAR_MAX_TITLE_ALIASES,
        RADAR_MAX_TITLE_NAME_LENGTH,
        RADAR_MAX_TITLE_TARGETS,
        RADAR_REGIONS,
        RADAR_REQUIREMENT_LEVELS,
        RADAR_WRITE_HEADER,
    )
    from .errors import ApiError
except ImportError:  # Direct execution: `python3 server/app.py`.
    from constants import (
        RADAR_COMPLETENESS,
        RADAR_CONDITIONS,
        RADAR_DEFAULT_RESULT_LIMIT,
        RADAR_DEFAULT_SOURCE,
        RADAR_MAX_QUERIES,
        RADAR_MAX_QUERY_LENGTH,
        RADAR_MAX_RESULT_LIMIT,
        RADAR_MAX_TERMS,
        RADAR_MAX_TERM_LENGTH,
        RADAR_MAX_TITLE_ALIASES,
        RADAR_MAX_TITLE_NAME_LENGTH,
        RADAR_MAX_TITLE_TARGETS,
        RADAR_REGIONS,
        RADAR_REQUIREMENT_LEVELS,
        RADAR_WRITE_HEADER,
    )
    from errors import ApiError


def require_radar_write_request(handler: BaseHTTPRequestHandler) -> None:
    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type application/json is required")
    if handler.headers.get(RADAR_WRITE_HEADER) != "1":
        raise ApiError(HTTPStatus.FORBIDDEN, "Collection Radar action header is required")


def radar_sources_payload() -> list[dict[str, Any]]:
    """Descripción pública de cada fuente registrada y sus capabilities."""
    return radar_registry.sources_payload()


def executable_radar_sources(source_ids: list[str]) -> list[str]:
    return radar_registry.executable_source_ids(source_ids)


def radar_source_capabilities(source_id: str) -> dict[str, Any]:
    spec = radar_registry.get_source(source_id)
    return dict(spec.capabilities) if spec else {}


def radar_source_label(source_id: str) -> str:
    spec = radar_registry.get_source(source_id)
    return spec.label if spec else str(source_id or "")


def default_radar_criteria() -> dict[str, Any]:
    return {
        "includeTerms": [],
        "anyTerms": [],
        "excludeTerms": [],
        "preferTerms": [],
        "queries": [],
        "titleTargets": [],
        "region": "",
        "excludeRegions": [],
        "condition": "any",
        "completeness": "any",
        "tested": "any",
        "originalParts": "any",
        "returnsRequired": False,
        "freeShippingOnly": False,
        "currency": "USD",
        "maxItemPrice": None,
        "maxTotalUsa": None,
        "targetItemPrice": None,
        "minLotSize": None,
        "maxPricePerPiece": None,
        "resultLimit": RADAR_DEFAULT_RESULT_LIMIT,
    }


def normalize_radar_id(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate or len(candidate) > 100 or not all(char.isalnum() or char in {"-", "_"} for char in candidate):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid radar search id")
    return candidate


def normalize_radar_text(value: Any, field: str, limit: int, *, required: bool = True) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text and required:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} is required")
    if len(text) > limit:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} is too long")
    return text


def normalize_radar_enum(value: Any, allowed: tuple[str, ...], default: str, field: str) -> str:
    if value is None:
        return default
    candidate = str(value).strip().lower()
    if not candidate:
        return default
    if candidate not in allowed:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be one of: {', '.join(item for item in allowed if item)}")
    return candidate


def normalize_radar_entity_console_id(value: Any, entity_type: str) -> str:
    """A qué consola pertenece un `entityId` que no es una consola.

    Una consola ya es la entidad, así que no lleva consola aparte: guardarla
    igual dejaría dos fuentes para el mismo dato. Para un juego o accesorio,
    en cambio, el id solo es ambiguo — el mismo juego existe en varias
    plataformas — y sin este dato "Registrar compra" no sabría a qué
    biblioteca escribir. Se acepta vacío: una búsqueda puede existir sin estar
    vinculada al catálogo, y en ese caso simplemente no ofrece escribir nada.
    """
    console_id = normalize_radar_text(value, "entityConsoleId", 120, required=False)
    if console_id and entity_type == "console":
        raise ApiError(
            HTTPStatus.BAD_REQUEST, "entityConsoleId does not apply when entityType is console"
        )
    return console_id


def normalize_radar_terms(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items: list[Any] = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a list of terms")
    terms: list[str] = []
    for item in raw_items:
        if isinstance(item, (list, dict)):
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a list of terms")
        term = " ".join(str(item or "").split()).strip()
        if not term:
            continue
        if len(term) > RADAR_MAX_TERM_LENGTH:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} has a term that is too long")
        if term.lower() not in {existing.lower() for existing in terms}:
            terms.append(term)
    if len(terms) > RADAR_MAX_TERMS:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} accepts up to {RADAR_MAX_TERMS} terms")
    return terms


def normalize_radar_queries(value: Any) -> list[str]:
    """Consultas extra de una búsqueda, además de la principal.

    No son términos: cada una es una consulta completa que se le manda a cada
    fuente por separado, como si el owner la hubiera tecleado. Por eso admiten
    más largo que un término y se deduplican sin distinguir mayúsculas — mandar
    dos veces la misma consulta gasta cuota de la API y no agrega nada.
    """

    if value is None:
        return []
    if isinstance(value, str):
        raw_items: list[Any] = value.splitlines()
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ApiError(HTTPStatus.BAD_REQUEST, "queries must be a list of queries")
    queries: list[str] = []
    for item in raw_items:
        if isinstance(item, (list, dict)):
            raise ApiError(HTTPStatus.BAD_REQUEST, "queries must be a list of queries")
        query = " ".join(str(item or "").split()).strip()
        if not query:
            continue
        if len(query) > RADAR_MAX_QUERY_LENGTH:
            raise ApiError(HTTPStatus.BAD_REQUEST, "queries has a query that is too long")
        if query.lower() not in {existing.lower() for existing in queries}:
            queries.append(query)
    if len(queries) > RADAR_MAX_QUERIES:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"queries accepts up to {RADAR_MAX_QUERIES} queries")
    return queries


def radar_search_queries(search_query: Any, criteria: dict[str, Any]) -> list[str]:
    """La consulta principal más las extra, sin repetidas ni vacías."""
    candidates = [search_query, *(criteria.get("queries") or [])]
    queries: list[str] = []
    for candidate in candidates:
        query = " ".join(str(candidate or "").split()).strip()
        if not query or query.lower() in {existing.lower() for existing in queries}:
            continue
        queries.append(query)
    return queries


def radar_query_key(value: Any) -> str:
    """Cómo se reconoce una consulta: sin espacios de más ni mayúsculas."""
    return " ".join(str(value or "").split()).strip().lower()


def normalize_radar_title_targets(value: Any) -> list[dict[str, Any]]:
    """Qué juego persigue cada consulta de una búsqueda de varios títulos.

    Una búsqueda que persigue cuarenta títulos tenía un solo techo de precio y
    ninguna referencia por título: un Super Mario World de 95 dólares entraba
    porque el techo era 100, y se juzgaba contra la mediana de toda la corrida,
    que mezcla un Chrono Trigger con un NBA Jam. Cada objetivo dice, para una
    consulta, qué juego del catálogo es —para compararlo con su propio precio y
    para que «Registrar compra» sepa qué escribir— y hasta cuánto vale la pena
    pagarlo.

    La consola del juego no va acá: es la de la búsqueda (`entityConsoleId`).
    Un objetivo cuya consulta ya no existe en la búsqueda no rompe nada; sólo
    deja de aplicarse.

    `aliases` son otros nombres con los que se publica el mismo título: sirven
    para reconocerlo (`best_title_target`), no se mandan a la fuente. Si una
    grafía necesita su propia consulta, va en `queries`.
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError(HTTPStatus.BAD_REQUEST, "titleTargets must be a list of objects")
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "titleTargets must be a list of objects")
        query = normalize_radar_text(item.get("query"), "titleTargets.query", RADAR_MAX_QUERY_LENGTH)
        key = radar_query_key(query)
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            {
                "query": query,
                "gameId": normalize_radar_text(item.get("gameId"), "titleTargets.gameId", 120, required=False),
                "name": normalize_radar_text(
                    item.get("name"), "titleTargets.name", RADAR_MAX_TITLE_NAME_LENGTH, required=False
                ),
                "aliases": normalize_radar_title_aliases(item.get("aliases")),
                "maxItemPrice": normalize_radar_amount(item.get("maxItemPrice"), "titleTargets.maxItemPrice"),
                "maxBoxedPrice": normalize_radar_amount(item.get("maxBoxedPrice"), "titleTargets.maxBoxedPrice"),
            }
        )
    if len(targets) > RADAR_MAX_TITLE_TARGETS:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"titleTargets accepts up to {RADAR_MAX_TITLE_TARGETS} targets"
        )
    return targets


def normalize_radar_title_aliases(value: Any) -> list[str]:
    """Los otros nombres de un objetivo, sin vacíos ni repetidos."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError(HTTPStatus.BAD_REQUEST, "titleTargets.aliases must be a list of names")
    aliases: list[str] = []
    for item in value:
        if isinstance(item, (list, dict)):
            raise ApiError(HTTPStatus.BAD_REQUEST, "titleTargets.aliases must be a list of names")
        alias = normalize_radar_text(
            item, "titleTargets.aliases", RADAR_MAX_TITLE_NAME_LENGTH, required=False
        )
        if alias and alias.lower() not in {existing.lower() for existing in aliases}:
            aliases.append(alias)
    if len(aliases) > RADAR_MAX_TITLE_ALIASES:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"titleTargets.aliases accepts up to {RADAR_MAX_TITLE_ALIASES} names"
        )
    return aliases


def radar_title_target(criteria: dict[str, Any], query: Any) -> dict[str, Any] | None:
    """El objetivo declarado para esa consulta, si la búsqueda declaró uno."""
    key = radar_query_key(query)
    if not key:
        return None
    for target in criteria.get("titleTargets") or []:
        if radar_query_key(target.get("query")) == key:
            return target
    return None


def radar_query_criteria(
    criteria: dict[str, Any], query: Any, *, completeness: str | None = None
) -> dict[str, Any]:
    """Los criterios con los que se juzga lo que trajo una consulta concreta.

    El techo de un título nunca afloja el de la búsqueda: manda el más bajo de
    los dos. Así el techo general del dueño sigue siendo el límite y cada
    título sólo puede ajustarlo hacia abajo.

    Un título puede tener además un techo para copias en caja: el del catálogo
    es de copia suelta, y con él un Super Mario World CIB de 60 dólares no
    llegaba nunca. `completeness` dice qué declara la publicación; sin ella
    —cuando se le pide a la fuente— vale el más alto de los dos techos, para
    que la fuente traiga ambas y el juicio por publicación decida.
    """

    target = radar_title_target(criteria, query)
    if target is None or target.get("maxItemPrice") is None:
        return criteria
    loose = float(target["maxItemPrice"])
    boxed = target.get("maxBoxedPrice")
    if boxed is None:
        ceiling = loose
    elif completeness is None:
        ceiling = max(loose, float(boxed))
    else:
        ceiling = float(boxed) if completeness in {"cib", "boxed", "sealed"} else loose
    general = criteria.get("maxItemPrice")
    scoped = dict(criteria)
    scoped["maxItemPrice"] = ceiling if general is None else min(ceiling, float(general))
    return scoped


def normalize_radar_amount(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a number") from None
    if amount != amount or amount in {float("inf"), float("-inf")}:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a number")
    if amount < 0:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} cannot be negative")
    return round(amount, 2)


def normalize_radar_count(value: Any, field: str, maximum: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a whole number") from None
    if count < 1 or count > maximum:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be between 1 and {maximum}")
    return count


def normalize_radar_criteria(value: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the structured criteria of a search and drop unknown fields."""
    criteria = default_radar_criteria()
    criteria.update(base or {})
    if value is None:
        return criteria
    if not isinstance(value, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "criteria must be an object")
    for field in ("includeTerms", "anyTerms", "excludeTerms", "preferTerms"):
        if field in value:
            criteria[field] = normalize_radar_terms(value.get(field), field)
    if "queries" in value:
        criteria["queries"] = normalize_radar_queries(value.get("queries"))
    if "titleTargets" in value:
        criteria["titleTargets"] = normalize_radar_title_targets(value.get("titleTargets"))
    if "region" in value:
        criteria["region"] = normalize_radar_text(value.get("region"), "region", 60, required=False)
    if "excludeRegions" in value:
        regions = [term.lower() for term in normalize_radar_terms(value.get("excludeRegions"), "excludeRegions")]
        unknown = [region for region in regions if region not in RADAR_REGIONS]
        if unknown:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, f"excludeRegions must be any of: {', '.join(RADAR_REGIONS)}"
            )
        criteria["excludeRegions"] = regions
    if "condition" in value:
        criteria["condition"] = normalize_radar_enum(value.get("condition"), RADAR_CONDITIONS, "any", "condition")
    if "completeness" in value:
        criteria["completeness"] = normalize_radar_enum(
            value.get("completeness"), RADAR_COMPLETENESS, "any", "completeness"
        )
    if "tested" in value:
        criteria["tested"] = normalize_radar_enum(value.get("tested"), RADAR_REQUIREMENT_LEVELS, "any", "tested")
    if "originalParts" in value:
        criteria["originalParts"] = normalize_radar_enum(
            value.get("originalParts"), RADAR_REQUIREMENT_LEVELS, "any", "originalParts"
        )
    if "returnsRequired" in value:
        criteria["returnsRequired"] = value.get("returnsRequired") is True
    if "freeShippingOnly" in value:
        criteria["freeShippingOnly"] = value.get("freeShippingOnly") is True
    if "currency" in value:
        currency = normalize_radar_text(value.get("currency"), "currency", 8, required=False).upper()
        criteria["currency"] = currency or "USD"
    if "maxItemPrice" in value:
        criteria["maxItemPrice"] = normalize_radar_amount(value.get("maxItemPrice"), "maxItemPrice")
    if "maxTotalUsa" in value:
        criteria["maxTotalUsa"] = normalize_radar_amount(value.get("maxTotalUsa"), "maxTotalUsa")
    if "targetItemPrice" in value:
        criteria["targetItemPrice"] = normalize_radar_amount(
            value.get("targetItemPrice"), "targetItemPrice"
        )
    if "minLotSize" in value:
        criteria["minLotSize"] = normalize_radar_count(value.get("minLotSize"), "minLotSize", 500)
    if "maxPricePerPiece" in value:
        criteria["maxPricePerPiece"] = normalize_radar_amount(value.get("maxPricePerPiece"), "maxPricePerPiece")
    if "resultLimit" in value:
        criteria["resultLimit"] = (
            normalize_radar_count(value.get("resultLimit"), "resultLimit", RADAR_MAX_RESULT_LIMIT)
            or RADAR_DEFAULT_RESULT_LIMIT
        )
    return criteria


def normalize_radar_sources(value: Any) -> list[str]:
    if value is None:
        return [RADAR_DEFAULT_SOURCE]
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ApiError(HTTPStatus.BAD_REQUEST, "sources must be a list of source ids")
    sources: list[str] = []
    for item in raw_items:
        source_id = str(item or "").strip().lower()
        if not source_id:
            continue
        if radar_registry.get_source(source_id) is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"Unknown radar source: {source_id}")
        if source_id not in sources:
            sources.append(source_id)
    return sources or [RADAR_DEFAULT_SOURCE]


def build_radar_search_query(name: str, platform: str, criteria: dict[str, Any], explicit: Any = None) -> str:
    """Arma la consulta que se le manda a la fuente.

    Una consulta escrita a mano gana siempre. Si no hay, se deriva — pero el
    nombre de la búsqueda sólo entra **cuando no hay términos requeridos**.

    El nombre es una etiqueta para el usuario, no términos de búsqueda: una
    búsqueda llamada «PS2 joyas baratas» mandaba literalmente `joyas baratas` a
    eBay y volvía vacía. Cuando la búsqueda declara qué términos exige, esos
    términos son la consulta.
    """

    explicit_query = " ".join(str(explicit or "").split()).strip()
    if explicit_query:
        return explicit_query[:300]
    include_terms = list(criteria.get("includeTerms") or [])
    parts = [*include_terms, platform] if include_terms else [name, platform]
    seen: set[str] = set()
    words: list[str] = []
    for part in parts:
        candidate = " ".join(str(part or "").split()).strip()
        if not candidate or candidate.lower() in seen:
            continue
        seen.add(candidate.lower())
        words.append(candidate)
    return " ".join(words)[:300]


def radar_json_field(raw: Any, fallback: Any) -> Any:
    """Read a persisted JSON column, falling back when the row is unreadable.

    The fallback also declares the expected shape: a value of another type is a
    corrupted row and must not reach the normalizers. Pass ``{}``/``None`` for an
    object column and ``[]`` for an array one.
    """

    expected = dict if fallback is None else type(fallback)
    try:
        parsed = json.loads(str(raw or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, expected) else fallback
