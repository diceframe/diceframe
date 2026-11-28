"""Encode/decode for persisted Scene and Token documents (B1).

Both directions validate: a persisted dict that this contract cannot interpret
is rejected rather than partially decoded. Round-trips are stable because the
validators return a fresh normalized structure and are idempotent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.tactical_scene.contract import (
    TacticalSceneDocument,
    TacticalSceneValidationReport,
    TokenDocument,
    normalize_tactical_scene_payload,
    normalize_token_payload,
)


def decode_tactical_scene_document(raw: Mapping[str, Any]) -> TacticalSceneDocument:
    """Decode one persisted scene dict into a validated TacticalSceneDocument."""
    return normalize_tactical_scene_payload(raw)[0]


def encode_tactical_scene_document(document: Mapping[str, Any]) -> TacticalSceneDocument:
    """Encode a TacticalSceneDocument into the persisted dict shape.

    Re-validates so callers cannot persist a partially normalized document.
    """
    return normalize_tactical_scene_payload(document)[0]


def decode_token_document(raw: Mapping[str, Any]) -> TokenDocument:
    """Decode one persisted token dict into a validated TokenDocument."""
    return normalize_token_payload(raw)[0]


def encode_token_document(document: Mapping[str, Any]) -> TokenDocument:
    """Encode a TokenDocument into the persisted dict shape."""
    return normalize_token_payload(document)[0]


def decode_tactical_scene_with_report(raw: Mapping[str, Any]) -> tuple[TacticalSceneDocument, TacticalSceneValidationReport]:
    """Decode a scene and expose unknown fields, degraded values and missing refs."""
    return normalize_tactical_scene_payload(raw)


__all__ = [
    "decode_tactical_scene_document",
    "decode_tactical_scene_with_report",
    "decode_token_document",
    "encode_tactical_scene_document",
    "encode_token_document",
]