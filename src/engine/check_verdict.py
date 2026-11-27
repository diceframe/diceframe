"""Language-independent check verdict codes.

``verdict`` on a :class:`CheckResult` is *display text* written in the game
language at resolve time.  It is not an identity: nothing branches on it, and a
translated label must never decide a mechanic (ADR-0015, ENGINEERING_RULES §10).

Everything that needs to know whether a check succeeded asks for a code here.
The text is produced only at a rendering boundary, via :func:`render`.

Deriving codes for saves written before this module existed
-----------------------------------------------------------
Older saves only carry the display text, so :func:`code_of` rebuilds the code
from the facts the core already persists (dice system, roll, threshold, DC,
difficulty, ``is_critical``, ``is_fumble``).  That reconstruction is exact for
core d100/d20 checks because the ladder is deterministic.  Checks produced by
an external ruleset runtime carry no such guarantee, so they stay unknown
unless the runtime declares ``verdict_code``; unknown means "do not offer the
optional rank-dependent branches", never "guess from the text".
"""

from __future__ import annotations

from typing import Any, Mapping

from src.localization.messages import t

CRITICAL_FAILURE = "critical_failure"
FAILURE = "failure"
SUCCESS = "success"
REGULAR_SUCCESS = "regular_success"
HARD_SUCCESS = "hard_success"
EXTREME_SUCCESS = "extreme_success"
CRITICAL_SUCCESS = "critical_success"

CODES: tuple[str, ...] = (
    CRITICAL_FAILURE,
    FAILURE,
    SUCCESS,
    REGULAR_SUCCESS,
    HARD_SUCCESS,
    EXTREME_SUCCESS,
    CRITICAL_SUCCESS,
)

#: Success ladder.  ``success`` is the plain d20 outcome and the Luck-upgraded
#: regular success; ``regular_success`` is the CoC d100 regular level.  Both
#: rank 1 so rank comparisons never depend on which vocabulary produced them.
RANK: dict[str, int] = {
    CRITICAL_FAILURE: -1,
    FAILURE: 0,
    SUCCESS: 1,
    REGULAR_SUCCESS: 1,
    HARD_SUCCESS: 2,
    EXTREME_SUCCESS: 3,
    CRITICAL_SUCCESS: 4,
}

SUCCESS_CODES = frozenset(code for code, rank in RANK.items() if rank >= 1)
FAILURE_CODES = frozenset(code for code, rank in RANK.items() if rank <= 0)

REQUIRED_RANK: dict[str, int] = {"regular": 1, "hard": 2, "extreme": 3}
REQUIRED_LEVEL_CODE: dict[str, str] = {
    "regular": REGULAR_SUCCESS,
    "hard": HARD_SUCCESS,
    "extreme": EXTREME_SUCCESS,
}

DIFFICULTY_LEVELS = ("regular", "hard", "extreme")
_MESSAGE_NAMESPACE = "engine.check_verdict"


def normalize(value: object) -> str:
    """A known code, or ``""``.  Never guesses from text."""

    text = str(value or "").strip().casefold()
    return text if text in RANK else ""


def rank(code: object) -> int | None:
    return RANK.get(normalize(code))


def is_success(code: object) -> bool:
    return normalize(code) in SUCCESS_CODES


def is_failure(code: object) -> bool:
    return normalize(code) in FAILURE_CODES


def is_critical(code: object) -> bool:
    return normalize(code) == CRITICAL_SUCCESS


def is_fumble(code: object) -> bool:
    return normalize(code) == CRITICAL_FAILURE


def render(code: object, language: object = None) -> str:
    """Display text for a code, in ``language`` (request language when omitted).

    An unknown code renders as the empty string instead of echoing raw text:
    callers fall back to whatever label the check already carries.
    """

    known = normalize(code)
    if not known:
        return ""
    return t(f"{_MESSAGE_NAMESPACE}.{known}", language)


def percentile_code(roll_value: int, threshold: int) -> str:
    """CoC 7e percentile ladder.  Single definition; `dice_rules_coc` delegates."""

    if roll_value <= 1:
        return CRITICAL_SUCCESS
    if roll_value >= 100 or (threshold < 50 and roll_value >= 96):
        return CRITICAL_FAILURE
    if roll_value > threshold:
        return FAILURE
    if roll_value <= threshold // 5:
        return EXTREME_SUCCESS
    if roll_value <= threshold // 2:
        return HARD_SUCCESS
    return REGULAR_SUCCESS


def normalize_difficulty(value: object) -> str:
    text = str(value or "").strip().casefold()
    return text if text in DIFFICULTY_LEVELS else "regular"


def apply_required_level(code: object, difficulty: object) -> str:
    """Downgrade a success that misses the required level; failures unchanged."""

    known = normalize(code)
    current = RANK.get(known)
    if current is None:
        return known
    if 1 <= current < REQUIRED_RANK.get(normalize_difficulty(difficulty), 1):
        return FAILURE
    return known


def required_level_code(difficulty: object) -> str:
    return REQUIRED_LEVEL_CODE.get(normalize_difficulty(difficulty), REGULAR_SUCCESS)


def _int(value: object) -> int | None:
    if type(value) is int:
        return value
    return None


def _derive(check: Mapping[str, Any]) -> str:
    """Rebuild the code from persisted facts.  ``""`` when that is not provable."""

    if bool(check.get("is_critical")):
        return CRITICAL_SUCCESS
    if bool(check.get("is_fumble")):
        return CRITICAL_FAILURE
    if str(check.get("planner_source") or "") == "runtime":
        return ""
    # Luck already rewrote the outcome upward without touching the roll.
    if _int(check.get("luck_spent")):
        return required_level_code(check.get("difficulty")) if check.get("difficulty") else SUCCESS
    dice = str(check.get("dice") or "").strip().casefold()
    if dice == "d100":
        roll_value = _int(check.get("roll"))
        threshold = _int(check.get("threshold"))
        if roll_value is None or threshold is None:
            return ""
        base = percentile_code(roll_value, max(1, min(99, threshold)))
        opposed = check.get("opposed")
        if isinstance(opposed, Mapping) and str(opposed.get("outcome") or "") != "win":
            return base if base == CRITICAL_FAILURE else FAILURE
        if check.get("difficulty"):
            return apply_required_level(base, check.get("difficulty"))
        return base
    if dice == "d20":
        total = _int(check.get("total"))
        dc = _int(check.get("dc"))
        if total is not None and dc is not None:
            return SUCCESS if total >= dc else FAILURE
        success = check.get("success")
        if isinstance(success, bool):
            return SUCCESS if success else FAILURE
    return ""


def code_of(check: object) -> str:
    """The code for one saved check, or ``""`` when it cannot be established.

    Order: the declared code, then the runtime's explicit ``success`` flag,
    then facts the core itself persisted, and finally an exact legacy label
    from ``src.compat.verdict_labels`` (pre-ADR-0015 writers and runtimes that
    echoed the engine's old wording).  Free prose is never interpreted, and
    ``""`` means "no optional rank-dependent branch".
    """

    if not isinstance(check, Mapping):
        return ""
    declared = normalize(check.get("verdict_code"))
    if declared:
        return declared
    explicit = check.get("success")
    if isinstance(explicit, bool):
        return SUCCESS if explicit else FAILURE
    derived = _derive(check)
    if derived:
        return derived
    from src.compat.verdict_labels import legacy_code

    return legacy_code(check.get("verdict"))


def label_of(check: object) -> str:
    """Best available display text for one check: localized code, else stored."""

    if not isinstance(check, Mapping):
        return ""
    return render(code_of(check)) or str(check.get("verdict") or "")


__all__ = [
    "CODES",
    "CRITICAL_FAILURE",
    "CRITICAL_SUCCESS",
    "DIFFICULTY_LEVELS",
    "EXTREME_SUCCESS",
    "FAILURE",
    "FAILURE_CODES",
    "HARD_SUCCESS",
    "RANK",
    "REGULAR_SUCCESS",
    "REQUIRED_LEVEL_CODE",
    "REQUIRED_RANK",
    "SUCCESS",
    "SUCCESS_CODES",
    "apply_required_level",
    "code_of",
    "is_critical",
    "is_failure",
    "is_fumble",
    "is_success",
    "label_of",
    "normalize",
    "normalize_difficulty",
    "percentile_code",
    "rank",
    "render",
    "required_level_code",
]