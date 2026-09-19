from __future__ import annotations

import pytest

from src.webui.services.memory import MemoryDependencies, MemoryService


class FakeMemoryRepository:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def recall(self, game_key, keywords, limit, offset, *, viewer_is_gm):
        self.calls.append(("recall", game_key, keywords, limit, offset, viewer_is_gm))
        return [{"id": 1, "value": "dragon"}]

    def list_entries(self, game_key, limit, offset, *, viewer_is_gm):
        self.calls.append(("list", game_key, limit, offset, viewer_is_gm))
        return [{"id": 2, "value": "castle"}]

    def count_entries(self, game_key, keyword="", *, viewer_is_gm):
        self.calls.append(("count", game_key, keyword, viewer_is_gm))
        return 1

    async def edit_entry(self, game_key, entry_id, updates):
        self.calls.append(("edit", game_key, entry_id, updates))
        return entry_id == 1

    async def forget_entry(self, game_key, entry_id):
        self.calls.append(("forget", game_key, entry_id))
        return entry_id == 1


def _service(repository: FakeMemoryRepository) -> MemoryService:
    return MemoryService(MemoryDependencies(
        repository=repository,
        parse_game_key=lambda raw: tuple(raw.split("|")),
    ))


def test_memory_listing_uses_canonical_storage_key_and_query_mode():
    repository = FakeMemoryRepository()
    service = _service(repository)

    result = service.list(
        "web|room|bot", "dragon", limit=5, offset=2, viewer_is_gm=True,
    )

    assert result == {
        "memories": [{"id": 1, "value": "dragon"}],
        "total": 1,
    }
    assert repository.calls == [
        ("recall", "('web', 'room', 'bot')", ["dragon"], 5, 2, True),
        ("count", "('web', 'room', 'bot')", "dragon", True),
    ]


@pytest.mark.asyncio
async def test_memory_mutations_preserve_not_found_contract():
    repository = FakeMemoryRepository()
    service = _service(repository)

    assert await service.update("web|room|bot", 1, {"value": "keep"}) == {
        "ok": True,
        "error": "",
    }
    assert await service.delete("web|room|bot", 9) == {
        "ok": False,
        "error": "记忆不存在",
    }
