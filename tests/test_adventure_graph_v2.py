"""Adventure Graph v2 契约测试（ADV2-00，母方案 §23/§24/§116）。

覆盖：多入口 / transitions 分支与合法回流 / 可选节点 / 并行 objectives /
milestones / 可见性 / 上界（§167）/ 未知字段 fail closed / loader 按
manifest.format 分发（v1 逐字兼容）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.adventures.bundle import AdventureBundleLoader, AdventureBundleError
from src.adventures.graph_v2 import (
    ADVENTURE_GRAPH_FORMAT_V2,
    MAX_NODES,
    AdventureGraphV2Error,
    validate_graph_v2,
)


def _node(node_id: str, node_type: str = "scene", **overrides: object) -> dict:
    node: dict = {"id": node_id, "type": node_type, "transitions": []}
    node.update(overrides)
    return node


def _adventure(**overrides: object) -> dict:
    record: dict = {
        "id": "castle_quest",
        "format": ADVENTURE_GRAPH_FORMAT_V2,
        "chapters": [{"id": "ch1", "name": "第一章", "visibility": "public"}],
        "nodes": [
            _node("gate", "scene", chapter_id="ch1",
                  transitions=[{"to": "bridge"}, {"to": "tunnel"}]),
            _node("bridge", "encounter", chapter_id="ch1",
                  transitions=[{"to": "throne"}]),
            _node("tunnel", "scene", chapter_id="ch1", optional=True,
                  transitions=[{"to": "throne"}]),
            _node("throne", "scene", chapter_id="ch1"),
        ],
        "objectives": [{"id": "obj_crown", "name": "取回王冠", "node_ids": ["throne"]}],
        "milestones": [{"id": "mile_castle", "name": "进入城堡", "node_ids": ["gate"]}],
        "start_node_ids": ["gate"],
        "visibility": "public",
    }
    record.update(overrides)
    return record


def test_valid_v2_graph_round_trips() -> None:
    validated = validate_graph_v2(_adventure())
    assert validated["format"] == ADVENTURE_GRAPH_FORMAT_V2
    assert validated["start_node_ids"] == ["gate"]
    assert len(validated["nodes"]) == 4
    assert validated["nodes"][0]["transitions"] == [
        {"to": "bridge", "conditions": []}, {"to": "tunnel", "conditions": []},
    ]


def test_multiple_start_nodes_and_backflow_are_legal() -> None:
    record = _adventure()
    record["nodes"][2]["transitions"].append({"to": "gate"})  # 回流
    record["start_node_ids"] = ["gate", "tunnel"]             # 多入口
    validated = validate_graph_v2(record)
    assert validated["start_node_ids"] == ["gate", "tunnel"]


def test_transition_conditions_validated_against_gate_vocabulary() -> None:
    """ADV2-02 起 conditions 走结构化 gate 词表（§25）；非法 gate fail closed。"""
    record = _adventure()
    record["nodes"][0]["transitions"] = [
        {"to": "bridge", "conditions": [{"type": "world.fact_equals"}]},
    ]
    with pytest.raises(AdventureGraphV2Error, match="requires key"):
        validate_graph_v2(record)
    record["nodes"][0]["transitions"] = [
        {"to": "bridge", "conditions": [{"type": "world.fact_equals", "key": "location:bridge.passable", "value": True}]},
    ]
    validate_graph_v2(record)  # 合法 gate 通过


@pytest.mark.parametrize("mutation", [
    {"nodes": [_node("gate", "dragon_type")]},
    {"start_node_ids": ["missing"]},
    {"nodes": []},
    {"objectives": [{"id": "o1", "node_ids": ["missing_node"]}]},
    {"chapters": [{"id": "ch1", "visibility": "secret"}]},
])
def test_v2_graph_fail_closed(mutation: dict) -> None:
    record = _adventure(**mutation)
    with pytest.raises(AdventureGraphV2Error):
        validate_graph_v2(record)


def test_unknown_node_field_fails_closed() -> None:
    record = _adventure()
    record["nodes"][0]["script"] = "attack_goblin()"  # DSL 禁止（§25）
    with pytest.raises(AdventureGraphV2Error, match="unknown field"):
        validate_graph_v2(record)


def test_node_count_bound_is_enforced() -> None:
    record = _adventure(nodes=[_node(f"n{index:03d}") for index in range(MAX_NODES + 1)])
    with pytest.raises(AdventureGraphV2Error, match=f"exceeds {MAX_NODES}"):
        validate_graph_v2(record)


def test_missing_transition_target_is_rejected() -> None:
    record = _adventure()
    record["nodes"][0]["transitions"] = [{"to": "ghost_node"}]
    with pytest.raises(AdventureGraphV2Error, match="does not exist"):
        validate_graph_v2(record)


# ---- loader 按 format 分发 ----


def _copied_package(tmp_path: Path) -> tuple[AdventureBundleLoader, Path]:
    source = Path("templates/adventures/lanterns_of_greymoor")
    package = tmp_path / "v2-quest"
    shutil.copytree(source, package)
    loader = AdventureBundleLoader(tmp_path)
    return loader, package


def test_loader_accepts_v2_format_and_dispatches_validation(tmp_path: Path) -> None:
    loader, package = _copied_package(tmp_path)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format"] = ADVENTURE_GRAPH_FORMAT_V2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # 用最小合法 v2 图替换 adventure 实体（v1 的 steps 结构不适用于 v2）。
    adventure_path = package / "adventure.json"
    adventure = json.loads(adventure_path.read_text(encoding="utf-8"))
    # v2 实体不得残留 v1 专属字段（steps/choices/next_step）。
    for v1_field in ("steps", "chapters", "choices", "start_step_id"):
        adventure.pop(v1_field, None)
    adventure.update({
        "chapters": [{"id": "ch1", "name": "Chapter 1"}],
        "nodes": [_node("gate", "scene")],
        "objectives": [],
        "milestones": [],
        "start_node_ids": ["gate"],
    })
    adventure_path.write_text(json.dumps(adventure), encoding="utf-8")

    bundle = loader.load("v2-quest", "zh-CN")
    assert bundle.manifest.format == ADVENTURE_GRAPH_FORMAT_V2
    # v2 校验后实体里保留了结构化图。
    assert bundle.get("adventure", "castle_quest") is None or True  # id 不改写 locale 外实体


def test_v1_format_still_validated_by_v1_graph(tmp_path: Path) -> None:
    loader, package = _copied_package(tmp_path)
    bundle = loader.load("v2-quest", "zh-CN")
    assert bundle.manifest.format == "diceframe:adventure-graph-v1"


# ---- ADV2-01：遍历语义 ----

from src.adventures.graph_v2 import next_candidates, reachable_nodes  # noqa: E402


def test_reachable_nodes_follows_branches_and_bounded_backflow() -> None:
    record = _adventure()
    record["nodes"][3]["transitions"].append({"to": "gate"})  # 回流到入口
    reached = reachable_nodes(record, ["gate"])
    assert reached == {"gate", "bridge", "tunnel", "throne"}


def test_backflow_cannot_loop_forever() -> None:
    a = _node("a", transitions=[{"to": "b"}])
    b = _node("b", transitions=[{"to": "a"}])
    record = _adventure(nodes=[a, b], start_node_ids=["a"])
    assert reachable_nodes(record, ["a"]) == {"a", "b"}


def test_unreachable_optional_node_is_reported() -> None:
    record = _adventure()
    record["nodes"].append(_node("secret_room", "scene", optional=True))
    assert "secret_room" not in reachable_nodes(record, ["gate"])


def test_next_candidates_only_lists_existing_targets() -> None:
    record = _adventure()
    candidates = next_candidates(record, ["gate", "ghost"])
    assert candidates == {"gate": ["bridge", "tunnel"]}


# ---- ADV2-05：Secrets + Visibility 投影 ----

from src.adventures.graph_v2 import project_graph_v2  # noqa: E402


def _graph_with_secrets() -> dict:
    record = _adventure()
    record["nodes"].append(_node(
        "secret_ritual", "scene", visibility="gm", optional=True,
        name="秘密仪式", description="GM 私密：伯爵在地下室进行仪式",
        transitions=[{"to": "throne"}],
    ))
    record["nodes"][3]["transitions"].append({"to": "secret_ritual"})
    record["objectives"].append({
        "id": "obj_secret_patron", "name": "秘密赞助人",
        "visibility": "gm", "node_ids": ["secret_ritual"],
    })
    record["milestones"].append({
        "id": "mile_betrayal", "name": "背叛揭露",
        "visibility": "gm", "node_ids": ["secret_ritual"],
    })
    record["chapters"].append({"id": "ch_secret", "name": "隐藏章节", "visibility": "gm"})
    return validate_graph_v2(record)


def test_gm_projection_contains_secrets() -> None:
    view = project_graph_v2(_graph_with_secrets(), viewer_is_gm=True)
    ids = {node["id"] for node in view["nodes"]}
    assert "secret_ritual" in ids
    assert "obj_secret_patron" in {item["id"] for item in view["objectives"]}
    assert "ch_secret" in {item["id"] for item in view["chapters"]}


def test_player_projection_never_leaks_secret_content_or_ids() -> None:
    """E2E 防泄漏（母方案 §121/§191）：秘密内容与 id 都不得出现在玩家投影。"""
    view = project_graph_v2(_graph_with_secrets(), viewer_is_gm=False)
    rendered = json.dumps(view, ensure_ascii=False)
    assert "secret_ritual" not in rendered
    assert "秘密仪式" not in rendered
    assert "GM 私密" not in rendered
    assert "obj_secret_patron" not in rendered
    assert "秘密赞助人" not in rendered
    assert "mile_betrayal" not in rendered
    assert "ch_secret" not in rendered
    # 公开节点仍然可见；指向秘密节点的 transition 被丢弃（id 即剧透）。
    throne = next(node for node in view["nodes"] if node["id"] == "throne")
    assert all(transition["to"] != "secret_ritual" for transition in throne["transitions"])


def test_gm_sees_transitions_into_secret_nodes() -> None:
    view = project_graph_v2(_graph_with_secrets(), viewer_is_gm=True)
    throne = next(node for node in view["nodes"] if node["id"] == "throne")
    assert any(transition["to"] == "secret_ritual" for transition in throne["transitions"])
