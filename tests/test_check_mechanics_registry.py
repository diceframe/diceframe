"""通用检定管线的骰制注册接缝：注册实现被委托，内建骰制不受影响。"""

from __future__ import annotations

from typing import Any

import pytest

from src.engine import check_mechanics
from src.engine.check_mechanics import (
    CheckMechanic,
    CheckResolution,
    check_mechanic_for,
    register_check_mechanic,
    request_dice_system,
    supported_dice_systems,
)
from src.engine.checks import resolve_check_request, roll_check_request
from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem


class _FixedMechanic(CheckMechanic):
    dice_system = "zz_fixed"

    def __init__(self) -> None:
        self.validated: list[tuple[list[int], int]] = []

    def request_fields(self, **_kwargs: Any) -> dict[str, Any]:
        return {"zz_extra": 7}

    def roll(self, request: dict[str, Any], rule: Any) -> dict[str, Any]:
        return {"value": 9, "rolls": [4, 5], "critical": False, "fumble": False, "zz_roll": True}

    def validate_roll(self, request: dict[str, Any], rule: Any, rolls: list[int], roll_value: int) -> None:
        self.validated.append((list(rolls), roll_value))
        if roll_value != sum(rolls):
            raise ValueError("zz mismatch")

    def resolve(self, resolution: CheckResolution) -> dict[str, Any]:
        resolution.common.update({
            "verdict": "成功",
            "total": resolution.roll_value + resolution.circumstance_modifier,
            "label_for_dex": resolution.attribute_label("dex"),
        })
        return resolution.common


@pytest.fixture
def fixed_mechanic():
    mechanic = _FixedMechanic()
    register_check_mechanic(mechanic)
    try:
        yield mechanic
    finally:
        check_mechanics._MECHANICS.pop(mechanic.dice_system, None)


def _rule_template(dice_system: str) -> dict[str, Any]:
    return {
        "rule_id": "zz_rule",
        "rule_name": "ZZ",
        "dice_system": dice_system,
        "attributes": [{"key": "dex", "name": "敏捷", "min": 1, "max": 20}],
    }


def _instance() -> GameInstance:
    return GameInstance(
        game_key=("test", "mechanics", "bot"),
        players={
            "u1": {
                "character_name": "Ari",
                "character_sheet": {"hp": 10, "max_hp": 10, "attributes": {"dex": 12}, "skills": []},
            },
        },
    )


@pytest.mark.parametrize("builtin", ["d20", "d100", "none"])
def test_builtin_dice_systems_cannot_be_replaced(builtin: str) -> None:
    mechanic = _FixedMechanic()
    mechanic.dice_system = builtin
    with pytest.raises(ValueError):
        register_check_mechanic(mechanic)
    assert check_mechanic_for(builtin) is None


def test_mechanic_without_id_is_rejected() -> None:
    mechanic = _FixedMechanic()
    mechanic.dice_system = ""
    with pytest.raises(ValueError):
        register_check_mechanic(mechanic)


def test_unregistered_dice_system_fails_closed_when_loading_a_rule() -> None:
    with pytest.raises(ValueError):
        RuleSystem(_rule_template("zz_fixed"))


def test_registered_dice_system_is_supported_and_stamped_on_requests(fixed_mechanic) -> None:
    assert "zz_fixed" in supported_dice_systems()
    assert request_dice_system("zz_fixed") == "zz_fixed"
    assert request_dice_system("ZZ_Fixed") == "zz_fixed"
    rule = RuleSystem(_rule_template("zz_fixed"))
    assert rule.dice_system == "zz_fixed"
    # 模板未声明 check_mechanic 时取注册实现的默认描述。
    assert rule.check_mechanic["dice"] == "zz_fixed"


def test_request_dice_system_keeps_builtin_mapping() -> None:
    assert request_dice_system("d100") == "d100"
    assert request_dice_system("d20") == "d20"
    # 未注册的值沿用历史行为：按 d20 处理。
    assert request_dice_system("") == "d20"
    assert request_dice_system("unknown") == "d20"


def test_roll_and_resolve_are_delegated_to_the_registered_mechanic(fixed_mechanic) -> None:
    rule = RuleSystem(_rule_template("zz_fixed"))
    instance = _instance()
    request = {
        "check_id": "c1", "actor_uid": "u1", "dice_system": "zz_fixed",
        "attribute": "dex", "circumstance_modifier": 3,
    }

    rolled = roll_check_request(request, rule)
    assert rolled == {
        "ok": True, "check_id": "c1", "dice_system": "zz_fixed",
        "value": 9, "rolls": [4, 5], "critical": False, "fumble": False, "zz_roll": True,
    }

    action = {
        "user_id": "u1", "text": "x", "check_request": request,
        "dice_value": rolled["value"], "dice_rolls": rolled["rolls"],
    }
    result = resolve_check_request(instance, action, rule)
    assert fixed_mechanic.validated == [([4, 5], 9)]
    assert result["dice"] == "zz_fixed"
    assert result["verdict"] == "成功"
    assert result["total"] == 12
    assert result["label_for_dex"] == "敏捷"
    # 注册骰制自己决定是否存在优势/劣势语义；通用层不替它套用 d20 的规则。
    assert result["advantage_mode"] == ""


def test_registered_mechanic_validates_client_supplied_rolls(fixed_mechanic) -> None:
    rule = RuleSystem(_rule_template("zz_fixed"))
    request = {"check_id": "c1", "actor_uid": "u1", "dice_system": "zz_fixed", "attribute": "dex"}
    action = {"user_id": "u1", "check_request": request, "dice_value": 10, "dice_rolls": [4, 5]}
    with pytest.raises(ValueError, match="zz mismatch"):
        resolve_check_request(_instance(), action, rule)


def test_builtin_d20_validation_is_unchanged() -> None:
    rule = RuleSystem(_rule_template("d20"))
    request = {"check_id": "c1", "actor_uid": "u1", "dice_system": "d20", "attribute": "dex", "target": 10}
    action = {"user_id": "u1", "check_request": request, "dice_value": 21, "dice_rolls": [21]}
    with pytest.raises(ValueError):
        resolve_check_request(_instance(), action, rule)
    action = {"user_id": "u1", "check_request": request, "dice_value": 15, "dice_rolls": [15]}
    result = resolve_check_request(_instance(), action, rule)
    assert result["dice"] == "d20"
    assert result["roll"] == 15
