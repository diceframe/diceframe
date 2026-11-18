"""Import SRD 5.2.1 spell descriptions from dnd5eapi (CC-BY-4.0).

Why a separate catalog instead of a field on ``srd_spells.json``: the spell
catalog is the canonical identity list the engine validates, and it changes
only when the rules change.  Description text is reference material with its
own provenance and its own license obligations, so it lives next to the
catalog instead of inside it.  A reader that only needs mechanics never has
to load prose, and a prose update can never accidentally alter a spell record.

Usage (needs network; the local proxy is passed explicitly because the shell
environment's proxy variable points at a dead port on this machine):

    python scripts/rulesets/import_srd_spell_descriptions.py \
        --out templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spell_descriptions.json \
        --proxy http://127.0.0.1:7890

The output is deterministic (sorted by spell id, stable key order) so a re-run
that changes nothing produces an empty diff.
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

API_ROOT = "https://www.dnd5eapi.co/api/2024/spells"
SPELL_CATALOG = Path(
    "templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json"
)


def _normalize(name: str) -> str:
    """``Hunter's Mark`` -> ``hunter_s_mark``: the canonical id convention."""

    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _open(proxy: str | None):
    if not proxy:
        return urllib.request.build_opener()
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    return urllib.request.build_opener(handler)


def _paragraphs(detail: dict[str, Any], *, key: str = "desc") -> list[str]:
    """Both API generations, one shape.

    The 2024 endpoint returns ``description`` as one string; the 2014 one
    returns ``desc`` as a list of paragraphs.  Splitting on blank lines keeps
    the paragraph structure the interface expects without inventing breaks
    inside a sentence.
    """

    if key == "desc" and "description" in detail:
        raw = detail.get("description")
    else:
        raw = detail.get(key)
    if isinstance(raw, str):
        return [part.strip() for part in raw.split("\n\n") if part.strip()]
    if isinstance(raw, list):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


def _get(opener, url: str, *, cache_dir: Path | None = None,
         retries: int = 3) -> Any:
    """Fetch with retries and an on-disk cache.

    339 sequential requests over a local proxy hit the occasional SSL handshake
    timeout; without a cache a single failure means starting over.  The cache
    is scratch (``.imports/``), never part of the committed output.
    """

    key = re.sub(r"[^a-z0-9]+", "_", url.rsplit("/", 1)[-1].lower())
    cached = (cache_dir / f"{key}.json") if cache_dir else None
    if cached is not None and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with opener.open(url, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps(payload), encoding="utf-8")
            return payload
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"failed to fetch {url}: {last}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--proxy", default="")
    # 缓存目录由调用方给：脚本里不写死机器盘符（工程规则禁止把本机路径入库）。
    parser.add_argument("--cache", default="")
    parser.add_argument("--catalog", default=str(SPELL_CATALOG))
    args = parser.parse_args(argv[1:])
    opener = _open(args.proxy or None)
    cache_dir = Path(args.cache) if args.cache else None

    canonical = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    ours = [str(entry["id"]) for entry in canonical["spells"]]

    index = _get(opener, API_ROOT, cache_dir=cache_dir)
    by_name = {_normalize(row["name"]): row for row in index["results"]}
    missing = [spell_id for spell_id in ours if spell_id not in by_name]
    if missing:
        print(f"unmatched canonical ids: {missing}", file=sys.stderr)
        return 2

    spells: dict[str, dict[str, Any]] = {}
    for spell_id in sorted(ours):
        row = by_name[spell_id]
        detail = _get(
            opener, f"https://www.dnd5eapi.co{row['url']}", cache_dir=cache_dir,
        )
        entry: dict[str, Any] = {
            "name": str(detail["name"]),
            "level": int(detail.get("level", 0) or 0),
            "school": _normalize(str((detail.get("school") or {}).get("name") or "")),
            "desc": _paragraphs(detail),
        }
        higher = _paragraphs(detail, key="higher_level")
        if higher:
            entry["higher_level"] = higher
        if not entry["desc"]:
            print(f"empty description: {spell_id}", file=sys.stderr)
            return 3
        spells[spell_id] = entry

    payload = {
        "schema_version": 1,
        "kind": "spell_description_catalog",
        "id": "srd_spell_descriptions",
        "source_ref": "srd-5.2.1:p105-p176:spell-descriptions",
        "automation_level": "reference",
        "license": {
            "id": "CC-BY-4.0",
            "attribution": "legal/ATTRIBUTION.md",
            "transcription": "dnd5eapi.co (SRD 5.2.1)",
        },
        "spells": spells,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(spells)} spell descriptions to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
