"""S0-F22: Channel Divinity, the healing pool, and the two healing riders.

Six features over two classes.  Three of them share one Channel Divinity
resource, one is an independent pool spent point by point, one is a passive
rider on spells cast with a real slot, and one is a passive rider that heals
its own owner.

Every combat assertion goes through ``resolve_intent`` -> ``apply_batch``, the
same two calls the web layer makes, so a rule that only holds inside the
resolver cannot pass here.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.resting.engine import Dnd2024RestEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    monk_draft,
)
from test_dnd2024_survivor import _instance

# 15/14/13/12/10/8 各用一次：标准数组的唯一合法排列由 builder 校验。
CLERIC_ABILITIES = {"str": 8, "dex": 12, "con": 14, "int": 10, "wis": 15, "cha": 13}
PALADIN_ABILITIES = {"str": 13, "dex": 10, "con": 14, "int": 8, "wis": 12, "cha": 15}
CLERIC_SKILLS = ["skill:history", "skill:medicine"]
PALADIN_SKILLS = ["skill:athletics", "skill:persuasion"]


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
    """One professional character advanced through the real chain.

    A Cleric or Paladin has to answer spell choices the quick presets never
    ask for, so this borrows the Monk draft as a legal level-1 skeleton and
    rewrites only what the class needs.  The subclass is chosen explicitly:
    ``advancement_choices`` picks the first legal option, which is not always
    the one under test.
    """

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


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle("en")


@pytest.fixture(scope="module")
def features(bundle):
    return Dnd2024ClassFeatureResolver(bundle)


def _cleric_sheet(bundle, level: int, subclass: str = "subclass:life_domain") -> dict:
    return _build(
        Dnd2024Runtime(), bundle, "class:cleric", level, CLERIC_ABILITIES,
        CLERIC_SKILLS, "equipment_package:cleric_a", subclass if level >= 3 else "",
    )


def _paladin_sheet(bundle, level: int, subclass: str = "subclass:oath_of_devotion") -> dict:
    return _build(
        Dnd2024Runtime(), bundle, "class:paladin", level, PALADIN_ABILITIES,
        PALADIN_SKILLS, "equipment_package:paladin_a", subclass if level >= 3 else "",
    )


@pytest.fixture(scope="module")
def sheets(bundle) -> dict[str, dict]:
    return {
        "cleric1": _cleric_sheet(bundle, 1),
        "cleric2": _cleric_sheet(bundle, 2),
        "cleric3": _cleric_sheet(bundle, 3),
        "cleric5": _cleric_sheet(bundle, 5),
        "cleric6": _cleric_sheet(bundle, 6),
        "cleric7": _cleric_sheet(bundle, 7),
        "cleric13": _cleric_sheet(bundle, 13),
        "cleric17": _cleric_sheet(bundle, 17),
        "cleric18": _cleric_sheet(bundle, 18),
        "paladin1": _paladin_sheet(bundle, 1),
        "paladin3": _paladin_sheet(bundle, 3),
        "paladin5": _paladin_sheet(bundle, 5),
        "paladin13": _paladin_sheet(bundle, 13),
        "paladin14": _paladin_sheet(bundle, 14),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _setup(sheet: dict) -> tuple[object, object]:
    return _instance(Dnd2024Runtime().load_bundle("en"), deepcopy(sheet))


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _conditions(instance, actor_id: str = "player:gm") -> dict:
    combat = ruleset_runtime.state(instance)["combat"]
    kind, raw = actor_id.split(":", 1)
    if kind == "player":
        return _canonical(instance, raw).setdefault("conditions", {})
    return combat["enemies"][raw].setdefault("conditions", {})


def _hp(instance, actor_id: str = "player:gm") -> int:
    kind, raw = actor_id.split(":", 1)
    if kind == "player":
        return int(_canonical(instance, raw)["resources"]["hp"])
    return int(ruleset_runtime.state(instance)["combat"]["enemies"][raw]["hp"])


def _set_hp(instance, actor_id: str, value: int) -> None:
    kind, raw = actor_id.split(":", 1)
    if kind == "player":
        _canonical(instance, raw)["resources"]["hp"] = value
        return
    ruleset_runtime.state(instance)["combat"]["enemies"][raw]["hp"] = value


def _enemy(
    creature_type: str = "humanoid", hp: int = 60, position: int = 5,
    enemy_id: str = "goblin-1",
) -> dict:
    """A brute with no attacks worth rolling: every case here is about healing.

    ``creature_type`` is what Turn Undead and Divine Sense read, so it has to
    be a real field rather than a display name.
    """

    return {
        "id": enemy_id, "name": "Brute", "hp": hp, "armor_class": 12,
        "speed": 30, "position": position, "initiative_modifier": 5,
        "creature_type": creature_type,
        "abilities": {"str": 16, "dex": 10, "con": 14, "int": 8, "wis": 8, "cha": 8},
        "saving_throws": {"str": 3, "con": 2},
        "attacks": [{
            "id": "slam", "name": "Slam", "attack_bonus": 5,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _add_ally(instance, sheet: dict, uid: str = "ally") -> None:
    """A second party member, so "another creature" can be a real ally.

    Beneficial spells refuse a hostile target, so a case about healing someone
    else needs someone else who is not the enemy.
    """

    instance.players[uid] = {
        "character_name": "Bo",
        "character_sheet": deepcopy(sheet),
    }


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


def _capability(engine, instance, capability_id: str, dice=(), **extra) -> dict:
    state = ruleset_runtime.state(instance)
    intent = {
        "intent_id": f"cap-{capability_id}-{state['version']}",
        "type": "class_capability",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm",
        "capability_id": capability_id,
    }
    intent.update(extra)
    return engine.resolve_intent(instance, intent, SequenceRng(list(dice)))


def _apply(engine, instance, resolved: dict) -> list[dict]:
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _activate(engine, instance, capability_id: str, dice=(), **extra) -> list[dict]:
    return _apply(
        engine, instance, _capability(engine, instance, capability_id, dice, **extra),
    )


def _combat(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _of_type(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("type") == kind]


def _inject(engine, instance, events: list[dict]) -> None:
    """Apply raw events through the same reducer ``apply_batch`` uses.

    Nothing in the SRD content stuns a player or resists a particular hit, so
    those two facts have to be written straight into the ledger.  The
    assertions still run against the real reducer, not a hand-built state.
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
        "events": list(events), "source_ref": "srd-5.2.1:p37:channel-divinity",
    }
    assert engine.apply_batch(instance, batch)["applied"] is True


def _end_turn(engine, instance, actor_id: str | None = None) -> list[dict]:
    state = ruleset_runtime.state(instance)
    if actor_id is None:
        actor_id = str(_combat(instance)["economy"].get("actor_id") or "")
    intent = {
        "intent_id": f"end:{actor_id}:{state['version']}", "type": "end_turn",
        "actor_id": actor_id, "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm",
    }
    return _apply(engine, instance, engine.resolve_intent(instance, intent, SequenceRng([])))


def _pool(instance, resource_id: str, uid: str = "gm") -> dict:
    return _canonical(instance, uid)["resources"]["class"][resource_id]


def _rest_short(sheet: dict, bundle) -> dict:
    return Dnd2024RestEngine(bundle).complete_short_rest(deepcopy(sheet), {})


def _rest_long(sheet: dict, bundle) -> dict:
    return Dnd2024RestEngine(bundle).complete_long_rest(deepcopy(sheet), {})


# ---------------------------------------------------------------------------
# 1. grants: the six features exist only at their own level and owner
# ---------------------------------------------------------------------------


def test_the_channel_features_appear_only_at_their_own_level(sheets, features) -> None:
    assert not features.has_feature(sheets["cleric1"]["ruleset_character"], "channel_divinity")
    assert features.has_feature(sheets["cleric2"]["ruleset_character"], "channel_divinity")

    # Preserve Life, Disciple of Life and Blessed Healer are Life Domain only.
    # Life Domain 的入口只在选了这个子职之后出现：3 级之前没有子职特性。
    assert not features.has_feature(
        sheets["cleric2"]["ruleset_character"], "preserve_life",
    )
    assert features.has_feature(sheets["cleric3"]["ruleset_character"], "preserve_life")
    assert features.has_feature(sheets["cleric3"]["ruleset_character"], "disciple_of_life")
    assert not features.has_feature(
        sheets["cleric5"]["ruleset_character"], "blessed_healer",
    )
    assert features.has_feature(sheets["cleric6"]["ruleset_character"], "blessed_healer")

    # Paladin: Lay On Hands at 1, Channel Divinity at 3, Restoring Touch at 14.
    assert features.has_feature(sheets["paladin1"]["ruleset_character"], "lay_on_hands")
    assert not features.has_feature(
        sheets["paladin1"]["ruleset_character"], "channel_divinity",
    )
    assert features.has_feature(sheets["paladin3"]["ruleset_character"], "channel_divinity")
    assert not features.has_feature(
        sheets["paladin13"]["ruleset_character"], "restoring_touch",
    )
    assert features.has_feature(
        sheets["paladin14"]["ruleset_character"], "restoring_touch",
    )


def test_the_capabilities_are_projected_only_for_the_owning_class(sheets, features) -> None:
    economy = {"action": 1, "bonus_action": 1}
    cleric = {
        view.id for view in features.combat_capabilities(
            sheets["cleric3"]["ruleset_character"], economy, actor_id="player:gm",
        )
    }
    assert {"divine_spark", "turn_undead", "preserve_life"} <= cleric
    assert "lay_on_hands" not in cleric

    paladin = {
        view.id for view in features.combat_capabilities(
            sheets["paladin3"]["ruleset_character"], economy, actor_id="player:gm",
        )
    }
    assert {"lay_on_hands", "divine_sense"} <= paladin
    assert "divine_spark" not in paladin


# ---------------------------------------------------------------------------
# 2. one shared Channel Divinity pool
# ---------------------------------------------------------------------------


def test_three_channel_uses_in_a_row_run_out(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance)
    # 每次使用之间走一轮，附赠/动作与 Channel 才会各自刷新；只有 Channel
    # 是真的会用完的那个。
    first = _activate(
        engine, instance, "divine_spark",
        dice=[5, 8], target_id="enemy:goblin-1", spark_mode="heal",
    )
    assert _of_type(first, "dnd2024.class_resource.spent")[0]["amount"] == 1
    assert _pool(instance, "channel_divinity")["current"] == 1
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    # Preserve Life 只接受结算时已经过半血的目标，所以先把自己压到半血以下。
    _canonical(instance)["resources"]["max_hp"] = 40
    _canonical(instance)["resources"]["hp"] = 10
    _activate(
        engine, instance, "preserve_life", dice=[],
        allocations=[{"target_id": "player:gm", "amount": 5}],
    )
    assert _pool(instance, "channel_divinity")["current"] == 0
    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    third = _capability(
        engine, instance, "turn_undead", dice=[], target_ids=["enemy:goblin-1"],
    )
    assert third["ok"] is False
    assert "channel" in third["error"].lower()


def test_a_short_rest_returns_one_channel_use(sheets, bundle) -> None:
    sheet = deepcopy(sheets["cleric5"])
    sheet["ruleset_character"]["resources"]["class"]["channel_divinity"]["current"] = 0
    restored = _rest_short(sheet, bundle)["character"]
    assert restored["resources"]["class"]["channel_divinity"]["current"] == 1
    full = _rest_long(restored, bundle)["character"]
    assert full["resources"]["class"]["channel_divinity"]["current"] == 2


def test_the_paladin_channel_is_its_own_pool(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    _activate(engine, instance, "divine_sense")
    resources = {
        key: int(value.get("current", 0))
        for key, value in _canonical(instance)["resources"]["class"].items()
    }
    # Divine Sense 不花 Channel：Paladin 池仍是满的，而 Cleric 池根本不存在。
    assert resources["channel_divinity"] == 2
    assert "divine_spark" not in resources


# ---------------------------------------------------------------------------
# 3. Divine Spark: dice per tier, two modes, one save
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, count",
    [("cleric2", 1), ("cleric7", 2), ("cleric13", 3), ("cleric18", 4)],
)
def test_divine_spark_rolls_one_die_per_tier(sheets, key, count) -> None:
    engine, instance = _setup(sheets[key])
    _start(engine, instance)
    # SequenceRng 只在真的掷骰时被消耗：这里给足 count+1 个面就能看出
    # 究竟用了几颗（后面的豁免骰也走同一个 rng）。
    events = _activate(
        engine, instance, "divine_spark",
        dice=[3] * (count + 2), target_id="enemy:goblin-1", spark_mode="heal",
    )
    healing = _of_type(events, "resource.changed")[0]
    maximized = int(_canonical(instance)["build"]["level"]) >= 17
    if maximized:
        # Supreme Healing 把骰面变成常量：零 RNG 消耗、骰迹为空（D1 契约）。
        assert healing["maximized"] is True
        assert healing["rolls"] == []
    else:
        assert len(healing["rolls"]) == count
    # 15 级 Wis 是 +4（15 + 三个 ASI 之一）= 7；这里只断言骰面数量与加值
    # 一致，具体加值由角色属性决定。
    wis = int(_canonical(instance)["abilities"]["wis"])
    face = 8 if maximized else 3
    assert healing["amount"] == max(1, face * count + (wis - 10) // 2)


def test_divine_spark_heals_another_creature_and_refuses_itself(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance)
    _set_hp(instance, "enemy:goblin-1", 20)
    events = _activate(
        engine, instance, "divine_spark",
        dice=[6], target_id="enemy:goblin-1", spark_mode="heal",
    )
    assert _of_type(events, "resource.changed")[0]["healing"] is True
    assert _hp(instance, "enemy:goblin-1") > 20

    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    self_target = _capability(
        engine, instance, "divine_spark",
        dice=[6], target_id="player:gm", spark_mode="heal",
    )
    assert self_target["ok"] is False
    assert "yourself" in self_target["error"]


def test_divine_spark_damage_costs_a_constitution_save(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance)
    # 敌人 Con 修正 +2，DC 是 Cleric 的法术豁免 DC；第一颗是 d8，第二颗是
    # Con 豁免的 d20（掷 2 必败，掷 20 必成）。
    failed = _activate(
        engine, instance, "divine_spark",
        dice=[5, 2], target_id="enemy:goblin-1",
        spark_mode="damage", damage_type="necrotic",
    )
    damage = _of_type(failed, "resource.changed")[0]
    assert damage["damage_type"] == "necrotic"
    wis = (int(_canonical(instance)["abilities"]["wis"]) - 10) // 2
    assert damage["amount"] == 5 + wis  # 掷出的 5 加 Wis，未减半

    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    saved = _activate(
        engine, instance, "divine_spark",
        dice=[5, 20], target_id="enemy:goblin-1",
        spark_mode="damage", damage_type="radiant",
    )
    check = _of_type(saved, "check.resolved")[0]
    assert check["success"] is True
    assert _of_type(saved, "resource.changed")[0]["amount"] == (5 + wis) // 2


def test_divine_spark_refuses_a_forged_damage_type(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance)
    resolved = _capability(
        engine, instance, "divine_spark",
        dice=[5, 2], target_id="enemy:goblin-1",
        spark_mode="damage", damage_type="fire",
    )
    assert resolved["ok"] is False
    assert "declared damage types" in resolved["error"]


def test_supreme_healing_maximises_the_spark_heal_but_not_the_damage(sheets) -> None:
    engine, instance = _setup(sheets["cleric17"])
    _start(engine, instance)
    # 三颗 d8 全 8、Wis +4（17 级至少 +4）→ 24 + 4。
    events = _activate(
        engine, instance, "divine_spark",
        dice=[8], target_id="enemy:goblin-1", spark_mode="heal",
    )
    healing = _of_type(events, "resource.changed")[0]
    assert healing.get("maximized") is True
    assert healing["rolls"] == []  # 最大化不掷骰，骰迹为空
    assert healing["amount"] == 24 + (int(_canonical(instance)["abilities"]["wis"]) - 10) // 2

    _end_turn(engine, instance, "player:gm")
    _end_turn(engine, instance, "enemy:goblin-1")
    # 伤害模式仍要掷骰：喂一颗 1 就能看出它没有被最大化。
    events = _activate(
        engine, instance, "divine_spark",
        dice=[1, 2], target_id="enemy:goblin-1",
        spark_mode="damage", damage_type="radiant",
    )
    assert "maximized" not in _of_type(events, "resource.changed")[0]


# ---------------------------------------------------------------------------
# 4. Turn Undead
# ---------------------------------------------------------------------------


def test_turn_undead_only_touches_the_chosen_undead(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[
        _enemy("undead", enemy_id="skeleton-1"),
        _enemy("undead", enemy_id="skeleton-2", position=25),
        _enemy("humanoid", enemy_id="goblin-1"),
    ])
    events = _activate(
        engine, instance, "turn_undead", dice=[2, 2],
        target_ids=["enemy:skeleton-1", "enemy:skeleton-2"],
    )
    checks = _of_type(events, "check.resolved")
    assert {row["actor_id"] for row in checks} == {"enemy:skeleton-1", "enemy:skeleton-2"}
    applied = _of_type(events, "condition.applied")
    assert {(row["target_id"], row["condition"]) for row in applied} == {
        ("enemy:skeleton-1", "frightened"), ("enemy:skeleton-1", "incapacitated"),
        ("enemy:skeleton-2", "frightened"), ("enemy:skeleton-2", "incapacitated"),
    }
    assert "frightened" not in _conditions(instance, "enemy:goblin-1")


def test_turn_undead_refuses_a_non_undead_target(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance)
    resolved = _capability(
        engine, instance, "turn_undead", dice=[], target_ids=["enemy:goblin-1"],
    )
    assert resolved["ok"] is False
    assert "Undead" in resolved["error"]


def test_a_successful_save_leaves_no_condition(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy("undead", enemy_id="skeleton-1")])
    events = _activate(
        engine, instance, "turn_undead", dice=[20], target_ids=["enemy:skeleton-1"],
    )
    assert _of_type(events, "check.resolved")[0]["success"] is True
    assert _of_type(events, "condition.applied") == []


def test_damage_ends_the_turn_and_a_resisted_hit_does_not(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy("undead", enemy_id="skeleton-1")])
    _activate(engine, instance, "turn_undead", dice=[2], target_ids=["enemy:skeleton-1"])
    assert "frightened" in _conditions(instance, "enemy:skeleton-1")

    # 抗性把伤害降到 0 时不算"受到伤害"，状态必须留着。
    _inject(engine, instance, [{
        "type": "resource.changed", "resource": "hp",
        "target_id": "enemy:skeleton-1", "delta": 0, "amount": 0,
        "damage_type": "slashing",
    }])
    assert "frightened" in _conditions(instance, "enemy:skeleton-1")

    _inject(engine, instance, [{
        "type": "resource.changed", "resource": "hp",
        "target_id": "enemy:skeleton-1", "delta": -3, "amount": 3,
        "damage_type": "slashing",
    }])
    assert "frightened" not in _conditions(instance, "enemy:skeleton-1")
    assert "incapacitated" not in _conditions(instance, "enemy:skeleton-1")


def test_the_turn_ends_when_its_source_is_incapacitated(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy("undead", enemy_id="skeleton-1")])
    _activate(engine, instance, "turn_undead", dice=[2], target_ids=["enemy:skeleton-1"])
    assert "frightened" in _conditions(instance, "enemy:skeleton-1")

    stun = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "stunned", "duration": "actor_turn_start",
    }]
    # 施法者失能后 Turn 状态由结算步骤统一移除，不是靠逐个来源写分支。
    _inject(engine, instance, [
        *stun,
        *engine._source_incapacitation_events(instance, _combat(instance), stun),
    ])
    assert "frightened" not in _conditions(instance, "enemy:skeleton-1")
    assert "incapacitated" not in _conditions(instance, "enemy:skeleton-1")


def test_the_turn_expires_after_ten_of_its_own_turns(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy("undead", enemy_id="skeleton-1")])
    _activate(engine, instance, "turn_undead", dice=[2], target_ids=["enemy:skeleton-1"])
    for _ in range(10):
        _end_turn(engine, instance, "player:gm")
        _end_turn(engine, instance, "enemy:skeleton-1")
    assert "frightened" not in _conditions(instance, "enemy:skeleton-1")


# ---------------------------------------------------------------------------
# 5. Divine Sense
# ---------------------------------------------------------------------------


def test_divine_sense_lists_the_hidden_undead_only_for_its_owner(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance, enemies=[
        _enemy("undead", enemy_id="skeleton-1", position=20),
        _enemy("humanoid", enemy_id="goblin-1", position=10),
    ])
    _activate(engine, instance, "divine_sense")
    view = engine.gameplay_view(instance, viewer_id="gm", viewer_is_gm=False)
    senses = view["combat"]["private_senses"]
    assert senses["range_ft"] == 60
    assert [row["actor_id"] for row in senses["creatures"]] == ["enemy:skeleton-1"]
    assert senses["creatures"][0]["creature_type"] == "undead"
    assert senses["consecrations_supported"] is False
    # 同一个视图给别人看是空的：侦测是私有信息。
    other = engine.gameplay_view(instance, viewer_id="someone-else", viewer_is_gm=False)
    assert other["combat"]["private_senses"] == {}


def test_divine_sense_is_a_bonus_action(sheets) -> None:
    engine, instance = _setup(sheets["paladin3"])
    _start(engine, instance)
    events = _activate(engine, instance, "divine_sense")
    spent = _of_type(events, "dnd2024.action.spent")
    assert [(row["resource"], row["amount"]) for row in spent] == [("bonus_action", 1)]
    assert _of_type(events, "condition.applied")[0]["condition"] == "divine_sense"


# ---------------------------------------------------------------------------
# 6. Lay On Hands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key, maximum", [("paladin1", 5), ("paladin5", 25)])
def test_the_lay_on_hands_pool_is_five_per_level(sheets, key, maximum) -> None:
    sheet = sheets[key]["ruleset_character"]
    assert sheet["resources"]["class"]["lay_on_hands"]["maximum"] == maximum


def test_lay_on_hands_spends_what_was_asked_for(sheets) -> None:
    engine, instance = _setup(sheets["paladin5"])
    _start(engine, instance)
    _canonical(instance)["resources"]["hp"] = 10
    events = _activate(
        engine, instance, "lay_on_hands", dice=[],
        target_id="player:gm", healing_amount=12,
    )
    assert _of_type(events, "dnd2024.class_resource.spent")[0]["amount"] == 12
    assert _pool(instance, "lay_on_hands")["current"] == 13
    assert _hp(instance, "player:gm") == 22


def test_lay_on_hands_charges_the_request_even_when_the_target_is_full(sheets) -> None:
    engine, instance = _setup(sheets["paladin5"])
    _start(engine, instance)
    _canonical(instance)["resources"]["hp"] = 20  # max_hp - 4
    _canonical(instance)["resources"]["max_hp"] = 24
    events = _activate(
        engine, instance, "lay_on_hands", dice=[],
        target_id="player:gm", healing_amount=20,
    )
    assert _of_type(events, "dnd2024.class_resource.spent")[0]["amount"] == 20
    assert _pool(instance, "lay_on_hands")["current"] == 5
    assert _hp(instance, "player:gm") == 24


def test_lay_on_hands_removes_poisoned_without_healing(sheets) -> None:
    engine, instance = _setup(sheets["paladin5"])
    _start(engine, instance)
    _conditions(instance)["poisoned"] = {"source": "probe"}
    _canonical(instance)["resources"]["hp"] = 10
    events = _activate(
        engine, instance, "lay_on_hands", dice=[],
        target_id="player:gm", healing_amount=0, remove_conditions=["poisoned"],
    )
    assert _of_type(events, "dnd2024.class_resource.spent")[0]["amount"] == 5
    assert _of_type(events, "condition.removed")[0]["condition"] == "poisoned"
    assert "poisoned" not in _conditions(instance)
    assert _hp(instance, "player:gm") == 10
    assert _pool(instance, "lay_on_hands")["current"] == 20


def test_lay_on_hands_refuses_a_condition_the_target_does_not_have(sheets) -> None:
    engine, instance = _setup(sheets["paladin5"])
    _start(engine, instance)
    resolved = _capability(
        engine, instance, "lay_on_hands", dice=[],
        target_id="player:gm", healing_amount=0, remove_conditions=["poisoned"],
    )
    assert resolved["ok"] is False
    assert "does not have" in resolved["error"]


def test_restoring_touch_removes_several_conditions_at_once(sheets) -> None:
    engine, instance = _setup(sheets["paladin14"])
    _add_ally(instance, sheets["paladin14"])
    _start(engine, instance, rolls=(3, 20, 8))
    # 状态挂在盟友身上：把 stunned 加给自己会让"失能角色不能行动"先一步生效，
    # 测到的就不是 Restoring Touch 了。
    held = _canonical(instance, "ally").setdefault("conditions", {})
    held["blinded"] = {"source": "probe"}
    held["stunned"] = {"source": "probe"}
    events = _activate(
        engine, instance, "lay_on_hands", dice=[],
        target_id="player:ally", healing_amount=0,
        remove_conditions=["blinded", "stunned"],
    )
    assert _of_type(events, "dnd2024.class_resource.spent")[0]["amount"] == 10
    assert {row["condition"] for row in _of_type(events, "condition.removed")} == {
        "blinded", "stunned",
    }


def test_restoring_touch_is_not_available_before_level_fourteen(sheets) -> None:
    engine, instance = _setup(sheets["paladin5"])
    _start(engine, instance)
    _conditions(instance)["blinded"] = {"source": "probe"}
    resolved = _capability(
        engine, instance, "lay_on_hands", dice=[],
        target_id="player:gm", healing_amount=0, remove_conditions=["blinded"],
    )
    assert resolved["ok"] is False
    assert "cannot remove" in resolved["error"]


def test_cannot_regain_hp_blocks_the_healing_but_not_the_removal(sheets) -> None:
    engine, instance = _setup(sheets["paladin5"])
    _start(engine, instance)
    _conditions(instance)["poisoned"] = {"source": "probe"}
    _conditions(instance)["cannot_regain_hp"] = {"source": "probe"}
    _canonical(instance)["resources"]["hp"] = 10
    events = _activate(
        engine, instance, "lay_on_hands", dice=[],
        target_id="player:gm", healing_amount=5, remove_conditions=["poisoned"],
    )
    healing = _of_type(events, "resource.changed")[0]
    assert healing["blocked_by"] == "cannot_regain_hp"
    assert healing["amount"] == 0
    assert "poisoned" not in _conditions(instance)
    assert _hp(instance, "player:gm") == 10


# ---------------------------------------------------------------------------
# 7. Preserve Life
# ---------------------------------------------------------------------------


def test_preserve_life_divides_one_fixed_budget(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[
        _enemy(hp=40, enemy_id="goblin-1"),
    ])
    _set_hp(instance, "enemy:goblin-1", 12)
    _canonical(instance)["resources"]["max_hp"] = 40
    _canonical(instance)["resources"]["hp"] = 4
    events = _activate(
        engine, instance, "preserve_life", dice=[],
        allocations=[{"target_id": "player:gm", "amount": 8}],
    )
    healing = _of_type(events, "resource.changed")[0]
    assert healing["feature_id"] == "preserve_life"
    assert healing["pool_total"] == 25  # 5 × Cleric 5
    assert healing["allocated_amount"] == 8
    assert _hp(instance, "player:gm") == 12


def test_preserve_life_cannot_cross_half_hit_points(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance)
    _canonical(instance)["resources"]["max_hp"] = 40
    _canonical(instance)["resources"]["hp"] = 10
    resolved = _capability(
        engine, instance, "preserve_life", dice=[],
        allocations=[{"target_id": "player:gm", "amount": 11}],
    )
    assert resolved["ok"] is False
    assert "half its Hit Points" in resolved["error"]


def test_preserve_life_refuses_a_budget_overrun_and_a_healthy_target(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance)
    _canonical(instance)["resources"]["max_hp"] = 200
    _canonical(instance)["resources"]["hp"] = 60
    over = _capability(
        engine, instance, "preserve_life", dice=[],
        allocations=[{"target_id": "player:gm", "amount": 26}],
    )
    assert over["ok"] is False
    assert "budget" in over["error"]

    _canonical(instance)["resources"]["hp"] = 120
    healthy = _capability(
        engine, instance, "preserve_life", dice=[],
        allocations=[{"target_id": "player:gm", "amount": 1}],
    )
    assert healthy["ok"] is False
    assert "Bloodied" in healthy["error"]


def test_preserve_life_writes_one_event_per_target(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy(hp=60, enemy_id="goblin-1")])
    _set_hp(instance, "enemy:goblin-1", 20)
    _canonical(instance)["resources"]["max_hp"] = 100
    _canonical(instance)["resources"]["hp"] = 20
    events = _activate(
        engine, instance, "preserve_life", dice=[],
        allocations=[
            {"target_id": "player:gm", "amount": 10},
            {"target_id": "enemy:goblin-1", "amount": 5},
        ],
    )
    rows = _of_type(events, "resource.changed")
    assert sorted(row["target_id"] for row in rows) == [
        "enemy:goblin-1", "player:gm",
    ]
    assert sum(row["allocated_amount"] for row in rows) == 15
    assert _of_type(events, "dnd2024.class_resource.spent")[0]["amount"] == 1


# ---------------------------------------------------------------------------
# 8. the two healing riders
# ---------------------------------------------------------------------------


def _cure_wounds(engine, instance, slot_level: int, dice, target="player:gm") -> dict:
    state = ruleset_runtime.state(instance)
    return engine.resolve_intent(instance, {
        "intent_id": f"cure-{state['version']}", "type": "cast_spell",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:cure_wounds", "slot_level": slot_level,
        "target_id": target,
    }, SequenceRng(list(dice)))


def test_disciple_of_life_adds_two_plus_the_slot_level(sheets) -> None:
    engine, instance = _setup(sheets["cleric6"])
    _start(engine, instance)
    _canonical(instance)["resources"]["hp"] = 5
    events = _apply(engine, instance, _cure_wounds(engine, instance, 1, [4]))
    heals = _of_type(events, "resource.changed")
    assert [row.get("feature_id") for row in heals] == [None, "disciple_of_life"]
    assert heals[0]["amount"] == 4 + 4   # 2d8 里只有一颗？见下：Cure Wounds 是 2d8
    # Disciple: 2 + 1 环 = 3
    assert heals[1]["amount"] == 3


def test_disciple_of_life_pays_the_real_slot_level_on_an_upcast(sheets) -> None:
    engine, instance = _setup(sheets["cleric6"])
    _start(engine, instance)
    _canonical(instance)["resources"]["hp"] = 5
    events = _apply(engine, instance, _cure_wounds(engine, instance, 3, [4, 4, 4, 4]))
    heals = _of_type(events, "resource.changed")
    assert heals[-1]["feature_id"] == "disciple_of_life"
    assert heals[-1]["amount"] == 5  # 2 + 3 环


def test_a_cast_without_a_slot_is_not_a_disciple_trigger(sheets) -> None:
    engine, instance = _setup(sheets["cleric6"])
    _start(engine, instance)
    # slot_level 0 不是"零环"，它表示这次施法没有花槽；Cleric 没有免费施法
    # 资源，所以这种请求在权威边界就被拒绝，Disciple 自然也无从触发。
    resolved = _cure_wounds(engine, instance, 0, [4])
    assert resolved["ok"] is False


def test_blessed_healer_heals_its_owner_once(sheets) -> None:
    engine, instance = _setup(sheets["cleric6"])
    _add_ally(instance, sheets["cleric6"])
    _start(engine, instance, rolls=(3, 20, 8))
    _canonical(instance)["resources"]["hp"] = 4
    _canonical(instance)["resources"]["max_hp"] = 60
    # 治疗盟友之后，施法者自己回 2+1；盟友不是自己，条件才成立。
    _canonical(instance, "ally")["resources"]["hp"] = 10
    events = _apply(
        engine, instance,
        _cure_wounds(engine, instance, 1, [4, 4], target="player:ally"),
    )
    riders = [
        row for row in _of_type(events, "resource.changed")
        if row.get("feature_id") == "blessed_healer"
    ]
    assert len(riders) == 1
    assert riders[0]["target_id"] == "player:gm"
    assert riders[0]["amount"] == 3


def test_blessed_healer_does_not_fire_on_a_pure_self_heal(sheets) -> None:
    engine, instance = _setup(sheets["cleric6"])
    _start(engine, instance)
    _canonical(instance)["resources"]["hp"] = 4
    events = _apply(engine, instance, _cure_wounds(engine, instance, 1, [4, 4]))
    assert not [
        row for row in _of_type(events, "resource.changed")
        if row.get("feature_id") == "blessed_healer"
    ]


def test_supreme_healing_maximises_the_spell_and_keeps_the_rider_fixed(sheets) -> None:
    engine, instance = _setup(sheets["cleric17"])
    _start(engine, instance)
    _canonical(instance)["resources"]["hp"] = 5
    events = _apply(engine, instance, _cure_wounds(engine, instance, 1, [1]))
    heals = _of_type(events, "resource.changed")
    assert heals[0].get("maximized") is True
    assert heals[0]["amount"] == 19  # 2d8 全 8，再加 Wis +3
    assert heals[-1]["feature_id"] == "disciple_of_life"
    assert heals[-1]["amount"] == 3


# ---------------------------------------------------------------------------
# 9. ledger, replay and forgery
# ---------------------------------------------------------------------------


def test_the_whole_batch_replays_to_the_same_state(sheets, bundle) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance)
    events = _activate(
        engine, instance, "divine_spark",
        dice=[6], target_id="enemy:goblin-1", spark_mode="heal",
    )
    ledger = [
        event
        for batch in ruleset_runtime.event_ledger(instance)
        for event in (batch.get("events") or [])
    ]
    assert _of_type(ledger, "dnd2024.class_resource.spent")
    assert _of_type(events, "resource.changed")
    # 事件批次里有真实的骰面，重放只应用不重掷。
    assert _of_type(events, "resource.changed")[0]["rolls"] == [6]


def test_a_forged_channel_use_without_the_resource_is_refused(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance)
    _pool(instance, "channel_divinity")["current"] = 0
    resolved = _capability(
        engine, instance, "divine_spark",
        dice=[6], target_id="enemy:goblin-1", spark_mode="heal",
    )
    assert resolved["ok"] is False
    assert "channel" in resolved["error"].lower()


def test_a_forged_target_out_of_range_is_refused(sheets) -> None:
    engine, instance = _setup(sheets["cleric5"])
    _start(engine, instance, enemies=[_enemy(position=200)])
    resolved = _capability(
        engine, instance, "divine_spark",
        dice=[6], target_id="enemy:goblin-1", spark_mode="heal",
    )
    assert resolved["ok"] is False
    assert "out of range" in resolved["error"]


def test_the_pools_and_conditions_survive_a_save_and_load(sheets, bundle) -> None:
    engine, instance = _setup(sheets["paladin14"])
    _start(engine, instance)
    _conditions(instance)["poisoned"] = {"source": "probe"}
    _activate(
        engine, instance, "lay_on_hands", dice=[],
        target_id="player:gm", healing_amount=10, remove_conditions=["poisoned"],
    )
    reloaded = deepcopy(instance.get_character_sheet("gm"))["ruleset_character"]
    assert reloaded["resources"]["class"]["lay_on_hands"]["current"] == 55
    assert "poisoned" not in reloaded["conditions"]
