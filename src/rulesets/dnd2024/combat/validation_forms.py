"""Form, land and material-component declaration checks.

One of the validation mixins: the engine assembles them into
``CombatValidationMixin``; the class holds no state of its own."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from ..forms import FormState, FormStateError, bootstrap_known_forms, mode_for, restrictions
from ..spells.font_of_magic import declaring_class_level
from .obscurement import LAND_AID_RANGE_FT, SANCTUARY_MOVE_LIMIT_FT, SANCTUARY_RANGE_FT, sanctuary_zone_id
from .primitives import CombatIntentError, actor_kind as _actor_kind, canonical as _canonical

def _cr_value(label: str) -> float:
    text = str(label or "0")
    if "/" in text:
        numerator, denominator = text.split("/")[:2]
        return float(numerator) / float(denominator)
    return float(text)


class CombatValidationFormsMixin:
    """Form, land and material-component declaration checks."""

    __slots__ = ()

    if TYPE_CHECKING:
        def _class_resource_state(self, character: dict[str, Any], resource_id: str) -> dict[str, int]:
            ...
        features: Any
        initialize_state: Any
        _WILD_SHAPE_CR_BY_LEVEL: Any
        bundle: Any
        def _spell_entity(self, spell_ref: str) -> dict[str, Any] | None:
            ...

    def _validate_form_intent(self, instance: Any, intent: dict[str, Any]) -> None:
        """变形只校验"能不能变"，不校验面板怎么合成。

        形态的 CR/Fly 门槛按德鲁伊等级走（SRD p42：2/4/8 级分别 1/4、1/2、1，
        8 级前不能有飞行速度）。这些门槛属于**动作资格**，所以放在校验层，
        而不是放进合成策略里。
        """

        actor_id = str(intent.get("actor_id") or "")
        kind, raw_id = _actor_kind(actor_id)
        if kind != "player":
            raise CombatIntentError("only a player can change shape")
        character = _canonical(instance.get_character_sheet(raw_id))
        level = declaring_class_level(character, "druid")
        # 门控按"是否拥有该特性"，不再按等级硬编码：等级只是授予条件，
        # 特性声明才是权威（多职业/兼职时两者会分叉）。
        if not self.features.has_feature(character, "wild_shape"):
            raise CombatIntentError("Wild Shape starts at druid level 2")
        forms = character.get("forms")
        active = forms.get("active") if isinstance(forms, dict) else None
        if str(intent.get("type")) == "form.revert":
            if not isinstance(active, dict):
                raise CombatIntentError("no shape is active")
            # SRD p42：主动离开形态是 Bonus Action。
            economy = (self.initialize_state(instance).get("combat") or {}).get("economy") or {}
            if int(economy.get("bonus_action", 0) or 0) < 1:
                raise CombatIntentError("the bonus action has already been spent")
            return
        self._validate_wild_shape_form(intent, level)
        self._validate_known_form(character, int(level), str(intent.get("form_ref") or ""))
        economy = (self.initialize_state(instance).get("combat") or {}).get("economy") or {}
        if int(economy.get("bonus_action", 0) or 0) < 1:
            raise CombatIntentError("the bonus action has already been spent")
        remaining = self._class_resource_state(character, "wild_shape")
        if remaining["maximum"] < 1:
            raise CombatIntentError("Wild Shape uses are not initialized")
        if remaining["current"] < 1:
            raise CombatIntentError("no Wild Shape uses left")

    def _validate_wild_shape_form(self, intent: dict[str, Any], level: int) -> None:
        form_ref = str(intent.get("form_ref") or "")
        entry = self._catalog_entry(form_ref)
        if entry is None:
            raise CombatIntentError(f"unknown form reference: {form_ref!r}")
        if str(entry.get("creature_type") or "") != "beast":
            raise CombatIntentError("Wild Shape needs a beast stat block")
        ceiling = next(
            cap for minimum, cap in self._WILD_SHAPE_CR_BY_LEVEL if level < minimum
        )
        if _cr_value(str(entry.get("cr") or "0")) > _cr_value(ceiling):
            raise CombatIntentError(
                f"a level {level} druid cannot take a CR {entry.get('cr')} form"
            )
        if level < 8 and int((entry.get("speeds") or {}).get("fly", 0) or 0) > 0:
            raise CombatIntentError("a druid below level 8 cannot take a flying form")
        modes = intent.get("equipment_modes")
        if isinstance(modes, dict):
            for item_ref, mode in modes.items():
                if not str(item_ref) or str(mode) not in {"drop", "merge", "wear"}:
                    raise CombatIntentError("equipment mode is not supported")
        else:
            mode = str(intent.get("equipment_mode") or "merge")
            if mode not in {"drop", "merge", "wear"}:
                raise CombatIntentError("equipment mode is not supported")

    def _validate_known_form(
        self, character: dict[str, Any], level: int, form_ref: str,
    ) -> None:
        """形态必须"已学会"。

        老存档（`forms.known` 不存在）按推荐四形态兜底：任务书 §4.1.6 要求目录
        覆盖 SRD 推荐的那四个，用它当默认不引入新数据。显式学会之后只认列表。
        """

        forms = character.get("forms")
        known = forms.get("known") if isinstance(forms, dict) else None
        if isinstance(known, dict):
            known = known.get("wild_shape")
        if isinstance(known, list) and known:
            if form_ref not in [str(item) for item in known]:
                raise CombatIntentError(
                    "that form is not among the known forms; learn it on a long rest"
                )
            return
        seeded = bootstrap_known_forms(
            [entry for catalog in self.bundle.list("monster_catalog")
             for entry in catalog.get("entries") or []],
            level,
        )
        if form_ref not in seeded:
            raise CombatIntentError(
                "that form is not among the known forms; learn it on a long rest"
            )

    def _catalog_entry(self, form_ref: str) -> dict[str, Any] | None:
        if not form_ref:
            return None
        for catalog in self.bundle.list("monster_catalog"):
            for entry in catalog.get("entries") or []:
                if str(entry.get("id") or "") == form_ref:
                    return dict(entry)
        return None

    @classmethod
    def _material_restriction(cls, spell: dict[str, Any]) -> str:
        """18 级 Beast Spells 的材料例外（SRD p43）。

        判定只读目录里的两个结构化布尔（`material_cost_specified` /
        `material_consumed`），它们由 `scripts/rulesets/import_srd_spell_materials.py`
        从上游材料文本生成。**不在运行时解析材料描述**：那等于把规则建在
        一个会随文案变化而失效的正则上。

        法术用材料但目录没标这两个布尔 → fail closed（禁止），因为"不知道"
        不能当成"没有价格"。
        """

        components = spell.get("components")
        if not isinstance(components, list):
            return "the spell's components are unknown"
        if not any(str(component).upper().startswith("M") for component in components):
            return ""
        if spell.get("material_consumed"):
            return "that spell consumes its material component"
        if spell.get("material_cost_specified"):
            return "that spell has a material component with a specified cost"
        if "material_consumed" not in spell or "material_cost_specified" not in spell:
            return "the spell's material component is not classified"
        return ""

    def _validate_shaped_spellcasting(
        self, instance: Any, actor_id: str, intent: dict[str, Any],
    ) -> None:
        """变形中的施法限制（SRD p43）。

        2–17 级一律禁止；18 级 Beast Spells 放开，但"材料有价格 / 材料被消耗"
        两类仍然禁止。禁止的是**这个动作**，不是角色的专注——已有的
        Concentration 不会被这里碰到。
        """

        kind, raw_id = _actor_kind(actor_id)
        if kind != "player":
            return
        character = _canonical(instance.get_character_sheet(raw_id))
        forms = character.get("forms")
        active = forms.get("active") if isinstance(forms, dict) else None
        if not isinstance(active, dict):
            return
        try:
            state = FormState.from_payload(active)
        except FormStateError:  # pragma: no cover - reducer guarantees the shape
            return
        if not restrictions(state)["spellcasting"]:
            raise CombatIntentError("a shape-shifted druid cannot cast spells")
        spell = self._spell_entity(str(intent.get("spell_ref") or ""))
        if spell is None:  # pragma: no cover - _validate_spell reports this
            return
        reason = self._material_restriction(spell)
        if reason:
            raise CombatIntentError(f"beast spells cannot cast it: {reason}")

    def _validate_druid_conversion(
        self, instance: Any, intent: dict[str, Any], state: dict[str, Any],
    ) -> None:
        """Wild Resurgence 与 Nature Magician 的资格校验。

        两条都要求"次数不够就不能换"：`current == 0` 才能用法术位换回一次，
        `current >= 1` 才能把次数换成法术位。前者还要求本回合没用过。
        """

        actor_id = str(intent.get("actor_id") or "")
        kind, raw_id = _actor_kind(actor_id)
        if kind != "player":
            raise CombatIntentError("only a player can convert Wild Shape uses")
        character = _canonical(instance.get_character_sheet(raw_id))
        level = declaring_class_level(character, "druid")
        uses = self._class_resource_state(character, "wild_shape")
        combat = state.get("combat") or {}
        if str(intent.get("type")) == "nature_magician":
            if not self.features.has_feature(character, "archdruid"):
                raise CombatIntentError("Nature Magician starts at druid level 20")
            chosen = intent.get("uses")
            if isinstance(chosen, bool) or not isinstance(chosen, int):
                raise CombatIntentError("uses must be an integer")
            if not 1 <= chosen <= min(4, uses["current"]):
                raise CombatIntentError("uses must be between 1 and the uses you have (max 4)")
            rest = self._class_resource_state(character, "archdruid")
            if rest["maximum"] < 1:
                # 资源没初始化就 fail closed：放行等于"没有次数限制"，
                # 而这条特性恰好就是靠次数限制成立的。
                raise CombatIntentError(
                    "Archdruid uses are not initialized on this character"
                )
            if rest["current"] < 1:
                raise CombatIntentError(
                    "Nature Magician needs a long rest before it can be used again"
                )
            return
        direction = str(intent.get("direction") or "")
        if not self.features.has_feature(character, "wild_resurgence"):
            raise CombatIntentError("Wild Resurgence starts at druid level 5")
        if direction == "slot_to_use":
            if uses["current"] != 0:
                raise CombatIntentError("you still have Wild Shape uses left")
            if combat.get("wild_resurgence_turn"):
                raise CombatIntentError("Wild Resurgence was already used this turn")
            slot_level = intent.get("slot_level")
            if isinstance(slot_level, bool) or not isinstance(slot_level, int) or slot_level < 1:
                raise CombatIntentError("a spell slot level is required")
            return
        if direction == "use_to_slot":
            if uses["current"] < 1:
                raise CombatIntentError("no Wild Shape use left to convert")
            rest = self._class_resource_state(character, "wild_resurgence")
            if rest["maximum"] < 1:
                raise CombatIntentError("Wild Resurgence uses are not initialized")
            if rest["current"] < 1:
                raise CombatIntentError(
                    "this direction needs a long rest before it can be used again"
                )
            return
        raise CombatIntentError("wild_resurgence direction is not supported")

    def _form_item_mode(
        self, instance: Any, actor_id: str, item_ref: str,
    ) -> str:
        """The active shape's mode for ``item_ref``, or ``""`` when not shaped."""

        kind, raw_id = _actor_kind(actor_id)
        if kind != "player":
            return ""
        character = _canonical(instance.get_character_sheet(raw_id))
        forms = character.get("forms")
        active = forms.get("active") if isinstance(forms, dict) else None
        if not isinstance(active, dict):
            return ""
        try:
            state = FormState.from_payload(active)
        except FormStateError:  # pragma: no cover - reducer guarantees the shape
            return ""
        return mode_for(state, str(item_ref))

    def _item_is_merged_in_form(
        self, instance: Any, actor_id: str, item_ref: str,
    ) -> bool:
        return self._form_item_mode(instance, actor_id, item_ref) == "merge"

    def _origin_position(
        self, intent: dict[str, Any], combat: dict[str, Any], actor_id: str, limit: int,
    ) -> int:
        """The point an area is placed at: an integer within ``limit`` feet of the actor."""

        origin = intent.get("origin")
        if isinstance(origin, bool) or not isinstance(origin, int):
            raise CombatIntentError("origin must be an integer position")
        position = int((combat.get("positions") or {}).get(actor_id, 0) or 0)
        if abs(origin - position) > limit:
            raise CombatIntentError(f"origin must be within {limit} feet of you")
        return origin

    def _validate_sanctuary_move(
        self, combat: dict[str, Any], actor_id: str, intent: dict[str, Any],
        economy: dict[str, Any],
    ) -> None:
        """Moving the Cube costs a Bonus Action, covers at most 60 feet, and only your own."""

        if int(economy.get("bonus_action", 0) or 0) < 1:
            raise CombatIntentError("the bonus action has already been spent")
        # 区域 id 由服务端按施法者派生。reducer 会给每个法术区域用 caster 回填
        # owner_id，所以"是我放的"不等于"是我的 Sanctuary"：只认 Sanctuary 本身。
        zone = (combat.get("zones") or {}).get(sanctuary_zone_id(actor_id))
        if (
            not isinstance(zone, dict)
            or str(zone.get("kind") or "") != "sanctuary"
            or str(zone.get("owner_id") or "") != actor_id
        ):
            raise CombatIntentError("that sanctuary is not yours")
        origin = self._origin_position(intent, combat, actor_id, SANCTUARY_RANGE_FT)
        if abs(origin - int(zone.get("origin", 0) or 0)) > SANCTUARY_MOVE_LIMIT_FT:
            raise CombatIntentError(
                f"the sanctuary can move at most {SANCTUARY_MOVE_LIMIT_FT} feet"
            )

    def _validate_land_feature(
        self, instance: Any, intent: dict[str, Any], state: dict[str, Any],
    ) -> None:
        """Circle of the Land 的主动能力：谁能不能用、够不够付、点在哪。

        一维投影下"点"就是坐标线上的一个位置：客户端给的 origin 必须落在
        60/120 尺之内，超出即拒绝，不做静默截断。
        """

        actor_id = str(intent.get("actor_id") or "")
        kind, raw_id = _actor_kind(actor_id)
        if kind != "player":
            raise CombatIntentError("only a player can use a land feature")
        character = _canonical(instance.get_character_sheet(raw_id))
        intent_type = str(intent.get("type"))
        feature_id = "natures_sanctuary" if intent_type == "nature_sanctuary" else "lands_aid"
        if not self.features.has_feature(character, feature_id):
            raise CombatIntentError(f"{feature_id} is not granted to this character")
        combat = state.get("combat") or {}
        economy = combat.get("economy") or {}
        mode = str(intent.get("mode") or "create")
        if feature_id == "natures_sanctuary" and mode == "move":
            self._validate_sanctuary_move(combat, actor_id, intent, economy)
            return
        if int(economy.get("action", 0) or 0) < 1:
            raise CombatIntentError("the action has already been spent")
        uses = self._class_resource_state(character, "wild_shape")
        if uses["current"] < 1:
            raise CombatIntentError("no Wild Shape uses left")
        limit = LAND_AID_RANGE_FT if feature_id == "lands_aid" else SANCTUARY_RANGE_FT
        origin = self._origin_position(intent, combat, actor_id, limit)
        if feature_id == "natures_sanctuary":
            # 区域 id 由服务端派生；新建只替换自己的那一个 Cube。
            return
        raw_targets = intent.get("damage_targets") or []
        if not isinstance(raw_targets, list):
            raise CombatIntentError("damage_targets must be a list")
        targets = [str(target) for target in raw_targets]
        if len(set(targets)) != len(targets):
            # 同一个目标写多遍不会多救一次，只会多挨几段伤害。
            raise CombatIntentError("a damage target is listed more than once")
        positions = combat.get("positions") or {}
        inside = {
            actor for actor, pos in positions.items()
            if abs(int(pos) - origin) <= 10
        }
        for target in targets:
            if target not in inside:
                raise CombatIntentError("a damage target is outside the sphere")
        heal_target = str(intent.get("heal_target") or "")
        if heal_target and heal_target not in inside:
            raise CombatIntentError("the healing target is outside the sphere")
        if not targets and not heal_target and inside:
            raise CombatIntentError("the sphere contains creatures; choose targets")
