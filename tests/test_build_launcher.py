"""Windows 便携版启动器（src/launcher/*.cs）的构建、应用窗口依赖与覆盖升级判定。"""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts import build_launcher, build_portable


def test_launcher_build_compiles_every_partial_source():
    # 启动器是 partial class，按职责拆在多个文件里；漏编任何一个都会在 csc 阶段才炸。
    names = {Path(source).name for source in build_launcher.launcher_sources()}
    assert {"DiceFrameLauncher.cs", "OverwriteUpgrade.cs", "AppWindow.cs"} <= names


def test_webview2_sdk_rejects_a_tampered_package(tmp_path):
    package = tmp_path / f"microsoft.web.webview2.{build_launcher.WEBVIEW2_SDK_VERSION}.nupkg"
    with zipfile.ZipFile(package, "w") as nupkg:
        for member in build_launcher.WEBVIEW2_SDK_FILES:
            nupkg.writestr(member, b"not the real sdk")

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        build_launcher.ensure_webview2_sdk(tmp_path)
    # 校验失败的包不能留在缓存里，下次构建要重新下载。
    assert not package.exists()


def test_portable_payload_carries_the_webview2_sdk(tmp_path):
    # SDK 跟着 app/ 走：旧版更新器、新版更新器和覆盖升级都整体搬运 app/，
    # 每个版本都带着与自己启动器匹配的 SDK；BSD 许可证随二进制一起分发。
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    for name in build_launcher.WEBVIEW2_SDK_FILES.values():
        (sdk / name).write_bytes(name.encode())
    app_dir = tmp_path / "package" / "app"

    build_portable.stage_webview2(sdk, app_dir)

    shipped = sorted(path.name for path in (app_dir / "launcher" / "webview2").iterdir())
    assert shipped == sorted(build_launcher.WEBVIEW2_SDK_FILES.values())
    assert "LICENSE.txt" in shipped


def _compile_launcher(output: Path) -> None:
    try:
        sdk = build_launcher.ensure_webview2_sdk()
    except OSError as exc:  # 离线时没法取 SDK：跳过而不是误报失败。
        pytest.skip(f"WebView2 SDK unavailable: {exc}")
    subprocess.run(
        build_launcher.csc_command(output, sdk),
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(os.name != "nt", reason="启动器只能在 Windows 上用 csc 编译")
def test_overwrite_upgrade_version_rules(tmp_path):
    exe = tmp_path / "DiceFrame.exe"
    _compile_launcher(exe)
    pairs = [
        ["3.1.3", "3.1.2"],
        ["3.1.2", "3.1.3"],
        ["3.1.2", "3.1.2"],
        ["3.10.0", "3.9.0"],
        ["v3.1.3", "3.1.2"],
        ["3.2.0", "3.2.0-beta.1"],
        ["3.2.0-beta.10", "3.2.0-beta.9"],
        ["3.0.0-rc.1", "3.0.0-beta.3"],
        ["3.1.2+build.5", "3.1.2"],
        ["garbage", "3.1.2"],
    ]
    slots = ["3.1.3", "3.2.0-beta.1", "../x", ""]
    script = (
        "$t = [Reflection.Assembly]::LoadFile($env:LAUNCHER_EXE).GetType('DiceFrameLauncher');"
        "$f = [Reflection.BindingFlags]'NonPublic,Static';"
        "$cmp = $t.GetMethod('CompareVersions', $f);"
        "$slot = $t.GetMethod('VersionDirectoryName', $f);"
        "$pairs = $env:PAIRS | ConvertFrom-Json;"
        "$slots = $env:SLOTS | ConvertFrom-Json;"
        "@{"
        " compare = @($pairs | ForEach-Object { [Math]::Sign([int]$cmp.Invoke($null, @($_[0], $_[1]))) });"
        " slots = @($slots | ForEach-Object { [string]$slot.Invoke($null, @([string]$_)) })"
        "} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "LAUNCHER_EXE": str(exe), "PAIRS": json.dumps(pairs), "SLOTS": json.dumps(slots)},
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    # 只有严格更新才接管；同版本、旧版本、解析不了的一律不动（返回值 <= 0）。
    assert payload["compare"] == [1, -1, 0, 1, 1, 1, 1, 1, 0, 0]
    # 槽位命名与应用内更新器 _safe_version_dir 一致，拒绝路径穿越。
    assert payload["slots"] == ["v3.1.3", "v3.2.0-beta.1", "", ""]
