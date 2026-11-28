"""Condition projection, immunity, and expiry for combatants."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from .check_modes import condition_flags
from .primitives import (
    PERSISTENT_RAGE_DURATION,
    RAGE_MAX_ROUNDS,
    TURNS_SCOPED_DURATIONS,
    WORLD_CLOCK_DURATIONS,
    CombatIntentError,
    actor_kind as _actor_kind,
    companion_actor as _companion_actor,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
)

if TYPE_CHECKING:
    from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
    from src.rulesets.dnd2024.combat.catalog import Dnd2024CombatCatalog


class CombatConditionsMixin:
    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by the combat engine and its other mixins; no runtime state.
        def _actor_view(self, instance: Any, combat: dict[str, Any], actor_id: str) -> dict[str, Any]: ...
        def _combat_actor_ids(
            self, instance: Any, combat: dict[str, Any],
        ) -> list[str]: ...
        @staticmethod
        def _conditions(
            snapshot: dict[str, Any], combat: dict[str, Any], actor_id: str,
        ) -> dict[str, Any]: ...
        def _distance(self, combat: dict[str, Any], first: str, second: str) -> int: ...
        @staticmethod
        def _feature_character(actor: dict[str, Any]) -> dict[str, Any]: ...
        def _logical_minutes(self, instance: Any) -> int: ...
        def _projected_auras(
            self, instance: Any, combat: dict[str, Any],
        ) -> list[dict[str, Any]]: ...
        def _wears_heavy_armor(self, snapshot: dict[str, Any], actor_id: str) -> bool: ...
        catalog: Dnd2024CombatCatalog
        features: Dnd2024ClassFeatureResolver

    def _statblock_immunity_events(
        self, combat: dict[str, Any], events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Turn an immune ``condition.applied`` into an auditable block.

        The check sits at the end of one intent rather than at each of the five
        places that apply conditions: immunity is a property of the *target*,
        so every producer gets the same answer for free.  The block is a
        separate, state-free event -- dropping the event silently would leave
        the ledger claiming a condition the state never took.
        """

        immunities: dict[str, set[str]] = {}
        for enemy_id, enemy in (combat.get("enemies") or {}).items():
            codes = enemy.get("condition_immunities") or ()
            if codes:
                immunities[f"enemy:{enemy_id}"] = {str(code) for code in codes}
        if not immunities:
            return events
        filtered: list[dict[str, Any]] = []
        for event in events:
            if event.get("type") == "condition.applied":
                blocked = immunities.get(str(event.get("target_id") or ""))
                if blocked and str(event.get("condition") or "") in blocked:
                    filtered.append({
                        "type": "condition.blocked",
                        "target_id": str(event["target_id"]),
                        "condition": str(event["condition"]),
                        "reason": "immune",
                        "source_actor_id": str(event.get("source_actor_id") or ""),
                    })
                    continue
            filtered.append(event)
        return filtered

    def _feature_immunity_events(
        self, instance: Any, combat: dict[str, Any], events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Turn an immune ``condition.applied`` into a block, for party actors too.

        ``_statblock_immunity_events`` covers enemies because their immunity is
        data.  A player's immunity is a *feature* (Nature's Ward, Mindless Rage),
        and those producers are many -- spells, riders, terrain.  Checking here,
        once per intent, is the same trade the statblock branch already made:
        immunity belongs to the target, so every producer inherits the answer.
        """

        if not any(
            isinstance(event, dict) and event.get("type") == "condition.applied"
            for event in events
        ):
            return events
        filtered: list[dict[str, Any]] = []
        for event in events:
            if event.get("type") == "condition.applied":
                target_id = str(event.get("target_id") or "")
                kind, raw_id = _actor_kind(target_id)
                if kind in {"player", "companion"}:
                    try:
                        target = self._actor_view(instance, combat, target_id)
                    except CombatIntentError:  # pragma: no cover - left the fight
                        target = None
                    if target is not None:
                        blocker = self._condition_immunity_blocker(
                            instance, combat, target, str(event.get("condition") or ""),
                        )
                        if blocker is not None:
                            filtered.append({
                                "type": "condition.blocked",
                                "target_id": target_id,
                                "condition": str(event.get("condition") or ""),
                                "reason": "immune",
                                "blocked_by": str(blocker.get("feature_id") or ""),
                                "source_actor_id": str(event.get("source_actor_id") or ""),
                            })
                            continue
            filtered.append(event)
        return filtered

    def _incapacitation_cleanup_events(
        self, instance: Any, combat: dict[str, Any], events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """End effects whose own text says the Incapacitated condition ends them.

        Superior Defense lasts "1 minute or until you have the Incapacitated
        condition", and the condition can arrive from a Stunning Strike, a spell
        or the environment.  Resolving it where the condition is *gained* would
        mean a branch per source; resolving it once per batch from the merged
        condition set means a new source needs no change here at all.

        The metadata flag is on the effect, not on the class, so nothing in this
        method names a feature.
        """

        applied: dict[str, set[str]] = {}
        for event in events:
            if event.get("type") != "condition.applied":
                continue
            target_id = str(event.get("target_id") or "")
            if target_id:
                applied.setdefault(target_id, set()).add(
                    str(event.get("condition") or "")
                )
        cleanup: list[dict[str, Any]] = []
        for actor_id, gained in applied.items():
            try:
                view = self._actor_view(instance, combat, actor_id)
            except CombatIntentError:  # pragma: no cover - target came from an event
                continue
            conditions = dict(view.get("conditions") or {})
            # 本批的新条件还没落地，必须并进来才看得到"刚刚被失能"这件事。
            for condition_id in gained:
                conditions.setdefault(condition_id, {})
            if not condition_flags(self.catalog.conditions, conditions)["incapacitated"]:
                continue
            for condition_id, meta in (view.get("conditions") or {}).items():
                if condition_id in gained:
                    continue
                if not isinstance(meta, dict) or not meta.get("ends_on_incapacitated"):
                    continue
                cleanup.append({
                    "type": "condition.removed", "target_id": actor_id,
                    "condition": condition_id, "reason": "incapacitated",
                })
        return cleanup

    def _source_incapacitation_events(
        self, instance: Any, combat: dict[str, Any], events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Effects that end when their *source* becomes Incapacitated or dies.

        Turn Undead is written that way: "This effect ends early on the
        creature if it takes any damage, if you have the Incapacitated
        condition, or if you die".  The ender is therefore not the bearer's
        own state but the caster's, and the bearer may be any actor on the
        table -- so the sweep runs once per batch over every condition that
        names a source, instead of a branch inside the caster's damage path.
        """

        lost: set[str] = set()
        for event in events:
            if event.get("type") != "condition.applied":
                continue
            target_id = str(event.get("target_id") or "")
            if target_id:
                lost.add(target_id)
        cleanup: list[dict[str, Any]] = []
        for source_id in lost:
            try:
                view = self._actor_view(instance, combat, source_id)
            except CombatIntentError:  # pragma: no cover - source came from an event
                continue
            conditions = dict(view.get("conditions") or {})
            for event in events:
                if (
                    event.get("type") == "condition.applied"
                    and str(event.get("target_id") or "") == source_id
                ):
                    conditions.setdefault(str(event.get("condition") or ""), {})
            flags = condition_flags(self.catalog.conditions, conditions)
            if not (flags["incapacitated"] or "dead" in conditions):
                continue
            for other_id in self._combat_actor_ids(instance, combat):
                if other_id == source_id:
                    continue
                try:
                    other = self._actor_view(instance, combat, other_id)
                except CombatIntentError:  # pragma: no cover - actor came from combat
                    continue
                for condition_id, meta in (other.get("conditions") or {}).items():
                    if not isinstance(meta, dict):
                        continue
                    if not meta.get("ends_on_source_incapacitated"):
                        continue
                    if str(meta.get("source_actor_id") or "") != source_id:
                        continue
                    cleanup.append({
                        "type": "condition.removed", "target_id": other_id,
                        "condition": condition_id, "reason": "source incapacitated",
                    })
        return cleanup

    def _condition_implies_incapacitated(self, condition_id: str) -> bool:
        row = (self.catalog.conditions or {}).get(condition_id) or {}
        return bool(row.get("implies_incapacitated"))

    def _timed_condition_expiry_events(
        self, instance: Any, combat: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """End effects that run on the world clock rather than on turns.

        Divine Sense and the Sacred Weapon bond both run for ten minutes and
        are not tied to a fight; a combat that ends before the clock passes
        must leave them in place, and a clock that passes outside combat must
        still end them.  Every intent sweeps the same limit, so no second timer
        exists to disagree with the ledger.
        """

        now = self._logical_minutes(instance)
        events: list[dict[str, Any]] = []
        for actor_id in self._combat_actor_ids(instance, combat):
            try:
                view = self._actor_view(instance, combat, actor_id)
            except CombatIntentError:  # pragma: no cover - actor came from combat
                continue
            for condition_id, meta in (view.get("conditions") or {}).items():
                if not isinstance(meta, dict):
                    continue
                if meta.get("duration") not in WORLD_CLOCK_DURATIONS:
                    continue
                expires_at = meta.get("expires_at_minute")
                if isinstance(expires_at, bool) or not isinstance(expires_at, int):
                    continue
                if now >= expires_at:
                    events.append({
                        "type": "condition.removed", "target_id": actor_id,
                        "condition": condition_id, "reason": "ten minutes passed",
                    })
        return events

    def _passive_condition_immunity(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        condition_id: str,
    ) -> dict[str, str] | None:
        """The feature the target owns that makes ``condition_id`` a no-op.

        Unlike an aura this needs no projection and no distance: the feature is
        on the target, so the only question is whether its gate is met.  The
        gate is a condition id (Mindless Rage is only on while Rage is on), and
        it is read from the *effective* condition set -- a condition that an
        aura already suppressed cannot switch a second immunity on.
        """

        wanted = str(condition_id or "")
        target_id = str(target.get("actor_id") or "")
        if not wanted or not target_id or str(target.get("side") or "") != "party":
            return None
        character = self._feature_character(target)
        if not character:
            return None
        conditions = self._effective_conditions(instance, combat, target)
        for definition in self.features.active_features_for(character):
            declared = definition.parameters.get("immune_conditions")
            if not isinstance(declared, list) or wanted not in declared:
                continue
            gate = definition.parameters.get("immune_while_condition")
            if isinstance(gate, str) and gate and gate not in conditions:
                continue
            return {
                "feature_id": definition.id,
                "owner": definition.owner,
                "source_actor_id": target_id,
            }
        return None

    def _condition_immunity_blocker(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        condition_id: str,
    ) -> dict[str, str] | None:
        """Why ``condition_id`` cannot affect the target at all right now.

        One question, two shapes of answer: an emanation the target stands in,
        or a feature the target carries.  Both are reported as the same record
        so the settlement path has exactly one branch, and ``scope`` says which
        kind it was -- an interception whose reason cannot be read is not an
        audit trail.
        """

        aura = self._aura_condition_immunity_source(
            instance, combat, target, condition_id,
        )
        if aura is not None:
            return {**aura, "scope": "aura"}
        passive = self._passive_condition_immunity(
            instance, combat, target, condition_id,
        )
        if passive is not None:
            return {**passive, "scope": "feature"}
        return None

    def _suppressed_condition_codes(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
    ) -> frozenset[str]:
        """Conditions the actor still carries but that have no effect now.

        Only suppression by an aura is modelled, and only for the condition
        ids some declaration actually immunizes -- asking the aura machinery
        about a condition nothing can intercept would cost a projection per
        condition for an answer that is always "no".
        """

        if str(actor.get("side") or "") != "party":
            return frozenset()
        declared = {
            code
            for definition in self.features.catalog.features_by_key.values()
            for code in (definition.parameters.get("aura_condition_immunities") or [])
            if isinstance(code, str)
        }
        present = declared & set((actor.get("conditions") or {}).keys())
        if not present:
            return frozenset()
        return frozenset(
            code for code in present
            if self._aura_condition_immunity_source(instance, combat, actor, code) is not None
        )

    def _effective_conditions(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
    ) -> dict[str, Any]:
        """The condition set the mechanics should read.

        Canonical state keeps every entry, its source and its duration: an aura
        does not delete a condition, it makes it inert while the creature
        stands inside.  This view is the other half of that sentence, and it is
        what ``condition_flags``, attack modes, speed and intent validation
        must consult so that "suppressed" means the same thing everywhere.
        """

        conditions = actor.get("conditions") or {}
        suppressed = self._suppressed_condition_codes(instance, combat, actor)
        if not suppressed:
            return conditions
        return {
            code: value for code, value in conditions.items() if code not in suppressed
        }

    def _effective_target(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
    ) -> dict[str, Any]:
        """A shallow copy of ``actor`` whose ``conditions`` are the effective set.

        Pure helpers such as ``attack_sources`` take a view and read
        ``conditions`` from it; giving them this copy is cheaper and less
        invasive than threading a second condition argument through every call.
        """

        suppressed = self._suppressed_condition_codes(instance, combat, actor)
        if not suppressed:
            return actor
        return {**actor, "conditions": self._effective_conditions(instance, combat, actor)}

    def _aura_condition_immunity_source(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        condition_id: str,
    ) -> dict[str, str] | None:
        """The aura that makes ``condition_id`` a no-op for the target now.

        A condition immunity rides on a base aura named by
        ``aura_source_feature_id``, so it uses that aura's range and dies with
        it when the holder is Incapacitated.  It is not an independent
        emanation, which is exactly why the old free-standing ``aura_range``
        declaration is gone.

        The catalog is consulted first: if no declaration lists ``condition_id``
        anywhere, nothing can intercept it and projecting the auras would be
        wasted work.  That early exit is what makes it affordable to ask this
        question on every condition-applying path rather than only on saves.

        The first qualifying source in catalog order is reported.  Two Devotion
        paladins therefore produce one interception with one stable source
        rather than two stacked settlements.
        """

        wanted = str(condition_id or "")
        target_id = str(target.get("actor_id") or "")
        if not wanted or not target_id or str(target.get("side") or "") != "party":
            return None
        declared = {
            code
            for definition in self.features.catalog.features_by_key.values()
            for code in (definition.parameters.get("aura_condition_immunities") or [])
            if isinstance(code, str)
        }
        if wanted not in declared:
            return None
        for row in self._projected_auras(instance, combat):
            if self._distance(combat, row["holder_id"], target_id) > row["range_ft"]:
                continue
            for extension in row["owned"]:
                if extension.parameters.get("aura_source_feature_id") != row["definition"].id:
                    continue
                immune = extension.parameters.get("aura_condition_immunities")
                if not isinstance(immune, list) or wanted not in immune:
                    continue
                return {
                    "feature_id": extension.id,
                    "owner": extension.owner,
                    "source_actor_id": row["holder_id"],
                }
        return None

    def _remove_concentration_conditions(
        self, snapshot: dict[str, Any], combat: dict[str, Any], owner: str,
    ) -> None:
        actor_ids = [
            *(_player_actor(uid) for uid in snapshot["characters"]),
            *(
                _companion_actor(companion_id)
                for companion_id in (snapshot["ruleset_state"].get("party", {}) or {}).get(
                    "companions", {},
                )
            ),
            *(_enemy_actor(enemy_id) for enemy_id in combat.get("enemies", {})),
        ]
        for actor_id in actor_ids:
            conditions = self._conditions(snapshot, combat, actor_id)
            for condition_id in [
                key for key, value in conditions.items()
                if isinstance(value, dict) and value.get("concentration_owner") == owner
            ]:
                conditions.pop(condition_id, None)

    def _expire_attack_marks(
        self, combat: dict[str, Any], actor_id: str,
    ) -> None:
        """Age the accepting actor's own attack marks at the start of their turn.

        Studied Attacks lasts "before the end of your next turn", which is one
        turn longer than a turn-start expiry: the mark must survive the whole
        of the attacker's next turn.  A ``turns_remaining`` counter therefore
        counts *turn starts still to come* -- created at 1, it drops to 0 as
        that turn begins (and stays usable), and is removed when the following
        turn begins.
        """

        marks = combat.get("attack_marks")
        if not isinstance(marks, dict):
            return
        for per_kind in marks.values():
            if not isinstance(per_kind, dict):
                continue
            per_actor = per_kind.get(str(actor_id))
            if not isinstance(per_actor, dict):
                continue
            for target_id in list(per_actor):
                row = per_actor[target_id]
                remaining = int(row.get("turns_remaining", 0) or 0) if isinstance(
                    row, dict
                ) else 0
                if remaining > 0:
                    row["turns_remaining"] = remaining - 1
                else:
                    per_actor.pop(target_id, None)

    def _expire_conditions(
        self, snapshot: dict[str, Any], combat: dict[str, Any], actor_id: str,
        previous_actor_id: str,
    ) -> None:
        self._expire_attack_marks(combat, actor_id)
        conditions = self._conditions(snapshot, combat, actor_id)
        for condition_id in [
            key for key, value in conditions.items()
            if isinstance(value, dict) and value.get("duration") in {
                "actor_turn_start", "target_turn_start",
            }
        ]:
            conditions.pop(condition_id, None)
        all_actor_ids = [
            *(_player_actor(uid) for uid in snapshot["characters"]),
            *(
                _companion_actor(companion_id)
                for companion_id in (snapshot["ruleset_state"].get("party", {}) or {}).get(
                    "companions", {},
                )
            ),
            *(_enemy_actor(enemy_id) for enemy_id in combat.get("enemies", {})),
        ]
        for target_id in all_actor_ids:
            target_conditions = self._conditions(snapshot, combat, target_id)
            for condition_id, value in list(target_conditions.items()):
                if not isinstance(value, dict):
                    continue
                if (
                    value.get("duration") == "attacker_next_turn_end"
                    and value.get("source_actor_id") == previous_actor_id
                ):
                    # "Until the end of <来源>的 **next** turn"：若这条是在来源
                    # 自己回合内挂上的，那么"这一回合结束"还不是"下个回合"，
                    # 要像 target_turn_end 一样多撑一轮。缺了这一步会提前整整
                    # 一轮过期（审核抓到的 bug）。
                    remaining_turns = int(value.get("expires_after", 1) or 1)
                    if remaining_turns > 1:
                        value["expires_after"] = remaining_turns - 1
                        continue
                    target_conditions.pop(condition_id, None)
                    continue
                if (
                    value.get("duration") == "attacker_next_turn_start"
                    and value.get("source_actor_id") == actor_id
                ):
                    # "Until the start of your next turn": the source actor's
                    # turn has just begun, so this instance expires now.
                    target_conditions.pop(condition_id, None)
                    continue
                if value.get("duration") == "target_turn_end" and (
                    target_id == previous_actor_id
                ):
                    # "Until the end of its next turn"（怪物命中后的 Poisoned
                    # 等 rider）：到期时刻是**目标自己**这一回合结束，
                    # 因此读 previous_actor_id，与来源是谁无关。记录里
                    # ``expires_after`` > 1 表示它是在目标自己的回合里挂上的，
                    # 那一次回合结束还不是"下个回合"。
                    remaining_turns = int(value.get("expires_after", 1) or 1)
                    if remaining_turns > 1:
                        value["expires_after"] = remaining_turns - 1
                        continue
                    target_conditions.pop(condition_id, None)
                    continue
                if value.get("duration") in TURNS_SCOPED_DURATIONS:
                    # 1 分钟在回合制里就是十个自己的回合：每到自己回合结束
                    # 减一，归零移除。计数放在条件上，所以中断、存档、重放
                    # 都读同一份事实，不需要外部计时器。计时属于**持有着**
                    # 的回合，而不是施法者的回合，因此这一支在下面的
                    # ``source_actor_id`` 过滤之前。
                    if target_id != previous_actor_id:
                        continue
                    remaining = int(value.get("turns_remaining", 0) or 0) - 1
                    if remaining <= 0:
                        target_conditions.pop(condition_id, None)
                    else:
                        value["turns_remaining"] = remaining
                    continue
                if value.get("source_actor_id") != previous_actor_id:
                    continue
                duration = value.get("duration")
                if duration == "caster_turn_end":
                    target_conditions.pop(condition_id, None)
                elif duration == "caster_next_turn_end":
                    remaining = int(value.get("turns_remaining", 2) or 2)
                    if remaining <= 1:
                        target_conditions.pop(condition_id, None)
                    else:
                        value["turns_remaining"] = remaining - 1
        if previous_actor_id:
            # Rage 的回合级生命周期。规则保证狂暴"持续至你的下个回合结束"：
            # 进入回合结束时只把状态标记为已确立（第一轮无条件存续，本回合
            # 是否攻击或延长都不影响）；从下个回合结束起，未续期即结束，
            # 续期则消耗剩余轮数，归零即结束。昏迷则无条件结束。
            previous_conditions = self._conditions(snapshot, combat, previous_actor_id)
            rage = previous_conditions.get("rage")
            if isinstance(rage, dict) and rage.get("duration") == PERSISTENT_RAGE_DURATION:
                # 持久狂怒：不靠攻击或附赠动作续期，单纯 Incapacitated /
                # Stunned / Paralyzed 也不结束；只有 Unconscious 或真的穿上
                # 重甲结束。剩余轮数每轮照常递减，没有旧 Rage 那条"进入的那个
                # 回合免费"的豁免——10 分钟是从进入那一刻起算的 100 轮。
                if (
                    "unconscious" in previous_conditions
                    or self._wears_heavy_armor(snapshot, previous_actor_id)
                ):
                    previous_conditions.pop("rage", None)
                else:
                    remaining = int(
                        rage.get("turns_remaining", RAGE_MAX_ROUNDS) or RAGE_MAX_ROUNDS
                    ) - 1
                    if remaining <= 0:
                        previous_conditions.pop("rage", None)
                    else:
                        rage["turns_remaining"] = remaining
                        rage["sustained_this_turn"] = False
            elif isinstance(rage, dict):
                # 失能即结束狂暴：unconscious / incapacitated 以及所有
                # 蕴含失能的条件（paralyzed / stunned / petrified）。
                if any(
                    condition in previous_conditions
                    for condition in (
                        "unconscious", "incapacitated", "paralyzed", "stunned", "petrified",
                    )
                ):
                    previous_conditions.pop("rage", None)
                elif not rage.get("established"):
                    rage["established"] = True
                    rage["sustained_this_turn"] = False
                elif rage.get("sustained_this_turn"):
                    remaining = int(
                        rage.get("turns_remaining", RAGE_MAX_ROUNDS) or RAGE_MAX_ROUNDS
                    ) - 1
                    if remaining <= 0:
                        previous_conditions.pop("rage", None)
                    else:
                        rage["turns_remaining"] = remaining
                        rage["sustained_this_turn"] = False
                else:
                    previous_conditions.pop("rage", None)
