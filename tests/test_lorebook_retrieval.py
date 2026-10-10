"""通用 Hybrid Lore Retrieval 的行为契约测试（施工方案 PR A §38 A–M / Q / R）。

这里覆盖的是"检索层自己"的行为：锚点 query、关键词保留、语义召回与合并、缓存
（language / profile / content_hash 三种隔离）、无向量与 embedding 失败的降级、
玩家视角 fail-closed、三条路径共用同一实例。

方案里 N / O / P（unreliable 投影、WorldState > Lorebook、自由度文案）属于 PR B 的
Lore Prompt Projection，不在本文件。
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.modules import narrative_notes
from src.engine.world_state import fresh_world_state
from src.lorebook.matcher import KeywordMatcher
from src.lorebook.retrieval import (
    LoreRetriever,
    build_lore_retrieval_query,
    embedding_profile,
    lore_entry_content_hash,
    lore_entry_embedding_text,
    lore_query_location,
    present_npc_names,
)
from src.lorebook.store import LorebookStore
from src.engine.modules import lorebook_runtime

# ---- 测试替身 ---------------------------------------------------------------


def _entry(
    entry_id: str,
    name: str,
    *,
    keywords: list[str] | None = None,
    content: str = "",
    entry_type: str = "location",
    tier: str = "core",
    visible_to: list[str] | None = None,
) -> dict:
    return {
        "id": entry_id,
        "world_id": "w1",
        "name": name,
        "type": entry_type,
        "keywords": keywords if keywords is not None else [],
        "content": content or f"{name}的正文。",
        "tier": tier,
        "order": 100,
        "visible_to": visible_to if visible_to is not None else ["*"],
    }


def _world_state(location: str = "", *, visibility: str = "public", uid: str = "p1") -> dict:
    state = fresh_world_state()
    if location:
        state["facts"][f"actor:{uid}.location"] = {
            "value": location,
            "visibility": visibility,
            "source_round": 0,
            "updated_revision": 1,
        }
    return state


def _instance(**overrides):
    data = {
        "world_id": "w1",
        "language": "zh-CN",
        "scene": "",
        "npcs": {},
        "players": {"p1": {"character_name": "莱拉"}},
        "world_state": fresh_world_state(),
        "log": [],
    }
    timers = overrides.pop("lorebook_timed_state", {})
    data.update(overrides)
    scene = data.pop("scene")
    # Lorebook timers live in the lorebook_runtime module slot (no facade).
    instance = SimpleNamespace(**data, modules={"lorebook_runtime": {**lorebook_runtime.fresh(), "timers": timers}})
    narrative_notes.replace_scene(instance, scene)
    return instance


class _FakeEmbeddingClient:
    """按"送入 embedding 的文本"给向量，并可注入失败。"""

    def __init__(
        self,
        entry_vectors: dict[str, list[float]] | None = None,
        *,
        query_vector: list[float] | None = None,
        model: str = "fake-embed",
        base_url: str = "http://fake.local/v1",
        max_input_chars: int = 500,
        embed_fails: bool = False,
        batch_fails: bool = False,
    ) -> None:
        self.entry_vectors = entry_vectors or {}
        self.query_vector = list(query_vector if query_vector is not None else [1.0, 0.0])
        self.model = model
        self.base_url = base_url
        self.max_input_chars = max_input_chars
        self.embed_fails = embed_fails
        self.batch_fails = batch_fails
        self.embed_calls: list[str] = []
        self.batch_calls: list[list[str]] = []

    async def embed(self, text: str):
        self.embed_calls.append(text)
        if self.embed_fails:
            return None
        return list(self.query_vector)

    async def embed_batch(self, texts: list[str]):
        self.batch_calls.append(list(texts))
        if self.batch_fails:
            return None
        return [list(self.entry_vectors.get(text, [0.0, 0.0])) for text in texts]


class _RaisingEmbeddingClient(_FakeEmbeddingClient):
    async def embed(self, text: str):
        raise RuntimeError("embedding endpoint exploded")

    async def embed_batch(self, texts: list[str]):
        raise RuntimeError("embedding batch exploded")


class _MatcherStore:
    """只提供 list_entries 的只读替身（用于纯锚点/关键词测试，不碰 SQLite）。"""

    def __init__(self, entries: list[dict]) -> None:
        self.entries = [dict(entry) for entry in entries]
        self.cache_reads: list[tuple[int, str, str]] = []

    def list_entries(self, world_id: str, entry_type: str | None = None) -> list[dict]:
        return [dict(entry) for entry in self.entries if entry["world_id"] == world_id]

    def load_embedding_cache(self, entry_ids, language, embedding_profile):
        self.cache_reads.append((len(list(entry_ids)), language, embedding_profile))
        return {}

    def save_embedding_cache(self, rows) -> None:  # pragma: no cover - 未配置语义时不会调用
        raise AssertionError("没有 embedding 客户端时不应写缓存")


def _retriever(entries: list[dict], client=None) -> LoreRetriever:
    """关键词（+ 可选语义）检索器：matcher 是真实实现，store 是只读替身。

    走一次 ``ensure_world``，让检索器拿到与生产一致的当前世界条目快照（语义候选、
    language 作用域都来自这里）。
    """

    matcher = KeywordMatcher()
    matcher.build(entries)
    retriever = LoreRetriever(
        matcher,
        store=_MatcherStore(entries),
        embedding_client_provider=(lambda: client) if client is not None else None,
    )
    retriever.ensure_world("w1", "zh-CN")
    return retriever


async def _ids(retriever: LoreRetriever, instance, text: str, **kwargs) -> list[str]:
    hits = await retriever.retrieve(instance, text, **kwargs)
    return [str(hit.get("id") or "") for hit in hits]


# ---- Case A / B / C / D：结构化锚点 -----------------------------------------


@pytest.mark.asyncio
async def test_case_a_action_keyword_still_hits() -> None:
    """A. 本轮行动文本里的关键词照旧命中。"""

    entries = [_entry("old_bridge", "旧石桥", keywords=["旧桥"])]
    retriever = _retriever(entries)

    assert await _ids(retriever, _instance(), "我去旧桥看看") == ["old_bridge"]


@pytest.mark.asyncio
async def test_case_b_scene_anchor_triggers_lore_absent_from_the_action() -> None:
    """B. 玩家只说"我看看桌子"，scene 里的"精神病院"仍然召回对应 Lore。"""

    entries = [_entry("asylum", "圣玛丽精神病院", keywords=["精神病院"])]
    retriever = _retriever(entries)
    instance = _instance(scene="圣玛丽精神病院 · 地下档案室")

    assert await _ids(retriever, instance, "我看看桌子") == ["asylum"]


@pytest.mark.asyncio
async def test_case_c_location_anchor_uses_world_state() -> None:
    """C. 行动与 scene 都没有地点词时，WorldState 的 canonical location 参与检索。"""

    entries = [_entry("old_bridge", "旧石桥", keywords=["旧石桥"])]
    retriever = _retriever(entries)
    instance = _instance(world_state=_world_state("旧石桥"))

    assert lore_query_location(instance) == "旧石桥"
    assert await _ids(retriever, instance, "我看看桌子") == ["old_bridge"]


@pytest.mark.asyncio
async def test_case_c_missing_location_never_blocks_retrieval() -> None:
    """C（缺省分支）。找不到 location 时省略该段，检索照常工作。"""

    entries = [_entry("bench", "长椅", keywords=["长椅"])]
    retriever = _retriever(entries)
    instance = _instance(world_state=fresh_world_state())

    assert lore_query_location(instance) == ""
    assert await _ids(retriever, instance, "我坐到长椅上") == ["bench"]


@pytest.mark.asyncio
async def test_case_d_present_npc_anchor_only_covers_npcs_in_the_scene() -> None:
    """D. 名册里有护士玛丽，但当前 scene 只出现院长：只有院长是 anchor。"""

    entries = [
        _entry("warden", "院长", keywords=["院长"], entry_type="npc"),
        _entry("nurse", "护士玛丽", keywords=["护士玛丽"], entry_type="npc"),
    ]
    retriever = _retriever(entries)
    instance = _instance(
        scene="地下档案室里，院长正背对着门。",
        npcs={
            "npc_warden": {"character_name": "院长"},
            "npc_nurse": {"name": "护士玛丽"},
        },
    )

    assert present_npc_names(instance, narrative_notes.scene(instance)) == ["院长"]
    hits = await _ids(retriever, instance, "我推开门")
    assert hits == ["warden"]


@pytest.mark.asyncio
async def test_query_never_scans_history_by_default() -> None:
    """§6：第一版只用 action / scene / location / present NPC，不扫历史轮次。"""

    entries = [_entry("old_bridge", "旧石桥", keywords=["旧桥"])]
    retriever = _retriever(entries)
    instance = _instance(log=[{"round": 1, "narrative": "我们之前走过旧桥。"}])

    query = build_lore_retrieval_query(instance, "我看看桌子")
    assert "旧桥" not in query
    assert await _ids(retriever, instance, "我看看桌子") == []


def test_query_sections_and_anchor_labels() -> None:
    instance = _instance(
        scene="地下档案室里，院长正背对着门。",
        npcs={"npc_warden": {"character_name": "院长"}},
        world_state=_world_state("st_mary_archive"),
    )

    query = build_lore_retrieval_query(instance, "我检查桌子下面")
    assert "[action]\n我检查桌子下面" in query
    assert "[scene]\n地下档案室里，院长正背对着门。" in query
    assert "[location]\nst_mary_archive" in query
    assert "[present_npcs]\n院长" in query


# ---- Case E：Ruleset 透明性 -------------------------------------------------


@pytest.mark.asyncio
async def test_case_e_retrieval_is_identical_across_rulesets() -> None:
    """E. 同一 Lore + 同一 scene，不同 Ruleset 得到完全相同的检索结果。"""

    entries = [
        _entry("asylum", "圣玛丽精神病院", keywords=["精神病院"]),
        _entry("bridge", "旧石桥", keywords=["旧桥"]),
    ]
    results = []
    for rule_id in ("freeform", "coc7", "dnd2024"):
        retriever = _retriever(entries)
        instance = _instance(
            scene="圣玛丽精神病院 · 地下档案室",
            rule_id=rule_id,
            combat_state="none",
        )
        results.append(await _ids(retriever, instance, "我看看桌子"))

    assert results[0] == results[1] == results[2] == ["asylum"]


def test_retrieval_module_has_no_ruleset_branching() -> None:
    """E（结构契约）。检索层不得引入具体 Ruleset 分支或第二套存储。"""

    source = io.open(
        Path("src/lorebook/retrieval.py"), encoding="utf-8",
    ).read()

    assert "rulesets" not in source
    assert "dnd2024" not in source
    assert "coc" not in source.lower().replace("cosine", "")
    assert "world_state" in source  # 只读投影，不写世界事实
    assert "apply_world_ops" not in source
    assert "set_control" not in source


# ---- Case F / G：语义召回与合并 ---------------------------------------------


@pytest.mark.asyncio
async def test_case_f_semantic_recall_respects_threshold() -> None:
    """F. 高相似条目召回，低相似条目被阈值排除。"""

    near = _entry("near", "档案室", keywords=["无关词甲"], content="档案室的记录。")
    far = _entry("far", "码头", keywords=["无关词乙"], content="码头上的雾。")
    client = _FakeEmbeddingClient({
        lore_entry_embedding_text(near): [1.0, 0.0],
        lore_entry_embedding_text(far): [0.0, 1.0],
    })
    retriever = _retriever([near, far], client)

    assert await _ids(retriever, _instance(), "我看看桌子") == ["near"]


@pytest.mark.asyncio
async def test_case_g_keyword_and_semantic_hits_merge_by_canonical_id() -> None:
    """G. 同时命中关键词与语义的条目只出现一次，纯语义条目出现一次。"""

    both = _entry("both", "旧石桥", keywords=["旧桥"])
    semantic_only = _entry("semantic", "档案室", keywords=["无关词"])
    client = _FakeEmbeddingClient({
        lore_entry_embedding_text(both): [1.0, 0.0],
        lore_entry_embedding_text(semantic_only): [1.0, 0.0],
    })
    retriever = _retriever([both, semantic_only], client)

    hits = await _ids(retriever, _instance(), "我去旧桥看看")
    assert hits == ["both", "semantic"]
    assert len(hits) == len(set(hits))


@pytest.mark.asyncio
async def test_semantic_hits_cannot_bypass_cooldown_timers() -> None:
    """§23. 被 cooldown 挡住的条目不会仅因向量相似进入上下文。"""

    cooling = _entry("cooling", "档案室", keywords=["无关词"])
    instance = _instance(
        lorebook_timed_state={"cooling": {"status": "cooldown", "remaining": 3}},
    )
    client = _FakeEmbeddingClient({lore_entry_embedding_text(cooling): [1.0, 0.0]})
    retriever = _retriever([cooling], client)

    assert await _ids(retriever, instance, "我看看桌子") == []


def test_embedding_text_uses_semantic_fields_only() -> None:
    """§10. 只把 name / type / keywords / content 送进 embedding。"""

    entry = _entry("e1", "旧石桥", keywords=["旧桥", "石桥"], content="十年前断裂。")
    entry.update({
        "sticky": 3, "cooldown": 2, "delay": 1, "probability": 40,
        "group": "bridges", "group_weight": 9, "order": 7,
        "visible_to": ["gm-secret"], "match_mode": "all",
    })

    text = lore_entry_embedding_text(entry)
    assert "Name: 旧石桥" in text
    assert "Type: location" in text
    assert "Keywords: 旧桥, 石桥" in text
    assert "Content: 十年前断裂。" in text
    for leaked in ("sticky", "cooldown", "delay", "probability", "group_weight", "gm-secret"):
        assert leaked not in text


def test_embedding_profile_never_contains_secrets() -> None:
    """§13. profile 只含模型 / 端点 / max_input，且不含 API key。"""

    client = _FakeEmbeddingClient(model="m1", base_url="http://host/v1", max_input_chars=500)
    client.api_key = "sk-super-secret"

    profile = embedding_profile(client)
    assert len(profile) == 32
    assert "secret" not in profile.lower()
    assert profile == embedding_profile(client)
    other = _FakeEmbeddingClient(model="m2", base_url="http://host/v1", max_input_chars=500)
    assert embedding_profile(other) != profile


# ---- Case H / I：降级路径 ---------------------------------------------------


@pytest.mark.asyncio
async def test_case_h_without_embedding_configuration_keyword_still_works() -> None:
    """H. 未配置向量模型：关键词 + scene anchor 正常工作，不抛异常。"""

    entries = [_entry("asylum", "圣玛丽精神病院", keywords=["精神病院"])]
    retriever = _retriever(entries)  # embedding_client_provider=None
    instance = _instance(scene="圣玛丽精神病院 · 地下档案室")

    assert await _ids(retriever, instance, "我看看桌子") == ["asylum"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["embed_none", "batch_none", "raise"])
async def test_case_i_embedding_failure_never_blocks_retrieval(failure: str) -> None:
    """I. embedding 失败（None / 异常）：关键词结果照常返回，回合不失败。"""

    entries = [_entry("old_bridge", "旧石桥", keywords=["旧桥"])]
    if failure == "raise":
        client = _RaisingEmbeddingClient()
    else:
        client = _FakeEmbeddingClient(
            embed_fails=failure == "embed_none",
            batch_fails=failure == "batch_none",
        )
    retriever = _retriever(entries, client)

    # 语义候选是另一条（纯语义）条目，失败时必须只是"没有语义增强"。
    extra = _entry("semantic", "档案室", keywords=["无关词"])
    retriever._entries.append(extra)  # type: ignore[attr-defined]

    assert await _ids(retriever, _instance(), "我去旧桥看看") == ["old_bridge"]


@pytest.mark.asyncio
async def test_dimension_mismatch_is_skipped_not_fatal() -> None:
    """§31. query 向量维度与内容向量不一致时跳过，不 crash。"""

    entry = _entry("weird", "档案室", keywords=["无关词"])
    client = _FakeEmbeddingClient(
        {lore_entry_embedding_text(entry): [1.0, 0.0, 0.0]},
        query_vector=[1.0, 0.0],
    )
    retriever = _retriever([entry], client)

    assert await _ids(retriever, _instance(), "我看看桌子") == []


# ---- Case J / K / L：缓存 ---------------------------------------------------


def _open_store(tmp_path: Path, entries: list[dict]) -> LorebookStore:
    store = LorebookStore(tmp_path / "lorebook.db")
    store.open()
    store.create_world("w1", "测试世界", language="zh-CN")
    for entry in entries:
        store.add_entry(entry)
    return store


@pytest.mark.asyncio
async def test_case_j_cache_is_filled_then_reused_and_invalidated_by_content(
    tmp_path: Path,
) -> None:
    """J. 首次 embed_batch 并写缓存；内容不变不再 embed；内容变化重新 embed。"""

    entry = _entry("asylum", "圣玛丽精神病院", keywords=["精神病院"])
    store = _open_store(tmp_path, [entry])
    client = _FakeEmbeddingClient({lore_entry_embedding_text(entry): [1.0, 0.0]})
    retriever = LoreRetriever(
        KeywordMatcher(), store=store, embedding_client_provider=lambda: client,
    )
    retriever.ensure_world("w1", "zh-CN")
    instance = _instance(world_state=_world_state("旧石桥"))

    try:
        await retriever.retrieve(instance, "我看看桌子")
        assert len(client.batch_calls) == 1

        cached = store.load_embedding_cache(
            ["asylum"], "zh-CN", embedding_profile(client),
        )
        assert cached["asylum"]["content_hash"] == lore_entry_content_hash(entry)

        # 第二次：内容没变 → 直接用缓存。
        await retriever.retrieve(instance, "我看看桌子")
        assert len(client.batch_calls) == 1

        # 内容变化：生产路径会 invalidate（webui 世界书编辑 → invalidate_lorebook_index）。
        store.update_entry("asylum", {"content": "档案室已经烧毁。"})
        retriever.invalidate_world("w1")
        retriever.ensure_world("w1", "zh-CN")
        await retriever.retrieve(instance, "我看看桌子")
        assert len(client.batch_calls) == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_case_k_switching_profile_reembeds_everything(tmp_path: Path) -> None:
    """K. 换模型 / 端点后 profile 变化，旧缓存不得复用。"""

    entry = _entry("asylum", "圣玛丽精神病院", keywords=["精神病院"])
    store = _open_store(tmp_path, [entry])
    first = _FakeEmbeddingClient(
        {lore_entry_embedding_text(entry): [1.0, 0.0]}, model="model-a",
    )
    retriever = LoreRetriever(
        KeywordMatcher(), store=store, embedding_client_provider=lambda: first,
    )
    retriever.ensure_world("w1", "zh-CN")
    instance = _instance()

    try:
        await retriever.retrieve(instance, "我看看桌子")
        assert len(first.batch_calls) == 1

        second = _FakeEmbeddingClient(
            {lore_entry_embedding_text(entry): [1.0, 0.0]}, model="model-b",
        )
        assert embedding_profile(second) != embedding_profile(first)
        retriever._embedding_client_provider = lambda: second  # type: ignore[attr-defined]
        await retriever.retrieve(instance, "我看看桌子")
        assert len(second.batch_calls) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_case_l_cache_rows_are_isolated_per_language(tmp_path: Path) -> None:
    """L. 同一 entry 的 zh-CN / en 是两条独立缓存行。"""

    entry = _entry("asylum", "圣玛丽精神病院", keywords=["精神病院"])
    store = _open_store(tmp_path, [entry])
    client = _FakeEmbeddingClient({lore_entry_embedding_text(entry): [1.0, 0.0]})
    retriever = LoreRetriever(
        KeywordMatcher(), store=store, embedding_client_provider=lambda: client,
    )
    instance = _instance()

    try:
        retriever.ensure_world("w1", "zh-CN")
        await retriever.retrieve(instance, "我看看桌子")
        retriever.invalidate_world("w1")
        retriever.ensure_world("w1", "en")
        await retriever.retrieve(instance, "I look at the desk")

        assert len(client.batch_calls) == 2
        profile = embedding_profile(client)
        assert store.load_embedding_cache(["asylum"], "zh-CN", profile)
        assert store.load_embedding_cache(["asylum"], "en", profile)
        rows = store._conn.execute(
            "SELECT language FROM lorebook_embeddings WHERE entry_id='asylum' ORDER BY language"
        ).fetchall()
        assert [row[0] for row in rows] == ["en", "zh-CN"]
    finally:
        store.close()


def test_deleting_an_entry_drops_its_cached_vectors(tmp_path: Path) -> None:
    """派生缓存跟着条目走：删除后不残留向量行。"""

    entry = _entry("asylum", "圣玛丽精神病院", keywords=["精神病院"])
    store = _open_store(tmp_path, [entry])
    try:
        store.save_embedding_cache([{
            "entry_id": "asylum",
            "language": "zh-CN",
            "embedding_profile": "profile",
            "content_hash": "hash",
            "embedding": [1.0, 0.0],
        }])
        assert store.load_embedding_cache(["asylum"], "zh-CN", "profile")

        store.delete_entry("asylum")
        assert store.load_embedding_cache(["asylum"], "zh-CN", "profile") == {}
    finally:
        store.close()


# ---- Case M：visibility fail-closed ----------------------------------------


@pytest.mark.asyncio
async def test_case_m_hidden_lore_reaches_gm_but_never_a_player_path() -> None:
    """M. GM-only Lore：GM 检索可命中；玩家 / 全队路径都拿不到（含语义候选）。"""

    hidden = {
        **_entry("secret", "地下室真相", keywords=["真相"], content="不可泄露。"),
        "visible_to": [],
    }
    secret_semantic = {
        **_entry("secret2", "火灾计划", keywords=["无关词"], content="计划纵火。"),
        "visible_to": [],
    }
    public = _entry("public", "大厅", keywords=["大厅"], visible_to=["*"])
    client = _FakeEmbeddingClient({
        lore_entry_embedding_text(hidden): [1.0, 0.0],
        lore_entry_embedding_text(secret_semantic): [1.0, 0.0],
        lore_entry_embedding_text(public): [0.0, 1.0],
    })
    entries = [hidden, secret_semantic, public]
    retriever = _retriever(entries, client)
    instance = _instance()

    gm_hits = await _ids(retriever, instance, "我在大厅里问真相是什么")
    assert "secret" in gm_hits
    assert "public" in gm_hits

    player_hits = await _ids(
        retriever, instance, "我在大厅里问真相是什么", viewer_is_gm=False, viewer_uid="p1",
    )
    assert player_hits == ["public"]

    party_hits = await _ids(
        retriever, instance, "我在大厅里问真相是什么", viewer_is_gm=False,
    )
    assert party_hits == ["public"]

    player_trace_ids = {row["entry_id"] for row in retriever.last_activation_trace if row.get("entry_id")}
    assert player_trace_ids == {"public"}
    assert len(retriever.last_activation_trace) == 1

    await _ids(retriever, instance, "我在大厅里问真相是什么", viewer_is_gm=True)
    assert {row["entry_id"] for row in retriever.last_activation_trace} >= {"secret", "secret2", "public"}


@pytest.mark.asyncio
async def test_private_location_fact_is_not_used_for_a_player_path() -> None:
    """M（锚点侧）。非 public 的 location 事实不进玩家路径的检索 query。"""

    entries = [_entry("bridge", "旧石桥", keywords=["旧石桥"])]
    retriever = _retriever(entries)
    instance = _instance(world_state=_world_state("旧石桥", visibility="gm"))

    assert lore_query_location(instance, viewer_is_gm=True) == "旧石桥"
    assert lore_query_location(instance, viewer_is_gm=False, viewer_uid="p1") == ""
    assert await _ids(
        retriever, instance, "我看看桌子", viewer_is_gm=False, viewer_uid="p1",
    ) == []


# ---- Case R：桌外提问只观察计时器 -------------------------------------------


@pytest.mark.asyncio
async def test_case_r_table_talk_observes_timers_without_mutating_them() -> None:
    """R. mutate_timers=False：sticky 仍然生效，但实例的计时状态一个字节都不变。"""

    sticky = _entry("sticky_lore", "旧石桥", keywords=["旧桥"])
    sticky["sticky"] = 3
    cooling = _entry("cooling", "码头", keywords=["码头"])
    cooling["cooldown"] = 2
    instance = _instance(lorebook_timed_state={})
    before = copy.deepcopy(lorebook_runtime.timers(instance))

    retriever = _retriever([sticky, cooling])
    hits = await _ids(
        retriever, instance, "我去旧桥和码头看看",
        viewer_is_gm=False, viewer_uid="p1", mutate_timers=False,
    )

    assert "sticky_lore" in hits
    assert "cooling" in hits
    assert lorebook_runtime.timers(instance) == before == {}


@pytest.mark.asyncio
async def test_normal_round_still_commits_timers() -> None:
    """对照 R：正常回合（默认 mutate_timers=True）沿用既有计时器写入语义。"""

    sticky = _entry("sticky_lore", "旧石桥", keywords=["旧桥"])
    sticky["sticky"] = 3
    instance = _instance(lorebook_timed_state={})
    retriever = _retriever([sticky])

    await retriever.retrieve(instance, "我去旧桥看看")

    assert lorebook_runtime.timers(instance)["sticky_lore"] == {
        "sticky_remaining": 3,
        "pending_cooldown": 0,
        "activated_tick": 0,
    }


# ---- Case Q：三条路径共用同一检索器 -----------------------------------------


def test_case_q_normal_round_and_swipe_and_kp_share_one_retriever() -> None:
    """Q / §22. 三条路径都走同一个 LoreRetriever，且都不再自己调用 matcher。"""

    from src.commands.game_handler import GameHandler
    from src.lorebook.matcher import KeywordMatcher as _KM

    class _Registry:
        def get(self, _key):
            return None

        def save(self, _instance):
            return None

    handler = GameHandler(
        registry=_Registry(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        lorebook_matcher=_KM(),
        lorebook_store=None,
        memory_store=None,
    )

    assert handler._round_processor.lore_retriever is handler.lore_retriever
    assert handler._swipe_generator.lore_retriever is handler.lore_retriever
    assert handler._kp_questions.lore_retriever is handler.lore_retriever

    for path in (
        "src/commands/round_processor.py",
        "src/commands/swipe_generator.py",
        "src/commands/kp_questions.py",
    ):
        source = io.open(Path(path), encoding="utf-8").read()
        assert "self.lore_retriever.retrieve(" in source
        assert "match_with_recursive(" not in source


# ---- Review 修复：Hybrid 最终排序（Blocker A） -------------------------------


def _merge(keyword_entries, semantic_entries):
    from src.lorebook.retrieval import LoreRetriever

    return LoreRetriever._merge_and_sort(list(keyword_entries), list(semantic_entries))


def test_review_case1_tier_and_order_beat_source_priority() -> None:
    """Case 1：keyword=archived/order=500 不得排在 semantic=core/order=1 之前。"""

    low = _entry("low_keyword", "旧档案", keywords=["旧档案"], tier="archived")
    low["order"] = 500
    high = _entry("high_semantic", "核心设定", keywords=["无关词"], tier="core")
    high["order"] = 1
    high["_semantic_score"] = 0.91

    assert [e["id"] for e in _merge([low], [high])] == ["high_semantic", "low_keyword"]


def test_review_case2_keyword_wins_only_on_equal_tier_and_order() -> None:
    """Case 2：同 tier + 同 order 时 keyword 命中优先于 pure semantic。"""

    keyword = _entry("kw", "关键词命中", keywords=["桥"], tier="core")
    keyword["order"] = 10
    semantic = _entry("sem", "纯语义命中", keywords=["无关词"], tier="core")
    semantic["order"] = 10
    semantic["_semantic_score"] = 0.99

    assert [e["id"] for e in _merge([keyword], [semantic])] == ["kw", "sem"]


def test_review_case3_semantic_score_breaks_ties_between_semantic_hits() -> None:
    """Case 3：两条 semantic 同 tier/order 时，cosine 高的排前。"""

    high = _entry("a_high", "高相似", keywords=["x"], tier="background")
    high["order"] = 20
    high["_semantic_score"] = 0.88
    low = _entry("b_low", "低相似", keywords=["y"], tier="background")
    low["order"] = 20
    low["_semantic_score"] = 0.61

    assert [e["id"] for e in _merge([], [low, high])] == ["a_high", "b_low"]


def test_review_case4_fully_equal_entries_sort_by_id() -> None:
    """Case 4：完全同级时用 canonical id 提供稳定排序。"""

    first = _entry("aaa", "甲", keywords=["x"], tier="core")
    second = _entry("bbb", "乙", keywords=["x"], tier="core")
    first["_semantic_score"] = second["_semantic_score"] = 0.7

    assert [e["id"] for e in _merge([], [second, first])] == ["aaa", "bbb"]


# ---- Review 修复：malformed vector fail-soft（Blocker B） --------------------


def test_normalize_vector_rejects_malformed_values() -> None:
    from src.lorebook.retrieval import normalize_vector

    assert normalize_vector([1, 0.5]) == [1.0, 0.5]
    assert normalize_vector(("1", 2)) == [1.0, 2.0]
    assert normalize_vector("not-a-vector") is None
    assert normalize_vector([]) is None
    assert normalize_vector(None) is None
    assert normalize_vector(["oops", 0.3]) is None
    assert normalize_vector([float("nan"), 0.3]) is None
    assert normalize_vector([float("inf"), 0.2]) is None
    assert normalize_vector([float("-inf"), 0.2]) is None
    assert normalize_vector([True, 0.2]) is None


class _BadCacheStore(_MatcherStore):
    """缓存里放一条坏向量，用于验证 fail-soft。"""

    def __init__(self, entries, bad_vector):
        super().__init__(entries)
        self.bad_vector = bad_vector

    def load_embedding_cache(self, entry_ids, language, embedding_profile):
        from src.lorebook.retrieval import lore_entry_content_hash

        return {
            str(entry["id"]): {
                "content_hash": lore_entry_content_hash(entry),
                "embedding": list(self.bad_vector),
            }
            for entry in self.entries
            if str(entry["id"]) in {str(i) for i in entry_ids}
        }


@pytest.mark.asyncio
async def test_review_cached_bad_vector_reembeds_without_crashing() -> None:
    """缓存向量 ["oops", 0.3]：不 crash，坏条目重新 embedding，keyword 结果继续。"""

    keyword_entry = _entry("kw", "旧石桥", keywords=["旧桥"])
    broken = _entry("broken", "档案室", keywords=["无关词"])
    client = _FakeEmbeddingClient({
        lore_entry_embedding_text(keyword_entry): [1.0, 0.0],
        lore_entry_embedding_text(broken): [1.0, 0.0],
    })
    matcher = KeywordMatcher()
    matcher.build([keyword_entry, broken])
    retriever = LoreRetriever(
        matcher,
        store=_BadCacheStore([keyword_entry, broken], ["oops", 0.3]),
        embedding_client_provider=lambda: client,
    )
    retriever.ensure_world("w1", "zh-CN")

    hits = await _ids(retriever, _instance(), "我去旧桥看看")
    assert "kw" in hits
    assert len(client.batch_calls) == 1  # 坏缓存触发重新 embedding


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_query", [["bad", 0.1], ["nan", 0.1]])
async def test_review_bad_query_vector_skips_semantic_only(bad_query) -> None:
    """query 向量非法（非数字）：semantic 跳过，keyword 结果继续。"""

    keyword_entry = _entry("kw", "旧石桥", keywords=["旧桥"])
    semantic_only = _entry("sem", "档案室", keywords=["无关词"])
    client = _FakeEmbeddingClient({
        lore_entry_embedding_text(semantic_only): [1.0, 0.0],
    })
    client.query_vector = bad_query
    retriever = _retriever([keyword_entry, semantic_only], client)

    assert await _ids(retriever, _instance(), "我去旧桥看看") == ["kw"]
    assert client.batch_calls == []


@pytest.mark.asyncio
async def test_review_nan_query_vector_skips_semantic() -> None:
    keyword_entry = _entry("kw", "旧石桥", keywords=["旧桥"])
    client = _FakeEmbeddingClient({}, query_vector=[float("nan"), 0.1])
    retriever = _retriever([keyword_entry], client)

    assert await _ids(retriever, _instance(), "我去旧桥看看") == ["kw"]


@pytest.mark.asyncio
async def test_review_inf_entry_vector_skips_that_entry() -> None:
    """embed_batch 返回 [Inf, 0.2]：该条目跳过，不 crash。"""

    keyword_entry = _entry("kw", "旧石桥", keywords=["旧桥"])
    broken = _entry("broken", "档案室", keywords=["无关词"])
    client = _FakeEmbeddingClient({
        lore_entry_embedding_text(keyword_entry): [1.0, 0.0],
        lore_entry_embedding_text(broken): [float("inf"), 0.2],
    })
    retriever = _retriever([keyword_entry, broken], client)

    assert await _ids(retriever, _instance(), "我去旧桥看看") == ["kw"]


# ---- Review 修复：lexical query 不含结构标签（Blocker C） --------------------


def test_review_lexical_query_has_no_structural_labels() -> None:
    from src.lorebook.retrieval import build_lore_lexical_query, build_lore_retrieval_queries

    instance = _instance(
        scene="地下档案室里，院长正背对着门。",
        npcs={"npc_warden": {"character_name": "院长"}},
        world_state=_world_state("st_mary_archive"),
    )
    lexical = build_lore_lexical_query(instance, "我检查桌子下面")

    for label in ("[action]", "[scene]", "[location]", "[present_npcs]"):
        assert label not in lexical
    assert "我检查桌子下面" in lexical
    assert "地下档案室" in lexical
    assert "st_mary_archive" in lexical
    assert "院长" in lexical

    # semantic query 仍保留结构标签
    queries = build_lore_retrieval_queries(instance, "我检查桌子下面")
    assert "[scene]" in queries["semantic"]
    assert "[location]" in queries["semantic"]
    assert "[present_npcs]" in queries["semantic"]
    assert "[scene]" not in queries["lexical"]


@pytest.mark.asyncio
async def test_review_structural_label_keywords_do_not_false_match() -> None:
    """关键词 location / scene 不得因为 [location]/[scene] 标签误命中。"""

    labelled = [
        _entry("kw_location", "地点标签", keywords=["location"], content="无关。"),
        _entry("kw_scene", "场景标签", keywords=["scene"], content="无关。"),
    ]
    retriever = _retriever(labelled)
    instance = _instance(scene="圣玛丽精神病院 · 地下档案室")

    assert await _ids(retriever, instance, "我看看桌子") == []


@pytest.mark.asyncio
async def test_review_real_location_value_still_matches() -> None:
    """对照：真正的 location 值就是 "location" 时，正常命中。"""

    entries = [_entry("kw_location", "地点标签", keywords=["location"], content="无关。")]
    retriever = _retriever(entries)
    instance = _instance(world_state=_world_state("location"))

    assert await _ids(retriever, instance, "我看看桌子") == ["kw_location"]
