"""Evaluate a captured multi-round narrative trace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.commands.narrative_quality_eval import evaluate_quality_trace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path, help="JSON file containing a turns array")
    parser.add_argument("--window", type=int, default=5)
    args = parser.parse_args()
    payload = json.loads(args.trace.read_text(encoding="utf-8"))
    turns = payload.get("turns") if isinstance(payload, dict) else payload
    print(json.dumps(
        evaluate_quality_trace(turns or [], window_size=args.window),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
