"""Owner-private, transactional recovery of authored lorebook content.

Plans contain metadata, never before-images. Canonical snapshots and journal
rows commit in the same owning store transaction as the content mutation.
"""
from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.webui.services.assistant_agent import _digest
from src.localization.messages import t

_MAX_SNAPSHOT_BYTES = 512 * 1024
_JSON_ENTRY_FIELDS = frozenset({
    'keywords', 'triggers_recursive', 'visible_to', 'connected_to', 'secondary_keys',
    'activation_conditions', 'provenance', 'extensions',
})
_LABELS = {
    'write_entries': '修改资料条目', 'set_book_enabled': '启停资料册',
    'set_entries_enabled': '启停资料条目', 'archive_entries': '资料条目移入回收站',
    'archive_book': '资料册移入回收站',
    'world_fields': '修改世界叙事', 'card_fields': '修改角色叙事',
}
_VISIBILITY_GROUP_KIND = 'visibility_plan_book'
_OPERATION_ID_RE = re.compile(r'[a-f0-9]{32}')


@dataclass(frozen=True)
class ContentRecoveryDependencies:
    store: Any
    list_games: Callable
    get_game: Callable
    world_definition: Callable
    save_entry: Callable


class ContentRecoveryService:
    def __init__(self, deps: ContentRecoveryDependencies, metadata=None):
        self.deps = deps
        self.store = deps.store
        self.metadata = metadata

    @staticmethod
    def _owner(owner: str) -> str:
        if not isinstance(owner, str) or not owner:
            raise PermissionError('Owner identity required')
        return _digest(owner)

    @staticmethod
    def _ids(identities: list[str], maximum=10) -> list[str]:
        if (not isinstance(identities, list) or not 1 <= len(identities) <= maximum
                or any(not isinstance(value, str) or not value.strip() or len(value) > 200 for value in identities)
                or len(set(identities)) != len(identities)):
            raise ValueError(t("webui.services.content_recovery.select_explicit_entries"))
        return identities

    def _snapshot(self, book_id: str, entry_ids: list[str] | None = None, *, whole=False) -> dict | None:
        snapshot = self.store.snapshot_lorebook(book_id)
        if snapshot is not None and not whole:
            snapshot['entries'] = [row for row in snapshot['entries'] if row['id'] in (entry_ids or [])]
        return snapshot

    def _book(self, book_id: str) -> dict:
        snapshot = self._snapshot(book_id)
        if snapshot is None:
            raise ValueError(t("webui.services.content_recovery.book_missing_or_trashed"))
        return snapshot

    def _authored(self, snapshot: dict, *, whole=False) -> None:
        book, bindings = snapshot['book'], snapshot['bindings']
        primary = [row for row in bindings if row.get('role') == 'primary']
        if whole and primary:
            raise ValueError(t("webui.services.content_recovery.main_book_not_trashable_by_agent"))
        kind = str(book.get('source_kind') or '')
        if primary:
            for binding in primary:
                if binding.get('scope_kind') != 'world':
                    raise ValueError(t("webui.services.content_recovery.source_editability_unproven"))
                definition = self.deps.world_definition(binding['scope_id'])
                source = definition.get('world') or {}
                if source.get('source') != 'user' or source.get('editable') is not True:
                    raise ValueError(t("webui.services.content_recovery.builtin_or_plugin_copy_first"))
        elif kind not in {'native', 'user', 'device'}:
            raise ValueError(t("webui.services.content_recovery.protected_source_copy_first"))

    def _targets(self, book_id: str, entry_ids: list[str], *, protect=True) -> dict:
        identities = self._ids(entry_ids)
        snapshot = self._snapshot(book_id, identities)
        if snapshot is None or {row['id'] for row in snapshot['entries']} != set(identities):
            raise ValueError(t("webui.services.content_recovery.entry_not_in_book"))
        if protect:
            self._authored(snapshot)
            if any(row.get('source_plugin') for row in snapshot['entries']):
                raise ValueError(t("webui.services.content_recovery.plugin_entry_copy_first"))
        return snapshot

    def _affected_games(self, snapshot: dict) -> list[str]:
        bindings = snapshot['bindings']
        affected = {str(row.get('scope_id') or '') for row in bindings if row['scope_kind'] == 'game'}
        for game in self.deps.list_games().get('games', []):
            key = str(game.get('game_key') or '')
            instance = self.deps.get_game(key)
            if instance is None:
                continue
            content = (getattr(instance, 'modules', {}) or {}).get('content_binding', {})
            explicit = isinstance(content, dict) and content.get('book_selection') == 'explicit'
            if any((row['scope_kind'] == 'global' and not explicit)
                   or (row['scope_kind'] == 'world' and not explicit and row['scope_id'] == getattr(instance, 'world_id', ''))
                   or (row['scope_kind'] == 'character' and row['scope_id'] in (getattr(instance, 'players', {}) or {}))
                   for row in bindings):
                affected.add(key)
        return sorted(value for value in affected if value)

    def _unreferenced(self, snapshot: dict) -> None:
        if self._affected_games(snapshot) or any(row['scope_kind'] in {'game', 'character'} for row in snapshot['bindings']):
            raise ValueError(t("webui.services.content_recovery.book_still_referenced"))

    def _no_entry_links(self, entry_ids: list[str]) -> None:
        targets = set(entry_ids)
        for book in self.store.list_lorebooks():
            for entry in self.store.list_book_entries(book['id']):
                links = entry.get('connected_to') or []
                if entry['id'] not in targets and isinstance(links, list) and targets.intersection(links):
                    raise ValueError(t("webui.services.content_recovery.entry_still_referenced"))

    def preview(self, kind: str, book_id: str, entry_ids: list[str] | None = None) -> dict:
        if kind == 'archive_book':
            snapshot = self._snapshot(book_id, whole=True)
            if snapshot is None: raise ValueError(t("webui.services.content_recovery.book_missing"))
            self._authored(snapshot, whole=True)
            if any(row.get('source_plugin') for row in snapshot['entries']):
                raise ValueError(t("webui.services.content_recovery.plugin_entry_copy_first"))
            self._unreferenced(snapshot)
            if len(snapshot['entries']) > 10:
                raise ValueError(t("webui.services.content_recovery.book_operation_too_many_entries"))
            self._no_entry_links([row['id'] for row in snapshot['entries']])
        elif entry_ids:
            snapshot = self._targets(book_id, entry_ids)
            if kind == 'archive_entries': self._no_entry_links(entry_ids)
        else:
            snapshot = self._book(book_id)
            self._authored(snapshot)
        return {
            'target_name': snapshot['book']['name'], 'book_id': book_id,
            'target_count': len(snapshot['entries']) if entry_ids or kind == 'archive_book' else 1,
            'entries': [{'id': row['id'], 'name': row['name']} for row in snapshot['entries']],
            'binding_count': len(snapshot['bindings']), 'affected_games': self._affected_games(snapshot),
            'recovery_hint': '记录修改前内容；可在内容撤销 / 回收站恢复。后续编辑冲突时不会覆盖。',
        }

    def guard(self, kind: str, book_id: str, entry_ids: list[str] | None = None) -> str:
        preview = self.preview(kind, book_id, entry_ids)
        return _digest({'preview': preview, 'snapshot': self._snapshot(book_id, entry_ids, whole=kind == 'archive_book')})

    @staticmethod
    def _bounded(payload: dict) -> None:
        if len(json.dumps(payload, ensure_ascii=False).encode()) > _MAX_SNAPSHOT_BYTES:
            raise ValueError(t("webui.services.content_recovery.snapshot_too_large_split"))

    def _mutate(self, owner: str, kind: str, book_id: str, entry_ids: list[str] | None, action: Callable, *, fields=None, protect=True, visibility_only=False) -> dict:
        owner_key = self._owner(owner)
        with self.store.transaction():
            if protect:
                summary = self.preview(kind, book_id, entry_ids)
            else:
                current = self._book(book_id)
                summary = {'target_name': current['book']['name'], 'book_id': book_id,
                           'target_count': len(entry_ids or []), 'affected_games': self._affected_games(current)}
            before = self._snapshot(book_id, entry_ids, whole=kind == 'archive_book')
            payload = {'summary': summary, 'book_id': book_id, 'entry_ids': entry_ids or [], 'fields': fields or {}, 'before': before}
            if visibility_only:
                payload['visibility_only'] = True
            self._bounded(payload)
            operation_id = uuid.uuid4().hex
            self.store.record_lorebook_operation({'id': operation_id, 'owner': owner_key, 'kind': kind, 'status': 'prepared', 'payload': payload})
            result = action()
            payload['after'] = self._snapshot(book_id, entry_ids, whole=kind == 'archive_book')
            self._bounded(payload)
            if visibility_only:
                # Undo retains a recovered snapshot in the same journal row.
                # Reserve its space before committing a supposedly reversible batch.
                self._bounded({**payload, 'recovered_after': before})
            if not self.store.update_lorebook_operation(operation_id, owner_key, {'status': 'applied', 'payload': payload}):
                raise ValueError(t("webui.services.content_recovery.recovery_record_save_failed"))
        return {'ok': True, 'operation_id': operation_id, 'reversible': True, **(result or {}),
                'navigation': {'route': 'lorebook', 'query': {'book': book_id}}}

    def write_entries(
        self, owner: str, book_id: str, rows: list[dict], *,
        _visibility_only: bool = False, expected_entries_digest: str | None = None,
    ) -> dict:
        self._ids([row.get('id') for row in rows], maximum=16)
        if expected_entries_digest is not None and (
                not isinstance(expected_entries_digest, str) or not expected_entries_digest):
            raise ValueError('expected_entries_digest must be a non-empty string')
        allowed = {'id', 'name', 'content', 'type', 'keywords', 'visible_to'}
        if any(set(row) - allowed for row in rows): raise ValueError('Unsupported entry write fields')
        if _visibility_only and any(
            set(row) != {'id', 'visible_to'} or not isinstance(row['visible_to'], list)
            or any(not isinstance(value, str) or not value.strip() for value in row['visible_to'])
            for row in rows
        ):
            raise ValueError('Visibility recovery only supports audience patches')
        fields = {row['id']: [key for key in row if key != 'id'] for row in rows}
        def apply():
            if (expected_entries_digest is not None
                    and _digest(self.store.list_book_entries(book_id)) != expected_entries_digest):
                raise ValueError(t("webui.services.content_recovery.book_entries_changed"))
            if _visibility_only:
                existing = {row['id'] for row in self.store.list_book_entries(book_id)}
                if any(row['id'] not in existing for row in rows):
                    raise ValueError('Visibility recovery only supports existing entries')
            results = []
            for row in rows:
                result = self.deps.save_entry(book_id, deepcopy(row))
                if result.get('ok') is not True: raise ValueError(result.get('error') or 'Entry write rejected')
                results.append({'id': row['id'], 'ok': True})
            return {'results': results}
        return self._mutate(owner, 'write_entries', book_id, [row['id'] for row in rows], apply, fields=fields, protect=False, visibility_only=_visibility_only)

    def preview_book_visibility(
        self, owner: str, book_id: str, rows: list[dict], expected_entries_digest: str,
    ) -> dict:
        self._owner(owner)
        current = self.store.list_book_entries(book_id)
        if _digest(current) != expected_entries_digest:
            raise ValueError(t("webui.services.content_recovery.book_entries_changed"))
        if len(current) > 1000:
            raise ValueError(t("webui.services.content_recovery.book_visibility_too_many_entries"))
        existing = {str(row.get('id')): row for row in current}
        if (not isinstance(rows, list) or len(rows) > 1000
                or any(not isinstance(row, dict) or set(row) != {'id', 'visible_to'}
                       or not isinstance(row['id'], str) or row['id'] not in existing
                       or not isinstance(row['visible_to'], list)
                       or any(not isinstance(value, str) or not value.strip() for value in row['visible_to'])
                       for row in rows)
                or len({row['id'] for row in rows}) != len(rows)):
            raise ValueError(t("webui.services.content_recovery.visibility_existing_entries_only"))
        changed = [row for row in rows if existing[row['id']].get('visible_to') != row['visible_to']]
        book = self._book(book_id)
        return {'target_name': book['book'].get('name', book_id), 'book_id': book_id,
                'total': len(current), 'changed_count': len(changed),
                'batch_count': (len(changed) + 15) // 16,
                'affected_games': self._affected_games(book)}

    def write_book_visibility(
        self, owner: str, book_id: str, rows: list[dict], expected_entries_digest: str,
        *, operation_id: str | None = None,
    ) -> dict:
        """Set visibility for an entire book through bounded journaled batches.

        ``rows`` contains only existing entry IDs and ``visible_to`` patches;
        the digest fences the complete book so callers may submit only changed
        rows without losing concurrent edits.  All batches share one store
        transaction, while each batch retains its own recovery journal record.
        """
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError(t("webui.services.content_recovery.book_visibility_too_many_entries"))
        if not isinstance(expected_entries_digest, str) or not expected_entries_digest:
            raise ValueError(t("webui.services.content_recovery.book_summary_required"))
        if operation_id is not None and (
                not isinstance(operation_id, str) or not _OPERATION_ID_RE.fullmatch(operation_id)):
            raise ValueError('visibility operation identity must be a stable 32-hex string')
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'id', 'visible_to'}:
                raise ValueError(t("webui.services.content_recovery.book_visibility_fields"))
            if not isinstance(row['id'], str) or not row['id'].strip() or not isinstance(row['visible_to'], list):
                raise ValueError(t("webui.services.content_recovery.book_visibility_entry_format"))
            if any(not isinstance(value, str) or not value.strip() for value in row['visible_to']):
                raise ValueError(t("webui.services.content_recovery.visible_to_not_string_list"))
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError(t("webui.services.content_recovery.book_visibility_duplicate_entries"))

        owner_key = self._owner(owner)
        request_digest = _digest({
            'book_id': book_id, 'rows': rows,
            'expected_entries_digest': expected_entries_digest,
        })
        operation_ids: list[str] = []
        changed_count = 0
        with self.store.transaction():
            # Receipt lookup and the collision check share the owning store
            # lock/transaction with the subsequent child journals.  Two
            # concurrent retries therefore cannot both pass the preflight.
            if operation_id is not None:
                existing = self.store.get_lorebook_operation(operation_id, owner_key)
                if existing is not None:
                    if existing.get('kind') != _VISIBILITY_GROUP_KIND:
                        raise ValueError('visibility operation identity is already used by another operation')
                    payload = existing.get('payload') or {}
                    if payload.get('request_digest') != request_digest:
                        raise ValueError('visibility operation request does not match its durable receipt')
                    if existing.get('status') != 'applied' or not isinstance(payload.get('result'), dict):
                        raise ValueError('visibility operation has no completed durable receipt')
                    return deepcopy(payload['result'])
            self.preview_book_visibility(owner, book_id, rows, expected_entries_digest)
            current = self.store.list_book_entries(book_id)
            current_digest = _digest(current)
            if current_digest != expected_entries_digest:
                raise ValueError(t("webui.services.content_recovery.book_entries_changed"))
            existing = {str(row.get('id')): row for row in current}
            unknown = [row['id'] for row in rows if row['id'] not in existing]
            if unknown:
                raise ValueError(t("webui.services.content_recovery.visibility_existing_entries_only"))
            changed = [row for row in rows if existing[row['id']].get('visible_to') != row['visible_to']]
            if not changed:
                result = {'ok': True, 'operation_ids': [], 'changed_count': 0, 'batch_count': 0,
                          'navigation': {'route': 'lorebook', 'query': {'book': book_id}}}
                if operation_id is not None:
                    result['operation_id'] = operation_id
                    result['request_digest'] = request_digest
                    self.store.record_lorebook_operation({
                        'id': operation_id, 'owner': owner_key, 'kind': _VISIBILITY_GROUP_KIND,
                        'status': 'prepared',
                        'payload': {'request_digest': request_digest, 'book_id': book_id,
                                    'result': result},
                    })
                    if not self.store.update_lorebook_operation(
                            operation_id, owner_key, {'status': 'applied', 'payload': {
                                'request_digest': request_digest, 'book_id': book_id,
                                'result': result,
                            }}):
                        raise ValueError(t("webui.services.content_recovery.visibility_receipt_save_failed"))
                return result
            # Recheck immediately before writing; the transaction lock makes
            # this the authoritative fence for all following batches.
            if _digest(self.store.list_book_entries(book_id)) != expected_entries_digest:
                raise ValueError(t("webui.services.content_recovery.book_entries_changed"))
            if operation_id is not None:
                self.store.record_lorebook_operation({
                    'id': operation_id, 'owner': owner_key, 'kind': _VISIBILITY_GROUP_KIND,
                    'status': 'prepared',
                    'payload': {'request_digest': request_digest, 'book_id': book_id,
                                'result': None},
                })
            for start in range(0, len(changed), 16):
                batch = changed[start:start + 16]
                result = self.write_entries(
                    owner, book_id, batch, _visibility_only=True,
                )
                operation_ids.append(result['operation_id'])
                changed_count += len(batch)
            result = {'ok': True, 'operation_ids': operation_ids, 'changed_count': changed_count,
                      'batch_count': len(operation_ids),
                      'navigation': {'route': 'lorebook', 'query': {'book': book_id}}}
            if operation_id is not None:
                result['operation_id'] = operation_id
                result['request_digest'] = request_digest
                if not self.store.update_lorebook_operation(
                        operation_id, owner_key, {'status': 'applied', 'payload': {
                            'request_digest': request_digest, 'book_id': book_id,
                            'result': result,
                        }}):
                    raise ValueError(t("webui.services.content_recovery.visibility_receipt_save_failed"))
        return result

    def read_book_visibility_receipt(self, owner: str, operation_id: str) -> dict | None:
        """Read the owner-private group receipt used for plan reconciliation.

        Group receipts deliberately have no entry label and are therefore
        omitted from :meth:`list_operations`, which is the normal undo UI.
        """
        if not isinstance(operation_id, str) or not _OPERATION_ID_RE.fullmatch(operation_id):
            raise ValueError('Invalid content operation identity')
        operation = self.store.get_lorebook_operation(operation_id, self._owner(owner))
        if operation is None or operation.get('kind') != _VISIBILITY_GROUP_KIND:
            return None
        if operation.get('status') != 'applied':
            return None
        payload = operation.get('payload') or {}
        result = payload.get('result')
        if not isinstance(result, dict):
            return None
        return deepcopy(result)

    def set_book_enabled(self, owner: str, book_id: str, enabled: bool) -> dict:
        if not isinstance(enabled, bool): raise ValueError('enabled must be boolean')
        return self._mutate(owner, 'set_book_enabled', book_id, None,
            lambda: self.store.update_lorebook(book_id, {'enabled': enabled}) and {}, fields={'book': ['enabled']})

    def set_entries_enabled(self, owner: str, book_id: str, entry_ids: list[str], enabled: bool) -> dict:
        if not isinstance(enabled, bool): raise ValueError('enabled must be boolean')
        def apply():
            for identity in entry_ids:
                if not self.store.update_book_entry(book_id, identity, {'enabled': enabled}): raise ValueError('Entry changed')
        return self._mutate(owner, 'set_entries_enabled', book_id, entry_ids, apply,
                            fields={identity: ['enabled'] for identity in entry_ids})

    def archive_entries(self, owner: str, book_id: str, entry_ids: list[str]) -> dict:
        def apply():
            for identity in entry_ids:
                if not self.store.delete_book_entry(book_id, identity): raise ValueError('Entry changed')
        return self._mutate(owner, 'archive_entries', book_id, entry_ids, apply)

    def archive_book(self, owner: str, book_id: str) -> dict:
        def apply():
            if not self.store.delete_lorebook(book_id): raise ValueError('Book changed')
        return self._mutate(owner, 'archive_book', book_id, None, apply)

    def _operation(self, owner: str, operation_id: str) -> dict:
        if not isinstance(operation_id, str) or not re.fullmatch(r'[a-f0-9]{32}', operation_id):
            raise ValueError('Invalid content operation identity')
        operation = self.store.get_lorebook_operation(operation_id, self._owner(owner))
        if operation is None or operation['status'] != 'applied':
            raise ValueError(t("webui.services.content_recovery.record_missing_or_handled"))
        return operation

    def recovery_preview(self, owner: str, operation_id: str, *, restore=False) -> dict:
        operation = self._operation(owner, operation_id)
        if operation['kind'] in {'world_fields', 'card_fields'}:
            if restore or self.metadata is None: raise ValueError(t("webui.services.content_recovery.use_undo_for_narrative"))
            return self.metadata.recovery_preview(owner, operation_id)
        archived = operation['kind'] in {'archive_entries', 'archive_book'}
        if restore != archived: raise ValueError(t("webui.services.content_recovery.choose_matching_operation"))
        payload = operation['payload']
        current = self._snapshot(payload['book_id'], payload['entry_ids'], whole=operation['kind'] == 'archive_book')
        if operation['kind'] == 'write_entries' and payload.get('visibility_only'):
            def _visibility_snapshot(value):
                if not value:
                    return value
                result = deepcopy(value)
                book = result.get('book') or {}
                book.pop('revision', None)
                book.pop('updated_at', None)
                result['book'] = book
                return result
            current_check = _visibility_snapshot(current)
            after_check = _visibility_snapshot(payload.get('after'))
        else:
            current_check, after_check = current, payload['after']
        if current_check != after_check:
            raise ValueError(t("webui.services.content_recovery.content_or_references_changed"))
        if operation['kind'] == 'write_entries':
            existed = {row['id'] for row in payload['before']['entries']}
            self._no_entry_links([identity for identity in payload['entry_ids'] if identity not in existed])
        return {**deepcopy(payload['summary']), 'operation_id': operation_id,
                'recovery_hint': '仅恢复该记录触及的内容；不会回退存档回合或覆盖后续编辑。'}

    def recovery_guard(self, owner: str, operation_id: str, *, restore=False) -> str:
        operation = self._operation(owner, operation_id)
        if operation['kind'] in {'world_fields', 'card_fields'}:
            if restore or self.metadata is None: raise ValueError(t("webui.services.content_recovery.use_undo_for_narrative"))
            return self.metadata.recovery_guard(owner, operation_id)
        preview = self.recovery_preview(owner, operation_id, restore=restore)
        return _digest({'preview': preview, 'operation': self._operation(owner, operation_id)})

    @staticmethod
    def _field_value(row: dict, field: str):
        value = row[field]
        if field in _JSON_ENTRY_FIELDS and isinstance(value, str): value = json.loads(value)
        return value

    def _recover(self, owner: str, operation_id: str, *, restore=False) -> dict:
        with self.store.transaction():
            self.recovery_preview(owner, operation_id, restore=restore)
            operation = self._operation(owner, operation_id)
            payload, kind = operation['payload'], operation['kind']
            before, book_id = payload['before'], payload['book_id']
            if kind == 'archive_book':
                self.store.restore_lorebook_snapshot(before)
            elif kind == 'archive_entries':
                for row in before['entries']: self.store.restore_lorebook_entry(book_id, row)
            elif kind == 'set_book_enabled':
                self.store.update_lorebook(book_id, {'enabled': bool(before['book']['enabled'])})
            elif kind == 'write_entries' and payload.get('visibility_only'):
                previous = {row['id']: row for row in before['entries']}
                for identity in payload['entry_ids']:
                    row = previous.get(identity)
                    previous_visibility = self._field_value(row, 'visible_to') if row is not None else None
                    if row is None or not self.store.update_book_entry(book_id, identity, {'visible_to': previous_visibility}):
                        raise ValueError('Entry changed')
            elif kind in {'write_entries', 'set_entries_enabled'}:
                previous = {row['id']: row for row in before['entries']}
                for identity in payload['entry_ids']:
                    if identity not in previous:
                        if not self.store.delete_book_entry(book_id, identity): raise ValueError('Entry changed')
                    else:
                        patch = {key: self._field_value(previous[identity], key) for key in payload['fields'][identity]}
                        if not self.store.update_book_entry(book_id, identity, patch): raise ValueError('Entry changed')
            else:
                raise ValueError('Unsupported recovery record')
            payload['recovered_after'] = self._snapshot(book_id, payload['entry_ids'], whole=kind == 'archive_book')
            self._bounded(payload)
            if not self.store.update_lorebook_operation(operation_id, self._owner(owner),
                {'status': 'restored' if restore else 'undone', 'payload': payload}):
                raise ValueError(t("webui.services.content_recovery.recovery_result_save_failed"))
        return {'ok': True, 'operation_id': operation_id, 'navigation': {'route': 'lorebook', 'query': {'book': book_id}}}

    def undo(self, owner: str, operation_id: str) -> dict:
        if self._operation(owner, operation_id)['kind'] in {'world_fields', 'card_fields'}:
            if self.metadata is None: raise ValueError(t("webui.services.content_recovery.narrative_recovery_unavailable"))
            return self.metadata.undo(owner, operation_id)
        return self._recover(owner, operation_id)

    def restore(self, owner: str, operation_id: str) -> dict:
        return self._recover(owner, operation_id, restore=True)

    def list_operations(self, owner: str) -> dict:
        operations = []
        for operation in self.store.list_lorebook_operations(self._owner(owner)):
            if operation['kind'] not in _LABELS: continue
            summary = operation['payload'].get('summary') or {}
            archive = operation['kind'] in {'archive_entries', 'archive_book'}
            applied = operation['status'] == 'applied'
            operations.append({'operation_id': operation['id'], 'kind': operation['kind'],
                'label': _LABELS.get(operation['kind'], '内容操作'), 'status': operation['status'],
                'created_at': operation['created_at'], 'target_name': str(summary.get('target_name') or ''),
                'target_count': int(summary.get('target_count') or 0),
                'can_undo': applied and not archive, 'can_restore': applied and archive})
        return {'ok': True, 'operations': operations}
