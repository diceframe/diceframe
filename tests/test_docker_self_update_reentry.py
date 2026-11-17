"""「更新到自己」重入与依赖锁覆盖的回归。

v3.3.0-beta.1 的 Docker 更新有两层问题：更新包依赖锁缺 ``python-i18n``（候选
import 即崩、回滚报「候选进程已退出」）；以及前端状态滞后时重复应用当前版本
会让 launcher 把健康的自己杀掉重启。这里锁定三层防线：
依赖锁必须覆盖 requirements.txt、apply 对同版本短路、launcher 对同版本
signal 幂等。
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from src.docker_launcher import contracts
from src.docker_launcher.launcher import DockerLauncher

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _package_tree(root: Path, version: str) -> Path:
    package = root / f"DiceFrame-v{version}-docker-update-linux-amd64"
    (package / "app" / "src").mkdir(parents=True)
    (package / "app" / "static-v2").mkdir(parents=True)
    (package / "runtime" / "site-packages").mkdir(parents=True)
    (package / "app" / "web_server.py").write_text("# server\n", encoding="utf-8")
    (package / "app" / "static-v2" / "index.html").write_text("ok", encoding="utf-8")
    (package / "app" / "src" / "version.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8",
    )
    (package / "runtime" / "site-packages" / "runtime.txt").write_text("ok", encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps({
        "schema": 1,
        "version": version,
        "platform": "linux-amd64",
        "python_abi": "cp311",
        "launcher_schema_min": 1,
        "runtime_api": 1,
        "data_rollback_safe": True,
        "entrypoint": "app/web_server.py",
        "site_packages": "runtime/site-packages",
        "health_path": "/api/system/update/health",
        "probation_seconds": 90,
    }), encoding="utf-8")
    return package


def _launcher(tmp_path: Path, version: str = "9.9.9") -> DockerLauncher:
    runtime = tmp_path / "runtime"
    archive = tmp_path / "seed.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        package = _package_tree(tmp_path / "seed-src", version)
        for path in package.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(package.parent).as_posix())
    checksum = tmp_path / "seed.sha256"
    checksum.write_text(f"{contracts.file_sha256(archive)}  seed.zip\n", encoding="utf-8")
    return DockerLauncher(runtime, archive, checksum)


def test_docker_lock_covers_every_requirement(tmp_path):
    """依赖锁少钉一个包就必须拒绝出包（beta.1 缺 python-i18n 的直接教训）。"""
    sys.path.insert(0, str(SCRIPTS))
    try:
        import build_docker_update
    finally:
        sys.path.pop(0)

    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "aiohttp>=3\n# comment\npython-i18n[YAML]>=0.3.9,<0.4\n", encoding="utf-8",
    )
    complete = tmp_path / "ok.lock"
    complete.write_text(
        "aiohttp==3.14.1 --hash=sha256:" + "0" * 64 + "\n"
        "python-i18n==0.3.9 --hash=sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )
    stale = tmp_path / "stale.lock"
    stale.write_text(
        "aiohttp==3.14.1 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8",
    )

    original_root, original_lock = build_docker_update.ROOT, build_docker_update.LOCK_FILE
    build_docker_update.ROOT = tmp_path
    try:
        build_docker_update.LOCK_FILE = complete
        build_docker_update._assert_lock_covers_requirements()
        build_docker_update.LOCK_FILE = stale
        with pytest.raises(SystemExit, match="python-i18n"):
            build_docker_update._assert_lock_covers_requirements()
    finally:
        build_docker_update.ROOT = original_root
        build_docker_update.LOCK_FILE = original_lock


def test_launcher_ignores_restart_signal_for_the_running_version(tmp_path, monkeypatch):
    """对已在运行的版本重复 apply：清 signal，不杀进程、不重启。"""
    launcher = _launcher(tmp_path, "9.9.9")
    seed_dir, _seed_manifest = launcher.ensure_seed()
    runtime = tmp_path / "runtime"
    active = seed_dir.resolve()
    launcher.active_dir = active
    relative = active.relative_to(runtime).as_posix()

    launched: list[Path] = []
    monkeypatch.setattr(
        launcher, "_launch_candidate",
        lambda candidate, previous: launched.append(candidate) or True,
    )
    launcher.signal_file.write_text(json.dumps({
        "schema": 1,
        "mode": "docker",
        "expected_version": "9.9.9",
        "relative_dir": relative,
        "probation_seconds": 90,
    }), encoding="utf-8")

    launcher._handle_restart_signal()

    assert launched == [], "同版本 signal 不得触发候选切换"
    assert not launcher.signal_file.exists(), "signal 应被清除"
    state = json.loads((runtime / "state.json").read_text(encoding="utf-8"))
    assert state["state"] == "done"
    assert state["version"] == "9.9.9"


@pytest.mark.asyncio
async def test_apply_short_circuits_when_already_running_target(tmp_path, monkeypatch):
    """前端状态滞后时对当前版本重复 apply：直接收敛为 done，不写重启 signal。"""
    from types import SimpleNamespace

    from src.webui.services import updater as updater_module

    runtime = tmp_path / "_updater"
    runtime.mkdir(parents=True)
    monkeypatch.setenv("TRPG_INSTALL_MODE", "docker-managed")
    monkeypatch.setenv("TRPG_DOCKER_RUNTIME_ROOT", str(runtime))
    monkeypatch.setenv("TRPG_LEGACY_PLUGIN_DIR", str(tmp_path / "plugins"))

    archive = tmp_path / "DiceFrame-v9.9.9-docker-update-linux-amd64.zip"
    archive.write_bytes(b"payload")

    service = updater_module.UpdaterService.__new__(updater_module.UpdaterService)
    service._data_dir = tmp_path
    service._dir = tmp_path
    service._state_file = tmp_path / "update_state.json"
    service._restart_signal = runtime / "restart_signal.json"
    service._root = tmp_path
    service._mirrors = None
    service._check_updates = None
    service._task = None
    service._state = {
        "state": "staged",
        "version": "9.9.9",
        "kind": "docker",
        "asset": archive.name,
        "path": str(archive),
        "sha256": "0" * 64,
    }
    monkeypatch.setattr(updater_module, "__version__", "9.9.9")
    monkeypatch.setattr(
        updater_module, "is_self_update_supported",
        lambda _root: {"supported": True, "mode": "docker"},
    )

    result = await service.apply_update()

    assert result["ok"] is True
    assert result["state"] == "done"
    assert not service._restart_signal.exists()
    assert service._state["state"] == "done"
    assert service._state.get("error", "") == ""


async def _admission_request(path: str, method: str = "POST"):
    from types import SimpleNamespace

    return SimpleNamespace(path=path, method=method)


@pytest.mark.asyncio
async def test_update_admission_exempts_first_run_consent(monkeypatch):
    """版本闸门不得拦住首跑的协议/遥测同意（更新校验期新用户会被卡死在同意页）。"""
    from src.webui import update_admission

    monkeypatch.setattr(update_admission, "activation_pending", lambda _env: True)
    reached: list[str] = []

    async def handler(request):
        reached.append(request.path)
        return "served"

    for path in ("/api/hub/preferences", "/api/login"):
        result = await update_admission.update_admission(
            await _admission_request(path, "PATCH"), handler,
        )
        assert result == "served"
    blocked = await update_admission.update_admission(
        await _admission_request("/api/games/x/room-password", "POST"), handler,
    )
    assert blocked.status == 503
    assert reached == ["/api/hub/preferences", "/api/login"]
