"""PR #398 final-hardening regressions: activation ordering, visibility fail-closed,
ST selective semantics, semantic/lexical pipeline parity, group competition,
book-entry ownership isolation and vector_activation defaults.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.modules import narrative_notes
from src.lorebook.activation import normalize_selective_logic
from src.lorebook.importer import commit_lorebook_import, preview_lorebook_import
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.retrieval import LoreRetriever
from src.lorebook.store import LorebookStore
from src.webui.api import WebAPI


def _build(entries: list[dict], *, rng=None) -> KeywordMatcher:
    matcher = KeywordMatcher(rng=rng)
    defaults = {"enabled": True, "tier": "background", "order": 100, "probability": 100}
    matcher.build([dict(defaults, **entry) for entry in entries])
    return matcher


def _gm(entry: dict) -> bool:
    return True


def _player(entry: dict) -> bool:
    return "*" in (entry.get("visible_to") or [])


# ---- P1: visibility must fail closed BEFORE recursion -----------------------


def test_hidden_parent_cannot_propagate_to_public_child() -> None:
    """GM 看到完整递归链；玩家视角下 hidden parent 不得触发 public child。"""

    matcher = _build([
        {"id": "secret-parent", "keywords": ["dragon"],
         "content": "The dragon guards the amulet.", "visible_to": []},
        {"id": "public-child", "keywords": ["amulet"], "content": "public", "visible_to": ["*"]},
    ])

    gm = {row["id"] for row in matcher.match_with_recursive("dragon", is_visible=_gm)}
    assert gm == {"secret-parent", "public-child"}

    player = {
        row["id"] for row in matcher.match_with_recursive("dragon", is_visible=_player)
    }
    assert player == set()


def test_hidden_child_is_not_reached_through_recursion() -> None:
    """递归中间节点不可见时，其下游也不得进入玩家结果。"""

    matcher = _build([
        {"id": "seed", "keywords": ["door"], "content": "sigil", "visible_to": ["*"]},
        {"id": "hidden-mid", "keywords": ["sigil"], "content": "vault", "visible_to": []},
        {"id": "public-leaf", "keywords": ["vault"], "content": "leaf", "visible_to": ["*"]},
    ])

    gm = {row["id"] for row in matcher.match_with_recursive("door", is_visible=_gm)}
    assert gm == {"seed", "hidden-mid", "public-leaf"}
    player = {row["id"] for row in matcher.match_with_recursive("door", is_visible=_player)}
    assert player == {"seed"}


def test_hidden_entry_never_mutates_timed_state() -> None:
    """不可见条目连 timed activation state 都不允许写。"""

    matcher = _build([
        {"id": "hidden", "keywords": ["door"], "content": "", "sticky": 3, "visible_to": []},
    ])
    timed_state: dict = {}
    matcher.match_with_recursive("door", timed_state=timed_state, is_visible=_player)
    assert timed_state == {}


# ---- P1: rejected entries have no recursion / timed side effects ------------


def test_probability_rejected_entry_does_not_recurse_or_write_timed_state() -> None:
    matcher = _build([
        {"id": "root", "keywords": ["door"], "content": "sigil", "probability": 0},
        {"id": "child", "keywords": ["sigil"], "content": "deep"},
        {"id": "sticky-root", "keywords": ["gate"], "content": "", "sticky": 3, "probability": 0},
    ])

    timed_state: dict = {}
    assert {row["id"] for row in matcher.match_with_recursive("door gate", timed_state=timed_state)} == set()
    assert timed_state == {}


def test_group_loser_does_not_recurse_or_write_timed_state() -> None:
    matcher = _build([
        {"id": "winner", "keywords": ["door"], "content": "", "group": "g", "group_weight": 10},
        {"id": "loser", "keywords": ["door"], "content": "sigil", "group": "g",
         "group_weight": 1, "sticky": 3},
        {"id": "child", "keywords": ["sigil"], "content": "deep"},
    ], rng=lambda: 0.99)

    timed_state: dict = {}
    ids = {row["id"] for row in matcher.match_with_recursive("door", timed_state=timed_state)}
    assert ids == {"winner"}
    assert timed_state == {}


# ---- P1: SillyTavern optional filter semantics ------------------------------


@pytest.mark.parametrize("logic", ["and_any", "and_all", "not_any", "not_all"])
def test_st_selective_logic_never_activates_without_primary(logic: str) -> None:
    """primary 没命中时，四种 selective logic 都不得激活（旧实现会误激活 not_any）。"""

    matcher = _build([
        {"id": "e", "keywords": ["Wizard"], "secondary_keys": ["Tower"],
         "selective_logic": logic, "_lorebook_fuzzy_enabled": False},
    ])
    assert not any(row["id"] == "e" for row in matcher.match("Nothing relevant here"))


@pytest.mark.parametrize(
    "logic,secondary,expected",
    [
        ("and_any", "Tower", True),
        ("and_any", "Forest", False),
        ("and_all", "Tower Castle", True),
        ("and_all", "Tower", False),
        ("not_any", "Forest", True),
        ("not_any", "Tower", False),
        ("not_all", "Tower", True),
        ("not_all", "Tower Castle", False),
    ],
)
def test_st_selective_logic_truth_table(logic: str, secondary: str, expected: bool) -> None:
    """primary 命中为前提，secondary 只是额外过滤器（fuzzy 已关闭以隔离语义）。"""

    matcher = _build([
        {"id": "e", "keywords": ["Wizard"], "secondary_keys": ["Tower", "Castle"],
         "selective_logic": logic, "_lorebook_fuzzy_enabled": False},
    ])
    assert any(row["id"] == "e" for row in matcher.match(f"Wizard at {secondary}")) is expected


def test_st_numeric_selective_logic_mapping_matches_sillytavern() -> None:
    """SillyTavern world_info_logic: 0=AND_ANY 1=NOT_ALL 2=NOT_ANY 3=AND_ALL."""

    assert normalize_selective_logic(0) == "and_any"
    assert normalize_selective_logic(1) == "not_all"
    assert normalize_selective_logic(2) == "not_any"
    assert normalize_selective_logic(3) == "and_all"


def test_legacy_primary_match_mode_stays_separate_from_selective_logic() -> None:
    """legacy match_mode 仍只约束 primary keywords，不受 secondary 影响。"""

    matcher = _build([
        {"id": "legacy", "keywords": ["安全区"], "match_mode": "not_any", "content": "危险"},
    ])
    assert any(row["id"] == "legacy" for row in matcher.match("走进危险地带"))
    assert not any(row["id"] == "legacy" for row in matcher.match("走进安全区"))


def test_st_import_keeps_selective_logic_out_of_match_mode() -> None:
    """ST selectiveLogic=2 (NOT_ANY) 不得再被折叠成 legacy match_mode='not_all'。"""

    payload = {"name": "ST", "entries": [
        {"uid": 1, "key": ["Wizard"], "keysecondary": ["Tower"], "selectiveLogic": 2, "content": "x"},
    ]}
    draft = preview_lorebook_import(payload)["book"]
    entry = draft.entries[0]
    assert entry.match_mode == "any"
    assert entry.selective_logic == "not_any"


def test_st_position_anchor_is_never_executed_as_prompt_slot(tmp_path) -> None:
    """ST position=4 (atDepth) 没有 DiceFrame 等价 slot：保留 raw、告警、不执行。"""

    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world("w", "World")
    try:
        payload = {"name": "ST", "entries": [
            {"uid": 1, "key": ["dragon"], "content": "x", "position": 4, "depth": 2},
        ]}
        preview = preview_lorebook_import(payload)
        assert preview["book"].entries[0].prompt_slot == ""
        assert any("position anchor" in warning for warning in preview["warnings"])

        commit_lorebook_import(store, preview["book"], {"scope_kind": "world", "scope_id": "w"},
                               book_id="book:st")
        row = store.list_book_entries("book:st")[0]
        assert row["prompt_slot"] == ""
        assert row["extensions"]["_preserved_position"] == 4
        # A hand-authored canonical slot stays executable.
        canonical = preview_lorebook_import({"name": "ST2", "entries": [
            {"uid": 2, "key": ["door"], "content": "y", "position": "post_history"},
        ]})
        assert canonical["book"].entries[0].prompt_slot == "post_history"
    finally:
        store.close()


def test_st_unknown_fields_are_preserved_with_warning(tmp_path) -> None:
    """ST 独有字段必须 preserve + 预览告警，不能静默丢弃。"""

    payload = {"name": "ST", "entries": [
        {"uid": 1, "key": ["dragon"], "content": "x", "vectorized": True,
         "automationId": "speak", "excludeRecursion": True, "preventRecursion": True,
         "useGroupScoring": "score"},
    ]}
    draft = preview_lorebook_import(payload)["book"]
    entry = draft.entries[0]
    assert any("vectorized" in warning and "automationId" in warning for warning in draft.warnings)
    # ST 的原生 recursion 字段名也要被识别成 canonical 语义。
    assert entry.recursion_flags == {"non_recursable": True, "prevent_further_recursion": True}
    assert entry.group_scoring == "score"
    assert entry.extensions["vectorized"] is True
    assert entry.extensions["automationId"] == "speak"


def test_import_transaction_rolls_back_half_imported_book(tmp_path) -> None:
    """book + binding + entries 必须是单个事务：中途失败不得留下半本书。"""

    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world("w", "World")
    try:
        draft = preview_lorebook_import({"spec": "lorebook_v3", "data": {"lorebook": {
            "name": "Broken", "entries": [
                {"id": "a", "name": "a", "content": "a", "keys": ["a"]},
                {"id": "b", "name": "b", "content": "b", "keys": ["b"]},
            ],
        }}})["book"]

        class _Boom(Exception):
            pass

        original = store.add_entry
        calls = {"n": 0}

        def _fail_second(entry):
            calls["n"] += 1
            if calls["n"] == 2:
                raise _Boom("second entry failed")
            return original(entry)

        store.add_entry = _fail_second  # type: ignore[method-assign]
        with pytest.raises(_Boom):
            commit_lorebook_import(store, draft, {"scope_kind": "world", "scope_id": "w"},
                                   book_id="book:broken")
        store.add_entry = original  # type: ignore[method-assign]

        assert store.get_lorebook("book:broken") is None
        assert [b["id"] for b in store.list_bindings() if b["book_id"] == "book:broken"] == []
        assert store.list_book_entries("book:broken") == []
    finally:
        store.close()


def test_preserved_extensions_round_trip_through_v3_export(tmp_path) -> None:
    """import → canonical DB → export lorebook_v3 → reimport 保留未知扩展。"""

    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world("w", "World")
    try:
        draft = preview_lorebook_import({"spec": "lorebook_v3", "data": {"lorebook": {
            "name": "Rt", "custom_vendor_field": {"keep": True},
            "entries": [{"id": "e1", "name": "e", "content": "c", "keys": ["k"],
                         "unknown_entry_field": 7}],
        }}})["book"]
        assert draft.preserved_extensions["raw"].get("custom_vendor_field")
        commit_lorebook_import(store, draft, {"scope_kind": "world", "scope_id": "w"},
                               book_id="book:rt")

        # Round trip: canonical DB -> v3 export -> reimport.
        from src.lorebook.exporter import export_lorebook_v3
        exported = export_lorebook_v3(store, "book:rt")
        assert exported["data"]["lorebook"]["preserved_extensions"]["raw"]["custom_vendor_field"] == {"keep": True}
        reimported = preview_lorebook_import(exported)["book"]
        assert reimported.preserved_extensions["raw"]["custom_vendor_field"] == {"keep": True}
        assert reimported.entries[0].extensions["_external_raw"]["unknown_entry_field"] == 7

        # Book settings still own the preserved payload for the native backup.
        assert store.get_lorebook("book:rt")["settings"]["preserved_extensions"]["raw"]["custom_vendor_field"] == {"keep": True}
    finally:
        store.close()


# ---- P1: group scoring / prioritize inclusion / weighted RNG ----------------


def test_group_scoring_keeps_only_the_highest_scored_subset() -> None:
    matcher = _build([
        {"id": "one-key", "keywords": ["door"], "content": "", "group": "g",
         "group_scoring": "score"},
        {"id": "two-keys", "keywords": ["door", "gate"], "content": "", "group": "g",
         "group_weight": 50, "group_scoring": "score"},
    ], rng=lambda: 0.99)

    ids = {row["id"] for row in matcher.match("door and gate")}
    assert ids == {"two-keys"}


def test_group_scoring_secondary_keys_contribute_only_for_positive_logic() -> None:
    """AND ANY 让 secondary 计分；NOT ANY 的 secondary 不计分（SillyTavern getScore）。"""

    positive = _build([
        {"id": "with-secondary", "keywords": ["door"], "secondary_keys": ["sigil"],
         "selective_logic": "and_any", "content": "", "group": "g", "group_scoring": "score"},
        {"id": "primary-only", "keywords": ["door"], "content": "", "group": "g",
         "group_scoring": "score"},
    ], rng=lambda: 0.99)
    assert {row["id"] for row in positive.match("door sigil")} == {"with-secondary"}

    negative = _build([
        {"id": "with-secondary", "keywords": ["door"], "secondary_keys": ["sigil"],
         "selective_logic": "not_any", "content": "", "group": "g", "group_scoring": "score"},
        {"id": "primary-only", "keywords": ["door", "gate"], "content": "", "group": "g",
         "group_scoring": "score"},
    ], rng=lambda: 0.99)
    assert {row["id"] for row in negative.match("door gate")} == {"primary-only"}


def test_prioritize_inclusion_follows_the_higher_insertion_order() -> None:
    """Prioritize Inclusion 依赖较高的 insertion order，因此 order 必须降序。"""

    matcher = _build([
        {"id": "low-order", "keywords": ["door"], "group": "g", "order": 10,
         "prioritize_inclusion": True},
        {"id": "high-order", "keywords": ["door"], "group": "g", "order": 900,
         "prioritize_inclusion": True},
    ], rng=lambda: 0.99)
    assert {row["id"] for row in matcher.match("door")} == {"high-order"}


def test_weighted_group_pick_is_deterministic_under_injected_rng() -> None:
    """A roll near 1.0 must land on the lightest member, proving the weighting."""

    matcher = _build([
        {"id": "weak", "keywords": ["door"], "group": "g", "group_weight": 1},
        {"id": "strong", "keywords": ["door"], "group": "g", "group_weight": 10},
    ], rng=lambda: 0.9999)
    assert {row["id"] for row in matcher.match("door")} == {"weak"}


# ---- P1: semantic candidates obey the same activation pipeline --------------


def _semantic_store(tmp_path: Path, entries: list[dict], *, recursive: bool = False) -> LorebookStore:
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_world("w", "World")
    if recursive:
        store.ensure_primary_world_book("w")
        store.update_lorebook("world:w", {"recursive_scanning": True})
    for entry in entries:
        row = {**entry, "world_id": "w"}
        row.setdefault("name", row.get("id", "entry"))
        store.add_entry(row)
    return store


def _instance(store: LorebookStore, timed_state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        world_id="w", language="zh-CN", modules={"narrative_notes": narrative_notes.fresh()}, npcs={}, players={},
        world_state={},
        # An empty dict must still be handed through by identity, otherwise the
        # retriever mutates a different object than the one the test inspects.
        lorebook_timed_state=timed_state if timed_state is not None else {},
        lorebook_store=store,
        action_actor_uids=[],
    )


def _player_instance(store: LorebookStore, timed_state: dict | None = None) -> SimpleNamespace:
    instance = _instance(store, timed_state)
    instance.players = {"p1": SimpleNamespace(user_id="p1", character_name="Hero")}
    return instance


def _retrieve_player(retriever: LoreRetriever, instance: SimpleNamespace, text: str) -> list[dict]:
    return asyncio.run(retriever.retrieve(
        instance, text, viewer_is_gm=False, viewer_uid="p1", viewer_name="Hero",
    ))


def _fake_client(query_vector: list[float]):
    """Every entry embeds to the query vector, so cosine similarity is always 1.0.

    These tests target the activation gates (probability / group / visibility /
    timed), not the vector math, so the fake deliberately ignores the text.
    """

    class _Client:
        model = "fake-embed"
        base_url = "http://fake.local/v1"
        max_input_chars = 500

        async def embed(self, text: str):
            return list(query_vector)

        async def embed_batch(self, texts: list[str]):
            return [list(query_vector) for _ in texts]

    return _Client()


def test_semantic_only_candidate_is_activatable(tmp_path) -> None:
    """vector_only 条目不能被关键词通道发现，但语义通道命中后应正常激活。"""

    entry = {"id": "sem", "keywords": ["nothing-matches-this"], "content": "semantic body",
             "vector_activation": "vector_only"}
    store = _semantic_store(tmp_path, [entry])
    try:
        client = _fake_client([1.0, 0.0])
        retriever = LoreRetriever(KeywordMatcher(), store=store, embedding_client_provider=lambda: client)
        hits = asyncio.run(retriever.retrieve(_instance(store), "完全不同的提问文本"))
        assert [row["id"] for row in hits] == ["sem"]
    finally:
        store.close()


def test_semantic_only_probability_zero_is_rejected(tmp_path) -> None:
    entry = {"id": "sem", "keywords": ["nothing-matches-this"], "content": "semantic body",
             "vector_activation": "vector_only", "probability": 0}
    store = _semantic_store(tmp_path, [entry])
    try:
        client = _fake_client([1.0, 0.0])
        retriever = LoreRetriever(KeywordMatcher(), store=store, embedding_client_provider=lambda: client)
        hits = asyncio.run(retriever.retrieve(_instance(store), "完全不同的提问文本"))
        assert hits == []
    finally:
        store.close()


def test_semantic_candidate_loses_group_competition(tmp_path) -> None:
    """semantic-only 候选必须和 lexical 候选在同一 inclusion group 里竞争。"""

    entries = [
        {"id": "kw-winner", "keywords": ["clue"], "content": "keyword body",
         "group": "g", "group_weight": 10},
        {"id": "sem-loser", "keywords": ["nothing-matches-this"], "content": "semantic body",
         "vector_activation": "vector_only", "group": "g", "group_weight": 1},
    ]
    store = _semantic_store(tmp_path, entries)
    try:
        client = _fake_client([1.0, 0.0])
        retriever = LoreRetriever(
            KeywordMatcher(rng=lambda: 0.5), store=store,
            embedding_client_provider=lambda: client,
        )
        hits = asyncio.run(retriever.retrieve(_instance(store), "clue"))
        assert [row["id"] for row in hits] == ["kw-winner"]
    finally:
        store.close()


def test_semantic_only_hidden_entry_is_fail_closed(tmp_path) -> None:
    entry = {"id": "sem", "keywords": ["nothing-matches-this"], "content": "semantic body",
             "vector_activation": "vector_only", "visible_to": []}
    store = _semantic_store(tmp_path, [entry])
    try:
        client = _fake_client([1.0, 0.0])
        retriever = LoreRetriever(KeywordMatcher(), store=store, embedding_client_provider=lambda: client)
        # GM 语义发现允许包含 hidden（供诊断），但玩家视角必须 fail closed。
        assert asyncio.run(retriever.retrieve(_instance(store), "完全不同的提问文本"))
        hits = _retrieve_player(retriever, _player_instance(store), "完全不同的提问文本")
        assert hits == []
    finally:
        store.close()


def test_semantic_only_entry_blocked_by_cooldown(tmp_path) -> None:
    entry = {"id": "sem", "keywords": ["nothing-matches-this"], "content": "semantic body",
             "vector_activation": "vector_only"}
    store = _semantic_store(tmp_path, [entry])
    try:
        client = _fake_client([1.0, 0.0])
        retriever = LoreRetriever(KeywordMatcher(), store=store, embedding_client_provider=lambda: client)
        timed = {"sem": {"cooldown_remaining": 2}}
        hits = asyncio.run(retriever.retrieve(_instance(store, timed), "完全不同的提问文本"))
        assert hits == []
    finally:
        store.close()


def test_semantic_only_activation_does_not_write_timed_state(tmp_path) -> None:
    """计时器归 keyword authority：纯 semantic 命中不得写 sticky/cooldown/delay。"""

    entry = {"id": "sem", "keywords": ["nothing-matches-this"], "content": "semantic body",
             "vector_activation": "vector_only", "sticky": 3, "cooldown": 2}
    store = _semantic_store(tmp_path, [entry])
    try:
        client = _fake_client([1.0, 0.0])
        retriever = LoreRetriever(KeywordMatcher(), store=store, embedding_client_provider=lambda: client)
        timed: dict = {}
        hits = asyncio.run(retriever.retrieve(_instance(store, timed), "完全不同的提问文本"))
        assert [row["id"] for row in hits] == ["sem"]
        assert timed == {}
    finally:
        store.close()


def test_keyword_channel_still_writes_timed_state(tmp_path) -> None:
    """同一组条目走 keyword 通道时，sticky/cooldown 照常写入（delay 不落计数器）。"""

    entry = {"id": "kw", "name": "kw", "world_id": "w", "keywords": ["clue"],
             "content": "keyword body", "sticky": 3}
    store = _semantic_store(tmp_path, [entry])
    try:
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        timed: dict = {}
        hits = asyncio.run(retriever.retrieve(_instance(store, timed), "clue"))
        assert [row["id"] for row in hits] == ["kw"]
        assert timed == {"kw": {"sticky_remaining": 3, "pending_cooldown": 0, "activated_tick": 0}}
    finally:
        store.close()


def test_retriever_visibility_is_fail_closed_before_recursion(tmp_path) -> None:
    """端到端：玩家视角下 hidden parent 的内容不得触发 public child。"""

    store = _semantic_store(
        tmp_path,
        [
            {"id": "secret-parent", "name": "Secret", "keywords": ["dragon"],
             "content": "The dragon guards the amulet.", "visible_to": []},
            {"id": "public-child", "name": "Public", "keywords": ["amulet"],
             "content": "public", "visible_to": ["*"]},
        ],
        recursive=True,
    )
    try:
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        gm = asyncio.run(retriever.retrieve(_instance(store), "dragon"))
        assert {row["id"] for row in gm} == {"secret-parent", "public-child"}

        player_instance = _instance(store)
        player_instance.players = {"p1": SimpleNamespace(user_id="p1", character_name="Hero")}
        player = asyncio.run(retriever.retrieve(
            player_instance, "dragon", viewer_is_gm=False, viewer_uid="p1", viewer_name="Hero",
        ))
        assert player == []
    finally:
        store.close()


def test_player_trace_reports_real_keyword_reasons(tmp_path) -> None:
    entry = {"id": "e", "world_id": "w", "name": "E", "keywords": ["dragon", "unused"],
             "content": "body", "visible_to": ["*"]}
    store = _semantic_store(tmp_path, [entry])
    try:
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        instance = _instance(store)
        asyncio.run(retriever.retrieve(instance, "dragon"))
        trace = {row["entry_id"]: row for row in retriever.last_activation_trace}["e"]
        assert trace["matched_keys"] == ["dragon"]
        assert trace["primary_result"] is True
        assert trace["secondary_result"] is None
        assert trace["candidate_sources"] == ["keyword"]
    finally:
        store.close()


# ---- P1: book entry CRUD ownership isolation --------------------------------


def _two_book_store(tmp_path: Path) -> LorebookStore:
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    store.create_lorebook({"id": "book-a", "name": "A"})
    store.create_lorebook({"id": "book-b", "name": "B"})
    store.add_entry({"id": "entry-b", "book_id": "book-b", "name": "B entry",
                     "keywords": ["b"], "content": "original"})
    return store


def test_store_rejects_cross_book_update_and_delete(tmp_path) -> None:
    store = _two_book_store(tmp_path)
    try:
        assert store.get_book_entry("book-a", "entry-b") is None
        assert store.get_book_entry("book-b", "entry-b")["id"] == "entry-b"

        assert store.update_book_entry("book-a", "entry-b", {"name": "hijacked"}) is False
        assert store.delete_book_entry("book-a", "entry-b") is False

        intact = store.get_entry("entry-b")
        assert intact["book_id"] == "book-b"
        assert intact["name"] == "B entry"
        assert intact["content"] == "original"

        assert store.update_book_entry("book-b", "entry-b", {"name": "renamed"}) is True
        assert store.get_entry("entry-b")["name"] == "renamed"
        assert store.delete_book_entry("book-b", "entry-b") is True
        assert store.get_entry("entry-b") is None
    finally:
        store.close()


def test_api_entry_routes_are_ownership_isolated(tmp_path) -> None:
    store = _two_book_store(tmp_path)
    try:
        api = WebAPI.__new__(WebAPI)
        api._lore = store

        hijack = api.save_lorebook_entry("book-a", {"id": "entry-b", "name": "hijacked"})
        assert hijack["ok"] is False and hijack["error_code"] == "entry_book_mismatch"
        assert store.get_entry("entry-b")["name"] == "B entry"

        removed = api.delete_lorebook_entry("book-a", "entry-b")
        assert removed["ok"] is False and removed["error_code"] == "entry_not_found"
        assert store.get_entry("entry-b") is not None

        created = api.save_lorebook_entry("book-a", {"name": "A entry", "content": "x"})
        assert created["ok"] is True and created["entry"]["book_id"] == "book-a"
    finally:
        store.close()


def test_import_external_entry_id_cannot_overwrite_another_book(tmp_path) -> None:
    store = _two_book_store(tmp_path)
    try:
        payload = {"name": "Import", "entries": [
            {"uid": "entry-b", "key": ["x"], "content": "hostile"},
        ]}
        draft = preview_lorebook_import(payload)["book"]
        commit_lorebook_import(store, draft, {"id": "binding:imp", "scope_kind": "global"},
                               book_id="book-imp", )
        hijacked = store.get_entry("entry-b")
        assert hijacked["book_id"] == "book-b"
        assert hijacked["content"] == "original"
        imported = store.list_book_entries("book-imp")
        assert len(imported) == 1 and imported[0]["book_id"] == "book-imp"
    finally:
        store.close()


# ---- P1: vector_activation defaults ----------------------------------------


def test_store_defaults_new_entries_to_hybrid(tmp_path) -> None:
    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    try:
        store.create_world("w", "World")
        store.add_entry({"id": "implicit", "world_id": "w", "name": "I", "content": "c"})
        assert store.get_entry("implicit")["vector_activation"] == "hybrid"
        store.add_entry({"id": "explicit", "world_id": "w", "name": "E", "content": "c",
                         "vector_activation": "off"})
        assert store.get_entry("explicit")["vector_activation"] == "off"
    finally:
        store.close()


def test_st_and_v3_imports_default_to_hybrid() -> None:
    st = preview_lorebook_import({"name": "ST", "entries": [
        {"uid": 1, "key": ["a"], "content": "x"},
    ]})["book"]
    assert st.entries[0].vector_activation == "hybrid"

    v3 = preview_lorebook_import({
        "spec": "lorebook_v3",
        "data": {"lorebook": {"name": "V3", "entries": [{"id": "e", "keys": ["a"], "content": "x"}]}},
    })["book"]
    assert v3.entries[0].vector_activation == "hybrid"

    narrowed = preview_lorebook_import({"name": "ST", "entries": [
        {"uid": 1, "key": ["a"], "content": "x", "vectorActivation": "vector_only"},
    ]})["book"]
    assert narrowed.entries[0].vector_activation == "vector_only"
