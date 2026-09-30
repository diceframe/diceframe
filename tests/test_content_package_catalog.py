"""PR H canonical package resources stay in the read-only catalog."""

from __future__ import annotations

import json

from src.content_modules.refs import CONTENT_KIND_REGISTRY
from src.plugin_host.host import PluginHost


def test_catalog_exposes_world_lorebook_and_adventure_resources_without_materializing(tmp_path) -> None:
    plugins = tmp_path / "plugins"
    package = plugins / "catalog-pack"
    for relative in ("content/worlds", "content/lorebooks", "content/adventures"):
        (package / relative).mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "content_schema_version": 2,
        "id": "catalog-pack",
        "name": "Catalog Pack",
        "version": "1.0.0",
        "plugin_type": "content-pack",
        "contributes": {
            "worlds": ["content/worlds/*.json"],
            "lorebooks": ["content/lorebooks/*.json"],
            "adventure_resources": ["content/adventures/*.json"],
        },
    }
    (package / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package / "config.schema.json").write_text(
        json.dumps({"type": "object", "properties": {}}), encoding="utf-8",
    )
    config_dir = tmp_path / "data" / "catalog-pack"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"enabled": True}), encoding="utf-8",
    )
    (package / "content/worlds/harbor.json").write_text(
        json.dumps({"id": "harbor", "name": "Harbor World", "default_rule": "freeform"}),
        encoding="utf-8",
    )
    (package / "content/lorebooks/harbor.json").write_text(
        json.dumps({"id": "harbor", "name": "Harbor Lore", "entries": []}),
        encoding="utf-8",
    )
    (package / "content/adventures/intro.json").write_text(
        json.dumps({"id": "intro", "name": "Introduction"}),
        encoding="utf-8",
    )

    host = PluginHost(plugins, tmp_path / "data")
    host.discover()
    resources = host.list_content_resources()

    assert CONTENT_KIND_REGISTRY.supports("world")
    assert CONTENT_KIND_REGISTRY.supports("lorebook")
    assert CONTENT_KIND_REGISTRY.supports("adventure")
    assert resources["world"][0]["id"] == "harbor"
    assert resources["lorebook"][0]["name"] == "Harbor Lore"
    assert resources["adventure"][0]["id"] == "intro"
    assert resources["lorebook"][0]["readonly"] is True
