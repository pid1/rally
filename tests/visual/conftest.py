"""A real browser against a real server, seeded with the sample database.

These tests are skipped unless Playwright and its Chromium build are present,
so `uv run pytest` stays fast and dependency-free for everyone who is not
working on the design system. CI installs the browser and runs them.
"""

from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

# pytest puts src/ on the path via `pythonpath`, but the server and the seed
# helpers run in subprocesses that inherit no such thing.
SRC = pathlib.Path(__file__).resolve().parents[2] / "src"

pytest.importorskip("playwright", reason="Playwright is not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

VIEWPORTS = {
    "mobile": (390, 844),
    "tablet": (834, 1112),
    "desktop": (1440, 900),
}

# Every page a family member can reach, plus the styleguide.
PAGES = {
    "dashboard": "/dashboard",
    "todo": "/todo",
    "todo-completed": "/todo/completed",
    "shopping": "/shopping",
    "shopping-purchased": "/shopping/purchased",
    "dinner-planner": "/dinner-planner",
    "meal-history": "/meal-history",
    "settings": "/settings",
    "styleguide": "/styleguide",
}

# Pages built from the page/toolbar primitives. Dashboard, Settings and the
# styleguide are excluded from toolbar assertions: they have no filter bar.
TOOLBAR_PAGES = [
    "todo",
    "todo-completed",
    "shopping",
    "shopping-purchased",
    "dinner-planner",
    "meal-history",
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run(args: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(args, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{args[:3]} failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session")
def live_server(tmp_path_factory) -> str:
    """Serve Rally from a seeded throwaway database and yield its base URL."""
    db_path = tmp_path_factory.mktemp("visual") / "rally.db"
    env = {
        **os.environ,
        "RALLY_DB_PATH": str(db_path),
        "PYTHONPATH": os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")]).rstrip(
            os.pathsep
        ),
    }

    _run([sys.executable, "-c", "from rally.database import init_db; init_db()"], env)
    _run([sys.executable, "-c", "from rally.cli import seed; seed()"], env)

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "rally.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"

    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("the test server exited before it became ready")
        try:
            urllib.request.urlopen(f"{base}/dashboard", timeout=1)
            break
        except urllib.error.URLError, ConnectionError, TimeoutError:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("the test server never became ready")

    try:
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment guard
            pytest.skip(f"Chromium is not installed for Playwright: {exc}")
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture(scope="session")
def measure(browser, live_server):
    """Return `measure(page_name, viewport) -> dict` for the whole session.

    Results are cached: every assertion below reads the same measurement of the
    same rendered page, so a full run costs one page load per page/viewport.
    """
    from .probes import MEASURE_JS

    cache: dict[tuple[str, str], dict] = {}
    contexts: dict[str, object] = {}

    def _measure(page_name: str, viewport: str) -> dict:
        key = (page_name, viewport)
        if key in cache:
            return cache[key]
        if viewport not in contexts:
            width, height = VIEWPORTS[viewport]
            contexts[viewport] = browser.new_context(
                viewport={"width": width, "height": height},
                # A phone reports a coarse pointer; the stylesheet keys its
                # touch targets off that as well as off width.
                is_mobile=(viewport == "mobile"),
                has_touch=(viewport != "desktop"),
            )
        page = contexts[viewport].new_page()
        try:
            page.goto(live_server + PAGES[page_name], wait_until="networkidle")
            page.wait_for_timeout(250)
            cache[key] = page.evaluate(MEASURE_JS)
        finally:
            page.close()
        return cache[key]

    try:
        yield _measure
    finally:
        for ctx in contexts.values():
            ctx.close()
