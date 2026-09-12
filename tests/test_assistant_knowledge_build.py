"""助手知识索引构建：文档源必须可配置，且能完全离线构建。

历史问题：scripts/build_assistant_knowledge.py 写死 GitHub raw，用户在
DICEFRAME_DOCS_BASE_URL 里配的镜像不生效；受限网络下打包会为每个文档等一次
超时（本地全套测试因此多花 220s）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import build_assistant_knowledge as bak


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text.encode("utf-8")

    def read(self) -> bytes:
        return self._text

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> None:
        return None


def test_read_content_doc_prefers_the_local_checkout(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "diceframe-content"
    doc = checkout / "docs" / "zh" / "guide.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# 本地文档", encoding="utf-8")
    monkeypatch.setattr(bak, "_CONTENT_REPO_CHECKOUT", checkout)
    monkeypatch.setattr(
        bak.urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("本地有检出时不应发网络请求"),
    )

    assert bak._read_content_doc("zh", "guide.md") == "# 本地文档"


def test_read_content_doc_uses_the_configured_docs_base_url(monkeypatch, tmp_path: Path) -> None:
    """DICEFRAME_DOCS_BASE_URL 类配置必须优先生效（与运行时一致）。"""

    monkeypatch.setattr(bak, "_CONTENT_REPO_CHECKOUT", tmp_path / "missing")
    monkeypatch.setattr(bak.ak, "_DOCS_BASE_URLS", ("https://mirror.example/docs",))
    requested: list[str] = []

    def fake_urlopen(url, timeout=None):  # noqa: ANN001, ANN202
        requested.append(url)
        return _FakeResponse("# 镜像文档")

    monkeypatch.setattr(bak.urllib.request, "urlopen", fake_urlopen)

    assert bak._read_content_doc("en", "deploy.md") == "# 镜像文档"
    assert requested == ["https://mirror.example/docs/en/deploy.md"]


def test_read_content_doc_falls_through_bases_then_gives_up(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bak, "_CONTENT_REPO_CHECKOUT", tmp_path / "missing")
    monkeypatch.setattr(
        bak.ak, "_DOCS_BASE_URLS",
        ("https://mirror.example/docs", "https://raw.example/docs"),
    )
    requested: list[str] = []

    def failing_urlopen(url, timeout=None):  # noqa: ANN001, ANN202
        requested.append(url)
        raise OSError("refused")

    monkeypatch.setattr(bak.urllib.request, "urlopen", failing_urlopen)

    assert bak._read_content_doc("zh", "guide.md") is None
    assert requested == [
        "https://mirror.example/docs/zh/guide.md",
        "https://raw.example/docs/zh/guide.md",
    ]


def test_local_only_skips_remote_fetch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bak, "_CONTENT_REPO_CHECKOUT", tmp_path / "missing")
    monkeypatch.setattr(
        bak.urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("local_only 不应发网络请求"),
    )

    assert bak._read_content_doc("zh", "guide.md", allow_remote=False) is None


def test_local_only_switch_reads_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("DICEFRAME_DOCS_LOCAL_ONLY", raising=False)
    assert bak._local_only_requested() is False
    assert bak._local_only_requested(True) is True

    for value in ("1", "true", "YES"):
        monkeypatch.setenv("DICEFRAME_DOCS_LOCAL_ONLY", value)
        assert bak._local_only_requested() is True

    monkeypatch.setenv("DICEFRAME_DOCS_LOCAL_ONLY", "0")
    assert bak._local_only_requested() is False


def test_build_writes_an_index_without_touching_the_network(monkeypatch, tmp_path: Path) -> None:
    """离线构建也要产出可用索引（内容文档缺失时降级，不报错）。"""

    monkeypatch.setattr(bak, "_CONTENT_REPO_CHECKOUT", tmp_path / "missing")
    monkeypatch.setattr(
        bak.urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("离线构建不应发网络请求"),
    )
    output = tmp_path / "assistant_knowledge_index.json"

    target = bak.build(output, local_only=True)

    assert target == output and output.is_file()
    assert output.read_text(encoding="utf-8").startswith("{")
