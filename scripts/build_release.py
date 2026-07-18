"""Build a clean DiceFrame release zip.

The package contains source code plus a prebuilt Vue frontend. Runtime data,
logs, local settings, tests, caches, and secrets are intentionally excluded.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.template_catalog import is_user_template_file

DIST_DIR = ROOT / "dist"
BUILD_ROOT = DIST_DIR / "_release_build"

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
    "docs/assets",
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

DOC_FILES = [
    "docs/BOT_BRIDGE_CORE_CN.md",
    "docs/BOT_BRIDGE_CORE_EN.md",
    "docs/DOCKER_DEPLOY_CN.md",
    "docs/DOCKER_DEPLOY_EN.md",
    "docs/PLUGIN_DEVELOPMENT_CN.md",
    "docs/PLUGIN_DEVELOPMENT_EN.md",
    "docs/PLUGIN_REGISTRY_CN.md",
    "docs/PLUGIN_REGISTRY_EN.md",
    "docs/USER_GUIDE_CN.md",
    "docs/USER_GUIDE_EN.md",
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


def prepare_package_tree(package_dir: Path) -> None:
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    for rel in ROOT_FILES + DOC_FILES:
        copy_file(ROOT / rel, package_dir / rel)
    for rel in ROOT_DIRS:
        copy_tree(ROOT / rel, package_dir / rel)

    frontend_dir = package_dir / "frontend-v2"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    for rel in FRONTEND_FILES:
        copy_file(ROOT / "frontend-v2" / rel, frontend_dir / rel)
    for rel in FRONTEND_DIRS:
        copy_tree(ROOT / "frontend-v2" / rel, frontend_dir / rel)


def build_frontend(package_dir: Path) -> None:
    frontend_dir = package_dir / "frontend-v2"
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("npm not found. Install Node.js 20+ before building a release.")
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
    bad = [name for name in names if any(pattern.search(name.replace("\\", "/")) for pattern in FORBIDDEN_ZIP_PATTERNS)]
    if bad:
        preview = "\n".join(bad[:20])
        raise RuntimeError(f"Release zip contains forbidden paths:\n{preview}")
    if not any(name.endswith("/static-v2/index.html") for name in names):
        raise RuntimeError("Release zip is missing static-v2/index.html")
    if not any("/static-v2/assets/" in name and name.endswith(".js") for name in names):
        raise RuntimeError("Release zip is missing built frontend assets")


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
