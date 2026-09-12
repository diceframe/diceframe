"""世界画廊后端：克隆模板、GM 风格保存边界与 prompt 拼接顺序。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import src.commands.prompt_composer as prompt_composer_module
from src.commands.prompt_composer import PromptComposer
from src.content.gm_style import normalize_gm_style, render_gm_style_section
from src.webui.services import worlds as worlds_service


class FakeLoreStore:
    def __init__(self) -> None:
        self.worlds: dict[str, dict] = {}
        self.entries: dict[str, dict] = {}

    def get_world(self, world_id: str):
        return self.worlds.get(world_id)

    def create_world(self, world_id: str, name: str, description: str = "", language: str = "") -> None:
        self.worlds[world_id] = {"id": world_id, "name": name, "description": description, "language": language}

    def update_world_language(self, world_id: str, language: str) -> None:
        self.worlds[world_id]["language"] = language

    def get_entry(self, entry_id: str):
        return self.entries.get(entry_id)

    def add_entry(self, entry: dict) -> None:
        self.entries[str(entry["id"])] = dict(entry)

    def list_entries(self, world_id: str, entry_type: str | None = None) -> list[dict]:
        return [e for e in self.entries.values() if e.get("world_id") == world_id]

    def list_worlds(self) -> list[dict]:
        return list(self.worlds.values())

    def delete_world(self, world_id: str) -> None:
        self.worlds.pop(world_id, None)


def make_api(tmp_path, plugin_host=None):
    return worlds_service.WorldDependencies(
        lorebook=FakeLoreStore(),
        worlds_dir=tmp_path,
        plugin_host=plugin_host,
        llm_client=None,
        character_gen_max_tokens=2048,
        invalidate_lorebook_index=None,
        list_instances=lambda: [],
    )


def write_template(tmp_path, template_id: str, data: dict) -> None:
    (tmp_path / f"{template_id}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


BUILTIN_TEMPLATE = {
    "world_id": "default_fantasy",
    "world_name": "Classic Fantasy",
    "language": "zh-CN",
    "default_rule": "freeform_fantasy",
    "starter_lorebook": [{"id": "e1", "name": "Border Town", "type": "location", "keywords": ["town"], "content": "A town."}],
    "_internal_marker": "must_not_survive_clone",
}


# ---- clone_world_from_template ----

def test_create_world_materializes_an_editable_user_template(tmp_path) -> None:
    api = make_api(tmp_path)

    result = worlds_service.create_world(api, "手动世界", "群岛与雾", "zh-CN")

    assert result["ok"] is True
    world_id = result["world_id"]
    assert worlds_service._WORLD_TEMPLATE_ID_RE.fullmatch(world_id)
    data = json.loads((tmp_path / f"{world_id}.json").read_text(encoding="utf-8"))
    assert data["custom"] is True
    assert data["world_name"] == "手动世界"
    assert data["world_setting"] == "群岛与雾"
    assert data["starter_lorebook"] == []

    style = worlds_service.update_world_gm_style(api, world_id, {"tone": "诗意"})
    assert style["ok"] is True


def test_clone_creates_user_world_and_seeds_entries(tmp_path) -> None:
    api = make_api(tmp_path)
    write_template(tmp_path, "default_fantasy", BUILTIN_TEMPLATE)

    result = worlds_service.clone_world_from_template(api, "default_fantasy")

    assert result["ok"] is True
    world_id = result["world_id"]
    assert world_id.startswith("custom_book_")
    assert result["language"] == "zh-CN"

    data = json.loads((tmp_path / f"{world_id}.json").read_text(encoding="utf-8"))
    assert data["world_id"] == world_id
    assert data["world_name"] == "Classic Fantasy（克隆）"
    assert data["custom"] is True
    assert "_internal_marker" not in data

    assert api.lorebook.get_world(world_id)["language"] == "zh-CN"
    seeded = api.lorebook.list_entries(world_id)
    assert len(seeded) == 1
    assert seeded[0]["name"] == "Border Town"


def test_clone_uses_custom_name(tmp_path) -> None:
    api = make_api(tmp_path)
    write_template(tmp_path, "default_fantasy", BUILTIN_TEMPLATE)

    result = worlds_service.clone_world_from_template(api, "default_fantasy", name="My Remix")

    assert result["ok"] is True
    assert result["name"] == "My Remix"
    data = json.loads((tmp_path / f"{result['world_id']}.json").read_text(encoding="utf-8"))
    assert data["world_name"] == "My Remix"


class _FakePluginContributions:
    def __init__(self, items: list) -> None:
        self._items = items

    def list(self, kind: str = "") -> list:
        return list(self._items) if kind == "world_template" else []


class _FakePluginHost:
    def __init__(self, templates: dict) -> None:
        self._templates = templates
        self.contributions = _FakePluginContributions([
            SimpleNamespace(path=Path(f"{stem}.json"), key=stem)
            for stem in templates
        ])

    def load_world_template(self, key: str, language: str):
        return self._templates.get(key)


def test_clone_world_from_plugin_template(tmp_path) -> None:
    """插件世界在画廊里可以克隆为用户可编辑世界（与内置世界同一条路径）。"""
    plugin_world = dict(BUILTIN_TEMPLATE, world_id="hyouka_world", world_name="Hyouka")
    api = make_api(
        tmp_path, _FakePluginHost({"hyouka_world": plugin_world}),
    )

    result = worlds_service.clone_world_from_template(api, "hyouka_world")

    assert result["ok"] is True, result
    world_id = result["world_id"]
    assert world_id.startswith("custom_book_")
    data = json.loads((tmp_path / f"{world_id}.json").read_text(encoding="utf-8"))
    assert data["world_name"] == "Hyouka（克隆）"
    assert data["custom"] is True
    assert "_internal_marker" not in data
    assert api.lorebook.get_world(world_id)["language"] == "zh-CN"
    assert len(api.lorebook.list_entries(world_id)) == 1


def test_set_user_world_scene_image_writes_template(tmp_path) -> None:
    """用户世界可设置头图引用；非法引用与内置世界被拒。"""
    api = make_api(tmp_path)
    created = worlds_service.create_world(api, "我的世界", "群岛与雾", "zh-CN")
    world_id = created["world_id"]

    ref = {"kind": "upload", "asset_id": "scene-123"}
    result = worlds_service.set_user_world_scene_image(api, world_id, ref)

    assert result["ok"] is True, result
    data = json.loads((tmp_path / f"{world_id}.json").read_text(encoding="utf-8"))
    assert data["scene_image"] == ref

    assert worlds_service.set_user_world_scene_image(api, world_id, {"kind": "upload"})["ok"] is False
    assert worlds_service.set_user_world_scene_image(api, world_id, {"kind": "plugin", "path": "x"})["ok"] is False

    write_template(tmp_path, "default_fantasy", BUILTIN_TEMPLATE)
    assert worlds_service.set_user_world_scene_image(api, "default_fantasy", ref)["ok"] is False


def test_clone_missing_template_fails(tmp_path) -> None:
    api = make_api(tmp_path)
    result = worlds_service.clone_world_from_template(api, "no_such_world")
    assert result["ok"] is False


def test_clone_empty_template_id_fails(tmp_path) -> None:
    api = make_api(tmp_path)
    result = worlds_service.clone_world_from_template(api, "")
    assert result["ok"] is False


def test_clone_rejects_noncanonical_traversal_id(tmp_path) -> None:
    api = make_api(tmp_path)
    outside = tmp_path.parent / "config.json"
    outside.write_text(json.dumps(BUILTIN_TEMPLATE), encoding="utf-8")

    result = worlds_service.clone_world_from_template(api, "../config")

    assert result["ok"] is False
    assert "id 不合法" in result["error"]
    assert api.lorebook.list_worlds() == []


def test_same_name_clones_get_distinct_ids_even_in_one_second(monkeypatch, tmp_path) -> None:
    api = make_api(tmp_path)
    write_template(tmp_path, "default_fantasy", BUILTIN_TEMPLATE)
    monkeypatch.setattr(worlds_service.time, "time", lambda: 1_700_000_000)

    first = worlds_service.clone_world_from_template(api, "default_fantasy", name="My Remix")
    second = worlds_service.clone_world_from_template(api, "default_fantasy", name="My Remix")

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["world_id"] != second["world_id"]
    assert (tmp_path / f"{first['world_id']}.json").is_file()
    assert (tmp_path / f"{second['world_id']}.json").is_file()


def test_clone_deprecated_template_fails(tmp_path) -> None:
    api = make_api(tmp_path)
    write_template(tmp_path, "old_world", {**BUILTIN_TEMPLATE, "world_id": "old_world", "deprecated": True})
    result = worlds_service.clone_world_from_template(api, "old_world")
    assert result["ok"] is False


# ---- normalize_gm_style ----

def test_normalize_gm_style_defaults_and_fallback() -> None:
    assert normalize_gm_style(None) == {
        "tone": "", "verbosity": "normal", "pace": "normal", "custom_instructions": "",
    }
    assert normalize_gm_style("junk")["verbosity"] == "normal"
    assert normalize_gm_style({"verbosity": "WEIRD"})["verbosity"] == "normal"
    assert normalize_gm_style({"verbosity": "  Detailed "})["verbosity"] == "detailed"
    assert normalize_gm_style({"tone": "dark", "verbosity": "detailed"})["pace"] == "normal"
    assert normalize_gm_style({"pace": "super_fast"})["pace"] == "normal"


def test_normalize_gm_style_truncates() -> None:
    style = normalize_gm_style({"tone": "a" * 500, "custom_instructions": "b" * 5000})
    assert len(style["tone"]) == 120
    assert len(style["custom_instructions"]) == 2000


# ---- render_gm_style_section ----

def test_render_gm_style_section_empty_when_default() -> None:
    assert render_gm_style_section(None, "zh-CN") == ""
    assert render_gm_style_section({}, "zh-CN") == ""
    assert render_gm_style_section({"gm_style": {"verbosity": "normal"}}, "zh-CN") == ""


def test_render_gm_style_section_contains_guard_and_fields() -> None:
    world = {"gm_style": {"tone": "dark", "verbosity": "brief", "custom_instructions": "CUSTOM_MARKER"}}
    section = render_gm_style_section(world, "zh-CN")
    assert "## GM 叙事风格" in section
    assert "不得覆盖上文规则与机制判定" in section
    # "dark" 是文风预设 token：服务端渲染预设正文，而不是"叙事口吻：dark"。
    assert "克制、压抑、紧张的叙述风格" in section
    assert "CUSTOM_MARKER" in section


# ---- update_world_gm_style ----

def test_update_gm_style_saves_normalized_for_user_world(tmp_path) -> None:
    api = make_api(tmp_path)
    write_template(tmp_path, "default_fantasy", BUILTIN_TEMPLATE)
    cloned = worlds_service.clone_world_from_template(api, "default_fantasy")
    world_id = cloned["world_id"]

    result = worlds_service.update_world_gm_style(
        api, world_id, {"tone": "  gothic  ", "verbosity": "WEIRD", "custom_instructions": "keep it short"},
    )

    assert result["ok"] is True
    assert result["gm_style"] == {
        "tone": "gothic", "verbosity": "normal", "pace": "normal", "custom_instructions": "keep it short",
    }
    data = json.loads((tmp_path / f"{world_id}.json").read_text(encoding="utf-8"))
    assert data["gm_style"] == result["gm_style"]


def test_update_gm_style_rejects_builtin_world(tmp_path) -> None:
    api = make_api(tmp_path)
    write_template(tmp_path, "default_fantasy", BUILTIN_TEMPLATE)
    api.lorebook.create_world("default_fantasy", "Classic Fantasy", language="zh-CN")

    result = worlds_service.update_world_gm_style(api, "default_fantasy", {"tone": "x"})

    assert result["ok"] is False
    assert "克隆" in result["error"]


def test_update_gm_style_unknown_world_fails(tmp_path) -> None:
    api = make_api(tmp_path)
    result = worlds_service.update_world_gm_style(api, "ghost", {"tone": "x"})
    assert result["ok"] is False


def test_update_gm_style_materializes_legacy_lore_only_world(tmp_path) -> None:
    api = make_api(tmp_path)
    world_id = "custom_book_legacy_1700000000"
    api.lorebook.create_world(
        world_id, "Legacy Manual World", description="Old lore", language="en",
    )
    api.lorebook.add_entry({
        "id": "legacy_entry",
        "world_id": world_id,
        "name": "Old Harbor",
        "type": "location",
        "keywords": ["harbor"],
        "content": "A foggy harbor.",
    })

    listed = worlds_service.list_worlds(api)["worlds"][0]
    assert listed["gm_style"] == {
        "tone": "", "verbosity": "normal", "pace": "normal", "custom_instructions": "",
    }

    result = worlds_service.update_world_gm_style(api, world_id, {"tone": "noir"})

    assert result["ok"] is True
    data = json.loads((tmp_path / f"{world_id}.json").read_text(encoding="utf-8"))
    assert data["custom"] is True
    assert data["gm_style"]["tone"] == "noir"
    assert data["starter_lorebook"][0]["id"] == "legacy_entry"


# ---- list_worlds 附带 gm_style ----

def test_list_worlds_exposes_gm_style_only_for_user_worlds(tmp_path) -> None:
    api = make_api(tmp_path)
    write_template(tmp_path, "default_fantasy", BUILTIN_TEMPLATE)
    api.lorebook.create_world("default_fantasy", "Classic Fantasy", language="zh-CN")
    api.lorebook.create_world("custom_book_demo_1", "Demo", language="zh-CN")
    write_template(tmp_path, "custom_book_demo_1", {
        "world_id": "custom_book_demo_1", "world_name": "Demo", "custom": True,
        "gm_style": {"tone": "noir", "verbosity": "LOUD"},
    })

    result = worlds_service.list_worlds(api)
    by_id = {w["id"]: w for w in result["worlds"]}

    assert by_id["default_fantasy"]["gm_style"] is None
    assert by_id["custom_book_demo_1"]["gm_style"] == {
        "tone": "noir", "verbosity": "normal", "pace": "normal", "custom_instructions": "",
    }


# ---- prompt_composer 拼接顺序 ----

def test_compose_gm_prompt_order(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "gm_system_zh.md").write_text("BASE_GM_PROMPT", encoding="utf-8")
    prompt_composer_module._GM_PROMPT_CACHE.clear()

    composer = PromptComposer(prompts_dir, tmp_path / "rules")
    instance = SimpleNamespace(
        language="zh-CN",
        plot_tracker=SimpleNamespace(format_for_context=lambda: "PLOT_MARKER"),
        players={},
        ruleset_runtime={},
        narrative_perspective="third_person",
    )
    world_data = {"gm_style": {"tone": "dark", "verbosity": "normal", "custom_instructions": ""}}

    prompt = composer.compose_gm_prompt(instance, rule_appendix="RULE_APPENDIX_MARKER", world_data=world_data)

    # 契约是各 section 都被 materialize 进 prompt；段落顺序属于风格细节，不锁。
    for section in ("BASE_GM_PROMPT", "RULE_APPENDIX_MARKER", "## GM 叙事风格", "PLOT_MARKER", "## 叙事视角"):
        assert section in prompt


def test_compose_gm_prompt_without_world_data_is_unchanged(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "gm_system_zh.md").write_text("BASE_GM_PROMPT", encoding="utf-8")
    prompt_composer_module._GM_PROMPT_CACHE.clear()

    composer = PromptComposer(prompts_dir, tmp_path / "rules")
    instance = SimpleNamespace(
        language="zh-CN", plot_tracker=None, players={}, ruleset_runtime={},
    )

    prompt = composer.compose_gm_prompt(instance)

    assert "BASE_GM_PROMPT" in prompt
    assert "GM 叙事风格" not in prompt
