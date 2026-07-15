"""剧情追踪器 —— 追踪任务进度、NPC 关系、关键决策，防止长期跑偏。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class QuestStatus(Enum):
    ACTIVE = "active"        # 进行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 已失败
    ABANDONED = "abandoned"  # 已放弃

    @classmethod
    def from_llm(cls, value: str) -> "QuestStatus":
        v = value.lower().strip()
        mapping = {
            "active": cls.ACTIVE, "进行中": cls.ACTIVE, "进行": cls.ACTIVE,
            "completed": cls.COMPLETED, "已完成": cls.COMPLETED, "完成": cls.COMPLETED, "成功": cls.COMPLETED,
            "failed": cls.FAILED, "已失败": cls.FAILED, "失败": cls.FAILED,
            "abandoned": cls.ABANDONED, "已放弃": cls.ABANDONED, "放弃": cls.ABANDONED,
        }
        return mapping.get(v, cls.ACTIVE)


class RelationTier(Enum):
    HOSTILE = "hostile"       # 敌对
    SUSPICIOUS = "suspicious" # 怀疑/警觉
    NEUTRAL = "neutral"       # 中立
    FRIENDLY = "friendly"     # 友善
    TRUSTED = "trusted"       # 信任
    ALLY = "ally"             # 盟友

    @classmethod
    def from_llm(cls, value: str) -> "RelationTier":
        """容忍 LLM 输出中文或变体。"""
        v = value.lower().strip()
        mapping = {
            "hostile": cls.HOSTILE, "敌对": cls.HOSTILE, "仇恨": cls.HOSTILE, "厌恶": cls.HOSTILE,
            "suspicious": cls.SUSPICIOUS, "怀疑": cls.SUSPICIOUS, "警觉": cls.SUSPICIOUS, "警惕": cls.SUSPICIOUS, "戒备": cls.SUSPICIOUS,
            "neutral": cls.NEUTRAL, "中立": cls.NEUTRAL, "冷淡": cls.NEUTRAL, "普通": cls.NEUTRAL, "陌生": cls.NEUTRAL,
            "friendly": cls.FRIENDLY, "友善": cls.FRIENDLY, "友好": cls.FRIENDLY, "亲近": cls.FRIENDLY, "尊敬": cls.FRIENDLY,
            "trusted": cls.TRUSTED, "信任": cls.TRUSTED, "信赖": cls.TRUSTED, "忠诚": cls.TRUSTED,
            "ally": cls.ALLY, "盟友": cls.ALLY, "同盟": cls.ALLY, "挚友": cls.ALLY,
        }
        return mapping.get(v, cls.NEUTRAL)


@dataclass
class QuestEntry:
    quest_id: str
    title: str
    description: str = ""
    status: QuestStatus = QuestStatus.ACTIVE
    progress: str = ""        # 当前进展描述
    round_started: int = 0
    round_updated: int = 0
    giver: str = ""           # 任务来源 NPC

    def to_dict(self) -> dict:
        return {
            "quest_id": self.quest_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "progress": self.progress,
            "round_started": self.round_started,
            "round_updated": self.round_updated,
            "giver": self.giver,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QuestEntry":
        return cls(
            quest_id=data.get("quest_id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=QuestStatus.from_llm(data.get("status", "active")),
            progress=data.get("progress", ""),
            round_started=data.get("round_started", 0),
            round_updated=data.get("round_updated", 0),
            giver=data.get("giver", ""),
        )


@dataclass
class RelationEntry:
    npc_name: str
    tier: RelationTier = RelationTier.NEUTRAL
    notes: str = ""           # 关系变化的简要记录
    round_updated: int = 0

    def to_dict(self) -> dict:
        return {
            "npc_name": self.npc_name,
            "tier": self.tier.value,
            "notes": self.notes,
            "round_updated": self.round_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RelationEntry":
        return cls(
            npc_name=data.get("npc_name", ""),
            tier=RelationTier.from_llm(data.get("tier", "neutral")),
            notes=data.get("notes", ""),
            round_updated=data.get("round_updated", 0),
        )


@dataclass
class DecisionEntry:
    """玩家的关键决策记录。"""
    description: str
    round_number: int = 0
    made_by: str = ""

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "round_number": self.round_number,
            "made_by": self.made_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DecisionEntry":
        return cls(
            description=data.get("description", ""),
            round_number=data.get("round_number", 0),
            made_by=data.get("made_by", ""),
        )


class PlotTracker:
    """剧情追踪器 —— 存储并格式化当前游戏的剧情状态。

    数据来源：每轮 LLM 输出的 plot_update 字段。
    注入方式：每轮 context 拼接时，将活跃任务和重要关系格式化为文本注入。
    """

    def __init__(self):
        self.quests: dict[str, QuestEntry] = {}
        self.relations: dict[str, RelationEntry] = {}
        self.decisions: list[DecisionEntry] = []
        self._quest_counter: int = 0

    # ---- 更新接口 ----

    def apply_update(self, plot_update: dict, round_number: int) -> list[str]:
        """应用一轮的剧情更新，返回变更摘要列表。"""
        changes: list[str] = []

        # 任务更新（兼容字符串格式）
        for qd in plot_update.get("quests", []):
            if isinstance(qd, str):
                qd = {"title": qd, "status": "active"}
            elif not isinstance(qd, dict):
                continue
            qid = qd.get("quest_id")
            if qid is None:
                # 按 title 匹配已有任务，避免同任务重复创建
                title = qd.get("title", "")
                for existing_id, existing_q in self.quests.items():
                    if existing_q.title == title:
                        qid = existing_id
                        break
                if qid is None:
                    qid = f"quest_{self._quest_counter}"
            if qid not in self.quests:
                self._quest_counter += 1
                self.quests[qid] = QuestEntry(
                    quest_id=qid,
                    title=qd.get("title", ""),
                    description=qd.get("description", ""),
                    status=QuestStatus.from_llm(qd.get("status", "active")),
                    progress=qd.get("progress", ""),
                    round_started=round_number,
                    round_updated=round_number,
                    giver=qd.get("giver", ""),
                )
                changes.append(f"新任务: {qd.get('title', '')}")
            else:
                q = self.quests[qid]
                old_status = q.status
                new_status = QuestStatus.from_llm(qd.get("status", q.status.value))
                q.status = new_status
                q.progress = qd.get("progress", q.progress)
                q.round_updated = round_number
                if new_status != old_status:
                    changes.append(f"任务状态变更: {q.title} → {new_status.value}")
                if qd.get("progress") and qd["progress"] != old_status.value:
                    changes.append(f"任务进展: {q.title}: {qd['progress']}")

        # 关系更新（兼容字符串格式）
        for rd in plot_update.get("relations", []):
            if isinstance(rd, str):
                rd = {"npc_name": rd, "tier": "neutral"}
            elif not isinstance(rd, dict):
                continue
            name = rd.get("npc_name", "")
            tier = RelationTier.from_llm(rd.get("tier", "neutral"))
            notes = rd.get("notes", "")
            if name in self.relations:
                old = self.relations[name]
                if old.tier != tier:
                    changes.append(f"关系变化: {name} {old.tier.value} → {tier.value}")
                old.tier = tier
                old.notes = notes or old.notes
                old.round_updated = round_number
            else:
                self.relations[name] = RelationEntry(
                    npc_name=name, tier=tier, notes=notes, round_updated=round_number,
                )
                changes.append(f"新关系: {name} ({tier.value})")

        # 决策记录（兼容 LLM 输出字符串或对象两种格式）
        for dd in plot_update.get("decisions", []):
            if isinstance(dd, str):
                entry = DecisionEntry(
                    description=dd, round_number=round_number, made_by="",
                )
            elif isinstance(dd, dict):
                entry = DecisionEntry(
                    description=dd.get("description", ""),
                    round_number=round_number,
                    made_by=dd.get("made_by", ""),
                )
            else:
                continue
            self.decisions.append(entry)
            changes.append(f"关键决策: {entry.description[:40]}")

        # 只保留最近 20 条决策
        if len(self.decisions) > 20:
            self.decisions = self.decisions[-20:]

        # 任务清理：ABANDONED 直接删除；COMPLETED/FAILED 超过 5 条时 trim 字段
        abandoned_keys = [
            qid for qid, q in self.quests.items()
            if q.status == QuestStatus.ABANDONED
        ]
        for qid in abandoned_keys:
            del self.quests[qid]
        completed_failed = [
            q for q in self.quests.values()
            if q.status in (QuestStatus.COMPLETED, QuestStatus.FAILED)
        ]
        if len(completed_failed) > 5:
            stale = sorted(completed_failed, key=lambda q: q.round_updated)[:-5]
            for q in stale:
                q.description = ""
                q.progress = ""

        return changes

    # ---- 格式化（注入 LLM context）----

    def format_for_context(self) -> str:
        """将当前剧情状态格式化为文本，注入 LLM 上下文。"""
        lines: list[str] = []

        # 活跃任务
        active_quests = [q for q in self.quests.values() if q.status == QuestStatus.ACTIVE]
        if active_quests:
            lines.append("【当前任务】")
            for q in active_quests:
                progress_str = f" ({q.progress})" if q.progress else ""
                lines.append(f"- {q.title}{progress_str}")

        # 完成/失败任务
        completed = [q for q in self.quests.values() if q.status == QuestStatus.COMPLETED]
        if completed:
            recent_completed = sorted(completed, key=lambda q: q.round_updated, reverse=True)[:5]
            lines.append("【已完成任务】")
            for q in recent_completed:
                lines.append(f"- {q.title}")

        # 重要 NPC 关系（排除中立的）
        notable_relations = [
            r for r in self.relations.values()
            if r.tier not in (RelationTier.NEUTRAL,)
        ]
        if notable_relations:
            lines.append("【NPC 关系】")
            for r in sorted(notable_relations, key=lambda x: x.round_updated, reverse=True):
                lines.append(f"- {r.npc_name}: {r.tier.value}")

        # 最近关键决策
        if self.decisions:
            recent = self.decisions[-5:]
            lines.append("【最近决策】")
            for d in recent:
                lines.append(f"- [Round {d.round_number}] {d.description}")

        return "\n".join(lines) if lines else ""

    # ---- 序列化 ----

    def to_dict(self) -> dict:
        return {
            "quests": {k: v.to_dict() for k, v in self.quests.items()},
            "relations": {k: v.to_dict() for k, v in self.relations.items()},
            "decisions": [d.to_dict() for d in self.decisions],
            "quest_counter": self._quest_counter,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlotTracker":
        pt = cls()
        pt.quests = {k: QuestEntry.from_dict(v) for k, v in data.get("quests", {}).items()}
        pt.relations = {k: RelationEntry.from_dict(v) for k, v in data.get("relations", {}).items()}
        pt.decisions = [DecisionEntry.from_dict(d) for d in data.get("decisions", [])]
        pt._quest_counter = data.get("quest_counter", 0)
        return pt
