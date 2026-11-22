"""Server-held, owner-session authorization for bounded Agent tasks."""
from __future__ import annotations

import secrets
from copy import deepcopy

SCOPE_KEYS = frozenset({'game_key', 'world_id', 'book_id', 'card_id', 'project_id'})


def make_authorization(mode, scope, domains, max_steps, now):
    if mode not in {'suggest', 'routine', 'task'}:
        raise ValueError('Unknown authorization mode')
    if (not isinstance(scope, dict) or set(scope) - SCOPE_KEYS
            or any(not isinstance(value, str) or not 1 <= len(value) <= 500 for value in scope.values())):
        raise ValueError('Invalid authorization scope')
    if (not isinstance(domains, list) or len(domains) > 16 or len(set(domains)) != len(domains)
            or any(not isinstance(value, str) or not value or len(value) > 40 for value in domains)):
        raise ValueError('Invalid authorization domains')
    if type(max_steps) is not int or not 1 <= max_steps <= 64:
        raise ValueError('Action budget must be between 1 and 64')
    if mode == 'suggest':
        return None
    return {'grant_id': secrets.token_urlsafe(12), 'mode': mode, 'scope': deepcopy(scope), 'domains': list(domains), 'max_steps': max_steps,
            'remaining_steps': max_steps, 'expires_at': now + (3600 if mode == 'task' else 14400)}


def current_authorization(data, now):
    value = data.get('authorization')
    if (not isinstance(value, dict) or value.get('expires_at', 0) <= now
            or value.get('remaining_steps', 0) <= 0):
        return None
    return deepcopy(value)


def permits(grant, action, arguments, assessment=None):
    effect = assessment.effect if assessment is not None else action.effective_effect
    if (not grant or effect in {'destructive', 'external'}
            or (assessment is not None and assessment.requires_approval)):
        return False
    if grant['domains'] and action.domain not in grant['domains']:
        # 规则域不归这份列表管：一个会话能看到哪些规则工具，由“它当前看的
        # 对局解析到哪个 runtime”决定，调用方已在对局核验与调用层各查一次。
        # 往 domains 里追加会改变“空列表 = 不限域”的既有语义（显式列表被
        # 追加是扩大、空列表被追加是收窄），所以这里显式放行规则域，其余域
        # 维持原判定。
        if not action.domain.startswith('ruleset-'):
            return False
    scope = grant['scope']
    targets = assessment.targets if assessment is not None and assessment.targets else (arguments,)
    if scope:
        if any(any(key not in target or scope[key] != target[key] for key in scope) for target in targets):
            return False
    if grant['mode'] == 'routine':
        return action.automatic and effect == 'write'
    return grant['mode'] == 'task' and effect in {'write', 'disclosure'}
