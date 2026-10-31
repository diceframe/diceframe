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

from .derivation import derive_armor_class
from .primitives import ref_id as _ref_id
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver

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


def _normalized(text: Any) -> str:
    return str(text or "").strip().casefold()


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

        abilities = canonical.get("abilities")
        if not isinstance(abilities, dict) or "dex" not in abilities:
            warnings.append("canonical abilities are unavailable; armor class not re-derived")
            return None
        features = Dnd2024ClassFeatureResolver(self.bundle)
        armor_class = derive_armor_class(
            abilities, item_refs, self.bundle, warnings=warnings,
            unarmored_ability_bonuses=features.unarmored_ac_bonus_abilities(canonical),
        )

        canonical_equipment = canonical.setdefault("equipment", {})
        derived = canonical.setdefault("derived", {})
        previous_refs = list(canonical_equipment.get("item_refs") or [])
        previous_ac = derived.get("armor_class")
        mirror_ac = sheet.get("armor_class")

        for warning in warnings:
            logger.warning("D&D2024 装备投影告警: %s", warning)

        if (
            previous_refs == item_refs
            and previous_ac == armor_class
            and mirror_ac == armor_class
        ):
            return None

        canonical_equipment["item_refs"] = item_refs
        derived["armor_class"] = armor_class
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
