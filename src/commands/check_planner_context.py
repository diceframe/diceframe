"""Context projection helpers for the round check/economy planner."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from src.compat.characters import MAX_SKILL_EFFECT_CHARS
from src.engine import module_hooks
from src.engine.character_utils import is_conscious
from src.engine.checks import find_action_opponent
from src.engine.currency import legacy_currency_spec
from src.engine.dice import d20_dc_cap
from src.engine.game_instance import GameInstance
from src.engine.modules import economy_state, game_config, progression_state, ruleset_runtime
from src.engine.world_state import project_visible_state
from src.llm.parser import sanitize_narration
from src.rules.rule_system import RuleSystem


def _skill_action_matches(effect_owner: str, action_text: str) -> bool:
    """Deterministic match: the action text mentions this skill's name.

    First version deliberately stays trivial (normalized containment); no
    embeddings, no LLM retrieval, no alias inference.
    """

    skill = re.sub(r"\s+", "", effect_owner).casefold()
    action = re.sub(r"\s+", "", str(action_text or "")).casefold()
    return bool(skill) and skill in action


def _skill_rows(
    sheet: dict[str, Any],
    action_text: str = "",
    selected_skill: str = "",
    include_matching_effects: bool = False,
) -> list[dict[str, Any]]:
    """Project sheet skills for planning/display.

    ``effect`` is descriptive metadata, never mechanical authority: it is only
    attached when the caller asks for it AND the current action actually uses
    that skill — either the action text mentions its name, or the player
    explicitly selected it (``selected_skill``).  It never enters
    roll/DC/damage resolution.
    """
    selected = str(selected_skill or "").strip().casefold()
    rows: list[dict[str, Any]] = []
    for item in sheet.get("skills", []) or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                try:
                    value = int(item.get("value", 0) or 0)
                except (TypeError, ValueError):
                    value = 0
                row = {"name": name, "value": value}
                effect = str(item.get("effect") or "").strip()
                # 明确选中（selected_skill）与文本命中同等视为「本轮使用该技能」。
                matched = _skill_action_matches(name, action_text) or bool(
                    selected and selected == name.casefold()
                )
                if effect and include_matching_effects and matched:
                    row["effect"] = effect[:MAX_SKILL_EFFECT_CHARS]
                rows.append(row)
        elif str(item).strip():
            rows.append({"name": str(item).strip(), "value": 0})
    return rows


def _recent_purchases(instance: GameInstance) -> list[dict[str, Any]]:
    """最近的购买提案快照，让 planner 看到“什么已经买过/报过价”。

    没有这份历史时，模型只能从 recent_narration 里看到上一轮的价格，
    容易把“还有什么”这类浏览追问误判成新的购买意图，导致成交后
    每回合重复弹窗（实际事故：round3 重复报价 round2 已成交的药水）。
    """
    rows: list[dict[str, Any]] = []
    for proposal in reversed(economy_state.state(instance).get("proposals", [])):
        if len(rows) >= 5:
            break
        if not isinstance(proposal, dict) or str(proposal.get("kind") or "") != "purchase":
            continue
        if str(proposal.get("status") or "") not in {"pending", "committed", "declined"}:
            continue
        rewards = proposal.get("rewards") or []
        items = sorted({
            str(reward.get("name") or "").strip()
            for reward in rewards
            if isinstance(reward, dict) and str(reward.get("name") or "").strip()
        })
        rows.append({
            "round": proposal.get("round"),
            "status": proposal.get("status"),
            "payer_id": proposal.get("payer_uid"),
            "items": items,
            "quantity": len(rewards),
            "amount": proposal.get("amount"),
        })
    rows.reverse()
    return rows


RECENT_CHECK_ROUNDS = 4
MAX_RECENT_CHECKS = 8


def _recent_checks(instance: GameInstance) -> list[dict[str, Any]]:
    """近几轮已结算的检定：让规划器认出「换个说法再试一次」的同一次尝试。

    只给模型做判断用的最小字段；行动原文只截一段，且已是玩家本人在本局提交过的内容。
    """
    rows: list[dict[str, Any]] = []
    for entry in instance.log[-RECENT_CHECK_ROUNDS:]:
        if not isinstance(entry, dict):
            continue
        actions = {
            str(action.get("user_id") or ""): str(action.get("text") or "")
            for action in entry.get("actions") or []
            if isinstance(action, dict)
        }
        for check in entry.get("check_results") or []:
            if not isinstance(check, dict):
                continue
            uid = str(check.get("actor_uid") or "")
            row: dict[str, Any] = {
                "round": entry.get("round"),
                "player": uid,
                "check": str(check.get("skill") or check.get("attribute_key") or "")[:40],
                "verdict": str(check.get("verdict") or ""),
            }
            action_text = sanitize_narration(actions.get(uid, "")).strip()
            if action_text:
                row["action"] = action_text[:120]
            push = check.get("push")
            if isinstance(push, dict) and push.get("status") == "resolved":
                row["pushed"] = True
            if check.get("luck_decision") == "spent":
                row["luck_spent"] = True
            rows.append(row)
    return rows[-MAX_RECENT_CHECKS:]


def _companion_roster(instance: GameInstance) -> dict[str, dict[str, Any]]:
    """DND party companions（ruleset_state.party.companions）；其他规则集为空。

    只返回活跃且有 canonical 角色卡的队友；generic planner 不感知规则集差异，
    没有该状态时（CoC 等）自然为空。
    """
    state = ruleset_runtime.state(instance)
    party = state.get("party") if isinstance(state, dict) else None
    companions = party.get("companions") if isinstance(party, dict) else None
    if not isinstance(companions, dict):
        return {}
    return {
        companion_id: companion
        for companion_id, companion in companions.items()
        if isinstance(companion, dict) and companion.get("active", True)
        and isinstance(companion.get("ruleset_character"), dict)
    }


def _companion_sheet(instance: GameInstance, companion_id: str) -> dict[str, Any]:
    """companion canonical 角色卡；proficiencies.skill_values 合成 skills 列表。"""
    companion = _companion_roster(instance).get(companion_id)
    if not companion:
        return {}
    sheet = companion["ruleset_character"]
    # DND canonical 用 abilities（3-18）；generic 检定读 attributes——缺失时镜像，
    # 与玩家侧 webui 投影（attributes/skills 镜像进 character_sheet）语义一致。
    merged = dict(sheet)
    if not merged.get("attributes"):
        merged["attributes"] = dict(sheet.get("abilities") or {})
    if not merged.get("skills"):
        skill_values = sheet.get("proficiencies", {}).get("skill_values") or {}
        merged["skills"] = [
            {"name": name, "value": value}
            for name, value in sorted(skill_values.items())
        ]
    return merged


def _match_actor(instance: GameInstance, value: object) -> tuple[str, str]:
    """把模型输出解析为检定主体 (kind, id)。

    支持显式前缀（player:<uid> / companion:<id>）、玩家 uid/名、队友 id/名。
    精确匹配优先；歧义、不存在时返回 ("", "")，绝不从纯叙事 NPC 猜测。
    """
    query = str(value or "").strip()
    if not query:
        return "", ""
    lowered = query.lower()
    if lowered.startswith("player:"):
        uid = query.removeprefix("player:").strip()
        return ("player", uid) if uid in instance.players else ("", "")
    if lowered.startswith("companion:"):
        companion_id = query.removeprefix("companion:").strip()
        return (
            ("companion", companion_id)
            if companion_id in _companion_roster(instance) else ("", "")
        )
    player_uid = _match_player(instance, query)
    if player_uid:
        return "player", player_uid
    matches = [
        companion_id
        for companion_id, companion in _companion_roster(instance).items()
        if query.casefold() in {
            companion_id.casefold(),
            str(companion.get("name") or "").strip().casefold(),
        }
    ]
    if len(matches) == 1:
        return "companion", matches[0]
    return "", ""
def _context_match_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _positive_inventory_quantity(value: Any) -> int | None:
    """接受可可靠解释的正整数数量，不把无效数量降级成无数量物品。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        quantity = int(value)
    except (ValueError, OverflowError):
        return None
    if isinstance(value, float) and value != quantity:
        return None
    return quantity if quantity > 0 else None


def _action_mentions_name(action: str, name: object) -> bool:
    """拉丁名称/ID 要求词边界；中文等名称允许紧邻中文叙述。"""
    if not isinstance(name, str) or not name.strip():
        return False
    name = name.strip().casefold()
    text = action.casefold()

    def identifier_char(char: str) -> bool:
        return (
            char == "_" or char.isdigit()
            or unicodedata.name(char, "").startswith("LATIN ")
            or unicodedata.category(char).startswith("M")
        )

    pattern = r"\s*".join(re.escape(part) for part in name.split())
    for match in re.finditer(pattern, text):
        if identifier_char(name[0]) and match.start() and identifier_char(text[match.start() - 1]):
            continue
        if identifier_char(name[-1]) and match.end() < len(text) and identifier_char(text[match.end()]):
            continue
        return True
    return False


def _item_context(sheet: dict[str, Any], action: str, target: str) -> dict[str, Any]:
    """只读压缩现有物品；partial 表示该清单不能提供物品不存在的证据。"""
    texts = [_context_match_text(action), _context_match_text(target)]
    candidates: list[tuple[int, dict[str, Any]]] = []
    partial = False
    for source_index, source in enumerate(("equipment", "key_items", "inventory")):
        entries = sheet.get(source)
        if not isinstance(entries, list):
            partial = True
            continue
        for entry in entries:
            row: dict[str, Any] = {"source": source}
            if isinstance(entry, str) and entry.strip():
                row["name"] = entry.strip()
            elif isinstance(entry, dict):
                for key in ("name", "item_ref", "type"):
                    value = entry.get(key)
                    if isinstance(value, str) and value.strip():
                        row[key] = value.strip()
                    elif key in entry:
                        partial = True
                quantity_key = "qty" if "qty" in entry else "quantity"
                if quantity_key in entry:
                    quantity = entry[quantity_key]
                    if source == "inventory":
                        quantity = _positive_inventory_quantity(quantity)
                        if quantity is None:
                            partial = True
                            continue
                        row["qty"] = quantity
                    elif type(quantity) is int and quantity >= 0:
                        row["qty"] = quantity
                    else:
                        partial = True
            if not row.get("name") and not row.get("item_ref"):
                partial = True
                continue
            matched = any(
                _context_match_text(row[key]) in text
                for key in ("name", "item_ref") if row.get(key)
                for text in texts
            )
            if source == "inventory" and len(entries) > 20 and not matched:
                partial = True
                continue
            candidates.append((0 if matched else source_index + 1, row))

    result: dict[str, Any] = {"items": [], "partial": partial}
    for _, row in sorted(candidates, key=lambda candidate: candidate[0]):
        result["items"].append(row)
        if len(result["items"]) > 20 or len(json.dumps(
            result, ensure_ascii=False, separators=(",", ":"),
        )) > 1500:
            result["items"].pop()
            result["partial"] = True
    return result


def _npc_context(
    instance: GameInstance, uid: str, action: str, target: str,
) -> dict[str, Any] | None:
    """复用目标解析，只附带明确、无歧义 NPC 的身份和已记录关系。"""
    reference = (
        _match_opponent(instance, target)
        if target.strip() else find_action_opponent(instance, uid, action)
    )
    if not reference.startswith("npc:"):
        return None
    npc_id = reference[4:]
    npc = instance.npcs.get(npc_id)
    if not isinstance(npc, dict):
        return None

    # 原解析器的精确名称分支可能返回首个同名对象；摘要不得据此消除歧义。
    aliases = {
        key: {
            value.strip().casefold()
            for value in (key, record.get("name"), record.get("character_name"))
            if isinstance(value, str) and value.strip()
        }
        for key, record in instance.npcs.items() if isinstance(record, dict)
    }
    if target.strip():
        query = target.strip().casefold()
        matches = {key for key, names in aliases.items() if query in names}
        if not matches and len(query) >= 2:
            matches = {
                key for key, names in aliases.items()
                if any(query in name or name in query for name in names)
            }
    else:
        # 同时提到其他玩家，也无法仅凭名字判断谁是受话者。
        if any(
            _action_mentions_name(action, name)
            for player_id, player in instance.players.items() if player_id != uid
            for name in (player_id, player.get("character_name"))
        ):
            return None
        matches = {
            key
            for key, names in aliases.items()
            if any(_action_mentions_name(action, name) for name in names)
        }
    if matches != {npc_id}:
        return None

    name = next((
        value.strip() for value in (npc.get("name"), npc.get("character_name"), npc_id)
        if isinstance(value, str) and value.strip()
    ), npc_id)
    result = {"reference": reference, "name": name}
    relation = npc.get("relation")
    if isinstance(relation, str) and relation.strip():
        result["relation"] = relation
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > 400:
        result.pop("relation", None)
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > 400:
        return None
    return result


def _planner_context(instance: GameInstance, rule: RuleSystem | None) -> str:
    players = []
    for action in instance.action_queue:
        uid = str(action.get("user_id") or "")
        if uid not in instance.players:
            continue
        if not is_conscious(instance.get_character_sheet(uid)):
            continue
        sheet = instance.get_character_sheet(uid)
        action_text = str(action.get("text") or "")[:1000]
        target_text = str(action.get("target_text") or "")
        selected_skill = str(action.get("selected_skill") or "")
        players.append({
            "player_id": uid,
            "character_name": instance.players[uid].get("character_name") or uid,
            "action": action_text,
            "attributes": sheet.get("attributes", {}),
            "skills": _skill_rows(
                sheet, action_text, selected_skill,
                include_matching_effects=True,
            ),
            "selected_attribute": str(action.get("selected_attribute") or ""),
            "selected_skill": selected_skill,
            "target_text": target_text,
            "item_context": _item_context(sheet, action_text, target_text),
        })
        npc_context = _npc_context(instance, uid, action_text, target_text)
        if npc_context is not None:
            players[-1]["npc_context"] = npc_context
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
    # 经济报价协议：模型只上报「十进制金额 + canonical 单位 id」，换算由服务端
    # CurrencyCodec 完成；这里提供当前规则允许的全部货币单位。
    currency_spec = rule.currency_spec if rule else legacy_currency_spec("")
    ruleset = {
        "id": instance.rule_id,
        "dice_system": dice_system,
        "mechanic": mechanic,
        "attributes": attributes,
        # 没有规则集时也要与通用 d20 尺度一致（base_d20/dnd5e 都是 10/15/20/25）；
        # prompt 要求档位以本表为准，这里再给一套 8/10/15 会让模型收到互相矛盾的数据。
        "dc_table": rule.dc_table if rule else {
            "easy": 10, "normal": 15, "hard": 20, "extreme": 25,
        },
        "target_policy": (
            "server_uses_character_sheet_percentile"
            if dice_system == "d100"
            else "gm_supplies_situational_dc"
        ),
        "currency_units": [
            {
                "id": unit.id,
                "name": unit.name,
                "rate": unit.rate,
                **({"symbol": unit.symbol} if unit.symbol else {}),
            }
            for unit in currency_spec.units
        ],
        "currency_display_unit": currency_spec.display_unit,
    }
    if dice_system == "d20":
        ruleset["max_check_dc"] = d20_dc_cap(rule)
    else:
        if rule is not None and rule.percentile_difficulty_levels:
            ruleset["difficulty_levels"] = True
        if rule is not None and not rule.situational_modifier_allowed:
            ruleset["situational_modifier"] = False
        if rule is not None and rule.percentile_stat_checks:
            ruleset["stat_checks"] = rule.percentile_stat_checks
        if rule is not None and rule.idea_roll_attribute:
            ruleset["idea_roll"] = True
        # d100 的阈值来自角色卡，d20 的 DC 档位对它毫无意义，只会误导模型去填 target。
        ruleset.pop("dc_table", None)
    payload = {
        "round": progression_state.round_value(instance),
        "scene": str(module_hooks.narrative_view(instance)["scene"] or "")[:500],
        "recent_narration": [
            sanitize_narration(str(entry.get("gm_response") or ""))[:1000]
            for entry in instance.log[-2:]
            if entry.get("gm_response")
        ],
        "difficulty": game_config.difficulty(instance),
        "ruleset": ruleset,
        "players": players,
        # AI 队友作为可选检定主体：模型只负责选"谁执行/什么检定"，
        # 数值仍由服务器角色卡决定。
        "companions": [
            {
                "actor_ref": f"companion:{companion_id}",
                "character_name": str(companion.get("name") or companion_id),
                "attributes": _companion_sheet(instance, companion_id).get("attributes", {}),
                "skills": _skill_rows(_companion_sheet(instance, companion_id)),
            }
            for companion_id, companion in sorted(_companion_roster(instance).items())
        ],
        "recent_purchases": _recent_purchases(instance),
        "recent_checks": _recent_checks(instance),
    }
    # 权威世界真相（Issue #284）：只给模型 canonical 事实与取值，让它能用稳定的
    # 地点 id 提出 world_requirements；玩家可见投影走 project_visible_state。
    visible_world = project_visible_state(instance, viewer_is_gm=True)
    if visible_world["facts"]:
        payload["world_state"] = {
            "revision": visible_world["revision"],
            "clock": visible_world["clock"],
            "facts": {
                key: fact.get("value")
                for key, fact in visible_world["facts"].items()
            },
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
