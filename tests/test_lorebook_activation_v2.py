from types import SimpleNamespace

from src.engine.modules import narrative_notes
from src.lorebook.activation import evaluate_probability, eligible_for_recursion
from src.lorebook.budget import apply_token_budget
from src.lorebook.resolver import resolve_active_books
from src.lorebook.trace import ActivationTrace
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.retrieval import LoreRetriever
from src.engine.game_instance import GameInstance
from src.engine.modules import lorebook_runtime


def test_probability_trace_is_injected_and_deterministic():
    accepted, trace = evaluate_probability({"probability": 50}, rng=lambda: 0.2)
    assert accepted and trace == {"configured": 50, "roll": 21, "accepted": True}


def test_budget_keeps_constant_and_stable_ids():
    rows, omitted = apply_token_budget([{"id": "b", "content": "x"}, {"id": "a", "content": "x", "is_constant": True}], 1, estimate=lambda _: 1)
    assert [row["id"] for row in rows] == ["a"] and omitted == ["b"]


def test_resolver_filters_private_books_for_party():
    class Store:
        def list_bindings(self):
            return [{"id": "w", "book_id": "world:w", "scope_kind": "world", "scope_id": "w", "enabled": True}, {"id": "p", "book_id": "book:p", "scope_kind": "character", "scope_id": "u", "enabled": True}]
    refs = resolve_active_books(SimpleNamespace(lorebook_store=Store(), world_id="w"), "party", "u")
    assert [ref.book_id for ref in refs] == ["world:w"]


def test_trace_emits_no_row_at_all_for_a_hidden_entry_in_safe_view():
    """safe view 不得为 hidden 条目产出任何一行。

    此前返回的是一个 redacted 占位行，但「每个 hidden 条目一行」本身就泄漏了
    hidden 数量与其分类——visibility 契约禁止的程度和泄漏 id / name 相同。
    """

    trace = ActivationTrace("secret", visibility="hidden", reason_code="visibility")
    assert trace.to_dict(safe=True) == {}
    assert trace.to_dict(safe=False)["entry_id"] == "secret"


def test_retriever_loads_world_and_global_books(tmp_path):
    from src.lorebook.store import LorebookStore

    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    try:
        store.create_world("w", "World")
        store.add_entry({"id": "world-entry", "world_id": "w", "name": "World clue", "keywords": ["clue"], "content": "world"})
        store.create_lorebook({"id": "global-book", "name": "Global"})
        store.bind_lorebook({"id": "global-binding", "book_id": "global-book", "scope_kind": "global", "scope_id": ""})
        store.add_entry({"id": "global-entry", "book_id": "global-book", "name": "Global clue", "keywords": ["clue"], "content": "global"})
        instance = SimpleNamespace(world_id="w", language="zh-CN", npcs={}, players={}, world_state={}, modules={"narrative_notes": narrative_notes.fresh(), "lorebook_runtime": {**lorebook_runtime.fresh(), "timers": {}}}, lorebook_store=store)
        retriever = LoreRetriever(KeywordMatcher(), store=store)
        import asyncio
        hits = asyncio.run(retriever.retrieve(instance, "clue"))
        assert {entry["id"] for entry in hits} == {"world-entry", "global-entry"}
    finally:
        store.close()


def test_matcher_secondary_keys_and_word_case_controls():
    matcher = KeywordMatcher()
    matcher.build([
        {"id": "secondary", "keywords": ["Wizard"], "secondary_keys": ["Tower"], "case_sensitive": True},
        {"id": "whole", "keywords": ["cat"], "match_whole_words": True},
        {"id": "regex", "keywords": [r"guard-\d+"], "use_regex": True},
    ])

    assert [row["id"] for row in matcher.match("Wizard at Tower") if row["id"] == "secondary"] == ["secondary"]
    assert not any(row["id"] == "secondary" for row in matcher.match("Wizard at tower"))
    assert not any(row["id"] == "whole" for row in matcher.match("catalog"))
    assert any(row["id"] == "whole" for row in matcher.match("a cat"))
    assert any(row["id"] == "regex" for row in matcher.match("guard-42"))


def test_matcher_recursive_scan_uses_entry_content_and_depth():
    matcher = KeywordMatcher()
    matcher.build([
        {"id": "seed", "keywords": ["door"], "content": "The door hides a sigil."},
        {"id": "child", "keywords": ["sigil"], "content": "The sigil names an ancient vault."},
        {"id": "grandchild", "keywords": ["vault"], "content": "deep"},
        {"id": "blocked", "keywords": ["vault"], "content": "blocked", "prevent_further_recursion": True},
    ])

    ids = {row["id"] for row in matcher.match_with_recursive("door")}
    assert {"seed", "child", "grandchild"}.issubset(ids)
    assert "blocked" in ids


def test_timed_state_is_migrated_at_game_save_load_boundary():
    instance = GameInstance(game_key=("web", "timers", "gm"))
    lorebook_runtime.replace_timers(instance, {"entry": {"status": "cooldown", "remaining": 2}})
    payload = instance.to_dict()
    assert payload["modules"]["lorebook_runtime"]["timers"]["entry"]["cooldown_remaining"] == 2
    restored = GameInstance.from_dict(payload)
    assert lorebook_runtime.timers(restored)["entry"]["cooldown_remaining"] == 2
    restored.update_lorebook_timed_state()
    assert lorebook_runtime.timers(restored)["entry"]["cooldown_remaining"] == 1


def test_fuzzy_matching_is_book_scoped_and_legacy_compatible():
    matcher = KeywordMatcher()
    matcher.build([
        {"id": "legacy", "keywords": ["castle"], "_lorebook_fuzzy_enabled": True},
        {"id": "v3", "keywords": ["castle"], "_lorebook_fuzzy_enabled": False},
    ])

    assert {row["id"] for row in matcher.match("castl")} == {"legacy"}


def test_resolver_defaults_fuzzy_by_book_source_kind(tmp_path):
    from types import SimpleNamespace

    from src.lorebook.resolver import resolve_active_books
    from src.lorebook.store import LorebookStore

    store = LorebookStore(tmp_path / "lore.db")
    store.open()
    try:
        store.create_world("w", "World")
        store.create_lorebook({"id": "v3", "name": "V3", "source_kind": "lorebook_v3"})
        store.bind_lorebook({"id": "b:v3", "book_id": "v3", "scope_kind": "world", "scope_id": "w"})
        store.create_lorebook({"id": "native", "name": "Native", "source_kind": "native", "settings": {"fuzzy_enabled": True}})
        store.bind_lorebook({"id": "b:native", "book_id": "native", "scope_kind": "world", "scope_id": "w"})
        refs = resolve_active_books(SimpleNamespace(world_id="w"), store=store)
        assert {ref.book_id: ref.fuzzy_enabled for ref in refs} == {"world:w": True, "v3": False, "native": True}
    finally:
        store.close()
