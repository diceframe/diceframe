from __future__ import annotations

from pathlib import Path

import pytest

from src.bots.bridge_core.models import BridgeInput
from src.bots.bridge_core.presenters import bound_help_text, format_action_result
from src.bots.bridge_core.service import DiceFrameBridgeService
from src.bots.bridge_core.store import JsonBridgeStore


class EnglishBridgeClient:
    def __init__(self) -> None:
        self.questions: list[tuple[str, str, str]] = []
        self.resolved_proposals: list[tuple[str, str, str, bool]] = []

    async def bind_game(self, game_key: str, bind_token: str) -> dict:
        assert bind_token == "bind-ok"
        return {
            "ok": True,
            "game_key": game_key,
            "gm_uid": "gm-1",
            "world_name": "The Long Night",
            "language": "en",
            "players": [
                {"user_id": "gm-1", "character_name": "Game Master"},
                {"user_id": "player-1", "character_name": "Erin"},
            ],
        }

    async def characters(self, game_key: str, actor: str) -> dict:
        return {
            "players": [{
                "user_id": "player-1",
                "character_name": "Erin",
                "character_sheet": {
                    "hp": 8,
                    "max_hp": 10,
                    "gold": 4,
                    "attributes": {"dex": 14},
                    "skills": [{"name": "Stealth", "value": 45}],
                },
            }],
        }

    async def build_join_link(self, game_key: str, user: str = "") -> str:
        return f"https://table.example/#/join?game={game_key}&user={user}"

    async def ask_kp(self, game_key: str, actor: str, question: str) -> dict:
        self.questions.append((game_key, actor, question))
        return {
            "ok": True,
            "answer": "You recognize the university seal, but nothing more.",
            "advanced": False,
            "action_consumed": False,
        }

    async def detail(self, game_key: str, actor: str) -> dict:
        return {
            "gm_uid": "gm-1",
            "economy_proposals": [{
                "id": "eco-party",
                "sequence": 7,
                "kind": "purchase",
                "amount": 3,
                "reason": "Bridge toll",
                "status": "pending",
                "payer_uid": "player-1",
                "approval_policy": "payer",
            }],
        }

    async def resolve_payment(
        self, game_key: str, actor: str, proposal_id: str, accepted: bool,
    ) -> dict:
        self.resolved_proposals.append((game_key, actor, proposal_id, accepted))
        return {"ok": True, "accepted": True, "committed": False}


@pytest.mark.asyncio
async def test_english_binding_persists_language_and_drives_shared_replies(tmp_path: Path):
    store = JsonBridgeStore(tmp_path / "bridge.json")
    client = EnglishBridgeClient()
    service = DiceFrameBridgeService(client, store)  # type: ignore[arg-type]

    bound = await service.handle(BridgeInput("discord-channel", "gm-platform", "/df bind game-1 bind-ok"))

    assert store.group("discord-channel")["language"] == "en"
    assert "Bound to DiceFrame game" in bound.replies[0]
    assert "/df join Character Name" in bound.replies[0]

    help_result = await service.handle(BridgeInput("discord-channel", "player-platform", "/df help"))
    assert "DiceFrame chat quick start" in help_result.replies[0]
    assert "认领角色" not in help_result.replies[0]

    joined = await service.handle(BridgeInput("discord-channel", "player-platform", "/df join Erin"))
    assert joined.replies == ["Character claimed: Erin"]

    question = await service.handle(BridgeInput("discord-channel", "player-platform", "/df ask kp what is this seal?"))
    assert question.replies == ["GM: You recognize the university seal, but nothing more."]
    assert client.questions == [("game-1", "player-1", "what is this seal?")]

    roll_notice = await service.handle(BridgeInput("discord-channel", "player-platform", "/df roll"))
    assert "Manual roll confirmation is no longer required" in roll_notice.replies[0]

    status = await service.handle(BridgeInput("discord-channel", "player-platform", "/df status"))
    assert "Erin status" in status.replies[0]
    assert "Gold: 4" in status.replies[0]

    proposals = await service.handle(BridgeInput("discord-channel", "player-platform", "/df pay"))
    assert "#7" in proposals.replies[0]
    confirmed = await service.handle(BridgeInput(
        "discord-channel", "player-platform", "/df confirm pay 7",
    ))
    assert "Payment of 3 gold confirmed." in confirmed.replies[0]
    assert client.resolved_proposals == [("game-1", "player-1", "eco-party", True)]

    reloaded = JsonBridgeStore(tmp_path / "bridge.json")
    await reloaded.load()
    assert reloaded.group("discord-channel")["language"] == "en"


def test_english_presenters_keep_platform_neutral_command_prefix():
    help_text = bound_help_text(
        {"roster": [{"character_name": "Erin"}]},
        command_prefix="/df",
        language="en",
    )

    assert "/df join Character Name" in help_text
    assert "/df advance" in help_text
    assert "/df ask kp <question>" in help_text
    assert format_action_result({}, "en") == "Action recorded."
