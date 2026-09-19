"""D&D 2024 module content catalog（母方案 §87/§110）。

模块 ruleset catalog 的契约（DNDMOD-00）、装载与解析（DNDMOD-01/02）。
rules runtime 拥有 D&D mechanics 数据的校验 authority；generic 层不 import
本包（MOD-01 边界）。
"""

from src.rulesets.dnd2024.content.contracts import (
    ABILITY_SCORES,
    CATALOG_KINDS,
    ITEM_CATEGORIES,
    CatalogContractError,
    DAMAGE_DICE_RE,
    validate_encounter_profile,
    validate_item_record,
    validate_monster_profile,
    validate_npc_statblock,
)

__all__ = [
    "ABILITY_SCORES",
    "CATALOG_KINDS",
    "DAMAGE_DICE_RE",
    "ITEM_CATEGORIES",
    "CatalogContractError",
    "validate_encounter_profile",
    "validate_item_record",
    "validate_monster_profile",
    "validate_npc_statblock",
]
