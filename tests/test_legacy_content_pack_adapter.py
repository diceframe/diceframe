from __future__ import annotations

from types import SimpleNamespace

from src.content_modules.adapters import LegacyContentPackAdapter
from src.content_modules.receipts import ImportReceiptStore
from src.webui.services import plugins


def test_legacy_adapter_keeps_sync_before_materialization() -> None:
    calls: list[str] = []
    adapter = LegacyContentPackAdapter(
        sync_lorebooks=lambda: calls.append("sync"),
        import_content=lambda plugin_id: calls.append(f"import:{plugin_id}"),
    )

    adapter.materialize("legacy-pack")
    assert calls == ["sync", "import:legacy-pack"]


def test_legacy_adapter_sync_is_explicit_for_startup_compatibility() -> None:
    calls: list[str] = []
    adapter = LegacyContentPackAdapter(
        sync_lorebooks=lambda: calls.append("sync"),
        import_content=lambda plugin_id: calls.append(f"import:{plugin_id}"),
    )

    adapter.sync()
    assert calls == ["sync"]


def test_import_receipt_round_trips_and_merges_objects(tmp_path) -> None:
    store = ImportReceiptStore(tmp_path)
    store.record("pack", source_version="1.0.0", object_type="lorebook_entry", object_id="entry-1")
    store.record("pack", source_version="1.0.0", object_type="character_card", object_id="card-1")
    store.record(
        "pack", source_version="1.1.0", object_type="lorebook_entry", object_id="entry-1", updated=True,
    )

    receipt = store.load("pack")
    assert receipt is not None
    assert receipt.source_kind == "module"
    assert receipt.source_version == "1.0.0"
    assert receipt.created_objects == [
        {"type": "lorebook_entry", "id": "entry-1"},
        {"type": "character_card", "id": "card-1"},
    ]
    assert receipt.updated_objects == [{"type": "lorebook_entry", "id": "entry-1"}]


def test_receipt_cleanup_does_not_guess_unreceipted_objects(tmp_path) -> None:
    class Lore:
        def __init__(self) -> None:
            self.entries = {
                "owned": {"id": "owned", "source_plugin": "pack"},
                "not-receipted": {"id": "not-receipted", "source_plugin": "pack"},
            }

        def list_plugin_worlds(self, _plugin_id):
            return []

        def get_entry(self, entry_id):
            return self.entries.get(entry_id)

        def delete_entry(self, entry_id):
            return self.entries.pop(entry_id, None) is not None

        def delete_entries_by_plugin(self, _plugin_id):
            raise AssertionError("receipt cleanup must not fall back to source_plugin")

    lore = Lore()
    store = plugins.PluginContentStoreDependencies(
        lorebook=lore,
        list_games=lambda: [],
        list_character_cards=lambda: {"cards": []},
        save_character_card=lambda _card: {"ok": True},
        delete_character_card=lambda _card_id: {"ok": True},
        save_entry=lambda _entry: {"ok": True},
    )
    deps = plugins.PluginContentDependencies(
        plugin_host=SimpleNamespace(data_dir=tmp_path),
        store=store,
        portraits=plugins.PluginPortraitDependencies(
            plugin_asset_path=lambda _plugin_id, _path: tmp_path,
            avatar_file=lambda _asset_id: None,
            generated_image_file=lambda _asset_id: None,
            save_avatar_upload=lambda _data, _name: {"ok": True},
        ),
    )
    receipts = ImportReceiptStore(tmp_path)
    receipts.record("pack", object_type="lorebook_entry", object_id="owned")
    receipts.record("pack", object_type="lorebook_entry", object_id="preexisting", updated=True)
    lore.entries["preexisting"] = {"id": "preexisting", "source_plugin": "pack"}

    result = plugins.cleanup_plugin_lorebook(deps, "pack")

    assert result["removed"] == 1
    assert "owned" not in lore.entries
    assert "not-receipted" in lore.entries
    assert "preexisting" in lore.entries
    assert receipts.load("pack") is None
