import json

from scripts import build_release


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_copy_tree_excludes_legacy_user_templates(monkeypatch, tmp_path):
    root = tmp_path / "root"
    source = root / "templates" / "rules"
    target = tmp_path / "package" / "templates" / "rules"
    _write(source / "base.json", {"rule_id": "base", "rule_name": "内置"})
    _write(source / "custom_rule_home.json", {
        "rule_id": "custom_rule_home",
        "rule_name": "用户规则",
        "custom": True,
    })
    monkeypatch.setattr(build_release, "ROOT", root)

    build_release.copy_tree(source, target)

    assert (target / "base.json").exists()
    assert not (target / "custom_rule_home.json").exists()


def test_prepare_package_tree_excludes_docs(monkeypatch, tmp_path):
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "USER_GUIDE_CN.md").write_text("guide", encoding="utf-8")
    package = tmp_path / "package"
    monkeypatch.setattr(build_release, "ROOT", root)

    build_release.prepare_package_tree(package)

    assert not (package / "docs").exists()
