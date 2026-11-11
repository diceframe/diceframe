"""Deferred, audited skill improvement for rules declaring policy version 1.

Successful settled checks mark stable skill-row IDs. Narrator tags do not mark,
roll, or increase skills. Settlement is called on an isolated aggregate under
the service authority gate and persisted with its receipt in one transaction.
"""
from __future__ import annotations

from copy import deepcopy
import json
import random
import secrets
from typing import Any
from uuid import uuid4

from src.engine import dice_rng, module_hooks
from src.engine.module_state import ModuleStateError, ModuleStateSpec, get_module_state, register_module_state
from src.engine.modules import progression_state

MODULE_NAME = 'skill_growth'


def fresh() -> dict[str, Any]:
    return {'schema_version': 1, 'pending': [], 'receipts': {}}


def ensure(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else fresh()


def require_writable(instance: Any) -> None:
    raw = instance.modules.get(MODULE_NAME)
    if raw is not None and (not isinstance(raw, dict) or type(raw.get('schema_version')) is not int or raw['schema_version'] != 1):
        raise ModuleStateError('unsupported skill_growth schema')
    if raw is not None and (not isinstance(raw.get('pending'), list) or not isinstance(raw.get('receipts'), dict)):
        raise ModuleStateError('invalid skill_growth ledger')
    if raw is not None and 'entropy' in raw and (not isinstance(raw['entropy'], str) or len(raw['entropy']) != 64):
        raise ModuleStateError('invalid skill_growth randomness reservation')


def view(instance: Any) -> dict[str, Any]:
    require_writable(instance)
    return deepcopy(instance.modules.get(MODULE_NAME) or fresh())


def support_status(rule: Any) -> tuple[str, str]:
    """Return the growth contract state without silently disabling it."""
    policy = getattr(rule, 'template', {}).get('skill_improvement') or {}
    if getattr(rule, 'growth_system', '') != 'skill_improvement':
        return 'unsupported', ''
    if (policy.get('version') == 1 and policy.get('success') == 'above_skill_or_96'
            and policy.get('gain') == 'd10' and type(policy.get('cap')) is int
            and 1 <= policy['cap'] <= 100):
        return 'supported', ''
    return 'invalid_policy', 'skill_improvement requires a complete version 1 policy (success, gain, and cap)'


def supported(rule: Any) -> bool:
    return support_status(rule)[0] == 'supported'


def require_supported_policy(rule: Any) -> None:
    status, message = support_status(rule)
    if status == 'invalid_policy':
        raise ValueError(message)


def reserve_entropy(instance: Any) -> None:
    """Reserve durable randomness before any settlement roll is evaluated.

    The service saves this reservation first. A failed/cancelled final save can
    then retry the same marked check without drawing a different result, even
    if the client uses a new receipt ID. Normal gameplay RNG is unaffected.
    """
    state = view(instance)
    if not state.get('entropy'):
        state['entropy'] = secrets.token_hex(32)
        instance.modules[MODULE_NAME] = state


def _canonical_skill_ids(rule: Any, name: Any) -> list[str]:
    """Rule-declared identities a skill label may stand for, in declaration order."""
    definitions = rule.template.get('skills') or {}
    canonical_names = rule.template.get('canonical_skill_names') or {}
    aliases = rule.template['skill_improvement'].get('identity_aliases') or {}
    return list(dict.fromkeys(
        [sid for sid, definition in definitions.items() if isinstance(definition, dict)
         and name in {sid, definition.get('name'), canonical_names.get(sid)}]
        + [sid for sid, names in aliases.items() if isinstance(names, list) and name in names]))


def _restore_marked_identities(rule: Any, sheet: dict, marks: list[dict]) -> None:
    """Re-attach pending marks to rows whose stored identity was dropped.

    Character normalization before 3.2.0 rewrote skill rows as name/value/effect,
    so saves can hold a pending mark whose row no longer carries ``id``. Rebind
    only an id-less row that unambiguously names the marked skill, either by the
    label recorded with the mark or by the rule's single canonical identity.
    Anything else stays unbound and settlement keeps refusing it.
    """
    skills = sheet.get('skills')
    if not isinstance(skills, list):
        return
    for mark in marks:
        sid = mark['skill_id']
        if any(isinstance(s, dict) and s.get('id') == sid for s in skills):
            continue
        label = mark.get('label')
        rows = [s for s in skills if isinstance(s, dict) and not s.get('id')
                and ((label and s.get('name') == label) or _canonical_skill_ids(rule, s.get('name')) == [sid])]
        if len(rows) == 1:
            rows[0]['id'] = sid


def mark_checks(instance: Any, rule: Any, checks: list[dict]) -> None:
    if not supported(rule):
        return
    require_writable(instance)
    slot = get_module_state(instance, MODULE_NAME)
    excluded = rule.template['skill_improvement'].get('excluded_skill_ids', [])
    settled_checks = {(change['actor_uid'], change['check_id']) for receipt in slot['receipts'].values()
                      for change in receipt['changes']}
    for check in checks:
        uid = check.get('actor_uid')
        value = check.get('roll')
        threshold = check.get('threshold')
        if (uid not in instance.players or not check.get('check_id') or check.get('dice') != 'd100'
                or (uid, check.get('check_id')) in settled_checks
                or not check.get('skill') or check.get('attribute_key') or check.get('luck_decision') == 'spent'
                or type(value) is not int or type(threshold) is not int or not 1 <= value <= 100
                or value > threshold or check.get('is_fumble')):
            continue
        sheet = instance.get_character_sheet(uid)
        # A row that lost its identity must get the pending mark's id back, not
        # a freshly minted one that would orphan the earlier mark.
        _restore_marked_identities(rule, sheet, [p for p in slot['pending'] if p['actor_uid'] == uid])
        skills = sheet.get('skills', [])
        matches = [s for s in skills if s.get('name') == check['skill']]
        if len(matches) != 1:
            continue  # Ambiguous legacy names cannot identify a skill row safely.
        skill = matches[0]
        if skill.get('improvable') is False or skill.get('id') in excluded:
            continue
        # Mint an identity once at the authoritative sheet boundary. Localized
        # names remain labels and never become persisted growth identities.
        if not skill.get('id'):
            candidates = _canonical_skill_ids(rule, skill['name'])
            if len(candidates) > 1:
                continue
            sid = candidates[0] if candidates else f'skill_{uuid4().hex}'
            if sid in excluded:
                continue
            skill['id'] = sid
        sid = skill['id']
        if sum(s.get('id') == sid for s in skills) != 1:
            continue
        if not any(p['actor_uid'] == uid and p['skill_id'] == sid for p in slot['pending']):
            slot['pending'].append({'actor_uid': uid, 'skill_id': sid, 'label': skill['name'],
                                    'check_id': check['check_id'], 'round': progression_state.round_value(instance)})
        instance.set_character_sheet(uid, sheet)


def settle(instance: Any, rule: Any, receipt_id: str, *, expected_run: str) -> dict[str, Any]:
    state = view(instance)
    if expected_run != instance.run_id or not isinstance(receipt_id, str) or not 1 <= len(receipt_id) <= 120:
        raise ValueError('invalid intermission identity')
    if receipt_id in state['receipts']:
        return deepcopy(state['receipts'][receipt_id])
    if not supported(rule):
        raise ValueError('this rule does not declare supported intermission skill improvement')
    targets = []
    staged: dict[str, dict] = {}
    # Validate every target before consuming any RNG or changing any sheet.
    for mark in state['pending']:
        uid = mark['actor_uid']
        if uid not in instance.players:
            raise ValueError('a marked character left; resolve its mark before settlement')
        if uid not in staged:
            # Identity repair happens on the staged copy, so a refused
            # settlement leaves the live sheet untouched.
            staged[uid] = deepcopy(instance.get_character_sheet(uid))
            _restore_marked_identities(rule, staged[uid], [m for m in state['pending'] if m['actor_uid'] == uid])
        sheet = staged[uid]
        found = [s for s in sheet.get('skills', []) if s.get('id') == mark['skill_id']]
        if len(found) != 1 or type(found[0].get('value')) is not int:
            raise ValueError(f"marked skill no longer has an unambiguous identity/value: {mark.get('label') or mark['skill_id']}")
        if found[0].get('improvable') is False or mark['skill_id'] in rule.template['skill_improvement'].get('excluded_skill_ids', []):
            raise ValueError('marked skill is no longer eligible under the current rule policy')
        targets.append((mark, sheet, found[0]))
    changes = []
    sheets: dict[str, dict] = {}
    cap = rule.template['skill_improvement']['cap']
    for mark, sheet, skill in targets:
        # Multiple skills of one actor must accumulate on the same staged sheet.
        sheet = sheets.setdefault(mark['actor_uid'], sheet)
        skill = next(s for s in sheet['skills'] if s.get('id') == mark['skill_id'])
        before = skill['value']
        entropy = state.get('entropy')
        if entropy:
            identity = json.dumps([instance.run_id, mark['actor_uid'], mark['skill_id'], mark['check_id']],
                                  ensure_ascii=True, separators=(',', ':'))
            rng = random.Random(f'{entropy}:{identity}')
            die = rng.randint(1, 100)
            gain = rng.randint(1, 10) if die > before or die >= 96 else 0
        else:
            # Pure domain callers own their transaction/durability boundary.
            die = dice_rng.roll_die(100)
            gain = dice_rng.roll_die(10) if die > before or die >= 96 else 0
        skill['value'] = min(cap, before + gain) if before < cap else before
        changes.append({**mark, 'before': before, 'check_roll': die, 'gain_roll': gain,
                        'after': skill['value']})
    receipt = {'id': receipt_id, 'run_id': instance.run_id, 'rule_id': instance.rule_id,
               'round': progression_state.round_value(instance), 'changes': changes,
               'policy': deepcopy(rule.template['skill_improvement'])}
    for uid, sheet in sheets.items():
        instance.set_character_sheet(uid, sheet)
    state['pending'] = []
    state['receipts'][receipt_id] = receipt
    instance.modules[MODULE_NAME] = state
    return deepcopy(receipt)


def _begin(instance: Any) -> None:
    state = view(instance); state.pop('_before_round', None)
    get_module_state(instance, MODULE_NAME)['_before_round'] = state


def _commit(instance: Any, entry: Any, _pre: Any) -> None:
    slot = get_module_state(instance, MODULE_NAME)
    if '_before_round' not in slot and not slot['pending'] and not slot['receipts']:
        return
    entry['pre_skill_growth'] = slot.pop('_before_round', view(instance))


def _abort(instance: Any) -> bool:
    before = view(instance).get('_before_round')
    if isinstance(before, dict):
        instance.modules[MODULE_NAME] = deepcopy(before)
        return True
    return False


def rewind_preflight(instance: Any, target: int) -> None:
    state = view(instance)
    if any(r['round'] >= target for r in state['receipts'].values()):
        raise ValueError('cannot rewrite across a settled intermission; start a new run instead')


def _rewind(instance: Any, entry: Any, _round: int, _source: str) -> None:
    before = entry.get('pre_skill_growth')
    state = view(instance)
    if isinstance(before, dict):
        state['pending'] = deepcopy(before.get('pending', []))
    else:
        state['pending'] = []
    state.pop('_before_round', None)
    instance.modules[MODULE_NAME] = state


def reset(instance: Any) -> None:
    require_writable(instance)
    instance.modules[MODULE_NAME] = fresh()


def _workspace_revision(instance: Any, revisions: dict[str, Any]) -> None:
    state = view(instance)
    if state['pending'] or state['receipts']:
        revisions[MODULE_NAME] = {'run_id': instance.run_id, 'pending': state['pending'],
                                  'receipts': sorted(state['receipts'])}


register_module_state(ModuleStateSpec(MODULE_NAME, 1, fresh, ensure))
module_hooks.register(module_hooks.GM_WORKSPACE_REVISIONS, MODULE_NAME, _workspace_revision)
module_hooks.register(module_hooks.AUTHORITATIVE_WRITE_ALLOWED, MODULE_NAME, require_writable)
module_hooks.register(module_hooks.SETTLED_SKILL_USE, MODULE_NAME, mark_checks)
module_hooks.register(module_hooks.ROUND_PROCESSING_BEGIN, MODULE_NAME, _begin)
module_hooks.register(module_hooks.ROUND_COMMIT, MODULE_NAME, _commit)
module_hooks.register(module_hooks.ROUND_ABORT, MODULE_NAME, _abort)
module_hooks.register(module_hooks.ROUND_REWIND_RESTORE, MODULE_NAME, _rewind)
module_hooks.register(module_hooks.ROUND_RECOVERY_PREFLIGHT, MODULE_NAME, require_writable)
module_hooks.register(module_hooks.INSTANCE_RESET_PREFLIGHT, MODULE_NAME, require_writable)
module_hooks.register(module_hooks.INSTANCE_NEW_RUN_PREFLIGHT, MODULE_NAME, require_writable)
