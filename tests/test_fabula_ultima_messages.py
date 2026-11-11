"""Fabula Ultima 运行时文案表的守卫。

- 每条文案都有 ``zh-CN`` 与 ``en``，两种语言的占位符同名；
- ``runtime`` / ``conflict`` / ``content_store`` 里取用的 key 都在表里，表里没有闲置的 key，
  每个调用点都给齐了占位符；
- 文案跟随对局语言，但 canonical 值（判定结论、id、数值）不随语言变化。
"""

from __future__ import annotations

import ast
import random
import re
import string
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.game_instance import GameInstance, GameState
from src.engine.player_control import set_control
from src.rulesets.fabula_ultima import conflict, content_store, runtime, runtime_messages
from src.rulesets.fabula_ultima.content import ATTRIBUTE_NAMES
from src.rulesets.fabula_ultima.i18n import REQUIRED_LANGUAGES, missing_translations, render
from src.rulesets.fabula_ultima.runtime_messages import LABEL_TABLES, MESSAGES

PACKAGE = Path(runtime.__file__).resolve().parent
SOURCES = ("runtime.py", "conflict.py", "content_store.py")
#: 直接读 ``MESSAGES`` 的两个入口：``render(language, MESSAGES, key, ...)`` 与
#: runtime 内的 ``_text(instance, key, ...)``；value 是 key 参数的位置。
KEY_POSITION = {"render": 2, "_text": 1}
VERDICTS = {"大成功", "成功", "失败", "大失败"}
_CJK = re.compile(r"[一-鿿]")

ALL_TABLES = {"MESSAGES": MESSAGES, **LABEL_TABLES}


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def _literal_keys(node: ast.expr) -> list[str] | None:
    """key 参数只能是字符串字面量，或在字面量之间二选一的条件表达式。"""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        left, right = _literal_keys(node.body), _literal_keys(node.orelse)
        return None if left is None or right is None else left + right
    return None


class _CatalogCalls(ast.NodeVisitor):
    """收集一个源文件里所有读文案表的调用。"""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.calls: list[tuple[int, list[str], set[str]]] = []  # (行号, 候选 key, 传入的占位符)
        self.problems: list[str] = []
        self._functions: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        if name not in KEY_POSITION:
            return
        where = f"{self.filename}:{node.lineno}"
        if name == "render":
            table = node.args[1] if len(node.args) > 1 else None
            if not isinstance(table, ast.Name):
                self.problems.append(f"{where}: render() 的文案表必须是具名的表")
                return
            if table.id != "MESSAGES":
                if table.id not in LABEL_TABLES:
                    self.problems.append(f"{where}: 未知的文案表 {table.id}")
                return
            if self._functions and self._functions[-1] == "_text":
                return  # _text 自身把 key 原样转给 render
        position = KEY_POSITION[name]
        keys = _literal_keys(node.args[position]) if len(node.args) > position else None
        if keys is None:
            self.problems.append(f"{where}: MESSAGES 的 key 必须是字面量（动态查表请用 *_LABELS）")
            return
        if any(keyword.arg is None for keyword in node.keywords):
            self.problems.append(f"{where}: 占位符必须逐个具名传入")
            return
        self.calls.append((node.lineno, keys, {str(keyword.arg) for keyword in node.keywords}))


def _scan() -> tuple[list[tuple[str, int, list[str], set[str]]], list[str]]:
    calls: list[tuple[str, int, list[str], set[str]]] = []
    problems: list[str] = []
    for filename in SOURCES:
        visitor = _CatalogCalls(filename)
        visitor.visit(ast.parse((PACKAGE / filename).read_text(encoding="utf-8-sig")))
        problems.extend(visitor.problems)
        calls.extend((filename, line, keys, params) for line, keys, params in visitor.calls)
    return calls, problems


# ---------------------------------------------------------------------- 文案表本身

@pytest.mark.parametrize("name", sorted(ALL_TABLES))
def test_every_entry_has_both_required_languages(name: str) -> None:
    assert missing_translations(ALL_TABLES[name]) == []


@pytest.mark.parametrize("name", sorted(ALL_TABLES))
def test_placeholders_match_between_languages(name: str) -> None:
    mismatched = {
        key: {language: sorted(_placeholders(variants[language])) for language in REQUIRED_LANGUAGES}
        for key, variants in ALL_TABLES[name].items()
        if len({frozenset(_placeholders(variants[language])) for language in REQUIRED_LANGUAGES}) != 1
    }
    assert mismatched == {}


@pytest.mark.parametrize("name", sorted(ALL_TABLES))
def test_templates_render_cleanly(name: str) -> None:
    """没有占位符的文案不经过 ``str.format``，所以不能带花括号；带占位符的必须能完整渲染。"""

    for key, variants in ALL_TABLES[name].items():
        for language in REQUIRED_LANGUAGES:
            template = variants[language]
            fields = _placeholders(template)
            if not fields:
                assert "{" not in template and "}" not in template, f"{name}[{key}][{language}]"
                continue
            rendered = render(language, ALL_TABLES[name], key, **{field: f"<{field}>" for field in fields})
            assert all(f"<{field}>" in rendered for field in fields), f"{name}[{key}][{language}]"


def test_only_new_copy_languages_are_maintained_here() -> None:
    """新文案只写 zh-CN 与 en；其它语言走回退链，由社区另行维护。"""

    extra = {
        f"{name}[{key}]": sorted(set(variants) - set(REQUIRED_LANGUAGES))
        for name, table in ALL_TABLES.items()
        for key, variants in table.items()
        if set(variants) - set(REQUIRED_LANGUAGES)
    }
    assert extra == {}


def test_label_tables_cover_their_canonical_ids() -> None:
    assert set(runtime_messages.ATTRIBUTE_LABELS) == set(runtime.ATTR_KEYS) == set(conflict.ATTR_KEYS)
    assert set(runtime_messages.ACTION_LABELS) == {item["id"] for item in conflict.ACTIONS}
    assert set(runtime_messages.AFFINITY_LABELS) == {"weak", "resist", "immune", "absorb"}
    assert set(runtime_messages.VERDICT_LABELS) == VERDICTS
    # 中文显示名与既有的 canonical 写法保持一致。
    assert {key: row["zh-CN"] for key, row in runtime_messages.ATTRIBUTE_LABELS.items()} == ATTRIBUTE_NAMES
    assert all(row["zh-CN"] == key for key, row in runtime_messages.VERDICT_LABELS.items())


# ---------------------------------------------------------------------- 源码里的取用

def test_catalog_keys_are_literal_and_exist() -> None:
    calls, problems = _scan()
    assert problems == []
    assert calls, "没有扫描到任何文案调用，扫描器可能失效"
    unknown = sorted({
        f"{filename}:{line}: {key}" for filename, line, keys, _ in calls for key in keys if key not in MESSAGES
    })
    assert unknown == []


def test_catalog_has_no_unused_keys() -> None:
    calls, _ = _scan()
    used = {key for _, _, keys, _ in calls for key in keys}
    assert sorted(set(MESSAGES) - used) == []


def test_call_sites_supply_every_placeholder() -> None:
    calls, _ = _scan()
    missing = sorted(
        f"{filename}:{line}: {key} 缺少 {sorted(_placeholders(MESSAGES[key]['en']) - params)}"
        for filename, line, keys, params in calls
        for key in keys
        if key in MESSAGES and _placeholders(MESSAGES[key]["en"]) - params
    )
    assert missing == []


# ---------------------------------------------------------------------- 行为：文案跟随对局语言

def _instance(language: str) -> GameInstance:
    instance = GameInstance(game_key=("web", f"fu-messages-{language}", "bot"), gm_uid="gm", rule_id="fabula_ultima")
    instance.language = language
    instance.state = GameState.ACTIVE_ACTION
    for uid in ("p1", "p2"):
        instance.put_player(uid, {
            "user_id": uid,
            "character_name": uid,
            "character_sheet": {
                "attributes": {"dex": 10, "ins": 8, "mig": 8, "wlp": 6},
                "hp": 40, "max_hp": 40, "mp": 30, "max_mp": 30, "ip": 3, "fabula": 2,
                "skills": [], "equipment": [{"name": "blade", "type": "weapon", "damage": 4}], "inventory": [],
            },
        })
        set_control(instance, uid, "human")
    return instance


def _play(language: str, seed: int = 7) -> dict:
    """同一个随机种子下打一小段冲突，返回各步结果与最终状态。"""

    engine = runtime.FabulaUltimaRuntime()
    instance = _instance(language)
    rng = random.Random(seed)
    steps = [
        {"type": "conflict.start", "enemies": [{"template_id": "goblin", "name": "Gob"}]},
        {"type": "conflict.select_actor", "actor_id": "player:p1"},
        {"type": "conflict.action", "action": "attack", "actor_id": "player:p1", "target": "enemy:1"},
        {"type": "conflict.select_actor", "actor_id": "enemy:1"},
        {"type": "conflict.enemy_action", "action": "attack", "target": "player:p2"},
        {"type": "conflict.select_actor", "actor_id": "player:p2"},
        {"type": "conflict.action", "action": "investigate", "actor_id": "player:p2", "target": "enemy:1"},
        {"type": "conflict.end"},
    ]
    results = [engine.resolve_intent(instance, dict(step, requester_is_gm=True), rng) for step in steps]
    return {"results": results, "state": runtime.fu_state(instance), "instance": instance}


@pytest.fixture(autouse=True)
def _bundled_tables_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 不读本机 data/ 下用户编辑过的 FU 数据表。
    monkeypatch.setenv("TRPG_DATA_DIR", str(tmp_path))


def test_render_follows_language_and_falls_back() -> None:
    params = {"target": "Aria", "damage": 7}
    chinese = render("zh-CN", MESSAGES, "target_takes_damage", **params)
    english = render("en", MESSAGES, "target_takes_damage", **params)
    assert chinese == "Aria 受到 7 伤害"
    assert english == "Aria takes 7 damage"
    # 未设置语言时保持中文；没有维护的语言回退英文。
    assert render("", MESSAGES, "target_takes_damage", **params) == chinese
    assert render("de", MESSAGES, "target_takes_damage", **params) == english


def test_intent_errors_and_labels_follow_game_language() -> None:
    engine = runtime.FabulaUltimaRuntime()
    outputs = {}
    for language in ("zh-CN", "en"):
        instance = _instance(language)
        refusal = engine.validate_intent(instance, {"type": "conflict.start", "requester_is_gm": False})
        assert refusal["code"] == "GM_ONLY"
        assert refusal["error"] == MESSAGES["gm_only"][language]
        outputs[language] = engine.available_intents(instance, "p1")
        # 前置条件不满足时原样返回拒绝；结算阶段的校验失败则抛错。两者都跟随对局语言。
        assert engine.resolve_intent(instance, {"type": "conflict.end"}, random.Random(1)) == {
            "ok": False, "code": "NO_CONFLICT", "error": MESSAGES["no_conflict"][language],
        }
        with pytest.raises(ValueError) as error:
            engine.resolve_intent(_started(language), {"type": "rest.take"}, random.Random(1))
        assert str(error.value) == MESSAGES["rest_in_conflict"][language]
    assert [row["type"] for row in outputs["zh-CN"]] == [row["type"] for row in outputs["en"]]
    assert [row["label"] for row in outputs["zh-CN"]] != [row["label"] for row in outputs["en"]]
    assert not any(_CJK.search(row["label"]) for row in outputs["en"])


def test_conflict_text_changes_with_language_but_mechanics_do_not() -> None:
    chinese, english = _play("zh-CN"), _play("en")

    def mechanics(run: dict) -> list:
        rows = []
        for result in run["results"]:
            events = [
                {key: value for key, value in event.items() if key not in {"text", "affinity"}}
                for event in result["event_batch"]["events"]
            ]
            rows.append((result["ok"], events, len(result["summary"])))
        return rows

    assert mechanics(chinese) == mechanics(english)
    for run in (chinese, english):
        history = run["state"]["conflict"]["history"]
        assert history and all(entry["verdict"] in VERDICTS for entry in history if entry.get("verdict"))
    assert [entry.get("verdict") for entry in chinese["state"]["conflict"]["history"]] == [
        entry.get("verdict") for entry in english["state"]["conflict"]["history"]
    ]

    chinese_text = [line for result in chinese["results"] for line in result["summary"]]
    english_text = [line for result in english["results"] for line in result["summary"]]
    assert chinese_text != english_text
    assert all(_CJK.search(line) for line in chinese_text)
    assert not any(_CJK.search(line) for line in english_text)
    assert not any(_CJK.search(line) for line in english["state"]["log"])

    labels = {
        language: [row["label"] for row in runtime.FabulaUltimaRuntime().gameplay_view(_started(language))["fabula_ultima"]["actions"]]
        for language in ("zh-CN", "en")
    }
    assert labels["zh-CN"] == [runtime_messages.ACTION_LABELS[item["id"]]["zh-CN"] for item in conflict.ACTIONS]
    assert labels["en"] == [runtime_messages.ACTION_LABELS[item["id"]]["en"] for item in conflict.ACTIONS]


def _started(language: str) -> GameInstance:
    instance = _instance(language)
    runtime.FabulaUltimaRuntime().resolve_intent(
        instance, {"type": "conflict.start", "requester_is_gm": True, "enemies": [{"template_id": "goblin"}]},
        random.Random(3),
    )
    return instance


def test_affinity_label_is_display_only() -> None:
    assert conflict.affinity_multiplier({"fire": "weak"}, "fire", "zh-CN") == (2.0, "弱点")
    assert conflict.affinity_multiplier({"fire": "weak"}, "fire", "en") == (2.0, "Vulnerable")
    assert conflict.affinity_multiplier({"fire": "weak"}, "fire") == (2.0, "弱点")
    assert conflict.affinity_multiplier({}, "fire", "en") == (1.0, "")
    # 判定结论是 canonical 值，不随语言变化。
    assert conflict.verdict_of([6, 6], 12, 20) == "大成功"
    assert conflict.verdict_of([1, 1], 2, 1) == "大失败"


def test_character_validation_language_sources() -> None:
    engine = runtime.FabulaUltimaRuntime()

    def rule(active_locale: str = "") -> SimpleNamespace:
        template = {"rule_id": "fabula_ultima", "default_locale": "zh-CN"}
        if active_locale:
            template["active_locale"] = active_locale
        return SimpleNamespace(template=template, rule_id="fabula_ultima", get_class_names=lambda: [])

    draft = {"attributes": "broken"}
    assert engine.validate_character(rule(), draft) == [
        MESSAGES["char_attributes_not_object"]["zh-CN"], MESSAGES["char_class_required"]["zh-CN"],
    ]
    # 规则对象上套了英文 locale overlay 时跟随它；
    assert engine.validate_character(rule("en"), draft) == [
        MESSAGES["char_attributes_not_object"]["en"], MESSAGES["char_class_required"]["en"],
    ]
    # 提交角色时调用方给的是对局语言，优先于规则对象。
    with pytest.raises(ValueError) as chinese:
        engine.normalize_character_submission(rule(), draft, "zh-CN")
    with pytest.raises(ValueError) as english:
        engine.normalize_character_submission(rule(), draft, "en")
    assert str(chinese.value) == "属性必须是对象；必须选择职业"
    assert str(english.value) == "Attributes must be an object; A class must be chosen"

    valid = {
        "attributes": {"dex": 8, "ins": 8, "mig": 8, "wlp": 8},
        "class_levels": {"a": 3, "b": 2},
    }
    chinese_sheet = engine.normalize_character_submission(rule(), dict(valid), "zh-CN")
    english_sheet = engine.normalize_character_submission(rule(), dict(valid), "en")
    assert chinese_sheet["resources"]["fabula"]["label"] == "物语点"
    assert english_sheet["resources"]["fabula"]["label"] == "Fabula Points"
    assert chinese_sheet["currency"]["label"] == "泽尼特" and english_sheet["currency"]["label"] == "Zenit"
    # 语言只影响显示名，不影响数值。
    for sheet in (chinese_sheet, english_sheet):
        for resource in sheet["resources"].values():
            resource.pop("label")
        sheet["currency"].pop("label")
    assert chinese_sheet == english_sheet


def test_content_table_validation_errors_follow_language() -> None:
    with pytest.raises(ValueError) as default:
        content_store.validate("items", {"id": ""})
    with pytest.raises(ValueError) as english:
        content_store.validate("items", {"id": ""}, None, "en")
    assert str(default.value) == MESSAGES["content_id_required"]["zh-CN"]
    assert str(english.value) == MESSAGES["content_id_required"]["en"]
    assert content_store.validate("items", {"id": "a", "name": "A"}, None, "en") == content_store.validate(
        "items", {"id": "a", "name": "A"},
    )
