"""How long a cast actually lasts.

The catalogue states a *base* duration in whichever field the effect happens to
use -- rounds for a zone, a ``1_hour``-style code for a condition, hours for a
mark.  Everything that changes it (upcasting, Extended Spell, and later ritual
casting) must be applied on top of that base rather than written back into the
catalogue, so one authority answers "when does this end" for zones, conditions
and marks alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_MAX_MINUTES = 24 * 60


@dataclass(frozen=True)
class Duration:
    """A resolved duration, in the three units the engine actually uses."""

    rounds: int
    minutes: int
    hours: int
    #: 无法换算成秒数的原文（"concentration"、"until dispelled"）。保留它，
    #: 这类时长由持有者状态决定结束，不能被改写成 24 小时。
    raw: str = ""

    def as_condition_text(self) -> str:
        if not (self.rounds or self.minutes or self.hours) and self.raw:
            return self.raw
        if self.hours:
            return f"{self.hours}_hour"
        if self.minutes:
            return f"{self.minutes}_minute"
        return f"{self.rounds}_round" if self.rounds else ""


def base(effect: Mapping[str, Any], spell: Mapping[str, Any] | None = None) -> Duration:
    """The catalogue's own duration, before any modifier."""

    rounds = int(effect.get("duration_rounds", 0) or 0)
    if rounds:
        return Duration(rounds, rounds // 10 or 1, 0)
    code = str(effect.get("condition_duration") or "")
    if code:
        return _from_code(code)
    # 没有可换算的时长时，把原文原样带回：有些条件由其它事实结束（"到你
    # 下一回合开始"），把它写成 24 小时会让条件永远不消失。

    hours = int(effect.get("mark_duration_hours", 0) or 0)
    if hours:
        return Duration(0, hours * 60, hours)
    text = str((spell or {}).get("duration") or "")
    resolved = _from_code(text)
    if not (resolved.rounds or resolved.minutes or resolved.hours) and not resolved.raw:
        return Duration(0, 0, 0, raw=text)
    return resolved


def effective(
    effect: Mapping[str, Any],
    spell: Mapping[str, Any] | None = None,
    *,
    slot_level: int = 0,
    extended: bool = False,
) -> Duration:
    """Base duration plus every modifier that applies to *this* cast.

    Upcasting is read from the effect's own per-level increments, so a spell
    that lengthens with the slot says so in data instead of in code.  Extended
    Spell doubles whatever comes out, capped at 24 hours.
    """

    resolved = base(effect, spell)
    spell_level = int((spell or {}).get("level", 0) or 0)
    steps = max(0, int(slot_level) - spell_level) if spell_level else 0
    if steps:
        resolved = Duration(
            resolved.rounds + int(effect.get("upcast_duration_rounds", 0) or 0) * steps,
            resolved.minutes + int(effect.get("upcast_duration_minutes", 0) or 0) * steps,
            resolved.hours + int(effect.get("upcast_duration_hours", 0) or 0) * steps,
        )
    if extended:
        resolved = Duration(
            resolved.rounds * 2,
            min(resolved.minutes * 2, _MAX_MINUTES),
            min(resolved.hours * 2, 24),
        )
    return resolved


def _from_code(code: str) -> Duration:
    text = code.strip().lower().replace(" ", "_")
    if not text:
        return Duration(0, 0, 0)
    digits = "".join(ch for ch in text if ch.isdigit())
    count = int(digits) if digits else 1
    if "hour" in text:
        return Duration(0, count * 60, count)
    if "minute" in text:
        return Duration(0, count, 0)
    if "round" in text:
        return Duration(count, count // 10 or 1, 0)
    if text in {"instantaneous", "instant"}:
        return Duration(0, 0, 0)
    # 其余都是"由某个事件结束"的代码（target_turn_start、caster_next_turn_end、
    # concentration……）。原样带回：把它们换算成 24 小时会让条件永不消失。
    return Duration(0, 0, 0, raw=code)
