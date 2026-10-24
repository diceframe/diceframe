"""Narrator-proposed affection is settled by the engine (GAL track P3)."""
from pathlib import Path

import pytest

from src.commands.affection_effects import affection_prompt_appendix, settle_affection
from src.commands.state_update_applier import StateUpdateApplier
from src.commands.tag_parser import parse_tag_state
from src.engine.game_instance import GameInstance
from src.engine.modules import affection
from src.rules.affection_config import AffectionConfig, parse_affection_config
from src.rules.loader import RuleBundleLoader
from src.rules.rule_system import RuleSystem

RULES_DIR = Path(__file__).resolve().parents[1] / "templates" / "rules"
CONFIG = AffectionConfig(max_delta_per_round=5)


def game(*npcs):
    inst = GameInstance(("test", "affection-settle"))
    for name in npcs:
        inst.npcs[name] = {"name": name}
    return inst


def builtin_rule(rule_id):
    return RuleSystem(RuleBundleLoader().load_rule(RULES_DIR, rule_id, "zh-CN"))


def test_rule_config_is_optional_and_strict():
    assert parse_affection_config(None) is None
    assert parse_affection_config({"max_delta_per_round": 5}) == CONFIG
    for raw in ([], {"max_delta_per_round": 0}, {"max_delta_per_round": 21},
                {"max_delta_per_round": 2.5}, {"max_delta_per_round": True},
                {"max_delta_per_round": 5, "decay": 1}, {}):
        with pytest.raises(ValueError):
            parse_affection_config(raw)
    with pytest.raises(ValueError):
        RuleSystem({"rule_id": "broken", "affection": {"max_delta_per_round": "5"}})


def test_only_rules_that_declare_affection_enable_it():
    assert builtin_rule("gal_narrative").affection == CONFIG
    assert builtin_rule("tavern_free").affection is None


def test_tag_is_parsed_as_a_proposal():
    data = parse_tag_state("她笑了。\n---\nNPC:艾莉丝:friendly\nAFFECTION:艾莉丝:+3:帮她捡起了书", "narrative")
    assert data["state_update"]["affection"] == [{"subject": "艾莉丝", "delta": "+3", "reason": "帮她捡起了书"}]
    data = parse_tag_state("……\n---\nAFFECTION:艾莉丝:-2", "narrative")
    assert data["state_update"]["affection"] == [{"subject": "艾莉丝", "delta": "-2", "reason": ""}]


def test_disabled_rule_settles_nothing():
    inst = game("艾莉丝")
    assert settle_affection(inst, [{"subject": "艾莉丝", "delta": "3"}], None) == []
    assert affection.scores(inst) == {}


def test_valid_proposals_apply_and_report_the_settled_change():
    inst = game("艾莉丝", "Bob")
    applied = settle_affection(inst, [
        {"subject": "艾莉丝", "delta": "+3"}, {"subject": "Bob", "delta": -2},
    ], CONFIG)
    assert applied == [
        {"subject": "艾莉丝", "delta": 3, "score": 3},
        {"subject": "Bob", "delta": -2, "score": -2},
    ]


@pytest.mark.parametrize("proposal", [
    {"subject": "陌生人", "delta": "3"},
    {"subject": "艾莉丝", "delta": "lots"},
    {"subject": "艾莉丝", "delta": "1.5"},
    {"subject": "艾莉丝", "delta": "３"},
    {"subject": "艾莉丝", "delta": ""},
    {"subject": "", "delta": "3"},
    "AFFECTION:艾莉丝:3",
])
def test_unknown_subjects_and_non_integer_changes_are_dropped(proposal):
    inst = game("艾莉丝")
    assert settle_affection(inst, [proposal], CONFIG) == []
    assert affection.scores(inst) == {}


def test_each_subject_total_is_capped_per_round_and_scores_stay_in_range():
    inst = game("艾莉丝")
    settle_affection(inst, [{"subject": "艾莉丝", "delta": "4"}, {"subject": "艾莉丝", "delta": "4"}], CONFIG)
    assert affection.score(inst, "艾莉丝") == 5
    settle_affection(inst, [{"subject": "艾莉丝", "delta": "+50"}, {"subject": "艾莉丝", "delta": "-48"}], CONFIG)
    assert affection.score(inst, "艾莉丝") == 7
    inst.modules["affection"]["scores"]["艾莉丝"] = 98
    assert settle_affection(inst, [{"subject": "艾莉丝", "delta": "5"}], CONFIG) == [
        {"subject": "艾莉丝", "delta": 2, "score": 100},
    ]


def test_subjects_per_round_are_bounded():
    names = [f"npc{index}" for index in range(10)]
    inst = game(*names)
    applied = settle_affection(inst, [{"subject": name, "delta": "1"} for name in names], CONFIG)
    assert [item["subject"] for item in applied] == names[:8]


def test_unreadable_slot_is_not_overwritten():
    inst = game("艾莉丝")
    inst.modules["affection"] = {"schema_version": 99, "opaque": True}
    assert settle_affection(inst, [{"subject": "艾莉丝", "delta": "3"}], CONFIG) == []
    assert inst.modules["affection"] == {"schema_version": 99, "opaque": True}


def test_applier_settles_against_the_runs_rule_after_registering_npcs():
    applier = StateUpdateApplier(RULES_DIR, None, lambda *a, **k: {})
    inst = game()
    inst.rule_id = "gal_narrative"
    update = {"npcs": {"艾莉丝": {"name": "艾莉丝", "tier": "friendly"}},
              "affection": [{"subject": "艾莉丝", "delta": "+9", "reason": "约会"}]}
    applier.apply_state_update(inst, update)
    assert affection.scores(inst) == {"艾莉丝": 5}

    inst = game("艾莉丝")
    inst.rule_id = "tavern_free"
    applier.apply_state_update(inst, {"affection": [{"subject": "艾莉丝", "delta": "3"}]})
    assert affection.scores(inst) == {}


def test_prompt_documents_the_tag_and_current_scores_only_when_enabled():
    inst = game("艾莉丝")
    assert affection_prompt_appendix(inst, None, "zh-CN") == ""
    text = affection_prompt_appendix(inst, CONFIG, "zh-CN")
    assert "AFFECTION:NPC名:变化量:原因" in text and "±5" in text and "暂无" in text
    affection.adjust(inst, "艾莉丝", 12)
    assert "艾莉丝: 12" in affection_prompt_appendix(inst, CONFIG, "en")
