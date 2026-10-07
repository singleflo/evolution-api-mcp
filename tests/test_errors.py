"""Tool result formatting and the three outcomes of a failed Evolution call."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from evolution_api_mcp.client import EvolutionError, EvolutionHTTPError, EvolutionUncertain, EvolutionUnreachable
from evolution_api_mcp.errors import (
    MAX_RESULT_CHARS,
    TRUNCATION_NOTICE,
    ToolExecutionError,
    raise_evolution_failure,
    tool_result,
)


async def failure_text(exc: EvolutionError, **kwargs: object) -> str:
    with pytest.raises(ToolExecutionError) as caught:
        await raise_evolution_failure(exc, **kwargs)  # type: ignore[arg-type]
    return str(caught.value)


def test_tool_execution_error_is_a_tool_error() -> None:
    assert issubclass(ToolExecutionError, ToolError)


def test_tool_result_passes_strings_through_and_serialises_everything_else() -> None:
    assert tool_result("already text") == "already text"
    assert tool_result({"name": "Zoë", "n": 1}) == '{"name": "Zoë", "n": 1}'
    assert tool_result({"at": datetime(2026, 9, 29, 21, 4, 5, tzinfo=timezone.utc)}) == (
        '{"at": "2026-09-29 21:04:05+00:00"}'
    )


def test_tool_result_keeps_text_at_the_limit_and_cuts_beyond_it_with_the_notice() -> None:
    at_limit = "x" * MAX_RESULT_CHARS
    assert tool_result(at_limit) == at_limit

    cut = tool_result("x" * (MAX_RESULT_CHARS + 1))
    assert cut == "x" * MAX_RESULT_CHARS + TRUNCATION_NOTICE
    assert cut.endswith("cut at 30000 characters: ask for fewer items (limit) or a narrower time range.")


def test_tool_result_cuts_a_non_list_payload_with_the_custom_notice() -> None:
    payload = {"text": "y" * 40_000}
    cut = tool_result(payload, notice="\n[cut: use a smaller limit]")
    assert len(cut) == MAX_RESULT_CHARS + len("\n[cut: use a smaller limit]")
    assert cut.endswith("\n[cut: use a smaller limit]")
    assert cut.startswith('{"text": "yyy')


def test_tool_result_drops_trailing_list_items_and_stays_valid_json() -> None:
    payload = {"rows": [f"row-{i:03d}-" + "y" * 100 for i in range(400)], "offset": 0}
    cut = json.loads(tool_result(payload))
    kept = len(cut["rows"])
    assert 0 < kept < 400
    assert cut["rows"] == payload["rows"][:kept]
    assert cut["offset"] == 0
    assert cut["truncated"] == (f"Showing {kept} of 400 rows; ask for fewer items (limit) or a narrower time range.")
    assert len(tool_result(payload)) <= MAX_RESULT_CHARS


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["before_mutation", "after_mutation_possible"])
async def test_unreachable_says_nothing_was_sent_in_either_phase(phase: str) -> None:
    text = await failure_text(EvolutionUnreachable("evo.example.com", "connection refused"), phase=phase)
    assert text == "Could not reach Evolution at evo.example.com: connection refused. Nothing was sent."


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["before_mutation", "after_mutation_possible"])
async def test_unauthorized_tells_both_deployments_how_to_fix_the_token(phase: str) -> None:
    text = await failure_text(EvolutionHTTPError(401, "Unauthorized", {"status": 401}), phase=phase)
    assert text == (
        "Evolution rejected the instance token (401 Unauthorized). Nothing was changed. "
        "Local server: update EVOLUTION_INSTANCE_TOKEN. "
        "Hosted: disconnect and reconnect with the instance's current token."
    )


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["before_mutation", "after_mutation_possible"])
async def test_a_4xx_refusal_is_definitive_in_either_phase(phase: str) -> None:
    exc = EvolutionHTTPError(400, "Method not available on WhatsApp Business API", {})
    text = await failure_text(exc, phase=phase)
    assert text == (
        "Evolution refused the request (400): Method not available on WhatsApp Business API. Nothing was changed."
    )


@pytest.mark.anyio
async def test_refusal_message_with_a_trailing_period_does_not_double_it() -> None:
    text = await failure_text(EvolutionHTTPError(404, "Instance not found.", {}), phase="before_mutation")
    assert text == "Evolution refused the request (404): Instance not found. Nothing was changed."


@pytest.mark.anyio
async def test_server_error_before_mutation_claims_nothing_changed() -> None:
    text = await failure_text(EvolutionHTTPError(502, "Bad Gateway", "Bad Gateway"), phase="before_mutation")
    assert text == "Evolution failed (HTTP 502): Bad Gateway. Nothing was changed."


@pytest.mark.anyio
async def test_uncertain_transport_error_before_mutation_claims_nothing_changed() -> None:
    text = await failure_text(EvolutionUncertain("evo.test", "read timed out"), phase="before_mutation")
    assert text == "Evolution failed (no answer from evo.test): read timed out. Nothing was changed."


@pytest.mark.anyio
async def test_uncertain_after_a_possible_write_embeds_the_reread_state() -> None:
    calls: list[int] = []

    async def reread() -> object:
        calls.append(1)
        return {"messages": [{"text": "Zoë"}]}

    text = await failure_text(EvolutionHTTPError(500, "boom", {}), phase="after_mutation_possible", reread=reread)
    assert text == (
        "UNCERTAIN: Evolution did not confirm the result (HTTP 500: boom); "
        'the change may already have been applied. Verified state: {"messages": [{"text": "Zoë"}]}. '
        "Do NOT repeat the call before checking this state."
    )
    assert calls == [1]
    assert "Nothing was changed" not in text


@pytest.mark.anyio
async def test_uncertain_transport_error_after_a_possible_write() -> None:
    text = await failure_text(EvolutionUncertain("evo.test", "read timed out"), phase="after_mutation_possible")
    assert text == (
        "UNCERTAIN: Evolution did not confirm the result (no answer from evo.test: read timed out); "
        "the change may already have been applied. Verified state: not re-read. "
        "Do NOT repeat the call before checking this state."
    )


@pytest.mark.anyio
async def test_a_failing_reread_reports_not_re_read_instead_of_a_guess() -> None:
    async def reread() -> object:
        raise EvolutionUnreachable("evo.test", "connection refused")

    text = await failure_text(
        EvolutionUncertain("evo.test", "read timed out"), phase="after_mutation_possible", reread=reread
    )
    assert "Verified state: not re-read." in text


@pytest.mark.anyio
async def test_reread_is_not_called_when_the_outcome_is_definitive() -> None:
    async def reread() -> object:
        raise AssertionError("must not re-read")

    await failure_text(EvolutionHTTPError(400, "bad", {}), phase="after_mutation_possible", reread=reread)
    await failure_text(EvolutionUnreachable("h", "r"), phase="after_mutation_possible", reread=reread)
    await failure_text(EvolutionHTTPError(500, "boom", {}), phase="before_mutation", reread=reread)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "message",
    [
        "Timed Out",
        "Error: connect ETIMEDOUT 1.2.3.4:443",
        "read ECONNRESET",
        "socket hang up",
        "the request timed out",
    ],
)
async def test_a_4xx_that_wraps_a_delivery_timeout_is_treated_like_a_5xx(message: str) -> None:
    text = await failure_text(EvolutionHTTPError(400, message, {}), phase="after_mutation_possible")
    assert text.startswith(f"UNCERTAIN: Evolution did not confirm the result (HTTP 400: {message});")
    before = await failure_text(EvolutionHTTPError(400, message, {}), phase="before_mutation")
    assert before == f"Evolution failed (HTTP 400): {message}. Nothing was changed."


@pytest.mark.anyio
async def test_401_stays_a_token_error_even_when_its_message_looks_like_a_timeout() -> None:
    text = await failure_text(EvolutionHTTPError(401, "timed out", {}), phase="after_mutation_possible")
    assert text.startswith("Evolution rejected the instance token")


@pytest.mark.anyio
async def test_the_original_exception_stays_attached_as_the_cause() -> None:
    original = EvolutionHTTPError(500, "boom", {})
    with pytest.raises(ToolExecutionError) as caught:
        await raise_evolution_failure(original, phase="before_mutation")
    assert caught.value.__cause__ is original
