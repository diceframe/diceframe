"""Structured ContentRef (MOD-05, 母方案 §9/§10).

大型模组的跨包引用不能只靠 v1 的 ``kind:id`` 裸字符串（无法表达"内容来自
哪个模组"，也无法支撑跨来源同名内容）。v2 引入结构化三元组：

```json
{"source": "module:example-castle-module", "kind": "monster", "id": "ash_vampire"}
```

硬规则（母方案 §9）：

- **集中解析**：各模块禁止自行 ``split(":")`` / ``startswith(...)`` 猜引用；
  一切解析走本模块。
- **v1 兼容**：裸 ``kind:id`` 字符串被解释为 *当前 Adventure 本地来源* 的
  引用（由调用方传入 ``default_source``，通常是
  ``adventure:<adventure_id>``）；本模块不猜上下文。
- source 语法沿用 world contracts 的 ``<source_kind>:<source_id>``，保证
  world entity 的 ``source_ref`` / ContentRef / 模组归属三者同源。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from src.engine.world.contracts import canonical_id, validate_source_ref

# 跨域内容 kind 首版词表：generic adventure 实体 + D&D 内容库类型（§29）。
# 词表允许按需扩展，但必须经过本常量（防自由字符串漂移）。
CONTENT_KINDS = (
    # generic adventure entities（v1 bundle kinds）
    "scene", "npc", "map_location", "encounter_catalog",
    # D&D ruleset catalog（§29）
    "monster", "npc_statblock", "item", "spell", "class_extension",
    "feature", "hazard", "trap", "encounter_profile", "reward",
)


class ContentRefError(ValueError):
    """A content reference is malformed or unresolvable: fail closed."""


@dataclass(frozen=True, slots=True)
class ContentRef:
    """One validated cross-package content reference.

    ``explicit`` 标记来源是引用自带的（v2 结构化）还是解析时归属默认来源的
    （v1 裸 ``kind:id``）：前者解析时**直接定位**该来源（不回溯，禁止
    silent shadow 的镜像语义），后者才允许按链回溯。
    """

    source: str
    kind: str
    id: str
    explicit: bool = False

    def canonical(self) -> str:
        """Deterministic string key (diagnostics / dedupe / logging)."""

        return f"{self.source}|{self.kind}|{self.id}"


def parse_content_ref(raw: Any, *, default_source: str) -> ContentRef:
    """Parse a v2 dict ref or a legacy v1 ``kind:id`` string.

    - v2 dict：``{"source", "kind", "id"}``；``source`` 缺省时必须提供
      ``default_source``（否则无从归属——不猜）。
    - v1 string：``kind:id``（首个冒号分隔）→ 归属 ``default_source``。
    """

    source = str(default_source or "").strip()
    explicit = False
    if isinstance(raw, dict):
        declared_source = str(raw.get("source") or "").strip()
        explicit = bool(declared_source)
        source = declared_source or source
        kind = raw.get("kind")
        ref_id = raw.get("id")
    elif isinstance(raw, str):
        text = raw.strip()
        if ":" not in text:
            raise ContentRefError(f"content ref must be kind:id or a structured object: {raw!r}")
        kind, _, ref_id = text.partition(":")
    else:
        raise ContentRefError(f"content ref must be an object or string: {raw!r}")

    if not source:
        raise ContentRefError(f"content ref has no source: {raw!r}")
    try:
        validated_source = validate_source_ref(source)
        validated_kind = canonical_id(kind, field="content kind")
        validated_id = canonical_id(ref_id, field="content id")
    except ValueError as exc:
        raise ContentRefError(str(exc)) from exc
    if validated_kind not in CONTENT_KINDS:
        raise ContentRefError(f"content kind is not supported: {validated_kind!r}")
    return ContentRef(
        source=validated_source, kind=validated_kind, id=validated_id,
        explicit=explicit,
    )


# ---- 解析链（母方案 §10：显式来源优先，v1 引用按链回溯）--------------------

Lookup = Callable[[str, str], Any]


@dataclass(frozen=True, slots=True)
class ContentResolution:
    """One successful lookup with its provenance."""

    ref: ContentRef
    source_label: str
    value: Any


class ContentRefChain:
    """Ordered source chain: adventure-local → module → dependencies → core.

    每个 entry 是 ``(source_label, lookup)``；``lookup(kind, id)`` 返回内容或
    ``None``。显式来源（v2 ref）直接定位到对应来源，**不做链回溯**（同一
    canonical ref 的覆盖必须显式，禁止 silent shadow，§10）；v1 引用（归属
    adventure-local）按链序查找，首个命中胜出，tried 清单用于诊断。
    """

    def __init__(self, chain: Sequence[tuple[str, Lookup]]) -> None:
        self._chain: list[tuple[str, Lookup]] = list(chain)

    def resolve(self, ref: ContentRef) -> ContentResolution:
        tried: list[str] = []
        for source_label, lookup in self._chain:
            if ref.explicit and source_label != ref.source:
                continue
            tried.append(source_label)
            value = lookup(ref.kind, ref.id)
            if value is not None:
                return ContentResolution(ref=ref, source_label=source_label, value=value)
            if ref.explicit:
                break
        raise ContentRefError(
            f"content ref unresolved: {ref.canonical()} (tried: {', '.join(tried) or 'none'})"
        )


__all__ = [
    "CONTENT_KINDS",
    "ContentRef",
    "ContentRefChain",
    "ContentRefError",
    "ContentResolution",
    "parse_content_ref",
]
