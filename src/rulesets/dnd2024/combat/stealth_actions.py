"""Shared Hide eligibility and settlement for both action and capability intents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .primitives import CombatIntentError

if TYPE_CHECKING:
    from collections.abc import Callable


class CombatStealthActionsMixin:
    __slots__ = ()

    if TYPE_CHECKING:
        _actor_view: Callable[[Any, dict[str, Any], str], dict[str, Any]]
        _spend_action_resource: Callable[[dict[str, Any]], str]
        cover_level: Callable[[Any, dict[str, Any], str], str]
        obscurement_level: Callable[[dict[str, Any], str], str]

        def _skill_check_events(
            self, instance: Any, combat: dict[str, Any], actor: dict[str, Any], rng: Any,
            *, skill: str, dc: int, kind: str, events: list[dict[str, Any]],
            sources: list[dict[str, Any]] | None = None, intent_id: str = "",
        ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]: ...

    HIDE_COVER_LEVELS = ("three_quarters", "total")

    def hide_preconditions(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
    ) -> dict[str, Any]:
        """Whether this creature may attempt to Hide here, and why not.

        默认拒绝：SRD 把"能藏"写成一种**例外**（重度遮蔽、¾ 或 Total Cover），
        the rest of the world being open ground. 一个判定位，三处消费者共用
        （validation 拒绝、available_intents 投影、结算前复核）。
        """

        level = self.obscurement_level(combat, actor_id)
        if level == "heavily":
            return {"allowed": True, "source": "obscurement", "reason": ""}
        cover = self.cover_level(instance, combat, actor_id)
        if cover in self.HIDE_COVER_LEVELS:
            return {"allowed": True, "source": "cover", "reason": ""}
        return {
            "allowed": False, "source": "",
            "reason": "no heavy obscurement or three-quarters cover",
        }

    def _hide_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str, rng: Any,
        *, intent_id: str = "", spend_action: bool = True,
    ) -> list[dict[str, Any]]:
        """Resolve the Hide action: one Dexterity (Stealth) check.

        RAW 的 DC 由 DM 裁决、通常对抗被动察觉；战斗视图没有察觉熟练数据，
        因此以存活敌人的感知豁免调整值作为被动察觉的数据代理（DC = 10 + 最高
        值）。成功即施加 P1 的 ``invisible`` condition，攻击优劣势与终止全部由
        该 condition 的既有 ``attack_modes`` / ``ends_on`` 元数据驱动。
        """

        # 两个入口（基本动作与 ``cunning_hide`` 能力）共用这一处复核：默认拒绝，
        # 只有重度遮蔽或 ¾/Total 掩护才允许尝试。
        gate = self.hide_preconditions(instance, combat, actor_id)
        if not gate["allowed"]:
            raise CombatIntentError(str(gate["reason"]))
        actor = self._actor_view(instance, combat, actor_id)
        events: list[dict[str, Any]] = []
        if spend_action:
            events.append({
                "type": "dnd2024.action.spent", "actor_id": actor_id,
                "resource": self._spend_action_resource(combat), "amount": 1,
            })
        alive_wisdom = [
            int((self._actor_view(instance, combat, f"enemy:{enemy_id}")["saving_throws"]
                or {}).get("wis", 0) or 0)
            for enemy_id, enemy in (combat.get("enemies") or {}).items()
            if int(enemy.get("hp", 0) or 0) > 0
        ]
        dc = 10 + max(alive_wisdom, default=0)
        sources: list[dict[str, Any]] = []
        if bool((actor.get("armor_training") or {}).get("stealth_disadvantage")):
            sources.append({"code": "stealth_disadvantage", "mode": "disadvantage"})
        events, check = self._skill_check_events(
            instance, combat, actor, rng,
            skill="stealth", dc=dc, kind="hide", events=events, sources=sources,
            intent_id=intent_id,
        )
        if check is None:
            return events
        events.append(check)
        if check["success"]:
            events.append({
                "type": "condition.applied", "target_id": actor_id,
                "condition": "invisible", "duration": "combat",
                "source_actor_id": actor_id,
                "ends_on": ["attack_roll", "spell_cast"],
            })
        return events

    def has_moved_this_turn(
        self, combat: dict[str, Any], actor_id: str,
    ) -> bool:
        """Whether the *current* actor has spent movement this turn.

        ``economy.moved`` is written by the reducer only when the acting
        creature pays movement, so a target shoved across the field does not
        count as having moved -- which is what Steady Aim's wording means.
        """

        economy = combat.get("economy") or {}
        if str(economy.get("actor_id") or "") != str(actor_id):
            return False
        return bool(economy.get("moved"))
