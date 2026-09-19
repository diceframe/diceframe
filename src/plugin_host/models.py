"""Runtime state models owned by the plugin host."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.plugin_sdk.contracts import (
    BridgeExtensionDescriptor,
    ProviderCapabilityDescriptor,
    ToolDescriptor,
)

from .contracts import PluginManifest
from .runtime_protocol import JsonRpcStdioClient


@dataclass
class PluginRuntime:
    manifest: PluginManifest
    schema: dict[str, Any]
    directory: Path
    config: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    process: asyncio.subprocess.Process | None = None
    monitor_task: asyncio.Task[None] | None = None
    rpc_client: JsonRpcStdioClient | None = None
    tools: list[ToolDescriptor] = field(default_factory=list)
    bridge_extensions: list[BridgeExtensionDescriptor] = field(default_factory=list)
    provider_capabilities: list[ProviderCapabilityDescriptor] = field(default_factory=list)
    status: str = "disabled"
    error: str = ""
    started_at: float = 0.0
    restart_delay_sec: float = 3.0
    source: str = "user"
    # MOD-03：content-pack 声明的 adventure_packages（declared-only 世界来源）。
    adventure_packages_root: Path | None = None
    adventure_package_directories: tuple[str, ...] = ()
