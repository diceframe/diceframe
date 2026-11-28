"""巡检后端用户可见文案的本地化缺口（ADR-0015 §8）。

这个脚本不做一次清账，只做两件事：

1. **机制目录逐文件 ratchet**。`src/engine/` 与 `src/rulesets/` 是机械代码，
   其中的中日文字面量要么是待迁文案、要么是机制表（如 `constants.py` 的关键词
   表）；两者都不许增长，出现新的机械文件也直接失败。
2. **全仓 CJK 字面量按包 ratchet**。`scripts/localization_baseline.json` 记录
   当前数量，只允许减少；完成一处迁移就把基线调低。

用法::

    python scripts/audit_localization.py
    python scripts/audit_localization.py --write-baseline
"""

from __future__ import annotations

import ast
import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "scripts" / "localization_baseline.json"
#: 机械目录：这里的 CJK 字面量是本地化回归，不是待翻译文案。
MECHANICS_DIRS = ("src/engine", "src/rulesets")
#: 机械目录里允许出现 CJK 的地方，全部是已登记的语言资源或兼容边界。
_ALLOWED_MECHANICS_PATHS = (
    "src/engine/check_verdict.py",
    "src/engine/intent/locales/",
    "src/engine/language.py",
)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Values that are docstrings, not runtime literals."""

    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def cjk_literals(path: Path) -> list[int]:
    """Line numbers of runtime string literals containing CJK, docstrings excluded."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # 被扫描文件自身的转义告警
        tree = ast.parse(path.read_bytes().decode("utf-8-sig"))
    skip = _docstring_ids(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip or not _has_cjk(node.value):
            continue
        lines.append(node.lineno)
    return sorted(set(lines))


def _package(rel: str) -> str:
    parts = rel.split("/")
    return "/".join(parts[:3]) if len(parts) > 3 else rel.rsplit("/", 1)[0]


def scan() -> tuple[dict[str, int], dict[str, int]]:
    counts: dict[str, int] = {}
    mechanics: dict[str, int] = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        count = len(cjk_literals(path))
        if not count:
            continue
        counts[_package(rel)] = counts.get(_package(rel), 0) + count
        if rel.startswith(MECHANICS_DIRS) and not rel.startswith(_ALLOWED_MECHANICS_PATHS):
            mechanics[rel] = count
    return counts, mechanics


def load_baseline() -> dict[str, dict[str, int]]:
    if not BASELINE_PATH.exists():
        return {"packages": {}, "mechanics": {}}
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return {
        "packages": {str(k): int(v) for k, v in data.get("packages", {}).items()},
        "mechanics": {str(k): int(v) for k, v in data.get("mechanics", {}).items()},
    }


def main(argv: list[str]) -> int:
    counts, mechanics = scan()
    total = sum(counts.values())
    if "--write-baseline" in argv:
        BASELINE_PATH.write_text(
            json.dumps(
                {
                    "note": "ADR-0015 §8 ratchet: runtime CJK string literals. Only decrease.",
                    "packages": dict(sorted(counts.items())),
                    "mechanics": dict(sorted(mechanics.items())),
                    "total": total,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"wrote {BASELINE_PATH.relative_to(ROOT)}: {len(counts)} packages, "
            f"{len(mechanics)} mechanics files, {total} literals"
        )
        return 0

    baseline = load_baseline()
    problems = 0
    grown_mechanics = {
        name: (baseline["mechanics"].get(name, 0), count)
        for name, count in mechanics.items()
        if count > baseline["mechanics"].get(name, 0)
    }
    grown = {
        name: (baseline["packages"].get(name, 0), count)
        for name, count in counts.items()
        if count > baseline["packages"].get(name, 0)
    }
    print("Backend localisation audit:")
    print(
        f"  runtime CJK literals: now={total} "
        f"baseline={sum(baseline['packages'].values())}"
    )
    for name, (was, now) in sorted(grown_mechanics.items()):
        print(f"  - {name}: {was} -> {now} [mechanics]")
        problems += 1
    for name, (was, now) in sorted(grown.items()):
        print(f"  - {name}: {was} -> {now}")
        problems += 1
    if problems:
        print("Move user-facing text to src/localization/locales/ (or intent/locales/) instead.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))