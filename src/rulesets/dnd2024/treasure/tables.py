"""Random treasure table schema, validation, and collision-free selection.

The table is the only owning module for ``kind: "treasure_table"`` entities.
Selection is a pure function over an already-rolled integer; callers own the
RNG so the same event batch can be replayed without re-rolling.

Hard boundaries:

- Results describe *what* was rolled. They never write inventory, equipment,
  or economy state.  awarding still goes through the existing reward intent /
  proposal / GM gate.
- Entries must cover ``1..sides`` exactly with no gaps and no overlaps.
- Nested table references are validated for existence, depth and cycles at
  bundle load time; invalid tables fail closed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.engine.dice_rng import parse_dice_formula

_TABLE_REF_PREFIX = "treasure_table:"
_ITEM_REF_PREFIX = "item:"
_RESULT_KEYS = frozenset({"table_ref", "items", "coins_gp"})
_MAX_NESTING_DEPTH = 8


class TreasureTableError(ValueError):
    """A treasure table is structurally invalid; the bundle must fail closed."""


def canonical_table_ref(raw: Any) -> str:
    """Return ``treasure_table:<id>`` or raise for a non-canonical reference."""

    value = str(raw or "").strip()
    if not value.startswith(_TABLE_REF_PREFIX):
        raise TreasureTableError(
            f"treasure table ref must start with {_TABLE_REF_PREFIX}: {value!r}"
        )
    table_id = value.removeprefix(_TABLE_REF_PREFIX)
    if not table_id or ":" in table_id:
        raise TreasureTableError(f"treasure table ref is invalid: {value!r}")
    return value


def _table_id_from_ref(ref: str) -> str:
    return ref.removeprefix(_TABLE_REF_PREFIX)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TreasureTableError(f"{field} must be an integer")
    return value


@dataclass(frozen=True, slots=True)
class TreasureEntry:
    from_value: int
    to_value: int
    result: dict[str, Any]

    def contains(self, roll_value: int) -> bool:
        return self.from_value <= roll_value <= self.to_value


@dataclass(frozen=True, slots=True)
class TreasureTable:
    table_id: str
    roll: str
    sides: int
    name: str
    entries: tuple[TreasureEntry, ...]

    @property
    def ref(self) -> str:
        return f"{_TABLE_REF_PREFIX}{self.table_id}"

    def entry_for(self, roll_value: int) -> TreasureEntry:
        if isinstance(roll_value, bool) or not isinstance(roll_value, int):
            raise TreasureTableError("treasure roll must be an integer")
        for entry in self.entries:
            if entry.contains(roll_value):
                return entry
        raise TreasureTableError(
            f"treasure roll {roll_value} falls outside table {self.table_id}"
        )

    def resolve(self, roll_value: int) -> dict[str, Any]:
        """Pure selection: no RNG, no state writes."""

        return deepcopy(self.entry_for(roll_value).result)


@dataclass(frozen=True, slots=True)
class Dnd2024TreasureCatalog:
    tables: dict[str, TreasureTable]

    @classmethod
    def from_bundle(cls, bundle: Any) -> "Dnd2024TreasureCatalog":
        return validate_treasure_tables(bundle)

    def get(self, raw_ref: Any) -> TreasureTable | None:
        ref = canonical_table_ref(raw_ref)
        return self.tables.get(_table_id_from_ref(ref))

    def list_tables(self) -> list[dict[str, str]]:
        return [
            {"table_ref": table.ref, "name": table.name}
            for table in sorted(self.tables.values(), key=lambda item: item.table_id)
        ]

    def resolve(self, raw_ref: Any, roll_value: int) -> dict[str, Any]:
        table = self.get(raw_ref)
        if table is None:
            raise TreasureTableError(f"treasure table is not defined: {raw_ref!r}")
        return table.resolve(roll_value)


def resolve_table(
    tables: Mapping[str, TreasureTable], table_id: str, roll_value: int,
) -> dict[str, Any]:
    """Resolve one already-rolled value against ``table_id``."""

    table = tables.get(table_id)
    if table is None:
        raise TreasureTableError(f"treasure table is not defined: {table_id!r}")
    return table.resolve(roll_value)


def _parse_roll(raw: Any, table_id: str) -> tuple[str, int]:
    formula = str(raw or "").strip()
    try:
        normalized, count, sides, modifier = parse_dice_formula(formula)
    except ValueError as exc:
        raise TreasureTableError(f"{table_id}.roll: {exc}") from None
    if count != 1 or modifier != 0:
        raise TreasureTableError(
            f"{table_id}.roll must be a single die with no modifier: {formula!r}"
        )
    return normalized, sides


def _validate_result(
    raw: Any, *, table_id: str, item_exists: Callable[[str], bool],
    table_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TreasureTableError(f"{table_id}.entries.result must be an object")
    keys = set(raw)
    unknown = sorted(keys - _RESULT_KEYS)
    if unknown:
        raise TreasureTableError(
            f"{table_id}.entries.result has unknown key: {unknown[0]}"
        )
    if len(keys) != 1:
        raise TreasureTableError(
            f"{table_id}.entries.result must use exactly one result shape"
        )
    shape = next(iter(keys))
    if shape == "table_ref":
        ref = raw["table_ref"]
        try:
            canonical = canonical_table_ref(ref)
        except TreasureTableError as exc:
            raise TreasureTableError(f"{table_id}.entries.result: {exc}") from None
        if _table_id_from_ref(canonical) not in table_ids:
            raise TreasureTableError(
                f"{table_id}.entries.result references unknown table: {canonical}"
            )
        return {"table_ref": canonical}
    if shape == "items":
        entries = raw["items"]
        if not isinstance(entries, list) or not entries:
            raise TreasureTableError(f"{table_id}.entries.result.items must be a non-empty array")
        validated: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"item_ref", "qty"}:
                raise TreasureTableError(
                    f"{table_id}.entries.result.items entries require item_ref and qty"
                )
            ref = entry["item_ref"]
            if not isinstance(ref, str) or not ref.startswith(_ITEM_REF_PREFIX):
                raise TreasureTableError(
                    f"{table_id}.entries.result item_ref must start with {_ITEM_REF_PREFIX}"
                )
            item_id = ref.removeprefix(_ITEM_REF_PREFIX)
            if not item_id or ":" in item_id or not item_exists(item_id):
                raise TreasureTableError(
                    f"{table_id}.entries.result references unknown item: {ref}"
                )
            qty = _integer(entry["qty"], f"{table_id}.entries.result item qty")
            if qty < 1:
                raise TreasureTableError(
                    f"{table_id}.entries.result item qty must be a positive integer"
                )
            validated.append({"item_ref": ref, "qty": qty})
        return {"items": validated}
    coins = _integer(raw["coins_gp"], f"{table_id}.entries.result coins_gp")
    if coins < 0:
        raise TreasureTableError(
            f"{table_id}.entries.result coins_gp must not be negative"
        )
    return {"coins_gp": coins}


def _parse_entries(
    raw: Any, *, table_id: str, sides: int,
    item_exists: Callable[[str], bool], table_ids: set[str],
) -> tuple[TreasureEntry, ...]:
    if not isinstance(raw, list) or not raw:
        raise TreasureTableError(f"{table_id}.entries must be a non-empty array")
    parsed: list[TreasureEntry] = []
    for raw_entry in raw:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {"from", "to", "result"}:
            raise TreasureTableError(
                f"{table_id}.entries entries require exactly from, to and result"
            )
        start = _integer(raw_entry["from"], f"{table_id}.entries.from")
        end = _integer(raw_entry["to"], f"{table_id}.entries.to")
        if start < 1 or end > sides or start > end:
            raise TreasureTableError(
                f"{table_id}.entries interval {start}-{end} is outside 1-{sides}"
            )
        parsed.append(TreasureEntry(
            from_value=start,
            to_value=end,
            result=_validate_result(
                raw_entry["result"],
                table_id=table_id,
                item_exists=item_exists,
                table_ids=table_ids,
            ),
        ))
    parsed.sort(key=lambda entry: entry.from_value)
    expected = 1
    for entry in parsed:
        if entry.from_value != expected:
            raise TreasureTableError(
                f"{table_id}.entries must cover 1-{sides} without gaps or overlaps"
            )
        expected = entry.to_value + 1
    if expected != sides + 1:
        raise TreasureTableError(
            f"{table_id}.entries must cover 1-{sides} without gaps or overlaps"
        )
    return tuple(parsed)


def _validate_nesting(tables: dict[str, TreasureTable]) -> None:
    def visit(table_id: str, stack: set[str], depth: int) -> None:
        if depth > _MAX_NESTING_DEPTH:
            raise TreasureTableError(
                f"treasure table nesting exceeds {_MAX_NESTING_DEPTH} levels: {table_id}"
            )
        if table_id in stack:
            raise TreasureTableError(f"treasure table nesting cycle: {table_id}")
        stack.add(table_id)
        for entry in tables[table_id].entries:
            nested = entry.result.get("table_ref")
            if nested:
                visit(_table_id_from_ref(nested), stack, depth + 1)
        stack.remove(table_id)

    for table_id in tables:
        visit(table_id, set(), 1)


def validate_treasure_tables(bundle: Any) -> Dnd2024TreasureCatalog:
    """Validate every bundle treasure table or raise ``TreasureTableError``."""

    raw_tables = bundle.list("treasure_table")
    if not raw_tables:
        return Dnd2024TreasureCatalog(tables={})
    table_ids = {str(raw.get("id") or "") for raw in raw_tables}
    tables: dict[str, TreasureTable] = {}
    for raw in raw_tables:
        if not isinstance(raw, dict):
            raise TreasureTableError("treasure table entity must be an object")
        table_id = str(raw.get("id") or "")
        if not table_id:
            raise TreasureTableError("treasure table id must not be empty")
        if raw.get("kind") != "treasure_table":
            raise TreasureTableError(f"{table_id}.kind must be treasure_table")
        if raw.get("schema_version") != 1:
            raise TreasureTableError(
                f"{table_id}.schema_version is unsupported: {raw.get('schema_version')!r}"
            )
        formula, sides = _parse_roll(raw.get("roll"), table_id)
        entries = _parse_entries(
            raw.get("entries"),
            table_id=table_id,
            sides=sides,
            item_exists=lambda item_id: bundle.get("item", item_id) is not None,
            table_ids=table_ids,
        )
        tables[table_id] = TreasureTable(
            table_id=table_id,
            roll=formula,
            sides=sides,
            name=str(raw.get("name") or table_id),
            entries=entries,
        )
    _validate_nesting(tables)
    return Dnd2024TreasureCatalog(tables=tables)
