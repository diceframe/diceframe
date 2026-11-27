"""Movement, escape and stand-up declaration checks.

One of the validation mixins: the engine assembles them into
``CombatValidationMixin``; the class holds no state of its own."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from .primitives import slot_available, stand_up_declaration, CombatIntentError


class CombatValidationMovementMixin:
    """Movement, escape and stand-up declaration checks."""

    __slots__ = ()

    if TYPE_CHECKING:
        _actor_view: Any
        _declared_action: Any
        catalog: Any

    def _validate_save_action_intent(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """S0-MM3c: name an action the creature has and still may use.

        Availability is checked here rather than during resolution because a
        spent per-day action or a recharge that has not come back must refuse
        the whole intent — and must not consume the creature's action either.
        """

        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        if str(target.get("side") or "") == str(actor.get("side") or "") or target["hp"] <= 0:
            raise CombatIntentError("save action target must be a living hostile actor")
        action = self._declared_action(actor, intent)
        if action is None or str(action.get("kind") or "") != "save_action":
            raise CombatIntentError("save action profile is not available to the actor")
        usage = self._action_usage(actor, str(action["id"]))
        if not usage["available"]:
            raise CombatIntentError("this action is not available yet")
        if set(intent) - {
            "intent_id", "type", "expected_version", "submitted_by", "actor_id",
            "target_id", "action_id",
        }:
            raise CombatIntentError("save action intent has unknown field(s)")

    @staticmethod
    def _action_usage(actor: dict[str, Any], action_id: str) -> dict[str, Any]:
        """Whether a stat-block action can be used right now, and why not.

        Absence of state means "never used": a fresh combat starts with every
        recharge available and every per-day budget whole.
        """

        stored = (actor.get("usage") or {}).get(action_id)
        stored = stored if isinstance(stored, dict) else {}
        return {
            "available": bool(stored.get("available", True)),
            "used": int(stored.get("used", 0) or 0),
        }

    def _validate_escape(
        self, intent: dict[str, Any], actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        """S0-MM3e: escape a condition whose DC and shape are already on record.

        The intent names a condition and an ability; the DC, the legal checks
        and the cost all come from state.  A caller cannot declare a DC — that
        is the whole point of reading it from the applied instance.
        """

        if set(intent) - {
            "intent_id", "type", "expected_version", "submitted_by", "actor_id",
            "condition", "check",
        }:
            raise CombatIntentError("escape intent has unknown field(s)")
        condition_id = str(intent.get("condition") or "")
        declared = self.catalog.conditions.get(condition_id) or {}
        escape = declared.get("escape")
        if not isinstance(escape, dict):
            raise CombatIntentError("this condition cannot be escaped")
        carried = (actor.get("conditions") or {}).get(condition_id)
        if not isinstance(carried, dict):
            raise CombatIntentError("the actor does not have that condition")
        dc = carried.get("escape_dc")
        if isinstance(dc, bool) or not isinstance(dc, int) or dc <= 0:
            # 施加时没有写 DC 的条件无法挣：不替它编一个数。
            raise CombatIntentError("this application declares no escape DC")
        chosen = str(intent.get("check") or "")
        allowed = {
            str(row.get("ability")): (str(row.get("skill") or "") or None)
            for row in escape.get("checks") or []
            if isinstance(row, dict)
        }
        if chosen not in allowed:
            raise CombatIntentError("escape check must be one of the declared abilities")
        cost = str(escape.get("cost") or "action")
        if not slot_available(economy, cost):
            raise CombatIntentError(f"escaping costs a {cost.replace('_', ' ')}")

    def _validate_stand_up(
        self, intent: dict[str, Any], actor: dict[str, Any], economy: dict[str, Any],
    ) -> None:
        """Standing up is movement, not an action: half the walking speed."""

        if set(intent) - {
            "intent_id", "type", "expected_version", "submitted_by", "actor_id",
        }:
            raise CombatIntentError("stand up intent has unknown field(s)")
        declared = stand_up_declaration(self.catalog.conditions, actor.get("conditions") or {})
        if declared is None:
            raise CombatIntentError("the actor is not in a condition one can stand up from")
        # ``or`` would treat a real speed of 0 as "absent" and fall back to the
        # printed speed — a grappled (speed 0) creature would then be quoted the
        # price of a stand-up it cannot pay for.
        current = economy.get("speed")
        cost = self.stand_up_cost(
            actor, declared,
            speed=int(current) if isinstance(current, int) else None,
        )
        if int(economy.get("movement", 0) or 0) < cost:
            raise CombatIntentError("not enough movement left to stand up")

    @staticmethod
    def stand_up_cost(
        actor: dict[str, Any], declared: dict[str, Any], *, speed: int | None = None,
    ) -> int:
        """Half the walking speed, rounded down (2024: "spend half your Speed").

        The *effective* speed is what the movement budget already uses, so a
        creature whose speed is halved pays half of the halved value — reading
        the printed speed instead would let it buy the same stand-up twice.
        """

        base = max(0, int(actor.get("speed", 0) or 0)) if speed is None else max(0, int(speed))
        fraction = max(1, int(declared.get("fraction") or 2))
        return base // fraction
