"""#610 follow-ups: scene endings stay terminal across races, restarts and bad proposals.

1) A narrator proposal is not confirmed while another round is being judged:
   confirming it then reached an ending under that round, which failed when
   the round tried to open the next advance, rolled the ending back and left
   an orphan round in history.  The proposal is kept and settled after the
   round commits.
2) Reset / restart / seed restart keep the gameplay's scene progression
   (and with it the ending freeze); seed restarts pass creation validation.
3) A deterministic failure of the completion transaction (e.g. a world op
   past the fact cap) rejects that proposal instead of blocking the queue.
4) Transition narration lands on the round that committed, even when the
   next round starts while the narration is generated.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from webapi_harness import web_api  # noqa: F401
from src.engine.game_instance import GameState
from src.engine.modules import adventure_runtime_state, game_config, progression_state
from src.webui.services import adventure_runtime


def _gal_rule(api, *, auto_confirm: bool = False) -> None:
    rule = {
        "rule_id": "gal_narrative", "rule_name": "GAL", "dice_system": "none",
        "mechanics": "narrative_only",
    }
    if auto_confirm:
        rule["adventure_progression"] = {"auto_confirm_completion": True, "activation_narration": False}
    (api._rules_dir / "gal_narrative.json").write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")


def _install_quest(api) -> None:
    from src.adventures import AdventureResolver
    from tests.test_adventure_auto_completion import QUEST_GRAPH
    from tests.test_adventure_v2_runtime import _package_files

    root = api._reg.save_dir.parent / "test-adventures"
    files = _package_files(dict(QUEST_GRAPH))
    manifest = json.loads(files["manifest.json"])
    manifest["required_runtime"] = {"id": "core:adventure", "minimum_version": 1}
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")
    for name, payload in files.items():
        target = root / "v2_quest" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    resolver = AdventureResolver.single_directory(root, source_kind="user")
    api._adventure_resolver = resolver
    api._adventure_loader = resolver
    api._adventure_source_registry = resolver.registry
    api._adventure_dependencies = replace(
        api._adventure_dependencies, adventure_loader=resolver,
        adventure_registry=resolver.registry, adventure_resolver=resolver,
    )
    api._ruleset_registry.set_adventure_resolver(resolver)


async def _gal_game(api, registry, **extra):
    created = await api.create_game(
        "template_world", "GAL", rule_id="gal_narrative", play_mode="gal", solo=True,
        players=[{"character_name": "叙事者", "attributes": {}}], **extra,
    )
    assert created.get("ok"), created
    return created["game_key"], registry.get(api._parse_key(created["game_key"]))


@pytest.mark.asyncio
async def test_ending_proposal_is_not_confirmed_while_the_next_round_is_judged(web_api):
    api, _lorebook, registry, fake_llm, _worlds = web_api
    _gal_rule(api, auto_confirm=True)
    _install_quest(api)
    game_key, inst = await _gal_game(api, registry, adventure_id="user:v2_quest")
    actor = next(iter(inst.players))
    progress = adventure_runtime_state.progress(inst)
    progress["active_nodes"] = ["finale"]
    progress["completed_nodes"] = ["start", "mid"]
    adventure_runtime_state.replace_progress(inst, progress)
    assert adventure_runtime_state.record_completion_suggestion(inst, {
        "format_version": 1, "node_id": "finale", "reason": "story concluded",
        "evidence_rounds": [inst.log[-1]["round"]],
    })

    original_call = fake_llm.call
    mid_round = {}

    async def call(system_prompt, user_message, **kwargs):
        if inst.state == GameState.ACTIVE_JUDGMENT and "settled" not in mid_round:
            # The previous round's request reaches its settlement step now.
            mid_round["settled"] = await api._settle_adventure_completion_for(inst)
            mid_round["ending"] = adventure_runtime_state.progress(inst).get("ending_node_id")
        return await original_call(system_prompt, user_message, **kwargs)

    fake_llm.call = call
    result = await api.submit_action(game_key, actor, "我推开门")

    assert mid_round == {"settled": None, "ending": None}
    assert result["status"] == 200, result
    rounds = [entry.get("round") for entry in inst.log]
    assert rounds == sorted(set(rounds))  # no duplicated / orphan round
    progress = adventure_runtime_state.progress(inst)
    assert progress.get("ending_node_id") == "finale"  # settled after the round committed
    saved = json.loads(Path(registry._save_path(inst.game_key)).read_text(encoding="utf-8"))
    assert saved["modules"]["adventure_runtime"]["progress"].get("ending_node_id") == "finale"

    again = await api.submit_action(game_key, actor, "我推开门")
    assert again["status"] == 409 and again["payload"]["error_code"] == "ADVENTURE_ENDED"
    assert [entry.get("round") for entry in inst.log] == rounds


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["reset_game", "restart_game"])
async def test_new_run_of_a_gal_game_keeps_scene_progression(web_api, operation):
    api, _lorebook, registry, _fake_llm, _worlds = web_api
    _gal_rule(api)
    game_key, inst = await _gal_game(api, registry)
    assert progression_state.mode(inst) == progression_state.SCENE

    await getattr(api, operation)(game_key)

    new_run = registry.get(api._parse_key(game_key))
    assert new_run is not inst
    assert adventure_runtime_state.play_mode(new_run) == "gal"
    assert progression_state.mode(new_run) == progression_state.SCENE


@pytest.mark.asyncio
async def test_seed_restart_of_a_gal_game_validates_and_keeps_scene_progression(web_api):
    api, _lorebook, registry, _fake_llm, _worlds = web_api
    _gal_rule(api)
    _game_key, inst = await _gal_game(api, registry)
    seed = game_config.seed_code(inst)
    players = [{"character_name": "叙事者", "attributes": {}}]
    before = {item.game_key for item in registry.list_all()}

    rejected = await api.create_from_seed(seed, solo=False, players=players)
    assert rejected["ok"] is False and rejected["error_code"] == "GAL_REQUIRES_SOLO"
    assert {item.game_key for item in registry.list_all()} == before

    created = await api.create_from_seed(seed, solo=True, players=players)
    assert created.get("ok"), created
    new_game = registry.get(api._parse_key(created["game_key"]))
    assert adventure_runtime_state.play_mode(new_game) == "gal"
    assert progression_state.mode(new_game) == progression_state.SCENE


@pytest.mark.asyncio
async def test_deterministic_transaction_failure_rejects_the_proposal_and_unblocks_the_queue(tmp_path):
    from src.webui.api import WebAPI
    from tests.test_adventure_v2_runtime import _deps, _install, _instance

    graph = {
        "id": "v2_quest", "format": "diceframe:adventure-graph-v2",
        "chapters": [{"id": "ch1", "name": "c"}],
        "nodes": [
            {"id": "a", "type": "scene", "chapter_id": "ch1", "transitions": [],
             "on_complete": [{"type": "world_op", "op": {"op": "set_fact", "key": "door.open", "value": True}}]},
            {"id": "b", "type": "scene", "chapter_id": "ch1", "transitions": [], "on_complete": []},
        ],
        "start_node_ids": ["a", "b"],
    }
    resolver = _install(tmp_path, graph)
    inst = _instance(resolver)
    deps = _deps(resolver)
    adventure_runtime.initialize_adventure_run(deps, inst)
    # A full world makes node a's world op fail deterministically (WorldStateError).
    facts = inst.world_state.setdefault("facts", {})
    for index in range(512):
        facts[f"k{index}"] = {"value": index, "visibility": "public"}
    inst.log.append({"round": 1, "gm_response": "x", "actions": []})
    for node in ("a", "b"):
        assert adventure_runtime_state.record_completion_suggestion(inst, {
            "format_version": 1, "node_id": node, "reason": "r", "evidence_rounds": [1],
        })

    class Registry:
        def get(self, _key):
            return inst

        async def save(self, _instance):
            return None

    api = WebAPI.__new__(WebAPI)
    api._reg = Registry()
    api._adventure_runtime_dependencies = deps
    api._handler = None
    api._llm_client = None
    api._load_rule_for_game = lambda _instance: SimpleNamespace(adventure_progression=SimpleNamespace(
        auto_confirm_completion=True, activation_narration=False,
    ))

    settled = await api._settle_adventure_completion_for(inst)

    assert settled is not None and settled["node_id"] == "b"
    progress = adventure_runtime_state.progress(inst)
    assert progress["completed_nodes"] == ["b"]
    assert progress.get("completion_suggestions") in (None, [])


@pytest.mark.asyncio
async def test_transition_narration_stays_on_the_round_that_committed():
    """The narration call runs after confirming; a newer round may start meanwhile."""
    from src.engine.game_instance import GameInstance
    from src.webui.services import turns

    instance = GameInstance(game_key=("web", "transition-round", "bot"))
    instance.log.append({"round": 1, "gm_response": "first", "actions": []})
    progression_state.set_round_value(instance, 2)  # round 1 just committed

    async def settle(inst):
        # Another request processes round 2 while the narration is generated.
        inst.log.append({"round": 2, "gm_response": "second", "actions": []})
        progression_state.set_round_value(inst, 3)
        return {"node_id": "mid", "transition_narration": "The tide turns."}

    async def save(_inst):
        return None

    dependencies = SimpleNamespace(settle_adventure_completion=settle, save_instance=save)
    await turns._settle_adventure_completion(dependencies, instance, "web|transition-round|bot")

    assert instance.log[0].get("state_changes") == ["The tide turns."]
    assert "state_changes" not in instance.log[1]
