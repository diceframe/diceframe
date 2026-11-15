"""What the rest panel reads before it can offer a preparation change.

The panel must not guess: capacity, the current lists and the candidate pool all
come from the same content the validator uses.  A Wizard's candidates are
narrowed to the spellbook (a list-only pick is illegal for him), and a non-caster
gets ``policy: null`` with empty candidates rather than an error -- "nothing to
prepare" is a state the panel renders, not a failure it reports.
"""

from __future__ import annotations

from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from src.rulesets.dnd2024.runtime import Dnd2024Runtime
from src.webui.routes.games import register_games
from src.webui.services import ruleset_rest

LOCALE = "en"


@pytest.fixture(scope="module")
def runtime() -> Dnd2024Runtime:
    return Dnd2024Runtime()


def _character(runtime: Dnd2024Runtime, class_id: str) -> dict:
    """A level-1 character of ``class_id``, reskinned from the arcane preset.

    Same construction the builder performs in one click: the class's own
    skills, equipment package, tool proficiencies and recommended spells.
    """

    bundle = runtime.load_bundle(LOCALE)
    choices = runtime.builder_choices(None, {"locale": LOCALE})
    base = next(
        item for item in choices["quick_presets"] if item["id"] == "curious_arcanist"
    )["draft"]
    background = bundle.get(
        "background", str(base["background_ref"]).removeprefix("background:")
    )
    background_skills = set(background.get("skill_refs") or [])
    background_tools = set(background.get("tool_proficiency_refs") or [])
    entity = bundle.get("class", class_id) or {}
    skill_choice = entity.get("skill_choice") or {}
    draft = {
        **base,
        "class_ref": f"class:{class_id}",
        "class_skill_refs": [
            ref for ref in skill_choice.get("allowed_refs") or []
            if ref not in background_skills
        ][: int(skill_choice.get("count") or 0)],
        "equipment_package_ref": (entity.get("equipment_package_refs") or [""])[0],
        "name": f"Options {class_id}",
    }
    recommended = entity.get("recommended_spell_choices") or {}
    if recommended:
        draft["class_spell_choices"] = {
            "cantrip_ids": recommended.get("cantrip_ids") or [],
            "prepared_spell_ids": recommended.get("prepared_spell_ids") or [],
            **(
                {"spellbook_ids": recommended["spellbook_ids"]}
                if recommended.get("spellbook_ids") else {}
            ),
        }
    tool_choice = entity.get("tool_choice") or {}
    if tool_choice:
        categories = set(tool_choice.get("allowed_categories") or [])
        draft["class_tool_refs"] = [
            f"tool:{tool['id']}" for tool in bundle.list("tool")
            if tool.get("category") in categories
            and f"tool:{tool['id']}" not in background_tools
        ][: int(tool_choice.get("count") or 0)]
    else:
        draft.pop("class_tool_refs", None)
    return runtime.finalize_character(None, draft)["ruleset_character"]


def _options(runtime: Dnd2024Runtime, character: dict) -> dict:
    """The read as the service hands it to the panel (live payload shape)."""

    return runtime.prepared_spell_options(None, {"ruleset_character": character})


def test_a_wizard_is_offered_only_what_is_written_in_the_book(runtime) -> None:
    character = _character(runtime, "wizard")
    spells = _options(runtime, character)
    book = set(spells["spellbook_refs"])
    prepared = set(spells["prepared_spell_refs"])

    assert spells["policy"]["source"] == "spellbook"
    assert spells["prepared_capacity"] == 4
    assert len(prepared) == spells["prepared_capacity"]
    assert prepared <= book
    # 候选池被收窄到法术书内：否则面板会给出一个校验必拒的选择。
    assert {spell["ref"] for spell in spells["leveled_spells"]} <= book
    assert len(spells["leveled_spells"]) > spells["prepared_capacity"]


def test_a_wizard_keeps_his_cantrips_apart_from_the_prepared_list(runtime) -> None:
    character = _character(runtime, "wizard")
    spells = _options(runtime, character)

    assert spells["cantrip_capacity"] == 3
    assert len(spells["cantrip_refs"]) == spells["cantrip_capacity"]
    assert spells["policy"]["cantrip_swap_on"] == "long_rest"
    # 候选是整张职业戏法表，现有选择在其中（长休换 1 个时要能挑新的）。
    candidates = {spell["ref"] for spell in spells["cantrips"]}
    assert set(spells["cantrip_refs"]) <= candidates
    assert len(candidates) > spells["cantrip_capacity"]
    assert all(spell["level"] == 0 for spell in spells["cantrips"])


def test_a_cleric_may_prepare_from_the_whole_class_list(runtime) -> None:
    character = _character(runtime, "cleric")
    spells = _options(runtime, character)

    assert spells["policy"]["source"] == "class_list"
    assert spells["policy"]["long_rest_replace"] == "any"
    assert spells["prepared_capacity"] == 4
    assert spells["spellbook_refs"] == []
    assert len(spells["leveled_spells"]) > spells["prepared_capacity"]
    assert set(spells["prepared_spell_refs"]) <= {
        spell["ref"] for spell in spells["leveled_spells"]
    }


def test_a_non_caster_reads_as_empty_instead_of_failing(runtime) -> None:
    character = _character(runtime, "fighter")
    spells = _options(runtime, character)

    assert spells["policy"] is None
    assert spells["prepared_capacity"] == 0
    assert spells["cantrip_capacity"] == 0
    assert spells["prepared_spell_refs"] == []
    assert spells["cantrips"] == []
    assert spells["leveled_spells"] == []


# --- HTTP 层：路由把「没有可准备的」当成 200，把别人的角色卡挡成 403 ---------


class _Registry:
    def __init__(self, runtime: Dnd2024Runtime) -> None:
        self._runtime = runtime

    def resolve(self, _template) -> Dnd2024Runtime:
        return self._runtime


class _Instance:
    def __init__(self, gm_uid: str, sheets: dict[str, dict]) -> None:
        self.gm_uid = gm_uid
        self.players = dict.fromkeys(sheets, "player")
        self._sheets = sheets

    def get_character_sheet(self, user_id: str) -> dict:
        return self._sheets[user_id]


def _dependencies(runtime: Dnd2024Runtime, instances: dict[str, _Instance]):
    return ruleset_rest.LiveRulesetRestDependencies(
        get_instance=lambda key: instances.get(key),
        parse_game_key=lambda key: key,
        save_instance=lambda *_args, **_kwargs: None,
        load_rule_for_game=lambda _instance: SimpleNamespace(
            template={"id": "core:dnd2024"},
        ),
        ruleset_registry=_Registry(runtime),
    )


class _Api:
    """Only the two calls this route makes; the rest of WebAPI is untouched."""

    def __init__(self, dependencies) -> None:
        self._dependencies = dependencies

    def get_game_instance(self, game_key: str):
        return self._dependencies.get_instance(game_key)

    def live_character_spell_options(self, game_key: str, user_id: str) -> dict:
        return ruleset_rest.prepared_spell_options(
            self._dependencies, game_key, user_id,
        )


async def _client(runtime: Dnd2024Runtime, instances: dict[str, _Instance]):
    app = web.Application()

    @web.middleware
    async def session(request: web.Request, handler):
        request["user_id"] = request.headers.get("X-Test-User", "")
        return await handler(request)

    app.middlewares.append(session)
    app["api"] = _Api(_dependencies(runtime, instances))
    register_games(app)
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_the_route_answers_200_for_a_character_with_nothing_to_prepare(
    runtime: Dnd2024Runtime,
) -> None:
    instances = {"game-1": _Instance("gm", {"seat-1": _character(runtime, "fighter")})}
    client = await _client(runtime, instances)
    await client.start_server()
    try:
        response = await client.get(
            "/api/games/game-1/character/seat-1/spells",
            headers={"X-Test-User": "seat-1"},
        )
        assert response.status == 200
        body = await response.json()
        assert body["ok"] is True
        assert body["spells"]["policy"] is None
        assert body["spells"]["leveled_spells"] == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_route_refuses_another_seats_sheet(runtime: Dnd2024Runtime) -> None:
    instances = {"game-1": _Instance("gm", {"seat-1": _character(runtime, "wizard")})}
    client = await _client(runtime, instances)
    await client.start_server()
    try:
        response = await client.get(
            "/api/games/game-1/character/seat-1/spells",
            headers={"X-Test-User": "seat-2"},
        )
        assert response.status == 403
        body = await response.json()
        assert body["ok"] is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_route_reports_a_missing_game_as_404(runtime: Dnd2024Runtime) -> None:
    client = await _client(runtime, {})
    await client.start_server()
    try:
        response = await client.get(
            "/api/games/nope/character/seat-1/spells",
            headers={"X-Test-User": "seat-1"},
        )
        assert response.status == 404
        body = await response.json()
        assert body["ok"] is False
    finally:
        await client.close()
