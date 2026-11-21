"""G16：GM 用一条裁定原语施加 / 移除规则书里的状态。

状态不是"一个数字"，它是规则书里的一个条目：GM 必须先知道它是什么，才能
在桌上裁它。所以这里钉两件事——只有规则书状态能被手工施加（来源绑定效应
不行），以及施加/移除走的事件与法术施加条件完全一致。
"""

from __future__ import annotations

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat.check_modes import condition_flags
from src.rulesets.dnd2024.combat.primitives import INTENT_TYPES

from dnd2024_monk_common import SequenceRng
from test_dnd2024_combat import _instance, _start


def _state(instance):
    return ruleset_runtime.state(instance)


def _combat():
    engine, instance = _instance()
    _start(engine, instance)
    return engine, instance


def _apply(engine, instance, resolved: dict) -> list[dict]:
    assert resolved["ok"] is True, resolved
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return resolved["event_batch"]["events"]


def _declare(
    engine, instance, *, condition: str, action: str = "apply",
    target_id: str = "enemy:goblin-1", submitted_by: str = "gm",
) -> dict:
    state = _state(instance)
    return engine.resolve_intent(instance, {
        "intent_id": f"gm-cond-{condition}-{action}-{state['version']}-{target_id}",
        "type": "gm_condition",
        "expected_version": int(state.get("version", 0) or 0),
        "submitted_by": submitted_by,
        "target_id": target_id,
        "condition": condition,
        "action": action,
    }, SequenceRng([]))


def _actor_conditions(instance, actor_id: str) -> dict:
    """直接读权威状态，不读视图——视图只反映基础值，会让"没生效"假绿。"""

    combat = _state(instance)["combat"]
    if actor_id.startswith("enemy:"):
        enemy = (combat.get("enemies") or {}).get(actor_id.removeprefix("enemy:")) or {}
        return enemy.get("conditions") or {}
    raw = actor_id.removeprefix("player:")
    sheet = instance.players[raw].get("character_sheet") or {}
    canonical = sheet.get("ruleset_character") or sheet
    return canonical.get("conditions") or {}


def _flags(engine, instance, actor_id: str) -> dict:
    return condition_flags(engine.catalog.conditions, _actor_conditions(instance, actor_id))


def test_the_catalog_separates_states_from_source_bound_effects():
    engine, _instance_ = _combat()
    appliable = engine.catalog.gm_appliable_conditions()

    # 14 个无等级 SRD 状态（Exhaustion 后置）。
    assert set(appliable) == {
        "blinded", "charmed", "deafened", "frightened", "grappled", "incapacitated",
        "invisible", "paralyzed", "petrified", "poisoned", "prone", "restrained",
        "stunned", "unconscious",
    }
    # 来源绑定效应仍在运行时注册表里（解释器要读它们的 flag），但不可手工施加。
    assert "blessed" in engine.catalog.conditions
    assert engine.catalog.conditions["blessed"]["kind"] == "effect"
    assert "blessed" not in appliable


def test_apply_a_state_and_the_ledger_shows_the_gm_as_its_source():
    engine, instance = _combat()
    events = _apply(engine, instance, _declare(engine, instance, condition="poisoned"))

    applied = [event for event in events if event["type"] == "condition.applied"]
    assert applied and applied[0]["source"] == "gm"
    assert applied[0]["condition"] == "poisoned"
    assert _actor_conditions(instance, "enemy:goblin-1")["poisoned"]["source"] == "gm"


def test_grappled_and_restrained_become_speed_zero():
    for condition in ("grappled", "restrained"):
        engine, instance = _combat()
        _apply(engine, instance, _declare(engine, instance, condition=condition))
        assert _flags(engine, instance, "enemy:goblin-1")["speed_zero"] is True


def test_petrified_implies_incapacitation_and_auto_failed_str_dex_saves():
    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, condition="petrified"))

    flags = _flags(engine, instance, "enemy:goblin-1")
    assert flags["incapacitated"] is True
    assert flags["auto_fail_saves"] == {"str", "dex"}


def test_a_player_target_works_too():
    engine, instance = _combat()
    _apply(engine, instance, _declare(
        engine, instance, condition="frightened", target_id="player:gm",
    ))

    assert "frightened" in _actor_conditions(instance, "player:gm")


def test_only_the_gm_can_declare_a_condition():
    engine, instance = _combat()
    result = _declare(engine, instance, condition="poisoned", submitted_by="p1")

    assert result["ok"] is False
    assert "only the GM" in result["error"]


def test_a_source_bound_effect_cannot_be_handed_out():
    engine, instance = _combat()
    result = _declare(engine, instance, condition="blessed")

    assert result["ok"] is False
    assert "not a condition the GM can apply" in result["error"]


def test_unknown_condition_is_refused():
    engine, instance = _combat()
    result = _declare(engine, instance, condition="not_a_condition")

    assert result["ok"] is False


def test_remove_can_clear_a_source_bound_effect():
    engine, instance = _combat()
    # 模拟法术产物：效应由来源写入，不是 GM 施加的。
    combat = _state(instance)["combat"]
    combat["enemies"]["goblin-1"]["conditions"]["blurred"] = {"source": "spell"}

    _apply(engine, instance, _declare(
        engine, instance, condition="blurred", action="remove",
    ))

    assert "blurred" not in _actor_conditions(instance, "enemy:goblin-1")


def test_repeat_apply_refreshes_instead_of_stacking():
    engine, instance = _combat()
    _apply(engine, instance, _declare(engine, instance, condition="poisoned"))
    _apply(engine, instance, _declare(engine, instance, condition="poisoned"))

    conditions = _actor_conditions(instance, "enemy:goblin-1")
    assert list(conditions).count("poisoned") == 1
    assert conditions["poisoned"]["source"] == "gm"


def test_bad_action_missing_target_and_non_actor_are_refused():
    engine, instance = _combat()

    bad_action = _declare(engine, instance, condition="poisoned", action="grant")
    assert bad_action["ok"] is False and "apply or remove" in bad_action["error"]

    assert _declare(engine, instance, condition="poisoned", target_id="enemy:nobody")["ok"] is False
    assert _declare(engine, instance, condition="poisoned", target_id="table")["ok"] is False


def test_the_intent_is_part_of_the_combat_contract():
    assert "gm_condition" in INTENT_TYPES


def test_every_gm_state_carries_a_name_and_a_rules_summary():
    engine, _instance_ = _combat()
    catalog = engine.catalog.condition_catalog()

    assert len(catalog) == 14
    for row in catalog:
        assert row["name"] and row["name"] != row["id"], row["id"]
        assert len(row["summary"]) > 10, row["id"]
        assert row["effects"], row["id"]


def test_the_definition_carries_the_flags_the_engine_will_use():
    engine, _instance_ = _combat()
    definition = engine.catalog.condition_definition("restrained")

    assert definition is not None
    assert definition["effects"]["speed_zero"] is True
    # 效应不是规则书状态，没有定义可读。
    assert engine.catalog.condition_definition("blessed") is None


def test_only_the_gm_view_carries_the_catalog():
    engine, instance = _combat()

    gm_view = engine.gameplay_view(instance, viewer_id="gm", viewer_is_gm=True)
    player_view = engine.gameplay_view(instance, viewer_id="p1", viewer_is_gm=False)

    assert len(gm_view["combat"]["condition_catalog"]) == 14
    assert player_view["combat"]["condition_catalog"] == []


def test_both_locales_localise_every_state():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "templates/rulesets/dnd2024_srd/locales"
    for locale in ("en", "zh-CN"):
        labels = json.loads(
            (root / locale / "combat_catalogs/srd_combat_core.json").read_text(
                encoding="utf-8"
            )
        )["fields"]["labels"]
        for condition_id in (
            "blinded", "charmed", "deafened", "frightened", "grappled",
            "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
            "prone", "restrained", "stunned", "unconscious",
        ):
            assert labels.get(f"condition.{condition_id}"), (locale, condition_id)
            assert labels.get(f"condition.{condition_id}.summary"), (locale, condition_id)


def test_the_effects_live_in_their_own_section_with_a_named_source():
    engine, _instance_ = _combat()
    catalog = engine.catalog

    assert len(catalog.effects) == 15
    for effect_id, entry in catalog.effects.items():
        assert entry["kind"] == "effect"
        assert str(entry.get("source_ref") or ""), effect_id
    # 合并视图仍包含它们：解释器按 flag 取值，行为不变。
    assert "blessed" in catalog.conditions and "blurred" in catalog.conditions


def test_effects_are_found_from_their_source_without_a_stored_back_pointer():
    engine, _instance_ = _combat()
    catalog = engine.catalog

    assert catalog.effects_for_source("feature:brutal_strike") == ["hamstrung"]
    assert catalog.effects_for_source("feature:stunning_strike") == [
        "staggered", "staggered_opening",
    ]
    assert catalog.effects_for_source("mastery:vex") == ["next_attack_advantage"]
    assert catalog.effects_for_source("nobody:none") == []
