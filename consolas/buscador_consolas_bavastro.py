#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from auction_search_config import (
    SHARED_KEYWORDS,
    collect_flags,
    compile_patterns,
    matched_terms,
    normalize_text,
    score_match,
)

API_BASE = "https://api-parseo.bavastronline.com"
WEB_BASE = "https://www.bavastronline.com.uy"
DEFAULT_AUCTIONS_CSV = "auctions_bavastro_matches.csv"
DEFAULT_OUTPUT_CSV = "consolas_matches.csv"
DEFAULT_KEYWORDS = SHARED_KEYWORDS
DEFAULT_TIMEOUT = 12
DEFAULT_PAGE_SIZE = 50
DEFAULT_SLEEP = 0.03

# Fallback por si no existe CSV de subastas previo.
FALLBACK_AUCTION_IDS = [
    930, 957, 1019, 1101, 1235, 1372, 1473, 1537, 1609, 1682, 1739, 1796,
    1843, 1876, 1891, 1926, 1961, 2017, 2058, 2105, 2130, 2138, 2172, 2207,
    2238, 2263, 2288, 2348, 2396, 2424, 2453, 2479, 2521, 2553, 2571, 2600,
    2640, 2656, 2682, 2742, 2792, 2839,
]

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
class Match:
    auction_id: int
    auction_name: str
    auction_end_date: str
    auction_url: str
    lot_auction_id: int
    lot_number: int | None
    final_amount: float
    base_price: float
    best_price: float
    number_of_bids: int
    currency_prefix: str
    lot_web_url: str
    lot_reference_url: str
    lot_api_url: str
    image_url: str
    matched_keywords: str
    risk_flags: str
    positive_flags: str
    score: int
    description: str


def read_auction_ids_from_csv(csv_path: Path) -> list[int]:
    ids: list[int] = []
    if not csv_path.exists():
        return ids
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = (row.get("id") or "").strip()
            if raw.isdigit():
                ids.append(int(raw))
    return sorted(set(ids))


def parse_ids_arg(ids_arg: str) -> list[int]:
    out: list[int] = []
    for part in ids_arg.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            out.append(int(part))
    return sorted(set(out))


def fetch_auction_lots(auction_id: int, page_size: int, timeout: int, delay: float) -> list[dict]:
    lots: list[dict] = []
    page = 1

    while True:
        url = f"{API_BASE}/auctions/{auction_id}/lots/published/"
        params = {"page": page, "page_size": page_size}
        resp = session.get(url, params=params, timeout=timeout)

        if resp.status_code == 404:
            break
        resp.raise_for_status()

        data = resp.json()
        results = data.get("results", [])
        if not results:
            break

        lots.extend(results)

        if not data.get("next"):
            break

        if delay > 0:
            time.sleep(delay)

        page += 1

    return lots


def extract_lot_image_url(lot_obj: dict) -> str:
    images = lot_obj.get("images")
    if not isinstance(images, list):
        return ""

    def _order_value(item: dict) -> int:
        raw = str((item or {}).get("order", "")).strip()
        if not raw:
            return 10_000_000
        try:
            return int(raw)
        except ValueError:
            return 10_000_000

    ordered = [
        image
        for image in sorted((image for image in images if isinstance(image, dict)), key=_order_value)
        if isinstance(image, dict)
        and str(image.get("url") or image.get("image") or "").strip()
    ]
    if not ordered:
        return ""
    return str(ordered[0].get("url") or ordered[0].get("image") or "").strip()


def build_matches(
    auction_ids: list[int],
    patterns: dict,
    page_size: int,
    timeout: int,
    delay: float,
    receipts_out: list[dict] | None = None,
) -> tuple[list[Match], int]:
    matches: list[Match] = []
    scanned_lots = 0

    for idx, auction_id in enumerate(auction_ids, start=1):
        auction_url = f"{WEB_BASE}/auctions/{auction_id}"
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            lots = fetch_auction_lots(auction_id, page_size=page_size, timeout=timeout, delay=delay)
        except requests.RequestException as exc:
            print(f"[ERR] subasta {auction_id}: {exc}")
            if receipts_out is not None:
                receipts_out.append(
                    {
                        "groupId": str(auction_id),
                        "status": "failed",
                        "lotCount": 0,
                        "errorCount": 1,
                        "startedAt": started_at,
                        "finishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                )
            continue

        print(f"[{idx}/{len(auction_ids)}] subasta {auction_id} -> {len(lots)} lotes")

        for item in lots:
            scanned_lots += 1
            lot_auction_id = item.get("id")
            lot_number = item.get("lot_number")
            base_price = float(item.get("base_price") or 0)
            best_price = float(item.get("best_price") or 0)
            number_of_bids = int(item.get("number_of_bids") or 0)
            lot_obj = item.get("lot") or {}
            description = (lot_obj.get("description") or "").strip()
            auction_name = ((lot_obj.get("auction") or {}).get("name") or "").strip()
            auction_end_date = ((lot_obj.get("auction") or {}).get("end_date") or "").strip()
            image_url = extract_lot_image_url(lot_obj)
            currency_prefix = (((lot_obj.get("currency") or {}).get("prefix")) or "$").strip() or "$"

            hits = matched_terms(description, patterns)
            if not hits:
                continue

            risk_flags, positive_flags = collect_flags(description)

            lot_web_url = f"{WEB_BASE}/lot/{lot_auction_id}"
            lot_reference_url = auction_url
            lot_api_url = f"{API_BASE}/lot_auctions/{lot_auction_id}/"

            final_amount = best_price if number_of_bids > 0 else base_price
            score = score_match(
                description=description,
                hits=hits,
                risk_flags=risk_flags,
                positive_flags=positive_flags,
                market_value=final_amount,
                number_of_bids=number_of_bids,
            )

            matches.append(
                Match(
                    auction_id=auction_id,
                    auction_name=auction_name,
                    auction_end_date=auction_end_date,
                    auction_url=auction_url,
                    lot_auction_id=int(lot_auction_id),
                    lot_number=int(lot_number) if lot_number is not None else None,
                    final_amount=final_amount,
                    base_price=base_price,
                    best_price=best_price,
                    number_of_bids=number_of_bids,
                    currency_prefix=currency_prefix,
                    lot_web_url=lot_web_url,
                    lot_reference_url=lot_reference_url,
                    lot_api_url=lot_api_url,
                    image_url=image_url,
                    matched_keywords=", ".join(hits),
                    risk_flags=", ".join(risk_flags),
                    positive_flags=", ".join(positive_flags),
                    score=score,
                    description=description,
                )
            )

        if receipts_out is not None:
            receipts_out.append(
                {
                    "groupId": str(auction_id),
                    "status": "complete",
                    "lotCount": len(lots),
                    "errorCount": 0,
                    "startedAt": started_at,
                    "finishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )

    return matches, scanned_lots


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Busca publicaciones de consolas dentro de lotes de subastas de Bavastro."
    )
    parser.add_argument(
        "--ids",
        help="IDs de subastas separados por coma. Si se omite, usa auctions_bavastro_matches.csv o fallback.",
    )
    parser.add_argument(
        "--keywords",
        default=",".join(DEFAULT_KEYWORDS),
        help="Keywords separadas por coma.",
    )
    parser.add_argument("--auctions-csv", default=DEFAULT_AUCTIONS_CSV)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    parser.add_argument("--min-score", type=int, default=0, help="Filtra matches por score mínimo.")
    parser.add_argument("--receipt", default="", help="Ruta JSON para el recibo de cobertura por subasta.")
    parser.add_argument("--max-desc", type=int, default=220, help="Máximo de caracteres en descripción impresa.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.ids:
        auction_ids = parse_ids_arg(args.ids)
    else:
        auction_ids = read_auction_ids_from_csv(Path(args.auctions_csv))
        if not auction_ids:
            auction_ids = FALLBACK_AUCTION_IDS[:]

    if not auction_ids:
        print("No hay IDs de subastas para escanear.")
        return 1

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    patterns = compile_patterns(keywords)

    print("=" * 88)
    print("BUSCADOR DE CONSOLAS EN LOTES BAVASTRO")
    print("=" * 88)
    print(f"Subastas a escanear: {len(auction_ids)}")
    print(f"Keywords: {', '.join(keywords)}")
    print()

    started = time.time()
    receipts: list[dict] = []
    matches, scanned_lots = build_matches(
        auction_ids,
        patterns,
        page_size=max(1, args.page_size),
        timeout=max(1, args.timeout),
        delay=max(0.0, args.sleep),
        receipts_out=receipts,
    )
    matches = [m for m in matches if m.score >= args.min_score]

    matches.sort(key=lambda m: (-m.score, m.auction_id, m.lot_number or 0, m.lot_auction_id))

    print("\n" + "=" * 88)
    print("MATCHES ENCONTRADOS")
    print("=" * 88)

    for m in matches:
        desc = (m.description[: args.max_desc] + "...") if len(m.description) > args.max_desc else m.description
        risk = f" | riesgos: {m.risk_flags}" if m.risk_flags else ""
        print(
            f"- score={m.score} | Subasta {m.auction_id} ({m.auction_name}) | Lote #{m.lot_number} | "
            f"monto final: {m.currency_prefix}{m.final_amount:,.0f} | "
            f"pujas: {m.number_of_bids} | fin: {m.auction_end_date} | "
            f"keywords: {m.matched_keywords}{risk}\n"
            f"  Artículo: {m.lot_web_url}\n"
            f"  Subasta: {m.lot_reference_url}\n"
            f"  API lote: {m.lot_api_url}\n"
            f"  {desc}"
        )

    elapsed = time.time() - started
    print("\n" + "=" * 88)
    print(f"Lotes escaneados: {scanned_lots}")
    print(f"Matches: {len(matches)}")
    print(f"Tiempo: {elapsed:.1f}s")

    if args.receipt:
        receipt_path = Path(args.receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        complete = bool(receipts) and len(receipts) == len(auction_ids) and all(
            item.get("status") == "complete" for item in receipts
        )
        receipt_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sourceId": "bavastro",
                    "status": "complete" if complete else "partial" if receipts else "failed",
                    "inventoryAuthoritative": complete,
                    "receipts": receipts,
                    "startedAt": datetime.fromtimestamp(started, timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "finishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "lotCount": scanned_lots,
                    "errorCount": sum(int(item.get("errorCount") or 0) for item in receipts),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    with Path(args.output).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "score",
                "auction_id",
                "auction_name",
                "auction_end_date",
                "auction_url",
                "lot_number",
                "lot_auction_id",
                "final_amount",
                "base_price",
                "best_price",
                "number_of_bids",
                "currency_prefix",
                "lot_web_url",
                "lot_reference_url",
                "lot_api_url",
                "image_url",
                "matched_keywords",
                "positive_flags",
                "risk_flags",
                "description",
            ],
        )
        writer.writeheader()
        for m in matches:
            writer.writerow(
                {
                    "score": m.score,
                    "auction_id": m.auction_id,
                    "auction_name": m.auction_name,
                    "auction_end_date": m.auction_end_date,
                    "auction_url": m.auction_url,
                    "lot_number": m.lot_number,
                    "lot_auction_id": m.lot_auction_id,
                    "final_amount": f"{m.final_amount:.2f}",
                    "base_price": f"{m.base_price:.2f}",
                    "best_price": f"{m.best_price:.2f}",
                    "number_of_bids": m.number_of_bids,
                    "currency_prefix": m.currency_prefix,
                    "lot_web_url": m.lot_web_url,
                    "lot_reference_url": m.lot_reference_url,
                    "lot_api_url": m.lot_api_url,
                    "image_url": m.image_url,
                    "matched_keywords": m.matched_keywords,
                    "positive_flags": m.positive_flags,
                    "risk_flags": m.risk_flags,
                    "description": m.description,
                }
            )

    print(f"CSV guardado en: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        sys.exit(1)
