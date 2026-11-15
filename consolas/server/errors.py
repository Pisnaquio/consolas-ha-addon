"""API error type shared by every route."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any


class ApiError(Exception):
    def __init__(self, status: int | HTTPStatus, message: str, details: Any | None = None) -> None:
        self.status = int(status)
        self.message = message
        self.details = details
        super().__init__(message)
