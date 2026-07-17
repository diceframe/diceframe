"""回合中的 LLM 调用、重试与回复解析。"""

from __future__ import annotations

import logging
from typing import Any

from src.commands.round_helpers import should_multi_step, validate_dice_constraint
from src.commands.tag_json import safe_parse_json
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance
from src.engine.health import record_health_event
from src.engine.language import is_english
from src.llm.parser import sanitize_narration

logger = logging.getLogger("trpg")
_NARRATION_SOFT_LIMIT_CHARS = 260
_NARRATION_COMPRESS_TRIGGER_CHARS = 450
_NARRATION_COMBAT_TARGET_CHARS = 400


def _narration_len(text: str) -> int:
    return len(str(text or "").replace("\n", "").strip())


def _replace_narration_in_content(content: str, narration: str) -> str:
    if "---" not in content:
        return narration
    return f"{narration.strip()}\n---{content.split('---', 1)[1]}"


async def _compress_long_narration(
    llm_client,
    gm_prompt: str,
    response,
    actions_text: str,
    combat_model: str,
    max_tokens: int,
) -> None:
    narration = str(response.narration or "").strip()
    if _narration_len(narration) <= _NARRATION_COMPRESS_TRIGGER_CHARS:
        return
    combat_words = ("战斗", "攻击", "砍", "刺", "射", "突袭", "格挡", "防御", "回避")
    target = (
        _NARRATION_COMBAT_TARGET_CHARS
        if combat_model != "none" and any(word in actions_text for word in combat_words)
        else _NARRATION_SOFT_LIMIT_CHARS
    )
    if is_english(getattr(response, "language", "")):
        prompt = (
            "Compress the following TRPG GM narration. Output only the compressed "
            "narration, without --- or any state tags.\n"
            "Requirements: keep established facts, NPC names, key clues, check/combat "
            "results, and the immediate pressure for the players; do not add new lore "
            f"or change outcomes. Keep it under about {target} characters and at most 2 paragraphs.\n\n"
            f"Original narration:\n{narration}"
        )
    else:
        prompt = (
            "请压缩以下 TRPG GM 正文，只输出压缩后的正文，不要输出 --- 或任何状态标签。\n"
            f"要求：保留已发生事实、NPC 名字、关键线索、检定/战斗结果和玩家可执行的下一步压力；"
            f"总字数控制在 {target} 字以内，最多 2 段；不要新增设定，不要改变结果。\n\n"
            f"原正文：\n{narration}"
        )
    compress_system = (
        "You are a narration compressor. Output only the compressed narration text, no preamble, no ---, no state tags, no meta commentary about the task."
        if is_english(getattr(response, "language", ""))
        else "你是叙事压缩器，只输出压缩后的正文，不要前言、不要 ---、不要状态标签、不要对任务的元说明。"
    )
    try:
        compressed = await llm_client.call(
            system_prompt=compress_system,
            user_message=prompt,
            temperature=0.2,
            max_tokens=min(max_tokens, 512),
        )
    except Exception:
        logger.warning("超长叙事二次压缩失败，保留原文", exc_info=True)
        return
    new_narration = str(compressed.narration or compressed.content or "").split("---", 1)[0].strip()
    if not new_narration:
        return
    if _narration_len(new_narration) >= _narration_len(narration):
        logger.info("超长叙事压缩未变短，保留原文")
        return
    response.narration = sanitize_narration(new_narration)
    response.content = _replace_narration_in_content(str(response.content or ""), response.narration)


async def append_multistep_analysis(
    llm_client: Any,
    instance: GameInstance,
    gm_prompt: str,
    context: str,
    actions_text: str,
    analysis_max_tokens: int,
) -> str:
    """WebUI 多步推理：先分析局势，再把分析摘要追加到上下文。"""
    if not should_multi_step(instance, actions_text):
        return context

    try:
        analyze_context = context + "\n\n请用 JSON 分析当前局势，格式: {\"situation\":\"...\",\"npc_intents\":{},\"environment\":\"...\",\"risks\":[],\"key_details\":[]}"
        analyze_res = await llm_client.call(
            system_prompt=gm_prompt,
            user_message=analyze_context,
            temperature=0.3,
            max_tokens=analysis_max_tokens,
        )
        analysis_text = analyze_res.content
        logger.info("多步推理: 分析完成 (round=%d, len=%d)",
                    instance.round_number, len(analysis_text))
        return context + "\n\n【局势分析（内部参考）】\n" + analysis_text[:600]
    except Exception:
        logger.exception("多步推理分析失败，降级为单次调用 (round=%d)", instance.round_number)
        return context


async def call_llm_with_tag_retry(
    llm_client: Any,
    instance: GameInstance,
    gm_prompt: str,
    context: str,
    combat_model: str,
    dice_block: str,
    narrative_max_tokens: int,
    actions_text: str = "",
) -> tuple[Any, dict]:
    """调用 LLM，解析标签；若叙事违反骰子约束则最多重试 3 次。"""
    response = None
    data: dict = {}
    for retry in range(3):
        retry_context = context
        if retry > 0:
            if is_english(getattr(instance, "language", "")):
                retry_context = context + "\n\nPrevious response contradicted the required dice/check result. Rewrite the narration and strictly follow the check outcome."
            else:
                retry_context = context + "\n\n⚠️ 上一轮回复与【系统检定·必须遵循】矛盾，请严格遵循检定结果重新叙述。"
        response = await llm_client.call(
            system_prompt=gm_prompt,
            user_message=retry_context,
            temperature=0.7,
            max_tokens=narrative_max_tokens,
        )
        response.language = getattr(instance, "language", "zh-CN")

        if "---" in response.content:
            narration_part = response.content.split("---", 1)[0].strip()
            response.narration = narration_part or response.narration or response.content
        response.narration = sanitize_narration(response.narration or response.content)
        data = parse_tag_state(response.content, combat_model)
        if not data.get("state_update") and not data.get("plot_update"):
            try:
                json_data = safe_parse_json(response.content)
                if json_data:
                    logger.info("标签无结果，JSON 回退成功 (round=%d)", instance.round_number)
                    data["state_update"] = json_data.get("state_update", {})
                    data["memory_delta"] = json_data.get("memory_delta", {})
                    data["info_asymmetry"] = json_data.get("info_asymmetry", {})
                    data["plot_update"] = json_data.get("plot_update", {})
            except ValueError:
                record_health_event(
                    instance,
                    component="llm_parser",
                    code="JSON_FALLBACK_FAILED",
                    severity="info",
                    title="JSON 回退解析失败",
                    message="标签解析无结构化结果后，JSON 回退解析也未成功。",
                    fallback="continue_tag_result",
                    repair_hint="如果连续发生，检查模型是否遵守标签或 JSON 输出格式。",
                )

        narration = response.narration or response.content
        if not dice_block or validate_dice_constraint(dice_block, narration):
            break
        logger.warning("骰子约束矛盾，重试 (%d/2, round=%d)", retry + 1, instance.round_number)
    else:
        logger.error("骰子约束连续3次矛盾，接受最后输出 (round=%d)", instance.round_number)

    await _compress_long_narration(
        llm_client, gm_prompt, response, actions_text, combat_model, narrative_max_tokens
    )
    return response, data


def apply_parsed_data_to_response(instance: GameInstance, response: Any, data: dict) -> None:
    """把解析出的标签数据落到 response 对象，供后续状态应用阶段使用。"""
    if data.get("state_update") or data.get("plot_update"):
        response.is_narration_only = False
        response.state_update = data["state_update"]
        response.memory_delta = data["memory_delta"]
        response.info_asymmetry = data["info_asymmetry"]
        response.plot_update = data["plot_update"]
        state_update = data.get("state_update", {})
        players_changed = list(state_update.get("players", {}).keys())
        scene = state_update.get("scene_change", "")
        loot = state_update.get("loot", [])
        logger.info(
            "标签解析成功 (round=%d): 玩家=%s, 场景=%s, 战利品=%d",
            instance.round_number,
            players_changed if players_changed else "无变化",
            scene or "不变",
            len(loot),
        )
        return

    if not response.state_update:
        response.is_narration_only = True
        streak = getattr(instance, "_tag_fail_streak", 0) + 1
        instance._tag_fail_streak = streak
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
        else:
            logger.warning("标签解析失败，本轮仅保留叙事 (round=%d, streak=%d)", instance.round_number, streak)
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
        instance._tag_fail_streak = 0
