"""外网隧道接入：插件上报公网 URL，写入 public_base_url。

方案 B（插件上报）：插件进程启动 cloudflared quick tunnel 拿到公网 HTTPS 地址后，
POST /api/bot/tunnel/publish 上报，核心鉴权后写入 public_base_url。
停止/卸载时 release，恢复发布前的地址。

public_base_url 的写入复用 config_update.prepare_config_update 的运行时/持久化路径，
同步更新内存 STATE 与 config.json，前端 /api/system/config 立即生效（修问题 A）。
隧道自身状态（publisher/url/prev/events）存 data/tunnel_state.json。
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.webui.config_update import prepare_config_update
from src.localization.messages import t

logger = logging.getLogger("trpg")


@dataclass(frozen=True)
class TunnelDependencies:
    config_state: Callable[[], dict[str, Any]]
    save_config: Callable[[], None]
    list_plugins: Callable[[], dict[str, Any]]


def _data_dir() -> Path:
    env = os.getenv("TRPG_DATA_DIR", "").strip()
    if env:
        return Path(env)
    # tunnel.py -> services -> webui -> src -> 主程序根
    return Path(__file__).resolve().parents[3] / "data"


def _state_file() -> Path:
    return _data_dir() / "tunnel_state.json"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict[str, Any]) -> None:
    _atomic_write_json(_state_file(), state)


def _record_event(state: dict[str, Any], event: str, plugin_id: str, url: str = "") -> None:
    events = state.get("events")
    if not isinstance(events, list):
        events = []
    events.append({
        "event": event,
        "plugin_id": plugin_id,
        "url": url,
        "at": time.time(),
    })
    state["events"] = events[-20:]


def _valid_public_url(url: str) -> str:
    """校验并归一化公网 URL：必须 https、合法主机名、长度受限。"""
    text = str(url or "").strip().rstrip("/")
    if len(text) > 253:
        raise ValueError(t("webui.services.tunnel.url_too_long"))
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(t("webui.services.tunnel.url_not_https"))
    return text


def _commit_public_base_url(dependencies: TunnelDependencies, url: str) -> None:
    """走标准配置更新路径，写盘失败时恢复内存与原配置。"""
    config_state = dependencies.config_state()
    prepared = prepare_config_update(config_state, {"public_base_url": url})
    if prepared.error:
        raise ValueError(prepared.error)
    previous = dict(config_state)
    config_state.clear()
    config_state.update(prepared.state)
    try:
        dependencies.save_config()
    except Exception:
        config_state.clear()
        config_state.update(previous)
        try:
            dependencies.save_config()
        except Exception:
            logger.exception("公网 URL 配置回滚写盘失败")
        raise


def publish_tunnel_url(
    dependencies: TunnelDependencies, plugin_id: str, url: str
) -> dict[str, Any]:
    """插件上报公网 URL，写入 public_base_url（首次保存 prev 供 release 恢复）。

    同一时刻只允许一个插件发布；需先 release 旧 publisher 再发布新插件。
    """
    normalized = _valid_public_url(url)
    state = _read_state()
    current_publisher = state.get("publisher_plugin_id")
    if current_publisher and current_publisher != plugin_id:
        installed = {
            p.get("id")
            for p in dependencies.list_plugins().get("plugins", [])
        }
        if current_publisher not in installed:
            # 旧 publisher 已卸载（幽灵状态，如手动删目录/异常退出未 release）：接管并继承其 prev_url。
            logger.warning("隧道 publisher %s 已卸载，自动接管", current_publisher)
            state["publisher_plugin_id"] = ""
            state["url"] = ""
        else:
            raise ValueError(t("webui.services.tunnel.already_published", current_publisher=current_publisher))
    if "prev_url" not in state:
        state["prev_url"] = str(
            dependencies.config_state().get("public_base_url") or ""
        )
    previous_public_url = str(
        dependencies.config_state().get("public_base_url") or ""
    )
    _commit_public_base_url(dependencies, normalized)
    state.update({
        "publisher_plugin_id": plugin_id,
        "url": normalized,
        "published_at": time.time(),
    })
    _record_event(state, "publish", plugin_id, normalized)
    try:
        _write_state(state)
    except Exception:
        try:
            _commit_public_base_url(dependencies, previous_public_url)
        except Exception as rollback_error:
            raise RuntimeError(t("webui.services.tunnel.publish_state_and_rollback_failed")) from rollback_error
        raise
    logger.info("隧道已发布: plugin=%s url=%s", plugin_id, normalized)
    return {"ok": True, "public_base_url": normalized}


def release_tunnel_url(
    dependencies: TunnelDependencies, plugin_id: str
) -> dict[str, Any]:
    """停止/卸载时恢复发布前的 public_base_url（仅当该插件是当前 publisher）。"""
    state = _read_state()
    if state.get("publisher_plugin_id") != plugin_id:
        return {"ok": True, "released": False}
    prev = str(state.get("prev_url") or "")
    previous_public_url = str(
        dependencies.config_state().get("public_base_url") or ""
    )
    _commit_public_base_url(dependencies, prev)
    _record_event(state, "release", plugin_id)
    state = {"publisher_plugin_id": "", "url": "", "prev_url": "", "events": state.get("events", [])}
    try:
        _write_state(state)
    except Exception:
        try:
            _commit_public_base_url(dependencies, previous_public_url)
        except Exception as rollback_error:
            raise RuntimeError(t("webui.services.tunnel.release_state_and_rollback_failed")) from rollback_error
        raise
    logger.info("隧道已释放: plugin=%s", plugin_id)
    return {"ok": True, "released": True, "restored": prev}


def tunnel_status(dependencies: TunnelDependencies) -> dict[str, Any]:
    """当前隧道状态 + 可用提供者（声明 tunnel.publish 权限的已安装插件）。"""
    state = _read_state()
    providers: list[dict[str, Any]] = []
    try:
        for plugin in dependencies.list_plugins().get("plugins", []):
            if "tunnel.publish" in (plugin.get("permissions") or []):
                providers.append({
                    "plugin_id": plugin.get("id", ""),
                    "name": plugin.get("name", plugin.get("id", "")),
                    "running": bool(plugin.get("running")),
                    "min_app_version": plugin.get("min_app_version", ""),
                    "needs_core_update": bool(plugin.get("needs_core_update")),
                })
    except Exception:
        logger.exception("读取隧道提供者列表失败")
    return {
        "ok": True,
        "active": bool(state.get("url")),
        "url": state.get("url", ""),
        "published_at": state.get("published_at", 0),
        "public_base_url": dependencies.config_state().get("public_base_url", ""),
        "providers": providers,
    }
