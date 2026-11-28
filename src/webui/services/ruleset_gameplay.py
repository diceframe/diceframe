"""Authenticated Web/API boundary for authoritative ruleset gameplay intents."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import secrets
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.webui.ruleset_draft_validation import validate_draft_shape
from src.adventures import binding_matches
from src.engine import module_hooks, progression
from src.engine.module_state import ModuleStateError
from src.engine.modules import (
    adventure_runtime_state,
    economy_state,
    progression_state,
    ruleset_runtime,
    session_stats,
    tactical_scene,
)
from src.tactical_scene.codec import decode_tactical_scene_document
from src.tactical_scene.contract import TacticalSceneContractError
from src.tactical_scene.ops import PLACEABLE_UPDATE, TacticalSceneOpError
from src.webui.services.tactical_scene_ops import Broadcast, _broadcast_projection
from src.engine.action_gate import (
    GateRequest, ROUND_PROCESSING, SOURCE_INTENT, STRUCTURED_INTENT_POLICY,
    check_not_judging, check_seat_exists, evaluate,
)
from src.rulesets.automation import (
    advance_automatic_intents,
    append_public_timeline_entry,
    is_public_story_milestone,
)
from src.rulesets.contracts import (
    AdventureBindingMigrationRuntime,
    AuthoritativeIntentHooks,
    AutomaticIntentRuntime,
    IntentResultProjectionRuntime,
    HostedIntentChoiceRuntime,
    SequentialActionRuntime,
    TemporaryEncounterPlannerRuntime,
)
from src.rulesets.registry import RulesetRuntimeRegistry
from src.rulesets.remote_adapter import RemoteRulesetRuntimeAdapter
from src.rulesets.sequential_actions import execute_sequential_plan
from src.llm.seat_policy import SeatRuntime
from src.webui.services import hosted_combat, adventure_commands
from src.localization.messages import t


logger = logging.getLogger("trpg")


def _RNG_UNSET() -> Any:  # pragma: no cover - identity sentinel, never called
    raise NotImplementedError


def _default_rng() -> Any:
    return random.Random(secrets.randbits(128))


async def _await_runtime_call(runtime: Any, method: str, *args: Any) -> Any:
    """把可能阻塞的外部 ruleset RPC 卸到工作线程（P1-04）。

    RemoteRulesetRuntimeAdapter 的 stdio 等待是有界的同步阻塞；直接在事件
    循环上调用会拖住所有其它请求。进程内 runtime 会就地改写 instance 共享
    状态，必须留在事件循环上执行，不得移入线程。
    """
    call = getattr(runtime, method)
    if isinstance(runtime, RemoteRulesetRuntimeAdapter):
        return await asyncio.to_thread(call, *args)
    return call(*args)


@dataclass(frozen=True)
class RulesetGameplayDependencies:
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    load_rule_for_game: Callable[[Any], Any | None]
    ruleset_registry: RulesetRuntimeRegistry
    resolve_adventure_binding: Callable[..., dict[str, Any]]
    save_instance: Callable[[Any], Awaitable[None]]
    apply_memory_delta: Callable[[str, dict[str, Any], int], Awaitable[Any]] | None
    # AI 临时遭遇等只读 LLM 提案使用；None 表示环境没有可用的 AI 服务商。
    resolve_llm_client: Callable[[], Any | None] | None = None
    # Existing authoritative-intent HTTP seam for Adventure v2 completion.
    # The callback is explicit so this service never reaches through WebAPI.
    complete_adventure_node: Callable[[Any, str, str, str], dict[str, Any]] | None = None
    request_adventure_choice: Callable[[Any, str, str, str], dict[str, Any]] | None = None
    seat_runtime: SeatRuntime | None = None
    # 组合根晚绑定的 SSE 广播器：写回成功后按 viewer 白名单广播场景投影。
    # None 表示未接线——写回照常落库，只是不广播（与 scene-03 一致）。
    resolve_broadcast: Callable[[], Any | None] | None = None
    # 服务端权威 RNG 工厂。默认每个意图从 OS 熵取 128bit 种子的 random.Random：
    # - 可序列化（getstate DTO）：外部 ruleset runtime 的 stdio 适配器必须能
    #   把 rng 传给子进程——SystemRandom 显式不支持 getstate，直接崩溃（P0）；
    # - 不可预测：种子完全来自服务端熵，客户端选择的 intent_id 无法预知骰面；
    # - 幂等重试：收据身份只含规范化的 intent payload（不含 rng 状态），
    #   同 intent 重试命中同一收据，不重算也不产生"新骰面"。
    # 需要确定性回放的部署可显式注入 rng_factory。
    rng_factory: Callable[[], Any] = _RNG_UNSET

    def __post_init__(self) -> None:
        if self.rng_factory is _RNG_UNSET:
            # 构造期才解析模块接缝：测试可以 monkeypatch _default_rng。
            object.__setattr__(self, "rng_factory", _default_rng)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message}


def _gate_error(code: str, message: str) -> dict[str, Any]:
    # Authority-gate rejections also carry ``error_code``, the field the
    # frontend localizes (``apiErrors.<code>``), like the other gated writers.
    return {**_error(code, message), "error_code": code}


def _intent_write_error(
    instance: Any, requester_id: str, requester_is_gm: bool = False,
) -> dict[str, Any] | None:
    """Check live admission under the state lock, before any transaction writes."""
    code = evaluate(
        instance,
        GateRequest(actor_uid=requester_id, source=SOURCE_INTENT, requester_is_gm=requester_is_gm),
        STRUCTURED_INTENT_POLICY,
    )
    if not code:
        return None
    return _error(
        code, t("webui.services.ruleset_gameplay.round_processing_retry_later") if code == ROUND_PROCESSING else t("webui.services.ruleset_gameplay.player_not_in_game"),
    )


def _seat_actor_id(uid: str) -> str:
    """Map a seat uid to its authoritative combat actor identity.

    ``player:<uid>`` is the actor identity convention the engine and the ruleset
    layer already share (``src/engine/checks.py`` and
    ``src/webui/services/combat_extension.py`` build the same string).  Generic
    code must not import the concrete ruleset, so this one string convention is
    kept here and used only to answer "is it this seat's turn right now?".
    """

    return f"player:{uid}"


def _active_combat(instance: Any) -> dict[str, Any] | None:
    state = ruleset_runtime.state(instance)
    combat = state.get("combat") if isinstance(state, dict) else None
    if isinstance(combat, dict) and combat.get("status") == "active":
        return combat
    return None


def current_hosted_seat(dependencies: RulesetGameplayDependencies, instance: Any) -> str | None:
    if _active_combat(instance) is None:
        return None
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return None
    runtime = dependencies.ruleset_registry.resolve(rule.template)
    if not isinstance(runtime, HostedIntentChoiceRuntime):
        return None
    pending = runtime.next_automatic_intent(instance)
    actor = str((pending or {}).get("actor_id") or "")
    return actor.removeprefix("player:") if actor.startswith("player:") else None


def _project_public_batch(
    runtime: Any, instance: Any, batch: dict[str, Any], applied: dict[str, Any],
) -> None:
    if applied.get("applied") and is_public_story_milestone(runtime, batch):
        append_public_timeline_entry(runtime, instance, batch)


def _automatic_segment(
    runtime: Any, instance: Any, rng: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The bounded server-owned automation ladder, or nothing without one."""

    if not isinstance(runtime, AutomaticIntentRuntime):
        return [], []
    return advance_automatic_intents(
        runtime, instance, rng,
        on_applied=lambda batch, applied: _project_public_batch(
            runtime, instance, batch, applied,
        ),
    )


async def _project_batch_memory(
    dependencies: RulesetGameplayDependencies,
    runtime: Any,
    instance: Any,
    batches: list[dict[str, Any]],
) -> None:
    """Best-effort long-term memory projection of already-persisted batches."""

    if not dependencies.apply_memory_delta:
        return
    memories = [
        memory
        for batch in batches
        for memory in runtime.memory_deltas_from_event_batch(batch, instance)
    ]
    for memory in memories:
        try:
            await dependencies.apply_memory_delta(
                instance.memory_namespace, {"add": [memory]},
                progression.current_round(instance),
            )
        except Exception:
            # Long-term memory is a derived projection of the persisted
            # EventBatch. A projection failure must not roll back or
            # contradict the already-saved authoritative campaign state.
            logger.exception("D&D chapter-summary memory projection failed")


def _context(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    requester_id: str,
    requester_is_gm: bool,
    *, adventure_command: bool = False,
) -> tuple[Any, Any, Any, str, dict[str, Any] | None]:
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if instance is None:
        return None, None, None, "", _error("GAME_NOT_FOUND", "游戏不存在")
    if not requester_id:
        return instance, None, None, "", _error("AUTH_REQUIRED", t("webui.services.ruleset_gameplay.game_session_required"))
    effective_requester = str(instance.gm_uid or "") if requester_is_gm else requester_id
    if not effective_requester:
        return instance, None, None, "", _error("GM_IDENTITY_MISSING", t("webui.services.ruleset_gameplay.gm_identity_missing"))
    code = evaluate(
        instance,
        GateRequest(actor_uid=requester_id, source=SOURCE_INTENT, requester_is_gm=requester_is_gm),
        # Shared by queries too. Write admission runs under the state lock,
        # after application-command authorization, to avoid a stale phase check.
        (check_seat_exists,),
    )
    if code:
        return instance, None, None, "", _error(code, t("webui.services.ruleset_gameplay.player_not_in_game"))
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        return instance, None, None, effective_requester, _error(
            "RULE_NOT_FOUND", t("webui.services.ruleset_gameplay.game_rule_missing"),
        )
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except ValueError as exc:
        return instance, rule, None, effective_requester, _error(
            "RULESET_RUNTIME_UNAVAILABLE", str(exc),
        )
    if not runtime.capabilities.authoritative_intents and not adventure_command:
        return instance, rule, runtime, effective_requester, _error(
            "RULESET_INTENTS_UNAVAILABLE", t("webui.services.ruleset_gameplay.rule_uses_free_text_rounds"),
        )
    if not adventure_command or runtime.capabilities.versioned_state:
        binding = dict(ruleset_runtime.binding(instance) or {})
        if binding.get("id") != runtime.runtime_id:
            return instance, rule, runtime, effective_requester, _error(
                "RULESET_BINDING_MISMATCH", t("webui.services.ruleset_gameplay.save_not_bound_to_runtime"),
            )
    return instance, rule, runtime, effective_requester, None


async def _response(
    dependencies: RulesetGameplayDependencies,
    rule: Any,
    runtime: Any,
    instance: Any,
    requester_id: str,
    *, requester_is_gm: bool = False, result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    view = await _await_runtime_call(runtime, "gameplay_view", instance, requester_id, requester_is_gm)
    actions = await _await_runtime_call(runtime, "available_intents", instance, requester_id)
    payload: dict[str, Any] = {
        "ok": True,
        "game_key": "|".join(instance.game_key),
        "rule_id": str(rule.rule_id),
        "ruleset_runtime": dependencies.ruleset_registry.describe(
            rule.template
        ).to_dict(),
        "gameplay": view,
        "available_actions": actions,
    }
    if result is not None:
        payload["result"] = await _viewer_result(
            runtime, instance, result, requester_id, requester_is_gm,
        )
    return payload


async def _viewer_result(
    runtime: Any, instance: Any, result: dict[str, Any],
    viewer_id: str, viewer_is_gm: bool,
) -> dict[str, Any]:
    """Let the ruleset decide what a viewer may see of a raw intent payload."""

    if isinstance(runtime, IntentResultProjectionRuntime):
        return await _await_runtime_call(
            runtime, "project_intent_result", instance, result, viewer_id, viewer_is_gm,
        )
    return result


def _restore_adventure_node_before_image(instance: Any, before: dict[str, Any]) -> None:
    """Put back world truth, Adventure progress and economy in capture order."""
    for field, value in before.items():
        if value is None:
            continue
        if field == "adventure_progress":
            adventure_runtime_state.replace_progress(instance, value)
        elif field == "economy":
            economy_state.replace_state(instance, value)
        else:
            setattr(instance, field, value)


async def _ensure_compatible_adventure_binding(
    dependencies: RulesetGameplayDependencies,
    runtime: Any,
    instance: Any,
) -> dict[str, Any] | None:
    binding = dict(getattr(instance, "adventure_binding", {}) or {})
    if not binding:
        return None
    try:
        try:
            expected = dependencies.resolve_adventure_binding(
                str(binding.get("adventure_id") or ""),
                runtime,
                str(getattr(instance, "world_id", "") or ""),
                str(getattr(instance, "language", "") or ""),
                str(binding.get("source_kind") or ""),
                str(binding.get("source_id") or ""),
            )
        except TypeError:
            # Older standalone harnesses expose the original four-argument
            # resolver.  Production composition always uses the source-aware
            # signature above; this compatibility branch cannot select a
            # source in a real WebAPI process.
            expected = dependencies.resolve_adventure_binding(
                str(binding.get("adventure_id") or ""),
                runtime,
                str(getattr(instance, "world_id", "") or ""),
                str(getattr(instance, "language", "") or ""),
            )
    except ValueError as exc:
        return _error("INCOMPATIBLE_ADVENTURE", str(exc))
    # FIX-02 §4.2：形状感知比较——旧存档的 5 字段绑定与重新解析出的来源感知
    # 绑定视为同一包；只有包身份真的变了才进入迁移/失败路径。
    if binding_matches(binding, expected):
        return None
    if not isinstance(runtime, AdventureBindingMigrationRuntime):
        return _error(
            "INCOMPATIBLE_ADVENTURE",
            "bound adventure package is missing or has changed",
        )
    migrated = runtime.migrate_adventure_binding(instance, expected)
    if migrated is None:
        return _error(
            "INCOMPATIBLE_ADVENTURE",
            "bound adventure package is missing or has changed",
        )
    if migrated:
        await dependencies.save_instance(instance)
    return None



async def plan_temporary_encounter(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    requester_id: str,
    requester_is_gm: bool = False,
) -> dict[str, Any]:
    """AI 临时遭遇提案（GM-only，只读）：生成 → GM 预览 → 确认后才 combat.start。

    本函数不改 combat、不加 state version、不写 EventBatch / adventure /
    encounter catalog；生成失败完全无副作用。
    """

    instance, rule, runtime, effective_requester, error = _context(
        dependencies, game_key, requester_id, requester_is_gm,
    )
    if error:
        return error
    if not requester_is_gm:
        return _error("GM_ONLY", t("webui.services.ruleset_gameplay.temporary_encounter_gm_only"))
    if not isinstance(runtime, TemporaryEncounterPlannerRuntime):
        return _error(
            "TEMPORARY_ENCOUNTER_UNAVAILABLE",
            t("webui.services.ruleset_gameplay.temporary_encounter_unsupported"),
        )
    async with instance._lock:
        binding_error = await _ensure_compatible_adventure_binding(
            dependencies, runtime, instance,
        )
        if binding_error:
            return binding_error
        view = runtime.gameplay_view(instance, effective_requester, True)
        view_data = view if isinstance(view, dict) else {}
        access = view_data.get("encounter_access")
        combat = view_data.get("combat")
        request = view_data.get("encounter_request")
        if isinstance(combat, dict) and combat.get("status") == "active":
            return _error("COMBAT_ACTIVE", t("webui.services.ruleset_gameplay.combat_in_progress"))
        request_pending = (
            isinstance(request, dict)
            and str(request.get("status") or "") == "pending"
        )
        # 标准自由对局没有叙事层 encounter_request；GM 从战斗工具
        # 明确点击 AI 生成时，sandbox capability 本身就是授权条件。
        # 活动冒险的未准备状态仍必须有待处理敌情，不能静默绕过剧情门槛。
        access_mode = str(access.get("mode") or "") if isinstance(access, dict) else ""
        if not request_pending and access_mode != "sandbox":
            return _error(
                "NO_PENDING_ENCOUNTER",
                t("webui.services.ruleset_gameplay.no_hostile_situation"),
            )
        if not isinstance(access, dict) or str(access.get("mode") or "") == "story":
            # 正式剧情遭遇永远优先：有绑定 preset 时绝不能用临时生成绕过。
            return _error(
                "STORY_ENCOUNTER_BOUND",
                t("webui.services.ruleset_gameplay.story_has_formal_encounter"),
            )
        requested_preset_id = (
            str(request.get("encounter_preset_id") or "")
            if isinstance(request, dict) and request_pending else ""
        )
        available_preset_ids = {
            str(item.get("id") or "")
            for item in (view_data.get("encounter_presets") or [])
            if isinstance(item, dict)
        }
        if requested_preset_id and requested_preset_id in available_preset_ids:
            return _error(
                "ENCOUNTER_ALREADY_PREPARED",
                t("webui.services.ruleset_gameplay.encounter_already_matched"),
            )
    llm_client = (
        dependencies.resolve_llm_client() if dependencies.resolve_llm_client else None
    )
    if llm_client is None:
        return _error("LLM_NOT_CONFIGURED", t("webui.services.ruleset_gameplay.no_ai_provider"))
    try:
        proposal = await runtime.plan_temporary_encounter(instance, llm_client)
    except ValueError as exc:
        return _error("TEMPORARY_ENCOUNTER_INVALID", str(exc))
    except Exception:
        logger.exception("AI temporary encounter planning failed")
        return _error(
            "LLM_REQUEST_FAILED",
            t("webui.services.ruleset_gameplay.temporary_encounter_ai_failed"),
        )
    if not isinstance(proposal, dict):
        return _error(
            "TEMPORARY_ENCOUNTER_INVALID",
            t("webui.services.ruleset_gameplay.temporary_encounter_invalid"),
        )
    return {"ok": True, "encounter": proposal}


async def available_actions(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    requester_id: str,
    requester_is_gm: bool = False,
) -> dict[str, Any]:
    instance, rule, runtime, effective_requester, error = _context(
        dependencies, game_key, requester_id, requester_is_gm,
    )
    if error:
        return error
    async with instance._lock:
        binding_error = await _ensure_compatible_adventure_binding(
            dependencies, runtime, instance,
        )
        if binding_error:
            return binding_error
        return await _response(
            dependencies, rule, runtime, instance, effective_requester,
            requester_is_gm=requester_is_gm,
        )

class _SceneBoardError(ValueError):
    """The active scene cannot supply a square grid board."""


def _token_actor_id(token: Any) -> str:
    """The DiceFrame actor named by a token's explicit reference, never by name."""

    if not isinstance(token, dict):
        return ""
    ref = token.get("actor_ref")
    if not isinstance(ref, dict):
        return ""
    raw_id = str(ref.get("id") or "")
    if raw_id.startswith(("player:", "companion:", "enemy:")):
        return raw_id
    kind = str(ref.get("kind") or "")
    if kind in {"player", "companion", "enemy"} and raw_id:
        return f"{kind}:{raw_id}"
    return ""


def _scene_grid_start(instance: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Take the bound scene's square grid snapshot for an explicit combat.start.

    The ruleset never imports the scene package; this application service hands
    the grid over as plain data.  Pixels are converted to cells here.
    """

    scene_ref = tactical_scene.active_scene_ref(instance)
    if not isinstance(scene_ref, str) or not scene_ref:
        raise _SceneBoardError("no active Tactical Scene is bound")
    raw = tactical_scene.scenes(instance).get(scene_ref)
    if not isinstance(raw, dict):
        raise _SceneBoardError("the bound Tactical Scene is missing")
    try:
        document = decode_tactical_scene_document(raw)
    except TacticalSceneContractError as exc:
        raise _SceneBoardError(f"the bound Tactical Scene is invalid: {exc}") from exc
    grid = document["grid"]
    if str(grid.get("type") or "") != "square":
        raise _SceneBoardError("the bound scene is not a square grid")
    cell_px = int(grid.get("size") or 0)
    if cell_px <= 0:
        raise _SceneBoardError("the bound scene has no usable grid size")
    dimensions = document["dimensions"]
    width = int(dimensions["width"]) // cell_px
    height = int(dimensions["height"]) // cell_px
    if width <= 0 or height <= 0:
        raise _SceneBoardError("the bound scene has no complete grid cells")
    distance = float(grid.get("distance") or 0.0)
    cell_size = int(round(distance)) if distance > 0 else 5
    walls: list[dict[str, float]] = []
    for wall in (document["placeables"].get("walls") or {}).values():
        restrictions = wall.get("restrictions") or {}
        if int(restrictions.get("move", 0) or 0) == 0:
            continue
        endpoints = wall.get("endpoints") or {}
        walls.append({
            "x0": float(endpoints["x0"]) / cell_px,
            "y0": float(endpoints["y0"]) / cell_px,
            "x1": float(endpoints["x1"]) / cell_px,
            "y1": float(endpoints["y1"]) / cell_px,
        })
    positions: dict[str, dict[str, Any]] = {}
    for token in (document["placeables"].get("tokens") or {}).values():
        actor_id = _token_actor_id(token)
        if not actor_id:
            continue
        anchor = token["position"]
        size = token.get("size") or {}
        positions[actor_id] = {
            "x": int(float(anchor["x"]) // cell_px),
            "y": int(float(anchor["y"]) // cell_px),
            "footprint": {
                "w": max(1, math.ceil(float(size.get("width") or 1))),
                "h": max(1, math.ceil(float(size.get("height") or 1))),
            },
        }
    board = {
        "id": scene_ref, "width": width, "height": height,
        "cell_size": cell_size, "distance_metric": "dnd2024", "walls": walls,
        # The revision captured here is the write-back fence: if the scene
        # moves under the combat, the final positions are not written back.
        "scene_revision": tactical_scene.revision(instance),
    }
    return board, positions


def _bind_grid_start_input(runtime: Any, instance: Any, intent: dict[str, Any]) -> dict[str, Any] | None:
    """Attach the scene-owned board; a client can never forge one."""

    if str(intent.get("type") or "") != "combat.start":
        return None
    intent.pop("board", None)
    intent.pop("spatial_positions", None)
    if str(intent.get("position_mode") or "") != "grid":
        intent.pop("position_mode", None)
        return None
    modes = getattr(getattr(runtime, "capabilities", None), "combat_spatial_modes", ())
    if "grid" not in modes:
        return _error("SPATIAL_MODE_UNAVAILABLE", "this rules runtime does not declare grid combat")
    try:
        board, positions = _scene_grid_start(instance)
    except _SceneBoardError as exc:
        return _error("TACTICAL_SCENE_BOARD_UNAVAILABLE", str(exc))
    intent["board"] = board
    intent["spatial_positions"] = positions
    return None


def _ended_grid_combat(instance: Any) -> dict[str, Any] | None:
    state = ruleset_runtime.state(instance)
    combat = state.get("combat") if isinstance(state, dict) else None
    if not isinstance(combat, dict) or str(combat.get("status") or "") != "ended":
        return None
    if str(combat.get("position_mode") or "") != "grid":
        return None
    if not isinstance(combat.get("board"), dict):
        return None
    return combat


def _scene_write_plan(instance: Any, requester_is_gm: bool) -> dict[str, Any] | None:
    """Collect the placeable updates that leaving a grid combat owes the scene.

    ``None`` means nothing to do; ``{"error": result}`` is a refusal; otherwise
    the plan is ``{"scene_id", "revision", "updates"}``.  No state is written.
    """

    if not requester_is_gm:
        return None
    combat = _ended_grid_combat(instance)
    if combat is None:
        return None
    board = combat["board"]
    scene_ref = str(board.get("id") or "")
    expected = board.get("scene_revision")
    if not scene_ref:
        return {"error": {"ok": False, "code": "tactical_scene_not_found", "scene_id": None},
                "error_code": "tactical_scene_not_found"}
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        # A grid combat started before this fence existed never captured one;
        # never guess a revision just to make the write succeed.
        return {"error": {
            "ok": False, "code": "tactical_scene_revision_unknown",
            "scene_id": scene_ref,
        }, "error_code": "tactical_scene_revision_unknown"}
    raw = tactical_scene.scenes(instance).get(scene_ref)
    if not isinstance(raw, dict):
        return {"error": {
            "ok": False, "code": "tactical_scene_not_found", "scene_id": scene_ref,
        }, "error_code": "tactical_scene_not_found"}
    try:
        document = decode_tactical_scene_document(raw)
    except TacticalSceneContractError:
        return {"error": {
            "ok": False, "code": "tactical_scene_invalid_payload", "scene_id": scene_ref,
        }, "error_code": "tactical_scene_invalid_payload"}
    cell_px = int(document["grid"].get("size") or 0)
    if cell_px <= 0:
        return {"error": {
            "ok": False, "code": "tactical_scene_invalid_payload", "scene_id": scene_ref,
        }, "error_code": "tactical_scene_invalid_payload"}
    positions = combat.get("spatial_position") or {}
    updates: list[tuple[str, dict[str, Any]]] = []
    for token_id, token in sorted((document["placeables"].get("tokens") or {}).items()):
        actor_id = _token_actor_id(token)
        position = positions.get(actor_id) if actor_id else None
        if not isinstance(position, dict):
            continue
        moved = deepcopy(token)
        current_position = moved.get("position") or {}
        new_x = int(position["x"]) * cell_px
        new_y = int(position["y"]) * cell_px
        if current_position.get("x") == new_x and current_position.get("y") == new_y:
            continue
        moved["position"] = {**current_position, "x": new_x, "y": new_y}
        updates.append((str(token_id), moved))
    return {"scene_id": scene_ref, "revision": expected, "updates": updates}


async def _apply_scene_write_plan(
    instance: Any, plan: dict[str, Any],
    save_instance: Callable[[Any], Awaitable[None]],
) -> dict[str, Any]:
    """Apply one plan atomically; the caller already holds ``instance._lock``."""

    scene_ref = plan["scene_id"]
    expected = plan["revision"]
    updates = plan["updates"]
    if not updates:
        return {"ok": True, "scene_id": scene_ref, "revision": expected, "tokens": []}
    try:
        before = tactical_scene.snapshot(instance)
    except ModuleStateError:
        return {
            "ok": False, "code": "tactical_scene_invalid_payload",
            "scene_id": scene_ref,
        }
    revision = expected
    written: list[str] = []
    try:
        for token_id, moved in updates:
            summary = tactical_scene.apply_op(
                instance,
                {
                    "kind": PLACEABLE_UPDATE, "scene_id": scene_ref,
                    "collection": "tokens", "placeable_id": token_id,
                    "placeable": moved,
                },
                expected_revision=revision,
            )
            revision = int(summary["revision"])
            written.append(token_id)
    except TacticalSceneOpError as exc:
        # Stale revision or an invalid token: write nothing at all.
        tactical_scene.restore(instance, before)
        return {"ok": False, "code": exc.code, "scene_id": scene_ref}
    try:
        await save_instance(instance)
    except asyncio.CancelledError:
        tactical_scene.restore(instance, before)
        raise
    except Exception:
        tactical_scene.restore(instance, before)
        return {"ok": False, "code": "save_failed", "scene_id": scene_ref}
    return {
        "ok": True, "scene_id": scene_ref, "revision": revision, "tokens": written,
    }


async def write_back_combat_positions(
    instance: Any, *,
    get_instance: Callable[[Any], Any | None],
    save_instance: Callable[[Any], Awaitable[None]],
    requester_is_gm: bool,
    broadcast: Broadcast | None = None,
) -> dict[str, Any] | None:
    """Write final grid positions back to the bound scene after combat ends.

    Leaving combat hands authority back to the scene.  Every token whose
    explicit actor reference names a participant is moved through the
    ``placeable_update`` operation, fenced by the slot revision captured at
    combat start: if the scene changed during the combat nothing is written
    and the conflict is reported instead of being silently overwritten.
    Returns ``None`` when there is nothing to sync.
    """

    if not requester_is_gm:
        return None
    event: dict[str, Any] | None = None
    async with instance.authoritative_write() as entered:
        if not entered:
            return {"ok": False, "code": "rewrite_in_progress"}
        current = get_instance(instance.game_key)
        if current is None or current is not instance:
            return {"ok": False, "code": "stale_run"}
        async with instance._lock:
            plan = _scene_write_plan(instance, requester_is_gm)
            if plan is None:
                return None
            if "error" in plan:
                return plan["error"]
            sync = await _apply_scene_write_plan(instance, plan, save_instance)
            event = _scene_write_event(instance, sync)
    await _emit_scene_write_broadcast(broadcast, instance, event)
    return sync


def _scene_write_event(instance: Any, sync: dict[str, Any] | None) -> dict[str, Any] | None:
    """Snapshot the committed scene projection while the caller holds the lock.

    ``None`` means nothing changed on the scene, so there is nothing to broadcast.
    """

    if not sync or not sync.get("ok") or not sync.get("tokens"):
        return None
    scene_id = str(sync.get("scene_id") or "")
    raw = tactical_scene.scenes(instance).get(scene_id)
    document: dict[str, Any] | None = None
    if isinstance(raw, dict):
        try:
            document = decode_tactical_scene_document(raw)
        except (ModuleStateError, TacticalSceneContractError):
            document = None
    return {
        "game_key": "|".join(instance.game_key),
        "scene_id": scene_id,
        "revision": int(sync["revision"]),
        "document": document,
    }


async def _emit_scene_write_broadcast(
    broadcast: Broadcast | None,
    instance: Any,
    event: dict[str, Any] | None,
) -> None:
    """Best-effort scene-03 projection after the write is durable and locks are free."""

    if broadcast is None or event is None:
        return
    summary = {
        "scene_id": event["scene_id"],
        "revision": event["revision"],
        "operation": PLACEABLE_UPDATE,
    }
    try:
        await _broadcast_projection(
            broadcast, event["game_key"], instance, summary, event["document"],
        )
    except Exception:
        # The scene write is already durable; a failed projection must not turn
        # the committed intent into an error response.
        logger.exception("Scene write-back broadcast failed")


def _scene_write_gm_author(
    instance: Any, intent: dict[str, Any], requester_is_gm: bool,
) -> bool:
    # combat.end is GM-gated by the ruleset; a GM identity is GM authority even
    # when the transport did not flag the request as an owner session.
    return requester_is_gm or (
        str(intent.get("submitted_by") or "") == str(getattr(instance, "gm_uid", "") or "")
    )


def _scene_sync_needed(
    instance: Any, intent: dict[str, Any], combat_was_active: bool,
) -> bool:
    """Explicit ``combat.end`` or a fight that ended itself inside ``apply_batch``.

    Victory / party_defeated close the combat from the ruleset's own resolution;
    the transition is only new when the combat was active before this intent.
    """

    if str(intent.get("type") or "") == "combat.end":
        return True
    return combat_was_active and _ended_grid_combat(instance) is not None


def _scene_sync_author(
    instance: Any, intent: dict[str, Any], requester_is_gm: bool, combat_was_active: bool,
) -> bool:
    """Who authorizes this write-back.

    ``combat.end`` is attributed to its GM author.  An automatic victory is
    authored by the system observing the end, so a seat's killing blow still
    releases the scene without granting that seat scene-edit authority.
    """

    if str(intent.get("type") or "") == "combat.end":
        return _scene_write_gm_author(instance, intent, requester_is_gm)
    return _scene_sync_needed(instance, intent, combat_was_active)


async def _sync_scene_after_combat_end(
    dependencies: RulesetGameplayDependencies,
    instance: Any,
    intent: dict[str, Any],
    result: dict[str, Any],
    requester_is_gm: bool,
    *,
    combat_was_active: bool = False,
) -> None:
    """Hosted path: locks are free, so reuse the full authoritative entry."""

    if not _scene_sync_needed(instance, intent, combat_was_active):
        return
    broadcast = (
        dependencies.resolve_broadcast() if dependencies.resolve_broadcast else None
    )
    try:
        sync = await write_back_combat_positions(
            instance,
            get_instance=dependencies.get_instance,
            save_instance=dependencies.save_instance,
            requester_is_gm=_scene_sync_author(
                instance, intent, requester_is_gm, combat_was_active,
            ),
            broadcast=broadcast,
        )
    except Exception:
        logger.exception("Scene write-back after combat end failed")
        sync = {"ok": False, "code": "scene_writeback_failed"}
    if sync is not None:
        result["scene_sync"] = sync


async def _sync_scene_after_combat_end_locked(
    dependencies: RulesetGameplayDependencies,
    instance: Any,
    intent: dict[str, Any],
    result: dict[str, Any],
    requester_is_gm: bool,
    *,
    combat_was_active: bool = False,
) -> dict[str, Any] | None:
    """Normal path: the caller holds ``instance._lock``; never re-acquire it.

    Returns the broadcast snapshot to emit once the caller releases the lock.
    """

    if not _scene_sync_needed(instance, intent, combat_was_active):
        return None
    try:
        plan = _scene_write_plan(
            instance,
            _scene_sync_author(instance, intent, requester_is_gm, combat_was_active),
        )
        if plan is None:
            return None
        sync = (
            plan["error"] if "error" in plan
            else await _apply_scene_write_plan(
                instance, plan, dependencies.save_instance,
            )
        )
    except Exception:
        logger.exception("Scene write-back after combat end failed")
        sync = {"ok": False, "code": "scene_writeback_failed"}
    result["scene_sync"] = sync
    return _scene_write_event(instance, sync)


async def submit_intent(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    requester_id: str,
    requester_is_gm: bool,
    body: Any,
) -> dict[str, Any]:
    is_adventure_command = isinstance(body, dict) and body.get('type') in {
        'adventure.node.complete', 'adventure.choice.request',
        'adventure.completion_suggestion.dismiss',
    }
    instance, rule, runtime, effective_requester, error = _context(
        dependencies, game_key, requester_id, requester_is_gm,
        adventure_command=is_adventure_command,
    )
    if error:
        return error
    # Adventure v2 completion is a GM-authorized application command that
    # deliberately reuses the normal authenticated intent endpoint.  It is not
    # a ruleset runtime event and therefore must be dispatched before the
    # concrete runtime validates its own intent vocabulary.
    if is_adventure_command:
        command = await adventure_commands.execute(
            adventure_commands.AdventureCommandDependencies(
                save=dependencies.save_instance,
                complete=dependencies.complete_adventure_node,
                request_choice=dependencies.request_adventure_choice,
                admission=lambda: _intent_write_error(instance, requester_id, requester_is_gm),
                binding=lambda: _ensure_compatible_adventure_binding(dependencies, runtime, instance),
                get_instance=dependencies.get_instance,
            ), instance, body, effective_requester, is_gm=requester_is_gm,
        )
        if not command["ok"]:
            return command
        return await _response(dependencies, rule, runtime, instance, effective_requester,
                         requester_is_gm=requester_is_gm, result=command["result"])

    try:
        intent = deepcopy(validate_draft_shape(body))
    except ValueError as exc:
        return _error("INVALID_INTENT_SHAPE", str(exc))
    intent["submitted_by"] = effective_requester
    if not isinstance(runtime, AuthoritativeIntentHooks):
        return _error(
            "RULESET_INTENT_HOOKS_UNAVAILABLE",
            t("webui.services.ruleset_gameplay.runtime_missing_requester_boundary"),
        )
    intent = await _await_runtime_call(
        runtime, "prepare_intent_submission", intent, effective_requester, requester_is_gm, instance,
    )
    board_error = _bind_grid_start_input(runtime, instance, intent)
    if board_error:
        return board_error
    is_sequential_action = str(intent.get("type") or "") == "sequential_action"

    if dependencies.seat_runtime is not None and isinstance(runtime, HostedIntentChoiceRuntime) and not is_sequential_action:
        async def preflight() -> dict | None:
            if dependencies.get_instance(instance.game_key) is not instance:
                return _error("GAME_CHANGED", t("webui.services.ruleset_gameplay.game_replaced_refresh"))
            admission = _intent_write_error(instance, requester_id, requester_is_gm)
            if admission:
                return admission
            progression.require_writable(instance)
            session_stats.require_writable(instance)
            return await _ensure_compatible_adventure_binding(dependencies, runtime, instance)

        combat_was_active = _active_combat(instance) is not None
        planned = await hosted_combat.plan_and_commit(
            instance, runtime, dependencies.seat_runtime, preflight=preflight,
            save=dependencies.save_instance, initial_intent=intent,
            project_batch=lambda staged, b, a: _project_public_batch(runtime, staged, b, a),
        )
        if not planned.get("ok"):
            return planned
        await _project_batch_memory(dependencies, runtime, instance, planned["batches"])
        result = planned["result"]
        await _sync_scene_after_combat_end(
            dependencies, instance, intent, result, requester_is_gm,
            combat_was_active=combat_was_active,
        )
        return await _response(dependencies, rule, runtime, instance, effective_requester,
                         requester_is_gm=requester_is_gm, result=result)

    async with instance._lock:
        admission_error = _intent_write_error(instance, requester_id, requester_is_gm)
        if admission_error:
            return admission_error
        progression.require_writable(instance)
        session_stats.require_writable(instance)
        module_hooks.run(module_hooks.SEAT_PREFLIGHT, instance)
        binding_error = await _ensure_compatible_adventure_binding(
            dependencies, runtime, instance,
        )
        if binding_error:
            return binding_error
        combat_was_active = _active_combat(instance) is not None
        before = {
            "ruleset_state": deepcopy(ruleset_runtime.state(instance)),
            "event_ledger": deepcopy(ruleset_runtime.event_ledger(instance)),
            "players": deepcopy(instance.players),
            "combat_state": module_hooks.combat_view(instance)["combat_state"],
            "combat_active": module_hooks.combat_view(instance)["combat_active"],
            "initiative_order": deepcopy(module_hooks.combat_view(instance)["initiative_order"]),
            "initiative_current": module_hooks.combat_view(instance)["initiative_current"],
            "last_activity": session_stats.last_activity(instance),
            "log": deepcopy(instance.log),
            "round_number": progression_state.round_value(instance),
        }
        # Only a first mark is undone if the transaction rolls back.
        marks_seat = (
            not requester_is_gm
            and effective_requester in instance.players
            and not module_hooks.seat_has_acted(instance, effective_requester)
        )
        try:
            rng = dependencies.rng_factory()
            batch: dict[str, Any] | None = None
            if is_sequential_action:
                if not isinstance(runtime, SequentialActionRuntime):
                    return _error(
                        "SEQUENTIAL_ACTION_UNAVAILABLE",
                        "当前规则运行时没有提供顺序动作 authority",
                    )
                if isinstance(runtime, RemoteRulesetRuntimeAdapter):
                    # 外部 adapter 的逐步骤 RPC 是有界同步阻塞（P1-04），
                    # 整个计划执行卸到工作线程；进程内实现留在事件循环上。
                    sequential = await asyncio.to_thread(
                        execute_sequential_plan, instance, runtime, intent,
                        effective_requester, requester_is_gm, rng,
                    )
                else:
                    sequential = execute_sequential_plan(
                        instance, runtime, intent, effective_requester,
                        requester_is_gm, rng,
                    )
                if not sequential.get("ok"):
                    return sequential
                resolved = sequential
                batches = [
                    batch for batch in sequential.get("resolved_event_batches") or []
                    if isinstance(batch, dict)
                ]
                applied = {"ok": True, "applied": bool(batches)}
                for batch in batches:
                    _project_public_batch(runtime, instance, batch, applied)
                batch = None
            else:
                resolved = await _await_runtime_call(runtime, "resolve_intent", instance, intent, rng)
                if not resolved.get("ok"):
                    return resolved
                batch = resolved.get("event_batch")
                if not isinstance(batch, dict):
                    return _error("INVALID_EVENT_BATCH", t("webui.services.ruleset_gameplay.runtime_no_event_batch"))
                applied = await _await_runtime_call(runtime, "apply_event_batch", instance, batch)
                _project_public_batch(runtime, instance, batch, applied)
            if marks_seat:
                # A player-side intent applied for its own seat: the seat has
                # acted, so card adoption / deletion become GM-only for it.
                module_hooks.run(
                    module_hooks.SEAT_ACTED, instance, effective_requester,
                    module_hooks.SEAT_SOURCE_INTENT,
                )
            automatic_batches, automatic_results = _automatic_segment(
                runtime, instance, rng,
            )
            session_stats.touch(instance)
            await dependencies.save_instance(instance)
        except (ValueError, KeyError, TypeError) as exc:
            instance.restore_ruleset_transaction(before)
            if marks_seat:
                module_hooks.run(module_hooks.SEAT_FORGOTTEN, instance, effective_requester)
            return _error("INTENT_REJECTED", str(exc))
        except Exception:
            instance.restore_ruleset_transaction(before)
            if marks_seat:
                module_hooks.run(module_hooks.SEAT_FORGOTTEN, instance, effective_requester)
            raise
        resolved_batches = [
            item for item in (resolved.get("resolved_event_batches") or [batch])
            if isinstance(item, dict)
        ]
        await _project_batch_memory(
            dependencies, runtime, instance, [*resolved_batches, *automatic_batches],
        )
        if is_sequential_action:
            result = {
                **resolved,
                "automatic_event_batches": automatic_batches,
                "automatic_results": automatic_results,
            }
        else:
            result = {
                **applied,
                "replayed": bool(resolved.get("replayed", False)),
                "pending_decision": deepcopy(resolved.get("pending_decision")),
                "automatic_event_batches": automatic_batches,
                "automatic_results": automatic_results,
                "resolved_event_batches": [batch, *automatic_batches],
            }
        scene_event = await _sync_scene_after_combat_end_locked(
            dependencies, instance, intent, result, requester_is_gm,
            combat_was_active=combat_was_active,
        )
        response = await _response(
            dependencies, rule, runtime, instance, effective_requester,
            requester_is_gm=requester_is_gm,
            result=result,
        )
    await _emit_scene_write_broadcast(
        dependencies.resolve_broadcast() if dependencies.resolve_broadcast else None,
        instance, scene_event,
    )
    return response


async def resume_authoritative_combat(
    dependencies: RulesetGameplayDependencies,
    game_key: str,
    seat_uid: str,
) -> dict[str, Any]:
    """控制权变更后，立刻把该席位的权威回合走完。

    这是 B8 的唯一入口：``next_automatic_intent()`` 已经能识别
    「此刻由 AI 托管的 ``player:<uid>``」，这里只补上触发时机——控制权刚写入
    就复用同一条确定性自动阶梯，直到轮到真人、战斗结束或没有自动意图为止，
    而不是等某个人再点一次攻击。

    ``handled=False`` 表示本局不是权威意图运行时（例如自由文本规则），调用方
    应当继续走探索回合的推进边界。**绝不**替不属于该席位的 actor 出手：只有
    ``next_automatic_intent()`` 现在给出的 actor 就是该席位时才继续，所以
    "当前 actor 不是这个 PC" 时不会有任何状态变化（B17 第二种情形）。

    失败时回滚整段事务并返回结构化错误，不半提交（B19）。
    """

    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if instance is None:
        return {
            "ok": False, "handled": True, "resumed": False,
            "error_code": "GAME_NOT_FOUND", "error": "游戏不存在",
        }
    rule = dependencies.load_rule_for_game(instance)
    if rule is None:
        # 没有规则就没有权威意图；交给探索回合的边界处理。
        return {"ok": True, "handled": False, "resumed": False, "reason": "no_rule"}
    try:
        runtime = dependencies.ruleset_registry.resolve(rule.template)
    except ValueError as exc:
        return {
            "ok": False, "handled": True, "resumed": False,
            "error_code": "RULESET_RUNTIME_UNAVAILABLE", "error": str(exc),
        }
    if not runtime.capabilities.authoritative_intents or _active_combat(instance) is None:
        return {
            "ok": True, "handled": False, "resumed": False,
            "reason": "not_authoritative_combat",
        }
    if not isinstance(runtime, AutomaticIntentRuntime):
        return {
            "ok": True, "handled": True, "resumed": False,
            "reason": "no_automatic_intent_runtime",
        }

    if dependencies.seat_runtime is not None and isinstance(runtime, HostedIntentChoiceRuntime):
        async def preflight() -> dict | None:
            if dependencies.get_instance(instance.game_key) is not instance:
                return _error("GAME_CHANGED", t("webui.services.ruleset_gameplay.game_replaced_refresh"))
            code = check_not_judging(instance, GateRequest(actor_uid=seat_uid, source=SOURCE_INTENT))
            if code:
                return _error(code, t("webui.services.ruleset_gameplay.round_processing_retry_later"))
            progression.require_writable(instance)
            session_stats.require_writable(instance)
            return await _ensure_compatible_adventure_binding(dependencies, runtime, instance)

        planned = await hosted_combat.plan_and_commit(
            instance, runtime, dependencies.seat_runtime, preflight=preflight,
            save=dependencies.save_instance, resume_uid=seat_uid,
            project_batch=lambda staged, b, a: _project_public_batch(runtime, staged, b, a),
        )
        if planned.get("ok") and planned.get("resumed"):
            await _project_batch_memory(dependencies, runtime, instance, planned["batches"])
            return {"ok": True, "handled": True, "resumed": True,
                    "automatic_event_batches": planned["result"]["automatic_event_batches"],
                    "automatic_results": planned["result"]["automatic_results"]}
        return {**planned, "handled": True, "resumed": False,
                **({"error_code": planned.get("code")} if not planned.get("ok") else {})}

    async with instance._lock:
        # Server-owned automation keeps its existing actor checks below; only
        # narrative judgment is a new restriction, including after lock waits.
        code = check_not_judging(instance, GateRequest(actor_uid=seat_uid, source=SOURCE_INTENT))
        if code:
            return {
                "ok": False, "handled": True, "resumed": False,
                "error_code": code, "error": t("webui.services.ruleset_gameplay.round_processing_retry_later"),
            }
        progression.require_writable(instance)
        session_stats.require_writable(instance)
        binding_error = await _ensure_compatible_adventure_binding(
            dependencies, runtime, instance,
        )
        if binding_error:
            return {**binding_error, "handled": True, "resumed": False}
        # 复核与推进在同一个持锁区段内：飞行期间回合可能已经易主，此时
        # "轮到谁"必须以写入那一刻的状态为准。
        pending = runtime.next_automatic_intent(instance)
        if pending is None or str(pending.get("actor_id") or "") != _seat_actor_id(seat_uid):
            return {
                "ok": True, "handled": True, "resumed": False,
                "reason": "not_this_seat",
            }
        before = {
            "ruleset_state": deepcopy(ruleset_runtime.state(instance)),
            "event_ledger": deepcopy(ruleset_runtime.event_ledger(instance)),
            "players": deepcopy(instance.players),
            "combat_state": module_hooks.combat_view(instance)["combat_state"],
            "combat_active": module_hooks.combat_view(instance)["combat_active"],
            "initiative_order": deepcopy(module_hooks.combat_view(instance)["initiative_order"]),
            "initiative_current": module_hooks.combat_view(instance)["initiative_current"],
            "last_activity": session_stats.last_activity(instance),
            "log": deepcopy(instance.log),
            "round_number": progression_state.round_value(instance),
        }
        try:
            rng = dependencies.rng_factory()
            automatic_batches, automatic_results = _automatic_segment(
                runtime, instance, rng,
            )
            session_stats.touch(instance)
            await dependencies.save_instance(instance)
        except (ValueError, KeyError, TypeError) as exc:
            instance.restore_ruleset_transaction(before)
            logger.warning(
                "控制权变更后的权威回合推进失败，已回滚: game=%s uid=%s",
                game_key, seat_uid, exc_info=True,
            )
            return {
                "ok": False, "handled": True, "resumed": False,
                "error_code": "AUTOMATIC_TURN_FAILED", "error": str(exc),
            }
        except Exception:
            instance.restore_ruleset_transaction(before)
            raise
        await _project_batch_memory(
            dependencies, runtime, instance, automatic_batches,
        )
    # Seat-control callers are not necessarily the GM: project as that seat.
    return {
        "ok": True,
        "handled": True,
        "resumed": True,
        **await _viewer_result(
            runtime, instance,
            {
                "automatic_event_batches": automatic_batches,
                "automatic_results": automatic_results,
            },
            seat_uid, False,
        ),
    }

