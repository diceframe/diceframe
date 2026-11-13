"""FU 规则运行时（``runtime`` / ``conflict`` / ``content_store``）的文案表。

约定见 :mod:`src.rulesets.fabula_ultima.i18n`：

- 每条文案同时提供 ``zh-CN`` 与 ``en``，两种语言的占位符必须同名；
- 一条完整的句子对应一个 key，不按片段拆分；
- key 是稳定标识，渲染后的文字不参与判断、不当持久化 ID；
- 技能名、职业名、状态 id、伤害类型 id、物品 / 怪物名等来自数据表的 canonical 值
  通过占位符原样带入，不在这里翻译。

``MESSAGES`` 只允许用字面量 key 取用（测试会扫描源码，保证没有缺失或闲置的
key）；需要按 canonical id 动态查显示名的，放在文件末尾的几张 ``*_LABELS`` 表里。
"""

from __future__ import annotations

MESSAGES: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------------ 通用
    "sentence_separator": {"zh-CN": "；", "en": "; "},
    "list_separator": {"zh-CN": "、", "en": ", "},

    # ------------------------------------------------------------------ 物品目录
    "item_effect_weapon": {"zh-CN": "武器 · 伤害 {damage}", "en": "Weapon · damage {damage}"},
    "item_effect_armor": {"zh-CN": "护甲 · 物防 {armor}", "en": "Armor · Defense {armor}"},
    "item_potion_name": {"zh-CN": "治疗药水", "en": "Healing Potion"},
    "item_potion_description": {"zh-CN": "恢复 20 HP。", "en": "Restores 20 HP."},
    "item_antidote_name": {"zh-CN": "万能药", "en": "Remedy"},
    "item_antidote_description": {"zh-CN": "解除异常状态。", "en": "Removes status effects."},
    "item_bomb_name": {"zh-CN": "炸弹", "en": "Bomb"},
    "item_bomb_description": {"zh-CN": "造成 10 点物理伤害。", "en": "Deals 10 physical damage."},

    # ------------------------------------------------------------------ 冒险节点
    "loot_party_ip": {
        "zh-CN": "战利品：全队各获得 {amount} 物资点",
        "en": "Loot: each party member gains {amount} IP",
    },
    "loot_item": {"zh-CN": "战利品：{name} ×{quantity}", "en": "Loot: {name} ×{quantity}"},
    "adventure_node_completed": {
        "zh-CN": "冒险节点完成：{node_id}",
        "en": "Adventure node completed: {node_id}",
    },
    "adventure_node_completed_next": {
        "zh-CN": "冒险节点完成：{node_id} → 新节点 {nodes}",
        "en": "Adventure node completed: {node_id} → new nodes: {nodes}",
    },
    "adventure_prompt_heading": {
        "zh-CN": "## 冒险节点（每轮必须判断，不能跳过）",
        "en": "## Adventure nodes (judge them every round; do not skip)",
    },
    "adventure_prompt_active": {"zh-CN": "当前激活节点：", "en": "Currently active nodes:"},
    "adventure_prompt_node": {
        "zh-CN": "- id={node_id}｜{name}｜type={node_type}｜{description}",
        "en": "- id={node_id} | {name} | type={node_type} | {description}",
    },
    "adventure_prompt_judge": {
        "zh-CN": "本轮叙事写完后，你必须逐条判断：该节点的目标是否已经在剧情里**明确达成**。",
        "en": (
            "After writing this round's narration, you must judge each node in turn: "
            "has its goal been **clearly achieved** in the story?"
        ),
    },
    "adventure_prompt_complete": {
        "zh-CN": (
            "若已达成，必须在状态标签区（--- 之后）追加一行："
            "ADVENTURE_COMPLETE:{{\"format_version\":1,\"node_id\":\"<上面某个 id>\","
            "\"reason\":\"<一句话说明怎么达成的>\",\"evidence_rounds\":[{round_number}]}}"
        ),
        "en": (
            "If it has, you must append one line in the status tag area (after ---): "
            "ADVENTURE_COMPLETE:{{\"format_version\":1,\"node_id\":\"<one of the ids above>\","
            "\"reason\":\"<one sentence on how it was achieved>\",\"evidence_rounds\":[{round_number}]}}"
        ),
    },
    "adventure_prompt_incomplete": {
        "zh-CN": (
            "若未达成，不要输出该标签，也不要为了推进而强行判定。"
            "该标签只是给玩家的完成建议，不会自动推进；玩家确认后才完成节点。"
        ),
        "en": (
            "If it has not, do not output the tag, and do not force a verdict just to move things along. "
            "The tag is only a completion suggestion for the players and advances nothing by itself; "
            "the node is completed only after a player confirms."
        ),
    },

    # ------------------------------------------------------------------ 建卡
    "char_attributes_not_object": {"zh-CN": "属性必须是对象", "en": "Attributes must be an object"},
    "char_attributes_missing": {"zh-CN": "缺少属性：{names}", "en": "Missing attributes: {names}"},
    "char_attribute_not_number": {
        "zh-CN": "属性 {key} 不是数字",
        "en": "Attribute {key} is not a number",
    },
    "char_attribute_bad_die": {
        "zh-CN": "属性 {key} 必须是 d6/d8/d10/d12",
        "en": "Attribute {key} must be d6/d8/d10/d12",
    },
    "char_attribute_total": {
        "zh-CN": "属性总和必须是 {expected}（当前 {total}）",
        "en": "Attribute dice must total {expected} (currently {total})",
    },
    "char_class_required": {"zh-CN": "必须选择职业", "en": "A class must be chosen"},
    "char_class_unknown": {"zh-CN": "未知职业：{names}", "en": "Unknown classes: {names}"},
    "char_class_two": {"zh-CN": "必须选择两个职业", "en": "Exactly two classes must be chosen"},
    "char_class_levels_total": {
        "zh-CN": "职业等级总和必须是 {expected}",
        "en": "Class levels must total {expected}",
    },
    "char_class_level_min": {"zh-CN": "每个职业至少 1 级", "en": "Each class needs at least 1 level"},
    "char_skills_not_list": {"zh-CN": "技能必须是列表", "en": "Skills must be a list"},
    "char_skills_max": {"zh-CN": "技能最多 {limit} 个", "en": "At most {limit} skills are allowed"},
    "char_skill_level_invalid": {
        "zh-CN": "技能 {name} 等级无效",
        "en": "Skill {name} has an invalid level",
    },
    "char_skill_level_range": {
        "zh-CN": "技能 {name} 等级必须在 1–{maximum}",
        "en": "Skill {name} must have a level of 1–{maximum}",
    },
    "char_skill_points_max": {
        "zh-CN": "技能总点数不能超过 {limit}",
        "en": "Total skill points cannot exceed {limit}",
    },
    "currency_label": {"zh-CN": "泽尼特", "en": "Zenit"},
    "resource_hp": {"zh-CN": "生命", "en": "HP"},
    "resource_mp": {"zh-CN": "精神值", "en": "MP"},
    "resource_ip": {"zh-CN": "物资点", "en": "IP"},
    "resource_fabula": {"zh-CN": "物语点", "en": "Fabula Points"},

    # ------------------------------------------------------------------ 意图入口
    "intent_conflict_end": {"zh-CN": "结束冲突", "en": "End conflict"},
    "intent_encounter_start": {"zh-CN": "开始遭遇：{name}", "en": "Start encounter: {name}"},
    "intent_choice": {"zh-CN": "选择：{label}", "en": "Choose: {label}"},
    "intent_node_complete": {"zh-CN": "完成节点：{name}", "en": "Complete node: {name}"},
    "intent_conflict_start": {"zh-CN": "开始冲突", "en": "Start conflict"},
    "intent_rest": {"zh-CN": "休息", "en": "Rest"},
    "intent_fabula_spend": {"zh-CN": "消耗物语点", "en": "Spend a Fabula Point"},
    "intent_class_levelup": {"zh-CN": "提升职业等级", "en": "Level up a class"},
    "gm_only": {"zh-CN": "只有 GM 可以发起冲突", "en": "Only the GM can start a conflict"},
    "conflict_already_active": {"zh-CN": "冲突已在进行中", "en": "A conflict is already in progress"},
    "no_conflict": {"zh-CN": "当前没有冲突", "en": "There is no active conflict"},
    "no_villain": {"zh-CN": "没有这个反派", "en": "There is no such villain"},
    "unknown_intent": {"zh-CN": "未知意图：{intent_type}", "en": "Unknown intent: {intent_type}"},

    # ------------------------------------------------------------------ 冲突开始
    "conflict_no_characters": {
        "zh-CN": "没有可参与冲突的角色",
        "en": "No character can take part in the conflict",
    },
    "conflict_manual_enemy_limit": {
        "zh-CN": "手动遭遇最多选择 5 只敌人",
        "en": "A manual encounter can have at most 5 enemies",
    },
    "conflict_started_log": {
        "zh-CN": "冲突开始：先攻 {rolls} + {bonus} = {total} vs DL {dc} → {first_side}",
        "en": "Conflict started: initiative {rolls} + {bonus} = {total} vs DL {dc} → {first_side}",
    },
    "initiative_players_first": {
        "zh-CN": "先攻 {roll_a}+{roll_b}（+{bonus}）= {total} vs DL {dc}，玩家方先行动",
        "en": "Initiative {roll_a}+{roll_b} (+{bonus}) = {total} vs DL {dc}: the players act first",
    },
    "initiative_enemies_first": {
        "zh-CN": "先攻 {roll_a}+{roll_b}（+{bonus}）= {total} vs DL {dc}，敌方先行动",
        "en": "Initiative {roll_a}+{roll_b} (+{bonus}) = {total} vs DL {dc}: the enemies act first",
    },
    "default_attack_name": {"zh-CN": "攻击", "en": "Attack"},
    "default_clock_name": {"zh-CN": "目标", "en": "Objective"},

    # ------------------------------------------------------------------ 敌方行动
    "enemy_skill_buff": {
        "zh-CN": "{enemy} 使用「{skill}」，获得增益",
        "en": "{enemy} uses \"{skill}\" and gains a buff",
    },
    "enemy_skill_roll": {
        "zh-CN": "{enemy} 使用「{skill}」：{roll_a}+{roll_b}={total}",
        "en": "{enemy} uses \"{skill}\": {roll_a}+{roll_b}={total}",
    },
    "defense_miss": {"zh-CN": "{target} 物防 {dc} → 未命中", "en": "{target} Defense {dc} → miss"},
    "magic_defense_miss": {
        "zh-CN": "{target} 魔防 {dc} → 未命中",
        "en": "{target} Magic Defense {dc} → miss",
    },
    "defense_any_miss": {
        "zh-CN": "{target} 物防/魔防 {dc} → 未命中",
        "en": "{target} Defense/Magic Defense {dc} → miss",
    },
    "defense_any_hit": {
        "zh-CN": "{target} 物防/魔防 {dc} → 命中",
        "en": "{target} Defense/Magic Defense {dc} → hit",
    },
    "target_takes_damage": {"zh-CN": "{target} 受到 {damage} 伤害", "en": "{target} takes {damage} damage"},
    "target_status": {"zh-CN": "{target} 陷入{status}", "en": "{target} becomes {status}"},
    "target_status_duration": {
        "zh-CN": "{target} 陷入{status}（{duration} 回合）",
        "en": "{target} becomes {status} ({duration} rounds)",
    },
    "intercept_log": {
        "zh-CN": "{target} 使用{skill}，消耗 {cost} MP 使攻击失效",
        "en": "{target} uses {skill}, spending {cost} MP to negate the attack",
    },
    "intercept_summary": {
        "zh-CN": "{target} {skill}：本次攻击自动失败",
        "en": "{target}'s {skill}: this attack fails automatically",
    },
    "defend_reduction": {"zh-CN": "防御姿态减伤 2", "en": "Defensive stance: damage reduced by 2"},
    "cover_halved": {
        "zh-CN": "{target} 受到掩护，伤害减半",
        "en": "{target} is covered: damage halved",
    },
    "enemy_attack_hit_log": {
        "zh-CN": "{enemy} 攻击 {target}：{rolls} = {total} vs 物防 {dc}，伤害 {damage}",
        "en": "{enemy} attacks {target}: {rolls} = {total} vs Defense {dc}, damage {damage}",
    },
    "enemy_attack_hit": {
        "zh-CN": "{enemy} 命中 {target}，造成 {damage} 伤害",
        "en": "{enemy} hits {target} for {damage} damage",
    },
    "counterattack": {
        "zh-CN": "{target} 反击，对 {enemy} 造成 {damage} 伤害",
        "en": "{target} counterattacks, dealing {damage} damage to {enemy}",
    },
    "enemy_attack_miss_log": {
        "zh-CN": "{enemy} 攻击 {target}：{rolls} = {total} vs 物防 {dc}，未命中",
        "en": "{enemy} attacks {target}: {rolls} = {total} vs Defense {dc}, miss",
    },
    "enemy_attack_miss": {"zh-CN": "{enemy} 未命中 {target}", "en": "{enemy} misses {target}"},
    "enemy_skill_missing": {
        "zh-CN": "怪物没有这个技能",
        "en": "The monster does not have that skill",
    },
    "enemy_skill_cooldown": {
        "zh-CN": "「{skill}」还在冷却",
        "en": "\"{skill}\" is still on cooldown",
    },
    "enemy_defend_log": {
        "zh-CN": "{enemy} 进入防御姿态",
        "en": "{enemy} takes a defensive stance",
    },
    "enemy_defend": {"zh-CN": "{enemy} 防御", "en": "{enemy} defends"},
    "enemy_hinder_log": {
        "zh-CN": "{enemy} 妨碍 {target}，施加动摇",
        "en": "{enemy} hinders {target}, inflicting Shaken",
    },
    "enemy_hinder": {
        "zh-CN": "{enemy} 妨碍目标，施加动摇",
        "en": "{enemy} hinders the target, inflicting Shaken",
    },
    "enemy_objective_clock": {
        "zh-CN": "{enemy} 推进目标命刻",
        "en": "{enemy} advances the objective Clock",
    },
    "enemy_objective": {"zh-CN": "{enemy} 推进目标", "en": "{enemy} advances the objective"},
    "enemy_investigate_log": {"zh-CN": "{enemy} 进行调查", "en": "{enemy} investigates the scene"},
    "enemy_investigate": {"zh-CN": "{enemy} 调查", "en": "{enemy} investigates"},
    "enemy_action_unavailable": {
        "zh-CN": "该怪物动作暂不可用",
        "en": "That monster action is not available yet",
    },

    # ------------------------------------------------------------------ 行动者编排
    "actor_not_specified": {"zh-CN": "未指定行动者", "en": "No actor was specified"},
    "current_actor": {"zh-CN": "当前行动者：{actor}", "en": "Current actor: {actor}"},
    "current_actor_not_enemy": {
        "zh-CN": "当前行动者不是敌人",
        "en": "The current actor is not an enemy",
    },
    "enemy_cannot_act": {"zh-CN": "敌人已无法行动", "en": "That enemy can no longer act"},
    "unit_cannot_act": {"zh-CN": "该单位已无法行动", "en": "That unit can no longer act"},
    "ai_no_action": {
        "zh-CN": "当前角色没有可执行的 AI 行动",
        "en": "The current character has no AI action available",
    },
    "no_current_actor": {"zh-CN": "当前没有行动者", "en": "There is no current actor"},
    "gm_select_actor_first": {
        "zh-CN": "请先由 GM 选择当前行动者",
        "en": "The GM must select the current actor first",
    },
    "not_current_actor": {
        "zh-CN": "当前行动者是 {current}，不能替其他单位行动",
        "en": "The current actor is {current}; you cannot act for another unit",
    },
    "not_your_turn": {
        "zh-CN": "还没轮到 {actor} 行动（当前是 {current}）",
        "en": "It is not {actor}'s turn yet (current actor: {current})",
    },
    "unknown_action": {"zh-CN": "未知行动：{action}", "en": "Unknown action: {action}"},
    "quick_action_used": {
        "zh-CN": "本回合已经用过快速行动",
        "en": "The quick action has already been used this turn",
    },
    "main_action_used": {
        "zh-CN": "本回合已经用过主动行动",
        "en": "The main action has already been used this turn",
    },
    "quick_action_log": {
        "zh-CN": "{actor} 使用快速行动，回合继续",
        "en": "{actor} uses a quick action; the turn continues",
    },
    "quick_action_summary": {
        "zh-CN": "快速行动已使用，仍可执行主动行动",
        "en": "Quick action used; a main action is still available",
    },

    # ------------------------------------------------------------------ 重投
    "reroll_nothing": {
        "zh-CN": "没有可以重投的冲突行动",
        "en": "There is no conflict action to reroll",
    },
    "reroll_player_only": {"zh-CN": "只能重投玩家行动", "en": "Only player actions can be rerolled"},
    "reroll_own_only": {"zh-CN": "只能重投自己的行动", "en": "You can only reroll your own action"},
    "reroll_spent": {"zh-CN": "消耗 1 点物语点重投", "en": "Spent 1 Fabula Point to reroll"},
    "fabula_short": {"zh-CN": "物语点不足", "en": "Not enough Fabula Points"},

    # ------------------------------------------------------------------ 玩家行动
    "attack_target_invalid": {"zh-CN": "攻击目标无效", "en": "Invalid attack target"},
    "opportunity_bonus": {"zh-CN": "机会加成 +{bonus}", "en": "Opportunity bonus +{bonus}"},
    "attack_roll": {
        "zh-CN": "{attr_a}+{attr_b} {roll_a}+{roll_b}={total} vs 物防 {dc} → {verdict}",
        "en": "{attr_a}+{attr_b} {roll_a}+{roll_b}={total} vs Defense {dc} → {verdict}",
    },
    "attack_log": {
        "zh-CN": "攻击 {enemy}：{rolls} = {total} vs 物防 {dc} → {verdict}",
        "en": "Attack on {enemy}: {rolls} = {total} vs Defense {dc} → {verdict}",
    },
    "enemy_absorbed": {"zh-CN": "{enemy} 吸收了这次攻击", "en": "{enemy} absorbed the attack"},
    "attack_hit": {"zh-CN": "命中 {enemy}，伤害 {damage}", "en": "Hit {enemy} for {damage} damage"},
    "attack_hit_affinity": {
        "zh-CN": "命中 {enemy}，伤害 {damage}（{affinity}）",
        "en": "Hit {enemy} for {damage} damage ({affinity})",
    },
    "defend_cover": {
        "zh-CN": "{actor} 为 {target} 提供掩护（伤害减半）",
        "en": "{actor} covers {target} (damage halved)",
    },
    "history_defend": {"zh-CN": "进入防御", "en": "Takes a defensive stance"},
    "history_defend_cover": {
        "zh-CN": "进入防御并掩护队友",
        "en": "Takes a defensive stance and covers an ally",
    },
    "defend": {"zh-CN": "{actor} 进入防御", "en": "{actor} takes a defensive stance"},
    "hinder_target_invalid": {"zh-CN": "妨碍目标无效", "en": "Invalid Hinder target"},
    "hinder_roll": {
        "zh-CN": "妨碍 {enemy}：{roll_a}+{roll_b}={total} vs 10 → {verdict}",
        "en": "Hinder {enemy}: {roll_a}+{roll_b}={total} vs 10 → {verdict}",
    },
    "objective_no_clock": {
        "zh-CN": "没有可推进的目标命刻",
        "en": "There is no objective Clock to advance",
    },
    "objective_roll": {
        "zh-CN": "推进目标：{roll_a}+{roll_b}={total} vs {dc} → 命刻 +{delta}",
        "en": "Objective: {roll_a}+{roll_b}={total} vs {dc} → Clock +{delta}",
    },
    "investigate_success": {
        "zh-CN": "调查：{roll_a}+{roll_b}={total} vs 10 → 得到线索",
        "en": "Investigate: {roll_a}+{roll_b}={total} vs 10 → a clue is found",
    },
    "investigate_failure": {
        "zh-CN": "调查：{roll_a}+{roll_b}={total} vs 10 → 没有收获",
        "en": "Investigate: {roll_a}+{roll_b}={total} vs 10 → nothing is found",
    },
    "fact_max_hp": {"zh-CN": "最大 HP", "en": "max HP"},
    "fact_defense": {"zh-CN": "物防", "en": "Defense"},
    "fact_magic_defense": {"zh-CN": "魔防", "en": "Magic Defense"},
    "fact_weakness": {"zh-CN": "弱点", "en": "vulnerability"},
    "fact_resistance": {"zh-CN": "抵抗", "en": "resistance"},
    "investigate_reveal": {
        "zh-CN": "调查揭示：{enemy} 的{fact}为 {value}",
        "en": "Investigation reveals: {enemy}'s {fact} is {value}",
    },
    "ip_short": {"zh-CN": "物资点不足", "en": "Not enough IP"},
    "bomb_target_invalid": {"zh-CN": "炸弹目标无效", "en": "Invalid bomb target"},
    "resource_bomb": {
        "zh-CN": "消耗 1 点物资点，对 {enemy} 投掷炸弹造成 10 伤害",
        "en": "Spent 1 IP to throw a bomb at {enemy} for 10 damage",
    },
    "resource_target_invalid": {"zh-CN": "物资目标无效", "en": "Invalid item target"},
    "resource_remedy": {
        "zh-CN": "消耗 1 点物资点，为 {target} 解除全部状态",
        "en": "Spent 1 IP to remove all status effects from {target}",
    },
    "resource_potion": {
        "zh-CN": "消耗 1 点物资点，为 {target} 回复 20 HP",
        "en": "Spent 1 IP to restore 20 HP to {target}",
    },
    "equip": {"zh-CN": "装备 {item}", "en": "Equips {item}"},
    "equip_replaced": {
        "zh-CN": "装备 {item}，换下 {replaced}",
        "en": "Equips {item}, replacing {replaced}",
    },
    "equip_log": {"zh-CN": "{actor} 装备 {item}", "en": "{actor} equips {item}"},
    "equip_missing": {
        "zh-CN": "背包里没有这件装备",
        "en": "That piece of equipment is not in the backpack",
    },

    # ------------------------------------------------------------------ 技能
    "skill_invalid": {"zh-CN": "技能无效", "en": "Invalid skill"},
    "skill_target_invalid": {"zh-CN": "技能目标无效", "en": "Invalid skill target"},
    "skill_target_limit": {
        "zh-CN": "技能最多选择 {count} 个目标",
        "en": "This skill can have at most {count} targets",
    },
    "skill_passive": {
        "zh-CN": "「{skill}」是被动技能，无需主动使用",
        "en": "\"{skill}\" is a passive skill and does not need to be used",
    },
    "skill_reaction": {
        "zh-CN": "「{skill}」是反应技能，会在触发时自动结算",
        "en": "\"{skill}\" is a reaction skill and resolves automatically when triggered",
    },
    "note_generic_skill": {
        "zh-CN": "自由技能（按普通攻击结算）",
        "en": "Free-form skill (resolved as a basic attack)",
    },
    "note_reaction_skill": {"zh-CN": "反应技能", "en": "Reaction skill"},
    "ally_required": {"zh-CN": "需要选择一名队友", "en": "An ally must be chosen"},
    "guard_ready": {
        "zh-CN": "{actor} 准备替 {target} 承受下一次攻击",
        "en": "{actor} is ready to take the next attack for {target}",
    },
    "mp_short": {"zh-CN": "MP 不足", "en": "Not enough MP"},
    "hp_short": {"zh-CN": "HP 不足", "en": "Not enough HP"},
    "hp_short_for_skill": {
        "zh-CN": "HP 不足，无法使用{skill}",
        "en": "Not enough HP to use {skill}",
    },
    "mp_spent": {"zh-CN": "消耗 {amount} MP", "en": "Spent {amount} MP"},
    "hp_cost": {"zh-CN": "以 {amount} HP 为代价", "en": "At the cost of {amount} HP"},
    "skill_hp_loss": {"zh-CN": "{skill}：失去 {amount} HP", "en": "{skill}: lost {amount} HP"},
    "skill_recover_hp_mp": {
        "zh-CN": "{skill}：回复 {amount} HP/MP",
        "en": "{skill}: recovered {amount} HP/MP",
    },
    "skill_roll": {
        "zh-CN": "技能「{skill}」{attr_a}+{attr_b} {roll_a}+{roll_b}={total}",
        "en": "Skill \"{skill}\" {attr_a}+{attr_b} {roll_a}+{roll_b}={total}",
    },
    "skill_roll_plain": {
        "zh-CN": "「{skill}」{roll_a}+{roll_b}={total}",
        "en": "\"{skill}\" {roll_a}+{roll_b}={total}",
    },
    "skill_damage": {
        "zh-CN": "对 {enemy} 造成 {damage} 伤害",
        "en": "Deals {damage} damage to {enemy}",
    },
    "skill_damage_affinity": {
        "zh-CN": "对 {enemy} 造成 {damage} 伤害（{affinity}）",
        "en": "Deals {damage} damage to {enemy} ({affinity})",
    },
    "target_hit_damage": {
        "zh-CN": "{target} 命中，伤害 {damage}",
        "en": "{target} is hit for {damage} damage",
    },
    "target_hit_damage_affinity": {
        "zh-CN": "{target} 命中，伤害 {damage}（{affinity}）",
        "en": "{target} is hit for {damage} damage ({affinity})",
    },
    "target_hit_dark_damage": {
        "zh-CN": "{target} 命中，暗属性伤害 {damage}",
        "en": "{target} is hit for {damage} dark damage",
    },
    "target_hit_dark_damage_affinity": {
        "zh-CN": "{target} 命中，暗属性伤害 {damage}（{affinity}）",
        "en": "{target} is hit for {damage} dark damage ({affinity})",
    },
    "target_element_damage": {
        "zh-CN": "{target} 受到 {damage} {element} 伤害",
        "en": "{target} takes {damage} {element} damage",
    },
    "target_element_damage_affinity": {
        "zh-CN": "{target} 受到 {damage} {element} 伤害（{affinity}）",
        "en": "{target} takes {damage} {element} damage ({affinity})",
    },
    "target_recovers_hp": {"zh-CN": "{target} 回复 {amount} HP", "en": "{target} recovers {amount} HP"},
    "target_restores_hp": {"zh-CN": "{target} 恢复 {amount} HP", "en": "{target} regains {amount} HP"},
    "target_buff": {"zh-CN": "{target} 获得 {stat} +{value}", "en": "{target} gains {stat} +{value}"},
    "target_buff_duration": {
        "zh-CN": "{target} 获得 {stat} +{value}（{duration} 回合）",
        "en": "{target} gains {stat} +{value} ({duration} rounds)",
    },
    "weapon_element": {
        "zh-CN": "{actor} 的武器附上{element}元素",
        "en": "{actor}'s weapon is imbued with the {element} element",
    },
    "attribute_die_up": {
        "zh-CN": "{target} 的{attribute}骰等级 +1",
        "en": "{target}'s {attribute} die size +1",
    },
    "enemy_mp_loss": {
        "zh-CN": "{enemy} 失去 {amount} MP（无 MP 时视为削弱）",
        "en": "{enemy} loses {amount} MP (counts as weakened if it has no MP)",
    },
    "enemy_shield_destroyed": {
        "zh-CN": "{enemy} 的盾被摧毁（物防 -2）",
        "en": "{enemy}'s shield is destroyed (Defense -2)",
    },
    "enemy_armor_destroyed": {
        "zh-CN": "{enemy} 的防具被摧毁（物防 -2）",
        "en": "{enemy}'s armor is destroyed (Defense -2)",
    },
    "enemy_vulnerable": {
        "zh-CN": "{enemy} 受到的伤害 +{amount}（到你的下回合）",
        "en": "{enemy} takes +{amount} damage (until your next turn)",
    },

    # ------------------------------------------------------------------ 冲突结束
    "conflict_victory": {"zh-CN": "冲突结束：胜利", "en": "Conflict over: victory"},
    "conflict_defeat_log": {
        "zh-CN": "战斗结束：全员倒下。你们被击倒在穹顶前，但还有最后一口气。",
        "en": (
            "The battle is over: everyone is down. You are struck down before the dome, "
            "but you still have one last breath."
        ),
    },
    "conflict_defeat": {
        "zh-CN": "冲突结束：全员倒下，队伍仍有最后一口气",
        "en": "Conflict over: everyone is down, but the party still has one last breath",
    },
    "conflict_ended": {"zh-CN": "冲突结束", "en": "Conflict ended"},

    # ------------------------------------------------------------------ 反派 / 物语点 / 休息 / 升级
    "villain_escape": {
        "zh-CN": "{villain} 消耗 1 终结点逃离（剩余 {fate}）",
        "en": "{villain} spends 1 Ultima Point to escape ({fate} left)",
    },
    "villain_surrender": {"zh-CN": "{villain} 投降", "en": "{villain} surrenders"},
    "villain_escalate": {
        "zh-CN": "{villain} 升格为{tier}，终结点重置为 {fate}",
        "en": "{villain} escalates to {tier}; Ultima Points reset to {fate}",
    },
    "fabula_spent": {
        "zh-CN": "{actor} 消耗 1 点物语点（{use}）",
        "en": "{actor} spends 1 Fabula Point ({use})",
    },
    "fabula_default_use": {"zh-CN": "改变故事", "en": "altering the story"},
    "rest_in_conflict": {"zh-CN": "冲突中不能休息", "en": "You cannot rest during a conflict"},
    "rest_recovered": {
        "zh-CN": "{actor} 完全恢复 HP/MP 并解除异常状态",
        "en": "{actor} fully recovers HP/MP and is cleared of status effects",
    },
    "levelup_unknown_class": {
        "zh-CN": "只能提升已有职业",
        "en": "You can only level up a class you already have",
    },
    "levelup_mastered": {"zh-CN": "该职业已精通", "en": "That class is already mastered"},
    "levelup_done": {
        "zh-CN": "{actor} 提升 {klass} 到 {level} 级，获得 1 技能点",
        "en": "{actor} raises {klass} to level {level} and gains 1 skill point",
    },

    # ------------------------------------------------------------------ 数据表校验
    "content_entry_not_object": {
        "zh-CN": "条目必须是 JSON 对象",
        "en": "The entry must be a JSON object",
    },
    "content_id_required": {"zh-CN": "id 不能为空", "en": "id cannot be empty"},
    "content_id_too_long": {"zh-CN": "id 过长", "en": "id is too long"},
    "content_name_required": {"zh-CN": "name 不能为空", "en": "name cannot be empty"},
    "content_category_invalid": {
        "zh-CN": "category 必须是 common / consumable / equipment",
        "en": "category must be common / consumable / equipment",
    },
    "content_effect_not_object": {"zh-CN": "effect 必须是对象", "en": "effect must be an object"},
}

# ---------------------------------------------------------------------- 按 id 查显示名
# 这些表的 key 是 canonical id（或引擎内的 canonical 值），运行时用变量取用。

#: 属性 key → 显示名。
ATTRIBUTE_LABELS: dict[str, dict[str, str]] = {
    "dex": {"zh-CN": "敏捷", "en": "Dexterity"},
    "ins": {"zh-CN": "洞察", "en": "Insight"},
    "mig": {"zh-CN": "力量", "en": "Might"},
    "wlp": {"zh-CN": "意志", "en": "Willpower"},
}

#: 冲突行动 id → 显示名（``conflict.ACTIONS``）。
ACTION_LABELS: dict[str, dict[str, str]] = {
    "attack": {"zh-CN": "攻击", "en": "Attack"},
    "defend": {"zh-CN": "防御", "en": "Defend"},
    "hinder": {"zh-CN": "妨碍", "en": "Hinder"},
    "objective": {"zh-CN": "推进目标", "en": "Advance objective"},
    "investigate": {"zh-CN": "调查", "en": "Investigate"},
    "resource": {"zh-CN": "消耗物资", "en": "Use item"},
    "equip": {"zh-CN": "装备", "en": "Equip"},
    "skill": {"zh-CN": "技能", "en": "Skill"},
}

#: 相性值 → 显示名（``conflict.affinity_multiplier``）。
AFFINITY_LABELS: dict[str, dict[str, str]] = {
    "weak": {"zh-CN": "弱点", "en": "Vulnerable"},
    "resist": {"zh-CN": "抵抗", "en": "Resistant"},
    "immune": {"zh-CN": "免疫", "en": "Immune"},
    "absorb": {"zh-CN": "吸收", "en": "Absorbs"},
}

#: 判定结论（canonical 值）→ 显示名。只用于拼进给人看的句子；参与比较、写进
#: 战斗记录 ``verdict`` 字段的仍是 canonical 值本身。
VERDICT_LABELS: dict[str, dict[str, str]] = {
    "大成功": {"zh-CN": "大成功", "en": "Critical Success"},
    "成功": {"zh-CN": "成功", "en": "Success"},
    "失败": {"zh-CN": "失败", "en": "Failure"},
    "大失败": {"zh-CN": "大失败", "en": "Fumble"},
}

LABEL_TABLES: dict[str, dict[str, dict[str, str]]] = {
    "ATTRIBUTE_LABELS": ATTRIBUTE_LABELS,
    "ACTION_LABELS": ACTION_LABELS,
    "AFFINITY_LABELS": AFFINITY_LABELS,
    "VERDICT_LABELS": VERDICT_LABELS,
}
