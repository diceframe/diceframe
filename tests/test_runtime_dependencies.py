from __future__ import annotations

from pathlib import Path

from scripts import ensure_runtime_deps


def test_missing_pydantic_ai_is_reported_as_a_runtime_dependency(monkeypatch) -> None:
    def fake_find_spec(name: str):
        return None if name == "pydantic_ai" else object()

    monkeypatch.setattr(ensure_runtime_deps.importlib.util, "find_spec", fake_find_spec)

    assert ensure_runtime_deps.missing_imports() == ["pydantic-ai-slim"]


def test_startup_installs_requirements_when_pydantic_ai_is_missing(monkeypatch, tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pydantic-ai-slim==1.107.7\n", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(ensure_runtime_deps, "REQUIREMENTS", requirements)
    monkeypatch.setattr(ensure_runtime_deps, "missing_imports", lambda: ["pydantic-ai-slim"])

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(ensure_runtime_deps.subprocess, "run", fake_run)

    assert ensure_runtime_deps.main() == 0
    assert calls == [
        (
            [
                ensure_runtime_deps.sys.executable,
                "-m",
                "pip",
                "install",
                # 优先拿 wheel，避免在没装编译工具链的机器上卡在源码构建。
                "--prefer-binary",
                "-r",
                str(requirements),
            ],
            {"cwd": str(ensure_runtime_deps.ROOT)},
        )
    ]
