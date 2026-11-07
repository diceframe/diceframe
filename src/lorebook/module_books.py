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

from typing import Any

from src.lorebook.importer import commit_lorebook_import, draft_lorebook_import
from src.plugin_host.content import safe_id_part

#: PluginContentCatalog 注入的目录元数据；importer 只应看到书文件本身。
_CATALOG_METADATA_KEYS = frozenset({"plugin_id", "plugin_name", "source", "readonly", "ref"})


def module_book_id(plugin_id: str, external_id: str) -> str:
    """Deterministic Book id for one declared module lorebook resource.

    同一 ``(plugin_id, resource key)`` 永远映射到同一个书 id：启用两遍写一次，
    模块页/创建页/卸载清理三处也能用同一 id 对齐。
    """

    return f"module_book_{safe_id_part(str(plugin_id))}_{safe_id_part(str(external_id))}"


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
    external_id = str(payload.get("id") or "") or str(resource.get("id") or "")
    draft = draft_lorebook_import(payload)
    # 声明来源是 module:plugin_id——identity、uninstall 反查、模块页归属
    # 三者同源，不落在 adapter 的 device 缺省上。
    draft.source = {"source_kind": "module", "source_id": str(plugin_id)}
    book_id = module_book_id(plugin_id, external_id or str(draft.name or "book"))
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
