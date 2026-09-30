"""Structured ContentRef (MOD-05, 母方案 §9/§10).

大型模组的跨包引用不能只靠 v1 的 ``kind:id`` 裸字符串（无法表达"内容来自
哪个模组"，也无法支撑跨来源同名内容）。v2 引入结构化三元组：

```json
{
  "source_kind": "module", "source_id": "example-castle-module",
  "kind": "monster", "id": "ash_vampire", "digest": "sha256:..."
}
```

The older ``{"source": "module:...", ...}`` spelling remains an adapter for
existing content and saves.  ``digest`` is an opaque package-provided value;
this layer does not invent an algorithm or recalculate it.

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
    # canonical Content Package resources
    "world", "lorebook", "adventure",
    # D&D ruleset catalog（§29）
    "monster", "npc_statblock", "item", "spell", "class_extension",
    "feature", "hazard", "trap", "encounter_profile", "reward",
)


class ContentRefError(ValueError):
    """A content reference is malformed or unresolvable: fail closed."""


@dataclass(frozen=True, slots=True)
class ContentKindRegistry:
    """Closed vocabulary for portable content references.

    The registry intentionally carries only canonical kind identities in PR A.
    Per-kind portable schemas belong here once each kind has a real owner and
    caller; inventing those schemas now would make the registry a second
    authority.
    """

    _kinds: frozenset[str]

    def __init__(self, kinds: Sequence[str] = CONTENT_KINDS) -> None:
        normalized = tuple(str(kind) for kind in kinds)
        if len(set(normalized)) != len(normalized):
            raise ValueError("content kind registry contains duplicates")
        object.__setattr__(self, "_kinds", frozenset(normalized))

    @property
    def kinds(self) -> tuple[str, ...]:
        """Stable sorted view for clients and diagnostics."""

        return tuple(sorted(self._kinds))

    def supports(self, kind: str) -> bool:
        return str(kind) in self._kinds

    def validate(self, value: Any) -> str:
        try:
            validated = canonical_id(value, field="content kind")
        except ValueError as exc:
            raise ContentRefError(str(exc)) from exc
        if not self.supports(validated):
            raise ContentRefError(f"content kind is not supported: {validated!r}")
        return validated


CONTENT_KIND_REGISTRY = ContentKindRegistry()


@dataclass(frozen=True, slots=True, init=False)
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
    digest: str = ""

    def __init__(
        self,
        source: str | None = None,
        kind: str = "",
        id: str = "",
        explicit: bool = False,
        digest: str = "",
        *,
        source_kind: str | None = None,
        source_id: str | None = None,
    ) -> None:
        """Construct a ref using the legacy or source-aware spelling.

        ``source=...`` remains the compatibility constructor used by existing
        ruleset callers.  New callers may pass ``source_kind`` and
        ``source_id``; supplying both spellings with different values fails
        closed instead of guessing which identity wins.
        """

        has_parts = source_kind is not None or source_id is not None
        if has_parts:
            if source_kind is None or source_id is None:
                raise ValueError("source_kind and source_id must be provided together")
            split_source = f"{source_kind}:{source_id}"
            if source is not None and str(source) != split_source:
                raise ValueError("source and source_kind/source_id disagree")
            source = split_source
        if source is None:
            raise ValueError("content ref source is required")
        object.__setattr__(self, "source", str(source))
        object.__setattr__(self, "kind", str(kind))
        object.__setattr__(self, "id", str(id))
        object.__setattr__(self, "explicit", bool(explicit))
        object.__setattr__(self, "digest", str(digest or ""))

    @property
    def source_kind(self) -> str:
        """The source registry kind (the first component of ``source``)."""

        return self.source.partition(":")[0]

    @property
    def source_id(self) -> str:
        """The source identity (everything after the first colon)."""

        return self.source.partition(":")[2]

    def canonical(self) -> str:
        """Deterministic string key (diagnostics / dedupe / logging)."""

        return f"{self.source}|{self.kind}|{self.id}"

    def to_portable_dict(self) -> dict[str, str]:
        """Return the client-readable source-aware reference shape.

        ``explicit`` is a resolver detail rather than portable identity.  A
        serialized ref is therefore always source-bound and unambiguous.
        ``digest`` remains opaque here; its producer/algorithm is owned by the
        package or bundle that supplied the content.
        """

        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "kind": self.kind,
            "id": self.id,
            "digest": self.digest,
        }

    # Aliases for callers that use common mapping terminology.
    as_dict = to_portable_dict
    to_dict = to_portable_dict


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
        declared_kind = raw.get("source_kind")
        declared_id = raw.get("source_id")
        if declared_kind is not None or declared_id is not None:
            if not isinstance(declared_kind, str) or not isinstance(declared_id, str):
                raise ContentRefError(f"content ref source_kind/source_id are invalid: {raw!r}")
            split_source = f"{declared_kind.strip()}:{declared_id.strip()}"
            if declared_source and declared_source != split_source:
                raise ContentRefError(f"content ref source fields disagree: {raw!r}")
            declared_source = split_source
        explicit = bool(declared_source)
        source = declared_source or source
        kind = raw.get("kind")
        ref_id = raw.get("id")
        digest = raw.get("digest", "")
        if not isinstance(digest, str):
            raise ContentRefError(f"content ref digest is invalid: {raw!r}")
        digest = digest.strip()
    elif isinstance(raw, str):
        text = raw.strip()
        if ":" not in text:
            raise ContentRefError(f"content ref must be kind:id or a structured object: {raw!r}")
        kind, _, ref_id = text.partition(":")
        digest = ""
    else:
        raise ContentRefError(f"content ref must be an object or string: {raw!r}")

    if not source:
        raise ContentRefError(f"content ref has no source: {raw!r}")
    try:
        validated_source = validate_source_ref(source)
        validated_kind = CONTENT_KIND_REGISTRY.validate(kind)
        validated_id = canonical_id(ref_id, field="content id")
    except ValueError as exc:
        raise ContentRefError(str(exc)) from exc
    return ContentRef(
        source=validated_source, kind=validated_kind, id=validated_id,
        explicit=explicit, digest=digest,
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
    "CONTENT_KIND_REGISTRY",
    "ContentKindRegistry",
    "ContentRef",
    "ContentRefChain",
    "ContentRefError",
    "ContentResolution",
    "parse_content_ref",
]
