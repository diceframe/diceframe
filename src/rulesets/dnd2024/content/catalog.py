"""D&D module content catalog loader (DNDMOD-01, 母方案 §10/§29/§111).

把多个来源的 D&D catalog 数据装载为一个 **source-aware** 的内容目录：

```text
core SRD bundle          （templates/rulesets/dnd2024_srd 之类）
module ruleset catalogs  （content-pack 声明的 packs/dnd2024/*）
adventure local catalog  （Adventure Bundle 自带内容）
```

硬规则（母方案 §10）：

- **source-aware**：每条记录归属其来源；同一 canonical ref
  （source|kind|id）在装载期重复 = 校验错误（不静默覆盖）；
- **跨来源同名允许**：引用带 source，天然不歧义；
- 解析经 MOD-05 的 ``ContentRefChain``：adventure-local → module → core
  优先序由调用方按序给出，显式引用直接定位；
- 所有记录经 DNDMOD-00 契约校验（fail closed），坏记录让整个来源装载失败。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.content_modules.refs import ContentRef, ContentRefChain, parse_content_ref
from src.rulesets.dnd2024.content.contracts import (
    CATALOG_KINDS,
    CatalogContractError,
    validate_encounter_profile,
    validate_item_record,
    validate_monster_profile,
    validate_npc_statblock,
)

_VALIDATORS = {
    "monster": validate_monster_profile,
    "npc_statblock": validate_npc_statblock,
    "item": validate_item_record,
    "encounter_profile": validate_encounter_profile,
}

_RECORD_ID_FIELDS = {
    "monster": "profile_id",
    "npc_statblock": "statblock_id",
    "item": "item_id",
    "encounter_profile": "encounter_id",
}


class CatalogLoadError(ValueError):
    """A catalog source is invalid: fail closed (whole source rejected)."""


@dataclass(frozen=True)
class CatalogSource:
    """One loaded catalog source: label + records keyed (kind, id)."""

    label: str
    records: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def lookup(self, kind: str, record_id: str) -> dict[str, Any] | None:
        return self.records.get((kind, record_id))


def load_catalog_dir(path: Path, *, source_label: str) -> CatalogSource:
    """Load one catalog directory (``*.json``, each record self-describing)."""

    root = Path(path)
    records: dict[tuple[str, str], dict[str, Any]] = {}
    if not root.is_dir():
        raise CatalogLoadError(f"catalog source is not a directory: {source_label}")
    for json_path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CatalogLoadError(f"catalog JSON invalid: {json_path.name}: {exc}") from exc
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict):
                raise CatalogLoadError(f"catalog entry must be an object: {json_path.name}")
            entry = dict(entry)
            kind = str(entry.pop("kind", "") or "")
            if kind not in _VALIDATORS:
                raise CatalogLoadError(
                    f"catalog entry kind is invalid: {kind!r} ({json_path.name})"
                )
            try:
                validated = _VALIDATORS[kind](entry)
            except CatalogContractError as exc:
                raise CatalogLoadError(f"catalog record invalid ({json_path.name}): {exc}") from exc
            record_id = str(validated[_RECORD_ID_FIELDS[kind]])
            key = (kind, record_id)
            if key in records:
                # 母方案 §10：same source+kind+id duplicate → validation error。
                raise CatalogLoadError(
                    f"catalog duplicate ref in {source_label}: {kind}:{record_id}"
                )
            records[key] = validated
    return CatalogSource(label=source_label, records=records)


def validate_catalog_record(kind: str, record: Any) -> dict[str, Any]:
    """Validate one in-memory catalog record of ``kind`` (fail closed).

    FIX-03 §5.1：adventure-local 内容要作为链首来源时，必须过与模块目录同一套
    契约校验；不合契约的实体（v1 bundle 信封形状）由调用方跳过。
    """

    validator = _VALIDATORS.get(kind)
    if validator is None:
        raise CatalogLoadError(f"unknown catalog kind: {kind!r}")
    if not isinstance(record, Mapping):
        raise CatalogLoadError(f"catalog entry must be an object: {kind}")
    payload = {key: value for key, value in record.items() if key != "kind"}
    return validator(payload)


class DndContentCatalog:
    """Aggregated, source-aware view over loaded catalog sources."""

    def __init__(self, sources: Sequence[CatalogSource]) -> None:
        self._sources: list[CatalogSource] = list(sources)
        self._validate_cross_source_duplicates()

    @staticmethod
    def _validate_cross_source_duplicates() -> None:
        return None  # 跨来源同名合法（引用带 source）；同源重复在装载期已拒绝。

    def sources(self) -> tuple[CatalogSource, ...]:
        return tuple(self._sources)

    def resolve(self, ref: ContentRef) -> dict[str, Any] | None:
        """Direct source-aware lookup for one validated ref.

        v1 裸 ``kind:id``（非 explicit）由调用方归属**它自己声明的默认来源**
        （母方案 §9：跨来源引用必须显式，不猜、不静默替换）；因此这里只做
        label 精确匹配，链序回溯留给显式声明来源的调用方自行决定。
        """

        for source in self._sources:
            if source.label == ref.source:
                return source.lookup(ref.kind, ref.id)
        return None

    def lookup_chain(self, *, local_label: str) -> ContentRefChain:
        """Build a MOD-05 resolution chain: adventure-local → module → core.

        ``local_label`` 是 adventure 本地目录的来源标签（链首）；其余按调用方
        给出的装载顺序（module 优先于 core）。
        """

        ordered = sorted(
            self._sources,
            key=lambda source: 0 if source.label == local_label else 1,
        )

        def _lookup_for(source: CatalogSource):
            def lookup(kind: str, record_id: str) -> dict[str, Any] | None:
                return source.lookup(kind, record_id)
            return lookup

        return ContentRefChain([
            (source.label, _lookup_for(source)) for source in ordered
        ])

    def resolve_ref(
        self, raw: Any, *, default_source: str,
    ) -> dict[str, Any] | None:
        """Convenience: parse + resolve one raw ref against the whole catalog."""

        ref = parse_content_ref(raw, default_source=default_source)
        return self.resolve(ref)

    def count(self, kind: str) -> int:
        if kind not in CATALOG_KINDS:
            raise CatalogLoadError(f"unknown catalog kind: {kind!r}")
        return sum(1 for source in self._sources for key in source.records if key[0] == kind)

    def records_for(self, kind: str) -> dict[str, dict[str, Any]]:
        """Flat ``{record_id: record}`` view of one kind, in chain order.

        FIX-03 §5.3：运行时需要按 kind 枚举目录内容（例如把模组的
        ``encounter_profile`` 投影成战斗遭遇预设）。同名跨来源时**链序在前者胜出**，
        与 v1 裸 ``kind:id`` 引用的解析语义一致（显式来源引用仍走 resolve）。
        """

        if kind not in CATALOG_KINDS:
            raise CatalogLoadError(f"unknown catalog kind: {kind!r}")
        flat: dict[str, dict[str, Any]] = {}
        for source in self._sources:
            for (record_kind, record_id), record in source.records.items():
                if record_kind == kind and record_id not in flat:
                    flat[record_id] = record
        return flat


def _source_from_mapping(label: str, records: Mapping[str, Mapping[str, Any]]) -> CatalogSource:
    """Build one source from an in-memory {kind: {id: record}} mapping (tests/轻量路径)。

    记录同样过契约校验，保证链上没有未验证数据。
    """

    normalized: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, by_id in records.items():
        if kind not in _VALIDATORS:
            raise CatalogLoadError(f"catalog entry kind is invalid: {kind!r}")
        for record_id, record in by_id.items():
            # 与文件装载一致：record 可以自带 ``kind``（文件里的自描述条目），
            # 契约本身不接受该字段。
            payload = (
                {key: value for key, value in record.items() if key != "kind"}
                if isinstance(record, Mapping) else record
            )
            validated = _VALIDATORS[kind](payload)
            key = (kind, str(record_id))
            if key in normalized:
                raise CatalogLoadError(f"catalog duplicate ref in {label}: {key[0]}:{key[1]}")
            normalized[key] = validated
    return CatalogSource(label=label, records=normalized)


def catalog_from_sources(
    sources: Sequence[tuple[str, Mapping[str, Mapping[str, Any]]]],
) -> DndContentCatalog:
    """Build a catalog from ordered in-memory sources (first = adventure local).

    同一 (label, kind, id) 出现两次 = 装载错误（母方案 §10），跨来源同名合法。
    """

    seen: set[tuple[str, str, str]] = set()
    loaded: list[CatalogSource] = []
    for label, records in sources:
        source = _source_from_mapping(label, records)
        for (kind, record_id) in source.records:
            identity = (label, kind, record_id)
            if identity in seen:
                raise CatalogLoadError(
                    f"catalog duplicate ref in {label}: {kind}:{record_id}"
                )
            seen.add(identity)
        loaded.append(source)
    return DndContentCatalog(loaded)


__all__ = [
    "CatalogLoadError",
    "CatalogSource",
    "DndContentCatalog",
    "catalog_from_sources",
    "load_catalog_dir",
]
