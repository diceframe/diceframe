"""Where each owned feat came from, and what a second acquisition means.

``features.feat_refs`` is a flat ``list[str]`` and stays that way: it is a
persisted field, and the sources are all still present in the sheet
(``build.background_ref``, ``build.species_feat_refs``,
``build.class_feature_feat_choices``, ``features.feature_choices`` and
``progression.history[].diff.advancement_feat_ref``).  So provenance is a
derived view -- a pure function over those fields -- not a new stored shape.

Two things live here:

* ``feat_provenance`` answers "which source granted this ref";
* ``acquire`` is the one place that decides whether a second grant is legal
  (``repeatable`` gates it, and one source can never grant the same feat twice).

Derivation deliberately does not call ``acquire``: it runs on every replay and
audit, and raising there would make an old sheet unopenable.  The caller that
*acquires* a feat is the one that has to refuse.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle


class FeatAcquisitionError(ValueError):
    """Raised when a feat would be acquired twice from a source that cannot."""


#: Where a feat can come from.  ``unknown`` exists only for refs found in
#: ``features.feat_refs`` whose source the sheet no longer records (legacy
#: saves, hand-edited sheets): naming it keeps those sheets openable instead of
#: calling them corrupt.
SOURCE_KINDS = frozenset({
    "background", "species", "class_feature", "advancement", "unknown",
})


@dataclass(frozen=True, slots=True)
class FeatProvenance:
    """One grant of one feat."""

    ref: str
    source_kind: str
    source_ref: str
    level: int | None = None

    def __post_init__(self) -> None:
        if self.source_kind not in SOURCE_KINDS:
            raise FeatAcquisitionError(
                f"{self.ref} has an unknown source kind: {self.source_kind!r}"
            )

    @property
    def source(self) -> str:
        return f"{self.source_kind}:{self.source_ref}" if self.source_ref else self.source_kind


def feat_provenance(
    bundle: LoadedRulesetBundle,
    character: Mapping[str, Any] | None,
    *,
    class_slots: Collection[str] | None = None,
) -> tuple[FeatProvenance, ...]:
    """Every grant the sheet still records, in a stable order.

    ``character`` may be a craft ``draft`` (flat fields) or a derived sheet
    (``identity`` / ``build`` / ``features`` / ``progression``); ``_views``
    normalises the two shapes once, so no caller has to know which one it has.

    The order is the acquisition order a character actually goes through:
    background, species, level-1 class slot, later class slots, advancement.
    ``unknown`` comes last because it is a leftover, not a source.
    """

    background_ref, species_ref, class_ref, build_map, feature_map, progression_map = (
        _views(character)
    )

    items: list[FeatProvenance] = []

    if background_ref:
        background = bundle.get("background", _ref_id(background_ref))
        feat_ref = _text(background.get("feat_ref")) if isinstance(background, Mapping) else ""
        if feat_ref:
            items.append(FeatProvenance(feat_ref, "background", background_ref))

    species_refs = build_map.get("species_feat_refs")
    if species_ref and isinstance(species_refs, list):
        for raw in species_refs:
            ref = _text(raw)
            if ref:
                items.append(FeatProvenance(ref, "species", species_ref))

    level_one = build_map.get("class_feature_feat_choices")
    if isinstance(level_one, Mapping):
        # Sorted so the order does not depend on dict insertion, which differs
        # between a draft, a decoded sheet and a hand-written fixture.
        for feature_id, raw in sorted(level_one.items(), key=lambda pair: str(pair[0])):
            if class_slots is not None and str(feature_id) not in class_slots:
                # A craft submitted for a slot this class does not grant is not
                # a grant; validation rejects it, and an old sheet must not gain
                # a feat it never legitimately had.
                continue
            ref = _text(raw)
            if ref.startswith("feat:"):
                items.append(FeatProvenance(ref, "class_feature", class_ref, 1))

    records = feature_map.get("feature_choices")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, Mapping):
                continue
            owner = _text(record.get("owner"))
            level = _optional_int(record.get("granted_level"))
            selected = record.get("selected_refs")
            if not isinstance(selected, list):
                continue
            for raw in selected:
                ref = _text(raw)
                if ref.startswith("feat:"):
                    items.append(FeatProvenance(ref, "class_feature", owner, level))

    history = progression_map.get("history")
    if isinstance(history, list):
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            diff = entry.get("diff")
            diff = diff if isinstance(diff, Mapping) else {}
            # Advancement feats are recorded on the diff, not in choices:
            # ``advancement.py`` writes ``diff.advancement_feat_ref``.
            ref = _text(diff.get("advancement_feat_ref"))
            if not ref:
                continue
            level = _optional_int(entry.get("to_level"))
            source_ref = f"level:{level}" if level is not None else ""
            items.append(FeatProvenance(ref, "advancement", source_ref, level))

    known = {item.ref for item in items}
    owned = feature_map.get("feat_refs")
    if isinstance(owned, list):
        for raw in owned:
            ref = _text(raw)
            if ref and ref not in known:
                items.append(FeatProvenance(ref, "unknown", ""))

    # Grants are returned one per acquisition, not one per ref: a ref granted
    # twice must stay visible as two items, or the duplicate check downstream
    # has nothing to see.  ``owned_feat_refs`` is what collapses them.
    return tuple(items)


def owned_feat_refs(provenance: Iterable[FeatProvenance]) -> list[str]:
    """The flat ref list a sheet stores, in acquisition order.

    This is the replacement for ``dict.fromkeys`` over three concatenated
    lists: same result, but the list is derived from grants that carry their
    source, so a duplicate can be explained instead of silently collapsed.
    """

    refs: list[str] = []
    seen: set[str] = set()
    for item in provenance:
        if item.ref in seen:
            continue
        seen.add(item.ref)
        refs.append(item.ref)
    return refs


def acquire(
    existing: Sequence[FeatProvenance],
    ref: str,
    source: FeatProvenance,
    *,
    repeatable: bool,
) -> tuple[FeatProvenance, ...]:
    """Add one grant, or explain why it cannot be added.

    ``repeatable`` comes from the feat declaration; everything else is decided
    here so the rule has one implementation instead of an ``if ref not in
    owned`` at each write-back site.
    """

    if not ref.startswith("feat:"):
        raise FeatAcquisitionError(f"{ref} is not a feat reference")
    if source.ref != ref:
        raise FeatAcquisitionError(
            f"source {source.ref} does not grant {ref}"
        )
    for item in existing:
        if item.ref != ref:
            continue
        if not repeatable:
            raise FeatAcquisitionError(
                f"{ref} is not repeatable but is already owned via {item.source}"
                f" (second source: {source.source})"
            )
        if item.source_kind == source.source_kind and item.source_ref == source.source_ref:
            raise FeatAcquisitionError(
                f"{ref} was already acquired from {source.source}"
            )
    return (*existing, source)


def _views(
    character: Mapping[str, Any] | None,
) -> tuple[
    str, str, str,
    Mapping[str, Any], Mapping[str, Any], Mapping[str, Any],
]:
    """Normalise a craft draft and a derived sheet to the same six fields.

    A draft is flat (``background_ref`` at the top); a derived sheet keeps the
    same facts in ``identity`` and ``build.class_levels``.  Both are read here
    so the extraction above has one shape to work with.
    """

    sheet = character if isinstance(character, Mapping) else {}
    identity = sheet.get("identity")
    build = sheet.get("build")
    features_value = sheet.get("features")
    progression_value = sheet.get("progression")
    features: Mapping[str, Any] = (
        features_value if isinstance(features_value, Mapping) else {}
    )
    progression: Mapping[str, Any] = (
        progression_value if isinstance(progression_value, Mapping) else {}
    )
    if isinstance(identity, Mapping) and isinstance(build, Mapping):
        levels = build.get("class_levels")
        first = levels[0] if isinstance(levels, list) and levels else {}
        class_ref = _text(first.get("class_ref")) if isinstance(first, Mapping) else ""
        return (
            _text(identity.get("background_ref")),
            _text(identity.get("species_ref")),
            class_ref,
            build,
            features,
            progression,
        )
    return (
        _text(sheet.get("background_ref")),
        _text(sheet.get("species_ref")),
        _text(sheet.get("class_ref")),
        sheet,
        {},
        {},
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _ref_id(ref: str) -> str:
    return ref.split(":", 1)[1] if ":" in ref else ref


__all__ = [
    "SOURCE_KINDS",
    "FeatAcquisitionError",
    "FeatProvenance",
    "acquire",
    "feat_provenance",
    "owned_feat_refs",
]
