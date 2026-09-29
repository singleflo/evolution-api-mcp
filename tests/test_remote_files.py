"""Expiring download links for files produced on the hosted server.

The token IS the credential (custom routes are unauthenticated by SDK
design), so the tests here assert the things that make that safe, not just a
200: the exact bytes, the no-store cache header, and, after expiry, both the
404 AND the file's removal from disk.

`ttl_minutes=0` is the deterministic way to force expiry: the row's
expires_at is already in the past when the request arrives, no clock
monkeypatching needed.
"""

import shutil

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from evolution_api_mcp import paths
from evolution_api_mcp.remote import files
from evolution_api_mcp.remote.files import publish, purge_expired_files, serve_file

IMAGE_BYTES = b"\x89PNG pretend image bytes"
PDF_BYTES = b"%PDF-1.4 pretend document"
PUBLIC_URL = "https://mcp.example.test"
SUBJECT = "t_test_subject"


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    """Given: the server's data directory is this test's own tmp_path."""
    monkeypatch.delenv("EVOLUTION_MCP_DATA_DIR", raising=False)
    paths.set_data_dir_override(tmp_path)
    yield tmp_path
    paths.set_data_dir_override(None)


def client() -> TestClient:
    """A bare Starlette app with only the file route."""
    app = Starlette(routes=[Route("/files/{token}", serve_file, methods=["GET"])])
    return TestClient(app)


def token_of(link: dict) -> str:
    return link["url"].rsplit("/", 1)[1]


def test_publish_answers_a_link_and_the_route_serves_the_exact_bytes(data_dir):
    """Given a produced file, When it is published and fetched,
    Then the bytes arrive with the attachment and no-store headers."""
    source = data_dir / "photo.png"
    source.write_bytes(IMAGE_BYTES)

    link = publish(source, SUBJECT, public_url=PUBLIC_URL)

    assert set(link) == {"name", "size", "url", "expires_at"}
    assert link["name"] == "photo.png"
    assert link["size"] == len(IMAGE_BYTES)
    assert link["url"].startswith(f"{PUBLIC_URL}/files/")
    assert not source.exists()  # moved, not copied

    response = client().get(f"/files/{token_of(link)}")

    assert response.status_code == 200
    assert response.content == IMAGE_BYTES
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_an_expired_token_answers_404_and_the_file_is_gone(data_dir):
    """Given a link past its TTL, When it is fetched,
    Then the answer is 404 AND the file no longer exists on disk.

    A 404 that leaves the bytes in place keeps a tenant's media sitting in
    the data directory forever.
    """
    source = data_dir / "secret.pdf"
    source.write_bytes(PDF_BYTES)
    token = token_of(publish(source, SUBJECT, public_url=PUBLIC_URL, ttl_minutes=0))

    response = client().get(f"/files/{token}")

    assert response.status_code == 404
    assert response.json() == {"error": "not found or expired"}
    assert not any((data_dir / "files" / SUBJECT).iterdir())


def test_an_unknown_token_answers_404(data_dir):
    """Given a token nothing published, When it is fetched, Then 404 JSON."""
    response = client().get("/files/not-a-real-token")

    assert response.status_code == 404
    assert response.json() == {"error": "not found or expired"}


def test_a_row_whose_bytes_are_gone_answers_the_same_404(data_dir):
    """Given a live link whose file vanished from disk, When it is fetched,
    Then the answer is indistinguishable from an unknown token."""
    source = data_dir / "vanished.pdf"
    source.write_bytes(PDF_BYTES)
    token = token_of(publish(source, SUBJECT, public_url=PUBLIC_URL))
    shutil.rmtree(data_dir / "files" / SUBJECT / token)

    response = client().get(f"/files/{token}")

    assert response.status_code == 404
    assert response.json() == {"error": "not found or expired"}


def test_a_path_traversal_url_never_reaches_the_handler(data_dir):
    """Given /files/../etc/passwd, When it is fetched, Then 404.

    Starlette's {token} path parameter cannot contain a slash, so dot
    segments are normalised away before routing; the handler's own defence is
    the exact-match token lookup.
    """
    assert client().get("/files/../etc/passwd").status_code == 404


def test_publish_refuses_a_subject_that_could_escape_the_files_root(data_dir):
    """Given a subject carrying a separator or dot segments, When published,
    Then ValueError: the subject becomes a directory name under
    data_dir()/files/."""
    source = data_dir / "x.pdf"
    source.write_bytes(PDF_BYTES)
    for bad in ("../escape", "a/b", "..", ""):
        with pytest.raises(ValueError):
            publish(source, bad, public_url=PUBLIC_URL)
    assert source.exists()


def test_purge_tenant_artifacts_removes_only_that_tenants_files(data_dir):
    """Given two tenants with published files, When one is purged,
    Then its tree is gone and the other tenant's files are untouched."""
    other = data_dir / "files" / "t_other" / "tok"
    other.mkdir(parents=True)
    (other / "keep.pdf").write_bytes(PDF_BYTES)
    mine = data_dir / "files" / SUBJECT / "tok"
    mine.mkdir(parents=True)
    (mine / "gone.pdf").write_bytes(PDF_BYTES)

    files.purge_tenant_artifacts(SUBJECT)

    assert not (data_dir / "files" / SUBJECT).exists()
    assert (other / "keep.pdf").exists()


def test_purge_tenant_artifacts_refuses_an_escaping_subject(data_dir):
    """Given a subject carrying a separator or dot segments, When purged,
    Then ValueError: the same guard as publish."""
    for bad in ("../escape", "a/b", "..", ""):
        with pytest.raises(ValueError):
            files.purge_tenant_artifacts(bad)


def test_purge_removes_only_the_expired_rows_and_directories(data_dir):
    """Given one expired and one live file, When purging,
    Then the expired row and its directory go and the live one survives."""
    stale = data_dir / "stale.pdf"
    stale.write_bytes(PDF_BYTES)
    live = data_dir / "live.png"
    live.write_bytes(IMAGE_BYTES)
    stale_token = token_of(publish(stale, SUBJECT, public_url=PUBLIC_URL, ttl_minutes=0))
    live_token = token_of(publish(live, SUBJECT, public_url=PUBLIC_URL))

    assert purge_expired_files() == 1

    assert not (data_dir / "files" / SUBJECT / stale_token).exists()
    assert (data_dir / "files" / SUBJECT / live_token / "live.png").exists()
    assert client().get(f"/files/{live_token}").status_code == 200
