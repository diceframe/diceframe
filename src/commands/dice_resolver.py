"""回合检定适配器。

原始掷骰与规则数学统一放在 ``src.engine.checks``；本模块只负责兼容旧调用、
记录结构化结果，以及把已经结算的结果格式化成 GM 必须遵守的约束文本。
"""

from __future__ import annotations
from src.engine.modules import progression_state

from typing import Any

from src.engine.checks import (
    build_check_request,
    default_check_attribute,
    resolve_check_request,
    roll_check_request,
)
from src.engine.check_mechanics import check_mechanic_for, request_dice_system
from src.engine.game_instance import GameInstance
from src.engine.language import localized_text
from src.engine.modules.check_modes import apply_grant
from src.rules.rule_system import RuleSystem


_VERDICT_MAPPINGS = {
    "en": {
        "大成功": "Critical Success",
        "极难成功": "Extreme Success",
        "困难成功": "Hard Success",
        "普通成功": "Regular Success",
        "成功": "Success",
        "失败": "Failure",
        "大失败": "Critical Failure",
    },
    "de": {
        "大成功": "Kritischer Erfolg",
        "极难成功": "Extremer Erfolg",
        "困难成功": "Schwerer Erfolg",
        "普通成功": "Normaler Erfolg",
        "成功": "Erfolg",
        "失败": "Fehlschlag",
        "大失败": "Kritischer Fehlschlag",
    },
}


def _verdict_text(verdict: str, english: bool, lang: str = "en") -> str:
    if not english:
        return verdict
    return _VERDICT_MAPPINGS.get(lang, _VERDICT_MAPPINGS["en"]).get(verdict, verdict)


class DiceResolver:
    """把核心检定结果接入回合状态和 GM 提示。"""

    def resolve_action_check(
        self,
        instance: GameInstance,
        action: dict[str, Any],
        rule: RuleSystem | None,
    ) -> str:
        """结算行动中已经确认的原始骰值；同一玩家行动不会二次掷骰。"""
        request = action.get("check_request") if isinstance(action.get("check_request"), dict) else {}
        uid = str(action.get("user_id") or request.get("actor_uid") or "")
        if uid not in instance.players:
            return ""
        if not action.get("dice_value"):
            if rule:
                return self.roll_rule_check(instance, str(action.get("text") or ""), rule)
            dice_system = str(request.get("dice_system") or "d20").lower()
            return self.roll_d100_check(instance, str(action.get("text") or "")) if dice_system == "d100" else ""

        check = resolve_check_request(instance, action, rule)
        if not check:
            return ""
        instance.record_check(check)
        return self._format_constraint(instance, rule, check)

    def roll_rule_check(self, instance: GameInstance, actions_text: str, rule: RuleSystem) -> str:
        """兼容旧入口：构造请求、掷一次骰，再交给统一结算函数。"""
        first_action = next(
            (
                action for action in instance.action_queue
                if action.get("selected_skill") or action.get("selected_attribute")
            ),
            instance.action_queue[0] if instance.action_queue else {},
        )
        uid = str(first_action.get("user_id") or "")
        if uid not in instance.players:
            return ""
        action = dict(first_action)
        action["text"] = str(action.get("text") or actions_text)
        request = action.get("check_request") if isinstance(action.get("check_request"), dict) else None
        if not request:
            request = build_check_request(instance, action, rule)
        if not request:
            request = self._legacy_request(instance, action, rule)
        return self._roll_and_resolve(instance, action, request, rule)

    def roll_d100_check(self, instance: GameInstance, actions_text: str) -> str:
        """兼容无规则对象的旧 d100 调用，并使用统一 CoC 成功等级。"""
        first_action = next(
            (action for action in instance.action_queue if action.get("selected_skill")),
            instance.action_queue[0] if instance.action_queue else {},
        )
        uid = str(first_action.get("user_id") or "")
        if uid not in instance.players:
            return ""
        action = dict(first_action)
        action["text"] = str(action.get("text") or actions_text)
        sheet = instance.get_character_sheet(uid)
        attributes = sheet.get("attributes") if isinstance(sheet.get("attributes"), dict) else {}
        if not action.get("selected_attribute") and attributes:
            action["selected_attribute"] = max(attributes, key=lambda key: int(attributes.get(key, 0) or 0))
        rule = self._legacy_d100_rule()
        request = build_check_request(instance, action, rule) or self._legacy_request(instance, action, rule)
        return self._roll_and_resolve(instance, action, request, rule)

    @staticmethod
    def _legacy_d100_rule() -> RuleSystem:
        return RuleSystem({
            "rule_id": "legacy_d100",
            "dice_system": "d100",
            "mechanics": "coc7e_core",
            "check_mechanic": {
                "dice": "d100",
                "comparison": "roll_lte_target",
                "critical": {"success": 1, "failure_rule": "coc7e"},
                "advantage": {
                    "type": "coc_bonus_penalty",
                    "allow_explicit": True,
                    "assistance_grants": "",
                },
            },
        })

    def roll_coc_check(
        self,
        instance: GameInstance,
        uid: str,
        player: dict,
        attrs: dict,
        matched_skill: dict | None,
    ) -> str:
        """保留旧公开方法；参数转换后仍走统一 d100 入口。"""
        del player
        action: dict[str, Any] = {"user_id": uid, "text": "CoC 检定"}
        if matched_skill:
            action["selected_skill"] = str(matched_skill.get("name") or "")
        elif attrs:
            action["selected_attribute"] = max(attrs, key=lambda key: int(attrs.get(key, 0) or 0))
        rule = self._legacy_d100_rule()
        request = build_check_request(instance, action, rule) or self._legacy_request(instance, action, rule)
        return self._roll_and_resolve(instance, action, request, rule)

    @staticmethod
    def _legacy_request(instance: GameInstance, action: dict[str, Any], rule: RuleSystem) -> dict[str, Any]:
        uid = str(action.get("user_id") or "")
        text = str(action.get("text") or "")
        attribute = str(action.get("selected_attribute") or "")
        if not attribute:
            intent = rule.find_intent(text, instance.language)
            attribute = rule.intent_default_attribute(intent) if intent else ""
        if attribute not in rule.attribute_keys:
            attribute = "dex" if "dex" in rule.attribute_keys else (
                rule.attribute_keys[0] if rule.attribute_keys else "int"
            )
        request = {
            "check_id": "",
            "required": True,
            "actor_uid": uid,
            "actor_name": str(instance.players.get(uid, {}).get("character_name") or uid),
            "dice_system": request_dice_system(rule.dice_system),
            "label": "检定",
            "skill": str(action.get("selected_skill") or ""),
            "attribute": attribute,
            "advantage_mode": "",
            "advantage_note": None,
            "planner_source": "legacy",
        }
        mechanic = check_mechanic_for(rule.dice_system)
        if mechanic is not None:
            sheet = instance.get_character_sheet(uid)
            attributes = sheet.get("attributes")
            request.update(mechanic.request_fields(
                rule=rule,
                sheet_attributes=attributes if isinstance(attributes, dict) else {},
                attribute=attribute,
                action=action,
            ))
        if rule.advantage_mechanic.get("type") in {"coc_bonus_penalty", "d20_keep_high_low"}:
            apply_grant(instance, request, f"round:{progression_state.round_value(instance)}")
        return request

    @staticmethod
    def _guess_attribute_key(actions_text: str, rule: RuleSystem) -> str:
        """旧调用兼容：属性推断本身由检定引擎的词表入口负责。"""
        return default_check_attribute(actions_text, rule)

    def _roll_and_resolve(
        self,
        instance: GameInstance,
        action: dict[str, Any],
        request: dict[str, Any],
        rule: RuleSystem,
    ) -> str:
        rolled = roll_check_request(request, rule)
        prepared = dict(action)
        prepared["check_request"] = request
        prepared["dice_value"] = rolled["value"]
        prepared["dice_rolls"] = rolled["rolls"]
        check = resolve_check_request(instance, prepared, rule)
        if not check:
            return ""
        instance.record_check(check)
        return self._format_constraint(instance, rule, check)

    @staticmethod
    def _format_constraint(
        instance: GameInstance,
        rule: RuleSystem | None,
        check: dict[str, Any],
    ) -> str:
        verdict = str(check.get("verdict") or "")
        if check.get("dice") == "d100":
            threshold = int(check.get("threshold", 1) or 1)
            if check.get("skill"):
                subject = f"技能「{check['skill']}」{threshold}%"
            else:
                subject = f"属性「{check.get('attribute') or 'int'}」={threshold}%"
            luck_cost = check.get("luck_cost")
            luck_hint = ""
            if check.get("luck_spend_available") and luck_cost:
                luck_hint = localized_text(instance.language, {
                    "en": f"\nLuck option: spend {luck_cost} Luck for a regular success.",
                    "zh-CN": f"\n幸运选项: 可消耗 {luck_cost} 点幸运变为普通成功。",
                    "ja": f"\n幸運オプション: {luck_cost} 点の幸運を消費して普通成功にできる。",
                    "de": f"\nGlücksoption: {luck_cost} Glückspunkte einsetzen, um einen normalen Erfolg zu erzielen.",
                })
            return localized_text(instance.language, {
                "en": (
                    "\n[System Check - Must Follow]\n"
                    f"Check: d100={check['roll']} vs {subject}\n"
                    f"Result: {_verdict_text(verdict, True)}{luck_hint}\n"
                    "Requirement: narrate this server-resolved result without changing the roll or outcome.\n"
                ),
                "zh-CN": (
                    "\n【系统检定·必须遵循】\n"
                    f"检定: d100={check['roll']} vs {subject}\n"
                    f"成功等级阈值: 普通≤{threshold}，困难≤{check['hard_threshold']}，"
                    f"极难≤{check['extreme_threshold']}\n"
                    f"结果: {verdict}{luck_hint}\n"
                    "要求: 这是服务端已结算结果，只按结果叙事，不得重掷或改判。\n"
                ),
                "ja": (
                    "\n【システム判定・必ず従うこと】\n"
                    f"判定: d100={check['roll']} vs {subject}\n"
                    f"成功レベル閾値: 普通≤{threshold}、困難≤{check['hard_threshold']}、"
                    f"極難≤{check['extreme_threshold']}\n"
                    f"結果: {verdict}{luck_hint}\n"
                    "要求: これはサーバー側で確定した結果。この結果に沿って叙述し、"
                    "振り直しや改変をしてはならない。\n"
                ),
                "de": (
                    "\n[Systemseitige Probe - Muss befolgt werden]\n"
                    f"Probe: d100={check['roll']} vs {subject}\n"
                    f"Erfolgsschwellen: normal≤{threshold}, schwer≤{check['hard_threshold']}, "
                    f"extrem≤{check['extreme_threshold']}\n"
                    f"Ergebnis: {_verdict_text(verdict, True, 'de')}{luck_hint}\n"
                    "Anforderung: Erzähle dieses vom Server ermittelte Ergebnis, ohne Wurf oder Ausgang zu ändern.\n"
                ),
            })

        rolls = list(check.get("rolls") or [check.get("roll")])
        mechanic = check_mechanic_for(check.get("dice"))
        if mechanic is not None:
            # 注册骰制自己描述结果；返回空串时沿用下面的通用格式。
            custom = mechanic.result_prompt(instance, check)
            if custom:
                return custom
        mode = str(check.get("advantage_mode") or "")
        if mode == "advantage":
            roll_label = f"d20优势={rolls} 取 {check['roll']}"
        elif mode == "disadvantage":
            roll_label = f"d20劣势={rolls} 取 {check['roll']}"
        else:
            roll_label = f"d20={check['roll']}"
        if check.get("advantage_note"):
            roll_label += f"（{check['advantage_note']}）"
        attribute = str(check.get("attribute") or "")
        modifier = int(check.get("modifier", 0) or 0)
        total = int(check.get("total", 0) or 0)
        dc = int(check.get("dc", 0) or 0)
        return localized_text(instance.language, {
            "en": (
                "\n[System Check - Must Follow]\n"
                f"Check: {roll_label} + {attribute} {modifier:+d} = {total} vs DC {dc}\n"
                f"Result: {_verdict_text(verdict, True)}\n"
                "Requirement: narrate this server-resolved result without changing the roll or outcome.\n"
            ),
            "zh-CN": (
                "\n【系统检定·必须遵循】\n"
                f"检定: {roll_label} + 属性「{attribute}」总修正 {modifier:+d} = {total} vs DC {dc}\n"
                f"结果: {verdict}\n"
                "要求: 这是服务端已结算结果，只按结果叙事，不得重掷或改判。\n"
            ),
            "ja": (
                "\n【システム判定・必ず従うこと】\n"
                f"判定: {roll_label} + 属性「{attribute}」合計修正 {modifier:+d} = {total} vs DC {dc}\n"
                f"結果: {verdict}\n"
                "要求: これはサーバー側で確定した結果。この結果に沿って叙述し、"
                "振り直しや改変をしてはならない。\n"
            ),
            "de": (
                "\n[Systemseitige Probe - Muss befolgt werden]\n"
                f"Probe: {roll_label} + {attribute} {modifier:+d} = {total} vs SG {dc}\n"
                f"Ergebnis: {_verdict_text(verdict, True, 'de')}\n"
                "Anforderung: Erzähle dieses vom Server ermittelte Ergebnis, ohne Wurf oder Ausgang zu ändern.\n"
            ),
        })
