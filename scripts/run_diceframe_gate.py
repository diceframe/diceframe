"""Run the DiceFrame plan's read-only integration gate.

The gate intentionally performs verification only.  It never stages files,
changes versions, creates tags, pushes, or publishes a release.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def _run(command: list[str], *, cwd: Path, timeout: int = 900) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        shell=False,
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "")[-20_000:],
        "stderr": (completed.stderr or "")[-20_000:],
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--implementation", type=Path, required=True)
    parser.add_argument("--ecosystem", type=Path, required=True)
    parser.add_argument("--missing-requirements", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--index-repo", type=Path)
    parser.add_argument("--frontend", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    checks: list[dict[str, Any]] = []

    checks.append(_run([
        sys.executable, "scripts/validate_diceframe_plan.py",
        "--implementation", str(args.implementation),
        "--ecosystem", str(args.ecosystem),
        "--missing-requirements", str(args.missing_requirements),
        "--revision", str(args.revision),
    ], cwd=root))
    checks.append(_run(["git", "diff", "--check"], cwd=root))
    checks.append(_run([
        sys.executable, "-m", "pytest",
        "tests/architecture/test_gameplay_packages.py",
        "tests/test_creator_projects.py",
        "tests/test_creator_routes.py",
        "tests/test_creator_worker.py",
        "tests/test_creator_worker_jobs.py",
        "tests/test_creator_sandbox.py",
        "tests/test_remote_ruleset_adapter.py",
        "tests/test_external_runtime_acceptance.py",
        "tests/test_stdio_runtime_transport.py",
        "tests/test_gameplay_provider_registry.py",
        "tests/test_gameplay_view_route.py",
        "tests/test_assistant_agent_application.py",
        "tests/test_document_reader.py",
        "tests/test_plugin_rulebook_routes.py",
        "tests/test_plugin_requirements.py",
        "tests/test_prebuilt_ui_route.py",
        "tests/test_plugin_host.py",
        "tests/test_plugin_provider.py",
        "tests/test_ui_extension_probe.py",
        "tests/test_ui_isolation.py",
        "tests/test_ui_isolation_browser.py",
        "tests/test_ui_actions.py",
        "tests/test_plugin_ui_projection.py",
        "-q", "-p", "no:cacheprovider",
    ], cwd=root))

    if args.index_repo:
        index_tests = [str(path.relative_to(args.index_repo)) for path in sorted((args.index_repo / "tests").glob("*.test.cjs"))]
        checks.append(_run(["node", "--test", *index_tests], cwd=args.index_repo))
        checks.append(_run(["node", "scripts/validate.cjs"], cwd=args.index_repo))
    if args.frontend:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        checks.append(_run([npm, "exec", "vue-tsc", "--", "-b"], cwd=args.frontend))
        checks.append(_run([npm, "exec", "eslint", "--", "src", "tests", "--max-warnings=0"], cwd=args.frontend))

    failed = [item for item in checks if item["returncode"] != 0]
    print(json.dumps({
        "passed": not failed,
        "check_count": len(checks),
        "failed": len(failed),
        "checks": checks,
    }, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
