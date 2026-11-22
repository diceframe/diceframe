"""S0-MM3e: ending a condition from inside it — escape and standing up.

Both read their requirement from state instead of the caller: the DC from the
applied instance, the allowed checks and the price from the condition type.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.check_modes import attack_sources
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


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
        game_key=("test", "dnd2024-escape", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    first = _character(runtime, "stalwart_guardian", "Arden")
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": first}
    assert instance.bind_ruleset_runtime(first["rule_binding"])
    return Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox()), instance


def _wolf() -> dict:
    return {
        "id": "wolf-1", "name": "Wolf", "hp": 11, "armor_class": 12,
        "speed": 40, "position": 5, "initiative_modifier": 10, "size": "medium",
        "abilities": {"str": 14, "dex": 15, "con": 12, "int": 3, "wis": 12, "cha": 6},
        "attacks": [{
            "id": "bite-0", "name": "Bite", "attack_bonus": 30,
            "damage": [{"dice": "1d6+2", "type": "piercing"}],
            "range": 5, "long_range": 5,
            "riders": [{"kind": "grapple", "escape_dc": 12, "max_target_size": "medium"}],
        }],
    }


def _events(engine, instance, intent: dict, rng) -> list[dict]:
    resolved = engine.resolve_intent(instance, intent, SequenceRng(rng))
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]["events"]


def _grappled(engine, instance) -> None:
    """Start a fight the wolf opens, then let it grab the player."""

    _events(engine, instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_wolf()],
    }, [1, 20])
    _events(engine, instance, {
        "intent_id": "bite", "type": "attack",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "enemy:wolf-1",
        "target_id": "player:gm", "attack_id": "bite-0",
    }, [15, 4])
    _pass_turn(engine, instance, "enemy:wolf-1")


def _pass_turn(engine, instance, actor_id: str) -> list[dict]:
    return _events(engine, instance, {
        "intent_id": f"pass-{ruleset_runtime.state(instance)['version']}",
        "type": "end_turn",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": actor_id,
    }, [])


def _conditions(instance) -> dict:
    sheet = instance.players["gm"]["character_sheet"]["ruleset_character"]
    return sheet.get("conditions") or {}


def _escape_intent(check: str = "str", **extra) -> dict:
    return {
        "intent_id": f"escape-{check}", "type": "escape",
        "submitted_by": "gm", "actor_id": "player:gm",
        "condition": "grappled", "check": check, **extra,
    }


# 状态本身就声明了怎么解除 ---------------------------------------------------


def test_the_applied_instance_carries_the_dc_and_the_registry_the_shape() -> None:
    engine, instance = _instance()
    _grappled(engine, instance)

    assert _conditions(instance)["grappled"]["escape_dc"] == 12
    registry = engine.catalog.conditions["grappled"]
    assert registry["escape"] == {
        "cost": "action",
        "checks": [
            {"ability": "str", "skill": "athletics"},
            {"ability": "dex", "skill": "acrobatics"},
        ],
    }


def test_escape_is_listed_with_its_dc_and_both_abilities() -> None:
    engine, instance = _instance()
    _grappled(engine, instance)

    entry = next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "escape"
    )

    assert entry["condition"] == "grappled"
    assert entry["escape_dc"] == 12
    assert [row["ability"] for row in entry["checks"]] == ["str", "dex"]
    assert entry["cost"] == "action"
    assert entry["available"] is True


# 结算 ----------------------------------------------------------------------


def test_a_successful_escape_removes_the_condition_and_spends_the_action() -> None:
    engine, instance = _instance()
    _grappled(engine, instance)
    intent = {
        **_escape_intent("str"),
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
    }

    events = _events(engine, instance, intent, [18])

    check = next(row for row in events if row.get("kind") == "escape")
    # 力量挣脱走 Athletics 的技能值（含熟练），不是裸力量调整值。
    assert check["skill"] == "athletics"
    assert (check["total"], check["target"], check["success"]) == (
        18 + check["modifier"], 12, True,
    )
    assert [row["condition"] for row in events if row["type"] == "condition.removed"] == [
        "grappled",
    ]
    assert "grappled" not in _conditions(instance)
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 0


def test_a_failed_escape_still_spends_the_action() -> None:
    engine, instance = _instance()
    _grappled(engine, instance)
    intent = {
        **_escape_intent("str"),
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
    }

    events = _events(engine, instance, intent, [2])

    check = next(row for row in events if row.get("kind") == "escape")
    assert check["success"] is False
    assert not [row for row in events if row["type"] == "condition.removed"]
    assert "grappled" in _conditions(instance)
    # 失败也花动作：否则挣脱就是免费重试。
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 0


def test_escape_uses_the_skill_bonus_of_the_chosen_check() -> None:
    engine, instance = _instance()
    _grappled(engine, instance)
    actor = engine._actor_view(instance, ruleset_runtime.state(instance)["combat"], "player:gm")

    intent = {
        **_escape_intent("dex"),
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
    }
    events = _events(engine, instance, intent, [10])

    check = next(row for row in events if row.get("kind") == "escape")
    # 敏捷挣脱走 Acrobatics 的技能值（含熟练），不是裸敏捷调整值。
    assert check["skill"] == "acrobatics"
    assert check["modifier"] == actor["skill_values"]["acrobatics"]


# 契约拒绝 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "reason"),
    [
        ({"condition": "grappled", "check": "con"}, "declared abilities"),
        ({"condition": "grappled", "check": "str", "dc": 5}, "unknown field"),
        ({"condition": "prone", "check": "str"}, "cannot be escaped"),
        ({"condition": "restrained", "check": "str"}, "does not have that condition"),
    ],
)
def test_a_malformed_escape_is_refused(intent: dict, reason: str) -> None:
    engine, instance = _instance()
    _grappled(engine, instance)
    request = {
        "intent_id": "escape-bad", "type": "escape", "submitted_by": "gm",
        "actor_id": "player:gm",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        **intent,
    }

    resolved = engine.resolve_intent(instance, request, SequenceRng([18]))

    assert resolved["ok"] is False
    assert reason in str(resolved["error"])


def test_an_application_without_a_dc_cannot_be_escaped() -> None:
    """rider 没写 DC 就不允许"随便挑一个数"挣脱。"""

    engine, instance = _instance()
    _grappled(engine, instance)
    ruleset_runtime.state(instance)["combat"]
    sheet = instance.players["gm"]["character_sheet"]["ruleset_character"]
    sheet["conditions"]["grappled"].pop("escape_dc")
    request = {
        **_escape_intent("str"),
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
    }

    resolved = engine.resolve_intent(instance, request, SequenceRng([18]))

    assert resolved["ok"] is False
    assert "no escape DC" in str(resolved["error"])


def test_escaping_needs_the_action() -> None:
    engine, instance = _instance()
    _grappled(engine, instance)
    ruleset_runtime.state(instance)["combat"]["economy"]["action"] = 0
    request = {
        **_escape_intent("str"),
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
    }

    resolved = engine.resolve_intent(instance, request, SequenceRng([18]))

    assert resolved["ok"] is False
    assert "costs a action" in str(resolved["error"])


# 起身 ----------------------------------------------------------------------


def _prone(engine, instance) -> None:
    """Player's turn, standing (not grappled), then knocked prone.

    Applying it through a batch (not by poking the sheet) keeps the movement
    budget honest: a creature that was grappled at turn start has 0 movement,
    which would make every stand-up look unaffordable.
    """

    _events(engine, instance, {
        "intent_id": "start-pr", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm", "enemies": [_wolf()],
    }, [1, 20])
    _pass_turn(engine, instance, "enemy:wolf-1")
    version = int(ruleset_runtime.state(instance)["version"])
    batch = {
        "batch_id": f"batch_{version}", "intent_id": f"prone:{version}",
        "intent_type": "combat.message", "expected_version": version,
        "result_version": version + 1, "source_ref": "test",
        "events": [
            {"type": "intent.submitted", "intent_type": "combat.message",
             "actor_id": "player:gm", "submitted_by": "gm"},
            {"type": "condition.applied", "target_id": "player:gm",
             "condition": "prone", "duration": "combat"},
        ],
    }
    assert engine.apply_batch(instance, batch)["applied"] is True


def test_standing_up_costs_half_the_speed_and_no_action() -> None:
    engine, instance = _instance()
    _prone(engine, instance)
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    before_movement, before_action = economy["movement"], economy["action"]

    entry = next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "stand_up"
    )
    events = _events(engine, instance, {
        "intent_id": "stand-up", "type": "stand_up",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
        "submitted_by": "gm", "actor_id": "player:gm",
    }, [])

    assert entry["movement_cost"] == economy["speed"] // 2
    assert "prone" not in _conditions(instance)
    after = ruleset_runtime.state(instance)["combat"]["economy"]
    assert after["movement"] == before_movement - entry["movement_cost"]
    assert after["action"] == before_action
    assert events[-1]["condition"] == "prone"


def test_standing_up_is_refused_without_enough_movement() -> None:
    engine, instance = _instance()
    _prone(engine, instance)
    ruleset_runtime.state(instance)["combat"]["economy"]["movement"] = 5

    resolved = engine.resolve_intent(instance, {
        "intent_id": "stand-up", "type": "stand_up", "submitted_by": "gm",
        "actor_id": "player:gm",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
    }, SequenceRng([]))

    assert resolved["ok"] is False
    assert "not enough movement" in str(resolved["error"])
    assert "prone" in _conditions(instance)


def test_standing_up_needs_a_condition_that_declares_it() -> None:
    engine, instance = _instance()
    _grappled(engine, instance)

    resolved = engine.resolve_intent(instance, {
        "intent_id": "stand-up", "type": "stand_up", "submitted_by": "gm",
        "actor_id": "player:gm",
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
    }, SequenceRng([]))

    assert resolved["ok"] is False
    assert "not in a condition" in str(resolved["error"])


# prone 的距离门槛 -----------------------------------------------------------


def test_prone_grants_advantage_only_within_five_feet() -> None:
    engine, instance = _instance()
    _prone(engine, instance)
    combat = ruleset_runtime.state(instance)["combat"]
    attacker = engine._actor_view(instance, combat, "enemy:wolf-1")
    target = engine._actor_view(instance, combat, "player:gm")

    close = attack_sources(
        engine.catalog.conditions, attacker, target, False, distance=5,
    )
    far = attack_sources(
        engine.catalog.conditions, attacker, target, False, distance=30,
    )

    assert {row["code"]: row["mode"] for row in close}["prone"] == "advantage"
    assert {row["code"]: row["mode"] for row in far}["prone"] == "disadvantage"
