"""Request body parsing and static path resolution."""

from __future__ import annotations

import json
import posixpath
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

try:
    from .config import AppConfig
    from .errors import ApiError
except ImportError:  # Direct execution: `python3 server/app.py`.
    from config import AppConfig
    from errors import ApiError


def read_json_body(handler: BaseHTTPRequestHandler, config: AppConfig) -> Any:
    transfer_encoding = str(handler.headers.get("Transfer-Encoding") or "").lower()
    if "chunked" in transfer_encoding:
        chunks: list[bytes] = []
        total_size = 0
        while True:
            size_line = handler.rfile.readline()
            if not size_line:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Incomplete chunked request body")
            try:
                chunk_size = int(size_line.split(b";", 1)[0].strip(), 16)
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid chunked request body") from exc
            if chunk_size == 0:
                # Consume optional trailer headers so the next request begins at a clean boundary.
                while True:
                    trailer = handler.rfile.readline()
                    if trailer in {b"", b"\r\n", b"\n"}:
                        break
                break
            total_size += chunk_size
            if total_size > config.max_body_size:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
            chunk = handler.rfile.read(chunk_size)
            if len(chunk) != chunk_size or handler.rfile.read(2) != b"\r\n":
                raise ApiError(HTTPStatus.BAD_REQUEST, "Incomplete chunked request body")
            chunks.append(chunk)
        raw = b"".join(chunks)
    else:
        raw_length = handler.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length") from exc
        if length > config.max_body_size:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid JSON body") from exc


def safe_static_path(static_dir: Path, request_path: str) -> Path:
    clean_path = posixpath.normpath(urllib.parse.unquote(request_path.split("?", 1)[0]))
    if clean_path in ("", ".", "/"):
        clean_path = "/index.html"
    candidate = (static_dir / clean_path.lstrip("/")).resolve()
    root = static_dir.resolve()
    if root != candidate and root not in candidate.parents:
        raise ApiError(HTTPStatus.FORBIDDEN, "Forbidden path")
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate
