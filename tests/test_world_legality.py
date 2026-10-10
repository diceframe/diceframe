"""Visibility projection + action legality v1 (WP5 / Issue 284).

Two halves:

* **World truth is not player-visible truth.**  ``project_visible_state`` is the
  only supported way to expose world truth, GM-private facts stay out of every
  player-facing projection, and the GM prompt marks what players cannot see.
* **Action legality is server-side evidence, not prompt wording.**  The planner
  may *propose* where an action happens; ``evaluate_world_requirements`` decides
  against authoritative world truth.  A proven contradiction becomes "move
  first / cannot complete" instead of an implicit teleport, while an empty world
  or unknown location never manufactures a block.
"""

from __future__ import annotations

import pytest

from src.commands.check_planner import _planner_context, plan_round_checks
from src.engine.game_instance import GameInstance, GameState
from src.engine.world_legality import (
    actor_location_fact_key,
    evaluate_world_requirements,
    passable_fact_key,
)
from src.engine.world_state import (
    apply_world_ops,
    fact_value,
    project_visible_state,
)
from src.llm.context_builder import build_context
from src.llm.world_prompt import (
    format_world_legality_block,
    format_world_state_block,
)
from src.rules.rule_system import RuleSystem
from src.engine.modules import world_reports


def make_instance(*, world: bool = True, language: str = "zh-CN") -> GameInstance:
    instance = GameInstance(game_key=("web", "world-legality", "bot"), rule_id="test")
    instance.language = language
    instance.state = GameState.ACTIVE_ACTION
    instance.players = {
        "p1": {"user_id": "p1", "character_name": "阿岚",
               "character_sheet": {"attributes": {"str": 12}}},
        "p2": {"user_id": "p2", "character_name": "白露",
               "character_sheet": {"attributes": {"dex": 14}}},
    }
    if world:
        apply_world_ops(instance, [
            {"op": "set_fact", "key": actor_location_fact_key("p1"),
             "value": "village_east"},
            {"op": "set_fact", "key": passable_fact_key("village_east"), "value": True},
            {"op": "set_fact", "key": passable_fact_key("village_west"), "value": True},
            {"op": "set_fact", "key": passable_fact_key("bridge_old"), "value": False},
            {"op": "set_fact", "key": passable_fact_key("north_tower"), "value": False,
             "visibility": "gm"},
            {"op": "set_fact", "key": "secret:cult.plan", "value": "ritual",
             "visibility": "gm"},
        ])
    return instance


def requirement(**overrides) -> dict:
    payload = {"player": "p1", "kind": "act", "location": "village_west"}
    payload.update(overrides)
    return payload


# ---- Part A: visibility ------------------------------------------------------


def test_gm_sees_hidden_facts_players_do_not() -> None:
    instance = make_instance()

    gm_view = project_visible_state(instance, viewer_is_gm=True)
    player_view = project_visible_state(instance, viewer_uid="p1", viewer_is_gm=False)

    assert "secret:cult.plan" in gm_view["facts"]
    assert "secret:cult.plan" not in player_view["facts"]
    assert set(player_view["facts"]) == {
        actor_location_fact_key("p1"),
        passable_fact_key("village_east"),
        passable_fact_key("village_west"),
        passable_fact_key("bridge_old"),
    }
    assert player_view["viewer"] == "player:p1"
    assert player_view["revision"] == gm_view["revision"]


def test_player_facing_blocks_never_contain_gm_only_facts() -> None:
    instance = make_instance()

    player_text = format_world_state_block(instance, viewer_is_gm=False, viewer_uid="p1")
    gm_text = format_world_state_block(instance, viewer_is_gm=True)

    assert "secret:cult.plan" not in player_text
    assert "north_tower" not in player_text
    assert "village_east" in player_text
    assert "secret:cult.plan" in gm_text
    assert "GM 私有" in gm_text


def test_gm_context_carries_world_truth_and_legality_blocks() -> None:
    instance = make_instance()
    world_reports.replace_last_world_legality(instance, [{
        "player": "p1", "code": "ACTION_LOCATION_MISMATCH",
        "location": "village_west", "current": "village_east",
    }])

    context = await_build_context(instance)

    assert "【世界真相·必须遵循】" in context
    assert "secret:cult.plan" in context
    assert "【行动合法性·必须遵循】" in context
    assert "必须先移动" in context
    # GM 私有事实与 legality 块都在玩家发言之后的可信段，不在玩家块内。
    player_start = context.index("【玩家发言】")
    separator = context.index("\n\n---\n\n", player_start)
    assert "secret:cult.plan" not in context[player_start:separator]


def await_build_context(instance: GameInstance) -> str:
    import asyncio

    return asyncio.run(build_context(
        instance, "SYS", [], "我去村西寻找线索。",
        world_state_text=format_world_state_block(instance, viewer_is_gm=True),
        world_legality_text=format_world_legality_block(instance),
    ))


# ---- Part B: action legality -------------------------------------------------


def test_acting_away_from_the_authoritative_location_is_blocked() -> None:
    """#284 原始场景：人在村东，却声明在村西执行本地动作。"""

    instance = make_instance()

    result = evaluate_world_requirements(instance, [requirement()])

    assert result["notes"] == [{
        "player": "p1", "code": "ACTION_LOCATION_MISMATCH",
        "location": "village_west", "current": "village_east",
    }]
    assert result["applied"] == []
    # 不隐式瞬移：权威位置不变。
    assert fact_value(instance.world_state, actor_location_fact_key("p1")) == "village_east"


def test_acting_at_the_current_location_is_allowed() -> None:
    instance = make_instance()

    result = evaluate_world_requirements(
        instance, [requirement(location="village_east")],
    )

    assert result == {"notes": [], "applied": []}


def test_legal_move_is_recorded_and_then_allows_the_action() -> None:
    instance = make_instance()

    moved = evaluate_world_requirements(instance, [
        requirement(kind="move", location="village_west"),
    ])

    assert moved["notes"] == []
    assert moved["applied"] == [{
        "player": "p1", "location": "village_west", "from": "village_east",
    }]
    assert fact_value(instance.world_state, actor_location_fact_key("p1")) == "village_west"

    # 移动之后同一地点的本地动作合法。
    assert evaluate_world_requirements(instance, [requirement()])["notes"] == []


def test_route_through_an_impassable_location_is_blocked() -> None:
    instance = make_instance()

    result = evaluate_world_requirements(instance, [
        requirement(kind="move", location="village_west", via=["bridge_old"]),
    ])

    assert result["notes"] == [{
        "player": "p1", "code": "ROUTE_IMPASSABLE", "location": "bridge_old",
        "destination": "village_west", "current": "village_east",
    }]
    assert result["applied"] == []
    assert fact_value(instance.world_state, actor_location_fact_key("p1")) == "village_east"


def test_impassable_destination_is_blocked() -> None:
    instance = make_instance()

    result = evaluate_world_requirements(instance, [
        requirement(kind="move", location="bridge_old"),
    ])

    assert [note["code"] for note in result["notes"]] == ["ROUTE_IMPASSABLE"]
    assert result["notes"][0]["location"] == "bridge_old"
    assert result["applied"] == []


def test_gm_only_impassable_fact_still_blocks_the_actor() -> None:
    """可见性只影响投影，不影响权威判定。"""

    instance = make_instance()

    result = evaluate_world_requirements(instance, [
        requirement(kind="move", location="north_tower"),
    ])

    assert [note["code"] for note in result["notes"]] == ["ROUTE_IMPASSABLE"]


def test_actor_can_leave_an_impassable_current_location() -> None:
    """passable=false 阻止进入 / 经过 / 抵达，不把行动者锁死在原地。"""

    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "set_fact", "key": actor_location_fact_key("p1"), "value": "bridge_old"},
    ])
    assert fact_value(instance.world_state, actor_location_fact_key("p1")) == "bridge_old"
    assert fact_value(instance.world_state, passable_fact_key("bridge_old")) is False

    result = evaluate_world_requirements(instance, [
        requirement(kind="move", location="village_west"),
    ])

    # 当前位置不参与路线检查：不能因为「已经站在不可通行的地方」就无法离开。
    assert result["notes"] == []
    assert result["applied"] == [{
        "player": "p1", "location": "village_west", "from": "bridge_old",
    }]
    assert fact_value(instance.world_state, actor_location_fact_key("p1")) == "village_west"


def test_impassable_current_location_does_not_soften_the_route_check() -> None:
    """当前位置被豁免，不代表声明的途经点被豁免。"""

    instance = make_instance()
    apply_world_ops(instance, [
        {"op": "set_fact", "key": actor_location_fact_key("p1"), "value": "bridge_old"},
    ])

    result = evaluate_world_requirements(instance, [
        requirement(kind="move", location="village_west", via=["north_tower"]),
    ])

    assert result["notes"] == [{
        "player": "p1", "code": "ROUTE_IMPASSABLE", "location": "north_tower",
        "destination": "village_west", "current": "bridge_old",
    }]
    assert result["applied"] == []
    assert fact_value(instance.world_state, actor_location_fact_key("p1")) == "bridge_old"


def test_empty_world_and_unknown_locations_never_block() -> None:
    """§7.7：信息不足不产生虚假硬阻断（旧游戏路径保持正常）。"""

    empty = make_instance(world=False)
    assert evaluate_world_requirements(empty, [requirement()]) == {
        "notes": [], "applied": [],
    }

    instance = make_instance()
    unknown = evaluate_world_requirements(instance, [
        requirement(location="nowhere_in_particular"),
        requirement(kind="move", location="nowhere_in_particular"),
    ])
    # 世界没有登记的地点：不判非法，也不写入任何位置。
    assert unknown == {"notes": [], "applied": []}
    assert fact_value(instance.world_state, actor_location_fact_key("p1")) == "village_east"

    # 行动者本人没有登记位置时（act）同样不阻断。
    actorless = evaluate_world_requirements(instance, [
        requirement(player="p2"),
    ])
    assert actorless["notes"] == []


@pytest.mark.parametrize("requirements", [
    None,
    [],
    "not-a-list",
    [{"player": "ghost", "kind": "act", "location": "village_west"}],
    [{"player": "p1", "kind": "teleport", "location": "village_west"}],
    [{"player": "p1", "kind": "act"}],
    [{"player": "p1", "kind": "act", "location": ""}],
    [{"player": "p1", "kind": "act", "location": 42}],
    ["not-an-object"],
])
def test_malformed_requirements_are_ignored(requirements: object) -> None:
    instance = make_instance()

    result = evaluate_world_requirements(instance, requirements)

    assert result == {"notes": [], "applied": []}
    assert fact_value(instance.world_state, actor_location_fact_key("p1")) == "village_east"


def test_legality_notes_persist_separately_from_overreach() -> None:
    instance = make_instance()
    world_reports.replace_last_world_legality(instance, [{
        "player": "p1", "code": "ACTION_LOCATION_MISMATCH",
        "location": "village_west", "current": "village_east",
    }])
    world_reports.replace_last_overreach(instance, [{"player": "p1", "reason": "把世界事实当既成事实"}])

    reloaded = GameInstance.from_dict(instance.to_dict())

    assert world_reports.last_world_legality(reloaded) == world_reports.last_world_legality(instance)
    assert world_reports.last_overreach(reloaded) == world_reports.last_overreach(instance)
    reloaded.reset_round_checks()
    assert world_reports.last_world_legality(reloaded) == []
    assert world_reports.last_overreach(reloaded) == []


def test_legality_block_is_localized_and_empty_without_notes() -> None:
    instance = make_instance()
    assert format_world_legality_block(instance) == ""

    world_reports.replace_last_world_legality(instance, [{
        "player": "p1", "code": "ACTION_LOCATION_MISMATCH",
        "location": "village_west", "current": "village_east",
    }])
    zh = format_world_legality_block(instance)
    assert "阿岚" in zh and "village_west" in zh

    instance.language = "en"
    assert "must move first" in format_world_legality_block(instance)


# ---- planner seam ------------------------------------------------------------


def test_planner_context_publishes_world_facts_with_canonical_ids() -> None:
    instance = make_instance()

    context = _planner_context(instance, None)

    assert actor_location_fact_key("p1") in context
    assert "village_east" in context
    # 没有世界事实时不注入该段，旧游戏 planner 上下文保持原样。
    assert "world_state" not in _planner_context(make_instance(world=False), None)


class _FakeToolResponse:
    native_tools = True
    provider_used = "fake"
    total_tokens = 7

    def __init__(self, arguments: dict):
        self.tool_calls = [{"name": "dice_checks", "arguments": arguments}]


class _FakeToolClient:
    default = "fake"

    def __init__(self, arguments: dict):
        self._arguments = arguments

    async def call_tools(self, *_args, **_kwargs):
        return _FakeToolResponse(self._arguments)


def planner_rule() -> RuleSystem:
    return RuleSystem({
        "rule_id": "test",
        "name": "Test",
        "dice_system": "d20",
        "mechanics": "dnd5e_core",
        "attributes": [
            {"key": "str", "name": "力量"},
            {"key": "dex", "name": "敏捷"},
        ],
        "dc_table": {"easy": 8, "normal": 12, "hard": 16},
    })


@pytest.mark.asyncio
async def test_planner_parses_structured_world_requirements() -> None:
    instance = make_instance()
    instance.action_queue = [{"user_id": "p1", "text": "我在村西搜寻线索"}]
    client = _FakeToolClient({
        "checks": [],
        "world_requirements": [
            {"player": "阿岚", "kind": "act", "location": "village_west"},
            {"player": "p1", "kind": "move", "location": "village_west",
             "via": ["bridge_old", "bridge_old", 7]},
            # 花名册外/形状非法：忽略，且不影响上面两条。
            {"player": "幽灵", "kind": "act", "location": "village_west"},
            {"player": "p1", "kind": "act"},
            "not-an-object",
        ],
    })

    _planned, metadata = await plan_round_checks(instance, planner_rule(), client)

    assert metadata["world_requirements"] == [
        {"player": "p1", "kind": "act", "location": "village_west"},
        {"player": "p1", "kind": "move", "location": "village_west",
         "via": ["bridge_old"]},
    ]


@pytest.mark.asyncio
async def test_planner_requirements_drive_the_server_legality_decision() -> None:
    """planner 只提议，是否阻断由 server 对照权威世界真相决定。"""

    instance = make_instance()
    instance.action_queue = [{"user_id": "p1", "text": "我在村西搜寻线索"}]
    client = _FakeToolClient({
        "checks": [],
        "world_requirements": [
            {"player": "p1", "kind": "act", "location": "village_west"},
        ],
    })

    _planned, metadata = await plan_round_checks(instance, planner_rule(), client)
    result = evaluate_world_requirements(instance, metadata["world_requirements"])

    assert [note["code"] for note in result["notes"]] == ["ACTION_LOCATION_MISMATCH"]
    assert fact_value(instance.world_state, actor_location_fact_key("p1")) == "village_east"


@pytest.mark.asyncio
async def test_missing_or_malformed_requirement_output_never_breaks_checks() -> None:
    instance = make_instance()
    instance.action_queue = [{"user_id": "p1", "text": "我推开木门"}]

    for arguments in ({"checks": []},
                      {"checks": [], "world_requirements": "not-a-list"},
                      {"checks": [], "world_requirements": [42]}):
        _planned, metadata = await plan_round_checks(
            instance, planner_rule(), _FakeToolClient(arguments),
        )
        assert metadata["world_requirements"] == []
        assert metadata["errors"] == []
