"""Build a clean DiceFrame release zip.

The package contains source code plus a prebuilt Vue frontend. Runtime data,
logs, local settings, tests, caches, and secrets are intentionally excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.template_catalog import is_user_template_file

try:
    from . import build_assistant_knowledge
except ImportError:  # Direct execution: python scripts/build_release.py
    import build_assistant_knowledge

DIST_DIR = ROOT / "dist"
BUILD_ROOT = DIST_DIR / "_release_build"
# v3 头像：6 规则 × 8 张 jpg（4 realistic + 4 anime）。旧 WebP 图集（12 张）已弃用。
EXPECTED_BUILTIN_AVATAR_ATLASES = 48
MAX_BUILTIN_AVATAR_COMPRESSED_BYTES = 8 * 1024 * 1024
BUILTIN_BACKGROUND_FILES = {
    "dark-fantasy-atmosphere.jpg",
    "campaign-mountain-city.jpg",
    "campaign-moonlit-ruins.jpg",
}

ROOT_FILES = [
    ".env.example",
    "Dockerfile",
    "LICENSE",
    "README.md",
    "README_EN.md",
    "docker-compose.yml",
    "requirements.txt",
    "requirements-portable.lock",
    "web_server.py",
    "web_ui.bat",
]

ROOT_DIRS = [
    "legal",
    "plugins",
    "prompts",
    "scripts",
    "src",
    "templates",
]

FRONTEND_FILES = [
    "index.html",
    "package-lock.json",
    "package.json",
    "tsconfig.app.json",
    "tsconfig.json",
    "vite.config.ts",
    "vitest.config.ts",
]

FRONTEND_DIRS = [
    "public",
    "src",
]

EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "data",
    "dist",
    "env",
    "node_modules",
    "playwright-report",
    "test-results",
    "tests",
    "venv",
}

EXCLUDED_SUFFIXES = {
    ".log",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".db",
    ".tsbuildinfo",
}

FORBIDDEN_ZIP_PATTERNS = [
    re.compile(r"(^|/)data/"),
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.(?!example$)"),
    re.compile(r"(^|/)\.git/"),
    re.compile(r"(^|/)\.codex/"),
    re.compile(r"(^|/)\.claude/"),
    re.compile(r"(^|/)node_modules/"),
    re.compile(r"(^|/)tests/"),
    re.compile(r"(^|/)frontend-v2/tests/"),
    re.compile(r"(^|/)frontend-v2/e2e/"),
    re.compile(r"(^|/)test-results/"),
    re.compile(r"(^|/)playwright-report/"),
    re.compile(r"\.(?:log|pyc|pyo|sqlite|db)$"),
]


def run(cmd: list[str], cwd: Path) -> None:
    print("> " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd), check=True)


def git_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return bool(result.stdout.strip())


def app_version() -> str:
    text = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("Cannot find __version__ in src/version.py")
    return match.group(1)


def is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDED_DIR_NAMES:
        return True
    if path.name.startswith(".env"):
        return True
    if path.name.startswith("ai_") or "_copy_" in path.name:
        return True
    return path.suffix in EXCLUDED_SUFFIXES


def copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    template_kind = "rules" if src == ROOT / "templates" / "rules" else "worlds" if src == ROOT / "templates" / "worlds" else ""
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if is_excluded(rel):
            continue
        if path.is_file() and template_kind and is_user_template_file(path, template_kind):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            copy_file(path, target)


CLOUDFLARED_VERSION = "2026.7.3"
CLOUDFLARED_SHA256 = {
    "cloudflared-windows-amd64.exe": "8635da433b6df8194746e88ed9d2589566c20e38bfc2a80e431a348b7c765841",
    "cloudflared-linux-amd64": "9d71c677db00134c1bd4144b7783486b654ad281b1ea62b4972098d19f770f17",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_cloudflared(package_dir: Path) -> None:
    """下载 cloudflared 二进制打进包内，让外网接入插件离线可用。

    当前 release.yml 仅 Windows 平台构建（ubuntu 跑 windows-zip、windows 跑
    portable），均不内置——source/portable 包里外网接入插件运行时自行下载。
    函数保留，供未来 Linux/Docker 构建复用（按构建平台选资产：
    windows-amd64 / linux-amd64）。失败不阻断打包（插件会运行时下载）。
    """
    target_dir = package_dir / "cloudflared"
    target_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        asset = "cloudflared-windows-amd64.exe"
        target = target_dir / "cloudflared.exe"
    else:
        asset = "cloudflared-linux-amd64"
        target = target_dir / "cloudflared"
    url = f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/{asset}"
    temporary = target.with_suffix(target.suffix + ".download")
    try:
        print(f"Downloading cloudflared {CLOUDFLARED_VERSION} ...")
        urllib.request.urlretrieve(url, temporary)
        expected = CLOUDFLARED_SHA256[asset]
        actual = _sha256_file(temporary)
        if actual.lower() != expected.lower():
            raise RuntimeError(f"cloudflared sha256 mismatch: expected {expected}, got {actual}")
        temporary.replace(target)
        if os.name != "nt":
            target.chmod(0o755)
        print(f"cloudflared saved to {target}")
    except Exception as exc:  # noqa: BLE001
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        print(f"Warning: cloudflared download failed, plugin will fetch at runtime: {exc}")


def prepare_package_tree(package_dir: Path, *, include_cloudflared: bool = False) -> None:
    """组装发布包目录树。

    include_cloudflared 默认 False：source 与 portable 包都不内置 cloudflared
    二进制，外网接入插件运行时自行下载（插件 v0.2.0 起带 sha256 校验）。
    参数保留，供未来 Linux/Docker 构建复用（内置 linux-amd64）。
    """
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    for rel in ROOT_FILES:
        copy_file(ROOT / rel, package_dir / rel)
    for rel in ROOT_DIRS:
        copy_tree(ROOT / rel, package_dir / rel)

    frontend_dir = package_dir / "frontend-v2"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    for rel in FRONTEND_FILES:
        copy_file(ROOT / "frontend-v2" / rel, frontend_dir / rel)
    for rel in FRONTEND_DIRS:
        copy_tree(ROOT / "frontend-v2" / rel, frontend_dir / rel)

    build_assistant_knowledge.build(package_dir / "src" / "webui" / "assistant_knowledge_index.json")

    if include_cloudflared:
        download_cloudflared(package_dir)


def build_frontend(package_dir: Path) -> None:
    frontend_dir = package_dir / "frontend-v2"
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("npm not found. Install Node.js 20.19+ or 22.12+ before building a release.")
    run([npm, "ci"], frontend_dir)
    run([npm, "run", "build"], frontend_dir)
    shutil.rmtree(frontend_dir / "node_modules", ignore_errors=True)
    for tsbuild in frontend_dir.glob("*.tsbuildinfo"):
        tsbuild.unlink(missing_ok=True)


def make_zip(package_dir: Path, output_zip: Path) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        try:
            output_zip.unlink()
        except PermissionError:
            output_zip = available_zip_name(output_zip)
    shutil.make_archive(str(output_zip.with_suffix("")), "zip", root_dir=package_dir.parent, base_dir=package_dir.name)
    validate_zip(output_zip)


def available_zip_name(path: Path) -> Path:
    stem = path.stem
    suffix = path.suffix
    for idx in range(1, 100):
        candidate = path.parent / f"{stem}-{idx}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Cannot find an available output name near {path}")


def validate_zip(output_zip: Path) -> None:
    with zipfile.ZipFile(output_zip) as zf:
        names = zf.namelist()
        infos = zf.infolist()
    bad = [name for name in names if any(pattern.search(name.replace("\\", "/")) for pattern in FORBIDDEN_ZIP_PATTERNS)]
    if bad:
        preview = "\n".join(bad[:20])
        raise RuntimeError(f"Release zip contains forbidden paths:\n{preview}")
    if not any(name.endswith("/static-v2/index.html") for name in names):
        raise RuntimeError("Release zip is missing static-v2/index.html")
    if not any("/static-v2/assets/" in name and name.endswith(".js") for name in names):
        raise RuntimeError("Release zip is missing built frontend assets")
    validate_avatar_payload(infos, require_source=True)
    validate_background_payload(infos, require_source=True)


def validate_avatar_payload(infos: list[zipfile.ZipInfo], *, require_source: bool) -> None:
    normalized = [(info, info.filename.replace("\\", "/")) for info in infos]
    built = [info for info, name in normalized if "/static-v2/avatars/v3/" in name and name.endswith(".jpg")]
    source = [info for info, name in normalized if "/frontend-v2/public/avatars/v3/" in name and name.endswith(".jpg")]
    legacy_png = [name for _, name in normalized if "/avatars/" in name and name.lower().endswith(".png")]
    if legacy_png:
        raise RuntimeError("Release zip contains legacy PNG portrait atlases")
    if len(built) != EXPECTED_BUILTIN_AVATAR_ATLASES:
        raise RuntimeError(
            f"Release zip must contain {EXPECTED_BUILTIN_AVATAR_ATLASES} built v3 portrait images, got {len(built)}"
        )
    if require_source and len(source) != EXPECTED_BUILTIN_AVATAR_ATLASES:
        raise RuntimeError(
            f"Source release must contain {EXPECTED_BUILTIN_AVATAR_ATLASES} source v3 portrait images, got {len(source)}"
        )
    compressed_size = sum(info.compress_size for info in built + source)
    if compressed_size > MAX_BUILTIN_AVATAR_COMPRESSED_BYTES:
        raise RuntimeError(
            f"Built-in portrait payload is too large: {compressed_size} bytes; optimize assets before publishing"
        )


def validate_background_payload(infos: list[zipfile.ZipInfo], *, require_source: bool) -> None:
    normalized = [info.filename.replace("\\", "/") for info in infos]
    built = {
        name.rsplit("/", 1)[-1]
        for name in normalized
        if "/static-v2/ui/" in name
    }
    missing_built = BUILTIN_BACKGROUND_FILES - built
    if missing_built:
        raise RuntimeError(
            "Release zip is missing built-in UI backgrounds: " + ", ".join(sorted(missing_built))
        )
    if require_source:
        source = {
            name.rsplit("/", 1)[-1]
            for name in normalized
            if "/frontend-v2/public/ui/" in name
        }
        missing_source = BUILTIN_BACKGROUND_FILES - source
        if missing_source:
            raise RuntimeError(
                "Source release is missing built-in UI backgrounds: " + ", ".join(sorted(missing_source))
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a clean DiceFrame release zip.")
    parser.add_argument("--version", default=app_version(), help="Release version, default: src/version.py")
    parser.add_argument("--output-dir", type=Path, default=DIST_DIR, help="Directory for the generated zip")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow packaging with uncommitted git changes")
    parser.add_argument("--skip-build", action="store_true", help="Copy files without running npm build")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if git_dirty() and not args.allow_dirty:
        print(
            "Working tree has uncommitted changes. Commit first, or rerun with --allow-dirty for a local test package.",
            file=sys.stderr,
        )
        return 2

    version = args.version.lstrip("v")
    package_name = f"DiceFrame-v{version}-windows"
    package_dir = BUILD_ROOT / package_name
    output_zip = args.output_dir.resolve() / f"{package_name}.zip"

    if BUILD_ROOT.exists():
        shutil.rmtree(BUILD_ROOT)
    prepare_package_tree(package_dir)
    if not args.skip_build:
        build_frontend(package_dir)
    make_zip(package_dir, output_zip)
    print(f"\nRelease package created: {output_zip}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)
