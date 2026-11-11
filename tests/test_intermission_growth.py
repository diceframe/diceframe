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


def _damaged_coc_save():
    """A save written before 3.2.0: pending marks kept ids, the sheet rows lost them."""
    from tests.test_advantage_authority import _instance, _rule
    names = ['侦查', '聆听', '说服', '心理学', '图书馆使用', '急救', '闪避', '潜行']
    inst = _instance(skills=[{'name': name, 'value': 85 if name in {'侦查', '聆听'} else 40} for name in names])
    inst.modules['skill_growth'] = {'schema_version': 1, 'receipts': {}, 'pending': [
        {'actor_uid': 'a', 'skill_id': sid, 'label': label, 'check_id': f'c{round_}', 'round': round_}
        for sid, label, round_ in (('persuade', '说服', 5), ('spot_hidden', '侦查', 7),
                                   ('psychology', '心理学', 13), ('stealth', '潜行', 28))]}
    return inst, _rule('freeform_coc.json')


def test_settlement_rebinds_marks_whose_skill_rows_lost_their_identity():
    inst, rule = _damaged_coc_save()
    with patch('src.engine.dice_rng.roll_die', side_effect=[99, 3, 99, 4, 99, 5, 99, 6]):
        receipt = skill_growth.settle(inst, rule, 'once', expected_run=inst.run_id)
    assert [(c['skill_id'], c['before'], c['after']) for c in receipt['changes']] == [
        ('persuade', 40, 43), ('spot_hidden', 85, 89), ('psychology', 40, 45), ('stealth', 40, 46)]
    skills = {s['name']: s for s in inst.get_character_sheet('a')['skills']}
    assert {name: skills[name].get('id') for name in ('侦查', '说服', '心理学', '潜行', '聆听')} == {
        '侦查': 'spot_hidden', '说服': 'persuade', '心理学': 'psychology', '潜行': 'stealth', '聆听': None}
    assert skill_growth.view(inst)['pending'] == []


def test_identity_rebinding_refuses_ambiguous_rows_and_leaves_the_save_untouched():
    inst, rule = _damaged_coc_save()
    inst.get_character_sheet('a')['skills'].append({'name': '潜行', 'value': 10})
    before = deepcopy(inst.to_dict())
    with patch('src.engine.dice_rng.roll_die', side_effect=AssertionError('preflight before RNG')):
        with pytest.raises(ValueError, match='潜行'):
            skill_growth.settle(inst, rule, 'once', expected_run=inst.run_id)
    assert inst.to_dict() == before


def test_new_success_on_a_row_that_lost_its_identity_reuses_the_pending_mark():
    inst, rule, check = fixture()
    skill_growth.mark_checks(inst, rule, [check])
    marked_id = skill_growth.view(inst)['pending'][0]['skill_id']
    assert marked_id.startswith('skill_')
    inst.get_character_sheet('alice')['skills'][0].pop('id')
    skill_growth.mark_checks(inst, rule, [{**check, 'check_id': 'check2'}])
    assert inst.get_character_sheet('alice')['skills'][0]['id'] == marked_id
    assert [p['skill_id'] for p in skill_growth.view(inst)['pending']] == [marked_id]
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
