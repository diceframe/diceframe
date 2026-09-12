"""把文档预处理成 AI 助手的本地检索索引包。

随程序打包，运行时直接加载（assistant_knowledge.py）。README/更新日志来自
本仓库；用户文档来自相邻的 diceframe-content 检出（工作区同目录），没有时
退回 GitHub raw 拉取，作为运行时远程拉取失败时的离线兜底快照。

文档变动后重新运行本脚本；build_release 打包时会自动调用。

用法：
    python scripts/build_assistant_knowledge.py
    python scripts/build_assistant_knowledge.py --output path/to/index.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 直接按文件加载 assistant_knowledge 模块，绕过 src/webui/__init__.py
# （后者 import WebAPI -> aiohttp，构建环境未装依赖会失败）
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "assistant_knowledge", ROOT / "src" / "webui" / "assistant_knowledge.py"
)
ak = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ak  # 注册后 exec，dataclass 才能解析模块
_spec.loader.exec_module(ak)

_CONTENT_REPO_CHECKOUT = ROOT.parent / "diceframe-content"
# 与运行时一致：DICEFRAME_DOCS_BASE_URL 优先，其次 GitHub raw。此前这里写死
# GITHUB_DOCS_BASE_URL，用户配了镜像也没用，离线/受限网络下每个文档都要等超时。
_FETCH_TIMEOUT_SECONDS = 8


def _local_only_requested(explicit: bool | None = None) -> bool:
    """是否只使用本地 diceframe-content 检出（不发网络请求）。

    显式参数优先；否则读 DICEFRAME_DOCS_LOCAL_ONLY，便于离线打包与测试。
    """

    if explicit is not None:
        return explicit
    return str(os.getenv("DICEFRAME_DOCS_LOCAL_ONLY", "")).strip().lower() in {
        "1", "true", "yes",
    }


def _read_content_doc(lang_key: str, path: str, *, allow_remote: bool = True) -> str | None:
    """从相邻 diceframe-content 检出读文档；没有检出时退回配置的文档源。"""

    local = _CONTENT_REPO_CHECKOUT / "docs" / lang_key / path
    if local.exists():
        return local.read_text(encoding="utf-8")
    if not allow_remote:
        return None
    bases = tuple(getattr(ak, "_DOCS_BASE_URLS", ()) or (ak.GITHUB_DOCS_BASE_URL,))
    for base in bases:
        url = f"{base}/{lang_key}/{path}"
        try:
            with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_SECONDS) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            continue
    print(f"  ! 跳过（本地无检出且拉取失败）: docs/{lang_key}/{path}")
    return None


def build(output: Path | None = None, *, local_only: bool | None = None) -> Path:
    target = output or ak.INDEX_FILE
    allow_remote = not _local_only_requested(local_only)
    if not allow_remote:
        print("  · 仅使用本地 diceframe-content 检出（跳过远程文档）")
    index: dict[str, list[dict]] = {}
    for lang_key in ("zh", "en"):
        chunks: list[dict] = []
        for relative in ak._DOCUMENTS[lang_key]:
            path = ROOT / relative
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for chunk in ak._parse_markdown(relative, text):
                chunks.append(_chunk_payload(chunk))
        for doc_path in ak._CONTENT_DOC_PATHS[lang_key]:
            text = _read_content_doc(lang_key, doc_path, allow_remote=allow_remote)
            if text is None:
                continue
            source = f"docs/{lang_key}/{doc_path}"
            for chunk in ak._parse_markdown(source, text):
                chunks.append(_chunk_payload(chunk))
        index[lang_key] = chunks
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(v) for v in index.values())
    size_kb = target.stat().st_size / 1024
    print(
        f"assistant knowledge index: {total} chunks "
        f"({len(index['zh'])} zh + {len(index['en'])} en), {size_kb:.1f} KB -> {target}"
    )
    return target


def _chunk_payload(chunk) -> dict:
    return {
        "source": chunk.source,
        "heading": chunk.heading,
        "text": chunk.text,
        "tokens": dict(chunk.tokens),
        "heading_tokens": sorted(chunk.heading_tokens),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the AI assistant local knowledge index.")
    parser.add_argument("--output", type=Path, default=None, help="Output index file path")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="只使用本地 diceframe-content 检出，不访问网络（也可用 DICEFRAME_DOCS_LOCAL_ONLY=1）",
    )
    args = parser.parse_args()
    build(args.output, local_only=args.local_only or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
