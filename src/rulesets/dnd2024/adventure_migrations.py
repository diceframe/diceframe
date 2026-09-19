"""D&D 2024 compatibility for explicitly known unreleased adventure digests."""

from __future__ import annotations

from typing import Any


_GREYMOOR_ADVENTURE_ID = "core:lanterns_of_greymoor"
_GREYMOOR_VERSION = "1.0.0"
_GREYMOOR_FORMAT = "diceframe:adventure-graph-v1"
_GREYMOOR_CURRENT_DIGEST = (
    "sha256:651e93c4f184357f002a8cc8eaeeb430dc8514a055841fe48a2e996dcaf5f656"
)
_GREYMOOR_UNRELEASED_DIGESTS = (
    "sha256:363c6786c0e9460ec911d85460c49b610addf8e86cc86d136538daee24d6740c",
    "sha256:81e6d8e383b4eb4bcce1cf4118b9b2cad46dd12bb735dfa5a8d18dce6bc152cf",
    "sha256:dd85173681cc6ad47ce56e5ab27ac0b7f683bdbfcf6628ab4c05e1e05941adbf",
)

_KNOWN_UNRELEASED_DIGEST_MIGRATIONS = {
    (
        _GREYMOOR_ADVENTURE_ID,
        _GREYMOOR_VERSION,
        _GREYMOOR_FORMAT,
        digest,
    ): _GREYMOOR_CURRENT_DIGEST
    for digest in _GREYMOOR_UNRELEASED_DIGESTS
}


def migrate_unreleased_adventure_binding(
    binding: dict[str, Any], expected: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the canonical binding for one explicitly known dev-only digest."""

    current = dict(binding or {})
    canonical = dict(expected or {})
    if current == canonical:
        return canonical
    for field in ("adventure_id", "version", "format", "world_id"):
        if str(current.get(field) or "") != str(canonical.get(field) or ""):
            return None
    # FIX-02 §4.3：来源身份只能"补上"，不能被这次迁移**改写**——旧绑定本来没写
    # 来源，迁移成当前唯一解析出的来源是可以证明的；但如果存档已经声明了来源，
    # 而 canonical 指向另一个来源，那是冲突，必须 fail closed，不许猜。
    current_source = str(current.get("source_kind") or "")
    canonical_source = str(canonical.get("source_kind") or "")
    if current_source and canonical_source and (
        current_source != canonical_source
        or str(current.get("source_id") or "") != str(canonical.get("source_id") or "")
    ):
        return None
    migration_key = (
        str(current.get("adventure_id") or ""),
        str(current.get("version") or ""),
        str(current.get("format") or ""),
        str(current.get("content_digest") or ""),
    )
    migrated_digest = _KNOWN_UNRELEASED_DIGEST_MIGRATIONS.get(migration_key)
    if migrated_digest != str(canonical.get("content_digest") or ""):
        return None
    migrated = dict(canonical)
    if not current_source:
        # §4.3：这次迁移只负责把**已知的未发布 digest** 修正为当前包。旧存档不会
        # 因为它获得来源身份——来源身份只在应用真正创建绑定（选择冒险开局）时
        # 落盘，避免在读存档的冷路径上改写身份语义。
        migrated.pop("source_kind", None)
        migrated.pop("source_id", None)
    return migrated


def apply_unreleased_adventure_binding_migration(
    instance: Any, expected: dict[str, Any],
) -> bool | None:
    """Upgrade top-level and campaign projections, or return None if incompatible."""

    current = dict(getattr(instance, "adventure_binding", {}) or {})
    canonical = migrate_unreleased_adventure_binding(current, expected)
    if canonical is None:
        return None

    campaign_binding: dict[str, Any] | None = None
    ruleset_state = getattr(instance, "ruleset_state", None)
    if isinstance(ruleset_state, dict):
        campaign = ruleset_state.get("campaign")
        if isinstance(campaign, dict):
            projected = campaign.get("adventure_binding")
            if isinstance(projected, dict):
                campaign_binding = projected

    if campaign_binding is not None:
        allowed = {
            "adventure_id": {
                "", str(current.get("adventure_id") or ""),
                str(canonical.get("adventure_id") or ""),
            },
            "world_id": {
                "", str(current.get("world_id") or ""),
                str(canonical.get("world_id") or ""),
            },
            "version": {
                "", str(current.get("version") or ""),
                str(canonical.get("version") or ""),
            },
            "content_digest": {
                "", str(current.get("content_digest") or ""),
                str(canonical.get("content_digest") or ""),
            },
        }
        if any(
            str(campaign_binding.get(field) or "") not in accepted
            for field, accepted in allowed.items()
        ):
            return None

    changed = current != canonical
    instance.adventure_binding = canonical
    if campaign_binding is not None:
        projection = {
            "adventure_id": canonical["adventure_id"],
            "world_id": canonical["world_id"],
            "version": canonical["version"],
            "content_digest": canonical["content_digest"],
        }
        changed = changed or any(
            campaign_binding.get(field) != value
            for field, value in projection.items()
        )
        campaign_binding.update(projection)
    return changed
