from __future__ import annotations

import json
import zipfile
import pytest

from src.plugin_host.requirements import (
    dependency_rows,
    parse_plugin_requirements,
    plugin_version_satisfies,
)
from src.plugin_host.host import PluginHost
from plugin_host_common import write_plugin


def test_plugin_requirements_are_bounded_and_versioned():
    assert parse_plugin_requirements({
        "requires": {"plugins": [{"id": "base-pack", "minimum_version": "1.2"}]},
    }) == [{"id": "base-pack", "minimum_version": "1.2"}]
    assert plugin_version_satisfies("1.2", "1.2.0") is True
    assert plugin_version_satisfies("2", "1.9.9") is False
    with pytest.raises(ValueError, match="duplicate"):
        parse_plugin_requirements({
            "requires": {"plugins": [
                {"id": "base-pack", "minimum_version": "1"},
                {"id": "base-pack", "minimum_version": "1"},
            ]},
        })

    rows = dependency_rows({
        "base-pack": {"version": "2"},
        "dependent-pack": {
            "version": "1",
            "requires": {"plugins": [{"id": "base-pack", "minimum_version": "2"}]},
        },
    })
    assert rows[1]["dependencies"][0]["satisfied"] is True


def test_discover_fails_closed_on_missing_plugin_dependency(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins,
        "dependent-pack",
        plugin_type="content-pack",
        entrypoint=False,
        manifest_extra={"requires": {"plugins": [{"id": "base-pack", "minimum_version": "1"}]}},
    )
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    detail = host.public_detail("dependent-pack")
    assert detail["status"] == "failed"
    assert "missing plugin dependency" in detail["error"]
    assert host.contributions.list() == []


def test_discover_fails_closed_on_plugin_dependency_version_mismatch(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "base-pack", plugin_type="content-pack", entrypoint=False,
                 manifest_extra={"version": "1"})
    write_plugin(
        plugins, "dependent-pack", plugin_type="content-pack", entrypoint=False,
        manifest_extra={"requires": {"plugins": [{"id": "base-pack", "minimum_version": "2"}]}},
    )
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    assert host.public_detail("dependent-pack")["status"] == "failed"
    assert "version mismatch" in host.public_detail("dependent-pack")["error"]


def test_discover_fails_closed_on_dependency_cycle(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(
        plugins, "pack-a", plugin_type="content-pack", entrypoint=False,
        manifest_extra={"requires": {"plugins": [{"id": "pack-b", "minimum_version": "1"}]}},
    )
    write_plugin(
        plugins, "pack-b", plugin_type="content-pack", entrypoint=False,
        manifest_extra={"requires": {"plugins": [{"id": "pack-a", "minimum_version": "1"}]}},
    )
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    assert host.public_detail("pack-a")["status"] == "failed"
    assert host.public_detail("pack-b")["status"] == "failed"
    assert "cycle" in host.public_detail("pack-a")["error"]


@pytest.mark.asyncio
async def test_install_blocks_missing_plugin_dependency_before_publish(tmp_path):
    package = tmp_path / "dependent.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("dependent-pack/plugin.json", json.dumps({
            "schema_version": 1, "id": "dependent-pack", "name": "Dependent",
            "version": "1", "description": "dependent", "plugin_type": "channel-adapter",
            "entrypoint": ["{python}", "-c", "pass"], "config_schema": "config.schema.json",
            "requires": {"plugins": [{"id": "base-pack", "minimum_version": "1"}]},
        }))
        archive.writestr("dependent-pack/config.schema.json", json.dumps({
            "type": "object", "properties": {},
        }))
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    with pytest.raises(ValueError, match="missing plugin dependency"):
        await host.install_from_zip(package.read_bytes())
    assert not (tmp_path / "plugins" / "dependent-pack").exists()


def test_dependency_plan_exposes_reverse_edges_and_blockers(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "base-pack", plugin_type="content-pack", entrypoint=False,
                 manifest_extra={"version": "2"})
    write_plugin(
        plugins, "dependent-pack", plugin_type="content-pack", entrypoint=False,
        manifest_extra={"requires": {"plugins": [{"id": "base-pack", "minimum_version": "2"}]}},
    )
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    plan = {row["id"]: row for row in host.dependency_plan()}
    assert plan["dependent-pack"]["dependencies"][0]["satisfied"] is True
    assert plan["base-pack"]["dependents"] == ["dependent-pack"]
    assert host.public_detail("base-pack")["dependents"] == ["dependent-pack"]


def _package_bytes(plugin_id: str, version: str, *, requires: dict | None = None) -> bytes:
    import io

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        manifest = {
            "schema_version": 1, "id": plugin_id, "name": plugin_id,
            "version": version, "description": plugin_id,
            "plugin_type": "content-pack", "config_schema": "config.schema.json",
        }
        if requires is not None:
            manifest["requires"] = requires
        archive.writestr(f"{plugin_id}/plugin.json", json.dumps(manifest))
        archive.writestr(f"{plugin_id}/config.schema.json", json.dumps({
            "type": "object", "properties": {
                "enabled": {"type": "boolean", "default": True},
            },
        }))
    return payload.getvalue()


def test_group_install_plan_resolves_candidate_dag_without_publishing(tmp_path):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    plan = host.plan_install_group([
        _package_bytes("dependent-pack", "1", requires={
            "plugins": [{"id": "base-pack", "minimum_version": "1"}],
        }),
        _package_bytes("base-pack", "1"),
    ])
    assert plan["ok"] is True
    assert plan["atomic_commit"] is False
    assert plan["order"] == ["base-pack", "dependent-pack"]
    assert not (tmp_path / "plugins").exists()


def test_group_install_plan_reports_missing_and_cycles_before_mutation(tmp_path):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    missing = host.plan_install_group([_package_bytes(
        "dependent-pack", "1", requires={
            "plugins": [{"id": "missing-pack", "minimum_version": "1"}],
        },
    )])
    assert missing["ok"] is False
    assert any("missing plugin dependency" in row["reason"] for row in missing["blockers"])
    cycle = host.plan_install_group([
        _package_bytes("pack-a", "1", requires={"plugins": [{"id": "pack-b", "minimum_version": "1"}]}),
        _package_bytes("pack-b", "1", requires={"plugins": [{"id": "pack-a", "minimum_version": "1"}]}),
    ])
    assert cycle["ok"] is False
    assert {row["plugin_id"] for row in cycle["blockers"] if "cycle" in row["reason"]} == {"pack-a", "pack-b"}


@pytest.mark.asyncio
async def test_group_install_publishes_candidates_and_cleans_transaction_journal(tmp_path):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    details = await host.install_group_from_zips([
        _package_bytes("dependent-pack", "1", requires={
            "plugins": [{"id": "base-pack", "minimum_version": "1"}],
        }),
        _package_bytes("base-pack", "1"),
    ])
    assert [item["id"] for item in details] == ["dependent-pack", "base-pack"]
    assert host.public_detail("dependent-pack")["status"] == "active"
    assert host.public_detail("base-pack")["status"] == "active"
    assert not (tmp_path / "data" / "_plugin_group_install_journal.json").exists()
    receipt = json.loads((tmp_path / "data" / "dependent-pack" / "install.json").read_text())
    assert receipt["resolved_dependencies"] == [{
        "id": "base-pack", "minimum_version": "1", "version": "1",
    }]


@pytest.mark.asyncio
async def test_group_install_rolls_back_all_candidates_when_publish_fails(tmp_path, monkeypatch):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    import src.plugin_host.host as host_module

    original = host_module._rename_dir_with_retry
    calls = {"count": 0}

    async def fail_on_second_publish(src, dst, **kwargs):
        if ".group-" in src.name and src.name.endswith(".installing"):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("injected publish failure")
        return await original(src, dst, **kwargs)

    monkeypatch.setattr(host_module, "_rename_dir_with_retry", fail_on_second_publish)
    with pytest.raises(OSError, match="injected publish failure"):
        await host.install_group_from_zips([
            _package_bytes("pack-a", "1"), _package_bytes("pack-b", "1"),
        ])
    assert not (tmp_path / "plugins" / "pack-a").exists()
    assert not (tmp_path / "plugins" / "pack-b").exists()
    assert not (tmp_path / "data" / "_plugin_group_install_journal.json").exists()


@pytest.mark.asyncio
async def test_uninstall_blocks_installed_dependents(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "base-pack", plugin_type="content-pack", entrypoint=False)
    write_plugin(
        plugins, "dependent-pack", plugin_type="content-pack", entrypoint=False,
        manifest_extra={"requires": {"plugins": [{"id": "base-pack", "minimum_version": "1"}]}},
    )
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    with pytest.raises(ValueError, match="required by installed plugin"):
        await host.uninstall("base-pack")
    assert (plugins / "base-pack").exists()


@pytest.mark.asyncio
async def test_update_blocks_candidate_that_breaks_installed_dependents(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "base-pack", plugin_type="content-pack", entrypoint=False,
                 manifest_extra={"version": "2"})
    write_plugin(
        plugins, "dependent-pack", plugin_type="content-pack", entrypoint=False,
        manifest_extra={"requires": {"plugins": [{"id": "base-pack", "minimum_version": "2"}]}},
    )
    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    package = tmp_path / "base-v1.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("base-pack/plugin.json", json.dumps({
            "schema_version": 1, "id": "base-pack", "name": "Base",
            "version": "1", "description": "base", "plugin_type": "content-pack",
            "config_schema": "config.schema.json",
        }))
        archive.writestr("base-pack/config.schema.json", json.dumps({
            "type": "object", "properties": {},
        }))
    with pytest.raises(ValueError, match="would break dependency"):
        await host.install_from_zip(package.read_bytes(), overwrite=True)
    assert host.public_detail("base-pack")["version"] == "2"
