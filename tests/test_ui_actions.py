import pytest

from src.application_actions import ActionRegistry, ApplicationAction
from src.webui.services.ui_actions import UiActionDependencies, dispatch_ui_action


def _action(*, automatic: bool):
    return ApplicationAction(
        name="creator_write",
        label="Creator write",
        schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
        handler=lambda args, owner: {"owner": owner, "value": args["value"]},
        mutation=True,
        automatic=automatic,
        guard=lambda args, owner: "stable",
        domain="creator",
        effect="write",
    )


@pytest.mark.asyncio
async def test_ui_action_dispatch_uses_the_same_guarded_authority():
    result = await dispatch_ui_action(
        UiActionDependencies(ActionRegistry([_action(automatic=True)])),
        "creator_write",
        "alice",
        {"value": "draft"},
    )
    assert result["result"] == {"owner": "alice", "value": "draft"}


@pytest.mark.asyncio
async def test_ui_action_dispatch_rejects_non_automatic_actions():
    with pytest.raises(PermissionError):
        await dispatch_ui_action(
            UiActionDependencies(ActionRegistry([_action(automatic=False)])),
            "creator_write",
            "alice",
            {"value": "draft"},
        )
