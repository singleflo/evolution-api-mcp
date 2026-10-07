"""Local-file and media helpers: which files may be sent, what kind they are, where downloads land."""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from evolution_api_mcp.errors import ToolExecutionError

MAX_FILE_BYTES = 104_857_600  # 100 MiB
MAX_INLINE_IMAGE_BYTES = 4_194_304  # 4 MiB

_UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
_UNSAFE_ID_CHARS = re.compile(r"[^0-9A-Za-z_-]")
MAX_NAME_CHARS = 120


def _is_hidden_below(root: Path, target: Path) -> bool:
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return False
    return any(part.startswith(".") for part in parts)


def resolve_local_file(path: str, roots: Sequence[Path]) -> Path:
    """Return the resolved regular file at `path`, or raise ToolExecutionError saying why it may not be sent."""
    given = Path(path).expanduser()
    if not given.is_absolute():
        raise ToolExecutionError("Give an absolute path (or one starting with ~).")
    try:
        resolved = given.resolve(strict=True)
        is_file = resolved.is_file()
    except (OSError, RuntimeError):
        raise ToolExecutionError(f"No such file: {path}") from None
    if not is_file:
        raise ToolExecutionError(f"No such file: {path}")

    resolved_roots = [root.resolve() for root in roots]
    matching = [root for root in resolved_roots if resolved.is_relative_to(root)]
    if not matching:
        listed = ", ".join(str(root) for root in resolved_roots) or "none"
        raise ToolExecutionError(
            f"{path} is outside the folders this server may read ({listed}). "
            "Set EVOLUTION_MCP_FILE_ROOTS to allow another folder."
        )
    # The path as given may reach the file through a hidden symlink even when the resolved target is visible.
    unresolved_roots = [root for root in roots if given.is_relative_to(root)]
    if any(_is_hidden_below(root, resolved) for root in matching) or any(
        _is_hidden_below(root, given) for root in unresolved_roots
    ):
        raise ToolExecutionError("Hidden files and folders (names starting with '.') are never sent.")

    size = resolved.stat().st_size
    if size == 0:
        raise ToolExecutionError("The file is empty.")
    if size > MAX_FILE_BYTES:
        raise ToolExecutionError(f"{path} is {size} bytes; the limit is 104857600 bytes (100 MiB).")
    return resolved


def media_kind(path: Path) -> tuple[str, Literal["image", "video", "audio", "document"]]:
    """Return `(mimetype, kind)` for a file name; unknown types are documents."""
    mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mimetype.startswith("image/"):
        return mimetype, "image"
    if mimetype.startswith("video/"):
        return mimetype, "video"
    if mimetype.startswith("audio/"):
        return mimetype, "audio"
    return mimetype, "document"


def _plain_name(name: str) -> str:
    """`name` without directories, control or reserved characters; not yet cut to length."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return _UNSAFE_NAME_CHARS.sub("_", base).strip()


def safe_file_name(name: str) -> str:
    """Reduce `name` to a plain file name: no directories, no control or reserved characters, at most 120 chars."""
    cleaned = _plain_name(name)[:MAX_NAME_CHARS]
    if cleaned in ("", ".", ".."):
        return "file"
    return cleaned


def download_target(download_dir: Path, chat_jid: str, message_id: str, file_name: str | None, mimetype: str) -> Path:
    """Where a downloaded attachment is written: `<dir>/<chat digits or id>_<message id>_<file name>`."""
    chat_part = _UNSAFE_ID_CHARS.sub("_", chat_jid.split("@", 1)[0]) or "chat"
    message_part = _UNSAFE_ID_CHARS.sub("_", message_id) or "message"
    name = file_name or f"media{mimetypes.guess_extension(mimetype) or ''}"
    return download_dir / f"{chat_part}_{message_part}_{safe_file_name(name)}"


def export_file_name(stamp: str, message_id: str, file_name: str | None, kind: str, mimetype: str) -> str:
    """Name of an exported attachment: `<stamp>_<message id>_<name>`; a long name is cut before its extension.

    A message without a stored file name is called after its type and the extension of its mimetype.
    """
    prefix = f"{stamp}_{_UNSAFE_ID_CHARS.sub('_', message_id) or 'message'}_"
    original = _plain_name(file_name) if file_name else ""
    if original in ("", ".", ".."):
        original = f"{kind}{mimetypes.guess_extension(mimetype.split(';', 1)[0].strip()) or ''}"
    suffix = Path(original).suffix[:16]
    stem = original[: len(original) - len(suffix)][: max(1, MAX_NAME_CHARS - len(prefix) - len(suffix))]
    return safe_file_name(prefix + stem + suffix)


def export_folder_name(label: str, since: str, until: str) -> str:
    """Folder of a chat export: `<chat label>_<first day>_<last day>`, with no directory separators."""
    plain = label.replace("/", "_").replace("\\", "_")[:60]
    return safe_file_name(f"{plain}_{since}_{until}")
