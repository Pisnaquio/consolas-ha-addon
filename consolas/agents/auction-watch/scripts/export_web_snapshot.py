#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
LATEST_DIR = REPO_ROOT / "agents" / "auction-watch" / "runs" / "latest"
DEFAULT_OUTPUT = REPO_ROOT / "web" / "runtime" / "auction-watch.json"
DEFAULT_DISMISSALS = REPO_ROOT / "agents" / "auction-watch" / "dismissals-cache.json"
EXTRA_MATCHES_FILENAME = "consolas_extra_matches.csv"
PUBLICATION_LIFECYCLE_VERSION = 1

STEP_SOURCE_LABELS = {
    "bavastro_discovery": "Bavastro",
    "bavastro_matches": "Bavastro",
    "castells_discovery": "Castells",
    "castells_matches": "Castells",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exporta snapshot web same-origin de auction-watch.")
    parser.add_argument("--input-dir", default=str(LATEST_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--dismissals",
        default=str(DEFAULT_DISMISSALS),
        help=(
            "Compatibilidad operativa: el archivo se acepta pero nunca filtra el "
            "snapshot crudo. Los descartes se aplican en HA/SQLite."
        ),
    )
    return parser.parse_args()


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def split_list(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def parse_dt(raw: str) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
      return None
    if value.endswith("Z"):
      value = value[:-1] + "+00:00"
    try:
      parsed = datetime.fromisoformat(value)
    except ValueError:
      return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def format_remaining(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds <= 0:
        return "cerrado"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def urgency_label(seconds: int | None) -> str:
    if seconds is None:
        return "seguimiento"
    if seconds <= 0:
        return "cerrado"
    if seconds <= 6 * 3600:
        return "cierre inminente"
    if seconds <= 24 * 3600:
        return "cierra hoy"
    if seconds <= 72 * 3600:
        return "cierra pronto"
    return "seguimiento"


def normalize_currency(raw: str) -> str:
    value = str(raw or "").strip().upper()
    if value in {"$", "$UY", "UYU", "PESOS URUGUAYOS"}:
        return "UYU"
    if value in {"USD", "US$", "$USD"}:
        return "USD"
    return value or "UYU"


def bavastro_public_url(raw: str, auction_id: str) -> str:
    auction = str(auction_id or "").strip()
    if auction.isdigit():
        return f"https://www.bavastronline.com.uy/auctions/{auction}"
    return str(raw or "").strip()


def row_float(row: dict[str, str], key: str) -> float | None:
    raw = str(row.get(key) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def build_watch_lookup(metadata: dict) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for entry in (((metadata.get("watchlist") or {}).get("active_hits")) or []):
        lot_url = str(entry.get("lot_url") or "").strip()
        if lot_url:
            lookup[lot_url] = entry
    return lookup


def build_featured(metadata: dict) -> dict | None:
    watch_hits = ((metadata.get("watchlist") or {}).get("active_hits")) or []
    if not watch_hits:
        return None

    hit = watch_hits[0]
    return {
        "watchId": hit.get("watch_id"),
        "consoleIds": [],
        "source": hit.get("source"),
        "groupId": hit.get("group_id") or "",
        "lotId": hit.get("lot_id") or "",
        "title": hit.get("label") or "Seguimiento prioritario",
        "description": hit.get("description") or "",
        "matchedKeywords": split_list(hit.get("matched_keywords", "")),
        "urgencyLabel": hit.get("urgency_label") or "seguimiento",
        "remainingText": hit.get("remaining_text") or "-",
        "closingAt": hit.get("closing_at_iso") or "",
        "priceLabel": hit.get("price_label") or "",
        "lotUrl": hit.get("lot_url") or "",
        "groupUrl": hit.get("group_url") or "",
        "imageUrl": hit.get("image_url") or "",
        "notes": hit.get("notes") or "",
        "watchlist": True,
    }


def enrich_featured_identity(featured: dict | None, matches: list[dict]) -> dict | None:
    if not featured:
        return None
    lot_url = str(featured.get("lotUrl") or "").strip()
    source_id = str(featured.get("source") or "").strip().lower()
    match = next(
        (
            item
            for item in matches
            if str(item.get("lotUrl") or "").strip() == lot_url
            and str(item.get("source") or "").strip().lower() == source_id
        ),
        None,
    )
    if not match:
        return featured
    return {
        **featured,
        "id": match.get("id") or featured.get("id") or "",
        "groupId": match.get("groupId") or "",
        "groupLabel": match.get("groupLabel") or "",
        "lotId": match.get("lotId") or "",
        "lotNumber": match.get("lotNumber") or "",
    }


def normalize_castells_rows(rows: list[dict[str, str]], watch_lookup: dict[str, dict], now: datetime) -> list[dict]:
    items: list[dict] = []
    for row in rows:
        lot_url = str(row.get("lot_url") or "").strip()
        watch = watch_lookup.get(lot_url) or {}
        closing_at = str(row.get("closing_at") or "").strip()
        closing_dt = parse_dt(closing_at)
        remaining_seconds = int((closing_dt - now).total_seconds()) if closing_dt else None
        next_bid_with_commission = row_float(row, "next_bid_with_commission")
        lot_id = str(row.get("lot_id") or "").strip()
        items.append(
            {
                "id": f"castells-{lot_id or row.get('remate_id', '').strip()}",
                "watchId": watch.get("watch_id") or "",
                "consoleIds": [],
                "source": "castells",
                "groupId": str(row.get("remate_id") or "").strip(),
                "groupLabel": f"Remate {str(row.get('remate_id') or '').strip()}",
                "lotId": lot_id,
                "lotNumber": str(row.get("lot_number") or "").strip(),
                "title": str(watch.get("label") or "").strip() or str(row.get("lot_description") or "").strip(),
                "description": str(row.get("lot_description") or "").strip(),
                "score": int(float(str(row.get("score") or "0") or 0)),
                "matchedKeywords": split_list(row.get("matched_keywords", "")),
                "positiveFlags": split_list(row.get("positive_flags", "")),
                "riskFlags": split_list(row.get("risk_flags", "")),
                "closingAt": closing_at,
                "remainingText": str(watch.get("remaining_text") or "").strip() or format_remaining(remaining_seconds),
                "urgencyLabel": str(watch.get("urgency_label") or "").strip() or urgency_label(remaining_seconds),
                "priceValue": next_bid_with_commission,
                "priceCurrency": normalize_currency(row.get("currency", "")),
                "priceLabel": str(watch.get("price_label") or "").strip()
                or (
                    f"Proxima puja c/comision: $UY{next_bid_with_commission:,.2f}"
                    if next_bid_with_commission is not None
                    else "Sin puja visible"
                ),
                "lotUrl": lot_url,
                "groupUrl": str(row.get("remate_url") or "").strip(),
                "imageUrl": str(row.get("image_url") or "").strip(),
                "watchlist": bool(watch),
                "notes": str(watch.get("notes") or "").strip(),
            }
        )
    return items


def normalize_bavastro_rows(rows: list[dict[str, str]], watch_lookup: dict[str, dict], now: datetime) -> list[dict]:
    items: list[dict] = []
    for row in rows:
        lot_url = str(row.get("lot_web_url") or "").strip()
        watch = watch_lookup.get(lot_url) or {}
        closing_at = str(row.get("auction_end_date") or row.get("end_date") or "").strip()
        closing_dt = parse_dt(closing_at)
        remaining_seconds = int((closing_dt - now).total_seconds()) if closing_dt else None
        final_amount = row_float(row, "final_amount")
        auction_id = str(row.get("auction_id") or row.get("id") or "").strip()
        lot_id = str(row.get("lot_auction_id") or row.get("lot_id") or "").strip()
        items.append(
            {
                "id": f"bavastro-{lot_id or auction_id}",
                "watchId": watch.get("watch_id") or "",
                "consoleIds": [],
                "source": "bavastro",
                "groupId": auction_id,
                "groupLabel": f"Subasta {auction_id}" if auction_id else "Subasta Bavastro",
                "lotId": lot_id,
                "lotNumber": str(row.get("lot_number") or "").strip(),
                "title": str(watch.get("label") or "").strip() or str(row.get("name") or row.get("description") or "").strip(),
                "description": str(row.get("description") or row.get("name") or "").strip(),
                "score": int(float(str(row.get("score") or "0") or 0)),
                "matchedKeywords": split_list(row.get("matched_keywords", "")),
                "positiveFlags": split_list(row.get("positive_flags", "")),
                "riskFlags": split_list(row.get("risk_flags", "")),
                "closingAt": closing_at,
                "remainingText": str(watch.get("remaining_text") or "").strip() or format_remaining(remaining_seconds),
                "urgencyLabel": str(watch.get("urgency_label") or "").strip() or urgency_label(remaining_seconds),
                "priceValue": final_amount,
                "priceCurrency": normalize_currency(row.get("currency_prefix", "")),
                "priceLabel": str(watch.get("price_label") or "").strip()
                or (f"Monto actual: {final_amount:,.2f}" if final_amount is not None else "Sin monto visible"),
                "lotUrl": lot_url,
                "groupUrl": bavastro_public_url(row.get("auction_url", ""), auction_id),
                "imageUrl": str(row.get("image_url") or "").strip(),
                "watchlist": bool(watch),
                "notes": str(watch.get("notes") or "").strip(),
            }
        )
    return items


def normalize_extra_rows(rows: list[dict[str, str]], watch_lookup: dict[str, dict], now: datetime) -> list[dict]:
    items: list[dict] = []
    for row in rows:
        source_id = str(row.get("source_id") or "").strip().lower()
        lot_url = str(row.get("lot_url") or "").strip()
        watch = watch_lookup.get(lot_url) or {}
        closing_at = str(row.get("closing_at") or row.get("event_at") or "").strip()
        closing_dt = parse_dt(closing_at)
        remaining_seconds = int((closing_dt - now).total_seconds()) if closing_dt else None
        price_value = (
            row_float(row, "total_next_bid_with_commission")
            or row_float(row, "total_current_with_commission")
            or row_float(row, "total_base_with_commission")
        )
        currency = normalize_currency(row.get("currency", ""))
        currency_label = "USD " if currency == "USD" else "$"
        lot_id = str(row.get("lot_id") or "").strip()
        group_id = str(row.get("group_id") or "").strip()
        items.append(
            {
                "id": f"{source_id}-{lot_id or group_id}",
                "watchId": watch.get("watch_id") or "",
                "consoleIds": [],
                "source": source_id,
                "groupId": group_id,
                "groupLabel": str(row.get("group_label") or "").strip() or f"Remate {group_id}",
                "lotId": lot_id,
                "lotNumber": str(row.get("lot_number") or "").strip(),
                "title": str(watch.get("label") or "").strip() or str(row.get("title") or "").strip(),
                "description": str(row.get("description") or row.get("title") or "").strip(),
                "score": int(float(str(row.get("score") or "0") or 0)),
                "matchedKeywords": split_list(row.get("matched_keywords", "")),
                "positiveFlags": split_list(row.get("positive_flags", "")),
                "riskFlags": split_list(row.get("risk_flags", "")),
                "closingAt": closing_at,
                "remainingText": str(watch.get("remaining_text") or "").strip() or format_remaining(remaining_seconds),
                "urgencyLabel": str(watch.get("urgency_label") or "").strip() or urgency_label(remaining_seconds),
                "priceValue": price_value,
                "priceCurrency": currency,
                "priceLabel": str(watch.get("price_label") or "").strip()
                or (f"Estimado c/cargos: {currency_label}{price_value:,.2f}" if price_value is not None else "Sin precio visible"),
                "lotUrl": lot_url,
                "groupUrl": str(row.get("group_url") or "").strip(),
                "imageUrl": str(row.get("image_url") or "").strip(),
                "watchlist": bool(watch),
                "notes": str(watch.get("notes") or "").strip(),
            }
        )
    return items


def sort_matches(items: list[dict]) -> list[dict]:
    def key(item: dict) -> tuple:
        closing_dt = parse_dt(item.get("closingAt", ""))
        closing_rank = closing_dt.timestamp() if closing_dt else 10**18
        return (not bool(item.get("watchlist")), closing_rank, -(item.get("score") or 0), str(item.get("title") or "").lower())

    return sorted(items, key=key)


def summarize_issue_detail(raw: str) -> str:
    detail = str(raw or "").strip()
    normalized = detail.lower()
    if "connection reset by peer" in normalized:
        return "El servidor cortó la conexión mientras se consultaban los lotes."
    if "timed out" in normalized or "timeout" in normalized:
        return "El servidor no respondió dentro del tiempo esperado."
    if "connection refused" in normalized:
        return "El servidor rechazó la conexión."
    if detail:
        return detail[:240] + ("…" if len(detail) > 240 else "")
    return "La fuente no pudo completar la consulta."


def build_run_issues(run_json: dict) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    reported_sources: set[str] = set()
    extra_sources = (run_json.get("extra_sources") or {}).get("sources") or []

    for source in extra_sources:
        if not isinstance(source, dict):
            continue
        status = str(source.get("status") or "unknown").strip().lower()
        if status not in {"failed", "partial"}:
            continue
        source_id = str(source.get("source_id") or "").strip().lower()
        errors = source.get("errors") or []
        detail = next((str(item).strip() for item in errors if str(item).strip()), "")
        issues.append(
            {
                "sourceId": source_id,
                "sourceLabel": str(source.get("label") or source_id or "Fuente externa"),
                "status": status,
                "summary": summarize_issue_detail(detail),
            }
        )
        if source_id:
            reported_sources.add(source_id)

    for step in run_json.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("status") or "").lower() != "failed":
            continue
        step_name = str(step.get("name") or "").strip()
        source_id = step_name.split("_", 1)[0].lower()
        if source_id in reported_sources or step_name == "extra_sources":
            continue
        source_label = STEP_SOURCE_LABELS.get(step_name, step_name.replace("_", " ").title())
        exit_code = step.get("exit_code")
        suffix = f" (código {exit_code})" if exit_code is not None else ""
        issues.append(
            {
                "sourceId": source_id,
                "sourceLabel": source_label,
                "status": "failed",
                "summary": f"La consulta de esta fuente no pudo completarse{suffix}.",
            }
        )
        if source_id:
            reported_sources.add(source_id)

    return issues


def source_health_from_steps(run_json: dict) -> dict[str, dict[str, object]]:
    steps_by_name = {
        str(step.get("name") or ""): step
        for step in run_json.get("steps") or []
        if isinstance(step, dict)
    }

    def combine(discovery_name: str, matches_name: str) -> dict[str, object]:
        discovery = steps_by_name.get(discovery_name) or {}
        matches = steps_by_name.get(matches_name) or {}
        statuses = [
            str(discovery.get("status") or "unknown").lower(),
            str(matches.get("status") or "unknown").lower(),
        ]
        if "failed" in statuses:
            status = "failed"
        elif "partial" in statuses:
            status = "partial"
        elif all(status in {"success", "skipped"} for status in statuses):
            status = "success"
        else:
            status = "unknown"
        return {
            "status": status,
            "inventoryAuthoritative": bool(
                discovery.get("inventory_authoritative") is True
                and matches.get("inventory_authoritative") is True
                and status == "success"
            ),
        }

    health = {
        "bavastro": combine("bavastro_discovery", "bavastro_matches"),
        "castells": combine("castells_discovery", "castells_matches"),
    }
    for source in ((run_json.get("extra_sources") or {}).get("sources") or []):
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "").strip().lower()
        status = str(source.get("status") or "unknown").strip().lower()
        if source_id and status in {"success", "partial", "failed"}:
            health[source_id] = {
                "status": status,
                "inventoryAuthoritative": bool(
                    source.get("inventory_authoritative") is True and status == "success"
                ),
            }
    return health


def build_publication_lifecycle(run_json: dict, all_matches: list[dict]) -> dict:
    active_keys = sorted(
        {
            (str(item.get("source") or "").strip().lower(), str(item.get("lotId") or "").strip())
            for item in all_matches
            if str(item.get("source") or "").strip() and str(item.get("lotId") or "").strip()
        }
    )
    return {
        "version": PUBLICATION_LIFECYCLE_VERSION,
        "activeKeys": [
            {"sourceId": source_id, "lotId": lot_id}
            for source_id, lot_id in active_keys
        ],
        "sourceHealth": source_health_from_steps(run_json),
    }


def build_active_match_metadata(all_matches: list[dict]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in all_matches:
        source_id = str(item.get("source") or "").strip().lower()
        lot_id = str(item.get("lotId") or "").strip()
        image_url = str(item.get("imageUrl") or "").strip()
        if source_id and lot_id and image_url:
            items.append({"sourceId": source_id, "lotId": lot_id, "imageUrl": image_url})
    return items


def export_snapshot(
    input_dir: Path,
    output_path: Path,
    dismissals_path: Path | None = None,
) -> dict:
    run_json = read_json(input_dir / "run.json")
    if not run_json:
        raise FileNotFoundError(f"No se encontró run.json en {input_dir}")

    generated_at = datetime.now().astimezone()
    now = parse_dt(str(run_json.get("finished_at") or "")) or generated_at
    watch_lookup = build_watch_lookup(run_json)
    castells_rows = read_csv_rows(input_dir / "consolas_castells_matches.csv")
    bavastro_rows = read_csv_rows(input_dir / "consolas_bavastro_matches.csv")
    extra_rows = read_csv_rows(input_dir / EXTRA_MATCHES_FILENAME)

    all_matches = sort_matches(
        normalize_castells_rows(castells_rows, watch_lookup, now)
        + normalize_bavastro_rows(bavastro_rows, watch_lookup, now)
        + normalize_extra_rows(extra_rows, watch_lookup, now)
    )
    # Publication is an immutable inventory fact. `dismissals_path` remains in
    # the signature for old callers, but local/cache decisions must never alter
    # what HA receives. SQLite owns the visible/followed/dismissed projection.
    del dismissals_path
    matches = all_matches
    featured = enrich_featured_identity(build_featured(run_json), all_matches)
    counts = dict(run_json.get("counts") or {})
    source_counts: dict[str, int] = {}
    for item in matches:
        source_id = str(item.get("source") or "").strip().lower()
        if source_id:
            source_counts[source_id] = source_counts.get(source_id, 0) + 1
    extra_counts = {
        source_id: count
        for source_id, count in source_counts.items()
        if source_id not in {"bavastro", "castells"}
    }
    counts["bavastro_matches"] = source_counts.get("bavastro", 0)
    counts["castells_matches"] = source_counts.get("castells", 0)
    counts["extra_matches"] = sum(extra_counts.values())
    counts["extra_matches_by_source"] = extra_counts
    counts["detected_matches"] = len(matches)
    counts["dismissed_matches"] = 0
    counts["total_matches"] = len(matches)
    legacy_status = str(run_json.get("status") or "unknown")
    raw_scan_status = str(run_json.get("scanStatus") or legacy_status).strip().lower()
    scan_status = {
        "success": "success",
        "partial": "partial",
        "partial_failure": "partial",
        "failed": "failed",
        "failure": "failed",
    }.get(raw_scan_status, "failed")
    payload = {
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "runId": run_json.get("run_id") or "",
        "status": legacy_status,
        "scanStatus": scan_status,
        "issues": build_run_issues(run_json),
        "publicationLifecycle": build_publication_lifecycle(run_json, all_matches),
        "activeMatchMetadata": build_active_match_metadata(all_matches),
        "counts": counts,
        "featured": featured,
        "matches": matches,
        "dismissalsApplied": 0,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    args = parse_args()
    payload = export_snapshot(
        Path(args.input_dir),
        Path(args.output),
        Path(args.dismissals),
    )
    print(
        json.dumps(
            {
                "output": str(Path(args.output)),
                "runId": payload.get("runId"),
                "status": payload.get("status"),
                "matches": len(payload.get("matches") or []),
                "featured": bool(payload.get("featured")),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
