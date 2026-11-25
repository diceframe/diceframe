"""Application-owned action contracts shared by UI adapters and agents.

Registration is explicit at application composition, never inferred from Python
members or HTTP routes. Domain handlers retain authorization and state authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Callable, Iterable


@dataclass(frozen=True)
class ActionAssessment:
    """Owner-derived impact; never supplied by model arguments or chat text."""

    effect: str
    requires_approval: bool = False
    targets: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {'effect': self.effect, 'requires_approval': self.requires_approval,
                'targets': deepcopy(list(self.targets))}


@dataclass(frozen=True)
class PreparedAction:
    """An immutable reviewable command, with owner-private execution input."""

    guard: str | None
    preview: dict
    payload: dict
    assessment: ActionAssessment


@dataclass(frozen=True)
class ApplicationAction:
    name: str
    label: str
    schema: dict
    handler: Callable
    mutation: bool = False
    creates_draft: bool = False
    uses_draft: bool = False
    guard: Callable | None = None
    preview: Callable | None = None
    domain: str = 'application'
    effect: str = 'read'
    version: int = 1
    automatic: bool = False
    instructions: str = ''
    authorize: Callable | None = None
    prepare: Callable | None = None
    execute: Callable | None = None
    resumable: bool = False

    @property
    def effective_effect(self) -> str:
        return 'write' if self.mutation and self.effect == 'read' else self.effect

    def descriptor(self) -> dict:
        return {'name': self.name, 'label': self.label, 'description': self.label,
                'parameters': deepcopy(self.schema), 'requires_confirmation': self.mutation,
                'domain': self.domain, 'effect': self.effective_effect, 'version': self.version,
                'automatic': self.automatic, 'instructions': self.instructions,
                'prepared_execution': self.prepare is not None, 'resumable_execution': self.resumable}

    @property
    def contract_digest(self) -> str:
        return _digest(self.descriptor())


def _digest(value) -> str:
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                separators=(',', ':')).encode()).hexdigest()


#: Ruleset-owned tools live under one domain per runtime.  Every other
#: domain belongs to the product itself and stays visible regardless of which
#: game is open.
RULESET_DOMAIN_PREFIX = 'ruleset-'


def visible_domain(domain: str, visible_domains: frozenset[str] | None) -> bool:
    """Whether one action belongs to a domain this session may see.

    ``None`` means "not scoped": every domain is visible, so every existing
    caller keeps its exact behaviour.  A concrete set filters *only* ruleset
    domains — a rule scope answers "which rule owns this tool", not "which
    product features are enabled", so global domains are never removed by it.
    """

    if visible_domains is None:
        return True
    return not domain.startswith(RULESET_DOMAIN_PREFIX) or domain in visible_domains


class ActionRegistry:
    _MAX_SEARCH_OFFSET = 1_000_000
    _DISCOVERY_HINT = (
        "没有文本匹配不代表没有能力；请使用空 query + domain 浏览目录，"
        "或使用 describe_capability 查询 canonical name。"
    )

    def __init__(self, actions: Iterable[ApplicationAction] = ()):
        self._actions: dict[str, ApplicationAction] = {}
        self._sources: dict[str, Callable] = {}
        self._source_names: dict[str, set[str]] = {}
        self._source_errors: set[str] = set()
        for action in actions:
            self.register(action)

    @staticmethod
    def _validate(action):
        if (not isinstance(action, ApplicationAction) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,95}', action.name)
                or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', action.domain)
                or action.effect not in {'read', 'write', 'disclosure', 'destructive', 'external'}
                or type(action.version) is not int or action.version < 1
                or not isinstance(action.schema, dict) or action.schema.get('type') != 'object'
                or not callable(action.handler)):
            raise ValueError('Invalid application action contract')
        if ((action.prepare is not None and (not action.mutation or not callable(action.prepare)))
                or (action.execute is not None and (action.prepare is None or not callable(action.execute)))
                or type(action.resumable) is not bool or (action.resumable and action.execute is None)):
            raise ValueError('Prepared execution requires an owning mutation planner')
        if action.automatic and (not action.mutation or action.effective_effect != 'write' or not action.guard):
            raise ValueError('Automatic actions require a guarded ordinary write')

    def register(self, action: ApplicationAction) -> None:
        self._validate(action)
        if action.name in self._actions:
            raise ValueError('duplicate application action')
        self._actions[action.name] = replace(action, schema=deepcopy(action.schema))

    def replace(self, action: ApplicationAction) -> None:
        self._validate(action)
        previous = self._actions.get(action.name)
        if previous is None or action.version <= previous.version:
            raise ValueError('Replacement requires an existing action and a newer contract version')
        self._actions[action.name] = replace(action, schema=deepcopy(action.schema))

    def unregister(self, name: str) -> None:
        self._actions.pop(name, None)

    def register_source(self, name: str, provider: Callable) -> None:
        """Attach a trusted owner of a changing action set, such as PluginHost."""
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', name) or not callable(provider):
            raise ValueError('Invalid application action source')
        if name in self._sources:
            raise ValueError('duplicate application action source')
        self._sources[name] = provider
        self._source_names[name] = set()

    def refresh_sources(self) -> None:
        for name, provider in self._sources.items():
            previous = self._source_names[name]
            try:
                actions = tuple(provider())
                for action in actions:
                    self._validate(action)
                identities = {action.name for action in actions}
                if len(identities) != len(actions) or identities & (set(self._actions) - previous):
                    raise ValueError('Action source conflicts with another owner')
            except (ValueError, TypeError, RuntimeError):
                for identity in previous:
                    self._actions.pop(identity, None)
                self._source_names[name] = set()
                self._source_errors.add(name)
                continue
            for identity in previous - identities:
                self._actions.pop(identity, None)
            for action in actions:
                self._actions[action.name] = replace(action, schema=deepcopy(action.schema))
            self._source_names[name] = identities
            self._source_errors.discard(name)

    def get(self, name: str) -> ApplicationAction | None:
        return self._actions.get(name)

    def values(self) -> tuple[ApplicationAction, ...]:
        return tuple(self._actions.values())

    @property
    def revision(self) -> str:
        return _digest([action.descriptor() for action in sorted(self.values(), key=lambda item: item.name)])

    def describe(self, name: str, *,
                 visible_domains: frozenset[str] | None = None) -> dict:
        action = self.get(name)
        # 不可见与不存在返回同一个错误：报“存在但你不能看”本身就是泄漏。
        if action is None or not visible_domain(action.domain, visible_domains):
            raise ValueError('Application capability unavailable')
        return action.descriptor()

    def search(self, query: str = '', domain: str = '', *, limit: int = 80,
               offset: int = 0,
               visible_domains: frozenset[str] | None = None) -> dict:
        if not isinstance(query, str) or len(query) > 200 or not isinstance(domain, str) or len(domain) > 40:
            raise ValueError('Invalid capability query')
        if (type(offset) is not int or offset < 0 or
                offset > self._MAX_SEARCH_OFFSET):
            raise ValueError('Invalid capability search offset')
        words = query.casefold().split()
        matches = []
        domain_counts: dict[str, int] = {}
        for action in sorted(self.values(), key=lambda item: item.name):
            # 过滤必须早于统计与分页：否则域计数与 total 仍会把不可见工具
            # 报出去，等于只藏了列表、没藏存在性。
            if not visible_domain(action.domain, visible_domains):
                continue
            domain_counts[action.domain] = domain_counts.get(action.domain, 0) + 1
            haystack = f'{action.name} {action.label} {action.domain} {action.instructions}'.casefold()
            if (not domain or action.domain == domain) and all(word in haystack for word in words):
                item = action.descriptor()
                item.pop('parameters')
                matches.append(item)
        page = matches[offset:offset + limit]
        result = {'ok': True, 'revision': self.revision, 'capabilities': page,
                'unavailable_sources': sorted(self._source_errors),
                'total': len(matches), 'offset': offset,
                'has_more': offset + len(page) < len(matches),
                'domains': [{'domain': name, 'count': domain_counts[name]}
                            for name in sorted(domain_counts)]}
        if query and not matches:
            result['discovery_hint'] = self._DISCOVERY_HINT
        return result
