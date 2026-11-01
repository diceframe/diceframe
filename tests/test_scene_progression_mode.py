"""GAL 轨道 P7：scene 推进模式。

契约（ADR-0010 §4）：
1) `scene` 进 `KNOWN_MODES`；round 字段继续作为推进计数（经济纪元同点）；
   旧运行时对 scene 存档按未知 mode fail closed；
2) scene 固定单人推进语义（不依赖 solo_mode 配置）；
3) scene 模式下冒险抵达结局（P4 `ending_node_id`）后不再开启推进；
   narrative_round 跑团行为不变。
"""

from __future__ import annotations

import pytest

from webapi_harness import web_api
from src.engine import progression
from src.engine.game_instance import GameInstance
from src.engine.modules import adventure_runtime_state, progression_state
from src.engine import turn_state


def make_instance(solo: bool = True) -> GameInstance:
    instance = GameInstance(game_key=("web", "scene-mode", "bot"), gm_uid="gm")
    instance.put_player("actor", {
        "user_id": "actor", "character_name": "actor",
        "character_sheet": {"hp": 10, "max_hp": 10},
    })
    if solo:
        from src.engine.modules import game_config

        game_config.replace_solo_mode(instance, True)
    return instance


def reach_ending(instance: GameInstance) -> None:
    adventure_runtime_state.replace_progress(instance, {
        "schema_version": 1,
        "active_nodes": [],
        "completed_nodes": ["finale"],
        "history": [{"kind": "ending_reached", "id": "finale"}],
        "ending_node_id": "finale",
    })


# ---- 词表与安装 ---------------------------------------------------------------


def test_scene_enters_the_known_modes_vocabulary():
    assert progression_state.SCENE in progression_state.KNOWN_MODES
    assert progression_state.NARRATIVE_ROUND in progression_state.KNOWN_MODES


def test_set_mode_accepts_known_values_and_fails_closed_on_unknown():
    inst = make_instance()
    progression_state.set_mode(inst, progression_state.SCENE)
    assert progression_state.mode(inst) == "scene"

    from src.engine.module_state import ModuleStateError

    with pytest.raises(ModuleStateError):
        progression_state.set_mode(inst, "werewolf")
    assert progression_state.mode(inst) == "scene"


def test_ensure_normalizes_scene_rounds_and_preserves_unknown_modes():
    slot = {"schema_version": 1, "mode": "scene", "round": "bogus"}
    normalized = progression_state.ensure(slot)
    assert normalized["mode"] == "scene" and normalized["round"] == 0

    legacy = {"schema_version": 1, "mode": "some_future_mode", "round": 7}
    untouched = progression_state.ensure(legacy)
    assert untouched is legacy  # 未知 mode 原样保留：旧运行时 fail closed 的根


# ---- 推进语义 -----------------------------------------------------------------


def test_scene_advances_on_any_action_without_solo_config():
    inst = make_instance(solo=False)
    progression_state.set_mode(inst, progression_state.SCENE)
    inst.action_queue.append({"user_id": "actor", "text": "你好"})
    assert turn_state.should_advance(inst) is True

    # narrative_round + 非 solo：维持多人就绪屏障（行为不变）
    tabletop = make_instance(solo=False)
    tabletop.action_queue.append({"user_id": "actor", "text": "我动"})
    assert turn_state.should_advance(tabletop) is False


def test_ending_blocks_scene_advancement_but_not_narrative_rounds():
    scene = make_instance()
    progression_state.set_mode(scene, progression_state.SCENE)
    reach_ending(scene)
    with pytest.raises(ValueError, match="has ended"):
        progression.ensure_advancement_allowed(scene)

    # 跑团局：ending 冻结不拦回合推进（行为不变）
    tabletop = make_instance()
    reach_ending(tabletop)
    progression.ensure_advancement_allowed(tabletop)  # no raise

    # scene 但未结局：放行
    fresh_scene = make_instance()
    progression_state.set_mode(fresh_scene, progression_state.SCENE)
    progression.ensure_advancement_allowed(fresh_scene)  # no raise


def test_start_round_locked_refuses_to_open_after_ending():
    scene = make_instance()
    progression_state.set_mode(scene, progression_state.SCENE)
    reach_ending(scene)
    with pytest.raises(ValueError, match="has ended"):
        turn_state.start_round_locked(scene)
    assert progression_state.round_value(scene) == 0  # 计数未被推进

    # 结局前正常推进
    fresh_scene = make_instance()
    progression_state.set_mode(fresh_scene, progression_state.SCENE)
    turn_state.start_round_locked(fresh_scene)
    assert progression_state.round_value(fresh_scene) == 1


def test_scene_era_follows_the_advance_counter():
    inst = make_instance()
    progression_state.set_mode(inst, progression_state.SCENE)
    turn_state.start_round_locked(inst)
    turn_state.start_round_locked(inst)
    assert progression.current_era(inst) == 2


# ---- 创建流 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gal_creation_installs_scene_mode(web_api):
    import json

    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    (api._rules_dir / "gal_narrative.json").write_text(
        json.dumps({
            "rule_id": "gal_narrative",
            "rule_name": "GAL",
            "dice_system": "none",
            "mechanics": "narrative_only",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    created = await api.create_game(
        "template_world", "GAL scene", rule_id="gal_narrative", play_mode="gal",
        solo=True, players=[{"character_name": "叙事者", "attributes": {}}],
    )
    assert created.get("ok"), created
    inst = registry.get(api._parse_key(created["game_key"]))
    assert progression_state.mode(inst) == "scene"

    # 跑团创建仍是 narrative_round（行为不变）
    tabletop = await api.create_game(
        "template_world", "跑团",
        players=[{"character_name": "冒险者", "attributes": {"str": 10}}],
    )
    assert tabletop.get("ok"), tabletop
    t_inst = registry.get(api._parse_key(tabletop["game_key"]))
    assert progression_state.mode(t_inst) == "narrative_round"


@pytest.mark.asyncio
async def test_gal_creation_no_longer_refuses_adventure_binding(web_api):
    """GAL + a real Adventure v2 package reaches the resolver and is created."""

    import json
    from dataclasses import replace

    from src.adventures import AdventureResolver
    from tests.test_adventure_v2_runtime import _graph, _package_files

    api, _lorebook, registry, _fake_llm, _worlds_dir = web_api
    (api._rules_dir / "gal_narrative.json").write_text(
        json.dumps({
            "rule_id": "gal_narrative",
            "rule_name": "GAL",
            "dice_system": "none",
            "mechanics": "narrative_only",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    adventure_root = api._reg.save_dir.parent / "test-adventures"
    package = adventure_root / "v2_quest"
    files = _package_files(_graph())
    manifest = json.loads(files["manifest.json"])
    manifest["required_runtime"] = {"id": "core:adventure", "minimum_version": 1}
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")
    for name, payload in files.items():
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    resolver = AdventureResolver.single_directory(adventure_root, source_kind="user")
    api._adventure_resolver = resolver
    api._adventure_loader = resolver
    api._adventure_source_registry = resolver.registry
    api._adventure_dependencies = replace(
        api._adventure_dependencies,
        adventure_loader=resolver,
        adventure_registry=resolver.registry,
        adventure_resolver=resolver,
    )
    api._ruleset_registry.set_adventure_resolver(resolver)

    result = await api.create_game(
        "template_world", "GAL 绑图", rule_id="gal_narrative", play_mode="gal",
        solo=True, adventure_id="user:v2_quest",
        players=[{"character_name": "叙事者", "attributes": {}}],
    )
    assert result.get("ok"), result
    assert result["adventure_binding"]["format"] == "diceframe:adventure-graph-v2"
    inst = registry.get(api._parse_key(result["game_key"]))
    assert progression_state.mode(inst) == "scene"

    # An incompatible v1 package fails closed before a registry/save entry is
    # materialized, and never falls back to the removed GAL rejection.
    bad_package = adventure_root / "bad_v1"
    bad_files = dict(files)
    bad_manifest = dict(manifest)
    bad_manifest.update({"adventure_id": "user:bad_v1", "format": "diceframe:adventure-graph-v1"})
    bad_files["manifest.json"] = json.dumps(bad_manifest).encode("utf-8")
    for name, payload in bad_files.items():
        target = bad_package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    before = {item.game_key for item in registry.list_all()}
    rejected = await api.create_game(
        "template_world", "GAL bad", rule_id="gal_narrative", play_mode="gal",
        solo=True, adventure_id="user:bad_v1",
        players=[{"character_name": "叙事者", "attributes": {}}],
    )
    assert rejected["error_code"] == "INCOMPATIBLE_ADVENTURE"
    assert {item.game_key for item in registry.list_all()} == before


# ---- 结局后的行动入口 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_ended_scene_refuses_player_action_before_it_enters_any_queue(web_api):
    """Settlement confirms the ending after the next advance was already opened.

    The table therefore sits in ACTIVE_ACTION with a reached ending.  A player
    submission must be refused at admission: nothing enters action_queue /
    pending_actions and no narrator round runs.
    """

    import json

    from src.engine.game_instance import GameState

    api, _lorebook, registry, fake_llm, _worlds_dir = web_api
    (api._rules_dir / "gal_narrative.json").write_text(
        json.dumps({
            "rule_id": "gal_narrative",
            "rule_name": "GAL",
            "dice_system": "none",
            "mechanics": "narrative_only",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    created = await api.create_game(
        "template_world", "GAL ended", rule_id="gal_narrative", play_mode="gal",
        solo=True, players=[{"character_name": "叙事者", "attributes": {}}],
    )
    assert created.get("ok"), created
    inst = registry.get(api._parse_key(created["game_key"]))
    actor_uid = next(iter(inst.players))
    reach_ending(inst)
    assert inst.state == GameState.ACTIVE_ACTION
    round_before = progression_state.round_value(inst)
    calls_before = len(fake_llm.calls)

    result = await api.submit_action(created["game_key"], actor_uid, "我还想继续聊")

    assert result["status"] == 409, result
    assert result["payload"]["error_code"] == "ADVENTURE_ENDED"
    assert inst.action_queue == [] and inst.pending_actions == []
    assert inst.state == GameState.ACTIVE_ACTION
    assert progression_state.round_value(inst) == round_before
    assert len(fake_llm.calls) == calls_before
