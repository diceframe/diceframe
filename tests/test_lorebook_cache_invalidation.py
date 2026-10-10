"""§2 — canonical entry CRUD 必须让下一次 retrieve 立即看到新状态。

Retriever 的 cache fingerprint 以前只看 ``book.updated_at``，而 entry 的
add/update/delete 只改 ``lorebook_entries``，于是：

    retrieve → 改 keyword → 第二次 retrieve 用旧 matcher → 改动不可见

这里覆盖施工单要求的全部 mutation 路径，并额外钉住两条容易回归的点：

* fingerprint 必须走 **单调 revision** 而不是 ``datetime('now')`` 秒级精度，
  同一秒内连续两次修改也要失效；
* legacy ``ensure_world`` 兼容路径同样不能 stale。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.modules import narrative_notes, progression_state
from src.lorebook.importer import commit_lorebook_import, preview_lorebook_import
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.retrieval import LoreRetriever
from src.lorebook.store import LorebookStore

WORLD_BOOK = "world:w"


def _store(tmp_path: Path, entries: list[dict] | None = None) -> LorebookStore:
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world("w", "World")
    store.ensure_primary_world_book("w")
    for entry in entries or []:
        row = {**entry, "world_id": "w"}
        row.setdefault("name", row.get("id", "entry"))
        store.add_entry(row)
    return store


def _instance(store: LorebookStore):
    return SimpleNamespace(
        world_id="w", language="zh-CN", npcs={}, players={},
        modules={"narrative_notes": narrative_notes.fresh(), "progression": {**progression_state.fresh(), "round": 0}},
        world_state={}, lorebook_timed_state={},
        lorebook_store=store, action_actor_uids=[],
    )


def _retriever(store: LorebookStore) -> LoreRetriever:
    return LoreRetriever(KeywordMatcher(), store=store)


def _hits(retriever: LoreRetriever, instance, text: str) -> set[str]:
    return {str(row.get("id")) for row in asyncio.run(retriever.retrieve(instance, text))}


def _revision(store: LorebookStore) -> int:
    return int(store.get_lorebook(WORLD_BOOK)["revision"])


# ---- 施工单要求的五条路径 ---------------------------------------------------


def test_keyword_edit_is_visible_on_the_next_retrieve(tmp_path: Path) -> None:
    """retrieve → 改 keyword → old 不命中 / new 命中。"""

    store = _store(tmp_path, [
        {"id": "e1", "keywords": ["harbor"], "content": "old body"},
    ])
    try:
        retriever = _retriever(store)
        instance = _instance(store)
        assert _hits(retriever, instance, "harbor") == {"e1"}
        assert _hits(retriever, instance, "lighthouse") == set()

        store.update_entry("e1", {"keywords": ["lighthouse"]})

        assert _hits(retriever, instance, "harbor") == set()
        assert _hits(retriever, instance, "lighthouse") == {"e1"}
    finally:
        store.close()


def test_disabling_an_entry_is_visible_on_the_next_retrieve(tmp_path: Path) -> None:
    """retrieve → disable → 下一次不命中。"""

    store = _store(tmp_path, [
        {"id": "e1", "keywords": ["harbor"], "content": "body"},
    ])
    try:
        retriever = _retriever(store)
        instance = _instance(store)
        assert _hits(retriever, instance, "harbor") == {"e1"}

        store.update_entry("e1", {"enabled": False})

        assert _hits(retriever, instance, "harbor") == set()
    finally:
        store.close()


def test_new_entry_is_visible_on_the_next_retrieve(tmp_path: Path) -> None:
    """retrieve → add → 下一次可命中。"""

    store = _store(tmp_path, [])
    try:
        retriever = _retriever(store)
        instance = _instance(store)
        assert _hits(retriever, instance, "harbor") == set()

        store.add_entry({
            "id": "e1", "world_id": "w", "name": "Harbor",
            "keywords": ["harbor"], "content": "body",
        })

        assert _hits(retriever, instance, "harbor") == {"e1"}
    finally:
        store.close()


def test_deleted_entry_disappears_on_the_next_retrieve(tmp_path: Path) -> None:
    """retrieve → delete → 下一次消失。"""

    store = _store(tmp_path, [
        {"id": "e1", "keywords": ["harbor"], "content": "body"},
    ])
    try:
        retriever = _retriever(store)
        instance = _instance(store)
        assert _hits(retriever, instance, "harbor") == {"e1"}

        store.delete_entry("e1")

        assert _hits(retriever, instance, "harbor") == set()
    finally:
        store.close()


def test_import_into_an_already_activated_book_is_immediately_visible(tmp_path: Path) -> None:
    """import 到已激活 existing Book → 下一次立即可见。"""

    store = _store(tmp_path, [
        {"id": "e1", "keywords": ["harbor"], "content": "body"},
    ])
    try:
        retriever = _retriever(store)
        instance = _instance(store)
        # 先让 Retriever 建立并缓存 matcher。
        assert _hits(retriever, instance, "harbor") == {"e1"}
        assert _hits(retriever, instance, "sigil") == set()

        preview = preview_lorebook_import({
            "entries": [{"uid": "st-1", "key": ["sigil"], "content": "imported"}],
        })
        commit_lorebook_import(store, preview["book"], None, book_id=WORLD_BOOK)

        assert _hits(retriever, instance, "sigil") == {
            str(row["id"]) for row in store.list_book_entries(WORLD_BOOK)
            if "sigil" in (row.get("keywords") or [])
        }
        assert _hits(retriever, instance, "sigil"), "import 后必须立即可见"
    finally:
        store.close()


# ---- 不依赖秒级精度 / 兼容路径 ---------------------------------------------


def test_revision_is_monotonic_and_does_not_depend_on_clock_resolution(tmp_path: Path) -> None:
    """同一秒内连续修改也必须产生新 revision（updated_at 精度不够）。"""

    store = _store(tmp_path, [
        {"id": "e1", "keywords": ["a"], "content": "body"},
    ])
    try:
        retriever = _retriever(store)
        instance = _instance(store)
        assert _hits(retriever, instance, "a") == {"e1"}
        first_scope = retriever._scope
        start = _revision(store)

        # 两次修改之间不 sleep：即使 updated_at 落在同一秒，fingerprint 也要变。
        store.update_entry("e1", {"keywords": ["b"]})
        store.update_entry("e1", {"keywords": ["c"]})

        assert _revision(store) == start + 2, "每次 entry mutation 都必须推进 revision"
        assert _hits(retriever, instance, "c") == {"e1"}
        assert retriever._scope != first_scope, "cache fingerprint 必须随 revision 变化"
    finally:
        store.close()


def test_every_entry_mutation_path_bumps_the_owning_book_revision(tmp_path: Path) -> None:
    """add / update / delete / 级联删除 都要推进所属 Book 的 revision。"""

    store = _store(tmp_path, [])
    try:
        baseline = _revision(store)

        store.add_entry({"id": "e1", "world_id": "w", "name": "E1", "content": "x"})
        assert _revision(store) == baseline + 1

        store.update_entry("e1", {"content": "y"})
        assert _revision(store) == baseline + 2

        # book 作用域 CRUD 走的是同一条路径，也必须推进。
        assert store.update_book_entry(WORLD_BOOK, "e1", {"content": "z"}) is True
        assert _revision(store) == baseline + 3

        assert store.delete_book_entry(WORLD_BOOK, "e1") is True
        assert _revision(store) == baseline + 4

        # 删除不属于该 book 的条目必须 fail closed，且不得推进 revision。
        assert store.delete_book_entry("book:other", "missing") is False
        assert _revision(store) == baseline + 4
    finally:
        store.close()


def test_legacy_world_scope_also_invalidates_on_entry_mutation(tmp_path: Path) -> None:
    """兼容 façade ``ensure_world`` 读的也是 primary world book，同样不能 stale。"""

    store = _store(tmp_path, [
        {"id": "e1", "keywords": ["harbor"], "content": "body"},
    ])
    try:
        retriever = _retriever(store)
        retriever.ensure_world("w", "zh-CN")
        assert {row["id"] for row in retriever.world_entries} == {"e1"}

        store.add_entry({
            "id": "e2", "world_id": "w", "name": "E2",
            "keywords": ["sigil"], "content": "body",
        })

        retriever.ensure_world("w", "zh-CN")
        assert {row["id"] for row in retriever.world_entries} == {"e1", "e2"}
    finally:
        store.close()


def test_revision_survives_reopen_and_defaults_to_zero(tmp_path: Path) -> None:
    """revision 是持久列：重开库后仍在，且迁移出来的旧库从 0 起算。"""

    path = tmp_path / "lore.db"
    store = LorebookStore(path)
    store.open()
    try:
        store.create_world("w", "World")
        store.ensure_primary_world_book("w")
        assert _revision(store) == 0
        store.add_entry({"id": "e1", "world_id": "w", "name": "E1", "content": "x"})
        assert _revision(store) == 1
    finally:
        store.close()

    reopened = LorebookStore(path)
    reopened.open()
    try:
        assert _revision(reopened) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("book_id", [WORLD_BOOK, "book:detached"])
def test_revision_bump_is_scoped_to_the_owning_book(tmp_path: Path, book_id: str) -> None:
    """只推进条目真正所属的 Book，不能误伤其它 Book。"""

    store = _store(tmp_path, [])
    try:
        store.create_lorebook({"id": "book:detached", "name": "Detached"})
        store.add_entry({"id": "e1", "book_id": book_id, "name": "E1", "content": "x"})
        owner = _revision(store) if book_id == WORLD_BOOK else int(
            store.get_lorebook("book:detached")["revision"]
        )
        other_book = "book:detached" if book_id == WORLD_BOOK else WORLD_BOOK
        other_before = int(store.get_lorebook(other_book)["revision"])

        store.update_entry("e1", {"content": "y"})

        owner_after = int(store.get_lorebook(book_id)["revision"])
        assert owner_after == owner + 1
        assert int(store.get_lorebook(other_book)["revision"]) == other_before
    finally:
        store.close()
