"""战利品物品分类规则加载。

从 state_update_applier 拆出的物品分类表加载逻辑：
按世界规则 JSON 的 item_categories 加载，规则未定义时回退内置表。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem

# 兜底分类表：覆盖常见武器/防具/消耗品/杂项，
# 避免「步兵长剑」「硬牛皮甲」这种装备被堆到道具栏（issue #9）
_DEFAULT_ITEM_CATEGORIES: dict[str, list[str]] = {
    "equipment": [
        "剑", "刀", "弓", "弩", "法杖", "匕首", "弯刀", "巨剑", "长矛",
        "矛", "钉头锤", "锤", "战锤", "斧", "盾", "皮甲", "链甲", "板甲",
        "袍", "靴", "手套", "头盔", "披风", "护腕", "项链", "戒指",
        "铠甲", "护甲", "胸甲", "腿甲", "臂甲", "护肩", "甲",
    ],
    "consumable": [
        "药水", "卷轴", "药剂", "干粮", "绷带", "解毒剂", "炸弹",
        "符咒", "火把", "箭矢", "弩箭", "口粮", "水壶",
    ],
    "misc": [
        "石头", "宝石", "水晶", "背包", "绳索", "灯笼", "金币",
    ],
    "key_item": [
        "钥匙", "地图", "信", "信件", "令牌", "凭证", "通行证",
        "徽章", "笔记", "手稿", "档案", "线索", "契约", "访问卡",
    ],
}


class ItemCategoryResolver:
    """按世界规则加载物品分类表，规则未定义时回退内置表。"""

    def __init__(
        self,
        rules_dir: Path,
        worlds_dir: Path | None,
        load_world_template: Callable[[str], dict],
    ):
        self.rules_dir = rules_dir
        self.worlds_dir = worlds_dir
        self._load_world_template = load_world_template

    def load_categories(self, instance: GameInstance) -> dict[str, list[str]]:
        rule_cats: dict[str, list[str]] = {}
        try:
            if instance.world_id and self.worlds_dir:
                world_data = self._load_world_template(instance.world_id)
                if world_data:
                    rule = RuleSystem.load_for_world(world_data, self.rules_dir)
                    if rule:
                        rule_cats = rule.item_categories
        except Exception:
            logger.warning("物品分类表加载失败，回退内置表: world_id=%s", instance.world_id, exc_info=True)
            pass

        if not rule_cats:
            rule_cats = _DEFAULT_ITEM_CATEGORIES
        return rule_cats
