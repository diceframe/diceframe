"""Unique Adventure resolver across every source (FIX-02 §4.1, 母方案 §31/§72).

修复前的双 resolver：

```text
WebAPI          → AdventureSourceRegistry（builtin / user / plugin）
D&D Runtime     → 自己 new AdventureBundleLoader(adventures_dir)
Create Game     → registry
binding 校验     → 各自的 loader
```

后果是同一个 adventure_id 在不同路径上可能解析到不同的包。本模块把它收敛成
**唯一入口**：

```text
AdventureResolver
├─ builtin       随应用发布的内置冒险（runtime 数据目录里带 marker 的同步副本，
│                 缺失时回退到随应用发布的 templates/adventures）
├─ user          用户独立导入的 standalone 冒险（数据目录里不带 marker 的目录）
└─ plugin:<id>   content-pack 模组声明的冒险包（declared-only）
```

硬规则：

- **来源互斥**：内置包的同步副本带 ``.diceframe-builtin`` 标记，归属 builtin；
  用户来源排除这些目录（否则同一个 id 会假冲突）。
- **不猜来源**：``resolve`` 未指定来源而 id 跨来源时抛
  :class:`AdventureSourceConflict`；带 ``source_kind``/``source_id`` 时只在该来源内
  解析，**不回退**。
- **legacy binding 兼容**（§4.2/§4.3）：只有 ``adventure_id`` 的旧绑定按"当前唯一"
  解析；出现冲突即 fail closed（recovery UI），绝不静默挑一个，也绝不改存档。
- 解析入口对 :class:`AdventureBundleLoader` 保持鸭子兼容（``list``/``resolve``/
  ``load``/``adventures_dir``），存量调用方零改动即可切到 resolver。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.adventures.bundle import (
    AdventureBundleError,
    AdventureBundleLoader,
    LoadedAdventureBundle,
)
from src.adventures.registry import (
    AdventureSource,
    AdventureSourceConflict,
    AdventureSourceRegistry,
)

# binding 的基础身份字段（v1 形状）与 FIX-02 新增的来源身份字段。
BASE_BINDING_FIELDS = (
    "adventure_id", "version", "format", "content_digest", "world_id",
)
SOURCE_BINDING_FIELDS = ("source_kind", "source_id")


@dataclass(frozen=True)
class AdventureResolution:
    """One resolved adventure package plus the source that owns it."""

    bundle: LoadedAdventureBundle
    source_kind: str
    source_id: str = ""

    @property
    def adventure_id(self) -> str:
        return self.bundle.manifest.adventure_id

    def source_label(self) -> str:
        return f"{self.source_kind}:{self.source_id}" if self.source_id else self.source_kind

    def binding(self, world_id: str) -> dict[str, str]:
        """Source-aware binding shape persisted on the game instance (FIX-02 §4.2).

        来源身份为空（单目录/standalone resolver）时退回旧 5 字段形状：空来源不是
        身份，写进存档只会制造"看起来有来源"的假象。
        """

        value = dict(self.bundle.binding(world_id))
        if str(self.source_kind or "").strip():
            value["source_kind"] = str(self.source_kind)
            value["source_id"] = str(self.source_id or "")
        return value


def binding_source(binding: Mapping[str, Any] | None) -> tuple[str, str]:
    """Read the declared source identity of a binding (``("", "")`` when absent)."""

    if not isinstance(binding, Mapping):
        return "", ""
    if "source_kind" not in binding or "source_id" not in binding:
        return "", ""
    return (
        str(binding.get("source_kind") or ""),
        str(binding.get("source_id") or ""),
    )


def is_source_aware_binding(binding: Mapping[str, Any] | None) -> bool:
    return bool(binding_source(binding)[0])


def binding_matches(
    binding: Mapping[str, Any] | None,
    expected: Mapping[str, Any] | None,
) -> bool:
    """Whether a persisted binding still describes the resolved package (§4.2).

    基础身份（adventure_id / version / format / content_digest / world_id）必须一致；
    带来源身份的绑定还必须来源一致——同一个 adventure_id 从**另一个来源**解析出来
    不算匹配（否则"明确来源"形同虚设）。旧绑定没有来源字段，只比基础身份。
    """

    if not isinstance(binding, Mapping) or not isinstance(expected, Mapping):
        return False
    for key in BASE_BINDING_FIELDS:
        if str(binding.get(key) or "") != str(expected.get(key) or ""):
            return False
    kind, source_id = binding_source(binding)
    if not kind:
        return True
    return (kind, source_id) == (
        str(expected.get("source_kind") or ""),
        str(expected.get("source_id") or ""),
    )


class AdventureResolver:
    """The single resolution entry point for builtin / user / plugin adventures."""

    def __init__(self, registry: AdventureSourceRegistry) -> None:
        self._registry = registry

    # ---- construction --------------------------------------------------

    @classmethod
    def from_directories(
        cls,
        builtin_dir: Path | None,
        user_dir: Path | None,
    ) -> "AdventureResolver":
        """Build the standard builtin+user resolver (disjoint sources).

        内置包在数据目录里的同步副本（带 marker）归 builtin；用户来源排除它们。
        runtime 数据目录存在时优先用它的内置副本（``sync_adventure_catalog``
        保证与发布包一致），否则回退到随应用发布的目录。
        """

        registry = AdventureSourceRegistry()
        builtin_source = cls._builtin_source(builtin_dir, user_dir)
        if builtin_source is not None:
            root, directory_ids = builtin_source
            registry.register(AdventureSource(
                "builtin", "",
                AdventureBundleLoader(root, allowed_directory_ids=directory_ids),
            ))
        if user_dir is not None and Path(user_dir).is_dir():
            registry.register(AdventureSource(
                "user", "",
                AdventureBundleLoader(user_dir, include_builtin_directories=False),
            ))
        return cls(registry)

    @staticmethod
    def _builtin_source(
        builtin_dir: Path | None, user_dir: Path | None,
    ) -> tuple[Path, frozenset[str] | None] | None:
        """Where builtin packages physically live, and which dirs belong to it.

        runtime 数据目录里带 ``.diceframe-builtin`` 标记的目录才是内置包；
        未标记的同级目录是用户包，必须留给 user 来源。
        """

        from src.adventures.catalog import is_builtin_adventure_directory

        if user_dir is not None and Path(user_dir).is_dir():
            marked = tuple(sorted(
                path.name for path in Path(user_dir).iterdir()
                if path.is_dir() and is_builtin_adventure_directory(path)
            ))
            if marked:
                return Path(user_dir), frozenset(marked)
        if builtin_dir is not None and Path(builtin_dir).is_dir():
            return Path(builtin_dir), None
        return None

    @classmethod
    def single_directory(
        cls, directory: Path | None, *, source_kind: str = "user",
    ) -> "AdventureResolver":
        """Standalone resolver over one directory (tests / standalone loads).

        ``source_kind`` 标注这个目录在语义上属于哪个来源（随应用发布的
        ``templates/adventures`` 用 ``builtin``；数据目录用 ``user``）。
        """

        registry = AdventureSourceRegistry()
        if directory is not None and Path(directory).is_dir():
            registry.register(
                AdventureSource(source_kind, "", AdventureBundleLoader(directory))
            )
        return cls(registry)

    # ---- loader-compatible surface --------------------------------------

    @property
    def registry(self) -> AdventureSourceRegistry:
        return self._registry

    @property
    def adventures_dir(self) -> Path:
        """The writable standalone/user directory (import/copy/update target)."""

        source = self._registry.source_for("user")
        if source is not None:
            return source.loader.adventures_dir
        source = self._registry.source_for("builtin")
        if source is not None:
            return source.loader.adventures_dir
        return Path(".")

    def source_for(self, kind: str, source_id: str = "") -> AdventureSource | None:
        return self._registry.source_for(kind, source_id)

    def sources(self) -> tuple[AdventureSource, ...]:
        return self._registry.sources()

    def sync_plugin_sources(self, sources: list[AdventureSource]) -> None:
        self._registry.sync_plugin_sources(sources)

    def conflicts(self, locale: str = "") -> dict[str, list[str]]:
        return self._registry.conflicts(locale)

    def list(self, locale: str = "") -> list[LoadedAdventureBundle]:
        return [bundle for bundle, _source in self._registry.list(locale)]

    def list_resolutions(self, locale: str = "") -> list[AdventureResolution]:
        return [
            AdventureResolution(bundle, source.kind, source.source_id)
            for bundle, source in self._registry.list(locale)
        ]

    def load(self, directory_id: str, locale: str = "") -> LoadedAdventureBundle:
        """Load one directory from the user source (or builtin as a fallback)."""

        for kind in ("user", "builtin"):
            source = self._registry.source_for(kind)
            if source is None:
                continue
            return source.loader.load(directory_id, locale)
        raise AdventureBundleError(f"adventure package does not exist: {directory_id}")

    def resolve(
        self,
        adventure_id: str,
        locale: str = "",
        *,
        source_kind: str = "",
        source_id: str = "",
    ) -> LoadedAdventureBundle:
        return self.resolve_with_source(
            adventure_id, locale, source_kind=source_kind, source_id=source_id,
        ).bundle

    def resolve_with_source(
        self,
        adventure_id: str,
        locale: str = "",
        *,
        source_kind: str = "",
        source_id: str = "",
    ) -> AdventureResolution:
        """Resolve one adventure; explicit source wins, otherwise unique-or-conflict."""

        bundle, source = self._registry.resolve(
            str(adventure_id or ""), locale,
            source_kind=str(source_kind or ""), source_id=str(source_id or ""),
        )
        return AdventureResolution(bundle, source.kind, source.source_id)

    # ---- source-aware binding ------------------------------------------

    def resolve_binding(
        self,
        binding: Mapping[str, Any] | None,
        locale: str = "",
    ) -> AdventureResolution:
        """Resolve the package a persisted binding refers to (§4.2).

        - 带来源身份（新绑定）：只在该来源内解析，缺来源即 fail closed；
        - 旧绑定（只有 adventure_id）：当前唯一 → 解析；跨来源重名 → 抛
          :class:`AdventureSourceConflict`，由调用方转成 recovery 状态。
        """

        adventure_id = ""
        if isinstance(binding, Mapping):
            adventure_id = str(binding.get("adventure_id") or "")
        if not adventure_id:
            raise AdventureBundleError("adventure binding has no adventure_id")
        source_kind, source_id = binding_source(binding)
        return self.resolve_with_source(
            adventure_id, locale, source_kind=source_kind, source_id=source_id,
        )


__all__ = [
    "BASE_BINDING_FIELDS",
    "SOURCE_BINDING_FIELDS",
    "AdventureResolution",
    "AdventureResolver",
    "AdventureSourceConflict",
    "binding_matches",
    "binding_source",
    "is_source_aware_binding",
]
