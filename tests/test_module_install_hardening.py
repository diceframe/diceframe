"""FIX-01 验收：Module 安装 hard gate + 生命周期保护（施工单 §3 验收清单）。

覆盖（全部走真实 WebAPI / PluginHost / LorebookStore / 存档目录）：

```text
catalog module install        → 不写 Lorebook / 卡库
legacy content-pack           → 行为不变（仍然 autoimport）
tool package                  → /api/modules/import 拒绝，且包未落地
incompatible runtime          → preview blocker + 直接 API 安装同样拒绝
Adventure / catalog 深校验     → 在安装事务内阻断（不落盘）
bound save                    → update / disable / stop / uninstall / overwrite 全部阻断
server restart 后 uninstall    → 仍阻断（不依赖"碰巧同步过 registry"）
ENDED save 引用 module         → 仍阻断（不只看内存 active 实例）
```
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from src.engine import persistence
from src.engine.game_instance import GameInstance, GameRegistry, GameState
from src.lorebook.store import LorebookStore
from src.plugin_host.host import PluginHost
from src.rulesets.builtin import build_default_ruleset_registry
from src.webui.api import WebAPI
from src.webui.routes.modules import register_modules
from src.webui.routes.plugins import register_plugins
from src.webui.services.module_validation import ModulePackageError
from src.webui.services.modules import ModuleInUse

BUILTIN_ADVENTURE = Path("templates/adventures/lanterns_of_greymoor")
MODULE_ID = "castle-module"
ADVENTURE_ID = "plugin:castle-quest"
CONTENT_WORLD = "castle_world"


# ---- package fixtures -------------------------------------------------------


def _adventure_files() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(BUILTIN_ADVENTURE.rglob("*")):
        if path.is_file():
            files[path.relative_to(BUILTIN_ADVENTURE).as_posix()] = path.read_bytes()
    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    manifest["adventure_id"] = ADVENTURE_ID
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")
    return {f"adventures/castle/{name}": payload for name, payload in files.items()}


def _content_files() -> dict[str, bytes]:
    """Real data-only content: one world template + one card + one npc."""

    return {
        f"content/worlds/{CONTENT_WORLD}.json": json.dumps({
            "world_schema_version": 2,
            "world_id": CONTENT_WORLD,
            "world_name": "Castle World",
            "starter_lorebook": [
                {"id": "gate", "name": "Castle Gate", "content": "石门。", "keywords": ["城门"]},
            ],
        }, ensure_ascii=False).encode("utf-8"),
        "content/characters/hero.json": json.dumps({
            "id": "hero", "character_name": "Hero", "description": "模板角色",
        }, ensure_ascii=False).encode("utf-8"),
        "content/npcs/himmel.json": json.dumps({
            "id": "himmel", "name": "Himmel", "description": "石门守卫",
        }, ensure_ascii=False).encode("utf-8"),
    }


def _catalog_files(*, broken: bool = False) -> dict[str, bytes]:
    hp = 0 if broken else 45
    return {
        "packs/dnd2024/monsters.json": json.dumps({
            "kind": "monster", "profile_id": "moss_golem", "name": "Moss Golem",
            "source_ref": f"module:{MODULE_ID}", "hp": hp, "armor_class": 14, "speed": 20,
            "abilities": {"str": 16, "dex": 8, "con": 16, "int": 5, "wis": 10, "cha": 5},
            "attacks": [{"id": "slam", "damage": "2d8+3", "attack_bonus": 5}],
        }, ensure_ascii=False).encode("utf-8"),
    }


def _module_files(
    *,
    plugin_type: str = "content-pack",
    profile: str = "adventure-module",
    delivery: str = "catalog",
    adventure: bool = True,
    content: bool = True,
    catalog: bool = False,
    broken_catalog: bool = False,
    broken_adventure: bool = False,
    requires: dict | None = None,
    plugin_id: str = MODULE_ID,
) -> dict[str, bytes]:
    contributes: dict[str, list[str]] = {}
    files: dict[str, bytes] = {}
    if content:
        contributes.update({
            "world_templates": ["content/worlds/*.json"],
            "character_templates": ["content/characters/*.json"],
            "npcs": ["content/npcs/*.json"],
        })
        files.update(_content_files())
    manifest: dict = {
        "schema_version": 1,
        "id": plugin_id,
        "name": "Castle Module",
        "version": "1.0.0",
        "plugin_type": plugin_type,
        "contributes": contributes,
        "config_schema": "config.schema.json",
    }
    if plugin_type == "content-pack":
        manifest["content_profile"] = profile
        manifest["content_delivery_mode"] = delivery
    else:
        manifest["entrypoint"] = ["{python}", "-c", "pass"]
    if adventure:
        manifest["adventure_packages"] = ["adventures/castle"]
        files.update(_adventure_files())
        if broken_adventure:
            graph_path = "adventures/castle/adventure.json"
            graph = json.loads(files[graph_path].decode("utf-8"))
            graph["steps"][0]["choice_ids"] = ["nope_missing_choice"]
            files[graph_path] = json.dumps(graph, ensure_ascii=False).encode("utf-8")
    if catalog:
        manifest["ruleset_catalogs"] = ["packs/dnd2024"]
        files.update(_catalog_files(broken=broken_catalog))
    if requires is not None:
        manifest["requires"] = requires
    files["plugin.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    files["config.schema.json"] = json.dumps({
        "type": "object",
        "properties": {"enabled": {"type": "boolean", "default": False, "ui": {"control": "switch"}}},
    }).encode("utf-8")
    return files


def _zip_payload(files: dict[str, bytes], plugin_id: str = MODULE_ID) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(f"{plugin_id}/{name}", payload)
    return buffer.getvalue()


# ---- environment -----------------------------------------------------------


class _FakeMarketplace:
    """Marketplace double that serves a locally built package (no network)."""

    def __init__(self, payload: bytes, plugin_id: str, version: str = "1.0.0") -> None:
        self._payload = payload
        self._plugin_id = plugin_id
        self._version = version

    async def package_for_plugin(self, plugin_id: str) -> dict:
        return {
            "ok": True,
            "payload": self._payload,
            "plugin": {"id": plugin_id, "version": self._version},
            "source": {"kind": "mirror"},
        }


def _make_api(data_dir: Path, *, secrets_dir: Path | None = None) -> tuple[WebAPI, PluginHost, LorebookStore]:
    rules_dir = data_dir / "rules"
    worlds_dir = data_dir / "worlds"
    adventures_dir = data_dir / "templates" / "adventures"
    for directory in (rules_dir, worlds_dir, adventures_dir):
        directory.mkdir(parents=True, exist_ok=True)
    registry = GameRegistry(data_dir / "saves")
    lorebook = LorebookStore(data_dir / "lorebook.db")
    lorebook.open()
    host = PluginHost(
        plugins_dir=(secrets_dir or data_dir) / "plugin-packages",
        data_dir=(secrets_dir or data_dir) / "plugins",
    )
    api = WebAPI(
        registry=registry,
        lorebook=lorebook,
        memory=None,
        rules_dir=rules_dir,
        handler=None,
        llm_client=None,
        worlds_dir=worlds_dir,
        adventures_dir=adventures_dir,
        plugin_host=host,
        ruleset_registry=build_default_ruleset_registry(),
        config_state={},
        save_config=lambda: None,
    )
    # 与 bootstrap 完全同一条接线（单一 choke point）。
    api.attach_package_hooks(host)
    return api, host, lorebook


@pytest.fixture()
def env(tmp_path):
    api, host, lorebook = _make_api(tmp_path / "data")
    try:
        yield SimpleNamespace(
            api=api, host=host, lorebook=lorebook,
            data_dir=tmp_path / "data", tmp_path=tmp_path,
        )
    finally:
        lorebook.close()


def _module_app(api: WebAPI) -> web.Application:
    app = web.Application()
    app["api"] = api
    register_modules(app)
    register_plugins(app)
    return app


# ---- §3.1 module surface only installs content-pack -------------------------


@pytest.mark.asyncio
async def test_tool_package_is_rejected_by_module_import(env) -> None:
    payload = _zip_payload(
        _module_files(plugin_type="tool", adventure=False, content=False),
    )
    async with TestClient(TestServer(_module_app(env.api))) as client:
        form = FormData()
        form.add_field("file", payload, filename=f"{MODULE_ID}.dfplugin")
        response = await client.post(
            "/api/modules/import", data=form, headers={"X-TRPG-Confirm": "true"},
        )
        body = await response.json()

    assert response.status == 400
    assert "content-pack" in body["error"]
    # 拒绝必须发生在落盘之前。
    assert MODULE_ID not in env.host.plugins
    assert not (env.data_dir / "plugin-packages" / MODULE_ID).exists()


@pytest.mark.asyncio
async def test_generic_plugin_install_still_accepts_tool_packages(env) -> None:
    """通用插件面行为不变：tool 包仍然可以装（只是不能走模组面）。"""

    payload = _zip_payload(
        _module_files(plugin_type="tool", adventure=False, content=False),
    )
    result = await env.api.install_plugin(payload)

    assert result["ok"] is True
    assert env.host.plugin_type_of(MODULE_ID) == "tool"


@pytest.mark.asyncio
async def test_marketplace_module_install_rejects_tool_package(env) -> None:
    """市场安装是第二条模组入口，同样只接受 content-pack。"""

    payload = _zip_payload(
        _module_files(plugin_type="tool", adventure=False, content=False),
    )
    env.host.marketplace = _FakeMarketplace(payload, MODULE_ID)

    with pytest.raises(ValueError, match="content-pack"):
        await env.api.install_marketplace_module(MODULE_ID)
    assert MODULE_ID not in env.host.plugins


# ---- §3.3 / §3.4 shared hard gate ------------------------------------------


def test_incompatible_runtime_blocks_preview_and_install(env) -> None:
    payload = _zip_payload(_module_files(
        requires={"rulesets": [{"id": "core:absent", "minimum_version": 3}]},
    ))

    preview = env.api.preview_module_import(payload)

    assert preview["blockers"] == ["ruleset_runtime_missing:core:absent>=3"]


@pytest.mark.asyncio
async def test_direct_module_install_is_rejected_when_preview_shows_blocker(env) -> None:
    payload = _zip_payload(_module_files(
        requires={"rulesets": [{"id": "core:absent", "minimum_version": 3}]},
    ))

    # 预览与直接安装共用同一套校验：blocker 在安装事务里同样阻断。
    assert env.api.preview_module_import(payload)["blockers"] == [
        "ruleset_runtime_missing:core:absent>=3",
    ]
    with pytest.raises(ValueError, match="ruleset_runtime_missing"):
        await env.api.import_module(payload)
    assert MODULE_ID not in env.host.plugins


@pytest.mark.asyncio
async def test_broken_declared_adventure_is_rejected_inside_the_transaction(env) -> None:
    payload = _zip_payload(_module_files(broken_adventure=True))

    with pytest.raises(ValueError, match="adventure_package_invalid:castle"):
        await env.api.import_module(payload)
    assert MODULE_ID not in env.host.plugins
    assert not (env.data_dir / "plugin-packages" / MODULE_ID).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_id", "minimum"),
    [("core:not-installed", 1), ("core:dnd2024", 999)],
)
async def test_adventure_manifest_runtime_is_an_install_gate_even_without_module_requires(
    env, runtime_id: str, minimum: int,
) -> None:
    files = _module_files(requires=None)
    manifest_path = "adventures/castle/manifest.json"
    adventure_manifest = json.loads(files[manifest_path].decode("utf-8"))
    adventure_manifest["required_runtime"] = {
        "id": runtime_id, "minimum_version": minimum,
    }
    files[manifest_path] = json.dumps(adventure_manifest).encode("utf-8")
    payload = _zip_payload(files)

    preview = env.api.preview_module_import(payload)
    expected = f"adventure_runtime_missing:castle:{runtime_id}>={minimum}"
    assert expected in preview["blockers"]
    with pytest.raises(ValueError, match="adventure_runtime_missing"):
        await env.api.import_module(payload)
    assert MODULE_ID not in env.host.plugins


@pytest.mark.asyncio
async def test_broken_declared_catalog_is_rejected_inside_the_transaction(env) -> None:
    payload = _zip_payload(_module_files(catalog=True, broken_catalog=True))

    with pytest.raises(ValueError, match="ruleset_catalog_invalid:dnd2024"):
        await env.api.import_module(payload)
    assert MODULE_ID not in env.host.plugins


@pytest.mark.asyncio
async def test_valid_module_with_adventure_and_catalog_installs(env) -> None:
    payload = _zip_payload(_module_files(catalog=True))

    result = await env.api.import_module(payload)

    assert result["ok"] is True
    # 启用后模块的 Adventure 来源才进入运行时来源注册表（既有语义）。
    await env.api.update_plugin_config(MODULE_ID, {"enabled": True})
    detail = env.api.module_detail(MODULE_ID)["module"]
    assert [item["adventure_id"] for item in detail["adventures"]] == [ADVENTURE_ID]
    assert env.api.module_compatibility(MODULE_ID)["blockers"] == []


# ---- §3.2 catalog delivery never auto-imports ------------------------------


@pytest.mark.asyncio
async def test_catalog_module_enable_does_not_write_lorebook_or_cards(env) -> None:
    await env.api.import_module(_zip_payload(_module_files(catalog=True)))

    await env.api.update_plugin_config(MODULE_ID, {"enabled": True})

    assert env.lorebook.list_entries(CONTENT_WORLD) == []
    cards = env.api.list_character_cards()["cards"]
    assert [card for card in cards if card.get("source_plugin") == MODULE_ID] == []


@pytest.mark.asyncio
async def test_legacy_content_pack_enable_still_autoimports(env) -> None:
    """兼容红线：缺省 content_delivery_mode 的传统内容包行为不变。"""

    await env.api.import_module(_zip_payload(_module_files(
        profile="content-pack", delivery="legacy_autoimport", adventure=False,
    )))

    await env.api.update_plugin_config(MODULE_ID, {"enabled": True})

    entries = env.lorebook.list_entries(CONTENT_WORLD)
    # legacy 行为不变：世界模板 starter_lorebook + npc 都被灌注。
    assert sorted(entry["name"] for entry in entries) == ["Castle Gate", "Himmel"]
    cards = env.api.list_character_cards()["cards"]
    assert [card["character_name"] for card in cards if card.get("source_plugin") == MODULE_ID] == ["Hero"]


# ---- §3.5 / §3.6 / §3.7 bound-save protection ------------------------------


async def _persist_bound_save(
    env, *, target_id: str = "room-a", state: GameState = GameState.PAUSED,
) -> str:
    """Write a real save on disk WITHOUT registering it in memory."""

    registry = env.api._reg
    instance = GameInstance(game_key=("web", target_id, "bot"), world_id="greymoor")
    instance.state = state
    assert instance.bind_adventure({
        "adventure_id": ADVENTURE_ID, "version": "1.0.0",
        "format": "diceframe:adventure-graph-v1",
        "content_digest": "sha256:deadbeef", "world_id": "greymoor",
    })
    await persistence.save(registry, instance)
    registry.remove(instance.game_key)
    return "|".join(instance.game_key)


@pytest.mark.asyncio
async def test_bound_save_blocks_every_destructive_module_action(env) -> None:
    payload = _zip_payload(_module_files(catalog=True))
    await env.api.import_module(payload)
    game_key = await _persist_bound_save(env)

    with pytest.raises(ModuleInUse) as uninstall_error:
        await env.api.uninstall_plugin(MODULE_ID)
    assert uninstall_error.value.action == "uninstall"
    assert [item["game_key"] for item in uninstall_error.value.games] == [game_key]

    with pytest.raises(ModuleInUse):
        await env.api.update_plugin_config(MODULE_ID, {"enabled": False})
    with pytest.raises(ModuleInUse):
        await env.api.control_plugin(MODULE_ID, "stop")
    with pytest.raises(ModuleInUse):
        await env.api.guard_module_mutation(MODULE_ID, "update")

    # overwrite 本地安装：宿主 choke point，装新包也不行。
    with pytest.raises(ModuleInUse):
        await env.api.import_module(payload, True)
    # overwrite 市场安装：同一条 host 路径（含后台自动更新）。
    env.host.marketplace = _FakeMarketplace(payload, MODULE_ID)
    with pytest.raises(ModuleInUse):
        await env.host.install_from_marketplace(MODULE_ID, overwrite=True)

    # 包仍然在，未被破坏。
    assert MODULE_ID in env.host.plugins


@pytest.mark.asyncio
async def test_unbound_module_actions_stay_allowed(env) -> None:
    await env.api.import_module(_zip_payload(_module_files(catalog=True)))

    assert await env.api.control_plugin(MODULE_ID, "stop") is not None
    result = await env.api.uninstall_plugin(MODULE_ID)

    assert result["ok"] is True
    assert MODULE_ID not in env.host.plugins


@pytest.mark.asyncio
async def test_restart_uninstall_is_still_blocked(env, tmp_path) -> None:
    """`server restart → registry 空` 不得让 destructive action fail-open。"""

    payload = _zip_payload(_module_files(catalog=True))
    await env.api.import_module(payload)
    game_key = await _persist_bound_save(env, target_id="room-restart")

    # 新进程：新 host（discover 重建 runtime），新 WebAPI，内存无实例。
    api2, host2, lorebook2 = _make_api(env.data_dir)
    try:
        host2.discover()
        assert MODULE_ID in host2.plugins
        # 来源注册表此时确实还没有 plugin 来源——guard 不能依赖它。
        assert api2._adventure_source_registry.source_for("plugin", MODULE_ID) is None

        with pytest.raises(ModuleInUse) as excinfo:
            await api2.uninstall_plugin(MODULE_ID)
        assert [item["game_key"] for item in excinfo.value.games] == [game_key]
    finally:
        lorebook2.close()


@pytest.mark.asyncio
async def test_ended_save_still_blocks_module_uninstall(env) -> None:
    await env.api.import_module(_zip_payload(_module_files(catalog=True)))
    await _persist_bound_save(env, target_id="room-ended", state=GameState.ENDED)

    with pytest.raises(ModuleInUse):
        await env.api.uninstall_plugin(MODULE_ID)


@pytest.mark.asyncio
async def test_unrelated_save_does_not_block_module_uninstall(env) -> None:
    await env.api.import_module(_zip_payload(_module_files(catalog=True)))
    registry = env.api._reg
    other = GameInstance(game_key=("web", "room-other", "bot"), world_id="greymoor")
    assert other.bind_adventure({
        "adventure_id": "core:lanterns_of_greymoor", "version": "1.0.0",
        "format": "diceframe:adventure-graph-v1",
        "content_digest": "sha256:other", "world_id": "greymoor",
    })
    await persistence.save(registry, other)
    registry.remove(other.game_key)

    assert (await env.api.uninstall_plugin(MODULE_ID))["ok"] is True


@pytest.mark.asyncio
async def test_module_usages_report_persisted_saves(env) -> None:
    await env.api.import_module(_zip_payload(_module_files(catalog=True)))
    game_key = await _persist_bound_save(env, target_id="room-usage", state=GameState.ENDED)

    usages = env.api.module_usages(MODULE_ID)

    assert usages["total_games"] == 1
    row = usages["usages"][0]["games"][0]
    assert row["game_key"] == game_key
    assert row["state"] == "ended"
    assert row["metadata_readable"] is True
