"""issue #700 回归：内容包 ``contributes.lorebooks`` 的落地路径。

声明被识别、注册进 catalog，但玩家侧三处（用户书库、模块详情页、开局候选）
全部拿不到。覆盖三条修复链路：

- legacy_autoimport 启用即整本物化为 module 书（含卸载清理与用户改动保留）；
- 模块详情页合并声明资源（catalog 模式不落地也可见）；
- 创建事务里 module 来源的 book binding 按需物化。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.lorebook.module_books import module_book_id
from src.lorebook.store import LorebookStore
from src.plugin_host import PluginHost
from src.webui.services import modules as module_service
from src.webui.services import plugins as plugin_service

from plugin_host_common import write_plugin

PLUGIN_ID = "lore-pack"
BOOK_KEY = "dagon-archive"


def _write_lorebook_pack(plugins_dir: Path, *, delivery: str = "legacy_autoimport") -> PluginHost:
    write_plugin(
        plugins_dir, PLUGIN_ID, plugin_type="content-pack", entrypoint=False,
        manifest_extra={
            "content_delivery_mode": delivery,
            "contributes": {"lorebooks": ["content/lorebooks/*.json"]},
        },
    )
    books_dir = plugins_dir / PLUGIN_ID / "content" / "lorebooks"
    books_dir.mkdir(parents=True)
    (books_dir / f"{BOOK_KEY}.json").write_text(json.dumps({
        "id": BOOK_KEY,
        "name": "Dagon Archive",
        "description": "守秘人档案",
        "entries": [
            {"id": "clue-harbor", "name": "港口线索", "content": "渔船没有回来。", "keywords": ["港口", "渔船"]},
            {"id": "npc-keeper", "name": "灯塔看守", "content": "他从不提那晚的事。", "keywords": ["看守"], "visible_to": ["gm"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    # 贡献注册只看 discover 时的 enabled 状态（status=active）。
    data_dir = plugins_dir.parent / "data"
    (data_dir / PLUGIN_ID).mkdir(parents=True, exist_ok=True)
    (data_dir / PLUGIN_ID / "config.json").write_text(
        json.dumps({"enabled": True}), encoding="utf-8",
    )
    return PluginHost(plugins_dir, data_dir)


def _content_dependencies(host: PluginHost, lorebook: LorebookStore) -> plugin_service.PluginContentDependencies:
    return plugin_service.PluginContentDependencies(
        plugin_host=host,
        store=plugin_service.PluginContentStoreDependencies(
            lorebook=lorebook,
            list_games=lambda: [],
            list_character_cards=lambda: {"cards": []},
            save_character_card=lambda card: {"ok": True},
            delete_character_card=lambda card_id: {"ok": True},
            save_entry=lambda entry: {"ok": True},
        ),
        portraits=plugin_service.PluginPortraitDependencies(
            plugin_asset_path=lambda plugin_id, relative_path: host.public_asset_path(
                plugin_id, relative_path,
            ),
            avatar_file=lambda asset_id: None,
            generated_image_file=lambda asset_id: None,
            save_avatar_upload=lambda data, name: {"ok": False},
        ),
    )


def _module_dependencies(host: PluginHost, lorebook: LorebookStore) -> module_service.ModuleDependencies:
    return module_service.ModuleDependencies(
        plugin_host=host,
        adventure_registry=None,
        lorebook_store=lorebook,
    )


@pytest.fixture()
def store(tmp_path):
    lorebook = LorebookStore(tmp_path / "lorebook.db")
    lorebook.open()
    try:
        yield lorebook
    finally:
        lorebook.close()


@pytest.fixture()
def host(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugin_host = _write_lorebook_pack(plugins_dir)
    plugin_host.discover()
    return plugin_host


def test_autoimport_materializes_declared_lorebook_as_one_module_book(host, store):
    dependencies = _content_dependencies(host, store)

    plugin_service._autoimport_plugin_content(dependencies, PLUGIN_ID)

    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)
    book = store.get_lorebook(book_id)
    assert book is not None, "声明的 lorebook 必须整本落地为用户书库的一本书"
    assert book["source_kind"] == "module"
    assert book["source_id"] == PLUGIN_ID
    assert book["name"] == "Dagon Archive"
    entries = store.list_book_entries(book_id)
    assert len(entries) == 2
    assert all(str(entry.get("book_id") or "") == book_id for entry in entries)

    # 幂等：启用两遍写一次（书与条目都不重复）。
    plugin_service._autoimport_plugin_content(dependencies, PLUGIN_ID)
    assert store.get_lorebook(book_id) is not None
    assert len(store.list_book_entries(book_id)) == 2
    assert [row["id"] for row in store.list_lorebooks()] == [book_id]


def test_autoimport_binds_materialized_book_to_pack_world(host, store, tmp_path):
    world_dir = tmp_path / "plugins" / PLUGIN_ID / "content" / "worlds"
    world_dir.mkdir(parents=True)
    (world_dir / "pacfic-shoal.json").write_text(json.dumps({
        "world_id": "pacfic_shoal", "world_name": "太平洋浅滩",
        "world_setting": "海岸惊魂", "starter_scene": "码头",
    }, ensure_ascii=False), encoding="utf-8")
    manifest_path = tmp_path / "plugins" / PLUGIN_ID / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contributes"]["world_templates"] = ["content/worlds/*.json"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    host2 = PluginHost(tmp_path / "plugins", tmp_path / "data")
    host2.discover()

    store.create_world("pacfic_shoal", "太平洋浅滩")
    plugin_service._autoimport_plugin_content(_content_dependencies(host2, store), PLUGIN_ID)

    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)
    bindings = [b for b in store.list_bindings(scope_kind="world", scope_id="pacfic_shoal")
                if b.get("book_id") == book_id]
    assert bindings, "包内带世界时，物化的书要挂 world 域 runtime binding（开局即候选）"
    assert bindings[0]["role"] == "runtime"


def test_uninstall_cleanup_removes_module_book(host, store):

    dependencies = _content_dependencies(host, store)
    plugin_service._autoimport_plugin_content(dependencies, PLUGIN_ID)
    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)

    result = plugin_service.cleanup_plugin_lorebook(dependencies, PLUGIN_ID)

    assert result["ok"] is True
    assert result["books_removed"] == 1
    assert store.get_lorebook(book_id) is None
    assert store.list_book_entries(book_id) == []


def test_uninstall_keeps_module_book_user_edited(host, store):

    dependencies = _content_dependencies(host, store)
    plugin_service._autoimport_plugin_content(dependencies, PLUGIN_ID)
    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)
    entry = store.list_book_entries(book_id)[0]
    store.update_book_entry(book_id, entry["id"], {
        "name": entry["name"], "content": "玩家补充：船员的日志残页。",
    })

    result = plugin_service.cleanup_plugin_lorebook(dependencies, PLUGIN_ID)

    assert result["books_kept"] == [book_id]
    assert result["books_removed"] == 0
    kept = store.get_lorebook(book_id)
    assert kept is not None, "用户改过的书按 detach 语义保留"
    assert store.list_book_entries(book_id)


def test_autoimport_does_not_overwrite_user_edited_entry(host, store):
    """P1-A：启用/更新会重放 autoimport，包里的旧正文不得盖掉玩家改写。"""

    dependencies = _content_dependencies(host, store)
    plugin_service._autoimport_plugin_content(dependencies, PLUGIN_ID)
    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)
    entry = store.list_book_entries(book_id)[0]
    store.update_book_entry(book_id, entry["id"], {"content": "玩家补写的港口线索残页。"})

    plugin_service._autoimport_plugin_content(dependencies, PLUGIN_ID)

    current = store.get_entry(entry["id"])
    assert current is not None
    assert current["content"] == "玩家补写的港口线索残页。"
    assert store.get_lorebook(book_id) is not None


def test_autoimport_still_refreshes_untouched_book(host, store, tmp_path):
    """反向保证：玩家没改过时，包更新后的新内容仍然照常写进来（不是冻结）。"""

    dependencies = _content_dependencies(host, store)
    plugin_service._autoimport_plugin_content(dependencies, PLUGIN_ID)
    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)

    book_path = tmp_path / "plugins" / PLUGIN_ID / "content" / "lorebooks" / f"{BOOK_KEY}.json"
    package = json.loads(book_path.read_text(encoding="utf-8"))
    package["entries"][0]["content"] = "包更新后的正文。"
    book_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")

    plugin_service._autoimport_plugin_content(dependencies, PLUGIN_ID)

    assert any(
        entry["content"] == "包更新后的正文。"
        for entry in store.list_book_entries(book_id)
    )


def test_uninstall_keeps_book_when_only_keywords_changed(host, store):
    """P1-B：只改关键词/匹配设置也是用户数据，不能按「没改过」整本删除。"""

    dependencies = _content_dependencies(host, store)
    plugin_service._autoimport_plugin_content(dependencies, PLUGIN_ID)
    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)
    entry = store.list_book_entries(book_id)[0]
    store.update_book_entry(book_id, entry["id"], {"keywords": ["玩家自己加的关键词"]})

    result = plugin_service.cleanup_plugin_lorebook(dependencies, PLUGIN_ID)

    assert result["books_kept"] == [book_id]
    assert result["books_removed"] == 0
    assert store.get_lorebook(book_id) is not None
    assert store.get_entry(entry["id"])["keywords"] == ["玩家自己加的关键词"]


def test_ondemand_materialization_records_receipt_so_uninstall_cleans_up(host, store):
    """P1-B：catalog 按需物化也要登记 receipt，未改过的书仍能随卸载清理。"""

    dependencies = _content_dependencies(host, store)
    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)

    assert plugin_service.materialize_module_book_binding(
        dependencies, "module", PLUGIN_ID, book_id,
    ) is True
    assert store.get_lorebook(book_id) is not None

    result = plugin_service.cleanup_plugin_lorebook(dependencies, PLUGIN_ID)

    assert result["books_removed"] == 1
    assert store.get_lorebook(book_id) is None


def test_ondemand_materialization_keeps_user_edited_book_on_uninstall(host, store):
    dependencies = _content_dependencies(host, store)
    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)
    plugin_service.materialize_module_book_binding(dependencies, "module", PLUGIN_ID, book_id)
    entry = store.list_book_entries(book_id)[0]
    store.update_book_entry(book_id, entry["id"], {"keywords": ["开局后补的线索"]})

    result = plugin_service.cleanup_plugin_lorebook(dependencies, PLUGIN_ID)

    assert result["books_kept"] == [book_id]
    assert result["books_removed"] == 0
    assert store.get_lorebook(book_id) is not None


def test_uninstall_keeps_module_book_without_receipt(host, store):
    """没有 receipt 就没有包写入基线：不能整本删除可能属于用户的资料。"""

    dependencies = _content_dependencies(host, store)
    book_id = "module_book_legacy_archive"
    store.create_lorebook({
        "id": book_id, "name": "旧版导入的书", "description": "",
        "language": "zh", "source_kind": "module", "source_id": PLUGIN_ID,
    })

    result = plugin_service.cleanup_plugin_lorebook(dependencies, PLUGIN_ID)

    assert result["books_kept"] == [book_id]
    assert result["books_removed"] == 0
    assert store.get_lorebook(book_id) is not None


def test_module_lorebooks_list_declared_resource_for_catalog_modules(host, store):
    # catalog 模式：不启用即灌注，但详情页的资料册区块要能看到声明。
    dependencies = _module_dependencies(host, store)
    rows = module_service._module_lorebooks(dependencies, PLUGIN_ID)

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == module_book_id(PLUGIN_ID, BOOK_KEY)
    assert row["name"] == "Dagon Archive"
    assert row["catalog"] is True
    assert row["entry_count"] == 2
    assert row["ref"] == {
        "source_kind": "module", "source_id": PLUGIN_ID,
        "kind": "lorebook", "id": module_book_id(PLUGIN_ID, BOOK_KEY), "digest": "",
    }


def test_module_lorebooks_dedupe_materialized_book_against_catalog_row(host, store):

    plugin_service._autoimport_plugin_content(_content_dependencies(host, store), PLUGIN_ID)
    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)
    store.update_lorebook(book_id, {"name": "玩家改名后的档案"})

    rows = module_service._module_lorebooks(_module_dependencies(host, store), PLUGIN_ID)

    # 物化行（含 enabled 状态）优先，catalog 行不再重复。
    assert len(rows) == 1
    assert rows[0]["id"] == book_id
    assert rows[0]["name"] == "玩家改名后的档案"
    # 已落地 = 一本普通的、可编辑的书；catalog 行表示"还在包里"。
    assert rows[0]["catalog"] is False
    assert rows[0]["entry_count"] == 2


def _write_nameless_pack(plugins_dir: Path) -> PluginHost:
    """A pack book with entries but no ``name`` (like coc-dagon's GM guide)."""

    write_plugin(
        plugins_dir, PLUGIN_ID, plugin_type="content-pack", entrypoint=False,
        manifest_extra={
            "content_delivery_mode": "catalog",
            "contributes": {"lorebooks": ["content/lorebooks/*.json"]},
        },
    )
    books_dir = plugins_dir / PLUGIN_ID / "content" / "lorebooks"
    books_dir.mkdir(parents=True)
    (books_dir / "dagon-gm-guide.json").write_text(json.dumps({
        "entries": [
            {"id": "guide-frame", "name": "守秘人框架", "content": "这不是调查怪物，是调查记录。"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    data_dir = plugins_dir.parent / "data"
    (data_dir / PLUGIN_ID).mkdir(parents=True, exist_ok=True)
    (data_dir / PLUGIN_ID / "config.json").write_text(
        json.dumps({"enabled": True}), encoding="utf-8",
    )
    return PluginHost(plugins_dir, data_dir)


def test_nameless_declared_book_keeps_one_readable_name_before_and_after_materializing(tmp_path):
    # 包里没写 name 时，catalog 会拿文件名兜底（"dagon-gm-guide"），物化时
    # adapter 又会兜到 "Legacy Lorebook"——同一个文件三个名字。两处必须同名。
    plugins_dir = tmp_path / "plugins"
    nameless = _write_nameless_pack(plugins_dir)
    nameless.discover()
    lorebook = LorebookStore(tmp_path / "lorebook.db")
    lorebook.open()
    try:
        rows = module_service._module_lorebooks(_module_dependencies(nameless, lorebook), PLUGIN_ID)
        assert [row["name"] for row in rows] == ["Dagon GM Guide"]
        # 声明 key 仍要暴露：包页据此读取只读条目正文。
        assert rows[0]["key"] == "dagon-gm-guide"
        assert rows[0]["entry_count"] == 1

        plugin_service._autoimport_plugin_content(_content_dependencies(nameless, lorebook), PLUGIN_ID)
        book = lorebook.get_lorebook(module_book_id(PLUGIN_ID, "dagon-gm-guide"))
        assert book["name"] == "Dagon GM Guide"
    finally:
        lorebook.close()


pytest_plugins = ["tests.webapi_harness"]


@pytest.mark.asyncio
async def test_create_game_materializes_declared_module_book_on_demand(web_api, tmp_path):
    """catalog 模式的声明书不在书库里：开局勾选时创建事务按需物化（#700 B）。"""
    api, lorebook, registry, _llm, _worlds_dir = web_api
    plugins_dir = tmp_path / "plugins"
    plugin_host = _write_lorebook_pack(plugins_dir, delivery="catalog")
    plugin_host.discover()
    dependencies = _content_dependencies(plugin_host, lorebook)
    lifecycle_deps = api._game_lifecycle._dependencies
    object.__setattr__(
        lifecycle_deps, "materialize_module_book",
        lambda source_kind, source_id, book_id: plugin_service.materialize_module_book_binding(
            dependencies, source_kind, source_id, book_id,
        ),
    )

    book_id = module_book_id(PLUGIN_ID, BOOK_KEY)
    result = await api.create_game(
        "template_world",
        book_bindings=[{
            "ref": {
                "source_kind": "module", "source_id": PLUGIN_ID,
                "kind": "lorebook", "id": book_id, "digest": "",
            },
        }],
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )

    assert result["ok"] is True, result
    book = lorebook.get_lorebook(book_id)
    assert book is not None, "创建事务必须按需物化 module 声明书"
    assert book["source_kind"] == "module"
    assert len(lorebook.list_book_entries(book_id)) == 2
    instance = registry.get(api._parse_key(result["game_key"]))
    refs = instance.modules["content_binding"]["book_refs"]
    assert refs and refs[0]["source_kind"] == "module" and refs[0]["id"] == book_id
    assert lorebook.list_bindings(scope_kind="game", scope_id=result["game_key"])
    # 落地之后它必须能在资料册页找到：这正是玩家抱怨"18 条不知道去哪了"
    # 的那个缺口——书只有在书库里，才谈得上可查、可编辑。而且不依赖"当前
    # 对局"：离开对局、从资料册页进来也要能看到。
    listing = api.list_lorebooks("template_world", "")
    assert book_id in {str(row.get("id") or "") for row in listing["books"]}


@pytest.mark.asyncio
async def test_create_game_in_module_supplied_world_keeps_module_book_visible(web_api, tmp_path):
    """模组没有世界模板时按模组建世界：书必须落在这个世界里，且查得到（大衮）。"""
    api, lorebook, _registry, _llm, _worlds_dir = web_api
    plugins_dir = tmp_path / "plugins"
    plugin_host = _write_nameless_pack(plugins_dir)
    plugin_host.discover()
    dependencies = _content_dependencies(plugin_host, lorebook)
    lifecycle_deps = api._game_lifecycle._dependencies
    object.__setattr__(
        lifecycle_deps, "materialize_module_book",
        lambda source_kind, source_id, book_id: plugin_service.materialize_module_book_binding(
            dependencies, source_kind, source_id, book_id,
        ),
    )

    world_id = "module_lore-pack_dagon-1"
    book_id = module_book_id(PLUGIN_ID, "dagon-gm-guide")
    result = await api.create_game(
        world_id,
        game_name="大衮：深潮之下",
        custom_world=True,
        book_bindings=[{
            "ref": {
                "source_kind": "module", "source_id": PLUGIN_ID,
                "kind": "lorebook", "id": book_id, "digest": "",
            },
        }],
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )

    assert result["ok"] is True, result
    assert lorebook.get_world(world_id) is not None
    assert lorebook.get_lorebook(book_id)["name"] == "Dagon GM Guide"
    assert any(
        str(binding.get("book_id") or "") == book_id
        for binding in lorebook.list_bindings(scope_kind="world", scope_id=world_id)
    )
    listing = api.list_lorebooks(world_id, "")
    assert book_id in {str(row.get("id") or "") for row in listing["books"]}


@pytest.mark.asyncio
async def test_create_game_rejects_unknown_module_book_without_materializing(web_api, tmp_path):
    """没有对应声明资源的 module ref 拒绝（BOOK_NOT_FOUND），且不创建对局。"""
    api, lorebook, registry, _llm, _worlds_dir = web_api
    plugins_dir = tmp_path / "plugins"
    plugin_host = _write_lorebook_pack(plugins_dir, delivery="catalog")
    plugin_host.discover()
    dependencies = _content_dependencies(plugin_host, lorebook)
    lifecycle_deps = api._game_lifecycle._dependencies
    object.__setattr__(
        lifecycle_deps, "materialize_module_book",
        lambda source_kind, source_id, book_id: plugin_service.materialize_module_book_binding(
            dependencies, source_kind, source_id, book_id,
        ),
    )

    missing = module_book_id(PLUGIN_ID, "not-declared")
    result = await api.create_game(
        "template_world",
        book_bindings=[{
            "ref": {
                "source_kind": "module", "source_id": PLUGIN_ID,
                "kind": "lorebook", "id": missing, "digest": "",
            },
        }],
        players=[{"character_name": "艾琳", "attributes": {"str": 10}}],
    )

    assert result["ok"] is False
    assert result["error_code"] == "BOOK_NOT_FOUND"
    assert lorebook.get_lorebook(missing) is None
