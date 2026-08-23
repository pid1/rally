"""Which family member hears about which kind of notification.

Rally sends five kinds of push, and until now the only per-person lever was
whether somebody had a Pushover key at all — one switch deciding all five. The
noise profiles are genuinely different: an event reminder fires once, thirty
minutes before something you are going to; a change notice fires on every edit
to every event you are on; a refresh digest arrives daily. This module is the
one place that decides who hears what.

Five gates, in this order, for every push Rally sends:

1. the install has a Pushover application token
2. the kind's install-wide switch is on, where it has one
3. the member has a Pushover key
4. the member's preference for that kind
5. the kind's own **audience rule**

The audience rules are unchanged and live where they always did — an event
reminder still goes to that event's attendees, a task still goes to its one
assignee, the refresh digest still concerns the household. A preference can
only ever *narrow* what somebody already receives; ticking every box does not
start sending you other people's appointments. ``shopping_added`` is the one
kind where the preference and the audience rule are the same thing, because a
shopping list has no attendees to narrow.

**An absent row means the kind's default.** The row only exists once somebody
has expressed a preference, so upgrading changes nobody's behaviour and the
defaults stay a property of the catalogue rather than of the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from rally.models import FamilyMember, MemberNotificationPref, Setting

EVENT_REMINDER = "event_reminder"
EVENT_CHANGE = "event_change"
TASK_ASSIGNMENT = "task_assignment"
PREP_REFRESH = "prep_refresh"
SHOPPING_ADDED = "shopping_added"


@dataclass(frozen=True)
class NotificationKind:
    """One kind of notification Rally sends.

    ``audience`` is a sentence rather than a code path because it is rendered
    verbatim in Settings: the answer to *"why didn't Jake get that?"* belongs
    on the same screen as the question.

    ``settings_key`` is the install-wide switch, where the kind has one.
    ``default_on`` is what an absent preference row resolves to, chosen so that
    upgrading preserves exactly what Rally did before — which is why
    ``shopping_added``, the kind this table was written for, is the one that
    starts off.
    """

    key: str
    label: str
    audience: str
    default_on: bool
    settings_key: str | None = None
    # How the switch behind ``settings_key`` reads when its row is absent.
    # Every existing switch defaults on; shopping additions are opt-in at both
    # levels, so shipping the feature is not the same as turning it on.
    settings_default_on: bool = True


KINDS: tuple[NotificationKind, ...] = (
    NotificationKind(
        key=EVENT_REMINDER,
        label="Event reminders",
        audience="The event's attendees, at its reminder lead time — and when somebody "
        "presses Notify attendees.",
        default_on=True,
    ),
    NotificationKind(
        key=EVENT_CHANGE,
        label="Calendar additions and changes",
        audience="The event's attendees, whenever a Rally event is added, changed or deleted.",
        default_on=True,
    ),
    NotificationKind(
        key=TASK_ASSIGNMENT,
        label="Task hand-offs",
        audience="The one person a task is created for or handed to.",
        default_on=True,
        settings_key="todo_notify_enabled",
    ),
    NotificationKind(
        key=PREP_REFRESH,
        label="Preparedness refresh digest",
        audience="Everybody with a Pushover key, once a day, covering everything due.",
        default_on=True,
        settings_key="prep_notify_enabled",
    ),
    NotificationKind(
        key=SHOPPING_ADDED,
        label="Shopping list additions",
        audience="Whoever asked for them — a shopping list belongs to the household, "
        "so there is no audience to narrow.",
        default_on=False,
        settings_key="shopping_notify_enabled",
        settings_default_on=False,
    ),
)

KINDS_BY_KEY: dict[str, NotificationKind] = {kind.key: kind for kind in KINDS}
KIND_KEYS: tuple[str, ...] = tuple(kind.key for kind in KINDS)


def defaults() -> dict[str, bool]:
    """What every kind resolves to before anybody has expressed a preference."""
    return {kind.key: kind.default_on for kind in KINDS}


def switch_enabled(db: Session, kind_key: str) -> bool:
    """Whether the kind's install-wide switch is on. True when it has none.

    Reads the settings row the feature's own section already writes, so there
    is exactly one switch per kind rather than a second one here.
    """
    kind = KINDS_BY_KEY.get(kind_key)
    if kind is None or kind.settings_key is None:
        return kind is not None
    row = db.query(Setting).filter(Setting.key == kind.settings_key).first()
    value = (row.value or "").strip().lower() if row else ""
    if not value:
        return kind.settings_default_on
    return value != "false"


def preferences(db: Session, member_id: int) -> dict[str, bool]:
    """One member's answer for every kind, with the defaults filled in.

    This is the shape ``FamilyMemberResponse`` carries, deliberately *resolved*:
    no client should have to know what the defaults are to render a checkbox.
    It is the preference alone — a member with no Pushover key still has
    preferences, and they take effect the moment a key is added.
    """
    resolved = defaults()
    rows = (
        db.query(MemberNotificationPref)
        .filter(MemberNotificationPref.family_member_id == member_id)
        .all()
    )
    for row in rows:
        if row.kind in resolved:
            resolved[row.kind] = bool(row.enabled)
    return resolved


def prefers(db: Session, member_id: int, kind_key: str) -> bool:
    """One member's preference for one kind. False for an unknown kind."""
    kind = KINDS_BY_KEY.get(kind_key)
    if kind is None:
        return False
    row = (
        db.query(MemberNotificationPref)
        .filter(
            MemberNotificationPref.family_member_id == member_id,
            MemberNotificationPref.kind == kind_key,
        )
        .first()
    )
    return kind.default_on if row is None else bool(row.enabled)


def wants(db: Session, member: FamilyMember, kind_key: str) -> bool:
    """Whether this person hears about this kind at all — gates 2 through 4.

    The application token (gate 1) is the sender's business, and the audience
    rule (gate 5) is the caller's: this answers the member-level question and
    nothing else.
    """
    if kind_key not in KINDS_BY_KEY:
        return False
    if not switch_enabled(db, kind_key):
        return False
    if not (member.pushover_user_key or "").strip():
        return False
    return prefers(db, member.id, kind_key)


def filter_recipients(
    db: Session, members: Sequence[FamilyMember], kind_key: str
) -> tuple[list[FamilyMember], list[str]]:
    """Split an audience into who still hears it and who asked not to.

    Callers pass the people the kind's audience rule already chose, so this
    only ever narrows. The muted names are returned rather than dropped for the
    same reason ``recipients_for_event`` returns the keyless ones: *"sent to
    Jon · Emma has event reminders turned off"* is a different claim from "it
    worked", and a button that silently drops a recipient is worse than one
    that says so by name.

    A member with no Pushover key is in neither list. That is *skipped*, not
    muted — they did not ask for silence, they simply cannot be reached — and
    every caller reports that already.
    """
    allowed: list[FamilyMember] = []
    muted: list[str] = []
    for member in members:
        if not (member.pushover_user_key or "").strip():
            continue
        if wants(db, member, kind_key):
            allowed.append(member)
        else:
            muted.append(member.name)
    return allowed, muted


def subscribers(db: Session, kind_key: str) -> list[FamilyMember]:
    """Everybody who opted in to a kind that has no audience rule of its own.

    Shopping list additions are the case: the list belongs to the household, so
    a naive "notify on add" buzzes every phone in the house. The only honest
    audience is the people who asked.
    """
    members = db.query(FamilyMember).order_by(FamilyMember.name.asc()).all()
    return [member for member in members if wants(db, member, kind_key)]


def set_preferences(db: Session, member_id: int, values: dict[str, bool]) -> None:
    """Write a partial set of preferences, leaving unmentioned kinds alone.

    A row is written even when the value matches the default: it records that
    somebody chose, which is what makes a later change to a default leave
    existing families where they are. Unknown kinds are ignored here — the API
    rejects them with a 422 long before this, and storing a preference nothing
    will ever read is the failure mode worth avoiding twice.
    """
    if not values:
        return

    rows = {
        row.kind: row
        for row in db.query(MemberNotificationPref)
        .filter(MemberNotificationPref.family_member_id == member_id)
        .all()
    }
    for key, enabled in values.items():
        if key not in KINDS_BY_KEY:
            continue
        row = rows.get(key)
        if row is None:
            db.add(
                MemberNotificationPref(family_member_id=member_id, kind=key, enabled=bool(enabled))
            )
        else:
            row.enabled = bool(enabled)
    db.commit()


def delete_preferences(db: Session, member_id: int) -> int:
    """Drop every preference row for a member. Returns the count deleted.

    Nothing enforces the reference, so deleting a member has to say so
    explicitly — the same reason ``DELETE /api/events/{id}`` cascades its own
    attendees and notifications by hand.
    """
    deleted = (
        db.query(MemberNotificationPref)
        .filter(MemberNotificationPref.family_member_id == member_id)
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
    return deleted


def overview(db: Session) -> list[dict]:
    """Every kind Rally sends, and who currently receives it.

    Read-only on purpose. One editor for one piece of state, and the editor
    belongs on the person's own record — an editable member × kind matrix does
    not survive 390px and would be a second place to change the same thing.
    """
    members = db.query(FamilyMember).order_by(FamilyMember.name.asc()).all()

    rows = []
    for kind in KINDS:
        switch_on = switch_enabled(db, kind.key)
        receiving: list[str] = []
        muted: list[str] = []
        no_key: list[str] = []
        for member in members:
            if not (member.pushover_user_key or "").strip():
                no_key.append(member.name)
            elif switch_on and prefers(db, member.id, kind.key):
                receiving.append(member.name)
            else:
                muted.append(member.name)
        rows.append(
            {
                "kind": kind.key,
                "label": kind.label,
                "audience": kind.audience,
                "default_on": kind.default_on,
                "settings_key": kind.settings_key,
                "enabled": switch_on,
                "receiving": receiving,
                "muted": muted,
                "no_key": no_key,
            }
        )
    return rows
