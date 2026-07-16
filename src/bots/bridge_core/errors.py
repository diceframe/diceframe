"""Shared bridge exceptions."""

from __future__ import annotations


class DiceFrameBridgeError(RuntimeError):
    """Base error for platform bridge failures."""


class DiceFrameHTTPError(DiceFrameBridgeError):
    """Raised when the DiceFrame HTTP API returns an error payload."""
