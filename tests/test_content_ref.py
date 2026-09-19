"""ContentRef v2 测试（MOD-05，母方案 §9/§10）。

覆盖：v2 dict / v1 kind:id 字符串解析、v1 归属 adventure-local 默认来源、
语法 fail closed、kind 词表、显式来源直查不回溯（禁 silent shadow 的反向
面）、v1 链式回溯顺序、失败时 tried 清单可诊断。
"""

from __future__ import annotations

import pytest

from src.content_modules import (
    CONTENT_KINDS,
    ContentRef,
    ContentRefChain,
    ContentRefError,
    parse_content_ref,
)


DEFAULT_SOURCE = "adventure:core:castle"


def test_parse_v2_structured_ref() -> None:
    ref = parse_content_ref(
        {"source": "module:castle-module", "kind": "monster", "id": "ash_vampire"},
        default_source=DEFAULT_SOURCE,
    )
    assert ref == ContentRef(
        source="module:castle-module", kind="monster", id="ash_vampire", explicit=True,
    )
    assert ref.canonical() == "module:castle-module|monster|ash_vampire"


def test_parse_v1_string_binds_to_default_source() -> None:
    ref = parse_content_ref("monster:ash_vampire", default_source=DEFAULT_SOURCE)
    assert ref.source == DEFAULT_SOURCE
    assert ref.kind == "monster"
    assert ref.id == "ash_vampire"


def test_parse_v2_without_source_uses_default() -> None:
    ref = parse_content_ref({"kind": "item", "id": "relic"}, default_source=DEFAULT_SOURCE)
    assert ref.source == DEFAULT_SOURCE


def test_v1_without_default_source_fails_closed() -> None:
    with pytest.raises(ContentRefError, match="no source"):
        parse_content_ref({"kind": "item", "id": "relic"}, default_source="")


@pytest.mark.parametrize("raw", [
    "没有冒号", "monster:", 42, None, [],
    {"kind": "dragon_type", "id": "x"},
    {"source": "unknown_kind:castle", "kind": "monster", "id": "x"},
    {"source": "module:castle", "kind": "monster", "id": "中文"},
])
def test_malformed_refs_fail_closed(raw: object) -> None:
    with pytest.raises(ContentRefError):
        parse_content_ref(raw, default_source=DEFAULT_SOURCE)


def test_content_kind_vocabulary_is_declared() -> None:
    assert "monster" in CONTENT_KINDS and "encounter_profile" in CONTENT_KINDS
    assert "scene" in CONTENT_KINDS and "npc" in CONTENT_KINDS


# ---- 解析链 ----


def _chain() -> ContentRefChain:
    adventure = {"monster": {"goblin": {"name": "Goblin"}}}
    module = {"monster": {"ash_vampire": {"name": "Ash Vampire"}}}
    core = {"monster": {"goblin": {"name": "Goblin (SRD)"}}}
    return ContentRefChain([
        ("adventure:core:castle", lambda kind, ref_id: adventure.get(kind, {}).get(ref_id)),
        ("module:castle-module", lambda kind, ref_id: module.get(kind, {}).get(ref_id)),
        ("core:srd", lambda kind, ref_id: core.get(kind, {}).get(ref_id)),
    ])


def test_v1_ref_walks_chain_in_priority_order() -> None:
    chain = _chain()
    ref = parse_content_ref("monster:goblin", default_source=DEFAULT_SOURCE)
    resolution = chain.resolve(ref)
    assert resolution.source_label == DEFAULT_SOURCE  # adventure-local 优先
    assert resolution.value == {"name": "Goblin"}


def test_v1_ref_falls_back_to_core_when_local_missing() -> None:
    chain = _chain()
    ref = parse_content_ref("monster:ash_vampire", default_source=DEFAULT_SOURCE)
    resolution = chain.resolve(ref)
    assert resolution.source_label == "module:castle-module"


def test_explicit_source_is_direct_and_never_shadows() -> None:
    chain = _chain()
    ref = parse_content_ref(
        {"source": "core:srd", "kind": "monster", "id": "goblin"},
        default_source=DEFAULT_SOURCE,
    )
    resolution = chain.resolve(ref)
    # 显式 core 引用直接命中 core（即使 adventure-local 也有同名内容）。
    assert resolution.source_label == "core:srd"
    assert resolution.value == {"name": "Goblin (SRD)"}


def test_unresolved_ref_reports_tried_sources() -> None:
    chain = _chain()
    ref = parse_content_ref("monster:missing", default_source=DEFAULT_SOURCE)
    with pytest.raises(ContentRefError, match="tried") as excinfo:
        chain.resolve(ref)
    assert "adventure:core:castle" in str(excinfo.value)
    assert "core:srd" in str(excinfo.value)


def test_explicit_source_miss_does_not_walk_the_chain() -> None:
    chain = _chain()
    ref = parse_content_ref(
        {"source": "module:castle-module", "kind": "monster", "id": "missing"},
        default_source=DEFAULT_SOURCE,
    )
    with pytest.raises(ContentRefError) as excinfo:
        chain.resolve(ref)
    # 显式来源未命中：不回溯其它来源（禁止 silent shadow 的镜像语义）。
    assert "adventure:core:castle" not in str(excinfo.value)
