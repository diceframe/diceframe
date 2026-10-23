"""Composition root for first-party gameplay modes.

The only module allowed to import concrete gameplay packages.
"""

from __future__ import annotations

from src.gameplays import trpg
from src.gameplays.contracts import GameplayRegistry
from src.gameplays.gal import GAL


def build_default_gameplay_registry() -> GameplayRegistry:
    return GameplayRegistry([trpg.FREE, trpg.ADVENTURE, GAL])


DEFAULT_GAMEPLAYS = build_default_gameplay_registry()
