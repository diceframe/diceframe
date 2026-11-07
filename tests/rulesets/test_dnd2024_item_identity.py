"""D1：content-pack 物品 → 规则集 ``item_ref`` 身份闭环回归测试。

覆盖 T1–T6：奖励桥透传身份、背包合并键、装备取件、失败关闭。
全部跑在真实 SRD bundle 上：身份是**规则身份**，不是显示名。
"""

from __future__ import annotations

import logging

import pytest

from src.commands.state_items import equip_owned_item, grant_classified_item
from src.engine.game_instance import GameInstance
from src.engine.modules import economy_state
from src.rulesets.dnd2024.character.reconciliation import (
    Dnd2024CharacterStateReconciler,
)
from src.rulesets.dnd2024.content.catalog import catalog_from_sources
from src.rulesets.dnd2024.content.contracts import (
    CatalogContractError,
    validate_item_record,
)
from src.rulesets.dnd2024.content.rewards import reward_intent_from_ref
from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.services.characters import grant_reward_to_sheet


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


@pytest.fixture(scope="module")
def bundle(runtime):
    return runtime.load_bundle("en")


_MODULE = "module:item-identity-test"


def _catalog(records: dict) -> object:
    return catalog_from_sources([(_MODULE, {"item": records})])


def _module_item(**overrides) -> dict:
    record = {
        "item_id": "dawn_blade",
        "name": "Dawn Blade",
        "category": "equipment",
        "ruleset_item_ref": "item:longsword",
    }
    record.update(overrides)
    return record


def _sheet(runtime: Dnd2024Runtime) -> dict:
    """A real level-1 sheet: identity tests need a real canonical record."""

    choices = runtime.builder_choices(None, {"locale": "en"})
    preset = choices["quick_presets"][0]
    return runtime.finalize_character(
        None, {**preset["draft"], "locale": "en", "name": "Identity Hero"},
    )


def _strip_equipment(sheet: dict) -> None:
    sheet["equipment"] = []
    sheet["inventory"] = []


# --------------------------------------------------------------------------
# T1 奖励桥 → 背包行，机械字段一律不落
# --------------------------------------------------------------------------

def test_reward_bridge_carries_identity_into_the_backpack(runtime) -> None:
    catalog = _catalog({"dawn_blade": _module_item()})
    intent = reward_intent_from_ref(
        catalog, "item:dawn_blade", default_source=_MODULE, recipient_uid="p1",
    )

    assert intent["item_ref"] == "item:longsword"
    # 身份映射不得夹带机械：奖励 intent 只说"给什么"，不说"怎么算"。
    assert not {"damage", "ac_base", "charges", "requires_attunement"} & set(intent)

    # webui 的冒险奖励入队必须原样保留身份 metadata。
    from src.webui.api import WebAPI

    instance = GameInstance(("test", "dnd2024-item", "bot"))
    queued = WebAPI._queue_adventure_reward_intents(None, instance, [intent])
    assert queued and queued[0]["kind"] == "reward"
    proposal = next(
        row for row in economy_state.state(instance)["proposals"]
        if row["id"] == queued[0]["proposal_id"]
    )
    reward = proposal["rewards"][0]
    assert reward["item_ref"] == "item:longsword"

    sheet = _sheet(runtime)
    _strip_equipment(sheet)
    grant_reward_to_sheet(sheet, reward)

    row = sheet["inventory"][0]
    assert row["name"] == "Dawn Blade"
    assert row["item_ref"] == "item:longsword"
    # 数量仍由 character/economy 权威维护，身份层不复制任何机械。
    assert row["qty"] == 1
    assert "damage" not in row and "ac_base" not in row


# --------------------------------------------------------------------------
# T2 二次 reconcile 幂等
# --------------------------------------------------------------------------

def test_reconcile_is_idempotent_for_identity_and_revision(runtime, bundle) -> None:
    sheet = _sheet(runtime)
    _strip_equipment(sheet)
    grant_classified_item(
        sheet, "Dawn Blade", "equipment", item_ref="item:longsword",
    )
    reconciler = Dnd2024CharacterStateReconciler(bundle)

    first = reconciler.reconcile(sheet, frozenset({"inventory"}))
    row = sheet["inventory"][0]
    assert first is not None
    assert row["item_ref"] == "item:longsword"
    assert row["type"] == "weapon"
    assert row["slot"] == "main_hand"
    revision = sheet["ruleset_revision"]

    second = reconciler.reconcile(sheet, frozenset({"inventory"}))

    assert second is None
    assert sheet["ruleset_revision"] == revision
    assert row["item_ref"] == "item:longsword"
    assert row["type"] == "weapon"
    assert row["slot"] == "main_hand"


# --------------------------------------------------------------------------
# T3 指向不存在的规则物品：只警告，不猜、不删
# --------------------------------------------------------------------------

def test_unknown_ruleset_ref_warns_without_guessing_or_deleting(
    runtime, bundle, caplog,
) -> None:
    sheet = _sheet(runtime)
    _strip_equipment(sheet)
    grant_classified_item(
        sheet, "Void Shard", "equipment", item_ref="item:no_such_item",
    )
    reconciler = Dnd2024CharacterStateReconciler(bundle)

    # 背包行：不改写身份、不删行、不猜一个"最像"的物品。
    reconciler.reconcile(sheet, frozenset({"inventory"}))
    row = sheet["inventory"][0]
    assert row["name"] == "Void Shard"
    assert row["qty"] == 1
    assert row["item_ref"] == "item:no_such_item"

    # 真的穿上它才进入装备投影；此时只允许告警，绝不获得规则效果。
    sheet["equipment"] = [dict(row, qty=1)]
    sheet["inventory"] = []
    with caplog.at_level(logging.WARNING):
        summary = reconciler.reconcile(sheet, frozenset({"equipment"}))

    assert sheet["equipment"][0]["item_ref"] == "item:no_such_item"
    assert not sheet["ruleset_character"]["equipment"]["item_refs"]
    assert any("unresolved" in record.getMessage() for record in caplog.records)
    assert summary is None or not summary["item_refs"]


# --------------------------------------------------------------------------
# T4 content-pack 契约失败关闭
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"item_id": "dawn_blade", "name": "Dawn Blade", "category": "equipment", "damage": "1d8"},
    {"item_id": "dawn_blade", "name": "Dawn Blade", "category": "equipment", "ruleset_item_ref": "longsword"},
    {"item_id": "dawn_blade", "name": "Dawn Blade", "category": "equipment", "ruleset_item_ref": "weapon:longsword"},
])
def test_content_pack_item_rejects_mechanics_and_bad_refs(bad) -> None:
    with pytest.raises(CatalogContractError):
        validate_item_record(bad)


def test_content_pack_item_keeps_the_optional_mapping_only_when_declared() -> None:
    plain = validate_item_record({
        "item_id": "torch", "name": "Torch", "category": "consumable",
    })
    assert "ruleset_item_ref" not in plain

    mapped = validate_item_record(_module_item())
    assert mapped["ruleset_item_ref"] == "item:longsword"


# --------------------------------------------------------------------------
# T5 同名不同身份：不合并、按身份取件、往返不丢
# --------------------------------------------------------------------------

def test_same_name_different_identity_never_merges_or_guesses() -> None:
    sheet = {"inventory": [], "equipment": []}
    grant_classified_item(sheet, "Sword", "equipment", item_ref="item:longsword")
    grant_classified_item(sheet, "Sword", "equipment", item_ref="item:rapier")
    # 同名同身份才累加。
    grant_classified_item(sheet, "Sword", "equipment", item_ref="item:longsword")

    rows = [row for row in sheet["inventory"] if row["name"] == "Sword"]
    assert len(rows) == 2
    assert {row["item_ref"] for row in rows} == {"item:longsword", "item:rapier"}
    assert {row["item_ref"]: row["qty"] for row in rows} == {
        "item:longsword": 2, "item:rapier": 1,
    }

    # 只有名字、无法唯一定位 → 拒绝，且不改变任何状态。
    before = [dict(row) for row in sheet["inventory"]]
    assert equip_owned_item(sheet, "Sword") is False
    assert sheet["inventory"] == before
    assert sheet["equipment"] == []

    # 带身份取件命中正确的那一行。
    assert equip_owned_item(sheet, "Sword", item_ref="item:rapier") is True
    equipped = sheet["equipment"][0]
    assert equipped["item_ref"] == "item:rapier"
    assert {row["item_ref"]: row["qty"] for row in sheet["inventory"]} == {
        "item:longsword": 2,
    }

    # 卸回背包时身份随行，不会被同名行吸收。
    from src.commands.state_items import return_equipment_to_inventory

    return_equipment_to_inventory(sheet, equipped)
    rapier_rows = [
        row for row in sheet["inventory"]
        if row["item_ref"] == "item:rapier"
    ]
    assert len(rapier_rows) == 1
    assert rapier_rows[0]["qty"] == 1


# --------------------------------------------------------------------------
# T6 本地化显示名不参与机械判定
# --------------------------------------------------------------------------

def test_display_name_is_never_consulted_after_identity_is_known(runtime, bundle) -> None:
    sheet = _sheet(runtime)
    _strip_equipment(sheet)
    grant_classified_item(sheet, "晨光之刃", "equipment", item_ref="item:longsword")
    reconciler = Dnd2024CharacterStateReconciler(bundle)

    reconciler.reconcile(sheet, frozenset({"inventory"}))
    row = sheet["inventory"][0]
    assert row["type"] == "weapon"
    assert row["slot"] == "main_hand"

    # 改成完全不相干的显示名：机械身份不变，说明没从名字重猜。
    row["name"] = "完全不相关的名字"
    sheet["equipment"] = [dict(row, qty=1)]
    del sheet["inventory"][0]
    reconciler.reconcile(sheet, frozenset({"equipment"}))

    equipped = sheet["equipment"][0]
    assert equipped["item_ref"] == "item:longsword"
    assert sheet["ruleset_character"]["equipment"]["item_refs"] == ["item:longsword"]
