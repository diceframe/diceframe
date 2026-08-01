"""RPC 插件描述符与 Bot Bridge 输出协议校验。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .runtime_protocol import PLUGIN_PROTOCOL_VERSION, PluginProtocolError

BRIDGE_EXTENSION_STAGES = frozenset({"before_message", "after_result", "render"})
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BRIDGE_EXTENSION_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def validate_tool_descriptors(initialized: Any) -> list[dict[str, Any]]:
    if not isinstance(initialized, dict) or int(initialized.get("protocol_version") or 0) != PLUGIN_PROTOCOL_VERSION:
        raise PluginProtocolError("工具插件协议版本不匹配")
    raw_tools = initialized.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        raise PluginProtocolError("工具插件必须注册至少一个工具")
    if len(raw_tools) > 64:
        raise PluginProtocolError("单个插件最多注册 64 个工具")
    tools: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in raw_tools:
        if not isinstance(raw, dict):
            raise PluginProtocolError("工具描述必须是对象")
        name = str(raw.get("name") or "").strip()
        if not _TOOL_NAME_RE.fullmatch(name):
            raise PluginProtocolError(f"工具名称非法：{name}")
        if name in names:
            raise PluginProtocolError(f"工具名称重复：{name}")
        names.add(name)
        input_schema = raw.get("input_schema")
        if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
            raise PluginProtocolError(f"工具 {name} 的 input_schema 必须是 object")
        try:
            json.dumps(input_schema, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise PluginProtocolError(f"工具 {name} 的 input_schema 不是有效 JSON") from exc
        tools.append({
            "name": name,
            "title": str(raw.get("title") or name).strip()[:120],
            "description": str(raw.get("description") or "").strip()[:1000],
            "input_schema": input_schema,
        })
    return tools


def validate_bridge_extension_descriptors(initialized: Any) -> list[dict[str, Any]]:
    if not isinstance(initialized, dict) or int(initialized.get("protocol_version") or 0) != PLUGIN_PROTOCOL_VERSION:
        raise PluginProtocolError("Bot Bridge 插件协议版本不匹配")
    raw_extensions = initialized.get("bridge_extensions")
    if not isinstance(raw_extensions, list) or not raw_extensions:
        raise PluginProtocolError("bot-extension 插件必须注册至少一个扩展")
    if len(raw_extensions) > 32:
        raise PluginProtocolError("单个插件最多注册 32 个 Bot Bridge 扩展")
    descriptors: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in raw_extensions:
        if not isinstance(raw, dict):
            raise PluginProtocolError("Bot Bridge 扩展描述必须是对象")
        name = str(raw.get("name") or "").strip()
        if not _BRIDGE_EXTENSION_NAME_RE.fullmatch(name):
            raise PluginProtocolError(f"Bot Bridge 扩展名称非法：{name}")
        if name in names:
            raise PluginProtocolError(f"Bot Bridge 扩展名称重复：{name}")
        names.add(name)
        stages = raw.get("stages")
        if not isinstance(stages, list) or not stages:
            raise PluginProtocolError(f"Bot Bridge 扩展 {name} 必须声明 stages")
        normalized_stages = list(dict.fromkeys(str(item).strip() for item in stages if str(item).strip()))
        unknown = sorted(set(normalized_stages) - BRIDGE_EXTENSION_STAGES)
        if unknown:
            raise PluginProtocolError(f"Bot Bridge 扩展 {name} 使用未知阶段：{', '.join(unknown)}")
        try:
            priority = int(raw.get("priority", 0))
            timeout_sec = float(raw.get("timeout_sec", 5))
        except (TypeError, ValueError) as exc:
            raise PluginProtocolError(f"Bot Bridge 扩展 {name} 的优先级或超时无效") from exc
        if not -1000 <= priority <= 1000:
            raise PluginProtocolError(f"Bot Bridge 扩展 {name} 的 priority 必须在 -1000 到 1000 之间")
        if not 1 <= timeout_sec <= 30:
            raise PluginProtocolError(f"Bot Bridge 扩展 {name} 的 timeout_sec 必须在 1 到 30 秒之间")
        descriptors.append({
            "name": name,
            "title": str(raw.get("title") or name).strip()[:120],
            "description": str(raw.get("description") or "").strip()[:1000],
            "stages": normalized_stages,
            "priority": priority,
            "timeout_sec": timeout_sec,
            "platforms": _descriptor_string_list(raw.get("platforms"), 32, "platforms", name),
            "kinds": _descriptor_string_list(raw.get("kinds"), 64, "kinds", name),
        })
    return descriptors


def _descriptor_string_list(raw: Any, limit: int, field_name: str, extension_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PluginProtocolError(f"Bot Bridge 扩展 {extension_name} 的 {field_name} 必须是字符串数组")
    values = list(dict.fromkeys(str(item).strip().lower() for item in raw if str(item).strip()))
    if len(values) > limit or any(len(item) > 64 for item in values):
        raise PluginProtocolError(f"Bot Bridge 扩展 {extension_name} 的 {field_name} 超出限制")
    return values


def normalize_bridge_outputs(
    runtime: Any,
    raw_outputs: Any,
    bridge_asset_path: Callable[[str, str], Path],
) -> list[dict[str, Any]]:
    if raw_outputs is None:
        return []
    if not isinstance(raw_outputs, list):
        raise PluginProtocolError("Bot Bridge 扩展 outputs 必须是数组")
    if len(raw_outputs) > 16:
        raise PluginProtocolError("Bot Bridge 扩展单次最多返回 16 条消息")
    plugin_id = str(runtime.manifest.get("id") or "")
    outputs: list[dict[str, Any]] = []
    for raw in raw_outputs:
        if not isinstance(raw, dict):
            raise PluginProtocolError("Bot Bridge 输出必须是对象")
        output_type = str(raw.get("type") or "").strip().lower()
        fallback_text = str(raw.get("fallback_text") or "").strip()[:20_000]
        if output_type == "text":
            text = str(raw.get("text") or "")
            if not text or len(text) > 20_000:
                raise PluginProtocolError("Bot Bridge 文本输出为空或超过 20000 字")
            outputs.append({"type": "text", "text": text})
            continue
        if output_type == "card":
            lines = raw.get("lines")
            if not isinstance(lines, list) or len(lines) > 100:
                raise PluginProtocolError("Bot Bridge 卡片 lines 必须是不超过 100 项的数组")
            outputs.append({
                "type": "card",
                "title": str(raw.get("title") or "").strip()[:200],
                "subtitle": str(raw.get("subtitle") or "").strip()[:500],
                "lines": [str(line)[:1000] for line in lines],
                "fallback_text": fallback_text,
            })
            continue
        if output_type == "image":
            relative_path = str(raw.get("path") or "").strip().replace("\\", "/")
            try:
                target = bridge_asset_path(plugin_id, relative_path)
            except (KeyError, ValueError) as exc:
                raise PluginProtocolError(str(exc)) from exc
            if target.stat().st_size <= 0:
                raise PluginProtocolError("Bot Bridge 图片文件为空")
            encoded_path = quote(relative_path, safe="/")
            outputs.append({
                "type": "image",
                "asset_url": f"/api/bot/plugin-assets/{quote(plugin_id, safe='')}/{encoded_path}",
                "caption": str(raw.get("caption") or "").strip()[:2000],
                "alt": str(raw.get("alt") or "").strip()[:500],
                "fallback_text": fallback_text,
            })
            continue
        raise PluginProtocolError(f"不支持的 Bot Bridge 输出类型：{output_type}")
    return outputs
