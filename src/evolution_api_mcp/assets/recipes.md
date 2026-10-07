# Evolution API Assistant recipes

Tested call sequences for common WhatsApp tasks: what is new, who wrote, finding attachments, replying, forwarding and
exporting. Each recipe names the question it answers, the wasteful path an agent takes without it, the calls that
answer it, and the date it was verified against a live instance.

Chat parameters take a phone number, a chat_id or a contact or group name, so a recipe passes the name the user
gave. A name shared by several chats is refused with their chat_ids; pass one of them to continue.

## "Cosa mi è arrivato oggi?"
**Trap:** Asking get_instance_status for the time zone, then list_chats and one read_messages per chat.
**Calls (1):** `list_recent_messages(since="<today>T00:00:00", direction="incoming")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Quali chat aspettano una mia risposta?"
**Trap:** Listing the chats, then opening each one with get_chat or get_message to see who spoke last.
**Calls (1):** `list_chats(waiting_for_reply=true)`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Fammi vedere i messaggi non letti"
**Trap:** list_chats for the unread counts, then read_messages for every chat that has some.
**Calls (1):** `list_recent_messages(only_unread=true)`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Leggi la conversazione con <contatto>"
**Trap:** Looking the contact up with find_chats before reading.
**Calls (1):** `read_messages(chat="<contact>")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Chi è <id>@lid?"
**Trap:** Adding get_contact_profile after get_chat; get_chat already returns the name and, when known, the phone number behind the @lid.
**Calls (1):** `get_chat(chat="<id>@lid")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "In quali gruppi siamo sia io sia <contatto>?"
**Trap:** Walking list_groups and checking participants; get_chat lists groups_in_common, and an empty list means no group has both.
**Calls (1):** `get_chat(chat="<contact>")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Trova il PDF che mi ha mandato <contatto>"
**Trap:** Looking the contact up with find_chats first; search_messages takes the name and filters by type and direction.
**Calls (1):** `search_messages(chat="<contact>", message_type="document", direction="incoming")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Scarica quel PDF"
**Trap:** Downloading through export_chat or searching again; the message id from the earlier search is enough.
**Calls (1):** `download_message_media(message_id="<id>")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Cerca "fattura" nel gruppo <gruppo>"
**Trap:** Reading the chat page by page and filtering by hand; Evolution has no text search, so search_messages scans for you.
**Calls (1):** `search_messages(chat="<chat>", query="fattura")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Mostrami il contesto di quel messaggio"
**Trap:** Paging read_messages back until the hit shows up.
**Calls (1):** `read_messages(chat="<chat>", around_message_id="<id>")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Rispondi a quel messaggio: "ok, grazie""
**Trap:** Looking the chat up, then sending and polling get_message_status; the send result already carries the status and the reply needs no chat when reply_to_message_id is given.
**Calls (1):** `send_text_message(reply_to_message_id="<id>", text="ok, grazie")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Inoltra quella foto a <contatto>"
**Trap:** Downloading the photo and sending it again by hand.
**Calls (1):** `forward_message(message_id="<id>", to=["<contact>"])`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Manda questi 3 file a <contatto>"
**Trap:** Three send_media_message calls with URLs, which local files cannot use.
**Calls (1):** `send_local_files(paths=["<file1>", "<file2>", "<file3>"], chat="<contact>")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Esporta la chat con <contatto> di settembre con gli allegati"
**Trap:** Looking up the chat and the time zone first, then downloading attachments one by one.
**Calls (1):** `export_chat(chat="<contact>", since="<year>-09-01T00:00:00", until="<year>-10-01T00:00:00")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Scarica tutti i documenti della chat <gruppo>"
**Trap:** One download_message_media call per document (30 changes per minute apply) after a search.
**Calls (1):** `export_chat(chat="<chat>", content="media", media_types=["document"])`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Gli ultimi link che mi hanno mandato"
**Trap:** Reading every chat and scanning the text for web addresses.
**Calls (1):** `search_messages(message_type="link", direction="incoming")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Chi ha reagito al mio ultimo messaggio a <contatto>?"
**Trap:** Calling get_message on each recent message; read_messages lists reactions on the message, and a message without reactions has no reactions key.
**Calls (1):** `read_messages(chat="<contact>", limit=20)`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Segna come letta la chat con <contatto>"
**Trap:** Looking the contact up with find_chats first.
**Calls (1):** `mark_chat_read(chat="<contact>")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.

## "Qual è il numero di <nome>?"
**Trap:** Opening get_chat or get_contact_profile for each candidate; find_chats returns the phone of each match.
**Calls (1):** `find_chats(query="<name>")`
**Verified:** 2026-10-03 on a WhatsApp Web (Baileys) instance.
