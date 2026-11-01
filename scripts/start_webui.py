"""Windows-friendly WebUI launcher.

Keep web_ui.bat small and put checks here, where quoting and control flow are
less fragile than in cmd.exe batch syntax.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
import re

import ensure_runtime_deps


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend-v2"
STATIC_DIR = ROOT / "static-v2"
ASSET_REF_RE = re.compile(r"""(?:src|href)=["'](/v2-assets/[^"']+)["']""")


def _frontend_source_mtime() -> float:
    candidates = [
        FRONTEND_DIR / "package.json",
        FRONTEND_DIR / "package-lock.json",
        FRONTEND_DIR / "vite.config.ts",
        FRONTEND_DIR / "tsconfig.json",
    ]
    candidates.extend(
        path for root_name in ("src", "public")
        for path in (FRONTEND_DIR / root_name).rglob("*")
        if path.is_file()
    )
    return max((path.stat().st_mtime for path in candidates if path.is_file()), default=0.0)


def frontend_built() -> bool:
    assets_dir = STATIC_DIR / "assets"
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists() or not assets_dir.is_dir() or not any(assets_dir.glob("*.js")):
        return False
    html = index_path.read_text(encoding="utf-8", errors="ignore")
    for ref in ASSET_REF_RE.findall(html):
        relative = ref.removeprefix("/v2-assets/")
        if not (STATIC_DIR / relative).exists():
            return False
    if _frontend_source_mtime() > index_path.stat().st_mtime:
        return False
    return True


def run(cmd: list[str], cwd: Path) -> int:
    print("> " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(cwd)).returncode


def frontend_dependencies_ready() -> bool:
    """Return whether the local frontend install contains the build toolchain."""
    bin_dir = FRONTEND_DIR / "node_modules" / ".bin"
    return all(
        any((bin_dir / name).is_file() for name in (tool, f"{tool}.cmd", f"{tool}.ps1"))
        for tool in ("vue-tsc", "vite")
    )


def ensure_frontend() -> int:
    if frontend_built():
        return 0
    print("\n未检测到前端构建产物，正在构建 frontend-v2...", flush=True)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        print(
            "未找到 npm。请先安装 Node.js 20.19+ 或 22.12+，然后重新运行 web_ui.bat。",
            file=sys.stderr,
            flush=True,
        )
        return 1
    if not frontend_dependencies_ready():
        code = run([npm, "ci", "--include=dev"], FRONTEND_DIR)
        if code:
            return code
    return run([npm, "run", "build"], FRONTEND_DIR)


def main() -> int:
    code = ensure_runtime_deps.main()
    if code:
        return code
    code = ensure_frontend()
    if code:
        return code
    return subprocess.run([sys.executable, str(ROOT / "web_server.py")], cwd=str(ROOT)).returncode


if __name__ == "__main__":
    raise SystemExit(main())
