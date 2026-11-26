"""Bounded projection of a player-authored narrative profile for GM context.

``ruleset_character.profile`` is the narrative bag a player edits in the
advanced character center (``src/webui/services/ruleset_characters.py`` owns the
field set and the write boundary).  Mechanics never read it, but the AI GM has
to know *who* the character is to portray them, so both the per-round LLM state
(``build_llm_view``) and the opening narration take the same budgeted excerpt.

Budgets exist because the excerpt shares the state budget with every character
sheet: an unbounded biography would push later players out of a truncated state
JSON.  ``notes`` is deliberately absent — the UI labels it "personal notes"
(仅供自己记录), so it never leaves the player's own sheet.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Field order and per-field character budgets.  Order is priority: pronouns are
#: cheap and fix gendered narration, personality/backstory say the most, the rest
#: fill what is left.
FIELD_LIMITS: tuple[tuple[str, int], ...] = (
    ("pronouns", 24),
    ("personality", 120),
    ("backstory", 160),
    ("appearance", 100),
    ("ideals", 60),
    ("bonds", 60),
    ("flaws", 60),
)

#: Total characters one character may contribute to the GM context.
TOTAL_LIMIT = 400


def narrative_profile_excerpt(
    character: Mapping[str, Any], *, total_limit: int = TOTAL_LIMIT,
) -> dict[str, str]:
    """The bounded narrative excerpt of one character sheet.

    Tolerant by design: a missing, malformed or partly foreign ``profile`` bag
    yields whatever fields are well-formed, never a failure.  Nothing here is
    authoritative state — it is presentation text for the narrator.
    """

    if not isinstance(character, Mapping):
        return {}
    canonical = character.get("ruleset_character")
    if not isinstance(canonical, Mapping):
        return {}
    return profile_excerpt(canonical.get("profile"), total_limit=total_limit)


def profile_excerpt(profile: Any, *, total_limit: int = TOTAL_LIMIT) -> dict[str, str]:
    """The same excerpt from a bare ``profile`` bag."""

    if not isinstance(profile, Mapping):
        return {}
    excerpt: dict[str, str] = {}
    budget = max(0, int(total_limit))
    for key, field_limit in FIELD_LIMITS:
        if budget <= 0:
            break
        raw = profile.get(key)
        if not isinstance(raw, str):
            continue
        text = " ".join(raw.split())
        if not text:
            continue
        limit = min(field_limit, budget)
        if len(text) > limit:
            text = text[: max(1, limit - 1)].rstrip() + "…"
        excerpt[key] = text
        budget -= len(text)
    return excerpt