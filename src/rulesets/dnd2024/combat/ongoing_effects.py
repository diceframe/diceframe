"""Persistent and turn-limited class effects, including weapon and self buffs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..character.builder import ability_modifier
from ..character.derivation import proficiency_bonus
from ..features.attack_declarations import SACRED_WEAPON, declaration_value
from ..features.resources import long_effect_active, resource_state
from .action_adapter import formula_node_from_dice_formula, roll_damage_node
from .primitives import (
    INTIMIDATING_PRESENCE_DURATION,
    PERSISTENT_RAGE_DURATION,
    RAGE_MAX_ROUNDS,
    SACRED_WEAPON_CONDITION,
    SACRED_WEAPON_DURATION,
    SUPERIOR_DEFENSE_CONDITION,
    SUPERIOR_DEFENSE_DURATION,
    CombatIntentError,
    world_minutes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..features.resolver import Dnd2024ClassFeatureResolver


class CombatOngoingEffectsMixin:
    __slots__ = ()

    if TYPE_CHECKING:
        # Implemented by the shared combat authority and view mixins.
        features: Dnd2024ClassFeatureResolver
        _logical_minutes: Callable[[Any], int]
        _feature_character: Callable[[dict[str, Any]], dict[str, Any]]
        _actor_view: Callable[[Any, dict[str, Any], str], dict[str, Any]]
        _distance: Callable[[dict[str, Any], str, str], int]
        _strength_total_replacement: Callable[[dict[str, Any], str, int], tuple[int, dict[str, Any]]]
        _condition_immunity_blocker: Callable[[Any, dict[str, Any], dict[str, Any], str], dict[str, str] | None]
        _condition_gain_cleanup_events: Callable[[Any, dict[str, Any], str], list[dict[str, Any]]]
        _rider_move_events: Callable[[dict[str, Any], dict[str, Any], str, dict[str, Any]], list[dict[str, Any]]]
        _saving_throw_check: Callable[[Any, dict[str, Any], dict[str, Any], str, str, int, str, list[dict[str, Any]], Any], dict[str, Any]]

        def _damage_instance_event(
            self, dice: str, damage_type: str, target: dict[str, Any],
            critical: bool, rng: Any, *, half: bool = False,
        ) -> tuple[dict[str, Any] | None, int]: ...

    def _persistent_rage_opening_events(
        self, view: dict[str, Any], character: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Persistent Rage: refill every expended Rage use, once per Long Rest."""

        if not self.features.has_feature(character, "persistent_rage"):
            return []
        charge = resource_state(character, "persistent_rage")
        if int(charge.get("current", 0) or 0) < 1:
            return []
        rages = resource_state(character, "rages")
        maximum = int(rages.get("maximum", 0) or 0)
        actor_id = str(view["actor_id"])
        events: list[dict[str, Any]] = []
        if maximum > 0:
            events.append({
                "type": "dnd2024.class_resource.refilled", "actor_id": actor_id,
                "resource_id": "rages", "target": maximum,
            })
        events.append({
            "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
            "resource_id": "persistent_rage", "amount": 1,
        })
        return events

    def _capability_rage_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any], intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        actor_id = str(actor["actor_id"])
        events: list[dict[str, Any]] = []
        # Entering Rage ends concentration before applying its condition.
        if actor.get("concentration"):
            events.append({
                "type": "dnd2024.concentration.ended", "actor_id": actor_id,
                "reason": "rage",
            })
        events.append({
            "type": "condition.applied", "target_id": actor_id,
            "condition": "rage",
            "duration": (
                PERSISTENT_RAGE_DURATION
                if self.features.has_feature(self._feature_character(actor), "persistent_rage")
                else "rage"
            ),
            "turns_remaining": RAGE_MAX_ROUNDS,
            "sustained_this_turn": False, "established": False,
            "source_actor_id": actor_id,
        })
        # Gaining immunity also ends conditions that are already active.
        events.extend(self._condition_gain_cleanup_events(instance, actor, "rage"))
        events.extend(self._rider_move_events(combat, actor, "rage", intent))
        return events

    def _temp_hp_grant_events(
        self, actor: dict[str, Any], feature_id: str, rng: Any,
    ) -> list[dict[str, Any]]:
        """A self-only Temporary Hit Point grant paid by an action.

        Tireless rolls its own die and adds the ability modifier.  The reducer
        already owns the one rule that matters here -- keep the higher of the
        old and the new pool, never touch Hit Points, Unconscious or stable --
        so this method produces only the roll and the amount.  A lower result
        therefore still costs the action and the charge, which is what the rule
        says: the die was rolled, not negotiated.
        """

        character = self._feature_character(actor)
        dice = str(self.features.feature_value(
            character, feature_id, "temp_hp_dice", "",
        ) or "")
        ability = str(self.features.feature_value(
            character, feature_id, "temp_hp_dice_ability", "",
        ) or "")
        minimum = int(self.features.feature_value(
            character, feature_id, "temp_hp_dice_minimum", 1,
        ) or 1)
        node = formula_node_from_dice_formula(dice)
        modifier = (
            ability_modifier(int((actor.get("abilities") or {}).get(ability, 10) or 10))
            if ability else 0
        )
        rolled, rolls = roll_damage_node(node, rng)
        amount = max(minimum, rolled + modifier)
        actor_id = str(actor["actor_id"])
        return [{
            "type": "resource.changed", "resource": "temp_hp",
            "target_id": actor_id, "source_actor_id": actor_id,
            "delta": amount, "amount": amount, "rolls": rolls,
            "feature_id": feature_id, "ability": ability,
            "ability_modifier": modifier, "rolled": rolled,
        }]

    def _self_invisible_events(
        self, actor: dict[str, Any], feature_id: str,
    ) -> list[dict[str, Any]]:
        """Nature's Veil: Invisible until the end of the actor's next turn.

        ``caster_next_turn_end`` with two ticks is exactly that span: the
        actor's own turn end takes it to one, the next one takes it to zero.
        Nothing else ends it -- attacking or being attacked does not, because
        the SRD text grants a fixed span and the generic visibility rules
        already answer who can see whom.  Re-applying writes the same
        condition again rather than stacking a second one.
        """

        character = self._feature_character(actor)
        condition = str(self.features.feature_value(
            character, feature_id, "condition", "invisible",
        ) or "invisible")
        duration = str(self.features.feature_value(
            character, feature_id, "condition_duration", "caster_next_turn_end",
        ) or "caster_next_turn_end")
        turns = int(self.features.feature_value(
            character, feature_id, "condition_turns", 2,
        ) or 2)
        actor_id = str(actor["actor_id"])
        return [{
            "type": "condition.applied", "target_id": actor_id,
            "condition": condition, "duration": duration,
            "turns_remaining": turns, "source_actor_id": actor_id,
            "feature_id": feature_id,
        }]

    def _resistance_all_events(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        feature_id: str,
    ) -> list[dict[str, Any]]:
        """Superior Defense: Resistance to every damage type except a named few.

        The condition carries the *exclusion* instead of an enumerated list of
        every type: a damage type added to the catalog later must be resisted by
        default, and a copied list would silently be one entry short.  Both time
        limits are recorded -- one real minute on the world clock and ten combat
        rounds -- because a fight does not advance the clock and a rest does not
        advance the round counter; whichever runs out first ends the effect.
        """

        character = self._feature_character(actor)
        minutes = int(self.features.feature_value(
            character, feature_id, "duration_minutes", 1,
        ) or 1)
        rounds = int(self.features.feature_value(
            character, feature_id, "duration_rounds", 10,
        ) or 10)
        excepted = self.features.feature_value(
            character, feature_id, "resist_all_except", ["force"],
        ) or []
        now = self._logical_minutes(instance)
        actor_id = str(actor["actor_id"])
        return [{
            "type": "condition.applied", "target_id": actor_id,
            "condition": SUPERIOR_DEFENSE_CONDITION,
            "duration": SUPERIOR_DEFENSE_DURATION,
            "duration_minutes": minutes, "duration_rounds": rounds,
            "expires_at_minute": now + minutes,
            "expires_at_round": int(combat.get("round", 1) or 1) + rounds,
            "resist_all_except": [
                str(kind) for kind in excepted if isinstance(kind, str) and kind
            ],
            "ends_on_incapacitated": bool(self.features.feature_value(
                character, feature_id, "ends_on_incapacitated", False,
            )),
            "source_actor_id": actor_id, "feature_id": feature_id,
        }]

    def _superior_defense_expiry_events(
        self, instance: Any, combat: dict[str, Any], round_number: int | None = None,
    ) -> list[dict[str, Any]]:
        """End Superior Defense when either of its two limits has passed.

        A combat round is six seconds, so ten rounds are one minute on the
        battlefield; the world clock is the only thing that measures time
        outside it.  Both limits are read and whichever expired first ends the
        effect, so it never outlives its minute by hiding behind ``combat.ended``.

        ``round_number`` is supplied only when a turn just advanced.  Every
        combat intent sweeps the clock limit too, which is what removes a
        leftover buff carried out of an earlier fight without inventing a
        second expiry mechanism.  An actor whose view cannot be built any more
        is simply skipped: it left the fight, and its conditions went with it.
        """

        now = self._logical_minutes(instance)
        events: list[dict[str, Any]] = []
        for actor_id in combat.get("initiative") or []:
            identifier = str(actor_id)
            try:
                view = self._actor_view(instance, combat, identifier)
            except CombatIntentError:
                continue
            condition = (view.get("conditions") or {}).get(SUPERIOR_DEFENSE_CONDITION)
            if not isinstance(condition, dict):
                continue
            expires_round = condition.get("expires_at_round")
            expired = (
                round_number is not None
                and isinstance(expires_round, int) and not isinstance(expires_round, bool)
                and round_number > expires_round
            )
            if not expired:
                expires_at = condition.get("expires_at_minute")
                expired = (
                    isinstance(expires_at, int) and not isinstance(expires_at, bool)
                    and now >= expires_at
                )
            if not expired:
                continue
            events.append({
                "type": "condition.removed", "target_id": identifier,
                "condition": SUPERIOR_DEFENSE_CONDITION,
                "reason": "one minute passed",
            })
        return events

    def _quivering_palm_end_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any], capability: Any, rng: Any,
    ) -> list[dict[str, Any]]:
        """Quivering Palm: ending the vibrations is its own action.

        目标与到期都存在角色级 `long_effects` 里：振动可能持续好几天，跨战斗、
        跨存档。"同一位面"没有权威系统，这个入口**不判定**——不默认成功，
        也不假装知道；文档与测试都按 `guided` 记录。
        """

        character = self._feature_character(actor)
        feature_id = capability.feature_id
        effect = long_effect_active(
            character, "quivering_palm", world_minutes(instance),
        )
        if effect is None:
            raise CombatIntentError("no creature is under Quivering Palm")
        detail = effect.get("detail") or {}
        target_id = str(detail.get("target_id") or "")
        if not target_id:
            raise CombatIntentError("the vibration has no recorded target")
        save_ability = str(self.features.feature_value(
            character, feature_id, "save_ability", "con",
        ) or "con")
        dc_ability = str(self.features.feature_value(
            character, feature_id, "dc_ability", "wis",
        ) or "wis")
        dice = str(self.features.feature_value(
            character, feature_id, "damage_dice", "10d12",
        ) or "10d12")
        abilities = actor.get("abilities") or {}
        level = int((character.get("build") or {}).get("level", 1) or 1)
        dc = (
            8
            + (int(abilities.get(dc_ability, 10) or 10) - 10) // 2
            + proficiency_bonus(level)
        )
        target = self._actor_view(instance, combat, target_id)
        # 走与法术、重复豁免同一个权威函数：自动失败（麻痹/昏迷）、光环、
        # 掩护、条件加值与 roll_id 都在里面，手写一份就全丢了。
        events: list[dict[str, Any]] = []
        check = self._saving_throw_check(
            instance, combat, target, target_id,
            save_ability, dc, actor_id, events, rng,
        )
        check["feature_id"] = feature_id
        check["channel_effect"] = "quivering_palm"
        # 位面由调用方断言，不由引擎判定：没有权威系统时，把这条事实原样
        # 写进事件，让日志能看出它来自哪里。
        check["plane_asserted"] = True
        events.append(check)
        saved = bool(check["success"])
        events.append({
            "type": "dnd2024.long_effect.ended",
            "actor_id": actor_id,
            "effect_id": "quivering_palm",
            "reason": "ended",
        })
        # 抗性与免疫走与其它伤害同一个实例函数：力场抗性必须生效。
        damage_type = str(self.features.feature_value(
            character, feature_id, "damage_type", "force",
        ) or "force")
        damage, _total = self._damage_instance_event(
            dice, damage_type, target, False, rng, half=saved,
        )
        if damage is not None:
            damage["feature_id"] = feature_id
            damage["source_actor_id"] = actor_id
            damage["reason"] = "quivering_palm"
            events.append(damage)
        return events

    def _holy_nimbus_events(
        self, instance: Any, actor_id: str, actor: dict[str, Any], capability: Any,
    ) -> list[dict[str, Any]]:
        """Holy Nimbus: a ten-minute emanation, recorded as a long effect.

        光环的范围与伤害由特性参数声明（`aura_range_ft` + `turn_start_aura_damage`），
        这里只负责"它现在生效"这个事实。住在 long_effects 而不是条件里：
        十分钟会跨战斗。
        """

        character = self._feature_character(actor)
        feature_id = capability.feature_id
        minutes = int(self.features.feature_value(
            character, feature_id, "duration_minutes", 10,
        ) or 10)
        radius = int(self.features.feature_value(
            character, feature_id, "aura_range_ft", 30,
        ) or 30)
        return [{
            "type": "dnd2024.long_effect.applied",
            "actor_id": actor_id,
            "effect_id": feature_id,
            "effect": {
                "feature_id": feature_id,
                "expires_at": world_minutes(instance) + minutes,
                "detail": {"bright_light_radius": radius},
            },
        }]

    def _dragon_wings_events(
        self, instance: Any, actor_id: str, actor: dict[str, Any], capability: Any,
    ) -> list[dict[str, Any]]:
        """Dragon Wings: a one-hour Fly Speed as a character-level effect.

        住在 `long_effects` 而不是条件里：战斗结束会清掉按回合计时的条件，
        而这是一小时的物理事实。到期按世界时钟的绝对分钟判定。
        """

        character = self._feature_character(actor)
        feature_id = capability.feature_id
        minutes = int(self.features.feature_value(
            character, feature_id, "duration_minutes", 60,
        ) or 60)
        fly = int(self.features.feature_value(
            character, feature_id, "fly_speed_ft", 60,
        ) or 60)
        return [{
            "type": "dnd2024.long_effect.applied",
            "actor_id": actor_id,
            "effect_id": feature_id,
            "effect": {
                "feature_id": feature_id,
                "expires_at": world_minutes(instance) + minutes,
                "detail": {"fly_speed": fly},
            },
        }]

    def _validate_intimidating_presence(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any], capability: Any, intent: dict[str, Any],
    ) -> str:
        """Fail closed on empty, duplicate, self, unknown or all-out-of-range targets."""

        raw_targets = intent.get("target_ids")
        if not isinstance(raw_targets, list) or not raw_targets:
            return "Intimidating Presence requires at least one target"
        targets: list[str] = []
        for item in raw_targets:
            if not isinstance(item, str) or not item.strip():
                return "Intimidating Presence target_ids must be non-empty actor ids"
            targets.append(item)
        if len(set(targets)) != len(targets):
            return "Intimidating Presence cannot target the same creature twice"
        character = self._feature_character(actor)
        range_ft = int(self.features.feature_value(
            character, capability.feature_id, "target_range_ft", 30,
        ) or 30)
        eligible = 0
        for target_id in targets:
            if target_id == actor_id:
                return "Intimidating Presence cannot target yourself"
            target = self._actor_view(instance, combat, target_id)
            if (
                int(target.get("hp", 0) or 0) > 0
                and self._distance(combat, actor_id, target_id) <= range_ft
            ):
                eligible += 1
        if eligible < 1:
            return "Intimidating Presence requires at least one living target within range"
        return ""

    def _intimidating_presence_events(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
        actor: dict[str, Any], capability: Any, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        """Intimidating Presence: a 30-foot emanation, one Wisdom save each.

        DC 是 8 + 力量调整 + 熟练，不是法术 DC——这条特性由蛮子的威压而不是
        施法能力驱动。范围在服务端按距离判定：客户端可以把任意 id 塞进
        target_ids，"我觉得他在 30 尺内"不是事实。

        失败者恐惧一分钟，并在自己每个回合末重复豁免；成功只结束自己的效果，
        不影响同一次使用里的其他目标。
        """

        character = self._feature_character(actor)
        feature_id = capability.feature_id
        save_ability = str(self.features.feature_value(
            character, feature_id, "save_ability", "wis",
        ) or "wis")
        condition = str(self.features.feature_value(
            character, feature_id, "condition", "frightened",
        ) or "frightened")
        minutes = int(self.features.feature_value(
            character, feature_id, "condition_minutes", 1,
        ) or 1)
        range_ft = int(self.features.feature_value(
            character, feature_id, "target_range_ft", 30,
        ) or 30)
        dc_ability = str(self.features.feature_value(
            character, feature_id, "dc_ability", "str",
        ) or "str")
        abilities = actor.get("abilities") or {}
        dc = (
            8
            + ability_modifier(int(abilities.get(dc_ability, 10) or 10))
            + int(actor.get("proficiency_bonus", 0) or 0)
        )
        events: list[dict[str, Any]] = []
        unique_targets = list(dict.fromkeys(
            str(item) for item in (intent.get("target_ids") or [])
            if isinstance(item, str) and item
        ))
        for target_id in unique_targets:
            if target_id == actor_id:
                continue
            if self._distance(combat, actor_id, target_id) > range_ft:
                continue
            target = self._actor_view(instance, combat, target_id)
            if int(target.get("hp", 0) or 0) <= 0:
                continue
            # 统一走共享豁免入口：魔抗/优势劣势、圣武士保护光环、Bless/Bane 骰与
            # 传说抗性都在其中结算，不再绕开豁免管线手写 d20。
            check = self._saving_throw_check(
                instance, combat, target, target_id,
                save_ability, dc, actor_id, events, rng,
            )
            check["feature_id"] = feature_id
            check["channel_effect"] = "intimidating_presence"
            check_index = len(events)
            events.append(check)
            if bool(check.get("success")):
                continue
            blocker = self._condition_immunity_blocker(
                instance, combat, target, condition,
            )
            if blocker is not None:
                scope = str(blocker.get("scope") or "")
                events[check_index]["blocked_condition"] = condition
                events[check_index][
                    "blocked_by_aura" if scope == "aura" else "blocked_by_feature"
                ] = {key: value for key, value in blocker.items() if key != "scope"}
                continue
            events.append({
                "type": "condition.applied", "target_id": target_id,
                "condition": condition,
                "duration": INTIMIDATING_PRESENCE_DURATION,
                "turns_remaining": minutes * 10,
                "source_actor_id": actor_id, "feature_id": feature_id,
                "channel_effect": "intimidating_presence",
                "save_repeat": {"ability": save_ability, "dc": dc},
            })
        return events

    def _validate_sacred_weapon(
        self, intent: dict[str, Any], actor: dict[str, Any],
        weapon: dict[str, Any], combat: dict[str, Any],
    ) -> None:
        """Sacred Weapon is declared on an attack, not as its own action.

        Two facts are re-derived here rather than trusted from the panel: that
        this is the *first* attack of a real Attack action (a Channel use
        cannot be bolted on to the second swing of a round, nor to an
        opportunity attack), and that the weapon being imbued is the very
        weapon this attack uses -- a Melee weapon the actor is carrying.
        """

        declared = declaration_value(intent, SACRED_WEAPON)
        damage_choice = intent.get("sacred_weapon_damage")
        if declared in (None, False) and damage_choice is None:
            return
        character = self._feature_character(actor)
        if not self.features.has_feature(character, "sacred_weapon"):
            raise CombatIntentError("sacred weapon is not available to this actor")
        weapon_ref = str(weapon.get("weapon_ref") or "")
        binding = (actor.get("conditions") or {}).get(SACRED_WEAPON_CONDITION)
        bound_here = (
            isinstance(binding, dict)
            and str(binding.get("weapon_ref") or "") == weapon_ref
        )
        if declared not in (None, False):
            if declared is not True:
                raise CombatIntentError("activate_sacred_weapon must be a boolean")
            if weapon.get("ranged") or weapon.get("unarmed"):
                raise CombatIntentError(
                    "sacred weapon imbues a Melee weapon you are holding"
                )
            economy = combat.get("economy") or {}
            if int(economy.get("attacks_remaining", 0) or 0) > 0:
                raise CombatIntentError(
                    "sacred weapon is imbued when you take the Attack action, "
                    "not after its first attack"
                )
            if (
                int(economy.get("action", 0) or 0) < 1
                and int(economy.get("surge_action", 0) or 0) < 1
            ):
                raise CombatIntentError("sacred weapon requires the Attack action")
            pool = resource_state(character, "channel_divinity")
            if int(pool.get("current", 0) or 0) < 1:
                raise CombatIntentError("channel divinity is already spent")
        if damage_choice is not None:
            if not bound_here and declared is not True:
                raise CombatIntentError(
                    "this weapon carries no sacred weapon bond"
                )
            allowed = [
                str(value) for value in (
                    self.features.feature_value(
                        character, "sacred_weapon", "weapon_damage_type_choices",
                        ["normal", "radiant"],
                    ) or []
                )
            ]
            if str(damage_choice) not in allowed:
                raise CombatIntentError(
                    "the declared sacred weapon damage type is not available"
                )

    def _sacred_weapon_binding(self, actor: dict[str, Any]) -> dict[str, Any]:
        """The weapon this Paladin has imbued right now, or ``{}``.

        The binding is one condition on the holder, never a flag on the weapon:
        a second activation replaces it, and ending it is a single removal.
        """

        meta = (actor.get("conditions") or {}).get(SACRED_WEAPON_CONDITION)
        return meta if isinstance(meta, dict) else {}

    def _sacred_weapon_bonus(
        self, actor: dict[str, Any], weapon_ref: str,
    ) -> int:
        """The attack bonus this weapon_ref carries, or zero.

        Matching is by canonical ref: the same weapon *type* in someone else's
        hands, or a second copy of it in this one, is not the imbued weapon.
        """

        binding = self._sacred_weapon_binding(actor)
        if not binding:
            return 0
        if str(binding.get("weapon_ref") or "") != str(weapon_ref or ""):
            return 0
        return int(binding.get("bonus", 0) or 0)

    def _sacred_weapon_bonus_value(
        self, actor: dict[str, Any],
    ) -> tuple[int, str]:
        """The bonus this Paladin's Sacred Weapon grants, and its ability.

        Read from the sheet every time instead of from the stored binding: the
        activation attack itself is answered before the condition exists, and
        both answers must be the same number.
        """

        character = self._feature_character(actor)
        ability = str(self.features.feature_value(
            character, "sacred_weapon", "weapon_attack_bonus_ability", "cha",
        ) or "cha")
        minimum = int(self.features.feature_value(
            character, "sacred_weapon", "weapon_attack_bonus_minimum", 1,
        ) or 1)
        return (
            max(
                minimum,
                ability_modifier(
                    int((actor.get("abilities") or {}).get(ability, 10) or 10)
                ),
            ),
            ability,
        )

    def _sacred_weapon_events(
        self, instance: Any, actor: dict[str, Any], weapon_ref: str,
    ) -> list[dict[str, Any]]:
        """Imbue one held Melee weapon: pay a Channel use, then record the bond.

        Order matters for audit: the old binding (if any) is ended *before* the
        new one exists, so no reader ever sees two bounds at once.
        """

        actor_id = str(actor["actor_id"])
        character = self._feature_character(actor)
        feature_id = "sacred_weapon"
        bonus, ability = self._sacred_weapon_bonus_value(actor)
        minutes = int(self.features.feature_value(
            character, feature_id, "weapon_binding_minutes", 10,
        ) or 10)
        choices = [
            str(value) for value in (
                self.features.feature_value(
                    character, feature_id, "weapon_damage_type_choices",
                    ["normal", "radiant"],
                ) or []
            ) if str(value)
        ]
        events: list[dict[str, Any]] = []
        if self._sacred_weapon_binding(actor):
            events.append({
                "type": "condition.removed", "target_id": actor_id,
                "condition": SACRED_WEAPON_CONDITION, "reason": "sacred_weapon_recast",
            })
        events.append({
            "type": "dnd2024.class_resource.spent", "actor_id": actor_id,
            "resource_id": str(self.features.feature_value(
                character, feature_id, "channel_resource_id", "channel_divinity",
            ) or "channel_divinity"),
            "amount": 1,
            "feature_id": feature_id,
        })
        events.append({
            "type": "condition.applied", "target_id": actor_id,
            "condition": SACRED_WEAPON_CONDITION,
            "duration": SACRED_WEAPON_DURATION,
            "duration_minutes": minutes,
            "expires_at_minute": self._logical_minutes(instance) + minutes,
            "weapon_ref": weapon_ref, "bonus": bonus, "ability": ability,
            "damage_type_choices": choices,
            # 光照只是可审计事实：没有光照系统时它不参与 AC、攻击、Hide 或
            # 感知，所以这里显式写 guided，而不是假装已经自动判定。
            "bright_light_radius": int(self.features.feature_value(
                character, feature_id, "bright_light_radius", 20,
            ) or 20),
            "dim_light_beyond": int(self.features.feature_value(
                character, feature_id, "dim_light_beyond", 20,
            ) or 20),
            "light_automation": str(self.features.feature_value(
                character, feature_id, "light_automation", "guided",
            ) or "guided"),
            "source_actor_id": actor_id, "feature_id": feature_id,
        })
        return events
