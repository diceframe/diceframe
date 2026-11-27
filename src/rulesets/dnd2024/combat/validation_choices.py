"""Turn-choice, check and self-restoration declaration checks.

One of the validation mixins: the engine assembles them into
``CombatValidationMixin``; the class holds no state of its own."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from .primitives import slot_available, CombatIntentError
from .check_windows import LUCKY_RESOURCE


class CombatValidationChoicesMixin:
    """Turn-choice, check and self-restoration declaration checks."""

    __slots__ = ()

    if TYPE_CHECKING:
        _feature_character: Any
        features: Any
        spells: Any
        ABILITY_CHECK_COSTS: Any
        _window_pool_available: Any
        _self_restoration_choice: Any
        catalog: Any

    @staticmethod
    def _choice_spent(choice: str, economy: dict[str, Any]) -> bool:
        """Whether this turn has already spent one of the three choices.

        Movement is answered from the ``moved`` marker the reducer writes when
        the current actor actually moves; a forced move of somebody else does
        not set it.  Action and Bonus Action are answered from the remaining
        budget.  A surge action is deliberately *not* folded in here: refusing
        the surge itself is a separate rule in ``_validate_turn_choice``, and
        counting it as "already acted" would blame the wrong thing.
        """

        if choice == "move":
            return bool(economy.get("moved"))
        if choice == "action":
            return int(economy.get("action", 0) or 0) < 1
        if choice == "bonus_action":
            return int(economy.get("bonus_action", 0) or 0) < 1
        return False

    def _intent_choice(
        self, intent: dict[str, Any], intent_type: str, actor: dict[str, Any],
    ) -> str:
        """Which of move / action / Bonus Action this intent would spend.

        Empty means "none of the three": a reaction, a decision, or ending the
        turn is not one of the choices the Abjure Foes Frightened restricts.
        """

        if intent_type == "move":
            return "move"
        if intent_type in {
            "attack", "multiattack", "save_action", "dash", "dodge", "disengage",
            "hide", "stabilize", "activate_item",
        }:
            return "action"
        if intent_type == "cast_spell":
            spell = self.spells.get(str(intent.get("spell_ref") or ""))
            if spell is None:
                return ""
            casting_time = str(spell["casting_time"])
            if casting_time.startswith("Bonus Action"):
                return "bonus_action"
            # 反应法术不占用三选一里的任何一项：斥退敌人不禁止反应。
            if casting_time.startswith("Reaction"):
                return ""
            return "action"
        if intent_type == "class_capability":
            capability = self.features.declared_capability(
                self._feature_character(actor),
                str(intent.get("capability_id") or ""),
            )
            if capability is None:
                return ""
            # ``cost.slot`` 是经济槽位的名字本身（action / bonus_action /
            # reaction），不是一个布尔。
            if capability.cost.slot == "bonus_action":
                return "bonus_action"
            return "action" if capability.cost.slot == "action" else ""
        return ""

    def _validate_ability_check(
        self, intent: dict[str, Any], combat: dict[str, Any],
    ) -> None:
        """A bare ability check: ability, DC and what it costs.

        The cost is *declared* rather than implied, because the same arithmetic
        serves a check paid with an action, one paid with a bonus action, and one
        that costs nothing at all (a contest, a failed-window consequence).
        """

        ability = str(intent.get("ability") or "")
        if ability not in {"str", "dex", "con", "int", "wis", "cha"}:
            raise CombatIntentError("ability check needs a canonical ability")
        dc = intent.get("dc")
        if isinstance(dc, bool) or not isinstance(dc, int) or not 1 <= dc <= 40:
            raise CombatIntentError("ability check DC must be 1..40")
        cost = str(intent.get("check_cost") or "action")
        if cost not in self.ABILITY_CHECK_COSTS:
            raise CombatIntentError("ability check cost is not supported")
        economy = combat.get("economy") or {}
        if cost != "none" and not slot_available(economy, cost):
            raise CombatIntentError(f"the {cost.replace('_', ' ')} has already been spent")

    def _validate_turn_choice(
        self, intent: dict[str, Any], intent_type: str, actor: dict[str, Any],
        economy: dict[str, Any], conditions: dict[str, Any],
    ) -> None:
        """Abjure Foes: one of move / action / Bonus Action per turn, no more.

        The restriction is data on the condition, so it applies to whatever
        the intent actually costs rather than to a list of banned intent types.
        Repeating the *same* choice is left to the ordinary economy: a second
        attack of the Attack action, or a second Bonus Action, is refused by
        the budget, not by this rule.

        One exception stays here rather than in the budget: Action Surge is the
        only thing that can hand out a second action after the first is spent,
        and the restriction exists precisely to cap the creature at one of the
        three for the turn, so a surge action is refused while the limit holds.
        """

        limit = self._turn_choice_limit(conditions)
        if not limit:
            return
        choice = self._intent_choice(intent, intent_type, actor)
        if choice not in limit:
            return
        if (
            choice == "action"
            and int(economy.get("action", 0) or 0) < 1
            and int(economy.get("surge_action", 0) or 0) > 0
        ):
            raise CombatIntentError(
                "your turn is limited to one of move, one action or one Bonus "
                "Action: Action Surge cannot buy another action"
            )
        spent = {
            name for name in limit
            if name != choice and self._choice_spent(name, economy)
        }
        if spent:
            labels = {
                "move": "move", "action": "take an action",
                "bonus_action": "take a Bonus Action",
            }
            used = ", ".join(sorted(labels.get(name, name) for name in spent))
            raise CombatIntentError(
                "your turn is limited to one of move, one action or one Bonus "
                f"Action: you already chose to {used}"
            )

    def _turn_choice_limit(self, conditions: dict[str, Any]) -> list[str]:
        """The three-way cap this condition set carries, if any."""

        for meta in conditions.values():
            if not isinstance(meta, dict):
                continue
            declared = meta.get("turn_choice_limit")
            if isinstance(declared, list):
                limit = [str(item) for item in declared if str(item)]
                if limit:
                    return limit
        return []

    def _validate_lucky_spend(
        self, instance: Any, intent: dict[str, Any], actor: dict[str, Any],
    ) -> None:
        """Declaring Lucky's advantage face requires an unspent point in hand.

        Ownership of the pool *is* the feat requirement (the resource is only
        synced for characters with ``feat:lucky``), so an absent pool is
        rejected exactly like an empty one.
        """

        spend_luck = intent.get("spend_luck")
        if spend_luck is None or spend_luck is False:
            return
        if spend_luck is not True:
            raise CombatIntentError("spend_luck must be a boolean")
        if not self._window_pool_available(
            instance, str(actor["actor_id"]), LUCKY_RESOURCE,
        ):
            raise CombatIntentError("lucky pool is empty")

    def _validate_self_restoration(
        self, actor: dict[str, Any], intent: dict[str, Any],
    ) -> None:
        """The turn-end removal is a choice, and a choice has to be legal.

        Absent means "not used": the engine never picks a condition for the
        player, and it never removes two.  A declared value must name one of
        the feature's own conditions *and* be one the actor actually has -- a
        stale client must not be able to delete a condition that is not there,
        nor one that arrived after the panel was rendered.
        """

        if "self_restoration_condition" not in intent:
            return
        requested = intent["self_restoration_condition"]
        if requested is None:
            return
        chosen = self._self_restoration_choice(actor)
        if chosen is None:
            raise CombatIntentError(
                "this actor has no feature that removes a condition at turn end"
            )
        feature_id, allowed = chosen
        if not isinstance(requested, str) or not requested:
            raise CombatIntentError("self_restoration_condition must be a condition id")
        if requested not in allowed:
            raise CombatIntentError(
                f"{feature_id} cannot remove the {requested} condition"
            )
        if requested not in (actor.get("conditions") or {}):
            raise CombatIntentError(f"the actor does not have the {requested} condition")

    def _restriction_sources(
        self, conditions: dict[str, Any], flag: str,
    ) -> list[str]:
        """Actor ids named by the conditions that declare ``flag``.

        A condition such as Frightened restricts behaviour *towards its own
        source*, so the restriction is data on the stored entry, not a branch
        on the condition id.  An entry without a recorded source cannot be
        scoped to anyone and is therefore not enforced -- blocking every
        direction would be a worse answer than a restriction that visibly does
        nothing, and the settlement paths always record the source.
        """

        sources: list[str] = []
        for condition_id, entry in self.catalog.conditions.items():
            if not entry.get(flag) or condition_id not in conditions:
                continue
            stored = conditions.get(condition_id)
            source = (
                str(stored.get("source_actor_id") or "")
                if isinstance(stored, dict) else ""
            )
            if source and source not in sources:
                sources.append(source)
        return sources
