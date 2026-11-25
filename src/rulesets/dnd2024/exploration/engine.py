"""Out-of-combat (exploration) spellcasting for D&D 2024.

Exploration casting deliberately does NOT reuse the combat action economy:
no action/bonus-action/movement accounting, no turn order. What it must
uphold is the same resource authority as combat — spell slots, concentration,
and HP changes are applied to the same canonical character state, and every
cast is recorded as an event with ``context = "exploration"``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.engine.modules import ruleset_runtime
from src.rulesets.bundle import LoadedRulesetBundle
from src.rulesets.dnd2024.character.attunement import (
    AttunementError,
    attuned_refs,
    canonical_item_ref,
    inventory_ref_counts,
    item_requires_attunement,
)
from src.rulesets.dnd2024.character.builder import ability_modifier
from src.rulesets.dnd2024.combat.action_adapter import (
    formula_node_from_dice_formula,
    heal_ability_modifier_node,
    roll_damage_node,
    spell_formula_node,
)
from src.rulesets.dnd2024.combat.catalog import Dnd2024CombatCatalog
from src.rulesets.dnd2024.combat.check_modes import condition_flags
from src.rulesets.dnd2024.combat.primitives import CombatIntentError, roll
from src.rulesets.dnd2024.items.activation import (
    POTION_ITEM_TYPE,
    ItemActivationError,
    build_item_healing_event,
    validate_activation_declaration,
    validate_consumable_declaration,
)
from src.rulesets.dnd2024.items.charges import (
    ChargesError,
    charges_of,
    charge_waiver_roll,
    destroy_check_events,
    spend_charges,
    validate_charges_declaration,
    validate_destroy_declaration,
)
from src.rulesets.dnd2024.exploration.revive_effects import (
    UNSUPPORTED_MESSAGE,
    is_fully_implemented,
    revive_definition,
    revive_events,
)
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.spells.casting import (
    apply_free_cast_cost, free_cast_resource, spell_caster, spell_cost_events,
    spellcasting_projection,
)
from src.rulesets.dnd2024.spells.catalog import Dnd2024SpellCatalog
from src.rulesets.dnd2024.spells.font_of_magic import font_slots
from src.rulesets.dnd2024.spells.feat_spells import concentration_holder
from src.rulesets.dnd2024.treasure import (
    Dnd2024TreasureCatalog,
    TreasureTableError,
    canonical_table_ref,
)
from src.rulesets.events import EventBatchError, apply_event_batch, stable_batch_id

EXPLORATION_INTENT_TYPES = frozenset({
    "exploration.cast_spell", "exploration.activate_item", "exploration.roll_treasure",
})

_ITEM_EVENT_FIELDS = {
    "dnd2024.item.consumed": frozenset({"type", "actor_id", "item_ref", "amount"}),
    "dnd2024.item.activated": frozenset({"type", "actor_id", "item_ref", "target_id", "cost"}),
    "dnd2024.item.charge_spent": frozenset({"type", "actor_id", "item_ref", "amount"}),
    "dnd2024.item.charge_waived": frozenset({
        "type", "actor_id", "item_ref", "declared", "roll", "threshold",
    }),
}

# 探索态没有敌方 actor；伤害/豁免/法术攻击模式的确定性结算需要敌对目标，
# 第一阶段不支持——已知但非确定的法术走 narrative 分支（§32）。
_SUPPORTED_EFFECT_MODES = frozenset({"healing", "buff"})


def _canonical_actor_id(value: Any) -> str:
    """Event/ledger identity is always ``player:<uid>``.

    The gameplay response boundary hands intents a bare uid; emitting that in
    events would make the reducer's ``player:<uid>`` consumption match fail.
    """

    raw = str(value or "")
    return raw if raw.startswith("player:") else f"player:{raw}"


class ExplorationIntentError(ValueError):
    """Raised when an exploration intent is structurally or rules invalid."""


@dataclass(slots=True)
class Dnd2024ExplorationEngine:
    bundle: LoadedRulesetBundle
    locale: str = ""
    bundle_validated: bool = False
    catalog: Dnd2024CombatCatalog = field(init=False)
    spells: Dnd2024SpellCatalog = field(init=False)
    features: Dnd2024ClassFeatureResolver = field(init=False)
    treasure: Dnd2024TreasureCatalog = field(init=False)

    def __post_init__(self) -> None:
        self.catalog = Dnd2024CombatCatalog.from_bundle(
            self.bundle, validate_items=not self.bundle_validated,
        )
        self.spells = Dnd2024SpellCatalog.from_bundle(self.bundle)
        self.features = Dnd2024ClassFeatureResolver(self.bundle)
        self.treasure = Dnd2024TreasureCatalog.from_bundle(self.bundle)

    # ------------------------------------------------------------------
    # state helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _state(instance: Any) -> dict[str, Any]:
        state = ruleset_runtime.state(instance)
        if not isinstance(state, dict):
            raise ExplorationIntentError("ruleset_state must be an object")
        state.setdefault("state_schema_version", 1)
        state.setdefault("version", 0)
        state.setdefault("party", {"companions": {}})
        state["party"].setdefault("companions", {})
        state.setdefault("combat", {"status": "none"})
        return state

    @staticmethod
    def _canonical_of(instance: Any, actor_id: str) -> dict[str, Any]:
        kind, raw_id = actor_id.split(":", 1) if ":" in actor_id else ("", actor_id)
        if kind == "player":
            if raw_id not in instance.players:
                raise ExplorationIntentError("spellcasting actor does not exist")
            return instance.get_character_sheet(raw_id).setdefault("ruleset_character", {})
        party = ruleset_runtime.state(instance).get("party", {})
        companion = (party.get("companions", {}) or {}).get(raw_id)
        if not isinstance(companion, dict) or not companion.get("active", True):
            raise ExplorationIntentError("spellcasting target does not exist")
        return companion["ruleset_character"]

    def _party_targets(self, instance: Any) -> list[dict[str, Any]]:
        targets = []
        for uid in instance.players:
            canonical = self._canonical_of(instance, f"player:{uid}")
            resources = canonical.get("resources", {})
            targets.append({
                "actor_id": f"player:{uid}", "side": "party",
                "hp": int(resources.get("hp", 0) or 0),
                "max_hp": int(resources.get("max_hp", 0) or 0),
                "name": str(instance.players[uid].get("character_name") or uid),
            })
        for companion_id, companion in sorted(
            (ruleset_runtime.state(instance).get("party", {}).get("companions", {}) or {}).items(),
        ):
            if not isinstance(companion, dict) or not companion.get("active", True):
                continue
            canonical = companion.get("ruleset_character", {})
            resources = canonical.get("resources", {})
            targets.append({
                "actor_id": f"companion:{companion_id}", "side": "party",
                "hp": int(resources.get("hp", 0) or 0),
                "max_hp": int(resources.get("max_hp", 0) or 0),
                "name": str(companion.get("name") or companion_id),
            })
        return targets

    def _spell_refs(self, canonical: dict[str, Any]) -> list[str]:
        return spellcasting_projection(canonical, self.features)["spell_refs"]

    def _ritual_spell_refs(self, canonical: dict[str, Any]) -> list[str]:
        """Spells the caster may cast ritually *without* preparing them.

        Ritual Adept reads the book, not the prepared list, so this is the one
        place that looks at ``spellbook_refs`` for casting purposes.
        """

        if not self.features.has_feature(canonical, "ritual_adept"):
            return []
        block = (canonical.get("spellcasting") or {}).get("class") or {}
        book = [str(ref) for ref in block.get("spellbook_refs") or []]
        return [
            ref for ref in dict.fromkeys(book)
            if (self.spells.get(ref) or {}).get("ritual")
        ]

    def _validate_ritual(
        self, canonical: dict[str, Any], spell_ref: str, spell: dict[str, Any],
        intent: dict[str, Any],
    ) -> None:
        """Ritual Adept: book + ritual tag, no slot, and no shortcut around time.

        时间与组件在本层没有可推进的时钟，所以这里只保证"不花槽、必须读书
        里的那一条"；施法时间留给叙事层，事件里写明是仪式而非普通施法。
        """

        if not self.features.has_feature(canonical, "ritual_adept"):
            raise ExplorationIntentError("ritual casting requires Ritual Adept")
        if not spell.get("ritual"):
            raise ExplorationIntentError("this spell has no Ritual tag")
        if spell_ref not in self._ritual_spell_refs(canonical):
            raise ExplorationIntentError("ritual casting reads from the spellbook")
        if int(intent.get("slot_level", 0) or 0) != 0:
            raise ExplorationIntentError("a ritual cast spends no spell slot")

    def _casting_eligible(self, canonical: dict[str, Any]) -> bool:
        """探索施法的公共资格：HP>0、非 dead、非失能。

        ``available_intents`` 与 ``_validate`` 共用同一判定，避免出现“入口不
        提供、但提交仍可结算”的裂缝。``reaction_blocked`` 只关反应，不禁止
        普通施法，因此不在此列。
        """

        resources = canonical.get("resources") or {}
        if int(resources.get("hp", 0) or 0) <= 0:
            return False
        conditions = canonical.get("conditions") or {}
        if "dead" in conditions:
            return False
        return not condition_flags(self.catalog.conditions, conditions)["incapacitated"]

    # ------------------------------------------------------------------
    # intents
    # ------------------------------------------------------------------

    def available_intents(self, instance: Any, actor_id: str) -> list[dict[str, Any]]:
        state = self._state(instance)
        if state["combat"].get("status") == "active":
            return []
        uid = actor_id.removeprefix("player:")
        actions: list[dict[str, Any]] = []
        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        # The gameplay response boundary passes a bare uid, while older callers
        # use the actor form.  Accept both here; never widen the intent's own
        # actor_id contract.
        if gm_uid and uid == gm_uid:
            tables = self.treasure.list_tables()
            if tables:
                actions.append({
                    "type": "exploration.roll_treasure",
                    "label": "Roll a treasure table",
                    "expected_version": int(state.get("version", 0) or 0),
                    "actor_id": actor_id, "tables": tables,
                })
        if not uid or uid not in instance.players:
            return actions
        canonical = self._canonical_of(instance, f"player:{uid}")
        if not self._casting_eligible(canonical):
            return actions
        class_magic = canonical.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        spells = []
        casting = spellcasting_projection(canonical, self.features)
        for spell_ref in casting["spell_refs"]:
            spell = self.spells.get(spell_ref)
            if spell is None:
                continue
            available_slots = (
                [
                    int(level) for level, count in sorted(
                        (class_magic.get("slots_current") or {}).items(),
                        key=lambda item: int(item[0]),
                    )
                    if int(count) > 0 and int(level) >= int(spell["level"])
                ]
                if int(spell["level"]) > 0 else [0]
            )
            if int(spell["level"]) > 0:
                # Font of Magic 造的临时槽在普通槽表之外，但施法时是真槽。
                for level, count in casting["font_slots"].items():
                    if int(count) > 0 and int(level) >= int(spell["level"]):
                        available_slots.append(int(level))
                available_slots = sorted(set(available_slots))
                if free_cast_resource(casting, spell_ref, int(spell["level"])) is not None:
                    available_slots.insert(0, 0)
            if int(spell["level"]) > 0 and not available_slots:
                continue
            spells.append({
                "spell_ref": spell_ref, "name": spell["name"], "level": spell["level"],
                "casting_time": spell["casting_time"], "available_slot_levels": available_slots,
            })
        # 仪式是"另一种施法方式"：只有未准备的书内仪式法术才单列，已准备的那条
        # 已经在上面的普通施法里出现过，重复列会让人以为是两个不同的法术。
        prepared = set(casting["spell_refs"])
        for spell_ref in self._ritual_spell_refs(canonical):
            spell = self.spells.get(spell_ref)
            if spell is None or spell_ref in prepared:
                continue
            spells.append({
                "spell_ref": spell_ref, "name": spell["name"], "level": spell["level"],
                "casting_time": spell["casting_time"], "available_slot_levels": [0],
                "ritual": True,
            })
        if spells:
            actions.append({
                "type": "exploration.cast_spell", "label": "Cast a spell",
                "expected_version": int(state.get("version", 0) or 0),
                "actor_id": actor_id, "spells": spells,
                "targets": self._party_targets(instance),
            })
        items = self._available_activation_items(instance, actor_id, canonical)
        if items:
            actions.append({
                "type": "exploration.activate_item", "label": "Use an item",
                "expected_version": int(state.get("version", 0) or 0),
                "actor_id": actor_id, "items": items,
            })
        return actions

    def _available_activation_items(
        self, instance: Any, actor_id: str, canonical: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Project legally activatable held potions for the existing action dock."""

        payload = instance.get_character_sheet(actor_id.removeprefix("player:"))
        owned = inventory_ref_counts(payload)
        attuned = attuned_refs(canonical)
        items: list[dict[str, Any]] = []
        for item_ref, count in sorted(owned.items()):
            if count < 1:
                continue
            item = self.bundle.get("item", item_ref.removeprefix("item:"))
            if not isinstance(item, dict):
                continue
            try:
                declaration = validate_activation_declaration(item)
                validate_consumable_declaration(item)
                maximum = validate_charges_declaration(item)
            except (ItemActivationError, ChargesError):
                continue
            if declaration is None:
                continue
            if str(item.get("item_type") or "") != POTION_ITEM_TYPE:
                continue
            if declaration["target"] != "self":
                continue
            if item_requires_attunement(self.bundle, item_ref) and item_ref not in attuned:
                continue
            charge_cost = declaration.get("charge_cost")
            if maximum is not None:
                if charge_cost is None or count > 1:
                    continue
                current = charges_of(canonical, item_ref)
                if current is None or current < int(charge_cost):
                    continue
            elif charge_cost is not None:
                continue
            items.append({
                "item_ref": item_ref,
                "name": str(item.get("name") or item_ref),
                "cost": declaration["cost"],
                "target": declaration["target"],
            })
        return items

    def validate_intent(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]:
        try:
            self._validate(instance, intent)
        except ExplorationIntentError as exc:
            return {"ok": False, "code": "INVALID_INTENT", "error": str(exc)}
        return {"ok": True}

    def _validate(self, instance: Any, intent: dict[str, Any]) -> None:
        if not isinstance(intent, dict):
            raise ExplorationIntentError("intent must be an object")
        if str(intent.get("type") or "") not in EXPLORATION_INTENT_TYPES:
            raise ExplorationIntentError("intent type is not supported")
        state = self._state(instance)
        expected = intent.get("expected_version")
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise ExplorationIntentError("expected_version must be an integer")
        if expected != int(state.get("version", 0) or 0):
            raise ExplorationIntentError(
                f"state version conflict: expected {expected}, current {state.get('version')}"
            )
        if state["combat"].get("status") == "active":
            action = (
                "treasure rolling"
                if intent.get("type") == "exploration.roll_treasure"
                else "exploration casting"
            )
            raise ExplorationIntentError(f"{action} requires no active combat")
        if str(intent.get("type") or "") == "exploration.roll_treasure":
            self._validate_roll_treasure(instance, intent)
            return
        actor_id = str(intent.get("actor_id") or "")
        uid = actor_id.removeprefix("player:")
        if not uid or uid not in instance.players:
            raise ExplorationIntentError("exploration casting is player-only in this phase")
        if str(intent.get("submitted_by") or "") != uid:
            raise ExplorationIntentError("a player can only cast for their own character")
        actor_id = f"player:{uid}"
        canonical = self._canonical_of(instance, actor_id)
        if not self._casting_eligible(canonical):
            raise ExplorationIntentError(
                "the spellcaster cannot cast while dead, at 0 HP, or incapacitated"
            )
        if str(intent.get("type") or "") == "exploration.activate_item":
            self._validate_activate_item(instance, intent, actor_id, uid, canonical)
            return
        spell_ref = str(intent.get("spell_ref") or "")
        spell = self.spells.get(spell_ref)
        if spell is None:
            raise ExplorationIntentError("spell does not exist in the ruleset")
        ritual = bool(intent.get("ritual"))
        if ritual:
            self._validate_ritual(canonical, spell_ref, spell, intent)
        elif spell_ref not in self._spell_refs(canonical):
            raise ExplorationIntentError("spell is not prepared or known by the actor")
        slot_level = intent.get("slot_level", 0 if ritual else spell["level"])
        if isinstance(slot_level, bool) or not isinstance(slot_level, int):
            raise ExplorationIntentError("slot_level must be an integer")
        class_magic = canonical.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        slots = class_magic.get("slots_current") or {}
        if ritual:
            # 仪式不花槽，也就没有"槽够不够"这一问；`_validate_ritual` 已经
            # 确认了它与免费施法资源无关。
            pass
        elif spell["level"] == 0:
            if slot_level != 0:
                raise ExplorationIntentError("a cantrip does not use a spell slot")
        elif slot_level == 0:
            casting = spellcasting_projection(canonical, self.features)
            if free_cast_resource(casting, spell_ref, int(spell["level"])) is None:
                raise ExplorationIntentError("the free spell cast is not available")
        else:
            if slot_level < spell["level"]:
                raise ExplorationIntentError("slot_level is lower than the spell level")
            if int(slots.get(str(slot_level), 0) or 0) < 1 \
                    and int(font_slots(canonical).get(str(slot_level), 0) or 0) < 1:
                raise ExplorationIntentError("the selected spell slot is not available")
        effect = self.catalog.spell_effects.get(spell_ref.removeprefix("spell:"))
        target_values = intent.get("target_ids")
        targets = [str(item) for item in (target_values or []) if str(item)]
        revive = revive_definition(spell_ref)
        if revive is not None:
            # 复活家族：语义表存在即走 revive 管线；任何未实现语义整体拒绝，
            # 绝不静默降级为部分生效。拒绝发生在事件构造之前，因此不扣法术位。
            if not is_fully_implemented(revive):
                raise ExplorationIntentError(UNSUPPORTED_MESSAGE)
            spell_name = spell_ref.removeprefix("spell:")
            if len(targets) != 1:
                raise ExplorationIntentError(f"{spell_name} requires a dead ally")
            target_id = targets[0]
            if (
                not target_id.startswith("player:")
                or target_id.removeprefix("player:") not in instance.players
            ):
                raise ExplorationIntentError(f"{spell_name} requires a dead ally")
            conditions = self._canonical_of(instance, target_id).get("conditions") or {}
            if "dead" not in conditions:
                raise ExplorationIntentError(f"{spell_name} requires a dead ally")
        elif effect is not None and str(effect.get("mode")) not in _SUPPORTED_EFFECT_MODES:
            # 伤害/豁免类确定性效果需要敌对目标，探索态不存在；这类请求拒绝，
            # 而不是让叙事层自行决定数值。
            raise ExplorationIntentError(
                "this spell has no exploration-safe deterministic effect"
            )
        required_count = int(effect.get("target_count", 1) or 1) if effect else 1
        if effect is not None and effect.get("mode") in _SUPPORTED_EFFECT_MODES:
            if not targets or len(targets) > required_count or len(set(targets)) != len(targets):
                raise ExplorationIntentError(
                    f"spell requires 1 to {required_count} unique targets"
                )
            party_ids = {target["actor_id"] for target in self._party_targets(instance)}
            for target_id in targets:
                if target_id not in party_ids:
                    raise ExplorationIntentError("spell target must be a party member")
            if effect.get("target_scope") == "self" and targets != [actor_id]:
                raise ExplorationIntentError("this spell can only target the caster")

    def _validate_roll_treasure(self, instance: Any, intent: dict[str, Any]) -> None:
        """Rolling a treasure table is a GM settlement action, never a combat intent."""

        gm_uid = str(getattr(instance, "gm_uid", "") or "")
        if not gm_uid or str(intent.get("submitted_by") or "") != gm_uid:
            raise ExplorationIntentError("only the game master can roll a treasure table")
        try:
            table_ref = canonical_table_ref(intent.get("table_ref"))
        except TreasureTableError as exc:
            raise ExplorationIntentError(str(exc)) from None
        if self.treasure.get(table_ref) is None:
            raise ExplorationIntentError(f"treasure table is not defined: {table_ref}")

    def _project_treasure_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """N4: add server-projected display labels without changing identity.

        ``item_ref`` stays the canonical identity; ``label`` is the localized
        display name when the bundle provides one, and is simply omitted
        otherwise. Currency gains a structured ``amount``/``unit`` pair so the
        client never parses a display string into a rules identity.
        """

        projected = deepcopy(result)
        items = projected.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    item_ref = canonical_item_ref(item.get("item_ref"))
                except AttunementError:
                    continue
                definition = self.bundle.get("item", item_ref.removeprefix("item:"))
                label = definition.get("name") if isinstance(definition, dict) else None
                if isinstance(label, str) and label.strip():
                    item["label"] = label
        coins = projected.get("coins_gp")
        if isinstance(coins, int) and not isinstance(coins, bool):
            projected["coins"] = {"amount": coins, "unit": "gp"}
        return projected

    def _roll_treasure(
        self, table_ref: str, rng: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Roll one die per nested table and return the auditable path + final result."""

        path: list[dict[str, Any]] = []
        current = canonical_table_ref(table_ref)
        seen: set[str] = set()
        for _ in range(8):
            if current in seen:
                raise ExplorationIntentError(f"treasure table nesting cycle: {current}")
            seen.add(current)
            table = self.treasure.get(current)
            if table is None:  # pragma: no cover - validation guards this
                raise ExplorationIntentError(f"treasure table is not defined: {current}")
            try:
                # 复用通用单骰原语：宝藏表只允许单骰、无修正，装载时已校验；
                # 这里不新增修正骰，也不改变骰位或嵌套顺序。
                value, _rolls = roll(table.roll, rng)
            except CombatIntentError as exc:  # pragma: no cover - load validation guards this
                raise ExplorationIntentError(str(exc)) from None
            entry = table.entry_for(value)
            path.append({
                "table_ref": current, "roll": value,
                "from": entry.from_value, "to": entry.to_value,
            })
            result = entry.result
            nested = result.get("table_ref")
            if not nested:
                return path, deepcopy(result)
            current = canonical_table_ref(nested)
        raise ExplorationIntentError("treasure table nesting exceeds 8 levels")

    def _validate_activate_item(
        self, instance: Any, intent: dict[str, Any], actor_id: str, uid: str,
        canonical: dict[str, Any],
    ) -> None:
        """Out of combat only self-targeted healing potions are mechanical."""

        try:
            item_ref = canonical_item_ref(intent.get("item_ref"))
        except AttunementError as exc:
            raise ExplorationIntentError(str(exc)) from None
        item = self.bundle.get("item", item_ref.removeprefix("item:"))
        if not isinstance(item, dict):
            raise ExplorationIntentError("item is not defined in the active bundle")
        try:
            declaration = validate_activation_declaration(item)
            validate_consumable_declaration(item)
            maximum = validate_charges_declaration(item)
            validate_destroy_declaration(item)
        except (ItemActivationError, ChargesError) as exc:
            raise ExplorationIntentError(str(exc)) from None
        if declaration is None:
            raise ExplorationIntentError("item cannot be activated")
        if str(item.get("item_type") or "") != POTION_ITEM_TYPE:
            raise ExplorationIntentError("exploration item use is limited to healing potions")
        if declaration["target"] != "self":
            raise ExplorationIntentError("exploration item use can only target the user")
        target_uid = str(intent.get("target_id") or actor_id).removeprefix("player:")
        if target_uid != uid:
            raise ExplorationIntentError("exploration item use can only target the user")
        payload = instance.get_character_sheet(uid)
        # Consumables are taken from the backpack by apply, so only inventory
        # rows are an authoritative consumable stop; equipment refs are not.
        owned = inventory_ref_counts(payload).get(item_ref, 0)
        if owned < 1:
            raise ExplorationIntentError("item is not carried")
        if item_requires_attunement(self.bundle, item_ref) and item_ref not in attuned_refs(canonical):
            raise ExplorationIntentError("item requires attunement")
        charge_cost = declaration.get("charge_cost")
        if maximum is not None:
            if charge_cost is None:
                raise ExplorationIntentError("charged item activation requires charge_cost")
            if owned > 1:
                raise ExplorationIntentError(
                    "cannot activate a charged item carried in multiple copies"
                )
            current = charges_of(canonical, item_ref)
            if current is None:
                raise ExplorationIntentError("item charges are not initialized")
            if current < int(charge_cost):
                raise ExplorationIntentError("not enough charges")
        elif charge_cost is not None:
            raise ExplorationIntentError("item has no charges")

    def resolve_intent(
        self, instance: Any, intent: dict[str, Any], rng: Any,
    ) -> dict[str, Any]:
        try:
            self._validate(instance, intent)
        except ExplorationIntentError as exc:
            return {"ok": False, "code": "INVALID_INTENT", "error": str(exc)}
        if str(intent.get("type") or "") == "exploration.roll_treasure":
            expected = int(intent["expected_version"])
            intent_id = str(intent.get("intent_id") or "")
            gm_actor = f"player:{str(getattr(instance, 'gm_uid', '') or '')}"
            events: list[dict[str, Any]] = [{
                "type": "intent.submitted", "intent_type": "exploration.roll_treasure",
                "actor_id": gm_actor,
                "submitted_by": str(intent.get("submitted_by") or ""),
            }]
            path, result = self._roll_treasure(
                canonical_table_ref(intent.get("table_ref")), rng,
            )
            events.append({
                "type": "dnd2024.treasure.rolled",
                "table_ref": canonical_table_ref(intent.get("table_ref")),
                "path": path, "result": self._project_treasure_result(result),
            })
            return {
                "ok": True, "events": events,
                "event_batch": self._batch(
                    intent_id, expected, events, "exploration.roll_treasure",
                ),
            }
        actor_id = _canonical_actor_id(intent["actor_id"])
        if str(intent.get("type") or "") == "exploration.activate_item":
            expected = int(intent["expected_version"])
            intent_id = str(intent.get("intent_id") or "")
            events = [{
                "type": "intent.submitted", "intent_type": "exploration.activate_item",
                "actor_id": actor_id, "submitted_by": str(intent.get("submitted_by") or ""),
            }]
            events.extend(self._activate_item_events(instance, intent, rng))
            return {
                "ok": True, "events": events,
                "event_batch": self._batch(
                    intent_id, expected, events, "exploration.activate_item",
                ),
            }
        spell_ref = str(intent["spell_ref"])
        spell = self.spells.get(spell_ref)
        if spell is None:  # pragma: no cover - validation guards this
            raise ExplorationIntentError("spell does not exist in the ruleset")
        ritual = bool(intent.get("ritual"))
        # 仪式不花槽：这里把 0 写死，而不是"信任客户端传了 0"。
        slot_level = 0 if ritual else int(intent.get("slot_level", spell["level"]) or 0)
        effect = self.catalog.spell_effects.get(spell_ref.removeprefix("spell:"))
        target_values = intent.get("target_ids")
        target_ids = [str(item) for item in (target_values or []) if str(item)]

        events: list[dict[str, Any]] = [{
            "type": "intent.submitted", "intent_type": "exploration.cast_spell",
            "actor_id": actor_id, "submitted_by": str(intent.get("submitted_by") or ""),
        }]
        expected = int(intent["expected_version"])
        intent_id = str(intent.get("intent_id") or "")
        caster = spell_caster(self._actor_view(instance, actor_id), spell_ref)
        if not ritual:
            events.extend(spell_cost_events(caster, spell, slot_level))
        if effect is not None and effect.get("concentration"):
            actor_canonical = self._canonical_of(instance, actor_id)
            if concentration_holder(actor_canonical).get("concentration"):
                events.append({
                    "type": "dnd2024.concentration.ended", "actor_id": actor_id,
                    "reason": "new_concentration_spell",
                })
            events.append({
                "type": "dnd2024.concentration.started", "actor_id": actor_id,
                "spell_ref": spell_ref, "target_ids": target_ids,
            })
        cast_event: dict[str, Any] = {
            "type": "dnd2024.spell.cast", "actor_id": actor_id, "spell_ref": spell_ref,
            "slot_level": slot_level, "target_ids": target_ids,
            "context": "exploration",
        }
        if ritual:
            # 事件必须说清"这一次是仪式"：审计要能区分"没花槽"的两种原因。
            cast_event["ritual"] = True
        events.append(cast_event)
        revive = revive_definition(spell_ref)
        if revive is not None:
            for target_id in target_ids:
                resources = self._canonical_of(instance, target_id).get("resources") or {}
                max_hp = int(resources.get("max_hp", 0) or 0)
                events.extend(revive_events(target_id, revive, max_hp))
            return {
                "ok": True, "events": events,
                "event_batch": self._batch(intent_id, expected, events),
            }
        if effect is None:
            # 未自动化的 utility spell：合法施放、扣资源、事件交给叙事层（§32）。
            events.append({
                "type": "dnd2024.spell.narrative", "actor_id": actor_id,
                "spell_ref": spell_ref, "slot_level": slot_level,
                "resolution": "narrative",
            })
            return {"ok": True, "events": events, "event_batch": self._batch(intent_id, expected, events)}
        for target_id in target_ids:
            target_canonical = self._canonical_of(instance, target_id)
            mode = str(effect["mode"])
            if mode == "healing":
                healing_node = spell_formula_node(
                    str(effect["healing"]), effect.get("upcast_healing"),
                    spell, slot_level, caster,
                )
                if effect.get("add_spell_ability"):
                    healing_node = heal_ability_modifier_node(
                        healing_node, caster,
                    )
                healing, rolls = roll_damage_node(healing_node, rng)
                healing = max(1, healing)
                resources = target_canonical.get("resources", {})
                before = int(resources.get("hp", 0) or 0)
                maximum = int(resources.get("max_hp", 0) or 0)
                events.append({
                    "type": "resource.changed", "resource": "hp", "target_id": target_id,
                    "delta": healing, "amount": healing, "healing": True, "rolls": rolls,
                    "hp_after": min(maximum, before + healing),
                })
            elif mode == "buff":
                condition = str(effect.get("condition") or "")
                if condition:
                    events.append({
                        "type": "condition.applied", "target_id": target_id,
                        "condition": condition,
                        "duration": str(effect.get("condition_duration") or ""),
                        "source_actor_id": actor_id,
                        "concentration_owner": actor_id if effect.get("concentration") else "",
                    })
        batch = self._batch(intent_id, expected, events)
        return {"ok": True, "events": events, "event_batch": batch}

    @staticmethod
    def _batch(
        intent_id: str, expected: int, events: list[dict[str, Any]],
        intent_type: str = "exploration.cast_spell",
    ) -> dict[str, Any]:
        return {
            "batch_id": stable_batch_id(
                {"intent_id": intent_id, "expected_version": expected}, expected,
            ),
            "intent_id": intent_id,
            "intent_type": intent_type,
            "expected_version": expected,
            "result_version": expected + 1,
            "events": events,
            "source_ref": "srd-5.2.1:p24-p27:playing-the-game",
        }

    def _activate_item_events(
        self, instance: Any, intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = _canonical_actor_id(intent["actor_id"])
        item_ref = canonical_item_ref(intent.get("item_ref"))
        item = self.bundle.get("item", item_ref.removeprefix("item:"))
        declaration = validate_activation_declaration(item)
        if declaration is None:  # pragma: no cover - validation guards this
            raise ExplorationIntentError("item cannot be activated")
        maximum = validate_charges_declaration(item)
        events: list[dict[str, Any]] = []
        charge_cost = declaration.get("charge_cost")
        # 与战斗内同一判定：免收时本次实际扣 0，事件与销毁检查都读这个值。
        spent: int | None = None
        if maximum is not None and charge_cost is not None:
            declared = int(charge_cost)
            spent = declared
            waiver = charge_waiver_roll(
                self._canonical_of(instance, actor_id), self.features, rng,
            )
            if waiver is not None:
                roll, threshold = waiver
                if roll >= threshold:
                    spent = 0
                    events.append({
                        "type": "dnd2024.item.charge_waived", "actor_id": actor_id,
                        "item_ref": item_ref, "declared": declared,
                        "roll": roll, "threshold": threshold,
                    })
            if spent:
                events.append({
                    "type": "dnd2024.item.charge_spent", "actor_id": actor_id,
                    "item_ref": item_ref, "amount": spent,
                })
        if validate_consumable_declaration(item):
            events.append({
                "type": "dnd2024.item.consumed", "actor_id": actor_id,
                "item_ref": item_ref, "amount": 1,
            })
        node = formula_node_from_dice_formula(declaration["effect"]["healing"])
        healing, rolls = roll_damage_node(node, rng)
        healing = max(1, healing)
        canonical = self._canonical_of(instance, actor_id)
        resources = canonical.get("resources", {})
        before = int(resources.get("hp", 0) or 0)
        maximum_hp = int(resources.get("max_hp", 0) or 0)
        conditions = canonical.get("conditions") or {}
        blocked = "cannot_regain_hp" in conditions
        events.append(build_item_healing_event(
            item_ref=item_ref,
            target_id=actor_id,
            rolls=rolls,
            healing=healing,
            hp_before=before,
            hp_max=maximum_hp,
            blocked_by="cannot_regain_hp" if blocked else None,
        ))
        events.append({
            "type": "dnd2024.item.activated", "actor_id": actor_id,
            "item_ref": item_ref, "target_id": actor_id,
            "cost": declaration["cost"],
        })
        # 与战斗内同一语义、同一事件形状：效果结算完毕后才判定销毁。
        if spent is not None:
            current = charges_of(self._canonical_of(instance, actor_id), item_ref)
            if current is not None:
                remaining = current - spent
                events.extend(destroy_check_events(
                    item,
                    actor_id=actor_id,
                    item_ref=item_ref,
                    remaining_charges=remaining if remaining >= 0 else None,
                    rng=rng,
                ))
        return events

    def _actor_view(self, instance: Any, actor_id: str) -> dict[str, Any]:
        """探索态施法者的最小施法视图（spell_formula_node 需要的形状）。"""
        canonical = self._canonical_of(instance, actor_id)
        class_magic = canonical.get("spellcasting", {}).get("class")
        class_magic = class_magic if isinstance(class_magic, dict) else {}
        derived = canonical.get("derived", {}) if isinstance(canonical.get("derived"), dict) else {}
        return {
            "actor_id": actor_id,
            **spellcasting_projection(canonical, self.features),
            "spell_attack_bonus": int(derived.get("spell_attack_bonus", 0) or 0),
            "spell_save_dc": int(derived.get("spell_save_dc", 0) or 0),
            "spell_ability": str(class_magic.get("ability") or ""),
            "abilities": deepcopy(canonical.get("abilities") or {}),
        }

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    def apply_batch(self, instance: Any, batch: dict[str, Any]) -> dict[str, Any]:
        ruleset_runtime.require_writable(instance)
        ruleset_runtime.require_binding(instance, self.bundle.manifest.runtime_id)
        snapshot = {
            "version": int(ruleset_runtime.state(instance).get("version", 0) or 0),
            "ruleset_state": deepcopy(ruleset_runtime.state(instance)),
            "characters": {
                uid: deepcopy(instance.get_character_sheet(uid).get("ruleset_character", {}))
                for uid in instance.players
            },
        }
        updated, ledger, duplicate = apply_event_batch(
            snapshot, ruleset_runtime.event_ledger(instance), batch, self._reduce_event,
        )
        if not duplicate:
            ruleset_state = updated["ruleset_state"]
            ruleset_state["version"] = updated["version"]
            from src.commands.state_items import (
                remove_destroyed_item,
                take_from_inventory,
            )

            updated_sheets: dict[str, dict[str, Any]] = {}
            for uid, canonical in updated["characters"].items():
                if uid not in instance.players:
                    continue
                sheet = deepcopy(instance.get_character_sheet(uid))
                sheet["ruleset_character"] = deepcopy(canonical)
                for event in batch.get("events") or []:
                    if (
                        str(event.get("type") or "") == "dnd2024.item.consumed"
                        and str(event.get("actor_id") or "") == f"player:{uid}"
                    ):
                        for _ in range(int(event.get("amount", 1) or 1)):
                            taken = take_from_inventory(sheet, "", str(event["item_ref"]))
                            if taken is None:
                                raise EventBatchError(
                                    f"item is no longer carried: {event['item_ref']}"
                                )
                    elif (
                        str(event.get("type") or "") == "dnd2024.item.destroyed"
                        and str(event.get("actor_id") or "") == f"player:{uid}"
                    ):
                        remove_destroyed_item(sheet, str(event["item_ref"]))
                updated_sheets[uid] = sheet
            ruleset_runtime.replace_state(instance, ruleset_state)
            for uid, sheet in updated_sheets.items():
                instance.set_character_sheet(uid, sheet)
            ruleset_runtime.replace_event_ledger(instance, ledger)
        return {
            "ok": True,
            "applied": not duplicate,
            "duplicate": duplicate,
            "state_version": int(ruleset_runtime.state(instance).get("version", 0) or 0),
            "event_batch": deepcopy(batch),
        }

    def _reduce_event(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        event_type = str(event["type"])
        if event_type in {"intent.submitted", "dnd2024.spell.cast", "dnd2024.spell.narrative"}:
            return
        if event_type == "dnd2024.character.revived":
            # 探索态没有 combat state，但 P9 的状态语义仍然适用：仅 player、
            # hp 至少 1 且不超上限、清 dead/unconscious/stable/death_saves。
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            if kind != "player" or raw_id not in snapshot["characters"]:
                raise EventBatchError("revived actor must be an existing player")
            character = snapshot["characters"][raw_id]
            resources = character.setdefault("resources", {})
            maximum = int(resources.get("max_hp", 0) or 0)
            hp = max(1, int(event.get("hp", 1) or 1))
            if maximum > 0:
                hp = min(maximum, hp)
            resources["hp"] = hp
            conditions = character.setdefault("conditions", {})
            for condition in ("dead", "unconscious", "stable", "death_saves"):
                conditions.pop(condition, None)
            return
        if event_type in {
            "dnd2024.free_cast.spent", "dnd2024.feat_spell.free_cast_spent",
            "dnd2024.class_resource.spent",
        }:
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            if kind != "player" or raw_id not in snapshot["characters"]:
                raise EventBatchError("exploration casting is player-only")
            try:
                apply_free_cast_cost(snapshot["characters"][raw_id], event)
            except ValueError as exc:
                raise EventBatchError(str(exc)) from exc
            return
        if event_type == "dnd2024.spell.slot_spent":
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            if kind != "player":
                raise EventBatchError("exploration casting is player-only")
            canonical = snapshot["characters"][raw_id]
            slots = canonical["spellcasting"]["class"]["slots_current"]
            level = str(event["slot_level"])
            if int(slots.get(level, 0) or 0) < 1:
                raise EventBatchError("spell slot is already spent")
            slots[level] -= 1
            return
        if event_type == "dnd2024.spell.font_slot_spent":
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            if kind != "player":
                raise EventBatchError("exploration casting is player-only")
            stored = snapshot["characters"][raw_id]["spellcasting"]["class"].get(
                "font_slots"
            )
            if not isinstance(stored, dict):
                raise EventBatchError("font slots are not initialized")
            level = str(event["slot_level"])
            remaining = stored.get(level)
            if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 1:
                raise EventBatchError("temporary spell slot is already spent")
            if remaining == 1:
                stored.pop(level)
            else:
                stored[level] = remaining - 1
            return
        if event_type == "dnd2024.concentration.started":
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            canonical = (
                snapshot["characters"][raw_id] if kind == "player"
                else self._companion_canonical(snapshot, raw_id)
            )
            concentration_holder(canonical)["concentration"] = {
                "spell_ref": event["spell_ref"], "target_ids": deepcopy(event["target_ids"]),
            }
            return
        if event_type == "dnd2024.concentration.ended":
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            if kind == "player":
                canonical = snapshot["characters"][raw_id]
            else:
                canonical = self._companion_canonical(snapshot, raw_id)
            concentration_holder(canonical)["concentration"] = None
            self._remove_concentration_conditions(snapshot, str(event["actor_id"]))
            return
        if event_type == "dnd2024.treasure.rolled":
            # Audit-only: the rolled result must still go through the reward
            # proposal / GM gate, so this reducer never writes inventory or coins.
            unknown = sorted(set(event) - {"type", "table_ref", "path", "result"})
            if unknown:
                raise EventBatchError(f"unknown dnd2024.treasure.rolled field: {unknown[0]}")
            path = event.get("path")
            if not isinstance(path, list) or not path:
                raise EventBatchError("treasure roll path must be a non-empty array")
            if len(path) > 8:
                raise EventBatchError("treasure roll path exceeds 8 levels")
            for layer in path:
                if not isinstance(layer, dict) or set(layer) != {
                    "table_ref", "roll", "from", "to",
                }:
                    raise EventBatchError("treasure roll path layer is invalid")
                try:
                    table_ref = canonical_table_ref(layer["table_ref"])
                except TreasureTableError as exc:
                    raise EventBatchError(str(exc)) from None
                if self.treasure.get(table_ref) is None:
                    raise EventBatchError(f"treasure table is not defined: {table_ref}")
                for key in ("roll", "from", "to"):
                    if isinstance(layer[key], bool) or not isinstance(layer[key], int):
                        raise EventBatchError("treasure roll path integers are invalid")
                if not 1 <= layer["from"] <= layer["to"]:
                    raise EventBatchError("treasure roll path interval is invalid")
            result = event.get("result")
            if (
                not isinstance(result, dict) or len(result) == 0
                or set(result) - {"table_ref", "items", "coins_gp", "coins"}
            ):
                raise EventBatchError("treasure roll result shape is invalid")
            if "coins" in result:
                coins = result["coins"]
                if (
                    not isinstance(coins, dict)
                    or set(coins) != {"amount", "unit"}
                    or isinstance(coins["amount"], bool)
                    or not isinstance(coins["amount"], int)
                    or coins["amount"] < 0
                    or not isinstance(coins["unit"], str)
                    or not coins["unit"]
                ):
                    raise EventBatchError("treasure roll coins shape is invalid")
                if coins["amount"] != result.get("coins_gp"):
                    raise EventBatchError("treasure roll coins disagree with coins_gp")
            items = result.get("items")
            if items is not None:
                if not isinstance(items, list):
                    raise EventBatchError("treasure roll items shape is invalid")
                for item in items:
                    if (
                        not isinstance(item, dict)
                        or set(item) - {"item_ref", "qty", "label"}
                        or isinstance(item.get("qty"), bool)
                        or not isinstance(item.get("qty"), int)
                        or "label" in item and not isinstance(item["label"], str)
                    ):
                        raise EventBatchError("treasure roll item shape is invalid")
            return
        if event_type in _ITEM_EVENT_FIELDS:
            unknown = sorted(set(event) - _ITEM_EVENT_FIELDS[event_type])
            if unknown:
                raise EventBatchError(f"unknown {event_type} field: {unknown[0]}")
            if event_type != "dnd2024.item.charge_spent":
                # 数量扣减由 apply_batch 通过 live backpack 权威接口落账。
                return
            kind, raw_id = str(event["actor_id"]).split(":", 1)
            if kind != "player":
                raise EventBatchError("exploration item use is player-only")
            canonical = snapshot["characters"][raw_id]
            try:
                item_ref = canonical_item_ref(event["item_ref"])
                item = self.bundle.get("item", item_ref.removeprefix("item:"))
                maximum = validate_charges_declaration(item)
            except (AttunementError, ChargesError) as exc:
                raise EventBatchError(str(exc)) from None
            if maximum is None:
                raise EventBatchError("item has no charges")
            state = (canonical.get("equipment") or {}).get("item_state")
            charges = state.get(item_ref) if isinstance(state, dict) else None
            entry = charges.get("charges") if isinstance(charges, dict) else None
            current = entry.get("current") if isinstance(entry, dict) else None
            try:
                new_current = spend_charges(current, event["amount"], maximum)
            except ChargesError as exc:
                raise EventBatchError(str(exc)) from None
            entry["current"] = new_current
            return
        if event_type == "resource.changed":
            self._apply_hp_change(snapshot, event)
            return
        if event_type == "condition.applied":
            conditions = self._conditions(snapshot, str(event["target_id"]))
            conditions[str(event["condition"])] = {
                key: deepcopy(value) for key, value in event.items()
                if key not in {"type", "target_id", "condition"}
            }
            return
        if event_type == "condition.removed":
            self._conditions(snapshot, str(event["target_id"])).pop(str(event["condition"]), None)
            return
        raise EventBatchError(f"unsupported exploration event type: {event_type}")

    def _companion_canonical(self, snapshot: dict[str, Any], companion_id: str) -> dict[str, Any]:
        companions = snapshot["ruleset_state"].setdefault("party", {}).setdefault("companions", {})
        companion = companions.get(companion_id)
        if not isinstance(companion, dict) or not isinstance(
            companion.get("ruleset_character"), dict,
        ):
            raise EventBatchError("companion canonical character is missing")
        return companion["ruleset_character"]

    def _conditions(self, snapshot: dict[str, Any], actor_id: str) -> dict[str, Any]:
        kind, raw_id = actor_id.split(":", 1)
        if kind == "player":
            return snapshot["characters"][raw_id].setdefault("conditions", {})
        return self._companion_canonical(snapshot, raw_id).setdefault("conditions", {})

    def _apply_hp_change(self, snapshot: dict[str, Any], event: dict[str, Any]) -> None:
        target_id = str(event["target_id"])
        kind, raw_id = target_id.split(":", 1)
        if kind == "player":
            character = snapshot["characters"][raw_id]
        else:
            character = self._companion_canonical(snapshot, raw_id)
        resources = character["resources"]
        before = int(resources.get("hp", 0) or 0)
        maximum = int(resources.get("max_hp", 0) or 0)
        resources["hp"] = max(0, min(maximum, before + int(event["delta"])))
        if resources["hp"] > 0:
            conditions = character.setdefault("conditions", {})
            conditions.pop("unconscious", None)
            conditions.pop("stable", None)
            conditions["death_saves"] = {"successes": 0, "failures": 0}

    def _remove_concentration_conditions(self, snapshot: dict[str, Any], owner: str) -> None:
        party = snapshot["ruleset_state"].get("party", {}) or {}
        actor_ids = [
            *(f"player:{uid}" for uid in snapshot["characters"]),
            *(f"companion:{companion_id}" for companion_id in party.get("companions", {})),
        ]
        for actor_id in actor_ids:
            conditions = self._conditions(snapshot, actor_id)
            for condition_id in [
                key for key, value in conditions.items()
                if isinstance(value, dict) and value.get("concentration_owner") == owner
            ]:
                conditions.pop(condition_id, None)
