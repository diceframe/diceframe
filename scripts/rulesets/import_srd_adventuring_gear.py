"""Import the SRD 5.2.1 *Adventuring Gear* list (CC-BY-4.0).

Scope is the rulebook's own Adventuring Gear subsection -- not "equipment".
Adventuring Gear is a narrow, explicit list (acid, ball bearings, chain, rope,
torch, ...); weapons, armour, tools, mounts, vehicles and trade goods each have
their own table and are out of scope.

One source, start to finish: dnd5eapi's 2024 category is the membership list,
and its per-item records carry the prose, the price, the weight and -- for the
five packs -- a structured contents list with quantities.  Nothing here needs a
second catalog, which matters because category taxonomies differ between
sources: open5e files Acid under ``weapon`` and the foci under
``spellcasting-focus``, so a cross-source join would silently change what "the
list" means.

Seven entries have no description in the source at all (crystal, orb, rod,
staff, wand, yew wand, sprig of mistletoe -- the focus components).  They are
kept with an empty ``desc``: the item exists in the list, and inventing a
sentence for it would be worse than admitting the gap.

Usage (pass the environment's proxy explicitly on this machine):

    python scripts/rulesets/import_srd_adventuring_gear.py \
        --out templates/rulesets/dnd2024_srd/content/equipment_catalogs/srd_adventuring_gear.json \
        --proxy http://127.0.0.1:7890
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

CATEGORY_URL = "https://www.dnd5eapi.co/api/2024/equipment-categories/adventuring-gear"
DETAIL_ROOT = "https://www.dnd5eapi.co"


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _open(proxy: str | None):
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )


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


def _paragraphs(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [part.strip() for part in raw.split("\n\n") if part.strip()]
    if isinstance(raw, list):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--proxy", default="")
    args = parser.parse_args(argv[1:])
    opener = _open(args.proxy or None)

    category = _get(opener, CATEGORY_URL)
    items: dict[str, dict[str, Any]] = {}
    no_prose: list[str] = []
    for row in category.get("equipment") or []:
        name = str(row["name"])
        detail = _get(opener, DETAIL_ROOT + str(row["url"]))
        item_id = _normalize(name)
        if item_id in items:
            print(f"duplicate id after normalisation: {name}", file=sys.stderr)
            return 2
        entry: dict[str, Any] = {
            "name": name,
            "desc": _paragraphs(detail.get("description")),
        }
        if not entry["desc"]:
            no_prose.append(name)
        cost = detail.get("cost") or {}
        if isinstance(cost.get("quantity"), (int, float)):
            unit = str(cost.get("unit") or "gp")
            if unit == "gp":
                entry["cost_gp"] = float(cost["quantity"])
        weight = detail.get("weight")
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            entry["weight_lb"] = float(weight)
        contents = detail.get("contents")
        if isinstance(contents, list) and contents:
            # 包是可拆的：买一个 Pack 等于拿到清单里这些物品。
            entry["contents"] = [
                {
                    "item": _normalize(str((item.get("item") or {}).get("name") or "")),
                    "name": str((item.get("item") or {}).get("name") or ""),
                    "quantity": int(item.get("quantity", 1) or 1),
                }
                for item in contents
                if isinstance(item, dict) and item.get("item")
            ]
        container = detail.get("container")
        if isinstance(container, list) and container:
            # 形状与 ``contents`` 不同：这里直接就是物品引用，没有 ``item`` 包一层。
            first = container[0] if isinstance(container[0], dict) else {}
            name_ref = str((first.get("item") or first).get("name") or "")
            if name_ref:
                entry["container"] = _normalize(name_ref)
        items[item_id] = entry

    payload = {
        "schema_version": 1,
        "kind": "equipment_text_catalog",
        "id": "srd_adventuring_gear",
        "source_ref": "srd-5.2.1:p89-p95:adventuring-gear",
        "automation_level": "reference",
        "scope": "adventuring-gear",
        "license": {
            "id": "CC-BY-4.0",
            "attribution": "legal/ATTRIBUTION.md",
            "transcription": "dnd5eapi.co (SRD 5.2.1, adventuring-gear category)",
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
    print(f"wrote {len(items)} adventuring-gear items to {out}")
    if no_prose:
        print(f"no description in source ({len(no_prose)}): " + ", ".join(no_prose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
