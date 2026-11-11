# -*- coding: utf-8 -*-
"""连接安全（Web Transport）服务：状态查询、切换事务与自签证书管理。

修改、签发与重新生成操作都走这里，保证：
- prepare 只验证 / 生成候选，不切换当前服务；
- activate 只接受一次性 preparation token，避免刷新或重试重复执行；
- 失败保留当前可用模式，不写入不可启动的配置。
"""

from __future__ import annotations

import os
import secrets as secrets_module
import ssl
import time
import asyncio
import aiohttp
from pathlib import Path
from typing import Any, Callable

from src.web_transport import (
    AcmeCertificateProvider,
    AcmeIssueRequest,
    AcmeOrderIdentifier,
    TLS_MODE_LETS_ENCRYPT,
    TLS_MODE_OFF,
    TLS_MODE_SELF_SIGNED,
    CertificateError,
    CertificateStore,
    SelfSignedCertificateProvider,
    ServerTransport,
    WebTransportConfig,
    parse_web_transport,
    validate_activation,
)
from src.web_transport.certificates.acme_client import AcmeError
from src.localization.messages import t as translate

_PREPARATION_TTL_SECONDS = 600


class SecurityTransportService:
    def __init__(
        self,
        state: dict[str, Any],
        save_config: Callable[[], None],
        data_dir: Path,
        transport: ServerTransport,
    ) -> None:
        self._state = state
        self._save_config = save_config
        self._transport = transport
        self._store = CertificateStore(data_dir)
        self._self_signed_provider = SelfSignedCertificateProvider(self._store)
        self._acme_provider = AcmeCertificateProvider(self._store)
        # 兼容现有调用方和测试；新逻辑按配置选择 provider。
        self._provider = self._self_signed_provider
        self._preparations: dict[str, dict[str, Any]] = {}
        self._acme_renew_lock = asyncio.Lock()

    # ---- 状态 ----

    def current_config(self) -> WebTransportConfig:
        return parse_web_transport(self._state.get("web_transport") or {})

    def get_status(self) -> dict[str, Any]:
        config = self.current_config()
        status: dict[str, Any] = {
            "ok": True,
            "tls_mode": self._transport.tls_mode,
            "scheme": self._transport.scheme,
            "tls_mode_source": config.tls_mode_source,
            "restart_required": False,
        }
        if self._transport.degraded_error:
            status["degraded_error"] = self._transport.degraded_error
        if config.tls_mode == TLS_MODE_LETS_ENCRYPT:
            # 仅返回可再次编辑的 ACME 公共参数，不包含账户密钥或证书路径。
            status["acme"] = config.acme.redacted_view()
        metadata = None
        if config.tls_mode == TLS_MODE_SELF_SIGNED:
            metadata = self._self_signed_provider.metadata()
        elif config.tls_mode == TLS_MODE_LETS_ENCRYPT and config.acme.identifier:
            metadata = self._acme_provider.metadata_for(
                config.acme.identifier_type, config.acme.identifier
            )
        if metadata is not None:
            certificate = metadata.public_view()
            if config.tls_mode == TLS_MODE_LETS_ENCRYPT:
                certificate.update(
                    {
                        "provider": "lets_encrypt",
                        "identifier_type": config.acme.identifier_type,
                        "identifier": config.acme.identifier,
                        "renewal_status": (self._acme_provider.state().get("renewal") or {}).get(
                            "last_result", "unknown"
                        ),
                    }
                )
            status["certificate"] = certificate
        return status

    # ---- 切换事务 ----

    async def prepare(self, mode: str, acme_raw: Any = None) -> dict[str, Any]:
        mode = str(mode or "").strip().lower()
        if mode not in (TLS_MODE_OFF, TLS_MODE_SELF_SIGNED):
            if mode != TLS_MODE_LETS_ENCRYPT:
                return {"ok": False, "error": f"不支持的连接模式：{mode or '(空)'}"}
        if mode == TLS_MODE_OFF:
            # 关闭不生成候选证书，直接走 disable 事务。
            return {"ok": False, "error": translate("security.use_disable_action")}
        env_mode = str(os.environ.get("TRPG_TLS_MODE") or "").strip().lower()
        if env_mode and env_mode != mode:
            return {"ok": False, "error": translate("security.tls_mode_managed_by_env")}

        if mode == TLS_MODE_LETS_ENCRYPT:
            return await self._prepare_lets_encrypt(acme_raw)

        try:
            prepared = self._self_signed_provider.prepare()
            # 候选必须真的能建出 SSLContext，才允许进入 activate。
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(str(prepared.cert_path), str(prepared.key_path))
        except (CertificateError, ssl.SSLError, OSError, ValueError) as exc:
            return {"ok": False, "error": translate("security.local_certificate_verification_failed", exc=exc)}

        token = secrets_module.token_urlsafe(24)
        self._preparations[token] = {
            "mode": TLS_MODE_SELF_SIGNED,
            "config": {"tls_mode": TLS_MODE_SELF_SIGNED},
            "fingerprint": prepared.metadata.fingerprint_sha256,
            "expires_at": time.time() + _PREPARATION_TTL_SECONDS,
        }
        self._expire_preparations()
        return {
            "ok": True,
            "mode": TLS_MODE_SELF_SIGNED,
            "token": token,
            "certificate": prepared.metadata.public_view(),
        }

    async def _prepare_lets_encrypt(self, acme_raw: Any) -> dict[str, Any]:
        raw = acme_raw if isinstance(acme_raw, dict) else {}
        candidate = parse_web_transport(
            {"tls_mode": TLS_MODE_LETS_ENCRYPT, "acme": raw}
        )
        error = validate_activation(candidate)
        if error:
            return {"ok": False, "error": error}
        env_mode = str(os.environ.get("TRPG_TLS_MODE") or "").strip().lower()
        if env_mode and env_mode != TLS_MODE_LETS_ENCRYPT:
            return {"ok": False, "error": translate("security.tls_mode_managed_by_env")}
        settings = candidate.acme
        request = self._acme_request(candidate)
        try:
            result = await self._acme_provider.issue(request)
        except (AcmeError, CertificateError, aiohttp.ClientError, OSError, ValueError) as exc:
            return {"ok": False, "error": translate("security.lets_encrypt_request_failed", exc=exc)}

        token = secrets_module.token_urlsafe(24)
        self._preparations[token] = {
            "mode": TLS_MODE_LETS_ENCRYPT,
            "config": {
                "tls_mode": TLS_MODE_LETS_ENCRYPT,
                "acme": settings.redacted_view(),
            },
            "fingerprint": result.prepared.metadata.fingerprint_sha256,
            "expires_at": time.time() + _PREPARATION_TTL_SECONDS,
        }
        self._expire_preparations()
        return {
            "ok": True,
            "mode": TLS_MODE_LETS_ENCRYPT,
            "token": token,
            "certificate": result.prepared.metadata.public_view(),
            "warnings": result.warnings,
        }

    def _acme_request(self, config: WebTransportConfig) -> AcmeIssueRequest:
        settings = config.acme
        return AcmeIssueRequest(
            identifier=AcmeOrderIdentifier(settings.identifier_type, settings.identifier),
            contact_email=settings.contact_email,
            directory=settings.directory,
            profile=settings.certificate_profile,
            http_challenge_port=settings.http_challenge_port,
            staging_preflight=settings.directory != "staging",
        )

    async def renew_if_due(self) -> dict[str, Any] | None:
        """续期当前生效的 Let's Encrypt 证书；未到窗口时不联网。"""
        config = self.current_config()
        if config.tls_mode != TLS_MODE_LETS_ENCRYPT or not config.acme.identifier:
            return None
        metadata = self._acme_provider.metadata_for(
            config.acme.identifier_type, config.acme.identifier
        )
        if metadata is None:
            return {"status": "missing"}
        if not self._acme_provider.renewal_due(metadata):
            return {"status": "not_due", "not_after": metadata.not_after}
        async with self._acme_renew_lock:
            # 等待同进程中的另一次检查后重新读取，避免重复签发。
            metadata = self._acme_provider.metadata_for(
                config.acme.identifier_type, config.acme.identifier
            )
            if metadata is None or not self._acme_provider.renewal_due(metadata):
                return {"status": "not_due"}
            lock_name = self._store.stable_identifier_id(
                config.acme.identifier_type, config.acme.identifier
            )
            lock_path = None
            for _ in range(25):
                lock_path = self._store.try_acquire_lock(lock_name)
                if lock_path is not None:
                    break
                await asyncio.sleep(0.2)
            if lock_path is None:
                return {"status": "busy"}
            try:
                result = await self._acme_provider.issue(self._acme_request(config))
            except (AcmeError, CertificateError, aiohttp.ClientError, OSError, ValueError) as exc:
                self._acme_provider.record_renewal(False, str(exc))
                return {"status": "failed", "error": str(exc)}
            finally:
                self._store.release_lock(lock_path)
            self._acme_provider.record_renewal(True)
            return {
                "status": "renewed",
                "fingerprint_sha256": result.prepared.metadata.fingerprint_sha256,
            }

    def activate(self, token: str) -> dict[str, Any]:
        token = str(token or "").strip()
        preparation = self._preparations.get(token)
        if not preparation:
            return {"ok": False, "error": translate("security.prepare_token_invalid")}
        if time.time() > preparation["expires_at"]:
            self._preparations.pop(token, None)
            return {"ok": False, "error": translate("security.prepare_token_expired")}

        mode = preparation["mode"]
        candidate = parse_web_transport(preparation.get("config") or {})
        error = validate_activation(candidate)
        if error:
            return {"ok": False, "error": error}
        # 候选证书在 prepare 与 activate 之间可能被 regenerate 替换；
        # 再次验证指纹，不一致则要求重新 prepare。
        try:
            if mode == TLS_MODE_LETS_ENCRYPT:
                prepared = self._acme_provider.load_live(
                    candidate.acme.identifier_type, candidate.acme.identifier
                )
            else:
                prepared = self._self_signed_provider.prepare()
        except (CertificateError, ssl.SSLError, OSError, ValueError) as exc:
            return {"ok": False, "error": translate("security.certificate_verification_failed", exc=exc)}
        if prepared.metadata.fingerprint_sha256 != preparation["fingerprint"]:
            return {"ok": False, "error": translate("security.certificate_changed")}

        # 一次性令牌：无论后续成败都销毁，防止重试重复执行。
        self._preparations.pop(token, None)
        self._state["web_transport"] = preparation.get("config") or {"tls_mode": mode}
        self._save_config()
        return {
            "ok": True,
            "tls_mode": mode,
            "target_scheme": "https" if mode != TLS_MODE_OFF else "http",
            "target_identifier": candidate.acme.identifier if mode == TLS_MODE_LETS_ENCRYPT else "",
            "restart_required": True,
        }

    def disable(self) -> dict[str, Any]:
        # 关闭 HTTPS 不删除证书文件与后续阶段保留的账户数据。
        self._state["web_transport"] = {"tls_mode": TLS_MODE_OFF}
        self._save_config()
        return {"ok": True, "tls_mode": TLS_MODE_OFF, "target_scheme": "http", "restart_required": True}

    # ---- 自签证书管理 ----

    def regenerate_self_signed(self) -> dict[str, Any]:
        old = self._provider.metadata()
        try:
            prepared = self._self_signed_provider.regenerate()
        except (CertificateError, OSError, ValueError) as exc:
            return {"ok": False, "error": translate("security.regenerate_failed", exc=exc)}
        current_mode = self.current_config().tls_mode
        return {
            "ok": True,
            "restart_required": current_mode == TLS_MODE_SELF_SIGNED,
            "certificate": prepared.metadata.public_view(),
            "previous_fingerprint": old.fingerprint_sha256 if old else "",
        }

    # ---- 内部 ----

    def _expire_preparations(self) -> None:
        now = time.time()
        for token in [t for t, item in self._preparations.items() if item["expires_at"] < now]:
            self._preparations.pop(token, None)
