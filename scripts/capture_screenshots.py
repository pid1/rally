#!/usr/bin/env python3
"""Regenerate `docs/screenshots/` against a freshly seeded throwaway database.

These images were hand-cropped before this script existed, which is why they
drifted: the family in them predates the current seed and the calendar toolbar
still said `Period`. Capturing them from one place makes the framing a property
of the repo rather than of whoever last held the mouse.

Nothing here touches `rally.db`. The database is seeded into a temporary file,
served on its own port, and deleted afterwards — the same discipline as the
`demo` script, for the same reason.

Usage:

    screenshots           # devenv script
    uv run python scripts/capture_screenshots.py [--only NAME ...]

`demo-poster.png` is deliberately never captured: it is the click-through
thumbnail for the walkthrough video hosted on a GitHub release, so refreshing it
would advertise a UI the video does not show.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"

# Retina for the README's hero shots, 1x for the inline reference ones — which
# is the split the existing files already had.
RETINA = 2


def _free_port_wait(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"server never came up on {PORT}")


def seed(db_path: Path) -> None:
    """A demo database, plus the two states the seed itself cannot produce."""
    env = {**os.environ, "RALLY_DB_PATH": str(db_path), "PYTHONPATH": str(ROOT / "src")}
    py = str(ROOT / ".devenv" / "state" / "venv" / "bin" / "python")

    subprocess.run(
        [py, "-c", "from rally.database import init_db; init_db()"],
        env=env,
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    subprocess.run([py, "-m", "rally.cli"], env=env, cwd=ROOT, check=True, capture_output=True)

    # A Pushover key on one member, so the member modal shows the field doing
    # something, and a preparedness review, which is normally an LLM call. The
    # review is injected rather than requested: a screenshot script must not
    # need an API key, and canned text keeps the image reproducible.
    extras = subprocess.run(
        [py, "-c", SEED_EXTRAS], env=env, cwd=ROOT, capture_output=True, text=True
    )
    if extras.returncode:
        # Surfaced rather than swallowed: this step depends on app internals
        # (a settings gate, an injectable LLM) and is the first thing to break
        # when they move.
        raise RuntimeError(f"seed extras failed:\n{extras.stderr.strip()}")


SEED_EXTRAS = """
import json
from rally.database import SessionLocal
from rally.models import FamilyMember, Setting
from rally import prep_review

db = SessionLocal()
# Ordered by *name*, because that is the order Settings lists them in and the
# capture opens the first row — a key on any other member would leave the shot
# documenting an empty field.
member = db.query(FamilyMember).order_by(FamilyMember.name).first()
member.pushover_user_key = "uQiRzpo4DXghDmr9QzzfQu27cmVRsG"

# The review is opt-in (it is a real LLM call in normal use), so the button is
# hidden until this is on. The screenshot documents the feature, so turn it on.
row = db.query(Setting).filter(Setting.key == "prep_review_enabled").first()
if row:
    row.value = "true"
else:
    db.add(Setting(key="prep_review_enabled", value="true"))
db.commit()

REVIEW = json.dumps({
    "assumptions": [
        "Ages are not recorded, so the suggestions below assume school-age children.",
    ],
    "gaps": [
        {"item": "Manual can opener", "priority": "high",
         "reason": "The pantry holds tinned food and nothing listed opens it without power."},
        {"item": "Spare phone charging bank", "priority": "medium",
         "reason": "No charging capacity is recorded for an outage lasting more than a day."},
        {"item": "Printed contact list", "priority": "medium",
         "reason": "Every number the family relies on is on a device that needs power."},
    ],
    "strengths": [
        "Water storage is well ahead of the usual three-day guidance.",
        "First aid is stocked and has a refresh date rather than being set and forgotten.",
    ],
})

# run_review expects (raw, model) back from the call it is given.
prep_review.run_review(db, llm=lambda *a, **k: (REVIEW, "demo-model"))
db.close()
"""


@dataclass(frozen=True)
class Shot:
    """One image.

    ``element`` captures that component rather than the page, so a modal's crop
    is its own width and cannot drift with the window. ``setup`` runs before the
    shot — opening a modal, switching a view — and is given the page.
    """

    name: str
    url: str
    width: int = 1280
    height: int = 900
    scale: int = RETINA
    full_page: bool = True
    element: str | None = None
    setup: Callable | None = None


def _open_member_modal(page):
    page.locator("#family-list .editable-item button", has_text="Edit").first.click()
    page.wait_for_selector("#member-modal-overlay .modal-content", state="visible")


def _calendar(view, rng):
    def go(page):
        page.select_option("#view-select", view)
        page.select_option("#range-select", rng)
        page.wait_for_timeout(600)

    return go


def _open_other_nav(page):
    """The `Other` dropdown, which is how a reader reaches Preparedness."""
    page.locator("#other-dropdown .nav-dropdown-btn").click()
    page.wait_for_selector("#other-dropdown.open", state="attached")


def _prep_settings_section(page):
    """Scroll the Preparedness block into view before cropping to it."""
    page.locator("#preparedness-form").scroll_into_view_if_needed()
    page.wait_for_timeout(300)


def _open_recurring_edit(page):
    """Scouts repeats weekly, so the edit form shows the recurrence controls.

    Picking by title rather than by position keeps the shot showing what its
    filename claims even if the seed gains an event.
    """
    page.select_option("#view-select", "agenda")
    page.select_option("#range-select", "rolling30")
    page.wait_for_timeout(600)
    page.locator(".agenda-day .editable-item", has_text="Scouts").first.click()
    page.wait_for_selector("#detail-modal-overlay .modal-content", state="visible")
    page.locator("#btn-detail-edit").click()
    page.wait_for_selector("#event-modal-overlay .modal-content", state="visible")
    page.wait_for_timeout(400)


def _open_event_detail(page):
    """The detail modal, where `Notify attendees` lives.

    The banner the old shot carried ("Sent to X. No Pushover key for Y.") is a
    *result*, and producing it means really pushing to Pushover. A screenshot
    script must not send anybody a notification, so this documents the control
    rather than its output.
    """
    page.select_option("#view-select", "agenda")
    page.select_option("#range-select", "rolling30")
    page.wait_for_timeout(600)
    # Today's event, so it is reliably inside the window whatever day the
    # seed is run on.
    page.locator(".agenda-day .editable-item", has_text="Piano lesson").first.click()
    page.wait_for_selector("#detail-modal-overlay .modal-content", state="visible")
    page.wait_for_timeout(300)


def _open_review(page):
    """The stored review renders into #review-panel on load."""
    page.wait_for_selector("#review-panel .prep-row", timeout=8000)


SHOTS: tuple[Shot, ...] = (
    # README heroes — retina, whole page.
    Shot("readme-dashboard", "/dashboard"),
    Shot("readme-calendar", "/calendar", setup=_calendar("calendar", "month")),
    Shot("readme-tasks", "/todo"),
    Shot("readme-shopping", "/shopping"),
    Shot("readme-preparedness", "/preparedness"),
    Shot("readme-mobile", "/calendar", width=390, height=844, full_page=False),
    # Calendar reference shots — 1x, matching the inline docs.
    Shot("calendar-month", "/calendar", scale=1, setup=_calendar("calendar", "month")),
    Shot("calendar-agenda", "/calendar", scale=1, setup=_calendar("agenda", "rolling30")),
    Shot(
        "calendar-mobile",
        "/calendar",
        width=390,
        height=844,
        scale=1,
        setup=_calendar("calendar", "month"),
    ),
    # Preparedness reference shots.
    Shot("preparedness-inventory", "/preparedness", width=1440, scale=1),
    Shot("preparedness-go-list", "/go-list", width=1440, scale=1),
    Shot("preparedness-review", "/preparedness", width=1440, scale=1, setup=_open_review),
    Shot("preparedness-mobile", "/preparedness", width=390, height=844, scale=1),
    Shot(
        "preparedness-nav",
        "/dashboard",
        width=1440,
        height=520,
        scale=1,
        full_page=False,
        setup=_open_other_nav,
    ),
    Shot(
        "preparedness-settings",
        "/settings",
        width=1440,
        scale=1,
        element="#preparedness-form",
        setup=_prep_settings_section,
    ),
    # Modals and sections — cropped to the component.
    Shot(
        "settings-member-pushover",
        "/settings",
        scale=1,
        element="#member-modal-overlay .modal-content",
        setup=_open_member_modal,
    ),
    Shot("settings-notifications", "/settings", scale=1, element="#notification-overview"),
    Shot(
        "event-notify",
        "/calendar",
        height=1200,
        scale=1,
        element="#detail-modal-overlay .modal-content",
        setup=_open_event_detail,
    ),
    Shot(
        "event-edit-recurring",
        "/calendar",
        # A modal is capped at 90vh and scrolls, so the viewport has to be tall
        # enough for the whole form or the crop simply ends mid-field.
        height=1400,
        scale=1,
        element="#event-modal-overlay .modal-content",
        setup=_open_recurring_edit,
    ),
)


def capture(only: set[str] | None) -> list[str]:
    from playwright.sync_api import sync_playwright

    wanted = [s for s in SHOTS if not only or s.name in only]
    failed = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for shot in wanted:
            context = browser.new_context(
                viewport={"width": shot.width, "height": shot.height},
                device_scale_factor=shot.scale,
            )
            page = context.new_page()
            try:
                page.goto(f"{BASE}{shot.url}", wait_until="networkidle")
                page.wait_for_timeout(400)
                if shot.setup:
                    shot.setup(page)
                page.wait_for_timeout(300)
                target = page.locator(shot.element).first if shot.element else page
                if shot.element:
                    target.screenshot(path=OUT / f"{shot.name}.png")
                else:
                    page.screenshot(path=OUT / f"{shot.name}.png", full_page=shot.full_page)
                print(f"  captured {shot.name}")
            except Exception as exc:  # noqa: BLE001 — one bad shot must not stop the run
                print(f"  ! {shot.name}: {type(exc).__name__}: {str(exc).splitlines()[0][:90]}")
                failed.append(shot.name)
            finally:
                context.close()
        browser.close()
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", help="capture just these shots, by name")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="rally-shots-"))
    db_path = tmp / "screenshots.db"
    server = None
    try:
        print(f"Seeding {db_path}...")
        seed(db_path)

        env = {
            **os.environ,
            "RALLY_DB_PATH": str(db_path),
            "PYTHONPATH": str(ROOT / "src"),
        }
        server = subprocess.Popen(
            [
                str(ROOT / ".devenv" / "state" / "venv" / "bin" / "uvicorn"),
                "rally.main:app",
                "--port",
                str(PORT),
            ],
            env=env,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _free_port_wait()
        print(f"Capturing into {OUT}...")
        failed = capture(set(args.only) if args.only else None)
    finally:
        if server:
            server.terminate()
            server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)

    if failed:
        print(f"\n{len(failed)} shot(s) failed: {', '.join(failed)}")
        return 1
    print("\nAll shots captured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
