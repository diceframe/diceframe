"""通用战斗资源池原语（Issue 212）。

HP、灵力、真元、护盾值、行动条都是资源池——通用层不认识它们的玩法含义，
只提供 canonical id 的读取、原子消耗与恢复。哪些 special_stat 或角色字段
会成为可消耗资源，由规则 runtime 显式声明（``combat_resources`` 能力），
通用层绝不把自由字段自动升级成资源。

规则（fail closed）：
- resource id 必须是 canonical key，未声明的引用直接失败；
- current 不得低于 minimum（不足即失败，绝不部分扣除）；
- restore 受 maximum 约束（默认 clamp）；
- 客户端提供的 current/maximum 一律无效——本模块只接受服务端状态；
- 资源变更本身不生成事件，权威事件由 effects 层在应用成功后统一生成。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

OVERFLOW_POLICIES = frozenset({"clamp"})


class ResourceError(ValueError):
    """资源池操作非法；调用方应拒绝整个 action。"""


class InsufficientResourceError(ResourceError):
    """资源不足：整个 action 失败，不产生部分扣除。"""


@dataclass(frozen=True)
class ResourcePool:
    """某个 actor 的一个资源池快照（纯值对象，修改返回新实例）。"""

    resource_id: str
    current: int
    maximum: int | None = None
    minimum: int = 0
    overflow: str = "clamp"

    def __post_init__(self) -> None:
        if not str(self.resource_id).strip():
            raise ResourceError("resource_id must be a non-empty canonical key")
        if self.overflow not in OVERFLOW_POLICIES:
            raise ResourceError(f"unsupported overflow policy: {self.overflow!r}")
        if self.minimum < 0:
            raise ResourceError("minimum must be >= 0")
        if self.maximum is not None and self.maximum < self.minimum:
            raise ResourceError("maximum must be >= minimum")
        if self.current < self.minimum:
            raise ResourceError(f"current below minimum: {self.current}")
        if self.maximum is not None and self.current > self.maximum:
            raise ResourceError(f"current above maximum: {self.current}")


def _as_positive_amount(amount: Any) -> int:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise ResourceError(f"resource amount must be a positive integer: {amount!r}")
    return amount


def spend_pool(pool: ResourcePool, amount: Any) -> ResourcePool:
    """消耗资源；不足时抛 InsufficientResourceError，不产生部分扣除。"""

    value = _as_positive_amount(amount)
    remaining = pool.current - value
    if remaining < pool.minimum:
        raise InsufficientResourceError(
            f"resource {pool.resource_id!r} insufficient: "
            f"need {value}, have {pool.current}"
        )
    return replace(pool, current=remaining)


def restore_pool(pool: ResourcePool, amount: Any) -> ResourcePool:
    """恢复资源；超过 maximum 时按 overflow 策略处理（当前仅 clamp）。"""

    value = _as_positive_amount(amount)
    restored = pool.current + value
    if pool.maximum is not None and restored > pool.maximum:
        if pool.overflow == "clamp":
            restored = pool.maximum
        else:  # pragma: no cover - OVERFLOW_POLICIES 目前只含 clamp
            raise ResourceError("unsupported overflow policy")
    return replace(pool, current=restored)


def set_pool(pool: ResourcePool, value: Any) -> ResourcePool:
    """服务端权威赋值；数值仍受 minimum/maximum 约束（clamp）。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ResourceError(f"resource value must be an integer: {value!r}")
    clamped = value
    if clamped < pool.minimum:
        clamped = pool.minimum
    if pool.maximum is not None and clamped > pool.maximum:
        clamped = pool.maximum
    return replace(pool, current=clamped)


def commit_spend(
    pools: Mapping[str, ResourcePool],
    amounts: Mapping[str, Any],
) -> dict[str, ResourcePool]:
    """原子消耗多个资源池：任一不足则整体失败，不产生部分扣除。

    ``amounts`` 为 0 的条目跳过（视为无消耗）；引用未声明的资源池直接失败。
    返回新的 pools 映射（未变化的条目复用原实例）。
    """

    prepared: dict[str, int] = {}
    for resource, raw_amount in amounts.items():
        amount = _as_positive_amount(raw_amount) if raw_amount else 0
        if amount == 0:
            continue
        pool = pools.get(resource)
        if pool is None:
            raise ResourceError(f"undeclared combat resource: {resource!r}")
        if pool.current - amount < pool.minimum:
            raise InsufficientResourceError(
                f"resource {resource!r} insufficient: need {amount}, have {pool.current}"
            )
        prepared[resource] = amount
    if not prepared:
        return dict(pools)
    updated = dict(pools)
    for resource, amount in prepared.items():
        updated[resource] = spend_pool(updated[resource], amount)
    return updated


def pools_from_payload(payload: Any) -> dict[str, ResourcePool]:
    """从服务端持久化 payload 重建资源池；客户端数据必须先经服务端校验。"""

    if not isinstance(payload, Mapping):
        raise ResourceError("combat resource pools payload must be an object")
    pools: dict[str, ResourcePool] = {}
    for resource_id, raw in payload.items():
        if not isinstance(raw, Mapping):
            raise ResourceError(f"resource pool {resource_id!r} must be an object")
        try:
            pools[str(resource_id)] = ResourcePool(
                resource_id=str(resource_id),
                current=raw.get("current", 0),
                maximum=raw.get("maximum"),
                minimum=raw.get("minimum", 0),
                overflow=raw.get("overflow", "clamp"),
            )
        except (TypeError, ValueError) as exc:
            raise ResourceError(f"invalid resource pool {resource_id!r}: {exc}") from exc
    return pools
