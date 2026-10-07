import re
from pathlib import Path

import pytest

from evolution_api_mcp import media
from evolution_api_mcp.errors import ToolExecutionError


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "share"
    folder.mkdir()
    return folder.resolve()


def _write(path: Path, data: bytes = b"data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_accepts_a_regular_file_inside_a_root(root):
    target = _write(root / "docs" / "invoice.pdf")
    assert media.resolve_local_file(str(target), [root]) == target


def test_relative_path_is_refused(root):
    _write(root / "a.txt")
    with pytest.raises(ToolExecutionError, match=r"Give an absolute path \(or one starting with ~\)\."):
        media.resolve_local_file("share/a.txt", [root])


def test_missing_file_and_directory_are_refused(root):
    (root / "folder").mkdir()
    with pytest.raises(ToolExecutionError, match=re.escape(f"No such file: {root / 'nope.txt'}")):
        media.resolve_local_file(str(root / "nope.txt"), [root])
    with pytest.raises(ToolExecutionError, match="No such file"):
        media.resolve_local_file(str(root / "folder"), [root])


def test_file_outside_every_root_is_refused_and_names_the_roots(root, tmp_path):
    outside = _write(tmp_path / "elsewhere" / "secret.txt")
    with pytest.raises(ToolExecutionError) as caught:
        media.resolve_local_file(str(outside), [root])
    message = str(caught.value)
    assert "is outside the folders this server may read" in message
    assert str(root) in message
    assert "EVOLUTION_MCP_FILE_ROOTS" in message


def test_symlink_inside_a_root_pointing_outside_is_refused(root, tmp_path):
    outside = _write(tmp_path / "elsewhere" / "secret.txt")
    (root / "innocent.txt").symlink_to(outside)
    with pytest.raises(ToolExecutionError, match="outside the folders"):
        media.resolve_local_file(str(root / "innocent.txt"), [root])


def test_symlink_that_stays_inside_the_root_resolves_to_its_target(root):
    target = _write(root / "real" / "photo.png")
    (root / "link.png").symlink_to(target)
    assert media.resolve_local_file(str(root / "link.png"), [root]) == target


def test_dot_directory_below_the_root_is_refused(root):
    hidden = _write(root / ".ssh" / "notes.txt")
    pattern = r"Hidden files and folders \(names starting with '\.'\) are never sent\."
    with pytest.raises(ToolExecutionError, match=pattern):
        media.resolve_local_file(str(hidden), [root])


def test_dot_file_below_the_root_is_refused(root):
    hidden = _write(root / ".env")
    with pytest.raises(ToolExecutionError, match="Hidden files and folders"):
        media.resolve_local_file(str(hidden), [root])


def test_hidden_symlink_to_a_visible_file_is_refused(root):
    target = _write(root / "visible.txt")
    (root / ".alias").symlink_to(target)
    with pytest.raises(ToolExecutionError, match="Hidden files and folders"):
        media.resolve_local_file(str(root / ".alias"), [root])


def test_dot_name_above_the_root_does_not_count_as_hidden(tmp_path):
    base = tmp_path / ".config" / "share"
    base.mkdir(parents=True)
    target = _write(base / "a.txt")
    assert media.resolve_local_file(str(target), [base.resolve()]) == target.resolve()


def test_empty_file_is_refused(root):
    empty = _write(root / "empty.txt", b"")
    with pytest.raises(ToolExecutionError, match="The file is empty."):
        media.resolve_local_file(str(empty), [root])


def test_size_limit_is_exact(root):
    at_limit = root / "at_limit.bin"
    over_limit = root / "over_limit.bin"
    for path, size in ((at_limit, media.MAX_FILE_BYTES), (over_limit, media.MAX_FILE_BYTES + 1)):
        with path.open("wb") as handle:  # sparse: no 100 MiB is written
            handle.truncate(size)
    assert media.resolve_local_file(str(at_limit), [root]) == at_limit
    with pytest.raises(ToolExecutionError) as caught:
        media.resolve_local_file(str(over_limit), [root])
    assert str(caught.value) == f"{over_limit} is 104857601 bytes; the limit is 104857600 bytes (100 MiB)."


@pytest.mark.parametrize(
    ("name", "mimetype", "kind"),
    [
        ("photo.png", "image/png", "image"),
        ("photo.jpg", "image/jpeg", "image"),
        ("clip.mp4", "video/mp4", "video"),
        ("song.mp3", "audio/mpeg", "audio"),
        ("voice.ogg", "audio/ogg", "audio"),
        ("report.pdf", "application/pdf", "document"),
        ("data.unknownext", "application/octet-stream", "document"),
        ("noextension", "application/octet-stream", "document"),
    ],
)
def test_media_kind(name, mimetype, kind):
    assert media.media_kind(Path("/x") / name) == (mimetype, kind)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\invoice.doc", "invoice.doc"),
        ('a:b*c?"d<e>f|g.txt', "a_b_c__d_e_f_g.txt"),
        ("line\nbreak\t.txt", "line_break_.txt"),
        ("", "file"),
        ("..", "file"),
        ("dir/", "file"),
    ],
)
def test_safe_file_name(raw, expected):
    assert media.safe_file_name(raw) == expected


def test_safe_file_name_is_capped_at_120_characters():
    assert media.safe_file_name("a" * 300) == "a" * 120


def test_download_target_for_a_person_and_a_group(tmp_path):
    person = media.download_target(
        tmp_path, "393331234567@s.whatsapp.net", "3EB0ABCDEF", "Report 1.pdf", "application/pdf"
    )
    assert person == tmp_path / "393331234567_3EB0ABCDEF_Report 1.pdf"
    group = media.download_target(tmp_path, "120363012345678901@g.us", "3EB0ABCDEF", "a.png", "image/png")
    assert group.name == "120363012345678901_3EB0ABCDEF_a.png"


def test_download_target_without_a_file_name_uses_the_mimetype_extension(tmp_path):
    target = media.download_target(tmp_path, "393331234567@s.whatsapp.net", "3EB0ABCDEF", None, "image/png")
    assert target.name == "393331234567_3EB0ABCDEF_media.png"


def test_download_target_cannot_escape_the_download_dir(tmp_path):
    target = media.download_target(tmp_path, "393331234567@s.whatsapp.net", "../../evil", "../../x.txt", "text/plain")
    assert target.parent == tmp_path
    assert "/" not in target.name


def test_export_file_name_prefixes_the_stamp_and_message_id():
    name = media.export_file_name("20260701-120130", "3EB0ABCDEF", "Report 1.pdf", "document", "application/pdf")
    assert name == "20260701-120130_3EB0ABCDEF_Report 1.pdf"


def test_export_file_name_without_a_stored_name_uses_the_type_and_the_mimetype_extension():
    assert media.export_file_name("S", "id", None, "voice_note", "audio/ogg; codecs=opus").startswith(
        "S_id_voice_note."
    )
    assert media.export_file_name("S", "id", None, "document", "application/x-unknown-thing") == "S_id_document"


def test_export_file_name_cannot_escape_and_keeps_the_extension_of_a_long_name():
    plain = media.export_file_name("S", "../../evil", "../../x.txt", "document", "text/plain")
    assert plain == "S_______evil_x.txt"
    long = media.export_file_name("20260701-120130", "3EB0ABCDEF", "a" * 300 + ".pdf", "document", "application/pdf")
    assert len(long) == media.MAX_NAME_CHARS
    assert long.startswith("20260701-120130_3EB0ABCDEF_aaa")
    assert long.endswith("a.pdf")


def test_export_folder_name_has_no_separators_and_a_short_label():
    assert media.export_folder_name("Mom/Dad: \\trip", "2026-07-01", "now") == "Mom_Dad_ _trip_2026-07-01_now"
    assert media.export_folder_name("x" * 100, "start", "now") == "x" * 60 + "_start_now"
