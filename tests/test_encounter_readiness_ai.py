"""遭遇准备 readiness 投影的 AI 座位回归测试。

设计对齐普通回合 ``human_actions_ready``：AI 托管、未认领、暂离或已倒下
的席位不参与人工 ready barrier。一个不会点击"准备"的 AI 队友不能把整桌
遭遇卡成永久 pending；全 AI 队伍时 GM 单独即可开始遭遇。
"""

from __future__ import annotations

from src.engine.game_instance import GameInstance
from src.engine.player_control import set_control
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from tests.test_dnd2024_temporary_encounter import _character


def _instance_with_players(runtime: Dnd2024Runtime, seats: list[str]) -> GameInstance:
    instance = GameInstance(
        game_key=("test", "enc-ready", "web"),
        world_id="default_fantasy",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    presets = {
        "gm": ("stalwart_guardian", "Arden"),
        "ally": ("curious_arcanist", "Mira"),
        "ai_seat": ("stalwart_guardian", "Bram"),
        "ai_seat2": ("curious_arcanist", "Nissa"),
    }
    for uid in seats:
        preset_id, name = presets[uid]
        sheet = _character(runtime, preset_id, name)
        instance.players[uid] = {"character_name": name, "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(
        instance.players["gm"]["character_sheet"]["rule_binding"]
    )
    return instance


def _readiness(instance: GameInstance, runtime: Dnd2024Runtime) -> dict:
    view = runtime.gameplay_view(instance, "gm", True)
    return view["encounter_request"]["readiness"]


def test_ai_hosted_seat_is_not_required_for_encounter_ready() -> None:
    runtime = Dnd2024Runtime()
    instance = _instance_with_players(runtime, ["gm", "ally", "ai_seat"])
    set_control(instance, "ai_seat", "ai")

    instance.ruleset_state["encounter_request"] = {
        "status": "pending",
        "ready_player_ids": ["ally"],
    }
    readiness = _readiness(instance, runtime)

    assert readiness["required_player_ids"] == ["ally"]
    assert readiness["all_ready"] is True
    assert [item["player_id"] for item in readiness["players"]] == ["ally"]


def test_human_seat_still_blocks_until_ready() -> None:
    runtime = Dnd2024Runtime()
    instance = _instance_with_players(runtime, ["gm", "ally", "ai_seat"])
    set_control(instance, "ai_seat", "ai")

    instance.ruleset_state["encounter_request"] = {
        "status": "pending",
        "ready_player_ids": [],
    }
    readiness = _readiness(instance, runtime)

    assert readiness["required_player_ids"] == ["ally"]
    assert readiness["all_ready"] is False


def test_default_control_mode_keeps_human_requirement() -> None:
    runtime = Dnd2024Runtime()
    instance = _instance_with_players(runtime, ["gm", "ally"])

    instance.ruleset_state["encounter_request"] = {
        "status": "pending",
        "ready_player_ids": [],
    }
    readiness = _readiness(instance, runtime)

    assert readiness["required_player_ids"] == ["ally"]
    assert readiness["all_ready"] is False


def test_all_ai_party_ready_with_gm_alone() -> None:
    runtime = Dnd2024Runtime()
    instance = _instance_with_players(runtime, ["gm", "ai_seat", "ai_seat2"])
    set_control(instance, "ai_seat", "ai")
    set_control(instance, "ai_seat2", "ai")

    instance.ruleset_state["encounter_request"] = {
        "status": "pending",
        "ready_player_ids": [],
    }
    readiness = _readiness(instance, runtime)

    assert readiness["required_player_ids"] == []
    assert readiness["required_count"] == 0
    assert readiness["all_ready"] is True
    assert readiness["players"] == []
