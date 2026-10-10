from types import SimpleNamespace

from src.engine.modules import narrative_notes, ruleset_runtime

from src.rulesets.dnd2024.director import Dnd2024Director
from src.rulesets.dnd2024.director.context import build_director_context
from src.rulesets.dnd2024.director.decision_resolver import resolve_decision
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


def _instance(*actions, scene="金狮酒馆", combat_status="none", players=None):
    return SimpleNamespace(
        world_id="greymoor",
        modules={
            "ruleset_runtime": {
                **ruleset_runtime.fresh(), "state": {"combat": {"status": combat_status}},
            },
            "narrative_notes": {**narrative_notes.fresh(), "scene": scene},
        },
        action_queue=[{"user_id": uid, "text": text} for uid, text in actions],
        players=players or {"p1": {}, "p2": {}},
    )


def test_director_classifies_hostile_action_without_mutating_instance():
    instance = _instance(("p1", "我拔刀攻击门口的敌人"))
    before = list(instance.action_queue)
    result = Dnd2024Director().propose_dict(instance, {"tutorial": {"status": "not_started"}})

    assert result["proposal"]["kind"] == "combat"
    assert result["proposal"]["requires_gm_confirmation"] is True
    assert instance.action_queue == before


def test_hostile_opening_action_is_deferred_from_generic_checks():
    instance = _instance(("p1", "我拔刀攻击门口的敌人"))
    ruleset_runtime.replace_binding(instance, {"id": "core:dnd2024", "version": 1})
    runtime = Dnd2024Runtime()

    assert runtime.deferred_narrative_check_action_ids(instance) == ["action:0"]


def test_charge_and_combat_preparation_are_deferred_before_generic_checks():
    instance = _instance(
        ("p1", "准备战斗，拿起武器"),
        ("p2", "冲锋"),
    )
    ruleset_runtime.replace_binding(instance, {"id": "core:dnd2024", "version": 1})

    assert Dnd2024Runtime().deferred_narrative_check_action_ids(instance) == [
        "action:0", "action:1",
    ]


def test_director_uses_party_decision_before_free_text_classification():
    context = build_director_context(
        _instance(("p1", "我调查灰烬")),
        {"tutorial": {"status": "active", "current_step": {"id": "step", "choices": [{}, {}]}}},
    )
    result = resolve_decision(context)
    assert result.kind == "party_decision"


def test_director_story_step_keeps_canonical_encounter_preset():
    context = build_director_context(
        _instance(("p1", "attack the goblin")),
        {"tutorial": {"status": "active", "current_step": {
            "id": "ambush", "choices": [], "encounter_preset_id": "first_skirmish",
        }}},
    )
    result = resolve_decision(context, "auto")
    assert result.kind == "combat"
    assert result.encounter_preset_id == "first_skirmish"
    assert result.requires_gm_confirmation is False


def test_director_never_exposes_unbounded_player_text():
    context = build_director_context(_instance(("p1", "x" * 2000)))
    assert 0 < len(context.actions[0]["text"]) <= 500


def test_runtime_exposes_director_as_read_only_gameplay_projection():
    instance = _instance(("p1", "我调查桌上的信件"))
    ruleset_runtime.replace_binding(instance, {"id": "core:dnd2024", "version": 1})
    runtime = Dnd2024Runtime()
    proposal = runtime.director_proposal(instance, {"tutorial": {"status": "not_started"}})
    assert proposal["proposal"]["kind"] == "check"
    assert "ruleset_state" not in proposal


def test_director_context_is_not_projected_to_gameplay_clients():
    instance = _instance(("p1", "我调查桌上的信件"))
    ruleset_runtime.replace_binding(instance, {"id": "core:dnd2024", "version": 1})
    gameplay = Dnd2024Runtime().gameplay_view(instance, "p1", False)
    assert "context" not in gameplay["director"]
    assert gameplay["director"]["proposal"]["kind"] == "check"
