"""巡检仍未声明错误码的接口错误（ADR-0015 §4）。

后端错误码必须在抛出点声明；用 ``"error": t("webui.services.x.y")`` 直接返回
文案的调用点在请求语言不是中文时既丢失 code，又只能回退英文，社区译文
（``apiErrors.<code>``）永远命中不了。

这不是一次清账，而是 ratchet：基线记录每个文件当前的未声明数量，只允许减少。
迁移一处就把该文件基线调低（``--write-baseline`` 会按当前代码重算）。

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


def _is_t_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "t"
    )


def undeclared_sites(path: Path) -> list[int]:
    """行号：``{"error": t(...)}`` 顶层返回，且没有 error_code 兄弟键。"""

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
        error_value = values[keys.index("error")]
        if _is_t_call(error_value) or (
            isinstance(error_value, ast.IfExp)
            and (_is_t_call(error_value.body) or _is_t_call(error_value.orelse))
        ):
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