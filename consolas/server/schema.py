"""SQLite schema creation and in-place migrations (init_db)."""

from __future__ import annotations

try:
    from .clock import utc_now
    from .config import AppConfig
    from .database import connect_db, ensure_directories
    from .radar_migrations import (
        mark_radar_migration,
        migrate_chasing_games_to_radar,
        migrate_drop_seller_identity,
        migrate_radar_purchases_off_listing_lifetime,
        migrate_radar_results_to_listings,
        radar_migration_applied,
        seed_psp_library_search,
        seed_radar_searches,
    )
    from .radar_preferences import (
        RADAR_DEFAULT_DESTINATION_COUNTRY,
        RADAR_DEFAULT_DESTINATION_ZIP,
        RADAR_DEFAULT_MAX_ACTIVE,
    )
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from database import connect_db, ensure_directories
    from radar_migrations import (
        mark_radar_migration,
        migrate_chasing_games_to_radar,
        migrate_drop_seller_identity,
        migrate_radar_purchases_off_listing_lifetime,
        migrate_radar_results_to_listings,
        radar_migration_applied,
        seed_psp_library_search,
        seed_radar_searches,
    )
    from radar_preferences import (
        RADAR_DEFAULT_DESTINATION_COUNTRY,
        RADAR_DEFAULT_DESTINATION_ZIP,
        RADAR_DEFAULT_MAX_ACTIVE,
    )


def init_db(config: AppConfig) -> None:
    ensure_directories(config)
    with connect_db(config) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_state (
              key TEXT PRIMARY KEY,
              state_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS media (
              id TEXT PRIMARY KEY,
              original_file_name TEXT NOT NULL DEFAULT '',
              mime_type TEXT NOT NULL,
              file_name TEXT NOT NULL,
              file_path TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS legacy_import_snapshots (
              id TEXT PRIMARY KEY,
              state_json TEXT NOT NULL,
              imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auction_watch_dismissals (
              source_id TEXT NOT NULL,
              lot_id TEXT NOT NULL,
              group_id TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              lot_url TEXT NOT NULL DEFAULT '',
              image_url TEXT NOT NULL DEFAULT '',
              dismissed_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              missing_since TEXT,
              PRIMARY KEY (source_id, lot_id)
            );

            CREATE TABLE IF NOT EXISTS auction_watch_following (
              source_id TEXT NOT NULL,
              lot_id TEXT NOT NULL,
              group_id TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              lot_url TEXT NOT NULL DEFAULT '',
              followed_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (source_id, lot_id)
            );

            CREATE TABLE IF NOT EXISTS auction_watch_run_requests (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              requested_at TEXT NOT NULL,
              started_at TEXT,
              heartbeat_at TEXT,
              finished_at TEXT,
              detail TEXT NOT NULL DEFAULT '',
              run_id TEXT NOT NULL DEFAULT '',
              snapshot_hash TEXT NOT NULL DEFAULT '',
              snapshot_status TEXT NOT NULL DEFAULT '',
              email_status TEXT NOT NULL DEFAULT '',
              overall_status TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS auction_watch_publications (
              run_id TEXT PRIMARY KEY,
              snapshot_hash TEXT NOT NULL,
              generated_at TEXT NOT NULL,
              accepted_at TEXT NOT NULL,
              matches INTEGER NOT NULL DEFAULT 0,
              recorded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chasing_games (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              platform TEXT NOT NULL DEFAULT '',
              search_query TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT 'ebay-us',
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_checked_at TEXT,
              last_error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS chasing_game_results (
              id TEXT PRIMARY KEY,
              chase_id TEXT NOT NULL,
              external_id TEXT NOT NULL,
              title TEXT NOT NULL,
              price_label TEXT NOT NULL DEFAULT '',
              condition_label TEXT NOT NULL DEFAULT '',
              shipping_label TEXT NOT NULL DEFAULT '',
              location_label TEXT NOT NULL DEFAULT '',
              listing_type TEXT NOT NULL DEFAULT '',
              listing_url TEXT NOT NULL,
              image_url TEXT NOT NULL DEFAULT '',
              is_active INTEGER NOT NULL DEFAULT 1,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              UNIQUE(chase_id, external_id),
              FOREIGN KEY(chase_id) REFERENCES chasing_games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS radar_migrations (
              id TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS radar_searches (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              search_type TEXT NOT NULL DEFAULT 'chase',
              status TEXT NOT NULL DEFAULT 'active',
              origin TEXT NOT NULL DEFAULT 'user',
              priority TEXT NOT NULL DEFAULT 'media',
              platform TEXT NOT NULL DEFAULT '',
              entity_type TEXT NOT NULL DEFAULT '',
              entity_id TEXT NOT NULL DEFAULT '',
              search_query TEXT NOT NULL,
              criteria_json TEXT NOT NULL DEFAULT '{}',
              sources_json TEXT NOT NULL DEFAULT '[]',
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_checked_at TEXT,
              last_error TEXT NOT NULL DEFAULT '',
              archived_at TEXT,
              deleted_at TEXT,
              legacy_chase_id TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS radar_search_results (
              id TEXT PRIMARY KEY,
              search_id TEXT NOT NULL,
              source_id TEXT NOT NULL DEFAULT 'ebay-us',
              external_id TEXT NOT NULL,
              title TEXT NOT NULL,
              price_label TEXT NOT NULL DEFAULT '',
              price_amount REAL,
              price_currency TEXT NOT NULL DEFAULT '',
              condition_label TEXT NOT NULL DEFAULT '',
              shipping_label TEXT NOT NULL DEFAULT '',
              location_label TEXT NOT NULL DEFAULT '',
              listing_type TEXT NOT NULL DEFAULT '',
              listing_url TEXT NOT NULL,
              image_url TEXT NOT NULL DEFAULT '',
              is_active INTEGER NOT NULL DEFAULT 1,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              UNIQUE(search_id, source_id, external_id),
              FOREIGN KEY(search_id) REFERENCES radar_searches(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS radar_listings (
              id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              external_id TEXT NOT NULL,
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              listing_url TEXT NOT NULL,
              image_url TEXT NOT NULL DEFAULT '',
              listing_kind TEXT NOT NULL DEFAULT 'unknown',
              price_amount REAL,
              price_currency TEXT NOT NULL DEFAULT '',
              shipping_amount REAL,
              shipping_currency TEXT NOT NULL DEFAULT '',
              total_amount REAL,
              price_label TEXT NOT NULL DEFAULT '',
              shipping_label TEXT NOT NULL DEFAULT '',
              condition_label TEXT NOT NULL DEFAULT '',
              location_label TEXT NOT NULL DEFAULT '',
              seller_label TEXT NOT NULL DEFAULT '',
              availability TEXT NOT NULL DEFAULT 'unknown',
              closes_at TEXT NOT NULL DEFAULT '',
              verified_at TEXT,
              content_expires_at TEXT,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              UNIQUE(source_id, external_id)
            );

            CREATE TABLE IF NOT EXISTS radar_search_matches (
              search_id TEXT NOT NULL,
              listing_id TEXT NOT NULL,
              confidence REAL NOT NULL DEFAULT 0,
              reasons_json TEXT NOT NULL DEFAULT '[]',
              blockers_json TEXT NOT NULL DEFAULT '[]',
              unverified_json TEXT NOT NULL DEFAULT '[]',
              matched_terms_json TEXT NOT NULL DEFAULT '[]',
              is_active INTEGER NOT NULL DEFAULT 1,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              PRIMARY KEY (search_id, listing_id),
              FOREIGN KEY(search_id) REFERENCES radar_searches(id) ON DELETE CASCADE,
              FOREIGN KEY(listing_id) REFERENCES radar_listings(id) ON DELETE CASCADE
            );

            -- Lo que dice una publicación además de su resumen (descripción,
            -- devoluciones, fotos, impuestos informados). Es caché de la
            -- fuente, no estado del usuario: se vuelve a pedir si cambia el
            -- precio o pasa el plazo.
            CREATE TABLE IF NOT EXISTS radar_listing_details (
              source_id TEXT NOT NULL,
              external_id TEXT NOT NULL,
              price_amount REAL,
              fetched_at TEXT NOT NULL,
              detail_json TEXT NOT NULL DEFAULT '{}',
              PRIMARY KEY (source_id, external_id)
            );

            CREATE TABLE IF NOT EXISTS radar_decisions (
              listing_id TEXT PRIMARY KEY,
              decision TEXT NOT NULL,
              reason TEXT NOT NULL DEFAULT '',
              note TEXT NOT NULL DEFAULT '',
              snooze_until TEXT,
              price_at_decision REAL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(listing_id) REFERENCES radar_listings(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS radar_decisions_state_idx
              ON radar_decisions (decision, snooze_until);

            CREATE TABLE IF NOT EXISTS radar_outbox (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              run_id TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              recipient TEXT NOT NULL,
              subject TEXT NOT NULL,
              body_text TEXT NOT NULL,
              body_html TEXT NOT NULL DEFAULT '',
              message_id TEXT NOT NULL,
              listing_ids_json TEXT NOT NULL DEFAULT '[]',
              attempts INTEGER NOT NULL DEFAULT 0,
              detail TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              sent_at TEXT
            );

            CREATE TABLE IF NOT EXISTS radar_notifications (
              listing_id TEXT PRIMARY KEY,
              notified_at TEXT NOT NULL,
              price_at_notification REAL,
              kind TEXT NOT NULL DEFAULT 'digest',
              message_id TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS radar_outbox_status_idx ON radar_outbox (status, created_at);

            CREATE TABLE IF NOT EXISTS radar_runs (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              slot_key TEXT NOT NULL DEFAULT '',
              schedule_date TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              searches_total INTEGER NOT NULL DEFAULT 0,
              searches_ok INTEGER NOT NULL DEFAULT 0,
              searches_failed INTEGER NOT NULL DEFAULT 0,
              listings_matched INTEGER NOT NULL DEFAULT 0,
              listings_rejected INTEGER NOT NULL DEFAULT 0,
              detail TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS radar_run_receipts (
              run_id TEXT NOT NULL,
              search_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              status TEXT NOT NULL,
              listing_count INTEGER NOT NULL DEFAULT 0,
              matched_count INTEGER NOT NULL DEFAULT 0,
              rejected_count INTEGER NOT NULL DEFAULT 0,
              error_count INTEGER NOT NULL DEFAULT 0,
              errors_json TEXT NOT NULL DEFAULT '[]',
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              PRIMARY KEY (run_id, search_id, source_id),
              FOREIGN KEY(run_id) REFERENCES radar_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS radar_schedule_slots (
              schedule_date TEXT NOT NULL,
              slot_key TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'fulfilled',
              fulfilled_by_run_id TEXT NOT NULL DEFAULT '',
              detail TEXT NOT NULL DEFAULT '',
              claimed_at TEXT NOT NULL,
              PRIMARY KEY (schedule_date, slot_key)
            );

            -- Fila única (PRD §15 RadarPreferences, recortado a lo que este
            -- slice necesita: el presupuesto mensual). NULL hasta que el
            -- owner lo configure — PRD §25: no bloquea nada, el control se
            -- muestra sin calcular saldo hasta tener un monto.
            CREATE TABLE IF NOT EXISTS radar_preferences (
              id TEXT PRIMARY KEY DEFAULT 'default',
              monthly_budget_usd REAL,
              updated_at TEXT NOT NULL
            );

            -- El registro del radar de que "Registrar compra" se ejecutó.
            -- Esto NO es la colección: es la evidencia para el presupuesto
            -- (gastado del mes) y el historial. El estado de usuario real
            -- (tengo/ownershipType/precioPagado) lo escribe el frontend por
            -- CollectionRepository, nunca esta tabla.
            --
            -- La publicación es opcional y su borrado no arrastra la compra:
            -- una compra hecha fuera del radar no tiene publicación, y la que
            -- sí la tiene la pierde a las 24 h de venderse (TTL de licencia).
            CREATE TABLE IF NOT EXISTS radar_purchases (
              id TEXT PRIMARY KEY,
              listing_id TEXT,
              entity_type TEXT NOT NULL DEFAULT '',
              entity_id TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              manual INTEGER NOT NULL DEFAULT 0,
              price_amount REAL NOT NULL,
              currency TEXT NOT NULL DEFAULT 'USD',
              purchased_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(listing_id) REFERENCES radar_listings(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS radar_purchases_purchased_at_idx
              ON radar_purchases (purchased_at);

            CREATE INDEX IF NOT EXISTS radar_runs_started_idx
              ON radar_runs (started_at DESC);
            CREATE INDEX IF NOT EXISTS radar_searches_status_idx
              ON radar_searches (status, deleted_at);
            CREATE INDEX IF NOT EXISTS radar_search_results_active_idx
              ON radar_search_results (search_id, is_active);
            CREATE INDEX IF NOT EXISTS radar_search_matches_active_idx
              ON radar_search_matches (search_id, is_active);
            CREATE INDEX IF NOT EXISTS radar_search_matches_listing_idx
              ON radar_search_matches (listing_id, is_active);
            """
        )
        dismissal_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(auction_watch_dismissals)").fetchall()
        }
        if "missing_since" not in dismissal_columns:
            conn.execute("ALTER TABLE auction_watch_dismissals ADD COLUMN missing_since TEXT")
        if "image_url" not in dismissal_columns:
            conn.execute("ALTER TABLE auction_watch_dismissals ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
        run_request_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(auction_watch_run_requests)").fetchall()
        }
        run_request_migrations = {
            "heartbeat_at": "TEXT",
            "run_id": "TEXT NOT NULL DEFAULT ''",
            "snapshot_hash": "TEXT NOT NULL DEFAULT ''",
            "snapshot_status": "TEXT NOT NULL DEFAULT ''",
            "email_status": "TEXT NOT NULL DEFAULT ''",
            "overall_status": "TEXT NOT NULL DEFAULT ''",
        }
        for column_name, column_definition in run_request_migrations.items():
            if column_name not in run_request_columns:
                conn.execute(
                    f"ALTER TABLE auction_watch_run_requests ADD COLUMN {column_name} {column_definition}"
                )
        radar_search_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(radar_searches)").fetchall()
        }
        # Vacío significa "todos los slots": una búsqueda existente no cambia de
        # frecuencia por el hecho de actualizar el add-on.
        # Distinguir una consulta escrita a mano de una derivada no puede depender
        # de comparar strings: cuando cambia la lógica de derivación, toda consulta
        # vieja parece escrita a mano y queda congelada. Se marca explícitamente.
        if "query_custom" not in radar_search_columns:
            conn.execute("ALTER TABLE radar_searches ADD COLUMN query_custom INTEGER NOT NULL DEFAULT 0")
        if "slots_json" not in radar_search_columns:
            conn.execute("ALTER TABLE radar_searches ADD COLUMN slots_json TEXT NOT NULL DEFAULT ''")
        # Un juego no se identifica sólo por su id: el mismo nombre existe en
        # varias plataformas. La identidad real es el par {consoleId, gameId},
        # igual que `catalogRef` en el Franchise Tracker. El Master ya conocía
        # la consola al proponer la búsqueda y la tiraba; acá se conserva para
        # que "Registrar compra" sepa a qué biblioteca escribir.
        if "entity_console_id" not in radar_search_columns:
            conn.execute("ALTER TABLE radar_searches ADD COLUMN entity_console_id TEXT NOT NULL DEFAULT ''")
        # Techo de búsquedas activas. Sin él, pedirle varias tandas al Master y
        # aceptarlas en bloque deja ochenta búsquedas corriendo tres veces por
        # día: el feed se vuelve ilegible y deja de servir para detectar nada.
        # El cupo del Master era por tanda, no global, así que nada frenaba la
        # acumulación.
        #
        # Se siembra desde el estado real y nunca por debajo de lo que ya hay
        # activo: un techo que empieza por debajo de la realidad bloquearía la
        # próxima activación legítima por algo que el usuario no hizo hoy.
        preference_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(radar_preferences)").fetchall()
        }
        if "max_active_searches" not in preference_columns:
            conn.execute("ALTER TABLE radar_preferences ADD COLUMN max_active_searches INTEGER")
            active_now = int(
                conn.execute(
                    "SELECT COUNT(*) FROM radar_searches WHERE status = 'active' AND deleted_at IS NULL"
                ).fetchone()[0]
            )
            seeded = max(RADAR_DEFAULT_MAX_ACTIVE, active_now)
            conn.execute(
                """INSERT INTO radar_preferences (id, max_active_searches, updated_at)
                   VALUES ('default', ?, ?)
                   ON CONFLICT(id) DO UPDATE SET max_active_searches = excluded.max_active_searches""",
                (seeded, utc_now()),
            )
        # El destino de recepción. Sin él, la Browse API de eBay no devuelve
        # opciones de envío para las publicaciones de envío CALCULADO, así que
        # el radar guardaba envío desconocido y mostraba el precio del artículo
        # como si fuera el total: un total corto es el peor error posible en una
        # decisión de compra. El ZIP es preferencia privada server-side (PRD
        # §15); la dirección completa nunca entra acá.
        if "destination_country" not in preference_columns:
            conn.execute("ALTER TABLE radar_preferences ADD COLUMN destination_country TEXT")
        if "destination_zip" not in preference_columns:
            conn.execute("ALTER TABLE radar_preferences ADD COLUMN destination_zip TEXT")
        # Se siembra una sola vez y con marcador: si el dueño lo borra o lo
        # cambia, no vuelve en el próximo arranque (misma lección que D2).
        if not radar_migration_applied(conn, "radar_destination_v1"):
            conn.execute(
                """INSERT INTO radar_preferences (id, destination_country, destination_zip, updated_at)
                   VALUES ('default', ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     destination_country = excluded.destination_country,
                     destination_zip = excluded.destination_zip""",
                (RADAR_DEFAULT_DESTINATION_COUNTRY, RADAR_DEFAULT_DESTINATION_ZIP, utc_now()),
            )
            mark_radar_migration(conn, "radar_destination_v1")
        purchase_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(radar_purchases)").fetchall()
        }
        if "entity_console_id" not in purchase_columns:
            conn.execute("ALTER TABLE radar_purchases ADD COLUMN entity_console_id TEXT NOT NULL DEFAULT ''")
        # Lo comprado no viaja de inmediato: se acumula en la casilla de Estados
        # Unidos y se reenvía junto. Sin fecha de reenvío, la compra sigue en la
        # pila que hay que mantener bajo la franquicia.
        if "shipped_at" not in purchase_columns:
            conn.execute("ALTER TABLE radar_purchases ADD COLUMN shipped_at TEXT")
        migrate_radar_purchases_off_listing_lifetime(conn)
        listing_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(radar_listings)").fetchall()
        }
        # Sin el precio anterior no hay forma de saber que algo bajó, que es
        # justamente lo que convierte un «lo quiero pero está caro» en un aviso.
        for column_name, column_definition in {
            "previous_price_amount": "REAL",
            "price_changed_at": "TEXT",
            # Slice 10: ShopGoodwill llega por carga manual (Personal Shopper /
            # alerta guardada), nunca por scan automático. `requires_verification`
            # marca que todavía nadie confirmó Add to Cart en la publicación real.
            "requires_verification": "INTEGER NOT NULL DEFAULT 0",
            "verified_at": "TEXT",
            # Un CIB vale por la caja y el manual, y eso sólo se comprueba
            # mirando las fotos. Lo hace el dueño, una vez: el contenido no
            # vence como la disponibilidad.
            "contents_verified_at": "TEXT",
        }.items():
            if column_name not in listing_columns:
                conn.execute(f"ALTER TABLE radar_listings ADD COLUMN {column_name} {column_definition}")
        match_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(radar_search_matches)").fetchall()
        }
        for column_name, column_definition in {
            "score": "INTEGER",
            "band": "TEXT NOT NULL DEFAULT ''",
            "valuation_json": "TEXT NOT NULL DEFAULT '{}'",
            # Qué título de la búsqueda encontró esta publicación, cuando la
            # búsqueda persigue varios: es lo que «Registrar compra» escribe.
            "target_json": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column_name not in match_columns:
                conn.execute(f"ALTER TABLE radar_search_matches ADD COLUMN {column_name} {column_definition}")
        decision_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(radar_decisions)").fetchall()
        }
        # "Reservado" es del presupuesto (PRD §10.6): una oportunidad que el
        # owner ya cuenta como plan probable, separada de sólo "seguirla".
        if "reserved" not in decision_columns:
            conn.execute("ALTER TABLE radar_decisions ADD COLUMN reserved INTEGER NOT NULL DEFAULT 0")
        migrate_chasing_games_to_radar(conn)
        migrate_radar_results_to_listings(conn)
        migrate_drop_seller_identity(conn)
        seed_radar_searches(conn)
        seed_psp_library_search(conn)
