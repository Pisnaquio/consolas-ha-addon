"""Service, Auction Watch and Collection Radar constants, plus the shared locks."""

from __future__ import annotations

import os
import threading
from datetime import timedelta


SERVICE_NAME = "consolas-server"
DEFAULT_DATA_DIR = "/data"
DEFAULT_STATIC_DIR = "/app/web"
DATABASE_NAME = "consolas.sqlite"
DEFAULT_PORT = 8788
DEFAULT_BODY_LIMIT = 25 * 1024 * 1024
AUCTION_WATCH_DISMISSALS_VERSION = 1
AUCTION_WATCH_FOLLOWING_VERSION = 1
AUCTION_WATCH_PUBLICATION_LIFECYCLE_VERSION = 1
AUCTION_WATCH_DISMISSAL_GRACE = timedelta(hours=48)
AUCTION_WATCH_PENDING_TIMEOUT = timedelta(minutes=10)
AUCTION_WATCH_RUNNING_TIMEOUT = timedelta(minutes=30)
AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS = 36 * 60 * 60
AUCTION_WATCH_RECEIPT_FILE = "publication-receipt.json"
AUCTION_WATCH_SNAPSHOT_HASH_HEADER = "X-Auction-Watch-Snapshot-Hash"
AUCTION_WATCH_SNAPSHOT_STATUSES = {"skipped", "published", "failed"}
AUCTION_WATCH_EMAIL_STATUSES = {"disabled", "pending", "sent", "failed", "uncertain"}
AUCTION_WATCH_OVERALL_STATUSES = {"completed", "degraded", "delivery_pending", "failed"}
AUCTION_WATCH_PUBLICATION_STATES = {"current", "superseded", "missing"}
CHASING_GAMES_VERSION = 1
CHASING_GAMES_SOURCE = "ebay-us"
CHASING_GAMES_INTERVAL_SECONDS = int(os.getenv("CHASING_GAMES_INTERVAL_SECONDS", "86400"))
EBAY_ENVIRONMENTS = {"sandbox", "production"}

# Collection Radar: modelo general de busqueda persistente. Ver
# docs/COLLECTION_RADAR_IMPLEMENTATION_PLAN.md
RADAR_SEARCHES_VERSION = 1
RADAR_DEFAULT_SOURCE = "ebay-us"
RADAR_WRITE_HEADER = "X-Consolas-Radar"
RADAR_SEARCH_STATUSES = ("draft", "active", "paused", "archived")
RADAR_SEARCH_TYPES = ("chase", "console", "lot", "upgrade", "discovery", "master")
RADAR_SEARCH_ORIGINS = ("user", "master", "suggestion")
RADAR_PRIORITIES = ("alta", "media-alta", "media", "baja")
RADAR_ENTITY_TYPES = ("", "console", "game", "accessory", "manual")
RADAR_CONDITIONS = ("any", "new", "used", "refurbished")
RADAR_COMPLETENESS = ("any", "loose", "boxed", "cib", "sealed")
RADAR_REQUIREMENT_LEVELS = ("any", "preferred", "required")
# Las regiones que el matcher sabe reconocer (radar/matching.py REGION_SIGNALS).
RADAR_REGIONS = ("ntsc-j", "pal", "ntsc-u/c")
RADAR_MAX_TERMS = 24
RADAR_MAX_TERM_LENGTH = 80
# Consultas adicionales de una misma búsqueda (PRD §9 "Consulta"). Una búsqueda
# que persigue una lista de títulos no se puede expresar con un solo `q`: eBay
# entiende una consulta por vez, y "biblioteca de PSP" no es una consulta. Cada
# entrada se manda a cada fuente por separado y los resultados se unen.
RADAR_MAX_QUERIES = 48
RADAR_MAX_QUERY_LENGTH = 300
# Un objetivo por consulta como máximo: la principal más las extra.
RADAR_MAX_TITLE_TARGETS = RADAR_MAX_QUERIES + 1
# Otros nombres de un mismo título: grafías («Little Big Planet 2»), ediciones
# de otra región («God of War Collection Volume II»), abreviaturas («MGS4»).
RADAR_MAX_TITLE_ALIASES = 6
RADAR_MAX_TITLE_NAME_LENGTH = 200
RADAR_DEFAULT_RESULT_LIMIT = 12
RADAR_MAX_RESULT_LIMIT = 50
RADAR_MIGRATION_CHASING_GAMES = "chasing_games_v1"
RADAR_MIGRATION_LISTINGS = "radar_listings_v1"
RADAR_MIGRATION_SEED = "seed_iss_deluxe_v1"
RADAR_MIGRATION_PSP_LIBRARY = "seed_psp_library_v1"
RADAR_MIGRATION_SELLER_IDENTITY = "drop_seller_identity_v1"
RADAR_MIGRATION_PURCHASES_OFF_LISTING = "radar_purchases_off_listing_v1"

# Scheduler durable. Los horarios están cerrados en el PRD §16: tres corridas
# diarias en la zona del usuario. Un slot genera como máximo un scan.
RADAR_TIMEZONE_NAME = os.getenv("CONSOLAS_RADAR_TIMEZONE", "America/Montevideo")
RADAR_SLOTS: tuple[tuple[str, int, int], ...] = (
    ("morning", 9, 0),
    ("afternoon", 16, 0),
    ("night", 22, 30),
)
RADAR_SLOT_KEYS = tuple(slot[0] for slot in RADAR_SLOTS)
# Presupuesto de consultas por corrida. La auditoría del 2026-11-01 contó 294
# consultas en 28 búsquedas activas, 201 de sus 213 títulos recomendaciones
# viejas. Se reparte primero a la wishlist, después a las consolas que faltan
# y a los lotes elegidos; las recomendaciones van últimas.
RADAR_QUERY_BUDGET = 150
# Las recomendaciones corren una vez por día, no tres: no alertan, así que
# verlas a la mañana alcanza.
RADAR_RECOMMENDATION_SLOTS = frozenset({"morning"})
RADAR_SLOT_LABELS = {"morning": "09:00", "afternoon": "16:00", "night": "22:30"}
RADAR_SCHEDULER_INTERVAL_SECONDS = max(5, int(os.getenv("CONSOLAS_RADAR_SCHEDULER_INTERVAL_SECONDS", "60")))
# Una corrida manual reciente y exitosa satisface el slot siguiente, igual que en
# Auction Watch: no se vuelve a escanear lo mismo minutos después.
RADAR_MANUAL_FRESHNESS_MINUTES = max(0, int(os.getenv("CONSOLAS_RADAR_MANUAL_FRESHNESS_MINUTES", "30")))
RADAR_RUN_KINDS = ("scheduled", "manual")
RADAR_RUN_STATUSES = ("running", "completed", "degraded", "failed")
RADAR_SLOT_STATES = ("fulfilled", "skipped")
RADAR_RUN_HISTORY_LIMIT = 20
# Decisiones del usuario sobre una publicación. Viven en tablas del radar: una
# decisión de compra nunca escribe estado de colección (eso es «Registrar compra»).
RADAR_DECISIONS = ("following", "dismissed", "snoozed", "purchased")
RADAR_DISMISS_REASONS = (
    "", "caro", "condicion", "region", "ya-lo-tengo", "no-me-interesa", "no-es-lo-que-busco", "dudoso"
)
RADAR_FEED_LIMIT = 10
# PRD §16: una baja del 8% en el total es un cambio material y merece avisar.
RADAR_MATERIAL_DROP = 0.08
# Cuánto vale «la abrí y tenía Buy It Now». Una publicación de eBay puede
# venderse en horas: pasado un día, la verificación es historia y hay que
# volver a mirarla. Un cambio de precio posterior también la vence.
RADAR_VERIFICATION_TTL_HOURS = 24
# Cuántas copias de un mismo título muestra una búsqueda de varios títulos, y
# sólo si la segunda aporta algo distinto (suelta frente a caja, otra región u
# otra edición). Catorce Tetris no son cobertura de una lista de deseados.
RADAR_MAX_COPIES_PER_TITLE = 2
# Cuántas coincidencias activas se leen para armar la lista de una búsqueda
# antes de diversificar. El límite de resultados se aplica después.
RADAR_INVENTORY_SCAN_LIMIT = 1000
# Lo que no cumple la definición de su objetivo (un lote sin cantidad, una copia
# con caja y sin manual) se ve en su búsqueda, aparte, pero no es una decisión.
RADAR_SECONDARY_SECTIONS = frozenset({"unconfirmed-quantity", "incomplete"})
# Entregas propias del radar (PRD §16). Asunto y destinatarios separados de
# Auction Watch; la infraestructura SMTP puede ser la misma.
# `alerts` es el que eligió el dueño (2026-11-01): *«el mail tiene que llegar
# solo cuando hay una oferta que rompa los ojos, no en cada corrida»*. Sin
# digest: lo que no es excepcional se ve en «Para mí» y no interrumpe.
RADAR_EMAIL_MODES = ("disabled", "alerts", "digest", "digest_and_alerts")
RADAR_OUTBOX_STATUSES = ("pending", "sending", "sent", "failed", "uncertain")
RADAR_OUTBOX_KINDS = ("digest", "alert")
# Una oportunidad excepcional no espera a la próxima corrida. Los umbrales son
# hechos medibles, no el score: ver `is_exceptional`.
RADAR_ALERT_RATIO = float(os.getenv("CONSOLAS_RADAR_ALERT_RATIO", "0.55"))
RADAR_ALERT_DROP = 0.20

_AUCTION_WATCH_DISMISSALS_LOCK = threading.RLock()
_AUCTION_WATCH_SNAPSHOT_LOCK = threading.RLock()
_AUCTION_WATCH_RUN_LOCK = threading.RLock()
_RADAR_LOCK = threading.RLock()
_RADAR_RUN_LOCK = threading.RLock()
# Un lock por búsqueda: el scheduler y un "Buscar ahora" no pueden escanear la
# misma búsqueda a la vez, ni siquiera durante la llamada de red.
_RADAR_SEARCH_LOCKS: dict[str, threading.Lock] = {}
# Alias historico: Chasing Games y Collection Radar comparten dominio y lock.
_CHASING_GAMES_LOCK = _RADAR_LOCK


RADAR_LISTING_KIND_LABELS = {
    "fixed_price": "Compra directa",
    "auction": "Subasta",
    "best_offer": "Acepta ofertas",
    "unknown": "",
}


# Los objetivos que se comparan contra pares de su misma completitud. Los lotes
# van por pieza y lo que no es ninguno de los cinco no tiene pares.
RADAR_PEERED_OBJECTIVES = frozenset({"game-loose", "game-cib", "console-loose", "console-cib"})
