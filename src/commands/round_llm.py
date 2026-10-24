"""回合中的 LLM 调用、重试与回复解析。"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.commands.narrative_application import apply_parsed_data_to_response as _apply_parsed
from src.commands.round_helpers import should_multi_step, validate_dice_constraint
from src.commands.protocol_repair import append_protocol_repair_instruction
from src.commands.tag_json import safe_parse_json
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance
from src.engine.health import record_health_event
from src.engine.language import localized_text, normalize_language
from src.engine.modules import progression_state
from src.engine.story import StoryFormatError, StorySink, StoryStream, read_story_mode
from src.commands.story_output import bound_story_response, decode_story_response
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
    "de": {"trigger": 1200, "soft": 900, "combat": 1100},
}
_NARRATION_COMPRESS_MIN_TOKENS = 1024
_NARRATION_COMPRESS_MAX_TOKENS = 2048
# GM prompt 中叙事风格 section 的多语言标题（由 src/content/gm_style.py 渲染）。
_NARRATION_STYLE_HEADINGS = (
    "## GM Narration Style",
    "## GM 叙事风格",
    "## GM ナラティブスタイル",
    "## GM-Erzählstil",
)


def _narration_len(text: str) -> int:
    return len(str(text or "").replace("\n", "").strip())


def _replace_narration_in_content(content: str, narration: str) -> str:
    if "---" not in content:
        return narration
    return f"{narration.strip()}\n---{content.split('---', 1)[1]}"


_THINK_OPEN_TAG = "<think>"
_THINK_CLOSE_TAG = "</think>"


def _partial_think_tag_len(text: str, tag: str) -> int:
    """返回 text 尾部恰好是 tag 真前缀的最长长度（0 表示没有）。"""
    for length in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0


class _NarrationDeltaFilter:
    """流式转发叙事正文，遇到 '---' 分隔符后停止转发。

    LLM 叙事输出形如 ``<叙事>\n---\n<结构化标签>``，--- 之后的标签是给解析器用的，
    不能推给前端。本类逐段接收 call_stream 的 delta，只把分隔符之前的部分经 on_delta
    推出；为避免 '---' 被拆到多个 chunk 中间，最多暂存 len(SEPARATOR)-1 个字符，
    flush() 时把剩余暂存一次性发出（纯叙事、无分隔符的场景）。

    同时剔除模型混入正文流中的 reasoning 思考块：``<think>…</think>``（含多个块、
    标签跨 chunk）与孤立 ``</think>`` 一律不转发；未闭合的 think 内容直到流结束
    都不会经 flush() 吐给玩家。
    """

    SEPARATOR = "---"
    PROTOCOL_HOLD_CHARS = 128

    def __init__(self, on_delta):
        self._on_delta = on_delta
        self._buf = ""
        self._sent = 0
        self._sealed = False
        self._inside_think = False
        self._think_probe = ""

    def _strip_think(self, text: str) -> str:
        """剔除增量中的 <think>…</think> 思考内容，标签允许跨 chunk。

        无法判定是否属于标签开头的尾部前缀暂存在 _think_probe，与下一批增量
        拼接后再判定；未闭合 think 的内容一律丢弃，不进入下游缓冲。
        """
        text = self._think_probe + text
        self._think_probe = ""
        parts: list[str] = []
        i = 0
        while i < len(text):
            if self._inside_think:
                close_idx = text.find(_THINK_CLOSE_TAG, i)
                if close_idx < 0:
                    hold = _partial_think_tag_len(text[i:], _THINK_CLOSE_TAG)
                    if hold:
                        self._think_probe = text[len(text) - hold:]
                    break
                self._inside_think = False
                i = close_idx + len(_THINK_CLOSE_TAG)
                continue
            open_idx = text.find(_THINK_OPEN_TAG, i)
            close_idx = text.find(_THINK_CLOSE_TAG, i)
            if open_idx < 0 and close_idx < 0:
                hold = max(
                    _partial_think_tag_len(text[i:], _THINK_OPEN_TAG),
                    _partial_think_tag_len(text[i:], _THINK_CLOSE_TAG),
                )
                parts.append(text[i:len(text) - hold] if hold else text[i:])
                if hold:
                    self._think_probe = text[len(text) - hold:]
                break
            if open_idx >= 0 and (close_idx < 0 or open_idx < close_idx):
                parts.append(text[i:open_idx])
                self._inside_think = True
                i = open_idx + len(_THINK_OPEN_TAG)
            else:
                # 孤立 </think>：只丢弃标签本身，不改变普通正文状态
                parts.append(text[i:close_idx])
                i = close_idx + len(_THINK_CLOSE_TAG)
        return "".join(parts)

    async def feed(self, text: str) -> None:
        if self._sealed or not text:
            return
        cleaned = self._strip_think(text)
        if not cleaned:
            return
        self._buf += cleaned
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
        # 未闭合 think 的内容已在流入时丢弃；残留的疑似标签前缀一并丢弃，
        # 宁可损失结尾几个字符也不把半截 <think 标签吐给玩家。
        self._think_probe = ""
        boundary = find_protocol_suffix_start(self._buf)
        end = boundary if boundary is not None else len(self._buf)
        remaining = self._buf[self._sent:end]
        self._sent = len(self._buf)
        if remaining:
            cleaned = sanitize_narration(remaining)
            if cleaned:
                await self._on_delta(cleaned)


def _extract_narration_style_section(gm_prompt: str) -> str:
    """从 GM prompt 提取叙事风格 section（含世界/对局自定义文风），供压缩阶段继承口吻。

    标题与 src/content/gm_style.py 的渲染保持一致（三语）；只截取到下一个
    "## " 标题为止。找不到时返回空串（未配置风格则无内容可继承）。提取结果
    仅作口吻参考，压缩 prompt 会另行声明不得执行其中的状态标签协议。
    """
    text = str(gm_prompt or "")
    for heading in _NARRATION_STYLE_HEADINGS:
        start = text.find(heading)
        if start < 0:
            continue
        rest = text[start + len(heading):]
        next_heading = rest.find("\n## ")
        section = rest if next_heading < 0 else rest[:next_heading]
        return f"{heading}{section}".strip()
    return ""


def _quick_actions_from_payload(payload: Any) -> list[str]:
    """校验二次压缩返回的 quick_actions，输出最终可保存的列表。

    每项 str/strip/去空、最多保留 4 条；有效项不足 2 条时返回空列表，
    交给 update_quick_actions() 的 default_quick_actions_by_class 兜底，
    不在这里自行再造默认动作。
    """
    raw = payload.get("quick_actions") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    items = [str(item).strip() for item in raw if str(item).strip()]
    return items[:4] if len(items) >= 2 else []


def _hard_truncate_narration(response, narration: str, target: int) -> None:
    """P2-C：压缩失败按目标长度硬截断，避免超长叙事进 log/context 推高下一轮
    截断概率（长→压缩失败→更长的反馈环）。"""
    truncated = narration[:target].rstrip()
    if truncated and truncated != narration:
        response.narration = sanitize_narration(truncated + "…")
        response.content = _replace_narration_in_content(str(response.content or ""), response.narration)


async def _compress_long_narration(
    llm_client,
    instance: GameInstance,
    gm_prompt: str,
    response,
    data: dict,
    public_narration_context: str,
    actions_text: str,
    combat_model: str,
    max_tokens: int,
) -> None:
    """超长叙事二次压缩（#272）：压缩正文的同时同步修正 QUICK_ACTIONS。

    只允许改动 response.narration / response.content 的正文部分 /
    data["quick_actions"]；authoritative 状态（HP/金币/战斗/检定等）一律不动，
    也不重新 parse 标签。压缩输入只有原正文、原 QUICK_ACTIONS、最近公开剧情
    和 GM 叙事风格，绝不接入完整内部 context。
    """
    narration = str(response.narration or "").strip()
    lang = normalize_language(getattr(response, "language", ""))
    limits = _NARRATION_LIMITS.get(lang, _NARRATION_LIMITS["zh-CN"])
    is_en = lang == "en"
    is_de = lang == "de"
    if _narration_len(narration) <= limits["trigger"]:
        return
    combat_words = ("战斗", "攻击", "砍", "刺", "射", "突袭", "格挡", "防御", "回避")
    is_combat = combat_model != "none" and any(word in actions_text for word in combat_words)
    target = limits["combat"] if is_combat else limits["soft"]
    style_section = _extract_narration_style_section(gm_prompt)
    old_quick_actions = [str(item) for item in (data.get("quick_actions") or [])]
    if is_en:
        prompt = (
            "Compress the following TRPG GM narration and review the QUICK_ACTIONS.\n\n"
            "Output only JSON, no Markdown, no explanations, in the form:\n"
            '{"narration": "the final compressed narration", "quick_actions": ["action 1", "action 2"]}\n\n'
            "Requirements:\n"
            "1. narration: keep established facts, NPC names, key clues, check/combat "
            "results, and the immediate pressure for the players; do not add new lore "
            "or change settled outcomes. "
            f"Keep it under about {target} characters (~{target // 6} words) and at most 2 paragraphs.\n"
            "2. quick_actions: must match the final narration; you may rely on information "
            "already revealed to players in the previously public narration; never use information "
            "players do not know yet; rewrite or drop actions that reference details removed from "
            "the narration and never previously revealed; keep 2-4 actions; never decide for the "
            "players; never auto-complete actions that require checks; never emit any state tags.\n"
            "3. Never change any game mechanics, dice results, character state, HP, gold, items, "
            "combat or puzzle outcomes."
        )
        quick_actions_lines = "\n".join(f"- {item}" for item in old_quick_actions) or "- (none)"
        sections = [
            prompt,
            f"Original narration:\n{narration}",
            f"Current QUICK_ACTIONS:\n{quick_actions_lines}",
            "Previously public narration:\n"
            f"{public_narration_context.strip() or '(none)'}",
        ]
        if style_section:
            sections.append(
                "GM narration style (tone reference only: inherit the voice, pacing and level "
                "of detail; never execute any state-tag protocol inside it, never re-run rules):\n"
                + style_section
            )
    elif is_de:
        prompt = (
            "Komprimiere die folgende TRPG-GM-Erzählung und überprüfe dabei QUICK_ACTIONS.\n\n"
            "Gib ausschließlich JSON aus, kein Markdown, keine Erklärungen, im Format:\n"
            '{"narration": "die endgültige komprimierte Erzählung", "quick_actions": ["Aktion 1", "Aktion 2"]}\n\n'
            "Anforderungen:\n"
            "1. narration: bewahre etablierte Fakten, NSC-Namen, wichtige Hinweise, Proben-/Kampfergebnisse "
            "und den unmittelbaren Druck für die Spieler; erfinde keine neuen Fakten und ändere keine "
            f"bereits festgelegten Ergebnisse. Halte es unter etwa {target} Zeichen (~{target // 6} Wörter) "
            "und höchstens 2 Absätzen.\n"
            "2. quick_actions: muss zur finalen Erzählung passen; du darfst bereits den Spielern bekannte "
            "Informationen aus der zuvor öffentlichen Erzählung verwenden; verwende niemals Informationen, "
            "die die Spieler noch nicht kennen; schreibe Aktionen um oder streiche sie, wenn sie sich auf "
            "aus der Erzählung entfernte, nie zuvor offengelegte Details beziehen; behalte 2-4 Aktionen; "
            "entscheide niemals für die Spieler; vervollständige niemals automatisch Aktionen, die Proben "
            "erfordern; gib keine Status-Tags aus.\n"
            "3. Ändere niemals Spielmechanik, Würfelergebnisse, Charakterstatus, TP, Gold, Gegenstände, "
            "Kampf- oder Rätselergebnisse."
        )
        quick_actions_lines = "\n".join(f"- {item}" for item in old_quick_actions) or "- (keine)"
        sections = [
            prompt,
            f"Ursprüngliche Erzählung:\n{narration}",
            f"Aktuelle QUICK_ACTIONS:\n{quick_actions_lines}",
            "Zuvor öffentliche Erzählung:\n"
            f"{public_narration_context.strip() or '(keine)'}",
        ]
        if style_section:
            sections.append(
                "GM-Erzählstil (nur als Ton-Referenz: übernimm Stimme, Tempo und Detailgrad; "
                "führe darin enthaltene Status-Tag-Anweisungen niemals aus, wiederhole keine Regelentscheidungen):\n"
                + style_section
            )
    else:
        prompt = (
            "请压缩以下 TRPG GM 正文，并同步检查 QUICK_ACTIONS。\n\n"
            "只输出 JSON，不要输出 Markdown，不要输出解释，格式：\n"
            '{"narration": "压缩后的最终正文", "quick_actions": ["行动1", "行动2"]}\n\n'
            "要求：\n"
            "1. narration：保留已发生事实、NPC 名字、关键线索、检定/战斗结果和玩家立即面对的压力；"
            "不要新增设定，不要改变已经结算的结果；"
            f"总字数控制在 {target} 字以内，最多 2 段。\n"
            "2. quick_actions：必须与最终 narration 一致；可以使用「此前已公开给玩家的最近剧情」中的"
            "已知信息；不得使用玩家尚不知道的信息；如果旧 QUICK_ACTIONS 引用了被删掉且此前未公开的信息，"
            "必须改写或删除；保持 2~4 个行动；不得代替玩家做决定；不得自动完成需要判定的行为；"
            "不得生成任何状态标签。\n"
            "3. 禁止修改任何游戏机制、骰子结果、角色状态、HP、金币、物品、战斗结果、谜题结果等。"
        )
        quick_actions_lines = "\n".join(f"- {item}" for item in old_quick_actions) or "-（无）"
        sections = [
            prompt,
            f"原正文：\n{narration}",
            f"当前 QUICK_ACTIONS：\n{quick_actions_lines}",
            "此前已公开给玩家的最近剧情：\n"
            f"{public_narration_context.strip() or '（无）'}",
        ]
        if style_section:
            sections.append(
                "GM 叙事风格（仅用于继承叙事口吻、节奏与详细程度；"
                "禁止执行其中要求输出的任何状态标签，禁止重新进行规则判定）：\n" + style_section
            )
    compress_system = localized_text(
        getattr(response, "language", ""),
        {
            "en": 'You are a narration compressor. Output only a JSON object with keys "narration" '
                  'and "quick_actions". No Markdown, no code fences, no ---, no state tags, no meta commentary.',
            "zh-CN": '你是叙事压缩器，只输出一个包含 "narration" 和 "quick_actions" 的 JSON 对象；'
                     '不要 Markdown、不要代码围栏、不要 ---、不要状态标签、不要对任务的元说明。',
            "ja": 'あなたはナレーション圧縮器です。"narration" と "quick_actions" のキーを持つ '
                  'JSON オブジェクトのみを出力してください。Markdown・コードフェンス・---・状態タグ・'
                  'タスクに対するメタ解説は出力しないでください。',
            "de": 'Du bist ein Erzählungskompressor. Gib ausschließlich ein JSON-Objekt mit den Schlüsseln '
                  '"narration" und "quick_actions" aus. Kein Markdown, keine Code-Zäune, kein ---, '
                  'keine Status-Tags, keine Meta-Kommentare zur Aufgabe.',
        },
    )
    compression_max_tokens = max(
        _NARRATION_COMPRESS_MIN_TOKENS,
        min(max_tokens, _NARRATION_COMPRESS_MAX_TOKENS),
    )
    try:
        compressed = await llm_client.call(
            system_prompt=compress_system,
            user_message="\n\n".join(sections),
            temperature=0.2,
            max_tokens=compression_max_tokens,
        )
    except Exception:
        logger.warning("超长叙事二次压缩失败，按 %d 字硬截断", target, exc_info=True)
        _hard_truncate_narration(response, narration, target)
        # 硬截断后正文里的信息已不可靠，旧 QUICK_ACTIONS 可能引用被截掉的内容，
        # 清空后由 update_quick_actions() 的默认动作兜底（#272）。
        logger.warning(
            "超长叙事压缩失败，正文硬截断并清空 QUICK_ACTIONS (round=%d)",
            progression_state.round_value(instance),
        )
        data["quick_actions"] = []
        return
    try:
        payload = safe_parse_json(str(compressed.content or compressed.narration or ""))
    except ValueError:
        payload = None
    new_narration = str(payload.get("narration") or "").strip() if isinstance(payload, dict) else ""
    if not new_narration:
        # JSON 无效或空 narration 视同压缩失败：硬截断兜底，且不保留旧 QUICK_ACTIONS。
        logger.warning(
            "超长叙事压缩返回无效，正文硬截断并清空 QUICK_ACTIONS (round=%d)",
            progression_state.round_value(instance),
        )
        _hard_truncate_narration(response, narration, target)
        data["quick_actions"] = []
        return
    if _narration_len(new_narration) >= _narration_len(narration):
        logger.info("超长叙事压缩未变短，保留原文与原 QUICK_ACTIONS (round=%d)", progression_state.round_value(instance))
        return
    response.narration = sanitize_narration(new_narration)
    response.content = _replace_narration_in_content(str(response.content or ""), response.narration)
    new_quick_actions = _quick_actions_from_payload(payload)
    if new_quick_actions != old_quick_actions:
        logger.info(
            "超长叙事压缩后同步 QUICK_ACTIONS (round=%d, before=%d, after=%d)",
            progression_state.round_value(instance),
            len(old_quick_actions),
            len(new_quick_actions),
        )
    data["quick_actions"] = new_quick_actions


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
        logger.info("局势分析: 未触发，跳过 (round=%d)", progression_state.round_value(instance))
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
                    progression_state.round_value(instance),
                    len(analysis_text),
                    int((time.perf_counter() - started) * 1000))
        return context + "\n\n【局势分析（内部参考）】\n" + analysis_text[:400]
    except Exception:
        logger.exception("局势分析: 失败，降级为单次调用 (round=%d)", progression_state.round_value(instance))
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
        story_stream = StoryStream(instance, on_delta) if read_story_mode(instance) == "dialogue" else None
        filt = _NarrationDeltaFilter(story_stream.feed if story_stream else on_delta)
        try:
            response = await llm_client.call_stream(
                system_prompt=gm_prompt,
                user_message=context,
                temperature=0.7,
                max_tokens=current_max_tokens,
                on_delta=filt.feed,
            )
            await filt.flush()
            if story_stream is not None:
                await story_stream.flush()
                response.story = story_stream.last_story
            response.token_budget_initial = narrative_max_tokens
            response.token_budget_used = current_max_tokens
            return response
        except OutputTruncatedError:
            if budget_index + 1 >= len(budgets):
                logger.warning(
                    "流式输出截断且已达放大上限 (max_tokens=%d, round=%d)",
                    current_max_tokens,
                    progression_state.round_value(instance),
                )
                raise
            bumped = budgets[budget_index + 1]
            logger.info(
                "叙事重试: 流式输出被截断，提高 max_tokens %d -> %d (round=%d, 本次耗时=%dms)",
                current_max_tokens,
                bumped,
                progression_state.round_value(instance),
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
    public_narration_context: str = "",
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
    story_retry_used = False
    dialogue = read_story_mode(instance) == "dialogue"
    streamed_story = None
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
                    "de": "⚠️ Die vorherige Antwort widersprach dem erforderlichen Würfel-/Probenergebnis. Schreibe die Erzählung neu und folge dabei strikt dem Probenergebnis.",
                },
            )
        elif retry_kind == "story":
            retry_context = context + (
                "\n\nThe previous public body did not follow character-dialogue format. "
                "Rewrite it as one narration/dialogue JSON object per line, using only roster IDs "
                "or new:local_handle, followed by the required --- state-tag block."
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
                progression_state.round_value(instance),
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
        if dialogue:
            streamed_story = getattr(response, "story", None) if stream else None
            try:
                decode_story_response(instance, response, strict=not story_retry_used)
            except StoryFormatError:
                if story_retry_used:
                    raise
                story_retry_used = True
                retry_kind = "story"
                if on_reset:
                    await on_reset()
                continue
        if not data.get("state_update") and not data.get("plot_update"):
            try:
                json_data = safe_parse_json(response.content)
                if json_data:
                    logger.info("标签无结果，JSON 回退成功 (round=%d)", progression_state.round_value(instance))
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
            logger.error("骰子约束连续2次矛盾，接受最后输出 (round=%d)", progression_state.round_value(instance))
            break
        dice_retry += 1
        retry_kind = "dice"
        logger.warning(
            "叙事重试: 骰子约束矛盾，第%d次重试 (round=%d, 本次耗时=%dms)",
            dice_retry,
            progression_state.round_value(instance),
            int((time.perf_counter() - attempt_started) * 1000),
        )
        if on_reset:
            await on_reset()

    logger.info(
        "GM 叙事: 完成 (round=%d, 总耗时=%dms)",
        progression_state.round_value(instance),
        int((time.perf_counter() - started) * 1000),
    )
    if dialogue:
        limits = _NARRATION_LIMITS.get(normalize_language(instance.language), _NARRATION_LIMITS["zh-CN"])
        bound_story_response(response, data, trigger=limits["trigger"], limit=limits["combat"])
        if stream and streamed_story != response.story:
            if on_reset:
                await on_reset()
            await on_delta(response.narration)
            if isinstance(on_delta, StorySink):
                await on_delta.story_update(response.story)
    else:
        await _compress_long_narration(
            llm_client, instance, gm_prompt, response, data,
            public_narration_context, actions_text, combat_model, narrative_max_tokens,
        )
    response.token_budget_initial = narrative_max_tokens
    response.token_budget_used = max_budget_used
    return response, data


def apply_parsed_data_to_response(instance: GameInstance, response: Any, data: dict) -> None:
    """把解析出的标签数据落到 response 对象，供后续状态应用阶段使用。

    实现已泛化到 `narrative_application`（GAL 轨道 P5），此处 re-export
    维持回合管线的既有 import 路径。
    """
    return _apply_parsed(instance, response, data)
