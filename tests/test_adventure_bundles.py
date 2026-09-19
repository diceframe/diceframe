from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.adventures import AdventureBundleError, AdventureBundleLoader
# MOD-01：encounter mechanics（hp/ac/attacks）由 core:dnd2024 runtime validator
# 校验；生产组合必然导入 dnd2024，测试在此显式导入以完成注册。
from src.rulesets.dnd2024 import adventure_validation  # noqa: F401


ROOT = Path(__file__).parents[1]
INSTALLED = ROOT / "templates" / "adventures"


def test_installed_adventure_localizes_display_without_changing_identity() -> None:
    loader = AdventureBundleLoader(INSTALLED)

    chinese = loader.resolve("core:lanterns_of_greymoor", "zh-CN")
    english = loader.resolve("core:lanterns_of_greymoor", "en")

    assert chinese.manifest == english.manifest
    assert chinese.content_digest == english.content_digest
    assert chinese.adventure["id"] == english.adventure["id"] == "lanterns_of_greymoor"
    assert chinese.adventure["tutorial"]["name"] == "灰沼失灯记"
    assert english.adventure["tutorial"]["name"] == "The Lost Lanterns of Greymoor"
    chinese_encounters = chinese.list("encounter_catalog")
    assert chinese_encounters[0]["presets"][0]["difficulty"] == "standard"
    assert chinese.binding("greymoor") == {
        "adventure_id": "core:lanterns_of_greymoor",
        "version": "1.0.0",
        "format": "diceframe:adventure-graph-v1",
        "content_digest": chinese.content_digest,
        "world_id": "greymoor",
    }


def _copied_package(tmp_path: Path) -> tuple[AdventureBundleLoader, Path]:
    destination = tmp_path / "adventures" / "lanterns_of_greymoor"
    shutil.copytree(INSTALLED / "lanterns_of_greymoor", destination)
    return AdventureBundleLoader(tmp_path / "adventures"), destination


def test_digest_covers_all_locales_even_when_loading_another_locale(tmp_path: Path) -> None:
    loader, package = _copied_package(tmp_path)
    before = loader.load("lanterns_of_greymoor", "zh-CN").content_digest
    locale_path = package / "locales" / "en" / "adventure.json"
    locale = json.loads(locale_path.read_text(encoding="utf-8"))
    locale["fields"]["tutorial"]["name"] = "Changed English display text"
    locale_path.write_text(json.dumps(locale), encoding="utf-8")

    after = loader.load("lanterns_of_greymoor", "zh-CN").content_digest

    assert after != before


def test_digest_is_independent_of_json_line_endings(tmp_path: Path) -> None:
    loader, package = _copied_package(tmp_path)
    before = loader.load("lanterns_of_greymoor", "zh-CN").content_digest
    for path in package.rglob("*.json"):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(content.replace(b"\n", b"\r\n"))

    after = loader.load("lanterns_of_greymoor", "zh-CN").content_digest

    assert after == before


def test_adventure_locale_cannot_override_mechanics(tmp_path: Path) -> None:
    loader, package = _copied_package(tmp_path)
    locale_path = package / "locales" / "en" / "adventure.json"
    locale = json.loads(locale_path.read_text(encoding="utf-8"))
    locale["fields"]["recommended_world_id"] = "forged-world"
    locale_path.write_text(json.dumps(locale), encoding="utf-8")

    with pytest.raises(AdventureBundleError, match="cannot override"):
        loader.load("lanterns_of_greymoor", "en")


def test_adventure_package_rejects_executable_content(tmp_path: Path) -> None:
    loader, package = _copied_package(tmp_path)
    adventure_path = package / "adventure.json"
    adventure = json.loads(adventure_path.read_text(encoding="utf-8"))
    adventure["script"] = "do_not_execute()"
    adventure_path.write_text(json.dumps(adventure), encoding="utf-8")

    with pytest.raises(AdventureBundleError, match="executable content"):
        loader.load("lanterns_of_greymoor", "zh-CN")


def test_adventure_step_must_reference_a_catalogued_encounter(tmp_path: Path) -> None:
    loader, package = _copied_package(tmp_path)
    adventure_path = package / "adventure.json"
    adventure = json.loads(adventure_path.read_text(encoding="utf-8"))
    adventure["steps"][0]["encounter_preset_id"] = "missing_werewolf"
    adventure_path.write_text(json.dumps(adventure), encoding="utf-8")

    with pytest.raises(AdventureBundleError, match="encounter preset is missing"):
        loader.load("lanterns_of_greymoor", "zh-CN")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda adventure: adventure["steps"].append(dict(adventure["steps"][0])),
        lambda adventure: adventure["chapters"][0]["step_ids"].remove("keepers_plea"),
        lambda adventure: adventure["chapters"][0]["step_ids"].append("thorn_ambush"),
        lambda adventure: adventure["choices"].append(dict(adventure["choices"][0])),
        lambda adventure: adventure["choices"][0].update({"step_id": "missing_step"}),
    ],
)
def test_adventure_graph_rejects_editor_integrity_errors(
    tmp_path: Path, mutation,
) -> None:
    loader, package = _copied_package(tmp_path)
    adventure_path = package / "adventure.json"
    adventure = json.loads(adventure_path.read_text(encoding="utf-8"))
    mutation(adventure)
    adventure_path.write_text(json.dumps(adventure), encoding="utf-8")

    with pytest.raises(AdventureBundleError):
        loader.load("lanterns_of_greymoor", "zh-CN")


def test_adventure_graph_rejects_disconnected_step(tmp_path: Path) -> None:
    loader, package = _copied_package(tmp_path)
    adventure_path = package / "adventure.json"
    adventure = json.loads(adventure_path.read_text(encoding="utf-8"))
    adventure["steps"].append({
        "id": "orphan_step", "chapter_id": "old_shrine", "scene_ref": "",
        "requires": "none", "choice_ids": [],
    })
    adventure["chapters"][2]["step_ids"].append("orphan_step")
    adventure_path.write_text(json.dumps(adventure), encoding="utf-8")

    with pytest.raises(AdventureBundleError):
        loader.load("lanterns_of_greymoor", "zh-CN")


def test_adventure_rejects_invalid_generated_monster_stats(tmp_path: Path) -> None:
    loader, package = _copied_package(tmp_path)
    encounter_path = package / "content" / "encounters" / "greymoor_encounters.json"
    encounter = json.loads(encounter_path.read_text(encoding="utf-8"))
    encounter["presets"][0]["enemies"][0]["hp"] = 0
    encounter_path.write_text(json.dumps(encounter), encoding="utf-8")

    with pytest.raises(AdventureBundleError):
        loader.load("lanterns_of_greymoor", "zh-CN")
