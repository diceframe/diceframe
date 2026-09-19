"""施工单 §10：最终 Golden E2E（26 步完整链，全部走真实组件）。

```text
 1. 安装 data-only D&D module                       → api.import_module（真实宿主事务）
 2. module 含 Adventure v2 / custom monster / custom
    item / encounter / GM secret node+process       → 模组包自带 catalog + v2 图
 3. Create Game 选择 module Adventure                → POST /api/games/create（HTTP）
 4. World Seed 物化                                  → 创建事务内的初始化
 5. GM 进入 Play                                     → 服务端投影（owner 视角）
 6. shared player 进入                                → 同一投影（player 视角）
 7. player API 看不到 GM secret                       → 秘密节点 / 私有事实不可见
 8. 推进 public node                                 → authenticated adventure.node.complete intent
 9. encounter 解析 module monster                     → combat preset 来自模组 catalog
10. Combat Runtime 正常战斗                           → combat.start 事件批
11. 完成 node                                        → 进度推进 + 后继开放
12. item reward 走权威奖励路径                        → **待用户决策**（见文件末尾说明）
13. 启动 hidden ritual process                        → node on_complete 的 world_op
14. advance logical time                             → real RoundProcessor planning path
15. process settlement                               → 进程 completed
16. public world consequence                         → 后果节点的 world op 落地
17. authoritative World Memory 写入                   → drain outbox → MemoryStore
18. save                                             → registry.save
19. restart server                                    → 新 WebAPI / 新 PluginHost
20. load same game                                   → recover_all
21. Adventure/module source 正确                      → source-aware 解析回同一个包
22. rollback                                         → rollback_last_round
23. WorldState + progress + memory 正确撤销            → before-image 恢复
24. module update 被 bound-save guard 阻断             → ModuleInUse
25. module overwrite 被阻断                           → ModuleInUse
26. module uninstall 被阻断                           → ModuleInUse
```

第 12 步是**唯一未完成项**：既有经济权威（``queue_proposal`` 要求
``0 < amount``，``is_auto_settleable_reward`` 明确排除 item 奖励）没有任何
"免费发放物品"的路径，所以 Adventure 的 item_reward 现在 fail closed。
补齐它需要改奖励结算语义（授权决策），不能由修复侧自行决定；
``test_golden_e2e_step12_item_reward_is_fail_closed_until_decided`` 冻结当前
行为：世界不改、进度不回退半格、inventory 不动。
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.adventures.graph_v2 import ADVENTURE_GRAPH_FORMAT_V2
from src.commands.game_handler import GameHandler
from src.engine import persistence
from src.engine.game_instance import GameInstance, GameRegistry
from src.engine.world.read import fact_value, world_facts, world_processes
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.store import LorebookStore
from src.memory.delta import MemoryStore
from src.plugin_host.host import PluginHost
from src.rulesets.builtin import build_default_ruleset_registry
from src.rulesets.dnd2024.play.contracts import EncounterAccess
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.api import WebAPI
from src.webui.routes.game_lifecycle_routes import api_create_game
from src.webui.routes.games import register_games as register_game_query_routes
from src.webui.services import modules
from src.webui.services.module_validation import ModulePackageError

from webapi_harness import FakeLLMClient

MODULE_ID = "golden-module"
MODULE_LABEL = f"module:{MODULE_ID}"
ADVENTURE_ID = "plugin:golden_quest"
ADVENTURE_DIRECTORY = "golden_quest"
WORLD_ID = "default_fantasy"
RULE_ID = "dnd2024_srd"
MONSTER_ID = "clockwork_rat"
ITEM_ID = "brass_key"
ENCOUNTER_ID = "cellar_pack"
SECRET_NODE = "ritual_altar"
SECRET_FACT = "gm:ritual.altar_location"
PUBLIC_CONSEQUENCE_FACT = "world.ritual_resolved"


# ---- module package ---------------------------------------------------------


def _monster() -> dict:
    return {
        "kind": "monster", "profile_id": MONSTER_ID, "name": "Clockwork Rat",
        "source_ref": MODULE_LABEL, "hp": 11, "armor_class": 13, "speed": 30,
        "abilities": {"str": 6, "dex": 14, "con": 10, "int": 3, "wis": 10, "cha": 3},
        "attacks": [{"id": "bite", "damage": "1d4+1", "attack_bonus": 4}],
    }


def _item() -> dict:
    return {
        "kind": "item", "item_id": ITEM_ID, "name": "Brass Key",
        "source_ref": MODULE_LABEL, "category": "key_item",
        "description": "地窖铜钥匙。",
    }


def _encounter() -> dict:
    return {
        "kind": "encounter_profile", "encounter_id": ENCOUNTER_ID,
        "name": "Cellar Pack", "source_ref": MODULE_LABEL, "difficulty": "standard",
        "enemies": [{
            "ref": {"source": MODULE_LABEL, "kind": "monster", "id": MONSTER_ID},
            "count": 2,
        }],
    }


def _adventure_record() -> dict:
    """Adventure v2：public 节点 + encounter + GM 秘密节点 + 秘密进程后果。"""

    return {
        "schema_version": 1,
        "kind": "adventure",
        "id": ADVENTURE_DIRECTORY,
        "source_ref": "diceframe-golden:quest",
        "recommended_world_id": WORLD_ID,
        "automation_level": "guided",
        "format": ADVENTURE_GRAPH_FORMAT_V2,
        "chapters": [
            {"id": "public_chapter", "name": "Cellar"},
            {"id": "secret_chapter", "name": "Ritual", "visibility": "gm"},
        ],
        "nodes": [
            {
                # 8/9/10：公开节点带 encounter 引用，完成时启动秘密进程。
                "id": "gate", "type": "encounter", "chapter_id": "public_chapter",
                "name": "Cellar Gate",
                "encounter_ref": f"encounter_profile:{ENCOUNTER_ID}",
                "transitions": [
                    {"to": "vault"},
                    {"to": "aftermath", "conditions": [
                        {"type": "world.process_status", "id": "ritual",
                         "value": "completed"},
                    ]},
                ],
                "on_complete": [
                    {"type": "progress_event", "kind": "objective_completed",
                     "id": "obj_open"},
                    {"type": "world_op", "op": {
                        "op": "start_process", "process_id": "ritual",
                        "kind": "secret_ritual", "participants": ["npc:warden"],
                        "due_at": {"day": 1, "minute": 120}, "visibility": "gm",
                    }},
                ],
            },
            {
                # 12：item reward 走权威奖励路径（提案 → GM 确认 → 物品）。
                "id": "vault", "type": "scene", "chapter_id": "public_chapter",
                "name": "Vault",
                "transitions": [{"to": "aftermath"}],
                "on_complete": [
                    {"type": "item_reward", "ref": f"item:{ITEM_ID}"},
                ],
            },
            {
                # 16：进程结算后的公开后果（秘密节点只对 GM 可见）。
                "id": "aftermath", "type": "scene", "chapter_id": "public_chapter",
                "name": "Aftermath",
                "transitions": [{"to": SECRET_NODE, "conditions": [
                    {"type": "world.fact_equals", "key": PUBLIC_CONSEQUENCE_FACT,
                     "value": True},
                ]}],
                "on_complete": [
                    {"type": "world_op", "op": {
                        "op": "set_fact", "key": PUBLIC_CONSEQUENCE_FACT, "value": True,
                    }},
                ],
            },
            {
                "id": SECRET_NODE, "type": "scene", "chapter_id": "secret_chapter",
                "visibility": "gm", "name": "Ritual Altar", "transitions": [],
            },
        ],
        "objectives": [{"id": "obj_open", "name": "Open the gate", "node_ids": ["gate"]}],
        "milestones": [],
        "start_node_ids": ["gate"],
        "world_seed": {
            "entities": [
                {"entity_id": "npc:warden", "kind": "npc"},
                {"entity_id": "location:cellar", "kind": "location"},
            ],
            "relations": [{
                "relation_id": "rel:warden-cellar", "kind": "located_at",
                "from_ref": "npc:warden", "to_ref": "location:cellar",
            }],
            "facts": [
                {"key": "location:cellar.door", "value": "locked"},
                {"key": SECRET_FACT, "value": "under the altar", "visibility": "gm"},
            ],
            "processes": [],
        },
    }


def _module_files() -> dict[str, bytes]:
    files = {
        "plugin.json": {
            "schema_version": 1, "id": MODULE_ID, "name": "Golden Module",
            "version": "1.0.0", "plugin_type": "content-pack",
            "content_profile": "adventure-module",
            "content_delivery_mode": "catalog",
            "ruleset_catalogs": ["packs/dnd2024"],
            "adventure_packages": [f"adventures/{ADVENTURE_DIRECTORY}"],
            "contributes": {},
        },
        "config.schema.json": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": False, "ui": {"control": "switch"}},
            },
        },
        "packs/dnd2024/monsters.json": _monster(),
        "packs/dnd2024/items.json": _item(),
        "packs/dnd2024/encounters.json": _encounter(),
        f"adventures/{ADVENTURE_DIRECTORY}/manifest.json": {
            "schema_version": 1, "adventure_id": ADVENTURE_ID, "version": "1.0.0",
            "format": ADVENTURE_GRAPH_FORMAT_V2, "world_policy": "portable",
            "recommended_world_id": WORLD_ID,
            "required_runtime": {"id": "core:dnd2024", "minimum_version": 1},
            "default_locale": "zh-CN", "supported_locales": ["zh-CN"],
        },
        f"adventures/{ADVENTURE_DIRECTORY}/adventure.json": _adventure_record(),
        f"adventures/{ADVENTURE_DIRECTORY}/locales/zh-CN/adventure.json": {
            "locale_schema_version": 1, "locale": "zh-CN",
            "target": {"kind": "adventure", "id": ADVENTURE_DIRECTORY},
            "fields": {"tutorial": {"name": "黄金链路", "summary": "施工单 §10。"}},
        },
    }
    return {
        name: json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for name, payload in files.items()
    }


# ---- environment ------------------------------------------------------------


def _write_world(worlds_dir: Path) -> None:
    worlds_dir.mkdir(parents=True, exist_ok=True)
    (worlds_dir / f"{WORLD_ID}.json").write_text(json.dumps({
        "world_id": WORLD_ID, "world_name": "默认奇幻",
        "description": "golden e2e world", "world_setting": "地窖",
        "starter_scene": "城门", "default_rule": RULE_ID, "starter_lorebook": [],
    }, ensure_ascii=False), encoding="utf-8")


def _dnd_characters(count: int = 2) -> list[dict]:
    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "zh-CN"})
    preset = next(
        item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian"
    )
    names = ("守门人", "同伴")
    return [
        {
            **runtime.finalize_character(
                None, {**preset["draft"], "locale": "zh-CN", "name": names[index]},
            ),
            "character_name": names[index],
        }
        for index in range(count)
    ]


class _Golden:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.data_dir = tmp_path / "data"
        self.rules_dir = self.data_dir / "rules"
        self.worlds_dir = self.data_dir / "worlds"
        self.adventures_dir = self.data_dir / "templates" / "adventures"
        self.prompts_dir = self.data_dir / "prompts"
        for directory in (
            self.rules_dir, self.worlds_dir, self.adventures_dir,
            self.prompts_dir, self.data_dir / "saves",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (self.prompts_dir / "gm_system_zh.md").write_text("你是测试 GM。", encoding="utf-8")
        self.rules_dir.joinpath(f"{RULE_ID}.json").write_text(json.dumps({
            "rule_id": RULE_ID, "rule_name": "5E 2024 SRD 专业规则", "dice_system": "d20",
            "runtime": {"id": "core:dnd2024", "minimum_version": 1},
            "attributes": [
                {"key": key, "name": key.upper(), "min": 3, "max": 20}
                for key in ("str", "dex", "con", "int", "wis", "cha")
            ],
        }, ensure_ascii=False), encoding="utf-8")
        _write_world(self.worlds_dir)
        self.plugins_root = self.data_dir / "plugin-packages"
        self.memory = MemoryStore(self.data_dir / "memory.db")
        self.memory.open()
        self.lorebook = LorebookStore(self.data_dir / "lorebook.db")
        self.lorebook.open()
        self.api, self.host = self._make_api()

    def _make_api(self) -> tuple[WebAPI, PluginHost]:
        registry = GameRegistry(self.data_dir / "saves")
        host = PluginHost(
            plugins_dir=self.plugins_root, data_dir=self.data_dir / "plugins",
        )
        host.discover()
        if MODULE_ID in host.plugins:
            host.plugins[MODULE_ID].status = "enabled"
        llm = FakeLLMClient()
        handler = GameHandler(
            registry=registry, llm_client=llm, lorebook_matcher=KeywordMatcher(),
            lorebook_store=self.lorebook, memory_store=self.memory,
            prompts_dir=self.prompts_dir, rules_dir=self.rules_dir,
            worlds_dir=self.worlds_dir,
        )
        api = WebAPI(
            registry=registry, lorebook=self.lorebook, memory=self.memory,
            rules_dir=self.rules_dir, handler=handler, llm_client=llm,
            worlds_dir=self.worlds_dir, adventures_dir=self.adventures_dir,
            plugin_host=host, ruleset_registry=build_default_ruleset_registry(),
            config_state={}, save_config=lambda: None,
        )
        api.attach_package_hooks(host)
        api._sync_plugin_adventure_sources()
        api._sync_module_catalogs()
        return api, host

    # ---- 1/2：安装模组 -----------------------------------------------------
    def install_module(self) -> None:
        module_dir = self.plugins_root / MODULE_ID
        for name, payload in _module_files().items():
            target = module_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

    # ---- 3/4：HTTP 创建对局（含 world seed 物化） ---------------------------
    async def create_game(self) -> dict:
        app = web.Application()
        app["api"] = self.api
        app.router.add_post("/api/games/create", api_create_game)
        register_game_query_routes(app)
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/api/games/create", json={
                "world_id": WORLD_ID,
                "game_name": "黄金链路",
                "rule_id": RULE_ID,
                "adventure_id": ADVENTURE_ID,
                "solo": False,
                "language": "zh-CN",
                "players": _dnd_characters(2),
            }, headers={"X-TRPG-Confirm": "true"})
            body = await response.json()
        assert response.status == 200, body
        assert body.get("ok") is True, body
        return body

    def instance(self, game_key: str) -> GameInstance:
        instance = self.api.get_game_instance(game_key)
        assert instance is not None
        return instance

    def runtime(self) -> Dnd2024Runtime:
        runtime = Dnd2024Runtime()
        runtime.set_adventure_resolver(self.api._adventure_resolver)
        runtime.set_module_content_sources(self.api.module_content_sources)
        return runtime

    def close(self) -> None:
        self.lorebook.close()
        self.memory.close()


@pytest.fixture()
def golden(tmp_path):
    environment = _Golden(tmp_path)
    try:
        yield environment
    finally:
        environment.close()


async def _created_golden(golden) -> dict:
    await golden.api.import_module(_zip_module_files())
    # 真实 enable 路径：启用后模组的 Adventure 来源才进入运行时来源注册表。
    await golden.api.update_plugin_config(MODULE_ID, {"enabled": True})
    created = await golden.create_game()
    assert created["ok"] is True, created
    return created


async def _complete_node(golden, created: dict, node_id: str) -> dict:
    """Complete a v2 node through the public authoritative intent seam."""

    gm_uid = str(created["players"][0]["user_id"])
    result = await golden.api.ruleset_submit_intent(
        created["game_key"], gm_uid, True,
        {"type": "adventure.node.complete", "node_id": node_id},
    )
    assert result["ok"] is True, result
    return result["result"]["adventure_node"]


async def _advance_time_through_round_processor(
    golden, instance: GameInstance, minutes: int,
) -> list[dict]:
    """Use the real round-planning application path, not Adventure helpers."""

    async def planned(*_args, **_kwargs):
        return [], {
            "available": True, "skipped": False, "total_tokens": 0,
            "errors": [], "overreach": [], "world_requirements": [],
            "economy_offers": [], "unpriced_purchase_intents": [],
            "world_time_advance": {"minutes": minutes},
        }

    instance.state = instance.state.ACTIVE_JUDGMENT
    instance.reset_round_checks()
    with patch("src.commands.round_processor.plan_round_checks", planned):
        await golden.api._handler.prepare_round_checks_ai(instance)
    return list(instance.last_world_events)


def _zip_module_files() -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in _module_files().items():
            archive.writestr(f"{MODULE_ID}/{name}", payload)
    return buffer.getvalue()


# ---- 1–4：安装 + 创建（HTTP）+ world seed 物化 ------------------------------


@pytest.mark.asyncio
async def test_golden_steps_1_to_4_install_and_create_through_http(golden) -> None:
    """步骤 1-4：真实安装事务 → HTTP 创建 → 创建事务内物化世界种子。"""

    result = await golden.api.import_module(_zip_module_files())
    assert result["ok"] is True
    # 步骤 2：模组内容确实进了 runtime catalog（monster / item / encounter）。
    detail = golden.api.module_detail(MODULE_ID)["module"]
    assert [row["adventure_id"] for row in detail["adventures"]] == [ADVENTURE_ID]
    assert detail["actions"]["uninstall"]["allowed"] is True

    await golden.api.update_plugin_config(MODULE_ID, {"enabled": True})
    created = await golden.create_game()

    instance = golden.instance(created["game_key"])
    # 步骤 3：绑定的就是模组里的那份 v2 包（来源身份可解析）。
    binding = instance.adventure_binding
    assert binding["adventure_id"] == ADVENTURE_ID
    assert binding["source_kind"] == "plugin"
    assert binding["source_id"] == MODULE_ID
    # 步骤 4：world seed 已物化（entity / relation / fact 三类 + GM 私密事实）。
    facts = world_facts(instance.world_state)
    assert facts["location:cellar.door"]["value"] == "locked"
    assert facts[SECRET_FACT]["visibility"] == "gm"
    assert "npc:warden" in instance.world_state["entities"]
    # 进度初始化，起始节点 active。
    assert instance.adventure_progress["active_nodes"] == ["gate"]


# ---- 5–7：GM / shared player 视角 ------------------------------------------


@pytest.mark.asyncio
async def test_golden_steps_5_to_7_shared_player_never_sees_gm_material(golden) -> None:
    created = await _created_golden(golden)
    game_key = created["game_key"]
    gm_uid = str(created["players"][0]["user_id"])

    gm_view = golden.api.game_adventure_projection(game_key, viewer_is_gm=True)["adventure"]
    player_view = golden.api.game_adventure_projection(game_key, viewer_is_gm=False)["adventure"]

    assert gm_view["available"] is True and player_view["available"] is True
    gm_node_ids = {node["id"] for node in gm_view["projection"]["nodes"]}
    player_node_ids = {node["id"] for node in player_view["projection"]["nodes"]}
    assert SECRET_NODE in gm_node_ids
    assert SECRET_NODE not in player_node_ids
    # on_complete（含秘密进程声明）绝不出现在玩家投影里。
    assert all("on_complete" not in node for node in player_view["projection"]["nodes"])

    # 步骤 7：HTTP 面对共享玩家也只返回公开部分（`?user=` 即玩家视角）。
    app = web.Application()
    app["api"] = golden.api
    register_game_query_routes(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/games/{game_key}/adventure?user={gm_uid}&share=1")
        body = await response.json()
    assert response.status == 200
    shared_node_ids = {node["id"] for node in body["adventure"]["projection"]["nodes"]}
    assert SECRET_NODE not in shared_node_ids
    # 世界事实层面的 GM 私有事实同样不进入玩家安全面。
    from src.engine.world.read import project_visible_state

    assert SECRET_FACT not in project_visible_state(
        golden.instance(game_key), viewer_is_gm=False,
    )["facts"]


# ---- 8–11：公开节点 → encounter → 战斗 → 完成节点 --------------------------


@pytest.mark.asyncio
async def test_golden_steps_8_to_11_encounter_uses_module_monsters(golden) -> None:
    created = await _created_golden(golden)
    instance = golden.instance(created["game_key"])
    runtime = golden.runtime()

    # 步骤 9：encounter 的敌人来自模组 catalog（不是内联 statblock）。
    engine = runtime._combat_engine(instance, EncounterAccess.sandbox(), "zh-CN")
    presets = {preset["id"]: preset for preset in engine.encounter_presets()}
    assert ENCOUNTER_ID in presets
    assert [enemy["id"] for enemy in presets[ENCOUNTER_ID]["enemies"]] == [
        f"{MONSTER_ID}_1", f"{MONSTER_ID}_2",
    ]
    assert presets[ENCOUNTER_ID]["enemies"][0]["hp"] == 11

    # 步骤 10：Combat Runtime 真的能开打（权威 intent → 事件批）。剧情已绑定
    # 遭遇，所以按引擎的剧情语义声明预设（不接受 "sandbox" 摘出去）。
    resolved = runtime.resolve_intent(instance, {
        "intent_id": "golden-combat-1",
        "type": "combat.start",
        "expected_version": int(instance.ruleset_state.get("version", 0) or 0),
        "submitted_by": str(created["players"][0]["user_id"]),
        "encounter_preset_id": ENCOUNTER_ID,
    }, random.Random(11))
    assert resolved["ok"] is True, resolved
    started = next(
        event for event in resolved["event_batch"]["events"]
        if event["type"] == "dnd2024.combat.started"
    )
    assert set(started["enemies"]) == {f"{MONSTER_ID}_1", f"{MONSTER_ID}_2"}
    assert started["enemies"][f"{MONSTER_ID}_1"]["attacks"][0]["damage"] == "1d4+1"

    # 步骤 8/11：完成公开节点（objective 完成 + 秘密进程启动）。
    result = await _complete_node(golden, created, "gate")

    assert result["activated_nodes"] == ["vault"]
    assert instance.adventure_progress["completed_nodes"] == ["gate"]
    assert "obj_open" in instance.adventure_progress["completed_objectives"]
    # 步骤 13：GM 的秘密进程已启动（gm 可见性）。
    ritual = world_processes(instance.world_state)["ritual"]
    assert ritual["status"] == "running" and ritual["visibility"] == "gm"
    # 后果节点要等进程结算（gate: world.process_status）——现在还不该开放。
    assert "aftermath" not in instance.adventure_progress["active_nodes"]


# ---- 12：item reward 走权威奖励路径 ----------------------------------------


@pytest.mark.asyncio
async def test_golden_step12_item_reward_goes_through_the_reward_authority(
    golden,
) -> None:
    """步骤 12：``item_reward`` → ContentRef 解析 → reward intent → 既有提案权威
    → GM 确认 → 角色 inventory。**任何时候都不直接写 inventory**。"""

    created = await _created_golden(golden)
    game_key = created["game_key"]
    instance = golden.instance(game_key)
    gm_uid = str(created["players"][0]["user_id"])
    await _complete_node(golden, created, "gate")
    result = await _complete_node(golden, created, "vault")

    # ① 转换器把裸物品 ref 解析成模组 catalog 里的 intent（来源 = owning module）。
    assert [intent["name"] for intent in result["reward_intents"]] == ["Brass Key"]
    assert result["reward_intents"][0]["ref"]["source"] == MODULE_LABEL
    # ② 权威出口只排队了一条 pending 提案，物品**还没有**进 inventory。
    proposal_id = result["queued_rewards"][0]["proposal_id"]
    proposal = next(
        item for item in instance.economy["proposals"] if item["id"] == proposal_id
    )
    assert proposal["kind"] == "reward"
    assert proposal["amount"] == 0
    assert proposal["status"] == "pending"
    assert proposal["approval_policy"] == "gm"
    assert proposal["rewards"] == [{"name": "Brass Key", "category": "key_item"}]
    sheet = instance.get_character_sheet(gm_uid) or {}
    assert "Brass Key" not in json.dumps(sheet, ensure_ascii=False)

    # ③ GM 用既有确认路径结算 → 物品由角色状态权威写入（带 before-image）。
    settled = await golden.api.resolve_payment(game_key, proposal_id, True, gm_uid)
    assert settled["ok"] is True, settled
    sheet = instance.get_character_sheet(gm_uid) or {}
    assert "Brass Key" in json.dumps(sheet, ensure_ascii=False)
    transaction = next(
        item for item in instance.economy["transactions"]
        if item.get("proposal_id") == proposal_id
    )
    assert transaction["status"] == "committed"
    assert [row["recipient_uid"] for row in transaction["reward_snapshots"]] == [gm_uid]

    # ④ 幂等：同一批奖励（同一 source_ref）不会被排两次队。
    queued_again = golden.api._queue_adventure_reward_intents(
        instance, result["reward_intents"],
    )
    assert queued_again[0]["proposal_id"] == proposal_id
    assert len([item for item in instance.economy["proposals"]]) == 1


@pytest.mark.asyncio
async def test_golden_node_save_failure_restores_world_progress_and_economy(golden) -> None:
    """The public intent transaction cannot leave a queued reward half-committed."""

    created = await _created_golden(golden)
    instance = golden.instance(created["game_key"])
    await _complete_node(golden, created, "gate")
    before = {
        "world_state": json.loads(json.dumps(instance.world_state)),
        "adventure_progress": json.loads(json.dumps(instance.adventure_progress)),
        "economy": json.loads(json.dumps(instance.economy)),
    }
    original = golden.api._ruleset_gameplay_dependencies

    async def save_failed(_instance):
        raise RuntimeError("storage unavailable")

    golden.api._ruleset_gameplay_dependencies = replace(
        original, save_instance=save_failed,
    )
    try:
        gm_uid = str(created["players"][0]["user_id"])
        result = await golden.api.ruleset_submit_intent(
            created["game_key"], gm_uid, True,
            {"type": "adventure.node.complete", "node_id": "vault"},
        )
    finally:
        golden.api._ruleset_gameplay_dependencies = original
    assert result["code"] == "ADVENTURE_NODE_FAILED"
    assert instance.world_state == before["world_state"]
    assert instance.adventure_progress == before["adventure_progress"]
    assert instance.economy == before["economy"]


@pytest.mark.asyncio
# ---- 13–17：进程结算 → 公开后果 → 权威记忆 ---------------------------------


@pytest.mark.asyncio
async def test_golden_steps_13_to_17_process_consequence_and_world_memory(golden) -> None:
    created = await _created_golden(golden)
    game_key = created["game_key"]
    instance = golden.instance(game_key)
    await _complete_node(golden, created, "gate")

    # 步骤 14/15：逻辑时间推进 → 秘密进程按 due_at 结算。
    await _advance_time_through_round_processor(golden, instance, 24 * 60)
    assert world_processes(instance.world_state)["ritual"]["status"] == "completed"

    # 步骤 16：进程完成后 gate 才开放后果节点，公开后果由它自己的 world op 落地。
    assert "aftermath" in instance.adventure_progress["active_nodes"]
    await _complete_node(golden, created, "aftermath")
    assert fact_value(instance.world_state, PUBLIC_CONSEQUENCE_FACT) is True
    # 后果满足后秘密节点才对 GM 开放（玩家看不到它）。
    assert SECRET_NODE in instance.adventure_progress["active_nodes"]

    # 步骤 17：WorldEvent receipts → 确定性 memory 投影 → outbox → MemoryStore。
    assert await golden.api.drain_economy_outbox(game_key) is True

    gm_memories = golden.api.list_memories(game_key, viewer_is_gm=True)
    player_memories = golden.api.list_memories(game_key, viewer_is_gm=False)
    assert gm_memories["total"] >= 1
    values = json.dumps(gm_memories["memories"], ensure_ascii=False)
    assert "process_settled" in values and "ritual" in values
    # FIX-05 §7.4：gm 可见性记忆不进入玩家安全面。
    assert all(
        row.get("visibility") != "gm" for row in player_memories["memories"]
    ), player_memories


# ---- 18–21：保存 → 重启 → 载入 → 来源正确 ----------------------------------


@pytest.mark.asyncio
async def test_golden_steps_18_to_21_survive_restart_with_sources(golden) -> None:
    created = await _created_golden(golden)
    game_key = created["game_key"]
    instance = golden.instance(game_key)
    await _complete_node(golden, created, "gate")
    await _advance_time_through_round_processor(golden, instance, 24 * 60)

    # 步骤 18：保存（真实 registry.save）。
    await persistence.save(golden.api._reg, instance)
    golden.api._reg.remove(instance.game_key)

    # 步骤 19：新进程（新 WebAPI / 新 PluginHost / 新 MemoryStore 已在 fixture 内）。
    reborn, host = golden._make_api()
    try:
        recovered = await reborn._reg.recover_all()
        assert [item.game_key for item in recovered] == [instance.game_key]
        loaded = recovered[0]

        # 步骤 20：世界 / 进度 / 绑定一起活过重启。
        assert fact_value(loaded.world_state, "location:cellar.door") == "locked"
        assert loaded.adventure_progress["completed_nodes"] == ["gate"]
        assert loaded.adventure_binding["adventure_id"] == ADVENTURE_ID

        # 步骤 21：Adventure 与模组来源都正确（source-aware 解析回同一个包）。
        projection = reborn.game_adventure_projection(game_key, viewer_is_gm=True)
        assert projection["adventure"]["available"] is True
        assert projection["adventure"]["binding"]["source_kind"] == "plugin"
        assert projection["adventure"]["binding"]["source_id"] == MODULE_ID
        assert modules.module_detail(
            reborn._module_dependencies, MODULE_ID,
        )["module"]["adventures"][0]["adventure_id"] == ADVENTURE_ID
    finally:
        host.plugins.clear()


# ---- 22–23：rollback 撤销世界 / 进度 / 记忆 --------------------------------


@pytest.mark.asyncio
async def test_golden_steps_22_to_23_rollback_restores_world_and_progress(golden) -> None:
    created = await _created_golden(golden)
    game_key = created["game_key"]
    instance = golden.instance(game_key)
    # 判定入口快照 = "本轮可能改过的东西"（世界真相 + Adventure 进度）。
    from src.engine.memory_outbox import pending_memory_reversals
    from src.engine.round_snapshots import capture_round_entity_snapshot

    capture_round_entity_snapshot(instance)
    world_before = json.loads(json.dumps(instance.world_state))
    progress_before = json.loads(json.dumps(instance.adventure_progress))
    memories_before = golden.api.list_memories(game_key, viewer_is_gm=True)["total"]

    # 本轮结算：完成节点（世界 + 进度）+ 物品奖励 + 时间推进（进程结算 + 权威记忆）。
    await _complete_node(golden, created, "gate")
    gm_uid = str(created["players"][0]["user_id"])
    reward = await _complete_node(golden, created, "vault")
    await golden.api.resolve_payment(
        game_key, reward["queued_rewards"][0]["proposal_id"], True, gm_uid,
    )
    assert "Brass Key" in json.dumps(
        instance.get_character_sheet(gm_uid) or {}, ensure_ascii=False,
    )
    await _advance_time_through_round_processor(golden, instance, 24 * 60)
    assert await golden.api.drain_economy_outbox(game_key) is True
    assert golden.api.list_memories(game_key, viewer_is_gm=True)["total"] > memories_before

    await instance.finish_judgment("本轮结束")
    await persistence.save(golden.api._reg, instance)

    # 步骤 22：真实 rollback（回滚上一轮）。
    result = await golden.api.rollback_round(game_key)

    assert result["ok"] is True, result
    # 步骤 23：世界真相与 Adventure 进度一起回到 before-image（不留半回滚）。
    assert instance.world_state == world_before
    assert instance.adventure_progress == progress_before
    assert instance.adventure_progress["completed_nodes"] == []
    # 物品奖励同样被撤销（reward_snapshots 的 before-image）。
    assert "Brass Key" not in json.dumps(
        instance.get_character_sheet(gm_uid) or {}, ensure_ascii=False,
    )
    # 权威世界记忆：回滚同步撤销已投递的投递记录，记忆回到本轮之前的条数。
    assert pending_memory_reversals(instance) == []
    assert [
        row.get("status") for row in instance.economy.get("external_effects_outbox", [])
    ] == ["reversed"]
    assert golden.api.list_memories(game_key, viewer_is_gm=True)["total"] == memories_before


@pytest.mark.asyncio
async def test_golden_create_right_after_restart_uses_a_freshly_synced_registry(
    golden,
) -> None:
    """回归：重启后**先开局**（不先读任何目录）也必须解析到模组冒险。

    FIX-07 修复前：来源注册表只在"读过冒险/模组页面"或"刚装完包"时同步，所以
    `重启 → 用模组冒险开局` 会报 `adventure package does not exist`。
    """

    await golden.api.import_module(_zip_module_files())
    await golden.api.update_plugin_config(MODULE_ID, {"enabled": True})

    reborn, host = golden._make_api()
    try:
        # 模拟 bootstrap：启动即刷新内容来源（组合根接线）。
        reborn.refresh_content_sources()

        created = await reborn.create_game(
            WORLD_ID, "重启后开局", rule_id=RULE_ID, adventure_id=ADVENTURE_ID,
            players=_dnd_characters(1),
            gm_uid="gm", language="zh-CN",
        )

        assert created["ok"] is True, created
        instance = reborn.get_game_instance(created["game_key"])
        assert instance.adventure_binding["source_id"] == MODULE_ID
    finally:
        host.plugins.clear()


# ---- 24–26：bound-save guard 阻断 update / overwrite / uninstall -----------

@pytest.mark.asyncio
async def test_golden_steps_24_to_26_bound_save_blocks_module_mutations(golden) -> None:
    created = await _created_golden(golden)
    # 对局仍在内存注册表里（= 有存档绑定该模组）。
    assert golden.api.get_game_instance(created["game_key"]) is not None

    guard = golden.api.module_detail(MODULE_ID)["module"]["actions"]
    assert guard["update"]["allowed"] is False
    assert guard["uninstall"]["allowed"] is False
    assert guard["overwrite"]["allowed"] is False
    assert guard["update"]["reason"] == "MODULE_IN_USE"
    assert guard["update"]["games"][0]["game_key"] == created["game_key"]

    with pytest.raises(modules.ModuleInUse):
        await golden.api.update_marketplace_plugin(MODULE_ID)
    with pytest.raises(modules.ModuleInUse):
        await golden.api.import_module(_zip_module_files(), True)
    with pytest.raises(modules.ModuleInUse):
        await golden.api.uninstall_plugin(MODULE_ID)

    # 包未被破坏。
    assert MODULE_ID in golden.host.plugins


__all__ = ["SimpleNamespace"]
