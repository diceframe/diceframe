"""_required_runtimes：冒险包 manifest 的 required_runtime 派生，与安装深校验同事实来源。"""
import json
from pathlib import Path
from types import SimpleNamespace

from src.webui.services.modules import _required_runtimes


def _runtime(tmp_path: Path, packages: dict[str, dict | None]):
    root = tmp_path / "adventures"
    root.mkdir()
    for directory, manifest in packages.items():
        target = root / directory
        target.mkdir(parents=True)
        if manifest is not None:
            (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return SimpleNamespace(adventure_packages_root=root, adventure_package_directories=tuple(packages))


def test_required_runtimes_dedupes_and_keeps_highest_minimum(tmp_path):
    runtime = _runtime(tmp_path, {
        "alpha": {"required_runtime": {"id": "community:fabula-ultima", "minimum_version": 1}},
        "beta": {"required_runtime": {"id": "community:fabula-ultima", "minimum_version": 2}},
        "gamma": {"required_runtime": {"id": "core:dnd2024", "minimum_version": 1}},
    })
    assert _required_runtimes(runtime) == [
        {"id": "community:fabula-ultima", "minimum_version": 2},
        {"id": "core:dnd2024", "minimum_version": 1},
    ]


def test_required_runtimes_reports_unknown_without_guessing(tmp_path):
    runtime = _runtime(tmp_path, {
        "blank_id": {"required_runtime": {"id": "  ", "minimum_version": 1}},
        "no_decl": {},
        "missing_manifest": None,
    })
    assert _required_runtimes(runtime) == []


def test_required_runtimes_handles_missing_root_and_bad_minimum(tmp_path):
    assert _required_runtimes(SimpleNamespace(adventure_packages_root=None, adventure_package_directories=())) == []
    runtime = _runtime(tmp_path, {"alpha": {"required_runtime": {"id": "core:x", "minimum_version": "bad"}}})
    assert _required_runtimes(runtime) == [{"id": "core:x", "minimum_version": 1}]
