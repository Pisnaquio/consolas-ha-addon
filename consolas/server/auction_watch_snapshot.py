"""Auction Watch: snapshot filtering, publication receipts and publishing."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

try:
    from .auction_watch_dismissals import (
        list_auction_watch_dismissals,
        normalize_active_match_metadata,
        normalize_auction_watch_identity,
        normalize_auction_watch_lifecycle,
        reconcile_auction_watch_dismissals,
    )
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS,
        AUCTION_WATCH_RECEIPT_FILE,
        _AUCTION_WATCH_SNAPSHOT_LOCK,
    )
    from .database import connect_db
    from .errors import ApiError
    from .json_files import read_json_object, write_json_object_atomic
except ImportError:  # Direct execution: `python3 server/app.py`.
    from auction_watch_dismissals import (
        list_auction_watch_dismissals,
        normalize_active_match_metadata,
        normalize_auction_watch_identity,
        normalize_auction_watch_lifecycle,
        reconcile_auction_watch_dismissals,
    )
    from clock import utc_now
    from config import AppConfig
    from constants import (
        AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS,
        AUCTION_WATCH_RECEIPT_FILE,
        _AUCTION_WATCH_SNAPSHOT_LOCK,
    )
    from database import connect_db
    from errors import ApiError
    from json_files import read_json_object, write_json_object_atomic


def filter_auction_watch_snapshot(config: AppConfig, snapshot: dict[str, Any]) -> dict[str, Any]:
    dismissal_payload = list_auction_watch_dismissals(config)
    dismissed_keys = {
        (str(item.get("sourceId") or "").lower(), str(item.get("lotId") or ""))
        for item in dismissal_payload["items"]
    }
    if not dismissed_keys:
        return snapshot

    filtered = dict(snapshot)
    raw_matches = snapshot.get("matches") if isinstance(snapshot.get("matches"), list) else []
    visible_matches = [
        item
        for item in raw_matches
        if not isinstance(item, dict)
        or (str(item.get("source") or "").lower(), str(item.get("lotId") or "")) not in dismissed_keys
    ]
    removed_keys = {
        (str(item.get("source") or "").lower(), str(item.get("lotId") or ""))
        for item in raw_matches
        if isinstance(item, dict)
        and (str(item.get("source") or "").lower(), str(item.get("lotId") or "")) in dismissed_keys
    }
    filtered["matches"] = visible_matches

    featured = snapshot.get("featured")
    if isinstance(featured, dict):
        featured_key = (str(featured.get("source") or "").lower(), str(featured.get("lotId") or ""))
        if featured_key in dismissed_keys:
            filtered["featured"] = None
            removed_keys.add(featured_key)

    filtered["dismissalsApplied"] = len(removed_keys)
    raw_counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    counts = dict(raw_counts)
    source_counts: dict[str, int] = {}
    for item in visible_matches:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source") or "").strip().lower()
        if source_id:
            source_counts[source_id] = source_counts.get(source_id, 0) + 1
    detected_matches = int(counts.get("detected_matches") or counts.get("total_matches") or len(raw_matches))
    new_matches = int(
        counts.get("new_matches")
        or sum(
            isinstance(item, dict) and item.get("firstSeenInRun") is True
            for item in raw_matches
        )
    )
    counts.update(
        {
            "detected_matches": detected_matches,
            "dismissed_matches": max(0, detected_matches - len(visible_matches)),
            "total_matches": len(visible_matches),
            "visible_matches": len(visible_matches),
            "new_matches": new_matches,
            "bavastro_matches": source_counts.get("bavastro", 0),
            "castells_matches": source_counts.get("castells", 0),
            "extra_matches": sum(
                match_count
                for source_id, match_count in source_counts.items()
                if source_id not in {"bavastro", "castells"}
            ),
            "extra_matches_by_source": {
                source_id: match_count
                for source_id, match_count in source_counts.items()
                if source_id not in {"bavastro", "castells"}
            },
        }
    )
    filtered["counts"] = counts
    return filtered


def canonical_auction_watch_snapshot_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_auction_watch_timestamp(value: Any, field: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"Auction Watch {field} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"Auction Watch {field} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"Auction Watch {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def normalize_auction_watch_run_id(value: Any) -> str:
    run_id = str(value or "").strip()
    if not run_id or len(run_id) > 160 or any(ord(char) < 32 for char in run_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch runId is invalid")
    return run_id


def normalize_auction_watch_snapshot_hash(value: Any, *, required: bool = True) -> str:
    snapshot_hash = str(value or "").strip().lower()
    if not snapshot_hash and not required:
        return ""
    if len(snapshot_hash) != 64 or any(char not in "0123456789abcdef" for char in snapshot_hash):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch snapshot hash is invalid")
    return snapshot_hash


def auction_watch_receipt_path(config: AppConfig) -> Path:
    return config.auction_watch_dir / "export" / AUCTION_WATCH_RECEIPT_FILE


def record_auction_watch_publication_receipt(config: AppConfig, receipt: dict[str, Any]) -> None:
    run_id = normalize_auction_watch_run_id(receipt.get("runId"))
    snapshot_hash = normalize_auction_watch_snapshot_hash(receipt.get("snapshotHash"))
    generated_at = str(receipt.get("generatedAt") or "").strip()
    accepted_at = str(receipt.get("acceptedAt") or "").strip()
    if not generated_at or not accepted_at:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch receipt is incomplete")
    matches = max(0, int(receipt.get("matches") or 0))
    with connect_db(config) as conn:
        existing = conn.execute(
            "SELECT snapshot_hash FROM auction_watch_publications WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing is not None and str(existing["snapshot_hash"]).lower() != snapshot_hash:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch runId was already published with different content",
                {"runId": run_id, "currentSnapshotHash": existing["snapshot_hash"]},
            )
        conn.execute(
            """
            INSERT INTO auction_watch_publications
              (run_id, snapshot_hash, generated_at, accepted_at, matches, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              snapshot_hash = excluded.snapshot_hash,
              generated_at = excluded.generated_at,
              accepted_at = excluded.accepted_at,
              matches = excluded.matches,
              recorded_at = excluded.recorded_at
            """,
            (run_id, snapshot_hash, generated_at, accepted_at, matches, utc_now()),
        )


def auction_watch_publication_history(config: AppConfig, run_id: str) -> dict[str, Any] | None:
    with connect_db(config) as conn:
        row = conn.execute(
            """
            SELECT run_id, snapshot_hash, generated_at, accepted_at, matches, recorded_at
            FROM auction_watch_publications WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def auction_watch_publication_history_hash(config: AppConfig, run_id: str) -> str:
    history = auction_watch_publication_history(config, run_id)
    return str(history.get("snapshot_hash") or "").strip().lower() if history else ""


def current_auction_watch_publication(config: AppConfig) -> dict[str, Any] | None:
    """Return the verified server snapshot identity currently visible to clients."""
    with _AUCTION_WATCH_SNAPSHOT_LOCK:
        payload = read_json_object(config.auction_watch_dir / "export" / "auction-watch.json")
        receipt = read_json_object(auction_watch_receipt_path(config))
    if not isinstance(payload, dict) or not isinstance(receipt, dict):
        return None

    run_id = str(payload.get("runId") or "").strip()
    generated_at = str(payload.get("generatedAt") or "").strip()
    snapshot_hash = canonical_auction_watch_snapshot_hash(payload)
    receipt_valid = (
        bool(run_id)
        and bool(generated_at)
        and str(receipt.get("runId") or "").strip() == run_id
        and str(receipt.get("snapshotHash") or "").strip().lower() == snapshot_hash
        and str(receipt.get("generatedAt") or "").strip() == generated_at
        and bool(str(receipt.get("acceptedAt") or "").strip())
    )
    if not receipt_valid:
        return None
    try:
        generated_datetime = parse_auction_watch_timestamp(generated_at, "generatedAt")
        accepted_at = str(receipt.get("acceptedAt") or "").strip()
        accepted_datetime = parse_auction_watch_timestamp(accepted_at, "acceptedAt")
    except ApiError:
        return None
    return {
        "runId": run_id,
        "snapshotHash": snapshot_hash,
        "generatedAt": generated_at,
        "generatedDatetime": generated_datetime,
        "acceptedAt": accepted_at,
        "acceptedDatetime": accepted_datetime,
    }


def auction_watch_publication_state(
    config: AppConfig,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Classify a terminal published request against current and historical receipts."""
    snapshot_status = str(request.get("snapshotStatus") or "").strip().lower()
    if snapshot_status != "published":
        return {}

    run_id = str(request.get("runId") or "").strip()
    snapshot_hash = str(request.get("snapshotHash") or "").strip().lower()
    current = current_auction_watch_publication(config)
    if current and current["runId"] == run_id and current["snapshotHash"] == snapshot_hash:
        return {"publicationState": "current"}

    history = auction_watch_publication_history(config, run_id) if run_id else None
    historical_valid = False
    history_accepted: datetime | None = None
    if history and str(history.get("snapshot_hash") or "").strip().lower() == snapshot_hash:
        try:
            history_accepted = parse_auction_watch_timestamp(history.get("accepted_at"), "acceptedAt")
            historical_valid = True
        except ApiError:
            historical_valid = False

    if historical_valid and current:
        is_posterior = current["acceptedDatetime"] > history_accepted
        if is_posterior:
            return {
                "publicationState": "superseded",
                "supersededByRunId": current["runId"],
            }
    return {"publicationState": "missing"}


def auction_watch_snapshot_sync(
    config: AppConfig,
    payload: dict[str, Any],
    *,
    source: str,
    receipt: dict[str, Any] | None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    run_id = str(payload.get("runId") or "").strip()
    generated_at = str(payload.get("generatedAt") or "").strip() or None
    snapshot_hash = canonical_auction_watch_snapshot_hash(payload)
    accepted_at = None
    receipt_valid = False
    receipt_present = receipt is not None
    if isinstance(receipt, dict):
        receipt_valid = (
            str(receipt.get("runId") or "").strip() == run_id
            and str(receipt.get("snapshotHash") or "").strip().lower() == snapshot_hash
            and str(receipt.get("generatedAt") or "").strip() == str(generated_at or "")
            and bool(str(receipt.get("acceptedAt") or "").strip())
        )
        if receipt_valid:
            accepted_at = str(receipt.get("acceptedAt") or "").strip() or None

    age_seconds: int | None = None
    generated_datetime: datetime | None = None
    if generated_at:
        try:
            generated_datetime = parse_auction_watch_timestamp(generated_at, "generatedAt")
        except ApiError:
            generated_datetime = None
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    future_timestamp = False
    if generated_datetime is not None:
        future_timestamp = generated_datetime > now + timedelta(minutes=5)
        age_seconds = max(0, int((now - generated_datetime).total_seconds()))

    payload_status = str(payload.get("status") or "").strip().lower()
    if payload_status == "unavailable" or not run_id or generated_datetime is None or future_timestamp:
        sync_status = "unavailable"
    elif source == "server" and receipt_present and not receipt_valid:
        sync_status = "unavailable"
    elif source != "server" or not receipt_valid:
        sync_status = "stale"
    elif age_seconds is None or age_seconds > getattr(
        config,
        "auction_watch_stale_after_seconds",
        AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS,
    ):
        sync_status = "stale"
    else:
        sync_status = "current"

    return {
        "runId": run_id,
        "snapshotHash": snapshot_hash,
        "generatedAt": generated_at,
        "acceptedAt": accepted_at,
        "source": source,
        "ageSeconds": age_seconds,
        "status": sync_status,
    }


def read_auction_watch_snapshot(config: AppConfig) -> dict[str, Any]:
    export_path = config.auction_watch_dir / "export" / "auction-watch.json"
    candidates = [
        (export_path, "server"),
        (config.auction_watch_dir / "latest" / "auction-watch.json", "server-legacy"),
        (config.static_dir / "data" / "auction-watch.json", "static"),
    ]
    receipt_path = auction_watch_receipt_path(config)
    receipt = read_json_object(receipt_path)
    if receipt is None and receipt_path.exists():
        receipt = {}
    for path, source in candidates:
        payload = read_json_object(path)
        if payload is None:
            continue
        candidate_receipt = receipt if path == export_path else None
        sync = auction_watch_snapshot_sync(
            config,
            payload,
            source=source,
            receipt=candidate_receipt,
        )
        filtered = filter_auction_watch_snapshot(config, payload)
        filtered["snapshotHash"] = sync["snapshotHash"]
        filtered["sync"] = sync
        return filtered
    return {
        "generatedAt": None,
        "runId": "",
        "snapshotHash": "",
        "matches": [],
        "status": "unavailable",
        "sync": {
            "runId": "",
            "snapshotHash": "",
            "generatedAt": None,
            "acceptedAt": None,
            "source": "none",
            "ageSeconds": None,
            "status": "unavailable",
        },
    }


def _publish_auction_watch_snapshot_unlocked(
    config: AppConfig,
    payload: Any,
    *,
    expected_hash: str | None = None,
) -> dict[str, Any]:
    """Persist the runner's complete public snapshot before an alert is sent.

    This lives in /data instead of the add-on's static bundle, so the scheduled
    runner can refresh opportunities without rebuilding the add-on on every run.
    """
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch snapshot must be an object")
    run_id = normalize_auction_watch_run_id(payload.get("runId"))
    generated_at = str(payload.get("generatedAt") or "").strip()
    generated_datetime = parse_auction_watch_timestamp(generated_at, "generatedAt")
    if generated_datetime > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch generatedAt is too far in the future")
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch snapshot matches must be a list")
    if len(matches) > 10_000:
        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Auction Watch snapshot has too many matches")

    for item in matches:
        if not isinstance(item, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Auction Watch snapshot match must be an object")
        normalize_auction_watch_identity(item.get("source"), item.get("lotId"))
    lifecycle = normalize_auction_watch_lifecycle(payload)
    active_image_urls = normalize_active_match_metadata(payload)

    snapshot_hash = canonical_auction_watch_snapshot_hash(payload)
    if expected_hash is not None:
        normalized_expected_hash = normalize_auction_watch_snapshot_hash(expected_hash)
        if normalized_expected_hash != snapshot_hash:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch snapshot hash does not match the request body",
                {"runId": run_id, "computedSnapshotHash": snapshot_hash},
            )
    historical_hash = auction_watch_publication_history_hash(config, run_id)
    if historical_hash and historical_hash != snapshot_hash:
        raise ApiError(
            HTTPStatus.CONFLICT,
            "Auction Watch runId was already published with different content",
            {"runId": run_id, "currentSnapshotHash": historical_hash},
        )

    export_dir = config.auction_watch_dir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / "auction-watch.json"
    current_payload = read_json_object(target)
    current_is_export = current_payload is not None
    if current_payload is None:
        current_payload = read_json_object(config.auction_watch_dir / "latest" / "auction-watch.json")
    current_receipt = read_json_object(auction_watch_receipt_path(config)) if current_is_export else None
    if current_payload is not None:
        current_run_id = str(current_payload.get("runId") or "").strip()
        current_hash = canonical_auction_watch_snapshot_hash(current_payload)
        current_generated_at = str(current_payload.get("generatedAt") or "").strip()
        current_generated_datetime: datetime | None = None
        if current_generated_at:
            try:
                current_generated_datetime = parse_auction_watch_timestamp(
                    current_generated_at,
                    "generatedAt",
                )
            except ApiError:
                current_generated_datetime = None
        if (
            current_generated_datetime is not None
            and current_generated_datetime > datetime.now(timezone.utc) + timedelta(minutes=5)
        ):
            current_generated_datetime = None

        current_receipt_valid = (
            isinstance(current_receipt, dict)
            and str(current_receipt.get("runId") or "").strip() == current_run_id
            and str(current_receipt.get("snapshotHash") or "").strip().lower() == current_hash
            and str(current_receipt.get("generatedAt") or "").strip() == current_generated_at
            and bool(str(current_receipt.get("acceptedAt") or "").strip())
        )

        if current_run_id == run_id and current_hash == snapshot_hash and current_receipt_valid:
            receipt = {
                "runId": run_id,
                "snapshotHash": snapshot_hash,
                "generatedAt": generated_at,
                "acceptedAt": current_receipt.get("acceptedAt"),
                "matches": len(matches),
            }
            record_auction_watch_publication_receipt(config, receipt)
            # Publication is only fully committed once lifecycle reconciliation
            # has also succeeded. Re-running it on an identical publish closes
            # the crash window after the snapshot/receipt renames.
            cleanup = reconcile_auction_watch_dismissals(
                config,
                lifecycle,
                active_image_urls=active_image_urls,
            )
            return {
                "ok": True,
                "receipt": receipt,
                "generatedAt": generated_at,
                "matches": len(matches),
                "idempotent": True,
                "dismissalCleanup": cleanup,
            }
        if current_run_id == run_id and current_hash != snapshot_hash:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch runId was already published with different content",
                {"runId": run_id, "currentSnapshotHash": current_hash},
            )
        if current_generated_datetime is not None and generated_datetime < current_generated_datetime:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch refused an older snapshot",
                {"runId": run_id, "currentRunId": current_run_id, "currentGeneratedAt": current_generated_at},
            )
        if (
            current_generated_datetime is not None
            and generated_datetime == current_generated_datetime
            and current_hash != snapshot_hash
        ):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Auction Watch refused conflicting snapshots with the same generatedAt",
                {"runId": run_id, "currentRunId": current_run_id, "generatedAt": generated_at},
            )

    accepted_at = utc_now()
    receipt = {
        "runId": run_id,
        "snapshotHash": snapshot_hash,
        "generatedAt": generated_at,
        "acceptedAt": accepted_at,
        "matches": len(matches),
    }
    # The durable receipt history is the admission commit for a run. It is
    # recorded before the derived files so a later publication cannot erase the
    # identity needed by a delayed completion after a crash between renames.
    record_auction_watch_publication_receipt(config, receipt)
    # A mismatched sidecar makes GET unavailable. Writing it before the snapshot
    # therefore fails closed if the process stops between renames.
    write_json_object_atomic(auction_watch_receipt_path(config), receipt)
    write_json_object_atomic(target, payload)

    cleanup = reconcile_auction_watch_dismissals(config, lifecycle, active_image_urls=active_image_urls)

    return {
        "ok": True,
        "receipt": receipt,
        "generatedAt": generated_at,
        "matches": len(matches),
        "dismissalCleanup": cleanup,
    }


def publish_auction_watch_snapshot(
    config: AppConfig,
    payload: Any,
    *,
    expected_hash: str | None = None,
) -> dict[str, Any]:
    with _AUCTION_WATCH_SNAPSHOT_LOCK:
        return _publish_auction_watch_snapshot_unlocked(
            config,
            payload,
            expected_hash=expected_hash,
        )
