from types import SimpleNamespace

import pytest

from src.webui.services.assistant_gameplay_tools import GameplayToolDependencies, gameplay_tools
from src.webui.services.assistant_agent import validate_arguments
from src.webui.services.assistant_authorization import make_authorization, permits
from src.engine.participant_view import resolve_viewer


class _Game:
    run_id = "run-1"
    state = "active_action"
    players = {"alice": {"character_name": "Alice"}}

    def __init__(self):
        self.progress = {"active_nodes": ["opening"]}

    def get_character_sheet(self, uid):
        return {"ruleset_revision": 3, "hp": 8}


def _fixture():
    game = _Game()
    viewers = {"alice": SimpleNamespace(is_member=True, is_gm=False, uid="alice"),
               "gm": SimpleNamespace(is_member=True, is_gm=True, uid="gm")}
    recovery = SimpleNamespace(
        get=lambda key, viewer: {"ok": True, "revision": 4, "sources": []},
        mutate=lambda key, viewer, action, body: {"ok": True, "body": body},
    )
    deps = GameplayToolDependencies(
        get_game=lambda key: game if key == "game" else None,
        viewer_for=lambda instance, owner: viewers[owner],
        recovery=recovery,
        complete_adventure_node=lambda *args, **kwargs: {"ok": True},
    )
    return game, viewers, recovery, gameplay_tools(deps)


def test_gameplay_tools_are_importable_and_expose_authoritative_operations():
    _game, _viewers, _recovery, tools = _fixture()
    names = {tool.name for tool in tools}
    assert {"get_gameplay_status", "request_adventure_choice", "complete_adventure_node",
            "mutate_resource_recovery"}.issubset(names)


def test_room_preferences_and_policy_are_disjoint_and_policy_is_never_automatic():
    game, viewers, _recovery, _tools = _fixture()
    current = {"quick_actions_enabled": True, "advancement_authority": "gm"}
    writes = []
    deps = GameplayToolDependencies(
        get_game=lambda key: game,
        viewer_for=lambda instance, owner: viewers[owner],
        room_preferences=lambda key, viewer: {'ok': True, 'preferences': dict(current)},
        set_room_preferences=lambda key, viewer, values: writes.append(values) or {"ok": True},
    )
    tools = gameplay_tools(deps)
    prefs = next(item for item in tools if item.name == "set_room_preferences")
    policy = next(item for item in tools if item.name == "set_room_policy")
    assert prefs.version == 2 and prefs.automatic and prefs.effect == "write"
    assert not policy.automatic and policy.effect == "external"
    assert set(prefs.schema["properties"]["preferences"]["properties"]) == {
        "quick_actions_enabled", "dice_reveal_mode", "narrative_perspective", "story_mode"}
    assert set(policy.schema["properties"]["preferences"]["properties"]) == {
        "advancement_authority", "advancement_mode", "gm_style_override",
        "push_enabled", "luck_timeout_seconds"}
    with pytest.raises(ValueError):
        validate_arguments({"game_key": "game", "run_id": "run-1",
                            "preferences": {"advancement_authority": "gm"}}, prefs.schema)
    with pytest.raises(ValueError):
        validate_arguments({"game_key": "game", "run_id": "run-1",
                            "preferences": {"quick_actions_enabled": True}}, policy.schema)
    grant = make_authorization("routine", {"game_key": "game"}, ["settings"], 4, 0)
    assert permits(grant, prefs, {"game_key": "game"})
    assert not permits(grant, policy, {"game_key": "game"})
    preview = policy.preview({'game_key': 'game', 'run_id': 'run-1',
                              'preferences': {'advancement_authority': 'ai_gm'}}, 'gm')
    assert preview['before'] == {'advancement_authority': 'gm'}
    assert preview['after'] == {'advancement_authority': 'ai_gm'}


def test_gm_operation_rejects_non_gm_before_handler():
    _game, _viewers, _recovery, tools = _fixture()
    tool = next(item for item in tools if item.name == "complete_adventure_node")
    with pytest.raises(PermissionError, match="GM"):
        tool.guard({"game_key": "game", "run_id": "run-1", "node_id": "opening"}, "alice")


def test_recovery_guard_changes_when_authoritative_revision_changes():
    _game, _viewers, recovery, tools = _fixture()
    tool = next(item for item in tools if item.name == "mutate_resource_recovery")
    args = {"game_key": "game", "run_id": "run-1", "action": "request", "expected_revision": 4}
    first = tool.guard(args, "alice")
    recovery.get = lambda key, viewer: {"ok": True, "revision": 5, "sources": []}
    assert first != tool.guard(args, "alice")


@pytest.mark.asyncio
async def test_recovery_write_generates_operation_id_and_preserves_run_revision():
    _game, _viewers, _recovery, tools = _fixture()
    tool = next(item for item in tools if item.name == "mutate_resource_recovery")
    result = await tool.handler({"game_key": "game", "run_id": "run-1", "action": "request",
                                 "expected_revision": 4, "payload": {"kind": "overnight"}}, "alice")
    assert result["body"]["run_id"] == "run-1"
    assert result["body"]["expected_revision"] == 4
    assert len(result["body"]["operation_id"]) == 32


@pytest.mark.asyncio
async def test_recovery_payload_cannot_replace_authority_fields():
    _game, _viewers, _recovery, tools = _fixture()
    tool = next(item for item in tools if item.name == "mutate_resource_recovery")
    with pytest.raises(ValueError, match="reserved fields"):
        await tool.handler({"game_key": "game", "run_id": "run-1", "action": "request",
                            "expected_revision": 4,
                            "payload": {"run_id": "forged", "kind": "overnight"}}, "alice")


@pytest.mark.asyncio
async def test_rest_requires_explicit_confirmation_and_is_manual_external():
    game, viewers, _recovery, _unused_tools = _fixture()
    # Keep the callback result visible while recording the exact authority body.
    received = []
    deps = GameplayToolDependencies(
        get_game=lambda key: game,
        viewer_for=lambda instance, owner: viewers[owner],
        rest=lambda key, uid, body: received.append(body) or {"ok": True},
    )
    tool = next(item for item in gameplay_tools(deps) if item.name == "perform_ruleset_rest")
    assert tool.effect == "external" and not tool.automatic
    args = {"game_key": "game", "run_id": "run-1", "rest": "short",
            "expected_revision": 3, "confirm_elapsed_time": True, "hit_dice": {"d10": 1}}
    validate_arguments(args, tool.schema)
    preview = tool.preview(args, "alice")
    assert preview["rest_type"] == "short"
    assert preview["target_character"] == {"uid": "alice", "name": "Alice"}
    assert preview['confirm_elapsed_time'] is True
    assert preview["hit_dice"] == {"d10": 1}
    await tool.handler(args, "alice")
    assert received[0]["confirm_elapsed_time"] is True
    with pytest.raises(ValueError, match="explicitly true"):
        await tool.handler({**args, "confirm_elapsed_time": False}, "alice")


@pytest.mark.asyncio
async def test_real_adventure_command_adapter_enforces_gm_and_run_fences(tmp_path):
    from src.engine.modules import adventure_runtime_state
    from src.webui.services import adventure_commands, adventure_runtime
    from tests.test_adventure_choices import installed_choice

    runtime_deps, game = installed_choice(tmp_path)
    game.gm_uid = "gm"
    async def save(_instance):
        return None
    async def binding():
        return None
    command_deps = adventure_commands.AdventureCommandDependencies(
        save=save,
        admission=lambda: None,
        binding=binding,
        complete=lambda target, node, recipient, actor, choice_id="": adventure_runtime.complete_adventure_node(
            runtime_deps, target, node, choice_id=choice_id),
        request_choice=lambda target, node, choice, actor: adventure_runtime.request_adventure_choice(
            runtime_deps, target, node, choice, actor),
    )

    async def command(instance, body, actor, is_gm):
        return await adventure_commands.execute(command_deps, instance, body, actor, is_gm=is_gm)

    deps = GameplayToolDependencies(
        get_game=lambda key: game,
        viewer_for=lambda instance, owner: resolve_viewer(
            instance, user_id=owner, owner_authenticated=False, player_preview=False),
        adventure_command=command,
    )
    tools = gameplay_tools(deps)
    complete = next(tool for tool in tools if tool.name == "complete_adventure_node")
    args = {"game_key": "game", "run_id": game.run_id, "node_id": "gate", "choice_id": "bridge_route"}
    with pytest.raises(PermissionError):
        complete.guard(args, "alice")
    with pytest.raises(ValueError, match="stale"):
        await complete.handler({**args, "run_id": "other"}, "gm")

    result = await complete.handler(args, "gm")
    assert result["ok"]
    assert adventure_runtime_state.progress(game)["active_nodes"] == ["bridge"]
    # A second dispatch cannot duplicate the completed node or any outcome.
    second = await complete.handler(args, "gm")
    assert not second["ok"]


@pytest.mark.asyncio
async def test_ruleset_actions_unavailable_reads_as_information_not_failure():
    """自由文本规则的 get_ruleset_available_actions 是产品语义，不是故障。

    服务端返回 RULESET_INTENTS_UNAVAILABLE 时，工具降级为
    ok=True + available=False 的说明性结果：运行日志不再把每次探询
    记成失败，agent 也不会把 409 语义当成可重试错误。
    """
    game = _Game()
    viewers = {"alice": SimpleNamespace(is_member=True, is_gm=False, uid="alice")}
    deps = GameplayToolDependencies(
        get_game=lambda key: game if key == "game" else None,
        viewer_for=lambda instance, owner: viewers[owner],
        available_ruleset_actions=lambda *args: {
            "ok": False,
            "code": "RULESET_INTENTS_UNAVAILABLE",
            "error": "该规则继续使用自由文本回合流程",
        },
    )
    tool = next(item for item in gameplay_tools(deps) if item.name == "get_ruleset_available_actions")
    result = await tool.handler({"game_key": "game"}, "alice")
    assert result["ok"] is True
    assert result["available"] is False
    assert result["actions"] == []
    assert "自由文本" in result["reason"]
