"""Adapter de eBay USA sobre la Browse API oficial.

Sólo API autorizada: no se scrapea eBay ni se elude el gate de producción. El
entorno (`sandbox` o `production`) sale de las opciones privadas del add-on y
nunca del frontend.

El adapter traduce los criterios estructurados de la búsqueda a los filtros que
la Browse API entiende, para que el descarte ocurra del lado de eBay y no
después de traer cien resultados irrelevantes.

Documentación: https://developer.ebay.com/api-docs/buy/api-browse.html
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ..model import MarketplaceListing, SourcePage, SourceReceipt, SourceHealth


SOURCE_ID = "ebay-us"
MARKETPLACE_ID = "EBAY_US"
REQUEST_TIMEOUT_SECONDS = 20
MAX_LIMIT = 50

# Condición de eBay → condición del criterio. Los ids son los de la Browse API.
EBAY_CONDITION_FILTERS = {
    "new": "{NEW}",
    "used": "{USED}",
    "refurbished": "{CERTIFIED_REFURBISHED|SELLER_REFURBISHED}",
}


class EbayCredentialsMissing(RuntimeError):
    """El add-on todavía no tiene credenciales de eBay Developers."""


class EbayRequestFailed(RuntimeError):
    """eBay rechazó o no pudo completar la consulta."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def to_amount(value: Any) -> float | None:
    try:
        amount = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if amount != amount or amount in {float("inf"), float("-inf")} or amount < 0:
        return None
    return round(amount, 2)


def build_browse_filters(criteria: dict[str, Any]) -> str:
    """Traduce los criterios a `filter=` de la Browse API.

    Sólo se traduce lo que eBay puede evaluar con certeza. Todo lo demás
    (términos excluidos, región, completitud) lo resuelve el matcher del radar
    sobre el texto ya normalizado.
    """

    currency = clean_text(criteria.get("currency")).upper() or "USD"
    filters: list[str] = []

    max_item_price = to_amount(criteria.get("maxItemPrice"))
    if max_item_price is not None:
        filters.append(f"price:[..{max_item_price:g}]")
        filters.append(f"priceCurrency:{currency}")

    condition = clean_text(criteria.get("condition")).lower()
    if condition in EBAY_CONDITION_FILTERS:
        filters.append(f"conditions:{EBAY_CONDITION_FILTERS[condition]}")

    if criteria.get("freeShippingOnly") is True:
        filters.append("maxDeliveryCost:0")

    if criteria.get("returnsRequired") is True:
        filters.append("returnsAccepted:true")

    return ",".join(filters)


def listing_kind(buying_options: Any) -> str:
    options = {str(option).upper() for option in (buying_options or []) if option}
    if "AUCTION" in options:
        return "auction"
    if "BEST_OFFER" in options:
        return "best_offer"
    if "FIXED_PRICE" in options:
        return "fixed_price"
    return "unknown"


def normalize_public_http_url(value: Any) -> str:
    """Sólo se conservan URLs http(s) absolutas; nada de javascript: ni data:."""
    candidate = clean_text(value)
    if not candidate:
        return ""
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return candidate


def parse_item_summary(raw: Any) -> MarketplaceListing | None:
    """Convierte un `itemSummary` de eBay en el modelo canónico del radar.

    El contenido es dato externo no confiable: se normaliza y se escapa aguas
    arriba, nunca se interpreta.
    """

    if not isinstance(raw, dict):
        return None
    external_id = clean_text(raw.get("itemId"))
    title = clean_text(raw.get("title"))
    listing_url = normalize_public_http_url(raw.get("itemWebUrl"))
    if not external_id or not title or not listing_url:
        return None

    price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
    shipping_options = raw.get("shippingOptions") if isinstance(raw.get("shippingOptions"), list) else []
    shipping = shipping_options[0] if shipping_options and isinstance(shipping_options[0], dict) else {}
    shipping_cost = shipping.get("shippingCost") if isinstance(shipping.get("shippingCost"), dict) else {}
    location = raw.get("itemLocation") if isinstance(raw.get("itemLocation"), dict) else {}
    image = raw.get("image") if isinstance(raw.get("image"), dict) else {}
    seller = raw.get("seller") if isinstance(raw.get("seller"), dict) else {}

    price_amount = to_amount(price.get("value"))
    price_currency = clean_text(price.get("currency")).upper()[:8]
    # Sin importe declarado el envío queda desconocido: un envío a calcular no es gratis.
    shipping_amount = to_amount(shipping_cost.get("value"))
    shipping_cost_type = clean_text(shipping.get("shippingCostType")).upper()

    seller_label = clean_text(seller.get("username"))
    feedback = clean_text(seller.get("feedbackPercentage"))
    if seller_label and feedback:
        seller_label = f"{seller_label} · {feedback}%"

    shipping_currency = clean_text(shipping_cost.get("currency")).upper() or price_currency
    shipping_label = ""
    if shipping_amount == 0:
        shipping_label = "Envío gratis"
    elif shipping_amount is not None:
        shipping_label = f"Envío {shipping_currency} {shipping_amount:g}"
    elif shipping_cost_type:
        shipping_label = "Envío a calcular" if shipping_cost_type == "CALCULATED" else shipping_cost_type

    return MarketplaceListing(
        source_id=SOURCE_ID,
        external_id=external_id,
        title=title,
        listing_url=listing_url,
        description=clean_text(raw.get("shortDescription")),
        image_url=normalize_public_http_url(image.get("imageUrl")),
        listing_kind=listing_kind(raw.get("buyingOptions")),
        price_amount=price_amount,
        price_currency=price_currency,
        shipping_amount=shipping_amount,
        shipping_currency=shipping_currency[:8],
        price_label=clean_text(f"{price_currency} {price.get('value', '')}") if price_amount is not None else "",
        shipping_label=shipping_label,
        condition_label=clean_text(raw.get("condition")),
        location_label=clean_text(location.get("country")),
        seller_label=seller_label,
        # La Browse API de búsqueda no confirma disponibilidad: eso es `verify()`,
        # que llega con la capability `availabilityCheck`.
        availability="unknown",
        closes_at=clean_text(raw.get("itemEndDate")),
    )


class EbayBrowseSource:
    source_id = SOURCE_ID
    label = "eBay USA"

    def api_host(self, config: Any) -> str:
        environment = str(getattr(config, "ebay_environment", "sandbox") or "sandbox")
        return "api.sandbox.ebay.com" if environment == "sandbox" else "api.ebay.com"

    def access_token(self, config: Any) -> str:
        client_id = str(getattr(config, "ebay_client_id", "") or "").strip()
        client_secret = str(getattr(config, "ebay_client_secret", "") or "").strip()
        if not client_id or not client_secret:
            raise EbayCredentialsMissing("Configurá las credenciales de eBay Developers en el add-on")
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(
            f"https://{self.api_host(config)}/identity/v1/oauth2/token",
            data=b"grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                token = str(json.loads(response.read().decode("utf-8")).get("access_token") or "")
        except urllib.error.HTTPError as exc:
            error_code = ""
            try:
                error_code = str(json.loads(exc.read().decode("utf-8")).get("error") or "")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            detail = f": {error_code}" if error_code else ""
            raise EbayRequestFailed(f"eBay rechazó las credenciales (HTTP {exc.code}){detail}") from exc
        except Exception as exc:
            raise EbayRequestFailed("No se pudo obtener el token de eBay") from exc
        if not token:
            raise EbayRequestFailed("eBay no devolvió un token de aplicación")
        return token

    def fetch_item_summaries(self, config: Any, query: str, criteria: dict[str, Any]) -> list[Any]:
        token = self.access_token(config)
        limit = int(criteria.get("resultLimit") or 12)
        parameters = {"q": query, "limit": str(max(1, min(limit, MAX_LIMIT)))}
        filters = build_browse_filters(criteria)
        if filters:
            parameters["filter"] = filters
        request = urllib.request.Request(
            f"https://{self.api_host(config)}/buy/browse/v1/item_summary/search?"
            f"{urllib.parse.urlencode(parameters)}",
            headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID},
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise EbayRequestFailed(f"La búsqueda de eBay falló (HTTP {exc.code})") from exc
        except Exception as exc:
            raise EbayRequestFailed("eBay no pudo completar la búsqueda") from exc
        summaries = payload.get("itemSummaries")
        return summaries if isinstance(summaries, list) else []

    def search(self, config: Any, query: str, criteria: dict[str, Any]) -> SourcePage:
        """Una consulta a eBay. El recibo declara si la cobertura fue completa."""
        started_at = utc_now()
        try:
            summaries = self.fetch_item_summaries(config, query, criteria)
        except (EbayCredentialsMissing, EbayRequestFailed) as error:
            return SourcePage(
                source_id=SOURCE_ID,
                listings=[],
                receipt=SourceReceipt(
                    source_id=SOURCE_ID,
                    status="failed",
                    query=query,
                    error_count=1,
                    started_at=started_at,
                    finished_at=utc_now(),
                    errors=[str(error)],
                ),
            )

        listings: list[MarketplaceListing] = []
        skipped = 0
        seen: set[str] = set()
        for raw in summaries:
            listing = parse_item_summary(raw)
            if listing is None:
                skipped += 1
                continue
            if listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            listings.append(listing)

        return SourcePage(
            source_id=SOURCE_ID,
            listings=listings,
            receipt=SourceReceipt(
                source_id=SOURCE_ID,
                # Una respuesta con ítems ilegibles no es cobertura completa.
                status="complete" if skipped == 0 else "partial",
                query=query,
                listing_count=len(listings),
                error_count=skipped,
                started_at=started_at,
                finished_at=utc_now(),
                errors=[f"{skipped} publicaciones sin identidad utilizable"] if skipped else [],
            ),
        )

    def health(self, config: Any) -> SourceHealth:
        environment = str(getattr(config, "ebay_environment", "sandbox") or "sandbox")
        has_credentials = bool(
            str(getattr(config, "ebay_client_id", "") or "").strip()
            and str(getattr(config, "ebay_client_secret", "") or "").strip()
        )
        if not has_credentials:
            return SourceHealth(
                source_id=SOURCE_ID,
                status="unconfigured",
                detail="Faltan las credenciales de eBay Developers en el add-on",
                environment=environment,
            )
        if environment == "sandbox":
            return SourceHealth(
                source_id=SOURCE_ID,
                status="degraded",
                detail="Entorno sandbox: las publicaciones no son compras reales",
                environment=environment,
            )
        return SourceHealth(source_id=SOURCE_ID, status="ready", detail="", environment=environment)


__all__ = [
    "EbayBrowseSource",
    "EbayCredentialsMissing",
    "EbayRequestFailed",
    "SOURCE_ID",
    "build_browse_filters",
    "parse_item_summary",
]
