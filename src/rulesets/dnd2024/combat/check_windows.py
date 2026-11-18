"""Post-roll d20 check windows for D&D 2024 combat.

A check window opens *after* a d20 check (or a damage roll) has been made and
*before* its consequences land.  The roller's dice are already on the table and
visible in the pending decision; a window owner may spend a resource to change
the result, and everyone else declines.

Two decision kinds share this machinery:

* ``check_window`` — the roll is a d20 check (attack roll, death save, or a
  concentration save). Outcomes: Lucky's disadvantage face rerolls one d20 and
  keeps the lower natural result; Cutting Words subtracts a Bardic Inspiration
  die from the total; Dark One's Own Luck adds a d10.
* ``damage_window`` — the roll is a damage roll. Cutting Words subtracts a
  Bardic Inspiration die from the damage after resistance is applied.

Invariants:

* The straight-through path is untouched: a window is only offered when a
  qualified owner exists, and declining emits exactly the events the original
  path would have emitted (the pending/cleared pair aside).
* Window dice are rolled at *resolve* time, never folded into the pre-existing
  dice sequence (``SP2`` §3.8 RNG order).
* Every consumption goes through ``dnd2024.class_resource.spent`` and, for
  reaction-priced windows, ``dnd2024.reaction.spent``; the reducer needs no
  new branch.
* The registry is the single place that names window codes and their
  resource/feature/reaction requirements. Resolution and validation only look
  up entries; no rule branches on a concrete feature id.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.engine.player_control import is_ai_controlled
from src.engine.world.contracts import clock_minutes
from src.rulesets.dnd2024.features.resources import resource_state

from .check_modes import mode_from_sources
from .primitives import (
    CombatIntentError,
    actor_kind,
    canonical as _canonical,
    player_actor as _player_actor,
)


CHECK_WINDOW_DECISION = "check_window"
DAMAGE_WINDOW_DECISION = "damage_window"
LUCKY_RESOURCE = "lucky_points"
BARDIC_RESOURCE = "bardic_inspiration"
DOOL_RESOURCE = "dark_ones_own_luck"


@dataclass(frozen=True, slots=True)
class WindowEntry:
    """One registered window: its surface, resource, feature, and cost."""

    code: str
    surface: str  # "attack_check" | "attack_damage" | "save"
    resource_id: str
    feature_id: str
    uses_reaction: bool
    # Holder-window entry: the cost is a held Bardic Inspiration die
    # instance, not a class-resource pool.  ``resource_id`` stays empty and
    # every pool read is replaced by the instance predicate.
    uses_instance: bool = False
    # Reroll entry: the cost buys a brand-new d20 (Countercharm, Indomitable,
    # Disciplined Survivor) instead of a bonus die added to the old total.
    # The settled check is replaced by the new roll, never merged with it.
    rerolls: bool = False
    # Natural-face entry: the cost rewrites the d20 itself (Stroke of Luck),
    # so no die is rolled and every downstream rule sees a natural 20.
    replaces_natural: bool = False


# The only registry of window codes. Adding a consumer means adding an entry
# and, at most, one eligibility predicate -- never a branch in resolution.py.
WINDOW_REGISTRY: dict[str, WindowEntry] = {
    "lucky_disadvantage": WindowEntry(
        code="lucky_disadvantage", surface="attack_check",
        resource_id=LUCKY_RESOURCE, feature_id="", uses_reaction=False,
    ),
    "cutting_words_check": WindowEntry(
        code="cutting_words_check", surface="attack_check",
        resource_id=BARDIC_RESOURCE, feature_id="cutting_words",
        uses_reaction=True,
    ),
    "dark_ones_own_luck": WindowEntry(
        code="dark_ones_own_luck", surface="save",
        resource_id=DOOL_RESOURCE, feature_id="dark_ones_own_luck",
        uses_reaction=False,
    ),
    "cutting_words_damage": WindowEntry(
        code="cutting_words_damage", surface="attack_damage",
        resource_id=BARDIC_RESOURCE, feature_id="cutting_words",
        uses_reaction=True,
    ),
    "bardic_inspiration": WindowEntry(
        code="bardic_inspiration", surface="attack_check",
        resource_id="", feature_id="bardic_inspiration",
        uses_reaction=False, uses_instance=True,
    ),
    "peerless_skill": WindowEntry(
        code="peerless_skill", surface="attack_check",
        resource_id=BARDIC_RESOURCE, feature_id="peerless_skill",
        uses_reaction=False,
    ),
    "bardic_inspiration_check": WindowEntry(
        code="bardic_inspiration_check", surface="ability_check",
        resource_id="", feature_id="bardic_inspiration",
        uses_reaction=False, uses_instance=True,
    ),
    "peerless_skill_check": WindowEntry(
        code="peerless_skill_check", surface="ability_check",
        resource_id=BARDIC_RESOURCE, feature_id="peerless_skill",
        uses_reaction=False,
    ),
    "cutting_words_ability": WindowEntry(
        code="cutting_words_ability", surface="ability_check",
        resource_id=BARDIC_RESOURCE, feature_id="cutting_words",
        uses_reaction=True,
    ),
    # S0-F3: failed-D20 windows.  ``tactical_mind`` spends a Second Wind use
    # and gives it back when the check still fails; ``stroke_of_luck``
    # rewrites the d20 face instead of adding to it.
    "tactical_mind": WindowEntry(
        code="tactical_mind", surface="ability_check",
        resource_id="second_wind", feature_id="tactical_mind",
        uses_reaction=False,
    ),
    "dark_ones_own_luck_check": WindowEntry(
        code="dark_ones_own_luck_check", surface="ability_check",
        resource_id=DOOL_RESOURCE, feature_id="dark_ones_own_luck",
        uses_reaction=False,
    ),
    "stroke_of_luck_attack": WindowEntry(
        code="stroke_of_luck_attack", surface="attack",
        resource_id="stroke_of_luck", feature_id="stroke_of_luck",
        uses_reaction=False, replaces_natural=True,
    ),
    "stroke_of_luck_check": WindowEntry(
        code="stroke_of_luck_check", surface="ability_check",
        resource_id="stroke_of_luck", feature_id="stroke_of_luck",
        uses_reaction=False, replaces_natural=True,
    ),
    "stroke_of_luck_save": WindowEntry(
        code="stroke_of_luck_save", surface="save",
        resource_id="stroke_of_luck", feature_id="stroke_of_luck",
        uses_reaction=False, replaces_natural=True,
    ),
    "countercharm": WindowEntry(
        code="countercharm", surface="save",
        resource_id="", feature_id="countercharm",
        uses_reaction=True, rerolls=True,
    ),
    "indomitable": WindowEntry(
        code="indomitable", surface="save",
        resource_id="indomitable", feature_id="indomitable",
        uses_reaction=False, rerolls=True,
    ),
    "disciplined_survivor": WindowEntry(
        code="disciplined_survivor", surface="save",
        resource_id="focus_points", feature_id="disciplined_survivor",
        uses_reaction=False, rerolls=True,
    ),
}


class CombatCheckWindowsMixin:
    """Offer, validate, and finish post-roll check/damage windows."""

    __slots__ = ()

    # ------------------------------------------------------ bardic instances

    def _logical_minutes(self, instance: Any) -> int:
        """The authoritative world clock as absolute logical minutes.

        One Bardic Inspiration hour lives on this clock: the authoritative
        flow owns it, there is no wall time and no background timer, so the
        same save resolves to the same minutes every time.
        """

        try:
            holder = getattr(instance, "world_state", None)
            raw = holder.get("clock") if isinstance(holder, dict) else None
            minutes = clock_minutes(raw)
            # 与 world 读取投影的降级一致：缺失或损坏的时钟读作第 1 天 00:00，
            # 而不是把这一桌卡在一场永远过不完的战斗里。
            return 0 if minutes is None else int(minutes)
        except Exception as error:  # pragma: no cover - clock is always present
            raise CombatIntentError("the world clock is unavailable") from error

    def _bardic_instances(self, combat: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            item for item in combat.get("bardic_instances") or []
            if isinstance(item, dict)
        ]

    def _active_bardic_instance(
        self, instance: Any, combat: dict[str, Any], holder_id: str,
    ) -> dict[str, Any] | None:
        """The holder's unexpired, unconsumed die, or ``None``.

        The same predicate hides the instance in every read path: "exactly at
        expiry or past it" is dead, only ``now < expires_at_minute`` lives.
        """

        now = self._logical_minutes(instance)
        for item in self._bardic_instances(combat):
            if str(item.get("holder_id") or "") != holder_id:
                continue
            if str(item.get("status") or "") != "active":
                continue
            if now < int(item.get("expires_at_minute", 0) or 0):
                return item
        return None

    def _bardic_die_sides(self, actor: dict[str, Any]) -> int:
        """The grantor's current Bardic die, or 0 when it cannot be granted."""

        sides = self.features.feature_value(
            self._feature_character(actor), "bardic_inspiration", "die_sides", 0,
        )
        if isinstance(sides, bool) or not isinstance(sides, int) or not 2 <= sides <= 100:
            return 0
        return sides

    def _active_bardic_view(
        self, instance: Any, combat: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """The projection the gameplay view shows: only live die instances.

        The read predicate is the same one the windows use, so an expired
        die disappears from the view at the same authority boundary where it
        becomes unusable.
        """

        now = self._logical_minutes(instance)
        return [
            deepcopy(item) for item in self._bardic_instances(combat)
            if str(item.get("status") or "") == "active"
            and now < int(item.get("expires_at_minute", 0) or 0)
        ]

    # ------------------------------------------------------------------ pools

    def _canonical_actor_character(self, instance: Any, actor_id: str) -> dict[str, Any]:
        """The canonical character that owns ``actor_id``'s resource pools.

        The combat actor view only projects pools that a feature declares by
        ``resource_id``, so window owners must read the canonical sheet.  Lucky,
        Bardic Inspiration, and Dark One's Own Luck are owned through their
        resource entries, not through a second ownership table.
        """

        kind, raw_id = actor_kind(actor_id)
        if kind == "player":
            if raw_id not in instance.players:
                return {}
            return _canonical(instance.get_character_sheet(raw_id))
        if kind == "companion":
            return self._companion(instance, raw_id).get("ruleset_character") or {}
        return {}

    def _window_pool_state(
        self, instance: Any, actor_id: str, resource_id: str,
    ) -> dict[str, Any]:
        return dict(resource_state(self._canonical_actor_character(instance, actor_id), resource_id))

    def _window_pool_available(
        self, instance: Any, actor_id: str, resource_id: str,
    ) -> bool:
        state = self._window_pool_state(instance, actor_id, resource_id)
        return bool(state) and int(state.get("current", 0) or 0) >= 1

    # -------------------------------------------------------------- eligibility

    def _window_die_sides(self, actor: dict[str, Any], code: str) -> int:
        """The die a window rolls for ``actor``, or 0 when it is unusable."""

        entry = WINDOW_REGISTRY.get(code)
        if entry is None:
            return 0
        if code == "lucky_disadvantage":
            sides = 20
        elif entry.feature_id:
            sides = self.features.feature_value(
                self._feature_character(actor), entry.feature_id, "die_sides", 0,
            )
        else:  # pragma: no cover - every feature window resolves above
            return 0
        if isinstance(sides, bool) or not isinstance(sides, int) or not 2 <= sides <= 100:
            return 0
        return sides

    def _cutting_words_range(self, actor: dict[str, Any]) -> int:
        """The declared reach of Cutting Words, or 0 when it is unusable.

        The reach lives in the catalog (``parameters.range_ft``) so a second
        subclass with a different reach needs no code change; an absent or
        malformed declaration closes the window rather than falling back to a
        remembered 60.
        """

        value = self.features.feature_value(
            self._feature_character(actor), "cutting_words", "range_ft", 0,
        )
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return 0
        return value

    def _cutting_words_bards(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        exclude: tuple[str, ...] = (),
    ) -> list[str]:
        """Party bards that can see ``attacker`` and still react.

        Ordered by actor id so multi-bard parties pick the same bard every
        time.  ``exclude`` carries bards that already spent on this attack's
        check face (one face per attack).
        """

        if attacker.get("side") != "enemy":
            return []
        if "invisible" in (attacker.get("conditions") or {}):
            return []
        order = [str(item) for item in combat.get("initiative", [])]
        candidates = sorted(
            [_player_actor(uid) for uid in instance.players]
            + [f"companion:{cid}" for cid in self._active_companions(instance)]
        )
        bards: list[str] = []
        for actor_id in candidates:
            if actor_id in exclude or actor_id not in order:
                continue
            actor = self._actor_view(instance, combat, actor_id)
            if actor.get("side") != "party" or int(actor.get("hp", 0) or 0) <= 0:
                continue
            if "blinded" in (actor.get("conditions") or {}):
                continue
            if not self._reaction_eligible(combat, actor):
                continue
            if not self.features.has_feature(
                self._feature_character(actor), "cutting_words",
            ):
                continue
            if not self._window_pool_available(instance, actor_id, BARDIC_RESOURCE):
                continue
            if self._window_die_sides(actor, "cutting_words_check") == 0:
                continue
            range_ft = self._cutting_words_range(actor)
            if range_ft == 0:
                continue
            if self._distance(combat, actor_id, str(attacker["actor_id"])) > range_ft:
                continue
            bards.append(actor_id)
        return bards

    def _attack_check_candidate(
        self, instance: Any, combat: dict[str, Any], code: str,
        attacker: dict[str, Any], defender: dict[str, Any], check: dict[str, Any],
        exclude_bards: tuple[str, ...] = (),
    ) -> str:
        """Return the owner actor id for ``code`` on this attack, or ``""``."""

        if code == "lucky_disadvantage":
            if defender.get("kind") not in {"player", "companion"}:
                return ""
            if not check.get("success"):
                return ""
            # Already disadvantaged: a second disadvantage source adds nothing.
            if str(check.get("advantage_mode") or "") == "disadvantage":
                return ""
            # A natural 1 always misses; lowering it changes nothing.
            if int(check.get("natural", 0) or 0) == 1:
                return ""
            if not self._window_pool_available(instance, str(defender["actor_id"]), LUCKY_RESOURCE):
                return ""
            return str(defender["actor_id"])
        if code == "cutting_words_check":
            if not check.get("success"):
                return ""
            bards = self._cutting_words_bards(instance, combat, attacker, exclude_bards)
            return bards[0] if bards else ""
        if code == "bardic_inspiration":
            # The holder's own failed attack roll.  A natural 1 stays a miss
            # no matter what is added to it, so it never opens a window.
            if check.get("success"):
                return ""
            if int(check.get("natural", 0) or 0) == 1:
                return ""
            holder_id = str(attacker["actor_id"])
            if attacker.get("side") != "party" or attacker.get("kind") not in {"player", "companion"}:
                return ""
            held = self._active_bardic_instance(instance, combat, holder_id)
            if held is None:
                return ""
            return holder_id
        if code == "peerless_skill":
            # The bard's own failed attack roll, paid from the Bard's own
            # Bardic Inspiration pool -- no held die required.
            if check.get("success"):
                return ""
            if int(check.get("natural", 0) or 0) == 1:
                return ""
            roller_id = str(attacker["actor_id"])
            if attacker.get("side") != "party" or attacker.get("kind") not in {"player", "companion"}:
                return ""
            if not self.features.has_feature(
                self._feature_character(attacker), "peerless_skill",
            ):
                return ""
            if not self._window_pool_available(instance, roller_id, BARDIC_RESOURCE):
                return ""
            if self._peerless_die_sides(attacker) == 0:
                return ""
            return roller_id
        if code == "stroke_of_luck_attack":
            # "Turn the roll into a 20" removes a natural 1 as well, so this
            # face is the one rescue that a nat-1 miss does not close.
            if check.get("success"):
                return ""
            if attacker.get("side") != "party":
                return ""
            return self._own_pool_owner(instance, attacker, code)
        return ""

    def _peerless_die_sides(self, actor: dict[str, Any]) -> int:
        sides = self.features.feature_value(
            self._feature_character(actor), "peerless_skill", "die_sides", 0,
        )
        if isinstance(sides, bool) or not isinstance(sides, int) or not 2 <= sides <= 100:
            return 0
        return sides

    def _failed_attack_windows(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        check: dict[str, Any],
    ) -> list[str]:
        """Holder-side codes for a failed attack roll, in registry order."""

        return [
            code for code in (
                "bardic_inspiration", "peerless_skill", "stroke_of_luck_attack",
            )
            if self._attack_check_candidate(instance, combat, code, attacker, attacker, check)
        ]

    # ------------------------------------------------- ability-check surfaces

    def _cutting_words_ability_bards(
        self, instance: Any, combat: dict[str, Any], roller_id: str,
        exclude: tuple[str, ...] = (),
    ) -> list[str]:
        """Party bards that can see ``roller_id`` and still react.

        The 2024 text says "a creature that you can see", with no side
        requirement and no hearing/Charmed clause, so this face accepts any
        roller -- unlike the attack face, which only reacts to hostile rolls.
        """

        roller = self._actor_view(instance, combat, roller_id)
        if "invisible" in (roller.get("conditions") or {}):
            return []
        order = [str(item) for item in combat.get("initiative", [])]
        candidates = sorted(
            [_player_actor(uid) for uid in instance.players]
            + [f"companion:{cid}" for cid in self._active_companions(instance)]
        )
        bards: list[str] = []
        for actor_id in candidates:
            if actor_id == roller_id or actor_id in exclude or actor_id not in order:
                continue
            actor = self._actor_view(instance, combat, actor_id)
            if actor.get("side") != "party" or int(actor.get("hp", 0) or 0) <= 0:
                continue
            if "blinded" in (actor.get("conditions") or {}):
                continue
            if not self._reaction_eligible(combat, actor):
                continue
            if not self.features.has_feature(
                self._feature_character(actor), "cutting_words",
            ):
                continue
            if not self._window_pool_available(instance, actor_id, BARDIC_RESOURCE):
                continue
            if self._window_die_sides(actor, "cutting_words_check") == 0:
                continue
            range_ft = self._cutting_words_range(actor)
            if range_ft == 0:
                continue
            if self._distance(combat, actor_id, roller_id) > range_ft:
                continue
            bards.append(actor_id)
        return bards

    def _ability_check_candidate(
        self, instance: Any, combat: dict[str, Any], code: str, roller_id: str,
        check: dict[str, Any], exclude_bards: tuple[str, ...] = (),
    ) -> str:
        roller = self._actor_view(instance, combat, roller_id)
        if code in {"bardic_inspiration_check", "peerless_skill_check"}:
            if check.get("success"):
                return ""
            if roller.get("side") != "party" or roller.get("kind") not in {"player", "companion"}:
                return ""
            if code == "bardic_inspiration_check":
                if self._active_bardic_instance(instance, combat, roller_id) is None:
                    return ""
                return roller_id
            if not self.features.has_feature(
                self._feature_character(roller), "peerless_skill",
            ):
                return ""
            if not self._window_pool_available(instance, roller_id, BARDIC_RESOURCE):
                return ""
            if self._peerless_die_sides(roller) == 0:
                return ""
            return roller_id
        if code == "cutting_words_ability":
            if not check.get("success"):
                return ""
            bards = self._cutting_words_ability_bards(
                instance, combat, roller_id, exclude_bards,
            )
            return bards[0] if bards else ""
        if code == "dark_ones_own_luck_check":
            # The die is legal on either polarity: the wording is "after you
            # make an ability check and see the result", not "on a failure".
            return self._dool_owner(instance, roller)
        if code in {"tactical_mind", "stroke_of_luck_check"}:
            if check.get("success"):
                return ""
            if roller.get("side") != "party":
                return ""
            return self._own_pool_owner(instance, roller, code)
        return ""

    def _ability_window_codes(
        self, instance: Any, combat: dict[str, Any], roller: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any],
    ) -> list[str]:
        """Ordered ability-check faces for this result.

        A failed check walks the roller's own cheap rescues first (Tactical
        Mind refunds itself when the check still fails, the held die costs the
        holder, Peerless costs the bard), then Dark One's Own Luck's die, and
        Stroke of Luck last because it is the only one that guarantees the
        result.  A successful check only has the two faces that can still
        change it: Cutting Words and the after-the-roll Dark Luck die.
        """

        if check.get("success"):
            return ["cutting_words_ability", "dark_ones_own_luck_check"]
        return [
            "tactical_mind", "bardic_inspiration_check", "peerless_skill_check",
            "dark_ones_own_luck_check", "stroke_of_luck_check",
        ]

    def _open_ability_window(
        self, instance: Any, combat: dict[str, Any], roller: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any],
        codes: list[str], source: str, target_id: str,
    ) -> dict[str, Any] | None:
        roller_id = str(roller["actor_id"])
        offered = list(settlement.get("ability_offered") or [])
        for index, code in enumerate(codes):
            if code in offered:
                continue
            exclude = tuple(str(item) for item in settlement.get("exclude_bards") or ())
            owner_id = self._ability_check_candidate(
                instance, combat, code, roller_id, check, exclude,
            )
            if not owner_id:
                continue
            if code == "bardic_inspiration_check":
                held = self._active_bardic_instance(instance, combat, owner_id)
                die_sides = int(held["die_sides"]) if held else 0
            elif code == "peerless_skill_check":
                die_sides = self._peerless_die_sides(roller)
            else:
                owner = self._actor_view(instance, combat, owner_id)
                die_sides = (
                    0 if WINDOW_REGISTRY[code].replaces_natural
                    else self._window_die_sides(owner, code)
                )
            if die_sides == 0 and not WINDOW_REGISTRY[code].replaces_natural:
                continue
            remaining = codes[index + 1:]
            # Cutting Words is per bard; its owners are excluded by the
            # finisher. Every other face is offered once per check, even if
            # declined or refunded after an unsuccessful rescue.
            if code != "cutting_words_ability":
                settlement["ability_offered"] = [*offered, code]
            settlement["remaining_windows"] = list(remaining)
            return self._build_check_window(
                instance, code=code, surface="ability_check", owner_id=owner_id,
                check=check, settlement=settlement, source=source,
                roller_id=roller_id, attacker_id=roller_id,
                target_id=target_id, remaining_windows=remaining, die_sides=die_sides,
            )
        return None

    def _ability_check_window_offer(
        self, instance: Any, combat: dict[str, Any], roller: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The first window on an ability check, or ``None``.

        Both polarities exist on this surface: a failed check can be rescued
        by the roller's own die, and a successful one can be cut by a Lore
        bard's Cutting Words.  Dark One's Own Luck applies to either.
        """

        codes = self._ability_window_codes(instance, combat, roller, check, settlement)
        return self._open_ability_window(
            instance, combat, roller, check, settlement, codes,
            str(settlement.get("intent_id") or ""),
            str(settlement.get("target_id") or ""),
        )

    def _next_ability_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Re-derive the ability-check chain against the new result."""

        roller_id = str(decision["actor_id"])
        roller = self._actor_view(instance, combat, roller_id)
        # Re-evaluate both polarities, but never reset asking history when
        # the queue is exhausted. Also seed the held face for older pending
        # decisions that predate ability_offered.
        code = str(decision["window_code"])
        offered = list(settlement.get("ability_offered") or [])
        if code != "cutting_words_ability" and code not in offered:
            settlement["ability_offered"] = [*offered, code]
        codes = self._ability_window_codes(
            instance, combat, roller, check, settlement,
        )
        return self._open_ability_window(
            instance, combat, roller, check, settlement, codes,
            str(decision.get("source") or ""),
            str(decision.get("target_id") or ""),
        )

    def _dool_owner(self, instance: Any, actor: dict[str, Any]) -> str:
        actor_id = str(actor["actor_id"])
        if actor.get("kind") not in {"player", "companion"}:
            return ""
        if not self.features.has_feature(
            self._feature_character(actor), "dark_ones_own_luck",
        ):
            return ""
        if not self._window_pool_available(instance, actor_id, DOOL_RESOURCE):
            return ""
        if self._window_die_sides(actor, "dark_ones_own_luck") == 0:
            return ""
        return actor_id

    # ------------------------------------------------------- failed-D20 faces

    def _declared(self, actor: dict[str, Any], feature_id: str, key: str, default: Any) -> Any:
        return self.features.feature_value(
            self._feature_character(actor), feature_id, key, default,
        )

    @staticmethod
    def _class_level(character: dict[str, Any] | None) -> int:
        build = (character or {}).get("build") or {}
        levels = build.get("class_levels")
        if isinstance(levels, list) and levels:
            return sum(
                int(row.get("level", 0) or 0)
                for row in levels if isinstance(row, dict)
            )
        return int(build.get("level", 0) or 0)

    def _own_pool_owner(
        self, instance: Any, actor: dict[str, Any], code: str,
    ) -> str:
        """The roller as a window owner when the declared pool can still pay."""

        entry = WINDOW_REGISTRY[code]
        if actor.get("kind") not in {"player", "companion"}:
            return ""
        if not self.features.has_feature(
            self._feature_character(actor), entry.feature_id,
        ):
            return ""
        if entry.resource_id and not self._window_pool_available(
            instance, str(actor["actor_id"]), entry.resource_id,
        ):
            return ""
        return str(actor["actor_id"])

    def _countercharm_bard(
        self, instance: Any, combat: dict[str, Any], roller: dict[str, Any],
        settlement: dict[str, Any],
    ) -> str:
        """A Bard 7 who can still react to this charmed/frightened failure.

        2024 Countercharm has no sight clause, so the only positional fact is
        the 30-foot reach.  The effect qualifies when the condition the save is
        against is one the Bard's own declaration names, and the declaration
        owns the reach as well.
        """

        condition = str(settlement.get("condition") or "")
        if not condition:
            return ""
        roller_id = str(roller["actor_id"])
        order = {str(item) for item in combat.get("initiative", [])}
        candidates = sorted(
            [_player_actor(uid) for uid in instance.players]
            + [f"companion:{cid}" for cid in self._active_companions(instance)]
        )
        for actor_id in candidates:
            if actor_id not in order:
                continue
            bard = self._actor_view(instance, combat, actor_id)
            if bard.get("side") != "party" or int(bard.get("hp", 0) or 0) <= 0:
                continue
            character = self._feature_character(bard)
            if not self.features.has_feature(character, "countercharm"):
                continue
            applies = [
                str(item) for item in (
                    self._declared(bard, "countercharm", "applies_conditions", []) or []
                )
            ]
            if condition not in applies:
                continue
            reach = self._declared(bard, "countercharm", "range_ft", 0)
            if isinstance(reach, bool) or not isinstance(reach, int) or reach <= 0:
                continue
            if self._distance(combat, actor_id, roller_id) > reach:
                continue
            if not self._reaction_eligible(combat, bard):
                continue
            return actor_id
        return ""

    def _save_window_candidates(
        self, instance: Any, combat: dict[str, Any], roller: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any],
        *, allow_success_face: bool,
    ) -> list[tuple[str, str]]:
        """Ordered ``(code, owner)`` pairs for one saving throw.

        Order is the policy: a guaranteed rewrite first (Stroke of Luck), then
        the rerolls the roller pays for, then Dark One's Own Luck's die, and
        last the ally's Reaction.  Every entry costs its own resource, so the
        order never hides a cheaper option -- it only fixes which one is asked
        first, which is what makes a replay reproducible.

        Death saves use the same reroll faces, but natural 1 remains a failure
        regardless of the total. Only replacing the natural face can save it.
        """

        if roller.get("side") != "party":
            return []
        if roller.get("kind") not in {"player", "companion"}:
            return []
        surface = str(settlement.get("surface") or "")
        failed = not self._nonattack_check_success(check)
        # Replacement/reroll faces can rescue a natural 1 even when its total
        # reaches 10; adding a bonus die cannot change that natural face.
        reroll_faces = ("stroke_of_luck_save", "indomitable", "disciplined_survivor")
        pairs: list[tuple[str, str]] = []
        if failed:
            for code in reroll_faces:
                owner_id = self._own_pool_owner(instance, roller, code)
                if owner_id:
                    pairs.append((code, owner_id))
            # A natural 1 on a death save is two failures no matter the total,
            # so adding a die can never help it.  Faces that *replace* the roll
            # (Stroke of Luck, and now the rerolls above) still can, which is
            # why only Dark One's Own Luck is skipped here.
            skip_dool = (
                surface == "death_save" and int(check.get("natural", 0) or 0) == 1
            )
            owner_id = "" if skip_dool else self._dool_owner(instance, roller)
            if owner_id:
                pairs.append(("dark_ones_own_luck", owner_id))
            bard = self._countercharm_bard(instance, combat, roller, settlement)
            if bard:
                pairs.append(("countercharm", bard))
        elif allow_success_face:
            # "after seeing the roll but before any of the roll's effects
            # occur" is not a failure-only wording; the die is offered on a
            # success too, where it can only widen the margin.
            owner_id = self._dool_owner(instance, roller)
            if owner_id:
                pairs.append(("dark_ones_own_luck", owner_id))
        return pairs

    def _save_window_offer(
        self, instance: Any, combat: dict[str, Any], roller: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any], surface: str,
        *, source: str, target_id: str = "", allow_success_face: bool = True,
    ) -> dict[str, Any] | None:
        """The next legal window on this saving throw, or ``None``."""

        offered = tuple(str(item) for item in settlement.get("offered") or ())
        pairs = [
            (code, owner_id)
            for code, owner_id in self._save_window_candidates(
                instance, combat, roller, check, settlement,
                allow_success_face=allow_success_face,
            )
            if code not in offered
        ]
        if not pairs:
            return None
        code, owner_id = pairs[0]
        entry = WINDOW_REGISTRY[code]
        owner = self._actor_view(instance, combat, owner_id)
        settlement["offered"] = [*offered, code]
        die_sides = (
            0 if entry.replaces_natural else self._window_die_sides(owner, code)
        )
        window = self._build_check_window(
            instance, code=code, surface=surface, owner_id=owner_id, check=check,
            settlement=settlement, source=source, roller_id=str(roller["actor_id"]),
            attacker_id="", target_id=target_id, remaining_windows=[],
            die_sides=die_sides,
        )
        if entry.rerolls:
            window["reroll_mode"] = str(
                self._declared(owner, entry.feature_id, "reroll_mode", "") or ""
            )
            window["reroll_bonus"] = self._reroll_bonus(owner, entry.feature_id)
        return window

    def _reroll_bonus(self, actor: dict[str, Any], feature_id: str) -> int:
        """The flat bonus a reroll feature adds, read from its declaration."""

        if not self._declared(actor, feature_id, "reroll_bonus_from_class_level", False):
            return 0
        return self._class_level(self._feature_character(actor))

    def _death_save_check_window(
        self, instance: Any, combat: dict[str, Any], actor: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The first window on a failing death save, or ``None``.

        A decisive save (natural 20, or a total of 10+) opens nothing.  Every
        legal face is available here, including the rerolls: a reroll discards
        the old die, so a failed death save can be rerolled into a success and
        a natural 1 stops being two failures once its face is gone.  Only
        *adding* a die is skipped on a natural 1 -- see
        ``_save_window_candidates``.
        """

        payload = dict(settlement or {})
        payload.setdefault("surface", "death_save")
        return self._save_window_offer(
            instance, combat, actor, check, payload, "death_save",
            source=str(actor["actor_id"]), allow_success_face=False,
        )

    def _concentration_with_dool(
        self, instance: Any, combat: dict[str, Any], target: dict[str, Any],
        damage: int, rng: Any, settlement: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Concentration save events, pausing before the failure lands.

        ``_concentration_events`` is untouched: this wrapper only replaces the
        failure tail when a window can still change it.  A successful save (or
        no eligible owner) is returned byte-for-byte.
        """

        events = self._concentration_events(instance, combat, target, damage, rng)
        if not events or events[0].get("success"):
            return events
        payload = dict(settlement or {})
        payload["surface"] = "concentration"
        payload["damage"] = int(damage)
        window = self._save_window_offer(
            instance, combat, target, deepcopy(events[0]), payload,
            "concentration", source=str(target["actor_id"]),
            target_id=str(target["actor_id"]), allow_success_face=False,
        )
        if window is None:
            return events
        return [{"type": "dnd2024.decision.pending", **window}]

    # -------------------------------------------------------------- offer shape

    def _window_assigned_to(self, instance: Any, owner_id: str) -> str:
        kind, raw_id = actor_kind(owner_id)
        human = kind == "player" and not is_ai_controlled(instance, raw_id)
        return raw_id if human else str(getattr(instance, "gm_uid", "") or "")

    def _build_check_window(
        self, instance: Any, *, code: str, surface: str, owner_id: str,
        check: dict[str, Any], settlement: dict[str, Any] | None,
        source: str, roller_id: str, attacker_id: str, target_id: str,
        remaining_windows: list[str], die_sides: int,
    ) -> dict[str, Any]:
        entry = WINDOW_REGISTRY[code]
        return {
            # The owner is part of the id: two bards reacting to the same
            # attack are two distinct decisions, and a code-only id would make
            # the second one replay the first one's cleared row.
            "decision_id": f"check-window:{code}:{source}:{owner_id}",
            "kind": CHECK_WINDOW_DECISION,
            "surface": surface,
            "window_code": code,
            "window_owner_id": owner_id,
            "actor_id": roller_id,
            "attacker_id": attacker_id,
            "target_id": target_id,
            "assigned_to": self._window_assigned_to(instance, owner_id),
            "options": ["spend", "decline"],
            "check": deepcopy(check),
            "settlement": deepcopy(settlement),
            "remaining_windows": list(remaining_windows),
            "resource_id": entry.resource_id,
            "die_sides": die_sides,
        }

    def _attack_check_window_offer(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        defender: dict[str, Any], check: dict[str, Any], settlement: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The first check-face window of this attack, or ``None``.

        Only hostile attacks against a party defender are eligible: Lucky's
        disadvantage face protects the target, and Cutting Words protects an
        ally the bard can see. A friendly attack never opens either window.
        """

        if attacker.get("side") == defender.get("side"):
            return None
        if defender.get("side") != "party":
            return None
        codes = [
            code for code in ("lucky_disadvantage", "cutting_words_check")
            if self._attack_check_candidate(instance, combat, code, attacker, defender, check)
        ]
        if not codes:
            return None
        code = codes[0]
        owner_id = self._attack_check_candidate(instance, combat, code, attacker, defender, check)
        settlement["remaining_windows"] = codes[1:]
        return self._build_check_window(
            instance, code=code, surface="attack", owner_id=owner_id, check=check,
            settlement=settlement, source=str(settlement["intent"].get("intent_id") or ""),
            roller_id=str(attacker["actor_id"]), attacker_id=str(attacker["actor_id"]),
            target_id=str(defender["actor_id"]), remaining_windows=codes[1:],
            die_sides=self._window_die_sides(
                defender if code == "lucky_disadvantage" else self._actor_view(instance, combat, owner_id),
                code,
            ),
        )

    def _next_attack_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Re-derive the remaining check-face chain against the new result."""

        attacker = self._actor_view(instance, combat, str(decision["attacker_id"]))
        defender = self._actor_view(instance, combat, str(decision["target_id"]))
        codes = [str(item) for item in decision.get("remaining_windows") or []]
        exclude = tuple(str(item) for item in settlement.get("exclude_bards") or [])
        for code in codes:
            owner_id = self._attack_check_candidate(
                instance, combat, code, attacker, defender, check, exclude,
            )
            if not owner_id:
                continue
            remaining = codes[codes.index(code) + 1:]
            settlement["remaining_windows"] = remaining
            return self._build_check_window(
                instance, code=code, surface="attack", owner_id=owner_id, check=check,
                settlement=settlement,
                source=str(settlement["intent"].get("intent_id") or ""),
                roller_id=str(attacker["actor_id"]), attacker_id=str(attacker["actor_id"]),
                target_id=str(defender["actor_id"]), remaining_windows=remaining,
                die_sides=self._window_die_sides(
                    defender if code == "lucky_disadvantage"
                    else self._actor_view(instance, combat, owner_id),
                    code,
                ),
            )
        # Every named code has been offered.  Cutting Words is priced per bard,
        # not per attack: once the first bard declined (or spent and the attack
        # still hits), a second qualified bard gets its own serial turn.  Bards
        # that already had this face are excluded, so this cannot loop.
        handled = (
            *exclude,
            *(str(item) for item in settlement.get("check_declined_bards") or ()),
        )
        owner_id = self._attack_check_candidate(
            instance, combat, "cutting_words_check", attacker, defender, check, handled,
        )
        if not owner_id:
            return None
        settlement["remaining_windows"] = []
        return self._build_check_window(
            instance, code="cutting_words_check", surface="attack", owner_id=owner_id,
            check=check, settlement=settlement,
            source=str(settlement["intent"].get("intent_id") or ""),
            roller_id=str(attacker["actor_id"]), attacker_id=str(attacker["actor_id"]),
            target_id=str(defender["actor_id"]), remaining_windows=[],
            die_sides=self._window_die_sides(
                self._actor_view(instance, combat, owner_id), "cutting_words_check",
            ),
        )

    def _failed_attack_window_offer(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        check: dict[str, Any], settlement: dict[str, Any],
        *, exclude: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        """The first holder-side window on a failed attack roll, or ``None``.

        The roller himself is the owner: a held Bardic Inspiration die or the
        Peerless Skill expenditure both add a die to the missed d20 after the
        fact.  The hit-face windows stay in ``remaining_windows`` -- a die
        that turns the miss into a hit re-derives them against the new total.

        A creator can legitimately hold more than one of these faces (a Lore
        Bard holding someone else's die also owns Peerless Skill), so the whole
        ordered candidate list is recorded in the settlement: each settlement
        offers one face, and the next one is offered only after this one is
        declined or spent without flipping the roll.
        """

        codes = [
            code for code in self._failed_attack_windows(instance, combat, attacker, check)
            if code not in exclude
        ]
        if not codes:
            return None
        code = codes[0]
        owner_id = str(attacker["actor_id"])
        if code == "bardic_inspiration":
            held = self._active_bardic_instance(instance, combat, owner_id)
            die_sides = int(held["die_sides"]) if held else 0
        elif code == "stroke_of_luck_attack":
            die_sides = 0
        else:
            die_sides = self._peerless_die_sides(attacker)
        if die_sides == 0 and not WINDOW_REGISTRY[code].replaces_natural:
            return None
        settlement["failed_offered"] = [
            *(str(item) for item in settlement.get("failed_offered") or ()), code,
        ]
        settlement["remaining_windows"] = ["lucky_disadvantage", "cutting_words_check"]
        # The straight-through miss emits the weapon's miss mastery (Graze);
        # declining or spending without flipping the result must land in the
        # same place, so the finisher has to know this window came from the
        # miss face.
        settlement["miss_face"] = True
        return self._build_check_window(
            instance, code=code, surface="attack", owner_id=owner_id, check=check,
            settlement=settlement,
            source=str(settlement["intent"].get("intent_id") or ""),
            roller_id=owner_id, attacker_id=owner_id,
            target_id=str(settlement.get("target_id") or ""),
            remaining_windows=list(settlement["remaining_windows"]),
            die_sides=die_sides,
        )

    def _damage_window_offer(
        self, instance: Any, combat: dict[str, Any], attacker: dict[str, Any],
        defender: dict[str, Any], damage_event: dict[str, Any],
        intent: dict[str, Any], *, exclude_bards: tuple[str, ...] = (),
        next_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The Cutting Words damage-face window, or ``None``.

        Offered only for hostile attacks against a party defender whose
        damage is positive; the same bard cannot spend on both faces of one
        attack (``exclude_bards`` carries the check-face spender).
        """

        if attacker.get("side") == defender.get("side"):
            return None
        if defender.get("side") != "party":
            return None
        if int(damage_event.get("amount", 0) or 0) <= 0:
            return None
        bards = self._cutting_words_bards(instance, combat, attacker, exclude_bards)
        if not bards:
            return None
        owner_id = bards[0]
        die_sides = self._window_die_sides(
            self._actor_view(instance, combat, owner_id), "cutting_words_damage",
        )
        if die_sides == 0:
            return None
        return {
            "decision_id": f"damage-window:cutting_words_damage:"
                           f"{intent.get('intent_id', '')}:{defender['actor_id']}",
            "kind": DAMAGE_WINDOW_DECISION,
            "surface": "damage",
            "window_code": "cutting_words_damage",
            "window_owner_id": owner_id,
            "actor_id": str(attacker["actor_id"]),
            "attacker_id": str(attacker["actor_id"]),
            "target_id": str(defender["actor_id"]),
            "assigned_to": self._window_assigned_to(instance, owner_id),
            "options": ["spend", "decline"],
            "damage_event": deepcopy(damage_event),
            "damage_sides": die_sides,
            "resource_id": BARDIC_RESOURCE,
            "next_decision": deepcopy(next_decision) if next_decision else None,
        }

    # -------------------------------------------------------------- validation

    def _validate_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any],
    ) -> None:
        """Re-check ownership and cost at resolve time, not just at offer time."""

        if intent.get("option") != "spend":
            return
        entry = WINDOW_REGISTRY.get(str(decision.get("window_code") or ""))
        if entry is None:
            raise CombatIntentError("this check window is not available")
        owner_id = str(decision.get("window_owner_id") or "")
        owner = self._actor_view(instance, combat, owner_id)
        if entry.uses_instance:
            if self._active_bardic_instance(instance, combat, owner_id) is None:
                raise CombatIntentError("no Bardic Inspiration die is held")
            return
        if entry.resource_id and not self._window_pool_available(
            instance, owner_id, entry.resource_id,
        ):
            raise CombatIntentError("this check window resource is already spent")
        if entry.feature_id and not self.features.has_feature(
            self._feature_character(owner), entry.feature_id,
        ):
            raise CombatIntentError("this check window feature is not owned")
        if entry.uses_reaction and not self._reaction_eligible(combat, owner):
            raise CombatIntentError("the reaction is not available")

    # ----------------------------------------------------------------- finish

    def _finish_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        entry = WINDOW_REGISTRY[str(decision["window_code"])]
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        check = deepcopy(decision["check"])
        spent = intent["option"] == "spend"
        if spent:
            if entry.uses_instance:
                # The holder spends the held die itself, not a resource pool:
                # the instance is consumed because it was rolled, whatever the
                # new result turns out to be.
                events.append({
                    "type": "dnd2024.bardic.consumed",
                    "actor_id": decision["window_owner_id"],
                    "holder_id": decision["window_owner_id"],
                    "reason": "rolled",
                })
            elif entry.resource_id:
                events.append({
                    "type": "dnd2024.class_resource.spent", "actor_id": decision["window_owner_id"],
                    "resource_id": entry.resource_id,
                })
            if entry.uses_reaction:
                # Countercharm is the only window that costs a Reaction and no
                # pool at all, so the two costs are independent.
                events.append({
                    "type": "dnd2024.reaction.spent", "actor_id": decision["window_owner_id"],
                    "reason": "reaction_feature",
                })
            if entry.replaces_natural:
                check = self._replace_natural(instance, combat, decision, check)
            elif entry.rerolls:
                check = self._reroll_check(decision, check, rng)
            else:
                die = int(rng.randint(1, int(decision["die_sides"])))
                check = self._spend_check_window(decision, check, die)
        surface = str(decision.get("surface") or "")
        if surface == "attack":
            return self._finish_attack_check_window(
                instance, combat, decision, check, spent, events, rng,
            )
        if surface == "death_save":
            return self._finish_death_save_window(
                instance, combat, decision, check, spent, events, rng,
            )
        if surface == "concentration":
            return self._finish_concentration_window(
                instance, combat, decision, check, spent, events, rng,
            )
        if surface == "save":
            return self._finish_repeat_save_window(
                instance, combat, decision, check, spent, events, rng,
            )
        if surface == "ability_check":
            return self._finish_ability_check_window(
                instance, combat, decision, check, spent, events, rng,
            )
        raise CombatIntentError("this check window surface is not supported")  # pragma: no cover

    def _finish_ability_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], spent: bool, events: list[dict[str, Any]], rng: Any,
    ) -> list[dict[str, Any]]:
        """Land an ability check after its window, then continue the chain.

        The consequence of a hide check is the ``invisible`` condition; the
        straight-through path applies it on success only, so every windowed
        path has to land in exactly that place.
        """

        settlement = deepcopy(decision.get("settlement") or {})
        code = str(decision["window_code"])
        events.append(check)
        if spent and not check.get("success") and code in {
            "peerless_skill_check", "tactical_mind",
        }:
            # Both features give the use back when the roll still fails:
            # Peerless Skill says so outright, and Tactical Mind buys the d10
            # with a Second Wind use that is only spent on a rescue.
            resource = (
                BARDIC_RESOURCE if code == "peerless_skill_check" else "second_wind"
            )
            events.append({
                "type": "dnd2024.class_resource.refunded",
                "actor_id": str(decision["window_owner_id"]),
                "resource_id": resource,
                "reason": (
                    "peerless_failure" if code == "peerless_skill_check"
                    else "tactical_mind_failure"
                ),
            })
        exclude = tuple(str(item) for item in settlement.get("exclude_bards") or ())
        if spent and code == "cutting_words_ability":
            exclude = (*exclude, str(decision["window_owner_id"]))
            settlement["exclude_bards"] = list(exclude)
        elif not spent and code == "cutting_words_ability" and check.get("success"):
            declined = tuple(
                str(item) for item in settlement.get("check_declined_bards") or ()
            )
            settlement["check_declined_bards"] = list(
                (*declined, str(decision["window_owner_id"]))
            )
        settlement["exclude_bards"] = list({
            *exclude,
            *(str(item) for item in settlement.get("check_declined_bards") or ()),
        })
        next_window = self._next_ability_window(instance, combat, decision, check, settlement)
        if next_window is not None:
            events.append({"type": "dnd2024.decision.pending", **next_window})
            return events
        if check.get("success") and str(settlement.get("kind") or "") == "hide":
            events.append({
                "type": "condition.applied", "target_id": str(decision["actor_id"]),
                "condition": "invisible", "duration": "combat",
                "source_actor_id": str(decision["actor_id"]),
                "ends_on": ["attack_roll", "spell_cast"],
            })
        return events

    def _check_total(self, check: dict[str, Any], natural: int) -> int:
        """The total a check would have with ``natural`` as its d20 face.

        Every component stays separate so the audit trail can still be read
        piece by piece: modifier, per-check bonus dice, aura, a window's bonus
        die and a window's penalty each remain their own field.
        """

        return (
            int(natural)
            + int(check.get("modifier", 0) or 0)
            + int(check.get("check_bonus", 0) or 0)
            + int(check.get("aura_bonus", 0) or 0)
            + int(check.get("window_bonus", 0) or 0)
            - int(check.get("check_penalty", 0) or 0)
            + int(check.get("reroll_bonus", 0) or 0)
            # 破甲一击的 +5 不是 modifier 的一部分（事件里单独成项），所以重掷
            # 或改骰重算总额时必须自己把它加回来；漏掉会让同一份事件前后差 5。
            + int(check.get("sundering_bonus", 0) or 0)
        )

    def _replace_natural(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any],
    ) -> dict[str, Any]:
        """Rewrite the d20 face (Stroke of Luck) and re-judge every effect.

        No die is rolled and no RNG is consumed: the rule says "turn the roll
        into a 20", so the natural face and everything derived from it -- a
        hit, a critical, a save, an ability check -- move together.
        """

        code = str(decision["window_code"])
        entry = WINDOW_REGISTRY[code]
        owner = self._actor_view(instance, combat, str(decision["window_owner_id"]))
        natural = self._declared(owner, entry.feature_id, "replace_natural_with", 20)
        if isinstance(natural, bool) or not isinstance(natural, int) or not 2 <= natural <= 20:
            natural = 20
        check.update({
            "window": code,
            "window_resource": entry.resource_id,
            "window_roll": None,
            "window_bonus": 0,
            "window_bonus_rolls": [],
            "natural": natural,
            "total": self._check_total(check, natural),
        })
        if str(check.get("kind") or "") == "attack":
            check["critical"] = natural >= int(check.get("crit_threshold", 20) or 20)
            check.pop("critical_upgrade", None)
            check["success"] = self._attack_check_success(check)
        else:
            check["success"] = self._nonattack_check_success(check)
        return check

    def _reroll_check(
        self, decision: dict[str, Any], check: dict[str, Any], rng: Any,
    ) -> dict[str, Any]:
        """Roll a brand-new d20 and force the new result.

        The previous total is kept on the event (``rerolled_*``) instead of
        being merged into the new one: "you must use the new roll" means the
        old result stops existing as a result, not that the two are added.
        """

        code = str(decision["window_code"])
        entry = WINDOW_REGISTRY[code]
        mode = str(decision.get("reroll_mode") or "")
        rolls = [int(rng.randint(1, 20))]
        if mode in {"advantage", "disadvantage"}:
            rolls.append(int(rng.randint(1, 20)))
        if mode == "advantage":
            natural = max(rolls)
        elif mode == "disadvantage":
            natural = min(rolls)
        else:
            natural = rolls[0]
        keep = {
            key: deepcopy(check[key]) for key in (
                "type", "kind", "actor_id", "source_actor_id", "ability",
                "modifier", "check_bonus", "check_bonus_rolls", "aura_bonus",
                "aura_sources", "target", "repeat_save", "death_save_crit_threshold",
            ) if key in check
        }
        rerolled = {
            **keep,
            "rolls": rolls,
            "natural": natural,
            "window": code,
            "window_resource": entry.resource_id,
            "window_roll": None,
            "window_bonus": 0,
            "window_bonus_rolls": [],
            "reroll_mode": mode,
            "reroll_bonus": int(decision.get("reroll_bonus", 0) or 0),
            "rerolled_from": check.get("roll_id"),
            "roll_id": f"{check.get('roll_id') or 'roll'}-r1",
            "rerolled_rolls": list(check.get("rolls") or []),
            "rerolled_natural": check.get("natural"),
            "rerolled_total": check.get("total"),
        }
        if mode:
            rerolled["advantage_mode"] = mode
            rerolled["advantage_sources"] = [
                *list(check.get("advantage_sources") or []),
                {"code": code, "mode": mode},
            ]
        rerolled["total"] = self._check_total(rerolled, natural)
        rerolled["success"] = self._nonattack_check_success(rerolled)
        return rerolled

    def _spend_check_window(
        self, decision: dict[str, Any], check: dict[str, Any], die: int,
    ) -> dict[str, Any]:
        """Apply one spent window die to the held check event."""

        code = str(decision["window_code"])
        window = {
            "window": code,
            "window_resource": str(decision["resource_id"]),
            "window_roll": die,
        }
        if code == "lucky_disadvantage":
            # Lucky imposes disadvantage: append the lucky d20 and keep the
            # lower natural result.  Re-normalizing the sources keeps
            # advantage/disadvantage cancellation declarative: an advantage
            # roll cancelled by Lucky collapses to the single lucky d20.
            sources = list(check.get("advantage_sources") or [])
            sources.append({"code": "lucky", "mode": "disadvantage"})
            mode = mode_from_sources(sources)
            if mode == "disadvantage":
                natural = min(int(check["natural"]), die)
            elif mode == "advantage":  # pragma: no cover - Lucky only adds disadvantage
                natural = max(int(check["natural"]), die)
            else:
                # Advantage and Lucky's disadvantage cancel: the lucky d20 is
                # the single remaining roll, not a candidate in a keep-lowest
                # comparison against the discarded advantage pair.
                natural = die
            rolls = [*list(check.get("rolls") or []), die]
            check.update({
                **window,
                "advantage_mode": mode,
                "advantage_sources": sources,
                "rolls": rolls,
                "natural": natural,
                "total": natural + int(check.get("modifier", 0) or 0)
                + int(check.get("check_bonus", 0) or 0),
                "critical": natural >= int(check.get("crit_threshold", 20) or 20),
            })
            check["success"] = self._attack_check_success(check)
            return check
        if code == "cutting_words_check":
            check.update({
                **window,
                "total": int(check["total"]) - die,
                "check_penalty": die,
                "check_penalty_rolls": [die],
            })
            check["success"] = self._attack_check_success(check)
            if not check["success"] and check.get("critical_upgrade"):
                # The critical was the hit's own upgrade; without the hit there
                # is nothing left to be a critical of.
                check["critical"] = False
            return check
        if code == "dark_ones_own_luck":
            total = int(check.get("total", check.get("natural", 0)) or 0) + die
            check.update({
                **window,
                "window_bonus": die,
                "window_bonus_rolls": [die],
                "total": total,
            })
            check["success"] = self._nonattack_check_success(check)
            return check
        if code in {"bardic_inspiration", "peerless_skill"}:
            # Add the die to the failed d20 total and re-judge.  A natural 1
            # never opened the window on the attack face, so no special case
            # is needed here.
            total = int(check["total"]) + die
            check.update({
                **window,
                "window_bonus": die,
                "window_bonus_rolls": [die],
                "total": total,
            })
            check["success"] = self._attack_check_success(check)
            return check
        if code in {
            "bardic_inspiration_check", "peerless_skill_check",
            "tactical_mind", "dark_ones_own_luck_check",
        }:
            total = int(check["total"]) + die
            check.update({
                **window,
                "window_bonus": die,
                "window_bonus_rolls": [die],
                "total": total,
                "success": total >= int(check["target"]),
            })
            return check
        if code == "cutting_words_ability":
            total = int(check["total"]) - die
            check.update({
                **window,
                "total": total,
                "check_penalty": die,
                "check_penalty_rolls": [die],
                "success": total >= int(check["target"]),
            })
            return check
        raise CombatIntentError("this check window code is not supported")  # pragma: no cover

    @staticmethod
    def _nonattack_check_success(check: dict[str, Any]) -> bool:
        """Death-save natural faces override bonuses, just as at settlement."""

        if check.get("kind") == "death_save":
            natural = int(check.get("natural", 0) or 0)
            if natural == 1:
                return False
            if natural >= int(check.get("death_save_crit_threshold", 20) or 20):
                return True
        return int(check["total"]) >= int(check["target"])

    @staticmethod
    def _attack_check_success(check: dict[str, Any]) -> bool:
        """Attack success after a window: a natural 1 always misses.

        A critical from the d20 itself always hits.  A critical that was only
        *upgraded* after the hit landed (a paralyzed target within 5 feet) is
        not a d20 fact: it survives only while the reduced total still reaches
        the AC, which is exactly what a Cutting Words cut can take away.
        """

        natural = int(check.get("natural", 0) or 0)
        if natural == 1:
            return False
        if bool(check.get("critical")) and not bool(check.get("critical_upgrade")):
            return True
        return int(check["total"]) >= int(check["target"])

    def _finish_attack_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], spent: bool, events: list[dict[str, Any]], rng: Any,
    ) -> list[dict[str, Any]]:
        settlement = deepcopy(decision.get("settlement") or {})
        actor_id = str(decision["attacker_id"])
        target_id = str(decision["target_id"])
        actor = self._actor_view(instance, combat, actor_id)
        target = self._actor_view(instance, combat, target_id)
        events.append(check)
        if (
            spent and str(decision["window_code"]) == "peerless_skill"
            and not check.get("success")
        ):
            # Peerless Skill: "On a failure, the Bardic Inspiration isn't
            # expended."  The die was rolled and recorded, but the use comes
            # back in the same batch -- never spent-and-forgotten.
            events.append({
                "type": "dnd2024.class_resource.refunded",
                "actor_id": str(decision["window_owner_id"]),
                "resource_id": BARDIC_RESOURCE,
                "reason": "peerless_failure",
            })
        if settlement.get("rage_sustain"):
            events.append({
                "type": "dnd2024.condition.sustained", "target_id": actor_id,
                "condition": "rage",
            })
        exclude_bards = tuple(str(item) for item in settlement.get("exclude_bards") or [])
        spent_reactions = tuple(str(item) for item in settlement.get("spent_reactions") or [])
        if spent:
            code = str(decision["window_code"])
            if code == "cutting_words_check":
                exclude_bards = (*exclude_bards, str(decision["window_owner_id"]))
                spent_reactions = (*spent_reactions, str(decision["window_owner_id"]))
                # Persist the cost into the settlement so any remaining window
                # of this same attack cannot re-offer the bard already spent.
                settlement["exclude_bards"] = list(exclude_bards)
                settlement["spent_reactions"] = list(spent_reactions)
            if check.get("success"):
                next_window = self._next_attack_check_window(
                    instance, combat, decision, check, settlement,
                )
                if next_window is not None:
                    events.append({"type": "dnd2024.decision.pending", **next_window})
                    return events
        elif check.get("success"):
            # A declined window still used up this bard's turn at the check
            # face: the next candidate has to be someone else, or the same
            # bard would be offered the same face forever.  The damage face
            # keeps its own exclusion list instead, because declining costs
            # nothing and the bard may still spend on the damage roll.
            if str(decision["window_code"]) == "cutting_words_check":
                declined = tuple(
                    str(item) for item in settlement.get("check_declined_bards") or ()
                )
                settlement["check_declined_bards"] = list(
                    (*declined, str(decision["window_owner_id"]))
                )
            next_window = self._next_attack_check_window(
                instance, combat, decision, check, settlement,
            )
            if next_window is not None:
                events.append({"type": "dnd2024.decision.pending", **next_window})
                return events
        if not check.get("success") and settlement.get("miss_face"):
            # Still a miss: the remaining holder-side faces get their own
            # window, in registry order, each with a fresh decision.
            follow_up = self._failed_attack_window_offer(
                instance, combat, actor, check, settlement,
                exclude=tuple(str(item) for item in settlement.get("failed_offered") or ()),
            )
            if follow_up is not None:
                events.append({"type": "dnd2024.decision.pending", **follow_up})
                return events
        if check.get("success"):
            events.extend(self._hit_settlement_events(
                instance, combat, actor, target,
                deepcopy(settlement.get("weapon") or {}), check,
                deepcopy(settlement.get("intent") or {}), rng,
                ability=str(settlement.get("ability") or ""),
                modifier=int(settlement.get("modifier", 0) or 0),
                damage_formula=str(settlement.get("damage_formula") or ""),
                damage_type=str(settlement.get("damage_type") or ""),
                crit_on_hit=bool(settlement.get("crit_on_hit")),
                exclude_bards=exclude_bards,
                spent_reactions=spent_reactions,
            ))
        elif settlement.get("miss_face"):
            # The straight-through miss of this attack would have applied the
            # weapon's miss mastery; a declined or failed window changes
            # nothing about that.
            events.extend(self._miss_mastery_events(
                instance, combat, actor, target,
                deepcopy(settlement.get("weapon") or {}),
                int(settlement.get("modifier", 0) or 0),
                str(settlement.get("damage_type") or ""), rng,
            ))
        events.extend(self._consume_attack_conditions(actor_id, target_id, actor, target))
        return events

    def _finish_death_save_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], spent: bool, events: list[dict[str, Any]], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(decision["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        settlement = deepcopy(decision.get("settlement") or {})
        settlement.setdefault("surface", "death_save")
        follow_up = self._save_window_offer(
            instance, combat, actor, check, settlement, "death_save",
            source=str(decision.get("source") or actor_id),
            allow_success_face=False,
        )
        if follow_up is not None:
            # A declined or failed face leaves the save unresolved until every
            # legal face has had its turn: landing the failure first would
            # count it twice.
            events.append({"type": "dnd2024.decision.pending", **follow_up})
            return events
        events.append(check)
        events.extend(self._death_save_outcome_events(instance, combat, actor, check, rng))
        return events

    def _finish_concentration_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], spent: bool, events: list[dict[str, Any]], rng: Any,
    ) -> list[dict[str, Any]]:
        actor_id = str(decision["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        settlement = deepcopy(decision.get("settlement") or {})
        settlement.setdefault("surface", "concentration")
        follow_up = self._save_window_offer(
            instance, combat, actor, check, settlement, "concentration",
            source=str(decision.get("source") or actor_id),
            target_id=str(decision.get("target_id") or actor_id),
            allow_success_face=False,
        )
        if follow_up is not None:
            events.append({"type": "dnd2024.decision.pending", **follow_up})
            return events
        events.append(check)
        if not check.get("success"):
            events.append({
                "type": "dnd2024.concentration.ended",
                "actor_id": actor_id,
                "reason": "failed_concentration_save",
            })
        return events

    def _finish_repeat_save_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        check: dict[str, Any], spent: bool, events: list[dict[str, Any]], rng: Any,
    ) -> list[dict[str, Any]]:
        """Land a repeated saving throw, then continue the remaining ones.

        ``_save_repeat_events`` pauses mid-loop when a window opens; the row it
        had not reached yet travels in the settlement so this finisher can pick
        the same loop up at exactly that point.
        """

        actor_id = str(decision["actor_id"])
        actor = self._actor_view(instance, combat, actor_id)
        settlement = deepcopy(decision.get("settlement") or {})
        follow_up = self._save_window_offer(
            instance, combat, actor, check, settlement, "save",
            source=str(decision.get("source") or actor_id),
            target_id=str(decision.get("target_id") or ""),
        )
        if follow_up is not None:
            events.append({"type": "dnd2024.decision.pending", **follow_up})
            return events
        events.append(check)
        condition_id = str(settlement.get("condition") or "")
        if check.get("success") and condition_id:
            events.append({
                "type": "condition.removed", "target_id": actor_id,
                "condition": condition_id, "reason": "save_repeat",
            })
        remaining = [tuple(row) for row in settlement.get("remaining") or []]
        if remaining:
            events.extend(
                self._repeat_save_events(
                    instance, combat, actor_id, rng, pending=remaining,
                )
            )
        return events

    def _finish_damage_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any],
        intent: dict[str, Any], rng: Any,
    ) -> list[dict[str, Any]]:
        entry = WINDOW_REGISTRY["cutting_words_damage"]
        events: list[dict[str, Any]] = [{
            "type": "dnd2024.decision.cleared", "decision_id": decision["decision_id"],
            "option": intent["option"],
        }]
        damage_event = deepcopy(decision["damage_event"])
        spent = intent["option"] == "spend"
        if spent:
            owner_id = str(decision["window_owner_id"])
            die = int(rng.randint(1, int(decision["damage_sides"])))
            events.append({
                "type": "dnd2024.class_resource.spent", "actor_id": owner_id,
                "resource_id": entry.resource_id,
            })
            events.append({
                "type": "dnd2024.reaction.spent", "actor_id": owner_id,
                "reason": "reaction_feature",
            })
            penalty = min(int(damage_event["amount"]), die)
            amount = max(0, int(damage_event["amount"]) - penalty)
            damage_event["amount"] = amount
            damage_event["delta"] = -amount
            damage_event["penalty"] = die
            damage_event["penalty_rolls"] = [die]
            events.append({
                "type": "check.resolved", "kind": "damage_window",
                "actor_id": owner_id, "target_id": str(decision["target_id"]),
                "rolls": [die], "natural": die, "total": die,
                "penalty": penalty, "success": True,
            })
        next_decision = decision.get("next_decision")
        if isinstance(next_decision, dict):
            next_decision = deepcopy(next_decision)
            if spent:
                held = next_decision.get("damage_event")
                if isinstance(held, dict):
                    # A later hit-triggered spell holds the same base damage;
                    # the reduction must land before it is submitted.
                    held["amount"] = int(damage_event["amount"])
                    held["delta"] = -int(damage_event["amount"])
                    held["penalty"] = int(damage_event.get("penalty", 0) or 0)
                    if damage_event.get("penalty_rolls"):
                        held["penalty_rolls"] = list(damage_event["penalty_rolls"])
            events.append({"type": "dnd2024.decision.pending", **next_decision})
            return events
        target = self._actor_view(instance, combat, str(decision["target_id"]))
        events.extend(self._resolve_committed_hit(
            instance, combat, str(decision["attacker_id"]), target, damage_event,
            int(damage_event["amount"]), rng,
        ))
        return events

    # ------------------------------------------------------------- AI ladders

    def _automatic_check_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any], version: int,
    ) -> dict[str, Any] | None:
        owner_id = str(decision.get("window_owner_id") or "")
        kind, raw_id = actor_kind(owner_id)
        if kind == "player" and not is_ai_controlled(instance, raw_id):
            return None
        check = decision.get("check") or {}
        code = str(decision.get("window_code") or "")
        target = int(check.get("target", 0) or 0)
        die = int(decision.get("die_sides", 0) or 0)
        failed = not check.get("success")
        option = "decline"
        if code == "lucky_disadvantage" and check.get("success"):
            margin = int(check["total"]) - int(check["target"])
            # The lucky d20 replaces the natural result with the lower one;
            # only a natural high enough above the margin can flip the hit.
            option = "spend" if int(check["natural"]) - margin >= 2 else "decline"
        elif code == "cutting_words_check" and check.get("success"):
            option = (
                "spend" if not check.get("critical")
                and int(check["total"]) - die < int(check["target"])
                else "decline"
            )
        elif code in {
            "bardic_inspiration", "peerless_skill",
            "bardic_inspiration_check", "peerless_skill_check", "tactical_mind",
        } and failed:
            option = "spend" if int(check["total"]) + die >= target else "decline"
        elif code == "cutting_words_ability" and check.get("success"):
            option = (
                "spend"
                if int(check["total"]) - die < int(check["target"])
                else "decline"
            )
        elif code in {"dark_ones_own_luck", "dark_ones_own_luck_check"} and failed:
            surface = str(decision.get("surface") or "")
            current = (
                int(check["total"])
                if surface not in {"death_save"} else int(check["natural"])
            )
            option = "spend" if current + die >= target else "decline"
        elif code in {"stroke_of_luck_check", "stroke_of_luck_save"} and failed:
            # A natural 20 hits unless the remaining modifiers are so negative
            # that even that cannot reach the DC.
            best = 20 + int(check.get("modifier", 0) or 0) + int(
                check.get("check_bonus", 0) or 0
            ) + int(check.get("aura_bonus", 0) or 0)
            option = "spend" if best >= target else "decline"
        elif code == "stroke_of_luck_attack" and failed:
            option = "spend"
        elif code in {"countercharm", "indomitable", "disciplined_survivor"} and failed:
            # A reroll is worth it only when the best possible d20 can reach
            # the DC; the previous total is irrelevant because it is discarded.
            best = (
                20
                + int(check.get("modifier", 0) or 0)
                + int(check.get("check_bonus", 0) or 0)
                + int(check.get("aura_bonus", 0) or 0)
                + int(decision.get("reroll_bonus", 0) or 0)
            )
            option = "spend" if best >= target else "decline"
        return self._window_resolve_intent(instance, decision, version, option)

    def _automatic_damage_window(
        self, instance: Any, combat: dict[str, Any], decision: dict[str, Any], version: int,
    ) -> dict[str, Any] | None:
        owner_id = str(decision.get("window_owner_id") or "")
        kind, raw_id = actor_kind(owner_id)
        if kind == "player" and not is_ai_controlled(instance, raw_id):
            return None
        amount = int((decision.get("damage_event") or {}).get("amount", 0) or 0)
        option = "spend" if amount > 1 and int(decision["damage_sides"]) >= 2 else "decline"
        return self._window_resolve_intent(instance, decision, version, option)

    @staticmethod
    def _window_resolve_intent(
        instance: Any, decision: dict[str, Any], version: int, option: str,
    ) -> dict[str, Any]:
        return {
            "intent_id": f"auto:check-window:{version}:{decision['decision_id']}",
            "type": "decision.resolve",
            "expected_version": version,
            "submitted_by": str(getattr(instance, "gm_uid", "") or ""),
            "decision_id": str(decision["decision_id"]),
            "option": option,
        }
