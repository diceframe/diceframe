"""D&D attack-mode authority shared by pre-roll projection and resolution."""
from __future__ import annotations
from src.engine.modules import ruleset_runtime

from typing import Any

from src.engine.modules.check_modes import scoped

from .obscurement import SENSE_KINDS


def context_id(engine: Any, instance: Any) -> str:
    combat = ruleset_runtime.state(instance).get("combat", {})
    order = combat.get("initiative", [])
    index = combat.get("turn_index", -1)
    current = order[index] if type(index) is int and 0 <= index < len(order) else ""
    return f"combat:{int(ruleset_runtime.state(instance).get('version', 0))}:{current}"


def condition_flags(
    registry: dict[str, dict[str, Any]], conditions: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate the registry-declared mechanical flags a condition set carries.

    Incapacitation and speed-zero gate intents, the auto-fail list feeds saving
    throws, and ``crit_on_hit_within_5`` upgrades an already-hit attack. Any
    single source turns a boolean on; the save list is the union of every
    source. The whole incapacitated family (paralyzed, stunned, unconscious,
    and sleep's plain ``incapacitated``) therefore works without engine
    branches naming each condition.
    """

    incapacitated = False
    speed_zero = False
    speed_halved = False
    auto_fail_saves: set[str] = set()
    crit_on_hit_within_5 = False
    for condition_id, entry in registry.items():
        if condition_id not in conditions:
            continue
        incapacitated = incapacitated or bool(entry.get("implies_incapacitated"))
        speed_zero = speed_zero or bool(entry.get("speed_zero"))
        speed_halved = speed_halved or bool(entry.get("speed_halved"))
        crit_on_hit_within_5 = crit_on_hit_within_5 or bool(
            entry.get("crit_on_hit_within_5")
        )
        auto_fail_saves.update(
            str(ability) for ability in entry.get("auto_fail_saves") or []
        )
    return {
        "incapacitated": incapacitated,
        "speed_zero": speed_zero,
        "speed_halved": speed_halved,
        "auto_fail_saves": auto_fail_saves,
        "crit_on_hit_within_5": crit_on_hit_within_5,
    }


def effective_speed(
    registry: dict[str, dict[str, Any]], conditions: dict[str, Any], base_speed: int,
) -> int:
    """回合开始的速度上限。

    ``speed_zero`` 压倒一切；否则先叠加全部 ``speed_modifier``（加法修正），
    再应用一次 ``speed_halved``（乘数效应）。两者顺序固定：先减再折半，而不是
    各自独立作用，否则"被减速 10 尺又减半"会算出两个不同的答案。多个减半来源
    仍只折半一次——D&D 的乘数规则不与自身相乘。
    """

    flags = condition_flags(registry, conditions)
    if flags["speed_zero"]:
        return 0
    total = 0
    for condition_id, entry in registry.items():
        if condition_id in conditions:
            total += int(entry.get("speed_modifier", 0) or 0)
    speed = max(0, int(base_speed) + total)
    if flags["speed_halved"]:
        speed //= 2
    return speed


def condition_check_bonus_rolls(
    registry: dict[str, dict[str, Any]], conditions: dict[str, Any],
    kind: str, rng: Any,
) -> list[dict[str, Any]]:
    """Roll the registry-declared per-check bonus dice for this check kind.

    ``kind`` is the rule-language semantic value (``attack`` covers every
    attack roll including spell attacks, ``saving_throw`` covers every save
    including repeat and concentration saves), never an event ``kind`` value.
    Each matching condition contributes one die per declared bonus, so the
    caller only sums the rolls and never names a condition. Catalog order is
    roll order, which keeps the dice trail reproducible.
    """

    rolls: list[dict[str, Any]] = []
    for condition_id, entry in registry.items():
        if condition_id not in conditions:
            continue
        for declaration in entry.get("check_bonus_dice") or []:
            if kind not in (declaration.get("kinds") or []):
                continue
            die = int(declaration["die"])
            rolls.append({
                "condition": str(condition_id), "die": die,
                "roll": int(rng.randint(1, die)),
            })
    return rolls


def attack_sources(
    registry: dict[str, dict], actor: dict, target: dict, range_disadvantage: bool,
    *, actor_senses: frozenset[str] = frozenset(),
    target_senses: frozenset[str] = frozenset(),
    distance: int | None = None,
) -> list[dict]:
    """Collect the registry-declared attack modifiers for this attack roll.

    A condition declares whether it matters when it sits on the attacker
    (``side == "actor"``) or on the target (``side == "target"``). ``negated_by``
    is checked against the *observer* -- the attacker for target-side
    declarations, the target for actor-side ones -- so a creature that can see
    through the trick does not grant/benefit from it.

    ``actor_senses`` / ``target_senses`` are the *derived* sense codes the two
    sides bring to this roll (Blindsight today).  A sense is not a condition:
    it depends on the distance between the two creatures, so it cannot be
    written onto either one of them.  It answers the same question the
    ``negated_by`` list asks, which is what keeps the registry the single place
    that says *what* cancels a condition while this function answers *whether
    the observer has it right now*.

    ``long_range`` stays code-generated: it is a positional fact of the attack,
    not a condition.

    ``within_ft`` / ``beyond_ft`` are the same kind of fact: Prone grants
    Advantage only to an attacker within 5 feet and Disadvantage beyond it, and
    writing that onto either creature would make it true at every distance.
    When the distance is unknown those declarations are skipped (fail closed: no
    Advantage from an unprovable claim).
    """

    sources = []
    for condition_id, entry in registry.items():
        for declaration in entry.get("attack_modes") or []:
            on_actor = declaration["side"] == "actor"
            holder = actor if on_actor else target
            observer = target if on_actor else actor
            senses = target_senses if on_actor else actor_senses
            if condition_id not in holder["conditions"]:
                continue
            within = declaration.get("within_ft")
            beyond = declaration.get("beyond_ft")
            if within is not None or beyond is not None:
                if distance is None:
                    continue
                if within is not None and distance > int(within):
                    continue
                if beyond is not None and distance <= int(beyond):
                    continue
            if any(
                code in observer["conditions"] or code in senses
                for code in declaration.get("negated_by") or []
            ):
                continue
            sources.append({"code": condition_id, "mode": declaration["mode"]})
    if range_disadvantage:
        sources.append({"code": "long_range", "mode": "disadvantage"})
    return sources


def mode_from_sources(sources: list[dict]) -> str:
    modes = {row["mode"] for row in sources}
    return next(iter(modes)) if len(modes) == 1 else ""


#: The only spell whose instance carries a ``mark_target_id`` today.  Foe Slayer,
#: Precise Hunter and the extra damage all read this one instance, so the ref
#: lives here instead of being spelled out at each reader.
HUNTERS_MARK_REF = "spell:hunter_s_mark"


def owned_mark(actor: dict) -> dict:
    """The Hunter's Mark instance this actor owns, or ``{}``.

    A mark is caster-bound state, never a condition on the target: two Rangers
    marking the same creature must not share a die, and a second caster must
    not inherit the first one's Advantage.  Reading the *attacker's* own
    concentration is what makes that true by construction.
    """

    concentration = actor.get("concentration")
    if not isinstance(concentration, dict):
        return {}
    if str(concentration.get("spell_ref") or "") != HUNTERS_MARK_REF:
        return {}
    return concentration


def marked_target_id(actor: dict) -> str:
    return str(owned_mark(actor).get("mark_target_id") or "")


def sense_declarations(engine: Any, observer: dict) -> list[dict[str, Any]]:
    """Every sense this creature owns, with the range it owns it at.

    This half is distance-independent on purpose: Blindsight 30 is a property
    of the creature and is shown as such, while *applying* it to a specific
    attack needs the distance between the two.  Splitting the two is what lets
    the same declaration answer both without a second copy of "30".
    """

    rows: list[dict[str, Any]] = []
    statblock = observer.get("senses")
    if isinstance(statblock, dict):
        # 怪物的感官来自 stat block，不来自特性声明；两者同一条链、同一条
        # 距离边界，谁先谁后都只影响来源标注，不影响结论。
        for code in ("blindsight", "truesight"):
            range_ft = int(statblock.get(code, 0) or 0)
            if range_ft > 0:
                rows.append({
                    "code": code, "range_ft": range_ft, "source": "statblock",
                })
    character = engine._feature_character(observer)
    # 特性给的感官按参数名认：``<code>_range_ft``。这样加一条感官（Witch Sight
    # 之后还会有）只改数据，不用在 combat 里写下这条特性的名字——GAP-43 的
    # 耦合守卫盯的就是这件事。
    for definition in engine.features.features_for(character):
        for code in SENSE_KINDS:
            range_ft = definition.parameters.get(f"{code}_range_ft")
            if isinstance(range_ft, bool) or not isinstance(range_ft, int):
                continue
            if range_ft > 0:
                rows.append({
                    "code": code, "range_ft": range_ft, "source": definition.id,
                })
    return rows


def observer_senses(
    engine: Any, observer: dict, *, distance: int | None,
) -> list[dict[str, Any]]:
    """Sourced sense codes this observer can apply at ``distance``.

    Blindsight is a range, not a flag: Feral Senses grants 30 feet, so the same
    creature cancels a Blur at 30 and does not at 31.  ``distance`` is ``None``
    only when no combat exists yet, which is the one case where nothing is
    within range of anything.

    Truesight is deliberately absent: the SRD's Blindsight sees through
    Invisibility and Blur, but it does not see through illusions, shapechangers
    or the Ethereal Plane, so it must never answer the ``truesight`` code.
    """

    if distance is None:
        return []
    return [
        row for row in sense_declarations(engine, observer)
        if distance <= int(row["range_ft"])
    ]


def vision_codes(engine: Any, observer: dict, *, distance: int | None) -> frozenset[str]:
    """Every negation code the observer brings to a sight question.

    Conditions and derived senses answer the same question -- "does this
    creature see through that?" -- so they are merged once here and matched
    against the registry's ``negated_by`` list by the caller.  Keeping the
    merge in one place is what stops "can I see it" from drifting away from
    "does its Blur still impose Disadvantage".
    """

    codes = {str(code) for code in (observer.get("conditions") or {})}
    codes.update(row["code"] for row in observer_senses(engine, observer, distance=distance))
    return frozenset(codes)


def mark_attack_sources(engine: Any, actor: dict, target: dict) -> list[dict[str, str]]:
    """Precise Hunter: Advantage against the creature your own mark names."""

    wanted = marked_target_id(actor)
    if not wanted or wanted != str(target.get("actor_id") or ""):
        return []
    # 声明自己说"给不给优势"，这里不替它决定：只判 has_feature 会让内容把
    # ``marked_target_attack_advantage`` 写成 false 时照样给优势（S0-F18 §4.2.3）。
    if not engine.features.feature_value(
        engine._feature_character(actor), "precise_hunter",
        "marked_target_attack_advantage", False,
    ):
        return []
    return [{"code": "precise_hunter", "mode": "advantage"}]


def mark_find_sources(
    engine: Any, actor: dict, skill_id: str, target_id: str,
) -> list[dict[str, str]]:
    """Advantage on the Wisdom checks made to find your marked quarry.

    The skill list and the spell's own identity both come from the catalog
    effect, so "Perception and Survival, and only while it is your current
    mark" is one declaration instead of three code paths.  Other abilities,
    other skills and other creatures answer with an empty list.
    """

    effect = engine.catalog.spell_effects.get(
        HUNTERS_MARK_REF.removeprefix("spell:")
    ) or {}
    allowed = {str(item) for item in effect.get("mark_find_skills") or ()}
    if str(skill_id) not in allowed:
        return []
    # 没有标记时 `marked_target_id` 与空串相等：不拦，"随便掷个察觉"就会拿到
    # 一条 hunter_s_mark 优势来源（S0-F18 §4.1.6）。与 mark_attack_sources
    # 的 `not wanted` 守卫保持同一写法。
    wanted = marked_target_id(actor)
    if not wanted or wanted != str(target_id):
        return []
    return [{"code": "hunter_s_mark", "mode": "advantage"}]


def attack_mode(engine: Any, instance: Any, actor: dict, target: dict,
                range_disadvantage: bool, *, use_grant: bool = True,
                extra_sources: tuple[dict, ...] = (),
                combat: dict[str, Any] | None = None) -> dict:
    """Both sides of the roll, with suppressed conditions already removed.

    ``combat`` is optional so the pure pre-roll callers can keep passing just
    views, but every caller that has it passes it: an aura that makes
    Frightened inert must change the attack mode, and a condition the engine
    suppresses in one place and not in another is worse than not suppressing it
    at all.
    """

    if combat is not None:
        actor = engine._effective_target(instance, combat, actor)
        target = engine._effective_target(instance, combat, target)
    if combat is None:
        combat = ruleset_runtime.state(instance).get("combat") or {}
    # 感官依赖两只生物之间的距离，所以在这里按本次攻击求值；写进任一方身上
    # 都会变成"隔着 60 尺也看得见"。
    distance: int | None = None
    if combat.get("positions") is not None:
        distance = engine._distance(
            combat, str(actor.get("actor_id") or ""), str(target.get("actor_id") or ""),
        )
    actor_senses = frozenset(
        row["code"] for row in observer_senses(engine, actor, distance=distance)
    )
    target_senses = frozenset(
        row["code"] for row in observer_senses(engine, target, distance=distance)
    )
    sources = attack_sources(
        engine.catalog.conditions, actor, target, range_disadvantage,
        actor_senses=actor_senses, target_senses=target_senses, distance=distance,
    )
    # 遮蔽只在"看不见"这一层影响攻击：轻度遮蔽只改感知检定，不改攻击。
    # 两个方向都要算——看不见目标给劣势，自己不被看见给优势，而"看不看得见"
    # 是成对事实（还要过感官），所以读 effective_obscurement 而不是原始等级。
    if combat.get("status") == "active":
        actor_id_ = str(actor.get("actor_id") or "")
        target_id_ = str(target.get("actor_id") or "")
        if actor_id_ and target_id_:
            if engine.effective_obscurement(instance, combat, actor_id_, target_id_)["blocks_sight"]:
                sources.append({"code": "obscured_target", "mode": "disadvantage"})
            if engine.effective_obscurement(instance, combat, target_id_, actor_id_)["blocks_sight"]:
                sources.append({"code": "unseen_attacker", "mode": "advantage"})
    # 意图声明的来源（如 Lucky）只在本次掷骰生效，不写进持续条件注册表：
    # 追加到来源列表后由同一个归一函数处理优劣势抵消。
    sources.extend(extra_sources)
    # Precise Hunter 也走这里：它回答的是"这一掷对谁有优势"，定义在攻击方
    # 自己持有的法术实例上，所以对武器攻击和法术攻击都成立。
    sources.extend(mark_attack_sources(engine, actor, target))
    if use_grant and actor["kind"] == "player":
        grant = scoped(instance, actor["actor_id"].removeprefix("player:"), context_id(engine, instance)).get("grant")
        if grant:
            sources.append({"code": "gm_grant", "mode": grant["mode"], "reason": grant["reason"]})
    return {"mode": mode_from_sources(sources), "sources": sources}


def policy(engine: Any, instance: Any, uid: str) -> dict:
    combat = ruleset_runtime.state(instance).get("combat", {})
    scope = context_id(engine, instance)
    result = {"kind": "d20_keep_high_low", "scope": "combat_attack", "context_id": scope,
              "blocked_reason": "", "previews": []}
    if combat.get("status") != "active":
        result["blocked_reason"] = "combat_required"
        return result
    actor_id = f"player:{uid}"
    order = combat.get("initiative", [])
    index = combat.get("turn_index", -1)
    if type(index) is not int or not 0 <= index < len(order) or order[index] != actor_id:
        result["blocked_reason"] = "not_this_seat"
        return result
    actor = engine._actor_view(instance, combat, actor_id)
    for available in engine.available_intents(instance, uid):
        if available["type"] == "cast_spell":
            for spell in available.get("spells", []):
                if spell.get("mode") != "spell_attack":
                    continue
                for target in available.get("targets", []):
                    intent = {"intent_id": "check-mode-spell-preview", "submitted_by": instance.gm_uid,
                              "type": "cast_spell", "actor_id": actor_id, "target_ids": [target["actor_id"]],
                              "spell_ref": spell["spell_ref"], "slot_level": spell["available_slot_levels"][0],
                              "expected_version": int(ruleset_runtime.state(instance)["version"])}
                    if engine.validate_intent(instance, intent).get("ok"):
                        result["previews"].append({"spell_ref": spell["spell_ref"], "target_id": target["actor_id"],
                            "label": f"{spell['name']} → {target.get('name') or target['actor_id']}",
                            **attack_mode(engine, instance, actor, engine._actor_view(instance, combat, target["actor_id"]), False, combat=combat)})
        if available["type"] != "attack":
            continue
        for weapon in available.get("weapons", []):
            for target in available.get("targets", []):
                intent = {"intent_id": "check-mode-preview", "submitted_by": instance.gm_uid,
                          "type": "attack", "actor_id": actor_id, "target_id": target["actor_id"],
                          "weapon_ref": weapon["weapon_ref"], "expected_version": int(ruleset_runtime.state(instance)["version"])}
                if not engine.validate_intent(instance, intent).get("ok"):
                    continue
                profile = engine._declared_attack(actor, intent)
                distance = engine._distance(combat, actor_id, target["actor_id"])
                normal = int(profile.get("thrown_range") or profile["range"])
                maximum = int(profile.get("long_range") or profile.get("thrown_range") or profile["range"])
                result["previews"].append({"weapon_ref": weapon["weapon_ref"], "target_id": target["actor_id"],
                    "label": f"{weapon.get('name') or weapon['weapon_ref']} → {target.get('name') or target['actor_id']}",
                    **attack_mode(engine, instance, actor, engine._actor_view(instance, combat, target["actor_id"]),
                                  normal < distance <= maximum, combat=combat)})
    return result
