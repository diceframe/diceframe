"""Request boundary for the language of API messages.

接口提示按标准的 ``Accept-Language`` 协商：前端把它显式设为当前界面语言，这里
取出权重最高的受支持语言写进请求上下文，后端的 ``src.localization.messages.t()``
不传语言时就按它渲染。

没带这个头的客户端（机器人、脚本、第三方调用）拿到的仍是原来的中文文案；
头里没有任何受支持的语言时同样如此，不替客户端猜。
"""

from __future__ import annotations

from aiohttp import web

from src.localization.messages import recognized_language, reset_request_language, set_request_language

#: 防御性上限：超长的头直接忽略，不做解析。
_MAX_HEADER_LENGTH = 512


def preferred_language(header: object) -> str:
    """The supported language with the highest ``q`` weight in an ``Accept-Language`` value.

    Returns ``""`` when the header is missing, malformed or names nothing we
    support. Equal weights keep the order the client listed them in.
    """

    text = str(header or "").strip()
    if not text or len(text) > _MAX_HEADER_LENGTH:
        return ""
    candidates: list[tuple[float, int, str]] = []
    for index, part in enumerate(text.split(",")):
        tag, _, parameters = part.strip().partition(";")
        weight = 1.0
        for parameter in parameters.split(";"):
            name, _, value = parameter.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    weight = float(value)
                except ValueError:
                    weight = 0.0
        language = recognized_language(tag)
        if language and 0.0 < weight <= 1.0:
            candidates.append((-weight, index, language))
    return min(candidates)[2] if candidates else ""


@web.middleware
async def request_language_middleware(request: web.Request, handler) -> web.StreamResponse:
    token = set_request_language(preferred_language(request.headers.get("Accept-Language")))
    try:
        return await handler(request)
    finally:
        reset_request_language(token)
