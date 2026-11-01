"""Generate dnd2024 spell_effects drafts from dnd5eapi (CC-BY 4.0) + 2024 rulebook.

Pipeline (prototype for the batch spell-fill plan):

  snapshot  Fetch 2014/2024 API details for the target spells into .imports/.
  draft     Map API skeletons to spell_effects drafts, verify dice values
            against the local 2024 rulebook (GBK-decoded CHM export), and
            emit three buckets: auto / corrected / manual.

Design rules (fail-closed):
  - Structure skeleton comes from the 2014 endpoint (fields are complete);
    attack_type prefers the 2024 endpoint when present.
  - Dice values are ALWAYS verified against the 2024 rulebook text; 2014
    values are never trusted (cure_wounds 1d8 -> 2d8 is the canonical case).
  - Anything unmappable (conditions, buffs, special semantics) or with a
    known missing effect primitive goes to the manual bucket with a reason.

Data source attribution: https://www.dnd5eapi.co (CC-BY-4.0, SRD 5.1/5.2).
Rulebook files are local, git-excluded, and never redistributed.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
API_BASE = "https://www.dnd5eapi.co/api"
SNAPSHOT_DIR = REPO / ".imports" / "dnd5eapi"
OUTPUT_DIR = REPO / ".imports" / "spell_fill"
RULEBOOK_DIR = REPO / ".rulebooks" / "dnd5r" / "玩家手册2024" / "法术详述"

# Target set: the "single" bucket from docs/internal/design/SPELL_GAP_MATRIX.md.
TARGET_IDS = [
    "shillelagh", "true_strike", "dissonant_whispers", "divine_favor",
    "ensnaring_strike", "hunter_s_mark", "hellish_rebuke", "mage_armor",
    "ray_of_sickness", "sanctuary", "searing_smite", "acid_arrow", "barkskin",
    "blindness_deafness", "blur", "dragon_s_breath", "flame_blade",
    "heat_metal", "invisibility", "magic_weapon", "mind_spike",
    "mirror_image", "ray_of_enfeeblement", "scorching_ray", "shining_smite",
    "warding_bond", "beacon_of_hope", "bestow_curse", "vampiric_touch",
    "blight", "fire_shield", "guardian_of_faith", "greater_invisibility",
    "phantasmal_killer", "vitriolic_sphere", "contagion", "flame_strike",
    "mislead", "seeming", "eyebite", "harm", "disintegrate",
    "finger_of_death", "power_word_kill", "foresight", "phantasmal_force",
]

# Known to need effect primitives that do not exist yet (SPELL_GAP_MATRIX
# P-numbers). These skip mapping entirely and land in manual with a reason.
PRIMITIVE_BLOCKED: dict[str, str] = {
    "true_strike": "P1 attack_sources（攻击优势）",
    "blur": "P1 attack_sources（来袭攻击劣势）",
    "invisibility": "P1 attack_sources（来袭攻击劣势）",
    "mirror_image": "P1 attack_sources（攻击重定向）",
    "mislead": "P1 attack_sources + P6 位移",
    "seeming": "P1 attack_sources（变形外观）",
    "greater_invisibility": "P1 attack_sources（来袭攻击劣势）",
    "foresight": "P1 attack_sources（优势+不可被惊）",
    "sanctuary": "P1 attack_sources（攻击重定向豁免）",
    "scorching_ray": "P3 多 beam 结算循环",
    "fire_shield": "P5 伤害类型抗性",
    "magic_weapon": "P12 武器改造",
    "flame_blade": "P12 武器改造",
    "shillelagh": "P12 武器改造",
    "heat_metal": "谓词（持有金属武器）",
}

NON_ID_RE = re.compile(r"[^a-z0-9]+")

# The 2024 rulebook keeps attribution prefixes that SRD 5.2.1 dropped from
# canonical ids (e.g. "Melf's Acid Arrow" -> acid_arrow). Map by H4 id here.
RULEBOOK_ALIASES: dict[str, str] = {
    "acid_arrow": "Melf's_Acid_Arrow",
}

# Semantic keywords scanned in the rulebook prose. A structurally mapped draft
# whose rulebook section carries these hints is demoted: the dice may match,
# but the primary resolution mode or a secondary effect needs human eyes.
AREA_HINTS = ("半径", "锥形", "立方")            # -> manual: geometry (phase 3)
TICK_HINTS = ("每回合结束", "回合结束时")         # -> manual: needs P2 tick
REACTION_HINTS = ("反应，", "反应：")             # -> review: reaction semantics
CONDITION_HINTS = ("目盲", "耳聋", "恐慌", "中毒", "麻痹", "束缚", "魅惑",
                   "无法恢复", "失能", "诅咒", "生命值上限",
                   "植物", "不死生物")  # -> review: condition / creature-type rider
TRIGGER_HINTS = ("命中时", "命中后", "下一次", "武器攻击", "恢复")  # -> review: rider damage / self-heal


def _spell_id(name: str) -> str:
    """Reproduce the catalog's canonical id rule (build_dnd2024_spell_catalog)."""
    return NON_ID_RE.sub("_", name.replace("’", "'").lower()).strip("_")


def _norm_key(value: str) -> str:
    """Loose match key: alphanumeric only, lowercase (handles Dragon's / slashes)."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


DICE_RE = re.compile(r"(\d+)d(\d+)(\s*\+\s*MOD)?")
MOD_HINT_RE = re.compile(r"施法属性调整值")


def _parse_dice(formula: str) -> tuple[int, int, bool] | None:
    match = DICE_RE.fullmatch(formula.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), bool(match.group(3))


def _dice_diff(high: str, low: str) -> str | None:
    """(N+k)dM minus NdM -> 'kdM'; requires same die size, no modifiers."""
    h, l = _parse_dice(high), _parse_dice(low)
    if not h or not l or h[1] != l[1] or h[2] or l[2]:
        return None
    if h[0] <= l[0]:
        return None
    return f"{h[0] - l[0]}d{h[1]}"


def _feet_range(entry: dict[str, Any]) -> int | None:
    rng = entry.get("range")
    if not isinstance(rng, dict):
        return None
    distance = rng.get("distance") or {}
    if rng.get("type") == "point":
        if distance.get("type") == "touch":
            return 5
        if distance.get("type") == "self":
            return 0
        amount = distance.get("amount")
        return int(amount) if isinstance(amount, (int, float)) else None
    return None


def _damage_blocks(entry: dict[str, Any]) -> list[dict[str, Any]]:
    damage = entry.get("damage")
    if not damage:
        return []
    return damage if isinstance(damage, list) else [damage]


def _slot_tables(entry: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Merge 2014 (full table) and 2024 (base only) damage_at_slot_level."""
    tables: dict[str, dict[str, str]] = {}
    for block in _damage_blocks(entry):
        for slot, formula in (block.get("damage_at_slot_level") or {}).items():
            tables.setdefault(str(slot), {})[
                block["damage_type"]["index"]
            ] = formula
    return tables


def _map_effect(entry_2014: dict[str, Any], entry_2024: dict[str, Any]) -> tuple[dict[str, Any], list[str]] | None:
    """Map one spell to a spell_effects draft. Returns (draft, notes) or None."""
    notes: list[str] = []
    attack = entry_2024.get("attack_type") or entry_2014.get("attack_type")
    level = entry_2014.get("level", entry_2024.get("level"))
    tables = _slot_tables(entry_2014)
    if not tables:
        tables = _slot_tables(entry_2024)
    heals = entry_2014.get("heal_at_slot_level") or entry_2024.get("heal_at_slot_level")
    dc = entry_2014.get("dc") or entry_2024.get("dc")
    spell_range = _feet_range(entry_2014) or _feet_range(entry_2024)

    if heals:
        base = heals.get(str(level))
        upcast = heals.get(str(level + 1))
        if not base:
            return None
        parsed = _parse_dice(base)
        if not parsed:
            return None
        draft: dict[str, Any] = {"mode": "healing", "healing": f"{parsed[0]}d{parsed[1]}"}
        if parsed[2] or MOD_HINT_RE.search(str(base)):
            draft["add_spell_ability"] = True
        if upcast:
            diff = _dice_diff(upcast, base)
            if diff:
                draft["upcast_healing"] = diff
            else:
                notes.append(f"upcast 差值不可推导: {upcast!r} vs {base!r}")
        if spell_range is not None:
            draft["range"] = spell_range
        return draft, notes

    if not tables:
        return None
    slots = tables.get(str(level)) or {}
    if not slots:
        return None
    if len(slots) > 1:
        return None, ["MULTITYPE:%s" % "+".join(sorted(slots))]
    damage_type, base = sorted(slots.items())[0]
    upcast_formula = (tables.get(str(level + 1)) or {}).get(damage_type) or ""
    parsed = _parse_dice(base)
    if not parsed:
        return None
    damage = f"{parsed[0]}d{parsed[1]}" if not parsed[2] else base

    if attack == "ranged" or attack == "melee":
        draft = {
            "mode": "spell_attack",
            "damage": damage,
            "damage_type": damage_type,
        }
    elif dc:
        draft = {
            "mode": "saving_throw",
            "save": dc["dc_type"]["index"],
            "damage": damage,
            "damage_type": damage_type,
        }
    else:
        draft = {
            "mode": "automatic_damage",
            "damage": damage,
            "damage_type": damage_type,
        }
    if upcast_formula:
        diff = _dice_diff(upcast_formula, base)
        if diff:
            draft["upcast_damage"] = diff
        else:
            notes.append(f"upcast 差值不可推导: {upcast_formula!r} vs {base!r}")
    if spell_range is not None:
        draft["range"] = spell_range
    return draft, notes


def _load_rulebook_sections() -> dict[str, str]:
    """Split 法术详述/*.htm into sections keyed by normalized H4 id."""
    sections: dict[str, str] = {}
    if not RULEBOOK_DIR.is_dir():
        return sections
    for path in sorted(RULEBOOK_DIR.glob("*.htm")):
        text = path.read_text(encoding="utf-8", errors="replace")
        parts = re.split(r"<H4\s+id=\"([^\"]+)\"", text)
        for i in range(1, len(parts) - 1, 2):
            h4_id, body = parts[i], parts[i + 1]
            sections[_norm_key(h4_id)] = body
    return sections


def _rulebook_dice(section: str) -> set[tuple[int, int]]:
    return {(int(n), int(m)) for n, m in re.findall(r"(\d+)d(\d+)", section)}


def _verify_against_rulebook(
    spell_id: str, draft: dict[str, Any], sections: dict[str, str],
) -> tuple[dict[str, Any], str, str]:
    """Verify dice + scan semantics; return (final_draft, bucket, detail).

    Buckets: auto (no hints, anchored mode, dice verified) / corrected (dice
    fixed per rulebook, still no hints) / review (mode plausible but rulebook
    prose carries riders: conditions, triggers, reactions, or no attack/save
    anchor at all) / manual (unmappable or hard blockers).
    """
    lookup = RULEBOOK_ALIASES.get(spell_id, spell_id)
    section = sections.get(_norm_key(lookup))
    if section is None:
        return draft, "manual", "规则书未命中（改名或拆分？）"
    book = _rulebook_dice(section)

    # Hard semantic blockers outrank dice verification.
    area = next((w for w in AREA_HINTS if w in section), None)
    if area:
        return draft, "manual", f"区域语义（{area}，阶段三几何依赖）"
    tick = next((w for w in TICK_HINTS if w in section), None)
    if tick:
        return draft, "manual", f"伤害结算在{tick}（P2 tick 依赖）"

    def _check(value: str, key: str) -> tuple[str, str, bool]:
        parsed = _parse_dice(value)
        if not parsed:
            return value, key, False
        pair = (parsed[0], parsed[1])
        if pair in book:
            return value, key, True
        same_die = sorted(p for p in book if p[1] == pair[1])
        if len(same_die) == 1:
            return f"{same_die[0][0]}d{same_die[0][1]}", key, True
        return value, key, False

    checks: list[tuple[str, str, bool]] = []
    corrected = dict(draft)
    for key in ("damage", "healing"):
        if key in draft:
            checks.append(_check(draft[key], key))
    for key in ("upcast_damage", "upcast_healing"):
        if key in draft:
            value, _, ok = _check(draft[key], key)
            if not ok:
                corrected.pop(key, None)
            else:
                corrected[key] = value
    for value, key, ok in checks:
        if not ok:
            return draft, "manual", f"{key}={value} 不在规则书骰式集合 {sorted(book)}"
        corrected[key] = value
    corrected_changed = any(
        corrected.get(k) != draft.get(k) for k in ("damage", "healing")
    )
    if corrected_changed:
        dice_note = (f"{draft.get('damage') or draft.get('healing')} -> "
                     f"{corrected.get('damage') or corrected.get('healing')}（以规则书为准）")
    else:
        dice_note = "数值与规则书一致"

    # Soft riders keep the draft but demand human confirmation.
    hints: list[str] = []
    if draft.get("mode") == "automatic_damage":
        hints.append("无攻击/豁免锚点，主结算模式待人工确认")
    for word in REACTION_HINTS:
        if word in section:
            hints.append(f"反应语义（{word.strip('，：')}）")
            break
    for label, group in (("condition", CONDITION_HINTS), ("trigger", TRIGGER_HINTS)):
        for word in group:
            if word in section:
                hints.append(f"{label} 次要效果（{word}）")
                break
    if hints:
        return corrected, "review", dice_note + "；" + "；".join(hints)
    return corrected, ("corrected" if corrected_changed else "auto"), dice_note


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "diceframe-maintenance"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def cmd_snapshot() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for version in ("2014", "2024"):
        index = _fetch_json(f"{API_BASE}/{version}/spells")
        by_key = {
            _spell_id(item["name"]): item["index"] for item in index["results"]
        }
        (SNAPSHOT_DIR / f"{version}_index.json").write_text(
            json.dumps(by_key, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        target_dir = SNAPSHOT_DIR / version
        target_dir.mkdir(parents=True, exist_ok=True)
        hits, misses = 0, []
        for spell_id in TARGET_IDS:
            api_index = by_key.get(spell_id)
            if not api_index:
                misses.append(spell_id)
                continue
            dest = target_dir / f"{api_index}.json"
            if dest.exists():
                hits += 1
                continue
            dest.write_text(
                json.dumps(_fetch_json(f"{API_BASE}/{version}/spells/{api_index}"),
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            hits += 1
            time.sleep(0.15)
        print(f"[{version}] snapshot {hits}/{len(TARGET_IDS)}; missing: {misses}")


def cmd_draft() -> None:
    sections = _load_rulebook_sections()
    buckets: dict[str, dict[str, Any]] = {"auto": {}, "corrected": {}, "review": {}}
    manual: list[tuple[str, str]] = []
    for spell_id in TARGET_IDS:
        if spell_id in PRIMITIVE_BLOCKED:
            manual.append((spell_id, f"等待原语：{PRIMITIVE_BLOCKED[spell_id]}"))
            continue
        paths = {
            version: SNAPSHOT_DIR / version for version in ("2014", "2024")
        }
        entries: dict[str, dict[str, Any]] = {}
        for version, folder in paths.items():
            index_map = json.loads((folder.parent / f"{version}_index.json").read_text(encoding="utf-8"))
            api_index = index_map.get(spell_id)
            if api_index and (folder / f"{api_index}.json").exists():
                entries[version] = json.loads((folder / f"{api_index}.json").read_text(encoding="utf-8"))
        if "2014" not in entries and "2024" not in entries:
            manual.append((spell_id, "API 快照缺失"))
            continue
        mapped = _map_effect(entries.get("2014", {}), entries.get("2024", {}))
        if not mapped:
            manual.append((spell_id, "无 damage/dc/heal 结构（condition/buff/特殊语义）"))
            continue
        draft, notes = mapped
        if notes and notes[0].startswith("MULTITYPE"):
            manual.append((spell_id, f"多伤害类型（{notes[0].split(':', 1)[1]}），schema 单类型限制"))
            continue
        final, bucket, detail = _verify_against_rulebook(spell_id, draft, sections)
        line = f"{spell_id}: {detail}" + (f"；注：{'；'.join(notes)}" if notes else "")
        if bucket in buckets:
            buckets[bucket][spell_id] = final
        else:
            manual.append((spell_id, detail))
        print(f"[{bucket:<9}] {line}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "drafts.json").write_text(
        json.dumps({**buckets, "manual": dict(manual)},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = [
        "# 法术批量填充原型报告（spell_effects 草稿）",
        "",
        f"- auto（可直接入库）: {len(buckets['auto'])}",
        f"- corrected（数值已按规则书修正）: {len(buckets['corrected'])}",
        f"- review（模式/数值可靠，含待人工确认的次要语义）: {len(buckets['review'])}",
        f"- manual（人工队列）: {len(manual)}",
        "",
    ]
    for name in ("corrected", "review"):
        report += [f"## {name}", ""]
        report += [f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in sorted(buckets[name].items())]
        report += [""]
    report += ["## manual", ""]
    report += [f"- {k}: {reason}" for k, reason in manual]
    (OUTPUT_DIR / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\nwrote {OUTPUT_DIR / 'drafts.json'} and report.md "
          f"(auto={len(buckets['auto'])}, corrected={len(buckets['corrected'])}, "
          f"review={len(buckets['review'])}, manual={len(manual)})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["snapshot", "draft"])
    args = parser.parse_args()
    if args.command == "snapshot":
        cmd_snapshot()
    else:
        cmd_draft()


if __name__ == "__main__":
    main()
