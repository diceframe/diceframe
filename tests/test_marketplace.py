import json
import hashlib
from pathlib import Path

import pytest

from src.plugin_host.marketplace import PluginMarketplace, _normalize_market_item
from src.plugin_host.mirrors import (
    FetchResult,
    MirrorManager,
    _streaming_download_timeout,
)


class FakeMirrors:
    def __init__(self, payload: bytes, manifest: dict | None = None):
        self.payload = payload
        self.manifest = manifest or {
            "schema_version": 1,
            "id": "demo-pack",
            "name": "Demo",
            "version": "1.2.0",
            "description": "demo",
            "plugin_type": "content-pack",
            "permissions": ["content.read"],
        }
        self.max_bytes = None
        self.download_url = ""

    async def fetch_github_api(self, api_path, **_kwargs):
        if api_path.endswith("/releases/latest"):
            data = {"tag_name": "v1.2.0", "html_url": "https://github.com/example/demo/releases/tag/v1.2.0"}
        else:
            data = {"sha": "a" * 40}
        return FetchResult(ok=True, data=json.dumps(data), status=200)

    async def fetch_raw(self, *_args, **_kwargs):
        return FetchResult(ok=True, data=json.dumps(self.manifest), status=200)

    async def fetch_github_url(self, url, *, binary=False, max_bytes=None):
        self.max_bytes = max_bytes
        self.download_url = url
        return FetchResult(ok=True, data=self.payload, url=url, status=200)


def _market_item(**changes):
    item = {
        "id": "demo-pack",
        "repository_url": "https://github.com/example/demo",
        "distribution": "repository",
        "risk_level": "declarative",
        "update_policy": "automatic",
        "approved_permissions": ["content.read"],
        "manifest": {
            "id": "demo-pack",
            "name": "Demo",
            "version": "1.1.0",
            "description": "demo",
            "plugin_type": "content-pack",
            "permissions": ["content.read"],
        },
    }
    item.update(changes)
    return _normalize_market_item(item)


def test_marketplace_repository_item_does_not_require_author_zip_or_sha256():
    item = _market_item()

    assert item["installable"] is True
    assert "sha256" not in item
    assert item["distribution"] == "repository"


@pytest.mark.parametrize("listing_status", ["paused", "withdrawn"])
def test_marketplace_listing_status_blocks_new_install_but_keeps_diagnostic(listing_status):
    item = _market_item(
        listing_status=listing_status,
        listing_status_reason="版权投诉待核验",
        listing_status_at="2026-11-12T00:00:00Z",
    )

    assert item["installable"] is False
    assert item["listing_status"] == listing_status
    assert "版权投诉" in item["verification_error"]


def test_marketplace_accepts_static_catalog_status_aliases():
    item = _market_item(status="withdrawn", status_reason="作者撤回")

    assert item["listing_status"] == "withdrawn"
    assert item["installable"] is False
    assert "作者撤回" in item["verification_error"]


@pytest.mark.asyncio
async def test_marketplace_uses_a_stale_catalog_cache_without_falling_back_to_latest(tmp_path: Path):
    class CachedMirrors:
        def __init__(self):
            self.fail = False

        async def fetch_raw(self, *_args, **_kwargs):
            if self.fail:
                return FetchResult(ok=False, error="offline", status=503)
            return FetchResult(ok=True, data=json.dumps([{
                "id": "demo-pack",
                "repository_url": "https://github.com/example/demo",
                "approved_release_tag": "v1.0.0",
                "approved_commit": "a" * 40,
                "approved_artifact_sha256": "b" * 64,
                "manifest": {"id": "demo-pack", "plugin_type": "content-pack"},
            }]), status=200)

    mirrors = CachedMirrors()
    marketplace = PluginMarketplace(mirrors, cache_path=tmp_path / "catalog-cache.json")
    first = await marketplace.list_plugins()
    assert first["ok"] is True
    mirrors.fail = True
    stale = await marketplace.list_plugins()
    assert stale["ok"] is True
    assert stale["source"]["stale"] is True
    assert stale["plugins"][0]["approved_commit"] == "a" * 40


@pytest.mark.asyncio
async def test_marketplace_listing_status_is_enforced_before_release_resolution():
    marketplace = PluginMarketplace(FakeMirrors(b"snapshot"))
    with pytest.raises(ValueError, match="暂停"):
        await marketplace.resolve_release(_market_item(
            listing_status="paused",
            listing_status_reason="版权投诉待核验",
        ))


def test_bundled_plugin_is_visible_but_not_installable():
    item = _market_item(distribution="bundled", trust_level="official")

    assert item["installable"] is False
    assert "随 DiceFrame" in item["verification_error"]


def test_reserved_plugin_type_is_visible_but_not_installable():
    item = _market_item(manifest={
        "id": "demo-pack",
        "name": "Demo",
        "version": "1.0.0",
        "plugin_type": "import-export",
    })

    assert item["support"]["level"] == "reserved"
    assert item["installable"] is False


def test_provider_plugin_type_is_installable():
    item = _market_item(manifest={
        "id": "demo-provider",
        "name": "Demo Provider",
        "version": "1.0.0",
        "plugin_type": "provider",
    })

    assert item["support"]["level"] == "supported"
    assert item["installable"] is True


def test_content_pack_with_map_contributions_is_installable():
    item = _market_item(manifest={
        "id": "demo-map",
        "name": "Demo Map",
        "version": "1.0.0",
        "plugin_type": "content-pack",
        "contributes": {"map_definitions": ["maps/*.json"]},
    })

    assert item["support"]["level"] == "supported"
    assert item["installable"] is True


@pytest.mark.asyncio
async def test_marketplace_downloads_latest_release_at_resolved_commit():
    payload = b"repository snapshot"
    mirrors = FakeMirrors(payload)
    marketplace = PluginMarketplace(mirrors)
    item = _market_item()

    async def listing():
        return {"ok": True, "plugins": [item]}

    marketplace.list_plugins = listing
    result = await marketplace.package_for_plugin("demo-pack")

    assert result["ok"] is True
    assert result["payload"] == payload
    assert result["plugin"]["commit_sha"] == "a" * 40
    assert mirrors.download_url.endswith(f"/{'a' * 40}.zip")
    assert mirrors.max_bytes is not None


@pytest.mark.asyncio
async def test_marketplace_uses_pinned_approved_commit_without_latest_lookup():
    payload = b"approved archive"

    class PinnedMirrors(FakeMirrors):
        def __init__(self):
            super().__init__(payload)
            self.api_paths = []

        async def fetch_github_api(self, api_path, **kwargs):
            self.api_paths.append(api_path)
            if api_path.endswith("/releases/latest"):
                raise AssertionError("approved entries must not resolve latest")
            return FetchResult(ok=True, data=json.dumps({"sha": "b" * 40}), status=200)

    mirrors = PinnedMirrors()
    marketplace = PluginMarketplace(mirrors)
    item = _market_item(
        approved_release_tag="v1.1.0",
        approved_commit="b" * 40,
        approved_artifact_sha256=hashlib.sha256(payload).hexdigest(),
    )

    async def listing():
        return {"ok": True, "plugins": [item]}

    marketplace.list_plugins = listing
    result = await marketplace.package_for_plugin("demo-pack")

    assert result["ok"] is True
    assert result["plugin"]["commit_sha"] == "b" * 40
    assert all(not path.endswith("/releases/latest") for path in mirrors.api_paths)


@pytest.mark.asyncio
async def test_marketplace_rejects_a_tampered_approved_artifact():
    payload = b"tampered archive"
    mirrors = FakeMirrors(payload)
    marketplace = PluginMarketplace(mirrors)
    item = _market_item(
        approved_release_tag="v1.1.0",
        approved_commit="b" * 40,
        approved_artifact_sha256="0" * 64,
    )

    async def listing():
        return {"ok": True, "plugins": [item]}

    marketplace.list_plugins = listing
    result = await marketplace.package_for_plugin("demo-pack")

    assert result["ok"] is False
    assert "approved artifact" in result["error"]


@pytest.mark.asyncio
async def test_marketplace_blocks_permission_expansion():
    mirrors = FakeMirrors(b"snapshot", manifest={
        "schema_version": 1,
        "id": "demo-pack",
        "name": "Demo",
        "version": "1.2.0",
        "description": "demo",
        "plugin_type": "content-pack",
        "permissions": ["content.read", "network.client"],
    })
    marketplace = PluginMarketplace(mirrors)

    with pytest.raises(ValueError, match="重新审核"):
        await marketplace.resolve_release(_market_item())


def test_official_github_source_is_attempted_before_proxies(tmp_path):
    manager = MirrorManager(tmp_path / "mirrors.json")

    assert manager.enabled()[0]["id"] == "github"


def test_streaming_download_timeout_has_no_total_deadline():
    timeout = _streaming_download_timeout(20)

    assert timeout.total is None
    assert timeout.connect == 20
    assert timeout.sock_connect == 20
    assert timeout.sock_read == 60


@pytest.mark.asyncio
async def test_text_fetch_enforces_max_bytes_for_chunked_responses(monkeypatch, tmp_path):
    class FakeContent:
        async def iter_chunked(self, _size):
            yield b"1234"

    class FakeResponse:
        status = 200
        content_length = None
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeSession:
        def __init__(self, *, timeout, headers):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, _url):
            return FakeResponse()

    monkeypatch.setattr("src.plugin_host.mirrors.aiohttp.ClientSession", FakeSession)
    manager = MirrorManager(tmp_path / "mirrors.json", max_attempts=1)

    result = await manager._fetch(
        "https://example.com/content.txt",
        {"id": "test", "name": "Test"},
        1,
        1,
        binary=False,
        max_bytes=3,
    )

    assert result.ok is False
    assert "超过限制" in result.error


def test_normalize_market_item_passes_through_latest_field():
    item = _market_item(latest={
        "version": "1.2.0",
        "release_tag": "v1.2.0",
        "release_url": "https://github.com/example/demo/releases/tag/v1.2.0",
        "commit_sha": "a" * 40,
        "published_at": "2026-08-01T00:00:00Z",
        "requires_approval": False,
    })

    assert item["latest"]["version"] == "1.2.0"
    assert item["latest"]["release_tag"] == "v1.2.0"
    assert item["latest"]["requires_approval"] is False


def test_normalize_market_item_returns_empty_latest_when_missing():
    item = _market_item()

    assert item["latest"] == {}


@pytest.mark.asyncio
async def test_streaming_download_uses_the_download_timeout(monkeypatch, tmp_path):
    captured = {}

    class FakeContent:
        async def iter_chunked(self, _size):
            yield b"payload"

    class FakeResponse:
        status = 200
        content_length = 7
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeSession:
        def __init__(self, *, timeout, headers):
            captured["timeout"] = timeout
            captured["headers"] = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, _url):
            return FakeResponse()

    monkeypatch.setattr("src.plugin_host.mirrors.aiohttp.ClientSession", FakeSession)
    manager = MirrorManager(tmp_path / "mirrors.json", timeout_sec=20, max_attempts=1)
    target = tmp_path / "update.zip"

    result = await manager._download_to_file(
        "https://github.com/example/update.zip",
        {"id": "github", "name": "GitHub"},
        1,
        1,
        target,
    )

    assert result.ok is True
    assert target.read_bytes() == b"payload"
    assert captured["timeout"].total is None
    assert captured["timeout"].sock_read == 60
