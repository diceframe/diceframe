"""记忆 delta 存储 —— 将 LLM 输出的 memory_delta 写入 SQLite，处理冲突消解。"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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

# 表升级：为没有 embedding 列的老表添加
_MIGRATE_EMBEDDING = """
ALTER TABLE memory_entries ADD COLUMN embedding TEXT;
"""


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
        # 尝试迁移旧表（添加 embedding 列）
        try:
            self._conn.execute(_MIGRATE_EMBEDDING)
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # 列已存在
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    async def edit_entry(self, game_key: str, entry_id: int, updates: dict) -> bool:
        """Edit one active memory while preserving game ownership."""
        allowed = {key: updates[key] for key in ("entity", "relation", "value", "confidence") if key in updates}
        if not allowed or not self._conn:
            return False
        allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
        allowed["embedding"] = None
        assignments = ", ".join(f"{key}=?" for key in allowed)
        async with self._lock:
            cursor = self._conn.execute(
                f"UPDATE memory_entries SET {assignments} WHERE id=? AND game_key=? AND status='active'",
                (*allowed.values(), int(entry_id), str(game_key)),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    async def forget_entry(self, game_key: str, entry_id: int) -> bool:
        if not self._conn:
            return False
        async with self._lock:
            cursor = self._conn.execute(
                "UPDATE memory_entries SET status='forgotten', updated_at=? WHERE id=? AND game_key=? AND status='active'",
                (datetime.now(timezone.utc).isoformat(), int(entry_id), str(game_key)),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    # ---- Delta 处理 ----

    async def apply_delta(self, game_key: str, delta: dict, round_number: int) -> None:
        """应用 memory_delta，根据冲突消解规则处理 add/update/forget。"""
        gk = str(game_key)
        now = datetime.now(timezone.utc).isoformat()
        new_ids: list[int] = []

        async with self._lock:
            for item in delta.get("add", []):
                eid = self._insert_or_update(gk, item, round_number, now, force_add=True)
                if eid:
                    new_ids.append(eid)

            for item in delta.get("update", []):
                self._insert_or_update(gk, item, round_number, now, force_add=False)

            for item in delta.get("forget", []):
                confidence = float(item.get("confidence", 1.0))
                entity = item.get("entity", "")
                relation = item.get("relation", "")
                if confidence >= 0.5:
                    self._conn.execute(
                        "UPDATE memory_entries SET status='forgotten', "
                        "updated_at=? WHERE game_key=? AND entity=? AND relation=? AND status='active'",
                        (now, gk, entity, relation),
                    )

            self._conn.commit()

        # 标记待处理 embedding（由外部在 async 上下文中调用 flush_pending_embeddings）
        if new_ids and self.embedding_client:
            self._pending_embed_ids.extend(new_ids)

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
        existing = self._conn.execute(
            "SELECT id, entity, relation, value, confidence, status FROM memory_entries "
            "WHERE game_key=? AND entity=? AND relation=? AND status='active'",
            (gk, entity, relation),
        ).fetchone()

        if existing:
            if force_add:
                existing_value = existing["value"]
                existing_entity = existing["entity"]
                existing_relation = existing["relation"]
                if (existing_entity == entity and existing_relation == relation
                        and existing_value == value):
                    self._conn.execute(
                        "UPDATE memory_entries SET source_round=?, updated_at=? "
                        "WHERE id=?", (round_num, now, existing["id"]))
                    return existing["id"]
                self._conn.execute(
                    "UPDATE memory_entries SET status='forgotten', updated_at=? "
                    "WHERE id=?", (now, existing["id"]))
                cur = self._conn.execute(
                    "INSERT INTO memory_entries "
                    "(game_key, entity, relation, value, confidence, status, source_round) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (gk, entity, relation, value, confidence, status, round_num))
                new_id = cur.lastrowid
            else:
                self._conn.execute(
                    "UPDATE memory_entries SET value=?, confidence=?, status=?, "
                    "source_round=?, updated_at=? WHERE id=?",
                    (value, confidence, status, round_num, now, existing["id"]))
        else:
            cur = self._conn.execute(
                "INSERT INTO memory_entries "
                "(game_key, entity, relation, value, confidence, status, source_round) "
                "VALUES (?,?,?,?,?,?,?)",
                (gk, entity, relation, value, confidence, status, round_num))
            new_id = cur.lastrowid
        return new_id

    # ---- 召回 ----

    def recall(self, game_key: str, keywords: list[str], limit: int = 10, offset: int = 0) -> list[dict]:
        """根据关键词召回相关记忆。"""
        gk = str(game_key)
        if not keywords:
            return []
        clauses = " OR ".join(["entity LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        params.insert(0, gk)
        rows = self._conn.execute(
            f"SELECT * FROM memory_entries WHERE game_key=? AND status='active' AND ({clauses}) "
            "ORDER BY confidence DESC, updated_at DESC LIMIT ? OFFSET ?",
            tuple(params + [max(1, int(limit)), max(0, int(offset))]),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_entries(self, game_key: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """List active memories for management UIs without weakening recall semantics."""
        if not self._conn:
            return []
        rows = self._conn.execute(
            "SELECT * FROM memory_entries WHERE game_key=? AND status='active' "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (str(game_key), max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_entries(self, game_key: str, keyword: str = "") -> int:
        """统计活跃记忆总数（可按 entity 关键词过滤，与 recall 口径一致）。"""
        if not self._conn:
            return 0
        gk = str(game_key)
        if keyword:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_entries "
                "WHERE game_key=? AND status='active' AND entity LIKE ?",
                (gk, f"%{keyword}%"),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_entries "
                "WHERE game_key=? AND status='active'",
                (gk,),
            ).fetchone()
        return row["cnt"] if row else 0

    def recall_by_text(self, game_key: str, text: str, limit: int = 10) -> list[dict]:
        """根据文本内容召回匹配的记忆（检查 entity 是否出现在 text 中）。"""
        gk = str(game_key)
        rows = self._conn.execute(
            "SELECT * FROM memory_entries WHERE game_key=? AND status='active' "
            "ORDER BY updated_at DESC LIMIT 500",
            (gk,),
        ).fetchall()
        matched = [dict(r) for r in rows if r["entity"] and r["entity"] in text]
        return matched[:limit]

    # ---- 向量召回 ----

    def recall_by_vector(self, game_key: str, query_embedding: list[float],
                         limit: int = 10) -> list[dict]:
        """基于向量余弦相似度的记忆召回。"""
        from src.memory.embedding import cosine_similarity

        gk = str(game_key)
        rows = self._conn.execute(
            "SELECT * FROM memory_entries WHERE game_key=? AND status='active' "
            "AND embedding IS NOT NULL ORDER BY updated_at DESC LIMIT 500",
            (gk,),
        ).fetchall()

        scored: list[tuple[float, dict]] = []
        for row in rows:
            entry = dict(row)
            emb_json = entry.get("embedding")
            if not emb_json:
                continue
            try:
                emb = json.loads(emb_json)
                sim = cosine_similarity(query_embedding, emb)
                if sim > 0.3:  # 相似度阈值
                    scored.append((sim, entry))
            except Exception:
                pass

        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    async def store_embedding(self, entry_id: int, embedding: list[float]) -> None:
        """为指定记忆条目存储向量。"""
        async with self._lock:
            self._conn.execute(
                "UPDATE memory_entries SET embedding=? WHERE id=?",
                (json.dumps(embedding), entry_id),
            )
            self._conn.commit()

    async def _embed_new_entries(self, entry_ids: list[int]) -> None:
        """为新增的记忆条目异步计算 embedding。"""
        if not self.embedding_client:
            return
        for eid in entry_ids:
            try:
                row = self._conn.execute(
                    "SELECT entity, relation, value FROM memory_entries WHERE id=?",
                    (eid,),
                ).fetchone()
                if not row:
                    continue
                text = f"{row['entity']}: {row['relation']} → {row['value']}"
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
                        self._conn.execute(
                            "UPDATE memory_entries SET embedding=? WHERE id=?",
                            (json.dumps(embeddings[i]), entry["id"]),
                        )
                        count += 1
                self._conn.commit()
            logger.info("批量 embedding 完成: %d/%d", count, len(entries))
            return count
        except Exception:
            logger.exception("批量 embedding 失败")
            return 0

    def get_unembedded_count(self, game_key: str) -> int:
        """获取尚未向量化的记忆数量。"""
        gk = str(game_key)
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_entries "
            "WHERE game_key=? AND status='active' AND embedding IS NULL",
            (gk,),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_unembedded(self, game_key: str, limit: int = 100) -> list[dict]:
        """获取尚未向量化的记忆条目。"""
        gk = str(game_key)
        rows = self._conn.execute(
            "SELECT * FROM memory_entries "
            "WHERE game_key=? AND status='active' AND embedding IS NULL "
            "LIMIT ?", (gk, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_unembedded(self, limit: int = 100) -> list[dict]:
        """获取所有游戏中尚未向量化的记忆条目。"""
        rows = self._conn.execute(
            "SELECT * FROM memory_entries "
            "WHERE status='active' AND embedding IS NULL "
            "LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
