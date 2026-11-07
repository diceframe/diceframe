"""Audit the public narration of a saved game without calling a model.

Reports, per round and in total:

- hidden-lore leaks: GM-only / character-only lore whose own wording appears in
  public narration (see ``src.knowledge.leak_audit``);
- protocol leaks: state tags that escaped into the narration;
- repeated sentences copied from the previous round;
- sanity checks resolved (``[Sanity Check]`` state changes).

Usage::

    python scripts/audit_narration.py --save data/saves/<game>/state.json \
        --lorebook exported_lorebook.json [--lorebook more.json] [--json]

``--save`` accepts a ``state.json`` (its recent log window) or a
``chatlog.jsonl`` (full history). Lorebook files may be a list of entries, an
object with ``entries`` (list or id -> entry map), or a DiceFrame export.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.leak_audit import (  # noqa: E402
    DEFAULT_MIN_RUNS,
    DEFAULT_RUN_SIZE,
    audit_rounds,
    split_lore_by_audience,
    summarize,
)


def load_rounds(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    log = data.get("log") if isinstance(data, dict) else None
    if not isinstance(log, list):
        raise SystemExit(f"{path}: no log list found")
    return [entry for entry in log if isinstance(entry, dict)]


def load_lore_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        for key in ("entries", "lore_entries", "items"):
            if key in data:
                data = data[key]
                break
        else:
            book = data.get("lorebook") or data.get("book")
            if isinstance(book, dict):
                data = book.get("entries", [])
    if isinstance(data, dict):
        data = list(data.values())
    if not isinstance(data, list):
        raise SystemExit(f"{path}: no lore entries found")
    return [entry for entry in data if isinstance(entry, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", type=Path, required=True)
    parser.add_argument("--lorebook", type=Path, action="append", default=[])
    parser.add_argument("--run-size", type=int, default=DEFAULT_RUN_SIZE)
    parser.add_argument("--min-runs", type=int, default=DEFAULT_MIN_RUNS)
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)

    rounds = load_rounds(args.save)
    entries: list[dict[str, Any]] = []
    for path in args.lorebook:
        entries.extend(load_lore_entries(path))
    protected, allowed = split_lore_by_audience(entries)
    results = audit_rounds(rounds, protected, allowed, run_size=args.run_size, min_runs=args.min_runs)
    report = {"summary": summarize(results), "protected_entries": len(protected), "public_entries": len(allowed)}

    if args.json:
        report["rounds"] = [asdict(item) for item in results]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    summary = report["summary"]
    print(f"rounds: {summary['rounds']}  average chars: {summary['average_chars']}")
    print(f"lore: {len(protected)} protected / {len(allowed)} public entries")
    print(f"sanity checks: {summary['sanity_checks']}  repeated sentences: {summary['repeated_sentences']}")
    print(f"protocol leaks in rounds: {summary['protocol_leak_rounds'] or 'none'}")
    for item in results:
        for finding in item.leaks:
            print(f"round {item.round}: possible leak of {finding.source_id} ({finding.runs} runs) e.g. {finding.evidence}")
    if not summary["leak_rounds"]:
        print("hidden-lore leaks: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
