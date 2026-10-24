"""Rule-declared adventure progression settings (GAL track P6).

A rule enables narrator-proposed node completion by declaring an
``adventure_progression`` block; without one the existing advisory-only
completion suggestions flow is unchanged (GM clicks stay the only authority).
The block is pure capability + balance declaration; gate evaluation and the
completion transaction remain the adventure runtime's invariants.

```json
"adventure_progression": {"auto_confirm_completion": true, "activation_narration": true}
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_FIELDS = frozenset({"auto_confirm_completion", "activation_narration"})


@dataclass(frozen=True)
class AdventureProgressionConfig:
    # The engine may authoritatively confirm narrator-proposed node
    # completions (validated through the standard completion transaction).
    auto_confirm_completion: bool
    # A confirmed activation generates one scene-transition narration.
    activation_narration: bool


def parse_adventure_progression_config(raw: Any) -> AdventureProgressionConfig | None:
    """``None`` when the rule declares nothing; a malformed block fails fast."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("adventure_progression must be an object")
    extra = sorted(set(raw) - _FIELDS)
    if extra:
        raise ValueError(f"adventure_progression has unknown field: {extra[0]!r}")
    values: dict[str, bool] = {}
    for field in sorted(_FIELDS):
        value = raw.get(field)
        if type(value) is not bool:
            raise ValueError(f"adventure_progression.{field} must be a boolean: {value!r}")
        values[field] = value
    return AdventureProgressionConfig(**values)
