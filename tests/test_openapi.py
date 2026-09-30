"""The checked-in OpenAPI spec must match the running app."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_openapi.py"


def _load():
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_openapi_spec_is_current():
    module = _load()
    assert module.SPEC_PATH.exists(), "run `openapi` to create ios/openapi.json"
    assert module.SPEC_PATH.read_text() == module.render_spec(), (
        "ios/openapi.json is stale; run `openapi` and commit the result"
    )


def test_spec_describes_the_dashboard_endpoint():
    spec = __import__("json").loads(_load().render_spec())
    assert "/api/dashboard" in spec["paths"]
