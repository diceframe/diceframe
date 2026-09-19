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

战斗入口（combat engine ``encounter_presets()``）也走同一条解析：命中模组
``encounter_profile`` 或引用式 enemy 的预设时，敌人实例就是模块 catalog 里的
怪物 profile —— 不是各写一份的内联 statblock（FIX-03 §5.3）。

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

# combat enemy id 词法：[a-z0-9][a-z0-9_.-]{0,63}（见 combat.validation 的
# validate_enemy_profiles）。上限 64 字符。
_ENEMY_ID_RE_LIMIT = 64


def _enemy_instance_id(profile_id: str, occurrence: int) -> str:
    """Deterministic, unique, combat-valid enemy instance id.

    FIX-03 §5.4：旧实现用 ``_1.._5`` 后缀取模，count > 5 就会产生重复 id
    （combat.validation 直接拒绝），也没有长度上界。现在：

    - 唯一：``occurrence`` 从 1 递增（0 = 该 profile 只出现一次，用裸 id）；
    - 确定性：只由 profile_id 与出现序号决定，重放同一遭遇得到同样的 id；
    - combat-valid：始终匹配 ``[a-z0-9][a-z0-9_.-]{0,63}``（必要时按上界截断
      profile 前缀，保留序号后缀，避免截断造成重复）。
    """

    base = str(profile_id or "").strip()
    if not base:
        raise ContentRefError("encounter enemy profile has no profile_id")
    suffix = "" if occurrence <= 0 else f"_{occurrence}"
    if len(base) + len(suffix) > _ENEMY_ID_RE_LIMIT:
        base = base[: _ENEMY_ID_RE_LIMIT - len(suffix)]
    return f"{base}{suffix}"


def _enemy_instance_from_profile(
    profile: dict[str, Any], index: int,
) -> dict[str, Any]:
    return {
        "id": _enemy_instance_id(str(profile["profile_id"]), index),
        "profile_id": str(profile["profile_id"]),
        "name": str(profile["name"]),
        "hp": int(profile["hp"]),
        "armor_class": int(profile["armor_class"]),
        "speed": int(profile.get("speed", 30)),
        "abilities": dict(profile.get("abilities") or {}),
        "attacks": [dict(attack) for attack in profile.get("attacks") or []],
    }


def expand_encounter_enemies(
    catalog: DndContentCatalog,
    enemies: Any,
    *,
    default_source: str,
) -> list[dict[str, Any]]:
    """Expand declared enemy refs into canonical combat enemy instances.

    ``enemies`` 是 encounter profile 的 ``enemies[]``（``{"ref", "count"}``）。
    解析失败 / 指向非 monster 一律 fail closed（绝不换一个怪物顶上）。

    id 语义（§5.4）：同一个怪物 profile 只出现一次时用裸 profile_id（可读）；
    出现多次（count > 1 或分多条声明）时按出现序号加 ``_N`` 后缀，保证
    count 1..50 全部唯一且确定性。
    """

    entries: list[tuple[Any, int]] = []
    occurrences: dict[str, int] = {}
    profiles: dict[str, Any] = {}
    for entry in enemies or []:
        if not isinstance(entry, dict):
            raise ContentRefError("encounter enemy entry must be an object")
        ref = parse_content_ref(entry.get("ref"), default_source=default_source)
        if ref.kind != "monster":
            raise ContentRefError(
                f"encounter enemy ref must point at a monster: {ref.canonical()}"
            )
        profile = catalog.resolve(ref)
        if profile is None:
            raise ContentRefError(f"encounter enemy ref unresolved: {ref.canonical()}")
        count = int(entry.get("count", 1) or 1)
        if count < 1:
            raise ContentRefError(
                f"encounter enemy count must be positive: {ref.canonical()}"
            )
        profile_id = str(profile.get("profile_id") or "")
        if not profile_id:
            raise ContentRefError(f"encounter enemy profile has no profile_id: {ref.canonical()}")
        profiles[profile_id] = profile
        occurrences[profile_id] = occurrences.get(profile_id, 0) + count
        entries.append((profile_id, count))

    seen: dict[str, int] = {}
    instances: list[dict[str, Any]] = []
    for profile_id, count in entries:
        profile = profiles[profile_id]
        for _ in range(count):
            index = seen.get(profile_id, 0)
            seen[profile_id] = index + 1
            repeated = occurrences[profile_id] > 1
            instances.append(_enemy_instance_from_profile(
                profile, index + 1 if repeated else 0,
            ))
    return instances


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

    return expand_encounter_enemies(
        catalog,
        encounter.get("enemies"),
        default_source=default_source,
    )


__all__ = ["expand_encounter_enemies", "resolve_encounter"]
