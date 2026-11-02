from __future__ import annotations

from copy import deepcopy

import pytest

from src.migrations.instance import CURRENT_INSTANCE_SCHEMA_VERSION, migrate_game_state_payload


VALUES = {
    "schema_version": 1, "difficulty": "硬核", "solo_mode": True,
    "seed_code": "SEED42", "entry_point": "bot", "narrative_perspective": "third_person",
    "gm_style_override": {}, "dice_reveal_mode": "auto", "luck_timeout_seconds": 0,
    "economy_reward_policy": {"mode": "auto_small_cash", "auto_reward_cap": 5000},
}


def legacy_payload():
    return {"instance_schema_version": 44, "modules": {
        "table_settings": deepcopy(VALUES),
        "economy": {"schema_version": 1, "state": {"run_id": "run_old", "transactions": [{"id": "receipt", "amount": 5000}]}},
    }}


def test_new_games_reveal_dice_automatically_and_explicit_click_survives_reload():
    from src.engine.game_instance import GameInstance
    from src.engine import module_hooks
    instance = GameInstance(game_key=("web", "dice-default", "bot"))
    assert module_hooks.check_presentation(instance)["dice_reveal_mode"] == "auto"
    instance.configure_session(dice_reveal_mode="click")
    restored = GameInstance.from_dict(instance.to_dict())
    assert module_hooks.check_presentation(restored)["dice_reveal_mode"] == "click"


def test_split_moves_values_once_without_reinterpreting_policy_or_ledger():
    source = legacy_payload()
    before = deepcopy(source)
    result = migrate_game_state_payload(source)
    assert result["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert source == before
    slots = result["modules"]
    assert slots["game_config"] == {"schema_version": 1, **{key: VALUES[key] for key in ("difficulty", "solo_mode", "seed_code", "entry_point")}}
    assert slots["narration_preferences"] == {"schema_version": 1, "narrative_perspective": "third_person", "gm_style_override": {}}
    assert slots["check_presentation"] == {"schema_version": 1, "dice_reveal_mode": "auto", "luck_timeout_seconds": 0}
    assert slots["economy"]["state"] == before["modules"]["economy"]["state"]
    assert slots["economy"]["reward_policy"] == VALUES["economy_reward_policy"]
    assert "table_settings" not in slots
    assert migrate_game_state_payload(result) == result


def test_split_preserves_none_override_and_opaque_old_fields():
    source = legacy_payload()
    source["modules"]["table_settings"].update({"gm_style_override": None, "extension_metadata": {"opaque": [1]}})
    result = migrate_game_state_payload(source)
    assert result["modules"]["narration_preferences"]["gm_style_override"] is None
    assert result["modules"]["table_settings"] == {"schema_version": 1, "extension_metadata": {"opaque": [1]}}


@pytest.mark.parametrize("mutation", ["future-table", "conflicting-target", "future-economy-with-policy"])
def test_unknown_or_mixed_split_cannot_partially_convert_input(mutation):
    source = legacy_payload()
    if mutation == "future-table":
        source["modules"]["table_settings"]["schema_version"] = 99
    elif mutation == "conflicting-target":
        source["modules"]["game_config"] = {"schema_version": 1, "difficulty": "custom"}
    else:
        source["modules"]["economy"] = {"schema_version": 99, "opaque": [1]}
    before = deepcopy(source)
    with pytest.raises(ValueError):
        migrate_game_state_payload(source)
    assert source == before


@pytest.mark.parametrize("invalid", [
    "timeout", "dice", "policy", "future-narration", "future-checks", "perspective", "password",
])
def test_session_configuration_preflights_all_fields_before_writing_kernel_values(invalid):
    from src.engine.game_instance import GameInstance
    instance = GameInstance(game_key=("web", "atomic-config", "bot"))
    kwargs = {"solo_mode": True, "gm_uid": "new-gm"}
    if invalid == "timeout":
        kwargs["luck_timeout_seconds"] = -1
    elif invalid == "dice":
        kwargs["dice_reveal_mode"] = "unknown"
    elif invalid == "policy":
        kwargs["economy_reward_policy"] = {"mode": "unknown"}
    elif invalid == "perspective":
        kwargs["narrative_perspective"] = "bogus"
    elif invalid == "password":
        kwargs["room_password"] = "short"
    elif invalid == "future-narration":
        instance.modules["narration_preferences"] = {"schema_version": 99, "opaque": [1]}
        kwargs["narrative_perspective"] = "third_person"
    else:
        instance.modules["check_presentation"] = {"schema_version": 99, "opaque": [1]}
        kwargs["dice_reveal_mode"] = "auto"
    before = deepcopy(instance.to_dict())
    with pytest.raises((ValueError, RuntimeError)):
        instance.configure_session(**kwargs)
    assert instance.to_dict() == before
