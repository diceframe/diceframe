"""Rule-declared affection settings (GAL track P3).

A rule enables narrator-proposed affection by declaring an ``affection``
block; without one the AFFECTION tag is neither documented to the model nor
settled.  The block holds balance only; the score range and subject limits
are the affection module's invariants.

```json
"affection": {"max_delta_per_round": 5}
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_DELTA_PER_ROUND_LIMIT = 20
_FIELDS = frozenset({"max_delta_per_round"})


@dataclass(frozen=True)
class AffectionConfig:
    # Largest total change one subject may receive in one settled round.
    max_delta_per_round: int


def parse_affection_config(raw: Any) -> AffectionConfig | None:
    """``None`` when the rule declares nothing; a malformed block fails fast."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("affection must be an object")
    extra = sorted(set(raw) - _FIELDS)
    if extra:
        raise ValueError(f"affection has unknown field: {extra[0]!r}")
    cap = raw.get("max_delta_per_round")
    if type(cap) is not int or not 1 <= cap <= MAX_DELTA_PER_ROUND_LIMIT:
        raise ValueError(
            f"affection.max_delta_per_round must be an integer from 1 to {MAX_DELTA_PER_ROUND_LIMIT}: {cap!r}"
        )
    return AffectionConfig(max_delta_per_round=cap)
