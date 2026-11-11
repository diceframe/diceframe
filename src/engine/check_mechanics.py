"""Check-mechanic registry (registry seam for dice systems beyond d20 / d100).

通用检定管线内建 d20 与 d100 两种骰制。其它骰制（例如「两枚属性骰相加」）的
mechanics 属于声明它的 ruleset，不属于 generic engine：

```text
规则模板 dice_system = "<id>"
    → 通用管线按 id 查表（本模块）
    → 具体 ruleset 包反向注册的 CheckMechanic 实现
```

边界（与 ``src/rulesets/module_catalogs.py`` 同一模式）：

- 本模块**中性**：只存 ``dice_system -> mechanic``，不 import 任何具体 ruleset；
- 具体实现反向注册（依赖方向：ruleset → engine），在组合根导入规则包时完成；
- 未注册的骰制 = 当前进程不支持，通用层 fail closed（规则模板加载即报错），
  不替规则猜测 mechanics；
- 内建的 d20 / d100 **不**经过本表，它们的语义不因任何注册实现而改变。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: 通用管线内建实现的骰制；注册实现不能覆盖它们。
BUILTIN_DICE_SYSTEMS = frozenset({"d20", "d100", "none"})


@dataclass(slots=True)
class CheckResolution:
    """One rolled check request, prepared by the generic pipeline for settlement.

    通用层已完成授权、主体解析（玩家 / 队友角色卡）、对手解析与公共结果字段；
    mechanic 只负责自己的规则数学，并把结果写回 ``common``。
    """

    instance: Any
    rule: Any
    uid: str
    action: dict[str, Any]
    request: dict[str, Any]
    character_sheet: dict[str, Any]
    attributes: dict[str, Any]
    actor_is_companion: bool
    roll_value: int
    rolls: list[int]
    #: 已按通用上下限裁剪的情境修正。
    circumstance_modifier: int
    opponent_ref: str
    opponent_name: str
    opponent_state: dict[str, Any]
    common: dict[str, Any]
    #: canonical attribute key -> 当前规则的显示名。
    attribute_label: Callable[[str], str]


class CheckMechanic:
    """Base class for a registered dice system; defaults are inert.

    子类至少实现 ``roll`` 与 ``resolve``。其余方法是通用管线的可选扩展点，
    默认不改变任何通用行为。
    """

    #: 规则模板 ``dice_system`` 的取值，也是 CheckRequest / 结果里的骰制标识。
    dice_system: str = ""
    #: 规划器的 ``target`` 是否按 DC 语义处理（与 d20 共用渠道归一化与硬上限）。
    target_is_dc: bool = True

    def descriptor(self) -> dict[str, Any]:
        """规则模板未声明 ``check_mechanic`` 时的默认元数据。"""

        return {"dice": self.dice_system, "comparison": "custom", "critical": {}}

    def request_fields(
        self,
        *,
        rule: Any,
        sheet_attributes: dict[str, Any],
        attribute: str,
        action: dict[str, Any],
        planned: dict[str, Any] | None = None,
        is_attack: bool = False,
        resolve_attribute: Callable[[object], str] | None = None,
    ) -> dict[str, Any]:
        """建请求阶段固化的 mechanic 专属字段（例如骰面），随请求落库。"""

        return {}

    def roll(self, request: dict[str, Any], rule: Any) -> dict[str, Any]:
        """掷骰，返回 ``{"value", "rolls", "critical", "fumble", ...}``。"""

        raise NotImplementedError

    def validate_roll(
        self,
        request: dict[str, Any],
        rule: Any,
        rolls: list[int],
        roll_value: int,
    ) -> None:
        """校验客户端回传的骰值与请求一致；不一致时抛 ``ValueError``。"""

    def resolve(self, resolution: CheckResolution) -> dict[str, Any]:
        """结算并返回完整的结构化判定结果（通常是更新后的 ``common``）。"""

        raise NotImplementedError

    def result_prompt(self, instance: Any, check: dict[str, Any]) -> str:
        """给叙事 GM 的结果约束文本；返回空串表示沿用通用格式。"""

        return ""

    def planner_actor_context(self, sheet: dict[str, Any]) -> dict[str, Any]:
        """附加到裁判上下文里每个行动者条目的字段。"""

        return {}

    def planner_tool_properties(self) -> dict[str, Any]:
        """附加到检定规划工具每条 check 的 JSON Schema 属性。"""

        return {}

    def rule_template_error(self, template: dict[str, Any]) -> str:
        """保存自定义规则模板时的额外校验；返回空串表示通过。"""

        return ""

    def manual_roll_refusal(self, language: object) -> dict[str, Any] | None:
        """玩家手动单骰入口不适用时返回拒绝载荷（按 ``language`` 渲染）；``None`` 表示不拦截。"""

        return None


_MECHANICS: dict[str, CheckMechanic] = {}


def register_check_mechanic(mechanic: CheckMechanic) -> None:
    """Register one dice system's mechanic (composition time; replaces by id)."""

    dice_system = str(getattr(mechanic, "dice_system", "") or "").strip().lower()
    if not dice_system:
        raise ValueError("check mechanic requires a dice_system id")
    if dice_system in BUILTIN_DICE_SYSTEMS:
        raise ValueError(f"built-in dice system cannot be replaced: {dice_system}")
    _MECHANICS[dice_system] = mechanic


def check_mechanic_for(dice_system: object) -> CheckMechanic | None:
    """The registered mechanic for a dice system id, or ``None`` = built-in/unsupported."""

    return _MECHANICS.get(str(dice_system or "").strip().lower())


def registered_dice_systems() -> frozenset[str]:
    return frozenset(_MECHANICS)


def supported_dice_systems() -> frozenset[str]:
    """Built-in dice systems plus every registered mechanic."""

    return BUILTIN_DICE_SYSTEMS | registered_dice_systems()


def request_dice_system(dice_system: object) -> str:
    """The dice-system id stamped on a CheckRequest for a rule's dice system."""

    normalized = str(dice_system or "").strip().lower()
    if normalized == "d100" or normalized in _MECHANICS:
        return normalized
    return "d20"


__all__ = [
    "BUILTIN_DICE_SYSTEMS",
    "CheckMechanic",
    "CheckResolution",
    "check_mechanic_for",
    "register_check_mechanic",
    "registered_dice_systems",
    "request_dice_system",
    "supported_dice_systems",
]
