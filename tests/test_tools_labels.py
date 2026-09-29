import json

import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools.labels import add_chat_label, list_labels, remove_chat_label
from tests.conftest import INSTANCE

FIND_LABELS = f"/label/findLabels/{INSTANCE}"
HANDLE_LABEL = f"/label/handleLabel/{INSTANCE}"

ANA = "393331234567@s.whatsapp.net"
GROUP = "120363012345678901@g.us"


@pytest.mark.anyio
async def test_list_labels_projects_id_name_and_color(evo, bound):
    evo.on(
        "GET",
        FIND_LABELS,
        json=[
            {"id": "1", "name": "New customer", "color": 0, "predefinedId": "3"},
            {"id": "2", "name": "Paid", "color": 4, "predefinedId": None},
        ],
    )
    with bound(evo):
        result = json.loads(await list_labels())

    assert result == {
        "labels": [
            {"label_id": "1", "name": "New customer", "color": 0},
            {"label_id": "2", "name": "Paid", "color": 4},
        ]
    }


@pytest.mark.anyio
async def test_list_labels_empty(evo, bound):
    evo.on("GET", FIND_LABELS, json=[])
    with bound(evo):
        assert json.loads(await list_labels()) == {"labels": []}


@pytest.mark.anyio
async def test_add_chat_label_posts_the_digits_and_reports_applied(evo, bound):
    evo.on("POST", HANDLE_LABEL, json={"numberJid": ANA, "labelId": "2", "add": True})
    with bound(evo):
        result = json.loads(await add_chat_label("+39 333 123 4567", "2"))

    assert evo.last("POST", HANDLE_LABEL).json == {"number": "393331234567", "labelId": "2", "action": "add"}
    assert result == {"chat_id": ANA, "label_id": "2", "applied": True}


@pytest.mark.anyio
async def test_remove_chat_label_posts_remove_and_reads_the_remove_flag(evo, bound):
    evo.on("POST", HANDLE_LABEL, json={"numberJid": ANA, "labelId": "2", "remove": True})
    with bound(evo):
        result = json.loads(await remove_chat_label(ANA, "2"))

    assert evo.last("POST", HANDLE_LABEL).json == {"number": "393331234567", "labelId": "2", "action": "remove"}
    assert result == {"chat_id": ANA, "label_id": "2", "applied": True}


@pytest.mark.anyio
async def test_label_change_without_confirmation_is_not_reported_as_applied(evo, bound):
    evo.on("POST", HANDLE_LABEL, json={"numberJid": ANA, "labelId": "2"})
    with bound(evo):
        result = json.loads(await add_chat_label(ANA, "2"))

    assert result["applied"] is False


@pytest.mark.anyio
async def test_label_tools_refuse_groups_without_calling_evolution(evo, bound):
    with bound(evo):
        with pytest.raises(ToolExecutionError, match="is not a phone number"):
            await add_chat_label(GROUP, "2")
        with pytest.raises(ToolExecutionError, match="is not a phone number"):
            await remove_chat_label(GROUP, "2")

    assert evo.requests == []


@pytest.mark.anyio
async def test_label_on_a_number_without_whatsapp_is_a_refusal(evo, bound):
    evo.on(
        "POST",
        HANDLE_LABEL,
        status=404,
        json={"status": 404, "error": "Not Found", "response": {"message": ["Number is not on WhatsApp"]}},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await add_chat_label(ANA, "2")

    assert str(caught.value) == "Evolution refused the request (404): Number is not on WhatsApp. Nothing was changed."
