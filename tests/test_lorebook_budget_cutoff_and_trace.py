"""PR #398 follow-up: overall lore budget, recursion cutoff, ActivationTrace reasons.

- Wave C 的两级预算：per-book budget → merge → overall lore budget，整体预算的数值
  由既有 context 预算派生，而不是在检索层新造一套 provider context-window。
- recursion 不能只有 MAX_RECURSIVE_DEPTH：大 Book 必须有 deterministic 的
  max-work / max-candidate 上限，而不是「先无限展开、最后才裁 budget」。
- ActivationTrace 的原因必须来自真实执行的判定（probability roll、group
  winner/loser、cooldown/delay、recursion parent/depth、budget omission），
  不能事后重算，也不能只有 matched / not_matched。
- 施工包 C7：旧 ``status=delayed`` 存档安全迁成「无 active state」。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.engine.modules import narrative_notes, progression_state
from src.llm.context_builder import lore_char_budget
from src.lorebook.activation import migrate_timed_state
from src.lorebook.budget import estimate_entry_chars, max_entries_within_budget
from src.lorebook.matcher import (
    MAX_ACTIVATED_ENTRIES,
    MAX_RECURSION_STEPS,
    KeywordMatcher,
)
from src.lorebook.retrieval import LoreRetriever
from src.lorebook.store import LorebookStore


def _build(entries: list[dict], *, rng=None) -> KeywordMatcher:
    matcher = KeywordMatcher(rng=rng)
    defaults = {
        "enabled": True, "tier": "background", "order": 100, "probability": 100,
        "keywords": [], "content": "", "sticky": 0, "cooldown": 0, "delay": 0,
        "triggers_recursive": [],
    }
    matcher.build([dict(defaults, **entry) for entry in entries])
    return matcher


def _store(tmp_path: Path, entries: list[dict], *, token_budget: int | None = None) -> LorebookStore:
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world("w", "World")
    store.ensure_primary_world_book("w")
    store.update_lorebook("world:w", {"recursive_scanning": True})
    if token_budget is not None:
        store.update_lorebook("world:w", {"token_budget": token_budget})
    for entry in entries:
        row = {**entry, "world_id": "w"}
        row.setdefault("name", row.get("id", "entry"))
        store.add_entry(row)
    return store


def _instance(store: LorebookStore, *, round_number: int = 0):
    return SimpleNamespace(
        world_id="w", language="zh-CN", npcs={}, players={},
        modules={"narrative_notes": narrative_notes.fresh(), "progression": {**progression_state.fresh(), "round": round_number}},
        world_state={}, lorebook_timed_state={},
        lorebook_store=store, action_actor_uids=[],
    )


def _retrieve(store: LorebookStore, text: str, **kwargs) -> tuple[list[dict], LoreRetriever]:
    retriever = LoreRetriever(KeywordMatcher(), store=store)
    hits = asyncio.run(retriever.retrieve(_instance(store), text, **kwargs))
    return hits, retriever


def _trace_by_id(retriever: LoreRetriever) -> dict[str, dict]:
    return {row["entry_id"]: row for row in retriever.last_activation_trace}


# ---- C7：旧 delayed 迁成「无 active state」 ---------------------------------


def test_legacy_delayed_state_migrates_to_no_active_state() -> None:
    """旧 delayed 记的是「还剩几轮」，新语义是「第 N 回合前不激活」，无法精确等价。

    因此安全迁成无 active state，之后由 entry.delay + authoritative tick 重新判定，
    而不是继续倒计时一个语义已经变了的计数器。
    """

    migrated = migrate_timed_state({"e": {"status": "delayed", "remaining": 3}})
    assert "e" not in migrated

    # 其它 legacy 状态不受影响。
    other = migrate_timed_state({
        "s": {"status": "active", "remaining": 2},
        "c": {"status": "cooldown", "remaining": 1},
    })
    assert other["s"]["sticky_remaining"] == 2
    assert other["c"]["cooldown_remaining"] == 1
    assert other["s"]["delay_remaining"] == 0


def test_new_shape_never_carries_a_delay_counter_forward() -> None:
    migrated = migrate_timed_state({"e": {"sticky_remaining": 0, "delay_remaining": 4}})
    assert migrated["e"]["delay_remaining"] == 4, "已经是新形状的存档按原样保留计数"


# ---- overall lore budget ---------------------------------------------------


def test_overall_budget_trims_after_per_book_merge(tmp_path: Path) -> None:
    entries = [
        {"id": f"e{i}", "keywords": ["clue"], "content": "x" * 100, "tier": "background",
         "order": 100 + i}
        for i in range(10)
    ]
    store = _store(tmp_path, entries)
    try:
        # 无整体预算：10 条全进。
        unbounded, _ = _retrieve(store, "clue")
        assert len(unbounded) == 10

        # 整体预算 350 字符：每条 100 字符，只能进 3 条。
        bounded, retriever = _retrieve(store, "clue", overall_budget=350)
        assert len(bounded) == 3
        # 裁剪按既有排序键，不是随机丢弃。
        assert [row["id"] for row in bounded] == ["e0", "e1", "e2"]

        trace = _trace_by_id(retriever)
        # 进入结果的条目报的是「靠什么进来的」，比笼统的 matched 更有用。
        assert trace["e0"]["reason_code"] == "keyword"
        assert trace["e0"]["budget"] == "included"
        assert trace["e9"]["reason_code"] == "budget", "被整体预算裁掉必须报 budget"
        assert trace["e9"]["budget"] == "omitted"
    finally:
        store.close()


def test_per_book_budget_still_applies_before_the_overall_one(tmp_path: Path) -> None:
    """per-book token_budget 先裁，整体预算再裁一次；两级都生效。"""

    entries = [
        {"id": f"e{i}", "keywords": ["clue"], "content": "x" * 40, "order": 100 + i}
        for i in range(6)
    ]
    # token_budget 以 token 计（4 字符 ≈ 1 token）：每条 10 token，预算 25 → 2 条。
    store = _store(tmp_path, entries, token_budget=25)
    try:
        hits, _ = _retrieve(store, "clue", overall_budget=10_000)
        assert [row["id"] for row in hits] == ["e0", "e1"]
    finally:
        store.close()


def test_overall_budget_is_derived_from_the_existing_context_authority() -> None:
    """整体预算必须来自 context_builder，检索层不得自己推导 context window。"""

    default_budget = lore_char_budget("")
    assert default_budget > 0
    # 世界级配置只能收紧，不能放宽。
    assert lore_char_budget("", lorebook_budget=500) == 500
    assert lore_char_budget("", lorebook_budget=default_budget * 10) == default_budget


def test_max_entries_within_budget_is_a_safe_upper_bound() -> None:
    entries = [{"content": "x" * 10}, {"content": "x" * 100}, {"content": "x" * 1000}]
    assert max_entries_within_budget(entries, None) is None
    assert max_entries_within_budget(entries, 0) is None
    # 最便宜的先排：10 + 100 = 110 ≤ 150，再加 1000 超了。
    assert max_entries_within_budget(entries, 150, estimate=estimate_entry_chars) == 2
    assert max_entries_within_budget(entries, 5, estimate=estimate_entry_chars) == 0


# ---- recursion work / candidate cutoff -------------------------------------


def _recursive_chain(length: int) -> list[dict]:
    """e0 -keyword-> e1 -content-> e2 -content-> ... 形成一条递归链。"""

    return [
        {"id": f"e{i}", "keywords": [f"k{i}"], "content": f"k{i + 1}"}
        for i in range(length)
    ]


def _deep_chain(length: int) -> list[dict]:
    """e0 -content k1-> e1 -content k2-> ... 一条 canonical/ST 递归链。"""

    return [
        {"id": f"e{i}", "keywords": [f"k{i}"], "content": f"k{i + 1}"}
        for i in range(length)
    ]


def test_max_steps_cutoff_is_deterministic() -> None:
    """``max_steps`` 只限制递归展开，且截断点可复现。"""

    matcher = _build(_deep_chain(12))
    first = [row["id"] for row in matcher.match_with_recursive(
        "k0", timed_state={}, max_steps=3,
    )]
    assert matcher.last_cutoff == "max_steps"
    second = [row["id"] for row in matcher.match_with_recursive(
        "k0", timed_state={}, max_steps=3,
    )]
    assert first == second, "截断点必须可复现"
    # depth 0 的 direct seed 不计入递归步数，因此 e0 一定在；递归从 e1 起算。
    assert first[0] == "e0"
    assert len(first) == 4, f"1 direct seed + 3 recursive steps, got {first}"


def test_direct_seeds_are_never_truncated_by_the_recursion_work_cap() -> None:
    """work cutoff 不得按 id 截断 direct/constant 候选（施工单 §3）。"""

    entries = [
        {"id": f"e{i:03d}", "keywords": ["clue"], "content": "body"} for i in range(50)
    ]
    matcher = _build(entries)
    hits = [row["id"] for row in matcher.match_with_recursive(
        "clue", timed_state={}, max_steps=10,
    )]
    assert len(hits) == 50, "50 个 direct 候选必须全部进入 ranking，而不是被 max_steps 砍到 10"
    assert matcher.last_cutoff == "", "没有递归展开就不该触发 work cutoff"


def test_high_priority_late_id_direct_candidate_still_wins_under_a_tight_cap(tmp_path: Path) -> None:
    """40 direct 候选、cap < 40、高 priority 条目 id 排最后 → 仍能胜出。

    「胜出」的判据是端到端结果：高 priority 条目进入最终预算选择，而不是被
    id 顺序的 work cutoff 提前挤掉。
    """

    entries = [
        {"id": f"e{i:03d}", "keywords": ["clue"], "content": "x" * 60, "priority": 0}
        for i in range(39)
    ]
    # id 字典序排在最后，但 priority 最高。
    entries.append({"id": "zzz", "keywords": ["clue"], "content": "x" * 60, "priority": 900})
    store = _store(tmp_path, entries)
    try:
        # 预算只装得下少数条目；若 direct 候选被 id 截断，zzz 根本进不了排序。
        hits, _ = _retrieve(store, "clue", overall_budget=200)
        ids = [row["id"] for row in hits]
        assert "zzz" in ids, "高 priority 条目不能因为 id 靠后就被 cap 挤掉"
    finally:
        store.close()


def test_constant_late_id_candidate_is_not_truncated_by_the_cap(tmp_path: Path) -> None:
    """constant 条目 id 排在 cap 之后 → 不得提前被截掉。"""

    entries = [
        {"id": f"e{i:03d}", "keywords": ["clue"], "content": "x" * 60}
        for i in range(40)
    ]
    entries.append({"id": "zzz", "keywords": [], "content": "x" * 60, "is_constant": True})
    store = _store(tmp_path, entries)
    try:
        hits, _ = _retrieve(store, "clue", overall_budget=200)
        assert "zzz" in {row["id"] for row in hits}, (
            "constant 条目必须进入 activation/预算排序，不能被 work cutoff 截掉"
        )
    finally:
        store.close()


def test_max_activated_cutoff_stops_further_expansion() -> None:
    matcher = _build(_deep_chain(6))
    hits = {row["id"] for row in matcher.match_with_recursive(
        "k0", timed_state={}, max_activated=2,
    )}
    assert matcher.last_cutoff == "max_activated"
    assert hits == {"e0", "e1"}, "达到候选上限后不再展开下一层"


def test_default_limits_do_not_bite_normal_books() -> None:
    matcher = _build(_deep_chain(4))
    hits = {row["id"] for row in matcher.match_with_recursive("k0", timed_state={})}
    assert hits == {"e0", "e1", "e2", "e3"}, "canonical 递归不再被固定 depth=3 截断"
    assert matcher.last_cutoff == ""
    assert MAX_RECURSION_STEPS > 0 and MAX_ACTIVATED_ENTRIES > 0


def test_canonical_recursion_is_not_capped_at_depth_three() -> None:
    """A → B → C → D → E：预算/scan_depth 允许时 E 必须可激活（施工单 §4）。"""

    matcher = _build(_deep_chain(6))
    hits = {row["id"] for row in matcher.match_with_recursive("k0", timed_state={})}
    assert hits == {"e0", "e1", "e2", "e3", "e4", "e5"}, (
        "canonical/ST 递归的边界来自 cycle guard / work cap / 各 per-entry gate，"
        "而不是所有新书硬截 3 层"
    )
    assert matcher.last_cutoff == ""


def test_cycle_guard_still_stops_canonical_recursion() -> None:
    """cycle 必须停止：a ↔ b 互相引用时不得无限展开。"""

    matcher = _build([
        {"id": "a", "keywords": ["start"], "content": "to-b"},
        {"id": "b", "keywords": ["to-b"], "content": "start to-a"},
        {"id": "to-a", "keywords": ["to-a"], "content": "to-b"},
    ])
    hits = [row["id"] for row in matcher.match_with_recursive("start", timed_state={})]
    assert len(hits) == len(set(hits)), "同一条目不得被重复激活"
    assert set(hits) == {"a", "b", "to-a"}


def test_scan_depth_still_bounds_canonical_recursion() -> None:
    """去掉固定 depth 后，per-entry ``scan_depth`` 仍然是有效边界。"""

    entries = _deep_chain(6)
    # scan_depth 是「该条目被递归到达时」的门：e2 在链上位于 depth 2，
    # 声明 scan_depth=1 表示它只允许在 depth <= 1 时被到达。
    entries[2]["scan_depth"] = 1
    matcher = _build(entries)
    hits = {row["id"] for row in matcher.match_with_recursive("k0", timed_state={})}
    assert {"e0", "e1"} <= hits
    assert "e2" not in hits, "scan_depth=1 必须挡住 depth 2 的 e2"
    assert "e3" not in hits, "e2 被挡住后链不再向下传播"


def test_legacy_triggers_recursive_keeps_historical_depth_bound() -> None:
    """legacy ``triggers_recursive`` 边保留历史 depth=3 行为（compatibility 层）。

    历史语义是 ``while frontier and depth < 3``，即只求值 depth 0/1/2 三层，
    因此链上第 4 个条目（l3）本来就不可达——这里钉住它没有被 §4 顺带放宽。
    """

    entries = [
        {"id": f"l{i}", "keywords": [f"k{i}"], "content": "",
         "triggers_recursive": [f"l{i + 1}"]}
        for i in range(6)
    ]
    entries.append({"id": "l6", "keywords": ["k6"], "content": ""})
    matcher = _build(entries)
    hits = {row["id"] for row in matcher.match_with_recursive("k0", timed_state={})}
    assert hits == {"l0", "l1", "l2"}, (
        "legacy 显式边继续按历史 depth 收敛，不影响 canonical/ST 递归"
    )


def test_max_steps_still_bounds_canonical_recursion() -> None:
    """max_steps 仍然生效（去掉固定 depth 后它是主要的 runaway 边界）。"""

    matcher = _build(_deep_chain(50))
    hits = [row["id"] for row in matcher.match_with_recursive(
        "k0", timed_state={}, max_steps=4,
    )]
    assert matcher.last_cutoff == "max_steps"
    assert len(hits) == 5, "1 direct seed + 4 recursive steps"


def test_budget_derived_candidate_cap_reaches_the_matcher(tmp_path: Path) -> None:
    """整体预算必须在**递归阶段**就收紧候选上限，而不是展开完再裁。"""

    # 一条长递归链 + 每步一个高开销条目：预算只装得下极少数。
    entries = [
        {"id": f"e{i:03d}", "keywords": [f"k{i}"], "content": f"k{i + 1} " + "x" * 500}
        for i in range(80)
    ]
    store = _store(tmp_path, entries)
    try:
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        # 预算只装得下 1 条（500 字符/条），放宽 2 倍后仍远小于 80，但不低于下限 32。
        asyncio.run(retriever.retrieve(_instance(store), "k000", overall_budget=600))
        assert retriever._matcher.last_cutoff == "max_activated"
    finally:
        store.close()


def test_no_budget_means_no_candidate_cap(tmp_path: Path) -> None:
    store = _store(tmp_path, [{"id": "e", "keywords": ["clue"], "content": "body"}])
    try:
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        assert retriever._candidate_cap(None) is None
        assert retriever._candidate_cap(0) is None
    finally:
        store.close()


# ---- ActivationTrace：真实原因 --------------------------------------------


def test_trace_records_the_real_probability_roll(tmp_path: Path) -> None:
    store = _store(tmp_path, [
        {"id": "sure", "keywords": ["clue"], "content": "body", "probability": 100},
        {"id": "never", "keywords": ["clue"], "content": "body", "probability": 0},
    ])
    try:
        hits, retriever = _retrieve(store, "clue")
        assert [row["id"] for row in hits] == ["sure"]
        trace = _trace_by_id(retriever)
        assert trace["never"]["reason_code"] == "probability_rejected"
        assert trace["never"]["probability"]["configured"] == 0
        assert trace["never"]["probability"]["accepted"] is False
        assert trace["never"]["probability"]["roll"] >= 1
        assert trace["sure"]["probability"]["accepted"] is True
    finally:
        store.close()


def test_trace_records_group_winner_and_loser(tmp_path: Path) -> None:
    store = _store(tmp_path, [
        {"id": "win", "keywords": ["clue"], "content": "body", "groups": ["g"],
         "prioritize_inclusion": True, "order": 10},
        {"id": "lose", "keywords": ["clue"], "content": "body", "groups": ["g"], "order": 20},
    ])
    try:
        hits, retriever = _retrieve(store, "clue")
        assert [row["id"] for row in hits] == ["win"]
        trace = _trace_by_id(retriever)
        assert trace["lose"]["reason_code"] == "group_lost"
        assert trace["lose"]["group"]["outcome"] == "lost"
        assert trace["lose"]["group"]["winner"] == "win"
        assert trace["win"]["group"]["outcome"] == "winner"
        assert trace["win"]["group"]["prioritized"] is True
        assert "g" in trace["win"]["group"]["names"]
    finally:
        store.close()


def test_trace_records_cooldown_and_delay_blocks(tmp_path: Path) -> None:
    store = _store(tmp_path, [
        {"id": "cool", "keywords": ["clue"], "content": "body", "cooldown": 2},
        {"id": "late", "keywords": ["clue"], "content": "body", "delay": 5},
    ])
    try:
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        instance = _instance(store, round_number=1)
        instance.lorebook_timed_state = {"cool": {"cooldown_remaining": 2}}
        asyncio.run(retriever.retrieve(instance, "clue"))
        trace = _trace_by_id(retriever)
        assert trace["cool"]["reason_code"] == "cooldown"
        assert trace["cool"]["timed"]["cooldown_blocked"] is True
        assert trace["cool"]["timed"]["cooldown_remaining"] == 2
        assert trace["late"]["reason_code"] == "delay"
        assert trace["late"]["timed"]["delay_blocked"] is True
        assert trace["late"]["timed"]["delay"] == 5
        assert trace["late"]["timed"]["current_tick"] == 1
    finally:
        store.close()


def test_trace_records_recursion_parent_and_depth(tmp_path: Path) -> None:
    store = _store(tmp_path, [
        {"id": "parent", "keywords": ["clue"], "content": "mentions sigil"},
        {"id": "child", "keywords": ["sigil"], "content": "child body"},
    ])
    try:
        hits, retriever = _retrieve(store, "clue")
        assert {row["id"] for row in hits} == {"parent", "child"}
        trace = _trace_by_id(retriever)
        assert trace["child"]["recursion_parent"] == "parent"
        assert trace["child"]["recursion_depth"] == 1
        assert trace["child"]["reason_code"] == "recursive"
        assert trace["parent"]["recursion_parent"] is None
        assert trace["parent"]["recursion_depth"] == 0
        assert trace["parent"]["reason_code"] == "keyword"
    finally:
        store.close()


def test_trace_marks_entries_that_were_never_candidates(tmp_path: Path) -> None:
    store = _store(tmp_path, [
        {"id": "hit", "keywords": ["clue"], "content": "body"},
        {"id": "quiet", "keywords": ["nothing-here"], "content": "body"},
    ])
    try:
        _, retriever = _retrieve(store, "clue")
        trace = _trace_by_id(retriever)
        assert trace["hit"]["reason_code"] == "keyword"
        assert trace["quiet"]["reason_code"] == "not_a_candidate"
    finally:
        store.close()


def test_budget_omission_never_masks_the_real_rejection(tmp_path: Path) -> None:
    """被 probability 拒绝的条目不能因为「也不在最终结果里」而被报成 budget。"""

    store = _store(tmp_path, [
        {"id": "keep", "keywords": ["clue"], "content": "x" * 100, "order": 10},
        {"id": "rejected", "keywords": ["clue"], "content": "x" * 100, "order": 20,
         "probability": 0},
    ])
    try:
        _, retriever = _retrieve(store, "clue", overall_budget=150)
        trace = _trace_by_id(retriever)
        assert trace["rejected"]["reason_code"] == "probability_rejected"
    finally:
        store.close()


def test_player_trace_still_hides_rejected_hidden_entries(tmp_path: Path) -> None:
    """新增原因字段不得削弱已经修好的 visibility fail-closed。"""

    store = _store(tmp_path, [
        {"id": "public", "keywords": ["clue"], "content": "body", "visible_to": ["*"]},
        {"id": "secret", "keywords": ["clue"], "content": "body", "visible_to": []},
    ])
    try:
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        instance = _instance(store)
        instance.players = {"p1": SimpleNamespace(user_id="p1", character_name="Hero")}
        asyncio.run(retriever.retrieve(
            instance, "clue", viewer_is_gm=False, viewer_uid="p1", viewer_name="Hero",
        ))
        rows = retriever.last_activation_trace
        assert [row["entry_id"] for row in rows] == ["public"]
        assert all("secret" not in str(row) for row in rows)

        # GM 视角能看到 hidden 条目被 visibility 拒绝的真实原因。
        asyncio.run(retriever.retrieve(instance, "clue", viewer_is_gm=True))
        gm_trace = _trace_by_id(retriever)
        assert gm_trace["secret"]["reason_code"] == "keyword"
    finally:
        store.close()
