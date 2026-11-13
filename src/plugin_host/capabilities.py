"""Runtime capability initialization and lookup for managed plugins."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from src.plugin_sdk.contracts import ProviderCapabilityDescriptor

from .contracts import ListedBridgeExtensionDescriptor, ListedToolDescriptor
from .descriptors import (
    validate_bridge_extension_descriptors,
    validate_provider_capabilities,
    validate_tool_descriptors,
)
from .models import PluginRuntime
from .runtime_protocol import PluginProtocolError


RuntimeCapabilityInitializer = Callable[[PluginRuntime, object], None]


def _initialize_tool_runtime(runtime: PluginRuntime, initialized: object) -> None:
    runtime.tools = validate_tool_descriptors(initialized)


def _initialize_provider_runtime(runtime: PluginRuntime, initialized: object) -> None:
    runtime.provider_capabilities = validate_provider_capabilities(initialized)


def _initialize_bridge_runtime(runtime: PluginRuntime, initialized: object) -> None:
    runtime.bridge_extensions = validate_bridge_extension_descriptors(initialized)


RUNTIME_CAPABILITY_INITIALIZERS: dict[str, RuntimeCapabilityInitializer] = {
    "tool": _initialize_tool_runtime,
    "provider": _initialize_provider_runtime,
    "bot-extension": _initialize_bridge_runtime,
}


def runtime_capability_initializer_types() -> frozenset[str]:
    return frozenset(RUNTIME_CAPABILITY_INITIALIZERS)


def initialize_runtime_capabilities(
    plugin_type: str,
    runtime: PluginRuntime,
    initialized: object,
) -> None:
    initializer = RUNTIME_CAPABILITY_INITIALIZERS.get(plugin_type)
    if initializer is None:
        raise PluginProtocolError(f"插件类型没有运行时能力初始化器：{plugin_type}")
    initializer(runtime, initialized)


def clear_runtime_capabilities(runtime: PluginRuntime) -> None:
    runtime.tools = []
    runtime.bridge_extensions = []
    runtime.provider_capabilities = []


def list_runtime_tools(
    runtimes: Mapping[str, PluginRuntime],
) -> list[ListedToolDescriptor]:
    tools: list[ListedToolDescriptor] = []
    for plugin_id, runtime in runtimes.items():
        if _plugin_type(runtime) != "tool" or runtime.status != "running":
            continue
        for descriptor in runtime.tools:
            item = cast(ListedToolDescriptor, {
                "name": descriptor["name"],
                "title": descriptor["title"],
                "description": descriptor["description"],
                "input_schema": descriptor["input_schema"],
                "plugin_id": plugin_id,
                "plugin_name": str(runtime.manifest.get("name") or plugin_id),
                "tool_ui": str(runtime.manifest.get("tool_ui") or "").strip(),
            })
            tools.append(item)
            if 'agent_effect' in descriptor:
                item['agent_effect'] = descriptor['agent_effect']
    return tools


def find_provider_plugin(
    runtimes: Mapping[str, PluginRuntime],
    capability: str,
) -> str | None:
    capability = str(capability or "").strip()
    if not capability:
        return None
    matches: list[str] = []
    for plugin_id, runtime in runtimes.items():
        if _plugin_type(runtime) != "provider" or runtime.status != "running":
            continue
        if provider_capability(runtime, capability) is not None:
            matches.append(plugin_id)
    # Provider selection is an authority boundary.  A capability collision
    # must not silently depend on dictionary order or plugin discovery order.
    return matches[0] if len(matches) == 1 else None


def provider_capability(
    runtime: PluginRuntime,
    capability: str,
) -> ProviderCapabilityDescriptor | None:
    return next(
        (item for item in runtime.provider_capabilities if item["kind"] == capability),
        None,
    )


def list_runtime_bridge_extensions(
    runtimes: Mapping[str, PluginRuntime],
) -> list[ListedBridgeExtensionDescriptor]:
    extensions: list[ListedBridgeExtensionDescriptor] = []
    for plugin_id, runtime in runtimes.items():
        if _plugin_type(runtime) != "bot-extension" or runtime.status != "running":
            continue
        for descriptor in runtime.bridge_extensions:
            item = cast(ListedBridgeExtensionDescriptor, {
                "name": descriptor["name"],
                "title": descriptor["title"],
                "description": descriptor["description"],
                "stages": descriptor["stages"],
                "priority": descriptor["priority"],
                "timeout_sec": descriptor["timeout_sec"],
                "platforms": descriptor["platforms"],
                "kinds": descriptor["kinds"],
                "plugin_id": plugin_id,
                "plugin_name": str(runtime.manifest.get("name") or plugin_id),
            })
            extensions.append(item)
    return sorted(
        extensions,
        key=lambda item: (-item["priority"], item["plugin_id"], item["name"]),
    )


def _plugin_type(runtime: PluginRuntime) -> str:
    return str(runtime.manifest.get("plugin_type") or "").strip()
