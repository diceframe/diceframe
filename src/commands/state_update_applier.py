"""LLM state_update 应用器。

从 game_handler 拆出的场景/战利品状态写入逻辑；
玩家字段更新拆到 player_state_applier，NPC 状态拆到 npc_state_applier，
战利品分类规则加载拆到 item_category_resolver，疯狂状态倒计时拆到 madness_tracker。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from uuid import uuid4

from src.engine.game_instance import GameInstance
from src.commands.item_category_resolver import ItemCategoryResolver
from src.commands.madness_tracker import MadnessTracker
from src.commands.npc_state_applier import NpcStateApplier
from src.commands.player_state_applier import PlayerStateApplier
from src.commands.state_items import (
    append_inventory_item,
    append_key_item,
    append_unique_equipment,
    classify_item,
)


class StateUpdateApplier:
    """将 LLM 输出的 state_update 应用到游戏状态。"""

    def __init__(
        self,
        rules_dir: Path,
        worlds_dir: Path | None,
        load_world_template: Callable[[str], dict],
    ):
        self._madness = MadnessTracker()
        self._players = PlayerStateApplier(self._madness)
        self._npcs = NpcStateApplier()
        self._item_cats = ItemCategoryResolver(rules_dir, worlds_dir, load_world_template)

    def apply_state_update(self, instance: GameInstance, update: dict) -> None:
        """将 LLM 输出的 state_update 应用到游戏状态。"""
        # 玩家状态更新
        self._players.apply_players(instance, update.get("players", {}))

        # NPC 状态更新
        self._npcs.apply_npcs(instance, update.get("npcs", {}))

        # 场景变换
        scene_change = update.get("scene_change")
        if scene_change:
            instance.scene = scene_change

        # 战利品 - 按规则 JSON 的 item_categories 智能分类；规则未定义时用内置回退
        rule_cats = self._item_cats.load_categories(instance)

        for loot in update.get("loot", []):
            uid = loot.get("player", "")
            item_name = loot.get("item", "")
            if uid not in instance.players:
                continue
            cs = instance.get_character_sheet(uid)
            # 遍历所有品类关键字匹配
            category = loot.get("category") or classify_item(item_name, rule_cats)
            if category == "equipment":
                append_unique_equipment(cs, item_name)
            elif category in ("key_item", "quest", "clue", "credential", "artifact"):
                append_key_item(cs, item_name, category=category)
            elif category == "cyberware":
                cw = cs.setdefault("cyberware", [])
                if not any(item.get("name") == item_name for item in cw):
                    cw.append({"name": item_name, "effect": ""})
            elif category in ("pills",):
                append_inventory_item(cs, item_name, category="丹药")
            else:
                append_inventory_item(cs, item_name)

        # 待确认支付（PAY tag 不直接扣金币，转入 pending 等玩家确认）
        for pay in update.get("pending_payments", []):
            uid = pay.get("uid", "")
            amount = int(pay.get("amount", 0) or 0)
            if not uid or amount <= 0 or uid not in instance.players:
                continue
            instance.pending_payments.append({
                "id": f"pay_{instance.round_number}_{uid}_{uuid4().hex[:8]}",
                "uid": uid,
                "amount": amount,
                "reason": pay.get("reason", "GM 建议支付"),
                "status": "pending",
                "round": instance.round_number,
            })

    def apply_madness(self, instance: GameInstance, uid: str, cs: dict, loss: int) -> None:
        """兼容旧内部调用；实际逻辑已拆到 MadnessTracker。"""
        self._madness.apply_madness(instance, uid, cs, loss)

    def tick_madness(self, instance: GameInstance) -> None:
        """兼容旧内部调用；实际逻辑已拆到 MadnessTracker。"""
        self._madness.tick_madness(instance)
