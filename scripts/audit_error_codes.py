"""巡检仍未声明错误码的接口错误（ADR-0015 §4）。

后端错误码必须在抛出点声明。只把文案直接当结果返回的调用点（``"error": t("webui.services.x.y")``
或裸字面量）没有 code，前端 `apiErrors.<code>` 永远命中不了，社区译文无法接手。

这不是一次清账，而是 ratchet：基线记录每个文件当前的未声明数量，只允许减少。
迁移一处就把该文件基线调低（``--write-baseline`` 会按当前代码重算）。

量的是「抛出点有没有声明 code」，不是「响应是否已经坏了」：响应出口会为冻结表里的
存量文案补码，也会把旧 ``code`` 键提升为 ``error_code``，所以每一条都是尚未按新契约写的
调用点，而不是一个用户可见的缺陷。

用法::

    python scripts/audit_error_codes.py            # 对照基线检查
    python scripts/audit_error_codes.py --write-baseline
"""

from __future__ import annotations

import ast
import json
import re
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "scripts" / "error_code_baseline.json"
SCAN_DIRS = ("src/webui/services", "src/webui/routes")
SCAN_FILES = ("src/webui/api.py",)
#: 只巡接口边界：这些载荷直接进响应体。引擎内部文案由 messages 契约管；
#: 需要 code 的引擎结果由 webui 层包成 error_payload。
_DIRECT_ERROR = re.compile(r'["\']error["\']\s*:\s*')


def undeclared_sites(path: Path) -> list[int]:
    """行号：作为响应体的载荷里有 ``error`` 却没有声明 ``error_code``。

    “响应体”包括：直接作为 ``return`` 的字典，以及 ``web.json_response(<dict>)``
    的第一个参数。其他位置的字典（批量报告里的逐条目明细等）不计。
    """

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)  # 被扫描文件自身的转义告警
            tree = ast.parse(path.read_bytes().decode("utf-8-sig"))
    except SyntaxError:  # 扫描失败不能静默当成 0
        raise
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        values = node.values
        if "error" not in keys or "error_code" in keys:
            continue
        # 计数口径是「载荷有没有 error_code」，不是文案怎么写：`t()` 渲染的、
        # 裸字面量的、以及只写了 `code` 的，前端都拿不到 code。
        found.append(node.lineno)
    return sorted(set(found))


def scan() -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    paths: list[Path] = []
    for dirname in SCAN_DIRS:
        base = ROOT / dirname
        if base.exists():
            paths.extend(sorted(base.rglob("*.py")))
    paths.extend(path for path in (ROOT / name for name in SCAN_FILES) if path.exists())
    for path in paths:
        sites = undeclared_sites(path)
        if sites:
            result[path.relative_to(ROOT).as_posix()] = sites
    return result


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in data.get("files", {}).items()}


def main(argv: list[str]) -> int:
    current = {name: len(sites) for name, sites in scan().items()}
    if "--write-baseline" in argv:
        BASELINE_PATH.write_text(
            json.dumps(
                {
                    "note": "ADR-0015 §4 ratchet: undeclared interface errors per file. Only decrease.",
                    "files": dict(sorted(current.items())),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {BASELINE_PATH.relative_to(ROOT)}: {len(current)} files, {sum(current.values())} sites")
        return 0

    baseline = load_baseline()
    regressions = {
        name: (baseline.get(name, 0), count)
        for name, count in current.items()
        if count > baseline.get(name, 0)
    }
    total_now = sum(current.values())
    total_then = sum(baseline.values())
    print("Undeclared interface errors (error_code missing at raise site):")
    print(f"  now={total_now} baseline={total_then} remaining files={len(current)}")
    if regressions:
        for name, (was, now) in sorted(regressions.items()):
            print(f"  - {name}: {was} -> {now} (new undeclared `\"error\": t(...)`)")
        print("Use error_payload() from src/webui/errors.py instead.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))