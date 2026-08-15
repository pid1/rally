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

from rally.database import Base
from rally.utils.timezone import now_utc


class FamilyMember(Base):
    """Family member model."""

    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(7), default="#333333")  # Hex color for UI
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
    ``DinnerPlan.attendee_ids`` — because a calendar is *filtered* by member
    ("show me Emma's week") and a dinner plan never is. Filtering a JSON array
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
    kind: Mapped[str] = mapped_column(String(20))  # reminder | manual
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


class DinnerPlan(Base):
    """Meal plan model - meal plans by date."""

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
