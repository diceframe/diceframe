"""D1/FD1: the feat declaration model, the parameter registry, and its gates.

Before this slice a feat was whichever dict the catalog happened to hold: no
model, no closed vocabulary, and a reader that had to know the feat's name.  The
tests here pin the replacement -- one shape, one registry, and a claim
(``automation_level``) the loader can actually check.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from src.rulesets.dnd2024.character.derivation import feat_initiative_bonus
from src.rulesets.dnd2024.features.feat_models import (
    LEGACY_FEAT_FIELDS,
    FeatDefinitionError,
    check_declaration_completeness,
    load_feat_definitions,
    parse_feat,
)
from src.rulesets.dnd2024.features.parameter_registry import (
    PARAMETERS,
    ParameterSpec,
    unread_parameters,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime

LOCALE = "en"
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

#: 每个内容包各自的专长实体。分组不是为了好看：``srd-5.2.1`` 的数字（17 条
#: 规则条目 / 19 个实体，Magic Initiate 三变体各占一个实体）来自三份独立的
#: SRD 索引，而不是"我们声明了多少"。``phb-2024`` 只有 Lucky —— 它有完整
#: 实现却不在 SRD 里，pack 字段就是为了让这件事可查，而不是靠人记得。
EXPECTED_FEAT_IDS_BY_PACK: dict[str, frozenset[str]] = {
    "srd-5.2.1": frozenset({
        "alert",
        "skilled",
        "savage_attacker",
        "magic_initiate_cleric",
        "magic_initiate_druid",
        "magic_initiate_wizard",
        "ability_score_improvement",
        "grappler",
        "archery",
        "defense",
        "great_weapon_fighting",
        "two_weapon_fighting",
        "boon_of_combat_prowess",
        "boon_of_dimensional_travel",
        "boon_of_fate",
        "boon_of_irresistible_offense",
        "boon_of_spell_recall",
        "boon_of_the_night_spirit",
        "boon_of_truesight",
    }),
    "phb-2024": frozenset({"lucky"}),
}


@pytest.fixture(scope="module")
def bundle():
    return Dnd2024Runtime().load_bundle(LOCALE)


def _feat(**overrides):
    base = {
        "schema_version": 1,
        "kind": "feat",
        "id": "probe",
        "category": "origin",
        "source_ref": "srd-5.2.1:p87:probe",
        "automation_level": "guided",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. 一个池：两种存放位置的 20 条走同一个解析器
# ---------------------------------------------------------------------------


def test_every_declared_feat_goes_through_the_model(bundle) -> None:
    definitions = load_feat_definitions(bundle)
    expected = frozenset().union(*EXPECTED_FEAT_IDS_BY_PACK.values())
    assert set(definitions) == expected


def test_each_feat_reports_the_pack_it_came_from(bundle) -> None:
    actual: dict[str, set[str]] = {}
    for definition in load_feat_definitions(bundle).values():
        actual.setdefault(definition.content_pack, set()).add(definition.id)
    assert {pack: frozenset(ids) for pack, ids in actual.items()} ==         EXPECTED_FEAT_IDS_BY_PACK


def test_the_srd_pack_is_exactly_the_srd(bundle) -> None:
    """The SRD pack's contents must match the published index, not our count.

    17 rule entries live in the SRD (4 Origin, 2 General, 4 Fighting Style,
    7 Epic Boon).  A pack that quietly grows is how "we implemented the SRD"
    stops meaning anything.
    """

    srd = [
        definition
        for definition in load_feat_definitions(bundle).values()
        if definition.content_pack == "srd-5.2.1"
    ]
    assert len(srd) == 19  # 17 条规则条目，Magic Initiate 的三个变体各一个实体
    by_category: dict[str, int] = {}
    for definition in srd:
        by_category[definition.category] = by_category.get(definition.category, 0) + 1
    assert by_category == {
        "origin": 6,          # Alert / Savage Attacker / Skilled + 3 × Magic Initiate
        "general": 2,
        "fighting_style": 4,
        "epic_boon": 7,
    }


def test_source_ref_agrees_with_the_pack(bundle) -> None:
    """A feat may not claim one book in its pack and another in its anchor.

    This is a test rather than a load-time rule: the repository has older
    anchors that would fail it, and a hard gate would block content that is
    otherwise correct.  Written here, it turns "the anchor is made up" from
    something nobody notices into a red test.
    """

    mismatched = [
        f"{d.id}: pack={d.content_pack} ref={d.source_ref}"
        for d in load_feat_definitions(bundle).values()
        if d.content_pack and not d.source_ref.startswith(f"{d.content_pack}:")
    ]
    assert not mismatched, mismatched


def test_an_unknown_top_level_field_fails_at_load() -> None:
    # 裸 dict 时代这个字段会被静默保留，然后某个消费者按名字去猜。
    with pytest.raises(FeatDefinitionError) as captured:
        parse_feat(_feat(initiative_proficiency=True))
    assert "unknown field" in str(captured.value)


def test_legacy_fields_are_carried_verbatim() -> None:
    definition = parse_feat(_feat(requires_spellcasting=True))
    assert definition.legacy == {"requires_spellcasting": True}


# ---------------------------------------------------------------------------
# 2. 参数注册表：键闭集、类型、按条目的值域
# ---------------------------------------------------------------------------


def test_an_unknown_parameter_names_the_feat_and_the_key() -> None:
    with pytest.raises(FeatDefinitionError) as captured:
        parse_feat(_feat(parameters={"ranged_damage_bonus": 2}))
    message = str(captured.value)
    # 旧报错只说"有未知键"，不说属于哪条专长，排查要靠人肉 grep。
    assert "probe" in message
    assert "ranged_damage_bonus" in message


@pytest.mark.parametrize("value", [11, -11, True, "2"])
def test_a_parameter_outside_its_declared_range_is_rejected(value) -> None:
    with pytest.raises(FeatDefinitionError):
        parse_feat(_feat(parameters={"ranged_attack_bonus": value}))


def test_a_negative_bonus_is_legal_now() -> None:
    # 旧白名单把每个数值钉在 1..10，惩罚值写不进来。注册表按条目给值域。
    definition = parse_feat(_feat(parameters={"ranged_attack_bonus": -2}))
    assert definition.parameters["ranged_attack_bonus"] == -2


def test_a_dice_parameter_is_validated_as_a_die(monkeypatch) -> None:
    # GWF 与 Savage Attacker 要用它；先证明形状有校验，再谈谁读。
    monkeypatch.setitem(PARAMETERS, "probe_die", ParameterSpec("dice", stage="unread"))
    assert parse_feat(
        _feat(parameters={"probe_die": "2d6"}),
    ).parameters["probe_die"] == "2d6"
    for bad in ("d6", "0d6", "2d1", "2d6x", ""):
        with pytest.raises(FeatDefinitionError):
            parse_feat(_feat(parameters={"probe_die": bad}))


# ---------------------------------------------------------------------------
# 3. 门禁：deterministic 的声明必须每条参数都有读者
# ---------------------------------------------------------------------------


def test_deterministic_feat_may_not_declare_an_unread_parameter(monkeypatch) -> None:
    monkeypatch.setitem(
        PARAMETERS, "probe_unread_key", ParameterSpec("int", stage="unread"),
    )
    with pytest.raises(FeatDefinitionError) as captured:
        parse_feat(
            _feat(
                automation_level="deterministic",
                parameters={"probe_unread_key": 1},
            ),
        )
    message = str(captured.value)
    assert "deterministic" in message
    assert "probe_unread_key" in message


def test_guided_feat_may_declare_an_unread_parameter(monkeypatch) -> None:
    # "guided" 的语义就是"还有一部分没接上"，所以它必须能带着未读声明。
    monkeypatch.setitem(
        PARAMETERS, "probe_unread_key", ParameterSpec("int", stage="unread"),
    )
    definition = parse_feat(
        _feat(automation_level="guided", parameters={"probe_unread_key": 1}),
    )
    assert definition.parameters["probe_unread_key"] == 1


def test_completeness_gate_is_idempotent_on_a_real_definition(bundle) -> None:
    for definition in load_feat_definitions(bundle).values():
        check_declaration_completeness(definition)


# ---------------------------------------------------------------------------
# 4. 被动效果：kind 闭集
# ---------------------------------------------------------------------------


def test_an_unknown_passive_effect_kind_fails_at_load() -> None:
    with pytest.raises(FeatDefinitionError):
        parse_feat(_feat(passive_effects=[{"kind": "speed_bonus", "value": 10}]))


def test_defense_keeps_its_armor_requirement(bundle) -> None:
    definition = load_feat_definitions(bundle)["defense"]
    assert [effect.kind for effect in definition.passive_effects] == ["armor_class_bonus"]
    assert definition.passive_effects[0].requires_armor is True


# ---------------------------------------------------------------------------
# 5. 三处专长名硬编码：两处已消，第三处（Magic Initiate 的 grants）归 P4
# ---------------------------------------------------------------------------


def test_alert_is_a_parameter_not_a_name() -> None:
    bundle = Dnd2024Runtime().load_bundle(LOCALE)
    assert feat_initiative_bonus(bundle, {"feat:alert"}, 2) == 2
    # 名字不再出现在任何比较里；它只是 ref 的一个值。扫整个 src/：建卡与
    # 升级重算是两条派生路径，只盯 derivation.py 会漏掉升级那一条。
    offenders = [
        str(path.relative_to(ROOT))
        for path in (SRC / "rulesets" / "dnd2024").rglob("*.py")
        if '"feat:alert"' in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


def test_skilled_declares_its_choice_once(bundle) -> None:
    definition = load_feat_definitions(bundle)["skilled"]
    choice = definition.choice
    assert choice is not None
    assert (choice.kind, choice.category, choice.count) == (
        "skill_proficiency", "skill_or_tool", 3,
    )
    # 授予的是新熟练，不是 Expertise；两个"已有熟练"约束都必须关掉。
    assert choice.grants == "proficiency"
    assert choice.requires_proficiency is False
    assert choice.exclude_expertise is False
    # SRD: "Repeatable. You can take this feat more than once." 声明成可重复；
    # 取得侧的合并（feat_refs 去重）还没读它，属 FD2a。
    assert definition.repeatable is True


def test_proficiency_choice_no_longer_exists() -> None:
    # 旧字段已被 choice 取代；留着它就是第二套声明，迟早与 choice 不一致。
    for path in (ROOT / "templates" / "rulesets" / "dnd2024_srd" / "content" / "feats").glob("*.json"):
        assert "proficiency_choice" not in path.read_text(encoding="utf-8"), path.name
    for path in (SRC / "rulesets" / "dnd2024").rglob("*.py"):
        assert "proficiency_choice" not in path.read_text(encoding="utf-8"), path.name


def test_magic_initiate_declares_its_list_as_a_parameter(bundle) -> None:
    definition = load_feat_definitions(bundle)["magic_initiate_wizard"]
    assert definition.parameters["spell_list"] == "wizard"
    # 戏法/1 环的选择仍是 choice_specs（P4 迁进 grants 并退役 feat_spells）。
    assert "choice_specs" in definition.legacy


# ---------------------------------------------------------------------------
# 6. legacy 袋子是封闭的：只许减
# ---------------------------------------------------------------------------


def test_legacy_fields_stay_a_bounded_set() -> None:
    assert LEGACY_FEAT_FIELDS == frozenset({
        "ability_choice",
        "any_ability_minimum",
        "requires_feature",
        "requires_spellcasting",
        "choice_specs",
    })


def test_unread_registry_is_empty_today() -> None:
    # 这条断言是"棘轮"的起点：加一条未读参数就必须改它，并解释为什么。
    assert unread_parameters() == ()


def test_labels_must_be_an_object() -> None:
    # 同文件其余字段一律 fail closed。labels 写坏成数组时静默当空，界面上
    # 只会少一个名字，没人会回头查声明。
    with pytest.raises(FeatDefinitionError) as captured:
        parse_feat(_feat(labels=["Probe Step"]))
    assert "labels" in str(captured.value)


def test_a_broken_feat_does_not_hide_a_class_feature_clash(bundle) -> None:
    """The uniqueness check spans two catalogs; one bad feat may not disable half.

    Skipping the whole check when the feat catalog cannot be parsed would let two
    class features share a capability id -- a clash that has nothing to do with
    feats and that the check used to catch before feats entered the id space.
    """

    from dataclasses import replace
    from types import SimpleNamespace

    from src.rulesets.dnd2024.features.models import (
        ClassFeatureError,
        Dnd2024ClassFeatureCatalog,
    )
    from src.rulesets.dnd2024.features.validation import (
        _validate_capability_ids_are_unique,
    )

    catalog = Dnd2024ClassFeatureCatalog.from_bundle(bundle)
    source = next(
        definition
        for definition in catalog.features_by_key.values()
        if definition.capabilities
    )
    twin = replace(source, id=f"{source.id}_twin")
    clashing = SimpleNamespace(features_by_key={
        ("probe", source.id): source,
        ("probe", twin.id): twin,
    })

    class _UnparseableFeats:
        """A bundle whose only feat cannot be parsed (no ``category``)."""

        def list(self, kind):
            if kind == "feat":
                return [{"schema_version": 1, "kind": "feat", "id": "broken"}]
            return []

        def get(self, kind, ident=None):
            return None

    with pytest.raises(ClassFeatureError) as captured:
        _validate_capability_ids_are_unique(clashing, _UnparseableFeats())
    assert source.capabilities[0].id in str(captured.value)
