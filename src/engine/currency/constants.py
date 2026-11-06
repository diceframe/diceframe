"""Shared technical bounds for canonical currency amounts."""

from __future__ import annotations


# The canonical amount may be a minor denomination (for example cents), so
# this cap stays well above ordinary display amounts while bounding malformed
# model or client payloads.
MAX_ECONOMY_AMOUNT = 10_000_000
