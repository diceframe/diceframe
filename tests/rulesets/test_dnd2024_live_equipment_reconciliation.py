"""PR-B：D&D2024 live equipment -> canonical mechanics 回归测试。

全部跑在**真实 SRD bundle** 上，覆盖用户真实行为路径：

    买 -> 穿 -> 进战斗 -> 卸

而不是只测 helper。核心断言：AC 只有一套公式、战斗读到的是更新后的
canonical AC、可用武器跟随当前装备、非法状态不被放大、本地化名称不参与
规则 authority。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.commands.state_update_applier import StateUpdateApplier
from src.engine.game_instance import GameInstance, GameState
from src.rulesets.contracts import CharacterStateReconciliationRuntime
from src.rulesets.dnd2024.combat.engine import Dnd2024CombatEngine
from src.rulesets.dnd2024.character.derivation import derive_armor_class
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.runtime import Dnd2024Runtime


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _draft(runtime: Dnd2024Runtime, class_ref: str = "class:fighter") -> dict:
    """A legal level-1 draft built from the real SRD bundle."""

    locale = "en"
    choices = runtime.builder_choices(None, {
        "locale": locale,
        "class_ref": class_ref,
        "background_ref": "background:soldier",
        "species_ref": "species:human",
    })
    class_entity = runtime.load_bundle(locale).get("class", class_ref.split(":", 1)[1])
    draft = {
        "locale": locale,
        "name": "Reconcile Hero",
        "level": 1,
        "alignment": "neutral_good",
        "ability_method": "standard_array",
        "base_abilities": class_entity["recommended_standard_array"],
        "background_ability_bonuses": {"str": 2, "con": 1},
        "class_ref": class_ref,
        "species_ref": "species:human",
        "background_ref": "background:soldier",
        "class_skill_refs": [
            item["ref"] for item in choices["class_skills"][:choices["class_skill_count"]]
        ],
        "equipment_package_ref": choices["equipment_packages"][0]["ref"],
        "background_equipment_package_ref": choices["background_equipment_packages"][0]["ref"],
        "language_refs": ["language:common", "language:dwarvish", "language:elvish"],
    }
    if "fighting_style" in (class_entity.get("level_1_feature_ids") or []):
        draft["class_feature_feat_choices"] = {"fighting_style": "feat:defense"}
    if class_entity.get("recommended_spell_choices"):
        draft["class_spell_choices"] = class_entity["recommended_spell_choices"]
    choices = runtime.builder_choices(None, draft)
    if choices["species_sizes"]:
        draft["species_size"] = "medium"
    species_choices = {}
    for choice in choices["species_choices"]:
        options = choice.get("option_ids") or choice.get("option_refs") or []
        species_choices[choice["id"]] = options[0]
    if species_choices:
        draft["species_choice_answers"] = species_choices
    if choices["species_skill_count"]:
        draft["species_skill_refs"] = [choices["species_skills"][0]["ref"]]
    if choices["species_feat_count"]:
        draft["species_feat_refs"] = ["feat:alert"]
    if choices["class_tool_count"]:
        draft["class_tool_refs"] = [
            item["ref"] for item in choices["class_tools"][:choices["class_tool_count"]]
        ]
    return draft


class _Registry:
    def __init__(self, runtime) -> None:
        self._runtime = runtime

    def resolve(self, template):
        return self._runtime


class _Game:
    """A live game whose D&D2024 character starts unarmored."""

    def __init__(self, runtime: Dnd2024Runtime, class_ref: str = "class:fighter") -> None:
        self.runtime = runtime
        sheet = runtime.finalize_character(None, _draft(runtime, class_ref))
        self.instance = GameInstance(("web", "reconcile", "bot"))
        self.instance.state = GameState.ACTIVE_ACTION
        self.instance.round_number = 1
        self.instance.language = "en"
        self.instance.players["p1"] = {
            "character_name": "Reconcile Hero", "character_sheet": sheet,
        }
        self._applier = StateUpdateApplier(
            Path("."), None, lambda *a, **k: {}, ruleset_registry=_Registry(runtime),
        )
        self._applier._load_rule = lambda instance: SimpleNamespace(
            rule_id="dnd2024_srd", template={},
        )

    # -- actions ---------------------------------------------------------
    def apply(self, update: dict) -> None:
        self._applier.apply_state_update(self.instance, update)

    def buy(self, item_name: str) -> None:
        """Economy delivery: the item lands in the backpack, nothing is worn."""

        self.apply({"loot": [
            {"player": "p1", "item": item_name, "category": "equipment"},
        ]})

    def equip(self, item_name: str) -> None:
        self.apply({"players": {"p1": {
            "equipment_ops": [{"op": "equip", "name": item_name}],
        }}})

    def unequip(self, item_name: str) -> None:
        self.apply({"players": {"p1": {
            "equipment_ops": [{"op": "unequip", "name": item_name}],
        }}})

    def strip(self) -> None:
        """Take off everything the character was created wearing."""

        for row in list(self.sheet.get("equipment") or []):
            self.unequip(str(row.get("name") or ""))

    # -- observations ----------------------------------------------------
    @property
    def sheet(self) -> dict:
        return self.instance.get_character_sheet("p1")

    @property
    def canonical(self) -> dict:
        return self.sheet["ruleset_character"]

    @property
    def armor_class(self) -> int:
        return self.canonical["derived"]["armor_class"]

    @property
    def mirror_armor_class(self) -> int:
        return self.sheet["armor_class"]

    @property
    def item_refs(self) -> list[str]:
        return list(self.canonical["equipment"]["item_refs"])

    def combat_actor(self) -> dict:
        engine = Dnd2024CombatEngine(bundle=self.runtime.load_bundle("en"))
        return engine._player_view("p1", self.canonical)

    def combat_attacks(self) -> list[str]:
        engine = Dnd2024CombatEngine(bundle=self.runtime.load_bundle("en"))
        return [
            str(attack.get("name") or attack.get("id") or "")
            for attack in engine._available_attacks(self.combat_actor())
        ]


# --------------------------------------------------------------------------
# §44 / §45 the reported scenario
# --------------------------------------------------------------------------

def test_buying_armor_does_not_change_armor_class(runtime) -> None:
    """购买 != 装备：钱花了，AC 不该动。"""

    game = _Game(runtime)
    game.strip()
    unarmored = game.armor_class

    game.buy("Chain Mail")

    assert game.armor_class == unarmored
    assert "item:chain_mail" not in game.item_refs


def test_equipping_armor_then_shield_walks_the_reported_ac_path(runtime) -> None:
    """AC12 -> 买锁子甲 AC12 -> 穿 AC16 -> 加盾 AC18 -> 卸盾 AC16 -> 卸甲 AC12。"""

    game = _Game(runtime)
    game.strip()
    unarmored = game.armor_class

    game.buy("Chain Mail")
    assert game.armor_class == unarmored

    game.equip("Chain Mail")
    assert game.armor_class == 16

    game.buy("Shield")
    assert game.armor_class == 16

    game.equip("Shield")
    assert game.armor_class == 18

    game.unequip("Shield")
    assert game.armor_class == 16

    game.unequip("Chain Mail")
    assert game.armor_class == unarmored


# --------------------------------------------------------------------------
# §old saves: canonical preparation must happen before the generic equip slots
# --------------------------------------------------------------------------


def test_an_old_row_without_metadata_is_prepared_before_the_first_equip(runtime) -> None:
    """旧存档里没有 canonical metadata 的装备行，第一次直接装备也要落对槽位。

    升级后玩家第一次 `equip Shield` 时，generic 层拿不到任何规则信息，会把非武器
    一律当成 ``armor/body``，于是把身上的锁子甲顶回背包——之后再 reconcile 也救不
    回来。规则集的 canonical 准备必须发生在这个槽位替换之前。
    """

    game = _Game(runtime)
    game.strip()
    game.buy("Chain Mail")
    game.equip("Chain Mail")
    assert game.armor_class == 16

    # 旧存档：背包里已经有盾，但只有名字和数量，没有任何 canonical metadata。
    # 刻意不先手工 reconcile。
    game.sheet["inventory"].append({"name": "Shield", "qty": 1, "category": "equipment"})

    game.equip("Shield")

    names = [str(row.get("name") or "") for row in game.sheet["equipment"]]
    assert "Chain Mail" in names, names
    shield = next(row for row in game.sheet["equipment"] if row.get("name") == "Shield")
    assert shield["slot"] == "off_hand"
    assert shield["type"] == "shield"
    assert shield["item_ref"] == "item:shield"
    assert {"item:chain_mail", "item:shield"} <= set(game.item_refs)
    assert game.armor_class == 18
    assert game.mirror_armor_class == 18
    assert game.combat_actor()["armor_class"] == 18


def test_stale_wrong_metadata_is_repaired_before_equip(runtime) -> None:
    """历史遗留的错误 ``type`` / ``slot`` 必须能被 canonical 定义修正。

    generic 层曾经把盾牌写成 ``armor/body``；既然已经能可靠解析出
    ``item_ref=item:shield``，那么由规则定义推导出的 ``type`` / ``slot`` 属于技术
    metadata，应当以 canonical 为准，而不是"已有值就不动"。
    """

    game = _Game(runtime)
    game.strip()
    game.buy("Chain Mail")
    game.equip("Chain Mail")

    game.sheet["inventory"].append({
        "name": "Shield", "qty": 1, "category": "equipment",
        "item_ref": "item:shield", "item_key": "shield",
        "type": "armor", "slot": "body",
    })

    game.equip("Shield")

    names = [str(row.get("name") or "") for row in game.sheet["equipment"]]
    assert "Chain Mail" in names, names
    shield = next(row for row in game.sheet["equipment"] if row.get("name") == "Shield")
    assert shield["type"] == "shield"
    assert shield["slot"] == "off_hand"
    assert game.armor_class == 18
    assert game.mirror_armor_class == 18
    assert game.combat_actor()["armor_class"] == 18


def test_canonical_preparation_keeps_player_owned_metadata(runtime) -> None:
    """修正技术 metadata 时不得覆盖玩家的名称 / 效果 / 数量等非规则字段。"""

    game = _Game(runtime)
    game.strip()

    game.sheet["inventory"].append({
        "name": "Shield", "qty": 3, "category": "equipment",
        "effect": "家传的盾，边缘刻着家徽",
        "quality": "rare",
        "item_ref": "item:shield", "type": "armor", "slot": "body",
    })

    game.equip("Shield")

    shield = next(row for row in game.sheet["equipment"] if row.get("name") == "Shield")
    assert shield["slot"] == "off_hand"
    assert shield["type"] == "shield"
    # 非规则 metadata 原样保留。
    assert shield["quality"] == "rare"
    assert shield["effect"] == "家传的盾，边缘刻着家徽"
    # 装备走的是"从背包取一件"，剩余数量仍归玩家。
    remaining = [row for row in game.sheet["inventory"] if row.get("name") == "Shield"]
    assert sum(int(row.get("qty", 0) or 0) for row in remaining) == 2


def test_unarmored_armor_class_follows_the_bundle_formula(runtime) -> None:
    game = _Game(runtime)
    game.strip()

    dex = int(game.canonical["abilities"]["dex"])
    assert game.armor_class == derive_armor_class(
        {"dex": dex}, [], runtime.load_bundle("en"), armor_category_refs=[],
    )


# --------------------------------------------------------------------------
# §28 mirror  /  §49 combat view
# --------------------------------------------------------------------------

def test_legacy_mirror_tracks_canonical_armor_class(runtime) -> None:
    game = _Game(runtime)
    game.strip()
    assert game.mirror_armor_class == game.armor_class

    game.buy("Chain Mail")
    game.equip("Chain Mail")
    game.buy("Shield")
    game.equip("Shield")

    assert game.armor_class == 18
    assert game.mirror_armor_class == 18


def test_combat_actor_view_sees_the_updated_armor_class(runtime) -> None:
    """§49：不能只测角色卡，战斗面板必须也是 18。"""

    game = _Game(runtime)
    game.strip()
    game.buy("Chain Mail")
    game.equip("Chain Mail")
    game.buy("Shield")
    game.equip("Shield")

    assert game.combat_actor()["armor_class"] == 18


# --------------------------------------------------------------------------
# §26 / §50 weapons
# --------------------------------------------------------------------------

def test_weapon_must_be_equipped_before_combat_offers_it(runtime) -> None:
    game = _Game(runtime)
    game.strip()

    game.buy("Longsword")
    assert "item:longsword" not in game.item_refs
    assert "Longsword" not in game.combat_attacks()

    game.equip("Longsword")
    assert "item:longsword" in game.item_refs
    assert "Longsword" in game.combat_attacks()

    game.unequip("Longsword")
    assert "item:longsword" not in game.item_refs
    assert "Longsword" not in game.combat_attacks()


def test_unarmed_strike_survives_every_equipment_change(runtime) -> None:
    game = _Game(runtime)
    game.strip()

    def unarmed() -> bool:
        return any("Unarmed" in name for name in game.combat_attacks())

    assert unarmed()
    game.buy("Longsword")
    game.equip("Longsword")
    assert unarmed()
    game.unequip("Longsword")
    assert unarmed()


def test_focus_is_projected_as_an_equipped_ref(runtime) -> None:
    """§27：法器/圣徽同样作为 canonical equipped ref 同步。"""

    game = _Game(runtime, class_ref="class:cleric")
    game.strip()

    game.buy("Holy Symbol")
    assert "item:holy_symbol" not in game.item_refs

    game.equip("Holy Symbol")
    assert "item:holy_symbol" in game.item_refs


# --------------------------------------------------------------------------
# §51 invalid state is contained, never amplified
# --------------------------------------------------------------------------

def test_equipping_an_unowned_item_is_rejected(runtime) -> None:
    game = _Game(runtime)
    game.strip()
    before = game.armor_class
    # Chain Shirt 不在战士的职业包或背景包里，角色从未拥有过。
    assert not any(
        "Chain Shirt" == str(row.get("name") or "")
        for row in [*game.sheet["equipment"], *game.sheet["inventory"]]
    )

    game.equip("Chain Shirt")

    assert game.armor_class == before
    assert "item:chain_shirt" not in game.item_refs


def test_two_shields_do_not_stack(runtime) -> None:
    game = _Game(runtime)
    game.strip()
    game.buy("Chain Mail")
    game.equip("Chain Mail")

    # 绕过 slot 规则直接制造非法 live state（模拟脏存档）。
    game.sheet["equipment"].extend([
        {"name": "Shield", "item_ref": "item:shield", "type": "shield", "slot": "off_hand"},
        {"name": "Shield", "item_ref": "item:shield", "type": "shield", "slot": "off_hand"},
    ])
    runtime.reconcile_character_state(game.instance, "p1", frozenset({"equipment"}))

    assert game.armor_class == 18  # 16 + 2，绝不是 16 + 2 + 2


def test_two_armors_do_not_stack(runtime) -> None:
    game = _Game(runtime)
    game.strip()

    game.sheet["equipment"].extend([
        {"name": "Leather Armor", "item_ref": "item:leather_armor",
         "type": "armor", "slot": "body"},
        {"name": "Chain Mail", "item_ref": "item:chain_mail",
         "type": "armor", "slot": "body"},
    ])
    runtime.reconcile_character_state(game.instance, "p1", frozenset({"equipment"}))

    # 取一件合法的最优结果，而不是把两个 base 相加。
    assert game.armor_class == 16


def test_a_fabricated_armor_class_is_overwritten_by_the_rules(runtime) -> None:
    """§51：LLM 捏造的 AC 不被接受。"""

    game = _Game(runtime)
    game.strip()
    game.buy("Chain Mail")
    game.equip("Chain Mail")

    game.canonical["derived"]["armor_class"] = 99
    game.sheet["armor_class"] = 99
    runtime.reconcile_character_state(game.instance, "p1", frozenset({"equipment"}))

    assert game.armor_class == 16
    assert game.mirror_armor_class == 16


def test_unresolvable_item_is_warned_about_but_never_deleted(runtime) -> None:
    """§64：规则投影失败不能删除玩家的装备。"""

    game = _Game(runtime)
    game.strip()
    game.sheet["equipment"].append({"name": "Moonforged Warplate of Nowhere"})

    runtime.reconcile_character_state(game.instance, "p1", frozenset({"equipment"}))

    names = [row["name"] for row in game.sheet["equipment"]]
    assert "Moonforged Warplate of Nowhere" in names


# --------------------------------------------------------------------------
# §20 / §21 / §52 canonical identity, locale is not authority
# --------------------------------------------------------------------------

@pytest.mark.parametrize("display_name", ["Chain Mail", "锁子甲", "链甲", "チェインメイル"])
def test_localized_names_all_resolve_to_one_canonical_item(runtime, display_name) -> None:
    """§21/§52：zh-CN / en / ja 都必须落到 item:chain_mail。"""

    bundle = runtime.load_bundle("en")
    reconciler = Dnd2024CharacterStateReconciler(
        bundle,
        locale_bundles=[
            runtime.load_bundle(locale) for locale in bundle.manifest.supported_locales
        ],
    )

    assert reconciler._resolve_item_id({"name": display_name}) == "chain_mail"


def test_reconciliation_writes_canonical_identity_back_to_live_rows(runtime) -> None:
    """§42：解析成功后补齐 canonical ref，之后不再依赖本地化名称。"""

    game = _Game(runtime)
    game.strip()
    game.sheet["inventory"].append({"name": "锁子甲", "qty": 1, "category": "equipment"})

    runtime.reconcile_character_state(game.instance, "p1", frozenset({"inventory"}))

    row = next(item for item in game.sheet["inventory"] if item["name"] == "锁子甲")
    assert row["item_ref"] == "item:chain_mail"


def test_item_ref_wins_over_a_misleading_display_name(runtime) -> None:
    """§20：canonical ref 优先级高于 display name。"""

    bundle = runtime.load_bundle("en")
    reconciler = Dnd2024CharacterStateReconciler(bundle)

    resolved = reconciler._resolve_item_id(
        {"name": "Shield", "item_ref": "item:chain_mail"},
    )

    assert resolved == "chain_mail"


# --------------------------------------------------------------------------
# §19 / §37 / §38 provenance, revision, idempotence
# --------------------------------------------------------------------------

def test_reconciliation_is_idempotent(runtime) -> None:
    """§38：live state 没变时不改 canonical、不加 revision、不写日志。"""

    game = _Game(runtime)
    game.strip()
    game.buy("Chain Mail")
    game.equip("Chain Mail")

    revision = game.sheet.get("ruleset_revision")
    log_length = len(game.sheet.get("ruleset_operation_log") or [])

    assert runtime.reconcile_character_state(
        game.instance, "p1", frozenset({"equipment"}),
    ) is None
    assert game.sheet.get("ruleset_revision") == revision
    assert len(game.sheet.get("ruleset_operation_log") or []) == log_length


def test_reconciliation_bumps_revision_when_it_changes_canonical_state(runtime) -> None:
    """§37：canonical 真的变了就必须推进 revision，挡住 stale 覆盖。"""

    game = _Game(runtime)
    game.strip()
    before = int(game.sheet.get("ruleset_revision", 0) or 0)

    game.buy("Chain Mail")
    game.equip("Chain Mail")

    assert int(game.sheet["ruleset_revision"]) > before
    assert game.sheet["ruleset_operation_log"][-1]["kind"] == "character_reconcile"


def test_item_grants_stay_creation_provenance(runtime) -> None:
    """§19/§57：item_grants 只记录开卡来源，reconcile 不得改写。"""

    game = _Game(runtime)
    grants_before = [dict(g) for g in game.canonical["equipment"]["item_grants"]]

    game.strip()
    game.buy("Chain Mail")
    game.equip("Chain Mail")

    assert game.canonical["equipment"]["item_grants"] == grants_before


# --------------------------------------------------------------------------
# §22 one formula  /  contract wiring
# --------------------------------------------------------------------------

def test_creation_and_reconciliation_share_one_armor_class_formula(runtime) -> None:
    """建卡与 live reconcile 的 AC 必须由同一个纯函数得出。"""

    bundle = runtime.load_bundle("en")
    game = _Game(runtime)
    game.strip()
    game.buy("Chain Mail")
    game.equip("Chain Mail")
    game.buy("Shield")
    game.equip("Shield")

    assert game.armor_class == derive_armor_class(
        game.canonical["abilities"], game.item_refs, bundle,
        armor_category_refs=list(
            game.canonical["proficiencies"]["armor_category_refs"]
        ),
    )


def test_runtime_implements_the_reconciliation_contract(runtime) -> None:
    assert isinstance(runtime, CharacterStateReconciliationRuntime)


def test_a_sheet_without_canonical_state_is_left_alone(runtime) -> None:
    """legacy / 未建卡的角色没有 ruleset_character，应安全 no-op。"""

    instance = GameInstance(("web", "legacy", "bot"))
    instance.language = "en"
    instance.players["p1"] = {"character_name": "Legacy", "character_sheet": {
        "hp": 10, "equipment": [{"name": "Chain Mail"}], "inventory": [],
    }}

    assert runtime.reconcile_character_state(
        instance, "p1", frozenset({"equipment"}),
    ) is None
