"""#8 自动更新后端：select_asset 配对、SHA-256 校验、状态机、镜像复用。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugin_host.mirrors import FetchResult
from src.webui.services import system, updater

ZIP_NAME = "DiceFrame-v1.6.0-windows.zip"
PORTABLE_NAME = "DiceFrame-v1.6.0-windows-portable.zip"


def _asset(name: str) -> dict:
    return {"name": name, "download_url": f"https://example.com/{name}", "size": 1024}


# ---------- select_asset ----------

def test_select_asset_pairs_source_zip_and_sha256():
    latest = {"assets": [_asset(ZIP_NAME), _asset(ZIP_NAME + ".sha256")]}
    selection = updater.select_asset(latest, "source")
    assert selection is not None
    zip_asset, sha_asset = selection
    assert zip_asset["name"] == ZIP_NAME
    assert sha_asset is not None
    assert sha_asset["name"] == ZIP_NAME + ".sha256"


def test_select_asset_portable_excludes_source_zip():
    latest = {"assets": [_asset(PORTABLE_NAME), _asset(ZIP_NAME), _asset(PORTABLE_NAME + ".sha256")]}
    selection = updater.select_asset(latest, "portable")
    assert selection is not None
    zip_asset, sha_asset = selection
    assert zip_asset["name"] == PORTABLE_NAME
    assert sha_asset["name"] == PORTABLE_NAME + ".sha256"


def test_select_asset_sha_none_when_sidecar_missing():
    latest = {"assets": [_asset(ZIP_NAME)]}
    selection = updater.select_asset(latest, "source")
    assert selection is not None
    zip_asset, sha_asset = selection
    assert zip_asset["name"] == ZIP_NAME
    assert sha_asset is None


def test_select_asset_returns_none_for_unknown_kind():
    assert updater.select_asset({"assets": [_asset(ZIP_NAME)]}, "foo") is None


def test_select_asset_returns_none_when_no_match():
    assert updater.select_asset({"assets": []}, "source") is None


# ---------- parse_sha256_file ----------

def test_parse_sha256_file_with_filename_line():
    digest = "a" * 64
    assert updater.parse_sha256_file(f"{digest}  {ZIP_NAME}", ZIP_NAME) == digest


def test_parse_sha256_file_bare_hash():
    digest = "b" * 64
    assert updater.parse_sha256_file(digest, ZIP_NAME) == digest


def test_parse_sha256_file_raises_when_no_hash():
    with pytest.raises(ValueError):
        updater.parse_sha256_file("no hash here", ZIP_NAME)


# ---------- verify_sha256 ----------

def test_verify_sha256_correct(tmp_path):
    content = b"fake zip bytes"
    path = tmp_path / "f.zip"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    updater.verify_sha256(path, digest)  # 不抛即通过


def test_verify_sha256_mismatch_raises(tmp_path):
    path = tmp_path / "f.zip"
    path.write_bytes(b"content")
    with pytest.raises(ValueError):
        updater.verify_sha256(path, "0" * 64)


def test_verify_sha256_invalid_format_raises(tmp_path):
    path = tmp_path / "f.zip"
    path.write_bytes(b"content")
    with pytest.raises(ValueError):
        updater.verify_sha256(path, "not-a-hex")


# ---------- is_self_update_supported ----------

def test_self_update_unsupported_in_docker(tmp_path, monkeypatch):
    monkeypatch.setenv("TRPG_DATA_DIR", "/app/data")
    result = updater.is_self_update_supported(tmp_path)
    assert result["supported"] is False
    assert result["reason"] == "docker"


def test_self_update_unsupported_when_readonly(tmp_path, monkeypatch):
    monkeypatch.delenv("TRPG_DATA_DIR", raising=False)
    monkeypatch.setattr(updater.os, "access", lambda *a, **k: False)
    result = updater.is_self_update_supported(tmp_path)
    assert result["supported"] is False
    assert result["reason"] == "readonly"


def test_self_update_supported_when_writable(tmp_path, monkeypatch):
    monkeypatch.delenv("TRPG_DATA_DIR", raising=False)
    monkeypatch.setattr(updater.os, "access", lambda *a, **k: True)
    result = updater.is_self_update_supported(tmp_path)
    assert result["supported"] is True


# ---------- UpdaterService 状态机 ----------

def _make_service(tmp_path, mirrors=None) -> updater.UpdaterService:
    if mirrors is None:
        mirrors = SimpleNamespace()
    return updater.UpdaterService(tmp_path, tmp_path, mirrors)


@pytest.mark.asyncio
async def test_download_update_success_flow(tmp_path, monkeypatch):
    content = b"fake zip content"
    digest = hashlib.sha256(content).hexdigest()
    mirrors = SimpleNamespace()

    async def fake_fetch(url, *, binary=False, max_bytes=None):
        return FetchResult(ok=True, data=f"{digest}  {ZIP_NAME}", mirror_name="测试镜像")

    async def fake_download(url, target, *, max_bytes=None, on_progress=None):
        target.write_bytes(content)
        if on_progress:
            on_progress(len(content), len(content))
        return FetchResult(ok=True, mirror_name="测试镜像")

    mirrors.fetch_github_url = fake_fetch
    mirrors.download_to_file = fake_download

    latest = {"version": "1.6.0", "assets": [_asset(ZIP_NAME), _asset(ZIP_NAME + ".sha256")]}

    async def fake_check(api, include_prerelease=False):
        return {"ok": True, "latest": latest}

    monkeypatch.setattr(system, "check_updates", fake_check)

    svc = _make_service(tmp_path, mirrors)
    result = await svc.download_update(SimpleNamespace(), "source")
    assert result["ok"] is True
    assert result["state"] == "downloading"
    await svc._task

    status = svc.get_status()
    assert status["state"] == "staged"
    assert status["version"] == "1.6.0"
    assert status["asset"] == ZIP_NAME
    assert status["sha256"] == digest
    assert status["mirror_used"] == "测试镜像"
    assert Path(status["path"]).read_bytes() == content
    assert status["current_version"]  # 来自 __version__


@pytest.mark.asyncio
async def test_download_update_progress_updates_bytes(tmp_path, monkeypatch):
    content = b"x" * 1024
    mirrors = SimpleNamespace()

    async def fake_fetch(url, *, binary=False, max_bytes=None):
        return FetchResult(ok=True, data="a" * 64)

    async def fake_download(url, target, *, max_bytes=None, on_progress=None):
        # 分两块写，触发两次进度回调
        target.write_bytes(content)
        if on_progress:
            on_progress(512, 1024)
            on_progress(1024, 1024)
        return FetchResult(ok=True, mirror_name="m")

    mirrors.fetch_github_url = fake_fetch
    mirrors.download_to_file = fake_download
    latest = {"version": "1.6.0", "assets": [_asset(ZIP_NAME), _asset(ZIP_NAME + ".sha256")]}
    monkeypatch.setattr(system, "check_updates", lambda api, include_prerelease=False: _async_ok(latest))

    svc = _make_service(tmp_path, mirrors)
    await svc.download_update(SimpleNamespace(), "source")
    await svc._task
    # 最终进度应等于文件大小
    assert svc.get_status()["downloaded_bytes"] == 1024


async def _async_ok(latest):
    return {"ok": True, "latest": latest}


@pytest.mark.asyncio
async def test_download_update_busy_rejected(tmp_path):
    svc = _make_service(tmp_path, SimpleNamespace())
    svc._state["state"] = "downloading"
    result = await svc.download_update(SimpleNamespace(), "source")
    assert result["ok"] is False
    assert "进行中" in result["error"]


@pytest.mark.asyncio
async def test_download_update_no_release(tmp_path, monkeypatch):
    async def fake_check(api, include_prerelease=False):
        return {"ok": True, "no_release": True, "latest": None}
    monkeypatch.setattr(system, "check_updates", fake_check)
    svc = _make_service(tmp_path, SimpleNamespace())
    result = await svc.download_update(SimpleNamespace(), "source")
    assert result["ok"] is False
    assert result.get("no_release") is True


@pytest.mark.asyncio
async def test_download_update_no_matching_asset(tmp_path, monkeypatch):
    latest = {"version": "1.6.0", "assets": [_asset("other.zip")]}
    monkeypatch.setattr(system, "check_updates", lambda api, include_prerelease=False: _async_ok(latest))
    svc = _make_service(tmp_path, SimpleNamespace())
    result = await svc.download_update(SimpleNamespace(), "source")
    assert result["ok"] is False
    assert "未找到" in result["error"]


@pytest.mark.asyncio
async def test_download_update_unknown_kind_rejected(tmp_path):
    svc = _make_service(tmp_path, SimpleNamespace())
    result = await svc.download_update(SimpleNamespace(), "foo")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_download_update_sha_mismatch_marks_failed(tmp_path, monkeypatch):
    content = b"real content"
    mirrors = SimpleNamespace()
    mirrors.fetch_github_url = lambda url, *, binary=False, max_bytes=None: _async_fetch("0" * 64)
    mirrors.download_to_file = lambda url, target, *, max_bytes=None, on_progress=None: _async_download(target, content)
    latest = {"version": "1.6.0", "assets": [_asset(ZIP_NAME), _asset(ZIP_NAME + ".sha256")]}
    monkeypatch.setattr(system, "check_updates", lambda api, include_prerelease=False: _async_ok(latest))

    svc = _make_service(tmp_path, mirrors)
    await svc.download_update(SimpleNamespace(), "source")
    await svc._task
    status = svc.get_status()
    assert status["state"] == "failed"
    assert "SHA-256" in status["error"]


@pytest.mark.asyncio
async def test_download_update_download_failure_marks_failed(tmp_path, monkeypatch):
    mirrors = SimpleNamespace()

    async def fake_fetch(url, *, binary=False, max_bytes=None):
        return FetchResult(ok=True, data="a" * 64)

    async def fake_download(url, target, *, max_bytes=None, on_progress=None):
        return FetchResult(ok=False, error="镜像源均失败")

    mirrors.fetch_github_url = fake_fetch
    mirrors.download_to_file = fake_download
    latest = {"version": "1.6.0", "assets": [_asset(ZIP_NAME), _asset(ZIP_NAME + ".sha256")]}
    monkeypatch.setattr(system, "check_updates", lambda api, include_prerelease=False: _async_ok(latest))

    svc = _make_service(tmp_path, mirrors)
    await svc.download_update(SimpleNamespace(), "source")
    await svc._task
    status = svc.get_status()
    assert status["state"] == "failed"
    assert "镜像源均失败" in status["error"]


@pytest.mark.asyncio
async def test_download_update_sha_sidecar_missing_skips_verify(tmp_path, monkeypatch):
    content = b"no sidecar"
    mirrors = SimpleNamespace()

    async def fake_download(url, target, *, max_bytes=None, on_progress=None):
        target.write_bytes(content)
        return FetchResult(ok=True, mirror_name="m")

    mirrors.fetch_github_url = lambda url, *, binary=False, max_bytes=None: _async_fetch(None)
    mirrors.download_to_file = fake_download
    # 无 .sha256 asset
    latest = {"version": "1.6.0", "assets": [_asset(ZIP_NAME)]}
    monkeypatch.setattr(system, "check_updates", lambda api, include_prerelease=False: _async_ok(latest))

    svc = _make_service(tmp_path, mirrors)
    await svc.download_update(SimpleNamespace(), "source")
    await svc._task
    status = svc.get_status()
    assert status["state"] == "staged"
    assert status["sha256"] == ""  # 跳过校验


def test_restart_marks_active_state_failed(tmp_path):
    updater_dir = tmp_path / "_updater"
    updater_dir.mkdir()
    (updater_dir / "state.json").write_text('{"state": "downloading", "asset": "x.zip"}', encoding="utf-8")
    svc = _make_service(tmp_path, SimpleNamespace())
    assert svc.get_status()["state"] == "failed"
    assert "中断" in svc.get_status()["error"]


# ---------- mock helpers ----------

async def _async_fetch(data):
    if data is None:
        return FetchResult(ok=False, error="404")
    return FetchResult(ok=True, data=f"{data}  {ZIP_NAME}")


async def _async_download(target, content):
    target.write_bytes(content)
    return FetchResult(ok=True, mirror_name="m")
