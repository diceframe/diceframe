"""Timed authoritative rituals (Magical Cunning) and their Pact Magic regain."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Mapping

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver


class RestError(ValueError):
    """Raised when a rest cannot legally be completed."""


class RestingRitualsMixin:
    """Timed rites that spend a declared use to regain Pact Magic slots."""

    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by ``Dnd2024RestEngine``; no runtime state.
        bundle: LoadedRulesetBundle
        rules: dict[str, Any]

        def sync_resources(self, payload: dict[str, Any]) -> dict[str, Any]: ...
        def _class_state(self, character: dict[str, Any]) -> tuple[str, str, int]: ...

        @staticmethod
        def _owns_spec(spec: dict[str, Any], character: dict[str, Any]) -> bool: ...
        @staticmethod
        def _require_conscious(character: dict[str, Any], rest_name: str) -> None: ...

    # ------------------------------------------------------------------
    # timed rituals (Magical Cunning)
    # ------------------------------------------------------------------

    def ritual_options(self, character: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        """What a timed ritual would give back right now, for the client to draw.

        Read-only, like ``rest_recovery_options``: it reports the declared
        minutes, the remaining uses, and the exact number of slots the rite
        would hand back, so the panel never has to re-derive ceil(max / 2).
        """

        payload = dict(character) if isinstance(character, Mapping) else {}
        try:
            character = self.sync_resources(payload)
            _class_ref, class_id, level = self._class_state(character)
        except RestError:
            return []
        class_state = character["resources"]["class"]
        pact = self._pact_slots(character)
        rows: list[dict[str, Any]] = []
        if pact is None:
            return rows
        slot_level, maximum, current = pact
        for spec in self.rules["class_resources"][class_id]:
            declaration = spec.get("ritual")
            if not isinstance(declaration, dict):
                continue
            if level < int(spec.get("minimum_level", 1) or 1):
                continue
            if not self._owns_spec(spec, character):
                continue
            state = class_state.get(spec["id"])
            if not isinstance(state, dict):
                continue
            full = self._ritual_full_budget(declaration, character)
            rows.append({
                "resource_id": str(spec["id"]),
                "minutes": int(declaration["minutes"]),
                "uses_left": int(state.get("current", 0) or 0),
                "slot_level": slot_level,
                "maximum": maximum,
                "expended": max(0, maximum - current),
                "regain": self._ritual_regain(declaration, maximum, current, full),
                "full_budget": full,
            })
        return rows

    def complete_ritual(
        self, payload: dict[str, Any], resource_id: str | None = None,
    ) -> dict[str, Any]:
        """Finish a timed rite: regain Pact Magic slots and spend the use.

        The caller is responsible for the other half of the transaction -- the
        minutes this returns have to move the world clock inside the same
        authoritative write -- so a failure on either side leaves no half-done
        rite behind.
        """

        character = self.sync_resources(payload)
        self._require_conscious(character, "Ritual")
        plan = self._plan_ritual(character, resource_id)
        class_magic = character.setdefault("spellcasting", {}).setdefault("class", {})
        before = deepcopy(class_magic.get("slots_current") or {})
        after = dict(before)
        after[plan["slot_level"]] = int(after.get(plan["slot_level"], 0) or 0) + int(
            plan["regain"]
        )
        class_magic["slots_current"] = after
        state = character["resources"]["class"][plan["resource_id"]]
        before_uses = int(state.get("current", 0) or 0)
        state["current"] = before_uses - 1
        events = [
            {
                "type": "recover_pact_slots",
                "resource_id": plan["resource_id"],
                "minutes": plan["minutes"],
                "full_budget": plan["full_budget"],
                "slots": {plan["slot_level"]: plan["regain"]},
                "before": before,
                "after": deepcopy(after),
            },
            {
                "type": "spend_resource_use",
                "resource_id": plan["resource_id"],
                "before": before_uses,
                "after": int(state["current"]),
            },
        ]
        return {
            "character": character,
            "events": events,
            "ritual": plan["resource_id"],
            "minutes": plan["minutes"],
            "source_ref": plan["source_ref"],
        }

    def _plan_ritual(
        self, character: dict[str, Any], resource_id: str | None,
    ) -> dict[str, Any]:
        """Check one rite completely before any slot or use moves."""

        _class_ref, class_id, level = self._class_state(character)
        class_state = character["resources"]["class"]
        declared = {
            str(spec["id"]): spec
            for spec in self.rules["class_resources"][class_id]
            if isinstance(spec.get("ritual"), dict)
        }
        wanted = str(resource_id or "").strip()
        if not wanted:
            # 不点名只在唯一候选时成立：多候选时替玩家挑一个属于自造选择。
            if len(declared) != 1:
                raise RestError("resource_id must name the ritual to perform")
            wanted = next(iter(declared))
        spec = declared.get(wanted)
        if spec is None or level < int(spec.get("minimum_level", 1) or 1):
            raise RestError(f"{wanted} is not a ritual option for this character")
        if not self._owns_spec(spec, character):
            raise RestError(f"{wanted} is not a ritual option for this character")
        state = class_state.get(wanted)
        if not isinstance(state, dict):
            raise RestError(f"{wanted} has no resource pool on this character")
        if int(state.get("current", 0) or 0) < 1:
            raise RestError(f"{wanted} cannot be used again until the next long rest")
        pact = self._pact_slots(character)
        if pact is None:
            raise RestError(f"{wanted} requires Pact Magic spell slots")
        slot_level, maximum, current = pact
        declaration = spec["ritual"]
        full = self._ritual_full_budget(declaration, character)
        regain = self._ritual_regain(declaration, maximum, current, full)
        if regain <= 0:
            # 每日一次的机会不该被一次「本来就没有槽可恢复」的仪式烧掉。
            raise RestError(f"{wanted} has no expended Pact Magic slots to regain")
        return {
            "resource_id": wanted,
            "minutes": int(declaration["minutes"]),
            "slot_level": slot_level,
            "regain": regain,
            "full_budget": full,
            "source_ref": str(spec["source_ref"]),
        }

    @staticmethod
    def _ritual_regain(
        declaration: Mapping[str, Any], maximum: int, current: int, full: bool,
    ) -> int:
        expended = max(0, int(maximum) - int(current))
        if full:
            return expended
        if declaration.get("budget") == "half_max_round_up":
            # 「不超过最大值一半（向上取整）」是槽的**个数**，不是环阶预算。
            return min((int(maximum) + 1) // 2, expended)
        return 0

    def _ritual_full_budget(
        self, declaration: Mapping[str, Any], character: dict[str, Any],
    ) -> bool:
        """Whether an owned feature upgrades this rite to a full refill.

        Eldritch Master does not grant a second use; it replaces the ceiling of
        the same Magical Cunning use, so the upgrade is declared on the rite and
        matched by feature id rather than by class level.
        """

        feature_id = str(declaration.get("full_feature") or "")
        if not feature_id:
            return False
        return Dnd2024ClassFeatureResolver(self.bundle).has_feature(character, feature_id)

    @staticmethod
    def _pact_slots(character: Mapping[str, Any]) -> tuple[str, int, int] | None:
        """``(slot level key, maximum, current)`` for a Pact Magic caster.

        Pact Magic keeps one slot level in the ordinary slot tables, so a
        character whose profile is not ``pact`` (or that somehow holds more
        than one row) has nothing this rite can restore.
        """

        class_magic = (character.get("spellcasting") or {}).get("class")
        if not isinstance(class_magic, Mapping):
            return None
        if str(class_magic.get("slot_profile") or "") != "pact":
            return None
        maximum = class_magic.get("slots_max") or {}
        current = class_magic.get("slots_current") or {}
        if not isinstance(maximum, Mapping) or len(maximum) != 1:
            return None
        key = next(iter(maximum))
        return str(key), int(maximum[key] or 0), int(current.get(str(key), 0) or 0)
