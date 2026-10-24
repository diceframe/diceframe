"""GAL 轨道 P10：样板内容包 + 全链端到端验收。

`lighthouse_summer` 是第一个内置 Adventure v2 包（gal 样板路线）。本文件把
P2/P3（好感槽 + 结算）、P4（gates / ending 冻结）、P6（权威完成事务）、
P7（scene 推进 + 终局守卫）与新的 `affection.score` gate 串联成一次整链
测试：好感结算 → 路线门解锁 → 抉择分支 → ending → 推进冻结。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.adventures.bundle import AdventureBundleLoader
from src.adventures.progress import ending_node_id
from src.commands.affection_effects import settle_affection
from src.engine import progression
from src.engine.game_instance import GameInstance
from src.engine.modules import adventure_runtime_state, progression_state
from src.rules.rule_system import RuleSystem
from src.webui.services import adventure_runtime

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_ADVENTURES = ROOT / "templates" / "adventures"
ADVENTURE_ID = "core:lighthouse_summer"
HEROINE = "林晚晴"


def _sample_graph() -> dict:
    bundle = AdventureBundleLoader(TEMPLATES_ADVENTURES).load("lighthouse_summer", "zh-CN")
    return bundle.adventure


def _runtime_deps():
    from src.adventures import AdventureResolver
    from tests.test_adventure_v2_runtime import _deps

    resolver = AdventureResolver.single_directory(
        TEMPLATES_ADVENTURES, source_kind="builtin",
    )
    return resolver, _deps(resolver)


def make_gal_instance(resolver) -> GameInstance:
    from src.engine.modules import game_config

    instance = GameInstance(
        game_key=("web", "gal-sample", "bot"),
        world_id="horizon_strait", rule_id="gal_narrative",
        language="zh-CN", gm_uid="gm",
    )
    binding = resolver.resolve_with_source(ADVENTURE_ID, "zh-CN").binding("horizon_strait")
    assert instance.bind_adventure(binding) is True
    # 创建流语义（P7）：gal 玩法安装 scene 推进；gal_narrative 皮启用
    # affection（P3）与自动确认（P6）由规则声明，无需在此重复设置。
    game_config.replace_solo_mode(instance, True)
    progression_state.set_mode(instance, progression_state.SCENE)
    instance.npcs[HEROINE] = {"name": HEROINE, "character_name": HEROINE}
    return instance


# ---- 内容契约 -----------------------------------------------------------------


@pytest.mark.parametrize("locale", ["zh-CN", "en"])
def test_sample_package_loads_in_both_locales(locale):
    bundle = AdventureBundleLoader(TEMPLATES_ADVENTURES).load("lighthouse_summer", locale)
    graph = bundle.adventure
    endings = [node for node in graph["nodes"] if node["type"] == "ending"]
    assert len(endings) == 3
    decision = next(node for node in graph["nodes"] if node["id"] == "n_confession")
    assert decision["choice_mode"] == "exclusive"
    confess = next(t for t in decision["transitions"] if t["choice_id"] == "confess")
    assert {"type": "affection.score", "id": HEROINE, "value": 40} in confess["conditions"]
    assert {t["choice_id"] for t in decision["transitions"]} == {
        "confess", "stay_friends", "drift_apart",
    }


def test_sample_world_ships_the_heroine_lorebook():
    import json

    data = json.loads((ROOT / "templates" / "worlds" / "horizon_strait.json").read_text(encoding="utf-8"))
    assert data["world_id"] == "horizon_strait"
    entries = {entry["name"] for entry in data["starter_lorebook"]}
    assert HEROINE in entries


# ---- 全链端到端 ---------------------------------------------------------------


def _gal_rule():
    return RuleSystem.load(ROOT / "templates" / "rules" / "gal_narrative.json")


def _settle_affection(instance, total: str) -> None:
    rule = _gal_rule()
    applied = settle_affection(instance, [
        {"subject": HEROINE, "delta": total, "reason": "样板测试结算"},
    ], rule.affection)
    assert applied, "affection settlement must apply on the gal skin"


def test_full_gal_chain_high_affection_reaches_the_warm_ending():
    resolver, runtime_deps = _runtime_deps()
    instance = make_gal_instance(resolver)
    adventure_runtime.initialize_adventure_run(runtime_deps, instance)
    progress = adventure_runtime_state.progress(instance)
    assert progress["active_nodes"] == ["n_arrival"]  # world_seed 已物化

    # P3：叙事提议结算好感（gal 皮 max_delta_per_round=5，50 分需多次钳制结算）。
    for _ in range(10):
        _settle_affection(instance, "+5")
    from src.engine.modules import affection

    assert affection.score(instance, HEROINE) == 50

    # P4/P6：权威完成事务推进主线；n_festival 完成时按好感门评估后继。
    for node_id in ("n_arrival", "n_lighthouse"):
        adventure_runtime.complete_adventure_node(runtime_deps, instance, node_id)
    assert "n_festival" in adventure_runtime_state.progress(instance)["active_nodes"]

    adventure_runtime.complete_adventure_node(runtime_deps, instance, "n_festival")
    progress = adventure_runtime_state.progress(instance)
    assert progress["active_nodes"] == ["n_confession"]  # 抉择节点唯一开放

    # 抉择：说出那句话 → 温暖结局。
    adventure_runtime.complete_adventure_node(
        runtime_deps, instance, "n_confession", choice_id="confess",
    )
    adventure_runtime.complete_adventure_node(runtime_deps, instance, "n_warm_end")

    progress = adventure_runtime_state.progress(instance)
    assert ending_node_id(progress) == "n_warm_end"  # P4：达成即终局
    assert progress["active_nodes"] == []

    # P7：终局后 scene 推进被拒绝。
    with pytest.raises(ValueError, match="has ended"):
        progression.ensure_advancement_allowed(instance)


def test_full_gal_chain_low_affection_locks_the_confession_choice():
    from src.adventures.progress import ProgressError

    resolver, runtime_deps = _runtime_deps()
    instance = make_gal_instance(resolver)
    adventure_runtime.initialize_adventure_run(runtime_deps, instance)

    # 好感不足（0 < 40）：告白选择被引擎拒绝（fail closed 的路线锁）。
    for node_id in ("n_arrival", "n_lighthouse", "n_festival"):
        adventure_runtime.complete_adventure_node(runtime_deps, instance, node_id)
    assert adventure_runtime_state.progress(instance)["active_nodes"] == ["n_confession"]

    with pytest.raises(ProgressError):
        adventure_runtime.complete_adventure_node(
            runtime_deps, instance, "n_confession", choice_id="confess",
        )
    # 状态未被破坏：抉择节点仍开放，只能走不依赖好感的选项。
    assert adventure_runtime_state.progress(instance)["active_nodes"] == ["n_confession"]

    adventure_runtime.complete_adventure_node(
        runtime_deps, instance, "n_confession", choice_id="stay_friends",
    )
    adventure_runtime.complete_adventure_node(runtime_deps, instance, "n_quiet_end")
    assert ending_node_id(adventure_runtime_state.progress(instance)) == "n_quiet_end"


def test_affection_gate_fails_closed_without_a_score():
    """分数未知（未结算过该对象）= 证据不足 = 不放行。"""

    from src.adventures.gates import gates_satisfied

    resolver, _ = _runtime_deps()
    instance = make_gal_instance(resolver)  # 未做任何好感结算
    gate = [{"type": "affection.score", "id": HEROINE, "value": 40}]
    assert gates_satisfied(gate, instance=instance, progress={}, rules_evaluator=None) is False
