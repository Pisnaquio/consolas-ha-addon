#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import html
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests

from auction_search_config import (
    SHARED_KEYWORDS,
    collect_flags,
    compile_patterns,
    matched_terms,
    score_match,
)

WEB_BASE = "https://subastascastells.com/"
HOME_URL = urljoin(WEB_BASE, "frontend.home.aspx")
LOTES_API = urljoin(WEB_BASE, "rest/API/Remate/lotes")
DEFAULT_OUTPUT_CSV = "consolas_castells_matches.csv"
DEFAULT_OUTPUT_MD = "consolas_castells_matches_readable.md"
DEFAULT_DISCOVERY_CSV = "consolas_castells_auctions.csv"
DEFAULT_TIMEOUT = 25
DEFAULT_SLEEP = 0.15
DEFAULT_LIMIT = 9999
DEFAULT_KEYWORDS = SHARED_KEYWORDS

session = requests.Session()
session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
        )
    }
)


@dataclass
class Auction:
    remate_id: int
    name: str
    category: str
    start_date: str
    end_date: str
    range_text: str
    items_text: str
    url: str
    image_url: str
    remate_tipo: int = 1
    commission_percent: float = 0.0
    currency_label: str = ""
    user_guid: str = ""


@dataclass
class Match:
    remate_id: int
    remate_name: str
    remate_category: str
    remate_range_text: str
    remate_url: str
    commission_percent: float
    lot_id: str
    lot_number: str
    lot_description: str
    lot_url: str
    image_url: str
    closing_at: str
    currency: str
    starting_price: float
    current_value: float
    next_bid: float
    current_with_commission: float
    next_bid_with_commission: float
    matched_keywords: str
    risk_flags: str
    positive_flags: str
    score: int

def to_float(value: object) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0

def fetch_text(url: str, timeout: int) -> str:
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def discover_auctions(timeout: int) -> list[Auction]:
    text = fetch_text(HOME_URL, timeout)
    seen: set[int] = set()
    auctions: list[Auction] = []

    # Castells embeds remates as JSON-like objects in GXState. This is more stable than
    # scraping rendered card markup, and keeps the link/remate id from the source page.
    for raw in re.findall(r'\{"RemateImagen":.*?"RemateNombre":"[^"]*"\}', text):
        try:
            item = json.loads(html.unescape(raw))
        except json.JSONDecodeError:
            continue

        remate_id = int(item.get("RemateId") or 0)
        if not remate_id or remate_id in seen:
            continue
        seen.add(remate_id)

        link = item.get("Link") or f"frontend.sitio.visualremate.aspx?Remate={remate_id}"
        auctions.append(
            Auction(
                remate_id=remate_id,
                name=(item.get("RemateNombre") or "").strip(),
                category=(item.get("RemateCategoriaNombre") or "").strip(),
                start_date=(item.get("RemateInicio") or "").strip(),
                end_date=(item.get("RemateCierre") or "").strip(),
                range_text=(item.get("RemateRangoTexto") or "").strip(),
                items_text=re.sub(r"<[^>]+>", "", html.unescape(item.get("RemateItems") or "")).strip(),
                url=urljoin(WEB_BASE, link),
                image_url=(item.get("RemateImagen") or "").strip(),
                remate_tipo=int(item.get("RemateTipo") or 1),
            )
        )

    return sorted(auctions, key=lambda a: (a.start_date, a.remate_id))


def enrich_auction(auction: Auction, timeout: int) -> Auction:
    text = fetch_text(auction.url, timeout)

    detail_match = re.search(r'"Detalleremate":(\{.*?\}),"vSDTFRONTREMATE"', text)
    if detail_match:
        try:
            detail = json.loads(detail_match.group(1))
            auction.remate_tipo = int(detail.get("RemateTipo") or auction.remate_tipo)
            auction.commission_percent = float(detail.get("RemateComision") or 0)
            auction.name = (detail.get("RemateNombre") or auction.name).strip()
            auction.range_text = (detail.get("RemateRangoTexto") or auction.range_text).strip()
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    guid_match = re.search(r'"UCREMATE_Actuserguid":"([^"]+)"', text)
    if guid_match:
        auction.user_guid = guid_match.group(1)

    currency_match = re.search(r'"LBLMONEDA_Caption":"([^"]+)"', text)
    if currency_match:
        auction.currency_label = currency_match.group(1)

    return auction


def fetch_lots(auction: Auction, timeout: int, limit: int, closed: bool = False) -> list[dict]:
    params = {
        "Remateid": auction.remate_id,
        "RemateTipo": auction.remate_tipo,
        "Cerrado": str(closed).lower(),
        "Lastloteid": 0,
        "Limit": limit,
        "Timezoneoffset": -180,
        "ClienteId": 0,
        "UserGUID": auction.user_guid or "81cccf51-2228-45d0-9ab9-1bc33eacfb84",
    }
    resp = session.get(LOTES_API, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data") or []

def build_matches(
    auctions: list[Auction],
    patterns: dict,
    timeout: int,
    limit: int,
    delay: float,
) -> tuple[list[Match], int, int]:
    matches: list[Match] = []
    scanned_lots = 0
    error_count = 0

    for idx, auction in enumerate(auctions, start=1):
        try:
            auction = enrich_auction(auction, timeout)
            lots = fetch_lots(auction, timeout=timeout, limit=limit)
        except requests.RequestException as exc:
            error_count += 1
            print(f"[ERR] remate {auction.remate_id} ({auction.name}): {exc}")
            continue
        except ValueError as exc:
            error_count += 1
            print(f"[ERR] remate {auction.remate_id} ({auction.name}): respuesta JSON inválida: {exc}")
            continue

        print(f"[{idx}/{len(auctions)}] remate {auction.remate_id} ({auction.name}) -> {len(lots)} lotes")
        scanned_lots += len(lots)

        for lot in lots:
            description = (lot.get("LoteDescripcion") or "").strip()
            hits = matched_terms(description, patterns)
            if not hits:
                continue

            risk_flags, positive_flags = collect_flags(description)
            current_value = to_float(lot.get("ValorActual"))
            next_bid = to_float(lot.get("ValorOfertar"))
            starting_price = to_float(lot.get("LotePrecioSalida"))
            commission_factor = 1 + (auction.commission_percent / 100)
            detail_url = urljoin(WEB_BASE, lot.get("DetalleUrl") or "")
            currency = (lot.get("LotePrecioSalidaMonedaWF") or auction.currency_label or "$").strip()

            matches.append(
                Match(
                    remate_id=auction.remate_id,
                    remate_name=auction.name,
                    remate_category=auction.category,
                    remate_range_text=auction.range_text,
                    remate_url=auction.url,
                    commission_percent=auction.commission_percent,
                    lot_id=str(lot.get("LoteId") or ""),
                    lot_number=str(lot.get("LoteNumero") or ""),
                    lot_description=description,
                    lot_url=detail_url,
                    image_url=(lot.get("LoteImageUrl") or "").strip(),
                    closing_at=(lot.get("LoteComienzoCierre") or "").strip(),
                    currency=currency,
                    starting_price=starting_price,
                    current_value=current_value,
                    next_bid=next_bid,
                    current_with_commission=current_value * commission_factor,
                    next_bid_with_commission=next_bid * commission_factor,
                    matched_keywords=", ".join(hits),
                    risk_flags=", ".join(risk_flags),
                    positive_flags=", ".join(positive_flags),
                    score=score_match(
                        description=description,
                        hits=hits,
                        risk_flags=risk_flags,
                        positive_flags=positive_flags,
                        market_value=current_value,
                    ),
                )
            )

        if delay > 0:
            time.sleep(delay)

    return matches, scanned_lots, error_count


def write_csv(matches: list[Match], path: Path) -> None:
    fieldnames = [
        "score",
        "remate_id",
        "remate_name",
        "remate_category",
        "remate_range_text",
        "remate_url",
        "commission_percent",
        "lot_number",
        "lot_id",
        "lot_description",
        "currency",
        "starting_price",
        "current_value",
        "next_bid",
        "current_with_commission",
        "next_bid_with_commission",
        "closing_at",
        "matched_keywords",
        "positive_flags",
        "risk_flags",
        "lot_url",
        "image_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in matches:
            row = m.__dict__.copy()
            for key in [
                "starting_price",
                "current_value",
                "next_bid",
                "current_with_commission",
                "next_bid_with_commission",
                "commission_percent",
            ]:
                row[key] = f"{row[key]:.2f}"
            writer.writerow(row)


def write_markdown(matches: list[Match], path: Path) -> None:
    grouped: dict[int, list[Match]] = {}
    for match in matches:
        grouped.setdefault(match.remate_id, []).append(match)

    lines = [
        "# Reporte Consolas - Castells",
        "",
        f"Total de matches: **{len(matches)}**",
        f"Remates con matches: **{len(grouped)}**",
        "",
    ]

    for remate_id, remate_matches in grouped.items():
        first = remate_matches[0]
        lines.extend(
            [
                f"## {remate_id} - {first.remate_name}",
                "",
                f"- Rango: {first.remate_range_text}",
                f"- Comision: {first.commission_percent:.2f}%",
                f"- URL: {first.remate_url}",
                "",
                "| Score | Lote | Actual | Prox. puja c/comision | Keywords | Riesgos | URL articulo |",
                "|---:|---:|---:|---:|---|---|---|",
            ]
        )
        for m in remate_matches:
            risk = m.risk_flags or "-"
            lines.append(
                f"| {m.score} | {m.lot_number} | {m.currency}{m.current_value:,.0f} | "
                f"{m.currency}{m.next_bid_with_commission:,.0f} | {m.matched_keywords} | "
                f"{risk} | {m.lot_url} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_discovery_csv(auctions: list[Auction], path: Path) -> None:
    fieldnames = [
        "remate_id",
        "name",
        "category",
        "start_date",
        "end_date",
        "range_text",
        "items_text",
        "url",
        "image_url",
        "remate_tipo",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for auction in auctions:
            writer.writerow(
                {
                    "remate_id": auction.remate_id,
                    "name": auction.name,
                    "category": auction.category,
                    "start_date": auction.start_date,
                    "end_date": auction.end_date,
                    "range_text": auction.range_text,
                    "items_text": auction.items_text,
                    "url": auction.url,
                    "image_url": auction.image_url,
                    "remate_tipo": auction.remate_tipo,
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Busca consolas, videojuegos y accesorios en remates activos de Subastas Castells."
    )
    parser.add_argument("--ids", help="IDs de remate separados por coma. Si se omite, descubre desde la home.")
    parser.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS), help="Keywords separadas por coma.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_CSV, help="Ruta CSV de salida.")
    parser.add_argument("--markdown", default=DEFAULT_OUTPUT_MD, help="Ruta Markdown de salida.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--min-score", type=int, default=0, help="Filtra matches por score mínimo.")
    parser.add_argument("--discover-only", action="store_true", help="Solo descubre remates activos y guarda CSV.")
    parser.add_argument("--discover-output", default=DEFAULT_DISCOVERY_CSV, help="Ruta CSV para remates descubiertos.")
    parser.add_argument("--no-markdown", action="store_true")
    return parser.parse_args()


def parse_ids_arg(ids_arg: str) -> list[int]:
    ids: list[int] = []
    for part in ids_arg.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return sorted(set(ids))


def main() -> int:
    args = parse_args()
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    patterns = compile_patterns(keywords)

    print("=" * 88)
    print("BUSCADOR DE CONSOLAS EN LOTES CASTELLS")
    print("=" * 88)
    print(f"Keywords: {', '.join(keywords)}")

    discovered_auctions = discover_auctions(timeout=max(1, args.timeout))

    if args.discover_only:
        if args.ids:
            allowed_ids = set(parse_ids_arg(args.ids))
            auctions = [auction for auction in discovered_auctions if auction.remate_id in allowed_ids]
        else:
            auctions = discovered_auctions

        discovery_path = Path(args.discover_output)
        write_discovery_csv(auctions, discovery_path)
        print(f"Remates activos detectados: {len(auctions)}")
        print(f"CSV guardado en: {discovery_path}")
        return 0

    if args.ids:
        auctions = [
            Auction(
                remate_id=remate_id,
                name="",
                category="",
                start_date="",
                end_date="",
                range_text="",
                items_text="",
                url=urljoin(WEB_BASE, f"frontend.sitio.visualremate.aspx?Remate={remate_id}"),
                image_url="",
            )
            for remate_id in parse_ids_arg(args.ids)
        ]
    else:
        auctions = discovered_auctions

    if not auctions:
        print("No se encontraron remates para escanear.")
        return 1

    print(f"Remates a escanear: {len(auctions)}")
    print()

    started = time.time()
    matches, scanned_lots, error_count = build_matches(
        auctions,
        patterns=patterns,
        timeout=max(1, args.timeout),
        limit=max(1, args.limit),
        delay=max(0.0, args.sleep),
    )
    matches = [m for m in matches if m.score >= args.min_score]
    matches.sort(key=lambda m: (-m.score, m.remate_id, int(m.lot_number or 0)))

    print("\n" + "=" * 88)
    print("MATCHES ENCONTRADOS")
    print("=" * 88)
    for m in matches:
        risk = f" | riesgos: {m.risk_flags}" if m.risk_flags else ""
        print(
            f"- score={m.score} | Remate {m.remate_id} ({m.remate_name}) | "
            f"Lote #{m.lot_number} | actual {m.currency}{m.current_value:,.0f} | "
            f"prox c/comision {m.currency}{m.next_bid_with_commission:,.0f} | "
            f"keywords: {m.matched_keywords}{risk}\n"
            f"  Articulo: {m.lot_url}\n"
            f"  Remate: {m.remate_url}\n"
            f"  {m.lot_description}"
        )

    elapsed = time.time() - started
    print("\n" + "=" * 88)
    print(f"Lotes escaneados: {scanned_lots}")
    print(f"Matches: {len(matches)}")
    print(f"Errores: {error_count}")
    print(f"Tiempo: {elapsed:.1f}s")

    output_path = Path(args.output)
    write_csv(matches, output_path)
    print(f"CSV guardado en: {output_path}")

    if not args.no_markdown:
        markdown_path = Path(args.markdown)
        write_markdown(matches, markdown_path)
        print(f"Markdown guardado en: {markdown_path}")

    if error_count and scanned_lots == 0:
        return 1

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        sys.exit(1)
