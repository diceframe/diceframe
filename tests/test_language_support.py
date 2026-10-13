from pathlib import Path

import src.commands.prompt_composer as prompt_module
from src.commands.prompt_composer import PromptComposer
from src.engine.game_instance import GameInstance
from src.engine.language import gm_language_instruction, localized_field, localized_text, normalize_language


def test_language_normalization_accepts_english_aliases():
    assert normalize_language("en-US") == "en"
    assert normalize_language("english") == "en"
    assert normalize_language("zh_CN") == "zh-CN"
    assert normalize_language("unknown") == "zh-CN"


def test_game_instance_persists_language_roundtrip():
    inst = GameInstance(game_key=("web", "english_room", "bot"), language="en")

    data = inst.to_dict()
    restored = GameInstance.from_dict(data)

    assert data["language"] == "en"
    assert restored.language == "en"
    assert restored.to_llm_view()["language"] == "en"


def test_gm_prompt_appends_english_language_instruction(tmp_path: Path, monkeypatch):
    prompts = tmp_path / "prompts"
    rules = tmp_path / "rules"
    prompts.mkdir()
    rules.mkdir()
    (prompts / "gm_system_zh.md").write_text("BASE GM PROMPT", encoding="utf-8")
    inst = GameInstance(game_key=("web", "english_room", "bot"), language="en")
    monkeypatch.setattr(prompt_module, "_GM_PROMPT_CACHE", None)

    prompt = PromptComposer(prompts, rules).compose_gm_prompt(inst)

    assert "BASE GM PROMPT" in prompt
    assert "QUICK_ACTIONS" in prompt


def test_auto_storyboard_prompt_does_not_allow_none_instead_of_panel(tmp_path: Path, monkeypatch):
    prompts = tmp_path / "prompts"
    rules = tmp_path / "rules"
    prompts.mkdir()
    rules.mkdir()
    (prompts / "gm_system_zh.md").write_text("BASE GM PROMPT", encoding="utf-8")
    inst = GameInstance(game_key=("web", "storyboard", "bot"), language="zh-CN")
    monkeypatch.setattr(prompt_module, "_GM_PROMPT_CACHE", None)

    composer = PromptComposer(prompts, rules)
    composer.set_auto_storyboard(True)
    prompt = composer.compose_gm_prompt(inst)

    assert "SCENE_PANEL" in prompt
    assert "禁止只输出 NONE 代替分镜" in prompt


def test_language_instruction_keeps_protocol_tags_in_english_mode():
    instruction = gm_language_instruction("en")

    assert "HP" in instruction
    assert "GOLD" in instruction
    assert "LOOT" in instruction
    assert "SCENE" in instruction


def test_localized_text_falls_back_to_english_for_ja_missing():
    texts = {"zh-CN": "中文", "en": "English"}
    # ja 缺失时回退 en，而非 zh-CN。
    assert localized_text("ja", texts) == "English"
    # en 缺失时回退 zh-CN。
    assert localized_text("en", {"zh-CN": "中文"}) == "中文"
    # zh-CN 缺失时回退 fallback。
    assert localized_text("zh-CN", {"en": "English"}, "兜底") == "English"


def test_localized_field_falls_back_to_english_suffix_for_ja():
    template = {"name": "中文名", "name_en": "English Name"}
    # ja 缺失 _ja 字段 → 回退 _en 字段。
    assert localized_field(template, "name", "ja") == "English Name"
    # zh-CN 用原字段。
    assert localized_field(template, "name", "zh-CN") == "中文名"
    # en 用 _en 字段。
    assert localized_field(template, "name", "en") == "English Name"
    # 无 _en 时回退原字段。
    assert localized_field({"name": "中文名"}, "name", "ja") == "中文名"


def test_prompt_composer_passes_game_language_to_world_and_rule_loaders():
    root = Path(__file__).resolve().parents[1]
    instance = GameInstance(
        game_key=("web", "locale-runtime", "bot"),
        world_id="default_fantasy",
        rule_id="freeform_fantasy",
        language="en",
    )
    calls: list[tuple[str, str]] = []

    def load_world(world_id: str, language: str):
        calls.append((world_id, language))
        from src.content.worlds import load_world_template
        return load_world_template(root / "templates" / "worlds", world_id, language)

    context = PromptComposer(
        root / "prompts", root / "templates" / "rules",
    ).load_rule_context(instance, load_world)

    assert ("default_fantasy", "en") in calls
    assert context.world_data["active_locale"] == "en"
    assert context.rule.rule_name == "Classic Fantasy Freeform"
