"""S0-F18: the Hunter's Mark instance, and the four features that read it.

The slice is one data shape with five readers: the caster's own concentration
instance carries ``mark_target_id``, and Favored Enemy (the free-cast pool),
Relentless Hunter (a damage exemption), Precise Hunter (Advantage), Feral
Senses (Blindsight as a *range*) and Foe Slayer (a die) are each one narrow
question asked of that instance.  Every test therefore goes through the chain
the web layer uses -- ``available_intents`` -> ``resolve_intent`` ->
``apply_batch`` -> ``end_turn`` -- and never writes a mark, a free use or a
concentration by hand.

Three conventions this file relies on:

* a mark is *caster-bound state*, so "two Rangers on one creature" and "an
  ally attacks the marked creature" are answered by which instance is read,
  not by a condition on the target;
* a sense is a *range*, so the same creature cancels a Blur at 30 feet and
  does not at 31 -- no test asserts a boolean;
* the spell catalog keeps the printed ``1d6``; only the owning caster's Foe
  Slayer replaces the die, and the catalog is asserted unchanged.

The item numbers in the docstrings are the task spec's matrix.  The legs the
engine cannot express yet (spell components, cover, a clock for the declared
duration, an explicit "end my concentration" intent, an enemy caster that can
incapacitate, death-driven concentration loss) are listed in the execution
report instead of being faked here.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.combat import Dnd2024CombatEngine
from src.rulesets.dnd2024.combat.check_modes import (
    HUNTERS_MARK_REF,
    attack_mode,
    observer_senses,
    vision_codes,
)
from src.rulesets.dnd2024.combat.triggered_spells import free_cast_resource
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.resting import Dnd2024RestEngine
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from dnd2024_monk_common import (
    SequenceRng,
    advancement_choices,
    goblin,
    monk_draft,
    monk_instance,
)

LOCALE = "en"
MARK = "spell:hunter_s_mark"
SECOND_CONCENTRATION = "spell:protection_from_energy"
SECOND = "enemy:goblin-2"
THIRD = "enemy:goblin-3"

# 标准数组的 15/14/13/12/10/8：游侠要的是敏捷 15，其余只影响 HP 与豁免。
RANGER_ABILITIES = {
    "str": 10, "dex": 15, "con": 14, "int": 8, "wis": 13, "cha": 12,
}
RANGER_SKILLS = ["skill:stealth", "skill:perception", "skill:survival"]
FIGHTER_ABILITIES = {
    "str": 15, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 8,
}
FIGHTER_SKILLS = ["skill:athletics", "skill:perception"]

#: The second concentration spell a Ranger really has.  Preferred into the
#: prepared list so "a new concentration spell ends the mark" is proved with a
#: spell the character actually casts, not with a hand-written instance.
PREFERRED = (SECOND_CONCENTRATION,)


# --------------------------------------------------------------------------
# sheets: the real builder and the real advancement path
# --------------------------------------------------------------------------

_RUNTIME = Dnd2024Runtime()
_SHEETS: dict[tuple, dict] = {}


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    """One runtime for the module: every sheet helper caches against it."""

    return _RUNTIME


def _spell_choices(class_ref: str, level: int, prefer: tuple[str, ...] = ()) -> dict:
    options = Dnd2024SpellSelection(_RUNTIME.load_bundle(LOCALE)).options(
        class_ref, level
    )
    requirements = options["requirements"]
    pool = [str(row["ref"]) for row in options["leveled_spells"]]
    wanted = [ref for ref in prefer if ref in pool]
    ordered = wanted + [ref for ref in pool if ref not in wanted]
    return {
        "cantrip_refs": [str(row["ref"]) for row in options["cantrips"]][
            : int(requirements["cantrip_count"])
        ],
        "prepared_spell_refs": ordered[: int(requirements["prepared_spell_count"])],
        "spellbook_refs": [],
    }


def _advance(
    sheet: dict, level: int, class_ref: str, prefer: tuple[str, ...] = (),
) -> dict:
    while int(sheet["ruleset_character"]["build"]["level"]) < level:
        next_level = int(sheet["ruleset_character"]["build"]["level"]) + 1
        picks = advancement_choices(_RUNTIME.preview_advancement(None, sheet, {}))
        picks["class_spell_choices"] = _spell_choices(class_ref, next_level, prefer)
        preview = _RUNTIME.preview_advancement(None, sheet, picks)
        assert preview["ok"] is True, (preview["errors"], picks)
        sheet = _RUNTIME.apply_advancement(None, sheet, picks)
    return sheet


def _draft(
    class_ref: str, package: str, abilities: dict[str, int], skills: list[str],
    extra: dict | None = None,
) -> dict:
    draft = monk_draft(_RUNTIME, LOCALE)
    draft.update(
        class_ref=class_ref,
        class_tool_refs=[],
        equipment_package_ref=package,
        base_abilities=dict(abilities),
        class_skill_refs=list(skills),
    )
    # 技能与工具从 builder 读回：写死一份列表会在该职业的可选集变化时
    # 悄悄造出一个不该通过校验的角色。
    choices = _RUNTIME.builder_choices(None, draft)
    draft["class_tool_refs"] = [row["ref"] for row in choices["class_tools"]][
        : choices["class_tool_count"]
    ]
    draft["species_skill_refs"] = [
        str(row["ref"]) for row in choices["species_skills"]
        if str(row["ref"]) not in skills
    ][: choices["species_skill_count"]]
    draft.update(extra or {})
    return draft


def ranger(level: int = 1, *, second_concentration: bool = False) -> dict:
    """A real Ranger grown through the real level-up path."""

    key = ("ranger", level, second_concentration)
    if key not in _SHEETS:
        prefer = PREFERRED if second_concentration else ()
        draft = _draft(
            "class:ranger", "equipment_package:ranger_a", RANGER_ABILITIES,
            RANGER_SKILLS,
        )
        draft["class_spell_choices"] = _spell_choices("class:ranger", 1, prefer)
        sheet = _RUNTIME.finalize_character(None, draft)
        _SHEETS[key] = _advance(sheet, level, "class:ranger", prefer)
    return deepcopy(_SHEETS[key])


def fighter(level: int = 1) -> dict:
    """A Marshal with no Ranger level: the control for "you do not get this"."""

    key = ("fighter", level)
    if key not in _SHEETS:
        draft = _draft(
            "class:fighter", "equipment_package:fighter_b", FIGHTER_ABILITIES,
            FIGHTER_SKILLS, {"class_feature_feat_choices": {"fighting_style": "feat:archery"}},
        )
        draft["class_spell_choices"] = _spell_choices("class:fighter", 1)
        sheet = _RUNTIME.finalize_character(None, draft)
        _SHEETS[key] = _advance(sheet, level, "class:fighter")
    return deepcopy(_SHEETS[key])


def _resolver(locale: str = LOCALE) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(_RUNTIME.load_bundle(locale))


# --------------------------------------------------------------------------
# combat plumbing
# --------------------------------------------------------------------------

def _apply(engine, instance, intent: dict, rolls=()) -> list[dict]:
    # 只在调用方没给 version 时补上当前版本：伪造过期版本是矩阵 22 的用例。
    intent = {
        "expected_version": int(ruleset_runtime.state(instance).get("version", 0)),
        **intent,
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng(list(rolls)))
    assert resolved["ok"] is True, resolved
    applied = engine.apply_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True, applied
    return resolved["event_batch"]["events"]


def _rejected(engine, instance, intent: dict) -> str:
    intent = {
        "expected_version": int(ruleset_runtime.state(instance).get("version", 0)),
        **intent,
    }
    resolved = engine.resolve_intent(instance, intent, SequenceRng([]))
    assert resolved["ok"] is False, resolved
    return str(resolved.get("error") or "")


def _combat(
    players: dict[str, dict], initiative: dict[str, int],
    enemies: list[dict] | None = None,
) -> tuple[Dnd2024CombatEngine, Any]:
    """Start a fight with a chosen turn order (``combat.start`` rolls by uid)."""

    first_uid = next(iter(players))
    engine, instance = monk_instance(_RUNTIME, deepcopy(players[first_uid]), uid=first_uid)
    for uid, sheet in list(players.items())[1:]:
        instance.players[uid] = {
            "character_name": uid.title(), "character_sheet": deepcopy(sheet),
        }
    order = sorted(instance.players)
    _apply(engine, instance, {
        "intent_id": "start", "type": "combat.start", "expected_version": 0,
        "submitted_by": first_uid,
        "enemies": enemies if enemies is not None else [goblin(position=20, hp=200)],
    }, [initiative[uid] for uid in order] + [1])
    return engine, instance


def _combat_state(instance) -> dict:
    return ruleset_runtime.state(instance)["combat"]


def _current(instance) -> str:
    combat = _combat_state(instance)
    return str(combat["initiative"][combat["turn_index"]])


def _end_turn(engine, instance) -> list[dict]:
    actor_id = _current(instance)
    uid = "gm" if actor_id.startswith("enemy:") else actor_id.split(":", 1)[1]
    return _apply(engine, instance, {
        "intent_id": f"end-{ruleset_runtime.state(instance)['version']}",
        "type": "end_turn", "submitted_by": uid, "actor_id": actor_id,
    })


def _to_actor(engine, instance, actor_id: str) -> None:
    for _ in range(12):
        if _current(instance) == actor_id:
            return
        _end_turn(engine, instance)
    raise AssertionError(f"the requested actor never got a turn: {actor_id}")


def _next_turn(engine, instance, actor_id: str = "player:gm") -> None:
    """Close this turn and come back round: the only way economy is restored."""

    _end_turn(engine, instance)
    _to_actor(engine, instance, actor_id)


def _actor(engine, instance, actor_id: str = "player:gm") -> dict:
    return engine._actor_view(instance, _combat_state(instance), actor_id)


def _canonical(instance, uid: str = "gm") -> dict:
    return instance.get_character_sheet(uid)["ruleset_character"]


def _mark(instance, uid: str = "gm") -> dict:
    return dict(_canonical(instance, uid)["spellcasting"]["class"].get("concentration") or {})


def _resource(instance, uid: str = "gm") -> dict:
    return dict(_canonical(instance, uid)["resources"]["class"].get("favored_enemy") or {})


def _slots(instance, uid: str = "gm") -> dict:
    return dict(_canonical(instance, uid)["spellcasting"]["class"]["slots_current"])


def _cast_mark(
    engine, instance, *, uid: str = "gm", slot_level: int = 0,
    target_id: str = "enemy:goblin-1", rolls=(),
) -> list[dict]:
    return _apply(engine, instance, {
        "intent_id": f"mark-{uid}-{ruleset_runtime.state(instance)['version']}",
        "type": "cast_spell", "submitted_by": uid, "actor_id": f"player:{uid}",
        "spell_ref": MARK, "slot_level": slot_level, "target_ids": [target_id],
    }, rolls)


def _mark_intent(*, uid: str = "gm", slot_level: int = 0, target_id="enemy:goblin-1") -> dict:
    return {
        "intent_id": f"mark-intent-{uid}", "type": "cast_spell",
        "submitted_by": uid, "actor_id": f"player:{uid}",
        "spell_ref": MARK, "slot_level": slot_level, "target_ids": [target_id],
    }


def _retarget_intent(
    *, uid: str = "gm", target_id: str = SECOND, targets: list[str] | None = None,
) -> dict:
    return {
        "intent_id": f"retarget-{uid}-{target_id}", "type": "cast_spell",
        "submitted_by": uid, "actor_id": f"player:{uid}",
        "spell_ref": MARK, "slot_level": 0,
        "target_ids": list(targets) if targets is not None else [target_id],
        "retarget_mark": True,
    }


def _attack_intent(instance, *, uid: str = "gm", target_id: str = "enemy:goblin-1", **extra) -> dict:
    intent: dict[str, Any] = {
        "intent_id": f"attack-{uid}-{ruleset_runtime.state(instance)['version']}",
        "type": "attack", "submitted_by": uid, "actor_id": f"player:{uid}",
        "target_id": target_id, "weapon_ref": "item:longbow",
    }
    intent.update(extra)
    return intent


def _attack(engine, instance, *, uid: str = "gm", rolls=(), **extra) -> list[dict]:
    return _apply(engine, instance, _attack_intent(instance, uid=uid, **extra), rolls)


def _check(events: list[dict], kind: str) -> dict:
    return next(
        event for event in events
        if event.get("type") == "check.resolved" and event.get("kind") == kind
    )


def _damage(events: list[dict], damage_type: str = "") -> list[dict]:
    return [
        event for event in events
        if event["type"] == "resource.changed"
        and event.get("resource") == "hp"
        and event.get("damage_type", "") == damage_type
    ]


def _concentration(events: list[dict]) -> list[dict]:
    return [
        {key: value for key, value in event.items() if key != "type"}
        for event in events
        if str(event["type"]).startswith("dnd2024.concentration")
    ]


def _add_enemy_condition(instance, condition: str, *, enemy: str = "goblin-1", **meta) -> None:
    """Persist a condition the way a legacy save or another spell would.

    The registry owns what the condition *does*; the test only says it is
    there, which is exactly the shape a reloaded save has.
    """

    enemies = _combat_state(instance)["enemies"]
    enemies[enemy].setdefault("conditions", {})[condition] = {"source": "test", **meta}


# --------------------------------------------------------------------------
# 1-4. what Favored Enemy grants, and how the pool behaves
# --------------------------------------------------------------------------

def test_matrix_01_the_five_declarations_land_at_their_own_levels(runtime) -> None:
    """Matrix 1: granted at 1/13/17/18/20, absent one level below, dual labels."""

    resolver = _resolver()
    high = {"relentless_hunter", "precise_hunter", "feral_senses", "foe_slayer"}
    expected = {12: set(), 13: {"relentless_hunter"}, 14: {"relentless_hunter"},
                16: {"relentless_hunter"},
                17: {"relentless_hunter", "precise_hunter"},
                18: {"relentless_hunter", "precise_hunter", "feral_senses"},
                19: {"relentless_hunter", "precise_hunter", "feral_senses"},
                20: high}

    for level, ids in expected.items():
        character = ranger(level)["ruleset_character"]
        owned = {feature.id for feature in resolver.features_for(character)}
        assert owned & high == ids, (level, sorted(owned))
        # Favored Enemy itself never goes away.
        assert "favored_enemy" in owned

    sources = {
        feature.id: feature.source_ref
        for feature in resolver.features_for(ranger(20)["ruleset_character"])
        if feature.id in {"favored_enemy", *high}
    }
    assert sources == {
        "favored_enemy": "srd-5.2.1:p58:favored-enemy",
        "relentless_hunter": "srd-5.2.1:p59:relentless-hunter",
        "precise_hunter": "srd-5.2.1:p59:precise-hunter",
        "feral_senses": "srd-5.2.1:p59:feral-senses",
        "foe_slayer": "srd-5.2.1:p59:foe-slayer",
    }

    # The numbers are read from the declarations, never from the call sites.
    top = ranger(20)["ruleset_character"]
    assert resolver.feature_value(top, "favored_enemy", "granted_spell_id") == "hunter_s_mark"
    assert resolver.feature_value(
        top, "relentless_hunter", "concentration_damage_exempt_spell_id",
    ) == "hunter_s_mark"
    assert resolver.feature_value(
        top, "precise_hunter", "marked_target_attack_advantage",
    ) is True
    assert resolver.feature_value(top, "feral_senses", "blindsight_range_ft") == 30
    assert resolver.feature_value(top, "foe_slayer", "mark_damage_die") == 10

    chinese = _resolver("zh-CN")
    assert [chinese.catalog.label(feature) for feature in (
        "favored_enemy", "relentless_hunter", "precise_hunter", "feral_senses",
        "foe_slayer",
    )] == ["宿敌", "不懈追猎者", "精准追猎者", "野性感知", "宿敌克星"]
    assert [resolver.catalog.label(feature) for feature in (
        "favored_enemy", "relentless_hunter", "precise_hunter", "feral_senses",
        "foe_slayer",
    )] == [
        "Favored Enemy", "Relentless Hunter", "Precise Hunter", "Feral Senses",
        "Foe Slayer",
    ]


def test_matrix_02_the_spell_is_always_prepared_and_never_a_prepared_slot() -> None:
    """Matrix 2: a grant, not a prepared spell -- and not a class-wide one."""

    sheet = ranger(1)
    magic = sheet["ruleset_character"]["spellcasting"]["class"]
    # 1 级游侠只有 2 个准备位，而 Hunter's Mark 不在其中任何一位上。
    assert len(magic["prepared_spell_refs"]) == 2
    assert MARK not in magic["prepared_spell_refs"]

    engine, instance = _combat({"gm": sheet}, {"gm": 20})
    actor = _actor(engine, instance)
    assert MARK in actor["spell_refs"]
    # 同一份投影里 `class_spell_refs` 才是"占了准备位的法术"，它不含这条授予。
    assert MARK not in actor["class_spell_refs"]
    grant = next(row for row in actor["spell_grants"] if row["spell_ref"] == MARK)
    assert grant["resource_id"] == "favored_enemy"
    # "目录里有条目"不等于"能结算"：这个状态必须从效果派生。
    assert grant["automation_status"] == "automated"

    # 非 Ranger 不会因为目录有条目就拿到宿敌。
    resolver = _resolver()
    marshal = fighter(1)["ruleset_character"]
    assert not resolver.has_feature(marshal, "favored_enemy")
    assert "favored_enemy" not in marshal["resources"]["class"]

    other, other_instance = _combat({"gm": fighter(1)}, {"gm": 20})
    other_actor = _actor(other, other_instance)
    assert MARK not in other_actor["spell_refs"]
    assert all(row["spell_ref"] != MARK for row in other_actor["spell_grants"])


def test_matrix_03_the_free_cast_track_follows_the_class_table(runtime) -> None:
    """Matrix 3: 2/2/3/3/4/4/5/5/6/6, and a level-up never refunds a use."""

    rest = Dnd2024RestEngine(runtime.load_bundle(LOCALE))
    expected = {
        1: 2, 4: 2, 5: 3, 8: 3, 9: 4, 12: 4, 13: 5, 16: 5, 17: 6, 20: 6,
    }
    for level, maximum in expected.items():
        synced = rest.sync_resources(ranger(level))
        row = synced["resources"]["class"]["favored_enemy"]
        assert (row["current"], row["maximum"]) == (maximum, maximum), level
        assert row["source_ref"] == "srd-5.2.1:p58:favored-enemy"

    # 花掉两次再升 4 级：新的上限只补上新给的额度，已花掉的那两次仍是花掉的。
    spent = ranger(1)
    spent["ruleset_character"]["resources"]["class"]["favored_enemy"]["current"] = 0
    grown = _advance(spent, 5, "class:ranger")
    row = grown["ruleset_character"]["resources"]["class"]["favored_enemy"]
    assert (row["current"], row["maximum"]) == (1, 3)


def test_matrix_04_a_free_cast_costs_the_pool_and_never_a_slot() -> None:
    """Matrix 4: pool first, slot when the pool is empty, rest boundaries."""

    engine, instance = _combat({"gm": ranger(1)}, {"gm": 20})

    events = _cast_mark(engine, instance)
    assert {"type": "dnd2024.action.spent", "actor_id": "player:gm",
            "resource": "bonus_action", "amount": 1} in events
    spent = next(
        event for event in events if event["type"] == "dnd2024.class_resource.spent"
    )
    assert (spent["resource_id"], spent["amount"]) == ("favored_enemy", 1)
    assert _resource(instance)["current"] == 1
    assert _slots(instance) == {"1": 2}
    # 免费施法不是"这个法术免费"的布尔值，而是可选的支付方式之一。
    assert not any(event["type"].startswith("dnd2024.spell.slot") for event in events)
    # 起源专长的免费池是另一个来源，这一笔不许冒充它。
    assert not any("feat_spell" in event["type"] for event in events)

    _next_turn(engine, instance)
    _cast_mark(engine, instance)
    assert _resource(instance)["current"] == 0
    assert _slots(instance) == {"1": 2}

    _next_turn(engine, instance)
    row = next(
        item for item in engine.available_intents(instance, "gm")
        if item["type"] == "cast_spell"
    )
    mark_row = next(entry for entry in row["spells"] if entry["spell_ref"] == MARK)
    # 免费次数用尽后，服务端选项里不再出现 0 环这一档。
    assert mark_row["available_slot_levels"] == [1]
    assert "the free spell cast is not available" in _rejected(
        engine, instance, _mark_intent(),
    )

    events = _cast_mark(engine, instance, slot_level=1)
    assert any(
        event["type"] == "dnd2024.spell.slot_spent" and event["slot_level"] == 1
        for event in events
    )
    assert _slots(instance) == {"1": 1}
    assert _resource(instance)["current"] == 0

    # 长休全恢复并结束专注；短休一点也不给。
    rest = Dnd2024RestEngine(_RUNTIME.load_bundle(LOCALE))
    short = rest.complete_short_rest(deepcopy(_canonical(instance)), {})
    assert short["character"]["resources"]["class"]["favored_enemy"]["current"] == 0
    long_rest = rest.complete_long_rest(deepcopy(_canonical(instance)))
    assert long_rest["character"]["resources"]["class"]["favored_enemy"]["current"] == 2
    assert long_rest["character"]["spellcasting"]["class"]["concentration"] is None


# --------------------------------------------------------------------------
# 5-6. what a cast validates and what the instance remembers
# --------------------------------------------------------------------------

def test_matrix_05_the_cast_boundary_is_the_existing_spell_boundary() -> None:
    """Matrix 5: Bonus Action, 90 ft, one visible creature, Concentration."""

    spell = next(
        row for row in _RUNTIME.load_bundle(LOCALE).get(
            "spell_catalog", "srd_spells"
        )["spells"] if row["id"] == "hunter_s_mark"
    )
    assert spell["casting_time"] == "Bonus Action"
    assert spell["range"] == "90 feet"
    assert spell["concentration"] is True
    assert spell["components"] == ["V"]
    assert spell["source_ref"] == "srd-5.2.1:p141:hunter_s_mark"

    engine, instance = _combat(
        {"gm": ranger(5)}, {"gm": 20},
        enemies=[goblin(position=95, hp=200),
                 {**goblin(position=20, hp=200), "id": "goblin-2", "name": "G2"}],
    )
    assert "target is out of range" in _rejected(engine, instance, _mark_intent())
    assert "spell requires 1 to 1 unique targets" in _rejected(
        engine, instance,
        _mark_intent() | {"target_ids": ["enemy:goblin-2", "enemy:goblin-2"]},
    )
    # 免费施放不可伪造：5 级游侠有 3 次，但把 slot_level 说成 0 仍要走真池子，
    # 消耗过的次数不能靠字段复活。
    events = _cast_mark(engine, instance, target_id="enemy:goblin-2")
    assert _concentration(events) == [{
        "actor_id": "player:gm", "spell_ref": MARK,
        "target_ids": ["enemy:goblin-2"], "mark_target_id": "enemy:goblin-2",
        "mark_expires_hours": 1,
    }]
    assert "the bonus action has already been spent" in _rejected(
        engine, instance, _mark_intent() | {"target_ids": ["enemy:goblin-2"]},
    )

    # 看不见的生物不能被标记：只有 Invisible 的抵消来源能改变这个答案。
    blind_engine, blind_instance = _combat(
        {"gm": ranger(5)}, {"gm": 20}, enemies=[goblin(position=20, hp=200)],
    )
    _add_enemy_condition(blind_instance, "invisible")
    assert "you cannot see the creature you are marking" in _rejected(
        blind_engine, blind_instance, _mark_intent(),
    )


def test_matrix_06_the_instance_carries_its_target_and_its_declared_duration() -> None:
    """Matrix 6: 1/8/24 hours by slot, one caster, one ``mark_target_id``."""

    engine, instance = _combat(
        {"gm": ranger(17)}, {"gm": 20},
        enemies=[goblin(position=20, hp=200),
                 {**goblin(position=25, hp=200), "id": "goblin-2", "name": "G2"}],
    )
    _cast_mark(engine, instance)
    assert _mark(instance) == {
        "spell_ref": MARK, "target_ids": ["enemy:goblin-1"],
        "mark_target_id": "enemy:goblin-1", "mark_expires_hours": 1,
    }

    for slot_level, hours in ((3, 8), (5, 24)):
        _next_turn(engine, instance)
        events = _cast_mark(engine, instance, slot_level=slot_level)
        started = next(
            event for event in events
            if event["type"] == "dnd2024.concentration.started"
        )
        assert started["mark_expires_hours"] == hours
        assert _mark(instance)["mark_expires_hours"] == hours

    # 换一个目标重施：旧实例先结束，所以同一施法者名下只有一个标记。
    _next_turn(engine, instance)
    events = _cast_mark(engine, instance, target_id=SECOND)
    assert _concentration(events) == [
        {"actor_id": "player:gm", "reason": "new_concentration_spell"},
        {"actor_id": "player:gm", "spell_ref": MARK, "target_ids": [SECOND],
         "mark_target_id": SECOND, "mark_expires_hours": 1},
    ]
    assert _mark(instance)["mark_target_id"] == SECOND


# --------------------------------------------------------------------------
# 7-9. the die, who owns it, and the checks it also helps
# --------------------------------------------------------------------------

def test_matrix_07_every_hit_this_turn_rolls_the_die_again() -> None:
    """Matrix 7: any attack roll on the marked creature, no per-turn limit."""

    engine, instance = _combat(
        {"gm": ranger(5)}, {"gm": 20},
        enemies=[goblin(position=20, hp=200),
                 {**goblin(position=25, hp=200), "id": "goblin-2", "name": "G2"}],
    )
    _cast_mark(engine, instance)

    first = _attack(engine, instance, rolls=[13, 5, 4])
    second = _attack(engine, instance, rolls=[13, 7, 6])
    for events, weapon_roll, mark_roll in ((first, 5, 4), (second, 7, 6)):
        assert _damage(events, "piercing")[0]["rolls"] == [weapon_roll]
        force = _damage(events, "force")
        assert len(force) == 1
        assert force[0]["rolls"] == [mark_roll]
        assert force[0]["feature_id"] == "hunter_s_mark"
        assert force[0]["owner_ref"] == MARK
        assert force[0]["amount"] == mark_roll

    # 打的是别的生物：武器伤害照旧，标记一分不给。Extra Attack 一回合只有
    # 两刀，所以第三刀要等下一个自己的回合。
    _next_turn(engine, instance)
    other = _attack(engine, instance, target_id=SECOND, rolls=[13, 5])
    assert _damage(other, "force") == []


def test_matrix_08_the_die_belongs_to_the_caster_not_to_the_target() -> None:
    """Matrix 8: an ally gets nothing, a second Ranger gets its own die."""

    engine, instance = _combat(
        {"gm": ranger(5), "ally": ranger(5)}, {"gm": 20, "ally": 15},
    )
    _cast_mark(engine, instance)
    # 盟友没有自己的实例：同一个目标，同样的攻击，就是没有那一骰。
    _to_actor(engine, instance, "player:ally")
    ally = _attack(engine, instance, uid="ally", rolls=[13, 5])
    assert _damage(ally, "force") == []
    assert _mark(instance, "ally") == {}

    # 两名 Ranger 都标记同一只生物：各读自己的实例，各掷自己的一骰。
    _next_turn(engine, instance, "player:ally")
    _cast_mark(engine, instance, uid="ally")
    assert _mark(instance)["mark_target_id"] == "enemy:goblin-1"
    assert _mark(instance, "ally")["mark_target_id"] == "enemy:goblin-1"

    _next_turn(engine, instance, "player:ally")
    ally_events = _attack(engine, instance, uid="ally", rolls=[13, 5, 6])
    assert [event["rolls"] for event in _damage(ally_events, "force")] == [[6]]
    _next_turn(engine, instance)
    gm_events = _attack(engine, instance, rolls=[13, 5, 2])
    assert [event["rolls"] for event in _damage(gm_events, "force")] == [[2]]

    # 抗性是既有边界：它只作用于标记这一份独立伤害实例。
    _next_turn(engine, instance)
    _add_enemy_condition(instance, "force_ward", resist_damage_types=["force"])
    events = _attack(engine, instance, rolls=[13, 5, 4])
    force = _damage(events, "force")[0]
    assert (force["amount"], force["resisted"], force["resistance"]) == (
        2, True, "force_ward",
    )
    assert _damage(events, "piercing")[0]["amount"] == 7


def test_matrix_09_finding_the_quarry_is_still_a_skill_check() -> None:
    """Matrix 9: Perception and Survival, that creature only, nothing else."""

    engine, instance = _combat(
        {"gm": ranger(5)}, {"gm": 20},
        enemies=[goblin(position=20, hp=200),
                 {**goblin(position=20, hp=200), "id": "goblin-2", "name": "G2"}],
    )
    _cast_mark(engine, instance)

    advantage = [{"code": "hunter_s_mark", "mode": "advantage"}]
    assert engine.hunters_mark_find_advantage(
        instance, "gm", "perception", "enemy:goblin-1",
    ) == advantage
    assert engine.hunters_mark_find_advantage(
        instance, "gm", "survival", "enemy:goblin-1",
    ) == advantage
    # 别的技能、别的目标、别的属性都拿不到这个来源。
    assert engine.hunters_mark_find_advantage(
        instance, "gm", "stealth", "enemy:goblin-1",
    ) == []
    assert engine.hunters_mark_find_advantage(
        instance, "gm", "perception", SECOND,
    ) == []
    # 没有实例的盟友更没有。
    assert engine.hunters_mark_find_advantage(
        instance, "gm", "athletics", "enemy:goblin-1",
    ) == []


# --------------------------------------------------------------------------
# 10-12. the instance ends, moves, and refuses to move
# --------------------------------------------------------------------------

def test_matrix_10_a_new_concentration_spell_takes_the_mark_with_it() -> None:
    """Matrix 10: no sourceless mark survives the swap."""

    engine, instance = _combat(
        {"gm": ranger(17, second_concentration=True)}, {"gm": 20},
        enemies=[goblin(position=5, hp=300)],
    )
    _cast_mark(engine, instance)
    events = _attack(engine, instance, rolls=[13, 11, 5, 4])
    assert len(_damage(events, "force")) == 1

    _next_turn(engine, instance)
    swapped = _apply(engine, instance, {
        "intent_id": "second", "type": "cast_spell", "submitted_by": "gm",
        "actor_id": "player:gm", "spell_ref": SECOND_CONCENTRATION,
        "slot_level": 3, "target_ids": ["player:gm"], "resist_type": "fire",
    })
    assert _concentration(swapped) == [
        {"actor_id": "player:gm", "reason": "new_concentration_spell"},
        {"actor_id": "player:gm", "spell_ref": SECOND_CONCENTRATION,
         "target_ids": ["player:gm"]},
    ]
    assert _mark(instance) == {
        "spell_ref": SECOND_CONCENTRATION, "target_ids": ["player:gm"],
    }

    # 没有实例了：伤害与 Precise 一起消失，而不是留下一个没有来源的标记。
    _next_turn(engine, instance)
    after = _attack(engine, instance, rolls=[13, 5])
    assert _damage(after, "force") == []
    assert _check(after, "attack")["advantage_mode"] == ""
    assert _check(after, "attack")["rolls"] == [13]


def test_matrix_11_moving_the_mark_pays_nothing() -> None:
    """Matrix 11: a Bonus Action, the same instance, no second cast."""

    engine, instance = _combat(
        {"gm": ranger(17, second_concentration=True)}, {"gm": 20},
        enemies=[goblin(position=20, hp=1),
                 {**goblin(position=25, hp=200), "id": "goblin-2", "name": "G2"}],
    )
    _cast_mark(engine, instance, slot_level=3)
    before = _mark(instance)
    assert before["mark_expires_hours"] == 8
    resource_before = _resource(instance)
    slots_before = _slots(instance)

    _attack(engine, instance, rolls=[15, 10, 8, 4])
    assert _combat_state(instance)["enemies"]["goblin-1"]["hp"] == 0
    _next_turn(engine, instance)

    events = _apply(engine, instance, _retarget_intent())
    spent_bonus = next(
        event for event in events if event["type"] == "dnd2024.action.spent"
    )
    assert spent_bonus == {
        "type": "dnd2024.action.spent", "actor_id": "player:gm",
        "resource": "bonus_action", "amount": 1,
    }
    assert _concentration(events) == [{
        "actor_id": "player:gm", "spell_ref": MARK, "target_id": SECOND,
        "previous_target_id": "enemy:goblin-1",
    }]
    # 不是再施一次法：没有 spell.cast、没有扣槽、没有扣免费次数、时长不变。
    types = [event["type"] for event in events]
    assert "dnd2024.spell.cast" not in types
    assert not any("slot" in name for name in types)
    assert _resource(instance) == resource_before
    assert _slots(instance) == slots_before
    assert _mark(instance) == {
        "spell_ref": MARK, "target_ids": [SECOND],
        "mark_target_id": SECOND, "mark_expires_hours": 8,
    }
    # 旧目标立刻不再是我的猎物。
    assert engine.hunters_mark_find_advantage(
        instance, "gm", "perception", "enemy:goblin-1",
    ) == []
    assert engine.hunters_mark_find_advantage(
        instance, "gm", "perception", SECOND,
    ) == [{"code": "hunter_s_mark", "mode": "advantage"}]


def test_matrix_12_a_move_has_five_ways_to_be_illegal() -> None:
    """Matrix 12: alive target, range, sight, economy, and target count."""

    def downed() -> tuple[Dnd2024CombatEngine, Any]:
        engine, instance = _combat(
            {"gm": ranger(17)}, {"gm": 20},
            enemies=[goblin(position=20, hp=1),
                     {**goblin(position=25, hp=200), "id": "goblin-2", "name": "G2"},
                     {**goblin(position=95, hp=200), "id": "goblin-3", "name": "G3"}],
        )
        _cast_mark(engine, instance)
        return engine, instance

    engine, instance = downed()
    _next_turn(engine, instance)
    assert "the mark can only move after its target drops to 0" in _rejected(
        engine, instance, _retarget_intent(),
    )

    engine, instance = downed()
    _attack(engine, instance, rolls=[15, 10, 8, 4])
    # 同一个回合里 Bonus Action 已经交给施法了。
    assert "the bonus action has already been spent" in _rejected(
        engine, instance, _retarget_intent(),
    )
    _next_turn(engine, instance)
    assert "target is out of range (95 > 90 feet)" in _rejected(
        engine, instance, _retarget_intent(target_id=THIRD),
    )
    assert "the mark moves to exactly one creature" in _rejected(
        engine, instance, _retarget_intent(targets=[SECOND, THIRD]),
    )
    assert "the mark is already on that creature" in _rejected(
        engine, instance, _retarget_intent(target_id="enemy:goblin-1"),
    )

    # 17 级没有 Blindsight：看不见的新目标不能接。
    _add_enemy_condition(instance, "invisible", enemy="goblin-2")
    assert "you cannot see the new target" in _rejected(
        engine, instance, _retarget_intent(),
    )


# --------------------------------------------------------------------------
# 13-14. Relentless Hunter: one spell, one kind of ending
# --------------------------------------------------------------------------

def _goblin_turn_damage(engine, instance, rolls=(19, 5, 1)) -> list[dict]:
    _to_actor(engine, instance, "enemy:goblin-1")
    return _apply(engine, instance, {
        "intent_id": "goblin-attack", "type": "attack", "submitted_by": "gm",
        "actor_id": "enemy:goblin-1", "target_id": "player:gm",
        "attack_id": "scimitar",
    }, list(rolls))


def test_matrix_13_damage_stops_breaking_this_one_concentration() -> None:
    """Matrix 13: 12th level saves, 13th level does not -- HP still falls."""

    def strike(level: int) -> tuple[list[dict], int, int]:
        engine, instance = _combat({"gm": ranger(level)}, {"gm": 20},
                                   enemies=[goblin(position=5, hp=200)])
        _cast_mark(engine, instance)
        before = _actor(engine, instance)["hp"]
        events = _goblin_turn_damage(engine, instance)
        return events, before, _actor(engine, instance)["hp"]

    control, control_before, control_after = strike(12)
    assert _check(control, "concentration")["success"] is False
    assert _concentration(control)[0]["reason"] == "failed_concentration_save"

    shielded, shielded_before, shielded_after = strike(13)
    # 没有掷骰、也没有一条假的"豁免失败"记录：这个豁免根本没发生。
    assert not any(
        event.get("type") == "check.resolved" and event.get("kind") == "concentration"
        for event in shielded
    )
    assert _concentration(shielded) == []
    assert shielded_after == shielded_before - 7
    assert control_after == control_before - 7


def test_matrix_14_the_exemption_covers_one_spell_and_only_damage() -> None:
    """Matrix 14: another concentration still breaks; a new one still swaps."""

    engine, instance = _combat(
        {"gm": ranger(13, second_concentration=True)}, {"gm": 20},
        enemies=[goblin(position=5, hp=200),
                 {**goblin(position=10, hp=200), "id": "goblin-2", "name": "G2"}],
    )
    _apply(engine, instance, {
        "intent_id": "second", "type": "cast_spell", "submitted_by": "gm",
        "actor_id": "player:gm", "spell_ref": SECOND_CONCENTRATION,
        "slot_level": 3, "target_ids": ["player:gm"], "resist_type": "fire",
    })
    events = _goblin_turn_damage(engine, instance)
    assert _check(events, "concentration")["success"] is False
    assert _concentration(events)[0]["reason"] == "failed_concentration_save"

    # 伤害豁免不是"专注免疫"：换成 Hunter's Mark 之后，重施仍然结束旧实例。
    engine, instance = _combat(
        {"gm": ranger(13)}, {"gm": 20},
        enemies=[goblin(position=5, hp=200),
                 {**goblin(position=10, hp=200), "id": "goblin-2", "name": "G2"}],
    )
    _cast_mark(engine, instance)
    _next_turn(engine, instance)
    swapped = _cast_mark(engine, instance, target_id=SECOND)
    assert _concentration(swapped)[0] == {
        "actor_id": "player:gm", "reason": "new_concentration_spell",
    }
    assert _mark(instance)["mark_target_id"] == SECOND


# --------------------------------------------------------------------------
# 15-16. Precise Hunter follows the instance, wherever it goes
# --------------------------------------------------------------------------

def _attack_mode(engine, instance, target_id: str, uid: str = "gm") -> dict:
    return attack_mode(
        engine, instance, _actor(engine, instance, f"player:{uid}"),
        _actor(engine, instance, target_id), False,
    )


def _precise(engine, instance, target_id: str, uid: str = "gm") -> bool:
    return any(
        source.get("code") == "precise_hunter"
        for source in _attack_mode(engine, instance, target_id, uid)["sources"]
    )


def test_matrix_15_advantage_belongs_to_the_owner_of_the_mark() -> None:
    """Matrix 15: own quarry only -- not another creature, not another Ranger."""

    engine, instance = _combat(
        {"gm": ranger(17), "ally": ranger(17)}, {"gm": 20, "ally": 15},
        enemies=[goblin(position=20, hp=300),
                 {**goblin(position=20, hp=300), "id": "goblin-2", "name": "G2"}],
    )
    _cast_mark(engine, instance)
    assert _precise(engine, instance, "enemy:goblin-1") is True
    assert _precise(engine, instance, SECOND) is False
    # 别人的实例不算：盟友没标记，攻击被标记的生物也没有优势。
    assert _precise(engine, instance, "enemy:goblin-1", uid="ally") is False

    # 17 级之前连自己也没有。
    low = _combat({"gm": ranger(16)}, {"gm": 20},
                  enemies=[goblin(position=20, hp=200)])[0:2]
    _cast_mark(low[0], low[1])
    assert _precise(low[0], low[1], "enemy:goblin-1") is False

    # 完整的攻击链上也一样：优势来源只有一条，寻找检定另有自己的来源。
    events = _attack(engine, instance, rolls=[13, 11, 5, 4])
    check = _check(events, "attack")
    assert check["advantage_mode"] == "advantage"
    assert {"code": "precise_hunter", "mode": "advantage"} in check["advantage_sources"]
    assert engine.hunters_mark_find_advantage(
        instance, "gm", "perception", "enemy:goblin-1",
    ) == [{"code": "hunter_s_mark", "mode": "advantage"}]


def test_matrix_16_precise_never_disagrees_with_the_instance() -> None:
    """Matrix 16: cast, retarget, reload, end -- the same one answer."""

    engine, instance = _combat(
        {"gm": ranger(17)}, {"gm": 20},
        enemies=[goblin(position=20, hp=1),
                 {**goblin(position=25, hp=300), "id": "goblin-2", "name": "G2"}],
    )

    def agrees() -> bool:
        marked = _mark(instance).get("mark_target_id")
        for target_id in ("enemy:goblin-1", SECOND):
            assert _precise(engine, instance, target_id) == (target_id == marked)
        return True

    assert agrees()  # 没有实例：两边都不是
    _cast_mark(engine, instance)
    assert agrees()
    _attack(engine, instance, rolls=[15, 10, 8, 4])
    assert agrees()  # 归零之后仍然是同一个目标
    _next_turn(engine, instance)
    _apply(engine, instance, _retarget_intent())
    assert agrees()

    restored = GameInstance.from_dict(json.loads(json.dumps(instance.to_dict())))
    assert _precise(engine, restored, SECOND) is True
    assert _precise(engine, restored, "enemy:goblin-1") is False


# --------------------------------------------------------------------------
# 17-18. Feral Senses: Blindsight is a range, and it is not Truesight
# --------------------------------------------------------------------------

def test_matrix_17_thirty_feet_is_the_whole_difference() -> None:
    """Matrix 17: Blur is cancelled at 30 and stays at 31."""

    def blurred_attack(level: int, position: int) -> dict:
        engine, instance = _combat({"gm": ranger(level)}, {"gm": 20},
                                   enemies=[goblin(position=position, hp=200)])
        _add_enemy_condition(instance, "blurred")
        events = _attack(engine, instance, rolls=[13, 5])
        return _check(events, "attack")

    inside = blurred_attack(18, 30)
    assert inside["advantage_mode"] == ""
    assert inside["advantage_sources"] == []
    outside = blurred_attack(18, 31)
    assert outside["advantage_mode"] == "disadvantage"
    assert outside["advantage_sources"] == [{"code": "blurred", "mode": "disadvantage"}]
    # 没有 Feral Senses 的职业在同一条 30 尺线内也抵消不了。
    low = blurred_attack(17, 30)
    assert low["advantage_mode"] == "disadvantage"

    # 同一个距离还决定"能不能看见它"，也就是能不能把它标成猎物。
    engine, instance = _combat({"gm": ranger(18)}, {"gm": 20},
                               enemies=[goblin(position=20, hp=200)])
    _add_enemy_condition(instance, "invisible")
    events = _cast_mark(engine, instance)
    assert _mark(instance)["mark_target_id"] == "enemy:goblin-1"
    assert _concentration(events)[0]["mark_target_id"] == "enemy:goblin-1"

    far, far_instance = _combat({"gm": ranger(18)}, {"gm": 20},
                                enemies=[goblin(position=31, hp=200)])
    _add_enemy_condition(far_instance, "invisible")
    assert "you cannot see the creature you are marking" in _rejected(
        far, far_instance, _mark_intent(),
    )


def test_matrix_18_blindsight_is_not_truesight() -> None:
    """Matrix 18: one code, no extrapolation, and it composes."""

    engine, instance = _combat({"gm": ranger(18)}, {"gm": 20},
                               enemies=[goblin(position=30, hp=200)])
    actor = _actor(engine, instance)
    # 声明本身与距离无关：它就是"我有 30 尺盲视"。
    assert observer_senses(engine, actor, distance=30) == [
        {"code": "blindsight", "range_ft": 30, "source": "feral_senses"},
    ]
    assert observer_senses(engine, actor, distance=31) == []
    # 它只回答 blindsight 这一件事：既不是 truesight，也不是无限范围。
    assert vision_codes(engine, actor, distance=1) == frozenset({"blindsight"})
    assert "truesight" not in vision_codes(engine, actor, distance=1)
    assert vision_codes(engine, actor, distance=None) == frozenset()

    # 与其他来源合成：Blur 被抵消，Faerie Fire 的优势留下。
    composed = _combat({"gm": ranger(18)}, {"gm": 20},
                       enemies=[goblin(position=30, hp=200)])
    _add_enemy_condition(composed[1], "blurred")
    _add_enemy_condition(composed[1], "faerie_fire")
    assert _check(_attack(composed[0], composed[1], rolls=[13, 5]), "attack")[
        "advantage_mode"
    ] == "advantage"

    # 31 尺时两个来源互相抵消：Blindsight 不再参与，等级也就回到了 d20。
    beyond = _combat({"gm": ranger(18)}, {"gm": 20},
                     enemies=[goblin(position=31, hp=200)])
    _add_enemy_condition(beyond[1], "blurred")
    _add_enemy_condition(beyond[1], "faerie_fire")
    assert _check(_attack(beyond[0], beyond[1], rolls=[13, 5]), "attack")[
        "advantage_mode"
    ] == ""


# --------------------------------------------------------------------------
# 19-20. Foe Slayer: one die, one owner, one instance
# --------------------------------------------------------------------------

def test_matrix_19_the_die_upgrades_only_at_twentieth_level() -> None:
    """Matrix 19: 1d6 at 19, 1d10 at 20, and the roll proves which."""

    def marked_die(level: int, rolls: list[int]) -> tuple[str, int]:
        engine, instance = _combat({"gm": ranger(level)}, {"gm": 20},
                                   enemies=[goblin(position=20, hp=200)])
        _cast_mark(engine, instance)
        # 施法花掉了一个 Bonus Action；新回合才又给出施法行与它的当前标记。
        _next_turn(engine, instance)
        row = next(
            item for item in engine.available_intents(instance, "gm")
            if item["type"] == "cast_spell"
        )
        die = row["hunters_mark"]["damage_die"]
        events = _attack(engine, instance, rolls=rolls)
        return die, _damage(events, "force")[0]["amount"]

    # 17 级起对当前标记有优势，所以两次 d20；差别只在最后一骰的骰面。
    assert marked_die(19, [13, 11, 5, 6]) == ("1d6", 6)
    # SequenceRng 会拒绝超出骰面的值，所以 9 只可能是 d10 掷出来的。
    assert marked_die(20, [13, 11, 5, 9]) == ("1d10", 9)


def test_matrix_20_the_upgrade_does_not_leak_out_of_the_instance() -> None:
    """Matrix 20: not the weapon, not the catalog, not an ally's mark."""

    engine, instance = _combat(
        {"gm": ranger(20), "ally": ranger(19)}, {"gm": 20, "ally": 15},
        enemies=[goblin(position=20, hp=300)],
    )
    _cast_mark(engine, instance)
    events = _attack(engine, instance, rolls=[13, 11, 7, 9])
    # 武器骰仍是 1d8，只有标记那一份变成 1d10。
    assert _damage(events, "piercing")[0]["rolls"] == [7]
    assert _damage(events, "force")[0]["rolls"] == [9]

    # 全局法术目录里印的还是 d6：升级是读实例的那一侧，不是改内容。
    catalog = engine.catalog.spell_effects["hunter_s_mark"]
    assert catalog["mark_damage"] == "1d6"

    # 19 级的盟友用自己的实例，自己那一骰还是 d6。
    _next_turn(engine, instance, "player:ally")
    _cast_mark(engine, instance, uid="ally", target_id="enemy:goblin-1")
    ally = _attack(engine, instance, uid="ally", rolls=[13, 11, 5, 6])
    assert _damage(ally, "force")[0]["amount"] == 6
    _next_turn(engine, instance, "player:ally")
    row = next(
        item for item in engine.available_intents(instance, "ally")
        if item["type"] == "cast_spell"
    )
    assert row["hunters_mark"]["damage_die"] == "1d6"

    # 转移之后，新目标吃到的还是这个施法者的 d10。
    mover, moved_instance = _combat(
        {"gm": ranger(20)}, {"gm": 20},
        enemies=[goblin(position=20, hp=1),
                 {**goblin(position=25, hp=300), "id": "goblin-2", "name": "G2"}],
    )
    _cast_mark(mover, moved_instance)
    _attack(mover, moved_instance, rolls=[15, 10, 8, 9])
    _next_turn(mover, moved_instance)
    _apply(mover, moved_instance, _retarget_intent())
    moved = _attack(mover, moved_instance, target_id=SECOND, rolls=[13, 11, 5, 8])
    assert _damage(moved, "force")[0]["rolls"] == [8]
    assert _damage(moved, "piercing")[0]["rolls"] == [5]


# --------------------------------------------------------------------------
# 21-23. what the client cannot say, and what survives a save
# --------------------------------------------------------------------------

def test_matrix_21_the_client_cannot_choose_any_of_it() -> None:
    """Matrix 21: forged target, die, range or exemption change nothing."""

    engine, instance = _combat(
        {"gm": ranger(20)}, {"gm": 20},
        enemies=[goblin(position=20, hp=200),
                 {**goblin(position=20, hp=200), "id": "goblin-2", "name": "G2"}],
    )
    _cast_mark(engine, instance)
    forged = _apply(engine, instance, _attack_intent(
        instance,
        mark_target_id=SECOND,
        mark_damage_die=20,
        blindsight_range_ft=300,
        concentration_damage_exempt_spell_id="spell:invisibility",
        advantage_mode="advantage",
        damage="99",
    ), [13, 11, 5, 4])
    check = _check(forged, "attack")
    # 客户端既买不到优势，也改不了骰面或目标。
    assert check["advantage_mode"] == "advantage"  # 来自 Precise，不是伪造字段
    assert check["advantage_sources"] == [{"code": "precise_hunter", "mode": "advantage"}]
    force = _damage(forged, "force")
    assert len(force) == 1 and force[0]["rolls"] == [4]
    assert _damage(forged, "piercing")[0]["amount"] == 7
    assert _mark(instance)["mark_target_id"] == "enemy:goblin-1"

    # 免费次数字段也不能凭空造出来：5 次用尽后说 slot_level=0 就是拒绝。
    engine, instance = _combat({"gm": ranger(1)}, {"gm": 20})
    for _ in range(2):
        _cast_mark(engine, instance)
        _next_turn(engine, instance)
    assert "the free spell cast is not available" in _rejected(
        engine, instance, _mark_intent() | {"free_cast": True, "slot_level": 0},
    )
    assert _resource(instance)["current"] == 0


def test_matrix_22_nothing_happens_twice() -> None:
    """Matrix 22: stale version, a replay, and a second cast in one turn."""

    engine, instance = _combat({"gm": ranger(5)}, {"gm": 20})
    version = int(ruleset_runtime.state(instance)["version"])
    intent = _mark_intent() | {"expected_version": version}
    assert "version" in _rejected(
        engine, instance, intent | {"expected_version": version - 1},
    ).lower()

    first = _apply(engine, instance, intent)
    assert _resource(instance)["current"] == 2
    replayed = engine.resolve_intent(instance, intent, SequenceRng([]))
    assert replayed["ok"] is True and replayed.get("replayed") is True, replayed
    applied = engine.apply_batch(instance, replayed["event_batch"])
    assert applied["duplicate"] is True
    # 重放既不第二次扣次数，也不产生第二个标记实例。
    assert _resource(instance)["current"] == 2
    assert _mark(instance)["mark_target_id"] == "enemy:goblin-1"
    assert len(first) > 0

    # 同一个回合里再施一次：Bonus Action 已经花掉，所以没有双标记。
    assert "the bonus action has already been spent" in _rejected(
        engine, instance, _mark_intent() | {"intent_id": "second-cast"},
    )
    assert _mark(instance)["mark_target_id"] == "enemy:goblin-1"

    # 转移同理：一个回合只有一次。
    engine, instance = _combat(
        {"gm": ranger(17)}, {"gm": 20},
        enemies=[goblin(position=20, hp=1),
                 {**goblin(position=25, hp=200), "id": "goblin-2", "name": "G2"}],
    )
    _cast_mark(engine, instance)
    _attack(engine, instance, rolls=[15, 10, 8, 4])
    _next_turn(engine, instance)
    _apply(engine, instance, _retarget_intent())
    assert "the bonus action has already been spent" in _rejected(
        engine, instance, _retarget_intent(target_id="enemy:goblin-1"),
    )


def test_matrix_23_a_reloaded_fight_is_the_same_fight() -> None:
    """Matrix 23: HP, slots, uses, mark and RNG survive a save and a reload."""

    engine, instance = _combat(
        {"gm": ranger(9)}, {"gm": 20},
        enemies=[goblin(position=20, hp=1),
                 {**goblin(position=25, hp=300), "id": "goblin-2", "name": "G2"}],
    )
    cast = _mark_intent() | {
        "expected_version": int(ruleset_runtime.state(instance)["version"]),
    }
    _apply(engine, instance, cast, [])
    _attack(engine, instance, rolls=[15, 8, 4])
    _next_turn(engine, instance)
    _apply(engine, instance, _retarget_intent())

    before = {
        "mark": _mark(instance), "resource": _resource(instance),
        "slots": _slots(instance), "hp": _actor(engine, instance)["hp"],
        "slots_state": _combat_state(instance)["enemies"]["goblin-2"]["hp"],
    }
    restored = GameInstance.from_dict(json.loads(json.dumps(instance.to_dict())))
    assert _mark(restored) == before["mark"]
    assert _resource(restored) == before["resource"]
    assert _slots(restored) == before["slots"]
    assert _actor(engine, restored)["hp"] == before["hp"]
    assert _combat_state(restored)["enemies"]["goblin-2"]["hp"] == before["slots_state"]

    # 重放记录过的意图是重复，不是第二刀。
    replay = engine.resolve_intent(restored, cast, SequenceRng([]))
    assert replay["ok"] is True and replay.get("replayed") is True, replay
    assert engine.apply_batch(restored, replay["event_batch"])["duplicate"] is True
    assert _mark(restored)["mark_target_id"] == SECOND
    assert _resource(restored) == before["resource"]

    # 战斗结束后长休：次数回满、专注清空，存档里的标记不再挡任何事。
    rest = Dnd2024RestEngine(_RUNTIME.load_bundle(LOCALE))
    rested = rest.complete_long_rest(deepcopy(_canonical(restored)))["character"]
    assert rested["resources"]["class"]["favored_enemy"]["current"] == 4
    assert rested["spellcasting"]["class"]["concentration"] is None


# --------------------------------------------------------------------------
# 24. the chains this slice touched
# --------------------------------------------------------------------------

def test_matrix_24_the_neighbouring_chains_still_work() -> None:
    """Matrix 24: the smoke test; the full regression is the focused suites."""

    engine, instance = _combat({"gm": ranger(5)}, {"gm": 20},
                               enemies=[goblin(position=20, hp=200)])

    # 内容只被加宽，没有被收窄：Invisible 仍然认原来的两个抵消代码，
    # 而且今天只有这一个法术声明了自己带唯一标记。
    modes = engine.catalog.conditions["invisible"]["attack_modes"]
    assert [entry["negated_by"] for entry in modes] == [
        ["see_invisibility", "truesight", "blindsight"],
        ["see_invisibility", "truesight", "blindsight"],
    ]
    assert [
        name for name, effect in engine.catalog.spell_effects.items()
        if effect.get("marks_target")
    ] == ["hunter_s_mark"]

    # 一个没有当前标记的游侠：治疗与攻击两条既有链路都照旧。
    healed = _apply(engine, instance, {
        "intent_id": "cure", "type": "cast_spell", "submitted_by": "gm",
        "actor_id": "player:gm", "spell_ref": "spell:cure_wounds",
        "slot_level": 1, "target_ids": ["player:gm"],
    }, [6])
    assert _damage(healed, "")[0]["healing"] is True
    assert _mark(instance) == {}

    _next_turn(engine, instance)
    plain = _attack(engine, instance, rolls=[13, 5])
    assert _check(plain, "attack")["advantage_mode"] == ""
    assert _damage(plain, "piercing")[0]["rolls"] == [5]
    assert _damage(plain, "force") == []


# --------------------------------------------------------------------------
# Review round (2026-11-18): the two fail-closed holes the audit found
# --------------------------------------------------------------------------

def test_a_negative_resource_amount_is_refused() -> None:
    """The reducer is the trust boundary: a negative amount must not add uses.

    ``current < amount`` is false for every negative number, and
    ``current - (-5)`` adds five: a forged ``class_resource.spent`` used to
    refill the Favored Enemy pool (S0-F18 §4.1.3).  The batch is refused whole
    and the ledger does not move.
    """

    from src.rulesets.events import stable_batch_id

    engine, instance = _combat({"gm": ranger(1)}, {"gm": 20})
    _cast_mark(engine, instance)
    before = _resource(instance)["current"]
    assert before == 1

    version = int(ruleset_runtime.state(instance).get("version", 0) or 0)
    intent = {"intent_id": f"forged-{version}", "type": "combat.message",
              "expected_version": version}
    batch = {
        "batch_id": stable_batch_id(intent, version),
        "intent_id": intent["intent_id"], "intent_type": "combat.message",
        "expected_version": version, "result_version": version + 1,
        "source_ref": "srd-5.2.1:p58:favored-enemy",
        "events": [{
            "type": "dnd2024.class_resource.spent", "actor_id": "player:gm",
            "resource_id": "favored_enemy", "amount": -5,
        }],
    }
    from src.rulesets.events import EventBatchError

    with pytest.raises(EventBatchError):
        engine.apply_batch(instance, batch)
    assert _resource(instance)["current"] == before


def test_a_find_check_without_a_mark_gets_no_advantage() -> None:
    """Empty target id and no mark are equal as strings -- not as a rule.

    ``mark_find_sources`` compared ``marked_target_id(actor) != target_id``
    without the ``not wanted`` guard its sibling ``mark_attack_sources`` has,
    so a Ranger who never cast the spell got a ``hunter_s_mark`` Advantage
    source on any Perception/Survival check that carried no target
    (S0-F18 §4.1.6).
    """

    from src.rulesets.dnd2024.combat.check_modes import mark_find_sources

    engine, instance = _combat({"gm": ranger(1)}, {"gm": 20})
    actor = engine._actor_view(instance, _combat_state(instance), "player:gm")

    assert mark_find_sources(engine, actor, "perception", "") == []
    # 有标记但问的是别的目标，同样不给。
    _cast_mark(engine, instance)
    actor = engine._actor_view(instance, _combat_state(instance), "player:gm")
    assert mark_find_sources(engine, actor, "perception", SECOND) == []
    assert mark_find_sources(
        engine, actor, "perception", "enemy:goblin-1",
    ) == [{"code": "hunter_s_mark", "mode": "advantage"}]
