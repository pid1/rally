"""Push a task to the person it was just handed to.

Rally already knows how to reach a phone — ``rally.notifications`` holds the
Pushover transport, the install's application token and each member's user key.
This module reuses all of it rather than growing a second notifier, the same
way the preparedness digest does.

What it adds is a third *shape* of notification. An event reminder points at one
occurrence and goes to that event's attendees; a refresh digest concerns the
household and goes to everybody. An assignment concerns exactly one person — the
assignee — and is announced once, at the moment somebody puts the task on their
list. Nobody else hears about it: a task moving between two people is not news
to the other two phones in the house.

Two things it deliberately does not do:

- **Announce a task nobody was handed.** ``Todo.assigned_to IS NULL`` means
  "Everyone", which is exactly the audience an event notification refuses to
  buzz. An unassigned task is on the shared list and stays there quietly.
- **Raise.** The task write is what the user asked for and it is already
  committed by the time anything here runs. A Pushover outage is logged, never
  surfaced as a failed request.

Recurring instances are not announced. ``process_recurring_todos()`` runs
opportunistically inside ``GET /api/todos``, so a push from there would fire
from a *read*, and a daily chore would buzz its owner every single morning
about a standing arrangement they already know about. The push marks the moment
a task is handed over, which for a recurring template happened when the
template was written.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from rally.models import FamilyMember, Setting, Todo
from rally.utils.settings import local_timezone_name
from rally.utils.timezone import today_local

# Settings key, mirroring ``prep_notify_enabled``. Absent means on: a family
# that has gone to the trouble of entering Pushover keys wants the pushes, and
# the row only exists once somebody has opened Settings to turn them off.
ENABLED_KEY = "todo_notify_enabled"

# Pushover's documented message ceiling. A task carrying a wall of description
# would otherwise get a 4xx instead of a notification.
MAX_MESSAGE_CHARS = 1024

# What a push says when there is nothing else true to say — no due date and no
# description. Never an empty body: Pushover rejects one.
BARE_BODY = "It's on your list."

# How far ahead a due date is still worth naming by weekday. Past a week
# "Due Tuesday" stops meaning *this* Tuesday and starts being a riddle.
WEEKDAY_HORIZON_DAYS = 7


def notifications_enabled(db: Session) -> bool:
    """Whether assignment pushes are turned on. Default on."""
    row = db.query(Setting).filter(Setting.key == ENABLED_KEY).first()
    value = (row.value or "").strip().lower() if row else ""
    return value != "false"


def _date_words(due: date, today: date) -> str:
    """A date as "Aug 28", carrying the year only when it is not this one."""
    stem = f"{due.strftime('%b')} {due.day}"
    return stem if due.year == today.year else f"{stem}, {due.year}"


def due_label(due_date: str | None, today: date) -> str | None:
    """When the task is due, in the shortest form that is still unambiguous.

    ``None`` for a task with no due date — the line is omitted rather than
    padded out with "no due date", which is not information anybody needs on a
    lock screen. A date that is already past is called overdue outright: the
    assignee is inheriting something late, and rounding that off to a bare date
    would be the one wording that hides it.
    """
    if not due_date:
        return None
    try:
        due = date.fromisoformat(due_date)
    except ValueError:
        return None

    delta = (due - today).days
    if delta < 0:
        return f"Overdue since {_date_words(due, today)}"
    if delta == 0:
        return "Due today"
    if delta == 1:
        return "Due tomorrow"
    if delta < WEEKDAY_HORIZON_DAYS:
        return f"Due {due.strftime('%A')}"
    return f"Due {_date_words(due, today)}"


def assignment_title(todo: Todo) -> str:
    """The push title: what happened, then to what.

    "New task" from the recipient's side is true of a fresh task and of one
    handed over from somebody else, so both take the same words — the reader
    cares what is now theirs, not which route it took to get there.
    """
    return f"New Task: {todo.title}"


def format_assignment(todo: Todo, today: date) -> str:
    """The body of an assignment push: when it is due, and what it is."""
    lines = []

    label = due_label(todo.due_date, today)
    if label:
        lines.append(label)

    description = (todo.description or "").strip()
    if description:
        room = MAX_MESSAGE_CHARS - sum(len(line) + 1 for line in lines)
        if len(description) > room:
            description = description[: room - 1].rstrip() + "…"
        lines.append(description)

    return "\n".join(lines) if lines else BARE_BODY


def assignee(db: Session, todo: Todo) -> FamilyMember | None:
    """The member a task belongs to, or ``None`` when it belongs to everyone."""
    if todo.assigned_to is None:
        return None
    return db.get(FamilyMember, todo.assigned_to)


def notify_assignment(db: Session, todo: Todo, *, previous_assignee: int | None = None) -> dict:
    """Tell the assignee a task is theirs. Never raises.

    ``previous_assignee`` is who held the task before this write, so a create
    (nobody) and a re-assignment take the same path and an edit that leaves the
    assignee alone stays silent. Editing the title of a task somebody has had
    for a week is not a hand-over.

    Returns ``{"sent": [...], "skipped": [...], "failed": [...]}`` with names,
    plus a ``skipped_reason`` when the whole send was a no-op — so a caller can
    tell "nobody to tell" from "it worked" without inspecting the database.
    """
    from rally.notifications import PushoverError, app_token, send_pushover

    result: dict = {"sent": [], "skipped": [], "failed": []}

    def skip(reason: str) -> dict:
        result["skipped_reason"] = reason
        return result

    try:
        if todo.assigned_to is None or todo.assigned_to == previous_assignee:
            return skip("nobody was newly assigned")
        if todo.completed:
            # A task that arrives already done is bookkeeping, not work.
            return skip("the task is already complete")
        if not notifications_enabled(db):
            return skip("task notifications are turned off")

        member = assignee(db, todo)
        if member is None:
            return skip("the assignee no longer exists")

        user_key = (member.pushover_user_key or "").strip()
        if not user_key:
            # The default state, not an error: most families set up one or two
            # keys and leave the rest blank.
            result["skipped"].append(member.name)
            return skip("the assignee has no Pushover key")

        token = app_token(db)
        if not token:
            result["skipped"].append(member.name)
            return skip("no Pushover application token configured")

        today = today_local(local_timezone_name(db))
        try:
            send_pushover(
                token,
                user_key,
                format_assignment(todo, today),
                title=assignment_title(todo),
                device=(member.pushover_device or "").strip() or None,
            )
        except PushoverError as exc:
            print(f"  Pushover failed for {member.name}: {exc}")
            result["failed"].append(member.name)
        else:
            result["sent"].append(member.name)

        return result
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Task assignment notification failed: {exc}")
        result["failed"].append("unknown")
        result["skipped_reason"] = str(exc)
        return result
