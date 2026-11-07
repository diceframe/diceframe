"""S0-B: class feature choices, Champion Additional Fighting Style, Defense.

The slice is exercised end to end -- declaration, preview, apply, persisted
ownership, Resolver projection, advancement history replay, armor class, and
live equipment reconciliation -- because "a feat_ref can be submitted" is not
the same thing as "the choice actually works".

Only ``feat:defense`` has an implemented effect.  The other three Fighting
Style feats are projected, marked ``effect_not_implemented`` and rejected, so
a selection that grants nothing can never look finished.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.rulesets.dnd2024.character.derivation import derive_armor_class
from src.rulesets.dnd2024.features import (
    ClassFeatureError,
    Dnd2024ClassFeatureResolver,
)
from src.rulesets.dnd2024.features.models import Dnd2024ClassFeatureCatalog
from src.rulesets.dnd2024.progression import ProgressionCatalogError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import advancement_choices
from test_dnd2024_live_equipment_reconciliation import _Game

OWNER = "subclass:champion"
FEATURE_ID = "additional_fighting_style"
CHOICE_ID = "fighting_style_feat"
DEFENSE = "feat:defense"
ARCHERY = "feat:archery"
# 本切片只实现 Defense；其余三项必须保持"可见但不可提交"。
UNIMPLEMENTED_STYLES = (
    "feat:archery",
    "feat:great_weapon_fighting",
    "feat:two_weapon_fighting",
)
BASE_CHOICE = {
    "id": CHOICE_ID,
    "kind": "advancement_feat",
    "category": "fighting_style",
    "count": 1,
    "enabled_options": [DEFENSE],
}


# --------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------

def _runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _draft(runtime: Dnd2024Runtime, *, fighting_style: str) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    draft = {**preset["draft"], "locale": "en", "name": "Champion"}
    draft["class_feature_feat_choices"] = {"fighting_style": fighting_style}
    return draft


def _sheet(
    runtime: Dnd2024Runtime, level: int, *, fighting_style: str = ARCHERY,
) -> dict:
    """Advance a real Champion through the real level-up path."""

    sheet = runtime.finalize_character(None, _draft(runtime, fighting_style=fighting_style))
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        asi = next(
            (
                requirement for requirement in preview["requirements"]
                if requirement["id"] == "ability_score_increases"
            ),
            None,
        )
        if asi is not None:
            total = int(asi.get("total", 2))
            abilities = sheet["ruleset_character"]["abilities"]
            allowed = asi.get("allowed", "any")
            pool = list(abilities) if allowed == "any" else list(allowed)
            ability = next(key for key in pool if abilities[key] + total <= 20)
            picks["ability_score_increases"] = {ability: total}
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _submission(
    ref: str = DEFENSE, **overrides: object,
) -> dict:
    record = {
        "owner": OWNER,
        "feature_id": FEATURE_ID,
        "choice_id": CHOICE_ID,
        "selected_refs": [ref],
        **overrides,
    }
    return {"feature_choices": [record]}


def _choice_definition(
    runtime: Dnd2024Runtime, locale: str = "en",
):
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(locale))
    return resolver, resolver.catalog.features_by_key[(OWNER, FEATURE_ID)]


def _catalog_with_choice(runtime: Dnd2024Runtime, mutate) -> Dnd2024ClassFeatureCatalog:
    """Rebuild the catalog with one mutated declaration, to prove it fails."""

    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    raw = entities["class_feature_catalog"]["srd_class_features"]
    feature = next(
        item for item in raw["classes"]["fighter"]["features"]
        if item["id"] == FEATURE_ID
    )
    mutate(feature)
    return Dnd2024ClassFeatureCatalog.from_bundle(replace(bundle, entities=entities))


def _resolver_with_choice(runtime: Dnd2024Runtime, mutate) -> Dnd2024ClassFeatureResolver:
    """Rebuild the whole Resolver, so cross-catalog checks also run."""

    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    raw = entities["class_feature_catalog"]["srd_class_features"]
    feature = next(
        item for item in raw["classes"]["fighter"]["features"]
        if item["id"] == FEATURE_ID
    )
    mutate(feature)
    return Dnd2024ClassFeatureResolver(replace(bundle, entities=entities))


def _armor_class_without_defense(character: dict, bundle) -> object:
    return derive_armor_class(
        character["abilities"],
        character["equipment"]["item_refs"],
        bundle,
        armor_category_refs=character["proficiencies"]["armor_category_refs"],
    )


# --------------------------------------------------------------------------
# 6.1 declaration and catalog validation
# --------------------------------------------------------------------------

def test_additional_fighting_style_is_declared_for_champion() -> None:
    """1. Owner, level and both localized labels come from the real bundle."""

    labels = {"en": "Additional Fighting Style", "zh-CN": "额外战斗风格"}
    for locale, label in labels.items():
        resolver, definition = _choice_definition(_runtime(), locale)

        assert definition.owner == OWNER
        assert definition.minimum_level == 7
        assert definition.choice is not None
        assert resolver.catalog.label(FEATURE_ID) == label
        # 授予等级必须与子职表一致：声明 7 级、实际别处授予即已 fail closed。
        champion = resolver.progression.subclasses["fighter"]["feature_ids_by_level"]
        assert FEATURE_ID in champion["7"]


def test_the_choice_slot_parses_without_touching_option_of() -> None:
    """2. The declared slot is complete, and Monk's option_of is untouched."""

    resolver, definition = _choice_definition(_runtime())
    choice = definition.choice

    assert choice is not None
    assert (choice.id, choice.kind, choice.category, choice.count) == (
        CHOICE_ID, "advancement_feat", "fighting_style", 1,
    )
    assert choice.enabled_options == (DEFENSE,)
    # option_of 仍是 Monk's Focus 的附属能力语义，与 choice 互不干扰。
    focus = resolver.catalog.features_by_key[("class:monk", "flurry_of_blows")]
    assert focus.option_of == "monks_focus"
    assert focus.choice is None


_INVALID_CHOICES: dict[str, dict[str, object]] = {
    "unknown-kind": {"kind": "spell_choice"},
    "count-not-one": {"count": 2},
    "missing-category": {"category": None},
    "unknown-category": {"category": "not_a_category"},
    "invalid-choice-id": {"id": "Not A Canonical Id"},
    "empty-enabled": {"enabled_options": []},
    "duplicate-enabled": {"enabled_options": [DEFENSE, DEFENSE]},
    "cross-category-ref": {"enabled_options": ["feat:grappler"]},
    "undeclared-ref": {"enabled_options": ["feat:nope"]},
}


@pytest.mark.parametrize("case", sorted(_INVALID_CHOICES))
def test_invalid_choice_declarations_fail_closed(case: str) -> None:
    """3. Every malformed slot shape is rejected at load, not at the table."""

    patch = _INVALID_CHOICES[case]

    def mutate(feature: dict) -> None:
        choice = dict(BASE_CHOICE)
        for key, value in patch.items():
            if value is None:
                choice.pop(key, None)
            else:
                choice[key] = value
        feature["choice"] = choice

    with pytest.raises(ClassFeatureError):
        _catalog_with_choice(_runtime(), mutate)


def test_a_declared_level_must_match_the_subclass_grant() -> None:
    """4a. A drifted minimum_level keeps S0-A's fail-closed behaviour."""

    def mutate(feature: dict) -> None:
        feature["minimum_level"] = 6

    with pytest.raises(ClassFeatureError):
        _resolver_with_choice(_runtime(), mutate)


def test_a_foreign_owner_never_grants_the_feature() -> None:
    """4b. An owner outside the class's own subclasses is rejected at load."""

    def mutate(feature: dict) -> None:
        feature["owner"] = "subclass:thief"

    with pytest.raises(ClassFeatureError):
        _resolver_with_choice(_runtime(), mutate)


def test_only_defense_declares_the_armor_class_effect() -> None:
    """5. The +1 is declared on Defense alone, and it requires armor."""

    bundle = _runtime().load_bundle("en")
    feats = bundle.get("advancement_feat_catalog", "srd_advancement_feats")["feats"]

    assert feats["defense"]["passive_effects"] == [
        {"kind": "armor_class_bonus", "value": 1, "requires_armor": True},
    ]
    for feat_id, feat in feats.items():
        if feat_id == "defense":
            continue
        assert "passive_effects" not in feat, feat_id


# --------------------------------------------------------------------------
# 6.2 preview and input validation
# --------------------------------------------------------------------------

def test_a_fighter_without_champion_gets_no_choice_requirement() -> None:
    """6. No subclass selection means no slot -- and a forged slot is refused."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    character = sheet["ruleset_character"]
    character["features"].pop("subclass_ref", None)

    preview = runtime.preview_advancement(None, sheet, {})

    # 7 级不授予任何子职特性，因此这一级没有特性槽；但 A 段要求子职只能
    # 来自显式选择，所以缺子职的卡会先被要求补上，而不是悄悄升到 7 级。
    assert preview["ok"] is False
    assert any(
        "subclass_ref is required" in error for error in preview["errors"]
    )
    kinds = {requirement["kind"] for requirement in preview["requirements"]}
    assert "feature_choice" not in kinds

    forged = runtime.preview_advancement(None, sheet, _submission())
    assert forged["ok"] is False
    assert any("does not belong" in error for error in forged["errors"])
    with pytest.raises(ProgressionCatalogError):
        runtime.apply_advancement(None, sheet, _submission())


def test_champion_level_seven_preview_offers_four_fighting_styles() -> None:
    """7. Exactly one slot, four options, both locales, one of them usable."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    preview = runtime.preview_advancement(None, sheet, {})

    slots = [
        requirement for requirement in preview["requirements"]
        if requirement["kind"] == "feature_choice"
    ]
    assert len(slots) == 1
    slot = slots[0]
    assert slot["id"] == f"feature_choice:{OWNER}:{FEATURE_ID}:{CHOICE_ID}"
    assert slot["required"] is True
    assert (slot["owner"], slot["feature_id"], slot["choice_id"]) == (
        OWNER, FEATURE_ID, CHOICE_ID,
    )
    assert slot["count"] == 1

    options = {option["value"]: option for option in slot["options"]}
    assert set(options) == {DEFENSE, *UNIMPLEMENTED_STYLES}
    assert options[DEFENSE]["available"] is True
    assert options[DEFENSE]["name"] == "Defense"
    for ref in UNIMPLEMENTED_STYLES:
        assert options[ref]["available"] is False
        assert "effect_not_implemented" in options[ref]["unavailable_reasons"]

    zh_sheet = _sheet(_runtime(), 6)
    zh_preview = _runtime().preview_advancement(None, zh_sheet, {})
    zh_options = {
        option["value"]: option["name"]
        for requirement in zh_preview["requirements"]
        if requirement["kind"] == "feature_choice"
        for option in requirement["options"]
    }
    assert zh_options[DEFENSE] == "Defense"


@pytest.mark.parametrize(
    "choices",
    [
        pytest.param({}, id="omitted"),
        pytest.param({"feature_choices": []}, id="empty-array"),
        pytest.param(
            {"feature_choices": [{
                "owner": OWNER, "feature_id": FEATURE_ID, "choice_id": CHOICE_ID,
            }]},
            id="missing-selected-refs",
        ),
        pytest.param(_submission(DEFENSE), id="single-is-legal"),
    ],
)
def test_incomplete_feature_choices_are_rejected(choices: dict) -> None:
    """8. Omission is not consent; only one complete selection previews."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    preview = runtime.preview_advancement(None, sheet, choices)

    if choices == _submission(DEFENSE):
        assert preview["ok"] is True
        return
    assert preview["ok"] is False
    assert any(
        "requires a selection" in error or "selected_refs" in error
        for error in preview["errors"]
    )


def test_selecting_two_styles_at_once_is_rejected() -> None:
    """8b. ``count`` is 1, so a two-ref answer is rejected, not truncated."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    preview = runtime.preview_advancement(
        None, sheet, _submission(DEFENSE, selected_refs=[DEFENSE, ARCHERY]),
    )

    assert preview["ok"] is False
    assert any("exactly 1" in error for error in preview["errors"])


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"owner": "class:fighter"}, id="wrong-owner"),
        pytest.param({"feature_id": "improved_critical"}, id="wrong-feature"),
        pytest.param({"choice_id": "other_choice"}, id="wrong-choice"),
        pytest.param({"choice_id": "fighting_style_choice"}, id="unknown-slot"),
    ],
)
def test_wrong_slot_identifiers_are_rejected(overrides: dict) -> None:
    """9. The slot is identified by the full (owner, feature, choice) triple."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    preview = runtime.preview_advancement(
        None, sheet, _submission(DEFENSE, **overrides),
    )

    assert preview["ok"] is False
    assert any(
        "does not belong" in error or "requires a selection" in error
        for error in preview["errors"]
    )


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("feat:grappler", id="other-category"),
        pytest.param("feat:unknown", id="undeclared-feat"),
        pytest.param("defense", id="bare-id"),
        pytest.param(7, id="not-a-string"),
    ],
)
def test_ineligible_refs_are_rejected(ref: object) -> None:
    """10. Category, existence, shape and duplicates are all enforced."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    preview = runtime.preview_advancement(
        None, sheet, _submission(DEFENSE, selected_refs=[ref]),
    )

    assert preview["ok"] is False


def test_an_owned_fighting_style_is_unavailable_not_reselectable() -> None:
    """11. "Another" fighting style cannot be the one already owned."""

    runtime = _runtime()
    # 1 级选了 Defense：7 级时 Defense 已在 feat_refs 中。
    sheet = _sheet(runtime, 6, fighting_style=DEFENSE)
    preview = runtime.preview_advancement(None, sheet, {})

    slot = next(
        requirement for requirement in preview["requirements"]
        if requirement["kind"] == "feature_choice"
    )
    defense = next(
        option for option in slot["options"] if option["value"] == DEFENSE
    )
    assert defense["available"] is False
    assert "already_owned" in defense["unavailable_reasons"]

    rejected = runtime.preview_advancement(None, sheet, _submission())
    assert rejected["ok"] is False
    with pytest.raises(ProgressionCatalogError):
        runtime.apply_advancement(None, sheet, _submission())


def test_the_slot_is_only_required_at_the_granting_level() -> None:
    """12. Neither a lower nor a higher level accepts or re-demands the slot."""

    runtime = _runtime()
    five = _sheet(runtime, 5)
    assert FEATURE_ID not in {
        feature.id
        for feature in Dnd2024ClassFeatureResolver(
            runtime.load_bundle("en")
        ).features_for(five["ruleset_character"])
    }
    lower = runtime.preview_advancement(None, five, _submission())
    assert lower["ok"] is False
    assert any("does not belong" in error for error in lower["errors"])

    seven = runtime.apply_advancement(None, _sheet(runtime, 6), _submission())
    higher = runtime.preview_advancement(None, seven, _submission())
    assert higher["ok"] is False
    assert any("does not belong" in error for error in higher["errors"])


def test_preview_does_not_mutate_its_inputs() -> None:
    """13. Preview stays a pure read of the payload and the submission."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    choices = _submission()
    sheet_before = deepcopy(sheet)
    choices_before = deepcopy(choices)

    runtime.preview_advancement(None, sheet, choices)

    assert sheet == sheet_before
    assert choices == choices_before


# --------------------------------------------------------------------------
# 6.3 apply, ownership and history
# --------------------------------------------------------------------------

def test_applying_the_choice_grants_defense_and_records_the_selection() -> None:
    """14. Ownership, persisted slot and Resolver projection all agree."""

    runtime = _runtime()
    advanced = runtime.apply_advancement(None, _sheet(runtime, 6), _submission())
    character = advanced["ruleset_character"]

    assert character["features"]["feat_refs"].count(DEFENSE) == 1
    assert character["features"]["feature_choices"] == [{
        "owner": OWNER,
        "feature_id": FEATURE_ID,
        "choice_id": CHOICE_ID,
        "selected_refs": [DEFENSE],
        "granted_level": 7,
    }]

    resolver, definition = _choice_definition(runtime)
    owned = {feature.id for feature in resolver.features_for(character)}
    assert FEATURE_ID in owned
    view = next(
        item for item in resolver.feature_views(character) if item.id == FEATURE_ID
    )
    assert view.to_dict()["selection"] == {
        "choice_id": CHOICE_ID, "selected_refs": [DEFENSE],
    }


def test_applying_without_the_choice_raises_and_changes_nothing() -> None:
    """15. A rejected preview leaves level, feats, HP and history untouched."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    before = deepcopy(sheet)

    with pytest.raises(ProgressionCatalogError):
        runtime.apply_advancement(None, sheet, {})

    assert sheet == before


@pytest.mark.parametrize("ref", UNIMPLEMENTED_STYLES)
def test_unimplemented_fighting_styles_cannot_be_selected(ref: str) -> None:
    """16a. Visible but not selectable: no silent no-op feat."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    preview = runtime.preview_advancement(None, sheet, _submission(ref))

    assert preview["ok"] is False
    with pytest.raises(ProgressionCatalogError):
        runtime.apply_advancement(None, sheet, _submission(ref))


def test_a_later_level_never_grants_defense_twice() -> None:
    """16b. The slot is spent; later level-ups do not re-award the feat."""

    runtime = _runtime()
    seven = runtime.apply_advancement(None, _sheet(runtime, 6), _submission())
    eight = _sheet(runtime, 8)

    # 用同一张 7 级角色继续升级，直到 9 级，Defense 只能出现一次。
    while int(seven["ruleset_character"]["build"]["level"]) < 9:
        preview = runtime.preview_advancement(None, seven, {})
        picks = advancement_choices(preview)
        asi = next(
            (
                requirement for requirement in preview["requirements"]
                if requirement["id"] == "ability_score_increases"
            ),
            None,
        )
        if asi is not None:
            total = int(asi.get("total", 2))
            abilities = seven["ruleset_character"]["abilities"]
            ability = next(
                key for key in abilities if abilities[key] + total <= 20
            )
            picks["ability_score_increases"] = {ability: total}
        preview = runtime.preview_advancement(None, seven, picks)
        assert preview["ok"] is True, preview["errors"]
        seven = runtime.apply_advancement(None, seven, picks)

    assert seven["ruleset_character"]["features"]["feat_refs"].count(DEFENSE) == 1
    assert eight["ruleset_character"]["build"]["level"] == 8


def test_rebuilding_from_history_reproduces_the_same_selection() -> None:
    """17. History replay re-runs the same validation and yields the same sheet."""

    runtime = _runtime()
    advanced = runtime.apply_advancement(None, _sheet(runtime, 6), _submission())
    canonical = advanced["ruleset_character"]
    rebuilt = runtime.normalize_character_submission(None, advanced, "en")[
        "ruleset_character"
    ]

    assert rebuilt["features"]["feature_choices"] == canonical["features"][
        "feature_choices"
    ]
    assert rebuilt["features"]["feat_refs"].count(DEFENSE) == 1
    assert rebuilt["derived"]["armor_class"] == canonical["derived"]["armor_class"]

    resolver, definition = _choice_definition(runtime)
    assert resolver.feature_selection(rebuilt, definition) == (
        resolver.feature_selection(canonical, definition)
    )


@pytest.mark.parametrize(
    "forged",
    [
        pytest.param(
            {
                "owner": "class:fighter", "feature_id": FEATURE_ID,
                "choice_id": CHOICE_ID, "selected_refs": [DEFENSE],
                "granted_level": 7,
            },
            id="wrong-owner",
        ),
        pytest.param(
            {
                "owner": "subclass:thief", "feature_id": FEATURE_ID,
                "choice_id": CHOICE_ID, "selected_refs": [DEFENSE],
                "granted_level": 7,
            },
            id="other-subclass",
        ),
        pytest.param(
            {
                "owner": OWNER, "feature_id": FEATURE_ID,
                "choice_id": CHOICE_ID, "selected_refs": ["feat:grappler"],
                "granted_level": 7,
            },
            id="unknown-ref",
        ),
        pytest.param(
            {
                "owner": OWNER, "feature_id": FEATURE_ID,
                "choice_id": "not_a_choice", "selected_refs": [DEFENSE],
                "granted_level": 7,
            },
            id="unknown-choice",
        ),
    ],
)
def test_a_forged_record_never_becomes_an_effective_selection(
    forged: dict,
) -> None:
    """18. A tampered record shows nothing and grants nothing extra."""

    runtime = _runtime()
    advanced = runtime.apply_advancement(None, _sheet(runtime, 6), _submission())
    character = deepcopy(advanced["ruleset_character"])
    character["features"]["feature_choices"] = [forged]
    resolver, definition = _choice_definition(runtime)

    selection = resolver.feature_selection(character, definition)

    assert selection == {"choice_id": CHOICE_ID, "selected_refs": []}
    assert character["features"]["feat_refs"] == advanced["ruleset_character"][
        "features"
    ]["feat_refs"]


def test_the_existing_champion_features_still_resolve() -> None:
    """19. S0-A's Improved Critical and the class resources are untouched."""

    runtime = _runtime()
    advanced = runtime.apply_advancement(None, _sheet(runtime, 6), _submission())
    character = advanced["ruleset_character"]
    resolver, _definition = _choice_definition(runtime)

    owned = {feature.id for feature in resolver.features_for(character)}
    assert {"improved_critical", FEATURE_ID}.issubset(owned)
    assert resolver.min_feature_value(character, "crit_threshold") == 19
    resources = {definition.id for definition in resolver.resource_definitions(character)}
    assert "second_wind" in resources
    capabilities = {
        capability.id
        for capability in resolver.combat_capabilities(character)
    }
    assert "action_surge" in capabilities


# --------------------------------------------------------------------------
# 6.4 Defense armor class and live equipment
# --------------------------------------------------------------------------

def test_defense_adds_nothing_without_worn_armor() -> None:
    """20. Unarmored, and shield-only, both stay at the plain baseline."""

    runtime = _runtime()
    game = _Game(runtime, fighting_style=DEFENSE)
    game.strip()
    unarmored = game.armor_class
    bundle = runtime.load_bundle("en")
    canonical = game.canonical
    assert DEFENSE in (canonical["features"].get("feat_refs") or [])
    assert unarmored == _armor_class_without_defense(canonical, bundle)

    game.buy("Shield")
    game.equip("Shield")
    assert DEFENSE in (game.canonical["features"].get("feat_refs") or [])
    # 持盾不是穿甲：Defense 仍不加成，盾牌自身加值照旧。
    assert game.armor_class == derive_armor_class(
        game.canonical["abilities"],
        game.canonical["equipment"]["item_refs"],
        bundle,
        armor_category_refs=game.canonical["proficiencies"]["armor_category_refs"],
    )


@pytest.mark.parametrize(
    "item_name",
    [
        pytest.param("Leather Armor", id="light"),
        pytest.param("Hide Armor", id="medium"),
        pytest.param("Chain Mail", id="heavy"),
    ],
)
def test_defense_adds_exactly_one_armor_class_with_worn_armor(item_name: str) -> None:
    """21. Every armor category gets exactly +1, never +2 and never 0."""

    runtime = _runtime()
    game = _Game(runtime, fighting_style=DEFENSE)
    game.strip()
    game.buy(item_name)
    game.equip(item_name)

    bundle = runtime.load_bundle("en")
    canonical = game.canonical
    without = _armor_class_without_defense(canonical, bundle)

    assert game.armor_class == without + 1
    assert game.mirror_armor_class == game.armor_class


def test_armor_in_the_backpack_does_not_count() -> None:
    """22. Worn, removed and re-worn track the bonus exactly."""

    runtime = _runtime()
    game = _Game(runtime, fighting_style=DEFENSE)
    game.strip()
    baseline = game.armor_class

    game.buy("Chain Mail")
    assert game.armor_class == baseline, "a backpack armor must not raise AC"

    game.equip("Chain Mail")
    worn = game.armor_class
    assert worn > baseline

    game.unequip("Chain Mail")
    assert game.armor_class == baseline


def test_live_reconciliation_keeps_defense_across_swaps() -> None:
    """23. Canonical and legacy AC stay equal through equip/unequip cycles."""

    runtime = _runtime()
    game = _Game(runtime, fighting_style=DEFENSE)
    game.strip()

    for _cycle in range(3):
        game.buy("Chain Mail")
        game.equip("Chain Mail")
        assert game.armor_class == game.mirror_armor_class
        game.unequip("Chain Mail")
        assert game.armor_class == game.mirror_armor_class

    assert DEFENSE in (game.canonical["features"].get("feat_refs") or [])


def test_duplicate_defense_and_extra_armor_do_not_stack() -> None:
    """24. A repeated ref and two worn armors add nothing beyond a single +1."""

    runtime = _runtime()
    bundle = runtime.load_bundle("en")
    game = _Game(runtime, fighting_style=DEFENSE)
    game.strip()
    game.buy("Chain Mail")
    game.equip("Chain Mail")
    worn = game.armor_class
    canonical = game.canonical
    abilities = canonical["abilities"]
    armor_refs = canonical["proficiencies"]["armor_category_refs"]

    assert derive_armor_class(
        abilities, ["item:chain_mail"], bundle,
        armor_category_refs=armor_refs,
        feat_refs=[DEFENSE, DEFENSE],
    ) == worn

    # 同时穿着两件护甲是非法 live state：既有的"取一件合法护甲"行为不变，
    # Defense 仍然只加一次。
    warnings: list[str] = []
    stacked = derive_armor_class(
        abilities, ["item:chain_mail", "item:hide"], bundle,
        armor_category_refs=armor_refs, warnings=warnings,
        feat_refs=[DEFENSE],
    )
    plain = derive_armor_class(
        abilities, ["item:chain_mail", "item:hide"], bundle,
        armor_category_refs=armor_refs,
    )

    assert plain == 16
    assert stacked == plain + 1
    assert any("extra armor ignored" in warning for warning in warnings)


def test_the_armor_class_formula_is_unchanged_without_the_feat() -> None:
    """25. Dex caps and shield bonuses still behave exactly as before.

    The character owns Archery, which declares no armor class effect, so the
    canonical value must equal the plain formula -- a feat without a passive
    bonus must not nudge armor class at all.
    """

    runtime = _runtime()
    bundle = runtime.load_bundle("en")
    game = _Game(runtime)
    game.strip()
    game.buy("Half Plate Armor")
    game.equip("Half Plate Armor")
    game.buy("Shield")
    game.equip("Shield")

    canonical = game.canonical
    abilities = canonical["abilities"]
    armor_refs = canonical["proficiencies"]["armor_category_refs"]
    without = derive_armor_class(
        abilities, canonical["equipment"]["item_refs"], bundle,
        armor_category_refs=armor_refs,
    )
    dex_bonus = min((abilities["dex"] - 10) // 2, 2)

    assert without == 15 + dex_bonus + 2
    assert game.armor_class == without
    assert DEFENSE not in (canonical["features"].get("feat_refs") or [])


def test_the_same_level_up_updates_armor_class() -> None:
    """26. Selecting Defense takes effect in the applying call itself."""

    runtime = _runtime()
    sheet = _sheet(runtime, 6)
    before = sheet["ruleset_character"]["derived"]["armor_class"]

    advanced = runtime.apply_advancement(None, sheet, _submission())
    after = advanced["ruleset_character"]["derived"]["armor_class"]

    assert "item:chain_mail" in advanced["ruleset_character"]["equipment"]["item_refs"]
    assert after == before + 1
