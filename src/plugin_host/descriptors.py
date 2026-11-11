"""RPC 插件描述符与 Bot Bridge 输出协议校验。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from src.plugin_sdk.contracts import (
    BridgeExtensionDescriptor,
    ProviderCapabilityDescriptor,
    ToolDescriptor,
)

from .runtime_protocol import PLUGIN_PROTOCOL_VERSION, PluginProtocolError
from src.localization.messages import t

BRIDGE_EXTENSION_STAGES = frozenset({"before_message", "after_result", "render"})
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BRIDGE_EXTENSION_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PROVIDER_CAPABILITY_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_PROVIDER_METHOD_NAME_RE = re.compile(r"^provider\.[a-z][a-z0-9_.-]{0,63}$")
PROVIDER_METHOD_ALIASES = frozenset({"generate"})
_TOOL_AGENT_EFFECTS = frozenset({"read_only", "mutation", "destructive"})


def validate_tool_descriptors(initialized: object) -> list[ToolDescriptor]:
    if not isinstance(initialized, dict) or int(initialized.get("protocol_version") or 0) != PLUGIN_PROTOCOL_VERSION:
        raise PluginProtocolError(t("plugin_host_descriptors.tool_protocol_mismatch"))
    raw_tools = initialized.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        raise PluginProtocolError(t("plugin_host_descriptors.tool_required"))
    if len(raw_tools) > 64:
        raise PluginProtocolError(t("plugin_host_descriptors.tool_too_many"))
    tools: list[ToolDescriptor] = []
    names: set[str] = set()
    for raw in raw_tools:
        if not isinstance(raw, dict):
            raise PluginProtocolError(t("plugin_host_descriptors.tool_descriptor_not_object"))
        name = str(raw.get("name") or "").strip()
        if not _TOOL_NAME_RE.fullmatch(name):
            raise PluginProtocolError(t("plugin_host_descriptors.tool_name_invalid", name=name))
        if name in names:
            raise PluginProtocolError(t("plugin_host_descriptors.tool_name_duplicate", name=name))
        names.add(name)
        input_schema = raw.get("input_schema")
        if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
            raise PluginProtocolError(t("plugin_host_descriptors.tool_schema_not_object", name=name))
        try:
            json.dumps(input_schema, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise PluginProtocolError(t("plugin_host_descriptors.tool_schema_not_json", name=name)) from exc
        descriptor: ToolDescriptor = {
            "name": name,
            "title": str(raw.get("title") or name).strip()[:120],
            "description": str(raw.get("description") or "").strip()[:1000],
            "input_schema": input_schema,
        }
        if "agent_effect" in raw:
            agent_effect = raw["agent_effect"]
            if not isinstance(agent_effect, str) or agent_effect not in _TOOL_AGENT_EFFECTS:
                raise PluginProtocolError(
                    t("plugin_host_descriptors.tool_agent_effect_invalid", name=name)
                )
            descriptor["agent_effect"] = agent_effect
        tools.append(descriptor)
    return tools


def validate_bridge_extension_descriptors(initialized: object) -> list[BridgeExtensionDescriptor]:
    if not isinstance(initialized, dict) or int(initialized.get("protocol_version") or 0) != PLUGIN_PROTOCOL_VERSION:
        raise PluginProtocolError(t("plugin_host_descriptors.bridge_protocol_mismatch"))
    raw_extensions = initialized.get("bridge_extensions")
    if not isinstance(raw_extensions, list) or not raw_extensions:
        raise PluginProtocolError(t("plugin_host_descriptors.extension_required"))
    if len(raw_extensions) > 32:
        raise PluginProtocolError(t("plugin_host_descriptors.extension_too_many"))
    descriptors: list[BridgeExtensionDescriptor] = []
    names: set[str] = set()
    for raw in raw_extensions:
        if not isinstance(raw, dict):
            raise PluginProtocolError(t("plugin_host_descriptors.extension_descriptor_not_object"))
        name = str(raw.get("name") or "").strip()
        if not _BRIDGE_EXTENSION_NAME_RE.fullmatch(name):
            raise PluginProtocolError(t("plugin_host_descriptors.extension_name_invalid", name=name))
        if name in names:
            raise PluginProtocolError(t("plugin_host_descriptors.extension_name_duplicate", name=name))
        names.add(name)
        stages = raw.get("stages")
        if not isinstance(stages, list) or not stages:
            raise PluginProtocolError(t("plugin_host_descriptors.extension_stages_required", name=name))
        normalized_stages = list(dict.fromkeys(str(item).strip() for item in stages if str(item).strip()))
        unknown = sorted(set(normalized_stages) - BRIDGE_EXTENSION_STAGES)
        if unknown:
            raise PluginProtocolError(t("plugin_host_descriptors.extension_unknown_stages", name=name, unknown=', '.join(unknown)))
        try:
            priority = int(raw.get("priority", 0))
            timeout_sec = float(raw.get("timeout_sec", 5))
        except (TypeError, ValueError) as exc:
            raise PluginProtocolError(t("plugin_host_descriptors.extension_priority_or_timeout_invalid", name=name)) from exc
        if not -1000 <= priority <= 1000:
            raise PluginProtocolError(t("plugin_host_descriptors.extension_priority_range", name=name))
        if not 1 <= timeout_sec <= 30:
            raise PluginProtocolError(t("plugin_host_descriptors.extension_timeout_range", name=name))
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


def validate_provider_capabilities(initialized: object) -> list[ProviderCapabilityDescriptor]:
    """校验 provider 插件 initialize 返回的 capabilities 声明。

    每个 capability 形如 {"kind": "text-transform", "version": 1,
    "methods": {"generate": "provider.text-transform.generate"}}；kind 即能力命名空间，
    methods 的键是宿主侧别名（如 generate），值是插件侧 RPC 方法名。
    """
    if not isinstance(initialized, dict) or int(initialized.get("protocol_version") or 0) != PLUGIN_PROTOCOL_VERSION:
        raise PluginProtocolError(t("plugin_host_descriptors.provider_protocol_mismatch"))
    raw_capabilities = initialized.get("capabilities")
    if not isinstance(raw_capabilities, list) or not raw_capabilities:
        raise PluginProtocolError(t("plugin_host_descriptors.capability_required"))
    if len(raw_capabilities) > 8:
        raise PluginProtocolError(t("plugin_host_descriptors.capability_too_many"))
    capabilities: list[ProviderCapabilityDescriptor] = []
    kinds: set[str] = set()
    for raw in raw_capabilities:
        if not isinstance(raw, dict):
            raise PluginProtocolError(t("plugin_host_descriptors.capability_descriptor_not_object"))
        kind = str(raw.get("kind") or "").strip()
        if not _PROVIDER_CAPABILITY_KIND_RE.fullmatch(kind):
            raise PluginProtocolError(t("plugin_host_descriptors.capability_kind_invalid", kind=kind))
        if kind in kinds:
            raise PluginProtocolError(t("plugin_host_descriptors.capability_kind_duplicate", kind=kind))
        kinds.add(kind)
        try:
            version = int(raw.get("version", 1))
        except (TypeError, ValueError) as exc:
            raise PluginProtocolError(t("plugin_host_descriptors.capability_version_invalid", kind=kind)) from exc
        if not 1 <= version <= 99:
            raise PluginProtocolError(t("plugin_host_descriptors.capability_version_range", kind=kind))
        raw_methods = raw.get("methods")
        if not isinstance(raw_methods, dict) or not raw_methods:
            raise PluginProtocolError(t("plugin_host_descriptors.capability_methods_required", kind=kind))
        methods: dict[str, str] = {}
        for alias, method_name in raw_methods.items():
            alias = str(alias).strip()
            if alias not in PROVIDER_METHOD_ALIASES:
                raise PluginProtocolError(t("plugin_host_descriptors.capability_unknown_alias", kind=kind, alias=alias))
            method_name = str(method_name or "").strip()
            if not _PROVIDER_METHOD_NAME_RE.fullmatch(method_name):
                raise PluginProtocolError(t("plugin_host_descriptors.capability_method_name_invalid", kind=kind, method_name=method_name))
            methods[alias] = method_name
        capabilities.append({
            "kind": kind,
            "version": version,
            "methods": methods,
            "title": str(raw.get("title") or kind).strip()[:120],
            "description": str(raw.get("description") or "").strip()[:1000],
        })
    return capabilities


def _descriptor_string_list(raw: Any, limit: int, field_name: str, extension_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PluginProtocolError(t("plugin_host_descriptors.extension_field_not_string_array", extension_name=extension_name, field_name=field_name))
    values = list(dict.fromkeys(str(item).strip().lower() for item in raw if str(item).strip()))
    if len(values) > limit or any(len(item) > 64 for item in values):
        raise PluginProtocolError(t("plugin_host_descriptors.extension_field_over_limit", extension_name=extension_name, field_name=field_name))
    return values


def normalize_bridge_outputs(
    runtime: Any,
    raw_outputs: Any,
    bridge_asset_path: Callable[[str, str], Path],
) -> list[dict[str, Any]]:
    if raw_outputs is None:
        return []
    if not isinstance(raw_outputs, list):
        raise PluginProtocolError(t("plugin_host_descriptors.outputs_not_array"))
    if len(raw_outputs) > 16:
        raise PluginProtocolError(t("plugin_host_descriptors.outputs_too_many"))
    plugin_id = str(runtime.manifest.get("id") or "")
    outputs: list[dict[str, Any]] = []
    for raw in raw_outputs:
        if not isinstance(raw, dict):
            raise PluginProtocolError(t("plugin_host_descriptors.output_not_object"))
        output_type = str(raw.get("type") or "").strip().lower()
        fallback_text = str(raw.get("fallback_text") or "").strip()[:20_000]
        if output_type == "text":
            text = str(raw.get("text") or "")
            if not text or len(text) > 20_000:
                raise PluginProtocolError(t("plugin_host_descriptors.text_output_invalid"))
            outputs.append({"type": "text", "text": text})
            continue
        if output_type == "card":
            lines = raw.get("lines")
            if not isinstance(lines, list) or len(lines) > 100:
                raise PluginProtocolError(t("plugin_host_descriptors.card_lines_invalid"))
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
                raise PluginProtocolError(t("plugin_host_descriptors.image_file_empty"))
            encoded_path = quote(relative_path, safe="/")
            outputs.append({
                "type": "image",
                "asset_url": f"/api/bot/plugin-assets/{quote(plugin_id, safe='')}/{encoded_path}",
                "caption": str(raw.get("caption") or "").strip()[:2000],
                "alt": str(raw.get("alt") or "").strip()[:500],
                "fallback_text": fallback_text,
            })
            continue
        raise PluginProtocolError(t("plugin_host_descriptors.output_type_unsupported", output_type=output_type))
    return outputs
