"""Per-family-member behavioral settings, answered once per device.

This is the sibling of ``notification_prefs``. That module decides who *hears*
about what; this one decides what a screen *does* when somebody opens it. The
first setting is the calendar's landing view, and the table is deliberately a
catalog rather than a column per idea, so the second one costs an entry here
and nothing else.

**A setting is answered per device, not per class of device.** An earlier cut
of this split the answer two ways, phone and computer, and keyed it on the
768px breakpoint. That is a guess about a device dressed up as a fact about
one: the wall tablet in the kitchen and the laptop on the desk are both "a
computer" by width and want opposite things, and two phones belonging to two
people are the same phone to a media query. The device is the thing a person
actually configures, so the device is what the answer hangs on — you set it on
the screen you are looking at, and it applies to that screen.

**The screen still gets a vote, as a choice rather than as a schema.** Every
setting offers ``auto``, which means "let Rally decide, the way it always
has" — for the calendar, a phone-width screen opens on the day and a wider one
on the month. It is the default, so a device nobody has configured behaves
exactly as it did before this table existed, and it is a real option in the
list, so "I want Rally to pick" is something a person can say rather than
something they get by not speaking.

**An absent row means the setting's default**, the same discipline
``member_notification_prefs`` follows: the row only exists once somebody has
chosen, so upgrading changes nobody's screen.

**Who is "this person"?** Rally has no logins — it is one household on one
network, and a kitchen display nobody is signed in to is the normal case. The
binding is made by the device too: a browser remembers which family member is
using it (``static/device_member.js``), and a device that has not said resolves
to the defaults below. So a stored answer needs both halves — *this person, on
this device* — and either one missing falls back to ``auto``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from rally.models import Device, MemberPreference
from rally.utils.timezone import now_utc

CALENDAR_DEFAULT_VIEW = "calendar_default_view"

# The answer that defers to Rally's own rule. Every setting accepts it, it is
# every setting's default, and it is never stored as anything other than what a
# person deliberately chose — an absent row resolves to it either way.
AUTO = "auto"


@dataclass(frozen=True)
class Choice:
    """One answer a setting accepts.

    ``value`` is stored and is not opaque for the calendar's setting: it is
    ``<view>:<range>``, the two toolbar controls that actually draw the page.
    Encoding the pair in the value is what keeps the mapping in one place —
    the alternative is a lookup table on the server and a second one in the
    browser, which is how "Agenda · Week" ends up rendering a month somewhere.
    ``auto`` is the exception: it names Rally's own rule rather than a view,
    and only the page that has the rule can resolve it.
    """

    value: str
    label: str


@dataclass(frozen=True)
class BehaviorSetting:
    """One thing a person can decide about how Rally behaves on one device.

    ``choices`` always leads with ``auto`` and ``default`` is always ``auto``:
    a setting whose default were a concrete value would move a screen nobody
    had configured, which is the one thing this table must never do.
    """

    key: str
    label: str
    description: str
    choices: tuple[Choice, ...]
    default: str = AUTO


# Every combination the calendar's two toolbar controls can express, with one
# deliberate omission: `calendar:rolling30`. A grid cannot draw a rolling
# thirty days — `syncRangeOptions()` hides that range in Calendar mode — so
# offering it here would be offering a landing view the page then silently
# changes out from under you.
CALENDAR_VIEW_CHOICES: tuple[Choice, ...] = (
    Choice(value=AUTO, label="Match the screen — day on a phone, month on a computer"),
    Choice(value="calendar:day", label="Calendar · Day"),
    Choice(value="calendar:week", label="Calendar · Week"),
    Choice(value="calendar:month", label="Calendar · Month"),
    Choice(value="agenda:day", label="Agenda · Day"),
    Choice(value="agenda:week", label="Agenda · Week"),
    Choice(value="agenda:month", label="Agenda · Month"),
    Choice(value="agenda:rolling30", label="Agenda · Next 30 days"),
)


CATALOG: tuple[BehaviorSetting, ...] = (
    BehaviorSetting(
        key=CALENDAR_DEFAULT_VIEW,
        label="Calendar opens on",
        description=(
            "The view the calendar lands on when this person opens it on this device. "
            "Changing the View or Range once you are there still works and is never "
            "remembered — this is where you start, not somewhere you are kept."
        ),
        choices=CALENDAR_VIEW_CHOICES,
    ),
)

CATALOG_BY_KEY: dict[str, BehaviorSetting] = {setting.key: setting for setting in CATALOG}
SETTING_KEYS: tuple[str, ...] = tuple(setting.key for setting in CATALOG)

# A setting whose default is a concrete view would move an unconfigured screen,
# and a setting that does not offer `auto` would make "let Rally pick" an answer
# nobody can give back once they have given another. The import is the cheapest
# place to notice either.
for _setting in CATALOG:
    _values = {choice.value for choice in _setting.choices}
    if AUTO not in _values:
        raise ValueError(f"{_setting.key} must offer {AUTO!r} as a choice")
    if _setting.default != AUTO:
        raise ValueError(f"{_setting.key} must default to {AUTO!r}, not {_setting.default!r}")


def defaults() -> dict[str, str]:
    """What every setting resolves to before anybody has chosen."""
    return {setting.key: setting.default for setting in CATALOG}


def is_valid(key: str, value: str) -> bool:
    """Whether ``value`` is an answer ``key`` accepts."""
    setting = CATALOG_BY_KEY.get(key)
    if setting is None:
        return False
    return any(choice.value == value for choice in setting.choices)


def preferences(db: Session, member_id: int, device_id: str) -> dict[str, str]:
    """One member's answers on one device, with the defaults filled in.

    Resolved rather than raw, for the same reason the notification preferences
    are: a client that has to know the defaults to render a dropdown is a
    second place for the defaults to live, and the two will disagree.

    A stored value the catalog no longer offers — a choice removed in a later
    release, or a hand-edited row — resolves to the default rather than being
    handed to a browser that has no such option to select. The row is left
    alone; reads report what the app can actually honor.
    """
    resolved = defaults()
    if not device_id:
        return resolved
    rows = (
        db.query(MemberPreference)
        .filter(
            MemberPreference.family_member_id == member_id,
            MemberPreference.device_id == device_id,
        )
        .all()
    )
    for row in rows:
        if is_valid(row.pref_key, row.value):
            resolved[row.pref_key] = row.value
    return resolved


def preferences_for_device(db: Session, device_id: str) -> dict[int, dict[str, str]]:
    """Every member's answers on one device, keyed by member id.

    One query rather than one per member: Settings renders the whole family's
    rows for the device in front of it, and the calendar wants exactly one of
    them. Both get the same shape, resolved the same way.
    """
    from rally.models import FamilyMember

    members = db.query(FamilyMember.id).all()
    resolved = {member_id: defaults() for (member_id,) in members}
    if not device_id:
        return resolved

    rows = db.query(MemberPreference).filter(MemberPreference.device_id == device_id).all()
    for row in rows:
        answers = resolved.get(row.family_member_id)
        if answers is not None and is_valid(row.pref_key, row.value):
            answers[row.pref_key] = row.value
    return resolved


def set_preferences(
    db: Session, member_id: int, device_id: str, values: dict[str, str]
) -> dict[str, str]:
    """Write a partial set of answers for one member on one device.

    Partial: a setting left out keeps its answer, so Settings saves one
    dropdown without sending the others back. Returns the resolved map, which
    is what the caller hands to a client that must not have to know the
    defaults.

    A row is written even when the value matches the default — including
    ``auto`` itself. It records that somebody chose, which is what makes a
    later change to a default leave existing families where they are. Unknown
    keys and values are ignored here; the API rejects them with a 422 long
    before this, and storing a preference nothing will ever read is the failure
    mode worth avoiding twice.
    """
    if not device_id:
        return defaults()

    rows = {
        row.pref_key: row
        for row in db.query(MemberPreference)
        .filter(
            MemberPreference.family_member_id == member_id,
            MemberPreference.device_id == device_id,
        )
        .all()
    }
    written = False
    for key, value in (values or {}).items():
        if not is_valid(key, value):
            continue
        row = rows.get(key)
        if row is None:
            db.add(
                MemberPreference(
                    family_member_id=member_id,
                    device_id=device_id,
                    pref_key=key,
                    value=value,
                )
            )
        else:
            row.value = value
        written = True
    if written:
        db.commit()
    return preferences(db, member_id, device_id)


def delete_member_preferences(db: Session, member_id: int) -> int:
    """Drop every preference row for a member, on every device.

    Nothing enforces the reference, so deleting a member has to say so
    explicitly — the same reason ``notification_prefs.delete_preferences``
    exists and the same reason ``DELETE /api/events/{id}`` cascades its own
    attendees by hand.
    """
    deleted = (
        db.query(MemberPreference)
        .filter(MemberPreference.family_member_id == member_id)
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
    return deleted


def delete_device_preferences(db: Session, device_id: str) -> int:
    """Drop every preference row for a device, for every member.

    Forgetting a device is the only way these rows are ever cleaned up. A
    browser that clears its storage comes back as a new device and leaves the
    old one behind, so the list this feeds is not a nicety — without it the
    table grows rows nothing can reach and nobody can see.
    """
    deleted = (
        db.query(MemberPreference)
        .filter(MemberPreference.device_id == device_id)
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
    return deleted


def touch_device(db: Session, device_id: str, label: str | None = None) -> Device | None:
    """Record that this device exists, and when it was last seen.

    Devices register themselves rather than being created by hand: the id is a
    token the browser made up, so the first time Rally hears it is the first
    time the device exists. ``label`` is only ever written when one is given,
    so a page that merely says hello cannot overwrite a name somebody typed.
    """
    device_id = (device_id or "").strip()
    if not device_id:
        return None

    device = db.query(Device).filter(Device.id == device_id).first()
    if device is None:
        device = Device(id=device_id, label=(label or "").strip() or None)
        db.add(device)
    elif label is not None:
        device.label = label.strip() or None
    device.last_seen_at = now_utc()
    db.commit()
    db.refresh(device)
    return device


def devices(db: Session) -> list[Device]:
    """Every device Rally has heard from, most recently seen first."""
    return db.query(Device).order_by(Device.last_seen_at.desc()).all()


def answer_counts(db: Session) -> dict[str, int]:
    """How many stored answers each device carries.

    Shown beside a device in Settings so *"forget this device"* says what it is
    about to throw away, rather than asking somebody to guess.
    """
    counts: dict[str, int] = {}
    for (device_id,) in db.query(MemberPreference.device_id).all():
        counts[device_id] = counts.get(device_id, 0) + 1
    return counts


def catalog() -> list[dict]:
    """The catalog as plain data, for the API and the templates.

    Server-rendered into Settings and the calendar rather than fetched: this is
    configuration, not state, and putting it in the first paint is what keeps a
    slow e-ink repaint from showing an empty dropdown before filling it in.
    """
    return [
        {
            "key": setting.key,
            "label": setting.label,
            "description": setting.description,
            "choices": [
                {"value": choice.value, "label": choice.label} for choice in setting.choices
            ],
            "default": setting.default,
        }
        for setting in CATALOG
    ]
