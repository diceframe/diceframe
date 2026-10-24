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
    """v0 的 GAL_CANNOT_BIND_ADVENTURE 限制已解除（结构化形态就位）。"""

    import json

    api, _lorebook, _registry, _fake_llm, _worlds_dir = web_api
    (api._rules_dir / "gal_narrative.json").write_text(
        json.dumps({
            "rule_id": "gal_narrative",
            "rule_name": "GAL",
            "dice_system": "none",
            "mechanics": "narrative_only",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    result = await api.create_game(
        "template_world", "GAL 绑图", rule_id="gal_narrative", play_mode="gal",
        solo=True, adventure_id="user:some_quest",
        players=[{"character_name": "叙事者", "attributes": {}}],
    )
    # 冒险包可能不存在而创建失败，但绝不能是旧的玩法限制。
    assert result.get("error_code") != "GAL_CANNOT_BIND_ADVENTURE"
