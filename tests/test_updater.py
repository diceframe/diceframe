"""#8 自动更新后端：select_asset 配对、SHA-256 校验、状态机、镜像复用。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugin_host.mirrors import FetchResult
from src.webui.services import updater

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
    monkeypatch.delenv("TRPG_INSTALL_ROOT", raising=False)
    monkeypatch.delenv("TRPG_DATA_DIR", raising=False)
    monkeypatch.setattr(updater.os, "access", lambda *a, **k: True)
    result = updater.is_self_update_supported(tmp_path)
    assert result["supported"] is True
    assert result["mode"] == "source"


def test_self_update_supported_in_portable_launcher(tmp_path, monkeypatch):
    monkeypatch.delenv("TRPG_DATA_DIR", raising=False)
    monkeypatch.setenv("TRPG_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setattr(updater.os, "access", lambda *a, **k: True)
    result = updater.is_self_update_supported(tmp_path)
    assert result["supported"] is True
    assert result["mode"] == "portable"


def test_self_update_unsupported_in_git_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("TRPG_INSTALL_ROOT", raising=False)
    monkeypatch.delenv("TRPG_DATA_DIR", raising=False)
    (tmp_path / ".git").mkdir()
    result = updater.is_self_update_supported(tmp_path)
    assert result["supported"] is False
    assert result["reason"] == "development"


def test_safe_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("DiceFrame/../../outside.txt", "bad")
    with pytest.raises(ValueError, match="不安全路径"):
        updater.safe_extract_archive(archive, tmp_path / "extract")
    assert not (tmp_path / "outside.txt").exists()


# ---------- UpdaterService 状态机 ----------

def _make_service(tmp_path, mirrors=None) -> updater.UpdaterService:
    if mirrors is None:
        mirrors = SimpleNamespace()
    return updater.UpdaterService(tmp_path, tmp_path, mirrors)


def _api_with_update_result(result: dict) -> SimpleNamespace:
    async def check_updates():
        return result

    return SimpleNamespace(check_updates=check_updates)


@pytest.mark.asyncio
async def test_download_update_success_flow(tmp_path):
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

    svc = _make_service(tmp_path, mirrors)
    result = await svc.download_update(
        _api_with_update_result({"ok": True, "latest": latest}),
        "source",
    )
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
async def test_download_update_progress_updates_bytes(tmp_path):
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

    svc = _make_service(tmp_path, mirrors)
    await svc.download_update(
        _api_with_update_result({"ok": True, "latest": latest}),
        "source",
    )
    await svc._task
    # 最终进度应等于文件大小
    assert svc.get_status()["downloaded_bytes"] == 1024


@pytest.mark.asyncio
async def test_download_update_busy_rejected(tmp_path):
    svc = _make_service(tmp_path, SimpleNamespace())
    svc._state["state"] = "downloading"
    result = await svc.download_update(SimpleNamespace(), "source")
    assert result["ok"] is False
    assert "进行中" in result["error"]


@pytest.mark.asyncio
async def test_download_update_no_release(tmp_path):
    svc = _make_service(tmp_path, SimpleNamespace())
    result = await svc.download_update(
        _api_with_update_result({"ok": True, "no_release": True, "latest": None}),
        "source",
    )
    assert result["ok"] is False
    assert result.get("no_release") is True


@pytest.mark.asyncio
async def test_download_update_no_matching_asset(tmp_path):
    latest = {"version": "1.6.0", "assets": [_asset("other.zip")]}
    svc = _make_service(tmp_path, SimpleNamespace())
    result = await svc.download_update(
        _api_with_update_result({"ok": True, "latest": latest}),
        "source",
    )
    assert result["ok"] is False
    assert "未找到" in result["error"]


@pytest.mark.asyncio
async def test_download_update_unknown_kind_rejected(tmp_path):
    svc = _make_service(tmp_path, SimpleNamespace())
    result = await svc.download_update(SimpleNamespace(), "foo")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_download_update_rejects_portable_package_for_source_install(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TRPG_INSTALL_ROOT", raising=False)
    monkeypatch.delenv("TRPG_DATA_DIR", raising=False)
    svc = _make_service(tmp_path, SimpleNamespace())
    result = await svc.download_update(SimpleNamespace(), "portable")
    assert result["ok"] is False
    assert "完整源码" in result["error"]
    assert svc._task is None


@pytest.mark.asyncio
async def test_download_update_rejects_source_package_for_portable_install(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TRPG_DATA_DIR", raising=False)
    monkeypatch.setenv("TRPG_INSTALL_ROOT", str(tmp_path))
    svc = _make_service(tmp_path, SimpleNamespace())
    result = await svc.download_update(SimpleNamespace(), "source")
    assert result["ok"] is False
    assert "便携版" in result["error"]
    assert svc._task is None


@pytest.mark.asyncio
async def test_download_update_sha_mismatch_marks_failed(tmp_path):
    content = b"real content"
    mirrors = SimpleNamespace()
    mirrors.fetch_github_url = lambda url, *, binary=False, max_bytes=None: _async_fetch("0" * 64)
    mirrors.download_to_file = lambda url, target, *, max_bytes=None, on_progress=None: _async_download(target, content)
    latest = {"version": "1.6.0", "assets": [_asset(ZIP_NAME), _asset(ZIP_NAME + ".sha256")]}

    svc = _make_service(tmp_path, mirrors)
    await svc.download_update(
        _api_with_update_result({"ok": True, "latest": latest}),
        "source",
    )
    await svc._task
    status = svc.get_status()
    assert status["state"] == "failed"
    assert "SHA-256" in status["error"]


@pytest.mark.asyncio
async def test_download_update_download_failure_marks_failed(tmp_path):
    mirrors = SimpleNamespace()

    async def fake_fetch(url, *, binary=False, max_bytes=None):
        return FetchResult(ok=True, data="a" * 64)

    async def fake_download(url, target, *, max_bytes=None, on_progress=None):
        return FetchResult(ok=False, error="镜像源均失败")

    mirrors.fetch_github_url = fake_fetch
    mirrors.download_to_file = fake_download
    latest = {"version": "1.6.0", "assets": [_asset(ZIP_NAME), _asset(ZIP_NAME + ".sha256")]}

    svc = _make_service(tmp_path, mirrors)
    await svc.download_update(
        _api_with_update_result({"ok": True, "latest": latest}),
        "source",
    )
    await svc._task
    status = svc.get_status()
    assert status["state"] == "failed"
    assert "镜像源均失败" in status["error"]


@pytest.mark.asyncio
async def test_download_update_sha_sidecar_missing_skips_verify(tmp_path):
    content = b"no sidecar"
    mirrors = SimpleNamespace()

    async def fake_download(url, target, *, max_bytes=None, on_progress=None):
        target.write_bytes(content)
        return FetchResult(ok=True, mirror_name="m")

    mirrors.fetch_github_url = lambda url, *, binary=False, max_bytes=None: _async_fetch(None)
    mirrors.download_to_file = fake_download
    # 无 .sha256 asset
    latest = {"version": "1.6.0", "assets": [_asset(ZIP_NAME)]}

    svc = _make_service(tmp_path, mirrors)
    await svc.download_update(
        _api_with_update_result({"ok": True, "latest": latest}),
        "source",
    )
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


def _write_portable_archive(path: Path, version: str = "1.7.0") -> None:
    top = f"DiceFrame-v{version}-windows-portable"
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(f"{top}/app/web_server.py", "# candidate")
        package.writestr(f"{top}/python/python.exe", b"python")
        package.writestr(f"{top}/DiceFrame.exe", b"launcher")


@pytest.mark.asyncio
async def test_portable_apply_stages_side_by_side_and_restart_signal(
    tmp_path, monkeypatch
):
    install_root = tmp_path / "DiceFrame"
    data_dir = install_root / "data"
    app_dir = install_root / "app"
    app_dir.mkdir(parents=True)
    updater_dir = data_dir / "_updater"
    updater_dir.mkdir(parents=True)
    archive = updater_dir / "portable.zip"
    _write_portable_archive(archive)
    monkeypatch.setenv("TRPG_INSTALL_ROOT", str(install_root))
    monkeypatch.delenv("TRPG_DATA_DIR", raising=False)

    svc = updater.UpdaterService(data_dir, app_dir, SimpleNamespace())
    svc._state = {
        "state": "staged",
        "version": "1.7.0",
        "kind": "portable",
        "path": str(archive),
    }
    svc._save_state()

    result = await svc.apply_update()
    assert result["ok"] is True
    await svc._task

    status = svc.get_status()
    assert status["state"] == "restarting"
    candidate = install_root / "versions" / "v1.7.0"
    assert (candidate / "app" / "web_server.py").is_file()
    assert (candidate / "python" / "python.exe").is_file()
    signal = json.loads(
        (updater_dir / "restart_signal.json").read_text(encoding="utf-8")
    )
    assert signal["expected_version"] == "1.7.0"
    assert Path(signal["candidate_dir"]) == candidate
    assert not archive.exists()


def _write_source_archive(path: Path, version: str = "1.7.0") -> None:
    top = f"DiceFrame-v{version}-windows"
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(f"{top}/web_server.py", "# new server")
        package.writestr(f"{top}/src/version.py", '__version__ = "1.7.0"')
        package.writestr(f"{top}/static-v2/index.html", "<html>new</html>")


@pytest.mark.asyncio
async def test_source_apply_creates_backup_and_requires_restart(
    tmp_path, monkeypatch
):
    root = tmp_path / "source-install"
    data_dir = root / "data"
    updater_dir = data_dir / "_updater"
    updater_dir.mkdir(parents=True)
    (root / "web_server.py").write_text("# old server", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "version.py").write_text("old", encoding="utf-8")
    (root / "static-v2").mkdir()
    (root / "static-v2" / "index.html").write_text("old", encoding="utf-8")
    archive = updater_dir / "source.zip"
    _write_source_archive(archive)
    old_backup = updater_dir / "backup-20000101-000000"
    old_backup.mkdir()
    (old_backup / "stale.txt").write_text("stale", encoding="utf-8")
    monkeypatch.delenv("TRPG_INSTALL_ROOT", raising=False)
    monkeypatch.delenv("TRPG_DATA_DIR", raising=False)

    svc = updater.UpdaterService(data_dir, root, SimpleNamespace())
    svc._state = {
        "state": "staged",
        "version": "1.7.0",
        "kind": "source",
        "path": str(archive),
    }
    svc._save_state()
    result = await svc.apply_update()
    assert result["ok"] is True
    await svc._task

    status = svc.get_status()
    assert status["state"] == "done"
    assert status["restart_needed"] is True
    assert (root / "web_server.py").read_text(encoding="utf-8") == "# new server"
    backup = Path(status["backup_dir"])
    assert (backup / "web_server.py").read_text(encoding="utf-8") == "# old server"
    assert not archive.exists()
    assert not old_backup.exists()
    assert sorted(updater_dir.glob("backup-*")) == [backup]


def test_source_apply_restores_backup_when_install_fails(
    tmp_path, monkeypatch
):
    root = tmp_path / "source-install"
    data_dir = root / "data"
    updater_dir = data_dir / "_updater"
    updater_dir.mkdir(parents=True)
    (root / "web_server.py").write_text("# old server", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "version.py").write_text("old", encoding="utf-8")
    (root / "static-v2").mkdir()
    (root / "static-v2" / "index.html").write_text("old", encoding="utf-8")
    archive = updater_dir / "source.zip"
    _write_source_archive(archive)
    monkeypatch.delenv("TRPG_INSTALL_ROOT", raising=False)

    svc = updater.UpdaterService(data_dir, root, SimpleNamespace())
    original_move = updater.shutil.move
    failed_once = False

    def failing_move(source, target):
        nonlocal failed_once
        source_path = Path(source)
        if (
            not failed_once
            and source_path.name == "src"
            and "extract-v1.7.0" in source_path.parts
        ):
            failed_once = True
            raise OSError("simulated install failure")
        return original_move(source, target)

    monkeypatch.setattr(updater.shutil, "move", failing_move)
    with pytest.raises(updater.UpdateRolledBackError, match="已恢复旧版本"):
        svc._apply_source_update(archive, "1.7.0")

    assert (root / "web_server.py").read_text(encoding="utf-8") == "# old server"
    assert (root / "src" / "version.py").read_text(encoding="utf-8") == "old"
    assert (root / "static-v2" / "index.html").read_text(encoding="utf-8") == "old"


def test_restart_state_is_preserved_while_signal_exists(tmp_path):
    updater_dir = tmp_path / "_updater"
    updater_dir.mkdir()
    (updater_dir / "state.json").write_text(
        '{"state": "restarting", "version": "1.7.0"}',
        encoding="utf-8",
    )
    (updater_dir / "restart_signal.json").write_text("{}", encoding="utf-8")
    svc = _make_service(tmp_path, SimpleNamespace())
    assert svc.get_status()["state"] == "restarting"


def test_restart_state_observes_launcher_completion(tmp_path):
    updater_dir = tmp_path / "_updater"
    updater_dir.mkdir()
    state_file = updater_dir / "state.json"
    state_file.write_text(
        '{"state": "restarting", "version": "1.7.0"}',
        encoding="utf-8",
    )
    (updater_dir / "restart_signal.json").write_text("{}", encoding="utf-8")
    svc = _make_service(tmp_path, SimpleNamespace())

    state_file.write_text(
        '{"state": "done", "version": "1.7.0", "restart_needed": false}',
        encoding="utf-8",
    )
    assert svc.get_status()["state"] == "done"


def test_restart_state_fails_when_signal_is_missing(tmp_path):
    updater_dir = tmp_path / "_updater"
    updater_dir.mkdir()
    (updater_dir / "state.json").write_text(
        '{"state": "restarting", "version": "1.7.0"}',
        encoding="utf-8",
    )
    svc = _make_service(tmp_path, SimpleNamespace())
    assert svc.get_status()["state"] == "failed"
    assert "握手" in svc.get_status()["error"]


# ---------- mock helpers ----------

async def _async_fetch(data):
    if data is None:
        return FetchResult(ok=False, error="404")
    return FetchResult(ok=True, data=f"{data}  {ZIP_NAME}")


async def _async_download(target, content):
    target.write_bytes(content)
    return FetchResult(ok=True, mirror_name="m")
