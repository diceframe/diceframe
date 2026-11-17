"""Composition root for first-party ruleset runtime implementations."""

from pathlib import Path
from src.adventures.compatibility import default_runtime_requirement

from src.rulesets.dnd2024 import Dnd2024Runtime
from src.rulesets.legacy_adapter import LegacyRulesetAdapter
from src.rulesets.registry import RulesetRuntimeRegistry


def default_adventure_runtime_requirement() -> dict[str, int | str]:
    """Return the first-party default for newly authored adventure packages."""

    return default_runtime_requirement()


def default_module_runtime_requirement() -> dict[str, int | str]:
    """Preserve the owner of existing implicit ruleset content catalogues."""
    return {'id': Dnd2024Runtime.runtime_id, 'minimum_version': Dnd2024Runtime.runtime_version}


def build_default_ruleset_registry(
    adventures_dir: str | Path | None = None,
) -> RulesetRuntimeRegistry:
    return RulesetRuntimeRegistry([
        LegacyRulesetAdapter(),
        Dnd2024Runtime(adventures_dir=adventures_dir),
    ])
