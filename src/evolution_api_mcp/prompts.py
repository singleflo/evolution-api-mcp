"""MCP prompts: four ready-made requests, and live completion of their `chat` argument from the name directory."""

from collections import Counter
from typing import Annotated

from mcp.server import MCPServer
from mcp_types import Completion, CompletionArgument, CompletionContext, PromptReference, ResourceTemplateReference
from pydantic import Field

from evolution_api_mcp import context, directory, messages

MAX_COMPLETIONS = 100


def _shadowed_lid(book: directory.Directory, chat_id: str) -> bool:
    """True for an @lid whose phone-JID twin is also in the directory."""
    return chat_id.endswith("@lid") and any(other in book.entries for other in book.identities(chat_id)[1:])


def _candidates(book: directory.Directory, typed: str) -> list[directory.Entry]:
    """Named chats matching the typed text: starting with it first, then the rest; one entry per person."""
    wanted = directory.normalize(typed)
    if wanted:
        return book.partial(wanted)
    people = [
        entry
        for entry in book.entries.values()
        if book.name_of(entry.chat_id) and not _shadowed_lid(book, entry.chat_id)
    ]
    return sorted(people, key=lambda entry: (directory.normalize(book.name_of(entry.chat_id) or ""), entry.chat_id))


def _labels(book: directory.Directory, entries: list[directory.Entry]) -> list[str]:
    """Display names; a name shared by several chats is shown as "Name (chat_id)"."""
    names = [(book.name_of(entry.chat_id) or entry.chat_id, entry.chat_id) for entry in entries]
    counts = Counter(name for name, _ in names)
    return [f"{name} ({chat_id})" if counts[name] > 1 else name for name, chat_id in names]


async def complete_chat(
    ref: PromptReference | ResourceTemplateReference,
    argument: CompletionArgument,
    completion_context: CompletionContext | None,
) -> Completion:
    """Contact and group names for a prompt's `chat` argument; any failure yields no suggestions."""
    if not isinstance(ref, PromptReference) or argument.name != "chat":
        return Completion(values=[])
    try:
        conn, client = await context.resolve()
        book = await directory.get(client, conn)
        entries = [entry for entry in _candidates(book, argument.value) if not messages.is_noise_chat(entry.chat_id)]
        values = _labels(book, entries)
        return Completion(values=values[:MAX_COMPLETIONS], total=len(values), has_more=len(values) > MAX_COMPLETIONS)
    except Exception:
        return Completion(values=[])


def register(mcp: MCPServer) -> None:
    """Mount the four prompts and the `chat` argument completion."""

    @mcp.prompt(
        name="inbox",
        title="What's new",
        description="Summarise what arrived on WhatsApp, chat by chat, and list the chats waiting for a reply.",
    )
    def inbox(
        since: Annotated[str, Field(description="Start of the period, e.g. 'today' or 'since Monday'.")] = "",
    ) -> str:
        return (
            f"Show me what arrived on WhatsApp {since or 'in the last 24 hours'}: group it by chat, "
            "summarise each chat in one line, and list the chats waiting for my reply."
        )

    @mcp.prompt(
        name="reply",
        title="Reply in a chat",
        description="Read the latest messages of a chat, draft a reply and wait for approval before sending it.",
    )
    def reply(
        chat: Annotated[str, Field(description="Contact or group name, or phone number.")],
        instructions: Annotated[str, Field(description="What the reply should say.")] = "",
    ) -> str:
        return (
            f"Show me the latest messages of {chat} and draft a reply{': ' + instructions if instructions else ''}. "
            "Show me the draft and wait for my approval before sending it."
        )

    @mcp.prompt(
        name="find_attachment",
        title="Find an attachment",
        description="Find a document, photo or other attachment by description, optionally within one chat.",
    )
    def find_attachment(
        description: Annotated[str, Field(description="What the attachment is, e.g. 'the PDF invoice from Mario'.")],
        chat: Annotated[str, Field(description="Contact or group name, or phone number.")] = "",
    ) -> str:
        return (
            f"Find the attachment that matches: {description}{' in the chat ' + chat if chat else ''}. "
            "List the matching messages with date and sender, then ask me which one to download."
        )

    @mcp.prompt(
        name="export",
        title="Export a chat",
        description="Export a chat, or a period of it, with its attachments.",
    )
    def export(
        chat: Annotated[str, Field(description="Contact or group name, or phone number.")],
        period: Annotated[str, Field(description="Period to export, e.g. 'September 2026'.")] = "",
        format: Annotated[str, Field(description="Transcript format: Markdown, JSON or WhatsApp TXT.")] = "Markdown",
    ) -> str:
        return (
            f"Export my WhatsApp chat with {chat}{' for ' + period if period else ''} as {format} "
            "with its attachments, and tell me where the export is."
        )

    mcp.completion()(complete_chat)
