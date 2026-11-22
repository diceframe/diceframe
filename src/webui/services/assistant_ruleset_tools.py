"""Ruleset-owned agent tools.

A ruleset's tools live under one domain derived from its runtime id, and the
registry keeps that mapping explicitly: runtime ids contain a colon
(``core:dnd2024``) while action sources and domains only allow
``[a-z0-9_-]``.  Nothing here parses a domain back into a runtime id — the
composition root records ``source -> runtime_id`` when it registers the source.

This module holds no mechanics.  Each tool turns agent arguments into the same
structured intent the table-facing path submits, so validation, dice, events
and state mutation stay in one authoritative pipeline.
"""
from __future__ import annotations

import inspect
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.application_actions import ApplicationAction

#: Cover levels a GM may declare, in ascending order (SRD 5.2.1).
COVER_LEVELS = ('none', 'half', 'three_quarters', 'total')

RULESET_DOMAIN_PREFIX = 'ruleset-'
#: `register_source()` and `ApplicationAction.domain` both cap at 40 chars and
#: only accept `[a-z0-9_-]`.
_MAX_IDENTIFIER = 40


def ruleset_slug(runtime_id: str) -> str:
    """A stable, contract-legal identifier for one runtime id."""

    return re.sub(r'[^a-z0-9]+', '-', str(runtime_id).lower()).strip('-')


def ruleset_domain(runtime_id: str) -> str:
    return (RULESET_DOMAIN_PREFIX + ruleset_slug(runtime_id))[:_MAX_IDENTIFIER]


def ruleset_source_name(runtime_id: str) -> str:
    return ('ruleset_' + ruleset_slug(runtime_id).replace('-', '_'))[:_MAX_IDENTIFIER]


@dataclass(frozen=True)
class RulesetToolDependencies:
    """Owner seams a ruleset tool needs; never the WebAPI facade."""

    get_game: Callable[[str], Any]
    viewer_for: Callable[[Any, str], Any]
    submit_ruleset_intent: Callable[[str, str, bool, dict], Any] | None = None


def _digest(value: Any) -> str:
    from src.application_actions import _digest as action_digest

    return action_digest(value)


async def _call(callback: Callable, *args: Any, **kwargs: Any) -> Any:
    result = callback(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _game(deps: RulesetToolDependencies, args: dict, owner: str) -> tuple[Any, Any]:
    game = deps.get_game(args['game_key'])
    if game is None:
        raise ValueError('Game does not exist')
    return game, deps.viewer_for(game, owner)


def _ruleset_state(game: Any) -> dict:
    return (getattr(game, 'modules', {}) or {}).get('ruleset_runtime') or {}


def _set_cover_schema() -> dict:
    return {
        'type': 'object',
        'properties': {
            'game_key': {'type': 'string', 'maxLength': 200},
            'run_id': {'type': 'string', 'maxLength': 120},
            'target_id': {'type': 'string', 'maxLength': 120},
            'level': {'type': 'string', 'enum': list(COVER_LEVELS)},
        },
        'required': ['game_key', 'run_id', 'target_id', 'level'],
        'additionalProperties': False,
    }


def ruleset_tools(
    deps: RulesetToolDependencies, runtime_id: str,
) -> tuple[ApplicationAction, ...]:
    """Every agent-facing tool owned by one ruleset."""

    if deps.submit_ruleset_intent is None:
        return ()

    domain = ruleset_domain(runtime_id)

    def set_cover_guard(args: dict, owner: str) -> str:
        game, viewer = _game(deps, args, owner)
        return _digest({
            'run_id': game.run_id,
            'ruleset_state': deepcopy(_ruleset_state(game)),
            'viewer_uid': getattr(viewer, 'uid', ''),
        })

    async def set_cover(args: dict, owner: str) -> Any:
        game, viewer = _game(deps, args, owner)
        if args['run_id'] != game.run_id:
            raise ValueError('Run changed; refresh first')
        # 版本由服务端现读：模型不必猜，guard 仍然锁住整个规则状态。
        intent = {
            'type': 'combat.set_cover',
            'intent_id': uuid.uuid4().hex,
            'expected_version': int(_ruleset_state(game).get('version', 0) or 0),
            'target_id': str(args['target_id']),
            'level': str(args['level']),
        }
        return await _call(
            deps.submit_ruleset_intent, args['game_key'],
            getattr(viewer, 'uid', ''), bool(getattr(viewer, 'is_gm', False)), intent,
        )

    return (
        ApplicationAction(
            'set_cover',
            'Declare the cover level of one combatant',
            _set_cover_schema(),
            set_cover,
            mutation=True,
            guard=set_cover_guard,
            domain=domain,
            effect='write',
            instructions=(
                'Declare how much cover one combatant currently has (the table\'s '
                'judgement, not a roll). Only the GM may call this, and only while '
                'combat is active; the target must be a combatant in this game. '
                'Levels: none, half (+2 AC and Dex saves), three_quarters (+5), '
                'total (cannot be targeted directly).'
            ),
        ),
    )
