"""peewee 查询构造层与存量裸 sqlite3 连接的桥接。

存储类（LorebookStore / MemoryStore）继续拥有连接、事务与迁移生命周期，
本类只把 peewee 编译出的 SQL 路由到该连接上执行。约束：

- schema 真相来源仍是各存储的 SCHEMA 与 user_version 迁移；模型层不做建表/迁移。
- 提交仍由存储类显式 ``conn.commit()``；不使用 peewee 的事务与自动提交语义。
- 同一模型家族的 ``database`` 是模块级单例，同一时刻只应有一个打开的存储实例
  （现行装配即进程级单例）；重复 ``attach`` 以最后一次绑定为准。
"""

from __future__ import annotations

import sqlite3

from peewee import InterfaceError, SqliteDatabase


class SharedConnectionSqliteDatabase(SqliteDatabase):
    """把 peewee 查询绑定到外部持有的单个 sqlite3 连接上。"""

    def __init__(self) -> None:
        super().__init__(None)
        self._shared_conn: sqlite3.Connection | None = None

    def attach(self, conn: sqlite3.Connection) -> None:
        """绑定当前打开的裸连接（连接生命周期归存储类所有）。"""
        self._shared_conn = conn

    def detach(self) -> None:
        self._shared_conn = None

    def _connect(self) -> sqlite3.Connection:
        if self._shared_conn is None:
            raise InterfaceError("no sqlite3 connection attached")
        return self._shared_conn

    # peewee 默认按线程隔离连接；绕过线程局部状态，全部查询走共享连接，
    # 与存储类"单连接 + 锁"的存量并发语义保持一致。
    def connection(self) -> sqlite3.Connection:
        return self._connect()

    def cursor(self, named_cursor=None):  # noqa: D102 - 与父类签名一致
        return self._connect().cursor()
