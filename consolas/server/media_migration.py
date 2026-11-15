"""Moves bootstrap media referenced by the state into the persistent media store."""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
from pathlib import Path
from typing import Any

try:
    from .clock import utc_now
    from .config import AppConfig
    from .database import connect_db
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from database import connect_db


def bootstrap_media_source_path(config: AppConfig, media_ref: str) -> Path | None:
    relative = str(media_ref or "").strip().removeprefix("./")
    if not relative.startswith("runtime/media/"):
        return None
    candidate = (config.static_dir / relative).resolve()
    root = config.static_dir.resolve()
    if root != candidate and root not in candidate.parents:
        return None
    return candidate


def import_bootstrap_media_file(config: AppConfig, source_path: Path, original_ref: str) -> str:
    relative = str(original_ref or "").strip().removeprefix("./")
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:16]
    suffix = source_path.suffix.lower() if source_path.suffix else ".bin"
    media_id = f"bootstrap_{digest}"
    file_name = f"{media_id}{suffix}"
    target_path = config.media_dir / file_name

    if not target_path.exists():
        shutil.copy2(source_path, target_path)

    created_at = utc_now()
    with connect_db(config) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO media (
              id, original_file_name, mime_type, file_name, file_path, size_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                media_id,
                source_path.name,
                mimetypes.guess_type(str(source_path))[0] or "application/octet-stream",
                file_name,
                str(target_path),
                target_path.stat().st_size,
                created_at,
            ),
        )

    return f"./media/{file_name}"


def migrate_runtime_media_references(
    config: AppConfig,
    value: Any,
    cache: dict[str, str] | None = None,
) -> tuple[Any, bool]:
    replacements = cache if cache is not None else {}

    if isinstance(value, str):
        raw = value.strip()
        if not raw.startswith("./runtime/media/") and not raw.startswith("runtime/media/"):
            return value, False
        if raw in replacements:
            return replacements[raw], replacements[raw] != value
        source_path = bootstrap_media_source_path(config, raw)
        if not source_path or not source_path.exists() or not source_path.is_file():
            return value, False
        migrated_url = import_bootstrap_media_file(config, source_path, raw)
        replacements[raw] = migrated_url
        return migrated_url, migrated_url != value

    if isinstance(value, list):
        changed = False
        next_list = []
        for item in value:
            migrated_item, item_changed = migrate_runtime_media_references(config, item, replacements)
            next_list.append(migrated_item)
            changed = changed or item_changed
        return next_list, changed

    if isinstance(value, dict):
        changed = False
        next_dict: dict[str, Any] = {}
        for key, item in value.items():
            migrated_item, item_changed = migrate_runtime_media_references(config, item, replacements)
            next_dict[key] = migrated_item
            changed = changed or item_changed
        return next_dict, changed

    return value, False
