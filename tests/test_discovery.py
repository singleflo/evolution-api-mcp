"""Discovery: which instance a token belongs to, and the credentials and servers it refuses."""

from __future__ import annotations

import httpx2
import pytest

from evolution_api_mcp import discovery, registry
from evolution_api_mcp.client import EvolutionClient, EvolutionHTTPError, EvolutionUnreachable
from evolution_api_mcp.context import InstanceIdentity
from evolution_api_mcp.errors import ToolExecutionError
from tests.conftest import BASE, TOKEN
from tests.fakes import FakeEvolution

ROOT = {
    "status": 200,
    "message": "Welcome to the Evolution API, it is working!",
    "version": "2.3.7",
    "clientName": "evolution",
}
GLOBAL_KEY_REFUSAL = (
    "This is the Evolution server's global AUTHENTICATION_API_KEY, which controls every instance on the server. "
    "Use the instance's own token instead."
)
NOT_INSTANCE_TOKEN = (
    "Evolution did not accept this token as an instance token. Check the token; Evolution resolves instance tokens "
    "only when DATABASE_SAVE_DATA_INSTANCE=true (its default)."
)


def instance_row(name: str = "shop", integration: str | None = "WHATSAPP-BUSINESS", token: str = TOKEN) -> dict:
    row: dict = {"name": name, "connectionStatus": "open", "token": token}
    if integration is not None:
        row["integration"] = integration
    return row


def instance_server(evo: FakeEvolution, rows: object, *, version: str = "2.3.7") -> EvolutionClient:
    """Evolution answering as documented for an instance token: root welcome, verify-creds 401, fetchInstances rows."""
    evo.on("GET", "/", json={**ROOT, "version": version})
    evo.on(
        "POST",
        "/verify-creds",
        status=401,
        json={"status": 401, "error": "Unauthorized", "message": "Unauthorized"},
    )
    evo.on("GET", "/instance/fetchInstances", json=rows)
    return EvolutionClient(BASE, TOKEN, transport=evo)


@pytest.mark.anyio
async def test_instance_token_yields_the_instance_identity(evo: FakeEvolution) -> None:
    client = instance_server(evo, [instance_row("shop", "WHATSAPP-BUSINESS")])

    identity = await discovery.discover(client)

    assert identity == InstanceIdentity(name="shop", integration="WHATSAPP-BUSINESS")
    for recorded in evo.requests:
        assert recorded.headers["apikey"] == TOKEN
    assert evo.last("POST", "/verify-creds").json == {}


@pytest.mark.anyio
async def test_missing_integration_defaults_to_baileys(evo: FakeEvolution) -> None:
    client = instance_server(evo, [instance_row("shop", None)])

    identity = await discovery.discover(client)

    assert identity.integration == registry.BAILEYS


@pytest.mark.anyio
async def test_global_key_is_refused_when_verify_creds_accepts_it(evo: FakeEvolution) -> None:
    evo.on("GET", "/", json=ROOT)
    evo.on("POST", "/verify-creds", json={"status": 200, "message": "Credentials are valid"})
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(discovery.DiscoveryRefused) as info:
        await discovery.discover(client)

    assert info.value.message == GLOBAL_KEY_REFUSAL
    assert all(r.path != "/instance/fetchInstances" for r in evo.requests)


@pytest.mark.anyio
async def test_single_row_whose_token_differs_is_treated_as_the_global_key(evo: FakeEvolution) -> None:
    client = instance_server(evo, [instance_row("shop", token="someone-elses-token")])

    with pytest.raises(discovery.DiscoveryRefused) as info:
        await discovery.discover(client)

    assert info.value.message == GLOBAL_KEY_REFUSAL


@pytest.mark.anyio
@pytest.mark.parametrize("rows", [[], None])
async def test_no_instance_for_the_token_is_refused(evo: FakeEvolution, rows: object) -> None:
    client = instance_server(evo, rows)

    with pytest.raises(discovery.DiscoveryRefused) as info:
        await discovery.discover(client)

    assert info.value.message == NOT_INSTANCE_TOKEN


@pytest.mark.anyio
async def test_fetch_instances_401_is_refused(evo: FakeEvolution) -> None:
    client = instance_server(evo, None)
    evo.on("GET", "/instance/fetchInstances", status=401, json={"status": 401, "error": "Unauthorized"})

    with pytest.raises(discovery.DiscoveryRefused) as info:
        await discovery.discover(client)

    assert info.value.message == NOT_INSTANCE_TOKEN


@pytest.mark.anyio
async def test_two_rows_are_refused_naming_both(evo: FakeEvolution) -> None:
    client = instance_server(evo, [instance_row("shop"), instance_row("support")])

    with pytest.raises(discovery.DiscoveryRefused) as info:
        await discovery.discover(client)

    assert info.value.message == (
        "This token belongs to 2 instances (shop, support). Give each instance its own token, then retry."
    )


@pytest.mark.anyio
async def test_unknown_integration_is_refused(evo: FakeEvolution) -> None:
    client = instance_server(evo, [instance_row("shop", "TELEGRAM")])

    with pytest.raises(discovery.DiscoveryRefused) as info:
        await discovery.discover(client)

    assert info.value.message == "Unknown Evolution integration 'TELEGRAM'."


@pytest.mark.anyio
@pytest.mark.parametrize(
    "answer",
    [
        {"json": {"hello": "world"}},
        {"text": "<html>welcome</html>"},
        {"json": {"status": 200, "message": "no version here"}},
        {"status": 404, "json": {"message": "Not Found"}},
    ],
)
async def test_server_that_is_not_evolution_is_refused(evo: FakeEvolution, answer: dict) -> None:
    evo.on("GET", "/", **answer)
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(discovery.DiscoveryRefused) as info:
        await discovery.discover(client)

    assert info.value.message == (
        "The URL does not answer like an Evolution API server (GET / returned no version). "
        "Check the Evolution server URL."
    )


@pytest.mark.anyio
async def test_evolution_1x_is_refused(evo: FakeEvolution) -> None:
    client = instance_server(evo, [instance_row()], version="1.8.7")

    with pytest.raises(discovery.DiscoveryRefused) as info:
        await discovery.discover(client)

    assert info.value.message == "Evolution API 1.8.7 is not supported; this server needs Evolution API 2.x."
    assert all(r.path != "/verify-creds" for r in evo.requests)


@pytest.mark.anyio
async def test_server_without_verify_creds_is_refused(evo: FakeEvolution) -> None:
    evo.on("GET", "/", json=ROOT)
    evo.on("POST", "/verify-creds", status=404, json={"message": "Cannot POST /verify-creds"})
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(discovery.DiscoveryRefused) as info:
        await discovery.discover(client)

    assert info.value.message == (
        "This Evolution server has no POST /verify-creds; Evolution API 2.3 or later is required."
    )


@pytest.mark.anyio
async def test_unexpected_verify_creds_status_is_not_a_refusal(evo: FakeEvolution) -> None:
    evo.on("GET", "/", json=ROOT)
    evo.on("POST", "/verify-creds", status=500, json={"message": "boom"})
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(EvolutionHTTPError) as info:
        await discovery.discover(client)

    assert info.value.status == 500


@pytest.mark.anyio
async def test_unreachable_server_is_not_a_refusal(evo: FakeEvolution) -> None:
    evo.fail("GET", "/", httpx2.ConnectError("connection refused"))
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(EvolutionUnreachable):
        await discovery.discover(client)


@pytest.mark.anyio
async def test_fetch_instance_row_returns_the_row_of_the_identity(evo: FakeEvolution) -> None:
    evo.on("GET", "/instance/fetchInstances", json=[instance_row("other"), instance_row("shop")])
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    row = await discovery.fetch_instance_row(client, InstanceIdentity("shop", "WHATSAPP-BUSINESS"))

    assert row["name"] == "shop"
    assert evo.last("GET", "/instance/fetchInstances").params == {"instanceName": "shop"}


@pytest.mark.anyio
async def test_fetch_instance_row_reports_a_vanished_instance(evo: FakeEvolution) -> None:
    evo.on("GET", "/instance/fetchInstances", json=[instance_row("other")])
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with pytest.raises(ToolExecutionError) as info:
        await discovery.fetch_instance_row(client, InstanceIdentity("shop", "WHATSAPP-BUSINESS"))

    assert str(info.value) == "Evolution no longer lists this instance; check that it still exists."
