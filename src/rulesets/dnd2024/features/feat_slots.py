"""Rules about what a feat-granting slot may offer.

``models.py`` was already at 1492 lines when these arrived; keeping them there
would have pushed a file that is trying to stay under the 1500-line review
signal over it.  Neither function parses a declaration -- one reads the catalog
for cross-checks, the other decides a slot's options after parsing -- so they
live beside the model rather than inside it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.rulesets.bundle import LoadedRulesetBundle

    from .models import ClassFeatureDefinition


def advancement_feat_categories(bundle: LoadedRulesetBundle) -> dict[str, str]:
    """Every declared advancement feat's id and category, for cross-catalog checks."""

    catalog = bundle.get("advancement_feat_catalog", "srd_advancement_feats")
    feats = catalog.get("feats") if isinstance(catalog, Mapping) else None
    if not isinstance(feats, Mapping):
        return {}
    return {
        str(feat_id): str(feat.get("category") or "")
        for feat_id, feat in feats.items()
        if isinstance(feat, Mapping)
    }


def fill_advancement_feat_options(
    features: list[ClassFeatureDefinition], bundle: LoadedRulesetBundle,
) -> list[ClassFeatureDefinition]:
    """Derive a feat slot's whitelist from what its options actually do.

    A slot used to name its options by hand.  FD6a wrote
    ``["feat:archery", "feat:defense"]`` into four JSON entries and dropped
    Two-Weapon Fighting -- a style the engine already implemented end to end.
    The slot went from selectable to rejected, and the only test looking at it
    asserted the omission.

    "May this be selected" is the same question as "does it do anything", and
    the feat declaration already answers the second one.  Keeping a second,
    hand-maintained list is how the omission happened.

    Only ``feature_choice`` slots are filled.  Ledger slots (ASI / epic boon)
    deliberately carry no whitelist: their answer is stored in the advancement
    history, and an empty list means the whole category.
    """

    # Local imports: ``feat_models`` imports ``models`` for FeatureChoice, so a
    # module-level import here would close a cycle.  Neither module imports this
    # one, so the dependency stays one-directional at call time.
    from .feat_models import FeatDefinitionError, load_feat_definitions
    from .parameter_registry import FeatParameterError

    try:
        definitions = load_feat_definitions(bundle)
    except (FeatDefinitionError, FeatParameterError):
        # A broken declaration is not this function's finding to report.  The
        # catalogs that own feats raise it with their own error type (the
        # advancement engine raises ProgressionCatalogError), and deriving a
        # whitelist must not replace that contract with an earlier exception.
        return features
    result: list[ClassFeatureDefinition] = []
    for definition in features:
        choice = definition.choice
        if (
            choice is None
            or choice.kind != "advancement_feat"
            or choice.handled_by != "feature_choice"
        ):
            result.append(definition)
            continue
        if choice.enabled_options:
            # 显式写出的清单保留，并照常交给 ``_validate_feature_choices``
            # 校验；推导只负责"空清单"。写死清单曾漏掉 Two-Weapon Fighting，
            # 所以守卫测试要求这些槽保持为空——两层一起拦。
            result.append(definition)
            continue
        options = tuple(
            f"feat:{feat_id}"
            for feat_id, feat in sorted(definitions.items())
            if feat.category == choice.category and feat.has_mechanical_effect
        )
        result.append(replace(definition, choice=replace(choice, enabled_options=options)))
    return result


__all__ = ["advancement_feat_categories", "fill_advancement_feat_options"]
