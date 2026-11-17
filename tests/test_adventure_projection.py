"""宿主向外部规则运行时投影冒险图的契约（ADR-0014 冒险接缝）。

覆盖三件事：

- 只投影绑定包，且只投影运行时会用到的字段；
- 投影有独立预算，超限时按「先丢描述、再丢遭遇目录」降级，而不是拖垮请求；
- 解析失败（包被删、绑定过期）不影响这局继续跑，也不会把异常泄漏给调用方。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.adventures.graph_v2 import validate_graph_v2
from src.engine.game_instance import GameInstance
from src.rulesets.adventure_projection import (
    MAX_PROJECTION_BYTES,
    project_bound_adventure,
)
from src.rulesets.contracts import RulesetCapabilities
from src.rulesets.registry import RulesetRuntimeRegistry
from src.rulesets.remote_adapter import RemoteRulesetRuntimeAdapter


def _graph() -> dict:
    record = {
        "id": "castle_quest",
        "schema_version": 2,
        "format": "diceframe:adventure-graph-v2",
        "name": "城堡任务",
        "chapters": [{"id": "ch1", "name": "第一章"}],
        "nodes": [
            {
                "id": "gate", "type": "scene", "chapter_id": "ch1", "name": "城门",
                "description": "大门紧闭。", "choice_mode": "exclusive",
                "visibility": "gm", "scene_ref": "scene:gate",
                "transitions": [{"choice_id": "knock", "choice_label": "敲门", "to": "hall"}],
            },
            {
                "id": "hall", "type": "encounter", "chapter_id": "ch1", "name": "大厅",
                "encounter_ref": "encounter_catalog:goblins",
                "transitions": [],
            },
        ],
        "objectives": [],
        "start_node_ids": ["gate"],
    }
    return validate_graph_v2(record)


def _bundle(graph: dict, *, adventure_id: str = "demo:castle") -> SimpleNamespace:
    manifest = SimpleNamespace(
        adventure_id=adventure_id, version="1.0.0",
        format="diceframe:adventure-graph-v2",
    )
    entities = {
        "encounter_catalog": {
            "goblins": {"name": "哥布林", "presets": [{"id": "default", "enemies": []}]},
        },
    }
    return SimpleNamespace(manifest=manifest, adventure=graph, entities=entities)


def _instance(binding: dict | None = None) -> GameInstance:
    instance = GameInstance(game_key=("web", "adv", "bot"), run_id="run-adv")
    instance.adventure_binding = (
        {"adventure_id": "demo:castle"} if binding is None else binding
    )
    return instance


class _Resolver:
    def __init__(self, resolution: object | Exception) -> None:
        self._resolution = resolution
        self.calls = 0

    def resolve_binding(self, binding, locale=""):
        self.calls += 1
        if isinstance(self._resolution, Exception):
            raise self._resolution
        return self._resolution


def test_projection_keeps_only_fields_a_runtime_can_act_on():
    graph = _graph()
    resolution = SimpleNamespace(bundle=_bundle(graph), kind="user", source_id="")
    projection = project_bound_adventure(_instance(), _Resolver(resolution))

    assert projection is not None
    assert projection["adventure_id"] == "demo:castle"
    assert projection["format"] == "diceframe:adventure-graph-v2"
    nodes = {row["id"]: row for row in projection["nodes"]}
    assert set(nodes) == {"gate", "hall"}
    # 图里合法但运行时用不到的字段（作者可见性、场景引用）不跨界。
    assert "visibility" not in nodes["gate"]
    assert "scene_ref" not in nodes["gate"]
    assert nodes["gate"]["transitions"][0]["choice_label"] == "敲门"
    assert nodes["hall"]["encounter_ref"] == "encounter_catalog:goblins"
    assert projection["encounter_catalog"]["goblins"]["presets"][0]["id"] == "default"


def test_no_binding_means_no_projection_and_no_resolver_call():
    resolver = _Resolver(RuntimeError("should not be reached"))
    instance = _instance(binding={})
    assert project_bound_adventure(instance, resolver) is None
    assert resolver.calls == 0


def test_stale_or_removed_package_degrades_to_no_projection():
    resolver = _Resolver(RuntimeError("package removed"))
    assert project_bound_adventure(_instance(), resolver) is None


def test_projection_drops_descriptions_rather_than_exceeding_its_budget():
    graph = _graph()
    long_text = "很长" * 400
    graph["nodes"] = [
        {
            "id": f"n{index}", "type": "scene", "chapter_id": "ch1",
            "name": f"节点{index}", "description": long_text,
            "transitions": [],
        }
        for index in range(256)
    ]
    graph["start_node_ids"] = ["n0"]
    resolution = SimpleNamespace(bundle=_bundle(graph), kind="user", source_id="")
    projection = project_bound_adventure(_instance(), _Resolver(resolution))

    assert projection is not None
    # 描述可以先丢，节点身份不能丢：运行时靠 id 判断当前节点是什么。
    assert all("description" not in row for row in projection["nodes"])
    assert projection["nodes"][0]["id"] == "n0"
    assert len(projection["nodes"]) == 256


def test_adapter_sends_the_projection_only_when_the_runtime_asks_for_it():
    graph = _graph()
    resolution = SimpleNamespace(bundle=_bundle(graph), kind="user", source_id="")
    sent: list[dict] = []

    def transport(request: dict) -> dict:
        sent.append(request)
        return {"request_id": request["request_id"], "ok": True, "result": []}

    instance = _instance()
    plain = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(), transport,
        adventure_resolver=_Resolver(resolution),
    )
    plain.available_intents(instance, "alice")
    assert "adventure" not in sent[-1]["payload"]["instance"]

    needs_graph = RemoteRulesetRuntimeAdapter(
        "external:demo", 1, RulesetCapabilities(adventure_projection=True), transport,
        adventure_resolver=_Resolver(resolution),
    )
    needs_graph.available_intents(instance, "alice")
    projection = sent[-1]["payload"]["instance"]["adventure"]
    assert projection["adventure_id"] == "demo:castle"


def test_registry_injects_the_resolver_into_late_registrations():
    class LateRuntime:
        runtime_id = "external:late"
        runtime_version = 1
        capabilities = RulesetCapabilities()

        def __init__(self) -> None:
            self.resolver = None

        def set_adventure_resolver(self, resolver: object) -> None:
            self.resolver = resolver

    resolver = object()
    registry = RulesetRuntimeRegistry([])
    registry.set_adventure_resolver(resolver)
    late = LateRuntime()
    registry.register(late)
    assert late.resolver is resolver

