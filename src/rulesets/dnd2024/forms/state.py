"""Shape-shifting state: what is active, and what it does to the panel.

This is the seam between "a rule exists" and "the engine uses it".  An effect
declares *which* form and *which* policy; this module holds that declaration and
answers the two questions the rest of the engine actually asks:

* what panel do I play from right now (``active_panel``)?
* may I cast a spell / use my equipment (``restrictions``)?

Keeping these two answers here means the combat code never branches on "am I
shaped" — it asks the state, and the state is a plain value that survives being
written to a save.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .panel import (
    POLYMORPH_POLICY,
    WILD_SHAPE_POLICY,
    StatPanel,
    compose,
    cr_value,
    panel_from_statblock,
)

#: Effect id -> policy table.  A new shape-shift adds a row here, not a branch.
EFFECT_POLICIES: dict[str, dict[str, str]] = {
    "wild_shape": WILD_SHAPE_POLICY,
    "polymorph": POLYMORPH_POLICY,
}

#: 哪些效果禁止施法。Wild Shape 在 18 级 Beast Spells 之后不在此列，由
#: ``beast_spells`` 字段单独放宽，而不是把整个效果从表里删掉。
NO_SPELLCASTING = frozenset({"wild_shape", "polymorph"})


class FormStateError(ValueError):
    """Raised when a form cannot be assumed or reverted."""


@dataclass(frozen=True)
class FormState:
    """One active shape-shift, as stored on the character."""

    effect_id: str
    form_ref: str
    form_name: str = ""
    duration_hours: int = 0
    assumed_at_turn: int = 0
    assumed_at_minute: int = 0
    beast_spells: bool = False
    #: 逐 item 的携带方式。旧存档只有单个字符串，读档时归一成"全部同一种"。
    equipment_modes: dict[str, str] = field(default_factory=dict)
    #: 形态面板的来源快照不存——它由 ``form_ref`` 重新读出来，避免存档里
    #: 出现第二份可能过期的数值。
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def policy(self) -> dict[str, str]:
        try:
            return EFFECT_POLICIES[self.effect_id]
        except KeyError as exc:
            raise FormStateError(f"unknown shape-shift effect: {self.effect_id}") from exc

    def as_payload(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "form_ref": self.form_ref,
            "form_name": self.form_name,
            "duration_hours": self.duration_hours,
            "assumed_at_turn": self.assumed_at_turn,
            "assumed_at_minute": self.assumed_at_minute,
            "beast_spells": self.beast_spells,
            "equipment_modes": dict(self.equipment_modes),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FormState":
        effect_id = str(payload.get("effect_id") or "")
        form_ref = str(payload.get("form_ref") or "")
        if effect_id not in EFFECT_POLICIES:
            raise FormStateError(f"unknown shape-shift effect: {effect_id!r}")
        if not form_ref:
            raise FormStateError("shape-shift state has no form_ref")
        modes = payload.get("equipment_modes")
        if not isinstance(modes, dict):
            # 兼容旧形状：一个字符串代表"所有装备都这么处理"。
            legacy = str(payload.get("equipment_mode") or "merge")
            modes = {"*": legacy}
        normalized: dict[str, str] = {}
        for item_ref, mode in modes.items():
            text = str(mode)
            if text not in {"drop", "merge", "wear"}:
                raise FormStateError(f"unknown equipment mode: {text!r}")
            normalized[str(item_ref)] = text
        try:
            duration_hours = payload.get("duration_hours") or 0
            assumed_at_turn = payload.get("assumed_at_turn") or 0
            assumed_at_minute = payload.get("assumed_at_minute") or 0
            if any(
                isinstance(value, bool)
                for value in (duration_hours, assumed_at_turn, assumed_at_minute)
            ):
                raise ValueError
            duration_hours = int(duration_hours)
            assumed_at_turn = int(assumed_at_turn)
            assumed_at_minute = int(assumed_at_minute)
        except (TypeError, ValueError, OverflowError) as exc:
            raise FormStateError("shape-shift turn metadata is invalid") from exc
        if duration_hours < 0 or assumed_at_turn < 0 or assumed_at_minute < 0:
            raise FormStateError("shape-shift turn metadata is invalid")
        return cls(
            effect_id=effect_id,
            form_ref=form_ref,
            form_name=str(payload.get("form_name") or ""),
            duration_hours=duration_hours,
            assumed_at_turn=assumed_at_turn,
            assumed_at_minute=assumed_at_minute,
            beast_spells=bool(payload.get("beast_spells")),
            equipment_modes=normalized,
        )


def assume_form(
    character_panel: StatPanel, entry: dict[str, Any], state: FormState,
) -> StatPanel:
    """The panel the creature plays from while this state is active."""

    form_panel = panel_from_statblock(entry)
    merged = compose(character_panel, form_panel, state.policy)
    return StatPanel(**{**merged.__dict__, "sources": dict(merged.sources)})


def mode_for(state: FormState, item_ref: str) -> str:
    """One item's mode: its own entry first, then the legacy wildcard."""

    return state.equipment_modes.get(item_ref) or state.equipment_modes.get("*") or "merge"


def merged_refs_for(state: FormState, item_refs: Iterable[str]) -> list[str]:
    """Which of *these* items stop working while shaped.

    "Merged" means the item has no effect — not that the UI hides it.  The
    wildcard mode has to be expanded against the real equipment list here;
    dropping it would make the AC recompute and the activation gate disagree
    about whether the default mode merges anything at all.
    """

    return [str(ref) for ref in item_refs if mode_for(state, str(ref)) == "merge"]


def restrictions(state: FormState) -> dict[str, bool]:
    """What the shape costs you while it lasts.

    ``spellcasting`` is the only hard one in the SRD; equipment follows the
    declared mode (merged equipment has no effect, it is not merely hidden).
    """

    allow_spells = state.beast_spells or state.effect_id not in NO_SPELLCASTING
    return {
        "spellcasting": allow_spells,
        "equipment_effects": any(
            mode != "merge" for mode in (state.equipment_modes or {"*": "merge"}).values()
        ),
        "equipment_visible": any(
            mode != "merge" for mode in (state.equipment_modes or {"*": "merge"}).values()
        ),
    }


def wild_shape_state(
    form_entry: dict[str, Any], *, druid_level: int, beast_spells: bool,
    equipment_modes: dict[str, str] | None = None, assumed_at_turn: int = 0,
    assumed_at_minute: int = 0,
) -> FormState:
    """Build the state a Wild Shape use produces.

    Duration is half the druid level in hours (SRD p42); the caller passes the
    level rather than this module looking it up, so the level question stays with
    the class that owns it.
    """

    creature_type = str(form_entry.get("creature_type") or "")
    if creature_type != "beast":
        raise FormStateError("Wild Shape needs a beast stat block")
    return FormState(
        effect_id="wild_shape",
        form_ref=str(form_entry.get("id") or ""),
        form_name=str(form_entry.get("name") or ""),
        duration_hours=max(1, int(druid_level) // 2),
        assumed_at_turn=int(assumed_at_turn),
        assumed_at_minute=int(assumed_at_minute),
        beast_spells=bool(beast_spells),
        equipment_modes=dict(equipment_modes or {"*": "merge"}),
    )


__all__ = [
    "EFFECT_POLICIES",
    "NO_SPELLCASTING",
    "FormState",
    "FormStateError",
    "KNOWN_FORM_COUNT",
    "RECOMMENDED_FORMS",
    "assume_form",
    "bootstrap_known_forms",
    "known_form_count",
    "learn_form",
    "merged_refs_for",
    "mode_for",
    "restrictions",
    "wild_shape_state",
]


#: SRD p42 推荐的起始四形态；目录里没有该 id 时跳过，不虚构数据。
RECOMMENDED_FORMS = ("rat", "riding-horse", "spider", "wolf")

#: 德鲁伊等级 -> 已知形态数量（SRD p42 的 Beast Shapes 表）。
KNOWN_FORM_COUNT = ((4, 4), (8, 6), (99, 8))


def known_form_count(druid_level: int) -> int:
    """Levels 1 has no Wild Shape at all, so it knows no forms."""

    if druid_level < 2:
        return 0
    for ceiling, count in KNOWN_FORM_COUNT:
        if druid_level < ceiling:
            return count
    return 8


def bootstrap_known_forms(catalog_entries: list[dict[str, Any]], druid_level: int) -> list[str]:
    """首次使用时的已知形态。

    没有建卡期选择流程之前，**默认取 SRD 推荐的那四个**（同样是任务书 §4.1.6
    要求目录必须覆盖的形态），并按当前等级的 CR/Fly 门槛过滤。这不是"猜"：
    推荐列表是官方给的，等级门槛是规则给的，两者都不需要新增数据。
    """

    ceiling = "1/4" if druid_level < 4 else "1/2" if druid_level < 8 else "1"
    wanted = known_form_count(druid_level)
    if wanted < 1:
        return []
    by_id = {str(row.get("id") or ""): row for row in catalog_entries}

    def eligible(row: dict[str, Any] | None) -> bool:
        if row is None:
            return False
        if str(row.get("creature_type") or "") != "beast":
            return False
        if cr_value(str(row.get("cr") or "0")) > cr_value(ceiling):
            return False
        if druid_level < 8 and int((row.get("speeds") or {}).get("fly", 0) or 0) > 0:
            return False
        return True

    allowed = [ref for ref in RECOMMENDED_FORMS if eligible(by_id.get(ref))]
    if len(allowed) < wanted:
        # 推荐列表只有四个；4 级要六个、8 级要八个，缺的从目录里按名字补齐。
        # 不补齐的话，高等级"没提交选择"的角色会永远少形态。
        for row in sorted(by_id.values(), key=lambda item: str(item.get("id") or "")):
            ref = str(row.get("id") or "")
            if ref in allowed or not eligible(row):
                continue
            allowed.append(ref)
            if len(allowed) == wanted:
                break
    return allowed[:wanted]


def learn_form(known: list[str], form_ref: str, *, druid_level: int) -> list[str]:
    """把形态加入已知列表（长休替换的运行时入口）。

    已满且要加新形态时**必须同时给出被替换的那个**，否则拒绝——一次长休只能
    换一个形态，静默挤掉别人会让玩家在下一场战斗里发现少了形态。
    """

    if form_ref in known:
        return list(known)
    if len(known) >= known_form_count(druid_level):
        raise FormStateError("known forms are full; replace one instead")
    return [*known, form_ref]
