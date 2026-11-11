# -*- coding: utf-8 -*-
"""Windows 便携版启动器与本安装后端之间的本机握手。

启动器（``src/launcher``）需要回答三个问题，而端口上的公开元数据回答不了：

1. **端口上的服务是不是本安装的后端？** 两份独立安装（不同数据目录）可以先后占用
   同一个端口，任何本地服务也都能伪造 ``/api/config`` 里的 ``server_version``。
   启动器在数据目录里维护一把随机实例密钥（``_launcher/instance.json``，换了安装
   位置会重新生成），后端用它对启动器给的随机 nonce 做 HMAC：只有能读到本安装
   数据目录的后端才答得出来。证明里同时绑定 boot_id 与 pid，启动器据此拿到的是
   **经过认证的** 进程身份，而不是对方自报的数字。
2. **怎样让一个不是自己启动的本安装后端退出？** 更新切换前，启动器用同一把密钥对
   停止请求签名；后端走与重启接口相同的优雅关闭路径，只是不再 execv。
3. **后端进程退出是崩溃还是自重启？** Windows 上 ``os.execv`` 会换 PID、原进程以 0
   退出。后端在 execv 之前写下重启意图（旧 pid + boot_id），启动器据此在限定时间内
   等新后端证明归属后重新接管，而不是把正常重启当崩溃关掉窗口。

密钥只由启动器创建。没有密钥（Docker、源码运行）时握手端点一律回答不可用，启动器
因此不会接管任何它无法证明归属的实例（fail closed）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger("trpg")

LAUNCHER_DIR_NAME = "_launcher"
INSTANCE_FILE_NAME = "instance.json"
RESTART_INTENT_FILE_NAME = "restart-intent.json"
#: 握手端点在没有实例密钥时返回的错误码。
LAUNCHER_LINK_UNAVAILABLE = "LAUNCHER_LINK_UNAVAILABLE"

# 两种证明用不同前缀，任何一种都不能被拿去冒充另一种。
_IDENTITY_LABEL = "diceframe-launcher-identity/v1"
_STOP_LABEL = "diceframe-launcher-stop/v1"
_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_NONCE_PATTERN = re.compile(r"^[0-9a-f]{32,128}$")
_PROOF_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def launcher_dir(data_dir: Path) -> Path:
    return Path(data_dir) / LAUNCHER_DIR_NAME


def read_instance_key(data_dir: Path) -> bytes | None:
    """读取启动器写下的实例密钥；缺失或格式不对一律视为没有（fail closed）。

    每次请求都重新读：启动器可能在后端运行期间生成或轮换密钥（例如孤儿后端由
    上一个启动器留下，新启动器刚创建密钥），不能缓存启动时的状态。
    """

    try:
        payload = json.loads((launcher_dir(data_dir) / INSTANCE_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = payload.get("key") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
        return None
    return bytes.fromhex(key)


def valid_nonce(value: object) -> bool:
    return isinstance(value, str) and bool(_NONCE_PATTERN.fullmatch(value))


def _mac(key: bytes, *parts: str) -> str:
    return hmac.new(key, "\n".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


def identity_proof(key: bytes, nonce: str, boot_id: str, pid: int) -> str:
    """证明「本后端能读到这份安装的数据目录」，并认证自己的 boot_id 与 pid。"""

    return _mac(key, _IDENTITY_LABEL, nonce, boot_id, str(int(pid)))


def stop_proof(key: bytes, nonce: str, boot_id: str) -> str:
    """启动器对「停止这一次启动的后端」的签名；绑定 boot_id，换了进程就作废。"""

    return _mac(key, _STOP_LABEL, nonce, boot_id)


def proof_matches(expected: str, provided: object) -> bool:
    if not isinstance(provided, str) or not _PROOF_PATTERN.fullmatch(provided):
        return False
    return hmac.compare_digest(expected, provided)


def record_restart_intent(data_dir: Path, *, boot_id: str, pid: int) -> None:
    """execv 之前留下「这是自重启」的记录，供启动器区分重启与崩溃。

    写不出来只记日志：最坏情况是启动器把这次重启按异常退出处理（与旧行为一致），
    不能因此阻止重启。
    """

    if read_instance_key(data_dir) is None:
        return  # 不是由便携版启动器托管（Docker、源码运行）：没人读它。
    target = launcher_dir(data_dir) / RESTART_INTENT_FILE_NAME
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "pid": int(pid),
                    "boot_id": str(boot_id),
                    "requested_at": int(time.time()),
                }
            ),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except OSError as exc:
        logger.warning("无法记录重启意图，启动器可能把这次重启当成异常退出：%s", exc)
        try:
            temporary.unlink()
        except OSError:
            pass
