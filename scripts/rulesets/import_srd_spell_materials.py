"""把 SRD 5.2.1 法术的"材料是否计费 / 是否被消耗"补进本地目录。

上游客服端有 `material_specified` / `material_consumed` 两个布尔，而本地目录
只存了 `components: ["V","S","M"]`——光看组件字母无法区分"一小撮沙"和"价值
300gp 的钻石"。18 级 Beast Spells 的例外恰好以这两件事为界，所以缺了它们，
规则只能要么全放（错）要么全禁（更错）。

用法：`python scripts/rulesets/import_srd_spell_materials.py`
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

API = "https://api.open5e.com/v2/spells/?document__key=srd-2024&limit=100"
CATALOG = Path(
    "templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json"
)


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
    upstream = fetch()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    spells = catalog["spells"]
    matched = 0
    for spell in spells:
        spell_id = str(spell["id"])
        # 本地 id 用下划线（cure_wounds），上游 key 用连字符（cure-wounds）。
        row = upstream.get(spell_id) or upstream.get(spell_id.replace("_", "-"))
        if row is None:
            continue
        matched += 1
        # 上游 `material_specified` 是**材料描述文本**（不是布尔），价格写在
        # 文本里（"a diamond worth 300+ GP"）。结构化的 `material_cost` 目前
        # 全是 None，所以只能从文本里认货币标记。
        text = str(row.get("material_specified") or "").lower()
        specified = any(
            marker in text for marker in ("worth", " gp", " pp", " sp", " cp")
        )
        spell["material_cost_specified"] = specified
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
