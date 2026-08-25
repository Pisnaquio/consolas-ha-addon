"""API-first source adapter for TodoRemates.

Discovery uses the public WordPress/WooCommerce APIs.  The custom auction
fields are not exposed there, so :meth:`enrich_lots` fetches only the product
pages selected by the shared matcher.
"""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

from .model import AuctionGroup, AuctionLot, SourceScanResult


SOURCE_ID = "todoremates"
SOURCE_LABEL = "TodoRemates"

BASE_URL = "https://todoremates.com.uy"
REMATES_API_URL = f"{BASE_URL}/wp-json/wp/v2/remate"
PRODUCTS_API_URL = f"{BASE_URL}/wp-json/wc/store/v1/products"
DEFAULT_COMMISSION_PERCENT = 20.0
LOCAL_TIMEZONE = ZoneInfo("America/Montevideo")
PAGE_SIZE = 100
MAX_PAGES = 50
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


def _api_price(product: dict[str, Any]) -> float:
    prices = product.get("prices")
    if not isinstance(prices, dict):
        return 0.0
    raw = _number(prices.get("price"))
    try:
        minor_unit = max(0, int(prices.get("currency_minor_unit") or 0))
    except (TypeError, ValueError):
        minor_unit = 0
    return raw / (10**minor_unit)


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
    pattern = rf"<[^>]+\b{re.escape(marker)}(?:\s*=\s*(?:['\"][^'\"]*['\"]|[^\s>]+))?[^>]*>"
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


def _local_iso(value: str) -> str:
    raw = html.unescape(value or "").strip()
    if not raw:
        return ""
    for pattern in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, pattern).replace(tzinfo=LOCAL_TIMEZONE)
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            continue
    return ""


def _epoch_iso(value: str) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(LOCAL_TIMEZONE).isoformat(
            timespec="seconds"
        )
    except (OverflowError, OSError, ValueError):
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


def _active_product(product: dict[str, Any]) -> bool:
    if product.get("is_password_protected") is True:
        return False
    if product.get("is_in_stock") is False:
        return False
    return bool(product.get("id") and product.get("permalink"))


def _parse_product_page(document: str) -> dict[str, object]:
    panel_tag = _opening_tag(document, "data-lote-panel")
    panel = _attributes(panel_tag)
    countdown_tag = _opening_tag(document, "data-lote-countdown")
    countdown = _attributes(countdown_tag)

    classes = panel.get("class", "").lower()
    ended = panel.get("data-ended", "").strip() in {"1", "true", "yes"}
    if ended or "is-ended" in classes:
        status = "closed"
    elif "is-paused" in classes:
        status = "paused"
    else:
        status = "active"

    closing_at = _epoch_iso(countdown.get("data-lote-countdown", ""))
    if not closing_at:
        closing_at = _local_iso(countdown.get("data-lote-close-label", ""))

    return {
        "found": bool(panel_tag),
        "base_price": _number(panel.get("data-base-price")),
        "current_price": _number(panel.get("data-current-offer")),
        "next_bid": _number(panel.get("data-next-minimum")),
        "event_at": _local_iso(countdown.get("data-lote-start-label", "")),
        "closing_at": closing_at,
        "status": status,
        "step": _number(panel.get("data-step")),
        "increment_percent": _number(panel.get("data-increment-percent")),
    }


class TodoRematesSource:
    source_id = SOURCE_ID
    label = SOURCE_LABEL

    def __init__(self) -> None:
        self.last_enrichment_errors: list[str] = []

    def collect(self, session: requests.Session, timeout: int = 25) -> SourceScanResult:
        _prepare_session(session)
        groups: list[AuctionGroup] = []
        lots: list[AuctionLot] = []
        errors: list[str] = []
        seen_lots: set[str] = set()

        try:
            term_pages = _paged_json(
                session,
                REMATES_API_URL,
                {"per_page": PAGE_SIZE, "hide_empty": "true"},
                timeout,
            )
            terms = [term for page in term_pages for term in page]
        except (requests.RequestException, TypeError, ValueError) as exc:
            return SourceScanResult(
                source_id=self.source_id,
                label=self.label,
                groups=[],
                lots=[],
                errors=[f"No se pudieron descubrir remates: {exc}"],
            )

        for term in terms:
            term_id = str(term.get("id") or "").strip()
            if not term_id:
                continue
            group_title = _clean_text(term.get("name")) or f"Remate {term_id}"
            group_url = html.unescape(str(term.get("link") or BASE_URL))
            group = AuctionGroup(
                source_id=self.source_id,
                group_id=term_id,
                title=group_title,
                url=group_url,
                commission_percent=DEFAULT_COMMISSION_PERCENT,
                currency="UYU",
                status="active",
                extra={
                    "discovery": "wordpress_rest+woocommerce_store_api",
                    "taxonomy": "remate",
                    "api_count": int(term.get("count") or 0),
                },
            )
            groups.append(group)

            try:
                product_pages = _paged_json(
                    session,
                    PRODUCTS_API_URL,
                    {
                        "per_page": PAGE_SIZE,
                        "_unstable_tax_remate": term_id,
                    },
                    timeout,
                )
                products = [product for page in product_pages for product in page]
            except (requests.RequestException, TypeError, ValueError) as exc:
                errors.append(f"{group_title}: no se pudieron obtener lotes: {exc}")
                continue

            for product in products:
                if not _active_product(product):
                    continue
                lot_id = str(product.get("id") or "").strip()
                if not lot_id or lot_id in seen_lots:
                    continue
                seen_lots.add(lot_id)

                title = _clean_text(product.get("name")) or f"Lote {lot_id}"
                description = _clean_text(product.get("description"))
                api_currency = ""
                if isinstance(product.get("prices"), dict):
                    api_currency = str(product["prices"].get("currency_code") or "")
                base_price = _api_price(product)
                lot_url = html.unescape(str(product.get("permalink") or ""))
                lots.append(
                    AuctionLot(
                        source_id=self.source_id,
                        source_label=self.label,
                        group_id=term_id,
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
                        next_bid=base_price,
                        commission_percent=DEFAULT_COMMISSION_PERCENT,
                        status="active",
                        extra={
                            "discovery": "woocommerce_store_api",
                            "api_slug": str(product.get("slug") or ""),
                            "api_product_type": str(product.get("type") or ""),
                            "api_currency_code": api_currency,
                            "api_price_unverified": True,
                        },
                    )
                )

        return SourceScanResult(
            source_id=self.source_id,
            label=self.label,
            groups=groups,
            lots=lots,
            errors=errors,
        )

    def enrich_lots(
        self,
        session: requests.Session,
        lots: list[AuctionLot],
        timeout: int = 25,
    ) -> list[AuctionLot]:
        """Refresh only shortlisted lots from TodoRemates product pages."""

        _prepare_session(session)
        self.last_enrichment_errors = []
        def enrich_one(lot: AuctionLot, request_session: requests.Session) -> tuple[AuctionLot, str]:
            if lot.source_id != self.source_id or not lot.lot_url:
                return lot, ""
            try:
                response = request_session.get(lot.lot_url, timeout=timeout)
                response.raise_for_status()
                details = _parse_product_page(response.text)
            except (requests.RequestException, TypeError, ValueError) as exc:
                extra = dict(lot.extra)
                extra["enrichment_error"] = str(exc)
                return replace(lot, extra=extra), f"Lote {lot.lot_id}: no se pudo actualizar la ficha: {exc}"

            extra = dict(lot.extra)
            extra.update(
                {
                    "auction_markup_found": bool(details["found"]),
                    "api_price_unverified": False,
                    "bid_step": details["step"],
                    "increment_percent": details["increment_percent"],
                }
            )
            base_price = float(details["base_price"] or lot.base_price)
            current_price = float(details["current_price"] or 0)
            next_bid = float(details["next_bid"] or base_price or lot.next_bid)
            return replace(
                lot,
                base_price=base_price,
                current_price=current_price,
                next_bid=next_bid,
                event_at=str(details["event_at"] or lot.event_at),
                closing_at=str(details["closing_at"] or lot.closing_at),
                status=str(details["status"] or lot.status),
                extra=extra,
            ), ""

        def enrich_with_owned_session(lot: AuctionLot) -> tuple[AuctionLot, str]:
            worker_session = requests.Session()
            try:
                _prepare_session(worker_session)
                return enrich_one(lot, worker_session)
            finally:
                worker_session.close()

        # Detail pages are independent.  Keep fake/custom sessions
        # deterministic, but use bounded parallelism for real scans so a few
        # slow product pages cannot make a large source look hung.
        if len(lots) <= 1 or not isinstance(session, requests.Session):
            results = [enrich_one(lot, session) for lot in lots]
        else:
            results_by_index: dict[int, tuple[AuctionLot, str]] = {}
            with ThreadPoolExecutor(max_workers=min(8, len(lots)), thread_name_prefix="todoremates") as pool:
                futures = {
                    pool.submit(enrich_with_owned_session, lot): index
                    for index, lot in enumerate(lots)
                }
                for future in as_completed(futures):
                    results_by_index[futures[future]] = future.result()
            results = [results_by_index[index] for index in range(len(lots))]

        enriched: list[AuctionLot] = []
        for lot, error in results:
            enriched.append(lot)
            if error:
                self.last_enrichment_errors.append(error)
        return enriched


def scan(session: requests.Session | None = None, timeout: int | float = 20) -> SourceScanResult:
    owned_session = session is None
    active_session = session or requests.Session()
    try:
        return TodoRematesSource().collect(active_session, int(timeout))
    finally:
        if owned_session:
            active_session.close()


def enrich_lots(
    session: requests.Session,
    lots: list[AuctionLot],
    timeout: int | float = 20,
) -> list[AuctionLot]:
    return TodoRematesSource().enrich_lots(session, lots, int(timeout))
