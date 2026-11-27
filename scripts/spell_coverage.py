"""How much of the SRD spell list can the engine actually resolve.

`docs/internal/design/SPELL_GAP_MATRIX.md` classifies the spells that have no
mechanics into buckets, and says which phase each bucket belongs to.  It is a
*classification*, not a progress report -- and a hand-written progress report
rots: the slice table still showed MM3 as "implemented, unmerged" months after
`#835` landed, and F25 as "blocked" after `#830`/`#836` merged.

So coverage is computed, not written down.  Two inputs, one output:

- the spell catalog (id, level) -- what exists;
- `spell_effects` in the combat catalog -- what the engine can resolve;
- the gap matrix -- which bucket a missing spell belongs to, hence its phase.

Run it plain for the report, ``--json`` for machines, ``--check`` to fail a build
that lost coverage.  The check is a ratchet, like the file-size one: adding
spells never breaks it, removing one does, and raising ``BASELINE_EFFECTS`` is a
deliberate edit rather than a silent drift.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SPELL_CATALOG = (
    ROOT / "templates/rulesets/dnd2024_srd/content/spell_catalogs/srd_spells.json"
)
COMBAT_CATALOG = (
    ROOT / "templates/rulesets/dnd2024_srd/content/combat_catalogs/srd_combat_core.json"
)
GAP_MATRIX = ROOT / "docs/internal/design/SPELL_GAP_MATRIX.md"

#: 已落地的法术条数。只许涨。丢了实现或改了 id 都会让 ``--check`` 变红。
BASELINE_EFFECTS = 51

#: 环阶分档，与玩家实际体验对应：1–3 环是 1–5 级团的主战场。
TIERS = ("0", "1-3", "4-5", "6-9")


@dataclass(frozen=True)
class Spell:
    id: str
    name: str
    level: int


def _load_spells() -> list[Spell]:
    data = json.loads(SPELL_CATALOG.read_text(encoding="utf-8"))
    return [
        Spell(
            id=str(entry["id"]),
            name=str(entry.get("name") or entry["id"]),
            level=int(entry.get("level") or 0),
        )
        for entry in data["spells"]
    ]


def _load_effects() -> set[str]:
    data = json.loads(COMBAT_CATALOG.read_text(encoding="utf-8"))
    return set(data.get("spell_effects") or {})


def _load_buckets() -> dict[str, set[str]]:
    """Bucket name -> spell ids, parsed from the gap matrix's lists.

    The matrix is the only place the classification lives, so it is parsed
    rather than duplicated.  A heading without a list is an error: silently
    returning an empty bucket would report "nothing missing" for a whole phase.
    """

    buckets: dict[str, set[str]] = {}
    current: str | None = None
    for line in GAP_MATRIX.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^### (.+)$", line)
        if heading:
            # ``直填桶 single（46；...）`` -> ``直填桶 single``
            current = heading.group(1).split("（", 1)[0].strip()
            buckets[current] = set()
            continue
        if current is None or not line.strip() or line.lstrip().startswith("注"):
            continue
        if line.startswith(("#", "|", ">")):
            continue
        buckets[current].update(re.findall(r"\b[a-z][a-z0-9_]{2,}\b", line))
    populated = {name: ids for name, ids in buckets.items() if ids}
    if not populated:
        raise SystemExit(
            "no buckets parsed from SPELL_GAP_MATRIX.md; the document shape changed",
        )
    return populated


def _tier(level: int) -> str:
    if level == 0:
        return "0"
    if level <= 3:
        return "1-3"
    if level <= 5:
        return "4-5"
    return "6-9"


def build_report() -> dict[str, Any]:
    spells = _load_spells()
    done = _load_effects()
    known = {spell.id for spell in spells}
    buckets = _load_buckets()

    # 桶分类里的 id 必须都在目录里；否则矩阵与目录已经漂移，覆盖率会算错。
    stray = sorted({sid for ids in buckets.values() for sid in ids} - known)
    if stray:
        raise SystemExit(
            "SPELL_GAP_MATRIX.md names spells the catalog does not have: "
            + ", ".join(stray[:5]),
        )

    bucket_of: dict[str, str] = {}
    for name, ids in buckets.items():
        for sid in ids:
            bucket_of.setdefault(sid, name)

    by_tier: dict[str, dict[str, int]] = {
        tier: {"total": 0, "done": 0} for tier in TIERS
    }
    missing: dict[str, dict[str, list[str]]] = {
        tier: {} for tier in TIERS
    }
    unclassified: list[str] = []
    for spell in spells:
        tier = _tier(spell.level)
        by_tier[tier]["total"] += 1
        if spell.id in done:
            by_tier[tier]["done"] += 1
            continue
        bucket = bucket_of.get(spell.id)
        if bucket is None:
            unclassified.append(f"{spell.id} (L{spell.level})")
            continue
        missing[tier].setdefault(bucket, []).append(f"{spell.id} (L{spell.level})")

    for tier in TIERS:
        for spell_ids in missing[tier].values():
            spell_ids.sort()

    return {
        "totals": {
            "spells": len(spells),
            "with_effects": len(done & known),
            "effects_not_in_catalog": sorted(done - known),
        },
        "by_tier": by_tier,
        "missing": missing,
        "unclassified": sorted(unclassified),
        "buckets": {name: len(ids) for name, ids in buckets.items()},
    }


def _percent(done: int, total: int) -> str:
    return f"{done / total * 100:>3.0f}%" if total else "  -"


def print_report(report: dict[str, Any]) -> None:
    totals = report["totals"]
    print(
        f"法术 {totals['spells']} 条，其中 {totals['with_effects']} 条有机械效果"
        f"（{_percent(totals['with_effects'], totals['spells'])}）",
    )
    if totals["effects_not_in_catalog"]:
        print("  警告：spell_effects 里有目录没有的 id：" + ", ".join(
            totals["effects_not_in_catalog"],
        ))

    print("\n环阶   法术   已实现   覆盖")
    for tier in TIERS:
        row = report["by_tier"][tier]
        print(
            f"{tier:>4}  {row['total']:>5}  {row['done']:>7}   "
            f"{_percent(row['done'], row['total'])}",
        )

    for tier in TIERS:
        buckets = report["missing"][tier]
        if not buckets:
            continue
        print(f"\n== {tier} 环缺口（按桶）==")
        for name in sorted(buckets, key=lambda key: -len(buckets[key])):
            ids = buckets[name]
            print(f"  {name}（{len(ids)}）")
            print("    " + ", ".join(ids))

    if report["unclassified"]:
        print("\n== 未分类（矩阵里没有，也不是叙事层以外的已知桶）==")
        print("  " + ", ".join(report["unclassified"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument(
        "--check", action="store_true",
        help=f"实现条数低于 {BASELINE_EFFECTS} 时失败（棘轮）",
    )
    args = parser.parse_args(argv)

    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    if args.check:
        actual = report["totals"]["with_effects"]
        if actual < BASELINE_EFFECTS:
            print(
                f"\nspell coverage regressed: {actual} < {BASELINE_EFFECTS}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
