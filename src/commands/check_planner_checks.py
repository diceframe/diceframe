"""Deterministic check normalization and safety-net checks."""

from __future__ import annotations

import re
import uuid
from typing import Any

from src.engine.checks import (
    build_check_request,
    find_action_opponent,
    is_explicit_attack_action,
    is_non_combat_declaration,
    matched_action_phrases,
)
from src.engine.check_channels import normalize_check_channels
from src.engine.dice import d20_dc_cap
from src.engine.game_instance import GameInstance
from src.engine.language import localized_text
from src.engine.modules import game_config, progression_state
from src.engine.modules.check_modes import apply_grant
from src.rules.rule_system import RuleSystem
from src.commands.check_planner_context import (
    _companion_roster,
    _companion_sheet,
    _match_actor,
    _match_opponent,
    _match_player,
    _skill_rows,
)

_SAFETY_CHECK_INTENTS = {"combat", "athletics", "stealth"}
_REPORTED_CONTENT_MARKERS = ("：", ":", "“", "”", "「", "」", '"')
_ACTOR_SELF_MARKERS = ("我", "自己", "本人", "i", "me", "my", "myself", "we", "our")
_CONCEALED_OR_HAZARDOUS_WORDS = (
    "暗门", "暗室", "隐藏", "秘密", "危险", "异常", "诡异", "未知",
    "残留", "血迹", "毒", "陷阱", "追赶", "袭击",
    "hidden", "secret", "hazard", "danger", "trap", "poison", "attack",
)
_SKILL_USE_PREFIXES = ("使用", "运用", "尝试", "进行", "施展", "用", "use", "attempt")
_is_non_combat_declaration = is_non_combat_declaration


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
    de_suffix = {"save": "Rettungswurf", "attack": "Angriff"}.get(kind, "Probe")
    return localized_text(instance.language, {
        "en": f"{subject} {en_suffix}".strip(),
        "zh-CN": f"{subject}{zh_suffix}",
        "ja": f"{subject}{ja_suffix}",
        "de": f"{subject} {de_suffix}".strip(),
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
        # 检定主体：默认行动玩家本人；模型显式委派 AI 队友时切换到队友角色卡。
        # 行动仍然挂回委派玩家（骰子由玩家掷，safety net 以 actor_uid 去重）。
        actor_kind_raw, actor_id = _match_actor(instance, raw.get("actor"))
        if raw.get("actor") and not actor_kind_raw:
            errors.append(f"checks[{index}] actor 不存在或存在歧义")
            continue
        if actor_kind_raw == "player" and actor_id != uid:
            errors.append(f"checks[{index}] actor 与行动玩家不一致")
            continue
        is_companion = actor_kind_raw == "companion"
        actor_ref = f"{actor_kind_raw}:{actor_id}" if is_companion else f"player:{uid}"
        actor_name = (
            str(_companion_roster(instance)[actor_id].get("name") or actor_id)
            if is_companion else (instance.players[uid].get("character_name") or uid)
        )
        sheet = (
            _companion_sheet(instance, actor_id) if is_companion
            else instance.get_character_sheet(uid)
        )
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
        # 同一情境事实只能进入一条渠道（DC / 优势劣势 / 环境 modifier）。
        # 模型把同一不利事实重复表达（抬 DC + 负 modifier + 劣势）时按固定
        # 优先级折叠，并留下 notes/dropped 审计信息，而不是全量执行。
        channels = normalize_check_channels(
            target=target if dice_system == "d20" else None,
            modifier=modifier,
            advantage_mode=advantage_mode,
            baseline_dc=rule.dc_for_difficulty(game_config.difficulty(instance), "normal") if rule else None,
            dc_cap=d20_dc_cap(rule),
            supports_advantage=rule is None or rule.supports_advantage_mode(advantage),
            dc_reason=str(raw.get("dc_reason") or ""),
            advantage_reason=str(raw.get("advantage_reason") or ""),
            modifier_reason=str(raw.get("modifier_reason") or ""),
        )
        if dice_system == "d20" and channels.target is not None:
            target = channels.target
        modifier = channels.modifier
        advantage_mode = channels.advantage_mode
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
            assist_kind, assist_id = _match_actor(instance, assistant)
            if not assist_kind:
                errors.append(f"checks[{index}] assist 包含不存在的玩家或队友")
                invalid_assistant = True
                break
            # 玩家沿用 uid 兼容既有显示；队友用 companion:<id> 引用。
            assistant_ref = (
                f"companion:{assist_id}" if assist_kind == "companion" else assist_id
            )
            if assistant_ref not in {uid, actor_ref} and assistant_ref not in assistants:
                assistants.append(assistant_ref)
        if invalid_assistant:
            continue
        assistance_grant = str(rule.advantage_mechanic.get("assistance_grants") or "") if rule else ""
        if assistants and not advantage_mode and dice_system == "d20" and assistance_grant:
            advantage_mode = assistance_grant
        request = {
            "check_id": uuid.uuid4().hex,
            "required": True,
            "actor_uid": uid,
            "actor_ref": actor_ref,
            "actor_name": actor_name,
            "dice_system": "d100" if dice_system == "d100" else "d20",
            "label": _label(instance, rule, attribute, skill, kind),
            "intent": "ai_planned",
            "skill": skill,
            "attribute": attribute,
            "target": target,
            "circumstance_modifier": modifier,
            "advantage_mode": advantage_mode,
            "advantage_note": str(raw.get("reason") or "")[:160] or None,
            # 理由在策略里用完整字符串做同源比较，落库/持久化时才按 schema 声明的
            # 160 字符上限截断（provider 不严格执行 maxLength 时也不能把超长文本
            # 写进游戏状态），与 advantage_note 的既有做法一致。
            "dc_reason": (channels.dc_reason[:160] or None),
            "advantage_reason": (channels.advantage_reason[:160] or None),
            "modifier_reason": (channels.modifier_reason[:160] or None),
            "planner_notes": list(channels.notes),
            "planner_dropped": dict(channels.dropped),
            "kind": kind,
            "opponent": opponent,
            "assist": assistants,
            "planner_source": "llm_tool_repaired" if repaired else "llm_tool",
        }
        planned.append((action_by_uid[uid], request))
        seen_players.add(uid)
    return planned, errors


def _safety_net_action_scope(text: object) -> str:
    """行动者本人可能作主语的那部分文本（切掉转述/引用内容）。"""
    source = str(text or "")
    cut = len(source)
    for marker in _REPORTED_CONTENT_MARKERS:
        index = source.find(marker)
        if 0 <= index < cut:
            cut = index
    return source[:cut]


def _safety_net_subjects(
    instance: GameInstance, uid: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(行动者本人标记, 其他已知参与者名)。只认权威状态里的参与者。"""
    player = instance.players.get(uid) or {}
    self_markers = list(_ACTOR_SELF_MARKERS)
    actor_name = str(player.get("character_name") or "").strip()
    if actor_name:
        self_markers.append(actor_name)
    foreign: set[str] = set()
    for other_id, other in instance.players.items():
        if other_id == uid:
            continue
        for value in (other_id, (other or {}).get("character_name")):
            if str(value or "").strip():
                foreign.add(str(value).strip())
    for companion_id, companion in _companion_roster(instance).items():
        for value in (companion_id, companion.get("name")):
            if str(value or "").strip():
                foreign.add(str(value).strip())
    for npc_id, npc in (getattr(instance, "npcs", None) or {}).items():
        if not isinstance(npc, dict):
            continue
        for value in (npc_id, npc.get("name"), npc.get("character_name")):
            if str(value or "").strip():
                foreign.add(str(value).strip())
    # 长名优先，避免「阿」这类短名先命中。
    return tuple(self_markers), tuple(sorted(foreign, key=len, reverse=True))


def _nearest_subject_is_foreign(
    prefix: str, self_markers: tuple[str, ...], foreign_names: tuple[str, ...],
) -> bool:
    """短语之前最近出现的主语是不是别人（没有主语时按行动者本人处理）。"""
    lowered = prefix.casefold()
    best_self = max(
        (lowered.rfind(marker.casefold()) for marker in self_markers), default=-1,
    )
    best_foreign = -1
    for name in foreign_names:
        index = lowered.rfind(name.casefold())
        if index >= 0:
            best_foreign = max(best_foreign, index)
    return best_foreign >= 0 and best_foreign > best_self


def _safety_net_action_confirmed(
    instance: GameInstance, uid: str, text: object, rule: RuleSystem | None, intent: str,
) -> bool:
    """Safety Net 是否可以为该意图补检定。

    需要同时满足：命中具体动作短语（而不是裸动词），且这句话的主语是行动者
    本人当前的动作。明确攻击仍由既有攻击关键词兜底，不受短语规则影响。
    """
    if intent == "combat" and is_explicit_attack_action(text):
        return True
    scope = _safety_net_action_scope(text)
    if not scope:
        return False
    self_markers, foreign_names = _safety_net_subjects(instance, uid)
    for phrase in matched_action_phrases(rule, intent, scope, instance.language):
        found = re.search(re.escape(phrase), scope, flags=re.IGNORECASE)
        if found is None:
            continue
        if not _nearest_subject_is_foreign(
            scope[:found.start()], self_markers, foreign_names,
        ):
            return True
    return False


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
        safety_intent = intent in _SAFETY_CHECK_INTENTS and _safety_net_action_confirmed(
            instance, uid, action.get("text"), rule, intent,
        )
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
            automatic = str(request.get("advantage_mode") or "")
            sources = {m for m in (automatic, assistance_grant) if m in {"advantage", "disadvantage"}}
            request["check_mode_sources"] = sorted(sources)
            request["advantage_mode"] = next(iter(sources)) if len(sources) == 1 else ""
            if automatic != request["advantage_mode"]:
                request["advantage_note"] = (
                    "Assistance: advantage and disadvantage cancel" if len(sources) == 2 else
                    "协助：2d20 取高" if assistance_grant == "advantage" else "协助：2d20 取低"
                )
        result.append((action, request))
    return result


def _apply_granted_advantage_modes(
    instance: GameInstance,
    rule: RuleSystem | None,
    planned: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Combine scoped GM grants with the planner's adjudication and rule assistance."""
    if not rule or rule.advantage_mechanic.get("type") not in {"coc_bonus_penalty", "d20_keep_high_low"}:
        return planned
    for _action, request in planned:
        apply_grant(instance, request, f"round:{progression_state.round_value(instance)}")
    return planned
