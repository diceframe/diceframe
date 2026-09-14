"""#companion 任务 Phase 4：非战斗（exploration）手动施法。

- 仅在 combat 非 active 时可用；玩家本人施法，资源走 canonical。
- 不复用战斗 action economy；不新建第二份法术位。
- 未自动化的已知法术：合法施放 + 扣法术位 + resolution=narrative。
"""

from __future__ import annotations

from tests.rulesets.test_dnd2024_combat import _character, _goblin, _start
from src.engine.game_instance import GameInstance
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.exploration import Dnd2024ExplorationEngine
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


class SequenceRng:
    def __init__(self, values: list[int]) -> None:
        self.values = list(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0) if self.values else minimum
        assert minimum <= value <= maximum
        return value


def _exploration_engine(runtime: Dnd2024Runtime) -> Dnd2024ExplorationEngine:
    return Dnd2024ExplorationEngine(runtime.load_bundle("en"), locale="en")


def _wizard_instance() -> tuple[Dnd2024Runtime, GameInstance, Dnd2024ExplorationEngine]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-explore", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    first = _character(runtime, "curious_arcanist", "Arden")
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": first}
    # 测试注入治疗法术（原预设不含）+ 治疗位
    class_magic = first["ruleset_character"]["spellcasting"]["class"]
    prepared = class_magic.setdefault("prepared_spell_refs", [])
    prepared.append("spell:cure_wounds")
    prepared.append("spell:bless")
    class_magic["slots_current"] = {"1": 2}
    assert instance.bind_ruleset_runtime(first["rule_binding"])
    return runtime, instance, _exploration_engine(runtime)


def test_exploration_cast_available_only_outside_combat() -> None:
    runtime, instance, engine = _wizard_instance()
    combat = Dnd2024CombatEngine(
        runtime.load_bundle("en"), EncounterAccess.sandbox(),
    )

    available = engine.available_intents(instance, "player:gm")
    assert any(item["type"] == "exploration.cast_spell" for item in available)

    _start(combat, instance)  # 进入战斗
    available = engine.available_intents(instance, "player:gm")
    assert not any(item["type"] == "exploration.cast_spell" for item in available)


def test_exploration_heal_consumes_slot_and_heals_party_target() -> None:
    """§37.11/37.12：扣法术位、写 cast 事件（context=exploration）、HP 恢复不超上限。"""
    runtime, instance, engine = _wizard_instance()
    # 队友受伤（hp 6 / max 20）
    party = instance.ruleset_state.setdefault("party", {"companions": {}})
    party["companions"]["mira"] = {
        "id": "mira", "name": "Mira", "controller": "ai", "active": True,
        "ruleset_character": {
            "resources": {"hp": 6, "max_hp": 20},
            "conditions": {"unconscious": {"source": "zero_hp"}} if False else {},
            "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "derived": {"armor_class": 12, "speed": 30},
            "spellcasting": {"class": {
                "ability": "wis", "slots_current": {}, "concentration": None,
                "prepared_spell_refs": [], "cantrip_refs": [],
            }},
            "build": {},
        },
    }
    slots_before = (
        instance.players["gm"]["character_sheet"]["ruleset_character"]
        ["spellcasting"]["class"]["slots_current"]["1"]
    )

    resolved = engine.resolve_intent(instance, {
        "intent_id": "exp-heal-1", "type": "exploration.cast_spell",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:cure_wounds", "slot_level": 1,
        "target_ids": ["companion:mira"],
    }, SequenceRng([5, 5]))
    assert resolved["ok"] is True
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True

    mira = instance.ruleset_state["party"]["companions"]["mira"]["ruleset_character"]
    assert mira["resources"]["hp"] > 6
    assert mira["resources"]["hp"] <= mira["resources"]["max_hp"]
    slots_after = (
        instance.players["gm"]["character_sheet"]["ruleset_character"]
        ["spellcasting"]["class"]["slots_current"]["1"]
    )
    assert slots_after == slots_before - 1
    assert any(
        event["type"] == "dnd2024.spell.cast" and event.get("context") == "exploration"
        for event in resolved["events"]
    )


def test_exploration_concentration_replaces_previous() -> None:
    """§37.13：新专注法术结束旧专注。"""
    runtime, instance, engine = _wizard_instance()

    first = engine.resolve_intent(instance, {
        "intent_id": "exp-bless-1", "type": "exploration.cast_spell",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:bless", "slot_level": 1,
        "target_ids": ["player:gm"],
    }, SequenceRng([1]))
    assert first["ok"] is True
    engine.apply_batch(instance, first["event_batch"])
    canonical = instance.players["gm"]["character_sheet"]["ruleset_character"]
    assert canonical["spellcasting"]["class"]["concentration"]["spell_ref"] == "spell:bless"

    second = engine.resolve_intent(instance, {
        "intent_id": "exp-bless-2", "type": "exploration.cast_spell",
        "expected_version": 1, "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:bless", "slot_level": 1,
        "target_ids": ["player:gm"],
    }, SequenceRng([1]))
    engine.apply_batch(instance, second["event_batch"])
    events = second["events"]
    assert any(
        event["type"] == "dnd2024.concentration.ended"
        and event.get("reason") == "new_concentration_spell"
        for event in events
    )
    canonical = instance.players["gm"]["character_sheet"]["ruleset_character"]
    assert canonical["spellcasting"]["class"]["concentration"]["spell_ref"] == "spell:bless"


def test_exploration_narrative_cast_consumes_slot_without_effect() -> None:
    """§32：已知法术无确定性效果 → 合法施放、扣位、resolution=narrative。"""
    runtime, instance, engine = _wizard_instance()

    resolved = engine.resolve_intent(instance, {
        "intent_id": "exp-detect-1", "type": "exploration.cast_spell",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:detect_magic", "slot_level": 1,
        "target_ids": [],
    }, SequenceRng([1]))
    assert resolved["ok"] is True
    assert any(
        event["type"] == "dnd2024.spell.narrative"
        and event.get("resolution") == "narrative"
        for event in resolved["events"]
    )
    engine.apply_batch(instance, resolved["event_batch"])
    slots = (
        instance.players["gm"]["character_sheet"]["ruleset_character"]
        ["spellcasting"]["class"]["slots_current"]["1"]
    )
    assert slots == 1  # 2 → 1


def test_exploration_rejects_offensive_spells_and_forged_submitters() -> None:
    runtime, instance, engine = _wizard_instance()

    offensive = engine.validate_intent(instance, {
        "intent_id": "exp-mm", "type": "exploration.cast_spell",
        "expected_version": 0, "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:magic_missile", "slot_level": 1,
        "target_ids": ["player:gm"],
    })
    assert offensive["ok"] is False

    forged = engine.validate_intent(instance, {
        "intent_id": "exp-forge", "type": "exploration.cast_spell",
        "expected_version": 0, "submitted_by": "someone-else",
        "actor_id": "player:gm", "spell_ref": "spell:cure_wounds",
        "slot_level": 1, "target_ids": ["player:gm"],
    })
    assert forged["ok"] is False

    # 战斗进行中拒绝
    combat = Dnd2024CombatEngine(
        runtime.load_bundle("en"), EncounterAccess.sandbox(),
    )
    intent = {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_goblin()],
    }
    resolved = combat.resolve_intent(instance, intent, SequenceRng([20, 1]))
    combat.apply_batch(instance, resolved["event_batch"])
    assert engine.validate_intent(instance, {
        "intent_id": "exp-in-combat", "type": "exploration.cast_spell",
        "expected_version": instance.ruleset_state["version"],
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:cure_wounds", "slot_level": 1,
        "target_ids": ["player:gm"],
    })["ok"] is False
