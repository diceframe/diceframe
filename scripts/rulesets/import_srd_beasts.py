"""Generate ``srd_monsters.json`` (CR 0-1, every creature type) from open5e v2.

S0-MM2 needs stat blocks as *content*, not as fixtures: Wild Shape's known forms,
Polymorph's target forms and an encounter builder all read the same entries, and
the catalog must be reproducible from a named source rather than typed from
memory.  The slice ships CR 0-1 across **all** creature types, not only beasts.

Source: ``https://api.open5e.com/v2/creatures/?document__key=srd-2024`` filtered
to ``cr__lte=1`` before the client-side filter (the server-side one is ignored).  The feed publishes the same numbers the PDF
prints (ability scores, saving throws, skills, senses, hit dice, attacks), so the
only transformation here is *shape*: long ability names to short keys, prose
attack lines to the structured form the combat contract already accepts.

Deliberate omissions (this slate stops before traits and actions):

* ``traits`` — out of scope (S0-MM2 §7);
* action prose, Multiattack, save-based actions, recharge — same;
* page numbers — the feed carries no page index, so ``source_ref`` names the
  creature key instead of a page.  Recorded as a deviation in the report.

Run:

    python scripts/rulesets/import_srd_beasts.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

BASE = "https://api.open5e.com/v2/creatures/"
QUERY = "?document__key=srd-2024&limit=100"
OUTPUT = Path(
    "templates/rulesets/dnd2024_srd/content/monster_catalogs/srd_monsters.json"
)

ABILITIES = {
    "strength": "str", "dexterity": "dex", "constitution": "con",
    "intelligence": "int", "wisdom": "wis", "charisma": "cha",
}
CR_LABELS = {0: "0", 0.125: "1/8", 0.25: "1/4", 0.5: "1/2", 1: "1"}
SENSE_RANGES = {
    "darkvision_range": "darkvision",
    "blindsight_range": "blindsight",
    "tremorsense_range": "tremorsense",
    "truesight_range": "truesight",
}


def ability_modifier(score: int) -> int:
    return (int(score) - 10) // 2


def cr_label(value: float) -> str:
    return CR_LABELS.get(float(value), str(int(value)))


def fetch_all(opener: urllib.request.OpenerDirector) -> list[dict]:
    records: list[dict] = []
    url: str | None = BASE + QUERY
    while url:
        with opener.open(url, timeout=60) as response:
            payload = json.loads(response.read().decode())
        records.extend(payload["results"])
        url = payload.get("next")
    # API 的 ``cr__lte`` 没有生效（实测返回 CR 0–8），所以过滤放在本地，
    # 与 equipment 导入踩到的"查询参数不生效"是同一类问题。
    # 收的是 **CR 0–1 的全部生物**，不只是 Beast：玩家会遇到的敌人绝大多数在
    # 这一段，而目录按类型可筛，不必靠"只导野兽"来自我限制。
    return [record for record in records if float(record.get("challenge_rating") or 0) <= 1]


_DESC_ATTACK_RE = re.compile(
    r"(?P<kind>Melee|Ranged) Attack Roll:\s*\+(?P<bonus>\d+)"
    r"(?:\s*\([^)]*\))?(?:\s+to hit)?,\s*"
    r"(?:reach|range)\s*(?P<reach>\d+)(?:/\d+)?\s*(?:ft\.|feet)",
    re.IGNORECASE,
)
# 骰式优先于固定值：prose 写成 "4 (1d4 + 2) Slashing"，前面的 4 是平均值，
# 先匹固定值会把每一击都读成常数。
_DESC_DICE_RE = re.compile(r"(?P<count>\d+)d(?P<sides>\d+)(?:\s*\+\s*(?P<bonus>\d+))?")
_DESC_FLAT_RE = re.compile(r"(?<![\d(])(?P<flat>\d+)\s+(?:[A-Z][a-z]+)\s+damage")


def parse_prose_attack(action: dict, index: int) -> dict | None:
    """Fallback for the Tiny animals: the feed leaves ``attacks`` empty.

    Their whole line lives in the prose ("Melee Attack Roll: +2, reach 5 ft.
    1 Piercing damage."), which is exactly a flat-damage attack the formula layer
    already supports.
    """

    text = str(action.get("desc") or "")
    head = _DESC_ATTACK_RE.search(text)
    if head is None:
        return None
    tail = text[head.end():]
    dice = _DESC_DICE_RE.search(tail)
    if dice is not None:
        formula = f"{int(dice.group('count'))}d{int(dice.group('sides'))}"
        if dice.group("bonus"):
            formula += f"+{int(dice.group('bonus'))}"
    else:
        flat = _DESC_FLAT_RE.search(tail)
        if flat is None:
            return None
        formula = flat.group("flat")
    kind_match = re.search(
        r"(Bludgeoning|Piercing|Slashing|Fire|Cold|Acid|Poison|Necrotic|Radiant|"
        r"Lightning|Thunder|Psychic|Force)", tail, re.IGNORECASE,
    )
    slug = str(action.get("name") or "attack").lower().replace(" ", "-")
    reach = int(head.group("reach"))
    return {
        "id": f"{slug[:40]}-{index}",
        "name": str(action.get("name") or "Attack"),
        "attack_bonus": int(head.group("bonus")),
        "damage": formula,
        "damage_type": (kind_match.group(1).lower() if kind_match else "bludgeoning"),
        "range": reach,
        "long_range": reach,
    }


def map_attacks(actions: list[dict]) -> list[dict]:
    """Only the plain single-target weapon attacks survive.

    The contract's ``attacks[]`` is a flat profile (bonus / damage / reach), so a
    Multiattack or a save-based action cannot be expressed here — those belong to
    the actions slate, not to this catalog.
    """

    attacks: list[dict] = []
    for action in actions:
        if str(action.get("action_type") or "ACTION") != "ACTION":
            continue
        produced = 0
        for index, raw in enumerate(action.get("attacks") or []):
            damage = raw.get("damage_die_type")
            if not damage or not raw.get("damage_die_count"):
                # 固定伤害（"Hit: 1 Bludgeoning damage"）没有骰面字段，
                # 交给下面的散文回退，别在这里静默丢一条攻击。
                continue
            count = int(raw["damage_die_count"])
            sides = int(str(damage).lstrip("Dd"))
            bonus = int(raw.get("damage_bonus") or 0)
            formula = f"{count}d{sides}" + (f"{bonus:+d}" if bonus else "")
            kind = (raw.get("damage_type") or raw.get("extra_damage_type") or {})
            slug = str(action.get("name") or "attack").lower().replace(" ", "-")
            reach = int(raw.get("reach") or raw.get("range") or 5)
            attacks.append({
                "id": f"{slug[:40]}-{index}",
                "name": str(action.get("name") or "Attack"),
                "attack_bonus": int(raw.get("to_hit_mod") or 0),
                "damage": formula,
                "damage_type": str(kind.get("key") or "bludgeoning"),
                "range": reach,
                "long_range": int(raw.get("long_range") or reach),
            })
            produced += 1
        if not produced:
            fallback = parse_prose_attack(action, 0)
            if fallback is not None:
                attacks.append(fallback)
    return attacks


def map_record(record: dict) -> dict:
    scores = {
        ABILITIES[key]: int(value)
        for key, value in (record.get("ability_scores") or {}).items()
        if key in ABILITIES
    }
    declared_saves = {
        ABILITIES[key]: int(value)
        for key, value in (record.get("saving_throws") or {}).items()
        if key in ABILITIES
    }
    # 只保留「与裸调整值不同」的那几项：相同的条目没有信息量，存下来反而会
    # 把覆盖表撑成六项全量，让「派生」永远失效。
    saving_throws = {
        ability: value for ability, value in declared_saves.items()
        if ability in scores and value != ability_modifier(scores[ability])
    }
    speeds_raw = record.get("speed") or {}
    # ``hover`` 是布尔而不是尺数；``True`` 在 Python 里是 int 子类，若一起
    # 走 int() 会变成数字，契约会以「hover must be a boolean」拒绝整条。
    speeds: dict[str, int | bool] = {
        key: int(value) for key, value in speeds_raw.items()
        if key != "unit" and key != "hover"
        and isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    speeds = {key: value for key, value in speeds.items() if value > 0}
    if speeds_raw.get("hover"):
        speeds["hover"] = True
    if not speeds:
        speeds = {"walk": 30}
    senses = {
        name: int(record[key]) for key, name in SENSE_RANGES.items()
        if isinstance(record.get(key), (int, float)) and record[key] > 0
    }
    raw_languages = (record.get("languages") or {}).get("data") or []
    # feed 的语言是对象数组（name/key/desc），契约要的是名字列表。
    languages = [
        str(item.get("name") or item.get("key") or "").strip()
        if isinstance(item, dict) else str(item).strip()
        for item in raw_languages
    ]
    languages = [name for name in dict.fromkeys(languages) if name]
    defenses = record.get("resistances_and_immunities") or {}
    return {
        "id": str(record["key"]).removeprefix("srd-2024_"),
        "name": str(record["name"]),
        "source_ref": f"srd-5.2.1:creatures:{record['key']}",
        "creature_type": str((record.get("type") or {}).get("key") or "beast"),
        "subtype": str(record.get("subcategory") or ""),
        "size": str((record.get("size") or {}).get("key") or ""),
        "cr": cr_label(record.get("challenge_rating") or 0),
        "xp": int(record.get("experience_points") or 0),
        "proficiency_bonus": int(record.get("proficiency_bonus") or 2),
        "armor_class": int(record.get("armor_class") or 10),
        "hp": int(record.get("hit_points") or 1),
        "hit_dice": str(record.get("hit_dice") or "").replace(" ", ""),
        "speeds": speeds,
        "abilities": scores,
        "saving_throws": saving_throws,
        "skills": {
            str(key): int(value)
            for key, value in (record.get("skill_bonuses") or {}).items()
        },
        "passive_perception": int(record.get("passive_perception") or 0),
        "languages": languages,
        "senses": senses,
        "resist_damage_types": list(defenses.get("resistances") or []),
        "immune_damage_types": list(defenses.get("immunities") or []),
        "vulnerable_damage_types": list(defenses.get("vulnerabilities") or []),
        "attacks": map_attacks(record.get("actions") or []),
    }


def main() -> int:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # open5e 拒绝没有 User-Agent 的请求（403），与内容无关。
    opener.addheaders = [("User-Agent", "diceframe-srd-import/1.0")]
    records = fetch_all(opener)
    monsters = [map_record(record) for record in records]
    monsters.sort(key=lambda row: (row["cr"], row["name"]))
    missing_attacks = [row["id"] for row in monsters if not row["attacks"]]
    payload = {
        "schema_version": 1,
        "kind": "monster_catalog",
        "id": "srd_monsters",
        "source_ref": "srd-5.2.1:p344-364:animals",
        # 目录是 bundle 实体，头字段缺失会让整个 ruleset 加载失败。
        "automation_level": "deterministic",
        "entries": monsters,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(monsters)} beasts -> {OUTPUT}")
    if missing_attacks:
        print(f"no attacks parsed: {', '.join(missing_attacks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
