from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message
        self.detail = detail or {}

    def body(self) -> dict[str, Any]:
        return {"error": self.error, "message": self.message, "detail": self.detail}
