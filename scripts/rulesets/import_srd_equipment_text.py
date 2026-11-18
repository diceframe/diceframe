"""Import SRD 5.2.1 equipment text (CC-BY-4.0) from open5e.

The spell import reads dnd5eapi; equipment cannot.  dnd5eapi's 2024
``/equipment`` entries carry identity, cost, weight and weapon/armour mechanics
but their ``description`` array is **empty for every item** -- there is no prose
to fetch.  open5e's ``items`` endpoint (document ``srd-2024``) is the one that
carries it, so this script reads that instead and keeps the same contract shape
as the spell descriptions: one reference catalog, own id space, own licence
fields, deterministic output.

Deliberately included: every SRD item, magic ones too (wondrous items, rings,
potions, scrolls).  A reader asking "what does this potion do" should not have
to consult two catalogs depending on whether the item is magical.

Deliberately NOT included: mechanics.  Cost and weight are copied because the
source states them and inventory needs them; damage dice, properties, masteries
and utilise-DCs stay out -- they belong to the item entities the ruleset
validates, and duplicating them here would create a second authority.

Usage (the local environment's proxy must be passed explicitly):

    python scripts/rulesets/import_srd_equipment_text.py \
        --out templates/rulesets/dnd2024_srd/content/equipment_catalogs/srd_equipment.json \
        --proxy http://127.0.0.1:7890 --cache E:/tmp/equipment-cache
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# 文档过滤在客户端做：接口的 ``document__key`` 参数在 items 上会被忽略（实测
# 返回 5.1 与 5.2 混排），拿它当过滤器会把 2014 的同名物品一起收进来。
INDEX_URL = "https://api.open5e.com/v2/items/?limit=100"
DOCUMENT_KEY = "srd-2024"


def _normalize(name: str) -> str:
    """``Poisoner's Kit`` -> ``poisoners_kit``: the canonical id convention.

    Parenthesised qualifiers stay in the id ("Arrows (20)" -> arrows_20)
    because the source treats them as separate items, and two items sharing an
    id would silently lose one of them.
    """

    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _open(proxy: str | None):
    if not proxy:
        return urllib.request.build_opener()
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    return urllib.request.build_opener(handler)


def _get(opener, url: str, *, retries: int = 3) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "diceframe-import/1.0"},
            )
            with opener.open(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"failed to fetch {url}: {last}")


def _decimal(value: Any) -> float | None:
    """open5e serialises numbers as fixed-point strings ("50.00", "2.000")."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _cost_gp(cost: Any) -> float | None:
    return _decimal(cost)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--proxy", default="")
    args = parser.parse_args(argv[1:])
    opener = _open(args.proxy or None)

    rows: list[dict[str, Any]] = []
    url = INDEX_URL
    while url:
        payload = _get(opener, url)
        rows.extend(payload.get("results") or [])
        url = payload.get("next")
    rows = [
        row for row in rows
        if str((row.get("document") or {}).get("key") or "") == DOCUMENT_KEY
    ]

    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        item_id = _normalize(name)
        if item_id in items:
            print(f"duplicate id after normalisation: {name} -> {item_id}", file=sys.stderr)
            return 2
        desc = row.get("desc")
        paragraphs = (
            [part.strip() for part in str(desc).split("\n\n") if part.strip()]
            if isinstance(desc, str) else []
        )
        entry: dict[str, Any] = {
            "name": name,
            "category": str((row.get("category") or {}).get("key") or ""),
            "desc": paragraphs,
        }
        cost = _cost_gp(row.get("cost"))
        if cost is not None:
            entry["cost_gp"] = cost
        weight = _decimal(row.get("weight"))
        if weight is not None:
            entry["weight_lb"] = weight
        items[item_id] = entry

    payload = {
        "schema_version": 1,
        "kind": "equipment_text_catalog",
        "id": "srd_equipment",
        "source_ref": "srd-5.2.1:p89-p95:equipment",
        "automation_level": "reference",
        "license": {
            "id": "CC-BY-4.0",
            "attribution": "legal/ATTRIBUTION.md",
            "transcription": "api.open5e.com (document srd-2024)",
        },
        "items": dict(sorted(items.items())),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    empty = [key for key, value in items.items() if not value["desc"]]
    print(f"wrote {len(items)} items to {out} ({len(empty)} without prose)")
    if empty:
        print("without prose: " + ", ".join(empty[:20]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
