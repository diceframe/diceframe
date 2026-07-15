"""轻量运行时依赖检查。

启动脚本调用本文件；只有缺少依赖时才执行 pip install，已满足时几乎不耗时。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"

# requirements 包名 -> import 名。Pillow 的 import 名是 PIL。
RUNTIME_IMPORTS = {
    "aiohttp": "aiohttp",
    "Pillow": "PIL",
}


def missing_imports() -> list[str]:
    missing: list[str] = []
    for package, import_name in RUNTIME_IMPORTS.items():
        if importlib.util.find_spec(import_name) is None:
            missing.append(package)
    return missing


def main() -> int:
    missing = missing_imports()
    if not missing:
        return 0
    if not REQUIREMENTS.exists():
        print(f"缺少运行依赖 {', '.join(missing)}，且找不到 {REQUIREMENTS}", file=sys.stderr)
        return 1
    print("检测到缺少运行依赖：" + "、".join(missing))
    print("正在执行：python -m pip install -r requirements.txt")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        cwd=str(ROOT),
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
