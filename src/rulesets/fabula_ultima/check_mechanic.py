"""Fabula Ultima 的叙事检定机制：两枚属性骰相加 vs DL。

这是 FU 对通用检定管线的唯一接入点（``src/engine/check_mechanics.py``）：
通用层按 ``dice_system == "fu"`` 查表后把建请求、掷骰、校验、结算和给 GM 的
结果文本全部委托到这里，generic engine 不含任何 FU 分支。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from src.engine.character_utils import armor_value
from src.engine.check_mechanics import CheckMechanic, CheckResolution, register_check_mechanic
from src.engine.dice import d20_dc_cap, roll
from src.engine.language import localized_text
from src.engine.modules import game_config
from src.engine.player_control import is_ai_controlled
from src.rulesets.fabula_ultima.i18n import language_of, render
from src.rulesets.fabula_ultima.dice import (
    FU_CRIT_MIN,
    FU_DIE_SIZES,
    fu_die_size,
    fu_high_roll,
    fu_is_critical,
    fu_is_fumble,
    fu_verdict,
)

DICE_SYSTEM = "fu"

#: 「机会」表的条目顺序：大成功由行动者选一项，大失败由 GM 选一项。
OPPORTUNITY_KEYS = (
    "advantage", "affliction", "bonding", "faux_pas", "favor", "information",
    "lost_item", "progress", "plot_twist", "scan", "unmask",
)

MESSAGES: dict[str, dict[str, str]] = {
    "opportunity_advantage": {
        "zh-CN": "优势：你或盟友的下一次检定 +4",
        "en": "Advantage: you or an ally get +4 on the next check",
    },
    "opportunity_affliction": {
        "zh-CN": "苦难：使一个生物陷入眩晕/动摇/迟缓/虚弱",
        "en": "Affliction: make a creature Dazed, Shaken, Slow or Weak",
    },
    "opportunity_bonding": {
        "zh-CN": "羁绊：与某人或某物建立羁绊，或为已有羁绊增加一种情感",
        "en": "Bonding: form a Bond with someone or something, or add an emotion to an existing Bond",
    },
    "opportunity_faux_pas": {
        "zh-CN": "失言：让在场的一个生物说出一句不利于自己的话",
        "en": "Faux Pas: a creature present says something that works against them",
    },
    "opportunity_favor": {
        "zh-CN": "亲睐：你的行动赢得某人的支持或好感",
        "en": "Favor: your action earns someone's support or goodwill",
    },
    "opportunity_information": {
        "zh-CN": "线索：你注意到一个有用的细节（GM 可直接给出，或让玩家补充）",
        "en": "Information: you notice a useful detail (the GM may give it or let the player add it)",
    },
    "opportunity_lost_item": {
        "zh-CN": "丢失：一件物品被毁、丢失、被盗或落在原地",
        "en": "Lost Item: an item is destroyed, lost, stolen or left behind",
    },
    "opportunity_progress": {
        "zh-CN": "进展：为一个命刻填充或擦除至多 2 格",
        "en": "Progress: fill or erase up to 2 sections of a Clock",
    },
    "opportunity_plot_twist": {
        "zh-CN": "情节转折：你选择的某人或某物突然出现在场景中",
        "en": "Plot Twist: someone or something of your choice suddenly appears in the scene",
    },
    "opportunity_scan": {
        "zh-CN": "扫描：你发现一个生物的某个弱点或特质",
        "en": "Scan: you discover one weakness or trait of a creature",
    },
    "opportunity_unmask": {
        "zh-CN": "揭秘：你得知一个生物的目标与动机",
        "en": "Unmask: you learn a creature's goals and motivations",
    },
    "check_label": {"zh-CN": "{first}+{second}检定", "en": "{first}+{second} Check"},
    "modifier_circumstance": {"zh-CN": "情境修正 {value:+d}", "en": "Circumstance {value:+d}"},
    "modifier_opportunity": {"zh-CN": "机会加成 +{value}", "en": "Opportunity bonus +{value}"},
    "modifier_separator": {"zh-CN": "；", "en": "; "},
    "manual_roll_refused": {
        "zh-CN": "Fabula Ultima 的检定需要两项属性，请直接提交行动，由裁判阶段成对掷骰。",
        "en": (
            "A Fabula Ultima check needs two attributes. Submit your action instead; "
            "the dice are rolled as a pair during adjudication."
        ),
    },
}


def opportunities(language: object) -> list[str]:
    """按对局语言渲染的「机会」选项。"""

    return [render(language, MESSAGES, f"opportunity_{key}") for key in OPPORTUNITY_KEYS]


#: 机会加成单次上限（与 BONUS 标签、机会面板的写入上限一致）。
PENDING_BONUS_CAP = 20

# 行动文本可能是任何语言：中文按子串、拉丁文字按词首匹配。
_HOSTILE_VERBS = (
    "攻击", "砍", "劈", "刺", "射", "砸", "揍", "打", "踢", "推", "撞", "压", "逼",
    "威胁", "制服", "擒", "斩", "挑",
)
_HOSTILE_WORDS = re.compile(
    r"\b(attack|strike|hit|slash|stab|shoot|punch|kick|shove|push|tackle|threaten|grab|restrain)",
    re.IGNORECASE,
)
_VERDICT_TEXT = {
    "en": {"大成功": "Critical Success", "成功": "Success", "失败": "Failure", "大失败": "Fumble"},
    "de": {
        "大成功": "Kritischer Erfolg", "成功": "Erfolg", "失败": "Fehlschlag",
        "大失败": "Kritischer Fehlschlag",
    },
}


def crit_min(rule: Any) -> int:
    """大成功阈值：两枚骰子同点且不低于该值（规则可声明 ``critical.doubles_min``）。"""

    critical = (getattr(rule, "check_mechanic", None) or {}).get("critical") if rule is not None else None
    if isinstance(critical, dict):
        try:
            return max(2, min(12, int(critical.get("doubles_min", FU_CRIT_MIN))))
        except (TypeError, ValueError):
            return FU_CRIT_MIN
    return FU_CRIT_MIN


def _looks_hostile_toward(text: str, name: str) -> bool:
    """行动文本是否真的把某个名字当作攻击对象（而非只在对话里提到）。"""

    if not text or not name:
        return False
    start = 0
    while True:
        index = text.find(name, start)
        if index < 0:
            return False
        window = text[max(0, index - 16): index + len(name) + 16]
        if any(verb in window for verb in _HOSTILE_VERBS) or _HOSTILE_WORDS.search(window):
            return True
        start = index + len(name)


def defense_dl(rule: Any, target: dict[str, Any]) -> int | None:
    """由目标的服务端状态推导物防：属性骰面 + 护甲/盾牌，没有 d20 的 10 底数。"""

    target_rule = (getattr(rule, "check_mechanic", None) or {}).get("attack_target") if rule is not None else None
    if not isinstance(target_rule, dict) or str(target_rule.get("type") or "") != "die_size":
        return None
    attributes = target.get("attributes") if isinstance(target.get("attributes"), dict) else {}
    total = fu_die_size(attributes.get(str(target_rule.get("attribute") or "dex"), 6))
    if target_rule.get("include_armor", True):
        total += armor_value(target)
    total += int(target_rule.get("bonus", 0) or 0)
    minimum = int(target_rule.get("min", 1) or 1)
    maximum = int(target_rule.get("max", 40) or 40)
    return max(minimum, min(maximum, int(total)))


class FabulaUltimaCheckMechanic(CheckMechanic):
    dice_system = DICE_SYSTEM
    target_is_dc = True

    def descriptor(self) -> dict[str, Any]:
        return {
            "dice": "2d_attributes",
            "comparison": "sum_gte_target",
            "critical": {"doubles_min": FU_CRIT_MIN, "fumble": "double_one"},
            "high_roll_damage": True,
        }

    # ------------------------------------------------------------------ 建请求

    def request_fields(
        self,
        *,
        rule: Any,
        sheet_attributes: dict[str, Any],
        attribute: str,
        action: dict[str, Any],
        planned: dict[str, Any] | None = None,
        is_attack: bool = False,
        resolve_attribute: Callable[[object], str] | None = None,
    ) -> dict[str, Any]:
        """固化第二属性与两枚骰面。

        面数在建请求阶段算好并随请求落库，掷骰与结算不再回查角色卡，避免
        角色卡中途变动导致同一次检定前后不一致。同一属性两次是合法的
        （开放检定，例如调查用【洞察+洞察】），显式给出时必须保留。
        """

        keys = [str(key) for key in (getattr(rule, "attribute_keys", None) or []) if key]
        requested = ""
        if planned is not None:
            raw_second = str(planned.get("attribute_b") or "").strip()
            if raw_second:
                requested = resolve_attribute(raw_second) if resolve_attribute else raw_second
        if not requested:
            requested = str(action.get("selected_attribute_b") or "").strip()
        if requested and keys and requested not in keys:
            requested = ""

        second = requested
        if not second and is_attack and rule is not None:
            # 武器攻击有惯用组合（近战多为敏捷+力量），规则可声明默认攻击组合。
            pair = rule.check_mechanic.get("attack_pair")
            for key in pair if isinstance(pair, list) else []:
                if str(key) in keys and str(key) != attribute:
                    second = str(key)
                    break
        if not second:
            others = [key for key in keys if key != attribute]
            second = (
                max(others, key=lambda key: fu_die_size(sheet_attributes.get(key, 6)))
                if others else attribute
            )
        if planned is not None and second not in sheet_attributes:
            second = attribute
        dice_a = fu_die_size(sheet_attributes.get(attribute, 6))
        dice_b = fu_die_size(sheet_attributes.get(second, dice_a)) if second else dice_a
        return {"attribute_b": second, "dice_a": dice_a, "dice_b": dice_b}

    # ------------------------------------------------------------------ 掷骰

    def roll(self, request: dict[str, Any], rule: Any) -> dict[str, Any]:
        # 没有优势/劣势骰；重掷由物语点在规则运行时处理。
        sides_a = fu_die_size(request.get("dice_a") or 6)
        sides_b = fu_die_size(request.get("dice_b") or sides_a)
        rolls = [roll(f"d{sides_a}").natural, roll(f"d{sides_b}").natural]
        return {
            "value": rolls[0] + rolls[1],
            "rolls": rolls,
            "critical": fu_is_critical(rolls, crit_min(rule)),
            "fumble": fu_is_fumble(rolls),
            # 伤害要读高值：掷骰阶段就把答案固定下来，结算阶段只做算术。
            "high_roll": fu_high_roll(rolls),
        }

    def validate_roll(
        self,
        request: dict[str, Any],
        rule: Any,
        rolls: list[int],
        roll_value: int,
    ) -> None:
        sides = [
            fu_die_size(request.get("dice_a") or FU_DIE_SIZES[-1], FU_DIE_SIZES[-1]),
            fu_die_size(request.get("dice_b") or FU_DIE_SIZES[-1], FU_DIE_SIZES[-1]),
        ]
        if len(rolls) != 2 or any(not 1 <= value <= limit for value, limit in zip(rolls, sides)):
            raise ValueError(f"非法的 fu 候选骰值: {rolls}")
        if roll_value != sum(rolls):
            raise ValueError(f"非法的 fu 检定值: {roll_value} 不是 {rolls} 之和")

    # ------------------------------------------------------------------ 结算

    def resolve(self, resolution: CheckResolution) -> dict[str, Any]:
        instance, rule, request = resolution.instance, resolution.rule, resolution.request
        language = language_of(instance)
        common, rolls = resolution.common, resolution.rolls
        attributes = resolution.attributes
        opponent_name, opponent_state = resolution.opponent_name, resolution.opponent_state

        # 防友军误绑：攻击检定把玩家当目标，但行动文本只是在对话里提到他，
        # 则目标其实是怪物/场景物——丢弃 opponent，回到裁判给的 DL，
        # 否则服务端会把该玩家的物防当成 DL。
        if (
            common["kind"] == "attack"
            and resolution.opponent_ref in instance.players
            and not _looks_hostile_toward(str(resolution.action.get("text") or ""), opponent_name)
        ):
            request["opponent"] = ""
            opponent_name, opponent_state = "", {}
            common["opponent"] = ""
            common["opponent_name"] = ""
        opponent_attributes = (
            opponent_state.get("attributes") if isinstance(opponent_state.get("attributes"), dict) else {}
        )

        key_a = str(request.get("attribute") or "dex")
        key_b = str(request.get("attribute_b") or key_a)
        label_a = resolution.attribute_label(key_a)
        label_b = resolution.attribute_label(key_b)
        sides_a = fu_die_size(request.get("dice_a") or attributes.get(key_a, 6))
        sides_b = fu_die_size(request.get("dice_b") or attributes.get(key_b, sides_a))
        circumstance_modifier = resolution.circumstance_modifier
        opportunity_bonus = self._consume_pending_bonus(resolution)
        modifier = circumstance_modifier + opportunity_bonus
        total = resolution.roll_value + modifier

        baseline = rule.dc_for_difficulty(game_config.difficulty(instance), "normal") if rule else 10
        try:
            raw_dc = int(request["target"]) if request.get("target") is not None else baseline
        except (TypeError, ValueError):
            raw_dc = baseline
        # 规则声明的 max_check_dc 是情境 DL 的硬上限；由目标状态推导出来的
        # 物防不受此限。
        dc = max(1, min(d20_dc_cap(rule), raw_dc))
        trusted_attack_dc = (
            defense_dl(rule, opponent_state) if common["kind"] == "attack" and opponent_name else None
        )
        if trusted_attack_dc is not None:
            dc = trusted_attack_dc

        # 对抗检定：对手用同样的双骰规则掷一次，结果缓存回请求，重算不重掷。
        if opponent_name and common["kind"] != "attack":
            if request.get("opponent_total") is None:
                opponent_sides_a = fu_die_size(opponent_attributes.get(key_a, 6))
                opponent_sides_b = fu_die_size(opponent_attributes.get(key_b, opponent_sides_a))
                opponent_rolls = [
                    roll(f"d{opponent_sides_a}").natural,
                    roll(f"d{opponent_sides_b}").natural,
                ]
                request["opponent_roll"] = opponent_rolls[0]
                request["opponent_modifier"] = 0
                request["opponent_total"] = sum(opponent_rolls)
            dc = max(1, min(40, int(request["opponent_total"])))

        verdict = fu_verdict(rolls, total, dc, crit_min=crit_min(rule))
        common.update({
            "label": common["label"] or render(
                language, MESSAGES, "check_label", first=label_a, second=label_b,
            ),
            "attribute": f"{label_a}+{label_b}",
            "attribute_key": key_a,
            "attribute_b": label_b,
            "attribute_b_key": key_b,
            "dice_a": sides_a,
            "dice_b": sides_b,
            "modifier": modifier,
            "modifier_breakdown": (
                render(language, MESSAGES, "modifier_separator").join(filter(None, [
                    render(language, MESSAGES, "modifier_circumstance", value=circumstance_modifier)
                    if circumstance_modifier else "",
                    render(language, MESSAGES, "modifier_opportunity", value=opportunity_bonus)
                    if opportunity_bonus else "",
                ])) or None
            ),
            "opportunity_bonus": opportunity_bonus,
            "total": total,
            "dc": dc,
            "difficulty": game_config.difficulty(instance),
            "target_source": "server_armor_class" if trusted_attack_dc is not None else "request_dc",
            "opponent_name": opponent_name,
            "opponent_roll": request.get("opponent_roll"),
            "opponent_modifier": request.get("opponent_modifier"),
            "opponent_total": request.get("opponent_total"),
            "verdict": verdict,
            "is_critical": verdict == "大成功",
            "is_fumble": verdict == "大失败",
            # 大成功给行动者「机会」，大失败给 GM 一个机会。
            "opportunity": opportunities(language) if verdict == "大成功" else [],
            "fumble_opportunity": opportunities(language) if verdict == "大失败" else [],
            # 伤害层直接消费该值：伤害 = 高值 + 武器，不再看属性加值。
            "high_roll": fu_high_roll(rolls),
            "dice_pair": list(rolls),
        })
        return common

    @staticmethod
    def _consume_pending_bonus(resolution: CheckResolution) -> int:
        """机会加成：由 BONUS 标签或机会面板写入角色卡，下一次检定自动消耗。"""

        if resolution.actor_is_companion:
            return 0
        sheet = resolution.character_sheet
        try:
            pending = int(sheet.get("pending_check_bonus") or 0)
        except (TypeError, ValueError):
            pending = 0
        if pending <= 0:
            return 0
        cleared = dict(sheet)
        cleared["pending_check_bonus"] = 0
        cleared.pop("pending_check_bonus_reason", None)
        resolution.instance.set_character_sheet(resolution.uid, cleared)
        resolution.character_sheet = cleared
        return min(PENDING_BONUS_CAP, pending)

    # ------------------------------------------------------------------ GM 提示

    def result_prompt(self, instance: Any, check: dict[str, Any]) -> str:
        verdict = str(check.get("verdict") or "")
        rolls = list(check.get("rolls") or [check.get("roll")])
        pair = "+".join(str(int(value)) for value in rolls)
        dice_label = f"d{check.get('dice_a') or '?'}+d{check.get('dice_b') or '?'}"
        high = int(check.get("high_roll", 0) or 0)
        total = int(check.get("total", 0) or 0)
        dc = int(check.get("dc", 0) or 0)
        modifier = int(check.get("modifier", 0) or 0)
        modifier_text = f" {modifier:+d}" if modifier else ""
        opportunity_zh, opportunity_en = self._opportunity_prompt(instance, check, verdict)
        verdict_en = _VERDICT_TEXT["en"].get(verdict, verdict)
        verdict_de = _VERDICT_TEXT["de"].get(verdict, verdict)
        return localized_text(instance.language, {
            "en": (
                "\n[System Check - Must Follow]\n"
                f"Check: {dice_label} = {pair} = {check['roll']}{modifier_text} -> {total} vs DL {dc}\n"
                f"High roll: {high} (damage = high roll + weapon/spell bonus)\n"
                f"Result: {verdict_en}\n"
                f"{opportunity_en}"
                "Requirement: narrate this server-resolved result without changing the roll or outcome.\n"
            ),
            "zh-CN": (
                "\n【系统检定·必须遵循】\n"
                f"检定: {dice_label} = {pair} = {check['roll']}{modifier_text} → {total} vs DL {dc}\n"
                f"高值: {high}（命中后伤害 = 高值 + 武器/法术加值）\n"
                f"结果: {verdict}\n"
                f"{opportunity_zh}"
                "要求: 这是服务端已结算结果，只按结果叙事，不得重掷或改判。\n"
            ),
            "ja": (
                "\n【システム判定・必ず従うこと】\n"
                f"判定: {dice_label} = {pair} = {check['roll']}{modifier_text} → {total} vs DL {dc}\n"
                f"ハイロール: {high}（ダメージ = ハイロール + 武器/呪文）\n"
                f"結果: {verdict}\n"
                f"{opportunity_zh}"
                "要求: これはサーバー側で確定した結果。この結果に沿って叙述し、"
                "振り直しや改変をしてはならない。\n"
            ),
            "de": (
                "\n[Systemseitige Probe - Muss befolgt werden]\n"
                f"Probe: {dice_label} = {pair} = {check['roll']}{modifier_text} → {total} vs SG {dc}\n"
                f"High Roll: {high} (Schaden = High Roll + Waffen-/Zauberbonus)\n"
                f"Ergebnis: {verdict_de}\n"
                f"{opportunity_en}"
                "Anforderung: Erzähle dieses vom Server ermittelte Ergebnis, ohne Wurf oder Ausgang zu ändern.\n"
            ),
        })

    @staticmethod
    def _opportunity_prompt(instance: Any, check: dict[str, Any], verdict: str) -> tuple[str, str]:
        """大成功 / 大失败必须当场给「机会」，否则模型很容易只写「你成功了」。"""

        options = check.get("opportunity") or check.get("fumble_opportunity") or []
        options_text = "；".join(str(item) for item in options)
        if not options_text:
            return "", ""
        if verdict == "大失败":
            return (
                f"\n大失败机会（由你选一项施加，并写进本轮叙事）: {options_text}\n",
                f"\nFumble opportunity (choose one and apply it in this narration): {options_text}\n",
            )
        if verdict != "大成功":
            return "", ""
        actor_uid = str(check.get("actor_uid") or "")
        if actor_uid and is_ai_controlled(instance, actor_uid):
            return (
                "\n机会（大成功·AI 席位）：这次大成功来自 AI 控制的队友，由你代选一项，"
                f"并在本轮叙事里明确写出所选项目与它带来的效果，不要含糊成「某种好处」。\n选项: {options_text}\n",
                "\nOpportunity (critical success, AI-controlled ally): pick one option on the AI's behalf "
                f"and state the chosen option and its effect explicitly in this narration. Options: {options_text}\n",
            )
        return (
            "\n机会（大成功）：不要替玩家选择。先叙述行动结果，再把以下选项列给玩家并问他选哪一项；"
            f"若玩家本轮行动里已声明「我选机会：X」，则直接结算 X。机会效果可留到下一轮开头结算。\n选项: {options_text}\n",
            "\nOpportunity (critical success): do NOT choose for the player. Narrate the outcome, then list "
            "these options and ask the player which one. If the player already declared a choice in this "
            f"action, apply it. Options: {options_text}\n",
        )

    # ------------------------------------------------------------------ 裁判 / 配置

    def planner_actor_context(self, sheet: dict[str, Any]) -> dict[str, Any]:
        # 服务端会在结算时自动把机会加成加进总结果，裁判不要重复加。
        try:
            pending = int(sheet.get("pending_check_bonus") or 0)
        except (TypeError, ValueError):
            pending = 0
        return {"pending_bonus": max(0, pending)}

    def planner_tool_properties(self) -> dict[str, Any]:
        return {
            "attribute_b": {
                "type": "string",
                "description": (
                    "The exact canonical key of the SECOND attribute for this check. Checks in this "
                    "ruleset always add two attribute dice (e.g. DEX+MIG for a melee attack, INS+WLP "
                    "for a spell, INS+INS for a study check); `attribute` is the first one."
                ),
            },
        }

    def rule_template_error(self, template: dict[str, Any]) -> str:
        raw_array = template.get("attribute_dice_array")
        if raw_array is None:
            return ""
        if not isinstance(raw_array, list) or len(raw_array) != len(template.get("attributes") or []):
            return "attribute_dice_array 必须是与 attributes 等长的数组"
        for item in raw_array:
            if isinstance(item, bool) or not isinstance(item, int) or item not in FU_DIE_SIZES:
                return "attribute_dice_array 的每项必须是 6/8/10/12"
        return ""

    def manual_roll_refusal(self, language: object) -> dict[str, Any] | None:
        # 一次检定绑定「两项属性」，单骰没有意义；骰子由裁判阶段成对掷出。
        return {
            "ok": False,
            "code": "FU_CHECK_NEEDS_ATTRIBUTES",
            "error": render(language, MESSAGES, "manual_roll_refused"),
        }


def register() -> None:
    register_check_mechanic(FabulaUltimaCheckMechanic())
