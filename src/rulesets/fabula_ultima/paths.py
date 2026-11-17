"""FU 数据文件的位置：随版本发布的默认表 + 用户可编辑的覆盖表。

```text
templates/rules/fabula_ultima.json          规则模板（启动时同步到运行目录）
templates/rulesets/fabula_ultima/*.json      怪物 / 物品 / 技能默认表（只读）
<data>/templates/fabula_ultima/*.json        用户在 FU 数据页编辑后的覆盖表
```

系统默认表不覆盖用户数据：用户表存在时只读用户表，编辑也只写用户表。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BUNDLED_DIR = ROOT / "templates" / "rulesets" / "fabula_ultima"
BUNDLED_RULES_DIR = ROOT / "templates" / "rules"


def data_dir() -> Path:
    return Path(os.environ.get("TRPG_DATA_DIR") or ROOT / "data")


def user_dir() -> Path:
    return data_dir() / "templates" / "fabula_ultima"


def adventures_dir() -> Path:
    return data_dir() / "templates" / "adventures"


def rule_path(rule_id: str) -> Path | None:
    """运行目录里的规则模板优先（含用户自定义副本），否则退回随版本发布的模板。"""

    for directory in (data_dir() / "templates" / "rules", BUNDLED_RULES_DIR):
        path = directory / f"{rule_id}.json"
        if path.exists():
            return path
    return None


def user_table_path(name: str) -> Path:
    return user_dir() / f"{name}.json"


def table_path(name: str) -> Path:
    """用户覆盖表存在时用它，否则用随版本发布的默认表。"""

    user_path = user_table_path(name)
    return user_path if user_path.exists() else BUNDLED_DIR / f"{name}.json"
