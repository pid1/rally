#!/usr/bin/env python3
"""Write (or check) the OpenAPI spec the iOS client is generated from.

The spec is checked in at `ios/openapi.json` so a change to the API shows up in
the same diff as the client change it requires. `tests/test_openapi.py` fails
when the file is stale, which is what stops the app and the server from
drifting apart quietly.

Usage:

    openapi                                   # devenv script: rewrite the file
    uv run python scripts/export_openapi.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "ios" / "openapi.json"


def render_spec() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from rally.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    args = parser.parse_args()

    spec = render_spec()
    if args.check:
        if not SPEC_PATH.exists() or SPEC_PATH.read_text() != spec:
            print(f"{SPEC_PATH} is stale; run `openapi` to regenerate it.", file=sys.stderr)
            return 1
        return 0

    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_text(spec)
    print(f"Wrote {SPEC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
