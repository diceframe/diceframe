"""用模型工具调用规划一整轮的结构化检定。"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from src.engine.checks import (
    build_check_request,
    detect_advantage_mode,
    find_action_opponent,
    is_explicit_attack_action,
    is_non_combat_declaration,
)
from src.engine.character_utils import is_conscious
from src.engine.dice import d20_dc_cap
from src.engine.economy import MAX_ECONOMY_AMOUNT
from src.engine.game_instance import GameInstance
from src.engine.language import localized_text
from src.llm.parser import sanitize_narration
from src.llm.tools import DICE_CHECKS_TOOL, DICE_CHECKS_TOOL_NAME
from src.rules.rule_system import RuleSystem

logger = logging.getLogger("trpg")

_SAFETY_CHECK_INTENTS = {"combat", "athletics", "stealth"}
_CONCEALED_OR_HAZARDOUS_WORDS = (
    "暗门", "暗室", "隐藏", "秘密", "危险", "异常", "诡异", "未知",
    "残留", "血迹", "毒", "陷阱", "追赶", "袭击",
    "hidden", "secret", "hazard", "danger", "trap", "poison", "attack",
)
_SKILL_USE_PREFIXES = ("使用", "运用", "尝试", "进行", "施展", "用", "use", "attempt")
_is_non_combat_declaration = is_non_combat_declaration


def _prompt_text(language: str) -> str:
    suffix = localized_text(language, {"en": "en", "zh-CN": "zh", "ja": "ja"})
    path = Path(__file__).resolve().parents[2] / "prompts" / f"check_planner_{suffix}.md"
    return path.read_text(encoding="utf-8")


def _skill_rows(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sheet.get("skills", []) or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                try:
                    value = int(item.get("value", 0) or 0)
                except (TypeError, ValueError):
                    value = 0
                rows.append({"name": name, "value": value})
        elif str(item).strip():
            rows.append({"name": str(item).strip(), "value": 0})
    return rows


def _planner_context(instance: GameInstance, rule: RuleSystem | None) -> str:
    players = []
    for action in instance.action_queue:
        uid = str(action.get("user_id") or "")
        if uid not in instance.players:
            continue
        if not is_conscious(instance.get_character_sheet(uid)):
            continue
        sheet = instance.get_character_sheet(uid)
        players.append({
            "player_id": uid,
            "character_name": instance.players[uid].get("character_name") or uid,
            "action": str(action.get("text") or "")[:1000],
            "attributes": sheet.get("attributes", {}),
            "skills": _skill_rows(sheet),
            "selected_attribute": str(action.get("selected_attribute") or ""),
            "selected_skill": str(action.get("selected_skill") or ""),
            "target_text": str(action.get("target_text") or ""),
        })
    mechanic = rule.check_mechanic if rule else {
        "dice": "d20",
        "comparison": "roll_plus_modifier_gte_target",
        "critical": {"success": 20, "failure": 1},
    }
    dice_system = str(rule.dice_system if rule else "d20").lower()
    attributes = [
        {"key": str(item.get("key") or ""), "name": str(item.get("name") or "")}
        for item in (rule.attributes if rule else [])
    ]
    ruleset = {
        "id": instance.rule_id,
        "dice_system": dice_system,
        "mechanic": mechanic,
        "attributes": attributes,
        "dc_table": rule.dc_table if rule else {"easy": 8, "normal": 10, "hard": 15},
        "target_policy": (
            "server_uses_character_sheet_percentile"
            if dice_system == "d100"
            else "gm_supplies_situational_dc"
        ),
    }
    if dice_system == "d20":
        ruleset["max_check_dc"] = d20_dc_cap(rule)
    payload = {
        "round": instance.round_number,
        "scene": str(instance.scene or "")[:500],
        "recent_narration": [
            sanitize_narration(str(entry.get("gm_response") or ""))[:1000]
            for entry in instance.log[-2:]
            if entry.get("gm_response")
        ],
        "difficulty": instance.difficulty,
        "ruleset": ruleset,
        "players": players,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _match_player(instance: GameInstance, value: object) -> str:
    query = str(value or "").strip().casefold()
    if not query:
        return ""
    for uid, player in instance.players.items():
        if query in {uid.casefold(), str(player.get("character_name") or "").strip().casefold()}:
            return uid
    return ""


def _match_opponent(instance: GameInstance, value: object) -> str:
    query = str(value or "").strip().casefold()
    if not query:
        return ""
    player = _match_player(instance, query)
    if player:
        return player
    for npc_id, npc in instance.npcs.items():
        names = {
            str(npc_id).strip().casefold(),
            str(npc.get("name") or "").strip().casefold(),
            str(npc.get("character_name") or "").strip().casefold(),
        }
        if query in names:
            return f"npc:{npc_id}"
    for index, enemy in enumerate(instance.combat_enemies):
        names = {
            str(enemy.get("name") or "").strip().casefold(),
            str(enemy.get("character_name") or "").strip().casefold(),
        }
        if query in names:
            return f"enemy:{index}"
    # 模型常把“考古学系主任”简称为“系主任”。仅在唯一匹配时接受双向包含，
    # 避免同场景有多个主任/守卫时猜错目标。
    if len(query) >= 2:
        partial_matches: set[str] = set()
        for npc_id, npc in instance.npcs.items():
            names = {
                str(npc_id).strip().casefold(),
                str(npc.get("name") or "").strip().casefold(),
                str(npc.get("character_name") or "").strip().casefold(),
            }
            if any(name and (query in name or name in query) for name in names):
                partial_matches.add(str(npc_id))
        if len(partial_matches) == 1:
            return f"npc:{next(iter(partial_matches))}"
    return ""


def _attribute_key(rule: RuleSystem | None, value: object) -> str:
    query = str(value or "").strip().casefold()
    if not query:
        return ""
    if not rule:
        return query
    for item in rule.attributes:
        key = str(item.get("key") or "").strip()
        names = {
            key.casefold(),
            str(item.get("name") or "").strip().casefold(),
            str(item.get("name_en") or "").strip().casefold(),
        }
        if query in names:
            return key
    return ""


def _skill_name(sheet: dict[str, Any], value: object) -> str:
    query = str(value or "").strip().casefold()
    if not query:
        return ""
    for item in _skill_rows(sheet):
        if str(item["name"]).casefold() == query:
            return str(item["name"])
    return ""


def _d100_target(sheet: dict[str, Any], attribute: str, skill: str) -> int:
    """Derive percentile thresholds from trusted character data, never model output."""
    if skill:
        row = next((item for item in _skill_rows(sheet) if item["name"] == skill), None)
        value = int(row["value"] if row else 0)
    else:
        attributes = sheet.get("attributes") if isinstance(sheet.get("attributes"), dict) else {}
        try:
            raw_value = int(attributes.get(attribute, 0) or 0)
        except (TypeError, ValueError):
            raw_value = 0
        value = raw_value if raw_value > 20 else raw_value * 5
    return max(1, min(99, value))


def _label(
    instance: GameInstance,
    rule: RuleSystem | None,
    attribute: str,
    skill: str,
    kind: str,
) -> str:
    subject = skill or attribute
    if rule and not skill:
        subject = next(
            (str(item.get("name") or attribute) for item in rule.attributes if item.get("key") == attribute),
            attribute,
        )
    en_suffix = {"save": "Save", "attack": "Attack"}.get(kind, "Check")
    zh_suffix = {"save": "豁免", "attack": "攻击"}.get(kind, "检定")
    ja_suffix = {"save": "セーヴ", "attack": "攻撃"}.get(kind, "判定")
    return localized_text(instance.language, {
        "en": f"{subject} {en_suffix}".strip(),
        "zh-CN": f"{subject}{zh_suffix}",
        "ja": f"{subject}{ja_suffix}",
    })


def normalize_check_specs(
    instance: GameInstance,
    rule: RuleSystem | None,
    raw_checks: list[Any],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[str]]:
    """校验模型参数并映射到行动；单条无效不会阻断同批其他检定。"""
    action_by_uid = {
        str(action.get("user_id") or ""): action
        for action in instance.action_queue
        if action.get("user_id") in instance.players
    }
    planned: list[tuple[dict[str, Any], dict[str, Any]]] = []
    errors: list[str] = []
    seen_players: set[str] = set()
    dice_system = str(rule.dice_system if rule else "d20").lower()
    for index, raw in enumerate(raw_checks[:8]):
        if not isinstance(raw, dict):
            errors.append(f"checks[{index}] 不是 object")
            continue
        uid = _match_player(instance, raw.get("player"))
        if not uid or uid not in action_by_uid:
            errors.append(f"checks[{index}] player 不存在或本轮未行动")
            continue
        if uid in seen_players:
            errors.append(f"checks[{index}] 同一玩家每轮只允许一个主检定")
            continue
        sheet = instance.get_character_sheet(uid)
        action = action_by_uid[uid]
        selected_attribute = str(action.get("selected_attribute") or "").strip()
        selected_skill = str(action.get("selected_skill") or "").strip()
        attribute_input = selected_attribute or str(raw.get("attribute") or "").strip()
        skill_input = selected_skill or str(raw.get("skill") or "").strip()
        attribute = _attribute_key(rule, attribute_input)
        skill = _skill_name(sheet, skill_input)
        repaired = False

        # Smaller models commonly put a skill name in `attribute` (especially for
        # percentile rules), or swap the two fields. Exact cross-field matches are
        # safe to repair because both values still come from the current rule/card.
        if attribute_input and not attribute and not skill:
            crossed_skill = _skill_name(sheet, attribute_input)
            if crossed_skill:
                skill = crossed_skill
                repaired = True
        if skill_input and not skill:
            crossed_attribute = _attribute_key(rule, skill_input)
            if crossed_attribute and not attribute:
                attribute = crossed_attribute
                skill_input = ""
                repaired = True

        # The model has already decided that a roll is warranted. If its field
        # choice is unusable, reuse only the deterministic rule/card matcher to
        # recover fields; it cannot create a new check or an arbitrary value.
        if not attribute or (skill_input and not skill):
            fallback = build_check_request(instance, action, rule)
            if fallback:
                if not attribute:
                    attribute = _attribute_key(rule, fallback.get("attribute"))
                if not skill and fallback.get("skill"):
                    skill = _skill_name(sheet, fallback.get("skill"))
                repaired = True

        sheet_attributes = sheet.get("attributes") if isinstance(sheet.get("attributes"), dict) else {}
        if attribute and attribute not in sheet_attributes:
            attribute = ""
        if skill_input and not skill:
            errors.append(f"checks[{index}] skill={skill_input!r} 不在角色卡中")
            continue
        if dice_system == "d100":
            if not attribute and not skill:
                errors.append(
                    f"checks[{index}] attribute={attribute_input!r} 既不是当前属性也不是角色技能"
                )
                continue
            target = _d100_target(sheet, attribute, skill)
        else:
            if not attribute:
                errors.append(f"checks[{index}] attribute={attribute_input!r} 不在当前规则或角色卡中")
                continue
            try:
                target = int(raw.get("target"))
            except (TypeError, ValueError):
                errors.append(f"checks[{index}] target 无效")
                continue
            # DC 硬上限由规则显式配置（默认 20），不从难度档位表反推：
            # 防止后期情境报出 25–30 的失控 DC 导致“只有自然 20 才成功”。
            target = max(1, min(d20_dc_cap(rule), target))
        modifier = max(-20, min(20, int(raw.get("modifier", 0) or 0)))
        advantage = str(raw.get("advantage") or "normal")
        advantage_mode = (
            advantage
            if advantage in {"advantage", "disadvantage"}
            and (rule is None or rule.supports_advantage_mode(advantage))
            else ""
        )
        kind = str(raw.get("kind") or "check")
        if kind not in {"check", "save", "attack"}:
            kind = "check"
        opponent_raw = str(raw.get("opponent") or "").strip()
        opponent = _match_opponent(instance, opponent_raw) if opponent_raw else ""
        if kind == "attack" and not opponent_raw:
            opponent = find_action_opponent(instance, uid, action.get("text"))
        if opponent_raw and not opponent:
            errors.append(f"checks[{index}] opponent 不存在")
            continue
        assistants: list[str] = []
        invalid_assistant = False
        for assistant in (raw.get("assist") or [])[:5]:
            assistant_uid = _match_player(instance, assistant)
            if not assistant_uid:
                errors.append(f"checks[{index}] assist 包含不存在的玩家")
                invalid_assistant = True
                break
            if assistant_uid != uid and assistant_uid not in assistants:
                assistants.append(assistant_uid)
        if invalid_assistant:
            continue
        assistance_grant = str(rule.advantage_mechanic.get("assistance_grants") or "") if rule else ""
        if assistants and not advantage_mode and dice_system == "d20" and assistance_grant:
            advantage_mode = assistance_grant
        request = {
            "check_id": uuid.uuid4().hex,
            "required": True,
            "actor_uid": uid,
            "actor_name": instance.players[uid].get("character_name") or uid,
            "dice_system": "d100" if dice_system == "d100" else "d20",
            "label": _label(instance, rule, attribute, skill, kind),
            "intent": "ai_planned",
            "skill": skill,
            "attribute": attribute,
            "target": target,
            "circumstance_modifier": modifier,
            "advantage_mode": advantage_mode,
            "advantage_note": str(raw.get("reason") or "")[:160] or None,
            "kind": kind,
            "opponent": opponent,
            "assist": assistants,
            "planner_source": "llm_tool_repaired" if repaired else "llm_tool",
        }
        planned.append((action_by_uid[uid], request))
        seen_players.add(uid)
    return planned, errors


def _merge_safety_net_checks(
    instance: GameInstance,
    rule: RuleSystem | None,
    planned: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """补上模型漏掉的明确/高风险检定，不接管普通叙事裁量。

    模型仍负责模糊场景；这里只覆盖玩家显式选择属性/技能、明确使用角色卡技能，
    以及战斗/潜行/高风险搜索等不应被直接叙事为自动成功的行动。
    """
    if rule and str(rule.dice_system).lower() == "none":
        return planned
    # 模型若把“不要开枪/避免交战”误标成 attack/combat，也先在这里撤掉；
    # 同一句若还有未否定的攻击词，_is_non_combat_declaration 会保留该检定。
    planned = [
        (action, request)
        for action, request in planned
        if not (
            (str(request.get("kind") or "") == "attack" or str(request.get("intent") or "") == "combat")
            and _is_non_combat_declaration(action.get("text"))
        )
    ]
    planned_uids = {str(request.get("actor_uid") or "") for _, request in planned}
    for action in instance.action_queue:
        uid = str(action.get("user_id") or "")
        if not uid or uid in planned_uids or uid not in instance.players:
            continue
        request = build_check_request(instance, action, rule)
        if not request:
            continue
        text = re.sub(r"\s+", "", str(action.get("text") or "")).casefold()
        explicit_selection = bool(action.get("selected_skill") or action.get("selected_attribute"))
        skill_cue = False
        for row in _skill_rows(instance.get_character_sheet(uid)):
            skill = re.sub(r"\s+", "", str(row.get("name") or "")).casefold()
            if not skill or skill not in text:
                continue
            skill_cue = any(f"{prefix}{skill}" in text for prefix in _SKILL_USE_PREFIXES)
            if skill_cue:
                break
        intent = str(request.get("intent") or "")
        if intent == "combat" and is_explicit_attack_action(action.get("text")):
            request["kind"] = "attack"
            request["opponent"] = str(request.get("opponent") or "") or find_action_opponent(
                instance, uid, action.get("text")
            )
        concealed_or_hazardous = (
            intent in {"investigate", "perception"}
            and any(word in text for word in _CONCEALED_OR_HAZARDOUS_WORDS)
        )
        safety_intent = intent in _SAFETY_CHECK_INTENTS
        if intent == "combat" and _is_non_combat_declaration(action.get("text")):
            safety_intent = False
        if not (
            explicit_selection
            or skill_cue
            or safety_intent
            or concealed_or_hazardous
        ):
            continue
        request["planner_source"] = "deterministic_safety_net"
        planned.append((action, request))
        planned_uids.add(uid)
    return planned


def _apply_d20_assistance(
    instance: GameInstance,
    rule: RuleSystem | None,
    planned: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """把 Help/协助给被帮助者的主检定，而不是给帮助者自己。

    模型工具仍可显式返回 ``assist``；此处只补足常见自然语言，并移除
    “我专心协助某人”这类纯 Help 行动被安全网误判出的独立检定。
    """
    if not rule or rule.dice_system != "d20":
        return planned
    assistance_grant = str(rule.advantage_mechanic.get("assistance_grants") or "")
    if assistance_grant not in {"advantage", "disadvantage"}:
        return planned
    action_by_uid = {
        str(action.get("user_id") or ""): action
        for action in instance.action_queue
        if action.get("user_id") in instance.players
    }
    assistance: dict[str, list[str]] = {}
    pure_helpers: set[str] = set()
    for helper_uid, action in action_by_uid.items():
        text = re.sub(r"\s+", "", str(action.get("text") or ""))
        for target_uid, pdata in instance.players.items():
            if target_uid == helper_uid:
                continue
            name = str(pdata.get("character_name") or "").strip()
            if not name or name not in text:
                continue
            helps_target = (
                f"协助{name}" in text
                or f"帮助{name}" in text
                or (f"为{name}" in text and "掩护" in text)
            )
            accepts_help = "接受" in text and any(word in text for word in ("协助", "帮助", "掩护", "指引"))
            if helps_target:
                assistance.setdefault(target_uid, []).append(helper_uid)
                if text.startswith("我协助") or "我专心协助" in text or text.startswith(f"我为{name}"):
                    pure_helpers.add(helper_uid)
            elif accepts_help:
                assistance.setdefault(helper_uid, []).append(target_uid)

    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for action, request in planned:
        actor_uid = str(request.get("actor_uid") or "")
        if actor_uid in pure_helpers:
            continue
        assistants = list(request.get("assist") or [])
        for assistant_uid in assistance.get(actor_uid, []):
            if assistant_uid != actor_uid and assistant_uid not in assistants:
                assistants.append(assistant_uid)
        if assistants:
            request["assist"] = assistants[:5]
            if not request.get("advantage_mode"):
                request["advantage_mode"] = assistance_grant
                request["advantage_note"] = (
                    "协助：2d20 取高" if assistance_grant == "advantage" else "协助：2d20 取低"
                )
        result.append((action, request))
    return result


def _apply_explicit_advantage_modes(
    rule: RuleSystem | None,
    planned: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """玩家明确写出的优势/劣势/奖惩骰覆盖模型的模糊推断。"""
    if not rule:
        return planned
    for action, request in planned:
        text = re.sub(r"\s+", "", str(action.get("text") or "")).casefold()
        mode, note = detect_advantage_mode(text, action, rule)
        if mode or "已抵消" in note:
            request["advantage_mode"] = mode
            request["advantage_note"] = note or None
            # 奖惩骰本身已经表达情境，不再同时套模型猜出的 ±百分比修正。
            if rule.advantage_mechanic.get("type") == "coc_bonus_penalty":
                request["circumstance_modifier"] = 0
    return planned


def normalize_economy_actions(
    instance: GameInstance,
    raw_actions: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """校验模型经济报价并归一到付款人。

    price_source=none 或缺价时安全跳过（没有人说出价格就不产生扣款提案），
    但有效的意图会以 ``unpriced`` 形式返回，供叙事后复检与本轮拦截使用；
    它们只存在于回合内存中，从不入库，也从不产生金额。amount 存在则
    price_source 必须是明确的转述来源。单条无效不影响同批。
    """
    offers: list[dict[str, Any]] = []
    unpriced: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_unpriced: set[tuple[str, str, int]] = set()
    for index, raw in enumerate(raw_actions[:8]):
        if not isinstance(raw, dict):
            errors.append(f"economy_actions[{index}] 不是 object")
            continue
        if str(raw.get("type") or "") != "purchase":
            errors.append(f"economy_actions[{index}] type 仅支持 purchase")
            continue
        uid = _match_player(instance, raw.get("player"))
        if not uid:
            errors.append(f"economy_actions[{index}] player 不存在")
            continue
        target = str(raw.get("target") or "").strip()[:120]
        if not target:
            errors.append(f"economy_actions[{index}] target 为空")
            continue
        price_source = str(raw.get("price_source") or "").strip()
        if price_source != "none" and raw.get("amount") is None:
            # 有目标但没有金额：与 price_source=none 同样处理为无价意图。
            price_source = "none"
        if price_source == "none":
            quantity = 1
            if raw.get("quantity") is not None:
                try:
                    quantity = int(raw.get("quantity") or 1)
                except (TypeError, ValueError):
                    errors.append(f"economy_actions[{index}] quantity 无效")
                    continue
                if not 1 <= quantity <= 8:
                    errors.append(f"economy_actions[{index}] quantity 超出范围")
                    continue
            key = (uid, target.casefold(), quantity)
            if key not in seen_unpriced:
                seen_unpriced.add(key)
                unpriced.append({
                    "payer_uid": uid,
                    "target": target,
                    "quantity": quantity,
                })
            continue
        try:
            quantity = int(raw.get("quantity", 1) or 1)
        except (TypeError, ValueError):
            errors.append(f"economy_actions[{index}] quantity 无效")
            continue
        if not 1 <= quantity <= 8:
            errors.append(f"economy_actions[{index}] quantity 超出范围")
            continue
        amount_raw = raw.get("amount")
        amount_scope = str(raw.get("amount_scope") or "total").strip()
        if amount_scope not in {"unit", "total"}:
            errors.append(f"economy_actions[{index}] amount_scope={amount_scope!r} 无效")
            continue
        try:
            amount = int(amount_raw)
        except (TypeError, ValueError):
            errors.append(f"economy_actions[{index}] amount 无效")
            continue
        if not 0 < amount <= MAX_ECONOMY_AMOUNT:
            errors.append(f"economy_actions[{index}] amount 超出范围")
            continue
        if price_source not in {"player_stated", "gm_narrated"}:
            errors.append(f"economy_actions[{index}] price_source={price_source!r} 无效")
            continue
        total_amount = amount * quantity if amount_scope == "unit" else amount
        if not 0 < total_amount <= MAX_ECONOMY_AMOUNT:
            errors.append(f"economy_actions[{index}] total amount 超出范围")
            continue
        offers.append({
            "payer_uid": uid,
            # Downstream proposal settlement always receives the total charge.
            "amount": total_amount,
            "quantity": quantity,
            "amount_scope": amount_scope,
            "target": target,
            "note": str(raw.get("note") or "").strip()[:160],
        })
    return offers, unpriced, errors


async def plan_round_checks(
    instance: GameInstance,
    rule: RuleSystem | None,
    llm_client: Any,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    """执行阶段 1；返回已校验的 (action, CheckRequest) 及调用元数据。"""
    if rule and str(rule.dice_system).lower() == "none":
        return [], {
            "available": True,
            "native_tools": False,
            "provider": "none",
            "total_tokens": 0,
            "errors": [],
            "skipped": "no_dice_rule",
        }
    if not instance.action_queue or not llm_client or not hasattr(llm_client, "call_tools"):
        return [], {"available": False, "errors": ["tool_call_unavailable"]}
    response = await llm_client.call_tools(
        _prompt_text(instance.language),
        _planner_context(instance, rule),
        tools=[DICE_CHECKS_TOOL],
        max_tokens=2048,
        temperature=0.1,
    )
    raw_checks: list[Any] = []
    raw_economy_actions: list[Any] = []
    overreach_notes: list[dict[str, str]] = []
    for call in response.tool_calls:
        if str(call.get("name") or "") != DICE_CHECKS_TOOL_NAME:
            continue
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        checks = arguments.get("checks")
        if isinstance(checks, list):
            raw_checks.extend(checks)
        # economy_actions 与 checks/overreach 独立解析：畸形/缺失只影响报价本身。
        try:
            economy = arguments.get("economy_actions")
            if isinstance(economy, list):
                raw_economy_actions.extend(economy)
        except Exception:
            logger.warning("economy_actions 解析失败，已忽略 (round=%d)", instance.round_number, exc_info=True)
        # overreach 与 checks 独立解析：畸形/缺失只影响标注本身，绝不波及检定规划。
        try:
            over = arguments.get("overreach")
            if isinstance(over, list):
                for item in over[:8]:
                    if not isinstance(item, dict):
                        continue
                    uid = _match_player(instance, item.get("player"))
                    reason = str(item.get("reason") or "").strip()[:160]
                    if uid and reason:
                        overreach_notes.append({"player": uid, "reason": reason})
        except Exception:
            logger.warning("overreach 标注解析失败，已忽略 (round=%d)", instance.round_number, exc_info=True)
    planned, errors = normalize_check_specs(instance, rule, raw_checks)
    planned = _merge_safety_net_checks(instance, rule, planned)
    planned = _apply_explicit_advantage_modes(rule, planned)
    planned = _apply_d20_assistance(instance, rule, planned)
    economy_offers, economy_intents_unpriced, economy_errors = normalize_economy_actions(
        instance, raw_economy_actions,
    )
    return planned, {
        "available": True,
        "native_tools": response.native_tools,
        "provider": response.provider_used,
        "total_tokens": response.total_tokens,
        "errors": errors + economy_errors,
        "overreach": overreach_notes,
        # 由调用方在过时检查通过后落库；这里不直接改动经济状态，
        # 否则创建提案推进的 revision 会让本轮规划被误判为过期。
        "economy_offers": economy_offers,
        # 无价购买意图只活在回合内存里：供叙事后复检报价与本轮 LOOT 拦截，
        # 从不持久化、不产生金额（ADR 0002 修订）。
        "unpriced_purchase_intents": economy_intents_unpriced,
    }


def _price_pass_prompt_text(language: str) -> str:
    suffix = localized_text(language, {"en": "en", "zh-CN": "zh", "ja": "ja"})
    path = (
        Path(__file__).resolve().parents[2] / "prompts" / f"purchase_price_pass_{suffix}.md"
    )
    return path.read_text(encoding="utf-8")


async def price_unpriced_purchase_intents(
    instance: GameInstance,
    llm_client: Any,
    narration_text: str,
    intents: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """叙事后复检：用本轮叙述文本给无价购买意图找真人报出的价格。

    复用 ``dice_checks`` 工具契约；金额必须逐字来自叙述文本中的原话，
    系统不推断价格（ADR 0002）。返回 (可入账报价, 仍无价的意图)。
    """
    if not intents or not llm_client or not hasattr(llm_client, "call_tools"):
        return [], list(intents)
    intent_rows = []
    for intent in intents[:8]:
        uid = str(intent.get("payer_uid") or "")
        if uid not in instance.players:
            continue
        intent_rows.append({
            "player_id": uid,
            "character_name": instance.players[uid].get("character_name") or uid,
            "target": str(intent.get("target") or ""),
            "quantity": int(intent.get("quantity", 1) or 1),
        })
    if not intent_rows:
        return [], list(intents)
    payload = json.dumps({
        "round": instance.round_number,
        "purchase_intents": intent_rows,
        "narration": sanitize_narration(str(narration_text or ""))[:4000],
    }, ensure_ascii=False, separators=(",", ":"))
    response = await llm_client.call_tools(
        _price_pass_prompt_text(instance.language),
        payload,
        tools=[DICE_CHECKS_TOOL],
        max_tokens=1024,
        temperature=0.1,
    )
    raw_actions: list[Any] = []
    for call in response.tool_calls:
        if str(call.get("name") or "") != DICE_CHECKS_TOOL_NAME:
            continue
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        economy = arguments.get("economy_actions")
        if isinstance(economy, list):
            raw_actions.extend(economy)
    offers, _unpriced, _errors = normalize_economy_actions(instance, raw_actions)
    # 只保留与提交意图对应的报价：复检无权替模型造出新的购买对象。
    intent_keys = {
        (str(intent.get("payer_uid") or ""), str(intent.get("target") or "").casefold())
        for intent in intents
    }
    priced_offers = [
        offer for offer in offers
        if (str(offer["payer_uid"]), str(offer["target"]).casefold()) in intent_keys
    ]
    consumed_keys = {
        (str(offer["payer_uid"]), str(offer["target"]).casefold()) for offer in priced_offers
    }
    remaining = [
        intent for intent in intents
        if (str(intent.get("payer_uid") or ""), str(intent.get("target") or "").casefold())
        not in consumed_keys
    ]
    return priced_offers, remaining
