"""D&D 2024 AI 临时遭遇兜底回归测试。

覆盖：GM 生成（无副作用）、玩家 403、剧情遭遇不可覆盖、AI 非法怪物拒绝、
确认走现有 combat.start(mode=sandbox)、篡改 preview 被权威校验拒绝、
GM 编辑并展开数量后的最终 payload 重走临时遭遇边界与队伍安全上限、
LLM 失败无副作用、Adventure 绑定保持。核心原则：AI 提案、GM 编辑确认、
Combat Engine 权威结算。
"""

from __future__ import annotations

import copy
import json
import random
from types import SimpleNamespace

import pytest

from src.commands.round_effects import apply_ruleset_combat_signal
from src.engine.game_instance import GameInstance
from src.rules.rule_system import RuleSystem
from src.rulesets.dnd2024.combat.validation import validate_enemy_profiles
from src.rulesets.dnd2024.director.temporary_encounter import (
    TEMPORARY_ENCOUNTER_TOOL_NAME,
    normalize_temporary_encounter,
    plan_temporary_encounter,
    validate_temporary_encounter_balance,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.rulesets.legacy_adapter import LegacyRulesetAdapter
from src.rulesets.registry import RulesetRuntimeRegistry
from src.webui.services import ruleset_gameplay
from src.engine.modules import legacy_combat, narrative_notes, ruleset_runtime, table_settings

_RULE = RuleSystem({
    "rule_id": "test_dnd2024",
    "runtime": {"id": "core:dnd2024", "minimum_version": 1},
})

# 模拟模型输出：id 交给服务端归一化（同名 → 唯一 id）。
_WOLF_RAW = {
    "title": "腐化狼群",
    "description": "两只被黑色孢子侵蚀的狼从废墟后冲出。",
    "enemies": [
        {
            "name": "Corrupted Wolf", "hp": 11, "armor_class": 13,
            "speed": 40, "position": 25, "initiative_modifier": 2,
            "attacks": [{"name": "bite", "attack_bonus": 4, "damage": "1d6+2", "range": 5}],
        },
        {
            "name": "Corrupted Wolf", "hp": 11, "armor_class": 13,
            "speed": 40, "position": 30,
            "attacks": [{"name": "bite", "attack_bonus": 4, "damage": "1d6+2", "range": 5}],
        },
    ],
}


class _FakeLLM:
    def __init__(self, arguments=None, error=None, *, tool_name=TEMPORARY_ENCOUNTER_TOOL_NAME):
        self.arguments = arguments
        self.error = error
        self.tool_name = tool_name
        self.calls = []

    async def call_tools(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        tool_calls = (
            [{"name": self.tool_name, "arguments": self.arguments}]
            if self.arguments is not None else []
        )
        return SimpleNamespace(
            tool_calls=tool_calls, total_tokens=9,
            provider_used="test", native_tools=True,
        )


class _SaveRegistry:
    def __init__(self) -> None:
        self.items: dict[tuple[str, ...], GameInstance] = {}

    def get(self, key):
        return self.items.get(tuple(key))

    async def save(self, instance) -> None:
        self.items[tuple(instance.game_key)] = instance


def _character(runtime: Dnd2024Runtime, preset_id: str, name: str) -> dict:
    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = next(item for item in choices["quick_presets"] if item["id"] == preset_id)
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": name},
    )


def _runtime_instance(
    *, adventure: bool = False, walk_to_combat: bool = False,
) -> tuple[Dnd2024Runtime, GameInstance]:
    runtime = Dnd2024Runtime()
    instance = GameInstance(
        game_key=("test", "temp-encounter", "bot"),
        world_id="greymoor" if adventure else "default_fantasy",
        rule_id="dnd2024_srd", gm_uid="gm", language="en",
    )
    gm = _character(runtime, "stalwart_guardian", "Arden")
    instance.players["gm"] = {"character_name": "Arden", "character_sheet": gm}
    ally = _character(runtime, "curious_arcanist", "Mira")
    instance.players["ally"] = {"character_name": "Mira", "character_sheet": ally}
    assert instance.bind_ruleset_runtime(gm["rule_binding"])
    if adventure:
        package = runtime._adventure_loader.resolve("core:lanterns_of_greymoor", "en")
        assert instance.bind_adventure(package.binding("greymoor"))
    if walk_to_combat:
        table_settings.replace_solo_mode(instance, True)
        # quick_start 已直接进入教学第一步；只需连续推进到绑定遭遇的节点。
        _submit(runtime, instance, "session_zero.quick_start")
        for choice in ("inspect_cold_ash", "reassure_mira", "follow_small_tracks"):
            _submit(runtime, instance, "tutorial.choose", choice_id=choice)
    return runtime, instance


def _submit(
    runtime: Dnd2024Runtime, instance: GameInstance, intent_type: str,
    submitted_by: str = "gm", **fields,
) -> dict:
    version = int(ruleset_runtime.state(instance).get("version", 0) or 0)
    intent = {
        "intent_id": f"intent-{intent_type}-{version}",
        "type": intent_type,
        "expected_version": version,
        "submitted_by": submitted_by,
        **fields,
    }
    resolved = runtime.resolve_intent(instance, intent, random.Random(7))
    assert resolved["ok"] is True, resolved
    applied = runtime.apply_event_batch(instance, resolved["event_batch"])
    assert applied["applied"] is True
    return resolved


def _dependencies(
    runtime: Dnd2024Runtime,
    registry: _SaveRegistry,
    instance: GameInstance,
    llm_client=None,
) -> ruleset_gameplay.RulesetGameplayDependencies:
    return ruleset_gameplay.RulesetGameplayDependencies(
        get_instance=registry.get,
        parse_game_key=lambda key: tuple(key.split("|")),
        load_rule_for_game=lambda inst: _RULE,
        ruleset_registry=RulesetRuntimeRegistry([LegacyRulesetAdapter(), runtime]),
        resolve_adventure_binding=lambda adventure_id, rt, world_id, language: dict(
            instance.adventure_binding or {},
        ),
        save_instance=registry.save,
        apply_memory_delta=None,
        resolve_llm_client=lambda: llm_client,
    )


def _state_fingerprint(instance: GameInstance) -> tuple:
    return (
        copy.deepcopy(ruleset_runtime.state(instance)),
        copy.deepcopy(ruleset_runtime.event_ledger(instance)),
        legacy_combat.combat_active(instance),
        legacy_combat.combat_state(instance),
        instance.round_number,
    )


# ---- normalize / 公共校验 ----


def test_normalize_dedupes_ids_and_fills_defaults() -> None:
    proposal = normalize_temporary_encounter(copy.deepcopy(_WOLF_RAW))
    assert [enemy["id"] for enemy in proposal["enemies"]] == ["corrupted_wolf", "corrupted_wolf_2"]
    first = proposal["enemies"][0]
    assert first["attacks"][0]["id"] == "bite"
    assert first["attacks"][0]["range"] == 5 and first["attacks"][0]["long_range"] == 5
    second = proposal["enemies"][1]
    assert second["position"] == 30 and second["initiative_modifier"] == 0
    assert proposal["title"] == "腐化狼群"


def test_normalize_rejects_out_of_range_and_non_dice_damage() -> None:
    def enemy(**overrides):
        base = {
            "name": "wolf", "hp": 11, "armor_class": 13,
            "attacks": [{"name": "bite", "attack_bonus": 4, "damage": "1d6+2"}],
        }
        base.update(overrides)
        return base

    with pytest.raises(ValueError):
        normalize_temporary_encounter({"enemies": [enemy(armor_class=999)]})
    with pytest.raises(ValueError):
        normalize_temporary_encounter({"enemies": [enemy(hp=0)]})
    with pytest.raises(ValueError):
        normalize_temporary_encounter({
            "enemies": [enemy(attacks=[{"name": "bite", "attack_bonus": 4, "damage": "造成大量伤害"}])],
        })
    with pytest.raises(ValueError):
        normalize_temporary_encounter({"enemies": [enemy() for _ in range(13)]})
    # 底层安全边界与 combat.start 完全同一份实现。
    validate_enemy_profiles(
        normalize_temporary_encounter({"enemies": [enemy()]})["enemies"],
    )


def test_balance_rejects_legal_but_deadly_attack_values() -> None:
    runtime, instance = _runtime_instance()
    proposal = normalize_temporary_encounter({
        "title": "过强敌人", "description": "", "enemies": [{
            "name": "过强敌人", "hp": 10, "armor_class": 14,
            "attacks": [{"name": "重击", "attack_bonus": 12, "damage": "4d12+8"}],
        }],
    })
    with pytest.raises(ValueError, match="普通难度上限"):
        validate_temporary_encounter_balance(instance, proposal)


@pytest.mark.asyncio
async def test_plan_requires_tool_call_and_validates_output() -> None:
    instance = SimpleNamespace(
        players={"p1": {}}, language="en", log=[],
        modules={"narrative_notes": {**narrative_notes.fresh(), "scene": "矿井深处"}},
        record_llm_usage=lambda tokens: None,
    )
    proposal = await plan_temporary_encounter(
        instance, {}, _FakeLLM(arguments=copy.deepcopy(_WOLF_RAW)),
    )
    assert proposal["title"] == "腐化狼群"
    assert proposal["planner"]["provider"] == "test"

    with pytest.raises(ValueError):
        await plan_temporary_encounter(instance, {}, _FakeLLM(arguments=None))
    with pytest.raises(ValueError):
        await plan_temporary_encounter(
            instance, {},
            _FakeLLM(arguments={"enemies": [{"name": "x", "hp": 1, "armor_class": 999,
                                             "attacks": [{"name": "a", "attack_bonus": 1, "damage": "1d4"}]}]}),
        )
    with pytest.raises(RuntimeError):
        await plan_temporary_encounter(instance, {}, _FakeLLM(error=RuntimeError("llm timeout")))


@pytest.mark.asyncio
async def test_generation_context_reads_dnd2024_canonical_party_fields() -> None:
    runtime, instance = _runtime_instance()
    instance.log.append({
        "round": 1,
        "actions": [{"user_id": "gm", "text": "检查矿井深处的抓挠声"}],
        "gm_response": "黑暗里传来低沉的喘息。",
    })
    llm = _FakeLLM(arguments=copy.deepcopy(_WOLF_RAW))

    await plan_temporary_encounter(instance, {}, llm)

    assert len(llm.calls) == 1
    context = json.loads(llm.calls[0][0][1])
    members = context["party"]["members"]
    by_name = {member["name"]: member for member in members}
    canonical = instance.players["gm"]["character_sheet"]["ruleset_character"]
    first_class = canonical["build"]["class_levels"][0]
    assert by_name[canonical["identity"]["name"]] == {
        "name": canonical["identity"]["name"],
        "class": first_class["class_ref"],
        "level": canonical["build"]["level"],
        "hp": canonical["resources"]["hp"],
        "max_hp": canonical["resources"]["max_hp"],
        "armor_class": canonical["derived"]["armor_class"],
    }
    assert context["recent_dialogue"][-1]["actions"][0]["text"] == "检查矿井深处的抓挠声"


# ---- service：权限 / 覆盖拒绝 / 无副作用 / 失败 ----


@pytest.mark.asyncio
async def test_gm_can_plan_and_generation_has_no_side_effects() -> None:
    runtime, instance = _runtime_instance(adventure=True)
    table_settings.replace_solo_mode(instance, True)
    _submit(runtime, instance, "session_zero.quick_start")
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    registry = _SaveRegistry()
    registry.items[tuple(instance.game_key)] = instance
    binding_before = copy.deepcopy(instance.adventure_binding)
    # 预热读取路径：campaign 状态在首次 gameplay_view 时惰性物化，与生成无关。
    runtime.gameplay_view(instance, "gm", True)
    fingerprint_before = _state_fingerprint(instance)

    deps = _dependencies(runtime, registry, instance, _FakeLLM(arguments=copy.deepcopy(_WOLF_RAW)))
    result = await ruleset_gameplay.plan_temporary_encounter(
        deps, "test|temp-encounter|bot", "gm", True,
    )

    assert result["ok"] is True
    encounter = result["encounter"]
    assert encounter["title"] == "腐化狼群"
    assert len(encounter["enemies"]) == 2
    # 只读：combat、state version、event ledger、round 全部不变。
    assert _state_fingerprint(instance) == fingerprint_before
    # Adventure 绑定保持，冒险包零修改。
    assert instance.adventure_binding == binding_before


@pytest.mark.asyncio
async def test_player_cannot_plan_temporary_encounter() -> None:
    runtime, instance = _runtime_instance()
    registry = _SaveRegistry()
    registry.items[tuple(instance.game_key)] = instance
    deps = _dependencies(runtime, registry, instance, _FakeLLM(arguments=copy.deepcopy(_WOLF_RAW)))

    result = await ruleset_gameplay.plan_temporary_encounter(
        deps, "test|temp-encounter|bot", "ally", False,
    )

    assert result["ok"] is False
    assert result["code"] == "GM_ONLY"


@pytest.mark.asyncio
async def test_free_play_can_plan_without_pending_encounter() -> None:
    runtime, instance = _runtime_instance()
    registry = _SaveRegistry()
    registry.items[tuple(instance.game_key)] = instance
    llm = _FakeLLM(arguments=copy.deepcopy(_WOLF_RAW))
    deps = _dependencies(runtime, registry, instance, llm)

    result = await ruleset_gameplay.plan_temporary_encounter(
        deps, "test|temp-encounter|bot", "gm", True,
    )

    assert result["ok"] is True
    assert result["encounter"]["title"] == "腐化狼群"
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_existing_legal_preset_rejects_without_calling_llm() -> None:
    runtime, instance = _runtime_instance()
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    preset_id = runtime._combat_engine(instance).encounter_presets()[0]["id"]
    ruleset_runtime.state(instance)["encounter_request"]["encounter_preset_id"] = preset_id
    registry = _SaveRegistry()
    registry.items[tuple(instance.game_key)] = instance
    llm = _FakeLLM(arguments=copy.deepcopy(_WOLF_RAW))
    deps = _dependencies(runtime, registry, instance, llm)

    result = await ruleset_gameplay.plan_temporary_encounter(
        deps, "test|temp-encounter|bot", "gm", True,
    )

    assert result["ok"] is False
    assert result["code"] == "ENCOUNTER_ALREADY_PREPARED"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_story_encounter_cannot_be_overridden() -> None:
    runtime, instance = _runtime_instance(adventure=True, walk_to_combat=True)
    ruleset_runtime.state(instance)["encounter_request"] = {
        "status": "pending", "encounter_preset_id": "first_skirmish",
    }
    registry = _SaveRegistry()
    registry.items[tuple(instance.game_key)] = instance
    deps = _dependencies(runtime, registry, instance, _FakeLLM(arguments=copy.deepcopy(_WOLF_RAW)))

    result = await ruleset_gameplay.plan_temporary_encounter(
        deps, "test|temp-encounter|bot", "gm", True,
    )

    assert result["ok"] is False
    assert result["code"] == "STORY_ENCOUNTER_BOUND"


@pytest.mark.asyncio
async def test_illegal_ai_output_and_llm_failure_are_side_effect_free() -> None:
    runtime, instance = _runtime_instance()
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    registry = _SaveRegistry()
    registry.items[tuple(instance.game_key)] = instance
    # 预热读取路径：campaign 状态在首次 gameplay_view 时惰性物化，与生成无关。
    runtime.gameplay_view(instance, "gm", True)
    fingerprint_before = _state_fingerprint(instance)
    game_key = "test|temp-encounter|bot"

    illegal = copy.deepcopy(_WOLF_RAW)
    illegal["enemies"][0]["armor_class"] = 999
    bad = await ruleset_gameplay.plan_temporary_encounter(
        _dependencies(runtime, registry, instance, _FakeLLM(arguments=illegal)),
        game_key, "gm", True,
    )
    assert bad["ok"] is False and bad["code"] == "TEMPORARY_ENCOUNTER_INVALID"

    failed = await ruleset_gameplay.plan_temporary_encounter(
        _dependencies(runtime, registry, instance, _FakeLLM(error=RuntimeError("timeout"))),
        game_key, "gm", True,
    )
    assert failed["ok"] is False and failed["code"] == "LLM_REQUEST_FAILED"

    unconfigured = await ruleset_gameplay.plan_temporary_encounter(
        _dependencies(runtime, registry, instance, None),
        game_key, "gm", True,
    )
    assert unconfigured["ok"] is False and unconfigured["code"] == "LLM_NOT_CONFIGURED"

    assert _state_fingerprint(instance) == fingerprint_before
    assert ruleset_runtime.state(instance).get("combat", {}).get("status") != "active"


# ---- GM 确认：现有 combat.start（mode=sandbox） ----


def test_gm_confirm_starts_existing_sandbox_combat() -> None:
    runtime, instance = _runtime_instance()
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    proposal = normalize_temporary_encounter(copy.deepcopy(_WOLF_RAW))

    _submit(runtime, instance, "combat.start", mode="sandbox", enemies=proposal["enemies"])

    view = runtime.gameplay_view(instance, "gm", True)
    assert view["combat"]["status"] == "active"
    assert {actor["name"] for actor in view["combat"]["actors"] if actor["kind"] == "enemy"} == {
        "Corrupted Wolf",
    }


def test_tampered_preview_enemies_are_rejected_by_authority() -> None:
    runtime, instance = _runtime_instance()
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    enemies = normalize_temporary_encounter(copy.deepcopy(_WOLF_RAW))["enemies"]
    enemies[0]["hp"] = 999999999
    version = int(ruleset_runtime.state(instance).get("version", 0) or 0)

    resolved = runtime.resolve_intent(instance, {
        "intent_id": "tampered", "type": "combat.start",
        "expected_version": version, "submitted_by": "gm",
        "mode": "sandbox", "enemies": enemies,
    }, random.Random(7))

    assert resolved["ok"] is False
    assert ruleset_runtime.state(instance).get("combat", {}).get("status") != "active"


def test_adventure_binding_survives_temporary_combat() -> None:
    runtime, instance = _runtime_instance(adventure=True)
    binding_before = copy.deepcopy(instance.adventure_binding)
    proposal = normalize_temporary_encounter(copy.deepcopy(_WOLF_RAW))

    _submit(runtime, instance, "combat.start", mode="sandbox", enemies=proposal["enemies"])

    view = runtime.gameplay_view(instance, "gm", True)
    assert view["combat"]["status"] == "active"
    assert instance.adventure_binding == binding_before


# ---- GM 编辑草稿：最终 payload 在 combat.start 权威入口重走校验 ----


def _expand_with_quantity(enemies: list[dict], quantity: int) -> list[dict]:
    """模拟前端数量展开：首个实例保留原 id，其余使用稳定后缀。"""

    expanded: list[dict] = []
    for enemy in enemies:
        for index in range(quantity):
            instance_enemy = copy.deepcopy(enemy)
            if index:
                instance_enemy["id"] = f"{enemy['id']}-{index + 1}"
            expanded.append(instance_enemy)
    return expanded


def _resolve_temporary_start(
    runtime: Dnd2024Runtime,
    instance: GameInstance,
    enemies: list[dict],
    *,
    submitted_by: str = "gm",
) -> dict:
    version = int(ruleset_runtime.state(instance).get("version", 0) or 0)
    return runtime.resolve_intent(instance, {
        "intent_id": f"temporary-edit-{submitted_by}-{version}",
        "type": "combat.start", "expected_version": version,
        "submitted_by": submitted_by, "mode": "sandbox",
        "temporary_encounter": True, "enemies": enemies,
    }, random.Random(7))


def test_valid_edited_payload_with_expanded_quantities_starts_combat() -> None:
    runtime, instance = _runtime_instance()
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    enemies = normalize_temporary_encounter(copy.deepcopy(_WOLF_RAW))["enemies"]
    enemies[0]["name"] = "Grey Wolf"
    enemies[0]["hp"] = 9
    enemies[0]["armor_class"] = 15
    expanded = _expand_with_quantity(enemies, 2)

    _submit(runtime, instance, "combat.start", mode="sandbox", temporary_encounter=True, enemies=expanded)

    view = runtime.gameplay_view(instance, "gm", True)
    assert view["combat"]["status"] == "active"
    enemy_actors = [actor for actor in view["combat"]["actors"] if actor["kind"] == "enemy"]
    hp_by_id = {actor["actor_id"]: actor["hp"] for actor in enemy_actors}
    assert sorted(hp_by_id) == [
        "enemy:corrupted_wolf", "enemy:corrupted_wolf-2",
        "enemy:corrupted_wolf_2", "enemy:corrupted_wolf_2-2",
    ]
    assert hp_by_id["enemy:corrupted_wolf"] == 9
    assert hp_by_id["enemy:corrupted_wolf_2"] == 11
    assert {actor["name"] for actor in enemy_actors} == {"Grey Wolf", "Corrupted Wolf"}


def test_edited_payload_over_party_limit_is_rejected() -> None:
    runtime, instance = _runtime_instance()
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    enemies = normalize_temporary_encounter(copy.deepcopy(_WOLF_RAW))["enemies"]
    # 两只狼各 3 只：展开 6 个实例，超过当前队伍的普通难度上限（4）。
    expanded = _expand_with_quantity(enemies, 3)

    resolved = _resolve_temporary_start(runtime, instance, expanded)

    assert resolved["ok"] is False
    assert "普通难度上限" in str(resolved.get("error") or "")
    assert ruleset_runtime.state(instance).get("combat", {}).get("status") != "active"


def test_edited_payload_exceeding_temporary_bounds_is_rejected() -> None:
    runtime, instance = _runtime_instance()
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    enemies = normalize_temporary_encounter(copy.deepcopy(_WOLF_RAW))["enemies"]
    # hp=600 在底层边界（≤10000）内，但超出临时遭遇允许范围（≤500）：
    # 最终 payload 必须重走生成期同一套收紧范围，不能绕过。
    enemies[0]["hp"] = 600

    resolved = _resolve_temporary_start(runtime, instance, enemies)

    assert resolved["ok"] is False
    assert "临时遭遇允许范围" in str(resolved.get("error") or "")
    assert ruleset_runtime.state(instance).get("combat", {}).get("status") != "active"


def test_edited_payload_with_illegal_damage_formula_is_rejected() -> None:
    runtime, instance = _runtime_instance()
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    enemies = normalize_temporary_encounter(copy.deepcopy(_WOLF_RAW))["enemies"]
    enemies[0]["attacks"][0]["damage"] = "造成大量伤害"

    resolved = _resolve_temporary_start(runtime, instance, enemies)

    assert resolved["ok"] is False
    assert "attack" in str(resolved.get("error") or "")
    assert ruleset_runtime.state(instance).get("combat", {}).get("status") != "active"


def test_non_gm_cannot_confirm_edited_temporary_encounter() -> None:
    runtime, instance = _runtime_instance()
    apply_ruleset_combat_signal(instance, {"combat_command": "start"}, runtime)
    enemies = normalize_temporary_encounter(copy.deepcopy(_WOLF_RAW))["enemies"]

    resolved = _resolve_temporary_start(
        runtime, instance, enemies, submitted_by="ally",
    )

    assert resolved["ok"] is False
    assert ruleset_runtime.state(instance).get("combat", {}).get("status") != "active"
