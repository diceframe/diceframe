"""Bind WebUI world and game lookups to rules and ruleset runtimes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.engine.modules import game_config
from src.engine.world_template import load_world_template as load_local_world_template
from src.rules.loader import RuleBundleLoader
from src.rules.rule_system import RuleSystem, apply_currency_context
from src.webui.services._common import _parse_game_key
from src.webui.services.rules import is_valid_rule_id

if TYPE_CHECKING:
    from src.plugin_host.host import PluginHost
    from src.rulesets.contracts import RulesetRuntime
    from src.rulesets.registry import RulesetRuntimeRegistry

# Agent tools opt in here; the agent layer does not know concrete runtime IDs.
AGENT_RULESET_RUNTIME_IDS = ("core:dnd2024",)


def load_world_template(
    worlds_dir: Path | None, plugins: PluginHost | None, world_id: str, language: str = "",
) -> dict[str, Any] | None:
    if not worlds_dir:
        return None
    data = load_local_world_template(worlds_dir, world_id, language)
    if data:
        return data
    if plugins:
        return plugins.load_world_template(world_id, language)
    return None


def load_rule_by_id(
    rules_dir: Path, plugins: PluginHost | None, rule_id: str, language: str = "",
) -> RuleSystem | None:
    rule_id = (rule_id or "").strip()
    if not rule_id or not is_valid_rule_id(rule_id):
        return None
    core_path = rules_dir / f"{rule_id}.json"
    if core_path.exists():
        return RuleSystem(RuleBundleLoader().load_rule(rules_dir, rule_id, language))
    if plugins:
        item = plugins.contributions.find("rule", rule_id)
        if item:
            localized = plugins.load_rule_template(
                rule_id, language, plugin_id=item.plugin_id,
            )
            if localized:
                return RuleSystem(localized)
            return RuleSystem.load(item.path)
    legacy_path = RuleSystem.path_for(rules_dir, rule_id, language)
    return RuleSystem.load(legacy_path) if legacy_path.exists() else None


def load_rule_for_game(
    instance: Any,
    worlds_dir: Path | None,
    world_loader: Callable[[str, str], dict[str, Any] | None],
    rule_loader: Callable[[str, str], RuleSystem | None],
) -> RuleSystem | None:
    """Prefer the saved rule over the world default; keep the game's currency context."""

    if not instance.world_id or not worlds_dir:
        return None
    language = getattr(instance, "language", "") or ""
    world_data = world_loader(instance.world_id, language)
    if not world_data:
        return None
    language = language or world_data.get("active_locale") or world_data.get("language", "")
    rule_id = str(
        getattr(instance, "rule_id", "")
        or world_data.get("default_rule")
        or "freeform_fantasy"
    )
    rule = rule_loader(rule_id, language)
    return apply_currency_context(
        rule,
        game_config.currency_label(instance),
        game_config.currency_system(instance),
    )


def load_runtime_for_game(
    instance: Any,
    load_rule: Callable[[Any], RuleSystem | None],
    registry: RulesetRuntimeRegistry,
) -> RulesetRuntime | None:
    rule = load_rule(instance)
    return registry.resolve(rule.template) if rule else None


def resolve_ruleset_check(
    load_runtime: Callable[[Any], RulesetRuntime | None],
    instance: Any, uid: str, check: dict[str, Any],
) -> Any:
    runtime = load_runtime(instance)
    hook = getattr(runtime, "resolve_ability_check", None)
    if not callable(hook):
        raise ValueError("this ruleset does not support ability checks")
    return hook(instance, uid, check)


def visible_ruleset_domains(
    get_instance: Callable[[tuple[str, str, str]], Any],
    load_runtime: Callable[[Any], RulesetRuntime | None],
    domains: Mapping[str, str], owner: str, context: dict,
) -> frozenset[str]:
    """A named game only grants an agent domain after GM/player membership is proven."""

    game_key = str((context or {}).get("game_key") or "").strip()
    if not game_key or not isinstance(owner, str) or not owner:
        return frozenset()
    try:
        instance = get_instance(_parse_game_key(game_key))
    except (ValueError, TypeError):
        return frozenset()
    if instance is None:
        return frozenset()
    gm_uid = str(getattr(instance, "gm_uid", "") or "")
    if owner != gm_uid and owner not in (getattr(instance, "players", {}) or {}):
        return frozenset()
    try:
        runtime = load_runtime(instance)
    except Exception:  # noqa: BLE001 - Resolution failure grants no ruleset scope.
        return frozenset()
    runtime_id = str(getattr(runtime, "runtime_id", "") or "")
    domain = domains.get(runtime_id)
    return frozenset({domain}) if domain else frozenset()


def adventure_runtime_adapter(
    load_runtime: Callable[[Any], RulesetRuntime | None], instance: Any, hook: str,
) -> Any:
    """Missing adapters do not authorize adventure gates or reward conversion."""

    try:
        runtime = load_runtime(instance)
    except Exception:  # noqa: BLE001 - An unresolved runtime grants no adapter.
        return None
    getter = getattr(runtime, hook, None)
    if not callable(getter):
        return None
    try:
        return getter(instance)
    except Exception:  # noqa: BLE001
        return None
