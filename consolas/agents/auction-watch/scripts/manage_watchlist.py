#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "agents" / "auction-watch"
WATCHLIST_FILE = AGENT_DIR / "watchlist.json"
LATEST_DIR = AGENT_DIR / "runs" / "latest"
CASTELLS_MATCHES_CSV = LATEST_DIR / "consolas_castells_matches.csv"
BAVASTRO_MATCHES_CSV = LATEST_DIR / "consolas_bavastro_matches.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Administra la watchlist que define el lote destacado de auction-watch."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="Lista los lotes configurados hoy como seguimiento prioritario.")
    subparsers.add_parser("active", help="Muestra los matches activos de la ultima corrida para elegir uno.")

    promote_parser = subparsers.add_parser(
        "promote",
        help="Promueve un lote activo de la ultima corrida a seguimiento prioritario.",
    )
    promote_parser.add_argument("--source", choices=["castells", "bavastro"], required=True)
    promote_group = promote_parser.add_mutually_exclusive_group(required=True)
    promote_group.add_argument("--lot-id", help="ID del lote activo.")
    promote_group.add_argument("--lot-url", help="URL del lote activo.")
    promote_group.add_argument("--index", type=int, help="Indice mostrado por el comando `active`.")
    promote_parser.add_argument("--label", help="Titulo personalizado para el destacado.")
    promote_parser.add_argument("--notes", default="", help="Nota corta para el mail y la web.")
    promote_parser.add_argument(
        "--priority",
        type=int,
        default=100,
        help="Prioridad manual. Menor numero = mas prioridad para salir como destacado.",
    )

    remove_parser = subparsers.add_parser(
        "remove",
        help="Saca un lote de la watchlist / categoria de lote destacado.",
    )
    remove_group = remove_parser.add_mutually_exclusive_group(required=True)
    remove_group.add_argument("--id", help="ID interno de la watchlist.")
    remove_group.add_argument("--lot-id", help="ID del lote a sacar.")
    remove_group.add_argument("--lot-url", help="URL del lote a sacar.")

    return parser.parse_args()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_watchlist() -> list[dict[str, object]]:
    if not WATCHLIST_FILE.exists():
        return []
    try:
        payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def save_watchlist(items: list[dict[str, object]]) -> None:
    WATCHLIST_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def shorten_text(value: object, max_chars: int = 78) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def slugify(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    cleaned = "-".join(part for part in normalized.split("-") if part)
    return cleaned or "watch-item"


def row_title(source: str, row: dict[str, str]) -> str:
    if source == "castells":
        return str(row.get("lot_description") or "").strip()
    return str(row.get("description") or "").strip()


def row_lot_id(source: str, row: dict[str, str]) -> str:
    if source == "castells":
        return str(row.get("lot_id") or "").strip()
    return str(row.get("lot_auction_id") or row.get("lot_id") or "").strip()


def row_lot_url(source: str, row: dict[str, str]) -> str:
    if source == "castells":
        return str(row.get("lot_url") or "").strip()
    return str(row.get("lot_web_url") or "").strip()


def row_group_label(source: str, row: dict[str, str]) -> str:
    if source == "castells":
        return f"Remate {str(row.get('remate_id') or '').strip()}"
    return f"Subasta {str(row.get('auction_id') or '').strip()}"


def active_rows_by_source() -> dict[str, list[dict[str, str]]]:
    return {
        "castells": read_csv_rows(CASTELLS_MATCHES_CSV),
        "bavastro": read_csv_rows(BAVASTRO_MATCHES_CSV),
    }


def print_watchlist(items: list[dict[str, object]]) -> int:
    if not items:
        print("No hay lotes en watchlist. Ningun lote saldra como destacado.")
        return 0

    print("Watchlist actual:")
    for index, item in enumerate(items, start=1):
        print(
            f"{index}. id={item.get('id')} | source={item.get('source')} | "
            f"priority={item.get('priority', 100)} | lot_id={item.get('lot_id', '')} | label={item.get('label', '')}"
        )
        if item.get("notes"):
            print(f"   nota: {item.get('notes')}")
        if item.get("lot_url"):
            print(f"   url : {item.get('lot_url')}")
    return 0


def print_active(rows_by_source: dict[str, list[dict[str, str]]], watchlist: list[dict[str, object]]) -> int:
    active_total = sum(len(rows) for rows in rows_by_source.values())
    if active_total == 0:
        print("No hay matches activos en la ultima corrida.")
        return 1

    watch_urls = {str(item.get("lot_url") or "").strip() for item in watchlist}
    watch_ids = {str(item.get("lot_id") or "").strip() for item in watchlist}
    shown_index = 1
    for source in ["castells", "bavastro"]:
        rows = rows_by_source.get(source) or []
        if not rows:
            continue
        print(f"{source.upper()} activos:")
        for row in rows:
            lot_id = row_lot_id(source, row)
            lot_url = row_lot_url(source, row)
            already = " ⭐" if (lot_id and lot_id in watch_ids) or (lot_url and lot_url in watch_urls) else ""
            title = shorten_text(row_title(source, row), 100)
            group = row_group_label(source, row)
            print(f"{shown_index}. [{source}] {group} | lot_id={lot_id}{already}")
            print(f"   {title}")
            print(f"   url: {lot_url}")
            shown_index += 1
        print("")
    print("⭐ = ya esta en watchlist / puede salir como lote destacado")
    return 0


def find_active_row(source: str, *, lot_id: str = "", lot_url: str = "", index: int | None = None) -> dict[str, str] | None:
    rows_by_source = active_rows_by_source()
    if index is not None:
        flat_rows: list[tuple[str, dict[str, str]]] = []
        for candidate_source in ["castells", "bavastro"]:
            for row in rows_by_source.get(candidate_source) or []:
                flat_rows.append((candidate_source, row))
        if index < 1 or index > len(flat_rows):
            return None
        selected_source, row = flat_rows[index - 1]
        if selected_source != source:
            return None
        return row

    for row in rows_by_source.get(source) or []:
        if lot_id and row_lot_id(source, row) == lot_id:
            return row
        if lot_url and row_lot_url(source, row) == lot_url:
            return row
    return None


def promote_item(args: argparse.Namespace) -> int:
    row = find_active_row(
        args.source,
        lot_id=str(args.lot_id or "").strip(),
        lot_url=str(args.lot_url or "").strip(),
        index=args.index,
    )
    if row is None:
        print("No encontre ese lote activo en la ultima corrida.", file=sys.stderr)
        return 1

    watchlist = load_watchlist()
    lot_id = row_lot_id(args.source, row)
    lot_url = row_lot_url(args.source, row)
    title = row_title(args.source, row)
    label = str(args.label or "").strip() or shorten_text(title, 72)
    watch_id = f"{args.source}-{slugify(label)}-{lot_id or slugify(lot_url)}"

    entry = {
        "id": watch_id,
        "label": label,
        "source": args.source,
        "priority": int(args.priority),
        "lot_id": lot_id,
        "lot_url": lot_url,
        "description_contains": title[:140],
        "notes": str(args.notes or "").strip(),
    }

    replaced = False
    for idx, item in enumerate(watchlist):
        same_lot = (lot_id and str(item.get("lot_id") or "").strip() == lot_id) or (
            lot_url and str(item.get("lot_url") or "").strip() == lot_url
        )
        if same_lot:
            watchlist[idx] = entry
            replaced = True
            break

    if not replaced:
        watchlist.append(entry)

    save_watchlist(watchlist)
    print("Watchlist actualizada.")
    print(f"- source: {args.source}")
    print(f"- priority: {int(args.priority)}")
    print(f"- lot_id: {lot_id}")
    print(f"- label : {label}")
    print(f"- url   : {lot_url}")
    print("")
    print("Este lote quedara como candidato a lote destacado en la proxima corrida.")
    return 0


def remove_item(args: argparse.Namespace) -> int:
    watchlist = load_watchlist()
    before = len(watchlist)

    def keep(item: dict[str, object]) -> bool:
        if args.id and str(item.get("id") or "").strip() == args.id:
            return False
        if args.lot_id and str(item.get("lot_id") or "").strip() == args.lot_id:
            return False
        if args.lot_url and str(item.get("lot_url") or "").strip() == args.lot_url:
            return False
        return True

    next_items = [item for item in watchlist if keep(item)]
    if len(next_items) == before:
        print("No encontre ningun item para sacar.", file=sys.stderr)
        return 1

    save_watchlist(next_items)
    print("Item removido de la watchlist.")
    if not next_items:
        print("La watchlist quedo vacia: ya no habra lote destacado hasta que promociones otro.")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "list":
        return print_watchlist(load_watchlist())
    if args.command == "active":
        return print_active(active_rows_by_source(), load_watchlist())
    if args.command == "promote":
        return promote_item(args)
    if args.command == "remove":
        return remove_item(args)
    print(f"Comando no soportado: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
