#!/usr/bin/env python3
"""Consolas backend for the Home Assistant add-on.

The server uses only Python's standard library so the add-on stays small and
portable inside Home Assistant. It intentionally starts with the same
normalized state contract used by the browser DataStore, then persists it in
SQLite on the HA server.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

# El add-on arranca `python3 /app/server/app.py` y los tests importan `server.app`.
# Poner este directorio en el path deja un único nombre `radar.*` en los dos modos,
# igual que hace `agents/auction-watch/scripts/run_watch.py` con su propio paquete.
_SERVER_DIR = Path(__file__).resolve().parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

# This module is the entry point (`python3 server/app.py`) and wiring only. The
# code lives in the sibling modules; the names below are re-exported because
# tests and tooling reach them as `server.app.<name>`.
try:
    from . import (
        auction_watch_dismissals, auction_watch_following, auction_watch_runs,
        auction_watch_snapshot, chasing_games, clock, config, constants, database, errors, health,
        http_handler, http_utils, json_files, media, media_migration, radar_catalog, radar_criteria,
        radar_decisions, radar_execution, radar_feed, radar_listings, radar_master_searches,
        radar_migrations, radar_notifications, radar_preferences, radar_purchases, radar_references,
        radar_results, radar_runs, radar_scheduler, radar_scoring, radar_searches, schema, state,
    )
    from .auction_watch_dismissals import (
        dismiss_auction_watch_lot, list_auction_watch_dismissals, normalize_active_match_metadata,
        normalize_public_http_url, reconcile_auction_watch_dismissals,
        require_auction_watch_write_request, restore_auction_watch_lot,
    )
    from .auction_watch_following import (
        follow_auction_watch_lot, list_auction_watch_following, unfollow_auction_watch_lot,
    )
    from .auction_watch_runs import (
        claim_auction_watch_run, complete_auction_watch_run, enqueue_auction_watch_run,
        heartbeat_auction_watch_run, latest_auction_watch_run_request,
    )
    from .auction_watch_snapshot import (
        canonical_auction_watch_snapshot_hash, publish_auction_watch_snapshot,
        read_auction_watch_snapshot,
    )
    from .chasing_games import (
        create_chasing_game, list_chasing_games, run_chasing_game, set_chasing_game_enabled,
    )
    from .config import AppConfig
    from .constants import RADAR_SLOT_KEYS
    from .database import connect_db
    from .errors import ApiError
    from .health import build_health_payload
    from .http_handler import Handler, Server
    from .http_utils import read_json_body
    from .radar_criteria import radar_search_queries
    from .radar_decisions import clear_radar_decision, record_radar_decision
    from .radar_execution import (
        create_manual_radar_listing, preview_radar_search, run_radar_search, verify_radar_listing,
        verify_radar_listing_contents,
    )
    from .radar_feed import list_radar_decisions, list_radar_feed
    from .radar_master_searches import radar_master_proposals, regenerate_radar_master
    from .radar_notifications import (
        deserves_notification, enqueue_radar_delivery, flush_radar_outbox, is_exceptional,
        list_radar_outbox, notify_radar_opportunities, radar_email_account, radar_email_configured,
        send_radar_email,
    )
    from .radar_preferences import (
        RADAR_DEFAULT_DESTINATION_COUNTRY, RADAR_DEFAULT_DESTINATION_ZIP, RADAR_DEFAULT_MAX_ACTIVE,
        RADAR_MAX_PENDING_DRAFTS, get_radar_preferences, radar_destination,
        update_radar_preferences,
    )
    from .radar_purchases import (
        DUTY_FREE_MERCHANDISE_USD, close_radar_shipment, compute_radar_budget,
        compute_radar_coverage, compute_radar_lot_valuation, compute_radar_shipment,
        delete_radar_purchase, list_radar_purchases, radar_targeted_entities, record_radar_purchase,
    )
    from .radar_references import (
        radar_bundle_game_value, radar_console_shipping_weight, radar_entity_references,
        radar_expected_item_kind, target_applies_to, usable_radar_references,
    )
    from .radar_results import diversify_radar_results, list_radar_listings
    from .radar_runs import radar_due_slots, radar_next_slot, radar_now_local, radar_query_plan
    from .radar_scheduler import (
        RadarSearchScheduler, list_radar_runs, purge_expired_radar_content,
        run_active_radar_searches, run_due_radar_slots, start_manual_radar_run,
    )
    from .radar_scoring import valuate_radar_match
    from .radar_searches import (
        create_radar_search, delete_radar_search, duplicate_radar_search, list_radar_searches,
        radar_search_payload, set_radar_search_status, update_radar_search,
    )
    from .schema import init_db
    from .state import ensure_state_media_migrated, read_state, write_state
except ImportError:  # Direct execution: `python3 server/app.py`.
    import auction_watch_dismissals, auction_watch_following, auction_watch_runs
    import auction_watch_snapshot, chasing_games, clock, config, constants, database, errors, health
    import http_handler, http_utils, json_files, media, media_migration, radar_catalog
    import radar_criteria, radar_decisions, radar_execution, radar_feed, radar_listings
    import radar_master_searches, radar_migrations, radar_notifications, radar_preferences
    import radar_purchases, radar_references, radar_results, radar_runs, radar_scheduler
    import radar_scoring, radar_searches, schema, state
    from auction_watch_dismissals import (
        dismiss_auction_watch_lot, list_auction_watch_dismissals, normalize_active_match_metadata,
        normalize_public_http_url, reconcile_auction_watch_dismissals,
        require_auction_watch_write_request, restore_auction_watch_lot,
    )
    from auction_watch_following import (
        follow_auction_watch_lot, list_auction_watch_following, unfollow_auction_watch_lot,
    )
    from auction_watch_runs import (
        claim_auction_watch_run, complete_auction_watch_run, enqueue_auction_watch_run,
        heartbeat_auction_watch_run, latest_auction_watch_run_request,
    )
    from auction_watch_snapshot import (
        canonical_auction_watch_snapshot_hash, publish_auction_watch_snapshot,
        read_auction_watch_snapshot,
    )
    from chasing_games import (
        create_chasing_game, list_chasing_games, run_chasing_game, set_chasing_game_enabled,
    )
    from config import AppConfig
    from constants import RADAR_SLOT_KEYS
    from database import connect_db
    from errors import ApiError
    from health import build_health_payload
    from http_handler import Handler, Server
    from http_utils import read_json_body
    from radar_criteria import radar_search_queries
    from radar_decisions import clear_radar_decision, record_radar_decision
    from radar_execution import (
        create_manual_radar_listing, preview_radar_search, run_radar_search, verify_radar_listing,
        verify_radar_listing_contents,
    )
    from radar_feed import list_radar_decisions, list_radar_feed
    from radar_master_searches import radar_master_proposals, regenerate_radar_master
    from radar_notifications import (
        deserves_notification, enqueue_radar_delivery, flush_radar_outbox, is_exceptional,
        list_radar_outbox, notify_radar_opportunities, radar_email_account, radar_email_configured,
        send_radar_email,
    )
    from radar_preferences import (
        RADAR_DEFAULT_DESTINATION_COUNTRY, RADAR_DEFAULT_DESTINATION_ZIP, RADAR_DEFAULT_MAX_ACTIVE,
        RADAR_MAX_PENDING_DRAFTS, get_radar_preferences, radar_destination,
        update_radar_preferences,
    )
    from radar_purchases import (
        DUTY_FREE_MERCHANDISE_USD, close_radar_shipment, compute_radar_budget,
        compute_radar_coverage, compute_radar_lot_valuation, compute_radar_shipment,
        delete_radar_purchase, list_radar_purchases, radar_targeted_entities, record_radar_purchase,
    )
    from radar_references import (
        radar_bundle_game_value, radar_console_shipping_weight, radar_entity_references,
        radar_expected_item_kind, target_applies_to, usable_radar_references,
    )
    from radar_results import diversify_radar_results, list_radar_listings
    from radar_runs import radar_due_slots, radar_next_slot, radar_now_local, radar_query_plan
    from radar_scheduler import (
        RadarSearchScheduler, list_radar_runs, purge_expired_radar_content,
        run_active_radar_searches, run_due_radar_slots, start_manual_radar_run,
    )
    from radar_scoring import valuate_radar_match
    from radar_searches import (
        create_radar_search, delete_radar_search, duplicate_radar_search, list_radar_searches,
        radar_search_payload, set_radar_search_status, update_radar_search,
    )
    from schema import init_db
    from state import ensure_state_media_migrated, read_state, write_state


SERVICE_VERSION = os.getenv("CONSOLAS_APP_VERSION", "0.1.99")


_SPLIT_MODULES = (
    auction_watch_dismissals,
    auction_watch_following,
    auction_watch_runs,
    auction_watch_snapshot,
    chasing_games,
    clock,
    config,
    constants,
    database,
    errors,
    health,
    http_handler,
    http_utils,
    json_files,
    media,
    media_migration,
    radar_catalog,
    radar_criteria,
    radar_decisions,
    radar_execution,
    radar_feed,
    radar_listings,
    radar_master_searches,
    radar_migrations,
    radar_notifications,
    radar_preferences,
    radar_purchases,
    radar_references,
    radar_results,
    radar_runs,
    radar_scheduler,
    radar_scoring,
    radar_searches,
    schema,
    state,
)
_UNSET = object()


class _ForwardingModule(types.ModuleType):
    """Keeps `patch("server.app.<name>")` working after the split.

    Before the split every function looked its collaborators up in this
    module, so patching a name here reached every caller. Rebinding a
    re-exported name here also rebinds it in each sibling module that holds
    the same object, which preserves that behavior. Nothing in production
    assigns attributes on this module.
    """

    def __setattr__(self, name: str, value: object) -> None:
        current = self.__dict__.get(name, _UNSET)
        if current is not _UNSET:
            for module in _SPLIT_MODULES:
                if module.__dict__.get(name, _UNSET) is current:
                    setattr(module, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ForwardingModule

# SERVICE_VERSION stays in this file because scripts/release-ha-addon.sh stamps
# and audits it here; the health payload reads it from its own module.
health.SERVICE_VERSION = SERVICE_VERSION


def main() -> int:
    config = AppConfig()
    init_db(config)
    ensure_state_media_migrated(config)
    purge_expired_radar_content(config)
    RadarSearchScheduler(config).start()
    print(f"[consolas] Starting on {config.host}:{config.port}")
    print(f"[consolas] Persistent data: {config.data_dir}")
    print(f"[consolas] Static web: {config.static_dir}")
    server = Server((config.host, config.port), Handler, config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
