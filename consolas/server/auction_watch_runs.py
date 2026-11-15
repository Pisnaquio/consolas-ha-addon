"""Auction Watch: queued run requests, claims, heartbeats and completion."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

try:
    from .auction_watch_snapshot import (
        auction_watch_publication_history_hash,
        auction_watch_publication_state,
        auction_watch_receipt_path,
        canonical_auction_watch_snapshot_hash,
        normalize_auction_watch_run_id,
        normalize_auction_watch_snapshot_hash,
        record_auction_watch_publication_receipt,
    )
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        AUCTION_WATCH_EMAIL_STATUSES,
        AUCTION_WATCH_OVERALL_STATUSES,
        AUCTION_WATCH_PENDING_TIMEOUT,
        AUCTION_WATCH_RUNNING_TIMEOUT,
        AUCTION_WATCH_SNAPSHOT_STATUSES,
        _AUCTION_WATCH_RUN_LOCK,
        _AUCTION_WATCH_SNAPSHOT_LOCK,
    )
    from .database import connect_db
    from .errors import ApiError
    from .json_files import read_json_object
except ImportError:  # Direct execution: `python3 server/app.py`.
    from auction_watch_snapshot import (
        auction_watch_publication_history_hash,
        auction_watch_publication_state,
        auction_watch_receipt_path,
        canonical_auction_watch_snapshot_hash,
        normalize_auction_watch_run_id,
        normalize_auction_watch_snapshot_hash,
        record_auction_watch_publication_receipt,
    )
    from clock import utc_now
    from config import AppConfig
    from constants import (
        AUCTION_WATCH_EMAIL_STATUSES,
        AUCTION_WATCH_OVERALL_STATUSES,
        AUCTION_WATCH_PENDING_TIMEOUT,
        AUCTION_WATCH_RUNNING_TIMEOUT,
        AUCTION_WATCH_SNAPSHOT_STATUSES,
        _AUCTION_WATCH_RUN_LOCK,
        _AUCTION_WATCH_SNAPSHOT_LOCK,
    )
    from database import connect_db
    from errors import ApiError
    from json_files import read_json_object


def auction_watch_run_request_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "requestedAt": row["requested_at"],
        "startedAt": row["started_at"],
        "heartbeatAt": row["heartbeat_at"],
        "finishedAt": row["finished_at"],
        "detail": row["detail"],
        "runId": row["run_id"],
        "snapshotHash": row["snapshot_hash"],
        "snapshotStatus": row["snapshot_status"],
        "emailStatus": row["email_status"],
        "overallStatus": row["overall_status"],
    }


def reconcile_stale_auction_watch_run_requests(
    conn: sqlite3.Connection,
    *,
    observed_at: datetime | None = None,
) -> dict[str, int]:
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    finished_at = now.isoformat().replace("+00:00", "Z")
    pending_before = (now - AUCTION_WATCH_PENDING_TIMEOUT).isoformat().replace("+00:00", "Z")
    running_before = (now - AUCTION_WATCH_RUNNING_TIMEOUT).isoformat().replace("+00:00", "Z")
    pending = conn.execute(
        """
        UPDATE auction_watch_run_requests
        SET status = 'failed', finished_at = ?,
            detail = 'La solicitud venció porque el buscador no estaba disponible.',
            snapshot_status = CASE WHEN snapshot_status = '' THEN 'failed' ELSE snapshot_status END,
            email_status = CASE WHEN email_status = '' THEN 'failed' ELSE email_status END,
            overall_status = 'failed'
        WHERE status = 'pending' AND requested_at < ?
        """,
        (finished_at, pending_before),
    ).rowcount
    running = conn.execute(
        """
        UPDATE auction_watch_run_requests
        SET status = 'failed', finished_at = ?, detail = 'La corrida anterior quedó interrumpida.',
            snapshot_status = CASE WHEN snapshot_status = '' THEN 'failed' ELSE snapshot_status END,
            email_status = CASE WHEN email_status = '' THEN 'uncertain' ELSE email_status END,
            overall_status = 'failed'
        WHERE status = 'running' AND COALESCE(heartbeat_at, started_at) IS NOT NULL
          AND COALESCE(heartbeat_at, started_at) < ?
        """,
        (finished_at, running_before),
    ).rowcount
    return {"pending": pending, "running": running}


def latest_auction_watch_run_request(config: AppConfig) -> dict[str, Any]:
    # Reconcile requests even when the scheduler disappears before claiming
    # them so the web UI cannot remain stuck on a disabled button forever.
    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        reconcile_stale_auction_watch_run_requests(conn)
        row = conn.execute(
            "SELECT * FROM auction_watch_run_requests ORDER BY requested_at DESC LIMIT 1"
        ).fetchone()
    request = auction_watch_run_request_row(row)
    if request is not None:
        request.update(auction_watch_publication_state(config, request))
    return {"ok": True, "request": request}


def enqueue_auction_watch_run(config: AppConfig) -> dict[str, Any]:
    now = utc_now()
    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        reconcile_stale_auction_watch_run_requests(conn)
        existing = conn.execute(
            """
            SELECT * FROM auction_watch_run_requests
            WHERE status IN ('pending', 'running', 'delivery_pending')
            ORDER BY requested_at LIMIT 1
            """
        ).fetchone()
        if existing is not None:
            return {"ok": True, "queued": False, "request": auction_watch_run_request_row(existing)}
        request_id = f"run_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO auction_watch_run_requests (id, status, requested_at) VALUES (?, 'pending', ?)",
            (request_id, now),
        )
        row = conn.execute("SELECT * FROM auction_watch_run_requests WHERE id = ?", (request_id,)).fetchone()
    return {"ok": True, "queued": True, "request": auction_watch_run_request_row(row)}


def claim_auction_watch_run(config: AppConfig) -> dict[str, Any]:
    now = utc_now()
    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        reconcile_stale_auction_watch_run_requests(conn)
        running = conn.execute(
            "SELECT * FROM auction_watch_run_requests WHERE status = 'running' ORDER BY requested_at LIMIT 1"
        ).fetchone()
        if running is not None:
            # The scheduler must not fall through to an automatic scan while a
            # manually requested run owns the lease but has not yet produced an
            # outbox record (for example, after a run-lock collision/crash).
            return {
                "ok": True,
                "request": None,
                "running": auction_watch_run_request_row(running),
            }
        pending = conn.execute(
            "SELECT * FROM auction_watch_run_requests WHERE status = 'pending' ORDER BY requested_at LIMIT 1"
        ).fetchone()
        if pending is None:
            return {"ok": True, "request": None}
        updated = conn.execute(
            """
            UPDATE auction_watch_run_requests
            SET status = 'running', started_at = ?, heartbeat_at = ?, detail = '',
                run_id = '', snapshot_hash = '', snapshot_status = '',
                email_status = '', overall_status = ''
            WHERE id = ? AND status = 'pending'
            """,
            (now, now, pending["id"]),
        ).rowcount
        if updated != 1:
            return {"ok": True, "request": None}
        row = conn.execute("SELECT * FROM auction_watch_run_requests WHERE id = ?", (pending["id"],)).fetchone()
    return {"ok": True, "request": auction_watch_run_request_row(row)}


def heartbeat_auction_watch_run(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Run heartbeat must be an object")
    request_id = str(payload.get("id") or "").strip()
    if not request_id.startswith("run_") or len(request_id) > 80:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Valid run request id is required")
    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute(
            """
            UPDATE auction_watch_run_requests
            SET heartbeat_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (utc_now(), request_id),
        ).rowcount
        row = conn.execute(
            "SELECT * FROM auction_watch_run_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
    if row is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "Run request not found")
    if updated != 1:
        raise ApiError(
            HTTPStatus.CONFLICT,
            "Run request is no longer running",
            {"id": request_id, "status": row["status"]},
        )
    return {"ok": True, "request": auction_watch_run_request_row(row)}


def auction_watch_publication_matches_receipt(
    config: AppConfig,
    run_id: str,
    snapshot_hash: str,
) -> bool:
    historical_hash = auction_watch_publication_history_hash(config, run_id)
    if historical_hash:
        return historical_hash == snapshot_hash
    with _AUCTION_WATCH_SNAPSHOT_LOCK:
        published_payload = read_json_object(
            config.auction_watch_dir / "export" / "auction-watch.json"
        )
        published_receipt = read_json_object(auction_watch_receipt_path(config))
        published_generated_at = (
            str(published_payload.get("generatedAt") or "").strip()
            if isinstance(published_payload, dict)
            else ""
        )
        matches = (
            isinstance(published_payload, dict)
            and isinstance(published_receipt, dict)
            and str(published_payload.get("runId") or "").strip() == run_id
            and canonical_auction_watch_snapshot_hash(published_payload) == snapshot_hash
            and str(published_receipt.get("runId") or "").strip() == run_id
            and str(published_receipt.get("snapshotHash") or "").strip().lower() == snapshot_hash
            and str(published_receipt.get("generatedAt") or "").strip() == published_generated_at
            and bool(str(published_receipt.get("acceptedAt") or "").strip())
        )
        if matches:
            record_auction_watch_publication_receipt(config, published_receipt)
        return matches


def complete_auction_watch_run(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Run completion must be an object")
    request_id = str(payload.get("id") or "").strip()
    if not request_id.startswith("run_") or len(request_id) > 80:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Valid run request id is required")
    success = payload.get("success") is True
    detail = str(payload.get("detail") or "").strip()[:1000]
    run_id_raw = str(payload.get("runId") or "").strip()
    run_id = normalize_auction_watch_run_id(run_id_raw) if run_id_raw else ""
    snapshot_hash = normalize_auction_watch_snapshot_hash(
        payload.get("snapshotHash"),
        required=False,
    )
    snapshot_status = str(payload.get("snapshotStatus") or "").strip().lower()
    email_status = str(payload.get("emailStatus") or "").strip().lower()
    overall_status = str(payload.get("overallStatus") or ("completed" if success else "failed")).strip().lower()
    if snapshot_status and snapshot_status not in AUCTION_WATCH_SNAPSHOT_STATUSES:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Auction Watch snapshotStatus")
    if email_status and email_status not in AUCTION_WATCH_EMAIL_STATUSES:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Auction Watch emailStatus")
    if overall_status not in AUCTION_WATCH_OVERALL_STATUSES:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Auction Watch overallStatus")
    if snapshot_status == "published" and (not run_id or not snapshot_hash):
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "Published Auction Watch runs require runId and snapshotHash",
        )
    status = (
        "delivery_pending"
        if overall_status == "delivery_pending"
        else ("completed" if success else "failed")
    )

    with _AUCTION_WATCH_RUN_LOCK, connect_db(config) as conn:
        before = conn.execute(
            "SELECT * FROM auction_watch_run_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if before is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Run request not found")
        # Validate the publication only for the first transition out of
        # `running`. Later idempotent callbacks or delivery completion retries
        # rely on the identity already stored in this row, even if a newer
        # scheduled snapshot has since become current.
        publication_needs_validation = (
            snapshot_status == "published"
            and (
                str(before["status"]) == "running"
                or str(before["snapshot_status"]) != "published"
                or str(before["run_id"]) != run_id
                or str(before["snapshot_hash"]) != snapshot_hash
            )
        )
        if publication_needs_validation and not auction_watch_publication_matches_receipt(
            config,
            run_id,
            snapshot_hash,
        ):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Published Auction Watch run does not match the server receipt",
                {"runId": run_id, "snapshotHash": snapshot_hash},
            )
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE auction_watch_run_requests
            SET status = ?, finished_at = ?, detail = ?, run_id = ?,
                snapshot_hash = ?, snapshot_status = ?, email_status = ?, overall_status = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                status,
                utc_now(),
                detail,
                run_id,
                snapshot_hash,
                snapshot_status,
                email_status,
                overall_status,
                request_id,
            ),
        )
        if cursor.rowcount != 1:
            existing = conn.execute(
                "SELECT * FROM auction_watch_run_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if existing is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "Run request not found")
            delivery_progress = (
                str(existing["overall_status"]) == "delivery_pending"
                and overall_status == "delivery_pending"
                and str(existing["run_id"]) == run_id
                and str(existing["snapshot_hash"] or "") in {"", snapshot_hash}
            )
            if delivery_progress:
                progressed = conn.execute(
                    """
                    UPDATE auction_watch_run_requests
                    SET status = 'delivery_pending', finished_at = ?, detail = ?,
                        snapshot_hash = ?, snapshot_status = ?, email_status = ?,
                        overall_status = 'delivery_pending'
                    WHERE id = ? AND overall_status = 'delivery_pending' AND run_id = ?
                    """,
                    (
                        utc_now(),
                        detail,
                        snapshot_hash,
                        snapshot_status,
                        email_status,
                        request_id,
                        run_id,
                    ),
                )
                if progressed.rowcount == 1:
                    progressed_row = conn.execute(
                        "SELECT * FROM auction_watch_run_requests WHERE id = ?",
                        (request_id,),
                    ).fetchone()
                    return {
                        "ok": True,
                        "request": auction_watch_run_request_row(progressed_row),
                        "deliveryProgress": True,
                    }
            delivery_transition = (
                str(existing["overall_status"]) == "delivery_pending"
                and overall_status in {"completed", "degraded", "failed"}
                and str(existing["run_id"]) == run_id
                and str(existing["snapshot_hash"] or "") in {"", snapshot_hash}
            )
            if delivery_transition:
                transitioned = conn.execute(
                    """
                    UPDATE auction_watch_run_requests
                    SET status = ?, finished_at = ?, detail = ?, snapshot_hash = ?,
                        snapshot_status = ?, email_status = ?, overall_status = ?
                    WHERE id = ? AND overall_status = 'delivery_pending' AND run_id = ?
                    """,
                    (
                        status,
                        utc_now(),
                        detail,
                        snapshot_hash,
                        snapshot_status,
                        email_status,
                        overall_status,
                        request_id,
                        run_id,
                    ),
                )
                if transitioned.rowcount == 1:
                    transitioned_row = conn.execute(
                        "SELECT * FROM auction_watch_run_requests WHERE id = ?",
                        (request_id,),
                    ).fetchone()
                    return {
                        "ok": True,
                        "request": auction_watch_run_request_row(transitioned_row),
                        "deliveryTransition": True,
                    }
            same_result = (
                str(existing["status"]) == status
                and str(existing["detail"]) == detail
                and str(existing["run_id"]) == run_id
                and str(existing["snapshot_hash"]) == snapshot_hash
                and str(existing["snapshot_status"]) == snapshot_status
                and str(existing["email_status"]) == email_status
                and str(existing["overall_status"]) == overall_status
            )
            if same_result and str(existing["status"]) in {"completed", "failed", "delivery_pending"}:
                return {
                    "ok": True,
                    "request": auction_watch_run_request_row(existing),
                    "idempotent": True,
                }
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Run request is no longer running or completion conflicts with its stored result",
                {"id": request_id, "status": existing["status"]},
            )
        row = conn.execute("SELECT * FROM auction_watch_run_requests WHERE id = ?", (request_id,)).fetchone()
    return {"ok": True, "request": auction_watch_run_request_row(row)}
