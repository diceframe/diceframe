from pathlib import Path
import json

import pytest

from src.creator.projects import CreatorProjectStore


def test_creator_project_store_scopes_reads_and_uses_revisions(tmp_path: Path):
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    created = store.create("alice", "Harbor", source={"kind": "world", "id": "harbor"})
    project_id = created["project"]["id"]

    updated = store.update(
        "alice",
        project_id,
        expected_revision=0,
        files={"world.json": '{"id":"harbor"}', "README.md": "draft"},
    )
    assert updated["project"]["revision"] == 1
    assert updated["project"]["files"]["README.md"] == "draft"
    assert updated["project"]["digest"].startswith("sha256:")
    assert updated["project"]["checkpoints"][-1]["revision"] == 1

    with pytest.raises(KeyError):
        store.read("bob", project_id)
    with pytest.raises(ValueError, match="版本已变化"):
        store.update("alice", project_id, expected_revision=0, files={"README.md": "stale"})


def test_creator_project_store_rejects_escape_and_secret_paths(tmp_path: Path):
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    project_id = store.create("alice", "Harbor")["project"]["id"]

    with pytest.raises(ValueError, match="路径非法"):
        store.update("alice", project_id, expected_revision=0, files={"../secret.txt": "x"})
    with pytest.raises(ValueError, match="凭据"):
        store.update("alice", project_id, expected_revision=0, files={".env": "KEY=x"})


def test_creator_project_store_validation_is_revision_bound(tmp_path: Path):
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    project_id = store.create("alice", "Harbor")["project"]["id"]
    invalid = store.validate("alice", project_id)
    assert invalid["ok"] is False
    assert "plugin.json" in invalid["errors"][0]
    store.update(
        "alice",
        project_id,
        expected_revision=0,
        files={"plugin.json": '{"schema_version":1,"id":"harbor","name":"Harbor","version":"1.0.0","plugin_type":"content-pack"}'},
    )
    valid = store.validate("alice", project_id)
    assert valid["ok"] is True
    assert valid["revision"] == 1


def test_creator_project_build_is_deterministic_for_a_fixed_revision(tmp_path: Path):
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    project_id = store.create("alice", "Harbor")["project"]["id"]
    store.update(
        "alice",
        project_id,
        expected_revision=0,
        files={"plugin.json": '{"schema_version":1,"id":"harbor","name":"Harbor","version":"1.0.0","plugin_type":"content-pack"}'},
    )
    first = store.build("alice", project_id, expected_revision=1)
    second = store.build("alice", project_id, expected_revision=1)
    assert first["ok"] is True
    assert first["artifact_sha256"] == second["artifact_sha256"]
    assert first["payload"] == second["payload"]
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["receipt_id"] == second["receipt_id"]
    restored = CreatorProjectStore(tmp_path / "creator-projects.json")
    replayed = restored.build("alice", project_id, expected_revision=1)
    assert replayed["replayed"] is True
    assert replayed["receipt_id"] == first["receipt_id"]


def test_creator_project_preview_is_bounded_and_does_not_expose_source_files(tmp_path: Path):
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    project_id = store.create("alice", "Harbor")['project']['id']
    store.update(
        "alice", project_id, expected_revision=0,
        files={
            "plugin.json": '{"schema_version":1,"id":"harbor","name":"Harbor","version":"1.0.0","plugin_type":"content-pack","content_profile":"content-pack"}',
            "README.md": "# Harbor\n\nA preview.",
            "content/items/sword.json": '{"id":"sword"}',
        },
    )
    preview = store.preview("alice", project_id)
    assert preview["ok"] is True
    assert preview["preview"]["name"] == "Harbor"
    assert preview["preview"]["resource_counts"]["content"] == 1
    assert preview["preview"]["rulebooks"] == []
    assert "files" not in preview["preview"]


def test_creator_project_preview_projects_declared_rulebooks_without_reading_content(tmp_path: Path):
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    project_id = store.create("alice", "Rules")['project']['id']
    store.update(
        "alice", project_id, expected_revision=0,
        files={
            "plugin.json": json.dumps({
                "schema_version": 1, "id": "rules", "name": "Rules",
                "version": "1.0.0", "plugin_type": "content-pack",
                "rulebooks": [{"id": "player", "title": "Player", "path": "docs/player.md", "audience": "player"}],
            }),
            "docs/player.md": "# Player rules",
        },
    )
    preview = store.preview("alice", project_id)
    assert preview["preview"]["rulebooks"] == [{
        "id": "player", "title": "Player", "audience": "player",
        "path": "docs/player.md", "present": True,
    }]


def test_creator_submission_receipt_omits_payload_and_requires_manual_review(tmp_path: Path):
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    project_id = store.create("alice", "Harbor")['project']['id']
    store.update(
        "alice", project_id, expected_revision=0,
        files={"plugin.json": '{"schema_version":1,"id":"harbor","name":"Harbor","version":"1.2.3","plugin_type":"content-pack"}'},
    )
    receipt = store.submission("alice", project_id, expected_revision=1)
    assert receipt["ok"] is True
    assert "payload" not in receipt
    assert receipt["submission"]["release_tag"] == "v1.2.3"
    assert receipt["submission"]["requires_manual_upload"] is True


def test_creator_project_delete_is_owner_scoped(tmp_path: Path):
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    project_id = store.create("alice", "Harbor")["project"]["id"]
    with pytest.raises(KeyError):
        store.delete("bob", project_id)
    assert store.delete("alice", project_id)["deleted"] is True
    assert store.list("alice")["projects"] == []


def test_creator_project_update_revalidates_merged_capacity(tmp_path: Path):
    """P2-01 回归：多次合法的小批次写入不能绕过 4 MiB 总大小上限。"""
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    created = store.create("alice", "Bulk", source={"kind": "world", "id": "bulk"})
    project_id = created["project"]["id"]
    revision = 0

    # 第一批 15 × 250 KiB ≈ 3.75 MiB：单批合法，且在总量内。
    store.update("alice", project_id, expected_revision=revision,
                 files={f"chunk-{index}.txt": "x" * (250 * 1024) for index in range(15)})
    revision += 1
    before = store.read("alice", project_id)

    # 第二批自身只有 2 × 250 KiB（合法），但合并后超过 4 MiB。
    with pytest.raises(ValueError, match="工程文件总大小超过 4 MiB"):
        store.update("alice", project_id, expected_revision=revision,
                     files={"overflow-1.txt": "x" * (250 * 1024),
                            "overflow-2.txt": "x" * (250 * 1024)})
    after = store.read("alice", project_id)
    assert after["project"]["revision"] == revision  # revision 完全不变
    assert after["project"]["files"] == before["project"]["files"]  # 内容不变


def test_creator_project_update_revalidates_merged_file_count(tmp_path: Path):
    """P2-01 回归：文件数上限按合并后的最终集合校验。"""
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    created = store.create("alice", "Many", source={"kind": "world", "id": "many"})
    project_id = created["project"]["id"]
    revision = 0
    for start in range(0, 2048, 256):
        store.update("alice", project_id, expected_revision=revision,
                     files={f"f-{start + index}.txt": "x" for index in range(256)})
        revision += 1
    assert len(store.read("alice", project_id)["project"]["files"]) == 2048

    with pytest.raises(ValueError, match="工程文件数量超过 2048"):
        store.update("alice", project_id, expected_revision=revision,
                     files={"f-2048.txt": "x"})
    after = store.read("alice", project_id)
    assert after["project"]["revision"] == revision
    assert len(after["project"]["files"]) == 2048


def test_creator_validate_runs_install_package_check(tmp_path: Path):
    """P1-03：Creator 验证复用与安装同源的深度校验，两层结果分开上报。"""
    store = CreatorProjectStore(tmp_path / "creator-projects.json", package_checker=lambda payload: [])
    created = store.create("alice", "Pack", source={"kind": "world", "id": "pack"})
    project_id = created["project"]["id"]
    store.update("alice", project_id, expected_revision=0, files={
        "plugin.json": json.dumps({"schema_version": 1, "id": "pack", "name": "Pack",
                                   "version": "1.0.0", "plugin_type": "content-pack"}),
    })
    checked = store.validate("alice", project_id)
    assert checked["ok"] is True
    assert checked["package_check"] == {"ok": True, "errors": []}
    assert checked["installable"] is True


def test_creator_build_refuses_package_check_failures(tmp_path: Path):
    """P1-03：结构检查通过但深度校验失败 → Build 拒绝并完整报告，不产生收据。"""
    store = CreatorProjectStore(tmp_path / "creator-projects.json",
                                package_checker=lambda payload: ["插件 ID 非法"])
    created = store.create("alice", "Pack", source={"kind": "world", "id": "pack"})
    project_id = created["project"]["id"]
    store.update("alice", project_id, expected_revision=0, files={
        "plugin.json": json.dumps({"schema_version": 1, "id": "pack", "name": "Pack",
                                   "version": "1.0.0", "plugin_type": "content-pack"}),
        "notes.txt": "draft",
    })
    checked = store.validate("alice", project_id)
    assert checked["ok"] is True  # 结构检查通过
    assert checked["package_check"] == {"ok": False, "errors": ["插件 ID 非法"]}
    assert checked["installable"] is False

    built = store.build("alice", project_id, expected_revision=checked["revision"])
    assert built["ok"] is False
    assert built["package_check"]["errors"] == ["插件 ID 非法"]

    # 校验器修好后同一 revision 可正常构建（之前失败未产生收据，也非重放）。
    store._package_checker = lambda payload: []
    rebuilt = store.build("alice", project_id, expected_revision=checked["revision"])
    assert rebuilt["ok"] is True
    assert rebuilt["replayed"] is False


def test_creator_validate_skips_package_check_when_structure_fails(tmp_path: Path):
    """结构失败时不跑深度校验（package_check 为 None），installable 为 False。"""
    calls = []
    store = CreatorProjectStore(tmp_path / "creator-projects.json",
                                package_checker=lambda payload: calls.append(payload) or [])
    created = store.create("alice", "Broken", source={"kind": "world", "id": "broken"})
    project_id = created["project"]["id"]
    store.update("alice", project_id, expected_revision=0, files={"README.md": "no manifest"})
    checked = store.validate("alice", project_id)
    assert checked["ok"] is False
    assert checked["package_check"] is None
    assert checked["installable"] is False
    assert calls == []


def test_creator_project_update_reports_explicit_changed_paths(tmp_path: Path):
    """update 结果带显式 changed 清单：agent 观察与前端刷新都靠它，不靠整包反推。"""
    store = CreatorProjectStore(tmp_path / "creator-projects.json")
    project_id = store.create("alice", "Harbor")["project"]["id"]

    updated = store.update(
        "alice", project_id, expected_revision=0,
        files={"world.json": '{"id":"harbor"}', "content/npcs/mara.json": '{}'},
    )
    assert updated["changed"] == ["content/npcs/mara.json", "world.json"]

    removed = store.update(
        "alice", project_id, expected_revision=1,
        files={"world.json": '{"id":"harbor","v":2}'}, remove=["content/npcs/mara.json"],
    )
    assert removed["changed"] == ["content/npcs/mara.json", "world.json"]
    assert "content/npcs/mara.json" not in removed["project"]["files"]

    # 纯改名（不动文件）时清单为空，字段仍然存在。
    renamed = store.update("alice", project_id, expected_revision=2, name="Harbor 2")
    assert renamed["changed"] == []
