"""扫码配对：短时效、一次性的移动端登录配对码。

契约要点：

- 配对码只在 owner 会话（或免密服务器）主动请求时签发，TTL 很短、只能兑换一次。
- 服务端只保存 sha256 摘要，明文配对码只在签发响应里出现一次。
- 兑换成功签发的是一枚设备令牌（见 device_tokens.py），不是访问密码本身：
  访问密码在服务端只有 PBKDF2 哈希，而且主密码本就不该分发到每台手机上。
- 兑换端点匿名可达，因此必须与 /api/login 同级限流，并同样写入登录审计。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass

from src.webui.device_tokens import DeviceTokenStore
from src.localization.messages import t


logger = logging.getLogger("trpg")


# 配对码只用于「GM 当面拿手机扫屏幕」，2 分钟足够完成一次扫码；
# 过期后 Web 端自行重新签发，不做续期。
PAIRING_TTL_SECONDS = 120
# 同时存活的配对码上限：正常只有 1 个，这里只防内存被刷爆。
MAX_PENDING_CODES = 32
# 去掉 0/O/1/I/L 等易混字符，方便 GM 在扫码失败时口述或手输。
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8


@dataclass(frozen=True)
class PairingCode:
    code: str
    expires_at: float
    expires_in: int


class PairingCodeStore:
    """内存态配对码登记处；进程重启即全部作废（与 SSE 票据同语义）。"""

    def __init__(
        self,
        ttl_seconds: int = PAIRING_TTL_SECONDS,
        max_pending: int = MAX_PENDING_CODES,
    ) -> None:
        self.ttl_seconds = max(10, int(ttl_seconds))
        self.max_pending = max(1, int(max_pending))
        self._codes: dict[str, float] = {}

    @staticmethod
    def _digest(code: str) -> str:
        return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()

    def _purge(self, now: float) -> None:
        for digest in [d for d, expires in self._codes.items() if expires <= now]:
            self._codes.pop(digest, None)
        while len(self._codes) >= self.max_pending:
            oldest = min(self._codes, key=lambda key: self._codes[key])
            self._codes.pop(oldest, None)

    def issue(self) -> PairingCode:
        now = time.monotonic()
        self._purge(now)
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        self._codes[self._digest(code)] = now + self.ttl_seconds
        return PairingCode(
            code=code,
            # 墙上时钟用于前端倒计时展示；过期判定始终用单调时钟。
            expires_at=time.time() + self.ttl_seconds,
            expires_in=self.ttl_seconds,
        )

    def consume(self, code: str) -> bool:
        """兑换一次配对码；无效、过期或已用过都返回 False。"""
        text = str(code or "").strip()
        if not text:
            return False
        now = time.monotonic()
        self._purge(now)
        expires_at = self._codes.pop(self._digest(text), None)
        return expires_at is not None and expires_at > now

    def revoke_all(self) -> None:
        """作废所有待兑换的配对码。"""
        self._codes.clear()

    @property
    def pending_count(self) -> int:
        return len(self._codes)


class PairingService:
    """签发 / 兑换配对码，并把兑换结果写进与 /api/login 同一份登录审计。"""

    def __init__(
        self,
        devices: DeviceTokenStore,
        *,
        store: PairingCodeStore | None = None,
        audit: object | None = None,
    ) -> None:
        self._devices = devices
        self._store = store or PairingCodeStore()
        self._audit = audit

    @property
    def store(self) -> PairingCodeStore:
        return self._store

    def issue(self) -> dict:
        code = self._store.issue()
        return {
            "ok": True,
            "code": code.code,
            "expires_in": code.expires_in,
            "expires_at": code.expires_at,
        }

    def claim(self, code: str, ip: str, label: str = "", install_id: str = "") -> tuple[dict, int]:
        granted = self._store.consume(code)
        self._record(ip, granted)
        if not granted:
            return (
                {
                    "ok": False,
                    "error": t("webui.pairing.code_invalid_or_expired"),
                    "error_code": "PAIRING_CODE_INVALID",
                },
                401,
            )
        # A valid pairing code is the owner's own authorisation. If this
        # install is already bound to an older pairing, that pairing is the
        # same app install pairing again (e.g. after the old token was lost):
        # revoke it so one install keeps exactly one live device token.
        replaced = self._devices.install_id_holder(install_id) if install_id else ""
        if replaced:
            self._devices.revoke(replaced)
            logger.warning(
                "配对接管安装标识 install_id=%s：已吊销旧设备 %s（ip=%s）",
                install_id, replaced, ip or "unknown",
            )
        token, device = self._devices.issue(label, install_id)
        return (
            {
                "ok": True,
                # 明文令牌只在这一次响应里出现；服务端此后只留摘要。
                "device_token": token,
                "device_id": device["id"],
                "label": device["label"],
                "install_id": device.get("install_id", ""),
                **({"replaced_device_id": replaced} if replaced else {}),
            },
            200,
        )

    def revoke_all(self) -> int:
        """服务器从免密切换到有访问密码时，作废免密期发出的全部配对凭据。

        免密期间 ``POST /api/pairing`` 对任何能连上的人开放，那时签发的设备
        令牌等价于「谁连得上谁就是 owner」。设置访问密码这一步必须把它们一起
        收回，否则锁门之前发出去的钥匙还留在外面，而且它们在锁门后依然有效。

        已有密码时再改密码不走这里：设备令牌是与访问密码平级的独立凭据，
        设置页有单独的逐台 / 全部吊销入口（见 device_tokens.py）。
        """
        self._store.revoke_all()
        return self._devices.revoke_all()

    def _record(self, ip: str, success: bool) -> None:
        if not self._audit:
            return
        try:
            self._audit.record(ip or "unknown", success)
        except Exception:
            # 审计落盘失败不能挡住正常配对，异常仍进服务日志。
            logger.exception("配对兑换记录写入失败")
