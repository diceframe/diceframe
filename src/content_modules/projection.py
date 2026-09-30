"""Read-only projection contracts for canonical content.

Content projection is an application/read boundary.  A projector may combine
canonical content with already-resolved view context, but it must return a new
mapping and must not mutate the authority it received.  The broad contract is
kept deliberately small here; the domain-specific ``ContentProjectionService``
belongs to the later projection cutover (Track C PR D).
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Protocol, runtime_checkable

from src.lorebook.resolver import resolve_active_books


@runtime_checkable
class ContentProjection(Protocol):
    """A read-only projector from canonical content to a public view.

    ``context`` is intentionally opaque at this stage.  The Lorebook
    management service uses it for its already-existing binding facts; later
    projection work can add viewer/locale contexts without changing the
    canonical store contract.
    """

    def project(
        self,
        content: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class ContentProjectionService:
    """Canonical read-side projection over Book/Binding ownership.

    The service deliberately returns detached mappings.  It never mutates a
    Book, Entry, Binding, or runtime instance and it does not persist a
    compatibility ``world_id``.  Legacy world-only callers can still use the
    Store facade during the migration, but new runtime views should choose one
    of the explicit context methods below.
    """

    def __init__(self, store: Any, *, load_world_template: Any | None = None) -> None:
        self.store = store
        self.load_world_template = load_world_template

    @staticmethod
    def _filter(entries: list[dict[str, Any]], entry_type: str | None) -> list[dict[str, Any]]:
        if not entry_type:
            return [deepcopy(entry) for entry in entries]
        return [
            deepcopy(entry) for entry in entries
            if str(entry.get("type") or "") == str(entry_type)
        ]

    def for_book(self, book_id: str, *, entry_type: str | None = None) -> list[dict[str, Any]]:
        """Project one Book by its canonical owner."""

        if not self.store or not str(book_id or ""):
            return []
        list_book_entries = getattr(self.store, "list_book_entries", None)
        if callable(list_book_entries):
            return self._filter(list_book_entries(str(book_id)), entry_type)
        # Explicit compatibility boundary for stores predating Book-scoped CRUD.
        return self._filter(
            self.store.list_entries(str(book_id), entry_type) if hasattr(self.store, "list_entries") else [],
            entry_type,
        )

    def for_world_authoring(
        self, world_id: str, *, entry_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Project all Books explicitly bound to a World for authoring views."""

        world_id = str(world_id or "")
        if not world_id or not self.store:
            return []
        book_ids: list[str] = []
        if hasattr(self.store, "list_bindings"):
            book_ids = [
                str(binding.get("book_id") or "")
                for binding in self.store.list_bindings(scope_kind="world", scope_id=world_id)
                if binding.get("book_id")
            ]
        if not book_ids and hasattr(self.store, "primary_world_book_id"):
            book_ids = [str(self.store.primary_world_book_id(world_id))]
        result: list[dict[str, Any]] = []
        for book_id in dict.fromkeys(book_ids):
            result.extend(self.for_book(book_id, entry_type=entry_type))
        return result

    def for_game(
        self,
        instance: Any,
        *,
        viewer_kind: str = "gm",
        viewer_uid: str = "",
        action_actor_uids: list[str] | None = None,
        entry_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Project active Books for one runtime context."""

        if not instance or not self.store:
            return []
        refs = resolve_active_books(
            instance,
            viewer_kind,
            viewer_uid,
            list(action_actor_uids or getattr(instance, "action_actor_uids", []) or []),
            store=self.store,
        ) if hasattr(self.store, "list_bindings") else []
        if not refs:
            return self.for_world_authoring(
                str(getattr(instance, "world_id", "") or ""), entry_type=entry_type,
            ) if not hasattr(self.store, "list_bindings") else []
        result: list[dict[str, Any]] = []
        for ref in refs:
            for entry in self.for_book(ref.book_id, entry_type=entry_type):
                row = deepcopy(entry)
                row["_lorebook_id"] = ref.book_id
                row["_lorebook_order"] = ref.order
                result.append(row)
        return result

    def for_character(
        self, instance: Any, viewer_uid: str, *, entry_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Project content visible to one character seat."""

        return self.for_game(
            instance,
            viewer_kind="character",
            viewer_uid=str(viewer_uid or ""),
            entry_type=entry_type,
        )


__all__ = ["ContentProjection", "ContentProjectionService"]
