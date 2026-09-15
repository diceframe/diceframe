"""规则货币结构的数据模型：CurrencyUnit / CurrencySpec。

CurrencySpec 是规则货币结构的唯一权威定义；economy 引擎只处理
canonical base-unit 整数，货币名称/单位换算全部经由本模块与 codec。
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class CurrencyUnit:
    """一个货币单位；rate 是「1 个该单位 = 多少个 base unit」。"""

    id: str
    name: str
    rate: int
    symbol: str = ""


@dataclass(frozen=True)
class CurrencySpec:
    """某一规则的完整货币结构。

    - ``base_unit`` 是 economy 引擎 canonical 整数的计数单位，rate 恒为 1；
    - ``display_unit`` 是默认展示单位（可以等于 base_unit）；
    - ``schema_version`` 为 2 表示显式声明的 V2 货币系统，1 表示 legacy 兼容 spec。
    """

    schema_version: int
    base_unit: str
    display_unit: str
    units: tuple[CurrencyUnit, ...]

    def unit(self, unit_id: str) -> CurrencyUnit | None:
        key = str(unit_id or "")
        return next((u for u in self.units if u.id == key), None)

    @property
    def base(self) -> CurrencyUnit:
        return self.units_by_id()[self.base_unit]

    @property
    def display(self) -> CurrencyUnit:
        return self.units_by_id()[self.display_unit]

    def units_by_id(self) -> dict[str, CurrencyUnit]:
        return {unit.id: unit for unit in self.units}

    def to_dict(self) -> dict:
        """JSON 契约投影（前端 / API 使用；amount 仍为 canonical 整数）。"""

        return {
            "schema_version": self.schema_version,
            "base_unit": self.base_unit,
            "display_unit": self.display_unit,
            "units": [
                {
                    "id": unit.id,
                    "name": unit.name,
                    "rate": unit.rate,
                    **({"symbol": unit.symbol} if unit.symbol else {}),
                }
                for unit in self.units
            ],
        }
