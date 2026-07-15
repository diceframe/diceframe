"""游戏总览与管理服务：列表 / 详情 / 创建 / 重开 / 切换世界。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.engine.game_instance import GameState
from src.engine.game_instance import restore_players
from src.engine.dice import roll
from src.engine.health import health_payload, mark_health_event, record_health_event
from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.rules.rule_system import RuleSystem

from src.webui.services._common import _GAME_KEY_SEP, _is_safe_world_id

if TYPE_CHECKING:
    from src.webui.api import WebAPI

logger = logging.getLogger("trpg")


def list_games(api: "WebAPI") -> dict[str, Any]:
    active = []
    for inst in api._reg.list_all():
        active.append({
            "game_key": _GAME_KEY_SEP.join(inst.game_key),
            "world_id": inst.world_id,
            "world_name": inst.world_name,
            "group_name": inst.group_name,
            "state": inst.state.value,
            "round_number": inst.round_number,
            "player_count": len(inst.players),
            "max_players": 6,
            "combat_active": inst.combat_active,
            "scene": inst.scene,
            "total_llm_calls": inst.total_llm_calls,
            "total_tokens": inst.total_tokens,
            "last_activity": inst.last_activity,
            "seed_code": inst.seed_code,
            "language": normalize_language(getattr(inst, "language", DEFAULT_LANGUAGE)),
            "solo_mode": inst.solo_mode,
            "gm_uid": inst.gm_uid or "",
            "ready_count": inst.multiplayer_status()["ready_count"],
            "alive_count": inst.multiplayer_status()["alive_count"],
        })
    return {"games": active, "total": len(active)}


def game_detail(api: "WebAPI", game_key: str) -> dict[str, Any] | None:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return None
    return {
        "game_key": _GAME_KEY_SEP.join(inst.game_key),
        "world_id": inst.world_id or "",
        "world_name": inst.world_name, "group_name": inst.group_name,
        "state": inst.state.value, "round_number": inst.round_number,
        "player_count": len(inst.players), "scene": inst.scene,
        "total_llm_calls": inst.total_llm_calls,
        "total_tokens": inst.total_tokens,
        "started_at": inst.started_at, "last_activity": inst.last_activity,
        "seed_code": inst.seed_code,
        "language": normalize_language(getattr(inst, "language", DEFAULT_LANGUAGE)),
        "gm_uid": inst.gm_uid or "",
        "player_access_open": bool(getattr(inst, "player_access_open", True)),
        "has_room_password": bool(getattr(inst, "room_password", "")),
        "quick_actions": getattr(inst, "quick_actions", []),
        "pending_payments": [
            p for p in getattr(inst, "pending_payments", [])
            if p.get("status") == "pending"
        ],
        "difficulty": inst.difficulty,
        "solo_mode": inst.solo_mode,
        "max_players": inst.max_players,
        "multiplayer": inst.multiplayer_status(),
        "plot_tracker": inst.plot_tracker.to_dict() if inst.plot_tracker else None,
        "recap": _public_recap(inst),
    }


def _clean_public_narration(text: str) -> str:
    """只取公开叙事段，去掉 --- 后面的结构化标签，不截断长度。"""
    return str(text or "").split("\n---", 1)[0].strip()


def _public_recap(inst) -> dict[str, Any]:
    """给网页/群机器人使用的公开前情提要，不包含 private_log。

    pending_actions 是尚未推进的玩家行动（action_queue），供群机器人实时转发；
    每个 action 带 signature（user_id+timestamp），供跨 pending/recent 去重。
    """
    def _action_view(action: dict) -> dict[str, Any] | None:
        if not isinstance(action, dict):
            return None
        uid = str(action.get("user_id") or "")
        if uid == "system":
            return None
        name = inst.players.get(uid, {}).get("character_name") or uid or "冒险者"
        text = str(action.get("text") or "").strip()
        if not text:
            return None
        signature = f"{uid}:{action.get('timestamp') or text[:32]}"
        return {
            "character_name": name,
            "text": text,
            "signature": signature,
            "source": str(action.get("source") or ""),
            "dice_pending": bool(action.get("dice_pending")),
            "dice_roll_source": str(action.get("dice_roll_source") or ""),
        }

    recent_rounds: list[dict[str, Any]] = []
    for entry in (inst.log or [])[-3:]:
        actions = [view for a in entry.get("actions", []) or [] if (view := _action_view(a))]
        recent_rounds.append({
            "round": entry.get("round", "?"),
            "actions": actions,
            "gm_response": _clean_public_narration(entry.get("gm_response", "")),
            "state_changes": list(entry.get("state_changes", []) or []),
        })
    pending_actions = [view for a in (inst.action_queue or []) if (view := _action_view(a))]
    return {
        "narrative": str((getattr(inst, "summary", {}) or {}).get("narrative") or "").strip(),
        "key_facts": list(getattr(inst, "key_facts", []) or [])[-8:],
        "recent_rounds": recent_rounds,
        "pending_actions": pending_actions,
        "current_scene": inst.scene,
        "round_number": inst.round_number,
    }


def multiplayer_status(api: "WebAPI", game_key: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    return {"ok": True, **inst.multiplayer_status()}


async def set_player_away(api: "WebAPI", game_key: str, user_id: str, away: bool) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if user_id not in inst.players:
        return {"ok": False, "error": "玩家不存在"}
    ok = await inst.set_player_away(user_id, away)
    if not ok:
        return {"ok": False, "error": "无法切换该玩家状态"}
    await api._reg.save(inst)
    return {
        "ok": True,
        "user_id": user_id,
        "character_name": inst.players.get(user_id, {}).get("character_name") or user_id,
        "away": bool(away),
        "multiplayer": inst.multiplayer_status(),
    }


async def set_player_access(api: "WebAPI", game_key: str, open_access: bool) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    inst.player_access_open = bool(open_access)
    await api._reg.save(inst)
    return {"ok": True, "player_access_open": inst.player_access_open}


def roll_for_game(api: "WebAPI", game_key: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    rule = api._load_rule_for_game(inst)
    dice_system = str(rule.dice_system if rule else "d20").lower()
    if dice_system == "none":
        return {"ok": False, "error": "当前规则不需要掷骰"}
    formula = "d100" if dice_system == "d100" else "d20"
    result = roll(formula)
    return {
        "ok": True,
        "dice_system": formula,
        "value": result.natural,
        "critical": result.is_critical,
        "fumble": result.is_fumble,
    }


async def resolve_pending_dice_for_game(
    api: "WebAPI",
    game_key: str,
    user_id: str = "",
    source: str = "system",
) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    pending = inst.pending_dice_actions(user_id or None)
    if not pending:
        return {"ok": True, "resolved": []}
    resolved: list[dict[str, Any]] = []
    for action in pending:
        uid = str(action.get("user_id") or "")
        dice_system = str(action.get("dice_system") or "").lower()
        if not dice_system:
            rule = api._load_rule_for_game(inst)
            dice_system = str(rule.dice_system if rule else "d20").lower()
        if dice_system == "none":
            continue
        formula = "d100" if dice_system == "d100" else "d20"
        result = roll(formula)
        applied = await inst.apply_action_roll(uid, formula, result.natural, source=source)
        if not applied:
            continue
        payload = {
            "ok": True,
            "user_id": uid,
            "dice_system": formula,
            "value": result.natural,
            "critical": result.is_critical,
            "fumble": result.is_fumble,
            "source": source,
        }
        resolved.append(payload)
    return {"ok": True, "resolved": resolved, "roll": resolved[0] if resolved else None}


def private_log(api: "WebAPI", game_key: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}

    return {"ok": True, "messages": _private_log_messages(inst)[-50:]}


def private_log_for_user(api: "WebAPI", game_key: str, user_id: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if user_id not in inst.players:
        return {"ok": False, "error": "玩家不存在"}
    return {"ok": True, "messages": _private_log_messages(inst, user_id)[-50:]}


def _private_log_messages(inst, only_user_id: str = "") -> list[dict[str, Any]]:
    def player_name(uid: str) -> str:
        return inst.players.get(uid, {}).get("character_name") or uid

    messages: list[dict[str, Any]] = []
    for uid, items in (inst.private_log or {}).items():
        if only_user_id and uid != only_user_id:
            continue
        for item in items or []:
            messages.append({
                "user_id": uid,
                "character_name": player_name(uid),
                "round": item.get("round", 0),
                "text": item.get("text", ""),
                "source": item.get("source", "system"),
                "timestamp": item.get("timestamp", ""),
            })
    messages.sort(key=lambda x: (int(x.get("round", 0) or 0), str(x.get("timestamp", ""))))
    return messages


def game_health(api: "WebAPI", game_key: str, include_resolved: bool = False) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "game not found"}
    return health_payload(inst, include_resolved)


async def set_solo_mode(api: "WebAPI", game_key: str, solo: bool) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    inst.solo_mode = bool(solo)
    if inst.solo_mode and inst.action_queue and inst.state == GameState.ACTIVE_ACTION:
        for uid in inst.alive_players:
            inst.ready_players.add(uid)
    await api._reg.save(inst)
    return {"ok": True, "solo_mode": inst.solo_mode, "multiplayer": inst.multiplayer_status()}


async def mark_game_health_event(
    api: "WebAPI",
    game_key: str,
    event_id: str,
    *,
    resolved: bool = False,
    ignored: bool = False,
) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "game not found"}
    if not mark_health_event(inst, event_id, resolved=resolved, ignored=ignored):
        return {"ok": False, "error": "health event not found"}
    await api._reg.save(inst)
    return {"ok": True, "event_id": event_id, "resolved": resolved, "ignored": ignored}


async def rollback_round(api: "WebAPI", game_key: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if not inst.log:
        return {"ok": False, "error": "没有可撤回的上一轮"}
    last = inst.log.pop()
    snapshot = last.get("pre_state_snapshot", {})
    if isinstance(snapshot, dict) and snapshot:
        restore_players(inst, snapshot)
    inst.round_number = max(1, int(last.get("round", inst.round_number) or 1))
    inst.action_queue.clear()
    inst.pending_actions.clear()
    inst.ready_players.clear()
    inst.pending_payments.clear()
    inst.state = GameState.ACTIVE_ACTION
    inst.last_activity = datetime.now(timezone.utc).isoformat()
    record_health_event(
        inst,
        component="gm_control",
        code="GM_ROLLBACK",
        severity="info",
        title="GM 回退",
        message=f"已撤回到第 {inst.round_number} 轮开始前的玩家状态。",
        impact="上一轮叙事日志已移除，玩家公开状态已恢复到快照。",
        fallback="rollback_snapshot",
        repair_hint="如果仍不满意，可继续用 GM 指令修正下一次判定。",
    )
    await api._reg.save(inst)
    return {"ok": True, "message": f"已撤回到第 {inst.round_number} 轮开始前的玩家状态"}


async def gm_command(api: "WebAPI", game_key: str, command: str, mode: str = "note") -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    command = (command or "").strip()
    if mode == "rollback":
        return await rollback_round(api, game_key)
    if not command:
        return {"ok": False, "error": "请输入 GM 指令"}

    entry = {
        "user_id": "system",
        "text": f"【GM指令】{command}\n要求：下一次判定必须优先遵循这条 GM 指令；如果它修正了金币、道具、剧情方向或死亡风险，请按修正后的设定继续。",
        "timestamp": time.time(),
        "selected_attribute": "",
        "selected_skill": "",
        "target_text": "",
    }
    if inst.state == GameState.ACTIVE_ACTION:
        inst.action_queue.append(entry)
    else:
        inst.pending_actions.append(entry)
    record_health_event(
        inst,
        component="gm_control",
        code="GM_COMMAND",
        severity="info",
        title="GM 修正指令",
        message=command,
        impact="下一次判定会把这条 GM 指令作为系统行动纳入上下文。",
        fallback="queued_action",
        repair_hint="如果指令写错，可撤回上一轮或追加新的 GM 修正指令覆盖。",
    )
    await api._reg.save(inst)
    return {"ok": True, "message": "GM 指令已加入下一次判定"}


async def gm_private_message(api: "WebAPI", game_key: str, user_id: str, text: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    user_id = (user_id or "").strip()
    text = (text or "").strip()
    if user_id not in inst.players:
        return {"ok": False, "error": "目标玩家不存在"}
    if not text:
        return {"ok": False, "error": "请输入悄悄话内容"}
    inst.private_log.setdefault(user_id, []).append({
        "round": inst.round_number,
        "text": text,
        "source": "gm",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    await api._reg.save(inst)
    return {"ok": True, "message": "悄悄话已发送"}


async def create_game(api: "WebAPI", world_id: str, game_name: str = "",
                      group_name: str = "Web端", rule_id: str = "",
                      solo: bool = False,
                      lorebook_world_id: str = "",
                      difficulty: str = "标准",
                      description: str = "",
                      create_lorebook: bool = False,
                      blank_lorebook: bool = False,
                      source_world_id: str = "",
                      players: list[dict] | None = None,
                      custom_world: bool = False,
                      gm_uid: str = "",
                      room_password: str = "",
                      language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    if not api._handler or not api._reg:
        return {"ok": False, "error": "系统未就绪"}
    if not _is_safe_world_id(world_id):
        return {"ok": False, "error": "非法 world_id"}
    if source_world_id and not _is_safe_world_id(source_world_id):
        return {"ok": False, "error": "非法 source_world_id"}
    if not players:
        return {"ok": False, "error": "请至少创建或选择 1 名队伍角色"}

    unique_id = f"{world_id}_{time.time_ns()}"
    game_key = ("web", unique_id, "web_bot")

    instance = api._reg.get(game_key)
    if instance and instance.state not in (GameState.CREATED, GameState.ENDED):
        return {"ok": False, "error": "该世界已有进行中的游戏"}
    resolved_world_name = game_name or world_id
    resolved_language = normalize_language(language)

    # 自定义世界：既要在 lorebook 库里建空世界书，也要在 templates/worlds 下
    # 写一份最小模板 JSON。否则 process_round 的 _load_world_template 拿不到
    # 任何数据，会出现「填了唐代仙侠世界，开局却讲克苏鲁、地图是霍华德住所」
    # 这种完全错乱的情况（issue #7/#15）--没有模板意味着规则附录、骰制、
    # item_categories 全部回退到 freeform_fantasy 默认值，LLM 失去世界约束。
    if custom_world or create_lorebook:
        if api._lore and not api._lore.get_world(world_id):
            api._lore.create_world(world_id, resolved_world_name, description=description or "")
            logger.info("已创建自定义世界书: %s", world_id)
        if api._worlds_dir:
            template_path = api._worlds_dir / f"{world_id}.json"
            if not template_path.exists():
                resolved_rule = rule_id or "freeform_fantasy"
                base_template: dict[str, Any] = {}
                if blank_lorebook and source_world_id:
                    source_path = api._worlds_dir / f"{source_world_id}.json"
                    if source_path.exists():
                        try:
                            base_template = json.loads(source_path.read_text(encoding="utf-8"))
                            resolved_rule = rule_id or base_template.get("default_rule", resolved_rule)
                        except Exception:
                            logger.exception("读取空白世界书来源模板失败: %s", source_world_id)
                # 复用对应规则的 item_categories，保证战利品分类不回退
                cats: dict[str, list[str]] = base_template.get("item_categories", {})
                try:
                    rule_path = RuleSystem.path_for(api._rules_dir, resolved_rule)
                    if not cats and rule_path.exists():
                        cats = RuleSystem.load(rule_path).item_categories
                except Exception:
                    logger.exception("读取规则 item_categories 失败: %s", resolved_rule)
                resolved_description = description or base_template.get("description", "")
                min_template = {
                    "world_id": world_id,
                    "world_name": resolved_world_name,
                    "description": resolved_description,
                    "world_setting": description or base_template.get("world_setting", resolved_description),
                    "starter_scene": base_template.get("starter_scene", description[:120] if description else ""),
                    "suggested_difficulty": difficulty,
                    "language": resolved_language,
                    "default_rule": resolved_rule,
                    "starter_lorebook": [],
                }
                if cats:
                    min_template["item_categories"] = cats
                api._worlds_dir.mkdir(parents=True, exist_ok=True)
                template_path.write_text(
                    json.dumps(min_template, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                logger.info("已写入自定义世界模板: %s (rule=%s)", template_path, resolved_rule)

    instance = await api._handler.create_game(
        game_key, world_id=world_id,
        world_name=resolved_world_name, group_name=group_name,
        rule_id=rule_id or "freeform_fantasy",
        language=resolved_language,
    )
    instance.solo_mode = solo
    instance.difficulty = difficulty
    instance.entry_point = "web"
    instance.room_password = room_password or ""

    # 如果指定了外部世界书来源，复制条目
    if lorebook_world_id and lorebook_world_id != world_id and api._lore:
        src_entries = api._lore.list_entries(lorebook_world_id)
        if src_entries:
            dest_world = api._lore.get_world(world_id)
            if not dest_world:
                api._lore.create_world(world_id, resolved_world_name, description=f"来自 {lorebook_world_id}")
            for entry in src_entries:
                new_entry = dict(entry)
                new_entry["id"] = f"{world_id}_{entry['id']}"
                new_entry["world_id"] = world_id
                existing = api._lore.get_entry(new_entry["id"])
                if existing and existing.get("world_id") == world_id:
                    continue
                api._lore.add_entry(new_entry)
            api._handler._last_matcher_world_id = None  # 强制下次重建匹配器
            api._rebuild_lorebook_index(world_id)

    created_players: list[dict[str, Any]] = []
    for idx, character in enumerate(players or []):
        # 第一个角色绑 GM 身份（force_uid=gm_uid），后续角色为 GM 代建（独立 uid）
        if idx == 0 and gm_uid:
            created = await api.create_player(_GAME_KEY_SEP.join(game_key), character, force_uid=gm_uid)
        else:
            created = await api.create_player(_GAME_KEY_SEP.join(game_key), character, assign_new_id=True)
        if created.get("ok"):
            created_players.append(created)
        else:
            return {"ok": False, "error": f"创建角色失败: {created.get('error', '未知错误')}"}

    narration = await api._handler.start_game(instance)
    world_name = instance.world_name

    # GM 严格绑定成功创建的第一个角色；没有角色就没有 GM。
    instance.gm_uid = created_players[0]["user_id"] if created_players else ""
    await api._reg.save(instance)

    return {
        "ok": True,
        "game_key": _GAME_KEY_SEP.join(game_key),
        "world_id": instance.world_id,
        "world_name": world_name,
        "language": normalize_language(instance.language),
        "narration": narration,
        "players": created_players,
        "round_number": instance.round_number,
        "state": instance.state.value,
        "seed_code": instance.seed_code,
    }


async def reset_game(api: "WebAPI", game_key: str) -> dict[str, Any]:
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if not api._handler:
        return {"ok": False, "error": "系统未就绪"}
    inst = await api._handler.reset_game(inst)
    return {
        "ok": True,
        "narration": inst.log[-1].get("gm_response", "") if inst.log else "",
        "seed_code": inst.seed_code,
    }


async def restart_game(api: "WebAPI", game_key: str) -> dict[str, Any]:
    """重开世界：保留角色卡，重置剧情/场景。"""
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if not api._handler:
        return {"ok": False, "error": "系统未就绪"}
    if not inst.players:
        return {"ok": False, "error": "当前游戏没有角色，无法重开；请先创建角色或重新开局"}
    inst = await api._handler.restart_game(inst)
    return {
        "ok": True,
        "narration": inst.log[-1].get("gm_response", "") if inst.log else "",
        "seed_code": inst.seed_code,
    }


async def switch_world(api: "WebAPI", game_key: str, world_id: str) -> dict[str, Any]:
    """切换游戏关联的世界书，保留角色和进度。"""
    inst = api._reg.get(api._parse_key(game_key))
    if not inst:
        return {"ok": False, "error": "游戏不存在"}
    if not _is_safe_world_id(world_id):
        return {"ok": False, "error": "未指定或非法 world_id"}
    world_name = world_id
    if api._worlds_dir:
        try:
            world_data = api._load_world_template(world_id)
        except Exception as e:
            return {"ok": False, "error": f"加载世界失败: {e}"}
        if world_data:
            world_name = world_data.get("world_name", world_id)
        elif api._lore:
            world = api._lore.get_world(world_id)
            if not world:
                return {"ok": False, "error": f"世界 {world_id} 不存在"}
            world_name = world.get("name", world_id)
        else:
            return {"ok": False, "error": f"世界 {world_id} 不存在"}
    inst.world_id = world_id
    inst.world_name = world_name
    if api._handler:
        api._handler._last_matcher_world_id = None
        api._rebuild_lorebook_index(world_id)
    await api._reg.save(inst)
    return {"ok": True, "world_id": inst.world_id, "world_name": inst.world_name}


async def create_from_seed(api: "WebAPI", seed_code: str, solo: bool = False,
                           players: list[dict] | None = None,
                           gm_uid: str = "",
                           language: str = "") -> dict[str, Any]:
    if not api._handler or not api._reg:
        return {"ok": False, "error": "系统未就绪"}
    target_inst = None
    for inst in api._reg.list_all():
        if inst.seed_code == seed_code:
            target_inst = inst
            break
    if not target_inst:
        return {"ok": False, "error": f"未找到重开引用码 '{seed_code}' 对应的游戏，请确认原存档仍然存在"}
    if not players:
        return {"ok": False, "error": "请至少创建或选择 1 名队伍角色"}
    world_id = target_inst.world_id or "default_fantasy"
    world_name = target_inst.world_name
    resolved_language = normalize_language(language or getattr(target_inst, "language", DEFAULT_LANGUAGE))

    unique_id = f"{world_id}_{time.time_ns()}"
    game_key = ("web", unique_id, "web_bot")

    instance = await api._handler.create_game(
        game_key, world_id=world_id, world_name=world_name,
        group_name="Web端", seed_code=seed_code,
        difficulty=target_inst.difficulty,
        language=resolved_language,
    )
    instance.solo_mode = solo
    created_players: list[dict[str, Any]] = []
    for idx, character in enumerate(players or []):
        if idx == 0 and gm_uid:
            created = await api.create_player(_GAME_KEY_SEP.join(game_key), character, force_uid=gm_uid)
        else:
            created = await api.create_player(_GAME_KEY_SEP.join(game_key), character, assign_new_id=True)
        if created.get("ok"):
            created_players.append(created)
        else:
            return {"ok": False, "error": f"创建角色失败: {created.get('error', '未知错误')}"}
    narration = await api._handler.start_game(instance)
    world_name = instance.world_name

    # 与 create_game 一致：首个成功创建的角色拥有 GM 身份。
    instance.gm_uid = created_players[0]["user_id"] if created_players else ""
    await api._reg.save(instance)

    return {
        "ok": True,
        "game_key": _GAME_KEY_SEP.join(game_key),
        "world_id": instance.world_id,
        "world_name": world_name,
        "language": normalize_language(instance.language),
        "narration": narration,
        "players": created_players,
        "seed_code": seed_code,
        "round_number": instance.round_number,
        "state": instance.state.value,
    }
