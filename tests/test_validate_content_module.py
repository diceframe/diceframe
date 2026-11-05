"""Content module packaging validation 测试（LIFE-04，母方案 §127/§199）。

端到端跑校验脚本的主函数：合法 homebrew 模组 → ok 报告（含冒险与 catalog
清单）；坏 manifest → errors + 退出码语义；已打包的 .dfplugin / .zip 按安装时
同一套解压规则校验。
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from scripts.validate_content_module import main, validate_module, validate_package_archive


def _make_module(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "homebrew-module"
    (plugin_dir / "packs" / "dnd2024").mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "schema_version": 1, "id": "homebrew-module", "name": "Homebrew",
        "version": "1.0.0", "plugin_type": "content-pack",
        "content_profile": "adventure-module", "content_delivery_mode": "catalog",
        "ruleset_catalogs": ["packs/dnd2024"], "contributes": {},
    }), encoding="utf-8")
    (plugin_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}}', encoding="utf-8",
    )
    (plugin_dir / "packs" / "dnd2024" / "monsters.json").write_text(json.dumps({
        "kind": "monster", "profile_id": "moss_golem", "name": "Moss Golem",
        "source_ref": "module:homebrew-module", "hp": 45, "armor_class": 14,
        "speed": 20,
        "abilities": {"str": 16, "dex": 8, "con": 16, "int": 5, "wis": 10, "cha": 5},
        "attacks": [{"id": "slam", "damage": "2d8+3", "attack_bonus": 5}],
    }), encoding="utf-8")
    return plugin_dir


def test_valid_module_report_is_ok(tmp_path: Path) -> None:
    report = validate_module(_make_module(tmp_path))
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["catalogs"] == [{"directory": "dnd2024", "records": 1}]
    assert "no adventure_packages declared" in report["warnings"]


def test_bad_catalog_record_fails_the_report(tmp_path: Path) -> None:
    plugin_dir = _make_module(tmp_path)
    (plugin_dir / "packs" / "dnd2024" / "broken.json").write_text(json.dumps({
        "kind": "monster", "profile_id": "broken", "name": "Broken",
        "source_ref": "module:homebrew-module", "hp": 0, "armor_class": 14,
        "speed": 20,
        "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "attacks": [{"id": "slam", "damage": "2d8+3", "attack_bonus": 5}],
    }), encoding="utf-8")
    report = validate_module(plugin_dir)
    assert report["ok"] is False
    assert any("catalog dnd2024" in error for error in report["errors"])


def test_missing_plugin_json_fails_closed(tmp_path: Path) -> None:
    empty = tmp_path / "empty-module"
    empty.mkdir()
    report = validate_module(empty)
    assert report["ok"] is False
    assert report["errors"] == ["missing plugin.json"]


@pytest.mark.parametrize("valid", [True, False])
def test_validation_does_not_write_to_the_authors_workspace(tmp_path: Path, valid: bool) -> None:
    plugin_dir = _make_module(tmp_path)
    if not valid:
        manifest = plugin_dir / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["entrypoint"] = ["python", "forbidden.py"]
        manifest.write_text(json.dumps(data), encoding="utf-8")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    report = validate_module(plugin_dir)

    assert report["ok"] is valid, report
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def _zip_module(plugin_dir: Path, archive: Path, *, prefix: str | None = None) -> Path:
    """Zip ``plugin_dir`` under ``prefix/`` (default: the directory name, like package_plugin)."""

    top = plugin_dir.name if prefix is None else prefix
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(plugin_dir.rglob("*")):
            if path.is_file():
                relative = path.relative_to(plugin_dir).as_posix()
                handle.write(path, f"{top}/{relative}" if top else relative)
    return archive


def test_package_archive_is_validated_like_an_install(tmp_path: Path) -> None:
    plugin_dir = _make_module(tmp_path)
    (plugin_dir / "README.md").write_text("# Homebrew\n", encoding="utf-8")
    (plugin_dir / "LICENSE").write_text("MIT\n", encoding="utf-8")
    archive = _zip_module(plugin_dir, tmp_path / "homebrew-module-1.0.0.dfplugin")

    report = validate_package_archive(archive)

    assert report["ok"] is True, report
    assert report["module"] == str(archive)
    assert report["catalogs"] == [{"directory": "dnd2024", "records": 1}]
    package = report["package"]
    assert package["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert package["bytes"] == archive.stat().st_size
    assert (package["plugin_id"], package["version"], package["plugin_type"]) == (
        "homebrew-module", "1.0.0", "content-pack",
    )
    assert package["has_license"] is True and package["has_readme"] is True


def test_package_archive_reports_missing_license_and_readme(tmp_path: Path) -> None:
    archive = _zip_module(_make_module(tmp_path), tmp_path / "pack.zip")

    report = validate_package_archive(archive)

    assert report["ok"] is True, report
    assert report["package"]["has_license"] is False
    assert report["package"]["has_readme"] is False


def test_package_archive_accepts_manifest_at_archive_root(tmp_path: Path) -> None:
    archive = _zip_module(_make_module(tmp_path), tmp_path / "homebrew-module-1.0.0-src.zip", prefix="")

    report = validate_package_archive(archive)

    assert report["ok"] is True, report
    assert report["package"]["plugin_id"] == "homebrew-module"


def test_package_archive_rejects_top_level_directory_that_is_not_the_plugin_id(tmp_path: Path) -> None:
    archive = _zip_module(_make_module(tmp_path), tmp_path / "pack.zip", prefix="renamed")

    report = validate_package_archive(archive)

    assert report["ok"] is False
    assert report["errors"] == ["插件包顶层目录名必须与插件 ID 一致"]


@pytest.mark.parametrize("bad_name", ["../escape.json", "/absolute.json"])
def test_package_archive_rejects_unsafe_paths(tmp_path: Path, bad_name: str) -> None:
    archive = _zip_module(_make_module(tmp_path), tmp_path / "pack.zip")
    with zipfile.ZipFile(archive, "a") as handle:
        handle.writestr(bad_name, "{}")

    report = validate_package_archive(archive)

    assert report["ok"] is False
    # 绝对路径在 Windows 上没有盘符，会落到越界检查；两条都是安装时的拒绝理由。
    assert any("非法路径" in error or "路径越界" in error for error in report["errors"]), report


def test_package_archive_rejects_non_zip_payload(tmp_path: Path) -> None:
    archive = tmp_path / "pack.dfplugin"
    archive.write_bytes(b"not a zip")

    report = validate_package_archive(archive)

    assert report["ok"] is False
    assert any("zip" in error for error in report["errors"]), report


def test_package_archive_extracts_only_after_validation_passes(tmp_path: Path) -> None:
    plugin_dir = _make_module(tmp_path)
    good = _zip_module(plugin_dir, tmp_path / "good.zip")
    target = tmp_path / "out" / "homebrew-module"

    assert validate_package_archive(good, extract_to=target)["ok"] is True
    assert json.loads((target / "plugin.json").read_text(encoding="utf-8"))["id"] == "homebrew-module"

    again = validate_package_archive(good, extract_to=target)
    assert again["ok"] is False
    assert again["errors"] == [f"extract target already exists: {target}"]

    manifest = plugin_dir / "plugin.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["entrypoint"] = ["python", "forbidden.py"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    bad = _zip_module(plugin_dir, tmp_path / "bad.zip")
    rejected_target = tmp_path / "rejected" / "homebrew-module"
    assert validate_package_archive(bad, extract_to=rejected_target)["ok"] is False
    assert not rejected_target.exists()


def test_cli_validates_a_package_file(tmp_path: Path, monkeypatch, capsys) -> None:
    archive = _zip_module(_make_module(tmp_path), tmp_path / "pack.zip")
    monkeypatch.setattr(sys, "argv", ["validate_content_module.py", str(archive), "--json"])

    assert main() == 0
    assert json.loads(capsys.readouterr().out)["package"]["plugin_id"] == "homebrew-module"
