"""FIX-03 验收：D&D Module Catalog 真正接 Runtime（施工单 §5）。

覆盖：

```text
§5.1/§5.2  runtime 拥有 content catalog 链（adventure-local → owning module → core）
           组合根只注入"模组 catalog 来源"
§5.3       Adventure encounter → module encounter_profile → monster ContentRef
           → canonical enemy 实例 → combat.start 权威校验
§5.4       encounter 实例 id：唯一（count 1..20）、确定性、combat 词法有界
§5.5       Adventure outcome item_reward → ContentRef → reward intent
```

背景（修复前）：`resolve_encounter()` 与 `reward_intents_from_outcome()` 在生产
代码里**没有任何调用方**，战斗仍只吃冒险包内联 statblock，模块 catalog 只挂在
WebAPI 上；`_1.._5` 取模后缀在 count > 5 时会产生重复 enemy id。
"""

from __future__ import annotations

import json
import random
import re
import shutil
from pathlib import Path

import pytest

from src.content_modules.refs import ContentRefError
from src.engine.game_instance import GameInstance, GameRegistry
from src.lorebook.store import LorebookStore
from src.plugin_host.host import PluginHost
from src.rulesets.builtin import build_default_ruleset_registry
from src.rulesets.dnd2024.content.catalog import catalog_from_sources
from src.rulesets.dnd2024.content.encounter import expand_encounter_enemies
from src.rulesets.dnd2024.play.contracts import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.api import WebAPI

MODULE_ID = "cellar-module"
MODULE_LABEL = f"module:{MODULE_ID}"
MONSTER_ID = "clockwork_rat"
ITEM_ID = "brass_key"
ENCOUNTER_ID = "cellar_pack"
ADVENTURE_ID = "plugin:cellar"
BUILTIN_TEMPLATE = Path("templates/adventures/lanterns_of_greymoor")


# ---- module package with monster + item + encounter profile -----------------


def _monster(hp: int = 11, armor_class: int = 13, count_name: str = "Clockwork Rat") -> dict:
    return {
        "kind": "monster", "profile_id": MONSTER_ID, "name": count_name,
        "source_ref": MODULE_LABEL, "hp": hp, "armor_class": armor_class, "speed": 30,
        "abilities": {"str": 6, "dex": 14, "con": 10, "int": 3, "wis": 10, "cha": 3},
        "attacks": [{"id": "bite", "damage": "1d4+1", "attack_bonus": 4}],
    }


def _item() -> dict:
    return {
        "kind": "item", "item_id": ITEM_ID, "name": "Brass Key",
        "source_ref": MODULE_LABEL, "category": "key_item",
        "description": "地窖铜钥匙。",
    }


def _encounter(count: int = 2) -> dict:
    return {
        "kind": "encounter_profile", "encounter_id": ENCOUNTER_ID,
        "name": "Cellar Pack", "source_ref": MODULE_LABEL,
        "difficulty": "standard",
        "enemies": [{"ref": {"source": MODULE_LABEL, "kind": "monster", "id": MONSTER_ID},
                     "count": count}],
    }


def _install_module(tmp_path: Path) -> tuple[WebAPI, LorebookStore]:
    data_dir = tmp_path / "data"
    rules_dir = data_dir / "rules"
    worlds_dir = data_dir / "worlds"
    adventures_dir = data_dir / "templates" / "adventures"
    for directory in (rules_dir, worlds_dir, adventures_dir, data_dir / "saves"):
        directory.mkdir(parents=True, exist_ok=True)
    plugins_root = data_dir / "plugin-packages"
    module_dir = plugins_root / MODULE_ID
    (module_dir / "packs" / "dnd2024").mkdir(parents=True)
    # 模组自带一个 data-only 冒险包（供绑定与 adventure-local 链首使用）。
    adventure_dir = module_dir / "adventures" / "cellar"
    shutil.copytree(BUILTIN_TEMPLATE, adventure_dir)
    manifest_path = adventure_dir / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["adventure_id"] = ADVENTURE_ID
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False), encoding="utf-8")
    (module_dir / "plugin.json").write_text(json.dumps({
        "schema_version": 1, "id": MODULE_ID, "name": "Cellar Module",
        "version": "1.0.0", "plugin_type": "content-pack",
        "content_profile": "adventure-module",
        "content_delivery_mode": "catalog",
        "ruleset_catalogs": ["packs/dnd2024"],
        "adventure_packages": ["adventures/cellar"],
        "contributes": {},
    }, ensure_ascii=False), encoding="utf-8")
    (module_dir / "config.schema.json").write_text(
        '{"type": "object", "properties": {}}', encoding="utf-8",
    )
    (module_dir / "packs" / "dnd2024" / "monsters.json").write_text(
        json.dumps(_monster()), encoding="utf-8",
    )
    (module_dir / "packs" / "dnd2024" / "items.json").write_text(
        json.dumps(_item()), encoding="utf-8",
    )
    (module_dir / "packs" / "dnd2024" / "encounters.json").write_text(
        json.dumps(_encounter()), encoding="utf-8",
    )

    registry = GameRegistry(data_dir / "saves")
    lorebook = LorebookStore(data_dir / "lorebook.db")
    lorebook.open()
    host = PluginHost(
        plugins_dir=plugins_root, data_dir=data_dir / "plugins",
    )
    host.discover()
    # 模组内容目录只对"已启用"的模组生效（与生产一致：用户启用后才有 catalog）。
    host.plugins[MODULE_ID].status = "enabled"
    api = WebAPI(
        registry=registry, lorebook=lorebook, memory=None, rules_dir=rules_dir,
        handler=None, llm_client=None, worlds_dir=worlds_dir,
        adventures_dir=adventures_dir, plugin_host=host,
        ruleset_registry=build_default_ruleset_registry(),
        config_state={}, save_config=lambda: None,
    )
    # 模组的 Adventure 来源进入 resolver（与生产 enable 后一致）。
    api._sync_plugin_adventure_sources()
    return api, lorebook


def _runtime_with_module(api: WebAPI) -> Dnd2024Runtime:
    runtime = Dnd2024Runtime()
    runtime.set_adventure_resolver(api._adventure_resolver)
    runtime.set_module_content_sources(api.module_content_sources)
    return runtime


def _character(runtime: Dnd2024Runtime, name: str = "Arden") -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = choices["quick_presets"][0]
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": name},
    )


def _instance(
    runtime: Dnd2024Runtime, api: WebAPI | None = None, *,
    module_bound: bool = True,
) -> GameInstance:
    instance = GameInstance(
        game_key=("test", "dndmod-catalog", "bot"),
        world_id="default_fantasy", rule_id="dnd2024_srd",
        gm_uid="gm", language="en",
    )
    sheet = _character(runtime)
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": sheet}
    assert instance.bind_ruleset_runtime(sheet["rule_binding"])
    if module_bound:
        # 绑定来源身份 = 该模组；v1 裸 ContentRef 的默认来源即 owning module。
        if api is not None:
            binding = api._adventure_resolver.resolve_with_source(
                ADVENTURE_ID, "en", source_kind="plugin", source_id=MODULE_ID,
            ).binding("default_fantasy")
        else:
            binding = {
                "adventure_id": ADVENTURE_ID, "version": "1.0.0",
                "format": "diceframe:adventure-graph-v1",
                "content_digest": "sha256:x", "world_id": "default_fantasy",
                "source_kind": "plugin", "source_id": MODULE_ID,
            }
        assert instance.bind_adventure(binding) is True
    return instance


# ---- §5.1 / §5.2 catalog chain ---------------------------------------------


def test_runtime_owns_the_module_catalog_chain(tmp_path) -> None:
    api, lorebook = _install_module(tmp_path)
    try:
        runtime = _runtime_with_module(api)
        instance = _instance(runtime, api)

        catalog = runtime.content_catalog(instance)

        assert MONSTER_ID in catalog.records_for("monster")
        assert catalog.records_for("monster")[MONSTER_ID]["hp"] == 11
        assert ITEM_ID in catalog.records_for("item")
        assert ENCOUNTER_ID in catalog.records_for("encounter_profile")
        assert MODULE_LABEL in [source.label for source in catalog.sources()]
        # WebAPI 仍然可以给出同一个目录（只读 facade），但链路由 runtime 组装。
        assert MODULE_LABEL in [source.label for source in api.module_catalog().sources()]
    finally:
        lorebook.close()


def test_module_sources_are_ordered_with_the_owning_module_first(tmp_path) -> None:
    api, lorebook = _install_module(tmp_path)
    try:
        runtime = _runtime_with_module(api)
        instance = _instance(runtime, api)
        instance.adventure_binding = {
            "adventure_id": "plugin:cellar", "version": "1.0.0",
            "format": "diceframe:adventure-graph-v1",
            "content_digest": "sha256:x", "world_id": "default_fantasy",
            "source_kind": "plugin", "source_id": MODULE_ID,
        }

        ordered = api.module_content_sources(instance)
        assert ordered[0][0] == MODULE_LABEL
        assert runtime.owning_module_id(instance) == MODULE_ID
    finally:
        lorebook.close()


# ---- §5.4 encounter instance id --------------------------------------------


def _catalog():
    return catalog_from_sources([
        (MODULE_LABEL, {"monster": {MONSTER_ID: _monster()}}),
    ])


@pytest.mark.parametrize("count", list(range(1, 21)))
def test_encounter_enemy_ids_are_unique_and_combat_valid_for_any_count(count: int) -> None:
    enemies = [{"ref": {"source": MODULE_LABEL, "kind": "monster", "id": MONSTER_ID},
                "count": count}]

    ids = [item["id"] for item in expand_encounter_enemies(
        _catalog(), enemies, default_source=MODULE_LABEL,
    )]

    assert len(ids) == count
    assert len(set(ids)) == count
    for enemy_id in ids:
        assert re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", enemy_id)


def test_encounter_enemy_ids_are_deterministic_and_bounded() -> None:
    # catalog 契约把 profile_id 限制在 64 字符；加上序号后缀会超上界，
    # 实例 id 必须仍然合法（截断前缀、保留后缀）且唯一。
    long_profile = "m" * 64
    enemies = [{"ref": {"source": MODULE_LABEL, "kind": "monster", "id": MONSTER_ID},
                "count": 12}]
    first = expand_encounter_enemies(_catalog(), enemies, default_source=MODULE_LABEL)
    second = expand_encounter_enemies(_catalog(), enemies, default_source=MODULE_LABEL)

    assert [item["id"] for item in first] == [item["id"] for item in second]
    long_catalog = catalog_from_sources([
        (MODULE_LABEL, {"monster": {long_profile: {
            **_monster(), "profile_id": long_profile,
        }}}),
    ])
    long_ids = [item["id"] for item in expand_encounter_enemies(
        long_catalog,
        [{"ref": {"source": MODULE_LABEL, "kind": "monster", "id": long_profile}, "count": 12}],
        default_source=MODULE_LABEL,
    )]
    assert all(len(item) <= 64 for item in long_ids)
    assert len(set(long_ids)) == 12
    for enemy_id in long_ids:
        assert re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", enemy_id)


def test_distinct_profiles_keep_their_readable_bare_ids() -> None:
    catalog = catalog_from_sources([
        (MODULE_LABEL, {"monster": {
            "rat": {**_monster(1, 12), "profile_id": "rat", "name": "Rat"},
            "bat": {**_monster(2, 13), "profile_id": "bat", "name": "Bat"},
        }}),
    ])

    ids = [item["id"] for item in expand_encounter_enemies(
        catalog,
        [
            {"ref": {"source": MODULE_LABEL, "kind": "monster", "id": "rat"}},
            {"ref": {"source": MODULE_LABEL, "kind": "monster", "id": "bat"}},
        ],
        default_source=MODULE_LABEL,
    )]

    assert ids == ["rat", "bat"]


def test_unresolved_module_monster_fails_closed() -> None:
    with pytest.raises(ContentRefError, match="unresolved"):
        expand_encounter_enemies(
            _catalog(),
            [{"ref": {"source": "module:other", "kind": "monster", "id": "ghost"}}],
            default_source=MODULE_LABEL,
        )


# ---- FIX-07 §10：裸 ContentRef 的默认来源由 runtime 决定 --------------------


def test_bare_reward_ref_uses_the_runtime_default_source(tmp_path) -> None:
    """裸 ``item:brass_key`` 必须能解析到模组 catalog（Golden E2E 步骤 12 回归）。

    修复前 ``complete_adventure_node`` 用**绑定的来源身份**（``plugin:<module>``）
    当默认来源，那既不是 ContentRef 的 source 词表，也盖掉了 runtime 自己的
    owning-module 默认值 → 最自然的裸 ref 写法整体 fail closed。
    """

    api, lorebook = _install_module(tmp_path)
    try:
        runtime = _runtime_with_module(api)
        instance = _instance(runtime, api)

        intents = runtime.adventure_reward_intents(
            instance, {"rewards": [{"ref": f"item:{ITEM_ID}"}]}, recipient_uid="gm",
        )

        assert [intent["name"] for intent in intents] == ["Brass Key"]
        # 解析到的来源就是 owning module（runtime 的默认），不是绑定来源字符串。
        assert intents[0]["ref"]["source"] == MODULE_LABEL
    finally:
        lorebook.close()


def test_unresolved_module_monster_fails_closed() -> None:
    with pytest.raises(ContentRefError, match="unresolved"):
        expand_encounter_enemies(
            _catalog(),
            [{"ref": {"source": "module:other", "kind": "monster", "id": "ghost"}}],
            default_source=MODULE_LABEL,
        )


# ---- §5.3 module encounter → combat ----------------------------------------


def test_module_encounter_profile_becomes_a_combat_preset(tmp_path) -> None:
    api, lorebook = _install_module(tmp_path)
    try:
        runtime = _runtime_with_module(api)
        instance = _instance(runtime, api)

        engine = runtime._combat_engine(instance, EncounterAccess.sandbox(), "en")
        presets = {preset["id"]: preset for preset in engine.encounter_presets()}

        assert ENCOUNTER_ID in presets
        enemies = presets[ENCOUNTER_ID]["enemies"]
        assert [enemy["id"] for enemy in enemies] == [
            f"{MONSTER_ID}_1", f"{MONSTER_ID}_2",
        ]
        assert enemies[0]["hp"] == 11 and enemies[0]["armor_class"] == 13
        assert enemies[0]["attacks"][0]["damage"] == "1d4+1"
    finally:
        lorebook.close()


def test_combat_start_uses_module_monster_profiles(tmp_path) -> None:
    api, lorebook = _install_module(tmp_path)
    try:
        runtime = _runtime_with_module(api)
        instance = _instance(runtime, api)

        resolved = runtime.resolve_intent(instance, {
            "intent_id": "intent-start-1",
            "type": "combat.start",
            "expected_version": int(instance.ruleset_state.get("version", 0) or 0),
            "submitted_by": "gm",
            "encounter_preset_id": ENCOUNTER_ID,
            "mode": "sandbox",
        }, random.Random(7))

        assert resolved["ok"] is True, resolved
        started = next(
            event for event in resolved["event_batch"]["events"]
            if event["type"] == "dnd2024.combat.started"
        )
        assert set(started["enemies"]) == {f"{MONSTER_ID}_1", f"{MONSTER_ID}_2"}
        rat = started["enemies"][f"{MONSTER_ID}_1"]
        # 敌人数据来自模块 catalog 的 monster profile，而不是内联 statblock。
        assert rat["hp"] == 11 and rat["max_hp"] == 11
        assert rat["armor_class"] == 13
        assert rat["attacks"][0]["damage"] == "1d4+1"
    finally:
        lorebook.close()


def test_client_supplied_enemies_cannot_override_the_module_catalog(tmp_path) -> None:
    api, lorebook = _install_module(tmp_path)
    try:
        runtime = _runtime_with_module(api)
        instance = _instance(runtime, api)

        resolved = runtime.resolve_intent(instance, {
            "intent_id": "intent-start-2",
            "type": "combat.start",
            "expected_version": int(instance.ruleset_state.get("version", 0) or 0),
            "submitted_by": "gm",
            "encounter_preset_id": ENCOUNTER_ID,
            "mode": "sandbox",
            "enemies": [{"id": "forged", "hp": 9999, "armor_class": 30,
                         "attacks": [{"id": "x", "damage": "99d99", "attack_bonus": 20}]}],
        }, random.Random(7))

        started = next(
            event for event in resolved["event_batch"]["events"]
            if event["type"] == "dnd2024.combat.started"
        )
        assert "forged" not in started["enemies"]
        assert set(started["enemies"]) == {f"{MONSTER_ID}_1", f"{MONSTER_ID}_2"}
    finally:
        lorebook.close()


# ---- §5.5 reward intent chain ----------------------------------------------


def test_adventure_item_reward_resolves_through_the_module_catalog(tmp_path) -> None:
    api, lorebook = _install_module(tmp_path)
    try:
        runtime = _runtime_with_module(api)
        instance = _instance(runtime, api)

        intents = runtime.adventure_reward_intents(
            instance,
            {"rewards": [{"ref": "item:brass_key"}]},
            recipient_uid="gm",
        )

        assert len(intents) == 1
        intent = intents[0]
        assert intent["kind"] == "item_grant"
        assert intent["name"] == "Brass Key"
        assert intent["category"] == "key_item"
        assert intent["category_authoritative"] is True
        assert intent["recipient_uid"] == "gm"
        assert intent["ref"] == {"source": MODULE_LABEL, "kind": "item", "id": ITEM_ID}
        assert runtime.adventure_content_source(instance) == MODULE_LABEL
    finally:
        lorebook.close()


def test_unknown_module_reward_ref_fails_closed(tmp_path) -> None:
    api, lorebook = _install_module(tmp_path)
    try:
        runtime = _runtime_with_module(api)
        instance = _instance(runtime, api)

        from src.rulesets.dnd2024.content.rewards import RewardIntentError

        with pytest.raises(RewardIntentError, match="unresolved"):
            runtime.adventure_reward_intents(
                instance,
                {"rewards": [{"ref": {"source": MODULE_LABEL, "kind": "item",
                                      "id": "not_in_catalog"}}]},
                recipient_uid="gm",
            )
    finally:
        lorebook.close()
