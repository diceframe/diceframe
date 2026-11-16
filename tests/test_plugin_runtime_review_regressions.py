"""Creation, replay, and lifecycle contracts through installed real stdio."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import ruleset_runtime
from src.plugin_host.host import PluginHost
from src.rules.rule_system import RuleSystem
from src.rulesets.registry import RulesetRuntimeRegistry, UnknownRulesetRuntimeError
from src.rulesets.runtime_probe import RuntimeProbeError
from src.webui.api import WebAPI
from src.webui.services.game_creation_phases import bind_ruleset_runtime
from test_plugin_ruleset_runtimes import RUNTIME_ID, _make_source, _write_runtime_package


@pytest.mark.asyncio
async def test_installed_runtime_binds_new_game_and_replays_older_batch(tmp_path):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    try:
        await host.install_from_zip(_write_runtime_package(tmp_path, "t3-pack").read_bytes())
        await host.start("t3-pack", require_enabled=False)
        runtime = registry.get(RUNTIME_ID)
        rule = RuleSystem({"rule_id": "t3sample", "runtime": {"id": RUNTIME_ID, "minimum_version": 1}})
        instance = GameInstance(game_key=("web", "review", "host"))

        class Transaction:
            def rollback(self):
                pytest.fail("a valid remote binding must not roll back creation")

        assert bind_ruleset_runtime(Transaction(), instance, runtime, rule, "en") is None
        assert ruleset_runtime.bound_runtime_id(instance) == RUNTIME_ID
        rng = SimpleNamespace(getstate=lambda: ("seed", 1))
        first = runtime.resolve_intent(instance, {"intent_id": "a", "type": "t3.check"}, rng)["event_batch"]
        assert runtime.apply_event_batch(instance, first)["applied"]
        second = runtime.resolve_intent(instance, {"intent_id": "b", "type": "t3.check"}, rng)["event_batch"]
        assert runtime.apply_event_batch(instance, second)["applied"]
        before = deepcopy(instance.modules)
        assert runtime.apply_event_batch(instance, first) == {"ok": True, "applied": False, "replayed": True}
        assert instance.modules == before
        with pytest.raises(RuntimeProbeError, match="different payload"):
            runtime.apply_event_batch(instance, {**first, "events": [{"type": "forged"}]})
        assert instance.modules == before
    finally:
        source.close()


@pytest.mark.asyncio
async def test_source_close_fences_late_host_notifications_and_cleanup(tmp_path):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    registry = RulesetRuntimeRegistry([])
    source = _make_source(registry, host)
    await host.install_from_zip(_write_runtime_package(tmp_path, "t3-pack").read_bytes())
    await host.start("t3-pack", require_enabled=False)
    transport = registry.get(RUNTIME_ID)._probe.transport
    source.close()
    assert transport.process.poll() is not None
    await host.cleanup()
    assert source.sync(host) == {"registered": [], "removed": [], "failed": {}}
    with pytest.raises(UnknownRulesetRuntimeError):
        registry.get(RUNTIME_ID)


@pytest.mark.asyncio
async def test_api_replacement_transfers_source_without_restart(tmp_path):
    host = PluginHost(tmp_path / "plugins", tmp_path / "data")
    registry = RulesetRuntimeRegistry([])
    old = object.__new__(WebAPI)
    new = object.__new__(WebAPI)
    old._ruleset_registry = new._ruleset_registry = registry
    old.attach_plugin_ruleset_runtime_source(host)
    await host.install_from_zip(_write_runtime_package(tmp_path, "t3-pack").read_bytes())
    await host.start("t3-pack", require_enabled=False)
    runtime = registry.get(RUNTIME_ID)
    new.adopt_plugin_ruleset_runtime_source(old)
    old.close_plugin_ruleset_runtime_source()
    assert registry.get(RUNTIME_ID) is runtime
    await host.stop("t3-pack")
    with pytest.raises(UnknownRulesetRuntimeError):
        registry.get(RUNTIME_ID)
    assert runtime._probe.transport.process.poll() is not None
    new.close_plugin_ruleset_runtime_source()
