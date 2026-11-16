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
    "peewee": "peewee",
    "pydantic-ai-slim": "pydantic_ai",
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
    print("目标解释器：" + sys.executable)
    if sys.prefix == sys.base_prefix:
        # 系统解释器上装依赖是个坑：版本可能没有对应 wheel，pip 会去编译，
        # 第一次启动就变成十几分钟。先提醒，再照装（不能挡住首次体验）。
        print(
            "当前不在虚拟环境里，这些包会装进全局 Python。建议先执行 "
            "`py -3 -m venv .venv`，web_ui.bat 会优先使用 .venv 或 .venv314。",
        )
    print("正在执行：python -m pip install --prefer-binary -r requirements.txt")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--prefer-binary", "-r", str(REQUIREMENTS)],
        cwd=str(ROOT),
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
