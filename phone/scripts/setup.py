"""Create or update the rally-phone ElevenLabs agent and phone number, idempotently.

Dry run is the default: read-only lookups run (when a key is available) and
every write is printed, secrets redacted, but not sent. Nothing is written to
ElevenLabs or Twilio without --apply.

    uv run scripts/setup.py all                 # show everything that would be written
    uv run scripts/setup.py all --apply         # create/update the agent, save config
    uv run scripts/setup.py agent [--apply]     # the agent only
    uv run scripts/setup.py phone-number        # check the imported number (read-only)

The phone number defaults to the one imported in the ElevenLabs UI
(agent/agent.json "phone_number_id"); --phone-number-id picks another.
Optional, not the default flow: --number +1... imports a Twilio number with
Twilio credentials, and `twilio-numbers` lists Twilio numbers read-only.

--apply also writes ~/.config/rally-phone/config.json (IDs only) for the MCP server.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

import httpx

from rally_phone import agent_spec, secrets
from rally_phone.brief import BriefError, check_number
from rally_phone.config import write_config
from rally_phone.elevenlabs import DEFAULT_BASE_URL, ElevenLabs, ElevenLabsError, safe_id

TWILIO_API = "https://api.twilio.com"


@dataclass
class Setup:
    eleven: ElevenLabs | None
    apply: bool
    secrets_to_redact: list[str | None] = field(default_factory=list)
    twilio_transport: httpx.BaseTransport | None = None
    out: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)

    def say(self, text: str = "") -> None:
        self.out.append(secrets.redact(text, self.secrets_to_redact))
        print(self.out[-1])

    def write(self, method: str, path: str, body: dict | None = None) -> dict:
        """Print the request; send it only with --apply."""
        shown = json.dumps(body, indent=2) if body is not None else ""
        self.say(f"{'APPLY' if self.apply else 'DRY RUN'}: {method} {DEFAULT_BASE_URL}{path}")
        if shown:
            self.say(shown)
        if not self.apply:
            return {}
        if self.eleven is None:
            raise SystemExit("--apply needs an ElevenLabs API key")
        return self.eleven.request(method, path, json=body)

    # Steps -----------------------------------------------------------------

    def account_voices(self) -> list[dict]:
        """GET /v2/voices, every page (read-only; Voices Read)."""
        voices, token = [], None
        for _ in range(20):
            page = self.eleven.request(
                "GET", "/v2/voices", params={"page_size": 100, "next_page_token": token}
            )
            voices += page.get("voices", [])
            token = page.get("next_page_token")
            if not page.get("has_more") or not token:
                break
        return voices

    def voice(self) -> str:
        """Make sure the agent's voice is in the account; add it from the library if not."""
        v = agent_spec.load()["voice"]
        voice_id, owner, name = v["voice_id"], v.get("library_owner_id"), v.get("library_name")
        if self.eleven is None:
            self.say("(no ElevenLabs key: skipping the voice check)")
            return voice_id
        for have in self.account_voices():
            if have.get("voice_id") == voice_id or (name and have.get("name") == name):
                self.say(f"Voice {have.get('name')!r} is in the account ({have['voice_id']}).")
                return have["voice_id"]
        if not owner:
            raise SystemExit(f"Voice {voice_id} is not in the account and has no library owner")
        self.say(f"Voice {voice_id} is not in the account: adding it from the Voice Library.")
        res = self.write(
            "POST",
            f"/v1/voices/add/{safe_id(owner)}/{safe_id(voice_id)}",
            {"new_name": name or "Rally assistant voice"},
        )
        return res.get("voice_id") or voice_id

    def pronunciation(self) -> list[dict]:
        """Find, create or update the pronunciation dictionary; return the agent's locators."""
        spec = agent_spec.load()
        name, rules = spec["pronunciation_dictionary_name"], spec["pronunciation_rules"]
        if not rules:
            return []
        if self.eleven is None:
            self.say("(no ElevenLabs key: skipping the pronunciation dictionary lookup)")
            return [{"pronunciation_dictionary_id": "<dictionary-id>", "version_id": None}]
        found, cursor = None, None
        for _ in range(20):
            page = self.eleven.request(
                "GET", "/v1/pronunciation-dictionaries", params={"page_size": 100, "cursor": cursor}
            )
            matches = [
                d for d in page.get("pronunciation_dictionaries", []) if d.get("name") == name
            ]
            if matches:
                found = matches[0]
                break
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not cursor:
                break
        if found is None:
            self.say(f"No pronunciation dictionary named {name!r}: creating it.")
            res = self.write(
                "POST",
                "/v1/pronunciation-dictionaries/add-from-rules",
                {"name": name, "rules": rules},
            )
            dict_id, version = res.get("id", "<new-dictionary-id>"), res.get("version_id")
        else:
            dict_id = found["id"]
            current = self.eleven.request(
                "GET", f"/v1/pronunciation-dictionaries/{safe_id(dict_id)}"
            )
            version = current.get("latest_version_id")
            if agent_spec.normalise_rules(current.get("rules", [])) == agent_spec.normalise_rules(
                rules
            ):
                self.say(
                    f"Pronunciation dictionary {name!r} is up to date ({dict_id} @ {version})."
                )
            else:
                self.say(f"Pronunciation dictionary {name!r} has different rules: replacing them.")
                res = self.write(
                    "POST",
                    f"/v1/pronunciation-dictionaries/{safe_id(dict_id)}/set-rules",
                    {"rules": rules},
                )
                version = res.get("version_id", "<new-version-id>")
        return [{"pronunciation_dictionary_id": dict_id, "version_id": version}]

    def compare_live(self, agent_id: str, body: dict) -> list[str]:
        """Say which TTS and LLM settings the update would change on the live agent.

        Only fields setup sends are compared; defaults ElevenLabs adds are ignored.
        """
        live = self.eleven.request("GET", f"/v1/convai/agents/{safe_id(agent_id)}")
        changes: list[str] = []
        for label in ("tts", "agent.prompt"):
            want, have = body["conversation_config"], live.get("conversation_config") or {}
            for key in label.split("."):
                want, have = want.get(key, {}), (have or {}).get(key) or {}
            want = {k: v for k, v in want.items() if k != "prompt"}
            changes += _differences(want, have, label)
        if changes:
            self.say("Differs from the live agent: " + ", ".join(changes))
        else:
            self.say("TTS and LLM settings match the live agent.")
        return changes

    def agent(self, *, mcp_server_ids: list[str] | None = None) -> str | None:
        voice_id = self.voice()
        locators = self.pronunciation()
        body = agent_spec.build_agent_body(
            mcp_server_ids=mcp_server_ids, voice_id=voice_id, pronunciation_locators=locators
        )
        existing = None
        if self.eleven is None:
            self.say("(no ElevenLabs key: skipping the lookup; showing a create)")
        else:
            agents = self.eleven.list_agents(body["name"]).get("agents", [])
            matches = [a for a in agents if a.get("name") == body["name"]]
            if len(matches) > 1:
                raise SystemExit(f"{len(matches)} agents are named {body['name']!r}; keep one")
            existing = matches[0]["agent_id"] if matches else None
        if existing:
            self.say(f"Agent exists ({existing}): updating it to match phone/agent/.")
            self.compare_live(existing, body)
            self.write("PATCH", f"/v1/convai/agents/{safe_id(existing)}", body)
            agent_id = existing
        else:
            self.say("No agent named so: creating it.")
            agent_id = self.write("POST", "/v1/convai/agents/create", body).get("agent_id")
        if agent_id:
            self.config["agent_id"] = agent_id
        self.config["owner_name"] = agent_spec.load()["owner_name"]
        return agent_id

    def phone_number(
        self, *, phone_number_id: str | None, number: str | None, label: str
    ) -> str | None:
        if phone_number_id:
            if self.eleven is not None:
                pn = self.eleven.request(
                    "GET", f"/v1/convai/phone-numbers/{safe_id(phone_number_id)}"
                )
                self.say(
                    f"Using imported number {pn.get('phone_number')} "
                    f"({pn.get('provider')}, id {phone_number_id}); inbound agent: "
                    f"{(pn.get('assigned_agent') or {}).get('agent_name') or 'none'}."
                )
                if pn.get("phone_number"):
                    self.config["assistant_number"] = pn["phone_number"]
            self.config["phone_number_id"] = phone_number_id
            return phone_number_id
        if not number:
            self.list_elevenlabs_numbers()
            raise SystemExit("No phone number: pass --phone-number-id or set it in agent.json")
        try:
            number = check_number(number)
        except BriefError as e:
            raise SystemExit(str(e)) from None
        if self.eleven is not None:
            for pn in self.eleven.list_phone_numbers():
                if pn.get("phone_number") == number:
                    self.say(f"{number} is already imported (id {pn['phone_number_id']}).")
                    self.config["phone_number_id"] = pn["phone_number_id"]
                    self.config["assistant_number"] = number
                    return pn["phone_number_id"]
        sid = secrets.get_secret(secrets.TWILIO_ACCOUNT_SID)
        token = secrets.get_secret(secrets.TWILIO_AUTH_TOKEN)
        self.secrets_to_redact += [sid, token]
        self.say(
            f"Importing {number} from Twilio. ElevenLabs will point this number's voice "
            "webhook at itself: use a number nothing else answers."
        )
        res = self.write(
            "POST",
            "/v1/convai/phone-numbers",
            {
                "provider": "twilio",
                "phone_number": number,
                "label": label,
                "sid": sid,
                "token": token,
                "enable_sms": False,
            },
        )
        if res.get("phone_number_id"):
            self.config["phone_number_id"] = res["phone_number_id"]
            self.config["assistant_number"] = number
        return res.get("phone_number_id")

    def list_elevenlabs_numbers(self) -> None:
        if self.eleven is None:
            return
        numbers = self.eleven.list_phone_numbers()
        self.say(f"ElevenLabs has {len(numbers)} imported number(s).")
        for pn in numbers:
            self.say(
                f"  {pn.get('phone_number')}  {pn.get('label')}  id={pn.get('phone_number_id')}"
            )

    def twilio_numbers(self) -> list[dict]:
        """Read-only: GET IncomingPhoneNumbers. Never buys or changes anything."""
        sid = secrets.get_secret(secrets.TWILIO_ACCOUNT_SID)
        token = secrets.get_secret(secrets.TWILIO_AUTH_TOKEN)
        self.secrets_to_redact += [sid, token]
        with httpx.Client(
            base_url=TWILIO_API, auth=(sid, token), transport=self.twilio_transport, timeout=30
        ) as tw:
            resp = tw.get(
                f"/2010-04-01/Accounts/{sid}/IncomingPhoneNumbers.json", params={"PageSize": 50}
            )
        if resp.status_code >= 400:
            raise SystemExit(f"Twilio returned {resp.status_code}")
        numbers = resp.json().get("incoming_phone_numbers", [])
        self.say(f"Twilio numbers ({len(numbers)}):")
        for n in numbers:
            voice = httpx.URL(n["voice_url"]).host if n.get("voice_url") else "(not set)"
            caps = n.get("capabilities") or {}
            self.say(
                f"  {n.get('phone_number')}  {n.get('friendly_name')}  "
                f"voice={'yes' if caps.get('voice') else 'no'}  answers_via={voice}"
            )
        self.say("Pick one whose answers_via is unused; importing re-points its voice webhook.")
        return numbers

    def rally_mcp(self) -> list[str]:
        """V2 hook: register Rally's busy-only MCP server. Disabled in V1."""
        hook = agent_spec.load()["rally_mcp"]
        if not hook.get("enabled"):
            raise SystemExit("rally_mcp.enabled is false in agent/agent.json (V2 hook)")
        token = secrets.get_secret(secrets.RALLY_PHONE_MCP_TOKEN)
        self.secrets_to_redact.append(token)
        body = agent_spec.rally_mcp_body(token=token)
        existing = None
        if self.eleven is not None:
            for s in self.eleven.list_mcp_servers().get("mcp_servers", []):
                if (s.get("config") or {}).get("name") == hook["name"]:
                    existing = s["id"]
        if existing:
            self.write("PATCH", f"/v1/convai/mcp-servers/{existing}", body["config"])
            return [existing]
        res = self.write("POST", "/v1/convai/mcp-servers", body)
        return [res["id"]] if res.get("id") else ["<new-mcp-server-id>"]

    def save(self) -> None:
        if self.apply and self.config:
            path = write_config(self.config)
            self.say(f"Wrote {path}: {', '.join(sorted(self.config))}")
        elif self.config:
            self.say(f"DRY RUN: would write config with {', '.join(sorted(self.config))}")


def _differences(want, have, path: str) -> list[str]:
    if isinstance(want, dict) and isinstance(have, dict):
        out = []
        for key, value in want.items():
            out += _differences(value, have.get(key), f"{path}.{key}")
        return out
    if isinstance(want, float | int) and isinstance(have, float | int):
        return [] if float(want) == float(have) else [path]
    return [] if want == have else [path]


def main(argv: list[str] | None = None, *, eleven: ElevenLabs | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("step", choices=["agent", "phone-number", "twilio-numbers", "all"])
    p.add_argument("--apply", action="store_true", help="send writes (default: dry run)")
    p.add_argument("--phone-number-id", help="a number already imported in ElevenLabs")
    p.add_argument("--number", help="optional: E.164 Twilio number to import with Twilio creds")
    p.add_argument("--label", default="Rally phone assistant")
    p.add_argument("--enable-rally-mcp", action="store_true", help="V2: attach Rally's MCP")
    args = p.parse_args(argv)

    key = None
    if eleven is None:
        try:
            key = secrets.get_secret(secrets.ELEVENLABS_API_KEY, required=args.apply)
        except secrets.MissingSecret as e:
            print(str(e), file=sys.stderr)
            return 1
        eleven = ElevenLabs(key) if key else None
    s = Setup(eleven=eleven, apply=args.apply, secrets_to_redact=[key])
    try:
        if args.step == "twilio-numbers":
            s.twilio_numbers()
            s.list_elevenlabs_numbers()
            return 0
        if args.step in ("phone-number", "all"):
            pn_id = args.phone_number_id
            if not pn_id and not args.number:
                pn_id = agent_spec.load().get("phone_number_id")
            s.phone_number(phone_number_id=pn_id, number=args.number, label=args.label)
        if args.step in ("agent", "all"):
            ids = s.rally_mcp() if args.enable_rally_mcp else None
            s.agent(mcp_server_ids=ids)
        s.save()
    except ElevenLabsError as e:
        print(f"ElevenLabs error: {e}", file=sys.stderr)
        return 1
    except secrets.MissingSecret as e:
        print(str(e), file=sys.stderr)
        return 1
    if not args.apply:
        print("\nDry run: nothing was sent. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
