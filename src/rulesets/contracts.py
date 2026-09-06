"""Pure contracts shared by generic and first-party ruleset runtimes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from src.engine.combat_scheduler import SCHEDULER_KINDS


CharacterBuilderMode = Literal["legacy", "guided", "professional"]
CharacterLifecycleMode = Literal["legacy", "rules_aware"]


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
class AuthoritativeIntentHooks(Protocol):
    """Runtime-owned request and projection hooks for authoritative intents."""

    def prepare_intent_submission(
        self, intent: dict[str, Any], requester_id: str, requester_is_gm: bool,
    ) -> dict[str, Any]: ...

    def memory_deltas_from_event_batch(
        self, batch: dict[str, Any], instance: Any,
    ) -> list[dict[str, Any]]: ...


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
class AutomaticIntentRuntime(Protocol):
    """Optional server-owned turn automation for non-player actors."""

    def next_automatic_intent(self, instance: Any) -> dict[str, Any] | None: ...


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
