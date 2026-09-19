"""Explicit, version-aware registry for ruleset runtime implementations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.rulesets.contracts import (
    RulesetBinding,
    RulesetRuntime,
    RulesetRuntimeMetadata,
)


class RulesetRuntimeError(ValueError):
    """Base error for invalid or unavailable runtime bindings."""


class DuplicateRulesetRuntimeError(RulesetRuntimeError):
    pass


class UnknownRulesetRuntimeError(RulesetRuntimeError):
    pass


class IncompatibleRulesetRuntimeError(RulesetRuntimeError):
    pass


class InvalidRulesetBindingError(RulesetRuntimeError):
    pass


def parse_ruleset_binding(template: Mapping[str, Any]) -> RulesetBinding:
    """Parse a rule template binding; missing binding means legacy behavior."""

    raw = template.get("runtime")
    if raw is None:
        return RulesetBinding()
    if isinstance(raw, str):
        runtime_id = raw.strip()
        minimum_version = 1
    elif isinstance(raw, Mapping):
        runtime_id = str(raw.get("id") or "").strip()
        raw_version = raw.get("minimum_version", 1)
        if isinstance(raw_version, bool):
            raise InvalidRulesetBindingError("runtime.minimum_version must be a positive integer")
        try:
            minimum_version = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise InvalidRulesetBindingError(
                "runtime.minimum_version must be a positive integer"
            ) from exc
    else:
        raise InvalidRulesetBindingError("runtime must be a string or object")

    if not runtime_id:
        raise InvalidRulesetBindingError("runtime.id must not be empty")
    if minimum_version < 1:
        raise InvalidRulesetBindingError("runtime.minimum_version must be a positive integer")
    return RulesetBinding(runtime_id=runtime_id, minimum_version=minimum_version)


class RulesetRuntimeRegistry:
    """Resolve explicit runtime IDs without inspecting names or locale text."""

    def __init__(self, runtimes: Iterable[RulesetRuntime] = ()) -> None:
        self._runtimes: dict[str, RulesetRuntime] = {}
        for runtime in runtimes:
            self.register(runtime)

    def register(self, runtime: RulesetRuntime) -> None:
        runtime_id = str(getattr(runtime, "runtime_id", "") or "").strip()
        if not runtime_id:
            raise InvalidRulesetBindingError("runtime implementation must declare runtime_id")
        if runtime_id in self._runtimes:
            raise DuplicateRulesetRuntimeError(f"ruleset runtime already registered: {runtime_id}")
        version = getattr(runtime, "runtime_version", 0)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise InvalidRulesetBindingError(
                f"runtime {runtime_id} must declare a positive integer runtime_version"
            )
        self._runtimes[runtime_id] = runtime

    def get(self, runtime_id: str, *, minimum_version: int = 1) -> RulesetRuntime:
        runtime = self._runtimes.get(str(runtime_id or "").strip())
        if runtime is None:
            raise UnknownRulesetRuntimeError(f"ruleset runtime is not available: {runtime_id}")
        if runtime.runtime_version < minimum_version:
            raise IncompatibleRulesetRuntimeError(
                f"ruleset runtime {runtime.runtime_id} version {runtime.runtime_version} "
                f"does not satisfy minimum version {minimum_version}"
            )
        return runtime

    def resolve(self, template: Mapping[str, Any]) -> RulesetRuntime:
        binding = parse_ruleset_binding(template)
        return self.get(binding.runtime_id, minimum_version=binding.minimum_version)

    def describe(self, template: Mapping[str, Any]) -> RulesetRuntimeMetadata:
        binding = parse_ruleset_binding(template)
        runtime = self.get(binding.runtime_id, minimum_version=binding.minimum_version)
        return RulesetRuntimeMetadata(
            runtime_id=runtime.runtime_id,
            runtime_version=runtime.runtime_version,
            requested_minimum_version=binding.minimum_version,
            capabilities=runtime.capabilities,
        )

    def runtime_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._runtimes))

    def set_adventure_resolver(self, resolver: Any) -> int:
        """Inject the app's unique adventure resolver into every runtime (FIX-02 §4.1).

        组合根（WebAPI）拥有唯一的 AdventureResolver；runtime 通过本入口拿到它，
        不再自己 ``new AdventureBundleLoader``。返回接收该 resolver 的 runtime 数。
        """

        if resolver is None:
            return 0
        attached = 0
        for runtime in self._runtimes.values():
            setter = getattr(runtime, "set_adventure_resolver", None)
            if callable(setter):
                setter(resolver)
                attached += 1
        return attached

    def set_module_content_sources(self, provider: Any) -> int:
        """Inject module catalog sources into every runtime that accepts them.

        FIX-03 §5.2：runtime 拥有 gameplay catalog 真相，组合根只提供模组来源。
        返回接收该 provider 的 runtime 数。
        """

        if provider is None:
            return 0
        attached = 0
        for runtime in self._runtimes.values():
            setter = getattr(runtime, "set_module_content_sources", None)
            if callable(setter):
                setter(provider)
                attached += 1
        return attached
