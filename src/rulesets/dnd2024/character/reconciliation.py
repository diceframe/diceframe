"""Re-derive D&D 2024 canonical mechanics from live character state.

Authority runs one way only::

    character_sheet.equipment      <- 当前实际穿戴（live authority）
    character_sheet.inventory      <- 当前持有但未穿戴
    ruleset_character.equipment.item_refs
                                   <- 上面两者的 Ruleset Projection，不是第三份事实

``equipment.item_grants`` stays what it always was: character creation
provenance.  It records what a class/background package once handed out and is
never rewritten here, so selling or dropping starter gear can not be undone by
a later projection.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from .attunement import attuned_refs, attunement_capacity, owned_ref_counts
from .attunement import attunement_state_for_projection, pending_attunement_refs
from .derivation import derive_armor_class, derive_armor_training_state, derive_speed
from .primitives import ref_id as _ref_id
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.items.charges import initialize_item_state

logger = logging.getLogger("trpg")

_MAX_OPERATION_LOG = 32

# 参与 canonical 装备投影的物品类型。其余（消耗品、工具包、箭矢…）留在 live
# inventory，不进入规则投影。
_PROJECTED_ITEM_TYPES = frozenset({"weapon", "armor", "shield", "focus"})

# Ruleset item type -> generic equipment slot.
#
# The generic layer owns the slot vocabulary but cannot know what a ruleset item
# is: with no item definition to consult it drops every non-weapon into "body",
# so putting on a shield or a holy symbol would evict the armor.  Mapping the
# ruleset's own item types onto generic slots keeps that knowledge where it
# belongs -- the specific layer depends on the generic vocabulary, never the
# other way round.  Armor deliberately keeps the historical "body" slot so
# existing saves need no migration.
_GENERIC_SLOTS = {
    "weapon": "main_hand",
    "armor": "body",
    "shield": "off_hand",
    "focus": "accessory",
}


# Fields play owns, and a lifecycle re-projection must therefore never rebuild.
#
# The list is an explicit allow-list rather than "everything except resources":
# HP, hit dice and spell slots are ruleset-owned precisely because rest, combat
# and advancement are supposed to move them.  Getting that backwards would make
# a long rest fail to heal.
LIVE_OWNED_FIELDS: tuple[str, ...] = (
    "inventory", "equipment", "key_items", "currency", "gold",
)


def merge_live_character_projection(
    current: Mapping[str, Any], projected: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-project a character without rolling back what play has changed.

    ``project_legacy`` rebuilds the whole legacy sheet from the creation
    packages.  That is right when a character is created or imported, and wrong
    for one that has been played: a rest or a level up would hand back the
    starter kit, resurrect sold gear and refill the purse.

    Live-owned fields are taken from ``current``; everything else comes from
    the fresh projection.
    """

    merged = dict(projected)
    for field in LIVE_OWNED_FIELDS:
        if field in current:
            merged[field] = deepcopy(current[field])
    return merged


def _base_speed(canonical: Mapping[str, Any], bundle: Any) -> int:
    """The species Speed the derived value starts from, or 0 when unknown.

    A legacy sheet may lack ``sources.species_ref``; reporting 0 lets the caller
    keep the stored value instead of rewriting it to a guessed one.
    """

    species_ref = str(
        (canonical.get("sources") or {}).get("species_ref") or ""
    ).removeprefix("species:")
    if not species_ref:
        return 0
    species = bundle.get("species", species_ref) or {}
    value = species.get("speed")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _normalized(text: Any) -> str:
    return str(text or "").strip().casefold()


def _resolve_base_speed(
    canonical: Mapping[str, Any],
    derived: Mapping[str, Any],
    bundle: Any,
) -> int:
    """The Speed a derived value is recomputed from, or 0 when unknowable.

    ``_base_speed`` answers 0 for a legacy sheet that lost
    ``sources.species_ref``.  Leaving Speed alone is right for a sheet with no
    armour penalty to apply, but wrong the moment ``armor_training`` reports
    one: the sheet then claims "-10 ft" while still showing the unpenalised
    number, and combat used to hide that by subtracting the penalty a second
    time on top of the already-net ``derived.speed``.

    When the species is unknown the stored value is the only remaining
    candidate, and it is the honest one: before the penalty moved into
    ``derive_speed`` that field held the species value, so recomputing from it
    applies the penalty exactly once.
    """

    base = _base_speed(canonical, bundle)
    if base > 0:
        return base
    stored = derived.get("speed")
    if isinstance(stored, int) and not isinstance(stored, bool):
        return stored
    return 0


def build_item_identity_index(bundles: Iterable[Any]) -> dict[str, str]:
    """Map every display name the ruleset ships, in every locale, to an item id.

    Locale never participates in rule authority: 锁子甲 / Chain Mail both have
    to land on ``item:chain_mail``.  The index is built from the bundle's own
    localized item definitions, so it stays correct when content is translated
    or renamed -- unlike a hand-maintained table of strings.
    """

    index: dict[str, str] = {}
    for bundle in bundles:
        for item in bundle.list("item"):
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            index.setdefault(_normalized(item_id), item_id)
            name = _normalized(item.get("name"))
            if name:
                index.setdefault(name, item_id)
    return index


def _compat_alias_index(known_ids: frozenset[str]) -> dict[str, str]:
    """Legacy display-name aliases, kept strictly as an old-save compat path.

    These cover locales the bundle does not ship content for (ja) and older
    hand-written names.  Only aliases that resolve to an item the ruleset
    actually defines are accepted, so the table can never invent mechanics.
    """

    from src.engine.constants import ITEM_KEY_ALIASES

    return {
        _normalized(alias): item_key
        for alias, item_key in ITEM_KEY_ALIASES.items()
        if item_key in known_ids
    }


class Dnd2024CharacterStateReconciler:
    """Project live equipment onto canonical D&D 2024 mechanics.

    Idempotent by construction: it compares the freshly derived projection with
    what the character already carries and writes nothing -- no canonical
    change, no revision bump, no operation log entry -- when they agree.
    """

    def __init__(self, bundle: Any, *, locale_bundles: Sequence[Any] = ()) -> None:
        self.bundle = bundle
        self._names = build_item_identity_index(locale_bundles or (bundle,))
        self._known_ids = frozenset(
            str(item.get("id") or "") for item in bundle.list("item")
        )
        self._aliases = _compat_alias_index(self._known_ids)

    # ------------------------------------------------------------------
    # canonical identity resolution
    # ------------------------------------------------------------------

    def _resolve_item_id(self, row: Mapping[str, Any]) -> str:
        """Resolve one live equipment row to a canonical item id.

        Priority is fixed by contract: an explicit ``item_ref`` wins, then the
        generic layer's ``item_key``, and only then -- as an old-save compat
        path -- the display name.  A localized name is never allowed to be the
        lasting identity; see :meth:`reconcile`, which writes the resolved
        ``item_ref`` back onto the row so the name is not consulted again.
        """

        raw_ref = str(row.get("item_ref") or "")
        if raw_ref:
            item_id = _ref_id(raw_ref, "item")
            if item_id in self._known_ids:
                return item_id

        item_key = str(row.get("item_key") or "")
        if item_key and item_key in self._known_ids:
            return item_key

        name = _normalized(row.get("name"))
        if not name:
            return ""
        return self._names.get(name) or self._aliases.get(name, "")

    def _project_equipped_refs(
        self, equipment: Sequence[Any], warnings: list[str],
    ) -> list[str]:
        """Return the canonical refs for what is currently worn."""

        refs: list[str] = []
        for row in equipment:
            if not isinstance(row, dict):
                continue
            item_id = self._resolve_item_id(row)
            if not item_id:
                # §64：解析不出规则身份时只记录警告并忽略其专业规则效果，
                # 绝不猜一个最像的物品，更不能因此删除玩家的装备。
                warnings.append(f"unresolved live equipment: {row.get('name')!r}")
                continue
            item = self.bundle.get("item", item_id) or {}
            if item.get("item_type") in _PROJECTED_ITEM_TYPES:
                refs.append(f"item:{item_id}")
        return list(dict.fromkeys(refs))

    def _annotate_live_rows(self, rows: Iterable[Any]) -> None:
        """Write canonical identity and placement onto live rows in place.

        Purchased or looted rows arrive carrying only a display name, so the
        generic layer has nothing to place them by.  Filling in ``item_ref``,
        ``type`` and ``slot`` here means a later equip lands in the right slot
        without the generic layer ever learning a D&D rule, and the localized
        name is never consulted again for this row.

        Identity and placement are *technical* metadata derived from the ruleset
        item definition, so a value that contradicts the definition is repaired
        rather than preserved: a save written back when the generic layer had to
        guess placed a shield at ``armor/body``, and keeping that guess would
        evict the real body armor forever.  Player-owned metadata (``name``,
        ``effect``, ``quality``, ``qty``, custom descriptions) is never touched.
        """

        for row in rows:
            if not isinstance(row, dict):
                continue
            self._annotate_row(row)

    def _annotate_row(self, row: dict[str, Any]) -> bool:
        """Annotate one live row in place; return whether anything changed."""

        item_id = self._resolve_item_id(row)
        if not item_id:
            return False
        item = self.bundle.get("item", item_id) or {}
        item_type = str(item.get("item_type") or "")
        slot = _GENERIC_SLOTS.get(item_type, "")
        changed = False
        if row.get("item_ref") != f"item:{item_id}":
            row["item_ref"] = f"item:{item_id}"
            changed = True
        if row.get("item_key") != item_id:
            row["item_key"] = item_id
            changed = True
        # 技术 metadata：以规则定义为准，冲突值直接修正。
        if item_type and row.get("type") != item_type:
            row["type"] = item_type
            changed = True
        if slot and str(row.get("slot") or "") != slot:
            row["slot"] = slot
            changed = True
        return changed

    def prepare_owned_rows(
        self, sheet: dict[str, Any], item_names: Iterable[str] = (),
    ) -> list[str]:
        """Canonicalize owned rows *before* the generic layer equips them.

        The generic equip resolves a target slot from the row it is given: with
        no ruleset metadata a non-weapon falls back to ``body`` and evicts the
        armor actually worn.  Running this first means the row already carries
        ``item_ref`` / ``type`` / ``slot`` when that decision is made, so the
        ruleset's knowledge is applied by the ruleset, and the generic layer
        still learns nothing about D&D.

        ``item_names`` limits the work to the rows this mutation will touch;
        an empty iterable means every row.  Returns the canonical ids that were
        (re-)established on rows whose identity resolved.
        """

        wanted = {
            _normalized(name) for name in item_names if _normalized(name)
        }
        prepared: list[str] = []
        for field in ("equipment", "inventory"):
            rows = sheet.get(field)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if wanted and _normalized(row.get("name")) not in wanted:
                    continue
                item_id = self._resolve_item_id(row)
                if not item_id:
                    continue
                before = (
                    row.get("item_ref"), row.get("item_key"),
                    row.get("type"), row.get("slot"),
                )
                self._annotate_row(row)
                after = (
                    row.get("item_ref"), row.get("item_key"),
                    row.get("type"), row.get("slot"),
                )
                if after != before or not wanted:
                    prepared.append(item_id)
        return list(dict.fromkeys(prepared))

    # ------------------------------------------------------------------
    # reconciliation
    # ------------------------------------------------------------------

    def _armor_rules(
        self,
        canonical: dict[str, Any],
        item_refs: Sequence[str],
        warnings: list[str],
    ) -> dict[str, Any] | None:
        """Derive both armor consequences from one set of canonical facts.

        Shared by the live-equipment path and the canonical-only path so the AC
        formula and the training formula can never drift apart.  Missing rule
        inputs return ``None`` (fail closed): an absent proficiency list must
        never be read as "trained in everything".
        """

        abilities = canonical.get("abilities")
        if not isinstance(abilities, dict) or not {"str", "dex"}.issubset(abilities):
            warnings.append(
                "canonical abilities are unavailable; armor rules not re-derived"
            )
            return None
        armor_category_refs = (
            (canonical.get("proficiencies") or {}).get("armor_category_refs")
        )
        if not isinstance(armor_category_refs, list):
            warnings.append(
                "canonical armor proficiencies are unavailable; armor rules not re-derived"
            )
            return None
        features = Dnd2024ClassFeatureResolver(self.bundle)
        bound = sorted(attuned_refs(canonical))
        feat_state = canonical.get("features")
        owned_feats = feat_state.get("feat_refs") if isinstance(feat_state, dict) else ()
        armor_training = derive_armor_training_state(
            abilities, item_refs, armor_category_refs, self.bundle,
        )
        armor_class = derive_armor_class(
            abilities, item_refs, self.bundle,
            armor_category_refs=armor_category_refs, warnings=warnings,
            unarmored_ability_bonuses=features.unarmored_ac_bonus_abilities(canonical),
            attuned_refs=bound,
            feat_refs=owned_feats if isinstance(owned_feats, list) else (),
        )
        return {
            "armor_training": armor_training,
            "armor_class": armor_class,
            "attunement_slots": attunement_capacity(canonical, features),
            "attuned_refs": bound,
            "attunement_pending": pending_attunement_refs(
                item_refs, self.bundle, bound,
            ),
        }

    def reconcile(
        self, sheet: dict[str, Any], changed_domains: frozenset[str] = frozenset(),
    ) -> dict[str, Any] | None:
        """Re-derive the projection from ``sheet``; return a summary or ``None``.

        ``None`` means the projection was already current and the sheet was not
        touched.  ``changed_domains`` is only a hint about which live facts
        moved; the authoritative values are re-read from the sheet here.
        """

        canonical = sheet.get("ruleset_character")
        if not isinstance(canonical, dict):
            return None
        equipment = sheet.get("equipment")
        if not isinstance(equipment, list):
            equipment = []

        inventory = sheet.get("inventory")
        if not isinstance(inventory, list):
            inventory = []

        warnings: list[str] = []
        # §42：解析成功就把 canonical identity 补齐到 live metadata。背包行也要
        # 标注，否则刚买来的盾牌在装备时仍然没有槽位信息可用。
        self._annotate_live_rows(equipment)
        self._annotate_live_rows(inventory)
        item_refs = self._project_equipped_refs(equipment, warnings)

        rules = self._armor_rules(canonical, item_refs, warnings)
        if rules is None:
            for warning in warnings:
                logger.warning("D&D2024 装备投影告警: %s", warning)
            return None
        armor_training = rules["armor_training"]
        armor_class = rules["armor_class"]
        attuned = rules["attuned_refs"]
        slots = rules["attunement_slots"]
        pending = rules["attunement_pending"]
        features = Dnd2024ClassFeatureResolver(self.bundle)
        derived = canonical.setdefault("derived", {})
        # 速度与 AC 同源：换甲、持盾、力量不足都走同一入口重算。species 未知
        # 的旧档从已存的速度重算：既不改写成 0，也不让护甲惩罚落空。
        base_speed = _resolve_base_speed(canonical, derived, self.bundle)
        speed = (
            derive_speed(
                base_speed,
                feature_bonus=features.speed_bonus(canonical),
                armor_training=armor_training,
            )
            if base_speed > 0
            else None
        )
        # 旧档没有同调段：按空集处理，并在一次投影内补写规范化结构。
        migrated = "attunement" not in canonical
        if migrated:
            warnings.append(
                "canonical attunement state is missing; initialised empty"
            )
        features = Dnd2024ClassFeatureResolver(self.bundle)

        canonical_equipment = canonical.setdefault("equipment", {})
        previous_item_state = deepcopy(canonical_equipment.get("item_state"))
        # 零数量只是历史行，不属于持有；只有聚合数量大于 0 的才参与初始化。
        held_refs = {
            ref for ref, count in owned_ref_counts(sheet).items() if count > 0
        }
        initialize_item_state(canonical, self.bundle, held_refs)
        previous_refs = list(canonical_equipment.get("item_refs") or [])
        previous_ac = derived.get("armor_class")
        previous_training = derived.get("armor_training")
        previous_speed = derived.get("speed")
        previous_attuned = list(derived.get("attuned_refs") or [])
        previous_slots = derived.get("attunement_slots")
        previous_pending = list(derived.get("attunement_pending") or [])
        mirror_ac = sheet.get("armor_class")
        mirror_attunement = sheet.get("attunement")
        projected_attunement = {
            **attunement_state_for_projection(canonical, slots),
            "pending": pending,
        }

        for warning in warnings:
            logger.warning("D&D2024 装备投影告警: %s", warning)

        if (
            not migrated
            and canonical.get("attunement") == {"refs": attuned}
            and previous_refs == item_refs
            and previous_ac == armor_class
            and mirror_ac == armor_class
            and previous_training == armor_training
            and (speed is None or previous_speed == speed)
            and previous_attuned == attuned
            and previous_slots == slots
            and previous_pending == pending
            and mirror_attunement == projected_attunement
            and canonical_equipment.get("item_state") == previous_item_state
        ):
            return None

        canonical_equipment["item_refs"] = item_refs
        canonical["attunement"] = {"refs": attuned}
        derived["armor_class"] = armor_class
        # 受训状态可以独立于 AC 变化（皮甲换链甲衫 AC 相同但门控不同），
        # 因此幂等短路必须比较训练段。
        derived["armor_training"] = armor_training
        if speed is not None:
            # 速度没有 legacy mirror：canonical ``derived.speed`` 是唯一权威。
            derived["speed"] = speed
        derived["attuned_refs"] = attuned
        derived["attunement_slots"] = slots
        derived["attunement_pending"] = pending
        sheet["attunement"] = projected_attunement
        # 装备是职业特性生效前提的一部分（武艺要求未穿甲、未持盾、只持 Monk
        # Weapon）。canonical 装备刚变过，用户可见的职业特性/资源投影必须跟着
        # 变，否则界面会继续展示已经不再生效的武艺骰。
        sheet.update(features.projection_fields(canonical))
        # §28：legacy / public mirror 必须与 canonical 一致，因为 Combat、UI、
        # LLM view 可能读不同层；但计算 authority 仍然只有上面那一处。
        sheet["armor_class"] = armor_class

        sources = canonical.get("sources")
        if isinstance(sources, dict) and isinstance(sources.get("content_entities"), list):
            sources["content_entities"] = list(dict.fromkeys(
                [*sources["content_entities"], *item_refs],
            ))

        revision = int(sheet.get("ruleset_revision", 0) or 0) + 1
        sheet["ruleset_revision"] = revision
        raw_log = sheet.get("ruleset_operation_log")
        operation_log = list(raw_log) if isinstance(raw_log, list) else []
        operation_log.append({
            "kind": "character_reconcile",
            "domains": sorted(changed_domains),
            "revision": revision,
        })
        sheet["ruleset_operation_log"] = operation_log[-_MAX_OPERATION_LOG:]

        return {
            "item_refs": item_refs,
            "armor_class": armor_class,
            "revision": revision,
            "warnings": warnings,
        }

    def reconcile_canonical(
        self, canonical: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Re-derive a record that has no live equipment layer.

        Companions live entirely in ``ruleset_state.party.companions``;
        ``ruleset_character.equipment.item_refs`` is the only equipment fact
        available for them.  Using it is deterministic, while an absent live
        list would otherwise project "wearing nothing" and erase the refs.

        Only the canonical projection is written -- no sheet mirror, revision
        or operation log exists on that record.  Missing abilities or armor
        proficiencies leave it untouched (fail closed) instead of assuming
        training.
        """

        item_refs = list((canonical.get("equipment") or {}).get("item_refs") or [])
        equipment = canonical.setdefault("equipment", {})
        previous_item_state = deepcopy(equipment.get("item_state"))
        initialize_item_state(canonical, self.bundle, item_refs)
        warnings: list[str] = []
        rules = self._armor_rules(canonical, item_refs, warnings)
        for warning in warnings:
            logger.warning("D&D2024 装备投影告警: %s", warning)
        if rules is None:
            return None
        armor_training = rules["armor_training"]
        armor_class = rules["armor_class"]
        attuned = rules["attuned_refs"]
        slots = rules["attunement_slots"]
        pending = rules["attunement_pending"]
        features = Dnd2024ClassFeatureResolver(self.bundle)
        derived = canonical.setdefault("derived", {})
        base_speed = _resolve_base_speed(canonical, derived, self.bundle)
        speed = (
            derive_speed(
                base_speed,
                feature_bonus=features.speed_bonus(canonical),
                armor_training=armor_training,
            )
            if base_speed > 0
            else None
        )
        migrated = "attunement" not in canonical
        if migrated:
            warnings.append(
                "canonical attunement state is missing; initialised empty"
            )
        if (
            not migrated
            and canonical.get("attunement") == {"refs": attuned}
            and derived.get("armor_class") == armor_class
            and derived.get("armor_training") == armor_training
            and (speed is None or derived.get("speed") == speed)
            and derived.get("attuned_refs") == attuned
            and derived.get("attunement_slots") == slots
            and derived.get("attunement_pending") == pending
            and equipment.get("item_state") == previous_item_state
        ):
            return None
        canonical["attunement"] = {"refs": attuned}
        derived["armor_class"] = armor_class
        derived["armor_training"] = armor_training
        if speed is not None:
            derived["speed"] = speed
        derived["attuned_refs"] = attuned
        derived["attunement_slots"] = slots
        derived["attunement_pending"] = pending
        return {
            "item_refs": item_refs,
            "armor_class": armor_class,
            "armor_training": armor_training,
            "attunement_slots": slots,
            "attunement_pending": pending,
            "warnings": warnings,
        }
