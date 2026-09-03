#!/usr/bin/env python3
"""Collect, filter and serialize the registry-backed auction sources."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TextIO

import requests


AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for import_root in (REPO_ROOT, AGENT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from auction_search_config import (  # noqa: E402
    SHARED_KEYWORDS,
    collect_flags,
    compile_patterns,
    matched_terms,
    normalize_text,
    score_match,
)
from sources.model import AuctionLot, SourceScanResult  # noqa: E402
from sources.registry import SourceSpec, configured_sources  # noqa: E402


DEFAULT_MATCHES_CSV = Path("consolas_extra_matches.csv")
DEFAULT_STATUS_JSON = Path("extra_sources_status.json")
SEARCH_PATTERNS = compile_patterns(SHARED_KEYWORDS)
INACTIVE_STATUSES = {
    "cancelado",
    "cancelled",
    "cerrado",
    "closed",
    "ended",
    "finalizado",
    "finished",
    "inactive",
    "inactivo",
    "outofstock",
    "sold",
    "vendido",
}

CANONICAL_MATCH_FIELDS = (
    "source_id",
    "source_label",
    "group_id",
    "group_label",
    "group_url",
    "lot_id",
    "lot_number",
    "title",
    "description",
    "lot_url",
    "image_url",
    "currency",
    "base_price",
    "current_price",
    "next_bid",
    "commission_percent",
    "packaging_cost",
    "bid_count",
    "event_at",
    "closing_at",
    "status",
    "extra_json",
    "matched_keywords",
    "risk_flags",
    "positive_flags",
    "score",
    "total_base_with_commission",
    "total_current_with_commission",
    "total_next_bid_with_commission",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def is_active(lot: AuctionLot) -> bool:
    """Exclude only explicit terminal states; unknown states remain visible."""

    status = normalize_text(str(lot.status or "active")).replace(" ", "")
    return status not in INACTIVE_STATUSES


def lot_identity(lot: AuctionLot) -> tuple[str, str]:
    stable_id = str(lot.lot_id or "").strip() or str(lot.lot_url or "").strip()
    if not stable_id:
        stable_id = f"{lot.group_id}:{lot.lot_number}:{lot.title}"
    return str(lot.source_id or "").strip(), stable_id


def lot_search_text(lot: AuctionLot) -> str:
    # Group names describe mixed auctions/categories and are not evidence that
    # every lot is relevant. Keep them for display, not matching.
    return " ".join(
        str(part or "").strip()
        for part in (lot.title, lot.description)
        if str(part or "").strip()
    )


def find_hits(lot: AuctionLot) -> list[str]:
    return matched_terms(lot_search_text(lot), SEARCH_PATTERNS)


def total_with_commission(
    amount: float,
    commission_percent: float,
    packaging_cost: float,
) -> float:
    amount = float(amount or 0)
    if amount <= 0:
        return 0.0
    total = amount * (1 + max(float(commission_percent or 0), 0) / 100)
    total += max(float(packaging_cost or 0), 0)
    return round(total, 2)


def canonical_match_row(lot: AuctionLot, minimum_score: int | None = None) -> dict[str, Any] | None:
    """Serialize one relevant lot, or return ``None`` when it is not a match."""

    if not is_active(lot):
        return None

    search_text = lot_search_text(lot)
    hits = matched_terms(search_text, SEARCH_PATTERNS)
    if not hits:
        return None

    risk_flags, positive_flags = collect_flags(search_text)
    market_value = float(lot.next_bid or lot.current_price or lot.base_price or 0)
    raw_score = score_match(
        description=search_text,
        hits=hits,
        risk_flags=risk_flags,
        positive_flags=positive_flags,
        market_value=market_value,
        number_of_bids=int(lot.bid_count or 0),
    )
    # Risk is decision context, not a reason to hide a genuinely relevant lot.
    # Existing callers keep the original score semantics; only this broad scan
    # applies a floor and, by default, no minimum-score filter at all.
    score = max(1, raw_score)
    if minimum_score is not None and score < minimum_score:
        return None

    record = lot.to_dict()
    extra = record.pop("extra", {})
    record["extra_json"] = json.dumps(
        extra,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    record["matched_keywords"] = ", ".join(hits)
    record["risk_flags"] = ", ".join(risk_flags)
    record["positive_flags"] = ", ".join(positive_flags)
    record["score"] = score
    record["total_base_with_commission"] = total_with_commission(
        lot.base_price,
        lot.commission_percent,
        lot.packaging_cost,
    )
    record["total_current_with_commission"] = total_with_commission(
        lot.current_price,
        lot.commission_percent,
        lot.packaging_cost,
    )
    record["total_next_bid_with_commission"] = total_with_commission(
        lot.next_bid,
        lot.commission_percent,
        lot.packaging_cost,
    )
    return {field: record.get(field, "") for field in CANONICAL_MATCH_FIELDS}


def merge_enriched_lots(
    originals: list[AuctionLot],
    enriched: Iterable[AuctionLot] | None,
) -> list[AuctionLot]:
    """Replace enriched candidates without dropping candidates that failed enrichment."""

    if enriched is None:
        return originals

    enriched_lots = list(enriched)
    replacement_by_key = {lot_identity(lot): lot for lot in enriched_lots}
    merged = [replacement_by_key.pop(lot_identity(lot), lot) for lot in originals]
    merged.extend(replacement_by_key.values())
    return merged


def normalize_collected_lots(
    result: SourceScanResult,
    spec: SourceSpec,
    errors: list[str],
) -> list[AuctionLot]:
    lots: list[AuctionLot] = []
    seen: set[tuple[str, str]] = set()

    if result.source_id and result.source_id != spec.source_id:
        errors.append(
            f"collect returned source_id={result.source_id!r}; expected {spec.source_id!r}"
        )

    for index, lot in enumerate(result.lots):
        if not isinstance(lot, AuctionLot):
            errors.append(f"lot[{index}] is not an AuctionLot")
            continue
        if not lot.source_id:
            lot.source_id = spec.source_id
        if not lot.source_label:
            lot.source_label = spec.label
        if lot.source_id != spec.source_id:
            errors.append(
                f"lot[{index}] has source_id={lot.source_id!r}; expected {spec.source_id!r}"
            )
            continue
        identity = lot_identity(lot)
        if identity in seen:
            continue
        seen.add(identity)
        lots.append(lot)
    return lots


def write_matches_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_MATCH_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _close_session(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        close()


def run_scan(
    specs: Sequence[SourceSpec],
    matches_csv: Path,
    status_json: Path,
    *,
    timeout: float = 25,
    minimum_score: int | None = None,
    session_factory: Callable[[], requests.Session] = requests.Session,
    output: TextIO = sys.stdout,
) -> int:
    """Run registered sources independently and persist canonical outputs."""

    started_at = now_iso()
    all_rows: list[dict[str, Any]] = []
    source_statuses: list[dict[str, Any]] = []
    successful_sources = 0

    for spec in specs:
        source_started_at = now_iso()
        source_started_clock = time.monotonic()
        errors: list[str] = []
        warnings: list[str] = []
        groups_count = 0
        lots_count = 0
        active_lots_count = 0
        preliminary_matches_count = 0
        source_rows: list[dict[str, Any]] = []
        session: requests.Session | None = None
        receipts: list[dict[str, Any]] = []
        discovery_complete = False
        coverage_complete = False
        collection_succeeded = False
        status = "failed"

        try:
            adapter = spec.load()
            session = session_factory()
            result = adapter.collect(session=session, timeout=timeout)
            if not isinstance(result, SourceScanResult):
                raise TypeError(
                    f"collect() returned {type(result).__name__}, expected SourceScanResult"
                )

            groups_count = len(result.groups)
            errors.extend(str(item) for item in (result.errors or []) if str(item).strip())
            collection_succeeded = not (errors and not result.groups and not result.lots)
            receipts = [receipt.to_dict() for receipt in result.receipts]
            lots = normalize_collected_lots(result, spec, errors)
            discovery_complete = bool(result.discovery_complete)
            receipt_group_ids = {str(item.get("groupId") or "") for item in receipts}
            coverage_complete = discovery_complete and not errors and (
                len(receipts) == len(result.groups)
                and receipt_group_ids == {group.group_id for group in result.groups}
                and all(item.get("status") == "complete" for item in receipts)
            )
            lots_count = len(lots)
            active_lots = [lot for lot in lots if is_active(lot)]
            active_lots_count = len(active_lots)
            candidates = [lot for lot in active_lots if find_hits(lot)]
            preliminary_matches_count = len(candidates)

            enrich_lots = getattr(adapter, "enrich_lots", None)
            if candidates and callable(enrich_lots):
                try:
                    enriched = enrich_lots(
                        session=session,
                        lots=candidates,
                        timeout=timeout,
                    )
                    candidates = merge_enriched_lots(candidates, enriched)
                    enrichment_errors = [
                        str(item)
                        for item in (getattr(adapter, "last_enrichment_errors", []) or [])
                        if str(item).strip()
                    ]
                    if coverage_complete:
                        warnings.extend(enrichment_errors)
                    else:
                        errors.extend(enrichment_errors)
                    warnings.extend(
                        str(item)
                        for item in (getattr(adapter, "last_enrichment_warnings", []) or [])
                        if str(item).strip()
                    )
                except Exception as exc:  # keep preliminary matches on detail/API failure
                    detail = f"enrich_lots failed: {type(exc).__name__}: {exc}"
                    if coverage_complete:
                        warnings.append(detail)
                    else:
                        errors.append(detail)

            for lot in candidates:
                row = canonical_match_row(lot, minimum_score=minimum_score)
                if row is not None:
                    source_rows.append(row)

            # Avoid accidental duplicate cards while retaining every distinct lot.
            rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
            for row in source_rows:
                key = (
                    str(row.get("source_id") or ""),
                    str(row.get("lot_id") or row.get("lot_url") or ""),
                )
                rows_by_key.setdefault(key, row)
            source_rows = list(rows_by_key.values())
            status = "failed" if not collection_succeeded else "partial" if errors else "success"
            if collection_succeeded:
                successful_sources += 1
        except Exception as exc:
            status = "failed"
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            if session is not None:
                _close_session(session)

        all_rows.extend(source_rows)
        duration_ms = int((time.monotonic() - source_started_clock) * 1000)
        source_statuses.append(
            {
                "source_id": spec.source_id,
                "label": spec.label,
                "status": status,
                "groups": groups_count,
                "lots": lots_count,
                "active_lots": active_lots_count,
                "preliminary_matches": preliminary_matches_count,
                "matches": len(source_rows),
                "errors": errors,
                "warnings": warnings,
                "receipts": receipts,
                "discovery_complete": discovery_complete,
                "inventory_authoritative": coverage_complete,
                "started_at": source_started_at,
                "finished_at": now_iso(),
                "duration_ms": duration_ms,
            }
        )
        print(
            f"[{spec.source_id}] status={status} groups={groups_count} "
            f"lots={lots_count} active={active_lots_count} matches={len(source_rows)} "
            f"errors={len(errors)} warnings={len(warnings)}",
            file=output,
        )

    all_rows.sort(
        key=lambda row: (
            -int(row.get("score") or 0),
            str(row.get("source_id") or ""),
            str(row.get("group_id") or ""),
            str(row.get("lot_number") or ""),
            str(row.get("lot_id") or ""),
        )
    )

    failed_sources = sum(item["status"] == "failed" for item in source_statuses)
    partial_sources = sum(item["status"] == "partial" for item in source_statuses)
    payload = {
        "generated_at": now_iso(),
        "started_at": started_at,
        "status": (
            "failed"
            if successful_sources == 0
            else "partial"
            if failed_sources or partial_sources
            else "success"
        ),
        "sources": source_statuses,
        "inventory_authoritative": bool(source_statuses)
        and all(item["inventory_authoritative"] is True for item in source_statuses),
        "totals": {
            "configured_sources": len(specs),
            "successful_sources": successful_sources,
            "partial_sources": partial_sources,
            "failed_sources": failed_sources,
            "groups": sum(int(item["groups"]) for item in source_statuses),
            "lots": sum(int(item["lots"]) for item in source_statuses),
            "active_lots": sum(int(item["active_lots"]) for item in source_statuses),
            "matches": len(all_rows),
        },
    }
    write_matches_csv(matches_csv, all_rows)
    write_source_status(status_json, payload)

    print(
        f"[total] status={payload['status']} sources_ok={successful_sources}/{len(specs)} "
        f"matches={len(all_rows)} csv={matches_csv} status_json={status_json}",
        file=output,
    )
    return 0 if successful_sources > 0 else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Escanea fuentes adicionales de remates y genera un CSV canónico."
    )
    parser.add_argument(
        "--output-csv",
        "--matches-csv",
        dest="matches_csv",
        type=Path,
        default=DEFAULT_MATCHES_CSV,
    )
    parser.add_argument(
        "--status-json",
        "--source-status-json",
        dest="status_json",
        type=Path,
        default=DEFAULT_STATUS_JSON,
    )
    parser.add_argument(
        "--source",
        dest="source_ids",
        action="append",
        help="Id de fuente a ejecutar; se puede repetir. Por defecto ejecuta todas.",
    )
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="Filtro opcional. Si se omite, conserva todo match activo aunque tenga riesgo.",
    )
    parser.add_argument("--list-sources", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        specs = configured_sources(args.source_ids)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    if args.list_sources:
        for spec in specs:
            print(f"{spec.source_id}\t{spec.label}\t{spec.adapter_path}")
        return 0
    if args.timeout <= 0:
        print("[error] --timeout must be greater than zero", file=sys.stderr)
        return 2

    return run_scan(
        specs,
        args.matches_csv,
        args.status_json,
        timeout=args.timeout,
        minimum_score=args.min_score,
    )


if __name__ == "__main__":
    raise SystemExit(main())
