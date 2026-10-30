"""Settle narrator-proposed affection changes (GAL track P3).

The narrator may emit ``AFFECTION:NPC名:变化量:原因``.  That is a proposal
(ENGINEERING_RULES §13): it only takes effect when the bound rule declares
an ``affection`` block, the subject is an NPC of this run, and the change is
an integer.  Each subject's total change in one settled update is clamped to
the rule's ``max_delta_per_round``; the affection module then clamps the
score to its range.  Everything else is dropped and logged, never guessed.

This is the only writer of ``affection.adjust`` (architecture guard).
"""

from __future__ import annotations

import logging
from typing import Any

from src.engine.language import DEFAULT_LANGUAGE, localized_text
from src.engine.module_state import ModuleStateError
from src.engine.modules import affection, progression_state
from src.rules.affection_config import AffectionConfig

logger = logging.getLogger("trpg")

MAX_SUBJECTS_PER_ROUND = 8


def _delta(raw: Any) -> int | None:
    if type(raw) is int:
        return raw
    text = str(raw or "").strip()
    sign = text[:1] if text[:1] in "+-" else ""
    digits = text[len(sign):]
    if not digits.isascii() or not digits.isdigit():
        return None
    return int(sign + digits)


def settle_affection(instance: Any, proposals: Any, config: AffectionConfig | None) -> list[dict[str, Any]]:
    """Apply valid proposals; return what was applied (subject, delta, score)."""
    if not proposals:
        return []
    round_number = progression_state.round_value(instance)
    if config is None:
        logger.warning("忽略好感提议：规则未启用 affection (round=%d)", round_number)
        return []
    if not isinstance(proposals, list):
        logger.warning("忽略好感提议：格式非法 (round=%d)", round_number)
        return []
    totals: dict[str, int] = {}
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        subject = str(proposal.get("subject") or "").strip()
        canonical_subject = affection.resolve_subject(instance, subject)
        delta = _delta(proposal.get("delta"))
        if canonical_subject is None or delta is None:
            logger.warning(
                "忽略好感提议：subject=%r delta=%r round=%d",
                subject, proposal.get("delta"), round_number,
            )
            continue
        if canonical_subject not in totals and len(totals) >= MAX_SUBJECTS_PER_ROUND:
            logger.warning("忽略好感提议：本轮对象过多 subject=%r round=%d", subject, round_number)
            continue
        totals[canonical_subject] = totals.get(canonical_subject, 0) + delta
    cap = config.max_delta_per_round
    applied: list[dict[str, Any]] = []
    for subject, total in totals.items():
        delta = max(-cap, min(cap, total))
        if delta == 0:
            continue
        try:
            change = affection.adjust(instance, subject, delta)
        except (ModuleStateError, ValueError):
            logger.warning("好感结算失败 subject=%r round=%d", subject, round_number, exc_info=True)
            continue
        if change:
            applied.append({
                "subject": affection.display_subject(instance, subject),
                "delta": change,
                "score": affection.score(instance, subject),
            })
    return applied


def affection_prompt_appendix(instance: Any, config: AffectionConfig | None, language: str = DEFAULT_LANGUAGE) -> str:
    """Tag instructions plus current scores; empty when the rule has no affection."""
    if config is None:
        return ""
    try:
        scores = affection.scores(instance)
    except ModuleStateError:
        return ""
    listed = "; ".join(
        f"{affection.display_subject(instance, subject)}: {score}"
        for subject, score in scores.items()
    )
    cap = config.max_delta_per_round
    lo, hi = affection.MIN_SCORE, affection.MAX_SCORE
    return localized_text(language, {
        "en": (
            f"Affection (an NPC's attitude toward the protagonist, {lo} to {hi}): when this round's "
            f"events change it, add AFFECTION:NPC name:change:reason (change at most ±{cap} per NPC per round; "
            "register the NPC with an NPC tag first). The system settles the final value. "
            f"Current: {listed or 'none yet'}."
        ),
        "zh-CN": (
            f"好感度（NPC 对主角的态度，{lo} 到 {hi}）：本轮剧情让它变化时写 AFFECTION:NPC名:变化量:原因"
            f"（每个 NPC 每轮最多 ±{cap}；NPC 须先用 NPC 标签登记）。最终数值由系统结算。"
            f"当前：{listed or '暂无'}。"
        ),
        "ja": (
            f"好感度（NPC の主人公への態度、{lo}〜{hi}）：今回の展開で変化したら AFFECTION:NPC名:変化量:理由 を追加"
            f"（1 ラウンドにつき NPC ごとに最大 ±{cap}；NPC は先に NPC タグで登録）。最終値はシステムが確定します。"
            f"現在：{listed or 'なし'}。"
        ),
        "de": (
            f"Zuneigung (Haltung eines NSC zur Hauptfigur, {lo} bis {hi}): Wenn die Ereignisse dieser Runde sie ändern, "
            f"füge AFFECTION:NSC-Name:Änderung:Grund hinzu (höchstens ±{cap} pro NSC und Runde; den NSC zuerst mit "
            f"einem NPC-Tag registrieren). Das System legt den endgültigen Wert fest. Aktuell: {listed or 'noch keine'}."
        ),
    })
