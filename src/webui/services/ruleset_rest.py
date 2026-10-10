"""Stateless resolver for professional ruleset rests."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from datetime import datetime, timezone

from src.engine.modules import legacy_combat, ruleset_runtime
from src.webui.ruleset_draft_validation import validate_draft_shape
from src.webui.ruleset_rest_projection import public_rest_session, saved_rest_session

if TYPE_CHECKING:
    from src.rulesets.registry import RulesetRuntimeRegistry


@dataclass(frozen=True)
class RulesetRestDependencies:
    load_rule_by_id: Callable[[str, str], Any | None]
    ruleset_registry: "RulesetRuntimeRegistry"


@dataclass(frozen=True)
class LiveRulesetRestDependencies:
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule_for_game: Callable[[Any], Any | None]
    ruleset_registry: "RulesetRuntimeRegistry"


def resolve(
    dependencies: RulesetRestDependencies,
    rule_id: str,
    body: Any,
    language: str = "",
) -> dict[str, Any]:
    parsed = validate_draft_shape(body)
    character = parsed.get("character")
    rest = parsed.get("rest")
    hit_die_rolls = parsed.get("hit_die_rolls")
    if not isinstance(character, dict):
        raise ValueError("rest character must be a JSON object")
    if rest not in {"short", "long"}:
        raise ValueError("rest must be short or long")
    if hit_die_rolls is not None and not isinstance(hit_die_rolls, dict):
        raise ValueError("hit_die_rolls must be a JSON object")
    rule = dependencies.load_rule_by_id(rule_id, language)
    if rule is None:
        return {"ok": False, "code": "RULE_NOT_FOUND", "error": f"规则不存在: {rule_id}"}
    runtime = dependencies.ruleset_registry.resolve(rule.template)
    method = getattr(runtime, "complete_rest", None)
    if not callable(method):
        return {
            "ok": False,
            "code": "RULESET_REST_UNAVAILABLE",
            "error": "该规则尚未提供专业休息结算",
        }
    result = method(rule, character, rest, hit_die_rolls)
    return {"ok": True, "rule_id": rule.rule_id, **result}


def _failure(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message, **extra}


def _set_rest_session(instance: Any, session: dict[str, Any]) -> None:
    ruleset_runtime.require_writable(instance)
    state = ruleset_runtime.state(instance)
    if not isinstance(state, dict):
        state = {}
        ruleset_runtime.replace_state(instance, state)
    state["rest_session"] = session


def _rest_eligible_uids(instance: Any) -> list[str]:
    """Only present, conscious characters need to opt into a party rest."""

    return sorted(
        uid for uid in instance.players
        if uid not in instance.away_players
        and instance.is_alive(uid)
        and int(instance.get_character_sheet(uid).get("hp", 0) or 0) > 0
    )


def _validate_hit_dice_counts(character: dict[str, Any], requested: Any) -> dict[str, int]:
    if requested is None:
        return {}
    if not isinstance(requested, dict):
        raise ValueError("hit_dice must be a JSON object")
    canonical = character.get("ruleset_character")
    canonical = canonical if isinstance(canonical, dict) else character
    available = canonical.get("resources", {}).get("hit_dice", {})
    if not isinstance(available, dict):
        raise ValueError("character hit dice are missing")
    result: dict[str, int] = {}
    for die, raw_count in requested.items():
        if isinstance(raw_count, bool):
            raise ValueError(f"hit_dice.{die} must be a non-negative integer")
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"hit_dice.{die} must be a non-negative integer") from exc
        if count < 0 or count > int(available.get(die, -1)):
            raise ValueError(f"not enough {die} Hit Point Dice")
        try:
            sides = int(str(die).removeprefix("d"))
        except ValueError as exc:
            raise ValueError(f"invalid hit die {die}") from exc
        if sides < 2:
            raise ValueError(f"invalid hit die {die}")
        if count:
            result[str(die)] = count
    return result


def _server_hit_die_rolls(character: dict[str, Any], requested: Any) -> dict[str, list[int]]:
    if requested is None:
        return {}
    if not isinstance(requested, dict):
        raise ValueError("hit_dice must be a JSON object")
    canonical = character.get("ruleset_character")
    canonical = canonical if isinstance(canonical, dict) else character
    available = canonical.get("resources", {}).get("hit_dice", {})
    if not isinstance(available, dict):
        raise ValueError("character hit dice are missing")
    rolls: dict[str, list[int]] = {}
    for die, raw_count in requested.items():
        if isinstance(raw_count, bool):
            raise ValueError(f"hit_dice.{die} must be a non-negative integer")
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"hit_dice.{die} must be a non-negative integer") from exc
        if count < 0 or count > int(available.get(die, -1)):
            raise ValueError(f"not enough {die} Hit Point Dice")
        try:
            sides = int(str(die).removeprefix("d"))
        except ValueError as exc:
            raise ValueError(f"invalid hit die {die}") from exc
        if sides < 2:
            raise ValueError(f"invalid hit die {die}")
        rolls[str(die)] = [secrets.randbelow(sides) + 1 for _ in range(count)]
    return rolls


async def resolve_live(
    dependencies: LiveRulesetRestDependencies,
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
        return await _resolve_live_authority(dependencies, game_key, user_id, body, instance)


async def _resolve_live_authority(
    dependencies: LiveRulesetRestDependencies,
    game_key: str,
    user_id: str,
    body: Any,
    instance: Any,
) -> dict[str, Any]:
    parsed = validate_draft_shape(body)
    rest = parsed.get("rest")
    if rest not in {"short", "long"}:
        return _failure("INVALID_REST", "休息类型必须是 short 或 long")
    if "hit_die_rolls" in parsed:
        return _failure(
            "CLIENT_ROLLS_FORBIDDEN", "休息骰由服务端掷出；客户端只需提交要花费的生命骰数量",
        )
    if rest == "long" and parsed.get("hit_dice"):
        return _failure("INVALID_REST", "长休不需要花费生命骰")
    if parsed.get("confirm_elapsed_time") is not True:
        return _failure("REST_CONFIRMATION_REQUIRED", "请先确认游戏时间会随本次休息推进")
    operation_id = str(parsed.get("operation_id") or "").strip()
    if not operation_id or len(operation_id) > 160:
        return _failure("INVALID_OPERATION_ID", "休息操作必须提供有效的 operation_id")

    if user_id not in instance.players:
        return _failure("CHARACTER_NOT_FOUND", "角色不存在")
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return _failure("RULE_NOT_FOUND", "当前游戏规则不存在")
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        return _failure("RULESET_RUNTIME_UNAVAILABLE", str(exc))
    method = getattr(runtime, "complete_rest", None)
    if not callable(method):
        return _failure("RULESET_REST_UNAVAILABLE", "该规则尚未提供专业休息结算")
    context_validator = getattr(runtime, "validate_rest_context", None)
    if callable(context_validator):
        try:
            context_validator(instance, user_id, rest)
        except ValueError as exc:
            return _failure("REST_NOT_AVAILABLE", str(exc))

    character = instance.get_character_sheet(user_id)
    revision = int(character.get("ruleset_revision", 0) or 0)
    raw_log = character.get("ruleset_operation_log")
    operation_log = deepcopy(raw_log) if isinstance(raw_log, list) else []
    previous = next((
        entry for entry in operation_log
        if isinstance(entry, dict) and entry.get("operation_id") == operation_id
    ), None)
    if previous is not None:
        if previous.get("kind") != "rest":
            return _failure(
                "OPERATION_ID_REUSED", "operation_id 已被其他角色操作使用，请重试",
            )
        return {
            "ok": True, "rule_id": rule.rule_id, "game_key": game_key,
            "user_id": user_id, "revision": revision, "duplicate": True,
            "rest": previous.get("rest"), "events": deepcopy(previous.get("events") or []),
            "character": deepcopy(character),
        }
    raw_expected = parsed.get("expected_revision")
    if isinstance(raw_expected, bool):
        raw_expected = None
    try:
        expected_revision = int(raw_expected)
    except (TypeError, ValueError):
        return _failure(
            "INVALID_CHARACTER_REVISION",
            "休息操作必须提供有效的 expected_revision",
        )
    if expected_revision != revision:
        return _failure(
            "STALE_CHARACTER_REVISION", "角色已在其他位置更新，请刷新后重试",
            revision=revision,
        )
    try:
        hit_die_rolls = _server_hit_die_rolls(
            character, parsed.get("hit_dice") if rest == "short" else None,
        )
        result = method(rule, character, rest, hit_die_rolls)
    except (TypeError, ValueError) as exc:
        return _failure("INVALID_REST", str(exc))
    updated = deepcopy(character)
    updated.update(result["character"])
    updated["ruleset_revision"] = revision + 1
    operation_log.append({
        "operation_id": operation_id,
        "kind": "rest",
        "rest": rest,
        "events": deepcopy(result.get("events") or []),
        "revision": revision + 1,
    })
    updated["ruleset_operation_log"] = operation_log[-32:]
    try:
        ruleset_runtime.require_binding(instance, runtime.runtime_id)
    except ruleset_runtime.RulesetBindingError as exc:
        return _failure(exc.code, str(exc))
    before_player = deepcopy(instance.players[user_id])
    instance.set_character_sheet(user_id, updated)
    try:
        await dependencies.save_instance(instance)
    except Exception:
        instance.put_player(user_id, before_player)
        raise
    return {
        "ok": True, "rule_id": rule.rule_id, "game_key": game_key,
        "user_id": user_id, "revision": revision + 1, "duplicate": False,
        "rest": rest, "events": deepcopy(result.get("events") or []),
        "source_ref": result.get("source_ref"), "character": deepcopy(updated),
    }


async def resolve_live_party(
    dependencies: LiveRulesetRestDependencies,
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
        return await _resolve_live_party_authority(dependencies, game_key, user_id, body, instance)


async def _resolve_live_party_authority(
    dependencies: LiveRulesetRestDependencies,
    game_key: str,
    user_id: str,
    body: Any,
    instance: Any,
) -> dict[str, Any]:
    parsed = validate_draft_shape(body)
    rest = parsed.get("rest")
    if rest not in {"short", "long"}:
        return _failure("INVALID_REST", "休息类型必须是 short 或 long")
    if "hit_die_rolls" in parsed:
        return _failure("CLIENT_ROLLS_FORBIDDEN", "休息骰由服务端掷出；客户端只需提交要花费的生命骰数量")
    if rest == "long" and parsed.get("hit_dice"):
        return _failure("INVALID_REST", "长休不需要花费生命骰")
    if parsed.get("confirm_elapsed_time") is not True:
        return _failure("REST_CONFIRMATION_REQUIRED", "请先确认游戏时间会随本次休息推进")
    operation_id = str(parsed.get("operation_id") or "").strip()
    if not operation_id or len(operation_id) > 160:
        return _failure("INVALID_OPERATION_ID", "休息操作必须提供有效的 operation_id")

    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return _failure("RULE_NOT_FOUND", "当前游戏规则不存在")
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        return _failure("RULESET_RUNTIME_UNAVAILABLE", str(exc))
    method = getattr(runtime, "complete_rest", None)
    if not callable(method):
        return _failure("RULESET_REST_UNAVAILABLE", "该规则尚未提供专业休息结算")
    if legacy_combat.combat_active(instance) or legacy_combat.combat_state(instance) == "active":
        return _failure("REST_NOT_AVAILABLE", "战斗进行中不能休息；请先结束战斗")

    async with instance._lock:
        if user_id not in instance.players:
            return _failure("CHARACTER_NOT_FOUND", "角色不存在")
        character = instance.get_character_sheet(user_id)
        if int(character.get("hp", 0) or 0) < 1:
            return _failure("REST_NOT_AVAILABLE", "至少需要 1 HP 才能发起或加入休息")
        try:
            hit_dice = _validate_hit_dice_counts(
                character, parsed.get("hit_dice") if rest == "short" else {},
            )
        except ValueError as exc:
            return _failure("INVALID_REST", str(exc))

        session = saved_rest_session(instance)
        status = str(session.get("status") or "idle")
        if status in {"idle", "completed", "error"}:
            eligible_uids = _rest_eligible_uids(instance)
            if not eligible_uids:
                return _failure("REST_NOT_AVAILABLE", "当前没有可参加休息的存活角色")
            session = {
                "status": "collecting",
                "rest": rest,
                "required_uids": eligible_uids,
                "participants": {},
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        elif str(session.get("rest") or "") != rest:
            return _failure("REST_TYPE_MISMATCH", "队伍已经在准备另一种休息，请先完成或取消当前休息")

        required = {
            uid for uid in session.get("required_uids", [])
            if uid in instance.players and uid not in instance.away_players
            and instance.is_alive(uid)
            and int(instance.get_character_sheet(uid).get("hp", 0) or 0) > 0
        }
        if user_id not in required:
            return _failure("REST_NOT_AVAILABLE", "当前角色不在本次队伍休息范围内")
        ruleset_runtime.require_writable(instance)
        try:
            ruleset_runtime.require_binding(instance, runtime.runtime_id)
        except ruleset_runtime.RulesetBindingError as exc:
            return _failure(exc.code, str(exc))
        session["required_uids"] = sorted(required)
        participants = session.setdefault("participants", {})
        if not isinstance(participants, dict):
            participants = {}
            session["participants"] = participants
        previous = participants.get(user_id)
        if isinstance(previous, dict) and previous.get("operation_id") == operation_id:
            return {
                "ok": True, "pending": True, "duplicate": True,
                "rest": rest, "rest_session": public_rest_session(instance),
            }
        revision = int(character.get("ruleset_revision", 0) or 0)
        raw_expected = parsed.get("expected_revision")
        try:
            expected_revision = int(raw_expected)
        except (TypeError, ValueError):
            return _failure("INVALID_CHARACTER_REVISION", "休息操作必须提供有效的 expected_revision")
        if expected_revision != revision:
            return _failure("STALE_CHARACTER_REVISION", "角色已在其他位置更新，请刷新后重试", revision=revision)
        participants[user_id] = {
            "operation_id": operation_id,
            "expected_revision": expected_revision,
            "hit_dice": hit_dice,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        if not required.issubset(participants):
            session["status"] = "collecting"
            _set_rest_session(instance, session)
            await dependencies.save_instance(instance)
            return {
                "ok": True, "pending": True, "resolved": False, "rest": rest,
                "rest_session": public_rest_session(instance),
            }

        session["status"] = "resolving"
        _set_rest_session(instance, session)
        before_players = {uid: deepcopy(instance.players[uid]) for uid in required}
        results: dict[str, dict[str, Any]] = {}
        for uid in sorted(required):
            item = participants[uid]
            result = await resolve_live(dependencies, game_key, uid, {
                "rest": rest,
                "hit_dice": item.get("hit_dice", {}),
                "confirm_elapsed_time": True,
                "expected_revision": item.get("expected_revision"),
                "operation_id": item.get("operation_id"),
            })
            if not result.get("ok"):
                for restore_uid, player in before_players.items():
                    instance.put_player(restore_uid, player)
                session["status"] = "error"
                session["error"] = str(result.get("error") or "队伍休息结算失败")
                _set_rest_session(instance, session)
                await dependencies.save_instance(instance)
                return _failure("PARTY_REST_FAILED", session["error"], rest_session=public_rest_session(instance))
            results[uid] = result
        session["status"] = "completed"
        session["resolved_at"] = datetime.now(timezone.utc).isoformat()
        session["error"] = ""
        _set_rest_session(instance, session)
        await dependencies.save_instance(instance)
        own = results.get(user_id) or {}
        return {
            **own,
            "pending": False,
            "resolved": True,
            "party_results": [
                {"user_id": uid, "character_name": instance.players[uid].get("character_name", uid),
                 "events": deepcopy(results[uid].get("events") or [])}
                for uid in sorted(results)
            ],
            "rest_session": public_rest_session(instance),
        }
