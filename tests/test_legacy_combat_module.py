"""Combat turn view (derived, read-only) and retirement of the legacy_combat slot."""

from copy import deepcopy
from dataclasses import fields
from types import SimpleNamespace

import pytest

import src.rulesets.dnd2024  # noqa: F401  (registers the D&D combat view projector)
from src.engine.game_instance import GameInstance
from src.engine.module_state import registered_module_states
from src.engine.modules import legacy_combat as module
from src.engine.modules import round_safety, ruleset_runtime
from src.migrations.instance import (
    CURRENT_INSTANCE_SCHEMA_VERSION, _migrate_v41_to_v42, _migrate_v42_to_v43,
    migrate_game_state_payload,
)

DND_RUNTIME = "core:dnd2024"
SLOT = "legacy_combat"
VIEW_FIELDS = ("combat_state", "combat_active", "initiative_order", "initiative_current")
NEUTRAL = {"combat_state": "none", "combat_active": False, "initiative_order": [], "initiative_current": 0}
ENEMIES = [{"name": "guard", "hp": 12}]
LEGACY_VALUES = {
    "combat_active": True,
    "combat_enemies": deepcopy(ENEMIES),
    "combat_state": "active",
    "initiative_order": ["u1", "guard"],
    "initiative_current": 1,
}
ACTIVE_COMBAT = {"status": "active", "initiative": ["player:u1", "enemy:goblin"], "turn_index": 1}


def new_instance(**kwargs):
    return GameInstance(game_key=("test", "legacy-combat", "bot"), **kwargs)


def bind_dnd_combat(instance, combat=None):
    """Bind the D&D runtime and give it an encounter authority (test fixture)."""
    ruleset_runtime.replace_binding(instance, {
        "id": DND_RUNTIME, "version": 1, "content_version": "test", "state_schema_version": 1,
    })
    state = {"state_schema_version": 1}
    if combat is not None:
        state["combat"] = combat
    ruleset_runtime.replace_state(instance, state)
    return instance


# ---------- derived view --------------------------------------------------


def test_unbound_instance_reads_the_neutral_view_without_materializing_slots():
    instance = SimpleNamespace(modules={})
    for name, expected in NEUTRAL.items():
        assert getattr(module, name)(instance) == expected
    assert module.view(instance) == NEUTRAL
    assert instance.modules == {}


@pytest.mark.parametrize("runtime_id", ["core:unknown", "", "core:coc7"])
def test_runtime_without_projector_is_neutral_even_with_stored_combat(runtime_id):
    instance = new_instance()
    ruleset_runtime.replace_binding(instance, {"id": runtime_id} if runtime_id else {})
    ruleset_runtime.replace_state(instance, {"combat": dict(ACTIVE_COMBAT)})
    assert module.view(instance) == NEUTRAL


@pytest.mark.parametrize("status,expected_state", [
    ("active", "active"), ("ended", "none"), ("none", "none"), (None, "none"),
])
def test_dnd_view_projects_ruleset_authority_without_writing(status, expected_state):
    instance = bind_dnd_combat(new_instance(), {"status": status, "initiative": ["player:u1"], "turn_index": "2"})
    before = deepcopy(instance.modules)
    authority = ruleset_runtime.state(instance)["combat"]
    assert module.combat_state(instance) == expected_state
    assert module.combat_active(instance) is (status == "active")
    assert module.initiative_order(instance) == ["player:u1"]
    assert module.initiative_order(instance) is not authority["initiative"]
    assert module.initiative_current(instance) == 2
    assert instance.modules == before
    assert ruleset_runtime.state(instance)["combat"] is authority


@pytest.mark.parametrize("combat", [None, "corrupt", {}, {"turn_index": "x", "initiative": None}])
def test_dnd_view_without_a_usable_encounter_is_neutral(combat):
    instance = bind_dnd_combat(new_instance(), combat)
    assert module.view(instance) == NEUTRAL


def test_view_returns_fresh_lists_not_live_storage():
    instance = bind_dnd_combat(new_instance(), deepcopy(ACTIVE_COMBAT))
    order = module.initiative_order(instance)
    order.append("intruder")
    module.view(instance)["initiative_order"].clear()
    assert module.initiative_order(instance) == ACTIVE_COMBAT["initiative"]
    assert ruleset_runtime.state(instance)["combat"]["initiative"] == ACTIVE_COMBAT["initiative"]


def test_view_follows_authority_changes_immediately():
    instance = bind_dnd_combat(new_instance(), deepcopy(ACTIVE_COMBAT))
    assert module.combat_state(instance) == "active"
    ruleset_runtime.state(instance)["combat"]["status"] = "ended"
    assert module.combat_state(instance) == "none"
    assert module.combat_active(instance) is False


def test_view_ignores_a_stale_legacy_combat_slot():
    instance = bind_dnd_combat(new_instance(), deepcopy(ACTIVE_COMBAT))
    instance.modules[SLOT] = {"schema_version": 99, "opaque": True}
    assert module.combat_state(instance) == "active"
    assert instance.modules[SLOT] == {"schema_version": 99, "opaque": True}


# ---------- projector registry --------------------------------------------


def test_projector_registry_resolves_by_bound_runtime_and_normalizes(monkeypatch):
    monkeypatch.setattr(module, "_PROJECTORS", dict(module._PROJECTORS))
    calls = []

    def projector(instance):
        calls.append(instance)
        return {"combat_state": "active", "combat_active": 1, "initiative_order": ("a",), "initiative_current": True}

    module.register_combat_projector(" test:runtime ", projector)
    assert module.combat_projector_for("test:runtime") is projector
    instance = new_instance()
    ruleset_runtime.replace_binding(instance, {"id": "test:runtime"})
    # Bool is not an int turn index; tuples are copied into a fresh list.
    assert module.view(instance) == {
        "combat_state": "active", "combat_active": True, "initiative_order": ["a"], "initiative_current": 0,
    }
    assert calls == [instance]
    module.register_combat_projector("test:runtime", lambda instance: None)
    assert module.view(instance) == NEUTRAL


@pytest.mark.parametrize("runtime_id", ["", "  ", None])
def test_projector_registry_rejects_blank_runtime_ids(runtime_id):
    with pytest.raises(ValueError, match="runtime id"):
        module.register_combat_projector(runtime_id, lambda instance: None)


def test_projector_registry_rejects_non_callables():
    with pytest.raises(TypeError):
        module.register_combat_projector("test:runtime", "not callable")


def test_dnd_runtime_registers_its_projector_on_import():
    from src.rulesets.dnd2024.runtime import Dnd2024Runtime, project_combat_view

    assert module.combat_projector_for(Dnd2024Runtime.runtime_id) is project_combat_view


def test_bound_dnd_instance_reports_view_after_import_only():
    """A fresh interpreter that only imports the runtime package projects D&D combat."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = (
        "import src.rulesets.dnd2024\n"
        "from src.engine.game_instance import GameInstance\n"
        "from src.engine.modules import legacy_combat, ruleset_runtime\n"
        "i = GameInstance(game_key=('t','v','b'))\n"
        "ruleset_runtime.replace_binding(i, {'id': 'core:dnd2024'})\n"
        "ruleset_runtime.replace_state(i, {'combat': {'status': 'active', 'initiative': ['x'], 'turn_index': 0}})\n"
        "import sys\n"
        "assert 'src.rulesets.dnd2024.combat.engine' in sys.modules\n"
        "print(legacy_combat.combat_state(i), legacy_combat.initiative_order(i))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "active ['x']"


# ---------- no persisted slot ---------------------------------------------


def test_module_owns_no_state_slot():
    assert SLOT not in {spec.name for spec in registered_module_states()}
    instance = new_instance()
    assert SLOT not in instance.modules
    assert SLOT not in instance.to_dict().get("modules", {})
    for name in ("MODULE_NAME", "SCHEMA_VERSION", "SPEC", "fresh", "ensure", "require_writable",
                 "combat_enemies", "replace_combat_enemies", "restore_from_entity_snapshot", "reset"):
        assert not hasattr(module, name), name


def test_retired_combat_facades_are_not_game_instance_attributes():
    instance = new_instance()
    assert not LEGACY_VALUES.keys() & {item.name for item in fields(instance)}
    for key in LEGACY_VALUES:
        assert not hasattr(GameInstance, key)
        assert not hasattr(instance, key)
        with pytest.raises(AttributeError, match="was removed"):
            setattr(instance, key, LEGACY_VALUES[key])
    for name in ("begin_combat", "end_combat"):
        assert not hasattr(GameInstance, name)
    for name in ("begin", "end", "project_from_ruleset", "restore_from_transaction",
                 *(f"replace_{field}" for field in VIEW_FIELDS)):
        assert not hasattr(module, name)


# ---------- restore / reset -----------------------------------------------


def test_entity_snapshot_records_view_and_restore_ignores_old_enemy_and_turn_keys():
    instance = bind_dnd_combat(new_instance(), deepcopy(ACTIVE_COMBAT))
    instance.npcs = {"goblin": {"hp": 5}}
    instance.capture_round_entity_snapshot()
    snapshot = round_safety.round_entity_snapshot(instance)
    assert "combat_enemies" not in snapshot
    assert {key: snapshot[key] for key in VIEW_FIELDS} == {
        "combat_state": "active", "combat_active": True,
        "initiative_order": ACTIVE_COMBAT["initiative"], "initiative_current": 1,
    }
    # An old persisted snapshot still carries the retired keys.
    round_safety.replace_entity_snapshot(instance, {
        "npcs": {"goblin": {"hp": 9}}, "combat_enemies": deepcopy(ENEMIES),
        "combat_state": "none", "combat_active": False, "initiative_order": [], "initiative_current": 0,
    })
    authority = deepcopy(ruleset_runtime.state(instance))
    assert instance.restore_round_entity_snapshot() is True
    assert instance.npcs == {"goblin": {"hp": 9}}
    assert SLOT not in instance.modules
    assert module.combat_state(instance) == "active"
    assert ruleset_runtime.state(instance) == authority


def test_transaction_restore_takes_view_from_restored_ruleset_state():
    instance = bind_dnd_combat(new_instance(), deepcopy(ACTIVE_COMBAT))
    instance.players = {}
    snapshot = {
        "ruleset_state": {"state_schema_version": 1, "combat": {"status": "ended", "initiative": [], "turn_index": 0}},
        "event_ledger": [],
        "players": {},
        # Stale stored copies in the snapshot must not override authority.
        "combat_state": "active", "combat_active": True,
        "initiative_order": ["stale"], "initiative_current": 5,
    }
    instance.restore_ruleset_transaction(snapshot)
    assert module.view(instance) == NEUTRAL
    snapshot["ruleset_state"]["combat"] = deepcopy(ACTIVE_COMBAT)
    snapshot["combat_state"] = "none"
    instance.restore_ruleset_transaction(snapshot)
    assert module.combat_state(instance) == "active"
    assert module.initiative_order(instance) == ACTIVE_COMBAT["initiative"]


@pytest.mark.asyncio
async def test_reset_turns_the_view_neutral_without_a_slot():
    instance = bind_dnd_combat(new_instance(), deepcopy(ACTIVE_COMBAT))
    await instance.reset()
    assert module.view(instance) == NEUTRAL
    assert SLOT not in instance.modules


# ---------- migrations ----------------------------------------------------


def test_v32_payload_migrates_through_to_no_slot():
    original = {"instance_schema_version": 32, **deepcopy(LEGACY_VALUES), "opaque": {"items": [1]}}
    before = deepcopy(original)
    migrated = migrate_game_state_payload(original)
    assert original == before
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert SLOT not in migrated["modules"]
    assert not LEGACY_VALUES.keys() & migrated.keys()
    assert migrate_game_state_payload(migrated) == migrated


def payload_at(version, slot, **extra):
    modules = {} if slot is None else {SLOT: slot}
    return {"game_key": ["test", "legacy-combat", "bot"], "state": "waiting",
            "instance_schema_version": version, "modules": modules, **extra}


def test_v41_to_v42_strips_stored_turn_copies_and_keeps_enemies():
    original = payload_at(41, {"schema_version": 1, **deepcopy(LEGACY_VALUES)})
    step = _migrate_v41_to_v42(deepcopy(original))
    assert step["modules"][SLOT] == {"schema_version": 2, "combat_enemies": ENEMIES}
    assert step["instance_schema_version"] == 42
    assert _migrate_v41_to_v42(deepcopy(step)) == step


@pytest.mark.parametrize("version", [41, 42])
@pytest.mark.parametrize("slot", [
    None, "corrupt", {"schema_version": 1, **LEGACY_VALUES},
    {"schema_version": 2, "combat_enemies": ENEMIES}, {"schema_version": 99, "opaque": [1]},
])
def test_old_saves_drop_the_slot_whatever_its_shape(version, slot):
    original = payload_at(version, deepcopy(slot))
    before = deepcopy(original)
    migrated = migrate_game_state_payload(original)
    assert original == before
    assert migrated["instance_schema_version"] == CURRENT_INSTANCE_SCHEMA_VERSION
    assert SLOT not in migrated["modules"]
    assert migrate_game_state_payload(migrated) == migrated


def test_v42_to_v43_drops_enemies_without_creating_npcs():
    npcs = {"keeper": {"name": "Keeper", "hp": 4}}
    original = payload_at(42, {"schema_version": 2, "combat_enemies": [
        {"name": "Goblin", "hp": 7}, {"actor_id": "enemy:1", "character_name": "Orc", "hp": 9},
    ]}, npcs=deepcopy(npcs))
    step = _migrate_v42_to_v43(deepcopy(original))
    assert SLOT not in step["modules"]
    assert step["npcs"] == npcs
    assert _migrate_v42_to_v43(deepcopy(step)) == step
    restored = GameInstance.from_dict(original)
    assert restored.npcs == npcs
    assert SLOT not in restored.modules
    assert SLOT not in restored.to_dict()["modules"]


def test_old_dnd_save_with_active_stored_combat_reads_view_from_ruleset_state():
    ruleset_slot = {
        "schema_version": 1,
        "binding": {"id": DND_RUNTIME, "version": 1, "content_version": "test", "state_schema_version": 1},
        "state": {"state_schema_version": 1, "combat": {"status": "active", "initiative": ["player:u9"], "turn_index": 0}},
        "event_ledger": [],
    }
    # The stored copies disagree with the authority on purpose.
    payload = payload_at(41, {"schema_version": 1, **deepcopy(LEGACY_VALUES)})
    payload["modules"]["ruleset_runtime"] = ruleset_slot
    restored = GameInstance.from_dict(payload)
    assert SLOT not in restored.modules
    assert module.view(restored) == {
        "combat_state": "active", "combat_active": True,
        "initiative_order": ["player:u9"], "initiative_current": 0,
        "current_actor_id": "player:u9",
    }


def test_old_non_dnd_save_with_active_stored_combat_reads_neutral():
    restored = GameInstance.from_dict(payload_at(41, {"schema_version": 1, **deepcopy(LEGACY_VALUES)}))
    assert module.view(restored) == NEUTRAL
    assert SLOT not in restored.modules
