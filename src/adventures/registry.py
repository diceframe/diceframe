"""Adventure source registry (MOD-02, 母方案 §31/§72/§106).

统一 Adventure 的三个来源：

```text
builtin   随 DiceFrame 发布的内置冒险（templates/adventures）
user      用户独立导入的 standalone adventure（数据目录）
plugin    content-pack module 贡献的冒险（MOD-03 接入）
```

硬规则（母方案 §31）：

- **来源优先级不做 silent override**：同一个 ``adventure_id`` 出现在多个
  来源时是**显式冲突**——``resolve`` 默认抛
  :class:`AdventureSourceConflict`，除非调用方（存档 binding / UI）明确
  指定来源；``list`` 会把冲突项原样标出。
- 来源注册时按 ``(kind, source_id)`` 去重；builtin/user 各至多一个目录，
  plugin 可有多个（每模组一个）。
- 本模块只做来源聚合与解析，不做包校验（校验仍是 loader 的职责）、不做
  安装/写盘。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from src.adventures.bundle import (
    AdventureBundleError,
    AdventureBundleLoader,
    LoadedAdventureBundle,
)

SOURCE_KINDS = ("builtin", "user", "plugin")


class AdventureSourceConflict(ValueError):
    """The same adventure_id exists in more than one source; no silent winner."""

    def __init__(self, adventure_id: str, sources: list["AdventureSource"]) -> None:
        self.adventure_id = str(adventure_id)
        self.sources = list(sources)
        listing = ", ".join(sorted(f"{s.kind}:{s.source_id}" for s in self.sources))
        super().__init__(
            f"adventure {self.adventure_id!r} exists in multiple sources: {listing}"
        )


@dataclass(frozen=True)
class AdventureSource:
    """One adventure source: a kind, an owner id, and its loader."""

    kind: str
    source_id: str
    loader: AdventureBundleLoader

    def label(self) -> str:
        return f"{self.kind}:{self.source_id}" if self.source_id else self.kind


class AdventureSourceRegistry:
    """Aggregated, conflict-explicit view over all adventure sources."""

    def __init__(self) -> None:
        self._sources: list[AdventureSource] = []

    # ---- 注册 ----------------------------------------------------------

    def register(self, source: AdventureSource) -> None:
        if source.kind not in SOURCE_KINDS:
            raise ValueError(f"unknown adventure source kind: {source.kind!r}")
        for existing in self._sources:
            if existing.kind == source.kind and existing.source_id == source.source_id:
                raise ValueError(f"adventure source already registered: {source.label()}")
        self._sources.append(source)

    @classmethod
    def from_directories(
        cls,
        builtin_dir: Path | None,
        user_dir: Path | None,
    ) -> "AdventureSourceRegistry":
        """Build the standard builtin+user registry from two directories."""

        registry = cls()
        if builtin_dir is not None:
            registry.register(AdventureSource("builtin", "", AdventureBundleLoader(builtin_dir)))
        if user_dir is not None:
            registry.register(AdventureSource("user", "", AdventureBundleLoader(user_dir)))
        return registry

    # ---- 查询 ----------------------------------------------------------

    def sources(self) -> tuple[AdventureSource, ...]:
        return tuple(self._sources)

    def source_for(self, kind: str, source_id: str = "") -> AdventureSource | None:
        for source in self._sources:
            if source.kind == kind and source.source_id == (source_id or ""):
                return source
        return None

    def user_source(self) -> AdventureSource | None:
        return self.source_for("user")

    def __iter__(self) -> Iterator[AdventureSource]:
        return iter(self._sources)

    # ---- 聚合 ----------------------------------------------------------

    def list(
        self, locale: str = "",
    ) -> list[tuple[LoadedAdventureBundle, AdventureSource]]:
        """All bundles across sources, stable order (builtin, user, plugin)."""

        ordered = sorted(
            self._sources,
            key=lambda source: (SOURCE_KINDS.index(source.kind), source.source_id),
        )
        aggregated: list[tuple[LoadedAdventureBundle, AdventureSource]] = []
        for source in ordered:
            for bundle in source.loader.list(locale):
                aggregated.append((bundle, source))
        return aggregated

    def resolve(
        self,
        adventure_id: str,
        locale: str = "",
        *,
        source_kind: str = "",
        source_id: str = "",
    ) -> tuple[LoadedAdventureBundle, AdventureSource]:
        """Resolve one adventure; explicit conflict when it spans sources.

        ``source_kind``/``source_id``（例如来自存档 binding v2）指定来源时直接
        在该来源内解析；未指定而 id 跨来源时抛
        :class:`AdventureSourceConflict`——绝不静默挑选。
        """

        wanted = str(adventure_id or "")
        if source_kind:
            source = self.source_for(source_kind, source_id)
            if source is None:
                raise AdventureBundleError(
                    f"adventure source not available: {source_kind}:{source_id}"
                )
            return source.loader.resolve(wanted, locale), source
        matches: list[tuple[LoadedAdventureBundle, AdventureSource]] = []
        for source in self._sources:
            try:
                bundle = source.loader.resolve(wanted, locale)
            except AdventureBundleError:
                continue
            matches.append((bundle, source))
        if not matches:
            raise AdventureBundleError(f"adventure package does not exist: {wanted}")
        if len(matches) > 1:
            raise AdventureSourceConflict(wanted, [source for _, source in matches])
        return matches[0]

    def conflicts(self, locale: str = "") -> dict[str, list[str]]:
        """adventure_ids present in more than one source (for UI surfacing)."""

        seen: dict[str, list[str]] = {}
        for bundle, source in self.list(locale):
            seen.setdefault(bundle.manifest.adventure_id, []).append(source.label())
        return {
            adventure_id: labels
            for adventure_id, labels in seen.items()
            if len(labels) > 1
        }


__all__ = [
    "SOURCE_KINDS",
    "AdventureSource",
    "AdventureSourceConflict",
    "AdventureSourceRegistry",
]
