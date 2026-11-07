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
    ClassResourceDeclaration,
    Dnd2024ClassResourceCatalog,
    ResourceDefinition,
    resource_state,
)


def _humanize(feature_id: str) -> str:
    return str(feature_id).replace("_", " ").strip().title()


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
        """Every declared feature the character owns, in catalog order."""

        state = self.class_state(character)
        if state is None:
            return ()
        class_id, level = state
        granted = self._granted_feature_ids(class_id, level) | self._subclass_feature_ids(
            class_id, level,
        )
        owned: list[ClassFeatureDefinition] = []
        ordered = list(self.catalog.by_class.get(class_id, ()))
        # Feature ids are canonical and global, while a class table may grant a
        # feature declared under another class (Weapon Mastery is one such
        # shared feature).  Ownership therefore follows the progression grant;
        # the catalog only has to declare the id once.
        for feature_id in sorted(granted):
            if feature_id in self.catalog.features and feature_id not in ordered:
                ordered.append(feature_id)
        for feature_id in ordered:
            definition = self.catalog.features.get(feature_id)
            if definition is None or definition.minimum_level > level:
                continue
            if definition.option_of:
                # 选项特性（例如 Monk's Focus 的三个用法）只有在授予它的
                # 特性已经被职业表授予时才算拥有。
                if definition.option_of not in granted:
                    continue
            elif feature_id not in granted:
                continue
            owned.append(definition)
        return tuple(owned)

    def has_feature(
        self, character: Mapping[str, Any] | None, feature_id: str,
    ) -> bool:
        wanted = str(feature_id or "")
        if not wanted:
            return False
        return any(item.id == wanted for item in self.features_for(character))

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
            granted = self._granted_feature_ids(class_id, level) | self._subclass_feature_ids(
                class_id, level,
            )
            for feature_id in granted:
                definition = self.catalog.features.get(feature_id)
                if definition is None:
                    continue
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
    ) -> Any:
        """One scalar/table parameter of an owned feature.

        A parameter named ``<key>_track`` resolves against the class progression
        table, so a level-derived value (the Martial Arts die) is read from the
        same bundle table that advancement uses instead of being copied here.
        An optional ``<key>_format`` template renders the table value into its
        printable form (``1d{die}``); it is a single placeholder, never an
        expression.
        """

        definition = next(
            (item for item in self.features_for(character) if item.id == str(feature_id)),
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
        """The smallest numeric value of one parameter across owned features.

        Crit threshold and other "best value wins" primitives read through this
        method, so a class never hardcodes a subclass feature id and two owned
        declarations (19 and 18) resolve to the more generous 18 instead of
        stacking.
        """

        name = str(key)
        found: int | None = None
        for definition in self.features_for(character):
            value = definition.parameters.get(name)
            if isinstance(value, bool) or not isinstance(value, int):
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
        """

        abilities: list[str] = []
        for definition in self.features_for(character):
            ability = definition.parameters.get("unarmored_ac_ability_bonus")
            if isinstance(ability, str) and ability and ability not in abilities:
                abilities.append(ability)
        return tuple(abilities)

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
                name=self.catalog.label(definition.id, _humanize(definition.id)),
                summary=definition.summary,
                source_ref=definition.source_ref,
                minimum_level=definition.minimum_level,
                values=values,
                active=active,
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
        """Every class resource declared by the features the character owns."""

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

    def _granted_feature_ids(self, class_id: str, level: int) -> frozenset[str]:
        if self.progression is None:
            return frozenset()
        progression = self.progression.classes.get(str(class_id))
        if progression is None:
            return frozenset()
        granted: set[str] = set()
        for row in progression["features_by_level"][:level]:
            granted.update(str(feature_id) for feature_id in row)
        return frozenset(granted)

    def _subclass_feature_ids(self, class_id: str, level: int) -> frozenset[str]:
        """Subclass features granted up to ``level`` by the representative subclass.

        Ownership is derived from ``(class_id, level)`` only: the SRD bundle
        guarantees exactly one representative subclass per class, and the
        advancement chain forces that subclass at the level it is introduced.
        Consequently a directly built sheet without ``features.subclass_ref``
        owns the same features as one advanced through the real path.
        """

        if self.progression is None:
            return frozenset()
        subclass = self.progression.subclasses.get(str(class_id))
        if not subclass:
            return frozenset()
        granted: set[str] = set()
        for raw_level, feature_ids in subclass["feature_ids_by_level"].items():
            if 1 <= int(raw_level) <= level:
                granted.update(str(feature_id) for feature_id in feature_ids)
        return frozenset(granted)

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
        for definition in self.catalog.features.values():
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
            subclass = progression_catalog.subclasses.get(definition.class_id)
            if subclass is not None:
                for raw_level, feature_ids in subclass["feature_ids_by_level"].items():
                    # 声明等级与子职表授予等级漂移即拒绝：不允许"声明 3 级、
                    # 实际 7 级才给"的静默错位。
                    if definition.id in feature_ids and int(raw_level) != definition.minimum_level:
                        raise ClassFeatureError(
                            f"{definition.id}.minimum_level must match its subclass grant level"
                        )
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
            or not 2 <= threshold <= 19
        ):
            raise ClassFeatureError(
                f"{definition.id}.death_save_crit_threshold must be an integer from 2 to 19"
            )
        base = parameters.get("turn_start_regen_base")
        if "turn_start_regen_base" in parameters and (
            isinstance(base, bool) or not isinstance(base, int) or base < 0
        ):
            raise ClassFeatureError(
                f"{definition.id}.turn_start_regen_base must be a non-negative integer"
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

    @staticmethod
    def _validate_defeat_trigger_parameters(definition: ClassFeatureDefinition) -> None:
        """Fail closed on the defeat-trigger payload.

        The payload is a closed set: the temp-HP ability code plus two optional
        knobs (a class-level flag and a share radius).  A payload that declares
        any of them without the ability would silently resolve to nothing, so it
        is rejected at load time instead of becoming an inert feature.
        """

        parameters = definition.parameters
        keys = (
            "defeat_temp_hp_ability",
            "defeat_temp_hp_add_class_level",
            "defeat_share_range_ft",
        )
        if not any(key in parameters for key in keys):
            return
        ability = parameters.get("defeat_temp_hp_ability")
        if not isinstance(ability, str) or ability not in ABILITY_IDS:
            raise ClassFeatureError(
                f"{definition.id}.defeat_temp_hp_ability must name an ability"
            )
        flag = parameters.get("defeat_temp_hp_add_class_level")
        if "defeat_temp_hp_add_class_level" in parameters and not isinstance(flag, bool):
            raise ClassFeatureError(
                f"{definition.id}.defeat_temp_hp_add_class_level must be a boolean"
            )
        share = parameters.get("defeat_share_range_ft")
        if "defeat_share_range_ft" in parameters and (
            isinstance(share, bool) or not isinstance(share, int) or not 1 <= share <= 120
        ):
            raise ClassFeatureError(
                f"{definition.id}.defeat_share_range_ft must be an integer from 1 to 120"
            )

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

        A typo in the slot cap or the self-damage dice would silently change
        how much a maximized cast costs, so each declared knob is range-checked
        at load time.  Missing knobs are fine: consumers fall back to their
        documented defaults.
        """

        parameters = definition.parameters
        for key, low, high in (
            ("max_slot_level", 1, 9),
            ("cost_dice_per_slot", 1, 10),
            ("cost_die_sides", 2, 20),
        ):
            value = parameters.get(key)
            if key in parameters and (
                isinstance(value, bool) or not isinstance(value, int)
                or not low <= value <= high
            ):
                raise ClassFeatureError(
                    f"{definition.id}.{key} must be an integer from {low} to {high}"
                )
        damage_type = parameters.get("cost_damage_type")
        if "cost_damage_type" in parameters and (
            not isinstance(damage_type, str) or not damage_type
        ):
            raise ClassFeatureError(
                f"{definition.id}.cost_damage_type must name a damage type"
            )
