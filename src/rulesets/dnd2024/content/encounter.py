"""Encounter → canonical combat profile resolution (DNDMOD-02, 母方案 §112).

Encounter profile 里的敌人是 **ContentRef**（core / module / adventure-local
怪物）；本模块把引用解析为 combat engine 可消费的 canonical enemy 实例：

```text
encounter_profile.enemies[].ref
    → DndContentCatalog（source-aware）
    → validated monster profile
    → 按 count 展开（唯一 id）
    → combat enemy 实例（权威 combat.validation 再做最终校验）
```

边界：

- **战斗 mechanics authority 仍是 combat engine**：本模块只做"引用 → 数据"
  的解析，产出的实例还要过 combat.start 的权威校验；这里不掷骰、不扣血、
  不改状态。
- 解析失败（引用缺失/指向非 monster）fail closed，绝不静默用别的怪物顶上。
- 怪物 profile 本身来自 DNDMOD-01 目录（装载期已过契约校验），解析不做
  第二次语义校验，只做 id 形态与展开。
"""

from __future__ import annotations

from typing import Any

from src.content_modules.refs import ContentRefError, parse_content_ref
from src.rulesets.dnd2024.content.catalog import DndContentCatalog

# combat enemy id 词法：[a-z0-9][a-z0-9_.-]{0,63}（见 combat.validation）。
_ENEMY_ID_SUFFIXES = ("_1", "_2", "_3", "_4", "_5")


def _enemy_instance_id(profile_id: str, index: int, total: int) -> str:
    if total <= 1:
        return profile_id
    # combat id 词法不允许 '#'：多只同类敌人用 _N 后缀保证唯一且可读。
    return f"{profile_id}{_ENEMY_ID_SUFFIXES[index % len(_ENEMY_ID_SUFFIXES)]}"


def resolve_encounter(
    catalog: DndContentCatalog,
    encounter: dict[str, Any],
    *,
    default_source: str,
) -> list[dict[str, Any]]:
    """Resolve one validated encounter profile into combat enemy instances.

    ``encounter`` 必须已过 :func:`validate_encounter_profile`（调用方在装载
    时完成）。解析引用失败时抛 :class:`ContentRefError`。
    """

    instances: list[dict[str, Any]] = []
    for entry in encounter.get("enemies") or []:
        ref = parse_content_ref(entry.get("ref"), default_source=default_source)
        if ref.kind != "monster":
            raise ContentRefError(
                f"encounter enemy ref must point at a monster: {ref.canonical()}"
            )
        profile = catalog.resolve(ref)
        if profile is None:
            raise ContentRefError(f"encounter enemy ref unresolved: {ref.canonical()}")
        count = int(entry.get("count", 1) or 1)
        total = count
        for index in range(count):
            instances.append({
                "id": _enemy_instance_id(str(profile["profile_id"]), index, total),
                "profile_id": str(profile["profile_id"]),
                "name": str(profile["name"]),
                "hp": int(profile["hp"]),
                "armor_class": int(profile["armor_class"]),
                "speed": int(profile.get("speed", 30)),
                "abilities": dict(profile.get("abilities") or {}),
                "attacks": [dict(attack) for attack in profile.get("attacks") or []],
            })
    return instances


__all__ = ["resolve_encounter"]
