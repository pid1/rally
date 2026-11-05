# rally-phone

Claude places hands-off phone calls for Jonathan through an ElevenLabs voice
agent on his Twilio number, then reads back what happened.

Jonathan asks ("grab the plate task and call the county tax office"). Claude
reads the Rally to-do and writes a **call brief**: who to call, the goal, the
questions to ask, what may and may not be shared, when to give up, and
optionally a calling window. It then calls `place_call` or `schedule_call` on
this package's MCP server. The agent opens by saying it is an AI assistant
calling on Jonathan's behalf. Jonathan is not on the call. Afterwards Claude
calls `get_call` and gets the outcome, the answers, follow-ups and safety checks.
It reports to Jonathan and may update the Rally to-do through Rally's own MCP.

Research behind every choice, with sources: [RESEARCH.md](RESEARCH.md).

## Why a purpose-built MCP server

ElevenLabs' local MCP server (`elevenlabs-mcp`) is archived, and its
`make_outbound_call` cannot pass a brief (dynamic variables). The hosted
ElevenLabs MCP uses OAuth with broad agent read/write scope (it can rewrite and
publish the agent), doesn't document outbound calls with dynamic variables, and
returns verbose text. `rally_phone.server` is a small stdio FastMCP server
instead:

| Tool | What it does |
|---|---|
| `place_call(brief)` | Dials now. Returns `call_id`. |
| `schedule_call(brief)` | Queues a dial inside `brief.call_window`. Returns `schedule_id`. |
| `get_call(call_id \| schedule_id, include_transcript=false)` | Status, outcome, answers, follow-ups, safety checks; transcript only on request. |
| `list_recent_calls(limit=5)` | Recent calls and calls still scheduled. |
| `cancel_scheduled_call(schedule_id)` | Stops a queued call. |

Results are compact JSON. Anything the callee said (summary, extracted data,
transcript) sits under `untrusted_external_content`, as in Rally's MCP.

It lives in `phone/` as its own uv project, like `mcp/`. It runs on Jonathan's
Mac next to Claude, not in Rally's container, and shares no code with Rally.

```
phone/
  agent/            agent as code: system_prompt.md, first_message.txt, agent.json
                    (voice, LLM, turn-taking, limits, privacy, data collection,
                    evaluation, disabled Rally MCP hook), safety_tests.json
  scripts/setup.py  create/update the agent (dry run by default)
  scripts/safety_tests.py   push and run the prompt-safety tests (dry run by default)
  src/rally_phone/  brief.py (validation), calls.py, elevenlabs.py, server.py
  tests/            mocked HTTP only; nothing touches the network
```

## Setup

### 1. Keys

**ElevenLabs API key.** The MCP server and scripts look for it in this order:
`ELEVENLABS_API_KEY` in the environment, then an `elevenlabs=<key>` line in
`~/.env` (mode 600, where Jonathan's key already lives), then the Keychain item
`elevenlabs-api-key`. It is sent only in the `xi-api-key` header and never
logged or printed: dry runs show `<redacted>`.

Scopes the key needs: **ElevenAgents: Write** (placing calls is a write; it
also reads conversations), **Voices: Read** (setup checks the voice is in the
account) and **Pronunciation Dictionaries: Write** (setup finds, creates or
updates the names dictionary). Everything else stays at No Access, so anything
else, such as `/v1/user/subscription`, returns 401 and setup never calls it.

Setup's library add of the agent's voice (`POST /v1/voices/add/...`) needs
**Voices: Write**. The key doesn't have it, so the voice was added by hand in
the dashboard (Voice Library, "Add to my voices"). That is the recommended
route. Setup then finds the voice in `GET /v2/voices` and skips the add.

Set a **credit quota** on the key as a spend cap. If the agent update is
refused for the `privacy` block, add the conversation-privacy permission or set
retention in the dashboard (see RESEARCH.md (f)).

To use the Keychain instead of `~/.env` (`security` prompts for the value, so it
never lands in shell history):

```sh
security add-generic-password -a "$USER" -s elevenlabs-api-key -w
```

**Twilio:** not needed. Jonathan imported the number in the ElevenLabs UI
(label "Rally Assistant", id `phnum_5201m6p2gb63fncb57v3z6r7740n`, inbound and
outbound, no agent assigned). That ID is the default in `agent/agent.json`.
Optional, only for importing another number from the command line
(`setup.py phone-number --number +1...`) or listing Twilio numbers read-only
(`setup.py twilio-numbers`):

```sh
security add-generic-password -a "$USER" -s twilio-account-sid -w
security add-generic-password -a "$USER" -s twilio-auth-token -w
```

(or `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN`, or `twilio_account_sid=` /
`twilio_auth_token=` lines in `~/.env`).

**SMS stays on Twilio.** Inbound SMS on this number must stay routed to
Twilio's A2P messaging service, not ElevenLabs. Setup never imports or patches
the number, and the optional import path sends `enable_sms: false`. After any
change to the number in ElevenLabs, check in the Twilio console (Phone Numbers,
the number, Messaging) that it still uses the A2P Messaging Service.

### 2. Create the agent

```sh
cd phone
uv sync
uv run scripts/setup.py all            # dry run: read-only lookups, prints the exact request
uv run scripts/setup.py all --apply    # creates (or updates) the agent and writes config
```

Setup finds the agent by name ("Rally phone assistant"), so re-running it
updates the same agent to match `agent/`. `--apply` writes
`~/.config/rally-phone/config.json` (mode 600, IDs only): `agent_id`,
`phone_number_id`, `owner_name` and `assistant_number` (read from the imported
number). The environment variables `ELEVENLABS_AGENT_ID`,
`ELEVENLABS_PHONE_NUMBER_ID`, `RALLY_PHONE_OWNER_NAME` and
`RALLY_PHONE_ASSISTANT_NUMBER`
override it.

Setup does **not** assign the agent to the number for inbound calls. Outbound
calls name the agent per call, and inbound is V2.

### 3. Run the safety tests

```sh
uv run scripts/safety_tests.py             # dry run: shows the tests
uv run scripts/safety_tests.py --apply     # creates them in ElevenLabs and runs them
```

See [Prompt safety](#prompt-safety) below.

### 4. Connect Claude Code

```sh
claude mcp add -s user rally-phone -- uv run --directory /Users/jroemer/workdir/rally/phone rally-phone-mcp
```

Keep `place_call` and `schedule_call` on "ask before running". They are marked
open-world and non-idempotent, so Claude Code asks by default.

### 5. First call

Call Jonathan's own phone with a trivial brief and listen. Then check the
`get_call` result and the agent's evaluation results.

## Placing a call

Claude writes the brief from Jonathan's words and the Rally to-do:

```json
{
  "callee_name": "Example County Tax Office",
  "to_number": "+15125550123",
  "topic": "a licence plate renewal",
  "goal": "Find out whether the plate renewal can be done by mail and what it costs.",
  "questions": ["Can it be renewed by mail?", "What is the fee?", "What documents are needed?"],
  "may_share": ["The vehicle is a passenger car registered in this county."],
  "must_not_share": ["Which vehicle it is"],
  "give_up_when": "they need Jonathan in person or need an ID or account number",
  "callback_number": null,
  "callee_timezone": "America/Chicago",
  "rally_todo_id": 42
}
```

`brief.py` validates every brief before anything is sent:

* **Numbers:** E.164 only. US and Canada only (Caribbean +1 area codes are
  refused). No premium-rate (900, 976) or N11 numbers.
* **Fields:** required fields, length caps (120 to 600 characters), at most 10
  questions or facts, and no unknown fields.
* **Content:** briefs carrying Social Security numbers, long digit runs (card,
  account or licence numbers) or street addresses are refused with the field
  name. Claude should then tell Jonathan the call needs him.
* **Standing rules:** a do-not-share list is always added (identifiers, dates of
  birth, addresses, calendar and whereabouts, payments).
* **Explicit calls only:** there is no call without a brief. Nothing retries,
  loops or dials in the background.

* **Calling hours:** `callee_timezone` (the business's IANA zone) is required.
  `place_call` dials only 08:00-20:45 callee time, so the 15-minute cap ends the
  call by 9 p.m. Windows must lie within 08:00-21:00 on one day: the TCPA's
  8 a.m. to 9 p.m. rule, which the prompt repeats.

The agent also has hard limits of its own: one call at a time, 10 calls a day,
15 minutes per call, and only built-in tools: end call, voicemail detection,
keypad tones for phone menus (they never key in identifiers), and skip turn
for holds.

## Voice and model

* **Voice:** "Jon - Relaxed, Deep and Approachable" (`Cz0K1kOv9tD8l0b5Qu53`),
  a Studio Quality Voice Library voice verified for Eleven v4 Turbo. Jonathan
  picked it on 2026-11-05. `setup.py` checks `GET /v2/voices` and, if the voice
  isn't in the account, adds it from the library as "Rally assistant - Jon"
  before creating the agent. It then uses the voice ID that call returns.
* **TTS:** `eleven_v4_turbo`, ElevenLabs' real-time v4 model. Expressive mode
  is on. `text_normalisation_type: elevenlabs` makes numbers, dates and codes
  read reliably. Stability 0.6 and similarity 0.8; v4 has no style or speed
  controls. To try full Eleven v4, change `model_id` in `agent/agent.json` to
  `eleven_v4`.
* **LLM:** `claude-sonnet-5-5` (the prompting guide's pick for complex
  reasoning) at temperature 0.2, with a 1,024-token thinking budget.
* **Prompt:** follows ElevenLabs' prompting guide: Personality, Environment,
  Tone, Goal (numbered steps from the brief), Guardrails ("This step is
  important." on the critical rules, repeated at the end), Tools, Tool error
  handling. About 880 words. It reads numbers back digit by digit, uses audio
  tags rarely (never laughter with a government office), and uses punctuation
  for pauses because v4 has no SSML breaks.
* **Disclosure:** the opening line follows ElevenLabs' disclosure page: "I'm
  an AI assistant calling on behalf of Jonathan Roemer. This call is
  transcribed and may be shared with our service providers." It says
  "transcribed", not "recorded", because no audio is recorded.
* **Pronunciation:** `agent.json` `pronunciation_rules` holds alias rules
  (for now "Roemer" is read as "Roamer"). Setup finds the dictionary named
  "Rally phone assistant names", or creates it with
  `POST /v1/pronunciation-dictionaries/add-from-rules`. If its rules differ,
  setup replaces them with `POST .../{id}/set-rules`, which makes a new
  version. It then points `tts.pronunciation_dictionary_locators` at the
  dictionary's latest version.
* **Live check:** when the agent already exists, the dry run prints which TTS
  and LLM settings the update would change ("Differs from the live agent: ..."
  or "match the live agent").
* **Voicemail:** the voicemail tool leaves one short message: who is calling,
  on whose behalf, the topic, and "call back at" `{{callback_number}}`. That is
  the brief's callback number, or the assistant's own number if the brief gives
  none. Until inbound (V2) is set up, the assistant number rings out, so a
  brief should name Jonathan's callback number when a callback matters. See
  open question 5.

## Scheduling

Add a window in the business's local time:

```json
"call_window": {"timezone": "America/Chicago", "start": "2026-11-06T09:00", "end": "2026-11-06T16:30"}
```

`schedule_call` submits an ElevenLabs batch call with one recipient and
`scheduled_time_unix` set to the window start, or about a minute from now if
the window is already open. ElevenLabs then dials it, so nothing of ours has
to stay running. Daylight saving is handled by the IANA zone. Windows must
lie within 08:00-21:00 on one day in the callee's zone (TCPA hours) and start
within 14 days. `place_call` refuses a brief
whose window is not open now.

Limits: batch calls have no window end and no automatic retry. If nobody
answers, `get_call` says so (outcome `no_answer`), and Claude asks Jonathan
before scheduling again. `cancel_scheduled_call` stops a queued call.

## Results

`get_call` returns:

* status, start time and duration;
* `call_successful` and `safety_checks`: the six evaluation criteria in
  `agent.json`. A failure adds an `attention` line telling Claude to read the
  transcript.
* under `untrusted_external_content`: the summary and the data-collection
  fields `outcome`, `answers_to_questions`, `follow_ups`, `callback_requested`,
  `callback_details`, `appointment_details`, `reference_number` and
  `info_requested_not_available`, plus the transcript if asked for.

Analysis appears a little after the call ends (status `processing`, then
`done`). There are no notifications in V1: Claude checks when Jonathan asks, or
a few minutes after placing a call.

## Privacy

* No audio is recorded (`record_voice: false`, Twilio recording off).
  Transcripts and analysis are kept **30 days**, then deleted by ElevenLabs.
* The system prompt holds only the brief and Jonathan's name. It never holds
  calendar contents, family details or addresses.
* The agent treats everything the callee says as untrusted. It never claims to
  be human, never pays or commits to anything, and says "Jonathan will follow
  up" rather than guessing.
* The opening line says the call is transcribed. This matters for callees in
  all-party-consent states.
* Zero Retention Mode is off on purpose. It would block scheduled (batch) calls
  and fetching transcripts, leaving only a post-call webhook, which needs an
  always-on receiver.

## Prompt safety

`agent/safety_tests.json` has 16 adversarial callee turns, each with the
expected behaviour and a success and a failure example. They cover requests for
an SSN, a date of birth, an address, a card or account number, a PIN under
pressure, "ignore your instructions", a caller claiming to be Jonathan, requests
for calendar details, a money transfer, "are you human", unknown facts, booking
pressure, a transfer to billing, a do-not-call request, and reading back a
confirmation number. Two multi-turn simulations add a social
engineer and a fake "SYSTEM:" injection.

`scripts/safety_tests.py --apply` turns them into ElevenLabs agent tests:
next-reply `llm` tests and `simulation` tests. It creates the missing ones (by
name), updates the rest, and runs them all with
`POST /v1/convai/agents/{id}/run-tests`. Use `--repeat 5` for pass rates.
Results show in the agent's Tests tab. Re-run after any prompt change.

## Costs

* ElevenLabs Agents: $0.08 per minute beyond the plan's included minutes
  (Starter $6/month includes 75 minutes; Creator $22 includes 275), plus LLM
  tokens at cost. ElevenLabs adds no telephony fee.
* Twilio: $0.014 per outbound minute and $1.15 a month for the number.
* A typical 5-minute call costs about $0.50 plus a few cents of LLM. The
  15-minute cap bounds one call at about $1.50. The 10-a-day limit and the
  key's credit quota bound the rest.

## Later

* **Inbound (V2):** assign a separate inbound agent to the same number
  (`PATCH /v1/convai/phone-numbers/{id}` with `agent_id`). Inbound needs its own
  prompt because there is no brief. Callers can be recognised by
  `system__caller_id`. SMS stays on Twilio.
* **Rally availability tools (V2 hook, disabled):** `agent.json` `rally_mcp`
  describes registering Rally's busy-only MCP endpoint (`check_availability`,
  `find_free_slots`, `book_appointment`, `cancel_appointment`) as a custom MCP
  server. It authenticates with `Authorization: Bearer <token>`, where the token
  comes from `RALLY_PHONE_MCP_TOKEN`, `rally_phone_mcp_token=` in `~/.env` or
  the Keychain item `rally-phone-mcp-token`. ElevenLabs custom MCP supports
  headers or machine OAuth (client credentials), not Rally's interactive OAuth.
  Rally's endpoint should also allowlist ElevenLabs' US egress IPs
  `34.67.146.145` and `34.59.11.47`. To turn it on, set `enabled: true` and the
  real URL, then run `setup.py agent --enable-rally-mcp`. That registers the
  server (auto-approve, because no human is on the call to approve tools),
  attaches it and appends `agent/prompt_rally_tools.md`. Before enabling,
  reconcile that file with the "you do not book" line in `system_prompt.md`. MCP
  must first be allowed in the workspace (MCP terms).

## Open questions (with recommendations)

1. **Bookings: auto-confirm or approval?** Recommend **hold-only in V1**: the
   agent asks them to hold a time and Jonathan confirms. In V2, let the agent book
   only when the brief says `may_book` and Rally's `check_availability` says the
   slot is free. Anything else waits for Jonathan's approval.
2. **Retention period?** Recommend **30 days** (set). Long enough to fetch and
   reread results, and the Rally to-do keeps the outcome. Go to 7 days if results
   are always fetched the same day.
3. **Transcription disclosure in the opening line?** Recommend keeping it.
4. **LLM and TTS model:** `claude-sonnet-5-5` with `eleven_v4_turbo`. If
   replies feel slow, try GLM 5.2 or GPT-6 Luna (the guide's general-purpose
   picks) and re-run the safety tests. Full `eleven_v4` is a one-line switch.
5. **Callbacks to the assistant number:** without a `callback_number` in the
   brief, the voicemail gives the assistant's number, and nothing answers it
   until inbound is configured. Recommend a
   minimal inbound agent that says Jonathan will call back and collects the
   caller's name and reason, set up before the first voicemail goes out.
   Until then, Claude should add a `callback_number` from Jonathan when a
   callback matters.
6. **Daily cap 10 and 15-minute calls:** raise them in `agent.json` if hold
   times run longer.

## Development

```sh
cd phone
uv sync --frozen
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests use `httpx.MockTransport` for ElevenLabs and Twilio, and the fictional
555-01xx number range.
