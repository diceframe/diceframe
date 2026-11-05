"""用模型工具调用规划一整轮的结构化检定。"""

from __future__ import annotations

from src.engine.modules import game_config

from src.engine import module_hooks

import json
import logging
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from src.compat.characters import MAX_SKILL_EFFECT_CHARS
from src.engine.check_channels import normalize_check_channels
from src.engine.checks import (
    build_check_request,
    find_action_opponent,
    is_explicit_attack_action,
    is_non_combat_declaration,
    matched_action_phrases,
)
from src.engine.character_utils import is_conscious
from src.engine.currency import legacy_currency_spec, parse_currency_amount
from src.engine.currency.validation import CurrencySystemError
from src.engine.dice import d20_dc_cap
from src.engine.economy import MAX_ECONOMY_AMOUNT
from src.engine.game_instance import GameInstance
from src.engine.intent.parser import (
    parse_purchase_intents,
    purchase_balance_pattern,
    purchase_generic_target_terms,
    purchase_intent_pattern,
    purchase_quantity_classifier_pattern,
    purchase_quantity_words,
    purchase_offer_pattern,
    purchase_target_connector_pattern,
    purchase_target_prefix_pattern,
    purchase_target_suffix_pattern,
)
from src.engine.intent.locale_messages import locale_message
from src.engine.language import localized_text
from src.engine.modules import economy_state, progression_state, ruleset_runtime
from src.engine.modules.check_modes import apply_grant
from src.engine.world_events import MAX_ADVANCE_MINUTES
from src.engine.world_legality import (
    MAX_ROUTE_HOPS,
    MAX_WORLD_REQUIREMENTS,
    REQUIREMENT_KINDS,
)
from src.engine.world_state import project_visible_state
from src.llm.parser import sanitize_narration
from src.llm.tools import DICE_CHECKS_TOOL, DICE_CHECKS_TOOL_NAME
from src.rules.rule_system import RuleSystem

logger = logging.getLogger("trpg")

_SAFETY_CHECK_INTENTS = {"combat", "athletics", "stealth"}
# Safety Net 只判断「行动者本人当前的动作」：冒号与引号之后是转述/引用内容
# （第三方言行、过去事件），不是本轮动作。这里不做完整 NLP——只切掉明确
# 属于转述的尾巴，宁可保守。
_REPORTED_CONTENT_MARKERS = ("：", ":", "“", "”", "「", "」", "\"")
# 行动者本人作主语的标记；角色名与「我」等价。
_ACTOR_SELF_MARKERS = ("我", "自己", "本人", "i", "me", "my", "myself", "we", "our")
_CONCEALED_OR_HAZARDOUS_WORDS = (
    "暗门", "暗室", "隐藏", "秘密", "危险", "异常", "诡异", "未知",
    "残留", "血迹", "毒", "陷阱", "追赶", "袭击",
    "hidden", "secret", "hazard", "danger", "trap", "poison", "attack",
)
_SKILL_USE_PREFIXES = ("使用", "运用", "尝试", "进行", "施展", "用", "use", "attempt")
_is_non_combat_declaration = is_non_combat_declaration


def _has_possible_purchase_action(instance: GameInstance) -> bool:
    """Return whether a player action warrants a targeted economy re-scan.

    This is only a routing heuristic. It never creates an offer or infers a
    target/price; the follow-up tool call must still return an explicit,
    server-normalized economy action.
    """

    return bool(parse_purchase_intents(
        instance.action_queue,
        instance.players,
        str(getattr(instance, "language", "") or ""),
    ))


def _economy_repair_prompt(language: str) -> str:
    # Keep the retry prompt deliberately small. The tool schema already carries
    # the full economy contract, and the context carries the ruleset units;
    # re-sending the entire check-planner prompt would waste input tokens.
    return locale_message(language, "economy_repair_prompt")


def _economy_repair_context(instance: GameInstance, rule: RuleSystem | None) -> str:
    """Build only the evidence needed by the economy retry pass."""

    currency_spec = rule.currency_spec if rule else legacy_currency_spec("")
    payload = {
        "players": [
            {
                "player_id": str(action.get("user_id") or ""),
                "character_name": str(
                    (instance.players.get(str(action.get("user_id") or "")) or {}).get("character_name")
                    or action.get("user_id") or ""
                ),
                "action": str(action.get("text") or "")[:1000],
            }
            for action in instance.action_queue
            if str(action.get("user_id") or "") in instance.players
        ],
        "recent_narration": [
            sanitize_narration(str(entry.get("gm_response") or ""))[:1000]
            for entry in instance.log[-2:]
            if entry.get("gm_response")
        ],
        "currency_units": [
            {
                "id": unit.id,
                "name": unit.name,
                "rate": unit.rate,
                **({"symbol": unit.symbol} if unit.symbol else {}),
            }
            for unit in currency_spec.units
        ],
        "recent_purchases": _recent_purchases(instance),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _prompt_text(language: str) -> str:
    suffix = localized_text(
        language, {"en": "en", "zh-CN": "zh", "ja": "ja", "de": "de", "ru": "ru"}
    )
    path = Path(__file__).resolve().parents[2] / "prompts" / f"check_planner_{suffix}.md"
    return path.read_text(encoding="utf-8")


from src.commands.check_planner_context import (
    _action_mentions_name,
    _companion_roster,
    _companion_sheet,
    _context_match_text,
    _item_context,
    _match_actor,
    _npc_context,
    _planner_context,
    _positive_inventory_quantity,
    _recent_purchases,
    _skill_rows,
)

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


from src.commands.economy_planner import (
    append_unpriced_purchase_intent,
    normalize_economy_actions,
    _recover_purchase_intents_from_player_actions,
)

async def plan_round_checks(
    instance: GameInstance,
    rule: RuleSystem | None,
    llm_client: Any,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    """执行阶段 1；返回已校验的 (action, CheckRequest) 及调用元数据。"""
    # Narrative/no-dice rules still need economy planning when a player is
    # trying to buy something.  Keep the cheap no-model path for ordinary
    # narration, but do not let ``dice_system=none`` disable the independent
    # purchase protocol.
    if (
        rule
        and str(rule.dice_system).lower() == "none"
        and not _has_possible_purchase_action(instance)
    ):
        return [], {
            "available": True,
            "native_tools": False,
            "provider": "none",
            "total_tokens": 0,
            "errors": [],
            "skipped": "no_dice_rule",
        }
    if not instance.action_queue:
        return [], {"available": False, "errors": ["tool_call_unavailable"]}
    if not llm_client or not hasattr(llm_client, "call_tools"):
        recovered_offers: list[dict[str, Any]] = []
        recovered_unpriced: list[dict[str, Any]] = []
        _recover_purchase_intents_from_player_actions(
            instance, rule, recovered_offers, recovered_unpriced,
        )
        return [], {
            "available": False,
            "errors": ["tool_call_unavailable"],
            "economy_offers": recovered_offers,
            "unpriced_purchase_intents": recovered_unpriced,
        }
    response = await llm_client.call_tools(
        _prompt_text(instance.language),
        _planner_context(instance, rule),
        tools=[DICE_CHECKS_TOOL],
        max_tokens=4096,
        temperature=0.1,
    )
    raw_checks: list[Any] = []
    raw_economy_actions: list[Any] = []
    overreach_notes: list[dict[str, str]] = []
    world_requirements: list[dict[str, Any]] = []
    world_time_advance: dict[str, Any] | None = None
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
            logger.warning("economy_actions 解析失败，已忽略 (round=%d)", progression_state.round_value(instance), exc_info=True)
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
            logger.warning("overreach 标注解析失败，已忽略 (round=%d)", progression_state.round_value(instance), exc_info=True)
        # world_requirements 与 checks/overreach 独立解析：畸形/缺失只影响世界
        # 合法性判定本身，绝不波及检定规划。这里只做形状与花名册校验；「是否真的
        # 与世界真相矛盾」由 server 侧 world_legality 判定。
        try:
            raw_requirements = arguments.get("world_requirements")
            if isinstance(raw_requirements, list):
                for item in raw_requirements[:MAX_WORLD_REQUIREMENTS]:
                    if len(world_requirements) >= MAX_WORLD_REQUIREMENTS:
                        break
                    if not isinstance(item, dict):
                        continue
                    uid = _match_player(instance, item.get("player"))
                    kind = str(item.get("kind") or "act").strip()
                    location = str(item.get("location") or "").strip()[:120]
                    if not uid or not location or kind not in REQUIREMENT_KINDS:
                        continue
                    requirement: dict[str, Any] = {
                        "player": uid, "kind": kind, "location": location,
                    }
                    raw_via = item.get("via")
                    if isinstance(raw_via, list):
                        via = [
                            str(hop).strip()[:120]
                            for hop in raw_via[:MAX_ROUTE_HOPS]
                            if isinstance(hop, str) and str(hop).strip()
                        ]
                        if via:
                            requirement["via"] = list(dict.fromkeys(via))
                    world_requirements.append(requirement)
        except Exception:
            logger.warning(
                "world_requirements 解析失败，已忽略 (round=%d)",
                progression_state.round_value(instance), exc_info=True,
            )
        # world_time_advance 与 checks 独立解析：只报告本轮确实经过的逻辑时间，
        # 由 server 推进世界时钟并结算到期事件。
        try:
            raw_time = arguments.get("world_time_advance")
            if isinstance(raw_time, dict):
                raw_minutes = raw_time.get("minutes")
                if (
                    isinstance(raw_minutes, int) and not isinstance(raw_minutes, bool)
                    and 0 < raw_minutes <= MAX_ADVANCE_MINUTES
                ):
                    advance = {"minutes": raw_minutes}
                    note = str(raw_time.get("reason") or "").strip()[:160]
                    if note:
                        advance["reason"] = note
                    world_time_advance = advance
        except Exception:
            logger.warning(
                "world_time_advance 解析失败，已忽略 (round=%d)",
                progression_state.round_value(instance), exc_info=True,
            )
    planned, errors = normalize_check_specs(instance, rule, raw_checks)
    planned = _merge_safety_net_checks(instance, rule, planned)
    planned = _apply_d20_assistance(instance, rule, planned)
    planned = _apply_granted_advantage_modes(instance, rule, planned)
    economy_offers, economy_intents_unpriced, economy_errors = normalize_economy_actions(
        instance, raw_economy_actions, rule,
    )
    repair_tokens = 0
    repair_calls = 0
    repair_native_tools = False
    # A model may correctly narrate/check the round but omit the optional
    # economy field.  Give only likely purchase rounds one narrow second pass;
    # this improves recall without making the server invent a price.  The
    # second pass is still normalized by the same fail-closed validator.
    if _has_possible_purchase_action(instance) and not economy_offers:
        try:
            repair_response = await llm_client.call_tools(
                _economy_repair_prompt(instance.language),
                _economy_repair_context(instance, rule),
                tools=[DICE_CHECKS_TOOL],
                max_tokens=2048,
                temperature=0.0,
            )
            repair_calls = 1
            repair_tokens = int(getattr(repair_response, "total_tokens", 0) or 0)
            repair_native_tools = bool(getattr(repair_response, "native_tools", False))
            repair_actions: list[Any] = []
            for call in getattr(repair_response, "tool_calls", []) or []:
                if str(call.get("name") or "") != DICE_CHECKS_TOOL_NAME:
                    continue
                arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
                candidate_actions = arguments.get("economy_actions")
                if isinstance(candidate_actions, list):
                    repair_actions.extend(candidate_actions)
            if repair_actions:
                raw_economy_actions.extend(repair_actions)
                economy_offers, economy_intents_unpriced, repaired_errors = normalize_economy_actions(
                    instance, raw_economy_actions, rule,
                )
                economy_errors.extend(repaired_errors)
        except Exception as exc:
            # Quote repair is a recall enhancement; a provider failure must
            # never block ordinary check planning or narrative progression.
            economy_errors.append(f"economy repair pass unavailable: {type(exc).__name__}")
            logger.warning(
                "经济报价补扫失败，保留首轮规划: game=%s round=%d",
                instance.game_key, progression_state.round_value(instance), exc_info=True,
            )
    # The model passes are recall aids, not the authority.  A player action is
    # still a trusted source for a purchase intent: recover an explicit quote
    # or at least record an unpriced blocker so a missed model field can never
    # become a silent free delivery.
    _recover_purchase_intents_from_player_actions(
        instance, rule, economy_offers, economy_intents_unpriced,
    )
    priced_keys = {
        (
            str(offer.get("payer_uid") or ""),
            str(offer.get("target") or "").casefold(),
            int(offer.get("quantity", 1) or 1),
        )
        for offer in economy_offers
    }
    economy_intents_unpriced[:] = [
        intent for intent in economy_intents_unpriced
        if (
            str(intent.get("payer_uid") or ""),
            str(intent.get("target") or "").casefold(),
            int(intent.get("quantity", 1) or 1),
        ) not in priced_keys
    ]
    total_tokens = int(getattr(response, "total_tokens", 0) or 0) + repair_tokens
    return planned, {
        "available": True,
        "native_tools": bool(getattr(response, "native_tools", False)) or repair_native_tools,
        "provider": response.provider_used,
        "total_tokens": total_tokens,
        "calls": 1 + repair_calls,
        "errors": errors + economy_errors,
        "overreach": overreach_notes,
        # 结构化世界要求（模型提议）：由 server 侧 world_legality 与权威世界真相
        # 对照后才决定阻断或写入移动。
        "world_requirements": world_requirements,
        # 本轮经过的逻辑时间（模型提议，server 推进并结算到期事件）。
        "world_time_advance": world_time_advance,
        # 由调用方在过时检查通过后落库；这里不直接改动经济状态，
        # 否则创建提案推进的 revision 会让本轮规划被误判为过期。
        "economy_offers": economy_offers,
        # 无价购买意图只活在回合内存里：供结算阶段拦截同轮模型授予
        # （从不持久化、不产生金额；价格复检已按 ADR 0002 修订移除）。
        "unpriced_purchase_intents": economy_intents_unpriced,
    }
