"""Windows 便携版启动器（src/launcher/*.cs）的构建、应用窗口依赖与覆盖升级判定。"""

from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from scripts import build_launcher, build_portable
from src.web_transport.certificates.self_signed import SelfSignedCertificateProvider
from src.web_transport.certificates.storage import CertificateStore


def test_launcher_build_compiles_every_partial_source():
    # 启动器是 partial class，按职责拆在多个文件里；漏编任何一个都会在 csc 阶段才炸。
    names = {Path(source).name for source in build_launcher.launcher_sources()}
    assert {"DiceFrameLauncher.cs", "OverwriteUpgrade.cs", "AppWindow.cs", "ProcessEnvironment.cs"} <= names


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


# 两个事故都只在"裸 .NET"进程里复现：csc 直接产物没有 app.config，也不同于
# PowerShell 宿主（后者自带 4.8 运行时默认，TLS 默认已含 1.2、环境字典也重建过）。
# 探针必须与真实启动器同样方式编译，反射调用启动器的私有入口。
_PROBE_SOURCE = r"""
using System;
using System.IO;
using System.Net;
using System.Reflection;

internal static class Probe
{
    private static int Main(string[] args)
    {
        try
        {
            Type type = Assembly.LoadFile(Path.GetFullPath(args[0])).GetType("DiceFrameLauncher");
            BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            if (args[1] == "tls")
            {
                Console.WriteLine("default=" + ServicePointManager.SecurityProtocol);
                type.GetField("launcherDataDir", flags).SetValue(null, args[2]);
                type.GetMethod("InitLocalHttpClient", flags).Invoke(null, null);
                MethodInfo probe = type.GetMethod("IsDiceFrameServer", flags);
                Console.WriteLine("pinned=" + probe.Invoke(null, new object[] { args[3] }));
                Console.WriteLine("dead=" + probe.Invoke(null, new object[] { "https://127.0.0.1:1" }));
            }
            else
            {
                // 走启动器真实的后端启动路径：归一化必须发生在 StartServer 里第一次
                // 访问 EnvironmentVariables 之前，否则 .NET 在 UseShellExecute=false
                // 下复制环境块时抛 ArgumentException，后端进程根本创建不出来。
                type.GetField("consoleDetached", flags).SetValue(null, true);
                type.GetMethod("StartServer", flags).Invoke(
                    null, new object[] { args[2], args[3], args[4] });
                Console.WriteLine("proxy=" + Environment.GetEnvironmentVariable("https_proxy"));
                Console.WriteLine("kept=" + (Environment.GetEnvironmentVariable("PATH") != null));
            }
            return 0;
        }
        catch (Exception ex)
        {
            Exception baseEx = ex.GetBaseException();
            Console.Error.WriteLine(baseEx.GetType().FullName + ": " + baseEx.Message);
            return 1;
        }
    }
}
"""


def _compile_probe(output: Path, source: Path) -> None:
    subprocess.run(
        [
            str(build_launcher.find_csc()),
            "/nologo",
            "/optimize+",
            "/target:exe",
            "/platform:x64",
            "/r:System.dll",
            f"/out:{output}",
            str(source),
        ],
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


def _serve_config(body: bytes, tls: ssl.SSLContext | None = None) -> tuple[HTTPServer, int]:
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
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
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


@pytest.mark.skipif(os.name != "nt", reason="启动器只能在 Windows 上用 csc 编译")
def test_launcher_http_client_speaks_tls12_to_the_local_self_signed_server(tmp_path):
    """事故：csc 产物没有目标框架 app.config，.NET Framework 上 ServicePointManager
    默认只有 Ssl3/Tls，而后端最低接受 TLS 1.2。握手在证书回调执行之前就被服务端
    拒绝，窗口永远停在 Starting。InitLocalHttpClient 必须先显式启用 TLS 1.2，
    并按磁盘指纹记录信任自签证书。
    """
    exe = tmp_path / "DiceFrame.exe"
    _compile_launcher(exe)

    # 用生产证书管道生成证书和指纹文件，路径与格式与后端运行时写入的完全一致。
    data = tmp_path / "data"
    cert = SelfSignedCertificateProvider(CertificateStore(data)).prepare()
    (data / "config.json").write_text('{"tls_mode": "self_signed"}', encoding="utf-8")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # 与后端一致：拒绝 TLS 1.0/1.1，只有 1.2 起步的握手能成功。
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert.cert_path, cert.key_path)
    live = _serve_config(b'{"server_version": "3.2.0"}', tls=context)

    # 探针必须和真实启动器一样是裸 csc 产物：PowerShell 宿主的运行时默认
    # 已含 TLS 1.2，用它复现不了这次事故。
    probe_source = tmp_path / "probe.cs"
    probe_source.write_text(_PROBE_SOURCE, encoding="utf-8")
    probe_exe = tmp_path / "probe.exe"
    _compile_probe(probe_exe, probe_source)
    try:
        result = subprocess.run(
            [str(probe_exe), str(exe), "tls", str(data), f"https://127.0.0.1:{live[1]}"],
            check=True,
            capture_output=True,
            text=True,
            errors="replace",
        )
    finally:
        live[0].shutdown()

    payload = dict(line.split("=", 1) for line in result.stdout.splitlines())
    # 修复前：默认 Ssl3/Tls 连 TLS 1.2-only 服务都握手失败，pinned=False。
    assert payload["pinned"] == "True"
    assert payload["dead"] == "False"


@pytest.mark.skipif(os.name != "nt", reason="启动器只能在 Windows 上用 csc 编译")
def test_launcher_normalizes_duplicate_environment_variables_before_spawning_backend(tmp_path):
    """事故：Windows 环境块允许同时存在 HTTPS_PROXY / https_proxy，但 .NET
    Framework 在 UseShellExecute=false 下首次访问 ProcessStartInfo.
    EnvironmentVariables 时会把整块复制进大小写不敏感字典并抛
    ArgumentException，后端进程根本创建不出来。StartServer 必须先按首值
    归一化重复组，且不能动其它变量。
    """
    exe = tmp_path / "DiceFrame.exe"
    _compile_launcher(exe)

    probe_source = tmp_path / "probe.cs"
    probe_source.write_text(_PROBE_SOURCE, encoding="utf-8")
    probe_exe = tmp_path / "probe.exe"
    _compile_probe(probe_exe, probe_source)

    # 搭出 StartServer 要求的最小安装布局；"python" 用 where.exe 冒充：
    # 启动即退出，测试只关心它能不能被创建出来。
    install_root = tmp_path / "install"
    active_dir = install_root / "versions" / "v0.0.0-test"
    python_dir = active_dir / "python"
    app_dir = active_dir / "app"
    python_dir.mkdir(parents=True)
    app_dir.mkdir()
    shutil.copyfile(
        Path(os.environ["SystemRoot"]) / "System32" / "where.exe",
        python_dir / "python.exe",
    )
    (app_dir / "web_server.py").write_text("", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # 精确构造一组大小写重复；首值排在最前，结果可被稳定断言。
    env = {k: v for k, v in os.environ.items() if k.lower() != "https_proxy"}
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["https_proxy"] = "http://127.0.0.1:10"

    # check=True 顺带锁住事故本身：修复前 StartServer 在 EnvironmentVariables
    # 上抛 ArgumentException，探针退出码非零，后端进程创建不出来。
    result = subprocess.run(
        [str(probe_exe), str(exe), "env", str(install_root), str(active_dir), str(data_dir)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    payload = dict(line.split("=", 1) for line in result.stdout.splitlines())

    # 归一化按首值保留重复组；大小写无关的其它变量不受影响。
    assert payload["proxy"] == "http://127.0.0.1:9"
    assert payload["kept"] == "True"
