"""PR #398 follow-up regressions: vector_activation contract + timed lifecycle.

覆盖本轮四个 runtime 正确性缺陷：

1. ``vector_activation=off`` 曾在运行时被重新解释成 ``hybrid``，导致显式 off
   完全不可达（``_vector_mode`` 没有任何返回 ``off`` 的分支）。
2. 首次激活同时写 ``sticky_remaining`` 与 ``cooldown_remaining``，而两者同步递减，
   结果 cooldown 挡住的正是 sticky 本该生效的窗口——生命周期整个反了。
3. ``delay`` 被实现成「激活后堵 N 回合」，与 cooldown 完全等价，而 SillyTavern 的
   ``delay`` 是「聊天/回合计数达到 N 之前不激活」的前置门。
4. timed gate 与候选通道过滤只作用于 initial seeds，recursion frontier 里的子条目
   既能绕过自己的 cooldown，也能让 ``vector_only`` 被关键词扫描激活。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from src.engine.modules import narrative_notes, progression_state
from src.engine.modules.lorebook_runtime import normalize_timers as _normalize_lorebook_timed_state
from src.lorebook.activation import (
    advance_timed_state,
    delay_gate_blocked,
    migrate_timed_state,
    timed_gate_blocked,
)
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.retrieval import LoreRetriever
from src.lorebook.store import LorebookStore, normalize_vector_activation

# ---- 测试替身 ---------------------------------------------------------------


def _build(entries: list[dict], *, rng=None) -> KeywordMatcher:
    matcher = KeywordMatcher(rng=rng)
    defaults = {
        "enabled": True, "tier": "background", "order": 100, "probability": 100,
        "keywords": [], "content": "", "sticky": 0, "cooldown": 0, "delay": 0,
        "triggers_recursive": [],
    }
    matcher.build([dict(defaults, **entry) for entry in entries])
    return matcher


def _store(tmp_path: Path, entries: list[dict], *, book_settings: dict | None = None) -> LorebookStore:
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world("w", "World")
    store.ensure_primary_world_book("w")
    if book_settings:
        store.update_lorebook("world:w", book_settings)
    for entry in entries:
        row = {**entry, "world_id": "w"}
        row.setdefault("name", row.get("id", "entry"))
        store.add_entry(row)
    return store


def _instance(store: LorebookStore, timed_state: dict | None = None, *, round_number: int = 0):
    return SimpleNamespace(
        world_id="w", language="zh-CN", npcs={}, players={},
        modules={"narrative_notes": narrative_notes.fresh(), "progression": {**progression_state.fresh(), "round": round_number}},
        world_state={}, 
        lorebook_timed_state=timed_state if timed_state is not None else {},
        lorebook_store=store, action_actor_uids=[],
    )


def _client(vector: list[float]):
    """每个条目都 embed 成 query 向量，cosine 恒为 1.0：测的是门，不是向量数学。"""

    class _Client:
        model = "fake-embed"
        base_url = "http://fake.local/v1"
        max_input_chars = 500

        def __init__(self) -> None:
            self.embedded: list[list[str]] = []

        async def embed(self, text: str):
            return list(vector)

        async def embed_batch(self, texts: list[str]):
            self.embedded.append(list(texts))
            return [list(vector) for _ in texts]

    return _Client()


def _retrieve(store: LorebookStore, instance, text: str, client=None) -> list[dict]:
    retriever = LoreRetriever(
        KeywordMatcher(), store=store,
        embedding_client_provider=(lambda: client) if client is not None else None,
    )
    return asyncio.run(retriever.retrieve(instance, text))


# ---- §1 vector_activation：off 必须真的关闭 semantic ------------------------


def test_explicit_off_is_not_a_semantic_candidate(tmp_path: Path) -> None:
    """canonical explicit off：embedding provider 不参与该 entry，语义查询不得命中。"""

    store = _store(tmp_path, [
        {"id": "off", "keywords": ["nothing-matches-this"], "content": "off body",
         "vector_activation": "off"},
    ])
    try:
        client = _client([1.0, 0.0])
        assert _retrieve(store, _instance(store), "完全不同的提问文本", client) == []
        # 不只是「没进上下文」：off 条目根本不该被送去 embedding。
        assert client.embedded == []
    finally:
        store.close()


def test_explicit_off_still_activates_through_keywords(tmp_path: Path) -> None:
    """off 只关闭语义发现，不影响关键词通道。"""

    store = _store(tmp_path, [
        {"id": "off", "keywords": ["clue"], "content": "off body", "vector_activation": "off"},
    ])
    try:
        hits = _retrieve(store, _instance(store), "clue", _client([1.0, 0.0]))
        assert [row["id"] for row in hits] == ["off"]
    finally:
        store.close()


def test_hybrid_activates_on_both_channels(tmp_path: Path) -> None:
    store = _store(tmp_path, [
        {"id": "hy", "keywords": ["clue"], "content": "hybrid body", "vector_activation": "hybrid"},
    ])
    try:
        client = _client([1.0, 0.0])
        assert [r["id"] for r in _retrieve(store, _instance(store), "clue", client)] == ["hy"]
        assert [r["id"] for r in _retrieve(store, _instance(store), "无关提问", client)] == ["hy"]
    finally:
        store.close()


def test_vector_only_cannot_be_activated_by_keywords_alone(tmp_path: Path) -> None:
    store = _store(tmp_path, [
        {"id": "vo", "keywords": ["clue"], "content": "vector body",
         "vector_activation": "vector_only"},
    ])
    try:
        # 没有 embedding client：只剩关键词通道，vector_only 不得激活。
        assert _retrieve(store, _instance(store), "clue") == []
        # 有语义通道时可以激活。
        assert [r["id"] for r in _retrieve(store, _instance(store), "clue", _client([1.0, 0.0]))] == ["vo"]
    finally:
        store.close()


def test_entry_without_field_inherits_the_book_default(tmp_path: Path) -> None:
    """字段缺失才是 inherit：book 级 off 能真正关掉继承它的条目。"""

    entry = {"_lorebook_id": "world:w", "_lorebook_vector_activation": "off"}
    assert LoreRetriever._vector_mode(entry) == "off"
    assert LoreRetriever._vector_mode({"_lorebook_vector_activation": "vector_only"}) == "vector_only"
    # 没有 book 默认值时落到 canonical 默认（hybrid），保住 legacy 投影的语义增强。
    assert LoreRetriever._vector_mode({}) == "hybrid"
    # 显式 off 不再被 book 默认值覆盖。
    assert LoreRetriever._vector_mode(
        {"vector_activation": "off", "_lorebook_vector_activation": "hybrid"}
    ) == "off"


def test_write_and_read_paths_normalize_in_the_same_direction() -> None:
    """非法值在写路径与读路径必须落到同一个默认值，否则 off 又会不可达。"""

    assert normalize_vector_activation("nonsense") == "hybrid"
    assert normalize_vector_activation(None) == "hybrid"
    assert normalize_vector_activation("off") == "off"
    assert LoreRetriever._vector_mode({"vector_activation": "nonsense"}) == "hybrid"


def test_legacy_v5_rows_migrate_to_explicit_hybrid(tmp_path: Path) -> None:
    """v4/v5 老库的 primary world lore 迁移后仍保留 semantic behavior。

    pre-v6 的列是 INTEGER "vectorized" 旗标且默认 0，而 pre-v2 检索根本不看它。
    因此兼容性必须在 migration 里一次性决定为显式 hybrid，而不是在运行时把所有
    off 重新解释成 hybrid。
    """

    from src.migrations.lorebook import _v1, _v2, _v3, _v4, _v5, migrate
    from src.migrations.sqlite import run_migrations

    from src.lorebook.store import SCHEMA

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        run_migrations(conn, ((1, _v1), (2, _v2), (3, _v3), (4, _v4), (5, _v5)))
        conn.execute("INSERT OR IGNORE INTO worlds (id, name) VALUES ('w', 'World')")
        # 老库里 vectorized=0（默认值）与 vectorized=1 两种行都要保住语义检索。
        conn.execute(
            "INSERT INTO lorebook_entries (id, world_id, name, vector_activation) "
            "VALUES ('legacy-default', 'w', 'Legacy', 0)"
        )
        conn.execute(
            "INSERT INTO lorebook_entries (id, world_id, name, vector_activation) "
            "VALUES ('legacy-vectorized', 'w', 'Vectorized', 1)"
        )
        conn.commit()

        migrate(conn)

        modes = dict(conn.execute("SELECT id, vector_activation FROM lorebook_entries"))
        assert modes == {"legacy-default": "hybrid", "legacy-vectorized": "hybrid"}
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
    finally:
        conn.close()


def test_canonical_off_survives_migration_when_already_textual(tmp_path: Path) -> None:
    """已经是 canonical 文本模式的行不得被 migration 改写。"""

    store = _store(tmp_path, [
        {"id": "off", "vector_activation": "off"},
        {"id": "only", "vector_activation": "vector_only"},
    ])
    try:
        assert store.get_entry("off")["vector_activation"] == "off"
        assert store.get_entry("only")["vector_activation"] == "vector_only"
    finally:
        store.close()


# ---- §2 sticky → cooldown 生命周期 -----------------------------------------


def test_activation_arms_cooldown_behind_sticky() -> None:
    matcher = _build([{"id": "e", "keywords": ["door"], "sticky": 3, "cooldown": 2}])
    state: dict = {}
    matcher.match_with_recursive("door", timed_state=state)
    assert state["e"]["sticky_remaining"] == 3
    assert state["e"].get("cooldown_remaining", 0) == 0
    assert state["e"]["pending_cooldown"] == 2


def test_sticky_entry_keeps_firing_on_later_turns_without_a_new_match() -> None:
    """§2 的核心回归：sticky 条目第二轮必须仍然生效，而不是被自己的 cooldown 挡掉。"""

    matcher = _build([{"id": "e", "keywords": ["door"], "sticky": 3, "cooldown": 2}])
    state: dict = {}
    assert {r["id"] for r in matcher.match_with_recursive("door", timed_state=state)} == {"e"}

    for turn in range(3):
        advance_timed_state(state["e"])
        hits = {r["id"] for r in matcher.match_with_recursive("无关文本", timed_state=state)}
        if turn < 2:
            assert hits == {"e"}, f"sticky 在第 {turn + 2} 轮丢失"

    # sticky 用尽后 cooldown 才开始，条目此时被挡住。
    assert state["e"]["sticky_remaining"] == 0
    assert state["e"]["cooldown_remaining"] == 2
    assert {r["id"] for r in matcher.match_with_recursive("door", timed_state=state)} == set()


def test_full_sticky_then_cooldown_then_inactive_lifecycle() -> None:
    state = {"sticky_remaining": 2, "cooldown_remaining": 0, "pending_cooldown": 1,
             "delay_remaining": 0}

    assert not timed_gate_blocked(state)          # sticky 期间不被自己的 cooldown 挡
    assert advance_timed_state(state) is False    # sticky 2 → 1
    assert not timed_gate_blocked(state)
    assert advance_timed_state(state) is False    # sticky 1 → 0，此时 arm cooldown
    assert state["cooldown_remaining"] == 1
    assert timed_gate_blocked(state)              # cooldown 开始生效
    assert advance_timed_state(state) is True     # cooldown 1 → 0，整体过期
    assert not timed_gate_blocked(state)


def test_cooldown_without_sticky_starts_immediately() -> None:
    matcher = _build([{"id": "e", "keywords": ["door"], "cooldown": 2}])
    state: dict = {}
    matcher.match_with_recursive("door", timed_state=state)
    assert state["e"]["cooldown_remaining"] == 2
    assert state["e"]["pending_cooldown"] == 0
    assert {r["id"] for r in matcher.match_with_recursive("door", timed_state=state)} == set()


def test_retrigger_inside_an_active_window_does_not_refresh_the_timers() -> None:
    """周期内重复命中不重置计时器——否则常被提及的条目会永久 sticky。"""

    matcher = _build([{"id": "e", "keywords": ["door"], "sticky": 2, "cooldown": 1}])
    state: dict = {}
    matcher.match_with_recursive("door", timed_state=state)
    advance_timed_state(state["e"])
    assert state["e"]["sticky_remaining"] == 1

    matcher.match_with_recursive("door", timed_state=state)
    assert state["e"]["sticky_remaining"] == 1, "sticky 不应被重新命中刷新"


def test_corrupt_saves_written_by_the_old_runtime_are_repaired() -> None:
    """旧运行时写出的 sticky+cooldown 同时在跑的存档必须被修复，否则 bug 跨存档存活。"""

    repaired = migrate_timed_state({"e": {"sticky_remaining": 3, "cooldown_remaining": 2}})
    assert repaired["e"]["sticky_remaining"] == 3
    assert repaired["e"]["cooldown_remaining"] == 0
    assert repaired["e"]["pending_cooldown"] == 2
    assert not timed_gate_blocked(repaired["e"])


def test_engine_persistence_boundary_shares_the_repair() -> None:
    """codec 的规范化必须和 lorebook 域同一份实现，否则 save/reload 会把修复丢掉。"""

    normalized = _normalize_lorebook_timed_state({"e": {"sticky_remaining": 2, "cooldown_remaining": 5}})
    assert normalized["e"] == {
        "sticky_remaining": 2, "cooldown_remaining": 0, "delay_remaining": 0,
        "pending_cooldown": 5, "activated_tick": 0,
    }


def test_legacy_status_shape_is_still_understood() -> None:
    assert timed_gate_blocked({"status": "cooldown", "remaining": 2})
    assert timed_gate_blocked({"status": "delayed", "remaining": 1})
    assert not timed_gate_blocked({"status": "active", "remaining": 2})
    assert not timed_gate_blocked({"status": "cooldown", "remaining": 0})


# ---- §3 delay 是前置门，不是第二套 cooldown --------------------------------


def test_delay_blocks_until_the_turn_counter_reaches_it() -> None:
    """ST 语义：delay=3 的条目在第 3 回合之前不激活，而不是先激活再堵 3 回合。"""

    matcher = _build([{"id": "e", "keywords": ["door"], "delay": 3}])
    for tick in (0, 1, 2):
        state: dict = {}
        hits = matcher.match_with_recursive("door", timed_state=state, current_tick=tick)
        assert hits == [], f"delay=3 在 tick={tick} 不该激活"
        assert state == {}, "被 delay 挡住的条目不得写 timed state"

    state = {}
    hits = matcher.match_with_recursive("door", timed_state=state, current_tick=3)
    assert {r["id"] for r in hits} == {"e"}


def test_delay_never_writes_a_countdown_counter() -> None:
    """delay 不得变成与 sticky/cooldown 并列的第二套状态机。"""

    matcher = _build([{"id": "e", "keywords": ["door"], "delay": 2, "sticky": 1}])
    state: dict = {}
    matcher.match_with_recursive("door", timed_state=state, current_tick=5)
    assert state["e"]["sticky_remaining"] == 1
    assert "delay_remaining" not in state["e"]


def test_missing_tick_authority_skips_the_delay_gate() -> None:
    """world / NPC 投影没有 tick authority，不应凭空发明一个 tick 把条目全挡掉。"""

    matcher = _build([{"id": "e", "keywords": ["door"], "delay": 5}])
    assert {r["id"] for r in matcher.match("door")} == {"e"}
    assert delay_gate_blocked({"delay": 5}, current_tick=None) is False
    assert delay_gate_blocked({"delay": 5}, current_tick=4) is True
    assert delay_gate_blocked({"delay": 5}, current_tick=5) is False


def test_legacy_delay_remaining_saves_still_block() -> None:
    assert timed_gate_blocked({"delay_remaining": 2})
    state = {"sticky_remaining": 0, "cooldown_remaining": 0, "delay_remaining": 1,
             "pending_cooldown": 0}
    assert advance_timed_state(state) is True
    assert not timed_gate_blocked(state)


# ---- §4 timed / 通道 gate 属于每一次 candidate eligibility -----------------


def test_recursive_child_cannot_bypass_its_own_cooldown() -> None:
    """A 命中 → A.content 命中 B，但 B 在 cooldown：B 不得被递归激活。"""

    matcher = _build([
        {"id": "A", "keywords": ["door"], "content": "the door hides a sigil"},
        {"id": "B", "keywords": ["sigil"], "content": "sigil body", "cooldown": 2},
    ])
    state = {"B": {"cooldown_remaining": 2}}
    hits = {r["id"] for r in matcher.match_with_recursive("door", timed_state=state)}
    assert hits == {"A"}, "cooldown 中的 B 通过 recursion frontier 绕过了 timed gate"


def test_explicit_triggers_recursive_child_cannot_bypass_cooldown() -> None:
    matcher = _build([
        {"id": "A", "keywords": ["door"], "content": "body", "triggers_recursive": ["B"]},
        {"id": "B", "keywords": ["never-matches"], "content": "b body", "cooldown": 2},
    ])
    state = {"B": {"cooldown_remaining": 2}}
    hits = {r["id"] for r in matcher.match_with_recursive("door", timed_state=state)}
    assert hits == {"A"}


def test_recursive_child_obeys_the_delay_gate_like_a_direct_hit() -> None:
    matcher = _build([
        {"id": "A", "keywords": ["door"], "content": "the door hides a sigil"},
        {"id": "B", "keywords": ["sigil"], "content": "sigil body", "delay": 4},
    ])
    blocked = {r["id"] for r in matcher.match_with_recursive("door", timed_state={}, current_tick=1)}
    assert blocked == {"A"}
    allowed = {r["id"] for r in matcher.match_with_recursive("door", timed_state={}, current_tick=4)}
    assert allowed == {"A", "B"}


def test_recursion_cannot_keyword_activate_a_vector_only_entry() -> None:
    """通道过滤此前只作用于 initial seeds，recursion 扫描是关键词通道的漏口。"""

    matcher = _build([
        {"id": "A", "keywords": ["door"], "content": "the door hides a sigil"},
        {"id": "VO", "keywords": ["sigil"], "content": "vector body",
         "vector_activation": "vector_only"},
    ])
    hits = {
        r["id"] for r in matcher.match_with_recursive(
            "door", timed_state={},
            is_candidate=lambda entry: entry.get("vector_activation") != "vector_only",
        )
    }
    assert hits == {"A"}, "vector_only 条目被递归里的关键词扫描激活了"


def test_explicit_trigger_edge_still_reaches_a_vector_only_entry() -> None:
    """作者显式声明的 triggers_recursive 是显式边，不是关键词发现，因此被尊重。"""

    matcher = _build([
        {"id": "A", "keywords": ["door"], "content": "body", "triggers_recursive": ["VO"]},
        {"id": "VO", "keywords": ["never"], "content": "vector body",
         "vector_activation": "vector_only"},
    ])
    hits = {
        r["id"] for r in matcher.match_with_recursive(
            "door", timed_state={},
            is_candidate=lambda entry: entry.get("vector_activation") != "vector_only",
        )
    }
    assert hits == {"A", "VO"}


def test_sticky_vector_only_entry_keeps_firing_through_the_keyword_pass() -> None:
    """靠语义激活并进入 sticky 的 vector_only 条目，不该被通道过滤掐断 sticky。"""

    matcher = _build([
        {"id": "VO", "keywords": ["never-matches"], "content": "vector body",
         "vector_activation": "vector_only", "sticky": 2},
    ])
    state = {"VO": {"sticky_remaining": 2, "pending_cooldown": 0}}
    hits = {
        r["id"] for r in matcher.match_with_recursive(
            "无关文本", timed_state=state,
            is_candidate=lambda entry: entry.get("vector_activation") != "vector_only",
        )
    }
    assert hits == {"VO"}


# ---- §13 player trace 不得泄漏 hidden 数量 ---------------------------------


def test_player_trace_emits_no_row_for_hidden_entries(tmp_path: Path) -> None:
    store = _store(tmp_path, [
        {"id": "public", "keywords": ["clue"], "content": "public body", "visible_to": ["*"]},
        {"id": "secret1", "keywords": ["clue"], "content": "secret body", "visible_to": []},
        {"id": "secret2", "keywords": ["clue"], "content": "secret body", "visible_to": []},
    ])
    try:
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        instance = _instance(store)
        instance.players = {"p1": SimpleNamespace(user_id="p1", character_name="Hero")}
        asyncio.run(retriever.retrieve(
            instance, "clue", viewer_is_gm=False, viewer_uid="p1", viewer_name="Hero",
        ))
        trace = retriever.last_activation_trace
        assert [row["entry_id"] for row in trace] == ["public"]
        # 行数本身不得随 hidden 条目增加：两条 secret 一行都不能出现。
        assert len(trace) == 1
    finally:
        store.close()
