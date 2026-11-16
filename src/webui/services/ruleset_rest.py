"""Stateless resolver for professional ruleset rests."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from src.engine import module_hooks
from src.engine.staged_transaction import StagedGameTransaction
from src.engine.modules import game_config, progression_state
from src.engine.modules import ruleset_runtime
from src.engine.world.memory_projection import queue_world_memory
from src.engine.world_events import MAX_ADVANCE_MINUTES, advance_world_time
from src.engine.world_state import clock_to_minutes, world_clock
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
    # Optional: re-evaluate Adventure gates and process due moments after
    # logical time moves. Rest still works when a caller does not wire it.
    advance_adventure_world: Callable[[Any], Any] | None = None


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
    result = method(
        rule, character, rest, hit_die_rolls,
        **_rest_options(runtime, parsed),
    )
    return {"ok": True, "rule_id": rule.rule_id, **result}


def _failure(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message, **extra}


def _rest_option_names(runtime: Any) -> frozenset[str]:
    """The ruleset-owned rest option contract; absent means "no extras"."""

    declared = getattr(runtime, "rest_option_names", ())
    if not isinstance(declared, (frozenset, set, tuple, list)):
        return frozenset()
    return frozenset(str(name) for name in declared)


def _rest_options(runtime: Any, parsed: dict[str, Any]) -> dict[str, Any]:
    """Forward only options the runtime explicitly declares.

    D&D's ``attunement`` is not a generic rest field: a ruleset that does not
    declare it never receives the kwarg, so the generic service stays free of
    ruleset-specific parameters.
    """

    return {
        name: parsed[name]
        for name in _rest_option_names(runtime)
        if name in parsed
    }


def prepared_spell_options(
    dependencies: LiveRulesetRestDependencies,
    game_key: str,
    user_id: str,
) -> dict[str, Any]:
    """Read-only preparation candidates for one seat's rest panel.

    A ruleset that does not implement the read (or a non-caster) still answers
    with ``ok: True`` and an empty policy: "this character has nothing to
    prepare" is not an error, and the panel needs to render that state rather
    than a failure banner.
    """

    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None:
        return _failure("GAME_NOT_FOUND", "游戏不存在")
    if user_id not in instance.players:
        return _failure("CHARACTER_NOT_FOUND", "角色不存在")
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return _failure("RULE_NOT_FOUND", "当前游戏规则不存在")
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        return _failure("RULESET_RUNTIME_UNAVAILABLE", str(exc))
    method = getattr(runtime, "prepared_spell_options", None)
    if not callable(method):
        return {
            "ok": True, "spells": {
                "policy": None, "prepared_capacity": 0, "prepared_spell_refs": [],
                "spellbook_refs": [], "cantrip_capacity": 0, "cantrip_refs": [],
                "cantrips": [], "leveled_spells": [],
            },
        }
    character = instance.get_character_sheet(user_id)
    return {"ok": True, "spells": method(rule, character)}


async def submit_live(
    dependencies: LiveRulesetRestDependencies,
    game_key: str,
    user_id: str,
    body: Any,
) -> dict[str, Any]:
    """Dispatch the authoritative live rest operation for this game mode."""
    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None:
        return _failure("GAME_NOT_FOUND", "游戏不存在")
    if game_config.solo_mode(instance):
        return await resolve_live(dependencies, game_key, user_id, body)
    return await resolve_live_party(dependencies, game_key, user_id, body)


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


def _rest_tick(instance: Any) -> int:
    """Absolute logical minute of the world clock; 0 for an unset clock.

    Rests are stamped with this instead of ``datetime.now()``: the same save has
    to describe the same rest whenever, and from whatever machine, it is read.
    """

    minutes = clock_to_minutes(world_clock(getattr(instance, "world_state", None)))
    return minutes if isinstance(minutes, int) and minutes >= 0 else 0


class _RestDurationError(ValueError):
    """A runtime declared a rest duration the service cannot execute."""

    code = "REST_DURATION_INVALID"


def _rest_duration(runtime: Any, rest: str) -> int:
    """Minutes this rest consumes, as declared by the ruleset runtime.

    A ruleset without the declaration models no elapsed time, so it advances
    nothing: the rest still resolves exactly as it did before this slice.
    """

    method = getattr(runtime, "rest_duration_minutes", None)
    if not callable(method):
        return 0
    value = method(rest)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _RestDurationError("rest duration must be a non-negative integer")
    return value


def _advance_rest_time(
    dependencies: "LiveRulesetRestDependencies", instance: Any, minutes: int,
) -> None:
    """Move the world clock in bounded steps and let the rest of the world follow.

    The clock is a world op: it settles due events and processes in the same
    commit. Adventure gates and assistant memory are downstream consumers and
    would otherwise never see the time pass.
    """

    remaining = max(0, int(minutes))
    while remaining:
        step = min(remaining, MAX_ADVANCE_MINUTES)
        outcome = advance_world_time(
            instance, step,
            source_round=progression_state.round_value(instance),
        )
        if dependencies.advance_adventure_world is not None:
            dependencies.advance_adventure_world(instance)
        queue_world_memory(
            instance, outcome.get("events") or [],
            round_number=progression_state.round_value(instance),
        )
        remaining -= step


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


def read_options(
    dependencies: LiveRulesetRestDependencies,
    game_key: str,
    user_id: str,
) -> dict[str, Any]:
    """Describe what a long rest requires or may change, without starting one.

    Read-only by contract: no lock, no revision bump, no rest session.  The
    panel uses this to draw its terrain and study pickers, so it must never be
    able to change the sheet even if the client calls it repeatedly.
    """

    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None:
        return _failure("GAME_NOT_FOUND", "游戏不存在")
    if user_id not in instance.players:
        return _failure("CHARACTER_NOT_FOUND", "角色不存在")
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return _failure("RULE_NOT_FOUND", "当前游戏规则不存在")
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except (AttributeError, TypeError, ValueError) as exc:
        return _failure("RULESET_RUNTIME_UNAVAILABLE", str(exc))
    method = getattr(runtime, "rest_options", None)
    if not callable(method):
        # A ruleset that models no rest choices is not an error: the panel
        # simply draws nothing extra.
        return {
            "ok": True,
            "rest_options": {"land_terrain": None, "free_cast": []},
            "rest_session": public_rest_session(instance),
        }
    character = instance.get_character_sheet(user_id)
    try:
        options = method(rule, character)
    except ValueError as exc:
        return _failure("REST_OPTIONS_UNAVAILABLE", str(exc))
    return {
        "ok": True,
        "rest_options": options,
        "rest_session": public_rest_session(instance),
    }


async def resolve_live(
    dependencies: LiveRulesetRestDependencies,
    game_key: str,
    user_id: str,
    body: Any,
    *,
    advance_time: bool = True,
) -> dict[str, Any]:
    """Resolve one character's rest.

    ``advance_time`` is off when a party settlement calls this per participant:
    the clock moves once for the whole party, at the DM's verdict, not once per
    character.
    """

    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None:
        return _failure("GAME_NOT_FOUND", "游戏不存在")
    async with instance.authoritative_write() as entered:
        if not entered:
            return _failure("REWRITE_IN_PROGRESS", "GM 正在重写历史回合，请等待完成后重试")
        if dependencies.get_instance(instance.game_key) is not instance:
            return _failure("STALE_RUN", "对局已重开，请刷新后重试")
        return await _resolve_live_authority(
            dependencies, game_key, user_id, body, instance, advance_time=advance_time,
        )


async def _resolve_live_authority(
    dependencies: LiveRulesetRestDependencies,
    game_key: str,
    user_id: str,
    body: Any,
    instance: Any,
    *,
    advance_time: bool = True,
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
        result = method(
            rule, character, rest, hit_die_rolls,
            **_rest_options(runtime, parsed),
        )
    except (TypeError, ValueError) as exc:
        return _failure("INVALID_REST", str(exc))
    try:
        # 时长在改任何状态之前就要问清楚：非法声明不该走成「人恢复了、时间没走」。
        minutes = _rest_duration(runtime, rest)
    except _RestDurationError as exc:
        return _failure(exc.code, str(exc))
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
    before_world = deepcopy(getattr(instance, "world_state", None))
    instance.set_character_sheet(user_id, updated)
    if advance_time:
        # 单人局没有第二个人可以裁决，玩家即 GM：结算与时间推进同一次权威写。
        try:
            _advance_rest_time(dependencies, instance, minutes)
        except Exception as exc:
            instance.put_player(user_id, before_player)
            instance.world_state = before_world
            return _failure("REST_TIME_ADVANCE_FAILED", f"游戏时间推进失败：{exc}"[:200])
    try:
        await dependencies.save_instance(instance)
    except Exception:
        instance.put_player(user_id, before_player)
        instance.world_state = before_world
        raise
    return {
        "ok": True, "rule_id": rule.rule_id, "game_key": game_key,
        "user_id": user_id, "revision": revision + 1, "duplicate": False,
        "rest": rest, "events": deepcopy(result.get("events") or []),
        "source_ref": result.get("source_ref"), "character": deepcopy(updated),
        "duration_minutes": minutes if advance_time else 0,
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
    # 只有 runtime 显式声明的 option 才会进入队伍进度和结算请求。
    rest_options = deepcopy(_rest_options(runtime, parsed))
    options_validator = getattr(runtime, "validate_rest_options", None)
    if callable(options_validator):
        try:
            options_validator(instance, user_id, rest, rest_options)
        except ValueError as exc:
            return _failure("REST_NOT_AVAILABLE", str(exc))
    if module_hooks.combat_view(instance)["combat_active"] or module_hooks.combat_view(instance)["combat_state"] == "active":
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
        if status in {"idle", "completed", "error", "rejected"}:
            eligible_uids = _rest_eligible_uids(instance)
            if not eligible_uids:
                return _failure("REST_NOT_AVAILABLE", "当前没有可参加休息的存活角色")
            # Preserve a pre-ledger writer's last verdict before replacing its
            # session, so upgrading does not reopen that replay window.
            old_decision = session.get("decision")
            if isinstance(old_decision, dict) and old_decision.get("operation_id"):
                ruleset_runtime.require_writable(instance)
                receipts = ruleset_runtime.state(instance).setdefault("rest_decision_receipts", [])
                if not any(row.get("operation_id") == old_decision["operation_id"] for row in receipts):
                    receipts.append({**deepcopy(old_decision), "session_id": session.get("session_id")})
            session = {
                "status": "collecting",
                "session_id": secrets.token_hex(16),
                "rest": rest,
                "required_uids": eligible_uids,
                "participants": {},
                "started_tick": _rest_tick(instance),
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
            **rest_options,
            "submitted_tick": _rest_tick(instance),
        }
        if not required.issubset(participants):
            session["status"] = "collecting"
            _set_rest_session(instance, session)
            await dependencies.save_instance(instance)
            return {
                "ok": True, "pending": True, "resolved": False, "rest": rest,
                "rest_session": public_rest_session(instance),
            }

        # 全员申报齐了不等于立刻结算：长休/短休占用的游戏时间由 DM 裁决，
        # 裁决期间 DM 可以描述时间推进、插入遭遇或直接拒绝这次休息。
        try:
            # 时长在这里就算好并写进会话：DM 的裁决界面与 AI GM 的预览都要
            # 显示出「批准会推进多久」，不能让客户端自己猜。
            minutes = _rest_duration(runtime, rest)
        except _RestDurationError as exc:
            return _failure(exc.code, str(exc))
        session["status"] = "awaiting_approval"
        session["awaiting_since_tick"] = _rest_tick(instance)
        session["duration_minutes"] = minutes
        session["decision"] = {}
        _set_rest_session(instance, session)
        await dependencies.save_instance(instance)
        return {
            "ok": True, "pending": True, "resolved": False, "rest": rest,
            "awaiting_approval": True,
            "rest_session": public_rest_session(instance),
        }


async def decide_live(
    dependencies: LiveRulesetRestDependencies,
    game_key: str,
    user_id: str,
    is_gm: bool,
    body: Any,
) -> dict[str, Any]:
    """DM verdict on a fully submitted party rest.

    Approval is where game time actually moves: the resolution of every
    participant and the clock advance commit together, so a failure in either
    leaves the whole party untouched.
    """

    instance = dependencies.get_instance(dependencies.parse_game_key(game_key))
    if instance is None:
        return _failure("GAME_NOT_FOUND", "游戏不存在")
    async with instance.authoritative_write() as entered:
        if not entered:
            return _failure("REWRITE_IN_PROGRESS", "GM 正在重写历史回合，请等待完成后重试")
        if dependencies.get_instance(instance.game_key) is not instance:
            return _failure("STALE_RUN", "对局已重开，请刷新后重试")
        return await _decide_authority(dependencies, instance, game_key, user_id, is_gm, body)


async def _decide_authority(
    dependencies: LiveRulesetRestDependencies,
    instance: Any,
    game_key: str,
    user_id: str,
    is_gm: bool,
    body: Any,
) -> dict[str, Any]:
    # Plan the whole party on an isolated aggregate. Per-character saves must
    # never publish a partially recovered party or a consumed verdict.
    async with instance._lock:
        transaction = StagedGameTransaction(instance)
        staged = transaction.staged

        async def defer_save(_instance: Any) -> None:
            pass

        staged_dependencies = replace(
            dependencies, get_instance=lambda _key: staged, save_instance=defer_save,
        )
        result = await _plan_decision(
            staged_dependencies, staged, game_key, user_id, is_gm, body,
        )
        if result.get("duplicate") or transaction.matches(staged):
            return result
        session = saved_rest_session(staged)
        if not result.get("ok"):
            # A failed time hook may also have changed module-owned outboxes
            # or adventure state. Publish only the failure session, not those
            # effects from the abandoned plan.
            staged.replace_persisted_state_from(transaction.before)
            _set_rest_session(staged, session)
        if session.get("decision", {}).get("operation_id"):
            state = ruleset_runtime.state(staged)
            state.setdefault("rest_decision_receipts", []).append({
                **deepcopy(session["decision"]),
                "session_id": session.get("session_id"),
            })
        transaction.publish(instance)
        try:
            await dependencies.save_instance(instance)
        except BaseException:
            transaction.rollback(instance)
            raise
        return result


async def _plan_decision(
    dependencies: LiveRulesetRestDependencies,
    instance: Any,
    game_key: str,
    user_id: str,
    is_gm: bool,
    body: Any,
) -> dict[str, Any]:
    parsed = validate_draft_shape(body)
    if "approve" not in parsed or not isinstance(parsed.get("approve"), bool):
        return _failure("INVALID_DECISION", "裁决必须明确 approve 为 true 或 false")
    approve = bool(parsed["approve"])
    operation_id = str(parsed.get("operation_id") or "").strip()
    if not operation_id or len(operation_id) > 160:
        return _failure("INVALID_OPERATION_ID", "裁决必须提供有效的 operation_id")
    if not is_gm:
        return _failure("REST_DECISION_FORBIDDEN", "只有 DM 能裁决队伍休息")

    async with instance._lock:
        session = saved_rest_session(instance)
        # 幂等要先于状态检查：同一个 operation_id 重放时会话早已离开
        # awaiting_approval，若先判状态就会把一次成功的裁决报成失败。
        decision = session.get("decision")
        decision = deepcopy(decision) if isinstance(decision, dict) else {}
        receipts = ruleset_runtime.state(instance).get("rest_decision_receipts", [])
        if decision.get("operation_id") == operation_id or any(
            entry.get("operation_id") == operation_id
            for entry in receipts if isinstance(entry, dict)
        ):
            return {
                "ok": True, "duplicate": True, "rest_session": public_rest_session(instance),
            }
        if str(session.get("status") or "") != "awaiting_approval":
            return _failure("REST_NOT_AWAITING_APPROVAL", "当前没有等待裁决的队伍休息")
        rest = str(session.get("rest") or "")
        if rest not in {"short", "long"}:
            return _failure("INVALID_REST", "休息类型必须是 short 或 long")
        rule = dependencies.load_rule_for_game(instance)
        if rule is None:
            return _failure("RULE_NOT_FOUND", "当前游戏规则不存在")
        try:
            runtime = dependencies.ruleset_registry.resolve(rule.template)
        except (AttributeError, TypeError, ValueError) as exc:
            return _failure("RULESET_RUNTIME_UNAVAILABLE", str(exc))
        try:
            ruleset_runtime.require_writable(instance)
            ruleset_runtime.require_binding(instance, runtime.runtime_id)
        except ruleset_runtime.RulesetBindingError as exc:
            return _failure(exc.code, str(exc))
        decided_at = _rest_tick(instance)
        session["decision"] = {
            "operation_id": operation_id,
            "approve": approve,
            "by": user_id,
            "tick": decided_at,
        }
        if not approve:
            session["status"] = "rejected"
            session.pop("awaiting_since_tick", None)
            _set_rest_session(instance, session)
            await dependencies.save_instance(instance)
            return {
                "ok": True, "rejected": True, "rest_session": public_rest_session(instance),
            }
        required = {
            str(uid) for uid in session.get("required_uids", [])
            if str(uid) in instance.players
        }
        participants = session.get("participants")
        participants = participants if isinstance(participants, dict) else {}
        if not required or not required.issubset(participants):
            return _failure("REST_NOT_AWAITING_APPROVAL", "队伍申报不完整，无法结算")
        try:
            minutes = _rest_duration(runtime, rest)
        except _RestDurationError as exc:
            return _failure(exc.code, str(exc))
        session["status"] = "resolving"
        _set_rest_session(instance, session)
        return await _settle_party(
            dependencies, instance, game_key, user_id, session, rest,
            required, participants, runtime, minutes,
        )


async def _settle_party(
    dependencies: LiveRulesetRestDependencies,
    instance: Any,
    game_key: str,
    user_id: str,
    session: dict[str, Any],
    rest: str,
    required: set[str],
    participants: dict[str, Any],
    runtime: Any,
    minutes: int,
) -> dict[str, Any]:
    """Resolve every participant, then move the clock, or roll both back."""

    before_players = {uid: deepcopy(instance.players[uid]) for uid in required}
    before_world = deepcopy(getattr(instance, "world_state", None))
    results: dict[str, dict[str, Any]] = {}
    for uid in sorted(required):
        item = participants[uid]
        result = await resolve_live(dependencies, game_key, uid, {
            "rest": rest,
            "hit_dice": item.get("hit_dice", {}),
            **_rest_options(runtime, item),
            "confirm_elapsed_time": True,
            "expected_revision": item.get("expected_revision"),
            "operation_id": item.get("operation_id"),
        }, advance_time=False)
        if not result.get("ok"):
            for restore_uid, player in before_players.items():
                instance.put_player(restore_uid, player)
            instance.world_state = before_world
            session["status"] = "error"
            session["error"] = str(result.get("error") or "队伍休息结算失败")
            _set_rest_session(instance, session)
            await dependencies.save_instance(instance)
            return _failure("PARTY_REST_FAILED", session["error"], rest_session=public_rest_session(instance))
        results[uid] = result
    try:
        _advance_rest_time(dependencies, instance, minutes)
    except Exception as exc:
        # 角色恢复与时间推进是同一次权威写：时钟走不动就整队回滚，
        # 不允许出现「人恢复了但时间没走」的半结算。
        for restore_uid, player in before_players.items():
            instance.put_player(restore_uid, player)
        instance.world_state = before_world
        session["status"] = "error"
        session["error"] = f"游戏时间推进失败：{exc}"[:200]
        _set_rest_session(instance, session)
        await dependencies.save_instance(instance)
        return _failure("PARTY_REST_FAILED", session["error"], rest_session=public_rest_session(instance))
    session["status"] = "completed"
    session["resolved_tick"] = _rest_tick(instance)
    session["duration_minutes"] = minutes
    session.pop("awaiting_since_tick", None)
    session["error"] = ""
    _set_rest_session(instance, session)
    await dependencies.save_instance(instance)
    own = results.get(user_id) or {}
    return {
        **own,
        "ok": True,
        "pending": False,
        "resolved": True,
        "duration_minutes": minutes,
        "party_results": [
            {"user_id": uid, "character_name": instance.players[uid].get("character_name", uid),
             "events": deepcopy(results[uid].get("events") or [])}
            for uid in sorted(results)
        ],
        "rest_session": public_rest_session(instance),
    }
