"""FIX-04 验收：Adventure v2 真正变成 Runtime（施工单 §6）。

覆盖：

```text
§6.1 node.on_complete 是 outcome 的唯一归属点，类型词表封闭
§6.2 adventure_progress 是权威持久化字段（存读往返）
§6.3 objective / milestone 必须存在；图校验拒绝重复 id
§6.4 gate 真正参与推进（完成节点 → 评估 gate → 开放后继）
§6.5/§6.6 创建事务内初始化进度 + 世界种子**原子**物化（一次提交）
§6.7 完成节点的 outcome 在一个权威事务内执行，任一失败全量回滚
§6.8 逻辑时间推进结算进程 → 后果节点（gate）开放
§6.9 D&D runtime 声明 v2 并提供 rules gate / reward 适配器
```

修复前：progress / gates / outcomes 只有 helper 与单测（`resolve_encounter`、
`outcomes_to_intents` 在生产代码里零调用方），`materialize_world_seed` 逐批
commit（"64 ops commit, 64 ops commit, 失败"会留下半个世界），`on_complete`
在图上根本不存在。
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.adventures import AdventureResolver
from src.adventures.graph_v2 import AdventureGraphV2Error, project_graph_v2, validate_graph_v2
from src.adventures.outcomes import OutcomeError
from src.adventures.progress import ProgressError
from src.engine.game_instance import GameInstance
from src.webui.services.adventure_materialization import materialize_world_seed
from src.engine.world_state import apply_world_ops, world_facts, world_processes
from src.webui.services import adventure_runtime

PACKAGE_ID = "v2_quest"
ADVENTURE_ID = "user:v2_quest"
WORLD_ID = "greymoor"


def _graph(**overrides: object) -> dict:
    record: dict = {
        "id": PACKAGE_ID,
        "format": "diceframe:adventure-graph-v2",
        "chapters": [{"id": "ch1", "name": "第一章"}],
        "nodes": [
            {
                "id": "gate", "type": "scene", "chapter_id": "ch1",
                "transitions": [{"to": "vault", "conditions": [
                    {"type": "world.fact_equals", "key": "gate.open", "value": True},
                ]}],
                "on_complete": [
                    {"type": "world_op", "op": {"op": "set_fact", "key": "gate.open", "value": True}},
                ],
            },
            {
                "id": "vault", "type": "scene", "chapter_id": "ch1",
                "transitions": [{"to": "aftermath"}],
                "on_complete": [
                    {"type": "progress_event", "kind": "milestone_reached", "id": "mile_vault"},
                ],
            },
            {"id": "aftermath", "type": "scene", "chapter_id": "ch1", "transitions": []},
        ],
        "objectives": [{"id": "obj_open", "name": "开门", "node_ids": ["gate"]}],
        "milestones": [{"id": "mile_vault", "name": "进入宝库", "node_ids": ["vault"]}],
        "start_node_ids": ["gate"],
    }
    record.update(overrides)
    return record


def _package_files(graph: dict) -> dict[str, bytes]:
    return {
        "manifest.json": json.dumps({
            "schema_version": 1, "adventure_id": ADVENTURE_ID, "version": "1.0.0",
            "format": "diceframe:adventure-graph-v2", "world_policy": "portable",
            "recommended_world_id": WORLD_ID,
            "required_runtime": {"id": "core:dnd2024", "minimum_version": 1},
            "default_locale": "zh-CN", "supported_locales": ["zh-CN"],
        }, ensure_ascii=False).encode("utf-8"),
        "adventure.json": json.dumps({
            "schema_version": 1, "kind": "adventure", "id": PACKAGE_ID,
            "source_ref": "diceframe-test:v2-quest",
            "recommended_world_id": WORLD_ID, "automation_level": "guided",
            **graph,
        }, ensure_ascii=False).encode("utf-8"),
        "locales/zh-CN/adventure.json": json.dumps({
            "locale_schema_version": 1, "locale": "zh-CN",
            "target": {"kind": "adventure", "id": PACKAGE_ID},
            "fields": {"tutorial": {"name": "V2 冒险", "summary": "测试用。"}},
        }, ensure_ascii=False).encode("utf-8"),
    }


def _install(tmp_path: Path, graph: dict) -> AdventureResolver:
    root = tmp_path / "adventures"
    package = root / PACKAGE_ID
    for name, payload in _package_files(graph).items():
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return AdventureResolver.single_directory(root, source_kind="user")


def _deps(resolver: AdventureResolver, **overrides: object) -> adventure_runtime.AdventureRuntimeDependencies:
    values: dict = {
        "resolve_binding": lambda instance: resolver.resolve_binding(
            instance.adventure_binding, "zh-CN",
        ),
        "materialize_world_seed": materialize_world_seed,
    }
    values.update(overrides)
    return adventure_runtime.AdventureRuntimeDependencies(**values)


def _instance(resolver: AdventureResolver) -> GameInstance:
    instance = GameInstance(
        game_key=("test", "adv2-runtime", "bot"),
        world_id=WORLD_ID, rule_id="dnd2024_srd", language="zh-CN", gm_uid="gm",
    )
    binding = resolver.resolve_with_source(ADVENTURE_ID, "zh-CN").binding(WORLD_ID)
    assert instance.bind_adventure(binding) is True
    return instance


# ---- §6.1 graph contract ----------------------------------------------------


def test_node_on_complete_is_validated_by_the_graph_contract() -> None:
    graph = validate_graph_v2(_graph())

    node = next(item for item in graph["nodes"] if item["id"] == "gate")
    assert node["on_complete"] == [{
        "type": "world_op",
        "op": {"op": "set_fact", "key": "gate.open", "value": True},
    }]


def test_unknown_outcome_type_and_shape_fail_closed() -> None:
    with pytest.raises(AdventureGraphV2Error, match="outcome type is not supported"):
        validate_graph_v2(_graph(nodes=[
            {"id": "gate", "type": "scene", "on_complete": [{"type": "teleport"}]},
        ], start_node_ids=["gate"]))
    with pytest.raises(AdventureGraphV2Error, match="on_complete must be an array"):
        validate_graph_v2(_graph(nodes=[
            {"id": "gate", "type": "scene", "on_complete": {"type": "world_op"}},
        ], start_node_ids=["gate"]))


def test_player_projection_never_leaks_on_complete() -> None:
    graph = validate_graph_v2(_graph())

    player = project_graph_v2(graph, viewer_is_gm=False)
    gm = project_graph_v2(graph, viewer_is_gm=True)

    assert all("on_complete" not in node for node in player["nodes"])
    assert any(node.get("on_complete") for node in gm["nodes"])


# ---- §6.3 duplicate ids -----------------------------------------------------


def test_duplicate_objective_and_milestone_ids_are_rejected() -> None:
    with pytest.raises(AdventureGraphV2Error, match="duplicate objective id"):
        validate_graph_v2(_graph(objectives=[
            {"id": "obj_open", "name": "A", "node_ids": ["gate"]},
            {"id": "obj_open", "name": "B", "node_ids": ["gate"]},
        ]))
    with pytest.raises(AdventureGraphV2Error, match="duplicate milestone id"):
        validate_graph_v2(_graph(milestones=[
            {"id": "mile_vault", "name": "A", "node_ids": ["gate"]},
            {"id": "mile_vault", "name": "B", "node_ids": ["gate"]},
        ]))


# ---- §6.2 persistence -------------------------------------------------------


def test_adventure_progress_persists_through_save_and_reload(tmp_path) -> None:
    resolver = _install(tmp_path, _graph())
    instance = _instance(resolver)
    deps = _deps(resolver)

    adventure_runtime.initialize_adventure_run(deps, instance)
    adventure_runtime.complete_adventure_node(deps, instance, "gate")

    payload = instance.to_dict()
    restored = GameInstance.from_dict(payload)

    assert restored.adventure_progress == instance.adventure_progress
    assert restored.adventure_progress["completed_nodes"] == ["gate"]
    assert restored.adventure_progress["active_nodes"] == ["vault"]


def test_legacy_save_without_progress_loads_as_empty(tmp_path) -> None:
    resolver = _install(tmp_path, _graph())
    payload = _instance(resolver).to_dict()
    payload.pop("adventure_progress")
    payload["adventure_progress"] = "corrupt"

    restored = GameInstance.from_dict(payload)

    assert restored.adventure_progress == {}


# ---- §6.5 / §6.6 initialize -------------------------------------------------


def test_initialize_adventure_run_sets_progress_and_materializes_seed(tmp_path) -> None:
    graph = _graph(world_seed={
        "entities": [{"entity_id": "location:vault", "kind": "location"}],
        "relations": [], "facts": [], "processes": [],
    })
    resolver = _install(tmp_path, graph)
    instance = _instance(resolver)

    result = adventure_runtime.initialize_adventure_run(_deps(resolver), instance)

    assert result["initialized"] is True
    assert result["active_nodes"] == ["gate"]
    assert instance.adventure_progress["active_nodes"] == ["gate"]
    assert "location:vault" in (instance.world_state.get("entities") or {})


def test_world_seed_materialization_commits_once_and_never_partially(tmp_path) -> None:
    """§6.6：65 条种子里第 65 条非法 → 世界必须**一条都没写**。"""

    entities = [{"entity_id": f"location:spot_{index}", "kind": "location"} for index in range(64)]
    entities.append({"entity_id": "location:spot_0", "kind": "location"})  # 重复 id
    graph = _graph(world_seed={
        "entities": entities, "relations": [], "facts": [], "processes": [],
    })
    resolver = _install(tmp_path, graph)
    instance = _instance(resolver)
    before = deepcopy(instance.world_state)

    with pytest.raises(Exception):
        materialize_world_seed(instance, resolver.resolve(ADVENTURE_ID, "zh-CN"))

    assert instance.world_state == before
    assert not (instance.world_state.get("entities") or {})


def test_initialize_failure_leaves_no_partial_progress(tmp_path) -> None:
    graph = _graph(world_seed={
        "entities": [{"entity_id": "location:vault", "kind": "location"}] * 2,
        "relations": [], "facts": [], "processes": [],
    })
    resolver = _install(tmp_path, graph)
    instance = _instance(resolver)

    with pytest.raises(Exception):
        adventure_runtime.initialize_adventure_run(_deps(resolver), instance)

    assert instance.adventure_progress == {}
    assert not (instance.world_state.get("entities") or {})


# ---- §6.4 / §6.7 node completion transaction --------------------------------


def test_complete_node_applies_outcomes_and_activates_successors(tmp_path) -> None:
    resolver = _install(tmp_path, _graph())
    instance = _instance(resolver)
    deps = _deps(resolver)
    adventure_runtime.initialize_adventure_run(deps, instance)

    result = adventure_runtime.complete_adventure_node(deps, instance, "gate")

    # §6.4：on_complete 的 world_op 让 gate 满足 → 后继节点开放。
    assert result["activated_nodes"] == ["vault"]
    assert world_facts(instance.world_state)["gate.open"]["value"] is True
    assert instance.adventure_progress["active_nodes"] == ["vault"]


def test_rollback_restores_adventure_progress_with_the_world(tmp_path) -> None:
    """FIX-07 §10 步骤 23：整轮回滚必须把进度和世界一起撤销。

    回归背景（Golden E2E）：``rollback_last_round`` 只恢复 ``pre_world_state``，
    进度留在被丢弃的分支上 → "世界退回去了、节点还是完成状态"。
    """

    from src.engine.round_snapshots import capture_round_entity_snapshot

    resolver = _install(tmp_path, _graph())
    instance = _instance(resolver)
    deps = _deps(resolver)
    adventure_runtime.initialize_adventure_run(deps, instance)

    capture_round_entity_snapshot(instance)
    world_before = deepcopy(instance.world_state)
    progress_before = deepcopy(instance.adventure_progress)

    adventure_runtime.complete_adventure_node(deps, instance, "gate")
    assert instance.adventure_progress["completed_nodes"] == ["gate"]
    asyncio.run(instance.finish_judgment("推进了一轮"))

    rolled = asyncio.run(instance.rollback_last_round())

    assert rolled is not None
    assert instance.world_state == world_before
    assert instance.adventure_progress == progress_before
    assert instance.adventure_progress["completed_nodes"] == []
    assert instance.adventure_progress["active_nodes"] == ["gate"]


def test_objective_milestone_validation_still_applies_inside_runtime(tmp_path) -> None:
    graph = _graph()
    graph["nodes"][1]["on_complete"] = [
        {"type": "progress_event", "kind": "milestone_reached", "id": "mile_ghost"},
    ]
    resolver = _install(tmp_path, graph)
    instance = _instance(resolver)
    deps = _deps(resolver)
    adventure_runtime.initialize_adventure_run(deps, instance)
    adventure_runtime.complete_adventure_node(deps, instance, "gate")

    with pytest.raises(ProgressError, match="milestone does not exist"):
        adventure_runtime.complete_adventure_node(deps, instance, "vault")

    # 全量回滚：进度与世界都不动。
    assert instance.adventure_progress["completed_nodes"] == ["gate"]
    assert instance.adventure_progress["active_nodes"] == ["vault"]


def test_item_reward_without_reward_sink_fails_closed_and_rolls_back(tmp_path) -> None:
    graph = _graph()
    graph["nodes"][0]["on_complete"] = [
        {"type": "world_op", "op": {"op": "set_fact", "key": "gate.open", "value": True}},
        {"type": "item_reward", "ref": {"source": "module:x", "kind": "item", "id": "brass_key"}},
    ]
    resolver = _install(tmp_path, graph)
    instance = _instance(resolver)
    deps = _deps(resolver, reward_converter=lambda _instance: (
        lambda raw, source, recipient: {"kind": "item_grant", "ref": raw, "name": "Brass Key"}
    ))
    adventure_runtime.initialize_adventure_run(deps, instance)
    before_world = deepcopy(instance.world_state)

    with pytest.raises(adventure_runtime.AdventureRuntimeError, match="reward sink"):
        adventure_runtime.complete_adventure_node(deps, instance, "gate")

    # §6.7：世界改动与进度一起回滚。
    assert instance.world_state == before_world
    assert instance.adventure_progress["completed_nodes"] == []
    assert instance.adventure_progress["active_nodes"] == ["gate"]


def test_item_reward_goes_through_the_injected_authority_sink(tmp_path) -> None:
    graph = _graph()
    graph["nodes"][0]["on_complete"] = [
        {"type": "item_reward", "ref": {"source": "module:x", "kind": "item", "id": "brass_key"}},
    ]
    resolver = _install(tmp_path, graph)
    instance = _instance(resolver)
    queued: list[dict] = []

    def sink(_instance, intents):
        queued.extend(intents)
        return [intent["name"] for intent in intents]

    deps = _deps(
        resolver,
        reward_converter=lambda _instance: (
            lambda raw, source, recipient: {
                "kind": "item_grant", "ref": raw, "name": "Brass Key",
                "recipient_uid": recipient,
            }
        ),
        queue_reward_intents=sink,
    )
    adventure_runtime.initialize_adventure_run(deps, instance)

    result = adventure_runtime.complete_adventure_node(
        deps, instance, "gate", recipient_uid="gm",
    )

    assert result["queued_rewards"] == ["Brass Key"]
    assert queued[0]["recipient_uid"] == "gm"
    assert instance.adventure_progress["completed_nodes"] == ["gate"]


def test_completing_an_unknown_or_inactive_node_fails_closed(tmp_path) -> None:
    resolver = _install(tmp_path, _graph())
    instance = _instance(resolver)
    deps = _deps(resolver)
    adventure_runtime.initialize_adventure_run(deps, instance)

    with pytest.raises(adventure_runtime.AdventureRuntimeError, match="node does not exist"):
        adventure_runtime.complete_adventure_node(deps, instance, "ghost")
    with pytest.raises(ProgressError, match="not active"):
        adventure_runtime.complete_adventure_node(deps, instance, "aftermath")


# ---- §6.8 process consequence ---------------------------------------------


def test_time_advance_settles_the_process_and_opens_the_consequence_node(tmp_path) -> None:
    graph = _graph()
    graph["nodes"] = [
        {
            "id": "gate", "type": "scene", "chapter_id": "ch1",
            "transitions": [{"to": "aftermath", "conditions": [
                {"type": "world.process_status", "id": "ritual", "value": "completed"},
            ]}],
            "on_complete": [
                {"type": "world_op", "op": {
                    "op": "start_process", "process_id": "ritual", "kind": "secret_ritual",
                    "due_at": {"day": 1, "minute": 120},
                }},
            ],
        },
        {
            "id": "aftermath", "type": "scene", "chapter_id": "ch1",
            "transitions": [],
            "on_complete": [
                {"type": "world_op", "op": {
                    "op": "set_fact", "key": "world.ritual_resolved", "value": True,
                }},
            ],
        },
    ]
    graph["objectives"] = []
    graph["milestones"] = []
    resolver = _install(tmp_path, graph)
    instance = _instance(resolver)
    deps = _deps(resolver)
    adventure_runtime.initialize_adventure_run(deps, instance)

    adventure_runtime.complete_adventure_node(deps, instance, "gate")
    # gate 未满足：后果节点不该在完成瞬间开放。
    assert instance.adventure_progress["active_nodes"] == []
    assert world_processes(instance.world_state)["ritual"]["status"] == "running"

    from src.engine.world_events import advance_world_time

    summary = advance_world_time(instance, 24 * 60)
    assert [item["process_id"] for item in summary["processes"]] == ["ritual"]
    assert world_processes(instance.world_state)["ritual"]["status"] == "completed"

    advanced = adventure_runtime.advance_adventure_world(deps, instance)

    assert advanced["activated_nodes"] == ["aftermath"]
    assert instance.adventure_progress["active_nodes"] == ["aftermath"]
    adventure_runtime.complete_adventure_node(deps, instance, "aftermath")
    assert world_facts(instance.world_state)["world.ritual_resolved"]["value"] is True
    # 幂等：再次推进不会重复激活。
    assert adventure_runtime.advance_adventure_world(deps, instance)["activated_nodes"] == []


# ---- §6.9 D&D runtime ------------------------------------------------------


def test_v2_adventure_drives_the_dnd_campaign_and_story_encounter(tmp_path) -> None:
    """§6.9 + §5.3：v2 绑定不再让 D&D 起局失败，且遭遇来自 v2 进度。"""

    from src.rulesets.dnd2024.runtime import Dnd2024Runtime

    graph = _graph()
    graph["nodes"] = [
        {
            "id": "arena", "type": "encounter", "chapter_id": "ch1",
            "encounter_ref": "encounter_profile:cellar_pack",
            "transitions": [],
            "on_complete": [],
        },
    ]
    graph["objectives"] = []
    graph["milestones"] = []
    graph["start_node_ids"] = ["arena"]
    resolver = _install(tmp_path, graph)
    runtime = Dnd2024Runtime()
    runtime.set_adventure_resolver(resolver)
    runtime.set_module_content_sources(lambda _instance: [(
        "module:test-module",
        {
            "monster": {
                "clockwork_rat": {
                    "kind": "monster", "profile_id": "clockwork_rat", "name": "Clockwork Rat",
                    "source_ref": "module:test-module", "hp": 11, "armor_class": 13, "speed": 30,
                    "abilities": {"str": 6, "dex": 14, "con": 10, "int": 3, "wis": 10, "cha": 3},
                    "attacks": [{"id": "bite", "damage": "1d4+1", "attack_bonus": 4}],
                },
            },
            "encounter_profile": {
                "cellar_pack": {
                    "kind": "encounter_profile", "encounter_id": "cellar_pack",
                    "name": "Cellar Pack", "source_ref": "module:test-module",
                    "difficulty": "standard",
                    "enemies": [{
                        "ref": {"source": "module:test-module", "kind": "monster",
                                "id": "clockwork_rat"},
                        "count": 2,
                    }],
                },
            },
        },
    )])
    instance = _instance(resolver)
    preset = runtime.builder_choices(None, {"locale": "zh-CN"})["quick_presets"][0]
    sheet = runtime.finalize_character(
        None, {**preset["draft"], "locale": "zh-CN", "name": "阿登"},
    )
    instance.players["gm"] = {"character_name": "阿登", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])

    # v2 起局不再被 v1 tutorial 校验挡住。
    runtime.initialize_new_run(instance, preserve_characters=True)
    campaign = runtime.gameplay_view(instance, "gm", True)["campaign"]
    assert campaign["tutorial"]["status"] == "not_applicable"

    deps = _deps(resolver)
    adventure_runtime.initialize_adventure_run(
        adventure_runtime.AdventureRuntimeDependencies(
            resolve_binding=lambda inst: resolver.resolve_binding(inst.adventure_binding, "zh-CN"),
            materialize_world_seed=materialize_world_seed,
        ),
        instance,
    )

    access = runtime._encounter_access(instance, campaign)
    assert access.mode == "story"
    assert access.status == "pending"
    assert access.encounter_preset_id == "cellar_pack"
    assert access.origin_step_id == "arena"

    resolved = runtime.resolve_intent(instance, {
        "intent_id": "intent-v2-encounter",
        "type": "combat.start",
        "expected_version": int(instance.ruleset_state.get("version", 0) or 0),
        "submitted_by": "gm",
        "encounter_preset_id": "cellar_pack",
        "encounter_instance_id": access.encounter_instance_id,
    }, __import__("random").Random(7))

    assert resolved["ok"] is True, resolved
    started = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.combat.started"
    )
    assert set(started["enemies"]) == {"clockwork_rat_1", "clockwork_rat_2"}
    assert started["enemies"]["clockwork_rat_1"]["hp"] == 11


def test_dnd_runtime_declares_v2_and_exposes_rules_adapters(tmp_path) -> None:
    from src.rulesets.dnd2024.runtime import Dnd2024Runtime

    runtime = Dnd2024Runtime()
    assert "diceframe:adventure-graph-v2" in runtime.capabilities.adventure_formats
    assert "diceframe:adventure-graph-v1" in runtime.capabilities.adventure_formats

    sheet = runtime.finalize_character(None, {**runtime.builder_choices(
        None, {"locale": "en"},
    )["quick_presets"][0]["draft"], "locale": "en", "name": "Arden"})
    instance = GameInstance(
        game_key=("test", "adv2-adapter", "bot"), world_id=WORLD_ID,
        rule_id="dnd2024_srd", language="en",
    )
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])

    evaluator = runtime.adventure_rules_evaluator(instance)
    assert evaluator("rules.party_level", "party", 1) is True
    assert evaluator("rules.party_level", "party", 99) is False
    assert evaluator("rules.item_possession", "brass key", True) is False
    assert evaluator("rules.item_possession", "brass key", False) is True
    assert evaluator("rules.outcome", "combat_ended", "pending") is True
    assert evaluator("rules.outcome", "unknown_outcome", "anything") is False

    apply_world_ops(instance, [{"op": "set_fact", "key": "gate.open", "value": True}])
    assert evaluator("rules.outcome", "combat_ended", "pending") is True
