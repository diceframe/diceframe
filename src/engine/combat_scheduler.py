"""通用战斗调度器协议与最小实现（Issue 212 PR 4）。

调度器决定"现在谁能动"。协议只依赖调用方传入的 plain combat-state 映射
（actor id、速度、先攻修正、存活标志），不接触 GameInstance，也不理解
任何规则语义。Phase 1 提供：

- RoundRobinScheduler：固定顺序轮转（现有传统回合模式的行为保持）；
- InitiativeScheduler：按先攻值排序轮转（现有 D&D 方向）；
- ThresholdScheduler：ATB 行动条（gauge 累积到阈值即就绪）。

确定性：所有排序使用显式 tie-break（先达到者 → speed 降序 →
initiative_modifier 降序 → canonical actor_id 字典序），绝不依赖 dict
顺序或客户端提交顺序。ATB 只能由规则 runtime 显式声明 capability 启用；
未知 scheduler kind fail closed。

Phase 1 范围：单人或服务端权威模式。并发消费、断线重连、暂停/恢复、
GM 强制、回滚恢复由调用方（PR 5）接入。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

SCHEDULER_KINDS = frozenset({"round_robin", "initiative", "threshold"})
_CONSUME_POLICIES = frozenset({"reset", "carry"})
_OVERFLOW_POLICIES = frozenset({"carry", "clamp"})


class SchedulerError(ValueError):
    """调度器配置或调用非法：fail closed。"""


@dataclass(frozen=True)
class SchedulerConfig:
    """调度器配置；来自规则模板的显式声明，绝不由字段猜测推导。"""

    kind: str
    threshold: int = 100
    overflow: str = "carry"
    consume: str = "reset"
    gauge_resource: str = "action_gauge"
    speed_stat: str = "action_speed"

    def __post_init__(self) -> None:
        if self.kind not in SCHEDULER_KINDS:
            raise SchedulerError(f"unknown scheduler kind: {self.kind!r}")
        if self.kind == "threshold":
            if isinstance(self.threshold, bool) or not isinstance(self.threshold, int) or self.threshold <= 0:
                raise SchedulerError("threshold must be a positive integer")
            if self.overflow not in _OVERFLOW_POLICIES:
                raise SchedulerError(f"unsupported overflow policy: {self.overflow!r}")
            if self.consume not in _CONSUME_POLICIES:
                raise SchedulerError(f"unsupported consume policy: {self.consume!r}")
            if not str(self.gauge_resource).strip() or not str(self.speed_stat).strip():
                raise SchedulerError("gauge_resource and speed_stat must be non-empty")

    @staticmethod
    def from_payload(payload: Any) -> "SchedulerConfig":
        if not isinstance(payload, Mapping):
            raise SchedulerError("scheduler config must be an object")
        return SchedulerConfig(
            kind=str(payload.get("kind") or ""),
            threshold=payload.get("threshold", 100),
            overflow=payload.get("overflow", "carry"),
            consume=payload.get("consume", "reset"),
            gauge_resource=str(payload.get("gauge") or "action_gauge"),
            speed_stat=str(payload.get("speed") or "action_speed"),
        )


@dataclass(frozen=True)
class SchedulerState:
    """可序列化调度状态（PR 5 起进入持久化投影）。"""

    kind: str
    order: tuple[str, ...] = ()
    turn_index: int = 0
    round: int = 1
    gauges: Mapping[str, int] = field(default_factory=dict)
    ready: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "order": list(self.order),
            "turn_index": self.turn_index,
            "round": self.round,
            "gauges": dict(self.gauges),
            "ready": list(self.ready),
        }

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "SchedulerState":
        if not isinstance(payload, Mapping):
            raise SchedulerError("scheduler state payload must be an object")
        kind = str(payload.get("kind") or "")
        if kind not in SCHEDULER_KINDS:
            raise SchedulerError(f"unknown scheduler kind: {kind!r}")
        return SchedulerState(
            kind=kind,
            order=tuple(str(item) for item in (payload.get("order") or [])),
            turn_index=int(payload.get("turn_index", 0) or 0),
            round=int(payload.get("round", 1) or 1),
            gauges={
                str(key): int(value)
                for key, value in (payload.get("gauges") or {}).items()
            },
            ready=tuple(str(item) for item in (payload.get("ready") or [])),
        )


@dataclass(frozen=True)
class SchedulerResult:
    """一次推进/消费的结果：新状态 + 权威事件 + 当前可行动者。"""

    state: SchedulerState
    events: tuple[dict[str, Any], ...] = ()
    ready_actor_ids: tuple[str, ...] = ()
    current_actor_id: str | None = None


@runtime_checkable
class TurnScheduler(Protocol):
    def initialize(self, combat_state: Mapping[str, Any]) -> SchedulerResult: ...

    def available_actors(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
    ) -> tuple[str, ...]: ...

    def advance(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
    ) -> SchedulerResult: ...

    def consume_turn(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
        actor_id: str,
    ) -> SchedulerResult: ...


def _actors(combat_state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    actors = combat_state.get("actors")
    if not isinstance(actors, Mapping) or not actors:
        raise SchedulerError("combat_state.actors must be a non-empty object")
    return {str(key): dict(value or {}) for key, value in actors.items()}


def _alive_ids(actors: Mapping[str, dict[str, Any]]) -> list[str]:
    return sorted(
        actor_id for actor_id, data in actors.items()
        if bool(data.get("alive", True))
    )


def _stat(actor: Mapping[str, Any], key: str) -> int:
    value = actor.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchedulerError(f"actor stat {key!r} must be an integer")
    return value


class _BaseScheduler:
    config: SchedulerConfig

    def _order_for(self, combat_state: Mapping[str, Any]) -> tuple[str, ...]:
        raise NotImplementedError

    def initialize(self, combat_state: Mapping[str, Any]) -> SchedulerResult:
        actors = _actors(combat_state)
        alive = _alive_ids(actors)
        if not alive:
            raise SchedulerError("no living actors to schedule")
        order = self._order_for(combat_state)
        state = SchedulerState(kind=self.config.kind, order=order)
        return SchedulerResult(
            state=state,
            events=({"type": "combat.scheduler.initialized", "scheduler": self.config.kind,
                     "order": list(order)},),
            ready_actor_ids=(order[0],),
            current_actor_id=order[0],
        )

    def available_actors(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
    ) -> tuple[str, ...]:
        actors = _actors(combat_state)
        if not state.order or state.turn_index >= len(state.order):
            return ()
        actor_id = state.order[state.turn_index]
        return (actor_id,) if actor_id in _alive_ids(actors) else ()


class RoundRobinScheduler(_BaseScheduler):
    """固定顺序轮转：保持传统回合模式的既有行为。"""

    config: SchedulerConfig

    def __init__(self, config: SchedulerConfig) -> None:
        if config.kind != "round_robin":
            raise SchedulerError("round_robin scheduler requires kind=round_robin")
        self.config = config

    def _order_for(self, combat_state: Mapping[str, Any]) -> tuple[str, ...]:
        declared = combat_state.get("order")
        if isinstance(declared, Sequence) and not isinstance(declared, str):
            order = tuple(str(item) for item in declared)
            actors = _actors(combat_state)
            if not order or any(actor_id not in actors for actor_id in order):
                raise SchedulerError("round_robin order must reference declared actors")
            return order
        return tuple(_alive_ids(_actors(combat_state)))

    def advance(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
    ) -> SchedulerResult:
        actors = _actors(combat_state)
        alive = _alive_ids(actors)
        if not alive:
            raise SchedulerError("no living actors to schedule")
        index = state.turn_index
        for _ in range(len(state.order)):
            index = (index + 1) % len(state.order)
            if state.order[index] in alive:
                break
        wrapped = index <= state.turn_index
        next_state = replace(
            state,
            turn_index=index,
            round=state.round + (1 if wrapped and index != state.turn_index else 0),
        )
        return SchedulerResult(
            state=next_state,
            events=({"type": "combat.scheduler.advanced", "scheduler": "round_robin",
                     "actor_id": next_state.order[index], "round": next_state.round},),
            ready_actor_ids=(next_state.order[index],),
            current_actor_id=next_state.order[index],
        )

    def consume_turn(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
        actor_id: str,
    ) -> SchedulerResult:
        if not state.order or state.order[state.turn_index] != actor_id:
            raise SchedulerError(f"actor {actor_id!r} is not the current turn holder")
        return self.advance(state, combat_state)


class InitiativeScheduler(_BaseScheduler):
    """先攻排序轮转：先攻值降序，同值按 canonical actor_id 字典序。"""

    config: SchedulerConfig

    def __init__(self, config: SchedulerConfig) -> None:
        if config.kind != "initiative":
            raise SchedulerError("initiative scheduler requires kind=initiative")
        self.config = config

    def _order_for(self, combat_state: Mapping[str, Any]) -> tuple[str, ...]:
        actors = _actors(combat_state)
        alive = _alive_ids(actors)
        return tuple(sorted(
            alive,
            key=lambda actor_id: (-_stat(actors[actor_id], "initiative"), actor_id),
        ))

    def advance(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
    ) -> SchedulerResult:
        actors = _actors(combat_state)
        alive = _alive_ids(actors)
        if not alive:
            raise SchedulerError("no living actors to schedule")
        index = state.turn_index
        for _ in range(len(state.order)):
            index = (index + 1) % len(state.order)
            if state.order[index] in alive:
                break
        wrapped = index <= state.turn_index
        next_state = replace(
            state,
            turn_index=index,
            round=state.round + (1 if wrapped and index != state.turn_index else 0),
        )
        return SchedulerResult(
            state=next_state,
            events=({"type": "combat.scheduler.advanced", "scheduler": "initiative",
                     "actor_id": next_state.order[index], "round": next_state.round},),
            ready_actor_ids=(next_state.order[index],),
            current_actor_id=next_state.order[index],
        )

    def consume_turn(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
        actor_id: str,
    ) -> SchedulerResult:
        if not state.order or state.order[state.turn_index] != actor_id:
            raise SchedulerError(f"actor {actor_id!r} is not the current turn holder")
        return self.advance(state, combat_state)


class ThresholdScheduler:
    """ATB 行动条：gauge 按 speed 累积到阈值即就绪（确定性 tie-break）。

    Phase 1 仅服务端权威推进（``advance`` 由服务器调用，客户端不能推进
    时间）。``consume``: reset = 扣除后归零；carry = 扣除阈值保留溢出。
    """

    def __init__(self, config: SchedulerConfig) -> None:
        if config.kind != "threshold":
            raise SchedulerError("threshold scheduler requires kind=threshold")
        self.config = config

    def initialize(self, combat_state: Mapping[str, Any]) -> SchedulerResult:
        actors = _actors(combat_state)
        alive = _alive_ids(actors)
        if not alive:
            raise SchedulerError("no living actors to schedule")
        for actor_id in alive:
            if _stat(actors[actor_id], "speed") <= 0:
                raise SchedulerError(
                    f"threshold scheduler requires speed > 0 for actor {actor_id!r}"
                )
        state = SchedulerState(
            kind="threshold",
            order=tuple(alive),
            gauges={actor_id: 0 for actor_id in alive},
        )
        return SchedulerResult(
            state=state,
            events=({"type": "combat.scheduler.initialized", "scheduler": "threshold",
                     "order": list(alive)},),
            ready_actor_ids=(),
            current_actor_id=None,
        )

    def available_actors(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
    ) -> tuple[str, ...]:
        actors = _actors(combat_state)
        return tuple(
            actor_id for actor_id in state.ready
            if actor_id in _alive_ids(actors)
        )

    def advance(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
    ) -> SchedulerResult:
        actors = _actors(combat_state)
        alive = _alive_ids(actors)
        pending = [
            actor_id for actor_id in alive
            if state.gauges.get(actor_id, 0) < self.config.threshold
        ]
        if not pending:
            return SchedulerResult(
                state=state, ready_actor_ids=self.available_actors(state, combat_state),
            )
        speeds = {
            actor_id: _stat(actors[actor_id], self.config.speed_stat)
            for actor_id in pending
        }
        if any(speed <= 0 for speed in speeds.values()):
            raise SchedulerError(
                "threshold scheduler requires speed > 0 for every pending actor"
            )
        # 到达阈值所需时间：ceil((threshold - gauge) / speed)，取最小者。
        time_to_ready = min(
            -(-(self.config.threshold - state.gauges.get(actor_id, 0)) // speeds[actor_id])
            for actor_id in pending
        )
        gauges = dict(state.gauges)
        for actor_id in alive:
            gauge = gauges.get(actor_id, 0)
            if gauge < self.config.threshold:
                gauge += speeds.get(actor_id, 0) * time_to_ready
                if gauge > self.config.threshold and self.config.overflow == "clamp":
                    gauge = self.config.threshold
                gauges[actor_id] = gauge
        newly_ready = [
            actor_id for actor_id in alive
            if gauges.get(actor_id, 0) >= self.config.threshold
            and actor_id not in state.ready
        ]
        newly_ready.sort(key=lambda actor_id: (
            -(gauges.get(actor_id, 0) - self.config.threshold),  # 先达到者（溢出即先到）
            -_stat(actors[actor_id], self.config.speed_stat),    # speed 较高者
            -_stat(actors[actor_id], "initiative_modifier"),     # 先攻修正较高者
            actor_id,                                            # canonical 字典序
        ))
        ready = tuple([*state.ready, *newly_ready])
        next_state = replace(state, gauges=gauges, ready=ready)
        return SchedulerResult(
            state=next_state,
            events=tuple(
                {"type": "combat.scheduler.ready", "actor_id": actor_id}
                for actor_id in newly_ready
            ),
            ready_actor_ids=self.available_actors(next_state, combat_state),
            current_actor_id=(self.available_actors(next_state, combat_state) or (None,))[0],
        )

    def consume_turn(
        self, state: SchedulerState, combat_state: Mapping[str, Any],
        actor_id: str,
    ) -> SchedulerResult:
        if actor_id not in self.available_actors(state, combat_state):
            raise SchedulerError(
                f"actor {actor_id!r} is not ready; repeated consume is rejected "
                "at the scheduler layer (intent retries stay idempotent upstream)"
            )
        gauge = state.gauges.get(actor_id, self.config.threshold)
        if self.config.consume == "carry":
            gauges = {**state.gauges, actor_id: gauge - self.config.threshold}
        else:  # reset
            gauges = {**state.gauges, actor_id: 0}
        ready = tuple(item for item in state.ready if item != actor_id)
        next_state = replace(state, gauges=gauges, ready=ready)
        return SchedulerResult(
            state=next_state,
            events=({"type": "combat.scheduler.consumed", "actor_id": actor_id},),
            ready_actor_ids=self.available_actors(next_state, combat_state),
            current_actor_id=(self.available_actors(next_state, combat_state) or (None,))[0],
        )


def scheduler_from_config(config: SchedulerConfig) -> TurnScheduler:
    """按配置构建调度器；未知 kind fail closed。"""

    if config.kind == "round_robin":
        return RoundRobinScheduler(config)
    if config.kind == "initiative":
        return InitiativeScheduler(config)
    if config.kind == "threshold":
        return ThresholdScheduler(config)
    raise SchedulerError(f"unknown scheduler kind: {config.kind!r}")
