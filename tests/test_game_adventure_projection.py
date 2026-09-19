"""Game-scoped adventure projection keeps GM content out of player HTTP data."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from src.adventures.bundle import AdventureBundleLoader
from src.adventures.graph_v2 import ADVENTURE_GRAPH_FORMAT_V2
from src.rulesets.builtin import default_adventure_runtime_requirement
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services import adventures


def _dependencies(tmp_path: Path) -> tuple[adventures.AdventureDependencies, AdventureBundleLoader]:
    source = Path("templates/adventures/lanterns_of_greymoor")
    package = tmp_path / "castle-quest"
    shutil.copytree(source, package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format"] = ADVENTURE_GRAPH_FORMAT_V2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    adventure_path = package / "adventure.json"
    graph = json.loads(adventure_path.read_text(encoding="utf-8"))
    for key in ("steps", "choices", "start_step_id"):
        graph.pop(key, None)
    graph.update({
        "chapters": [
            {"id": "public", "name": "Public chapter", "visibility": "public"},
            {"id": "secret", "name": "GM secret", "visibility": "gm"},
        ],
        "nodes": [
            {
                "id": "gate", "type": "scene", "chapter_id": "public",
                "name": "Gate", "transitions": [{"to": "ritual"}],
            },
            {
                "id": "ritual", "type": "scene", "chapter_id": "secret",
                "visibility": "gm", "name": "Secret ritual", "transitions": [],
            },
        ],
        "objectives": [
            {"id": "find-key", "name": "Find the key", "node_ids": ["gate"]},
            {
                "id": "gm-plot", "name": "Secret plot", "visibility": "gm",
                "node_ids": ["ritual"],
            },
        ],
        "milestones": [],
        "start_node_ids": ["gate"],
        "visibility": "public",
    })
    adventure_path.write_text(json.dumps(graph), encoding="utf-8")
    loader = AdventureBundleLoader(tmp_path)
    dependencies = adventures.AdventureDependencies(
        adventure_loader=loader,
        list_instances=lambda: [],
        load_rule_by_id=lambda _rule_id, _language: None,
        ruleset_registry=RulesetRuntimeRegistry(),
        default_runtime_requirement=default_adventure_runtime_requirement,
    )
    return dependencies, loader


def test_player_game_projection_never_exposes_bound_v2_secrets(tmp_path: Path) -> None:
    dependencies, loader = _dependencies(tmp_path)
    bundle = loader.resolve("core:lanterns_of_greymoor", "zh-CN")
    instance = SimpleNamespace(
        adventure_binding=bundle.binding("greymoor"), language="zh-CN", world_id="greymoor",
    )

    player = adventures.game_adventure_projection(
        dependencies, instance, viewer_is_gm=False,
    )
    rendered = json.dumps(player, ensure_ascii=False)

    assert player["adventure"]["available"] is True
    assert "Secret ritual" not in rendered
    assert "gm-plot" not in rendered
    assert "ritual" not in rendered
    gate = player["adventure"]["projection"]["nodes"][0]
    assert gate["transitions"] == []


def test_gm_game_projection_receives_complete_bound_v2_graph(tmp_path: Path) -> None:
    dependencies, loader = _dependencies(tmp_path)
    bundle = loader.resolve("core:lanterns_of_greymoor", "zh-CN")
    instance = SimpleNamespace(
        adventure_binding=bundle.binding("greymoor"), language="zh-CN", world_id="greymoor",
    )

    gm = adventures.game_adventure_projection(dependencies, instance, viewer_is_gm=True)

    assert {node["id"] for node in gm["adventure"]["projection"]["nodes"]} == {"gate", "ritual"}


def test_changed_bound_package_is_not_projected(tmp_path: Path) -> None:
    dependencies, loader = _dependencies(tmp_path)
    bundle = loader.resolve("core:lanterns_of_greymoor", "zh-CN")
    binding = bundle.binding("greymoor")
    binding["content_digest"] = "different"
    instance = SimpleNamespace(adventure_binding=binding, language="zh-CN", world_id="greymoor")

    projection = adventures.game_adventure_projection(dependencies, instance, viewer_is_gm=True)

    assert projection["adventure"] == {
        "binding": {key: binding[key] for key in (
            "adventure_id", "version", "format", "content_digest",
        )},
        "available": False,
        "reason": "binding_changed",
    }
