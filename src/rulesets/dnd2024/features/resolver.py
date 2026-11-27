"""The D&D 2024 class feature boundary.

One module answers "what does this character actually have": its class, its
class level, the class features it owns, their simple scalar parameters, its
class resource maxima, and the combat capabilities it may declare right now.

It never mutates state, never resolves dice, and never touches CombatState.
Callers (combat, view, projection, frontend payloads) ask this boundary instead
of each re-parsing the class level for themselves.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from src.rulesets.bundle import LoadedRulesetBundle
from .grants import check_modifiers, fixed_sources
from src.rulesets.dnd2024.character.primitives import ABILITY_IDS
from src.rulesets.dnd2024.progression.catalog import (
    Dnd2024ProgressionCatalog,
    ProgressionCatalogError,
)

from .combat import (
    CombatContext,
    combat_capabilities as _project_capabilities,
    martial_arts_profile as _martial_arts_profile,
)
from .equipment import Dnd2024EquipmentCatalog
from .models import (
    ClassFeatureDefinition,
    ClassFeatureError,
    CombatCapabilityView,
    Dnd2024ClassFeatureCatalog,
    FeatureCapability,
    FeatureView,
)
from .resources import (
    FEATURE_USAGE_SCOPES,
    ClassResourceDeclaration,
    Dnd2024ClassResourceCatalog,
    ResourceDefinition,
    long_effects,
    resource_state,
)
from .validation import (
    ENEMY_REDUCED_TO_ZERO_HP as ENEMY_REDUCED_TO_ZERO_HP,
    validate_declarations,
)


def _humanize(feature_id: str) -> str:
    return str(feature_id).replace("_", " ").strip().title()


def _grant_row(value: Any) -> tuple[int, str] | None:
    """Parse one ``"<level>:spell:<id>"`` always-prepared table row.

    The table lives in the declaration rather than in code, and the encoding is
    flat because a feature parameter may only hold scalars.  A row that does not
    parse is a catalog defect, not a grant: it is dropped so the loader's
    scalar check stays the only thing that can reject a declaration.
    """

    parts = str(value or "").split(":")
    if len(parts) != 3 or parts[1] != "spell" or not parts[2]:
        return None
    try:
        level = int(parts[0])
    except ValueError:
        return None
    if level < 1:
        return None
    return level, f"spell:{parts[2]}"


def _terrain_grant_row(value: Any) -> tuple[str, int, str] | None:
    """Parse one ``"<terrain>:<level>:spell:<id>"`` land table row."""

    parts = str(value or "").split(":")
    if len(parts) != 4 or not parts[0] or parts[2] != "spell" or not parts[3]:
        return None
    try:
        level = int(parts[1])
    except ValueError:
        return None
    if level < 1:
        return None
    return parts[0], level, f"spell:{parts[3]}"


# Free-cast slot encoding.  A feature parameter may only hold scalars, so the
# six facts that define one slot travel as a single flat row rather than as a
# nested object: ``"<slot_id>:<level>:<recharge>:<uses>:<spell_ref>"``.
#
# ``unlimited`` is a real recharge value, not "no recharge": Spell Mastery never
# runs out, and a record that stored a finite count for it would be a lie.
_FREE_CAST_RECHARGES = frozenset({"long_rest", "short_rest", "unlimited"})
# A choice slot writes ``-`` where a fixed slot writes its spell, so the row
# keeps five fields either way.
_FREE_CAST_CHOICE = "-"
# Where a choice slot may pick from.  ``class_list`` reads the declared lists,
# ``spellbook`` reads the character's own spellbook, ``fixed`` needs no pick.
FREE_CAST_SOURCES = frozenset({"fixed", "class_list", "spellbook"})


def _free_cast_slot(value: Any) -> dict[str, Any] | None:
    """Parse one free-cast slot row.

    ``uses`` of ``0`` means the slot never runs out, and a ``spell_ref`` of
    ``-`` means the player chooses the spell (the alternative is a feature that
    names its own spell, like Contact Patron).  The last field is split off with
    ``maxsplit`` because a spell ref itself contains a colon.
    """

    parts = str(value or "").split(":", 4)
    if len(parts) != 5:
        return None
    slot_id, raw_level, recharge, raw_uses, raw_spell = parts
    if not slot_id or recharge not in _FREE_CAST_RECHARGES:
        return None
    try:
        spell_level = int(raw_level)
        uses = int(raw_uses)
    except ValueError:
        return None
    if spell_level < 1 or uses < 0:
        return None
    spell_ref = str(raw_spell or "")
    if spell_ref != _FREE_CAST_CHOICE:
        if not spell_ref.startswith("spell:") or len(spell_ref) <= len("spell:"):
            return None
    return {
        "slot_id": slot_id,
        "spell_level": spell_level,
        "recharge": recharge,
        "uses": uses,
        "spell_ref": "" if spell_ref == _FREE_CAST_CHOICE else spell_ref,
    }


class Dnd2024ClassFeatureResolver:
    """Resolve class features, class resources, and combat capabilities."""

    catalog: Dnd2024ClassFeatureCatalog
    resources: Dnd2024ClassResourceCatalog
    progression: Dnd2024ProgressionCatalog | None
    available: bool

    def __init__(self, bundle: LoadedRulesetBundle):
        self.bundle = bundle
        # canonical 装备元数据是惰性构建的：它只在真的有 feature 声明了装备前提
        # 时才被读取，避免每次角色投影都白拷一份物品目录。
        self._equipment: Dnd2024EquipmentCatalog | None = None
        # 成长专长的 kind -> category 映射同样惰性：只有声明了 choice 的特性
        # 才需要它，普通特性投影不该为此读一遍专项目录。
        self._feat_categories: dict[str, str] | None = None
        # 技能目录同样惰性：只有声明了技能选择的特性才需要它。
        self._skill_id_set: set[str] | None = None
        # 与 ``Dnd2024Runtime._sync_class_resources`` 相同的边界：bundle 没有
        # 声明这套可选内容时，职业特性整体降级为「不存在」，而不是让每一个
        # 角色投影都失败。声明存在但格式非法时仍然 fail closed。
        self.available = (
            bundle.get("class_feature_catalog", "srd_class_features") is not None
            and bundle.get("rest_catalog", "srd_recovery") is not None
            and bundle.get("progression_catalog", "srd_classes") is not None
        )
        if not self.available:
            self.catalog = Dnd2024ClassFeatureCatalog.empty()
            self.resources = Dnd2024ClassResourceCatalog.empty()
            self.progression = None
            return
        self.catalog = Dnd2024ClassFeatureCatalog.from_bundle(bundle)
        self.resources = Dnd2024ClassResourceCatalog.from_bundle(bundle)
        self.progression = Dnd2024ProgressionCatalog.from_bundle(bundle)
        validate_declarations(
            self.catalog, self.bundle, self.progression, self.resources,
        )

    @property
    def _feature_index(self) -> Mapping[tuple[str, str], ClassFeatureDefinition]:
        """The catalog's current owner-scoped index.

        Reading it on demand keeps monkeypatched catalogs (tests and
        diagnostics) authoritative instead of pinning a stale snapshot.
        """

        return self.catalog.features_by_key

    @property
    def equipment(self) -> Dnd2024EquipmentCatalog:
        """The canonical item / weapon metadata equipment eligibility is read from."""

        if self._equipment is None:
            self._equipment = Dnd2024EquipmentCatalog.from_bundle(self.bundle)
        return self._equipment

    # ------------------------------------------------------------------
    # identity
    # ------------------------------------------------------------------

    def class_state(self, character: Mapping[str, Any] | None) -> tuple[str, int] | None:
        """Return ``(class_id, level)`` for a single-class character, else ``None``.

        A character that is not a professional D&D character (a companion with a
        partial sheet, a legacy import, an enemy) simply has no class features;
        this boundary degrades to "owns nothing" instead of raising.
        """

        if not isinstance(character, Mapping):
            return None
        build = character.get("build")
        if not isinstance(build, Mapping):
            return None
        class_levels = build.get("class_levels")
        if not isinstance(class_levels, list) or len(class_levels) != 1:
            return None
        row = class_levels[0]
        if not isinstance(row, Mapping):
            return None
        class_ref = str(row.get("class_ref") or "")
        class_id = class_ref.removeprefix("class:")
        if not class_id:
            return None
        level = row.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 20:
            return None
        return class_id, level

    # ------------------------------------------------------------------
    # feature availability
    # ------------------------------------------------------------------

    def features_for(
        self, character: Mapping[str, Any] | None,
    ) -> tuple[ClassFeatureDefinition, ...]:
        """Every declared feature the character owns, in catalog order.

        Ownership is a set of owner-scoped keys.  Class features come from the
        progression table; subclass features are granted only when the
        character's persisted ``features.subclass_ref`` names the subclass the
        bundle associates with that class.
        """

        state = self.class_state(character)
        if state is None:
            return ()
        return self._owned_feature_definitions(*state, character)

    def _owned_feature_definitions(
        self, class_id: str, level: int,
        character: Mapping[str, Any] | None = None,
    ) -> tuple[ClassFeatureDefinition, ...]:
        """The features one class row owns at ``level``, in catalog order.

        ``features_for`` and ``attacks_per_action`` both read ownership through
        this one filter.  Keeping two copies was harmless only while the shipped
        tables happened to agree: a grant made below a feature's
        ``minimum_level``, or an option feature whose parent is not granted,
        read as "not owned" on the character sheet while the attack count still
        took the raw grant set.

        ``character`` is required to resolve which subclass the player selected;
        callers that only have a class row still pass it through so the two
        entry points can never disagree about ownership.
        """

        granted = self._granted_feature_keys(class_id, level, character)
        owned: list[ClassFeatureDefinition] = []
        ordered = list(self.catalog.by_class.get(class_id, ()))
        # A progression table may grant a shared declaration declared under
        # another class (Extra Attack and Weapon Mastery are shared names).
        # Ownership follows the grant, while identity stays owner-scoped.
        for key in sorted(granted):
            if key not in ordered:
                ordered.append(key)
        by_key = self.catalog.features_by_key
        for key in ordered:
            definition = by_key.get(key)
            if definition is None or definition.minimum_level > level:
                continue
            if definition.option_of:
                # 选项特性（例如 Monk's Focus 的三个用法）只有在同一个 owner
                # 下授予它的父特性已经被拥有时才算拥有。
                parent_key = (definition.owner, definition.option_of)
                if parent_key not in granted:
                    continue
                parent = by_key.get(parent_key)
                if (
                    parent is not None
                    and parent.choice is not None
                    and parent.choice.kind == "feature_option"
                    and definition.id not in self._selected_feature_options(
                        character, parent,
                    )
                ):
                    # 互斥选项：父特性拥有不等于两个选项都拥有。Divine Strike
                    # 与 Potent Spellcasting 是替代关系，同时拥有会让一次命中
                    # 既拿 1d8 又拿 Wisdom 加值。
                    continue
            elif key not in granted:
                continue
            owned.append(definition)
        return tuple(owned)

    def has_feature(
        self,
        character: Mapping[str, Any] | None,
        feature_id: str,
        *,
        owner: str | None = None,
    ) -> bool:
        """Whether the character owns ``feature_id``, optionally one declaration.

        ``owner`` narrows the question to a *declaration owner*, never to the
        class that granted it.  It exists for ids declared twice with different
        owners -- ``cutting_words`` belongs to both College of Lore and College
        of Valor -- where "the character has the Bard's Cutting Words" is not
        the same question as "the character has the Lore one".

        A shared declaration keeps its single owner, so the answer for a second
        class is deliberately False on the owner check while still True without
        it: a Monk 5 owns Extra Attack, and that declaration is filed under
        ``class:fighter`` because Extra Attack is declared once, with one
        number, for every class that grants it.  Callers asking "does this
        class grant X" must read the class table, not this method.
        """

        wanted = str(feature_id or "")
        if not wanted:
            return False
        wanted_owner = str(owner) if owner is not None else None
        return any(
            item.id == wanted
            and (wanted_owner is None or item.owner == wanted_owner)
            for item in self.features_for(character)
        )

    def attacks_per_action(self, character: Mapping[str, Any] | None) -> int:
        """How many attacks one Attack action grants, per the class tables.

        Extra Attack is a declared class feature, not a class-name switch: the
        progression table decides *which* class grants which feature id and at
        which level, and the feature catalog declares the number.  RAW never
        stacks the feature, so every class row is evaluated on its own and the
        highest value wins -- a Fighter 5 / Barbarian 5 makes two attacks, not
        three.  Unlike ``class_state`` this reads every class row, because
        multiclass characters are exactly the case where the old
        "first class wins" guess went wrong.
        """

        if not self.available or self.progression is None:
            return 1
        if not isinstance(character, Mapping):
            return 1
        build = character.get("build")
        if not isinstance(build, Mapping):
            return 1
        rows = build.get("class_levels")
        if not isinstance(rows, list):
            return 1
        best = 1
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            class_id = str(row.get("class_ref") or "").removeprefix("class:")
            level = row.get("level")
            if not class_id or isinstance(level, bool) or not isinstance(level, int):
                continue
            if not 1 <= level <= 20:
                continue
            for definition in self._owned_feature_definitions(
                class_id, level, character,
            ):
                value = definition.parameters.get("attacks_per_action")
                if isinstance(value, int) and not isinstance(value, bool):
                    best = max(best, value)
        return best

    def feature_is_active(
        self, character: Mapping[str, Any] | None, definition: ClassFeatureDefinition,
    ) -> bool:
        """Whether one owned feature's declared equipment precondition holds now.

        Ownership (``features_for``) never changes with equipment; only the
        benefits a feature grants do.  Monk's Focus, Flurry of Blows, Patient
        Defense and Step of the Wind declare no equipment requirement, so they
        keep working while Martial Arts is suppressed.
        """

        requirement = definition.equipment_requirement
        if requirement is None:
            return True
        if not self.available:
            return True
        return self.equipment.requirement_met(character, requirement)

    def active_features_for(
        self, character: Mapping[str, Any] | None,
    ) -> tuple[ClassFeatureDefinition, ...]:
        """Owned features whose benefits currently apply, in catalog order."""

        return tuple(
            definition for definition in self.features_for(character)
            if self.feature_is_active(character, definition)
        )

    def feature_value(
        self,
        character: Mapping[str, Any] | None,
        feature_id: str,
        key: str,
        default: Any = None,
        *,
        owner: str | None = None,
    ) -> Any:
        """One scalar/table parameter of an owned feature.

        A parameter named ``<key>_track`` resolves against the class progression
        table, so a level-derived value (the Martial Arts die) is read from the
        same bundle table that advancement uses instead of being copied here.
        An optional ``<key>_format`` template renders the table value into its
        printable form (``1d{die}``); it is a single placeholder, never an
        expression.

        When ``owner`` is supplied, only the declaration with that owner is
        considered; omitting it preserves the historical short-id lookup.
        """

        wanted = str(feature_id)
        wanted_owner = str(owner) if owner is not None else None
        definition = next(
            (
                item for item in self.features_for(character)
                if item.id == wanted
                and (wanted_owner is None or item.owner == wanted_owner)
            ),
            None,
        )
        if definition is None:
            return default
        return self._value(definition, character, key, default)

    def saving_throw_proficiency_all(
        self, character: Mapping[str, Any] | None,
    ) -> bool:
        """Whether an owned feature grants proficiency in every saving throw.

        Disciplined Survivor is the only declaration today.  The contract is
        the declaration itself (``parameters.saving_throw_proficiency ==
        "all"``), so a second feature that grants the same thing needs no new
        code and no character-sheet branch.
        """

        return any(
            str(definition.parameters.get("saving_throw_proficiency") or "") == "all"
            for definition in self.features_for(character)
        )

    def owns_definition(
        self, character: Mapping[str, Any] | None, definition: Any,
    ) -> bool:
        """Whether a feature is in force, not merely owned by level."""

        parent = definition.option_of
        if not parent:
            return True
        chosen = self.feature_option_choice(character, parent, owner=definition.owner)
        return chosen == definition.id

    def language_sources(
        self, character: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        """``{language_ref: feature_id}`` for every feature-granted language."""

        return fixed_sources(
            (
                definition for definition in self.features_for(character)
                if self.owns_definition(character, definition)
            ),
            "language_grants",
        )

    def category_sources(
        self, character: Mapping[str, Any] | None, key: str,
    ) -> dict[str, str]:
        """``{category: feature_id}`` for feature-granted armor/weapon training."""

        return fixed_sources(
            (
                definition for definition in self.features_for(character)
                if self.owns_definition(character, definition)
            ),
            key,
        )

    def check_modifiers(
        self, character: Mapping[str, Any] | None, surface: str,
    ) -> list[dict[str, Any]]:
        """Declared check modifiers for one surface."""

        return check_modifiers(
            (
                definition for definition in self.features_for(character)
                if self.owns_definition(character, definition)
            ),
            surface,
        )

    def saving_throw_proficiency_sources(
        self, character: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        """``{ability: feature_id}`` for every feature-granted saving throw.

        The source travels with the grant because the ref set cannot say who
        put an ``ability:wis`` there: the class table and a feature look
        identical once merged, and an audit that cannot tell them apart cannot
        explain the bonus either.
        """

        sources: dict[str, str] = {}
        for definition in self.features_for(character):
            declared = definition.parameters.get("saving_throw_proficiencies")
            if not isinstance(declared, list):
                continue
            for ability in declared:
                if isinstance(ability, str) and ability and ability not in sources:
                    sources[ability] = definition.id
        return sources

    def ability_bonuses(
        self, character: Mapping[str, Any] | None,
    ) -> dict[str, tuple[int, int]]:
        """Ability score increases from owned features: ``{ability: (bonus, cap)}``.

        Primal Champion and Body and Mind are the declarations today.  A bonus
        is a benefit, so only declarations whose equipment gate is met count;
        the cap travels with the bonus because "to a maximum of 25" is a
        property of the grant, not of the character.
        """

        grants: dict[str, tuple[int, int]] = {}
        for definition in self.active_features_for(character):
            parameters = definition.parameters
            cap = parameters.get("ability_score_cap")
            if not isinstance(cap, int) or isinstance(cap, bool):
                continue
            for ability in ABILITY_IDS:
                bonus = parameters.get(f"ability_bonus_{ability}")
                if not isinstance(bonus, int) or isinstance(bonus, bool):
                    continue
                previous = grants.get(ability)
                # 两道同属性加值取较高的上限：上限是"这几点最多到多少"，
                # 更宽的那条决定了最终值，收紧它只会凭空削掉加值。
                grants[ability] = (
                    (previous[0] if previous else 0) + bonus,
                    max(cap, previous[1]) if previous else cap,
                )
        return grants

    def strength_total_replacement(
        self, character: Mapping[str, Any] | None,
    ) -> bool:
        """Whether a Strength check total below the Strength score is replaced.

        Indomitable Might is the only declaration today.  It is read as a
        declaration rather than by feature id so a second feature that does the
        same thing needs no branch here.
        """

        return any(
            definition.parameters.get("strength_total_replacement") is True
            for definition in self.active_features_for(character)
        )

    # ------------------------------------------------------------------
    # always-prepared spell grants
    # ------------------------------------------------------------------

    def metamagic_known(self, character: Mapping[str, Any] | None) -> dict[str, Any]:
        """The Metamagic options this character chose, as a lookup.

        Parsed from ``features.metamagic.known``; the advancement step writes it
        at levels 2/10/17 and can replace one entry per level gained.  An absent
        or malformed record yields nothing -- the read path must not invent a
        known option, because that would hand out free points.
        """

        features = (character or {}).get("features")
        block = features.get("metamagic") if isinstance(features, Mapping) else None
        raw = block.get("known") if isinstance(block, Mapping) else None
        if not isinstance(raw, list):
            return {}
        return {str(item): {} for item in raw if isinstance(item, str) and item}

    def spell_grant_choices(
        self, character: Mapping[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        """The persisted per-feature spell-grant choices.

        Two declarations need one: Circle of the Land picks a terrain each Long
        Rest, and Magical Discoveries learns two spells at Bard 6.  Both are
        player choices, so they live on the sheet; the *grants* are re-derived
        from them on every read and never stored beside them, which is what
        keeps a downgrade, a rebuild or a replay from carrying a stale terrain.
        A malformed record is ignored here -- the entry point that writes one
        validates it, and the read path must not invent a grant either.
        """

        spellcasting = (character or {}).get("spellcasting")
        if not isinstance(spellcasting, Mapping):
            return {}
        class_magic = spellcasting.get("class")
        if not isinstance(class_magic, Mapping):
            return {}
        raw = class_magic.get("spell_grant_choices")
        if not isinstance(raw, Mapping):
            return {}
        return {
            str(key): dict(value)
            for key, value in raw.items()
            if isinstance(value, Mapping)
        }

    def spell_grant_terrain_feature(
        self, character: Mapping[str, Any] | None,
    ) -> ClassFeatureDefinition | None:
        """The owned declaration that makes the character choose a land terrain."""

        for definition in self.features_for(character):
            if definition.parameters.get("terrain_options"):
                return definition
        return None

    # ------------------------------------------------------------------
    # free-cast slots
    # ------------------------------------------------------------------

    def free_cast_choices(
        self, character: Mapping[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        """The persisted free-cast slot records, keyed by slot id.

        A record holds only what the player owns: the chosen spell and how many
        uses are left.  Level, recharge and the use maximum stay in the
        declaration, so a content fix is never shadowed by stale sheet data.
        """

        spellcasting = (character or {}).get("spellcasting")
        if not isinstance(spellcasting, Mapping):
            return {}
        class_magic = spellcasting.get("class")
        if not isinstance(class_magic, Mapping):
            return {}
        raw = class_magic.get("free_cast_slots")
        if not isinstance(raw, Mapping):
            return {}
        return {
            str(key): dict(value)
            for key, value in raw.items()
            if isinstance(value, Mapping)
        }

    def free_cast_slots(
        self, character: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Every free-cast slot the character owns, merged with its record.

        The identity of a slot is ``(owner, feature_id, slot_id)`` and it is
        never flattened into a single "can cast for free" flag: each row keeps
        its own spell, level, recharge and remaining uses, because Contact
        Patron, the four Mystic Arcana, Spell Mastery and Signature Spells are
        six independent pools with six different recharge rules.

        An absent record means "not spent yet" for a slot whose spell the
        feature itself names, and "not chosen yet" for one the player picks.
        """

        stored = self.free_cast_choices(character)
        return self._free_cast_rows(self.features_for(character), character, stored)

    def _free_cast_rows(
        self,
        definitions: tuple[ClassFeatureDefinition, ...],
        character: Mapping[str, Any] | None,
        stored: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Free-cast rows for an already-resolved ownership list."""

        records = self.free_cast_choices(character) if stored is None else stored
        rows: list[dict[str, Any]] = []
        for definition in definitions:
            parameters = definition.parameters
            raw_slots = parameters.get("free_cast_slots") or ()
            if not raw_slots:
                continue
            source = str(parameters.get("free_cast_source") or "fixed")
            for raw in raw_slots:
                parsed = _free_cast_slot(raw)
                if parsed is None:
                    continue
                rows.append(self._free_cast_row(
                    definition, parsed, records.get(parsed["slot_id"]), source,
                ))
        return rows

    def _free_cast_row(
        self,
        definition: ClassFeatureDefinition,
        parsed: Mapping[str, Any],
        record: Mapping[str, Any] | None,
        source: str,
    ) -> dict[str, Any]:
        parameters = definition.parameters
        uses = int(parsed["uses"])
        fixed = str(parsed["spell_ref"] or "")
        spell_ref = fixed
        uses_remaining: int | None = None if uses == 0 else uses
        valid = True
        if record is not None:
            # A record that disagrees with the declaration about the slot's
            # level or recharge is not a slot any more; ignoring the mismatch
            # would let a tampered sheet cast a level 2 spell out of a level 6
            # arcanum by relabelling it.
            stored_level = record.get("spell_level")
            stored_recharge = record.get("recharge")
            if (
                (stored_level is not None and int(stored_level) != int(parsed["spell_level"]))
                or (stored_recharge is not None and str(stored_recharge) != str(parsed["recharge"]))
            ):
                valid = False
            chosen = record.get("spell_ref")
            if not fixed and isinstance(chosen, str) and chosen:
                spell_ref = chosen
            if uses and isinstance(record.get("uses_remaining"), int):
                uses_remaining = max(0, int(record["uses_remaining"]))
        lists = [str(ref) for ref in parameters.get("free_cast_lists") or ()]
        return {
            "id": f"free_cast:{definition.owner}:{definition.id}:{parsed['slot_id']}",
            "owner": definition.owner,
            "feature_id": definition.id,
            "slot_id": str(parsed["slot_id"]),
            "spell_level": int(parsed["spell_level"]),
            "recharge": str(parsed["recharge"]),
            "uses_max": uses,
            "spell_ref": spell_ref,
            "selected": bool(spell_ref),
            "uses_remaining": uses_remaining,
            "valid": valid,
            "source": "fixed" if fixed else source,
            "spell_lists": lists,
            "casting_time": str(parameters.get("free_cast_casting_time") or ""),
            "save_auto_succeed": bool(parameters.get("free_cast_save_auto")),
            "replacement": int(parameters.get("free_cast_replacement") or 0),
            "replacement_group": str(
                parameters.get("free_cast_replacement_group") or definition.id
            ),
            "rest_replace": int(parameters.get("free_cast_rest_replace") or 0),
        }

    def spell_grant_rows(
        self, character: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Always-prepared spells the character owns, one row per source.

        Provenance is the point of this projection: a row names the ``owner``
        and ``feature_id`` that grants the spell, the level at which the grant
        started, and the terrain or choice slot it came from.  Two sources that
        hand out the same spell stay two rows, so revoking one can never delete
        a spell the other still provides.
        """

        return self._spell_grant_rows(self.features_for(character), character)

    def _spell_grant_rows(
        self,
        definitions: tuple[ClassFeatureDefinition, ...],
        character: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Grant rows for an already-resolved ownership list.

        ``actor_projection`` passes the list it just resolved so the combat
        boundary resolves ownership once; every other caller uses
        :meth:`spell_grant_rows`, which is why both read the same tables.
        """

        state = self.class_state(character)
        if state is None:
            return []
        class_id, level = state
        class_entity = self.bundle.get("class", class_id) or {}
        ability = str(class_entity.get("spellcasting_ability") or "")
        choices = self.spell_grant_choices(character)
        rows: list[dict[str, Any]] = []
        for definition in definitions:
            parameters = definition.parameters
            for raw in parameters.get("always_prepared") or ():
                parsed = _grant_row(raw)
                if parsed is None or parsed[0] > level:
                    continue
                rows.append(
                    self._grant_row(definition, parsed[1], parsed[0], ability)
                )
            terrain = str((choices.get(definition.id) or {}).get("terrain") or "")
            for raw in parameters.get("always_prepared_terrain") or ():
                terrain_row = _terrain_grant_row(raw)
                if (
                    terrain_row is None
                    or terrain_row[1] > level
                    or terrain_row[0] != terrain
                ):
                    continue
                rows.append(self._grant_row(
                    definition, terrain_row[2], terrain_row[1], ability,
                    terrain=terrain,
                ))
            slot = choices.get(definition.id) or {}
            has_choice_slot = bool(
                int(parameters.get("spell_choice_count") or 0)
                or int(parameters.get("spell_choice_per_new_slot_level") or 0)
            )
            if has_choice_slot and (
                str(parameters.get("spell_choice_target") or "prepared")
                != "spellbook"
            ):
                # 写进法术书的授予不是"常备"：它就是书里的一页，准备与否由
                # player 决定，所以不能出现在 always-prepared 投影里。
                granted_level = int(slot.get("granted_level") or definition.minimum_level)
                for ref in slot.get("spells") or ():
                    if not isinstance(ref, str) or not ref:
                        continue
                    rows.append(self._grant_row(
                        definition, ref, granted_level, ability,
                        choice_id=definition.id,
                    ))
            if bool(parameters.get("free_cast_prepares")):
                # Spell Mastery, Signature Spells and Contact Patron all say
                # "you always have them prepared"; the free cast is a separate
                # fact about the same spell, so it comes through as one more
                # source on the prepared view rather than as its own entry.
                for free in self._free_cast_rows(
                    (definition,), character, self.free_cast_choices(character),
                ):
                    if not free["spell_ref"] or not free["valid"]:
                        continue
                    rows.append(self._grant_row(
                        definition, free["spell_ref"],
                        int(definition.minimum_level), ability,
                        choice_id=free["slot_id"],
                    ))
        return rows

    @staticmethod
    def _grant_row(
        definition: ClassFeatureDefinition,
        spell_ref: str,
        granted_level: int,
        ability: str,
        *,
        terrain: str = "",
        choice_id: str = "",
    ) -> dict[str, Any]:
        return {
            "owner": definition.owner,
            "feature_id": definition.id,
            "spell_ref": str(spell_ref),
            "granted_level": int(granted_level),
            "terrain": str(terrain),
            "choice_id": str(choice_id),
            "source": "class_feature",
            "spell_ability": ability,
        }

    def min_feature_value(
        self,
        character: Mapping[str, Any] | None,
        key: str,
        default: Any = None,
    ) -> Any:
        """The smallest numeric value of one parameter across active features.

        Crit threshold and other "best value wins" primitives read through this
        method, so a class never hardcodes a subclass feature id and two owned
        declarations (19 and 18) resolve to the more generous 18 instead of
        stacking.  Owned-but-inactive features contribute no values.
        """

        name = str(key)
        found: int | float | None = None
        for definition in self.active_features_for(character):
            value = definition.parameters.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if found is None or value < found:
                found = value
        return default if found is None else found

    def max_feature_value_source(
        self,
        character: Mapping[str, Any] | None,
        key: str,
        default: int = 0,
    ) -> tuple[int, str]:
        """The largest numeric value of one parameter and the declaration it came from.

        Turn-start regeneration needs both the amount and the declaration that
        supplied it (the paired ability lives in the same definition).  Multiple
        owned declarations resolve to the single largest base instead of
        stacking, mirroring ``min_feature_value`` for the most generous
        threshold.
        """

        name = str(key)
        found: int | None = None
        source_id = ""
        for definition in self.features_for(character):
            value = definition.parameters.get(name)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if found is None or value > found:
                found = value
                source_id = definition.id
        if found is None:
            return int(default), ""
        return found, source_id

    def unarmored_ac_bonus_abilities(
        self, character: Mapping[str, Any] | None,
    ) -> tuple[str, ...]:
        """Ability ids whose modifiers add to an unarmored base AC.

        Each owned declaration names exactly one additional ability. Multiple
        declarations are alternative candidates for the "one base AC at a
        time" rule, so the armor class formula takes the best candidate instead
        of summing ability modifiers.

        Only currently active declarations contribute: an unarmored-AC ability
        is a benefit, and a benefit whose equipment precondition does not hold
        is not in force.  Declaring the ability is still the catalog's job, so
        nothing here names a feature id.
        """

        abilities: list[str] = []
        for definition in self.active_features_for(character):
            ability = definition.parameters.get("unarmored_ac_ability_bonus")
            if isinstance(ability, str) and ability and ability not in abilities:
                abilities.append(ability)
        return tuple(abilities)

    def speed_bonus(self, character: Mapping[str, Any] | None) -> int:
        """The flat Speed increase the currently active features grant.

        Each source contributes additively and exactly once; a level-scaled
        bonus reads the class progression table through the ``_track``
        convention instead of copying the numbers here.  Only active
        declarations count, so Fast Movement drops out under Heavy armor and
        Unarmored Movement drops out under armor or a Shield.  The derived Speed
        total is assembled by ``derive_speed`` -- this method never writes a
        rule number into the character.
        """

        total = 0
        for definition in self.active_features_for(character):
            fixed = definition.parameters.get("speed_bonus_ft")
            if isinstance(fixed, int) and not isinstance(fixed, bool):
                total += fixed
            track = definition.parameters.get("speed_bonus_track")
            if isinstance(track, str) and track:
                scaled = self.feature_value(
                    character, definition.id, "speed_bonus", 0,
                    owner=definition.owner,
                )
                if isinstance(scaled, int) and not isinstance(scaled, bool):
                    total += scaled
        return total

    def movement_modes(self, character: Mapping[str, Any] | None) -> dict[str, bool]:
        """The special Speeds the owned declarations grant.

        Roving's "+10 feet while you aren't wearing Heavy armor" is a Speed
        bonus, and ``speed_bonus`` drops it under Heavy armor.  The Climb and
        Swim Speeds the same feature grants are unconditional: Heavy armor
        removes the +10, not those two Speeds.  That asymmetry is the rule, so
        this reads *owned* declarations while the Speed bonus reads only
        *active* ones.

        A mode is a yes/no fact, never a number: the distance always equals the
        character's own current Speed, which ``derive_speed`` owns.
        """

        climb = swim = False
        for definition in self.features_for(character):
            if definition.parameters.get("climb_speed_from_speed") is True:
                climb = True
            if definition.parameters.get("swim_speed_from_speed") is True:
                swim = True
        return {"climb": climb, "swim": swim}

    def jump_ability(self, character: Mapping[str, Any] | None) -> str:
        """The ability that sets jump distance; default is Strength.

        Second-Story Work permits Dexterity instead of Strength (SRD p64).
        The derived profile reports the largest legal jump, not a forced
        substitution that could reduce the character's normal jump distance.
        """

        abilities = (character or {}).get("abilities") or {}
        best = "str"
        for definition in self.features_for(character):
            ability = definition.parameters.get("jump_ability")
            if isinstance(ability, str) and ability and int(
                abilities.get(ability, 10)
            ) > int(abilities.get(best, 10)):
                best = ability
        return best

    def feature_max_hp_bonus(self, character: Mapping[str, Any] | None) -> int:
        """The Hit Point maximum increase granted by owned features.

        ``max_hp_bonus_first`` applies at the declaring minimum level and
        ``max_hp_bonus_per_level_after`` once per later class level. Ownership
        is already resolved by ``features_for``, so this only counts levels
        after the grant and never retroactively pays for earlier ones.
        """

        state = self.class_state(character)
        if state is None:
            return 0
        _, level = state
        total = 0
        for definition in self.features_for(character):
            first = definition.parameters.get("max_hp_bonus_first", 0)
            per_level = definition.parameters.get("max_hp_bonus_per_level_after", 0)
            if isinstance(first, bool) or not isinstance(first, int):
                continue
            if isinstance(per_level, bool) or not isinstance(per_level, int):
                continue
            total += first + per_level * max(0, level - definition.minimum_level)
        return total

    def feature_flag_sources(
        self,
        character: Mapping[str, Any] | None,
        key: str,
    ) -> list[str]:
        """Owned declaration ids whose boolean parameter ``key`` is truthy.

        Advantage provenance is built straight from this list: the resolver
        never names a feature, it only reports which declarations asked for the
        flag.
        """

        name = str(key)
        return [
            definition.id
            for definition in self.features_for(character)
            if definition.parameters.get(name)
        ]

    def feature_flag_source_rows(
        self,
        character: Mapping[str, Any] | None,
        key: str,
    ) -> list[dict[str, str]]:
        """Owned declaration identities whose boolean parameter ``key`` is truthy.

        Same order and same ownership as :meth:`feature_flag_sources`, but each
        row carries the ``owner`` as well: provenance that only names a local id
        cannot tell two subclasses' identically-named features apart, and the
        reported source list is the only record a client sees.
        """

        name = str(key)
        return [
            {"code": definition.id, "owner": definition.owner}
            for definition in self.features_for(character)
            if definition.parameters.get(name)
        ]

    def feature_selection(
        self,
        character: Mapping[str, Any] | None,
        definition: ClassFeatureDefinition,
    ) -> dict[str, Any] | None:
        """The committed selection of one declared choice, or ``None``.

        Only a record matching the full ``(owner, feature_id, choice_id)``
        identity and carrying refs of the declared category is projected.  A
        duplicate record for the same slot is ambiguous, so it resolves to an
        empty selection rather than letting the last write win.  Nothing here
        grants a feature: ownership still comes only from the class and
        subclass grant tables, never from a submitted choice.

        A ``skill_proficiency`` slot is projected against the *skill* catalog:
        a ref the bundle does not define, or one left out of the declaration's
        own whitelist, is not part of the selection.  The returned shape stays
        ``{choice_id, selected_refs}`` for both kinds so a client reads one
        contract.
        """

        choice = definition.choice
        if choice is None:
            return None
        matches = [
            record
            for record in self._feature_choice_records(character)
            if record.get("owner") == definition.owner
            and record.get("feature_id") == definition.id
            and record.get("choice_id") == choice.id
        ]
        # 同一个三元组可以合法地出现多次：Bard 的 Expertise 在 2 级与 9 级各选
        # 两项，两级共用一个 (owner, feature_id, choice_id)。区分它们的是
        # ``granted_level``，所以按等级分组：组内重复仍然是"写了两次"的歧义，
        # 不同等级则是先后两次合法选择，合并成一份选择集。
        groups: dict[Any, list[Mapping[str, Any]]] = {}
        for record in matches:
            groups.setdefault(record.get("granted_level"), []).append(record)
        if any(len(group) > 1 for group in groups.values()):
            return {"choice_id": choice.id, "selected_refs": []}
        allowed = set(choice.enabled_options)
        selected: list[str] = []
        for group in groups.values():
            raw_refs = group[0].get("selected_refs")
            if not isinstance(raw_refs, list):
                continue
            for ref in raw_refs:
                if not isinstance(ref, str):
                    continue
                if not self._choice_ref_is_valid(choice, ref, allowed):
                    continue
                if ref not in selected:
                    selected.append(ref)
        return {"choice_id": choice.id, "selected_refs": selected}

    def _choice_ref_is_valid(
        self, choice: Any, ref: str, allowed: set[str],
    ) -> bool:
        """Whether one persisted ref belongs to this declared choice's pool."""

        if choice.kind == "skill_proficiency":
            if not ref.startswith("skill:"):
                return False
            if ref[6:] not in self._skill_ids():
                return False
            return ref in allowed if allowed else True
        categories = self._advancement_feat_categories()
        if categories.get(ref.removeprefix("feat:")) != choice.category:
            return False
        return True

    def _skill_ids(self) -> set[str]:
        """The skill catalog's ids, read once per resolver."""

        if self._skill_id_set is None:
            self._skill_id_set = {
                str(entity.get("id")) for entity in self.bundle.list("skill")
                if isinstance(entity, Mapping)
            }
        return self._skill_id_set

    def expertise_skills(
        self, character: Mapping[str, Any] | None,
    ) -> list[dict[str, str]]:
        """The skills this character holds Expertise in, with their sources.

        Expertise is a property of the character, not of one class: a Bard /
        Rogue multiclass has one pool and the SRD forbids the same skill twice
        ("You can't have Expertise in the same skill proficiency more than
        once").  The set is therefore the union over every *owned* declaration
        that declares an Expertise-granting skill choice.

        A persisted record whose ``(owner, feature_id)`` matches no owned
        declaration contributes nothing, so a forged or stale slot cannot
        turn a skill into a two-Proficiency-Bonus skill.  Each entry keeps the
        declaring owner so two same-named ``expertise`` features stay
        distinguishable in provenance.
        """

        seen: dict[str, dict[str, str]] = {}
        for definition in self.features_for(character):
            choice = definition.choice
            if (
                choice is None
                or choice.kind != "skill_proficiency"
                or choice.grants != "expertise"
            ):
                continue
            selection = self.feature_selection(character, definition)
            for ref in (selection or {}).get("selected_refs") or []:
                skill_id = str(ref)[6:]
                if skill_id in seen:
                    continue
                seen[skill_id] = {
                    "skill_ref": f"skill:{skill_id}",
                    "code": definition.id,
                    "owner": definition.owner,
                }
        return [seen[key] for key in sorted(seen)]

    def half_proficiency_bonus(
        self, character: Mapping[str, Any] | None,
    ) -> bool:
        """Whether an owned declaration grants Jack of All Trades.

        The flag is read from the declaration, never from a feature id: a second
        class that later grants the same half-bonus needs no code change, and a
        same-named feature that does *not* grant it stays inert.
        """

        return any(
            definition.parameters.get("half_proficiency_bonus") is True
            for definition in self.active_features_for(character)
        )

    def ability_check_floor(
        self, character: Mapping[str, Any] | None,
    ) -> tuple[int, list[dict[str, str]]]:
        """The d20 floor an owned declaration puts on proficient checks.

        Returns the highest declared floor and every declaration that states
        it.  A floor of ``10`` means "treat a d20 roll of 9 or lower as a 10";
        it is a substitution of the *face*, never a reroll, so the caller must
        not draw a second d20.  The caller also owns the proficiency gate: this
        method answers only "what floor does the character own".
        """

        floor = 0
        sources: list[dict[str, str]] = []
        for definition in self.active_features_for(character):
            value = definition.parameters.get("ability_check_floor")
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            sources.append({"code": definition.id, "owner": definition.owner})
            floor = max(floor, value)
        return floor, sources

    def initiative_advantage_sources(
        self, character: Mapping[str, Any] | None,
    ) -> list[dict[str, str]]:
        """Owned declarations that grant Advantage on Initiative rolls."""

        return [
            {"code": definition.id, "owner": definition.owner}
            for definition in self.active_features_for(character)
            if definition.parameters.get("initiative_advantage") is True
        ]

    def skill_advantage_sources(
        self, character: Mapping[str, Any] | None, skill_id: str,
    ) -> list[dict[str, str]]:
        """Owned declarations that grant Advantage on one named skill check.

        ``skill_advantage_refs`` is an explicit list, so "Advantage on Strength
        (Athletics)" never widens into every Strength check: a skill that no
        declaration names answers with an empty list.
        """

        wanted = f"skill:{str(skill_id)}"
        sources: list[dict[str, str]] = []
        for definition in self.active_features_for(character):
            refs = definition.parameters.get("skill_advantage_refs")
            if not isinstance(refs, list) or wanted not in refs:
                continue
            sources.append({"code": definition.id, "owner": definition.owner})
        return sources

    def critical_hit_move(
        self, character: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        """The post-critical movement an owned declaration grants, or ``None``.

        The divisor is declared, so the "half your Speed" budget is computed
        from the current Speed instead of being stored: a Speed change between
        the hit and the move would otherwise pay out a stale number.
        """

        for definition in self.active_features_for(character):
            divisor = definition.parameters.get("critical_hit_move_speed_divisor")
            if isinstance(divisor, bool) or not isinstance(divisor, int) or divisor < 1:
                continue
            return {
                "code": definition.id,
                "owner": definition.owner,
                "divisor": divisor,
            }
        return None

    @staticmethod
    def _feature_choice_records(
        character: Mapping[str, Any] | None,
    ) -> list[Mapping[str, Any]]:
        """Every structurally valid persisted choice record, in stored order."""

        if not isinstance(character, Mapping):
            return []
        features = character.get("features")
        if not isinstance(features, Mapping):
            return []
        raw = features.get("feature_choices")
        if not isinstance(raw, list):
            return []
        return [record for record in raw if isinstance(record, Mapping)]

    def _selected_feature_options(
        self,
        character: Mapping[str, Any] | None,
        parent: ClassFeatureDefinition,
    ) -> frozenset[str]:
        """The option ids the character committed to under ``parent``.

        Identity is ``(owner, feature_id, choice_id)`` exactly as the
        advancement path stores it.  A record whose ``selected_refs`` is
        malformed contributes nothing: the character then simply does not own
        the option, which is the same answer as "never chose".
        """

        choice = parent.choice
        if choice is None:  # pragma: no cover - callers filter on choice
            return frozenset()
        selected: set[str] = set()
        for record in self._feature_choice_records(character):
            if (
                str(record.get("owner") or "") != parent.owner
                or str(record.get("feature_id") or "") != parent.id
                or str(record.get("choice_id") or "") != choice.id
            ):
                continue
            refs = record.get("selected_refs")
            if not isinstance(refs, list):
                continue
            for ref in refs:
                local = str(ref).removeprefix("feature:")
                if local in choice.enabled_options or f"feature:{local}" in choice.enabled_options:
                    selected.add(local)
        return frozenset(selected)

    def feature_option_choice(
        self,
        character: Mapping[str, Any] | None,
        feature_id: str,
        *,
        owner: str | None = None,
    ) -> str:
        """The single option id chosen under ``feature_id``, or ``""``.

        Returns ``""`` when the feature is not owned, is not an option parent,
        or has no (or more than one) committed selection.  Callers must treat
        an empty answer as "no option in force" rather than defaulting to one,
        so an un-answered choice grants nothing instead of guessing.
        """

        for definition in self.features_for(character):
            if definition.id != feature_id:
                continue
            if owner is not None and definition.owner != owner:
                continue
            if definition.choice is None or definition.choice.kind != "feature_option":
                continue
            selected = self._selected_feature_options(character, definition)
            if len(selected) == 1:
                return next(iter(selected))
            return ""
        return ""

    def _advancement_feat_categories(self) -> dict[str, str]:
        """The advancement feat catalog's id -> category map, read once."""

        if self._feat_categories is None:
            catalog = self.bundle.get("advancement_feat_catalog", "srd_advancement_feats")
            feats = catalog.get("feats") if isinstance(catalog, Mapping) else None
            self._feat_categories = (
                {
                    str(feat_id): str(feat.get("category") or "")
                    for feat_id, feat in feats.items()
                    if isinstance(feat, Mapping)
                }
                if isinstance(feats, Mapping)
                else {}
            )
        return self._feat_categories

    def feature_views(
        self, character: Mapping[str, Any] | None,
    ) -> tuple[FeatureView, ...]:
        """Presentation-safe views of the owned features, with resolved values.

        An owned feature whose equipment precondition does not hold is still
        listed (the character really has it) but reports ``active = False`` and
        no effect values, so a client never renders a die or an ability choice
        that is not currently in force.
        """

        views: list[FeatureView] = []
        for definition in self.features_for(character):
            active = self.feature_is_active(character, definition)
            values: dict[str, Any] = {}
            if active:
                for key, raw in definition.parameters.items():
                    if key.endswith("_format"):
                        continue
                    if key.endswith("_track"):
                        resolved = self._value(
                            definition, character, key[: -len("_track")], None,
                        )
                        if resolved is not None:
                            values[key[: -len("_track")]] = resolved
                    else:
                        values[key] = deepcopy(raw)
            views.append(FeatureView(
                id=definition.id,
                owner=definition.owner,
                name=self.catalog.label(definition.id, _humanize(definition.id)),
                summary=definition.summary,
                source_ref=definition.source_ref,
                minimum_level=definition.minimum_level,
                values=values,
                active=active,
                selection=self.feature_selection(character, definition),
            ))
        return tuple(views)

    def actor_projection(self, character: Mapping[str, Any] | None) -> dict[str, Any]:
        """One-pass projection of everything combat needs from this boundary.

        Combat keeps consuming plain data: the actor's canonical class
        resources (with their localized names), and the feature-derived
        Unarmed Strike / Monk Weapon parameters.  Neither the combat engine nor
        the frontend re-derives a class level, a Martial Arts die, or whether
        Martial Arts is currently active.
        """

        owned = self.features_for(character)
        active = tuple(
            definition for definition in owned
            if self.feature_is_active(character, definition)
        )
        grants = self._spell_grant_rows(owned, character)
        resources = [
            definition.to_dict()
            for definition in self._resource_definitions(owned, character)
        ]
        # 一条授予行可以同时标出"哪个池能替它付账"（Natural Recovery 的免费面
        # 就是"当前地形授予的任一法术，无槽一次，每长休一次"）。标在原行上而不是
        # 另起一行：另起一行会让同一法术在 `spell_grants` 里出现两次，而那一行
        # 只有两个键——provenance 没了，按 `spell_ref` 读发布的消费者也会撞上重复。
        #
        # 池必须真的可用才标：`_resource_definitions` 不看 `requires_subclass`
        # （月德鲁伊的 max 会算成 0），存档里也可能还没有这个池。把不存在的池
        # 标进去，等于投影在说"这个法术能被它付账"，而付账时会失败。
        usable_pools = {
            str(row["id"]) for row in resources if int(row["maximum"] or 0) > 0
        }
        free_cast_pools = {
            definition.id: str(definition.parameters.get("free_cast_resource_id") or "")
            for definition in owned
            if definition.parameters.get("free_cast_resource_id")
        }
        return {
            "spell_grants": [
                # 常驻授予（子职法术表、Land 地形、Magical Discoveries）与
                # "免费施放" 授予是两种东西：前者只说明"已准备"，不带资源，
                # 后者挂一个职业资源池。两者同列但都不许对方冒充自己。
                *[
                    {
                        **row,
                        "resource_id": free_cast_pools[row["feature_id"]],
                    }
                    if free_cast_pools.get(row["feature_id"]) in usable_pools
                    else dict(row)
                    for row in grants
                ],
                *[
                    {
                        "spell_ref": f"spell:{definition.parameters['granted_spell_id']}",
                        "resource_id": definition.resource_id,
                    }
                    for definition in active
                    if definition.parameters.get("granted_spell_id")
                ],
            ],
            # 0/0 行对谁都没有意义：子职不对、等级没到、或存档里还没有这个池时，
            # 放进来只会让 UI 与 API 消费者看见一个"幽灵资源"。
            "class_resources": [
                row for row in resources if int(row["maximum"] or 0) > 0
            ],
            "unarmed_damage_die": self._unarmed_die(active, character),
            "unarmed_ability_choice": list(self._ability_choice(active, character)),
            "martial_arts_weapon_refs": list(
                self._benefitting_weapon_refs(character, active)
            ),
            "ranged_attack_bonus": self._ranged_attack_bonus(character),
            "light_extra_attack_ability_damage": (
                self._light_extra_attack_ability_damage(character)
            ),
        }

    def projection_fields(self, character: Mapping[str, Any] | None) -> dict[str, Any]:
        """The legacy-sheet class feature / class resource rows.

        The single place that defines this projection shape: character creation,
        advancement, rest and live equipment reconciliation all publish the same
        two fields instead of each re-deriving them.
        """

        return {
            "class_features": [view.to_dict() for view in self.feature_views(character)],
            "class_resources": [
                definition.to_dict()
                for definition in self.resource_definitions(character)
                if int(definition.maximum) > 0
            ],
        }

    # ------------------------------------------------------------------
    # class resources
    # ------------------------------------------------------------------

    def class_resource_definition(
        self, character: Mapping[str, Any] | None, resource_id: str,
    ) -> ResourceDefinition | None:
        """The character's own view of one class resource, or ``None`` if unowned."""

        state = self.class_state(character)
        if state is None:
            return None
        class_id, level = state
        wanted = str(resource_id or "")
        # 职业特性与起源专长共用 ``resources.class`` 存储；窗口资源（幸运点、
        # 诗人激励、黑暗强运）正是通过这两条拥有路径被战斗投影消费的。特性未
        # 显式声明 resource_id 时仍按职业声明兜底，保持旧角色投影不丢资源。
        declaration = self._owned_resource_declaration(character, wanted)
        owner = next(
            (
                definition for definition in self.catalog.class_features(class_id)
                if definition.resource_id == wanted
            ),
            None,
        )
        if declaration is None:
            if owner is None:
                return None
            minimum_level = owner.minimum_level
            source_ref = owner.source_ref
        else:
            minimum_level = declaration.minimum_level
            source_ref = declaration.source_ref
        if level < minimum_level:
            return None
        # 子职门禁必须在投影里也成立：`sync_resources` 早就会按 requires_subclass
        # 把池删掉，但投影读的是记录本身。少了这一道，月德鲁伊的 actor 会拿到
        # 一个 store 里不存在的自然恢复（1/1），前端过滤 maximum>0 也救不了。
        required = str(getattr(declaration, "requires_subclass", "") or "")
        if required:
            features = character.get("features") if isinstance(character, Mapping) else None
            held = str((features or {}).get("subclass_ref") or "")
            if held != required:
                return None
        entry = resource_state(character, wanted)
        current = entry.get("current", 0)
        maximum = entry.get("maximum", 0)
        return ResourceDefinition(
            id=wanted,
            name=self.catalog.label(wanted, _humanize(wanted)),
            current=current if isinstance(current, int) and not isinstance(current, bool) else 0,
            maximum=maximum if isinstance(maximum, int) and not isinstance(maximum, bool) else 0,
            minimum_level=minimum_level,
            source_ref=source_ref,
            recovery=declaration.recovery() if declaration is not None else {},
        )

    def resource_definitions(
        self, character: Mapping[str, Any] | None,
    ) -> tuple[ResourceDefinition, ...]:
        """Every class resource the character owns a declaration for."""

        return self._resource_definitions(self.features_for(character), character)

    def _resource_definitions(
        self,
        owned: tuple[ClassFeatureDefinition, ...],
        character: Mapping[str, Any] | None,
    ) -> tuple[ResourceDefinition, ...]:
        resource_ids: list[str] = []
        for definition in owned:
            if definition.resource_id and definition.resource_id not in resource_ids:
                resource_ids.append(definition.resource_id)
        # A pool the class table declares is owned through the class record, not
        # through a named feature.  Enumerating only ``feature.resource_id`` left
        # every such pool (Channel Divinity, Wild Shape, Lay On Hands, Sorcery
        # Points, ...) written into the store but invisible on the sheet and in
        # the combat window.  ``class_resource_definition`` still applies the
        # declared ``minimum_level``, so a low-level character resolves nothing.
        state = self.class_state(character)
        if state is not None:
            for resource_id in self.resources.by_class.get(state[0], {}):
                if resource_id not in resource_ids:
                    resource_ids.append(resource_id)
        # Origin-feat pools (Lucky's Luck Points) are owned through
        # ``features.feat_refs`` rather than a class table, but they live in the
        # same canonical ``resources.class`` store.  The actor projection is the
        # public source for decision-window resource counts, so an owned feat
        # pool must appear there exactly like a class pool does.
        feat_refs = self._feat_refs(character)
        for feat_id, declarations in self.resources.by_feat.items():
            if f"feat:{feat_id}" not in feat_refs:
                continue
            for resource_id in declarations:
                if resource_id not in resource_ids:
                    resource_ids.append(resource_id)
        result: list[ResourceDefinition] = []
        for resource_id in resource_ids:
            resolved = self.class_resource_definition(character, resource_id)
            if resolved is not None:
                result.append(resolved)
        return tuple(result)

    @staticmethod
    def _feat_refs(character: Mapping[str, Any] | None) -> set[str]:
        features = character.get("features") if isinstance(character, Mapping) else None
        features = features if isinstance(features, Mapping) else {}
        return {str(ref) for ref in features.get("feat_refs") or []}

    def _owned_resource_declaration(
        self, character: Mapping[str, Any] | None, resource_id: str,
    ) -> ClassResourceDeclaration | None:
        """The class or owned-feat declaration that grants ``resource_id``."""

        state = self.class_state(character)
        if state is None:
            return None
        declaration = self.resources.declaration(state[0], resource_id)
        if declaration is not None:
            return declaration
        feat_refs = self._feat_refs(character)
        for feat_id, declarations in self.resources.by_feat.items():
            if f"feat:{feat_id}" not in feat_refs:
                continue
            declaration = declarations.get(resource_id)
            if declaration is not None:
                return declaration
        return None

    # ------------------------------------------------------------------
    # combat capabilities
    # ------------------------------------------------------------------

    def combat_capabilities(
        self,
        character: Mapping[str, Any] | None,
        combat_context: Mapping[str, Any] | None = None,
        *,
        include_unavailable: bool = False,
        actor_id: str = "",
    ) -> tuple[CombatCapabilityView, ...]:
        """Capabilities the character may declare in the given combat context.

        Only features whose equipment precondition currently holds contribute
        capabilities: Bonus Unarmed Strike is a Martial Arts benefit and must
        disappear with it, while the Focus actions (a different class feature)
        stay available.

        ``actor_id`` scopes the turn-start window.  The window lives on the
        combat economy, which is one object for the whole table, so a
        capability that may only be paid "at the start of your turn" has to be
        matched against the actor being evaluated -- otherwise every monk would
        see the window the current actor's turn just opened. The combat actor
        snapshot already filters expired long effects using the world clock.
        """

        active = self.active_features_for(character)
        definitions = {
            definition.id: definition for definition in self.resource_definitions(character)
        }
        spellcasting = (character or {}).get("spellcasting")
        class_magic = spellcasting.get("class") if isinstance(spellcasting, Mapping) else None
        return _project_capabilities(
            self.catalog,
            active,
            CombatContext.from_economy(
                combat_context, actor_id,
                class_magic=class_magic if isinstance(class_magic, Mapping) else None,
                active_effects=frozenset(long_effects(character)),
            ),
            definitions,
            include_unavailable=include_unavailable,
        )

    def declared_capability(
        self, character: Mapping[str, Any] | None, capability_id: str,
    ) -> FeatureCapability | None:
        """The currently active capability declaration with this id, or ``None``."""

        wanted = str(capability_id or "")
        for definition in self.active_features_for(character):
            for capability in definition.capabilities:
                if capability.id == wanted:
                    return capability
        return None

    def feature_definition(self, feature_id: str) -> ClassFeatureDefinition | None:
        """The declaration with this id, whichever owner-scoped key holds it.

        ``features_by_key`` is keyed by ``(owner, id)`` because the same feature
        id can exist for a class and a subclass.  Callers that only know the id
        -- a capability's declaring feature, for instance -- need the owner to
        find the class-level table the feature's numbers live in, so this
        lookup prefers the bare class owner over a subclass one.
        """

        wanted = str(feature_id or "")
        found: ClassFeatureDefinition | None = None
        for (owner, identifier), definition in self.catalog.features_by_key.items():
            if identifier != wanted:
                continue
            if owner.startswith("class:"):
                return definition
            found = found or definition
        return found

    def unarmed_strike_die(self, character: Mapping[str, Any] | None) -> str:
        """The feature-derived base damage of this character's Unarmed Strike."""

        return self._unarmed_die(self.active_features_for(character), character)

    def _unarmed_die(
        self, owned: tuple[ClassFeatureDefinition, ...], character: Mapping[str, Any] | None,
    ) -> str:
        for definition in owned:
            die = self._value(definition, character, "unarmed_damage_die", "")
            if isinstance(die, str) and die:
                return die
        return ""

    def _ranged_attack_bonus(self, character: Mapping[str, Any] | None) -> int:
        """Flat bonus this character's feats grant to ranged weapon attack rolls.

        Feat parameters are aggregated here so combat never reads the
        advancement feat catalog: the actor projection is the single boundary
        that knows which feats the character owns. Whether an attack actually
        uses a ranged weapon stays the combat engine's decision.
        """

        if not isinstance(character, Mapping):
            return 0
        features = character.get("features")
        feat_refs = features.get("feat_refs") if isinstance(features, Mapping) else None
        if not isinstance(feat_refs, list):
            return 0
        catalog = self.bundle.get("advancement_feat_catalog", "srd_advancement_feats")
        feats = catalog.get("feats") if isinstance(catalog, Mapping) else None
        if not isinstance(feats, Mapping):
            return 0
        total = 0
        for ref in feat_refs:
            if not isinstance(ref, str):
                continue
            feat = feats.get(ref.removeprefix("feat:"))
            parameters = feat.get("parameters") if isinstance(feat, Mapping) else None
            bonus = (
                parameters.get("ranged_attack_bonus")
                if isinstance(parameters, Mapping) else None
            )
            if isinstance(bonus, int) and not isinstance(bonus, bool):
                total += bonus
        return total

    def _light_extra_attack_ability_damage(
        self, character: Mapping[str, Any] | None,
    ) -> bool:
        """Whether the Light extra attack adds the ability modifier to damage.

        Two-Weapon Fighting是唯一来源；判定留在 combat（它才知道这次攻击是不是
        Light 追加之击），这里只回答"这名角色有没有这条风格"。参数走 feat 的
        ``parameters`` 通道，与 ``ranged_attack_bonus`` 同一形状。
        """

        if not isinstance(character, Mapping):
            return False
        features = character.get("features")
        feat_refs = features.get("feat_refs") if isinstance(features, Mapping) else None
        if not isinstance(feat_refs, list):
            return False
        catalog = self.bundle.get("advancement_feat_catalog", "srd_advancement_feats")
        feats = catalog.get("feats") if isinstance(catalog, Mapping) else None
        if not isinstance(feats, Mapping):
            return False
        for ref in feat_refs:
            if not isinstance(ref, str):
                continue
            feat = feats.get(ref.removeprefix("feat:"))
            parameters = feat.get("parameters") if isinstance(feat, Mapping) else None
            if (
                isinstance(parameters, Mapping)
                and parameters.get("light_extra_attack_ability_damage") is True
            ):
                return True
        return False

    def unarmed_ability_choice(
        self, character: Mapping[str, Any] | None,
    ) -> tuple[str, ...]:
        """The abilities this character may use for Unarmed Strikes (and Monk Weapons)."""

        return self._ability_choice(self.active_features_for(character), character)

    def _ability_choice(
        self, owned: tuple[ClassFeatureDefinition, ...], character: Mapping[str, Any] | None,
    ) -> tuple[str, ...]:
        for definition in owned:
            choice = self._value(definition, character, "unarmed_ability_choice", ())
            if isinstance(choice, (list, tuple)) and choice:
                return tuple(str(item) for item in choice)
        return ()

    def effective_unarmed_strike(
        self, character: Mapping[str, Any] | None, base: Mapping[str, Any],
    ) -> dict[str, Any]:
        """The actor-specific effective profile of the canonical Unarmed Strike."""

        return _martial_arts_profile(
            base,
            damage_die=self.unarmed_strike_die(character),
            ability_choice=self.unarmed_ability_choice(character),
        )

    def _benefitting_weapon_refs(
        self,
        character: Mapping[str, Any] | None,
        active: tuple[ClassFeatureDefinition, ...],
    ) -> tuple[str, ...]:
        """Equipped weapon refs that currently receive a feature's weapon benefits.

        The feature that supplies the Unarmed Strike damage die also declares
        which canonical weapon kinds it covers (Martial Arts: the Monk Weapon),
        so a wielded Monk Weapon gets the same ability choice and damage-die
        treatment as the canonical Unarmed Strike.  Returns nothing while that
        feature is inactive, and nothing for a character with no such feature.
        """

        if not self.available:
            return ()
        for definition in active:
            if not self._value(definition, character, "unarmed_damage_die", ""):
                continue
            requirement = definition.equipment_requirement
            if requirement is None or not requirement.weapon_kinds:
                continue
            return self.equipment.weapon_refs_of_kinds(character, requirement.weapon_kinds)
        return ()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _value(
        self,
        definition: ClassFeatureDefinition,
        character: Mapping[str, Any] | None,
        key: str,
        default: Any,
    ) -> Any:
        name = str(key)
        if name in definition.parameters:
            return deepcopy(definition.parameters[name])
        track_id = definition.parameters.get(f"{name}_track")
        if not isinstance(track_id, str) or not track_id:
            return default
        resolved = self._track_value(character, definition.class_id, track_id, default)
        template = definition.parameters.get(f"{name}_format")
        if isinstance(template, str) and template:
            if not isinstance(resolved, int):
                return default
            return template.format(die=resolved)
        return resolved

    def _granted_feature_keys(
        self, class_id: str, level: int, character: Mapping[str, Any] | None,
    ) -> frozenset[tuple[str, str]]:
        """Owner-scoped keys the class table and selected subclass grant."""

        if self.progression is None:
            return frozenset()
        progression = self.progression.classes.get(str(class_id))
        if progression is None:
            return frozenset()
        owner = f"class:{class_id}"
        granted: set[tuple[str, str]] = set()
        for row in progression["features_by_level"][:level]:
            for local_id in row:
                key = self._key_for_grant(str(local_id), owner)
                if key is not None:
                    granted.add(key)
        subclass_ref = self._selected_subclass_ref(class_id, character)
        if subclass_ref:
            subclass = self.progression.subclasses.get(str(class_id))
            if subclass is not None:
                for raw_level, feature_ids in subclass["feature_ids_by_level"].items():
                    if 1 <= int(raw_level) <= level:
                        for local_id in feature_ids:
                            key = self._key_for_grant(str(local_id), subclass_ref)
                            if key is not None:
                                granted.add(key)
        return frozenset(granted)

    def _key_for_grant(
        self, local_id: str, preferred_owner: str,
    ) -> tuple[str, str] | None:
        """The declaration a grant actually owns.

        Prefer the grant's own owner.  A uniquely declared shared feature (for
        example Extra Attack granted to several classes) keeps that shared
        declaration's owner rather than inventing a second definition: the
        number is one rule, and duplicating the declaration would let one class
        silently carry a different ``attacks_per_action`` from another.

        The consequence is visible but intentional -- a Monk's Extra Attack
        reports ``owner == "class:fighter"`` -- which is why
        :meth:`has_feature` documents its ``owner`` argument as a declaration
        owner, not as the granting class.
        """

        preferred = (preferred_owner, local_id)
        if preferred in self._feature_index:
            return preferred
        matches = [
            key for key, definition in self._feature_index.items()
            if definition.id == local_id
        ]
        return matches[0] if len(matches) == 1 else None

    def _selected_subclass_ref(
        self, class_id: str, character: Mapping[str, Any] | None,
    ) -> str:
        """Return the persisted subclass ref when it belongs to ``class_id``.

        Missing, empty, malformed, or foreign refs deliberately grant nothing.
        A direct sheet gets no representative subclass fallback: choosing a
        subclass is character state, not a consequence of having a class level.
        """

        if self.progression is None:
            return ""
        subclass = self.progression.subclasses.get(str(class_id))
        if subclass is None:
            return ""
        features = character.get("features") if isinstance(character, Mapping) else None
        ref = features.get("subclass_ref") if isinstance(features, Mapping) else None
        if not isinstance(ref, str) or not ref:
            return ""
        return ref if ref == f"subclass:{subclass.get('id')}" else ""

    def _track_value(
        self,
        character: Mapping[str, Any] | None,
        class_id: str,
        track_id: str,
        default: Any,
    ) -> Any:
        state = self.class_state(character)
        if state is None or self.progression is None:
            return default
        try:
            snapshot = self.progression.snapshot(f"class:{class_id}", state[1])
        except ProgressionCatalogError:
            return default
        tracks = snapshot["tracks"]
        if track_id not in tracks:
            return default
        return int(tracks[track_id])
