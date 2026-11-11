"""DiceFrame Hub 偏好、详情和社区交互。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

import aiohttp

from src.hub_client import HubUnavailable
from src.plugin_host.mirrors import parse_github_repository
from src.localization.messages import t

# 作者 GitHub 仓库 Raw README 的大小上限（超出视为不可信，直接拒绝）。
_README_RAW_MAX_BYTES = 256 * 1024
# 只允许这些固定候选文件名；禁止请求任意模型提供的 URL（SSRF 防护）。
_README_CANDIDATES = ("README_CN.md", "README.md", "README_EN.md")


@dataclass(frozen=True)
class HubDependencies:
    client: Any | None
    plugin_host: Any | None
    config_state: Callable[[], dict[str, Any]]
    save_config: Callable[[], None]
    current_legal_documents: Callable[[], Awaitable[dict[str, Any]]]
    legal_bundle_version: Callable[[dict[str, Any]], str]
    legal_acceptance_payload: Callable[[dict[str, Any], str], dict[str, Any]]
    legal_accepted: Callable[[dict[str, Any], dict[str, Any] | None], bool]
    record_legal_acceptance: Callable[..., None]


def _client(dependencies: HubDependencies):
    client = dependencies.client
    if client is None:
        raise RuntimeError(t("hub.hub_disabled"))
    return client


async def preferences(
    dependencies: HubDependencies, language: str = "zh-CN",
) -> dict[str, Any]:
    state = dependencies.config_state()
    client = dependencies.client
    identity_exists = bool(client and client.identity_file.exists())
    documents = await dependencies.current_legal_documents()
    return {
        "ok": True,
        "available": client is not None,
        "telemetry_enabled": bool(state.get("hub_telemetry_enabled", False)),
        "choice_made": bool(state.get("hub_telemetry_choice_made", False)),
        "identity_created": identity_exists,
        "legal_version": dependencies.legal_bundle_version(documents),
        "legal_documents": dependencies.legal_acceptance_payload(documents, language),
        "legal_accepted": dependencies.legal_accepted(state, documents),
    }


async def update_preferences(
    dependencies: HubDependencies,
    telemetry_enabled: bool,
    legal_acceptance: dict[str, Any] | None = None,
    language: str = "zh-CN",
) -> dict[str, Any]:
    state = dependencies.config_state()
    documents = await dependencies.current_legal_documents()
    if telemetry_enabled and not (
        dependencies.legal_accepted(state, documents)
        or legal_acceptance is not None
    ):
        raise ValueError(t("hub.privacy_policy_required"))
    previous = {
        "hub_telemetry_enabled": state.get("hub_telemetry_enabled", False),
        "hub_telemetry_choice_made": state.get("hub_telemetry_choice_made", False),
        "legal_terms_accepted_updated_at": state.get("legal_terms_accepted_updated_at"),
        "legal_terms_accepted_version": state.get("legal_terms_accepted_version"),
        "legal_terms_accepted_hash": state.get("legal_terms_accepted_hash"),
        "legal_terms_accepted_language": state.get("legal_terms_accepted_language"),
        "legal_privacy_acknowledged_version": state.get("legal_privacy_acknowledged_version"),
        "legal_privacy_accepted_updated_at": state.get("legal_privacy_accepted_updated_at"),
        "legal_privacy_accepted_version": state.get("legal_privacy_accepted_version"),
        "legal_privacy_accepted_hash": state.get("legal_privacy_accepted_hash"),
        "legal_privacy_accepted_language": state.get("legal_privacy_accepted_language"),
        "legal_accepted_at": state.get("legal_accepted_at"),
    }
    if legal_acceptance is not None:
        dependencies.record_legal_acceptance(
            state,
            acceptance=legal_acceptance,
            documents=documents,
            accepted_at=datetime.now(UTC).isoformat(),
        )
    state["hub_telemetry_enabled"] = bool(telemetry_enabled)
    state["hub_telemetry_choice_made"] = True
    try:
        dependencies.save_config()
    except Exception:
        for key, value in previous.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        raise
    client = dependencies.client
    if client is not None:
        await client.set_telemetry(bool(telemetry_enabled), choice_made=True)
    return await preferences(dependencies, language)


async def delete_identity(dependencies: HubDependencies) -> dict[str, Any]:
    client = _client(dependencies)
    await client.delete_identity()
    return await preferences(dependencies)


async def create_rendezvous_room(
    dependencies: HubDependencies, peer_count: int
) -> dict[str, Any]:
    return {
        "ok": True,
        **await _client(dependencies).create_rendezvous_room(peer_count),
    }


async def rendezvous_config(dependencies: HubDependencies) -> dict[str, Any]:
    return {"ok": True, **await _client(dependencies).rendezvous_config()}


async def plugin_detail(dependencies: HubDependencies, plugin_id: str) -> dict[str, Any]:
    return {"ok": True, **await _client(dependencies).plugin_detail(plugin_id)}


async def plugin_readme(dependencies: HubDependencies, plugin_id: str) -> dict[str, Any]:
    """插件详情 README 的三层兜底：Hub 已清洗 HTML → 本地磁盘缓存 → 作者 GitHub Raw。

    Hub 成功时把已清洗正文写入磁盘缓存；Hub 失败时先读缓存并标记 stale，
    无缓存时才按注册表中已经校验的 GitHub 仓库地址读取固定候选 README，
    禁止请求任意 URL。返回 `markdown`（Raw 原文）由前端用公共清洗器渲染。
    """
    client = _client(dependencies)
    hub_error = ""
    try:
        payload = await client.plugin_readme(plugin_id)
        html = str(payload.get("html") or "")
        if html:
            meta = await _repository_meta(dependencies, plugin_id)
            client.write_readme_cache(
                plugin_id,
                html=html,
                repository_url=meta["repository_url"],
                commit_sha=meta["commit_sha"],
            )
            return {
                "ok": True,
                "html": html,
                "source": {"hub": True, "github": False, "cached": False, "stale": False},
            }
        hub_error = "Hub 未提供 README"
    except (HubUnavailable, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        hub_error = str(exc)

    cached = client.read_readme_cache(plugin_id)
    if cached is not None:
        return {
            "ok": True,
            "html": str(cached.get("html") or ""),
            "source": {
                "hub": True,
                "github": False,
                "cached": True,
                "stale": True,
                "error": hub_error,
            },
        }

    markdown = await _fetch_author_readme(dependencies, plugin_id)
    if markdown:
        return {
            "ok": True,
            "html": "",
            "markdown": markdown,
            "source": {"hub": False, "github": True, "cached": False, "stale": False},
        }
    return {
        "ok": False,
        "html": "",
        "markdown": "",
        "error": hub_error or t("hub.readme_unavailable"),
        "source": {"hub": False, "github": False, "cached": False, "stale": False, "error": hub_error},
    }


async def _repository_meta(
    dependencies: HubDependencies, plugin_id: str,
) -> dict[str, str]:
    """从已校验的商店索引取插件仓库地址与最新提交版本。"""
    plugins = dependencies.plugin_host
    marketplace = getattr(plugins, "marketplace", None)
    if marketplace is None:
        return {"repository_url": "", "commit_sha": ""}
    try:
        listing = await marketplace.list_plugins()
    except Exception:
        return {"repository_url": "", "commit_sha": ""}
    items = listing.get("plugins") if isinstance(listing, dict) else None
    if not isinstance(items, list):
        return {"repository_url": "", "commit_sha": ""}
    item = next((entry for entry in items if isinstance(entry, dict) and entry.get("id") == plugin_id), None)
    if item is None:
        return {"repository_url": "", "commit_sha": ""}
    latest = item.get("latest") if isinstance(item.get("latest"), dict) else {}
    commit_sha = str(latest.get("commit_sha") or item.get("commit_sha") or "").strip().lower()
    return {
        "repository_url": str(item.get("repository_url") or "").strip(),
        "commit_sha": commit_sha,
    }


async def _fetch_author_readme(
    dependencies: HubDependencies, plugin_id: str,
) -> str:
    """只在注册表仓库地址基础上读取固定候选 README；非 GitHub 或私网地址直接跳过。"""
    plugins = dependencies.plugin_host
    mirrors = getattr(plugins, "mirrors", None)
    if mirrors is None:
        return ""
    meta = await _repository_meta(dependencies, plugin_id)
    repository_url = meta["repository_url"]
    if not repository_url:
        return ""
    try:
        owner, repo = parse_github_repository(repository_url)
    except ValueError:
        return ""
    commit_sha = meta["commit_sha"]
    ref = commit_sha if len(commit_sha) == 40 else "main"
    for candidate in _README_CANDIDATES:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{candidate}"
        try:
            result = await mirrors.fetch_github_url(url, max_bytes=_README_RAW_MAX_BYTES)
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError):
            continue
        if result.ok and isinstance(result.data, str) and result.data.strip():
            return result.data
    return ""


async def plugin_ratings(dependencies: HubDependencies, plugin_id: str) -> dict[str, Any]:
    return {"ok": True, **await _client(dependencies).plugin_ratings(plugin_id)}


async def set_plugin_like(
    dependencies: HubDependencies, plugin_id: str, liked: bool,
) -> dict[str, Any]:
    return {"ok": True, **await _client(dependencies).set_like(plugin_id, liked)}


async def set_plugin_rating(
    dependencies: HubDependencies,
    plugin_id: str,
    stars: int | None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        **await _client(dependencies).set_rating(plugin_id, stars, tags),
    }


class HubService:
    """Hub preferences, discovery, and community actions with explicit boundaries."""

    def __init__(self, dependencies: HubDependencies) -> None:
        self._dependencies = dependencies

    async def preferences(self, language: str = "zh-CN") -> dict[str, Any]:
        return await preferences(self._dependencies, language)

    async def update_preferences(
        self,
        telemetry_enabled: bool,
        legal_acceptance: dict[str, Any] | None = None,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        return await update_preferences(
            self._dependencies, telemetry_enabled, legal_acceptance, language,
        )

    async def delete_identity(self) -> dict[str, Any]:
        return await delete_identity(self._dependencies)

    async def create_rendezvous_room(self, peer_count: int) -> dict[str, Any]:
        return await create_rendezvous_room(self._dependencies, peer_count)

    async def rendezvous_config(self) -> dict[str, Any]:
        return await rendezvous_config(self._dependencies)

    async def plugin_detail(self, plugin_id: str) -> dict[str, Any]:
        return await plugin_detail(self._dependencies, plugin_id)

    async def plugin_readme(self, plugin_id: str) -> dict[str, Any]:
        return await plugin_readme(self._dependencies, plugin_id)

    async def plugin_ratings(self, plugin_id: str) -> dict[str, Any]:
        return await plugin_ratings(self._dependencies, plugin_id)

    async def set_plugin_like(self, plugin_id: str, liked: bool) -> dict[str, Any]:
        return await set_plugin_like(self._dependencies, plugin_id, liked)

    async def set_plugin_rating(
        self, plugin_id: str, stars: int | None, tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return await set_plugin_rating(
            self._dependencies, plugin_id, stars, tags,
        )
