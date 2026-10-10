"""Runtime module dependency, ownership and aggregate-size boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
MODULES = SRC / "engine" / "modules"
MAX_TOP_LEVEL_FIELDS = 37

CONTROL_WRITERS = {
    SRC / "engine" / "player_control.py",
    SRC / "migrations" / "instance.py",
    # This writes the creation response dict, not the instance's seat state.
    SRC / "webui" / "services" / "game_creation_phases.py",
}


# The GameInstance combat facades are gone, so any ``x.combat_extension`` /
# ``x.combat_extension_round_snapshots`` store outside these owners would be a
# silent shadow attribute on the dataclass. Direct attribute writes only: this
# does not police aliases, subscript mutation, clear/pop, or generic setattr.
COMBAT_WRITERS = {
    SRC / "webui" / "services" / "combat_extension.py",
    SRC / "engine" / "round_snapshots.py",
    MODULES / "combat_extension_state.py",
    SRC / "migrations" / "instance.py",
    # Existing lifecycle owner replaces current and clears snapshots on reset.
    SRC / "engine" / "instance_lifecycle.py",
    # Existing rollback/abort owner clears current on invalid restoration.
    SRC / "engine" / "round_recovery.py",
    # Existing historical rewrite owner has the same fail-closed fallback.
    SRC / "commands" / "swipe_generator.py",
}


def _combat_property_writes(path: Path, tree: ast.AST) -> list[int]:
    if path in COMBAT_WRITERS:
        return []
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.attr in {"combat_extension", "combat_extension_round_snapshots"}
    ]


def test_no_combat_shadow_attribute_writes_outside_owners() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line in _combat_property_writes(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: combat attribute write outside owner")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    "other.combat_extension = {}",
    "self.combat_extension_round_snapshots: dict = {}",
    "other.combat_extension |= {}",
    "other.combat_extension, (x, self.combat_extension_round_snapshots) = values",
    "del other.combat_extension_round_snapshots",
])
def test_combat_guard_rejects_direct_writes_including_game_instance(source) -> None:
    tree = ast.parse(source)
    assert _combat_property_writes(SRC / "engine" / "game_instance.py", tree)
    assert _combat_property_writes(SRC / "webui" / "routes" / "outsider.py", tree)
    for owner in COMBAT_WRITERS:
        assert not _combat_property_writes(owner, tree)


# Writes go through the module API; keep those calls in the same owner files.
# GameInstance no longer has compatibility setters, so it is not an owner.
# Matches ``combat_extension_state.replace_*(...)`` only, not import aliases.
COMBAT_REPLACE_CALLERS = COMBAT_WRITERS


def _combat_replace_calls(path: Path, tree: ast.AST) -> list[int]:
    if path in COMBAT_REPLACE_CALLERS:
        return []
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "combat_extension_state"
        and node.func.attr in {"replace_current", "replace_round_snapshots"}
    ]


def test_only_combat_owners_call_replace_api() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line in _combat_replace_calls(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: combat replace call outside owner")
    assert not violations, "\n".join(violations)


def test_combat_replace_guard_rejects_non_owner_calls() -> None:
    tree = ast.parse("combat_extension_state.replace_current(x, {})\n"
                     "combat_extension_state.replace_round_snapshots(x, {})")
    assert len(_combat_replace_calls(SRC / "webui" / "routes" / "outsider.py", tree)) == 2
    assert len(_combat_replace_calls(SRC / "engine" / "game_instance.py", tree)) == 2
    for owner in COMBAT_REPLACE_CALLERS:
        assert not _combat_replace_calls(owner, tree)


def test_only_ruleset_runtime_owners_assign_binding() -> None:
    owners = {
        SRC / "engine" / "game_instance.py",
        MODULES / "ruleset_runtime.py",
        SRC / "engine" / "game_state_codec.py",
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            if node.attr == "ruleset_runtime":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: ruleset binding write outside owner")
    assert not violations, "\n".join(violations)


def test_only_legacy_combat_owners_assign_fields() -> None:
    owners = {
        SRC / "engine" / "game_instance.py",
        MODULES / "legacy_combat.py",
        SRC / "engine" / "game_state_codec.py",
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            if node.attr not in {
                "combat_active", "combat_enemies", "combat_state", "initiative_order", "initiative_current",
            }:
                continue
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: legacy combat write outside owner")
    assert not violations, "\n".join(violations)


def test_only_round_safety_owners_assign_fields() -> None:
    owners = {
        SRC / "engine" / "game_instance.py",
        MODULES / "round_safety.py",
        SRC / "engine" / "game_state_codec.py",
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            if node.attr not in {"round_start_snapshot", "round_entity_snapshot", "death_save_outcomes"}:
                continue
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: round safety write outside owner")
    assert not violations, "\n".join(violations)


def test_only_adventure_runtime_owners_assign_fields() -> None:
    owners = {
        SRC / "engine" / "game_instance.py",
        MODULES / "adventure_runtime_state.py",
        SRC / "engine" / "game_state_codec.py",
    }
    fields = {"adventure_progress", "play_mode"}
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                if node.attr in fields:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: adventure runtime write outside owner")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in fields
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: adventure runtime setattr outside owner")
    assert not violations, "\n".join(violations)


def test_only_narrative_notes_owners_assign_scene() -> None:
    owners = {
        SRC / "engine" / "game_instance.py",
        MODULES / "narrative_notes.py",
        SRC / "engine" / "game_state_codec.py",
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and node.attr == "scene"
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: scene write outside owner")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "scene"
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: scene setattr outside owner")
    assert not violations, "\n".join(violations)


def test_only_checks_owners_assign_fields() -> None:
    owners = {
        SRC / "engine" / "game_instance.py",
        MODULES / "checks.py",
        SRC / "engine" / "game_state_codec.py",
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            if node.attr not in {"last_check", "last_checks", "round_checks_prepared", "manual_roll_requests"}:
                continue
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: checks write outside owner")
    assert not violations, "\n".join(violations)


def test_only_session_stats_owners_assign_fields() -> None:
    owners = {
        SRC / "engine" / "game_instance.py",
        MODULES / "session_stats.py",
        SRC / "engine" / "game_state_codec.py",
    }
    # Same names on unrelated response/plugin objects are not session stats.
    unrelated = {
        (SRC / "commands" / "protocol_repair.py", "repaired", "total_tokens"),
        (SRC / "plugin_host" / "host.py", "runtime", "started_at"),
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            if node.attr not in {"total_llm_calls", "total_tokens", "started_at", "last_activity"}:
                continue
            if isinstance(node.value, ast.Name) and (path, node.value.id, node.attr) in unrelated:
                continue
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: session stats write outside owner")
    assert not violations, "\n".join(violations)


def test_only_private_channel_owners_assign_compatibility_properties() -> None:
    # Aggregate methods own appends/removal; lifecycle owns reset clears.
    owners = {
        MODULES / "private_channels.py",
        SRC / "engine" / "game_instance.py",
        SRC / "engine" / "instance_lifecycle.py",
        SRC / "migrations" / "instance.py",
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Attribute, ast.Subscript)) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            target = node
            while isinstance(target, ast.Subscript):
                target = target.value
            if isinstance(target, ast.Attribute) and target.attr in {"private_log", "table_talk"}:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: private channel write outside owner")
    assert not violations, "\n".join(violations)


def test_only_media_owners_assign_compatibility_properties() -> None:
    # Aggregate setters own updates; lifecycle retains its existing reset policy.
    owners = {
        MODULES / "media.py",
        SRC / "engine" / "game_instance.py",
        SRC / "engine" / "instance_lifecycle.py",
        SRC / "migrations" / "instance.py",
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in owners:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Attribute, ast.Subscript)) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            target = node
            while isinstance(target, ast.Subscript):
                target = target.value
            if isinstance(target, ast.Attribute) and target.attr in {"scene_image", "map_background"}:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: media write outside owner")
    assert not violations, "\n".join(violations)


def _world_report_property_writes(path: Path, tree: ast.AST) -> list[int]:
    if path == MODULES / "world_reports.py":
        return []
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.attr in {"last_overreach", "last_world_legality", "last_world_events"}
    ]


def test_only_world_report_owner_assigns_compatibility_properties() -> None:
    # GameInstance.reset_round_checks owns existing in-place clears. This guard
    # covers direct attribute assignment/deletion, as the combat guard does.
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line in _world_report_property_writes(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: world report property write outside owner")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    "instance.last_overreach = []",
    "self.last_world_legality: list = []",
    "instance.last_world_events += []",
    "instance.last_overreach, (x, self.last_world_events) = values",
    "del instance.last_world_legality",
])
def test_world_report_guard_rejects_direct_writes(source) -> None:
    tree = ast.parse(source)
    for path in ("engine/game_instance.py", "commands/round_processor.py", "webui/routes/outsider.py"):
        assert _world_report_property_writes(SRC / path, tree)
    assert not _world_report_property_writes(MODULES / "world_reports.py", tree)


TABLE_SETTINGS_FIELDS = frozenset({
    "difficulty", "narrative_perspective", "gm_style_override", "solo_mode",
    "seed_code", "entry_point", "luck_timeout_seconds", "economy_reward_policy",
    "dice_reveal_mode",
})
# The GameInstance table settings facades are gone: writes go through
# ``table_settings.replace_<field>(...)``. Aggregate methods (configure_*/set_*)
# and the reset lifecycle own those calls; new-run construction in
# commands/game_lifecycle.py copies the source run's gm_style_override.
TABLE_SETTINGS_REPLACE_CALLERS = {
    MODULES / "table_settings.py",
    SRC / "engine" / "game_instance.py",
    SRC / "engine" / "instance_lifecycle.py",
    SRC / "commands" / "game_lifecycle.py",
}


def _table_settings_property_writes(path: Path, tree: ast.AST) -> list[int]:
    """Direct attribute stores of a retired name (would raise on GameInstance)."""
    if path == MODULES / "table_settings.py":
        return []
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.attr in TABLE_SETTINGS_FIELDS
    ]


def _table_settings_replace_calls(path: Path, tree: ast.AST) -> list[int]:
    if path in TABLE_SETTINGS_REPLACE_CALLERS:
        return []
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "table_settings"
        and node.func.attr.startswith("replace_")
        and node.func.attr.removeprefix("replace_") in TABLE_SETTINGS_FIELDS
    ]


def test_only_table_settings_owners_assign_compatibility_properties() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line in _table_settings_property_writes(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: table settings attribute write")
        for line in _table_settings_replace_calls(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: table settings replace call outside owner")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    "candidate.gm_style_override = {}",
    "instance.solo_mode: bool = True",
    "instance.seed_code += 'x'",
    "del instance.economy_reward_policy",
    "self.difficulty = '硬核'",
])
def test_table_settings_guard_rejects_external_writes(source) -> None:
    tree = ast.parse(source)
    for path in ("commands/game_lifecycle.py", "engine/game_instance.py", "engine/instance_lifecycle.py"):
        assert _table_settings_property_writes(SRC / path, tree)
    assert not _table_settings_property_writes(MODULES / "table_settings.py", tree)


def test_table_settings_guard_rejects_replace_calls_outside_owners() -> None:
    tree = ast.parse("table_settings.replace_solo_mode(x, True)\n"
                     "table_settings.replace_economy_reward_policy(x, {})\n"
                     "table_settings.solo_mode(x)")
    assert len(_table_settings_replace_calls(SRC / "webui" / "services" / "game_controls.py", tree)) == 2
    for owner in TABLE_SETTINGS_REPLACE_CALLERS:
        assert not _table_settings_replace_calls(owner, tree)


def _room_access_property_writes(path: Path, tree: ast.AST) -> list[int]:
    # Aggregate methods retain their existing room access mutation policy.
    if path in {MODULES / "room_access.py", SRC / "engine" / "game_instance.py"}:
        return []
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.attr in {"max_players", "player_access_open", "bot_bind_token", "room_password", "room_token"}
    ]


def test_only_room_access_owners_assign_compatibility_properties() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line in _room_access_property_writes(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: room access write outside owner")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    "candidate.max_players = 4",
    "instance.player_access_open: bool = False",
    "instance.bot_bind_token += 'x'",
    "instance.room_password, (x, instance.room_token) = values",
    "del instance.room_token",
])
def test_room_access_guard_rejects_external_writes(source) -> None:
    tree = ast.parse(source)
    assert _room_access_property_writes(SRC / "commands" / "game_lifecycle.py", tree)
    assert _room_access_property_writes(SRC / "webui" / "routes" / "outsider.py", tree)
    assert not _room_access_property_writes(MODULES / "room_access.py", tree)
    assert not _room_access_property_writes(SRC / "engine" / "game_instance.py", tree)


def _runtime_nodes(node: ast.AST):
    """Ignore type-only bodies, but still inspect runtime else branches."""
    yield node
    if isinstance(node, ast.If) and (
        isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        or isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
    ):
        for child in node.orelse:
            yield from _runtime_nodes(child)
        return
    for child in ast.iter_child_nodes(node):
        yield from _runtime_nodes(child)


def test_runtime_modules_do_not_import_game_instance() -> None:
    violations: list[str] = []
    paths = [SRC / "engine" / "module_state.py", *sorted(MODULES.glob("*.py"))]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in _runtime_nodes(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    parts = list(path.relative_to(ROOT).with_suffix("").parts[:-1])
                    module = ".".join(parts[:len(parts) - node.level + 1] + ([module] if module else []))
                names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            if any(name == "src.engine.game_instance" or name.startswith("src.engine.game_instance.") for name in names):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: runtime GameInstance import")
    assert not violations, "\n".join(violations)


def test_only_module_owners_write_module_slots() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.parent == MODULES or path == SRC / "migrations" / "instance.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            # Store/Del also covers annotated/augmented assignments and nested
            # indexing, so a second subscript cannot bypass slot ownership.
            if not isinstance(node, ast.Subscript) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            value = node.value
            while isinstance(value, ast.Subscript):
                value = value.value
            if isinstance(value, ast.Attribute) and value.attr == "modules":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: module slot write outside owner")
    assert not violations, "\n".join(violations)


def test_only_player_control_owner_writes_seat_controls() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in CONTROL_WRITERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            target = node
            while isinstance(target, ast.Subscript):
                key = target.slice
                if (
                    isinstance(key, ast.Constant) and key.value == "control"
                    or isinstance(key, ast.Name) and key.id == "CONTROL_KEY"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: seat control write outside owner")
                    break
                target = target.value
    assert not violations, "\n".join(violations)


def test_only_economy_owners_write_ledger_keys() -> None:
    writers = {
        SRC / "engine" / "economy.py",
        MODULES / "economy_state.py",
        SRC / "migrations" / "instance.py",
        # Known direct outbox writer; converge on an owner API after R2.
        SRC / "engine" / "memory_outbox.py",
    }
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in writers:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript) or not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            value = node.value
            while isinstance(value, ast.Subscript):
                value = value.value
            # Facade receiver (``x.economy[...]``) or module API
            # (``economy_state.state(x)[...]``) — both reach the live ledger.
            if isinstance(value, ast.Call):
                value = value.func
            if isinstance(value, ast.Attribute) and (
                value.attr == "economy"
                or (value.attr == "state" and isinstance(value.value, ast.Name) and value.value.id == "economy_state")
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: economy key write outside owner")
    assert not violations, "\n".join(violations)


def _imports(path: Path, tree: ast.AST):
    """Resolve ordinary, from-parent and relative imports, including local ones."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                parts = list(path.relative_to(ROOT).with_suffix("").parts[:-1])
                module = ".".join(parts[:len(parts) - node.level + 1] + ([module] if module else []))
            for alias in node.names:
                yield node.lineno, f"{module}.{alias.name}"


def _under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def test_action_gate_does_not_import_transport_commands_or_rulesets() -> None:
    path = SRC / "engine" / "action_gate.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    violations = [
        f"{path.relative_to(ROOT)}:{line}: forbidden import {name}"
        for line, name in _imports(path, tree)
        if any(_under(name, prefix) for prefix in ("src.webui", "src.commands", "src.rulesets"))
    ]
    assert not violations, "\n".join(violations)


def test_engine_does_not_import_commands() -> None:
    # Existing economy effect application imports this reward normalizer locally.
    # Keep that exact debt visible; R4-a does not move economy/item application.
    exceptions = {(SRC / "engine" / "economy.py", "src.commands.state_items.normalized_reward_entries")}
    violations: list[str] = []
    for path in sorted((SRC / "engine").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line, name in _imports(path, tree):
            if _under(name, "src.commands") and (path, name) not in exceptions:
                violations.append(f"{path.relative_to(ROOT)}:{line}: engine imports {name}")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source, prefix", [
    ("import src.commands.ai_player as ai", "src.commands"),
    ("from src import commands", "src.commands"),
    ("from ..commands import ai_player", "src.commands"),
    ("def helper():\n    from src.commands.state_items import normalized_reward_entries", "src.commands"),
    ("from src.webui.services import turns", "src.webui"),
    ("from .. import rulesets", "src.rulesets"),
    ("if TYPE_CHECKING:\n    import src.rulesets.contracts", "src.rulesets"),
])
def test_gate_dependency_guard_resolves_import_forms(source, prefix) -> None:
    path = SRC / "engine" / "action_gate.py"
    assert any(_under(name, prefix) for _, name in _imports(path, ast.parse(source)))


# The GameInstance round_number setter forwards to the same module writer.
ROUND_COUNTER_MODULE_WRITERS = {SRC / "engine" / "progression.py", SRC / "engine" / "game_instance.py"}


def _round_counter_writes(path: Path, tree: ast.AST) -> list[int]:
    # Field declarations and constructor keywords (e.g. DecisionEntry's independent
    # round_number) are not runtime attribute writes. No file-wide exceptions.
    # Like the other ownership guards, this does not track aliases or setattr.
    # The module write path ``progression_state.set_round_value(...)`` is
    # reserved for progression (plus the compatibility setter).
    if path == SRC / "engine" / "progression.py":
        return []
    attribute_writes = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.attr == "round_number"
    ]
    if path in ROUND_COUNTER_MODULE_WRITERS:
        return attribute_writes
    return attribute_writes + [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "progression_state"
        and node.func.attr == "set_round_value"
    ]


def test_only_progression_writes_round_counter() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line in _round_counter_writes(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: round counter write outside progression")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    "instance.round_number = 3",
    "self.round_number += 1",
    "instance.round_number: int = 3",
    "other.round_number, (x, self.round_number) = values",
    "del instance.round_number",
])
def test_round_counter_guard_rejects_attribute_writes(source) -> None:
    tree = ast.parse(source)
    for path in ("engine/game_instance.py", "engine/plot_tracker.py", "rulesets/automation.py"):
        assert _round_counter_writes(SRC / path, tree)
    assert not _round_counter_writes(SRC / "engine/progression.py", tree)


def test_round_counter_guard_rejects_module_writes_outside_progression() -> None:
    tree = ast.parse("progression_state.set_round_value(instance, 3)")
    for path in ("engine/plot_tracker.py", "rulesets/automation.py", "webui/services/turns.py"):
        assert _round_counter_writes(SRC / path, tree)
    for owner in ROUND_COUNTER_MODULE_WRITERS:
        assert not _round_counter_writes(owner, tree)


def test_round_counter_guard_allows_independent_fields_and_construction() -> None:
    tree = ast.parse("class Decision:\n    round_number: int = 0\nd = Decision(round_number=3)")
    assert not _round_counter_writes(SRC / "engine/plot_tracker.py", tree)


def test_progression_does_not_import_transport_commands_or_rulesets() -> None:
    path = SRC / "engine" / "progression.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    violations = [
        f"{path.relative_to(ROOT)}:{line}: forbidden import {name}"
        for line, name in _imports(path, tree)
        if any(_under(name, prefix) for prefix in ("src.webui", "src.commands", "src.rulesets"))
    ]
    assert not violations, "\n".join(violations)


def test_participant_view_does_not_import_transport_or_runtime() -> None:
    path = SRC / "engine" / "participant_view.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    forbidden = ("src.webui", "src.commands", "src.rulesets", "src.engine.game_instance")
    violations = [
        f"{path.relative_to(ROOT)}:{line}: forbidden import {name}"
        for line, name in _imports(path, tree)
        if any(_under(name, prefix) for prefix in forbidden)
    ]
    assert not violations, "\n".join(violations)


def test_visibility_rules_do_not_import_transport_or_runtime() -> None:
    path = SRC / "engine" / "visibility_rules.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    forbidden = ("src.webui", "src.commands", "src.rulesets", "src.engine.game_instance")
    violations = [
        f"{path.relative_to(ROOT)}:{line}: forbidden import {name}"
        for line, name in _imports(path, tree)
        if any(_under(name, prefix) for prefix in forbidden)
    ]
    assert not violations, "\n".join(violations)


def _duplicate_proposal_visibility_checks(path: Path, tree: ast.AST) -> list[int]:
    if path in {SRC / "engine" / "visibility_rules.py", SRC / "engine" / "economy.py",
                SRC / "llm" / "context_builder.py"}:
        return []
    return [
        getattr(node, "lineno", 0) for node in ast.walk(tree)
        if isinstance(node, (ast.BoolOp, ast.Compare, ast.comprehension))
        and {"contributors", "payer_uid"}.issubset({
            child.value for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        })
    ]


def test_proposal_visibility_is_not_reimplemented() -> None:
    violations = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for line in _duplicate_proposal_visibility_checks(path, tree):
            violations.append(f"{path.relative_to(ROOT)}:{line}: duplicate proposal visibility")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    "proposal.get('payer_uid') == uid or uid in proposal.get('contributors', [])",
    "[p for p in records if p.get('payer_uid') and p.get('contributors')]",
])
def test_proposal_visibility_guard_rejects_duplicate_checks(source: str) -> None:
    assert _duplicate_proposal_visibility_checks(SRC / "webui" / "routes" / "other.py", ast.parse(source))


def test_read_routes_use_participant_viewer_instead_of_legacy_gm_check() -> None:
    protected = {
        "api_detail", "api_game_adventure_projection", "api_log",
        "api_private_log", "api_table_talk", "_ruleset_requester_is_gm",
    }
    found = set()
    violations = []
    for path in sorted((SRC / "webui" / "routes").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in protected:
                continue
            found.add(node.name)
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")
                if name == "is_game_gm":
                    violations.append(f"{path.relative_to(ROOT)}:{call.lineno}: legacy read identity")
    assert found == protected
    assert not violations, "\n".join(violations)


def test_game_instance_top_level_field_count_does_not_grow() -> None:
    path = SRC / "engine" / "game_instance.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    instance = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "GameInstance")
    count = sum(isinstance(node, ast.AnnAssign) for node in instance.body)
    assert count <= MAX_TOP_LEVEL_FIELDS, f"GameInstance has {count} fields (maximum {MAX_TOP_LEVEL_FIELDS})"
