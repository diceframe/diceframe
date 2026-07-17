"""Context 拼接器 —— 按 TokenBudget 优先级硬分配，将游戏状态拼接为 LLM 输入。"""

from __future__ import annotations

import json
import logging
import os

from src.engine.game_instance import GameInstance
from src.engine.language import is_english

logger = logging.getLogger("trpg")


# ---------- Token 预算分配（按模型自适应）----------

# 模型上下文窗口检测表（保守估计，留 20% 余量）
_MODEL_CONTEXT_PRESETS: dict[str, int] = {
    "deepseek": 48640,      # DeepSeek 64K (~48640 chars / 2 tokens per char)
    "qwen": 48640,           # 通义千问 64K
    "gpt-4-turbo": 48640,   # GPT-4-turbo 128K tokens
    "gpt-4": 32640,         # GPT-4 8K → 保守 ~16384 chars 的一半用于输出
    "gpt-3.5": 16320,       # GPT-3.5 4K
    "claude": 65536,        # Claude 100K+
    "glm": 48640,           # ChatGLM 128K
}

_FALLBACK_MAX_CHARS = int(os.getenv("TRPG_MAX_CONTEXT_CHARS", 48000))

# 预算比例（占总预算的百分比）
_BUDGET_SYSTEM_PROMPT = 0.20   # 系统提示词（由 API 的 system role 承载，这里只预留预算，不重复塞进 user context）
_BUDGET_GAME_STATE = 0.12      # 游戏状态 JSON（精简视图，无 log/health_events）
_BUDGET_LOREBOOK = 0.20        # Lorebook 条目
_BUDGET_SUMMARY = 0.08         # 最新摘要 + 关键事实
_BUDGET_MEMORY = 0.06          # 长期记忆
_BUDGET_HISTORY_MIN = 0.22     # 对话历史最小比例
# 剩余 ~6% 用于玩家消息和分隔符


def _detect_max_chars(provider_name: str = "") -> int:
    """根据模型名称检测上下文窗口大小。若设定了环境变量 TRPG_MAX_CONTEXT_CHARS 则直接使用。"""
    env_override = os.getenv("TRPG_MAX_CONTEXT_CHARS")
    if env_override:
        return int(env_override)
    name_lower = provider_name.lower()
    for key, limit in _MODEL_CONTEXT_PRESETS.items():
        if key in name_lower:
            logger.debug("模型上下文检测: %s → %d chars", provider_name, limit)
            return limit
    return _FALLBACK_MAX_CHARS


def _estimate_tokens(text: str) -> int:
    """估算 token 数：CJK 字符约 1 token/字，其余约 4 字符/token。"""
    cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    return max(1, cjk + (len(text) - cjk) // 4)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3] + "..."


_GM_COMPACT_KEYS = ("npc", "location", "scene", "item", "gold", "hp", "combat")

def _is_key_round(entry: dict) -> bool:
    gm = (entry.get("gm_response", "") or "").lower()
    tags = entry.get("tags_summary", {}).get("tags", [])
    tag_str = " ".join(tags).lower()
    keywords = ("战斗", "攻击", "受伤", "倒地", "购买", "花费", "金币", "交易",
                "谜题", "机关", "登场", "第一次", "发现", "线索", "秘密", "真相",
                "combat", "attack", "damage", "gold", "pay", "puzzle", "clue",
                "hp", "扣", "得到", "获得", "解锁")
    if any(kw in gm for kw in keywords):
        return True
    if any(kw in tag_str for kw in ("hp:", "gold:", "pay:", "npc:", "puzzle:", "quest:", "xp:")):
        return True
    return False

def _format_history(log: list[dict], max_chars: int, english: bool = False) -> str:
    MIN_KEEP = 5
    if not log:
        return ""
    entries = list(log)
    keep_full = entries[-MIN_KEEP:]
    eligible = entries[:-MIN_KEEP]

    key_rounds: dict[int, int] = {}
    for i, entry in enumerate(eligible):
        if _is_key_round(entry):
            key_rounds[i] = 1

    selected: dict[int, str] = {}
    used = 0

    def _entry_full(entry: dict) -> str:
        actions_text = "; ".join(a.get("text", "") for a in entry.get("actions", []))
        gm_text = entry.get("gm_response", "")
        player_label = "Players" if english else "玩家"
        return f"[Round {entry.get('round','?')}]\n{player_label}: {actions_text}\nGM: {gm_text}"

    def _entry_slim(entry: dict) -> str:
        actions_text = "; ".join(a.get("text", "") for a in entry.get("actions", []))
        gm_text = entry.get("gm_response", "")
        player_label = "Players" if english else "玩家"
        return f"[Round {entry.get('round','?')}] {player_label}: {actions_text} | GM: {gm_text[:80]}"

    for entry in keep_full:
        line = _entry_full(entry)
        selected[entry.get("round", len(selected))] = line
        used += len(line)

    for i, entry in enumerate(eligible):
        if i not in key_rounds:
            continue
        if used >= max_chars:
            break
        line = _entry_full(entry)
        used += len(line)
        selected[entry.get("round", len(selected))] = line

    for i, entry in enumerate(eligible):
        if i in key_rounds or used >= max_chars:
            continue
        line = _entry_slim(entry)
        used += len(line)
        selected[entry.get("round", len(selected))] = line

    sorted_lines = [line for _, line in sorted(selected.items())]
    return "\n\n".join(sorted_lines)


async def build_context(
    instance: GameInstance,
    gm_prompt_filled: str,
    lorebook_entries: list[dict],
    player_message: str,
    memory_store=None,
    platform: str = "",
    provider_name: str = "",
    lorebook_budget: int = 0,
) -> str:
    """将游戏状态拼接为完整的 LLM 上下文。

    Args:
        instance: 当前游戏实例
        gm_prompt_filled: 已填充占位符的 GM 系统提示词
        lorebook_entries: 匹配到的 Lorebook 条目列表（已按 tier 排序）
        player_message: 当前玩家说的话
        memory_store: MemoryStore 实例，用于召回长期记忆
        platform: 平台名，用于模型检测（可选）

    Returns:
        完整的上下文字符串
    """
    max_total = _detect_max_chars(provider_name)
    english = is_english(getattr(instance, "language", "zh-CN"))

    # 按比例分配预算
    budget_system = int(max_total * _BUDGET_SYSTEM_PROMPT)
    budget_state = int(max_total * _BUDGET_GAME_STATE)
    budget_lorebook = int(max_total * _BUDGET_LOREBOOK)
    if lorebook_budget > 0:
        budget_lorebook = min(budget_lorebook, lorebook_budget)
    budget_summary = int(max_total * _BUDGET_SUMMARY)
    budget_memory = int(max_total * _BUDGET_MEMORY)
    budget_history_base = max(int(max_total * _BUDGET_HISTORY_MIN), max_total // 6)

    parts: list[str] = []
    reserved_system_chars = min(len(gm_prompt_filled), budget_system)

    # 1. 游戏状态（LLM 精简视图，含属性修正）
    state = instance.to_llm_view()
    state_json = json.dumps(state, ensure_ascii=False)
    # 超预算时丢弃 inventory（最小关键字段），避免硬截断 JSON 破坏语法
    if len(state_json) > budget_state:
        for pdata in state.get("players", {}).values():
            pdata.get("character_sheet", {}).pop("inventory", None)
        state_json = json.dumps(state, ensure_ascii=False)
    state_json = _truncate(state_json, budget_state)
    parts.append(("## Game State" if english else "【游戏状态】") + f"\n{state_json}")

    # 2. Lorebook 条目（核心 NPC/场景优先）
    lorebook_text = ""
    trimmed: list[str] = []
    for entry in lorebook_entries:
        visible = entry.get("visible_to", [])
        vis_hint = ""
        if visible:
            vis_hint = f" [visible only to {','.join(visible)}]" if english else f" [仅{','.join(visible)}可见]"
        entry_text = f"[{entry.get('type', 'other')}]{vis_hint} {entry.get('name', '')}: {entry.get('content', '')}"
        if len(lorebook_text) + len(entry_text) > budget_lorebook:
            trimmed.append(entry.get("name", entry.get("id", "?")))
            continue
        lorebook_text += entry_text + "\n"
    if trimmed:
        logger.info("Lorebook 预算裁剪: 丢弃 %d 条 (%s), budget=%d",
                     len(trimmed), ", ".join(trimmed[:5]), budget_lorebook)
    if lorebook_text:
        parts.append(("## World Knowledge" if english else "【世界观知识】") + f"\n{lorebook_text.strip()}")

    # 3. 摘要 + 关键事实
    summary = instance.summary.get("narrative", "")
    summary_section_parts: list[str] = []
    if summary:
        summary_section_parts.append(_truncate(summary, budget_summary))
    if instance.key_facts:
        facts_lines = [
            f"- {f.get('content', '')}"
            for f in instance.key_facts
            if isinstance(f, dict) and f.get("content")
        ]
        if facts_lines:
            facts_text = _truncate("\n".join(facts_lines), budget_summary)
            summary_section_parts.append(facts_text)
    if summary_section_parts:
        parts.append(("## Recent Events" if english else "【近期经历】") + "\n" + "\n".join(summary_section_parts))

    # D1: 已确认事项（防 GM 重复讨论）
    if instance.confirmed_items:
        confirmed_text = ("; ".join(instance.confirmed_items[-20:]) if english else "、".join(instance.confirmed_items[-20:]))
        heading = "## Confirmed Items\nIf players ask about the same thing again, move forward instead of re-explaining." if english else "【已确认事项】（玩家再问相同内容时直接推进，不要重复解释）"
        parts.append(f"{heading}\n{confirmed_text}")

    # 4. 长期记忆召回（召回源：玩家消息 + 最近 3 轮 GM 回复，提高命中率）
    if memory_store:
        try:
            from src.memory.recall import recall_and_format
            recall_source = player_message
            recent_log = instance.log[-3:] if instance.log else []
            for entry in recent_log:
                gm_resp = entry.get("gm_response", "")
                if gm_resp:
                    recall_source += "\n" + gm_resp
            memory_text = await recall_and_format(
                memory_store, str(instance.game_key), recall_source, limit=8,
            )
            if memory_text:
                memory_text = _truncate(memory_text, budget_memory)
                parts.append(memory_text)
        except Exception:
            pass

    # 5. 计算剩余预算 → 对话历史
    used_chars = reserved_system_chars + sum(len(p) for p in parts)
    remaining = max(0, max_total - used_chars - len(player_message) - 200)
    history_budget = min(budget_history_base + max(0, remaining - budget_history_base), max_total // 2)
    history_budget = max(history_budget, budget_history_base)
    history = _format_history(instance.log, history_budget, english)
    if history:
        parts.append(("## Conversation History" if english else "【对话历史】") + f"\n{history}")

    # 6. 玩家刚说的话
    parts.append(("## Player Message" if english else "【玩家发言】") + f"\n{player_message}")

    context = "\n\n---\n\n".join(parts)

    logger.debug(
        "Context 拼接完成: total_chars=%d, est_tokens=%d, max=%d",
        len(context), _estimate_tokens(context), max_total,
    )
    return context
