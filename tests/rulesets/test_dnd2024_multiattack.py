"""S0-MM3b: one Multiattack action, N attacks, one action spent."""

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
        game_key=("test", "dnd2024-multiattack", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    first = _character(runtime, "stalwart_guardian", "Arden")
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": first}
    assert instance.bind_ruleset_runtime(first["rule_binding"])
    return Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox()), instance


def _attack(attack_id: str, name: str, *, bonus: int = 30, damage: str = "1d6", kind: str = "piercing",
            range_ft: int = 5, long_range: int | None = None, **extra) -> dict:
    return {
        "id": attack_id, "name": name, "attack_bonus": bonus,
        "damage": [{"dice": damage, "type": kind}],
        "range": range_ft, "long_range": long_range or range_ft,
        **extra,
    }


def _enemy(attacks: list[dict], actions: list[dict], *, action_id: str = "multiattack-0") -> dict:
    return {
        "id": "beast-1", "name": "Beast", "hp": 60, "armor_class": 12,
        "speed": 40, "position": 5, "initiative_modifier": 10, "size": "large",
        "abilities": {"str": 16, "dex": 12, "con": 14, "int": 3, "wis": 12, "cha": 6},
        "saving_throws": {},
        "attacks": attacks,
        "actions": actions,
    }


def _events(engine, instance, intent: dict, rng) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, rng)
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]["events"]


def _start(engine, instance, enemy: dict) -> list[dict]:
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [enemy],
    }
    return _events(engine, instance, intent, SequenceRng([1, 20]))


def _multiattack(engine, instance, *, rolls: list[int], choice: str | None = None,
                 intent_id: str = "multi-1") -> list[dict]:
    intent = {
        "intent_id": intent_id, "type": "multiattack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:beast-1",
        "target_id": "player:gm", "action_id": "multiattack-0",
    }
    if choice is not None:
        intent["choice"] = choice
    return _events(engine, instance, intent, SequenceRng(rolls))


def _of_type(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("type") == kind]


def _hits(events: list[dict]) -> list[dict]:
    return [
        event for event in _of_type(events, "resource.changed")
        if event.get("resource") == "hp" and event.get("target_id") == "player:gm"
    ]


def _player_hp(instance) -> int:
    sheet = instance.players["gm"]["character_sheet"]["ruleset_character"]
    return int(sheet["resources"]["hp"])


# 1. repeat -----------------------------------------------------------------


def test_repeat_makes_one_intent_into_three_attacks() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("rend-0", "Rend")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "repeat": {"attack_id": "rend-0", "count": 3}}],
    ))
    before = _player_hp(instance)

    events = _multiattack(engine, instance, rolls=[15, 2, 15, 2, 15, 2])

    assert len([row for row in _of_type(events, "check.resolved") if row["kind"] == "attack"]) == 3
    assert len(_hits(events)) == 3
    assert before - _player_hp(instance) == sum(row["amount"] for row in _hits(events))
    # 一次动作：动作位只被扣一次，剩余攻击额度归零。
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    assert economy["action"] == 0
    assert economy["attacks_remaining"] == 0


def test_gm_available_intents_exposes_enemy_multiattack() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("rend-0", "Rend")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "repeat": {"attack_id": "rend-0", "count": 2}}],
    ))

    entry = next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "multiattack"
    )

    assert entry["actor_id"] == "enemy:beast-1"
    assert entry["action_id"] == "multiattack-0"
    assert [target["actor_id"] for target in entry["targets"]] == ["player:gm"]


def test_repeat_costs_exactly_one_action() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("rend-0", "Rend")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "repeat": {"attack_id": "rend-0", "count": 2}}],
    ))

    events = _multiattack(engine, instance, rolls=[15, 2, 15, 2])

    spent = _of_type(events, "dnd2024.attack.spent")
    assert [row["resource"] for row in spent] == ["action", "action"]
    # 第二次攻击不是"又花了一个动作"：它从第一次开出的额度里扣。
    assert spent[0]["attacks_per_action"] == 2
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 0


# 2. combo ------------------------------------------------------------------


def test_combo_uses_each_attack_own_profile() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [
            _attack("bite-0", "Bite", bonus=30, damage="1d6", kind="piercing"),
            _attack("claw-0", "Claw", bonus=30, damage="1d8", kind="slashing"),
        ],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "combo": [{"attack_id": "bite-0", "count": 1},
                    {"attack_id": "claw-0", "count": 1}]}],
    ))

    events = _multiattack(engine, instance, rolls=[15, 4, 15, 6])

    hits = _hits(events)
    assert [(row["damage_type"], row["amount"]) for row in hits] == [
        ("piercing", 4), ("slashing", 6),
    ]


# 3. choice -----------------------------------------------------------------


def test_choice_resolves_only_the_branch_that_was_named() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("claw-0", "Claw"), _attack("flame-0", "Hurl Flame", kind="fire")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "choice": [
              {"label": "claws", "parts": [{"attack_id": "claw-0", "count": 2}]},
              {"label": "flame", "parts": [{"attack_id": "flame-0", "count": 1}]},
          ]}],
    ))

    events = _multiattack(engine, instance, choice="flame", rolls=[15, 5])

    hits = _hits(events)
    assert len(hits) == 1
    assert hits[0]["damage_type"] == "fire"


def test_choice_without_a_branch_is_refused() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("claw-0", "Claw")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "choice": [
              {"label": "one", "parts": [{"attack_id": "claw-0", "count": 1}]},
              {"label": "two", "parts": [{"attack_id": "claw-0", "count": 2}]},
          ]}],
    ))
    intent = {
        "intent_id": "multi-1", "type": "multiattack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:beast-1",
        "target_id": "player:gm", "action_id": "multiattack-0",
        "choice": "three",
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([15]))

    assert resolved["ok"] is False


# 4. 引用解析失败 ------------------------------------------------------------


def test_an_unknown_attack_reference_is_refused() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("claw-0", "Claw")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "repeat": {"attack_id": "ghost-0", "count": 2}}],
    ))
    intent = {
        "intent_id": "multi-1", "type": "multiattack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:beast-1",
        "target_id": "player:gm", "action_id": "multiattack-0",
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([15]))

    assert resolved["ok"] is False
    assert "does not have" in str(resolved["error"])


# 5. 别名限定在生物内 --------------------------------------------------------


def test_an_alias_resolves_inside_the_creature_only() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("claw-0", "Claw", aliases=["Rend"])],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "repeat": {"attack_id": "rend", "count": 2}}],
    ))

    events = _multiattack(engine, instance, rolls=[15, 3, 15, 3])

    assert len(_hits(events)) == 2


def test_a_name_match_is_case_insensitive_but_stays_local() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("bite-0", "Bite"), _attack("claw-0", "Claw")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "repeat": {"attack_id": "claw", "count": 1}}],
    ))

    events = _multiattack(engine, instance, rolls=[15, 3])

    assert [row["damage_type"] for row in _hits(events)] == ["piercing"]


# 6. 原子性 ------------------------------------------------------------------


def test_a_part_out_of_range_rejects_the_whole_intent() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [
            _attack("claw-0", "Claw"),
            _attack("spit-0", "Spit", range_ft=5, long_range=10),
        ],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "combo": [{"attack_id": "claw-0", "count": 1},
                    {"attack_id": "spit-0", "count": 1}]}],
    ))
    # 玩家在 0，野兽在 5：Spit 的 10 尺够，把野兽挪远一点让它不够。
    ruleset_runtime.state(instance)["combat"]["positions"]["enemy:beast-1"] = 40
    before = _player_hp(instance)
    intent = {
        "intent_id": "multi-1", "type": "multiattack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:beast-1",
        "target_id": "player:gm", "action_id": "multiattack-0",
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 3, 15, 3]))

    assert resolved["ok"] is False
    assert "out of range" in str(resolved["error"])
    # 整批没有落地：第一击也没打出来，动作还在。
    assert _player_hp(instance) == before
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 1


def test_a_defeated_target_stops_the_remaining_attacks() -> None:
    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("rend-0", "Rend", damage="100")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "repeat": {"attack_id": "rend-0", "count": 3}}],
    ))

    events = _multiattack(engine, instance, rolls=[15, 1])

    truncated = _of_type(events, "dnd2024.multiattack.truncated")
    assert [(row["reason"], row["attacks_made"], row["attacks_left"]) for row in truncated] == [
        ("target_defeated", 1, 2),
    ]


# 契约 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        {"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack"},
        {"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
         "repeat": {"attack_id": "claw-0", "count": 2},
         "combo": [{"attack_id": "claw-0", "count": 1}]},
        {"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
         "repeat": {"attack_id": "claw-0", "count": 11}},
        {"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
         "choice": [{"label": "only", "parts": [{"attack_id": "claw-0", "count": 1}]}]},
        {"id": "multiattack-0", "name": "Multiattack", "kind": "save_action"},
        {"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
         "repeat": {"attack_id": "claw-0", "count": 2}, "surprise": 1},
    ],
)
def test_a_malformed_multiattack_action_is_refused(action: dict) -> None:
    engine, instance = _instance()
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_enemy([_attack("claw-0", "Claw")], [action])],
    }

    resolved = engine.resolve_intent(instance, intent, SequenceRng([1, 20]))

    assert resolved["ok"] is False


def test_the_catalog_multiattacks_all_resolve() -> None:
    catalog = MonsterCatalog(json.loads(CATALOG.read_text(encoding="utf-8")))

    repeats = 0
    combos = 0
    # 同 `test_dnd2024_attack_riders`：这批数字描述 CR 0–1 档，目录已扩到 CR 4。
    for entry in [row for row in catalog.all() if float(cr_value(row["cr"])) <= 1]:
        attack_ids = {attack["id"] for attack in entry["attacks"]}
        for action in entry.get("actions") or []:
            parts = [action["repeat"]] if "repeat" in action else action.get("combo") or []
            for part in parts:
                assert part["attack_id"] in attack_ids, (entry["id"], part)
            repeats += 1 if "repeat" in action else 0
            combos += 1 if "combo" in action else 0
    # 11 条可表达：10 条 repeat + 1 条 combo（Brown Bear）。其余 6 条需要
    # 替换攻击、自由组合或施法，未导入。
    assert (repeats, combos) == (10, 1)


# 截断与额度：剩下的攻击仍可打，但不能再开一次 Multiattack ------------------


def _wizard_instance() -> tuple[Dnd2024CombatEngine, GameInstance]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-multiattack-wizard", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    first = _character(runtime, "curious_arcanist", "Mira")
    first["ruleset_character"]["spellcasting"] = {"class": {
        "ability": "int", "slots_current": {"1": 3},
        "prepared_spell_refs": ["spell:shield"], "cantrip_refs": [],
    }}
    instance.players["gm"] = {"character_name": "Mira", "character_sheet": first}
    assert instance.bind_ruleset_runtime(first["rule_binding"])
    return Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox()), instance


def test_a_truncated_multiattack_keeps_the_rest_of_its_quota() -> None:
    """窗口挡在第二次攻击前：剩下的攻击留在额度里，但不能变成第二次动作。"""

    engine, instance = _wizard_instance()
    _start(engine, instance, _enemy(
        [_attack("rend-0", "Rend")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "repeat": {"attack_id": "rend-0", "count": 3}}],
    ))

    events = _multiattack(engine, instance, rolls=[15, 2])

    truncated = _of_type(events, "dnd2024.multiattack.truncated")
    assert [(row["reason"], row["attacks_made"], row["attacks_left"]) for row in truncated] == [
        ("pending_decision", 1, 2),
    ]
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    assert economy["attacks_remaining"] == 2
    # 决策未答完之前，任何新意图都会被挡（比"动作花光"更靠前的一道闸）。
    blocked = engine.resolve_intent(instance, {
        "intent_id": "multi-2", "type": "multiattack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:beast-1",
        "target_id": "player:gm", "action_id": "multiattack-0",
    }, SequenceRng([15, 2]))
    assert blocked["ok"] is False
    assert "reaction spell decision" in str(blocked["error"])

    _decline(engine, instance)
    # 额度只兑现 Attack action 的剩余次数：答完窗口后再提交一次 Multiattack
    # 仍然要被拒，剩下的两击只能用普通 attack 打出去。
    refused = engine.resolve_intent(instance, {
        "intent_id": "multi-3", "type": "multiattack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:beast-1",
        "target_id": "player:gm", "action_id": "multiattack-0",
    }, SequenceRng([15, 2]))
    assert refused["ok"] is False
    assert "action has already been spent" in str(refused["error"])

    follow_up = engine.resolve_intent(instance, {
        "intent_id": "attack-leftover", "type": "attack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:beast-1",
        "target_id": "player:gm", "attack_id": "rend-0",
    }, SequenceRng([15, 2]))
    assert follow_up["ok"] is True


def _decline(engine, instance) -> list[dict]:
    decision = next(
        row for row in ruleset_runtime.state(instance)["combat"]["pending_decisions"]
        if row.get("kind") == "hit_reaction_spell"
    )
    return _events(engine, instance, {
        "intent_id": "decline-1", "type": "decision.resolve",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "decision_id": decision["decision_id"],
        "option": "decline",
    }, SequenceRng([2]))


def test_roll_ids_stay_unique_inside_one_multiattack() -> None:
    """每一击都在自己的 list 里计数，整批里不能出现两个 ``attack:0``。"""

    engine, instance = _instance()
    _start(engine, instance, _enemy(
        [_attack("rend-0", "Rend")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "repeat": {"attack_id": "rend-0", "count": 3}}],
    ))

    events = _multiattack(engine, instance, rolls=[15, 2, 15, 2, 15, 2])

    roll_ids = [
        row["roll_id"] for row in _of_type(events, "check.resolved")
        if row["kind"] == "attack"
    ]
    assert len(roll_ids) == len(set(roll_ids)) == 3


# ---------------------------------------------------------------------------
# S0-MM5：组合池（"in any combination"）
# ---------------------------------------------------------------------------

def _pool_enemy() -> dict:
    return _enemy(
        [_attack("bite-0", "Bite"), _attack("claw-0", "Claw")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "pool": {"attacks": ["bite-0", "claw-0"], "count": 2}}],
    )


def _pool(engine, instance, *, sequence, rolls) -> list[dict]:
    intent = {
        "intent_id": "pool-1", "type": "multiattack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:beast-1",
        "target_id": "player:gm", "action_id": "multiattack-0",
        "sequence": list(sequence),
    }
    return _events(engine, instance, intent, SequenceRng(rolls))


def test_a_pool_resolves_in_the_declared_order() -> None:
    engine, instance = _instance()
    _start(engine, instance, _pool_enemy())

    events = _pool(engine, instance, sequence=["claw-0", "bite-0"], rolls=[15, 2, 15, 2])

    hits = _hits(events)
    assert len(hits) == 2, "池声明两次就该掷两次"
    # 顺序即结算顺序：第一次用的是 Claw。
    assert hits[0]["damage_type"] == "piercing"


@pytest.mark.parametrize("sequence", [["bite-0"], ["bite-0", "tail-0"],
                                     ["bite-0", "claw-0", "bite-0"]])
def test_a_pool_refuses_a_sequence_it_cannot_honour(sequence) -> None:
    engine, instance = _instance()
    _start(engine, instance, _pool_enemy())

    with pytest.raises(Exception):
        _pool(engine, instance, sequence=sequence, rolls=[15, 2] * 3)


def test_a_pool_naming_an_attack_it_does_not_have_is_refused() -> None:
    engine, instance = _instance()
    bad = _enemy(
        [_attack("bite-0", "Bite")],
        [{"id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
          "pool": {"attacks": ["bite-0", "tail-0"], "count": 2}}],
    )

    # 动作契约只能查 id 形状，看不到该生物的攻击表；引用是否成立在展开时回答。
    _start(engine, instance, bad)
    with pytest.raises(Exception):
        _pool(engine, instance, sequence=["bite-0", "tail-0"], rolls=[15, 2, 15, 2])
