"""Which feats contribute active capabilities, and what their names are.

Two small readings that the class-feature resolver needs once feats can grant
capabilities.  They live here rather than in ``resolver.py`` for the same reason
the Defensive Tactics helpers live in their own mixin: that file is inside the
1500-line size ratchet, and a task that adds sixty lines to it makes the next
one pay for both.

Both functions are pure: they take what they need instead of reaching for a
resolver, which also makes them testable without constructing a character.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.rulesets.dnd2024.features.feat_models import FeatDefinition


def owned_capability_feats(
    feat_refs: Any,
    definitions: Mapping[str, FeatDefinition],
) -> tuple[FeatDefinition, ...]:
    """The feats among ``feat_refs`` that actually grant a capability.

    A feat without capabilities is skipped rather than passed along: it would
    only enlarge the projection's ``owned_ids``, letting a
    ``requires_feature_id`` be satisfied by an id that grants nothing.
    """

    if not isinstance(feat_refs, list):
        return ()
    owners: list[FeatDefinition] = []
    for ref in feat_refs:
        if not isinstance(ref, str):
            continue
        definition = definitions.get(ref.removeprefix("feat:"))
        if definition is not None and definition.capabilities:
            owners.append(definition)
    return tuple(owners)


def capability_labels(owners: Iterable[FeatDefinition]) -> dict[str, str]:
    """Capability display names declared by the feats that grant them.

    Read from the parsed declaration, not from ``bundle.get("feat", ...)``: feats
    live in two layouts (one entity per file, and rows inside the advancement
    catalog), and only the parsed model sees both.
    """

    labels: dict[str, str] = {}
    for definition in owners:
        labels.update(definition.labels)
    return labels
