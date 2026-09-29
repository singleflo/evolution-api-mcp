"""EvolutionClient: request building, body parsing and the four failure types."""

from __future__ import annotations

import socket

import httpx2
import pytest

from evolution_api_mcp import __version__
from evolution_api_mcp.client import (
    EvolutionClient,
    EvolutionHTTPError,
    EvolutionUncertain,
    EvolutionUnreachable,
    extract_message,
)
from tests.conftest import BASE, INSTANCE, TOKEN
from tests.fakes import FakeEvolution


class CapturingTransport(httpx2.AsyncBaseTransport):
    """Records what reached the wire (raw target and timeouts), which FakeEvolution decodes away."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json={"ok": True}, request=request)


@pytest.mark.anyio
async def test_sends_the_instance_token_and_identifying_headers(evo: FakeEvolution) -> None:
    evo.on("GET", "/", json={"version": "2.3.7"})
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    assert await client.request("GET", "/") == {"version": "2.3.7"}

    headers = evo.last("GET", "/").headers
    assert headers["apikey"] == TOKEN
    assert headers["accept"] == "application/json"
    assert headers["user-agent"] == f"evolution-api-mcp/{__version__}"


@pytest.mark.anyio
async def test_trailing_slash_of_the_base_url_is_stripped_and_host_is_exposed(evo: FakeEvolution) -> None:
    evo.on("GET", "/instance/fetchInstances", json=[])
    client = EvolutionClient("http://user:secret@evo.test:8080///", TOKEN, transport=evo)

    assert client.base_url == "http://user:secret@evo.test:8080"
    assert client.host == "evo.test:8080"
    await client.request("GET", "/instance/fetchInstances")
    assert evo.requests[0].path == "/instance/fetchInstances"


@pytest.mark.anyio
async def test_json_body_and_query_params_reach_evolution(evo: FakeEvolution) -> None:
    evo.on("POST", f"/message/sendText/{INSTANCE}", status=201, json={"key": {"id": "ABC"}})
    evo.on("GET", f"/group/findGroupInfos/{INSTANCE}", json={"id": "1@g.us"})
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    posted = await client.request("POST", f"/message/sendText/{INSTANCE}", json={"number": "1", "text": "hi"})
    fetched = await client.request("GET", f"/group/findGroupInfos/{INSTANCE}", params={"groupJid": "1@g.us"})

    assert posted == {"key": {"id": "ABC"}}
    assert fetched == {"id": "1@g.us"}
    assert evo.last("POST", f"/message/sendText/{INSTANCE}").json == {"number": "1", "text": "hi"}
    assert evo.last("GET", f"/group/findGroupInfos/{INSTANCE}").params == {"groupJid": "1@g.us"}


@pytest.mark.anyio
async def test_delete_can_carry_a_json_body(evo: FakeEvolution) -> None:
    evo.on("DELETE", f"/chat/deleteMessageForEveryone/{INSTANCE}", json={"status": "SUCCESS"})
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    await client.request("DELETE", f"/chat/deleteMessageForEveryone/{INSTANCE}", json={"id": "ABC", "fromMe": True})

    assert evo.last("DELETE", f"/chat/deleteMessageForEveryone/{INSTANCE}").json == {"id": "ABC", "fromMe": True}


@pytest.mark.anyio
async def test_already_quoted_path_segments_are_sent_as_given() -> None:
    transport = CapturingTransport()
    client = EvolutionClient(BASE + "/", TOKEN, transport=transport)

    await client.request("GET", "/typebot/fetch/a%2Fb/my%20inst", params={"q": "x y"})

    assert transport.requests[0].url.raw_path == b"/typebot/fetch/a%2Fb/my%20inst?q=x+y"


@pytest.mark.anyio
async def test_default_timeout_is_90_seconds_with_a_10_second_connect_and_can_be_overridden() -> None:
    transport = CapturingTransport()
    client = EvolutionClient(BASE, TOKEN, transport=transport)

    await client.request("GET", "/")
    await client.request("POST", "/chat/getBase64FromMediaMessage/x", json={}, timeout=300.0)

    default, override = (r.extensions["timeout"] for r in transport.requests)
    assert (default["connect"], default["read"]) == (10.0, 90.0)
    assert (override["connect"], override["read"]) == (10.0, 300.0)


@pytest.mark.anyio
async def test_success_bodies_are_none_json_or_text(evo: FakeEvolution) -> None:
    evo.on("GET", "/empty")
    evo.on("GET", "/json", json={"a": [1, 2]})
    evo.on("GET", "/text", text="pong")
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    assert await client.request("GET", "/empty") is None
    assert await client.request("GET", "/json") == {"a": [1, 2]}
    assert await client.request("GET", "/text") == "pong"


@pytest.mark.anyio
async def test_unauthorized_answer_becomes_an_http_error_with_the_message(evo: FakeEvolution) -> None:
    body = {"status": 401, "error": "Unauthorized", "message": "Unauthorized"}
    evo.on("GET", "/instance/fetchInstances", status=401, json=body)
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(EvolutionHTTPError) as caught:
        await client.request("GET", "/instance/fetchInstances")

    assert (caught.value.status, caught.value.message, caught.value.body) == (401, "Unauthorized", body)
    assert TOKEN not in str(caught.value)


@pytest.mark.anyio
async def test_client_and_server_errors_carry_status_and_extracted_message(evo: FakeEvolution) -> None:
    evo.on(
        "POST",
        f"/message/sendText/{INSTANCE}",
        status=400,
        json={"status": 400, "error": "Bad Request", "response": {"message": ["number is required", "text is empty"]}},
    )
    evo.on(
        "GET",
        "/boom",
        status=500,
        json={"status": 500, "error": "Internal Server Error", "response": {"message": "boom"}},
    )
    evo.on("GET", "/html", status=502, text="<html>Bad Gateway</html>")
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(EvolutionHTTPError) as bad:
        await client.request("POST", f"/message/sendText/{INSTANCE}", json={})
    with pytest.raises(EvolutionHTTPError) as boom:
        await client.request("GET", "/boom")
    with pytest.raises(EvolutionHTTPError) as gateway:
        await client.request("GET", "/html")

    assert (bad.value.status, bad.value.message) == (400, "number is required; text is empty")
    assert (boom.value.status, boom.value.message) == (500, "boom")
    assert (gateway.value.status, gateway.value.message) == (502, "<html>Bad Gateway</html>")
    assert gateway.value.body == "<html>Bad Gateway</html>"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "exc",
    [
        httpx2.ConnectError("connection refused"),
        httpx2.ConnectTimeout("connect timed out"),
        httpx2.PoolTimeout("pool exhausted"),
        httpx2.UnsupportedProtocol("unsupported protocol"),
    ],
)
async def test_failures_before_any_byte_is_sent_are_unreachable(evo: FakeEvolution, exc: Exception) -> None:
    evo.fail("POST", f"/message/sendText/{INSTANCE}", exc)
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(EvolutionUnreachable) as caught:
        await client.request("POST", f"/message/sendText/{INSTANCE}", json={})

    assert caught.value.host == "evo.test"
    assert caught.value.reason == str(exc)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "exc",
    [
        httpx2.ReadTimeout("read timed out"),
        httpx2.WriteTimeout("write timed out"),
        httpx2.RemoteProtocolError("server disconnected"),
        httpx2.ReadError("connection reset"),
    ],
)
async def test_failures_after_the_request_was_sent_are_uncertain(evo: FakeEvolution, exc: Exception) -> None:
    evo.fail("POST", f"/message/sendText/{INSTANCE}", exc)
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(EvolutionUncertain) as caught:
        await client.request("POST", f"/message/sendText/{INSTANCE}", json={})

    assert caught.value.host == "evo.test"
    assert caught.value.reason == str(exc)


@pytest.mark.anyio
async def test_an_exception_without_text_still_gives_a_reason(evo: FakeEvolution) -> None:
    evo.fail("GET", "/", httpx2.ReadTimeout(""))
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(EvolutionUncertain) as caught:
        await client.request("GET", "/")

    assert caught.value.reason == "ReadTimeout"


@pytest.mark.anyio
@pytest.mark.parametrize("base_url", ["evo.test", "ftp://evo.test", "http://[::1"])
async def test_unusable_base_urls_are_unreachable_without_touching_the_network(base_url: str) -> None:
    client = EvolutionClient(base_url, TOKEN)

    with pytest.raises(EvolutionUnreachable):
        await client.request("GET", "/")
    await client.aclose()


@pytest.mark.anyio
async def test_a_refused_local_connection_is_unreachable() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = EvolutionClient(f"http://127.0.0.1:{port}", TOKEN)

    with pytest.raises(EvolutionUnreachable) as caught:
        await client.request("GET", "/")

    assert caught.value.host == f"127.0.0.1:{port}"
    await client.aclose()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {"status": 400, "error": "Bad Request", "response": {"message": "Instance not connected"}},
            "Instance not connected",
        ),
        ({"response": {"message": ["a", "b"]}, "message": "outer"}, "a; b"),
        ({"status": 404, "error": "Not Found", "response": {"message": ["Cannot GET /x"]}}, "Cannot GET /x"),
        ({"message": "Unauthorized", "error": "Unauthorized"}, "Unauthorized"),
        ({"error": {"message": "nested failure"}}, "nested failure"),
        ({"error": "Bad Request"}, "Bad Request"),
        (
            {"response": {"message": {"jid": "1@s.whatsapp.net", "exists": False}}},
            '{"jid": "1@s.whatsapp.net", "exists": false}',
        ),
        ({"response": {"message": [{"exists": False, "number": "1"}]}}, '{"exists": false, "number": "1"}'),
        ({"response": {"message": ""}, "message": "fallback"}, "fallback"),
        ({"unexpected": 1}, "{'unexpected': 1}"),
        ("plain text failure", "plain text failure"),
        ([1, 2], "[1, 2]"),
        (None, "no error details"),
        ("", "no error details"),
    ],
)
def test_extract_message_handles_every_documented_body_shape(body: object, expected: str) -> None:
    assert extract_message(body) == expected


def test_extract_message_truncates_to_500_characters() -> None:
    assert extract_message({"response": {"message": "x" * 900}}) == "x" * 500
    assert len(extract_message(["y" * 400, "z" * 400])) == 500
