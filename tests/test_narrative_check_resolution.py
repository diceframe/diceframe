from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.commands.check_planner import plan_round_checks
from src.commands.dice_resolver import DiceResolver
from src.engine.game_instance import GameInstance
from src.engine.modules import checks as checks_module
from src.rules.rule_system import RuleSystem
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.narrative_checks import (
    normalize_resolved_checks,
    resolve_planned_checks,
    resolver_for,
)


class _Resolver:
    def __init__(self, answer=None, *, capabilities=None) -> None:
        self.capabilities = capabilities or RulesetCapabilities(narrative_checks=True)
        self.answer = answer
        self.calls: list[list[dict]] = []

    def resolve_narrative_checks(self, instance, requests, rng=None):  # noqa: ARG002
        self.calls.append([dict(item) for item in requests])
        return self.answer if self.answer is not None else [
            {
                "check_id": request.get("check_id"),
                "dice": "2d8",
                "rolls": [5, 3],
                "roll": 8,
                "total": 8,
                "dc": 10,
                "verdict": "失败",
            }
            for request in requests
        ]


def _instance() -> GameInstance:
    instance = GameInstance(("web", "room", "bot"))
    instance.players["p1"] = {
        "user_id": "p1",
        "character_name": "阿岚",
        "character_sheet": {"attributes": {"str": 8, "dex": 10}, "skills": []},
    }
    instance.action_queue = [{"user_id": "p1", "text": "我用肩膀撞开生锈的铁门"}]
    return instance


def _no_dice_rule() -> RuleSystem:
    return RuleSystem({
        "rule_id": "fu_probe",
        "rule_name": "探针",
        "dice_system": "none",
        "attributes": [
            {"key": "str", "name": "力量"},
            {"key": "dex", "name": "敏捷"},
        ],
        "dc_table": {"normal": 10},
    })


def test_capability_declaration_gates_the_resolver() -> None:
    """A runtime that keeps the default capabilities is never asked to roll."""

    undeclared = _Resolver(capabilities=RulesetCapabilities())
    assert resolver_for(undeclared) is None
    assert resolver_for(_Resolver()) is not None
    assert resolver_for(None) is None
    assert resolver_for(SimpleNamespace(capabilities=None)) is None


def test_results_are_paired_by_check_id_and_broken_answers_are_dropped() -> None:
    requests = [{"check_id": "a", "actor_uid": "p1"}, {"check_id": "b", "actor_uid": "p1"}]
    paired = normalize_resolved_checks(requests, [
        {"check_id": "b", "roll": 12, "verdict": "成功"},
        {"check_id": "a", "roll": 0, "verdict": "成功"},
    ])

    assert [request["check_id"] for request, _ in paired] == ["b"]


def test_results_can_arrive_without_ids_in_order() -> None:
    requests = [{"check_id": "a"}, {"check_id": "b"}]
    paired = normalize_resolved_checks(requests, [
        {"roll": 4, "verdict": "失败"},
        {"roll": 9, "verdict": "成功", "constraint": "自定义说明"},
    ])

    assert [check["verdict"] for _, check in paired] == ["失败", "成功"]
    assert paired[1][1]["constraint"] == "自定义说明"


def test_runtime_resolved_check_is_recorded_and_sent_to_the_gm() -> None:
    instance = _instance()
    action = {
        "user_id": "p1",
        "text": "我用肩膀撞开生锈的铁门",
        "check_result": {
            "check_id": "c1",
            "label": "力量+敏捷检定",
            "actor_uid": "p1",
            "actor_name": "阿岚",
            "dice": "2d8+2d10",
            "rolls": [5, 3, 6, 2],
            "roll": 16,
            "total": 16,
            "dc": 12,
            "verdict": "成功",
        },
        "dice_value": 16,
        "dice_rolls": [5, 3, 6, 2],
        "dice_system": "runtime",
    }

    block = DiceResolver().resolve_action_check(instance, action, None)

    stored = checks_module.last_check(instance)
    assert stored is not None
    assert stored["verdict"] == "成功"
    assert stored["roll"] == 16
    assert "必须遵循" in block
    assert "成功" in block
    assert "16" in block


def test_runtime_constraint_wording_is_used_verbatim() -> None:
    instance = _instance()
    action = {
        "user_id": "p1",
        "check_result": {
            "check_id": "c1",
            "roll": 16,
            "verdict": "成功",
            "constraint": "物语点检定: 2d8+2d10=16 vs DL 12，成功。",
        },
        "dice_value": 16,
    }

    block = DiceResolver().resolve_action_check(instance, action, None)

    assert "物语点检定: 2d8+2d10=16 vs DL 12，成功。" in block


@pytest.mark.asyncio
async def test_no_dice_rule_still_plans_checks_when_the_runtime_resolves_them() -> None:
    """Without the flag the whole round is skipped before the model is called."""

    instance = _instance()

    class FakeClient:
        async def call_tools(self, *_args, **_kwargs):
            return SimpleNamespace(
                tool_calls=[{
                    "name": "dice_checks",
                    "arguments": {"checks": [
                        {"player": "p1", "attribute": "str", "target": 12},
                    ]},
                }],
                total_tokens=5,
                provider_used="fake",
                native_tools=True,
            )

    planned, _metadata = await plan_round_checks(
        instance, _no_dice_rule(), FakeClient(), narrative_checks=True,
    )

    assert [request["attribute"] for _, request in planned] == ["str"]
    assert planned[0][1]["target"] == 12


@pytest.mark.asyncio
async def test_no_dice_rule_without_the_flag_keeps_the_cheap_skip() -> None:
    instance = _instance()

    class FailClient:
        async def call_tools(self, *_args, **_kwargs):
            pytest.fail("无骰规则不应调用检定模型")

    planned, metadata = await plan_round_checks(instance, _no_dice_rule(), FailClient())

    assert planned == []
    assert metadata["skipped"] == "no_dice_rule"


@pytest.mark.asyncio
async def test_round_processor_hands_free_text_checks_to_the_runtime() -> None:
    instance = _instance()
    resolver = _Resolver()
    planned = [(instance.action_queue[0], {
        "check_id": "a", "actor_uid": "p1", "attribute": "str", "target": 10,
    })]

    surviving = resolve_planned_checks(instance, resolver, planned)

    assert [request["check_id"] for _, request in surviving] == ["a"]
    assert surviving[0][1]["dice_system"] == "runtime"
    action = instance.action_queue[0]
    assert action["check_result"]["verdict"] == "失败"
    assert action["dice_value"] == 8
    assert action["dice_rolls"] == [5, 3]
    assert action["dice_roll_source"] == "runtime"


def test_only_unsettled_actions_are_sent_to_runtime_on_replan() -> None:
    instance = _instance()
    resolver = _Resolver()
    settled = instance.action_queue[0]
    first_request = {"check_id": "original", "actor_uid": "p1", "target": 10}
    resolve_planned_checks(instance, resolver, [(settled, first_request)])
    original_result = dict(settled["check_result"])
    fresh = {"user_id": "p1", "text": "another action"}
    replanned = {"check_id": "new-planner-id", "actor_uid": "p1", "target": 99}
    surviving = resolve_planned_checks(instance, resolver, [
        (settled, replanned),
        (fresh, {"check_id": "fresh", "actor_uid": "p1"}),
    ])
    assert [request["check_id"] for request in resolver.calls[-1]] == ["fresh"]
    assert surviving[0][1] == {**first_request, "dice_system": "runtime"}
    assert settled["check_result"] == original_result
    assert fresh["dice_value"] == 8


@pytest.mark.asyncio
async def test_round_processor_drops_checks_the_runtime_cannot_answer() -> None:
    instance = _instance()
    planned = [(instance.action_queue[0], {"check_id": "a", "actor_uid": "p1"})]

    class Broken:
        capabilities = RulesetCapabilities(narrative_checks=True)

        def resolve_narrative_checks(self, *_args, **_kwargs):
            raise RuntimeError("runtime gone")

    surviving = resolve_planned_checks(instance, Broken(), planned)

    assert surviving == []
    assert "check_result" not in instance.action_queue[0]
    assert "dice_value" not in instance.action_queue[0]
