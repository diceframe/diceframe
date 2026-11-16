"""Deterministic SRD 5.2.1 rest and resource recovery."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.attunement import AttunementError
from src.rulesets.dnd2024.character.attunement import apply_short_rest_attunement
from src.rulesets.dnd2024.character.attunement import owned_ref_counts
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.items.charges import ChargesError, recharge_for_rest
from src.rulesets.dnd2024.progression.catalog import Dnd2024ProgressionCatalog
from src.rulesets.dnd2024.spells.feat_spells import concentration_holder, restore_feat_free_casts
from src.rulesets.dnd2024.spells.preparation import PreparationPolicy
from src.rulesets.dnd2024.spells.selection import Dnd2024SpellSelection


class RestError(ValueError):
    """Raised when a rest cannot legally be completed."""


# 升级时 current 如何随新的 maximum 调整。默认值必须保持既有语义（见
# ``sync_resources``）：任何没有显式声明的职业资源都继续沿用旧行为。
RESIZE_POLICIES = ("preserve_spent", "preserve_current")
DEFAULT_RESIZE_POLICY = "preserve_spent"


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

    def _sync_specs(
        self, specs: list[dict[str, Any]], class_state: dict[str, Any],
        tracks: dict[str, int], character: dict[str, Any], level: int,
        *, skip: bool = False,
    ) -> None:
        for spec in specs:
            if skip or level < int(spec.get("minimum_level", 1) or 1):
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
    ) -> dict[str, Any]:
        character = self.sync_resources(payload)
        self._require_conscious(character, "Short Rest")
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
        self._recover_class_resources(character, "short", events)
        self._recover_spell_slots(character, "short", events)
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
        self, payload: dict[str, Any], prepared_spells: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        character = self.sync_resources(payload)
        self._require_conscious(character, "Long Rest")
        events: list[dict[str, Any]] = []
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
        self._recover_class_resources(character, "long", events)
        self._recover_spell_slots(character, "long", events)
        if prepared_spells:
            events.extend(self._apply_prepared_spells(character, prepared_spells))
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
