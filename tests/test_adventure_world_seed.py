"""World seed 测试（ADV2-03，母方案 §119 / §27）。

覆盖：v2 冒险声明四类种子（entity/relation/fact/process）→ 物化后世界容器
状态正确、幂等重放、种子上的未知字段 fail closed（load 时）、坏种子条目
（缺 key / 缺 endpoint）load 时拒绝。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.adventures.bundle import AdventureBundleError, AdventureBundleLoader
from src.adventures.graph_v2 import ADVENTURE_GRAPH_FORMAT_V2, AdventureGraphV2Error
from src.engine.game_instance import GameInstance
from src.engine.world.materialization import materialize_world_seed
from src.engine.world_state import (
    fact_value,
    fact_visibility,
    world_entities,
    world_processes,
    world_relations,
)


def _node(node_id: str, node_type: str = "scene", **overrides: object) -> dict:
    node: dict = {"id": node_id, "type": node_type, "transitions": []}
    node.update(overrides)
    return node


def _v2_adventure_record(**seed: object) -> dict:
    record: dict = {
        "schema_version": 1,
        "kind": "adventure",
        "id": "lanterns_of_greymoor",
        "source_ref": "gm:story",
        "automation_level": "guided",
        "format": ADVENTURE_GRAPH_FORMAT_V2,
        "chapters": [{"id": "ch1", "name": "第一章"}],
        "nodes": [_node("gate", "scene", chapter_id="ch1")],
        "objectives": [],
        "milestones": [],
        "start_node_ids": ["gate"],
    }
    record.update(seed)
    return record


def _seeded_record() -> dict:
    return _v2_adventure_record(world_seed={
        "entities": [
            {"entity_id": "npc:count", "kind": "npc", "source_ref": "gm:story"},
            {"entity_id": "location:castle", "kind": "location"},
        ],
        "relations": [
            {"relation_id": "rel:count-castle", "kind": "located_at",
             "from_ref": "npc:count", "to_ref": "location:castle"},
        ],
        "facts": [
            {"key": "location:castle.passable", "value": True},
            {"key": "gm:treasure.spot", "value": "cellar", "visibility": "gm"},
        ],
        "processes": [
            {"process_id": "process:haunting", "kind": "haunting", "visibility": "gm"},
        ],
    })


def _make_v2_bundle(tmp_path: Path, record: dict) -> AdventureBundleLoader:
    source = Path("templates/adventures/lanterns_of_greymoor")
    package = tmp_path / "seeded-quest"
    shutil.copytree(source, package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format"] = ADVENTURE_GRAPH_FORMAT_V2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # v2 复用 "chapters" 键（不同 shape），只能删 v1 独有键。
    for v1_field in ("steps", "choices", "start_step_id"):
        record.pop(v1_field, None)
    (package / "adventure.json").write_text(json.dumps(record), encoding="utf-8")
    return AdventureBundleLoader(tmp_path)


def test_v2_seed_materializes_all_four_kinds(tmp_path: Path) -> None:
    loader = _make_v2_bundle(tmp_path, _seeded_record())
    bundle = loader.load("seeded-quest", "zh-CN")
    instance = GameInstance(game_key=("web", "adv2-seed", "bot"))

    receipt = materialize_world_seed(instance, bundle)

    assert "npc:count" in world_entities(instance.world_state)
    assert "location:castle" in world_entities(instance.world_state)
    assert world_relations(instance.world_state)["rel:count-castle"]["status"] == "active"
    assert fact_value(instance.world_state, "location:castle.passable") is True
    # GM 私密种子正常入容器，但标记为 gm：秘密保护在投影层（§79）。
    assert fact_value(instance.world_state, "gm:treasure.spot") == "cellar"
    assert fact_visibility(instance.world_state, "gm:treasure.spot") == "gm"
    assert world_processes(instance.world_state)["process:haunting"]["status"] == "running"
    assert set(receipt["created_entity_ids"]) >= {"npc:count", "location:castle"}

    # 幂等重放：不再创建。
    again = materialize_world_seed(instance, bundle)
    assert again["created_entity_ids"] == []


def test_seed_without_world_seed_still_materializes_bundle_entities(tmp_path: Path) -> None:
    loader = _make_v2_bundle(tmp_path, _v2_adventure_record())
    bundle = loader.load("seeded-quest", "zh-CN")
    instance = GameInstance(game_key=("web", "adv2-seed2", "bot"))
    materialize_world_seed(instance, bundle)
    # bundle 自带的 npc/map_location 实体照常物化（v1 实体来源）。
    assert any(key.startswith("npc:") for key in world_entities(instance.world_state))


def test_bad_seed_fails_at_load_time(tmp_path: Path) -> None:
    record = _seeded_record()
    record["world_seed"]["facts"].append({"value": 1})  # 缺 key
    loader = _make_v2_bundle(tmp_path, record)
    with pytest.raises(AdventureGraphV2Error, match="needs key and value"):
        loader.load("seeded-quest", "zh-CN")


def test_seed_unknown_field_fails_at_load_time(tmp_path: Path) -> None:
    record = _seeded_record()
    record["world_seed"]["entities"][0]["hp"] = 30  # 机制字段进种子 → 拒绝
    loader = _make_v2_bundle(tmp_path, record)
    with pytest.raises(AdventureGraphV2Error, match="unknown field"):
        loader.load("seeded-quest", "zh-CN")


def test_seed_with_mechanics_like_fact_value_is_rejected_by_write_path(tmp_path: Path) -> None:
    record = _seeded_record()
    record["world_seed"]["facts"] = [
        {"key": "npc:count.stats", "value": {"hp": 30}},  # 非标量：写入口拒绝
    ]
    loader = _make_v2_bundle(tmp_path, record)
    bundle = loader.load("seeded-quest", "zh-CN")
    instance = GameInstance(game_key=("web", "adv2-seed3", "bot"))
    with pytest.raises(Exception):
        materialize_world_seed(instance, bundle)
