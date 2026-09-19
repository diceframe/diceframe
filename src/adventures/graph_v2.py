"""Adventure Graph v2 format contract (ADV2-00, 母方案 §23/§24/§116).

\`\`\`text
format = "diceframe:adventure-graph-v2"
\`\`\`

v2 面向大型 D&D 模组的图模型（相对 v1 的线性 steps/next_step）：

\`\`\`text
Adventure
├─ chapters[]
├─ nodes[]            scene / objective / encounter / decision / milestone / reference
├─ objectives[]       可并行目标
├─ milestones[]
└─ start_node_ids[]   多入口
\`\`\`

- 节点用 **transitions[]** 而不是单一 next_step_id：多分支 / 可选节点 /
  并行目标 / 合法回流（§24）；transition 条件（structured gates）在
  ADV2-02 定义，本契约只锁定形状（当前必须是空对象）。
- **不做万能脚本 DSL**（§25）：节点只能携带结构化数据与引用。
- 遍历安全（§77/§167）：节点数 / 每节点 transitions / refs 均有上界；
  引用必须存在；回流合法但结构仍受节点数上限约束。
- 可见性（§79）：node / objective / milestone 均带 public|gm，secret 在
  projection 层过滤（ADV2-05）。
- v1（diceframe:adventure-graph-v1）继续由既有 loader 校验，长期兼容（§71）。
"""

from __future__ import annotations

import re
from typing import Any

from src.adventures.gates import GateError, validate_gates

ADVENTURE_GRAPH_FORMAT_V2 = "diceframe:adventure-graph-v2"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_REF_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*:[a-z0-9][a-z0-9_.-]*$")
VISIBILITIES = ("public", "gm")
NODE_TYPES = ("scene", "objective", "encounter", "decision", "milestone", "reference")

# 上界（母方案 §167；基于 v1 bounds 的同量级制定）。
MAX_CHAPTERS = 32
MAX_NODES = 256
MAX_OBJECTIVES = 64
MAX_MILESTONES = 64
MAX_TRANSITIONS_PER_NODE = 16
MAX_REFS_PER_NODE = 16
MAX_START_NODES = 16


class AdventureGraphV2Error(ValueError):
    """A v2 adventure graph is invalid: fail closed."""


def _required_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise AdventureGraphV2Error(f"{label} is invalid: {value!r}")
    return text


def _bounded_list(value: Any, label: str, maximum: int) -> list:
    if not isinstance(value, list):
        raise AdventureGraphV2Error(f"{label} must be an array")
    if len(value) > maximum:
        raise AdventureGraphV2Error(f"{label} exceeds {maximum} entries")
    return value


def _validate_visibility(value: Any, label: str) -> str:
    if value not in VISIBILITIES:
        raise AdventureGraphV2Error(f"{label} visibility is invalid: {value!r}")
    return str(value)


def _validate_transition(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AdventureGraphV2Error(f"{label} transition must be an object")
    extra = sorted(set(raw) - {"to", "conditions"})
    if extra:
        raise AdventureGraphV2Error(f"{label} transition has unknown field: {extra[0]!r}")
    to_id = _required_id(raw.get("to"), f"{label} transition.to")
    try:
        conditions = validate_gates(raw.get("conditions", []), label=label)
    except GateError as exc:
        raise AdventureGraphV2Error(f"{label} {exc}") from exc
    return {"to": to_id, "conditions": conditions}


# 种子上界（母方案 §167）：与 world 容器上限同量级、收紧到冒险种子语义。
MAX_SEED_ENTITIES = 128
MAX_SEED_RELATIONS = 128
MAX_SEED_FACTS = 128
MAX_SEED_PROCESSES = 32


def _validate_world_seed(raw: Any) -> dict[str, Any]:
    """Validate the optional v2 world seed（结构性校验，母方案 §119）。

    种子条目就是 world ops 的**载荷字段**（去掉 op 名）：entity/relation/fact
    /process 四类；最终合法性由物化时的唯一写入口 apply_world_ops fail closed
    把关（process due_at 相对物化时刻的未来性等）。
    """

    if raw is None:
        return {"entities": [], "relations": [], "facts": [], "processes": []}
    if not isinstance(raw, dict):
        raise AdventureGraphV2Error("world_seed must be an object")
    extra = sorted(set(raw) - {"entities", "relations", "facts", "processes"})
    if extra:
        raise AdventureGraphV2Error(f"world_seed has unknown field: {extra[0]!r}")
    entities = _bounded_list(raw.get("entities", []), "world_seed.entities", MAX_SEED_ENTITIES)
    relations = _bounded_list(raw.get("relations", []), "world_seed.relations", MAX_SEED_RELATIONS)
    facts = _bounded_list(raw.get("facts", []), "world_seed.facts", MAX_SEED_FACTS)
    processes = _bounded_list(raw.get("processes", []), "world_seed.processes", MAX_SEED_PROCESSES)
    for entity in entities:
        if not isinstance(entity, dict) or not entity.get("entity_id") or not entity.get("kind"):
            raise AdventureGraphV2Error("world_seed entity needs entity_id and kind")
        extra = sorted(set(entity) - {"entity_id", "kind", "visibility", "source_ref"})
        if extra:
            raise AdventureGraphV2Error(f"world_seed entity has unknown field: {extra[0]!r}")
    for relation in relations:
        if not isinstance(relation, dict) or not all(
            relation.get(field) for field in ("relation_id", "kind", "from_ref", "to_ref")
        ):
            raise AdventureGraphV2Error(
                "world_seed relation needs relation_id/kind/from_ref/to_ref"
            )
        extra = sorted(set(relation) - {"relation_id", "kind", "from_ref", "to_ref", "visibility", "source_ref"})
        if extra:
            raise AdventureGraphV2Error(f"world_seed relation has unknown field: {extra[0]!r}")
    for fact in facts:
        if not isinstance(fact, dict) or not fact.get("key") or "value" not in fact:
            raise AdventureGraphV2Error("world_seed fact needs key and value")
        extra = sorted(set(fact) - {"key", "value", "visibility"})
        if extra:
            raise AdventureGraphV2Error(f"world_seed fact has unknown field: {extra[0]!r}")
    for process in processes:
        if not isinstance(process, dict) or not process.get("process_id") or not process.get("kind"):
            raise AdventureGraphV2Error("world_seed process needs process_id and kind")
        extra = sorted(
            set(process) - {"process_id", "kind", "participants", "location", "due_at", "visibility", "source_ref"}
        )
        if extra:
            raise AdventureGraphV2Error(f"world_seed process has unknown field: {extra[0]!r}")
    return {
        "entities": [dict(item) for item in entities],
        "relations": [dict(item) for item in relations],
        "facts": [dict(item) for item in facts],
        "processes": [dict(item) for item in processes],
    }


def validate_graph_v2(adventure: Any) -> dict[str, Any]:
    """Validate one v2 adventure record (entity kind ``adventure``) and return it.

    结构契约：多入口 start_node_ids、可选节点、并行 objectives、合法回流。
    引用完整性（node.to / chapter 归属 / encounter_ref 等）在此一并校验。
    """

    if not isinstance(adventure, dict):
        raise AdventureGraphV2Error("v2 adventure must be an object")
    allowed = {
        # 图字段（本契约拥有）。
        "chapters", "nodes", "objectives", "milestones",
        "start_node_ids", "visibility",
        # 实体 envelope / 展示字段（由 _entities 与 locale 层负责）。
        "id", "format", "name", "summary", "tutorial", "schema_version",
        "kind", "source_ref", "automation_level", "estimated_minutes",
        "recommended_level", "player_count", "world_policy",
        "recommended_world_id",
        # ADV2-03：初始世界种子（entity/relation/fact/process）。
        "world_seed",
    }
    extra = sorted(set(adventure) - allowed)
    if extra:
        raise AdventureGraphV2Error(f"v2 adventure has unknown field: {extra[0]!r}")

    adventure_id = _required_id(adventure.get("id"), "v2 adventure id")
    visibility = _validate_visibility(adventure.get("visibility", "public"), "adventure")

    # chapters：仅 id + name（成员关系由节点 chapter_id 表达，v2 不做双向
    # 强制清单——可选节点允许不属于任何章节）。
    chapters: dict[str, dict[str, Any]] = {}
    for raw in _bounded_list(adventure.get("chapters", []), "chapters", MAX_CHAPTERS):
        if not isinstance(raw, dict):
            raise AdventureGraphV2Error("chapter must be an object")
        chapter_id = _required_id(raw.get("id"), "chapter id")
        if chapter_id in chapters:
            raise AdventureGraphV2Error(f"duplicate chapter id: {chapter_id!r}")
        chapters[chapter_id] = {
            "id": chapter_id,
            "name": str(raw.get("name") or ""),
            "visibility": _validate_visibility(raw.get("visibility", "public"), f"chapter {chapter_id}"),
        }

    # nodes。
    nodes: dict[str, dict[str, Any]] = {}
    for raw in _bounded_list(adventure.get("nodes", []), "nodes", MAX_NODES):
        if not isinstance(raw, dict):
            raise AdventureGraphV2Error("node must be an object")
        node_allowed = {
            "id", "type", "chapter_id", "visibility", "optional",
            "transitions", "scene_ref", "npc_refs", "encounter_ref",
            "name", "description",
        }
        node_extra = sorted(set(raw) - node_allowed)
        if node_extra:
            raise AdventureGraphV2Error(f"v2 node has unknown field: {node_extra[0]!r}")
        node_id = _required_id(raw.get("id"), "node id")
        if node_id in nodes:
            raise AdventureGraphV2Error(f"duplicate node id: {node_id!r}")
        node_type = raw.get("type")
        if node_type not in NODE_TYPES:
            raise AdventureGraphV2Error(f"node type is invalid: {node_type!r}")
        chapter_id = raw.get("chapter_id")
        if chapter_id is not None:
            chapter_id = _required_id(chapter_id, f"node {node_id} chapter_id")
            if chapter_id not in chapters:
                raise AdventureGraphV2Error(
                    f"node {node_id!r} references unknown chapter: {chapter_id!r}"
                )
        node_visibility = _validate_visibility(raw.get("visibility", "public"), f"node {node_id}")
        refs: dict[str, Any] = {}
        for ref_field in ("scene_ref", "encounter_ref"):
            value = raw.get(ref_field)
            if value is not None:
                text = str(value)
                if not _REF_RE.fullmatch(text):
                    raise AdventureGraphV2Error(
                        f"node {node_id} {ref_field} is invalid: {value!r}"
                    )
                refs[ref_field] = text
        npc_refs = _bounded_list(raw.get("npc_refs", []), f"node {node_id} npc_refs", MAX_REFS_PER_NODE)
        for value in npc_refs:
            if not isinstance(value, str) or not _REF_RE.fullmatch(value):
                raise AdventureGraphV2Error(
                    f"node {node_id} npc_refs entry is invalid: {value!r}"
                )
        transitions = _bounded_list(
            raw.get("transitions", []), f"node {node_id} transitions", MAX_TRANSITIONS_PER_NODE,
        )
        nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            "chapter_id": chapter_id,
            "visibility": node_visibility,
            "optional": bool(raw.get("optional", False)),
            "name": str(raw.get("name") or ""),
            "description": str(raw.get("description") or ""),
            "transitions": [
                _validate_transition(transition, f"node {node_id}")
                for transition in transitions
            ],
            "scene_ref": refs.get("scene_ref"),
            "encounter_ref": refs.get("encounter_ref"),
            "npc_refs": list(npc_refs),
        }
    if not nodes:
        raise AdventureGraphV2Error("v2 adventure requires at least one node")

    # start nodes：多入口；必须存在。
    start_node_ids = _bounded_list(
        adventure.get("start_node_ids", []), "start_node_ids", MAX_START_NODES,
    )
    start_ids = [_required_id(value, "start_node_id") for value in start_node_ids]
    if not start_ids:
        raise AdventureGraphV2Error("v2 adventure requires at least one start node")
    for node_id in start_ids:
        if node_id not in nodes:
            raise AdventureGraphV2Error(f"start node does not exist: {node_id!r}")

    # transitions 的 to 必须存在（回流合法：不要求 DAG）。
    for node_id, node in nodes.items():
        for transition in node["transitions"]:
            if transition["to"] not in nodes:
                raise AdventureGraphV2Error(
                    f"node {node_id!r} transition target does not exist: {transition['to']!r}"
                )

    # objectives / milestones：结构化声明，可并行；引用节点必须存在。
    objectives: list[dict[str, Any]] = []
    for raw in _bounded_list(adventure.get("objectives", []), "objectives", MAX_OBJECTIVES):
        if not isinstance(raw, dict):
            raise AdventureGraphV2Error("objective must be an object")
        objective_id = _required_id(raw.get("id"), "objective id")
        objective = {
            "id": objective_id,
            "name": str(raw.get("name") or ""),
            "visibility": _validate_visibility(raw.get("visibility", "public"), f"objective {objective_id}"),
            "node_ids": [
                _required_id(value, f"objective {objective_id} node_id")
                for value in _bounded_list(raw.get("node_ids", []), f"objective {objective_id} node_ids", MAX_NODES)
            ],
        }
        for node_id in objective["node_ids"]:
            if node_id not in nodes:
                raise AdventureGraphV2Error(
                    f"objective {objective_id!r} references unknown node: {node_id!r}"
                )
        objectives.append(objective)
    milestones: list[dict[str, Any]] = []
    for raw in _bounded_list(adventure.get("milestones", []), "milestones", MAX_MILESTONES):
        if not isinstance(raw, dict):
            raise AdventureGraphV2Error("milestone must be an object")
        milestone_id = _required_id(raw.get("id"), "milestone id")
        milestone = {
            "id": milestone_id,
            "name": str(raw.get("name") or ""),
            "visibility": _validate_visibility(raw.get("visibility", "public"), f"milestone {milestone_id}"),
            "node_ids": [
                _required_id(value, f"milestone {milestone_id} node_id")
                for value in _bounded_list(raw.get("node_ids", []), f"milestone {milestone_id} node_ids", MAX_NODES)
            ],
        }
        for node_id in milestone["node_ids"]:
            if node_id not in nodes:
                raise AdventureGraphV2Error(
                    f"milestone {milestone_id!r} references unknown node: {node_id!r}"
                )
        milestones.append(milestone)

    world_seed = _validate_world_seed(adventure.get("world_seed"))

    return {
        "id": adventure_id,
        "format": ADVENTURE_GRAPH_FORMAT_V2,
        "world_seed": world_seed,
        "visibility": visibility,
        "chapters": list(chapters.values()),
        "nodes": list(nodes.values()),
        "objectives": objectives,
        "milestones": milestones,
        "start_node_ids": start_ids,
    }


__all__ = [
    "ADVENTURE_GRAPH_FORMAT_V2",
    "MAX_NODES",
    "MAX_TRAVERSAL_STEPS",
    "NODE_TYPES",
    "AdventureGraphV2Error",
    "next_candidates",
    "reachable_nodes",
    "validate_graph_v2",
]


# ---- 遍历语义（ADV2-01，母方案 §24/§77）------------------------------------

# 有界遍历：回流合法，但任何计算都必须有步数上限（§77 结构遍历必须有 bound）。
MAX_TRAVERSAL_STEPS = 512


def reachable_nodes(
    graph: dict[str, Any],
    start_node_ids: list[str],
    *,
    max_steps: int = MAX_TRAVERSAL_STEPS,
) -> set[str]:
    """Bounded forward traversal from start nodes; backflow cannot loop forever."""

    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    visited: set[str] = set()
    frontier = [node_id for node_id in start_node_ids if node_id in nodes]
    steps = 0
    while frontier and steps < max_steps:
        node_id = frontier.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        steps += 1
        for transition in nodes[node_id].get("transitions", []):
            target = str(transition.get("to") or "")
            if target in nodes and target not in visited:
                frontier.append(target)
    return visited


def next_candidates(
    graph: dict[str, Any], active_node_ids: list[str],
) -> dict[str, list[str]]:
    """Open transitions from each active node（conditions 在 ADV2-02 前恒为空集，
    因此所有 transition 都开放）。"""

    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    candidates: dict[str, list[str]] = {}
    for node_id in active_node_ids:
        node = nodes.get(node_id)
        if node is None:
            continue
        candidates[node_id] = [
            str(transition.get("to") or "")
            for transition in node.get("transitions", [])
            if str(transition.get("to") or "") in nodes
        ]
    return candidates
