"""DiceFrame plugin marketplace index and repository installation helpers."""

from __future__ import annotations

import json
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .mirrors import (
    DEFAULT_MARKETPLACE_BRANCH,
    DEFAULT_MARKETPLACE_FILE,
    DEFAULT_MARKETPLACE_OWNER,
    DEFAULT_MARKETPLACE_REPO,
    MirrorManager,
    github_commit_archive_url,
    parse_github_repository,
    validate_public_http_url,
)
from .package_limits import MAX_PLUGIN_PACKAGE_BYTES
from .policy import effective_plugin_permissions, plugin_risk_level
from .support import plugin_type_support
from src.version import needs_core_update

@dataclass(frozen=True)
class MarketplaceSource:
    owner: str = DEFAULT_MARKETPLACE_OWNER
    repo: str = DEFAULT_MARKETPLACE_REPO
    branch: str = DEFAULT_MARKETPLACE_BRANCH
    file_path: str = DEFAULT_MARKETPLACE_FILE


class PluginMarketplace:
    def __init__(
        self,
        mirrors: MirrorManager,
        source: MarketplaceSource | None = None,
        *,
        hub_client: Any = None,
    ) -> None:
        self.mirrors = mirrors
        self.source = source or MarketplaceSource()
        self.hub_client = hub_client

    async def list_plugins(self) -> dict[str, Any]:
        if self.hub_client is not None:
            hub_listing = await self.hub_client.catalog()
            if hub_listing.get("ok"):
                plugins = [
                    item
                    for item in (
                        _normalize_market_item(item) for item in hub_listing.get("items", [])
                    )
                    if item is not None
                ]
                return {
                    "ok": True,
                    "plugins": plugins,
                    "total": len(plugins),
                    "source": hub_listing.get("source", {"mirror_name": "DiceFrame Hub"}),
                }
        fetched = await self.mirrors.fetch_raw(
            self.source.owner,
            self.source.repo,
            self.source.branch,
            self.source.file_path,
        )
        if not fetched.ok or not isinstance(fetched.data, str):
            return {"ok": False, "error": fetched.error or "插件市场读取失败", "plugins": [], "source": fetched.to_dict()}
        try:
            raw_items = json.loads(fetched.data)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"插件市场 JSON 无效：{exc}", "plugins": [], "source": fetched.to_dict()}
        if not isinstance(raw_items, list):
            return {"ok": False, "error": "插件市场 JSON 必须是数组", "plugins": [], "source": fetched.to_dict()}
        plugins = [item for item in (_normalize_market_item(item) for item in raw_items) if item is not None]
        source = fetched.to_dict()
        source.pop("data", None)
        return {"ok": True, "plugins": plugins, "total": len(plugins), "source": source}

    async def resolve_release(self, item: dict[str, Any]) -> dict[str, Any]:
        repository_url = str(item.get("repository_url") or "")
        owner, repo = parse_github_repository(repository_url)
        release_result = await self.mirrors.fetch_github_api(
            f"/repos/{quote(owner)}/{quote(repo)}/releases/latest",
            official_first=True,
        )
        if not release_result.ok or not isinstance(release_result.data, str):
            raise ValueError(release_result.error or "无法读取插件最新 Release")
        try:
            release = json.loads(release_result.data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"GitHub Release 响应无效：{exc}") from exc
        tag = str(release.get("tag_name") or "").strip()
        if not tag or release.get("draft") or release.get("prerelease"):
            raise ValueError("插件仓库没有可安装的正式 GitHub Release")

        commit_result = await self.mirrors.fetch_github_api(
            f"/repos/{quote(owner)}/{quote(repo)}/commits/{quote(tag, safe='')}",
            official_first=True,
        )
        if not commit_result.ok or not isinstance(commit_result.data, str):
            raise ValueError(commit_result.error or "无法解析插件 Release 的 Git commit")
        try:
            commit_sha = str(json.loads(commit_result.data).get("sha") or "").lower()
        except json.JSONDecodeError as exc:
            raise ValueError(f"GitHub commit 响应无效：{exc}") from exc
        archive_url = github_commit_archive_url(repository_url, commit_sha)

        manifest_result = await self.mirrors.fetch_raw(owner, repo, commit_sha, "plugin.json")
        if not manifest_result.ok or not isinstance(manifest_result.data, str):
            raise ValueError(manifest_result.error or "Release 根目录缺少 plugin.json")
        try:
            manifest = json.loads(manifest_result.data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Release 中的 plugin.json 无效：{exc}") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Release 中的 plugin.json 必须是对象")
        if str(manifest.get("id") or "") != str(item.get("id") or ""):
            raise ValueError("Release 中的插件 ID 与官方索引不一致")
        version = str(manifest.get("version") or "").strip()
        if not version:
            raise ValueError("Release 中的 plugin.json 缺少版本")

        config_path = str(manifest.get("config_schema") or "config.schema.json").strip()
        config_result = await self.mirrors.fetch_raw(owner, repo, commit_sha, config_path)
        if not config_result.ok or not isinstance(config_result.data, str):
            raise ValueError(config_result.error or f"Release 缺少配置 Schema：{config_path}")
        try:
            schema = json.loads(config_result.data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Release 中的配置 Schema 无效：{exc}") from exc
        if not isinstance(schema, dict):
            raise ValueError("Release 中的配置 Schema 必须是对象")

        permissions = effective_plugin_permissions(manifest, schema)
        approved_permissions = _string_list(item.get("approved_permissions"))
        added_permissions = [permission for permission in permissions if permission not in approved_permissions]
        risk_level = plugin_risk_level(manifest)
        approved_risk = str(item.get("risk_level") or "").strip()
        if added_permissions or (approved_risk and approved_risk != risk_level):
            details = "、".join(added_permissions) if added_permissions else f"{approved_risk} → {risk_level}"
            raise ValueError(f"插件新版本扩大权限或改变运行方式，需要重新审核：{details}")
        update_policy = "automatic" if risk_level == "declarative" else "notify"
        resolved = dict(item)
        resolved.update({
            "version": version,
            "manifest": manifest,
            "release_tag": tag,
            "release_url": str(release.get("html_url") or ""),
            "commit_sha": commit_sha,
            "archive_url": archive_url,
            "risk_level": risk_level,
            "update_policy": update_policy,
            "permissions": permissions,
            "installable": True,
            "verification_error": "",
        })
        return resolved

    async def package_for_plugin(self, plugin_id: str) -> dict[str, Any]:
        listing = await self.list_plugins()
        if not listing.get("ok"):
            return listing
        normalized_id = plugin_id.strip()
        for indexed_item in listing["plugins"]:
            if indexed_item["id"] != normalized_id:
                continue
            if indexed_item.get("distribution") == "bundled":
                return {"ok": False, "error": "该插件随 DiceFrame 提供，不需要从商店安装", "plugin": indexed_item}
            try:
                item = await self.resolve_release(indexed_item)
            except ValueError as exc:
                return {"ok": False, "error": str(exc), "plugin": indexed_item}
            event_id = secrets.token_urlsafe(18)
            if self.hub_client is not None:
                self.hub_client.queue_download_event(
                    plugin_id,
                    event_id=event_id,
                    kind="download_started",
                    plugin_version=str(item.get("version") or ""),
                )
            archive_url = validate_public_http_url(str(item.get("archive_url") or ""))
            fetched = await self.mirrors.fetch_github_url(
                archive_url,
                binary=True,
                max_bytes=MAX_PLUGIN_PACKAGE_BYTES,
            )
            if not fetched.ok or not isinstance(fetched.data, bytes):
                return {"ok": False, "error": fetched.error or "插件仓库快照下载失败", "plugin": item, "source": fetched.to_dict()}
            source = fetched.to_dict()
            source.pop("data", None)
            return {
                "ok": True,
                "plugin": item,
                "payload": fetched.data,
                "source": source,
                "hub_event_id": event_id,
                "artifact_hash": hashlib.sha256(fetched.data).hexdigest(),
            }
        return {"ok": False, "error": f"插件市场中找不到：{plugin_id}"}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _min_app_version(item: Any, manifest: dict) -> str:
    """索引条目顶层审核基线优先，manifest 兜底；两侧都取，保证展示与 needs_core_update 同源。"""
    return str((item.get("min_app_version") if isinstance(item, dict) else None)
               or manifest.get("min_app_version") or "").strip()


def _latest_field(item: Any) -> dict[str, Any]:
    """透传索引同步写入的最新 Release 信息；无则返回空对象。

    latest.version 是插件仓库最新正式 Release 的真实版本（由索引每晚同步
    刷新），用于与本地已装版本比较，判断是否真的有新版可更新。
    """
    latest = item.get("latest") if isinstance(item, dict) else None
    if not isinstance(latest, dict):
        return {}
    return {
        "version": str(latest.get("version") or ""),
        "release_tag": str(latest.get("release_tag") or ""),
        "release_url": str(latest.get("release_url") or ""),
        "commit_sha": str(latest.get("commit_sha") or ""),
        "published_at": str(latest.get("published_at") or ""),
        "requires_approval": bool(latest.get("requires_approval")),
    }


def _normalize_market_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    manifest = item.get("manifest") if isinstance(item.get("manifest"), dict) else {}
    plugin_id = str(manifest.get("id") or item.get("id") or "").strip()
    if not plugin_id:
        return None
    repository_url = str(item.get("repository_url") or item.get("repositoryUrl") or manifest.get("repository_url") or "")
    urls = manifest.get("urls") if isinstance(manifest.get("urls"), dict) else {}
    if not repository_url:
        repository_url = str(urls.get("repository") or "")
    name = str(manifest.get("name") or item.get("name") or plugin_id)
    description = str(manifest.get("description") or item.get("description") or "")
    version = str(manifest.get("version") or item.get("version") or "")
    plugin_type = str(manifest.get("plugin_type") or item.get("plugin_type") or item.get("pluginType") or "")
    support = plugin_type_support(plugin_type)
    distribution = str(item.get("distribution") or "repository")
    risk_level = str(item.get("risk_level") or item.get("riskLevel") or plugin_risk_level(manifest))
    update_policy = str(item.get("update_policy") or item.get("updatePolicy") or ("automatic" if risk_level == "declarative" else "notify"))
    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    trust_level = str(item.get("trust_level") or item.get("trustLevel") or "").strip().lower()
    if not trust_level:
        trust_level = "official" if "official" in tags or distribution == "bundled" else "community"
    verification_error = str(item.get("verification_error") or "")
    installable = bool(item.get("installable", True))
    security = item.get("security") if isinstance(item.get("security"), dict) else {}
    if security.get("install_allowed") is False:
        installable = False
        reasons = security.get("blocking_reasons")
        if isinstance(reasons, list) and reasons:
            verification_error = "；".join(str(reason) for reason in reasons if str(reason))
        verification_error = verification_error or "Hub 安全策略暂不允许安装该插件"
    if distribution == "bundled":
        installable = False
        verification_error = verification_error or "该插件随 DiceFrame 提供，不需要从商店安装"
    elif support["level"] == "reserved":
        installable = False
        verification_error = "该插件类型仍为预留能力，当前版本不能从商店安装"
    elif support["level"] == "unsupported":
        installable = False
        verification_error = "DiceFrame 不支持该插件类型"
    elif update_policy in {"approval-required", "blocked"}:
        installable = False
        verification_error = verification_error or "该版本需要重新审核"
    return {
        "id": plugin_id,
        "name": name,
        "version": version,
        "description": description,
        "plugin_type": plugin_type,
        "support": support,
        "repository_url": repository_url,
        "archive_url": str(item.get("archive_url") or ""),
        "release_tag": str(item.get("release_tag") or ""),
        "release_url": str(item.get("release_url") or ""),
        "branch": str(item.get("branch") or "main"),
        "stars": int(item.get("stars") or 0),
        "distribution": distribution,
        "risk_level": risk_level,
        "update_policy": update_policy,
        "author": item.get("author") or manifest.get("author") or "",
        "license": item.get("license") or manifest.get("license") or "",
        "tags": tags,
        "trust_level": trust_level,
        # 模组元数据（LIFE-03，母方案 §126）：仅 content-pack 携带；
        # 规则目标 / 冒险数 / 自动化等级 / 语言供模块库过滤与卡片展示。
        "content_profile": str(manifest.get("content_profile") or "content-pack"),
        "content_delivery_mode": str(manifest.get("content_delivery_mode") or "legacy_autoimport"),
        "ruleset_targets": [
            str(requirement.get("id") or "")
            for requirement in (
                (manifest.get("requires") or {}).get("rulesets") or []
                if isinstance(manifest.get("requires") or {}, dict) else []
            )
            if isinstance(requirement, dict) and requirement.get("id")
        ],
        "adventure_count": (
            len(manifest.get("adventure_packages") or [])
            if isinstance(manifest.get("adventure_packages"), list) else 0
        ),
        # automation_level 只能在 manifest 顶层显式声明（冒险实体的自动化
        # 等级要等包下载后才能统计，Marketplace 清单不猜，母方案 §12/§165）。
        "automation_level": str(manifest.get("automation_level") or ""),
        "languages": [
            str(locale) for locale in (
                manifest.get("supported_locales") or []
                if isinstance(manifest.get("supported_locales"), list) else []
            )
        ],
        "commit_sha": str(item.get("commit_sha") or item.get("commitSha") or ""),
        "approved_permissions": _string_list(item.get("approved_permissions")),
        "permissions": _string_list(manifest.get("permissions") or item.get("permissions")),
        "verified": bool(item.get("commit_sha")) or distribution == "bundled",
        "installable": installable,
        "verification_error": verification_error,
        "capabilities": _string_list(manifest.get("capabilities") or item.get("capabilities")),
        "min_app_version": _min_app_version(item, manifest),
        "needs_core_update": needs_core_update(_min_app_version(item, manifest)),
        "docs": str(manifest.get("docs") or item.get("docs") or urls.get("documentation") or ""),
        "homepage": str(item.get("homepage") or urls.get("homepage") or repository_url),
        "latest": _latest_field(item),
        "manifest": manifest,
        "stats": item.get("stats") if isinstance(item.get("stats"), dict) else {},
        "security": item.get("security") if isinstance(item.get("security"), dict) else {},
        "readme": item.get("readme") if isinstance(item.get("readme"), dict) else {},
    }
