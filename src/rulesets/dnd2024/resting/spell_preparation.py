"""Rest-time prepared spell changes: parse the swap, then apply it to the sheet."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.spells.preparation import PreparationPolicy
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection

from .rituals import RestError


def _canonical(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("ruleset_character")
    return nested if isinstance(nested, dict) else payload


class RestingSpellPreparationMixin:
    """Validate and apply rest-time prepared spell and cantrip changes."""

    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by ``Dnd2024RestEngine``; no runtime state.
        bundle: LoadedRulesetBundle

        def _class_state(self, character: dict[str, Any]) -> tuple[str, str, int]: ...

    def parse_prepared_spells(
        self, payload: dict[str, Any], request: Any, context: str = "long_rest",
    ) -> dict[str, list[str]]:
        """Validate a rest preparation change without mutating anything.

        One budget per rest: the class policy decides whether the change is
        legal at all (a Bard swaps spells on level-up, not on a rest; a Paladin
        may replace one per Long Rest), and its Short Rest counterpart is the
        feature-declared budget (Memorize Spell).  Either way the ordinary spell
        selection rules apply with the *rest* budget -- never the level-up cap.
        """

        if context not in {"long_rest", "short_rest"}:
            raise RestError(f"unknown preparation context {context}")
        character = _canonical(payload)
        class_ref, _class_id, level = self._class_state(character)
        policy = PreparationPolicy.for_class(self.bundle, class_ref)
        if context == "long_rest":
            if policy is None or not policy.can_change_on_long_rest():
                raise RestError(
                    "this class cannot change prepared spells on a long rest"
                )
        else:
            # 短休换准备不是职业规则，而是特性预算；没有给出预算的职业
            # 一律拒绝，和长休的 "cannot change" 是同一种拒绝。
            selection = Dnd2024SpellSelection(self.bundle)
            if selection.short_rest_replace_budget(character) < 1:
                raise RestError(
                    "this character cannot change prepared spells on a short rest"
                )
        if not isinstance(request, dict):
            raise RestError("prepared_spells must be an object with prepared_spell_refs")
        raw_prepared = request.get("prepared_spell_refs")
        if not isinstance(raw_prepared, list) or any(
            not isinstance(ref, str) for ref in raw_prepared
        ):
            raise RestError("prepared_spells must be an object with prepared_spell_refs")
        block = character.get("spellcasting", {}).get("class")
        block = block if isinstance(block, dict) else {}
        current = {
            "cantrip_refs": list(block.get("cantrip_refs") or []),
            "prepared_spell_refs": list(block.get("prepared_spell_refs") or []),
            "spellbook_refs": list(block.get("spellbook_refs") or []),
        }
        raw_cantrips = request.get("cantrip_refs")
        if raw_cantrips is not None and (
            not isinstance(raw_cantrips, list)
            or any(not isinstance(ref, str) for ref in raw_cantrips)
        ):
            raise RestError("prepared_spells cantrip_refs must be an array of spell refs")
        parsed, errors = Dnd2024SpellSelection(self.bundle).validate(
            class_ref,
            level,
            {
                "prepared_spell_refs": raw_prepared,
                "cantrip_refs": (
                    raw_cantrips if raw_cantrips is not None else current["cantrip_refs"]
                ),
                "spellbook_refs": current["spellbook_refs"],
            },
            previous=current,
            context=context,
            character=character,
        )
        if errors:
            raise RestError("; ".join(errors))
        if (
            set(parsed["prepared_spell_refs"]) == set(current["prepared_spell_refs"])
            and set(parsed["cantrip_refs"]) == set(current["cantrip_refs"])
        ):
            # 填不出改动的请求是调用方错误，不是一次成功的休息（同 S0-G3
            # 对空补齐的处理）。
            raise RestError("prepared_spells would not change anything")
        change = {"prepared_spell_refs": list(parsed["prepared_spell_refs"])}
        if raw_cantrips is not None:
            change["cantrip_refs"] = list(parsed["cantrip_refs"])
        return change

    @staticmethod
    def _apply_prepared_spells(
        character: dict[str, Any], change: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        block = character.setdefault("spellcasting", {}).get("class")
        if not isinstance(block, dict):
            raise RestError("character spellcasting block is missing")
        before = list(block.get("prepared_spell_refs") or [])
        after = list(change["prepared_spell_refs"])
        block["prepared_spell_refs"] = after
        if "cantrip_refs" in change:
            block["cantrip_refs"] = list(change["cantrip_refs"])
        return [{"type": "prepared_spells_changed", "before": before, "after": after}]
