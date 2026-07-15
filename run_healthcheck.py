"""一键运行 TRPG 插件健康检查。"""

from __future__ import annotations

import os
import subprocess
import sys


COMMANDS = [
    ("Text mojibake audit", [sys.executable, "audit_text_i18n.py"]),
    ("API route contract audit", [sys.executable, "audit_api_contracts.py"]),
    (
        "Python compile check",
        [
            sys.executable,
            "-m",
            "py_compile",
            "audit_api_contracts.py",
            "audit_text_i18n.py",
            "run_healthcheck.py",
            "src/webui/api.py",
            "src/webui/routes/pages.py",
            "web_server.py",
            "src/commands/game_handler.py",
            "src/engine/game_instance.py",
            "src/engine/character_utils.py",
        ],
    ),
    ("Pytest", [sys.executable, "-m", "pytest", "-q"]),
]


def main() -> int:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    for title, command in COMMANDS:
        print(f"\n== {title} ==", flush=True)
        result = subprocess.run(command, text=True, env=env)
        if result.returncode != 0:
            print(f"\nHealthcheck failed: {title} (exit={result.returncode})")
            return result.returncode
    print("\nHealthcheck complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())