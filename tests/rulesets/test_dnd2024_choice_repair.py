"""Repair fills what a character owes; it is not a second upgrade path."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.progression.advancement import (
    Dnd2024AdvancementEngine,
    ProgressionCatalogError,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.services.ruleset_advancement import (
    CardAdvancementDependencies,
    complete_card_choices,
    pending_card_choices,
)

CHAMPION = "subclass:champion"
DRACONIC = "subclass:draconic_sorcery"
LOCALE = "en"


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _engine(runtime: Dnd2024Runtime) -> Dnd2024AdvancementEngine:
    return Dnd2024AdvancementEngine(runtime.load_bundle(LOCALE))


def _resolver(runtime: Dnd2024Runtime) -> Dnd2024ClassFeatureResolver:
    return Dnd2024ClassFeatureResolver(runtime.load_bundle(LOCALE))


def _fighter(runtime: Dnd2024Runtime) -> dict:
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    draft = {**preset["draft"], "locale": LOCALE, "name": "Repair Tester"}
    return runtime.finalize_character(None, draft)["ruleset_character"]


def _advance_to(
    engine: Dnd2024AdvancementEngine, character: dict, target: int,
    *, subclass: str = CHAMPION,
) -> dict:
    while int(character["build"]["level"]) < target:
        requirements = {
            row["id"] for row in engine.preview_next_level(character)["requirements"]
        }
        choices: dict = {}
        if "subclass_ref" in requirements:
            choices["subclass_ref"] = subclass
        if "ability_score_increases" in requirements:
            choices["ability_score_increases"] = {"con": 2}
        character = engine.apply_next_level(character, choices)
    return character


def _without_subclass(character: dict) -> dict:
    damaged = deepcopy(character)
    damaged["features"].pop("subclass_ref", None)
    return damaged


def _missing_ids(engine: Dnd2024AdvancementEngine, character: dict) -> set[str]:
    return {row["id"] for row in engine.missing_choices(character)}


def _history(target: int, increases_at: dict[int, dict] | None = None) -> list[dict]:
    """A contiguous history for levels 2..target.

    The fixtures need a card that reached a level without going through this
    engine, and the only thing the audit trusts is what each level answered --
    so a forged card forges answers, not a parallel ledger.
    """

    increases_at = increases_at or {}
    return [
        {
            "from_level": level - 1,
            "to_level": level,
            "choices": (
                {"ability_score_increases": dict(increases_at[level])}
                if level in increases_at else {}
            ),
            "diff": {},
            "source_ref": "test:fixture",
        }
        for level in range(2, target + 1)
    ]


def _drop_increase(character: dict, level: int) -> dict:
    """Undo one level's ability answer, as if the player never made it.

    属性与 history 的 choices 一起退，才是一次真正的"没选"：只退其中一处，
    补齐时读到的是自相矛盾的卡。
    """

    damaged = deepcopy(character)
    for entry in damaged["progression"]["history"]:
        if int(entry["to_level"]) == level:
            entry["choices"].pop("ability_score_increases", None)
            entry["choices"].pop("feat_ref", None)
            entry["choices"].pop("epic_boon_ref", None)
            entry["diff"].pop("advancement_feat_ref", None)
    damaged["abilities"]["con"] -= 2
    return damaged


def _forge_to(
    character: dict, target: int, increases_at: dict[int, dict],
    feature_choices_at: dict[int, list[dict]] | None = None,
) -> dict:
    """Push a real card up to ``target`` without playing every level.

    补齐的测试需要"早就到了这个等级、但某一级没答"的卡。history 必须连续，
    因为重放逐级走它；伪造一张有洞的 history 只会测出一个永远过不了重放的
    卡，而不是补齐的缺点。

    ``feature_choices_at`` 是某一级开始必答的特性选项（冠军 7 级的额外战斗
    风格）。不回答，伪造的卡本身就不合法，测出来的只是夹具的洞。今天这张卡
    的目录里还没有那个槽，答案因此是惰性的；等槽随 S0-B 进了目录，这份夹具
    不用再改一次。
    """

    forged = deepcopy(character)
    forged["build"]["level"] = target
    forged["build"]["class_levels"][0]["level"] = target
    forged["progression"]["level"] = target

    history = [
        entry for entry in forged["progression"]["history"]
        if int(entry["to_level"]) <= target
    ]
    seen = {int(entry["to_level"]) for entry in history}
    for level in range(2, target + 1):
        if level in seen:
            continue
        choices: dict = {}
        if level in increases_at:
            choices["ability_score_increases"] = dict(increases_at[level])
        if feature_choices_at and level in feature_choices_at:
            choices["feature_choices"] = deepcopy(feature_choices_at[level])
        history.append({
            "from_level": level - 1,
            "to_level": level,
            "choices": choices,
            "diff": {},
            "source_ref": "test:fixture",
        })
    history.sort(key=lambda entry: int(entry["to_level"]))
    forged["progression"]["history"] = history
    return forged


def _sorcerer_draft(runtime: Dnd2024Runtime) -> dict:
    choices = runtime.builder_choices(None, {
        "locale": LOCALE,
        "class_ref": "class:sorcerer",
        "background_ref": "background:acolyte",
        "species_ref": "species:human",
    })
    class_entity = runtime.load_bundle(LOCALE).get("class", "sorcerer")
    draft = {
        "locale": LOCALE,
        "name": "Draconic Hero",
        "level": 1,
        "alignment": "neutral_good",
        "ability_method": "standard_array",
        "base_abilities": {
            "str": 8, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 15,
        },
        "background_ability_bonuses": {"int": 2, "cha": 1},
        "class_ref": "class:sorcerer",
        "species_ref": "species:human",
        "background_ref": "background:acolyte",
        "class_skill_refs": ["skill:arcana", "skill:deception"],
        "equipment_package_ref": choices["equipment_packages"][0]["ref"],
        "background_equipment_package_ref": choices[
            "background_equipment_packages"
        ][0]["ref"],
        "language_refs": ["language:common", "language:dwarvish", "language:elvish"],
        "class_spell_choices": class_entity["recommended_spell_choices"],
        "feat_choice_answers": {
            "feat:magic_initiate_cleric": {
                "spellcasting_ability": ["wis"],
                "cantrips": ["guidance", "light"],
                "level_1_spell": ["bless"],
            },
        },
    }
    choices = runtime.builder_choices(None, draft)
    if choices["species_sizes"]:
        draft["species_size"] = (
            "medium" if "medium" in choices["species_sizes"]
            else choices["species_sizes"][0]
        )
    if choices["species_skill_count"]:
        draft["species_skill_refs"] = ["skill:perception"]
    if choices["species_feat_count"]:
        draft["species_feat_refs"] = ["feat:alert"]
    if choices["class_tool_count"]:
        draft["class_tool_refs"] = [
            item["ref"]
            for item in choices["class_tools"][: choices["class_tool_count"]]
        ]
    return draft


def test_repairing_the_subclass_clears_the_requirement() -> None:
    """矩阵 27：补完之后，这张卡不再被报告为缺项。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    assert "subclass_ref" in _missing_ids(engine, five)

    repaired = engine.repair_choices(five, {"subclass_ref": CHAMPION})

    assert "subclass_ref" not in _missing_ids(engine, repaired)
    assert repaired["features"]["subclass_ref"] == CHAMPION


def test_repairing_the_subclass_restores_its_features() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    assert _resolver(runtime).has_feature(
        five, "improved_critical", owner=CHAMPION,
    ) is False

    repaired = engine.repair_choices(five, {"subclass_ref": CHAMPION})

    assert _resolver(runtime).has_feature(
        repaired, "improved_critical", owner=CHAMPION,
    ) is True


def test_repair_never_moves_the_level() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))

    repaired = engine.repair_choices(five, {"subclass_ref": CHAMPION})

    assert repaired["build"]["level"] == 5
    assert repaired["build"]["class_levels"][0]["level"] == 5
    assert repaired["progression"]["level"] == 5


def test_repair_refuses_a_subclass_from_another_class() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(five, {"subclass_ref": "subclass:lore"})

    assert "not an SRD subclass for this class" in str(caught.value)


def test_repair_refuses_a_subclass_that_is_not_missing() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(five, {"subclass_ref": CHAMPION})

    assert "is not missing from this character" in str(caught.value)


def test_repairing_an_ability_increase_pays_constitution_back_over_every_level() -> None:
    """矩阵 23：补齐 ASI 不是加一级的量，而是按已有等级回溯。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)

    damaged = _drop_increase(five, 4)
    assert "ability_score_increases" in _missing_ids(engine, damaged)

    hp_before = damaged["resources"]["max_hp"]
    repaired = engine.repair_choices(damaged, {
        "advancements": [
            {"level": 4, "ability_score_increases": {"con": 2}},
        ],
    })

    assert repaired["abilities"]["con"] == five["abilities"]["con"]
    # CON 从 +2 调整值回到 +3，五个已有等级各补 1 点。
    assert repaired["resources"]["max_hp"] - hp_before == 5


def test_repairing_a_subclass_pays_the_declared_maximum_not_one_level() -> None:
    """矩阵 22：子职的 HP 加成是当前等级的绝对值。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    one = runtime.finalize_character(None, _sorcerer_draft(runtime))[
        "ruleset_character"
    ]

    five = deepcopy(one)
    five["build"]["level"] = 5
    five["build"]["class_levels"][0]["level"] = 5
    five["progression"]["level"] = 5
    five["progression"]["history"] = _history(5, {4: {"cha": 2}})
    assert "subclass_ref" in _missing_ids(engine, five)

    hp_before = five["resources"]["max_hp"]
    repaired = engine.repair_choices(five, {"subclass_ref": DRACONIC})

    declared = _resolver(runtime).feature_max_hp_bonus(repaired)
    assert declared > 0
    assert repaired["resources"]["max_hp"] - hp_before == declared


def test_repair_refuses_an_advancement_the_character_does_not_owe() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(five, {
            "advancements": [
                {"level": 4, "ability_score_increases": {"con": 2}},
            ],
        })

    assert "owes no ability increase" in str(caught.value)


def test_repair_still_requires_a_choice_for_the_level_it_fills() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)
    damaged = _drop_increase(five, 4)
    damaged["abilities"]["con"] -= 2

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(damaged, {"advancements": [{"level": 4}]})

    assert "feat_ref is required" in str(caught.value)


def test_repair_refuses_the_same_level_twice_for_the_same_ability() -> None:
    """同一级的一片请求里只能补一次：第二条不能再加一遍同一个属性。

    缺项是按等级算的，条目是按条应用的。只按缺项判定的话，两条同级条目
    都能通过，属性就被加了两遍。外层 operation_id 幂等的是整个请求，挡不住
    请求内部的重复。
    """

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)
    damaged = _drop_increase(five, 4)
    con_before = damaged["abilities"]["con"]

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(damaged, {
            "advancements": [
                {"level": 4, "ability_score_increases": {"con": 2}},
                {"level": 4, "ability_score_increases": {"con": 2}},
            ],
        })

    assert "level is submitted more than once: 4" in str(caught.value)
    # 整单拒绝，不留前缀已应用的中间态。
    assert damaged["abilities"]["con"] == con_before
    assert "ability_score_increases" in _missing_ids(engine, damaged)


def test_repair_refuses_the_same_level_twice_for_different_abilities() -> None:
    """换一个属性也算重复：重复判定看等级，不看提交内容是否相同。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)
    damaged = _drop_increase(five, 4)
    dex_before = damaged["abilities"]["dex"]

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(damaged, {
            "advancements": [
                {"level": 4, "ability_score_increases": {"con": 2}},
                {"level": 4, "ability_score_increases": {"dex": 2}},
            ],
        })

    assert "level is submitted more than once: 4" in str(caught.value)
    assert damaged["abilities"]["dex"] == dex_before


def test_repair_refuses_a_repeated_epic_boon_entry() -> None:
    """Epic Boon 级同样按等级去重，专长不能被领两次。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    four = _advance_to(engine, _fighter(runtime), 4)
    forged = _forge_to(four, 19, {})
    assert "epic_boon_ref" in _missing_ids(engine, forged)

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(forged, {
            "advancements": [
                {"level": 19, "epic_boon_ref": "feat:boon_of_combat_prowess",
                 "ability_score_increases": {"str": 1}},
                {"level": 19, "epic_boon_ref": "feat:boon_of_combat_prowess",
                 "ability_score_increases": {"str": 1}},
            ],
        })

    assert "level is submitted more than once: 19" in str(caught.value)


def test_repair_writes_the_history_entry_it_filled() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))

    repaired = engine.repair_choices(five, {"subclass_ref": CHAMPION})

    history = repaired["progression"]["history"]
    level_three = next(entry for entry in history if entry["to_level"] == 3)
    assert level_three["choices"]["subclass_ref"] == CHAMPION
    assert [entry["to_level"] for entry in history] == sorted(
        entry["to_level"] for entry in history
    )


def _round_trip(runtime: Dnd2024Runtime, character: dict) -> dict:
    normalized = runtime.normalize_character_submission(
        None, {"ruleset_character": character}, LOCALE,
    )
    return normalized["ruleset_character"]


def test_a_repaired_ability_increase_replays_like_a_real_level_up() -> None:
    """补齐写进 history 的 ASI 必须能被重放读出来。

    只写账本时，审计这一侧干净了，``normalize_character_submission`` 那一侧
    仍然把这一级看作没答：补齐返回成功，卡却过不了创建/加入。
    """

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)
    damaged = _drop_increase(five, 4)

    repaired = engine.repair_choices(damaged, {
        "advancements": [{"level": 4, "ability_score_increases": {"con": 2}}],
    })

    entry = next(
        item for item in repaired["progression"]["history"] if item["to_level"] == 4
    )
    assert entry["choices"]["ability_score_increases"] == {"con": 2}

    inner = _round_trip(runtime, repaired)
    assert inner["build"]["level"] == 5
    assert inner["abilities"]["con"] == five["abilities"]["con"]


def test_a_repaired_feat_replays_like_a_real_level_up() -> None:
    """专长补齐同理：重放读 ``choices``，不读 ``diff``。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)
    damaged = _drop_increase(five, 4)

    repaired = engine.repair_choices(damaged, {
        "advancements": [{
            "level": 4,
            "feat_ref": "feat:grappler",
            "ability_score_increases": {"dex": 1},
        }],
    })

    entry = next(
        item for item in repaired["progression"]["history"] if item["to_level"] == 4
    )
    assert entry["choices"]["feat_ref"] == "feat:grappler"
    assert entry["choices"]["ability_score_increases"] == {"dex": 1}

    inner = _round_trip(runtime, repaired)
    assert inner["build"]["level"] == 5
    assert "feat:grappler" in inner["features"]["feat_refs"]


def test_a_repaired_epic_boon_replays_like_a_real_level_up() -> None:
    """Epic Boon 走的是 ``epic_boon_ref`` 而不是 ``feat_ref``，两边都得写对。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    four = _advance_to(engine, _fighter(runtime), 4)
    forged = _forge_to(four, 19, {
        6: {"dex": 2}, 8: {"wis": 2}, 12: {"int": 2},
        14: {"cha": 2}, 16: {"str": 1, "dex": 1},
    }, feature_choices_at={7: [{
        "owner": CHAMPION,
        "feature_id": "additional_fighting_style",
        "choice_id": "fighting_style_feat",
        "selected_refs": ["feat:archery"],
    }]})
    assert "epic_boon_ref" in _missing_ids(engine, forged)

    repaired = engine.repair_choices(forged, {
        "advancements": [{
            "level": 19,
            "epic_boon_ref": "feat:boon_of_combat_prowess",
            "ability_score_increases": {"str": 1},
        }],
    })

    entry = next(
        item for item in repaired["progression"]["history"] if item["to_level"] == 19
    )
    assert entry["choices"]["epic_boon_ref"] == "feat:boon_of_combat_prowess"
    assert "feat_ref" not in entry["choices"]

    inner = _round_trip(runtime, repaired)
    assert inner["build"]["level"] == 19
    assert "feat:boon_of_combat_prowess" in inner["features"]["feat_refs"]


def test_repair_refuses_to_invent_a_history_entry_out_of_order() -> None:
    """history 缺中间一层时整单拒绝，而不是补出一张过不了重放的卡。

    这一级没有条目、又不是接在末尾，说明这张卡的 history 本身有洞。补一个
    条目进去等于替角色凭空走一步；不补则重放照样拒绝。两种都不可接受，所以
    在动数据之前就拒绝，并说清是哪一级。
    """

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _advance_to(engine, _fighter(runtime), 5)
    damaged = _drop_increase(five, 4)
    damaged["progression"]["history"] = [
        entry for entry in damaged["progression"]["history"]
        if int(entry["to_level"]) != 4
    ]
    con_before = damaged["abilities"]["con"]

    with pytest.raises(ProgressionCatalogError) as caught:
        engine.repair_choices(damaged, {
            "advancements": [{"level": 4, "ability_score_increases": {"con": 2}}],
        })

    assert "no entry for level 4" in str(caught.value)
    assert damaged["abilities"]["con"] == con_before


# --------------------------------------------------------------------------
# the write contract the HTTP layer sits on
# --------------------------------------------------------------------------

def _card_dependencies(
    cards: list[dict], runtime: Dnd2024Runtime,
) -> CardAdvancementDependencies:
    return CardAdvancementDependencies(
        read_cards=lambda: cards,
        write_cards=lambda updated: cards.__setitem__(slice(None), updated),
        load_rule_by_id=lambda rule_id, language="": SimpleNamespace(
            rule_id=rule_id,
        ),
        runtime_for_card=lambda card: runtime,
    )


def _card(character: dict) -> dict:
    return {
        "id": "card-1",
        "rule_id": "test_dnd2024",
        "language": LOCALE,
        "ruleset_revision": 0,
        "ruleset_character": character,
        "equipment": [],
    }


def test_pending_lists_what_the_card_owes_without_writing() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    cards = [_card(five)]
    dependencies = _card_dependencies(cards, runtime)

    result = pending_card_choices(dependencies, "card-1")

    assert result["ok"] is True
    assert [row["id"] for row in result["missing"]] == ["subclass_ref"]
    assert cards[0]["ruleset_revision"] == 0


def test_completing_a_choice_writes_the_card_and_bumps_the_revision() -> None:
    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    cards = [_card(five)]
    dependencies = _card_dependencies(cards, runtime)

    result = complete_card_choices(dependencies, "card-1", {
        "repairs": {"subclass_ref": CHAMPION},
        "expected_revision": 0,
        "operation_id": "repair-1",
    })

    assert result["ok"] is True, result
    assert result["revision"] == 1
    assert result["duplicate"] is False
    assert cards[0]["ruleset_character"]["features"]["subclass_ref"] == CHAMPION
    assert pending_card_choices(dependencies, "card-1")["missing"] == []


def test_a_retried_repair_does_not_pay_hit_points_twice() -> None:
    """矩阵 25：幂等按 operation_id，不按内容。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    cards = [_card(five)]
    dependencies = _card_dependencies(cards, runtime)

    first = complete_card_choices(dependencies, "card-1", {
        "repairs": {"subclass_ref": CHAMPION},
        "expected_revision": 0,
        "operation_id": "repair-1",
    })
    hp_after_first = first["card"]["ruleset_character"]["resources"]["max_hp"]

    second = complete_card_choices(dependencies, "card-1", {
        "repairs": {"subclass_ref": CHAMPION},
        "expected_revision": 1,
        "operation_id": "repair-1",
    })

    assert second["duplicate"] is True
    assert second["revision"] == 1
    assert second["card"]["ruleset_character"]["resources"]["max_hp"] == hp_after_first


def test_a_stale_revision_is_refused() -> None:
    """矩阵 26：与升级提交共用同一把乐观锁。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))
    cards = [_card(five)]
    cards[0]["ruleset_revision"] = 3
    dependencies = _card_dependencies(cards, runtime)

    result = complete_card_choices(dependencies, "card-1", {
        "repairs": {"subclass_ref": CHAMPION},
        "expected_revision": 1,
        "operation_id": "repair-1",
    })

    assert result["ok"] is False
    assert result["code"] == "STALE_CHARACTER_REVISION"
    assert cards[0]["ruleset_revision"] == 3


def test_a_rogue_ability_level_at_ten_is_audited() -> None:
    """矩阵 15：Rogue 的 10 级 ASI 同样由目录决定。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "lucky_scout")
    draft = {**preset["draft"], "locale": LOCALE, "name": "Rogue Tester"}
    rogue = runtime.finalize_character(None, draft)["ruleset_character"]
    four = _advance_to(engine, rogue, 4, subclass="subclass:thief")

    forged = deepcopy(four)
    forged["build"]["level"] = 10
    forged["build"]["class_levels"][0]["level"] = 10
    forged["progression"]["level"] = 10
    forged["progression"]["history"] = _history(10, {4: {"con": 2}, 8: {"con": 2}})

    assert any(
        row["id"] == "ability_score_increases" and row["level"] == 10
        for row in engine.missing_choices(forged)
    )


def test_completing_a_subclass_recomputes_the_derived_facts() -> None:
    """矩阵 24：补齐改的是派生值，不只是特性列表。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    one = runtime.finalize_character(None, _sorcerer_draft(runtime))["ruleset_character"]
    five = deepcopy(one)
    five["build"]["level"] = 5
    five["build"]["class_levels"][0]["level"] = 5
    five["progression"]["level"] = 5
    five["progression"]["history"] = _history(5, {4: {"cha": 2}})
    before = deepcopy(five["derived"])

    repaired = engine.repair_choices(five, {"subclass_ref": DRACONIC})

    assert repaired["derived"] != before


def test_a_level_twenty_character_can_still_be_completed() -> None:
    """矩阵 28：补齐不经过升级事务，所以 20 级也有入口。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    four = _advance_to(engine, _fighter(runtime), 4)

    forged = deepcopy(four)
    forged["features"].pop("subclass_ref", None)
    forged["build"]["level"] = 20
    forged["build"]["class_levels"][0]["level"] = 20
    forged["progression"]["level"] = 20
    forged["progression"]["history"] = _history(20, {4: {"con": 2}})
    with pytest.raises(ProgressionCatalogError):
        engine.preview_next_level(forged)

    repaired = engine.repair_choices(forged, {"subclass_ref": CHAMPION})

    assert repaired["build"]["level"] == 20
    assert repaired["features"]["subclass_ref"] == CHAMPION


def test_a_ruleset_without_the_repair_path_is_refused_instead_of_crashing() -> None:
    """补选是较新的能力：只实现升级流程的插件运行时不应当抛 AttributeError。

    路由把非 200 映射成 422，所以这里必须是正常的错误响应，而不是 500。
    """

    cards = [_card({})]
    dependencies = CardAdvancementDependencies(
        read_cards=lambda: cards,
        write_cards=lambda updated: cards.__setitem__(slice(None), updated),
        load_rule_by_id=lambda rule_id, language="": SimpleNamespace(
            rule_id=rule_id,
        ),
        runtime_for_card=lambda card: SimpleNamespace(
            preview_advancement=lambda *args, **kwargs: {"ok": True},
            apply_advancement=lambda *args, **kwargs: {},
        ),
    )

    pending = pending_card_choices(dependencies, "card-1")
    completed = complete_card_choices(
        dependencies,
        "card-1",
        {
            "repairs": {"subclass_ref": CHAMPION},
            "operation_id": "op-1",
            "expected_revision": 0,
        },
    )

    assert pending["ok"] is False
    assert pending["code"] == "RULESET_ADVANCEMENT_UNAVAILABLE"
    assert "补齐" in pending["error"]
    assert completed["ok"] is False
    assert completed["code"] == "RULESET_ADVANCEMENT_UNAVAILABLE"
    assert cards[0]["ruleset_revision"] == 0


def test_repair_refuses_a_request_that_fills_nothing() -> None:
    """空请求不能算成功：卡还欠着，却会白白推进一次 revision。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))

    with pytest.raises(ProgressionCatalogError):
        engine.repair_choices(five, {})

    assert "subclass_ref" in _missing_ids(engine, five)


def test_repair_refuses_a_key_it_does_not_understand() -> None:
    """把 `subclass` 写成 `subclass_ref` 之外的名字时，不能静默什么都不做。"""

    runtime = Dnd2024Runtime()
    engine = _engine(runtime)
    five = _without_subclass(_advance_to(engine, _fighter(runtime), 5))

    with pytest.raises(ProgressionCatalogError) as excinfo:
        engine.repair_choices(five, {"subclass": CHAMPION})

    assert "unknown repair keys: subclass" in str(excinfo.value)
    assert "subclass_ref" in _missing_ids(engine, five)
