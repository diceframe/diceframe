"""LLM 输出解析器 —— 从 GM 回复末尾提取结构化 JSON 块。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("trpg")


@dataclass
class ParsedResult:
    """解析后的 LLM 输出。"""
    narration: str                            # 叙事文本（给玩家看的）
    state_update: dict | None = None          # 状态更新
    memory_delta: dict | None = None          # 记忆变更
    info_asymmetry: dict | None = None        # 信息不对称（私聊消息）
    plot_update: dict | None = None           # 剧情推进（任务/关系/决策）
    is_narration_only: bool = False           # JSON 解析失败，降级为纯叙事


# 匹配末尾 ```json ... ``` 块的模式
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

# LLM 常见的元文本（不应出现在叙事中）
_META_PATTERNS = [
    re.compile(r"^明白了[，。]\s*", re.MULTILINE),
    re.compile(r"^好的[，。]\s*", re.MULTILINE),
    re.compile(r"^了解[了]?[，。]\s*", re.MULTILINE),
    re.compile(r"^以下是.*回复[：:]?\s*", re.MULTILINE),
    re.compile(r"^修正后.*[：:]\s*", re.MULTILINE),
    re.compile(r"^我会.*输出[。！]\s*", re.MULTILINE),
    re.compile(r"^严格遵循[。，].*\s*", re.MULTILINE),
]

_INTERNAL_CONTEXT_HEADINGS = (
    "【游戏状态】",
    "【对话历史】",
    "【玩家发言】",
    "【局势分析（内部参考）】",
    "【局势分析(内部参考)】",
)

_INTERNAL_ANALYSIS_PREFIXES = (
    "我们被要求",
    "玩家动作是",
    "上面已经有",
    "但注意这是",
    "现在用户",
    "实际上，用户",
    "看起来像是",
    "可能用户",
    "我应该做什么",
)


def sanitize_narration(text: str) -> str:
    """Remove leaked prompt/context blocks from player-facing narration."""
    if not text:
        return ""
    lines = str(text).splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(heading) for heading in _INTERNAL_CONTEXT_HEADINGS):
            skipping = True
            continue
        if any(stripped.startswith(prefix) for prefix in _INTERNAL_ANALYSIS_PREFIXES):
            continue
        if skipping and stripped.startswith("【") and stripped.endswith("】"):
            if any(stripped.startswith(heading) for heading in _INTERNAL_CONTEXT_HEADINGS):
                continue
            skipping = False
        if not skipping:
            kept.append(line)
    cleaned = "\n".join(kept).strip()
    for pattern in _META_PATTERNS:
        cleaned = pattern.sub("", cleaned).strip()
    return cleaned


def _build_result(narration: str, data: dict) -> ParsedResult:
    state_update = data.get("state_update")
    memory_delta = data.get("memory_delta")
    info_asymmetry = data.get("info_asymmetry")
    plot_update = data.get("plot_update")

    if memory_delta is not None:
        if not isinstance(memory_delta, dict):
            logger.warning("memory_delta 不是 dict，已忽略")
            memory_delta = None
        else:
            for key in ("add", "update", "forget"):
                if key not in memory_delta:
                    memory_delta[key] = []
                elif not isinstance(memory_delta[key], list):
                    memory_delta[key] = []

    return ParsedResult(
        narration=narration,
        state_update=state_update,
        memory_delta=memory_delta,
        info_asymmetry=info_asymmetry,
        plot_update=plot_update,
    )


def _repair_json(json_str: str) -> str:
    """自动修复 LLM 常见的 JSON 格式错误。"""
    s = json_str.strip()
    # 修复 0：字符串被 max_tokens 截断时，补上末尾引号
    if s.count('"') % 2 != 0:
        s += '"'

    # 修复 1：末尾多余逗号
    s = re.sub(r',\s*([}\]])', r'\1', s)
    # 修复 2：缺少末尾花括号
    missing = s.count('{') - s.count('}')
    if missing > 0:
        s += '}' * missing
    # 修复 3：缺少末尾方括号
    missing_b = s.count('[') - s.count(']')
    if missing_b > 0:
        s += ']' * missing_b
    # 修复 4：单引号字符串（全量替换时小心 value 内含单引号）
    if '"' not in s and "'" in s:
        s = re.sub(r"'([^']*)':", r'"\1":', s)
        s = re.sub(r":\s*'([^']*)'", r': "\1"', s)
    # 修复 5：LLM 在字符串值内塞了裸双引号 → 转义
    s = _escape_stray_quotes_in_values(s)
    # 修复 6：对象末尾的逗号前有注释 // comment
    s = re.sub(r'\s*//[^\n]*', '', s)
    # 修复 7：头尾有多余字符（LLM 可能在 JSON 前后夹了一句中文）
    s = _strip_surrounding_text(s)
    return s


def _escape_stray_quotes_in_values(json_str: str) -> str:
    """转义字符串值内部不该出现的裸双引号。

    LLM 有时会输出: "value": "他说"你好"世界"
    这会导致解析失败。策略：在已知的 key: 之后，成对引号之间如果出现
    第三个引号，将它转义。
    """
    result = []
    i = 0
    in_key = False
    expect_colon = False
    in_string = False
    string_start = 0
    while i < len(json_str):
        ch = json_str[i]
        if ch == '"' and not in_string:
            in_string = True
            string_start = i
            result.append(ch)
        elif ch == '"' and in_string:
            # 检查是否是合法的字符串结束：后面是 , } ] 或空白 followed by 这些
            after = json_str[i + 1:i + 10].lstrip()
            if after and after[0] in ',}:]':
                in_string = False
                result.append(ch)
            else:
                # 字符串内部的裸引号，转义
                result.append('\\"')
        elif ch == '\\' and in_string:
            result.append(ch)
            if i + 1 < len(json_str):
                i += 1
                result.append(json_str[i])
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _strip_surrounding_text(s: str) -> str:
    """如果 JSON 前后被中文/英文文本包裹，尝试截取 JSON 部分。"""
    start = s.find('{')
    if start == -1:
        return s
    depth = 0
    for i in range(start, len(s)):
        if s[i] == '{':
            depth += 1
        elif s[i] == '}':
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return s


def _find_balanced_json(text: str) -> str | None:
    """用括号计数法从文本中提取首个完整 JSON 对象，支持任意深度嵌套。"""
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_llm_response(raw: str) -> ParsedResult:
    """从 LLM 原始响应中提取叙事文本和结构化数据。

    算法：
    1. 找到所有 ```json ... ``` 块
    2. 取最后一个块解析为 JSON
    3. 叙事文本 = JSON 块之前的内容（所有 JSON 块都去掉）
    4. 如果没有任何 JSON 块或解析失败 → 整个响应视为叙事

    失败时 is_narration_only=True，上层调用方应触发 JSON 重试或降级处理。
    """
    if not raw or not raw.strip():
        return ParsedResult(narration="", is_narration_only=True)

    matches = list(_JSON_BLOCK_RE.finditer(raw))
    if not matches:
        # 兜底1：尝试匹配整个响应为裸 JSON（JSON 模式输出）
        try:
            data = json.loads(raw.strip())
            logger.info("直接 JSON 解析成功（JSON 模式输出）")
            return _build_result("", data)
        except json.JSONDecodeError:
            pass

        # 兜底2：括号计数法匹配嵌套 JSON（支持多层花括号）
        bare_match = _find_balanced_json(raw)
        if bare_match:
            try:
                data = json.loads(bare_match)
                narration = raw[:raw.index(bare_match)].strip()
                narration = sanitize_narration(narration)
                logger.info("通过括号计数匹配解析成功")
                return _build_result(narration, data)
            except (json.JSONDecodeError, ValueError):
                pass
        logger.debug("LLM 响应中未找到 JSON 块，使用标签或降级纯叙事")
        narration = raw.strip()
        narration = sanitize_narration(narration)
        return ParsedResult(narration=narration, is_narration_only=True)

    # 取最后一个 JSON 块
    last_match = matches[-1]
    json_str = last_match.group(1).strip()

    # 叙事文本 = 全部 JSON 块都被去掉后的残余
    narration = raw
    for m in reversed(matches):
        narration = narration[:m.start()] + narration[m.end():]
    narration = narration.strip()

    # 过滤 LLM 的元文本（"明白了""好的" 等确认语）
    narration = sanitize_narration(narration)
    # 如果过滤后变空了，保留原文（至少玩家能看到点东西）
    if not narration:
        narration = raw.strip()
        for m in reversed(matches):
            narration = narration[:m.start()] + narration[m.end():]
        narration = sanitize_narration(narration)

    # 尝试解析（先尝试修复版，再原始版）
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        try:
            repaired = _repair_json(json_str)
            data = json.loads(repaired)
            logger.info("JSON 自动修复成功")
        except json.JSONDecodeError as exc:
            logger.warning("LLM 输出 JSON 解析失败: %s", exc)
            return ParsedResult(narration=narration, is_narration_only=True)

    return _build_result(narration, data)


def make_retry_message(original_prompt: str, previous_error: str) -> str:
    """构造重试提示：告诉 LLM 上次 JSON 格式哪里错了。"""
    return (
        f"你上次回复末尾的 JSON 格式有问题。\n"
        f"请重新输出完整 GM 回复，并在末尾添加一行紧凑 JSON：\n"
        f'{{"state_update":{{"players":{{}}}},"memory_delta":{{"add":[],"update":[],"forget":[]}},"info_asymmetry":{{}}}}\n'
        f"没有变动就传空对象。不要输出'明白了'这类确认文字，直接输出故事。\n\n"
        f"原来上下文：\n{original_prompt}"
    )
