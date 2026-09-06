"""通用战斗资源池原语的契约测试（Issue 212 PR 1）。"""

from __future__ import annotations

import pytest

from src.engine.combat_resources import (
    InsufficientResourceError,
    ResourceError,
    ResourcePool,
    commit_spend,
    pools_from_payload,
    restore_pool,
    set_pool,
    spend_pool,
)


def test_spend_subtracts_and_keeps_minimum() -> None:
    pool = ResourcePool(resource_id="mana", current=12, maximum=20)
    spent = spend_pool(pool, 5)
    assert spent.current == 7
    # 原值对象不可变：修改返回新实例。
    assert pool.current == 12

    floor = ResourcePool(resource_id="mana", current=1, minimum=1)
    with pytest.raises(InsufficientResourceError):
        spend_pool(floor, 1)


def test_restore_clamps_at_maximum() -> None:
    pool = ResourcePool(resource_id="mana", current=15, maximum=20)
    assert restore_pool(pool, 3).current == 18
    assert restore_pool(pool, 99).current == 20


def test_set_is_clamped_into_bounds() -> None:
    pool = ResourcePool(resource_id="hp", current=10, maximum=30, minimum=0)
    assert set_pool(pool, 25).current == 25
    assert set_pool(pool, 99).current == 30
    assert set_pool(pool, -5).current == 0
    with pytest.raises(ResourceError):
        set_pool(pool, "20")


def test_invalid_pool_construction_fails_closed() -> None:
    with pytest.raises(ResourceError):
        ResourcePool(resource_id="", current=1)
    with pytest.raises(ResourceError):
        ResourcePool(resource_id="mana", current=5, overflow="carry")
    with pytest.raises(ResourceError):
        ResourcePool(resource_id="mana", current=1, minimum=2)
    with pytest.raises(ResourceError):
        ResourcePool(resource_id="mana", current=25, maximum=20)
    with pytest.raises(ResourceError):
        ResourcePool(resource_id="mana", current=10, maximum=20, minimum=15)


def test_amounts_must_be_positive_integers() -> None:
    pool = ResourcePool(resource_id="mana", current=10, maximum=20)
    for bad in (0, -3, "5", True, 1.5, None):
        with pytest.raises(ResourceError):
            spend_pool(pool, bad)
        with pytest.raises(ResourceError):
            restore_pool(pool, bad)


def test_commit_spend_is_atomic_across_pools() -> None:
    pools = {
        "mana": ResourcePool(resource_id="mana", current=12, maximum=20),
        "action_gauge": ResourcePool(resource_id="action_gauge", current=100),
    }
    # 双池同时消耗成功。
    updated = commit_spend(pools, {"mana": 5, "action_gauge": 40})
    assert updated["mana"].current == 7
    assert updated["action_gauge"].current == 60
    # 原 mapping 不被修改。
    assert pools["mana"].current == 12

    # 任一不足 → 整体失败，成功的那份也不能扣。
    with pytest.raises(InsufficientResourceError):
        commit_spend(pools, {"mana": 5, "action_gauge": 999})
    assert pools["mana"].current == 12
    assert pools["action_gauge"].current == 100

    # 零消耗视为无消耗，不报错。
    assert commit_spend(pools, {"mana": 0}) == pools


def test_commit_spend_rejects_undeclared_resources() -> None:
    pools = {"mana": ResourcePool(resource_id="mana", current=12)}
    with pytest.raises(ResourceError):
        commit_spend(pools, {"qigong": 5})


def test_pools_from_payload_round_trip() -> None:
    payload = {
        "mana": {"current": 12, "maximum": 20},
        "barrier": {"current": 30, "maximum": 30, "minimum": 0},
    }
    pools = pools_from_payload(payload)
    assert pools["mana"].current == 12
    assert pools["barrier"].maximum == 30
    # 非法 payload fail closed：不会静默变成空池。
    with pytest.raises(ResourceError):
        pools_from_payload({"mana": 12})
    with pytest.raises(ResourceError):
        pools_from_payload({"mana": {"current": 99, "maximum": 20}})
    with pytest.raises(ResourceError):
        pools_from_payload("not-a-mapping")
