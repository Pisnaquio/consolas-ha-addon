"""HTTP request handler (routing) and threaded server."""

from __future__ import annotations

import json
import mimetypes
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from .auction_watch_dismissals import (
        dismiss_auction_watch_lot,
        list_auction_watch_dismissals,
        require_auction_watch_write_request,
        restore_auction_watch_lot,
    )
    from .auction_watch_following import (
        follow_auction_watch_lot,
        list_auction_watch_following,
        unfollow_auction_watch_lot,
    )
    from .auction_watch_runs import (
        claim_auction_watch_run,
        complete_auction_watch_run,
        enqueue_auction_watch_run,
        heartbeat_auction_watch_run,
        latest_auction_watch_run_request,
    )
    from .auction_watch_snapshot import publish_auction_watch_snapshot, read_auction_watch_snapshot
    from .chasing_games import (
        create_chasing_game,
        delete_chasing_game,
        list_chasing_games,
        require_chasing_games_write_request,
        run_chasing_game,
        set_chasing_game_enabled,
    )
    from .config import AppConfig
    from .constants import (
        AUCTION_WATCH_SNAPSHOT_HASH_HEADER,
        RADAR_FEED_LIMIT,
        RADAR_RUN_HISTORY_LIMIT,
    )
    from .errors import ApiError
    from .health import build_health_payload
    from .http_utils import read_json_body, safe_static_path
    from .media import save_media
    from .radar_criteria import normalize_radar_id, require_radar_write_request
    from .radar_decisions import clear_radar_decision, record_radar_decision
    from .radar_execution import (
        create_manual_radar_listing,
        preview_radar_search,
        run_radar_search,
        verify_radar_listing,
        verify_radar_listing_contents,
    )
    from .radar_feed import list_radar_decisions, list_radar_feed
    from .radar_master_searches import (
        radar_master_proposals,
        radar_wishlist_coverage,
        regenerate_radar_master,
    )
    from .radar_notifications import list_radar_outbox
    from .radar_preferences import get_radar_preferences, update_radar_preferences
    from .radar_purchases import (
        close_radar_shipment,
        compute_radar_budget,
        compute_radar_coverage,
        compute_radar_lot_valuation,
        compute_radar_shipment,
        delete_radar_purchase,
        list_radar_purchases,
        record_radar_purchase,
    )
    from .radar_results import list_radar_listings
    from .radar_scheduler import list_radar_runs, start_manual_radar_run
    from .radar_searches import (
        create_radar_search,
        delete_radar_search,
        duplicate_radar_search,
        list_radar_searches,
        radar_search_payload,
        set_radar_search_status,
        update_radar_search,
    )
    from .state import (
        build_state_export,
        read_state,
        restore_state,
        write_state,
    )
except ImportError:  # Direct execution: `python3 server/app.py`.
    from auction_watch_dismissals import (
        dismiss_auction_watch_lot,
        list_auction_watch_dismissals,
        require_auction_watch_write_request,
        restore_auction_watch_lot,
    )
    from auction_watch_following import (
        follow_auction_watch_lot,
        list_auction_watch_following,
        unfollow_auction_watch_lot,
    )
    from auction_watch_runs import (
        claim_auction_watch_run,
        complete_auction_watch_run,
        enqueue_auction_watch_run,
        heartbeat_auction_watch_run,
        latest_auction_watch_run_request,
    )
    from auction_watch_snapshot import publish_auction_watch_snapshot, read_auction_watch_snapshot
    from chasing_games import (
        create_chasing_game,
        delete_chasing_game,
        list_chasing_games,
        require_chasing_games_write_request,
        run_chasing_game,
        set_chasing_game_enabled,
    )
    from config import AppConfig
    from constants import AUCTION_WATCH_SNAPSHOT_HASH_HEADER, RADAR_FEED_LIMIT, RADAR_RUN_HISTORY_LIMIT
    from errors import ApiError
    from health import build_health_payload
    from http_utils import read_json_body, safe_static_path
    from media import save_media
    from radar_criteria import normalize_radar_id, require_radar_write_request
    from radar_decisions import clear_radar_decision, record_radar_decision
    from radar_execution import (
        create_manual_radar_listing,
        preview_radar_search,
        run_radar_search,
        verify_radar_listing,
        verify_radar_listing_contents,
    )
    from radar_feed import list_radar_decisions, list_radar_feed
    from radar_master_searches import (
        radar_master_proposals,
        radar_wishlist_coverage,
        regenerate_radar_master,
    )
    from radar_notifications import list_radar_outbox
    from radar_preferences import get_radar_preferences, update_radar_preferences
    from radar_purchases import (
        close_radar_shipment,
        compute_radar_budget,
        compute_radar_coverage,
        compute_radar_lot_valuation,
        compute_radar_shipment,
        delete_radar_purchase,
        list_radar_purchases,
        record_radar_purchase,
    )
    from radar_results import list_radar_listings
    from radar_scheduler import list_radar_runs, start_manual_radar_run
    from radar_searches import (
        create_radar_search,
        delete_radar_search,
        duplicate_radar_search,
        list_radar_searches,
        radar_search_payload,
        set_radar_search_status,
        update_radar_search,
    )
    from state import (
        build_state_export,
        read_state,
        restore_state,
        write_state,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "ConsolasServer/0.1"

    def send_json(self, payload: Any, status: int | HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, error: ApiError) -> None:
        payload = {"ok": False, "error": error.message}
        if error.details is not None:
            payload["details"] = error.details
        self.send_json(payload, error.status)

    def config(self) -> AppConfig:
        return self.server.config  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        try:
            self.route_get()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def do_HEAD(self) -> None:
        try:
            path = self.path.split("?", 1)[0]
            if path.startswith("/api/"):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            target = safe_static_path(self.config().static_dir, path)
            if not target.exists() or not target.is_file():
                target = self.config().static_dir / "index.html"
            mime_type, _ = mimetypes.guess_type(str(target))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type or "application/octet-stream")
            self.send_header("Content-Length", str(target.stat().st_size))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def do_POST(self) -> None:
        try:
            self.route_post()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def do_PUT(self) -> None:
        try:
            self.route_put()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def do_DELETE(self) -> None:
        try:
            self.route_delete()
        except ApiError as error:
            self.send_error_json(error)
        except Exception as error:
            self.send_error_json(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)))

    def route_get(self) -> None:
        config = self.config()
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self.send_json(build_health_payload(config))
            return
        if path in {"/api/readiness", "/api/ready"}:
            health = build_health_payload(config)
            self.send_json(
                health,
                HTTPStatus.OK if health["ready"] else HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        if path == "/api/state":
            self.send_json(read_state(config))
            return
        if path == "/api/state/export":
            self.send_json(build_state_export(config))
            return
        if path == "/api/auction-watch":
            self.send_json(read_auction_watch_snapshot(config))
            return
        if path == "/api/auction-watch/dismissals":
            self.send_json(list_auction_watch_dismissals(config))
            return
        if path == "/api/auction-watch/following":
            self.send_json(list_auction_watch_following(config))
            return
        if path == "/api/auction-watch/run-now":
            self.send_json(latest_auction_watch_run_request(config))
            return
        if path == "/api/chasing-games":
            self.send_json(list_chasing_games(config))
            return
        if path == "/api/radar/searches":
            self.send_json(list_radar_searches(config))
            return
        if path == "/api/radar/feed":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            limit = (query.get("limit") or [str(RADAR_FEED_LIMIT)])[0]
            self.send_json(list_radar_feed(config, int(limit) if limit.isdigit() else RADAR_FEED_LIMIT))
            return
        if path == "/api/radar/decisions":
            self.send_json(list_radar_decisions(config))
            return
        if path == "/api/radar/outbox":
            self.send_json(list_radar_outbox(config))
            return
        if path == "/api/radar/master":
            self.send_json({"ok": True, "proposals": radar_master_proposals(config)})
            return
        if path == "/api/radar/wishlist-coverage":
            self.send_json({"ok": True, **radar_wishlist_coverage(config)})
            return
        if path == "/api/radar/runs":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            limit = (query.get("limit") or [str(RADAR_RUN_HISTORY_LIMIT)])[0]
            self.send_json(
                list_radar_runs(config, int(limit) if limit.isdigit() else RADAR_RUN_HISTORY_LIMIT)
            )
            return
        if path == "/api/radar/listings":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            limit = (query.get("limit") or ["100"])[0]
            self.send_json(list_radar_listings(config, int(limit) if limit.isdigit() else 100))
            return
        if path == "/api/radar/preferences":
            self.send_json(get_radar_preferences(config))
            return
        if path == "/api/radar/budget":
            self.send_json(compute_radar_budget(config))
            return
        if path == "/api/radar/shipment":
            self.send_json(compute_radar_shipment(config))
            return
        if path == "/api/radar/purchases":
            self.send_json(list_radar_purchases(config))
            return
        if path == "/api/radar/coverage":
            self.send_json(compute_radar_coverage(config))
            return
        if path.startswith("/api/radar/searches/"):
            search_id = normalize_radar_id(path.removeprefix("/api/radar/searches/"))
            self.send_json(radar_search_payload(config, search_id))
            return
        if path.startswith("/media/"):
            self.serve_media(path.removeprefix("/media/"))
            return
        if path == "/api" or path.startswith("/api/"):
            # Sin esto una ruta de API inexistente caía al fallback estático y
            # devolvía index.html con 200: el cliente recibía HTML donde
            # esperaba JSON y no podía distinguirlo de un endpoint válido.
            raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        self.serve_static(path)

    def route_put(self) -> None:
        if self.path.split("?", 1)[0] != "/api/state":
            raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        payload = read_json_body(self, self.config())
        self.send_json(write_state(self.config(), payload))

    def route_post(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            payload = read_json_body(self, self.config())
            self.send_json(write_state(self.config(), payload))
            return
        if path == "/api/state/restore":
            payload = read_json_body(self, self.config())
            self.send_json(restore_state(self.config(), payload))
            return
        if path == "/api/media":
            payload = read_json_body(self, self.config())
            self.send_json(save_media(self.config(), payload), HTTPStatus.CREATED)
            return
        if path == "/api/auction-watch/dismissals":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(
                dismiss_auction_watch_lot(self.config(), payload),
                HTTPStatus.CREATED,
            )
            return
        if path == "/api/auction-watch/following":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(follow_auction_watch_lot(self.config(), payload), HTTPStatus.CREATED)
            return
        if path == "/api/auction-watch/snapshot":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            expected_hash = self.headers.get(AUCTION_WATCH_SNAPSHOT_HASH_HEADER)
            if not expected_hash:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    f"{AUCTION_WATCH_SNAPSHOT_HASH_HEADER} header is required",
                )
            self.send_json(
                publish_auction_watch_snapshot(
                    self.config(),
                    payload,
                    expected_hash=expected_hash,
                )
            )
            return
        if path == "/api/auction-watch/run-now":
            require_auction_watch_write_request(self)
            self.send_json(enqueue_auction_watch_run(self.config()), HTTPStatus.ACCEPTED)
            return
        if path == "/api/auction-watch/run-now/claim":
            require_auction_watch_write_request(self)
            self.send_json(claim_auction_watch_run(self.config()))
            return
        if path == "/api/auction-watch/run-now/heartbeat":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(heartbeat_auction_watch_run(self.config(), payload))
            return
        if path == "/api/auction-watch/run-now/complete":
            require_auction_watch_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(complete_auction_watch_run(self.config(), payload))
            return
        if path == "/api/radar/searches":
            require_radar_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(create_radar_search(self.config(), payload), HTTPStatus.CREATED)
            return
        if path == "/api/radar/decisions":
            require_radar_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(record_radar_decision(self.config(), payload), HTTPStatus.CREATED)
            return
        if path == "/api/radar/preferences":
            require_radar_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(update_radar_preferences(self.config(), payload))
            return
        if path == "/api/radar/purchases":
            require_radar_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(record_radar_purchase(self.config(), payload), HTTPStatus.CREATED)
            return
        if path == "/api/radar/shipment/close":
            require_radar_write_request(self)
            read_json_body(self, self.config())
            self.send_json(close_radar_shipment(self.config()))
            return
        if path == "/api/radar/lot-valuation":
            require_radar_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(compute_radar_lot_valuation(payload))
            return
        if path == "/api/radar/manual-listings":
            require_radar_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(create_manual_radar_listing(self.config(), payload), HTTPStatus.CREATED)
            return
        if path.startswith("/api/radar/listings/") and path.endswith("/verify-contents"):
            require_radar_write_request(self)
            listing_id = normalize_radar_id(
                path.removeprefix("/api/radar/listings/").removesuffix("/verify-contents")
            )
            self.send_json(verify_radar_listing_contents(self.config(), listing_id))
            return
        if path.startswith("/api/radar/listings/") and path.endswith("/verify"):
            require_radar_write_request(self)
            listing_id = normalize_radar_id(path.removeprefix("/api/radar/listings/").removesuffix("/verify"))
            self.send_json(verify_radar_listing(self.config(), listing_id))
            return
        if path == "/api/radar/master/regenerate":
            require_radar_write_request(self)
            self.send_json(regenerate_radar_master(self.config()), HTTPStatus.CREATED)
            return
        if path == "/api/radar/run-now":
            require_radar_write_request(self)
            self.send_json(start_manual_radar_run(self.config()), HTTPStatus.ACCEPTED)
            return
        if path.startswith("/api/radar/searches/"):
            require_radar_write_request(self)
            payload = read_json_body(self, self.config())
            parts = path.removeprefix("/api/radar/searches/").split("/")
            if len(parts) == 1:
                self.send_json(update_radar_search(self.config(), parts[0], payload))
                return
            if len(parts) != 2:
                raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            search_id, action = parts
            if action == "run":
                self.send_json(run_radar_search(self.config(), search_id))
                return
            if action == "preview":
                self.send_json(preview_radar_search(self.config(), search_id))
                return
            if action == "status":
                self.send_json(set_radar_search_status(self.config(), search_id, payload.get("status")))
                return
            if action == "duplicate":
                self.send_json(duplicate_radar_search(self.config(), search_id), HTTPStatus.CREATED)
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        if path == "/api/chasing-games":
            require_chasing_games_write_request(self)
            payload = read_json_body(self, self.config())
            self.send_json(create_chasing_game(self.config(), payload), HTTPStatus.CREATED)
            return
        if path.startswith("/api/chasing-games/"):
            require_chasing_games_write_request(self)
            payload = read_json_body(self, self.config())
            parts = path.removeprefix("/api/chasing-games/").split("/")
            if len(parts) != 2:
                raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            chase_id, action = parts
            if action == "run":
                self.send_json(run_chasing_game(self.config(), chase_id))
                return
            if action == "enabled":
                self.send_json(set_chasing_game_enabled(self.config(), chase_id, payload.get("enabled") is True))
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def route_delete(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/radar/purchases/"):
            require_radar_write_request(self)
            purchase_id = parsed.path.removeprefix("/api/radar/purchases/")
            self.send_json(delete_radar_purchase(self.config(), purchase_id))
            return
        if parsed.path.startswith("/api/radar/decisions/"):
            require_radar_write_request(self)
            listing_id = parsed.path.removeprefix("/api/radar/decisions/")
            self.send_json(clear_radar_decision(self.config(), listing_id))
            return
        if parsed.path.startswith("/api/radar/searches/"):
            require_radar_write_request(self)
            search_id = normalize_radar_id(parsed.path.removeprefix("/api/radar/searches/"))
            self.send_json(delete_radar_search(self.config(), search_id))
            return
        if parsed.path.startswith("/api/chasing-games/"):
            require_chasing_games_write_request(self)
            chase_id = parsed.path.removeprefix("/api/chasing-games/")
            self.send_json(delete_chasing_game(self.config(), chase_id))
            return
        if parsed.path != "/api/auction-watch/dismissals":
            if parsed.path != "/api/auction-watch/following":
                raise ApiError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        require_auction_watch_write_request(self)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        source_id = (query.get("sourceId") or [""])[0]
        lot_id = (query.get("lotId") or [""])[0]
        if parsed.path == "/api/auction-watch/following":
            self.send_json(unfollow_auction_watch_lot(self.config(), source_id, lot_id))
            return
        self.send_json(restore_auction_watch_lot(self.config(), source_id, lot_id))

    def serve_static(self, path: str) -> None:
        target = safe_static_path(self.config().static_dir, path)
        if not target.exists() or not target.is_file():
            target = self.config().static_dir / "index.html"
        self.send_file(target, deny_frame=target.name == "auction-watch-action.html")

    def serve_media(self, file_name: str) -> None:
        safe_name = Path(file_name).name
        target = (self.config().media_dir / safe_name).resolve()
        if not target.exists() or not target.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "Media not found")
        self.send_file(target, cache_control="public, max-age=31536000, immutable")

    def send_file(
        self,
        path: Path,
        cache_control: str = "no-cache",
        *,
        deny_frame: bool = False,
    ) -> None:
        data = path.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        if deny_frame:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[consolas] {self.address_string()} - {fmt % args}")


class Server(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler: type[Handler], config: AppConfig) -> None:
        self.config = config
        super().__init__(address, handler)
