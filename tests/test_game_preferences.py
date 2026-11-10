"""New-game defaults are snapshots; current-table changes retain authority."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.engine import check_decisions, game_preferences
from src.engine.game_instance import GameInstance, GameState
from src.engine.modules import check_policy, checks
from src.engine.participant_view import Viewer
from src.game_preferences import default_preferences, validate_preferences
from src.rules.rule_system import RuleSystem
from src.webui.config_update import prepare_config_update
from src.webui.services.game_preferences import GamePreferenceDependencies, GamePreferenceService

pytest_plugins = ['tests.webapi_harness']

ROOT = Path(__file__).resolve().parents[1]


def prepared_game():
    inst = GameInstance(('web', 'preferences', 'bot'), gm_uid='gm', state=GameState.ACTIVE_JUDGMENT)
    inst.players = {'a': {'character_name': 'A', 'character_sheet': {'luck': 50}}}
    checks.replace_last_checks(inst, [{'check_id': 'c1', 'actor_uid': 'a', 'kind': 'check',
        'skill': 'Investigate', 'dice': 'd100', 'dice_mechanic': 'coc_bonus_penalty',
        'roll': 60, 'rolls': [60], 'threshold': 40, 'verdict': '失败', 'advantage_mode': '',
        'luck_spend_available': True, 'luck_decision': 'pending'}])
    inst.complete_round_check_preparation()
    rule = RuleSystem.load(ROOT / 'templates/rules/freeform_coc.json')
    check_decisions.prepare(inst, rule)
    return inst, rule


def service_for(inst, rule, save=None):
    save = save or AsyncMock()
    service = GamePreferenceService(GamePreferenceDependencies(
        get_instance=lambda key: inst, save_instance=save,
        load_rule=lambda instance: rule, get_runtime=lambda instance: None,
        get_defaults=lambda: default_preferences(),
    ))
    return service, save


def test_defaults_are_on_and_config_round_trip(tmp_path):
    from src.webui.runtime_config import ConfigStore, RuntimePaths
    store = ConfigStore(RuntimePaths.from_root(tmp_path, {}), {})
    initial = store.load()
    assert initial.state['game_defaults']['push_enabled'] is True
    assert initial.state['game_defaults']['dice_reveal_mode'] == 'auto'
    assert initial.state['game_defaults']['quick_actions_enabled'] is True
    updated = prepare_config_update(initial.state, {'game_defaults': {'push_enabled': False, 'story_mode': 'dialogue'}})
    assert not updated.error
    store.save(updated.state)
    assert store.public_view(store.load())['game_defaults']['push_enabled'] is False


@pytest.mark.parametrize('invalid', [{'push_enabled': 'false'}, {'quick_actions_enabled': 'false'}, {'luck_timeout_seconds': True},
    {'luck_timeout_seconds': -1}, {'story_mode': 'unknown'}, {'dice_reveal_mode': 'no'},
    {'narrative_perspective': 'unknown'}, {'room_password': 'secret'},
    {'advancement_mode': 'unknown'}, {'advancement_authority': 'player'}, None, []])
def test_default_configuration_rejects_invalid_or_non_preference_fields(invalid):
    before = {'game_defaults': default_preferences()}
    snapshot = deepcopy(before)
    result = prepare_config_update(before, {'game_defaults': invalid})
    assert result.error
    assert before == snapshot


def test_off_switch_suppresses_new_push_without_changing_luck_or_dice():
    inst, rule = prepared_game()
    checks.last_checks(inst)[0].pop('push')
    check_policy.replace_push_enabled(inst, False)
    original = deepcopy(checks.last_checks(inst))
    check_decisions.prepare(inst, rule)
    assert checks.last_checks(inst) == original
    assert inst.pending_luck_checks()
    assert check_policy.push_enabled(GameInstance.from_dict(inst.to_dict())) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['pending', 'requested', 'offered', 'resolved'])
async def test_disabling_closes_only_unfinished_push_without_rerolling(phase):
    inst, rule = prepared_game()
    check = checks.last_checks(inst)[0]
    push = check['push']
    push.update(status=phase, reason='New approach', consequence='Noise')
    if phase != 'pending':
        check.update(luck_decision='declined', luck_spend_available=False)
    before = deepcopy(check)
    svc, save = service_for(inst, rule)
    response = await svc.update('web|preferences|bot', Viewer('gm', 'gm'), {'push_enabled': False})
    assert response['ok']
    save.assert_awaited_once()
    current = checks.last_checks(inst)[0]
    assert current['roll'] == before['roll']
    assert current['luck_decision'] == before['luck_decision']
    assert current['push']['status'] == ('resolved' if phase == 'resolved' else 'declined')
    if phase != 'resolved':
        assert current['push']['decision_reason'] == 'push_disabled_by_gm'
    assert not check_decisions.pending(inst)
    await svc.update('web|preferences|bot', Viewer('gm', 'gm'), {'push_enabled': True})
    check_decisions.prepare(inst, rule)
    assert not check_decisions.pending(inst)  # Re-enabling never reopens a closed choice.


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,uid,extra', [
    ('request', 'a', {'reason': 'Use a lamp'}),
    ('offer', 'gm', {'consequence': 'Noise'}),
    ('reject', 'gm', {'reason': 'Not here'}),
    ('decline', 'a', {}),
    ('confirm', 'a', {}),
])
async def test_disabled_table_refuses_every_later_push_operation(operation, uid, extra):
    """#714：关掉开关这条逃生路径不能被一次迟到的 Push 请求重新打开。"""
    from src.webui.services.check_decisions import CheckDecisionDependencies, CheckDecisionService
    inst, rule = prepared_game()
    svc, _ = service_for(inst, rule)
    assert (await svc.update('key', Viewer('gm', 'gm'), {'push_enabled': False}))['ok']
    after_disable = deepcopy(inst.to_dict())
    push = checks.last_checks(inst)[0]['push']
    scope = {'run_id': inst.run_id, 'round_number': inst.round_number, 'expected_revision': push['revision']}

    with pytest.raises(ValueError, match='Push is disabled'):
        check_decisions.change(inst, 'c1', uid, operation, **scope, **extra)
    save = AsyncMock()
    service = CheckDecisionService(CheckDecisionDependencies(lambda _key: inst, save))
    viewer = Viewer('gm', 'gm') if uid == 'gm' else Viewer('seat', 'a')
    refused = await service.update('key', 'c1', viewer, operation, {**scope, **extra})

    assert refused['ok'] is False and refused['code'] == 'PUSH_INVALID_OR_STALE'
    save.assert_not_called()
    assert inst.to_dict() == after_disable


@pytest.mark.asyncio
async def test_disabling_clears_the_waiting_push_and_offers_no_new_one():
    inst, rule = prepared_game()
    checks.last_checks(inst)[0].update(luck_decision='declined', luck_spend_available=False)
    svc, _ = service_for(inst, rule)

    response = await svc.update('key', Viewer('gm', 'gm'), {'push_enabled': False})

    # The response the client renders: nothing left to decide, the round may resume.
    assert response['closed_push_decisions'] == ['c1']
    assert response['pending_push_decisions'] == []
    assert response['phase'] == 'ready' and response['ready_to_resolve'] is True
    # A later failed check of the same judgment is not offered Push while disabled.
    fresh = {k: v for k, v in deepcopy(checks.last_checks(inst)[0]).items() if k != 'push'}
    checks.replace_last_checks(inst, [*checks.last_checks(inst), {**fresh, 'check_id': 'c2'}])
    check_decisions.prepare(inst, rule)
    assert 'push' not in checks.last_checks(inst)[1]
    assert not check_decisions.pending(inst)


@pytest.mark.asyncio
async def test_preference_write_is_gm_only_and_rollback_safe():
    inst, rule = prepared_game()
    before = deepcopy(inst.to_dict())
    svc, save = service_for(inst, rule, AsyncMock(side_effect=OSError('disk full')))
    denied = await svc.update('key', Viewer('seat', 'a'), {'push_enabled': False})
    assert denied['error_code'] == 'GM_ONLY'
    save.assert_not_awaited()
    result = await svc.update('key', Viewer('gm', 'gm'), {'push_enabled': False})
    assert result['error_code'] == 'SAVE_FAILED'
    assert inst.to_dict() == before


def test_preferences_are_independent_of_global_mutation_and_preserve_world_style():
    inst = GameInstance(('web', 'snapshot', 'bot'))
    defaults = validate_preferences({'push_enabled': False, 'gm_style_override': None})
    game_preferences.apply(inst, defaults)
    defaults['push_enabled'] = True
    assert game_preferences.project(inst)['push_enabled'] is False
    assert game_preferences.project(inst)['gm_style_override'] is None
    loaded = GameInstance.from_dict(inst.to_dict())
    assert game_preferences.project(loaded) == game_preferences.project(inst)


@pytest.mark.asyncio
async def test_quick_action_visibility_keeps_content_and_survives_loading_and_resets():
    from src.engine import module_hooks
    from src.engine.modules import round_presentation
    inst, rule = prepared_game()
    module_hooks.record(inst, module_hooks.QUICK_ACTIONS, ['Inspect the lock'])
    svc, save = service_for(inst, rule)
    before = deepcopy(checks.last_checks(inst))
    result = await svc.update('key', Viewer('gm', 'gm'), {'quick_actions_enabled': False})
    assert result['ok'] and result['preferences']['quick_actions_enabled'] is False
    assert module_hooks.read(inst, module_hooks.QUICK_ACTIONS) == ['Inspect the lock']
    assert checks.last_checks(inst) == before
    loaded = GameInstance.from_dict(inst.to_dict())
    assert game_preferences.project(loaded)['quick_actions_enabled'] is False
    round_presentation.begin_round(loaded)
    round_presentation.reset(loaded)
    assert game_preferences.project(loaded)['quick_actions_enabled'] is False
    assert module_hooks.read(loaded, module_hooks.QUICK_ACTIONS) == []
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_factory_defaults_apply_once_to_new_tables_including_non_web_entries(tmp_path):
    from src.commands.game_factory import GameFactory
    from src.engine.game_instance import GameRegistry
    registry = GameRegistry(tmp_path / 'saves')
    factory = GameFactory(registry, None, tmp_path / 'worlds')
    defaults = {'push_enabled': False, 'quick_actions_enabled': False, 'story_mode': 'dialogue'}
    factory.set_defaults_provider(lambda: defaults)
    key = ('qq', 'table', 'bot')
    inst = await factory.create_game(key, 'world', 'World', 'Table')
    assert game_preferences.project(inst)['push_enabled'] is False
    assert game_preferences.project(inst)['story_mode'] == 'dialogue'
    assert game_preferences.project(inst)['quick_actions_enabled'] is False
    defaults['push_enabled'] = True
    defaults['quick_actions_enabled'] = True
    assert await factory.create_game(key, 'world', 'World', 'Table') is inst
    assert game_preferences.project(inst)['push_enabled'] is False
    assert game_preferences.project(inst)['quick_actions_enabled'] is False


@pytest.mark.asyncio
async def test_creation_defaults_allow_explicit_overrides_and_restart_keeps_snapshot(web_api):
    api, _, registry, _, _ = web_api
    api._config_state = {'game_defaults': validate_preferences({'push_enabled': False, 'quick_actions_enabled': False,
        'story_mode': 'dialogue', 'gm_style_override': {'tone': 'dark'}})}
    source = await api.create_game('template_world', 'defaulted', players=[{'character_name': 'A'}])
    assert source['ok']
    inst = registry.get(api._parse_key(source['game_key']))
    assert game_preferences.project(inst)['push_enabled'] is False
    assert game_preferences.project(inst)['story_mode'] == 'dialogue'
    assert game_preferences.project(inst)['quick_actions_enabled'] is False
    explicit = await api.create_game('template_world', 'explicit', story_mode='prose', gm_style_override=None,
                                     players=[{'character_name': 'B'}])
    assert explicit['ok']
    other = registry.get(api._parse_key(explicit['game_key']))
    assert game_preferences.project(other)['story_mode'] == 'prose'
    assert game_preferences.project(other)['gm_style_override'] is None
    api._config_state['game_defaults'] = default_preferences()
    assert (await api.restart_game(source['game_key']))['ok']
    restarted = registry.get(api._parse_key(source['game_key']))
    assert game_preferences.project(restarted)['push_enabled'] is False
    assert game_preferences.project(restarted)['quick_actions_enabled'] is False
    assert game_preferences.project(restarted)['story_mode'] == 'dialogue'
    seeded = await api.create_from_seed(source['seed_code'], players=[{'character_name': 'C'}])
    assert seeded['ok']
    clone = registry.get(api._parse_key(seeded['game_key']))
    assert game_preferences.project(clone)['push_enabled'] is False
    assert game_preferences.project(clone)['quick_actions_enabled'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('slot', ['check_policy', 'checks', 'check_presentation', 'narration_preferences', 'storytelling', 'round_presentation'])
async def test_future_owners_cannot_be_overwritten_by_applying_defaults(slot):
    inst, rule = prepared_game()
    inst.modules[slot] = {'schema_version': 999, 'opaque': {'keep': True}}
    before = deepcopy(inst.to_dict())
    svc, save = service_for(inst, rule)
    result = await svc.update('key', Viewer('gm', 'gm'), {**default_preferences(), 'push_enabled': False})
    assert result['ok'] is False
    save.assert_not_awaited()
    assert inst.to_dict() == before


def test_old_round_presentation_defaults_on_and_disabled_owner_keeps_opaque_preferences():
    from src.engine import module_hooks
    from src.engine.modules import round_presentation
    inst = GameInstance(('web', 'old-presentation', 'bot'))
    inst.modules['round_presentation'].pop('quick_actions_enabled', None)
    assert game_preferences.project(inst)['quick_actions_enabled'] is True
    game_preferences.apply(inst, {'quick_actions_enabled': False})
    stored = deepcopy(inst.modules['round_presentation'])
    removed = module_hooks.unregister_owner(round_presentation.MODULE_NAME)
    try:
        assert game_preferences.project(inst)['quick_actions_enabled'] is True
        game_preferences.apply(inst, {'quick_actions_enabled': True})
        assert inst.modules['round_presentation'] == stored
    finally:
        for point, handler in removed.items():
            module_hooks.register(point, round_presentation.MODULE_NAME, handler)
    assert game_preferences.project(inst)['quick_actions_enabled'] is False


@pytest.mark.asyncio
async def test_generation_in_flight_rejects_preference_changes():
    from unittest.mock import Mock
    inst, rule = prepared_game()
    inst._process_task = Mock(done=lambda: False)
    before = deepcopy(inst.to_dict())
    svc, save = service_for(inst, rule)
    response = await svc.update('key', Viewer('gm', 'gm'), {'push_enabled': False})
    assert response['error_code'] == 'ROUND_PROCESSING'
    save.assert_not_awaited()
    assert inst.to_dict() == before


@pytest.mark.asyncio
async def test_story_mode_rejection_does_not_partially_apply_other_fields():
    inst, rule = prepared_game()
    before = deepcopy(inst.to_dict())
    svc, save = service_for(inst, rule)
    response = await svc.update('key', Viewer('gm', 'gm'), {'push_enabled': False, 'story_mode': 'dialogue'})
    assert response['ok'] is False
    save.assert_not_awaited()
    assert inst.to_dict() == before


@pytest.mark.asyncio
async def test_global_defaults_are_not_in_anonymous_configuration():
    import json
    from types import SimpleNamespace
    from aiohttp.test_utils import make_mocked_request
    from src.webui.config_controller import ConfigController
    from src.webui.routes.auth import ACCESS_PASSWORD_CONFIGURED_KEY
    private = {'game_defaults': {'gm_style_override': {'custom_instructions': 'secret keeper notes'}}, 'model': 'public'}
    controller = ConfigController(SimpleNamespace(public_config=lambda: deepcopy(private)))
    request = make_mocked_request('GET', '/api/config')
    request[ACCESS_PASSWORD_CONFIGURED_KEY] = True
    response = await controller.get(request)
    payload = json.loads(response.body)
    assert 'game_defaults' not in payload
    assert payload['model'] == 'public'


@pytest.mark.asyncio
@pytest.mark.parametrize('preview,uid', [(True, 'gm'), (False, 'a')])
async def test_player_and_preview_cannot_read_or_write_gm_preferences(preview, uid):
    from types import SimpleNamespace
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request
    from src.webui.routes.game_preferences import preferences
    inst, _ = prepared_game()
    app = web.Application()
    mutate = AsyncMock()
    app['api'] = SimpleNamespace(get_game_instance=lambda key: inst, update_game_preferences=mutate)
    request = make_mocked_request('POST', '/api/games/key/preferences', app=app, match_info={'game_key': 'key'})
    request['player_preview'] = preview
    request['owner_authenticated'] = preview
    request['user_id'] = uid
    response = await preferences(request)
    assert response.status == 403
    mutate.assert_not_awaited()


@pytest.mark.asyncio
async def test_disable_resumes_once_and_a_later_generation_failure_does_not_undo_saved_preferences(monkeypatch):
    from types import SimpleNamespace
    from src.webui.api import WebAPI
    from src.webui.services import turns
    inst, rule = prepared_game()
    checks.last_checks(inst)[0].update(luck_decision='declined', luck_spend_available=False)
    checks.last_checks(inst)[0]['push']['status'] = 'requested'
    svc, save = service_for(inst, rule)
    resume = AsyncMock(side_effect=RuntimeError('generation failed'))
    monkeypatch.setattr(turns, 'continue_after_decision', resume)
    facade = SimpleNamespace(_game_preferences=svc, get_game_instance=lambda key: inst, _turn_dependencies=object())
    response = await WebAPI.update_game_preferences(facade, 'key', Viewer('gm', 'gm'), {'push_enabled': False})
    assert response['status'] == 200
    assert response['payload']['ok'] is True
    assert response['payload']['preferences']['push_enabled'] is False
    assert response['payload']['resume']['ok'] is False
    assert 'defaults' not in response['payload']
    assert resume.await_count == 1
    assert resume.call_args.args[3]['ready_to_resolve'] is True
    save.assert_awaited_once()
    assert not check_decisions.pending(inst)


def test_detaching_optional_policy_keeps_game_playable_without_erasing_opaque_preferences():
    from src.engine import module_hooks
    inst, rule = prepared_game()
    checks.last_checks(inst)[0].pop('push')
    check_policy.replace_push_enabled(inst, False)
    original = deepcopy(inst.modules['check_policy'])
    removed = module_hooks.unregister_owner(check_policy.NAME)
    try:
        check_decisions.prepare(inst, rule)
        assert check_decisions.pending(inst)
        assert game_preferences.project(inst)['push_enabled'] is True
        assert inst.modules['check_policy'] == original
    finally:
        for point, handler in removed.items():
            module_hooks.register(point, check_policy.NAME, handler)
    assert check_policy.push_enabled(inst) is False
