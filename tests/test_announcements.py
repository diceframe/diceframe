from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest

from src.webui.routes import announcements
from src.webui.services import announcements as announcements_service
from src.webui.services import content as content_service


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    # 公告与公共内容服务的进程内缓存都需要隔离，避免测试互相污染。
    monkeypatch.setattr(announcements_service, "_CACHE", {})
    monkeypatch.setattr(content_service, "_CACHE", {})
    monkeypatch.setattr(content_service, "_FAILURE_UNTIL", {})
    monkeypatch.setattr(content_service, "_INFLIGHT", {})


class FakeAPI:
    def __init__(self, content_cache_dir=None):
        self.content = content_service.PublicContentService(content_cache_dir)

    async def fetch_public_content_text(self, path, **kwargs):
        return await self.content.fetch_text(path, **kwargs)

    def public_content_disk_age(self, path):
        return self.content.disk_cache_age(path)


def _announcement_service(api: FakeAPI) -> announcements_service.AnnouncementService:
    return announcements_service.AnnouncementService(
        announcements_service.AnnouncementDependencies(
            fetch_public_text=api.fetch_public_content_text,
            disk_cache_age=api.public_content_disk_age,
        )
    )


def _remote(text_by_path):
    async def fake_remote(path):
        return text_by_path.get(path, "")
    return fake_remote


# ---- service：在线成功、缓存、重启磁盘命中、离线、语言隔离 ----

@pytest.mark.asyncio
async def test_online_success_returns_content_and_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(
        content_service, "_fetch_remote",
        _remote({"announcements/zh.md": "# 你好\n公告"}),
    )
    api = FakeAPI(content_cache_dir=tmp_path)

    result = await _announcement_service(api).fetch("zh-CN")

    assert result["fetched"] is True
    assert result["content"] == "# 你好\n公告"
    assert len(result["hash"]) == 16
    assert result["stale"] is False


@pytest.mark.asyncio
async def test_public_content_uses_configured_github_mirrors(tmp_path):
    calls = []

    class MirrorManager:
        async def fetch_github_url(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(ok=True, data="mirrored content")

    service = content_service.PublicContentService(
        tmp_path,
        mirror_manager=MirrorManager(),
    )

    result = await service.fetch_text("manifest.json", force_refresh=True, allow_cached=False)

    assert result == "mirrored content"
    assert calls == [(
        "https://raw.githubusercontent.com/diceframe/diceframe-content/main/content/manifest.json",
        {"binary": False, "max_bytes": content_service._MAX_BYTES, "resolve": False},
    )]


@pytest.mark.asyncio
async def test_memory_hit_skips_network(tmp_path, monkeypatch):
    calls = {"n": 0}

    async def counting_remote(path):
        calls["n"] += 1
        return "公告"

    monkeypatch.setattr(content_service, "_fetch_remote", counting_remote)
    api = FakeAPI(content_cache_dir=tmp_path)

    first = await _announcement_service(api).fetch("zh-CN")
    second = await _announcement_service(api).fetch("zh-CN")

    assert first == second
    assert calls["n"] == 1  # 第二次命中公告内存缓存


@pytest.mark.asyncio
async def test_restart_offline_serves_disk_cache_and_marks_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(
        content_service, "_fetch_remote",
        _remote({"announcements/zh.md": "上次公告"}),
    )
    api = FakeAPI(content_cache_dir=tmp_path)
    fresh = await _announcement_service(api).fetch("zh-CN")
    assert fresh["content"] == "上次公告"
    assert fresh["stale"] is False

    # 模拟重启：清空所有进程内缓存，把磁盘缓存文件年龄改为 2 小时前，随后断网。
    monkeypatch.setattr(announcements_service, "_CACHE", {})
    monkeypatch.setattr(content_service, "_CACHE", {})
    monkeypatch.setattr(content_service, "_FAILURE_UNTIL", {})
    monkeypatch.setattr(content_service, "_INFLIGHT", {})
    cache_path = api.content.cache_file("announcements/zh.md")
    assert cache_path is not None and cache_path.exists()
    old = time.time() - 7200
    os.utime(cache_path, (old, old))
    monkeypatch.setattr(content_service, "_fetch_remote", _remote({}))

    result = await _announcement_service(api).fetch("zh-CN")

    assert result["fetched"] is True
    assert result["content"] == "上次公告"
    assert result["stale"] is True


@pytest.mark.asyncio
async def test_offline_without_cache_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(content_service, "_fetch_remote", _remote({}))
    api = FakeAPI(content_cache_dir=tmp_path)

    result = await _announcement_service(api).fetch("zh-CN")

    assert result == {"content": "", "hash": "", "fetched": False, "stale": False}


@pytest.mark.asyncio
async def test_zh_and_en_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(
        content_service, "_fetch_remote",
        _remote({
            "announcements/zh.md": "中文公告",
            "announcements/en.md": "English notice",
        }),
    )
    api = FakeAPI(content_cache_dir=tmp_path)

    zh = await _announcement_service(api).fetch("zh-CN")
    en = await _announcement_service(api).fetch("en")

    assert zh["content"] == "中文公告"
    assert en["content"] == "English notice"
    assert zh["hash"] != en["hash"]
    # 再次请求仍保持隔离（各自命中自己的缓存）。
    assert (await _announcement_service(api).fetch("zh-CN")) == zh
    assert (await _announcement_service(api).fetch("en")) == en


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_fetch(tmp_path, monkeypatch):
    calls = {"n": 0}

    async def delayed_remote(path):
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return "公告"

    monkeypatch.setattr(content_service, "_fetch_remote", delayed_remote)
    api = FakeAPI(content_cache_dir=tmp_path)

    first, second = await asyncio.gather(
        _announcement_service(api).fetch("zh-CN"),
        _announcement_service(api).fetch("zh-CN"),
    )

    assert calls["n"] == 1  # content.py 单飞合并并发请求
    assert first == second


@pytest.mark.asyncio
async def test_stale_memory_cache_survives_upstream_failure(tmp_path, monkeypatch):
    now = time.monotonic()
    monkeypatch.setattr(announcements_service, "_CACHE", {
        "zh.md": {"content": "旧公告", "hash": "old", "fetched_at": now - 700},
    })
    monkeypatch.setattr(content_service, "_fetch_remote", _remote({}))
    api = FakeAPI(content_cache_dir=tmp_path)

    result = await _announcement_service(api).fetch("zh-CN")

    assert result == {"content": "旧公告", "hash": "old", "fetched": True, "stale": True}


def test_language_mapping():
    assert announcements_service._file_for_language("zh-CN") == "zh.md"
    assert announcements_service._file_for_language("zh-TW") == "zh.md"
    assert announcements_service._file_for_language("en") == "en.md"
    assert announcements_service._file_for_language("ja") == "en.md"
    assert announcements_service._file_for_language("") == "en.md"


# ---- route ----

def _make_request(query=None, owner=False):
    class FakeRequest:
        def __init__(self):
            self.query = query or {}
            self.headers = {}
            self._owner = owner

        def get(self, key, default=None):
            return {"owner_authenticated": self._owner}.get(key, default)

    class FakeApi:
        async def get_official_announcement(self, language="zh-CN"):
            return {"content": "公告", "hash": "abc", "fetched": True, "stale": False}

    req = FakeRequest()
    req.app = {"api": FakeApi()}
    return req, FakeApi()


@pytest.mark.asyncio
async def test_route_returns_public_payload():
    req, api = _make_request(query={"lang": "zh-CN"})
    resp = await announcements.api_announcements(req)
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload == {"content": "公告", "hash": "abc", "fetched": True, "stale": False}
