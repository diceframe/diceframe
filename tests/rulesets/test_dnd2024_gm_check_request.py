"""G4：GM 发起的技能/属性检定，结算在规则集里。

通用服务（`manual_rolls`）只负责"存请求 / 转发 / 存结果"；d20 + 修正 + 比 DC
是 D&D 的事实，所以由 `Dnd2024Runtime.resolve_ability_check` 回答。修正直接读
角色卡已派生的 `proficiencies.skill_values`——它已含属性、熟练与专精。
"""

from __future__ import annotations

import asyncio

from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.services.manual_rolls import ManualRollDependencies, ManualRollService

from dnd2024_monk_common import SequenceRng
from test_dnd2024_combat import _instance


def _resolve(instance, check, die=10):
    return Dnd2024Runtime().resolve_ability_check(
        instance, "gm", check, rng=SequenceRng([die]),
    )


def test_an_ability_check_uses_the_ability_modifier():
    _engine, instance = _instance()

    # stalwart_guardian 的 str 是 17 → +3。
    result = _resolve(instance, {"ability": "str", "dc": 13}, die=10)

    assert result["modifier"] == 3
    assert result["total"] == 13
    assert result["success"] is True
    assert result["natural"] == 10


def test_a_proficient_skill_carries_its_proficiency_bonus():
    _engine, instance = _instance()

    # athletics 在 skill_refs 里：str +3，熟练 +2。
    result = _resolve(instance, {"skill": "athletics", "dc": 15}, die=10)

    assert result["skill"] == "athletics"
    assert result["modifier"] == 5
    assert result["total"] == 15


def test_two_skills_on_the_same_ability_differ_by_proficiency():
    _engine, instance = _instance()

    # 同为 wis 10（+0）：perception 熟练 = +2，insight 不熟练 = +0。
    proficient = _resolve(instance, {"skill": "perception", "dc": 12}, die=10)
    plain = _resolve(instance, {"skill": "insight", "dc": 12}, die=10)

    assert proficient["modifier"] == 2 and proficient["success"] is True
    assert plain["modifier"] == 0 and plain["success"] is False


def test_an_unknown_skill_is_refused():
    _engine, instance = _instance()

    try:
        _resolve(instance, {"skill": "not_a_skill", "dc": 10})
    except ValueError as exc:
        assert "unknown skill" in str(exc)
    else:  # pragma: no cover - the call must fail
        raise AssertionError("unknown skill was accepted")


def test_an_invalid_ability_is_refused():
    _engine, instance = _instance()

    try:
        _resolve(instance, {"ability": "luck", "dc": 10})
    except ValueError as exc:
        assert "ability must be one of" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid ability was accepted")


def _service(instance, resolve_check):
    async def save(_instance):
        return None

    return ManualRollService(ManualRollDependencies(
        lambda key: tuple(key.split("/")),
        lambda _key: instance,
        save,
        None,
        resolve_check=resolve_check,
    ))


def test_a_check_request_is_resolved_by_the_ruleset_and_stored():
    _engine, instance = _instance()
    runtime = Dnd2024Runtime()
    service = _service(
        instance,
        lambda inst, uid, check: runtime.resolve_ability_check(
            inst, uid, check, rng=SequenceRng([10]),
        ),
    )

    created = asyncio.run(service.create("test/game", "gm", {
        "operation_id": "op-check", "run_id": instance.run_id,
        "purpose": "check", "target_uids": ["gm"], "label": "Perception",
        "check": {"skill": "perception", "dc": 12},
    }))
    assert created["ok"] is True, created
    request = created["request"]
    assert request["check"] == {"ability": "", "skill": "perception", "dc": 12}
    assert request["target"] == 12

    resolved = asyncio.run(service.resolve(
        "test/game", "gm", request["id"], {"run_id": instance.run_id},
    ))

    assert resolved["ok"] is True, resolved
    assert resolved["result"]["total"] == 12
    assert resolved["result"]["verdict"] == "success"
    assert resolved["result"]["modifier"] == 2


def test_a_check_block_without_purpose_check_is_refused():
    _engine, instance = _instance()
    service = _service(instance, lambda *_args: {"total": 0})

    created = asyncio.run(service.create("test/game", "gm", {
        "operation_id": "op-free", "run_id": instance.run_id,
        "purpose": "free", "target_uids": ["gm"], "formula": "d20",
        "check": {"ability": "dex", "dc": 10},
    }))

    assert created["ok"] is False
    assert "purpose=check" in created["error"]


def test_a_ruleset_without_the_hook_refuses_the_request():
    _engine, instance = _instance()
    service = _service(instance, None)

    created = asyncio.run(service.create("test/game", "gm", {
        "operation_id": "op-nohook", "run_id": instance.run_id,
        "purpose": "check", "target_uids": ["gm"],
        "check": {"ability": "dex", "dc": 10},
    }))

    assert created["ok"] is False
    assert "does not support" in created["error"]


def test_a_check_cannot_choose_both_an_ability_and_a_skill():
    _engine, instance = _instance()
    service = _service(instance, lambda *_args: {"total": 0})

    created = asyncio.run(service.create("test/game", "gm", {
        "operation_id": "op-both", "run_id": instance.run_id,
        "purpose": "check", "target_uids": ["gm"],
        "check": {"ability": "dex", "skill": "stealth", "dc": 10},
    }))

    assert created["ok"] is False
    assert "both" in created["error"]


def test_a_dnd_check_rejects_at_most_comparison():
    _engine, instance = _instance()
    service = _service(instance, lambda *_args: {"total": 0})

    created = asyncio.run(service.create("test/game", "gm", {
        "operation_id": "op-at-most", "run_id": instance.run_id,
        "purpose": "check", "comparison": "at_most", "target_uids": ["gm"],
        "check": {"ability": "dex", "dc": 10},
    }))

    assert created["ok"] is False
    assert "at_least" in created["error"]
