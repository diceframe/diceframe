"""Attack, weapon and multiattack declaration checks.

One of the validation mixins: the engine assembles them into
``CombatValidationMixin``; the class holds no state of its own."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from ..character.attunement import AttunementError, canonical_item_ref, inventory_ref_counts, owned_ref_counts
from .weapon_masteries import WEAPON_MASTERY_FEATURE_ID, cleave_grant, nick_active
from ..features.attack_declarations import BRUTAL_STRIKE, CUNNING_STRIKE_EFFECTS_KEY, LIGHT_EXTRA_ATTACK, STUNNING_STRIKE, declaration_value, validate_cunning_strike, validate_uses, validate_weapon_selection
from .primitives import CombatIntentError, rider_attack_kind, world_minutes
from .check_modes import effective_speed, marked_target_id
from .hands import apply_swap


class CombatValidationAttackMixin:
    """Attack, weapon and multiattack declaration checks."""

    __slots__ = ()

    if TYPE_CHECKING:
        catalog: Any
        bundle: Any
        _spend_action_resource: Any
        _distance: Any
        _feature_character: Any
        _feature_used_this_turn: Any
        attacked_this_turn: Any
        features: Any
        mark_damage_this_turn: Any
        def _activation_payload(self, instance: Any, actor: dict[str, Any]) -> Any:
            ...
        _actor_view: Any
        _declared_attack: Any
        _effective_conditions: Any
        def _restriction_sources(
            self, conditions: dict[str, Any], flag: str,
        ) -> list[str]:
            ...
        def _validate_lucky_spend(
            self, instance: Any, intent: dict[str, Any], actor: dict[str, Any],
        ) -> None:
            ...
        _validate_sacred_weapon: Any
        _declared_action: Any
        _multiattack_parts: Any
        _attack_check_mode: Any
        attack_ability: Any
        attack_range_disadvantage: Any

    def _validate_two_handed(
        self, actor: dict[str, Any], intent: dict[str, Any], weapon: dict[str, Any],
        hands: dict[str, Any] | None = None,
    ) -> None:
        """两用武器双手持用的声明校验。

        ``equipment_refs`` 是携带清单，``hands`` 才是这一场的持握状态：角色
        可以随身带备用武器而不握着它，所以"另一只手是否被占"由 hands 回答，
        而不是由"背包里有没有盾"回答。
        """

        declared = intent.get("two_handed")
        if declared is None or declared is False:
            return
        if declared is not True:
            raise CombatIntentError("two_handed must be a boolean")
        if actor["kind"] not in {"player", "companion"}:
            # 敌方 stat block 没有两用概念，其攻击档也不声明该字段。
            raise CombatIntentError("two_handed is not available to this actor")
        if not weapon.get("damage_two_handed"):
            raise CombatIntentError("this weapon has no two-handed damage")
        if self._held_shield_blocks(
            actor, str(weapon.get("weapon_ref") or ""), hands=hands,
        ):
            raise CombatIntentError("a shield in the other hand blocks two-handed use")

    def _is_weapon_ref(self, ref: str) -> bool:
        return str(ref).removeprefix("item:") in self.catalog.weapons

    def _two_handed_refs(self, actor: dict[str, Any]) -> set[str]:
        """Carried refs whose weapon needs both hands (mounted exception aside)."""

        refs: set[str] = set()
        for raw in actor.get("equipment_refs") or []:
            ref = str(raw)
            weapon = self.catalog.weapons.get(ref.removeprefix("item:"))
            if not isinstance(weapon, dict) or not weapon.get("two_handed"):
                continue
            exception = str(weapon.get("two_handed_exception") or "")
            if exception == "mounted" and bool(actor.get("mounted")):
                continue
            refs.add(ref)
        return refs

    def _weapon_swap_hands(
        self, actor: dict[str, Any], intent: dict[str, Any],
    ) -> dict[str, str | None] | None:
        """Validate the declared swap and return the grip it produces.

        One equip/unequip per attack is the SRD budget, so a declaration that
        tries to do two things at once is malformed rather than rich.  Stowing
        is limited to weapons: a shield comes off with an action, not for free.
        """

        swap = intent.get("weapon_swap")
        if swap is None:
            return None
        if actor["kind"] not in {"player", "companion"}:
            raise CombatIntentError("weapon_swap is not available to this actor")
        carried = {str(ref) for ref in actor.get("equipment_refs") or []}
        if isinstance(swap, dict):
            draw = str(swap.get("draw") or "")
            stow = str(swap.get("stow") or "")
            if draw and draw not in carried:
                raise CombatIntentError("weapon to draw is not carried")
            if stow and not self._is_weapon_ref(stow):
                raise CombatIntentError(
                    "only a weapon can be stowed as part of an attack"
                )
        hands, error = apply_swap(
            actor.get("hands"), swap, two_handed_refs=self._two_handed_refs(actor),
        )
        if error:
            raise CombatIntentError(error)
        return hands

    def _held_shield_blocks(
        self, actor: dict[str, Any], weapon_ref: str,
        hands: dict[str, Any] | None = None,
    ) -> bool:
        """Whether a shield occupies the hand this weapon does not.

        ``equipment`` 是携带清单，``hands`` 才是持握。带在身上的备用武器随时
        可以按"每次攻击一次"的规则收起来，因此不构成阻挡；盾牌是绑在手臂上
        的，不能免费脱手，所以它才是真正的阻挡源。缺少 ``hands`` 的调用方
        （只读视图、旧测试夹具）退回旧口径：按携带清单里的盾牌判断。
        """

        hands = actor.get("hands") if hands is None else hands
        if isinstance(hands, dict):
            for side in ("left", "right"):
                ref = str(hands.get(side) or "")
                if not ref or ref == weapon_ref:
                    continue
                item = self.bundle.get("item", ref.removeprefix("item:")) or {}
                if item.get("item_type") == "shield":
                    return True
            return False
        for ref in actor.get("equipment_refs") or []:
            item = self.bundle.get("item", str(ref).removeprefix("item:")) or {}
            if item.get("item_type") == "shield":
                return True
        return False

    def _validate_loading(
        self, combat: dict[str, Any], actor: dict[str, Any], intent: dict[str, Any],
        weapon: dict[str, Any],
    ) -> None:
        """A Loading weapon fires once per action / Bonus Action / Reaction.

        窗口由"支付这次攻击的经济池"标识：同一个 Attack action 里的两次攻击
        共用 ``action``（或 ``surge_action``），所以第二发会被拒绝，尽管
        ``attacks_remaining`` 还允许它。换一个窗口（Bonus Action、Action
        Surge）是另一发，仍然合法。
        """

        if not weapon.get("loading"):
            return
        if actor["kind"] not in {"player", "companion"}:
            return
        resource = (
            "bonus_action" if declaration_value(intent, LIGHT_EXTRA_ATTACK)
            else self._spend_action_resource(combat)
        )
        record = (combat.get("loading_shots") or {}).get(str(actor["actor_id"]))
        if not isinstance(record, dict):
            return
        if (
            int(record.get("round", -1)) == int(combat.get("round", 0) or 0)
            and int(record.get("turn_index", -1)) == int(combat.get("turn_index", 0) or 0)
            and str(record.get("window") or "") == resource
        ):
            raise CombatIntentError("a Loading weapon can fire only once per action")

    def _validate_light_extra_attack(
        self, combat: dict[str, Any], actor: dict[str, Any], intent: dict[str, Any],
        weapon: dict[str, Any],
    ) -> None:
        """The Light extra attack: a Bonus Action, a *different* Light weapon.

        There is no "off-hand" in these rules, and there is no "other hand"
        condition either: the SRD asks for "a different Light weapon", and the
        reference point is the weapon that triggered the extra attack.  So the
        gate is (a) this turn's Attack action attacked with a Light weapon and
        (b) at least one of those weapons is Light and is not the weapon used
        for the extra attack itself.  A same-hand or other-hand reading would
        let a shortsword be its own reference, which the rules forbid.

        Equip identity comes from ``_declared_attack``; the damage modifier
        decision (Two-Weapon Fighting) lives in resolution, next to the roll.
        """

        declared = declaration_value(intent, LIGHT_EXTRA_ATTACK)
        if declared is None:
            return
        if declared is not True:
            raise CombatIntentError("light_extra_attack must be a boolean")
        if actor["kind"] not in {"player", "companion"}:
            raise CombatIntentError("light_extra_attack is not available to this actor")
        if not weapon.get("light"):
            raise CombatIntentError("the extra attack must use a Light weapon")
        record = (combat.get("light_attacks") or {}).get(str(actor["actor_id"]))
        this_round = int(combat.get("round", 0) or 0)
        this_turn = int(combat.get("turn_index", 0) or 0)
        if not isinstance(record, dict) or (
            int(record.get("round", -1)) != this_round
            or int(record.get("turn_index", -1)) != this_turn
        ):
            raise CombatIntentError(
                "the extra attack requires an Attack action with a Light weapon this turn"
            )
        ref = str(weapon.get("weapon_ref") or "")
        # 参照点是"触发这次追加的那次攻击所用的武器"。同一把武器不能给自己做
        # 参照（无论本回合用它打过几次），非 Light 武器也不是合法参照 —— 两条
        # 都由这一个过滤表达，不做集合比较、不取末项。
        enabling = [
            str(used)
            for used in record.get("weapon_refs") or []
            if str(used) != ref
            and bool(
                (self.catalog.weapons.get(str(used).removeprefix("item:")) or {}).get("light")
            )
        ]
        if not enabling:
            raise CombatIntentError(
                "the extra attack needs a different Light weapon than the one that enabled it"
            )
        economy = combat.get("economy") or {}
        if str(economy.get("actor_id") or "") != str(actor["actor_id"]):
            raise CombatIntentError("the extra attack can only be made on your own turn")
        if nick_active(self.features, self._feature_character(actor), weapon):
            # Nick：这一次属于 Attack action，不花附赠动作，但每回合只有一次。
            # "另一把 Light 武器"这条不受影响，所以放在它之后判。
            if bool(record.get("nick_used")):
                raise CombatIntentError(
                    "Nick already gave its extra attack this turn"
                )
            return
        if int(economy.get("bonus_action", 0) or 0) < 1:
            raise CombatIntentError(
                "the extra attack costs a Bonus Action that is already spent"
            )

    def _validate_cleave(
        self, combat: dict[str, Any], actor: dict[str, Any], intent: dict[str, Any],
        weapon: dict[str, Any],
    ) -> None:
        """Cleave: a second attack after a hit, five feet from the first target.

        The prerequisite is a fact about the *previous* attack, so it comes out
        of ``attack_history`` -- which is why that ledger now records whether the
        attack hit and which weapon it used.  Everything else is the same shape
        as Horde Breaker's second attack: it does not pay the action, it rolls
        its own d20, and it may not be repeated this turn.
        """

        original_id = str(intent.get("cleave_from") or "")
        if not original_id:
            return
        actor_id = str(actor["actor_id"])
        if cleave_grant(weapon) is None:
            raise CombatIntentError("this weapon does not have Cleave")
        if not self.features.has_feature(
            self._feature_character(actor), WEAPON_MASTERY_FEATURE_ID,
        ):
            raise CombatIntentError("this character has not unlocked weapon masteries")
        round_index = int(combat.get("round", 0) or 0)
        turn_index = int(combat.get("turn_index", 0) or 0)
        granted = (combat.get("granted_attacks") or {}).get(actor_id) or {}
        if (
            granted.get("cleave")
            and int(granted.get("round", -1)) == round_index
            and int(granted.get("turn_index", -1)) == turn_index
        ):
            raise CombatIntentError("Cleave is already used this turn")
        weapon_ref = str(weapon.get("weapon_ref") or "")
        hit_before = any(
            str(row.get("actor_id") or "") == actor_id
            and str(row.get("target_id") or "") == original_id
            and int(row.get("round", 0) or 0) == round_index
            and int(row.get("turn_index", 0) or 0) == turn_index
            and bool(row.get("hit"))
            and str(row.get("weapon_ref") or "") == weapon_ref
            for row in combat.get("attack_history") or []
        )
        if not hit_before:
            raise CombatIntentError(
                "Cleave needs a hit with this weapon earlier this turn"
            )
        target_id = str(intent.get("target_id") or "")
        if target_id == original_id:
            raise CombatIntentError("Cleave must pick a different target")
        grant = cleave_grant(weapon) or {}
        limit = int(grant.get("target_within_ft") or 0)
        if self._distance(combat, target_id, original_id) > limit:
            raise CombatIntentError(
                f"Cleave's target must be within {limit} feet of the first target"
            )
        reach = int(weapon.get("long_range") or weapon.get("range", 5) or 5)
        if self._distance(combat, actor_id, target_id) > reach:
            raise CombatIntentError("Cleave's target is out of your reach")

    def _validate_grip(
        self, actor: dict[str, Any], weapon: dict[str, Any],
        hands: dict[str, Any] | None = None,
    ) -> None:
        """A Two-Handed weapon must actually get both hands.

        The versatile declaration already asked this question; Two-Handed
        weapons ask it implicitly, every time they are used.  Without this
        check "shield in one hand, greatsword in the other" is a legal attack,
        which is exactly what the data now says it is not.
        """

        if not weapon.get("two_handed"):
            return
        if actor["kind"] not in {"player", "companion"}:
            return
        exception = str(weapon.get("two_handed_exception") or "")
        if exception == "mounted" and bool(actor.get("mounted")):
            return
        if self._held_shield_blocks(
            actor, str(weapon.get("weapon_ref") or ""), hands=hands,
        ):
            raise CombatIntentError("a shield in the other hand blocks two-handed use")

    def _validate_horde_breaker(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """Horde Breaker's second attack, if this intent is one.

        An ordinary attack returns immediately.  A second attack has to prove
        five things the SRD states in one sentence: the Hunter owns the option,
        has not used it this turn, the original target was attacked this turn,
        the new target is a different creature within 5 feet of the original,
        and that creature has not been attacked this turn.
        """

        original_id = str(intent.get("second_target_of") or "")
        if not original_id:
            return
        actor_id = str(intent["actor_id"])
        character = self._feature_character(actor)
        if not self.features.has_feature(
            character, "horde_breaker", owner="subclass:hunter",
        ):
            raise CombatIntentError("this character has no horde breaker")
        if self._feature_used_this_turn(combat, actor_id, "horde_breaker"):
            raise CombatIntentError("horde breaker is already used this turn")
        attacked = self.attacked_this_turn(combat, actor_id)
        if original_id not in attacked:
            raise CombatIntentError(
                "horde breaker needs an attack against the original target this turn"
            )
        target_id = str(intent.get("target_id") or "")
        if target_id == original_id:
            raise CombatIntentError("horde breaker must pick a different target")
        if target_id in attacked:
            raise CombatIntentError(
                "horde breaker cannot attack a creature attacked this turn"
            )
        limit = int(self.features.feature_value(
            character, "horde_breaker", "target_within_of_original_ft", 5,
        ) or 0)
        if self._distance(combat, target_id, original_id) > limit:
            raise CombatIntentError(
                f"horde breaker's target must be within {limit} feet of the original"
            )

    def _validate_superior_prey(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """Superior Hunter's Prey: the second target, and why it qualifies."""

        actor_id = str(intent["actor_id"])
        character = self._feature_character(actor)
        if not self.features.has_feature(
            character, "superior_hunters_prey", owner="subclass:hunter",
        ):
            raise CombatIntentError("this character has no superior hunter's prey")
        if self._feature_used_this_turn(combat, actor_id, "superior_hunters_prey"):
            raise CombatIntentError("superior hunter's prey is already used this turn")
        original_id = str(intent.get("target_id") or "")
        second_id = str(intent.get("second_target_id") or "")
        if not original_id or not second_id:
            raise CombatIntentError("superior hunter's prey needs both targets")
        if second_id == original_id:
            raise CombatIntentError("superior hunter's prey needs a different target")
        if marked_target_id(actor) != original_id:
            raise CombatIntentError(
                "superior hunter's prey needs the creature marked by your Hunter's Mark"
            )
        if not self.mark_damage_this_turn(combat, actor_id, original_id):
            raise CombatIntentError(
                "superior hunter's prey needs mark damage dealt to the marked creature this turn"
            )
        limit = int(self.features.feature_value(
            character, "superior_hunters_prey", "second_target_range_ft", 30,
        ) or 0)
        if self._distance(combat, second_id, original_id) > limit:
            raise CombatIntentError(
                f"superior hunter's prey's target must be within {limit} feet of the marked one"
            )

    def _validate_attack(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any], actor: dict[str, Any],
    ) -> None:
        character = self._feature_character(actor)
        error = validate_uses(self.features, character, intent, "attack")
        if error:
            raise CombatIntentError(error)
        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        # 敌我判断基于 side：companion/player 同为 party，互不视为合法攻击目标。
        actor_side = str(actor.get("side") or "")
        target_side = str(target.get("side") or "")
        if target_side == actor_side or target["hp"] <= 0:
            raise CombatIntentError("attack target must be a living hostile actor")
        conditions = self._effective_conditions(instance, combat, actor)
        if target_id in self._restriction_sources(conditions, "cannot_attack_source"):
            raise CombatIntentError(
                "a charmed creature cannot attack the creature that charmed it"
            )
        weapon = self._declared_attack(actor, intent)
        if weapon is None:
            # 一个 lookup 同时覆盖装备武器、徒手打击与敌人 attack profile：
            # 客户端无法声明角色并不真正拥有的攻击档案。
            raise CombatIntentError("attack profile is not available to the actor")
        # 先算出手部变化，再按变化后的握持判定：规则允许"先拔出再攻击"，
        # 所以这一击可以合法地使用刚拔出的那把武器。
        swap_hands = self._weapon_swap_hands(actor, intent)
        self._validate_grip(actor, weapon, hands=swap_hands)
        self._validate_ammunition(instance, actor, weapon)
        self._validate_two_handed(actor, intent, weapon, hands=swap_hands)
        self._validate_light_extra_attack(combat, actor, intent, weapon)
        self._validate_cleave(combat, actor, intent, weapon)
        self._validate_loading(combat, actor, intent, weapon)
        self._validate_lucky_spend(instance, intent, actor)
        self._validate_sacred_weapon(intent, actor, weapon, combat)
        distance = self._distance(combat, actor["actor_id"], target_id)
        normal_range = int(weapon.get("thrown_range") or weapon.get("range", 5))
        long_range = int(weapon.get("long_range") or normal_range)
        if distance > long_range:
            raise CombatIntentError(f"target is out of range ({distance} > {long_range} feet)")
        self._validate_hit_riders(actor, intent, combat)
        error = validate_weapon_selection(self.features, character, intent, weapon, world_minutes(instance))
        if error:
            raise CombatIntentError(error)
        # 只有真的带了载荷才进这条校验：`validate_cunning_strike` 要读一次
        # 背包（制毒工具），而绝大多数攻击什么也没声明——把它放在无条件路径
        # 上等于给每次攻击加一次全量物品统计。
        if intent.get(CUNNING_STRIKE_EFFECTS_KEY) is not None:
            error = validate_cunning_strike(
                self.features, character, intent,
                target_size=str(target.get("size") or ""),
                # 只有玩家与伙伴有背包权威（`_activation_payload` 就是这两条
                # 路径）；敌人的攻击也会走到这里，`_companion()` 对它会直接报错。
                carried_refs=(
                    owned_ref_counts(self._activation_payload(instance, actor))
                    if actor["kind"] in {"player", "companion"} else None
                ),
                speed=effective_speed(
                    self.catalog.conditions, actor.get("conditions") or {},
                    max(0, int(actor.get("speed", 0) or 0)),
                ),
            )
            if error:
                raise CombatIntentError(error)
        self._validate_attack_state_options(instance, combat, intent, actor, weapon, distance)

    def _validate_multiattack(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """S0-MM3b: one action, N attacks, all of them provable now.

        The whole batch is validated here rather than per attack during
        resolution: "任一击被拒绝则整批拒绝" only holds if nothing can fail
        halfway through.  A missing attack id, an out-of-range part or a branch
        the action does not offer all end the intent before any die is rolled.
        """

        target_id = str(intent.get("target_id") or "")
        target = self._actor_view(instance, combat, target_id)
        if str(target.get("side") or "") == str(actor.get("side") or "") or target["hp"] <= 0:
            raise CombatIntentError("attack target must be a living hostile actor")
        # 字段闭集：`second_target_of` 是 Horde Breaker 的免费第二击通道，
        # 让它出现在 Multiattack 上等于白送一次动作；其余字段（换手、双手、
        # Lucky）都只属于玩家武器意图。
        if set(intent) - {
            "intent_id", "type", "expected_version", "submitted_by", "actor_id",
            "target_id", "action_id", "choice",
            # S0-MM5：池形状按声明的顺序结算，所以 `sequence` 只属于这一形状。
            "sequence",
        }:
            raise CombatIntentError("multiattack intent has unknown field(s)")
        action = self._declared_action(actor, intent)
        if action is None:
            raise CombatIntentError("multiattack profile is not available to the actor")
        parts = self._multiattack_parts(actor, action, intent)
        if not parts:
            raise CombatIntentError("multiattack resolves to no attack")
        distance = self._distance(combat, str(actor["actor_id"]), target_id)
        for attack, _count in parts:
            long_range = int(attack.get("long_range") or attack.get("range", 5))
            if distance > long_range:
                raise CombatIntentError(
                    f"target is out of range for {attack.get('id')} "
                    f"({distance} > {long_range} feet)"
                )

    def _validate_attack_state_options(
        self, instance: Any, combat: dict[str, Any], intent: dict[str, Any],
        actor: dict[str, Any], weapon: dict[str, Any], distance: int,
    ) -> None:
        """Reject a pre-roll declaration the actor cannot legally make now.

        Three facts are re-derived from the authoritative state rather than
        trusted from the panel: whether this is the first attack roll of the
        turn (Reckless), whether any Disadvantage source exists at all
        (Brutal), and whether the once-per-turn budget is already spent.  A
        submitted declaration that fails any of them must fail the intent --
        silently ignoring it would let the client believe it had Advantage
        while the roll was a straight d20.
        """

        reckless = intent.get("reckless_attack")
        brutal = declaration_value(intent, BRUTAL_STRIKE)
        effects = intent.get("brutal_strike_effects")
        if reckless is None and brutal is None and effects is None:
            return
        character = self._feature_character(actor)
        actor_id = str(actor["actor_id"])

        if reckless is not None:
            if not isinstance(reckless, bool):
                raise CombatIntentError("reckless_attack must be a boolean")
            if reckless:
                if not self.features.has_feature(character, "reckless_attack"):
                    raise CombatIntentError(
                        "reckless attack is not available to this actor"
                    )
                condition = str(self.features.feature_value(
                    character, "reckless_attack", "self_condition", "",
                ) or "")
                if condition and condition in (actor.get("conditions") or {}):
                    raise CombatIntentError("this actor is already attacking recklessly")
                if str((combat.get("economy") or {}).get("actor_id") or "") != actor_id:
                    # "When you make your first attack roll on your turn": an
                    # Opportunity Attack happens on someone else's turn.
                    raise CombatIntentError(
                        "reckless attack can only be declared on your own turn"
                    )
                if self._attack_roll_count(combat, actor_id) > 0:
                    raise CombatIntentError(
                        "reckless attack must be declared before the first attack roll"
                    )

        if brutal is not None:
            if not isinstance(brutal, bool):
                raise CombatIntentError("brutal_strike must be a boolean")
            if brutal:
                if not self.features.has_feature(character, "brutal_strike"):
                    raise CombatIntentError(
                        "brutal strike is not available to this actor"
                    )
                ability = str(self.features.feature_value(
                    character, "brutal_strike", "ability", "",
                ) or "")
                if ability and self.attack_ability(actor, weapon) != ability:
                    raise CombatIntentError(
                        "brutal strike requires a Strength-based attack"
                    )
                required = str(self.features.feature_value(
                    character, "brutal_strike", "requires_declaration", "",
                ) or "")
                reckless_condition = str(self.features.feature_value(
                    character, required, "self_condition", "",
                ) or "") if required else ""
                declared_now = bool(reckless) or (
                    reckless_condition in (actor.get("conditions") or {})
                )
                if not declared_now:
                    raise CombatIntentError(
                        "brutal strike requires reckless attack this turn"
                    )
                if self._feature_used_this_turn(combat, actor_id, "brutal_strike"):
                    raise CombatIntentError("brutal strike was already used this turn")
                # "The chosen attack roll mustn't have Disadvantage." 相消不算：
                # 只要来源里存在任何一份 Disadvantage 就不合格，否则"劣势被
                # 另一份优势抵消"会变成 Brutal 的通行证。
                check_mode = self._attack_check_mode(
                    instance, combat, actor, self._actor_view(
                        instance, combat, str(intent["target_id"]),
                    ), self.attack_range_disadvantage(weapon, distance),
                    ability=self.attack_ability(actor, weapon),
                    declared_reckless=bool(reckless),
                    spend_luck=bool(intent.get("spend_luck")),
                )
                if any(
                    str(row.get("mode") or "") == "disadvantage"
                    for row in check_mode["sources"]
                ):
                    raise CombatIntentError(
                        "brutal strike is not available while the attack has disadvantage"
                    )
                self._validate_brutal_strike_effects(
                    character, intent.get("brutal_strike_effects"),
                )
        elif effects:
            # Options without the declaration are a forged submission: ignoring
            # them would let a client believe a push was coming while the roll
            # was a plain one.
            raise CombatIntentError(
                "brutal strike effects require a brutal strike declaration"
            )

    def _validate_brutal_strike_effects(
        self, character: Any, submitted: Any,
    ) -> None:
        """Every chosen option must be one this level unlocks, at most ``cap``."""

        allowed = [str(value) for value in self.features.feature_value(
            character, "brutal_strike", "effect_options", [],
        ) or []]
        if self.features.has_feature(character, "improved_brutal_strike"):
            allowed.extend(
                str(value) for value in self.features.feature_value(
                    character, "improved_brutal_strike",
                    "additional_effect_options", [],
                ) or []
            )
        state = self.features.class_state(character)
        level = int(state[1]) if state else 0
        cap = int(self.features.feature_value(
            character, "brutal_strike", "effects_per_use", 0,
        ) or 0)
        if level >= int(self.features.feature_value(
            character, "brutal_strike", "upgrade_level", 0,
        ) or 0):
            cap = int(self.features.feature_value(
                character, "brutal_strike", "effects_per_use_upgrade", cap,
            ) or cap)
        if submitted is None:
            return
        if not isinstance(submitted, list) or any(
            not isinstance(value, str) for value in submitted
        ):
            raise CombatIntentError("brutal strike effects must be a list of ids")
        if len(submitted) > cap:
            raise CombatIntentError(
                f"brutal strike allows at most {cap} effect(s) at this level"
            )
        if len(set(submitted)) != len(submitted):
            raise CombatIntentError(
                "brutal strike cannot apply the same effect twice"
            )
        unavailable = sorted(set(submitted) - set(allowed))
        if unavailable:
            raise CombatIntentError(
                f"brutal strike effect is not available: {unavailable[0]}"
            )

    @staticmethod
    def _attack_roll_count(combat: dict[str, Any], actor_id: str) -> int:
        """Attack rolls this actor has already made in the current turn."""

        record = (combat.get("attack_rolls") or {}).get(str(actor_id))
        if not isinstance(record, dict):
            return 0
        if int(record.get("round", -1)) != int(combat.get("round", 0)):
            return 0
        if int(record.get("turn_index", -1)) != int(combat.get("turn_index", 0)):
            return 0
        return int(record.get("count", 0) or 0)

    def _validate_hit_riders(
        self, actor: dict[str, Any], intent: dict[str, Any], combat: dict[str, Any],
    ) -> None:
        """Reject a rider choice the actor cannot legally make right now.

        Every rejection happens here rather than being silently ignored at
        resolve time: a submitted ``stunning_strike`` the actor cannot pay for
        must fail the intent, not quietly produce a plain hit.
        """

        unarmed_type = intent.get("unarmed_damage_type")
        rider_type = intent.get("rider_damage_type")
        stunning = declaration_value(intent, STUNNING_STRIKE)
        if intent.get("open_hand_choice") is not None:
            raise CombatIntentError(
                "open hand technique applies only to Flurry of Blows attacks"
            )
        if unarmed_type is None and rider_type is None and stunning is None:
            return
        weapon = self._declared_attack(actor, intent)
        if weapon is None:
            raise CombatIntentError("attack profile is not available to the actor")
        character = self._feature_character(actor)

        if unarmed_type is not None:
            if not weapon.get("unarmed"):
                raise CombatIntentError(
                    "an unarmed damage type applies to Unarmed Strikes only"
                )
            allowed = self.features.feature_value(
                character, "empowered_strikes", "unarmed_damage_type_choices", [],
            ) or []
            if unarmed_type not in allowed:
                raise CombatIntentError(
                    "the declared unarmed damage type is not available"
                )

        if rider_type is not None:
            allowed_types: set[str] = set()
            kind = rider_attack_kind(weapon)
            for feature_id in ("divine_strike", "primal_strike"):
                if not self.features.has_feature(character, feature_id):
                    continue
                kinds = {
                    str(value) for value in self.features.feature_value(
                        character, feature_id, "attack_kinds", [],
                    ) or []
                }
                if kind not in kinds and not (
                    kind != "unarmed_strike" and "weapon" in kinds
                ):
                    # 这一击不在该 rider 的 attack_kinds 里：声明它就是声明了一件
                    # 结算期会静默丢掉的事，必须当场拒绝。
                    continue
                values = self.features.feature_value(
                    character, feature_id, "damage_types", [],
                ) or []
                allowed_types.update(str(value) for value in values)
            if rider_type not in allowed_types:
                raise CombatIntentError(
                    "the declared rider damage type is not available"
                )

        if stunning is not None:
            if not isinstance(stunning, bool):
                raise CombatIntentError("stunning_strike must be a boolean")
            if not stunning:
                return
            if not self.features.has_feature(character, "stunning_strike"):
                raise CombatIntentError("stunning strike is not available to this actor")
            if not (weapon.get("unarmed") or weapon.get("ability_choice")):
                raise CombatIntentError(
                    "stunning strike requires a Monk weapon or an Unarmed Strike"
                )
            if self._feature_used_this_turn(
                combat, str(actor["actor_id"]), "stunning_strike",
            ):
                raise CombatIntentError("stunning strike was already used this turn")
            cost = int(
                self.features.feature_value(
                    character, "stunning_strike", "focus_cost", 0,
                ) or 0
            )
            current = 0
            for entry in actor.get("class_resources") or []:
                if isinstance(entry, dict) and str(entry.get("id")) == "focus_points":
                    current = int(entry.get("current", 0) or 0)
            if cost and current < cost:
                raise CombatIntentError(
                    "not enough Focus Points for a stunning strike"
                )

    def _validate_ammunition(
        self, instance: Any, actor: dict[str, Any], weapon: dict[str, Any],
    ) -> None:
        """A ranged profile that declares ammunition needs a carried copy.

        Companions have no live backpack in this slice; their ranged attacks
        keep the pre-D3 behaviour instead of inventing an inventory authority.
        """

        raw = weapon.get("ammunition_item_ref")
        if not raw or actor["kind"] != "player":
            return
        try:
            item_ref = canonical_item_ref(raw)
        except AttunementError as exc:
            raise CombatIntentError(str(exc)) from None
        payload = instance.get_character_sheet(actor["id"])
        if inventory_ref_counts(payload).get(item_ref, 0) < 1:
            raise CombatIntentError("no ammunition for this attack")
