"""验证 /api/config 公开视图携带服务器版本元数据。

移动端客户端在登录前只能匿名探测 /api/config，靠其中的 server_version 与
min_client_version 做“App 过旧 / 服务器过旧”双向兼容提示；字段缺失时
客户端按版本未知放行（兼容旧服务器），因此这里只保证字段存在且取自
src.version，不约束具体数值。
"""

from __future__ import annotations

import web_server
from src.version import MIN_CLIENT_VERSION, __version__


def test_public_config_exposes_server_version_metadata():
    public = web_server._public_config()

    assert public["server_version"] == __version__
    assert public["min_client_version"] == MIN_CLIENT_VERSION


def test_min_client_version_is_semver_like():
    # 客户端按点分数字段比较（对齐 version_below 语义），非空且可解析才有效
    parts = str(MIN_CLIENT_VERSION).strip().lstrip("vV").split(".")
    assert len(parts) >= 2
    for chunk in parts:
        assert chunk.split("-")[0].isdigit()
