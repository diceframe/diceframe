"""CoC/理智类疯狂状态处理。"""

from __future__ import annotations

import logging
import random

from src.engine.game_instance import GameInstance

logger = logging.getLogger("trpg")


class MadnessTracker:
    """根据理智损失应用和推进疯狂状态。"""

    def apply_madness(self, instance: GameInstance, uid: str, character_sheet: dict, loss: int) -> None:
        """根据理智值损失量判断疯狂状态。"""
        sanity = character_sheet.get("sanity", 99)
        if sanity <= 0 and not character_sheet.get("deceased"):
            character_sheet["deceased"] = True
            character_sheet["death_cause"] = "永久疯狂"
            character_sheet["death_round"] = instance.round_number
            name = instance.players[uid].get("character_name", uid)
            logger.info("%s 因永久疯狂而不可操作 (round=%d)", name, instance.round_number)
            return
        if loss >= 5:
            name = instance.players[uid].get("character_name", uid)
            if loss >= 10:
                character_sheet["madness"] = {
                    "type": "long_term",
                    "remaining_rounds": random.randint(1, 10),
                    "symptom": "持续行为异常",
                }
                logger.info("%s 进入长期疯狂 (round=%d, rounds=%d)",
                            name, instance.round_number, character_sheet["madness"]["remaining_rounds"])
            else:
                character_sheet["madness"] = {
                    "type": "temporary",
                    "remaining_rounds": random.randint(1, 3),
                    "symptom": "临时行为异常",
                }
                logger.info("%s 进入临时疯狂 (round=%d, rounds=%d)",
                            name, instance.round_number, character_sheet["madness"]["remaining_rounds"])

    def tick_madness(self, instance: GameInstance) -> None:
        """每轮结束时减少疯狂状态回合数。"""
        for uid, player in instance.players.items():
            character_sheet = player.get("character_sheet", {})
            madness = character_sheet.get("madness")
            if not madness or madness.get("type") is None:
                continue
            madness["remaining_rounds"] = madness.get("remaining_rounds", 0) - 1
            name = player.get("character_name", uid)
            if madness["remaining_rounds"] <= 0:
                logger.info("%s 疯狂状态结束: type=%s", name, madness.get("type"))
                character_sheet["madness"] = {"type": None, "remaining_rounds": 0, "symptom": ""}
            else:
                logger.debug("%s 疯狂状态剩余 %d 轮", name, madness["remaining_rounds"])
