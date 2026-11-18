"""Authoritative actor and action views for D&D 2024 combat."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from .triggered_spells import free_cast_resource

from src.engine.modules import ruleset_runtime
from src.rulesets.dnd2024.features import CombatCapabilityView
from src.rulesets.dnd2024.features.combat import martial_arts_profile
from src.rulesets.dnd2024.features.resources import (
    OVERCHANNEL_FEATURE_ID,
    OVERCHANNEL_OWNER,
    OVERCHANNEL_SPELL_CLASS,
)
from src.rulesets.dnd2024.spells.casting import spell_caster, spellcasting_projection

from .check_modes import sense_declarations
from .check_windows import LUCKY_RESOURCE
from .primitives import (
    DIVINE_SENSE_CONDITION,
    FEATURE_STATE_KEYS,
    CombatIntentError,
    UNARMED_STRIKE_REF,
    actor_kind as _actor_kind,
    actor_side as _actor_side,
    canonical as _canonical,
    companion_actor as _companion_actor,
    enemy_actor as _enemy_actor,
    player_actor as _player_actor,
)


BENEFICIAL_MODES = frozenset({"healing", "buff", "stabilize", "temp_hp", "teleport", "revive"})


def _target_side(mode: str) -> str:
    """Server-owned target side for a spell effect, matching validation."""

    if mode in BENEFICIAL_MODES:
        return "ally"
    if mode == "dispel":
        return "any"
    return "enemy"


class CombatViewMixin:
    """Project canonical combat state into presentation-safe actor views."""

    __slots__ = ()

    def _companion(self, instance: Any, raw_id: str) -> dict[str, Any]:
        """从 DND party companion 权威状态读取一个活跃的 AI 队友。"""
        state = ruleset_runtime.state(instance)
        party = state.get("party") if isinstance(state, dict) else None
        companions = party.get("companions") if isinstance(party, dict) else None
        companion = companions.get(raw_id) if isinstance(companions, dict) else None
        if not isinstance(companion, dict) or not companion.get("active", True):
            raise CombatIntentError("companion actor does not exist")
        return companion

    def _active_companions(self, instance: Any) -> dict[str, dict[str, Any]]:
        state = ruleset_runtime.state(instance)
        party = state.get("party") if isinstance(state, dict) else None
        companions = party.get("companions") if isinstance(party, dict) else None
        if not isinstance(companions, dict):
            return {}
        return {
            companion_id: companion
            for companion_id, companion in companions.items()
            if isinstance(companion, dict) and companion.get("active", True)
        }

    def _actor_view(self, instance: Any, combat: dict[str, Any], actor_id: str) -> dict[str, Any]:
        kind, raw_id = _actor_kind(actor_id)
        if kind == "player":
            if raw_id not in instance.players:
                raise CombatIntentError("player actor does not exist")
            character = _canonical(instance.get_character_sheet(raw_id))
            return self._with_hands(combat, self._player_view(raw_id, character))
        if kind == "companion":
            # Companion 是"己方角色"：复用 player canonical 视图结构与装备/法术
            # 目录，不走 enemy attack profile。
            character = self._companion(instance, raw_id).get("ruleset_character") or {}
            view = self._player_view(raw_id, character)
            view["actor_id"] = _companion_actor(raw_id)
            view["kind"] = "companion"
            return self._with_hands(combat, view)
        if kind == "enemy":
            enemy = combat.get("enemies", {}).get(raw_id)
            if not isinstance(enemy, dict):
                raise CombatIntentError("enemy actor does not exist")
            return {
                "actor_id": actor_id, "kind": "enemy", "id": raw_id, "side": "enemy",
                "hp": int(enemy.get("hp", 0) or 0),
                "max_hp": int(enemy.get("max_hp", 0) or 0),
                "armor_class": int(enemy.get("armor_class", 10) or 10),
                "speed": int(enemy.get("speed", 30) or 30),
                "abilities": deepcopy(enemy.get("abilities") or {}),
                "saving_throws": deepcopy(enemy.get("saving_throws") or {}),
                "attacks": deepcopy(enemy.get("attacks") or []),
                "conditions": deepcopy(enemy.get("conditions") or {}),
                "death_saves": {}, "equipment_refs": [], "spell_refs": [], "slots": {},
                "weapon_category_refs": [], "proficiency_bonus": 0,
                "creature_type": str(enemy.get("creature_type") or ""),
                "counterspell_dc": int(enemy.get("counterspell_dc", 0) or 0),
                "skill_values": {}, "skill_refs": [], "concentration": None,
            }
        raise CombatIntentError("actor_id is invalid")

    def _actor_view_from_data(
        self, instance: Any, enemies: dict[str, dict[str, Any]], actor_id: str,
        companions: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kind, raw_id = _actor_kind(actor_id)
        if kind == "player":
            return self._player_view(raw_id, _canonical(instance.get_character_sheet(raw_id)))
        if kind == "companion":
            companion = (companions or {}).get(raw_id) or self._companion(instance, raw_id)
            view = self._player_view(raw_id, companion.get("ruleset_character") or {})
            view["actor_id"] = _companion_actor(raw_id)
            view["kind"] = "companion"
            return view
        enemy = enemies[raw_id]
        return {
            "actor_id": actor_id, "kind": "enemy", "hp": enemy["hp"],
            "speed": enemy["speed"], "conditions": {}, "build": {},
        }

    def _player_view(self, uid: str, character: dict[str, Any]) -> dict[str, Any]:
        class_magic = character.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        conditions = character.get("conditions")
        conditions = conditions if isinstance(conditions, dict) else {}
        resources = character.get("resources")
        resources = resources if isinstance(resources, dict) else {}
        feature_state = character.get("features")
        feature_state = feature_state if isinstance(feature_state, dict) else {}
        subclass_ref = feature_state.get("subclass_ref")
        subclass_ref = subclass_ref if isinstance(subclass_ref, str) else ""
        # 职业资源与 feature-derived 攻击参数在此一次性投影：后面的 combat 代码
        # 只消费结果，不再各自解析职业等级或 level -> 武艺骰。
        feature_projection = self.features.actor_projection(character)
        # 起源专长（魔法学徒）授予的戏法/1 环法术与职业特性授予并列：
        # 各自带施法属性、攻击加值与 DC，结算时按法术来源取值。
        #
        # 常驻授予还要带 `automation_status`：它只由"这个法术真的有可结算
        # 效果"派生，不手写。目录里存在一条法术不等于能施放，客户端也必须
        # 能从同一个字段看出来。
        casting = spellcasting_projection(character, self.features, feature_projection)
        spell_grants = [
            {
                **row,
                "automation_status": (
                    "automated"
                    if str(row.get("spell_ref", "")).removeprefix("spell:")
                    in self.catalog.spell_effects
                    else "not_automated"
                ),
            }
            for row in casting["spell_grants"]
        ]
        # 免费施法槽是独立资源，不是"这个法术免费"的布尔值：每槽自报
        # 固定环阶、recharge、剩余次数与所选法术，结算时按 (slot, level)
        # 匹配一个来源。
        free_cast_slots = casting["free_cast_slots"]
        view: dict[str, Any] = {
            "actor_id": _player_actor(uid), "kind": "player", "id": uid, "side": "party",
            "hp": int(resources.get("hp", 0) or 0),
            "max_hp": int(resources.get("max_hp", 0) or 0),
            "temp_hp": int(resources.get("temp_hp", 0) or 0),
            "armor_class": int(character.get("derived", {}).get("armor_class", 10) or 10),
            # 护甲力量惩罚已经由 ``derive_speed`` 扣过一次。战斗侧若在这里
            # 再减一遍 ``armor_training.speed_penalty``，角色卡与战斗就会报出
            # 两个不同的速度（力量 8 穿链甲：卡 20 尺、战斗 10 尺），而两边
            # 都不报错。派生值只有一份，投影只搬不重算。
            "speed": max(0, int(character.get("derived", {}).get("speed", 30) or 30)),
            "armor_training": deepcopy(
                character.get("derived", {}).get("armor_training") or {}
            ),
            "abilities": deepcopy(character.get("abilities") or {}),
            "saving_throws": deepcopy(character.get("derived", {}).get("saving_throws") or {}),
            "skill_values": deepcopy(character.get("proficiencies", {}).get("skill_values") or {}),
            # 技能熟练本身也要投影：Reliable Talent 只对"使用技能熟练"的检定
            # 生效，而加值数字分不清 "+2 因为熟练" 与 "+2 因为属性"。
            "skill_refs": list(character.get("proficiencies", {}).get("skill_refs") or []),
            "proficiency_bonus": int(
                character.get("derived", {}).get("proficiency_bonus", 2) or 2
            ),
            "weapon_category_refs": list(
                character.get("proficiencies", {}).get("weapon_category_refs") or []
            ),
            "equipment_refs": list(character.get("equipment", {}).get("item_refs") or []),
            "spell_refs": casting["spell_refs"],
            "class_spell_refs": casting["class_spell_refs"],
            "slots": deepcopy(class_magic.get("slots_current") or {}),
            # Font of Magic 造的临时槽：单独一栏，绝不并进普通槽表，否则
            # "最多 5 环"和"长休消失"这两件事就没地方可查了。
            "font_slots": casting["font_slots"],
            "spell_grants": deepcopy(spell_grants),
            "free_cast_slots": deepcopy(free_cast_slots),
            "spell_attack_bonus": int(
                character.get("derived", {}).get("spell_attack_bonus", 0) or 0
            ),
            "spell_save_dc": int(character.get("derived", {}).get("spell_save_dc", 0) or 0),
            "spell_ability": str(class_magic.get("ability") or ""),
            "concentration": deepcopy(
                class_magic.get("concentration") if class_magic
                else (character.get("spellcasting") or {}).get("concentration")
            ),
            "conditions": deepcopy(conditions),
            "death_saves": deepcopy(conditions.get("death_saves") or {}),
            "build": deepcopy(character.get("build") or {}),
            "features": {
                "subclass_ref": subclass_ref,
                # Which option a two-way choice committed to (Blessed Strikes,
                # Elemental Fury).  Ownership of the chosen option is decided
                # from this record, so a view that dropped it would read as
                # "never chose" and silently withdraw the feature's mechanics.
                "feature_choices": deepcopy(feature_state.get("feature_choices") or []),
                # Copied verbatim, not sanitised: a corrupted ledger must reach
                # the counter read and fail closed there, never look like zero.
                "usage": deepcopy(feature_state["usage"])
                if feature_state.get("usage") is not None else [],
                # Character-scoped feature state (a re-chosen resistance, the
                # next survival DC) outlives the combat, so the view has to
                # carry it or combat would silently read "never chosen".
                **{
                    feature_id: deepcopy(feature_state[feature_id])
                    for feature_id in FEATURE_STATE_KEYS
                    if isinstance(feature_state.get(feature_id), dict)
                },
            },
            "class_resources": deepcopy(feature_projection["class_resources"]),
            "martial_arts_die": str(feature_projection["unarmed_damage_die"]),
            # Heroic Inspiration is a character-scoped flag: a turn-start grant
            # that combat could not read back would be re-granted every round.
            "heroic_inspiration": deepcopy(character.get("heroic_inspiration")),
            "unarmed_ability_choice": list(feature_projection["unarmed_ability_choice"]),
            "martial_arts_weapon_refs": list(
                feature_projection["martial_arts_weapon_refs"]
            ),
            "ranged_attack_bonus": int(feature_projection["ranged_attack_bonus"]),
            # Light 追加攻击是否吃属性调整值（Two-Weapon Fighting 风格）。
            "light_extra_attack_ability_damage": bool(
                feature_projection["light_extra_attack_ability_damage"]
            ),
        }
        # S0-F18 §4.2.4：感官是角色自己的属性，必须在 actor view 里。挂在
        # cast_spell 那一行会让"法术用尽 / 不在自己回合"时整条感官消失，
        # 而 Blindsight 30 与能否施法无关。
        view["senses"] = sense_declarations(self, view)
        return view

    @staticmethod
    def _spell_caster(actor: dict[str, Any], spell_ref: str) -> dict[str, Any]:
        """The actor as the caster of one spell.

        A class spell uses the class spellcasting numbers; a spell known only
        through an origin feat uses that feat's chosen spellcasting ability.
        """

        return spell_caster(actor, spell_ref)

    def _project_character(self, character: dict[str, Any]) -> dict[str, Any]:
        from src.rulesets.dnd2024.character.builder import Dnd2024CharacterBuilder

        return {
            **Dnd2024CharacterBuilder(self.bundle).project_legacy(
                character, features=self.features,
            ),
            "rule_binding": deepcopy(character["rule_binding"]),
            "ruleset_character": deepcopy(character),
        }

    def _hand_exception_applies(self, actor: dict[str, Any], weapon: dict[str, Any]) -> bool:
        """``two_handed_exception``（目前只有骑枪的骑乘例外）是否生效。"""

        exception = str(weapon.get("two_handed_exception") or "")
        if not exception:
            return False
        return exception == "mounted" and bool(actor.get("mounted"))

    def _default_hands(self, actor: dict[str, Any]) -> dict[str, Any]:
        """Derive the opening grip from the equipment list.

        ``equipment`` is a loadout, not a pose: it says what the character
        carries by default.  This derivation is what turns it into two hands,
        once per encounter, and only a declared swap may change it afterwards.
        A two-handed weapon wins both hands; otherwise one-handed weapons fill
        the right hand first, and shields take whatever hand is left.
        """

        two_handed: str = ""
        one_handed: list[str] = []
        shields: list[str] = []
        for raw in actor.get("equipment_refs") or []:
            ref = str(raw)
            weapon_id = ref.removeprefix("item:")
            weapon = self.catalog.weapons.get(weapon_id)
            if isinstance(weapon, dict):
                if weapon.get("two_handed") and not self._hand_exception_applies(actor, weapon):
                    two_handed = two_handed or ref
                else:
                    one_handed.append(ref)
                continue
            item = self.bundle.get("item", weapon_id) or {}
            if item.get("item_type") == "shield":
                shields.append(ref)
        if two_handed:
            if shields:
                # 盾牌是绑在手臂上的：戴着盾就不可能把两只手都交给双手武器。
                # 这种"实际握不住"的姿势要如实表达出来，让校验拒绝，而不是
                # 在投影里把盾悄悄摘掉。
                return {"left": shields[0], "right": two_handed}
            return {"left": two_handed, "right": two_handed}
        hands: dict[str, Any] = {"left": None, "right": None}
        for ref in one_handed:
            if hands["right"] is None:
                hands["right"] = ref
            elif hands["left"] is None:
                hands["left"] = ref
            else:
                break
        for ref in shields:
            if hands["left"] is None:
                hands["left"] = ref
            elif hands["right"] is None:
                hands["right"] = ref
        return hands

    def hands_for(self, combat: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
        """The actor's current grip: a declared swap, else the opening grip.

        Pure by design -- reading combat must not write combat.  Only a swap
        intent records an override, and that record dies with the encounter.
        """

        stored = (combat.get("hands") or {}).get(str(actor.get("actor_id") or ""))
        if isinstance(stored, dict):
            return {"left": stored.get("left"), "right": stored.get("right")}
        return self._default_hands(actor)

    def _with_hands(self, combat: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
        """Attach the encounter-scoped grip and mounted flag to a party actor."""

        view["mounted"] = bool((combat.get("mounted") or {}).get(view["actor_id"]))
        view["hands"] = self.hands_for(combat, view)
        return view

    def _available_attacks(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        """Every attack profile this actor may declare right now.

        Enemy actors expose their stat-block attacks.  Player-like actors expose
        their equipped weapons plus the canonical Unarmed Strike, which is a
        natural capability rather than an inventory item: it is never part of
        ``equipment.item_refs`` and cannot be removed by equipment changes.
        Equipped weapons stay first so callers that prefer the first usable
        profile keep preferring real weapons.

        A wielded Monk Weapon keeps its canonical identity, damage type,
        properties, range and proficiency; only the *effective* damage formula
        and attack-ability choice are feature-derived (see
        :func:`~src.rulesets.dnd2024.features.combat.martial_arts_profile`),
        and only for the refs the feature boundary reported as benefiting.
        """

        if actor["kind"] == "enemy":
            return deepcopy(actor["attacks"])
        benefitting = {
            str(ref) for ref in actor.get("martial_arts_weapon_refs") or []
        }
        result = []
        for ref in actor["equipment_refs"]:
            weapon_id = str(ref).removeprefix("item:")
            weapon = self.catalog.weapons.get(weapon_id)
            if weapon:
                item = self.bundle.get("item", weapon_id) or {}
                profile = {
                    "weapon_ref": ref,
                    "id": weapon_id,
                    **deepcopy(weapon),
                    "name": str(item.get("name") or weapon.get("name") or weapon_id),
                }
                if str(ref) in benefitting:
                    profile = martial_arts_profile(
                        profile,
                        damage_die=str(actor.get("martial_arts_die") or ""),
                        ability_choice=actor.get("unarmed_ability_choice") or (),
                    )
                result.append(profile)
        unarmed = self._unarmed_strike(actor)
        if unarmed is not None:
            result.append(unarmed)
        return result

    def _divine_sense_projection(
        self, instance: Any, combat: dict[str, Any], viewer_id: str,
    ) -> dict[str, Any]:
        """What Divine Sense shows *its owner*, and nobody else.

        A detection is private information: broadcasting the list of hidden
        Undead would hand every seat the same knowledge and make the ability
        pointless.  This projection is therefore built only for the viewer who
        holds the condition, and it reports position and creature type only --
        not Hit Points, not equipment.

        ``consecrations_supported`` is deliberately false: the baseline has no
        consecration/desecration data, and pretending to have searched would
        be worse than saying the search is not modelled.
        """

        if not viewer_id:
            return {}
        actor_id = _player_actor(str(viewer_id))
        try:
            viewer = self._actor_view(instance, combat, actor_id)
        except CombatIntentError:
            return {}
        condition = (viewer.get("conditions") or {}).get(DIVINE_SENSE_CONDITION)
        if not isinstance(condition, dict):
            return {}
        radius = int(condition.get("range_ft", 60) or 60)
        wanted = [
            str(kind) for kind in (condition.get("creature_types") or [])
            if str(kind)
        ]
        creatures: list[dict[str, Any]] = []
        for other_id in self._combat_actor_ids(instance, combat):
            if other_id == actor_id:
                continue
            try:
                other = self._actor_view(instance, combat, other_id)
            except CombatIntentError:  # pragma: no cover - actor came from combat
                continue
            creature_type = str(other.get("creature_type") or "").lower()
            if creature_type not in wanted:
                continue
            distance = self._distance(combat, actor_id, other_id)
            if distance > radius:
                continue
            creatures.append({
                "actor_id": other_id,
                "creature_type": creature_type,
                "position": self._position(combat, other_id),
                "distance_ft": distance,
            })
        return {
            "range_ft": radius,
            "creature_types": wanted,
            "creatures": creatures,
            "consecrations": [],
            "consecrations_supported": False,
        }

    def _spell_statuses(self, instance, actor, economy, trigger=None, *, is_current=True):
        """Known spells remain visible even when no cast is currently legal."""
        if actor["kind"] not in {"player", "companion"}:
            return []
        available = {
            row["spell_ref"]
            for row in self._available_spells(instance, actor, economy, trigger)
        }
        statuses = []
        for ref in actor["spell_refs"]:
            spell = self.spells.get(ref)
            if spell is None:
                continue
            effect = self.catalog.spell_effects.get(ref.removeprefix("spell:"))
            reason = ""
            if effect is None:
                reason = "not_automated"
            elif not is_current:
                reason = "not_your_turn"
            elif "rage" in actor["conditions"]:
                reason = "raging"
            elif effect["mode"] == "reaction_defense":
                reason = "requires_being_hit"
            elif effect["mode"] == "triggered_damage" and not trigger:
                reason = "requires_melee_hit"
            elif trigger and effect["mode"] != "triggered_damage":
                reason = "finish_melee_hit"
            elif ref not in available:
                cost = "bonus_action" if str(spell["casting_time"]).startswith("Bonus Action") else "action"
                reason = "action_spent" if int(economy.get(cost, 0)) < 1 else "slot_already_spent" if economy.get("slot_spell_cast") else "no_spell_slot"
            statuses.append({"spell_ref": ref, "name": spell["name"], "blocked_reason": reason})
        return statuses

    def _unarmed_strike(self, actor: dict[str, Any]) -> dict[str, Any] | None:
        """Canonical Unarmed Strike profile, or None when content omits it.

        The identity, damage type and reach come from the combat catalog exactly
        once; the feature boundary only supplies the actor-specific effective
        base damage (the Martial Arts die) and ability choice, and returns both
        empty while Martial Arts is not in force, so the profile falls back to
        plain canonical Unarmed Strike.  No combat file re-derives a
        ``level -> die`` table.
        """

        if actor["kind"] not in {"player", "companion"}:
            return None
        profile = self.catalog.unarmed_strike
        if not profile:
            return None
        effective = martial_arts_profile(
            profile,
            damage_die=str(actor.get("martial_arts_die") or ""),
            ability_choice=actor.get("unarmed_ability_choice") or (),
        )
        return {
            **effective,
            "weapon_ref": UNARMED_STRIKE_REF,
            "id": UNARMED_STRIKE_REF,
            "name": self.catalog.labels.get(UNARMED_STRIKE_REF) or UNARMED_STRIKE_REF,
            "unarmed": True,
        }

    def _declared_attack(
        self, actor: dict[str, Any], intent: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve the attack an intent declares against this actor's authority.

        Player-like actors declare ``weapon_ref`` (an equipped item or the
        canonical unarmed strike); enemy stat blocks declare ``attack_id``.
        Validation and resolution share this one lookup so a client cannot
        declare a profile the actor does not actually have.
        """

        if actor["kind"] == "enemy":
            attack_id = str(intent.get("attack_id") or "")
            return next(
                (deepcopy(item) for item in actor["attacks"] if item.get("id") == attack_id),
                None,
            )
        weapon_ref = str(intent.get("weapon_ref") or "")
        return next(
            (
                item for item in self._available_attacks(actor)
                if item["weapon_ref"] == weapon_ref
            ),
            None,
        )

    @staticmethod
    def _feature_character(actor: dict[str, Any]) -> dict[str, Any]:
        """The feature-relevant slice of an actor's canonical character.

        The actor view already carries exactly what the feature boundary reads
        (canonical build, the canonical class resource entries, and the
        currently equipped refs), so combat never reaches back into the instance
        for a second copy of character state.  The round trip is lossless for
        the fields the boundary consumes: ``current`` / ``maximum``, and the
        equipment that decides whether an equipment-gated feature is active.
        """

        class_state: dict[str, Any] = {}
        for entry in actor.get("class_resources") or []:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            class_state[str(entry["id"])] = {
                "current": entry.get("current", 0),
                "maximum": entry.get("maximum", 0),
            }
        features: dict[str, Any] = {
            "subclass_ref": str((actor.get("features") or {}).get("subclass_ref") or ""),
        }
        # The committed option of a two-way choice decides which mechanics are
        # owned; dropping it here would make every option-gated feature read as
        # "never chosen" during combat.
        raw_choices = (actor.get("features") or {}).get("feature_choices")
        if raw_choices is not None:
            features["feature_choices"] = deepcopy(raw_choices)
        raw_usage = (actor.get("features") or {}).get("usage")
        if raw_usage is not None:
            features["usage"] = deepcopy(raw_usage)
        for feature_id in FEATURE_STATE_KEYS:
            stored = (actor.get("features") or {}).get(feature_id)
            if isinstance(stored, dict):
                features[feature_id] = deepcopy(stored)
        return {
            "build": actor.get("build") or {},
            "features": features,
            "abilities": actor.get("abilities") or {},
            "resources": {"class": class_state},
            "equipment": {"item_refs": list(actor.get("equipment_refs") or [])},
            "heroic_inspiration": deepcopy(actor.get("heroic_inspiration")),
        }

    def _available_class_capabilities(
        self, actor: dict[str, Any], economy: dict[str, Any],
        *, include_unavailable: bool = False,
    ) -> tuple[CombatCapabilityView, ...]:
        """Feature-provided combat capabilities for this actor, in catalog order."""

        if actor.get("kind") not in {"player", "companion"}:
            return ()
        return self.features.combat_capabilities(
            self._feature_character(actor),
            economy,
            include_unavailable=include_unavailable,
            actor_id=str(actor.get("actor_id") or ""),
        )

    def _available_spells(
        self, instance: Any, actor: dict[str, Any], economy: dict[str, Any],
        trigger: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if actor["kind"] not in {"player", "companion"}:
            return []
        if "rage" in actor["conditions"]:
            # 狂暴期间不能施法（SRD 5.2.1 Rage: No Concentration or Spells）。
            return []
        result = []
        for ref in actor["spell_refs"]:
            spell = self.spells.get(ref)
            effect = self.catalog.spell_effects.get(str(ref).removeprefix("spell:"))
            if spell is None or effect is None:
                continue
            if effect["mode"] == "triggered_damage" and not trigger:
                continue
            if effect["mode"] == "reaction_defense":
                # 只能在被攻击命中时，通过 hit_reaction_spell 决策施放。
                continue
            cost = "bonus_action" if str(spell["casting_time"]).startswith("Bonus Action") else "action"
            if int(economy.get(cost, 0) or 0) < 1:
                continue
            available_slots = [
                int(level) for level, count in actor["slots"].items()
                if int(count) > 0 and int(level) >= int(spell["level"]) and not economy.get("slot_spell_cast")
            ] if spell["level"] > 0 else [0]
            if spell["level"] > 0 and not economy.get("slot_spell_cast"):
                # 临时槽在普通槽表之外，但施法时它是真槽：不并进来，玩家就
                # 看得到"我造了个槽"却选不了它。
                for level, count in (actor.get("font_slots") or {}).items():
                    if int(count) > 0 and int(level) >= int(spell["level"]):
                        available_slots.append(int(level))
            if spell["level"] > 0 and free_cast_resource(
                actor, ref, int(spell["level"]),
            ):
                available_slots.insert(0, 0)
            available_slots = sorted(set(available_slots))
            if available_slots:
                # 目标声明随法术一起投影：前端只消费服务端语义，不再按
                # spell mode 硬编码"盟友/敌人"。
                spell_range = self._spell_effective_range(actor, spell, effect)
                row = {
                    "spell_ref": ref, "name": spell["name"], "level": spell["level"],
                    "casting_time": spell["casting_time"], "range": spell_range,
                    "mode": effect["mode"], "available_slot_levels": available_slots,
                    "target_side": _target_side(effect["mode"]),
                    "target_count": int(effect.get("target_count", 1) or 1),
                    "upcast_target_count": int(effect.get("upcast_target_count", 0) or 0),
                    "repeat_targets": bool(effect.get("repeat_targets", False)),
                }
                if effect.get("target_scope"):
                    row["target_scope"] = effect["target_scope"]
                if spell_range != int(effect["range"]):
                    # 被特性改写过的射程要能对账：前端显示的是和，不是原文。
                    row["range_base"] = int(effect["range"])
                if effect.get("resist_choice"):
                    row["resist_choice"] = list(effect["resist_choice"])
                if effect["mode"] == "triggered_damage":
                    row["trigger_target_id"] = trigger["target_id"]
                if effect["mode"] == "spell_attack":
                    # Lucky is a feat resource, so it never appears in the
                    # actor's class-resource rows; ask the same authoritative
                    # pool lookup the attack/death-save declarations use.
                    row["lucky_advantage_available"] = self._window_pool_available(
                        instance, str(actor["actor_id"]), LUCKY_RESOURCE,
                    )
                character = self._feature_character(actor)
                max_slot = (
                    int(self.features.feature_value(
                        character, OVERCHANNEL_FEATURE_ID, "maximum_slot_level", 0,
                        owner=OVERCHANNEL_OWNER,
                    ) or 0)
                    if self.features.has_feature(
                        character, OVERCHANNEL_FEATURE_ID, owner=OVERCHANNEL_OWNER,
                    ) else 0
                )
                eligible_slots = [
                    level for level in available_slots if 1 <= level <= max_slot
                ]
                # 列表归属与学派是两个独立字段：Overchannel 认的是
                # `class_refs` 里的 Wizard，不是 `school == evocation`。
                row["overchannel_available"] = bool(
                    max_slot and effect.get("damage")
                    and effect["mode"] in {"spell_attack", "saving_throw", "automatic_damage"}
                    and OVERCHANNEL_SPELL_CLASS in spell["class_refs"]
                    and eligible_slots
                )
                result.append(row)
        return result

    def _all_targets(self, instance: Any, combat: dict[str, Any]) -> list[dict[str, Any]]:
        targets = []
        for uid in instance.players:
            view = self._actor_view(instance, combat, _player_actor(uid))
            targets.append({
                "actor_id": view["actor_id"], "kind": "player", "side": "party", "hp": view["hp"],
                "max_hp": view["max_hp"], "position": self._position(combat, view["actor_id"]),
                "name": str(instance.players[uid].get("character_name") or uid),
                "is_dead": "dead" in view.get("conditions", {}),
            })
        for companion_id in sorted(self._active_companions(instance)):
            view = self._actor_view(instance, combat, _companion_actor(companion_id))
            targets.append({
                "actor_id": view["actor_id"], "kind": "companion", "side": "party", "hp": view["hp"],
                "max_hp": view["max_hp"], "position": self._position(combat, view["actor_id"]),
                "name": str(self._companion(instance, companion_id).get("name") or companion_id),
                "is_dead": "dead" in view.get("conditions", {}),
            })
        for enemy_id in combat.get("enemies", {}):
            view = self._actor_view(instance, combat, _enemy_actor(enemy_id))
            targets.append({
                "actor_id": view["actor_id"], "kind": "enemy", "side": "enemy", "hp": view["hp"],
                "max_hp": view["max_hp"], "position": self._position(combat, view["actor_id"]),
                "name": str(combat["enemies"][enemy_id].get("name") or enemy_id),
                "is_dead": "dead" in view.get("conditions", {}),
            })
        return targets

    def _hostile_targets(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
    ) -> list[dict[str, Any]]:
        kind, _raw = _actor_kind(actor_id)
        actor_side = _actor_side(kind)
        # 敌我判断只基于 side：companion 与 player 同为 party，互不视为敌对。
        return [
            target for target in self._all_targets(instance, combat)
            if target.get("side", _actor_side(target["kind"])) != actor_side
        ]
