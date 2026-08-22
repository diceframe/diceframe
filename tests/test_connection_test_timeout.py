import pytest

from src.memory.embedding import EmbeddingClient
from src.webui.config_update import connection_test_timeout, prepare_config_update


def test_config_update_accepts_connection_test_timeout():
    prepared = prepare_config_update({}, {"test_timeout_seconds": 90})

    assert prepared.error == ""
    assert prepared.state["test_timeout_seconds"] == 90


@pytest.mark.parametrize("value", [4, 301])
def test_config_update_rejects_out_of_range_connection_test_timeout(value):
    prepared = prepare_config_update({}, {"test_timeout_seconds": value})

    assert prepared.error == "连接测试超时必须在 5–300 秒之间"
    assert "test_timeout_seconds" not in prepared.state


def test_connection_test_timeout_uses_config_and_safe_bounds():
    assert connection_test_timeout({"test_timeout_seconds": 90}) == 90
    assert connection_test_timeout({"test_timeout_seconds": 1}) == 5
    assert connection_test_timeout({"test_timeout_seconds": "bad"}) == 30


def test_embedding_client_accepts_connection_test_timeout():
    assert EmbeddingClient("https://example.com/v1", timeout_seconds=90).timeout_seconds == 90
    assert EmbeddingClient("https://example.com/v1", timeout_seconds=999).timeout_seconds == 300
