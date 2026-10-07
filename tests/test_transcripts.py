import json

from evolution_api_mcp import transcripts
from evolution_api_mcp.directory import Directory

PERSON = "393331234567@s.whatsapp.net"
LID = "98765432101234@lid"


def _message(message_id, *, stamp="2026-07-01T12:00:00+02:00", from_me=False, sender=PERSON, **extra):
    return {
        "message_id": message_id,
        "chat_id": PERSON,
        "from_me": from_me,
        "sender": "me" if from_me else sender,
        "timestamp": stamp,
        "type": "text",
        **extra,
    }


def test_a_sender_without_a_name_is_shown_as_plus_digits_then_as_the_jid():
    names = Directory(lid_phone={LID: "393339876543"})
    messages = [
        _message("a", text="one"),
        _message("b", text="two", sender=LID),
        _message("c", text="three", sender="1234@lid"),
    ]
    text = transcripts.render_markdown(PERSON, None, "NOW", messages, names, {})
    assert "**12:00** +393331234567: one" in text
    assert "**12:00** +393339876543: two" in text
    assert "**12:00** 1234@lid: three" in text
    assert text.startswith(f"# {PERSON}\n")


def test_messages_without_text_get_a_one_line_description():
    messages = [
        _message("a", type="location", location={"latitude": 45.5, "longitude": 9.2, "name": "Duomo"}),
        _message("b", type="contact", contact={"name": "Luca"}),
        _message("c", type="poll", poll={"question": "Pizza?", "options": ["yes", "no"]}),
        _message("d", type="image"),
        _message("e", type="document", media={"file_name": "a.pdf"}),
        _message("f", type="reaction", reaction={"emoji": "\u2764", "target_message_id": "gone"}),
    ]
    text = transcripts.render_markdown(PERSON, "Mario", "NOW", messages, Directory(), {})
    assert "(location: Duomo 45.5, 9.2)" in text
    assert "(contact: Luca)" in text
    assert "(poll: Pizza? - yes / no)" in text
    assert ": (image)\n" in text
    assert "(document: a.pdf)" in text
    assert "(reaction: \u2764)" in text


def test_markdown_shows_the_reactions_of_a_message_and_links_a_file_with_spaces():
    messages = [
        _message(
            "a",
            text="hello",
            reactions=[{"emoji": "\u2764", "by": "Ana"}, {"emoji": "\U0001f44d", "by": "Bob"}],
        ),
        _message("b", type="document", media={"file_name": "my file.pdf"}),
    ]
    text = transcripts.render_markdown(PERSON, "Mario", "NOW", messages, Directory(), {"b": "media/my file.pdf"})
    assert "Reactions: \u2764 Ana, \U0001f44d Bob" in text
    assert "Attachment: [my file.pdf](media/my%20file.pdf)" in text
    assert text.endswith("\n") and "\n\n\n" not in text


def test_the_summary_line_reports_an_empty_export_without_a_period():
    text = transcripts.render_markdown(PERSON, "Mario", "NOW", [], Directory(), {})
    assert text == f"# Mario\n\nChat {PERSON} \u00b7 exported NOW \u00b7 0 messages\n"


def test_whatsapp_txt_marks_omitted_media_and_keeps_multiline_text_as_plain_lines():
    messages = [
        _message("a", text="line one\nline two", sender_name="Ana"),
        _message("b", type="voice_note"),
        _message("c", type="location", location={"latitude": 1, "longitude": 2}),
    ]
    text = transcripts.render_whatsapp_txt(messages, Directory(), {}, None)
    assert text == (
        "[01/07/26, 12:00:00] Ana: line one\n"
        "line two\n"
        "[01/07/26, 12:00:00] +393331234567: \u200evoice note omitted\n"
        "[01/07/26, 12:00:00] +393331234567: (location: 1, 2)\n"
    )


def test_whatsapp_txt_of_nothing_is_empty_and_own_messages_use_the_given_name_or_me():
    assert transcripts.render_whatsapp_txt([], Directory(), {}, "Giulia") == ""
    mine = [_message("a", text="hi", from_me=True)]
    assert transcripts.render_whatsapp_txt(mine, Directory(), {}, "Giulia").startswith(
        "[01/07/26, 12:00:00] Giulia: hi"
    )
    assert transcripts.render_whatsapp_txt(mine, Directory(), {}, None).startswith("[01/07/26, 12:00:00] Me: hi")


def test_a_quote_names_this_number_as_me_and_an_unnamed_jid_by_its_digits():
    names = Directory(me=frozenset({"393930000000@s.whatsapp.net"}))
    messages = [
        _message("a", text="x", quoted={"message_id": "q", "sender": "393930000000@s.whatsapp.net", "text": "mine"}),
        _message("b", text="y", quoted={"message_id": "q", "sender": PERSON, "text": "two\nlines"}),
        _message("c", text="z", quoted={"message_id": "q"}),
    ]
    text = transcripts.render_markdown(PERSON, "Mario", "NOW", messages, names, {})
    assert "> Me: mine" in text
    assert "> +393331234567: two lines" in text
    assert "z\n> (quoted message)" in text


def test_json_adds_media_file_only_where_a_file_was_exported():
    messages = [_message("a", text="hi"), _message("b", type="image")]
    document = json.loads(
        transcripts.render_json(PERSON, "Mario", "NOW", "S", None, "UTC", messages, {"b": "media/x.jpg"})
    )
    assert document["messages"][0] == messages[0]
    assert document["messages"][1]["media_file"] == "media/x.jpg"
    assert document["since"] == "S" and document["until"] is None and document["time_zone"] == "UTC"
