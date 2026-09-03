"""Remotes auction source backed by its public RSS and JSON-LD metadata.

The RSS feed is the discovery source: one request returns every published
auction and lot.  Price/commission enrichment is deliberately separate so the
scanner can call it only for lots that survived its first relevance filter.

Remotes auction pages also contain a live runtime state blob.  This adapter
never parses, returns, stores or logs that blob.  Enrichment streams only the
HTML ``<head>`` and accepts the public schema.org JSON-LD plus selected Open
Graph fields.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
import re
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from xml.etree import ElementTree

import requests

from .model import AuctionGroup, AuctionLot, GroupReceipt, SourceScanResult


SOURCE_ID = "remotes"
SOURCE_LABEL = "Remotes"

BASE_URL = "https://www.remotes.com.uy"
FEED_URL = f"{BASE_URL}/feed/publicados"
_USER_AGENT = "consolas-auction-watch/1.0 (+local personal collection monitor)"
_MAX_FEED_BYTES = 32 * 1024 * 1024
_MAX_METADATA_HEAD_BYTES = 12 * 1024 * 1024

_GROUP_ID_RE = re.compile(r"/participar/remate/([^/?#]+)", re.IGNORECASE)
_SAFE_GROUP_ID_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_COMMISSION_RE = re.compile(
    r"comisi[oó]n[^0-9%]{0,60}([0-9]+(?:[.,][0-9]+)?)\s*%",
    re.IGNORECASE,
)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _element_text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return _clean_text("".join(element.itertext()))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _child_text(element: ElementTree.Element, name: str) -> str:
    return _element_text(_child(element, name))


def _parse_optional_number(value: Any) -> float | None:
    """Parse JSON numbers and common Spanish/English formatted numbers."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None

    raw = re.sub(r"[^0-9,\.\-+]", "", str(value).strip())
    if not raw or raw in {"-", "+", ".", ","}:
        return None

    comma = raw.rfind(",")
    dot = raw.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal = "," if comma > dot else "."
        thousands = "." if decimal == "," else ","
        raw = raw.replace(thousands, "").replace(decimal, ".")
    elif comma >= 0:
        raw = _normalize_single_separator(raw, ",")
    elif dot >= 0:
        raw = _normalize_single_separator(raw, ".")

    try:
        number = float(raw)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _normalize_single_separator(raw: str, separator: str) -> str:
    pieces = raw.split(separator)
    if len(pieces) == 2:
        decimals = len(pieces[1])
        # One/two digits are decimal in both common locales. Six decimal
        # digits are also emitted by some auction backends (e.g. 350.000000).
        if decimals in {1, 2} or decimals > 3:
            return ".".join(pieces)
        if decimals == 3 and pieces[0].lstrip("+-"):
            return "".join(pieces)
        return ".".join(pieces)

    # Repeated separators with three-digit groups are thousands separators.
    if all(len(piece) == 3 for piece in pieces[1:]):
        return "".join(pieces)

    # Otherwise keep the last separator as decimal and remove the others.
    return "".join(pieces[:-1]) + "." + pieces[-1]


def _number(value: Any) -> float:
    parsed = _parse_optional_number(value)
    return parsed if parsed is not None else 0.0


def _currency(value: Any, default: str = "UYU") -> str:
    normalized = _clean_text(value).upper()
    if normalized in {"", "$", "$U", "$U.", "UYU", "PESO", "PESOS"}:
        return default
    if normalized in {"US$", "U$S", "USD", "DOLAR", "DOLARES", "DÓLAR", "DÓLARES"}:
        return "USD"
    return normalized[:8]


def _event_at(value: Any) -> str:
    number = _parse_optional_number(value)
    if number is None:
        return ""
    if abs(number) >= 1_000_000_000_000:
        number /= 1000.0
    try:
        parsed = datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    # Reject values that are almost certainly placeholders or corrupt data.
    if not 2000 <= parsed.year <= 2200:
        return ""
    return parsed.isoformat().replace("+00:00", "Z")


def _group_id(url: str, title: str = "", event_at: str = "") -> str:
    match = _GROUP_ID_RE.search(urlsplit(url).path)
    if match:
        return match.group(1)
    digest = sha256(f"{url}\0{title}\0{event_at}".encode("utf-8")).hexdigest()[:16]
    return f"rss-{digest}"


def _lot_number(url: str) -> str:
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    for key, values in query.items():
        if key.casefold() == "lote" and values:
            return _clean_text(values[0])
    return ""


def _fallback_lot_id(group_id: str, url: str, title: str) -> str:
    digest = sha256(f"{url}\0{title}".encode("utf-8")).hexdigest()[:16]
    return f"{group_id}:rss-{digest}"


def _parse_feed(payload: bytes | str) -> tuple[list[AuctionGroup], list[AuctionLot]]:
    if isinstance(payload, bytes):
        if len(payload) > _MAX_FEED_BYTES:
            raise ValueError("RSS de Remotes supera el tamaño permitido")
        lowered = payload[:4096].lower()
        xml_payload = payload
    else:
        xml_payload = payload.encode("utf-8")
        if len(xml_payload) > _MAX_FEED_BYTES:
            raise ValueError("RSS de Remotes supera el tamaño permitido")
        lowered = payload[:4096].lower().encode("utf-8", errors="ignore")

    # ElementTree does not fetch external entities, but rejecting declarations
    # also prevents internal entity-expansion payloads from being accepted.
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("RSS de Remotes contiene declaraciones XML no admitidas")

    try:
        root = ElementTree.fromstring(xml_payload)
    except ElementTree.ParseError as exc:
        raise ValueError("RSS de Remotes inválido") from exc

    channel = next((node for node in root.iter() if _local_name(node.tag) == "channel"), None)
    if channel is None:
        raise ValueError("RSS de Remotes sin canal")

    groups: list[AuctionGroup] = []
    lots: list[AuctionLot] = []
    seen_groups: set[str] = set()
    seen_lots: set[str] = set()

    for item in _children(channel, "item"):
        title = _child_text(item, "title") or "Remate sin título"
        raw_url = _child_text(item, "link")
        url = urljoin(BASE_URL, raw_url)
        event_at = _event_at(_child_text(item, "fecha"))
        group_id = _group_id(url, title, event_at)
        if group_id in seen_groups:
            continue
        seen_groups.add(group_id)

        location = _child_text(item, "description")
        declared_lot_count = int(_number(_child_text(item, "cantLotes")))
        group = AuctionGroup(
            source_id=SOURCE_ID,
            group_id=group_id,
            title=title,
            url=url,
            event_at=event_at,
            closing_at="",
            commission_percent=0.0,
            currency="UYU",
            location=location,
            status="active",
            extra={
                "discovery_method": "public_rss",
                "declared_lot_count": declared_lot_count,
            },
        )
        groups.append(group)

        lots_parent = _child(item, "lotes")
        if lots_parent is None:
            continue
        for position, lot_node in enumerate(_children(lots_parent, "lote"), start=1):
            lot_title = _child_text(lot_node, "title") or "Lote sin título"
            raw_lot_url = _child_text(lot_node, "link")
            lot_url = urljoin(url, raw_lot_url)
            lot_number = _lot_number(lot_url)
            lot_id = (
                f"{group_id}:{lot_number}"
                if lot_number
                else _fallback_lot_id(group_id, lot_url, lot_title)
            )
            if lot_id in seen_lots:
                continue
            seen_lots.add(lot_id)

            image_url = urljoin(BASE_URL, _child_text(lot_node, "foto"))
            lots.append(
                AuctionLot(
                    source_id=SOURCE_ID,
                    source_label=SOURCE_LABEL,
                    group_id=group_id,
                    group_label=title,
                    group_url=url,
                    lot_id=lot_id,
                    lot_number=lot_number or str(position),
                    title=lot_title,
                    description=_child_text(lot_node, "description"),
                    lot_url=lot_url,
                    image_url=image_url,
                    currency="UYU",
                    base_price=0.0,
                    current_price=0.0,
                    next_bid=0.0,
                    commission_percent=0.0,
                    packaging_cost=0.0,
                    bid_count=0,
                    event_at=event_at,
                    closing_at="",
                    status="active",
                    extra={
                        "discovery_method": "public_rss",
                        "metadata_status": "not_requested",
                    },
                )
            )

    return groups, lots


class _PublicMetadataParser(HTMLParser):
    """Keep only JSON-LD and allow-listed metadata from the document head."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.json_ld: list[str] = []
        self.meta: dict[str, str] = {}
        self.head_complete = False
        self._capture_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered_tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if lowered_tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").casefold()
            if key in {"og:title", "og:description"}:
                self.meta[key] = _clean_text(attributes.get("content"))
        elif lowered_tag == "script":
            script_type = attributes.get("type", "").split(";", 1)[0].strip().casefold()
            self._capture_json_ld = script_type == "application/ld+json"
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture_json_ld:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.casefold()
        if lowered_tag == "script" and self._capture_json_ld:
            self.json_ld.append("".join(self._json_ld_parts))
            self._capture_json_ld = False
            self._json_ld_parts = []
        elif lowered_tag == "head":
            self.head_complete = True


@dataclass(frozen=True)
class _ProductMetadata:
    lot_number: str
    title: str = ""
    description: str = ""
    lot_url: str = ""
    image_url: str = ""
    currency: str = "UYU"
    displayed_price: float | None = None
    status: str = "active"


@dataclass(frozen=True)
class _AuctionMetadata:
    products: dict[str, _ProductMetadata]
    commission_percent: float | None = None
    warnings: tuple[str, ...] = ()


def _iter_json_ld_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _iter_json_ld_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_ld_nodes(child)


def _schema_types(value: Any) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {_clean_text(item).casefold() for item in values if item is not None}


def _first_offer(product: dict[str, Any]) -> dict[str, Any]:
    offers = product.get("offers")
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list):
        return next((item for item in offers if isinstance(item, dict)), {})
    return {}


def _first_image(value: Any) -> str:
    if isinstance(value, str):
        return urljoin(BASE_URL, value)
    if isinstance(value, list):
        for item in value:
            image = _first_image(item)
            if image:
                return image
        return ""
    if isinstance(value, dict):
        return urljoin(BASE_URL, _clean_text(value.get("url") or value.get("contentUrl")))
    return ""


def _product_lot_number(product: dict[str, Any], group_id: str) -> str:
    offer = _first_offer(product)
    for candidate_url in (offer.get("url"), product.get("url")):
        number = _lot_number(_clean_text(candidate_url))
        if number:
            return number

    sku = _clean_text(product.get("sku"))
    sku_match = re.match(
        rf"^LOTE[-_]{re.escape(group_id)}[-_](.+)$",
        sku,
        flags=re.IGNORECASE,
    )
    return _clean_text(sku_match.group(1)) if sku_match else ""


def _strip_lot_prefix(title: str, lot_number: str) -> str:
    prefix = re.compile(
        rf"^\s*lote\s*#?\s*{re.escape(lot_number)}\s*[-–—:]\s*",
        flags=re.IGNORECASE,
    )
    return _clean_text(prefix.sub("", title)) or _clean_text(title)


def _status_from_availability(value: Any) -> str:
    availability = _clean_text(value).rsplit("/", 1)[-1].casefold()
    if availability in {"outofstock", "soldout", "discontinued"}:
        return "closed"
    return "active"


def _metadata_from_parser(parser: _PublicMetadataParser, group_id: str) -> _AuctionMetadata:
    products: dict[str, _ProductMetadata] = {}
    warnings: list[str] = []

    if not parser.json_ld:
        warnings.append("JSON-LD ausente")

    for raw_json_ld in parser.json_ld:
        try:
            document = json.loads(raw_json_ld)
        except (TypeError, ValueError):
            warnings.append("JSON-LD inválido")
            continue

        for node in _iter_json_ld_nodes(document):
            if "product" not in _schema_types(node.get("@type")):
                continue
            number = _product_lot_number(node, group_id)
            if not number:
                continue
            offer = _first_offer(node)
            offer_url = urljoin(BASE_URL, _clean_text(offer.get("url") or node.get("url")))
            product = _ProductMetadata(
                lot_number=number,
                title=_strip_lot_prefix(_clean_text(node.get("name")), number),
                description=_clean_text(node.get("description")),
                lot_url=offer_url,
                image_url=_first_image(node.get("image")),
                currency=_currency(offer.get("priceCurrency")),
                displayed_price=_parse_optional_number(offer.get("price")),
                status=_status_from_availability(offer.get("availability")),
            )
            products.setdefault(number.casefold(), product)

    if parser.json_ld and not products:
        warnings.append("JSON-LD sin productos")

    commission: float | None = None
    description = parser.meta.get("og:description", "")
    match = _COMMISSION_RE.search(description)
    if match:
        commission = _parse_optional_number(match.group(1))

    return _AuctionMetadata(
        products=products,
        commission_percent=commission,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _canonical_metadata_url(group_id: str) -> str:
    if not _SAFE_GROUP_ID_RE.fullmatch(group_id):
        raise ValueError("identificador de remate inválido")
    return f"{BASE_URL}/participar/remate/{quote(group_id, safe='')}"


def _response_metadata(
    session: requests.Session,
    group_id: str,
    timeout: float,
) -> _AuctionMetadata:
    """Stream and parse only the public metadata in the document head."""

    url = _canonical_metadata_url(group_id)
    response = session.get(
        url,
        timeout=timeout,
        stream=True,
        headers={
            "Accept": "text/html, application/xhtml+xml;q=0.9",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        response.raise_for_status()
        parser = _PublicMetadataParser()
        encoding = response.encoding or "utf-8"
        try:
            decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        except LookupError:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        bytes_seen = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            if isinstance(chunk, str):
                bytes_seen += len(chunk.encode("utf-8"))
                decoded = chunk
            else:
                bytes_seen += len(chunk)
                decoded = decoder.decode(chunk)
            if bytes_seen > _MAX_METADATA_HEAD_BYTES:
                raise ValueError("encabezado HTML demasiado grande")
            parser.feed(decoded)
            if parser.head_complete:
                break
        parser.close()
        return _metadata_from_parser(parser, group_id)
    finally:
        response.close()


def _safe_request_error(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "error de conexión"
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        return f"HTTP {response.status_code}" if response is not None else "error HTTP"
    if isinstance(exc, ValueError):
        return _clean_text(exc)[:160] or "metadatos inválidos"
    return exc.__class__.__name__


def _with_metadata_error(lot: AuctionLot, error: str) -> AuctionLot:
    extra = dict(lot.extra or {})
    extra.update({"metadata_status": "unavailable", "metadata_error": error})
    return replace(lot, extra=extra)


def _with_metadata(lot: AuctionLot, metadata: _AuctionMetadata) -> AuctionLot:
    product = metadata.products.get(lot.lot_number.casefold())
    extra = dict(lot.extra or {})
    extra["metadata_status"] = "enriched" if product else "lot_not_found"
    extra["metadata_source"] = "schema_org_json_ld"
    if metadata.warnings:
        extra["metadata_warnings"] = list(metadata.warnings)

    if product is None:
        return replace(
            lot,
            commission_percent=(
                metadata.commission_percent
                if metadata.commission_percent is not None
                else lot.commission_percent
            ),
            extra=extra,
        )

    extra["price_kind"] = "displayed_schema_offer"
    extra["base_price_status"] = "not_exposed_in_safe_metadata"
    extra["packaging_cost_status"] = "not_exposed_in_safe_metadata"
    metadata_lot_url = product.lot_url if _lot_number(product.lot_url) else ""
    return replace(
        lot,
        title=product.title or lot.title,
        description=product.description or lot.description,
        # Some live JSON-LD offers point only to the Remotes home page. Never
        # let that less-specific metadata replace the direct RSS lot link.
        lot_url=metadata_lot_url or lot.lot_url,
        image_url=product.image_url or lot.image_url,
        currency=product.currency or lot.currency,
        current_price=(
            product.displayed_price
            if product.displayed_price is not None
            else lot.current_price
        ),
        commission_percent=(
            metadata.commission_percent
            if metadata.commission_percent is not None
            else lot.commission_percent
        ),
        status=product.status or lot.status,
        extra=extra,
    )


class RemotesSource:
    source_id = SOURCE_ID
    label = SOURCE_LABEL

    def __init__(self) -> None:
        self.last_enrichment_errors: list[str] = []
        self.last_enrichment_warnings: list[str] = []

    def collect(
        self,
        session: requests.Session,
        timeout: float = 25,
    ) -> SourceScanResult:
        """Discover all published auctions/lots with one public RSS request."""

        errors: list[str] = []
        groups: list[AuctionGroup] = []
        lots: list[AuctionLot] = []
        response: requests.Response | None = None
        try:
            response = session.get(
                FEED_URL,
                timeout=timeout,
                headers={
                    "Accept": "application/rss+xml, application/xml;q=0.9",
                    "User-Agent": _USER_AGENT,
                },
            )
            response.raise_for_status()
            payload = response.content
            groups, lots = _parse_feed(payload)
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"feed: {_safe_request_error(exc)}")
        finally:
            if response is not None:
                response.close()

        lots_by_group: dict[str, int] = {}
        for lot in lots:
            lots_by_group[lot.group_id] = lots_by_group.get(lot.group_id, 0) + 1
        finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        receipts = [
            GroupReceipt(
                group_id=group.group_id,
                status=(
                    "complete"
                    if int(group.extra.get("declared_lot_count") or 0)
                    == lots_by_group.get(group.group_id, 0)
                    else "partial"
                ),
                lot_count=lots_by_group.get(group.group_id, 0),
                error_count=(
                    0
                    if int(group.extra.get("declared_lot_count") or 0)
                    == lots_by_group.get(group.group_id, 0)
                    else 1
                ),
                started_at=finished_at,
                finished_at=finished_at,
            )
            for group in groups
        ]
        discovery_complete = not errors and all(
            receipt.status == "complete" for receipt in receipts
        )

        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=groups,
            lots=lots,
            errors=errors,
            receipts=receipts,
            discovery_complete=discovery_complete,
        )

    def enrich_lots(
        self,
        session: requests.Session,
        lots: list[AuctionLot],
        timeout: float = 25,
    ) -> list[AuctionLot]:
        """Refresh safe public metadata for candidate lots only.

        Candidates are grouped by auction, resulting in at most one HTTP GET
        per represented auction. Failures preserve the RSS lot unchanged apart
        from a sanitized metadata error in ``extra``.
        """

        self.last_enrichment_errors = []
        self.last_enrichment_warnings = []
        grouped_indexes: dict[str, list[int]] = {}
        for index, lot in enumerate(lots):
            grouped_indexes.setdefault(lot.group_id, []).append(index)

        enriched = list(lots)
        for group_id, indexes in grouped_indexes.items():
            try:
                metadata = _response_metadata(session, group_id, timeout)
            except (requests.RequestException, ValueError) as exc:
                safe_error = _safe_request_error(exc)
                detail = f"remate {group_id}: {safe_error}"
                # The RSS feed already supplied the complete active lot and
                # candidate set. A detail-page rate limit only withholds
                # optional price/commission metadata, so keep coverage healthy
                # and surface it as a warning instead of a partial source.
                if safe_error == "HTTP 429":
                    self.last_enrichment_warnings.append(detail)
                else:
                    self.last_enrichment_errors.append(detail)
                for index in indexes:
                    enriched[index] = _with_metadata_error(enriched[index], safe_error)
                continue

            if metadata.warnings:
                warnings = ", ".join(metadata.warnings)
                self.last_enrichment_warnings.append(f"remate {group_id}: {warnings}")
            for index in indexes:
                enriched[index] = _with_metadata(enriched[index], metadata)

        return enriched


def scan(
    session: requests.Session | None = None,
    timeout: float = 20,
) -> SourceScanResult:
    """Small functional wrapper for registries that prefer module functions."""

    owns_session = session is None
    active_session = session or requests.Session()
    try:
        return RemotesSource().collect(active_session, timeout=timeout)
    finally:
        if owns_session:
            active_session.close()


__all__ = [
    "FEED_URL",
    "RemotesSource",
    "SOURCE_ID",
    "SOURCE_LABEL",
    "scan",
]
