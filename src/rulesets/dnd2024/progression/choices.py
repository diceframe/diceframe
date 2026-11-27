"""Level-up choice planning and repair for the advancement engine.

The slots this module owns are the ones a level-up (or a repair) must
answer: the advancement-feat / ASI slot and the feature-owned option and
skill slots, plus the repair entry point that fills choices a character
still owes.  The engine's public entry points (``preview_next_level``,
``apply_next_level``, ``missing_choices``) stay in ``advancement`` and
reach these methods through ``self``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from src.rulesets.dnd2024.character.builder import ABILITY_IDS, ability_modifier
from src.rulesets.dnd2024.features import Dnd2024ClassFeatureResolver
from src.rulesets.dnd2024.progression.catalog import ProgressionCatalogError

if TYPE_CHECKING:
    from src.rulesets.bundle import LoadedRulesetBundle
    from src.rulesets.dnd2024.features.feat_models import FeatDefinition
    from src.rulesets.dnd2024.progression.catalog import Dnd2024ProgressionCatalog


# 一个特性选项槽的规范化输入只接受这四个字段，多余字段一律拒绝。
FEATURE_CHOICE_KEYS = frozenset({"owner", "feature_id", "choice_id", "selected_refs"})

# 纯 ASI 走的是一条与专长相同的代码路径：属性值提升被声明成一条伪专长，
# 让「占专长槽」只有一种实现。它不是角色拥有的专长，账本据此把它记成
# ability_score_increase，而不是 feat_ref——否则审计会去要一个从未选过的专长。
PLAIN_ABILITY_FEAT_REF = "feat:ability_score_improvement"


def _canonical(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("ruleset_character")
    return nested if isinstance(nested, dict) else payload


def _class_id(class_ref: str) -> str:
    prefix = "class:"
    value = str(class_ref or "")
    return value[len(prefix):] if value.startswith(prefix) else ""


class AdvancementChoicesMixin:
    """The choice-planning half of ``Dnd2024AdvancementEngine``.

    Engine state and the public entry points live on the engine class;
    this mixin only contributes methods, resolved through the MRO.
    """

    __slots__ = ()

    if TYPE_CHECKING:
        # Supplied by ``Dnd2024AdvancementEngine``; no runtime state here.
        bundle: LoadedRulesetBundle
        catalog: Dnd2024ProgressionCatalog
        advancement_feats: dict[str, FeatDefinition]
        advancement_feat_labels: dict[str, str]
        _feature_resolver: Dnd2024ClassFeatureResolver
        def missing_choices(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...
        def _single_class_state(self, character: dict[str, Any]) -> tuple[str, int]: ...
        @staticmethod
        def _pending_subclass_level(
            feature_state: dict[str, Any], level: int, subclass: dict[str, Any],
        ) -> int | None: ...
        @staticmethod
        def _ledger_entries(character: dict[str, Any]) -> list[dict[str, Any]] | None: ...
        def _update_derived(self, character: dict[str, Any]) -> None: ...

    def repair_choices(
        self,
        payload: dict[str, Any],
        repairs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fill the choices this character still owes, without changing level.

        Repair is not a second upgrade path.  It never moves the level, and it
        only accepts ids that ``missing_choices`` actually reported -- a stray
        key is an error rather than a field to ignore, the same rule the
        upgrade preview applies to unknown choice keys.

        Hit points are recomputed from the two things that can move them here,
        and neither is "one level's worth":

        - a subclass's declared maximum, which is an absolute value at the
          current level, not a per-level increment;
        - a constitution increase, which pays back over the levels already
          earned, exactly as an upgrade-time increase does.
        """

        character = deepcopy(_canonical(payload))
        repairs = deepcopy(repairs or {})
        unknown = sorted(set(repairs) - {"subclass_ref", "advancements"})
        if unknown:
            raise ProgressionCatalogError(
                "unknown repair keys: " + ", ".join(unknown)
            )
        class_ref, level = self._single_class_state(character)
        feature_state = character.get("features")
        feature_state = feature_state if isinstance(feature_state, dict) else {}
        features = character.setdefault("features", {})
        subclass = self.catalog.subclass_choice(class_ref)
        expected_subclass_ref = f"subclass:{subclass['id']}"
        pending = self.missing_choices(character)
        pending_ids = {row["id"] for row in pending}
        errors: list[str] = []

        # 一个等级在一次请求里只能被补齐一次。缺项是按等级算的，条目却是
        # 按条应用的：同一级提交两条，两条都能通过缺项判定，各自加一遍属性。
        # 这不是幂等能挡住的问题——`operation_id` 幂等的是整个请求。
        # 所以在动任何数据之前整单拒绝，不留「前缀已应用」的中间态。
        raw_advancements = repairs.get("advancements")
        advancement_repairs = raw_advancements if raw_advancements is not None else []
        if not isinstance(advancement_repairs, list):
            raise ProgressionCatalogError("advancements must be a list of repairs")
        seen_repair_levels: set[int] = set()
        for entry in advancement_repairs:
            if not isinstance(entry, dict):
                continue
            raw_level = entry.get("level")
            if isinstance(raw_level, bool) or not isinstance(raw_level, int):
                continue
            if raw_level in seen_repair_levels:
                raise ProgressionCatalogError(
                    "advancement repair level is submitted more than once: "
                    f"{raw_level}"
                )
            seen_repair_levels.add(raw_level)

        resolver = Dnd2024ClassFeatureResolver(self.bundle)
        hp_before = resolver.feature_max_hp_bonus(character)
        abilities = character.get("abilities")
        abilities = abilities if isinstance(abilities, dict) else {}
        con_before = ability_modifier(int(abilities.get("con", 0) or 0))

        grant_level = self._pending_subclass_level(feature_state, level, subclass)
        entries = self._ledger_entries(character)
        progression = character.get("progression")
        progression = progression if isinstance(progression, dict) else {}
        history = progression.get("history")
        history = history if isinstance(history, list) else None
        repaired_anything = False

        chosen_subclass = repairs.get("subclass_ref")
        if chosen_subclass is not None:
            value = str(chosen_subclass)
            if "subclass_ref" not in pending_ids:
                errors.append("subclass_ref is not missing from this character")
            elif value != expected_subclass_ref:
                errors.append("subclass_ref is not an SRD subclass for this class")
            else:
                features["subclass_ref"] = value
                repaired_anything = True
                if entries is not None and history is not None and grant_level is not None:
                    self._merge_history_choice(history, grant_level, subclass_ref=value)

        for entry in advancement_repairs:
            if not isinstance(entry, dict):
                errors.append("advancement repairs must be objects")
                continue
            raw_level = entry.get("level")
            if isinstance(raw_level, bool) or not isinstance(raw_level, int):
                errors.append("an advancement repair needs an integer level")
                continue
            if not 1 <= raw_level <= level:
                errors.append(
                    f"advancement repair level is out of range: {raw_level}"
                )
                continue
            if not any(
                row["id"] == "ability_score_increases" and row["level"] == raw_level
                for row in pending
            ):
                errors.append(f"level {raw_level} owes no ability increase")
                continue
            snapshot = self.catalog.snapshot(class_ref, raw_level)
            increases, feat_ref, feat_errors = self._advancement_feat(
                character, snapshot, entry
            )
            errors.extend(feat_errors)
            if feat_errors:
                continue
            for ability, points in increases.items():
                abilities[ability] = int(abilities.get(ability, 0) or 0) + int(points)
            repaired_anything = True
            epic = "epic_boon" in snapshot["gained_feature_ids"]
            # The feat slot is a fact about the character, whether or not any
            # ledger is being maintained, so it must not live inside the
            # ledger's branch.
            if feat_ref and feat_ref != PLAIN_ABILITY_FEAT_REF:
                owned = features.setdefault("feat_refs", [])
                if feat_ref not in owned:
                    owned.append(feat_ref)
            if entries is not None and history is not None:
                self._merge_history_choice(
                    history,
                    raw_level,
                    increases=dict(increases) if increases else None,
                    feat_ref=feat_ref,
                    epic=epic,
                )

        if not errors and not repaired_anything:
            # A repair that fills nothing is a caller bug, not a success: the
            # card keeps the debt, and the HTTP layer would bump the revision
            # and invalidate every other client's optimistic lock for nothing.
            errors.append("no outstanding choice was filled")

        if errors:
            raise ProgressionCatalogError("; ".join(errors))

        if repaired_anything:
            hp_after = resolver.feature_max_hp_bonus(character)
            con_after = ability_modifier(int(abilities.get("con", 0) or 0))
            hp_gain = (hp_after - hp_before) + level * (con_after - con_before)
            if hp_gain:
                resources = character.setdefault("resources", {})
                resources["max_hp"] = (
                    int(resources.get("max_hp", 0) or 0) + hp_gain
                )
                resources["hp"] = min(
                    int(resources["max_hp"]),
                    int(resources.get("hp", 0) or 0) + hp_gain,
                )
            self._update_derived(character)
        return character

    @staticmethod
    def _merge_history_choice(
        history: list[Any],
        level: int,
        *,
        subclass_ref: str = "",
        increases: dict[str, int] | None = None,
        feat_ref: str = "",
        epic: bool = False,
    ) -> None:
        """Write a repaired choice back into the level's history entry.

        Replay reads ``choices``, so a repair that only touched ``features`` or
        ``abilities`` returns a card that cannot be imported: ``normalize_
        character_submission`` walks the history through level-up, and a level
        whose entry is blank still looks unanswered there.

        A level with no entry can only be filled when it continues the chain.
        Inserting one in the middle would invent a step the character never
        took, which is a different thing from repairing a step it took without
        answering.
        """

        target: dict[str, Any] | None = None
        for item in history:
            if isinstance(item, dict) and int(item.get("to_level", 0) or 0) == level:
                target = item
                break
        if target is None:
            last = max(
                (int(item.get("to_level", 0) or 0) for item in history if isinstance(item, dict)),
                default=0,
            )
            if level != last + 1:
                raise ProgressionCatalogError(
                    f"progression.history has no entry for level {level} and "
                    "cannot record a repair out of order"
                )
            target = {
                "from_level": level - 1,
                "to_level": level,
                "choices": {},
                "diff": {},
                "source_ref": "",
            }
            history.append(target)

        choices = target.setdefault("choices", {})
        if not isinstance(choices, dict):
            raise ProgressionCatalogError(
                "progression.history entry choices must be an object"
            )
        if subclass_ref:
            choices["subclass_ref"] = subclass_ref
        if increases:
            merged = choices.get("ability_score_increases")
            merged = dict(merged) if isinstance(merged, dict) else {}
            for ability, points in increases.items():
                merged[ability] = int(merged.get(ability, 0) or 0) + int(points)
            choices["ability_score_increases"] = merged
        if feat_ref:
            diff = target.setdefault("diff", {})
            if not isinstance(diff, dict):
                raise ProgressionCatalogError(
                    "progression.history entry diff must be an object"
                )
            diff["advancement_feat_ref"] = feat_ref
            # Replay reads `choices`, not `diff`: normalize_character_submission
            # walks the history through apply_next_level, which asks the feat
            # for its ability split. A repair that only wrote `diff` produced a
            # card that could never be imported -- the level looked like a plain
            # ability increase and then failed "must total exactly 2".
            if feat_ref != PLAIN_ABILITY_FEAT_REF:
                choices["epic_boon_ref" if epic else "feat_ref"] = feat_ref

    def _advancement_feat(
        self,
        character: dict[str, Any],
        snapshot: dict[str, Any],
        choices: dict[str, Any],
    ) -> tuple[dict[str, int], str, list[str]]:
        gained_features = snapshot["gained_feature_ids"]
        raw = choices.get("ability_score_increases")
        general = "ability_score_improvement" in gained_features
        epic = "epic_boon" in gained_features
        selected_ref = str(
            choices.get("epic_boon_ref") if epic else choices.get("feat_ref") or ""
        )
        if general and not selected_ref and isinstance(raw, dict):
            selected_ref = "feat:ability_score_improvement"
        errors: list[str] = []
        if not general and not epic:
            if raw:
                errors.append("ability_score_increases is not available at this level")
            if choices.get("feat_ref") or choices.get("epic_boon_ref"):
                errors.append("advancement feat selection is not available at this level")
            return {}, "", errors

        expected_category = "epic_boon" if epic else "general"
        feat_id = selected_ref.removeprefix("feat:") if selected_ref.startswith("feat:") else ""
        feat = self.advancement_feats.get(feat_id)
        if not selected_ref:
            errors.append(f"{'epic_boon_ref' if epic else 'feat_ref'} is required at this level")
            return {}, "", errors
        if feat is None or feat.category != expected_category:
            errors.append(f"{selected_ref} is not an eligible {expected_category} feat")
            return {}, "", errors
        target_level = int(snapshot["level"])
        if target_level < feat.minimum_level:
            errors.append(f"{selected_ref} level prerequisite is not met")
        owned = list(character.get("features", {}).get("feat_refs") or [])
        if selected_ref in owned and not feat.repeatable:
            errors.append(f"{selected_ref} cannot be selected more than once")
        minimums = feat.legacy.get("any_ability_minimum")
        abilities = character.get("abilities") or {}
        if isinstance(minimums, dict) and not any(
            int(abilities.get(ability, 0) or 0) >= int(minimum)
            for ability, minimum in minimums.items()
        ):
            errors.append(f"{selected_ref} ability prerequisite is not met")
        if feat.legacy.get("requires_spellcasting") and not snapshot.get("slot_profile"):
            errors.append(f"{selected_ref} requires Spellcasting")

        ability_choice = feat.legacy.get("ability_choice")
        if not isinstance(ability_choice, dict):
            if raw not in (None, {}):
                errors.append(f"{selected_ref} does not grant an ability increase")
            return {}, selected_ref, errors
        if not isinstance(raw, dict):
            errors.append("ability_score_increases is required at this level")
            return {}, selected_ref, errors
        parsed: dict[str, int] = {}
        allowed = ability_choice.get("allowed")
        allowed_abilities = set(ABILITY_IDS if allowed == "any" else allowed or [])
        points = int(ability_choice.get("points", 0) or 0)
        maximum = int(ability_choice.get("maximum", 20) or 20)
        pattern = str(ability_choice.get("pattern") or "")
        for ability, increase in raw.items():
            if (
                ability not in allowed_abilities
                or isinstance(increase, bool)
                or not isinstance(increase, int)
                or increase not in ({1, 2} if pattern == "2_or_1_1" else {1})
            ):
                errors.append(
                    f"ability_score_increases are not valid for {selected_ref}"
                )
                continue
            if int(abilities.get(ability, 0) or 0) + int(increase) > maximum:
                errors.append(
                    f"ability_score_increases would raise {ability} above {maximum}"
                )
            parsed[ability] = int(increase)
        expected_lengths = {1, 2} if pattern == "2_or_1_1" else {1}
        if sum(parsed.values()) != points or len(parsed) not in expected_lengths:
            errors.append(f"ability_score_increases must total exactly {points}")
        return parsed, selected_ref, errors

    def _plan_feature_choices(
        self,
        character: dict[str, Any],
        class_ref: str,
        target_level: int,
        gained_feature_ids: list[str],
        snapshot: dict[str, Any],
        choices: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        """Validate the selections this level's newly gained features demand.

        Only a feature gained *at this level* may own a slot, so an old slot
        cannot be resubmitted and a future one cannot be pre-filled.  A
        submitted record matching no such slot is an error, never a field to
        silently ignore.  Options outside a feature's executability whitelist
        are still projected, but marked ``effect_not_implemented`` and
        rejected, so a selection that grants nothing cannot look finished.
        """

        errors: list[str] = []
        requirements: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []

        feature_state = character.get("features")
        feature_state = feature_state if isinstance(feature_state, dict) else {}
        subclass_ref = str(feature_state.get("subclass_ref") or "")
        owners = [f"class:{_class_id(class_ref)}"]
        if subclass_ref:
            owners.append(subclass_ref)
        resolver = self._feature_resolver
        declared: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for feature_id in gained_feature_ids:
            for owner in owners:
                key = (owner, str(feature_id))
                if key in seen:
                    continue
                definition = resolver.catalog.features_by_key.get(key)
                if definition is None or definition.choice is None:
                    continue
                seen.add(key)
                declared.append(definition)

        raw_choices = choices.get("feature_choices")
        if raw_choices is None:
            raw_choices = []
        elif not isinstance(raw_choices, list):
            errors.append("feature_choices must be an array")
            raw_choices = []

        submitted: list[dict[str, Any]] = []
        for raw in raw_choices:
            if not isinstance(raw, dict):
                errors.append("each feature choice must be an object")
                continue
            unknown = sorted(set(raw) - FEATURE_CHOICE_KEYS)
            if unknown:
                errors.append(f"feature choice has an unknown field: {unknown[0]}")
                continue
            submitted.append({
                "owner": str(raw.get("owner") or ""),
                "feature_id": str(raw.get("feature_id") or ""),
                "choice_id": str(raw.get("choice_id") or ""),
                "selected_refs": raw.get("selected_refs"),
            })

        matched: set[int] = set()
        expertise = {
            row["skill_ref"] for row in resolver.expertise_skills(character)
        }
        proficient = {
            str(ref) for ref in (character.get("proficiencies") or {}).get("skill_refs") or []
        }
        for definition in declared:
            choice = definition.choice
            if choice is not None and choice.kind == "language":
                # 语言槽与技能槽分开处理：语言不参与任何机械化判定，它只是
                # "这个角色懂什么语言"的权威记录。因此**未作答不阻塞升级**——
                # 强制每个既有游侠在 2 级补两个语言会让他们全部升级失败，而
                # 漏选两个语言不会导致任何错误结算。
                allowed_languages = set(choice.enabled_options) or {
                    f"language:{entity['id']}" for entity in self.bundle.list("language")
                    if entity.get("rarity") == "standard"
                }
                known_languages = {
                    *(str(ref) for ref in ((character.get("proficiencies") or {}).get("language_refs") or [])),
                    *resolver.language_sources(character),
                    *(str(ref) for prior in records for ref in prior["selected_refs"] if str(ref).startswith("language:")),
                }
                options = []
                for entity in self.bundle.list("language"):
                    lang_ref = f"language:{entity['id']}"
                    reasons: list[str] = []
                    if lang_ref not in allowed_languages:
                        reasons.append("not_standard")
                    if lang_ref in known_languages:
                        reasons.append("already_known")
                    options.append({
                        "value": lang_ref,
                        "available": not reasons,
                        "unavailable_reasons": reasons,
                    })
                requirements.append({
                    "id": f"feature_choice:{definition.owner}:{definition.id}:{choice.id}",
                    "kind": "feature_choice",
                    "enforced": False,
                    "required": False,
                    "owner": definition.owner,
                    "feature_id": definition.id,
                    "choice_id": choice.id,
                    "count": choice.count,
                    "options": options,
                })
                slot = [
                    index for index, record in enumerate(submitted)
                    if record["owner"] == definition.owner
                    and record["feature_id"] == definition.id
                    and record["choice_id"] == choice.id
                ]
                if len(slot) > 1:
                    errors.append(
                        f"feature choice {definition.id} was submitted more than once"
                    )
                    matched.update(slot)
                    continue
                if not slot:
                    continue
                matched.add(slot[0])
                record = submitted[slot[0]]
                raw_refs = record["selected_refs"]
                if not isinstance(raw_refs, list) or not all(
                    isinstance(ref, str) for ref in raw_refs
                ):
                    errors.append(
                        f"feature choice {definition.id} selected_refs must be strings"
                    )
                    continue
                if len(raw_refs) != choice.count:
                    errors.append(
                        f"feature choice {definition.id} must select exactly "
                        f"{choice.count} option"
                    )
                    continue
                if len(set(raw_refs)) != len(raw_refs):
                    errors.append(
                        f"feature choice {definition.id} selected_refs repeats an option"
                    )
                    continue
                allowed_languages = set(choice.enabled_options) or {
                    f"language:{entity['id']}" for entity in self.bundle.list("language")
                    if entity.get("rarity") == "standard"
                }
                if any(ref not in allowed_languages for ref in raw_refs):
                    errors.append(
                        f"feature choice {definition.id} selected a non-Standard language"
                    )
                    continue
                known_languages = {
                    *(str(ref) for ref in ((character.get("proficiencies") or {}).get("language_refs") or [])),
                    *resolver.language_sources(character),
                    *(str(ref) for prior in records for ref in prior["selected_refs"] if str(ref).startswith("language:")),
                }
                duplicate_lang = next((ref for ref in raw_refs if ref in known_languages), "")
                if duplicate_lang:
                    errors.append(f"{duplicate_lang} is already known")
                    continue
                records.append({
                    "owner": definition.owner,
                    "feature_id": definition.id,
                    "choice_id": choice.id,
                    "selected_refs": list(raw_refs),
                    "granted_level": target_level,
                    "grants": "",
                })
                continue
            if choice is not None and choice.kind == "skill_proficiency":
                options = self._skill_options(choice, proficient, expertise)
                requirements.append({
                    "id": f"feature_choice:{definition.owner}:{definition.id}:{choice.id}",
                    "kind": "feature_choice",
                "enforced": True,
                    "required": True,
                    "owner": definition.owner,
                    "feature_id": definition.id,
                    "choice_id": choice.id,
                    "count": choice.count,
                    "options": options,
                })
                chosen = self._plan_skill_choice(
                    definition, choice, options, submitted, matched, errors,
                    target_level=target_level, proficient=proficient,
                    expertise=expertise,
                )
                records.extend(chosen)
                if choice.grants == "expertise":
                    expertise.update(
                        str(ref) for record in chosen for ref in record["selected_refs"]
                    )
                elif choice.grants == "proficiency":
                    proficient.update(
                        str(ref) for record in chosen for ref in record["selected_refs"]
                    )
                continue
            if choice is not None and choice.kind == "feature_option":
                # 互斥特性选项（Divine Strike / Potent Spellcasting、Primal
                # Strike / Potent Spellcasting）的候选就是声明里点名的那些
                # 特性，不走专长目录，也不存在"未实现"这一档：写进
                # enabled_options 就等于承诺它已实现。
                options = [
                    {
                        "value": str(ref),
                        "available": True,
                        "unavailable_reasons": [],
                    }
                    for ref in choice.enabled_options
                ]
                requirements.append({
                    "id": f"feature_choice:{definition.owner}:{definition.id}:{choice.id}",
                    "kind": "feature_choice",
                "enforced": True,
                    "required": True,
                    "owner": definition.owner,
                    "feature_id": definition.id,
                    "choice_id": choice.id,
                    "count": choice.count,
                    "options": options,
                })
                slot = [
                    index for index, record in enumerate(submitted)
                    if record["owner"] == definition.owner
                    and record["feature_id"] == definition.id
                    and record["choice_id"] == choice.id
                ]
                if len(slot) > 1:
                    errors.append(
                        f"feature choice {definition.id} was submitted more than once"
                    )
                    matched.update(slot)
                    continue
                if not slot:
                    errors.append(
                        f"feature choice {definition.id} requires a selection at this level"
                    )
                    continue
                matched.add(slot[0])
                raw_refs = submitted[slot[0]]["selected_refs"]
                if not isinstance(raw_refs, list) or not all(
                    isinstance(ref, str) for ref in raw_refs
                ):
                    errors.append(
                        f"feature choice {definition.id} selected_refs must be strings"
                    )
                    continue
                allowed = set(choice.enabled_options)
                picked: list[str] = []
                bad = False
                for ref in raw_refs:
                    candidate = (
                        str(ref) if str(ref).startswith("feature:")
                        else f"feature:{ref}"
                    )
                    if candidate not in allowed:
                        errors.append(
                            f"{ref} is not a declared option of {definition.id}"
                        )
                        bad = True
                        continue
                    if candidate in picked:
                        errors.append(
                            f"feature choice {definition.id} selected_refs repeats an option"
                        )
                        bad = True
                        continue
                    picked.append(candidate)
                if len(picked) != choice.count:
                    if not bad:
                        # 空清单也必须炸：静默放行会让角色"没选"却升级成功，
                        # 之后所有依赖该选项的机械都读成"从未选择"。
                        errors.append(
                            f"feature choice {definition.id} must select exactly "
                            f"{choice.count} option(s)"
                        )
                    continue
                records.append({
                    "owner": definition.owner,
                    "feature_id": definition.id,
                    "choice_id": choice.id,
                    "selected_refs": picked,
                    "granted_level": target_level,
                })
                continue
            enabled = set(choice.enabled_options)
            projected: list[dict[str, Any]] = []
            for option in self._feat_options(
                choice.category, character, target_level, snapshot,
            ):
                if option["value"] in enabled:
                    projected.append(option)
                    continue
                reasons = list(option["unavailable_reasons"])
                if "effect_not_implemented" not in reasons:
                    reasons.append("effect_not_implemented")
                projected.append({**option, "available": False, "unavailable_reasons": reasons})
            requirements.append({
                "id": f"feature_choice:{definition.owner}:{definition.id}:{choice.id}",
                "kind": "feature_choice",
                "enforced": True,
                "required": True,
                "owner": definition.owner,
                "feature_id": definition.id,
                "choice_id": choice.id,
                "count": choice.count,
                "options": projected,
            })

            slot = [
                index for index, record in enumerate(submitted)
                if record["owner"] == definition.owner
                and record["feature_id"] == definition.id
                and record["choice_id"] == choice.id
            ]
            if not slot:
                errors.append(
                    f"feature choice {definition.id} requires a selection at this level"
                )
                continue
            if len(slot) > 1:
                errors.append(
                    f"feature choice {definition.id} was submitted more than once"
                )
                matched.update(slot)
                continue
            matched.add(slot[0])
            raw_refs = submitted[slot[0]]["selected_refs"]
            if not isinstance(raw_refs, list):
                errors.append(
                    f"feature choice {definition.id} selected_refs must be an array"
                )
                continue
            if len(raw_refs) != choice.count:
                errors.append(
                    f"feature choice {definition.id} must select exactly "
                    f"{choice.count} option"
                )
                continue
            if not all(isinstance(ref, str) for ref in raw_refs):
                errors.append(
                    f"feature choice {definition.id} selected_refs must be strings"
                )
                continue
            if len(set(raw_refs)) != len(raw_refs):
                errors.append(
                    f"feature choice {definition.id} selected_refs repeats an option"
                )
                continue
            ref = str(raw_refs[0])
            feat = self.advancement_feats.get(ref.removeprefix("feat:"))
            if not ref.startswith("feat:") or feat is None or (
                feat.category != choice.category
            ):
                errors.append(f"{ref} is not a {choice.category} feat")
                continue
            if ref not in enabled:
                errors.append(f"{ref} cannot be selected: effect_not_implemented")
                continue
            option = next((item for item in projected if item["value"] == ref), None)  # type: ignore[arg-type]
            if option is None or option["unavailable_reasons"]:
                reasons = option["unavailable_reasons"] if option else ["unknown"]
                errors.append(f"{ref} is not available: {', '.join(reasons)}")
                continue
            records.append({
                "owner": definition.owner,
                "feature_id": definition.id,
                "choice_id": choice.id,
                "selected_refs": [ref],
                "granted_level": target_level,
            })

        for index, record in enumerate(submitted):
            if index not in matched:
                errors.append(
                    f"feature choice {record['choice_id']} does not belong to a "
                    "feature gained at this level"
                )
        return requirements, records, errors

    def _skill_options(
        self,
        choice: Any,
        proficient: set[str],
        expertise: set[str],
    ) -> list[dict[str, Any]]:
        """Every skill in this choice's pool, with why one is not pickable.

        The pool is the declaration's own whitelist when it names one (Scholar
        names six) and the whole skill catalog otherwise (Expertise).  Options
        are never filtered out: "you are not proficient in it" and "you already
        have Expertise in it" have to stay visible, or the player cannot tell a
        missing option from a rejected one.
        """

        allowed = set(choice.enabled_options)
        options: list[dict[str, Any]] = []
        for skill in self.bundle.list("skill"):
            ref = f"skill:{skill['id']}"
            if allowed and ref not in allowed:
                continue
            reasons: list[str] = []
            if choice.requires_proficiency and ref not in proficient:
                reasons.append("not_proficient")
            if choice.exclude_expertise and ref in expertise:
                reasons.append("already_expertise")
            options.append({
                "value": ref,
                "name": str(skill.get("name") or skill["id"]),
                "source_ref": str(skill.get("source_ref") or ""),
                "available": not reasons,
                "unavailable_reasons": reasons,
            })
        return options

    def _plan_skill_choice(
        self,
        definition: Any,
        choice: Any,
        options: list[dict[str, Any]],
        submitted: list[dict[str, Any]],
        matched: set[int],
        errors: list[str],
        *,
        target_level: int,
        proficient: set[str],
        expertise: set[str],
    ) -> list[dict[str, Any]]:
        """Validate one skill slot and return the record it commits."""

        slot = [
            index for index, record in enumerate(submitted)
            if record["owner"] == definition.owner
            and record["feature_id"] == definition.id
            and record["choice_id"] == choice.id
        ]
        if not slot:
            errors.append(
                f"feature choice {definition.id} requires a selection at this level"
            )
            return []
        if len(slot) > 1:
            errors.append(
                f"feature choice {definition.id} was submitted more than once"
            )
            matched.update(slot)
            return []
        matched.add(slot[0])
        raw_refs = submitted[slot[0]]["selected_refs"]
        if not isinstance(raw_refs, list) or not all(
            isinstance(ref, str) for ref in raw_refs
        ):
            errors.append(f"feature choice {definition.id} selected_refs must be strings")
            return []
        if len(raw_refs) != choice.count:
            errors.append(
                f"feature choice {definition.id} must select exactly {choice.count} option"
                + ("" if choice.count == 1 else "s")
            )
            return []
        if len(set(raw_refs)) != len(raw_refs):
            errors.append(f"feature choice {definition.id} selected_refs repeats an option")
            return []
        by_value = {option["value"]: option for option in options}
        for ref in raw_refs:
            option = by_value.get(ref)
            if option is None:
                errors.append(f"{ref} is not a declared option of {definition.id}")
                continue
            if option["unavailable_reasons"]:
                errors.append(f"{ref} is not available: {', '.join(option['unavailable_reasons'])}")
                continue
            if choice.requires_proficiency and ref not in proficient:
                errors.append(f"{ref} is not a skill proficiency of this character")
                continue
            if choice.exclude_expertise and ref in expertise:
                errors.append(f"{ref} already has Expertise")
                continue
            expertise.add(ref)
        if len([ref for ref in raw_refs if ref in by_value]) != choice.count:
            return []
        if any(
            option["unavailable_reasons"] for option in
            (by_value[ref] for ref in raw_refs if ref in by_value)
        ):
            return []
        return [{
            "owner": definition.owner,
            "feature_id": definition.id,
            "choice_id": choice.id,
            "selected_refs": list(raw_refs),
            "granted_level": target_level,
            # 落地时要区分"加倍"与"新增熟练"：只看 ref 分不出来。
            "grants": choice.grants,
        }]

    def _feat_options(
        self,
        category: str,
        character: dict[str, Any],
        target_level: int,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        abilities = character.get("abilities") or {}
        owned = list(character.get("features", {}).get("feat_refs") or [])
        result: list[dict[str, Any]] = []
        for feat_id, feat in self.advancement_feats.items():
            if feat.category != category:
                continue
            unavailable_reasons: list[str] = []
            if target_level < feat.minimum_level:
                unavailable_reasons.append("level")
            if f"feat:{feat_id}" in owned and not feat.repeatable:
                unavailable_reasons.append("already_owned")
            # 这两条前置仍以旧字段声明（FD2 迁进 ``requires``），只有**位置**
            # 从顶层换到了 ``legacy``，判据语义一字未改。
            minimums = feat.legacy.get("any_ability_minimum")
            if isinstance(minimums, dict) and not any(
                int(abilities.get(ability, 0) or 0) >= int(minimum)
                for ability, minimum in minimums.items()
            ):
                unavailable_reasons.append("ability_prerequisite")
            if feat.legacy.get("requires_spellcasting") and not snapshot.get("slot_profile"):
                unavailable_reasons.append("spellcasting")
            result.append({
                "value": f"feat:{feat_id}",
                "name": self.advancement_feat_labels[feat_id],
                "source_ref": feat.source_ref,
                "available": not unavailable_reasons,
                "unavailable_reasons": unavailable_reasons,
                # 对外投影保持原形：前端读这个键，内部声明改了它也不变。
                "ability_choice": deepcopy(feat.legacy.get("ability_choice") or {}),
            })
        return result
