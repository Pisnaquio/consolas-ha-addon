"""Data directories and SQLite connections."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

try:
    from .config import AppConfig
except ImportError:  # Direct execution: `python3 server/app.py`.
    from config import AppConfig


def ensure_directories(config: AppConfig) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.media_dir.mkdir(parents=True, exist_ok=True)
    config.auction_watch_dir.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect_db(config: AppConfig) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
