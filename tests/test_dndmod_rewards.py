"""Item / Reward intent bridge 测试（DNDMOD-03，母方案 §113 / §189）。

覆盖：item ContentRef → reward intent（名称/分类/provenance）、裸 ref 归属
默认来源、非 item 引用与未知引用 fail closed、outcome 批量解析与未知字段、
分类映射（catalog 显式类别优先）。
"""

from __future__ import annotations

import pytest

from src.rulesets.dnd2024.content.catalog import catalog_from_sources
from src.rulesets.dnd2024.content.rewards import (
    RewardIntentError,
    reward_intent_from_ref,
    reward_intents_from_outcome,
)


SIGIL = {
    "item_id": "castle_sigil", "name": "Castle Sigil",
    "source_ref": "module:castle-module", "category": "key_item",
    "description": "进入内城的信物。",
}
POTION = {
    "item_id": "healing_potion", "name": "Healing Potion",
    "source_ref": "core:srd", "category": "consumable",
}


def _catalog() -> object:
    return catalog_from_sources([
        ("module:castle-module", {"item": {"castle_sigil": SIGIL}}),
        ("core:srd", {"item": {"healing_potion": POTION}}),
    ])


def test_item_ref_resolves_to_reward_intent() -> None:
    intent = reward_intent_from_ref(
        _catalog(), "item:castle_sigil",
        default_source="module:castle-module", recipient_uid="p1",
    )
    assert intent["kind"] == "item_grant"
    assert intent["name"] == "Castle Sigil"
    assert intent["category"] == "key_item"
    assert intent["recipient_uid"] == "p1"
    assert intent["ref"] == {
        "source": "module:castle-module", "kind": "item", "id": "castle_sigil",
    }
    assert intent["category_authoritative"] is True


def test_explicit_cross_source_ref_resolves() -> None:
    intent = reward_intent_from_ref(
        _catalog(),
        {"source": "core:srd", "kind": "item", "id": "healing_potion"},
        default_source="module:castle-module", recipient_uid="p1",
    )
    assert intent["name"] == "Healing Potion"
    assert intent["category"] == "consumable"


def test_non_item_or_unknown_ref_fails_closed() -> None:
    with pytest.raises(RewardIntentError, match="must point at an item"):
        reward_intent_from_ref(
            _catalog(), "monster:goblin",
            default_source="module:castle-module", recipient_uid="p1",
        )
    with pytest.raises(RewardIntentError, match="unresolved"):
        reward_intent_from_ref(
            _catalog(), "item:does_not_exist",
            default_source="module:castle-module", recipient_uid="p1",
        )


def test_outcome_rewards_resolve_in_batch() -> None:
    intents = reward_intents_from_outcome(
        _catalog(),
        {"rewards": [
            {"ref": "item:castle_sigil", "recipient_uid": "p1"},
            {"ref": {"source": "core:srd", "kind": "item", "id": "healing_potion"}},
        ]},
        default_source="module:castle-module", recipient_uid="p2",
    )
    assert [item["recipient_uid"] for item in intents] == ["p1", "p2"]
    assert intents[0]["category"] == "key_item"


def test_outcome_without_rewards_is_empty_not_error() -> None:
    assert reward_intents_from_outcome(
        _catalog(), {}, default_source="module:castle-module", recipient_uid="p1",
    ) == []


def test_outcome_rewards_fail_closed_on_unknown_fields() -> None:
    with pytest.raises(RewardIntentError, match="unknown field"):
        reward_intents_from_outcome(
            _catalog(),
            {"rewards": [{"ref": "item:castle_sigil", "qty": 3}]},
            default_source="module:castle-module", recipient_uid="p1",
        )
