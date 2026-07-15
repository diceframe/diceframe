"""AI 生成服务：连接测试 / 世界 / 角色 / 文本生成。"""

from __future__ import annotations

import logging
import json
import re
import time
from typing import TYPE_CHECKING, Any

from src.engine.language import DEFAULT_LANGUAGE, normalize_language
from src.generation import creator
from src.rules.rule_system import RuleSystem

if TYPE_CHECKING:
    from src.webui.api import WebAPI

logger = logging.getLogger("trpg")


async def test_connection(api: "WebAPI", base_url: str, api_key: str,
                          model: str, proxy_url: str = "") -> dict[str, Any]:
    """测试 LLM API 连接是否正常。"""
    import aiohttp
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "回复：OK"}],
        "max_tokens": 10,
    }

    start = time.time()
    try:
        if not api._llm_client:
            return {"ok": False, "error": "LLM 客户端未初始化", "elapsed": 0}
        session = await api._llm_client._get_session()
        active_proxy = proxy_url or api._llm_client.proxy_url
        request_kwargs = {"proxy": active_proxy} if active_proxy else {}
        async with session.post(
            url, json=body, headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
            **request_kwargs,
        ) as resp:
            return await _parse_connection_test_response(resp, start)
    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed": round(time.time() - start, 2)}


async def _parse_connection_test_response(resp, start: float) -> dict[str, Any]:
    elapsed = round(time.time() - start, 2)
    if resp.status == 200:
        data = await resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {
            "ok": True,
            "elapsed": elapsed,
            "response": content.strip(),
            "tokens": data.get("usage", {}).get("total_tokens", 0),
        }
    error_text = await resp.text()
    return {
        "ok": False,
        "error": f"HTTP {resp.status}: {error_text[:200]}",
        "elapsed": elapsed,
    }


async def generate_world(api: "WebAPI", prompt: str, rule_id: str = "",
                         language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    """使用 AI 根据用户描述生成完整世界模板。"""
    if not api._llm_client:
        return {"ok": False, "error": "LLM 客户端未初始化，请先配置 API Key"}
    try:
        return await _gen_world(api, prompt, rule_id, normalize_language(language))
    except Exception as e:
        logger.exception("AI 生成世界失败")
        return {"ok": False, "error": str(e)}


async def _gen_world(api: "WebAPI", prompt: str, rule_id: str, language: str) -> dict[str, Any]:
    return await creator.generate_world(
        api._llm_client, prompt, rule_id or "freeform_fantasy",
        worlds_dir=api._worlds_dir, lorebook_store=api._lore,
        max_tokens=api.character_gen_max_tokens,
        language=language,
    )


def _slug(value: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())[:32].strip("_")
    return raw or "custom"


async def generate_rule(api: "WebAPI", prompt: str, source_rule_id: str = "") -> dict[str, Any]:
    """按母版规则生成并保存一套 AI 自定义规则。"""
    if not api._llm_client:
        return {"ok": False, "error": "LLM 客户端未初始化，请先配置 API Key"}
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "请输入规则题材描述"}
    source_rule_id = (source_rule_id or "freeform_fantasy").strip()
    source_path = RuleSystem.path_for(api._rules_dir, source_rule_id)
    if not source_path.exists():
        return {"ok": False, "error": f"母版规则不存在: {source_rule_id}"}
    try:
        source_rule = json.loads(source_path.read_text(encoding="utf-8"))
        rule_id = f"ai_rule_{_slug(prompt)}_{int(time.time())}"
        data = await creator.generate_rule(
            api._llm_client,
            prompt,
            source_rule=source_rule,
            source_rule_id=source_rule_id,
            rule_id=rule_id,
            max_tokens=max(api.character_gen_max_tokens, 4096),
        )
        if not data:
            return {"ok": False, "error": "AI 返回规则 JSON 解析失败，请重试"}
        data["rule_id"] = rule_id
        data["custom"] = True
        data["source_rule_id"] = source_rule_id
        api._rules_dir.mkdir(parents=True, exist_ok=True)
        target = RuleSystem.path_for(api._rules_dir, rule_id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 读取一次确保 JSON 与规则系统兼容；不兼容则不落正式文件。
        RuleSystem.load(tmp)
        tmp.replace(target)
        return {
            "ok": True,
            "rule_id": rule_id,
            "rule_name": data.get("rule_name", rule_id),
            "description": data.get("description", ""),
            "source_rule_id": source_rule_id,
            "rule": data,
        }
    except Exception as e:
        logger.exception("AI 生成规则失败")
        return {"ok": False, "error": str(e)}


async def generate_character(api: "WebAPI", prompt: str, game_key: str = "", rule_id: str = "") -> dict[str, Any]:
    """使用 AI 根据用户描述生成角色卡。"""
    if not api._llm_client:
        return {"ok": False, "error": "LLM 客户端未初始化，请先配置 API Key"}
    try:
        rule = api._load_rule_by_id(rule_id)
        if game_key:
            inst = api._reg.get(api._parse_key(game_key))
            if inst:
                rule = rule or api._load_rule_for_game(inst)
        data = await creator.generate_character(
            api._llm_client, prompt, game_key, api._reg, rule=rule,
            max_tokens=api.character_gen_max_tokens,
        )
        if not data:
            return {"ok": False, "error": "AI 返回内容解析失败，请重试"}
        return {"ok": True, "character": data}
    except Exception as e:
        logger.exception("AI 生成角色失败")
        return {"ok": False, "error": str(e)}


async def generate_text(api: "WebAPI", prompt: str, system_hint: str = "") -> dict[str, Any]:
    """轻量文字生成：直接发 prompt 给 LLM，返回原始文本，不解析 JSON。"""
    if not api._llm_client:
        return {"ok": False, "error": "LLM 客户端未初始化"}
    system = system_hint or "你是一个TRPG角色设定助手。根据用户描述，生成简短实用的回答。不要输出JSON，直接输出文字。"
    try:
        response = await api._llm_client.call(
            system_prompt=system,
            user_message=prompt,
            temperature=0.7, max_tokens=api.text_gen_max_tokens,
        )
        text = response.narration or response.content
        return {"ok": True, "text": text.strip()}
    except Exception as e:
        logger.exception("文字生成失败")
        return {"ok": False, "error": str(e)}
