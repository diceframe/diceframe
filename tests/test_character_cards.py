from src.lorebook.store import LorebookStore
from src.webui.services.character_cards import (
    CharacterCardDependencies,
    _import_tavern_as_npc,
)


def test_update_card_can_explicitly_clear_uploaded_portrait(tmp_path):
    import json
    from src.webui.services.character_cards import update_character_card

    cards_path = tmp_path / "cards.json"
    cards_path.write_text(json.dumps([{
        "id": "card-1",
        "character_name": "Aster",
        "portrait": {"kind": "upload", "asset_id": "avatar.webp"},
    }]), encoding="utf-8")

    dependencies = CharacterCardDependencies(cards_path=cards_path)
    result = update_character_card(dependencies, "card-1", {"portrait": None})

    assert result["ok"] is True
    assert "portrait" not in result["card"]
    assert "portrait" not in json.loads(cards_path.read_text(encoding="utf-8"))[0]


def test_export_preserves_business_fields_strips_runtime_markers(tmp_path):
    """导出去掉运行期插件标记，保留 source/raw_sillytavern 业务字段。"""
    import json
    from src.webui.services.character_cards import export_character_cards

    cards_path = tmp_path / "cards.json"
    dependencies = CharacterCardDependencies(cards_path=cards_path)
    card = {
        "id": "st_123", "schema_version": 2, "character_name": "Himmel",
        "attributes": {}, "skills": [],
        "source": "SillyTavern: himmel.png",
        "raw_sillytavern": {"name": "Himmel", "description": "冒险者"},
        "source_plugin": "napcat", "plugin_content_id": "p1",
    }
    cards_path.write_text(json.dumps([card], ensure_ascii=False), encoding="utf-8")
    result = export_character_cards(dependencies, ["st_123"])
    assert result["ok"] is True
    payload = json.loads(result["payload"].decode("utf-8"))
    # 业务字段保留
    assert payload["raw_sillytavern"] == {"name": "Himmel", "description": "冒险者"}
    assert payload["source"] == "SillyTavern: himmel.png"
    # 运行期插件标记去掉
    assert "source_plugin" not in payload
    assert "plugin_content_id" not in payload


def test_export_batch_disambiguates_same_name(tmp_path):
    """批量导出同名卡时 zip 内文件名不冲突，不互相覆盖。"""
    import io
    import json
    import zipfile
    from src.webui.services.character_cards import export_character_cards

    cards_path = tmp_path / "cards.json"
    dependencies = CharacterCardDependencies(cards_path=cards_path)
    cards = [
        {"id": "c1", "schema_version": 2, "character_name": "艾琳", "attributes": {}},
        {"id": "c2", "schema_version": 2, "character_name": "艾琳", "attributes": {}},
    ]
    cards_path.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    result = export_character_cards(dependencies, ["c1", "c2"])
    assert result["ok"] is True
    assert result["content_type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(result["payload"])) as zf:
        names = zf.namelist()
        assert len(names) == 2  # 两张都保留
        assert len(set(names)) == 2  # 文件名不重复


def test_export_import_roundtrip_preserves_raw_sillytavern(tmp_path):
    """导出→导入无损往返：raw_sillytavern 保留，识别为 DiceFrame 卡原样入库。"""
    import asyncio
    import base64
    import json
    from src.webui.services.character_cards import export_character_cards, import_character_card

    cards_path = tmp_path / "cards.json"
    dependencies = CharacterCardDependencies(cards_path=cards_path)
    card = {
        "id": "st_123", "schema_version": 2, "character_name": "Himmel",
        "attributes": {}, "skills": [], "source": "SillyTavern: himmel.png",
        "raw_sillytavern": {"name": "Himmel", "description": "冒险者"},
    }
    cards_path.write_text(json.dumps([card], ensure_ascii=False), encoding="utf-8")
    exported = export_character_cards(dependencies, ["st_123"])
    file_data = base64.b64encode(exported["payload"]).decode()
    imported = asyncio.run(
        import_character_card(
            dependencies, file_data=file_data, file_name="Himmel.json",
        )
    )
    assert imported["ok"] is True
    assert imported["format"] == "diceframe"
    assert imported["card"]["raw_sillytavern"] == card["raw_sillytavern"]


def test_import_tavern_as_npc_creates_npc_and_book_entries(tmp_path):
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    try:
        store.create_world("w1", "测试世界", description="", language="zh-CN")
        tavern = {
            "name": "Himmel",
            "description": "一位老练的冒险者",
            "personality": "温和而坚定",
            "scenario": "酒馆偶遇",
            "first_mes": "你好，旅行者",
            "tags": ["英雄", "冒险者"],
            "character_book": [
                {"keys": ["剑", "武器"], "content": "Himmel 的佩剑", "comment": "佩剑"},
                {"keys": ["过去"], "content": "Himmel 的往事", "comment": "往事"},
            ],
        }
        dependencies = CharacterCardDependencies(
            cards_path=tmp_path / "cards.json",
            lorebook=store,
            rebuild_lorebook_index=lambda _world_id: None,
        )
        result = _import_tavern_as_npc(dependencies, tavern, "w1")

        assert result["ok"] is True
        assert result["imported_as"] == "npc"
        assert result["npc_name"] == "Himmel"
        assert result["lorebook_entries"] == 2

        npc = store.get_entry("w1_tavern_Himmel")
        assert npc is not None
        assert npc["type"] == "npc"
        assert npc["tier"] == "core"
        assert "Himmel" in npc["keywords"]
        assert "英雄" in npc["keywords"]
        assert "一位老练的冒险者" in npc["content"]
        assert "温和而坚定" in npc["content"]

        book1 = store.get_entry("w1_tavern_Himmel_book_0")
        assert book1 is not None
        assert book1["type"] == "other"
        assert "剑" in book1["keywords"]
        assert book1["content"] == "Himmel 的佩剑"

        # 幂等：再导一次是更新而非新增；primary world projection 不混入
        # 独立 character-card Book 的条目。
        tavern["description"] = "更新后的描述"
        _import_tavern_as_npc(dependencies, tavern, "w1")
        npc2 = store.get_entry("w1_tavern_Himmel")
        assert "更新后的描述" in npc2["content"]
        assert [entry["id"] for entry in store.list_entries("w1")] == ["w1_tavern_Himmel"]
        assert len(store.list_book_entries("character_card:w1:Himmel")) == 2
    finally:
        store.close()


def test_import_tavern_as_npc_requires_world(tmp_path):
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    try:
        dependencies = CharacterCardDependencies(
            cards_path=tmp_path / "cards.json",
            lorebook=store,
            rebuild_lorebook_index=lambda _world_id: None,
        )
        # 无 world_id
        assert _import_tavern_as_npc(
            dependencies, {"name": "X"}, "",
        )["ok"] is False
        # 世界不存在
        assert _import_tavern_as_npc(
            dependencies, {"name": "X"}, "no-such-world",
        )["ok"] is False
    finally:
        store.close()


def test_import_tavern_carries_play_directives_and_nsfw_warning(tmp_path):
    """system_prompt / post_history_instructions 进 NPC content；NSFW 卡返回标记。"""
    from src.webui.services.character_cards import _tavern_has_nsfw
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    try:
        store.create_world("w1", "测试世界", description="", language="zh-CN")
        tavern = {
            "name": "Sucrose",
            "description": "一位生物炼金术士",
            "personality": "社恐、好奇、笨拙",
            "scenario": "森林偶遇",
            "first_mes": "啊、啊啦…",
            "system_prompt": "短句；只写角色行为，不写玩家的动作。",
            "post_history_instructions": "耳朵被提及时会慌乱地躲起来。",
            "tags": ["Game Characters", "Female", "Cute"],
            "character_book": None,
        }
        dependencies = CharacterCardDependencies(
            cards_path=tmp_path / "cards.json",
            lorebook=store,
            rebuild_lorebook_index=lambda _world_id: None,
        )
        result = _import_tavern_as_npc(dependencies, tavern, "w1")
        assert result["ok"] is True
        assert "nsfw_warning" not in result  # 无 NSFW 标记，不给警告

        npc = store.get_entry("w1_tavern_Sucrose")
        assert npc is not None
        assert "扮演指令: 短句；只写角色行为，不写玩家的动作。" in npc["content"]
        assert "后续指令: 耳朵被提及时会慌乱地躲起来。" in npc["content"]

        # NSFW 标记 -> 返回 True（中英文标记都覆盖）
        assert _tavern_has_nsfw({**tavern, "tags": ["NSFW", "Submissive"]}) is True
        assert _tavern_has_nsfw(tavern) is False
    finally:
        store.close()


def test_export_import_roundtrip_preserves_skill_effect(tmp_path):
    """导出 → 导入后 skills[].effect 仍然存在。"""

    import asyncio
    import base64
    import json
    from src.webui.services.character_cards import export_character_cards, import_character_card

    cards_path = tmp_path / "cards.json"
    dependencies = CharacterCardDependencies(cards_path=cards_path)
    card = {
        "id": "df_effect",
        "schema_version": 2,
        "character_name": "Himmel",
        "attributes": {},
        "skills": [
            {"name": "火焰球", "value": 80, "effect": "向目标发射火球。"},
            {"name": "侦查", "value": 45},
        ],
    }
    cards_path.write_text(json.dumps([card], ensure_ascii=False), encoding="utf-8")
    exported = export_character_cards(dependencies, ["df_effect"])
    file_data = base64.b64encode(exported["payload"]).decode()
    imported = asyncio.run(
        import_character_card(
            dependencies, file_data=file_data, file_name="Himmel.json",
        )
    )
    assert imported["ok"] is True
    assert imported["card"]["skills"] == card["skills"]


def _table_save_payload(card: dict, **overrides) -> dict:
    """What a table/game save can legitimately carry: no server bookkeeping."""

    from copy import deepcopy

    payload = {
        key: deepcopy(card[key]) for key in (
            "character_name", "race", "class", "background", "attributes", "skills",
            "equipment", "inventory", "key_items", "gold", "currency", "portrait",
            "rule_id", "identity",
        ) if key in card
    }
    payload.update(overrides)
    return payload


def test_table_save_keeps_plugin_card_ownership(tmp_path):
    """入席自动存回不得让插件卡失去 source_plugin / plugin_content_id。

    卸载、启用去重和「非房主只能看到插件卡」都按 source_plugin 判定；一旦被
    普通存回抹掉，插件卡会变成没人拥有的本地卡。
    """

    import json
    from src.webui.services.character_cards import save_character_card

    cards_path = tmp_path / "cards.json"
    dependencies = CharacterCardDependencies(cards_path=cards_path)
    plugin_card = {
        "id": "plugin_demo_p1", "schema_version": 2,
        "character_name": "Vesper", "race": "人类", "class": "游荡者", "background": "罪犯",
        "attributes": {}, "skills": [],
        "source": "插件内容包：demo",
        "source_plugin": "demo", "plugin_content_id": "p1",
    }
    cards_path.write_text(json.dumps([plugin_card], ensure_ascii=False), encoding="utf-8")

    saved = save_character_card(
        dependencies, _table_save_payload(plugin_card, gold=77),
    )

    assert saved["ok"] is True
    [stored] = json.loads(cards_path.read_text(encoding="utf-8"))
    assert stored["id"] == "plugin_demo_p1"
    assert stored["gold"] == 77
    assert stored["source_plugin"] == "demo"
    assert stored["plugin_content_id"] == "p1"
    assert stored["source"] == "插件内容包：demo"


def test_table_save_keeps_the_tavern_original(tmp_path):
    """酒馆导入卡（无 provenance）被存回时保留 raw_sillytavern 与来源标签。"""

    import json
    from src.webui.services.character_cards import save_character_card

    cards_path = tmp_path / "cards.json"
    dependencies = CharacterCardDependencies(cards_path=cards_path)
    tavern_card = {
        "id": "st_1", "schema_version": 2,
        "character_name": "Himmel", "race": "人类", "class": "冒险者", "background": "",
        "attributes": {}, "skills": [],
        "source": "SillyTavern: himmel.png",
        "raw_sillytavern": {"name": "Himmel", "description": "勇者"},
    }
    cards_path.write_text(json.dumps([tavern_card], ensure_ascii=False), encoding="utf-8")

    saved = save_character_card(dependencies, _table_save_payload(tavern_card))

    assert saved["ok"] is True
    [stored] = json.loads(cards_path.read_text(encoding="utf-8"))
    assert stored["raw_sillytavern"] == {"name": "Himmel", "description": "勇者"}
    assert stored["source"] == "SillyTavern: himmel.png"


def test_an_explicit_source_still_relabels_the_card(tmp_path):
    """显式来源不是"无来源"：调用方明确写的标签优先。"""

    import json
    from src.webui.services.character_cards import save_character_card

    cards_path = tmp_path / "cards.json"
    dependencies = CharacterCardDependencies(cards_path=cards_path)
    card = {
        "id": "c1", "schema_version": 2,
        "character_name": "Aster", "race": "精灵", "class": "法师", "background": "",
        "attributes": {}, "skills": [], "source": "旧标签",
    }
    cards_path.write_text(json.dumps([card], ensure_ascii=False), encoding="utf-8")

    saved = save_character_card(
        dependencies, _table_save_payload(card, source="新标签"),
    )

    assert saved["ok"] is True
    [stored] = json.loads(cards_path.read_text(encoding="utf-8"))
    assert stored["source"] == "新标签"
