"""双层摘要压缩 -- 每 N 轮触发 LLM 压缩对话历史。

滚动累积：新摘要融合旧摘要 + 新日志，避免早期剧情在摘要层丢失。
"""

from __future__ import annotations

import logging

from src.engine.game_instance import GameInstance
from src.engine.language import is_english

logger = logging.getLogger("trpg")

_SUMMARY_PROMPT_NEW = """你是游戏日志的摘要员。请阅读以下游戏日志，生成一段叙事摘要和关键事实列表。

输出格式（严格 JSON）：
```json
{{
  "narrative": "一段流畅的叙事摘要，用中文描述最近发生了什么，控制在 200 字以内。",
  "key_facts": [
    {{"type": "类型(location_discovered/npc_status/item_acquired/decision_made等)", "content": "事实描述"}}
  ]
}}
```

游戏日志：
{log_text}
"""

_SUMMARY_PROMPT_ROLLING = """你是游戏日志的摘要员。请阅读以下旧摘要和新游戏日志，生成一段融合后的叙事摘要和关键事实列表。

要求：
- 旧摘要中的关键信息应延续到新摘要中，不要丢失重要剧情脉络
- 新发生的事件应自然衔接旧摘要
- 叙事摘要控制在 250 字以内
- key_facts 保留旧摘要中仍然有效的事实，并补充新事实

输出格式（严格 JSON）：
```json
{{
  "narrative": "融合后的叙事摘要",
  "key_facts": [
    {{"type": "类型(location_discovered/npc_status/item_acquired/decision_made等)", "content": "事实描述"}}
  ]
}}
```

旧摘要：
{previous_summary}

新游戏日志：
{log_text}
"""

_SUMMARY_PROMPT_NEW_EN = """You are a TRPG session log summarizer. Read the following game log and produce a narrative summary and key facts.

Output format (strict JSON):
```json
{{
  "narrative": "A smooth English narrative summary of what recently happened, under 160 words.",
  "key_facts": [
    {{"type": "type such as location_discovered/npc_status/item_acquired/decision_made", "content": "fact description in English"}}
  ]
}}
```

Game log:
{log_text}
"""

_SUMMARY_PROMPT_ROLLING_EN = """You are a TRPG session log summarizer. Read the previous summary and new game log, then produce a merged narrative summary and key facts.

Requirements:
- Preserve important plot threads from the previous summary.
- Connect new events naturally to the previous summary.
- Keep the narrative summary under 180 English words.
- Keep still-valid old key facts and add new facts.

Output format (strict JSON):
```json
{{
  "narrative": "merged narrative summary in English",
  "key_facts": [
    {{"type": "type such as location_discovered/npc_status/item_acquired/decision_made", "content": "fact description in English"}}
  ]
}}
```

Previous summary:
{previous_summary}

New game log:
{log_text}
"""


def build_summary_input(instance: GameInstance, last_n_rounds: int = 10) -> str:
    """从最近的日志中构建摘要输入。"""
    recent = instance.log[-last_n_rounds:]
    lines = []
    for entry in recent:
        actions = "; ".join(a.get("text", "") for a in entry.get("actions", []))
        gm = entry.get("gm_response", "")
        lines.append(f"Round {entry.get('round','?')}\n玩家: {actions}\nGM: {gm}")
    return "\n\n".join(lines)


def needs_summary(instance: GameInstance, interval: int = 10) -> bool:
    """判断是否需要触发摘要压缩。"""
    return instance.round_number > 0 and instance.round_number % interval == 0


async def summarize(instance: GameInstance, llm_client, system_prompt: str,
                    max_tokens: int = 400) -> None:
    """调用 LLM 生成摘要并更新 GameInstance。

    由每轮 LLM 调用后检查触发，不需要独立调度。
    滚动累积：有旧摘要时融合生成，无旧摘要时全新生成。
    """
    log_text = build_summary_input(instance)
    prev_narrative = instance.summary.get("narrative", "") if instance.summary else ""
    if prev_narrative:
        template = _SUMMARY_PROMPT_ROLLING_EN if is_english(instance.language) else _SUMMARY_PROMPT_ROLLING
        prompt = template.format(
            previous_summary=prev_narrative, log_text=log_text,
        )
    else:
        template = _SUMMARY_PROMPT_NEW_EN if is_english(instance.language) else _SUMMARY_PROMPT_NEW
        prompt = template.format(log_text=log_text)

    try:
        response = await llm_client.call(
            system_prompt=system_prompt,
            user_message=prompt,
            temperature=0.3,  # 摘要用低温度
            max_tokens=max_tokens,
        )
        # 解析 JSON（复用 generation/creator 的 parse_json）
        from src.generation.creator import parse_json
        data = parse_json(response.content)
        if data:
            instance.summary["narrative"] = data.get("narrative", response.narration)
            instance.key_facts = data.get("key_facts", [])
        else:
            instance.summary["narrative"] = response.narration or response.content
            instance.key_facts = []
        logger.info("摘要生成完成: round=%d", instance.round_number)
    except Exception:
        logger.exception("摘要生成失败")
        # 降级：保留旧摘要，不覆盖（旧摘要可能仍然有效）
        # 仅在没有任何旧摘要时才用 GM 回复兜底
        if not (instance.summary and instance.summary.get("narrative")):
            recent_gm = [e.get("gm_response", "") for e in instance.log[-3:]]
            instance.summary["narrative"] = " ... ".join(recent_gm)[:300]
