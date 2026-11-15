"""Uploaded media: data URL decoding and storage."""

from __future__ import annotations

import base64
import mimetypes
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import Any

try:
    from .clock import utc_now
    from .config import AppConfig
    from .database import connect_db
    from .errors import ApiError
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from database import connect_db
    from errors import ApiError


def decode_data_url(data_url: str) -> tuple[str, bytes]:
    prefix, separator, encoded = data_url.partition(",")
    if not separator or ";base64" not in prefix:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Expected base64 data URL")
    mime_type = prefix.removeprefix("data:").split(";", 1)[0] or "application/octet-stream"
    try:
        return mime_type, base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid base64 media payload") from exc


def media_extension(mime_type: str, original_file_name: str = "") -> str:
    original_suffix = Path(original_file_name).suffix.lower()
    if original_suffix and len(original_suffix) <= 8:
        return original_suffix
    return mimetypes.guess_extension(mime_type) or ".bin"


def save_media(config: AppConfig, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
    data_url = str(payload.get("dataUrl") or "")
    original_file_name = str(payload.get("fileName") or payload.get("originalFileName") or "")
    mime_type, data = decode_data_url(data_url)
    allowed_mime = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    suffix = Path(original_file_name).suffix.lower()
    if mime_type not in allowed_mime or suffix not in allowed_suffixes:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Only JPG, PNG, WEBP or GIF images are accepted")
    if len(data) > 8 * 1024 * 1024:
        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Image exceeds the 8 MB limit")
    media_id = f"media_{uuid.uuid4().hex}"
    file_name = f"{media_id}{media_extension(mime_type, original_file_name)}"
    file_path = config.media_dir / file_name
    file_path.write_bytes(data)
    created_at = utc_now()
    with connect_db(config) as conn:
        conn.execute(
            """
            INSERT INTO media (
              id, original_file_name, mime_type, file_name, file_path, size_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (media_id, original_file_name, mime_type, file_name, str(file_path), len(data), created_at),
        )
    return {
        "id": media_id,
        "url": f"./media/{file_name}",
        "fileName": file_name,
        "mimeType": mime_type,
        "sizeBytes": len(data),
        "createdAt": created_at,
    }
