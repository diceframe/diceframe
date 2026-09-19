import pytest

from src.memory.delta import MemoryStore


@pytest.mark.asyncio
async def test_memory_entry_can_be_corrected_and_forgotten(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.open()
    try:
        await store.apply_delta("game", {"add": [{"entity": "导师", "relation": "住在", "value": "北城"}]}, 1)
        entry = store.list_entries("game", 10, viewer_is_gm=True)[0]

        assert await store.edit_entry("game", entry["id"], {"value": "南城", "confidence": 0.8})
        assert store.list_entries("game", 10, viewer_is_gm=True)[0]["value"] == "南城"
        assert await store.forget_entry("game", entry["id"])
        assert store.list_entries("game", 10, viewer_is_gm=True) == []
    finally:
        store.close()
