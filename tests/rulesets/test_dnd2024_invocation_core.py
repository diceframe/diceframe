"""S0-F32：Eldritch Invocation 主干（数量、前置、对象绑定、替换、闭包）。

用例只走真实升级路径 `preview_advancement` / `apply_advancement`：数量对账、
"未选就拦下"、绑定对象与替换都必须能在预览与写成后的角色卡上看到。直接调
`option_pool.plan_pool` 只用来覆盖"数据里还没有的依赖闭包"这一条——闭包要等
F34 的 Pact/Thirsting Blade 才在内容里出现，定义只能用桩。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024 import Dnd2024Runtime
from src.rulesets.dnd2024.combat.check_modes import vision_codes
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver, option_pool
from src.rulesets.dnd2024.features.models import FeatureChoice
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection
from dnd2024_monk_common import SequenceRng, advancement_choices, monk_draft, start_combat

LOCALE = "en"
WARLOCK = "class:warlock"
POOL = "eldritch_invocations"
CHOICE_ID = "invocations"
CANTRIPS = ("spell:eldritch_blast", "spell:chill_touch")
#: 升到 4 级要 3 个戏法、5 个准备法术，夹具必须够用。
KNOWN_CANTRIPS = ("spell:eldritch_blast", "spell:chill_touch", "spell:poison_spray")
PREPARED = (
    "spell:hex", "spell:charm_person", "spell:hellish_rebuke",
    "spell:bane", "spell:expeditious_retreat",
)
ABILITIES = {
    "str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15,
}
#: 2 级就能拿到的三项，用来填"本级必须选满"的槽。
LEVEL_TWO_PICKS = [
    {"ref": "feature:agonizing_blast", "object_ref": "spell:eldritch_blast"},
    "feature:eldritch_mind",
    "feature:fiendish_vigor",
]


def _sheet(
    level: int = 1, cantrips: tuple[str, ...] = CANTRIPS,
) -> tuple[Dnd2024Runtime, dict]:
    runtime = Dnd2024Runtime()
    draft = monk_draft(runtime)
    spell_choices = _spell_choices(runtime, 1)
    spell_choices["cantrip_refs"] = list(cantrips)
    draft.update(
        class_ref=WARLOCK,
        class_skill_refs=["skill:arcana", "skill:deception"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:warlock_a",
        base_abilities=dict(ABILITIES),
        name="Warlock",
        class_spell_choices=spell_choices,
    )
    sheet = runtime.finalize_character(None, draft)
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        preview, choices = _plan(runtime, sheet)
        assert preview["ok"] is True, preview["errors"]
        sheet = runtime.apply_advancement(None, sheet, choices)
    return runtime, sheet


def _plan(
    runtime: Dnd2024Runtime,
    sheet: dict,
    picks: list | None = None,
    replace: dict | None = None,
) -> tuple[dict, dict]:
    """One level-up, with the invocation picks the caller wants to submit."""

    preview = runtime.preview_advancement(None, sheet, {})
    choices = advancement_choices(preview)
    # 法术选择是另一个必填槽，通用助手不管它（魔契师夹具有自己的 filler）。
    choices["class_spell_choices"] = _spell_choices(runtime, preview["to_level"])
    if picks is not None:
        choices["feature_choices"] = [{
            "owner": WARLOCK,
            "feature_id": POOL,
            "choice_id": CHOICE_ID,
            "selected_refs": list(picks),
        }]
    if replace is not None:
        choices["feature_choice_replace"] = replace
    return runtime.preview_advancement(None, sheet, choices), choices


def _spell_choices(runtime: Dnd2024Runtime, level: int) -> dict:
    requirements = Dnd2024SpellSelection(runtime.load_bundle(LOCALE)).requirements(
        WARLOCK, level,
    )
    return {
        "cantrip_refs": list(KNOWN_CANTRIPS[: int(requirements["cantrip_count"])]),
        "prepared_spell_refs": list(PREPARED[: int(requirements["prepared_spell_count"])]),
        "spellbook_refs": [],
    }


def _pool_requirement(preview: dict) -> dict:
    for requirement in preview.get("requirements") or []:
        if requirement.get("feature_id") == POOL:
            return requirement
    raise AssertionError("this level has no invocation slot")


def _option(requirement: dict, ref: str) -> dict:
    for row in requirement["options"]:
        if row["value"] == ref:
            return row
    raise AssertionError(f"{ref} is not projected")


def _pool_record(character: dict) -> dict:
    for record in character["features"]["feature_choices"]:
        if record.get("feature_id") == POOL:
            return record
    raise AssertionError("no invocation record was written")


def _bound(record: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in record["selected_refs"]:
        if isinstance(raw, dict):
            out[raw["object_ref"]] = raw["ref"]
        else:
            out[raw] = ""
    return out


# ---------------------------------------------------------------------------
# 1. 数量与来源
# ---------------------------------------------------------------------------


def test_the_pool_is_declared_against_the_warlock_track() -> None:
    """数量、替换额度与候选都写在声明里，Python 里没有 "3/5/7"。"""

    bundle = Dnd2024Runtime().load_bundle(LOCALE)
    resolver = Dnd2024ClassFeatureResolver(bundle)
    parent = resolver.catalog.features_by_key[(WARLOCK, POOL)]
    choice = parent.choice
    assert choice.count_track == POOL
    assert choice.replace_per_level == 1
    assert len(choice.enabled_options) == 8
    levels = {
        ref[len("feature:"):]: resolver.catalog.features_by_key[
            (WARLOCK, ref[len("feature:"):])
        ].minimum_level
        for ref in choice.enabled_options
    }
    # SRD 只给 Eldritch Mind 之外的四项写了 "Level 2+ Warlock"，Witch Sight 是
    # 15 级；Eldritch Mind 原文没有前置行，1 级就能选。
    assert levels["eldritch_mind"] == 1
    assert {key: value for key, value in levels.items() if key != "eldritch_mind"} == {
        "agonizing_blast": 2, "devil_s_sight": 2, "eldritch_spear": 2,
        "fiendish_vigor": 2, "lessons_of_the_first_ones": 2,
        "repelling_blast": 2, "witch_sight": 15,
    }

    catalog = bundle.get("progression_catalog", "srd_classes")
    tracks = catalog["classes"]["warlock"]["tracks"]
    assert tracks["eldritch_invocations"][4] == 5  # 5 级 5 个


def test_a_level_two_warlock_owes_the_whole_track() -> None:
    """1 级没选的不会永久作废：到 2 级一次补齐到 3 个。"""

    runtime, sheet = _sheet()
    preview, _ = _plan(runtime, sheet, picks=[])
    requirement = _pool_requirement(preview)
    assert (requirement["count"], requirement["total"], requirement["owned"]) == (3, 3, 0)
    assert preview["ok"] is False
    assert any("must select exactly 3" in error for error in preview["errors"])


def test_picking_three_writes_one_record_and_makes_them_owned() -> None:
    runtime, sheet = _sheet()
    preview, choices = _plan(runtime, sheet, picks=LEVEL_TWO_PICKS)
    assert preview["ok"] is True, preview["errors"]
    advanced = runtime.apply_advancement(None, sheet, choices)
    character = advanced["ruleset_character"]
    record = _pool_record(character)
    assert record["granted_level"] == 2
    assert _bound(record) == {
        "spell:eldritch_blast": "feature:agonizing_blast",
        "feature:eldritch_mind": "",
        "feature:fiendish_vigor": "",
    }
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))
    owned = {definition.id for definition in resolver.features_for(character)}
    assert {"agonizing_blast", "eldritch_mind", "fiendish_vigor"} <= owned
    assert resolver.owns_binding(character, "agonizing_blast", "spell:eldritch_blast")


def test_a_level_without_a_new_slot_asks_for_nothing() -> None:
    """track 在 2→3 级不变，所以 3 级不该再要一次选择。"""

    runtime, sheet = _sheet(level=2)
    preview, choices = _plan(runtime, sheet, picks=[])
    sheet = runtime.apply_advancement(None, sheet, choices)

    preview, _ = _plan(runtime, sheet, picks=[])
    requirement = _pool_requirement(preview)
    assert requirement["count"] == 0
    assert requirement["owned"] == 3
    assert preview["ok"] is True, preview["errors"]


def test_level_five_asks_for_the_two_the_table_adds() -> None:
    runtime, sheet = _sheet(level=4)
    preview, _ = _plan(runtime, sheet, picks=[])
    requirement = _pool_requirement(preview)
    assert (requirement["count"], requirement["total"], requirement["owned"]) == (2, 5, 3)


# ---------------------------------------------------------------------------
# 2. 对象绑定
# ---------------------------------------------------------------------------


def test_agonizing_blast_needs_a_damaging_warlock_cantrip() -> None:
    runtime, sheet = _sheet()
    requirement = _pool_requirement(_plan(runtime, sheet, picks=[])[0])
    options = _option(requirement, "feature:agonizing_blast")["bind_options"]
    assert {row["value"] for row in options if row["available"]} == set(CANTRIPS)


def test_eldritch_spear_only_accepts_a_cantrip_that_reaches_ten_feet() -> None:
    runtime, sheet = _sheet()
    requirement = _pool_requirement(_plan(runtime, sheet, picks=[])[0])
    options = _option(requirement, "feature:eldritch_spear")["bind_options"]
    by_ref = {row["value"]: row for row in options}
    assert by_ref["spell:eldritch_blast"]["available"] is True
    assert by_ref["spell:chill_touch"]["available"] is False
    assert "range" in by_ref["spell:chill_touch"]["unavailable_reasons"]


def test_a_cantrip_that_deals_no_damage_cannot_be_bound() -> None:
    runtime, sheet = _sheet(cantrips=("spell:eldritch_blast", "spell:prestidigitation"))
    picks = [
        {"ref": "feature:agonizing_blast", "object_ref": "spell:prestidigitation"},
        "feature:eldritch_mind",
        "feature:fiendish_vigor",
    ]
    preview, _ = _plan(runtime, sheet, picks=picks)
    assert preview["ok"] is False
    assert any("not an eligible known_cantrip" in error for error in preview["errors"])


def test_a_cantrip_option_must_name_its_cantrip() -> None:
    runtime, sheet = _sheet()
    picks = ["feature:agonizing_blast", "feature:eldritch_mind", "feature:fiendish_vigor"]
    preview, _ = _plan(runtime, sheet, picks=picks)
    assert preview["ok"] is False
    assert any("must be bound to one known_cantrip" in error for error in preview["errors"])


def test_two_picks_of_the_same_option_need_two_different_cantrips() -> None:
    runtime, sheet = _sheet()
    same = [
        {"ref": "feature:agonizing_blast", "object_ref": "spell:eldritch_blast"},
        {"ref": "feature:agonizing_blast", "object_ref": "spell:eldritch_blast"},
        "feature:eldritch_mind",
    ]
    preview, _ = _plan(runtime, sheet, picks=same)
    assert preview["ok"] is False
    assert any("already bound" in error for error in preview["errors"])

    two = [
        {"ref": "feature:agonizing_blast", "object_ref": "spell:eldritch_blast"},
        {"ref": "feature:agonizing_blast", "object_ref": "spell:chill_touch"},
        "feature:eldritch_mind",
    ]
    preview, _ = _plan(runtime, sheet, picks=two)
    assert preview["ok"] is True, preview["errors"]


def test_an_option_that_is_not_repeatable_cannot_be_taken_twice() -> None:
    runtime, sheet = _sheet()
    picks = ["feature:eldritch_mind", "feature:eldritch_mind", "feature:fiendish_vigor"]
    preview, _ = _plan(runtime, sheet, picks=picks)
    assert preview["ok"] is False
    assert any("cannot be taken more than once" in error for error in preview["errors"])


# ---------------------------------------------------------------------------
# 3. 前置与不可用选项
# ---------------------------------------------------------------------------


def test_witch_sight_is_not_offered_before_level_fifteen() -> None:
    runtime, sheet = _sheet()
    requirement = _pool_requirement(_plan(runtime, sheet, picks=[])[0])
    option = _option(requirement, "feature:witch_sight")
    assert option["available"] is False
    assert "level" in option["unavailable_reasons"]


def test_a_guided_option_stays_visible_and_unselectable() -> None:
    """没有光照模型就标 guided：能看见、能读理由，但提交要被拒。"""

    runtime, sheet = _sheet()
    requirement = _pool_requirement(_plan(runtime, sheet, picks=[])[0])
    option = _option(requirement, "feature:devil_s_sight")
    assert option["available"] is False
    # 面板只认 effect_not_implemented 这一类原因；声明里写的具体缺什么
    # （light_model）跟在后面供审计看。
    assert option["unavailable_reasons"][0] == "effect_not_implemented"
    assert "light_model" in option["unavailable_reasons"]

    picks = ["feature:devil_s_sight", "feature:eldritch_mind", "feature:fiendish_vigor"]
    preview, _ = _plan(runtime, sheet, picks=picks)
    assert preview["ok"] is False
    assert any("effect_not_implemented" in error for error in preview["errors"])


# ---------------------------------------------------------------------------
# 4. 替换
# ---------------------------------------------------------------------------


def test_one_pick_may_be_replaced_when_a_level_is_gained() -> None:
    runtime, sheet = _sheet()
    picks = [
        {"ref": "feature:eldritch_spear", "object_ref": "spell:eldritch_blast"},
        "feature:eldritch_mind",
        "feature:fiendish_vigor",
    ]
    preview, choices = _plan(runtime, sheet, picks=picks)
    assert preview["ok"] is True, preview["errors"]
    sheet = runtime.apply_advancement(None, sheet, choices)

    # 换进来的可重复选项同样要带绑定对象。
    preview, _ = _plan(
        runtime, sheet,
        picks=[],
        replace={"drop": "feature:fiendish_vigor", "add": "feature:agonizing_blast"},
    )
    assert preview["ok"] is False
    assert any("must be bound" in error for error in preview["errors"])

    preview, choices = _plan(
        runtime, sheet,
        picks=[],
        replace={
            "drop": "feature:fiendish_vigor",
            "add": {"ref": "feature:agonizing_blast", "object_ref": "spell:eldritch_blast"},
        },
    )
    assert preview["ok"] is True, preview["errors"]
    advanced = runtime.apply_advancement(None, sheet, choices)
    record = _pool_record(advanced["ruleset_character"])
    assert len(record["selected_refs"]) == 3
    assert "feature:fiendish_vigor" not in record["selected_refs"]
    assert "spell:eldritch_blast" in _bound(record)
    assert record["granted_level"] == 3
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))
    owned = {definition.id for definition in resolver.features_for(advanced["ruleset_character"])}
    assert "agonizing_blast" in owned
    assert "fiendish_vigor" not in owned


def test_replacement_must_name_an_option_the_character_has() -> None:
    runtime, sheet = _sheet(level=2)
    preview, choices = _plan(
        runtime, sheet,
        picks=[],
        replace={"drop": "feature:repelling_blast", "add": "feature:eldritch_mind"},
    )
    assert preview["ok"] is False
    assert any("is not one of your" in error for error in preview["errors"])


def test_replacement_refuses_to_break_a_dependency() -> None:
    """闭包只能由数据算出：F32 的八项之间没有依赖，所以用桩钉住规则。"""

    def option(option_id: str, **parameters) -> SimpleNamespace:
        return SimpleNamespace(
            id=option_id, owner=WARLOCK, minimum_level=2,
            parameters={"invocation_effect_status": "complete", **parameters},
            option_of=POOL,
        )

    options = {
        (WARLOCK, "blade"): option("blade"),
        (WARLOCK, "thirsting"): option(
            "thirsting", invocation_requires_invocations=["feature:blade"],
        ),
        (WARLOCK, "mind"): option("mind"),
    }
    resolver = SimpleNamespace(catalog=SimpleNamespace(
        features_by_key=options, label=lambda key, fallback=None: str(key),
    ))
    parent = SimpleNamespace(
        id=POOL, owner=WARLOCK,
        choice=FeatureChoice(
            id=CHOICE_ID, kind="feature_option", category="feature", count=1,
            enabled_options=("feature:blade", "feature:thirsting", "feature:mind"),
            count_track=POOL, replace_per_level=1,
        ),
    )
    character = {"features": {"feature_choices": [{
        "owner": WARLOCK, "feature_id": POOL, "choice_id": CHOICE_ID,
        "selected_refs": ["feature:blade", "feature:thirsting"],
        "granted_level": 2,
    }]}}
    plan = option_pool.plan_pool(
        resolver=resolver, definition=parent, character=character,
        target_level=2, track_total=2, submitted=None,
        replacement={"drop": "feature:blade", "add": "feature:mind"},
        spells={}, spell_effects={},
    )
    assert plan.ok is False
    assert any("requires feature:blade" in error for error in plan.errors)



def test_a_pool_the_content_cannot_fill_does_not_block_the_level() -> None:
    """池子不够 ≠ 玩家漏选：差额要投影出去，但不能把升级卡死。

    SRD 有二十多项祈唤，本片只声明了八项，所以 12 级（track 8）在只有两个
    伤害戏法时最多凑出 7 个合法组合。把这种情形和"有得选却没选"混成同一条
    错误，等于让角色永久停在 11 级。
    """

    parent = SimpleNamespace(
        id=POOL, owner=WARLOCK,
        choice=FeatureChoice(
            id=CHOICE_ID, kind="feature_option", category="feature", count=1,
            enabled_options=("feature:only_choice",), count_track=POOL,
            replace_per_level=1,
        ),
    )
    options = {
        (WARLOCK, "only_choice"): SimpleNamespace(
            id="only_choice", owner=WARLOCK, minimum_level=15,
            parameters={"invocation_effect_status": "complete"}, option_of=POOL,
        ),
    }
    resolver = SimpleNamespace(catalog=SimpleNamespace(
        features_by_key=options, label=lambda key, fallback=None: str(key),
    ))
    plan = option_pool.plan_pool(
        resolver=resolver, definition=parent, character={"features": {}},
        target_level=5, track_total=5, submitted=None, replacement=None,
        spells={}, spell_effects={},
    )
    assert plan.ok is True
    assert plan.requirement is not None
    assert plan.requirement["count"] == 0
    assert plan.requirement["shortfall"] == 5
    # 记录会被种下来（空的）：它是"这张卡进入池子时代"的标记，下一级据此
    # 要求补齐，而不是又被当成老卡放过。
    assert plan.record is not None
    assert plan.record["selected_refs"] == []
    assert plan.record["granted_level"] == 5

    # 有得选却没选，仍然要拦。
    options[(WARLOCK, "only_choice")].minimum_level = 2
    blocked = option_pool.plan_pool(
        resolver=resolver, definition=parent, character={"features": {}},
        target_level=5, track_total=5, submitted=None, replacement=None,
        spells={}, spell_effects={},
    )
    assert blocked.ok is False
    assert any("must select exactly 1" in error for error in blocked.errors)


# ---------------------------------------------------------------------------
# 5. 六项真实效果
# ---------------------------------------------------------------------------


def _combat(
    invocations: list,
    *,
    level: int = 2,
    cantrips: tuple[str, ...] = CANTRIPS,
    concentration: dict | None = None,
) -> tuple[object, object]:
    """A Warlock in combat whose invocation record is written the way the
    level-up path writes it.

    Picking through `advancement` is covered in §1; here the effects need
    specific bindings, so the record is installed directly -- the same shape,
    not a shorter one.
    """

    from test_dnd2024_combat import _instance_with_sheet

    runtime, sheet = _sheet(cantrips=cantrips)
    character = sheet["ruleset_character"]
    character["build"]["level"] = level
    character["build"]["class_levels"] = [{"class_ref": WARLOCK, "level": level}]
    character.setdefault("features", {})["feature_choices"] = [{
        "owner": WARLOCK,
        "feature_id": POOL,
        "choice_id": CHOICE_ID,
        "selected_refs": list(invocations),
        "granted_level": level,
    }]
    if concentration is not None:
        character.setdefault("spellcasting", {}).setdefault("class", {})[
            "concentration"
        ] = concentration
    engine, instance = _instance_with_sheet(sheet)
    start_combat(engine, instance)
    state = ruleset_runtime.state(instance)["combat"]
    # stat block 的 size 是权威事实；夹具里的哥布林原本不带，补一处再打。
    state["enemies"]["goblin-1"]["size"] = "medium"
    return engine, instance


_CASTS = {"count": 0}


def _cast(engine, instance, intent: dict, rolls=()):
    _CASTS["count"] += 1
    payload = {
        "intent_id": f"invocation-{_CASTS['count']}",
        "expected_version": ruleset_runtime.state(instance)["version"],
        "submitted_by": "gm",
        "actor_id": "player:gm",
        **intent,
    }
    return engine.resolve_intent(instance, payload, SequenceRng(list(rolls)))


def _refusal(resolved: dict) -> str:
    """The one message a refused intent carries (error or errors)."""

    if resolved.get("error"):
        return str(resolved["error"])
    return " ".join(str(item) for item in resolved.get("errors") or [])


def _hp_event(events: list[dict]) -> dict:
    return next(
        event for event in events
        if event["type"] == "resource.changed" and event.get("resource") == "hp"
    )


def _blast_events(engine, instance, rolls=(15, 7), **extra):
    resolved = _cast(engine, instance, {
        "type": "cast_spell", "spell_ref": "spell:eldritch_blast",
        "slot_level": 0, "target_id": "enemy:goblin-1", **extra,
    }, rolls=rolls)
    assert resolved["ok"] is True, resolved
    events = resolved["event_batch"]["events"]
    assert engine.apply_batch(instance, resolved["event_batch"])["applied"] is True
    return events


def test_agonizing_blast_adds_charisma_to_each_damage_roll() -> None:
    binding = {"ref": "feature:agonizing_blast", "object_ref": "spell:eldritch_blast"}
    engine, instance = _combat([binding])
    damage = _hp_event(_blast_events(engine, instance))
    charisma = (ABILITIES["cha"] - 10) // 2
    assert damage["amount"] == sum(damage["rolls"]) + charisma

    plain_engine, plain_instance = _combat([])
    plain = _hp_event(_blast_events(plain_engine, plain_instance))
    assert plain["amount"] == sum(plain["rolls"])


def test_agonizing_blast_does_not_touch_an_unbound_cantrip() -> None:
    binding = {"ref": "feature:agonizing_blast", "object_ref": "spell:eldritch_blast"}
    engine, instance = _combat([binding])
    resolved = _cast(engine, instance, {
        "type": "cast_spell", "spell_ref": "spell:chill_touch",
        "slot_level": 0, "target_id": "enemy:goblin-1",
    }, rolls=[15, 5])
    assert resolved["ok"] is True, resolved
    damage = _hp_event(resolved["event_batch"]["events"])
    assert damage["amount"] == sum(damage["rolls"])


def test_eldritch_spear_extends_only_the_bound_cantrip() -> None:
    engine, instance = _combat([
        {"ref": "feature:eldritch_spear", "object_ref": "spell:eldritch_blast"},
    ], level=2)
    combat = ruleset_runtime.state(instance)["combat"]
    view = engine._actor_view(instance, combat, "player:gm")
    effect = engine.catalog.spell_effects["eldritch_blast"]
    blast = engine.spells.get("spell:eldritch_blast")
    chill = engine.spells.get("spell:chill_touch")
    assert engine._spell_effective_range(view, blast, effect) == 120 + 30 * 2
    chill_effect = engine.catalog.spell_effects["chill_touch"]
    assert engine._spell_effective_range(view, chill, chill_effect) == 5


def test_repelling_blast_pushes_on_a_hit_and_refuses_too_far() -> None:
    engine, instance = _combat([
        {"ref": "feature:repelling_blast", "object_ref": "spell:eldritch_blast"},
    ])
    events = _blast_events(engine, instance, spell_push_ft=10)
    push = next(
        event for event in events
        if event["type"] == "dnd2024.position.changed"
        and event.get("source_rider") == "repelling_blast"
    )
    assert abs(push["distance"]) == 10
    assert push["forced"] is True

    engine, instance = _combat([
        {"ref": "feature:repelling_blast", "object_ref": "spell:eldritch_blast"},
    ])
    too_far = _cast(engine, instance, {
        "type": "cast_spell", "spell_ref": "spell:eldritch_blast",
        "slot_level": 0, "target_id": "enemy:goblin-1", "spell_push_ft": 15,
    }, rolls=[15, 7])
    assert too_far["ok"] is False
    assert "at most 10 feet" in _refusal(too_far)


def test_repelling_blast_leaves_a_too_large_target_in_place() -> None:
    """体型按**每次命中**判：目标过大只是不推它，不把整次施法判死。"""

    engine, instance = _combat([
        {"ref": "feature:repelling_blast", "object_ref": "spell:eldritch_blast"},
    ])
    ruleset_runtime.state(instance)["combat"]["enemies"]["goblin-1"]["size"] = "huge"
    events = _blast_events(engine, instance, spell_push_ft=5)
    assert not [
        event for event in events
        if event["type"] == "dnd2024.position.changed"
        and event.get("source_rider") == "repelling_blast"
    ]
    # 命中与伤害照旧。
    assert _hp_event(events)["amount"] > 0


def test_eldritch_mind_gives_advantage_only_to_concentration() -> None:
    engine, instance = _combat(
        ["feature:eldritch_mind"],
        concentration={"spell_ref": "spell:hex"},
    )
    combat = ruleset_runtime.state(instance)["combat"]
    target = engine._actor_view(instance, combat, "player:gm")
    events = engine._concentration_events(
        instance, combat, target, 10, SequenceRng([5, 18]),
    )
    check = next(event for event in events if event["type"] == "check.resolved")
    assert check["rolls"] == [5, 18]
    assert check["natural"] == 18
    assert check["advantage_sources"] == [
        {"code": "eldritch_mind", "mode": "advantage"},
    ]


def test_fiendish_vigor_casts_false_life_at_twelve_without_rolling() -> None:
    engine, instance = _combat(["feature:fiendish_vigor"])
    resolved = _cast(engine, instance, {
        "type": "cast_spell", "spell_ref": "spell:false_life",
        "slot_level": 0, "target_id": "player:gm",
    }, rolls=[1, 1])
    assert resolved["ok"] is True, resolved
    temp = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "resource.changed" and event.get("resource") == "temp_hp"
    )
    # 2d4 取最大是 8，加固定 +4 = 12；骰出来的 1+1 必须没被采用。
    assert temp["amount"] == 12
    assert temp.get("maximized") is True


def test_witch_sight_gives_truesight_thirty_at_fifteen() -> None:
    engine, instance = _combat(["feature:witch_sight"], level=15)
    combat = ruleset_runtime.state(instance)["combat"]
    view = engine._actor_view(instance, combat, "player:gm")
    assert {"code": "truesight", "range_ft": 30, "source": "witch_sight"} in view["senses"]
    assert "truesight" in vision_codes(engine, view, distance=20)
    assert "truesight" not in vision_codes(engine, view, distance=40)


def test_a_level_one_build_can_take_its_invocation() -> None:
    """建卡就能选第一个：1 级 1 个名额不是等 2 级补的。"""

    runtime = Dnd2024Runtime()
    draft = monk_draft(runtime)
    draft.update(
        class_ref=WARLOCK,
        class_skill_refs=["skill:arcana", "skill:deception"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:warlock_a",
        base_abilities=dict(ABILITIES),
        name="Warlock",
        class_spell_choices={
            "cantrip_refs": list(CANTRIPS),
            "prepared_spell_refs": list(PREPARED[:2]),
            "spellbook_refs": [],
        },
        feature_choices=[{
            "owner": WARLOCK,
            "feature_id": POOL,
            "choice_id": CHOICE_ID,
            "selected_refs": ["feature:eldritch_mind"],
        }],
    )
    choices = runtime.builder_choices(None, draft)
    draft["class_tool_refs"] = [row["ref"] for row in choices["class_tools"]][
        : choices["class_tool_count"]
    ]
    sheet = runtime.finalize_character(None, draft)
    character = sheet["ruleset_character"]
    record = _pool_record(character)
    assert record["selected_refs"] == ["feature:eldritch_mind"]
    assert record["granted_level"] == 1
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))
    assert "eldritch_mind" in {item.id for item in resolver.features_for(character)}


def test_the_builder_offers_the_level_one_slot() -> None:
    """建卡面板要能看见这个槽（否则玩家没有地方点）。"""

    runtime = Dnd2024Runtime()
    draft = monk_draft(runtime)
    draft.update(
        class_ref=WARLOCK,
        class_skill_refs=["skill:arcana", "skill:deception"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:warlock_a",
        base_abilities=dict(ABILITIES),
        name="Warlock",
        class_spell_choices={
            "cantrip_refs": list(CANTRIPS),
            "prepared_spell_refs": list(PREPARED[:2]),
            "spellbook_refs": [],
        },
    )
    choices = runtime.builder_choices(None, draft)
    slots = choices["invocation_choices"]
    assert len(slots) == 1
    slot = slots[0]
    assert (slot["owner"], slot["feature_id"], slot["choice_id"]) == (
        WARLOCK, POOL, CHOICE_ID,
    )
    assert slot["count"] == 1
    assert slot["total"] == 1
    available = {row["value"] for row in slot["options"] if row["available"]}
    # 1 级只有 Eldritch Mind（原文无前置）；其余六项是 2 级，Witch Sight 15 级。
    assert available == {"feature:eldritch_mind"}


def test_a_level_one_build_cannot_take_a_level_two_option() -> None:
    runtime = Dnd2024Runtime()
    draft = monk_draft(runtime)
    draft.update(
        class_ref=WARLOCK,
        class_skill_refs=["skill:arcana", "skill:deception"],
        class_tool_refs=[],
        equipment_package_ref="equipment_package:warlock_a",
        base_abilities=dict(ABILITIES),
        name="Warlock",
        class_spell_choices={
            "cantrip_refs": list(CANTRIPS),
            "prepared_spell_refs": list(PREPARED[:2]),
            "spellbook_refs": [],
        },
        feature_choices=[{
            "owner": WARLOCK,
            "feature_id": POOL,
            "choice_id": CHOICE_ID,
            "selected_refs": ["feature:agonizing_blast"],
        }],
    )
    errors = runtime.validate_builder_draft(draft) if hasattr(
        runtime, "validate_builder_draft",
    ) else []
    if not errors:
        with pytest.raises(ValueError):
            runtime.finalize_character(None, draft)
        return
    assert any("not available at level 1" in error for error in errors)


def test_an_old_warlock_card_still_saves_without_invocations() -> None:
    """本切片之前建的 2 级魔契师卡不能被 replay 拒掉。

    老卡的 history 里没有池记录；重放时后端允许这种"没有记录"的卡跳过必填槽，
    并把标记种下来（否则下一级又会要求补齐，等于永远打不开）。正常升级不走
    这条豁免——那条路径没有记录就是漏选，照旧拦。
    """

    runtime = Dnd2024Runtime()
    bundle = runtime.load_bundle(LOCALE)
    _, sheet = _sheet()
    character = sheet["ruleset_character"]
    # 先把卡升到 2 级（带选择），再把池记录与历史里的选择抹掉，模拟老卡。
    preview, choices = _plan(
        runtime, sheet, picks=LEVEL_TWO_PICKS,
    )
    assert preview["ok"] is True, preview["errors"]
    sheet = runtime.apply_advancement(None, sheet, choices)
    character = sheet["ruleset_character"]
    character["features"].pop("feature_choices", None)
    for entry in character["progression"]["history"]:
        entry.get("choices", {}).pop("feature_choices", None)

    rebuilt = runtime.normalize_character_submission(None, sheet)
    record = _pool_record(rebuilt["ruleset_character"])
    assert record["legacy"] is True
    assert record["selected_refs"] == []


def test_the_capacity_counts_picks_not_option_rows() -> None:
    """容量按"还能选几次"算：可重复选项每绑一个戏法就多一次。

    只按选项行数算会把 7 个合法组合报成 5 个，然后把本可填满的提交拒掉。
    """

    runtime = Dnd2024Runtime()
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))
    definition = resolver.catalog.features_by_key[(WARLOCK, POOL)]
    bundle = runtime.load_bundle(LOCALE)
    spells = {
        row["id"]: row
        for row in (bundle.get("spell_catalog", "srd_spells") or {}).get("spells") or []
    }
    combat = bundle.get("combat_catalog", "srd_combat_core") or {}
    plan = option_pool.plan_pool(
        resolver=resolver,
        definition=definition,
        character={
            "build": {"level": 2, "class_levels": []},
            "features": {},
            "spellcasting": {"class": {"cantrip_refs": list(CANTRIPS)}},
        },
        target_level=2,
        track_total=8,
        submitted=None,
        replacement=None,
        spells=spells,
        spell_effects=combat.get("spell_effects") or {},
    )
    assert plan.requirement is not None
    # 两把伤害戏法：agonizing×2、spear×1、repelling×2、mind、vigor = 7。
    # 12 级要 8 个、1 级已选 0 个，所以容量把 count 压到 7、shortfall 记 1。
    assert plan.requirement["count"] == 7
    assert plan.requirement["shortfall"] == 1


def test_two_options_granted_together_may_depend_on_each_other() -> None:
    """同批新增的依赖要成立：先拿 A 再拿依赖 A 的 B，不能因为顺序被拒。

    F34 的 Pact of the Blade + Thirsting Blade 就是这一形状；规则只要求
    "获得时满足前置"，没要求玩家分两级挑选。
    """

    def option(option_id: str, **parameters) -> SimpleNamespace:
        return SimpleNamespace(
            id=option_id, owner=WARLOCK, minimum_level=2,
            parameters={"invocation_effect_status": "complete", **parameters},
            option_of=POOL,
        )

    options = {
        (WARLOCK, "blade"): option("blade"),
        (WARLOCK, "thirsting"): option(
            "thirsting", invocation_requires_invocations=["feature:blade"],
        ),
    }
    resolver = SimpleNamespace(catalog=SimpleNamespace(
        features_by_key=options, label=lambda key, fallback=None: str(key),
    ))
    parent = SimpleNamespace(
        id=POOL, owner=WARLOCK,
        choice=FeatureChoice(
            id=CHOICE_ID, kind="feature_option", category="feature", count=1,
            enabled_options=("feature:blade", "feature:thirsting"),
            count_track=POOL, replace_per_level=1,
        ),
    )
    plan = option_pool.plan_pool(
        resolver=resolver, definition=parent, character={"features": {}},
        target_level=5, track_total=2,
        submitted=["feature:blade", "feature:thirsting"], replacement=None,
        spells={}, spell_effects={},
    )
    assert plan.ok is True, plan.errors
    assert plan.record is not None
    assert plan.record["selected_refs"] == ["feature:blade", "feature:thirsting"]


def test_the_card_view_shows_a_bound_pick() -> None:
    """角色卡的 selection 要能看见绑定选择（否则前端渲染出"选了个寂寞"）。"""

    runtime, sheet = _sheet()
    preview, choices = _plan(runtime, sheet, picks=LEVEL_TWO_PICKS)
    assert preview["ok"] is True, preview["errors"]
    advanced = runtime.apply_advancement(None, sheet, choices)
    character = advanced["ruleset_character"]
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))
    parent = resolver.catalog.features_by_key[(WARLOCK, POOL)]
    selection = resolver.feature_selection(character, parent)
    assert selection is not None
    assert set(selection["selected_refs"]) == {
        "feature:agonizing_blast",
        "feature:eldritch_mind",
        "feature:fiendish_vigor",
    }


def test_a_legacy_card_still_sees_what_it_owes() -> None:
    """老卡只是"不强制"，槽位和待补数量仍然要投影出来。

    否则 legacy 标记就成了"永久 0 个祈唤"：面板看不到槽，玩家无处可补。
    """

    runtime = Dnd2024Runtime()
    _, sheet = _sheet()
    preview, choices = _plan(runtime, sheet, picks=LEVEL_TWO_PICKS)
    assert preview["ok"] is True, preview["errors"]
    sheet = runtime.apply_advancement(None, sheet, choices)
    character = sheet["ruleset_character"]
    character["features"].pop("feature_choices", None)
    for entry in character["progression"]["history"]:
        entry.get("choices", {}).pop("feature_choices", None)
    rebuilt = runtime.normalize_character_submission(None, sheet)

    preview = runtime.preview_advancement(None, rebuilt, {})
    requirement = _pool_requirement(preview)
    assert requirement["legacy"] is True
    assert requirement["required"] is False
    assert requirement["count"] == requirement["total"] - requirement["owned"]
    # 内容能填满，所以 shortfall 是 0：差额是"没选"，不是"没得选"。
    assert requirement["shortfall"] == 0


def test_maximisation_requires_the_slot_that_actually_paid() -> None:
    """只有"这次真的用了 Fiendish Vigor 的免槽"才取最大。

    写成单元用例而不是走施法：魔契师没法准备 False Life（它不在职业表上），
    用普通法术位放它是个不可能的配置，拿它当反例等于证明不了什么。
    """

    from src.rulesets.dnd2024.combat import invocation_effects

    features = SimpleNamespace(
        has_feature=lambda character, feature_id: feature_id == "fiendish_vigor",
        feature_value=lambda character, feature_id, key, default=None: True,
    )
    rows = [{
        "slot_id": "fiendish_vigor",
        "spell_ref": "spell:false_life",
        "feature_id": "fiendish_vigor",
    }]
    paid_by_vigor = invocation_effects.temp_hp_maximized(
        features, {}, rows, 0, "spell:false_life", "fiendish_vigor",
    )
    assert paid_by_vigor is True
    paid_elsewhere = invocation_effects.temp_hp_maximized(
        features, {}, rows, 0, "spell:false_life", "some_other_slot",
    )
    assert paid_elsewhere is False
    with_a_slot = invocation_effects.temp_hp_maximized(
        features, {}, rows, 1, "spell:false_life", "",
    )
    assert with_a_slot is False


def test_false_life_is_castable_without_preparing_it() -> None:
    """False Life 不在魔契师表上，是免槽授予让它可施放——不是准备列表。"""

    engine, instance = _combat(["feature:fiendish_vigor"])
    combat = ruleset_runtime.state(instance)["combat"]
    view = engine._actor_view(instance, combat, "player:gm")
    assert "spell:false_life" in view["spell_refs"]
    assert "spell:false_life" not in (
        view.get("class_spell_refs") or []
    )


def test_eldritch_spear_applies_before_distant_spell_doubles() -> None:
    """先加后乘：先加 30×等级，Distant Spell 再翻倍。

    反过来的话 15 级魔契师只会多 30 尺，而不是多了之后再翻一倍。
    """

    from types import SimpleNamespace

    engine, instance = _combat([
        {"ref": "feature:eldritch_spear", "object_ref": "spell:eldritch_blast"},
    ], level=5)
    combat = ruleset_runtime.state(instance)["combat"]
    view = engine._actor_view(instance, combat, "player:gm")
    effect = engine.catalog.spell_effects["eldritch_blast"]
    blast = engine.spells.get("spell:eldritch_blast")
    distant = SimpleNamespace(has=lambda code: code == "distant_spell")
    # 5 级：120 + 30×5 = 270；再翻倍 = 540。
    assert engine._spell_effective_range(view, blast, effect) == 270
    assert engine._spell_effective_range(view, blast, effect, distant) == 540


def test_eldritch_spear_scales_with_the_warlock_level() -> None:
    """加值跟着等级走，不是建卡时算一次就固定。"""

    from types import SimpleNamespace

    for level, expected in ((2, 120 + 60), (5, 120 + 150)):
        engine, instance = _combat([
            {"ref": "feature:eldritch_spear", "object_ref": "spell:eldritch_blast"},
        ], level=level)
        combat = ruleset_runtime.state(instance)["combat"]
        view = engine._actor_view(instance, combat, "player:gm")
        effect = engine.catalog.spell_effects["eldritch_blast"]
        blast = engine.spells.get("spell:eldritch_blast")
        assert engine._spell_effective_range(view, blast, effect) == expected, level


def test_repelling_blast_does_not_push_on_a_miss() -> None:
    """未命中不推：推挤挂在命中之后。"""

    engine, instance = _combat([
        {"ref": "feature:repelling_blast", "object_ref": "spell:eldritch_blast"},
    ])
    # 哥布林 AC 12，这一掷总计 2 + 攻击加值仍不足。
    events = _blast_events(engine, instance, rolls=[2, 7], spell_push_ft=10)
    assert not [
        event for event in events
        if event["type"] == "dnd2024.position.changed"
    ]


def test_repelling_blast_refuses_a_cantrip_without_an_attack_roll() -> None:
    """绑到豁免型戏法上时，提交推挤要被拒（不是静默不推）。"""

    engine, instance = _combat([
        {"ref": "feature:repelling_blast", "object_ref": "spell:sacred_flame"},
    ])
    # sacred_flame 不在魔契师表上，只能挂进已准备列表来构造这个越界场景：
    # 目的是证明"绑到豁免型戏法上时，提交推挤会被拒"，不是证明它能被准备。
    character = instance.players["gm"]["character_sheet"]["ruleset_character"]
    character["spellcasting"]["class"]["prepared_spell_refs"] = [
        "spell:sacred_flame",
    ]
    refused = _cast(engine, instance, {
        "type": "cast_spell", "spell_ref": "spell:sacred_flame",
        "slot_level": 0, "target_id": "enemy:goblin-1", "spell_push_ft": 5,
    }, rolls=[15, 3])
    assert refused["ok"] is False
    assert "attack roll" in _refusal(refused)


def test_eldritch_mind_leaves_other_saves_at_one_die() -> None:
    """优势只给"维持专注"的那一次，普通 Con 豁免仍是单骰。"""

    engine, instance = _combat(["feature:eldritch_mind"], level=2)
    combat = ruleset_runtime.state(instance)["combat"]
    target = engine._actor_view(instance, combat, "player:gm")
    save = engine._saving_throw_check(
        instance, combat, target, "player:gm", "con", 15, "enemy:goblin-1",
        [], SequenceRng([5]),
    )
    assert save["rolls"] == [5]
    assert "advantage_sources" not in save


def test_witch_sight_comes_at_fifteen_not_fourteen() -> None:
    for level, expected in ((14, ""), (15, "truesight")):
        engine, instance = _combat(["feature:witch_sight"], level=level)
        combat = ruleset_runtime.state(instance)["combat"]
        view = engine._actor_view(instance, combat, "player:gm")
        codes = vision_codes(engine, view, distance=20)
        assert (expected in codes) is bool(expected), level


def test_the_pool_record_survives_save_and_replay() -> None:
    """持久化契约：dict 形状的绑定选择经过 save/load 与逐级 replay 后不丢。"""

    runtime, sheet = _sheet()
    preview, choices = _plan(runtime, sheet, picks=LEVEL_TWO_PICKS)
    assert preview["ok"] is True, preview["errors"]
    advanced = runtime.apply_advancement(None, sheet, choices)
    before = _pool_record(advanced["ruleset_character"])

    for _ in range(2):
        advanced = runtime.normalize_character_submission(None, advanced)
    after = _pool_record(advanced["ruleset_character"])
    assert after["selected_refs"] == before["selected_refs"]
    resolver = Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))
    assert resolver.owns_binding(
        advanced["ruleset_character"], "agonizing_blast", "spell:eldritch_blast",
    )
