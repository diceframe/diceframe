"""One panel shape for characters and stat blocks, and how forms overlay it.

A "panel" is the bundle of numbers a creature is played from: ability scores, AC,
speeds, senses, size, saving throws, skills, attacks, HP, hit dice, languages and
creature type.  A character's derivation produces one, an SRD stat block produces
one, and a shape-shifting effect is nothing more than *a second panel plus a rule
for which slot wins*.

Why a policy table instead of a big branch: Wild Shape and Polymorph differ in
**which slots** are replaced, not in how the overlay itself works.  Wild Shape
keeps your HP, your mental scores and your class features; Polymorph replaces
everything.  Encoding that difference as data means the engine has exactly one
composition path, and adding Shapechange later is a third policy table rather
than a third code path.

The four policies are enough for every shape-shifting effect in the SRD:

* ``keep``    -- the character's value survives (HP, INT/WIS/CHA under Wild Shape)
* ``replace`` -- the form's value wins (physical scores, speeds, attacks)
* ``max``     -- the better of the two wins ("if the beast's modifier is higher,
  use it") for saving throws and skills
* ``union``   -- both sides' entries survive (not used yet; reserved for
  languages when a form grants one you lack)

Composition order matters: the panel is composed first, then equipment and
conditions apply on top.  Doing it the other way round would drop a buff that
was already running when the creature changed shape, which SRD p187 forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace as dc_replace
from typing import Any

POLICIES = ("keep", "replace", "max", "union")

#: Slots every panel carries.  A missing slot on either side is simply absent
#: from the merged panel -- this module never invents a default.
SLOTS = (
    "abilities", "armor_class", "speeds", "senses", "size", "creature_type",
    "saving_throws", "skills", "attacks", "hp", "hit_dice", "languages",
)

#: Wild Shape (SRD 5.2.1 p43): 保留 HP / HD / 心智三项 / 生物类型 / 语言 /
#: 专长与职业特性；替换物理三项、速度、感官、体型与攻击；护甲、豁免、技能取高。
WILD_SHAPE_POLICY: dict[str, str] = {
    "hp": "keep",
    "hit_dice": "keep",
    "creature_type": "keep",
    "languages": "keep",
    "abilities.str": "replace",
    "abilities.dex": "replace",
    "abilities.con": "replace",
    "abilities.int": "keep",
    "abilities.wis": "keep",
    "abilities.cha": "keep",
    "armor_class": "max",
    "speeds": "replace",
    "senses": "replace",
    "size": "replace",
    "saving_throws": "max",
    "skills": "max",
    "attacks": "replace",
}

#: Polymorph (SRD 5.2.1 p153): 游戏统计**全部**换成形态的，只保留性格与阵营，
#: 且不能施法、不能说话。所以除类型外一律 replace。
POLYMORPH_POLICY: dict[str, str] = {
    "hp": "replace",
    "hit_dice": "replace",
    "creature_type": "replace",
    "languages": "replace",
    "abilities.str": "replace",
    "abilities.dex": "replace",
    "abilities.con": "replace",
    "abilities.int": "replace",
    "abilities.wis": "replace",
    "abilities.cha": "replace",
    "armor_class": "replace",
    "speeds": "replace",
    "senses": "replace",
    "size": "replace",
    "saving_throws": "replace",
    "skills": "replace",
    "attacks": "replace",
}


class FormPanelError(ValueError):
    """Raised when a panel or a policy cannot be applied."""


@dataclass(frozen=True)
class StatPanel:
    """The numbers a creature is played from, from any source."""

    abilities: dict[str, int] = field(default_factory=dict)
    armor_class: int = 0
    speeds: dict[str, Any] = field(default_factory=dict)
    senses: dict[str, int] = field(default_factory=dict)
    size: str = ""
    creature_type: str = ""
    saving_throws: dict[str, int] = field(default_factory=dict)
    skills: dict[str, int] = field(default_factory=dict)
    attacks: list[dict[str, Any]] = field(default_factory=list)
    hp: int = 0
    hit_dice: str = ""
    languages: list[str] = field(default_factory=list)
    #: 每个槽最终来自哪一侧：``"character"`` / ``"form"`` / ``"equal"``。
    sources: dict[str, str] = field(default_factory=dict)

    def slot(self, name: str) -> Any:
        if name.startswith("abilities."):
            return self.abilities.get(name.split(".", 1)[1])
        if name not in SLOTS:
            raise FormPanelError(f"unknown panel slot: {name}")
        return getattr(self, name)


def _merge_scalar(policy: str, base: Any, extra: Any) -> tuple[Any, str]:
    if policy == "keep":
        return base, "character"
    if policy == "replace":
        return extra, "form"
    if policy == "max":
        if base is None or extra is None:
            return (extra if base is None else base), ("form" if base is None else "character")
        if int(extra) > int(base):
            return extra, "form"
        return base, ("character" if int(base) > int(extra) else "equal")
    raise FormPanelError(f"{policy} needs a collection slot")


def _merge_mapping(policy: str, base: dict, extra: dict) -> tuple[dict, str]:
    if policy == "keep":
        return dict(base), "character"
    if policy == "replace":
        return dict(extra), "form"
    if policy == "max":
        merged = dict(base)
        source = "character"
        for key, value in extra.items():
            if key not in merged or int(value) > int(merged[key]):
                merged[key] = value
                source = "form" if source == "character" else source
        return merged, source
    if policy == "union":
        merged = dict(base)
        for key, value in extra.items():
            merged.setdefault(key, value)
        return merged, "character"
    raise FormPanelError(f"unsupported policy for a mapping slot: {policy}")


def compose(
    character: StatPanel, form: StatPanel, policy: dict[str, str] | None = None,
) -> StatPanel:
    """Overlay ``form`` onto ``character`` slot by slot.

    A slot with no policy in the table falls back to ``keep``: silently adopting
    the form's value for a slot nobody thought about is how a shape-shift ends up
    quietly granting something it should not.
    """

    table = dict(policy or {})
    unknown = sorted(set(table) - set(SLOTS) - {
        name for name in table if name.startswith("abilities.")
    })
    if unknown:
        raise FormPanelError(f"unknown slot in policy: {unknown[0]}")
    invalid = sorted({value for value in table.values() if value not in POLICIES})
    if invalid:
        raise FormPanelError(f"unknown policy: {invalid[0]}")

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}

    merged_abilities = dict(character.abilities)
    for ability in ("str", "dex", "con", "int", "wis", "cha"):
        slot = f"abilities.{ability}"
        rule = table.get(slot, "keep")
        base = character.abilities.get(ability)
        extra = form.abilities.get(ability)
        if rule in {"keep", "replace"}:
            merged_abilities[ability] = base if rule == "keep" else (
                extra if extra is not None else base
            )
            sources[slot] = "character" if rule == "keep" else "form"
        elif rule == "max":
            if base is None or extra is None:
                merged_abilities[ability] = extra if base is None else base
                sources[slot] = "form" if base is None else "character"
            else:
                merged_abilities[ability] = max(int(base), int(extra))
                sources[slot] = "form" if int(extra) > int(base) else "character"
        else:
            raise FormPanelError(f"{rule} is not valid for a scalar slot")
    values["abilities"] = merged_abilities

    for slot in ("armor_class", "hp"):
        rule = table.get(slot, "keep")
        values[slot], sources[slot] = _merge_scalar(
            rule, getattr(character, slot), getattr(form, slot),
        )

    for slot in ("speeds", "senses", "saving_throws", "skills"):
        rule = table.get(slot, "keep")
        values[slot], sources[slot] = _merge_mapping(
            rule, getattr(character, slot), getattr(form, slot),
        )

    for slot in ("attacks", "languages"):
        rule = table.get(slot, "keep")
        base = list(getattr(character, slot))
        extra = list(getattr(form, slot))
        if rule == "keep":
            values[slot], sources[slot] = base, "character"
        elif rule == "replace":
            values[slot], sources[slot] = (extra if extra else base), "form"
        elif rule == "union":
            values[slot] = base + [item for item in extra if item not in base]
            sources[slot] = "character"
        else:
            raise FormPanelError(f"{rule} is not valid for a list slot")

    for slot in ("size", "creature_type", "hit_dice"):
        rule = table.get(slot, "keep")
        base = getattr(character, slot)
        extra = getattr(form, slot)
        if rule == "keep":
            values[slot], sources[slot] = base, "character"
        elif rule == "replace":
            values[slot], sources[slot] = (extra or base), "form"
        else:
            raise FormPanelError(f"{rule} is not valid for a text slot")

    return StatPanel(sources=sources, **values)


def panel_from_statblock(entry: dict[str, Any]) -> StatPanel:
    """A catalog entry as a panel. Missing fields stay missing, never defaulted."""

    speeds = {
        key: value for key, value in (entry.get("speeds") or {}).items()
        if key != "hover"
    }
    return StatPanel(
        abilities={
            key: int(value) for key, value in (entry.get("abilities") or {}).items()
        },
        armor_class=int(entry.get("armor_class") or 0),
        speeds=speeds,
        senses={
            key: int(value) for key, value in (entry.get("senses") or {}).items()
        },
        size=str(entry.get("size") or ""),
        creature_type=str(entry.get("creature_type") or ""),
        saving_throws={
            key: int(value) for key, value in (entry.get("saving_throws") or {}).items()
        },
        skills={
            key: int(value) for key, value in (entry.get("skills") or {}).items()
        },
        attacks=[dict(attack) for attack in entry.get("attacks") or []],
        hp=int(entry.get("hp") or 0),
        hit_dice=str(entry.get("hit_dice") or ""),
        languages=list(entry.get("languages") or []),
    )


def panel_from_view(view: dict[str, Any]) -> StatPanel:
    """A derived character (or companion) view as a panel.

    The view is already the merged product of sheet, equipment and conditions, so
    it is the correct *character* side of an overlay; nothing here re-derives.
    """

    return StatPanel(
        abilities={
            key: int(value) for key, value in (view.get("abilities") or {}).items()
        },
        armor_class=int(view.get("armor_class") or 0),
        speeds={"walk": int(view.get("speed") or 0)},
        senses=dict(view.get("senses") or {}),
        size=str(view.get("size") or ""),
        creature_type=str(view.get("creature_type") or ""),
        saving_throws={
            key: int(value) for key, value in (view.get("saving_throws") or {}).items()
        },
        skills={
            key: int(value) for key, value in (view.get("skill_values") or {}).items()
        },
        attacks=[dict(attack) for attack in view.get("attacks") or []],
        hp=int(view.get("max_hp") or 0),
        hit_dice=str(view.get("hit_dice") or ""),
        languages=list(view.get("languages") or []),
    )


__all__ = [
    "POLICIES",
    "POLYMORPH_POLICY",
    "SLOTS",
    "WILD_SHAPE_POLICY",
    "FormPanelError",
    "StatPanel",
    "compose",
    "panel_from_statblock",
    "panel_from_view",
]
