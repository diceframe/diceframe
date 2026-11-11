"""GM-only, all-or-nothing current-table preference transactions."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.engine import game_preferences
from src.engine.module_state import ModuleStateError
from src.engine.participant_view import Viewer
from src.engine.staged_transaction import StagedGameTransaction
from src.game_preferences import validate_preferences
from src.webui.services.check_decisions import decision_payload
from src.localization.messages import t

logger = logging.getLogger('trpg')


@dataclass(frozen=True)
class GamePreferenceDependencies:
    get_instance: Callable[[str], Any | None]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule: Callable[[Any], Any]
    get_runtime: Callable[[Any], Any]
    get_defaults: Callable[[], dict]


class GamePreferenceService:
    def __init__(self, dependencies: GamePreferenceDependencies):
        self.deps = dependencies

    def get(self, key: str, viewer: Viewer, *, include_defaults: bool = False) -> dict:
        inst = self.deps.get_instance(key)
        if inst is None:
            return {'ok': False, 'error_code': 'GAME_NOT_FOUND'}
        if not viewer.is_gm:
            return {'ok': False, 'error_code': 'GM_ONLY'}
        try:
            runtime = self.deps.get_runtime(inst)
            result = {'ok': True, 'preferences': game_preferences.project(inst, runtime),
                      'push_supported': game_preferences.supports_push(self.deps.load_rule(inst))}
            if include_defaults:
                result['defaults'] = validate_preferences(self.deps.get_defaults())
            return result
        except (ValueError, ModuleStateError):
            return {'ok': False, 'error_code': 'UNSUPPORTED_PREFERENCES', 'error': t("game_preferences.version_unsupported")}

    async def update(self, key: str, viewer: Viewer, raw: Any) -> dict:
        if not viewer.is_gm:
            return {'ok': False, 'error_code': 'GM_ONLY'}
        inst = self.deps.get_instance(key)
        if inst is None:
            return {'ok': False, 'error_code': 'GAME_NOT_FOUND'}
        try:
            values = validate_preferences(raw, partial=True)
        except ValueError as exc:
            return {'ok': False, 'error_code': 'INVALID_PREFERENCES', 'error': str(exc)}
        async with inst.authoritative_write() as entered, inst._lock:
            if not entered:
                return {'ok': False, 'error_code': 'REWRITE_IN_PROGRESS', 'error': t("game_preferences.rewriting_retry_later")}
            if self.deps.get_instance(key) is not inst:
                return {'ok': False, 'error_code': 'STALE_RUN', 'error': t("game_preferences.game_restarted_refresh")}
            if inst.round_processing_in_flight():
                return {'ok': False, 'error_code': 'ROUND_PROCESSING', 'error': t("game_preferences.round_generating")}
            transaction = None
            try:
                transaction = StagedGameTransaction(inst)
                closed = game_preferences.apply(transaction.staged, values, self.deps.get_runtime(inst))
                game_preferences.project(transaction.staged, self.deps.get_runtime(inst))
                transaction.publish(inst)
                try:
                    await self.deps.save_instance(inst)
                except BaseException:
                    transaction.rollback(inst)
                    raise
            except asyncio.CancelledError:
                raise
            except (ValueError, ModuleStateError) as exc:
                return {'ok': False, 'error_code': 'INVALID_PREFERENCES', 'error': str(exc)}
            except Exception:
                logger.exception('Saving table preferences failed')
                return {'ok': False, 'error_code': 'SAVE_FAILED', 'error': t("game_preferences.save_failed_settings_kept")}
        result = self.get(key, viewer)
        result['closed_push_decisions'] = closed
        return decision_payload(inst, result) if closed else result
