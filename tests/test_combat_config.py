"""规则模板 combat 声明解析的契约测试（Issue 212 phase 2 Slice 2.1）。"""

from __future__ import annotations

import pytest

from src.engine.combat_config import (
    CombatConfigError,
    combat_extension_from_template,
)


def _template(combat: dict | None) -> dict:
    template: dict = {"rule_id": "freeform_wuxia", "dice_system": "d20"}
    if combat is not None:
        template["combat"] = combat
    return template


def _valid_combat() -> dict:
    return {
        "scheduler": {"kind": "threshold", "gauge": "action_gauge",
                      "speed": "action_speed", "threshold": 100},
        "resources": [
            {"id": "hp", "source": "hp"},
            {"id": "qi", "source": "special_stat", "stat": "qi", "maximum": 100},
            {"id": "barrier", "source": "combat_state", "maximum": 50,
             "damage_priority": "before_hp", "damage_types": ["all"]},
        ],
        "actions": [
            {"id": "item:healing_pill.use", "kind": "consumable", "name": "回春丹",
             "effects": [{"kind": "resource_change", "resource": "hp",
                          "amount": {"op": "add", "args": [
                              {"op": "constant", "value": 10},
                              {"op": "attribute", "id": "wis"}]}}]},
            {"id": "ability:qi_palm", "kind": "ability", "name": "内力掌",
             "costs": [{"resource": "qi", "amount": {"op": "constant", "value": 8}}],
             "effects": [{"kind": "damage", "amount": {
                 "op": "multiply", "args": [
                     {"op": "attribute", "id": "str"}, {"op": "constant", "value": 2}]},
                 "damage_type": "bludgeoning"}]},
        ],
    }


def test_no_combat_block_returns_none() -> None:
    """没有 combat 块 = 保持原有玩法；出现 speed 字段也不会自动启用 ATB。"""
    assert combat_extension_from_template(_template(None)) is None


def test_valid_declaration_parses() -> None:
    config = combat_extension_from_template(_template(_valid_combat()))
    assert config is not None
    assert config.scheduler is not None and config.scheduler.kind == "threshold"
    assert config.resource_ids == {"hp", "qi", "barrier"}
    assert config.hp_resource == "hp"
    assert config.barrier_resources == {"barrier": frozenset({"all"})}
    palm = config.action("ability:qi_palm")
    assert palm is not None and palm.costs[0].resource == "qi"
    assert config.action("spell:fireball") is None


def test_unknown_scheduler_kind_fails_closed() -> None:
    combat = _valid_combat()
    combat["scheduler"] = {"kind": "xianxia_atb"}
    with pytest.raises(Exception, match="unknown scheduler kind"):
        combat_extension_from_template(_template(combat))


def test_resources_fail_closed() -> None:
    base = _valid_combat()
    # 没有 hp 池：拒绝。
    combat = {**base, "resources": [r for r in base["resources"] if r["id"] != "hp"]}
    with pytest.raises(CombatConfigError, match="hp source pool"):
        combat_extension_from_template(_template(combat))
    # 重复 id：拒绝。
    combat = {**base, "resources": [*base["resources"], base["resources"][1]]}
    with pytest.raises(CombatConfigError, match="unique"):
        combat_extension_from_template(_template(combat))
    # special_stat 缺 stat：拒绝。
    combat = {**base, "resources": [
        {"id": "hp", "source": "hp"},
        {"id": "qi", "source": "special_stat"},
    ]}
    with pytest.raises(CombatConfigError, match="requires stat"):
        combat_extension_from_template(_template(combat))
    # 未知来源：拒绝。
    combat = {**base, "resources": [
        {"id": "hp", "source": "hp"},
        {"id": "qi", "source": "character_field"},
    ]}
    with pytest.raises(CombatConfigError, match="unknown source"):
        combat_extension_from_template(_template(combat))


def test_actions_reference_declared_resources_only() -> None:
    base = _valid_combat()
    base["actions"] = [{
        "id": "ability:bad", "kind": "ability", "name": "坏招",
        "costs": [{"resource": "qigong", "amount": {"op": "constant", "value": 1}}],
        "effects": [{"kind": "damage", "amount": {"op": "constant", "value": 1}}],
    }]
    with pytest.raises(CombatConfigError, match="undeclared resource"):
        combat_extension_from_template(_template(base))


def test_actions_require_known_effect_kinds() -> None:
    base = _valid_combat()
    base["actions"] = [{
        "id": "ability:bad", "kind": "ability", "name": "坏招",
        "effects": [{"kind": "fireball", "amount": {"op": "constant", "value": 1}}],
    }]
    with pytest.raises(CombatConfigError, match="unknown effect kind"):
        combat_extension_from_template(_template(base))


def test_broken_combat_block_type_fails_closed() -> None:
    with pytest.raises(CombatConfigError, match="must be an object"):
        combat_extension_from_template(_template("threshold"))
