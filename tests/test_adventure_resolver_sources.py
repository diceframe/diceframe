"""FIX-02 验收：Adventure Source + Binding 真正统一（施工单 §4）。

覆盖：

```text
builtin / user standalone / plugin module 三种来源都能 create/bind
真实数据目录（内置包被同步进 data/templates/adventures 并带 marker）不再假冲突
save → restart → D&D Runtime 解析到同一个包
重名 adventure_id：明确来源 binding 正常解析
旧 id-only binding：唯一 → 正常；冲突 → fail closed（source_conflict + recovery）
```

回归背景：FIX-02 之前 `AdventureSourceRegistry` 同时把"随应用发布的内置包"和
"data 目录里同步过来的同一份内置包"注册成 builtin / user 两个来源，
``resolve("core:lanterns_of_greymoor")`` 因此抛
``AdventureSourceConflict``——真实安装下**用内置冒险开不了局**。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.adventures import AdventureResolver, AdventureSourceConflict, sync_adventure_catalog
from src.adventures.registry import AdventureSource
from src.commands.game_handler import GameHandler
from src.engine.game_instance import GameInstance, GameRegistry
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.store import LorebookStore
from src.plugin_host.host import PluginHost
from src.rulesets.builtin import build_default_ruleset_registry
from src.webui.api import WebAPI
from src.webui.services import adventures

from webapi_harness import FakeLLMClient

BUILTIN_TEMPLATE = Path("templates/adventures/lanterns_of_greymoor")
BUILTIN_ID = "core:lanterns_of_greymoor"
USER_ID = "user:my_quest"
SHARED_ID = "shared:quest"
WORLD_ID = "greymoor"


# ---- helpers ---------------------------------------------------------------


def _clone_adventure(root: Path, directory_id: str, adventure_id: str) -> Path:
    package = root / directory_id
    shutil.copytree(BUILTIN_TEMPLATE, package, dirs_exist_ok=True)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["adventure_id"] = adventure_id
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return package


def _write_rule(rules_dir: Path) -> None:
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "dnd2024_srd.json").write_text(
        json.dumps({
            "rule_id": "dnd2024_srd",
            "rule_name": "5E 2024 SRD 专业规则",
            "dice_system": "d20",
            "runtime": {"id": "core:dnd2024", "minimum_version": 1},
            "attributes": [
                {"key": key, "name": key.upper(), "min": 3, "max": 20}
                for key in ("str", "dex", "con", "int", "wis", "cha")
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_world(worlds_dir: Path, world_id: str) -> None:
    worlds_dir.mkdir(parents=True, exist_ok=True)
    (worlds_dir / f"{world_id}.json").write_text(
        json.dumps({
            "world_id": world_id,
            "world_name": world_id,
            "description": "resolution test world",
            "world_setting": "灰沼",
            "starter_scene": "路口",
            "default_rule": "dnd2024_srd",
            "starter_lorebook": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _make_api(data_dir: Path, *, runtime_dir: Path) -> WebAPI:
    rules_dir = data_dir / "rules"
    worlds_dir = data_dir / "worlds"
    prompts_dir = data_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "gm_system_zh.md").write_text("你是测试 GM。", encoding="utf-8")
    _write_rule(rules_dir)
    _write_world(worlds_dir, WORLD_ID)
    registry = GameRegistry(data_dir / "saves")
    lorebook = LorebookStore(data_dir / "lorebook.db")
    lorebook.open()
    llm = FakeLLMClient()
    handler = GameHandler(
        registry=registry,
        llm_client=llm,
        lorebook_matcher=KeywordMatcher(),
        lorebook_store=lorebook,
        memory_store=None,
        prompts_dir=prompts_dir,
        rules_dir=rules_dir,
        worlds_dir=worlds_dir,
    )
    return WebAPI(
        registry=registry,
        lorebook=lorebook,
        memory=None,
        rules_dir=rules_dir,
        handler=handler,
        llm_client=llm,
        worlds_dir=worlds_dir,
        adventures_dir=runtime_dir,
        ruleset_registry=build_default_ruleset_registry(),
        config_state={},
        save_config=lambda: None,
    )


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path / "data"
        self.bundled_dir = tmp_path / "bundled"
        self.runtime_dir = self.data_dir / "templates" / "adventures"
        shutil.copytree(BUILTIN_TEMPLATE, self.bundled_dir / "lanterns_of_greymoor")
        # bootstrap.sync_builtin_templates() 的真实行为：内置包被复制进数据目录
        # 并打上 .diceframe-builtin 标记，用户包与它同级共存。
        sync_adventure_catalog(self.bundled_dir, self.runtime_dir)
        _clone_adventure(self.runtime_dir, "my_quest", USER_ID)
        self.api = _make_api(self.data_dir, runtime_dir=self.runtime_dir)
        self.lorebook = self.api._lore

    def close(self) -> None:
        self.lorebook.close()

    def plugin_module(self, plugin_id: str = "conflict-module") -> PluginHost:
        """Install a content-pack module contributing one adventure package."""

        plugins_root = self.data_dir / "plugin-packages"
        plugin_dir = plugins_root / plugin_id
        _clone_adventure(plugin_dir / "adventures", "shared", SHARED_ID)
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "schema_version": 1, "id": plugin_id, "name": "Conflict Module",
            "version": "1.0.0", "plugin_type": "content-pack",
            "content_profile": "adventure-module",
            "content_delivery_mode": "catalog",
            "adventure_packages": ["adventures/shared"],
            "contributes": {},
        }, ensure_ascii=False), encoding="utf-8")
        (plugin_dir / "config.schema.json").write_text(
            '{"type": "object", "properties": {}}', encoding="utf-8",
        )
        host = PluginHost(
            plugins_dir=plugins_root, data_dir=self.data_dir / "plugins",
        )
        _, runtime = host._load_runtime(plugin_dir)
        runtime.status = "enabled"
        host.plugins[plugin_id] = runtime
        return host


@pytest.fixture()
def env(tmp_path):
    environment = _Env(tmp_path)
    try:
        yield environment
    finally:
        environment.close()


# ---- §4.1 来源互斥：真实数据目录不再假冲突 ---------------------------------


def test_builtin_and_user_sources_are_disjoint_after_sync(env) -> None:
    resolver = AdventureResolver.from_directories(env.bundled_dir, env.runtime_dir)

    resolutions = {
        resolution.adventure_id: resolution for resolution in resolver.list_resolutions("")
    }

    assert set(resolutions) == {BUILTIN_ID, USER_ID}
    assert resolutions[BUILTIN_ID].source_kind == "builtin"
    assert resolutions[USER_ID].source_kind == "user"
    assert resolver.conflicts("") == {}


def test_synced_builtin_copy_is_not_a_second_source(env) -> None:
    """data 目录里的内置副本带 marker → 归 builtin，不产生 builtin/user 重名。"""

    resolver = AdventureResolver.from_directories(env.bundled_dir, env.runtime_dir)

    resolution = resolver.resolve_with_source(BUILTIN_ID, "zh-CN")

    assert resolution.source_kind == "builtin"
    assert resolution.bundle.manifest.adventure_id == BUILTIN_ID
    # 用户来源看不到内置副本。
    user_source = resolver.source_for("user")
    assert user_source is not None
    assert {bundle.manifest.adventure_id for bundle in user_source.loader.list("")} == {USER_ID}


# ---- §4.1 三种来源都能 create/bind -----------------------------------------


@pytest.mark.asyncio
async def test_creating_a_game_with_the_builtin_adventure_works_in_a_real_data_dir(env) -> None:
    """回归：真实数据目录下用内置冒险开局（FIX-02 之前直接 ValueError）。"""

    preset = env.api.ruleset_builder_choices(
        "dnd2024_srd", {"locale": "zh-CN"}, "zh-CN",
    )["choices"]["quick_presets"][0]
    character = env.api.ruleset_builder_finalize(
        "dnd2024_srd",
        {**preset["draft"], "locale": "zh-CN", "name": "灰沼重开者"},
        "zh-CN",
    )["character"]

    created = await env.api.create_game(
        WORLD_ID, "内置冒险开局", rule_id="dnd2024_srd",
        adventure_id=BUILTIN_ID, players=[character],
    )

    assert created["ok"] is True, created
    instance = env.api._reg.get(env.api._parse_key(created["game_key"]))
    assert instance.adventure_binding["adventure_id"] == BUILTIN_ID
    assert instance.adventure_binding["source_kind"] == "builtin"
    assert env.api.game_adventure_projection(
        created["game_key"], viewer_is_gm=True,
    )["adventure"]["available"] is True


def test_user_and_plugin_sources_resolve_with_their_own_identity(env) -> None:
    host = env.plugin_module()
    env.api._plugins = host
    env.api._sync_plugin_adventure_sources()
    runtime = env.api._ruleset_registry.get("core:dnd2024")

    user_binding = adventures.resolve_binding_for_runtime(
        env.api._adventure_dependencies, USER_ID, runtime, WORLD_ID, "zh-CN",
    )
    plugin_binding = adventures.resolve_binding_for_runtime(
        env.api._adventure_dependencies, SHARED_ID, runtime, WORLD_ID, "zh-CN",
    )

    assert user_binding["source_kind"] == "user"
    assert plugin_binding["source_kind"] == "plugin"
    assert plugin_binding["source_id"] == "conflict-module"


def test_runtime_uses_the_same_resolver_instance_as_the_webapi(env) -> None:
    runtime = env.api._ruleset_registry.get("core:dnd2024")

    assert runtime._adventure_loader is env.api._adventure_resolver


@pytest.mark.asyncio
async def test_restart_resolves_the_same_package(env) -> None:
    """save → 重启（新 WebAPI）→ D&D Runtime 解析到同一个包。"""

    host = env.plugin_module()
    env.api._plugins = host
    env.api._sync_plugin_adventure_sources()
    runtime = env.api._ruleset_registry.get("core:dnd2024")
    binding = adventures.resolve_binding_for_runtime(
        env.api._adventure_dependencies, SHARED_ID, runtime, WORLD_ID, "zh-CN",
    )
    instance = GameInstance(game_key=("web", "restart-room", "bot"), world_id=WORLD_ID)
    instance.language = "zh-CN"
    assert instance.bind_adventure(binding) is True
    await env.api._reg.save(instance)

    restarted = _make_api(env.data_dir, runtime_dir=env.runtime_dir)
    restarted._plugins = env.plugin_module()
    restarted._sync_plugin_adventure_sources()
    try:
        loaded = await restarted._reg.load(("web", "restart-room", "bot"))
        assert loaded is not None
        restarted_runtime = restarted._ruleset_registry.get("core:dnd2024")
        bundle = restarted_runtime.load_adventure(loaded, "zh-CN")

        assert bundle is not None
        assert bundle.manifest.adventure_id == SHARED_ID
        assert bundle.content_digest == binding["content_digest"]
        assert restarted.game_adventure_projection(
            "web|restart-room|bot", viewer_is_gm=True,
        )["adventure"]["available"] is True
    finally:
        restarted._lore.close()


# ---- §4.2 重名：明确来源正常，旧 id-only 绑定 fail closed ------------------


def test_duplicate_id_requires_an_explicit_source(env) -> None:
    host = env.plugin_module()
    env.api._plugins = host
    env.api._sync_plugin_adventure_sources()
    _clone_adventure(env.runtime_dir, "shared_user", SHARED_ID)
    resolver = env.api._adventure_resolver

    with pytest.raises(AdventureSourceConflict):
        resolver.resolve_with_source(SHARED_ID, "zh-CN")

    explicit = resolver.resolve_with_source(
        SHARED_ID, "zh-CN", source_kind="plugin", source_id="conflict-module",
    )
    assert explicit.source_kind == "plugin"


def test_legacy_id_only_binding_fails_closed_on_conflict(env) -> None:
    host = env.plugin_module()
    env.api._plugins = host
    env.api._sync_plugin_adventure_sources()
    _clone_adventure(env.runtime_dir, "shared_user", SHARED_ID)

    legacy = GameInstance(game_key=("web", "legacy-room", "bot"), world_id=WORLD_ID)
    legacy.language = "zh-CN"
    assert legacy.bind_adventure({
        "adventure_id": SHARED_ID, "version": "1.0.0",
        "format": "diceframe:adventure-graph-v1",
        "content_digest": "sha256:deadbeef", "world_id": WORLD_ID,
    })
    env.api._reg.register(legacy)

    projection = env.api.game_adventure_projection("web|legacy-room|bot", viewer_is_gm=True)

    assert projection["adventure"]["available"] is False
    assert projection["adventure"]["reason"] == "source_conflict"
    assert sorted(projection["adventure"]["sources"]) == ["plugin:conflict-module", "user"]


def test_source_aware_binding_resolves_even_when_the_id_is_duplicated(env) -> None:
    host = env.plugin_module()
    env.api._plugins = host
    env.api._sync_plugin_adventure_sources()
    _clone_adventure(env.runtime_dir, "shared_user", SHARED_ID)
    # 明确来源（UI 选择来源后）→ 直接解析到该来源的包，并写出来源感知绑定。
    binding = env.api._adventure_resolver.resolve_with_source(
        SHARED_ID, "zh-CN", source_kind="plugin", source_id="conflict-module",
    ).binding(WORLD_ID)
    instance = GameInstance(game_key=("web", "explicit-room", "bot"), world_id=WORLD_ID)
    instance.language = "zh-CN"
    assert instance.bind_adventure(binding) is True
    env.api._reg.register(instance)

    projection = env.api.game_adventure_projection("web|explicit-room|bot", viewer_is_gm=True)

    assert projection["adventure"]["available"] is True
    assert projection["adventure"]["binding"]["source_kind"] == "plugin"
    assert projection["adventure"]["binding"]["source_id"] == "conflict-module"


def test_legacy_binding_still_resolves_when_the_id_is_unique(env) -> None:
    legacy = GameInstance(game_key=("web", "unique-room", "bot"), world_id=WORLD_ID)
    legacy.language = "zh-CN"
    runtime = env.api._ruleset_registry.get("core:dnd2024")
    resolved = adventures.resolve_binding_for_runtime(
        env.api._adventure_dependencies, USER_ID, runtime, WORLD_ID, "zh-CN",
    )
    assert legacy.bind_adventure({
        key: runtime_value for key, runtime_value in resolved.items()
        if key not in {"source_kind", "source_id"}
    }) is True
    env.api._reg.register(legacy)

    projection = env.api.game_adventure_projection("web|unique-room|bot", viewer_is_gm=True)

    assert projection["adventure"]["available"] is True
    assert "source_kind" not in projection["adventure"]["binding"]


def test_restart_rebind_accepts_a_source_aware_binding_without_breaking_legacy(env) -> None:
    """旧 5 字段绑定 + 重开后解析出的来源感知绑定 = 同一个包（不阻断 restart）。"""

    runtime = env.api._ruleset_registry.get("core:dnd2024")
    resolved = adventures.resolve_binding_for_runtime(
        env.api._adventure_dependencies, USER_ID, runtime, WORLD_ID, "zh-CN",
    )
    instance = GameInstance(game_key=("web", "rebind-room", "bot"), world_id=WORLD_ID)
    assert instance.bind_adventure({
        key: value for key, value in resolved.items()
        if key not in {"source_kind", "source_id"}
    }) is True

    # 同一个包 + 补上来源身份 → 合法（不是"换了一个包"）。
    assert instance.bind_adventure(resolved) is True
    assert instance.adventure_binding["source_kind"] == "user"

    other_source = dict(resolved, source_kind="plugin", source_id="conflict-module")
    assert instance.bind_adventure(other_source) is False
