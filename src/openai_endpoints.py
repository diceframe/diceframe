"""OpenAI 兼容端点地址：把服务商 base_url 拼成具体接口地址。

对话客户端直接在 base_url 后拼 `/chat/completions`；图像、语音合成、语音识别
沿用同一个 base_url，因此 base_url 里已经带了 API 版本段（`/v1`、方舟的
`/api/v3`、`/v1beta/openai` 等）时直接拼接口路径，不能再补一个 `/v1`。
只有完全没有版本段的地址（如 `https://api.openai.com`、本地 `http://host:port`）
才按 OpenAI 惯例补 `/v1`。已经写成完整接口地址的保持原样。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

_VERSION_SEGMENT = re.compile(r"(?:^|/)v\d+(?:(?:alpha|beta)\d*)?(?:/|$)", re.IGNORECASE)


def openai_endpoint_url(base_url: str, endpoint: str) -> str:
    """Return the full URL of an OpenAI-compatible ``endpoint`` (e.g. ``images/generations``)."""
    parsed = urlparse(str(base_url or "").strip())
    path = parsed.path.rstrip("/")
    suffix = "/" + endpoint.strip("/")
    if path.endswith(suffix):
        target = path
    elif _VERSION_SEGMENT.search(path):
        target = path + suffix
    else:
        target = path + "/v1" + suffix
    return urlunparse(parsed._replace(path=target))
