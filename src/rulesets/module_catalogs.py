"""Declared module-catalog loader registry (registry seam, MOD/DNDMOD boundary).

content-pack 模组可以在 manifest 里声明 ``ruleset_catalogs``（模块自带的
content catalog 目录，例如 ``packs/dnd2024``）。**装载这些目录的契约属于目标
ruleset runtime**（record kind / 字段 / mechanics 边界都是 runtime 自己的契约），
所以 generic 层不能 import 任何具体 ruleset：

```text
安装/预览校验（generic）
    → module_catalogs.load_declared_module_catalog(runtime_id, ...)
    → dnd2024 注册的 load_catalog_dir(...)      # 具体实现反向注册自己
```

边界（与 ``src/adventures/runtime_validation.py`` 同一模式）：

- 本模块**中性**：只存 ``runtime_id -> loader``，不 import 具体 ruleset；
- 具体实现反向注册（依赖方向：dnd2024 → module_catalogs）；
- 未注册的 runtime 表示"该 runtime 不支持声明式 catalog"，调用方按上下文
  决定是否阻断（安装校验 fail closed，见
  ``src/webui/services/module_validation.py``）；
- 注册即全量替换，无事件、无回调 —— 只是启动期的静态能力注册。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


class ModuleCatalogError(ValueError):
    """A declared module catalog cannot be loaded for the target runtime."""


# loader: (directory: Path, *, source_label: str) -> loaded source object
ModuleCatalogLoader = Callable[..., Any]

_LOADERS: dict[str, ModuleCatalogLoader] = {}


def register_module_catalog_loader(
    runtime_id: str, loader: ModuleCatalogLoader,
) -> None:
    """Register the declared-catalog loader for one runtime id (composition time)."""

    normalized = str(runtime_id or "").strip()
    if not normalized:
        raise ValueError("module catalog loader requires a runtime id")
    _LOADERS[normalized] = loader


def module_catalog_loader_for(runtime_id: str) -> ModuleCatalogLoader | None:
    """The registered loader for a runtime id, or ``None`` = unsupported."""

    return _LOADERS.get(str(runtime_id or "").strip())


def load_declared_module_catalog(
    runtime_id: str,
    directory: Path,
    *,
    source_label: str,
) -> Any:
    """Load one declared catalog directory through the runtime that owns it.

    Raises :class:`ModuleCatalogError` when the runtime registers no loader —
    the generic layer never guesses a catalog contract on the runtime's behalf.
    """

    loader = module_catalog_loader_for(runtime_id)
    if loader is None:
        raise ModuleCatalogError(
            f"runtime {str(runtime_id or '')!r} cannot load declared ruleset catalogs"
        )
    return loader(Path(directory), source_label=source_label)


__all__ = [
    "ModuleCatalogError",
    "ModuleCatalogLoader",
    "load_declared_module_catalog",
    "module_catalog_loader_for",
    "register_module_catalog_loader",
]
