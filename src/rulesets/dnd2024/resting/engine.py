"""Deterministic SRD 5.2.1 rest and resource recovery."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.attunement import AttunementError
from src.rulesets.dnd2024.character.attunement import apply_short_rest_attunement
from src.rulesets.dnd2024.character.attunement import owned_ref_counts
from src.rulesets.dnd2024.forms import KnownFormsError, bootstrap_known_forms, replace_one
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.combat.catalog import DAMAGE_TYPES
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.features.models import Dnd2024ClassFeatureCatalog
from src.rulesets.dnd2024.features.resources import reset_feature_usage
from src.rulesets.dnd2024.items.charges import ChargesError, recharge_for_rest
from src.rulesets.dnd2024.progression.catalog import Dnd2024ProgressionCatalog
from src.rulesets.dnd2024.spells.feat_spells import concentration_holder, restore_feat_free_casts
from src.rulesets.dnd2024.spells.font_of_magic import clear_font_slots
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog
from src.rulesets.dnd2024.spells.preparation import PreparationPolicy
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection


class RestError(ValueError):
    """Raised when a rest cannot legally be completed."""


# 升级时 current 如何随新的 maximum 调整。默认值必须保持既有语义（见
# ``sync_resources``）：任何没有显式声明的职业资源都继续沿用旧行为。
RESIZE_POLICIES = ("preserve_spent", "preserve_current")
DEFAULT_RESIZE_POLICY = "preserve_spent"

# 短休预算型恢复（Arcane/Natural Recovery 的槽位、Sorcerous Restoration 的点数）。
# 模式与预算口径都由 rest 目录声明，代码不按职业分支：新增一个同类特性时只需要
# 在内容里写一行 ``short_recovery``。
SHORT_RECOVERY_MODES = ("spell_slots", "points")
SHORT_RECOVERY_BUDGETS = ("half_level_round_up", "half_level_round_down")

# 需要真实耗时的权威仪式（目前只有 Magical Cunning）。它与休息的区别是时间：
# 仪式在自己的权威写里推进 ``minutes`` 分钟，然后才恢复；时长与恢复口径同样由
# 内容声明，代码不按职业分支。
RITUAL_MODES = ("pact_slots",)
RITUAL_BUDGETS = ("half_max_round_up",)


def _canonical(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("ruleset_character")
    return nested if isinstance(nested, dict) else payload


@dataclass(slots=True)
class Dnd2024RestEngine:
    bundle: LoadedRulesetBundle
    progression: Dnd2024ProgressionCatalog = field(init=False)
    rules: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.progression = Dnd2024ProgressionCatalog.from_bundle(self.bundle)
        rules = self.bundle.get("rest_catalog", "srd_recovery")
        if rules is None:
            raise RestError("D&D 2024 rest catalog is missing")
        class_resources = rules.get("class_resources")
        if not isinstance(class_resources, dict) or set(class_resources) != set(
            self.progression.classes
        ):
            raise RestError("rest resource policies must cover every class exactly once")
        for class_id, specs in class_resources.items():
            if not isinstance(specs, list):
                raise RestError(f"class_resources.{class_id} must be an array")
            seen: set[str] = set()
            for spec in specs:
                if not isinstance(spec, dict) or not str(spec.get("id") or ""):
                    raise RestError(f"class_resources.{class_id} has an invalid resource")
                resource_id = str(spec["id"])
                if resource_id in seen:
                    raise RestError(f"class_resources.{class_id} duplicates {resource_id}")
                seen.add(resource_id)
                self._validate_spec(spec, f"class_resources.{class_id}.{resource_id}")
        feat_resources = rules.get("feat_resources")
        if feat_resources is not None:
            if not isinstance(feat_resources, dict):
                raise RestError("rest feat_resources must be an object")
            for feat_id, specs in feat_resources.items():
                if self.bundle.get("feat", str(feat_id)) is None:
                    raise RestError(f"feat_resources.{feat_id} references an unknown feat")
                if not isinstance(specs, list):
                    raise RestError(f"feat_resources.{feat_id} must be an array")
                seen = set()
                for spec in specs:
                    if not isinstance(spec, dict) or not str(spec.get("id") or ""):
                        raise RestError(f"feat_resources.{feat_id} has an invalid resource")
                    resource_id = str(spec["id"])
                    if resource_id in seen:
                        raise RestError(f"feat_resources.{feat_id} duplicates {resource_id}")
                    seen.add(resource_id)
                    self._validate_spec(spec, f"feat_resources.{feat_id}.{resource_id}")
        spell_slots = rules.get("spell_slots")
        if not isinstance(spell_slots, dict) or set(spell_slots) != {"full", "half", "pact"}:
            raise RestError("rest spell-slot policies are incomplete")
        self.rules = rules

    @staticmethod
    def _validate_spec(spec: dict[str, Any], label: str) -> None:
        maximum_fields = [
            key for key in (
                "maximum_track", "maximum_fixed", "maximum_ability_modifier",
                "maximum_proficiency_bonus",
            )
            if key in spec
        ]
        if len(maximum_fields) != 1:
            raise RestError(f"{label} needs one maximum")
        if "maximum_proficiency_bonus" in spec and spec["maximum_proficiency_bonus"] is not True:
            raise RestError(f"{label} maximum_proficiency_bonus must be true")
        resize_policy = spec.get("resize_policy", DEFAULT_RESIZE_POLICY)
        if resize_policy not in RESIZE_POLICIES:
            raise RestError(f"{label} has an unknown resize policy")
        if not str(spec.get("source_ref") or "").startswith("srd-5.2.1:"):
            raise RestError(f"{label} source is invalid")
        requires_subclass = spec.get("requires_subclass")
        # 子职专属的资源池：声明写成 ``subclass:<id>``，与特性目录的 owner 同一套
        # 身份。写错前缀就等于一个永远为假的资格判据，池会静默不生成。
        if requires_subclass is not None and not str(requires_subclass).startswith("subclass:"):
            raise RestError(f"{label} requires_subclass must name a subclass")
        recovery = spec.get("short_recovery")
        if recovery is not None:
            Dnd2024RestEngine._validate_short_recovery(recovery, label)
        ritual = spec.get("ritual")
        if ritual is not None:
            Dnd2024RestEngine._validate_ritual(ritual, label)

    @staticmethod
    def _validate_ritual(ritual: Any, label: str) -> None:
        if not isinstance(ritual, dict):
            raise RestError(f"{label}.ritual must be an object")
        minutes = ritual.get("minutes")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or not 1 <= minutes <= 1440
        ):
            raise RestError(f"{label}.ritual minutes must be an integer from 1 to 1440")
        if ritual.get("mode") not in RITUAL_MODES:
            raise RestError(f"{label}.ritual has an unknown mode")
        if ritual.get("budget") not in RITUAL_BUDGETS:
            raise RestError(f"{label}.ritual has an unknown budget")
        full_feature = ritual.get("full_feature")
        if full_feature is not None and (
            not isinstance(full_feature, str) or not full_feature
        ):
            raise RestError(f"{label}.ritual full_feature must name a feature")

    @staticmethod
    def _validate_short_recovery(recovery: Any, label: str) -> None:
        if not isinstance(recovery, dict):
            raise RestError(f"{label}.short_recovery must be an object")
        mode = recovery.get("mode")
        if mode not in SHORT_RECOVERY_MODES:
            raise RestError(f"{label}.short_recovery has an unknown mode")
        if recovery.get("budget") not in SHORT_RECOVERY_BUDGETS:
            raise RestError(f"{label}.short_recovery has an unknown budget")
        if mode == "spell_slots":
            max_slot_level = recovery.get("max_slot_level")
            if (
                isinstance(max_slot_level, bool)
                or not isinstance(max_slot_level, int)
                or not 1 <= max_slot_level <= 9
            ):
                raise RestError(f"{label}.short_recovery max_slot_level is invalid")
        else:
            track = recovery.get("track")
            if not isinstance(track, str) or not track:
                raise RestError(f"{label}.short_recovery must name a track")

    def sync_resources(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Add or resize class resources without silently refilling spent uses.

        How ``current`` follows a changed ``maximum`` is a declarative,
        per-resource policy (``resize_policy``), so a class can state its own
        semantics without any code branching on a class or resource id:

        * ``preserve_spent`` (the default, and today's behaviour for every
          resource that does not opt in): keep the number of *spent* uses, i.e.
          ``current`` grows together with the maximum;
        * ``preserve_current``: keep a legal ``current`` and clamp it to the new
          maximum, so advancing never hands out free uses.

        A resource that does not exist yet is always created full: the grant is
        the first time the character receives it.
        """

        character = deepcopy(_canonical(payload))
        class_ref, class_id, level = self._class_state(character)
        snapshot = self.progression.snapshot(class_ref, level)
        progression = character.setdefault("progression", {})
        progression.setdefault("mode", "single_class")
        progression["class_ref"] = class_ref
        progression["level"] = level
        progression["tracks"] = deepcopy(snapshot["tracks"])
        progression["content_version"] = snapshot["content_version"]

        resources = character.setdefault("resources", {})
        class_state = resources.setdefault("class", {})
        self._sync_specs(
            self.rules["class_resources"][class_id], class_state, snapshot["tracks"],
            character, level,
        )
        feat_refs = {
            str(ref) for ref in (character.get("features") or {}).get("feat_refs") or []
        }
        for feat_id, specs in (self.rules.get("feat_resources") or {}).items():
            # 专长池的资格判据是 canonical feat_refs；删专长后残留池必须消失，
            # 不能凭旧存档里的键继续“拥有”一次 Lucky。
            qualified = f"feat:{feat_id}" in feat_refs
            self._sync_specs(
                specs, class_state, snapshot["tracks"], character, level,
                skip=not qualified,
            )
        return character

    @staticmethod
    def _owns_spec(spec: dict[str, Any], character: dict[str, Any]) -> bool:
        """Whether the character holds the subclass a resource spec is tied to.

        ``requires_subclass`` keeps a subclass-only pool (Circle of the Land's
        Natural Recovery) off every other member of the class: the rest table is
        keyed by class, so without this gate a Moon druid would be handed a
        counter it can never spend.
        """

        required = str(spec.get("requires_subclass") or "")
        if not required:
            return True
        features = character.get("features")
        features = features if isinstance(features, dict) else {}
        return str(features.get("subclass_ref") or "") == required

    def _sync_specs(
        self, specs: list[dict[str, Any]], class_state: dict[str, Any],
        tracks: dict[str, int], character: dict[str, Any], level: int,
        *, skip: bool = False,
    ) -> None:
        for spec in specs:
            if (
                skip
                or level < int(spec.get("minimum_level", 1) or 1)
                or not self._owns_spec(spec, character)
            ):
                class_state.pop(spec["id"], None)
                continue
            maximum = self._maximum(spec, tracks, character)
            previous = class_state.get(spec["id"])
            previous = previous if isinstance(previous, dict) else {}
            previous_maximum = int(previous.get("maximum", 0) or 0)
            previous_current = int(previous.get("current", previous_maximum) or 0)
            if not previous:
                current = maximum
            elif spec.get("resize_policy", DEFAULT_RESIZE_POLICY) == "preserve_current":
                current = min(maximum, max(0, previous_current))
            else:
                current = min(maximum, max(0, previous_current + max(0, maximum - previous_maximum)))
            class_state[spec["id"]] = {
                "current": current,
                "maximum": maximum,
                "source_ref": spec["source_ref"],
            }

    def complete_short_rest(
        self, payload: dict[str, Any], hit_die_rolls: dict[str, list[int]] | None = None,
        attunement: dict[str, Any] | None = None,
        free_cast: dict[str, Any] | None = None,
        recovery: dict[str, Any] | None = None,
        fiendish_resilience: str | None = None,
        hunter_options: dict[str, Any] | None = None,
        memorize_spell: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if free_cast:
            # Spell Mastery 的研究只发生在长休；短休给这个参数不是"没效果"，
            # 而是用错了入口，直接拒绝比静默忽略更安全。
            raise RestError("free cast slots can only be re-chosen during a long rest")
        character = self.sync_resources(payload)
        self._require_conscious(character, "Short Rest")
        # Fiend 的抗性改选与其它选择一样先校验再落账：非法类型不能留下
        # 已经恢复的 HP 与槽位。
        fiendish_choice = self._plan_fiendish_resilience(
            character,
            fiendish_resilience if fiendish_resilience is not None
            else payload.get("fiendish_resilience"),
        )
        # 预算型恢复先算成计划：超预算、选到没消耗的槽、当天已用过都要在
        # 任何状态变化之前拒绝，这样一次非法选择不会留下半个休息结果。
        recovery_plan = self._plan_short_recovery(
            character, recovery if recovery is not None else payload.get("recovery"),
        )
        # Hunter 的选项替换与 Fiend 的抗性改选同形：先算计划，任何一步非法都
        # 在状态变化之前拒绝。
        hunter_plan = self._plan_hunter_options(
            character,
            hunter_options if hunter_options is not None
            else payload.get("hunter_options"),
        )
        # Memorize Spell 与上面几个同形：先算计划，任何一步非法都在状态变化前拒绝。
        memorize_plan = self._plan_memorize_spell(
            character,
            memorize_spell if memorize_spell is not None
            else payload.get("memorize_spell"),
        )
        events: list[dict[str, Any]] = []
        rolls = hit_die_rolls or {}
        if not isinstance(rolls, dict):
            raise RestError("hit_die_rolls must be an object")
        resources = character["resources"]
        hit_dice = resources.get("hit_dice")
        if not isinstance(hit_dice, dict):
            raise RestError("character hit dice are missing")
        con_modifier = ability_modifier(int(character.get("abilities", {}).get("con", 0) or 0))
        for die, values in rolls.items():
            if die not in hit_dice or not isinstance(values, list):
                raise RestError(f"hit_die_rolls contains unavailable die {die}")
            try:
                sides = int(str(die).removeprefix("d"))
            except ValueError as exc:
                raise RestError(f"invalid hit die {die}") from exc
            if len(values) > int(hit_dice[die]):
                raise RestError(f"not enough {die} Hit Point Dice")
            for roll in values:
                if isinstance(roll, bool) or not isinstance(roll, int) or not 1 <= roll <= sides:
                    raise RestError(f"{die} roll must be an integer from 1 to {sides}")
                before = int(resources.get("hp", 0) or 0)
                healed = max(1, roll + con_modifier)
                resources["hp"] = min(int(resources["max_hp"]), before + healed)
                hit_dice[die] = int(hit_dice[die]) - 1
                events.append({
                    "type": "spend_hit_die",
                    "die": die,
                    "roll": roll,
                    "healed": int(resources["hp"]) - before,
                })
        self._apply_tireless_exhaustion(character, events)
        self._recover_class_resources(character, "short", events)
        self._recover_spell_slots(character, "short", events)
        self._recover_free_cast_slots(character, "short", events)
        events.extend(self._apply_short_recovery(character, recovery_plan))
        events.extend(self._apply_hunter_options(character, hunter_plan))
        events.extend(self._apply_memorize_spell(character, memorize_plan))
        events.extend(self._apply_fiendish_resilience(character, fiendish_choice))
        events.extend(self._reset_relentless_rage_dc(character))
        events.extend(self._apply_attunement(character, payload, attunement))
        events.extend(self._recharge_items(character, payload, "short"))
        return {
            "character": character,
            "events": events,
            "rest": "short",
            "source_ref": self.rules["short_rest"]["source_ref"],
            "requires_elapsed_time_confirmation": True,
        }

    def complete_long_rest(
        self, payload: dict[str, Any], land_terrain: str | None = None,
        prepared_spells: dict[str, list[str]] | None = None,
        free_cast: dict[str, Any] | None = None,
        fiendish_resilience: str | None = None,
        hunter_options: dict[str, Any] | None = None,
        wild_shape_forms: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        character = self.sync_resources(payload)
        self._require_conscious(character, "Long Rest")
        hunter_plan = self._plan_hunter_options(
            character,
            hunter_options if hunter_options is not None
            else payload.get("hunter_options"),
        )
        fiendish_choice = self._plan_fiendish_resilience(
            character,
            fiendish_resilience if fiendish_resilience is not None
            else payload.get("fiendish_resilience"),
        )
        # Land 地形选择先算成一份计划再落账：校验通过之前不动任何状态，
        # 因此一次非法地形不会留下"半套"休息结果。
        terrain_plan = self._plan_land_terrain(
            character, land_terrain if land_terrain is not None
            else payload.get("land_terrain"),
        )
        # Spell Mastery 的"研究法术书"同理：先校验再落账，一项非法替换
        # 不能让长休留下已经恢复的 HP 与槽位。
        free_cast_plan = self._plan_free_cast_rebind(
            character, free_cast if free_cast is not None
            else payload.get("free_cast"),
        )
        # S0-F25：长休可以替换**一个**已知形态。同样先计划再落账——非法替换
        # 不能让长休留下已经恢复的 HP 与槽位。
        form_plan = self._plan_wild_shape_forms(
            character,
            wild_shape_forms if wild_shape_forms is not None
            else payload.get("wild_shape_forms"),
        )
        events: list[dict[str, Any]] = []
        events.extend(self._apply_hunter_options(character, hunter_plan))
        if form_plan is not None:
            character.setdefault("forms", {}).setdefault("known", {})["wild_shape"] = list(form_plan)
            events.append({"type": "wild_shape_forms_replaced", "forms": list(form_plan)})
        resources = character["resources"]
        previous_hp = int(resources.get("hp", 0) or 0)
        resources["hp"] = int(resources.get("max_hp", 0) or 0)
        resources["max_hp_reduction"] = 0
        if resources["hp"] != previous_hp:
            events.append({"type": "restore_hp", "before": previous_hp, "after": resources["hp"]})
        class_ref, _class_id, level = self._class_state(character)
        class_entity = self.bundle.get("class", class_ref.removeprefix("class:")) or {}
        hit_die = f"d{int(class_entity.get('hit_die', 0) or 0)}"
        resources["hit_dice"] = {hit_die: level}
        events.append({"type": "restore_all_hit_dice", "hit_dice": {hit_die: level}})
        conditions = character.setdefault("conditions", {})
        exhaustion = int(conditions.get("exhaustion", 0) or 0)
        conditions["exhaustion"] = max(0, exhaustion - 1)
        if exhaustion:
            events.append({
                "type": "reduce_exhaustion",
                "before": exhaustion,
                "after": conditions["exhaustion"],
            })
        resources["ability_score_reductions"] = {}
        # 造的临时槽先作废，再按普通规则恢复：顺序反过来，"长休后槽位
        # 总数"就会在一瞬间把临时槽算成普通槽。
        removed_font_slots = clear_font_slots(character)
        if removed_font_slots:
            events.append({
                "type": "discard_font_slots",
                "slots": removed_font_slots,
            })
        self._recover_class_resources(character, "long", events)
        self._recover_spell_slots(character, "long", events)
        # 重新研究先落账，再按 recharge 补满：长休结束时被换上的法术槽也是满的，
        # 顺序反了就会把"研究完睡一觉"算成"睡完又花掉一次"。
        events.extend(self._apply_free_cast_rebind(character, free_cast_plan))
        self._recover_free_cast_slots(character, "long", events)
        events.extend(self._apply_land_terrain(character, terrain_plan))
        if prepared_spells:
            events.extend(self._apply_prepared_spells(character, prepared_spells))
        events.extend(self._apply_fiendish_resilience(character, fiendish_choice))
        events.extend(self._reset_relentless_rage_dc(character))
        # 长休作用域的特性使用计数只在这里清零：短休、战斗结束、回合结束、
        # 升级与语言切换都不得动它，否则"首次免费"会被反复刷新。
        for record in reset_feature_usage(character):
            events.append({
                "type": "reset_feature_usage",
                "owner": record["owner"],
                "feature_id": record["feature_id"],
                "scope": record["scope"],
                "before": record["count"],
                "after": 0,
            })
        for feat_ref in restore_feat_free_casts(character):
            events.append({"type": "restore_feat_spell", "feat_ref": feat_ref})
        holder = concentration_holder(character)
        if holder.get("concentration") is not None:
            events.append({"type": "end_concentration", "reason": "long_rest"})
            holder["concentration"] = None
        # 临时 HP 在长休后消失；目录里的法术效果最长 1 小时，八小时后全部到期，
        # 狂暴同理。只移除带服务端法术来源快照的条件，伤病类条件不受影响。
        temp_hp = int(resources.get("temp_hp", 0) or 0)
        if temp_hp:
            resources["temp_hp"] = 0
            events.append({"type": "lose_temp_hp", "before": temp_hp})
        expired = sorted(
            key for key, value in conditions.items()
            if key == "rage" or (isinstance(value, dict) and value.get("source_spell_ref"))
        )
        for key in expired:
            conditions.pop(key, None)
        if expired:
            events.append({"type": "end_spell_effects", "conditions": expired})
        events.extend(self._recharge_items(character, payload, "long"))
        return {
            "character": character,
            "events": events,
            "rest": "long",
            "source_ref": self.rules["long_rest"]["source_ref"],
            "requires_elapsed_time_confirmation": True,
        }

    def parse_prepared_spells(
        self, payload: dict[str, Any], request: Any,
    ) -> dict[str, list[str]]:
        """Validate a Long Rest preparation change without mutating anything.

        The class policy decides whether the change is legal at all (a Bard
        swaps spells on level-up, not on a rest), then the ordinary spell
        selection rules apply with the *rest* budget: the cap is
        ``long_rest_replace`` -- unlimited for Cleric, Druid and Wizard, one for
        Paladin and Ranger -- never the level-up cap.
        """

        character = _canonical(payload)
        class_ref, _class_id, level = self._class_state(character)
        policy = PreparationPolicy.for_class(self.bundle, class_ref)
        if policy is None or not policy.can_change_on_long_rest():
            raise RestError(
                "this class cannot change prepared spells on a long rest"
            )
        if not isinstance(request, dict):
            raise RestError("prepared_spells must be an object with prepared_spell_refs")
        raw_prepared = request.get("prepared_spell_refs")
        if not isinstance(raw_prepared, list) or any(
            not isinstance(ref, str) for ref in raw_prepared
        ):
            raise RestError("prepared_spells must be an object with prepared_spell_refs")
        block = character.get("spellcasting", {}).get("class")
        block = block if isinstance(block, dict) else {}
        current = {
            "cantrip_refs": list(block.get("cantrip_refs") or []),
            "prepared_spell_refs": list(block.get("prepared_spell_refs") or []),
            "spellbook_refs": list(block.get("spellbook_refs") or []),
        }
        raw_cantrips = request.get("cantrip_refs")
        if raw_cantrips is not None and (
            not isinstance(raw_cantrips, list)
            or any(not isinstance(ref, str) for ref in raw_cantrips)
        ):
            raise RestError("prepared_spells cantrip_refs must be an array of spell refs")
        parsed, errors = Dnd2024SpellSelection(self.bundle).validate(
            class_ref,
            level,
            {
                "prepared_spell_refs": raw_prepared,
                "cantrip_refs": (
                    raw_cantrips if raw_cantrips is not None else current["cantrip_refs"]
                ),
                "spellbook_refs": current["spellbook_refs"],
            },
            previous=current,
            context="long_rest",
        )
        if errors:
            raise RestError("; ".join(errors))
        if (
            set(parsed["prepared_spell_refs"]) == set(current["prepared_spell_refs"])
            and set(parsed["cantrip_refs"]) == set(current["cantrip_refs"])
        ):
            # 填不出改动的请求是调用方错误，不是一次成功的休息（同 S0-G3
            # 对空补齐的处理）。
            raise RestError("prepared_spells would not change anything")
        change = {"prepared_spell_refs": list(parsed["prepared_spell_refs"])}
        if raw_cantrips is not None:
            change["cantrip_refs"] = list(parsed["cantrip_refs"])
        return change

    @staticmethod
    def _apply_prepared_spells(
        character: dict[str, Any], change: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        block = character.setdefault("spellcasting", {}).get("class")
        if not isinstance(block, dict):
            raise RestError("character spellcasting block is missing")
        before = list(block.get("prepared_spell_refs") or [])
        after = list(change["prepared_spell_refs"])
        block["prepared_spell_refs"] = after
        if "cantrip_refs" in change:
            block["cantrip_refs"] = list(change["cantrip_refs"])
        return [{"type": "prepared_spells_changed", "before": before, "after": after}]

    def _plan_wild_shape_forms(
        self, character: Mapping[str, Any], request: Any,
    ) -> list[str] | None:
        """长休的形态替换计划；没请求就返回 ``None``（保持原样）。"""

        if request is None:
            return None
        class_ref, class_id, level = self._class_state(character)
        if class_id != "druid":
            raise RestError("only a druid can replace known forms")
        if not isinstance(request, Mapping):
            raise RestError("wild_shape_forms must be an object")
        old_ref = str(request.get("old_form_ref") or "")
        new_ref = str(request.get("new_form_ref") or "")
        entries = [
            entry for catalog in self.bundle.list("monster_catalog")
            for entry in catalog.get("entries") or []
        ]
        known = [
            str(item) for item in (
                (character.get("forms") or {}).get("known", {}).get("wild_shape") or []
            )
        ]
        if not known:
            # 旧存档没有已知列表：战斗校验按推荐形态兜底，长休必须给出同一个答案。
            # 否则这些角色在战斗里"会"的形态，到长休却一个都不认识，永远换不了。
            known = bootstrap_known_forms(entries, level)
        try:
            return replace_one(known, old_ref, new_ref, level, entries)
        except KnownFormsError as exc:
            raise RestError(str(exc)) from exc

    def rest_options(self, character: Mapping[str, Any] | None) -> dict[str, Any]:
        """Read-only description of what a Long Rest requires or may change.

        The rest panel renders from this table instead of guessing: the terrain
        options are the same ``terrain_options`` the commit path validates, and
        the study rows come from the same slot table the rebind planner reads.
        A character with neither gets two empty fields rather than an error, so
        a fighter's rest panel simply shows nothing extra.

        This is a Long-Rest projection by construction: a Short Rest takes no
        terrain and no study, and the Short Rest entry point rejects both.
        """

        payload: dict[str, Any] = character if isinstance(character, Mapping) else {}
        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        definition = resolver.spell_grant_terrain_feature(payload)
        terrain: dict[str, Any] | None = None
        if definition is not None:
            options = [
                str(item) for item in definition.parameters.get("terrain_options") or ()
            ]
            stored = str(
                (resolver.spell_grant_choices(payload).get(definition.id) or {}).get(
                    "terrain"
                ) or ""
            )
            terrain = {
                "owner": definition.owner,
                "feature_id": definition.id,
                "current": stored,
                "options": options,
                "required": True,
            }
        return {
            "land_terrain": terrain,
            "free_cast": Dnd2024SpellSelection(self.bundle).rest_free_cast_rows(payload),
            # 短休那一半也一并交出：面板只有一个休息入口，客户端不该为了知道
            # 「这一觉能拿回什么槽」再去猜一份自己的表。
            "short_recovery": self._recovery_projection(payload),
            "hunter_options": self._hunter_option_projection(payload),
        }

    def _hunter_option_projection(
        self, payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """The Hunter option groups this character may replace at rest.

        One row per group, carrying the current pick and the alternatives the
        catalog declares -- so the panel offers exactly what the commit path
        accepts, and a ranger without the subclass gets an empty list rather
        than an error.
        """

        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        catalog = Dnd2024ClassFeatureCatalog.from_bundle(self.bundle)
        raw = (payload.get("features") or {}).get("feature_choices")
        records = [dict(row) for row in raw] if isinstance(raw, list) else []
        rows: list[dict[str, Any]] = []
        for feature_id, owner in self.HUNTER_OPTION_GROUPS.items():
            if not resolver.has_feature(payload, feature_id, owner=owner):
                continue
            options = sorted(self._option_ids(feature_id))
            current = ""
            for record in records:
                if (str(record.get("owner") or "") != owner
                        or str(record.get("feature_id") or "") != feature_id):
                    continue
                refs = record.get("selected_refs")
                if isinstance(refs, list) and refs:
                    current = str(refs[0]).removeprefix("feature:")
                    break
            rows.append({
                "owner": owner,
                "feature_id": feature_id,
                "current": current,
                "options": options,
                "label": str(catalog.labels.get(feature_id) or feature_id),
            })
        return rows

    def _recovery_projection(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        """``rest_recovery_options`` that degrades instead of raising.

        The projection is read on every rest panel open, including for sheets
        the rest engine would refuse to settle (no single class, unwritable
        state).  A drawing aid must not be the thing that errors there; the
        commit path still fails closed through ``_plan_short_recovery``.
        """

        try:
            return self.rest_recovery_options(dict(payload))
        except RestError:
            return []

    # ------------------------------------------------------------------
    # Circle of the Land terrain
    # ------------------------------------------------------------------

    def _plan_land_terrain(
        self, character: dict[str, Any], requested: Any,
    ) -> dict[str, Any] | None:
        """Validate the land terrain this Long Rest commits, without mutating.

        The choice belongs to the rest, not to creation: a Land Druid picks a
        terrain every Long Rest, and picking the same one again is a legal,
        idempotent choice.  This returns the plan so an invalid terrain fails
        before any Hit Point, slot, or resource has been touched.
        """

        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        definition = resolver.spell_grant_terrain_feature(character)
        text = str(requested or "")
        if definition is None:
            if text:
                raise RestError("this character has no land terrain to choose")
            return None
        options = [
            str(item) for item in definition.parameters.get("terrain_options") or ()
        ]
        stored = str(
            (resolver.spell_grant_choices(character).get(definition.id) or {}).get(
                "terrain"
            ) or ""
        )
        if text and text not in options:
            raise RestError(f"land terrain must be one of {', '.join(options)}")
        return {
            "owner": definition.owner,
            "feature_id": definition.id,
            "options": options,
            "before": stored,
            "after": text,
        }

    def _apply_land_terrain(
        self, character: dict[str, Any], plan: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Commit the planned terrain.

        Switching replaces the old terrain on the same feature slot, so the
        previous land's spells disappear with the source that granted them
        while every other source -- ordinary Druid preparation, another
        subclass's grant -- is left alone.  Omitting the choice never invents
        one: a character that already chose keeps the terrain (and is told
        nothing changed), and one that never chose gets an explicit missing
        event instead of a silent default.
        """

        if plan is None:
            return []
        before, after = plan["before"], plan["after"]
        if not after:
            if before:
                return []
            return [{
                "type": "spell_grant_terrain_missing",
                "owner": plan["owner"],
                "feature_id": plan["feature_id"],
                "options": list(plan["options"]),
            }]
        spellcasting = character.setdefault("spellcasting", {})
        block = spellcasting.get("class")
        if not isinstance(block, dict):
            block = {}
            spellcasting["class"] = block
        choices = block.setdefault("spell_grant_choices", {})
        entry = choices.get(plan["feature_id"])
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["terrain"] = after
        choices[plan["feature_id"]] = entry
        return [{
            "type": "spell_grant_terrain",
            "owner": plan["owner"],
            "feature_id": plan["feature_id"],
            "before": before,
            "after": after,
            "changed": before != after,
        }]

    def _recharge_items(
        self, character: dict[str, Any], payload: dict[str, Any], rest: str,
    ) -> list[dict[str, Any]]:
        held_refs = {
            ref for ref, count in owned_ref_counts(payload).items() if count > 0
        }
        try:
            return recharge_for_rest(character, self.bundle, held_refs, rest)
        except ChargesError as exc:
            raise RestError(str(exc)) from None

    def _apply_attunement(
        self, character: dict[str, Any], payload: dict[str, Any],
        request: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Resolve the optional short-rest attunement payload.

        A rejected binding must not lose the rest: the transition is all-or-
        nothing but the recovery already applied stands, and the rejection is
        recorded for audit instead of swallowed.
        """

        if request is None:
            return []
        if not isinstance(request, dict):
            raise RestError("attunement must be an object")
        try:
            return apply_short_rest_attunement(
                character, payload, request, self.bundle,
                Dnd2024ClassFeatureResolver(self.bundle),
            )
        except AttunementError as exc:
            return [{
                "type": "attunement_rejected",
                "item_ref": request.get("attune") or request.get("unattune"),
                "reason": str(exc),
            }]

    def validate_attunement(
        self, character: dict[str, Any], payload: dict[str, Any],
        request: dict[str, Any] | None,
    ) -> None:
        """Validate a short-rest attunement request without touching live state.

        Party rest joins are validated before every participant is collected;
        otherwise the last member's bad payload only fails after everyone has
        already committed to the rest.
        """

        if request is None:
            return
        if not isinstance(request, dict):
            raise RestError("attunement must be an object")
        probe = deepcopy(character)
        events = self._apply_attunement(probe, payload, request)
        rejected = [
            event for event in events
            if str(event.get("type") or "") == "attunement_rejected"
        ]
        if rejected:
            raise RestError(str(rejected[0].get("reason") or "attunement rejected"))

    def validate_land_terrain(
        self, character: dict[str, Any], terrain: Any,
    ) -> None:
        """Validate a Long Rest land choice without touching live state.

        Same reason as ``validate_attunement``: a party rest is opened only
        after every participant's payload is known to be usable, so a typo in
        the terrain must fail before the session starts rather than halfway
        through the resolution loop.
        """

        if terrain is None:
            return
        probe = deepcopy(character)
        self._plan_land_terrain(probe, terrain)

    # ------------------------------------------------------------------
    # free-cast slots
    # ------------------------------------------------------------------

    def validate_free_cast(
        self, character: dict[str, Any], changes: Any,
    ) -> None:
        """Validate a Long Rest study without touching live state.

        Kept symmetric with ``validate_land_terrain``: an illegal study must
        fail when the rest is *opened*, because a party rest that dies halfway
        through its resolution loop rolls every participant back.
        """

        if changes in (None, {}):
            return
        probe = deepcopy(character)
        self._plan_free_cast_rebind(probe, changes)

    def _plan_free_cast_rebind(
        self, character: dict[str, Any], requested: Any,
    ) -> dict[str, dict[str, Any]]:
        """Validate a Long Rest spell study without mutating the character."""

        if requested in (None, {}):
            return {}
        selection = Dnd2024SpellSelection(self.bundle)
        records, errors = selection.rebind_free_cast(character, requested)
        if errors:
            raise RestError("; ".join(errors))
        return records

    @staticmethod
    def _apply_free_cast_rebind(
        character: dict[str, Any], plan: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not plan:
            return []
        block = character.setdefault("spellcasting", {}).setdefault("class", {})
        stored = block.setdefault("free_cast_slots", {})
        events: list[dict[str, Any]] = []
        for slot_id, record in plan.items():
            before = stored.get(slot_id)
            before_ref = str((before or {}).get("spell_ref") or "")
            after_ref = str(record.get("spell_ref") or "")
            # 计划里带着所有已研究的槽，只有真正换了的那一格才值得记一条事件：
            # 提交一格却回放出两条"某法术换成它自己"，读日志的人会被误导。
            stored[slot_id] = deepcopy(record)
            if before_ref == after_ref:
                continue
            events.append({
                "type": "rebind_free_cast",
                "slot_id": slot_id,
                "before": before_ref,
                "after": after_ref,
            })
        return events

    def _recover_free_cast_slots(
        self, character: dict[str, Any], rest: str, events: list[dict[str, Any]],
    ) -> None:
        """Refill the free-cast slots this rest covers.

        Four Mystic Arcana refill on a Long Rest only, the two Signature slots
        on either rest, and Spell Mastery never (its slots are unlimited and
        carry no counter at all).  A slot that has not been used keeps no
        record, so there is nothing to write back.
        """

        bounds = {
            "long_rest": {"long"},
            "short_rest": {"short", "long"},
            "unlimited": set(),
        }
        block = character.get("spellcasting")
        block = block.get("class") if isinstance(block, dict) else None
        stored = block.get("free_cast_slots") if isinstance(block, dict) else None
        if not isinstance(stored, dict):
            return
        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        for row in resolver.free_cast_slots(character):
            if rest not in bounds.get(str(row["recharge"]), set()):
                continue
            record = stored.get(row["slot_id"])
            if not isinstance(record, dict):
                continue
            maximum = int(row["uses_max"])
            before = record.get("uses_remaining")
            if before == maximum:
                continue
            record["uses_remaining"] = maximum
            events.append({
                "type": "restore_free_cast",
                "slot_id": str(row["slot_id"]),
                "before": before,
                "after": maximum,
            })

    HUNTER_OPTION_GROUPS = {
        # 两组选项都在短休与长休可以替换（SRD p61 "Whenever you finish a Short
        # or Long Rest"），所以这里不区分休息种类。
        "hunters_prey": "subclass:hunter",
        "defensive_tactics": "subclass:hunter",
    }

    def _option_choice_id(self, feature_id: str) -> str:
        """The ``choice.id`` of an option group, as the sheet records it."""

        catalog = Dnd2024ClassFeatureCatalog.from_bundle(self.bundle)
        for definition in catalog.features_by_key.values():
            if definition.id == feature_id and definition.choice is not None:
                return definition.choice.id
        return f"{feature_id}_option"

    def _option_ids(self, feature_id: str) -> set[str]:
        """The ids this option group declares, read from the catalog itself.

        读取的是父特性的 ``choice.enabled_options``，因此新增或改名一个选项只
        需要改目录，不需要再到这里补一张表。
        """

        catalog = Dnd2024ClassFeatureCatalog.from_bundle(self.bundle)
        ids: set[str] = set()
        for definition in catalog.features_by_key.values():
            if definition.id != feature_id or definition.choice is None:
                continue
            ids.update(
                str(ref).removeprefix("feature:")
                for ref in definition.choice.enabled_options
            )
        return ids

    def _plan_hunter_options(
        self, character: dict[str, Any], requested: Any,
    ) -> dict[str, str]:
        """Validate a Hunter's rest-time option swap.

        Absent means "keep what you have": the SRD lets the player replace the
        option, it never forces a change.  Every rejection happens before any
        state changes, so a bad payload cannot leave a half-finished rest.
        """

        if requested is None or requested == "":
            return {}
        if not isinstance(requested, dict):
            raise RestError("hunter_options must be an object keyed by feature id")
        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        plan: dict[str, str] = {}
        for feature_id, raw in requested.items():
            owner = self.HUNTER_OPTION_GROUPS.get(str(feature_id))
            if owner is None:
                raise RestError(f"{feature_id} has no rest-time option to replace")
            if not isinstance(raw, str) or not raw.strip():
                raise RestError(f"{feature_id} choice must be a feature id")
            # 选项以 "feature:" 前缀提交，与 feature_choices 的记录形状一致。
            choice = raw.strip()
            choice_id = choice.removeprefix("feature:")
            if choice_id not in self._option_ids(feature_id):
                raise RestError(f"{raw} is not an option of {feature_id}")
            if not resolver.has_feature(character, feature_id, owner=owner):
                raise RestError(f"this character has no {feature_id}")
            plan[feature_id] = choice_id
        return plan

    def _plan_memorize_spell(
        self, character: dict[str, Any], request: Any,
    ) -> dict[str, str]:
        """Memorize Spell: exactly one level 1+ Wizard spell swapped in the book.

        短休换准备是**特性**给的预算（5 级法师一次一个），不是职业策略，所以
        不走 `PreparationPolicy`：那条路按"每级/每次长休能换几个"算，法师的长休
        本来就不限数量。这里只认这次请求本身是否合法。
        """

        if request is None:
            return {}
        if not isinstance(request, Mapping):
            raise RestError("memorize_spell must be an object")
        unknown = sorted(set(request) - {"replace_ref", "with_ref"})
        if unknown:
            raise RestError(f"memorize_spell has an unknown field: {unknown[0]}")
        replaced = str(request.get("replace_ref") or "")
        gained = str(request.get("with_ref") or "")
        if not replaced or not gained:
            raise RestError("memorize_spell needs replace_ref and with_ref")
        if replaced == gained:
            raise RestError("memorize_spell must swap two different spells")
        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        if not resolver.has_feature(character, "memorize_spell"):
            raise RestError("this character has no Memorize Spell")
        block = (character.get("spellcasting") or {}).get("class")
        block = block if isinstance(block, dict) else {}
        prepared = [str(ref) for ref in block.get("prepared_spell_refs") or []]
        book = [str(ref) for ref in block.get("spellbook_refs") or []]
        if replaced not in prepared:
            raise RestError("the replaced spell is not prepared")
        if gained not in book:
            raise RestError("the new spell must be in the spellbook")
        if gained in prepared:
            raise RestError("the new spell is already prepared")
        spells = Dnd2024SpellCatalog.from_bundle(self.bundle)
        for ref in (replaced, gained):
            spell = spells.get(ref) or {}
            if int(spell.get("level", 0) or 0) < 1:
                raise RestError("Memorize Spell swaps level 1+ spells only")
            lists = [str(item) for item in spell.get("class_refs") or []]
            if "class:wizard" not in lists:
                raise RestError("Memorize Spell swaps Wizard spells only")
        return {"replace_ref": replaced, "with_ref": gained}

    def _apply_memorize_spell(
        self, character: dict[str, Any], plan: dict[str, str],
    ) -> list[dict[str, Any]]:
        if not plan:
            return []
        block = character.setdefault("spellcasting", {}).setdefault("class", {})
        prepared = [str(ref) for ref in block.get("prepared_spell_refs") or []]
        block["prepared_spell_refs"] = [
            plan["with_ref"] if ref == plan["replace_ref"] else ref
            for ref in prepared
        ]
        return [{
            "type": "memorize_spell",
            "replaced": plan["replace_ref"],
            "with": plan["with_ref"],
        }]

    def _apply_hunter_options(
        self, character: dict[str, Any], plan: dict[str, str],
    ) -> list[dict[str, Any]]:
        if not plan:
            return []
        block = character.setdefault("features", {})
        raw = block.get("feature_choices")
        records = [dict(row) for row in raw] if isinstance(raw, list) else []
        events: list[dict[str, Any]] = []
        for feature_id, option_id in plan.items():
            owner = self.HUNTER_OPTION_GROUPS[feature_id]
            # 记录形状与建卡/升级一致：(owner, feature_id, choice_id) 定位槽，
            # 具体选项放在 selected_refs 里。写错这一层，解析器就当"没选过"。
            choice_id = self._option_choice_id(feature_id)
            selected = f"feature:{option_id}"
            before = ""
            for record in records:
                if (str(record.get("owner") or "") == owner
                        and str(record.get("feature_id") or "") == feature_id
                        and str(record.get("choice_id") or "") == choice_id):
                    current = record.get("selected_refs")
                    if isinstance(current, list) and current:
                        before = str(current[0])
                    record["selected_refs"] = [selected]
                    break
            else:
                # 没有历史记录时补一条：换选项本身就是"从这一项改成那一项"，
                # 缺记录只说明建卡时选了默认项而没有落 slot。
                records.append({
                    "owner": owner, "feature_id": feature_id,
                    "choice_id": choice_id, "selected_refs": [selected],
                })
            events.append({
                "type": "rebind_feature_choice", "owner": owner,
                "feature_id": feature_id, "choice_id": choice_id,
                "before": before or None, "after": selected,
            })
        block["feature_choices"] = records
        return events

    def _plan_fiendish_resilience(
        self, character: dict[str, Any], requested: Any,
    ) -> str | None:
        """Validate a Fiend's rest-time damage-type choice.

        ``None``/absent means "keep the current choice": the feature only
        changes when the player explicitly re-chooses.  A first rest without a
        choice therefore leaves the character with no resistance rather than
        defaulting to a type.
        """

        if requested is None or requested == "":
            return None
        if not isinstance(requested, str):
            raise RestError("fiendish_resilience must be a damage type")
        chosen = requested.strip().lower()
        if chosen not in DAMAGE_TYPES:
            raise RestError(f"unknown damage type for fiendish resilience: {requested}")
        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        if not resolver.has_feature(
            character, "fiendish_resilience", owner="subclass:fiend_patron",
        ):
            raise RestError("this character has no fiendish resilience")
        excluded = resolver.feature_value(
            character, "fiendish_resilience", "excluded_damage_types", [],
        )
        if isinstance(excluded, list) and chosen in {str(item) for item in excluded}:
            raise RestError(f"{chosen} cannot be chosen with fiendish resilience")
        return chosen

    def _apply_fiendish_resilience(
        self, character: dict[str, Any], chosen: str | None,
    ) -> list[dict[str, Any]]:
        if chosen is None:
            return []
        block = character.setdefault("features", {})
        current = block.get("fiendish_resilience")
        current = current if isinstance(current, dict) else {}
        before = str(current.get("damage_type") or "")
        block["fiendish_resilience"] = {**current, "damage_type": chosen}
        return [{
            "type": "rebind_fiendish_resilience", "before": before or None,
            "after": chosen, "feature_id": "fiendish_resilience",
            "owner": "subclass:fiend_patron",
        }]

    def _apply_tireless_exhaustion(
        self, character: dict[str, Any], events: list[dict[str, Any]],
    ) -> None:
        """Tireless: one level of Exhaustion less whenever a Short Rest ends.

        The rest catalog owns the Long Rest's own reduction; this one belongs to
        a feature, so it only fires for a character that really owns it.  Both
        are per rest and never compound -- they are reductions of two different
        rests, not two sources applying to one -- which is why the amount comes
        from the declaration instead of being counted per feature.
        """

        exhaustion = int((character.get("conditions") or {}).get("exhaustion", 0) or 0)
        if exhaustion <= 0:
            return
        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        if not resolver.has_feature(character, "tireless"):
            return
        reduction = resolver.feature_value(
            character, "tireless", "short_rest_exhaustion_reduction", 1,
        )
        amount = int(reduction) if isinstance(reduction, int) and not isinstance(
            reduction, bool
        ) else 1
        conditions = character.setdefault("conditions", {})
        conditions["exhaustion"] = max(0, exhaustion - max(0, amount))
        events.append({
            "type": "reduce_exhaustion",
            "before": exhaustion,
            "after": conditions["exhaustion"],
            "feature_id": "tireless",
        })

    def _reset_relentless_rage_dc(
        self, character: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Both rests reset the survival DC to the declaration's base.

        Nothing is written when the DC is already at the base, so a rest that
        never used the feature does not invent a recovery event.
        """

        block = character.get("features")
        if not isinstance(block, dict):
            return []
        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        if not resolver.has_feature(character, "relentless_rage"):
            return []
        base = resolver.feature_value(character, "relentless_rage", "base_dc", 10)
        base_dc = int(base) if isinstance(base, int) and not isinstance(base, bool) else 10
        stored = block.get("relentless_rage")
        stored = stored if isinstance(stored, dict) else {}
        if stored.get("next_dc") == base_dc:
            return []
        block["relentless_rage"] = {**stored, "next_dc": base_dc}
        return [{"type": "reset_relentless_rage_dc", "dc": base_dc}]

    def _class_state(self, character: dict[str, Any]) -> tuple[str, str, int]:
        build = character.get("build")
        rows = build.get("class_levels") if isinstance(build, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise RestError("rest recovery currently requires one class")
        class_ref = str(rows[0].get("class_ref") or "")
        class_id = class_ref.removeprefix("class:")
        level = rows[0].get("level")
        if isinstance(level, bool) or not isinstance(level, int):
            raise RestError("character class level is invalid")
        self.progression.snapshot(class_ref, level)
        return class_ref, class_id, level

    @staticmethod
    def _require_conscious(character: dict[str, Any], rest_name: str) -> None:
        if int(character.get("resources", {}).get("hp", 0) or 0) < 1:
            raise RestError(f"{rest_name} requires at least 1 HP")

    @staticmethod
    def _maximum(
        spec: dict[str, Any], tracks: dict[str, int], character: dict[str, Any],
    ) -> int:
        if "maximum_track" in spec:
            return int(tracks.get(str(spec["maximum_track"]), 0) or 0)
        if "maximum_fixed" in spec:
            return int(spec["maximum_fixed"])
        if "maximum_proficiency_bonus" in spec:
            value = int(character.get("derived", {}).get("proficiency_bonus", 0) or 0)
            return max(int(spec.get("minimum", 0) or 0), value)
        ability = str(spec["maximum_ability_modifier"])
        value = ability_modifier(int(character.get("abilities", {}).get(ability, 0) or 0))
        return max(int(spec.get("minimum", 0) or 0), value)

    def _recover_class_resources(
        self, character: dict[str, Any], rest: str, events: list[dict[str, Any]],
    ) -> None:
        _class_ref, class_id, level = self._class_state(character)
        class_state = character["resources"]["class"]
        self._recover_specs(
            self.rules["class_resources"][class_id], class_state, level, rest, events,
        )
        # 专长池与职业池共享 ``resources.class`` 存储，但资格仍按 feat_refs 门控：
        # 没有专长就没有池，也就谈不上恢复。
        feat_refs = {
            str(ref) for ref in (character.get("features") or {}).get("feat_refs") or []
        }
        for feat_id, specs in (self.rules.get("feat_resources") or {}).items():
            if f"feat:{feat_id}" not in feat_refs:
                continue
            self._recover_specs(specs, class_state, level, rest, events)

    def _recover_specs(
        self, specs: list[dict[str, Any]], class_state: dict[str, Any],
        level: int, rest: str, events: list[dict[str, Any]],
    ) -> None:
        for spec in specs:
            state = class_state.get(spec["id"])
            if not isinstance(state, dict):
                continue
            policy: Any = spec.get(rest, "none")
            if rest == "short" and level >= int(spec.get("short_all_from_level", 99) or 99):
                policy = "all"
            before = int(state["current"])
            if policy == "all":
                state["current"] = int(state["maximum"])
            elif isinstance(policy, dict):
                state["current"] = min(
                    int(state["maximum"]), before + int(policy.get("amount", 0) or 0)
                )
            if int(state["current"]) != before:
                events.append({
                    "type": "restore_class_resource",
                    "resource_id": spec["id"],
                    "before": before,
                    "after": state["current"],
                })

    def _recover_spell_slots(
        self, character: dict[str, Any], rest: str, events: list[dict[str, Any]],
    ) -> None:
        class_magic = character.get("spellcasting", {}).get("class")
        if not isinstance(class_magic, dict):
            return
        profile = str(class_magic.get("slot_profile") or "")
        policy = self.rules["spell_slots"].get(profile, {}).get(rest, "none")
        if policy != "all":
            return
        before = deepcopy(class_magic.get("slots_current") or {})
        class_magic["slots_current"] = deepcopy(class_magic.get("slots_max") or {})
        if class_magic["slots_current"] != before:
            events.append({
                "type": "restore_spell_slots",
                "before": before,
                "after": deepcopy(class_magic["slots_current"]),
            })

    # ------------------------------------------------------------------
    # short-rest recovery budgets (Arcane/Natural Recovery, Sorcerous Restoration)
    # ------------------------------------------------------------------

    def validate_short_recovery(
        self, character: dict[str, Any], recovery: Any,
    ) -> None:
        """Validate a Short Rest recovery choice without touching live state.

        Same shape as ``validate_land_terrain``: a party rest is opened only
        after every participant's payload is known to be usable, so an
        overspent budget has to fail before the session starts.
        """

        if recovery in (None, {}):
            return
        self._plan_short_recovery(self.sync_resources(character), recovery)

    def rest_recovery_options(
        self, character: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """What a Short Rest could give back right now, for the client to draw.

        A read-only projection over the same declarations the commit path
        plans from: nothing here decides anything, it only reports the budget
        and how many uses of each pool are left.
        """

        character = self.sync_resources(character)
        _class_ref, class_id, level = self._class_state(character)
        class_state = character["resources"]["class"]
        class_magic = character.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        rows: list[dict[str, Any]] = []
        for spec in self.rules["class_resources"][class_id]:
            declaration = spec.get("short_recovery")
            if not isinstance(declaration, dict):
                continue
            if level < int(spec.get("minimum_level", 1) or 1):
                continue
            if not self._owns_spec(spec, character):
                continue
            state = class_state.get(spec["id"])
            if not isinstance(state, dict):
                continue
            row: dict[str, Any] = {
                "resource_id": str(spec["id"]),
                "mode": str(declaration["mode"]),
                "budget": self._recovery_budget(declaration, level),
                "uses_left": int(state.get("current", 0) or 0),
            }
            if declaration["mode"] == "spell_slots":
                row["max_slot_level"] = int(declaration["max_slot_level"])
                row["slots"] = self._expended_slots(
                    class_magic, int(declaration["max_slot_level"]),
                )
            else:
                row["track"] = str(declaration["track"])
                track = class_state.get(str(declaration["track"]))
                track = track if isinstance(track, dict) else {}
                row["spent"] = int(track.get("maximum", 0) or 0) - int(
                    track.get("current", 0) or 0
                )
            rows.append(row)
        return rows

    @staticmethod
    def _expended_slots(
        class_magic: Mapping[str, Any], max_slot_level: int,
    ) -> list[dict[str, int]]:
        current = class_magic.get("slots_current") or {}
        maximum = class_magic.get("slots_max") or {}
        rows: list[dict[str, int]] = []
        for level in sorted(str(key) for key in maximum):
            try:
                numeric = int(level)
            except ValueError:
                continue
            if not 1 <= numeric <= max_slot_level:
                continue
            top = int(maximum.get(level, 0) or 0)
            left = int(current.get(level, 0) or 0)
            rows.append({"level": numeric, "expended": max(0, top - left), "maximum": top})
        return rows

    @staticmethod
    def _recovery_budget(declaration: Mapping[str, Any], level: int) -> int:
        if declaration.get("budget") == "half_level_round_up":
            return (int(level) + 1) // 2
        return int(level) // 2

    def _plan_short_recovery(
        self, character: dict[str, Any], recovery: Any,
    ) -> list[dict[str, Any]]:
        """Turn the request into a checked plan; raise before any state moves."""

        if recovery in (None, {}):
            return []
        if not isinstance(recovery, dict):
            raise RestError("recovery must be an object keyed by feature id")
        _class_ref, class_id, level = self._class_state(character)
        declared = {
            str(spec["id"]): spec
            for spec in self.rules["class_resources"][class_id]
            if isinstance(spec.get("short_recovery"), dict)
        }
        class_state = character["resources"]["class"]
        class_magic = character.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        plan: list[dict[str, Any]] = []
        for raw_id, request in recovery.items():
            resource_id = str(raw_id)
            spec = declared.get(resource_id)
            if spec is None or level < int(spec.get("minimum_level", 1) or 1):
                raise RestError(
                    f"{resource_id} is not a short-rest recovery option for this character"
                )
            if not self._owns_spec(spec, character):
                raise RestError(
                    f"{resource_id} is not a short-rest recovery option for this character"
                )
            state = class_state.get(resource_id)
            if not isinstance(state, dict):
                raise RestError(f"{resource_id} has no resource pool on this character")
            if int(state.get("current", 0) or 0) < 1:
                raise RestError(
                    f"{resource_id} cannot be used again until the next long rest"
                )
            declaration = spec["short_recovery"]
            budget = self._recovery_budget(declaration, level)
            if budget <= 0:
                raise RestError(f"{resource_id} has no budget at this level")
            if not isinstance(request, dict):
                raise RestError(f"{resource_id} recovery must be an object")
            if declaration["mode"] == "spell_slots":
                slots = self._plan_slot_recovery(
                    class_magic, request, budget, int(declaration["max_slot_level"]),
                    resource_id,
                )
                if not slots:
                    continue
                plan.append({
                    "resource_id": resource_id, "mode": "spell_slots",
                    "budget": budget, "slots": slots,
                })
            else:
                points = self._plan_point_recovery(
                    class_state, request, budget, str(declaration["track"]),
                    resource_id,
                )
                if not points:
                    continue
                plan.append({
                    "resource_id": resource_id, "mode": "points",
                    "budget": budget, "track": str(declaration["track"]),
                    "points": points,
                })
        return plan

    @staticmethod
    def _plan_slot_recovery(
        class_magic: Mapping[str, Any], request: dict[str, Any], budget: int,
        max_slot_level: int, resource_id: str,
    ) -> dict[str, int]:
        extra = set(request) - {"slots"}
        if extra:
            raise RestError(
                f"{resource_id} recovery does not accept {sorted(extra)[0]}"
            )
        requested = request.get("slots") or {}
        if not isinstance(requested, dict):
            raise RestError(f"{resource_id}.slots must be an object keyed by slot level")
        current = class_magic.get("slots_current") or {}
        maximum = class_magic.get("slots_max") or {}
        picked: dict[str, int] = {}
        total = 0
        for raw_level, raw_count in requested.items():
            try:
                slot_level = int(str(raw_level))
            except ValueError as exc:
                raise RestError(f"{resource_id} has an invalid slot level") from exc
            if not 1 <= slot_level <= max_slot_level:
                raise RestError(
                    f"{resource_id} can only recover slots of level 1 to {max_slot_level}"
                )
            if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 1:
                raise RestError(f"{resource_id} slot counts must be positive integers")
            key = str(slot_level)
            expanded = max(
                0, int(maximum.get(key, 0) or 0) - int(current.get(key, 0) or 0)
            )
            if raw_count > expanded:
                raise RestError(
                    f"{resource_id} cannot recover more level {slot_level} slots than are expended"
                )
            picked[key] = raw_count
            total += slot_level * raw_count
        if total > budget:
            raise RestError(
                f"{resource_id} budget is {budget} combined levels, {total} requested"
            )
        return picked

    @staticmethod
    def _plan_point_recovery(
        class_state: Mapping[str, Any], request: dict[str, Any], budget: int,
        track: str, resource_id: str,
    ) -> int:
        extra = set(request) - {"points"}
        if extra:
            raise RestError(
                f"{resource_id} recovery does not accept {sorted(extra)[0]}"
            )
        points = request.get("points", 0)
        if isinstance(points, bool) or not isinstance(points, int) or points < 0:
            raise RestError(f"{resource_id}.points must be a non-negative integer")
        if points == 0:
            return 0
        if points > budget:
            raise RestError(f"{resource_id} can regain at most {budget} points")
        state = class_state.get(track)
        state = state if isinstance(state, dict) else {}
        spent = int(state.get("maximum", 0) or 0) - int(state.get("current", 0) or 0)
        if points > spent:
            raise RestError(f"{resource_id} cannot regain more points than are expended")
        return points

    def _apply_short_recovery(
        self, character: dict[str, Any], plan: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Commit a checked plan: slots or points first, then the use itself."""

        if not plan:
            return []
        class_state = character["resources"]["class"]
        class_magic = character.setdefault("spellcasting", {}).setdefault("class", {})
        events: list[dict[str, Any]] = []
        for item in plan:
            if item["mode"] == "spell_slots":
                before = deepcopy(class_magic.get("slots_current") or {})
                current = dict(before)
                for level, count in item["slots"].items():
                    current[level] = int(current.get(level, 0) or 0) + int(count)
                class_magic["slots_current"] = current
                events.append({
                    "type": "recover_spell_slots",
                    "resource_id": item["resource_id"],
                    "budget": item["budget"],
                    "slots": dict(item["slots"]),
                    "before": before,
                    "after": deepcopy(current),
                })
            else:
                track = class_state[item["track"]]
                before = int(track.get("current", 0) or 0)
                track["current"] = min(
                    int(track.get("maximum", 0) or 0), before + int(item["points"])
                )
                events.append({
                    "type": "recover_class_points",
                    "resource_id": item["resource_id"],
                    "budget": item["budget"],
                    "track": item["track"],
                    "before": before,
                    "after": int(track["current"]),
                })
            # 使用次数只在真的换回了东西之后才扣：一次"选了零恢复"的提交
            # 不该悄悄烧掉今天唯一的一次。
            state = class_state[item["resource_id"]]
            before_uses = int(state.get("current", 0) or 0)
            state["current"] = before_uses - 1
            events.append({
                "type": "spend_resource_use",
                "resource_id": item["resource_id"],
                "before": before_uses,
                "after": int(state["current"]),
            })
        return events

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
