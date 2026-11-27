"""Loading repairs a display HP layer that diverged from canonical ruleset health."""

from __future__ import annotations

from copy import deepcopy

from src.compat.characters import repair_ruleset_health_display
from src.engine.game_instance import GameInstance


def _canonical(hp: int, conditions: dict | None = None) -> dict:
    return {
        "resources": {"hp": hp, "max_hp": 10},
        "conditions": conditions if conditions is not None else {},
    }


def _diverged_sheet(canonical: dict) -> dict:
    return {
        "hp": 10,
        "max_hp": 10,
        "resources": {"hp": {"label": "生命", "current": 10, "max": 10, "min": 0}},
        "status": None,
        "deceased": False,
        "death_round": 4,
        "ruleset_character": deepcopy(canonical),
    }


def _instance_with_sheet(sheet: dict) -> GameInstance:
    instance = GameInstance(game_key=("web", "health-repair", "bot"), rule_id="dnd2024_srd")
    instance.players["a"] = {
        "character_name": "尤落",
        "control": {"mode": "human", "revision": 0},
        "character_sheet": sheet,
    }
    return instance


def test_repair_syncs_display_to_zero_hp_canonical() -> None:
    canonical = _canonical(0, {"unconscious": {}, "death_saves": {"successes": 0, "failures": 1}})
    sheet = _diverged_sheet(canonical)

    repair_ruleset_health_display(sheet)

    assert sheet["hp"] == 0 and sheet["max_hp"] == 10
    assert sheet["resources"]["hp"] == {"label": "生命", "current": 0, "max": 10, "min": 0}
    assert sheet["deceased"] is False
    assert sheet["status"] == "downed"
    assert sheet["death_saves"] == {"successes": 0, "failures": 1}
    assert "death_round" not in sheet
    assert sheet["ruleset_character"] == canonical


def test_repair_keeps_dead_character_dead_without_reviving() -> None:
    canonical = _canonical(0, {"dead": {"source": "death_saves"}})
    sheet = _diverged_sheet(canonical)

    repair_ruleset_health_display(sheet)

    assert sheet["deceased"] is True
    assert sheet["status"] == "dead"
    assert sheet["hp"] == 0
    assert sheet["death_round"] == 4
    assert "death_saves" not in sheet
    assert sheet["ruleset_character"] == canonical


def test_repair_leaves_legacy_sheet_untouched() -> None:
    sheet = {
        "hp": 10,
        "max_hp": 10,
        "status": "downed",
        "death_saves": {"success": 1, "failure": 0},
    }
    original = deepcopy(sheet)

    result = repair_ruleset_health_display(sheet)

    assert result is sheet
    assert sheet == original


def test_repair_requires_int_canonical_hp() -> None:
    sheet = _diverged_sheet({"resources": {"hp": {"current": 0}}, "conditions": {}})
    original = deepcopy(sheet)

    repair_ruleset_health_display(sheet)

    assert sheet == original


def test_repair_ignores_bool_canonical_hp() -> None:
    for bad_hp in (True, False):
        sheet = _diverged_sheet({"resources": {"hp": bad_hp, "max_hp": 10}, "conditions": {}})
        original = deepcopy(sheet)

        repair_ruleset_health_display(sheet)

        assert sheet == original


def test_repair_ignores_malformed_canonical_max_hp() -> None:
    for bad_max_hp in ("bad", True, False, {"max": 10}):
        sheet = _diverged_sheet({"resources": {"hp": 4, "max_hp": bad_max_hp}, "conditions": {}})
        original = deepcopy(sheet)

        repair_ruleset_health_display(sheet)

        assert sheet == original


def test_repair_marks_negative_canonical_hp_downed() -> None:
    canonical = _canonical(-3)
    sheet = _diverged_sheet(canonical)

    repair_ruleset_health_display(sheet)

    assert sheet["hp"] == -3
    assert sheet["status"] == "downed"
    assert sheet["deceased"] is False


def test_loading_repairs_diverged_save_display_from_canonical() -> None:
    canonical = _canonical(0, {"unconscious": {}, "death_saves": {"successes": 0, "failures": 2}})
    instance = _instance_with_sheet(_diverged_sheet(canonical))

    loaded = GameInstance.from_dict(instance.to_dict())

    sheet = loaded.get_character_sheet("a")
    assert sheet["hp"] == 0
    assert sheet["max_hp"] == 10
    assert sheet["resources"]["hp"]["current"] == 0
    assert sheet["resources"]["hp"]["max"] == 10
    assert sheet["status"] == "downed"
    assert sheet["deceased"] is False
    assert sheet["death_saves"] == {"successes": 0, "failures": 2}
    assert sheet["ruleset_character"] == canonical


def test_loading_leaves_legacy_sheet_without_canonical_untouched() -> None:
    sheet = {
        "hp": 10,
        "max_hp": 10,
        "status": "downed",
        "death_saves": {"success": 1, "failure": 0},
        "race": "人类",
        "class": "冒险者",
    }
    original = deepcopy(sheet)
    instance = _instance_with_sheet(sheet)

    loaded = GameInstance.from_dict(instance.to_dict())

    assert loaded.get_character_sheet("a") == original


def test_health_display_repair_is_idempotent_across_round_trip() -> None:
    canonical = _canonical(0, {"death_saves": {"successes": 1, "failures": 0}})
    instance = _instance_with_sheet(_diverged_sheet(canonical))

    first = GameInstance.from_dict(instance.to_dict())
    second = GameInstance.from_dict(first.to_dict())

    assert first.get_character_sheet("a") == second.get_character_sheet("a")
