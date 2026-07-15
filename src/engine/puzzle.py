"""环境谜题系统 —— 谜题的生成、激活、解法校验和结果处理。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("trpg")

PUZZLE_TYPES = ("riddle", "mechanism", "trap", "social", "combat_puzzle")
PUZZLE_STATES = ("dormant", "discovered", "active", "solved", "failed", "bypassed")


class PuzzleType(Enum):
    RIDDLE = "riddle"           # 字面谜题，需特定回答
    MECHANISM = "mechanism"     # 物理机关，需技能检定
    TRAP = "trap"              # 环境陷阱，需侦查/解除
    SOCIAL = "social"          # 社交挑战，需说服/威吓/欺骗
    COMBAT_PUZZLE = "combat_puzzle"  # 战斗谜题（击败特定敌人或触发条件）


class PuzzleState(Enum):
    DORMANT = "dormant"        # 未被发现
    DISCOVERED = "discovered"  # 已发现但未激活
    ACTIVE = "active"          # 激活中，等待玩家解决
    SOLVED = "solved"          # 已解决
    FAILED = "failed"          # 已失败
    BYPASSED = "bypassed"      # 已绕开


@dataclass
class PuzzleInstance:
    """单个谜题的运行时状态。"""

    puzzle_id: str
    name: str = ""
    description: str = ""
    puzzle_type: PuzzleType = PuzzleType.MECHANISM
    state: PuzzleState = PuzzleState.DORMANT

    # 解法定义
    solution: str | None = None            # 字面谜题的答案文本
    required_skill: str | None = None       # 需要的技能名称
    required_dc: int = 15                   # 检定难度 DC
    allowed_skills: list[str] = field(default_factory=list)  # 可用的替代技能

    # 结果
    success_narration: str = ""
    failure_narration: str = ""
    success_effect: dict = field(default_factory=dict)   # 对游戏状态的影响
    failure_effect: dict = field(default_factory=dict)

    # 状态追踪
    attempts: int = 0
    max_attempts: int = 3
    hint_given: bool = False

    def discover(self) -> None:
        if self.state == PuzzleState.DORMANT:
            self.state = PuzzleState.DISCOVERED
            logger.info("谜题已发现: %s", self.puzzle_id)

    def activate(self) -> None:
        if self.state in (PuzzleState.DORMANT, PuzzleState.DISCOVERED):
            self.state = PuzzleState.ACTIVE
            logger.info("谜题已激活: %s", self.puzzle_id)

    def can_attempt(self) -> bool:
        return self.state == PuzzleState.ACTIVE and self.attempts < self.max_attempts

    def attempt(self) -> bool:
        """记录一次尝试，返回是否仍有尝试次数。"""
        self.attempts += 1
        if self.attempts >= self.max_attempts:
            self.state = PuzzleState.FAILED
            logger.info("谜题失败(超过最大尝试次数): %s", self.puzzle_id)
            return False
        return True

    def solve(self) -> None:
        self.state = PuzzleState.SOLVED
        logger.info("谜题已解决: %s", self.puzzle_id)

    def bypass(self) -> None:
        self.state = PuzzleState.BYPASSED
        logger.info("谜题已绕开: %s", self.puzzle_id)

    def to_dict(self) -> dict:
        return {
            "puzzle_id": self.puzzle_id,
            "name": self.name,
            "description": self.description,
            "puzzle_type": self.puzzle_type.value,
            "state": self.state.value,
            "solution": self.solution,
            "required_skill": self.required_skill,
            "required_dc": self.required_dc,
            "allowed_skills": self.allowed_skills,
            "success_narration": self.success_narration,
            "failure_narration": self.failure_narration,
            "success_effect": self.success_effect,
            "failure_effect": self.failure_effect,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "hint_given": self.hint_given,
            "solved": self.is_solved(),
        }

    def is_solved(self) -> bool:
        return self.state in (PuzzleState.SOLVED, PuzzleState.BYPASSED)

    def is_failed(self) -> bool:
        return self.state == PuzzleState.FAILED


class PuzzleManager:
    """谜题管理器 —— 管理所有激活的谜题，处理校验和推进。"""

    def __init__(self):
        self.puzzles: dict[str, PuzzleInstance] = {}

    def add_puzzle(self, puzzle: PuzzleInstance) -> None:
        self.puzzles[puzzle.puzzle_id] = puzzle

    def get_active_puzzles(self) -> list[PuzzleInstance]:
        return [p for p in self.puzzles.values() if p.state == PuzzleState.ACTIVE]

    def get_puzzle(self, puzzle_id: str) -> PuzzleInstance | None:
        return self.puzzles.get(puzzle_id)

    def check_riddle_answer(self, puzzle_id: str, answer: str) -> tuple[bool, str]:
        """校验字面谜题的答案。"""
        puzzle = self.get_puzzle(puzzle_id)
        if not puzzle or puzzle.puzzle_type != PuzzleType.RIDDLE:
            return False, "没有对应的谜题或类型不匹配"
        if not puzzle.can_attempt():
            return False, "谜题已被解决或尝试次数已用完"

        expected = (puzzle.solution or "").lower().replace(" ", "")
        actual = answer.lower().replace(" ", "")
        if expected and expected in actual:
            puzzle.solve()
            return True, puzzle.success_narration or "回答正确！谜题已解决。"

        can_continue = puzzle.attempt()
        if not can_continue:
            return False, puzzle.failure_narration or "谜题失败，尝试次数已用完。"
        return False, "回答不正确，请再试一次。"

    def check_skill_check(self, puzzle: PuzzleInstance, skill: str,
                          roll_result: int) -> tuple[bool, str]:
        """校验技能检定型谜题。"""
        if not puzzle.can_attempt():
            return False, "谜题已被解决或尝试次数已用完"

        allowed = puzzle.allowed_skills or []
        if puzzle.required_skill:
            allowed.append(puzzle.required_skill)
        if allowed and skill not in allowed:
            puzzle.attempt()
            return False, f"{skill} 检定不适用于此谜题。（已消耗 {puzzle.attempts}/{puzzle.max_attempts} 次尝试）"

        if roll_result >= puzzle.required_dc:
            puzzle.solve()
            return True, puzzle.success_narration or "检定成功！谜题已解决。"

        can_continue = puzzle.attempt()
        if not can_continue:
            return False, puzzle.failure_narration or "谜题失败，尝试次数已用完。"
        return False, f"检定失败 (需要 {puzzle.required_dc}，掷出 {roll_result})。剩余尝试: {puzzle.max_attempts - puzzle.attempts}"

    def to_dict(self) -> dict:
        return {
            pid: p.to_dict() for pid, p in self.puzzles.items()
        }

    def to_active_dict(self) -> dict:
        """仅序列化未解决的谜题（dormant/discovered/active），已解决/失败/绕开的不存盘。"""
        return {
            pid: p.to_dict() for pid, p in self.puzzles.items()
            if p.state in (PuzzleState.DORMANT, PuzzleState.DISCOVERED, PuzzleState.ACTIVE)
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PuzzleManager":
        mgr = cls()
        for pid, pd in data.items():
            puzzle = PuzzleInstance(
                puzzle_id=pd["puzzle_id"],
                name=pd.get("name", ""),
                description=pd.get("description", ""),
                puzzle_type=PuzzleType(pd.get("puzzle_type", "mechanism")),
                state=PuzzleState(pd.get("state", "dormant")),
                solution=pd.get("solution"),
                required_skill=pd.get("required_skill"),
                required_dc=pd.get("required_dc", 15),
                allowed_skills=pd.get("allowed_skills", []),
                success_narration=pd.get("success_narration", ""),
                failure_narration=pd.get("failure_narration", ""),
                success_effect=pd.get("success_effect", {}),
                failure_effect=pd.get("failure_effect", {}),
                attempts=pd.get("attempts", 0),
                max_attempts=pd.get("max_attempts", 3),
                hint_given=pd.get("hint_given", False),
            )
            mgr.add_puzzle(puzzle)
        return mgr


def create_puzzle_from_lorebook(entry: dict) -> PuzzleInstance | None:
    """从世界书条目创建 PuzzleInstance。"""
    if entry.get("type") != "puzzle":
        return None
    return PuzzleInstance(
        puzzle_id=entry.get("id", f"puzzle_{entry.get('name', 'unknown')}"),
        name=entry.get("name", ""),
        description=entry.get("content", ""),
        puzzle_type=PuzzleType(entry.get("puzzle_type", "mechanism")),
        solution=entry.get("solution"),
        required_skill=entry.get("required_skill"),
        required_dc=entry.get("required_dc", 15),
        allowed_skills=entry.get("allowed_skills", []),
        success_narration=entry.get("success_narration", ""),
        failure_narration=entry.get("failure_narration", ""),
        max_attempts=entry.get("max_attempts", 3),
    )
