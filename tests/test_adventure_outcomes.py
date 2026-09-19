"""Runtime outcomes 测试（ADV2-04，母方案 §26/§120）。

覆盖：三类 outcome（world_op / item_reward / progress_event）的声明校验、
intent 转换、未知类型/字段 fail closed、world op 完整校验留给唯一写入口、
reward 经 DNDMOD-03 桥。
"""

from __future__ import annotations

import pytest

from src.adventures.outcomes import (
    OUTCOME_TYPES,
    OutcomeError,
    outcomes_to_intents,
    validate_outcome,
)
from src.rulesets.dnd2024.content.catalog import catalog_from_sources
from src.rulesets.dnd2024.content.rewards import (
    RewardIntentError,
    reward_intent_from_ref,
)


SIGIL = {
    "item_id": "castle_sigil", "name": "Castle Sigil",
    "source_ref": "module:castle-module", "category": "key_item",
}


def _catalog() -> object:
    return catalog_from_sources([("module:castle-module", {"item": {"castle_sigil": SIGIL}})])


def test_outcome_vocabulary_is_the_declared_three() -> None:
    assert set(OUTCOME_TYPES) == {"world_op", "item_reward", "progress_event"}


def test_world_op_outcome_passes_payload_through() -> None:
    outcome = validate_outcome({
        "type": "world_op",
        "op": {"op": "set_fact", "key": "location:bridge.passable", "value": False},
    })
    assert outcome["op"]["op"] == "set_fact"


def test_world_op_without_payload_fails_closed() -> None:
    with pytest.raises(OutcomeError, match="requires an op payload"):
        validate_outcome({"type": "world_op"})
    # op 名缺失同样拒绝（完整校验在唯一写入口，但声明必须有 op 名）。
    with pytest.raises(OutcomeError, match="requires an op payload"):
        validate_outcome({"type": "world_op", "op": {"key": "a.b"}})


def test_unknown_outcome_type_and_fields_fail_closed() -> None:
    with pytest.raises(OutcomeError, match="not supported"):
        validate_outcome({"type": "kill_everyone"})
    with pytest.raises(OutcomeError, match="unknown field"):
        validate_outcome({
            "type": "item_reward", "ref": "item:castle_sigil", "force": True,
        })
    with pytest.raises(OutcomeError, match="invalid"):
        validate_outcome({"type": "progress_event", "kind": "world_destroyed", "id": "x"})


def _converter():
    """D&D 侧提供的转换器（生产组合由 ruleset content 包注入）。"""
    catalog = _catalog()
    return lambda ref, default_source, recipient: reward_intent_from_ref(
        catalog, ref, default_source=default_source, recipient_uid=recipient,
    )


def test_outcomes_to_intents_splits_all_three_kinds() -> None:
    intents = outcomes_to_intents(
        [
            {"type": "world_op", "op": {"op": "set_fact", "key": "location:bridge.passable", "value": False}},
            {"type": "item_reward", "ref": "item:castle_sigil", "recipient_uid": "p1"},
            {"type": "progress_event", "kind": "milestone_reached", "id": "mile_castle"},
        ],
        reward_converter=_converter(),
        default_source="module:castle-module",
        recipient_uid="p2",
    )
    assert intents["world_ops"] == [
        {"op": "set_fact", "key": "location:bridge.passable", "value": False},
    ]
    assert intents["reward_intents"][0]["name"] == "Castle Sigil"
    assert intents["reward_intents"][0]["recipient_uid"] == "p1"
    assert intents["progress_events"] == [{"kind": "milestone_reached", "id": "mile_castle"}]


def test_item_reward_requires_converter() -> None:
    with pytest.raises(OutcomeError, match="requires a reward converter"):
        outcomes_to_intents(
            [{"type": "item_reward", "ref": "item:castle_sigil"}],
            reward_converter=None,
            default_source="module:castle-module",
        )


def test_unresolvable_reward_ref_fails_closed() -> None:
    # 转换器（D&D 侧）对未解析引用抛 RewardIntentError——异常穿透，
    # 调用方据其阻断 outcome 执行。
    with pytest.raises(RewardIntentError, match="unresolved"):
        outcomes_to_intents(
            [{"type": "item_reward", "ref": "item:missing"}],
            reward_converter=_converter(),
            default_source="module:castle-module",
        )
