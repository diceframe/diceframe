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

What it maps, and what it deliberately leaves out:

* attacks: one or two damage instances plus the four rider kinds (S0-MM3a);
* actions: Multiattack (``repeat`` / ``combo``) and save-based actions with
  their ``recharge`` / ``per_day`` limits (S0-MM3b/c);
* traits: eight closed ids (S0-MM3d);
* not mapped: replacement attacks, "any combination", spellcasting actions,
  movement-precondition riders, tiered failures, attach/swallow — each needs a
  primitive the engine does not have yet, and each is registered as a gap
  instead of being approximated;
* page numbers — the feed carries no page index, so ``source_ref`` names the
  creature key instead of a page.  Recorded as a deviation in the report.

Run:

    python scripts/rulesets/import_srd_beasts.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

BASE = "https://api.open5e.com/v2/creatures/"
#: 收进来的最高 CR。默认是当前交付档位（S0-MM4）。
CR_MAX = 4.0
#: 已经交付过的那一档：这一档的条目即使有缺口也留在 ``entries`` 里
#: （删既有内容是独立决定），缺口记进 ``incomplete``。
DELIVERED_CR = 1.0
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
    # 收的是 **CR 上限以内的全部生物**，不只是 Beast：目录按类型可筛，不必靠
    # "只导野兽"来自我限制。上限是 ``CR_MAX``，默认 4（S0-MM4）。
    return [
        record for record in records
        if float(record.get("challenge_rating") or 0) <= CR_MAX
    ]


_DESC_ATTACK_RE = re.compile(
    r"(?P<kind>Melee or Ranged|Melee|Ranged) Attack Roll:\s*\+(?P<bonus>\d+)"
    r"(?:\s*\([^)]*\))?(?:\s+to hit)?,\s*"
    r"(?:reach\s*(?P<reach>\d+)|range\s*(?P<range>\d+)(?:/(?P<long>\d+))?)"
    r"\s*(?:ft\.|feet)",
    re.IGNORECASE,
)
# 骰式优先于固定值：prose 写成 "4 (1d4 + 2) Slashing"，前面的 4 是平均值，
# 先匹固定值会把每一击都读成常数。
_DESC_DICE_RE = re.compile(r"(?P<count>\d+)d(?P<sides>\d+)(?:\s*\+\s*(?P<bonus>\d+))?")
_DESC_FLAT_RE = re.compile(r"(?<![\d(])(?P<flat>\d+)\s+(?:[A-Z][a-z]+)\s+damage")
# S0-MM3a："5 (1d6 + 2) Piercing damage plus 5 (2d4) Poison damage" 是两段
# 伤害，不是一段。第二段只在明确写了 "plus" 时才存在。
# "reach 10 ft. or range 20/60 ft."：投掷武器的近战触及与射程同时印着，
# 主正则只会吃掉靠前的那一个，第二个要单独找。
_DESC_ALT_RANGE_RE = re.compile(
    r"\bor range\s*(?P<range>\d+)(?:/(?P<long>\d+))?\s*(?:ft\.|feet)",
    re.IGNORECASE,
)
_DESC_SECOND_PART_RE = re.compile(
    r"plus\s+\d+\s*\((?P<count>\d+)d(?P<sides>\d+)(?:\s*\+\s*(?P<bonus>\d+))?\)\s*"
    r"(?P<type>Bludgeoning|Piercing|Slashing|Fire|Cold|Acid|Poison|Necrotic|"
    r"Radiant|Lightning|Thunder|Psychic|Force)\s+damage",
    re.IGNORECASE,
)
# 附带效果的三种可解析句式。不做"读完整段散文"的自然语言解析：只认这三条
# 明确的形状，其余一律不猜（未解析的部分在执行报告里按数量登记）。
_RIDER_SIZE_GATE_RE = re.compile(
    r"If the target is an? (?P<size>Tiny|Small|Medium|Large|Huge) or smaller creature,?\s*"
    r"it has the (?P<condition>Prone|Grappled|Restrained|Blinded|Charmed|"
    r"Frightened|Poisoned|Incapacitated) condition"
    r"(?: \(escape DC (?P<escape>\d+)\))?",
)
_RIDER_ON_HIT_RE = re.compile(
    r",\s*and the target has the (?P<condition>Prone|Grappled|Restrained|Blinded|"
    r"Charmed|Frightened|Poisoned|Deafened|Incapacitated) condition"
    r"(?: until the (?P<when>start|end) of (?:its|the (?P<owner>[A-Za-z' -]+?)'s) next turn)?",
)
_RIDER_SAVE_RE = re.compile(
    r"(?P<ability>Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma) "
    r"Saving Throw: DC (?P<dc>\d+)[^.]*\.\s*(?:First )?Failure: The target has the "
    r"(?P<condition>Prone|Grappled|Restrained|Blinded|Charmed|Frightened|Poisoned|"
    r"Deafened|Incapacitated|Paralyzed) condition"
    r"(?: until the (?P<when>start|end) of (?:its|the (?P<owner>[A-Za-z' -]+?)'s) next turn)?",
)
_ABILITIES = {
    "strength": "str", "dexterity": "dex", "constitution": "con",
    "intelligence": "int", "wisdom": "wis", "charisma": "cha",
}


def rider_duration(when: str, owner: str) -> str | None:
    """Map "until … next turn" prose to a duration the engine expires.

    ``owner`` is the creature the sentence names: its own turn is the target's
    turn ("its"), otherwise it is the attacker's.  Anything else (a third
    party, "1 minute", "while grappled") is not expressible and returns None so
    the rider is left without a duration instead of getting a wrong one.
    """

    if not when:
        return ""
    text = owner.strip().lower()
    if not text:
        return "target_turn_end" if when == "end" else "target_turn_start"
    return "attacker_next_turn_start" if when == "start" else None


def parse_riders(desc: str) -> list[dict]:
    """The riders this prose states, in the order it states them."""

    riders: list[dict] = []
    if re.search(r"that isn't an? (?:Undead|elf|Construct)", desc):
        # "isn't an Undead or elf" 是"对谁生效"的生物类型前提，本片没有这个
        # 谓词；不解析比默默对所有目标生效安全。
        return riders
    if "Failure by 5 or More" in desc or "Second Failure" in desc:
        # 分档失败（差 5 点以上 / 第二次失败）需要"差多少"这个事实，当前
        # 没有。整条不解析，避免把两阶段效果压成一段。
        return riders
    gated = _RIDER_SIZE_GATE_RE.search(desc)
    if gated is not None:
        condition = gated.group("condition").lower()
        escape = gated.group("escape")
        if condition == "grappled" and escape:
            riders.append({
                "kind": "grapple", "escape_dc": int(escape),
                "max_target_size": gated.group("size").lower(),
            })
        else:
            riders.append({
                "kind": "prone" if condition == "prone" else "condition",
                **({"condition": condition} if condition != "prone" else {}),
                "max_target_size": gated.group("size").lower(),
            })
        return riders
    saved = _RIDER_SAVE_RE.search(desc)
    if saved is not None:
        rider: dict = {"kind": "condition", "condition": saved.group("condition").lower(),
                       "save": {"ability": _ABILITIES[saved.group("ability").lower()],
                                "dc": int(saved.group("dc"))}}
        duration = rider_duration(saved.group("when") or "", saved.group("owner") or "")
        if duration is None:
            return riders
        if duration:
            rider["duration"] = duration
        riders.append(rider)
        return riders
    plain = _RIDER_ON_HIT_RE.search(desc)
    if plain is not None:
        condition = plain.group("condition").lower()
        rider = {"kind": "prone" if condition == "prone" else "condition"}
        if condition != "prone":
            rider["condition"] = condition
        duration = rider_duration(plain.group("when") or "", plain.group("owner") or "")
        if duration is None:
            return riders
        if duration:
            rider["duration"] = duration
        riders.append(rider)
    return riders


def second_damage_part(desc: str) -> dict | None:
    """The "plus 1d6 Fire" half of an attack, when the prose states one.

    "plus 2 (1d4) Slashing damage **if the attack roll had Advantage**" is not
    this: the instance only exists conditionally, and importing it as
    unconditional damage would make every goblin hit harder than the stat block
    says.  Anything the sentence qualifies after the dice is left out.
    """

    match = _DESC_SECOND_PART_RE.search(desc)
    if match is None:
        return None
    rest = desc[match.end():].lstrip()
    if rest.startswith("if "):
        return None
    formula = f"{int(match.group('count'))}d{int(match.group('sides'))}"
    if match.group("bonus"):
        formula += f"+{int(match.group('bonus'))}"
    return {"dice": formula, "type": match.group("type").lower()}


def parse_prose_attack(action: dict, index: int) -> dict | None:
    """Fallback for the Tiny animals: the feed leaves ``attacks`` empty.

    Their whole line lives in the prose ("Melee Attack Roll: +2, reach 5 ft.
    1 Piercing damage."), which is exactly a flat-damage attack the formula layer
    already supports.
    """

    description = str(action.get("desc") or "")
    head = _DESC_ATTACK_RE.search(description)
    if head is None:
        return None
    tail = description[head.end():]
    # 主伤害只读 " plus " 之前那一段：蝎子/蜘蛛的整条是
    # "1 Piercing damage plus 3 (1d6) Poison damage"，从整条里找骰式会把第二段
    # 的骰子当主伤害——那不是"少读一段"，是把 1 点读成了 1d6。
    primary_text = tail.split(" plus ")[0]
    dice = _DESC_DICE_RE.search(primary_text)
    if dice is not None:
        formula = f"{int(dice.group('count'))}d{int(dice.group('sides'))}"
        if dice.group("bonus"):
            formula += f"+{int(dice.group('bonus'))}"
    else:
        flat = _DESC_FLAT_RE.search(primary_text)
        if flat is None:
            return None
        formula = flat.group("flat")
    kind_match = re.search(
        r"(Bludgeoning|Piercing|Slashing|Fire|Cold|Acid|Poison|Necrotic|Radiant|"
        r"Lightning|Thunder|Psychic|Force)", primary_text, re.IGNORECASE,
    )
    slug = str(action.get("name") or "attack").lower().replace(" ", "-")
    reach = int(head.group("reach") or 0)
    ranged = head.group("range")
    long_side = head.group("long")
    if ranged is None:
        alternate = _DESC_ALT_RANGE_RE.search(description[head.end():head.end() + 40])
        if alternate is not None:
            ranged = alternate.group("range")
            long_side = alternate.group("long")
    if ranged is not None:
        # 投掷/远程形态优先：射程是这只生物真正会用的距离，近战触及都短于它。
        range_ft = int(ranged)
        long_range = int(long_side or ranged)
    else:
        range_ft = reach or 5
        long_range = range_ft
    parts = [{
        "dice": formula,
        "type": (kind_match.group(1).lower() if kind_match else "bludgeoning"),
    }]
    second = second_damage_part(description)
    if second is not None:
        parts.append(second)
    profile = {
        "id": f"{slug[:40]}-{index}",
        "name": str(action.get("name") or "Attack"),
        "attack_bonus": int(head.group("bonus")),
        "damage": parts,
        "range": range_ft,
        "long_range": long_range,
    }
    riders = parse_riders(description)
    if riders:
        profile["riders"] = riders
    return profile


def map_attacks(actions: list[dict]) -> list[dict]:
    """Only the plain single-target weapon attacks survive.

    ``attacks[]`` carries the damage instances and the riders a hit applies; a
    Multiattack or a save-based action still cannot be expressed here — those
    belong to the actions slate, not to this catalog.
    """

    attacks: list[dict] = []
    for action in actions:
        if str(action.get("action_type") or "ACTION") != "ACTION":
            continue
        description = str(action.get("desc") or "")
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
            parts = [{"dice": formula, "type": str(kind.get("key") or "bludgeoning")}]
            second = second_damage_part(description)
            if second is not None:
                parts.append(second)
            profile = {
                "id": f"{slug[:40]}-{index}",
                "name": str(action.get("name") or "Attack"),
                "attack_bonus": int(raw.get("to_hit_mod") or 0),
                "damage": parts,
                "range": reach,
                "long_range": int(raw.get("long_range") or reach),
            }
            riders = parse_riders(description)
            if riders:
                profile["riders"] = riders
            attacks.append(profile)
            produced += 1
        if not produced:
            fallback = parse_prose_attack(action, 0)
            if fallback is not None:
                attacks.append(fallback)
    return attacks


_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_ATTACK_NAME = r"(?P<name>[A-Z][A-Za-z'’ -]*?)"
# "The armor makes two Slam attacks." —— 三种可表达的形状之一。
_MULTI_REPEAT_RE = re.compile(
    r"makes (?P<count>one|two|three|four|five) " + _ATTACK_NAME + r" attacks?\.",
)
# "The bear makes one Bite attack and one Claw attack."
_MULTI_COMBO_RE = re.compile(
    r"makes (?P<count>one|two|three) " + _ATTACK_NAME + r" attack and "
    r"(?P<count2>one|two|three) (?P<name2>[A-Z][A-Za-z'’ -]*?) attacks?\.",
)
# 这些说法需要本片没有表达力的机制（替换攻击、组合自由选择、施法）。整条
# 不解析，不做"近似成 repeat"。
_MULTI_UNSUPPORTED = (
    "replace", "Spellcasting", "in any combination", " or ", "uses ", "use ",
)


def parse_multiattack(action: dict, attacks: list[dict]) -> dict | None:
    """One Multiattack line as a ``repeat`` or ``combo`` action, or nothing.

    Every referenced name must resolve inside *this* creature's attack list:
    "Rend" is a real attack for some beasts and another creature's combo name
    for others, so a global lookup would attach the wrong numbers.
    """

    text = str(action.get("desc") or "")
    if any(word in text for word in _MULTI_UNSUPPORTED):
        return None

    def resolve(name: str) -> str | None:
        wanted = name.strip().lower()
        for attack in attacks:
            if str(attack.get("name") or "").lower() == wanted:
                return str(attack["id"])
        return None

    combo = _MULTI_COMBO_RE.search(text)
    if combo is not None:
        first = resolve(combo.group("name"))
        second = resolve(combo.group("name2"))
        if not first or not second:
            return None
        return {
            "id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
            "combo": [
                {"attack_id": first, "count": _COUNT_WORDS[combo.group("count")]},
                {"attack_id": second, "count": _COUNT_WORDS[combo.group("count2")]},
            ],
        }
    repeat = _MULTI_REPEAT_RE.search(text)
    if repeat is not None:
        attack_id = resolve(repeat.group("name"))
        if not attack_id:
            return None
        return {
            "id": "multiattack-0", "name": "Multiattack", "kind": "multiattack",
            "repeat": {
                "attack_id": attack_id, "count": _COUNT_WORDS[repeat.group("count")],
            },
        }
    return None


_SAVE_ACTION_RE = re.compile(
    r"(?P<ability>Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma) "
    r"Saving Throw: DC (?P<dc>\d+)",
)
_SAVE_ROLL_RE = re.compile(
    r"(?P<flat>\d+)?\s*\((?P<count>\d+)d(?P<sides>\d+)(?:\s*\+\s*(?P<bonus>\d+))?\)\s*"
    r"(?P<type>Bludgeoning|Piercing|Slashing|Fire|Cold|Acid|Poison|Necrotic|"
    r"Radiant|Lightning|Thunder|Psychic|Force)\s+damage",
    re.IGNORECASE,
)
_USAGE_RE = re.compile(r"\(Recharge (?P<from>\d)(?:-(?P<to>\d))?\)", re.IGNORECASE)
_USAGE_PER_DAY_RE = re.compile(r"\((?P<count>\d)/(?:Day|day)\)")


def parse_save_action(action: dict) -> dict | None:
    """One "save or suffer" action as ``on_fail`` / ``on_success`` damage.

    Only the plain shape is read: a line naming one save DC, one damage roll
    charged on a failure, and either "Half damage" or a second named roll on a
    success.  Anything that also applies a condition, retries the save, or
    depends on how badly the save failed needs primitives this slice does not
    have, so it is left out rather than approximated.

    "Half damage" becomes an explicit, independently rolled pool: the failure's
    dice halved when the count is even (``4d6`` → ``2d6``), and the same pool
    flagged ``half`` when it is odd — two and a half dice do not exist, and
    rounding the *failure* roll would make the success branch depend on dice it
    must not share.
    """

    text = str(action.get("desc") or "")
    head = _SAVE_ACTION_RE.search(text)
    if head is None:
        return None
    if text.count("Saving Throw:") > 1 or any(
        word in text for word in ("repeats the save", "Failure by", "Second Failure")
    ):
        # 重掷豁免或分档失败都需要本片没有的机制。
        return None
    failure = text.find("Failure:")
    if failure < 0:
        return None
    fail_rows = _SAVE_ROLL_RE.findall(text[failure:])
    if len(fail_rows) != 1:
        # 0 条表示"失败只施加条件"（本片用条件 rider 表达，不是这里）；
        # 多条表示一次豁免打两种伤害，当前契约只收一条 outcome 的骰子。
        return None
    flat, count, sides, bonus, kind = fail_rows[0]
    fail_formula = f"{int(count)}d{int(sides)}" + (f"+{int(bonus)}" if bonus else "")
    action_row: dict = {
        "id": "save-0", "name": str(action.get("name") or "Save action"),
        "kind": "save_action",
        "ability": _ABILITIES[head.group("ability").lower()],
        "dc": int(head.group("dc")),
        "on_fail": [{"damage": [{"dice": fail_formula, "type": kind.lower()}]}],
    }
    success = text.find("Success:")
    if success >= 0:
        tail = text[success:]
        success_rows = _SAVE_ROLL_RE.findall(tail)
        if len(success_rows) == 1:
            _f, s_count, s_sides, s_bonus, s_kind = success_rows[0]
            formula = f"{int(s_count)}d{int(s_sides)}" + (
                f"+{int(s_bonus)}" if s_bonus else ""
            )
            action_row["on_success"] = [{"damage": [{"dice": formula, "type": s_kind.lower()}]}]
        elif "Half damage" in tail:
            dice_count = int(count)
            if dice_count % 2 == 0:
                pool = {"dice": f"{dice_count // 2}d{int(sides)}", "type": kind.lower()}
            else:
                pool = {"dice": fail_formula, "type": kind.lower(), "half": True}
            action_row["on_success"] = [{"damage": [pool]}]
    # feed 的结构化字段才是权威：``RECHARGE_ON_ROLL`` 5 就是"掷出 5-6 恢复"。
    # 描述文本里的 "(Recharge 5-6)" 只是同一件事的另一种写法。
    structured = action.get("usage_limits")
    if isinstance(structured, dict) and structured.get("type"):
        kind_of_limit = str(structured["type"])
        param = int(structured.get("param") or 0)
        if kind_of_limit in {"RECHARGE_ON_ROLL", "RECHARGE"} and param:
            action_row["usage_limits"] = {"recharge": {"from": param, "to": 6}}
            return action_row
        if kind_of_limit == "PER_DAY" and param:
            action_row["usage_limits"] = {"per_day": param}
            return action_row
    usage = _USAGE_RE.search(text)
    per_day = _USAGE_PER_DAY_RE.search(text)
    if usage is not None:
        start = int(usage.group("from"))
        end = int(usage.group("to") or 6)
        action_row["usage_limits"] = {"recharge": {"from": start, "to": max(start, end)}}
    elif per_day is not None:
        action_row["usage_limits"] = {"per_day": int(per_day.group("count"))}
    return action_row


def map_actions(actions: list[dict], attacks: list[dict]) -> list[dict]:
    """The stat block's ``actions[]``: Multiattack and save-based actions."""

    produced: list[dict] = []
    for action in actions:
        if str(action.get("action_type") or "ACTION") != "ACTION":
            continue
        name = str(action.get("name") or "").strip().lower()
        if name == "multiattack":
            parsed = parse_multiattack(action, attacks)
            if parsed is not None:
                produced.append(parsed)
            continue
        parsed = parse_save_action(action)
        if parsed is not None:
            parsed["id"] = f"save-{len(produced)}"
            produced.append(parsed)
    return produced


_TRAIT_IDS = {
    "magic resistance": "magic_resistance",
    "amphibious": "amphibious",
    "legendary resistance": "legendary_resistance",
    "pack tactics": "pack_tactics",
    "spider climb": "spider_climb",
    "water breathing": "water_breathing",
    "flyby": "flyby",
    "amorphous": "amorphous",
}
_LEGENDARY_USES_RE = re.compile(r"\((?P<count>\d)/(?:Day|day)")
#: 描述里"还有一个效果没被读出来"的线索。只用于判定缺口，不参与映射。
_CLAIMS_A_RIDER = re.compile(
    r"the target is |the target has |and the target |knocked |grappled |restrained |prone",
    re.IGNORECASE,
)


def map_traits(record_traits: list[dict]) -> list[dict]:
    """The eight supported traits, by closed id.

    Every other trait in the feed is left out on purpose: a trait the engine
    cannot settle would load and then do nothing, which is worse than a trait
    the catalog never claimed.
    """

    produced: list[dict] = []
    seen: set[str] = set()
    for trait in record_traits or []:
        name = str(trait.get("name") or "").strip().lower()
        trait_id = _TRAIT_IDS.get(name)
        if trait_id is None or trait_id in seen:
            continue
        seen.add(trait_id)
        row: dict = {"id": trait_id}
        if trait_id == "legendary_resistance":
            uses = _LEGENDARY_USES_RE.search(str(trait.get("desc") or ""))
            row["params"] = {"per_day": int(uses.group("count")) if uses else 3}
        produced.append(row)
    return produced


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
    attacks = map_attacks(record.get("actions") or [])
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
        "attacks": attacks,
        "actions": map_actions(record.get("actions") or [], attacks),
        "traits": map_traits(record.get("traits") or []),
    }


def unmapped(record: dict) -> list[str]:
    """这个条目里**真的**被丢掉了的动作/特质名。

    与映射函数逐条对齐，而不是"再解析一遍"——两者一旦分叉，就会出现
    "标记为完整、实际丢了能力"。四条对齐点：

    * ``map_attacks`` / ``map_actions`` 只处理 ``ACTION``：Bonus/Reaction 动作
      会被整条跳过，所以它们一律是缺口；
    * 攻击上的 ``RECHARGE_ON_ROLL`` 不在攻击档案里（``map_attacks`` 不读
      ``usage_limits``），限次丢失；
    * 描述里有条件/推倒一类的 rider、而 ``parse_riders`` 什么都没读出来；
    * 已经由``attacks`` 结构化产出、只是 prose 解析不出来的动作**不算**缺口。
    """

    actions = record.get("actions") or []
    attacks = map_attacks(actions)
    produced = {str(attack.get("id") or "") for attack in attacks}
    dropped: list[str] = []
    for action in actions:
        name = str(action.get("name") or "")
        kind = str(action.get("action_type") or "ACTION").upper()
        if kind != "ACTION":
            # Bonus/Reaction 动作没有任何消费者：整条丢掉，必须记下来。
            dropped.append(f"{name} ({kind.replace(chr(95), chr(32)).title()})")
            continue
        if name.strip().lower() == "multiattack":
            if parse_multiattack(action, attacks) is None:
                dropped.append(name)
            continue
        parsed = parse_prose_attack(action, 0)
        if parsed is None and parse_save_action(action) is None:
            # 结构化攻击已产出时，prose 解析失败不是缺口。
            if not any(str(attack.get("name") or "") == name for attack in attacks):
                dropped.append(name)
            continue
        if parsed is not None:
            limits = action.get("usage_limits") or {}
            if str(limits.get("type") or "").upper() == "RECHARGE_ON_ROLL":
                dropped.append(f"{name} (Recharge)")
            desc = str(action.get("desc") or "")
            if not parse_riders(desc) and _CLAIMS_A_RIDER.search(desc):
                dropped.append(f"{name} (rider)")
    for trait in record.get("traits") or []:
        if not map_traits([trait]):
            dropped.append(str(trait.get("name") or ""))
    return sorted({name for name in dropped if name})


def main() -> int:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # open5e 拒绝没有 User-Agent 的请求（403），与内容无关。
    opener.addheaders = [("User-Agent", "diceframe-srd-import/1.0")]
    global CR_MAX
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cr", type=float, default=CR_MAX,
                        help="highest challenge rating to import (default 4)")
    CR_MAX = parser.parse_args().max_cr
    records = fetch_all(opener)
    monsters: list[dict] = []
    skipped: dict[str, list[str]] = {}
    incomplete: dict[str, list[str]] = {}
    for record in records:
        gaps = unmapped(record)
        entry = map_record(record)
        if gaps:
            # CR 0–1 是已交付档位：条目保留（删条目是独立决定），但缺口如实列出。
            # 更高的档位只收完整的，避免"库里有这只、它的招牌能力是空气"。
            if float(record.get("challenge_rating") or 0) <= DELIVERED_CR:
                incomplete[entry["id"]] = gaps
            else:
                skipped[entry["id"]] = gaps
                continue
        monsters.append(entry)
    monsters.sort(key=lambda row: (row["cr"], row["name"]))
    missing_attacks = [row["id"] for row in monsters if not row["attacks"]]
    payload = {
        "schema_version": 1,
        "kind": "monster_catalog",
        "id": "srd_monsters",
        "source_ref": "srd-5.2.1:p344-364:stat-blocks",
        # 目录是 bundle 实体，头字段缺失会让整个 ruleset 加载失败。
        "automation_level": "deterministic",
        "entries": monsters,
        # 目录级、不是条目级：条目要过 `validate_enemy_profiles`，而那是
        # `combat.start` 的同一个闭集边界。这两张表只给内容方查账用。
        "incomplete": dict(sorted(incomplete.items())),
        "skipped": dict(sorted(skipped.items())),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"{len(monsters)} creatures (CR <= {CR_MAX:g}) -> {OUTPUT}\n"
        f"  incomplete: {len(incomplete)}  skipped: {len(skipped)}"
    )
    if missing_attacks:
        print(f"no attacks parsed: {', '.join(missing_attacks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
