"""Feature-granted facts: languages, training categories, check modifiers.

Three queries with the same shape -- walk the features that are in force, read
one declared key, keep the source -- so they live together instead of growing
the resolver.  Nothing here knows a feature name: the declaration says what to
grant, this only collects it.

``saving_throw_proficiency_sources`` stays on the resolver because it predates
this module and has callers that pass a resolved ownership list.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def fixed_sources(
    definitions: Iterable[Any], key: str,
) -> dict[str, str]:
    """``{value: feature_id}`` for one string-list parameter.

    Sources travel with the grant because a merged ref list cannot say who put
    an entry there, and an audit that cannot tell them apart cannot explain it
    either.
    """

    sources: dict[str, str] = {}
    for definition in definitions:
        declared = definition.parameters.get(key)
        if not isinstance(declared, list):
            continue
        for value in declared:
            if isinstance(value, str) and value and value not in sources:
                sources[value] = definition.id
    return sources


def check_modifiers(
    definitions: Iterable[Any], surface: str,
) -> list[dict[str, Any]]:
    """Declared modifiers for one surface, in declaration order.

    Validity is the loader's job; this only selects, so the combat side stays
    free of feature names.  Each row carries its declaring feature id so an
    event can explain where a bonus came from.
    """

    rows: list[dict[str, Any]] = []
    for definition in definitions:
        for entry in definition.check_modifiers:
            if str(entry.get("surface") or "") == surface:
                rows.append({**entry, "feature_id": definition.id})
    return rows


def in_force(
    definition: Any, chosen_option: Mapping[str, str],
) -> bool:
    """Whether a feature is in force, not merely owned by level.

    An ``option_of`` branch is available from its level and active only once
    picked; otherwise Divine Order would grant a Cleric Heavy armor *and* the
    Thaumaturge bonus at once.  ``chosen_option`` maps a parent feature id to
    the option id the character committed to.
    """

    parent = definition.option_of
    if not parent:
        return True
    return chosen_option.get(parent, "") == definition.id
