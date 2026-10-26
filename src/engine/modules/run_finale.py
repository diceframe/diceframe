"""Recoverable finale preparation and immutable run archive.

Preparing is a persisted write barrier, not a fake ENDED state. Cancelling
preparation never rolls rewards. A committed archive and settlement receipt
are published with ENDED in the same aggregate transaction.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from types import SimpleNamespace
from uuid import uuid4

from src.engine import module_hooks, check_decisions
from src.engine.game_state import GameState
from src.engine.module_state import ModuleStateError, ModuleStateSpec, register_module_state
from src.engine.modules import checks, economy_state, adventure_runtime_state
from src.engine.player_control import ensure_control
from src.rulesets.contracts import RunFinaleRuntime

MODULE_NAME = 'run_finale'


def fresh() -> dict[str, Any]:
    return {'schema_version': 1, 'job': None, 'archive': None, 'deliveries': {}}


def ensure(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else fresh()


def _state(instance: Any) -> dict[str, Any]:
    raw = instance.modules.get(MODULE_NAME)
    if raw is None:
        return fresh()
    if not isinstance(raw, dict) or type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        raise ModuleStateError('unsupported run_finale schema')
    if not isinstance(raw.get('deliveries'), dict):
        raise ModuleStateError('invalid run_finale delivery ledger')
    for key, status in (('job', 'preparing'), ('archive', 'ended')):
        value = raw.get(key)
        if value is not None and (not isinstance(value, dict) or value.get('status') != status
                                  or not value.get('id') or not value.get('run_id')):
            raise ModuleStateError(f'invalid run_finale {key}')
    return raw


def _workspace_revision(instance: Any, revisions: dict[str, Any]) -> None:
    state = _state(instance)
    item = state.get('archive') or state.get('job')
    if item:
        revisions[MODULE_NAME] = {'id': item['id'], 'status': item['status'], 'revision': item.get('revision', 0),
                                  'deliveries': state['deliveries']}


def view(instance: Any) -> dict[str, Any]:
    return {**fresh(), **deepcopy(_state(instance))}


def writable(instance: Any) -> bool:
    state = _state(instance)
    return state.get('job') is None and state.get('archive') is None


def _public_actions(instance: Any, entry: dict[str, Any]) -> list[dict[str, str]]:
    """Project new and legacy action fields into one public transcript.

    Historical saves used ``player_actions`` either as a list or as a
    ``uid -> text`` mapping.  New saves use ``actions``.  Treat both as input
    representations, preserve first-seen order, and never expose system/GM
    directives or action metadata.
    """
    candidates: list[tuple[Any, Any]] = []
    for field in ('actions', 'player_actions'):
        raw = entry.get(field)
        if isinstance(raw, dict):
            candidates.extend(raw.items())
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    candidates.append((item.get('user_id'), item.get('text')))
                elif isinstance(item, str):
                    # Legacy list entries without an actor cannot be safely
                    # attributed; keep the text only when it is not a system
                    # directive.
                    candidates.append(('', item))
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    players = getattr(instance, 'players', {}) or {}
    for raw_uid, raw_text in candidates:
        uid = str(raw_uid or '').strip()
        if not isinstance(raw_text, str):
            continue
        text = raw_text.strip()
        if uid == 'system' or text.lstrip().startswith(('【GM指令】', '[GM Directive]')):
            continue
        if uid and uid not in players:
            continue
        if not text:
            continue
        key = (uid, text)
        if key in seen:
            continue
        seen.add(key)
        result.append({'user_id': uid, 'text': text})
    return result


def _project_public_transcript(
    instance: Any, entries: Any, *, narration_field: str,
) -> list[dict[str, Any]]:
    """Canonical public projection for round logs AND persisted finale sources.

    Persisted snapshots are not automatically trusted: old archives may contain
    raw protocol content or extra internal fields. Only round/text/actions are
    returned. The original entries are never mutated or silently migrated.
    """
    # Local import avoids the parser -> context_builder -> game_instance cycle.
    from src.llm.parser import sanitize_narration

    if not isinstance(entries, list):
        return []
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_text = entry.get(narration_field)
        # Never stringify arbitrary objects into player-visible text.
        if not isinstance(raw_text, str):
            continue
        # Preserve the existing admission rule for live round logs. Archived
        # sources may legitimately have empty text but public player actions.
        if narration_field == 'gm_response' and not raw_text:
            continue
        try:
            round_number = int(entry.get('round', 0))
        except (TypeError, ValueError):
            continue
        text = sanitize_narration(raw_text)
        actions = _public_actions(instance, entry)
        if not text and not actions:
            continue
        result.append({'round': round_number, 'text': text, 'actions': actions})
    return result


def public_source(instance: Any) -> list[dict[str, Any]]:
    """Public transcript for a new finale, using the canonical projector."""
    return _project_public_transcript(instance, instance.log, narration_field='gm_response')


def fingerprint(instance: Any) -> str:
    from src.engine.plot_tracker import PlotTracker
    raw = deepcopy(instance.to_dict())
    raw.setdefault('plot_tracker', PlotTracker().to_dict())
    raw['log'] = deepcopy(instance.log)  # Codec can bound inline history.
    raw['modules'].pop(MODULE_NAME, None)
    raw['modules'].pop('session_stats', None)  # AI usage accounting is allowed.
    # Room access is independent administrative state: staged publication keeps
    # its live credentials/settings, and GM credential rotation remains legal
    # while a finale is frozen.
    raw['modules'].pop('room_access', None)
    growth = raw['modules'].get('skill_growth')
    if isinstance(growth, dict):
        growth.pop('entropy', None)  # Durable RNG reservation is not source material.
    for player in raw.get('players', {}).values():
        ensure_control(player)
    adventure = raw['modules'].get('adventure_runtime')
    if isinstance(adventure, dict) and adventure.get('schema_version') == 1:
        adventure['play_mode'] = adventure_runtime_state.derive_play_mode(adventure.get('play_mode'), raw.get('adventure_binding'))
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def blockers(instance: Any, runtime: Any = None) -> list[str]:
    result = []
    if instance.state not in {GameState.ACTIVE_ACTION, GameState.PAUSED}:
        result.append('phase: finish the current round or pause at an action boundary')
    if instance.round_processing_in_flight() or instance._process_lock.locked():
        result.append('processing: wait for the current model/round task')
    if instance.action_queue or instance.pending_actions or instance.ready_players:
        result.append('actions: resolve or explicitly withdraw queued actions')
    if module_hooks.combat_view(instance)['combat_active']:
        result.append('combat: finish combat first')
    if any(r.get('status') in {'pending', 'rolling'} for r in checks.manual_roll_requests(instance)):
        result.append('manual_rolls: resolve or cancel pending dice requests')
    if check_decisions.pending_any(instance):
        result.append('checks: resolve pending luck/push decisions')
    ledger = economy_state.state(instance)
    if any(p.get('status') == 'pending' for p in ledger.get('proposals', [])):
        result.append('economy: resolve or cancel pending proposals/rewards')
    if any(d.get('status') in {'pending', 'reversal_pending'} for d in ledger.get('external_effects_outbox', [])):
        result.append('outbox: deliver pending external effects first')
    if isinstance(runtime, RunFinaleRuntime):
        result.extend(runtime.finale_preflight(instance))
    return result


def prepare(instance: Any, runtime: Any = None, *, public_scene: dict | None = None) -> dict[str, Any]:
    state = view(instance)
    if state['archive']:
        raise ValueError('this run already ended')
    if state['job']:
        return state['job']
    problems = blockers(instance, runtime)
    if problems:
        raise ValueError('; '.join(problems))
    job = {'id': f'finale_{uuid4().hex}', 'run_id': instance.run_id, 'status': 'preparing',
           'revision': 0,
           'source_digest': fingerprint(instance), 'source': public_source(instance),
           'created_at': datetime.now(timezone.utc).isoformat(),
           'epilogue': '', 'recap': '', 'gm_notes': ''}
    # Only player-safe current context is supplied by the application resolver;
    # future plan nodes/GM conditions never become public finale material.
    scene = public_scene or {}
    job['public_scene'] = {
        'current_nodes': [{'name': str(row.get('name') or '')[:160], 'description': str(row.get('description') or '')[:800]}
                          for row in scene.get('current_nodes', [])[:8]],
        'objectives': [{'name': str(row.get('name') or '')[:240], 'status': str(row.get('status') or 'active')[:40]}
                       for row in scene.get('objectives', [])[:8]],
    }
    state['job'] = job
    instance.modules[MODULE_NAME] = state
    return deepcopy(job)


def require_job(instance: Any, job_id: Any) -> tuple[dict, dict]:
    state = view(instance); job = state['job']
    if not job or job['id'] != job_id or job['run_id'] != instance.run_id:
        raise ValueError('stale finale preparation; reload this run')
    if job['source_digest'] != fingerprint(instance):
        raise ValueError('the frozen run changed; cancel preparation and start again')
    return state, job


def materials(instance: Any, body: dict) -> None:
    state, job = require_job(instance, body.get('job_id'))
    if type(body.get('job_revision')) is not int or body['job_revision'] != job['revision']:
        raise ValueError('finale materials changed in another window; reload and review again')
    for key in ('epilogue', 'recap', 'gm_notes'):
        value = body.get(key, job[key])
        if not isinstance(value, str) or len(value) > 80_000:
            raise ValueError('finale materials exceed the text limit')
        job[key] = value.strip()
    job['revision'] += 1
    instance.modules[MODULE_NAME] = state


def cancel(instance: Any, job_id: Any) -> None:
    state = view(instance)
    if state['archive']:
        raise ValueError('committed settlement cannot be cancelled')
    if not state['job'] or state['job']['id'] != job_id:
        raise ValueError('stale finale preparation')
    state['job'] = None
    instance.modules[MODULE_NAME] = state


def commit(instance: Any, body: dict, rule: Any, runtime: Any = None) -> dict:
    from src.engine.modules import skill_growth
    state = view(instance)
    if state['archive']:
        if state['archive']['id'] != body.get('job_id'):
            raise ValueError('this run already has a different settlement')
        return state['archive']
    # The kernel owns settlement semantics; callers cannot bypass the
    # explicit policy diagnostic by invoking commit directly.  Keep the
    # idempotent archived result above this check so retries remain safe.
    skill_growth.require_supported_policy(rule)
    state, job = require_job(instance, body.get('job_id'))
    if type(body.get('job_revision')) is not int or body['job_revision'] != job['revision']:
        raise ValueError('reviewed finale materials are stale; reload and review again')
    if body.get('confirmed') is not True or not job['epilogue'] or not job['recap']:
        raise ValueError('review/save the public epilogue and whole-run recap, then explicitly confirm')
    receipt: dict[str, Any] = {'id': job['id'], 'run_id': instance.run_id,
                              'rule_id': instance.rule_id, 'adventure_binding': deepcopy(instance.adventure_binding),
                              'source_digest': job['source_digest'], 'automatic_san_or_money': 'unsupported; use explicit rule/GM actions before preparing'}
    if isinstance(runtime, RunFinaleRuntime):
        receipt['ruleset'] = runtime.settle_finale(instance, job['id'])
    if skill_growth.supported(rule):
        receipt['skill_growth'] = skill_growth.settle(instance, rule, job['id'], expected_run=instance.run_id)
    else:
        receipt['skill_growth'] = {'supported': False}
    archive = {**job, 'status': 'ended', 'receipt': receipt,
               'ended_at': datetime.now(timezone.utc).isoformat(),
               'character_names': {uid: str(player.get('character_name') or uid) for uid, player in instance.players.items()},
               'characters': {uid: deepcopy(instance.get_character_sheet(uid)) for uid in instance.players}}
    state.update(job=None, archive=archive)
    instance.modules[MODULE_NAME] = state
    instance.state = GameState.ENDED
    return deepcopy(archive)


def project(
    instance: Any, *, is_gm: bool, uid: str = '', include_public_source: bool = False,
) -> dict[str, Any]:
    """Role-aware finale view; public exports explicitly request source rows.

    The canonical projector is also used for already-persisted legacy sources.
    The default lightweight view does not re-scan large transcripts on every
    status read, and the privileged GM workspace retains its original view.
    """
    state = view(instance)
    raw = state['archive'] or state['job']
    if not raw:
        return {'status': 'playing'}
    result = {k: deepcopy(raw[k]) for k in ('id', 'run_id', 'status', 'created_at', 'revision')}
    if state['archive']:
        source = (
            _project_public_transcript(instance, raw.get('source'), narration_field='text')
            if include_public_source else None
        )
        rounds = [row['round'] for row in (source if source is not None else raw['source'])]
        result.update(epilogue=raw['epilogue'], recap=raw['recap'], ended_at=raw['ended_at'],
                      source_rounds=rounds)
        if source is not None:
            result['source'] = source
        if uid in raw['characters']:
            result['character'] = deepcopy(raw['characters'][uid])
    if is_gm:
        result = deepcopy(raw)
    return result


def carry(instance: Any, uid: str) -> dict[str, Any]:
    archive = view(instance)['archive']
    if not archive or uid not in archive['characters']:
        raise ValueError('settled character not found')
    # Fresh character-card identity; no shared-library overwrite or credentials.
    sheet = deepcopy(archive['characters'][uid])
    return {**sheet, 'schema_version': 2, 'format': 'diceframe-character-carry', 'format_version': 1,
            'id': f'carry_{archive["id"]}_{hashlib.sha256(uid.encode()).hexdigest()[:16]}',
            'character_name': str(instance.players.get(uid, {}).get('character_name') or uid),
            'rule_id': instance.rule_id, 'source': 'Settled adventure character',
            'carry_receipt': {'run_id': archive['run_id'], 'receipt_id': archive['id'], 'rule_id': instance.rule_id},
            'character_sheet': sheet}


def package_files(payload: dict[str, Any]) -> dict[str, bytes]:
    """GM save-package supplements from the same persisted, scrubbed snapshot.

    Public recap downloads still use the role-filtered service. A full save
    package is GM-only and includes explicitly separate private notes/receipts.
    File names use hashes so imported actor IDs cannot become archive paths.
    """
    snapshot = SimpleNamespace(modules=payload.get('modules') or {},
                               players=payload.get('players') or {}, rule_id=payload.get('rule_id', ''))
    archive = view(snapshot)['archive']
    if not archive:
        return {}

    def encode(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')

    files = {'finale/epilogue.md': archive['epilogue'].encode('utf-8'),
             'finale/recap.md': archive['recap'].encode('utf-8'),
             'finale/gm-notes.md': archive['gm_notes'].encode('utf-8'),
             'finale/source.json': encode(archive['source']),
             'finale/receipt.json': encode(archive['receipt'])}
    characters = []
    for uid in archive['characters']:
        path = f'finale/characters/{hashlib.sha256(uid.encode()).hexdigest()}.json'
        files[path] = encode(carry(snapshot, uid))
        characters.append({'actor_uid': uid, 'path': path})
    manifest = {'format': 'diceframe-run-archive', 'format_version': 1,
                'run_id': archive['run_id'], 'receipt_id': archive['id'],
                'characters': characters,
                'sha256': {path: hashlib.sha256(content).hexdigest() for path, content in files.items()}}
    files['finale/manifest.json'] = encode(manifest)
    return files


def record_delivery(instance: Any, delivery_id: str, delivery: dict) -> None:
    state = view(instance)
    if not state['archive']:
        raise ValueError('only a settled run can carry characters')
    state['deliveries'][delivery_id] = deepcopy(delivery)
    instance.modules[MODULE_NAME] = state


def reset_preflight(instance: Any) -> None:
    state = view(instance)
    if state['job'] or state['archive']:
        raise ValueError('preserve this archive; carry settled characters into a separate new game')


def _new_run_preflight(instance: Any) -> None:
    view(instance)


def _read(instance: Any, kind: str) -> dict | None:
    if kind != module_hooks.RUN_LIFECYCLE_VIEW:
        return None
    raw = project(instance, is_gm=False)
    return {key: raw[key] for key in ('status', 'id', 'revision') if key in raw}


register_module_state(ModuleStateSpec(MODULE_NAME, 1, fresh, ensure))
module_hooks.register(module_hooks.GM_WORKSPACE_REVISIONS, MODULE_NAME, _workspace_revision)
module_hooks.register(module_hooks.STATE_READ, MODULE_NAME, _read)
module_hooks.register(module_hooks.INSTANCE_NEW_RUN_PREFLIGHT, MODULE_NAME, _new_run_preflight)
