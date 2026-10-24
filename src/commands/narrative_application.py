"""回合管线之外的叙事响应应用链（GAL 轨道 P5 泛化）。

`apply_parsed_data_to_response` 把解析出的标签数据落到 response 对象；
`apply_narrative_effects` 应用一段与回合机制无关的通用尾部链（成长奖励、
快捷行动、记忆、剧情、私信）。两者最初都长在回合管线
（`round_llm.py` / `round_processor.process_round_impl`）里，行为保持不变地
抽出，供链外的叙事消费方（如节点激活后的场景过渡叙事）复用。

注意 `RoundProcessor.commit_deferred_economy_effects` 仍保留自己的手工链：
那边的步骤顺序（plot → private → memory）与"记忆失败必须抛出以中止结算
事务"的语义是刻意设计，不迁移到这里统一。
"""

from __future__ import annotations

import logging
from typing import Any

from src.commands.round_effects import (
    apply_growth_rewards,
    apply_memory_delta,
    apply_plot_update,
    store_private_messages,
    update_quick_actions,
)
from src.engine.game_instance import GameInstance
from src.engine.health import record_health_event
from src.engine.language import localized_text
from src.engine.modules import progression_state

# 与回合管线（round_llm）共用 "trpg" 日志通道，保持搬家后的日志路由不变。
logger = logging.getLogger("trpg")


def apply_parsed_data_to_response(
    instance: GameInstance,
    response: Any,
    data: dict,
    *,
    context_label: str | None = None,
) -> None:
    """把解析出的标签数据落到 response 对象，供后续状态应用阶段使用。

    ``context_label`` 仅用于日志与健康事件的上下文描述（链外调用时传
    如 "scene-transition"）；缺省时回退到当前回合号，与回合管线内行为一致。
    """
    round_context = (
        context_label if context_label is not None
        else str(progression_state.round_value(instance))
    )
    if data.get("state_update") or data.get("plot_update"):
        response.is_narration_only = False
        instance.set_tag_failure_streak(0)  # 成功解析即清零，防止 streak 累积误触发提示
        response.state_update = data["state_update"]
        response.memory_delta = data["memory_delta"]
        response.info_asymmetry = data["info_asymmetry"]
        response.plot_update = data["plot_update"]
        state_update = data.get("state_update", {})
        players_changed = list(state_update.get("players", {}).keys())
        scene = state_update.get("scene_change", "")
        loot = state_update.get("loot", [])
        logger.info(
            "标签解析成功 (round=%s): 玩家=%s, 场景=%s, 战利品=%d",
            round_context,
            players_changed if players_changed else "无变化",
            scene or "不变",
            len(loot),
        )
        return

    if not response.state_update:
        response.is_narration_only = True
        streak = instance._tag_fail_streak + 1
        instance.set_tag_failure_streak(streak)
        if streak >= 3:
            logger.error("标签连续%d轮解析失败！建议：检查模型是否支持当前prompt格式，或更换模型", streak)
            record_health_event(
                instance,
                component="llm_parser",
                code="TAG_PARSE_STREAK",
                severity="error",
                title="结构化解析连续失败",
                message=f"标签已连续 {streak} 轮解析失败。",
                impact="HP、资源、物品、任务和记忆等结构化状态可能持续未更新。",
                fallback="narration_only",
                repair_hint="建议暂停并检查模型、prompt 标签格式，或重新生成本轮。",
            )
            # P2-B：连续失败时给玩家可见提示，避免"叙事里受伤但 HP 没扣"的
            # 状态漂移无声无息（health_event 仅 GM 可见）。作为系统事件展示。
            _sync_notice = localized_text(
                getattr(instance, "language", ""),
                {
                    "en": "⚠️ System: state sync has failed for several rounds; HP/resources/items "
                          "may be out of date. Ask the GM to check, or regenerate this round.",
                    "zh-CN": "⚠️ 系统提示：连续多轮状态同步失败，HP/资源/物品可能未更新。"
                              "请告知 GM 检查，或重新生成本轮。",
                    "ja": "⚠️ システム通知：複数ラウンドにわたり状態同期に失敗しています。"
                          "HP/資源/アイテムが最新でない可能性があります。GM に確認を依頼するか、"
                          "このラウンドを再生成してください。",
                    "de": "⚠️ Systemhinweis: Die Statussynchronisation ist mehrere Runden in Folge "
                          "fehlgeschlagen; TP/Ressourcen/Gegenstände sind möglicherweise veraltet. "
                          "Bitte den GM um eine Prüfung oder generiere diese Runde neu.",
                },
            )
            system_notices = getattr(response, "system_notices", None)
            if not isinstance(system_notices, list):
                system_notices = []
                response.system_notices = system_notices
            system_notices.append(_sync_notice)
        else:
            logger.warning(
                "标签解析失败，本轮仅保留叙事 (round=%s, streak=%d)",
                round_context, streak,
            )
            record_health_event(
                instance,
                component="llm_parser",
                code="NARRATION_ONLY_FALLBACK",
                severity="warning",
                title="结构化解析失败",
                message="本轮 AI 回复未解析出状态标签，系统仅保留叙事。",
                impact="HP、资源、物品、任务和记忆等结构化状态可能未更新。",
                fallback="narration_only",
                repair_hint="可重新生成本轮，或检查模型是否遵守 prompt 标签格式。",
            )
        response.state_update = {}
        response.memory_delta = {"add": [], "update": [], "forget": []}
        response.info_asymmetry = {}
        response.plot_update = {"quests": [], "relations": [], "decisions": []}
    else:
        instance.set_tag_failure_streak(0)


async def apply_narrative_effects(
    instance: GameInstance,
    data: dict,
    response: Any,
    *,
    rule: Any = None,
    progression: Any = None,
    runtime: Any | None = None,
    apply_growth: bool = True,
    apply_quick_actions: bool = True,
    memory_store: Any = None,
) -> list[str]:
    """应用叙事响应的通用尾部链，返回成长奖励产生的系统提示。

    链序与回合管线（process_round_impl）保持一致：成长奖励 → 快捷行动 →
    记忆增量（best-effort）→ embedding 队列冲刷 → 剧情更新 → 私信存储。
    回合特有项（检定、战斗信号、谜题、经济决策屏障、回合提交）不在其中。
    """
    notices: list[str] = []
    if apply_growth:
        notices.extend(apply_growth_rewards(
            instance, data, response, rule, progression, runtime,
        ))
    if apply_quick_actions:
        update_quick_actions(instance, data)
    await apply_memory_delta(instance, response, memory_store)
    # 消费待处理 embedding 队列，让新记忆在运行中也能获得向量（此前从未被调用）
    if memory_store and memory_store.embedding_client:
        try:
            await memory_store.flush_pending_embeddings()
        except Exception:
            logger.warning(
                "flush_pending_embeddings 失败 (round=%d)",
                progression_state.round_value(instance), exc_info=True,
            )
    apply_plot_update(instance, response)
    store_private_messages(instance, response)
    return notices
