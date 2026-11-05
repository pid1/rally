# rally-phone research memo

All sources checked on **2026-11-05**. "SDK" means the generated
[elevenlabs-python](https://github.com/elevenlabs/elevenlabs-python) client on
`main` that day (its `raw_client.py` files name every endpoint and its `types/`
files every field). Read-only GETs with Jonathan's restricted key confirmed the
list response shapes marked "verified" below; no write call was made.

## (a) ElevenLabs Agents APIs

Base URL `https://api.elevenlabs.io`, auth header `xi-api-key`.

| Purpose | Endpoint | Notes |
|---|---|---|
| Create agent | `POST /v1/convai/agents/create` | body `conversation_config` (required), `platform_settings`, `name`, `tags`; returns `agent_id`. [docs](https://elevenlabs.io/docs/api-reference/agents/create) |
| Update / get / list agent | `PATCH` / `GET /v1/convai/agents/{id}`, `GET /v1/convai/agents?search=` | SDK `conversational_ai/agents/raw_client.py`. List returns `{agents, next_cursor, has_more}` (verified). |
| Dynamic variables | `{{name}}` in prompt and first message; values sent per call in `conversation_initiation_client_data.dynamic_variables`; strings, numbers, booleans only; placeholders at `conversation_config.agent.dynamic_variables.dynamic_variable_placeholders`; `system__*` reserved; `secret__*` never reach the LLM. | [docs](https://elevenlabs.io/docs/eleven-agents/customization/personalization/dynamic-variables) |
| Overrides | Off by default; each field enabled under `platform_settings.overrides.conversation_config_override` (e.g. `agent.first_message`, `agent.prompt.prompt`, `tts.voice_id`). Docs prefer dynamic variables. | [docs](https://elevenlabs.io/docs/eleven-agents/customization/personalization/overrides) |
| Outbound call (Twilio) | `POST /v1/convai/twilio/outbound-call` with `agent_id`, `agent_phone_number_id`, `to_number`, optional `conversation_initiation_client_data`, `call_recording_enabled`, `telephony_call_config` (`ringing_timeout_secs`, `twilio_call_recording_enabled`, `twilio_machine_detection`). Returns `success`, `message`, `conversation_id`, `callSid`. | [docs](https://elevenlabs.io/docs/api-reference/twilio/outbound-call) |
| Batch / scheduled calls | `POST /v1/convai/batch-calling/submit` with `call_name`, `agent_id`, `agent_phone_number_id`, `recipients[]` (each `phone_number` + `conversation_initiation_client_data`), **`scheduled_time_unix`**, `timezone`, `target_concurrency_limit`, `telephony_call_config`. Status `pending/in_progress/completed/failed/cancelled`. Also `GET /v1/convai/batch-calling/{id}` (recipients with `status` and `conversation_id`), `POST .../{id}/cancel`, `POST .../{id}/retry`, `GET /v1/convai/batch-calling/workspace` (returns `{batch_calls, next_doc, has_more}`, verified). | [submit](https://elevenlabs.io/docs/api-reference/batch-calling/create), [guide](https://elevenlabs.io/docs/eleven-agents/phone-numbers/batch-calls), SDK `batch_calls/raw_client.py` |
| Get conversation | `GET /v1/convai/conversations/{id}`: `status` (`initiated`, `in-progress`, `processing`, `done`, `failed`), `transcript[]` (`role`, `message`, `time_in_call_secs`), `metadata` (`start_time_unix_secs`, `call_duration_secs`, `termination_reason`, `phone_call.call_result`, `cost_fiat`), `analysis` (`call_successful`, `transcript_summary`, `data_collection_results{id: {value, rationale}}`, `evaluation_criteria_results{id: {result, rationale}}`). List: `GET /v1/convai/conversations?agent_id=&page_size=` returns `{conversations, next_cursor, has_more}` (verified). Delete: `DELETE /v1/convai/conversations/{id}`. | [docs](https://elevenlabs.io/docs/api-reference/conversations/get) |
| Data collection | `platform_settings.data_collection`: map of id to `{type: string/boolean/integer/number, description, enum?}`; 25 items per agent (50 on Trial/Enterprise). | [docs](https://elevenlabs.io/docs/eleven-agents/customization/agent-analysis/data-collection), SDK `types/analysis_property.py` |
| Evaluation criteria | `platform_settings.evaluation.criteria[]`: `{id, name, type: "prompt", conversation_goal_prompt, use_knowledge_base}`; result `success/failure/unknown` + rationale; 30 per agent. | [docs](https://elevenlabs.io/docs/eleven-agents/customization/agent-analysis/success-evaluation), SDK `types/prompt_evaluation_criteria.py` |
| Import Twilio number | `POST /v1/convai/phone-numbers` with `provider: "twilio"`, `phone_number`, `label`, `sid`, `token`, optional `agent_id`, `account_auth_token`, `region_config`, `enable_sms`. Returns `phone_number_id`. List `GET /v1/convai/phone-numbers` returns a bare list (verified, currently empty); update `PATCH /v1/convai/phone-numbers/{id}`. | [docs](https://elevenlabs.io/docs/api-reference/phone-numbers/create) |
| Agent tests | `POST /v1/convai/agent-testing/create` (`type`: `llm` next-reply, `tool`, `simulation`), `GET /v1/convai/agent-testing`, `POST /v1/convai/agents/{id}/run-tests` `{tests: [{test_id}], repeat_count}`. | [create](https://elevenlabs.io/docs/api-reference/tests/create), [guide](https://elevenlabs.io/docs/eleven-agents/customization/agent-testing) |

**Twilio credentials and webhooks.** The import needs the number, a label, a
SID and a token: either Account SID + Auth Token, or an API Key SID (`SK...`) +
secret, and ElevenLabs recommends a limited API key. With an API key,
`account_auth_token` is also needed for validating inbound webhook signatures.
ElevenLabs "automatically configures the Twilio phone number with the correct
settings", meaning it points the number's voice webhook at itself. Purchased
numbers do inbound and outbound; a Verified Caller ID does outbound only.
([native integration](https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/native-integration))
Jonathan imported the number in the ElevenLabs UI on 2026-11-05 (label "Rally
Assistant", inbound and outbound, no agent assigned; a read-only GET confirmed
it). Setup therefore defaults to that `phone_number_id` and needs no Twilio
credentials.

**Scheduling findings.** Batch calling has `scheduled_time_unix` but **no window
end, retry policy or business-hours rule**. Batches use the lower of 50% of
workspace concurrency or 70% of agent concurrency. **Zero Retention Mode cannot
be used with batch calls**, and batch calls fail unless `language`,
`first_message`, system prompt and `voice_id` are overridable on the agent.
([batch guide](https://elevenlabs.io/docs/eleven-agents/phone-numbers/batch-calls))

## (b) Custom MCP: OAuth or headers?

The dashboard docs describe only a **Secret Token** (an Authorization header
value) and **HTTP headers**, transports SSE and Streamable HTTP, and three
approval modes. ([MCP](https://elevenlabs.io/docs/eleven-agents/customization/tools/mcp))
The API (`McpServerConfigInput` in the SDK) also has `secret_token`,
`request_headers`, `request_meta`, and `auth_connection`, which references a
workspace **auth connection**. Auth connections can be `oauth2_client_credentials`,
`oauth2_jwt`, `private_key_jwt`, `bearer_auth`, `basic_auth`,
`custom_header_auth` or `mtls`.
([auth connections](https://elevenlabs.io/docs/api-reference/workspace/auth-connections/create))

**Answer:** machine-to-machine only (static headers, bearer, or OAuth2 client
credentials/JWT). There is **no documented interactive OAuth (authorization code
with user sign-in or dynamic client registration) for custom MCP servers.** So
ElevenLabs cannot use Rally MCP's Cloudflare Access Managed OAuth flow. Rally
needs a separate busy-only endpoint that accepts `Authorization: Bearer <token>`
(as the spec plans), plus the IP allowlist below. Also note that MCP is off
until a workspace member accepts the MCP terms (or sets `can_use_mcp_servers`),
and it is **not available with Zero Retention Mode or HIPAA**.

## (c) Egress IP ranges

Published and static. Changes are announced in advance in the changelog. They
cover all outbound traffic: MCP requests, webhooks and tools.
([IP allowlisting](https://elevenlabs.io/docs/eleven-api/resources/ip-allowlisting))

| Region | IPs |
|---|---|
| US (default) | `34.67.146.145`, `34.59.11.47` |
| EU | `35.204.38.71`, `34.147.113.54` |
| Asia | `35.185.187.110`, `35.247.157.189` |
| EU residency | `34.77.234.246`, `34.140.184.144` |
| India residency | `34.93.26.174`, `34.93.252.69` |
| Singapore residency | `34.87.23.17`, `34.126.179.103` |

For Rally's per-connection allowlist, use the two US IPs (the default
`api.elevenlabs.io` workspace). The page doesn't say which region an agent's
MCP calls leave from, so add EU and Asia too if a call is ever refused.

## (d) Plan tier and pricing

From [elevenlabs.io/pricing/agents](https://elevenlabs.io/pricing/agents):
telephony is included on every plan and ElevenLabs adds no telephony fee.
Twilio bills its own carrier charges directly. The LLM is billed at cost on top
of the minute rate, from credits.

| Plan | $/month | Agent minutes included | Concurrency |
|---|---|---|---|
| Free | 0 | 15 | 4 |
| Starter | 6 | 75 | 6 |
| Creator | 22 | 275 | 10 |
| Pro | 99 | 1,238 | 20 |
| Scale | 299 | 3,738 | 30 |
| Business | 990 | 12,375 | 40 |

Additional minutes cost **$0.08/min** on every plan; burst minutes (over the
concurrency limit) cost $0.16/min. **Recommendation:** Starter ($6) covers a
few calls a week and includes the commercial licence. Creator ($22) if usage
grows. Jonathan's current plan could not be read: the key can't call
`/v1/user/subscription`.

## (e) Official ElevenLabs MCP

* **`elevenlabs/elevenlabs-mcp` (local, stdio)** is **deprecated and archived**:
  "no longer actively maintained", pointing to the hosted server.
  ([repo](https://github.com/elevenlabs/elevenlabs-mcp)) Its tools include
  `create_agent`, `add_knowledge_base_to_agent`, `list_agents`, `get_agent`,
  `get_conversation`, `list_conversations`, `simulate_conversation`,
  `list_phone_numbers` and `make_outbound_call`, plus TTS, voice and music tools.
  `make_outbound_call(agent_id, agent_phone_number_id, to_number)` takes **no
  dynamic variables and no brief**. `get_conversation` returns the transcript
  and metadata as text. There is no batch or scheduled calling.
* **Hosted MCP `https://api.elevenlabs.io/v1/mcp`** uses OAuth with scopes for
  ElevenAgents read/write and Text to Speech. It can review conversations and
  read transcripts. Its tool list isn't published, and outbound calls with
  dynamic variables aren't documented. Agent updates "publish directly" to
  `main`. ([hosted MCP](https://elevenlabs.io/docs/eleven-agents/operate/hosted-mcp))

**Answer:** neither fits. The local server can't pass a brief and is archived.
The hosted server is broad (it can rewrite and publish the agent), its outbound
calls are undocumented, and its results aren't shaped for token efficiency.

## (f) API key permissions

Keys can be limited by scope, credit quota and IP allowlist (1 to 100 public
IPs or CIDRs). ([API keys](https://elevenlabs.io/docs/overview/administration/workspaces/api-keys))
The permission enum (SDK `types/permission_type.py`) includes `convai_read`,
`convai_write`, `voices_read`, `workspace_read`, `conversation_privacy_manage`
and more. The dashboard shows these as levels per product ("ElevenAgents:
Write", "Voices: Read").

* **Runtime (MCP server):** ElevenAgents **Write**. Placing calls and
  submitting batches are writes. Write appears to include read: the existing
  key read agents, numbers, conversations, batches and tests.
* **Setup:** ElevenAgents Write plus Voices Read (voice choice). This is
  Jonathan's current key.
* Add a **credit quota** on the key (for example a month's plan) as a spend cap.
  An IP allowlist suits a fixed IP only.
* Unverified: whether setting `privacy` on the agent needs
  `conversation_privacy_manage`. If the agent PATCH is refused for `privacy`,
  add that scope or set retention in the dashboard.

## (g) Retention and recording

`platform_settings.privacy`: `record_voice`, `retention_days` (default 2 years;
`-1` unlimited; `0` deletes on a schedule), `delete_audio`,
`delete_transcript_and_pii`, `apply_to_existing_conversations`,
`zero_retention_mode`, `conversation_history_redaction`. Retention applies
separately to transcripts and audio.
([retention](https://elevenlabs.io/docs/eleven-agents/customization/privacy/retention),
SDK `types/privacy_config_input.py`)
Per-agent Zero Retention Mode stores no audio and no PII-bearing transcripts.
Results then arrive only by post-call webhook, which needs an always-on
receiver. It is also incompatible with batch calls and MCP.
([ZRM](https://elevenlabs.io/docs/eleven-agents/customization/privacy/zrm))
Twilio-side recording is a separate per-call flag, off by default.

**Chosen:** `record_voice: false`, no Twilio recording, `retention_days: 30`
for transcripts and analysis, and no ZRM. Claude fetches results within days,
and the Rally to-do keeps the outcome.

## (h) Twilio side

Nothing beyond owning a voice-capable number, according to ElevenLabs: it
configures the number itself. Practical notes:

* Importing re-points the number's **voice webhook** to ElevenLabs. Pick a
  number nothing else answers. `setup.py twilio-numbers` shows each number's
  current webhook host (read-only).
* Not stated in the docs, from general Twilio knowledge (unverified): a Twilio
  **trial** account can only call verified numbers and plays a trial notice, so
  use an upgraded account. Voice Geographic Permissions allow US calls by
  default. Registering the number for SHAKEN/STIR / Voice Integrity in Trust
  Hub helps answer rates, but it is optional.
* Costs ([Twilio US voice pricing](https://www.twilio.com/en-us/voice/pricing/us)):
  outbound $0.0140/min, local number $1.15/month, inbound $0.0085/min, recording
  $0.0025/min (unused), answering machine detection $0.0075/call (unused).

**Typical call:** 5 minutes ≈ $0.40 ElevenLabs + $0.07 Twilio + LLM tokens
(a few cents, unverified). The agent's 15-minute cap bounds one call at about
$1.40 plus LLM.

## Inbound later (V2)

The same imported number can take inbound calls by setting its `agent_id`
(`PATCH /v1/convai/phone-numbers/{id}`). Use a **separate inbound agent**,
because the outbound prompt depends on a brief. Callers can be recognised with
`system__caller_id`, and per-caller context can come from
`platform_settings.workspace_overrides.conversation_initiation_client_data_webhook`.
That needs a reachable webhook, which is why it is deferred. An API-key Twilio
import also needs `account_auth_token` for inbound signature validation.

## (i) Revision 2026-11-05: Eleven v4, voice, LLM, prompt, tools

* **TTS model.** `eleven_v4_turbo` is "the real-time variant of Eleven v4" and
  turns on expressive mode automatically (`conversation_config.tts.expressive_mode`).
  Full `eleven_v4` is "our highest-quality model", meant for content.
  "Style and Speed sliders are not available in Eleven v4"; Stability and
  Similarity are, and "SSML is not supported".
  ([expressive mode](https://elevenlabs.io/docs/eleven-agents/customization/voice/expressive-mode),
  [Eleven v4](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/eleven-v4))
  The SDK's `TtsConversationalConfigInput` has `text_normalisation_type`
  (`system_prompt` or `elevenlabs`). The prompting guide calls `elevenlabs`
  more reliable, at a small latency cost.
* **Voice.** "Jon - Relaxed, Deep and Approachable" (`Cz0K1kOv9tD8l0b5Qu53`,
  library owner `b45c1524...cdc9`). A read-only `GET /v1/shared-voices` shows
  category `high_quality`, rate 1.0, a 730-day notice and no live moderation,
  and lists `eleven_v4_turbo` among its verified models. It is **not yet in
  the account**: `GET /v2/voices` returns 21 voices without it, and
  `GET /v1/voices/{id}` returns 400. Adding it uses
  `POST /v1/voices/add/{public_user_id}/{voice_id}` with `{"new_name": ...}`,
  which returns `voice_id`.
  ([add shared voice](https://elevenlabs.io/docs/api-reference/voices/voice-library/share))
  **Permission: not documented and not testable without a write.** The SDK's
  permission enum has both `voices_write` and `add_voice_from_voice_library`.
  The key's 401 errors name the missing permission (`/v1/models` said "missing
  the permission models_read"), so a refused add will say which one it needs.
  Expect Voices Write.
* **LLM.** The prompting guide lists "GLM 5.2 or GPT-6 Luna" as starting
  points and "Claude Sonnet 5.5" for complex reasoning
  ([guide](https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide)).
  The SDK `Llm` enum id is **`claude-sonnet-5-5`**. The prompt config has both
  `thinking_budget` (tokens; 0 turns it off where allowed) and
  `reasoning_effort` (`none` to `max`). The models page says which one applies
  "depending on the model" without naming Sonnet 5.5's, and advises a low
  setting for voice ([models](https://elevenlabs.io/docs/eleven-agents/customization/llm)).
  The config sends `thinking_budget: 1024`; switch to `reasoning_effort: "low"`
  if it is refused. Low temperature (0.0-0.3) suits structured calls.
* **Prompt structure** follows the guide: Personality, Environment, Tone, Goal,
  Guardrails, Tools (one `##` per tool), Tool error handling; sentence-case
  headings; short bullets; "This step is important." on critical lines, with
  the top rules repeated; under about 2,000 tokens.
* **Built-in tools** (SDK `SystemToolConfigInputParams`): `end_call` (no
  params), `voicemail_detection` (`voicemail_message`, which supports dynamic
  variables; with no message it just hangs up), `play_keypad_touch_tone`
  (`use_out_of_band_dtmf`, SIP only, and `suppress_turn_after_dtmf`), and
  `skip_turn` (`wait_timeout_secs`, which pauses the end-on-silence timer;
  `-1` waits silently). Language detection is not used.
* **Disclosure.** Tell users they are talking to AI and that conversations are
  recorded and may be shared with ElevenLabs and LLM providers. Suggested
  verbal opening: "Hi, I'm an AI assistant", plus that the call "may be
  recorded and shared with service providers".
  ([disclosure](https://elevenlabs.io/docs/eleven-agents/legal/disclosure-requirement))
* **TCPA.** "Outbound calls to residential numbers are restricted to 8:00 a.m.
  to 9:00 p.m. in the recipient's local time zone." Opt-outs must be honoured
  (the agent ends the call, and `do_not_call_requested` is recorded).
  ([TCPA](https://elevenlabs.io/docs/eleven-agents/legal/tcpa))
  Rally's calls are informational calls Jonathan asked for, mostly to
  businesses, but the hours rule is enforced for every call.

## (j) Pronunciation dictionaries (checked 2026-11-05)

SDK `pronunciation_dictionaries/raw_client.py` and the API reference:

* `POST /v1/pronunciation-dictionaries/add-from-rules`, body `{name, rules,
  description?, workspace_access?}`, returns `id` and `version_id`.
  ([docs](https://elevenlabs.io/docs/api-reference/pronunciation-dictionaries/create-from-rules))
* `GET /v1/pronunciation-dictionaries?page_size=&cursor=` returns
  `{pronunciation_dictionaries, next_cursor, has_more}`. `GET
  /v1/pronunciation-dictionaries/{id}` returns `latest_version_id` and the
  latest `rules` (verified read-only).
  ([docs](https://elevenlabs.io/docs/api-reference/pronunciation-dictionaries/get))
* `POST /v1/pronunciation-dictionaries/{id}/set-rules` with `{rules}` replaces
  all rules and returns the new `version_id` (`add-rules` and `remove-rules`
  also exist).
* An alias rule is `{type: "alias", string_to_replace, alias, case_sensitive,
  word_boundaries}`; both flags default to true.
* The agent references a version through
  `conversation_config.tts.pronunciation_dictionary_locators`:
  `[{pronunciation_dictionary_id, version_id}]`.
