"""Manifest-driven child-process plugin host."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ALLOWED_CONTROLS = {"switch", "text", "secret", "number", "select", "string-list"}


@dataclass
class PluginRuntime:
    manifest: dict[str, Any]
    schema: dict[str, Any]
    directory: Path
    config: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    process: asyncio.subprocess.Process | None = None
    monitor_task: asyncio.Task | None = None
    status: str = "disabled"
    error: str = ""


class PluginHost:
    def __init__(self, plugins_dir: Path, data_dir: Path, *, base_env: dict[str, str] | None = None) -> None:
        self.plugins_dir = plugins_dir
        self.data_dir = data_dir
        self.base_env = base_env or {}
        self.plugins: dict[str, PluginRuntime] = {}
        self.logger = logging.getLogger("trpg.plugins")

    def discover(self) -> list[dict[str, Any]]:
        self.plugins.clear()
        if not self.plugins_dir.exists():
            return []
        for manifest_path in sorted(self.plugins_dir.glob("*/plugin.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                plugin_id = str(manifest.get("id") or "")
                if not _ID_RE.fullmatch(plugin_id) or manifest_path.parent.name != plugin_id:
                    raise ValueError("插件 ID 非法或与目录名不一致")
                if int(manifest.get("schema_version", 0)) != 1:
                    raise ValueError("不支持的 manifest schema_version")
                schema_path = (manifest_path.parent / str(manifest.get("config_schema") or "config.schema.json")).resolve()
                if manifest_path.parent.resolve() not in schema_path.parents:
                    raise ValueError("配置 Schema 路径越界")
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                self._validate_schema(schema)
                runtime = PluginRuntime(manifest, schema, manifest_path.parent)
                runtime.config, runtime.secrets = self._load_config(plugin_id, schema)
                runtime.status = "disabled" if not runtime.config.get("enabled") else "stopped"
                self.plugins[plugin_id] = runtime
            except Exception as exc:
                self.logger.exception("插件加载失败: %s", manifest_path)
                fallback_id = manifest_path.parent.name
                self.plugins[fallback_id] = PluginRuntime(
                    {"id": fallback_id, "name": fallback_id, "version": "?", "description": "插件清单无效"},
                    {"type": "object", "properties": {}}, manifest_path.parent,
                    status="failed", error=str(exc),
                )
        return self.list_public()

    def list_public(self) -> list[dict[str, Any]]:
        return [self.public_detail(plugin_id) for plugin_id in self.plugins]

    def public_detail(self, plugin_id: str) -> dict[str, Any]:
        runtime = self._require(plugin_id)
        if runtime.process and runtime.process.returncode is not None and runtime.status == "running":
            runtime.status = "failed"
            runtime.error = f"插件进程已退出，code={runtime.process.returncode}"
        public_config = dict(runtime.config)
        for key, field_schema in runtime.schema.get("properties", {}).items():
            if self._sensitive(field_schema):
                value = runtime.secrets.get(key, "")
                public_config[key] = {"configured": bool(value), "masked": f"***{value[-4:]}" if value else ""}
        return {
            "id": plugin_id,
            "name": runtime.manifest.get("name", plugin_id),
            "version": runtime.manifest.get("version", ""),
            "description": runtime.manifest.get("description", ""),
            "enabled": bool(runtime.config.get("enabled")),
            "running": bool(runtime.process and runtime.process.returncode is None),
            "status": runtime.status,
            "error": runtime.error,
            "schema": runtime.schema,
            "config": public_config,
        }

    async def start_enabled(self) -> None:
        for plugin_id, runtime in self.plugins.items():
            if runtime.config.get("enabled") and runtime.status != "failed":
                await self.start(plugin_id)

    async def update_config(self, plugin_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        runtime = self._require(plugin_id)
        properties = runtime.schema.get("properties", {})
        new_config = dict(runtime.config)
        new_secrets = dict(runtime.secrets)
        for key, value in changes.items():
            if key not in properties:
                continue
            if self._sensitive(properties[key]):
                if isinstance(value, dict):
                    # Public plugin details expose secrets as
                    # {"configured": true, "masked": "***xxxx"}.  If the UI
                    # saves an unchanged form, do not persist that mask object
                    # as the real secret.
                    continue
                normalized = self._normalize_value(properties[key], value)
                if normalized:
                    new_secrets[key] = normalized
            else:
                normalized = self._normalize_value(properties[key], value)
                new_config[key] = normalized
        self._validate_required(runtime.schema, new_config, new_secrets)
        runtime.config, runtime.secrets = new_config, new_secrets
        self._save_config(plugin_id, runtime)
        await self.restart(plugin_id)
        return self.public_detail(plugin_id)

    async def start(self, plugin_id: str) -> None:
        runtime = self._require(plugin_id)
        if not runtime.config.get("enabled"):
            runtime.status = "disabled"
            return
        if runtime.process and runtime.process.returncode is None:
            runtime.status = "running"
            return
        runtime.status, runtime.error = "starting", ""
        generated = False
        for key, field_schema in runtime.schema.get("properties", {}).items():
            if self._sensitive(field_schema) and (field_schema.get("ui") or {}).get("generate") and not runtime.secrets.get(key):
                runtime.secrets[key] = secrets.token_urlsafe(24)
                generated = True
        if generated:
            self._save_config(plugin_id, runtime)
        env = os.environ.copy()
        env.update(self.base_env)
        env["TRPG_PARENT_PID"] = str(os.getpid())
        for key, field_schema in runtime.schema.get("properties", {}).items():
            env_name = str((field_schema.get("ui") or {}).get("env") or "")
            if not env_name:
                continue
            value = runtime.secrets.get(key, "") if self._sensitive(field_schema) else runtime.config.get(key, field_schema.get("default"))
            env[env_name] = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value).lower() if isinstance(value, bool) else str(value or "")
        command = runtime.manifest.get("entrypoint")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            runtime.status, runtime.error = "failed", "entrypoint 必须是非空字符串数组"
            return
        executable = sys.executable if command[0] == "{python}" else command[0]
        args = command[1:] if command[0] == "{python}" else command[1:]
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            runtime.process = await asyncio.create_subprocess_exec(executable, *args, cwd=str(runtime.directory.parent.parent), env=env, **kwargs)
            runtime.status = "running"
            self.logger.info("插件 %s 已启动，PID=%s", plugin_id, runtime.process.pid)
            runtime.monitor_task = asyncio.create_task(self._monitor_process(plugin_id, runtime.process))
        except Exception as exc:
            runtime.status, runtime.error = "failed", str(exc)
            self.logger.exception("插件 %s 启动失败", plugin_id)

    async def stop(self, plugin_id: str) -> None:
        runtime = self._require(plugin_id)
        monitor = runtime.monitor_task
        runtime.monitor_task = None
        if monitor and not monitor.done():
            monitor.cancel()
        process = runtime.process
        if process and process.returncode is None:
            runtime.status = "stopping"
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        runtime.process = None
        runtime.status = "disabled" if not runtime.config.get("enabled") else "stopped"

    async def restart(self, plugin_id: str) -> None:
        await self.stop(plugin_id)
        await self.start(plugin_id)

    async def cleanup(self) -> None:
        for plugin_id in list(self.plugins):
            await self.stop(plugin_id)

    async def _monitor_process(self, plugin_id: str, process: asyncio.subprocess.Process) -> None:
        try:
            code = await process.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("插件 %s 进程监控失败", plugin_id)
            return
        runtime = self.plugins.get(plugin_id)
        if not runtime or runtime.process is not process:
            return
        if runtime.status == "stopping" or not runtime.config.get("enabled"):
            return
        runtime.status = "failed"
        runtime.error = f"插件进程已退出，code={code}"
        runtime.process = None
        self.logger.warning("插件 %s 意外退出，3 秒后尝试自动重启，code=%s", plugin_id, code)
        await asyncio.sleep(3)
        if self.plugins.get(plugin_id) is runtime and runtime.config.get("enabled") and runtime.status == "failed":
            await self.start(plugin_id)

    def migrate_config(self, plugin_id: str, legacy: dict[str, Any]) -> None:
        runtime = self._require(plugin_id)
        marker = self.data_dir / plugin_id / ".migrated-v1"
        if marker.exists():
            return
        for key, value in legacy.items():
            field_schema = runtime.schema.get("properties", {}).get(key)
            if not field_schema or value in (None, ""):
                continue
            if self._sensitive(field_schema):
                runtime.secrets[key] = str(value)
            else:
                runtime.config[key] = self._normalize_value(field_schema, value)
        self._save_config(plugin_id, runtime)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1\n", encoding="ascii")

    def _load_config(self, plugin_id: str, schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        folder = self.data_dir / plugin_id
        config = {key: field.get("default") for key, field in schema.get("properties", {}).items() if "default" in field and not self._sensitive(field)}
        secrets_data: dict[str, str] = {}
        for filename, target in (("config.json", config), ("secrets.json", secrets_data)):
            path = folder / filename
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    target.update(loaded)
        return config, secrets_data

    def _save_config(self, plugin_id: str, runtime: PluginRuntime) -> None:
        folder = self.data_dir / plugin_id
        folder.mkdir(parents=True, exist_ok=True)
        self._atomic_json(folder / "config.json", runtime.config)
        self._atomic_json(folder / "secrets.json", runtime.secrets)

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    @staticmethod
    def _validate_schema(schema: dict[str, Any]) -> None:
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise ValueError("配置 Schema 必须是 object")
        for key, field_schema in schema["properties"].items():
            control = (field_schema.get("ui") or {}).get("control")
            if control and control not in _ALLOWED_CONTROLS:
                raise ValueError(f"字段 {key} 使用不支持的控件 {control}")

    @staticmethod
    def _sensitive(field_schema: dict[str, Any]) -> bool:
        ui = field_schema.get("ui") or {}
        return bool(ui.get("sensitive") or ui.get("control") == "secret")

    @staticmethod
    def _normalize_value(field_schema: dict[str, Any], value: Any) -> Any:
        field_type = field_schema.get("type")
        if field_type == "boolean": return bool(value)
        if field_type == "number":
            number = float(value)
            if "exclusiveMinimum" in field_schema and number <= float(field_schema["exclusiveMinimum"]): raise ValueError("数值必须大于最小值")
            return number
        if field_type == "integer": return int(value)
        if field_type == "array": return list(dict.fromkeys(str(item).strip() for item in (value if isinstance(value, list) else []) if str(item).strip()))
        text = str(value or "").strip()
        if field_schema.get("enum") and text not in field_schema["enum"]: raise ValueError("选项无效")
        return text

    @staticmethod
    def _validate_required(schema: dict[str, Any], config: dict[str, Any], secrets_data: dict[str, str]) -> None:
        for key in schema.get("required", []):
            if not config.get(key) and not secrets_data.get(key):
                raise ValueError(f"缺少必填配置：{key}")

    def _require(self, plugin_id: str) -> PluginRuntime:
        if plugin_id not in self.plugins:
            raise KeyError(f"插件不存在：{plugin_id}")
        return self.plugins[plugin_id]
