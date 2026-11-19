"""S0-F23: Sacred Weapon, Smite of Protection, Abjure Foes, and Sear Undead.

Four features over two classes, and none of them is a standalone action: two
attach to an attack, one attaches to a cast, one attaches to a Channel use that
already existed.  The tests therefore drive the real intent pipeline --
``resolve_intent`` then ``apply_batch`` -- instead of calling the helpers, so a
rule that only holds inside the resolver cannot pass here.

Two conventions matter when reading the dice sequences:

* ``SequenceRng`` is consumed in the order the resolver asks, and the Initiative
  roll happens before any combat intent.  With two players the roll order is by
  actor id, so ``player:ally`` is asked before ``player:gm``.
* An attack that hits a creature the attacker could smite against opens a
  ``melee_hit_spell`` decision, and that decision blocks every other intent
  until it is resolved.  ``_attack`` declines it unless a test wants it.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import SequenceRng, advancement_choices, monk_draft
from test_dnd2024_survivor import _instance

# 15/14/13/12/10/8 各用一次：标准数组的唯一合法排列由 builder 校验。
PALADIN_ABILITIES = {"str": 13, "dex": 10, "con": 14, "int": 8, "wis": 12, "cha": 15}
# 魅力为负的圣武士：神圣武器的最低 +1 与斥退敌人的"至少一个目标"都靠它验证。
PALADIN_LOW_CHA = {"str": 15, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 8}
CLERIC_ABILITIES = {"str": 8, "dex": 12, "con": 14, "int": 10, "wis": 15, "cha": 13}
# 感知为负的牧师：灼烧亡灵固定一颗 d8。
CLERIC_LOW_WIS = {"str": 13, "dex": 12, "con": 14, "int": 10, "wis": 8, "cha": 15}
PALADIN_SKILLS = ["skill:athletics", "skill:persuasion"]
CLERIC_SKILLS = ["skill:history", "skill:medicine"]

MELEE = "item:longsword"
RANGED = "item:javelin"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _spell_choices(bundle, class_ref: str, level: int) -> dict:
    options = Dnd2024SpellSelection(bundle).options(class_ref, level)
    requirements = options["requirements"]
    return {
        "cantrip_refs": [row["ref"] for row in options["cantrips"]][
            : int(requirements["cantrip_count"])
        ],
        "prepared_spell_refs": [row["ref"] for row in options["leveled_spells"]][
            : int(requirements["prepared_spell_count"])
        ],
        "spellbook_refs": [],
    }


def _build(
    runtime: Dnd2024Runtime, bundle, class_ref: str, level: int,
    abilities: dict, skills: list[str], package: str, subclass: str = "",
) -> dict:
    """One professional character advanced through the real chain."""

    draft = monk_draft(runtime, "en")
    draft.update(
        class_ref=class_ref, class_skill_refs=skills,
        equipment_package_ref=package, base_abilities=abilities,
    )
    choices = runtime.builder_choices(None, draft)
    draft["class_tool_refs"] = [item["ref"] for item in choices["class_tools"]][
        : choices["class_tool_count"]
    ]
    draft["class_spell_choices"] = _spell_choices(bundle, class_ref, 1)
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        preview = runtime.preview_advancement(None, sheet, {})
        picks = advancement_choices(preview)
        picks["class_spell_choices"] = _spell_choices(bundle, class_ref, next_level)
        if subclass and "subclass_ref" in picks:
            picks["subclass_ref"] = subclass
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    return sheet


def _paladin_sheet(
    bundle, level: int, abilities: dict | None = None,
    subclass: str = "subclass:oath_of_devotion",
) -> dict:
    return _build(
        Dnd2024Runtime(), bundle, "class:paladin", level,
        abilities or PALADIN_ABILITIES, PALADIN_SKILLS,
        "equipment_package:paladin_a", subclass if level >= 3 else "",
    )


def _cleric_sheet(
    bundle, level: int, abilities: dict | None = None,
    subclass: str = "subclass:life_domain",
) -> dict:
    return _build(
        Dnd2024Runtime(), bundle, "class:cleric", level,
        abilities or CLERIC_ABILITIES, CLERIC_SKILLS,
        "equipment_package:cleric_a", subclass if level >= 3 else "",
    )


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle("en")


@pytest.fixture(scope="module")
def features(bundle):
    return Dnd2024ClassFeatureResolver(bundle)


@pytest.fixture(scope="module")
def sheets(bundle) -> dict[str, dict]:
    return {
        "paladin2": _paladin_sheet(bundle, 2),
        "paladin3": _paladin_sheet(bundle, 3),
        "paladin5": _paladin_sheet(bundle, 5),
        "paladin8": _paladin_sheet(bundle, 8),
        "paladin9": _paladin_sheet(bundle, 9),
        "paladin9_low_cha": _paladin_sheet(bundle, 9, PALADIN_LOW_CHA),
        "paladin14": _paladin_sheet(bundle, 14),
        "paladin15": _paladin_sheet(bundle, 15),
        "cleric4": _cleric_sheet(bundle, 4),
        "cleric5": _cleric_sheet(bundle, 5),
        "cleric5_low_wis": _cleric_sheet(bundle, 5, CLERIC_LOW_WIS),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _setup(sheet: dict) -> tuple[object, object]:
    return _instance(Dnd2024Runtime().load_bundle("en"), deepcopy(sheet))


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _conditions(instance, actor_id: str = "player:gm") -> dict:
    kind, raw = actor_id.split(":", 1)
    if kind == "player":
        return _canonical(instance, raw).setdefault("conditions", {})
    return ruleset_runtime.state(instance)["combat"]["enemies"][raw].setdefault(
        "conditions", {},
    )


def _hp(instance, actor_id: str = "player:gm") -> int:
    kind, raw = actor_id.split(":", 1)
    if kind == "player":
        return int(_canonical(instance, raw)["resources"]["hp"])
    return int(ruleset_runtime.state(instance)["combat"]["enemies"][raw]["hp"])


def _enemy(
    creature_type: str = "humanoid", hp: int = 80, position: int = 5,
    enemy_id: str = "goblin-1",
) -> dict:
    return {
        "id": enemy_id, "name": "Brute", "hp": hp, "armor_class": 12,
        "speed": 30, "position": position, "initiative_modifier": 5,
        "creature_type": creature_type,
        "abilities": {"str": 16, "dex": 10, "con": 14, "int": 8, "wis": 8, "cha": 8},
        "saving_throws": {"str": 3, "con": 2},
        "attacks": [
            {
                "id": "slam", "name": "Slam", "attack_bonus": 5,
                "damage": "1d6+2", "damage_type": "slashing", "range": 5,
            },
            # 掩蔽的用例要跨距离攻击，近战触及够不到灵光外的目标。
            {
                "id": "bow", "name": "Bow", "attack_bonus": 5,
                "damage": "1d6+2", "damage_type": "piercing", "range": 150,
            },
        ],
    }


def _add_ally(instance, sheet: dict, uid: str = "ally") -> None:
    instance.players[uid] = {"character_name": "Bo", "character_sheet": deepcopy(sheet)}


def _start(engine, instance, rolls=(20, 3), enemies=None, intent_id="start-1") -> list[dict]:
    state = ruleset_runtime.state(instance)
    resolved = engine.resolve_intent(instance, {
        "intent_id": intent_id, "type": "combat.start",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "enemies": enemies or [_enemy()],
    }, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _intent(instance, actor_id: str = "player:gm", **body) -> dict:
    state = ruleset_runtime.state(instance)
    # 玩家只能替自己的角色提交意图，敌人由 GM 提交。
    submitted_by = (
        actor_id.split(":", 1)[1] if actor_id.startswith("player:") else "gm"
    )
    return {
        "intent_id": f"{body.get('type')}-{state['version']}-{actor_id}",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": submitted_by, "actor_id": actor_id, **body,
    }


def _apply(engine, instance, resolved: dict) -> list[dict]:
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _attack(
    engine, instance, weapon_ref: str = MELEE, dice=None, *,
    target_id: str = "enemy:goblin-1", decline: bool = True,
    actor_id: str = "player:gm", **extra,
) -> tuple[list[dict], list[dict]]:
    """One attack through the pipeline, with its hit-spell decision declined.

    The five default d20/damage faces are enough for a longsword hit against
    AC 12 and are all "1" beyond the first so the damage is predictable.
    """

    resolved = engine.resolve_intent(instance, _intent(
        instance, actor_id=actor_id, type="attack", target_id=target_id,
        weapon_ref=weapon_ref, **extra,
    ), SequenceRng(list(dice or [15, 3, 3, 3, 3])))
    events = _apply(engine, instance, resolved)
    declined: list[dict] = []
    if decline:
        declined = _decline_hit_spell(engine, instance, actor_id)
    return events, declined


def _decline_hit_spell(
    engine, instance, actor_id: str = "player:gm",
) -> list[dict]:
    events: list[dict] = []
    for decision in list(
        ruleset_runtime.state(instance)["combat"].get("pending_decisions") or []
    ):
        if decision.get("kind") != "melee_hit_spell":
            continue
        if str(decision.get("actor_id") or actor_id) != actor_id:
            continue
        resolved = engine.resolve_intent(instance, _intent(
            instance, actor_id=actor_id, type="decision.resolve",
            decision_id=decision["decision_id"], option="decline",
        ), SequenceRng([]))
        events.extend(_apply(engine, instance, resolved))
    return events


def _move(engine, instance, distance: int = 5, actor_id: str = "player:gm") -> dict:
    """One move intent, applied when it is legal.

    The "already moved" marker only exists once the batch is applied, and a
    move that leaves a threat's reach applies nothing until its opportunity
    attack is settled, so both halves run here.
    """

    resolved = engine.resolve_intent(instance, _intent(
        instance, actor_id=actor_id, type="move", distance=distance,
    ), SequenceRng([]))
    if resolved["ok"]:
        engine.apply_batch(instance, resolved["event_batch"])
        _settle_pending(engine, instance)
    return resolved


def _settle_pending(engine, instance, option: str = "decline") -> list[dict]:
    """Answer every pending decision the way an uninterested GM would."""

    events: list[dict] = []
    while True:
        pending = _combat(instance).get("pending_decisions") or []
        if not pending:
            return events
        state = ruleset_runtime.state(instance)
        resolved = engine.resolve_intent(instance, {
            "intent_id": f"settle-{state['version']}",
            "expected_version": int(state.get("version", 0) or 0),
            "submitted_by": "gm", "type": "decision.resolve",
            "decision_id": pending[0]["decision_id"], "option": option,
        }, SequenceRng([]))
        events.extend(_apply(engine, instance, resolved))


def _end_turn(engine, instance, actor_id: str | None = None) -> list[dict]:
    if actor_id is None:
        actor_id = str(
            ruleset_runtime.state(instance)["combat"]["economy"].get("actor_id") or ""
        )
    return _apply(engine, instance, engine.resolve_intent(instance, _intent(
        instance, actor_id=actor_id, type="end_turn",
    ), SequenceRng([])))


def _capability(engine, instance, capability_id: str, dice=(), **extra) -> dict:
    return engine.resolve_intent(instance, _intent(
        instance, type="class_capability", capability_id=capability_id, **extra,
    ), SequenceRng(list(dice)))


def _to_turn(engine, instance, actor_id: str, limit: int = 16) -> None:
    """End turns until ``actor_id`` is the current actor."""

    for _ in range(limit):
        current = str(_combat(instance)["economy"].get("actor_id") or "")
        if current == actor_id:
            return
        _end_turn(engine, instance, current)
    raise AssertionError(f"{actor_id} never got a turn")


def _next_turn(engine, instance, actor_id: str) -> None:
    """End ``actor_id``'s current turn and run the round until it comes back."""

    _end_turn(engine, instance, actor_id)
    _to_turn(engine, instance, actor_id)


def _with_ammo(sheet: dict, count: int = 20) -> dict:
    """The same character with a quiver in the live inventory.

    Ammunition is not an equipment row: the attack pipeline counts copies from
    ``inventory`` and spends them with ``take_from_inventory``, so a case about
    a bow has to put the arrows where that path looks.
    """

    live = deepcopy(sheet)
    live["inventory"] = [
        *(live.get("inventory") or []), {"item_ref": "item:arrows", "qty": count},
    ]
    return live


def _with_item(sheet: dict, bundle, name: str, qty: int = 1) -> dict:
    """The same character, now also carrying one more item.

    Buying and equipping is the only path a new weapon ref can enter combat
    through, so a case about a ranged weapon has to go through the real
    reconciliation instead of an invented ``equipment_refs`` list.
    """

    live = deepcopy(sheet)
    reconciler = Dnd2024CharacterStateReconciler(bundle, locale_bundles=(bundle,))
    reconciler.reconcile(live)
    live["equipment"] = [
        *live["equipment"], {"name": name, "qty": qty, "category": "equipment"},
    ]
    assert reconciler.reconcile(live) is not None
    return live


def _combat(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _of_type(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("type") == kind]


def _inject(engine, instance, events: list[dict]) -> None:
    """Apply raw events through the same reducer ``apply_batch`` uses.

    Nothing in the SRD content imbues a weapon, halves radiant damage, or hands
    an enemy an extra action, so those facts are written straight into the
    ledger.  The assertions still run against the real reducer.
    """

    from src.rulesets.events import stable_batch_id

    version = int(ruleset_runtime.state(instance).get("version", 0) or 0)
    intent = {
        "intent_id": f"inject-{version}", "type": "combat.message",
        "expected_version": version,
    }
    batch = {
        "batch_id": stable_batch_id(intent, version),
        "intent_id": intent["intent_id"], "intent_type": "combat.message",
        "expected_version": version, "result_version": version + 1,
        "events": list(events), "source_ref": "srd-5.2.1:p37:sear-undead",
    }
    assert engine.apply_batch(instance, batch)["applied"] is True


def _pool(instance, resource_id: str = "channel_divinity", uid: str = "gm") -> dict:
    return _canonical(instance, uid)["resources"]["class"][resource_id]


def _positions(instance, **positions: int) -> None:
    """Place actors directly: movement minutes are not what these cases test."""

    _combat(instance)["positions"].update(positions)


# ---------------------------------------------------------------------------
# 1. grants: each feature appears only at its own level and owner
# ---------------------------------------------------------------------------


def test_the_four_features_appear_only_at_their_own_level(sheets, features) -> None:
    # Sacred Weapon and Smite of Protection are Oath of Devotion; Abjure Foes is
    # base Paladin; Sear Undead is base Cleric.
    assert features.has_feature(sheets["paladin3"]["ruleset_character"], "sacred_weapon")
    assert not features.has_feature(
        sheets["paladin8"]["ruleset_character"], "abjure_foes",
    )
    assert features.has_feature(sheets["paladin9"]["ruleset_character"], "abjure_foes")
    assert not features.has_feature(
        sheets["paladin14"]["ruleset_character"], "smite_of_protection",
    )
    assert features.has_feature(
        sheets["paladin15"]["ruleset_character"], "smite_of_protection",
    )
    assert not features.has_feature(sheets["cleric4"]["ruleset_character"], "sear_undead")
    assert features.has_feature(sheets["cleric5"]["ruleset_character"], "sear_undead")


def test_the_subclass_features_need_the_subclass(bundle, features) -> None:
    # Sacred Weapon 与 Smite of Protection 由子职授予，不由职业授予：3 级之前
    # 圣武士还没有子职，所以两个特性都不存在。SRD 只提供 Devotion 一个圣武士
    # 子职，因此"别的子职没有"只能由授予方（子职表）本身回答。
    assert not features.has_feature(
        _paladin_sheet(bundle, 2)["ruleset_character"], "sacred_weapon",
    )
    cleric = _cleric_sheet(bundle, 9)
    assert not features.has_feature(cleric["ruleset_character"], "sacred_weapon")
    assert not features.has_feature(
        cleric["ruleset_character"], "smite_of_protection",
    )


def test_abjure_foes_is_the_only_new_capability(sheets, features) -> None:
    economy = {"action": 1, "bonus_action": 1}
    paladin = {
        view.id for view in features.combat_capabilities(
            sheets["paladin9"]["ruleset_character"], economy, actor_id="player:gm",
        )
    }
    assert "abjure_foes" in paladin
    # 神圣武器与守护至圣斩都不是独立能力：一个挂在攻击意图上，一个挂在施法上。
    assert "sacred_weapon" not in paladin
    assert "smite_of_protection" not in paladin

    cleric = {
        view.id for view in features.combat_capabilities(
            sheets["cleric5"]["ruleset_character"], economy, actor_id="player:gm",
        )
    }
    assert "turn_undead" in cleric
    assert "sear_undead" not in cleric


# ---------------------------------------------------------------------------
# 2. Sacred Weapon: activation inside a real Attack action
# ---------------------------------------------------------------------------


def test_sacred_weapon_is_activated_by_the_attack_that_uses_it(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    events, _ = _attack(engine, instance, MELEE, activate_sacred_weapon=True)

    spent = _of_type(events, "dnd2024.class_resource.spent")
    assert [row["resource_id"] for row in spent] == ["channel_divinity"]
    assert _pool(instance)["current"] == 1

    applied = [
        row for row in _of_type(events, "condition.applied")
        if row["condition"] == "sacred_weapon"
    ]
    assert len(applied) == 1
    assert applied[0]["weapon_ref"] == MELEE
    assert applied[0]["feature_id"] == "sacred_weapon"
    # 激活与首个攻击是同一个动作：这一掷已经吃到加值。
    check = _of_type(events, "check.resolved")[0]
    assert check["sacred_weapon_bonus"] == 2
    assert check["modifier"] == 1 + 2 + 2


def test_sacred_weapon_cannot_be_added_after_the_first_attack(sheets) -> None:
    engine, instance = _setup(sheets["paladin5"])  # Extra Attack: a second swing exists
    _start(engine, instance)
    _attack(engine, instance, MELEE)
    resolved = engine.resolve_intent(instance, _intent(
        instance, type="attack", target_id="enemy:goblin-1", weapon_ref=MELEE,
        activate_sacred_weapon=True,
    ), SequenceRng([15, 3, 3, 3, 3]))
    assert resolved["ok"] is False
    assert "not after its first attack" in resolved["error"]
    assert _pool(instance)["current"] == 2


def test_a_dodge_cannot_smuggle_a_sacred_weapon_activation(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    events = _apply(engine, instance, engine.resolve_intent(instance, _intent(
        instance, type="dodge", activate_sacred_weapon=True,
    ), SequenceRng([])))
    assert _of_type(events, "check.resolved") == []
    assert "sacred_weapon" not in _conditions(instance)
    assert _pool(instance)["current"] == 2


def test_sacred_weapon_needs_an_unspent_channel_use(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    _pool(instance)["current"] = 0
    resolved = engine.resolve_intent(instance, _intent(
        instance, type="attack", target_id="enemy:goblin-1", weapon_ref=MELEE,
        activate_sacred_weapon=True,
    ), SequenceRng([15, 3, 3, 3, 3]))
    assert resolved["ok"] is False
    assert "channel divinity is already spent" in resolved["error"]


# ---------------------------------------------------------------------------
# 3. Sacred Weapon: only the held Melee weapon it names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("weapon_ref", ["unarmed_strike"])
def test_only_a_melee_weapon_can_be_imbued(sheets, weapon_ref) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    resolved = engine.resolve_intent(instance, _intent(
        instance, type="attack", target_id="enemy:goblin-1", weapon_ref=weapon_ref,
        activate_sacred_weapon=True,
    ), SequenceRng([15, 3, 3, 3, 3]))
    assert resolved["ok"] is False
    assert "Melee weapon" in resolved["error"]


def _without_shield(sheet: dict) -> dict:
    """The same character with the shield taken off.

    长弓是 Two-Handed：盾牌绑在手臂上时它根本握不住，握持校验会先拒绝，
    那样就测不到"远程武器不能被祝圣"这条。所以这个用例先把盾牌脱下。
    """

    live = deepcopy(sheet)
    canonical = live["ruleset_character"]
    canonical["equipment"]["item_refs"] = [
        ref for ref in canonical["equipment"]["item_refs"] if ref != "item:shield"
    ]
    live["equipment"] = [
        row for row in live.get("equipment") or []
        if "item_ref" not in row or row.get("item_ref") != "item:shield"
    ]
    return live


def test_a_ranged_weapon_cannot_be_imbued(sheets, bundle) -> None:
    # 标枪是带 Thrown 的近战武器，可以绑定；真正的远程武器不行。
    sheet = _without_shield(_with_ammo(_with_item(sheets["paladin3"], bundle, "Longbow")))
    refs = sheet["ruleset_character"]["equipment"]["item_refs"]
    assert "item:longbow" in refs
    engine, instance = _setup(sheet)
    _start(engine, instance)
    resolved = engine.resolve_intent(instance, _intent(
        instance, type="attack", target_id="enemy:goblin-1", weapon_ref="item:longbow",
        activate_sacred_weapon=True,
    ), SequenceRng([15, 3, 3, 3, 3]))
    assert resolved["ok"] is False
    assert "Melee weapon" in resolved["error"]


def test_a_weapon_the_actor_does_not_carry_is_refused(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    resolved = engine.resolve_intent(instance, _intent(
        instance, type="attack", target_id="enemy:goblin-1", weapon_ref="item:greataxe",
        activate_sacred_weapon=True,
    ), SequenceRng([15, 3, 3, 3, 3]))
    assert resolved["ok"] is False
    assert "attack profile is not available" in resolved["error"]


def test_a_thrown_melee_weapon_is_still_a_melee_weapon(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    _attack(engine, instance, RANGED, activate_sacred_weapon=True)
    assert _conditions(instance)["sacred_weapon"]["weapon_ref"] == RANGED


def test_the_bonus_follows_the_ref_not_the_weapon_name(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _add_ally(instance, sheets["paladin3"])
    _start(engine, instance, rolls=(3, 20, 8))
    _attack(engine, instance, MELEE, activate_sacred_weapon=True)

    # 盟友手里也是一把 item:longsword，但它没有绑定：同一件物品的引用不等于
    # 同一份祝福。
    _to_turn(engine, instance, "player:ally")
    resolved = engine.resolve_intent(instance, _intent(
        instance, actor_id="player:ally", type="attack",
        target_id="enemy:goblin-1", weapon_ref=MELEE,
    ), SequenceRng([15, 3, 3, 3, 3]))
    events = _apply(engine, instance, resolved)
    _decline_hit_spell(engine, instance, "player:ally")
    check = _of_type(events, "check.resolved")[0]
    # 力量 13 → +1，熟练 +2；没有任何神圣武器加值。
    assert check["modifier"] == 3
    assert "sacred_weapon_bonus" not in check


def test_a_negative_charisma_still_grants_plus_one(sheets) -> None:
    sheet = sheets["paladin9_low_cha"]
    engine, instance = _setup(sheet)
    _start(engine, instance)
    events, _ = _attack(engine, instance, MELEE, activate_sacred_weapon=True)
    check = _of_type(events, "check.resolved")[0]
    assert check["sacred_weapon_bonus"] == 1

    # 同一角色、同一掷，不激活时少 1：加值确实是神圣武器给的。
    plain_engine, plain = _setup(sheet)
    _start(plain_engine, plain)
    baseline, _ = _attack(plain_engine, plain, MELEE)
    assert check["modifier"] == _of_type(baseline, "check.resolved")[0]["modifier"] + 1


def test_a_miss_keeps_the_bond(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    events, _ = _attack(engine, instance, MELEE, [2, 3, 3], activate_sacred_weapon=True)
    check = _of_type(events, "check.resolved")[0]
    assert check["success"] is False
    assert _conditions(instance)["sacred_weapon"]["weapon_ref"] == MELEE


# ---------------------------------------------------------------------------
# 4. Sacred Weapon: damage type choice
# ---------------------------------------------------------------------------


def test_a_hit_may_deal_radiant_instead_of_the_weapon_type(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    # 命中会开一个至圣斩决策：伤害被挂起，拒绝之后才真正落地。
    _events, declined = _attack(
        engine, instance, MELEE, activate_sacred_weapon=True,
        sacred_weapon_damage="radiant",
    )
    damage = [
        row for row in _of_type(declined, "resource.changed")
        if row.get("resource") == "hp" and row["amount"] > 0
    ]
    assert [row["damage_type"] for row in damage] == ["radiant"]
    # 类型变了，骰数没变：武器仍然是一颗 d8。
    assert damage[0]["rolls"] == [3]


def test_the_default_keeps_the_weapon_damage_type(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    _events, declined = _attack(engine, instance, MELEE, activate_sacred_weapon=True)
    damage = [
        row for row in _of_type(declined, "resource.changed")
        if row.get("resource") == "hp" and row["amount"] > 0
    ]
    assert damage[0]["damage_type"] == "slashing"


def test_the_damage_choice_needs_a_bond(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    resolved = engine.resolve_intent(instance, _intent(
        instance, type="attack", target_id="enemy:goblin-1", weapon_ref=MELEE,
        sacred_weapon_damage="radiant",
    ), SequenceRng([15, 3, 3, 3, 3]))
    assert resolved["ok"] is False
    assert "no sacred weapon bond" in resolved["error"]


# ---------------------------------------------------------------------------
# 5. Sacred Weapon: ending the bond
# ---------------------------------------------------------------------------


def test_recasting_ends_the_old_bond_before_the_new_one(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    _attack(engine, instance, MELEE, activate_sacred_weapon=True)
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")

    events, _ = _attack(engine, instance, MELEE, activate_sacred_weapon=True)
    removed = [
        row for row in _of_type(events, "condition.removed")
        if row["condition"] == "sacred_weapon"
    ]
    assert removed and removed[0]["reason"] == "sacred_weapon_recast"
    applied = [
        row for row in _of_type(events, "condition.applied")
        if row["condition"] == "sacred_weapon"
    ]
    assert len(applied) == 1
    assert events.index(removed[0]) < events.index(applied[0])


def test_dropping_the_weapon_ends_the_bond_and_picking_it_up_does_not(sheets, bundle) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    _attack(engine, instance, MELEE, activate_sacred_weapon=True)
    live = deepcopy(instance.get_character_sheet("gm"))
    assert live["ruleset_character"]["conditions"]["sacred_weapon"]["weapon_ref"] == MELEE

    reconciler = Dnd2024CharacterStateReconciler(bundle, locale_bundles=(bundle,))
    reconciler.reconcile(live)  # 先把 live 行标注出规则身份
    dropped = [
        row for row in live["equipment"] if row.get("item_ref") != MELEE
    ]
    assert len(dropped) < len(live["equipment"])
    live["equipment"] = dropped
    result = reconciler.reconcile(live)
    assert result is not None
    assert result["ended_weapon_bindings"] == [MELEE]
    assert "sacred_weapon" not in live["ruleset_character"]["conditions"]

    # 重新装备同一把武器不会把祝福还回来。
    live["equipment"] = [
        *dropped, {"name": "Longsword", "qty": 1, "category": "equipment"},
    ]
    again = reconciler.reconcile(live)
    assert again is not None
    assert "sacred_weapon" not in live["ruleset_character"]["conditions"]


def test_the_bond_lasts_ten_minutes(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    instance.world_state = {"clock": {"day": 1, "minute": 30}}
    _start(engine, instance)
    _attack(engine, instance, MELEE, activate_sacred_weapon=True)
    assert _conditions(instance)["sacred_weapon"]["expires_at_minute"] == 40

    instance.world_state = {"clock": {"day": 1, "minute": 39}}
    events = _end_turn(engine, instance, "player:gm")
    assert "sacred_weapon" in _conditions(instance)

    instance.world_state = {"clock": {"day": 1, "minute": 40}}
    events = _end_turn(engine, instance, "enemy:goblin-1")
    removed = [
        row for row in _of_type(events, "condition.removed")
        if row["condition"] == "sacred_weapon"
    ]
    assert removed and removed[0]["reason"] == "ten minutes passed"
    assert "sacred_weapon" not in _conditions(instance)


# ---------------------------------------------------------------------------
# 6. Sacred Weapon: the light is a guided fact and nothing more
# ---------------------------------------------------------------------------


def test_the_light_fields_are_recorded_as_guided_only(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    _attack(engine, instance, MELEE, activate_sacred_weapon=True)
    meta = _conditions(instance)["sacred_weapon"]
    assert meta["bright_light_radius"] == 20
    assert meta["dim_light_beyond"] == 20
    assert meta["light_automation"] == "guided"

    # 没有光照系统时它什么都不改：AC、攻击加值与 Hide 的可用性都不受影响。
    view = engine._actor_view(instance, _combat(instance), "player:gm")
    assert view["armor_class"] == int(
        _canonical(instance)["derived"]["armor_class"]
    )
    assert "invisible" not in _conditions(instance)


# ---------------------------------------------------------------------------
# 7. Smite of Protection
# ---------------------------------------------------------------------------


def _cast_smite(
    engine, instance, slot_level: int = 1, target_id: str = "enemy:goblin-1",
) -> list[dict]:
    resolved = engine.resolve_intent(instance, _intent(
        instance, type="cast_spell", spell_ref="spell:divine_smite",
        target_id=target_id, slot_level=slot_level,
    ), SequenceRng([3, 4, 5, 6, 1, 2]))
    return _apply(engine, instance, resolved)


def test_a_divine_smite_cast_opens_half_cover_without_a_channel_use(sheets) -> None:
    engine, instance = _setup(sheets["paladin15"])
    _start(engine, instance)
    _attack(engine, instance, MELEE, decline=False)
    before = _pool(instance)["current"]
    events = _cast_smite(engine, instance)

    cover = [
        row for row in _of_type(events, "condition.applied")
        if row["condition"] == "smite_of_protection"
    ]
    assert len(cover) == 1
    assert cover[0]["source_spell_ref"] == "spell:divine_smite"
    assert cover[0]["feature_id"] == "smite_of_protection"
    assert cover[0]["duration"] == "actor_turn_start"
    assert _pool(instance)["current"] == before  # 不扣 Channel，也不扣额外资源


def test_a_free_divine_smite_also_opens_half_cover(sheets) -> None:
    engine, instance = _setup(sheets["paladin15"])
    _start(engine, instance)
    _attack(engine, instance, MELEE, decline=False)
    events = _cast_smite(engine, instance, slot_level=0)
    cover = [
        row for row in _of_type(events, "condition.applied")
        if row["condition"] == "smite_of_protection"
    ]
    # 免费施法同样是一次真实施放：走的是同一条施法事件链。
    assert len(cover) == 1
    assert _of_type(events, "dnd2024.feat_spell.free_cast_spent") or _of_type(
        events, "dnd2024.class_resource.spent",
    ) or _of_type(events, "dnd2024.free_cast.spent")


def test_declining_the_smite_does_not_open_half_cover(sheets) -> None:
    engine, instance = _setup(sheets["paladin15"])
    _start(engine, instance)
    events, declined = _attack(engine, instance, MELEE, decline=False)
    assert _of_type(events, "dnd2024.decision.pending")
    declined = _decline_hit_spell(engine, instance)
    assert declined
    assert "smite_of_protection" not in _conditions(instance)


def test_half_cover_raises_the_ac_of_everyone_in_the_aura(sheets) -> None:
    engine, instance = _setup(sheets["paladin15"])
    _add_ally(instance, sheets["paladin3"])
    _start(engine, instance, rolls=(3, 20, 8))
    _positions(instance, **{"player:gm": 0, "player:ally": 5, "enemy:goblin-1": 5})
    _attack(engine, instance, MELEE, decline=False)
    _cast_smite(engine, instance)
    combat = _combat(instance)

    # 灵光内的两个人都被查询到，持有者自己也包括在内。
    for actor_id in ("player:gm", "player:ally"):
        view = engine._actor_view(instance, combat, actor_id)
        cover = engine._cover_record(instance, combat, view)
        assert cover["armor_class_bonus"] == 2
        assert cover["dex_save_bonus"] == 2
        assert cover["sources"][0]["source_actor_id"] == "player:gm"

    # 真正落地的攻击对着被抬高 2 点的 AC。
    _to_turn(engine, instance, "enemy:goblin-1")
    ally = engine._actor_view(instance, _combat(instance), "player:ally")
    resolved = engine.resolve_intent(instance, _intent(
        instance, actor_id="enemy:goblin-1", type="attack",
        target_id="player:ally", attack_id="bow",
    ), SequenceRng([2, 3]))
    events = _apply(engine, instance, resolved)
    check = _of_type(events, "check.resolved")[0]
    assert check["cover_bonus"] == 2
    assert check["target"] == int(ally["armor_class"]) + 2


def test_half_cover_follows_the_aura_not_a_snapshot(sheets) -> None:
    engine, instance = _setup(sheets["paladin15"])
    _add_ally(instance, sheets["paladin3"])
    _start(engine, instance, rolls=(3, 20, 8))
    _positions(instance, **{"player:gm": 0, "player:ally": 20, "enemy:goblin-1": 5})
    _attack(engine, instance, MELEE, decline=False)
    _cast_smite(engine, instance)
    combat = _combat(instance)

    outside = engine._cover_record(
        instance, combat, engine._actor_view(instance, combat, "player:ally"),
    )
    assert outside == {
        "level": "none", "armor_class_bonus": 0, "dex_save_bonus": 0, "sources": [],
    }

    # 走进灵光、且效果未到期：掩蔽恢复。走出去又失去，因为灵光是唯一来源。
    _positions(instance, **{"player:ally": 5})
    inside = engine._cover_record(
        instance, combat, engine._actor_view(instance, combat, "player:ally"),
    )
    assert inside["armor_class_bonus"] == 2

    # 敌人从来拿不到圣武士给的掩蔽。
    enemy = engine._actor_view(instance, combat, "enemy:goblin-1")
    assert engine._cover_record(instance, combat, enemy)["armor_class_bonus"] == 0


def test_an_incapacitated_paladin_grants_no_cover(sheets) -> None:
    engine, instance = _setup(sheets["paladin15"])
    _start(engine, instance)
    _attack(engine, instance, MELEE, decline=False)
    _cast_smite(engine, instance)
    _inject(engine, instance, [{
        "type": "condition.applied", "target_id": "player:gm", "condition": "stunned",
    }])
    cover = engine._cover_record(instance, _combat(instance), engine._actor_view(
        instance, _combat(instance), "player:gm",
    ))
    assert cover == {
        "level": "none", "armor_class_bonus": 0, "dex_save_bonus": 0, "sources": [],
    }


def test_half_cover_ends_at_the_start_of_the_next_turn(sheets) -> None:
    engine, instance = _setup(sheets["paladin15"])
    _start(engine, instance)
    _attack(engine, instance, MELEE, decline=False)
    _cast_smite(engine, instance)
    assert "smite_of_protection" in _conditions(instance)

    _end_turn(engine, instance, "player:gm")
    assert "smite_of_protection" in _conditions(instance)
    _end_turn(engine, instance, "enemy:goblin-1")
    # 敌人回合结束、圣武士的下一回合开始：效果准时结束。
    assert "smite_of_protection" not in _conditions(instance)


def test_a_second_cast_leaves_one_instance_not_two(sheets) -> None:
    engine, instance = _setup(sheets["paladin15"])
    _start(engine, instance, enemies=[
        _enemy(position=5, enemy_id="goblin-1"),
        _enemy(position=5, enemy_id="goblin-2"),
    ])
    _attack(engine, instance, MELEE, decline=False)
    _cast_smite(engine, instance)
    # 圣斩要花附赠动作，一回合里不可能有第二次；第二次施法只能跨回合，
    # 而前一份会在本方回合开始时准时到期。
    _next_turn(engine, instance, "player:gm")
    assert "smite_of_protection" not in _conditions(instance)

    _attack(engine, instance, MELEE, target_id="enemy:goblin-2", decline=False)
    _cast_smite(engine, instance, target_id="enemy:goblin-2")
    # 同 id 覆盖：只有一份实例，加值仍是 +2，不是 +4。
    assert list(_conditions(instance)).count("smite_of_protection") == 1
    holder = engine._actor_view(instance, _combat(instance), "player:gm")
    cover = engine._cover_record(instance, _combat(instance), holder)
    assert cover["armor_class_bonus"] == 2
    assert len(cover["sources"]) == 1


# ---------------------------------------------------------------------------
# 8. Abjure Foes: targets, cost, saves
# ---------------------------------------------------------------------------


def _two_enemies() -> list[dict]:
    return [
        _enemy(position=5, enemy_id="goblin-1"),
        _enemy(position=10, enemy_id="goblin-2"),
    ]


def test_abjure_foes_spends_one_channel_for_exactly_charisma_targets(sheets) -> None:
    engine, instance = _setup(sheets["paladin9"])
    _start(engine, instance, enemies=_two_enemies())
    events = _apply(engine, instance, _capability(
        engine, instance, "abjure_foes", [3, 3],
        target_ids=["enemy:goblin-1", "enemy:goblin-2"],
    ))
    assert _pool(instance)["current"] == 1
    spend = _of_type(events, "dnd2024.class_resource.spent")
    assert [row["amount"] for row in spend] == [1]
    assert len(_of_type(events, "check.resolved")) == 2
    assert _of_type(events, "dnd2024.action.spent")


def test_a_short_or_long_target_list_is_refused_whole(sheets) -> None:
    engine, instance = _setup(sheets["paladin9"])
    _start(engine, instance, enemies=_two_enemies())
    for targets in (
        ["enemy:goblin-1"],
        ["enemy:goblin-1", "enemy:goblin-2", "enemy:goblin-1"],
    ):
        resolved = _capability(engine, instance, "abjure_foes", [3, 3], target_ids=targets)
        assert resolved["ok"] is False
        assert "exactly 2" in resolved["error"] or "must not repeat" in resolved["error"]
    assert _pool(instance)["current"] == 2


def test_duplicate_and_out_of_range_targets_are_refused(sheets) -> None:
    engine, instance = _setup(sheets["paladin9"])
    _start(engine, instance, enemies=_two_enemies())
    resolved = _capability(
        engine, instance, "abjure_foes", [3, 3],
        target_ids=["enemy:goblin-1", "enemy:goblin-1"],
    )
    assert resolved["ok"] is False
    assert "must not repeat" in resolved["error"]

    _positions(instance, **{"enemy:goblin-2": 65})
    resolved = _capability(
        engine, instance, "abjure_foes", [3, 3],
        target_ids=["enemy:goblin-1", "enemy:goblin-2"],
    )
    assert resolved["ok"] is False
    assert "out of range" in resolved["error"]


def test_an_unseen_target_is_refused(sheets) -> None:
    engine, instance = _setup(sheets["paladin9"])
    _start(engine, instance, enemies=_two_enemies())
    _inject(engine, instance, [{
        "type": "condition.applied", "target_id": "enemy:goblin-2",
        "condition": "invisible",
    }])
    resolved = _capability(
        engine, instance, "abjure_foes", [3, 3],
        target_ids=["enemy:goblin-1", "enemy:goblin-2"],
    )
    assert resolved["ok"] is False
    assert "you can see" in resolved["error"]


def test_each_target_saves_on_its_own_against_the_paladin_dc(sheets) -> None:
    engine, instance = _setup(sheets["paladin9"])
    _start(engine, instance, enemies=_two_enemies())
    actor = engine._actor_view(instance, _combat(instance), "player:gm")
    events = _apply(engine, instance, _capability(
        engine, instance, "abjure_foes", [2, 20],
        target_ids=["enemy:goblin-1", "enemy:goblin-2"],
    ))
    checks = _of_type(events, "check.resolved")
    assert [row["success"] for row in checks] == [False, True]
    assert all(row["target"] == actor["spell_save_dc"] for row in checks)
    assert all(row["ability"] == "wis" for row in checks)
    assert "frightened" in _conditions(instance, "enemy:goblin-1")
    assert "frightened" not in _conditions(instance, "enemy:goblin-2")


def test_the_frightened_carries_the_three_way_choice(sheets) -> None:
    engine, instance = _setup(sheets["paladin9"])
    _start(engine, instance, enemies=_two_enemies())
    _apply(engine, instance, _capability(
        engine, instance, "abjure_foes", [2, 2],
        target_ids=["enemy:goblin-1", "enemy:goblin-2"],
    ))
    meta = _conditions(instance, "enemy:goblin-1")["frightened"]
    assert meta["turn_choice_limit"] == ["move", "action", "bonus_action"]
    assert meta["duration"] == "abjure_foes"
    assert meta["turns_remaining"] == 10
    assert meta["ends_on_damage"] is True


# ---------------------------------------------------------------------------
# 9. Abjure Foes: one of the three per turn
# ---------------------------------------------------------------------------


def _abjure_and_reach_enemy_turn(sheets, *, enemies=None) -> tuple[object, object]:
    engine, instance = _setup(sheets["paladin9"])
    _start(engine, instance, enemies=enemies or _two_enemies())
    _apply(engine, instance, _capability(
        engine, instance, "abjure_foes", [2, 2],
        target_ids=["enemy:goblin-1", "enemy:goblin-2"],
    ))
    while _combat(instance)["economy"]["actor_id"] != "enemy:goblin-1":
        assert _end_turn(engine, instance)
    return engine, instance


def test_moving_first_costs_the_action_and_the_bonus_action(sheets) -> None:
    engine, instance = _abjure_and_reach_enemy_turn(sheets)
    moved = _move(engine, instance, 5, "enemy:goblin-1")
    assert moved["ok"] is True, moved

    blocked = engine.resolve_intent(instance, _intent(
        instance, actor_id="enemy:goblin-1", type="attack",
        target_id="player:gm", attack_id="slam",
    ), SequenceRng([15, 3]))
    assert blocked["ok"] is False
    assert "already chose to move" in blocked["error"]


def test_acting_first_costs_the_move(sheets) -> None:
    engine, instance = _abjure_and_reach_enemy_turn(sheets)
    events = _apply(engine, instance, engine.resolve_intent(instance, _intent(
        instance, actor_id="enemy:goblin-1", type="attack",
        target_id="player:gm", attack_id="slam",
    ), SequenceRng([15, 3])))
    assert _of_type(events, "check.resolved")

    blocked = _move(engine, instance, 5, "enemy:goblin-1")
    assert blocked["ok"] is False
    assert "already chose to take an action" in blocked["error"]


def test_a_bonus_action_first_costs_the_move(sheets) -> None:
    engine, instance = _setup(sheets["paladin9_low_cha"])  # Cha 8: exactly one target
    _add_ally(instance, sheets["paladin3"])
    _start(engine, instance, rolls=(3, 20, 8))
    _positions(instance, **{"player:gm": 0, "player:ally": 0, "enemy:goblin-1": 5})
    _apply(engine, instance, _capability(
        engine, instance, "abjure_foes", [2],
        target_ids=["player:ally"],
    ))
    while _combat(instance)["economy"]["actor_id"] != "player:ally":
        assert _end_turn(engine, instance)

    # 圣疗是附赠动作能力：先花掉它，就不能再移动。
    _add_ally_pool = _pool(instance, "lay_on_hands", "ally")
    assert _add_ally_pool["current"] > 0
    _apply(engine, instance, _capability(
        engine, instance, "lay_on_hands", [],
        actor_id="player:ally", target_id="player:ally", healing_amount=5,
    ))
    blocked = _move(engine, instance, 5, "player:ally")
    assert blocked["ok"] is False
    assert "already chose to take a Bonus Action" in blocked["error"]


def test_an_abjured_creature_can_still_react(sheets) -> None:
    engine, instance = _abjure_and_reach_enemy_turn(sheets)
    enemy = engine._actor_view(instance, _combat(instance), "enemy:goblin-1")
    # 三选一不是失能：反应预算与失能判定都不受影响。
    assert engine._reaction_eligible(_combat(instance), enemy) is True


def test_action_surge_cannot_buy_a_second_choice(sheets) -> None:
    engine, instance = _abjure_and_reach_enemy_turn(sheets)
    _apply(engine, instance, engine.resolve_intent(instance, _intent(
        instance, actor_id="enemy:goblin-1", type="attack",
        target_id="player:gm", attack_id="slam",
    ), SequenceRng([15, 3])))
    _inject(engine, instance, [{
        "type": "dnd2024.economy.granted", "actor_id": "enemy:goblin-1",
        "grants": {"surge_action": 1},
    }])
    blocked = engine.resolve_intent(instance, _intent(
        instance, actor_id="enemy:goblin-1", type="attack",
        target_id="player:gm", attack_id="slam",
    ), SequenceRng([15, 3]))
    assert blocked["ok"] is False
    assert "Action Surge cannot buy another" in blocked["error"]


# ---------------------------------------------------------------------------
# 10. Abjure Foes: how it ends
# ---------------------------------------------------------------------------


def test_positive_damage_ends_it_and_zero_does_not(sheets) -> None:
    engine, instance = _abjure_and_reach_enemy_turn(sheets)
    _inject(engine, instance, [{
        "type": "resource.changed", "resource": "hp", "target_id": "enemy:goblin-1",
        "delta": 0, "amount": 0, "damage_type": "radiant", "rolls": [],
    }])
    assert "frightened" in _conditions(instance, "enemy:goblin-1")

    _inject(engine, instance, [{
        "type": "resource.changed", "resource": "hp", "target_id": "enemy:goblin-1",
        "delta": -3, "amount": 3, "damage_type": "slashing", "rolls": [],
    }])
    assert "frightened" not in _conditions(instance, "enemy:goblin-1")
    # 另一个目标不受影响。
    assert "frightened" in _conditions(instance, "enemy:goblin-2")


def test_the_one_minute_runs_out_after_ten_of_its_own_turns(sheets) -> None:
    engine, instance = _abjure_and_reach_enemy_turn(sheets)
    for _ in range(9):
        _next_turn(engine, instance, "enemy:goblin-1")
    assert _conditions(instance, "enemy:goblin-1")["frightened"]["turns_remaining"] == 1
    _next_turn(engine, instance, "enemy:goblin-1")
    assert "frightened" not in _conditions(instance, "enemy:goblin-1")


def test_a_frightened_immune_creature_keeps_no_hidden_limit(sheets) -> None:
    # 勇气灵光让光环内的队友免疫 Frightened。斥退敌人选得到友方（SRD 是
    # "creatures you choose"，不是"敌人"），被免疫的那个不该落下三选一限制。
    engine, instance = _setup(sheets["paladin15"])
    _add_ally(instance, sheets["paladin3"])
    _start(engine, instance, rolls=(3, 20, 8))
    _positions(instance, **{"player:gm": 0, "player:ally": 5, "enemy:goblin-1": 5})
    events = _apply(engine, instance, _capability(
        engine, instance, "abjure_foes", [2, 2],
        target_ids=["player:ally", "enemy:goblin-1"],
    ))
    assert "frightened" not in _conditions(instance, "player:ally")
    # 敌人不在勇气灵光的受益方里，照样恐惧并带上限制。
    assert _conditions(instance, "enemy:goblin-1")["frightened"]["turn_choice_limit"]
    blocked = [
        row for row in _of_type(events, "check.resolved")
        if row.get("blocked_condition") == "frightened"
    ]
    assert len(blocked) == 1
    assert blocked[0]["actor_id"] == "player:ally"
    assert blocked[0]["blocked_by_aura"]["feature_id"] == "aura_of_courage"


# ---------------------------------------------------------------------------
# 11. Sear Undead
# ---------------------------------------------------------------------------

TURN_DICE = [3, 3, 5, 6]


def test_sear_rolls_one_d8_per_wisdom_modifier_and_hits_the_failed_undead(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy(creature_type="undead", hp=80)])
    events = _apply(engine, instance, _capability(
        engine, instance, "turn_undead", TURN_DICE,
        target_ids=["enemy:goblin-1"], sear_undead=True,
    ))
    damage = [
        row for row in _of_type(events, "resource.changed")
        if row.get("resource") == "hp" and row["amount"] > 0
    ]
    # Wis 15 → +2 → 两颗 d8，掷出 5 和 6。
    # 4 级 ASI 把感知从 15 抬到 16：+3 → 三颗 d8，掷出 3、5、6。
    assert len(damage) == 1
    assert damage[0]["amount"] == 14
    assert damage[0]["rolls"] == [3, 5, 6]
    assert damage[0]["damage_type"] == "radiant"
    assert damage[0]["feature_id"] == "sear_undead"
    assert damage[0]["turn_effect_preserved"] is True
    assert _hp(instance, "enemy:goblin-1") == 66


def test_a_negative_wisdom_still_rolls_one_d8(sheets) -> None:
    engine, instance = _setup(sheets["cleric5_low_wis"])
    _start(engine, instance, enemies=[_enemy(creature_type="undead", hp=80)])
    events = _apply(engine, instance, _capability(
        engine, instance, "turn_undead", [3, 7],
        target_ids=["enemy:goblin-1"], sear_undead=True,
    ))
    damage = [
        row for row in _of_type(events, "resource.changed")
        if row.get("resource") == "hp" and row["amount"] > 0
    ]
    assert damage[0]["amount"] == 7
    assert damage[0]["rolls"] == [7]


def test_a_creature_that_saved_takes_no_sear_damage(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[
        _enemy(creature_type="undead", hp=80, enemy_id="goblin-1"),
        _enemy(creature_type="undead", hp=80, position=10, enemy_id="goblin-2"),
    ])
    events = _apply(engine, instance, _capability(
        engine, instance, "turn_undead", [20, 3, 5, 6],
        target_ids=["enemy:goblin-1", "enemy:goblin-2"], sear_undead=True,
    ))
    damage = [
        row for row in _of_type(events, "resource.changed")
        if row.get("resource") == "hp" and row["amount"] > 0
    ]
    assert [row["target_id"] for row in damage] == ["enemy:goblin-2"]
    assert _hp(instance, "enemy:goblin-1") == 80


def test_without_sear_no_extra_die_is_rolled(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy(creature_type="undead", hp=80)])
    events = _apply(engine, instance, _capability(
        engine, instance, "turn_undead", [3],
        target_ids=["enemy:goblin-1"],
    ))
    assert _of_type(events, "resource.changed") == []
    assert len(_of_type(events, "check.resolved")) == 1
    assert _hp(instance, "enemy:goblin-1") == 80


def test_sear_does_not_end_the_turn_effect_but_later_damage_does(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy(creature_type="undead", hp=80)])
    _apply(engine, instance, _capability(
        engine, instance, "turn_undead", TURN_DICE,
        target_ids=["enemy:goblin-1"], sear_undead=True,
    ))
    conditions = _conditions(instance, "enemy:goblin-1")
    assert "frightened" in conditions
    assert "incapacitated" in conditions
    assert conditions["frightened"]["duration"] == "turn_undead"

    _inject(engine, instance, [{
        "type": "resource.changed", "resource": "hp", "target_id": "enemy:goblin-1",
        "delta": -1, "amount": 1, "damage_type": "slashing", "rolls": [],
    }])
    assert "frightened" not in _conditions(instance, "enemy:goblin-1")


def test_sear_goes_through_resistance_and_can_be_halved_to_nothing(sheets) -> None:
    engine, instance = _setup(sheets["cleric5_low_wis"])
    _start(engine, instance, enemies=[_enemy(creature_type="undead", hp=80)])
    _inject(engine, instance, [{
        "type": "condition.applied", "target_id": "enemy:goblin-1",
        "condition": "stoneskinned", "resist_damage_types": ["radiant"],
    }])
    events = _apply(engine, instance, _capability(
        engine, instance, "turn_undead", [3, 6],
        target_ids=["enemy:goblin-1"], sear_undead=True,
    ))
    damage = [
        row for row in _of_type(events, "resource.changed")
        if row.get("resource") == "hp" and row["amount"] > 0
    ]
    assert damage[0]["amount"] == 3
    assert damage[0]["resisted"] is True

    # 一颗 d8 掷出 1，抗性减半后是 0：没有伤害事件，状态照旧保留。
    # 同一回合只能用一个 Magic action，所以先把回合转一圈。
    _next_turn(engine, instance, "player:gm")
    events = _apply(engine, instance, _capability(
        engine, instance, "turn_undead", [3, 1],
        target_ids=["enemy:goblin-1"], sear_undead=True,
    ))
    assert _of_type(events, "resource.changed") == []
    assert "frightened" in _conditions(instance, "enemy:goblin-1")


def test_sear_is_damage_and_no_healing_booster_touches_it(sheets) -> None:
    # 生命牧师的治疗加成只认治疗：Disciple of Life 不给伤害加值，
    # Supreme Healing 也只写在两条治疗路径上，所以掷出几就是几。
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy(creature_type="undead", hp=80)])
    events = _apply(engine, instance, _capability(
        engine, instance, "turn_undead", [3, 1, 1, 1],
        target_ids=["enemy:goblin-1"], sear_undead=True,
    ))
    damage = _of_type(events, "resource.changed")
    assert len(damage) == 1
    assert damage[0]["rolls"] == [1, 1, 1]
    assert damage[0]["amount"] == 3
    assert "maximized" not in damage[0]
    # 没有治疗事件：Blessed Healer 不会借着这次伤害回血。
    assert not [row for row in damage if int(row.get("delta", 0)) > 0]


def test_sear_can_finish_the_last_enemy(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy(creature_type="undead", hp=5)])
    events = _apply(engine, instance, _capability(
        engine, instance, "turn_undead", TURN_DICE,
        target_ids=["enemy:goblin-1"], sear_undead=True,
    ))
    assert _of_type(events, "dnd2024.combat.ended")


def test_sear_is_refused_below_level_five(sheets) -> None:
    engine, instance = _setup(sheets["cleric4"])
    _start(engine, instance, enemies=[_enemy(creature_type="undead", hp=80)])
    resolved = _capability(
        engine, instance, "turn_undead", [3, 5],
        target_ids=["enemy:goblin-1"], sear_undead=True,
    )
    assert resolved["ok"] is False
    assert "sear undead is not available" in resolved["error"]


# ---------------------------------------------------------------------------
# 12. channel sharing, replay, save/load
# ---------------------------------------------------------------------------


def test_sacred_weapon_and_abjure_share_one_channel_pool(sheets) -> None:
    engine, instance = _setup(sheets["paladin9"])
    _start(engine, instance, enemies=_two_enemies())
    _attack(engine, instance, MELEE, activate_sacred_weapon=True)
    assert _pool(instance)["current"] == 1
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    _end_turn(engine, instance, "enemy:goblin-2")
    _apply(engine, instance, _capability(
        engine, instance, "abjure_foes", [2, 2],
        target_ids=["enemy:goblin-1", "enemy:goblin-2"],
    ))
    assert _pool(instance)["current"] == 0
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    _end_turn(engine, instance, "enemy:goblin-2")
    blocked = _capability(
        engine, instance, "abjure_foes", [2, 2],
        target_ids=["enemy:goblin-1", "enemy:goblin-2"],
    )
    assert blocked["ok"] is False


def test_a_batch_replays_to_the_same_state(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy(creature_type="undead", hp=80)])
    resolved = _capability(
        engine, instance, "turn_undead", TURN_DICE,
        target_ids=["enemy:goblin-1"], sear_undead=True,
    )
    batch = deepcopy(resolved["event_batch"])
    assert engine.apply_batch(instance, batch)["applied"] is True
    first_hp = _hp(instance, "enemy:goblin-1")
    first_conditions = deepcopy(_conditions(instance, "enemy:goblin-1"))
    replay = engine.apply_batch(instance, batch)
    assert replay["applied"] is False  # 同一批次不会被应用第二次
    assert _hp(instance, "enemy:goblin-1") == first_hp
    assert _conditions(instance, "enemy:goblin-1") == first_conditions


def test_the_bond_and_the_cover_survive_a_save_and_load(sheets, bundle) -> None:
    engine, instance = _setup(sheets["paladin15"])
    _start(engine, instance)
    _attack(engine, instance, MELEE, activate_sacred_weapon=True)
    _attack(engine, instance, MELEE, decline=False)
    _cast_smite(engine, instance)

    state = deepcopy(ruleset_runtime.state(instance))
    sheet = deepcopy(instance.get_character_sheet("gm"))
    reloaded = Dnd2024Runtime().load_bundle("en")
    fresh_engine, fresh = _instance(reloaded, sheet)
    ruleset_runtime.replace_state(fresh, state)
    conditions = _conditions(fresh)
    assert conditions["sacred_weapon"]["weapon_ref"] == MELEE
    assert conditions["smite_of_protection"]["duration"] == "actor_turn_start"
    # 投影是只读的：连续两次读取不会改任何状态。
    before = deepcopy(conditions)
    engine._cover_record(fresh, _combat(fresh), fresh_engine._actor_view(
        fresh, _combat(fresh), "player:gm",
    ))
    assert _conditions(fresh) == before
