"""CoC recovery commands using the existing aggregate writer and logical clock."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import logging
from typing import Any, Awaitable, Callable

from src.engine.modules import progression_state, resource_recovery as recovery
from src.engine.participant_view import Viewer
from src.engine.staged_transaction import StagedGameTransaction
from src.engine.world_events import MAX_ADVANCE_MINUTES, advance_world_time
from src.engine.world.memory_projection import queue_world_memory
from src.engine.world_state import clock_to_minutes, world_clock
from src.rulesets.resource_recovery import managed_resources, policy, requirements, reward_delta, skill_value
from src.webui.services.recovery_projection import blockers, eligibility, intermissions, project, public_receipt
from src.localization.messages import t

logger = logging.getLogger('trpg')
COMMON = {'run_id', 'operation_id', 'expected_revision'}
FIELDS = {
    'request': {'kind', 'actor_uid', 'healer_uid', 'skill_id'},
    'approve': {'source_id'}, 'reject': {'source_id'}, 'claim': {'source_id'},
    'reward': {'actor_uid', 'resource_key', 'resource_delta', 'reason'},
    'luck': {'intermission_id'},
}


class RecoveryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RecoveryDependencies:
    get_instance: Callable[[str], Any]
    save_instance: Callable[[Any], Awaitable[None]]
    load_rule: Callable[[Any], Any]
    advance_adventure_world: Callable[[Any], Any] | None = None


def _error(code: str, message: str) -> dict:
    return {'ok': False, 'code': code, 'error': message}


def _identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        raise RecoveryError('INVALID_REQUEST', f'invalid {field}')
    return value


class RecoveryService:
    def __init__(self, dependencies: RecoveryDependencies):
        self.deps = dependencies

    def get(self, key: str, viewer: Viewer) -> dict:
        instance = self.deps.get_instance(key)
        if instance is None:
            return _error('GAME_NOT_FOUND', 'Game not found')
        if not viewer.is_member or (viewer.is_seat and viewer.uid not in instance.players):
            return _error('MEMBER_REQUIRED', 'Game membership required')
        try:
            return project(instance, self.deps.load_rule(instance), viewer)
        except ValueError as exc:
            return _error('RECOVERY_UNAVAILABLE', str(exc))

    async def mutate(self, key: str, viewer: Viewer, action: str, body: Any) -> dict:
        if not viewer.is_member:
            return _error('MEMBER_REQUIRED', 'Game membership required')
        if action in {'approve', 'reject', 'reward', 'luck'} and not viewer.is_gm:
            return _error('GM_ONLY', 'Only the GM can authorize recovery')
        instance = self.deps.get_instance(key)
        if instance is None:
            return _error('GAME_NOT_FOUND', 'Game not found')
        try:
            self._validate_body(action, body)
            # Gate schema validation may itself fail; keep it inside this guard.
            async with instance.authoritative_write() as entered, instance._lock:
                if self.deps.get_instance(key) is not instance or body['run_id'] != instance.run_id:
                    raise RecoveryError('STALE_RUN', t("webui.services.resource_recovery.run_changed_refresh"))
                if not entered:
                    raise RecoveryError('READ_ONLY', t("webui.services.resource_recovery.run_read_only"))
                if viewer.is_seat and viewer.uid not in instance.players:
                    raise RecoveryError('MEMBER_REQUIRED', 'Game membership required')
                recovery.require_writable(instance)
                rule = self.deps.load_rule(instance)
                selected = policy(rule)
                if selected is None or not managed_resources(rule):
                    raise RecoveryError('UNSUPPORTED_RULE', t("webui.services.resource_recovery.no_recovery_policy"))
                operation = body['operation_id']
                fingerprint = {name: deepcopy(value) for name, value in body.items() if name != 'expected_revision'}
                previous = recovery.command(instance, operation)
                if previous is not None:
                    if (previous.get('principal') != [viewer.kind, viewer.uid] or previous['action'] != action
                            or previous['body'] != fingerprint):
                        raise RecoveryError('OPERATION_ID_REUSED', 'Operation ID already used with another command')
                    return self._result(key, viewer, previous, duplicate=True)
                if body['expected_revision'] != recovery.view(instance)['revision']:
                    raise RecoveryError('STALE_REVISION', t("webui.services.resource_recovery.recovery_changed_refresh"))
                problems = blockers(instance)
                if problems:
                    raise RecoveryError('RECOVERY_BLOCKED', '; '.join(problems))
                transaction = StagedGameTransaction(instance)
                record = self._apply(transaction.staged, rule, viewer, action, body)
                record.update(action=action, body=fingerprint, principal=[viewer.kind, viewer.uid])
                recovery.record_command(transaction.staged, operation, record)
                transaction.publish(instance)
                try:
                    await self.deps.save_instance(instance)
                except BaseException as exc:
                    transaction.rollback(instance)
                    if isinstance(exc, Exception):
                        logger.exception('Recovery persistence failed: %s', action)
                        raise RecoveryError('SAVE_FAILED', t("webui.services.resource_recovery.save_failed_state_retained")) from exc
                    raise
                return self._result(key, viewer, record, duplicate=False)
        except asyncio.CancelledError:
            raise
        except RecoveryError as exc:
            return _error(exc.code, str(exc))
        except ValueError as exc:
            return _error('RECOVERY_REJECTED', str(exc))
        except Exception:
            logger.exception('Recovery transaction failed: %s', action)
            return _error('SAVE_FAILED', t("webui.services.resource_recovery.save_failed_state_retained"))

    @staticmethod
    def _validate_body(action: str, body: Any) -> None:
        if action not in FIELDS or not isinstance(body, dict) or set(body) - (COMMON | FIELDS[action]):
            raise RecoveryError('INVALID_REQUEST', 'Unknown recovery action or fields')
        _identity(body.get('operation_id'), 'operation_id')
        _identity(body.get('run_id'), 'run_id')
        if type(body.get('expected_revision')) is not int or body['expected_revision'] < 0:
            raise RecoveryError('INVALID_REQUEST', 'expected_revision must be a nonnegative integer')

    @staticmethod
    def _require_actor(instance: Any, uid: Any) -> str:
        uid = _identity(uid, 'actor_uid')
        reason = eligibility(instance, uid)
        if reason:
            raise RecoveryError('INELIGIBLE_ACTOR', reason)
        return uid

    def _apply(self, instance: Any, rule: Any, viewer: Viewer, action: str, body: dict) -> dict:
        selected = policy(rule)
        operation = body['operation_id']
        # Old owner-authenticated web tables may predate gm_uid. This is a
        # server audit principal, never an actor or a client-provided identity.
        authorizer = viewer.uid or instance.gm_uid or 'authenticated-web-owner'
        tick = clock_to_minutes(world_clock(instance.world_state))
        if action == 'request':
            uid = body.get('actor_uid', viewer.uid)
            if not viewer.is_gm and uid != viewer.uid:
                raise RecoveryError('NOT_SOURCE_OWNER', t("webui.services.resource_recovery.request_own_character_only"))
            uid = self._require_actor(instance, uid)
            kind = body.get('kind')
            if not isinstance(kind, str) or kind not in {'overnight', 'therapy'}:
                raise RecoveryError('INVALID_REQUEST', 'Request overnight rest or therapy')
            contract = requirements(rule, kind)
            if contract['resource_key'] not in managed_resources(rule):
                raise ValueError('This rule does not declare SAN recovery')
            self._require_resource(instance, uid, contract['resource_key'])
            healer = skill = ''
            if kind == 'therapy':
                healer = self._require_actor(instance, body.get('healer_uid'))
                skill = _identity(body.get('skill_id'), 'skill_id')
                if skill not in contract['skills']:
                    raise ValueError('This treatment skill is not allowed by the recovery policy')
                skill_value(rule, instance.get_character_sheet(healer), skill)
            elif body.get('healer_uid') or body.get('skill_id'):
                raise ValueError('Overnight rest cannot include a treatment check')
            if any(row['run_id'] == instance.run_id and row['actor_uid'] == uid and row['kind'] == kind
                   and row['status'] in {'pending', 'approved'} for row in recovery.view(instance)['sources'].values()):
                raise ValueError('An unresolved recovery request already exists')
            source = recovery.new_source(instance, kind, uid, healer_uid=healer, skill_id=skill,
                                         started_tick=tick, policy_snapshot=selected)
            return {'source_ids': [source['id']]}
        if action == 'reward':
            uid = self._require_actor(instance, body.get('actor_uid'))
            resource = body.get('resource_key')
            if not isinstance(resource, str) or resource not in managed_resources(rule):
                raise ValueError('Reward requires a declared SAN or Luck resource')
            reason = _identity(body.get('reason'), 'reward reason')
            self._require_resource(instance, uid, resource)
            source = recovery.new_source(instance, 'reward', uid, resource_key=resource,
                                         resource_delta=reward_delta(body.get('resource_delta')), reason=reason,
                                         started_tick=tick, policy_snapshot=selected)
            recovery.approve_source(instance, source['id'], authorizer)
            return {'source_ids': [source['id']]}
        if action == 'luck':
            requirements(rule, 'luck')
            if 'luck' not in managed_resources(rule):
                raise ValueError('This rule does not declare Luck recovery')
            identity = _identity(body.get('intermission_id'), 'intermission_id')
            receipt = next((row for row in intermissions(instance) if row['id'] == identity), None)
            if receipt is None:
                raise ValueError('Choose an existing intermission after a played round')
            origin = f"intermission:{instance.run_id}:{receipt['round']}"
            if any(row['kind'] == 'luck' and row['origin_id'] == origin
                   for row in recovery.view(instance)['sources'].values()):
                raise ValueError('This intermission already granted its Luck recovery opportunity')
            identities = []
            for uid in instance.players:
                if eligibility(instance, uid) or type(instance.get_character_sheet(uid).get('luck')) is not int:
                    continue
                source = recovery.new_source(instance, 'luck', uid, origin_id=origin, started_tick=tick,
                                             policy_snapshot=selected)
                recovery.approve_source(instance, source['id'], authorizer)
                identities.append(source['id'])
            if not identities:
                raise ValueError('No eligible characters have Luck')
            return {'source_ids': identities}
        identity = _identity(body.get('source_id'), 'source_id')
        source = recovery.view(instance)['sources'].get(identity)
        if source is None or source['run_id'] != instance.run_id:
            raise ValueError('Recovery source is missing or belongs to another run')
        if not viewer.is_gm and source['actor_uid'] != viewer.uid:
            raise RecoveryError('NOT_SOURCE_OWNER', t("webui.services.resource_recovery.claim_own_recovery_only"))
        if action == 'reject':
            if source['status'] != 'pending':
                raise ValueError('Only pending requests can be rejected')
            recovery.reject_source(instance, identity, authorizer)
            return {'source_ids': [identity]}
        minutes = 0
        events = []
        if action == 'approve':
            if source['status'] != 'pending' or source['kind'] not in {'overnight', 'therapy'}:
                raise ValueError('Only a pending rest or treatment request can be approved')
            if source['policy'] != selected:
                raise ValueError('The requested recovery policy has changed')
            self._require_actor(instance, source['actor_uid'])
            if source['kind'] == 'therapy':
                self._require_actor(instance, source['healer_uid'])
            recovery.approve_source(instance, identity, authorizer)
            duration = requirements(rule, source['kind'])['duration']
            minutes = max(0, duration - (tick - source['started_tick']))
            remaining = minutes
            while remaining:
                step = min(remaining, MAX_ADVANCE_MINUTES)
                outcome = advance_world_time(instance, step, source_round=progression_state.round_value(instance))
                if self.deps.advance_adventure_world is not None:
                    self.deps.advance_adventure_world(instance)
                queue_world_memory(instance, outcome.get('events') or [],
                                   round_number=progression_state.round_value(instance))
                events.append(outcome)
                remaining -= step
        elif source['status'] != 'approved' or source['kind'] not in {'reward', 'luck'}:
            raise ValueError('This source is not an approved, unclaimed reward or Luck opportunity')
        recovery.settle(instance, rule, identity, operation)
        return {'source_ids': [identity], 'receipt_id': identity,
                'time_advanced_minutes': minutes, 'world_events': events}

    @staticmethod
    def _require_resource(instance: Any, uid: str, key: str) -> None:
        if type(instance.get_character_sheet(uid).get(key)) is not int:
            raise ValueError('Character has no authoritative recovery resource')

    def _result(self, key: str, viewer: Viewer, record: dict, *, duplicate: bool) -> dict:
        result = self.get(key, viewer)
        result['duplicate'] = duplicate
        identities = record['source_ids']
        result['source_ids'] = deepcopy(identities)
        if len(identities) == 1:
            result['source_id'] = identities[0]
        if record.get('receipt_id'):
            instance = self.deps.get_instance(key)
            receipt = public_receipt(recovery.view(instance)['receipts'][record['receipt_id']])
            receipt['time_advanced_minutes'] = record.get('time_advanced_minutes', 0)
            result.update(receipt_id=receipt['source_id'], receipt=receipt)
        return result
