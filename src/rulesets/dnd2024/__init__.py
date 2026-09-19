"""First-party 5E 2024 SRD professional ruleset runtime."""

from src.rulesets.dnd2024.runtime import Dnd2024Runtime

# DNDMOD-04：把本 runtime 的 declared-catalog 装载契约注册到 generic registry
# seam（``src.rulesets.module_catalogs``）。注册点放在 runtime 包入口——只要这个
# runtime 可用，generic 安装/预览校验就一定能问到它的 catalog 契约，不依赖
# "恰好先同步过一次 catalog"。
from src.rulesets.dnd2024.content.catalog import (  # noqa: E402
    load_catalog_dir as _load_catalog_dir,
)
from src.rulesets.module_catalogs import (  # noqa: E402
    register_module_catalog_loader as _register_module_catalog_loader,
)

_register_module_catalog_loader("core:dnd2024", _load_catalog_dir)

__all__ = ["Dnd2024Runtime"]
