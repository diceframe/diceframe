"""A created real spell slot can be exchanged exactly once, without casting."""
from copy import deepcopy

import pytest

from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from test_dnd2024_font_of_magic import (
    _sorcerer, _combat, _commit, _capability, _font, _points, _slots, _economy,
)


@pytest.mark.parametrize("ordinary", [0, 2])
def test_created_slot_converts_back_without_spending_an_ordinary_slot(ordinary):
    runtime = Dnd2024Runtime()
    sheet = _sorcerer(runtime, 5, points=5)
    sheet["ruleset_character"]["spellcasting"]["class"]["slots_current"]["1"] = ordinary
    engine, instance = _combat(runtime, sheet)
    _commit(engine, instance, _capability(engine, instance, "font_create_slot", 1))
    assert _font(instance) == {"1": 1} and _points(instance) == 3
    events = _commit(engine, instance, _capability(engine, instance, "font_slot_to_points", 1))
    spends = [e for e in events if e["type"].endswith("slot_spent")]
    assert len(spends) == 1 and spends[0]["type"] == "dnd2024.spell.font_slot_spent"
    assert spends[0]["exchange"] is True
    assert _font(instance) == {} and _points(instance) == 4
    assert _slots(instance)["1"] == ordinary
    assert not _economy(instance).get("slot_spell_cast")
    if not ordinary:
        before = deepcopy(instance.to_dict())
        assert not _capability(engine, instance, "font_slot_to_points", 1)["ok"]
        assert instance.to_dict() == before


def test_overflow_refuses_temporary_slot_without_consumption():
    runtime = Dnd2024Runtime()
    sheet = _sorcerer(runtime, 5, points=5)
    sheet["ruleset_character"]["spellcasting"]["class"]["font_slots"] = {"1": 1}
    engine, instance = _combat(runtime, sheet)
    before = deepcopy(instance.to_dict())
    assert not _capability(engine, instance, "font_slot_to_points", 1)["ok"]
    assert instance.to_dict() == before
