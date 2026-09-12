"""Lorebook 表模型 —— 与 SCHEMA / user_version 迁移产出的 on-disk 结构一一对应。

模型只服务于查询构造；建表与迁移仍由 ``store.SCHEMA`` 和 ``src.migrations.lorebook``
负责，因此字段类型只需要保证读写时的取值/类型语义与存量一致。``worlds`` 上的
外键级联由 SCHEMA 声明和 ``PRAGMA foreign_keys=ON`` 承担，模型层刻意不声明
关系字段，避免 peewee 引入第二条删除路径。
"""

from __future__ import annotations

from peewee import (
    BooleanField,
    CharField,
    IntegerField,
    Model,
    SQL,
    TextField,
)

from src.db.peewee_bridge import SharedConnectionSqliteDatabase

database = SharedConnectionSqliteDatabase()


class World(Model):
    id = CharField(primary_key=True)
    name = CharField()
    description = TextField(default="")
    language = CharField(default="zh-CN")
    author = CharField(default="")
    version = CharField(default="1.0")
    created_at = CharField(constraints=[SQL("DEFAULT (datetime('now'))")])
    updated_at = CharField(constraints=[SQL("DEFAULT (datetime('now'))")])

    class Meta:
        database = database
        table_name = "worlds"


class LorebookEntry(Model):
    id = CharField(primary_key=True)
    world_id = CharField()
    name = CharField()
    type = CharField(default="other")
    keywords = TextField(default="[]")
    content = TextField(default="")
    unreliable = BooleanField(default=False)
    sync_on_enter = BooleanField(default=False)
    tier = CharField(
        default="background",
        constraints=[SQL("CHECK(tier IN ('core','background','archived'))")],
    )
    triggers_recursive = TextField(default="[]")
    visible_to = TextField(default="[]")
    is_constant = BooleanField(default=False)
    match_mode = CharField(
        default="any",
        constraints=[SQL("CHECK(match_mode IN ('any','all','not_any','not_all'))")],
    )
    sticky = IntegerField(default=0)
    cooldown = IntegerField(default=0)
    delay = IntegerField(default=0)
    order = IntegerField(default=100, column_name="order")
    probability = IntegerField(default=100)
    group = CharField(default="", column_name="group")
    group_weight = IntegerField(default=1)
    connected_to = TextField(default="[]")
    source_plugin = CharField(default="")
    created_at = CharField(constraints=[SQL("DEFAULT (datetime('now'))")])
    updated_at = CharField(constraints=[SQL("DEFAULT (datetime('now'))")])

    class Meta:
        database = database
        table_name = "lorebook_entries"
