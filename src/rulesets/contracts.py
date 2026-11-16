"""Pure contracts shared by generic and first-party ruleset runtimes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol, runtime_checkable


@runtime_checkable
class RunFinaleRuntime(Protocol):
    """Optional final settlement boundary; never creates implicit rewards."""

    def finale_preflight(self, instance: Any) -> list[str]: ...

    def settle_finale(self, instance: Any, receipt_id: str) -> dict[str, Any]: ...


@runtime_checkable
class CurrentSceneRuntime(Protocol):
    """Read-only current story view for formats owned by the rule runtime."""

    def scene_objective_context(self, instance: Any, *, viewer_is_gm: bool) -> dict[str, Any]: ...

from src.engine.combat_scheduler import SCHEDULER_KINDS


CharacterBuilderMode = Literal["legacy", "guided", "professional"]
CharacterLifecycleMode = Literal["legacy", "rules_aware"]


@runtime_checkable
class CheckModePolicyRuntime(Protocol):
    """Runtime owns supported dice modes, their lifetime and public previews."""

    def check_mode_policy(self, instance: Any, rule: Any, uid: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RulesetCapabilities:
    """Feature switches exposed to API clients without leaking runtime classes."""

    experience_profile: str = "generic"
    character_builder: CharacterBuilderMode = "legacy"
    character_lifecycle: CharacterLifecycleMode = "legacy"
    authoritative_intents: bool = False
    deterministic_combat: bool = False
    versioned_state: bool = False
    session_zero: bool = False
    tutorial_coach: bool = False
    narrative_turns: bool = False
    adventure_formats: tuple[str, ...] = ()
    # 通用战斗扩展（Issue 212）：只有规则 runtime 显式声明才会启用；
    # 前端据此展示动作/资源条/行动条，不自行推断。
    combat_action_effects: bool = False
    combat_resource_pools: bool = False
    combat_scheduler: str = ""  # "" | round_robin | initiative | threshold
    # R05 / SK-01：只有规则 runtime 显式声明，宿主才会接受同一行动中的
    # 顺序多检定或组合技能计划。两者是独立能力，不自动互相推导。
    sequential_checks: bool = False
    composite_action: bool = False

    def __post_init__(self) -> None:
        if self.combat_scheduler and self.combat_scheduler not in SCHEDULER_KINDS:
            raise ValueError(f"unknown combat scheduler kind: {self.combat_scheduler!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RulesetBinding:
    """A rule template's explicit request for a runtime implementation."""

    runtime_id: str = "core:legacy"
    minimum_version: int = 1


@dataclass(frozen=True, slots=True)
class RulesetRuntimeMetadata:
    """JSON-safe description of the runtime selected for a rule template."""

    runtime_id: str
    runtime_version: int
    requested_minimum_version: int
    capabilities: RulesetCapabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.runtime_id,
            "version": self.runtime_version,
            "requested_minimum_version": self.requested_minimum_version,
            "capabilities": self.capabilities.to_dict(),
        }


@runtime_checkable
class RulesetRuntime(Protocol):
    """Boundary implemented by legacy and professional ruleset runtimes.

    Web-specific request objects and UI component details must never cross this
    contract. Methods intentionally exchange plain, JSON-compatible data.
    """

    runtime_id: str
    runtime_version: int
    capabilities: RulesetCapabilities

    def describe_experience(self, rule: Any, locale: str) -> dict[str, Any]: ...

    def builder_choices(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]: ...

    def validate_character(self, rule: Any, draft: dict[str, Any]) -> list[str]: ...

    def derive_character(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]: ...

    def finalize_character(self, rule: Any, draft: dict[str, Any]) -> dict[str, Any]: ...

    def normalize_character_submission(
        self, rule: Any, character: dict[str, Any], locale: str = "",
    ) -> dict[str, Any]: ...

    def available_intents(self, instance: Any, actor_id: str) -> list[dict[str, Any]]: ...

    def validate_intent(self, instance: Any, intent: dict[str, Any]) -> dict[str, Any]: ...

    def resolve_intent(self, instance: Any, intent: dict[str, Any], rng: Any) -> dict[str, Any]: ...

    def apply_event_batch(self, instance: Any, batch: dict[str, Any]) -> dict[str, Any]: ...

    def gameplay_view(
        self, instance: Any, viewer_id: str = "", viewer_is_gm: bool = False,
    ) -> dict[str, Any]: ...

    def build_llm_view(self, instance: Any) -> dict[str, Any]: ...

    def project_legacy_character(self, character: dict[str, Any]) -> dict[str, Any]: ...

    def migrate_state(self, payload: dict[str, Any], from_version: int) -> dict[str, Any]: ...


@runtime_checkable
class VersionedStateRuntime(Protocol):
    """Required when capabilities.versioned_state is true.

    Return the character rule_binding shape: rule_id, runtime_id,
    runtime_version, content_version, and state_schema_version. New games bind
    this identity before any ruleset state is written.
    """

    def game_binding(self, rule: Any, locale: str) -> dict[str, Any]: ...


@runtime_checkable
class AuthoritativeIntentHooks(Protocol):
    """Runtime-owned request and projection hooks for authoritative intents."""

    def prepare_intent_submission(
        self, intent: dict[str, Any], requester_id: str, requester_is_gm: bool,
    ) -> dict[str, Any]: ...

    def memory_deltas_from_event_batch(
        self, batch: dict[str, Any], instance: Any,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class SequentialActionRuntime(Protocol):
    """Optional owner for R05 multi-step action plans.

    The host owns plan shape, dependency ordering, staged publication and
    replay identity.  A ruleset owns the semantic preflight for resources,
    cooldowns, action economy and phases, then resolves each step into its
    normal EventBatch contract.  A failed check is a successful resolution
    with ``success=False``; a rejected step must return ``ok=False`` and never
    publish a partial plan.
    """

    def validate_sequential_plan(
        self, instance: Any, plan: dict[str, Any],
        requester_id: str, requester_is_gm: bool,
    ) -> dict[str, Any]: ...

    def resolve_sequential_step(
        self, instance: Any, step: dict[str, Any], rng: Any,
    ) -> dict[str, Any]: ...

    def apply_event_batch(
        self, instance: Any, batch: dict[str, Any],
    ) -> dict[str, Any]: ...


@runtime_checkable
class NarrativeHealthRuntime(Protocol):
    """Optional owner for HP changes proposed through generic narration.

    Preflight runs before generic player mutations. Settlement receives the
    bounded net HP delta, then repairs the legacy projection from authoritative
    state and returns the delta actually accepted by the ruleset.
    """

    def preflight_narrative_health(self, instance: Any) -> None: ...

    def apply_narrative_hp_delta(self, instance: Any, user_id: str, delta: int) -> int: ...


@runtime_checkable
class NarrativeStatePolicyRuntime(Protocol):
    """Optional adapter for filtering generic LLM state before it is applied."""

    def filter_narrative_state_update(
        self, instance: Any, update: dict[str, Any],
    ) -> dict[str, Any]: ...


@runtime_checkable
class NarrativeCombatSignalRuntime(Protocol):
    """Optional bridge from a resolved narrative turn to structured combat UI."""

    def apply_narrative_combat_signal(
        self, instance: Any, signal: str, proposal: dict[str, Any] | None = None,
    ) -> bool: ...


@runtime_checkable
class NarrativeCheckPolicyRuntime(Protocol):
    """Optional policy for actions whose checks belong to a structured runtime."""

    def deferred_narrative_check_action_ids(self, instance: Any) -> list[str]: ...


@runtime_checkable
class NarrativeAdvancementRuntime(Protocol):
    """Optional bridge for ruleset-owned narrative advancement policy."""

    def narrative_advancement_prompt(self, instance: Any, locale: str) -> str: ...

    def apply_narrative_advancement_rewards(
        self, instance: Any, update: dict[str, Any],
    ) -> list[str]: ...


@runtime_checkable
class NarrativeDirectorRuntime(Protocol):
    """Optional read-only director projection for a ruleset runtime."""

    def director_proposal(
        self, instance: Any, campaign: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class GameDetailProjectionRuntime(Protocol):
    """Optional read-only fields contributed to the generic game detail view."""

    def game_detail_projection(self, instance: Any) -> dict[str, Any]: ...


@runtime_checkable
class IntentResultProjectionRuntime(Protocol):
    """Optional viewer projection of an authoritative intent/automation payload.

    Raw ``apply_event_batch`` results may carry GM-only state (for example full
    stat blocks); the runtime decides what a non-GM viewer may receive.
    """

    def project_intent_result(
        self, instance: Any, result: dict[str, Any], viewer_id: str, viewer_is_gm: bool,
    ) -> dict[str, Any]: ...


@runtime_checkable
class PlayerJoinRuntime(Protocol):
    """Optional hook for rulesets that must enroll a new player in live state."""

    def on_player_join(self, instance: Any, user_id: str) -> None: ...


@runtime_checkable
class CharacterRevivalRuntime(Protocol):
    """Optional hook for rulesets that own canonical revival state."""

    def on_character_revived(self, instance: Any, user_id: str) -> None: ...


@runtime_checkable
class CharacterDeathStateRuntime(Protocol):
    """Optional read capability for canonical death state checks."""

    def character_needs_revival(self, instance: Any, user_id: str) -> bool: ...


@runtime_checkable
class LiveAdvancementPolicyRuntime(Protocol):
    """Optional lifecycle hooks for a ruleset-owned live advancement policy."""

    def configure_live_advancement(
        self, instance: Any, mode: str, authority: str,
    ) -> dict[str, Any]: ...

    def live_advancement_policy(self, instance: Any) -> dict[str, Any]: ...


@runtime_checkable
class LiveAdvancementTransactionRuntime(Protocol):
    """Optional ruleset-owned entitlement and control policy for live advancement."""

    def live_advancement_status(self, instance: Any) -> dict[str, Any]: ...

    def validate_live_advancement(
        self, instance: Any, user_id: str, target_level: int,
    ) -> None: ...

    def snapshot_live_advancement(self, instance: Any) -> Any: ...

    def consume_live_advancement(
        self, instance: Any, user_id: str, target_level: int,
    ) -> None: ...

    def reconcile_live_advancement(self, instance: Any, user_id: str) -> None: ...

    def restore_live_advancement(self, instance: Any, snapshot: Any) -> None: ...

    def apply_live_advancement_control(
        self, instance: Any, command: dict[str, Any],
    ) -> dict[str, Any]: ...


@runtime_checkable
class NarrativeDirectorAutomationRuntime(Protocol):
    """Optional conversion from a Director proposal to an authoritative intent."""

    def director_automatic_intent(
        self, instance: Any, proposal: dict[str, Any],
    ) -> dict[str, Any] | list[dict[str, Any]] | None: ...


@runtime_checkable
class NarrativeDirectorPlanningRuntime(Protocol):
    """Optional semantic planning step owned by a ruleset runtime."""

    async def plan_director_turn(
        self, instance: Any, llm_client: Any,
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class TemporaryEncounterPlannerRuntime(Protocol):
    """Optional read-only AI proposal for a temporary free encounter.

    The proposal never touches persisted state; the GM confirms and the
    authoritative combat.start validation still applies.
    """

    async def plan_temporary_encounter(
        self, instance: Any, llm_client: Any,
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class AutomaticIntentRuntime(Protocol):
    """Optional server-owned turn automation for non-player actors."""

    def next_automatic_intent(self, instance: Any) -> dict[str, Any] | None: ...


@runtime_checkable
class HostedIntentChoiceRuntime(Protocol):
    """Ruleset-owned finite, validated operations for a hosted player seat."""

    def hosted_intent_choices(self, instance: Any, seat_uid: str) -> list[dict[str, Any]]: ...

    def hosted_intent_context(self, instance: Any, seat_uid: str) -> dict[str, Any]: ...


@runtime_checkable
class AdventureBindingMigrationRuntime(Protocol):
    """Optional compatibility boundary for persisted adventure bindings."""

    def migrate_adventure_binding(
        self, instance: Any, expected: dict[str, Any],
    ) -> bool | None: ...


@runtime_checkable
class RunLifecycleRuntime(Protocol):
    """Optional ruleset-owned initialization for a fresh playable run."""

    def initialize_new_run(
        self, instance: Any, *, preserve_characters: bool,
    ) -> None: ...


@runtime_checkable
class PublicTimelineProjectionRuntime(Protocol):
    """Optional ruleset-owned projection into the shared public story feed."""

    def is_public_story_milestone(self, batch: dict[str, Any]) -> bool: ...

    def public_timeline_projection(
        self, batch: dict[str, Any], locale: str,
    ) -> dict[str, str]: ...


# 只描述"哪一类角色事实动过"，不描述动成了什么。Ruleset 收到提示后自行重读
# 当前权威角色状态，因此 live state 始终是唯一 authority，projection 不会退化
# 成第二份业务事实。第一版只有 equipment / inventory 会被实际发出。
CHARACTER_MUTATION_DOMAINS: frozenset[str] = frozenset({
    "equipment", "inventory", "resources",
})


@runtime_checkable
class CharacterStateReconciliationRuntime(Protocol):
    """Optional hook: re-derive ruleset-owned projections from live character state.

    ``changed_domains`` is a hint drawn from :data:`CHARACTER_MUTATION_DOMAINS`,
    never a payload.  It names the kind of live fact that moved and nothing
    else; the runtime re-reads the authoritative character sheet itself.  That
    keeps the generic layer free of rule knowledge and stops the projection
    from becoming a second, separately maintained source of truth.

    Implementations must be idempotent: reconciling twice without an
    intervening live mutation must not rewrite canonical state, bump a
    revision, or append an operation log entry.  Return ``None`` when the
    projection was already current.
    """

    def reconcile_character_state(
        self,
        instance: Any,
        user_id: str,
        changed_domains: frozenset[str],
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class CharacterItemPreparationRuntime(Protocol):
    """Optional hook: canonicalize owned item rows *before* a generic mutation.

    Reconciliation runs after the mutation and can only describe the result.  A
    generic equip, however, decides which slot to replace from the row it is
    given; an old save whose rows carry no ruleset metadata therefore lands in
    the wrong slot and evicts equipment that the later reconciliation cannot put
    back.  This hook exists so the ruleset can establish canonical identity and
    placement first, while the generic layer still knows nothing about any
    specific rule.

    ``item_names`` are the display names this mutation is about to equip; the
    runtime re-reads the authoritative character sheet itself.  Implementations
    must be safe to call with names that are absent, already canonical, or
    unresolvable -- unresolvable rows are left exactly as they are.
    """

    def prepare_owned_items(
        self,
        instance: Any,
        user_id: str,
        item_names: frozenset[str],
    ) -> None: ...
