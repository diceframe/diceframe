"""Declared module Book resources materialized into the canonical user store.

内容包 ``contributes.lorebooks`` 声明的资料册此前只进 catalog（包内资料浏览），
没有任何落地路径（issue #700）。本模块把「一个声明的 lorebook 资源 = 用户
书库里的一本书」这条物化规则集中在一处，供三条链路共用：

- ``legacy_autoimport`` 启用即落地（行为与字段名一致）；
- ``catalog`` 模组开局时按需物化（玩家显式选择要带上，绝不启用即灌注）；
- 卸载清理按 ``source_kind="module"`` / ``source_id=<plugin_id>`` 反查。

物化整本书而不是逐条 entry：书是玩家可见、可绑定、可管理的单位；条目
（含可见性分档等扩展）由既有 import 机制逐条 upsert，幂等可重放。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from src.lorebook.importer import commit_lorebook_import, draft_lorebook_import
from src.plugin_host.content import safe_id_part

#: PluginContentCatalog 注入的目录元数据；importer 只应看到书文件本身。
_CATALOG_METADATA_KEYS = frozenset({"plugin_id", "plugin_name", "source", "readonly", "ref"})

#: 书/条目的记账字段：变了不代表用户改了内容，指纹里必须排除。
#: ``revision`` 是每次写入都自增的派生标记，把「系统写一次」当成用户改动会让
#: 卸载与重导入都误判；真正的内容字段（含 keywords/visible_to/匹配设置等）都在。
_BOOK_FINGERPRINT_IGNORED = frozenset({"id", "revision", "created_at", "updated_at"})
_ENTRY_FINGERPRINT_IGNORED = frozenset({
    "id", "world_id", "book_id", "source_plugin", "created_at", "updated_at",
})


def module_book_id(plugin_id: str, external_id: str) -> str:
    """Deterministic Book id for one declared module lorebook resource.

    同一 ``(plugin_id, resource key)`` 永远映射到同一个书 id：启用两遍写一次，
    模块页/创建页/卸载清理三处也能用同一 id 对齐。
    """

    return f"module_book_{safe_id_part(str(plugin_id))}_{safe_id_part(str(external_id))}"


def declared_module_book_id(plugin_id: str, resource: dict[str, Any]) -> str:
    """Book id that ``materialize_module_book`` will use for this resource.

    Ownership decisions (re-import guard, uninstall) need the id **before**
    writing anything, so the derivation lives here instead of being duplicated
    at every call site.
    """

    payload = {
        key: value
        for key, value in dict(resource).items()
        if key not in _CATALOG_METADATA_KEYS
    }
    external_id = str(payload.get("id") or "")
    if not external_id:
        # 没写 id 的书用名字兜底，与 materialize 时的 draft 归一化保持同一来源。
        external_id = str(draft_lorebook_import(payload).name or "book")
    return module_book_id(plugin_id, external_id)


#: 缩写按惯用大写渲染，其余单词首字母大写。
_NAME_TOKEN_CASE = {
    "gm": "GM",
    "npc": "NPC",
    "pc": "PC",
    "dnd": "DnD",
    "coc": "CoC",
    "ai": "AI",
    "ui": "UI",
    "api": "API",
    "tts": "TTS",
    "asr": "ASR",
}


def module_book_display_name(resource: dict[str, Any]) -> str:
    """One declared Book's readable name, even when the pack forgot to name it.

    有些包把 ``content/lorebooks/<file>.json`` 写成只有 entries、没有 ``name``
    （大衮的 ``dagon-gm-guide`` 就是这样）。catalog 会把文件名当书名兜底，于是
    创建页显示 ``dagon-gm-guide``、物化出来的书叫默认的 "Legacy Lorebook"——
    同一个包文件在三个地方三个名字。名字兜底集中在这里，catalog 行与物化共用。
    """

    external_id = str(resource.get("id") or "").strip()
    name = str(resource.get("name") or "").strip()
    if name and name != external_id:
        return name
    words = [word for word in re.split(r"[-_.\s]+", external_id) if word]
    return " ".join(
        _NAME_TOKEN_CASE.get(word.lower(), word[:1].upper() + word[1:])
        for word in words
    )


def entry_fingerprint(entry: dict[str, Any] | None) -> str:
    """Digest of one entry's user-editable content, as the canonical store returns it."""

    if not entry:
        return ""
    content = {k: v for k, v in entry.items() if k not in _ENTRY_FINGERPRINT_IGNORED}
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def book_fingerprint(lorebook: Any, book_id: str) -> str:
    """Digest of a Book's full user-editable state (book fields + every entry field).

    卸载与重导入都拿它和 receipt 里「包上一次写入」的指纹比对：任何用户可见
    的改动（书名、描述、检索设置、条目的 keywords/visible_to/匹配策略…）都会
    让指纹变化，不能只看正文——只改关键词也是用户数据。
    """

    if lorebook is None:
        return ""
    get_book = getattr(lorebook, "get_lorebook", None)
    book = get_book(str(book_id)) if callable(get_book) else None
    if not isinstance(book, dict):
        return ""
    list_entries = getattr(lorebook, "list_book_entries", None)
    rows = list_entries(str(book_id)) if callable(list_entries) else []
    basis = {
        "book": {k: v for k, v in book.items() if k not in _BOOK_FINGERPRINT_IGNORED},
        "entries": sorted(
            entry_fingerprint(row) for row in rows or [] if isinstance(row, dict)
        ),
    }
    text = json.dumps(basis, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def materialize_module_book(
    lorebook_store: Any,
    plugin_id: str,
    resource: dict[str, Any],
    *,
    target_world: str = "",
) -> str:
    """Materialize one declared module Book into the canonical store.

    The whole resource becomes ONE Book tagged ``source_kind="module"`` /
    ``source_id=<plugin_id>``；包带 world_template 时再挂一条 world 域
    runtime binding，使该世界的开局候选直接包含这本书（等价于旧
    starter_lorebook 的「开这世界的局就带上」，但不再向 world schema v3
    禁止的内嵌写法妥协）。幂等：书 on-conflict-ignore、条目按 canonical
    id upsert，重放安全。
    """

    payload = {
        key: value
        for key, value in dict(resource).items()
        if key not in _CATALOG_METADATA_KEYS
    }
    draft = draft_lorebook_import(payload)
    # 包里没写 name 时 adapter 会落到 "Legacy Lorebook"；书库里那本书的名字
    # 必须和创建页/包页看到的一致，否则玩家认不出它是同一本。
    display_name = module_book_display_name(payload)
    if display_name and display_name != str(payload.get("name") or "").strip():
        draft.name = display_name
    # 声明来源是 module:plugin_id——identity、uninstall 反查、模块页归属
    # 三者同源，不落在 adapter 的 device 缺省上。
    draft.source = {"source_kind": "module", "source_id": str(plugin_id)}
    book_id = declared_module_book_id(plugin_id, resource)
    binding = None
    if target_world and callable(getattr(lorebook_store, "get_world", None)) and lorebook_store.get_world(target_world):
        binding = {
            "book_id": book_id,
            "scope_kind": "world",
            "scope_id": str(target_world),
            "role": "runtime",
            "enabled": True,
            "order": 110,
        }
    commit_lorebook_import(lorebook_store, draft, binding, book_id=book_id)
    return book_id


def declared_module_book_resource(
    host: Any,
    plugin_id: str,
    book_id: str,
) -> dict[str, Any] | None:
    """Reverse-map a deterministic module Book id back to its declared resource.

    创建页/模块页拿到的 ref id 就是 book id；物化入口需要资源本体时在这里
    扫描该插件声明的 lorebook 资源做匹配（清单很小，且只在本包内）。
    """

    lister = getattr(host, "list_content_resources", None)
    if not callable(lister):
        return None
    try:
        resources = (lister("lorebook") or {}).get("lorebook", [])
    except Exception:
        return None
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        if str(resource.get("plugin_id") or "") != str(plugin_id):
            continue
        external_id = str(resource.get("id") or "")
        if external_id and module_book_id(plugin_id, external_id) == str(book_id):
            return resource
    return None
