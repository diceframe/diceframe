"""Typed application contracts for the plugin host boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NotRequired, TypedDict

from src.plugin_sdk.contracts import (
    BridgeExtensionDescriptor,
    ProviderCapabilityDescriptor,
    ToolDescriptor,
)


class PluginManifest(TypedDict, total=False):
    """Known manifest keys with only the guarantees made by host validation.

    Values that the host merely converts while reading remain ``object``;
    declaring a narrower type here would incorrectly imply normalization of
    the stored manifest. Fields with structural validation retain useful
    precise types.
    """

    schema_version: str | int | float | bool
    id: object
    name: object
    version: object
    description: object
    plugin_type: object
    entrypoint: list[str]
    min_app_version: object
    permissions: list[str] | None
    capabilities: object
    contributes: dict[str, object]
    content_schema_version: str | int | float | bool
    locale_schema_version: str | int | float | bool
    default_locale: object
    config_schema: object
    tool_ui: object
    docs: object


class PluginTypeDescriptor(TypedDict):
    level: str
    summary: str
    process_mode: str
    inferred_permissions: list[str]
    required_permission: str | None
    contributes: Mapping[str, str] | None
    filterable: bool
    filter_order: int
    cleanup: NotRequired[list[str]]


class PluginSupportView(TypedDict):
    level: str
    summary: str


class PluginTypeListView(TypedDict):
    id: str
    level: str
    filterable: bool
    filter_order: int


class PluginContributionView(TypedDict):
    plugin_id: str
    plugin_name: str
    plugin_type: str
    kind: str
    key: str
    path: str
    title: str
    description: str


class PluginPublicDetail(TypedDict, total=False):
    id: str
    name: object
    version: object
    description: object
    plugin_type: str
    support: PluginSupportView
    has_entrypoint: bool
    enabled: bool
    running: bool
    status: str
    error: str
    schema: dict[str, Any]
    config: dict[str, Any]
    capabilities: object
    permissions: list[str]
    permission_details: list[dict[str, str]]
    min_app_version: str
    needs_core_update: bool
    tool_ui: str
    tools: list[ToolDescriptor]
    bridge_extensions: list[BridgeExtensionDescriptor]
    contributions: list[PluginContributionView]
    docs: object
    dependencies: list[dict[str, Any]]
    dependents: list[str]


class ListedToolDescriptor(ToolDescriptor):
    plugin_id: str
    plugin_name: str
    tool_ui: str


class ListedBridgeExtensionDescriptor(BridgeExtensionDescriptor):
    plugin_id: str
    plugin_name: str


PluginStoppedCallback = Callable[[str], Awaitable[None]]
AIProviderResolver = Callable[[str], object]
