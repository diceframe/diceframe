import pytest

from src.asr.providers import _openai_transcription_url
from src.imagegen.providers import _openai_image_edit_url, _openai_image_url
from src.openai_endpoints import openai_endpoint_url
from src.tts.providers import _openai_speech_url


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        # 没有版本段：按 OpenAI 惯例补 /v1（保持原有行为）
        ("https://api.openai.com", "https://api.openai.com/v1/images/generations"),
        ("http://127.0.0.1:8880/", "http://127.0.0.1:8880/v1/images/generations"),
        ("https://gateway.example.test/openai", "https://gateway.example.test/openai/v1/images/generations"),
        # 已带版本段：与对话客户端一致，直接拼接口路径
        ("https://api.example.test/v1", "https://api.example.test/v1/images/generations"),
        ("https://ark.cn-beijing.volces.com/api/v3", "https://ark.cn-beijing.volces.com/api/v3/images/generations"),
        ("https://ark.cn-beijing.volces.com/api/v3/", "https://ark.cn-beijing.volces.com/api/v3/images/generations"),
        (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "https://generativelanguage.googleapis.com/v1beta/openai/images/generations",
        ),
        # 已是完整接口地址：原样使用
        (
            "https://ark.cn-beijing.volces.com/api/v3/images/generations",
            "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        ),
    ],
)
def test_openai_endpoint_url_respects_version_segment(base_url, expected):
    assert openai_endpoint_url(base_url, "images/generations") == expected


def test_openai_endpoint_url_keeps_query_and_ignores_lookalike_segments():
    assert openai_endpoint_url("https://api.example.test/v1?key=x", "audio/speech") == (
        "https://api.example.test/v1/audio/speech?key=x"
    )
    assert openai_endpoint_url("https://api.example.test/v2ray", "audio/speech") == (
        "https://api.example.test/v2ray/v1/audio/speech"
    )


def test_capability_url_builders_share_the_same_rule():
    base = "https://ark.cn-beijing.volces.com/api/v3"
    assert _openai_image_url(base) == base + "/images/generations"
    assert _openai_image_edit_url(base) == base + "/images/edits"
    assert _openai_speech_url(base) == base + "/audio/speech"
    assert _openai_transcription_url(base) == base + "/audio/transcriptions"
