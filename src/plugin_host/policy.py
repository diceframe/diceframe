"""Shared plugin permission and runtime-risk policy."""

from __future__ import annotations

from typing import Any

PERMISSION_DETAILS = {
    "process.spawn": "启动独立插件进程",
    "network.client": "由插件进程访问外部网络",
    "diceframe.http": "调用 DiceFrame HTTP API",
    "plugin.config": "读取插件普通配置",
    "plugin.secrets": "读取插件敏感配置",
    "plugin.data": "读写插件专属数据目录",
    "content.read": "注册和读取内容包资源",
    "content.import": "由用户主动导入内容到角色卡库或世界书",
    "theme.tokens": "注册主题 CSS 变量",
    "map.assets": "注册地图地点和素材资源",
    "tool.execute": "注册并执行结构化工具调用",
}

DECLARATIVE_PLUGIN_TYPES = {"content-pack", "theme", "map-pack"}


def has_entrypoint(manifest: dict[str, Any]) -> bool:
    command = manifest.get("entrypoint")
    return isinstance(command, list) and bool(command)


def plugin_risk_level(manifest: dict[str, Any]) -> str:
    if has_entrypoint(manifest):
        return "unrestricted-process"
    if str(manifest.get("plugin_type") or "") in DECLARATIVE_PLUGIN_TYPES:
        return "declarative"
    return "unsupported-runtime"


def effective_plugin_permissions(manifest: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    declared = manifest.get("permissions")
    if isinstance(declared, list) and declared:
        return sorted(dict.fromkeys(str(item).strip() for item in declared if str(item).strip()))
    inferred = {"plugin.config"}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if any(_sensitive(field) for field in properties.values() if isinstance(field, dict)):
        inferred.add("plugin.secrets")
    plugin_type = str(manifest.get("plugin_type") or "")
    if has_entrypoint(manifest):
        inferred.update({"process.spawn", "plugin.data"})
    if plugin_type == "channel-adapter":
        inferred.update({"network.client", "diceframe.http"})
    elif plugin_type == "content-pack":
        inferred.update({"content.read", "content.import"})
    elif plugin_type == "theme":
        inferred.add("theme.tokens")
    elif plugin_type == "map-pack":
        inferred.add("map.assets")
    elif plugin_type == "tool":
        inferred.add("tool.execute")
    return sorted(inferred)


def _sensitive(field_schema: dict[str, Any]) -> bool:
    ui = field_schema.get("ui") or {}
    return bool(ui.get("sensitive") or ui.get("control") == "secret")
