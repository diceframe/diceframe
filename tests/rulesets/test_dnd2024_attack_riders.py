"""S0-MM3a: an attack's damage instances and what a hit does beyond damage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.monsters.catalog import MonsterCatalog, cr_value
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

CATALOG = (
    Path(__file__).resolve().parents[2]
    / "templates/rulesets/dnd2024_srd/content/monster_catalogs/srd_monsters.json"
)


@dataclass
class SequenceRng:
    values: list[int]

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum
        return value


def _character(runtime: Dnd2024Runtime, preset_id: str, name: str) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": name},
    )


def _instance() -> tuple[Dnd2024CombatEngine, GameInstance]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-attack-riders", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    first = _character(runtime, "stalwart_guardian", "Arden")
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": first}
    assert instance.bind_ruleset_runtime(first["rule_binding"])
    return Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox()), instance


def _wolf(*, attack: dict | None = None) -> dict:
    return {
        "id": "wolf-1", "name": "Wolf", "hp": 11, "armor_class": 12,
        "speed": 40, "position": 5, "initiative_modifier": 10,
        "size": "medium",
        "abilities": {"str": 14, "dex": 15, "con": 12, "int": 3, "wis": 12, "cha": 6},
        "saving_throws": {},
        "attacks": [attack or {
            "id": "bite-0", "name": "Bite", "attack_bonus": 30,
            "damage": [{"dice": "1d6+2", "type": "piercing"}],
            "range": 5, "long_range": 5,
        }],
    }


def _events(engine, instance, intent: dict, rng) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]["events"]


def _start(engine, instance, enemy: dict, *, rng: SequenceRng | None = None) -> list[dict]:
    """Start a fight the wolf opens: its initiative beats the player's.

    The player rolls first (1) and the enemy second (20 + its modifier), so the
    enemy holds the turn and can be driven as the current actor.
    """

    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [enemy],
    }
    return _events(engine, instance, intent, rng or SequenceRng([1, 20]))


def _player_conditions(instance) -> dict:
    sheet = instance.players["gm"]["character_sheet"]["ruleset_character"]
    return sheet.get("conditions") or {}


def _state(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _bite(engine, instance, attack_id: str = "bite-0", *, rolls: list[int]) -> list[dict]:
    intent = {
        "intent_id": "enemy-bite", "type": "attack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:wolf-1",
        "target_id": "player:gm", "attack_id": attack_id,
    }
    return _events(engine, instance, intent, SequenceRng(rolls))


def _of_type(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("type") == kind]


def _hp_events(events: list[dict]) -> list[dict]:
    return [
        event for event in _of_type(events, "resource.changed")
        if event.get("resource") == "hp"
    ]


def _ready(engine, instance, condition: str) -> None:
    """Put one condition on the player without going through a spell."""

    version = int(ruleset_runtime.state(instance)["version"])
    batch = {
        "batch_id": f"batch_{version}", "intent_id": f"fixture:{version}",
        "intent_type": "combat.message", "expected_version": version,
        "result_version": version + 1, "source_ref": "test",
        "events": [
            {"type": "intent.submitted", "intent_type": "combat.message",
             "actor_id": "player:gm", "submitted_by": "gm"},
            {"type": "condition.applied", "target_id": "player:gm",
             "condition": condition, "duration": "combat"},
        ],
    }
    assert engine.apply_batch(instance, batch)["applied"] is True


# 1. 单段攻击照旧（回归） ---------------------------------------------------


def test_one_damage_part_still_resolves_as_one_instance() -> None:
    engine, instance = _instance()
    _start(engine, instance, _wolf())

    events = _bite(engine, instance, rolls=[15, 4])

    hits = _hp_events(events)
    assert len(hits) == 1
    assert hits[0]["damage_type"] == "piercing"
    assert hits[0]["amount"] == 6
    # 旧形状（damage 字符串 + damage_type）在装配期归一到数组，状态里只有
    # 一种形状。
    stored = _state(instance)["enemies"]["wolf-1"]["attacks"][0]
    assert stored["damage"] == [{"dice": "1d6+2", "type": "piercing"}]
    assert "damage_type" not in stored


# 2. 两段伤害各自过抗性 ------------------------------------------------------


def test_second_part_is_resisted_on_its_own() -> None:
    engine, instance = _instance()
    _start(engine, instance, _wolf(attack={
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [
            {"dice": "2d6", "type": "slashing"},
            {"dice": "2d6", "type": "fire"},
        ],
        "range": 5, "long_range": 5,
    }))
    # 狂暴抗钝击/穿刺/斩击，不抗火焰：只有第一段被减半。
    _ready(engine, instance, "rage")

    events = _bite(engine, instance, rolls=[15, 3, 3, 3, 3])

    hits = _hp_events(events)
    assert [(row["damage_type"], row["amount"]) for row in hits] == [
        ("slashing", 3), ("fire", 6),
    ]
    assert hits[0]["resisted"] is True
    assert "resisted" not in hits[1]


def test_two_parts_are_rolled_and_reported_in_declaration_order() -> None:
    engine, instance = _instance()
    _start(engine, instance, _wolf(attack={
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [
            {"dice": "1d6", "type": "slashing"},
            {"dice": "1d6", "type": "fire"},
        ],
        "range": 5, "long_range": 5,
    }))

    events = _bite(engine, instance, rolls=[15, 3, 3])

    hits = _hp_events(events)
    assert [(row["damage_type"], row["amount"]) for row in hits] == [
        ("slashing", 3), ("fire", 3),
    ]


# 3. riders 顺序与语义 -------------------------------------------------------


def test_riders_land_in_declaration_order() -> None:
    engine, instance = _instance()
    _start(engine, instance, _wolf(attack={
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [{"dice": "1d6+2", "type": "piercing"}],
        "range": 5, "long_range": 5,
        "riders": [
            {"kind": "prone", "max_target_size": "medium"},
            {"kind": "grapple", "escape_dc": 12, "max_target_size": "medium"},
        ],
    }))

    events = _bite(engine, instance, rolls=[15, 4])

    applied = [
        event for event in _of_type(events, "condition.applied")
        if event.get("target_id") == "player:gm"
    ]
    assert [event["condition"] for event in applied] == ["prone", "grappled"]
    assert applied[1]["escape_dc"] == 12
    conditions = _player_conditions(instance)
    assert set(conditions) >= {"prone", "grappled"}
    assert conditions["grappled"]["escape_dc"] == 12


def test_a_size_gate_keeps_the_rider_off_a_bigger_target() -> None:
    engine, instance = _instance()
    _start(engine, instance, _wolf(attack={
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [{"dice": "1d6+2", "type": "piercing"}],
        "range": 5, "long_range": 5,
        # Tiny 上限：Medium 的玩家吃不到击倒。
        "riders": [{"kind": "prone", "max_target_size": "tiny"}],
    }))

    events = _bite(engine, instance, rolls=[15, 4])

    assert not _of_type(events, "condition.applied")
    skipped = _of_type(events, "dnd2024.rider.skipped")
    assert [row["reason"] for row in skipped] == ["size_gate"]


def test_a_push_moves_the_target_away_from_the_attacker() -> None:
    engine, instance = _instance()
    _start(engine, instance, _wolf(attack={
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [{"dice": "1d6+2", "type": "piercing"}],
        "range": 5, "long_range": 5,
        "riders": [{"kind": "push", "distance": 10}],
    }))
    before = _state(instance)["positions"]["player:gm"]

    events = _bite(engine, instance, rolls=[15, 4])

    moved = _of_type(events, "dnd2024.position.changed")
    # 玩家在 0、狼在 5：推开就是往负方向 10 尺，且是被迫移动。
    assert [(row["actor_id"], row["distance"], row["forced"]) for row in moved] == [
        ("player:gm", -10, True),
    ]
    assert _state(instance)["positions"]["player:gm"] == before - 10


def test_a_saving_throw_rider_only_lands_on_a_failure() -> None:
    attack = {
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [{"dice": "1d6+2", "type": "piercing"}],
        "range": 5, "long_range": 5,
        "riders": [{
            "kind": "condition", "condition": "poisoned",
            "save": {"ability": "con", "dc": 25},
            "duration": "target_turn_end",
        }],
    }
    engine, instance = _instance()
    _start(engine, instance, _wolf(attack=attack))

    # 豁免失败（DC 25 掷不过）：上毒。
    events = _bite(engine, instance, rolls=[15, 4, 3])
    failed = [row for row in _of_type(events, "check.resolved") if row["kind"] == "saving_throw"]
    assert [row["kind"] for row in failed] == ["saving_throw"]
    assert "poisoned" in _player_conditions(instance)


def test_a_successful_save_leaves_the_condition_off() -> None:
    attack = {
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [{"dice": "1d6+2", "type": "piercing"}],
        "range": 5, "long_range": 5,
        "riders": [{
            "kind": "condition", "condition": "poisoned",
            "save": {"ability": "con", "dc": 2},
            "duration": "target_turn_end",
        }],
    }
    engine, instance = _instance()
    _start(engine, instance, _wolf(attack=attack))

    events = _bite(engine, instance, rolls=[15, 4, 3])

    checks = [row for row in _of_type(events, "check.resolved") if row["kind"] == "saving_throw"]
    assert [row["success"] for row in checks] == [True]
    assert not _of_type(events, "condition.applied")
    assert "poisoned" not in _player_conditions(instance)


# 4. 条件免疫照常拦截 --------------------------------------------------------


def test_riders_go_through_the_condition_immunity_gate() -> None:
    """The rider emits an ordinary ``condition.applied``; the gate blocks it.

    Stat-block immunity (S0-MM1) is checked once, at the end of the intent, so
    a rider needs no immunity logic of its own — and must not have any, or the
    two answers could disagree.
    """

    engine, instance = _instance()
    _start(engine, instance, _wolf(attack={
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [{"dice": "1d6+2", "type": "piercing"}],
        "range": 5, "long_range": 5,
        "riders": [{"kind": "prone"}, {"kind": "condition", "condition": "poisoned"}],
    }))
    events = _bite(engine, instance, rolls=[15, 4])
    applied = [
        event for event in _of_type(events, "condition.applied")
        if event.get("target_id") == "player:gm"
    ]
    assert [event["condition"] for event in applied] == ["prone", "poisoned"]

    # 玩家的免疫由特性声明（F13）；这里直接问那同一道闸门。
    blocked = engine._statblock_immunity_events(
        {"enemies": {"ghost": {"condition_immunities": ["prone"]}}},
        [
            {"type": "condition.applied", "target_id": "enemy:ghost",
             "condition": "prone", "source_actor_id": "player:gm"},
            {"type": "condition.applied", "target_id": "enemy:ghost",
             "condition": "poisoned", "source_actor_id": "player:gm"},
        ],
    )

    assert [event["type"] for event in blocked] == ["condition.blocked", "condition.applied"]
    assert blocked[0]["reason"] == "immune"


# 5-7. 契约拒绝 --------------------------------------------------------------


def test_legacy_single_damage_enemy_still_starts() -> None:
    engine, instance = _instance()
    _start(engine, instance, _wolf(attack={
        "id": "bite-0", "name": "Bite", "attack_bonus": 4,
        "damage": "1d6+2", "damage_type": "piercing", "range": 5,
    }))

    stored = _state(instance)["enemies"]["wolf-1"]["attacks"][0]
    assert stored["damage"] == [{"dice": "1d6+2", "type": "piercing"}]


@pytest.mark.parametrize(
    "attack",
    [
        {"id": "bite-0", "name": "Bite", "attack_bonus": 4},
        {"id": "bite-0", "name": "Bite", "attack_bonus": 4, "damage": []},
        {
            "id": "bite-0", "name": "Bite", "attack_bonus": 4,
            "damage": [{"dice": "1d6", "type": "piercing"}], "damage_type": "piercing",
        },
        {
            "id": "bite-0", "name": "Bite", "attack_bonus": 4,
            "damage": [{"dice": "1d6", "type": "screaming"}],
        },
        {
            "id": "bite-0", "name": "Bite", "attack_bonus": 4,
            "damage": [{"dice": "1d6", "type": "fire"}],
            "riders": [{"kind": "swallow"}],
        },
        {
            "id": "bite-0", "name": "Bite", "attack_bonus": 4,
            "damage": [{"dice": "1d6", "type": "fire"}],
            "riders": [{"kind": "grapple", "escape_dc": 99}],
        },
        {
            "id": "bite-0", "name": "Bite", "attack_bonus": 4,
            "damage": [{"dice": "1d6", "type": "fire"}],
            "riders": [{"kind": "push", "distance": 5}, {"kind": "push", "distance": 5},
                       {"kind": "push", "distance": 5}, {"kind": "push", "distance": 5}],
        },
        {
            "id": "bite-0", "name": "Bite", "attack_bonus": 4,
            "damage": [{"dice": "1d6", "type": "fire"}],
            "riders": [{"kind": "condition", "condition": "moonstruck"}],
        },
    ],
)
def test_a_malformed_attack_profile_is_refused(attack: dict) -> None:
    engine, instance = _instance()
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_wolf(attack=attack)],
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([20, 1]))

    assert resolved["ok"] is False
    assert "attack" in str(resolved.get("error") or "")


# 8. 目录全量重生成后仍可加载 ------------------------------------------------


def test_the_shipped_catalog_uses_the_structured_shape() -> None:
    catalog = MonsterCatalog(json.loads(CATALOG.read_text(encoding="utf-8")))

    # 目录现在覆盖到 CR 4（S0-MM4）；这三条断言描述的是**已交付的 CR 0–1 档**，
    # 所以按档过滤，而不是跟着目录总数走。
    delivered = [entry for entry in catalog.all() if float(cr_value(entry["cr"])) <= 1]
    assert len(delivered) == 135
    wolves = catalog.find(creature_type="beast", cr_max="1/4")
    assert wolves
    riders = 0
    multipart = 0
    for entry in delivered:
        for attack in entry["attacks"]:
            assert isinstance(attack["damage"], list)
            riders += len(attack.get("riders") or [])
            multipart += 1 if len(attack["damage"]) > 1 else 0
    # 精确覆盖数，不是"大于零"：解析退化时这里必须红。15 条 rider 与 22 条
    # 两段伤害就是 CR 0-1 里能被这三条句式确切读出的全部；其余（移动前提、
    # 分档失败、吞下、附着、生物类型前提）没有解析，见执行报告 §3/§6。
    assert (riders, multipart) == (15, 22)


def test_a_conditional_second_part_is_not_imported() -> None:
    """"plus 1d4 if the attack roll had Advantage" is not unconditional damage."""

    catalog = MonsterCatalog(json.loads(CATALOG.read_text(encoding="utf-8")))

    goblin = catalog.get("goblin-warrior")
    assert goblin is not None
    scimitar = next(attack for attack in goblin["attacks"] if attack["name"] == "Scimitar")
    assert scimitar["damage"] == [{"dice": "1d6+2", "type": "slashing"}]


def test_the_catalog_wolf_knocks_its_target_down() -> None:
    catalog = MonsterCatalog(json.loads(CATALOG.read_text(encoding="utf-8")))
    wolf = catalog.get("wolf")
    assert wolf is not None
    bite = next(attack for attack in wolf["attacks"] if attack["id"] == "bite-0")
    assert bite["damage"] == [{"dice": "1d6+2", "type": "piercing"}]
    assert bite["riders"] == [{"kind": "prone", "max_target_size": "medium"}]


# 受击反应窗口：窗口挡在中间时，第二段与 rider 不能丢 ----------------------


def _wizard_instance() -> tuple[Dnd2024CombatEngine, GameInstance]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-attack-riders-wizard", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    first = _character(runtime, "curious_arcanist", "Mira")
    # 护盾术与妖火靠注入的施法块提供：预设角色不保证带它们。
    first["ruleset_character"]["spellcasting"] = {"class": {
        "ability": "int", "slots_current": {"1": 3},
        "prepared_spell_refs": ["spell:shield", "spell:faerie_fire"],
        "cantrip_refs": [],
    }}
    instance.players["gm"] = {"character_name": "Mira", "character_sheet": first}
    assert instance.bind_ruleset_runtime(first["rule_binding"])
    return Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox()), instance


def _decline(engine, instance, *, rolls: list[int]) -> list[dict]:
    combat = _state(instance)
    decision = next(
        row for row in combat["pending_decisions"]
        if row.get("kind") == "hit_reaction_spell"
    )
    intent = {
        "intent_id": "decline-1", "type": "decision.resolve",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "decline",
    }
    return _events(engine, instance, intent, SequenceRng(list(rolls)))


def test_a_declined_shield_still_lands_the_second_part_and_the_rider() -> None:
    """命中经窗口收尾时，那一条路径同样要结算第二段与 rider。

    只有直通路径会走 ``_resolve_committed_hit``；受击反应收尾曾自己提交
    主伤害，第二段与 rider 会静默消失。
    """

    engine, instance = _wizard_instance()
    _start(engine, instance, _wolf(attack={
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [
            {"dice": "1d6", "type": "slashing"},
            {"dice": "1d6", "type": "fire"},
        ],
        "range": 5, "long_range": 5,
        "riders": [{"kind": "prone"}],
    }))

    opening = _bite(engine, instance, rolls=[15, 4])
    assert _of_type(opening, "dnd2024.decision.pending")

    events = _decline(engine, instance, rolls=[5])

    hits = [
        row for row in _hp_events(events) if row.get("target_id") == "player:gm"
    ]
    assert [row["damage_type"] for row in hits] == ["slashing", "fire"]
    assert [row["condition"] for row in _of_type(events, "condition.applied")] == ["prone"]


# 条件的生命周期：rider 不能活过这一场战斗 --------------------------------


def test_a_rider_condition_does_not_outlive_the_combat() -> None:
    engine, instance = _instance()
    _start(engine, instance, _wolf(attack={
        "id": "bite-0", "name": "Bite", "attack_bonus": 30,
        "damage": [{"dice": "1d6+2", "type": "piercing"}],
        "range": 5, "long_range": 5,
        "riders": [{"kind": "prone"}, {"kind": "grapple", "escape_dc": 12}],
    }))
    _bite(engine, instance, rolls=[15, 4])
    assert {"prone", "grappled"} <= set(_player_conditions(instance))

    intent = {
        "intent_id": "end-combat", "type": "combat.end",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm",
    }
    _events(engine, instance, intent, SequenceRng([]))

    # 没有"起身""挣脱"这两个动作时，一条没有时限的 rider 会跟着角色卡走到
    # 下一场战斗——那是数据泄漏，不是规则。
    assert _player_conditions(instance) == {}


def test_a_condition_immunity_blocks_a_rider_end_to_end() -> None:
    """不需要人工拨闸门：一条真实意图产出的 rider 条件会被免疫挡下。

    玩家侧没有"自带 rider 的武器"，所以端到端证据由"法术施加条件"提供：
    它与 rider 走的是同一个 ``condition.applied`` 出口，也就走同一道闸门。
    """

    engine, instance = _wizard_instance()
    _start(engine, instance, _wolf())
    # 敌人免疫 faerie_fire：这道阀门读的是 stat block 的 condition_immunities。
    ruleset_runtime.state(instance)["combat"]["enemies"]["wolf-1"][
        "condition_immunities"
    ] = ["faerie_fire"]
    _pass_turn(engine, instance, "enemy:wolf-1")
    intent = {
        "intent_id": "cast-faerie-fire", "type": "cast_spell",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:faerie_fire", "slot_level": 1,
        "target_ids": ["enemy:wolf-1"],
    }

    events = _events(engine, instance, intent, SequenceRng([1]))

    blocked = _of_type(events, "condition.blocked")
    assert [(row["target_id"], row["condition"], row["reason"]) for row in blocked] == [
        ("enemy:wolf-1", "faerie_fire", "immune"),
    ]
    conditions = ruleset_runtime.state(instance)["combat"]["enemies"]["wolf-1"]["conditions"]
    assert "faerie_fire" not in conditions


def _pass_turn(engine, instance, actor_id: str) -> list[dict]:
    intent = {
        "intent_id": f"pass-{ruleset_runtime.state(instance)['version']}",
        "type": "end_turn",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": actor_id,
    }
    return _events(engine, instance, intent, SequenceRng([]))
