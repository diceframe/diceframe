"""Which content pack a rule entity came from, and how the bundle enforces it.

The bundle is named ``dnd2024-srd`` but nothing stopped it from carrying a rule
that is not in the SRD: a feat that exists only in the Player's Handbook could
be declared with an SRD-looking source anchor, and "how much of the SRD do we
implement" became a number nobody could compute.  ``content_pack`` makes the
distinction machine-readable: the manifest declares which packs the bundle may
contain, every entity resolves to one (the primary pack by default), and an
undeclared pack is a load error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rulesets.bundle import RulesetBundleError, RulesetBundleLoader


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _bundle(
    tmp_path: Path, *, manifest_packs: dict | None, feat_pack: str = "",
) -> Path:
    root = tmp_path / "bundles" / "pack_bundle"
    _write(root / "legal" / "ATTRIBUTION.md", "Attribution")
    manifest = {
        "schema_version": 1,
        "bundle_id": "test:pack",
        "runtime_id": "test:runtime",
        "ruleset_version": "0.1.0",
        "content_version": "test+r1",
        "default_locale": "en",
        "supported_locales": ["en"],
        "license": {"id": "CC-BY-4.0", "attribution": "legal/ATTRIBUTION.md"},
    }
    if manifest_packs is not None:
        manifest["content_packs"] = manifest_packs
    _write(root / "manifest.json", manifest)
    feat = {
        "schema_version": 1,
        "kind": "feat",
        "id": "probe",
        "category": "origin",
        "source_ref": "srd-5.2.1:p87:probe",
        "automation_level": "guided",
    }
    if feat_pack:
        feat["content_pack"] = feat_pack
    _write(root / "content" / "feats" / "probe.json", feat)
    return root


def _load(tmp_path: Path, **kwargs) -> dict:
    _bundle(tmp_path, **kwargs)
    bundle = RulesetBundleLoader(tmp_path / "bundles").load("pack_bundle")
    return bundle.entities["feat"]["probe"]


def test_a_feat_without_a_pack_belongs_to_the_primary_one(tmp_path: Path) -> None:
    entity = _load(
        tmp_path,
        manifest_packs={"primary": "srd-5.2.1", "declared": ["srd-5.2.1", "phb-2024"]},
    )
    assert entity["content_pack"] == "srd-5.2.1"


def test_an_explicit_secondary_pack_is_kept(tmp_path: Path) -> None:
    entity = _load(
        tmp_path,
        manifest_packs={"primary": "srd-5.2.1", "declared": ["srd-5.2.1", "phb-2024"]},
        feat_pack="phb-2024",
    )
    assert entity["content_pack"] == "phb-2024"


def test_an_undeclared_pack_fails_at_load(tmp_path: Path) -> None:
    # 没有这一条，任何人都能往 SRD 包里塞一条不在 SRD 的内容并标任意来源。
    with pytest.raises(RulesetBundleError) as captured:
        _load(
            tmp_path,
            manifest_packs={"primary": "srd-5.2.1", "declared": ["srd-5.2.1"]},
            feat_pack="phb-2024",
        )
    assert "content_pack" in str(captured.value)


@pytest.mark.parametrize(
    "packs",
    [
        # primary 不在 declared 里：一边说主来源是 A，一边说允许的来源不含 A。
        {"primary": "srd-5.2.1", "declared": ["phb-2024"]},
        # declared 为空：声明了来源体系却一个都不允许。
        {"primary": "srd-5.2.1", "declared": []},
        # 重复项：同一来源写两次，说明有人在拼字符串。
        {"primary": "srd-5.2.1", "declared": ["srd-5.2.1", "srd-5.2.1"]},
    ],
)
def test_an_inconsistent_pack_declaration_fails(tmp_path: Path, packs: dict) -> None:
    with pytest.raises(RulesetBundleError):
        _load(tmp_path, manifest_packs=packs)


def test_a_bundle_without_a_pack_system_still_loads(tmp_path: Path) -> None:
    # 极简夹具（以及本仓大量既有测试）不声明来源体系；它们的内容不应因此
    # 被判为"非法来源"，只是没有来源信息可用。
    entity = _load(tmp_path, manifest_packs=None)
    assert entity["content_pack"] == ""


def test_non_string_content_pack_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RulesetBundleError, match="content_pack must be a string"):
        _load(
            tmp_path,
            manifest_packs={"primary": "srd-5.2.1", "declared": ["srd-5.2.1"]},
            feat_pack=123,  # type: ignore[arg-type]
        )


def test_advancement_catalog_row_undeclared_content_pack_fails_closed() -> None:
    from copy import deepcopy
    from src.rulesets.dnd2024.features.feat_models import (
        FeatDefinitionError,
        load_feat_definitions,
    )
    from src.rulesets.dnd2024.runtime import Dnd2024Runtime

    bundle = deepcopy(Dnd2024Runtime().load_bundle("en"))
    catalog = bundle.entities["advancement_feat_catalog"]["srd_advancement_feats"]
    first_key = next(iter(catalog["feats"]))
    catalog["feats"][first_key]["content_pack"] = "undeclared-pack"

    with pytest.raises(FeatDefinitionError, match="undeclared content_pack"):
        load_feat_definitions(bundle)
