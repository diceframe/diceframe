"""Room access persistence, migration and lifecycle contracts (R7-h)."""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from src.commands.game_lifecycle import GameLifecycle
from src.engine.game_instance import GameInstance
from src.engine.module_state import ModuleStateError
from src.engine.modules import room_access as module
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION,
    _migrate_v28_to_v29,
    migrate_game_state_payload,
)

VALUES = {
    "max_players": 9,
    "player_access_open": False,
    "bot_bind_token": "bind-token",
    "room_password": "plain-password",
    "room_token": "room-token",
}


def test_upgrade_moves_values_without_mutating_input():
    payload = {"instance_schema_version": 28, "gm_uid": "gm", **VALUES}
    before = deepcopy(payload)
    result = migrate_game_state_payload(payload)
    assert payload == before
    assert result["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert result["modules"][module.MODULE_NAME] == {"schema_version": 1, **VALUES}
    assert all(key not in result for key in VALUES)
    assert result["gm_uid"] == "gm"


def test_migration_is_idempotent():
    result = migrate_game_state_payload({"instance_schema_version": 28, **VALUES})
    assert migrate_game_state_payload(result) == result
    single = _migrate_v28_to_v29(dict(VALUES))
    assert _migrate_v28_to_v29(deepcopy(single)) == single


@pytest.mark.parametrize("slot", [{}, {"schema_version": 99, "opaque": [1]}, module.fresh()])
def test_existing_slot_wins_even_when_empty_or_unknown(slot):
    result = _migrate_v28_to_v29({"modules": {module.MODULE_NAME: slot}, **VALUES})
    assert result["modules"][module.MODULE_NAME] is slot
    assert all(key not in result for key in VALUES)


def test_missing_values_use_old_codec_defaults():
    defaults = {
        "schema_version": 1,
        "max_players": 6,
        "player_access_open": True,
        "bot_bind_token": "",
        "room_password": "",
        "room_token": "",
    }
    assert module.fresh() == defaults
    assert _migrate_v28_to_v29({})["modules"][module.MODULE_NAME] == defaults
    assert module.ensure(None) == defaults
    raw = {"schema_version": 1}
    assert module.ensure(raw) is raw
    assert raw == defaults


@pytest.mark.parametrize("missing_key", VALUES)
def test_partial_slots_and_legacy_payloads_default_only_the_missing_key(missing_key):
    values = {key: value for key, value in VALUES.items() if key != missing_key}
    expected = {"schema_version": 1, **VALUES, missing_key: module.fresh()[missing_key]}
    assert _migrate_v28_to_v29(dict(values))["modules"][module.MODULE_NAME] == expected
    assert module.ensure({"schema_version": 1, **values}) == expected


@pytest.mark.parametrize("value", [None, False, 0, "", "unconventional", [], {"opaque": [1]}])
def test_present_values_are_preserved_by_migration_ensure_and_codec(value):
    values = {key: deepcopy(value) for key in VALUES}
    payload = {
        "instance_schema_version": 28,
        "game_key": ["web", "legacy", "bot"],
        "state": "created",
        **values,
    }
    migrated = migrate_game_state_payload(payload)
    assert migrated["modules"][module.MODULE_NAME] == {"schema_version": 1, **values}
    raw = {"schema_version": 1, **values}
    assert module.ensure(raw) is raw
    for key in VALUES:
        assert raw[key] is values[key]
    restored = GameInstance.from_dict(payload)
    roundtrip = GameInstance.from_dict(restored.to_dict())
    for instance in (restored, roundtrip):
        for key in VALUES:
            assert getattr(instance, key) == value
            assert type(getattr(instance, key)) is type(value)


def test_future_instance_version_is_rejected():
    with pytest.raises(ValueError, match="unsupported game instance schema"):
        migrate_game_state_payload({"instance_schema_version": CURRENT_INSTANCE_SCHEMA_VERSION + 1})


def test_future_module_version_is_preserved_but_rejected_on_access():
    slot = {"schema_version": module.SCHEMA_VERSION + 1, "opaque": [1]}
    before = deepcopy(slot)
    assert module.ensure(slot) is slot
    instance = GameInstance(game_key=("web", "future", "bot"), modules={module.MODULE_NAME: slot})
    for key in VALUES:
        with pytest.raises(ModuleStateError):
            getattr(instance, key)
        with pytest.raises(ModuleStateError):
            setattr(instance, key, VALUES[key])
    assert slot == before


@pytest.mark.parametrize("key", VALUES)
def test_properties_return_and_replace_the_same_object(key):
    instance = GameInstance(game_key=("web", "identity", "bot"))
    value = {"opaque": []}
    setattr(instance, key, value)
    assert getattr(instance, key) is value is instance.modules[module.MODULE_NAME][key]
    value["opaque"].append("changed")
    assert getattr(instance, key)["opaque"] == ["changed"]
    replacement = ["replacement"]
    setattr(instance, key, replacement)
    assert getattr(instance, key) is replacement is instance.modules[module.MODULE_NAME][key]


def test_codec_roundtrip_preserves_all_room_settings():
    instance = GameInstance(game_key=("web", "roundtrip", "bot"), gm_uid="gm")
    for key, value in VALUES.items():
        setattr(instance, key, value)
    payload = instance.to_dict()
    assert all(key not in payload for key in VALUES)
    assert payload["modules"][module.MODULE_NAME] == {"schema_version": 1, **VALUES}
    assert payload["gm_uid"] == "gm"
    restored = GameInstance.from_dict(payload)
    for key, value in VALUES.items():
        assert getattr(restored, key) == value
    assert restored.gm_uid == "gm"


@pytest.mark.parametrize("password", ["new-password", ""])
def test_set_room_password_keeps_plaintext_and_clears_token(password):
    instance = GameInstance(game_key=("web", "password", "bot"))
    instance.set_room_token("previous-session")
    instance.set_room_password(password)
    assert instance.room_password == password
    assert instance.room_token == ""
    assert instance.modules[module.MODULE_NAME]["room_password"] == password
    assert instance.modules[module.MODULE_NAME]["room_token"] == ""


@pytest.mark.asyncio
async def test_reset_preserves_room_access_values_and_slot_identity():
    instance = GameInstance(game_key=("web", "reset", "bot"))
    for key, value in VALUES.items():
        setattr(instance, key, value)
    slot = instance.modules[module.MODULE_NAME]
    await instance.reset()
    assert instance.modules[module.MODULE_NAME] is slot
    for key, value in VALUES.items():
        assert getattr(instance, key) == value


@pytest.mark.asyncio
async def test_new_run_candidate_copies_room_access_settings():
    source = GameInstance(game_key=("web", "new-run", "bot"), gm_uid="gm")
    for key, value in VALUES.items():
        setattr(source, key, value)
    candidate = GameInstance(game_key=source.game_key)
    lifecycle = GameLifecycle(
        registry=Mock(), llm_client=Mock(), prompt=Mock(), state_applier=Mock(),
        ensure_matcher_for_world=Mock(), create_game=AsyncMock(return_value=candidate),
        load_world_template=Mock(), narrative_max_tokens=100, brief_max_tokens=100,
    )
    result = await lifecycle._new_run_candidate(source, preserve_players=False)
    assert result is candidate
    assert result.modules[module.MODULE_NAME] is not source.modules[module.MODULE_NAME]
    for key, value in VALUES.items():
        assert getattr(result, key) == value
    assert result.gm_uid == source.gm_uid
