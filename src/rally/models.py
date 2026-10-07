"""Rally database models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from rally import member_colors
from rally.database import Base
from rally.utils.timezone import now_utc


class FamilyMember(Base):
    """Family member model."""

    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # One of rally.member_colors.MEMBER_COLORS — a closed palette, not free hex.
    # The default is the darkest entry so a member created by a path that skips
    # auto-assignment is still legible rather than the old near-black #333333.
    color: Mapped[str] = mapped_column(String(7), default=member_colors.DEFAULT_COLOR)
    calendar_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # Deprecated, kept for migration compat
    pushover_user_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # This person's Pushover user/group key; NULL means "never notified"
    pushover_device: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # Optional Pushover device name; NULL means all of their devices
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class Calendar(Base):
    """Calendar feed model — each calendar is linked to a family member.

    Supports four types:
    - native: Rally's own events, stored in the ``events`` table (no URL)
    - ics: Public ICS feed URL (unauthenticated)
    - caldav_google: Google CalDAV via app-specific password
    - caldav_apple: Apple iCloud CalDAV via app-specific password

    A native calendar is a row here rather than a table of its own so that
    per-member ownership, the Settings CRUD screen, and the generator's join
    against ``family_members`` all apply to it unchanged — the fetch loop gains
    one branch instead of a parallel concept beside it.
    """

    __tablename__ = "calendars"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100))  # Display name, e.g. "Google Family"
    url: Mapped[str] = mapped_column(Text, default="")  # Feed/server URL; empty for native
    family_member_id: Mapped[int] = mapped_column(Integer)  # FK to family_members.id
    owner_email: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )  # For declined-event detection
    cal_type: Mapped[str] = mapped_column(
        String(20), default="ics"
    )  # native, ics, caldav_google, caldav_apple
    username: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )  # Email for CalDAV auth
    password: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # App-specific password for CalDAV
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class Setting(Base):
    """Key-value settings store."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class AISettingsHistory(Base):
    """Versioned snapshots of AI settings (agent_voice, family_context).

    A new row is inserted on every explicit save of either field. The active
    snapshot for each field is referenced from the settings table via the
    'current_agent_voice_history_id' / 'current_family_context_history_id'
    keys. Rollback re-points the reference and bumps last_used_at — no new
    row is inserted.
    """

    __tablename__ = "ai_settings_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    field_name: Mapped[str] = mapped_column(
        String(50), index=True
    )  # 'agent_voice' or 'family_context'
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    last_used_at: Mapped[datetime] = mapped_column(
        default=now_utc
    )  # Bumped whenever this row becomes the active version (save or rollback)


class LLMSettingsHistory(Base):
    """Versioned snapshots of the coupled LLM provider + model configuration.

    A new row is inserted on every explicit save of the LLM settings, capturing
    the provider and its model together as one unit (value is a JSON object
    {"provider": ..., "model": ...}). The active snapshot is referenced from
    the settings table via the 'current_llm_config_history_id' key. Rollback
    re-points the reference and bumps last_used_at — no new row is inserted.
    """

    __tablename__ = "llm_settings_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    field_name: Mapped[str] = mapped_column(
        String(50), index=True
    )  # Always 'llm_config' (kept for parity with ai_settings_history / future fields)
    value: Mapped[str] = mapped_column(Text)  # JSON: {"provider": ..., "model": ...}
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    last_used_at: Mapped[datetime] = mapped_column(
        default=now_utc
    )  # Bumped whenever this row becomes the active version (save or rollback)


class StemConceptHistory(Base):
    """History of STEM 'concept of the day' topics that have been used.

    One row per (title, used_on) usage. The generator loads concepts used within
    the last 60 days and instructs the LLM not to repeat those specific topics.
    """

    __tablename__ = "stem_concept_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))  # Concept name as generated
    field: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # Science, Technology, Engineering, or Math
    used_on: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (local date used)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class FollowedTeam(Base):
    """A team or racing series whose schedule appears in the daily summary.

    ``team_key`` is nullable rather than seeded with a fake team for racing: a
    racing series is a league-level subscription and forcing a team id would be
    a lie, the same way ``Todo.assigned_to IS NULL`` means "Everyone".

    ``radio_station`` lives here rather than on the event because for NFL, NHL
    and NASCAR a radio affiliation is a season-long constant that no feed
    carries. For MLB it is overridden per game by statsapi, which does.

    ``provider`` is stored rather than inferred from ``league`` so moving a
    league to a different source is a column update, not a branch in the fetch
    path.
    """

    __tablename__ = "followed_teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))  # espn | mlb
    league: Mapped[str] = mapped_column(String(30))  # e.g. hockey/nhl, racing/nascar-premier
    team_key: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )  # NULL for a racing series, which has no team
    label: Mapped[str] = mapped_column(String(100))  # Display name
    radio_station: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class SportsEventNotice(Base):
    """Record that a notable upcoming event has already been announced.

    Mirrors ``StemConceptHistory``: same problem (don't repeat yourself across
    days), same shape, same purge discipline. Without it a season opener would
    be announced in all fourteen morning summaries leading up to it.

    A notice is written once and never rewritten. Record-driven notability means
    an event can *become* notable partway through the window; it is announced
    the morning it first qualifies, with the reason true at that moment.
    """

    __tablename__ = "sports_event_notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)  # provider + id
    event_local_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, local
    announced_on: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, local
    notability_reason: Mapped[str | None] = mapped_column(
        String(60), nullable=True
    )  # The reason shown when it was announced; for debugging, never re-announced on
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class Event(Base):
    """A Rally-owned calendar event, or the template of a recurring series.

    Times are stored twice on purpose, and the pair is the point:

    - ``start_utc`` / ``end_utc`` are instants, and they are what orders a day
      correctly. ``end_utc`` is **exclusive**, matching ICS ``DTEND``.
    - ``start_date`` / ``end_date`` are local calendar dates, and they are what
      renders correctly. ``end_date`` is **inclusive**, because that is what a
      human means by "ends Friday" and what the edit form shows.

    Deriving either pair from the other at read time is precisely where the
    classic all-day off-by-one lives, so both are written once at the boundary
    and read verbatim afterwards.

    ``tzid`` is captured per event rather than read from the global
    ``local_timezone`` setting: changing the family's timezone must not re-time
    events that already exist.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_id: Mapped[int] = mapped_column(Integer)  # FK to calendars.id (cal_type='native')
    uid: Mapped[str] = mapped_column(String(200), unique=True, index=True)  # RFC 5545 UID
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    start_utc: Mapped[datetime] = mapped_column(DateTime)
    end_utc: Mapped[datetime] = mapped_column(DateTime)  # Exclusive
    start_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, local
    end_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, local, inclusive
    tzid: Mapped[str] = mapped_column(String(64), default="UTC")
    rrule: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # RFC 5545 RRULE body, no prefix; NULL means a single event
    series_end_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # Denormalized UNTIL; NULL means unbounded
    notify_minutes_before: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Push reminder lead time; NULL means no reminder
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (Index("ix_events_calendar_start", "calendar_id", "start_date"),)


class EventAttendee(Base):
    """Which family members an event belongs to.

    A join table rather than a JSON array on the event — breaking with
    ``MealPlan.attendee_ids`` — because a calendar is *filtered* by member
    ("show me Emma's week") and a meal plan never is. Filtering a JSON array
    in SQLite means loading the whole window and filtering in Python, which is
    fine for the planner's seven rows and not for a month. Notifications make
    the same case twice: the recipients of a reminder are exactly this table.
    """

    __tablename__ = "event_attendees"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    family_member_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)

    __table_args__ = (
        Index("ix_event_attendees_unique", "event_id", "family_member_id", unique=True),
    )


class EventOverride(Base):
    """One occurrence of a series that differs from the rest, or is gone.

    Keyed on ``occurrence_date`` — the local date the occurrence *originally*
    fell on, which stays its identity even after it is moved. An index into the
    series would have been simpler and wrong: it shifts the moment an earlier
    occurrence is cancelled.

    Every field is nullable, and NULL means "inherit from the series". A row
    with ``cancelled=True`` is a deleted occurrence (ICS ``EXDATE``).
    """

    __tablename__ = "event_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    occurrence_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, original local date
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    all_day: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    start_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    end_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Moves this one occurrence to another native calendar. NULL inherits the
    # series' calendar, so the owner-derived fields (color, member name, and
    # the attendee fallback the member filter reads) follow the series unless
    # this says otherwise.
    calendar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index("ix_event_overrides_unique", "event_id", "occurrence_date", unique=True),
    )


class EventNotification(Base):
    """Record that an event occurrence was pushed to one family member.

    Mirrors ``SportsEventNotice``: same problem (send once, not once per poll),
    same shape, same purge discipline. The unique index below *is* the
    send-once guarantee for reminders.

    ``status`` matters as much as the row's existence. A failed send is
    recorded rather than dropped — but it does not consume the dedupe slot,
    because a five-minute provider outage must not silently eat the day's
    reminders. The retry rewrites the same row.
    """

    __tablename__ = "event_notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    occurrence_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, local
    family_member_id: Mapped[int] = mapped_column(Integer)
    # reminder | manual | created | updated | deleted
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="sent")  # sent | failed
    detail: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)

    __table_args__ = (
        Index(
            "ix_event_notifications_unique",
            "event_id",
            "occurrence_date",
            "family_member_id",
            "kind",
            unique=True,
        ),
    )


class MemberNotificationPref(Base):
    """One family member's answer for one kind of notification.

    Whether somebody has a Pushover key was the only per-person lever Rally
    had, and it decided five different kinds of notification at once. This
    table is the narrower one: Dad can hear about the shopping list without
    hearing every calendar edit.

    **An absent row means the kind's default** — the same discipline
    ``todo_notify_enabled`` follows, where the row only exists once somebody
    has expressed a preference. So installing this feature changes nobody's
    behavior, and a kind added later inherits its own default for free rather
    than needing a backfill.

    A preference can only ever *narrow* what somebody already receives: it is
    applied after the kind's audience rule, never instead of it. Ticking
    ``event_reminder`` does not start sending you other people's appointments.

    No foreign key, matching ``event_attendees`` and ``shopping_items``:
    resolution always starts from a member row, so a stray orphan can never
    grant anybody a notification. ``DELETE /api/family/{id}`` clears these rows
    explicitly all the same.
    """

    __tablename__ = "member_notification_prefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    family_member_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(40))  # one of notification_prefs.KINDS
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index("ix_member_notification_prefs_unique", "family_member_id", "kind", unique=True),
    )


class Device(Base):
    """One browser Rally has heard from, and what the family calls it.

    Devices register themselves: the id is a token the browser generated and
    kept in its own storage, so the first time Rally sees it is the first time
    the device exists. There is no enrollment step and nothing to pair — the
    household is already behind one front door, and a device that turns up
    unknown is a family member opening Rally, not an intruder.

    ``label`` starts as the browser's own coarse guess ("iPhone", "Mac") and is
    there to be corrected. It is what makes the device list legible: a column
    of random tokens answers no question anybody has, and *"forget this
    device"* is unusable if you cannot tell which one it is.

    ``last_seen_at`` is the other half of that. A browser that clears its
    storage comes back as a new device and leaves the old row behind, so the
    list needs to say which entries are still alive.
    """

    __tablename__ = "devices"

    # A client-generated token, not an integer: the browser has to be able to
    # mint it offline and keep using the same one, which a server-assigned id
    # cannot do without a round trip on first paint.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(default=now_utc)


class MemberPreference(Base):
    """One family member's answer for one behavioral setting on one device.

    The sibling of ``MemberNotificationPref``: that table decides who *hears*
    about what, this one decides what a screen *does* when they open it. Both
    are catalogs rather than a column per idea, so the next setting is an entry
    in ``rally.member_prefs`` and no schema change at all.

    **The key is the pair.** A preference belongs to a person *on a device*,
    not to a person and not to a class of device. An earlier cut keyed the
    second half on a media query — phone or computer — which is a guess about a
    device dressed up as a fact about one: the kitchen wall tablet and the desk
    laptop are both "a computer" by width and want opposite things. The device
    is what a person actually configures, so the device is what the answer
    hangs on.

    **An absent row means the setting's default**, which is always ``auto`` —
    Rally's own rule, the behavior that predates this table. So upgrading moves
    nobody's screen, and a device nobody has configured is not a device with a
    missing answer.

    No foreign key on either half, matching the tables beside it: resolution
    always starts from a member row and a device row, so a stray orphan can
    never change anybody's screen. Deleting a member and forgetting a device
    each clear these rows explicitly.
    """

    __tablename__ = "member_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    family_member_id: Mapped[int] = mapped_column(Integer, index=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    pref_key: Mapped[str] = mapped_column(String(40))  # one of member_prefs.SETTING_KEYS
    value: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index(
            "ix_member_preferences_unique",
            "family_member_id",
            "device_id",
            "pref_key",
            unique=True,
        ),
    )


class DashboardSnapshot(Base):
    """Dashboard snapshot model - stores generated daily summary data."""

    __tablename__ = "dashboard_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    timestamp: Mapped[datetime] = mapped_column(default=now_utc)
    data: Mapped[dict] = mapped_column(JSON)  # Stores the JSON response from Claude
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class Todo(Base):
    """Todo item model."""

    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    assigned_to: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to family_members.id
    recurring_todo_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to recurring_todos.id
    remind_days_before: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Days before due_date to start showing in LLM briefings
    completed: Mapped[bool] = mapped_column(default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class RecurringTodo(Base):
    """Recurring todo template model."""

    __tablename__ = "recurring_todos"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurrence_type: Mapped[str] = mapped_column(String(20))  # daily, weekly, monthly, custom
    recurrence_day: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 0-6 for weekly, 1-31 for monthly
    custom_rule: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # rule dict for custom recurrence type
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_due_date: Mapped[bool] = mapped_column(default=False)
    remind_days_before: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Days before due_date to start showing in LLM briefings
    start_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # YYYY-MM-DD: the earliest date this series may fire; NULL means "from today"
    last_generated_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # YYYY-MM-DD: recurrence date of the most recently generated instance
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class ShoppingStore(Base):
    """A user-defined store items can be grouped under (Costco, Hardware Store, …).

    There is deliberately no seeded "Anywhere" row: an item with no particular
    store has ``store_id IS NULL``, mirroring ``Todo.assigned_to IS NULL``
    meaning "Everyone".
    """

    __tablename__ = "shopping_stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))  # Unique case-insensitively (see index below)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index(
            "ix_shopping_stores_name_nocase",
            text("name COLLATE NOCASE"),
            unique=True,
        ),
    )


class ShoppingItem(Base):
    """An item on the family shopping list.

    Completion uses the same column names and semantics as ``Todo`` so the two
    routers read alike: a completed item stays visible until local midnight.
    Rows completed more than PURCHASED_RETENTION_DAYS ago are purged from the
    database — safe because every add is separately recorded in
    ``ShoppingItemHistory``, which autocomplete reads instead.
    """

    __tablename__ = "shopping_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    store_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to shopping_stores.id; NULL is the "Anywhere" catch-all
    completed: Mapped[bool] = mapped_column(default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Hand-arranged position *within a store group*, ascending — the order the
    # aisles are walked in, which is why it is per-store rather than global.
    # Values are only ever compared, never counted on to be contiguous: a
    # cross-store drag leaves a gap behind it and nothing renumbers the group it
    # left. New items get `min - 1` so an add still lands at the top, which is
    # what `created_at DESC` did before this column existed.
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class ShoppingItemHistory(Base):
    """Permanent, deduplicated record of every item name ever added.

    Powers autocomplete. Deliberately outlives the purchased-item purge: the
    30-day retention on ``shopping_items`` trims the purchased list without
    touching the family's vocabulary. ``store_id`` is the *most recently used*
    store rather than the most common one — a true mode would need a row per
    (name, store) pair, which un-deduplicates the table this counter exists to
    keep small.
    """

    __tablename__ = "shopping_item_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_key: Mapped[str] = mapped_column(
        String(200), unique=True, index=True
    )  # Trimmed + casefolded name; the dedupe key
    name: Mapped[str] = mapped_column(String(200))  # Display casing from the most recent add
    store_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Most recently used store
    times_added: Mapped[int] = mapped_column(Integer, default=1)
    last_added_at: Mapped[datetime] = mapped_column(default=now_utc)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class MealPlan(Base):
    """Meal plan model - meal plans by date.

    The table keeps its original name, from when the feature only planned
    dinners. Renaming it would take a migration, and ``dev``/``seed`` create
    tables without running migrations, so a renamed model would quietly get a
    new, empty table beside the real one.
    """

    __tablename__ = "dinner_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (multiple plans per date allowed)
    meal_type: Mapped[str] = mapped_column(
        String(20), default="Dinner"
    )  # Breakfast, Lunch, Dinner, Snacks
    plan: Mapped[str] = mapped_column(Text)
    attendee_ids: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )  # JSON array of family_member IDs (who's eating)
    cook_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to family_members.id (who's cooking)
    rating: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 1-5 star rating; null means not yet reviewed
    review: Mapped[str | None] = mapped_column(Text, nullable=True)  # Free-text review of the meal
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)", name="ck_dinner_plan_rating"
        ),
    )


class Note(Base):
    """One day's note — free text the family writes ahead of the day itself.

    The column is ``body`` rather than ``note`` on purpose: ``note``/``notes``
    already means "an annotation on something else" three times over
    (``ShoppingItem.note``, ``PrepItem.notes``, and a schedule item's ``notes``
    on the dashboard), and this column is the record's whole content.

    ``date`` is unique, and that index is the only thing enforcing one note per
    day. Nothing else in the stack depends on the uniqueness holding, so it has
    to hold here.
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # YYYY-MM-DD
    body: Mapped[str] = mapped_column(Text)  # Markdown source as the family typed it
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class PrepLocation(Base):
    """A place preparedness stock lives: Garage shelf, Truck, Bug-out bag.

    Mirrors ``ShoppingStore`` exactly, including the case-insensitive unique
    index and the absence of a seeded catch-all row: an item with no place has
    ``location_id IS NULL``, the same convention as ``ShoppingItem.store_id``
    and ``Todo.assigned_to``.

    ``sort_order`` is the one addition the shopping stores do not have. A go
    list is *walked* in physical order — truck, then garage, then basement —
    and alphabetical is the wrong order to pack in. Ties break alphabetically.
    """

    __tablename__ = "prep_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index("ix_prep_locations_name_nocase", text("name COLLATE NOCASE"), unique=True),
    )


class PrepItem(Base):
    """An item of preparedness stock.

    ``quantity`` is free text, stored and displayed verbatim ("3 cases",
    "~10 pr", "8-10"). With no par levels or low-stock alerts in scope, a
    parsed integer would be structure bought for features that are not being
    built and paid for on every entry. The tradeoff is accepted: quantity
    cannot be sorted, summed or compared.

    ``next_refresh_date`` is *stored* rather than derived from
    ``last_refreshed_on`` plus the interval. It is the single value the
    refresh sweep reads and it is indexed, the same call ``RecurringTodo``
    makes with ``last_generated_date``. Every write path that can move it
    recomputes it (see ``rally.preparedness``).

    Dates are ``String(10)`` YYYY-MM-DD like ``Todo.due_date``: they are days
    on a wall calendar, not instants, and must never have a timezone applied
    twice.
    """

    __tablename__ = "prep_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to prep_locations.id; NULL is the "Unassigned" catch-all
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- refresh schedule ---
    refresh_mode: Mapped[str] = mapped_column(String(10), default="none")  # none | date | interval
    refresh_interval_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_refresh_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True, index=True
    )  # YYYY-MM-DD; the only column the sweep reads
    remind_days_before: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Lead time, same name and semantics as Todo.remind_days_before
    last_refreshed_on: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD

    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        CheckConstraint(
            "refresh_mode IN ('none','date','interval')", name="ck_prep_item_refresh_mode"
        ),
    )


class PrepRefreshNotice(Base):
    """Record that an item's refresh for a given date has been announced.

    Mirrors ``SportsEventNotice`` and ``EventNotification``: written once,
    never rewritten, and the reason the sweep is safe to run every minute
    forever, across restarts and clock changes. Without it the family would be
    told about the same canned food every single morning until they dealt with
    it.

    The key is a *string*, ``f"{item_id}:{refresh_date}"``. Keying on the pair
    rather than on the item is what re-arms an item for free when its date
    moves: new date, new key, no row, announce normally. No cleanup code and no
    flag to reset. It is also what would make overdue escalation cheap later —
    a cycle number folds into the same key with no schema change.
    """

    __tablename__ = "prep_refresh_notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    notice_key: Mapped[str] = mapped_column(
        String(80), unique=True, index=True
    )  # f"{item_id}:{refresh_date}"
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    refresh_date: Mapped[str] = mapped_column(String(10))  # the date announced
    sent_on: Mapped[str] = mapped_column(String(10))  # local date the push went out
    recipients: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # Comma-separated member names, for the "did it send?" view
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class PrepReview(Base):
    """A stored LLM review of the preparedness inventory.

    Reviews are snapshotted rather than recomputed on view, following
    ``DashboardSnapshot``: the call costs real money and several seconds, so a
    page load must never trigger one. ``POST`` runs a review, ``GET`` reads the
    last one back, and the timestamp is shown so a stale review is obviously
    stale rather than quietly wrong.

    ``item_count`` is stored alongside the payload so the UI can say "reviewed
    when you had 38 items, you now have 44" — the cheapest possible staleness
    signal, and the one that actually matters after a big restock.
    """

    __tablename__ = "prep_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)  # The parsed review object
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Which model produced it
    item_count: Mapped[int] = mapped_column(Integer, default=0)  # Inventory size at review time
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class CalendarCache(Base):
    """Cached occurrences for one external calendar.

    Reads must not touch the network. The `/calendar` page was fetching every
    remote feed synchronously on every request — measured at 11.5s against
    three sources, of which 8.9MB of ICS was one — so the page spent its whole
    life on "Loading calendar…". Native events stay live because they are a
    local query (0.13s); only external sources are cached.

    ``occurrences`` holds the already-expanded window as JSON. Caching the raw
    feed text instead would look tidier, but neither feed here sends an ETag or
    a Last-Modified, so a read would still pay the 4s download; and the parse
    and recurrence expansion is another 2.4s on the large feed. Expanded and
    stored is the only shape that makes a read free.

    ``content_hash`` is what makes a sync *incremental*: when the fetched body
    is byte-identical to last time, the expansion is skipped and only
    ``fetched_at`` moves. ``etag`` / ``last_modified`` are sent as conditional
    request headers when the server offers them — neither of this install's
    feeds does, but Apple, Fastmail and Nextcloud all do, and a 304 skips the
    download as well as the parse.
    """

    __tablename__ = "calendar_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    occurrences: Mapped[list] = mapped_column(JSON, default=list)  # serialized Occurrence dicts
    window_start: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, local
    window_end: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD, local, exclusive

    # Incremental-sync bookkeeping.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # CalDAV only: one RFC 6578 sync token per server-side calendar under the
    # principal. Handing these back asks "what changed?" without downloading
    # anything, which is the CalDAV equivalent of the ICS fingerprint.
    sync_tokens: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(100), nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(default=now_utc)  # last successful contact
    changed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # last time the content actually differed
    # A failing feed keeps serving its last good occurrences rather than
    # blanking the calendar; the error travels alongside so the UI can say so.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    # Set when a feed rate-limits us: the earliest time the background sync may
    # try this calendar again. Syncing every five minutes across several feeds
    # is enough traffic that a provider will eventually say "not so fast", and
    # the wrong answer to a 429 is to come back in five minutes and ask again.
    # Only the automatic path honors it; the Refresh button is a person asking,
    # and it stays able to try.
    retry_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class PackingListTemplate(Base):
    """A reusable packing list: Swim at Nana's, Beach day, School backpack.

    This is the master. It is never checked off itself — a ``PackingListDay``
    puts it on a date, and checking happens there. ``pack_days_before`` says
    how many days ahead that day's copy should be packed (0 is the day itself),
    which is what the daily summary keys its reminder on. A day can set its own
    (``PackingListDay.pack_days_before``); this is the default.

    ``name`` is unique case-insensitively, like store and location names, so
    two templates can never read the same.
    """

    __tablename__ = "packing_list_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pack_days_before: Mapped[int] = mapped_column(Integer, default=0)  # 0 is the day itself
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index("ix_packing_list_templates_name_nocase", text("name COLLATE NOCASE"), unique=True),
        # Named in migration 034, before the table was renamed; a renamed table
        # keeps its constraints, and renaming this one would mean a rebuild.
        CheckConstraint("pack_days_before >= 0", name="ck_packing_list_pack_days_before"),
    )


class PackingListBag(Base):
    """A bag things are packed into: a backpack, Pool bag, Car.

    One household list, shared by every template and every day, like
    ``ShoppingStore``. Items name their bag in free text and a new name joins
    this list, so it grows as it is used; the Manage bags modal renames and
    removes. An item in no bag has ``bag_id IS NULL`` and reads as "No bag".

    A bag is unique by its name (ignoring case) **and** its owner, so Emma and
    Jake can each have a "Backpack". No owner counts as one owner of its own,
    hence ``IFNULL(owner_id, 0)`` in the index: SQLite treats NULLs as
    distinct, which would otherwise allow any number of ownerless "Backpack"s.
    A typed name finds the item owner's bag, then the ownerless one
    (``rally.packing_lists.bag_named``).

    ``owner_id`` (a family member; NULL is Everyone) and ``parent_bag_id``
    (the bag it goes in; NULL is none) are the household's defaults. A
    template or a day can read a bag differently (``PackingListTemplateBag``,
    ``PackingListDayBag``); ``rally.packing_lists.resolve_day_bags`` and
    ``resolve_template_bags`` are where the three are laid over each other.
    """

    __tablename__ = "packing_list_bags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FK to family_members.id
    parent_bag_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to packing_list_bags.id; the bag this one goes in
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index(
            "ix_packing_list_bags_name_owner_nocase",
            text("name COLLATE NOCASE"),
            text("IFNULL(owner_id, 0)"),
            unique=True,
        ),
    )


class PackingListTemplateItem(Base):
    """One thing to pack, on a packing list template.

    Items live on the template only. A day's copy never duplicates them — it
    records which of them are checked (``PackingListDayCheck``) and its own
    changes (``PackingListDayItem``) — so an edit here reaches every day the
    template is on, with nothing to sync.

    ``owner_id`` (a family member; NULL is "Everyone") and ``bag_id`` (NULL is
    "No bag") are the two ways the page groups a packing list: who owns what,
    and what goes in each bag. ``sort_order`` is one order for the whole
    template; each view reads it within its groups. It is compared, never
    counted on to be contiguous, the ``ShoppingItem.sort_order`` contract, and
    a new item goes to the bottom: a packing list is entered top to bottom.
    """

    __tablename__ = "packing_list_template_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    packing_list_template_id: Mapped[int] = mapped_column(
        Integer, index=True
    )  # FK to packing_list_templates.id
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FK to family_members.id
    bag_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FK to packing_list_bags.id
    name: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class PackingListItemHistory(Base):
    """Every item name somebody has typed onto a packing list, for autocomplete.

    The packing list counterpart of ``ShoppingItemHistory``, deduplicated on
    ``name_key`` (trimmed and casefolded), with the casing it was last typed
    in and the owner and bag it last had. Typing a name (adding an item, or
    renaming one) creates its row; nothing else does, so a suggestion somebody
    forgot stays forgotten until somebody types it again.

    ``times_added`` is how many past days the name was on a packing list —
    what was packed, not what was typed. ``rally.packing_lists.count_packed_days``
    adds to it once a day is over, so a list taken off a day before then was
    never counted and has nothing to undo. A newly typed name starts at 0.
    """

    __tablename__ = "packing_list_item_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bag_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    times_added: Mapped[int] = mapped_column(Integer, default=0)  # Past days it was on a list
    last_added_at: Mapped[datetime] = mapped_column(default=now_utc)  # Last typed

    __table_args__ = (Index("ix_packing_list_item_history_name_key", "name_key", unique=True),)


class PackingListDay(Base):
    """A packing list on a date: "Swim at Nana's" on Saturday.

    Usually a template put on a day. That day holds no copy of the template's
    items — only its checks and its own changes (``PackingListDayItem``) — and
    ``(packing_list_template_id, date)`` is unique, because the same template
    twice on one day would be two copies of one bag.

    A **templateless** day has ``packing_list_template_id IS NULL``: its
    template was deleted (its items were copied onto it first, see
    ``rally.packing_lists.delete_template``). It carries its own ``name``,
    ``description`` and ``pack_days_before``, and every item on it is one of
    its own. SQLite treats NULLs as distinct in a unique index, so any number
    of templateless days can share a date.

    ``item_order`` is the day's hand-arranged order, a list of
    ``"template:<id>"`` / ``"day:<id>"`` keys; NULL is the default order (the
    template's, then the day's own). Nothing enforces its contents, so
    ``rally.packing_lists.resolve_day`` reads it leniently.

    ``date`` is ``String(10)`` YYYY-MM-DD like ``Todo.due_date``: a day on a
    wall calendar, never an instant.
    """

    __tablename__ = "packing_list_days"

    id: Mapped[int] = mapped_column(primary_key=True)
    packing_list_template_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )  # FK to packing_list_templates.id; NULL for a templateless day
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Templateless only
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # Templateless only
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    schedule_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )  # FK to packing_list_template_schedules.id; NULL for a day added by hand
    pack_days_before: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # This day's own lead time; NULL follows the template's. Required when templateless
    # Set once somebody edits this day's label: a schedule's relabel skips it.
    label_edited: Mapped[bool] = mapped_column(default=False)
    item_order: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index(
            "ix_packing_list_days_packing_list_template_date",
            "packing_list_template_id",
            "date",
            unique=True,
        ),
    )


class PackingListDayItem(Base):
    """One day's own change to its packing list: the overlay a day keeps on top.

    A day still follows its template — an item added to the template shows up
    on every day — and this table holds what is true of one day only:

    * ``template_item_id`` set: a template item as it reads *on this day*.
      ``name``, ``note``, ``owner_id`` and ``bag_id`` are the day's full
      values, copied when it was first edited here, so a later edit to the
      template no longer reaches this one item on this one day. ``removed``
      takes it off this day without touching the template. Its check still
      lives in ``PackingListDayCheck``, like any template item.
    * ``template_item_id`` NULL: an item only this day has, after the
      template's items in its own ``sort_order``, with its own owner and bag,
      and its own ``checked``, since ``PackingListDayCheck`` names template
      items only. Every item on a templateless day is one of these.

    Unique per ``(day_id, template_item_id)`` where ``template_item_id`` is
    set: one reading of a template item per day.
    """

    __tablename__ = "packing_list_day_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    day_id: Mapped[int] = mapped_column(Integer, index=True)  # FK to packing_list_days.id
    template_item_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )  # FK to packing_list_template_items.id; NULL for an item only this day has
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bag_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)  # Day-only items
    removed: Mapped[bool] = mapped_column(default=False)  # Template items only
    checked: Mapped[bool] = mapped_column(default=False)  # Day-only items only
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index(
            "ix_packing_list_day_items_day_template_item",
            "day_id",
            "template_item_id",
            unique=True,
            sqlite_where=text("template_item_id IS NOT NULL"),
        ),
    )


class PackingListTemplateSchedule(Base):
    """A template put on days by a repeating rule: "School backpack" every weekday.

    The recurrence columns are named exactly as on ``RecurringTodo``
    (``recurrence_type``, ``recurrence_day``, ``custom_rule``, ``start_date``,
    ``last_generated_date``) because ``rally.recurrence`` reads them by name:
    a schedule is handed to the same date functions a recurring task is, so
    there is one place that knows what "the first Sunday" means.

    A schedule creates ordinary ``PackingListDay`` rows ahead of time (see
    ``rally.packing_lists.process_schedules``) and has no hold over them after
    that. ``last_generated_date`` is the high-water mark: a date at or before
    it is never generated again, which is what keeps a day somebody removed
    from coming back. Pausing, ending or editing a schedule therefore leaves
    the days it already made exactly where they are — except its ``label``,
    which names those days, so a new one relabels its days from today on.
    """

    __tablename__ = "packing_list_template_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    # One schedule per template: a template either repeats or it does not.
    packing_list_template_id: Mapped[int] = mapped_column(
        Integer, index=True, unique=True
    )  # FK to packing_list_templates.id
    recurrence_type: Mapped[str] = mapped_column(String(20))  # daily, weekly, monthly, custom
    recurrence_day: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 0-6 (Monday first) for weekly, 1-31 for monthly
    custom_rule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    start_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # YYYY-MM-DD: the first day it may land on; NULL means "from today"
    end_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # YYYY-MM-DD: the last day it may land on, inclusive; NULL means "no end"
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)  # Copied onto each day
    last_generated_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class PackingListDayCheck(Base):
    """A template item checked off on one day. The row's existence *is* the check.

    Keyed on ``(day_id, template_item_id)``, which is the whole reason checking
    off on Saturday cannot touch the template or any other day: there is no
    column on either for it to write. Unchecking deletes the row.
    """

    __tablename__ = "packing_list_day_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    day_id: Mapped[int] = mapped_column(Integer, index=True)  # FK to packing_list_days.id
    template_item_id: Mapped[int] = mapped_column(
        Integer, index=True
    )  # FK to packing_list_template_items.id
    checked_at: Mapped[datetime] = mapped_column(default=now_utc)

    __table_args__ = (
        Index(
            "ix_packing_list_day_checks_day_template_item",
            "day_id",
            "template_item_id",
            unique=True,
        ),
    )


class PackingListTemplateBag(Base):
    """A template's reading of a bag: whose it is and what it goes in, on that
    template — "on Beach week the suitcase is Dad's".

    Both fields are the template's, copied whole when the reading is made, the
    rule a day's reading of an item follows: the row existing is the change,
    and deleting it (Reset) hands the bag back to the household's defaults.
    A day the template is on reads it, unless that day has its own
    (``PackingListDayBag``). Unique per ``(packing_list_template_id, bag_id)``.
    """

    __tablename__ = "packing_list_template_bags"

    id: Mapped[int] = mapped_column(primary_key=True)
    packing_list_template_id: Mapped[int] = mapped_column(
        Integer, index=True
    )  # FK to packing_list_templates.id
    bag_id: Mapped[int] = mapped_column(Integer, index=True)  # FK to packing_list_bags.id
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FK to family_members.id
    parent_bag_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to packing_list_bags.id
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        Index(
            "ix_packing_list_template_bags_template_bag",
            "packing_list_template_id",
            "bag_id",
            unique=True,
        ),
    )


class PackingListDayBag(Base):
    """One day's reading of a bag: whose it is and what it goes in, that day
    only. Wins over the template's reading and the household's defaults.

    Copied whole, like ``PackingListTemplateBag``. Unique per ``(day_id, bag_id)``.
    """

    __tablename__ = "packing_list_day_bags"

    id: Mapped[int] = mapped_column(primary_key=True)
    day_id: Mapped[int] = mapped_column(Integer, index=True)  # FK to packing_list_days.id
    bag_id: Mapped[int] = mapped_column(Integer, index=True)  # FK to packing_list_bags.id
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FK to family_members.id
    parent_bag_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to packing_list_bags.id
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (Index("ix_packing_list_day_bags_day_bag", "day_id", "bag_id", unique=True),)


class PackingListDayBagCheck(Base):
    """A bag grabbed on one day. The row's existence *is* the check, as with
    ``PackingListDayCheck``.

    A bag is on a day only while something on the day is in it (or in a bag
    inside it), so a check whose bag has left the day is deleted rather than
    kept for its return (``rally.packing_lists.prune_bag_checks``).
    """

    __tablename__ = "packing_list_day_bag_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    day_id: Mapped[int] = mapped_column(Integer, index=True)  # FK to packing_list_days.id
    bag_id: Mapped[int] = mapped_column(Integer, index=True)  # FK to packing_list_bags.id
    checked_at: Mapped[datetime] = mapped_column(default=now_utc)

    __table_args__ = (
        Index("ix_packing_list_day_bag_checks_day_bag", "day_id", "bag_id", unique=True),
    )
