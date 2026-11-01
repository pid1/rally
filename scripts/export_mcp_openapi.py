"""Write the OpenAPI document Rally serves rally-mcp to mcp/tests/fixtures/.

rally-mcp's tests generate their tools from this snapshot, and
tests/test_mcp_policy.py fails when it no longer matches what Rally serves,
so the two projects cannot drift apart unnoticed. Run after changing a route
or rally/audit/mcp_policy.py:

    uv run python scripts/export_mcp_openapi.py
"""

import json
import pathlib
import sys
import warnings

warnings.filterwarnings("ignore")
# src layout, not installed: put src on the path the way pytest's `pythonpath` does.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from rally.audit import mcp_policy  # noqa: E402
from rally.main import app  # noqa: E402

OUT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "mcp"
    / "tests"
    / "fixtures"
    / "rally-mcp-openapi.json"
)


def render() -> str:
    return json.dumps(mcp_policy.openapi_for_mcp(app), indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT}")
