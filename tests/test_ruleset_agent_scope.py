# -*- coding: utf-8 -*-
"""规则域的 agent 作用域：发现、授权与工具接线。

这些用例锁住三件事：作用域内的会话看得到、作用域外的会话搜不到也 describe
不到（且统计里不出现），以及授权列表的既有语义不因规则域而改变。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.application_actions import ActionRegistry, ApplicationAction, visible_domain
from src.llm.client import LLMToolResponse
from src.webui.services.assistant_agent import AgentDependencies, AgentRuntime
from src.webui.api import WebAPI
from src.webui.services.assistant_authorization import make_authorization, permits
from src.webui.services.assistant_ruleset_tools import (
    RulesetToolDependencies,
    ruleset_domain,
    ruleset_source_name,
    ruleset_tools,
)

RULESET_DOMAIN = "ruleset-core-dnd2024"
GAME_KEY = "web|room|bot"


def action(
    name: str, domain: str, *, handler=None,
    effect: str = "write", mutation: bool = True, automatic: bool = False,
) -> ApplicationAction:
    return ApplicationAction(
        name, name, {"type": "object", "properties": {}},
        handler or (lambda args, owner: {"ok": True}),
        domain=domain, effect=effect, mutation=mutation, automatic=automatic,
    )


def _registry() -> ActionRegistry:
    return ActionRegistry([
        action("set_cover", RULESET_DOMAIN),
        action("list_games", "games", effect="read", mutation=False),
        action("invoke_plugin_tool", "plugins"),
        action("set_obscurement", "ruleset-core-legacy"),
    ])


# --------------------------------------------------------------- 发现面


def test_a_ruleset_tool_is_invisible_outside_its_scope():
    scoped = _registry().search("", "", visible_domains=frozenset())

    assert {row["name"] for row in scoped["capabilities"]} == {
        "list_games", "invoke_plugin_tool",
    }
    # 统计必须与列表同源：只藏列表会把"存在哪些域、各有多少"照样报出去。
    assert scoped["total"] == 2
    assert {row["domain"] for row in scoped["domains"]} == {"games", "plugins"}


def test_a_scope_reveals_only_its_own_rule_and_never_hides_globals():
    scoped = _registry().search("", "", visible_domains=frozenset({RULESET_DOMAIN}))

    assert {row["name"] for row in scoped["capabilities"]} == {
        "set_cover", "list_games", "invoke_plugin_tool",
    }
    # 另一条规则的域仍然不可见。
    assert "set_obscurement" not in {row["name"] for row in scoped["capabilities"]}


def test_unscoped_callers_keep_every_domain():
    unscoped = _registry().search("")

    assert unscoped["total"] == 4
    assert {"ruleset-core-dnd2024", "ruleset-core-legacy"} <= {
        row["domain"] for row in unscoped["domains"]
    }


def test_describe_treats_an_invisible_tool_as_absent():
    registry = _registry()

    with pytest.raises(ValueError):
        registry.describe("set_cover", visible_domains=frozenset())
    with pytest.raises(ValueError):
        registry.describe("set_obscurement", visible_domains=frozenset({RULESET_DOMAIN}))

    assert registry.describe(
        "set_cover", visible_domains=frozenset({RULESET_DOMAIN}))["name"] == "set_cover"


def test_visible_domain_never_filters_a_product_domain():
    assert visible_domain("games", frozenset())
    assert visible_domain("plugins", frozenset())
    assert not visible_domain(RULESET_DOMAIN, frozenset())
    assert visible_domain(RULESET_DOMAIN, frozenset({RULESET_DOMAIN}))
    # None 是"没有作用域"，既有调用方因此一字不改。
    assert visible_domain(RULESET_DOMAIN, None)


# --------------------------------------------------------------- 授权面


def test_a_rule_domain_must_be_explicitly_granted():
    grant = make_authorization("task", {}, ["games"], 4, 100)

    assert not permits(grant, action("set_cover", RULESET_DOMAIN), {})
    explicit = make_authorization("task", {}, [RULESET_DOMAIN], 4, 100)
    assert permits(explicit, action("set_cover", RULESET_DOMAIN), {})
    assert not permits(grant, action("edit_lorebook", "lorebook"), {})


def test_an_empty_domain_list_still_means_every_domain():
    grant = make_authorization("task", {}, [], 4, 100)

    assert permits(grant, action("set_cover", RULESET_DOMAIN), {})
    assert permits(grant, action("edit_lorebook", "lorebook"), {})


# --------------------------------------------------------------- 对局核验


class _Registry:
    def __init__(self, instance):
        self.instance = instance

    def get(self, _key):
        return self.instance


class _Runtime:
    runtime_id = "core:dnd2024"


class _ScopeHost:
    """只带解析所需依赖的最小宿主；测试直接调用未绑定的 WebAPI 方法。"""

    _ruleset_agent_domains = {"core:dnd2024": RULESET_DOMAIN}

    def __init__(self, instance):
        self._reg = _Registry(instance)

    def _load_runtime_for_game(self, _instance):
        return _Runtime()


def _instance(gm_uid: str = "gm", players: tuple[str, ...] = ("p1",)):
    return SimpleNamespace(gm_uid=gm_uid, players={uid: {} for uid in players})


def _resolve(instance, owner: str, context: dict):
    return WebAPI._visible_ruleset_domains(_ScopeHost(instance), owner, context)


def test_the_gm_and_every_player_resolve_the_ruleset_scope():
    instance = _instance(players=("p1", "p2"))

    assert _resolve(instance, "gm", {"game_key": GAME_KEY}) == frozenset({RULESET_DOMAIN})
    assert _resolve(instance, "p1", {"game_key": GAME_KEY}) == frozenset({RULESET_DOMAIN})
    assert _resolve(instance, "p2", {"game_key": GAME_KEY}) == frozenset({RULESET_DOMAIN})


def test_an_owner_outside_the_game_gets_no_ruleset_scope():
    assert _resolve(_instance(), "stranger", {"game_key": GAME_KEY}) == frozenset()


def test_no_game_key_means_global_capabilities_only():
    assert _resolve(_instance(), "gm", {}) == frozenset()
    assert _resolve(_instance(), "gm", {"game_key": "   "}) == frozenset()


def test_an_unknown_game_gets_no_ruleset_scope():
    class _Missing(_ScopeHost):
        def __init__(self):
            super().__init__(None)

    assert WebAPI._visible_ruleset_domains(
        _Missing(), "gm", {"game_key": GAME_KEY}) == frozenset()


# --------------------------------------------------------------- 工具接线


def test_the_source_and_domain_fit_the_registry_contract():
    # runtime id 带冒号，source/domain 只允许 [a-z0-9_-]：名字必须规范化，
    # 归属由宿主记账而不是从名字反解。
    assert ruleset_source_name("core:dnd2024") == "ruleset_core_dnd2024"
    assert ruleset_domain("core:dnd2024") == RULESET_DOMAIN
    ActionRegistry().register_source(ruleset_source_name("core:dnd2024"), lambda: ())


def test_long_runtime_ids_keep_distinct_registry_identifiers():
    first = "core:" + "a" * 60
    second = "core:" + "a" * 59 + "b"

    assert ruleset_domain(first) != ruleset_domain(second)
    assert ruleset_source_name(first) != ruleset_source_name(second)


def _tool(deps: RulesetToolDependencies, runtime_id: str = "core:dnd2024"):
    tools = ruleset_tools(deps, runtime_id)
    assert len(tools) == 1
    return tools[0]


def _deps(game, viewer, captured: dict) -> RulesetToolDependencies:
    async def submit(game_key, uid, is_gm, intent):
        captured.update(game_key=game_key, uid=uid, is_gm=is_gm, intent=intent)
        return {"ok": True}

    return RulesetToolDependencies(
        get_game=lambda _key: game,
        viewer_for=lambda _instance, _owner: viewer,
        submit_ruleset_intent=submit,
    )


def _battle_game(status: str = "active"):
    return SimpleNamespace(
        run_id="run-1",
        players={"p1": {"character_name": "Alice"}},
        modules={"ruleset_runtime": {
            "version": 7,
            "combat": {"status": status, "enemies": {"goblin": {"name": "Goblin"}}},
        }},
    )


def test_set_cover_turns_one_call_into_the_table_intent():
    captured: dict = {}
    tool = _tool(_deps(_battle_game(), SimpleNamespace(uid="gm", is_gm=True), captured))

    result = asyncio.run(tool.handler({
        "game_key": GAME_KEY, "run_id": "run-1",
        "target_id": "enemy:goblin", "level": "half",
    }, "gm"))

    assert result == {"ok": True}
    intent = captured["intent"]
    assert intent["type"] == "combat.set_cover"
    assert intent["expected_version"] == 7
    assert intent["target_id"] == "enemy:goblin"
    assert intent["level"] == "half"
    assert intent["intent_id"]
    assert captured["uid"] == "gm" and captured["is_gm"] is True


def test_set_cover_refuses_a_stale_run():
    captured: dict = {}
    tool = _tool(_deps(_battle_game(), SimpleNamespace(uid="gm", is_gm=True), captured))

    with pytest.raises(ValueError):
        asyncio.run(tool.handler({
            "game_key": GAME_KEY, "run_id": "run-0",
            "target_id": "enemy:goblin", "level": "half",
        }, "gm"))
    assert captured == {}


def test_an_unknown_target_comes_back_with_the_current_list():
    captured: dict = {}
    tool = _tool(_deps(_battle_game(), SimpleNamespace(uid="gm", is_gm=True), captured))

    result = asyncio.run(tool.handler({
        "game_key": GAME_KEY, "run_id": "run-1",
        "target_id": "goblin", "level": "half",
    }, "gm"))

    # 裸 id 是模型最可能先试的形状：错误里必须直接给出可填的值，
    # 否则它只能对着 "target actor does not exist" 猜第二次。
    assert result["code"] == "UNKNOWN_TARGET"
    assert [row["actor_id"] for row in result["targets"]] == [
        "player:p1", "enemy:goblin",
    ]
    assert [row["name"] for row in result["targets"]] == ["Alice", "Goblin"]
    assert captured == {}


def test_cover_is_refused_before_combat_starts():
    captured: dict = {}
    tool = _tool(_deps(_battle_game("idle"), SimpleNamespace(uid="gm", is_gm=True), captured))

    result = asyncio.run(tool.handler({
        "game_key": GAME_KEY, "run_id": "run-1",
        "target_id": "enemy:goblin", "level": "half",
    }, "gm"))

    assert result["code"] == "COMBAT_NOT_ACTIVE"
    assert captured == {}


def test_the_tool_tells_the_model_where_target_ids_come_from():
    tool = _tool(_deps(_battle_game(), SimpleNamespace(uid="gm", is_gm=True), {}))

    # 取数路径属于工具自描述：不写进系统提示词，也不能悄悄丢。
    assert "get_ruleset_available_actions" in tool.instructions
    assert "player:<uid>" in tool.instructions


# --------------------------------------------------------------- 端到端


class _RecordingPlanner:
    """按脚本出牌，并记下每一轮模型实际拿到了哪些工具、哪一段 payload。"""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.offered: list[set[str]] = []
        self.received: list[str] = []

    async def call_tools(self, system, user, **kwargs):
        self.offered.append({row["function"]["name"] for row in kwargs["tools"]})
        self.received.append(user)
        step = self.steps.pop(0)
        if step.get("tool") == "respond":
            return LLMToolResponse(
                tool_calls=[{"name": "final_result", "arguments": {"answer": step.get("answer", "")}}],
                total_tokens=0, provider_used="test")
        return LLMToolResponse(
            tool_calls=[{"name": step["tool"], "arguments": step.get("arguments", {})}],
            total_tokens=0, provider_used="test")


def _runtime(tmp_path, planner, scope, captured):
    registry = ActionRegistry()
    registry.register_source(
        ruleset_source_name("core:dnd2024"),
        lambda: ruleset_tools(
            _deps(_battle_game(), SimpleNamespace(uid="gm", is_gm=True), captured),
            "core:dnd2024",
        ),
    )
    return AgentRuntime(AgentDependencies(
        llm_client=lambda: planner,
        configuration_error=lambda _lang: None,
        tools=(),
        registry=registry,
        storage_dir=tmp_path / "agent",
        ruleset_scope=lambda _owner, _context: frozenset(scope),
    ))


async def _run(agent, session_id=""):
    events = []
    async def emit(name, payload):
        events.append((name, payload))
    await agent.run("gm", [{"role": "user", "content": "给他半掩护"}], "zh-CN",
                    {"game_key": GAME_KEY}, session_id, emit)
    return events


@pytest.mark.asyncio
async def test_a_dnd_session_offers_and_executes_set_cover(tmp_path):
    captured: dict = {}
    planner = _RecordingPlanner(
        {"tool": "set_cover", "arguments": {
            "game_key": GAME_KEY, "run_id": "run-1",
            "target_id": "enemy:goblin", "level": "half"}},
        {"tool": "respond", "answer": "done"},
    )
    agent = _runtime(tmp_path, planner, {"ruleset-core-dnd2024"}, captured)
    grant = agent.authorize("gm", "", "task", {"game_key": GAME_KEY}, [], 8)

    await _run(agent, grant["session_id"])

    # 工具真的出现在模型的函数列表里，而且调用走到了同一条权威提交路径。
    assert "set_cover" in planner.offered[0]
    assert captured["intent"]["type"] == "combat.set_cover"
    assert captured["intent"]["target_id"] == "enemy:goblin"
    assert captured["intent"]["level"] == "half"
    assert RULESET_DOMAIN in planner.received[0]


@pytest.mark.asyncio
async def test_another_ruleset_session_never_offers_set_cover(tmp_path):
    captured: dict = {}
    planner = _RecordingPlanner({"tool": "respond", "answer": "not available"})
    agent = _runtime(tmp_path, planner, set(), captured)
    # 即使授权了 task 模式、给了完整域列表，不属于本会话的规则域仍然不出现。
    grant = agent.authorize("gm", "", "task", {"game_key": GAME_KEY}, [], 8)

    await _run(agent, grant["session_id"])

    assert "set_cover" not in planner.offered[0]
    assert captured == {}
    # 不可见与不存在是同一回事：整段初始 payload（包括 capability_domains）
    # 都不能把那个规则域的名字报给模型。
    assert RULESET_DOMAIN not in planner.received[0]
