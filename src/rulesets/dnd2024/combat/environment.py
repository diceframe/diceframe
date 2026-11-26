"""Cover, obscurement, and aura projection over the battlefield."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.character.primitives import ABILITY_IDS
from .check_modes import condition_flags
from .obscurement import (
    best_obscurement,
    effective_level,
    obscurement_row,
    zone_covers,
)
from src.rulesets.dnd2024.features.resources import long_effect_active
from .primitives import world_minutes, SMITE_OF_PROTECTION_CONDITION

if TYPE_CHECKING:
    from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
    from src.rulesets.dnd2024.combat.catalog import Dnd2024CombatCatalog


def _level_for_bonus(bonus: int) -> str:
    """The ladder level a declared bonus corresponds to."""

    if bonus >= 5:
        return "three_quarters"
    if bonus >= 2:
        return "half"
    return "none"


class CombatEnvironmentMixin:
    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by the combat engine and its other mixins; no runtime state.
        def _actor_view(self, instance: Any, combat: dict[str, Any], actor_id: str) -> dict[str, Any]: ...
        def _all_targets(self, instance: Any, combat: dict[str, Any]) -> list[dict[str, Any]]: ...
        _cover_depth: int
        def _distance(self, combat: dict[str, Any], first: str, second: str) -> int: ...
        @staticmethod
        def _feature_character(actor: dict[str, Any]) -> dict[str, Any]: ...
        @staticmethod
        def _initiative_rank(combat: dict[str, Any], actor_id: str) -> tuple[int, str]: ...
        @staticmethod
        def _position(combat: dict[str, Any], actor_id: str) -> int: ...
        catalog: Dnd2024CombatCatalog
        features: Dnd2024ClassFeatureResolver

    def _projected_auras(
        self, instance: Any, combat: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Every base aura a living, conscious party member projects right now.

        One row per (holder, base aura).  ``range_ft`` already folds in that
        holder's own Aura Expansion, so a level-18 paladin widens only their own
        aura and a level-6 ally beside them keeps 10 feet -- the expansion is a
        property of who owns it, never a global maximum.

        The emanation is off as a whole whenever the holder cannot sustain it:
        any condition that implies Incapacitated disables it, and so does being
        at 0 HP or carrying the ``dead`` condition.  That second half cannot be
        left to the condition registry alone -- ``dead`` is not a catalog
        condition yet, and the reducer removes ``unconscious`` the moment it
        marks a creature dead, so a corpse would otherwise keep radiating a
        bonus to its allies.  Enemies never project a class aura, which is why
        the roster scan starts from ``side == "party"`` rather than from every
        combatant.
        """

        rows: list[dict[str, Any]] = []
        for ally in self._all_targets(instance, combat):
            if str(ally.get("side") or "") != "party":
                continue
            holder_id = str(ally["actor_id"])
            actor = self._actor_view(instance, combat, holder_id)
            conditions = actor.get("conditions") or {}
            flags = condition_flags(self.catalog.conditions, conditions)
            if (
                flags.get("incapacitated")
                or "dead" in conditions
                or int(actor.get("hp", 1)) <= 0
            ):
                continue
            character = self._feature_character(actor)
            owned = self.features.active_features_for(character)
            for definition in owned:
                # 有些光环不是"拥有就一直开着"：Holy Nimbus 要先花附赠
                # 动作点亮十分钟，它生效与否由 long effect 决定。
                required_effect = str(
                    definition.parameters.get("requires_long_effect") or ""
                )
                if required_effect:
                    character_row = self._feature_character(actor)
                    if long_effect_active(
                        character_row, required_effect, world_minutes(instance),
                    ) is None:
                        continue
                declared = definition.parameters.get("aura_range_ft")
                if (
                    isinstance(declared, bool) or not isinstance(declared, int)
                    or declared < 1
                ):
                    continue
                range_ft = declared
                for extension in owned:
                    if extension.parameters.get("aura_expands_feature_id") != definition.id:
                        continue
                    override = extension.parameters.get("aura_range_override_ft")
                    if (
                        isinstance(override, int) and not isinstance(override, bool)
                        and override >= 1
                    ):
                        range_ft = max(range_ft, override)
                rows.append({
                    "holder_id": holder_id,
                    "definition": definition,
                    "range_ft": range_ft,
                    "character": character,
                    "owned": owned,
                })
        return rows

    def _saving_throw_aura_bonus(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
    ) -> dict[str, Any]:
        """The single best saving-throw aura bonus covering the target.

        RAW lets a creature inside two auras choose one.  Settling that on the
        sheet would need a prompt the passive path cannot show, so the highest
        bonus wins and equal bonuses break on initiative order and then actor
        id.  Bonuses never stack: ``sources`` names the winner alone.

        An uncovered target gets ``{"bonus": 0, "sources": []}`` so every caller
        can emit ``aura_bonus``/``aura_sources`` unconditionally and keep the
        existing event shape for characters who never met a paladin.
        """

        target_id = str(target.get("actor_id") or "")
        if not target_id or str(target.get("side") or "") != "party":
            return {"bonus": 0, "sources": []}
        best: dict[str, Any] | None = None
        best_rank: tuple[int, str] = (0, "")
        for row in self._projected_auras(instance, combat):
            definition = row["definition"]
            ability = definition.parameters.get("aura_save_bonus_ability")
            if not isinstance(ability, str) or ability not in ABILITY_IDS:
                continue
            if self._distance(combat, row["holder_id"], target_id) > row["range_ft"]:
                continue
            floor = definition.parameters.get("aura_save_bonus_minimum")
            minimum = floor if isinstance(floor, int) and not isinstance(floor, bool) else 1
            score = (row["character"].get("abilities") or {}).get(ability)
            modifier = ability_modifier(int(score)) if isinstance(score, int) else 0
            bonus = max(minimum, modifier)
            rank = self._initiative_rank(combat, row["holder_id"])
            if best is None or bonus > best["bonus"] or (
                bonus == best["bonus"] and rank < best_rank
            ):
                best = {
                    "bonus": bonus,
                    "feature_id": definition.id,
                    "owner": definition.owner,
                    "source_actor_id": row["holder_id"],
                    "range": row["range_ft"],
                    "ability": ability,
                    "ability_modifier": modifier,
                }
                best_rank = rank
        if best is None:
            return {"bonus": 0, "sources": []}
        return {"bonus": best.pop("bonus"), "sources": [best]}

    def cover_level(
        self, instance: Any, combat: dict[str, Any], actor_id: str,
    ) -> str:
        """Just the level: what "behind three-quarters cover" rules ask about.

        深度计数：灵光投影内部会经 ``_all_targets`` 重建 actor 视图，视图又
        回头查询掩护等级——内层（depth>0）一律答 ``none``，环在此断开。
        """

        # ``_cover_depth`` is a slot of the engine dataclass; mypy checks the
        # write against this mixin's empty ``__slots__`` and needs a waiver.
        self._cover_depth += 1  # type: ignore[misc]
        try:
            side = "enemy" if str(actor_id).startswith("enemy:") else "party"
            row = self._cover_record(
                instance, combat, {"actor_id": str(actor_id), "side": side},
            )
            return str(row.get("level") or "none")
        finally:
            self._cover_depth -= 1  # type: ignore[misc]

    def obscurement_level(
        self, combat: dict[str, Any], actor_id: str,
    ) -> str:
        """Just the level: "is he in heavy obscurement?" is the question Hide asks.

        本切片只有 GM 声明一种来源（法术制造的黑暗/浓雾留给对应法术切片），
        但合并仍然走 ``best_obscurement``：声明优先的语义与掩护一致，来源多起来
        时不需要改消费者。
        """

        return str(self.obscurement_row(combat, actor_id)["level"])

    def obscurement_row(
        self, combat: dict[str, Any], actor_id: str,
    ) -> dict[str, Any]:
        """The one answer shape: level, its two effects, and where it came from."""

        levels: list[str] = []
        sources: list[str] = []
        declared = (combat.get("obscurement") or {}).get(str(actor_id))
        if isinstance(declared, dict):
            level = str(declared.get("level") or "none")
            if level != "none":
                levels.append(level)
                sources.append(str(declared.get("source") or "gm"))
        position = self._position(combat, str(actor_id))
        for zone_id, zone in sorted((combat.get("zones") or {}).items()):
            if not isinstance(zone, dict):
                continue
            if not zone_covers(zone, int(zone.get("origin", 0) or 0), position):
                continue
            levels.append(str(zone.get("level") or "none"))
            sources.append(f"zone:{zone_id}")
        if not levels:
            return obscurement_row("none")
        return obscurement_row(best_obscurement(levels), sources)

    def effective_obscurement(
        self, instance: Any, combat: dict[str, Any], observer_id: str, target_id: str,
    ) -> dict[str, Any]:
        """What the observer actually experiences when looking at the target.

        The only place that answers "can he see it": consumers (attack
        Disadvantage, Perception, the Hide prerequisite) must read this instead
        of the raw level, because the same ``heavily`` means three different
        things to normal sight, Darkvision and Blindsight.
        """

        row = self.obscurement_row(combat, target_id)
        if row["level"] == "none":
            return {**row, "bypass": ""}
        observer = self._actor_view(instance, combat, observer_id)
        senses = observer.get("senses")
        magical = any(
            bool((combat.get("zones") or {}).get(source.removeprefix("zone:"), {}).get("magical"))
            for source in row["sources"]
            if source.startswith("zone:")
        )
        level, bypass = effective_level(
            str(row["level"]), magical=magical, senses=senses,
            distance_ft=self._distance(combat, observer_id, target_id),
        )
        resolved = obscurement_row(level, row["sources"])
        resolved["bypass"] = bypass
        return resolved

    def _zone_cover_level(
        self, combat: dict[str, Any], actor_id: str, side: str,
    ) -> str:
        """The best Half Cover an area gives this creature right now."""

        if not actor_id:
            return "none"
        position = int((combat.get("positions") or {}).get(actor_id, 0) or 0)
        level = "none"
        for zone in (combat.get("zones") or {}).values():
            if not isinstance(zone, dict) or not zone.get("half_cover"):
                continue
            if not zone_covers(zone, int(zone.get("origin", 0) or 0), position):
                continue
            if str(zone.get("beneficiaries") or "all") == "allies" and side != "party":
                continue
            level = "half"
        return level

    def _cover_record(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
    ) -> dict[str, Any]:
        """The best cover this creature has right now: Smite of Protection.

        Cover is positional and momentary, so it is answered by a query instead
        of being written onto anyone.  The condition lives on the Paladin, and
        the reach comes from their own Aura of Protection -- which means an ally
        who walks out loses it and one who walks back in (while it is still
        running) gets it again, without a second copy of the radius.

        Bonuses never stack, with each other or with a higher cover that a
        future slice may add: the best single source wins, and several Paladins
        holding the same benefit still give out exactly one +2.
        """

        from .cover import cover_row

        target_id = str(target.get("actor_id") or "")
        declared = (combat.get("cover") or {}).get(target_id)
        if isinstance(declared, dict):
            # 声明优先：桌子说了"他躲在箱子后面"，任何派生来源都不该悄悄反对。
            source = str(declared.get("source") or "gm")
            return cover_row(str(declared.get("level") or "none"), [f"gm:{source}"])
        # 区域提供的掩护：Nature's Sanctuary 的 Cube 只保护持有者与盟友，
        # 所以受益者由区域自己声明，而不是"在区域里就算"。
        zone_cover = self._zone_cover_level(combat, target_id, str(target.get("side") or ""))
        if zone_cover != "none":
            # 与 GM 声明同一条路径：区域掩护也直接 return 完整的 cover_row。
            return cover_row(zone_cover, ["zone:sanctuary"])
        empty = cover_row("none")
        if not target_id or str(target.get("side") or "") != "party":
            return empty
        best: dict[str, Any] | None = None
        for row in self._projected_auras(instance, combat):
            holder_id = str(row["holder_id"])
            holder = self._actor_view(instance, combat, holder_id)
            meta = (holder.get("conditions") or {}).get(SMITE_OF_PROTECTION_CONDITION)
            if not isinstance(meta, dict):
                continue
            # 掩蔽借的是条件自己声明的那道灵光：既不复制半径，也不默认套用
            # 任何一道光环，光环换名或换属性时这里跟着走。
            source_aura = str(meta.get("cover_source_feature_id") or "aura_of_protection")
            if str(row["definition"].id) != source_aura:
                continue
            if self._distance(combat, holder_id, target_id) > int(row["range_ft"]):
                continue
            armor_class_bonus = int(meta.get("cover_armor_class_bonus", 2) or 0)
            dex_save_bonus = int(meta.get("cover_dex_save_bonus", 2) or 0)
            if armor_class_bonus < 1 and dex_save_bonus < 1:
                continue
            rank = self._initiative_rank(combat, holder_id)
            if best is None or (armor_class_bonus, dex_save_bonus) > (
                best["armor_class_bonus"], best["dex_save_bonus"]
            ) or (
                (armor_class_bonus, dex_save_bonus)
                == (best["armor_class_bonus"], best["dex_save_bonus"])
                and rank < best["_rank"]
            ):
                best = {
                    "armor_class_bonus": armor_class_bonus,
                    "dex_save_bonus": dex_save_bonus,
                    "sources": [{
                        "feature_id": "smite_of_protection",
                        "source_actor_id": holder_id,
                        "range_ft": int(row["range_ft"]),
                        "source_spell_ref": str(meta.get("source_spell_ref") or ""),
                    }],
                    "_rank": rank,
                }
        if best is None:
            return empty
        best.pop("_rank", None)
        # 灵光给的是 +2/+5，等级由加值反推：数字仍是它声明的那个，
        # 但下游问"是否处于 ¾ 掩护"时要有等级可答。
        best["level"] = _level_for_bonus(int(best["armor_class_bonus"]))
        return best

    def _cover_save_bonus(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        ability: str, *, axis: tuple[str, str] = ("horizontal", ""),
    ) -> dict[str, Any]:
        """Cover's contribution to one saving throw, which is Dex-only.

        Asked separately from the AC half so a caller never has to remember
        which ability cover helps: anything but Dexterity answers zero and the
        event stays shaped like every other save.  ``axis`` is the effect's
        business: an effect that arrives from above or erupts from the ground
        declares ``ignored`` and never asks about horizontal cover.
        """

        if str(ability or "") != "dex" or axis[0] == "ignored":
            return {"bonus": 0, "sources": [], "level": "none"}
        cover = self._cover_record(instance, combat, target)
        return {
            "bonus": int(cover["dex_save_bonus"]),
            "sources": cover["sources"],
            "level": str(cover.get("level") or "none"),
        }
