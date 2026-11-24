"""把 SRD 5.2.1 法术的"材料是否计费 / 是否被消耗"补进本地目录。

上游有 `material_specified`（材料描述文本）/ `material_consumed`（布尔），而
本地目录只存了 `components: ["V","S","M"]`——光看组件字母无法区分"一小撮沙"和
"价值 300gp 的钻石"。18 级 Beast Spells 的例外恰好以这两件事为界，所以缺了
它们，规则只能要么全放（错）要么全禁（更错）。

用法：`python scripts/rulesets/import_srd_spell_materials.py`
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

API = "https://api.open5e.com/v2/spells/?document__key=srd-2024&limit=100"
CATALOG = Path(
    "templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json"
)

# 有价格只认两种写法：单词 worth，或者带词边界的金额（含拼写出来的硬币名）。
# 不能用 " sp" 这类子串——它会命中 sprig、spring、sphere 和 "the spell"，把
# 免费材料判成计费（Goodberry、Levitate、Web、Resilient Sphere ……）。
_COIN_AMOUNT_RE = re.compile(
    r"\b\d[\d,+]*\s*(?:gp|sp|ep|cp|pp)\b"
    r"|\b\d+\s*(?:copper|silver|electrum|gold|platinum)\s+pieces?\b"
)
_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


def has_material_cost(text: object) -> bool:
    """Whether a material description names a price ("a diamond worth 300+ GP")."""

    lowered = str(text or "").lower()
    return "worth" in lowered or bool(_COIN_AMOUNT_RE.search(lowered))


def _row_key(value: object) -> str:
    """An id without separators, so both sides compare the same way.

    The local catalog writes ids with underscores (``cure_wounds``) while the
    upstream key drops or joins them differently (``cure-wounds``,
    ``enlargereduce``, ``arcanists-magic-aura``).
    """

    return _SEPARATOR_RE.sub("", str(value or "").lower())


def fetch() -> dict[str, dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.addheaders = [("User-Agent", "diceframe-spell-materials/1.0")]
    rows: dict[str, dict] = {}
    url: str | None = API
    while url:
        with opener.open(url, timeout=90) as response:
            payload = json.loads(response.read().decode())
        for row in payload["results"]:
            # 上游 key 带文档前缀（srd-2024_revivify），本地 id 是裸 id。
            key = str(row["key"]).removeprefix("srd-2024_")
            rows[key] = row
        url = payload.get("next")
    return rows


def main() -> None:
    upstream = {
        _row_key(key): row for key, row in fetch().items()
    }
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    spells = catalog["spells"]
    matched = 0
    for spell in spells:
        row = upstream.get(_row_key(spell["id"]))
        if row is None:
            # 没对上就不写这两个布尔：运行时对"有材料但未分类"按 fail closed 处理。
            continue
        matched += 1
        # 上游 `material_specified` 是材料描述文本，价格写在文本里
        # （"a diamond worth 300+ GP"）。结构化的 `material_cost` 目前全是 None，
        # 所以只能从文本里认价格。
        spell["material_cost_specified"] = has_material_cost(row.get("material_specified"))
        spell["material_consumed"] = bool(row.get("material_consumed"))
    CATALOG.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    costly = sum(1 for s in spells if s.get("material_cost_specified"))
    consumed = sum(1 for s in spells if s.get("material_consumed"))
    print(
        f"{len(spells)} spells, {matched} matched upstream, "
        f"{costly} with a specified material cost, {consumed} consuming it"
    )


if __name__ == "__main__":
    main()
