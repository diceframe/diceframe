"""CoC 7e 规则层：d100 成功等级、奖励骰/惩罚骰。

只消费 dice_rng 的真实骰值；成功等级判定唯一入口为 coc_success_level，
避免同一引擎里出现两套互相矛盾的 CoC 判定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.engine.dice_rng import MAX_DICE_COUNT, DiceResult, parse_dice_formula, roll, roll_die
from src.localization.messages import t

# 理智检定中算作「成功」的等级；coc_success_level 不会返回笼统的「成功」。
SANITY_SUCCESS_VERDICTS = frozenset({"普通成功", "困难成功", "极难成功", "大成功"})
MAX_SANITY_FIXED_LOSS = 100


def coc_success_level(roll_value: int, threshold: int) -> str:
    """CoC 7e 风格成功等级。"""
    if roll_value <= 1:
        return "大成功"
    if roll_value >= 100 or (threshold < 50 and roll_value >= 96):
        return "大失败"
    if roll_value > threshold:
        return "失败"
    if roll_value <= threshold // 5:
        return "极难成功"
    if roll_value <= threshold // 2:
        return "困难成功"
    return "普通成功"


def check_coc(threshold: int) -> tuple[DiceResult, str]:
    """CoC 7e 风格 d100 检定，返回成功等级。"""
    threshold = max(1, min(99, int(threshold)))
    result = roll("d100")
    verdict = coc_success_level(result.natural, threshold)
    result.is_critical = verdict == "大成功"
    result.is_fumble = verdict == "大失败"
    return result, verdict


def check_d100(threshold: int) -> tuple[DiceResult, str]:
    """d100 技能检定（掷 d100 ≤ skill 值 = 成功，用于 CoC 类规则）。

    与 coc_success_level 统一，避免同一引擎里两套互相矛盾的 CoC 判定。
    """
    return check_coc(threshold)


def check_d100_bonus(threshold: int, bonus_dice: int = 0, penalty_dice: int = 0) -> tuple[DiceResult, str]:
    """CoC 7e 奖励骰/惩罚骰。

    bonus_dice: 奖励骰数量（掷多个十位骰，取最终结果更低者）
    penalty_dice: 惩罚骰数量（掷多个十位骰，取最终结果更高者）
    """
    if bonus_dice < 0 or penalty_dice < 0:
        raise ValueError(t("engine.dice_rules_coc.bonus_penalty_negative"))
    if bonus_dice > MAX_DICE_COUNT or penalty_dice > MAX_DICE_COUNT:
        raise ValueError(t("engine.dice_rules_coc.bonus_penalty_too_many", max_dice_count=MAX_DICE_COUNT))
    if bonus_dice and penalty_dice:
        cancel_count = min(bonus_dice, penalty_dice)
        bonus_dice -= cancel_count
        penalty_dice -= cancel_count
    threshold = max(1, min(99, int(threshold)))
    units = roll_die(10, zero_based=True)
    extra_count = max(bonus_dice, penalty_dice)
    all_tens = [roll_die(10, zero_based=True) * 10 for _ in range(1 + extra_count)]
    # 先构造每个组合的最终值（00+0 = 100），再按奖惩取最高/最低；
    # 旧实现先对十位取 min/max 再转换 100，会把奖惩结果完全反转。
    candidates = [100 if (ten + units) == 0 else ten + units for ten in all_tens]
    total = max(candidates) if penalty_dice > 0 else min(candidates)
    verdict = coc_success_level(total, threshold)
    # rolls 保存所有共享个位数组合的最终候选值，便于 UI/审计证明奖惩骰
    # 确实是“同一个位 + 多个十位”，而不是简单掷两次完整 d100。
    result = DiceResult(formula="d100", rolls=candidates, modifier=0,
                        total=total, natural=total,
                        is_critical=(verdict == "大成功"), is_fumble=(verdict == "大失败"))
    return result, verdict


def project_percentile_dice(candidates: list[int], chosen: int, mode: str) -> dict:
    """Project rule-owned faces; never present independent d100s as shared units."""
    if not candidates or chosen not in candidates or any(type(value) is not int or not 1 <= value <= 100 for value in candidates):
        raise ValueError("invalid percentile candidates")
    units = candidates[0] % 10
    if any(value % 10 != units for value in candidates):
        raise ValueError("CoC candidates must use shared units")
    if len(candidates) > 1 and mode not in {"advantage", "disadvantage"}:
        raise ValueError("extra percentile candidates require a bonus or penalty mode")
    return {"units": units, "tens": [(value % 100) // 10 * 10 for value in candidates],
            "candidates": list(candidates), "taken_index": candidates.index(chosen),
            "bonus_dice": len(candidates) - 1 if mode == "advantage" else 0,
            "penalty_dice": len(candidates) - 1 if mode == "disadvantage" else 0}


@dataclass(frozen=True)
class SanityLoss:
    """一次理智检定的损失写法。

    ``split`` 为真时是 7e 标准的「成功损失/失败损失」（如 ``0/1d6``）；
    为假时是旧写法的单一损失骰（如 ``1d6``），成功时损失减半向上取整。
    """

    success: str
    failure: str
    split: bool

    @property
    def text(self) -> str:
        return f"{self.success}/{self.failure}" if self.split else self.failure


def _sanity_loss_term(term: str) -> str:
    if re.fullmatch(r"\d{1,3}", term):
        value = int(term)
        if value > MAX_SANITY_FIXED_LOSS:
            raise ValueError(t("engine.dice_rules_coc.sanity_loss_out_of_range", term=term))
        return str(value)
    return parse_dice_formula(term)[0]


def parse_sanity_loss(raw: Any) -> SanityLoss:
    """解析理智损失写法；无法识别时抛 ValueError，且不消耗任何随机数。"""
    text = str(raw or "").strip().lower().replace(" ", "").replace("／", "/")
    if not text:
        raise ValueError(t("engine.dice_rules_coc.sanity_loss_empty"))
    if "/" in text:
        success, failure = text.split("/", 1)
        if not success or not failure or "/" in failure:
            raise ValueError(t("engine.dice_rules_coc.sanity_loss_invalid", raw=raw))
        return SanityLoss(_sanity_loss_term(success), _sanity_loss_term(failure), True)
    term = _sanity_loss_term(text)
    return SanityLoss(term, term, False)


def _roll_sanity_loss(term: str, *, maximum: bool = False) -> tuple[int, list[int]]:
    if term.isdigit():
        return int(term), []
    _formula, count, sides, modifier = parse_dice_formula(term)
    if maximum:
        return max(0, count * sides + modifier), []
    result = roll(term)
    return max(0, result.total), list(result.rolls)


def resolve_sanity_check(current_sanity: int, loss: SanityLoss) -> dict[str, Any]:
    """按 CoC 7e 结算一次理智检定：d100 对当前理智值。

    - 任何等级的成功：成功损失（旧单骰写法为损失减半向上取整）；
    - 失败：失败损失；
    - 大失败：失败损失可能的最大值。
    """
    threshold = max(1, min(99, int(current_sanity)))
    result, verdict = check_coc(threshold)
    if verdict in SANITY_SUCCESS_VERDICTS:
        if loss.split:
            amount, loss_rolls = _roll_sanity_loss(loss.success)
        else:
            full, loss_rolls = _roll_sanity_loss(loss.failure)
            amount = (full + 1) // 2
    elif verdict == "大失败":
        amount, loss_rolls = _roll_sanity_loss(loss.failure, maximum=True)
    else:
        amount, loss_rolls = _roll_sanity_loss(loss.failure)
    return {
        "roll": result.natural,
        "threshold": threshold,
        "verdict": verdict,
        "loss": amount,
        "loss_spec": loss.text,
        "loss_rolls": loss_rolls,
    }


# ---- 难度档位与对抗检定 ----

DIFFICULTY_LEVELS = ("regular", "hard", "extreme")
_SUCCESS_RANK = {"大失败": -1, "失败": 0, "普通成功": 1, "困难成功": 2, "极难成功": 3, "大成功": 4}
_REQUIRED_RANK = {"regular": 1, "hard": 2, "extreme": 3}
REQUIRED_LEVEL_VERDICT = {"regular": "普通成功", "hard": "困难成功", "extreme": "极难成功"}


def normalize_difficulty(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in DIFFICULTY_LEVELS else "regular"


def required_threshold(threshold: int, difficulty: str) -> int:
    """达到所需档位时骰值不能超过的上限：常规=技能值，困难=一半，极难=五分之一。"""
    value = int(threshold)
    if difficulty == "hard":
        return value // 2
    if difficulty == "extreme":
        return value // 5
    return value


def success_rank(verdict: str) -> int:
    return _SUCCESS_RANK.get(str(verdict or ""), 0)


def apply_required_level(verdict: str, difficulty: str) -> str:
    """成功但没达到所需档位时判为失败；大成功、失败、大失败保持原样。"""
    rank = success_rank(verdict)
    if 1 <= rank < _REQUIRED_RANK.get(difficulty, 1):
        return "失败"
    return verdict


def opposed_outcome(actor_verdict: str, actor_threshold: int,
                    opponent_verdict: str, opponent_threshold: int) -> str:
    """CoC 7e 对抗：成功等级高者胜；等级相同则技能值高者胜；仍相同为平局。

    双方都失败时没有人胜出，返回 ``"tie"``（维持现状）。
    """
    actor_rank = max(0, success_rank(actor_verdict))
    opponent_rank = max(0, success_rank(opponent_verdict))
    if actor_rank == 0 and opponent_rank == 0:
        return "tie"
    if actor_rank != opponent_rank:
        return "win" if actor_rank > opponent_rank else "lose"
    if int(actor_threshold) != int(opponent_threshold):
        return "win" if int(actor_threshold) > int(opponent_threshold) else "lose"
    return "tie"
