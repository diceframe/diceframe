from copy import deepcopy

import pytest

from src.engine.participant_view import Viewer
from src.engine.world_state import clock_to_minutes
from src.webui.api import WebAPI
from src.webui.services.assistant_agent import AgentDependencies, AgentRuntime
from src.webui.services.assistant_gameplay_tools import GameplayToolDependencies, gameplay_tools
from tests.rulesets.test_dnd2024_character_lifecycle import professional_context
from tests.test_assistant_agent import Planner


@pytest.mark.asyncio
async def test_agent_rest_uses_shared_party_dispatch_and_waits_for_second_player(
    professional_context,
):
    api, instance, character = professional_context
    ally = deepcopy(character)
    ally["character_name"] = "Second Hero"
    ally["ruleset_character"]["identity"]["name"] = "Second Hero"
    instance.players["ally"] = {"character_name": "Second Hero", "character_sheet": ally}
    for uid in ("gm", "ally"):
        sheet = instance.get_character_sheet(uid)
        sheet["hp"] = 1
        sheet["ruleset_character"]["resources"]["hp"] = 1
    from src.engine.modules import game_config
    game_config.replace_solo_mode(instance, False)

    def viewer_for(game, owner):
        return Viewer("gm" if owner == "gm" else "seat", owner)

    async def rest(key, uid, body):
        # Exercise the production WebAPI dispatch method on the real fixture's
        # dependencies; the Agent and HTTP route share this entry point.
        return await WebAPI.ruleset_rest_submit_live(api, key, uid, body)

    tools = gameplay_tools(GameplayToolDependencies(
        get_game=lambda key: instance,
        viewer_for=viewer_for,
        rest=rest,
    ))
    rest_tool = next(tool for tool in tools if tool.name == "perform_ruleset_rest")
    key = "web|character-lifecycle|web_bot"
    before_clock = clock_to_minutes(instance.world_state["clock"])
    first = await rest_tool.handler({
        "game_key": key, "run_id": instance.run_id, "rest": "short",
        "expected_revision": 0, "hit_dice": {"d10": 1},
        "confirm_elapsed_time": True,
    }, "gm")
    assert first["pending"] is True and first["resolved"] is False
    assert clock_to_minutes(instance.world_state["clock"]) == before_clock
    assert instance.get_character_sheet("gm").get("ruleset_revision", 0) == 0

    second = await rest_tool.handler({
        "game_key": key, "run_id": instance.run_id, "rest": "short",
        "expected_revision": 0, "hit_dice": {"d10": 1},
        "confirm_elapsed_time": True,
    }, "ally")
    # 全员申报只走到「等 DM 裁决」：结算与时间推进都还不发生。
    assert second["resolved"] is False
    assert second["rest_session"]["status"] == "awaiting_approval"
    assert instance.get_character_sheet("gm").get("ruleset_revision", 0) == 0
    assert clock_to_minutes(instance.world_state["clock"]) == before_clock

    decided = await WebAPI.ruleset_rest_decide(api, key, "gm", True, {
        "approve": True, "operation_id": "party-approve",
    })
    assert decided["ok"] is True
    assert decided["rest_session"]["status"] == "completed"
    assert instance.get_character_sheet("gm")["ruleset_revision"] == 1
    assert instance.get_character_sheet("ally")["ruleset_revision"] == 1
    # 短休的 1 小时在批准那一刻推进，且只推进一次。
    assert clock_to_minutes(instance.world_state["clock"]) == before_clock + 60

    # A completed session cannot settle a second time with the old revisions.
    duplicate = await rest_tool.handler({
        "game_key": key, "run_id": instance.run_id, "rest": "short",
        "expected_revision": 0, "hit_dice": {"d10": 1},
        "confirm_elapsed_time": True,
    }, "gm")
    assert not duplicate["ok"]
    assert instance.get_character_sheet("gm")["ruleset_revision"] == 1


@pytest.mark.asyncio
async def test_shared_dispatch_keeps_solo_rest_behavior(professional_context):
    api, instance, _character = professional_context
    from src.engine.modules import game_config
    game_config.replace_solo_mode(instance, True)

    async def rest(key, uid, body):
        return await WebAPI.ruleset_rest_submit_live(api, key, uid, body)

    tools = gameplay_tools(GameplayToolDependencies(
        get_game=lambda key: instance,
        viewer_for=lambda game, owner: Viewer("gm", owner),
        rest=rest,
    ))
    rest_tool = next(tool for tool in tools if tool.name == "perform_ruleset_rest")
    result = await rest_tool.handler({
        "game_key": "web|character-lifecycle|web_bot", "run_id": instance.run_id,
        "rest": "short", "expected_revision": 0, "hit_dice": {"d10": 1},
        "confirm_elapsed_time": True,
    }, "gm")
    assert result["ok"] is True
    assert result.get("pending") is not True


@pytest.mark.asyncio
async def test_agent_proposal_confirm_replay_then_second_party_submission(
    professional_context, tmp_path,
):
    api, instance, character = professional_context
    ally = deepcopy(character)
    ally["character_name"] = "Second Hero"
    ally["ruleset_character"]["identity"]["name"] = "Second Hero"
    instance.players["ally"] = {"character_name": "Second Hero", "character_sheet": ally}
    for uid in ('gm', 'ally'):
        sheet = instance.get_character_sheet(uid)
        sheet['hp'] = 1
        sheet['ruleset_character']['resources']['hp'] = 1
    from src.engine.modules import game_config
    game_config.replace_solo_mode(instance, False)
    key = "web|character-lifecycle|web_bot"

    async def rest(game_key, uid, body):
        return await WebAPI.ruleset_rest_submit_live(api, game_key, uid, body)

    gameplay = gameplay_tools(GameplayToolDependencies(
        get_game=lambda _key: instance,
        viewer_for=lambda _game, owner: Viewer("gm" if owner == "gm" else "seat", owner),
        rest=rest,
    ))

    args = {
        "game_key": key, "run_id": instance.run_id, "rest": "short",
        "expected_revision": 0, "hit_dice": {"d10": 1},
        "confirm_elapsed_time": True,
    }
    planner = Planner({'tool': 'perform_ruleset_rest', 'arguments': args})
    agent = AgentRuntime(AgentDependencies(
        llm_client=lambda: planner, configuration_error=lambda _lang: None,
        tools=gameplay, storage_dir=tmp_path / 'agent',
    ))
    grant = agent.authorize('gm', '', 'task', {'game_key': key}, ['ruleset'], 8)
    session_id = grant['session_id']
    events = []
    async def emit(name, value):
        events.append((name, deepcopy(value)))
    await agent.run('gm', [{'role': 'user', 'content': 'Take a short rest'}], 'en', {}, session_id, emit)
    approval = next(value for name, value in events if name == 'approval')
    assert instance.get_character_sheet('gm')['hp'] == 1

    first = await agent.confirm("gm", session_id, approval["approval_id"], approval["plan_digest"], True)
    replay = await agent.confirm("gm", session_id, approval["approval_id"], approval["plan_digest"], True)
    assert first == replay
    assert first["result"]["pending"] is True
    assert instance.get_character_sheet("gm").get("ruleset_revision", 0) == 0
    assert instance.get_character_sheet('gm')['hp'] == 1
    assert instance.get_character_sheet('ally')['hp'] == 1

    second = await WebAPI.ruleset_rest_submit_live(api, key, "ally", {
        "rest": "short", "hit_dice": {"d10": 1}, "confirm_elapsed_time": True,
        "expected_revision": 0, "operation_id": "party-ally-after-agent",
    })
    assert second["rest_session"]["status"] == "awaiting_approval"
    assert instance.get_character_sheet("gm").get("ruleset_revision", 0) == 0
    assert instance.get_character_sheet("gm")['hp'] == 1

    decided = await WebAPI.ruleset_rest_decide(api, key, "gm", True, {
        "approve": True, "operation_id": "party-approve-after-agent",
    })
    assert decided["ok"] is True
    assert instance.get_character_sheet("gm")["ruleset_revision"] == 1
    assert instance.get_character_sheet("ally")["ruleset_revision"] == 1
    assert instance.get_character_sheet('gm')['hp'] > 1
    assert instance.get_character_sheet('ally')['hp'] > 1
    assert len(instance.get_character_sheet("gm")["ruleset_operation_log"]) == 1
