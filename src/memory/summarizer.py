"""双层摘要压缩 -- 每 N 轮触发 LLM 压缩对话历史。

滚动累积：新摘要融合旧摘要 + 新日志，避免早期剧情在摘要层丢失。
"""

from __future__ import annotations

import logging

from src.engine.game_instance import GameInstance
from src.engine.language import localized_text
from src.engine.modules import narrative_notes, progression_state
from src.llm.parser import sanitize_narration

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

_SUMMARY_PROMPT_NEW_JA = """あなたはゲームログの要約担当です。以下のゲームログを読み、ナレーションの要約と重要な事実のリストを生成してください。

出力形式（厳密なJSON）：
```json
{{
  "narrative": "流暢なナレーションの要約。最近起こった出来事を日本語で説明し、200文字以内に収める。",
  "key_facts": [
    {{"type": "タイプ(location_discovered/npc_status/item_acquired/decision_madeなど)", "content": "事実の説明"}}
  ]
}}
```

ゲームログ：
{log_text}
"""

_SUMMARY_PROMPT_ROLLING_JA = """あなたはゲームログの要約担当です。以下の過去の要約と新しいゲームログを読み、統合したナレーションの要約と重要な事実のリストを生成してください。

要件：
- 過去の要約の重要な情報は新しい要約へ引き継ぎ、重要な筋書きを失わないこと
- 新しく起きた出来事は過去の要約へ自然に繋がるようにすること
- ナレーションの要約は250文字以内に収めること
- key_facts は過去の要約で依然として有効な事実を残し、新しい事実を追加すること

出力形式（厳密なJSON）：
```json
{{
  "narrative": "統合後のナレーションの要約",
  "key_facts": [
    {{"type": "タイプ(location_discovered/npc_status/item_acquired/decision_madeなど)", "content": "事実の説明"}}
  ]
}}
```

過去の要約：
{previous_summary}

新しいゲームログ：
{log_text}
"""

_SUMMARY_PROMPT_NEW_DE = """Du bist der Zusammenfassungs-Assistent für TRPG-Sitzungsprotokolle. Lies das folgende Spielprotokoll und erstelle eine Erzählzusammenfassung sowie wichtige Fakten.

Ausgabeformat (striktes JSON):
```json
{{
  "narrative": "Eine flüssige deutsche Erzählzusammenfassung dessen, was zuletzt geschah, unter 160 Wörtern.",
  "key_facts": [
    {{"type": "Typ wie location_discovered/npc_status/item_acquired/decision_made", "content": "Faktenbeschreibung auf Deutsch"}}
  ]
}}
```

Spielprotokoll:
{log_text}
"""

_SUMMARY_PROMPT_ROLLING_DE = """Du bist der Zusammenfassungs-Assistent für TRPG-Sitzungsprotokolle. Lies die vorherige Zusammenfassung und das neue Spielprotokoll und erstelle eine zusammengeführte Erzählzusammenfassung sowie wichtige Fakten.

Anforderungen:
- Bewahre wichtige Handlungsstränge aus der vorherigen Zusammenfassung.
- Verbinde neue Ereignisse natürlich mit der vorherigen Zusammenfassung.
- Halte die Erzählzusammenfassung unter 180 deutschen Wörtern.
- Behalte noch gültige alte Fakten bei und ergänze neue Fakten.

Ausgabeformat (striktes JSON):
```json
{{
  "narrative": "zusammengeführte Erzählzusammenfassung auf Deutsch",
  "key_facts": [
    {{"type": "Typ wie location_discovered/npc_status/item_acquired/decision_made", "content": "Faktenbeschreibung auf Deutsch"}}
  ]
}}
```

Vorherige Zusammenfassung:
{previous_summary}

Neues Spielprotokoll:
{log_text}
"""


def build_summary_input(instance: GameInstance, last_n_rounds: int = 10) -> str:
    """从最近的日志中构建摘要输入。"""
    recent = instance.log[-last_n_rounds:]
    lines = []
    for entry in recent:
        actions = "; ".join(
            a.get("text", "") for a in entry.get("actions", [])
            if a.get("user_id") != "system"
        )
        gm = sanitize_narration(entry.get("gm_response", ""))
        lines.append(f"Round {entry.get('round','?')}\n玩家: {actions}\nGM: {gm}")
    return "\n\n".join(lines)


def needs_summary(instance: GameInstance, interval: int = 10) -> bool:
    """判断是否需要触发摘要压缩。"""
    return progression_state.round_value(instance) > 0 and progression_state.round_value(instance) % interval == 0


async def summarize(instance: GameInstance, llm_client, system_prompt: str,
                    max_tokens: int = 1024) -> None:
    """调用 LLM 生成摘要并更新 GameInstance。

    由每轮 LLM 调用后检查触发，不需要独立调度。
    滚动累积：有旧摘要时融合生成，无旧摘要时全新生成。
    """
    log_text = build_summary_input(instance)
    previous_summary = narrative_notes.summary(instance)
    prev_narrative = (
        sanitize_narration(previous_summary.get("narrative", ""))
        if previous_summary else ""
    )
    if prev_narrative:
        template = localized_text(instance.language, {
            "en": _SUMMARY_PROMPT_ROLLING_EN,
            "zh-CN": _SUMMARY_PROMPT_ROLLING,
            "ja": _SUMMARY_PROMPT_ROLLING_JA,
            "de": _SUMMARY_PROMPT_ROLLING_DE,
        })
        prompt = template.format(
            previous_summary=prev_narrative, log_text=log_text,
        )
    else:
        template = localized_text(instance.language, {
            "en": _SUMMARY_PROMPT_NEW_EN,
            "zh-CN": _SUMMARY_PROMPT_NEW,
            "ja": _SUMMARY_PROMPT_NEW_JA,
            "de": _SUMMARY_PROMPT_NEW_DE,
        })
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
            instance.set_summary_narrative(sanitize_narration(
                data.get("narrative", response.narration)
            ))
            instance.set_key_facts(data.get("key_facts", []))
        else:
            instance.set_summary_narrative(sanitize_narration(
                response.narration or response.content
            ))
            instance.set_key_facts([])
        logger.info("摘要生成完成: round=%d", progression_state.round_value(instance))
    except Exception:
        logger.exception("摘要生成失败")
        # 降级：保留旧摘要，不覆盖（旧摘要可能仍然有效）
        # 仅在没有任何旧摘要时才用 GM 回复兜底
        summary = narrative_notes.summary(instance)
        if not (summary and summary.get("narrative")):
            recent_gm = [
                sanitize_narration(e.get("gm_response", ""))
                for e in instance.log[-3:]
            ]
            instance.set_summary_narrative(" ... ".join(recent_gm)[:300])
