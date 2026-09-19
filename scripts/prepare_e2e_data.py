"""Create the deterministic save used by clean-data browser tests."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adventures import AdventureBundleLoader, AdventureResolver
from src.adventures.graph_v2 import ADVENTURE_GRAPH_FORMAT_V2
from src.engine.game_instance import GameInstance, GameState
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.services.legal import bundled_documents


E2E_GAME_KEY = ("web", "e2e-room", "web_bot")
E2E_DND_GAME_KEY = ("web", "e2e-dnd2024", "web_bot")
E2E_ADVENTURE_GAME_KEY = ("web", "e2e-adventure", "web_bot")

# FIX-00：Play 页的 AdventurePanel 必须能读到投影。这个 data-only v2 冒险同时
# 承载“GM 看得到秘密节点 / 分享玩家看不到”的浏览器验收。
E2E_ADVENTURE_DIRECTORY = "e2e_quest"
E2E_ADVENTURE_ID = f"user:{E2E_ADVENTURE_DIRECTORY}"
E2E_ADVENTURE_PUBLIC_NODE = "E2E Public Gate"
E2E_ADVENTURE_SECRET_NODE = "E2E Secret Ritual"

# FIX-06：模组库（ModulesView / ModuleDetailView）的浏览器验收需要一个真实的
# data-only content-pack。它带自己的冒险 id（不与上面的 user 冒险重名，避免制造
# 来源冲突），也不被任何存档绑定，因此详情页的三个受保护按钮都应为可用。
E2E_MODULE_ID = "e2e-module"
E2E_MODULE_NAME = "E2E Module"
E2E_MODULE_ADVENTURE_DIRECTORY = "module_quest"
E2E_MODULE_ADVENTURE_ID = f"plugin:{E2E_MODULE_ADVENTURE_DIRECTORY}"


def _write_e2e_module(data_dir: Path) -> None:
    """Install a real data-only content-pack for the module-library browser checks.

    FIX-06 §8：模组页面（已安装 / 本地导入 / 在线 + 详情页受保护按钮）必须有真实
    包可读，所以这里按 PluginHost 的包目录约定落一个 content-pack 包（不写任何
    Lorebook / 卡库：catalog 投递不 autoimport）。
    """

    package = data_dir / "plugin-packages" / E2E_MODULE_ID
    adventure_dir = package / "adventures" / E2E_MODULE_ADVENTURE_DIRECTORY
    manifest = {
        "schema_version": 1,
        "id": E2E_MODULE_ID,
        "name": E2E_MODULE_NAME,
        "version": "1.0.0",
        "plugin_type": "content-pack",
        "content_profile": "adventure-module",
        "content_delivery_mode": "catalog",
        "contributes": {},
        "adventure_packages": [f"adventures/{E2E_MODULE_ADVENTURE_DIRECTORY}"],
        "config_schema": "config.schema.json",
    }
    files: dict[str, dict] = {
        "plugin.json": manifest,
        "config.schema.json": {"type": "object", "properties": {}},
        f"adventures/{E2E_MODULE_ADVENTURE_DIRECTORY}/manifest.json": {
            "schema_version": 1,
            "adventure_id": E2E_MODULE_ADVENTURE_ID,
            "version": "1.0.0",
            "format": ADVENTURE_GRAPH_FORMAT_V2,
            "world_policy": "portable",
            "recommended_world_id": "default_fantasy",
            "required_runtime": {"id": "core:dnd2024", "minimum_version": 1},
            "default_locale": "zh-CN",
            "supported_locales": ["zh-CN"],
        },
        f"adventures/{E2E_MODULE_ADVENTURE_DIRECTORY}/adventure.json": {
            "schema_version": 1,
            "kind": "adventure",
            "id": E2E_MODULE_ADVENTURE_DIRECTORY,
            "source_ref": "diceframe-e2e:module-quest",
            "recommended_world_id": "default_fantasy",
            "automation_level": "guided",
            "chapters": [{"id": "module_chapter", "name": "E2E Module Chapter"}],
            "nodes": [{
                "id": "module_gate", "type": "scene", "chapter_id": "module_chapter",
                "name": "E2E Module Gate", "transitions": [],
            }],
            "objectives": [],
            "milestones": [],
            "start_node_ids": ["module_gate"],
        },
        f"adventures/{E2E_MODULE_ADVENTURE_DIRECTORY}/locales/zh-CN/adventure.json": {
            "locale_schema_version": 1,
            "locale": "zh-CN",
            "target": {"kind": "adventure", "id": E2E_MODULE_ADVENTURE_DIRECTORY},
            "fields": {"tutorial": {"name": "E2E 模组冒险", "summary": "模组库浏览器验收。"}},
        },
    }
    for relative, payload in files.items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    if not adventure_dir.is_dir():  # pragma: no cover - 落盘失败必须显式失败
        raise RuntimeError("failed to write the E2E module package")


def _write_save(data_dir: Path, instance: GameInstance) -> Path:
    save_file = data_dir / "saves" / "#".join(instance.game_key) / "state.json"
    save_file.parent.mkdir(parents=True, exist_ok=True)
    save_file.write_text(
        json.dumps(instance.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return save_file


def _e2e_players() -> dict[str, dict]:
    return {
        "e2e-gm": {
            "character_name": "E2E GM",
            "character_sheet": {
                "character_name": "E2E GM",
                "attributes": {"str": 12, "dex": 10},
                "skills": [],
                "hp": 10,
                "max_hp": 10,
                "portrait": {"kind": "builtin", "id": "warrior"},
                "equipment": [
                    {"name": "Longsword", "type": "weapon", "damage": "1d8", "slot": "main_hand"},
                    {"name": "Shield", "type": "armor", "slot": "off_hand"},
                ],
                "inventory": [{"name": "Healing Potion", "quantity": 2, "effect": "Restore health"}],
                "key_items": [{"name": "Town Gate Seal", "description": "Proof of passage"}],
            },
        },
        "e2e-player": {
            "character_name": "E2E Player",
            "character_sheet": {
                "character_name": "E2E Player",
                "attributes": {"str": 9, "dex": 13},
                "skills": [],
                "hp": 9,
                "max_hp": 9,
                "portrait": {"kind": "builtin", "id": "ranger"},
                "equipment": [{"name": "Shortbow", "type": "weapon", "damage": "1d6"}],
                "inventory": [{"name": "Rope", "quantity": 1}],
                "key_items": [],
            },
        },
    }


def _write_e2e_adventure(data_dir: Path) -> dict:
    """Install the data-only v2 adventure used by the browser play checks."""

    package = data_dir / "templates" / "adventures" / E2E_ADVENTURE_DIRECTORY
    files = {
        "manifest.json": {
            "schema_version": 1,
            "adventure_id": E2E_ADVENTURE_ID,
            "version": "1.0.0",
            "format": ADVENTURE_GRAPH_FORMAT_V2,
            "world_policy": "portable",
            "recommended_world_id": "default_fantasy",
            "required_runtime": {"id": "core:dnd2024", "minimum_version": 1},
            "default_locale": "zh-CN",
            "supported_locales": ["zh-CN"],
        },
        "adventure.json": {
            "schema_version": 1,
            "kind": "adventure",
            "id": E2E_ADVENTURE_DIRECTORY,
            "source_ref": "diceframe-e2e:e2e-quest",
            "recommended_world_id": "default_fantasy",
            "automation_level": "guided",
            "estimated_minutes": 15,
            "visibility": "public",
            "chapters": [
                {"id": "public_chapter", "name": "E2E Public Chapter"},
                {"id": "secret_chapter", "name": "E2E Secret Chapter", "visibility": "gm"},
            ],
            "nodes": [
                {
                    "id": "public_gate", "type": "scene", "chapter_id": "public_chapter",
                    "name": E2E_ADVENTURE_PUBLIC_NODE,
                    "transitions": [{"to": "secret_ritual"}],
                },
                {
                    "id": "secret_ritual", "type": "scene", "chapter_id": "secret_chapter",
                    "visibility": "gm", "name": E2E_ADVENTURE_SECRET_NODE, "transitions": [],
                },
            ],
            "objectives": [
                {"id": "reach_gate", "name": "Reach the gate", "node_ids": ["public_gate"]},
            ],
            "milestones": [],
            "start_node_ids": ["public_gate"],
        },
        "locales/zh-CN/adventure.json": {
            "locale_schema_version": 1,
            "locale": "zh-CN",
            "target": {"kind": "adventure", "id": E2E_ADVENTURE_DIRECTORY},
            "fields": {"tutorial": {"name": "E2E 冒险", "summary": "浏览器验收用冒险。"}},
        },
    }
    for relative, payload in files.items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    bundle_loader_dir = package.parent
    resolver = AdventureResolver.single_directory(bundle_loader_dir, source_kind="user")
    resolution = resolver.resolve_with_source(E2E_ADVENTURE_ID, "zh-CN")
    if resolution.bundle.content_digest != AdventureBundleLoader(
        bundle_loader_dir,
    ).resolve(E2E_ADVENTURE_ID, "zh-CN").content_digest:
        raise RuntimeError("E2E adventure digest is not stable")
    return resolution.binding("default_fantasy")


def prepare_e2e_data(data_dir: Path) -> Path:
    data_dir = data_dir.resolve()
    documents = bundled_documents()
    config_file = data_dir / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps(
            {
                "ai_providers": [{
                    "id": "e2e-provider",
                    "name": "E2E Provider",
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_format": "openai",
                    "models": ["e2e-chat", "e2e-embedding"],
                }],
                "llm_provider_ref": "e2e-provider",
                "model": "e2e-chat",
                "hub_telemetry_enabled": False,
                "hub_telemetry_choice_made": True,
                "legal_terms_accepted_updated_at": documents["terms"]["updated_at"],
                "legal_privacy_accepted_updated_at": documents["privacy"]["updated_at"],
                "legal_accepted_at": "2026-08-11T00:00:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (data_dir / "secrets.json").write_text(
        json.dumps(
            {"ai_provider_key_e2e-provider": secrets.token_urlsafe(24)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    instance = GameInstance(
        game_key=E2E_GAME_KEY,
        world_id="default_fantasy",
        world_name="E2E Adventure",
        group_name="Browser Tests",
        state=GameState.ACTIVE_ACTION,
        round_number=2,
        solo_mode=False,
        gm_uid="e2e-gm",
        scene="Town Gate",
    )
    instance.players = _e2e_players()
    instance.log = [{
        "round": 1,
        "actions": [
            {"user_id": "e2e-gm", "text": "Inspect the gate."},
            {"user_id": "e2e-player", "text": "Watch the road."},
        ],
        "gm_response": "The road is quiet.",
    }]
    save_file = _write_save(data_dir, instance)

    # FIX-00：独立的一局绑定 data-only v2 冒险，供 Play 页 AdventurePanel 的
    # “GM 可读 / 分享玩家只读公开部分”浏览器验收使用，避免扰动既有布局用例。
    adventure_instance = GameInstance(
        game_key=E2E_ADVENTURE_GAME_KEY,
        world_id="default_fantasy",
        world_name="E2E Adventure Module",
        group_name="Adventure Browser Tests",
        state=GameState.ACTIVE_ACTION,
        round_number=1,
        solo_mode=False,
        gm_uid="e2e-gm",
        scene="Town Gate",
    )
    adventure_instance.players = _e2e_players()
    if not adventure_instance.bind_adventure(_write_e2e_adventure(data_dir)):
        raise RuntimeError("failed to bind the E2E adventure in the fixture")
    _write_save(data_dir, adventure_instance)

    runtime = Dnd2024Runtime()
    choices = runtime.builder_choices(None, {"locale": "zh-CN"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == "stalwart_guardian")
    dnd_character = runtime.finalize_character(
        None, {**preset["draft"], "locale": "zh-CN", "name": "新手守护者"},
    )
    dnd_instance = GameInstance(
        game_key=E2E_DND_GAME_KEY,
        world_id="default_fantasy",
        world_name="D&D 2024 新手桌",
        group_name="Professional Ruleset Browser Tests",
        state=GameState.ACTIVE_ACTION,
        solo_mode=False,
        gm_uid="e2e-gm",
        max_players=2,
        scene="灰沼村议事厅",
        rule_id="dnd2024_srd",
        language="zh-CN",
    )
    dnd_instance.players = {
        "e2e-gm": {
            "character_name": "新手守护者",
            "character_sheet": dnd_character,
        },
    }
    if not dnd_instance.bind_ruleset_runtime(dnd_character["rule_binding"]):
        raise RuntimeError("failed to bind D&D 2024 runtime in E2E fixture")
    _write_save(data_dir, dnd_instance)
    # FIX-06：模组库的浏览器验收需要真实 content-pack 包（§8 的产品面）。
    _write_e2e_module(data_dir)
    return save_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args()
    environment_dir = os.getenv("DICEFRAME_E2E_DATA_DIR")
    raw_data_dir = args.data_dir or (Path(environment_dir) if environment_dir else None)
    if raw_data_dir is None:
        parser.error("--data-dir or DICEFRAME_E2E_DATA_DIR is required")
    print(prepare_e2e_data(raw_data_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
