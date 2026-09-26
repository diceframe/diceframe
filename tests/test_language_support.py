from pathlib import Path

import src.commands.prompt_composer as prompt_module
from src.commands.prompt_composer import PromptComposer
from src.engine.game_instance import GameInstance
from src.engine.language import (
    content_locale_candidates,
    gm_language_instruction,
    localized_field,
    localized_text,
    normalize_language,
)


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


ROOT = Path(__file__).resolve().parents[1]


def test_content_locale_candidates_insert_english_before_the_chinese_core():
    assert content_locale_candidates("ru") == ["ru", "en"]
    assert content_locale_candidates("de") == ["de", "en"]
    assert content_locale_candidates("ru-RU") == ["ru-RU", "ru", "en"]
    assert content_locale_candidates("") == []


def test_content_locale_candidates_never_send_chinese_through_english():
    # The core template file already holds the Chinese text, so a Chinese
    # request must fall through to it rather than pick up an English overlay.
    assert "en" not in content_locale_candidates("zh-CN")
    assert "en" not in content_locale_candidates("zh")


def test_world_template_falls_back_to_english_for_a_locale_without_content():
    from src.content.worlds import load_world_template

    worlds = ROOT / "templates" / "worlds"
    chinese = load_world_template(worlds, "greymoor", "zh-CN") or {}
    english = load_world_template(worlds, "greymoor", "en") or {}
    russian = load_world_template(worlds, "greymoor", "ru") or {}

    assert english.get("active_locale") == "en"
    assert russian.get("active_locale") == "en"
    assert russian.get("world_name") == english.get("world_name")
    assert chinese.get("world_name") != english.get("world_name")
    assert english.get("world_name")


def test_rule_locale_falls_back_to_english_but_keeps_its_own_translation():
    from src.rules.rule_system import RuleSystem

    rules = ROOT / "templates" / "rules"
    assert RuleSystem.path_for(rules, "dnd5e", "en").parent.name == "en"
    # ja ships its own overlay and must keep it.
    assert RuleSystem.path_for(rules, "dnd5e", "ja").parent.name == "ja"
    # ru and de ship none, and English beats the Chinese core.
    for language in ("ru", "de"):
        assert RuleSystem.path_for(rules, "dnd5e", language).parent.name == "en"
    # Chinese still resolves to the core file, not to an overlay.
    assert RuleSystem.path_for(rules, "dnd5e", "zh-CN").name == "dnd5e.json"


def test_ruleset_bundle_selects_english_for_an_unsupported_locale():
    from src.rulesets.bundle import RulesetBundleLoader

    loader = RulesetBundleLoader(ROOT / "templates" / "rulesets")
    assert loader.load("dnd2024_srd", "zh-CN").locale == "zh-CN"
    assert loader.load("dnd2024_srd", "en").locale == "en"
    for language in ("ru", "de", "ja"):
        assert loader.load("dnd2024_srd", language).locale == "en"
    # No request at all still means the bundle default.
    assert loader.load("dnd2024_srd", "").locale == "zh-CN"


def test_default_character_sheet_carries_no_chinese_into_other_languages():
    import re

    from src.engine.character_utils import make_default_character

    templates = ROOT / "templates"
    han = re.compile(r"[\u4e00-\u9fff]")

    # A Chinese game keeps its Chinese sheet.
    chinese = make_default_character("T", "freeform_fantasy", templates, "zh-CN")
    assert han.search(str(chinese))

    # Every other language must not leak Chinese into the sheet, because the
    # sheet goes into the GM context and pulls narration back to Chinese.
    for language in ("en", "ru", "de"):
        sheet = make_default_character("T", "freeform_fantasy", templates, language)
        assert not han.search(str(sheet)), f"Chinese leaked into the {language} sheet"
        assert sheet["race"]
        assert sheet["inventory"][0]["name"]
        assert sheet["resources"]["hp"]["label"]

    # ja ships its own rule locale and must keep its own script.
    japanese = make_default_character("T", "freeform_fantasy", templates, "ja")
    assert japanese["race"] == "人間"
