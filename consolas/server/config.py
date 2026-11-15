"""Runtime configuration read from the environment (AppConfig)."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from .constants import (
        AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS,
        DATABASE_NAME,
        DEFAULT_BODY_LIMIT,
        DEFAULT_DATA_DIR,
        DEFAULT_PORT,
        DEFAULT_STATIC_DIR,
        EBAY_ENVIRONMENTS,
        RADAR_EMAIL_MODES,
        RADAR_QUERY_BUDGET,
    )
except ImportError:  # Direct execution: `python3 server/app.py`.
    from constants import (
        AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS,
        DATABASE_NAME,
        DEFAULT_BODY_LIMIT,
        DEFAULT_DATA_DIR,
        DEFAULT_PORT,
        DEFAULT_STATIC_DIR,
        EBAY_ENVIRONMENTS,
        RADAR_EMAIL_MODES,
        RADAR_QUERY_BUDGET,
    )


class AppConfig:
    def __init__(self) -> None:
        self.host = os.getenv("CONSOLAS_HOST", "0.0.0.0")
        self.port = int(os.getenv("CONSOLAS_PORT", str(DEFAULT_PORT)))
        self.data_dir = Path(os.getenv("CONSOLAS_DATA_DIR", DEFAULT_DATA_DIR))
        self.static_dir = Path(os.getenv("CONSOLAS_STATIC_DIR", DEFAULT_STATIC_DIR))
        self.media_dir = self.data_dir / "media"
        self.auction_watch_dir = self.data_dir / "auction-watch"
        self.auction_watch_stale_after_seconds = max(
            60,
            int(
                os.getenv(
                    "CONSOLAS_AUCTION_WATCH_STALE_AFTER_SECONDS",
                    str(AUCTION_WATCH_DEFAULT_STALE_AFTER_SECONDS),
                )
            ),
        )
        self.db_path = self.data_dir / DATABASE_NAME
        self.max_body_size = int(os.getenv("CONSOLAS_MAX_BODY_SIZE", str(DEFAULT_BODY_LIMIT)))
        # Leer el detalle de las candidatas (`getItem`). Se puede apagar, pero
        # apagado ninguna publicación de eBay llega a ganga ni a alerta: sin
        # descripción no se sabe qué falta ni qué está roto.
        self.radar_item_details = os.getenv("CONSOLAS_RADAR_ITEM_DETAILS", "1").strip().lower() not in {
            "0", "false", "no", "off"
        }
        # Cuántas consultas a eBay puede hacer una corrida. Una búsqueda de
        # varios títulos hace hasta 49, así que contar búsquedas no alcanza.
        try:
            self.radar_query_budget = max(1, int(os.getenv("CONSOLAS_RADAR_QUERY_BUDGET", str(RADAR_QUERY_BUDGET))))
        except ValueError:
            self.radar_query_budget = RADAR_QUERY_BUDGET
        self.ebay_client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
        self.ebay_client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
        requested_ebay_environment = os.getenv("EBAY_ENVIRONMENT", "sandbox").strip().lower()
        self.ebay_environment = requested_ebay_environment if requested_ebay_environment in EBAY_ENVIRONMENTS else "sandbox"

        # Entregas del radar. El destinatario y el modo son propios; el transporte
        # SMTP cae al de Auction Watch cuando no se configura uno aparte, porque
        # es la misma casilla de salida y no tiene sentido pedirla dos veces.
        requested_mode = os.getenv("RADAR_EMAIL_MODE", "disabled").strip().lower()
        self.radar_email_mode = requested_mode if requested_mode in RADAR_EMAIL_MODES else "disabled"
        self.radar_email_from = os.getenv("RADAR_EMAIL_FROM", "").strip()
        self.radar_email_to = os.getenv("RADAR_EMAIL_TO", "").strip()
        self.radar_smtp_host = (os.getenv("RADAR_SMTP_HOST") or os.getenv("AUCTION_WATCH_SMTP_HOST") or "").strip()
        self.radar_smtp_port = int(os.getenv("RADAR_SMTP_PORT") or os.getenv("AUCTION_WATCH_SMTP_PORT") or 587)
        self.radar_smtp_username = (
            os.getenv("RADAR_SMTP_USERNAME") or os.getenv("AUCTION_WATCH_SMTP_USERNAME") or ""
        ).strip()
        self.radar_smtp_password = (
            os.getenv("RADAR_SMTP_PASSWORD") or os.getenv("AUCTION_WATCH_SMTP_PASSWORD") or ""
        )
        self.radar_smtp_starttls = (os.getenv("RADAR_SMTP_STARTTLS", "true").strip().lower()
                                    not in {"0", "false", "no", "off"})
