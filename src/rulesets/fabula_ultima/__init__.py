"""Fabula Ultima 规则运行时包。

对通用层的全部接入点：

- ``FabulaUltimaRuntime``：注册进 ``RulesetRuntimeRegistry``（组合根 ``src/rulesets/builtin.py``）；
- ``check_mechanic``：导入本包时反向注册 ``dice_system = "fu"`` 的叙事检定机制。
"""

from src.rulesets.fabula_ultima import check_mechanic as _check_mechanic
from src.rulesets.fabula_ultima.runtime import FabulaUltimaRuntime

_check_mechanic.register()

__all__ = ["FabulaUltimaRuntime"]
