"""轻量运行时依赖检查。

启动脚本调用本文件；只有缺少依赖时才执行 pip install，已满足时几乎不耗时。
"""

from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
EMBEDDING_REQUIREMENTS = ROOT / "requirements-embedding.txt"

# requirements 包名 -> import 名。Pillow 的 import 名是 PIL。
RUNTIME_IMPORTS = {
    "aiohttp": "aiohttp",
    "Pillow": "PIL",
    "peewee": "peewee",
    "pydantic-ai-slim": "pydantic_ai",
}
EMBEDDING_IMPORTS = ("numpy", "onnxruntime", "tokenizers")


def missing_imports() -> list[str]:
    missing: list[str] = []
    for package, import_name in RUNTIME_IMPORTS.items():
        if importlib.util.find_spec(import_name) is None:
            missing.append(package)
    return missing


def _ensure_base_dependencies() -> int:
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


def _supports_embedding_wheels() -> bool:
    if sys.implementation.name != "cpython" or not (3, 11) <= sys.version_info[:2] <= (3, 14):
        return False
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return machine in {"amd64", "x86_64"}
    if sys.platform == "darwin":
        major = platform.mac_ver()[0].partition(".")[0]
        return machine == "arm64" and major.isdigit() and int(major) >= 14
    if sys.platform == "linux" and machine in {"x86_64", "aarch64"}:
        name, release = platform.libc_ver()
        try:
            version = tuple(int(part) for part in release.split(".")[:2])
        except ValueError:
            return False
        return name == "glibc" and version >= (2, 28)
    return False


def main() -> int:
    result = _ensure_base_dependencies()
    if result != 0:
        return result
    if (sys.prefix != sys.base_prefix and _supports_embedding_wheels()
            and any(importlib.util.find_spec(name) is None for name in EMBEDDING_IMPORTS)):
        print("正在为当前虚拟环境安装本地向量推理组件。")
        completed = subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--only-binary=:all:",
                "--timeout", "10", "--retries", "1", "-r", str(EMBEDDING_REQUIREMENTS),
            ],
            cwd=ROOT,
        )
        if completed.returncode != 0:
            print("本地向量推理组件安装失败。DiceFrame 将继续启动，可配置外部 Embedding 服务商。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
