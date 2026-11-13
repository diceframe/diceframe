"""Validate the DiceFrame ecosystem and implementation plan documents.

This is deliberately a structural/coverage gate. It does not decide whether a
future architecture is wise and it never treats a plan phrase as proof that
runtime code exists. The command is useful in CI and accepts the four
authoritative document paths explicitly so it does not depend on a developer's
Downloads directory.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _check_fences(text: str) -> bool:
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if len(stripped) < 3 or stripped[0] not in (chr(96), "~"):
            continue
        count = 0
        while count < len(stripped) and stripped[count] == stripped[0]:
            count += 1
        if count < 3:
            continue
        if fence is None:
            fence = (stripped[0], count)
        elif stripped[0] == fence[0] and count >= fence[1]:
            fence = None
    return fence is None


def _task_cards(implementation: str) -> dict[str, list[str]]:
    cards: dict[str, list[str]] = {}
    for key, body in re.findall(
        r"^#### ([A-Z]\d{2}) — [^\n]+\n(.*?)(?=^#### |^### 12\.3)",
        implementation,
        re.MULTILINE | re.DOTALL,
    ):
        dependency = re.search(r"^\*\*依赖：\*\* (.+)$", body, re.MULTILINE)
        cards[key] = re.findall(r"([A-Z]\d{2})", dependency.group(1)) if dependency else []
    return cards


def _acyclic(graph: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return False
        if node in visited:
            return True
        visiting.add(node)
        if any(not visit(dep) for dep in graph.get(node, [])):
            return False
        visiting.remove(node)
        visited.add(node)
        return True

    return all(visit(node) for node in graph)


def validate(paths: dict[str, Path]) -> dict:
    docs = {name: _read(path) for name, path in paths.items()}
    checks: list[dict] = []

    def record(name: str, passed: bool, details: object = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "details": details})

    for name in ("implementation", "ecosystem", "missing", "revision"):
        record(f"{name}_fences_balanced", _check_fences(docs[name]))

    implementation_chapters = [
        int(value) for value in re.findall(r"^## (\d+)\.", docs["implementation"], re.MULTILINE)
    ]
    ecosystem_chapters = [
        int(value) for value in re.findall(r"^## (\d+)\.", docs["ecosystem"], re.MULTILINE)
    ]
    record("implementation_chapters_contiguous", implementation_chapters == list(range(20)))
    record("ecosystem_chapters_contiguous", ecosystem_chapters == list(range(26)))

    cards = _task_cards(docs["implementation"])
    record("task_cards_include_rc_and_sk", {"R04", "R05"} <= set(cards), sorted(cards))
    unknown = sorted({dep for deps in cards.values() for dep in deps if dep not in cards})
    record("task_dependencies_exist", not unknown, unknown)
    record("task_dependency_graph_acyclic", _acyclic(cards))
    record(
        "store_mvp_does_not_depend_on_full_platform",
        not bool(
            {"M01", "M02", "M03", "I01", "R01", "R02", "U02", "A03"}
            & (set(cards.get("S04", ())) | set(cards.get("S05", ()))
        ),
    )
    )

    coverage = {
        "RC-01": {
            "missing": ("RC-01", "独立建卡", "入局兼容"),
            "ecosystem": ("RC-01", "角色库", "独立选择规则", "兼容检查"),
            "implementation": ("RC-01", "creation_rule_id", "独立角色库建卡", "入局兼容"),
        },
        "SK-01": {
            "missing": ("SK-01", "sequential checks", "composite action", "重复提交"),
            "ecosystem": ("SK-01", "sequential_checks", "composite_action", "重复提交"),
            "implementation": ("SK-01", "sequential_checks", "composite_action", "重复提交"),
        },
    }
    for requirement, sources in coverage.items():
        for source, phrases in sources.items():
            record(
                "{}_covers_{}".format(source, requirement),
                all(phrase in docs[source] for phrase in phrases),
                list(phrases),
            )
        record("revision_mentions_{}".format(requirement), requirement in docs["revision"])

    return {
        "passed": all(item["passed"] for item in checks),
        "check_count": len(checks),
        "task_cards": len(cards),
        "checks": checks,
        "files": {name: str(path) for name, path in paths.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--implementation", type=Path, required=True)
    parser.add_argument("--ecosystem", type=Path, required=True)
    parser.add_argument("--missing-requirements", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    result = validate({
        "implementation": args.implementation,
        "ecosystem": args.ecosystem,
        "missing": args.missing_requirements,
        "revision": args.revision,
    })
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("passed", "check_count", "task_cards")}, ensure_ascii=False))
    if not result["passed"]:
        for item in result["checks"]:
            if not item["passed"]:
                print(json.dumps(item, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
