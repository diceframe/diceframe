"""Run the repository's backend and frontend quality gates."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NPM = "npm.cmd" if os.name == "nt" else "npm"

COMMANDS = [
    ("Text mojibake audit", [sys.executable, "scripts/audit_text_i18n.py"]),
    ("API route contract audit", [sys.executable, "scripts/audit_api_contracts.py"]),
    ("Architecture audit", [sys.executable, "scripts/audit_architecture.py"]),
    ("Python compile check", [sys.executable, "-m", "compileall", "-q", "src", "scripts", "web_server.py"]),
    ("Python correctness lint", [sys.executable, "-m", "ruff", "check", "."]),
    ("Gradual Python type check", [sys.executable, "-m", "mypy"]),
    (
        "Pytest and coverage",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--cov=src",
            "--cov-branch",
            "--cov-report=term:skip-covered",
        ],
    ),
    ("Frontend lint", [NPM, "run", "lint"], ROOT / "frontend-v2"),
    ("Frontend type check", [NPM, "run", "typecheck"], ROOT / "frontend-v2"),
    ("Frontend unit tests", [NPM, "run", "test"], ROOT / "frontend-v2"),
    ("Frontend production build", [NPM, "run", "build"], ROOT / "frontend-v2"),
]


def main() -> int:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    for item in COMMANDS:
        title, command, *cwd = item
        print(f"\n== {title} ==", flush=True)
        result = subprocess.run(command, text=True, env=env, cwd=cwd[0] if cwd else ROOT)
        if result.returncode != 0:
            print(f"\nHealthcheck failed: {title} (exit={result.returncode})")
            return result.returncode
    print("\nHealthcheck complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
