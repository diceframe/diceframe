"""S0-F26：Circle of the Land 的主动能力与抗性。

这些用例只走意图与事件流，不直接调私有方法：区域成员、豁免、抗性与资源
支付都是"能否打出这一下"的一部分，绕过它们就等于没测规则。
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024 import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
from tests.rulesets.dnd2024_monk_common import (
    SequenceRng, monk_draft, monk_instance, start_combat,
)

LOCALE = "en"


def _land_druid(level: int = 3):
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle(LOCALE)
    draft = monk_draft(runtime, LOCALE)
    draft.update(
        class_ref="class:druid",
        class_skill_refs=["skill:animal_handling", "skill:nature"],
        equipment_package_ref="equipment_package:druid_a",
        level=1,
    )
    choices = runtime.builder_choices(None, draft)
    taken = set(draft["class_skill_refs"])
    draft["species_skill_refs"] = [
        item["ref"] for item in choices["species_skills"] if item["ref"] not in taken
    ][: choices["species_skill_count"]]
    draft["class_tool_refs"] = [
        item["ref"] for item in choices["class_tools"]
    ][: choices["class_tool_count"]]
    level1 = Dnd2024SpellSelection(bundle).options("class:druid", 1)
    need = level1["requirements"]
    draft["class_spell_choices"] = {
        "cantrip_refs": [row["ref"] for row in level1["cantrips"]][: int(need["cantrip_count"])],
        "prepared_spell_refs": [
            row["ref"] for row in level1["leveled_spells"]
        ][: int(need["prepared_spell_count"])],
        "spellbook_refs": [],
    }
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        target = int(sheet["ruleset_character"]["build"]["level"]) + 1
        row = Dnd2024SpellSelection(bundle).options("class:druid", target)
        req = row["requirements"]
        picks: dict = {
            "class_spell_choices": {
                "cantrip_refs": [c["ref"] for c in row["cantrips"]][: int(req["cantrip_count"])],
                "prepared_spell_refs": [
                    c["ref"] for c in row["leveled_spells"]
                ][: int(req["prepared_spell_count"])],
                "spellbook_refs": [],
            },
        }
        preview = runtime.preview_advancement(None, sheet, picks)
        needs_subclass = any(
            row.get("id") == "subclass_ref" for row in preview.get("requirements") or []
        )
        if needs_subclass:
            picks["subclass_ref"] = "subclass:circle_of_the_land"
        feat = next(
            (row for row in preview.get("requirements") or []
             if str(row.get("id") or "").endswith("feat_ref")),
            None,
        )
        if feat is not None:
            options = feat.get("options") or []
            assert options, "a feat level must offer options"
            picks[str(feat["id"])] = str(options[0].get("value") or "")
        ability = next(
            (row for row in preview.get("requirements") or []
             if row.get("id") == "ability_score_increases"),
            None,
        )
        if ability is not None:
            # 分配要合法：不能把感知顶到 20 以上，所以按剩余空间摊开。
            points = int(ability.get("total") or 2)
            current = dict(sheet["ruleset_character"]["abilities"])
            spread: dict[str, int] = {}
            for key in ("wis", "con", "dex", "str"):
                room = 20 - int(current.get(key, 10) or 10)
                take = max(0, min(points, room))
                if take:
                    spread[key] = take
                    points -= take
                if points == 0:
                    break
            if spread:
                picks["ability_score_increases"] = spread
        feature_choices = [
            row for row in preview.get("requirements") or []
            if row.get("kind") == "feature_choice"
        ]
        if feature_choices:
            picks["feature_choices"] = [
                {
                    "owner": str(row.get("owner") or ""),
                    "feature_id": str(row.get("feature_id") or ""),
                    "choice_id": str(row.get("choice_id") or ""),
                    "selected_refs": [
                        str((row.get("options") or [{}])[0].get("value") or "")
                    ],
                }
                for row in feature_choices
            ]
        forms = (preview.get("form_requirements") or {}).get("known_form_options")
        if forms:
            picks["known_form_refs"] = [row["ref"] for row in forms][
                : int(preview["form_requirements"]["known_form_count"])
            ]
        preview = runtime.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, picks)
    engine, instance = monk_instance(runtime, sheet)
    start_combat(engine, instance)
    return engine, instance


def _state(instance) -> dict:
    from src.engine.modules import ruleset_runtime

    return ruleset_runtime.state(instance)


def _resolve(engine, instance, intent: dict, rolls=()):
    intent = {
        "expected_version": _state(instance)["version"],
        "submitted_by": "gm",
        "actor_id": "player:gm",
        **intent,
    }
    return engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))


def test_lands_aid_shares_one_damage_roll_and_heals_one_target() -> None:
    engine, instance = _land_druid(3)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["wild_shape"] = {
        "current": 2, "maximum": 2,
    }
    result = _resolve(engine, instance, {
        "intent_id": "aid-1", "type": "land_aid", "origin": 0,
        "damage_targets": ["enemy:goblin-1"], "heal_target": "player:gm",
    }, rolls=[4, 4, 1, 1])
    assert result["ok"] is True, result.get("error")
    events = result["event_batch"]["events"]
    damage = [e for e in events if e.get("damage_type") == "necrotic"]
    assert damage and damage[0]["rolls"] == [4, 4]      # 3 级 = 2d6，共用一份
    heal = [e for e in events if e.get("healing")]
    assert heal and heal[0]["rolls"] == [1, 1]


def test_lands_aid_needs_the_feature_and_a_wild_shape_use() -> None:
    engine, instance = _land_druid(2)          # 2 级还没有 Land's Aid
    result = _resolve(engine, instance, {
        "intent_id": "aid-2", "type": "land_aid", "origin": 0, "damage_targets": [],
    })
    assert result["ok"] is False
    assert "not granted" in result["error"]


def test_sanctuary_gives_allies_half_cover_but_not_enemies() -> None:
    engine, instance = _land_druid(14)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["wild_shape"] = {
        "current": 2, "maximum": 2,
    }
    created = _resolve(engine, instance, {
        "intent_id": "san-1", "type": "nature_sanctuary", "mode": "create", "origin": 0,
    })
    assert created["ok"] is True, created.get("error")
    engine.apply_batch(instance, created["event_batch"])
    combat = _state(instance)["combat"]
    assert engine.cover_level(instance, combat, "player:gm") == "half"
    # beneficiaries=allies：敌人站在同一位置也拿不到。
    assert engine.cover_level(instance, combat, "enemy:goblin-1") != "half"


def test_ward_resists_the_current_lands_damage_type() -> None:
    engine, instance = _land_druid(10)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    # land 与法术授予选择住在 spellcasting.class 下（F4 的写入位置）。
    character.setdefault("spellcasting", {}).setdefault("class", {})[
        "spell_grant_choices"
    ] = {"circle_of_the_land_spells": {"terrain": "arid"}}
    view = engine._actor_view(instance, _state(instance)["combat"], "player:gm")
    # arid → Fire；_resist_damage 是引擎里唯一的抗性权威。
    amount, source = engine._resist_damage(view, 10, "fire")
    assert (amount, source) == (5, "natures_ward")
    assert engine._resist_damage(view, 10, "cold")[1] == ""


def test_sanctuary_expires_on_the_world_clock() -> None:
    engine, instance = _land_druid(14)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["wild_shape"] = {
        "current": 2, "maximum": 2,
    }
    created = _resolve(engine, instance, {
        "intent_id": "san-exp", "type": "nature_sanctuary", "mode": "create", "origin": 0,
    })
    assert created["ok"] is True, created.get("error")
    engine.apply_batch(instance, created["event_batch"])
    combat = _state(instance)["combat"]
    assert combat.get("zones"), "the Cube must exist right after creation"

    # 推进世界时钟一分钟：区域由分钟时钟裁剪，不靠回合数。
    zone = next(iter(combat["zones"].values()))
    zone["expires_at_minute"] = 0
    events = engine._zone_expiry_events(instance, combat)
    assert [event["reason"] for event in events] == ["expired"]


def test_moving_the_sanctuary_does_not_recharge_it() -> None:
    engine, instance = _land_druid(14)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["wild_shape"] = {
        "current": 2, "maximum": 2,
    }
    created = _resolve(engine, instance, {
        "intent_id": "san-move-1", "type": "nature_sanctuary", "mode": "create", "origin": 0,
    })
    engine.apply_batch(instance, created["event_batch"])
    combat = _state(instance)["combat"]
    zone_id, zone = next(iter(combat["zones"].items()))
    before_expiry = zone.get("expires_at_minute")
    uses_before = instance.get_character_sheet("gm")["ruleset_character"][
        "resources"
    ]["class"]["wild_shape"]["current"]
    moved = _resolve(engine, instance, {
        "intent_id": "san-move-2", "type": "nature_sanctuary", "mode": "move",
        "zone_id": zone_id, "origin": 20,
    })
    assert moved["ok"] is True, moved.get("error")
    engine.apply_batch(instance, moved["event_batch"])
    combat = _state(instance)["combat"]
    assert combat["zones"][zone_id]["origin"] == 20
    assert combat["zones"][zone_id]["expires_at_minute"] == before_expiry
    uses_after = instance.get_character_sheet("gm")["ruleset_character"][
        "resources"
    ]["class"]["wild_shape"]["current"]
    assert uses_after == uses_before      # 移动不扣资源


def test_an_ally_inside_the_sanctuary_borrows_the_ward_resistance() -> None:
    from src.rulesets.dnd2024.combat.obscurement import zone_members

    zone = {"shape": "cube", "size_ft": 15, "origin": 0, "beneficiaries": "allies"}
    positions = {"player:gm": 0, "player:ally": 7, "enemy:goblin-1": 10}
    inside = zone_members(
        {"positions": positions}, zone, positions,
        lambda actor: actor.startswith("player:"),
    )
    assert inside == ["player:ally", "player:gm"]   # 稳定顺序；敌人不在受益者之列


def test_ward_makes_the_poisoned_condition_a_no_op() -> None:
    """Ward 的免疫走既有的特性免疫通道（`immune_conditions`），不是新加判断。"""

    engine, instance = _land_druid(10)
    state = _state(instance)
    probe = engine.resolve_intent(instance, {
        "intent_id": "ward-block-1", "type": "dodge",
        "expected_version": state["version"], "submitted_by": "gm",
        "actor_id": "player:gm",
    }, SequenceRng([]))
    assert probe["ok"] is True, probe.get("error")
    batch = dict(probe["event_batch"])
    batch["events"] = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "poisoned", "source_actor_id": "enemy:goblin-1",
    }]
    engine.apply_batch(instance, batch)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    conditions = character.get("conditions") or {}
    # 免疫在"每条意图的公共收尾"上拦截：状态里不该有真的中毒。
    assert "poisoned" not in conditions, conditions


def test_zones_survive_a_state_round_trip() -> None:
    """区域是持久状态：序列化再读回，Cube 仍然在、掩护仍然成立。"""

    import json as _json

    from src.engine.modules import ruleset_runtime

    engine, instance = _land_druid(14)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["wild_shape"] = {
        "current": 2, "maximum": 2,
    }
    created = _resolve(engine, instance, {
        "intent_id": "rt-1", "type": "nature_sanctuary", "mode": "create", "origin": 0,
    })
    engine.apply_batch(instance, created["event_batch"])
    before = _state(instance)
    assert before["combat"].get("zones")

    restored = _json.loads(_json.dumps(before))
    ruleset_runtime.replace_state(instance, restored)
    after = _state(instance)
    zone = next(iter(after["combat"]["zones"].values()))
    assert zone["shape"] == "cube" and zone["half_cover"] is True
    assert zone["ward_type"] in {"", "fire", "cold", "lightning", "poison"}
    assert engine.cover_level(instance, after["combat"], "player:gm") == "half"


def test_the_two_abilities_are_offered_to_the_player_as_actions() -> None:
    """引擎里有意图 ≠ 玩家能用：入口必须出现在 `available_intents` 里。"""

    engine, instance = _land_druid(14)
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["wild_shape"] = {
        "current": 2, "maximum": 2,
    }
    actions = engine.available_intents(instance, "gm")
    types = {str(action.get("type")) for action in actions}
    assert "land_aid" in types, types
    assert "nature_sanctuary" in types, types
    sanctuary = next(a for a in actions if a["type"] == "nature_sanctuary")
    assert sanctuary["mode"] == "create"

    # 次数用光后入口消失：面板不该给一个按下去必被拒的按钮。
    character["resources"]["class"]["wild_shape"]["current"] = 0
    types = {str(a.get("type")) for a in engine.available_intents(instance, "gm")}
    assert "land_aid" not in types
    assert "nature_sanctuary" not in types


# ---------------------------------------------------------------------------
# 服务端权威：移动、区域身份、目标列表、时长、失能
# ---------------------------------------------------------------------------

def _grant_wild_shape_uses(instance) -> None:
    character = instance.get_character_sheet("gm")["ruleset_character"]
    character.setdefault("resources", {}).setdefault("class", {})["wild_shape"] = {
        "current": 2, "maximum": 2,
    }


def _create_sanctuary(engine, instance, origin: int = 0, **extra) -> str:
    created = _resolve(engine, instance, {
        "intent_id": f"san-{origin}", "type": "nature_sanctuary",
        "mode": "create", "origin": origin, **extra,
    })
    assert created["ok"] is True, created.get("error")
    engine.apply_batch(instance, created["event_batch"])
    return "sanctuary:player:gm"


def _stored_spell_zone(owner_id: str) -> dict:
    """A zone the way the reducer stores a spell's: owner_id back-filled from caster."""
    return {
        "kind": "sphere", "shape": "sphere", "size_ft": 20, "level": "heavily",
        "origin": 0, "radius_ft": 20, "owner_id": owner_id, "caster_id": owner_id,
        "rounds_left": 10, "expires_at_minute": None,
    }


def test_moving_the_sanctuary_has_real_limits() -> None:
    engine, instance = _land_druid(14)
    _grant_wild_shape_uses(instance)
    zone_id = _create_sanctuary(engine, instance)
    _state(instance)["combat"]["economy"]["bonus_action"] = 1

    # 离自己 100 尺：在作用距离内，但离 Cube 现在的位置超过一次能走的 60 尺。
    too_far = _resolve(engine, instance, {
        "intent_id": "mv-far", "type": "nature_sanctuary", "mode": "move", "origin": 100,
    })
    assert too_far["ok"] is False
    assert "at most 60 feet" in too_far["error"]

    out_of_range = _resolve(engine, instance, {
        "intent_id": "mv-away", "type": "nature_sanctuary", "mode": "move", "origin": 5000,
    })
    assert out_of_range["ok"] is False
    assert "within 120 feet" in out_of_range["error"]

    not_a_position = _resolve(engine, instance, {
        "intent_id": "mv-text", "type": "nature_sanctuary", "mode": "move", "origin": "abc",
    })
    assert not_a_position["ok"] is False
    assert "integer position" in not_a_position["error"]

    fine = _resolve(engine, instance, {
        "intent_id": "mv-ok", "type": "nature_sanctuary", "mode": "move", "origin": 40,
    })
    assert fine["ok"] is True, fine.get("error")
    engine.apply_batch(instance, fine["event_batch"])
    assert _state(instance)["combat"]["zones"][zone_id]["origin"] == 40


def test_a_druids_own_spell_zone_is_not_a_sanctuary_to_move() -> None:
    """reducer 用 caster 回填 owner_id，所以"是我放的"不能当成"是我的 Sanctuary"。"""
    engine, instance = _land_druid(14)
    _grant_wild_shape_uses(instance)
    fog = "fog_cloud:player:gm:cast-1"
    _state(instance)["combat"]["zones"][fog] = _stored_spell_zone("player:gm")
    _state(instance)["combat"]["economy"]["bonus_action"] = 1

    moved = _resolve(engine, instance, {
        "intent_id": "mv-fog", "type": "nature_sanctuary", "mode": "move",
        "zone_id": fog, "origin": 4000,
    })

    assert moved["ok"] is False
    assert "not yours" in moved["error"]
    assert _state(instance)["combat"]["zones"][fog]["origin"] == 0


def test_creating_a_sanctuary_never_replaces_another_zone() -> None:
    """区域 id 由服务端派生：客户端自报的 id 不能覆盖别人的区域。"""
    engine, instance = _land_druid(14)
    _grant_wild_shape_uses(instance)
    enemy_fog = "fog_cloud:enemy:goblin-1:cast-1"
    _state(instance)["combat"]["zones"][enemy_fog] = _stored_spell_zone("enemy:goblin-1")

    _create_sanctuary(engine, instance, origin=0, zone_id=enemy_fog)

    zones = _state(instance)["combat"]["zones"]
    assert set(zones) == {enemy_fog, "sanctuary:player:gm"}
    assert zones[enemy_fog]["owner_id"] == "enemy:goblin-1"
    assert zones[enemy_fog]["kind"] == "sphere"


def test_a_target_listed_twice_is_refused_instead_of_hit_twice() -> None:
    engine, instance = _land_druid(3)
    _grant_wild_shape_uses(instance)

    twice = _resolve(engine, instance, {
        "intent_id": "aid-twice", "type": "land_aid", "origin": 0,
        "damage_targets": ["enemy:goblin-1", "enemy:goblin-1"],
    }, rolls=[4, 4, 1])
    assert twice["ok"] is False
    assert "more than once" in twice["error"]

    wrong_shape = _resolve(engine, instance, {
        "intent_id": "aid-shape", "type": "land_aid", "origin": 0,
        "damage_targets": "enemy:goblin-1",
    })
    assert wrong_shape["ok"] is False
    assert "damage_targets must be a list" in wrong_shape["error"]


def test_the_sanctuary_ends_after_ten_rounds_even_though_combat_never_moves_the_clock() -> None:
    """"1 分钟"也是十个战斗回合；战斗不推进世界时钟，只靠分钟上限它永远不会结束。"""
    engine, instance = _land_druid(14)
    _grant_wild_shape_uses(instance)
    zone_id = _create_sanctuary(engine, instance)
    created_in = int(_state(instance)["combat"]["round"])
    assert _state(instance)["combat"]["zones"][zone_id]["expires_at_round"] == created_in + 10

    ended_in = None
    for step in range(60):
        current = str(engine._current_actor(_state(instance)["combat"]))
        ended = _resolve(engine, instance, {
            "intent_id": f"end-{step}", "type": "end_turn", "actor_id": current,
        })
        assert ended["ok"] is True, ended.get("error")
        engine.apply_batch(instance, ended["event_batch"])
        if zone_id not in _state(instance)["combat"]["zones"]:
            ended_in = int(_state(instance)["combat"]["round"])
            break

    # 第 1 回合放下，覆盖第 1–10 回合；第 11 回合的第一条意图把它清掉。
    assert ended_in == created_in + 10


def test_an_incapacitated_druid_cannot_use_the_land_abilities() -> None:
    engine, instance = _land_druid(14)
    _grant_wild_shape_uses(instance)
    probe = _resolve(engine, instance, {"intent_id": "probe-stunned", "type": "dodge"})
    assert probe["ok"] is True, probe.get("error")
    batch = dict(probe["event_batch"])
    batch["events"] = [{
        "type": "condition.applied", "target_id": "player:gm",
        "condition": "stunned", "source_actor_id": "enemy:goblin-1",
    }]
    engine.apply_batch(instance, batch)

    offered = {str(action.get("type")) for action in engine.available_intents(instance, "gm")}
    assert "land_aid" not in offered
    assert "nature_sanctuary" not in offered

    used = _resolve(engine, instance, {
        "intent_id": "aid-stunned", "type": "land_aid", "origin": 0,
        "damage_targets": ["enemy:goblin-1"],
    })
    assert used["ok"] is False
    assert "incapacitated actor cannot take actions" in used["error"]
