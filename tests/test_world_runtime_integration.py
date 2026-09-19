"""FIX-05 验收：World Runtime 最终集成（施工单 §7）。

覆盖：

```text
§7.1 依赖方向：Adventure → 世界的桥搬到 application adapter，
     src.engine.world* 不再 import src.adventures（architecture guard 强制）
§7.2 architecture guard：world runtime 不依赖 adventures/rulesets/webui/plugin_host；
     Adventure 只能用只读投影（src.engine.world.read），不得 import authority
§7.3 AI Context：World Prompt = facts + clock → 有界 viewer-safe 投影
     （facts + 相关实体 / 关系 / 进程 + clock），玩家视角不泄漏 gm 可见性
§7.4 Memory recall viewer policy：GM → public+gm；玩家安全面 → 仅非 gm 私有
§7.5 Memory 内容：权威记忆保留确定性语义（kind/subject/status/revision/event id）
```

修复前：``src.engine.world.materialization`` 直接 import ``src.adventures``（World
Runtime 认识 Adventure）；``gates.py`` 直接 import 世界 authority；投影只带 facts +
clock；记忆读取完全没有可见性过滤；权威记忆只存 ``kind @ evt``。
"""

from __future__ import annotations

import asyncio
import ast
import json
import shutil
from pathlib import Path

from src.adventures.bundle import AdventureBundleLoader
from src.adventures.graph_v2 import ADVENTURE_GRAPH_FORMAT_V2
from src.engine.game_instance import GameInstance
from src.engine.world.read import (
    MAX_PROJECTED_ENTITIES,
    project_visible_state,
)
from src.engine.world_state import apply_world_ops, fresh_world_state
from src.memory.delta import MemoryStore
from src.memory.recall import recall_best
from src.webui.services.adventure_materialization import materialize_world_seed

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _instance(game_key: str = "wr-integration") -> GameInstance:
    return GameInstance(game_key=("web", game_key, "bot"), language="zh-CN")


def _world_instance() -> GameInstance:
    instance = _instance()
    apply_world_ops(instance, [
        {"op": "set_fact", "key": "location:bridge.passable", "value": False},
        {"op": "set_fact", "key": "gm:ritual", "value": True, "visibility": "gm"},
        {"op": "register_entity", "entity_id": "location:bridge", "kind": "location"},
        {"op": "register_entity", "entity_id": "npc:keeper", "kind": "npc"},
        {"op": "register_entity", "entity_id": "gm:shade", "kind": "npc",
         "visibility": "gm"},
        {"op": "add_relation", "relation_id": "rel:keeper-bridge", "kind": "located_at",
         "from_ref": "npc:keeper", "to_ref": "location:bridge"},
        {"op": "start_process", "process_id": "ritual", "kind": "secret_ritual",
         "participants": ["npc:keeper"], "location": "location:bridge",
         "due_at": {"day": 2, "minute": 0}, "visibility": "gm"},
    ])
    return instance


# ---- §7.1 / §7.2 dependency direction --------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    package = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                name = ".".join([*base, node.module]) if node.module else ".".join(base)
            else:
                name = node.module or ""
            if name:
                modules.add(name)
    return modules


def test_world_runtime_never_imports_the_adventure_definition_layer() -> None:
    """§7.1：World Runtime 不认识 Adventure（物化桥在 application seam）。"""

    world_files = sorted((SRC / "engine" / "world").rglob("*.py")) + [
        SRC / "engine" / "world_state.py",
        SRC / "engine" / "world_events.py",
        SRC / "engine" / "world_legality.py",
    ]
    violations = [
        f"{path.relative_to(ROOT)} imports {module}"
        for path in world_files
        if path.exists()
        for module in sorted(_imported_modules(path))
        if module == "src.adventures" or module.startswith("src.adventures.")
    ]

    assert not violations, "\n".join(violations)
    # 物化桥必须在 application seam（webui service），而不是 engine。
    assert not (SRC / "engine" / "world" / "materialization.py").exists()
    assert (SRC / "webui" / "services" / "adventure_materialization.py").is_file()


def test_adventure_layer_uses_only_the_world_read_projection() -> None:
    """§7.2：定义层可 import world.contracts / world.read，不得 import authority。"""

    authority = (
        "src.engine.world_state", "src.engine.world_events",
        "src.engine.world_legality", "src.engine.world.ops",
    )
    violations = []
    for path in sorted((SRC / "adventures").rglob("*.py")):
        for module in sorted(_imported_modules(path)):
            if any(module == item or module.startswith(item + ".") for item in authority):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")

    assert not violations, "\n".join(violations)


# ---- §7.3 bounded viewer-safe projection -----------------------------------


def test_projection_keeps_gm_visibility_out_of_the_player_view() -> None:
    instance = _world_instance()

    player = project_visible_state(instance, viewer_uid="p1", viewer_is_gm=False)
    gm = project_visible_state(instance, viewer_is_gm=True)

    assert "location:bridge.passable" in player["facts"]
    assert "gm:ritual" not in player["facts"]
    assert "gm:ritual" in gm["facts"]
    assert "gm:shade" not in player["entities"]
    assert "gm:shade" in gm["entities"]
    assert "ritual" not in player["processes"]
    assert "ritual" in gm["processes"]
    assert player["viewer"] == "player:p1"
    assert gm["viewer"] == "gm"


def test_projection_is_scoped_to_the_relevant_slice() -> None:
    instance = _world_instance()

    projection = project_visible_state(
        instance, viewer_is_gm=True, location="location:bridge",
        participants=["npc:keeper"],
    )

    # 相关实体 = 当前地点（经 located_at 拓扑）+ 在场角色；远处的 gm:shade 不进来。
    assert set(projection["entities"]) == {"location:bridge", "npc:keeper"}
    assert "rel:keeper-bridge" in projection["relations"]
    # 进程按 location 相关（process 记录带 location 字段）。
    assert "ritual" in projection["processes"]
    assert projection["truncated"] is False


def test_player_projection_does_not_leak_gm_topology_or_processes() -> None:
    instance = _instance()
    apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": "location:bridge", "kind": "location"},
        {"op": "register_entity", "entity_id": "npc:keeper", "kind": "npc"},
        {"op": "add_relation", "relation_id": "rel:secret", "kind": "located_at",
         "from_ref": "npc:keeper", "to_ref": "location:bridge", "visibility": "gm"},
        {"op": "start_process", "process_id": "secret", "kind": "ritual",
         "participants": ["npc:keeper"], "location": "location:bridge",
         "due_at": {"day": 2, "minute": 0}, "visibility": "gm"},
    ])

    player = project_visible_state(instance, viewer_is_gm=False)

    assert "rel:secret" not in player["relations"]
    assert "secret" not in player["processes"]
    # 关系被隐藏时，玩家视角也不会因该关系而"看见"端点以外的记录。
    assert "npc:keeper" in player["entities"]


def test_projection_bounds_large_worlds_and_marks_truncation() -> None:
    instance = _instance()
    apply_world_ops(instance, [
        {"op": "register_entity", "entity_id": f"npc:unit_{index}", "kind": "npc"}
        for index in range(MAX_PROJECTED_ENTITIES + 5)
    ])

    projection = project_visible_state(instance, viewer_is_gm=True)

    assert len(projection["entities"]) == MAX_PROJECTED_ENTITIES
    assert projection["truncated"] is True


def test_world_prompt_consumes_entities_relations_and_processes() -> None:
    from src.llm.world_prompt import format_world_state_block

    instance = _world_instance()

    text = format_world_state_block(
        instance, viewer_is_gm=True, location="location:bridge",
        participants=["npc:keeper"],
    )

    assert "世界真相" in text
    assert "npc:keeper" in text
    assert "rel:keeper-bridge" in text
    assert "ritual" in text
    # 玩家视角不出现 gm 私有事实/实体/进程。
    player_text = format_world_state_block(
        instance, viewer_is_gm=False, location="location:bridge",
        participants=["npc:keeper"],
    )
    assert "gm:ritual" not in player_text
    assert "gm:shade" not in player_text
    assert "ritual" not in player_text


def test_world_prompt_is_empty_for_an_unestablished_world() -> None:
    from src.llm.world_prompt import format_world_state_block

    assert format_world_state_block(_instance(), viewer_is_gm=True) == ""


# ---- §7.4 memory recall viewer policy --------------------------------------


def _store(tmp_path: Path) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory.db")
    store.open()
    return store


def _seed_memory(store: MemoryStore, game_key: str) -> None:
    asyncio.run(store.apply_delta(game_key, {
        "add": [
            {"entity": "npc:keeper", "relation": "knows", "value": "守桥人"},
            {"entity": "gm:ritual", "relation": "knows", "value": "秘密仪式"},
        ],
        "memory_kind": "authoritative_world",
        "source_kind": "worldevent",
        "source_id": "evt:000001:0:entity_registered",
        "world_revision": 3,
        "visibility": "public",
    }, 1))
    asyncio.run(store.apply_delta(game_key, {
        "add": [
            {"entity": "gm:shade", "relation": "knows", "value": "幕后黑手"},
        ],
        "memory_kind": "authoritative_world",
        "source_kind": "worldevent",
        "source_id": "evt:000002:0:entity_registered",
        "world_revision": 4,
        "visibility": "gm",
    }, 2))


def test_memory_recall_viewer_policy_hides_gm_only_entries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed_memory(store, "gk")

        gm_entries = store.list_entries("gk", viewer_is_gm=True)
        player_entries = store.list_entries("gk", viewer_is_gm=False)

        assert {entry["entity"] for entry in gm_entries} == {
            "npc:keeper", "gm:ritual", "gm:shade",
        }
        assert {entry["entity"] for entry in player_entries} == {"npc:keeper", "gm:ritual"}
        assert store.count_entries("gk", viewer_is_gm=True) == 3
        assert store.count_entries("gk", viewer_is_gm=False) == 2
        assert store.recall("gk", ["gm:shade"], viewer_is_gm=False) == []
        assert [entry["entity"] for entry in store.recall(
            "gk", ["gm:shade"], viewer_is_gm=True,
        )] == ["gm:shade"]
    finally:
        store.close()


def test_player_safe_recall_never_returns_gm_memories(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed_memory(store, "gk")

        query = "gm:shade npc:keeper 守桥人 幕后黑手"
        player_rows = asyncio.run(
            recall_best(store, "gk", query, viewer_is_gm=False)
        )
        gm_rows = asyncio.run(recall_best(store, "gk", query, viewer_is_gm=True))

        assert player_rows, "玩家安全召回不应整体为空"
        assert all(entry["entity"] != "gm:shade" for entry in player_rows)
        assert any(entry["entity"] == "gm:shade" for entry in gm_rows)
    finally:
        store.close()


# ---- §7.5 authoritative memory content -------------------------------------


def test_authoritative_memory_keeps_deterministic_semantics() -> None:
    from src.engine.world.memory_projection import world_memory_delta

    delta = world_memory_delta({
        "event_id": "evt:000007:0:process_settled",
        "kind": "process_settled",
        "revision": 7,
        "clock": {"day": 2, "minute": 0},
        "source_round": 3,
        "visibility": "public",
        "subject": "ritual",
        "summary": "status=completed",
    })

    assert delta is not None
    value = delta["add"][0]["value"]
    # 事件种类 / 主体 / 状态 / revision / event id 全部保留（不是 "kind @ evt"）。
    for fragment in ("process_settled", "ritual", "status=completed", "rev 7",
                     "evt:000007:0:process_settled"):
        assert fragment in value
    assert delta["world_revision"] == 7
    assert delta["source_id"] == "evt:000007:0:process_settled"


def test_seeded_memory_round_trips_through_the_store(tmp_path: Path) -> None:
    from src.engine.world.memory_projection import world_memory_delta

    store = _store(tmp_path)
    try:
        delta = world_memory_delta({
            "event_id": "evt:000009:0:relation_status_changed",
            "kind": "relation_status_changed",
            "revision": 9,
            "clock": {"day": 1, "minute": 30},
            "source_round": 2,
            "visibility": "public",
            "subject": "rel:harbor",
            "summary": "kind=allied_with status=severed",
        })
        asyncio.run(store.apply_delta("gk", delta, 2))

        rows = store.recall("gk", ["rel:harbor"], viewer_is_gm=True)

        assert len(rows) == 1
        assert "kind=allied_with status=severed" in rows[0]["value"]
        assert rows[0]["memory_kind"] == "authoritative_world"
        assert rows[0]["world_revision"] == 9
        assert json.dumps(rows[0], ensure_ascii=False)
    finally:
        store.close()


# ---- materialization still works from the application seam -----------------


def _v2_seeded_bundle(tmp_path: Path) -> AdventureBundleLoader:
    """Reuse the real bundled template package (never invent manifest fields)."""

    source = Path("templates/adventures/lanterns_of_greymoor")
    package = tmp_path / "seeded-quest"
    shutil.copytree(source, package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format"] = ADVENTURE_GRAPH_FORMAT_V2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    record = json.loads(
        (package / "adventure.json").read_text(encoding="utf-8")
    )
    for v1_field in ("steps", "choices", "start_step_id"):
        record.pop(v1_field, None)
    record["format"] = ADVENTURE_GRAPH_FORMAT_V2
    record["nodes"] = [{
        "id": "gate", "type": "scene", "chapter_id": "ch1", "transitions": [],
    }]
    record["objectives"] = []
    record["milestones"] = []
    record["start_node_ids"] = ["gate"]
    record["chapters"] = [{"id": "ch1", "name": "第一章"}]
    record["world_seed"] = {
        "entities": [{"entity_id": "location:hall", "kind": "location"}],
        "relations": [], "facts": [], "processes": [],
    }
    (package / "adventure.json").write_text(json.dumps(record), encoding="utf-8")
    return AdventureBundleLoader(tmp_path)


def test_world_seed_materialization_is_atomic_from_the_application_seam(
    tmp_path: Path,
) -> None:
    """§7.1 搬家后的适配器保持 §6.6 的原子语义。"""

    loader = _v2_seeded_bundle(tmp_path)
    bundle = loader.load("seeded-quest", "zh-CN")
    instance = _instance("wr-seed")
    before = fresh_world_state()

    receipt = materialize_world_seed(instance, bundle)

    assert "location:hall" in (instance.world_state.get("entities") or {})
    assert receipt["adventure_id"] == bundle.manifest.adventure_id
    assert before["revision"] == 0 and instance.world_state["revision"] == 1
    # 幂等重放：不重复创建、不推进 revision。
    again = materialize_world_seed(instance, bundle)
    assert again["created_entity_ids"] == []
