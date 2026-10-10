"""REVIEW-7 §6 — Golden 最后补强的 3 个点。

现有 Golden 已经是真链，这里只补施工单要求的三条：

* §6.1 group 竞争必须是 **deterministic acceptance**：固定 rng 时同样输入给出同样
  winner，而不是「反正只留一个」。
* §6.2 per-book token_budget 与 overall lore budget 是**两级**预算，必须证明
  per-book 先裁、overall 再裁，而不是只看单级。
* §6.3 Swipe 的**成功链**：staged 克隆 → lore 检索 → 成功生成 swipe → 恰好提交一次
  → live 状态正确 → 权威 round tick 不额外推进 → timed state 不重复消费。
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.modules import narrative_notes, progression_state
from src.commands.state_update_applier import StateUpdateApplier
from src.commands.swipe_generator import SwipeGenerator
from src.engine.game_instance import GameInstance
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.retrieval import LoreRetriever
from src.lorebook.store import LorebookStore

WORLD = "w1"


def _store(tmp_path: Path) -> LorebookStore:
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world(WORLD, "Harbor World", description="", language="zh-CN")
    return store


def _instance(store: LorebookStore, **overrides):
    base = dict(
        world_id=WORLD, language="zh-CN", npcs={}, players={},
        modules={"narrative_notes": narrative_notes.fresh(), "progression": {**progression_state.fresh(), "round": 1}},
        world_state={}, lorebook_timed_state={},
        lorebook_store=store, game_id="", game_key="", action_actor_uids=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---- §6.1 deterministic group ----------------------------------------------


def _group_fixture(store: LorebookStore, book_id: str) -> None:
    """同组三条、无 prioritize_inclusion：winner 只能由 rng 决定。"""

    store.create_lorebook({"id": book_id, "name": "Group"})
    store.bind_lorebook({
        "id": f"b:{book_id}", "book_id": book_id,
        "scope_kind": "global", "scope_id": "",
    })
    for name in ("g1", "g2", "g3"):
        store.add_entry({
            "id": name, "book_id": book_id, "name": name,
            "keywords": ["harbor"], "content": name,
            "groups": ["shared"], "group_weight": 1,
        })


def _retrieve_with_seed(store: LorebookStore, seed: int, text: str) -> set[str]:
    """固定 seed 的 retriever：同一 seed 必须复现同一 winner。"""

    rng = random.Random(seed)
    retriever = LoreRetriever(KeywordMatcher(rng=rng.random), store=store)
    hits = asyncio.run(retriever.retrieve(_instance(store), text))
    return {entry["id"] for entry in hits}


def test_golden_group_winner_is_reproducible_under_a_fixed_rng(tmp_path):
    store = _store(tmp_path)
    try:
        _group_fixture(store, "grp")
        # 同一个 seed 重复跑多次：赢家必须完全一致。
        runs = [_retrieve_with_seed(store, 1234, "harbor") for _ in range(5)]
        assert all(len(run) == 1 for run in runs), f"组内应只留一个: {runs}"
        assert len({next(iter(run)) for run in runs}) == 1, f"固定 rng 下赢家不稳定: {runs}"

        # 换一个 seed 仍然只出一个（不同 seed 允许不同赢家，但绝不整组通过）。
        other = [_retrieve_with_seed(store, 99, "harbor") for _ in range(3)]
        assert all(len(run) == 1 for run in other)
        assert len({next(iter(run)) for run in other}) == 1
        assert {next(iter(run)) for run in other} <= {"g1", "g2", "g3"}
    finally:
        store.close()


def test_golden_prioritize_inclusion_beats_the_rng(tmp_path):
    """带 prioritize_inclusion 的成员必须确定性获胜，与 rng 无关。"""

    store = _store(tmp_path)
    try:
        _group_fixture(store, "grp")
        store.update_entry("g2", {"prioritize_inclusion": True, "priority": 500})
        for seed in (1, 2, 3, 4, 5):
            assert _retrieve_with_seed(store, seed, "harbor") == {"g2"}, (
                f"seed={seed} 时 prioritize_inclusion 未获胜"
            )
    finally:
        store.close()


def test_golden_group_loser_is_traced_as_group_lost(tmp_path):
    store = _store(tmp_path)
    try:
        _group_fixture(store, "grp")
        store.update_entry("g2", {"prioritize_inclusion": True, "priority": 500})
        rng = random.Random(7)
        matcher = KeywordMatcher(rng=rng.random)
        retriever = LoreRetriever(matcher, store=store)
        asyncio.run(retriever.retrieve(_instance(store), "harbor"))
        # trace 必须能区分「竞争落选」与其它拒绝原因，调用方才不会误报。
        rows = matcher.last_decisions
        assert rows["g2"]["group"]["outcome"] == "winner"
        assert rows["g1"]["group"]["outcome"] == "lost"
    finally:
        store.close()


# ---- §6.2 两级预算：per-book 先裁，overall 再裁 -----------------------------


def test_golden_per_book_budget_trims_before_the_overall_budget(tmp_path):
    """两级预算的真实链路证明。

    构造：A 里有一条 **优先级最高但超长** 的条目 a2，B 里有一条优先级较低的短条目 b1。
    如果 per-book 预算没有先裁掉 a2，overall 预算就会先按优先级留下 a2 而挤掉 b1。
    因此「b1 仍然出现」正是 per-book 先裁的证据。
    """

    store = _store(tmp_path)
    try:
        store.create_lorebook({"id": "bookA", "name": "A", "token_budget": 500})
        store.bind_lorebook({
            "id": "b:A", "book_id": "bookA", "scope_kind": "global", "scope_id": "",
        })
        store.create_lorebook({"id": "bookB", "name": "B"})
        store.bind_lorebook({
            "id": "b:B", "book_id": "bookB", "scope_kind": "global", "scope_id": "",
        })
        store.add_entry({
            "id": "a1", "book_id": "bookA", "name": "a1",
            "keywords": ["harbor"], "content": "a" * 100, "priority": 100,
        })
        store.add_entry({
            "id": "a2", "book_id": "bookA", "name": "a2",
            "keywords": ["harbor"], "content": "b" * 5000, "priority": 999,
        })
        store.add_entry({
            "id": "b1", "book_id": "bookB", "name": "b1",
            "keywords": ["harbor"], "content": "c" * 100, "priority": 50,
        })

        # 第一级：bookA 自己的 token_budget 先裁掉超长的 a2。
        # 第二级：overall 预算足以容纳 a1 + b1，但远不够 a2。
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        hits = asyncio.run(retriever.retrieve(
            _instance(store), "harbor", overall_budget=400,
        ))
        got = {entry["id"] for entry in hits}
        assert "a2" not in got, "per-book token_budget 未先裁掉超长条目"
        assert got == {"a1", "b1"}, (
            f"两级预算结果不对（per-book 未先裁会让 a2 挤掉 b1）: {got}"
        )

        # 把 overall 再收紧：第二级必须真的继续裁。
        tight = asyncio.run(retriever.retrieve(
            _instance(store), "harbor", overall_budget=150,
        ))
        assert {entry["id"] for entry in tight} == {"a1"}, "overall 预算未生效"
    finally:
        store.close()


# ---- §6.3 Swipe 成功链 -----------------------------------------------------


class _PromptStub:
    def load_swipe_rule_context(self, _instance, _loader):
        return SimpleNamespace(rule=None, combat_model=None, world_data={}, rule_appendix="")

    def compose_gm_prompt(self, *_args, **_kwargs):
        return "system"

    async def build_user_context(self, *_args, **_kwargs):
        return "user"


class _LLMStub:
    default = "fake"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls = 0

    async def call(self, **_kwargs):
        self.calls += 1
        content = self._replies.pop(0) if self._replies else "fallback"
        return SimpleNamespace(content=content)


def _swipe_setup(tmp_path: Path, replies: list[str]):
    store = _store(tmp_path)
    primary = store.primary_world_book_id(WORLD)
    store.add_entry({
        "id": "seed", "book_id": primary, "name": "Seed",
        "keywords": ["harbor"], "content": "harbor lore", "sticky": 3,
    })

    instance = GameInstance(game_key=("web", "room", "gm"))
    instance.world_id = WORLD
    instance.language = "zh-CN"
    instance.lorebook_store = store
    instance.lorebook_timed_state = {}
    instance.players = {"p1": {"character_name": "Aster"}}
    instance.round_number = 1
    instance.log = [{
        "round": 1,
        "gm_response": "original narration",
        "swipes": ["original narration"],
        "actions": [{"user_id": "p1", "text": "look at the harbor"}],
        "pre_state_snapshot": {},
    }]
    saved: list = []

    async def _save(inst):
        # SwipeGenerator awaits this hook, so it must be a coroutine function.
        saved.append(inst)

    class _SpyRetriever(LoreRetriever):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.seen: list = []

        async def retrieve(self, target, *args, **kwargs):  # type: ignore[override]
            self.seen.append(target)
            return await super().retrieve(target, *args, **kwargs)

    retriever = _SpyRetriever(KeywordMatcher(), store=store)
    # The real applier, not a stub: `parse_tag_state` always yields a (possibly
    # empty) state_update skeleton, so the swipe path genuinely calls it. Its own
    # behaviour is out of scope here -- this test is about staged/commit/timers.
    applier = StateUpdateApplier(Path("."), None, lambda *_a, **_k: None)
    generator = SwipeGenerator(
        _LLMStub(replies), KeywordMatcher(), _PromptStub(), applier,
        load_world_template=lambda *_a, **_k: None,
        ensure_matcher_for_world=lambda *_a, **_k: None,
        narrative_max_tokens=128,
        get_instance=lambda _key: instance,
        save_instance=_save,
        lore_retriever=retriever,
    )
    return store, instance, generator, retriever, saved


def test_golden_successful_swipe_staged_path_commits_exactly_once(tmp_path):
    store, instance, generator, retriever, saved = _swipe_setup(
        tmp_path, ["A rewritten harbor narration."],
    )
    try:
        before_round = instance.round_number
        narration = asyncio.run(generator.generate(instance, 1))

        assert narration == "A rewritten harbor narration.", f"生成失败: {narration!r}"
        # staged：检索跑在克隆上，不是 live 实例。
        assert retriever.seen and all(row is not instance for row in retriever.seen)
        # 恰好提交一次。
        assert len(saved) == 1, f"save_instance 调用次数应为 1，实际 {len(saved)}"
        assert saved[0] is instance

        entry = instance.log[0]
        assert "A rewritten harbor narration." in entry["swipes"], "新 swipe 未落进 run log"
        assert entry["gm_response"] == "A rewritten harbor narration."

        # 权威 round tick 不额外推进：swipe 是改写历史轮，不是新回合。
        assert instance.round_number == before_round, "swipe 不应推进 round_number"
    finally:
        store.close()


def test_golden_successful_swipe_consumes_timers_exactly_once(tmp_path):
    """timed state 不重复消费。

    swipe 是**改写历史轮**，不是新回合：权威 tick 由 `update_lorebook_timed_state()`
    按 round 推进，swipe 自己不推进它。所以同一轮反复生成 swipe 时，计时状态必须
    稳定在「激活一次」的结果上，而不是每生成一次就再扣一次。
    """

    store, instance, generator, _retriever, saved = _swipe_setup(
        tmp_path, ["First rewrite.", "Second rewrite.", "Third rewrite."],
    )
    try:
        assert asyncio.run(generator.generate(instance, 1)) == "First rewrite."
        first = dict(instance.lorebook_timed_state.get("seed") or {})
        # 命中一次即把 sticky 武装到配置值；这是激活，不是自减。
        assert first.get("sticky_remaining") == 3, f"sticky 未按配置武装: {first}"
        assert first.get("activated_tick") == 1, f"激活 tick 不对: {first}"

        def _counters() -> dict:
            """计时计数器的形状会被 save/load repair 边界补齐，只比较数值。"""

            row = instance.lorebook_timed_state.get("seed") or {}
            return {k: int(row.get(k, 0) or 0) for k in (
                "sticky_remaining", "cooldown_remaining", "pending_cooldown",
                "delay_remaining", "activated_tick",
            )}

        baseline = _counters()

        # 同一轮再生成：计时状态必须完全不被打动（不重复消费、不重复武装）。
        assert asyncio.run(generator.generate(instance, 1)) == "Second rewrite."
        assert _counters() == baseline, f"同一轮重复 swipe 改变了计时状态: {baseline} -> {_counters()}"

        assert asyncio.run(generator.generate(instance, 1)) == "Third rewrite."
        assert _counters() == baseline
        assert len(saved) == 3, "每次成功 swipe 恰好提交一次"

        # 只有权威 tick 才推进计时：模拟下一回合。
        instance.update_lorebook_timed_state()
        assert instance.lorebook_timed_state["seed"]["sticky_remaining"] == 2, (
            "权威 round tick 应把 sticky 3 → 2"
        )
    finally:
        store.close()


def test_golden_failed_swipe_does_not_consume_or_commit(tmp_path):
    """失败链的对照：不提交、也不消费 timer。"""

    class _Boom:
        default = "fake"

        async def call(self, **_kwargs):
            raise RuntimeError("llm down")

    store, instance, generator, _retriever, saved = _swipe_setup(tmp_path, [])
    try:
        generator.llm_client = _Boom()
        before = json.dumps(instance.to_dict(), sort_keys=True, default=str)
        with pytest.raises(RuntimeError, match="llm down"):
            asyncio.run(generator.generate(instance, 1))
        assert saved == []
        assert json.dumps(instance.to_dict(), sort_keys=True, default=str) == before
    finally:
        store.close()
