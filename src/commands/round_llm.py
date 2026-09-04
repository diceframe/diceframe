"""回合中的 LLM 调用、重试与回复解析。"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.commands.round_helpers import should_multi_step, validate_dice_constraint
from src.commands.protocol_repair import append_protocol_repair_instruction
from src.commands.tag_json import safe_parse_json
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance
from src.engine.health import record_health_event
from src.engine.language import localized_text, normalize_language
from src.llm.client import OutputTruncatedError, length_retry_budgets
from src.llm.parser import (
    find_protocol_suffix_start,
    has_malformed_protocol_leak,
    normalize_tag_protocol,
    sanitize_narration,
)
from src.llm.protocol import leaked_protocol_line_start, strip_protocol_markup_from_public_line

logger = logging.getLogger("trpg")
# 叙事压缩目标，按语言归一后的 key（normalize_language 返回值）取配置。
# 中文按字符数（普通 260 / 战斗 400），英文按词数换算成字符（普通 ≈150 词、
# 战斗 ≈200 词，触发线设在超过提示词上限才压）。新增语言时在此加一行配置，
# 并同步补充该语言的压缩 prompt 文案；未登记的语言回退中文配置。
_NARRATION_LIMITS = {
    "zh-CN": {"trigger": 500, "soft": 260, "combat": 400},
    "en": {"trigger": 1200, "soft": 900, "combat": 1100},
}
_NARRATION_COMPRESS_MIN_TOKENS = 1024
_NARRATION_COMPRESS_MAX_TOKENS = 2048


def _narration_len(text: str) -> int:
    return len(str(text or "").replace("\n", "").strip())


def _replace_narration_in_content(content: str, narration: str) -> str:
    if "---" not in content:
        return narration
    return f"{narration.strip()}\n---{content.split('---', 1)[1]}"


class _NarrationDeltaFilter:
    """流式转发叙事正文，遇到 '---' 分隔符后停止转发。

    LLM 叙事输出形如 ``<叙事>\n---\n<结构化标签>``，--- 之后的标签是给解析器用的，
    不能推给前端。本类逐段接收 call_stream 的 delta，只把分隔符之前的部分经 on_delta
    推出；为避免 '---' 被拆到多个 chunk 中间，最多暂存 len(SEPARATOR)-1 个字符，
    flush() 时把剩余暂存一次性发出（纯叙事、无分隔符的场景）。
    """

    SEPARATOR = "---"
    PROTOCOL_HOLD_CHARS = 128

    def __init__(self, on_delta):
        self._on_delta = on_delta
        self._buf = ""
        self._sent = 0
        self._sealed = False

    async def feed(self, text: str) -> None:
        if self._sealed or not text:
            return
        self._buf += text
        separator_idx = self._buf.find(self.SEPARATOR)
        protocol_idx = find_protocol_suffix_start(self._buf)
        candidates = [
            index
            for index in (separator_idx, protocol_idx)
            if index is not None and index >= 0
        ]
        idx = min(candidates) if candidates else -1
        if idx != -1:
            head = self._buf[self._sent:idx]
            self._sent = idx
            self._sealed = True
            if head:
                await self._on_delta(head)
            return

        # A single malformed tag line cannot be treated as an executable suffix
        # safely, but it must still never reach the player. Hold that line until
        # complete, remove the protocol token, and preserve explicit prose after it.
        while True:
            pending = self._buf[self._sent:]
            leaked_at = leaked_protocol_line_start(pending)
            if leaked_at is None:
                break
            absolute = self._sent + leaked_at
            if absolute > self._sent:
                head = self._buf[self._sent:absolute]
                self._sent = absolute
                if head:
                    await self._on_delta(head)
            newline = self._buf.find("\n", self._sent)
            if newline < 0:
                return
            leaked_line = self._buf[self._sent:newline]
            public_remainder = strip_protocol_markup_from_public_line(leaked_line)
            self._sent = newline + 1
            if public_remainder:
                await self._on_delta(public_remainder + "\n")
        # 暂存末尾最多 len(SEPARATOR)-1 个字符，防止分隔符跨 chunk 被提前发出
        hold = min(self.PROTOCOL_HOLD_CHARS, len(self._buf) - self._sent)
        if hold > 0:
            forward = self._buf[self._sent:len(self._buf) - hold]
            self._sent = len(self._buf) - hold
        else:
            forward = self._buf[self._sent:]
            self._sent = len(self._buf)
        if forward:
            await self._on_delta(forward)

    async def flush(self) -> None:
        if self._sealed:
            return
        boundary = find_protocol_suffix_start(self._buf)
        end = boundary if boundary is not None else len(self._buf)
        remaining = self._buf[self._sent:end]
        self._sent = len(self._buf)
        if remaining:
            cleaned = sanitize_narration(remaining)
            if cleaned:
                await self._on_delta(cleaned)


async def _compress_long_narration(
    llm_client,
    gm_prompt: str,
    response,
    actions_text: str,
    combat_model: str,
    max_tokens: int,
) -> None:
    narration = str(response.narration or "").strip()
    lang = normalize_language(getattr(response, "language", ""))
    limits = _NARRATION_LIMITS.get(lang, _NARRATION_LIMITS["zh-CN"])
    is_en = lang == "en"
    if _narration_len(narration) <= limits["trigger"]:
        return
    combat_words = ("战斗", "攻击", "砍", "刺", "射", "突袭", "格挡", "防御", "回避")
    is_combat = combat_model != "none" and any(word in actions_text for word in combat_words)
    target = limits["combat"] if is_combat else limits["soft"]
    if is_en:
        prompt = (
            "Compress the following TRPG GM narration. Output only the compressed "
            "narration, without --- or any state tags.\n"
            "Requirements: keep established facts, NPC names, key clues, check/combat "
            "results, and the immediate pressure for the players; do not add new lore "
            f"or change outcomes. Keep it under about {target} characters (~{target // 6} words) and at most 2 paragraphs.\n\n"
            f"Original narration:\n{narration}"
        )
    else:
        prompt = (
            "请压缩以下 TRPG GM 正文，只输出压缩后的正文，不要输出 --- 或任何状态标签。\n"
            f"要求：保留已发生事实、NPC 名字、关键线索、检定/战斗结果和玩家可执行的下一步压力；"
            f"总字数控制在 {target} 字以内，最多 2 段；不要新增设定，不要改变结果。\n\n"
            f"原正文：\n{narration}"
        )
    compress_system = localized_text(
        getattr(response, "language", ""),
        {
            "en": "You are a narration compressor. Output only the compressed narration text, no preamble, no ---, no state tags, no meta commentary about the task.",
            "zh-CN": "你是叙事压缩器，只输出压缩后的正文，不要前言、不要 ---、不要状态标签、不要对任务的元说明。",
            "ja": "あなたはナレーション圧縮器です。圧縮後のナレーション本文のみを出力し、前置き・---・状態タグ・タスクに対するメタ解説を出力しないでください。",
        },
    )
    try:
        compression_max_tokens = max(
            _NARRATION_COMPRESS_MIN_TOKENS,
            min(max_tokens, _NARRATION_COMPRESS_MAX_TOKENS),
        )
        compressed = await llm_client.call(
            system_prompt=compress_system,
            user_message=prompt,
            temperature=0.2,
            max_tokens=compression_max_tokens,
        )
    except Exception:
        # P2-C：压缩失败按目标长度硬截断，避免超长叙事进 log/context 推高下一轮
        # 截断概率（长→压缩失败→更长的反馈环）。
        logger.warning("超长叙事二次压缩失败，按 %d 字硬截断", target, exc_info=True)
        truncated = narration[:target].rstrip()
        if truncated and truncated != narration:
            response.narration = sanitize_narration(truncated + "…")
            response.content = _replace_narration_in_content(str(response.content or ""), response.narration)
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
    started = time.perf_counter()
    if not should_multi_step(instance, actions_text):
        # 供验收日志证明：本轮没有额外局势分析调用。
        logger.info("局势分析: 未触发，跳过 (round=%d)", instance.round_number)
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
        logger.info("局势分析: 完成 (round=%d, len=%d, 耗时=%dms)",
                    instance.round_number,
                    len(analysis_text),
                    int((time.perf_counter() - started) * 1000))
        return context + "\n\n【局势分析（内部参考）】\n" + analysis_text[:400]
    except Exception:
        logger.exception("局势分析: 失败，降级为单次调用 (round=%d)", instance.round_number)
        return context


async def _call_stream_with_length_retry(
    llm_client: Any,
    instance: GameInstance,
    gm_prompt: str,
    context: str,
    narrative_max_tokens: int,
    on_delta,
    on_reset,
):
    """流式调用被截断时，按 1×、2×、4× 的预算独立重试。"""
    budgets = length_retry_budgets(narrative_max_tokens)
    for budget_index, current_max_tokens in enumerate(budgets):
        attempt_started = time.perf_counter()
        filt = _NarrationDeltaFilter(on_delta)
        try:
            response = await llm_client.call_stream(
                system_prompt=gm_prompt,
                user_message=context,
                temperature=0.7,
                max_tokens=current_max_tokens,
                on_delta=filt.feed,
            )
            await filt.flush()
            response.token_budget_initial = narrative_max_tokens
            response.token_budget_used = current_max_tokens
            return response
        except OutputTruncatedError:
            if budget_index + 1 >= len(budgets):
                logger.warning(
                    "流式输出截断且已达放大上限 (max_tokens=%d, round=%d)",
                    current_max_tokens,
                    instance.round_number,
                )
                raise
            bumped = budgets[budget_index + 1]
            logger.info(
                "叙事重试: 流式输出被截断，提高 max_tokens %d -> %d (round=%d, 本次耗时=%dms)",
                current_max_tokens,
                bumped,
                instance.round_number,
                int((time.perf_counter() - attempt_started) * 1000),
            )
            if on_reset:
                await on_reset()


async def call_llm_with_tag_retry(
    llm_client: Any,
    instance: GameInstance,
    gm_prompt: str,
    context: str,
    combat_model: str,
    dice_block: str,
    narrative_max_tokens: int,
    actions_text: str = "",
    *,
    on_delta=None,
    on_reset=None,
) -> tuple[Any, dict]:
    """调用 LLM，解析标签；若叙事违反骰子约束则最多重试 1 次。

    传入 on_delta 时走流式调用（call_stream），逐段叙事经 _NarrationDeltaFilter 过滤掉
    --- 之后的结构化标签后推给前端；骰子矛盾或截断重试前调 on_reset 让前端清空已显示
    的流式文本。流式与非流式截断均按 1×、2×、4× 预算重试，且不占用骰子矛盾重试次数。
    """
    response = None
    data: dict = {}
    stream = on_delta is not None
    max_budget_used = narrative_max_tokens
    dice_retry = 0
    retry_kind = ""
    protocol_retry_used = False
    started = time.perf_counter()
    while True:
        attempt_started = time.perf_counter()
        retry_context = context
        if retry_kind == "protocol":
            retry_context = append_protocol_repair_instruction(
                context,
                getattr(instance, "language", "zh-CN"),
            )
        elif retry_kind == "dice":
            retry_context = context + "\n\n" + localized_text(
                getattr(instance, "language", ""),
                {
                    "en": "Previous response contradicted the required dice/check result. Rewrite the narration and strictly follow the check outcome.",
                    "zh-CN": "⚠️ 上一轮回复与【系统检定·必须遵循】矛盾，请严格遵循检定结果重新叙述。",
                    "ja": "⚠️ 前の応答が【システム判定・必須遵守】の判定結果に矛盾しています。判定結果を厳守してナレーションを書き直してください。",
                },
            )
        if stream:
            response = await _call_stream_with_length_retry(
                llm_client,
                instance,
                gm_prompt,
                retry_context,
                narrative_max_tokens,
                on_delta,
                on_reset,
            )
        else:
            response = await llm_client.call(
                system_prompt=gm_prompt,
                user_message=retry_context,
                temperature=0.7,
                max_tokens=narrative_max_tokens,
            )
        max_budget_used = max(
            max_budget_used,
            int(getattr(response, "token_budget_used", 0) or 0),
        )
        response.language = getattr(instance, "language", "zh-CN")
        malformed_protocol = has_malformed_protocol_leak(response.content)
        if malformed_protocol and not protocol_retry_used:
            protocol_retry_used = True
            retry_kind = "protocol"
            logger.warning(
                "叙事重试: 检测到模型协议标签泄漏，按严格格式重试 (round=%d, 本次耗时=%dms)",
                instance.round_number,
                int((time.perf_counter() - attempt_started) * 1000),
            )
            if on_reset:
                await on_reset()
            continue
        response.content = normalize_tag_protocol(response.content)

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
        if dice_retry >= 1:
            logger.error("骰子约束连续2次矛盾，接受最后输出 (round=%d)", instance.round_number)
            break
        dice_retry += 1
        retry_kind = "dice"
        logger.warning(
            "叙事重试: 骰子约束矛盾，第%d次重试 (round=%d, 本次耗时=%dms)",
            dice_retry,
            instance.round_number,
            int((time.perf_counter() - attempt_started) * 1000),
        )
        if on_reset:
            await on_reset()

    logger.info(
        "GM 叙事: 完成 (round=%d, 总耗时=%dms)",
        instance.round_number,
        int((time.perf_counter() - started) * 1000),
    )
    await _compress_long_narration(
        llm_client, gm_prompt, response, actions_text, combat_model, narrative_max_tokens
    )
    response.token_budget_initial = narrative_max_tokens
    response.token_budget_used = max_budget_used
    return response, data


def apply_parsed_data_to_response(instance: GameInstance, response: Any, data: dict) -> None:
    """把解析出的标签数据落到 response 对象，供后续状态应用阶段使用。"""
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
            "标签解析成功 (round=%d): 玩家=%s, 场景=%s, 战利品=%d",
            instance.round_number,
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
                },
            )
            system_notices = getattr(response, "system_notices", None)
            if not isinstance(system_notices, list):
                system_notices = []
                response.system_notices = system_notices
            system_notices.append(_sync_notice)
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
        instance.set_tag_failure_streak(0)
