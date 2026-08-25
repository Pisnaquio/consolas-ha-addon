"""API-first source adapter for Prado Subastas."""

from __future__ import annotations

import html
import re
from dataclasses import replace
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .model import AuctionGroup, AuctionLot, SourceScanResult


SOURCE_ID = "prado"
SOURCE_LABEL = "Prado Subastas"

BASE_URL = "https://pradorematesenlinea.uy"
PRODUCTS_API_URL = f"{BASE_URL}/wp-json/wc/store/v1/products"
DEFAULT_COMMISSION_PERCENT = 18.3
DEFAULT_LOCATION = "Av. Millan 3990, Montevideo"
LOCAL_TIMEZONE = ZoneInfo("America/Montevideo")
PAGE_SIZE = 100
MAX_PAGES = 100
USER_AGENT = "consolas-auction-watch/1.0 (+personal auction monitor)"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _clean_text(value: object) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(str(value or ""))
        parser.close()
        text = " ".join(parser.parts)
    except (TypeError, ValueError):
        text = str(value or "")
    return " ".join(html.unescape(text).split())


def _number(value: object) -> float:
    raw = html.unescape(str(value or "")).strip()
    if not raw:
        return 0.0
    raw = re.sub(r"[^0-9,.-]", "", raw)
    if not raw or raw in {"-", ".", ","}:
        return 0.0

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        tail = raw.rsplit(",", 1)[1]
        raw = raw.replace(",", ".") if len(tail) <= 2 else raw.replace(",", "")
    elif "." in raw:
        tail = raw.rsplit(".", 1)[1]
        if len(tail) == 3:
            raw = raw.replace(".", "")

    try:
        return float(raw)
    except ValueError:
        return 0.0


def _money_from_markup(markup: str) -> float:
    text = _clean_text(markup)
    values = re.findall(r"(?:\$|UYU)\s*([0-9][0-9.,]*)", text, re.IGNORECASE)
    return _number(values[-1]) if values else 0.0


def _lot_number(title: str, fallback: str) -> str:
    match = re.search(r"\blote\s*#?\s*([0-9]+[a-z]?)\b", title, re.IGNORECASE)
    return match.group(1).upper() if match else fallback


def _first_image(product: dict[str, Any]) -> str:
    images = product.get("images")
    if not isinstance(images, list):
        return ""
    for image in images:
        if isinstance(image, dict) and image.get("src"):
            return html.unescape(str(image["src"]))
    return ""


def _opening_tag(document: str, marker: str) -> str:
    pattern = rf"<[^>]+(?:\b{re.escape(marker)}\b)[^>]*>"
    match = re.search(pattern, document, re.IGNORECASE | re.DOTALL)
    return match.group(0) if match else ""


def _attributes(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(
        r"([:\w-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
        tag,
        re.DOTALL,
    ):
        attrs[match.group(1).lower()] = html.unescape(
            match.group(2) if match.group(2) is not None else match.group(3) or match.group(4) or ""
        )
    return attrs


def _local_iso(value: str, zone_name: str = "America/Montevideo") -> str:
    raw = html.unescape(value or "").strip()
    if not raw:
        return ""
    try:
        zone = ZoneInfo(zone_name or "America/Montevideo")
    except ZoneInfoNotFoundError:
        zone = LOCAL_TIMEZONE
    for pattern in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).replace(tzinfo=zone).isoformat(timespec="seconds")
        except ValueError:
            continue
    return ""


def _prepare_session(session: requests.Session) -> None:
    headers = getattr(session, "headers", None)
    if headers is not None:
        headers.setdefault("User-Agent", USER_AGENT)
        headers.setdefault("Accept", "application/json, text/html;q=0.9")


def _paged_json(
    session: requests.Session,
    url: str,
    params: dict[str, object],
    timeout: int | float,
) -> Iterable[list[dict[str, Any]]]:
    page = 1
    while page <= MAX_PAGES:
        query = dict(params)
        query["page"] = page
        response = session.get(url, params=query, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"respuesta JSON inesperada en {url}")
        rows = [row for row in payload if isinstance(row, dict)]
        yield rows

        headers = getattr(response, "headers", {}) or {}
        try:
            total_pages = int(headers.get("X-WP-TotalPages") or 0)
        except (TypeError, ValueError):
            total_pages = 0
        per_page = int(query.get("per_page") or PAGE_SIZE)
        if not rows or (total_pages and page >= total_pages) or (not total_pages and len(rows) < per_page):
            return
        page += 1
    raise ValueError(f"la API excedio el limite de {MAX_PAGES} paginas")


def _price_details(price_html: str) -> tuple[float, float, float, str]:
    decoded = html.unescape(price_html or "")
    tag = _opening_tag(decoded, "woo-ua-auction-price")
    attrs = _attributes(tag)
    classes = attrs.get("class", "").lower()
    status = attrs.get("data-status", "").strip().lower()
    shown_price = _money_from_markup(decoded)
    bid = _number(attrs.get("data-bid"))
    if "current-bid" in classes or bid:
        return 0.0, bid or shown_price, 0.0, status
    return shown_price, 0.0, shown_price, status


def _is_active_product(product: dict[str, Any]) -> bool:
    if str(product.get("type") or "").lower() != "auction":
        return False
    if product.get("is_password_protected") is True or product.get("is_in_stock") is False:
        return False
    _, _, _, status = _price_details(str(product.get("price_html") or ""))
    return status not in {"closed", "ended", "expired", "finished"}


def _parse_product_page(document: str) -> dict[str, object]:
    price_tag = _opening_tag(document, "woo-ua-auction-price")
    price_attrs = _attributes(price_tag)
    price_classes = price_attrs.get("class", "").lower()
    status_value = price_attrs.get("data-status", "").lower()

    price_position = document.find(price_tag) if price_tag else -1
    price_segment = document[price_position : price_position + 900] if price_position >= 0 else ""
    shown_price = _money_from_markup(price_segment)
    current_price = _number(price_attrs.get("data-bid"))
    base_price = 0.0
    if "starting-bid" in price_classes:
        base_price = shown_price
        current_price = 0.0
    elif not current_price and "current-bid" in price_classes:
        current_price = shown_price

    countdown_tag = _opening_tag(document, "uwa_auction_product_countdown")
    countdown = _attributes(countdown_tag)
    closing_at = _local_iso(
        countdown.get("data-time", ""),
        countdown.get("data-zone", "America/Montevideo"),
    )
    if not closing_at:
        end_match = re.search(
            r"(?:Termina|Temina)\s+el:\s*</?[^>]*>*\s*(\d{1,2}/\d{1,2}/\d{4})\s*(\d{1,2}:\d{2})",
            document,
            re.IGNORECASE | re.DOTALL,
        )
        if end_match:
            closing_at = _local_iso(f"{end_match.group(1)} {end_match.group(2)}")

    select_match = re.search(
        r"<select[^>]+id=[\"']uwa_bid_value_direct[\"'][^>]*>(.*?)</select>",
        document,
        re.IGNORECASE | re.DOTALL,
    )
    next_bid = 0.0
    if select_match:
        option_match = re.search(r"<option[^>]+value\s*=\s*[\"']?([^\s\"'>]+)", select_match.group(1), re.I)
        if option_match:
            next_bid = _number(option_match.group(1))
    if not next_bid:
        input_match = re.search(
            r"<input[^>]+id=[\"']uwa_bid_value_direct[\"'][^>]*>",
            document,
            re.IGNORECASE | re.DOTALL,
        )
        if input_match:
            input_attrs = _attributes(input_match.group(0))
            next_bid = _number(input_attrs.get("min") or input_attrs.get("value"))
    if not next_bid and base_price:
        next_bid = base_price

    root_match = re.search(
        r"<div[^>]+class=[\"'][^\"']*product-type-auction[^\"']*[\"'][^>]*>",
        document,
        re.IGNORECASE | re.DOTALL,
    )
    root_classes = _attributes(root_match.group(0) if root_match else "").get("class", "").lower()
    expired = (
        status_value in {"closed", "ended", "expired", "finished"}
        or "uwa_auction_status_expired" in root_classes
    )

    return {
        "found": bool(price_tag),
        "base_price": base_price,
        "current_price": current_price,
        "next_bid": next_bid,
        "closing_at": closing_at,
        "status": "closed" if expired else "active",
        "plugin_status": status_value,
    }


class PradoSource:
    source_id = SOURCE_ID
    label = SOURCE_LABEL

    def __init__(self) -> None:
        self.last_enrichment_errors: list[str] = []

    def collect(self, session: requests.Session, timeout: int = 25) -> SourceScanResult:
        _prepare_session(session)
        errors: list[str] = []
        groups_by_id: dict[str, AuctionGroup] = {}
        lots: list[AuctionLot] = []
        seen_lots: set[str] = set()

        try:
            pages = _paged_json(
                session,
                PRODUCTS_API_URL,
                {"per_page": PAGE_SIZE, "stock_status": "instock"},
                timeout,
            )
            products = [product for page in pages for product in page]
        except (requests.RequestException, TypeError, ValueError) as exc:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                groups=[],
                lots=[],
                errors=[f"No se pudieron descubrir lotes: {exc}"],
            )

        for product in products:
            if not _is_active_product(product):
                continue
            lot_id = str(product.get("id") or "").strip()
            lot_url = html.unescape(str(product.get("permalink") or ""))
            if not lot_id or not lot_url or lot_id in seen_lots:
                continue
            seen_lots.add(lot_id)

            categories = product.get("categories")
            category_rows = [row for row in categories if isinstance(row, dict)] if isinstance(categories, list) else []
            category = category_rows[0] if category_rows else {}
            group_id = str(category.get("id") or "auctions")
            group_title = _clean_text(category.get("name")) or "Subastas online"
            group_url = html.unescape(str(category.get("link") or BASE_URL))

            if group_id not in groups_by_id:
                groups_by_id[group_id] = AuctionGroup(
                    source_id=self.source_id,
                    group_id=group_id,
                    title=group_title,
                    url=group_url,
                    commission_percent=DEFAULT_COMMISSION_PERCENT,
                    currency="UYU",
                    location=DEFAULT_LOCATION,
                    status="active",
                    extra={
                        "discovery": "woocommerce_store_api",
                        "taxonomy": "product_cat" if category else "",
                    },
                )

            title = _clean_text(product.get("name")) or f"Lote {lot_id}"
            description = _clean_text(product.get("short_description"))
            price_html = str(product.get("price_html") or "")
            base_price, current_price, next_bid, plugin_status = _price_details(price_html)
            lots.append(
                AuctionLot(
                    source_id=self.source_id,
                    source_label=self.label,
                    group_id=group_id,
                    group_label=group_title,
                    group_url=group_url,
                    lot_id=lot_id,
                    lot_number=_lot_number(title, lot_id),
                    title=title,
                    description=description,
                    lot_url=lot_url,
                    image_url=_first_image(product),
                    currency="UYU",
                    base_price=base_price,
                    current_price=current_price,
                    next_bid=next_bid,
                    commission_percent=DEFAULT_COMMISSION_PERCENT,
                    status="active",
                    extra={
                        "discovery": "woocommerce_store_api",
                        "api_slug": str(product.get("slug") or ""),
                        "api_product_type": str(product.get("type") or ""),
                        "plugin_status": plugin_status,
                        "categories": [_clean_text(row.get("name")) for row in category_rows],
                    },
                )
            )

        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=list(groups_by_id.values()),
            lots=lots,
            errors=errors,
        )

    def enrich_lots(
        self,
        session: requests.Session,
        lots: list[AuctionLot],
        timeout: int = 25,
    ) -> list[AuctionLot]:
        """Refresh only shortlisted lots from Ultimate Auction product pages."""

        _prepare_session(session)
        self.last_enrichment_errors = []
        enriched: list[AuctionLot] = []
        for lot in lots:
            if lot.source_id != self.source_id or not lot.lot_url:
                enriched.append(lot)
                continue
            try:
                response = session.get(lot.lot_url, timeout=timeout)
                response.raise_for_status()
                details = _parse_product_page(response.text)
            except (requests.RequestException, TypeError, ValueError) as exc:
                self.last_enrichment_errors.append(
                    f"Lote {lot.lot_id}: no se pudo actualizar la ficha: {exc}"
                )
                extra = dict(lot.extra)
                extra["enrichment_error"] = str(exc)
                enriched.append(replace(lot, extra=extra))
                continue

            extra = dict(lot.extra)
            extra.update(
                {
                    "auction_markup_found": bool(details["found"]),
                    "plugin_status": str(details["plugin_status"] or extra.get("plugin_status") or ""),
                }
            )
            enriched.append(
                replace(
                    lot,
                    base_price=float(details["base_price"] or lot.base_price),
                    current_price=float(details["current_price"] or lot.current_price),
                    next_bid=float(details["next_bid"] or lot.next_bid),
                    closing_at=str(details["closing_at"] or lot.closing_at),
                    status=str(details["status"] or lot.status),
                    extra=extra,
                )
            )
        return enriched


def scan(session: requests.Session | None = None, timeout: int | float = 20) -> SourceScanResult:
    owned_session = session is None
    active_session = session or requests.Session()
    try:
        return PradoSource().collect(active_session, int(timeout))
    finally:
        if owned_session:
            active_session.close()


def enrich_lots(
    session: requests.Session,
    lots: list[AuctionLot],
    timeout: int | float = 20,
) -> list[AuctionLot]:
    return PradoSource().enrich_lots(session, lots, int(timeout))
