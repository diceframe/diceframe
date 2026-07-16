"""Compatibility exports for the shared DiceFrame bridge HTTP client."""

from __future__ import annotations

from src.bots.bridge_core.client import DiceFrameClient, build_join_link

TRPGBotAPI = DiceFrameClient
_build_join_link = build_join_link
