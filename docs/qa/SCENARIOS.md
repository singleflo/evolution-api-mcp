# Scenario catalog

Twenty-one requests an assistant gets from the owner of a WhatsApp number, each with the number of tool calls it
should take. The catalog has two uses:

- `tests/test_scenarios_live.py` drives each scenario's expected call through an in-memory MCP client against a real
  instance and asserts the shape of the result (`uv run pytest -m live`).
- A person runs each request by hand in real agents (protocol below), logs the outcome in [RUNS.md](RUNS.md), and
  writes only the scenarios that passed into `src/evolution_api_mcp/assets/recipes.md`.

The requests are written the way the owner talks, in Italian with an English rendering. `<contatto>`, `<gruppo>` and
`<nome>` stand for a contact or group of the instance under test; `<id>` for a message id an earlier answer showed.
The agent gets the request text only: no tool names, no hints.

## Rules for every scenario

- **Budget.** The maximum number of MCP tool calls the agent makes from the request to its answer. Every call counts,
  including a call that failed. Calls of the previous turn that a scenario lists under *Setup* do not count.
- **Pass when.** The agent's final answer is correct for the instance, it stays within budget, and no call failed
  because of a wrong guess (a wrong parameter name, a chat name it had to retry, a tool it picked first and
  abandoned). A refusal the tool names as expected (for example a send held back by the user's own policy) is not a
  wrong guess.
- **Expected calls.** The sequence the descriptions are written to produce. Another valid sequence within budget
  also passes; the expected one is what the live suite runs.
- **Sends.** S11 to S15 and S20 change something other people or the phone can see (or write files) and run in the
  live suite only with `EVOLUTION_TEST_ALLOW_SEND=1`, and only against `EVOLUTION_TEST_CHAT`.
- **Local only.** S13 uses `send_local_files`, which exists only on the local server.

## S01 — What arrived today

- **IT:** Cosa mi è arrivato oggi?
- **EN:** What came in today?
- **Budget:** 1
- **Expected calls:** `list_recent_messages(since="<today>T00:00:00")`
- **Pass when:** the answer groups today's messages by chat with the chat names the result carries and mentions that
  nothing arrived when `chats` is empty.

## S02 — Chats waiting for my reply

- **IT:** Quali chat aspettano una mia risposta?
- **EN:** Which chats are waiting for a reply from me?
- **Budget:** 1
- **Expected calls:** `list_chats(waiting_for_reply=true)`
- **Pass when:** the answer lists the returned chats by name with the preview of their last message, all of which
  came from the other side.

## S03 — Unread messages

- **IT:** Fammi vedere i messaggi non letti
- **EN:** Show me the unread messages.
- **Budget:** 1
- **Expected calls:** `list_recent_messages(only_unread=true)`
- **Pass when:** the answer shows each chat with unread messages, its unread count and its newest unread messages,
  without a second call to read them.

## S04 — Read a conversation

- **IT:** Leggi la conversazione con <contatto>
- **EN:** Read my conversation with <contatto>.
- **Budget:** 1
- **Expected calls:** `read_messages(chat="<contatto>")`
- **Pass when:** the agent passes the name straight to `read_messages` (no lookup first) and summarises the newest
  messages.

## S05 — Who is this @lid

- **IT:** Chi è <id>@lid?
- **EN:** Who is <id>@lid?
- **Budget:** 1
- **Expected calls:** `get_chat(chat="<id>@lid")`
- **Setup:** an `@lid` id taken from an earlier `list_chats` or `read_messages` answer.
- **Pass when:** the answer gives the name and, when the result carries one, the phone number behind the id.

## S06 — Groups in common

- **IT:** In quali gruppi siamo sia io sia <contatto>?
- **EN:** Which groups are both <contatto> and I in?
- **Budget:** 1
- **Expected calls:** `get_chat(chat="<contatto>")`
- **Pass when:** the answer lists `groups_in_common` (mentioning `groups_in_common_truncated` when set). On a
  Business instance the answer says Evolution offers no such list, instead of guessing.

## S07 — Find the PDF a contact sent

- **IT:** Trova il PDF che mi ha mandato <contatto>
- **EN:** Find the PDF <contatto> sent me.
- **Budget:** 1
- **Expected calls:** `search_messages(sender="<contatto>", message_type="document")`
- **Pass when:** the answer names the document messages found, with file name, date and chat; it does not list
  messages by anyone else.

## S08 — Download that PDF

- **IT:** Scarica quel PDF
- **EN:** Download that PDF.
- **Budget:** 1
- **Expected calls:** `download_message_media(message_id="<id>")`
- **Setup:** S07.
- **Pass when:** the answer gives the path of the saved file (`saved_to`) and its size.

## S09 — Search a word in a group

- **IT:** Cerca "fattura" nel gruppo <gruppo>
- **EN:** Search for "fattura" in the group <gruppo>.
- **Budget:** 1
- **Expected calls:** `search_messages(chat="<gruppo>", query="fattura")`
- **Pass when:** the answer shows the matches with sender and date, and says when the scan did not cover the whole
  group (`complete` false).

## S10 — Context around a hit

- **IT:** Mostrami il contesto di quel messaggio
- **EN:** Show me the context of that message.
- **Budget:** 1
- **Expected calls:** `read_messages(chat="<gruppo>", around_message_id="<id>")`
- **Setup:** S09.
- **Pass when:** the answer shows the messages before and after the hit, with the hit marked, in one call.

## S11 — Reply to a message

- **IT:** Rispondi a quel messaggio: "ok, grazie"
- **EN:** Reply to that message: "ok, grazie".
- **Budget:** 1
- **Expected calls:** `send_text_message(reply_to_message_id="<id>", text="ok, grazie")`
- **Setup:** S10.
- **Pass when:** the message is delivered as a quoted reply in the chat of the message, with no `chat` argument and
  no lookup call.

## S12 — Forward a photo

- **IT:** Inoltra quella foto a <contatto>
- **EN:** Forward that photo to <contatto>.
- **Budget:** 1
- **Expected calls:** `forward_message(message_id="<id>", to=["<contatto>"])`
- **Setup:** S17 (or any answer that showed a photo's message id).
- **Pass when:** the answer reports the new message id and states that WhatsApp shows it without the Forwarded
  label.

## S13 — Send three files

- **IT:** Manda questi 3 file a <contatto>
- **EN:** Send these 3 files to <contatto>.
- **Budget:** 1
- **Expected calls:** `send_local_files(paths=["<file1>", "<file2>", "<file3>"], chat="<contatto>")`
- **Setup:** three files inside the allowed folders (`EVOLUTION_MCP_FILE_ROOTS`).
- **Pass when:** all three files are delivered in order with one call.

## S14 — Export a month with attachments

- **IT:** Esporta la chat con <contatto> di settembre con gli allegati
- **EN:** Export my chat with <contatto> for September with the attachments.
- **Budget:** 1
- **Expected calls:** `export_chat(chat="<contatto>", since="<year>-09-01T00:00:00", until="<year>-10-01T00:00:00")`
- **Pass when:** the answer gives the export folder (or the download link when hosted), the number of messages and
  files, and mentions `media_skipped` when it is not empty.

## S15 — Every document of a group

- **IT:** Scarica tutti i documenti della chat <gruppo>
- **EN:** Download all the documents of the chat <gruppo>.
- **Budget:** 1
- **Expected calls:** `export_chat(chat="<gruppo>", content="media", media_types=["document"])`
- **Pass when:** the agent uses one export call, not one `download_message_media` call per document, and reports how
  many files were saved.

## S16 — Who mentioned me in the groups

- **IT:** Chi mi ha menzionato oggi nei gruppi?
- **EN:** Who mentioned me in the groups today?
- **Budget:** 1
- **Expected calls:** `list_recent_messages(since="<today>T00:00:00", kind="groups", mentions_me=true)`
- **Pass when:** the answer lists the messages that @-mention this number with sender and group, or says none did.

## S17 — Yesterday's photos in a group

- **IT:** Le foto di ieri nel gruppo <gruppo>
- **EN:** Yesterday's photos in the group <gruppo>.
- **Budget:** 1
- **Expected calls:**
  `search_messages(chat="<gruppo>", message_type="image", since="<yesterday>T00:00:00", until="<today>T00:00:00")`
- **Pass when:** the answer lists the images of that day with sender and time.

## S18 — The latest links I received

- **IT:** Gli ultimi link che mi hanno mandato
- **EN:** The latest links people sent me.
- **Budget:** 1
- **Expected calls:** `search_messages(message_type="link", direction="incoming")`
- **Pass when:** the answer lists received messages that contain a web address, newest first.

## S19 — Who reacted to my last message

- **IT:** Chi ha reagito al mio ultimo messaggio a <contatto>?
- **EN:** Who reacted to my last message to <contatto>?
- **Budget:** 1
- **Expected calls:** `read_messages(chat="<contatto>", limit=5)`
- **Pass when:** the answer names the reactors from the `reactions` of the newest message sent from this number, or
  says it has none.

## S20 — Mark a chat as read

- **IT:** Segna come letta la chat con <contatto>
- **EN:** Mark my chat with <contatto> as read.
- **Budget:** 1
- **Expected calls:** `mark_chat_read(chat="<contatto>")`
- **Pass when:** the answer reports how many messages were marked; 0 is a correct answer for a chat with nothing
  received. Runs on WhatsApp Web (Baileys) instances only.

## S21 — Someone's phone number

- **IT:** Qual è il numero di <nome>?
- **EN:** What is <nome>'s phone number?
- **Budget:** 1
- **Expected calls:** `find_chats(query="<nome>")`
- **Pass when:** the answer gives the `phone` of the matching person, or lists the candidates when the name matches
  several people.

## Manual-run protocol

Hosts: **omp** (`.omp/mcp.json`), **Claude Code** and **Codex CLI**, each configured with the local server against
the dev instance (the placeholders in the README's host examples, real values from the environment).

1. Start a new session in the host for every scenario. Setup scenarios run in the same session first, so the later
   request can say "that message".
2. Give the agent only the Italian request text. Do not name tools, parameters or this catalog.
3. Count the MCP tool calls of the server (`evolution-api-mcp`) from the request to the final answer, failed calls
   included. Note the wall time from sending the request to the final answer, in seconds.
4. Judge *Pass when* for the scenario. A scenario passes in a host when the answer is correct, the call count is
   within budget and no call failed because of a wrong guess.
5. Log one row per run in [RUNS.md](RUNS.md): Date, Host, Model, Scenario, Calls used, Budget, Wall time (s), Result,
   Fix commit. Never record contact names, numbers, chat ids or message text, in the log or in a commit message.
6. **A scenario fails** when it goes over budget in at least 2 of the 3 hosts. Fix the cause: the tool descriptions,
   the server `instructions`, or the tool's behaviour. Then rerun that scenario on all three hosts and log the fix
   commit in the new rows.
7. Write `src/evolution_api_mcp/assets/recipes.md` entries only for scenarios that passed, dated the day they
   passed, in the format that file documents (question, trap, calls, verified).

## Index criterion

Searches are live scans with filters pushed to Evolution; no local index exists. A local index is a separate
iteration that only this measurement opens:

- the median wall time of S07, S09, S17 or S18 across the logged runs exceeds 5 seconds, or
- one of S07, S09, S17 or S18 goes over budget in at least 2 of the 3 hosts.

When either holds, write `INDEX-NEEDED: <scenario ids>` on the first line of [RUNS.md](RUNS.md). Nothing else in
this catalog changes.
