"""Derivation stage for deterministic D&D 2024 characters."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from copy import deepcopy
from typing import Any

from src.rulesets.dnd2024.binding import rule_binding
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver

from .attunement import AttunementError as _AttunementError
from .attunement import attunement_capacity as _attunement_capacity
from .attunement import canonical_item_ref as _canonical_item_ref
from .attunement import requires_attunement as _requires_attunement
from .primitives import ABILITY_IDS, ability_modifier, proficiency_bonus, ref_id as _ref_id


def _item_key(ref: Any) -> str:
    """Canonical item id, tolerating the legacy bare-id form."""

    try:
        return _canonical_item_ref(ref).removeprefix("item:")
    except _AttunementError:
        return _ref_id(str(ref or ""), "item")


def _bound_refs(refs: Collection[str]) -> set[str]:
    return {_item_key(ref) for ref in refs}


# 普通盾牌的基础护甲等级加值（SRD 5.2.1: Shield, +2 AC）。只在内容用单字段
# ``ac_bonus`` 且声明了需同调、未同调时，用来兜底「这件盾牌最多只能当普通盾
# 用」。有 ``ac_base`` 声明的内容永远走显式基础值，不受这里影响。
PLAIN_SHIELD_AC_BONUS = 2


def _feature_proxy(
    class_ref: str, level: int, feature_state: Mapping[str, Any] | None = None,
    equipped_refs: Iterable[str] = (),
    feature_choices: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The minimal canonical shape ``features_for`` needs to resolve ownership.

    Subclass features are owned only through a persisted selection, so the
    proxy carries ``subclass_ref`` when the caller has one.  A draft built at
    level 1 has none and correctly owns no subclass feature.

    ``equipped_refs`` carries the character's canonical equipment so that an
    ``equipment_requirement`` is answered against what is actually worn.  Without
    it every equipment-gated benefit reads as satisfied at creation time, and a
    Monk holding a Shield would keep the unarmored formula the SRD denies.

    ``feature_choices`` carries the committed ``(owner, feature_id, choice_id)``
    slots.  Expertise lives only in those slots, so a proxy without them would
    read every skill as an ordinary proficiency even though the sheet records
    the selection.
    """

    proxy: dict[str, Any] = {
        "build": {"class_levels": [{"class_ref": class_ref, "level": level}]},
    }
    if isinstance(feature_state, Mapping):
        subclass_ref = feature_state.get("subclass_ref")
        if isinstance(subclass_ref, str) and subclass_ref:
            proxy["features"] = {"subclass_ref": subclass_ref}
    choices = [dict(record) for record in feature_choices]
    if choices:
        proxy.setdefault("features", {})["feature_choices"] = choices
    refs = [str(ref) for ref in equipped_refs if str(ref)]
    if refs:
        proxy["equipment"] = {"item_refs": refs}
    return proxy


def _passive_armor_class_bonus(bundle: Any, feat_refs: Collection[str]) -> int:
    """The flat AC bonus owned advancement feats declare for worn armor.

    Every number comes from the feat's own ``passive_effects`` declaration, so
    the formula holds no magic constant.  A duplicated ref adds its value once,
    and an effect without ``requires_armor`` never lands here at all.
    """

    if not feat_refs:
        return 0
    catalog = bundle.get("advancement_feat_catalog", "srd_advancement_feats")
    feats = catalog.get("feats") if isinstance(catalog, Mapping) else None
    if not isinstance(feats, Mapping):
        return 0
    total = 0
    for ref in {str(raw) for raw in feat_refs}:
        feat = feats.get(ref.removeprefix("feat:"))
        if not isinstance(feat, Mapping):
            continue
        effects = feat.get("passive_effects")
        if not isinstance(effects, list):
            continue
        for effect in effects:
            if not isinstance(effect, Mapping):
                continue
            if effect.get("kind") != "armor_class_bonus":
                continue
            if effect.get("requires_armor") is not True:
                continue
            value = effect.get("value")
            if isinstance(value, int) and not isinstance(value, bool):
                total += value
    return total


def derive_armor_class(
    abilities: Mapping[str, int],
    equipped_item_refs: Iterable[str],
    bundle: Any,
    *,
    armor_category_refs: Collection[str],
    warnings: list[str] | None = None,
    unarmored_ability_bonuses: Iterable[str] = (),
    attuned_refs: Collection[str] = (),
    feat_refs: Collection[str] = (),
) -> int:
    """The one and only D&D 2024 armor class formula.

    Character creation and live equipment reconciliation both call this, so the
    rule exists exactly once.  Every number comes from the ruleset bundle's own
    item definitions -- never from an LLM, the frontend, the generic layer, or a
    localized display name.

    The result is independent of the order ``equipped_item_refs`` arrives in.
    Abnormal live state is contained rather than amplified: several armors
    resolve to the single best one instead of stacking their bases, only one
    shield bonus is ever applied, and an item the bundle does not define is
    skipped with a warning instead of guessing at a lookalike.

    Attunement gates the magic component only: an attunable item keeps its
    ordinary contribution while unbound, so a legacy single-field shield
    declaration cannot smuggle its magic bonus past the gate.

    ``unarmored_ability_bonuses`` names the abilities that a feature may add to
    an *unarmored* base AC.  "While you aren't wearing armor" is a hard
    precondition: with any armor present the plain ``10 + Dexterity`` baseline
    is used, so a feature formula never competes with the armor's own AC.  Each
    named ability is an alternative candidate, and the best candidate wins --
    ability modifiers are never summed.

    ``feat_refs`` names the advancement feats the character owns.  Only a flat
    ``armor_class_bonus`` effect that declares ``requires_armor`` is consumed
    here, and only while an armor was actually selected: Defense-style bonuses
    never apply unarmored, and a shield is not armor for this purpose.
    """

    dex_modifier = ability_modifier(int(abilities["dex"]))
    unarmored = 10 + dex_modifier
    best_armor: int | None = None
    shield_bonus = 0
    bound = _bound_refs(attuned_refs)
    for raw_ref in equipped_item_refs:
        ref = str(raw_ref or "")
        item = bundle.get("item", _ref_id(ref, "item"))
        if item is None:
            if warnings is not None:
                warnings.append(f"unknown item reference ignored by armor class: {ref}")
            continue
        magic_bonus = int(item.get("ac_bonus", 0) or 0)
        pending_attunement = _requires_attunement(item) and _item_key(ref) not in bound
        if pending_attunement:
            # 未同调的魔法物品只是普通同类物品：保留基础属性，不给魔法加值。
            magic_bonus = 0
        item_type = item.get("item_type")
        if item_type == "armor":
            dex_bonus = 0
            if item.get("use_dex", True):
                dex_bonus = dex_modifier
                dex_cap = item.get("dex_cap")
                if dex_cap is not None:
                    dex_bonus = min(dex_bonus, int(dex_cap))
            candidate = int(item.get("ac_base", 10)) + dex_bonus + magic_bonus
            if best_armor is None:
                best_armor = candidate
            else:
                # 同时穿着多件盔甲是非法 live state：取一件合法的，绝不相加。
                if warnings is not None:
                    warnings.append(f"extra armor ignored by armor class: {ref}")
                best_armor = max(best_armor, candidate)
        elif item_type == "shield":
            # 盾牌必须先受训，否则它不给护甲等级加值（Armor Training 条款）。
            if "armor_category:shield" not in armor_category_refs:
                continue
            # 未同调只去掉魔法加值，基础盾牌加值仍然有效。内容可以用
            # ``ac_base`` 明确声明基础值、``ac_bonus`` 声明魔法加值；没有
            # ``ac_base`` 的旧条目把 ``ac_bonus`` 当作完整的基础盾牌加值。
            if "ac_base" in item:
                bonus = int(item.get("ac_base", 0) or 0) + magic_bonus
            elif pending_attunement:
                # 单字段声明 + 需同调 = 这个 ``ac_bonus`` 里含魔法加值。
                # 未同调时整份拿走等于让同调限制对盾牌失效，只能按普通盾牌算。
                bonus = min(int(item.get("ac_bonus", 0) or 0), PLAIN_SHIELD_AC_BONUS)
            else:
                bonus = int(item.get("ac_bonus", 0) or 0)
            if shield_bonus:
                # 同理：两面盾不能 +2 +2。
                if warnings is not None:
                    warnings.append(f"extra shield ignored by armor class: {ref}")
                continue
            shield_bonus = bonus
    if best_armor is None:
        for ability in unarmored_ability_bonuses:
            unarmored = max(
                unarmored,
                10 + dex_modifier + ability_modifier(int(abilities[str(ability)])),
            )
    base = max(unarmored, best_armor if best_armor is not None else unarmored)
    if best_armor is not None:
        # "While you're wearing Light, Medium, or Heavy armor" 是硬前提：没有
        # 选中任何护甲时，被动加值一律不加，也不看是否持盾。
        base += _passive_armor_class_bonus(bundle, feat_refs)
    return base + shield_bonus


def derive_speed(
    base_speed: int,
    *,
    feature_bonus: int = 0,
    armor_training: Mapping[str, Any] | None = None,
) -> int:
    """The one and only derived Speed formula.

    Character creation, advancement, and live equipment reconciliation all call
    this, so the rule exists exactly once.  ``base_speed`` is the species value;
    ``feature_bonus`` is the sum the feature resolver reports for *active*
    declarations only, so an equipment-gated bonus is already gone when its
    precondition fails.  ``armor_training`` contributes the "-10 feet" a worn
    armor imposes when the wearer's Strength is below its requirement.

    Speed never goes negative, and no rule number lives here: both the bonus and
    the penalty arrive from their own declarations.
    """

    penalty = 0
    if isinstance(armor_training, Mapping):
        raw = armor_training.get("speed_penalty", 0)
        if isinstance(raw, int) and not isinstance(raw, bool):
            penalty = raw
    bonus = feature_bonus if isinstance(feature_bonus, int) else 0
    return max(0, int(base_speed) + bonus - penalty)


_SKILL_CHOICE_KEYS = frozenset({"owner", "feature_id", "choice_id", "selected_refs"})


def _plan_creation_pools(
    bundle: Any,
    features: Any,
    proxy: Mapping[str, Any],
    draft: Mapping[str, Any],
    level: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The pool picks a draft commits at creation, validated like a level-up.

    An unanswered slot is tolerated (the draft predates the field and the first
    level-up tops the character up), but anything submitted must satisfy the
    same count, level, prerequisite and binding rules.
    """

    from src.rulesets.dnd2024.features import option_pool

    class_ref = str(draft.get("class_ref") or "")
    cantrips = [
        str(ref) for ref in (
            (draft.get("class_spell_choices") or {}).get("cantrip_refs") or []
        )
    ]
    submitted: dict[tuple[str, str, str], list[Any]] = {}
    for record in draft.get("feature_choices") or []:
        if not isinstance(record, dict):
            continue
        refs = record.get("selected_refs")
        if not isinstance(refs, list):
            continue
        key = (
            str(record.get("owner") or ""),
            str(record.get("feature_id") or ""),
            str(record.get("choice_id") or ""),
        )
        submitted.setdefault(key, []).extend(refs)
    spells_catalog = bundle.get("spell_catalog", "srd_spells") or {}
    spells = {
        str(row.get("id")): row
        for row in (spells_catalog.get("spells") or [])
        if isinstance(row, dict)
    }
    combat = bundle.get("combat_catalog", "srd_combat_core") or {}
    effects = combat.get("spell_effects") if isinstance(combat, Mapping) else None
    progression = bundle.get("progression_catalog", "srd_classes") or {}
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for definition in features.features_for(proxy):
        choice = definition.choice
        if choice is None or not choice.count_track:
            continue
        record, slot_errors = option_pool.plan_creation_pool(
            resolver=features,
            definition=definition,
            cantrips=cantrips,
            submitted=submitted.get((definition.owner, definition.id, choice.id)),
            level=level,
            track_total=option_pool.track_value(
                progression, choice.count_track, class_ref, level,
            ),
            spells=spells,
            spell_effects=effects if isinstance(effects, Mapping) else {},
        )
        if record is not None:
            records.append(record)
        errors.extend(slot_errors)
    return records, errors


def plan_skill_choices(
    raw_choices: Any,
    features: Dnd2024ClassFeatureResolver,
    proxy: Mapping[str, Any],
    skill_refs: Collection[str],
    *,
    level: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate the skill selections a draft commits at character creation.

    Only a feature the character actually owns *at this level* may hold a slot,
    so a submitted record can neither invent an owner nor pre-fill a future
    level.  Expertise is the only skill choice granted at level 1 today (Rogue),
    but nothing here names that class: the declaration decides.

    Every rule the level-up path enforces is enforced again, because a draft is
    an untrusted submission: the exact count, uniqueness inside the slot, the
    declaration's own whitelist, "you must already be proficient", and "you
    can't take Expertise in the same skill twice".  Slots are processed in
    catalog order so the second slot sees what the first one took.
    """

    errors: list[str] = []
    if raw_choices is None:
        raw_choices = []
    if not isinstance(raw_choices, list):
        return [], ["feature_choices must be an array"]
    submitted: list[dict[str, Any]] = []
    for raw in raw_choices:
        if not isinstance(raw, Mapping):
            errors.append("each feature choice must be an object")
            continue
        unknown = sorted(set(raw) - _SKILL_CHOICE_KEYS)
        if unknown:
            errors.append(f"feature choice has an unknown field: {unknown[0]}")
            continue
        submitted.append({
            "owner": str(raw.get("owner") or ""),
            "feature_id": str(raw.get("feature_id") or ""),
            "choice_id": str(raw.get("choice_id") or ""),
            "selected_refs": raw.get("selected_refs"),
        })

    slots = [
        definition for definition in features.features_for(dict(proxy))
        if definition.choice is not None
        and definition.choice.kind == "skill_proficiency"
    ]
    known_skills = {
        str(entity.get("id")) for entity in features.bundle.list("skill")
    }
    matched: set[int] = set()
    records: list[dict[str, Any]] = []
    taken: dict[str, str] = {}
    proficient = {str(ref) for ref in skill_refs}
    for definition in slots:
        choice = definition.choice
        if choice is None:  # pragma: no cover - filtered above
            continue
        slot = [
            index for index, record in enumerate(submitted)
            if record["owner"] == definition.owner
            and record["feature_id"] == definition.id
            and record["choice_id"] == choice.id
        ]
        if len(slot) > 1:
            errors.append(f"feature choice {definition.id} was submitted more than once")
            matched.update(slot)
            continue
        if not slot:
            # 建卡阶段把未作答视为"还没选"，而不是整张卡非法：建卡界面会先
            # 投影这个槽（``builder_choices.skill_choices``）再让玩家作答，
            # 而历史存档与既有测试夹具里本来就没有这个字段。代价是不发放
            # Expertise——这是缺口，不是静默通过；升级路径仍然要求作答。
            continue
        matched.add(slot[0])
        raw_refs = submitted[slot[0]]["selected_refs"]
        if not isinstance(raw_refs, list) or not all(
            isinstance(ref, str) for ref in raw_refs
        ):
            errors.append(f"feature choice {definition.id} selected_refs must be strings")
            continue
        if len(raw_refs) != choice.count:
            errors.append(
                f"feature choice {definition.id} must select exactly {choice.count} option"
                + ("" if choice.count == 1 else "s")
            )
            continue
        if len(set(raw_refs)) != len(raw_refs):
            errors.append(f"feature choice {definition.id} selected_refs repeats an option")
            continue
        allowed = set(choice.enabled_options)
        slot_refs: list[str] = []
        for ref in raw_refs:
            if allowed and ref not in allowed:
                errors.append(f"{ref} is not a declared option of {definition.id}")
                continue
            skill_id = str(ref).removeprefix("skill:")
            if not ref.startswith("skill:") or skill_id not in known_skills:
                errors.append(f"{ref} is not a declared skill")
                continue
            if choice.requires_proficiency and ref not in proficient:
                errors.append(f"{ref} is not a skill proficiency of this character")
                continue
            if choice.exclude_expertise and skill_id in taken:
                errors.append(f"{ref} already has Expertise from {taken[skill_id]}")
                continue
            if choice.grants == "expertise":
                taken[skill_id] = definition.id
            slot_refs.append(ref)
        if len(slot_refs) != choice.count:
            continue
        records.append({
            "owner": definition.owner,
            "feature_id": definition.id,
            "choice_id": choice.id,
            "selected_refs": slot_refs,
            "granted_level": int(level),
        })
    for index, record in enumerate(submitted):
        if index in matched:
            continue
        errors.append(
            "feature choice references a slot this character does not have: "
            f"{record['owner']}/{record['feature_id']}/{record['choice_id']}"
        )
    return records, errors


_ARMOR_CATEGORIES = frozenset({"light", "medium", "heavy"})


def apply_feature_ability_bonuses(
    abilities: Mapping[str, int],
    grants: Mapping[str, tuple[int, int]],
    applied: Mapping[str, int],
) -> tuple[dict[str, int], dict[str, int]]:
    """Give the owned features' ability increases, replacing what they gave before.

    ``grants`` is ``{ability: (bonus, cap)}`` as the feature boundary reports
    it, and ``applied`` is what this function returned last time.  Replacing
    instead of adding is what makes the rule survive being run twice: the same
    call on an unchanged character moves nothing, a feature that is no longer
    granted hands its points back, and a level-up in between keeps its own
    increase because only the feature's share is taken off before the new share
    is added.

    The cap is applied to the score the character would otherwise have, so a
    Strength of 21 becomes 25 under "+4, to a maximum of 25" while a Strength of
    12 becomes 16.
    """

    updated = {
        ability: int(abilities.get(ability, 0) or 0) for ability in ABILITY_IDS
    }
    record: dict[str, int] = {}
    for ability in ABILITY_IDS:
        previous = int(applied.get(ability, 0) or 0)
        base = updated[ability] - previous
        grant = grants.get(ability)
        if not grant:
            # 不再授予时必须把上次给的点收回，否则角色掉等级后仍留着 +4。
            updated[ability] = base
            continue
        bonus, cap = int(grant[0]), int(grant[1])
        raised = min(cap, base + bonus) if bonus else base
        record[ability] = raised - base
        updated[ability] = raised
    return updated, record


def derive_skill_check(
    ability_score: int,
    *,
    proficiency_bonus: int,
    proficient: bool,
    expertise: bool = False,
    half_proficiency: bool = False,
    pb_used_elsewhere: bool = False,
) -> dict[str, Any]:
    """The one and only ability-check bonus formula for a skill.

    Character creation, advancement, and live re-derivation all call this, so
    the rule exists exactly once.  The 2024 SRD states three separate
    contributions and never stacks them:

    * no proficiency -> ``0``;
    * proficiency -> ``+PB``;
    * Expertise -> ``+2 x PB``, because "your Proficiency Bonus is doubled for
      that check" is a *replacement* of the ordinary contribution, not an
      additional PB (doubling twice would make a third source silently triple
      it);
    * Jack of All Trades -> ``floor(PB / 2)``, and only "for a skill proficiency
      you lack" that "doesn't otherwise use your Proficiency Bonus".  It never
      rides on top of proficiency or Expertise, and ``pb_used_elsewhere``
      carries the second half of that condition for the check that is actually
      being resolved.

    Expertise without proficiency contributes nothing: every selection path
    rejects that combination, so reaching it here means the sheet is corrupt and
    paying out a doubled bonus would reward the corruption.
    """

    bonus = int(proficiency_bonus)
    modifier = ability_modifier(int(ability_score))
    components: list[dict[str, Any]] = [{"code": "ability", "amount": modifier}]
    contribution = 0
    applied_half = False
    if proficient and expertise:
        contribution = 2 * bonus
        components.append({"code": "expertise", "amount": contribution})
    elif proficient:
        contribution = bonus
        components.append({"code": "proficiency", "amount": contribution})
    elif half_proficiency and not pb_used_elsewhere:
        contribution = bonus // 2
        applied_half = True
        components.append({"code": "half_proficiency", "amount": contribution})
    return {
        "ability_modifier": modifier,
        "proficiency_bonus": bonus,
        "proficient": bool(proficient),
        "expertise": bool(proficient and expertise),
        "half_proficiency": applied_half,
        "contribution": contribution,
        "total": modifier + contribution,
        "components": components,
    }


def derive_jump_distances(
    abilities: Mapping[str, int], *, ability: str = "str",
) -> dict[str, Any]:
    """The jump distances an ability score and its modifier produce.

    A Long Jump covers up to the *score*; a High Jump covers ``3`` plus the
    *modifier*, floored at zero.  A standing jump is half the corresponding
    running distance, and the halving happens before anything else reads the
    number -- rounding up would hand out distance the rule does not grant.

    ``ability`` is the score that is read, not an extra bonus: Second-Story Work
    swaps Strength for Dexterity, so the caller passes ``dex`` and the Strength
    score stops mattering.  Reach (``1.5 x height``) is deliberately absent: the
    canonical record carries no height fact, so publishing a reach here would be
    inventing one.
    """

    score = int(abilities[str(ability)])
    high_jump = max(0, 3 + ability_modifier(score))
    long_jump = max(0, score)
    return {
        "jump_ability": str(ability),
        "long_jump_ft": long_jump,
        "standing_long_jump_ft": long_jump // 2,
        "high_jump_ft": high_jump,
        "standing_high_jump_ft": high_jump // 2,
    }


def derive_movement_profile(
    speed: int,
    abilities: Mapping[str, int],
    features: Dnd2024ClassFeatureResolver,
    character: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The special Speeds and jump distances that ride on the derived Speed.

    Climb and Swim Speeds equal the *current* Speed, so they are re-derived here
    instead of being stored as independent numbers; a mode nobody declared is
    ``0`` rather than the Speed, because "no Climb Speed" is a real answer that
    decides whether the extra-per-foot cost applies.

    Order matters only in that this must run after ``derive_speed``: the profile
    copies that result and never recomputes it.
    """

    modes = features.movement_modes(character)
    return {
        "climb_speed": int(speed) if modes["climb"] else 0,
        "swim_speed": int(speed) if modes["swim"] else 0,
        **derive_jump_distances(abilities, ability=features.jump_ability(character)),
    }


def derive_armor_training_state(
    abilities: Mapping[str, int],
    equipped_item_refs: Iterable[str],
    armor_category_refs: Collection[str],
    bundle: Any,
) -> dict[str, Any]:
    """Armor-training consequences of the armor the character is wearing.

    The 2024 rules gate three separate facts on worn armor: an untrained light,
    medium, or heavy armor imposes disadvantage on Strength/Dexterity D20 tests
    and blocks spellcasting; a worn armor whose Strength requirement the
    character does not meet reduces Speed by 10 feet; and an armor with a
    Stealth penalty imposes disadvantage on Dexterity (Stealth) checks.  The
    numbers all come from the item definitions, never from a guessed category.

    A missing or unknown ``category`` on an armor item is unusable directory
    data: the function raises instead of silently treating the armor as trained.
    Shields carry no category and never participate; an item the bundle does
    not define is skipped the same way the armor-class formula skips it.
    """

    strength = int(abilities["str"])
    untrained = False
    speed_penalty = 0
    stealth_disadvantage = False
    for raw_ref in equipped_item_refs:
        ref = str(raw_ref or "")
        item = bundle.get("item", _ref_id(ref, "item"))
        if item is None or item.get("item_type") != "armor":
            continue
        category = item.get("category")
        if not isinstance(category, str) or category not in _ARMOR_CATEGORIES:
            raise ValueError(
                f"armor item {ref!r} has an invalid category: {category!r}"
            )
        if f"armor_category:{category}" not in armor_category_refs:
            untrained = True
        minimum = item.get("strength_minimum")
        if minimum is not None and int(minimum) > strength:
            speed_penalty = 10
        if bool(item.get("stealth_disadvantage")):
            stealth_disadvantage = True
    return {
        "untrained_armor_worn": untrained,
        "speed_penalty": speed_penalty,
        "stealth_disadvantage": stealth_disadvantage,
    }


class CharacterDerivationMixin:
    """Derive canonical mechanics only after the validation stage succeeds."""

    __slots__ = ()

    def derive(self, draft: dict[str, Any]) -> dict[str, Any]:
        errors = self.validate(draft)
        if errors:
            raise ValueError("; ".join(errors))

        level = int(draft.get("level", 1))
        prof = proficiency_bonus(level)
        class_entity = self._required_entity(draft["class_ref"], "class")
        species = self._required_entity(draft["species_ref"], "species")
        background = self._required_entity(draft["background_ref"], "background")
        package = self._required_entity(draft["equipment_package_ref"], "equipment_package")
        background_package_ref = str(draft.get("background_equipment_package_ref") or "")
        background_package = (
            self._required_entity(background_package_ref, "equipment_package")
            if background_package_ref else None
        )
        base = draft["base_abilities"]
        bonuses = draft["background_ability_bonuses"]
        abilities = {key: int(base[key]) + int(bonuses.get(key, 0) or 0) for key in ABILITY_IDS}

        background_feat_refs = [str(background["feat_ref"])] if background.get("feat_ref") else []
        species_feat_refs = list(draft.get("species_feat_refs") or [])
        class_feature_feat_choices = deepcopy(draft.get("class_feature_feat_choices") or {})
        class_feature_feat_refs = (
            [str(class_feature_feat_choices["fighting_style"])]
            if "fighting_style" in set(class_entity.get("level_1_feature_ids") or [])
            and isinstance(class_feature_feat_choices.get("fighting_style"), str)
            else []
        )
        feat_refs = list(dict.fromkeys([
            *background_feat_refs, *species_feat_refs, *class_feature_feat_refs,
        ]))
        feat_choice_answers = deepcopy(draft.get("feat_choice_answers") or {})
        skilled_refs = list(
            (feat_choice_answers.get("feat:skilled") or {}).get("proficiencies") or []
        )
        skilled_skill_refs = [ref for ref in skilled_refs if str(ref).startswith("skill:")]
        skilled_tool_refs = [ref for ref in skilled_refs if str(ref).startswith("tool:")]

        class_skills = list(draft["class_skill_refs"])
        background_skills = list(background.get("skill_refs") or [])
        species_skills = list(draft.get("species_skill_refs") or [])
        skill_refs = list(dict.fromkeys([
            *background_skills, *class_skills, *species_skills, *skilled_skill_refs,
        ]))
        features = Dnd2024ClassFeatureResolver(self.bundle)
        # A level-1 build can already own a skill choice (Rogue Expertise), so the
        # slot has to be validated before any skill value is derived: honouring an
        # unvalidated slot would pay out a doubled bonus for a selection the
        # level-up path would have rejected.
        creation_proxy = _feature_proxy(
            draft["class_ref"], level, draft.get("features"),
        )
        # 池记录先摘出去：`plan_skill_choices` 会把任何匹配不到技能槽的记录
        # 判成"这张卡没有这个槽"，而池子本来就由下一段单独校验。
        pool_keys = {
            (definition.owner, definition.id, definition.choice.id)
            for definition in features.features_for(creation_proxy)
            if definition.choice is not None and definition.choice.count_track
        }
        skill_submissions = [
            record for record in draft.get("feature_choices") or []
            if not (
                isinstance(record, dict)
                and (
                    str(record.get("owner") or ""),
                    str(record.get("feature_id") or ""),
                    str(record.get("choice_id") or ""),
                ) in pool_keys
            )
        ]
        feature_choices, choice_errors = plan_skill_choices(
            skill_submissions,
            features,
            creation_proxy,
            skill_refs,
            level=level,
        )
        pool_records, pool_errors = _plan_creation_pools(
            self.bundle, features, creation_proxy, draft, level,
        )
        if choice_errors or pool_errors:
            raise ValueError("; ".join([*choice_errors, *pool_errors]))
        feature_choices = [*feature_choices, *pool_records]
        expertise = {
            row["skill_ref"] for row in features.expertise_skills(
                _feature_proxy(
                    draft["class_ref"], level, draft.get("features"),
                    feature_choices=feature_choices,
                )
            )
        }
        half_proficiency = features.half_proficiency_bonus(
            _feature_proxy(
                draft["class_ref"], level, draft.get("features"),
                feature_choices=feature_choices,
            )
        )
        skill_values: dict[str, int] = {}
        all_skills = self.bundle.list("skill")
        for skill in all_skills:
            ref = f"skill:{skill['id']}"
            ability_id = _ref_id(str(skill["ability_ref"]), "ability")
            skill_values[str(skill["id"])] = derive_skill_check(
                int(abilities[ability_id]),
                proficiency_bonus=prof,
                proficient=ref in skill_refs,
                expertise=ref in expertise,
                half_proficiency=half_proficiency,
            )["total"]

        save_refs = list(class_entity.get("saving_throw_refs") or [])
        selected_packages = [package, *([background_package] if background_package else [])]
        item_refs = list(dict.fromkeys(
            ref
            for selected_package in selected_packages
            for ref in (selected_package.get("item_refs") or [])
        ))
        item_grants: list[dict[str, Any]] = []
        for selected_package in selected_packages:
            grants = selected_package.get("item_grants")
            if isinstance(grants, list):
                item_grants.extend(deepcopy(grants))
            else:
                item_grants.extend(
                    {"item_ref": ref, "quantity": 1}
                    for ref in (selected_package.get("item_refs") or [])
                )
        for ref in item_refs:
            # 建卡阶段仍要求装备包里每个引用都真实存在（validation 已保证）。
            self._required_entity(ref, "item")
        features = Dnd2024ClassFeatureResolver(self.bundle)
        feature_proxy = _feature_proxy(
            draft["class_ref"], level, draft.get("features"), item_refs,
            feature_choices,
        )
        # 全豁免熟练（Disciplined Survivor）由声明决定，不是第二张熟练表：
        # 与职业自带的两个豁免 ref 合并成一个集合，加值仍只按 PB 计一次。
        if features.saving_throw_proficiency_all(feature_proxy):
            save_refs = sorted({*save_refs, *(f"ability:{item}" for item in ABILITY_IDS)})
        # 单项豁免熟练（Slippery Mind）走同一个集合：合并不是追加，所以职业本来
        # 已经给的豁免不会因此多拿一次 PB；来源单独记在下面，集合本身看不出谁给的。
        feature_save_sources = features.saving_throw_proficiency_sources(feature_proxy)
        feature_save_refs = {f"ability:{ability}" for ability in feature_save_sources}
        if feature_save_refs:
            save_refs = sorted({*save_refs, *feature_save_refs})
        saving_throws = {
            ability_id: ability_modifier(abilities[ability_id])
            + (prof if f"ability:{ability_id}" in save_refs else 0)
            for ability_id in ABILITY_IDS
        }
        armor_category_refs = [
            f"armor_category:{item}"
            for item in class_entity.get("armor_proficiencies", [])
        ]
        armor_class = derive_armor_class(
            abilities, item_refs, self.bundle,
            armor_category_refs=armor_category_refs,
            unarmored_ability_bonuses=features.unarmored_ac_bonus_abilities(
                feature_proxy
            ),
            # 建卡已经能选 1 级 Fighting Style（Defense 是合法选项），这条
            # 路径必须与 live 协调一致：否则同一张卡在建卡时有 +1、换装后
            # 才出现，等于两套公式。
            feat_refs=feat_refs,
        )
        armor_training = derive_armor_training_state(
            abilities, item_refs, armor_category_refs, self.bundle,
        )
        derived_speed = derive_speed(
            int(species["speed"]),
            feature_bonus=features.speed_bonus(feature_proxy),
            armor_training=armor_training,
        )

        hit_die = int(class_entity["hit_die"])
        con_modifier = ability_modifier(abilities["con"])
        fixed_hp = int(class_entity.get("fixed_hp_per_level", max(1, hit_die // 2 + 1)))
        species_hp_bonus = int(species.get("hp_per_level_bonus", 0) or 0)
        max_hp = max(1, hit_die + con_modifier)
        max_hp += max(0, level - 1) * max(1, fixed_hp + con_modifier)
        max_hp += species_hp_bonus * level
        max_hp += features.feature_max_hp_bonus(feature_proxy)
        # 被动察觉就是 10 + 该技能检定的加值，因此直接复用聚合结果：Expertise
        # 与半熟练若在这里被漏掉，角色卡上会出现两套感知。
        passive_perception = 10 + int(skill_values.get("perception", 0) or 0)

        initiative = ability_modifier(abilities["dex"])
        if "feat:alert" in feat_refs:
            initiative += prof
        size_options = list(species.get("size_options") or [])
        selected_size = str(draft.get("species_size") or (size_options[0] if size_options else "medium"))
        language_refs = list(draft.get("language_refs") or [])
        background_tool_refs = list(background.get("tool_proficiency_refs") or [])
        class_tool_refs = [
            *list(class_entity.get("tool_proficiency_refs") or []),
            *list(draft.get("class_tool_refs") or []),
        ]
        tool_refs = list(dict.fromkeys([
            *background_tool_refs, *class_tool_refs, *skilled_tool_refs,
        ]))

        magic_initiate = []
        for feat_ref in feat_refs:
            feat = self.bundle.get("feat", _ref_id(feat_ref, "feat")) or {}
            if not feat.get("spell_list"):
                continue
            answers = feat_choice_answers.get(feat_ref) or {}
            magic_initiate.append({
                "feat_ref": feat_ref,
                "spell_list": str(feat["spell_list"]),
                "spellcasting_ability": (answers.get("spellcasting_ability") or [""])[0],
                "cantrip_ids": list(answers.get("cantrips") or []),
                "level_1_spell_id": (answers.get("level_1_spell") or [""])[0],
                "source_ref": str(feat.get("source_ref") or ""),
            })

        grants_with_sources = [
            *(
                {"kind": "skill_proficiency", "ref": ref, "source_ref": draft["background_ref"]}
                for ref in background_skills
            ),
            *(
                {"kind": "skill_proficiency", "ref": ref, "source_ref": draft["class_ref"]}
                for ref in class_skills
            ),
            *(
                {"kind": "skill_proficiency", "ref": ref, "source_ref": draft["species_ref"]}
                for ref in species_skills
            ),
            *(
                {"kind": "saving_throw_proficiency", "ref": ref, "source_ref": draft["class_ref"]}
                for ref in save_refs
                if ref not in feature_save_refs
            ),
            *(
                {
                    "kind": "saving_throw_proficiency",
                    "ref": f"ability:{ability}",
                    "source_ref": feature_id,
                }
                for ability, feature_id in feature_save_sources.items()
            ),
            *(
                {"kind": "tool_proficiency", "ref": ref, "source_ref": draft["background_ref"]}
                for ref in background_tool_refs
            ),
            *(
                {"kind": "tool_proficiency", "ref": ref, "source_ref": draft["class_ref"]}
                for ref in class_tool_refs
            ),
            *(
                {"kind": "feat", "ref": ref, "source_ref": draft["background_ref"]}
                for ref in background_feat_refs
            ),
            *(
                {"kind": "feat", "ref": ref, "source_ref": draft["species_ref"]}
                for ref in species_feat_refs
            ),
            *(
                {
                    "kind": "skill_proficiency" if ref.startswith("skill:") else "tool_proficiency",
                    "ref": ref,
                    "source_ref": "feat:skilled",
                }
                for ref in skilled_refs
            ),
        ]

        return {
            "rule_binding": rule_binding(self.bundle),
            "locale": self.bundle.locale,
            "identity": {
                "name": str(draft["name"]).strip(),
                "species_ref": draft["species_ref"],
                "background_ref": draft["background_ref"],
                "size": selected_size,
                "alignment": str(draft.get("alignment") or "neutral"),
            },
            "build": {
                "level": level,
                "class_levels": [{"class_ref": draft["class_ref"], "level": level}],
                "ability_method": draft.get("ability_method", "standard_array"),
                "base_abilities": deepcopy(base),
                "background_ability_bonuses": deepcopy(bonuses),
                "class_skill_refs": class_skills,
                "equipment_package_ref": draft["equipment_package_ref"],
                "background_equipment_package_ref": background_package_ref,
                "species_choice_answers": deepcopy(draft.get("species_choice_answers") or {}),
                "species_skill_refs": species_skills,
                "species_feat_refs": species_feat_refs,
                "class_feature_feat_choices": class_feature_feat_choices,
                "feat_choice_answers": feat_choice_answers,
                "class_tool_refs": list(draft.get("class_tool_refs") or []),
                "language_refs": language_refs,
                "grants_with_sources": grants_with_sources,
            },
            "abilities": abilities,
            "proficiencies": {
                "saving_throw_refs": save_refs,
                "armor_category_refs": armor_category_refs,
                "weapon_category_refs": [
                    f"weapon_category:{item}"
                    for item in class_entity.get("weapon_proficiencies", [])
                ],
                "skill_refs": skill_refs,
                "skill_values": skill_values,
                "tool_refs": tool_refs,
                "language_refs": language_refs,
            },
            "resources": {
                "hp": max_hp,
                "max_hp": max_hp,
                "hit_dice": {f"d{hit_die}": level},
            },
            "equipment": {
                "item_refs": item_refs,
                "item_grants": item_grants,
                "coins_gp": sum(int(item.get("coins_gp", 0) or 0) for item in selected_packages),
            },
            # 新角色从未同调过任何物品；容量是派生值（基础 3 + 特性加槽）。
            "attunement": {"refs": []},
            # S0-F25：已知形态是**角色级**选择，跨变形、跨战斗存在。这里只落
            # 建卡/升级时选好的 ref；没有选择时留空，校验层按推荐形态兜底。
            "forms": {
                "known": {
                    "wild_shape": [
                        str(ref) for ref in draft.get("known_form_refs") or []
                    ],
                },
            },
            "features": {
                "feat_refs": feat_refs,
                "feat_choice_answers": feat_choice_answers,
                # 技能选择与成长专长选择同住一个槽表：两者都是 (owner,
                # feature_id, choice_id) 三元组，分成两套账会让重建时只回放一半。
                "feature_choices": feature_choices,
                "species_traits": deepcopy(species.get("traits") or []),
            },
            "spellcasting": {"magic_initiate": magic_initiate},
            "derived": {
                "proficiency_bonus": prof,
                "armor_class": armor_class,
                "initiative": initiative,
                "speed": derived_speed,
                **derive_movement_profile(
                    derived_speed, abilities, features, feature_proxy,
                ),
                "armor_training": armor_training,
                "passive_perception": passive_perception,
                "saving_throws": saving_throws,
                "attunement_slots": _attunement_capacity(feature_proxy, features),
                "attuned_refs": [],
            },
            "sources": {
                "class_ref": draft["class_ref"],
                "species_ref": draft["species_ref"],
                "background_ref": draft["background_ref"],
                "content_entities": [
                    draft["class_ref"], draft["species_ref"], draft["background_ref"],
                    *skill_refs, *tool_refs, *feat_refs, *item_refs,
                ],
            },
        }
