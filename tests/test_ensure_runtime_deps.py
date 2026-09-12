from __future__ import annotations

from scripts import ensure_runtime_deps


def test_runtime_dependency_check_includes_peewee(monkeypatch) -> None:
    def fake_find_spec(name: str):
        return None if name == "peewee" else object()

    monkeypatch.setattr(ensure_runtime_deps.importlib.util, "find_spec", fake_find_spec)

    assert ensure_runtime_deps.missing_imports() == ["peewee"]
