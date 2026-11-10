"""Per-seat player control contract: *who plays* a character, not *who they are*.

A DiceFrame seat (``GameInstance.players[uid]``) already owns the character:
identity, HP, equipment, spell slots, conditions, world position and history all
live there.  What was missing is an explicit answer to "who decides what this
character does right now?" — a human, the server AI, or nobody yet.

This module owns that answer and nothing else:

```text
control.mode = human       a real player is responsible
control.mode = ai          the server produces this character's actions
control.mode = unclaimed   the seat exists but nobody plays it yet
```

Design boundaries:

- **AI is a controller, not a character type.**  There is exactly one character
  record per seat and exactly one combat actor (``player:<uid>``) regardless of
  the mode.  Nothing is copied, moved, or re-keyed when the controller changes.
- **Deterministic only.**  No LLM calls, no network, no threads, no persistence
  side effects, no combat or UI logic.  This module mutates one small record.
- **Single write entry.**  ``set_control`` is the only supported way to change a
  controller; everything else reads through the helpers below.
- **Reads degrade, writes fail closed.**  A corrupted persisted record reads as
  the conservative default (``human``, the pre-contract behaviour) instead of
  inventing an AI controller, while ``set_control`` rejects unknown modes and
  unknown seats outright.
- **Round rollback does not own control.**  The control record sits beside
  ``character_sheet``, and the round/swipe rollback helpers (``_snapshot_players``
  / ``restore_players``) only snapshot and restore character-sheet fields, so
  "AI took over, then the GM rolled the round back" cannot silently re-assign the
  seat.  Control still travels with ordinary save/load.
- **The room says what "away" means.**  :data:`AWAY_CONTROL_POLICIES` is the
  table-wide setting that decides whether stepping away hands the seat to the
  server AI for a moment; it belongs to the same contract because it produces
  control transitions through :func:`set_control` and nothing else.  Only an
  explicit GM action, an explicit player 暂离, or this explicit room setting can
  ever produce an AI controller -- disconnects never do.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from src.engine.game_state import GameState

# 玩家记录里的控制键；与 ``character_sheet`` 同级，不属于角色状态。
CONTROL_KEY = "control"

# 第一版只允许这三种控制者。gm / remote_bot / script / hybrid / spectator 等
# 不是本轮范围：多一个模式就多一套权限与结算语义。
CONTROL_MODES = ("human", "ai", "unclaimed")

# 旧存档没有任何控制信息，唯一不猜测的答案就是旧版本行为：真人控制。
DEFAULT_CONTROL_MODE = "human"

# temporary=true 表示「真人暂离、AI 临时托管」，恢复目标只能是真人或未认领。
RESUME_MODES = ("human", "unclaimed")

# GM 席位（开局时的第一张卡）始终属于房主自己：它绑定房主会话、从不签发分享凭证，
# 没有任何人能「认领」它。``unclaimed`` 在这里只会得到一个连房主都提交不了行动的
# 死席位，所以 GM 席位只能是真人或 AI 托管。
GM_SEAT_CONTROL_MODES = ("human", "ai")

# 房间级「暂离语义」。旧存档没有这个设置，唯一不猜测的答案是旧版本行为：
# 暂离只改在场状态，绝不把角色交给 AI。
AWAY_CONTROL_POLICIES = ("pause", "ai_takeover")
DEFAULT_AWAY_CONTROL_POLICY = "pause"

# 真人发起的一次普通行动被控制权拒绝时，唯一允许的两个对外错误码。
SUBMISSION_BLOCK_CODES = ("PLAYER_AI_CONTROLLED", "PLAYER_UNCLAIMED")

MAX_CONTROL_REVISION = 2**31 - 1


class PlayerControlError(ValueError):
    """An invalid control change: fail closed instead of guessing a controller."""


def default_control() -> dict[str, Any]:
    """The conservative control record of a seat with no stored control."""

    return {
        "mode": DEFAULT_CONTROL_MODE,
        "revision": 0,
        "temporary": False,
        "resume_mode": None,
    }


def normalize_control(raw: Any) -> dict[str, Any]:
    """Return the deterministic control record for one seat.

    Corrupted or unknown input degrades to :func:`default_control` rather than
    inventing an AI controller; only :func:`set_control` rejects input.  The
    result is idempotent, so normalizing a normalized record changes nothing.
    """

    if not isinstance(raw, Mapping):
        return default_control()
    mode = raw.get("mode")
    if mode not in CONTROL_MODES:
        mode = DEFAULT_CONTROL_MODE
    revision = raw.get("revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or not 0 <= revision <= MAX_CONTROL_REVISION
    ):
        revision = 0
    temporary = bool(raw.get("temporary", False))
    resume_mode = raw.get("resume_mode")
    if resume_mode not in RESUME_MODES:
        resume_mode = None
    # 「AI 临时托管」必须知道要还给谁：``temporary=True`` 却没有恢复目标是自相
    # 矛盾的记录（存档损坏、旧版本写坏、人为改文件）。既不能猜一个恢复目标继续
    # 托管，也不能留在 ``ai`` 上假装它可恢复 —— 安全降级为真人，并保留合法
    # revision（revision 只描述记录被改过几次，降级不改变这个事实）。
    if mode == "ai" and temporary and resume_mode is None:
        mode = DEFAULT_CONTROL_MODE
    # ``temporary`` / ``resume_mode`` 只在「AI 临时托管」时有意义：一个由真人
    # 控制的席位不能同时携带恢复目标，否则读出来就是自相矛盾的状态。
    if mode != "ai" or not temporary:
        temporary = False
        resume_mode = None
    return {
        "mode": str(mode),
        "revision": revision,
        "temporary": temporary,
        "resume_mode": resume_mode,
    }


def get_control(instance: Any, uid: str) -> dict[str, Any]:
    """Read one seat's normalized control record.

    An unknown seat reads as the conservative default instead of raising: reads
    never manufacture an AI controller, and callers asking about a seat that no
    longer exists get "no control evidence" rather than a hard failure.
    """

    player = _player_record(instance, uid)
    if player is None:
        return default_control()
    return normalize_control(player.get(CONTROL_KEY))


def control_mode(instance: Any, uid: str) -> str:
    """The seat's control mode (``human`` / ``ai`` / ``unclaimed``)."""

    return str(get_control(instance, uid)["mode"])


def submission_block(instance: Any, uid: str) -> str:
    """Why a *human-initiated* action from this seat must be refused.

    Returns one of :data:`SUBMISSION_BLOCK_CODES`, or ``""`` when a human is
    allowed to submit.  Pure deterministic read: the answer comes from the
    control record alone, so AI hosting never changes whether the server itself
    may act on a character.  An unknown seat returns ``""`` because callers
    already reject seats that are not on the roster; this helper does not invent
    a second "not a player" error.
    """

    if _player_record(instance, uid) is None:
        return ""
    mode = control_mode(instance, uid)
    if mode == "ai":
        return "PLAYER_AI_CONTROLLED"
    if mode == "unclaimed":
        return "PLAYER_UNCLAIMED"
    return ""


def set_control(
    instance: Any,
    uid: str,
    mode: str,
    *,
    temporary: bool = False,
    resume_mode: str | None = None,
) -> dict[str, Any]:
    """Set one seat's controller.  The only supported write entry point.

    Raises :class:`PlayerControlError` for an unknown seat, an unsupported mode,
    or a temporary AI hosting request without a valid resume target.  ``revision``
    increases only when the stored record actually changes, so a repeated request
    is not mistaken for a control handover.
    """

    player = _player_record(instance, uid)
    if player is None:
        raise PlayerControlError(f"unknown player seat: {uid!r}")
    if mode not in CONTROL_MODES:
        raise PlayerControlError(f"unsupported control mode: {mode!r}")
    if mode == "ai":
        if temporary:
            if resume_mode not in RESUME_MODES:
                raise PlayerControlError(
                    f"temporary AI hosting needs a resume mode: {resume_mode!r}"
                )
        else:
            resume_mode = None
    else:
        temporary = False
        resume_mode = None

    current = normalize_control(player.get(CONTROL_KEY))
    requested = normalize_control({
        "mode": mode,
        "revision": current["revision"],
        "temporary": temporary,
        "resume_mode": resume_mode,
    })
    if requested == current:
        # 没有实际变化：不提升 revision，避免把 no-op 当成一次控制权交接。
        player[CONTROL_KEY] = current
        return dict(current)
    if current["revision"] >= MAX_CONTROL_REVISION:
        raise PlayerControlError("control revision is exhausted")
    requested["revision"] = int(current["revision"]) + 1
    player[CONTROL_KEY] = requested
    return dict(requested)


def claim_seat(
    instance: Any,
    uid: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Claim an existing seat for a human: the canonical join entry point.

    A seat that is currently ``ai`` or ``unclaimed`` becomes ``human`` through
    :func:`set_control`, so the transition is recorded by the same single write
    entry point every other control change uses.  Nothing about the character
    moves: HP, equipment, spell slots, world position and the combat actor stay
    exactly where they were (``无损认领``).

    Fails closed with :class:`PlayerControlError` on an unknown seat, on a seat
    that is already human-controlled (``CONTROL_NOT_CLAIMABLE``: it is a re-join,
    not a claim), and on a stale ``expected_revision`` (``CONTROL_STALE``: the
    caller decided to claim a control record that has since changed).
    """

    player = _player_record(instance, uid)
    if player is None:
        raise PlayerControlError(f"unknown player seat: {uid!r}")
    current = normalize_control(player.get(CONTROL_KEY))
    if expected_revision is not None and (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision != current["revision"]
    ):
        raise PlayerControlError(
            "CONTROL_STALE: control revision changed, "
            f"expected {expected_revision!r} but found {current['revision']!r}"
        )
    if current["mode"] == "human":
        raise PlayerControlError(
            f"CONTROL_NOT_CLAIMABLE: seat {uid!r} is already human-controlled"
        )
    return set_control(instance, uid, "human")


def control_change_block(instance: Any) -> str:
    """Why a control change must wait, or ``""`` at a safe boundary.

    A control handover is only safe while the table is accepting actions and no
    round is in flight, otherwise a seat could change controller halfway through
    judgment.  ``PLAYER_AI_CONTROLLED`` / ``PLAYER_UNCLAIMED`` describe a seat;
    this one describes the table, and callers surface it as a retryable conflict.
    """

    if getattr(instance, "state", None) != GameState.ACTIVE_ACTION:
        return "CONTROL_CHANGE_BUSY"
    process_lock = getattr(instance, "_process_lock", None)
    if process_lock is not None and process_lock.locked():
        return "CONTROL_CHANGE_BUSY"
    return ""


def is_human_controlled(instance: Any, uid: str) -> bool:
    return control_mode(instance, uid) == "human"


def is_ai_controlled(instance: Any, uid: str) -> bool:
    return control_mode(instance, uid) == "ai"


def is_temporarily_ai_controlled(instance: Any, uid: str) -> bool:
    """Is this seat only *temporarily* hosted by the server AI?

    Temporary hosting is the away-takeover shape (``temporary=True`` with a
    ``resume_mode``), so the roster can show it differently from a seat the GM or
    the room deliberately handed to the AI for good.
    """

    record = get_control(instance, uid)
    return record["mode"] == "ai" and bool(record["temporary"])


def normalize_away_control_policy(raw: Any) -> str:
    """Coerce a stored room policy to a known value; unknown reads as the default.

    Same read/write asymmetry as the control record: reading degrades to the
    legacy behaviour (``pause``, i.e. away never hands a character to the AI)
    because that is the behaviour a save written before this setting existed
    actually had.  :func:`set_away_control_policy` still refuses unknown values.
    """

    value = str(raw or "").strip()
    return value if value in AWAY_CONTROL_POLICIES else DEFAULT_AWAY_CONTROL_POLICY


def away_control_policy(instance: Any) -> str:
    """The room's away semantics, defaulting to the legacy ``pause``."""

    from src.engine import module_hooks

    return normalize_away_control_policy(module_hooks.ask(
        module_hooks.STATE_READ, instance, module_hooks.AWAY_CONTROL_POLICY,
        default=DEFAULT_AWAY_CONTROL_POLICY,
    ))


def set_away_control_policy(instance: Any, policy: Any) -> str:
    """Set the room-level away policy; fail closed on an unknown value."""

    value = str(policy or "").strip()
    if value not in AWAY_CONTROL_POLICIES:
        raise PlayerControlError(
            f"unknown away_control_policy {policy!r}; "
            f"expected one of {AWAY_CONTROL_POLICIES}"
        )
    from src.engine import module_hooks

    module_hooks.record(instance, module_hooks.AWAY_CONTROL_POLICY, value)
    return value


def begin_away_hosting(instance: Any, uid: str) -> dict[str, Any]:
    """Hand a human seat to the server AI because its player stepped away.

    Only meaningful for a seat a human actually controls: an ``unclaimed`` or
    already-``ai`` seat has no human to hand over.  The result is the temporary
    shape ``{mode: ai, temporary: true, resume_mode: human}``, so the seat can
    always be handed back and can never quietly become a permanent AI.
    """

    current = control_mode(instance, uid)
    if current != "human":
        return get_control(instance, uid)
    return set_control(instance, uid, "ai", temporary=True, resume_mode="human")


def end_away_hosting(instance: Any, uid: str) -> dict[str, Any]:
    """Return a temporarily hosted seat to whoever it was taken from.

    A no-op unless the seat really is temporarily hosted, so an ordinary "I am
    back" on a seat the GM permanently gave to the AI does not steal it back.
    """

    record = get_control(instance, uid)
    if record["mode"] != "ai" or not record["temporary"]:
        return record
    resume = str(record.get("resume_mode") or "human")
    if resume not in RESUME_MODES:
        resume = "human"
    return set_control(instance, uid, resume)


def is_unclaimed(instance: Any, uid: str) -> bool:
    return control_mode(instance, uid) == "unclaimed"


def human_controlled_players(instance: Any) -> list[str]:
    return _players_by_mode(instance, "human")


def ai_controlled_players(instance: Any) -> list[str]:
    return _players_by_mode(instance, "ai")


def unclaimed_players(instance: Any) -> list[str]:
    return _players_by_mode(instance, "unclaimed")


def ensure_control(player: Any) -> bool:
    """Materialize or repair one seat record in place.

    Returns ``True`` when the record was written.  Idempotent: a record that
    already normalizes to itself is left untouched.
    """

    if not isinstance(player, MutableMapping):
        return False
    stored = player.get(CONTROL_KEY)
    record = normalize_control(stored)
    if stored == record:
        return False
    player[CONTROL_KEY] = record
    return True


def ensure_controls(instance: Any) -> list[str]:
    """Materialize and repair the control record of every seat.

    Idempotent: a seat whose stored record already normalizes to itself is left
    untouched.  Used by the aggregate so a seat written by any path (or edited by
    hand) always exposes an explicit controller.
    """

    players = getattr(instance, "players", None)
    if not isinstance(players, Mapping):
        return []
    repaired: list[str] = []
    for uid, player in players.items():
        if ensure_control(player):
            repaired.append(str(uid))
    return sorted(repaired)


def release_temporary_controls(instance: Any) -> list[str]:
    """Return temporarily AI-hosted seats to their resume mode.

    Temporary hosting means "the real player stepped away for a moment" and must
    never survive as a permanent assignment.  Restart / new-run flows that keep
    the roster call this so a seat cannot stay AI-controlled across a restart.
    """

    released: list[str] = []
    for uid in _player_uids(instance):
        record = get_control(instance, uid)
        if record["mode"] != "ai" or not record["temporary"]:
            continue
        set_control(instance, uid, str(record["resume_mode"] or DEFAULT_CONTROL_MODE))
        released.append(uid)
    return sorted(released)


def _player_uids(instance: Any) -> list[str]:
    players = getattr(instance, "players", None)
    if not isinstance(players, Mapping):
        return []
    return [str(uid) for uid in players]


def _players_by_mode(instance: Any, mode: str) -> list[str]:
    return sorted(
        uid for uid in _player_uids(instance) if control_mode(instance, uid) == mode
    )


def _player_record(instance: Any, uid: str) -> MutableMapping[str, Any] | None:
    players = getattr(instance, "players", None)
    if not isinstance(players, Mapping):
        return None
    player = players.get(uid)
    if not isinstance(player, MutableMapping):
        return None
    return player


__all__ = [
    "AWAY_CONTROL_POLICIES",
    "CONTROL_KEY",
    "CONTROL_MODES",
    "DEFAULT_AWAY_CONTROL_POLICY",
    "DEFAULT_CONTROL_MODE",
    "GM_SEAT_CONTROL_MODES",
    "MAX_CONTROL_REVISION",
    "PlayerControlError",
    "RESUME_MODES",
    "SUBMISSION_BLOCK_CODES",
    "ai_controlled_players",
    "away_control_policy",
    "begin_away_hosting",
    "claim_seat",
    "control_change_block",
    "control_mode",
    "default_control",
    "end_away_hosting",
    "ensure_control",
    "ensure_controls",
    "get_control",
    "human_controlled_players",
    "is_ai_controlled",
    "is_human_controlled",
    "is_temporarily_ai_controlled",
    "is_unclaimed",
    "normalize_away_control_policy",
    "normalize_control",
    "release_temporary_controls",
    "set_away_control_policy",
    "set_control",
    "submission_block",
    "unclaimed_players",
]
