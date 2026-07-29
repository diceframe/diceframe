"""Shared bridge exceptions."""

from __future__ import annotations


class DiceFrameBridgeError(RuntimeError):
    """Base error for platform bridge failures."""


class DiceFrameHTTPError(DiceFrameBridgeError):
    """Raised when the DiceFrame HTTP API returns an error payload."""

    def __init__(self, message: str, *, status: int | None = None, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = str(code or "")
