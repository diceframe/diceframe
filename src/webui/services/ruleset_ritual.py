"""Live resolution of a timed ritual, in the same commit as its minutes.

Magical Cunning is a 1-minute rite: the slots only come back when the minute
has actually passed.  Rather than parking the character in a waiting state, the
rite spends its own minute inside a single authoritative write -- validate,
restore, spend the use, move the clock -- and any failure rolls all four back.
That keeps "you must really spend the time" true without inventing an
interruption rule nobody wrote.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.engine import module_hooks
from src.engine.modules import ruleset_runtime
from src.webui.ruleset_draft_validation import validate_draft_shape
from src.webui.services.world_time import advance_game_time

if TYPE_CHECKING:
    from src.rulesets.registry import RulesetRuntimeRegistry


@dataclass(frozen=True)
class LiveRulesetRitualDependencies:
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule_for_game: Callable[[Any], Any | None]
    ruleset_registry: "RulesetRuntimeRegistry"
    advance_adventure_world: Callable[[Any], Any] | None = None


def _failure(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def _runtime_for(dependencies: "LiveRulesetRitualDependencies", instance: Any) -> Any:
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        raise _RitualUnavailable("RULE_NOT_FOUND", "当前游戏规则不存在")
    try:
        return dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _RitualUnavailable("RULESET_RUNTIME_UNAVAILABLE", str(exc)) from exc


class _RitualUnavailable(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_options(
    dependencies: LiveRulesetRitualDependencies, game_key: str, user_id: str,
) -> dict[str, Any]:
    """Read-only: what this character could perform right now.

    Never opens a session and never touches the sheet; a character with no
    ritual simply gets an empty list, which is what the panel draws.
    """

    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None:
        return _failure("GAME_NOT_FOUND", "游戏不存在")
    if user_id not in instance.players:
        return _failure("CHARACTER_NOT_FOUND", "角色不存在")
    try:
        runtime = _runtime_for(dependencies, instance)
    except _RitualUnavailable as exc:
        return _failure(exc.code, str(exc))
    project = getattr(runtime, "ritual_options", None)
    if not callable(project):
        return _failure("RULESET_RITUAL_UNAVAILABLE", "该规则尚未提供仪式结算")
    character = instance.get_character_sheet(user_id)
    try:
        rows = project(instance, character)
    except ValueError as exc:
        return _failure("RITUAL_OPTIONS_UNAVAILABLE", str(exc))
    return {"ok": True, "game_key": game_key, "user_id": user_id, "ritual_options": rows}


async def resolve_live(
    dependencies: LiveRulesetRitualDependencies,
    game_key: str,
    user_id: str,
    body: Any,
) -> dict[str, Any]:
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None:
        return _failure("GAME_NOT_FOUND", "游戏不存在")
    async with instance.authoritative_write() as entered:
        if not entered:
            return _failure("REWRITE_IN_PROGRESS", "GM 正在重写历史回合，请等待完成后重试")
        if dependencies.get_instance(instance.game_key) is not instance:
            return _failure("STALE_RUN", "对局已重开，请刷新后重试")
        return await _resolve_authority(dependencies, game_key, user_id, body, instance)


async def _resolve_authority(
    dependencies: LiveRulesetRitualDependencies,
    game_key: str,
    user_id: str,
    body: Any,
    instance: Any,
) -> dict[str, Any]:
    parsed = validate_draft_shape(body)
    if parsed.get("confirm_elapsed_time") is not True:
        return _failure("RITUAL_CONFIRMATION_REQUIRED", "请先确认仪式会推进游戏时间")
    operation_id = str(parsed.get("operation_id") or "").strip()
    if not operation_id or len(operation_id) > 160:
        return _failure("INVALID_OPERATION_ID", "仪式操作必须提供有效的 operation_id")
    if user_id not in instance.players:
        return _failure("CHARACTER_NOT_FOUND", "角色不存在")
    try:
        runtime = _runtime_for(dependencies, instance)
    except _RitualUnavailable as exc:
        return _failure(exc.code, str(exc))
    method = getattr(runtime, "complete_ritual", None)
    if not callable(method):
        return _failure("RULESET_RITUAL_UNAVAILABLE", "该规则尚未提供仪式结算")
    rule = dependencies.load_rule_for_game(instance)
    combat = module_hooks.combat_view(instance)
    if combat["combat_active"] or combat["combat_state"] == "active":
        return _failure("RITUAL_NOT_AVAILABLE", "战斗进行中不能举行仪式；请先结束战斗")
    try:
        ruleset_runtime.require_binding(instance, runtime.runtime_id)
    except ruleset_runtime.RulesetBindingError as exc:
        return _failure(exc.code, str(exc))

    character = instance.get_character_sheet(user_id)
    revision = int(character.get("ruleset_revision", 0) or 0)
    raw_log = character.get("ruleset_operation_log")
    operation_log = deepcopy(raw_log) if isinstance(raw_log, list) else []
    previous = next((
        entry for entry in operation_log
        if isinstance(entry, dict) and entry.get("operation_id") == operation_id
    ), None)
    if previous is not None:
        if previous.get("kind") != "ritual":
            return _failure(
                "OPERATION_ID_REUSED", "operation_id 已被其他角色操作使用，请重试",
            )
        return {
            "ok": True, "rule_id": getattr(rule, "rule_id", ""), "game_key": game_key,
            "user_id": user_id, "revision": revision, "duplicate": True,
            "ritual": previous.get("ritual"), "events": deepcopy(previous.get("events") or []),
            "character": deepcopy(character), "duration_minutes": 0,
        }
    raw_expected = parsed.get("expected_revision")
    if isinstance(raw_expected, bool):
        raw_expected = None
    try:
        expected_revision = int(raw_expected)
    except (TypeError, ValueError):
        return _failure(
            "INVALID_CHARACTER_REVISION", "仪式操作必须提供有效的 expected_revision",
        )
    if expected_revision != revision:
        return _failure(
            "STALE_CHARACTER_REVISION", "角色已在其他位置更新，请刷新后重试",
            revision=revision,
        )
    try:
        result = method(rule, character, parsed.get("resource_id") or None)
    except (TypeError, ValueError) as exc:
        return _failure("RITUAL_NOT_AVAILABLE", str(exc))
    minutes = result.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
        return _failure("RITUAL_DURATION_INVALID", "仪式时长声明非法")
    updated = deepcopy(character)
    updated.update(result["character"])
    updated["ruleset_revision"] = revision + 1
    operation_log.append({
        "operation_id": operation_id,
        "kind": "ritual",
        "ritual": result.get("ritual"),
        "events": deepcopy(result.get("events") or []),
        "revision": revision + 1,
    })
    updated["ruleset_operation_log"] = operation_log[-32:]
    try:
        ruleset_runtime.require_binding(instance, runtime.runtime_id)
    except ruleset_runtime.RulesetBindingError as exc:
        return _failure(exc.code, str(exc))
    before_player = deepcopy(instance.players[user_id])
    before_world = deepcopy(getattr(instance, "world_state", None))
    instance.set_character_sheet(user_id, updated)
    # 恢复与它花掉的那一分钟是同一次权威写：时钟走不动就整笔回滚，
    # 不允许出现「槽回来了但时间没走」的免费仪式。
    try:
        advance_game_time(
            instance, minutes,
            advance_adventure_world=dependencies.advance_adventure_world,
        )
    except Exception as exc:
        instance.put_player(user_id, before_player)
        instance.world_state = before_world
        return _failure("RITUAL_TIME_ADVANCE_FAILED", f"游戏时间推进失败：{exc}"[:200])
    try:
        await dependencies.save_instance(instance)
    except Exception:
        instance.put_player(user_id, before_player)
        instance.world_state = before_world
        raise
    return {
        "ok": True, "rule_id": getattr(rule, "rule_id", ""), "game_key": game_key,
        "user_id": user_id, "revision": revision + 1, "duplicate": False,
        "ritual": result.get("ritual"), "events": deepcopy(result.get("events") or []),
        "source_ref": result.get("source_ref"), "character": deepcopy(updated),
        "duration_minutes": minutes,
    }
