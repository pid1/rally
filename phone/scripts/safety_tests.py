"""Turn agent/safety_tests.json into ElevenLabs agent tests, and run them.

Dry run is the default: prints the test definitions. With --apply it creates
any missing tests (POST /v1/convai/agent-testing/create, matched by name) and
runs them all on the agent (POST /v1/convai/agents/{id}/run-tests). Results
appear in the ElevenLabs dashboard under the agent's Tests tab, or with
`elevenlabs agents test <agent_id>` from the ElevenLabs CLI.

    uv run scripts/safety_tests.py                 # show the tests
    uv run scripts/safety_tests.py --apply         # create and run them
    uv run scripts/safety_tests.py --apply --repeat 5
"""

from __future__ import annotations

import argparse
import json
import sys

from rally_phone import agent_spec, secrets
from rally_phone.config import Settings
from rally_phone.elevenlabs import ElevenLabs, safe_id

PREFIX = "rally-phone safety: "


def load_cases() -> dict:
    return json.loads((agent_spec.AGENT_DIR / "safety_tests.json").read_text())


def test_bodies() -> list[dict]:
    spec = load_cases()
    owner = agent_spec.load()["owner_name"]
    variables = {"owner_name": owner, **agent_spec.PLACEHOLDERS}
    first = (agent_spec.AGENT_DIR / "first_message.txt").read_text().strip()
    for k, v in variables.items():
        first = first.replace("{{" + k + "}}", str(v))
    bodies = []
    for c in spec["cases"]:
        bodies.append(
            {
                "type": "llm",
                "name": PREFIX + c["id"],
                "chat_history": [
                    {"role": "agent", "time_in_call_secs": 0, "message": first},
                    {"role": "user", "time_in_call_secs": 8, "message": c["utterance"]},
                ],
                "dynamic_variables": variables,
                "success_condition": "Return True only if the agent's reply does this: "
                + c["expected"],
                "success_examples": [{"response": c["success_example"], "type": "success"}],
                "failure_examples": [{"response": c["failure_example"], "type": "failure"}],
            }
        )
    for s in spec.get("simulations", []):
        bodies.append(
            {
                "type": "simulation",
                "name": PREFIX + s["id"],
                "dynamic_variables": variables,
                "simulation_scenario": s["scenario"],
                "simulation_max_turns": s["max_turns"],
                "success_conditions": s["success_conditions"],
            }
        )
    return bodies


def main(argv: list[str] | None = None, *, eleven: ElevenLabs | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--apply", action="store_true", help="create and run the tests")
    p.add_argument("--repeat", type=int, default=None, help="repeat_count, 2-20")
    args = p.parse_args(argv)

    bodies = test_bodies()
    if not args.apply:
        for b in bodies:
            print(f"DRY RUN: POST /v1/convai/agent-testing/create  {b['name']} ({b['type']})")
        print(json.dumps(bodies[0], indent=2))
        print(f"\n{len(bodies)} tests. Dry run: nothing was sent. Re-run with --apply.")
        return 0

    if eleven is None:
        eleven = ElevenLabs(secrets.get_secret(secrets.ELEVENLABS_API_KEY))
    agent_id = Settings.load().agent_id
    existing = {
        t["name"]: t["id"]
        for t in eleven.request("GET", "/v1/convai/agent-testing", params={"page_size": 100}).get(
            "tests", []
        )
    }
    ids = []
    for b in bodies:
        test_id = existing.get(b["name"])
        if test_id:
            eleven.request("PUT", f"/v1/convai/agent-testing/{safe_id(test_id)}", json=b)
        else:
            test_id = eleven.request("POST", "/v1/convai/agent-testing/create", json=b)["id"]
        ids.append(test_id)
    run = {"tests": [{"test_id": i} for i in ids]}
    if args.repeat:
        run["repeat_count"] = args.repeat
    res = eleven.request("POST", f"/v1/convai/agents/{safe_id(agent_id)}/run-tests", json=run)
    print(f"Started {len(ids)} tests: invocation {res.get('id')}. See the agent's Tests tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
