"""Legacy display-label → verdict code bridge.

Before ADR-0015 the engine's check verdict *was* a display string, so saves,
external ruleset runtimes and plugin-authored payloads may still carry
``"大成功"`` / ``"Critical Success"`` and nothing else.  Reading those back is a
compatibility concern and lives here — never in business logic, and never as a
branching authority: a value that is not an exact known label stays unknown, so
nothing is guessed from prose.

Delete this module once no producer writes a bare label (stage 2 of the ADR).
"""

from __future__ import annotations

from src.engine import check_verdict

#: Exact historical labels, as written by the engine before codes existed and by
#: runtimes that echoed them.  Matching is exact and case-folded; no prefix,
#: no substring, no translation of arbitrary text.
LEGACY_VERDICT_LABELS: dict[str, str] = {
    "大成功": check_verdict.CRITICAL_SUCCESS,
    "critical success": check_verdict.CRITICAL_SUCCESS,
    "kritischer erfolg": check_verdict.CRITICAL_SUCCESS,
    "クリティカル": check_verdict.CRITICAL_SUCCESS,
    "极难成功": check_verdict.EXTREME_SUCCESS,
    "extreme success": check_verdict.EXTREME_SUCCESS,
    "extremer erfolg": check_verdict.EXTREME_SUCCESS,
    "イクストリーム成功": check_verdict.EXTREME_SUCCESS,
    "困难成功": check_verdict.HARD_SUCCESS,
    "hard success": check_verdict.HARD_SUCCESS,
    "schwerer erfolg": check_verdict.HARD_SUCCESS,
    "ハード成功": check_verdict.HARD_SUCCESS,
    "普通成功": check_verdict.REGULAR_SUCCESS,
    "regular success": check_verdict.REGULAR_SUCCESS,
    "normaler erfolg": check_verdict.REGULAR_SUCCESS,
    "レギュラー成功": check_verdict.REGULAR_SUCCESS,
    "成功": check_verdict.SUCCESS,
    "success": check_verdict.SUCCESS,
    "erfolg": check_verdict.SUCCESS,
    "大失败": check_verdict.CRITICAL_FAILURE,
    "critical failure": check_verdict.CRITICAL_FAILURE,
    "fumble": check_verdict.CRITICAL_FAILURE,
    "kritischer fehlschlag": check_verdict.CRITICAL_FAILURE,
    "ファンブル": check_verdict.CRITICAL_FAILURE,
    "失败": check_verdict.FAILURE,
    "failure": check_verdict.FAILURE,
    "fehlschlag": check_verdict.FAILURE,
    "失敗": check_verdict.FAILURE,
}


def legacy_code(label: object) -> str:
    """The code for an exact legacy label, or ``""``."""

    text = str(label or "").strip().casefold()
    return LEGACY_VERDICT_LABELS.get(text, "")