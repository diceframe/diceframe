"""Runtime-specific adventure content validator registry (MOD-01, 母方案 §185/§186).

generic Adventure Loader 只校验**结构**（graph / ids / refs / locale / 包安全）；
runtime 专属 mechanics（例如 D&D encounter 的 hp / armor_class / attacks）由
目标 ruleset runtime 注册的 validator 校验：

```text
AdventureBundleLoader
    → detect required_runtime.id
    → runtime_validation.adventure_validator_for(runtime_id)
    → Dnd2024AdventureValidator(entities)   # core:dnd2024 注册
```

边界：

- 本注册表是**中性**的：不 import 任何具体 ruleset；具体实现反向注册自己
  （依赖方向：dnd2024 → runtime_validation，generic loader 只依赖本模块）。
- 未注册（runtime 未加载 / 第三方 runtime）：generic 结构仍可读，
  mechanics compatibility = unresolved（母方案 §185），由安装/运行侧按
  上下文决定是否阻断——loader 不猜。
- 注册即全量替换（同一 runtime_id 重复注册以后者为准），无事件、无回调，
  也不是 EventBus——只是启动期的静态能力注册。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

# validator: (entities: {kind: {id: entity}}) -> None；机制不合法时抛 ValueError。
AdventureContentValidator = Callable[[Mapping[str, Mapping[str, dict[str, Any]]]], None]

_VALIDATORS: dict[str, AdventureContentValidator] = {}


def register_adventure_validator(runtime_id: str, validator: AdventureContentValidator) -> None:
    """Register the mechanics validator for one runtime id (composition time)."""

    normalized = str(runtime_id or "").strip()
    if not normalized:
        raise ValueError("adventure validator requires a runtime id")
    _VALIDATORS[normalized] = validator


def adventure_validator_for(runtime_id: str) -> AdventureContentValidator | None:
    """The registered validator for a runtime id, or ``None`` = unresolved."""

    return _VALIDATORS.get(str(runtime_id or "").strip())


__all__ = [
    "AdventureContentValidator",
    "adventure_validator_for",
    "register_adventure_validator",
]
