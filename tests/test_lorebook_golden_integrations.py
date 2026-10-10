"""§13 — Golden 后端集成链路。

施工单要求在后端补三条真实链路（不是单元级 mock）：

* **A** Store → Resolver → LoreRetriever：probability / inclusion group /
  overall budget / semantic 通道 / GM-player-KP 可见性。
* **B** GameInstance → save → restart → reload：sticky / cooldown / delay /
  round tick，以及 KP 提问观察计时器但不修改。
* **C** 真实 ``SwipeGenerator`` staged 路径。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.modules import narrative_notes, progression_state
from src.engine.game_instance import GameInstance
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.resolver import resolve_active_books
from src.lorebook.retrieval import LoreRetriever
from src.lorebook.store import LorebookStore
from src.engine.modules import lorebook_runtime

WORLD = "w"
GAME_KEY = "web|room|gm"


# ---- 共享 fixture ----------------------------------------------------------


class _EmbedClient:
    """每个条目都 embed 成同一个向量，cosine 恒为 1：测的是通道，不是向量数学。"""

    model = "fake-embed"
    base_url = "http://fake.local/v1"
    max_input_chars = 500

    async def embed(self, text: str):
        return [1.0, 0.0]

    async def embed_batch(self, texts: list[str]):
        return [[1.0, 0.0] for _ in texts]


def _world_store(tmp_path: Path) -> LorebookStore:
    """四个作用域的 Book：global / world / game / character。"""

    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world(WORLD, "Harbor World", description="", language="zh-CN")
    store.create_lorebook({"id": "global-book", "name": "Global"})
    store.bind_lorebook({
        "id": "b:global", "book_id": "global-book",
        "scope_kind": "global", "scope_id": "",
    })
    store.create_lorebook({"id": "game-book", "name": "Game"})
    store.bind_lorebook({
        "id": "b:game", "book_id": "game-book",
        "scope_kind": "game", "scope_id": GAME_KEY,
    })
    store.create_lorebook({"id": "hero-book", "name": "Hero"})
    store.bind_lorebook({
        "id": "b:hero", "book_id": "hero-book",
        "scope_kind": "character", "scope_id": "p1",
    })
    return store


def _add(store: LorebookStore, book_id: str, entry: dict) -> None:
    store.add_entry({"book_id": book_id, **entry})


def _instance(store: LorebookStore, **overrides):
    base = dict(
        world_id=WORLD, language="zh-CN", npcs={}, players={}, world_state={},
        lorebook_store=store, game_id=GAME_KEY, game_key=GAME_KEY,
        action_actor_uids=[],
    )
    timers = overrides.pop("lorebook_timed_state", {})
    base.update(overrides)
    base["modules"] = {
        "narrative_notes": narrative_notes.fresh(),
        "lorebook_runtime": {**lorebook_runtime.fresh(), "timers": timers},
        "progression": {**progression_state.fresh(), "round": 1},
    }
    return SimpleNamespace(**base)


def _retrieve(store, instance, text, *, client=None, **kwargs):
    retriever = LoreRetriever(
        KeywordMatcher(), store=store,
        embedding_client_provider=(lambda: client) if client is not None else None,
    )
    return asyncio.run(retriever.retrieve(instance, text, **kwargs))


# ---- A: Store → Resolver → LoreRetriever ----------------------------------


def test_golden_a_resolver_selects_books_by_scope_and_viewer(tmp_path):
    """Resolver 是作用域的唯一裁决者：global/world 恒在，game 按场次，
    character 只在「本人视角或本人在场」时放行。"""

    store = _world_store(tmp_path)
    try:
        # GM 但该角色不在场：不拿别人的 character book。
        gm = _instance(store)
        assert {r.book_id for r in resolve_active_books(gm, "gm")} == {
            f"world:{WORLD}", "global-book", "game-book",
        }
        # 该角色在场（action_actor_uids 是 resolver 的参数）：GM 才拿得到它的
        # character book。
        acting = _instance(store)
        assert {r.book_id for r in resolve_active_books(acting, "gm", "", ["p1"])} == {
            f"world:{WORLD}", "global-book", "game-book", "hero-book",
        }
        # 玩家本人视角：global + world + 本场次的 game book + 自己的 character book。
        own = _instance(store)
        assert {r.book_id for r in resolve_active_books(own, "character", "p1")} == {
            f"world:{WORLD}", "global-book", "game-book", "hero-book",
        }
        # 纯 party 视角：character book 不放行。
        party = _instance(store)
        assert {r.book_id for r in resolve_active_books(party, "party")} == {
            f"world:{WORLD}", "global-book", "game-book",
        }
    finally:
        store.close()


def test_golden_a_probability_zero_never_activates(tmp_path):
    store = _world_store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        _add(store, primary, {"id": "always", "name": "A", "keywords": ["harbor"],
                              "content": "always", "probability": 100})
        _add(store, primary, {"id": "never", "name": "N", "keywords": ["harbor"],
                              "content": "never", "probability": 0})
        hits = _retrieve(store, _instance(store), "harbor")
        assert {e["id"] for e in hits} == {"always"}
    finally:
        store.close()


def test_golden_a_inclusion_group_only_one_wins(tmp_path):
    """同一 inclusion group 内竞争，不会整组一起进上下文。"""

    store = _world_store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        _add(store, primary, {
            "id": "strong", "name": "S", "keywords": ["harbor"], "content": "strong",
            "groups": ["g"], "group_weight": 10,
        })
        _add(store, primary, {
            "id": "weak", "name": "W", "keywords": ["harbor"], "content": "weak",
            "groups": ["g"], "group_weight": 1,
        })
        hits = _retrieve(store, _instance(store), "harbor")
        # group_weight 是组内权重（可随机化），所以断言的是「只出一个」这条产品性质，
        # 而不是某一个具体赢家。
        assert len(hits) == 1, f"组内应只留一个，实际 {[e['id'] for e in hits]}"
        assert {e["id"] for e in hits} <= {"strong", "weak"}
    finally:
        store.close()


def test_golden_a_overall_budget_trims_the_tail(tmp_path):
    """overall_budget 是真的整体裁剪：低优先级的条目被丢掉，而不是只进候选。"""

    store = _world_store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        _add(store, primary, {"id": "top", "name": "T", "keywords": ["harbor"],
                              "content": "x" * 40, "priority": 900})
        _add(store, primary, {"id": "tail", "name": "L", "keywords": ["harbor"],
                              "content": "y" * 4000, "priority": 1})
        no_budget = {e["id"] for e in _retrieve(store, _instance(store), "harbor")}
        assert no_budget == {"top", "tail"}
        trimmed = {e["id"] for e in _retrieve(
            store, _instance(store), "harbor", overall_budget=200,
        )}
        assert trimmed == {"top"}, f"预算裁剪后只该剩最高优先级，实际 {trimmed}"
    finally:
        store.close()


def test_golden_a_semantic_channel_reaches_retrieval(tmp_path):
    """semantic 通道端到端可用：关键词不命中也要能靠向量进入上下文。"""

    store = _world_store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        _add(store, primary, {
            "id": "sem", "name": "S", "keywords": ["no-such-token"],
            "content": "semantic body", "vector_activation": "vector_only",
        })
        assert _retrieve(store, _instance(store), "完全不同的提问") == []
        hits = _retrieve(store, _instance(store), "完全不同的提问", client=_EmbedClient())
        assert {e["id"] for e in hits} == {"sem"}
    finally:
        store.close()


def test_golden_a_visibility_is_enforced_end_to_end(tmp_path):
    """GM 全见；party 只见公开；character 见公开 + 本人。"""

    store = _world_store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        _add(store, primary, {"id": "public", "name": "P", "keywords": ["harbor"],
                              "content": "public", "visible_to": ["party"]})
        _add(store, primary, {"id": "secret", "name": "S", "keywords": ["harbor"],
                              "content": "secret", "visible_to": []})
        _add(store, primary, {"id": "hero", "name": "H", "keywords": ["harbor"],
                              "content": "hero", "visible_to": ["p1"]})

        gm = {e["id"] for e in _retrieve(store, _instance(store), "harbor")}
        assert gm == {"public", "secret", "hero"}, "GM 必须全见"

        party = {e["id"] for e in _retrieve(
            store, _instance(store), "harbor", viewer_is_gm=False,
        )}
        assert party == {"public"}, f"party 只见公开条目，实际 {party}"

        own = {e["id"] for e in _retrieve(
            store, _instance(store), "harbor", viewer_is_gm=False, viewer_uid="p1",
        )}
        assert own == {"public", "hero"}, f"角色只见公开 + 本人，实际 {own}"
    finally:
        store.close()


# ---- B: GameInstance → save → restart → reload ----------------------------


def test_golden_b_timed_state_survives_save_restart_reload(tmp_path):
    """sticky → cooldown 的完整生命周期跨 save/restart/reload 保持一致。"""

    instance = GameInstance(game_key=("web", "room", "gm"))
    lorebook_runtime.replace_timers(instance, {
        "e1": {"sticky_remaining": 2, "cooldown_remaining": 0,
               "pending_cooldown": 1, "delay_remaining": 0},
    })

    seen: list[dict] = []
    for _ in range(3):
        # 每一轮都走真实的持久化边界：save → restart → reload。
        reloaded = GameInstance.from_dict(instance.to_dict())
        saved = lorebook_runtime.timers(reloaded)["e1"]
        # save/load 是 repair 边界（会补齐缺省字段），所以比较计时语义而非整个 dict。
        for field in ("sticky_remaining", "cooldown_remaining", "pending_cooldown"):
            assert saved[field] == lorebook_runtime.timers(instance)["e1"][field], (
                f"save/restart 丢了 {field}"
            )
        seen.append(dict(saved))
        reloaded.update_lorebook_timed_state()
        instance = reloaded

    # 进入每一轮 tick 之前的状态：sticky 2 → 1 → 0(并 arm cooldown 1)。
    assert [s["sticky_remaining"] for s in seen] == [2, 1, 0]
    assert [s["cooldown_remaining"] for s in seen] == [0, 0, 1], (
        "sticky 用尽时必须 arm cooldown"
    )
    # 第 3 次 tick 之后整体过期，计时状态被清掉。
    assert "e1" not in lorebook_runtime.timers(instance)


# ---- C: 真实 SwipeGenerator staged 路径 -----------------------------------


def test_golden_c_swipe_rewrite_runs_on_a_staged_clone_and_never_commits_on_failure(
    tmp_path,
):
    """真实的 SwipeGenerator：重写跑在 staged 克隆上，LLM 失败时 live 实例零改动。"""

    import json

    from src.commands.swipe_generator import SwipeGenerator

    store = _world_store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        _add(store, primary, {"id": "seed", "name": "S", "keywords": ["harbor"],
                              "content": "seed"})

        instance = GameInstance(game_key=("web", "room", "gm"))
        instance.world_id = WORLD
        instance.language = "zh-CN"
        instance.lorebook_store = store
        lorebook_runtime.replace_timers(instance, {})
        instance.players = {"p1": {"character_name": "Aster"}}
        instance.round_number = 1
        instance.log = [{
            "round": 1,
            "gm_response": "original",
            "swipes": ["original"],
            "actions": [{"user_id": "p1", "text": "look at the harbor"}],
            "pre_state_snapshot": {},
        }]
        before = json.dumps(instance.to_dict(), sort_keys=True, default=str)
        saved: list = []

        class _SpyRetriever(LoreRetriever):
            """记录被检索的是哪个实例：staged 路径必须拿克隆，不是 live。"""

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.seen: list = []

            async def retrieve(self, target, *args, **kwargs):  # type: ignore[override]
                self.seen.append(target)
                return await super().retrieve(target, *args, **kwargs)

        class _LLM:
            default = "fake"

            async def call(self, **_kwargs):
                raise RuntimeError("llm down")

        class _Prompt:
            def load_swipe_rule_context(self, _instance, _loader):
                return SimpleNamespace(
                    rule=None, combat_model=None, world_data={}, rule_appendix="",
                )

            def compose_gm_prompt(self, *_args, **_kwargs):
                return "system"

            async def build_user_context(self, *_args, **_kwargs):
                return "user"

        retriever = _SpyRetriever(KeywordMatcher(), store=store)
        generator = SwipeGenerator(
            _LLM(), KeywordMatcher(), _Prompt(), None,
            load_world_template=lambda *_a, **_k: None,
            ensure_matcher_for_world=lambda *_a, **_k: None,
            narrative_max_tokens=128,
            get_instance=lambda _key: instance,
            save_instance=lambda inst: saved.append(inst),
            lore_retriever=retriever,
        )

        with pytest.raises(RuntimeError, match="llm down"):
            asyncio.run(generator.generate(instance, 1))

        # staged：检索发生在克隆上，而不是 live 实例。
        assert retriever.seen, "swipe 必须真的走过 lore 检索"
        assert all(row is not instance for row in retriever.seen), (
            "staged 路径把 live 实例直接交给了检索器"
        )
        # 失败即不提交：save_instance 不被调用，live 实例逐字节不变。
        assert saved == [], "失败的 swipe 不得提交"
        assert json.dumps(instance.to_dict(), sort_keys=True, default=str) == before, (
            "失败的 swipe 改写了 live 实例"
        )
    finally:
        store.close()


def test_golden_b_kp_question_observes_timers_without_mutating(tmp_path):
    """KP/玩家提问必须看到计时器，但绝不改写它们。"""

    store = _world_store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        # visible_to 必须包含 p1，否则按 fail-closed 语义是 GM-only，玩家视角看不到。
        _add(store, primary, {"id": "timed", "name": "T", "keywords": ["harbor"],
                              "content": "timed", "sticky": 3, "cooldown": 2,
                              "visible_to": ["p1"]})
        state: dict = {}
        instance = _instance(store, lorebook_timed_state=state,
                             players={"p1": {"character_name": "Aster"}})

        # 正常回合：写入计时状态。
        _retrieve(store, instance, "harbor")
        assert state["timed"]["sticky_remaining"] == 3
        before = {k: dict(v) for k, v in state.items()}

        # KP 提问：观察但不修改。
        _retrieve(store, instance, "harbor", viewer_is_gm=False,
                  viewer_uid="p1", viewer_name="Aster", mutate_timers=False)
        assert state == before, "mutate_timers=False 不得改动计时状态"

        # 确认提问确实「看到」了计时器（sticky 生效中仍然命中）。
        hits = _retrieve(store, instance, "unrelated text", viewer_is_gm=False,
                         viewer_uid="p1", viewer_name="Aster", mutate_timers=False)
        assert {e["id"] for e in hits} == {"timed"}
        assert state == before
    finally:
        store.close()


def test_golden_b_round_tick_drives_delay(tmp_path):
    """delay 由权威 round tick 驱动，而不是倒计时自减。"""

    instance = GameInstance(game_key=("web", "room", "gm"))
    lorebook_runtime.replace_timers(instance, {
        "e1": {"sticky_remaining": 0, "cooldown_remaining": 0,
               "pending_cooldown": 0, "delay_remaining": 2},
    })
    payload = instance.to_dict()
    restored = GameInstance.from_dict(payload)
    assert lorebook_runtime.timers(restored)["e1"]["delay_remaining"] == 2
    restored.update_lorebook_timed_state()
    assert lorebook_runtime.timers(restored)["e1"]["delay_remaining"] == 1
    restored.update_lorebook_timed_state()
    # delay 归零后条目整体过期，计时状态被清掉而不是留一个 0 的僵尸项。
    assert "e1" not in lorebook_runtime.timers(restored)
