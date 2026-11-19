"""Import the SRD 5.2.1 *Tools* list (CC-BY-4.0).

Tools are their own table in the equipment chapter: artisan's tools, kits,
musical instruments and gaming sets.  They are not Adventuring Gear and not
weapons, and the distinction matters to the engine -- a tool carries an
``ability`` and a ``utilize`` list (a check and its DC), which is exactly what
a poison, a lock or a disguise check needs to consult.

Single source: dnd5eapi's 2024 ``tools`` category.  Its records carry the prose,
the price, the weight, the associated ability, the utilize entries (action +
DC + success type), the craft entries and -- for kits -- the contents.

Usage (pass the environment's proxy explicitly on this machine):

    python scripts/rulesets/import_srd_tools.py \
        --out templates/rulesets/dnd2024_srd/content/equipment_catalogs/srd_tools.json \
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

CATEGORY_URL = "https://www.dnd5eapi.co/api/2024/equipment-categories/tools"
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

    items: dict[str, dict[str, Any]] = {}
    no_prose: list[str] = []
    for row in _get(opener, CATEGORY_URL).get("equipment") or []:
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
        if isinstance(cost.get("quantity"), (int, float)) and str(
            cost.get("unit") or "gp"
        ) == "gp":
            entry["cost_gp"] = float(cost["quantity"])
        weight = detail.get("weight")
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            entry["weight_lb"] = float(weight)
        ability = detail.get("ability") or {}
        if isinstance(ability.get("name"), str) and ability["name"]:
            entry["ability"] = ability["name"].lower()
        utilize = detail.get("utilize")
        if isinstance(utilize, list) and utilize:
            # Utilize 条目就是"用这套工具做什么、查什么、DC 多少"。
            entry["utilize"] = [
                {
                    "task": str(row.get("name") or ""),
                    "dc": int(((row.get("dc") or {}).get("dc_value")) or 0),
                    "success": str((row.get("dc") or {}).get("success_type") or ""),
                }
                for row in utilize
                if isinstance(row, dict)
            ]
        craft = detail.get("craft")
        if isinstance(craft, list) and craft:
            entry["craft"] = [
                _normalize(str(row.get("name") or ""))
                for row in craft
                if isinstance(row, dict) and row.get("name")
            ]
        contents = detail.get("contents")
        if isinstance(contents, list) and contents:
            entry["contents"] = [
                {
                    "item": _normalize(str((row.get("item") or {}).get("name") or "")),
                    "name": str((row.get("item") or {}).get("name") or ""),
                    "quantity": int(row.get("quantity", 1) or 1),
                }
                for row in contents
                if isinstance(row, dict) and row.get("item")
            ]
        items[item_id] = entry

    payload = {
        "schema_version": 1,
        "kind": "equipment_text_catalog",
        "id": "srd_tools",
        "source_ref": "srd-5.2.1:p93-p95:tools",
        "automation_level": "reference",
        "scope": "tools",
        "license": {
            "id": "CC-BY-4.0",
            "attribution": "legal/ATTRIBUTION.md",
            "transcription": "dnd5eapi.co (SRD 5.2.1, tools category)",
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
    print(f"wrote {len(items)} tools to {out}")
    if no_prose:
        print(f"no description in source ({len(no_prose)}): " + ", ".join(no_prose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
