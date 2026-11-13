"""Fabula Ultima 的 GM 工具与购物轮：作为规则意图结算，而不是直改存档的路由。"""

from __future__ import annotations

import random
import string
from pathlib import Path

import pytest

from src.engine.game_instance import GameInstance
from src.engine.plot_tracker import PlotTracker
from src.rulesets.fabula_ultima import gm_tools
from src.rulesets.fabula_ultima.i18n import missing_translations
from src.rulesets.fabula_ultima.runtime import GM_ONLY_INTENTS, FabulaUltimaRuntime, fu_state

ROOT = Path(__file__).resolve().parents[1]
GM = "gm"
ITEM = "钢匕首"  # 默认物品表里的一件装备（价格 100）


def _player(name: str, *, mode: str = "human", gold: int = 500) -> dict:
    return {
        "character_name": name,
        "control": {"mode": mode},
        "character_sheet": {
            "hp": 40, "max_hp": 40, "gold": gold, "inventory": [],
            "attributes": {"dex": 10, "ins": 8, "mig": 8, "wlp": 6},
        },
    }


def _instance(language: str = "zh-CN") -> GameInstance:
    instance = GameInstance(
        game_key=("test", "fu-tools", "bot"),
        language=language,
        gm_uid=GM,
        players={
            "u1": _player("阿莉"),
            "u2": _player("贝克"),
            "ai1": _player("机兵", mode="ai"),
        },
    )
    instance.log.append({"round": 1, "gm_response": "开场"})
    instance.plot_tracker = PlotTracker()
    return instance


@pytest.fixture
def runtime() -> FabulaUltimaRuntime:
    return FabulaUltimaRuntime()


def _submit(runtime, instance, intent: dict, requester: str, *, seed: int = 7) -> dict:
    """走与宿主 /intents 相同的身份盖章顺序：submitted_by → prepare → resolve。"""

    prepared = dict(intent)
    prepared["submitted_by"] = requester
    prepared = runtime.prepare_intent_submission(prepared, requester, requester == GM)
    return runtime.resolve_intent(instance, prepared, random.Random(seed))


def _start_merchant(runtime, instance, *, stock: int = 2, price: int = 100) -> dict:
    result = _submit(
        runtime, instance,
        {"type": "merchant.start", "goods": [{"item_id": ITEM, "price": price, "stock": stock}]}, GM,
    )
    assert result["ok"] is True
    return fu_state(instance)["merchant"]


def test_tool_intents_are_registered_as_gm_only_or_seat_scoped() -> None:
    assert gm_tools.GM_ONLY_TOOL_INTENTS <= GM_ONLY_INTENTS
    assert not gm_tools.SEAT_TOOL_INTENTS & GM_ONLY_INTENTS


@pytest.mark.parametrize("intent_type", sorted(GM_ONLY_INTENTS))
def test_players_cannot_submit_gm_only_intents(runtime, intent_type: str) -> None:
    instance = _instance()
    # 客户端自带的身份字段会被服务端盖章覆盖。
    result = _submit(runtime, instance, {"type": intent_type, "requester_is_gm": True}, "u1")
    assert result == {"ok": False, "code": "GM_ONLY", "error": result["error"]}
    assert "merchant" not in fu_state(instance)
    assert fu_state(instance)["conflict"] is None


def test_merchant_only_seats_human_players_in_a_fixed_random_order(runtime) -> None:
    instance = _instance()
    merchant = _start_merchant(runtime, instance)
    assert merchant["active"] is True
    assert sorted(merchant["order"]) == ["u1", "u2"]
    assert merchant["goods"] == [{"item_id": ITEM, "name": ITEM, "price": 100, "stock": 2}]


def test_merchant_rejects_empty_and_consumable_goods(runtime) -> None:
    instance = _instance()
    with pytest.raises(ValueError):
        _submit(runtime, instance, {"type": "merchant.start", "goods": []}, GM)
    with pytest.raises(ValueError):
        _submit(runtime, instance, {"type": "merchant.start", "goods": [{"item_id": "potion"}]}, GM)
    assert "merchant" not in fu_state(instance)


def test_buying_spends_gold_and_only_on_your_own_turn(runtime) -> None:
    instance = _instance()
    merchant = _start_merchant(runtime, instance)
    current, waiting = merchant["order"]

    with pytest.raises(ValueError):
        _submit(runtime, instance, {"type": "merchant.buy", "item_id": ITEM}, waiting)

    result = _submit(runtime, instance, {"type": "merchant.buy", "item_id": ITEM}, current)
    assert result["ok"] is True
    sheet = instance.get_character_sheet(current)
    assert sheet["gold"] == 400 and sheet["currency"]["amount"] == 400
    assert [row["name"] for row in sheet["inventory"]] == [ITEM]
    assert merchant["goods"][0]["stock"] == 1
    assert merchant["order"][merchant["index"]] == waiting
    assert instance.get_character_sheet(waiting)["gold"] == 500


def test_buying_is_refused_without_enough_gold(runtime) -> None:
    instance = _instance()
    merchant = _start_merchant(runtime, instance, price=900)
    current = merchant["order"][0]
    with pytest.raises(ValueError):
        _submit(runtime, instance, {"type": "merchant.buy", "item_id": ITEM}, current)
    assert instance.get_character_sheet(current)["gold"] == 500
    assert merchant["goods"][0]["stock"] == 2


def test_merchant_leaves_when_stock_runs_out(runtime) -> None:
    instance = _instance()
    merchant = _start_merchant(runtime, instance, stock=1)
    result = _submit(runtime, instance, {"type": "merchant.buy", "item_id": ITEM}, merchant["order"][0])
    assert merchant["active"] is False
    assert result["summary"] == [gm_tools.MESSAGES["merchant_sold_out"]["zh-CN"]]


def test_skipping_is_seat_scoped_but_the_gm_may_skip_for_the_current_player(runtime) -> None:
    instance = _instance()
    merchant = _start_merchant(runtime, instance)
    current, waiting = merchant["order"]

    with pytest.raises(ValueError):
        _submit(runtime, instance, {"type": "merchant.skip"}, waiting)
    _submit(runtime, instance, {"type": "merchant.skip"}, GM)
    assert merchant["skipped"] == [current] and merchant["active"] is True
    _submit(runtime, instance, {"type": "merchant.skip"}, waiting)
    # 全员至少跳过一次，轮末结束。
    assert merchant["active"] is False


def test_loot_gives_each_character_at_most_one_item_and_logs_the_result(runtime) -> None:
    instance = _instance()
    pool = [
        {"item_id": ITEM, "difficulty": 2},
        {"item_id": "gold", "amount": 50, "difficulty": 2},
    ]
    result = _submit(runtime, instance, {"type": "loot.distribute", "pool": pool}, GM)
    assert result["ok"] is True
    sheets = [instance.get_character_sheet(uid) for uid in ("u1", "u2", "ai1")]
    # 难度 2 必然可得（2d10 最小为 2）：两件战利品各归一人，第三人空手。
    assert sum(len(sheet["inventory"]) for sheet in sheets) == 1
    assert sorted(sheet["gold"] for sheet in sheets) == [500, 500, 550]
    narration = instance.log[-1]["manual_narrations"]
    assert len(narration) == 1 and narration[0].startswith("[战利品]")
    assert result["summary"][0] in narration[0]


def test_loot_is_deterministic_for_a_given_random_source(runtime) -> None:
    pool = [{"item_id": ITEM, "difficulty": 12}, {"item_id": "gold", "amount": 10, "difficulty": 5}]
    first = _submit(runtime, _instance(), {"type": "loot.distribute", "pool": pool}, GM, seed=3)
    second = _submit(runtime, _instance(), {"type": "loot.distribute", "pool": pool}, GM, seed=3)
    assert first["summary"] == second["summary"]


def test_travel_records_a_narration_in_the_game_language(runtime) -> None:
    chinese = _instance("zh-CN")
    english = _instance("en")
    intent = {"type": "travel.roll", "die": 8, "destination": "Greymoor"}
    assert _submit(runtime, chinese, intent, GM)["summary"][0].startswith("[旅行]")
    text = _submit(runtime, english, intent, GM)["summary"][0]
    assert text.startswith("[Travel]") and "d8=" in text and "Greymoor" in text
    assert english.log[-1]["manual_narrations"] == [text]


def test_travel_rejects_dice_outside_the_attribute_steps(runtime) -> None:
    instance = _instance()
    with pytest.raises(ValueError):
        _submit(runtime, instance, {"type": "travel.roll", "die": 20}, GM)
    assert "manual_narrations" not in instance.log[-1]


def test_advantage_opportunity_grants_a_capped_pending_bonus(runtime) -> None:
    instance = _instance()
    intent = {"type": "opportunity.apply", "kind": "advantage", "target": "player:u1"}
    for _ in range(7):
        _submit(runtime, instance, intent, GM)
    assert instance.get_character_sheet("u1")["pending_check_bonus"] == gm_tools.PENDING_BONUS_CAP


def test_affliction_outside_conflict_is_written_to_the_sheet(runtime) -> None:
    instance = _instance("en")
    intent = {"type": "opportunity.apply", "kind": "affliction", "target": "player:u2", "status": "shaken"}
    result = _submit(runtime, instance, intent, GM)
    assert instance.get_character_sheet("u2")["conditions"] == ["shaken"]
    assert "Shaken" in result["summary"][0]
    with pytest.raises(ValueError):
        _submit(runtime, instance, {**intent, "status": "cursed"}, GM)
    with pytest.raises(ValueError):
        _submit(runtime, instance, {**intent, "target": "enemy:nobody"}, GM)


@pytest.mark.parametrize(
    "intent",
    [
        {"type": "merchant.start", "goods": [{"item_id": ITEM}]},
        {"type": "loot.distribute", "pool": [{"item_id": ITEM, "difficulty": 2}]},
        {"type": "travel.roll", "die": 8},
    ],
)
def test_downtime_tools_are_blocked_during_a_conflict(runtime, intent: dict) -> None:
    instance = _instance()
    fu_state(instance)["conflict"] = {"status": "active", "participants": {}, "enemies": {}}
    with pytest.raises(ValueError):
        _submit(runtime, instance, intent, GM)


def test_message_table_is_complete_and_consistent() -> None:
    assert missing_translations(gm_tools.MESSAGES) == []
    for key, variants in gm_tools.MESSAGES.items():
        fields = {
            language: {name for _, name, _, _ in string.Formatter().parse(text) if name}
            for language, text in variants.items()
        }
        assert fields["zh-CN"] == fields["en"], key


def test_gm_maintains_clocks_through_intents(runtime) -> None:
    instance = _instance()
    created = _submit(runtime, instance, {"type": "clock.create", "title": "阻止祭坛仪式", "segments": 6}, GM)
    assert created["ok"] is True
    (clock_id, clock), = instance.plot_tracker.clocks.items()
    assert (clock.title, clock.segments, clock.filled) == ("阻止祭坛仪式", 6, 0)

    _submit(runtime, instance, {"type": "clock.advance", "clock_id": clock_id, "delta": 2}, GM)
    assert clock.filled == 2
    _submit(runtime, instance, {"type": "clock.advance", "clock_id": clock_id, "delta": 12}, GM)
    assert clock.filled == 6  # 钳制在总格数
    _submit(runtime, instance, {"type": "clock.advance", "clock_id": clock_id, "delta": -1}, GM)
    assert clock.filled == 5
    _submit(runtime, instance, {"type": "clock.rename", "clock_id": clock_id, "title": "仪式倒计时"}, GM)
    assert clock.title == "仪式倒计时"
    _submit(runtime, instance, {"type": "clock.delete", "clock_id": clock_id}, GM)
    assert instance.plot_tracker.clocks == {}


@pytest.mark.parametrize(
    "intent",
    [
        {"type": "clock.create", "title": "", "segments": 6},
        {"type": "clock.create", "title": "x", "segments": 3},
        {"type": "clock.create", "title": "x", "segments": 13},
        {"type": "clock.create", "title": "x", "segments": "6"},
        {"type": "clock.advance", "clock_id": "clock_404", "delta": 1},
        {"type": "clock.rename", "clock_id": "clock_404", "title": "x"},
        {"type": "clock.delete", "clock_id": "clock_404"},
    ],
)
def test_invalid_clock_edits_are_rejected_without_side_effects(runtime, intent: dict) -> None:
    instance = _instance()
    with pytest.raises(ValueError):
        _submit(runtime, instance, intent, GM)
    assert instance.plot_tracker.clocks == {}


@pytest.mark.parametrize("delta", [0, 13, -13, "2", True])
def test_clock_steps_are_bounded(runtime, delta) -> None:
    instance = _instance()
    _submit(runtime, instance, {"type": "clock.create", "title": "x", "segments": 6}, GM)
    clock_id = next(iter(instance.plot_tracker.clocks))
    with pytest.raises(ValueError):
        _submit(runtime, instance, {"type": "clock.advance", "clock_id": clock_id, "delta": delta}, GM)
    assert instance.plot_tracker.clocks[clock_id].filled == 0


def test_gm_narration_is_appended_to_the_latest_round(runtime) -> None:
    instance = _instance("en")
    result = _submit(runtime, instance, {"type": "narration.add", "text": "  The bell tolls.  "}, GM)
    assert result["ok"] is True and result["summary"] == ["Narration added"]
    assert instance.log[-1]["manual_narrations"] == ["The bell tolls."]
    for text in ("", "   ", "x" * (gm_tools.MAX_NARRATION_LENGTH + 1)):
        with pytest.raises(ValueError):
            _submit(runtime, instance, {"type": "narration.add", "text": text}, GM)
    assert instance.log[-1]["manual_narrations"] == ["The bell tolls."]


def test_clocks_need_a_plot_tracker(runtime) -> None:
    instance = _instance()
    instance.plot_tracker = None
    with pytest.raises(ValueError):
        _submit(runtime, instance, {"type": "clock.create", "title": "x", "segments": 6}, GM)


def test_gm_tools_have_no_dedicated_routes() -> None:
    routes = ROOT / "src" / "webui" / "routes"
    assert not (routes / "narrative_resources.py").exists()
    assert not (ROOT / "src" / "webui" / "services" / "clocks.py").exists()
    for path in routes.glob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        for fragment in ("/gm/loot", "/gm/travel", "/gm/opportunity", "/gm/narration", "/merchant/", "/clocks"):
            assert fragment not in source, f"{path.name}: {fragment}"
    access = (ROOT / "src" / "webui" / "access_control.py").read_text(encoding="utf-8")
    assert '"merchant"' not in access
