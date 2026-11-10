from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.engine.game_instance import GameInstance
from src.engine.modules import skill_growth


def fixture():
    inst = GameInstance(('test', 'growth'))
    inst.players['alice'] = {'character_name': 'Alice', 'character_sheet': {
        'hp': 10, 'max_hp': 10, 'skills': [{'name': 'Spot Hidden', 'value': 40}]}}
    rule = SimpleNamespace(growth_system='skill_improvement', template={'skill_improvement': {
        'version': 1, 'success': 'above_skill_or_96', 'gain': 'd10', 'cap': 99,
        'excluded_skill_ids': ['cthulhu_mythos']}})
    check = {'check_id': 'check1', 'actor_uid': 'alice', 'skill': 'Spot Hidden', 'dice': 'd100',
             'kind': 'check', 'roll': 20, 'threshold': 40, 'luck_decision': 'declined'}
    return inst, rule, check


def test_real_success_marks_without_rolling_or_growing_until_intermission():
    inst, rule, check = fixture()
    with patch('src.engine.dice_rng.roll_die', side_effect=AssertionError('early RNG')):
        skill_growth.mark_checks(inst, rule, [check, check])
    assert inst.get_character_sheet('alice')['skills'][0]['value'] == 40
    assert len(skill_growth.view(inst)['pending']) == 1
    with patch('src.engine.dice_rng.roll_die', side_effect=[98, 6]) as dice:
        first = skill_growth.settle(inst, rule, 'once', expected_run=inst.run_id)
        second = skill_growth.settle(inst, rule, 'once', expected_run=inst.run_id)
        assert first == second and dice.call_count == 2
    assert inst.get_character_sheet('alice')['skills'][0]['value'] == 46
    assert not skill_growth.view(inst)['pending']
    restored = GameInstance.from_dict(inst.to_dict())
    assert skill_growth.settle(restored, rule, 'once', expected_run=inst.run_id) == first
    skill_growth.mark_checks(restored, rule, [check])
    assert skill_growth.view(restored)['pending'] == []


def test_character_normalization_keeps_marked_identity_for_settlement():
    from src.compat.characters import normalize_character_sheet

    inst, rule, check = fixture()
    skill_growth.mark_checks(inst, rule, [check])
    marked_id = skill_growth.view(inst)['pending'][0]['skill_id']
    normalize_character_sheet(inst.get_character_sheet('alice'))
    assert inst.get_character_sheet('alice')['skills'][0]['id'] == marked_id
    with patch('src.engine.dice_rng.roll_die', side_effect=[98, 6]):
        receipt = skill_growth.settle(inst, rule, 'once', expected_run=inst.run_id)
    assert receipt['changes'][0]['after'] == 46


@pytest.mark.parametrize('change', [
    {'roll': 80}, {'skill': ''}, {'dice': 'd20'}, {'luck_decision': 'spent'},
    {'attribute_key': 'int'}, {'actor_uid': 'invented'},
])
def test_ineligible_checks_do_not_create_marks(change):
    inst, rule, check = fixture(); check.update(change)
    skill_growth.mark_checks(inst, rule, [check])
    assert skill_growth.view(inst)['pending'] == []


def test_dnd_and_llm_growth_tags_cannot_trigger_growth_rng():
    from src.commands.progression_resolver import ProgressionResolver
    from pathlib import Path
    inst, rule, check = fixture(); rule.growth_system = 'xp_level'
    skill_growth.mark_checks(inst, rule, [check])
    assert not skill_growth.view(inst)['pending']
    with patch('random.randint', side_effect=AssertionError('LLM tag must not roll')):
        ProgressionResolver(Path('templates/rules'), Path('templates/worlds')).skill_growth_checks(
            inst, [{'uid': 'alice', 'skill': 'Spot Hidden'}])


def test_settlement_unknown_schema_stale_run_and_skill_conflict_fail_closed():
    inst, rule, check = fixture(); skill_growth.mark_checks(inst, rule, [check])
    before = deepcopy(inst.to_dict())
    with pytest.raises(ValueError):
        skill_growth.settle(inst, rule, 'once', expected_run='old')
    assert inst.to_dict() == before
    inst.get_character_sheet('alice')['skills'].clear()
    before = deepcopy(inst.to_dict())
    with patch('src.engine.dice_rng.roll_die', side_effect=AssertionError('preflight before RNG')):
        with pytest.raises(ValueError):
            skill_growth.settle(inst, rule, 'once', expected_run=inst.run_id)
    assert inst.to_dict() == before


@pytest.mark.asyncio
async def test_settled_growth_cannot_be_rewound_with_optional_hooks_detached():
    from src.engine import module_hooks
    inst, rule, check = fixture()
    skill_growth.mark_checks(inst, rule, [check])
    skill_growth.settle(inst, rule, 'settled', expected_run=inst.run_id)
    inst.log.append({'round': 0, 'gm_response': 'Public round'})
    before = deepcopy(inst.to_dict())
    removed = module_hooks.unregister_owner('skill_growth')
    try:
        with pytest.raises(ValueError, match='settled intermission'):
            await inst.rollback_last_round()
        assert inst.to_dict() == before
    finally:
        for point, callback in removed.items():
            module_hooks.register(point, 'skill_growth', callback)


def test_real_percentile_check_and_round_reward_path_marks_only_server_success():
    from src.engine.checks import build_check_request, resolve_check_request, roll_check_request
    from src.engine.modules import checks
    from src.commands.round_effects import apply_growth_rewards
    from tests.test_advantage_authority import _instance, _rule
    inst = _instance(skills=[{'name': '侦查', 'value': 60}])
    rule = _rule('freeform_coc.json')
    action = {'user_id': 'a', 'text': '搜索书房', 'selected_skill': '侦查'}
    request = build_check_request(inst, action, rule)
    request['check_id'] = 'server-success'
    with patch('random.randint', return_value=20):
        rolled = roll_check_request(request, rule)
    result = resolve_check_request(inst, {**action, 'check_request': request,
                                          'dice_value': rolled['value'], 'dice_rolls': rolled['rolls']}, rule)
    checks.replace_last_checks(inst, [result])
    with patch('src.engine.dice_rng.roll_die', side_effect=AssertionError('growth must be deferred')):
        apply_growth_rewards(inst, {'growth_skills': [{'uid': 'b', 'skill': 'invented'}]}, None, rule, None)
    pending = skill_growth.view(inst)['pending']
    assert len(pending) == 1 and pending[0]['actor_uid'] == 'a' and pending[0]['skill_id'] == 'spot_hidden'
    assert inst.get_character_sheet('a')['skills'][0]['value'] == 60


@pytest.mark.parametrize('label', ['克苏鲁神话', 'Cthulhu Mythos'])
def test_excluded_legacy_skill_labels_resolve_to_the_same_canonical_policy(label):
    from tests.test_advantage_authority import _rule
    inst, _, check = fixture()
    inst.get_character_sheet('alice')['skills'] = [{'name': label, 'value': 30}]
    check.update(skill=label, threshold=30)
    skill_growth.mark_checks(inst, _rule('freeform_coc.json'), [check])
    assert skill_growth.view(inst)['pending'] == []
