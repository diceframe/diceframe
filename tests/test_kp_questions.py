from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from src.bots.bridge_core.commands import kp_question
from src.commands.kp_questions import KPQuestionResponder, build_kp_question_prompt
from src.engine.game_instance import GameInstance
from src.llm.context_builder import build_player_safe_context
from src.webui.services.kp_questions import (
    KPQuestionDependencies,
    KPQuestionService,
)


def _instance() -> GameInstance:
    instance = GameInstance(
        game_key=("web", "questions", "bot"),
        world_id="",
        world_name="阿卡姆疑云",
        gm_uid="gm",
    )
    instance.solo_mode = False
    instance.players = {
        "p1": {
            "user_id": "p1",
            "character_name": "莱拉",
            "character_sheet": {"hp": 10, "max_hp": 10},
        },
        "p2": {
            "user_id": "p2",
            "character_name": "张恕轩",
            "character_sheet": {"hp": 12, "max_hp": 12},
        },
    }
    instance.round_number = 3
    instance.action_queue = [{"user_id": "p2", "text": "检查门锁"}]
    return instance


def test_kp_question_requires_an_explicit_separator() -> None:
    assert kp_question("询问 这个检定怎么判？") == "这个检定怎么判？"
    assert kp_question("询问：我知道教授的住址吗？") == "我知道教授的住址吗？"
    assert kp_question("ask: what can my character see?") == "what can my character see?"
    assert kp_question("ask kp what can my character see?") == "what can my character see?"
    assert kp_question("ask kp: is this a bonus action?") == "is this a bonus action?"
    assert kp_question("询问") == ""
    assert kp_question("询问：") == ""
    assert kp_question("ask:") == ""
    assert kp_question("ask kp:") == ""
    assert kp_question("询问守卫后门在哪里") is None
    assert kp_question("ask the guard where the cellar is") is None
    assert kp_question("ask") is None


class _Registry:
    def __init__(self, instance: GameInstance | None) -> None:
        self.instance = instance
        self.save_calls = 0

    def get(self, _key):
        return self.instance

    async def save(self, _instance) -> None:
        self.save_calls += 1


class _QuestionHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.instances: list[GameInstance] = []

    async def answer_kp_question(
        self, instance, actor_uid: str, question: str, visibility: str = "private",
    ) -> dict:
        self.calls.append((actor_uid, question, visibility))
        self.instances.append(instance)
        return {"answer": "你见过门上的密斯卡托尼克大学徽记。", "total_tokens": 42}


class _Api:
    def __init__(self, instance: GameInstance | None) -> None:
        self._reg = _Registry(instance)
        self._handler = _QuestionHandler()
        self.questions = KPQuestionService(KPQuestionDependencies(
            registry=self._reg,
            parse_game_key=lambda _game_key: ("web", "questions", "bot"),
            answer_question=self._handler.answer_kp_question,
        ))


@pytest.mark.asyncio
async def test_question_service_is_read_only_and_does_not_consume_action() -> None:
    instance = _instance()
    api = _Api(instance)
    before = copy.deepcopy(instance.to_dict())

    result = await api.questions.ask("game", "p1", "我认识门上的徽记吗？")

    assert result["status"] == 200
    assert result["payload"]["kind"] == "kp_table_talk"
    assert result["payload"]["advanced"] is False
    assert result["payload"]["action_consumed"] is False
    assert result["payload"]["round_number"] == 3
    assert api._handler.calls == [("p1", "我认识门上的徽记吗？", "private")]
    assert api._handler.instances[0] is not instance
    assert api._reg.save_calls == 0
    assert instance.to_dict() == before


@pytest.mark.asyncio
async def test_party_question_persists_only_the_separate_table_talk_exchange() -> None:
    instance = _instance()
    api = _Api(instance)

    result = await api.questions.ask(
        "game", "p1", "大家都知道这是什么吗？", "party",
    )

    assert result["status"] == 200
    assert result["payload"]["visibility"] == "party"
    assert result["payload"]["exchange"]["question"] == "大家都知道这是什么吗？"
    assert api._handler.calls == [("p1", "大家都知道这是什么吗？", "party")]
    assert api._reg.save_calls == 1
    assert instance.table_talk[-1]["answer"].startswith("你见过")
    assert instance.action_queue == [{"user_id": "p2", "text": "检查门锁"}]
    assert instance.round_number == 3
    assert "table_talk" not in instance.to_llm_view()


@pytest.mark.asyncio
async def test_question_service_requires_a_claimed_player() -> None:
    instance = _instance()
    api = _Api(instance)

    denied = await api.questions.ask("game", "stranger", "这里是哪？")
    empty = await api.questions.ask("game", "p1", "  ")

    assert denied["status"] == 403
    assert denied["payload"]["code"] == "PLAYER_NOT_IN_GAME"
    assert empty["status"] == 400
    assert empty["payload"]["code"] == "EMPTY_QUESTION"
    assert api._handler.calls == []


@pytest.mark.asyncio
async def test_question_service_rejects_while_the_game_is_processing() -> None:
    instance = _instance()
    api = _Api(instance)
    await instance._process_lock.acquire()
    try:
        result = await api.questions.ask("game", "p1", "现在是什么情况？")
    finally:
        instance._process_lock.release()

    assert result["status"] == 409
    assert result["payload"]["code"] == "GAME_PROCESSING"
    assert api._handler.calls == []


class _Matcher:
    def match_with_recursive(self, question: str, **_kwargs):
        # 检索文本现在是通用 LoreRetriever 的 lexical query：只有值，没有
        # [action]/[scene]/[location]/[present_npcs] 结构标签（否则标签本身会误触发关键词）。
        assert "[action]" not in question
        assert "[scene]" not in question
        assert "我知道这枚徽记吗？" in question
        _kwargs["timed_state"]["question-only"] = {"status": "active", "remaining": 2}
        return [
            {
                "name": "大学徽记",
                "content": "该角色在上一幕见过它。",
                "visible_to": ["p1"],
            },
            {
                "name": "地下室真相",
                "content": "不可泄露的隐藏世界书内容。",
                "visible_to": [],
            },
            {
                "name": "另一角色的秘密",
                "content": "只属于另一名角色。",
                "visible_to": ["p2"],
            },
            {
                "name": "全队知识",
                "content": "所有人都已经看过这个大厅。",
                "visible_to": ["party"],
            },
        ]


class _PromptComposer:
    def __init__(self) -> None:
        self.system_prompt = ""
        self.player_message = ""
        self.matches: list[dict] = []
        self.actor_uid = ""
        self.visibility = ""

    def load_rule_context(self, _instance, _loader):
        return SimpleNamespace(rule_appendix="规则附录", world_data=None)

    async def build_player_safe_context(
        self,
        _instance,
        system_prompt,
        matches,
        player_message,
        actor_uid,
        **_kwargs,
    ) -> str:
        self.system_prompt = system_prompt
        self.player_message = player_message
        self.matches = matches
        self.actor_uid = actor_uid
        self.visibility = str(_kwargs.get("visibility") or "")
        return "只读上下文"


class _LLM:
    default = "test"

    def __init__(self) -> None:
        self.call_args = None

    async def call(self, system_prompt, context, **kwargs):
        self.call_args = (system_prompt, context, kwargs)
        return SimpleNamespace(
            narration="你记得这是密斯卡托尼克大学的徽记。",
            content="你记得这是密斯卡托尼克大学的徽记。",
            total_tokens=33,
            provider_used="test",
        )


@pytest.mark.asyncio
async def test_responder_uses_a_non_mutating_table_talk_prompt() -> None:
    instance = _instance()
    prompt = _PromptComposer()
    llm = _LLM()
    responder = KPQuestionResponder(
        llm,
        _Matcher(),
        prompt,
        lambda *_args: None,
        lambda *_args: None,
    )
    before = copy.deepcopy(instance.to_dict())

    result = await responder.answer(instance, "p1", "我知道这枚徽记吗？")

    assert result["answer"].startswith("你记得")
    assert prompt.actor_uid == "p1"
    assert [entry["name"] for entry in prompt.matches] == ["大学徽记", "全队知识"]
    assert prompt.visibility == "private"
    assert "不可泄露" not in str(prompt.matches)
    assert instance.to_dict() == before


@pytest.mark.asyncio
async def test_party_responder_uses_only_public_lore() -> None:
    prompt = _PromptComposer()
    responder = KPQuestionResponder(
        _LLM(), _Matcher(), prompt, lambda *_args: None, lambda *_args: None,
    )

    await responder.answer(_instance(), "p1", "我知道这枚徽记吗？", "party")

    assert [entry["name"] for entry in prompt.matches] == ["全队知识"]
    assert prompt.visibility == "party"
    assert "共享给全队" in prompt.system_prompt


def test_prompt_treats_unknown_outcomes_as_future_actions() -> None:
    prompt = build_kp_question_prompt(_instance(), "莱拉")

    assert "游戏内行动" in prompt
    assert "不要替他执行或结算" in prompt
    assert "纯文本回答" in prompt


@pytest.mark.asyncio
async def test_player_safe_context_excludes_hidden_and_other_player_data() -> None:
    instance = _instance()
    instance.players["p1"]["character_sheet"].update({
        "background": "提问者自己的秘密背景",
        "inventory": ["提问者的银钥匙"],
    })
    instance.players["p2"]["character_sheet"].update({
        "class": "不应暴露的职业",
        "inventory": ["另一玩家的秘密道具"],
    })
    instance.log = [{
        "round": 2,
        "actions": [{"user_id": "p1", "text": "查看公开徽记"}],
        "gm_response": "所有人都看见了大学徽记。",
    }]
    instance.summary = {"narrative": "队伍公开进入了旧宅。"}
    instance.key_facts = [{"content": "旧宅正门已经锁上。"}]
    instance.private_log = {
        "p1": [{"round": 2, "text": "你独自闻到海水味。"}],
        "p2": [{"round": 2, "text": "另一玩家看见了密道。"}],
    }

    context = await build_player_safe_context(
        instance,
        "安全系统提示",
        [
            {"name": "公开授权知识", "content": "角色可知", "visible_to": ["public"]},
            {"name": "角色授权知识", "content": "只给莱拉", "visible_to": ["p1"]},
            {"name": "隐藏知识", "content": "地下室真正藏着王冠", "visible_to": []},
            {"name": "他人知识", "content": "另一角色的身世", "visible_to": ["p2"]},
        ],
        "询问地下室里有什么？",
        "p1",
        provider_name="test",
    )

    assert "提问者自己的秘密背景" in context
    assert "提问者的银钥匙" in context
    assert "另一玩家的秘密道具" not in context
    assert "不应暴露的职业" not in context
    assert "所有人都看见了大学徽记" in context
    assert "你独自闻到海水味" in context
    assert "另一玩家看见了密道" not in context
    assert "公开授权知识" in context
    assert "角色授权知识" in context
    assert "地下室真正藏着王冠" not in context
    assert "另一角色的身世" not in context


@pytest.mark.asyncio
async def test_party_safe_context_excludes_questioner_private_knowledge() -> None:
    instance = _instance()
    instance.players["p1"]["character_sheet"].update({
        "background": "提问者的秘密背景",
        "inventory": ["提问者私藏的银钥匙"],
    })
    instance.private_log = {
        "p1": [{"round": 2, "text": "只有你听见阁楼的脚步声。"}],
    }

    context = await build_player_safe_context(
        instance,
        "全队安全系统提示",
        [
            {"name": "全队知识", "content": "大家都看见了徽记", "visible_to": ["party"]},
            {"name": "角色知识", "content": "只有莱拉知道", "visible_to": ["p1"]},
        ],
        "大家知道这是什么吗？",
        "p1",
        provider_name="test",
        visibility="party",
    )

    assert "大家都看见了徽记" in context
    assert "只有莱拉知道" not in context
    assert "提问者的秘密背景" not in context
    assert "提问者私藏的银钥匙" not in context
    assert "只有你听见阁楼的脚步声" not in context
