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
