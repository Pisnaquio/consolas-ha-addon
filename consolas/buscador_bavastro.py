#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from auction_search_config import HISTORICAL_BAVASTRO_QUERY, normalize_text

API_BASE = "https://api-parseo.bavastronline.com/published_auctions"
DEFAULT_QUERY = HISTORICAL_BAVASTRO_QUERY
DEFAULT_WINDOW = 2500
DEFAULT_WORKERS = 16
DEFAULT_TIMEOUT = 10
DEFAULT_HEADROOM = 120
DEFAULT_LIST_LIMIT = 100
CSV_FILE = "auctions_bavastro_matches.csv"

session = requests.Session()
session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
        )
    }
)

def get_latest_known_id(timeout: int) -> int | None:
    """Toma el mayor ID visible en el listado público actual (normalmente subastas activas)."""
    try:
        resp = session.get(f"{API_BASE}/", params={"limit": 50}, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        results = data.get("results", [])
        ids = [int(item.get("id", 0)) for item in results if item.get("id")]
        return max(ids) if ids else None
    except requests.RequestException:
        return None


def fetch_auction(auction_id: int, timeout: int) -> dict:
    url = f"{API_BASE}/{auction_id}/"
    try:
        resp = session.get(url, timeout=timeout)
        if resp.status_code == 404:
            return {"id": auction_id, "exists": False, "url": url}

        if resp.status_code != 200:
            return {
                "id": auction_id,
                "exists": False,
                "url": url,
                "error": f"HTTP {resp.status_code}",
            }

        data = resp.json()
        return {
            "id": auction_id,
            "exists": True,
            "url": url,
            "name": data.get("name", ""),
            "state": data.get("state", "unknown"),
            "active": bool(data.get("active", False)),
            "end_date": data.get("end_date"),
        }
    except requests.RequestException as exc:
        return {
            "id": auction_id,
            "exists": False,
            "url": url,
            "error": str(exc),
        }


def is_active_result(result: dict) -> bool:
    if not result.get("exists"):
        return False
    if result.get("active"):
        return True
    state = normalize_text(str(result.get("state") or ""))
    return state in {"active", "published", "open"}


def fetch_current_published_ids(timeout: int, list_limit: int) -> list[int]:
    page = 1
    seen: set[int] = set()
    ids: list[int] = []

    while True:
        resp = session.get(
            f"{API_BASE}/",
            params={"page": page, "limit": max(1, list_limit)},
            timeout=timeout,
        )
        resp.raise_for_status()

        data = resp.json()
        results = data.get("results", [])
        if not results:
            break

        for item in results:
            raw_id = item.get("id")
            if not raw_id:
                continue
            auction_id = int(raw_id)
            if auction_id in seen:
                continue
            seen.add(auction_id)
            ids.append(auction_id)

        if not data.get("next"):
            break
        page += 1

    return sorted(ids)


def discover_active_auctions(timeout: int, list_limit: int, workers: int) -> tuple[list[dict], int]:
    ids = fetch_current_published_ids(timeout=timeout, list_limit=list_limit)
    found: list[dict] = []
    errors = 0

    if not ids:
        return found, errors

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch_auction, aid, timeout): aid for aid in ids}

        for idx, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result.get("error"):
                errors += 1
                print(f"[ERR]   {result['id']} | {result['error']}")
            elif is_active_result(result):
                found.append(result)
                print(
                    f"[ACTIVE] {result['id']} | {result.get('name')} | "
                    f"state={result.get('state')} | {result['url']}"
                )

            if idx % 25 == 0 or idx == len(ids):
                print(f"...progreso: {idx}/{len(ids)}")

    found.sort(key=lambda item: item["id"])
    return found, errors


def match_query(text: str, query: str) -> bool:
    text_n = normalize_text(text)
    terms = [normalize_text(t) for t in query.split(",") if t.strip()]
    return any(term in text_n for term in terms)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Busca subastas de Bavastro por texto usando la API real."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Texto a buscar (coma para OR).")
    parser.add_argument("--start", type=int, help="ID inicial a escanear.")
    parser.add_argument("--end", type=int, help="ID final a escanear.")
    parser.add_argument(
        "--window",
        type=int,
        default=DEFAULT_WINDOW,
        help="Si no se pasa --start, escanea esta ventana hacia atrás desde el ID más reciente.",
    )
    parser.add_argument(
        "--headroom",
        type=int,
        default=DEFAULT_HEADROOM,
        help="IDs extra por encima del último ID público conocido.",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--csv", default=CSV_FILE, help="Ruta CSV de salida.")
    parser.add_argument("--no-csv", action="store_true", help="No guardar CSV.")
    parser.add_argument("--active-only", action="store_true", help="Devuelve todas las subastas activas publicadas hoy.")
    parser.add_argument("--list-limit", type=int, default=DEFAULT_LIST_LIMIT, help="Tamaño de página al leer subastas publicadas.")
    parser.add_argument("--show-miss", action="store_true", help="Mostrar MISS.")
    parser.add_argument("--show-ok", action="store_true", help="Mostrar subastas existentes sin match.")
    return parser.parse_args()


def resolve_range(args: argparse.Namespace) -> tuple[int, int, int | None]:
    latest = get_latest_known_id(args.timeout)

    if args.end:
        end_id = args.end
    elif latest is not None:
        end_id = latest + max(0, args.headroom)
    else:
        end_id = 5000

    if args.start:
        start_id = args.start
    else:
        start_id = max(1, end_id - max(1, args.window) + 1)

    if start_id > end_id:
        start_id, end_id = end_id, start_id

    return start_id, end_id, latest


def main() -> int:
    args = parse_args()

    print("=" * 88)
    print("BUSCADOR BAVASTRO (API)")
    print("=" * 88)
    started_at = time.time()
    found: list[dict] = []
    exists_count = 0
    miss_count = 0
    errors = 0

    if args.active_only:
        print("Modo: subastas activas publicadas")
        print(f"List limit: {max(1, args.list_limit)}")
        print()
        try:
            found, errors = discover_active_auctions(
                timeout=max(1, args.timeout),
                list_limit=max(1, args.list_limit),
                workers=max(1, args.workers),
            )
        except requests.RequestException as exc:
            print(f"[ERR] listado de subastas publicadas: {exc}")
            return 1
        exists_count = len(found)
        total = len(found)
    else:
        start_id, end_id, latest = resolve_range(args)
        total = end_id - start_id + 1
        if total <= 0:
            print("Rango vacío.")
            return 1

        print(f"Consulta: '{args.query}'")
        print(f"Rango: {start_id}..{end_id} ({total} IDs)")
        if latest is not None:
            print(f"Último ID público detectado: {latest}")
        else:
            print("Último ID público detectado: no disponible")
        print()

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(fetch_auction, aid, args.timeout): aid for aid in range(start_id, end_id + 1)}

            for idx, future in enumerate(as_completed(futures), start=1):
                result = future.result()

                if result.get("exists"):
                    exists_count += 1
                    name = result.get("name", "")
                    if match_query(name, args.query):
                        found.append(result)
                        print(
                            f"[MATCH] {result['id']} | {name} | state={result.get('state')} | {result['url']}"
                        )
                    elif args.show_ok:
                        print(f"[OK]    {result['id']} | {name} | state={result.get('state')}")
                else:
                    miss_count += 1
                    if result.get("error"):
                        errors += 1
                        print(f"[ERR]   {result['id']} | {result['error']}")
                    elif args.show_miss:
                        print(f"[MISS]  {result['id']} | {result['url']}")

                if idx % 250 == 0 or idx == total:
                    print(f"...progreso: {idx}/{total}")

        found.sort(key=lambda x: x["id"])

    print("\n" + "=" * 88)
    if args.active_only:
        print("SUBASTAS ACTIVAS ENCONTRADAS")
    else:
        print(f"RESULTADOS PARA '{args.query}'")
    print("=" * 88)
    for item in found:
        print(
            f"- {item['id']} | {item.get('name')} | state={item.get('state')} | "
            f"end={item.get('end_date')} | {item['url']}"
        )

    elapsed = time.time() - started_at
    if args.active_only:
        print(f"\nSubastas activas detectadas: {total}")
    else:
        print(f"\nTotal IDs escaneados: {total}")
    print(f"Subastas existentes: {exists_count}")
    print(f"MISS (404): {miss_count - errors}")
    print(f"Errores red/HTTP: {errors}")
    print(f"Coincidencias: {len(found)}")
    print(f"Tiempo: {elapsed:.1f}s")

    if not args.no_csv and found:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["id", "name", "state", "active", "end_date", "url"],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(found)
        print(f"CSV guardado en: {args.csv}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        raise SystemExit(1)
