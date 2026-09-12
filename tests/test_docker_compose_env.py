"""Docker Compose 必须把用户能在 .env 里写的配置真的传进容器。

Compose 的 .env 只用于变量替换，不会自动注入容器环境；.env.example 教用户写
TRPG_TLS_MODE 这类变量时，compose 里必须显式透传，否则文档与行为不一致。

这里不依赖 PyYAML（CI 环境没有它）：只需要按缩进取出 environment 的键与 ports
的条目，文件本身由本仓库维护，结构稳定。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
EXTRA_HTTP = ROOT / "docker-compose.extra-http-port.yml"
EXTRA_HTTPS = ROOT / "docker-compose.extra-https-port.yml"
ACME = ROOT / "docker-compose.acme-challenge.yml"

# src/web_transport/config.py 里对外支持的 TLS 环境变量。
SUPPORTED_TLS_ENV = (
    "TRPG_TLS_MODE",
    "TRPG_TLS_IDENTIFIER_TYPE",
    "TRPG_TLS_IDENTIFIER",
    "TRPG_TLS_CONTACT_EMAIL",
    "TRPG_TLS_CHALLENGE_TYPE",
    "TRPG_TLS_ACME_CHALLENGE_PORT",
)
# 阶段 D 预留、validate_activation 会显式拒绝：不能透传，否则等于暗示可用。
RESERVED_TLS_ENV = ("TRPG_TLS_CERT_FILE", "TRPG_TLS_KEY_FILE")
LISTENER_ENV = ("TRPG_WEB_HOSTS", "TRPG_WEB_HTTP_PORT", "TRPG_WEB_HTTPS_PORT")


def _block_lines(text: str, key: str, *, entry_prefix: str | None = None) -> list[str]:
    """取出 ``key:`` 之后更深缩进的条目（去注释、忽略空行）。"""

    lines: list[str] = []
    indent: int | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        current = len(line) - len(line.lstrip())
        if indent is None:
            if line.strip() == f"{key}:":
                indent = current
            continue
        if current <= indent:
            break
        body = line.strip()
        if entry_prefix is None:
            lines.append(body)
        elif body.startswith(entry_prefix):
            lines.append(body[len(entry_prefix):].strip())
    return lines


def _compose_env_keys(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {
        entry.split(":", 1)[0].strip()
        for entry in _block_lines(text, "environment")
        if ":" in entry
    }


def _published_ports(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return _block_lines(text, "ports", entry_prefix="- ")


@pytest.mark.parametrize("name", (*LISTENER_ENV, *SUPPORTED_TLS_ENV))
def test_compose_passes_through_documented_environment(name: str) -> None:
    assert name in _compose_env_keys(COMPOSE)


@pytest.mark.parametrize("name", RESERVED_TLS_ENV)
def test_compose_does_not_advertise_reserved_tls_files(name: str) -> None:
    assert name not in _compose_env_keys(COMPOSE)


def test_env_example_only_documents_supported_variables() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in RESERVED_TLS_ENV:
        assert name not in example
    for name in (*LISTENER_ENV, *SUPPORTED_TLS_ENV):
        assert f"{name}=" in example


def test_extra_port_overrides_can_be_used_one_at_a_time() -> None:
    """常见场景只需要一个附加 HTTP 端口，不能强制同时填 HTTPS 端口。"""

    assert _compose_env_keys(EXTRA_HTTP) == {"TRPG_WEB_HTTP_PORT"}
    assert _published_ports(EXTRA_HTTP) == [
        '"${TRPG_WEB_HTTP_PORT:?}:${TRPG_WEB_HTTP_PORT:?}"'
    ]

    assert _compose_env_keys(EXTRA_HTTPS) == {"TRPG_WEB_HTTPS_PORT"}
    assert _published_ports(EXTRA_HTTPS) == [
        '"${TRPG_WEB_HTTPS_PORT:?}:${TRPG_WEB_HTTPS_PORT:?}"'
    ]


def test_base_compose_publishes_only_the_main_port() -> None:
    """默认不额外占用宿主端口：附加端口必须由用户显式叠加文件。"""

    assert _published_ports(COMPOSE) == ['"${DICEFRAME_HTTP_PORT:-9876}:9876"']


def test_acme_challenge_override_maps_host_80_to_the_container_port() -> None:
    """http-01 校验方只访问公网 80：映射必须是 宿主机 80 → 容器 challenge 端口。

    "配置 8080、发布 8080" 对 Let's Encrypt 无效 —— 它不会去访问 8080；容器内
    改监听 8080 时，宿主机侧仍要固定 80。
    """

    assert _compose_env_keys(ACME) == {"TRPG_TLS_ACME_CHALLENGE_PORT"}
    assert _published_ports(ACME) == ['"80:${TRPG_TLS_ACME_CHALLENGE_PORT:-80}"']


def test_documented_docker_overrides_exist() -> None:
    """文档里点名的叠加文件必须真实存在，避免用户照抄命令却报找不到文件。"""

    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("extra-http-port", "extra-https-port", "acme-challenge"):
        path = ROOT / f"docker-compose.{name}.yml"
        assert path.is_file()
        assert f"docker-compose.{name}.yml" in example


def test_parser_ignores_comments() -> None:
    """辅助函数只看真实条目：注释里的变量名不能被当成已透传。"""

    sample = """
services:
  web:
    environment:
      # TRPG_TLS_CERT_FILE: 注释里提到的预留项不算
      TRPG_TLS_MODE: "${TRPG_TLS_MODE:-}"
    ports:
      # - "80:80"
      - "9876:9876"
""".strip()

    assert _block_lines(sample, "environment") == ['TRPG_TLS_MODE: "${TRPG_TLS_MODE:-}"']
    assert _compose_env_keys(Path(COMPOSE)) >= {"TRPG_TLS_MODE"}
    assert re.fullmatch(r"TRPG_[A-Z0-9_]+", "TRPG_TLS_MODE")
