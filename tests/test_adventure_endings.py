"""Adventure ending nodes: reaching one ends the adventure (GAL track P4)."""
from copy import deepcopy

import pytest

from src.adventures.graph_v2 import AdventureGraphV2Error, validate_graph_v2
from src.adventures.progress import ProgressError, complete_node, ending_node_id, new_progress
from src.adventures.scene_context import format_scene_context, project_scene_objectives
from src.engine.modules import adventure_runtime_state


def ending_graph(**node_overrides):
    good_end = {"id": "good_end", "type": "ending", "name": "Sunset Promise",
                "description": "They meet again at the station."}
    good_end.update(node_overrides)
    return {"id": "route", "schema_version": 2, "chapters": [], "objectives": [], "milestones": [],
            "nodes": [
                {"id": "confession", "type": "decision", "choice_mode": "exclusive", "transitions": [
                    {"to": "good_end", "choice_id": "accept", "choice_label": "Accept"},
                    {"to": "secret_end", "choice_id": "leave", "choice_label": "Leave town"},
                ]},
                {"id": "side_story", "type": "scene", "transitions": []},
                good_end,
                {"id": "secret_end", "type": "ending", "visibility": "gm", "name": "SECRET END",
                 "description": "SECRET EPILOGUE"},
            ],
            "start_node_ids": ["confession", "side_story"]}


def test_ending_is_a_node_type_old_graph_types_are_unchanged():
    graph = validate_graph_v2(ending_graph())
    assert [node["type"] for node in graph["nodes"]] == ["decision", "scene", "ending", "ending"]


@pytest.mark.parametrize("overrides", [
    {"transitions": [{"to": "side_story"}]},
    {"choice_mode": "exclusive", "transitions": []},
    {"on_complete": [{"type": "progress_event", "kind": "node_completed", "id": "side_story"}]},
])
def test_ending_cannot_lead_anywhere(overrides):
    with pytest.raises(AdventureGraphV2Error):
        validate_graph_v2(ending_graph(**overrides))


def test_ending_may_still_record_objectives_and_world_outcomes():
    graph = ending_graph(on_complete=[
        {"type": "world_op", "op": {"op": "set_fact", "key": "route.ended", "value": True}},
    ])
    validate_graph_v2(graph)


def test_reaching_an_ending_closes_every_open_node_and_freezes_progress():
    graph = validate_graph_v2(ending_graph())
    progress = new_progress(graph)
    assert ending_node_id(progress) == ""
    assert complete_node(progress, graph, "confession", choice_id="accept") == ["good_end"]
    assert progress["active_nodes"] == ["side_story", "good_end"]
    assert complete_node(progress, graph, "good_end") == []
    assert ending_node_id(progress) == "good_end"
    assert progress["active_nodes"] == []
    assert progress["completed_nodes"] == ["confession", "good_end"]
    assert progress["history"][-1] == {"kind": "ending_reached", "id": "good_end"}
    frozen = deepcopy(progress)
    with pytest.raises(ProgressError, match="ended"):
        complete_node(progress, graph, "side_story")
    assert progress == frozen


def _ended(choice):
    graph = validate_graph_v2(ending_graph())
    progress = new_progress(graph)
    target = complete_node(progress, graph, "confession", choice_id=choice)[0]
    complete_node(progress, graph, target)
    return graph, progress


def test_scene_context_tells_the_narrator_to_close_the_story():
    graph, progress = _ended("accept")
    player = project_scene_objectives(graph, progress, viewer_is_gm=False)
    assert player["ending"] == {"name": "Sunset Promise", "description": "They meet again at the station."}
    assert player["current_nodes"] == []
    gm = project_scene_objectives(graph, progress, viewer_is_gm=True)
    assert gm["ending"]["id"] == "good_end" and gm["possible_next_nodes"] == []
    text = format_scene_context(gm, "zh-CN", max_chars=2000)
    assert "冒险已抵达结局：Sunset Promise" in text and "They meet again" in text


def test_hidden_ending_ends_the_story_without_leaking_its_text():
    graph, progress = _ended("leave")
    player = project_scene_objectives(graph, progress, viewer_is_gm=False)
    assert player["ending"] == {}
    assert "SECRET" not in str(player)
    assert "reached its ending." in format_scene_context(player, "en", max_chars=2000)
    assert project_scene_objectives(graph, progress, viewer_is_gm=True)["ending"]["name"] == "SECRET END"


def _installed_ending(tmp_path):
    from src.adventures import AdventureResolver
    from src.rulesets.registry import RulesetRuntimeRegistry
    from src.webui.services import adventure_runtime, adventures
    from tests.test_adventure_v2_runtime import PACKAGE_ID, _deps, _install, _instance

    graph = ending_graph()
    graph.update(id=PACKAGE_ID, format="diceframe:adventure-graph-v2")
    resolver = _install(tmp_path, graph)
    instance = _instance(resolver)
    runtime_deps = _deps(resolver)
    adventure_runtime.initialize_adventure_run(runtime_deps, instance)
    resolver = AdventureResolver.single_directory(tmp_path / "adventures", source_kind="user")
    deps = adventures.AdventureDependencies(
        adventure_loader=resolver, adventure_resolver=resolver, list_instances=lambda: [instance],
        load_rule_by_id=lambda *_: None, ruleset_registry=RulesetRuntimeRegistry(),
        default_runtime_requirement=lambda: {},
    )
    return runtime_deps, deps, instance


@pytest.mark.parametrize("choice, visible", [("accept", True), ("leave", False)])
def test_game_projection_reports_the_ending_and_persists(tmp_path, choice, visible):
    from src.engine.game_instance import GameInstance
    from src.webui.services import adventure_runtime, adventures

    runtime_deps, deps, instance = _installed_ending(tmp_path)
    target = "good_end" if choice == "accept" else "secret_end"
    adventure_runtime.complete_adventure_node(runtime_deps, instance, "confession", choice_id=choice)
    adventure_runtime.complete_adventure_node(runtime_deps, instance, target)
    restored = GameInstance.from_dict(instance.to_dict())
    assert ending_node_id(adventure_runtime_state.progress(restored)) == target

    progress = adventures.game_adventure_projection(deps, restored, viewer_is_gm=False)["adventure"]["projection"]["progress"]
    assert progress["ended"] is True and progress["active_nodes"] == []
    assert ("ending_node_id" in progress) is visible
    gm = adventures.game_adventure_projection(deps, restored, viewer_is_gm=True)["adventure"]["projection"]["progress"]
    assert gm["ending_node_id"] == target
    with pytest.raises(ProgressError):
        adventure_runtime.complete_adventure_node(runtime_deps, restored, "side_story")
