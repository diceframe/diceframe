"""Authenticated application commands; gameplay facade only composes this owner."""
from __future__ import annotations

import logging
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Callable, Awaitable
from typing import Any

from src.engine.modules import adventure_runtime_state, economy_state
from src.localization.messages import t

logger = logging.getLogger('trpg')


@dataclass(frozen=True)
class AdventureCommandDependencies:
    save: Callable[[Any], Awaitable[None]]
    complete: Callable[..., dict] | None
    request_choice: Callable[..., dict] | None
    admission: Callable[[], dict | None]
    binding: Callable[[], Awaitable[dict | None]]
    get_instance: Callable[[Any], Any] | None = None


async def execute(deps: AdventureCommandDependencies, instance: Any, body: dict,
                  actor_uid: str, *, is_gm: bool) -> dict:
    kind = body['type']
    if kind == 'adventure.node.complete' and not is_gm:
        return {'ok': False, 'code': 'GM_ONLY', 'error': t("webui.services.adventure_commands.confirm_gm_only")}
    if kind == 'adventure.completion_suggestion.dismiss':
        if not is_gm:
            return {'ok': False, 'code': 'GM_ONLY', 'error': t("webui.services.adventure_commands.dismiss_gm_only")}
        if set(body) != {'type', 'proposal_id'}:
            return {'ok': False, 'code': 'INVALID_ADVENTURE_COMMAND', 'error': 'unexpected adventure command field'}
        proposal_id = body.get('proposal_id')
        if not isinstance(proposal_id, str) or not proposal_id or len(proposal_id) > 80:
            return {'ok': False, 'code': 'INVALID_ADVENTURE_COMMAND', 'error': 'proposal_id must be a non-empty string'}
        async with instance.authoritative_write() as write_entered, instance._lock:
            if not write_entered:
                return {"ok": False, "error_code": "REWRITE_IN_PROGRESS", "code": "REWRITE_IN_PROGRESS", "error": "historical rewrite is in progress"}
            if deps.get_instance is not None and deps.get_instance(instance.game_key) is not instance:
                return {"ok": False, "error_code": "STALE_RUN", "code": "STALE_RUN", "error": "game run was replaced"}
            error = deps.admission()
            if error:
                return error
            adventure_runtime_state.require_writable(instance)
            error = await deps.binding()
            if error:
                return error
            binding = getattr(instance, 'adventure_binding', None)
            if not isinstance(binding, dict) or binding.get('format') != 'diceframe:adventure-graph-v2':
                return {'ok': False, 'code': 'ADVENTURE_RUNTIME_UNAVAILABLE', 'error': 'Adventure v2 runtime is unavailable'}
            before = deepcopy(adventure_runtime_state.progress(instance))
            try:
                node_id = adventure_runtime_state.dismiss_completion_suggestion(instance, proposal_id)
                if node_id is None:
                    return {'ok': False, 'code': 'SUGGESTION_NOT_FOUND', 'error': t("webui.services.adventure_commands.suggestion_handled_or_missing")}
                await deps.save(instance)
            except BaseException as exc:
                adventure_runtime_state.replace_progress(instance, before)
                if isinstance(exc, asyncio.CancelledError) or not isinstance(exc, Exception):
                    raise
                logger.exception('Adventure suggestion dismissal failed and was restored')
                return {'ok': False, 'code': 'ADVENTURE_NODE_FAILED', 'error': t("webui.services.adventure_commands.save_failed_suggestion_restored")}
        return {'ok': True, 'result': {'completion_suggestion_dismissed': {'proposal_id': proposal_id, 'node_id': node_id}}}
    if kind not in {'adventure.node.complete', 'adventure.choice.request'}:
        return {'ok': False, 'code': 'INVALID_ADVENTURE_COMMAND', 'error': 'unknown adventure command'}
    if set(body) - {'type', 'node_id', 'choice_id'}:
        return {'ok': False, 'code': 'INVALID_ADVENTURE_COMMAND', 'error': 'unexpected adventure command field'}
    node_id, choice_id = body.get('node_id'), body.get('choice_id', '')
    if not isinstance(node_id, str) or not node_id.strip() or not isinstance(choice_id, str):
        return {'ok': False, 'code': 'INVALID_ADVENTURE_NODE', 'error': 'node_id and choice_id must be strings'}
    callback = deps.complete if kind == 'adventure.node.complete' else deps.request_choice
    if callback is None:
        return {'ok': False, 'code': 'ADVENTURE_RUNTIME_UNAVAILABLE', 'error': 'Adventure runtime is unavailable'}
    async with instance.authoritative_write() as write_entered, instance._lock:
        if not write_entered:
            return {"ok": False, "error_code": "REWRITE_IN_PROGRESS", "code": "REWRITE_IN_PROGRESS", "error": "historical rewrite is in progress"}
        if deps.get_instance is not None and deps.get_instance(instance.game_key) is not instance:
            return {"ok": False, "error_code": "STALE_RUN", "code": "STALE_RUN", "error": "game run was replaced"}
        error = deps.admission()
        if error:
            return error
        adventure_runtime_state.require_writable(instance)
        error = await deps.binding()
        if error:
            return error
        before = {
            "world_state": deepcopy(instance.world_state),
            "adventure_progress": deepcopy(adventure_runtime_state.progress(instance)),
            "economy": deepcopy(economy_state.state(instance)),
        }
        try:
            if kind == 'adventure.node.complete':
                args = (instance, node_id.strip(), actor_uid, actor_uid)
                result = callback(*args, choice_id=choice_id) if choice_id else callback(*args)
            else:
                result = callback(instance, node_id.strip(), choice_id, actor_uid)
            await deps.save(instance)
        except BaseException as exc:
            instance.world_state = before["world_state"]
            adventure_runtime_state.replace_progress(instance, before["adventure_progress"])
            economy_state.replace_state(instance, before["economy"])
            if isinstance(exc, asyncio.CancelledError) or not isinstance(exc, Exception):
                raise
            if isinstance(exc, (ValueError, KeyError, TypeError)):
                return {'ok': False, 'code': 'ADVENTURE_NODE_REJECTED', 'error': str(exc)}
            logger.exception('Adventure command failed and was restored')
            return {'ok': False, 'code': 'ADVENTURE_NODE_FAILED', 'error': t("webui.services.adventure_commands.settlement_failed_state_restored")}
    return {'ok': True, 'result': {'adventure_node' if kind == 'adventure.node.complete' else 'adventure_choice_request': result}}
