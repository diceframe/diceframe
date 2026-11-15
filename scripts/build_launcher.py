"""Build the Windows DiceFrame launcher executable."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import math
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "src" / "launcher"
# 启动器是一个 partial class，按职责拆在 src/launcher/*.cs 里，一起编译。
SOURCE = SOURCE_DIR / "DiceFrameLauncher.cs"
ICON_SOURCE = ROOT / "frontend-v2" / "public" / "favicon.svg"
CANDIDATE_CSC = [
    Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
]
# 应用窗口用的 WebView2 SDK（BSD-3-Clause）。固定版本与 SHA-256；编译时引用，打包时随载荷
# 放进 app/launcher/webview2/（见 AppWindow.cs 与 build_portable.stage_webview2）。
WEBVIEW2_SDK_VERSION = "1.0.4314.35"
WEBVIEW2_SDK_SHA256 = "90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4"
WEBVIEW2_SDK_URL = (
    "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/"
    f"{WEBVIEW2_SDK_VERSION}/microsoft.web.webview2.{WEBVIEW2_SDK_VERSION}.nupkg"
)
WEBVIEW2_CACHE_DIR = ROOT / "dist" / "_launcher" / "webview2"
# nupkg 内路径 → 解出的文件名。启动器只用 WinForms 控件（net462）与 x64 原生加载器。
WEBVIEW2_SDK_FILES = {
    "lib/net462/Microsoft.Web.WebView2.Core.dll": "Microsoft.Web.WebView2.Core.dll",
    "lib/net462/Microsoft.Web.WebView2.WinForms.dll": "Microsoft.Web.WebView2.WinForms.dll",
    "runtimes/win-x64/native/WebView2Loader.dll": "WebView2Loader.dll",
    "LICENSE.txt": "LICENSE.txt",
}


def find_csc() -> Path:
    for candidate in CANDIDATE_CSC:
        if candidate.exists():
            return candidate
    found = shutil.which("csc")
    if found:
        return Path(found)
    raise RuntimeError("Cannot find csc.exe. Build the portable launcher on Windows with .NET Framework installed.")


def run(cmd: list[str], cwd: Path) -> None:
    print("> " + " ".join(str(part) for part in cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd), check=True)


def blend(dst: tuple[int, int, int, int], src: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    sr, sg, sb, sa = src
    dr, dg, db, da = dst
    alpha = sa / 255.0
    inv = 1.0 - alpha
    return (
        int(sr * alpha + dr * inv),
        int(sg * alpha + dg * inv),
        int(sb * alpha + db * inv),
        min(255, int(sa + da * inv)),
    )


class Canvas:
    def __init__(self, size: int) -> None:
        self.size = size
        self.scale = size / 48.0
        self.pixels = [(0, 0, 0, 0)] * (size * size)

    def set_pixel(self, x: int, y: int, color: tuple[int, int, int, int]) -> None:
        if 0 <= x < self.size and 0 <= y < self.size:
            idx = y * self.size + x
            self.pixels[idx] = blend(self.pixels[idx], color)

    def rect(self, x: float, y: float, w: float, h: float, color: tuple[int, int, int, int]) -> None:
        x0, y0 = int(x * self.scale), int(y * self.scale)
        x1, y1 = int((x + w) * self.scale), int((y + h) * self.scale)
        for py in range(y0, y1):
            for px in range(x0, x1):
                self.set_pixel(px, py, color)

    def rounded_rect(self, x: float, y: float, w: float, h: float, radius: float, color: tuple[int, int, int, int]) -> None:
        x0, y0 = x * self.scale, y * self.scale
        x1, y1 = (x + w) * self.scale, (y + h) * self.scale
        r = radius * self.scale
        for py in range(math.floor(y0), math.ceil(y1)):
            for px in range(math.floor(x0), math.ceil(x1)):
                cx = min(max(px + 0.5, x0 + r), x1 - r)
                cy = min(max(py + 0.5, y0 + r), y1 - r)
                if (px + 0.5 - cx) ** 2 + (py + 0.5 - cy) ** 2 <= r * r + 0.01:
                    self.set_pixel(px, py, color)

    def stroke_rounded_rect(self, x: float, y: float, w: float, h: float, radius: float, stroke: float, color: tuple[int, int, int, int], fill: tuple[int, int, int, int]) -> None:
        self.rounded_rect(x, y, w, h, radius, color)
        self.rounded_rect(x + stroke, y + stroke, w - stroke * 2, h - stroke * 2, max(radius - stroke, 0), fill)

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float, color: tuple[int, int, int, int]) -> None:
        x1 *= self.scale
        y1 *= self.scale
        x2 *= self.scale
        y2 *= self.scale
        radius = max(width * self.scale / 2.0, 0.5)
        min_x = math.floor(min(x1, x2) - radius)
        max_x = math.ceil(max(x1, x2) + radius)
        min_y = math.floor(min(y1, y2) - radius)
        max_y = math.ceil(max(y1, y2) + radius)
        dx = x2 - x1
        dy = y2 - y1
        length_sq = dx * dx + dy * dy
        for py in range(min_y, max_y + 1):
            for px in range(min_x, max_x + 1):
                if length_sq == 0:
                    t = 0.0
                else:
                    t = ((px + 0.5 - x1) * dx + (py + 0.5 - y1) * dy) / length_sq
                    t = min(1.0, max(0.0, t))
                cx = x1 + t * dx
                cy = y1 + t * dy
                if (px + 0.5 - cx) ** 2 + (py + 0.5 - cy) ** 2 <= radius * radius:
                    self.set_pixel(px, py, color)

    def circle(self, cx: float, cy: float, r: float, color: tuple[int, int, int, int]) -> None:
        cx *= self.scale
        cy *= self.scale
        r *= self.scale
        for py in range(math.floor(cy - r), math.ceil(cy + r) + 1):
            for px in range(math.floor(cx - r), math.ceil(cx + r) + 1):
                if (px + 0.5 - cx) ** 2 + (py + 0.5 - cy) ** 2 <= r * r:
                    self.set_pixel(px, py, color)

    def png_bytes(self) -> bytes:
        rows = []
        for y in range(self.size):
            start = y * self.size
            row = bytearray([0])
            for r, g, b, a in self.pixels[start : start + self.size]:
                row.extend([r, g, b, a])
            rows.append(bytes(row))
        raw = b"".join(rows)
        return make_png(self.size, self.size, raw)


def chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def make_png(width: int, height: int, raw: bytes) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def render_icon(size: int) -> bytes:
    dark = (18, 15, 12, 255)
    panel = (37, 27, 19, 255)
    gold = (216, 173, 82, 255)
    canvas = Canvas(size)
    canvas.rounded_rect(0, 0, 48, 48, 9, dark)
    canvas.stroke_rounded_rect(6, 6, 36, 36, 6, 1.6, gold, dark)
    canvas.line(6, 13, 6, 6, 1.6, gold)
    canvas.line(6, 6, 13, 6, 1.6, gold)
    canvas.line(35, 6, 42, 6, 1.6, gold)
    canvas.line(42, 6, 42, 13, 1.6, gold)
    canvas.line(42, 35, 42, 42, 1.6, gold)
    canvas.line(42, 42, 35, 42, 1.6, gold)
    canvas.line(13, 42, 6, 42, 1.6, gold)
    canvas.line(6, 42, 6, 35, 1.6, gold)
    canvas.stroke_rounded_rect(15, 15, 18, 18, 3.5, 1.4, gold, panel)
    for cx, cy in ((20, 20), (28, 20), (24, 24), (20, 28), (28, 28)):
        canvas.circle(cx, cy, 1.6, gold)
    return canvas.png_bytes()


def write_ico(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    images = [(size, render_icon(size)) for size in (16, 24, 32, 48, 64, 128, 256)]
    offset = 6 + len(images) * 16
    entries = []
    payloads = []
    for size, payload in images:
        width = 0 if size == 256 else size
        entries.append(struct.pack("<BBBBHHII", width, width, 0, 0, 1, 32, len(payload), offset))
        payloads.append(payload)
        offset += len(payload)
    target.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(payloads))


def launcher_sources() -> list[str]:
    return [str(path) for path in sorted(SOURCE_DIR.glob("*.cs"))]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_webview2_sdk(cache_dir: Path = WEBVIEW2_CACHE_DIR) -> Path:
    """Download (once) and unpack the pinned WebView2 SDK; return the directory of its files."""
    sdk_dir = cache_dir / WEBVIEW2_SDK_VERSION
    if all((sdk_dir / name).is_file() for name in WEBVIEW2_SDK_FILES.values()):
        return sdk_dir
    package = cache_dir / f"microsoft.web.webview2.{WEBVIEW2_SDK_VERSION}.nupkg"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not package.is_file():
        print(f"Downloading {WEBVIEW2_SDK_URL}", flush=True)
        temporary = package.with_suffix(".download")
        try:
            urllib.request.urlretrieve(WEBVIEW2_SDK_URL, temporary)
            temporary.replace(package)
        finally:
            temporary.unlink(missing_ok=True)
    actual = _sha256(package)
    if not hmac.compare_digest(actual, WEBVIEW2_SDK_SHA256):
        package.unlink(missing_ok=True)
        raise RuntimeError(
            f"WebView2 SDK SHA-256 mismatch: expected {WEBVIEW2_SDK_SHA256}, got {actual}"
        )
    sdk_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package) as nupkg:
        for member, name in WEBVIEW2_SDK_FILES.items():
            (sdk_dir / name).write_bytes(nupkg.read(member))
    return sdk_dir


def csc_command(
    output: Path,
    sdk_dir: Path,
    icon: Path | None = None,
    *,
    defines: tuple[str, ...] = (),
) -> list[str]:
    command = [
        str(find_csc()),
        "/nologo",
        "/optimize+",
        "/target:exe",
        "/platform:x64",
        # WebRequestHandler.ServerCertificateValidationCallback 需要显式引用。
        "/r:System.Net.Http.dll",
        "/r:System.Net.Http.WebRequest.dll",
        # 应用窗口（AppWindow.cs）：WinForms 宿主 + WebView2 控件。
        "/r:System.Windows.Forms.dll",
        "/r:System.Drawing.dll",
        f"/r:{sdk_dir / 'Microsoft.Web.WebView2.Core.dll'}",
        f"/r:{sdk_dir / 'Microsoft.Web.WebView2.WinForms.dll'}",
    ]
    if icon is not None:
        command.append(f"/win32icon:{icon}")
    if defines:
        # 只有测试会传（例如 LAUNCHER_TESTING）；发布构建永远不带。
        command.append("/define:" + ";".join(defines))
    command.append(f"/out:{output}")
    command.extend(launcher_sources())
    return command


def build_launcher(output: Path, icon: Path) -> None:
    if not SOURCE.exists():
        raise RuntimeError(f"Cannot find launcher source: {SOURCE}")
    if not ICON_SOURCE.exists():
        raise RuntimeError(f"Cannot find icon source: {ICON_SOURCE}")
    write_ico(icon)
    output.parent.mkdir(parents=True, exist_ok=True)
    run(csc_command(output, ensure_webview2_sdk(), icon), ROOT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build DiceFrame.exe launcher.")
    parser.add_argument("--output", type=Path, required=True, help="Path to generated DiceFrame.exe")
    parser.add_argument("--icon", type=Path, default=ROOT / "dist" / "_launcher" / "DiceFrame.ico", help="Generated .ico path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_launcher(args.output.resolve(), args.icon.resolve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
