"""Monk Focus Points on the existing canonical resource and rest lifecycle.

Focus is not a new resource table: it is ``resources.class.focus_points``,
created by ``Dnd2024RestEngine.sync_resources`` at the granting level, resized
by advancement, and recovered by the existing short/long rest engine.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.engine.game_instance import GameInstance
from src.rulesets.dnd2024.resting import Dnd2024RestEngine, RestError
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

from dnd2024_monk_common import (
    SequenceRng,
    advance,
    capability_intent,
    focus_state,
    monk_instance,
    monk_sheet,
    start_combat,
)
from src.engine.modules import ruleset_runtime


def _character(runtime: Dnd2024Runtime, level: int = 2) -> dict:
    return monk_sheet(runtime, level=level)["ruleset_character"]


def _consume_focus(instance: GameInstance, uid: str = "gm") -> None:
    sheet = deepcopy(instance.get_character_sheet(uid))
    sheet["ruleset_character"]["resources"]["class"]["focus_points"]["current"] -= 1
    instance.set_character_sheet(uid, sheet)


def test_creating_a_monk_initializes_focus_at_the_granting_level() -> None:
    runtime = Dnd2024Runtime()

    level_one = _character(runtime, 1)
    level_two = _character(runtime, 2)

    assert "focus_points" not in level_one["resources"]["class"]
    assert level_two["resources"]["class"]["focus_points"] == {
        "current": 2,
        "maximum": 2,
        "source_ref": "srd-5.2.1:p50:monks-focus",
    }


def test_focus_never_goes_below_zero() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, monk_sheet(runtime, level=2))
    start_combat(engine, instance)

    # 2 点专注：先用疾风连击花掉 1 点，再用带专注的坚守防御花掉第 2 点。
    engine.apply_batch(instance, engine.resolve_intent(
        instance,
        capability_intent(engine, instance, "flurry_of_blows", intent_id="f-1"),
        SequenceRng([15, 4, 15, 4]),
    )["event_batch"])
    assert focus_state(instance)["current"] == 1
    ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] = 1
    engine.apply_batch(instance, engine.resolve_intent(
        instance,
        capability_intent(
            engine, instance, "patient_defense_focus", intent_id="pdf-1", target_id="",
        ),
        SequenceRng([]),
    )["event_batch"])
    assert focus_state(instance)["current"] == 0

    ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] = 1
    rejected = engine.validate_intent(
        instance, capability_intent(engine, instance, "flurry_of_blows", intent_id="f-2"),
    )

    assert rejected["ok"] is False
    assert "not enough Focus Points" in rejected["error"]
    assert focus_state(instance)["current"] == 0


def test_client_cannot_forge_focus() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, monk_sheet(runtime, level=2))
    start_combat(engine, instance)

    resolved = engine.resolve_intent(instance, capability_intent(
        engine, instance, "flurry_of_blows", intent_id="f-1",
        focus_points=99, resource={"id": "focus_points", "amount": 0},
        costs=[{"kind": "resource", "name": "Focus Points", "amount": 0}],
    ), SequenceRng([15, 4, 15, 4]))
    engine.apply_batch(instance, resolved["event_batch"])

    # 伪造的 payload 字段对权威成本没有任何影响：仍然恰好扣 1 点。
    assert focus_state(instance) == {
        "current": 1, "maximum": 2, "source_ref": "srd-5.2.1:p50:monks-focus",
    }
    assert ruleset_runtime.state(instance)["combat"]["economy"]["bonus_action"] == 0


def test_advancement_preserves_a_legal_focus_current_without_refilling() -> None:
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=2)
    sheet["ruleset_character"]["resources"]["class"]["focus_points"]["current"] = 1

    advanced = advance(runtime, sheet, 3)
    focus = advanced["ruleset_character"]["resources"]["class"]["focus_points"]

    # 上限按职业表提高，合法 current 原样保留：升级不会自动补回专注点。
    assert (focus["current"], focus["maximum"]) == (1, 3)
    assert advanced["class_resources"][0]["current"] == 1
    assert advanced["class_resources"][0]["maximum"] == 3


def test_advancement_clamps_a_current_above_the_new_maximum() -> None:
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=2)
    sheet["ruleset_character"]["resources"]["class"]["focus_points"] = {
        "current": 99,
        "maximum": 99,
        "source_ref": "srd-5.2.1:p50:monks-focus",
    }

    advanced = advance(runtime, sheet, 3)
    focus = advanced["ruleset_character"]["resources"]["class"]["focus_points"]

    # 异常旧数据被夹到新的上限，而不是把 99 带进新等级。
    assert (focus["current"], focus["maximum"]) == (3, 3)


def _minimal_character(class_ref: str, level: int, resources: dict) -> dict:
    return {
        "build": {"class_levels": [{"class_ref": class_ref, "level": level}]},
        "abilities": {"str": 16, "dex": 12, "con": 14, "int": 8, "wis": 10, "cha": 8},
        "resources": {"class": resources},
    }


def test_focus_resize_policy_preserves_current_clamps_and_never_refills() -> None:
    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))

    def sync(focus: dict | None) -> dict:
        resources = {"focus_points": focus} if focus is not None else {}
        character = _minimal_character("class:monk", 3, resources)
        return engine.sync_resources(character)["resources"]["class"]["focus_points"]

    assert sync({"current": 1, "maximum": 2})["current"] == 1
    assert sync({"current": 99, "maximum": 99})["current"] == 3
    assert sync({"current": 0, "maximum": 2})["current"] == 0
    # 资源此前不存在（首次被授予）时仍然是满的。
    assert sync(None)["current"] == 3


def test_a_resource_without_the_policy_keeps_the_preserve_spent_semantics() -> None:
    """默认策略必须保持既有行为：未声明的资源继续「保留已消费数量」。"""

    runtime = Dnd2024Runtime()
    engine = Dnd2024RestEngine(runtime.load_bundle("en"))
    character = _minimal_character(
        "class:barbarian", 3, {"rages": {"current": 1, "maximum": 2}},
    )

    rages = engine.sync_resources(character)["resources"]["class"]["rages"]

    assert (rages["current"], rages["maximum"]) == (2, 3)


def test_the_resize_policy_is_a_per_resource_declaration() -> None:
    runtime = Dnd2024Runtime()
    rules = Dnd2024RestEngine(runtime.load_bundle("en")).rules["class_resources"]
    policies = {
        (class_id, str(spec["id"])): str(spec.get("resize_policy", "preserve_spent"))
        for class_id, specs in rules.items()
        for spec in specs
    }

    assert policies[("monk", "focus_points")] == "preserve_current"
    # 只有 Focus 显式选择了新语义，其它职业资源全部保持默认。
    assert {
        policy for key, policy in policies.items() if key != ("monk", "focus_points")
    } == {"preserve_spent"}


def test_an_unknown_resize_policy_fails_closed() -> None:
    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle("en")
    entities = deepcopy(bundle.entities)
    entities["rest_catalog"]["srd_recovery"]["class_resources"]["monk"][0][
        "resize_policy"
    ] = "preserve_everything"

    with pytest.raises(RestError, match="unknown resize policy"):
        Dnd2024RestEngine(replace(bundle, entities=entities))


def test_short_rest_restores_focus() -> None:
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=2)
    sheet["ruleset_character"]["resources"]["class"]["focus_points"]["current"] = 0

    result = runtime.complete_rest(None, sheet, "short", {})
    focus = result["character"]["ruleset_character"]["resources"]["class"]["focus_points"]

    assert focus["current"] == 2
    assert any(
        event.get("type") == "restore_class_resource"
        and event.get("resource_id") == "focus_points"
        for event in result["events"]
    )
    # 短休摘要里就是既有的 resource delta，不需要 Monk 专用弹窗。
    assert result["character"]["class_resources"][0]["current"] == 2


def test_long_rest_restores_focus() -> None:
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=2)
    sheet["ruleset_character"]["resources"]["class"]["focus_points"]["current"] = 0

    result = runtime.complete_rest(None, sheet, "long")
    focus = result["character"]["ruleset_character"]["resources"]["class"]["focus_points"]

    assert focus["current"] == 2
    assert result["character"]["class_resources"][0]["id"] == "focus_points"


def test_missing_focus_is_repaired_by_the_existing_rest_path() -> None:
    # 旧存档升级到支持版本后可能缺少 Focus：由既有 rest/sync 合法补齐。
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=2)
    sheet["ruleset_character"]["resources"]["class"].pop("focus_points")

    result = runtime.complete_rest(None, sheet, "short", {})

    assert result["character"]["ruleset_character"]["resources"]["class"][
        "focus_points"
    ]["maximum"] == 2


def test_focus_survives_a_save_load_roundtrip() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, monk_sheet(runtime, level=2))
    start_combat(engine, instance)
    engine.apply_batch(instance, engine.resolve_intent(
        instance,
        capability_intent(engine, instance, "flurry_of_blows", intent_id="f-1"),
        SequenceRng([15, 4, 15, 4]),
    )["event_batch"])
    assert focus_state(instance)["current"] == 1

    restored = GameInstance.from_dict(instance.to_dict())
    restored_sheet = restored.get_character_sheet("gm")

    assert restored_sheet["ruleset_character"]["resources"]["class"][
        "focus_points"
    ]["current"] == 1
    assert restored_sheet["class_resources"][0]["current"] == 1


def test_focus_heads_the_projected_class_resource_list() -> None:
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=2)

    assert [(item["id"], item["name"], item["current"], item["maximum"]) for item in
            sheet["class_resources"]] == [
        ("focus_points", "Focus Points", 2, 2),
        # Monk 2 级同时声明了一次/长休的 Uncanny Metabolism 池。
        ("uncanny_metabolism", "Uncanny Metabolism", 1, 1),
    ]
    # 1 级还没有专注点：投影里不出现 0/0 的空资源。
    assert monk_sheet(runtime, level=1)["class_resources"] == []


def test_combat_actor_view_exposes_focus_with_its_localized_name() -> None:
    runtime = Dnd2024Runtime()
    engine, instance = monk_instance(runtime, monk_sheet(runtime, level=2))
    start_combat(engine, instance)

    def focus_row() -> dict:
        actor = next(
            item for item in engine.gameplay_view(instance)["combat"]["actors"]
            if item["actor_id"] == "player:gm"
        )
        return next(
            entry for entry in actor["class_resources"] if entry["id"] == "focus_points"
        )

    assert (focus_row()["name"], focus_row()["current"], focus_row()["maximum"]) == (
        "Focus Points", 2, 2,
    )

    engine.apply_batch(instance, engine.resolve_intent(
        instance,
        capability_intent(engine, instance, "flurry_of_blows", intent_id="f-1"),
        SequenceRng([15, 4, 15, 4]),
    )["event_batch"])

    # §34：消费之后同一个战斗视图立刻反映 1/2，不需要重新加载页面。
    assert (focus_row()["current"], focus_row()["maximum"]) == (1, 2)


def test_localized_focus_label_reaches_the_projection() -> None:
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=2, locale="zh-CN")
    feature_ids = [feature["id"] for feature in sheet["class_features"]]

    assert feature_ids == [
        "unarmored_defense", "unarmored_movement", "martial_arts", "monks_focus",
        "flurry_of_blows", "patient_defense", "step_of_the_wind",
    ]
    assert sheet["class_resources"][0]["name"] == "专注点"
    martial_arts = next(
        feature for feature in sheet["class_features"] if feature["id"] == "martial_arts"
    )
    assert martial_arts["name"] == "武艺"
    assert martial_arts["values"]["unarmed_damage_die"] == "1d6"


@pytest.mark.parametrize("rest", ["short", "long"])
def test_rest_rejects_a_monk_at_zero_hp(rest: str) -> None:
    runtime = Dnd2024Runtime()
    sheet = monk_sheet(runtime, level=2)
    sheet["ruleset_character"]["resources"]["hp"] = 0

    with pytest.raises(ValueError, match="at least 1 HP"):
        runtime.complete_rest(None, sheet, rest)
