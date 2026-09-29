import json

import httpx2
import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BUSINESS
from evolution_api_mcp.tools.templates import (
    create_template,
    delete_template,
    edit_template,
    list_templates,
    send_template_message,
)
from tests.conftest import INSTANCE

FIND = f"/template/find/{INSTANCE}"
CREATE = f"/template/create/{INSTANCE}"
EDIT = f"/template/edit/{INSTANCE}"
DELETE = f"/template/delete/{INSTANCE}"
SEND = f"/message/sendTemplate/{INSTANCE}"
PERSON = "393331234567@s.whatsapp.net"

BODY = {"type": "BODY", "text": "Hello {{1}}, your order {{2}} shipped."}
ORDER_IT = {
    "id": "111",
    "name": "order_shipped",
    "language": "it",
    "status": "APPROVED",
    "category": "UTILITY",
    "components": [BODY],
    "parameter_format": "POSITIONAL",
}
ORDER_EN = {**ORDER_IT, "id": "222", "language": "en_US", "status": "PENDING"}
PROMO = {
    "id": "333",
    "name": "spring_promo",
    "language": "en_US",
    "status": "REJECTED",
    "category": "MARKETING",
    "components": [],
}


def _meta_400(message, code="100"):
    return {
        "status": 400,
        "error": "Bad Request",
        "message": message,
        "details": {"whatsapp_error": message, "whatsapp_code": code, "type": "whatsapp_api_error"},
    }


# --- list_templates --------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_templates_projects_meta_rows(evo, bound, make_identity):
    evo.on("GET", FIND, json=[ORDER_IT, PROMO])
    with bound(evo, identity=make_identity(BUSINESS)):
        result = json.loads(await list_templates())
    assert result == {
        "templates": [
            {
                "template_id": "111",
                "name": "order_shipped",
                "language": "it",
                "status": "APPROVED",
                "category": "UTILITY",
                "components": [BODY],
            },
            {
                "template_id": "333",
                "name": "spring_promo",
                "language": "en_US",
                "status": "REJECTED",
                "category": "MARKETING",
                "components": [],
            },
        ]
    }


@pytest.mark.anyio
async def test_list_templates_filters_by_name_and_status(evo, bound, make_identity):
    evo.on("GET", FIND, json=[ORDER_IT, ORDER_EN, PROMO])
    with bound(evo, identity=make_identity(BUSINESS)):
        by_name = json.loads(await list_templates(name_contains="ORDER"))
        approved = json.loads(await list_templates(status="APPROVED"))
        both = json.loads(await list_templates(name_contains="order", status="PENDING"))
        none = json.loads(await list_templates(name_contains="nothing"))
    assert [t["template_id"] for t in by_name["templates"]] == ["111", "222"]
    assert [t["template_id"] for t in approved["templates"]] == ["111"]
    assert [t["template_id"] for t in both["templates"]] == ["222"]
    assert none == {"templates": []}


@pytest.mark.anyio
async def test_list_templates_accepts_a_meta_data_wrapper_and_an_empty_answer(evo, bound, make_identity):
    with bound(evo, identity=make_identity(BUSINESS)):
        evo.on("GET", FIND, json={"data": [ORDER_IT], "paging": {}})
        wrapped = json.loads(await list_templates())
        evo.on("GET", FIND)
        empty = json.loads(await list_templates())
    assert [t["name"] for t in wrapped["templates"]] == ["order_shipped"]
    assert empty == {"templates": []}


@pytest.mark.anyio
async def test_list_templates_meta_rejection_names_reason_and_code(evo, bound, make_identity):
    evo.on("GET", FIND, status=400, json=_meta_400("Unsupported get request.", code=100))
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await list_templates()
    assert str(caught.value) == (
        "WhatsApp Business Platform refused the request: Unsupported get request (code 100). Nothing was changed."
    )


# --- send_template_message -------------------------------------------------------------------------------------

SENT = {
    "key": {"id": "wamid.HBgM", "remoteJid": PERSON, "fromMe": True},
    "status": "PENDING",
    "messageType": "templateMessage",
    "messageTimestamp": 1759180000,
}


@pytest.mark.anyio
async def test_send_template_with_body_parameters_builds_body_components(evo, bound, make_identity):
    evo.on("POST", SEND, status=201, json=SENT)
    with bound(evo, identity=make_identity(BUSINESS)):
        result = json.loads(
            await send_template_message(
                chat="+39 333 123 4567", template_name="order_shipped", language="it", body_parameters=["Ana", "A-1001"]
            )
        )
    assert evo.last("POST", SEND).json == {
        "number": "393331234567",
        "name": "order_shipped",
        "language": "it",
        "components": [
            {"type": "body", "parameters": [{"type": "text", "text": "Ana"}, {"type": "text", "text": "A-1001"}]}
        ],
    }
    assert result == {
        "message_id": "wamid.HBgM",
        "chat_id": PERSON,
        "status": "PENDING",
        "timestamp": "2025-09-29T21:06:40Z",
    }


@pytest.mark.anyio
async def test_send_template_passes_raw_components_through(evo, bound, make_identity):
    components = [{"type": "header", "parameters": [{"type": "image", "image": {"link": "https://example.com/a.png"}}]}]
    evo.on("POST", SEND, status=201, json=SENT)
    with bound(evo, identity=make_identity(BUSINESS)):
        await send_template_message(chat=PERSON, template_name="promo", language="en_US", components=components)
    assert evo.last("POST", SEND).json["components"] == components


@pytest.mark.anyio
async def test_send_template_without_parameters_sends_empty_components(evo, bound, make_identity):
    evo.on("POST", SEND, status=201, json=SENT)
    with bound(evo, identity=make_identity(BUSINESS)):
        await send_template_message(chat=PERSON, template_name="hello_world", language="en_US")
    assert evo.last("POST", SEND).json["components"] == []


@pytest.mark.anyio
async def test_send_template_refuses_body_parameters_together_with_components(evo, bound, make_identity):
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await send_template_message(
            chat=PERSON, template_name="t", language="en", body_parameters=["a"], components=[{"type": "body"}]
        )
    assert str(caught.value) == "Give body_parameters or components, not both. Nothing was sent."
    assert evo.requests == []


@pytest.mark.anyio
async def test_send_template_refuses_group_chats(evo, bound, make_identity):
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError, match="phone numbers only"):
        await send_template_message(chat="120363012345678901@g.us", template_name="t", language="en")
    assert evo.requests == []


@pytest.mark.anyio
async def test_send_template_meta_rejection_in_the_201_body_is_a_refusal(evo, bound, make_identity):
    evo.on(
        "POST",
        SEND,
        status=201,
        json={"message": "Message undeliverable", "type": "OAuthException", "code": 131026, "fbtrace_id": "A1"},
    )
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await send_template_message(chat=PERSON, template_name="t", language="en")
    assert str(caught.value) == (
        "WhatsApp Business Platform refused the message (code 131026): Message undeliverable. Nothing was sent."
    )


# --- create_template -------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_template_posts_meta_fields_and_returns_id_and_status(evo, bound, make_identity):
    evo.on(
        "POST",
        CREATE,
        status=201,
        json={
            "id": "cl-row",
            "templateId": "444",
            "name": "order_shipped",
            "template": {"id": "444", "status": "PENDING", "category": "UTILITY"},
            "instanceId": "cl-inst",
        },
    )
    with bound(evo, identity=make_identity(BUSINESS)):
        result = json.loads(
            await create_template(name="order_shipped", category="UTILITY", language="it", components=[BODY])
        )
    assert evo.last("POST", CREATE).json == {
        "name": "order_shipped",
        "category": "UTILITY",
        "language": "it",
        "components": [BODY],
        "allowCategoryChange": False,
    }
    assert result == {"template_id": "444", "name": "order_shipped", "status": "PENDING"}


@pytest.mark.anyio
async def test_create_template_meta_rejection_names_reason_and_code(evo, bound, make_identity):
    evo.on("POST", CREATE, status=400, json=_meta_400("Invalid parameter", code=100))
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await create_template(
            name="bad", category="MARKETING", language="en", components=[BODY], allow_category_change=True
        )
    assert str(caught.value) == (
        "WhatsApp Business Platform refused the request: Invalid parameter (code 100). Nothing was changed."
    )
    assert evo.last("POST", CREATE).json["allowCategoryChange"] is True


@pytest.mark.anyio
async def test_create_template_meta_rejection_without_a_code_omits_it(evo, bound, make_identity):
    evo.on("POST", CREATE, status=400, json=_meta_400("Template name already exists.", code="UNKNOWN_ERROR"))
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await create_template(name="dup", category="UTILITY", language="en", components=[BODY])
    assert str(caught.value) == (
        "WhatsApp Business Platform refused the request: Template name already exists. Nothing was changed."
    )


@pytest.mark.anyio
async def test_create_template_lost_connection_wrapped_in_400_is_uncertain_with_the_listing(evo, bound, make_identity):
    evo.on("POST", CREATE, status=400, json=_meta_400("Connection error: read ECONNRESET", code="UNKNOWN_ERROR"))
    evo.on("GET", FIND, json=[ORDER_IT, PROMO])
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await create_template(name="order_shipped", category="UTILITY", language="it", components=[BODY])
    message = str(caught.value)
    assert message.startswith("UNCERTAIN: Evolution did not confirm the result")
    assert '"template_id": "111"' in message
    assert "spring_promo" not in message


# --- edit_template ---------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_template_sends_only_the_given_fields(evo, bound, make_identity):
    evo.on("POST", EDIT, json={"success": True})
    with bound(evo, identity=make_identity(BUSINESS)):
        result = json.loads(await edit_template(template_id="111", components=[BODY], allow_category_change=False))
    assert evo.last("POST", EDIT).json == {"templateId": "111", "components": [BODY], "allowCategoryChange": False}
    assert result == {"template_id": "111", "updated": True}


@pytest.mark.anyio
async def test_edit_template_category_only(evo, bound, make_identity):
    evo.on("POST", EDIT, json={"success": True})
    with bound(evo, identity=make_identity(BUSINESS)):
        await edit_template(template_id="111", category="MARKETING")
    assert evo.last("POST", EDIT).json == {"templateId": "111", "category": "MARKETING"}


@pytest.mark.anyio
async def test_edit_template_needs_a_change(evo, bound, make_identity):
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await edit_template(template_id="111")
    assert str(caught.value) == "Give components, category or allow_category_change to change. Nothing was changed."
    assert evo.requests == []


@pytest.mark.anyio
async def test_edit_template_meta_rejection_is_a_refusal(evo, bound, make_identity):
    evo.on("POST", EDIT, status=400, json=_meta_400("Cannot edit an approved template again today", code=200))
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await edit_template(template_id="111", category="UTILITY")
    assert str(caught.value) == (
        "WhatsApp Business Platform refused the request: Cannot edit an approved template again today (code 200). "
        "Nothing was changed."
    )


# --- delete_template -------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_template_by_name_sends_only_the_name(evo, bound, make_identity):
    evo.on("DELETE", DELETE, json={"success": True})
    with bound(evo, identity=make_identity(BUSINESS)):
        result = json.loads(await delete_template(name="order_shipped"))
    assert evo.last("DELETE", DELETE).json == {"name": "order_shipped"}
    assert result == {"deleted": True, "name": "order_shipped"}


@pytest.mark.anyio
async def test_delete_template_version_sends_hsm_id(evo, bound, make_identity):
    evo.on("DELETE", DELETE, json={"success": True})
    with bound(evo, identity=make_identity(BUSINESS)):
        await delete_template(name="order_shipped", template_id="222")
    assert evo.last("DELETE", DELETE).json == {"name": "order_shipped", "hsmId": "222"}


@pytest.mark.anyio
async def test_delete_template_meta_rejection_is_a_refusal(evo, bound, make_identity):
    evo.on("DELETE", DELETE, status=400, json=_meta_400("Template not found", code=100))
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await delete_template(name="ghost")
    assert str(caught.value) == (
        "WhatsApp Business Platform refused the request: Template not found (code 100). Nothing was changed."
    )


@pytest.mark.anyio
async def test_delete_template_without_an_answer_is_uncertain_with_the_remaining_versions(evo, bound, make_identity):
    evo.fail("DELETE", DELETE, httpx2.ReadTimeout("timed out"))
    evo.on("GET", FIND, json=[ORDER_IT, ORDER_EN, PROMO])
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as caught:
        await delete_template(name="order_shipped")
    message = str(caught.value)
    assert message.startswith("UNCERTAIN:")
    assert '"template_id": "111"' in message
    assert '"template_id": "222"' in message
    assert "spring_promo" not in message
