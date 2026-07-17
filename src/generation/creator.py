"""AI 生成器 —— 世界生成和角色生成的共用逻辑。"""

from __future__ import annotations

import json
import logging
from src.engine.character_utils import initial_special_stat_value, set_hp
from src.engine.language import DEFAULT_LANGUAGE, is_english, localized_field, normalize_language

logger = logging.getLogger("trpg")

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
     {{"id": "{world_prefix}_npc_1", "name": "NPC名", "type": "npc", "keywords": ["关键词"], "content": "条目描述", "tier": "core", "unreliable": false}},
     {{"id": "{world_prefix}_loc_1", "name": "地点名", "type": "location", "keywords": ["关键词"], "content": "条目描述", "tier": "core"}}
   ]
 }

 要求：
 - starter_lorebook包含3-5条初始条目（至少1个NPC、1个地点、1个事件）
 - id格式：{world_prefix}_npc_1、{world_prefix}_loc_1 等
 - tier设为"core"表示核心条目
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
     {{"id": "{world_prefix}_npc_1", "name": "NPC name", "type": "npc", "keywords": ["trigger keyword"], "content": "entry content in English", "tier": "core", "unreliable": false}},
     {{"id": "{world_prefix}_loc_1", "name": "Location name", "type": "location", "keywords": ["trigger keyword"], "content": "entry content in English", "tier": "core"}}
   ]
 }

Requirements:
- starter_lorebook must include 3-5 initial entries, including at least 1 NPC, 1 location, and 1 event.
- Use IDs like {world_prefix}_npc_1 and {world_prefix}_loc_1.
- Use tier "core" for central entries.
- All player-facing text must be natural English.
- Keep JSON keys and enum values exactly as specified."""

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

_LOREBOOK_ENTRIES_SYSTEM_PROMPT = """你是TRPG世界书编辑。用户会用自然语言描述世界观、势力、地点、人物、事件或谜题。

请把描述整理成可直接写入世界书的结构化条目。输出严格 JSON，不要包含 JSON 外文本：
{
  "entries": [
    {
      "name": "条目名",
      "type": "npc|location|item|event|puzzle|faction|other",
      "keywords": ["用于触发的关键词", "别名"],
      "content": "80-180字，说明这个条目对跑团叙事的作用、关系和可用细节",
      "tier": "core|background",
      "unreliable": false
    }
  ]
}

要求：
- 生成 3-8 条，数量根据用户信息密度决定。
- 至少覆盖用户明确提到的核心人物、地点、势力或事件。
- keywords 包含名称、简称、别称，避免空数组。
- type 只能使用上述枚举。
- tier 仅核心设定用 core，其余用 background。
- 不要编造压倒性神器或无解设定；内容应方便 GM 在剧情中调用。
- 所有文本使用中文。"""

_LOREBOOK_ENTRIES_SYSTEM_PROMPT_EN = """You are a TRPG lorebook editor. The user describes setting material, factions, locations, characters, events, or puzzles in natural language.

Convert the description into structured lorebook entries. Output strict JSON only, with no text outside JSON:
{
  "entries": [
    {
      "name": "entry name",
      "type": "npc|location|item|event|puzzle|faction|other",
      "keywords": ["trigger keyword", "alias"],
      "content": "80-180 words explaining how this entry matters to play, its relationships, and usable details",
      "tier": "core|background",
      "unreliable": false
    }
  ]
}

Requirements:
- Generate 3-8 entries depending on information density.
- Cover explicitly mentioned people, places, factions, or events.
- keywords must include names, short names, and aliases. Do not leave them empty.
- type must use only the listed enum values.
- Use core only for central setting material; use background for the rest.
- Do not invent overwhelming artifacts or unsolvable facts. Entries should be easy for the GM to use.
- All player-facing text must be natural English."""

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
- 所有中文文本自然、短而实用。"""

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
- Player-facing display text should be natural, concise English. Keep required JSON keys and enum values unchanged."""


def _localized_rule_text(value: dict | str | None, language: str, fallback: str = "") -> str:
    if isinstance(value, dict):
        return str(value.get("en") or value.get("zh") or fallback)
    return str(value or fallback)


def _build_character_prompt(rule, language: str = DEFAULT_LANGUAGE) -> str:
    """根据规则模板动态构造角色生成提示词。"""
    english = is_english(language)
    attrs_desc = "、".join(
        f"{localized_field(a, 'name', language) or a.get('name') or a.get('key')}({a['key']}, {a.get('min',3)}-{a.get('max',18)})"
        for a in rule.attributes
    ) if rule.attributes else ("none" if english else "无")
    attribute_keys = rule.attribute_keys if rule.attributes else ["str", "dex", "con", "int", "wis", "cha"]
    attr_keys = ", ".join(f'"{key}"' for key in attribute_keys)
    attrs_example = ", ".join(f'"{key}": 10' for key in attribute_keys)
    classes_desc = ", ".join(localized_field(c, "name", language) or c.get("name") or ("Adventurer" if english else "冒险者") for c in rule.classes) if rule.classes else ("Adventurer" if english else "冒险者")
    total_points = rule.attribute_points
    skill_pools = localized_field(rule.template, "skill_pools", language)
    if not isinstance(skill_pools, dict):
        skill_pools = rule.template.get("skill_pools", {})
    skills_desc = ", ".join(
        sorted(set(s for pool in skill_pools.values() for s in pool))
    ) if skill_pools else ("Perception, Basic Attack" if english else "侦查、基础攻击")
    ss_desc = ""
    for ss in rule.special_stats:
        name = localized_field(ss, "name", language) or ss.get("name") or ss["key"]
        ss_desc += f"\nSpecial stat: {name}({ss['key']}), max {ss.get('max', 99)}" if english else f"\n特殊属性: {name}({ss['key']}), 上限{ss.get('max', 99)}"

    if english:
        return f"""You are a TRPG character generator. Create a character sheet strictly following the current rule template.

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
- Do not accept overpowered species, powers, or concepts."""

    return f"""你是一个TRPG角色生成师。根据玩家描述，严格按照当前规则模板生成角色卡。

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
- 不接受超模种族或设定"""


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


async def generate_world(llm_client, prompt: str, rule_id: str = "freeform_fantasy",
                          worlds_dir=None, lorebook_store=None,
                          max_tokens: int = 2048,
                          language: str = DEFAULT_LANGUAGE) -> dict:
    """AI 生成世界模板，返回 {ok, world_id, world_name, description, starter_scene, lorebook_count}。"""
    language = normalize_language(language)
    system_template = _WORLD_SYSTEM_PROMPT_EN if is_english(language) else _WORLD_SYSTEM_PROMPT
    system = system_template.replace("{rule_id}", rule_id).replace("{world_prefix}", prompt.replace(" ", "_")[:12])
    user_message = (
        f"Create the following world setting:\n{prompt}\nRule: {rule_id}"
        if is_english(language)
        else f"创建以下世界观：{prompt}\n使用规则：{rule_id}"
    )

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
    world_prefix = world_id.replace("ai_", "")
    data["world_id"] = world_id
    data["language"] = language

    # 确保条目 ID 以世界前缀开头，防止跨世界冲突
    for entry in data.get("starter_lorebook", []):
        eid = entry.get("id", "")
        if not eid.startswith(world_prefix):
            entry["id"] = f"{world_prefix}_{eid}"

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
    英文模式保留全部（LLM 需参考英文字段生成英文规则）。
    """
    if is_english(language):
        return template
    return {k: v for k, v in template.items() if not k.endswith("_en")}


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
    user_prompt = (
        f"User genre description:\n{prompt}\n\n"
        f"Target rule_id: {rule_id}\n"
        f"Master rule ID: {source_rule_id}\n"
        f"Master rule JSON:\n{json.dumps(source_rule, ensure_ascii=False, indent=2)}"
        if is_english(language)
        else (
            f"用户题材描述：\n{prompt}\n\n"
            f"目标 rule_id：{rule_id}\n"
            f"母版规则ID：{source_rule_id}\n"
            f"母版规则 JSON：\n{json.dumps(source_rule, ensure_ascii=False, indent=2)}"
        )
    )
    data = await _call_json_with_repair(
        llm_client,
        system_prompt=_RULE_SYSTEM_PROMPT_EN if is_english(language) else _RULE_SYSTEM_PROMPT,
        user_message=user_prompt,
        temperature=0.55,
        max_tokens=max_tokens,
        label="规则生成",
    )
    if not data:
        return None
    data["rule_id"] = rule_id
    data["custom"] = True
    data["source_rule_id"] = source_rule_id
    data.setdefault("rule_version", "1.0-ai")
    data.setdefault("ruleset_level", "assisted")
    data.setdefault("dice_system", source_rule.get("dice_system", "d20"))
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
    existing = ("; ".join((existing_names or [])[:80]) if is_english(language) else "、".join((existing_names or [])[:80]))
    user_prompt = (
        f"Target lorebook: {world_name or 'Unnamed World'}\n"
        f"Existing entry names: {existing or 'None'}\n"
        f"User description:\n{prompt}"
        if is_english(language)
        else (
            f"目标世界书：{world_name or '未命名世界'}\n"
            f"已有条目名：{existing or '无'}\n"
            f"用户描述：\n{prompt}"
        )
    )
    data = await _call_json_with_repair(
        llm_client,
        system_prompt=_LOREBOOK_ENTRIES_SYSTEM_PROMPT_EN if is_english(language) else _LOREBOOK_ENTRIES_SYSTEM_PROMPT,
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
        system_prompt = _CHARACTER_SYSTEM_PROMPT_EN if is_english(language) else _CHARACTER_SYSTEM_PROMPT
        attr_keys = ["str", "dex", "con", "int", "wis", "cha"]
        attr_total = 60
    user_message = f"Create this character:\n{prompt}" if is_english(language) else f"创建以下角色：{prompt}"

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

    # 技能过滤禁用词
    banned = ("无敌", "全能", "必杀", "秒杀", "绝对", "不死", "造物", "掌控")
    raw_skills = data.get("skills", [])
    display_skills = [
        s for s in raw_skills
        if not any(b in (s if isinstance(s, str) else s.get("name", "")) for b in banned)
    ]
    data["skills"] = display_skills[:rule.max_skills if rule else 3]

    # 装备品质清洗
    for eq in data.get("equipment", []):
        if eq.get("quality") not in ("common",):
            eq["quality"] = "common"

    # 种族清洗
    banned_races = ("半神", "古龙", "天使", "恶魔领主", "神", "魔神", "龙神", "不死族")
    race = data.get("race", "")
    if any(b in race for b in banned_races):
        data["race"] = "人类"

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
            inst.players[uid] = {
                "character_name": data["character_name"],
                "character_sheet": data,
            }

    return data


def _parse_key(game_key: str) -> tuple:
    parts = game_key.split("|")
    if len(parts) >= 3:
        return tuple(parts[:3])
    return (game_key, "", "")
