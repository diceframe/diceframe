"""回合应用服务：统一 Web、Bot 共用的行动、幸运与推进流程。

HTTP routes 只负责读取请求和绑定 SSE 回调；回合状态机、骰子结算、
幸运暂停与响应 DTO 都在这里保持单一实现。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from src.commands.round_processor import RoundNotProcessed, RoundProcessingFailure
from src.engine import progression
from src.engine.action_gate import (
    ACTOR_DECEASED,
    ECONOMY_DECISION_PENDING,
    HUMAN_FREE_TEXT_POST_RETRY_POLICY,
    HUMAN_FREE_TEXT_PRE_RETRY_POLICY,
    PLAYER_NOT_IN_GAME,
    ROUND_PROCESSING,
    RUN_CHANGED,
    SOURCE_HUMAN,
    STRUCTURED_INTENT_REQUIRED,
    StructuredIntentRequirement,
    GateRequest,
    check_run_unchanged,
    evaluate,
)
from src.engine.economy import (
    has_blocking_economy_decision,
    blocking_economy_proposals,
    is_auto_settleable_reward,
    pending_effect_groups,
    pending_economy_proposals,
)
from src.engine.game_instance import GameState
from src.engine.language import localized_text
from src.engine.memory_outbox import pending_memory_deliveries, pending_memory_reversals
from src.engine.player_control import SUBMISSION_BLOCK_CODES
from src.engine.visibility_rules import proposal_visible_to
from src.webui.services._common import MAX_ACTIONS_PER_TURN

if TYPE_CHECKING:
    from src.engine.game_instance import GameInstance
    from src.rulesets.registry import RulesetRuntimeRegistry

logger = logging.getLogger("trpg")

# 控制权拒绝真人提交时的对外文案；键与 player_control.SUBMISSION_BLOCK_CODES 一致。
_SUBMISSION_BLOCK_MESSAGES = {
    "PLAYER_AI_CONTROLLED": "该角色当前由 AI 托管，真人无法提交行动（可先接管该角色）",
    "PLAYER_UNCLAIMED": "该角色尚未被认领，请先认领角色再提交行动",
}


NarrationDelta = Callable[[str], Awaitable[None]]
NarrationReset = Callable[[], Awaitable[None]]


class RoundProcessingError(RuntimeError):
    """本轮没有按预期完成；``result`` 可直接返回给 route。

    两种情形都走这里，避免调用点重复分支：

    - 判定/叙事抛错：``rolled_back`` 表示实例是否已退回行动阶段；
    - 本轮根本没有执行（并发占用、状态已变化、等待幸运或经济）：
      ``error_code`` 为 ``ROUND_PROCESSING_BUSY`` 或 ``ROUND_NOT_PROCESSED``。

    任意一种都必须让调用方收到非 2xx，绝不返回"空正文的成功回合"：
    那会让对局停在判定阶段而客户端以为已经处理完（生产事故 2026-09-10）。
    """

    def __init__(self, result: TurnResult, *, rolled_back: bool = False) -> None:
        payload = result.get("payload") if isinstance(result, dict) else None
        super().__init__(str((payload or {}).get("error") or "round processing failed"))
        self.result = result
        self.rolled_back = rolled_back


class AIPlayerActionFill(Protocol):
    async def __call__(
        self, instance: Any, /, *,
        requires_structured_intent: StructuredIntentRequirement = False,
    ) -> Any: ...


@dataclass(frozen=True)
class TurnDependencies:
    get_instance: Callable[[tuple[str, ...]], Any | None]
    parse_game_key: Callable[[str], tuple[str, ...]]
    ruleset_registry: "RulesetRuntimeRegistry"
    load_rule_for_game: Callable[[Any], Any | None]
    prepare_round_checks_ai: Callable[[Any], Awaitable[Any]] | None
    prepare_round_checks: Callable[[Any], Any] | None
    resolve_pending_dice: Callable[..., Awaitable[dict[str, Any]]]
    roll_for_game: Callable[[str], dict[str, Any]]
    save_instance: Callable[[Any], Awaitable[None]]
    process_round: Callable[..., Awaitable[tuple[str, Any]]] | None
    resolve_luck_decision: Callable[..., Awaitable[dict[str, Any]]]
    decline_pending_luck: Callable[..., Awaitable[dict[str, Any]]]
    drain_economy_outbox: Callable[[Any], Awaitable[bool]] | None = None
    # 奖励自动结算：settings(instance) 返回 (enabled, gold_cap)；resolve_reward 复用
    # 标准支付确认服务（交易流水 + 效果组提交 + 存档），只是免掉 GM 点击。
    # 策略按局解析：本局覆盖 → 规则模板默认 → 服务器全局配置。
    economy_auto_reward_settings: Callable[[Any], tuple[bool, int]] | None = None
    resolve_reward: Callable[[str, str, str], Awaitable[dict[str, Any]]] | None = None
    # AI 托管席位补行动（src/commands/ai_player.py）：只在真人闸门满足之后、
    # 本轮推进之前调用一次。None 表示该运行时没有配置这条能力（行为不变）。
    fill_ai_player_actions: AIPlayerActionFill | None = None
    # 权威战斗的即时接管（src/webui/services/ruleset_gameplay.py）：控制权变更
    # 之后立刻把该席位的确定性回合走完。None 表示该运行时没有这条能力。
    resume_authoritative_combat: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None


class TurnResult(TypedDict):
    """应用服务结果；status 只供 route 映射 HTTP 状态，不进入 JSON。"""

    payload: dict[str, Any]
    status: int


def _result(payload: dict[str, Any], status: int = 200) -> TurnResult:
    return {"payload": payload, "status": status}


def _visible_economy_proposals(instance: "GameInstance", viewer_uid: str = "") -> list[dict[str, Any]]:
    return [
        proposal
        for proposal in pending_economy_proposals(instance)
        if proposal_visible_to(
            proposal, viewer_uid=viewer_uid,
            viewer_is_gm=bool(viewer_uid) and viewer_uid == instance.gm_uid,
        )
    ]


def economy_decision_pending_payload(
    instance: "GameInstance",
    viewer_uid: str = "",
) -> dict[str, Any]:
    """Build a non-leaking progression barrier response for one viewer."""

    unresolved = blocking_economy_proposals(instance)
    visible = [
        proposal
        for proposal in unresolved
        if proposal_visible_to(
            proposal, viewer_uid=viewer_uid,
            viewer_is_gm=bool(viewer_uid) and viewer_uid == instance.gm_uid,
        )
    ]
    return {
        "ok": False,
        "error_code": "ECONOMY_DECISION_PENDING",
        "error": "请先处理待确认的经济提案，再继续本局叙事",
        "pending_count": (
            len(unresolved)
            or len(pending_effect_groups(instance))
            or len(pending_memory_deliveries(instance))
            or len(pending_memory_reversals(instance))
        ),
        "economy_proposals": visible,
    }


def _gate_rejection(instance: "GameInstance", actor_uid: str, code: str) -> TurnResult:
    """Map admission codes to the existing response text and HTTP status."""
    if code == PLAYER_NOT_IN_GAME:
        # Preserve the existing error-only membership response contract.
        return _result({"error": "未加入本局，请先通过邀请链接加入"}, 403)
    if code in SUBMISSION_BLOCK_CODES:
        return _result({
            "ok": False,
            "error_code": code,
            "error": _SUBMISSION_BLOCK_MESSAGES.get(code, "当前角色无法提交行动"),
        }, 409)
    if code == STRUCTURED_INTENT_REQUIRED:
        return _result({
            "ok": False,
            "error_code": code,
            "error": "当前处于权威战斗，请在专业战斗工具中选择合法动作",
        }, 409)
    if code == ACTOR_DECEASED:
        return _result({"error": "角色已死亡，无法提交行动", "error_code": code}, 403)
    if code == ECONOMY_DECISION_PENDING:
        return _result(economy_decision_pending_payload(instance, actor_uid), 409)
    if code == RUN_CHANGED:
        return _result({
            "ok": False, "error_code": "STALE_RUN", "error": "对局已重开，请刷新后重试",
        }, 409)
    if code == ROUND_PROCESSING:
        return _result({
            "error": "本轮正在推进剧情，请等待下一轮开始", "phase": "processing", "error_code": code,
        }, 409)
    return _result({"ok": False, "error_code": code, "error": code}, 409)


def _pending_luck_payload(
    instance: "GameInstance",
    *,
    roll: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "phase": "luck",
        "advanced": False,
        "message": "检定已完成，请选择是否消耗幸运后再继续叙事",
        "check_result": instance.last_check,
        "check_results": list(instance.last_checks),
        "pending_luck_decisions": instance.pending_luck_checks(),
        "multiplayer": instance.multiplayer_status(),
    }
    if roll:
        payload["roll"] = roll
    return payload


def _round_payload(
    instance: "GameInstance",
    narration: str,
    *,
    phase: str | None = None,
    ok: bool | None = None,
    include_recap: bool = False,
    viewer_uid: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "narration": narration,
        "quick_actions": list(instance.quick_actions),
        "economy_proposals": _visible_economy_proposals(instance, viewer_uid),
        "check_result": instance.last_check,
        "check_results": list(instance.last_checks),
    }
    if phase is not None:
        payload["phase"] = phase
    if ok is not None:
        payload["ok"] = ok
    if include_recap:
        payload["recap"] = instance.last_state_update
    return payload


def _luck_error_status(code: str) -> int:
    if code in {"GAME_NOT_FOUND", "CHECK_NOT_FOUND"}:
        return 404
    if code == "LUCK_FORBIDDEN":
        return 403
    if code in {"LUCK_ALREADY_RESOLVED", "LUCK_NOT_PENDING", "REWRITE_IN_PROGRESS", "STALE_RUN"}:
        return 409
    return 400


async def _retry_external_economy_effects(
    dependencies: TurnDependencies,
    instance: "GameInstance",
) -> None:
    drain = dependencies.drain_economy_outbox
    if drain is not None:
        await drain(instance)


async def _prepare_checks(
    dependencies: TurnDependencies,
    instance: "GameInstance",
) -> list[dict[str, Any]]:
    """调用两阶段处理器；为外部/测试处理器保留同步兼容入口。"""
    ai_prepare = dependencies.prepare_round_checks_ai
    if ai_prepare:
        return list((await ai_prepare(instance)) or [])
    legacy_prepare = dependencies.prepare_round_checks
    return list((legacy_prepare(instance) if legacy_prepare else []) or [])


async def _fill_ai_player_actions(
    dependencies: TurnDependencies,
    instance: "GameInstance",
    *,
    game_key: str,
) -> bool:
    """让 AI 托管席位在真人交齐后补上本轮行动（失败不阻塞本轮）。

    补行动本身是一个独立的命令层能力；这个服务只负责在唯一的推进入口调用
    它一次。``ai_player`` 内部已按席位隔离失败并自行记录 ``AI_ACTION_SKIPPED``，
    这里的兜底只防住实现缺陷，绝不让它把真人的这一轮卡住。

    返回"这一遍是否真的写入了至少一条行动"，供调用方决定是否需要单独落盘：
    补了行动但本轮没有推进时，那条行动不能只留在内存里。
    """
    fill = dependencies.fill_ai_player_actions
    # 闸门只有一个所有者：``ai_player.fill_ai_player_actions`` 自己决定该不该行动
    # （真人交齐，或"全 AI 桌没有真人可等"）。这里再拦一道旧闸门会让全 AI 桌永远
    # 进不去——没有活跃真人时 ``human_actions_ready()`` 恒为 False，正是要修的场景。
    if fill is None:
        return False
    def requires_structured_intent() -> bool:
        # Synchronous read-only admission query, evaluated before generation AND
        # inside commit's authority/state locks. Do not capture combat/capabilities
        # now: either may change while context/model generation is in flight.
        rule = dependencies.load_rule_for_game(instance)
        if rule is None:
            # No rule is legacy narrative behavior only for an unbound instance.
            return bool(instance.ruleset_runtime)
        try:
            runtime = dependencies.ruleset_registry.resolve(rule.template)
        except ValueError:
            return True  # Unknown/incompatible runtimes must not admit free text.
        state = instance.ruleset_state
        combat = state.get("combat") if isinstance(state, dict) else None
        combat_active = isinstance(combat, dict) and combat.get("status") == "active"
        return runtime.capabilities.authoritative_intents and (
            not runtime.capabilities.narrative_turns or combat_active
        )

    try:
        records = await fill(instance, requires_structured_intent=requires_structured_intent)
    except Exception:
        logger.exception("AI 托管席位补行动失败，本轮继续: game=%s", game_key)
        return False
    if not isinstance(records, (list, tuple)):
        return False
    return any(
        isinstance(record, dict) and str(record.get("status") or "") == "added"
        for record in records
    )


async def _process_round(
    dependencies: TurnDependencies,
    instance: "GameInstance",
    *,
    game_key: str,
    on_delta: NarrationDelta | None,
    on_reset: NarrationReset | None,
    run_changed: Callable[[], bool] | None = None,
) -> tuple[str, Any]:
    if dependencies.process_round is None:
        raise RuntimeError("round processor is not available")
    try:
        narration, response = await dependencies.process_round(
            instance,
            on_delta=on_delta,
            on_reset=on_reset,
        )
    except RoundNotProcessed as exc:
        # 本轮没有被执行（并发占用 / 状态已变化 / 等待幸运或经济）：
        # 返回结构化非 2xx，而不是伪造一个空正文的成功回合。
        raise RoundProcessingError(
            _not_processed_result(instance, exc.reason),
        ) from exc
    except RoundProcessingFailure as exc:
        # 回滚、落盘与重置广播已由处理边界（RoundProcessor.process_round）完成，
        # 覆盖了幸运超时/CLI 等不经过本服务的入口，这里只做结果翻译。
        raise RoundProcessingError(
            _round_failure_result(instance, rolled_back=exc.rolled_back),
            rolled_back=exc.rolled_back,
        ) from exc
    except Exception as exc:
        if run_changed is not None and run_changed():
            raise RoundProcessingError(_gate_rejection(instance, "", RUN_CHANGED)) from exc
        # 兜底：process_round 依赖本身抛错（例如非 RoundProcessor 的实现）。
        logger.exception("回合处理失败: game=%s，尝试回滚到行动阶段", game_key)
        rolled_back = await _rollback_failed_round(
            dependencies, instance, game_key, on_reset=on_reset,
        )
        raise RoundProcessingError(
            _round_failure_result(instance, rolled_back=rolled_back),
            rolled_back=rolled_back,
        ) from exc
    if run_changed is not None and run_changed():
        raise RoundProcessingError(_gate_rejection(instance, "", RUN_CHANGED))
    await _auto_settle_rewards(dependencies, instance, game_key, run_changed=run_changed)
    return narration, response


def _round_message(instance: "GameInstance", texts: dict[str, str]) -> str:
    return localized_text(getattr(instance, "language", ""), texts, fallback=texts["zh-CN"])


def _round_failure_result(instance: "GameInstance", *, rolled_back: bool) -> TurnResult:
    """叙事/判定失败：区分"已回滚可重试"和"提交点之后失败"。

    两种情况的客户端动作不同——前者可以改行动重试，后者必须刷新查看结果，
    所以不能共用同一句文案和同一个 error_code。
    """
    if rolled_back:
        message = _round_message(instance, {
            "zh-CN": "剧情生成失败，本轮已退回行动阶段，可修改行动后重试",
            "en": "Narration generation failed. This round was rolled back to the action phase; "
                  "revise your actions and try again.",
            "ja": "物語の生成に失敗しました。このターンは行動フェーズに戻りました。"
                  "行動を修正して再試行してください。",
            "de": "Die Erzählungsgenerierung ist fehlgeschlagen. Diese Runde wurde in die Aktionsphase "
                  "zurückgesetzt; überarbeite deine Aktionen und versuche es erneut.",
        })
        error_code = "ROUND_PROCESSING_FAILED"
    else:
        message = _round_message(instance, {
            "zh-CN": "本轮已推进完成，但收尾步骤失败，请刷新查看",
            "en": "The round advanced, but a follow-up step failed. Refresh to see the result.",
            "ja": "このターンは進行しましたが、後処理に失敗しました。再読み込みして確認してください。",
            "de": "Die Runde wurde fortgesetzt, aber ein Folgeschritt ist fehlgeschlagen. Aktualisiere die "
                  "Seite, um das Ergebnis zu sehen.",
        })
        error_code = "ROUND_POST_COMMIT_FAILED"
    return _result({
        "ok": False,
        "error_code": error_code,
        "error": message,
        "phase": "error",
        "rolled_back": rolled_back,
    }, 502)


def _not_processed_result(instance: "GameInstance", reason: str) -> TurnResult:
    """本轮未执行：区分"有人在生成中""已被 GM 抢占"和"状态已变化"。"""
    if reason == "busy":
        return _result({
            "ok": False,
            "error_code": "ROUND_PROCESSING_BUSY",
            "error": _round_message(instance, {
                "zh-CN": "本轮仍在生成剧情，请稍候再试；若长时间无响应请稍后再点强制推进",
                "en": "This round is still being generated. Wait a moment and try again.",
                "ja": "このターンはまだ生成中です。少し待ってから再試行してください。",
                "de": "Diese Runde wird noch generiert. Warte einen Moment und versuche es erneut.",
            }),
            "phase": "processing",
            "reason": reason,
        }, 409)
    if reason == "preempted":
        return _result({
            "ok": False,
            "error_code": "ROUND_NOT_PROCESSED",
            "error": _round_message(instance, {
                "zh-CN": "本轮生成已被 GM 中止并退回行动阶段，请确认行动后重新提交",
                "en": "The GM aborted this round's generation; it is back in the action phase. "
                      "Review your action and submit again.",
                "ja": "GM がこのターンの生成を中止し、行動フェーズに戻しました。"
                      "行動を確認して再提出してください。",
                "de": "Der GM hat die Generierung dieser Runde abgebrochen; sie befindet sich wieder in "
                      "der Aktionsphase. Überprüfe deine Aktion und reiche sie erneut ein.",
            }),
            "phase": "error",
            "reason": reason,
        }, 409)
    return _result({
        "ok": False,
        "error_code": "ROUND_NOT_PROCESSED",
        "error": _round_message(instance, {
            "zh-CN": "本轮状态已变化（可能已推进、已撤回或仍在等待决定），请刷新后重试",
            "en": "This round already changed state (advanced, rolled back, or still waiting on a "
                  "decision). Refresh and try again.",
            "ja": "このターンの状態が変化しました（進行済み・撤回済み・または決定待ち）。"
                  "再読み込みして再試行してください。",
            "de": "Der Status dieser Runde hat sich bereits geändert (fortgesetzt, zurückgesetzt oder "
                  "wartet noch auf eine Entscheidung). Aktualisiere die Seite und versuche es erneut.",
        }),
        "phase": "error",
        "reason": reason,
    }, 409)


async def _rollback_failed_round(
    dependencies: TurnDependencies,
    instance: "GameInstance",
    game_key: str,
    *,
    on_reset: NarrationReset | None,
) -> bool:
    """回滚失败回合并广播重置；清理自身的异常只记日志，不吞掉原始异常。

    返回是否真的回滚成功。回合在提交点之后失败时 ``abort_round_processing``
    是空操作，此时 MUST NOT 广播 ``narration_reset``——那会清掉已经提交入库的
    叙事文本。调用方据返回值选择错误码与文案。
    """
    rolled_back = False
    try:
        rolled_back = bool(await instance.abort_round_processing())
        if rolled_back and on_reset is not None:
            await on_reset()
        await dependencies.save_instance(instance)
    except Exception:
        logger.exception("回滚失败回合状态异常: game=%s", game_key)
    return rolled_back


async def _auto_settle_rewards(
    dependencies: TurnDependencies,
    instance: "GameInstance",
    game_key: str,
    *,
    run_changed: Callable[[], bool] | None = None,
) -> None:
    """Auto-settle qualifying narrative reward proposals after a round.

    A pending kind=reward proposal blocks the table until the GM confirms it,
    which turns the GM into a clerk for ordinary rewards ("击杀怪物，获得 12
    金币").  Qualifying rewards settle through the standard payment-confirm
    service (ledger + deferred effects + save), so the authority path is
    identical to a manual confirmation — only the click is removed.  Anything
    over the cap or with the switch off stays pending for the GM.

    每个 reward proposal 独立结算：单个失败（服务返回非 ok 或异常）只影响
    该提案（保持 pending 交还 GM），不中断其余提案，也不保证整批原子提交。
    """

    resolve_reward = dependencies.resolve_reward
    settings = dependencies.economy_auto_reward_settings
    if resolve_reward is None or settings is None:
        return
    try:
        enabled, gold_cap = settings(instance)
    except Exception:
        logger.warning("读取奖励自动结算配置失败，本轮跳过自动结算", exc_info=True)
        return
    if not enabled or gold_cap < 1:
        return
    economy = getattr(instance, "economy", {})
    proposals = economy.get("proposals", []) if isinstance(economy, dict) else []
    for proposal in list(proposals):
        if run_changed is not None and run_changed():
            return
        if not is_auto_settleable_reward(instance, proposal, gold_cap=gold_cap):
            continue
        proposal_id = str(proposal.get("id") or "")
        if not proposal_id:
            continue
        try:
            result = await resolve_reward(game_key, proposal_id, str(instance.gm_uid or ""))
            if not result.get("ok"):
                logger.warning(
                    "奖励自动结算未成功，保留待确认: game=%s proposal=%s code=%s",
                    game_key, proposal_id, result.get("code"),
                )
        except Exception:
            logger.warning(
                "奖励自动结算异常，保留待确认: game=%s proposal=%s",
                game_key, proposal_id, exc_info=True,
            )


def _auto_reward_cap(dependencies: TurnDependencies, instance: Any) -> int | None:
    """Gold cap for auto-settleable rewards; None when the switch is off.

    Passed into the barrier check so a plain reward within the cap does not
    block the table: it will settle automatically when the round completes.
    """

    settings = dependencies.economy_auto_reward_settings
    if settings is None:
        return None
    try:
        enabled, gold_cap = settings(instance)
    except Exception:
        return None
    return gold_cap if enabled and gold_cap >= 1 else None


async def _advance_progression(
    dependencies: TurnDependencies,
    instance: "GameInstance",
    *,
    game_key: str,
    viewer_uid: str = "",
    include_recap: bool = False,
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
    roll_payload: dict[str, Any] | None = None,
    run_changed: Callable[[], bool] | None = None,
) -> TurnResult | None:
    """本局唯一的"让 AI 补行动并推进本轮"边界；未推进时返回 ``None``。

    普通玩家提交与控制权变更都走这里，因此 AI 席位的补行动、真人闸门、
    检定准备与叙事生成只有一份实现。调用方只负责决定要不要调用，以及
    "没有推进"时对外说什么。
    """
    # AI 托管席位的补行动只有一个注入点：真人闸门满足之后、本轮推进之前。
    # 这里不做任何 AI 判断（闸门、顺序、幂等、竞争守卫都在 ai_player 里），
    # 也不让它的失败挡住真人这一轮。
    progression.require_writable(instance)
    wrote_ai_actions = await _fill_ai_player_actions(
        dependencies, instance, game_key=game_key,
    )

    if run_changed is not None and run_changed():
        return _gate_rejection(instance, viewer_uid, RUN_CHANGED)
    advanced = await instance.try_advance()
    if run_changed is not None and run_changed():
        return _gate_rejection(instance, viewer_uid, RUN_CHANGED)
    if not advanced:
        if wrote_ai_actions:
            # 补了行动但本轮没有推进（例如还有待确认的经济提案）：必须现在落盘，
            # 否则会出现"控制权已保存、AI 行动却只在内存里"的状态。
            await dependencies.save_instance(instance)
        return None
    await _prepare_checks(dependencies, instance)
    if run_changed is not None and run_changed():
        return _gate_rejection(instance, viewer_uid, RUN_CHANGED)
    if instance.pending_luck_checks():
        await dependencies.save_instance(instance)
        return _result(_pending_luck_payload(instance, roll=roll_payload))
    try:
        narration, _ = await _process_round(
            dependencies, instance, game_key=game_key, on_delta=on_delta, on_reset=on_reset,
            run_changed=run_changed,
        )
    except RoundProcessingError as exc:
        return exc.result
    if run_changed is not None and run_changed():
        return _gate_rejection(instance, viewer_uid, RUN_CHANGED)
    payload = _round_payload(
        instance,
        narration,
        phase="done",
        include_recap=include_recap,
        viewer_uid=viewer_uid,
    )
    payload["advanced"] = True
    if roll_payload:
        payload["roll"] = roll_payload
    return _result(payload)


async def resume_after_control_change(
    dependencies: TurnDependencies,
    game_key: str,
    *,
    seat_uid: str = "",
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
) -> TurnResult:
    """控制权写入成功后，立即唤醒现有的唯一推进边界。

    这个 helper 只做三件事：确认控制权已经落盘、判断当前 phase 是否可推进、
    然后调用既有 progression（AI 补行动的闸门属于 ``ai_player``，这里不再重复
    判断）。它不生成 prompt、不产生 AI 行动、不规划检定、不写叙事、不碰战斗规则。

    权威战斗由 ``resume_authoritative_combat``（同一注入能力）接管：控制权一变
    就沿着既有的确定性自动阶梯走完该席位的回合；否则走自由文本回合的推进
    边界。没有任何可推进条件时返回 ``resumed=False`` 与原因，**绝不伪造行动**，
    也绝不为等人而空转。
    """
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance:
        return _result({"ok": False, "resumed": False, "error": "游戏不存在"}, 404)

    combat = dependencies.resume_authoritative_combat
    if combat is not None:
        combat_outcome = await combat(game_key, seat_uid)
        if combat_outcome.get("handled"):
            return _result(dict(combat_outcome))

    if instance.state != GameState.ACTIVE_ACTION:
        return _result({
            "ok": True, "resumed": False, "reason": "phase_not_actionable",
        })
    # 只在"确实存在还没交行动的活跃真人"时才算闸门未开（AI 不抢跑，保持既有语义与
    # 对外 reason）。没有活跃真人时（单人局把房主自己设为 AI 托管）没有真人可等，
    # 必须继续往下走，否则"设为 AI 托管"之后永远不 resume。
    if instance.active_human_players and not instance.human_actions_ready():
        return _result({
            "ok": True, "resumed": False, "reason": "human_gate_open",
        })
    advanced = await _advance_progression(
        dependencies, instance, game_key=game_key, include_recap=True,
        on_delta=on_delta, on_reset=on_reset,
    )
    if advanced is None:
        return _result({
            "ok": True, "resumed": False, "reason": "not_advanceable",
        })
    advanced["payload"]["resumed"] = True
    return advanced


async def submit_action(
    dependencies: TurnDependencies,
    game_key: str,
    actor_uid: str,
    text: str,
    *,
    confirm: bool = False,
    d20: Any = None,
    server_roll: bool = False,
    selected_attribute: str = "",
    selected_skill: str = "",
    target_text: str = "",
    source: str = "",
    expected_run_id: str = "",
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
) -> TurnResult:
    """提交一次行动，并在满足推进条件时完成判定与叙事。"""
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance:
        return _result({"error": "游戏不存在，请刷新页面重新开始"}, 404)
    rule = dependencies.load_rule_for_game(instance)
    requires_structured = False
    if rule is not None:
        try:
            runtime = dependencies.ruleset_registry.resolve(rule.template)
        except ValueError as exc:
            return _result({
                "ok": False,
                "error_code": "RULESET_RUNTIME_UNAVAILABLE",
                "error": str(exc),
            }, 409)
        state = getattr(instance, "ruleset_state", {})
        combat = state.get("combat") if isinstance(state, dict) else None
        combat_active = isinstance(combat, dict) and combat.get("status") == "active"
        requires_structured = runtime.capabilities.authoritative_intents and (
            not runtime.capabilities.narrative_turns or combat_active
        )

    def _economy_blocked() -> bool:
        return has_blocking_economy_decision(
            instance, auto_reward_gold_cap=_auto_reward_cap(dependencies, instance),
        )

    request = GateRequest(
        actor_uid=actor_uid,
        source=SOURCE_HUMAN,
        expected_run_id=expected_run_id or None,
        requires_structured_intent=requires_structured,
        economy_blocked=_economy_blocked,
    )
    code = evaluate(instance, request, HUMAN_FREE_TEXT_PRE_RETRY_POLICY)
    if code:
        return _gate_rejection(instance, actor_uid, code)
    def run_changed() -> bool:
        # Replacement may leave the old object's run_id intact. Both identity
        # fences matter; an omitted/empty token retains the legacy path.
        return bool(expected_run_id) and (
            bool(check_run_unchanged(instance, request))
            or dependencies.get_instance(instance.game_key) is not instance
        )

    if run_changed():
        return _gate_rejection(instance, actor_uid, RUN_CHANGED)
    # Only opted-in callers hold authority across retry, resume, enqueue and
    # progression. Production reset/restart uses this same reentrant gate.
    async with instance.authoritative_write() if expected_run_id else nullcontext(True) as entered:
        if run_changed():
            return _gate_rejection(instance, actor_uid, RUN_CHANGED)
        if not entered:
            return _result({
                "ok": False,
                "error_code": "REWRITE_IN_PROGRESS",
                "error": "GM 正在重写历史回合，请等待完成后再提交行动",
            }, 409)
        progression.require_writable(instance)
        # Retry stays after the pre-retry guards, outside the pure gate.
        await _retry_external_economy_effects(dependencies, instance)
        if run_changed():
            return _gate_rejection(instance, actor_uid, RUN_CHANGED)
        code = evaluate(instance, request, HUMAN_FREE_TEXT_POST_RETRY_POLICY)
        if code:
            return _gate_rejection(instance, actor_uid, code)

        existing_action = next(
            (action for action in instance.action_queue if action.get("user_id") == actor_uid),
            None,
        )
        existing_pending_roll = bool(existing_action and existing_action.get("dice_pending"))
        if instance.solo_mode:
            action_count = sum(1 for action in instance.action_queue if action.get("user_id") == actor_uid)
            if action_count >= MAX_ACTIONS_PER_TURN:
                return _result({"error": f"本回合已达行动上限（{MAX_ACTIONS_PER_TURN} 条）"}, 400)
        elif (
            existing_action
            and int(existing_action.get("revision_count", 1) or 1) >= 3
            and not (confirm and existing_pending_roll)
        ):
            return _result({"error": "本轮行动已修改 3 次，请等待其他玩家或 GM 推进"}, 400)

        # Forward only nonempty tokens: existing callers and test adapters keep
        # their original call shape. Aggregate checks run after its state lock.
        run_options = {"expected_run_id": expected_run_id} if expected_run_id else {}
        if instance.state == GameState.PAUSED:
            if instance.round_number <= 0:
                await instance.start_round(**run_options)
            else:
                await instance.resume(**run_options)
            if run_changed():
                return _gate_rejection(instance, actor_uid, RUN_CHANGED)

        roll_payload: dict[str, Any] | None = None
        if confirm and existing_pending_roll:
            resolved = await dependencies.resolve_pending_dice(
                game_key, actor_uid, "player",
            )
            if run_changed():
                return _gate_rejection(instance, actor_uid, RUN_CHANGED)
            if not resolved.get("ok"):
                status = 409 if resolved.get("code") == "REWRITE_IN_PROGRESS" else 400
                return _result(resolved, status)
            roll_payload = resolved.get("roll")
        elif confirm and d20 is None and server_roll:
            roll_payload = dependencies.roll_for_game(game_key)
            if not roll_payload.get("ok"):
                return _result(roll_payload, 400)
            d20 = roll_payload["value"]

        if not (confirm and existing_pending_roll):
            action_text = text
            action_added = await instance.add_action(
                actor_uid,
                action_text,
                selected_attribute,
                selected_skill,
                target_text,
                source=source,
                **run_options,
            )
            if run_changed():
                return _gate_rejection(instance, actor_uid, RUN_CHANGED)
            process_lock = getattr(instance, "_process_lock", None)
            if (
                not action_added
                and process_lock is not None
                and process_lock.locked()
            ):
                return _result({
                    "ok": False,
                    "error_code": "REWRITE_IN_PROGRESS",
                    "error": "GM 正在重写历史回合，请等待完成后再提交行动",
                }, 409)
            # Purchase requests are recorded together with the action. Persisting
            # here keeps a request recoverable even when another player has not yet
            # submitted an action and no narrative round has started.
            if action_added:
                try:
                    await dependencies.save_instance(instance)
                except Exception:
                    logger.exception("保存行动/购买请求失败: game=%s", game_key)
                if run_changed():
                    return _gate_rejection(instance, actor_uid, RUN_CHANGED)

        # 真人提交之后与"控制权变更之后"共用同一个推进入口：AI 托管席位的补行动
        # （真人闸门、顺序、幂等、竞争守卫都在 ai_player 里）、检定准备与叙事生成
        # 只有一份实现，它的失败也不会挡住真人这一轮。
        advanced = await _advance_progression(
            dependencies, instance, game_key=game_key, viewer_uid=actor_uid,
            include_recap=True, on_delta=on_delta, on_reset=on_reset,
            roll_payload=roll_payload,
            run_changed=run_changed if expected_run_id else None,
        )
        if run_changed():
            return _gate_rejection(instance, actor_uid, RUN_CHANGED)
        if advanced is not None:
            return advanced

        multiplayer = instance.multiplayer_status()
        waiting_names = [
            player.get("character_name") or player.get("user_id")
            for player in multiplayer.get("waiting_players", [])
        ]
        waiting_text = "、".join(str(name) for name in waiting_names if name)
        message = f"行动已公开，等待 {waiting_text} 行动" if waiting_text else "行动已公开，等待系统推进"
        payload = {
            "narration": message,
            "advanced": False,
            "phase": "done",
            "multiplayer": multiplayer,
        }
        if roll_payload:
            payload["roll"] = roll_payload
        return _result(payload)


async def resolve_luck_and_continue(
    dependencies: TurnDependencies,
    game_key: str,
    check_id: str,
    actor_uid: str,
    spend: bool,
    *,
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
) -> TurnResult:
    """原子处理幸运选择；所有选择完成后继续生成叙事。"""
    decision = await dependencies.resolve_luck_decision(
        game_key, check_id, actor_uid, spend,
    )
    if not decision.get("ok"):
        return _result(decision, _luck_error_status(str(decision.get("code") or "")))
    if decision.get("round_already_resolved"):
        return _result({**decision, "phase": "done", "advanced": True})
    if not decision.get("ready_to_resolve"):
        return _result({**decision, "advanced": False})

    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance:
        return _result({"ok": False, "error": "游戏不存在"}, 404)
    await _retry_external_economy_effects(dependencies, instance)
    if has_blocking_economy_decision(
        instance, auto_reward_gold_cap=_auto_reward_cap(dependencies, instance),
    ):
        return _result(economy_decision_pending_payload(instance, actor_uid), 409)
    try:
        narration, _ = await _process_round(
            dependencies, instance, game_key=game_key, on_delta=on_delta, on_reset=on_reset,
        )
    except RoundProcessingError as exc:
        return exc.result
    payload = {
        **decision,
        **_round_payload(instance, narration, phase="done", viewer_uid=actor_uid),
        "advanced": True,
    }
    return _result(payload)


async def advance_round(
    dependencies: TurnDependencies,
    game_key: str,
    actor_uid: str,
    *,
    force: bool = False,
    on_delta: NarrationDelta | None = None,
    on_reset: NarrationReset | None = None,
    _allow_preempt: bool = True,
) -> TurnResult:
    """GM 推进回合，统一处理卡死恢复、待掷骰和待幸运选择。

    ``force=True`` 且本轮**正在**生成时，GM 的中止意图优先：先抢占在飞处理
    （取消 + 回滚 + 落盘），再按正常流程重新处理本回合。只允许抢占一次，
    避免与另一个推进入口互相抢占成环。
    """
    instance = dependencies.get_instance(
        dependencies.parse_game_key(game_key)
    )
    if not instance:
        return _result({"error": "not found"}, 404)
    if actor_uid != instance.gm_uid:
        return _result({"ok": False, "error": "仅 GM 可推进"}, 403)
    progression.require_writable(instance)
    if force and _allow_preempt and instance.round_processing_in_flight():
        if await instance.cancel_round_processing():
            logger.warning("GM 强制推进：已中止在飞生成 - game_key=%s", game_key)
            return await advance_round(
                dependencies, game_key, actor_uid,
                force=True, on_delta=on_delta, on_reset=on_reset,
                _allow_preempt=False,
            )
    await _retry_external_economy_effects(dependencies, instance)
    if has_blocking_economy_decision(
        instance, auto_reward_gold_cap=_auto_reward_cap(dependencies, instance),
    ):
        return _result(economy_decision_pending_payload(instance, actor_uid), 409)

    if instance.state == GameState.ACTIVE_JUDGMENT and instance.action_queue:
        await _prepare_checks(dependencies, instance)
        pending_luck = instance.pending_luck_checks()
        if pending_luck and not force:
            return _result(_pending_luck_payload(instance), 409)
        advanced_declined_luck: list[dict[str, Any]] = []
        if pending_luck:
            declined = await dependencies.decline_pending_luck(game_key)
            advanced_declined_luck = list(declined.get("declined_luck_decisions") or [])
        logger.warning("检测到卡死状态，自动恢复 process_round - game_key=%s", game_key)
        try:
            narration, _ = await _process_round(
                dependencies, instance, game_key=game_key, on_delta=on_delta, on_reset=on_reset,
            )
        except RoundProcessingError as exc:
            return exc.result
        payload = _round_payload(instance, narration, viewer_uid=actor_uid)
        if advanced_declined_luck:
            payload["declined_luck_decisions"] = advanced_declined_luck
        return _result(payload)

    if not instance.can_accept_actions():
        return _result({"ok": False, "narration": "当前不能推进"})

    auto_rolls: list[dict[str, Any]] = []
    if instance.has_pending_dice():
        if not force:
            return _result({
                "ok": False,
                "narration": "仍有玩家行动等待掷骰",
                "multiplayer": instance.multiplayer_status(),
            })
        resolved = await dependencies.resolve_pending_dice(
            game_key, source="system",
        )
        if not resolved.get("ok"):
            return _result(resolved, 400)
        auto_rolls = list(resolved.get("resolved") or [])

    forced_waiting: list[str] = []
    if force and not instance.should_advance():
        multiplayer = instance.multiplayer_status()
        waiting = multiplayer.get("waiting_players", [])
        if not instance.action_queue:
            return _result({"ok": False, "narration": "还没有任何玩家行动，无法推进"})
        for player in waiting:
            uid = str(player.get("user_id", "") or "")
            name = str(player.get("character_name", "") or uid)
            if uid:
                await instance.add_action(uid, "本轮暂不行动，保持警戒。")
                forced_waiting.append(name)

    advanced = await instance.advance_round() if force else await instance.try_advance()
    if advanced:
        await _prepare_checks(dependencies, instance)
        pending_luck = instance.pending_luck_checks()
        if pending_luck and not force:
            await dependencies.save_instance(instance)
            return _result(_pending_luck_payload(instance))
        declined_luck: list[dict[str, Any]] = []
        if pending_luck:
            declined = await dependencies.decline_pending_luck(game_key)
            declined_luck = list(declined.get("declined_luck_decisions") or [])
        try:
            narration, _ = await _process_round(
                dependencies, instance, game_key=game_key, on_delta=on_delta, on_reset=on_reset,
            )
        except RoundProcessingError as exc:
            return exc.result
        payload = _round_payload(instance, narration, ok=True, viewer_uid=actor_uid)
        if forced_waiting:
            payload["forced_waiting"] = forced_waiting
        if auto_rolls:
            payload["auto_rolls"] = auto_rolls
        if declined_luck:
            payload["declined_luck_decisions"] = declined_luck
        return _result(payload)

    multiplayer = instance.multiplayer_status()
    waiting_names = [
        player.get("character_name") or player.get("user_id")
        for player in multiplayer.get("waiting_players", [])
    ]
    waiting_text = "、".join(str(name) for name in waiting_names if name)
    message = f"推进失败：仍在等待 {waiting_text} 行动" if waiting_text else "推进失败：当前状态不能推进"
    return _result({"ok": False, "narration": message, "multiplayer": multiplayer})
