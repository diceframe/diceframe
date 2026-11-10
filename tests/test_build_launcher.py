"""Windows 便携版启动器（src/launcher/*.cs）的构建、应用窗口依赖与覆盖升级判定。"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
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


@pytest.mark.skipif(os.name != "nt", reason="启动器只能在 Windows 上用 csc 编译")
def test_launcher_rechecks_the_self_signed_fingerprint_after_a_live_switch(tmp_path):
    """运行中开启 HTTPS 时，服务端 os.execv 重启，启动器不会重新解析 endpoint。

    证书指纹必须按当前磁盘记录判定，而不是启动时的缓存：否则应用窗口会被自签证书
    拦在错误页。这里同时锁住 fail-closed：模式不是 self_signed 或记录缺失都不信任。
    """
    exe = tmp_path / "DiceFrame.exe"
    _compile_launcher(exe)
    data = tmp_path / "data"
    certs = data / "certs" / "self-signed"
    certs.mkdir(parents=True)
    fingerprint = ":".join(["AB"] * 32)
    (certs / "fingerprint.txt").write_text(fingerprint, encoding="utf-8")
    (data / "config.json").write_text('{"tls_mode": "off"}', encoding="utf-8")

    script = (
        "$t = [Reflection.Assembly]::LoadFile($env:LAUNCHER_EXE).GetType('DiceFrameLauncher');"
        "$f = [Reflection.BindingFlags]'NonPublic,Static';"
        "$t.GetField('launcherDataDir', $f).SetValue($null, $env:DATA_DIR);"
        "$read = $t.GetMethod('ReadRecordedFingerprint', $f);"
        "$val = $t.GetMethod('ValidateLocalCertificate', $f);"
        "$out = @{ off = [string]$read.Invoke($null, @()) };"
        "Set-Content -LiteralPath (Join-Path $env:DATA_DIR 'config.json')"
        " -Value '{\"tls_mode\": \"self_signed\"}' -NoNewline;"
        "$out.live = [string]$read.Invoke($null, @());"
        "Remove-Item -LiteralPath (Join-Path $env:DATA_DIR 'certs/self-signed/fingerprint.txt');"
        "$out.missing = [string]$read.Invoke($null, @());"
        "$out.null_cert = [bool]$val.Invoke($null, @($null,"
        " [System.Net.Security.SslPolicyErrors]::RemoteCertificateChainErrors));"
        "$out | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "LAUNCHER_EXE": str(exe), "DATA_DIR": str(data)},
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    # HTTP 模式下即便文件还在也不信任。
    assert payload["off"] == ""
    # 同一个进程内切到 self_signed（服务端重启，启动器不重启）后立刻读到指纹。
    assert payload["live"] == fingerprint
    # 记录被删后回到 fail-closed。
    assert payload["missing"] == ""
    assert payload["null_cert"] is False


def _serve_config(body: bytes) -> tuple[HTTPServer, int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定的方法名
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


@pytest.mark.skipif(os.name != "nt", reason="启动器只能在 Windows 上用 csc 编译")
def test_launcher_recognizes_an_already_running_instance(tmp_path):
    """端口上已有 DiceFrame 时启动器要接管它，而不是再 spawn 一个必然 10048 的后端。

    事故现场：切换 HTTPS 后服务端 os.execv 换了 PID，上一个启动器退出时留下了
    仍在监听 18000 的孤儿后端；之后每次双击都在 bind 上失败，窗口卡在启动画面。
    """
    feature = _serve_config(b'{"server_version": "9.9.9", "min_client_version": "0.6.0"}')
    other = _serve_config(b'{"hello": "world"}')
    exe = tmp_path / "DiceFrame.exe"
    try:
        _compile_launcher(exe)
        script = (
            "$t = [Reflection.Assembly]::LoadFile($env:LAUNCHER_EXE).GetType('DiceFrameLauncher');"
            "$f = [Reflection.BindingFlags]'NonPublic,Static';"
            "$t.GetMethod('InitLocalHttpClient', $f).Invoke($null, @()) | Out-Null;"
            "$probe = $t.GetMethod('IsDiceFrameServer', $f);"
            "@{"
            " diceframe = [bool]$probe.Invoke($null, @($env:UP));"
            " other = [bool]$probe.Invoke($null, @($env:OTHER));"
            " dead = [bool]$probe.Invoke($null, @('http://127.0.0.1:1'))"
            "} | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env={
                **os.environ,
                "LAUNCHER_EXE": str(exe),
                "UP": f"http://127.0.0.1:{feature[1]}",
                "OTHER": f"http://127.0.0.1:{other[1]}",
            },
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        feature[0].shutdown()
        other[0].shutdown()

    payload = json.loads(result.stdout)
    # 公开配置里的 server_version 是 DiceFrame 的特征：据此复用已有实例。
    assert payload["diceframe"] is True
    # 别的服务占着端口时绝不能当成自己的实例接管。
    assert payload["other"] is False
    # 没人监听就是没人监听。
    assert payload["dead"] is False
