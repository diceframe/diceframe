"""D&D 2024 adventure content validation (MOD-01, 母方案 §8/§185/§186).

D&D encounter 的 mechanics 校验从 generic Adventure loader 迁入本模块：
hp / armor_class / attacks / damage / attack_bonus 的存在性与数值边界由
**D&D runtime** 拥有，generic loader 保持规则无关。

本模块在导入时把 validator 注册到 ``src.adventures.runtime_validation``
（runtime id = ``core:dnd2024``）；生产组合（common_factory → ruleset
builtin registry）必然导入 dnd2024 runtime，注册随组合发生。错误类型沿用
``AdventureBundleError``，保证 v1 行为兼容（既有测试与调用方的异常契约不变）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.adventures.bundle import AdventureBundleError
from src.adventures.runtime_validation import register_adventure_validator

_ENCOUNTER_ENEMY_HP_BOUNDS = (1, 100000)
_ENCOUNTER_ENEMY_AC_BOUNDS = (1, 40)
_ENCOUNTER_ATTACK_BONUS_BOUNDS = (-20, 20)


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AdventureBundleError(f"{label} is required")
    return text


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdventureBundleError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise AdventureBundleError(f"{label} must be between {minimum} and {maximum}")
    return value


def validate_dnd2024_adventure_content(
    entities: Mapping[str, Mapping[str, dict[str, Any]]],
) -> None:
    """Validate D&D mechanics inside adventure encounter catalogs."""

    for catalog in entities.get("encounter_catalog", {}).values():
        presets = catalog.get("presets") if isinstance(catalog, Mapping) else None
        for preset in presets or []:
            if not isinstance(preset, Mapping):
                continue
            preset_id = str(preset.get("id") or "")
            for enemy in preset.get("enemies") or []:
                if not isinstance(enemy, Mapping):
                    continue
                _bounded_int(
                    enemy.get("hp", 0), "encounter enemy hp",
                    *_ENCOUNTER_ENEMY_HP_BOUNDS,
                )
                _bounded_int(
                    enemy.get("armor_class", 0), "encounter enemy armor_class",
                    *_ENCOUNTER_ENEMY_AC_BOUNDS,
                )
                attacks = enemy.get("attacks")
                if not isinstance(attacks, list) or not attacks:
                    raise AdventureBundleError(
                        f"encounter enemy must contain attacks: {preset_id}"
                    )
                for attack in attacks:
                    if not isinstance(attack, Mapping):
                        raise AdventureBundleError(
                            f"encounter attack must be an object: {preset_id}"
                        )
                    _required_text(attack.get("id"), "encounter attack id")
                    _required_text(attack.get("damage"), "encounter attack damage")
                    _bounded_int(
                        attack.get("attack_bonus", 0), "encounter attack bonus",
                        *_ENCOUNTER_ATTACK_BONUS_BOUNDS,
                    )


register_adventure_validator("core:dnd2024", validate_dnd2024_adventure_content)

__all__ = ["validate_dnd2024_adventure_content"]
