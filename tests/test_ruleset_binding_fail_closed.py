"""Ruleset writes require a matching saved binding before any state mutation."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import logging

import pytest

from src.engine.game_instance import GameInstance, GameRegistry
from src.engine.modules import ruleset_runtime
from src.rules.rule_system import RuleSystem
from src.rulesets.builtin import build_default_ruleset_registry
from src.rulesets.dnd2024 import advancement_access
from src.webui.services import ruleset_advancement, ruleset_rest
from tests.test_ruleset_runtime_transaction_preflight import live_state


RUNTIME_ID = "core:dnd2024"
MESSAGE = "存档未绑定当前权威规则运行时"
BAD_BINDINGS = [{}, {"id": "core:other", "state_schema_version": 1}]
SERVICE_WRITES = ["rest", "party_rest", "advance", "configure", "grant", "award_xp"]


@pytest.fixture
def context(monkeypatch):
    registry = build_default_ruleset_registry()
    runtime = registry.get(RUNTIME_ID)
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian")
    character = runtime.finalize_character(None, {**preset["draft"], "locale": "en", "name": "Hero"})
    instance = GameInstance(game_key=("web", "binding", "web_bot"), world_id="test", rule_id="dnd2024_srd")
    ruleset_runtime.replace_binding(instance, {"id": RUNTIME_ID, "state_schema_version": 1})
    for uid in ("hero", "ally"):
        instance.players[uid] = {"character_name": uid, "character_sheet": deepcopy(character)}
    rule = RuleSystem({"rule_id": "dnd2024_srd", "runtime": {"id": RUNTIME_ID, "minimum_version": 1}})
    save = AsyncMock()
    monkeypatch.setattr(GameRegistry, "save", save)
    dependencies = dict(
        get_instance=lambda key: instance,
        parse_game_key=lambda key: instance.game_key,
        save_instance=save,
        load_rule_for_game=lambda game: rule,
        ruleset_registry=registry,
    )
    return SimpleNamespace(
        instance=instance, runtime=runtime, registry=registry, rule=rule, save=save,
        rest=ruleset_rest.LiveRulesetRestDependencies(**dependencies),
        advancement=ruleset_advancement.LiveAdvancementDependencies(**dependencies),
    )


async def service_write(context, operation):
    if operation in {"rest", "party_rest"}:
        method = ruleset_rest.resolve_live if operation == "rest" else ruleset_rest.resolve_live_party
        return await method(context.rest, "game", "hero", {
            "rest": "long", "confirm_elapsed_time": True,
            "expected_revision": 0, "operation_id": "binding-rest",
        })
    if operation == "advance":
        return await ruleset_advancement.apply_live(context.advancement, "game", "hero", {
            "choices": {"hp_method": "fixed"}, "expected_revision": 0, "operation_id": "binding-advance",
        })
    return await ruleset_advancement.control_live(context.advancement, "game", {
        "action": operation, "mode": "xp", "authority": "gm", "user_id": "hero", "amount": 300,
    })


def seed_state(instance):
    advancement_access.configure(instance, "xp", "gm")
    advancement_access.grant(instance, "hero", source="gm")
    ruleset_runtime.state(instance)["rest_session"] = {
        "status": "collecting", "rest": "long", "required_uids": ["hero", "ally", "gone"],
        "participants": {}, "started_at": "existing",
    }
    ruleset_runtime.event_ledger(instance).append({"batch_id": "existing", "events": []})


@pytest.mark.parametrize("binding", BAD_BINDINGS, ids=["empty", "mismatch"])
@pytest.mark.parametrize("populated", [False, True], ids=["fresh-state", "existing-state"])
@pytest.mark.parametrize("operation", SERVICE_WRITES)
@pytest.mark.asyncio
async def test_service_writes_reject_without_mutation_or_save(context, binding, populated, operation):
    if populated:
        seed_state(context.instance)
    ruleset_runtime.replace_binding(context.instance, deepcopy(binding))
    before = deepcopy(live_state(context.instance))

    result = await service_write(context, operation)

    assert result == {"ok": False, "code": "RULESET_BINDING_MISMATCH", "error": MESSAGE}
    assert live_state(context.instance) == before
    context.save.assert_not_called()


@pytest.mark.parametrize("operation", SERVICE_WRITES)
@pytest.mark.asyncio
async def test_bound_service_writes_still_succeed_and_save(context, operation):
    seed_state(context.instance)
    result = await service_write(context, operation)
    assert result["ok"] is True
    context.save.assert_awaited_once_with(context.instance)


@pytest.mark.parametrize("binding", BAD_BINDINGS, ids=["empty", "mismatch"])
@pytest.mark.parametrize("engine_name", ["campaign", "combat", "exploration"])
@pytest.mark.parametrize("populated", [False, True], ids=["fresh-state", "existing-state"])
def test_batch_backstops_reject_before_defaults_reduction_or_save(context, binding, engine_name, populated):
    instance, runtime = context.instance, context.runtime
    if populated:
        seed_state(instance)
    if engine_name == "campaign":
        engine = runtime._campaign_engine(instance, "en")
    elif engine_name == "combat":
        engine = runtime._combat_engine(instance, locale="en")
    else:
        engine = runtime._exploration_engine("en")
    ruleset_runtime.replace_binding(instance, deepcopy(binding))
    before = deepcopy(live_state(instance))
    batch = {
        "batch_id": "unbound", "intent_id": "unbound", "expected_version": 0,
        "result_version": 1, "events": [{"type": "intent.submitted"}],
    }

    with pytest.raises(ruleset_runtime.RulesetBindingError, match=MESSAGE) as error:
        engine.apply_batch(instance, batch)

    assert error.value.code == "RULESET_BINDING_MISMATCH"
    assert live_state(instance) == before
    context.save.assert_not_called()


ADVANCEMENT_WRITES = {
    "configure": lambda instance: advancement_access.configure(instance, "xp", "gm"),
    "grant": lambda instance: advancement_access.grant(instance, "hero", source="gm"),
    "award_xp": lambda instance: advancement_access.award_xp(instance, "hero", 300, source="gm"),
    "consume": lambda instance: advancement_access.consume(instance, "hero", 2),
    "reconcile": lambda instance: advancement_access.reconcile_after_level_up(instance, "hero"),
    "ai_rewards": lambda instance: advancement_access.apply_ai_rewards(instance, {"milestone_grants": ["all"]}),
    "restore": lambda instance: advancement_access.restore(instance, {"history": [{"type": "restore"}]}),
    "control": lambda instance: advancement_access.control(instance, {"action": "award_xp", "user_id": "hero", "amount": 300}),
}


@pytest.mark.parametrize("binding", BAD_BINDINGS, ids=["empty", "mismatch"])
@pytest.mark.parametrize("operation", ADVANCEMENT_WRITES)
@pytest.mark.parametrize("populated", [False, True], ids=["fresh-state", "existing-state"])
def test_advancement_backstops_reject_before_defaults_or_save(context, binding, operation, populated):
    if populated:
        seed_state(context.instance)
    ruleset_runtime.replace_binding(context.instance, deepcopy(binding))
    before = deepcopy(live_state(context.instance))

    with pytest.raises(ruleset_runtime.RulesetBindingError, match=MESSAGE) as error:
        ADVANCEMENT_WRITES[operation](context.instance)

    assert error.value.code == "RULESET_BINDING_MISMATCH"
    assert live_state(context.instance) == before
    context.save.assert_not_called()


@pytest.mark.parametrize("binding", BAD_BINDINGS, ids=["empty", "mismatch"])
@pytest.mark.parametrize("signal", ["start", "begin"])
def test_narrative_signal_returns_false_without_mutation_and_warns_once(context, binding, signal, caplog):
    ruleset_runtime.replace_binding(context.instance, deepcopy(binding))
    before = deepcopy(live_state(context.instance))
    with caplog.at_level(logging.WARNING, logger="src.rulesets.dnd2024.runtime"):
        assert context.runtime.apply_narrative_combat_signal(context.instance, signal) is False
    assert live_state(context.instance) == before
    context.save.assert_not_called()
    assert len(caplog.records) == 1
    assert str(context.instance.game_key) in caplog.records[0].message
    assert "RULESET_BINDING_MISMATCH" in caplog.records[0].message


def test_bound_narrative_signal_still_creates_request(context):
    assert context.runtime.apply_narrative_combat_signal(context.instance, "start") is True
    assert ruleset_runtime.state(context.instance)["encounter_request"]["status"] == "pending"


@pytest.mark.parametrize("binding", BAD_BINDINGS, ids=["empty", "mismatch"])
def test_read_entries_do_not_require_binding(context, binding):
    seed_state(context.instance)
    ruleset_runtime.replace_binding(context.instance, deepcopy(binding))
    status = ruleset_advancement.live_status(context.advancement, "game")
    preview = ruleset_advancement.preview_live(context.advancement, "game", "hero", {"choices": {"hp_method": "fixed"}})
    assert status["ok"] is True
    assert preview["ok"] is True
    assert context.runtime.gameplay_view(context.instance)["combat"]["status"] == "none"
    context.save.assert_not_called()


def test_unbound_read_defaults_still_materialize(context):
    ruleset_runtime.replace_binding(context.instance, {})
    assert ruleset_runtime.state(context.instance) == {}
    assert ruleset_advancement.live_status(context.advancement, "game")["ok"] is True
    assert ruleset_runtime.state(context.instance)["advancement"]["mode"] == "milestone"
    assert context.instance.to_dict()["modules"]["ruleset_runtime"] == ruleset_runtime.fresh()
    context.save.assert_not_called()


@pytest.mark.parametrize("modules", [
    None, {}, {"ruleset_runtime": None}, {"ruleset_runtime": []},
    {"ruleset_runtime": {"schema_version": 1}},
    *({"ruleset_runtime": {"schema_version": 1, "binding": binding}}
      for binding in (None, [], "bad", {}, {"id": None}, {"id": 1}, {"id": ""})),
])
def test_binding_helpers_reject_malformed_storage_without_materializing(modules):
    instance = SimpleNamespace(modules=deepcopy(modules))
    before = deepcopy(vars(instance))
    assert ruleset_runtime.bound_runtime_id(instance) == ""
    with pytest.raises(ruleset_runtime.RulesetBindingError, match=MESSAGE):
        ruleset_runtime.require_binding(instance, RUNTIME_ID)
    assert vars(instance) == before


def test_binding_helpers_read_matching_binding_without_repair(context):
    before = deepcopy(live_state(context.instance))
    assert ruleset_runtime.bound_runtime_id(context.instance) == RUNTIME_ID
    ruleset_runtime.require_binding(context.instance, RUNTIME_ID)
    assert live_state(context.instance) == before


# N2: the generic rest service only forwards runtime-declared options.
class _PlainRestRuntime:
    """A ruleset whose rest implementation does not know D&D attunement."""

    def complete_rest(self, rule, character, rest, hit_die_rolls):
        return {"character": {"rest": rest}, "rest": rest, "events": []}


class _DeclaringRestRuntime:
    rest_option_names = frozenset({"attunement"})

    def __init__(self) -> None:
        self.received = None

    def complete_rest(self, rule, character, rest, hit_die_rolls, *, attunement=None):
        self.received = attunement
        return {"character": {"rest": rest}, "rest": rest, "events": []}


def _resolve_rest(runtime, body):
    dependencies = SimpleNamespace(
        load_rule_by_id=lambda rule_id, language="": SimpleNamespace(
            rule_id=rule_id, template=RUNTIME_ID,
        ),
        ruleset_registry=SimpleNamespace(resolve=lambda template: runtime),
    )
    return ruleset_rest.resolve(dependencies, "binding", body)


def test_generic_rest_does_not_forward_attunement_to_other_rulesets() -> None:
    result = _resolve_rest(
        _PlainRestRuntime(),
        {
            "character": {"ruleset_character": {}}, "rest": "short",
            "hit_die_rolls": {}, "attunement": {"attune": "item:ring"},
        },
    )

    assert result["ok"] is True
    assert result["rest"] == "short"


def test_declared_rulesets_still_receive_their_rest_option() -> None:
    runtime = _DeclaringRestRuntime()
    result = _resolve_rest(
        runtime,
        {
            "character": {"ruleset_character": {}}, "rest": "short",
            "hit_die_rolls": {}, "attunement": {"attune": "item:ring"},
        },
    )

    assert result["ok"] is True
    assert runtime.received == {"attune": "item:ring"}


@pytest.mark.parametrize("rest", ["short", "long"], ids=["short-rest", "long-rest"])
@pytest.mark.asyncio
async def test_party_rest_rejects_bad_attunement_before_joining(context, rest):
    before = deepcopy(live_state(context.instance))

    result = await ruleset_rest.resolve_live_party(context.rest, "game", "hero", {
        "rest": rest, "confirm_elapsed_time": True,
        "expected_revision": 0, "operation_id": "early-attunement",
        "attunement": {"attune": "item:not_in_content"},
    })

    assert result["ok"] is False
    assert result["code"] == "REST_NOT_AVAILABLE"
    assert live_state(context.instance) == before
    assert ruleset_runtime.state(context.instance).get("rest_session") is None


def test_the_dnd2024_runtime_declares_its_rest_options() -> None:
    # 这份声明就是服务层的白名单：多一个不多余，少一个就把合法选项挡在门外。
    assert build_default_ruleset_registry().get(RUNTIME_ID).rest_option_names == frozenset(
        {"attunement", "prepared_spells"}
    )
