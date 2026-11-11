"""FU 的 GM 工具与购物轮：商人、战利品、旅行、机会。

全部作为规则意图走通用的 ``/intents`` 入口，由宿主统一负责身份、加锁、
随机源、失败回滚、保存与广播；本模块只表达规则本身：

- ``merchant.start`` / ``merchant.buy`` / ``merchant.skip``：回合制购物；
- ``loot.distribute``：每名角色 2d10，取能拿到的最高难度战利品；
- ``travel.roll``：GM 选定骰面的旅行检定；
- ``opportunity.apply``：结构化的两种机会（优势 +4 / 苦难状态）；
- ``clock.create`` / ``clock.advance`` / ``clock.rename`` / ``clock.delete``：GM 手动维护命刻；
- ``narration.add``：GM 手动叙事，写进最近一轮并进入下一次 AI 上下文。

校验失败一律抛 ``ValueError``，宿主据此回滚整个意图。所有文字按对局语言渲染。
"""

from __future__ import annotations

from typing import Any

from src.engine import plot_clocks
from src.engine.modules import progression_state
from src.rulesets.fabula_ultima.dice import FU_DIE_SIZES
from src.rulesets.fabula_ultima.i18n import language_of, render

#: 只有 GM 能发起的工具意图。
CLOCK_INTENTS = frozenset({"clock.create", "clock.advance", "clock.rename", "clock.delete"})
GM_ONLY_TOOL_INTENTS = frozenset({
    "merchant.start", "loot.distribute", "travel.roll", "opportunity.apply", "narration.add",
}) | CLOCK_INTENTS
#: 玩家席位只能替自己提交的购物意图。
SEAT_TOOL_INTENTS = frozenset({"merchant.buy", "merchant.skip"})
TOOL_INTENTS = GM_ONLY_TOOL_INTENTS | SEAT_TOOL_INTENTS

OPPORTUNITY_BONUS = 4
PENDING_BONUS_CAP = 20
AFFLICTIONS = frozenset({"dazed", "shaken", "slow", "weak"})
MAX_MERCHANT_GOODS = 40
MAX_NARRATION_LENGTH = 4000

MESSAGES: dict[str, dict[str, str]] = {
    "conflict_active": {
        "zh-CN": "冲突进行中，不能使用该工具",
        "en": "This tool cannot be used while a conflict is in progress",
    },
    "currency_label": {"zh-CN": "泽尼特", "en": "Zenit"},
    "merchant_closed": {"zh-CN": "商人当前没有营业", "en": "The merchant is not open"},
    "merchant_goods_empty": {"zh-CN": "请至少添加一件商品", "en": "Add at least one item for sale"},
    "merchant_goods_invalid": {
        "zh-CN": "商品无效：只能上架物品表里的普通物品或装备",
        "en": "Invalid goods: only common items or equipment from the item table can be sold",
    },
    "merchant_no_humans": {
        "zh-CN": "没有可以参与购物的真人玩家",
        "en": "There are no human players to take part in shopping",
    },
    "merchant_opened": {"zh-CN": "商人已开始营业", "en": "The merchant is open for business"},
    "merchant_not_your_turn_buy": {"zh-CN": "还没轮到你购买", "en": "It is not your turn to buy"},
    "merchant_not_your_turn": {"zh-CN": "还没轮到你", "en": "It is not your turn"},
    "merchant_item_gone": {
        "zh-CN": "这件商品已经售罄或不存在",
        "en": "That item is sold out or does not exist",
    },
    "merchant_gold_short": {"zh-CN": "金币不足", "en": "Not enough gold"},
    "merchant_bought": {"zh-CN": "购买完成，等待下一位", "en": "Purchase complete; waiting for the next player"},
    "merchant_sold_out": {"zh-CN": "商品售罄，商人离开", "en": "Everything is sold; the merchant leaves"},
    "merchant_skipped": {"zh-CN": "已跳过", "en": "Skipped"},
    "merchant_all_skipped": {
        "zh-CN": "所有玩家都已跳过，商人离开",
        "en": "Every player has passed; the merchant leaves",
    },
    "loot_empty": {"zh-CN": "请至少添加一件战利品", "en": "Add at least one piece of loot"},
    "loot_invalid": {
        "zh-CN": "战利品无效：只能使用物品表里的条目或金币",
        "en": "Invalid loot: only entries from the item table or gold can be used",
    },
    "loot_gold": {"zh-CN": "金币 ×{amount}", "en": "Gold ×{amount}"},
    "loot_row": {"zh-CN": "{name} 2d10={total} → {item}", "en": "{name} 2d10={total} → {item}"},
    "loot_nothing": {"zh-CN": "空手", "en": "nothing"},
    "loot_row_separator": {"zh-CN": "；", "en": "; "},
    "loot_narration": {"zh-CN": "[战利品] {summary}", "en": "[Loot] {summary}"},
    "travel_die_invalid": {
        "zh-CN": "旅行骰面必须是 d6 / d8 / d10 / d12",
        "en": "The travel die must be d6 / d8 / d10 / d12",
    },
    "travel_no_round": {
        "zh-CN": "冒险还没有开始，无法记录旅行结果",
        "en": "The adventure has not started yet, so the travel result cannot be recorded",
    },
    "travel_unknown_destination": {"zh-CN": "未知地点", "en": "an unknown place"},
    "travel_discovery": {"zh-CN": "意外发现", "en": "an unexpected discovery"},
    "travel_safe": {"zh-CN": "平安无事", "en": "a safe journey"},
    "travel_danger": {"zh-CN": "遭遇危险", "en": "danger on the road"},
    "travel_narration": {
        "zh-CN": "[旅行] 前往「{destination}」，d{die}={roll}，{outcome}。",
        "en": "[Travel] Heading to \"{destination}\": d{die}={roll}, {outcome}.",
    },
    "opportunity_invalid": {"zh-CN": "机会参数无效", "en": "Invalid opportunity parameters"},
    "opportunity_bad_status": {
        "zh-CN": "苦难只能是眩晕 / 动摇 / 迟缓 / 虚弱",
        "en": "An affliction must be Dazed / Shaken / Slow / Weak",
    },
    "opportunity_target_missing": {"zh-CN": "目标不存在", "en": "The target does not exist"},
    "opportunity_target_not_in_conflict": {
        "zh-CN": "目标不在这场冲突中",
        "en": "The target is not part of this conflict",
    },
    "opportunity_target_invalid": {"zh-CN": "机会的目标无效", "en": "Invalid target for an opportunity"},
    "opportunity_advantage": {
        "zh-CN": "{name} 的下一次检定 +{bonus}",
        "en": "{name} gets +{bonus} on their next check",
    },
    "opportunity_affliction": {"zh-CN": "{name} 陷入 {status}", "en": "{name} becomes {status}"},
    "opportunity_bonus_reason": {"zh-CN": "机会-优势", "en": "Opportunity: Advantage"},
    "clock_tracker_missing": {
        "zh-CN": "这局游戏没有剧情追踪器，无法使用命刻",
        "en": "This game has no plot tracker, so clocks are unavailable",
    },
    "clock_missing": {"zh-CN": "命刻不存在", "en": "That clock does not exist"},
    "clock_delta_invalid": {"zh-CN": "命刻推进幅度无效", "en": "Invalid clock step"},
    "clock_created": {"zh-CN": "已新增命刻「{title}」（{segments} 格）", "en": "Clock \"{title}\" added ({segments} sections)"},
    "clock_updated": {"zh-CN": "命刻「{title}」：{filled}/{segments}", "en": "Clock \"{title}\": {filled}/{segments}"},
    "clock_renamed": {"zh-CN": "命刻已改名为「{title}」", "en": "Clock renamed to \"{title}\""},
    "clock_deleted": {"zh-CN": "命刻已删除", "en": "Clock deleted"},
    "narration_empty": {"zh-CN": "叙事内容不能为空", "en": "The narration cannot be empty"},
    "narration_too_long": {
        "zh-CN": "叙事内容过长（最多 {limit} 字）",
        "en": "The narration is too long ({limit} characters at most)",
    },
    "narration_no_round": {
        "zh-CN": "冒险还没有开始，无法插入叙事",
        "en": "The adventure has not started yet, so narration cannot be added",
    },
    "narration_added": {"zh-CN": "已加入手动叙事", "en": "Narration added"},
    "status_dazed": {"zh-CN": "眩晕", "en": "Dazed"},
    "status_shaken": {"zh-CN": "动摇", "en": "Shaken"},
    "status_slow": {"zh-CN": "迟缓", "en": "Slow"},
    "status_weak": {"zh-CN": "虚弱", "en": "Weak"},
}


def _text(instance: Any, key: str, /, **params: Any) -> str:
    return render(language_of(instance), MESSAGES, key, **params)


def _conflict_active(state: dict[str, Any]) -> bool:
    conflict = state.get("conflict")
    return isinstance(conflict, dict) and conflict.get("status") == "active"


def _require_no_conflict(instance: Any, state: dict[str, Any]) -> None:
    if _conflict_active(state):
        raise ValueError(_text(instance, "conflict_active"))


def _catalog(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id") or row.get("name")): row for row in items}


def _character_name(instance: Any, uid: str) -> str:
    return str((instance.players.get(uid) or {}).get("character_name") or uid)


def _set_gold(instance: Any, sheet: dict[str, Any], amount: int) -> None:
    sheet["gold"] = int(amount)
    currency = sheet.setdefault(
        "currency", {"amount": 0, "base_unit": "unit", "label": _text(instance, "currency_label")},
    )
    currency["amount"] = int(amount)


def _append_narration(instance: Any, text: str) -> None:
    """写进最近一轮的 GM 手动叙事：时间线独立显示，并进入下一次 AI 上下文。"""

    if not instance.log:
        return
    entry = instance.log[-1]
    manual = entry.get("manual_narrations")
    if not isinstance(manual, list):
        manual = []
        entry["manual_narrations"] = manual
    manual.append(text)


# ---------------------------------------------------------------------- 商人

def _merchant(instance: Any, state: dict[str, Any]) -> dict[str, Any]:
    merchant = state.get("merchant")
    if not isinstance(merchant, dict) or not merchant.get("active"):
        raise ValueError(_text(instance, "merchant_closed"))
    return merchant


def _merchant_current(merchant: dict[str, Any]) -> str:
    order = [str(uid) for uid in (merchant.get("order") or [])]
    index = int(merchant.get("index") or 0)
    return order[index] if 0 <= index < len(order) else ""


def _merchant_advance(merchant: dict[str, Any]) -> None:
    order = [str(uid) for uid in (merchant.get("order") or [])]
    goods = [row for row in (merchant.get("goods") or []) if int(row.get("stock") or 0) > 0]
    merchant["goods"] = goods
    if not goods or not order:
        merchant["active"] = False
        return
    merchant["index"] = int(merchant.get("index") or 0) + 1
    if merchant["index"] < len(order):
        return
    merchant["index"] = 0
    merchant["round"] = int(merchant.get("round") or 1) + 1
    skipped = {str(uid) for uid in (merchant.get("skipped") or [])}
    if all(uid in skipped for uid in order):
        merchant["active"] = False


def merchant_start(
    instance: Any, state: dict[str, Any], intent: dict[str, Any], rng: Any, items: list[dict[str, Any]],
) -> list[str]:
    _require_no_conflict(instance, state)
    raw_goods = intent.get("goods")
    if not isinstance(raw_goods, list) or not raw_goods:
        raise ValueError(_text(instance, "merchant_goods_empty"))
    catalog = _catalog(items)
    goods: list[dict[str, Any]] = []
    for raw in raw_goods[:MAX_MERCHANT_GOODS]:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("item_id") or "")
        item = catalog.get(item_id)
        if not item or item.get("category") == "consumable":
            continue
        goods.append({
            "item_id": item_id,
            "name": str(item.get("name") or item_id),
            "price": max(0, int(raw.get("price") or item.get("price") or 0)),
            "stock": max(1, int(raw.get("stock") or 1)),
        })
    if not goods:
        raise ValueError(_text(instance, "merchant_goods_invalid"))
    # 只有真人玩家参与购物（含倒下/离队）；顺序随机一次后固定。
    humans = [
        str(uid) for uid in instance.players
        if str(((instance.players[uid].get("control") or {}).get("mode") or "")) == "human"
    ]
    if not humans:
        raise ValueError(_text(instance, "merchant_no_humans"))
    rng.shuffle(humans)
    state["merchant"] = {
        "active": True, "goods": goods, "order": humans, "index": 0, "round": 1,
        "skipped": [], "history": [],
    }
    return [_text(instance, "merchant_opened")]


def merchant_buy(
    instance: Any, state: dict[str, Any], intent: dict[str, Any], items: list[dict[str, Any]],
) -> list[str]:
    merchant = _merchant(instance, state)
    uid = str(intent.get("submitted_by") or "")
    if not uid or uid != _merchant_current(merchant):
        raise ValueError(_text(instance, "merchant_not_your_turn_buy"))
    item_id = str(intent.get("item_id") or "")
    good = next(
        (row for row in (merchant.get("goods") or [])
         if row.get("item_id") == item_id and int(row.get("stock") or 0) > 0),
        None,
    )
    item = _catalog(items).get(item_id)
    if good is None or item is None:
        raise ValueError(_text(instance, "merchant_item_gone"))
    sheet = instance.get_character_sheet(uid)
    price = int(good.get("price") or 0)
    if int(sheet.get("gold") or 0) < price:
        raise ValueError(_text(instance, "merchant_gold_short"))
    _set_gold(instance, sheet, int(sheet.get("gold") or 0) - price)
    sheet.setdefault("inventory", []).append(dict(item))
    instance.set_character_sheet(uid, sheet)
    good["stock"] = int(good.get("stock") or 0) - 1
    merchant.setdefault("history", []).append({
        "user_id": uid, "name": _character_name(instance, uid), "action": "buy",
        "item_id": item_id, "item_name": good.get("name"), "price": price,
    })
    _merchant_advance(merchant)
    return [_text(instance, "merchant_bought" if merchant.get("active") else "merchant_sold_out")]


def merchant_skip(instance: Any, state: dict[str, Any], intent: dict[str, Any]) -> list[str]:
    merchant = _merchant(instance, state)
    current = _merchant_current(merchant)
    if not current:
        raise ValueError(_text(instance, "merchant_closed"))
    # GM 可以替当前玩家跳过；玩家只能跳过自己的回合。
    if not intent.get("requester_is_gm") and str(intent.get("submitted_by") or "") != current:
        raise ValueError(_text(instance, "merchant_not_your_turn"))
    skipped = merchant.setdefault("skipped", [])
    if current not in skipped:
        skipped.append(current)
    merchant.setdefault("history", []).append({
        "user_id": current, "name": _character_name(instance, current), "action": "skip",
    })
    _merchant_advance(merchant)
    return [_text(instance, "merchant_skipped" if merchant.get("active") else "merchant_all_skipped")]


# ---------------------------------------------------------------------- 战利品

def loot_distribute(
    instance: Any, state: dict[str, Any], intent: dict[str, Any], rng: Any, items: list[dict[str, Any]],
) -> list[str]:
    _require_no_conflict(instance, state)
    raw_pool = intent.get("pool")
    if not isinstance(raw_pool, list) or not raw_pool:
        raise ValueError(_text(instance, "loot_empty"))
    catalog = _catalog(items)
    pool: list[dict[str, Any]] = []
    for raw in raw_pool:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("item_id") or "")
        difficulty = max(1, min(20, int(raw.get("difficulty") or 1)))
        if item_id == "gold":
            amount = max(1, int(raw.get("amount") or 1))
            pool.append({
                "item_id": "gold", "name": _text(instance, "loot_gold", amount=amount),
                "amount": amount, "difficulty": difficulty, "kind": "gold",
            })
            continue
        item = catalog.get(item_id)
        if not item:
            continue
        pool.append({
            "item_id": item_id, "name": str(item.get("name") or item_id), "difficulty": difficulty,
            "kind": str(item.get("category") or "common"), "item": dict(item),
        })
    if not pool:
        raise ValueError(_text(instance, "loot_invalid"))

    # 每个玩家角色（含 AI、倒下、离队）按随机顺序各掷 2d10，拿难度不超过点数和
    # 的剩余战利品里难度最高的一件；同难度随机，每人最多一件，剩余消失。
    uids = [str(uid) for uid in instance.players]
    rng.shuffle(uids)
    remaining = list(pool)
    rows: list[str] = []
    for uid in uids:
        total = rng.randint(1, 10) + rng.randint(1, 10)
        name = _character_name(instance, uid)
        eligible = [entry for entry in remaining if int(entry["difficulty"]) <= total]
        if not eligible:
            rows.append(_text(instance, "loot_row", name=name, total=total, item=_text(instance, "loot_nothing")))
            continue
        best = max(int(entry["difficulty"]) for entry in eligible)
        chosen = rng.choice([entry for entry in eligible if int(entry["difficulty"]) == best])
        remaining.remove(chosen)
        sheet = instance.get_character_sheet(uid)
        if chosen["kind"] == "gold":
            _set_gold(instance, sheet, int(sheet.get("gold") or 0) + int(chosen["amount"]))
        else:
            sheet.setdefault("inventory", []).append(dict(chosen["item"]))
        instance.set_character_sheet(uid, sheet)
        rows.append(_text(instance, "loot_row", name=name, total=total, item=chosen["name"]))
    summary = _text(instance, "loot_row_separator").join(rows)
    _append_narration(instance, _text(instance, "loot_narration", summary=summary))
    return [summary]


# ---------------------------------------------------------------------- 旅行

def travel_roll(instance: Any, state: dict[str, Any], intent: dict[str, Any], rng: Any) -> list[str]:
    _require_no_conflict(instance, state)
    try:
        die = int(intent.get("die") or 8)
    except (TypeError, ValueError):
        die = 8
    if die not in FU_DIE_SIZES:
        raise ValueError(_text(instance, "travel_die_invalid"))
    if not instance.log:
        raise ValueError(_text(instance, "travel_no_round"))
    destination = str(intent.get("destination") or "").strip()[:120]
    roll = rng.randint(1, die)
    # 危险不自动开战，发现不自动给奖励：结果只写进叙事，由 GM 接着处理。
    outcome = "discovery" if roll == 1 else "safe" if roll <= 5 else "danger"
    text = _text(
        instance, "travel_narration",
        destination=destination or _text(instance, "travel_unknown_destination"),
        die=die, roll=roll, outcome=_text(instance, f"travel_{outcome}"),
    )
    _append_narration(instance, text)
    return [text]


# ---------------------------------------------------------------------- 机会

def opportunity_apply(instance: Any, state: dict[str, Any], intent: dict[str, Any]) -> list[str]:
    kind = str(intent.get("kind") or "")
    target = str(intent.get("target") or "")
    if kind not in {"advantage", "affliction"} or not target:
        raise ValueError(_text(instance, "opportunity_invalid"))
    conflict = state.get("conflict") if _conflict_active(state) else None
    status = str(intent.get("status") or "")
    if kind == "affliction" and status not in AFFLICTIONS:
        raise ValueError(_text(instance, "opportunity_bad_status"))

    def advantage(name: str) -> list[str]:
        return [_text(instance, "opportunity_advantage", name=name, bonus=OPPORTUNITY_BONUS)]

    def affliction(name: str) -> list[str]:
        return [_text(instance, "opportunity_affliction", name=name, status=_text(instance, f"status_{status}"))]

    if target.startswith("player:"):
        uid = target.split(":", 1)[1]
        if uid not in instance.players:
            raise ValueError(_text(instance, "opportunity_target_missing"))
        name = _character_name(instance, uid)
        if kind == "advantage":
            sheet = instance.get_character_sheet(uid)
            sheet["pending_check_bonus"] = min(
                PENDING_BONUS_CAP, int(sheet.get("pending_check_bonus") or 0) + OPPORTUNITY_BONUS,
            )
            sheet["pending_check_bonus_reason"] = _text(instance, "opportunity_bonus_reason")
            instance.set_character_sheet(uid, sheet)
            return advantage(name)
        if conflict is not None:
            row = (conflict.get("participants") or {}).get(target)
            if not isinstance(row, dict):
                raise ValueError(_text(instance, "opportunity_target_not_in_conflict"))
            if status not in row.setdefault("conditions", []):
                row["conditions"].append(status)
            row.setdefault("statuses", []).append({"id": status, "remaining": None})
        else:
            # 非冲突状态的苦难写进角色卡，进入冲突时带入。
            sheet = instance.get_character_sheet(uid)
            conditions = sheet.setdefault("conditions", [])
            if status not in conditions:
                conditions.append(status)
            instance.set_character_sheet(uid, sheet)
        return affliction(name)

    if target.startswith("enemy:") and conflict is not None:
        enemy = (conflict.get("enemies") or {}).get(target)
        if not isinstance(enemy, dict) or enemy.get("defeated"):
            raise ValueError(_text(instance, "opportunity_target_missing"))
        name = str(enemy.get("name") or target)
        if kind == "advantage":
            enemy["pending_check_bonus"] = min(
                PENDING_BONUS_CAP, int(enemy.get("pending_check_bonus") or 0) + OPPORTUNITY_BONUS,
            )
            return advantage(name)
        if status not in enemy.setdefault("conditions", []):
            enemy["conditions"].append(status)
        enemy.setdefault("statuses", []).append({"id": status, "remaining": None})
        return affliction(name)

    raise ValueError(_text(instance, "opportunity_target_invalid"))


# ---------------------------------------------------------------------- 命刻

def clock_edit(instance: Any, intent: dict[str, Any]) -> list[str]:
    """GM 手动维护剧情追踪器里的命刻；编辑后下一轮直接生效。"""

    tracker = getattr(instance, "plot_tracker", None)
    if tracker is None:
        raise ValueError(_text(instance, "clock_tracker_missing"))
    intent_type = str(intent.get("type") or "")
    clock_id = str(intent.get("clock_id") or "")
    language = language_of(instance)
    try:
        round_number = int(progression_state.round_value(instance) or 0)
    except Exception:  # 尚未开局的对局没有轮次，不影响命刻本身
        round_number = 0
    try:
        if intent_type == "clock.create":
            clock = plot_clocks.create_clock(
                tracker, intent.get("title"), intent.get("segments"), round_number, language=language,
            )
            return [_text(instance, "clock_created", title=clock["title"], segments=clock["segments"])]
        if intent_type == "clock.advance":
            delta = intent.get("delta", 1)
            if isinstance(delta, bool) or not isinstance(delta, int):
                raise ValueError(_text(instance, "clock_delta_invalid"))
            clock = plot_clocks.apply_clock_delta(tracker, clock_id, delta, round_number, language=language)
            return [_text(
                instance, "clock_updated",
                title=clock["title"], filled=clock["filled"], segments=clock["segments"],
            )]
        if intent_type == "clock.rename":
            clock = plot_clocks.rename_clock(
                tracker, clock_id, intent.get("title"), round_number, language=language,
            )
            return [_text(instance, "clock_renamed", title=clock["title"])]
        plot_clocks.delete_clock(tracker, clock_id)
        return [_text(instance, "clock_deleted")]
    except KeyError:
        raise ValueError(_text(instance, "clock_missing")) from None


# ---------------------------------------------------------------------- 手动叙事

def narration_add(instance: Any, intent: dict[str, Any]) -> list[str]:
    """GM 手动叙事：不改变冲突状态，时间线独立显示，并进入下一次 AI 上下文。"""

    text = str(intent.get("text") or "").strip()
    if not text:
        raise ValueError(_text(instance, "narration_empty"))
    if len(text) > MAX_NARRATION_LENGTH:
        raise ValueError(_text(instance, "narration_too_long", limit=MAX_NARRATION_LENGTH))
    if not instance.log:
        raise ValueError(_text(instance, "narration_no_round"))
    _append_narration(instance, text)
    return [_text(instance, "narration_added")]
