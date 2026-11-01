from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from scripts import start_webui


def test_ensure_frontend_repairs_an_incomplete_node_modules(monkeypatch, tmp_path: Path):
    frontend_dir = tmp_path / "frontend-v2"
    (frontend_dir / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(start_webui, "FRONTEND_DIR", frontend_dir)
    monkeypatch.setattr(start_webui, "frontend_built", lambda: False)
    monkeypatch.setattr(start_webui.shutil, "which", lambda name: "npm.cmd")

    commands: list[list[str]] = []
    monkeypatch.setattr(
        start_webui,
        "run",
        lambda command, cwd: commands.append(command) or 0,
    )

    assert start_webui.ensure_frontend() == 0
    assert commands == [["npm.cmd", "ci", "--include=dev"], ["npm.cmd", "run", "build"]]
