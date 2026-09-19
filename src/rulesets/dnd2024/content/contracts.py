"""D&D 2024 module content catalog contracts (DNDMOD-00, 母方案 §29/§110).

模块 ruleset catalog 的**数据契约**：monster / npc_statblock / item /
encounter_profile 四类首版内容的持久化 shape 与 fail-closed 校验。

对齐既有代码事实：

- 数值边界与 ``src.rulesets.dnd2024.combat.validation`` 一致且取其严者
  （hp 1..10000、armor_class 1..40、speed 0..200、attack_bonus、damage 公式
  必须匹配 combat 的骰式），因为 catalog 数据最终进入权威战斗；
- 引用使用 ``src.content_modules.refs`` 的 ContentRef（MOD-05），encounter
  敌人以 ContentRef 指向 core / module / adventure-local 怪物（母方案 §112）；
- source 语法沿用 ``world.contracts.validate_source_ref``（MOD-05 同源真值）。

本模块只校验 shape；目录的装载 / 解析链 / 优先级在 DNDMOD-01/02。
"""

from __future__ import annotations

import re
from typing import Any

from src.content_modules.refs import parse_content_ref
from src.engine.world.contracts import validate_source_ref

# 与 combat.validation 相同的骰式（damage 必须可确定性结算）。
DAMAGE_DICE_RE = re.compile(r"^[1-9]\d*d(?:4|6|8|10|12|20)(?:\+[1-9]\d*)?$")

ABILITY_SCORES = ("str", "dex", "con", "int", "wis", "cha")
_ABILITY_BOUNDS = (1, 30)

CATALOG_KINDS = ("monster", "npc_statblock", "item", "encounter_profile")

_ENCOUNTER_DIFFICULTIES = ("story", "standard", "challenging", "lethal")


class CatalogContractError(ValueError):
    """A module catalog record is invalid: fail closed."""


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CatalogContractError(f"{label} must be an integer in {minimum}..{maximum}")
    return value


def _required_text(value: Any, label: str, *, maximum: int = 200) -> str:
    text = str(value or "").strip()
    if not text:
        raise CatalogContractError(f"{label} is required")
    if len(text) > maximum:
        raise CatalogContractError(f"{label} is too long (>{maximum})")
    return text


def _optional_source_ref(value: Any, label: str) -> str | None:
    if value is None:
        return None
    try:
        return validate_source_ref(value)
    except ValueError as exc:
        raise CatalogContractError(f"{label}: {exc}") from exc


def _validate_abilities(abilities: Any, label: str) -> dict[str, int]:
    if not isinstance(abilities, dict) or set(abilities) != set(ABILITY_SCORES):
        raise CatalogContractError(
            f"{label} must contain exactly {', '.join(ABILITY_SCORES)}"
        )
    return {
        score: _bounded_int(value, f"{label}.{score}", *_ABILITY_BOUNDS)
        for score, value in abilities.items()
    }


def _validate_attacks(attacks: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(attacks, list) or not 1 <= len(attacks) <= 20:
        raise CatalogContractError(f"{label} requires 1 to 20 attacks")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for attack in attacks:
        if not isinstance(attack, dict):
            raise CatalogContractError(f"{label} attack must be an object")
        attack_id = _required_text(attack.get("id"), f"{label} attack id", maximum=64)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", attack_id):
            raise CatalogContractError(f"{label} attack id is invalid: {attack_id!r}")
        if attack_id in seen:
            raise CatalogContractError(f"{label} duplicates attack id: {attack_id!r}")
        seen.add(attack_id)
        damage = _required_text(attack.get("damage"), f"{label}.{attack_id} damage", maximum=64)
        if DAMAGE_DICE_RE.fullmatch(damage) is None:
            raise CatalogContractError(
                f"{label}.{attack_id} damage must match NdM[+K] (combat dice): {damage!r}"
            )
        normalized.append({
            "id": attack_id,
            "name": _required_text(attack.get("name", attack_id), f"{label}.{attack_id} name", maximum=120),
            "damage": damage,
            "attack_bonus": _bounded_int(
                attack.get("attack_bonus", 0), f"{label}.{attack_id} attack_bonus", -20, 30,
            ),
            "range": _bounded_int(attack.get("range", 5), f"{label}.{attack_id} range", 5, 600),
        })
    return normalized


def _monster_mechanics(record: dict[str, Any], label: str) -> dict[str, Any]:
    return {
        "hp": _bounded_int(record.get("hp"), f"{label} hp", 1, 10000),
        "armor_class": _bounded_int(record.get("armor_class"), f"{label} armor_class", 1, 40),
        "speed": _bounded_int(record.get("speed", 30), f"{label} speed", 0, 200),
        "abilities": _validate_abilities(record.get("abilities"), label),
        "attacks": _validate_attacks(record.get("attacks"), label),
    }


def validate_monster_profile(record: Any) -> dict[str, Any]:
    """Validate one module monster profile (mechanics authority: D&D runtime)."""

    if not isinstance(record, dict):
        raise CatalogContractError("monster profile must be an object")
    allowed = {
        "profile_id", "name", "source_ref", "hp", "armor_class", "speed",
        "abilities", "attacks", "description",
    }
    extra = sorted(set(record) - allowed)
    if extra:
        raise CatalogContractError(f"monster profile has unknown field: {extra[0]!r}")
    profile_id = _required_text(record.get("profile_id"), "monster profile_id", maximum=64)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", profile_id):
        raise CatalogContractError(f"monster profile_id is invalid: {profile_id!r}")
    return {
        "profile_id": profile_id,
        "name": _required_text(record.get("name"), "monster name"),
        "source_ref": _optional_source_ref(record.get("source_ref"), "monster source_ref"),
        "description": str(record.get("description") or ""),
        **_monster_mechanics(record, f"monster {profile_id}"),
    }


def validate_npc_statblock(record: Any) -> dict[str, Any]:
    """Validate one module NPC statblock.

    NPC statblock = monster mechanics + 剧情身份字段；它与 world 侧的
    ``npc:<id>`` entity 是两回事（母方案 §187：world 身份 + rules 数据，
    经 Adventure definition 绑定）。
    """

    if not isinstance(record, dict):
        raise CatalogContractError("npc statblock must be an object")
    allowed = {
        "statblock_id", "name", "source_ref", "hp", "armor_class", "speed",
        "abilities", "attacks", "description",
    }
    extra = sorted(set(record) - allowed)
    if extra:
        raise CatalogContractError(f"npc statblock has unknown field: {extra[0]!r}")
    statblock_id = _required_text(record.get("statblock_id"), "statblock_id", maximum=64)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", statblock_id):
        raise CatalogContractError(f"npc statblock_id is invalid: {statblock_id!r}")
    return {
        "statblock_id": statblock_id,
        "name": _required_text(record.get("name"), "npc name"),
        "source_ref": _optional_source_ref(record.get("source_ref"), "npc source_ref"),
        "description": str(record.get("description") or ""),
        **_monster_mechanics(record, f"npc {statblock_id}"),
    }


ITEM_CATEGORIES = ("equipment", "consumable", "treasure", "tool", "key_item")


def validate_item_record(record: Any) -> dict[str, Any]:
    """Validate one module item record.

    v1 只承载身份 / 类别 / 描述；装备与数量的 mechanics 归 character/economy
    侧（母方案 §189：不能同一 item 数量两边 authoritative）。
    """

    if not isinstance(record, dict):
        raise CatalogContractError("item record must be an object")
    allowed = {"item_id", "name", "source_ref", "category", "description"}
    extra = sorted(set(record) - allowed)
    if extra:
        raise CatalogContractError(f"item record has unknown field: {extra[0]!r}")
    item_id = _required_text(record.get("item_id"), "item_id", maximum=64)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", item_id):
        raise CatalogContractError(f"item_id is invalid: {item_id!r}")
    category = record.get("category")
    if category not in ITEM_CATEGORIES:
        raise CatalogContractError(f"item category is invalid: {category!r}")
    return {
        "item_id": item_id,
        "name": _required_text(record.get("name"), "item name"),
        "source_ref": _optional_source_ref(record.get("source_ref"), "item source_ref"),
        "category": category,
        "description": str(record.get("description") or ""),
    }


def validate_encounter_profile(record: Any) -> dict[str, Any]:
    """Validate one module encounter profile.

    敌人以 **ContentRef** 指向 core / module / adventure-local 怪物
    （母方案 §112）；本契约只校验 ref 语法与数量边界，解析在 DNDMOD-02。
    """

    if not isinstance(record, dict):
        raise CatalogContractError("encounter profile must be an object")
    allowed = {"encounter_id", "name", "source_ref", "difficulty", "enemies", "description"}
    extra = sorted(set(record) - allowed)
    if extra:
        raise CatalogContractError(f"encounter profile has unknown field: {extra[0]!r}")
    encounter_id = _required_text(record.get("encounter_id"), "encounter_id", maximum=64)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", encounter_id):
        raise CatalogContractError(f"encounter_id is invalid: {encounter_id!r}")
    difficulty = record.get("difficulty")
    if difficulty not in _ENCOUNTER_DIFFICULTIES:
        raise CatalogContractError(f"encounter difficulty is invalid: {difficulty!r}")
    enemies = record.get("enemies")
    if not isinstance(enemies, list) or not 1 <= len(enemies) <= 20:
        raise CatalogContractError("encounter requires 1 to 20 enemy entries")
    # encounter 内的 v1 裸 ref（kind:id）默认归属 encounter 自己的模块来源
    # （母方案 §9：v1 引用解释为"当前 Adventure/包 本地"）。
    default_ref_source = str(record.get("source_ref") or "") or None
    normalized_enemies: list[dict[str, Any]] = []
    for enemy in enemies:
        if not isinstance(enemy, dict):
            raise CatalogContractError("encounter enemy entry must be an object")
        allowed_enemy = {"ref", "count"}
        extra_enemy = sorted(set(enemy) - allowed_enemy)
        if extra_enemy:
            raise CatalogContractError(
                f"encounter enemy entry has unknown field: {extra_enemy[0]!r}"
            )
        try:
            ref = parse_content_ref(enemy.get("ref"), default_source=default_ref_source)
        except ValueError as exc:
            raise CatalogContractError(f"encounter enemy ref: {exc}") from exc
        normalized_enemies.append({
            "ref": {"source": ref.source, "kind": ref.kind, "id": ref.id},
            "count": _bounded_int(enemy.get("count", 1), "encounter enemy count", 1, 20),
        })
    return {
        "encounter_id": encounter_id,
        "name": _required_text(record.get("name"), "encounter name"),
        "source_ref": _optional_source_ref(record.get("source_ref"), "encounter source_ref"),
        "difficulty": difficulty,
        "description": str(record.get("description") or ""),
        "enemies": normalized_enemies,
    }


__all__ = [
    "ABILITY_SCORES",
    "CATALOG_KINDS",
    "ITEM_CATEGORIES",
    "CatalogContractError",
    "DAMAGE_DICE_RE",
    "validate_encounter_profile",
    "validate_item_record",
    "validate_monster_profile",
    "validate_npc_statblock",
]
