"""Audit long multiplayer combat campaigns against CheckResult authority invariants."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.commands.combat_resolver import CombatResolver  # noqa: E402
from src.engine.checks import resolve_check_request  # noqa: E402
from src.engine.game_instance import GameInstance  # noqa: E402
from src.rules.rule_system import RuleSystem  # noqa: E402


def _player(index: int, *, percentile: bool) -> dict[str, Any]:
    skill = 75 if index % 2 == 0 else 30
    return {
        "character_name": f"Player {index + 1}",
        "character_sheet": {
            "hp": 100,
            "max_hp": 100,
            "attributes": {"str": 10, "dex": 10, "int": 10},
            "skills": [{"name": "Audit Pistol", "value": skill}] if percentile else [],
            "equipment": [{"name": "Audit Weapon", "slot": "main_hand", "damage": 8}],
            "luck": 0,
        },
    }


def _target(index: int) -> dict[str, Any]:
    return {
        "name": f"Target {index + 1}",
        "character_name": f"Target {index + 1}",
        "hp": 100,
        "max_hp": 100,
        "armor": index * 2,
        "attributes": {"dex": 10 + index * 2},
    }


def _expected_coc(value: int, threshold: int) -> str:
    if value == 1:
        return "大成功"
    if value == 100 or (threshold < 50 and value >= 96):
        return "大失败"
    if value > threshold:
        return "失败"
    if value <= threshold // 5:
        return "极难成功"
    if value <= threshold // 2:
        return "困难成功"
    return "普通成功"


def _expected_d20(value: int, total: int, dc: int) -> str:
    if value == 20:
        return "大成功"
    if value == 1:
        return "大失败"
    return "成功" if total >= dc else "失败"


def _scenario(round_index: int, *, percentile: bool, players: int) -> tuple[GameInstance, RuleSystem]:
    rule_name = "freeform_coc.json" if percentile else "dnd5e.json"
    rule = RuleSystem.load(ROOT / "templates" / "rules" / rule_name)
    instance = GameInstance(
        ("audit", "coc" if percentile else "dnd", str(round_index)),
        rule_id=rule.rule_id,
    )
    instance.round_number = round_index
    instance.players = {
        f"p{index + 1}": _player(index, percentile=percentile)
        for index in range(players)
    }
    instance.npcs = {f"target-{index}": _target(index) for index in range(3)}
    d20_rolls = (1, 8, 12, 17, 20, 14)
    d100_rolls = (1, 30, 60, 96, 100, 45)

    for index in range(players):
        uid = f"p{index + 1}"
        target_index = index % 3
        target_ref = f"npc:target-{target_index}"
        check_id = f"r{round_index}-{uid}"
        value = d100_rolls[index % len(d100_rolls)] if percentile else d20_rolls[index % len(d20_rolls)]
        request = {
            "check_id": check_id,
            "actor_uid": uid,
            "dice_system": "d100" if percentile else "d20",
            "kind": "attack",
            "opponent": target_ref,
            "attribute": "str",
            # Deliberately untrusted: D&D must replace this with server AC;
            # CoC must replace it with the character's real skill threshold.
            "target": 99 if percentile else (1 if index % 2 == 0 else 40),
            "skill": "Audit Pistol" if percentile else "",
            "advantage_mode": "",
        }
        action = {
            "user_id": uid,
            "text": f"{uid} attacks Target {target_index + 1}",
            "selected_skill": "Audit Pistol" if percentile else "",
            "check_request": request,
            "dice_value": value,
            "dice_rolls": [value],
        }
        result = resolve_check_request(instance, action, rule)
        if result is None:
            raise AssertionError(f"round={round_index} player={uid}: missing CheckResult")
        if percentile:
            threshold = 75 if index % 2 == 0 else 30
            expected = _expected_coc(value, threshold)
            if result.get("threshold") != threshold or result.get("verdict") != expected:
                raise AssertionError(
                    f"round={round_index} player={uid}: CoC threshold/verdict mismatch"
                )
        else:
            expected_dc = 10 + target_index + target_index * 2
            expected = _expected_d20(value, int(result.get("total", 0)), expected_dc)
            if result.get("dc") != expected_dc or result.get("verdict") != expected:
                raise AssertionError(
                    f"round={round_index} player={uid}: D&D AC/verdict mismatch"
                )
        instance.action_queue.append(action)
        instance.last_checks.append(result)
    return instance, rule


def _audit_round(round_index: int, *, percentile: bool, players: int) -> dict[str, int]:
    instance, rule = _scenario(round_index, percentile=percentile, players=players)
    before = {ref: int(target["hp"]) for ref, target in instance.npcs.items()}

    # Combat resolution may consume CheckResult and roll damage in future rule
    # variants, but it must never invoke another hit-check RNG.  Current damage
    # is deterministic, so any direct RNG call in this path is a regression.
    import src.engine.dice_rng as dice_rng

    original_randint = dice_rng.random.randint

    def forbidden_hit_rng(*_args, **_kwargs):
        raise AssertionError("CombatResolver must not roll another hit check")

    dice_rng.random.randint = forbidden_hit_rng
    try:
        CombatResolver().resolve_combat(instance, "ignored", rule.combat_model)
    finally:
        dice_rng.random.randint = original_randint

    if len(instance.pending_combat_results) != players:
        raise AssertionError(
            f"round={round_index}: expected {players} combat records, "
            f"got {len(instance.pending_combat_results)}"
        )

    damage_by_target: dict[str, int] = defaultdict(int)
    running_hp = {f"npc:{target_id}": hp for target_id, hp in before.items()}
    total_damage = 0
    for index, record in enumerate(instance.pending_combat_results):
        uid = f"p{index + 1}"
        target_ref = f"npc:target-{index % 3}"
        check_id = f"r{round_index}-{uid}"
        if record.get("attacker_uid") != uid:
            raise AssertionError(f"round={round_index}: attacker order/crossing mismatch")
        if record.get("target_ref") != target_ref or record.get("check_id") != check_id:
            raise AssertionError(f"round={round_index} player={uid}: target/check crossing")
        damage = int(record.get("damage", -1))
        hp_before = int(record.get("target_hp_before", -1))
        hp_after = int(record.get("target_hp_after", -1))
        if hp_before != running_hp[target_ref] or hp_before - hp_after != damage:
            raise AssertionError(f"round={round_index} player={uid}: HP delta != final damage")
        running_hp[target_ref] = hp_after
        damage_by_target[target_ref] += damage
        total_damage += damage

    for target_id, initial_hp in before.items():
        target_ref = f"npc:{target_id}"
        final_hp = int(instance.npcs[target_id]["hp"])
        if initial_hp - final_hp != damage_by_target[target_ref]:
            raise AssertionError(f"round={round_index} target={target_id}: aggregate HP mismatch")
    return {"actions": players, "checks": players, "damage": total_damage}


def run_audit(*, rounds: int = 1_000, players: int = 6) -> dict[str, Any]:
    if rounds < 1:
        raise ValueError("rounds must be positive")
    if not 1 <= players <= 6:
        raise ValueError("players must be between 1 and 6")
    totals = {"actions": 0, "checks": 0, "damage": 0}
    errors: list[str] = []
    for percentile in (False, True):
        for round_index in range(1, rounds + 1):
            try:
                result = _audit_round(round_index, percentile=percentile, players=players)
                for key in totals:
                    totals[key] += result[key]
            except AssertionError as exc:
                if len(errors) < 20:
                    errors.append(("coc" if percentile else "dnd") + f": {exc}")
    return {
        "ok": not errors,
        "rounds_per_ruleset": rounds,
        "rulesets": 2,
        "players": players,
        "actions": totals["actions"],
        "checks": totals["checks"],
        "applied_damage": totals["damage"],
        "error_count": len(errors),
        "errors": errors,
    }


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=1_000, help="rounds per ruleset")
    parser.add_argument("--players", type=int, default=6, help="players per round (1-6)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()
    report = run_audit(rounds=args.rounds, players=args.players)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Combat long-campaign audit: " + ("PASS" if report["ok"] else "FAIL"))
        print(
            f"rounds_per_ruleset={report['rounds_per_ruleset']} players={report['players']} "
            f"actions={report['actions']} checks={report['checks']} "
            f"applied_damage={report['applied_damage']} errors={report['error_count']}"
        )
        for error in report["errors"]:
            print(f"ERROR: {error}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
