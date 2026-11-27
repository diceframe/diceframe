"""Track-counted option pools: how many, which ones, and what they bind.

A pool is a class feature whose picks are *repeatable and cumulative* --
Eldritch Invocations is the first (`1/3/3/3/5/…` from the Warlock table), and
F33-F35 keep adding options to it.  Metamagic is the same shape with its counts
hardcoded in Python; here the declaration names the progression track instead,
so changing the table is a data edit.

Three things this module owns, and nothing else:

* **quantity** -- the total a character owes at a level, read from the track,
  minus what is already recorded.  A character who never picked at 1st level
  owes the full 3 at 2nd: that is a top-up, not a permanent hole.
* **prerequisites** -- declared per option (level, effect status, which other
  options must be owned, what a pick binds).  An option whose effect has no
  home yet is *projected but not selectable*: hiding it would make the panel
  look like the rule does not exist.
* **replacement** -- at most N swaps per level, refused when the dropped option
  is a prerequisite of one that stays.  The closure comes from the data, never
  from a hardcoded list: F34's Pact/Thirsting Blade chain is not visible here.

Storage stays where S0-B put it: one record in ``features.feature_choices``
keyed by ``(owner, feature_id, choice_id)`` whose ``selected_refs`` is the
*current* set.  Replacement rewrites that set; the per-level audit lives in
``progression.history[*].choices``, which keeps what was actually submitted.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

#: Where an option's picks are recorded.  Named so callers never spell the
#: nesting out by hand and drift from the writer.
CHOICE_RECORD_KEY = "feature_choices"

#: Requirement id prefix, matching the generic feature-choice slots.
REQUIREMENT_PREFIX = "feature_choice"

class InvocationPoolError(ValueError):
    """Raised when a pool declaration cannot be honoured at all."""


@dataclass(frozen=True, slots=True)
class Pick:
    """One selection: which option, and the object it is bound to."""

    ref: str
    object_ref: str = ""

    def as_ref(self) -> str | dict[str, str]:
        """The persisted shape: a bare ref, or a ref plus its binding."""

        if self.object_ref:
            return {"ref": self.ref, "object_ref": self.object_ref}
        return self.ref


@dataclass(frozen=True, slots=True)
class PoolPlan:
    """What one level-up asked for, and what it would write."""

    requirement: dict[str, Any] | None = None
    #: The record to write, or ``None`` when nothing changed.
    record: dict[str, Any] | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors


def _records(character: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    features = (character or {}).get("features")
    raw = features.get(CHOICE_RECORD_KEY) if isinstance(features, Mapping) else None
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, Mapping)]


def _matching(
    character: Mapping[str, Any] | None,
    owner: str,
    feature_id: str,
    choice_id: str,
) -> list[Mapping[str, Any]]:
    return [
        row for row in _records(character)
        if str(row.get("owner") or "") == owner
        and str(row.get("feature_id") or "") == feature_id
        and str(row.get("choice_id") or "") == choice_id
    ]


def parse_ref(raw: Any) -> Pick | None:
    """Read one stored or submitted element, in either accepted shape.

    Strings are what every pre-existing choice writes, so they keep working
    untouched; the object form exists only because a repeatable option has to
    remember *which* cantrip each pick was bound to.
    """

    if isinstance(raw, str):
        return Pick(ref=raw) if raw else None
    if isinstance(raw, Mapping):
        ref = str(raw.get("ref") or "")
        object_ref = str(raw.get("object_ref") or "")
        if not ref:
            return None
        return Pick(ref=ref, object_ref=object_ref)
    return None


def owned_picks(
    character: Mapping[str, Any] | None,
    owner: str,
    feature_id: str,
    choice_id: str,
) -> list[Pick]:
    """Every pick this character currently holds, in record order, deduped."""

    picks: list[Pick] = []
    seen: set[tuple[str, str]] = set()
    for row in _matching(character, owner, feature_id, choice_id):
        raw_refs = row.get("selected_refs")
        if not isinstance(raw_refs, list):
            continue
        for raw in raw_refs:
            pick = parse_ref(raw)
            if pick is None:
                continue
            key = (pick.ref, pick.object_ref)
            if key in seen:
                continue
            seen.add(key)
            picks.append(pick)
    return picks


def bound_objects(
    character: Mapping[str, Any] | None, feature_id: str, owner: str = "",
) -> tuple[str, ...]:
    """Objects one repeatable option is bound to.

    The effect side asks "is this cantrip boosted by that option"; it must read
    the same records the writer wrote, which is why this lives beside them
    instead of being re-derived per caller.

    The record is owned by the *pool* (``eldritch_invocations``), while the
    picks inside it name the options, so the filter is on ``pick.ref`` and
    never on the record's ``feature_id``.
    """

    wanted = f"feature:{feature_id}"
    objects: list[str] = []
    for row in _records(character):
        if owner and str(row.get("owner") or "") != owner:
            continue
        raw_refs = row.get("selected_refs")
        if not isinstance(raw_refs, list):
            continue
        for raw in raw_refs:
            pick = parse_ref(raw)
            if pick is None or pick.ref != wanted:
                continue
            if pick.object_ref and pick.object_ref not in objects:
                objects.append(pick.object_ref)
    return tuple(objects)


def owns_binding(
    character: Mapping[str, Any] | None, feature_id: str, object_ref: str,
    owner: str = "",
) -> bool:
    """Whether one pick of ``feature_id`` is bound to ``object_ref``."""

    return object_ref in bound_objects(character, feature_id, owner)


def track_value(
    progression: Mapping[str, Any], track: str, class_ref: str, level: int,
) -> int:
    """The track's value at ``level`` (0 when out of range).

    One reader for the three call sites (advancement passes the snapshot, the
    builder and the derive stage pass the catalog) so a level can never be
    counted off-by-one in only one of them.
    """

    classes = progression.get("classes") if isinstance(progression, Mapping) else None
    row = classes.get(str(class_ref).removeprefix("class:")) if isinstance(
        classes, Mapping
    ) else None
    values = row.get("tracks", {}).get(track) if isinstance(row, Mapping) else None
    if not isinstance(values, list) or level <= 0 or level > len(values):
        return 0
    return int(values[level - 1] or 0)


def _requirement_id(owner: str, feature_id: str, choice_id: str) -> str:
    return f"{REQUIREMENT_PREFIX}:{owner}:{feature_id}:{choice_id}"


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


def cantrip_candidates(
    character: Mapping[str, Any] | None,
    option: Mapping[str, Any],
    *,
    spells: Mapping[str, Mapping],
    spell_effects: Mapping[str, Mapping],
    owner_class_ref: str = "class:warlock",
) -> list[dict[str, Any]]:
    """Known Warlock cantrips this option could be bound to.

    Every predicate is a declared knob, so the same function answers Agonizing
    Blast ("deals damage"), Eldritch Spear ("10+ feet") and Repelling Blast
    ("attack roll") without three copies of "walk the cantrip list".
    """

    spellcasting = (character or {}).get("spellcasting")
    class_magic = spellcasting.get("class") if isinstance(spellcasting, Mapping) else None
    known = class_magic.get("cantrip_refs") if isinstance(class_magic, Mapping) else None
    if not isinstance(known, list):
        return []
    wants_damage = bool(option.get("invocation_cantrip_deals_damage"))
    wants_attack = bool(option.get("invocation_cantrip_attack_roll"))
    min_range = int(option.get("invocation_cantrip_min_range_ft", 0) or 0)
    rows: list[dict[str, Any]] = []
    for raw_ref in known:
        ref = str(raw_ref or "")
        if not ref.startswith("spell:"):
            continue
        spell_id = ref[len("spell:"):]
        entity = spells.get(spell_id)
        if not isinstance(entity, Mapping):
            continue
        # 0 环要当成 0 环：`get("level") or -1` 会把 0 变成 -1，然后一个戏法
        # 都不剩（这正是第一版漏掉全部候选的原因）。
        if entity.get("level") != 0:
            continue
        if owner_class_ref and owner_class_ref not in (entity.get("class_refs") or ()):
            continue
        effect = spell_effects.get(spell_id)
        effect = effect if isinstance(effect, Mapping) else {}
        reasons: list[str] = []
        if wants_damage and not effect.get("damage"):
            reasons.append("damage")
        if wants_attack and str(effect.get("mode") or "") != "spell_attack":
            reasons.append("attack_roll")
        if min_range and int(effect.get("range", 0) or 0) < min_range:
            reasons.append("range")
        rows.append({"value": ref, "available": not reasons,
                     "unavailable_reasons": reasons})
    return rows


def _option_reasons(
    option: Any,
    *,
    target_level: int,
    owned_refs: set[str],
) -> list[str]:
    """Why this option cannot be taken right now, in the panel's vocabulary.

    ``guided`` / ``not_automated`` map onto the existing
    ``effect_not_implemented`` label -- "this ruleset does not implement it yet"
    is the sentence a player needs; the declared ``invocation_unautomated_reason``
    rides along as a second entry for the audit view.
    """

    parameters = option.parameters
    reasons: list[str] = []
    status = str(parameters.get("invocation_effect_status") or "")
    if status != "complete":
        reasons.append("effect_not_implemented")
        detail = str(parameters.get("invocation_unautomated_reason") or "")
        if detail:
            reasons.append(detail)
    if target_level < int(option.minimum_level or 1):
        reasons.append("level")
    for required in parameters.get("invocation_requires_invocations") or ():
        if str(required) not in owned_refs:
            reasons.append("prerequisite_invocation")
            break
    return reasons


def _fillable_refs(
    projected: list[dict[str, Any]],
    options: Mapping[str, Any],
    owned_refs: set[str] | frozenset[str] = frozenset(),
) -> set[str]:
    """Options that could end up selected in this batch.

    An option blocked *only* by a prerequisite is fillable when that
    prerequisite is itself fillable, so the set has to be closed transitively --
    checking one level would still under-count a two-step chain.
    """

    blocked = {
        str(row["value"]): set(row["unavailable_reasons"]) for row in projected
    }
    fillable = {
        ref for ref, reasons in blocked.items()
        if not reasons
    }
    changed = True
    while changed:
        changed = False
        for ref, reasons in blocked.items():
            if ref in fillable:
                continue
            # 除"前置"以外的原因（等级、效果未实现）都不能靠同一批补上。
            if reasons - {"prerequisite_invocation"}:
                continue
            option = options.get(ref)
            if option is None:
                continue
            requires = option.parameters.get("invocation_requires_invocations") or ()
            if all(str(item) in fillable or str(item) in owned_refs for item in requires):
                fillable.add(ref)
                changed = True
    return fillable


def _pick_capacity(
    row: Mapping[str, Any], bind_options: Mapping[str, list[dict[str, Any]]],
    *, selectable: bool,
) -> int:
    """How many *picks* one projected option can still supply.

    A repeatable option is one row but several picks: Agonizing Blast bound to
    two different cantrips fills two slots.  Counting rows instead of picks
    under-reports what the character can legally choose, which then caps
    ``count`` and rejects a submission that was fillable.
    """

    if not selectable:
        return 0
    binds = bind_options.get(str(row["value"]))
    if binds is None:
        return 1
    return sum(1 for item in binds if item["available"])


def _pick_errors(
    pick: Pick,
    *,
    definition_id: str,
    options: Mapping[str, Any],
    reasons_by_ref: Mapping[str, list[str]],
    owned: list[Pick],
    picked_so_far: list[Pick],
    batch_refs: set[str] | frozenset[str] | None = None,
    bind_options: Mapping[str, list[dict[str, Any]]],
    target_level: int,
) -> list[str]:
    """Every reason one submitted pick is illegal, in submission order.

    Prerequisites are evaluated against ``owned + picked_so_far``: the SRD lets a
    level grant two options where one requires the other (F34's Pact of the Blade
    plus Thirsting Blade), and order inside one submission must not decide
    whether that is legal.  Everything else (level, effect status, ownership,
    binding) is order-independent and comes from the projection.
    """

    option = options.get(pick.ref)
    if option is None:
        return [f"{pick.ref} is not a declared option of {definition_id}"]
    allowed = ({item.ref for item in picked_so_far} if batch_refs is None else set(batch_refs)) | {item.ref for item in owned}
    reasons = [
        reason for reason in (reasons_by_ref.get(pick.ref) or [])
        if reason != "prerequisite_invocation"
    ]
    for required in option.parameters.get("invocation_requires_invocations") or ():
        if str(required) not in allowed:
            reasons.append("prerequisite_invocation")
            break
    if reasons:
        return [f"{pick.ref} is not available at level {target_level}: {reasons[0]}"]
    repeatable = bool(option.parameters.get("invocation_repeatable"))
    if not repeatable and any(other.ref == pick.ref for other in owned):
        return [f"{pick.ref} cannot be taken more than once"]
    if not repeatable and any(other.ref == pick.ref for other in picked_so_far):
        return [f"{pick.ref} cannot be taken more than once"]
    binding = str(option.parameters.get("invocation_binds") or "")
    if binding and not pick.object_ref:
        return [f"{pick.ref} must be bound to one {binding}"]
    if not binding and pick.object_ref:
        return [f"{pick.ref} takes no bound object"]
    # 重复要在"能不能绑"之前判：两次选同一项、绑同一把戏法是重复，不是
    # "这个对象不合格"——后者会让玩家去找一个其实合格的对象。
    if any(
        other.ref == pick.ref and other.object_ref == pick.object_ref
        for other in [*owned, *picked_so_far]
    ):
        return [f"{pick.ref} is already bound to {pick.object_ref}"]
    if binding:
        eligible = {
            row["value"] for row in bind_options.get(pick.ref, [])
            if row["available"]
        }
        if pick.object_ref not in eligible:
            return [
                f"{pick.object_ref} is not an eligible {binding} for {pick.ref}"
            ]
    return []


def _match_drop(owned: list[Pick], drop: Pick) -> list[Pick]:
    """Picks a replacement may drop, most specific match first.

    ``drop`` carries ``object_ref`` when the client named one (a repeatable
    option needs it: "the Agonizing Blast on chill touch"), and only a ref
    otherwise, which is how a legacy client still selects its first pick.
    """

    exact = [
        pick for pick in owned
        if pick.ref == drop.ref and pick.object_ref == drop.object_ref
    ]
    if exact:
        return exact
    if drop.object_ref:
        return []
    return [pick for pick in owned if pick.ref == drop.ref]


def plan_pool(
    *,
    resolver: Any,
    definition: Any,
    character: Mapping[str, Any] | None,
    target_level: int,
    track_total: int,
    submitted: list[Any] | None,
    replacement: Mapping[str, Any] | None,
    spells: Mapping[str, Mapping],
    spell_effects: Mapping[str, Mapping],
    allow_legacy: bool = False,
) -> PoolPlan:
    """Turn this level's submission into a requirement, a record, or errors.

    ``submitted`` is the raw ``selected_refs`` of the records this batch sent
    for this slot (``None`` when the player sent none).  Nothing is written
    here: the caller applies ``record`` only after the whole preview is clean.
    """

    choice = definition.choice
    if choice is None or not choice.count_track:
        raise InvocationPoolError(
            f"{definition.id} does not declare a track-counted choice"
        )
    options = {
        ref: resolver.catalog.features_by_key[(definition.owner, ref[len("feature:"):])]
        for ref in choice.enabled_options
    }
    owned = owned_picks(character, definition.owner, definition.id, choice.id)
    owned_refs = {pick.ref for pick in owned}
    required_now = max(0, int(track_total) - len(owned))
    errors: list[str] = []

    # 1. candidates, with the reasons each unavailable one carries.
    projected: list[dict[str, Any]] = []
    bind_options: dict[str, list[dict[str, Any]]] = {}
    bound_already = {(pick.ref, pick.object_ref) for pick in owned}
    for ref, option in options.items():
        reasons = _option_reasons(
            option,
            target_level=target_level,
            owned_refs=owned_refs,
        )
        repeatable = bool(option.parameters.get("invocation_repeatable"))
        if not repeatable and ref in owned_refs:
            # 不可重复的选项已经在你身上了：再选一次会在提交时被拒，但面板
            # 先给出来再说"重复"就是让玩家白点一次。
            reasons.append("already_owned")
        binding = str(option.parameters.get("invocation_binds") or "")
        if binding == "known_cantrip":
            candidates = cantrip_candidates(
                character, option.parameters,
                spells=spells, spell_effects=spell_effects,
                owner_class_ref=definition.owner,
            )
            for row in candidates:
                if row["available"] and (ref, row["value"]) in bound_already:
                    # 这一项已经绑过这个戏法了：可重复选项要的是**不同**对象。
                    row["available"] = False
                    row["unavailable_reasons"] = [
                        *row["unavailable_reasons"], "already_bound",
                    ]
            if not [row for row in candidates if row["available"]]:
                reasons.append("no_eligible_cantrip")
            bind_options[ref] = candidates
        elif binding == "origin_feat":
            # Origin feats have no advancement catalogue yet (the builder reads
            # the feat entities directly), so nothing can be granted without
            # inventing a pool.  Declared `guided` until that exists.
            bind_options[ref] = []
        projected.append({
            "value": ref,
            "name": resolver.catalog.label(option.id, option.id),
            "available": not reasons,
            "unavailable_reasons": reasons,
            **({"bind_options": bind_options[ref]} if ref in bind_options else {}),
        })

    # 池子不够 ≠ 玩家漏选。职业表说要 8 个，而当前内容包只凑得出 7 个时，拦下
    # 升级只会让角色永久卡住；正确做法是把差额投影出去（`shortfall`），并且只在
    # "还有得选却没选"时才拦。容量按**可选取次数**算，不按选项行数算。
    #
    # 容量还要算上"同一批里新拿到的依赖项"：只有前置未满足的选项是可以填的
    # （F34 的 Pact of the Blade + Thirsting Blade 同批获得），否则容量会小于
    # 玩家合法能选的数量，把一次本可填满的提交拒掉。
    fillable = _fillable_refs(projected, options, owned_refs)
    capacity = sum(
        _pick_capacity(row, bind_options, selectable=True)
        for row in projected if row["value"] in fillable
    )
    expected_now = min(required_now, capacity)

    # 2. what this batch submitted.
    new_picks: list[Pick] = []
    for raw in submitted or []:
        pick = parse_ref(raw)
        if pick is None:
            errors.append(f"feature choice {definition.id} selected_refs must be refs")
            continue
        new_picks.append(pick)

    # 老卡（本切片之前建的）没有这条池记录，也没有提交：replay 时不能因此
    # 打不开存档。只认"没有记录"这一种，且只允许 replay 路径声明它；正常
    # 升级永远不走这条豁免，否则"漏选"就变成了静默通过。
    #
    # 标记是粘性的：一张老卡补齐之前每一级都会被要求，等于每级都报错、永远
    # 进不去；玩家真正提交一次完整选择后（`legacy` 为假）标记自然消失。
    sticky = any(bool(row.get("legacy")) for row in _matching(
        character, definition.owner, definition.id, choice.id,
    ))
    legacy = not new_picks and (
        sticky or (allow_legacy and not owned and required_now > 0)
    )

    # The slot is projected whatever the submission looks like: a rejected
    # pick must come back with the slot that rejected it, or the panel loses
    # the very field the player has to fix.  `replace` is attached here (not
    # after validation) for the same reason -- a rejected replacement is
    # exactly the case where the panel must still show the swap form.
    requirement = {
        "id": _requirement_id(definition.owner, definition.id, choice.id),
        "kind": "feature_choice",
        "enforced": True,
        # 老卡只是"不强制"，不是"看不见"：槽位照样投影出该补的数量，
        # 否则玩家连补的入口都没有，legacy 就变成了永久 0 个祈唤。
        "required": bool(expected_now) and not legacy,
        "owner": definition.owner,
        "feature_id": definition.id,
        "choice_id": choice.id,
        "count": expected_now,
        "total": int(track_total),
        "owned": len(owned),
        "shortfall": required_now - expected_now,
        "options": projected,
        **({"legacy": True} if legacy else {}),
    }
    if owned and int(choice.replace_per_level or 0):
        requirement["replace"] = {
            "count": int(choice.replace_per_level),
            "drop_options": [pick.as_ref() for pick in owned],
            "add_options": [
                row["value"] for row in projected if row["available"]
            ],
        }
    if legacy:
        # 把记录种下来并打上标记：后续等级的 replay 看到它就不再要求补齐，
        # 玩家真正提交一次完整选择时标记自然消失。
        return PoolPlan(requirement=requirement, record={
            "owner": definition.owner,
            "feature_id": definition.id,
            "choice_id": choice.id,
            "selected_refs": [pick.as_ref() for pick in owned],
            "granted_level": int(target_level),
            "legacy": True,
        })

    if expected_now and not new_picks:
        errors.append(
            f"feature choice {definition.id} must select exactly {expected_now} "
            "option(s)"
        )
    elif new_picks and len(new_picks) != expected_now:
        errors.append(
            f"feature choice {definition.id} must select exactly {expected_now} "
            "option(s)"
        )
    reasons_by_ref = {row["value"]: row["unavailable_reasons"] for row in projected}
    batch_refs = {item.ref for item in new_picks}
    for index, pick in enumerate(new_picks):
        errors.extend(_pick_errors(
            pick,
            definition_id=definition.id,
            options=options,
            reasons_by_ref=reasons_by_ref,
            owned=owned,
            picked_so_far=new_picks[:index],
            batch_refs=batch_refs,
            bind_options=bind_options,
            target_level=target_level,
        ))
    if errors:
        return PoolPlan(requirement=requirement, errors=tuple(errors))

    # 3. replacement: at most `replace_per_level` swaps, closure-checked.
    kept = list(owned)
    dropped: Pick | None = None
    added: Pick | None = None
    if replacement is not None:
        drop_pick = parse_ref(replacement.get("drop"))
        add_pick = parse_ref(replacement.get("add"))
        if not int(choice.replace_per_level or 0):
            errors.append(f"{definition.id} cannot be replaced")
        elif drop_pick is None or add_pick is None:
            errors.append("a replacement needs both a drop and an add")
        else:
            match = _match_drop(owned, drop_pick)
            if not match:
                errors.append(
                    f"{drop_pick.ref} is not one of your {definition.id} choices"
                )
            else:
                dropped = match[0]
                kept = [pick for pick in owned if pick is not dropped]
                added = add_pick
                if (add_pick.ref, add_pick.object_ref) == (
                    dropped.ref, dropped.object_ref,
                ):
                    # 同一个对象换成同一个对象没有意义；但"同一选项换一个
                    # 绑定对象"是唯一能改绑的入口，必须允许。
                    errors.append("a replacement must change the option")
        if errors:
            return PoolPlan(requirement=requirement, errors=tuple(errors))

    current = kept + new_picks
    if added is not None and dropped is not None:
        errors.extend(_pick_errors(
            added,
            definition_id=definition.id,
            options=options,
            reasons_by_ref=reasons_by_ref,
            owned=[],
            picked_so_far=current,
            batch_refs={item.ref for item in current},
            bind_options=bind_options,
            target_level=target_level,
        ))
        if errors:
            return PoolPlan(requirement=requirement, errors=tuple(errors))
        current = current + [added]

    # 4. closure: a kept option may not lose something it depends on.
    kept_refs = {pick.ref for pick in current}
    for pick in current:
        option = options.get(pick.ref)
        if option is None:
            continue
        for required in option.parameters.get("invocation_requires_invocations") or ():
            if str(required) not in kept_refs:
                errors.append(
                    f"{pick.ref} requires {required}, which this change removes"
                )
    if errors:
        return PoolPlan(requirement=requirement, errors=tuple(errors))

    # 没有记录时也要种一条（哪怕是空的）：它就是"这张卡进入池子时代"的标记，
    # 下一次升级据此要求补齐，而不是又被当成老卡放过。
    changed = bool(new_picks) or dropped is not None or not owned
    record = None
    if changed:
        record = {
            "owner": definition.owner,
            "feature_id": definition.id,
            "choice_id": choice.id,
            "selected_refs": [pick.as_ref() for pick in current],
            "granted_level": int(target_level),
        }
    return PoolPlan(requirement=requirement, record=record)


def plan_pools(
    *,
    resolver: Any,
    character: Mapping[str, Any],
    target_level: int,
    tracks: Mapping[str, Any],
    choices: Mapping[str, Any],
    spells: Mapping[str, Mapping],
    spell_effects: Mapping[str, Mapping],
    allow_legacy: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Every slot this level owes for the pools the character already owns.

    One call for all pools: a class may declare more than one later (a second
    track, a subclass pool), and a caller that looped itself would have to
    repeat the submission grouping per pool.
    """

    raw_choices = choices.get("feature_choices")
    raw_choices = raw_choices if isinstance(raw_choices, list) else []
    submitted: dict[tuple[str, str, str], list[Any]] = {}
    for row in raw_choices:
        if not isinstance(row, dict):
            continue
        refs = row.get("selected_refs")
        if not isinstance(refs, list):
            continue
        key = (
            str(row.get("owner") or ""),
            str(row.get("feature_id") or ""),
            str(row.get("choice_id") or ""),
        )
        submitted.setdefault(key, []).extend(refs)
    replacement = choices.get("feature_choice_replace")
    replacement = replacement if isinstance(replacement, dict) else None

    requirements: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for definition in resolver.features_for(character):
        choice = definition.choice
        if choice is None or not choice.count_track:
            continue
        plan = plan_pool(
            resolver=resolver,
            definition=definition,
            character=character,
            target_level=target_level,
            track_total=int(tracks.get(choice.count_track, 0) or 0),
            submitted=submitted.get((definition.owner, definition.id, choice.id)),
            replacement=replacement,
            spells=spells,
            spell_effects=spell_effects,
            allow_legacy=allow_legacy,
        )
        errors.extend(plan.errors)
        if plan.requirement is not None:
            requirements.append(plan.requirement)
        if plan.record is not None:
            records.append(plan.record)
    return requirements, records, errors


def plan_creation_pool(
    *,
    resolver: Any,
    definition: Any,
    cantrips: list[str],
    submitted: list[Any] | None,
    level: int,
    track_total: int,
    spells: Mapping[str, Mapping],
    spell_effects: Mapping[str, Mapping],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate the picks a new character makes at creation, if any.

    A level-1 build may already own one invocation.  An unanswered slot is
    tolerated here for the same reason Expertise is (the draft predates the
    field, and the level-up path tops the character up to the track), but a
    *submitted* record is validated with the same rules the level-up path uses
    -- a draft is an untrusted submission and must not accept a pick the
    character could not have made.
    """

    if not submitted:
        return None, []
    character: dict[str, Any] = {
        "build": {"level": level, "class_levels": []},
        "features": {},
        "spellcasting": {"class": {"cantrip_refs": list(cantrips)}},
    }
    plan = plan_pool(
        resolver=resolver,
        definition=definition,
        character=character,
        target_level=level,
        track_total=track_total,
        submitted=list(submitted),
        replacement=None,
        spells=spells,
        spell_effects=spell_effects,
    )
    return plan.record, list(plan.errors)


def write_record(features: dict[str, Any], record: Mapping[str, Any]) -> None:
    """Replace a pool's record in place, appending when there is none yet.

    A pool keeps one record whose ``selected_refs`` is the current set:
    replacement has to be able to *remove* an option, which a second appended
    record cannot express.  What each level actually submitted is still in
    ``progression.history``.
    """

    rows = features.get(CHOICE_RECORD_KEY)
    if not isinstance(rows, list):
        rows = []
        features[CHOICE_RECORD_KEY] = rows
    key = (record["owner"], record["feature_id"], record["choice_id"])

    def matches(row: Any) -> bool:
        if not isinstance(row, Mapping):
            return False
        return (
            str(row.get("owner") or ""),
            str(row.get("feature_id") or ""),
            str(row.get("choice_id") or ""),
        ) == key

    # 一次写只留一条：同一 key 有两条时只替换第一条，旧记录会继续参与
    # ``owned_picks``，看起来像"选了两遍"。
    kept = [row for row in rows if not matches(row)]
    kept.append(deepcopy(dict(record)))
    rows[:] = kept


__all__ = [
    "CHOICE_RECORD_KEY",
    "InvocationPoolError",
    "Pick",
    "PoolPlan",
    "REQUIREMENT_PREFIX",
    "bound_objects",
    "cantrip_candidates",
    "owned_picks",
    "owns_binding",
    "parse_ref",
    "plan_creation_pool",
    "plan_pool",
    "plan_pools",
    "track_value",
    "write_record",
]
