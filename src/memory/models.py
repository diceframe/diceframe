"""Memory 表模型 —— 与 SCHEMA / user_version 迁移产出的 on-disk 结构一一对应。

模型只服务于查询构造；建表与迁移仍由 ``store.SCHEMA`` 和 ``src.migrations.memory``
负责。``memory_entries`` 与 ``memory_economy_deliveries`` 的外键/唯一约束由
数据库层承担，模型层不声明关系字段，保持与存量删除/恢复语义一一对应。
"""

from __future__ import annotations

from peewee import (
    AutoField,
    CharField,
    CompositeKey,
    FloatField,
    IntegerField,
    Model,
    SQL,
    TextField,
)

from src.db.peewee_bridge import SharedConnectionSqliteDatabase

database = SharedConnectionSqliteDatabase()


class MemoryEntry(Model):
    id = AutoField()
    game_key = CharField()
    entity = CharField()
    relation = CharField()
    value = TextField()
    confidence = FloatField(default=1.0)
    status = CharField(
        default="active",
        constraints=[SQL("CHECK(status IN ('active','pending','forgotten'))")],
    )
    source_round = IntegerField(null=True)
    embedding = TextField(null=True)
    created_at = CharField(constraints=[SQL("DEFAULT (datetime('now'))")])
    updated_at = CharField(constraints=[SQL("DEFAULT (datetime('now'))")])

    class Meta:
        database = database
        table_name = "memory_entries"


class MemoryEconomyDelivery(Model):
    game_key = CharField()
    delivery_id = CharField()
    before_state = TextField()
    after_state = TextField()
    status = CharField(
        default="applied",
        constraints=[SQL("CHECK(status IN ('applied','reversed'))")],
    )
    created_at = CharField()
    reversed_at = CharField(null=True)

    class Meta:
        database = database
        table_name = "memory_economy_deliveries"
        primary_key = CompositeKey("game_key", "delivery_id")
