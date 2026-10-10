"""Table settings persistence, migration and lifecycle contracts (R7-g)."""

from copy import deepcopy

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import table_settings as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v27_to_v28,
    migrate_game_state_payload,
)

VALUES = {
    "difficulty": "硬核",
    "narrative_perspective": "third_person",
    "gm_style_override": {"tone": "grim"},
    "solo_mode": True,
    "seed_code": "SEED42",
    "entry_point": "bot",
    "luck_timeout_seconds": 0,
    "economy_reward_policy": {"mode": "auto_small_cash", "auto_reward_cap": 5_000},
}


def test_upgrade_moves_values_verbatim_without_mutating_input():
    payload = {"instance_schema_version": 27, **deepcopy(VALUES)}
    before = deepcopy(payload)
    result = migrate_game_state_payload(payload)
    assert payload == before
    assert result["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert result["modules"][module.MODULE_NAME] == {"schema_version": 1, **VALUES, "dice_reveal_mode": "click"}
    assert all(key not in result for key in VALUES)
    assert migrate_game_state_payload(result) == result
    single = _migrate_v27_to_v28(deepcopy(payload))
    assert _migrate_v27_to_v28(deepcopy(single)) == single


def test_reward_cap_is_moved_not_rescaled():
    payload = {"instance_schema_version": 27, "rule_id": "freeform_coc",
               "economy_reward_policy": {"auto_reward_cap": 5_000}}
    result = migrate_game_state_payload(payload)
    assert result["modules"][module.MODULE_NAME]["economy_reward_policy"]["auto_reward_cap"] == 5_000


@pytest.mark.parametrize("slot", [{}, {"schema_version": 99, "opaque": [1]}, module.fresh()])
def test_existing_slot_wins_even_when_empty_or_unknown(slot):
    result = _migrate_v27_to_v28({"modules": {module.MODULE_NAME: slot}, **deepcopy(VALUES)})
    assert result["modules"][module.MODULE_NAME] is slot
    assert all(key not in result for key in VALUES)


def test_missing_values_use_the_same_defaults_as_the_old_codec():
    assert _migrate_v27_to_v28({})["modules"][module.MODULE_NAME] == module.fresh()
    assert module.fresh() == {
        "schema_version": 1, "difficulty": "标准", "narrative_perspective": "auto",
        "gm_style_override": None, "solo_mode": False, "seed_code": "", "entry_point": "web",
        "luck_timeout_seconds": 60, "economy_reward_policy": {},
        "dice_reveal_mode": "click",
    }
    assert module.ensure(None) == module.fresh()


def test_future_version_is_rejected():
    with pytest.raises(ValueError):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})


ACCESS = {
    "difficulty": (module.difficulty, module.replace_difficulty),
    "narrative_perspective": (module.narrative_perspective, module.replace_narrative_perspective),
    "gm_style_override": (module.gm_style_override, module.replace_gm_style_override),
    "solo_mode": (module.solo_mode, module.replace_solo_mode),
    "seed_code": (module.seed_code, module.replace_seed_code),
    "entry_point": (module.entry_point, module.replace_entry_point),
    "luck_timeout_seconds": (module.luck_timeout_seconds, module.replace_luck_timeout_seconds),
    "economy_reward_policy": (module.economy_reward_policy, module.replace_economy_reward_policy),
    "dice_reveal_mode": (module.dice_reveal_mode, module.replace_dice_reveal_mode),
}


def _apply_values(instance: GameInstance) -> None:
    for key, value in VALUES.items():
        ACCESS[key][1](instance, deepcopy(value))


def test_deleted_table_settings_facades_are_not_game_instance_attributes():
    instance = GameInstance(game_key=("web", "settings", "bot"))
    for name in ACCESS:
        assert not hasattr(GameInstance, name), name
        assert not hasattr(instance, name), name
        with pytest.raises(AttributeError, match="was removed"):
            setattr(instance, name, None)
        assert name not in vars(instance)


def test_module_api_shares_the_slot_object():
    instance = GameInstance(game_key=("web", "settings", "bot"))
    policy = {"mode": "auto_small_cash", "auto_reward_cap": 10}
    module.replace_economy_reward_policy(instance, policy)
    assert module.economy_reward_policy(instance) is policy is instance.modules[module.MODULE_NAME]["economy_reward_policy"]
    policy["auto_reward_cap"] = 20
    assert module.economy_reward_policy(instance)["auto_reward_cap"] == 20
    instance.set_narrative_perspective("immersive")
    assert instance.modules[module.MODULE_NAME]["narrative_perspective"] == "immersive"


def test_codec_roundtrip_preserves_all_settings():
    instance = GameInstance(game_key=("web", "roundtrip", "bot"))
    _apply_values(instance)
    payload = instance.to_dict()
    assert all(key not in payload for key in VALUES)
    restored = GameInstance.from_dict(payload)
    assert module.difficulty(restored) == "硬核"
    assert module.narrative_perspective(restored) == "third_person"
    assert module.gm_style_override(restored) == {"tone": "grim"}
    assert module.solo_mode(restored) is True
    assert module.seed_code(restored) == "SEED42"
    assert module.entry_point(restored) == "bot"
    assert module.luck_timeout_seconds(restored) == 0
    assert module.economy_reward_policy(restored) == {"mode": "auto_small_cash", "auto_reward_cap": 5_000}
    assert module.dice_reveal_mode(restored) == "click"


@pytest.mark.asyncio
async def test_reset_keeps_preserved_settings_and_does_not_clear_the_others():
    instance = GameInstance(game_key=("web", "reset", "bot"))
    _apply_values(instance)
    await instance.reset()
    assert module.solo_mode(instance) is True
    assert module.narrative_perspective(instance) == "third_person"
    assert module.gm_style_override(instance) == {"tone": "grim"}
    assert module.seed_code(instance) == "SEED42"
    assert module.difficulty(instance) == "硬核"
    assert module.entry_point(instance) == "bot"
    assert module.luck_timeout_seconds(instance) == 0
    assert module.economy_reward_policy(instance) == {"mode": "auto_small_cash", "auto_reward_cap": 5_000}
