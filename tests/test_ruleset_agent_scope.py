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


def test_a_rule_domain_is_not_governed_by_the_grant_domain_list():
    grant = make_authorization("task", {}, ["games"], 4, 100)

    assert permits(grant, action("set_cover", RULESET_DOMAIN), {})
    # 非规则域维持原判定：显式列表之外仍然拒绝。
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


def test_set_cover_turns_one_call_into_the_table_intent():
    captured: dict = {}
    game = SimpleNamespace(run_id="run-1", modules={"ruleset_runtime": {"version": 7}})
    tool = _tool(_deps(game, SimpleNamespace(uid="gm", is_gm=True), captured))

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
    game = SimpleNamespace(run_id="run-1", modules={"ruleset_runtime": {"version": 7}})
    tool = _tool(_deps(game, SimpleNamespace(uid="gm", is_gm=True), captured))

    with pytest.raises(ValueError):
        asyncio.run(tool.handler({
            "game_key": GAME_KEY, "run_id": "run-0",
            "target_id": "enemy:goblin", "level": "half",
        }, "gm"))
    assert captured == {}
