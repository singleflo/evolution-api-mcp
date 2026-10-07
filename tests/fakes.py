"""A programmable fake of Evolution API's HTTP answers.

`FakeEvolution` is an `httpx2.AsyncBaseTransport`: it stands where the network stands and nowhere else. The real
`EvolutionClient`, the real tools and the real request building all run against it, so a test proves what the
code sends and how it reads Evolution's reply. It models Evolution's documented answers only: every status and body
a test programs must come from the verified facts in the implementation plan (or the Evolution source), never from
what the code under test happens to expect.

Rules:

- An unprogrammed request fails loudly with `AssertionError("unprogrammed Evolution call <METHOD> <path>")`:
  a test that never said what Evolution answers is not a passing test.
- Paths are matched without the query string; the query is captured separately in `RecordedRequest.params`.
  Programmed paths and `RecordedRequest.path` use the percent-DECODED path (`/message/sendText/my inst`); the
  raw encoded form (`/message/sendText/my%20inst`) is accepted too when programming and looking up.
- Every request is recorded before it is answered, including the ones made to fail, so `requests` is the full log.
- Re-programming the same (method, path) replaces the earlier answer.
"""

from __future__ import annotations

import json as jsonlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import unquote

import httpx2


@dataclass
class RecordedRequest:
    """One request as Evolution saw it."""

    method: str
    path: str
    params: dict[str, str]
    json: object | None
    headers: dict[str, str]


@dataclass
class _Answer:
    status: int = 200
    json: object = None
    text: str | None = None
    handler: Callable[[RecordedRequest], tuple[int, object]] | None = None
    exc: Exception | None = None


class FakeEvolution(httpx2.AsyncBaseTransport):
    """Programmable Evolution: canned answers in, recorded requests out."""

    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self._answers: dict[tuple[str, str], _Answer] = {}

    def on(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        json: object = None,
        text: str | None = None,
        handler: Callable[[RecordedRequest], tuple[int, object]] | None = None,
    ) -> None:
        """Answer `method path` with `status` and a JSON body (or `text`), or compute the answer in `handler`.

        `json=None` with no `text` answers an empty body, which is what Evolution does for `null` results the
        client reads as `None`. Send the literal text "null" to answer a JSON null.
        """
        self._answers[(method.upper(), path)] = _Answer(status=status, json=json, text=text, handler=handler)

    def fail(self, method: str, path: str, exc: Exception) -> None:
        """Raise `exc` (an httpx2 transport error) when `method path` is requested."""
        self._answers[(method.upper(), path)] = _Answer(exc=exc)

    def last(self, method: str, path: str) -> RecordedRequest:
        """The most recent recorded request for `method path`; AssertionError when there is none."""
        for recorded in reversed(self.requests):
            if recorded.method == method.upper() and _path_matches(path, recorded.path):
                return recorded
        seen = ", ".join(f"{r.method} {r.path}" for r in self.requests) or "none"
        raise AssertionError(f"Evolution never received {method.upper()} {path}; received: {seen}")

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        recorded = RecordedRequest(
            method=request.method.upper(),
            path=request.url.path,
            params=dict(request.url.params.items()),
            json=jsonlib.loads(body) if body else None,
            headers=dict(request.headers.items()),
        )
        self.requests.append(recorded)

        answer = self._find(recorded.method, recorded.path, request.url.raw_path.split(b"?", 1)[0].decode("ascii"))
        if answer is None:
            raise AssertionError(f"unprogrammed Evolution call {recorded.method} {recorded.path}")
        if answer.exc is not None:
            raise answer.exc

        status, payload, text = answer.status, answer.json, answer.text
        if answer.handler is not None:
            status, payload = answer.handler(recorded)
            text = None
        if text is not None:
            return httpx2.Response(status, text=text, request=request)
        if payload is None:
            return httpx2.Response(status, request=request)
        return httpx2.Response(status, json=payload, request=request)

    def _find(self, method: str, decoded: str, raw: str) -> _Answer | None:
        for key in ((method, decoded), (method, raw)):
            if key in self._answers:
                return self._answers[key]
        return None


def _path_matches(wanted: str, recorded_decoded: str) -> bool:
    return unquote(wanted) == recorded_decoded


def program_directory(
    evo: FakeEvolution,
    instance: str = "inst",
    *,
    contacts: Iterable[dict] = (),
    groups: Iterable[dict] = (),
    rows: Iterable[dict] = (),
    instance_row: dict | None = None,
) -> None:
    """Answer the four requests that build the name directory: contacts, groups, a page of messages, the instance."""
    records = list(rows)
    block = {"total": len(records), "pages": 1, "currentPage": 1, "records": records}
    evo.on("POST", f"/chat/findContacts/{instance}", json=list(contacts))
    evo.on("GET", f"/group/fetchAllGroups/{instance}", json=list(groups))
    evo.on("POST", f"/chat/findMessages/{instance}", json={"messages": block})
    evo.on(
        "GET",
        "/instance/fetchInstances",
        json=[instance_row or {"name": instance, "ownerJid": "393930000000:7@s.whatsapp.net", "profileName": "Me"}],
    )
