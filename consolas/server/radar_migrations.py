"""Collection Radar: data migrations and seeded searches."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

try:
    from .clock import utc_now
    from .constants import (
        RADAR_DEFAULT_SOURCE,
        RADAR_MIGRATION_CHASING_GAMES,
        RADAR_MIGRATION_LISTINGS,
        RADAR_MIGRATION_PSP_LIBRARY,
        RADAR_MIGRATION_PURCHASES_OFF_LISTING,
        RADAR_MIGRATION_SEED,
        RADAR_MIGRATION_SELLER_IDENTITY,
    )
    from .radar_criteria import default_radar_criteria
    from .radar_listings import radar_listing_id, radar_listing_kind_from_label
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from constants import (
        RADAR_DEFAULT_SOURCE,
        RADAR_MIGRATION_CHASING_GAMES,
        RADAR_MIGRATION_LISTINGS,
        RADAR_MIGRATION_PSP_LIBRARY,
        RADAR_MIGRATION_PURCHASES_OFF_LISTING,
        RADAR_MIGRATION_SEED,
        RADAR_MIGRATION_SELLER_IDENTITY,
    )
    from radar_criteria import default_radar_criteria
    from radar_listings import radar_listing_id, radar_listing_kind_from_label


def radar_migration_applied(conn: sqlite3.Connection, migration_id: str) -> bool:
    return conn.execute("SELECT 1 FROM radar_migrations WHERE id = ?", (migration_id,)).fetchone() is not None


def mark_radar_migration(conn: sqlite3.Connection, migration_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO radar_migrations (id, applied_at) VALUES (?, ?)",
        (migration_id, utc_now()),
    )


def migrate_radar_purchases_off_listing_lifetime(conn: sqlite3.Connection) -> None:
    """Desata la compra de la vida de su publicación.

    La tabla original exigía publicación y la borraba en cascada con ella. Pero
    una publicación de eBay vence a las 24 h de dejar de aparecer, y una
    publicación comprada deja de aparecer justamente porque se vendió: el
    purgado de contenido se llevaba la compra, y con ella el gasto del mes y lo
    que esperaba en la casilla. Tampoco había lugar para una compra hecha fuera
    del radar.

    SQLite no deja cambiar ni el NOT NULL ni la acción de la clave foránea, así
    que la tabla se reconstruye copiando cada fila tal como está.
    """

    if radar_migration_applied(conn, RADAR_MIGRATION_PURCHASES_OFF_LISTING):
        return
    columns = {str(row["name"]): row for row in conn.execute("PRAGMA table_info(radar_purchases)").fetchall()}
    listing_required = bool(columns.get("listing_id") and columns["listing_id"]["notnull"])
    cascades = any(
        str(row["table"]) == "radar_listings" and str(row["on_delete"]).upper() == "CASCADE"
        for row in conn.execute("PRAGMA foreign_key_list(radar_purchases)").fetchall()
    )
    if listing_required or cascades or "title" not in columns or "manual" not in columns:
        conn.executescript(
            """
            CREATE TABLE radar_purchases_rebuilt (
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
              entity_console_id TEXT NOT NULL DEFAULT '',
              shipped_at TEXT,
              FOREIGN KEY(listing_id) REFERENCES radar_listings(id) ON DELETE SET NULL
            );
            INSERT INTO radar_purchases_rebuilt (
              id, listing_id, entity_type, entity_id, price_amount, currency,
              purchased_at, created_at, entity_console_id, shipped_at
            )
            SELECT id,
                   -- Una fila que ya apunta a una publicación inexistente no
                   -- puede frenar la migración: la compra se conserva igual.
                   CASE WHEN listing_id IN (SELECT id FROM radar_listings) THEN listing_id END,
                   entity_type, entity_id, price_amount, currency,
                   purchased_at, created_at, entity_console_id, shipped_at
              FROM radar_purchases;
            DROP TABLE radar_purchases;
            ALTER TABLE radar_purchases_rebuilt RENAME TO radar_purchases;
            CREATE INDEX IF NOT EXISTS radar_purchases_purchased_at_idx
              ON radar_purchases (purchased_at);
            """
        )
    mark_radar_migration(conn, RADAR_MIGRATION_PURCHASES_OFF_LISTING)


def migrate_chasing_games_to_radar(conn: sqlite3.Connection) -> None:
    """Copy legacy Chasing Games searches into the general radar model, once.

    The marker is written even when there was nothing to copy. Guarding on "the
    radar table is empty" instead would resurrect every deleted search on the
    next restart.
    """

    if radar_migration_applied(conn, RADAR_MIGRATION_CHASING_GAMES):
        return
    for row in conn.execute("SELECT * FROM chasing_games ORDER BY created_at").fetchall():
        search_id = str(row["id"])
        conn.execute(
            """
            INSERT OR IGNORE INTO radar_searches (
              id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
              search_query, criteria_json, sources_json, notes, created_at, updated_at,
              last_checked_at, last_error, legacy_chase_id
            ) VALUES (?, ?, 'chase', ?, 'user', 'media', ?, '', '', ?, ?, ?, '', ?, ?, ?, ?, ?)
            """,
            (
                search_id,
                str(row["title"]),
                "active" if row["enabled"] else "paused",
                str(row["platform"] or ""),
                str(row["search_query"]),
                json.dumps(default_radar_criteria(), ensure_ascii=False, separators=(",", ":")),
                json.dumps([str(row["source"] or RADAR_DEFAULT_SOURCE)], ensure_ascii=False, separators=(",", ":")),
                str(row["created_at"]),
                str(row["updated_at"]),
                row["last_checked_at"],
                str(row["last_error"] or ""),
                search_id,
            ),
        )
        for result in conn.execute(
            "SELECT * FROM chasing_game_results WHERE chase_id = ?", (search_id,)
        ).fetchall():
            conn.execute(
                """
                INSERT OR IGNORE INTO radar_search_results (
                  id, search_id, source_id, external_id, title, price_label, price_amount, price_currency,
                  condition_label, shipping_label, location_label, listing_type, listing_url, image_url,
                  is_active, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(result["id"]),
                    search_id,
                    str(row["source"] or RADAR_DEFAULT_SOURCE),
                    str(result["external_id"]),
                    str(result["title"]),
                    str(result["price_label"] or ""),
                    str(result["condition_label"] or ""),
                    str(result["shipping_label"] or ""),
                    str(result["location_label"] or ""),
                    str(result["listing_type"] or ""),
                    str(result["listing_url"]),
                    str(result["image_url"] or ""),
                    int(result["is_active"]),
                    str(result["first_seen_at"]),
                    str(result["last_seen_at"]),
                ),
            )
    mark_radar_migration(conn, RADAR_MIGRATION_CHASING_GAMES)


def migrate_radar_results_to_listings(conn: sqlite3.Connection) -> None:
    """Divide los resultados por búsqueda en publicaciones y coincidencias.

    `radar_search_results` guardaba una fila por (búsqueda, publicación): la misma
    publicación se duplicaba en cada búsqueda que la encontrara. El modelo del PRD
    separa identidad (`radar_listings`) de intención (`radar_search_matches`), así
    una publicación existe una vez y se muestra una vez, con varias razones.

    Corre una sola vez, con marcador propio: la tabla vieja queda intacta como vía
    de rollback y deja de leerse.
    """

    if radar_migration_applied(conn, RADAR_MIGRATION_LISTINGS):
        return
    for row in conn.execute("SELECT * FROM radar_search_results ORDER BY first_seen_at").fetchall():
        source_id = str(row["source_id"] or RADAR_DEFAULT_SOURCE)
        external_id = str(row["external_id"])
        listing_id = radar_listing_id(source_id, external_id)
        price_amount = row["price_amount"]
        conn.execute(
            """
            INSERT INTO radar_listings (
              id, source_id, external_id, title, description, listing_url, image_url, listing_kind,
              price_amount, price_currency, shipping_amount, shipping_currency, total_amount,
              price_label, shipping_label, condition_label, location_label, seller_label,
              availability, closes_at, content_expires_at, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, NULL, '', ?, ?, ?, ?, ?, '', 'unknown', '', NULL, ?, ?)
            ON CONFLICT(source_id, external_id) DO UPDATE SET
              last_seen_at = MAX(radar_listings.last_seen_at, excluded.last_seen_at)
            """,
            (
                listing_id, source_id, external_id, str(row["title"]), str(row["listing_url"]),
                str(row["image_url"] or ""), radar_listing_kind_from_label(str(row["listing_type"] or "")),
                price_amount, str(row["price_currency"] or ""), price_amount,
                str(row["price_label"] or ""), str(row["shipping_label"] or ""),
                str(row["condition_label"] or ""), str(row["location_label"] or ""),
                str(row["first_seen_at"]), str(row["last_seen_at"]),
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO radar_search_matches (
              search_id, listing_id, confidence, reasons_json, blockers_json, unverified_json,
              matched_terms_json, is_active, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, '[]', '[]', '[]', ?, ?, ?)
            """,
            (
                str(row["search_id"]), listing_id, 0.5,
                json.dumps(["Resultado guardado antes del modelo de coincidencias"], ensure_ascii=False),
                int(row["is_active"]), str(row["first_seen_at"]), str(row["last_seen_at"]),
            ),
        )
    mark_radar_migration(conn, RADAR_MIGRATION_LISTINGS)


def migrate_drop_seller_identity(conn: sqlite3.Connection) -> None:
    """Borra los nombres de usuario de vendedores que se hayan guardado antes.

    Cambiar el adapter evita guardarlos de ahora en más, pero la declaración que
    se le firma a eBay —que la aplicación no almacena datos de sus usuarios—
    tiene que ser cierta también de lo que ya está en disco.

    El formato viejo era `usuario · 99.4%`. Se conserva la reputación, que es lo
    que el score necesita, y se descarta la identidad.
    """

    if radar_migration_applied(conn, RADAR_MIGRATION_SELLER_IDENTITY):
        return
    for row in conn.execute(
        "SELECT id, seller_label FROM radar_listings WHERE seller_label != ''"
    ).fetchall():
        label = str(row["seller_label"])
        if "·" not in label:
            continue
        percentage = label.rsplit("·", 1)[-1].strip().rstrip("%").strip()
        replacement = f"{percentage}% de feedback" if percentage else ""
        conn.execute(
            "UPDATE radar_listings SET seller_label = ? WHERE id = ?", (replacement, str(row["id"]))
        )
    mark_radar_migration(conn, RADAR_MIGRATION_SELLER_IDENTITY)


def seed_radar_searches(conn: sqlite3.Connection) -> None:
    """Create the first explicit chase once, without touching collection state."""
    if radar_migration_applied(conn, RADAR_MIGRATION_SEED):
        return
    mark_radar_migration(conn, RADAR_MIGRATION_SEED)
    if conn.execute("SELECT 1 FROM radar_searches LIMIT 1").fetchone():
        return
    now = utc_now()
    conn.execute(
        """
        INSERT INTO radar_searches (
          id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
          search_query, criteria_json, sources_json, notes, created_at, updated_at
        ) VALUES (?, ?, 'chase', 'active', 'user', 'alta', ?, '', '', ?, ?, ?, '', ?, ?)
        """,
        (
            "iss-deluxe-snes",
            "International Superstar Soccer Deluxe",
            "SNES",
            "International Superstar Soccer Deluxe SNES",
            json.dumps(default_radar_criteria(), ensure_ascii=False, separators=(",", ":")),
            json.dumps([RADAR_DEFAULT_SOURCE], ensure_ascii=False, separators=(",", ":")),
            now,
            now,
        ),
    )


# Los títulos que el owner pidió perseguir, en el orden en que los pidió. Son
# datos del pedido, no lógica: el motor que los usa (`criteria.queries`) no sabe
# qué es una PSP y sirve igual para cualquier otra lista de títulos.
PSP_LIBRARY_TOP_PRIORITY = (
    "God of War Chains of Olympus",
    "God of War Ghost of Sparta",
    "Metal Gear Solid Peace Walker",
    "Crisis Core Final Fantasy VII",
    "Castlevania The Dracula X Chronicles",
    "Grand Theft Auto Vice City Stories",
    "Grand Theft Auto Liberty City Stories",
    "Daxter",
    "Patapon",
    "Patapon 2",
    "LocoRoco",
    "LocoRoco 2",
    "Lumines",
    "Burnout Legends",
    "Kingdom Hearts Birth by Sleep",
)

PSP_LIBRARY_SECOND_PRIORITY = (
    "Ratchet and Clank Size Matters",
    "Resistance Retribution",
    "Killzone Liberation",
    "Tekken Dark Resurrection",
    "Gran Turismo",
    "Wipeout Pure",
    "Wipeout Pulse",
    "Ridge Racer",
    "Monster Hunter Freedom Unite",
    "Final Fantasy Tactics The War of the Lions",
    "Tactics Ogre Let Us Cling Together",
    "Jeanne d'Arc",
    "Mega Man Powered Up",
    "Mega Man Maverick Hunter X",
    "Persona 3 Portable",
    "Silent Hill Origins",
    "Silent Hill Shattered Memories",
    "The Warriors",
    "LittleBigPlanet",
    "Ys Seven",
)

# Lotes: la consulta principal de la búsqueda más dos formas de nombrar lo mismo.
PSP_LIBRARY_LOT_QUERIES = ("PSP game lot", "PSP UMD lot", "PSP games bundle")


def psp_library_criteria() -> dict[str, Any]:
    """Los criterios del pedido de biblioteca de PSP que el radar sí puede evaluar.

    Lo que queda afuera —tiers de precio por título, PriceCharting, ventas
    cerradas, fotos, envío a un ZIP concreto— está documentado en
    `docs/BACKLOG.md`. Acá no se declara nada que el matcher no pueda medir.
    """

    criteria = default_radar_criteria()
    criteria.update(
        {
            # Cada título es su propia consulta: eBay entiende una por vez y
            # "biblioteca de PSP" no es una consulta.
            "queries": [
                *(f"{title} PSP" for title in PSP_LIBRARY_TOP_PRIORITY),
                *(f"{title} PSP" for title in PSP_LIBRARY_SECOND_PRIORITY),
                *PSP_LIBRARY_LOT_QUERIES[1:],
            ],
            # Puerta de plataforma, no de título: una consulta por "Lumines" o
            # "Gran Turismo" sin esto traería la versión de otra consola.
            "anyTerms": ["psp", "playstation portable", "umd"],
            "excludeTerms": [
                "repro",
                "reproduction",
                "bootleg",
                "counterfeit",
                "no umd",
                "umd video",
                "umd movie",
                "for parts",
                "parts only",
                "broken",
                "water damage",
                "mold",
                "demo",
                "digital code",
                "download code",
                "screen protector",
                "replacement screen",
                "battery cover",
                "charger",
                "carrying case",
                "faceplate",
                "custom case",
            ],
            # Sin bloqueo regional: los juegos de PSP no lo tienen, y el owner
            # pidió aceptar NTSC-U/C, PAL y NTSC-J.
            "region": "",
            # CIB es la preferencia, no el filtro. Exigirlo ya se probó en la
            # búsqueda de juegos de PS3 y devolvía ruido: el vendedor rara vez
            # escribe "complete in box" aunque la caja esté en la foto.
            "completeness": "any",
            # Bloquea ante evidencia de reproducción; la ausencia de declaración
            # queda como "verificar antes de comprar", no como descarte.
            "originalParts": "required",
            "currency": "USD",
            # El único techo que el owner declaró como absoluto: "No recomendar
            # juegos de US$100 o más salvo una oportunidad extraordinaria".
            "maxItemPrice": 100,
            # "Para juegos comunes, priorizar CIB por debajo de US$20."
            "targetItemPrice": 20,
            # "Entregar como máximo 10 resultados realmente interesantes."
            "resultLimit": 10,
        }
    )
    return criteria


def seed_psp_library_search(conn: sqlite3.Connection) -> None:
    """Deja creada, **suspendida**, la búsqueda de biblioteca de PSP del owner.

    Nace `paused` a propósito: el owner todavía no confirmó que compró la
    consola, y pidió poder activarla de un clic cuando lo haga. La migración es
    aditiva e idempotente por partida doble — por su id de migración y por el
    nombre — así que no duplica ni pisa una búsqueda que el owner ya haya
    editado, y si la borra no vuelve a aparecer.
    """

    if radar_migration_applied(conn, RADAR_MIGRATION_PSP_LIBRARY):
        return
    mark_radar_migration(conn, RADAR_MIGRATION_PSP_LIBRARY)
    name = "Biblioteca PSP — juegos esenciales"
    existing = conn.execute(
        "SELECT 1 FROM radar_searches WHERE lower(name) = ? AND deleted_at IS NULL",
        (name.lower(),),
    ).fetchone()
    if existing:
        return
    now = utc_now()
    conn.execute(
        """INSERT INTO radar_searches (
             id, name, search_type, status, origin, priority, platform, entity_type, entity_id,
             entity_console_id, search_query, query_custom, criteria_json, sources_json, slots_json,
             notes, created_at, updated_at
           ) VALUES (?, ?, 'lot', 'paused', 'user', 'alta', 'PSP', '', '', '', ?, 1, ?, ?, ?, ?, ?, ?)""",
        (
            "radar-psp-library",
            name,
            PSP_LIBRARY_LOT_QUERIES[0],
            json.dumps(psp_library_criteria(), ensure_ascii=False, separators=(",", ":")),
            json.dumps(["ebay-us"], ensure_ascii=False, separators=(",", ":")),
            json.dumps(["morning", "afternoon", "night"], ensure_ascii=False, separators=(",", ":")),
            "Suspendida hasta que confirmes la PSP. Juegos sueltos y lotes: una consulta por "
            "título más tres de lote. CIB se prefiere pero no se exige. Sin bloqueo de región. "
            "Los tramos de precio por título (menos de 20, 20-40, más de 40) no son expresables "
            "en una sola búsqueda: ver docs/BACKLOG.md.",
            now,
            now,
        ),
    )
