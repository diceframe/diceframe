"""C2B: obscurement is a level the table declares, and the rules only have two.

The SRD defines lightly and heavily -- nothing else.  Two neighbouring ideas are
deliberately kept out of this ladder: something solid blocking sight is ``total``
cover (it cannot be targeted), and bright/dim/darkness is a property of a light
source that *resolves to* lightly or heavily rather than adding a rung.

This slice builds the declaration path and the single query every future
consumer will read.  It does not touch a die roll: attack Disadvantage,
Perception Disadvantage and the Hide prerequisite all land in later slices, and
having them here would mean inventing the pair rule ("who is obscured from whom")
before there is a consumer to shape it.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.obscurement import (
    OBSCUREMENT_LEVELS,
    best_obscurement,
    obscurement_effects,
    obscurement_rank,
    obscurement_row,
    sense_map,
)

from dnd2024_monk_common import SequenceRng
from test_dnd2024_channel_control import _inject
from test_dnd2024_combat import _instance, _start


def _state(instance):
    return ruleset_runtime.state(instance)


def _combat():
    engine, instance = _instance()
    _start(engine, instance)
    return engine, instance


def _apply(engine, instance, resolved: dict) -> list[dict]:
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _declare(engine, instance, level: str, target_id: str = "enemy:goblin-1",
             submitted_by: str = "gm") -> dict:
    state = _state(instance)
    return engine.resolve_intent(instance, {
        "intent_id": f"obs-{level}-{state['version']}-{submitted_by}",
        "type": "combat.set_obscurement",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": submitted_by,
        "target_id": target_id,
        "level": level,
    }, SequenceRng([]))


def _declared(instance) -> dict:
    return _state(instance)["combat"].get("obscurement") or {}


# ---------------------------------------------------------------------------
# 1–2: the ladder itself
# ---------------------------------------------------------------------------

def test_the_ladder_has_exactly_the_two_official_levels() -> None:
    assert OBSCUREMENT_LEVELS == ("none", "lightly", "heavily")


@pytest.mark.parametrize(
    ("level", "disadvantage", "blocks"),
    [("none", False, False), ("lightly", True, False), ("heavily", True, True)],
)
def test_each_level_implies_its_two_consequences(
    level: str, disadvantage: bool, blocks: bool,
) -> None:
    row = obscurement_row(level)
    assert row["level"] == level
    assert row["sight_disadvantage"] is disadvantage
    assert row["blocks_sight"] is blocks


def test_an_unknown_level_degrades_to_none_in_the_derivation() -> None:
    """派生层容错，是为了读历史状态；声明层仍然严格拒绝（见下）。"""

    assert obscurement_row("opaque")["level"] == "none"
    assert obscurement_rank("opaque") == 0
    assert obscurement_effects("dark") == {"sight_disadvantage": False, "blocks_sight": False}


def test_the_row_shape_is_stable() -> None:
    row = obscurement_row("heavily", ["gm", "spell:darkness"])
    assert set(row) == {"level", "sight_disadvantage", "blocks_sight", "sources"}
    assert row["sources"] == ["gm", "spell:darkness"]


def test_several_sources_give_the_worse_one_not_a_sum() -> None:
    assert best_obscurement(["lightly", "heavily"]) == "heavily"
    assert best_obscurement(["none", "lightly"]) == "lightly"
    assert best_obscurement([]) == "none"


# ---------------------------------------------------------------------------
# 3–5: declaration round-trip
# ---------------------------------------------------------------------------

def test_a_gm_declaration_reaches_state_and_view() -> None:
    engine, instance = _combat()
    events = _apply(engine, instance, _declare(engine, instance, "heavily"))
    assert [event["type"] for event in events][-1] == "dnd2024.obscurement.declared"
    assert _declared(instance) == {
        "enemy:goblin-1": {"level": "heavily", "source": "gm"},
    }
    view = engine._actor_view(instance, _state(instance)["combat"], "enemy:goblin-1")
    assert view["obscurement"]["level"] == "heavily"
    assert view["obscurement"]["blocks_sight"] is True


def test_declaring_none_revokes_and_leaves_no_tombstone() -> None:
    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, "lightly"))
    _apply(engine, instance, _declare(engine, instance, "none"))
    assert _declared(instance) == {}
    view = engine._actor_view(instance, _state(instance)["combat"], "enemy:goblin-1")
    assert view["obscurement"]["level"] == "none"


def test_a_second_declaration_replaces_the_first() -> None:
    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, "lightly"))
    _apply(engine, instance, _declare(engine, instance, "heavily"))
    assert _declared(instance)["enemy:goblin-1"] == {"level": "heavily", "source": "gm"}


# ---------------------------------------------------------------------------
# 6–8: rejections
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", ["opaque", "dark", "", "Heavily", "heavy"])
def test_a_level_outside_the_ladder_is_rejected(level: str) -> None:
    engine, instance = _combat()
    resolved = _declare(engine, instance, level)
    assert resolved["ok"] is False
    assert "obscurement level must be one of" in str(resolved.get("error"))
    assert _declared(instance) == {}


def test_only_the_gm_may_declare() -> None:
    engine, instance = _combat()
    resolved = _declare(engine, instance, "lightly", submitted_by="gm2")
    assert resolved["ok"] is False
    assert "only the GM can declare obscurement" in str(resolved.get("error"))


def test_an_unknown_target_is_rejected() -> None:
    engine, instance = _combat()
    resolved = _declare(engine, instance, "lightly", target_id="enemy:nobody")
    assert resolved["ok"] is False
    assert "obscurement target actor does not exist" in str(resolved.get("error"))


def test_a_target_outside_the_combat_is_rejected() -> None:
    engine, instance = _combat()
    resolved = _declare(engine, instance, "lightly", target_id="object:crate")
    assert resolved["ok"] is False
    assert "obscurement target must be a combat actor" in str(resolved.get("error"))


# ---------------------------------------------------------------------------
# 9–11: precedence, query defaults, lifecycle
# ---------------------------------------------------------------------------

def test_a_declaration_wins_over_any_derived_source() -> None:
    """声明优先与掩护同口径：派生来源只进 ``sources``，不参与取最高。"""

    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, "lightly"))
    combat = _state(instance)["combat"]
    row = engine.obscurement_row(combat, "enemy:goblin-1")
    assert row["level"] == "lightly"
    assert row["sources"] == ["gm"]


def test_the_query_defaults_to_none_without_raising() -> None:
    engine, instance = _combat()
    combat = _state(instance)["combat"]
    assert engine.obscurement_level(combat, "enemy:goblin-1") == "none"
    assert engine.obscurement_level(combat, "player:nobody") == "none"
    assert engine.obscurement_row({}, "enemy:goblin-1")["level"] == "none"


def test_state_is_built_empty_and_never_leaks_into_a_new_fight() -> None:
    engine, instance = _combat()
    assert _declared(instance) == {}
    _apply(engine, instance, _declare(engine, instance, "heavily"))
    # 声明随战斗生灭：另一场战斗（新实例）从空开始。
    other_engine, other = _combat()
    assert _declared(other) == {}


def test_replaying_the_same_declaration_is_deterministic() -> None:
    engine, instance = _combat()
    resolved = _declare(engine, instance, "heavily")
    batch = deepcopy(resolved["event_batch"])
    assert engine.apply_batch(instance, batch)["applied"] is True
    first = deepcopy(_state(instance)["combat"]["obscurement"])
    engine.apply_batch(instance, deepcopy(batch))
    assert _state(instance)["combat"]["obscurement"] == first


# ---------------------------------------------------------------------------
# C2B v2：区域源、定级合并、感官合并
# ---------------------------------------------------------------------------

from src.rulesets.dnd2024.combat.obscurement import (  # noqa: E402
    effective_level,
    zone_covers,
)


def _zones(instance) -> dict:
    return _state(instance)["combat"].get("zones") or {}


def _zone(engine, instance, *, spell_ref="spell:fog_cloud", caster="player:gm",
          origin: int = 0, radius: int = 20, magical: bool = False,
          level: str = "heavily", concentration: bool = True,
          rounds: int = 10, zone_id: str = "z1") -> None:
    """Write one zone through the real reducer, the way a cast would."""

    _inject(engine, instance, [{
        "type": "dnd2024.zone.created", "zone_id": zone_id, "kind": "sphere",
        "level": level, "origin": origin, "radius_ft": radius,
        "magical": magical, "spell_ref": spell_ref, "caster_id": caster,
        "concentration": concentration, "rounds_left": rounds,
    }])


def test_zone_rows_are_written_with_their_magic_flag() -> None:
    engine, instance = _combat()
    _zone(engine, instance, magical=False)
    _zone(engine, instance, zone_id="z2", spell_ref="spell:darkness", magical=True)
    zones = _zones(instance)
    assert zones["z1"]["magical"] is False
    assert zones["z2"]["magical"] is True


def test_coverage_is_a_radius_on_the_existing_axis() -> None:
    assert zone_covers({"radius_ft": 20}, origin=10, position=25) is True
    assert zone_covers({"radius_ft": 20}, origin=10, position=31) is False


def test_a_creature_inside_a_zone_reads_that_level() -> None:
    engine, instance = _combat()
    _zone(engine, instance, origin=10, radius=20)
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 10
    row = engine.obscurement_row(combat, "enemy:goblin-1")
    assert row["level"] == "heavily"
    assert any(source.startswith("zone:") for source in row["sources"])


def test_walking_out_of_the_zone_returns_to_none() -> None:
    engine, instance = _combat()
    _zone(engine, instance, origin=0, radius=20)
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 10
    assert engine.obscurement_row(combat, "enemy:goblin-1")["level"] == "heavily"
    combat["positions"]["enemy:goblin-1"] = 40
    assert engine.obscurement_row(combat, "enemy:goblin-1")["level"] == "none"


def test_two_zones_do_not_stack() -> None:
    engine, instance = _combat()
    _zone(engine, instance, origin=0, radius=20, zone_id="z1")
    _zone(engine, instance, origin=0, radius=20, zone_id="z2")
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    row = engine.obscurement_row(combat, "enemy:goblin-1")
    assert row["level"] == "heavily"
    assert len(row["sources"]) == 2


def test_a_declaration_and_a_zone_take_the_higher_one() -> None:
    """v1 的"声明优先"作废：两者都是独立事实，答案只能是更高的那个。"""

    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, "lightly"))
    _zone(engine, instance, origin=0, radius=20, level="heavily")
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    assert engine.obscurement_row(combat, "enemy:goblin-1")["level"] == "heavily"


def test_concentration_loss_removes_only_that_casters_zone() -> None:
    engine, instance = _combat()
    _zone(engine, instance, caster="player:gm", zone_id="mine")
    _zone(engine, instance, caster="player:other", zone_id="theirs")
    _inject(engine, instance, [{
        "type": "dnd2024.concentration.ended", "actor_id": "player:gm",
        "reason": "test",
    }])
    assert set(_zones(instance)) == {"theirs"}


def test_a_zone_expires_when_its_rounds_run_out() -> None:
    engine, instance = _combat()
    _zone(engine, instance, rounds=1, zone_id="short")
    _zone(engine, instance, rounds=5, zone_id="long")
    _inject(engine, instance, [{
        "type": "dnd2024.turn.advanced", "actor_id": "player:gm",
        "previous_actor_id": "player:gm", "round": 2, "turn_index": 0,
        "economy": {},
    }])
    assert "short" not in _zones(instance)
    assert "long" in _zones(instance)


def test_darkvision_downgrades_nonmagical_heavy_to_light() -> None:
    level, bypass = effective_level(
        "heavily", magical=False, senses={"darkvision": 60}, distance_ft=30,
    )
    assert (level, bypass) == ("lightly", "darkvision")


def test_darkvision_cannot_see_through_magical_darkness() -> None:
    level, bypass = effective_level(
        "heavily", magical=True, senses={"darkvision": 60}, distance_ft=30,
    )
    assert level == "heavily"
    assert bypass == "darkvision_fails_magical_darkness"


def test_darkvision_out_of_range_changes_nothing() -> None:
    level, bypass = effective_level(
        "heavily", magical=False, senses={"darkvision": 60}, distance_ft=70,
    )
    assert (level, bypass) == ("heavily", "")


def test_blindsight_and_truesight_ignore_obscurement_within_range() -> None:
    assert effective_level(
        "heavily", magical=True, senses={"blindsight": 30}, distance_ft=20,
    ) == ("none", "blindsight")
    assert effective_level(
        "heavily", magical=True, senses={"truesight": 60}, distance_ft=20,
    ) == ("none", "truesight")
    assert effective_level(
        "heavily", magical=True, senses={"blindsight": 30}, distance_ft=40,
    ) == ("heavily", "")


def test_effective_obscurement_reads_the_observers_senses() -> None:
    engine, instance = _combat()
    _zone(engine, instance, origin=0, radius=20, magical=True)
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    combat.setdefault("positions", {})["player:gm"] = 20
    # 没有特殊感官：魔法黑暗照旧看不见。
    plain = engine.effective_obscurement(
        instance, combat, "player:gm", "enemy:goblin-1",
    )
    assert plain["level"] == "heavily"
    # 同一局面下观测者若声明了 blindsight，结论变成看得见。
    monster = combat["enemies"]["goblin-1"]
    monster.setdefault("senses", {})["blindsight"] = 30
    bypass = engine.effective_obscurement(
        instance, combat, "enemy:goblin-1", "enemy:goblin-1",
    )
    assert bypass["bypass"] in {"blindsight", "darkvision_fails_magical_darkness"}


def test_senses_reach_the_query_in_either_shape() -> None:
    """A stat block keeps a map; an actor view keeps rows, and both must count.

    ``sense_declarations`` returns ``[{"code", "range_ft", "source"}]`` and
    ``_player_view`` stores exactly that, while a monster's stat block keeps the
    flat ``{code: range}`` it was loaded from.  Reading only the mapping made
    every player character's senses vanish without an error -- the fog blocked
    sight and nothing said why.
    """

    rows = [
        {"code": "blindsight", "range_ft": 30, "source": "feral_senses"},
        # 同一感官来自两个声明时取更远的那个：多一个来源不该让人看得更近。
        {"code": "blindsight", "range_ft": 60, "source": "second_source"},
        # 旗标不是射程；当成 1 尺感官等于凭空造一条规则。
        {"code": "darkvision", "range_ft": True, "source": "flag_not_a_range"},
    ]
    assert sense_map(rows) == {"blindsight": 60}
    assert sense_map({"darkvision": 60, "blindsight": 30}) == {
        "darkvision": 60, "blindsight": 30,
    }
    assert effective_level(
        "heavily", magical=True, senses=rows, distance_ft=60,
    ) == ("none", "blindsight")
    assert effective_level(
        "heavily", magical=True, senses=rows, distance_ft=61,
    ) == ("heavily", "")


def test_a_player_characters_senses_are_not_dropped() -> None:
    """End to end: the actor view's own rows reach the obscurement answer.

    A Ranger 18 owns Feral Senses (Blindsight 30), and the actor view carries it
    as a declaration row.  Before the shapes were merged, ``effective_obscurement``
    fell back to "no senses" for every player character -- this asserts the
    answer a table would actually see, not the field that feeds it.
    """

    from test_dnd2024_combat import _instance_with_sheet
    from test_dnd2024_hunters_mark import ranger

    engine, instance = _instance_with_sheet(ranger(18))
    _start(engine, instance)
    _zone(engine, instance, origin=0, radius=20, magical=True)
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    combat.setdefault("positions", {})["player:gm"] = 20

    answer = engine.effective_obscurement(
        instance, combat, "player:gm", "enemy:goblin-1",
    )
    assert answer["bypass"] == "blindsight"
    assert answer["level"] == "none"


def test_casting_fog_cloud_leaves_a_nonmagical_zone() -> None:
    """端到端：真的施放一次，区域由结算路径落下，不是测试手工写进去的。"""

    from test_dnd2024_combat import _instance

    engine, instance = _instance(wizard=True)
    # 让这位法师"知道"雾云：卡片层的法术清单是被校验的那份数据源。
    canonical = instance.get_character_sheet("gm")["ruleset_character"]
    spellcasting = canonical["spellcasting"]["class"]
    for bucket in spellcasting.values():
        if isinstance(bucket, list) and any(
            isinstance(item, str) and item.startswith("spell:") for item in bucket
        ) and "spell:fog_cloud" not in bucket:
            bucket.append("spell:fog_cloud")
    _start(engine, instance)
    state = _state(instance)
    resolved = engine.resolve_intent(instance, {
        "intent_id": "cast-fog", "type": "cast_spell",
        "expected_version": int(state.get("version", 0)),
        "submitted_by": "gm", "actor_id": "player:gm",
        "spell_ref": "spell:fog_cloud", "slot_level": 1,
        "target_ids": ["enemy:goblin-1"],
    }, SequenceRng([]))
    assert resolved["ok"] is True, resolved
    _apply(engine, instance, resolved)
    zones = _zones(instance)
    assert len(zones) == 1
    zone = next(iter(zones.values()))
    assert (zone["level"], zone["magical"], zone["radius_ft"]) == ("heavily", False, 20)
    # 圆心落在施放时目标所在的坐标上，之后不随生物移动。
    assert zone["origin"] == _state(instance)["combat"]["positions"]["enemy:goblin-1"]


# ---------------------------------------------------------------------------
# C2E：攻击检定消费者
# ---------------------------------------------------------------------------

def _attack_mode(engine, instance, actor_id: str, target_id: str) -> dict:
    from src.rulesets.dnd2024.combat.check_modes import attack_mode

    combat = _state(instance)["combat"]
    actor = engine._actor_view(instance, combat, actor_id)
    target = engine._actor_view(instance, combat, target_id)
    return attack_mode(
        engine, instance, actor, target, False, combat=combat,
    )


def test_an_unseen_target_gives_disadvantage() -> None:
    engine, instance = _combat()
    _zone(engine, instance, origin=0, radius=20, level="heavily")
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    combat.setdefault("positions", {})["player:gm"] = 30
    mode = _attack_mode(engine, instance, "player:gm", "enemy:goblin-1")
    assert mode["mode"] == "disadvantage"
    assert {"code": "obscured_target", "mode": "disadvantage"} in mode["sources"]


def test_an_unseen_attacker_gives_advantage() -> None:
    engine, instance = _combat()
    _zone(engine, instance, origin=30, radius=20, level="heavily")
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["player:gm"] = 30
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    mode = _attack_mode(engine, instance, "player:gm", "enemy:goblin-1")
    assert mode["mode"] == "advantage"
    assert {"code": "unseen_attacker", "mode": "advantage"} in mode["sources"]


def test_both_obscured_cancels_out() -> None:
    engine, instance = _combat()
    _zone(engine, instance, origin=0, radius=60, level="heavily")
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["player:gm"] = 0
    combat.setdefault("positions", {})["enemy:goblin-1"] = 30
    mode = _attack_mode(engine, instance, "player:gm", "enemy:goblin-1")
    assert mode["mode"] == ""
    codes = {row["code"] for row in mode["sources"]}
    assert {"obscured_target", "unseen_attacker"} <= codes


def test_light_obscurement_does_not_touch_the_attack_roll() -> None:
    engine, instance = _combat()
    _zone(engine, instance, origin=0, radius=20, level="lightly")
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    combat.setdefault("positions", {})["player:gm"] = 30
    mode = _attack_mode(engine, instance, "player:gm", "enemy:goblin-1")
    assert mode["mode"] == ""


def test_blindsight_removes_both_sides_of_the_penalty() -> None:
    """观测者有盲视：既看得见目标，也不会因为自己看不见而被压制。"""

    engine, instance = _combat()
    _zone(engine, instance, origin=0, radius=20, level="heavily")
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    combat.setdefault("positions", {})["player:gm"] = 10
    monster = combat["enemies"]["goblin-1"]
    monster.setdefault("senses", {})["blindsight"] = 30
    mode = _attack_mode(engine, instance, "enemy:goblin-1", "player:gm")
    codes = {row["code"] for row in mode["sources"]}
    assert "obscured_target" not in codes


def test_the_penalty_reaches_the_actual_roll() -> None:
    """不只是 helper：真正掷一次攻击，事件里必须有两个骰并取低。"""

    engine, instance = _combat()
    _zone(engine, instance, origin=0, radius=4, level="heavily")
    combat = _state(instance)["combat"]
    # 徒手射程 5 尺：让两人都在雾里、都在触手可及处，遮蔽只来自区域而不来自距离。
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    combat.setdefault("positions", {})["player:gm"] = 5
    resolved = engine.resolve_intent(instance, {
        "intent_id": "atk-fog", "type": "attack",
        "expected_version": int(_state(instance).get("version", 0)),
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "unarmed_strike",
    }, SequenceRng([15, 4, 3]))
    assert resolved["ok"] is True, resolved
    live = _state(instance)["combat"]
    assert engine.effective_obscurement(
        instance, live, "player:gm", "enemy:goblin-1",
    )["blocks_sight"] is True, (live.get("positions"), live.get("zones"))
    assert engine.effective_obscurement(
        instance, live, "enemy:goblin-1", "player:gm",
    )["blocks_sight"] is False, (live.get("positions"), live.get("zones"))
    check = next(
        event for event in resolved["event_batch"]["events"]
        if event.get("type") == "check.resolved" and event.get("kind") == "attack"
    )
    assert check["advantage_mode"] == "disadvantage"
    assert len(check["rolls"]) == 2
    assert check["natural"] == 4


# ---------------------------------------------------------------------------
# 抵消语义：优势与劣势不是一一相抵，而是"同时存在即归零"
# ---------------------------------------------------------------------------

def test_any_mix_of_advantage_and_disadvantage_is_flat() -> None:
    """SRD：只要优势、劣势同时生效，就既无优势也无劣势——不看各有几条。"""

    from src.rulesets.dnd2024.combat.check_modes import mode_from_sources

    one_each = [
        {"code": "obscured_target", "mode": "disadvantage"},
        {"code": "unseen_attacker", "mode": "advantage"},
    ]
    assert mode_from_sources(one_each) == ""
    # 两条优势 + 一条劣势仍然是 flat：不是"净剩一条优势"。
    assert mode_from_sources([
        *one_each,
        {"code": "gm_grant", "mode": "advantage"},
    ]) == ""
    # 三条劣势 + 一条优势同理。
    assert mode_from_sources([
        {"code": "obscured_target", "mode": "disadvantage"},
        {"code": "long_range", "mode": "disadvantage"},
        {"code": "untrained_armor", "mode": "disadvantage"},
        {"code": "unseen_attacker", "mode": "advantage"},
    ]) == ""
    # 只有一侧时，条数也不影响：三条劣势仍是 disadvantage。
    assert mode_from_sources([
        {"code": "a", "mode": "disadvantage"},
        {"code": "b", "mode": "disadvantage"},
        {"code": "c", "mode": "disadvantage"},
    ]) == "disadvantage"


def test_two_sided_obscurement_rolls_exactly_one_d20() -> None:
    """双方互相看不见：一优一劣抵消，因此只掷一颗 d20。"""

    engine, instance = _combat()
    _zone(engine, instance, origin=0, radius=60, level="heavily")
    combat = _state(instance)["combat"]
    combat.setdefault("positions", {})["enemy:goblin-1"] = 0
    combat.setdefault("positions", {})["player:gm"] = 5
    resolved = engine.resolve_intent(instance, {
        "intent_id": "atk-mutual", "type": "attack",
        "expected_version": int(_state(instance).get("version", 0)),
        "submitted_by": "gm", "actor_id": "player:gm",
        "target_id": "enemy:goblin-1", "weapon_ref": "unarmed_strike",
    }, SequenceRng([15, 4, 3]))
    assert resolved["ok"] is True, resolved
    check = next(
        event for event in resolved["event_batch"]["events"]
        if event.get("type") == "check.resolved" and event.get("kind") == "attack"
    )
    assert check["advantage_mode"] == ""
    assert len(check["rolls"]) == 1
    codes = {row["code"] for row in check["advantage_sources"]}
    assert {"obscured_target", "unseen_attacker"} <= codes
