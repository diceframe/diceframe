"""记忆 delta 存储 —— 将 LLM 输出的 memory_delta 写入 SQLite，处理冲突消解。

查询构造走 peewee（src.memory.models）；连接、SCHEMA 建表、user_version 迁移、
asyncio.Lock 与事务提交仍由本类持有，行为契约与迁移前一致。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from functools import reduce
from operator import or_
from pathlib import Path

from src.memory.models import MemoryEconomyDelivery, MemoryEntry
from src.memory.models import database as _models_database
from src.migrations.memory import migrate as migrate_memory

logger = logging.getLogger("trpg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key TEXT NOT NULL,
    entity TEXT NOT NULL,
    relation TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    status TEXT DEFAULT 'active'
        CHECK(status IN ('active','pending','forgotten')),
    source_round INTEGER,
    embedding TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_game_key ON memory_entries(game_key);
CREATE INDEX IF NOT EXISTS idx_memory_entity  ON memory_entries(game_key, entity);
CREATE INDEX IF NOT EXISTS idx_memory_status  ON memory_entries(game_key, status);

PRAGMA journal_mode=WAL;
"""

_ACTIVE = MemoryEntry.status == "active"


def _active_query(game_key: str):
    return MemoryEntry.select().where(
        (MemoryEntry.game_key == str(game_key)) & _ACTIVE,
    )


class MemoryStore:
    """长期记忆 SQLite 存储，处理 memory_delta 的冲突消解。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self.embedding_client = None  # EmbeddingClient 实例（可选）
        self._pending_embed_ids: list[int] = []

    def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False 仅在单线程 asyncio 环境中安全；
        # 若未来引入多线程请改用 aiosqlite。
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        migrate_memory(self._conn)
        self._conn.commit()
        _models_database.attach(self._conn)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            _models_database.detach()

    async def edit_entry(self, game_key: str, entry_id: int, updates: dict) -> bool:
        """Edit one active memory while preserving game ownership."""
        allowed = {key: updates[key] for key in ("entity", "relation", "value", "confidence") if key in updates}
        if not allowed or not self._conn:
            return False
        allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
        allowed["embedding"] = None
        async with self._lock:
            rowcount = MemoryEntry.update(**allowed).where(
                (MemoryEntry.id == int(entry_id))
                & (MemoryEntry.game_key == str(game_key))
                & _ACTIVE,
            ).execute()
            self._conn.commit()
        return rowcount == 1

    async def forget_entry(self, game_key: str, entry_id: int) -> bool:
        if not self._conn:
            return False
        async with self._lock:
            rowcount = MemoryEntry.update(
                status="forgotten",
                updated_at=datetime.now(timezone.utc).isoformat(),
            ).where(
                (MemoryEntry.id == int(entry_id))
                & (MemoryEntry.game_key == str(game_key))
                & _ACTIVE,
            ).execute()
            self._conn.commit()
        return rowcount == 1

    async def clear_game(self, game_key: str) -> int:
        """Remove every memory entry owned by one game session.

        Reset/restart intentionally reuse the public ``game_key`` for the
        save, so merely clearing the in-memory GameInstance is not enough:
        the durable memory projection would otherwise be recalled by the new
        run.  This operation is scoped to one exact key and is safe to call
        when no entries exist.
        """
        if not self._conn:
            return 0
        async with self._lock:
            rowcount = MemoryEntry.delete().where(
                MemoryEntry.game_key == str(game_key),
            ).execute()
            MemoryEconomyDelivery.delete().where(
                MemoryEconomyDelivery.game_key == str(game_key),
            ).execute()
            self._conn.commit()
        return int(rowcount or 0)

    # ---- Delta 处理 ----

    async def apply_delta(self, game_key: str, delta: dict, round_number: int) -> None:
        """应用 memory_delta，根据冲突消解规则处理 add/update/forget。"""
        if self._conn is None:
            raise RuntimeError("memory store is not open")
        connection = self._conn
        gk = str(game_key)
        now = datetime.now(timezone.utc).isoformat()
        new_ids: list[int] = []

        async with self._lock:
            try:
                new_ids = self._apply_delta_locked(gk, delta, round_number, now)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        # 标记待处理 embedding（由外部在 async 上下文中调用 flush_pending_embeddings）
        if new_ids and self.embedding_client:
            self._pending_embed_ids.extend(new_ids)

    async def apply_economy_delta(
        self,
        game_key: str,
        delivery_id: str,
        delta: dict,
        round_number: int,
    ) -> None:
        """Apply one transaction-associated delta with a durable inverse journal."""

        if self._conn is None:
            raise RuntimeError("memory store is not open")
        gk = str(game_key)
        effect_id = str(delivery_id)
        if not effect_id:
            raise ValueError("economy memory delivery id is required")
        now = datetime.now(timezone.utc).isoformat()
        keys = self._delta_keys(delta)
        new_ids: list[int] = []
        async with self._lock:
            delivered = MemoryEconomyDelivery.select().where(
                (MemoryEconomyDelivery.game_key == gk)
                & (MemoryEconomyDelivery.delivery_id == effect_id),
            ).exists()
            if delivered:
                return
            try:
                before = self._snapshot_keys(gk, keys)
                new_ids = self._apply_delta_locked(
                    gk, delta, int(round_number), now,
                )
                after = self._snapshot_keys(gk, keys)
                MemoryEconomyDelivery.insert(
                    game_key=gk,
                    delivery_id=effect_id,
                    before_state=json.dumps(before, ensure_ascii=False, sort_keys=True),
                    after_state=json.dumps(after, ensure_ascii=False, sort_keys=True),
                    status="applied",
                    created_at=now,
                ).execute()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if new_ids and self.embedding_client:
            self._pending_embed_ids.extend(new_ids)

    async def reverse_economy_delta(
        self,
        game_key: str,
        delivery_id: str,
    ) -> bool:
        """Reverse one delivered economy delta without clobbering newer facts."""

        if self._conn is None:
            raise RuntimeError("memory store is not open")
        gk = str(game_key)
        effect_id = str(delivery_id)
        async with self._lock:
            record = MemoryEconomyDelivery.get_or_none(
                (MemoryEconomyDelivery.game_key == gk)
                & (MemoryEconomyDelivery.delivery_id == effect_id),
            )
            if record is None:
                return False
            if record.status == "reversed":
                return True
            before_rows = {
                int(item["id"]): item
                for item in json.loads(record.before_state or "[]")
            }
            after_rows = {
                int(item["id"]): item
                for item in json.loads(record.after_state or "[]")
            }
            try:
                for entry_id, after in after_rows.items():
                    current = MemoryEntry.get_or_none(
                        (MemoryEntry.id == entry_id)
                        & (MemoryEntry.game_key == gk),
                    )
                    if current is None or not self._same_memory_state(
                        dict(current.__data__), after,
                    ):
                        continue
                    before = before_rows.get(entry_id)
                    if before is None:
                        MemoryEntry.delete().where(
                            (MemoryEntry.id == entry_id)
                            & (MemoryEntry.game_key == gk),
                        ).execute()
                    else:
                        self._restore_memory_row(before)
                for entry_id, before in before_rows.items():
                    if entry_id in after_rows:
                        continue
                    still_exists = MemoryEntry.select().where(
                        MemoryEntry.id == entry_id,
                    ).exists()
                    if not still_exists:
                        self._insert_memory_row(before)
                MemoryEconomyDelivery.update(
                    status="reversed",
                    reversed_at=datetime.now(timezone.utc).isoformat(),
                ).where(
                    (MemoryEconomyDelivery.game_key == gk)
                    & (MemoryEconomyDelivery.delivery_id == effect_id)
                    & (MemoryEconomyDelivery.status == "applied"),
                ).execute()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return True

    def _apply_delta_locked(
        self,
        gk: str,
        delta: dict,
        round_number: int,
        now: str,
    ) -> list[int]:
        new_ids: list[int] = []
        for item in delta.get("add", []):
            entry_id = self._insert_or_update(
                gk, item, round_number, now, force_add=True,
            )
            if entry_id:
                new_ids.append(entry_id)
        for item in delta.get("update", []):
            entry_id = self._insert_or_update(
                gk, item, round_number, now, force_add=False,
            )
            if entry_id:
                new_ids.append(entry_id)
        for item in delta.get("forget", []):
            if not isinstance(item, dict):
                continue
            confidence = float(item.get("confidence", 1.0))
            entity = item.get("entity", "")
            relation = item.get("relation", "")
            if confidence >= 0.5:
                MemoryEntry.update(
                    status="forgotten", updated_at=now,
                ).where(
                    (MemoryEntry.game_key == gk)
                    & (MemoryEntry.entity == entity)
                    & (MemoryEntry.relation == relation)
                    & _ACTIVE,
                ).execute()
        return new_ids

    @staticmethod
    def _delta_keys(delta: dict) -> list[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for operation in ("add", "update", "forget"):
            for raw in delta.get(operation, []):
                item = raw
                if isinstance(raw, str):
                    text = raw.strip()
                    item = {"entity": text, "relation": "记录"}
                if not isinstance(item, dict):
                    continue
                entity = str(item.get("entity") or "")
                relation = str(item.get("relation") or "")
                if entity.strip():
                    keys.add((entity, relation))
        return sorted(keys)

    def _snapshot_keys(
        self,
        game_key: str,
        keys: list[tuple[str, str]],
    ) -> list[dict]:
        rows: list[dict] = []
        for entity, relation in keys:
            matches = MemoryEntry.select().where(
                (MemoryEntry.game_key == game_key)
                & (MemoryEntry.entity == entity)
                & (MemoryEntry.relation == relation),
            ).order_by(MemoryEntry.id)
            rows.extend(dict(row.__data__) for row in matches)
        return rows

    @staticmethod
    def _same_memory_state(current: dict, expected: dict) -> bool:
        fields = (
            "game_key", "entity", "relation", "value", "confidence",
            "status", "source_round",
        )
        return all(current.get(field) == expected.get(field) for field in fields)

    def _restore_memory_row(self, row: dict) -> None:
        MemoryEntry.update(
            game_key=row["game_key"], entity=row["entity"], relation=row["relation"],
            value=row["value"], confidence=row["confidence"], status=row["status"],
            source_round=row.get("source_round"), embedding=row.get("embedding"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        ).where(MemoryEntry.id == row["id"]).execute()

    def _insert_memory_row(self, row: dict) -> None:
        MemoryEntry.insert(
            id=row["id"], game_key=row["game_key"], entity=row["entity"],
            relation=row["relation"], value=row["value"],
            confidence=row["confidence"], status=row["status"],
            source_round=row.get("source_round"), embedding=row.get("embedding"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        ).execute()

    def _insert_or_update(self, gk: str, item: dict, round_num: int,
                          now: str, force_add: bool) -> int | None:
        """插入或更新记忆，返回新条目的 id（如果是新插入的话）。"""
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return None
            item = {
                "entity": text,
                "relation": "记录",
                "value": text,
                "confidence": 1.0,
            }
        if not isinstance(item, dict):
            return None
        entity = item.get("entity", "")
        relation = item.get("relation", "")
        value = item.get("value", "")
        if not (str(entity).strip() and str(value).strip()):
            logger.debug("skip empty memory item: entity=%r value=%r", entity, value)
            return None
        confidence = float(item.get("confidence", 1.0))
        status = "active"  # low-confidence via recall sort
        new_id = None

        # 检查是否存在 active 条目
        existing = MemoryEntry.select(
            MemoryEntry.id, MemoryEntry.entity, MemoryEntry.relation,
            MemoryEntry.value, MemoryEntry.confidence, MemoryEntry.status,
        ).where(
            (MemoryEntry.game_key == gk)
            & (MemoryEntry.entity == entity)
            & (MemoryEntry.relation == relation)
            & _ACTIVE,
        ).first()

        if existing:
            if force_add:
                if (existing.entity == entity and existing.relation == relation
                        and existing.value == value):
                    MemoryEntry.update(
                        source_round=round_num, updated_at=now,
                    ).where(MemoryEntry.id == existing.id).execute()
                    return existing.id
                MemoryEntry.update(
                    status="forgotten", updated_at=now,
                ).where(MemoryEntry.id == existing.id).execute()
                new_id = MemoryEntry.insert(
                    game_key=gk, entity=entity, relation=relation, value=value,
                    confidence=confidence, status=status, source_round=round_num,
                ).execute()
            else:
                MemoryEntry.update(
                    value=value, confidence=confidence, status=status,
                    source_round=round_num, updated_at=now,
                ).where(MemoryEntry.id == existing.id).execute()
        else:
            new_id = MemoryEntry.insert(
                game_key=gk, entity=entity, relation=relation, value=value,
                confidence=confidence, status=status, source_round=round_num,
            ).execute()
        return new_id

    # ---- 召回 ----

    def recall(self, game_key: str, keywords: list[str], limit: int = 10, offset: int = 0) -> list[dict]:
        """根据关键词召回相关记忆。"""
        gk = str(game_key)
        if not keywords:
            return []
        conditions = reduce(
            or_,
            (MemoryEntry.entity.ilike(f"%{kw}%") for kw in keywords),
        )
        rows = (
            _active_query(gk)
            .where(conditions)
            .order_by(MemoryEntry.confidence.desc(), MemoryEntry.updated_at.desc())
            .limit(max(1, int(limit)))
            .offset(max(0, int(offset)))
        )
        return [dict(r.__data__) for r in rows]

    def search_active_by_terms(self, game_key: str, terms: list[str],
                               limit: int = 1000) -> list[dict]:
        """按词项对 entity/relation/value 做 LIKE 粗筛（recall 增强通道）。"""
        gk = str(game_key)
        if not terms:
            return []
        conditions = reduce(or_, (
            (
                MemoryEntry.entity.ilike(f"%{term}%")
                | MemoryEntry.relation.ilike(f"%{term}%")
                | MemoryEntry.value.ilike(f"%{term}%")
            )
            for term in terms
        ))
        rows = (
            _active_query(gk)
            .where(conditions)
            .order_by(MemoryEntry.updated_at.desc())
            .limit(max(1, int(limit)))
        )
        return [dict(r.__data__) for r in rows]

    def list_entries(self, game_key: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """List active memories for management UIs without weakening recall semantics."""
        if not self._conn:
            return []
        rows = (
            _active_query(game_key)
            .order_by(MemoryEntry.updated_at.desc())
            .limit(max(1, int(limit)))
            .offset(max(0, int(offset)))
        )
        return [dict(row.__data__) for row in rows]

    def count_entries(self, game_key: str, keyword: str = "") -> int:
        """统计活跃记忆总数（可按 entity 关键词过滤，与 recall 口径一致）。"""
        if not self._conn:
            return 0
        query = _active_query(game_key)
        if keyword:
            query = query.where(MemoryEntry.entity.ilike(f"%{keyword}%"))
        return query.count()

    def recall_by_text(self, game_key: str, text: str, limit: int = 10) -> list[dict]:
        """根据文本内容召回匹配的记忆（检查 entity 是否出现在 text 中）。"""
        gk = str(game_key)
        rows = (
            _active_query(gk)
            .order_by(MemoryEntry.updated_at.desc())
            .limit(500)
        )
        matched = [
            dict(r.__data__) for r in rows if r.entity and r.entity in text
        ]
        return matched[:limit]

    # ---- 向量召回 ----

    def recall_by_vector(self, game_key: str, query_embedding: list[float],
                         limit: int = 10) -> list[dict]:
        """基于向量余弦相似度的记忆召回。"""
        from src.memory.embedding import cosine_similarity

        gk = str(game_key)
        rows = (
            _active_query(gk)
            .where(MemoryEntry.embedding.is_null(False))
            .order_by(MemoryEntry.updated_at.desc())
            .limit(500)
        )

        scored: list[tuple[float, dict]] = []
        for row in rows:
            entry = dict(row.__data__)
            emb_json = entry.get("embedding")
            if not emb_json:
                continue
            try:
                emb = json.loads(emb_json)
                sim = cosine_similarity(query_embedding, emb)
                if sim > 0.3:  # 相似度阈值
                    scored.append((sim, entry))
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.debug("忽略无效记忆向量: entry_id=%s", entry.get("id"), exc_info=True)

        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    async def store_embedding(self, entry_id: int, embedding: list[float]) -> None:
        """为指定记忆条目存储向量。"""
        async with self._lock:
            MemoryEntry.update(
                embedding=json.dumps(embedding),
            ).where(MemoryEntry.id == entry_id).execute()
            self._conn.commit()

    async def _embed_new_entries(self, entry_ids: list[int]) -> None:
        """为新增的记忆条目异步计算 embedding。"""
        if not self.embedding_client:
            return
        for eid in entry_ids:
            try:
                row = MemoryEntry.get_or_none(MemoryEntry.id == eid)
                if not row:
                    continue
                text = f"{row.entity}: {row.relation} → {row.value}"
                emb = await self.embedding_client.embed(text)
                if emb:
                    await self.store_embedding(eid, emb)
            except Exception:
                logger.exception("新记忆 embedding 失败: id=%d", eid)

    async def flush_pending_embeddings(self) -> int:
        """消费 _pending_embed_ids 队列，在 async 上下文中批量计算 embedding。返回成功数量。"""
        if not self.embedding_client:
            return 0
        ids_to_process: list[int] = []
        async with self._lock:
            ids_to_process, self._pending_embed_ids = self._pending_embed_ids, []
        if ids_to_process:
            await self._embed_new_entries(ids_to_process)
        return len(ids_to_process)

    async def embed_all_pending(self, game_key: str | None = None) -> int:
        """为未向量化的记忆批量计算 embedding。game_key=None 时不限游戏。"""
        if not self.embedding_client:
            return 0
        entries = self.get_unembedded(game_key, limit=100)
        if not entries:
            return 0
        texts = [f"{e['entity']}: {e['relation']} → {e['value']}" for e in entries]
        try:
            embeddings = await self.embedding_client.embed_batch(texts)
            if not embeddings:
                return 0
            count = 0
            async with self._lock:
                for i, entry in enumerate(entries):
                    if i < len(embeddings) and embeddings[i]:
                        MemoryEntry.update(
                            embedding=json.dumps(embeddings[i]),
                        ).where(MemoryEntry.id == entry["id"]).execute()
                        count += 1
                self._conn.commit()
            logger.info("批量 embedding 完成: %d/%d", count, len(entries))
            return count
        except Exception:
            logger.exception("批量 embedding 失败")
            return 0

    def get_unembedded_count(self, game_key: str) -> int:
        """获取尚未向量化的记忆数量。"""
        return (
            _active_query(game_key)
            .where(MemoryEntry.embedding.is_null())
            .count()
        )

    def get_unembedded(self, game_key: str, limit: int = 100) -> list[dict]:
        """获取尚未向量化的记忆条目。"""
        rows = (
            _active_query(game_key)
            .where(MemoryEntry.embedding.is_null())
            .limit(limit)
        )
        return [dict(r.__data__) for r in rows]

    def get_all_unembedded(self, limit: int = 100) -> list[dict]:
        """获取所有游戏中尚未向量化的记忆条目。"""
        rows = (
            MemoryEntry.select()
            .where(_ACTIVE & MemoryEntry.embedding.is_null())
            .limit(limit)
        )
        return [dict(r.__data__) for r in rows]
