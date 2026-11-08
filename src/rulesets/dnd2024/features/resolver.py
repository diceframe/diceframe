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
    resource_state,
)


def _humanize(feature_id: str) -> str:
    return str(feature_id).replace("_", " ").strip().title()


# The defeat-trigger payload is one closed four-key set.  ``trigger`` names the
# only supported event; the other three describe the temp-HP formula.  Keeping
# the names in one place means the loader and the combat helper can never
# disagree about which keys exist.
_DEFEAT_TRIGGER_KEYS = (
    "trigger", "temp_hp_ability", "temp_hp_class_ref", "temp_hp_minimum",
)
# The one trigger this slice understands.  It is exported because the combat
# settlement reads the same name: a loader that accepts a trigger the engine
# never fires would ship a silently inert feature.
ENEMY_REDUCED_TO_ZERO_HP = "enemy_reduced_to_zero_hp"
_DEFEAT_TRIGGERS = frozenset({ENEMY_REDUCED_TO_ZERO_HP})
# Parameters the previous implementation accepted.  A declaration that still
# carries them describes a rule SRD 5.2.1 does not have, so loading fails
# instead of quietly ignoring the extra keys.
_RETIRED_DEFEAT_KEYS = (
    "defeat_temp_hp_ability",
    "defeat_temp_hp_add_class_level",
    "defeat_share_range_ft",
)


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
        self._validate_declarations()

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

        Second-Story Work lets the Thief *replace* Strength with Dexterity --
        the substitution changes which score is read, it never adds a second
        modifier.  Exactly one owned declaration may name it, so the first is
        authoritative and the default stays Strength.
        """

        for definition in self.features_for(character):
            ability = definition.parameters.get("jump_ability")
            if isinstance(ability, str) and ability:
                return ability
        return "str"

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
        selected: list[str] = []
        if len(matches) == 1:
            raw_refs = matches[0].get("selected_refs")
            categories = self._advancement_feat_categories()
            if isinstance(raw_refs, list):
                for ref in raw_refs:
                    if not isinstance(ref, str):
                        continue
                    if categories.get(ref.removeprefix("feat:")) != choice.category:
                        continue
                    if ref not in selected:
                        selected.append(ref)
        return {"choice_id": choice.id, "selected_refs": selected}

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
        return {
            "spell_grants": [
                {"spell_ref": f"spell:{definition.parameters['granted_spell_id']}", "resource_id": definition.resource_id}
                for definition in active if definition.parameters.get("granted_spell_id")
            ],
            "class_resources": [
                definition.to_dict()
                for definition in self._resource_definitions(owned, character)
            ],
            "unarmed_damage_die": self._unarmed_die(active, character),
            "unarmed_ability_choice": list(self._ability_choice(active, character)),
            "martial_arts_weapon_refs": list(
                self._benefitting_weapon_refs(character, active)
            ),
            "ranged_attack_bonus": self._ranged_attack_bonus(character),
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
    ) -> tuple[CombatCapabilityView, ...]:
        """Capabilities the character may declare in the given combat context.

        Only features whose equipment precondition currently holds contribute
        capabilities: Bonus Unarmed Strike is a Martial Arts benefit and must
        disappear with it, while the Focus actions (a different class feature)
        stay available.
        """

        active = self.active_features_for(character)
        definitions = {
            definition.id: definition for definition in self.resource_definitions(character)
        }
        return _project_capabilities(
            self.catalog,
            active,
            CombatContext.from_economy(combat_context),
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

    def _validate_declarations(self) -> None:
        """Fail closed when a declared parameter has no backing bundle table."""

        progression_catalog = self.progression
        if progression_catalog is None:  # pragma: no cover - guarded by ``available``
            return
        for definition in self._feature_index.values():
            spell_ref = definition.parameters.get("granted_spell_id")
            if spell_ref is not None:
                spells = (self.bundle.get("spell_catalog", "srd_spells") or {}).get("spells", [])
                if not isinstance(spell_ref, str) or not spell_ref or ":" in spell_ref or not any(
                    row.get("id") == spell_ref for row in spells
                ):
                    raise ClassFeatureError(f"{definition.id} grants an unknown spell")
            progression = progression_catalog.classes.get(definition.class_id)
            if progression is None:
                raise ClassFeatureError(
                    f"class feature {definition.id} references an unknown class table"
                )
            self._validate_owner_grant(definition, progression_catalog)
            self._validate_aura_parameters(definition)
            for key, value in definition.parameters.items():
                if key == "crit_threshold":
                    if (
                        isinstance(value, bool) or not isinstance(value, int)
                        or not 2 <= value <= 19
                    ):
                        raise ClassFeatureError(
                            f"{definition.id}.{key} must be an integer from 2 to 19"
                        )
                if key == "attacks_per_action":
                    # 声明值直接决定 Attack 动作能吃几次攻击；2 是 Extra Attack
                    # 的下限，8 远高于任何官方职业的上限，超出区间一定写错了。
                    if (
                        isinstance(value, bool) or not isinstance(value, int)
                        or not 2 <= value <= 8
                    ):
                        raise ClassFeatureError(
                            f"{definition.id}.{key} must be an integer from 2 to 8"
                        )
                if key.endswith("_format"):
                    if (
                        not isinstance(value, str)
                        or value.count("{die}") != 1
                        or value.replace("{die}", "").count("{") != 0
                        or value.replace("{die}", "").count("}") != 0
                    ):
                        raise ClassFeatureError(
                            f"{definition.id}.{key} must be a single-placeholder template"
                        )
                    continue
                if not key.endswith("_track"):
                    continue
                if not isinstance(value, str) or not value:
                    raise ClassFeatureError(f"{definition.id}.{key} must name a track")
                if value not in progression["tracks"]:
                    raise ClassFeatureError(
                        f"{definition.id}.{key} names an unknown progression track: {value}"
                    )
            self._validate_passive_parameters(definition)
            self._validate_defeat_trigger_parameters(definition)
            self._validate_defense_parameters(definition)
            self._validate_speed_parameters(definition)
            self._validate_movement_parameters(definition)
            self._validate_overchannel_parameters(definition)
            if definition.resource_id:
                declaration = self.resources.declaration(
                    definition.class_id, definition.resource_id,
                )
                if declaration is None:
                    raise ClassFeatureError(
                        f"{definition.id} references an undeclared class resource: "
                        f"{definition.resource_id}"
                    )

    @staticmethod
    def _validate_owner_grant(
        definition: ClassFeatureDefinition,
        progression_catalog: Dnd2024ProgressionCatalog,
    ) -> None:
        """Prove a non-option declaration has a real owner-side grant.

        Subclass declarations must name the subclass the progression catalog
        associates with the declaring class.  Class declarations may still be
        issued by the subclass table: several pre-existing subclass features
        were declared before owner scoping and retain ``class:`` as their
        default owner.

        A subclass declaration must state exactly the level its own table
        grants it at: the subclass table is its only source, so a drifted
        level would silently move the unlock.  A class declaration is looser
        on purpose.  Third-party bundles can hand an id out earlier than the
        catalog says it starts, and ``features_for`` already resolves that
        case through the ``minimum_level > level`` filter; failing the whole
        bundle there would turn a cosmetic mismatch into a ruleset that
        refuses to load.  What must never pass, either way, is a declaration
        that no table grants at all -- that feature can never become owned.
        """

        if definition.option_of:
            return
        class_id = definition.class_id
        progression = progression_catalog.classes[class_id]
        subclass = progression_catalog.subclasses.get(class_id)
        class_levels = [
            level
            for level, feature_ids in enumerate(
                progression["features_by_level"], start=1,
            )
            if definition.id in feature_ids
        ]
        subclass_levels: list[int] = []
        if subclass is not None:
            subclass_levels = [
                int(level)
                for level, feature_ids in subclass["feature_ids_by_level"].items()
                if definition.id in feature_ids
            ]
        if definition.owner.startswith("subclass:"):
            subclass_id = definition.owner.removeprefix("subclass:")
            if subclass is None or str(subclass.get("id") or "") != subclass_id:
                raise ClassFeatureError(
                    f"{definition.id}.owner is not a subclass of {class_id}"
                )
            if not subclass_levels:
                raise ClassFeatureError(
                    f"{definition.id} has no grant in {definition.owner}"
                )
            if definition.minimum_level != min(subclass_levels):
                raise ClassFeatureError(
                    f"{definition.id}.minimum_level must match its subclass grant"
                )
        else:
            levels = class_levels or subclass_levels
            if not levels:
                raise ClassFeatureError(
                    f"{definition.id} has no class or subclass grant"
                )

    @staticmethod
    def _validate_passive_parameters(definition: ClassFeatureDefinition) -> None:
        """Fail closed on passive primitives a consumer would otherwise misread.

        A declared advantage flag must be exactly ``True``: a ``False``/truthy
        lookalike would silently change death-save dice with no way to tell
        intent from typo.  The regeneration pair must appear together so the
        amount and its ability never drift apart.
        """

        parameters = definition.parameters
        if "death_save_advantage" in parameters and (
            parameters["death_save_advantage"] is not True
        ):
            raise ClassFeatureError(
                f"{definition.id}.death_save_advantage must be true when declared"
            )
        threshold = parameters.get("death_save_crit_threshold")
        if "death_save_crit_threshold" in parameters and (
            isinstance(threshold, bool) or not isinstance(threshold, int)
            or not 2 <= threshold <= 20
        ):
            raise ClassFeatureError(
                f"{definition.id}.death_save_crit_threshold must be an integer from 2 to 20"
            )
        base = parameters.get("turn_start_regen_base")
        if "turn_start_regen_base" in parameters and (
            isinstance(base, bool) or not isinstance(base, int) or base < 1
        ):
            raise ClassFeatureError(
                f"{definition.id}.turn_start_regen_base must be a positive integer"
            )
        ability = parameters.get("turn_start_regen_ability")
        if "turn_start_regen_ability" in parameters and (
            not isinstance(ability, str) or ability not in ABILITY_IDS
        ):
            raise ClassFeatureError(
                f"{definition.id}.turn_start_regen_ability must name an ability"
            )
        if (
            "turn_start_regen_base" in parameters
        ) != ("turn_start_regen_ability" in parameters):
            raise ClassFeatureError(
                f"{definition.id} must declare turn_start_regen_base and "
                "turn_start_regen_ability as a pair"
            )

    def _validate_defeat_trigger_parameters(self, definition: ClassFeatureDefinition) -> None:
        """Fail closed on the defeat-trigger payload.

        The payload is one closed four-key set: what triggers it, which ability
        feeds the formula, which class level the formula adds, and the floor.
        Declaring a subset would leave the formula with either no ability or no
        level, which resolves to a silently wrong or inert feature -- so a
        partial set is rejected at load time rather than at settlement.

        The set carries no distance, share, resource, cooldown or visibility
        knob, because the rule it encodes has none: only the source's own kill
        triggers it.
        """

        parameters = definition.parameters
        retired = [key for key in _RETIRED_DEFEAT_KEYS if key in parameters]
        if retired:
            # 旧载荷带着“他人击杀 10 尺共享”与“加职业等级”开关。它们不是
            # SRD 5.2.1 的 Dark One's Blessing，静默忽略等于把已删除的规则
            # 留在目录里，因此这里显式拒绝。
            raise ClassFeatureError(
                f"{definition.id} declares the retired parameter {retired[0]}"
            )
        keys = _DEFEAT_TRIGGER_KEYS
        # Only claim a ``trigger`` key this payload actually owns.  Another
        # slice may declare its own trigger name on a different feature, and
        # rejecting it here would couple two unrelated payloads.
        formula_keys = [key for key in keys if key != "trigger" and key in parameters]
        if not formula_keys and parameters.get("trigger") not in _DEFEAT_TRIGGERS:
            return
        missing = [key for key in keys if key not in parameters]
        if missing:
            raise ClassFeatureError(
                f"{definition.id} must declare {' / '.join(keys)} together; "
                f"missing {' / '.join(missing)}"
            )
        trigger = parameters.get("trigger")
        if trigger not in _DEFEAT_TRIGGERS:
            raise ClassFeatureError(
                f"{definition.id}.trigger must be one of {sorted(_DEFEAT_TRIGGERS)}"
            )
        ability = parameters.get("temp_hp_ability")
        if not isinstance(ability, str) or ability not in ABILITY_IDS:
            raise ClassFeatureError(
                f"{definition.id}.temp_hp_ability must name an ability"
            )
        class_ref = parameters.get("temp_hp_class_ref")
        if not isinstance(class_ref, str) or not class_ref.startswith("class:"):
            raise ClassFeatureError(
                f"{definition.id}.temp_hp_class_ref must be a class:<id> reference"
            )
        class_id = class_ref.removeprefix("class:")
        progression = self.progression
        if (
            not class_id
            or progression is None
            or class_id not in progression.classes
        ):
            raise ClassFeatureError(
                f"{definition.id}.temp_hp_class_ref names an unknown class table"
            )
        minimum = parameters.get("temp_hp_minimum")
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ClassFeatureError(
                f"{definition.id}.temp_hp_minimum must be an integer of at least 1"
            )

    @staticmethod
    def _validate_speed_parameters(definition: ClassFeatureDefinition) -> None:
        """Fail closed on the Speed-bonus contract.

        A flat Speed bonus is a small positive number of feet; a negative or
        absurd value would silently rewrite every derived Speed in the game.
        The level-scaled form is validated by the generic ``_track`` rule.
        """

        parameters = definition.parameters
        value = parameters.get("speed_bonus_ft")
        if "speed_bonus_ft" in parameters and (
            isinstance(value, bool) or not isinstance(value, int)
            or not 1 <= value <= 120
        ):
            raise ClassFeatureError(
                f"{definition.id}.speed_bonus_ft must be an integer from 1 to 120"
            )

    @staticmethod
    def _validate_movement_parameters(definition: ClassFeatureDefinition) -> None:
        """Fail closed on the special-Speed and jump contract.

        ``climb_speed_from_speed`` / ``swim_speed_from_speed`` are flags, not
        amounts: the distance always equals the character's current Speed, so a
        ``False`` or a number would promise a mode the resolver cannot honour.
        ``jump_ability`` names the score that sets the jump distance, and a typo
        there would silently fall back to Strength -- the very substitution the
        Thief feature exists to make.
        """

        parameters = definition.parameters
        for key in ("climb_speed_from_speed", "swim_speed_from_speed"):
            if key in parameters and parameters[key] is not True:
                raise ClassFeatureError(
                    f"{definition.id}.{key} must be true when declared"
                )
        ability = parameters.get("jump_ability")
        if "jump_ability" in parameters and ability not in ("str", "dex"):
            raise ClassFeatureError(
                f"{definition.id}.jump_ability must be str or dex"
            )

    def _validate_aura_parameters(self, definition: ClassFeatureDefinition) -> None:
        """Fail closed on the aura declaration contract.

        An aura is three separate declarations that only mean something
        together: the base aura owns the range and the saving-throw bonus, an
        expansion names a base aura and overrides its range, and a condition
        immunity names the base aura it rides on.  Nothing here names Paladin
        -- the relation is catalog data, so the shape is reusable -- and a
        dangling or self-referential relation is rejected at load because the
        consumer would otherwise project no aura at all and say nothing.
        """

        parameters = definition.parameters
        # 同一 id 可能同时存在于 class 与 subclass 两个 owner 下，按 id 收集后
        # 只认真的声明了范围的那一条，避免拿错 owner 的同名条目。
        index = self.catalog.features_by_key

        def _base_aura(feature_id: Any) -> ClassFeatureDefinition | None:
            if not isinstance(feature_id, str) or not feature_id:
                return None
            for candidate in index.values():
                if candidate.id == feature_id and "aura_range_ft" in candidate.parameters:
                    return candidate
            return None

        for key in ("aura_range_ft", "aura_range_override_ft"):
            value = parameters.get(key)
            if key in parameters and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ClassFeatureError(
                    f"{definition.id}.{key} must be a positive integer"
                )

        has_ability = "aura_save_bonus_ability" in parameters
        has_minimum = "aura_save_bonus_minimum" in parameters
        if has_ability != has_minimum:
            raise ClassFeatureError(
                f"{definition.id} must declare aura_save_bonus_ability and "
                f"aura_save_bonus_minimum together"
            )
        if has_ability:
            ability = parameters["aura_save_bonus_ability"]
            if not isinstance(ability, str) or ability not in ABILITY_IDS:
                raise ClassFeatureError(
                    f"{definition.id}.aura_save_bonus_ability must name an ability"
                )
            minimum = parameters["aura_save_bonus_minimum"]
            if (
                isinstance(minimum, bool) or not isinstance(minimum, int)
                or minimum < 1
            ):
                raise ClassFeatureError(
                    f"{definition.id}.aura_save_bonus_minimum must be an integer "
                    f"of at least 1"
                )

        # 豁免加值必须挂在一个有范围的基础光环上，否则它永远不会生效。
        bonus_declared = has_ability or "aura_save_bonus_minimum" in parameters
        if bonus_declared and "aura_range_ft" not in parameters:
            raise ClassFeatureError(
                f"{definition.id} declares a saving-throw bonus without aura_range_ft"
            )

        has_immunities = "aura_condition_immunities" in parameters
        has_source = "aura_source_feature_id" in parameters
        if has_immunities != has_source:
            raise ClassFeatureError(
                f"{definition.id} must declare aura_source_feature_id and "
                f"aura_condition_immunities together"
            )
        if has_immunities:
            immunities = parameters["aura_condition_immunities"]
            if not isinstance(immunities, list) or not immunities:
                raise ClassFeatureError(
                    f"{definition.id}.aura_condition_immunities must be a non-empty list"
                )
            if any(not isinstance(code, str) or not code for code in immunities):
                raise ClassFeatureError(
                    f"{definition.id}.aura_condition_immunities must hold condition ids"
                )
            if len(set(immunities)) != len(immunities):
                raise ClassFeatureError(
                    f"{definition.id}.aura_condition_immunities repeats a condition"
                )
            unsupported = sorted(code for code in immunities if code != "charmed")
            if unsupported:
                raise ClassFeatureError(
                    f"{definition.id}.aura_condition_immunities does not support "
                    f"{', '.join(unsupported)} yet"
                )
            source = _base_aura(parameters["aura_source_feature_id"])
            if source is None:
                raise ClassFeatureError(
                    f"{definition.id}.aura_source_feature_id must name a base aura"
                )

        has_override = "aura_range_override_ft" in parameters
        has_expands = "aura_expands_feature_id" in parameters
        if has_override != has_expands:
            raise ClassFeatureError(
                f"{definition.id} must declare aura_expands_feature_id and "
                f"aura_range_override_ft together"
            )
        if has_expands:
            expanded = _base_aura(parameters["aura_expands_feature_id"])
            if expanded is None or expanded.id == definition.id:
                raise ClassFeatureError(
                    f"{definition.id}.aura_expands_feature_id must name another base aura"
                )
            # 扩展链必须收敛：A 覆盖 B、B 又覆盖 A 时，"有效范围"取决于从哪
            # 条开始折叠，任何确定性说法都是假的，因此整条链拒绝加载。
            seen = {definition.id}
            cursor = expanded.id
            while cursor:
                if cursor in seen:
                    raise ClassFeatureError(
                        f"{definition.id}.aura_expands_feature_id forms a cycle"
                    )
                seen.add(cursor)
                following = _base_aura(cursor)
                if following is None:
                    break
                next_id = following.parameters.get("aura_expands_feature_id")
                cursor = next_id if isinstance(next_id, str) and next_id else ""

    @staticmethod
    def _validate_defense_parameters(definition: ClassFeatureDefinition) -> None:
        """Fail closed on the defense-calculation contract.

        The unarmored AC bonus is one ability id, never a stacked expression;
        the two Hit Point knobs are bounded non-negative integers so a typo
        cannot silently become a huge or negative maximum.
        """

        parameters = definition.parameters
        ability = parameters.get("unarmored_ac_ability_bonus")
        if "unarmored_ac_ability_bonus" in parameters and (
            not isinstance(ability, str) or ability not in ABILITY_IDS
        ):
            raise ClassFeatureError(
                f"{definition.id}.unarmored_ac_ability_bonus must name an ability"
            )
        for key in ("max_hp_bonus_first", "max_hp_bonus_per_level_after"):
            value = parameters.get(key)
            if key in parameters and (
                isinstance(value, bool) or not isinstance(value, int)
                or not 0 <= value <= 20
            ):
                raise ClassFeatureError(
                    f"{definition.id}.{key} must be an integer from 0 to 20"
                )

    @staticmethod
    def _validate_overchannel_parameters(definition: ClassFeatureDefinition) -> None:
        """Fail closed on the maximize-damage contract.

        A typo in the slot cap, the self-damage die or the base cost would
        silently change how much a maximized cast costs, so each declared knob
        is range-checked at load time.  Missing knobs are fine: consumers fall
        back to their documented defaults.
        """

        parameters = definition.parameters
        for key, low, high in (
            ("maximum_slot_level", 1, 9),
            ("self_damage_die", 2, 20),
            ("initial_self_damage_dice", 1, 10),
        ):
            value = parameters.get(key)
            if key in parameters and (
                isinstance(value, bool) or not isinstance(value, int)
                or not low <= value <= high
            ):
                raise ClassFeatureError(
                    f"{definition.id}.{key} must be an integer from {low} to {high}"
                )
        scope = parameters.get("usage_scope")
        if "usage_scope" in parameters and scope not in FEATURE_USAGE_SCOPES:
            raise ClassFeatureError(
                f"{definition.id}.usage_scope must be one of "
                f"{sorted(FEATURE_USAGE_SCOPES)}"
            )
