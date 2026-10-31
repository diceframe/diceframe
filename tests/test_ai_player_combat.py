"""AI-hosted player seats in authoritative D&D 2024 combat (AI teammate plan, PR4).

PR1 made *who plays a seat* an explicit persisted record, PR2 made it
authoritative, and PR3 let an ``ai`` seat act in ordinary exploration rounds.
This file locks down the combat counterpart: when an AI-hosted PC
(``player:<uid>`` whose control mode is ``ai``) has the turn in authoritative
combat, the **server** submits a legal structured combat intent for it.

Behaviour under test:

* the automatic intent comes from the existing deterministic ladder
  (heal a downed/wounded ally -> attack the nearest hostile -> move into range ->
  Dodge -> End Turn), the same one companions already use -- no new engine, no
  LLM, and ``submitted_by`` stays the server/GM authority;
* only an ``ai`` seat is served: a ``human`` or ``unclaimed`` seat still yields
  ``None``, and the check happens at decision time, never cached;
* validation stays fail-closed: only the GM authority may submit for an
  AI-hosted PC, a human still cannot submit for another player's character;
* a PC at 0 HP makes a real ``death_save`` (companions do not);
* the turn always terminates (legal ``end_turn`` fallback) and the action
  economy is never exceeded;
* the seat decides from its own character sheet -- own weapons, own spell
  options, own HP -- not the GM's or another party member's;
* enemy and companion automatic behaviour is unchanged.

Every test drives the real ``Dnd2024CombatEngine`` (and, where noted, the real
``Dnd2024Runtime`` composition root) with real intent validation, resolution and
event-batch application.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from src.engine.game_instance import GameInstance
from src.engine.player_control import set_control
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.play import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.engine.modules import ruleset_runtime


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


def _goblin(*, position: int = 5, hp: int = 18) -> dict:
    return {
        "id": "goblin-1", "name": "Goblin", "hp": hp, "max_hp": hp, "armor_class": 12,
        "speed": 30, "position": position, "initiative_modifier": 2,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "saving_throws": {"dex": 2, "wis": -1, "con": 0},
        "attacks": [{
            "id": "scimitar", "name": "Scimitar", "attack_bonus": 4,
            "damage": "1d6+2", "damage_type": "slashing", "range": 5,
        }],
    }


def _companion(
    companion_id: str, name: str, *, hp: int = 20, max_hp: int | None = None,
) -> dict:
    return {
        "id": companion_id, "name": name, "controller": "ai", "active": True,
        "ruleset_character": {
            "resources": {"hp": hp, "max_hp": max_hp if max_hp is not None else hp},
            "conditions": {},
            "abilities": {
                "str": 18, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 10,
            },
            "derived": {
                "armor_class": 16, "speed": 30, "initiative": 1,
                "proficiency_bonus": 2, "saving_throws": {"str": 4, "con": 2},
            },
            "proficiencies": {
                "skill_values": {"athletics": 6, "medicine": 4},
                "weapon_category_refs": ["weapon_category:martial"],
            },
            "equipment": {"item_refs": ["item:greatsword"]},
            "spellcasting": {"class": {
                "ability": "wis", "slots_current": {}, "concentration": None,
                "prepared_spell_refs": [], "cantrip_refs": [],
            }},
            "build": {"class_levels": [{"class_ref": "class:fighter", "level": 5}]},
        },
    }


def _seed_companion(instance: GameInstance, companion: dict) -> None:
    party = ruleset_runtime.state(instance).setdefault("party", {"companions": {}})
    party.setdefault("companions", {})[companion["id"]] = companion


def _setup(
    *,
    gm_preset: str = "lucky_scout",
    ai_preset: str = "stalwart_guardian",
    with_unclaimed: bool = False,
) -> tuple[Dnd2024Runtime, Dnd2024CombatEngine, GameInstance]:
    """Three-seat table: GM (human), ai1 (AI-hosted) and optionally u1 (unclaimed)."""

    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "dnd2024-ai-player-combat", "bot"),
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    gm = _character(runtime, gm_preset, "GM")
    instance.players["gm"] = {"character_name": "GM", "character_sheet": gm}
    ai = _character(runtime, ai_preset, "Aria")
    # 只留一把巨剑：武器选择因此可读且确定（阶梯按武器引用排序）。
    ai["ruleset_character"]["equipment"]["item_refs"] = ["item:greatsword"]
    instance.players["ai1"] = {"character_name": "Aria", "character_sheet": ai}
    assert instance.bind_ruleset_runtime(gm["rule_binding"])
    set_control(instance, "ai1", "ai")
    if with_unclaimed:
        extra = _character(runtime, "stalwart_guardian", "Nobody")
        instance.players["u1"] = {"character_name": "Nobody", "character_sheet": extra}
        set_control(instance, "u1", "unclaimed")
    engine = Dnd2024CombatEngine(runtime.load_bundle("en"), EncounterAccess.sandbox())
    return runtime, engine, instance


def _start(
    engine: Dnd2024CombatEngine,
    instance: GameInstance,
    *,
    rolls: list[int],
    enemy_position: int = 5,
    enemy_hp: int = 18,
) -> dict:
    resolved = engine.resolve_intent(instance, {
        "intent_id": "start-1", "type": "combat.start", "expected_version": 0,
        "submitted_by": "gm",
        "enemies": [_goblin(position=enemy_position, hp=enemy_hp)],
    }, SequenceRng(list(rolls)))
    assert resolved["ok"] is True
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    return resolved["event_batch"]


def _set_turn(engine: Dnd2024CombatEngine, instance: GameInstance, actor_id: str) -> None:
    combat = ruleset_runtime.state(instance)["combat"]
    combat["turn_index"] = combat["initiative"].index(actor_id)
    combat["economy"] = engine._fresh_economy(engine._actor_view(instance, combat, actor_id))


def _apply(
    engine: Dnd2024CombatEngine, instance: GameInstance, intent: dict, rolls: list[int],
) -> dict:
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved.get("error")
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    return applied


def _drain(
    engine: Dnd2024CombatEngine, instance: GameInstance, *, limit: int = 32,
) -> list[dict]:
    """Apply automatic intents exactly like the server loop, and require it to stop."""

    intents: list[dict] = []
    for _ in range(limit):
        intent = engine.next_automatic_intent(instance)
        if intent is None:
            return intents
        _apply(engine, instance, intent, [])
        intents.append(intent)
    raise AssertionError("automatic intents did not terminate")


def _conditions(instance: GameInstance, uid: str) -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]["conditions"]


def _sheet(instance: GameInstance, uid: str) -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


# ---- 1. AI 托管席位真的走同一条权威链出招 ------------------------------------


def test_ai_hosted_pc_takes_its_turn_through_the_authoritative_chain() -> None:
    runtime, engine, instance = _setup()
    _start(engine, instance, rolls=[20, 1, 3])
    _set_turn(engine, instance, "player:ai1")
    version = ruleset_runtime.state(instance)["version"]

    intent = engine.next_automatic_intent(instance)

    assert intent is not None
    assert intent["actor_id"] == "player:ai1"
    assert intent["type"] == "attack"
    assert intent["target_id"] == "enemy:goblin-1"
    assert intent["weapon_ref"] == "item:greatsword"
    assert intent["submitted_by"] == "gm"
    assert intent["expected_version"] == version
    assert engine.validate_intent(instance, intent) == {"ok": True}
    # runtime 组合根走的是同一个实现（不是第二条路径）。
    assert runtime.next_automatic_intent(instance) == intent

    before = ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"]
    applied = _apply(engine, instance, intent, [15, 4, 5])

    assert applied["state_version"] == version + 1
    assert ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] < before
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 0


# ---- 2. 真人 / 未认领席位没有自动意图 ----------------------------------------


def test_human_and_unclaimed_seats_never_get_an_automatic_intent() -> None:
    _runtime, engine, instance = _setup(with_unclaimed=True)
    _start(engine, instance, rolls=[20, 1, 2, 3])
    assert ruleset_runtime.state(instance)["combat"]["initiative"] == [
        "player:ai1", "player:gm", "player:u1", "enemy:goblin-1",
    ]

    for actor_id in ("player:gm", "player:u1"):
        _set_turn(engine, instance, actor_id)
        assert engine.next_automatic_intent(instance) is None

    _set_turn(engine, instance, "player:ai1")
    assert engine.next_automatic_intent(instance) is not None


# ---- 3. 真人不能替 AI 托管席位出招 ------------------------------------------


def test_a_human_cannot_submit_for_an_ai_hosted_seat() -> None:
    _runtime, engine, instance = _setup(with_unclaimed=True)
    _start(engine, instance, rolls=[20, 1, 2, 3])
    _set_turn(engine, instance, "player:ai1")
    version = ruleset_runtime.state(instance)["version"]

    for submitter in ("u1", "gm_other", "ai1"):
        rejected = engine.validate_intent(instance, {
            "intent_id": f"forged-{submitter}", "type": "attack",
            "expected_version": version, "submitted_by": submitter,
            "actor_id": "player:ai1", "target_id": "enemy:goblin-1",
            "weapon_ref": "item:greatsword",
        })
        assert rejected["ok"] is False
        assert rejected["error"] == "only the GM can submit intents for an AI-hosted character"

    # 真实解析链同样拒绝，而不是只在 validate 上把关。
    forged = engine.resolve_intent(instance, {
        "intent_id": "forged-resolve", "type": "attack", "expected_version": version,
        "submitted_by": "u1", "actor_id": "player:ai1",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:greatsword",
    }, SequenceRng([20]))
    assert forged["ok"] is False
    assert forged["code"] == "INVALID_INTENT"

    # 服务器 / GM automation authority 仍然可以。
    allowed = engine.validate_intent(instance, {
        "intent_id": "server-submitted", "type": "attack", "expected_version": version,
        "submitted_by": "gm", "actor_id": "player:ai1",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:greatsword",
    })
    assert allowed == {"ok": True}


# ---- 4. 真人依旧不能替真人出招（错误信息不变）--------------------------------


def test_a_human_still_cannot_submit_for_another_humans_seat() -> None:
    _runtime, engine, instance = _setup(with_unclaimed=True)
    _start(engine, instance, rolls=[20, 1, 2, 3])
    set_control(instance, "u1", "human")  # 第二个真人席位
    _set_turn(engine, instance, "player:gm")

    rejected = engine.validate_intent(instance, {
        "intent_id": "cross-submit", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "u1", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:dagger",
    })
    assert rejected["ok"] is False
    assert rejected["error"] == "a player can submit intents only for their own character"

    # 连 GM 也不能替真人玩家的角色行动：席位不是 AI 托管。
    _set_turn(engine, instance, "player:u1")
    gm_for_human = engine.validate_intent(instance, {
        "intent_id": "gm-for-human", "type": "end_turn",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:u1",
    })
    assert gm_for_human["ok"] is False
    assert gm_for_human["error"] == "a player can submit intents only for their own character"


# ---- 5. 动作经济不可超越 -----------------------------------------------------


def test_ai_hosted_pc_cannot_exceed_the_action_economy() -> None:
    _runtime, engine, instance = _setup()
    canonical = _sheet(instance, "ai1")
    canonical["build"]["level"] = 5
    canonical["build"]["class_levels"][0]["level"] = 5  # Extra Attack: 2 attacks
    _start(engine, instance, rolls=[20, 1, 3], enemy_hp=200)
    _set_turn(engine, instance, "player:ai1")

    first = engine.next_automatic_intent(instance)
    assert first is not None and first["type"] == "attack"
    _apply(engine, instance, first, [15, 4, 5])
    assert ruleset_runtime.state(instance)["combat"]["economy"]["attacks_remaining"] == 1

    second = engine.next_automatic_intent(instance)
    assert second is not None and second["type"] == "attack"
    _apply(engine, instance, second, [15, 4, 5])
    economy = ruleset_runtime.state(instance)["combat"]["economy"]
    assert economy["action"] == 0 and economy["attacks_remaining"] == 0

    # 两次攻击用尽后自动阶梯不再攻击，而是收尾。
    third = engine.next_automatic_intent(instance)
    assert third is not None and third["type"] == "end_turn"

    overreach = engine.validate_intent(instance, {
        "intent_id": "third-attack", "type": "attack",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:ai1",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:greatsword",
    })
    assert overreach["ok"] is False
    assert overreach["error"] == "the Attack action is no longer available"


def test_ai_hosted_pc_movement_is_capped_by_remaining_speed() -> None:
    _runtime, engine, instance = _setup()
    _start(engine, instance, rolls=[20, 1, 3], enemy_position=400, enemy_hp=200)
    _set_turn(engine, instance, "player:ai1")

    intent = engine.next_automatic_intent(instance)

    assert intent is not None and intent["type"] == "move"
    # 需要 395 尺才能进入巨剑射程，但只允许走完剩下的移动力。
    assert intent["distance"] == 30
    assert engine.validate_intent(instance, intent) == {"ok": True}
    _apply(engine, instance, intent, [])
    assert ruleset_runtime.state(instance)["combat"]["positions"]["player:ai1"] == 30
    assert ruleset_runtime.state(instance)["combat"]["economy"]["movement"] == 0

    overreach = engine.validate_intent(instance, {
        "intent_id": "extra-move", "type": "move",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm", "actor_id": "player:ai1", "distance": 30,
    })
    assert overreach["ok"] is False
    assert overreach["error"] == "movement exceeds the remaining speed"


# ---- 6. 0 HP：PC 做死亡豁免，companion 依旧不做 ------------------------------


def test_ai_hosted_pc_at_zero_hp_makes_a_death_save() -> None:
    _runtime, engine, instance = _setup()
    _start(engine, instance, rolls=[20, 1, 3])
    canonical = _sheet(instance, "ai1")
    canonical["resources"]["hp"] = 0
    canonical.setdefault("conditions", {})["unconscious"] = {"source": "zero_hp"}
    _set_turn(engine, instance, "player:ai1")
    version = ruleset_runtime.state(instance)["version"]

    intent = engine.next_automatic_intent(instance)

    assert intent is not None
    assert intent["type"] == "death_save"
    assert intent["actor_id"] == "player:ai1"
    assert intent["submitted_by"] == "gm"
    assert engine.validate_intent(instance, intent) == {"ok": True}

    # 它不会尝试攻击：0 HP 的角色只能做死亡豁免。
    attack = engine.validate_intent(instance, {
        "intent_id": "downed-attack", "type": "attack", "expected_version": version,
        "submitted_by": "gm", "actor_id": "player:ai1",
        "target_id": "enemy:goblin-1", "weapon_ref": "item:greatsword",
    })
    assert attack["ok"] is False
    assert attack["error"] == "an unconscious actor can only make a death save"

    _apply(engine, instance, intent, [10])

    # 真实的 apply：豁免结果写进了这个席位自己的角色卡，HP 仍是 0。
    assert _conditions(instance, "ai1")["death_saves"] == {"successes": 1, "failures": 0}
    assert _sheet(instance, "ai1")["resources"]["hp"] == 0


def test_a_companion_at_zero_hp_still_never_makes_a_death_save() -> None:
    _runtime, engine, instance = _setup()
    _seed_companion(instance, _companion("mira", "Mira", hp=0))
    _start(engine, instance, rolls=[20, 1, 10, 3])
    _set_turn(engine, instance, "companion:mira")
    version = ruleset_runtime.state(instance)["version"]

    intent = engine.next_automatic_intent(instance)

    # Companion 行为未变：0 HP 的它仍然走原有阶梯，不会生成 death_save。
    assert intent == {
        "intent_id": "auto:companion:1:companion:mira:attack",
        "expected_version": 1,
        "submitted_by": "gm",
        "actor_id": "companion:mira",
        "type": "attack",
        "target_id": "enemy:goblin-1",
        "weapon_ref": "item:greatsword",
    }
    rejected = engine.validate_intent(instance, {
        "intent_id": "companion-death-save", "type": "death_save",
        "expected_version": version, "submitted_by": "gm", "actor_id": "companion:mira",
    })
    assert rejected["ok"] is False
    assert rejected["error"] == "companions do not make death saves"


# ---- 7. 回合一定会结束 -------------------------------------------------------


def test_ai_hosted_pc_turn_always_terminates_when_nothing_useful_is_left() -> None:
    _runtime, engine, instance = _setup()
    _start(engine, instance, rolls=[20, 1, 3], enemy_position=400, enemy_hp=200)
    assert ruleset_runtime.state(instance)["combat"]["initiative"] == [
        "player:ai1", "player:gm", "enemy:goblin-1",
    ]
    _set_turn(engine, instance, "player:ai1")

    intents = _drain(engine, instance)

    # 够不到敌人也没有别的可用招：移动 → Dodge → End Turn，然后交还给真人。
    assert [intent["type"] for intent in intents] == ["move", "dodge", "end_turn"]
    assert all(intent["actor_id"] == "player:ai1" for intent in intents)
    combat = ruleset_runtime.state(instance)["combat"]
    assert combat["initiative"][combat["turn_index"]] == "player:gm"
    assert engine.next_automatic_intent(instance) is None


def test_ai_hosted_pc_with_no_live_hostile_ends_its_turn() -> None:
    _runtime, engine, instance = _setup()
    _start(engine, instance, rolls=[20, 1, 3])
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["hp"] = 0
    _set_turn(engine, instance, "player:ai1")

    # 没有活着的敌对目标：先按既有阶梯 Dodge，再用合法的 End Turn 收尾，
    # 而不是把回合停在原地。
    dodge = engine.next_automatic_intent(instance)
    assert dodge is not None and dodge["type"] == "dodge"
    _apply(engine, instance, dodge, [])

    end = engine.next_automatic_intent(instance)
    assert end is not None and end["type"] == "end_turn"
    _apply(engine, instance, end, [])

    assert ruleset_runtime.state(instance)["combat"]["status"] == "ended"
    assert ruleset_runtime.state(instance)["combat"]["outcome"] == "victory"
    assert engine.next_automatic_intent(instance) is None


# ---- 8. 决策时重读控制权，不缓存 --------------------------------------------


def test_control_handover_to_a_human_stops_the_automatic_intent() -> None:
    _runtime, engine, instance = _setup()
    _start(engine, instance, rolls=[20, 1, 3])
    _set_turn(engine, instance, "player:ai1")

    live = engine.next_automatic_intent(instance)
    assert live is not None and live["type"] == "attack"

    set_control(instance, "ai1", "human")

    # 席位被真人接管：不再产生自动意图，也不能再用服务器代提交的意图。
    assert engine.next_automatic_intent(instance) is None
    assert engine.validate_intent(instance, live)["ok"] is False

    # 再交回 AI：决定不缓存，立刻恢复。
    set_control(instance, "ai1", "ai")
    assert engine.next_automatic_intent(instance) is not None
    assert engine.validate_intent(instance, live) == {"ok": True}


# ---- 9. 敌人 / 队友的自动行为未变 -------------------------------------------


def test_enemy_automatic_intent_is_unchanged() -> None:
    _runtime, engine, instance = _setup()
    _start(engine, instance, rolls=[20, 1, 3])
    _set_turn(engine, instance, "enemy:goblin-1")

    intent = engine.next_automatic_intent(instance)

    assert intent == {
        "intent_id": "auto:enemy:1:enemy:goblin-1:attack",
        "expected_version": 1,
        "submitted_by": "gm",
        "actor_id": "enemy:goblin-1",
        "type": "attack",
        "target_id": "player:gm",
        "attack_id": "scimitar",
    }
    before = _sheet(instance, "gm")["resources"]["hp"]
    _apply(engine, instance, intent, [15, 3])
    assert _sheet(instance, "gm")["resources"]["hp"] < before

    finish = engine.next_automatic_intent(instance)
    assert finish is not None and finish["type"] == "end_turn"
    assert finish["actor_id"] == "enemy:goblin-1"


def test_companion_automatic_intent_is_unchanged() -> None:
    _runtime, engine, instance = _setup()
    _seed_companion(instance, _companion("mira", "Mira"))
    _start(engine, instance, rolls=[20, 1, 10, 3])
    _set_turn(engine, instance, "companion:mira")

    intent = engine.next_automatic_intent(instance)

    assert intent == {
        "intent_id": "auto:companion:1:companion:mira:attack",
        "expected_version": 1,
        "submitted_by": "gm",
        "actor_id": "companion:mira",
        "type": "attack",
        "target_id": "enemy:goblin-1",
        "weapon_ref": "item:greatsword",
    }
    assert engine.validate_intent(instance, intent) == {"ok": True}
    _apply(engine, instance, intent, [15, 4, 5])
    assert ruleset_runtime.state(instance)["combat"]["economy"]["action"] == 0


def test_companion_healing_ladder_is_unchanged() -> None:
    _runtime, engine, instance = _setup()
    healer = _companion("mira", "Mira")
    healer["ruleset_character"]["spellcasting"]["class"].update({
        "prepared_spell_refs": ["spell:cure_wounds"],
        "slots_current": {"1": 1},
    })
    _seed_companion(instance, healer)
    instance.players["gm"]["character_sheet"]["ruleset_character"]["resources"]["hp"] = 0
    _start(engine, instance, rolls=[20, 1, 10, 3])
    _set_turn(engine, instance, "companion:mira")

    intent = engine.next_automatic_intent(instance)

    assert intent is not None and intent["type"] == "cast_spell"
    assert intent["spell_ref"] == "spell:cure_wounds"
    assert intent["slot_level"] == 1
    assert intent["target_ids"] == ["player:gm"]
    assert intent["submitted_by"] == "gm"


# ---- 10. 只读自己的角色卡 ---------------------------------------------------


def test_ai_hosted_pc_attacks_with_its_own_weapon() -> None:
    _runtime, engine, instance = _setup()
    # 差异是可观察的：GM 的角色拿的是匕首，AI 席位拿的是巨剑。
    assert "item:dagger" in _sheet(instance, "gm")["equipment"]["item_refs"]
    assert "item:greatsword" in _sheet(instance, "ai1")["equipment"]["item_refs"]
    _start(engine, instance, rolls=[20, 1, 3])
    _set_turn(engine, instance, "player:ai1")

    intent = engine.next_automatic_intent(instance)
    assert intent is not None and intent["type"] == "attack"
    assert intent["weapon_ref"] == "item:greatsword"
    # 攻击加值同样来自它自己（力量 17 → +3，熟练 +2）。
    resolved = engine.resolve_intent(instance, intent, SequenceRng([15, 4, 5]))
    roll_event = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "check.resolved"
    )
    assert roll_event["actor_id"] == "player:ai1"
    assert roll_event["total"] - roll_event["natural"] == 5
    assert _sheet(instance, "gm")["equipment"]["item_refs"] != _sheet(
        instance, "ai1",
    )["equipment"]["item_refs"]


def test_ai_hosted_pc_heals_a_downed_ally_with_its_own_spell() -> None:
    _runtime, engine, instance = _setup(gm_preset="stalwart_guardian", ai_preset="kindly_bulwark")
    gm_sheet = _sheet(instance, "gm")
    assert "spell:cure_wounds" in _sheet(instance, "ai1")["spellcasting"]["class"][
        "prepared_spell_refs"
    ]
    assert "spell:cure_wounds" not in (
        gm_sheet.get("spellcasting", {}).get("class", {}).get("prepared_spell_refs") or []
    )
    gm_sheet["resources"]["hp"] = 0
    gm_sheet.setdefault("conditions", {})["unconscious"] = {"source": "zero_hp"}
    _start(engine, instance, rolls=[20, 1, 3])
    _set_turn(engine, instance, "player:ai1")

    intent = engine.next_automatic_intent(instance)

    assert intent is not None and intent["type"] == "cast_spell"
    assert intent["spell_ref"] == "spell:cure_wounds"
    assert intent["slot_level"] == 1
    assert intent["target_ids"] == ["player:gm"]
    assert engine.validate_intent(instance, intent) == {"ok": True}
    _apply(engine, instance, intent, [3, 4])
    assert _sheet(instance, "gm")["resources"]["hp"] > 0


# ---- 11. 决定是确定性的（无 LLM、无副作用）----------------------------------


def test_automatic_intent_is_deterministic_and_read_only() -> None:
    _runtime, engine, instance = _setup()
    _start(engine, instance, rolls=[20, 1, 3])
    _set_turn(engine, instance, "player:ai1")
    before = deepcopy(ruleset_runtime.state(instance))

    first = engine.next_automatic_intent(instance)
    second = engine.next_automatic_intent(instance)

    assert first is not None and first == second
    assert ruleset_runtime.state(instance) == before


def test_an_incapacitated_companion_ends_its_turn_instead_of_attacking() -> None:
    _runtime, engine, instance = _setup()
    companion = _companion("mira", "Mira")
    companion["ruleset_character"]["conditions"]["paralyzed"] = {"source": "test"}
    _seed_companion(instance, companion)
    _start(engine, instance, rolls=[20, 1, 10, 3])
    _set_turn(engine, instance, "companion:mira")

    intent = engine.next_automatic_intent(instance)

    assert intent["type"] == "end_turn"
    assert engine.validate_intent(instance, intent)["ok"] is True
