"""AI 生成器 —— 世界生成和角色生成的共用逻辑。"""

from __future__ import annotations

import copy
import json
import logging
from src.engine.character_utils import initial_special_stat_value, set_hp
from src.engine.currency import CurrencySystemError, validate_currency_system
from src.engine.language import DEFAULT_LANGUAGE, localized_field, localized_text, normalize_language

logger = logging.getLogger("trpg")

# P2-L：AI 生成后验清洗的禁用词/种族（中英双语）。英文名需 lower() 后查英文禁词，
# 否则英文 "Invincible"/"Immortal" 等会绕过（prompt 双语禁了，后验只查中文是漏洞）。
_BANNED_SKILL_TERMS = (
    "无敌", "全能", "必杀", "秒杀", "绝对", "不死", "造物", "掌控",
    "invincible", "omnipotent", "instant kill", "absolute", "immortal",
    "creator", "god mode",
)
_BANNED_RACES = (
    "半神", "古龙", "天使", "恶魔领主", "神", "魔神", "龙神", "不死族",
    "demigod", "ancient dragon", "archangel", "demon lord", "god", "immortal",
)
_BANNED_RACES_LOWER = tuple(b.lower() for b in _BANNED_RACES)


def apply_generated_visibility(entry: dict) -> None:
    """把 AI 输出的 visibility 建议归一化成 visible_to。

    只认枚举值 "public"（全队可见，写 canonical 标记 "*"）；缺失、写错、
    或模型自由发挥一律 fail-closed 成 GM 秘密。点名到具体角色的分配是
    游戏内决策，不由生成时的模型代劳。entry 上的原始 visibility 字段
    一并消费掉，避免混进存储与回显。
    """
    public = str(entry.pop("visibility", "") or "").strip().lower() == "public"
    entry["visible_to"] = ["*"] if public else []

_WORLD_SYSTEM_PROMPT = """你是一个TRPG世界构建师。根据用户的一句话描述，生成一个完整的世界设定。

输出格式（严格JSON，不要包含任何JSON之外的文本）：
{
  "world_name": "世界名称(简洁有吸引力)",
  "description": "一句话简介",
  "world_setting": "世界观设定文本，200-300字，包含历史背景、主要势力、当前时代特征",
  "starter_scene": "开场场景描述，100-150字，简洁有力，给玩家明确的行动切入点",
  "suggested_difficulty": "标准",
  "default_rule": "{rule_id}",
   "starter_lorebook": [
     {{"id": "{world_prefix}_npc_1", "name": "NPC名", "type": "npc", "keywords": ["关键词"], "content": "条目描述", "tier": "core", "unreliable": false, "visibility": "public"}},
     {{"id": "{world_prefix}_loc_1", "name": "地点名", "type": "location", "keywords": ["关键词"], "content": "条目描述", "tier": "core", "visibility": "public"}}
   ]
 }

 要求：
 - starter_lorebook包含3-5条初始条目（至少1个NPC、1个地点、1个事件）
 - id格式：{world_prefix}_npc_1、{world_prefix}_loc_1 等
 - tier设为"core"表示核心条目
 - visibility 只有两个值："public"（玩家常识）与 "secret"（GM 秘密），拿不准就用 "secret"。
 - visibility="public" 是对整段 content 的整体授权：public 条目的全部内容都必须允许玩家无需调查直接知道。
 - public 条目中禁止混入隐藏动机、真实身份、剧情底牌、阴谋、尚未发现的线索、隐藏入口、未调查出的现场事实、未来揭示或任何 GM-only 信息。
 - 同一人物或地点同时有公开信息和秘密信息时，必须拆成两个独立条目：一条 public 只写公开信息，另一条 secret 只写秘密信息。
 - 只要整段里有任何一句不该让玩家立即知道，整条就必须是 secret。
 - 所有文本使用流畅中文"""

_WORLD_SYSTEM_PROMPT_EN = """You are a TRPG world builder. Generate a complete playable world setting from the user's short description.

Output format (strict JSON, no text outside JSON):
{
  "world_name": "A concise and appealing English world name",
  "description": "One-sentence English summary",
  "world_setting": "World setting in English, 180-260 words, including historical background, major factions, and the current era",
  "starter_scene": "Opening scene in English, 90-140 words, concise and actionable for players",
  "suggested_difficulty": "标准",
  "default_rule": "{rule_id}",
   "starter_lorebook": [
     {{"id": "{world_prefix}_npc_1", "name": "NPC name", "type": "npc", "keywords": ["trigger keyword"], "content": "entry content in English", "tier": "core", "unreliable": false, "visibility": "public"}},
     {{"id": "{world_prefix}_loc_1", "name": "Location name", "type": "location", "keywords": ["trigger keyword"], "content": "entry content in English", "tier": "core", "visibility": "public"}}
   ]
 }

Requirements:
- starter_lorebook must include 3-5 initial entries, including at least 1 NPC, 1 location, and 1 event.
- Use IDs like {world_prefix}_npc_1 and {world_prefix}_loc_1.
- Use tier "core" for central entries.
- visibility has exactly two values: "public" for common player knowledge and "secret" for GM-only material. When unsure, use "secret".
- visibility="public" authorizes the ENTIRE entry content: every part of a public entry must be knowable by players without investigation.
- Never mix hidden motives, secret identities, plot twists, conspiracies, undiscovered clues, hidden entrances, un-investigated on-scene facts, future reveals, or any GM-only information into a public entry.
- When one person or place has both public and secret information, split it into two separate entries: one public entry with only common knowledge, one secret entry with only the hidden information.
- If any sentence should not be known to players immediately, the whole entry must be secret.
- All player-facing text must be natural English.
- Keep JSON keys and enum values exactly as specified."""

_WORLD_SYSTEM_PROMPT_DE = """Du bist ein TRPG-Weltenbauer. Erzeuge aus der kurzen Beschreibung des Nutzers eine vollständige, spielbare Weltbeschreibung.

Ausgabeformat (striktes JSON, kein Text außerhalb des JSON):
{
  "world_name": "Ein prägnanter, ansprechender deutscher Weltname",
  "description": "Einsätzige deutsche Zusammenfassung",
  "world_setting": "Weltbeschreibung auf Deutsch, 180-260 Wörter, mit historischem Hintergrund, den wichtigsten Fraktionen und der aktuellen Epoche",
  "starter_scene": "Eröffnungsszene auf Deutsch, 90-140 Wörter, knapp und für Spieler sofort handlungsfähig",
  "suggested_difficulty": "标准",
  "default_rule": "{rule_id}",
   "starter_lorebook": [
     {{"id": "{world_prefix}_npc_1", "name": "NPC-Name", "type": "npc", "keywords": ["Auslöser-Schlüsselwort"], "content": "Eintragsinhalt auf Deutsch", "tier": "core", "unreliable": false, "visibility": "public"}},
     {{"id": "{world_prefix}_loc_1", "name": "Ortsname", "type": "location", "keywords": ["Auslöser-Schlüsselwort"], "content": "Eintragsinhalt auf Deutsch", "tier": "core", "visibility": "public"}}
   ]
 }

Anforderungen:
- starter_lorebook muss 3-5 Anfangseinträge enthalten, darunter mindestens 1 NSC, 1 Ort und 1 Ereignis.
- Verwende IDs wie {world_prefix}_npc_1 und {world_prefix}_loc_1.
- Verwende "core" für zentrale Einträge.
- visibility hat genau zwei Werte: "public" für allgemeines Spielerwissen und "secret" für reines GM-Material. Im Zweifel "secret" verwenden.
- visibility="public" gibt den GESAMTEN Eintragsinhalt frei: jeder Teil eines öffentlichen Eintrags muss den Spielern ohne Nachforschung bekannt sein dürfen.
- Vermische niemals verborgene Motive, geheime Identitäten, Wendungen, Verschwörungen, unentdeckte Hinweise, versteckte Eingänge, nicht untersuchte Tatortdetails, zukünftige Enthüllungen oder reine GM-Informationen in einen öffentlichen Eintrag.
- Wenn eine Person oder ein Ort sowohl öffentliche als auch geheime Informationen hat, teile sie in zwei getrennte Einträge auf: einen öffentlichen mit nur allgemeinem Wissen, einen geheimen mit nur den verborgenen Informationen.
- Wenn auch nur ein Satz nicht sofort den Spielern bekannt sein sollte, muss der gesamte Eintrag geheim sein.
- Der gesamte spielerseitige Text muss natürliches Deutsch sein.
- Behalte JSON-Schlüssel und Enum-Werte exakt wie vorgegeben bei."""

_CHARACTER_SYSTEM_PROMPT = """你是一个通用TRPG角色卡生成师。根据用户描述，生成一个适合任意题材的初始角色卡，不默认套用某个具体规则书或世界观。

输出格式（严格JSON，不要包含任何JSON之外的文本）：
{
  "character_name": "角色名",
  "race": "族群/身份",
  "class": "职业/定位",
  "level": 1,
  "attributes": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
  "hp": 50, "max_hp": 50,
  "skills": [{"name": "技能名", "value": 20}],
  "background": "背景故事，≤50字",
  "equipment": [{"name": "装备名", "type": "misc", "damage": 0, "slot": "pack", "quality": "common"}],
  "inventory": [{"name": "物品名", "qty": 1, "effect": "效果"}]
}

要求：
- 保持初始角色强度，属性总和约60，每项3-18。
- 技能 1-3 个，优先选择能体现题材和人物身份的技能。
- 装备仅使用 common 品质，避免稀有、传说、神器级物品。
- 特殊能力写入 background 或技能，不要做成无代价的压倒性能力。
- 禁止出现"无敌""全能""必杀""秒杀""绝对""不死""造物""掌控"等超模词。
- 如果用户描述属于调查、现代、赛博朋克、武侠、奇幻等题材，角色名、职业、技能和装备要贴合该题材。"""

_CHARACTER_SYSTEM_PROMPT_EN = """You are a general TRPG character sheet generator. Create a starting character that fits the user's description and genre without assuming a specific official ruleset.

Output strict JSON only, with no text outside JSON:
{
  "character_name": "Character name",
  "race": "heritage / identity",
  "class": "role / archetype",
  "level": 1,
  "attributes": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
  "hp": 50, "max_hp": 50,
  "skills": [{"name": "skill name", "value": 20}],
  "background": "short English background, under 50 words",
  "equipment": [{"name": "equipment name", "type": "misc", "damage": 0, "slot": "pack", "quality": "common"}],
  "inventory": [{"name": "item name", "qty": 1, "effect": "effect"}]
}

Requirements:
- Keep the character at starting power. Attribute total should be about 60, each value 3-18.
- Use 1-3 skills that reflect the genre and identity.
- Equipment must be common quality; avoid rare, legendary, artifact-level, or overwhelming items.
- Put special abilities in background or skills, not as cost-free dominant powers.
- Avoid overpowered words like invincible, omnipotent, instant kill, absolute, immortal, creator, or control-all.
- Names, roles, skills, equipment, and background must be natural English and match the requested genre."""

_CHARACTER_SYSTEM_PROMPT_DE = """Du bist ein allgemeiner TRPG-Charakterbogen-Generator. Erstelle eine Startfigur, die zur Beschreibung und zum Genre des Nutzers passt, ohne ein bestimmtes offizielles Regelwerk vorauszusetzen.

Gib ausschließlich striktes JSON aus, ohne Text außerhalb des JSON:
{
  "character_name": "Charaktername",
  "race": "Abstammung / Identität",
  "class": "Rolle / Archetyp",
  "level": 1,
  "attributes": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
  "hp": 50, "max_hp": 50,
  "skills": [{"name": "Fertigkeitsname", "value": 20}],
  "background": "kurzer deutscher Hintergrund, unter 50 Wörtern",
  "equipment": [{"name": "Ausrüstungsname", "type": "misc", "damage": 0, "slot": "pack", "quality": "common"}],
  "inventory": [{"name": "Gegenstandsname", "qty": 1, "effect": "Effekt"}]
}

Anforderungen:
- Halte die Figur auf Startniveau. Attributsumme sollte etwa 60 betragen, jeder Wert 3-18.
- Verwende 1-3 Fertigkeiten, die Genre und Identität widerspiegeln.
- Ausrüstung muss gewöhnliche Qualität haben; vermeide seltene, legendäre, artefaktartige oder überwältigende Gegenstände.
- Packe besondere Fähigkeiten in Hintergrund oder Fertigkeiten, nicht als kostenlose dominante Kräfte.
- Vermeide übermächtige Begriffe wie unbesiegbar, allmächtig, Instant-Kill, absolut, unsterblich, Schöpfer oder Alleskontrolleur.
- Namen, Rollen, Fertigkeiten, Ausrüstung und Hintergrund müssen natürliches Deutsch sein und zum gewünschten Genre passen."""

_LOREBOOK_ENTRIES_SYSTEM_PROMPT = """你是TRPG世界书编辑。用户会用自然语言描述世界观、势力、地点、人物、事件、谜题、法术或职业。

请把描述整理成可直接写入世界书的结构化条目。输出严格 JSON，不要包含 JSON 外文本：
{
  "entries": [
    {
      "name": "条目名",
      "type": "npc|location|item|event|puzzle|faction|spell|class|other",
      "keywords": ["用于触发的关键词", "别名"],
      "content": "80-180字，说明这个条目对跑团叙事的作用、关系和可用细节",
      "tier": "core|background",
      "unreliable": false,
      "visibility": "public"
    }
  ]
}

要求：
- 生成 3-8 条，数量根据用户信息密度决定。
- 至少覆盖用户明确提到的核心人物、地点、势力或事件。
- keywords 包含名称、简称、别称，避免空数组。
- type 只能使用上述枚举。
- tier 仅核心设定用 core，其余用 background。
- visibility 只有两个值："public"（玩家常识）与 "secret"（GM 秘密），拿不准就用 "secret"。
- visibility="public" 是对整段 content 的整体授权：public 条目的全部内容都必须允许玩家无需调查直接知道。
- public 条目中禁止混入隐藏动机、真实身份、剧情底牌、阴谋、尚未发现的线索、隐藏入口、未调查出的现场事实、未来揭示或任何 GM-only 信息。
- 同一人物或地点同时有公开信息和秘密信息时，必须拆成两个独立条目：一条 public 只写公开信息，另一条 secret 只写秘密信息。
- 只要整段里有任何一句不该让玩家立即知道，整条就必须是 secret。
- 不要编造压倒性神器或无解设定；内容应方便 GM 在剧情中调用。
- 所有文本使用中文。"""

_LOREBOOK_ENTRIES_SYSTEM_PROMPT_EN = """You are a TRPG lorebook editor. The user describes setting material, factions, locations, characters, events, puzzles, spells, or classes in natural language.

Convert the description into structured lorebook entries. Output strict JSON only, with no text outside JSON:
{
  "entries": [
    {
      "name": "entry name",
      "type": "npc|location|item|event|puzzle|faction|spell|class|other",
      "keywords": ["trigger keyword", "alias"],
      "content": "80-180 words explaining how this entry matters to play, its relationships, and usable details",
      "tier": "core|background",
      "unreliable": false,
      "visibility": "public"
    }
  ]
}

Requirements:
- Generate 3-8 entries depending on information density.
- Cover explicitly mentioned people, places, factions, or events.
- keywords must include names, short names, and aliases. Do not leave them empty.
- type must use only the listed enum values.
- Use core only for central setting material; use background for the rest.
- visibility has exactly two values: "public" for common player knowledge and "secret" for GM-only material. When unsure, use "secret".
- visibility="public" authorizes the ENTIRE entry content: every part of a public entry must be knowable by players without investigation.
- Never mix hidden motives, secret identities, plot twists, conspiracies, undiscovered clues, hidden entrances, un-investigated on-scene facts, future reveals, or any GM-only information into a public entry.
- When one person or place has both public and secret information, split it into two separate entries: one public entry with only common knowledge, one secret entry with only the hidden information.
- If any sentence should not be known to players immediately, the whole entry must be secret.
- Do not invent overwhelming artifacts or unsolvable facts. Entries should be easy for the GM to use.
- All player-facing text must be natural English."""

_LOREBOOK_ENTRIES_SYSTEM_PROMPT_DE = """Du bist ein TRPG-Lorebook-Redakteur. Der Nutzer beschreibt Weltmaterial, Fraktionen, Orte, Figuren, Ereignisse, Rätsel, Zauber oder Klassen in natürlicher Sprache.

Wandle die Beschreibung in strukturierte Lorebook-Einträge um. Gib ausschließlich striktes JSON aus, ohne Text außerhalb des JSON:
{
  "entries": [
    {
      "name": "Eintragsname",
      "type": "npc|location|item|event|puzzle|faction|spell|class|other",
      "keywords": ["Auslöser-Schlüsselwort", "Alias"],
      "content": "80-180 Wörter, die erklären, wie dieser Eintrag für das Spiel relevant ist, seine Beziehungen und nutzbare Details",
      "tier": "core|background",
      "unreliable": false,
      "visibility": "public"
    }
  ]
}

Anforderungen:
- Erzeuge 3-8 Einträge, je nach Informationsdichte.
- Decke ausdrücklich genannte Personen, Orte, Fraktionen oder Ereignisse ab.
- keywords müssen Namen, Kurznamen und Aliase enthalten. Lasse sie nicht leer.
- type darf nur die aufgeführten Enum-Werte verwenden.
- Verwende "core" nur für zentrales Setting-Material; verwende "background" für den Rest.
- visibility hat genau zwei Werte: "public" für allgemeines Spielerwissen und "secret" für reines GM-Material. Im Zweifel "secret" verwenden.
- visibility="public" gibt den GESAMTEN Eintragsinhalt frei: jeder Teil eines öffentlichen Eintrags muss den Spielern ohne Nachforschung bekannt sein dürfen.
- Vermische niemals verborgene Motive, geheime Identitäten, Wendungen, Verschwörungen, unentdeckte Hinweise, versteckte Eingänge, nicht untersuchte Tatortdetails, zukünftige Enthüllungen oder reine GM-Informationen in einen öffentlichen Eintrag.
- Wenn eine Person oder ein Ort sowohl öffentliche als auch geheime Informationen hat, teile sie in zwei getrennte Einträge auf: einen öffentlichen mit nur allgemeinem Wissen, einen geheimen mit nur den verborgenen Informationen.
- Wenn auch nur ein Satz nicht sofort den Spielern bekannt sein sollte, muss der gesamte Eintrag geheim sein.
- Erfinde keine überwältigenden Artefakte oder unlösbaren Fakten. Einträge sollten für den GM leicht nutzbar sein.
- Der gesamte spielerseitige Text muss natürliches Deutsch sein."""

_JSON_REPAIR_SYSTEM_PROMPT = """你是 JSON 修复器。用户会给你一段应该是 JSON 的模型输出。

你的任务：
- 只输出合法 JSON，不要解释。
- 尽量保留原字段、原语义和原文本。
- 修复常见问题：多余说明、Markdown 代码块、尾逗号、漏引号、中文标点、截断导致的括号未闭合。
- 如果某个数组或对象明显未闭合，请补齐闭合符号；不要编造大量新内容。
- 输出必须能被 JSON.parse/json.loads 直接解析。"""

_RULE_SYSTEM_PROMPT = """你是TRPG规则设计师。请基于给定“母版规则 JSON”和用户题材描述，生成一套可直接用于 DiceFrame 的轻量自定义规则。

输出严格 JSON，不要包含 JSON 外文本。必须保留 DiceFrame 兼容字段：
{
  "rule_id": "英文数字下划线短横线",
  "rule_name": "中文规则名",
  "rule_name_en": "English Name",
  "description": "一句话说明",
  "dice_system": "d20|d100|none",
  "combat_model": "hp_based|lethal_narrative|none",
  "mechanics": "机制代号",
  "ruleset_level": "assisted",
  "attributes": [{"key":"英文key","name":"中文名","min":3,"max":18}],
  "special_stats": [{"key":"英文key","name":"中文名","max":100,"description":"用途"}],
  "attribute_points": 60,
  "attr_hint": "给玩家的属性填写说明",
  "hp_formula": "5 + con * 3",
  "max_skills": 4,
  "skill_point_total": 220,
  "max_skill_value": 80,
  "skill_mode": "narrative",
  "skill_hint": "给玩家的技能填写说明",
  "currency": "货币名",
  "currency_system": {"schema_version":2,"base_unit":"fen","display_unit":"yuan","units":[{"id":"yuan","name":"人民币","symbol":"¥","rate":100},{"id":"fen","name":"分","rate":1}]},
  "classes": [{"name":"职业/身份","description":"定位","starter_equipment":["初始装备"]}],
  "skill_pools": {"职业/身份":["技能1","技能2"]},
  "item_categories": {"equipment":["物品"],"consumable":["消耗品"],"misc":["杂项"]},
  "gm_prompt_appendix": "给GM的规则与风格执行说明",
  "difficulty_instructions": {"轻松":"...","标准":"...","硬核":"..."}
}

要求：
- 这是轻量辅助规则，不要冒充任何官方规则书的完整RAW复刻。
- 如果用户提到具体作品，只提炼风味和结构，不复刻专有文本。
- 属性 key 必须是英文/数字/下划线，HP 公式只能使用属性 key、+ - * / //、min/max/abs/int。
- 属性数量建议 5-8 个；必须能支撑建卡。
- gm_prompt_appendix 要具体，能约束AI不串题材。
- gm_prompt_appendix 不得发明或要求使用新的大写协议标签；状态标签由 DiceFrame 的系统提示统一提供。
- 所有中文文本自然、短而实用。
- currency_system 可选：只有当题材明确存在更小货币单位时才生成（schema_version 固定为 2；base_unit 必须是 units 里 rate=1 的单位；所有 unit id 用英文小写下划线；rate 必须是正整数）。没有更小单位时省略整个字段，不要为凑字段而发明单位。"""

_RULE_SYSTEM_PROMPT_EN = """You are a TRPG rules designer. Based on the provided master rule JSON and the user's genre description, generate a lightweight custom rule JSON that can be used directly by DiceFrame.

Output strict JSON only. Preserve DiceFrame-compatible fields:
{
  "rule_id": "short English id with digits, underscores, or hyphens",
  "rule_name": "Chinese fallback rule name",
  "rule_name_en": "English rule name",
  "description": "one-sentence English description",
  "dice_system": "d20|d100|none",
  "combat_model": "hp_based|lethal_narrative|none",
  "mechanics": "mechanic code",
  "ruleset_level": "assisted",
  "attributes": [{"key":"english_key","name":"Chinese fallback","name_en":"English name","min":3,"max":18}],
  "special_stats": [{"key":"english_key","name":"Chinese fallback","name_en":"English name","max":100,"description":"English purpose"}],
  "attribute_points": 60,
  "attr_hint": "English attribute creation guidance",
  "hp_formula": "5 + con * 3",
  "max_skills": 4,
  "skill_point_total": 220,
  "max_skill_value": 80,
  "skill_mode": "narrative",
  "skill_hint": "English skill creation guidance",
  "currency": "Gold",
  "currency_system": {"schema_version":2,"base_unit":"cent","display_unit":"dollar","units":[{"id":"dollar","name":"Dollar","symbol":"$","rate":100},{"id":"cent","name":"Cent","rate":1}]},
  "classes": [{"name":"English role / identity","description":"English role description","starter_equipment":["starter equipment"]}],
  "skill_pools": {"English role / identity":["skill 1","skill 2"]},
  "item_categories": {"equipment":["item"],"consumable":["consumable"],"misc":["misc"]},
  "gm_prompt_appendix": "English GM execution notes for genre and rules",
  "difficulty_instructions": {"轻松":"English easy-mode guidance","标准":"English standard guidance","硬核":"English hard-mode guidance"}
}

Requirements:
- This is a lightweight assisted ruleset, not a full official RAW recreation.
- If the user references a specific work, extract genre flavor and structure without copying proprietary text.
- Attribute keys must use English letters, digits, or underscores.
- HP formula may only use attribute keys and + - * / // min max abs int.
- Use 5-8 attributes when possible and make character creation practical.
- gm_prompt_appendix must be concrete enough to keep AI on genre.
- gm_prompt_appendix must not invent or require new uppercase protocol tags; DiceFrame supplies state-tag instructions separately.
- Player-facing display text should be natural, concise English. Keep required JSON keys and enum values unchanged.
- currency_system is optional: emit it only when the genre clearly has a smaller denomination (schema_version fixed to 2; base_unit must be the units entry with rate 1; unit ids use lowercase English/underscores; rates are positive integers). Omit the whole field when there is no smaller unit; never invent units just to fill it."""

_RULE_SYSTEM_PROMPT_DE = """Du bist ein TRPG-Regeldesigner. Erzeuge basierend auf dem bereitgestellten Master-Regel-JSON und der Genre-Beschreibung des Nutzers ein leichtgewichtiges benutzerdefiniertes Regel-JSON, das direkt von DiceFrame verwendet werden kann.

Gib ausschließlich striktes JSON aus. Behalte DiceFrame-kompatible Felder bei:
{
  "rule_id": "kurze englische ID mit Ziffern, Unterstrichen oder Bindestrichen",
  "rule_name": "chinesischer Ausweichname der Regel",
  "rule_name_en": "English rule name",
  "description": "einsätzige deutsche Beschreibung",
  "dice_system": "d20|d100|none",
  "combat_model": "hp_based|lethal_narrative|none",
  "mechanics": "Mechanik-Code",
  "ruleset_level": "assisted",
  "attributes": [{"key":"englischer_key","name":"deutscher Name","name_en":"English name","min":3,"max":18}],
  "special_stats": [{"key":"englischer_key","name":"deutscher Name","name_en":"English name","max":100,"description":"deutscher Verwendungszweck"}],
  "attribute_points": 60,
  "attr_hint": "deutsche Anleitung zur Attributsvergabe",
  "hp_formula": "5 + con * 3",
  "max_skills": 4,
  "skill_point_total": 220,
  "max_skill_value": 80,
  "skill_mode": "narrative",
  "skill_hint": "deutsche Anleitung zur Fertigkeitsvergabe",
  "currency": "Gold",
  "currency_system": {"schema_version":2,"base_unit":"cent","display_unit":"dollar","units":[{"id":"dollar","name":"Dollar","symbol":"$","rate":100},{"id":"cent","name":"Cent","rate":1}]},
  "classes": [{"name":"deutsche Rolle / Identität","description":"deutsche Rollenbeschreibung","starter_equipment":["Startausrüstung"]}],
  "skill_pools": {"deutsche Rolle / Identität":["Fertigkeit 1","Fertigkeit 2"]},
  "item_categories": {"equipment":["Gegenstand"],"consumable":["Verbrauchsgegenstand"],"misc":["Sonstiges"]},
  "gm_prompt_appendix": "deutsche GM-Ausführungshinweise zu Genre und Regeln",
  "difficulty_instructions": {"轻松":"deutsche Anleitung für den leichten Modus","标准":"deutsche Anleitung für den Standardmodus","硬核":"deutsche Anleitung für den harten Modus"}
}

Anforderungen:
- Dies ist ein leichtgewichtiges Hilfsregelwerk, keine vollständige offizielle RAW-Nachbildung.
- Wenn der Nutzer ein bestimmtes Werk erwähnt, extrahiere nur Stimmung und Struktur, ohne proprietären Text zu kopieren.
- Attribut-Keys müssen englische Buchstaben, Ziffern oder Unterstriche verwenden.
- Die HP-Formel darf nur Attribut-Keys sowie + - * / // min max abs int verwenden.
- Verwende möglichst 5-8 Attribute und mache die Charaktererstellung praktikabel.
- gm_prompt_appendix muss konkret genug sein, um die KI im Genre zu halten.
- gm_prompt_appendix darf keine neuen großgeschriebenen Protokoll-Tags erfinden oder verlangen; DiceFrame liefert Status-Tag-Anweisungen separat.
- Der spielerseitige Anzeigetext sollte natürliches, prägnantes Deutsch sein. Behalte erforderliche JSON-Schlüssel und Enum-Werte unverändert bei.
- currency_system ist optional: gib es nur an, wenn das Genre klar eine kleinere Währungseinheit hat (schema_version fest auf 2; base_unit muss der units-Eintrag mit rate 1 sein; unit-ids in Kleinbuchstaben/Unterstrich; rates sind positive ganze Zahlen). Lasse das Feld weg, wenn es keine kleinere Einheit gibt; erfinde keine Einheiten nur zum Ausfüllen."""


def _localized_rule_text(value: dict | str | None, language: str, fallback: str = "") -> str:
    if isinstance(value, dict):
        return str(value.get("en") or value.get("zh") or fallback)
    return str(value or fallback)


def _build_character_prompt(rule, language: str = DEFAULT_LANGUAGE) -> str:
    """根据规则模板动态构造角色生成提示词。"""
    attrs_desc = "、".join(
        f"{localized_field(a, 'name', language) or a.get('name') or a.get('key')}({a['key']}, {a.get('min',3)}-{a.get('max',18)})"
        for a in rule.attributes
    ) if rule.attributes else localized_text(language, {"en": "none", "zh-CN": "无", "ja": "なし", "de": "keine"})
    attribute_keys = rule.attribute_keys if rule.attributes else ["str", "dex", "con", "int", "wis", "cha"]
    attr_keys = ", ".join(f'"{key}"' for key in attribute_keys)
    attrs_example = ", ".join(f'"{key}": 10' for key in attribute_keys)
    classes_desc = ", ".join(localized_field(c, "name", language) or c.get("name") or localized_text(language, {"en": "Adventurer", "zh-CN": "冒险者", "ja": "冒険者", "de": "Abenteurer"}) for c in rule.classes) if rule.classes else localized_text(language, {"en": "Adventurer", "zh-CN": "冒险者", "ja": "冒険者", "de": "Abenteurer"})
    total_points = rule.attribute_points
    skill_pools = rule.skill_pools
    if not isinstance(skill_pools, dict):
        skill_pools = rule.template.get("skill_pools", {})
    skills_desc = ", ".join(
        sorted(set(s for pool in skill_pools.values() for s in pool))
    ) if skill_pools else localized_text(language, {"en": "Perception, Basic Attack", "zh-CN": "侦查、基础攻击", "ja": "知覚、基本攻撃", "de": "Wahrnehmung, Grundangriff"})
    ss_desc = ""
    for ss in rule.special_stats:
        name = localized_field(ss, "name", language) or ss.get("name") or ss["key"]
        ss_desc += localized_text(language, {
            "en": f"\nSpecial stat: {name}({ss['key']}), max {ss.get('max', 99)}",
            "zh-CN": f"\n特殊属性: {name}({ss['key']}), 上限{ss.get('max', 99)}",
            "ja": f"\n特殊ステータス: {name}({ss['key']}), 上限{ss.get('max', 99)}",
            "de": f"\nSpezialwert: {name}({ss['key']}), Maximum {ss.get('max', 99)}",
        })

    return localized_text(language, {
        "en": f"""You are a TRPG character generator. Create a character sheet strictly following the current rule template.

Rule: {getattr(rule, 'rule_name_en', '') or rule.rule_name}
Attributes ({total_points} points total): {attrs_desc}
Attribute keys: {{{attr_keys}}}
Available roles: {classes_desc}
Available skills: {skills_desc}{ss_desc}

Output strict JSON only:
{{
  "character_name": "Character name",
  "race": "Origin",
  "class": "Role",
  "level": 1,
  "attributes": {{{attrs_example}}},
  "hp": 50, "max_hp": 50,
  "skills": [{{"name": "skill name", "value": 20}}],
  "background": "English background, under 50 words",
  "equipment": [{{"name": "equipment name", "type": "weapon", "damage": 6, "slot": "main_hand", "quality": "common"}}],
  "inventory": [{{"name": "item name", "qty": 1, "effect": "effect"}}]
}}

Requirements:
- Allocate {total_points} attribute points and keep every value within its rule range.
- Prefer the available role list, but custom genre-appropriate role names are allowed.
- Choose {rule.max_skills} skills; each skill must include name and value.
- Keep background concise, under 50 words.
- Equipment must be common quality.
- Do not accept overpowered species, powers, or concepts.""",
        "zh-CN": f"""你是一个TRPG角色生成师。根据玩家描述，严格按照当前规则模板生成角色卡。

规则: {rule.rule_name}
属性系统（共{total_points}点分配）: {attrs_desc}
属性键: {{{attr_keys}}}
可选职业: {classes_desc}
可选技能: {skills_desc}{ss_desc}

输出格式（严格JSON，不要包含任何JSON之外的文本）：
{{{{
  "character_name": "角色名",
  "race": "种族",
  "class": "职业",
  "level": 1,
  "attributes": {{{attrs_example}}},
  "hp": 50, "max_hp": 50,
  "skills": [{{{{"name": "技能名", "value": 数值}}}}],
  "background": "背景故事，≤50字",
  "equipment": [{{{{"name": "装备名", "type": "weapon", "damage": 6, "slot": "main_hand", "quality": "common"}}}}],
  "inventory": [{{{{"name": "物品名", "qty": 1, "effect": "效果"}}}}]
}}}}

要求:
- {total_points}点属性，每项在规则范围内
- 优先从以上职业列表中选择；如果题材不匹配，允许自定义贴合题材的职业名
- 技能选{rule.max_skills}个，每个包含name和value字段，value取规则基础值
- 背景简洁≤50字
- 装备仅common品质
- 不接受超模种族或设定""",
        "de": f"""Du bist ein TRPG-Charaktergenerator. Erstelle einen Charakterbogen streng nach der aktuellen Regelvorlage.

Regel: {getattr(rule, 'rule_name_en', '') or rule.rule_name}
Attribute (insgesamt {total_points} Punkte): {attrs_desc}
Attribut-Keys: {{{attr_keys}}}
Verfügbare Rollen: {classes_desc}
Verfügbare Fertigkeiten: {skills_desc}{ss_desc}

Gib ausschließlich striktes JSON aus:
{{{{
  "character_name": "Charaktername",
  "race": "Herkunft",
  "class": "Rolle",
  "level": 1,
  "attributes": {{{attrs_example}}},
  "hp": 50, "max_hp": 50,
  "skills": [{{{{"name": "Fertigkeitsname", "value": 20}}}}],
  "background": "deutscher Hintergrund, unter 50 Wörtern",
  "equipment": [{{{{"name": "Ausrüstungsname", "type": "weapon", "damage": 6, "slot": "main_hand", "quality": "common"}}}}],
  "inventory": [{{{{"name": "Gegenstandsname", "qty": 1, "effect": "Effekt"}}}}]
}}}}

Anforderungen:
- Verteile {total_points} Attributpunkte und halte jeden Wert im Regelbereich.
- Bevorzuge die verfügbare Rollenliste, aber genre-passende eigene Rollennamen sind erlaubt.
- Wähle {rule.max_skills} Fertigkeiten; jede Fertigkeit braucht name und value.
- Halte den Hintergrund knapp, unter 50 Wörtern.
- Ausrüstung muss gewöhnliche Qualität haben.
- Akzeptiere keine übermächtigen Spezies, Kräfte oder Konzepte.""",
        "ja": f"""あなたは TRPG のキャラクター生成器です。プレイヤーの説明に従い、現在のルールテンプレートに厳密に従ってキャラクターシートを生成してください。

ルール: {rule.rule_name}
属性システム（合計{total_points}点）: {attrs_desc}
属性キー: {{{attr_keys}}}
選択可能な職業: {classes_desc}
選択可能なスキル: {skills_desc}{ss_desc}

出力形式（厳密なJSONのみ、JSON以外のテキストを出力しない）：
{{{{
  "character_name": "キャラクター名",
  "race": "種族",
  "class": "職業",
  "level": 1,
  "attributes": {{{attrs_example}}},
  "hp": 50, "max_hp": 50,
  "skills": [{{{{"name": "スキル名", "value": 数値}}}}],
  "background": "背景（50文字以内）",
  "equipment": [{{{{"name": "装備名", "type": "weapon", "damage": 6, "slot": "main_hand", "quality": "common"}}}}],
  "inventory": [{{{{"name": "アイテム名", "qty": 1, "effect": "効果"}}}}]
}}}}

要件:
- {total_points}点の属性を、それぞれルールの範囲内で割り当てる
- 可能な限り上記の職業リストから選ぶ。ジャンルに合わない場合は、ジャンルに合う職業名を独自に定義してよい
- スキルを{rule.max_skills}個選ぶ。各スキルは name と value フィールドを含み、value はルールの基本値を使う
- 背景は簡潔に、50文字以内
- 装備は common 品質のみ
- 過剰な種族・設定を受け入れない""",
    })


def parse_json(content: str) -> dict | None:
    """从文本中提取 JSON，支持裸 JSON、```json 块、括号计数、自动修复。"""
    if not content or not content.strip():
        return None

    from src.llm.parser import _find_balanced_json, _repair_json
    text = content.strip()

    # 1. 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.debug("parse_json 直接解析失败: %s (pos=%d)", e, e.pos)

    # 2. 尝试 ```json ``` 块
    for marker in ("```json", "```"):
        parts = text.split(marker)
        if len(parts) > 1:
            candidate = parts[-1].split("```")[0].strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    return json.loads(_repair_json(candidate))
                except json.JSONDecodeError:
                    pass

    # 3. 括号计数截取
    balanced = _find_balanced_json(text)
    if balanced:
        try:
            return json.loads(balanced)
        except json.JSONDecodeError:
            try:
                return json.loads(_repair_json(balanced))
            except json.JSONDecodeError:
                pass

    # 4. 全量修复
    try:
        return json.loads(_repair_json(text))
    except json.JSONDecodeError:
        pass

    logger.warning("parse_json 所有策略均失败，原始内容长度=%d", len(text))
    return None


async def _call_json_with_repair(
    llm_client,
    *,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    label: str,
) -> dict | None:
    """调用 LLM 获取 JSON，并在解析失败时追加一次 JSON 修复重试。"""
    response = await llm_client.call(
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )
    data = parse_json(response.content)
    if data is not None:
        return data

    raw = response.content or ""
    logger.warning("%s JSON 解析失败，尝试修复重试，原始返回(前300字): %s", label, raw[:300])
    if not raw.strip():
        return None
    repair = await llm_client.call(
        system_prompt=_JSON_REPAIR_SYSTEM_PROMPT,
        user_message=f"请修复以下 JSON 输出，只返回修复后的 JSON：\n\n{raw}",
        temperature=0.0,
        max_tokens=max_tokens,
        json_mode=True,
    )
    data = parse_json(repair.content)
    if data is None:
        logger.warning("%s JSON 修复重试仍失败，修复返回(前300字): %s", label, (repair.content or "")[:300])
    return data


def _unique_world_id(world_id: str, worlds_dir) -> str:
    """同名世界已存在时加数字后缀，避免覆盖前一次生成的存档（P3-F）。"""
    from pathlib import Path
    if not worlds_dir:
        return world_id
    base = world_id
    candidate = world_id
    suffix = 2
    while (Path(worlds_dir) / f"{candidate}.json").exists():
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


async def generate_world(llm_client, prompt: str, rule_id: str = "freeform_fantasy",
                          worlds_dir=None, lorebook_store=None,
                          max_tokens: int = 2048,
                          language: str = DEFAULT_LANGUAGE) -> dict:
    """AI 生成世界模板，返回 {ok, world_id, world_name, description, starter_scene, lorebook_count}。"""
    language = normalize_language(language)
    system_template = localized_text(language, {
        "en": _WORLD_SYSTEM_PROMPT_EN,
        "zh-CN": _WORLD_SYSTEM_PROMPT,
        "de": _WORLD_SYSTEM_PROMPT_DE,
        "ja": """あなたは TRPG のワールドビルダーです。ユーザーの短い説明から、そのまま遊べる完全な世界設定を生成してください。

出力形式（厳密なJSONのみ。JSON 以外のテキストを出力しない）：
{
  "world_name": "簡潔で魅力的な日本語の世界名",
  "description": "1文の日本語の要約",
  "world_setting": "日本語の世界設定。180〜260文字。歴史的背景・主要勢力・現在の時代の特徴を含む",
  "starter_scene": "日本語の導入シーン。90〜140文字。プレイヤーに明確な行動の入り口を与える",
  "suggested_difficulty": "标准",
  "default_rule": "{rule_id}",
   "starter_lorebook": [
     {{"id": "{world_prefix}_npc_1", "name": "NPC名", "type": "npc", "keywords": ["トリガーキーワード"], "content": "日本語のエントリ内容", "tier": "core", "unreliable": false, "visibility": "public"}},
     {{"id": "{world_prefix}_loc_1", "name": "場所名", "type": "location", "keywords": ["トリガーキーワード"], "content": "日本語のエントリ内容", "tier": "core", "visibility": "public"}}
   ]
 }

要件：
- starter_lorebook には 3〜5 件の初期エントリを含める（NPC 1件・場所 1件・イベント 1件以上）。
- ID は {world_prefix}_npc_1、{world_prefix}_loc_1 のような形式にする。
- 中心となるエントリの tier は "core" にする。
- visibility は 2 値のみ："public"（プレイヤーの常識）と "secret"（GM 秘密）。迷ったら secret。
- visibility="public" はエントリ content 全体への許可です：public エントリの内容は、調査なしにプレイヤーが知っていてよい情報だけにする。
- public エントリに隠された動機・正体・伏線・陰謀・未発見の手がかり・隠し入口・未調査の現場事実・未来の暴露・GM 専用情報を混ぜてはいけない。
- 同じ人物や場所に公開情報と秘密情報が両方ある場合は、2 つの独立したエントリに分割する：1 つは公開情報のみの public、もう 1 つは隠された情報のみの secret。
- ひとつでも即座に知られてはならない文があるなら、エントリ全体を secret にする。
- プレイヤー向けのテキストはすべて自然な日本語にする。
- JSON のキーと enum 値は指定どおりに保つ。""",
    })
    system = system_template.replace("{rule_id}", rule_id).replace("{world_prefix}", prompt.replace(" ", "_")[:12])
    user_message = localized_text(language, {
        "en": f"Create the following world setting:\n{prompt}\nRule: {rule_id}",
        "zh-CN": f"创建以下世界观：{prompt}\n使用规则：{rule_id}",
        "ja": f"以下の世界設定を作成してください：\n{prompt}\n使用ルール：{rule_id}",
        "de": f"Erstelle die folgende Weltbeschreibung:\n{prompt}\nRegel: {rule_id}",
    })

    data = await _call_json_with_repair(
        llm_client,
        system_prompt=system,
        user_message=user_message,
        temperature=0.7,
        max_tokens=max_tokens,
        label="世界生成",
    )
    if not data:
        return {"ok": False, "error": "AI 返回内容解析失败，请重试"}

    world_id = "ai_" + data.get("world_name", prompt[:8]).replace(" ", "_")
    world_id = _unique_world_id(world_id, worlds_dir)
    world_prefix = world_id.replace("ai_", "")
    data["world_id"] = world_id
    data["language"] = language
    data["custom"] = True

    # 确保条目 ID 以世界前缀开头，防止跨世界冲突
    for entry in data.get("starter_lorebook", []):
        eid = entry.get("id", "")
        if not eid.startswith(world_prefix):
            entry["id"] = f"{world_prefix}_{eid}"
        # visibility 建议先归一化，落盘的世界模板 JSON 同步保留 visible_to
        apply_generated_visibility(entry)

    if worlds_dir:
        worlds_dir.mkdir(parents=True, exist_ok=True)
        (worlds_dir / f"{world_id}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    if lorebook_store:
        if not lorebook_store.get_world(world_id):
            lorebook_store.create_world(world_id, data.get("world_name", world_id),
                                        description=data.get("description", ""))
            for entry in data.get("starter_lorebook", []):
                entry["world_id"] = world_id
                lorebook_store.add_entry(entry)

    return {
        "ok": True, "world_id": world_id, "world_name": data.get("world_name", ""),
        "language": language,
        "description": data.get("description", ""), "starter_scene": data.get("starter_scene", ""),
        "lorebook_count": len(data.get("starter_lorebook", [])),
    }


def _master_template_for_prompt(template: dict, language: str) -> dict:
    """生成规则 prompt 时按语言剔除另一语言的字段，减少 token。

    中文模式剔除 *_en 后缀字段（如 gm_prompt_appendix_en、skill_pools_en）；
    其余模式（en/ja/de）保留全部——非中文规则 prompt 都要求 LLM 参考英文
    字段（如 rule_name_en）生成对应输出，剔除会让那些字段变成无源引用。
    """
    if normalize_language(language) != "zh-CN":
        return template
    return {k: v for k, v in template.items() if not k.endswith("_en")}


_GENERATED_DE_TOP_LEVEL_FIELDS = (
    "rule_name",
    "description",
    "attr_hint",
    "skill_hint",
    "gm_prompt_appendix",
    "difficulty_instructions",
    "currency",
    "skill_pools",
    "item_categories",
)
_GENERATED_DE_NESTED_COLLECTIONS = ("attributes", "classes", "special_stats")


def _materialize_generated_de_fields(data: dict, language: str) -> None:
    """德语 AI 生成规则的本地化字段物化（#277 followup）。

    de 生成 prompt 把德语文本写在 canonical 字段（name/description 等），而
    localized_field(..., "de") 在 name_de 缺失时会先命中 *_en，导致生成的德语
    规则再次用于德语建卡/prompt 时显示英语。此处把德语文本复制进 *_de 字段
    （仅缺省时，deepcopy 防共享引用），统一字段协议；不修改 localized_field
    的全局回退顺序。非德语生成（en/zh/ja）直接原样返回，不添加 *_de 字段。
    """
    if normalize_language(language) != "de":
        return
    for key in _GENERATED_DE_TOP_LEVEL_FIELDS:
        if key in data and f"{key}_de" not in data:
            data[f"{key}_de"] = copy.deepcopy(data[key])
    for collection in _GENERATED_DE_NESTED_COLLECTIONS:
        for item in data.get(collection) or []:
            if not isinstance(item, dict):
                continue
            for key in ("name", "description"):
                if key in item and f"{key}_de" not in item:
                    item[f"{key}_de"] = item[key]


async def generate_rule(
    llm_client,
    prompt: str,
    *,
    source_rule: dict,
    source_rule_id: str,
    rule_id: str,
    max_tokens: int = 4096,
    language: str = DEFAULT_LANGUAGE,
) -> dict | None:
    """基于母版规则和题材描述生成自定义规则 JSON。"""
    language = normalize_language(language)
    source_rule = _master_template_for_prompt(source_rule, language)
    user_prompt = localized_text(language, {
        "en": (
            f"User genre description:\n{prompt}\n\n"
            f"Target rule_id: {rule_id}\n"
            f"Master rule ID: {source_rule_id}\n"
            f"Master rule JSON:\n{json.dumps(source_rule, ensure_ascii=False, indent=2)}"
        ),
        "zh-CN": (
            f"用户题材描述：\n{prompt}\n\n"
            f"目标 rule_id：{rule_id}\n"
            f"母版规则ID：{source_rule_id}\n"
            f"母版规则 JSON：\n{json.dumps(source_rule, ensure_ascii=False, indent=2)}"
        ),
        "ja": (
            f"ユーザーのジャンル説明：\n{prompt}\n\n"
            f"対象 rule_id：{rule_id}\n"
            f"マスタールール ID：{source_rule_id}\n"
            f"マスタールール JSON：\n{json.dumps(source_rule, ensure_ascii=False, indent=2)}"
        ),
        "de": (
            f"Genre-Beschreibung des Nutzers:\n{prompt}\n\n"
            f"Ziel-rule_id: {rule_id}\n"
            f"Master-Regel-ID: {source_rule_id}\n"
            f"Master-Regel-JSON:\n{json.dumps(source_rule, ensure_ascii=False, indent=2)}"
        ),
    })
    data = await _call_json_with_repair(
        llm_client,
        system_prompt=localized_text(language, {
            "en": _RULE_SYSTEM_PROMPT_EN,
            "zh-CN": _RULE_SYSTEM_PROMPT,
            "de": _RULE_SYSTEM_PROMPT_DE,
            "ja": """あなたは TRPG のルールデザイナーです。指定されたマスタールール JSON とユーザーのジャンル説明に基づいて、DiceFrame でそのまま使える軽量なカスタムルール JSON を生成してください。

厳密な JSON のみを出力してください。DiceFrame 互換のフィールドを保持すること：
{
  "rule_id": "英数字・アンダースコア・ハイフンの短いID",
  "rule_name": "日本語のフォールバックルール名",
  "rule_name_en": "English rule name",
  "description": "1文の日本語の説明",
  "dice_system": "d20|d100|none",
  "combat_model": "hp_based|lethal_narrative|none",
  "mechanics": "メカニクスコード",
  "ruleset_level": "assisted",
  "attributes": [{"key":"english_key","name":"日本語フォールバック名","name_en":"English name","min":3,"max":18}],
  "special_stats": [{"key":"english_key","name":"日本語フォールバック名","name_en":"English name","max":100,"description":"日本語の用途"}],
  "attribute_points": 60,
  "attr_hint": "日本語の属性入力ガイド",
  "hp_formula": "5 + con * 3",
  "max_skills": 4,
  "skill_point_total": 220,
  "max_skill_value": 80,
  "skill_mode": "narrative",
  "skill_hint": "日本語のスキル入力ガイド",
  "currency": "Gold",
  "currency_system": {"schema_version":2,"base_unit":"cent","display_unit":"dollar","units":[{"id":"dollar","name":"Dollar","symbol":"$","rate":100},{"id":"cent","name":"Cent","rate":1}]},
  "classes": [{"name":"日本語の職業 / 出自","description":"日本語の職業説明","starter_equipment":["初期装備"]}],
  "skill_pools": {"日本語の職業 / 出自":["スキル1","スキル2"]},
  "item_categories": {"equipment":["アイテム"],"consumable":["消耗品"],"misc":["雑貨"]},
  "gm_prompt_appendix": "ジャンルとルールの運用を日本語で記したGM向け指示",
  "difficulty_instructions": {"轻松":"日本語のイージーモード指針","标准":"日本語のスタンダード指針","硬核":"日本語のハードモード指針"}
}

要件：
- これは軽量な補助ルールセットであり、既存の公式ルールブックの完全な RAW の再現ではない。
- 特定の作品に言及する場合は、ジャンルの風味と構造だけを抽出し、独自テキストを複製しない。
- 属性キーは英字・数字・アンダースコアのみを使う。
- HP 式は属性キーと + - * / // min max abs int のみ使用できる。
- 可能なら属性は 5〜8 個にし、キャラクター作成が実用的であること。
- gm_prompt_appendix はジャンルを逸脱させないよう十分具体的に書く。
- gm_prompt_appendix で新しい大文字のプロトコルタグを創作・要求しない。状態タグの指示は DiceFrame のシステムプロンプトが別途提供する。
- プレイヤー向け表示テキストは自然で簡潔な日本語にする。必須の JSON キーと enum 値は変更しない。
- currency_system は任意：ジャンルに明確な補助通貨単位がある場合のみ出力する（schema_version は 2 固定；base_unit は units の中で rate が 1 の単位；unit id は英小文字とアンダースコア；rate は正の整数）。補助単位がなければフィールド全体を省略し、埋め合わせに単位を捏造しない。""",
        }),
        user_message=user_prompt,
        temperature=0.55,
        max_tokens=max_tokens,
        label="规则生成",
    )
    if not data:
        return None
    _materialize_generated_de_fields(data, language)
    # AI 输出的 V2 货币声明必须服务端校验，坏 schema 不允许落盘（fail closed，
    # 不做语义猜测修复）；模型未输出时优先继承母版规则的 currency_system，
    # 母版也没有则不生成字段，运行时按 legacy rate=1 兼容，不按名称猜单位。
    generated_system = data.get("currency_system")
    if generated_system is not None:
        try:
            validate_currency_system(generated_system)
        except CurrencySystemError as exc:
            raise ValueError(f"AI 生成规则 currency_system 非法: {exc}") from None
    elif isinstance(source_rule.get("currency_system"), dict):
        data["currency_system"] = copy.deepcopy(source_rule["currency_system"])
    data["rule_id"] = rule_id
    data["custom"] = True
    data["source_rule_id"] = source_rule_id
    data.setdefault("rule_version", "1.0-ai")
    data.setdefault("ruleset_level", "assisted")
    data.setdefault("dice_system", source_rule.get("dice_system", "d20"))
    if str(data["dice_system"]).lower() == "d20":
        data.setdefault("max_check_dc", source_rule.get("max_check_dc", 20))
        data.setdefault("check_mechanic", source_rule.get("check_mechanic", {
            "dice": "d20",
            "comparison": "roll_plus_modifier_gte_target",
            "critical": {"success": 20, "failure": 1},
        }))
    data.setdefault("combat_model", source_rule.get("combat_model", "hp_based"))
    data.setdefault("mechanics", "ai_custom_lite")
    data.setdefault("attributes", source_rule.get("attributes", []))
    data.setdefault("attribute_points", source_rule.get("attribute_points", 60))
    data.setdefault("hp_formula", source_rule.get("hp_formula", "5 + con * 3"))
    data.setdefault("max_skills", source_rule.get("max_skills", 4))
    data.setdefault("skill_point_total", source_rule.get("skill_point_total", 200))
    data.setdefault("max_skill_value", source_rule.get("max_skill_value", 80))
    data.setdefault("skill_mode", source_rule.get("skill_mode", "narrative"))
    data.setdefault("currency", source_rule.get("currency", "金币"))
    data.setdefault("gm_prompt_appendix", "")
    return data


async def generate_lorebook_entries(
    llm_client,
    prompt: str,
    *,
    world_name: str = "",
    existing_names: list[str] | None = None,
    max_tokens: int = 2048,
    language: str = DEFAULT_LANGUAGE,
) -> list[dict] | None:
    """根据自然语言生成世界书条目列表。"""
    language = normalize_language(language)
    existing = localized_text(language, {
        "en": "; ".join((existing_names or [])[:80]),
        "zh-CN": "、".join((existing_names or [])[:80]),
        "ja": "、".join((existing_names or [])[:80]),
        "de": "; ".join((existing_names or [])[:80]),
    })
    user_prompt = localized_text(language, {
        "en": (
            f"Target lorebook: {world_name or 'Unnamed World'}\n"
            f"Existing entry names: {existing or 'None'}\n"
            f"User description:\n{prompt}"
        ),
        "zh-CN": (
            f"目标世界书：{world_name or '未命名世界'}\n"
            f"已有条目名：{existing or '无'}\n"
            f"用户描述：\n{prompt}"
        ),
        "ja": (
            f"対象ロアブック：{world_name or '名前のない世界'}\n"
            f"既存エントリ名：{existing or 'なし'}\n"
            f"ユーザー説明：\n{prompt}"
        ),
        "de": (
            f"Ziel-Lorebook: {world_name or 'Unbenannte Welt'}\n"
            f"Vorhandene Eintragsnamen: {existing or 'Keine'}\n"
            f"Beschreibung des Nutzers:\n{prompt}"
        ),
    })
    data = await _call_json_with_repair(
        llm_client,
        system_prompt=localized_text(language, {
            "en": _LOREBOOK_ENTRIES_SYSTEM_PROMPT_EN,
            "zh-CN": _LOREBOOK_ENTRIES_SYSTEM_PROMPT,
            "de": _LOREBOOK_ENTRIES_SYSTEM_PROMPT_DE,
            "ja": """あなたは TRPG のロアブック編集者です。ユーザーは設定資料・勢力・場所・人物・出来事・謎・呪文・職業を自然言語で説明します。

その説明を構造化されたロアブックエントリに変換してください。厳密な JSON のみを出力し、JSON 以外のテキストを出力しない：
{
  "entries": [
    {
      "name": "エントリ名",
      "type": "npc|location|item|event|puzzle|faction|spell|class|other",
      "keywords": ["トリガーキーワード", "別名"],
      "content": "このエントリがプレイにどう関わるか・関連性・使える詳細を説明した80〜180文字",
      "tier": "core|background",
      "unreliable": false,
      "visibility": "public"
    }
  ]
}

要件：
- 情報の密度に応じて 3〜8 件を生成する。
- 明示的に言及された人物・場所・勢力・出来事は必ず含める。
- keywords には名前・略称・別名を含め、空配列にしない。
- type は列挙された値のみを使う。
- core は中心設定のみに使い、それ以外は background にする。
- visibility は 2 値のみ："public"（プレイヤーの常識）と "secret"（GM 秘密）。迷ったら secret。
- visibility="public" はエントリ content 全体への許可です：public エントリの内容は、調査なしにプレイヤーが知っていてよい情報だけにする。
- public エントリに隠された動機・正体・伏線・陰謀・未発見の手がかり・隠し入口・未調査の現場事実・未来の暴露・GM 専用情報を混ぜてはいけない。
- 同じ人物や場所に公開情報と秘密情報が両方ある場合は、2 つの独立したエントリに分割する：1 つは公開情報のみの public、もう 1 つは隠された情報のみの secret。
- ひとつでも即座に知られてはならない文があるなら、エントリ全体を secret にする。
- 圧倒的なアーティファクトや解けない設定を創作しない。GM がシナリオで使いやすい内容にする。
- プレイヤー向けのテキストはすべて自然な日本語にする。""",
        }),
        user_message=user_prompt,
        temperature=0.7,
        max_tokens=max_tokens,
        label="世界书条目生成",
    )
    if not data or not isinstance(data.get("entries"), list):
        return None
    return data["entries"]


async def generate_character(llm_client, prompt: str, game_key: str = "",
                               registry=None, rule=None,
                               max_tokens: int = 2048,
                               language: str = DEFAULT_LANGUAGE) -> dict | None:
    """AI 生成角色卡，返回角色卡 dict 或 None。

    Args:
        llm_client: LLM 客户端
        prompt: 用户描述
        game_key: 游戏 key（可选，用于直接加入游戏）
        registry: 游戏注册表（可选）
        rule: RuleSystem 实例（可选，用于生成规则适配的角色）
    """
    language = normalize_language(language)
    if rule:
        system_prompt = _build_character_prompt(rule, language)
        attr_keys = rule.attribute_keys
        attr_total = rule.attribute_points
    else:
        system_prompt = localized_text(language, {
            "en": _CHARACTER_SYSTEM_PROMPT_EN,
            "zh-CN": _CHARACTER_SYSTEM_PROMPT,
            "de": _CHARACTER_SYSTEM_PROMPT_DE,
            "ja": """あなたは汎用 TRPG のキャラクターシート生成器です。特定の公式ルールブックを前提とせず、ユーザーの説明とジャンルに合う初期キャラクターを作成してください。

厳密な JSON のみを出力し、JSON 以外のテキストを出力しない：
{
  "character_name": "キャラクター名",
  "race": "種族 / 出自",
  "class": "職業 / 立ち位置",
  "level": 1,
  "attributes": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
  "hp": 50, "max_hp": 50,
  "skills": [{"name": "スキル名", "value": 20}],
  "background": "短い日本語の背景、50文字以内",
  "equipment": [{"name": "装備名", "type": "misc", "damage": 0, "slot": "pack", "quality": "common"}],
  "inventory": [{"name": "アイテム名", "qty": 1, "effect": "効果"}]
}

要件：
- 初期レベルの強さを保つ。属性合計は約60、各値は3〜18。
- ジャンルと出自に合うスキルを1〜3個選ぶ。
- 装備は common 品質のみ。レア・レジェンド・アーティファクト級・圧倒的なアイテムは避ける。
- 特殊能力は background か skills に書き、無償の支配的能力にはしない。
- invincible, omnipotent, instant kill, absolute, immortal, creator, control-all のような過剰な語は避ける。
- 名前・職業・スキル・装備・背景は自然な日本語で、指定されたジャンルに合わせる。""",
        })
        attr_keys = ["str", "dex", "con", "int", "wis", "cha"]
        attr_total = 60
    user_message = localized_text(language, {
        "en": f"Create this character:\n{prompt}",
        "zh-CN": f"创建以下角色：{prompt}",
        "ja": f"このキャラクターを作成してください：\n{prompt}",
        "de": f"Erstelle diese Figur:\n{prompt}",
    })

    response = await llm_client.call(
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=0.8, max_tokens=max_tokens,
        json_mode=True,
    )
    data = parse_json(response.content)
    # 偶发输出截断：json_mode 下部分供应商会返回不完整 JSON，去掉 json_mode 重试一次
    if data is None and response.content and not response.content.rstrip().endswith("}"):
        logger.warning("角色生成输出疑似被截断（%d 字符），去掉 json_mode 重试一次", len(response.content))
        response = await llm_client.call(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.8, max_tokens=max_tokens,
            json_mode=False,
        )
        data = parse_json(response.content)
    if data is None and response.content:
        logger.warning("角色生成 JSON 解析失败，尝试修复重试，原始返回(前300字): %s", response.content[:300])
        repair = await llm_client.call(
            system_prompt=_JSON_REPAIR_SYSTEM_PROMPT,
            user_message=f"请修复以下 JSON 输出，只返回修复后的 JSON：\n\n{response.content}",
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=True,
        )
        data = parse_json(repair.content)
    if not data:
        logger.warning("角色生成 JSON 解析失败，原始返回(前300字): %s", response.content[:300])
        try:
            import json as _json
            _json.loads(response.content.strip())
        except Exception as _e:
            logger.warning("直接 json.loads 同样失败: %s", _e)
        return None

    # 确保字段完整
    data.setdefault("level", 1)
    data.setdefault("xp", 0)
    data.setdefault("deceased", False)
    for k in attr_keys:
        data.setdefault("attributes", {}).setdefault(k, 10)
    hp = data.get("hp", 50)
    set_hp(data, hp, max(hp, data.get("max_hp", hp)))
    data.setdefault("skills", [])
    data.setdefault("equipment", [])
    data.setdefault("inventory", [])
    data.setdefault("key_items", [])
    data.setdefault("background", "")

    # 后验校验：属性点总和超限则等比压缩
    attrs = data.get("attributes", {})
    for k in attr_keys:
        attrs.setdefault(k, 10)
    total = sum(attrs.get(k, 10) for k in attr_keys)
    if total > attr_total:
        scale = attr_total / total
        for k in attr_keys:
            attrs[k] = max(3, min(18, round(attrs[k] * scale)))
    data["attributes"] = attrs

    if rule:
        hp = rule.calculate_hp(attrs, data.get("class", ""))
        set_hp(data, hp, hp)
    else:
        # HP 按职业封顶
        cls = (data.get("class", "") or "").lower()
        max_hp = 65 if any(k in cls for k in ("战", "圣")) else 50 if any(k in cls for k in ("法", "术")) else 60
        hp = min(data.get("hp", 50), max_hp)
        set_hp(data, hp, max(hp, data.get("max_hp", hp)))

    # 技能过滤禁用词（P2-L：双语；英文名 lower 后查英文禁词，防英文绕过）
    raw_skills = data.get("skills", [])

    def _skill_text(skill_obj):
        return skill_obj if isinstance(skill_obj, str) else skill_obj.get("name", "")

    display_skills = [
        s for s in raw_skills
        if not any(b.lower() in _skill_text(s).lower() for b in _BANNED_SKILL_TERMS)
    ]
    data["skills"] = display_skills[:rule.max_skills if rule else 3]

    # 装备品质清洗
    for eq in data.get("equipment", []):
        if eq.get("quality") not in ("common",):
            eq["quality"] = "common"

    # 种族清洗（P2-L：双语；英文名同样拦截）
    race = (data.get("race", "") or "").lower()
    if any(b in race for b in _BANNED_RACES_LOWER):
        data["race"] = localized_text(language, {"en": "Human", "zh-CN": "人类", "ja": "人間", "de": "Mensch"})

    # 初始化 special_stats
    if rule:
        for ss in rule.special_stats:
            max_val = ss.get("max", 99)
            init_val = initial_special_stat_value(ss, attrs)
            data[ss["key"]] = init_val
            data[f"max_{ss['key']}"] = max_val

    # 如果提供了 game_key 和 registry，直接加入游戏
    if game_key and registry:
        import time
        inst = registry.get(game_key if isinstance(game_key, tuple) else _parse_key(game_key))
        if inst:
            uid = "ai_gen_" + str(int(time.time()))
            inst.put_player(uid, {
                "character_name": data["character_name"],
                "character_sheet": data,
            })

    return data


def _parse_key(game_key: str) -> tuple:
    parts = game_key.split("|")
    if len(parts) >= 3:
        return tuple(parts[:3])
    return (game_key, "", "")
