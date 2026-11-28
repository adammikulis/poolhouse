"""The one refusal of the phone API: an HTTP status, a code the app reads and a sentence the person reads."""

from __future__ import annotations

__all__ = ["ApiError"]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
