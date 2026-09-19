"""Adventure materialization seam 测试（WR-09，母方案 §27/§102）。

用真实内置 Bundle（lanterns_of_greymoor）走完整链路：loader 解析 → 纯推导
world_seed_ops（定义层不触碰 authority）→ engine 侧 materialize_world_seed
经唯一写入口物化 → 幂等重放 → 世界坐标与 provenance 校验。
"""

from __future__ import annotations

import pytest

from src.adventures.bundle import AdventureBundleLoader
from src.adventures.materialization import world_entity_id, world_seed_ops
from src.engine.game_instance import GameInstance
from src.webui.services.adventure_materialization import materialize_world_seed
from src.engine.world_state import world_entities


@pytest.fixture(scope="module")
def bundle():
    loader = AdventureBundleLoader("templates/adventures")
    return loader.load("lanterns_of_greymoor", locale="zh-CN")


def make_instance() -> GameInstance:
    return GameInstance(game_key=("web", "wr2-materialize", "bot"))


def test_seed_ops_cover_npcs_and_map_locations_with_traceable_source(bundle) -> None:
    ops = world_seed_ops(bundle)
    assert ops, "内置冒险应有初始种子"
    entity_ids = {op["entity_id"] for op in ops}
    # world entity id 用 canonical 前缀，且与 bundle 实体一一对应。
    assert any(entity_id.startswith("npc:") for entity_id in entity_ids)
    assert any(entity_id.startswith("location:") for entity_id in entity_ids)
    assert len(entity_ids) == len(ops)
    # provenance 可追溯到 adventure 包，且满足 world contracts 语法。
    assert all(op["source_ref"] == f"adventure:{bundle.manifest.adventure_id}" for op in ops)
    assert all(op["kind"] in ("npc", "location") for op in ops)
    # 机制数据不进 world：种子只有 register_entity。
    assert all(op["op"] == "register_entity" for op in ops)


def test_world_entity_id_maps_bundle_kinds() -> None:
    assert world_entity_id("npc", "mira") == "npc:mira"
    assert world_entity_id("map_location", "old_shrine") == "location:old_shrine"
    with pytest.raises(KeyError):
        world_entity_id("scene", "x")  # scene 不是世界实体


def test_materialize_creates_entities_through_the_authoritative_path(bundle) -> None:
    instance = make_instance()
    receipt = materialize_world_seed(instance, bundle, source_round=0)

    assert receipt["adventure_id"] == bundle.manifest.adventure_id
    assert receipt["content_digest"] == bundle.content_digest
    assert receipt["created_entity_ids"]
    assert receipt["skipped_entity_ids"] == []
    entities = world_entities(instance.world_state)
    assert set(receipt["created_entity_ids"]).issubset(set(entities))
    # 全部经唯一写入口：revision 有推进、来源轮次为 0。
    assert instance.world_state["revision"] >= 1
    first = entities[receipt["created_entity_ids"][0]]
    assert first["created_revision"] >= 1
    assert first["status"] == "active"


def test_materialize_is_idempotent_on_replay(bundle) -> None:
    instance = make_instance()
    first = materialize_world_seed(instance, bundle)
    revision_after_first = instance.world_state["revision"]

    second = materialize_world_seed(instance, bundle)
    assert second["created_entity_ids"] == []
    assert second["skipped_entity_ids"] == first["created_entity_ids"]
    # 幂等：重放不再推进世界。
    assert instance.world_state["revision"] == revision_after_first


def test_materialize_after_partial_loss_only_creates_missing(bundle) -> None:
    instance = make_instance()
    receipt = materialize_world_seed(instance, bundle)
    # 模拟部分丢失：抹掉一个已物化实体（例如经由后续 retire/回滚场景）。
    victim = receipt["created_entity_ids"][0]
    del instance.world_state["entities"][victim]

    again = materialize_world_seed(instance, bundle)
    assert again["created_entity_ids"] == [victim]
    assert set(again["skipped_entity_ids"]) == set(receipt["created_entity_ids"]) - {victim}


def test_seed_ops_are_pure_and_do_not_touch_the_instance(bundle) -> None:
    instance = make_instance()
    before = instance.world_state
    world_seed_ops(bundle)
    assert instance.world_state is before
    assert instance.world_state["revision"] == 0
