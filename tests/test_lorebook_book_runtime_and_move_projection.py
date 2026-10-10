"""REVIEW-7 §1/§2 — Book 自身也是 runtime 语义的一部分。

两个 blocker：

1. Book `enabled=false` 之前只是 Sidebar 上的标签，Resolver 仍返回该 Book，
   Retriever 继续加载它。停用必须是 runtime 决定。
2. Book 自己的 retrieval setting（`enabled` / `scan_depth` / `token_budget` /
   `recursive_scanning` / `settings_json`）之前只更新秒级 `updated_at`，没有 bump
   monotonic revision，所以同一秒内连续修改会让 scope fingerprint 不变、
   matcher 不 rebuild，继续使用旧的 Book annotation。

以及 §2 的 move：`move_entry` 只改 `book_id`，没有同步 `world_id`
compatibility projection，会让 `delete_world_cascade` 误删已移走的条目。

所有缓存断言都复用**同一个** retriever 实例；每次新建 retriever 会绕过缓存，
测不出这组 bug（已验证：去掉 revision bump 后同秒修改返回陈旧结果）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.modules import narrative_notes, progression_state
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.resolver import resolve_active_books
from src.lorebook.retrieval import LoreRetriever
from src.lorebook.store import LorebookStore

WORLD = "w1"


def _store(tmp_path: Path) -> LorebookStore:
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world(WORLD, "World", description="", language="zh-CN")
    store.create_lorebook({"id": "gb", "name": "Global"})
    store.bind_lorebook({
        "id": "b:gb", "book_id": "gb", "scope_kind": "global", "scope_id": "",
    })
    return store


def _instance(store: LorebookStore):
    return SimpleNamespace(
        world_id=WORLD, language="zh-CN", npcs={}, players={},
        modules={"narrative_notes": narrative_notes.fresh(), "progression": {**progression_state.fresh(), "round": 1}},
        world_state={}, lorebook_timed_state={},
        lorebook_store=store, game_id="", game_key="", action_actor_uids=[],
    )


class _Session:
    """One persistent retriever so every call goes through the real cache."""

    def __init__(self, store: LorebookStore) -> None:
        self._retriever = LoreRetriever(KeywordMatcher(), store=store)
        self._instance = _instance(store)

    def __call__(self, text: str) -> set[str]:
        hits = asyncio.run(self._retriever.retrieve(self._instance, text))
        return {entry["id"] for entry in hits}


# ---- §1 Book enabled 必须真正退出 runtime ---------------------------------


def test_disabled_book_leaves_the_runtime_and_comes_back(tmp_path):
    store = _store(tmp_path)
    try:
        store.add_entry({
            "id": "A", "book_id": "gb", "name": "A",
            "keywords": ["door"], "content": "the door",
        })
        retrieve = _Session(store)
        assert retrieve("door") == {"A"}

        store.update_lorebook("gb", {"enabled": False})
        assert retrieve("door") == set(), "停用的 Book 不得再贡献候选"
        # 停用是一个 runtime 决定，不只是 Sidebar 标签：Resolver 不能再返回它。
        active = {r.book_id for r in resolve_active_books(_instance(store), "gm")}
        assert "gb" not in active, f"停用的 Book 仍在 runtime: {active}"

        store.update_lorebook("gb", {"enabled": True})
        assert retrieve("door") == {"A"}, "重新启用必须恢复"
    finally:
        store.close()


def test_book_settings_mutation_bumps_the_monotonic_revision(tmp_path):
    """每次 Book 修改都 bump revision：检索字段让缓存失效，展示字段也会改变
    Book 的内容状态（revision 同时是导入预览的 state token）。"""

    store = _store(tmp_path)
    try:
        for field, value in (
            ("enabled", False), ("enabled", True),
            ("scan_depth", 9), ("token_budget", 4321), ("recursive_scanning", True),
            ("settings_json", {"fuzzy_enabled": True}),
            ("name", "Renamed"), ("description", "Edited"),
        ):
            before = store.get_lorebook("gb")["revision"]
            assert store.update_lorebook("gb", {field: value}) is True
            after = store.get_lorebook("gb")["revision"]
            assert after == before + 1, f"{field} 未 bump revision"

    finally:
        store.close()


def test_same_second_recursive_scanning_change_is_used_by_the_next_retrieve(tmp_path):
    store = _store(tmp_path)
    try:
        store.add_entry({
            "id": "A", "book_id": "gb", "name": "A",
            "keywords": ["door"], "content": "the door hides a sigil",
        })
        store.add_entry({
            "id": "B", "book_id": "gb", "name": "B",
            "keywords": ["sigil"], "content": "deep vault",
        })
        retrieve = _Session(store)
        assert retrieve("door") == {"A"}, "recursive_scanning 关闭时不该展开"

        # 同一秒内修改：只有 monotonic revision 能让缓存失效。
        store.update_lorebook("gb", {"recursive_scanning": True})
        assert retrieve("door") == {"A", "B"}, "同秒修改 recursive_scanning 未被采用"
    finally:
        store.close()


def test_same_second_token_budget_change_is_used_by_the_next_retrieve(tmp_path):
    store = _store(tmp_path)
    try:
        store.add_entry({
            "id": "long", "book_id": "gb", "name": "L",
            "keywords": ["tide"], "content": "x" * 2000,
        })
        retrieve = _Session(store)
        store.update_lorebook("gb", {"token_budget": 10})
        assert retrieve("tide") == set(), "超预算条目应被裁掉"

        store.update_lorebook("gb", {"token_budget": 100000})
        assert retrieve("tide") == {"long"}, "同秒放宽 budget 未被采用"
    finally:
        store.close()


def test_same_second_scan_depth_change_reaches_the_resolver(tmp_path):
    """scan_depth 由 BookRef annotation 带入 matcher：同一秒修改必须可见。"""

    store = _store(tmp_path)
    try:
        _Session(store)("door")  # 先建立缓存
        store.update_lorebook("gb", {"scan_depth": 7})
        refs = {r.book_id: r for r in resolve_active_books(_instance(store), "gm")}
        assert refs["gb"].scan_depth == 7, "解析出的 Book annotation 仍是旧值"
        assert refs["gb"].revision == store.get_lorebook("gb")["revision"]
    finally:
        store.close()


def test_disabled_book_is_not_a_candidate_for_any_scope(tmp_path):
    """停用是一个 runtime 决定，对不同作用域的 Book 同样生效。"""

    store = _store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        store.update_lorebook(primary, {"enabled": False})
        active = {r.book_id for r in resolve_active_books(_instance(store), "gm")}
        assert primary not in active, f"停用的主世界书仍在 runtime: {active}"

        # 反向确认：启用后它必须回来，说明不是「主世界书本就不可见」。
        store.update_lorebook(primary, {"enabled": True})
        active = {r.book_id for r in resolve_active_books(_instance(store), "gm")}
        assert primary in active
    finally:
        store.close()


# ---- §2 move_entry world_id compatibility projection -----------------------


def test_move_from_primary_world_book_clears_world_id_and_survives_cascade(tmp_path):
    store = _store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        store.create_lorebook({"id": "book:other", "name": "Other"})
        # primary world book 的条目按 canonical invariant 带 world_id。
        store.add_entry({
            "id": "e1", "book_id": primary, "world_id": WORLD,
            "name": "E", "content": "c", "keywords": ["k"],
        })
        assert store.get_entry("e1")["world_id"] == WORLD
        source_rev = store.get_lorebook(primary)["revision"]
        target_rev = store.get_lorebook("book:other")["revision"]

        assert store.move_entry(primary, "book:other", "e1") is True
        moved = store.get_entry("e1")
        assert moved["id"] == "e1", "canonical id 必须保持不变"
        assert moved["book_id"] == "book:other"
        assert moved["world_id"] is None, "独立 Book 的条目不得残留 world_id 投影"
        assert store.get_lorebook(primary)["revision"] == source_rev + 1
        assert store.get_lorebook("book:other")["revision"] == target_rev + 1

        # 真正的风险：旧兼容删除路径按 world_id 删，会误删已移走的条目。
        store.delete_world_cascade(WORLD)
        assert store.get_entry("e1") is not None, "移走的独立条目被世界级联误删"
    finally:
        store.close()


def test_move_into_primary_world_book_sets_world_id(tmp_path):
    store = _store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        store.add_entry({
            "id": "e1", "book_id": "gb", "name": "E", "content": "c", "keywords": ["k"],
        })
        assert store.get_entry("e1")["world_id"] is None

        assert store.move_entry("gb", primary, "e1") is True
        moved = store.get_entry("e1")
        assert moved["book_id"] == primary
        assert moved["world_id"] == WORLD, "移入主世界书必须建立兼容投影"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("book_id", "expected"),
    [("world:w1", "w1"), ("world:", None), ("gb", None), ("", None), ("book:other", None)],
)
def test_world_projection_helper_is_canonical(book_id, expected):
    assert LorebookStore.world_projection_for_book(book_id) == expected


# ---- §1 回归：主世界书停用不能被 legacy façade 复活 -------------------------


def test_disabled_primary_world_book_really_leaves_the_runtime(tmp_path):
    """停用主世界书是最常见的用法，不能被 `ensure_world` 回退路径复活。

    `ensure_lore_context` 在 resolver 返回空集合时会回落到 legacy world 读取；
    如果那个回落不区分「store 根本没有 binding」与「有 binding 但都被停用」，
    停用的主世界书就会被重新加载 —— 这正是本 blocker 的隐藏半边。
    """

    store = _store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        store.add_entry({
            "id": "pw", "book_id": primary, "world_id": WORLD,
            "name": "PW", "keywords": ["harbor"], "content": "primary lore",
        })
        store.add_entry({
            "id": "gl", "book_id": "gb", "name": "GL",
            "keywords": ["harbor"], "content": "global lore",
        })
        retrieve = _Session(store)
        assert retrieve("harbor") == {"pw", "gl"}

        store.update_lorebook(primary, {"enabled": False})
        assert retrieve("harbor") == {"gl"}, "停用的主世界书被复活了"

        store.update_lorebook(primary, {"enabled": True})
        assert retrieve("harbor") == {"pw", "gl"}

        store.update_lorebook("gb", {"enabled": False})
        assert retrieve("harbor") == {"pw"}

        store.update_lorebook(primary, {"enabled": False})
        assert retrieve("harbor") == set(), "全部停用后不得返回任何候选"

        store.update_lorebook(primary, {"enabled": True})
        store.update_lorebook("gb", {"enabled": True})
        assert retrieve("harbor") == {"pw", "gl"}
    finally:
        store.close()


def test_store_without_bindings_still_uses_the_legacy_world_facade(tmp_path):
    """反向保护：真正没有 binding 的 legacy store 仍要走 world façade。

    否则「区分两种空集合」的修复会把老库直接读空。
    """

    store = _store(tmp_path)
    try:
        primary = store.primary_world_book_id(WORLD)
        store.add_entry({
            "id": "pw", "book_id": primary, "world_id": WORLD,
            "name": "PW", "keywords": ["harbor"], "content": "primary lore",
        })
        # 主世界 binding 按设计不可删除，所以这里直接在表上清空，模拟一个
        # 真正的 pre-binding legacy store（这正是回落分支存在的理由）。
        store._conn.execute("DELETE FROM lorebook_bindings")
        store._conn.commit()
        assert store.list_bindings() == []

        retrieve = _Session(store)
        assert retrieve("harbor") == {"pw"}, "无 binding 的 legacy store 不该被读空"
    finally:
        store.close()
