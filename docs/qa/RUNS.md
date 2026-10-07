INDEX-NEEDED: S07, S09, S17, S18
# Manual scenario runs

One row per run of a scenario from [SCENARIOS.md](SCENARIOS.md) in a real agent, following the protocol there.

## Rules

- **Never record** contact or group names, phone numbers, chat ids, message ids or message text: not in a cell, not in
  a note, not in a commit message. A scenario id is the only reference to what was asked.
- One row per scenario per host per run. A rerun after a fix is a new row; the old row stays.
- **Date** is `YYYY-MM-DD`. **Host** is `omp`, `Claude Code` or `Codex CLI`. **Model** is the model the host used.
- **Calls used** counts every MCP tool call of this server from the request to the final answer, failed ones
  included. **Budget** is the scenario's budget. **Wall time (s)** runs from sending the request to the final answer.
- **Result** is `pass` (correct answer, within budget, no call failed because of a wrong guess) or `fail` followed by
  a few neutral words on the cause (for example `fail: over budget, looked the chat up first`).
- **Fix commit** is the short hash of the commit that fixed a failed scenario; empty on a pass. `uncommitted` means
  the fix is in the working tree and not yet committed.
- A scenario fails when it goes over budget in at least 2 of the 3 hosts. Fix it and rerun it on all three hosts.
- If the median wall time of S07, S09, S17 or S18 exceeds 5 s, or one of them goes over budget in at least 2 hosts,
  the first line of this file becomes `INDEX-NEEDED: <scenario ids>`. It stays absent otherwise.

## About the 2026-10-03 run

- **Hosts.** omp 18.4 and Claude Code 2.1, both on claude-opus-5-5, each started fresh per scenario (chained
  scenarios share one session) with the local server from this checkout against the dev instance. Codex CLI is not
  installed on this machine, so there are no Codex rows (last row).
- **Two hosts instead of three.** With two hosts, a scenario fails when it is over budget on **both** of them, and a
  row marked `fail` on one host only does not fail the scenario. Final verdict per scenario (latest rows):
  S01-S15 and S18-S21 pass; S16 and S17 fail (see below).
- **One test chat.** The instance has no group and one test contact, so `<gruppo>` and `<contatto>` are that
  contact. The first S17 attempt worded the request with "gruppo" and the agents searched for a group that cannot
  exist, so those two rows are `skipped`; S17 was rerun as "nella chat con ...". S12 (forward) can only target the
  chat the photo already sits in, which Claude Code answers with a question; omp forwarded.
- **Data limits.** Attachments older than a few days are no longer served by WhatsApp ("Failed to fetch stream"), so the
  first S08 rows are `skipped`; S08 passed on a fresh file sent by S13. The instance holds no mention, so S16 can
  only confirm "none". S14 asked for a month with messages (June), since September holds none.
- **Fixes** (all in the working tree, uncommitted): routing facts in the server instructions and in the descriptions of
  `get_chat`, `read_messages`, `search_messages`, `get_message_status`, `download_message_media` and `export_chat`;
  `export_chat` fetches attachments four at a time, starts none after 90 s and no longer returns Evolution's signed
  download address in `media_skipped` (S14, S15).
- **Unresolved.** S16: at 00:07-00:22 the day "today" was minutes old, and both agents also checked the day before
  (2 calls). S17: both agents view every photo after the search (4 calls); the search result alone already answers the
  request.
- **Index criterion** (S07, S09, S17, S18). Median wall time of the logged runs: S07 13.6 s, S09 13.2 s, S17 17.3 s,
  S18 8.4 s, so all four exceed 5 s, and S07 (first runs) and S17 went over budget on both hosts. The wall time is
  dominated by the model's own latency (a 1-call scenario takes 7-14 s); the search call itself took a median of
  0.45 s (S07), 0.53 s (S17) and 1.1 s (S18) in these runs, and 1.7 s for S09 measured directly over 1300 messages.

## Runs

| Date | Host | Model | Scenario | Calls used | Budget | Wall time (s) | Result | Fix commit |
|---|---|---|---|---|---|---|---|---|
| 2026-10-02 | omp | claude-opus-5-5 | S01 | 2 | 1 | 7.9 | fail: over budget, checked the server time zone first |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S01 | 1 | 1 | 6.6 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S02 | 11 | 1 | 26.0 | fail: over budget, opened each listed chat again |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S02 | 1 | 1 | 10.6 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S03 | 1 | 1 | 11.6 | pass |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S03 | 1 | 1 | 10.2 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S04 | 1 | 1 | 14.8 | pass |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S04 | 2 | 1 | 13.1 | fail: over budget, looked the chat up first |  |
| 2026-10-02 | omp | claude-opus-5-5 | S05 | 1 | 1 | 15.1 | pass |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S05 | 2 | 1 | 11.8 | fail: over budget, added a profile lookup |  |
| 2026-10-02 | omp | claude-opus-5-5 | S06 | 3 | 1 | 13.4 | fail: over budget, cross-checked with the group list |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S06 | 3 | 1 | 10.6 | fail: over budget, never asked the chat tool, listed groups first |  |
| 2026-10-02 | omp | claude-opus-5-5 | S07 | 2 | 1 | 18.3 | fail: over budget, looked the contact up first |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S07 | 2 | 1 | 13.2 | fail: over budget, looked the contact up first |  |
| 2026-10-02 | omp | claude-opus-5-5 | S08 | 2 | 1 | 32.8 | skipped: no data (the old attachment is no longer served by WhatsApp) |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S08 | 1 | 1 | 15.0 | skipped: no data (the old attachment is no longer served by WhatsApp) |  |
| 2026-10-02 | omp | claude-opus-5-5 | S09 | 1 | 1 | 13.2 | pass |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S09 | 1 | 1 | 24.7 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S10 | 1 | 1 | 13.7 | pass |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S10 | 1 | 1 | 17.8 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S11 | 2 | 1 | 14.8 | fail: over budget, checked delivery status after sending |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S11 | 0 | 1 | 5.5 | fail: sent nothing, asked which message (the hit was an own message) |  |
| 2026-10-02 | omp | claude-opus-5-5 | S09 | 2 | 1 | 13.1 | fail: over budget, re-read the hit with a second call |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S09 | 1 | 1 | 9.6 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S10 | 1 | 1 | 13.3 | pass |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S10 | 1 | 1 | 11.1 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S11 | 2 | 1 | 13.0 | fail: over budget, checked delivery status after sending |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S11 | 1 | 1 | 9.1 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S13 | 4 | 1 | 22.9 | fail: over budget, checked the status of each file |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S13 | 1 | 1 | 17.4 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S08 | 1 | 1 | 7.3 | pass |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S08 | 1 | 1 | 6.1 | pass |  |
| 2026-10-02 | omp | claude-opus-5-5 | S12 | 1 | 1 | 14.1 | pass |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S12 | 0 | 1 | 3.9 | fail: sent nothing, asked first (the photo sits in the target chat already) |  |
| 2026-10-02 | omp | claude-opus-5-5 | S14 | 5 | 1 | 81.9 | fail: over budget, looked up the chat and the zone, then retried a download |  |
| 2026-10-02 | Claude Code | claude-opus-5-5 | S14 | 3 | 1 | 57.3 | fail: over budget, looked up the chat and the zone |  |
| 2026-10-03 | omp | claude-opus-5-5 | S15 | 63 | 1 | 252.4 | fail: over budget, export outlasted the host timeout, then one download per file |  |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S15 | 3 | 1 | 219.6 | fail: over budget, looked the chat up and searched after a 199 s export |  |
| 2026-10-03 | omp | claude-opus-5-5 | S16 | 3 | 1 | 14.7 | fail: over budget, looked up the zone and checked the day before |  |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S16 | 2 | 1 | 10.1 | fail: over budget, checked the day before |  |
| 2026-10-03 | omp | claude-opus-5-5 | S17 | 11 | 1 | 27.3 | skipped: no data (the request named a group; the instance has none and the agent searched for one) |  |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S17 | 6 | 1 | 17.3 | skipped: no data (the request named a group; the instance has none and the agent searched for one) |  |
| 2026-10-03 | omp | claude-opus-5-5 | S18 | 1 | 1 | 8.6 | pass |  |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S18 | 1 | 1 | 8.2 | pass |  |
| 2026-10-03 | omp | claude-opus-5-5 | S19 | 4 | 1 | 18.3 | fail: over budget, searched and re-read the message to confirm no reactions |  |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S19 | 2 | 1 | 8.3 | fail: over budget, re-read the message to confirm no reactions |  |
| 2026-10-03 | omp | claude-opus-5-5 | S17 | 9 | 1 | 31.0 | fail: over budget, looked up the chat and the zone, then viewed and saved the photos |  |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S17 | 4 | 1 | 15.0 | fail: over budget, viewed each photo |  |
| 2026-10-03 | omp | claude-opus-5-5 | S12 | 2 | 1 | 17.6 | fail: over budget, checked delivery status after forwarding |  |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S12 | 0 | 1 | 4.2 | fail: sent nothing, asked which of three matching photos |  |
| 2026-10-03 | omp | claude-opus-5-5 | S20 | 2 | 1 | 21.9 | fail: over budget, looked the chat up first |  |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S20 | 1 | 1 | 5.1 | pass |  |
| 2026-10-03 | omp | claude-opus-5-5 | S21 | 1 | 1 | 7.6 | pass |  |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S21 | 1 | 1 | 7.3 | pass |  |
| 2026-10-03 | omp | claude-opus-5-5 | S06 | 5 | 1 | 19.9 | fail: over budget, cross-checked with the group list | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S06 | 4 | 1 | 17.4 | fail: over budget, looked the contact up and the group list was rate-limited | uncommitted |
| 2026-10-03 | omp | claude-opus-5-5 | S07 | 1 | 1 | 13.9 | pass | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S07 | 2 | 1 | 12.8 | fail: over budget, looked the contact up first | uncommitted |
| 2026-10-03 | omp | claude-opus-5-5 | S14 | 1 | 1 | 23.1 | pass | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S14 | 2 | 1 | 21.8 | fail: over budget, looked the chat up first | uncommitted |
| 2026-10-03 | omp | claude-opus-5-5 | S15 | 5 | 1 | 99.5 | fail: over budget, followed the export with a search and downloads | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S15 | 47 | 1 | 222.4 | fail: over budget, one download per file instead of the export | uncommitted |
| 2026-10-03 | omp | claude-opus-5-5 | S16 | 2 | 1 | 12.3 | fail: over budget, checked the day before (the day was 22 minutes old) | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S16 | 2 | 1 | 9.9 | fail: over budget, checked the day before (the day was 22 minutes old) | uncommitted |
| 2026-10-03 | omp | claude-opus-5-5 | S17 | 6 | 1 | 30.4 | fail: over budget, viewed and saved the photos | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S17 | 4 | 1 | 16.9 | fail: over budget, viewed each photo | uncommitted |
| 2026-10-03 | omp | claude-opus-5-5 | S19 | 2 | 1 | 14.2 | fail: over budget, re-read the message to confirm no reactions | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S19 | 1 | 1 | 7.0 | pass | uncommitted |
| 2026-10-03 | omp | claude-opus-5-5 | S06 | 1 | 1 | 7.9 | pass | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S06 | 4 | 1 | 13.2 | fail: over budget, looked the contact up and the group list was rate-limited | uncommitted |
| 2026-10-03 | omp | claude-opus-5-5 | S15 | 3 | 1 | 86.4 | fail: over budget, retried one download after the export | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S15 | 1 | 1 | 63.1 | pass | uncommitted |
| 2026-10-03 | omp | claude-opus-5-5 | S17 | 4 | 1 | 17.7 | fail: over budget, viewed each photo | uncommitted |
| 2026-10-03 | Claude Code | claude-opus-5-5 | S17 | 4 | 1 | 14.2 | fail: over budget, viewed each photo | uncommitted |
| 2026-10-03 | Codex CLI | | all | | | | skipped: not run — codex not installed | |
