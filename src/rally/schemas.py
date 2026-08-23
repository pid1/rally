"""Rally Pydantic schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from rally import notification_prefs

# Sentinel value to distinguish "field not provided" from "field set to None"
UNSET = object()

# Family Members


class FamilyMemberBase(BaseModel):
    name: str
    color: str = "#333333"
    # Pushover profile. A member without a key is simply never notified — that
    # is the default, not an error state.
    pushover_user_key: str | None = None
    pushover_device: str | None = None


def _check_notification_kinds(values: dict[str, bool] | None) -> dict[str, bool] | None:
    """Reject a preference for a kind Rally does not send.

    A typo'd key would otherwise be stored forever and read by nothing, which
    looks exactly like a preference that quietly stopped working. Raising here
    makes it a 422 at the door instead.
    """
    if not values:
        return values
    unknown = sorted(set(values) - set(notification_prefs.KIND_KEYS))
    if unknown:
        known = ", ".join(notification_prefs.KIND_KEYS)
        raise ValueError(f"Unknown notification kind(s): {', '.join(unknown)}. Known: {known}")
    return values


class FamilyMemberCreate(FamilyMemberBase):
    # Omitted means "the defaults" — everything on except shopping additions.
    notifications: dict[str, bool] | None = None

    @field_validator("notifications")
    @classmethod
    def check_notification_kinds(cls, values):
        return _check_notification_kinds(values)


class FamilyMemberUpdate(BaseModel):
    name: str | None = None
    color: str | None = None
    pushover_user_key: str | None = UNSET  # None means "clear"; UNSET means "not provided"
    pushover_device: str | None = UNSET
    # A *partial* map: kinds left out keep whatever they resolve to today.
    # UNSET means "not provided", the same distinction the fields above draw.
    notifications: dict[str, bool] | None = UNSET

    @field_validator("notifications")
    @classmethod
    def check_notification_kinds(cls, values):
        return _check_notification_kinds(values)


class FamilyMemberResponse(FamilyMemberBase):
    id: int
    created_at: datetime
    updated_at: datetime
    # Resolved values with the defaults already filled in, so no client has to
    # know what the defaults are. This is the preference alone: somebody with
    # no Pushover key still has one, and it takes effect the moment a key is
    # added rather than needing a second trip through Settings.
    notifications: dict[str, bool] = {}

    model_config = ConfigDict(from_attributes=True)


# Calendars


class CalendarBase(BaseModel):
    label: str
    url: str = ""  # Empty for a native calendar, which has nothing to fetch
    family_member_id: int
    owner_email: str | None = None
    cal_type: str = "ics"  # native, ics, caldav_google, caldav_apple
    username: str | None = None  # Email for CalDAV auth
    password: str | None = None  # App-specific password for CalDAV


class CalendarCreate(CalendarBase):
    pass


class CalendarUpdate(BaseModel):
    label: str | None = None
    url: str | None = None
    family_member_id: int | None = None
    owner_email: str | None = None
    cal_type: str | None = None
    username: str | None = None
    password: str | None = None


class CalendarResponse(CalendarBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_calendar(cls, cal) -> CalendarResponse:
        """Build response from a Calendar model, never exposing the password."""
        return cls(
            id=cal.id,
            label=cal.label,
            url=cal.url,
            family_member_id=cal.family_member_id,
            owner_email=cal.owner_email,
            cal_type=cal.cal_type or "ics",
            username=cal.username,
            password=None,
            created_at=cal.created_at,
            updated_at=cal.updated_at,
        )


# Settings


class SettingsUpdate(BaseModel):
    """Bulk settings update — key/value pairs."""

    settings: dict[str, str]


class SettingsResponse(BaseModel):
    """All settings as a flat dict."""

    settings: dict[str, str]


# AI Settings (versioned agent_voice / family_context)

AI_SETTINGS_FIELDS = ("agent_voice", "family_context")


class AISettingValueUpdate(BaseModel):
    """Explicit save of an AI settings field — creates a new history snapshot."""

    value: str


class AISettingRollback(BaseModel):
    """Roll an AI settings field back to an existing history snapshot."""

    history_id: int


class AISettingState(BaseModel):
    """Currently active value of an AI settings field."""

    field_name: str
    value: str
    history_id: int | None = None  # None when no snapshot exists yet


class AISettingHistoryEntry(BaseModel):
    """One snapshot row from ai_settings_history."""

    id: int
    field_name: str
    value: str
    created_at: datetime
    last_used_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AISettingHistoryResponse(BaseModel):
    """Version history for one AI settings field, newest first."""

    field_name: str
    current_history_id: int | None = None
    history: list[AISettingHistoryEntry]


# LLM Config (versioned provider + model, coupled as a single snapshot)

LLM_CONFIG_FIELD = "llm_config"


# Default token budget mirroring generate.LLM_MAX_TOKENS. Not imported directly —
# generate.py is the generator's own module and schemas.py stays free of it to
# avoid a needless cross-module coupling for one constant.
DEFAULT_LLM_MAX_TOKENS = 4000

LLMMaxTokensMode = Literal["model_max", "custom"]


class LLMConfigUpdate(BaseModel):
    """Explicit save of the LLM provider + model pair — creates a new history snapshot.

    ``max_tokens`` is always sent by the client (whatever is currently shown in
    the field); in ``model_max`` mode the server ignores it and resolves the
    real value from the provider instead. ``max_tokens_mode`` is meaningful for
    Anthropic only — the router forces it to ``custom`` for every other provider.

    The "must be positive" rule is deliberately NOT enforced here as a Field
    constraint: the browser sends a blank/zero placeholder in ``model_max``
    mode (the field is read-only and not yet resolved), and that value is
    correctly ignored downstream — a schema-level gt=0 would reject the
    request before the handler ever gets to ignore it. The router validates
    positivity itself, and only when max_tokens_mode == "custom".
    """

    provider: str
    model: str
    max_tokens: int = DEFAULT_LLM_MAX_TOKENS
    max_tokens_mode: LLMMaxTokensMode = "custom"


class LLMConfigState(BaseModel):
    """Currently active LLM provider + model configuration."""

    provider: str
    model: str
    max_tokens: int | None = None  # None when no snapshot exists yet
    max_tokens_mode: LLMMaxTokensMode | None = None
    history_id: int | None = None  # None when no snapshot exists yet


class LLMConfigHistoryEntry(BaseModel):
    """One snapshot row from llm_settings_history, with the coupled value unpacked."""

    id: int
    provider: str
    model: str
    max_tokens: int
    max_tokens_mode: LLMMaxTokensMode
    created_at: datetime
    last_used_at: datetime


class LLMConfigHistoryResponse(BaseModel):
    """Version history for the LLM configuration, newest first."""

    current_history_id: int | None = None
    history: list[LLMConfigHistoryEntry]


# Todos


class TodoBase(BaseModel):
    title: str
    description: str | None = None
    due_date: str | None = None  # YYYY-MM-DD format
    assigned_to: int | None = None  # family_members.id
    remind_days_before: int | None = None  # Days before due_date to start LLM reminders


class TodoCreate(TodoBase):
    pass


class TodoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: str | None = (
        UNSET  # YYYY-MM-DD format; None means "clear"; UNSET means "not provided"
    )
    assigned_to: int | None = UNSET  # family_members.id; None means "Everyone"
    remind_days_before: int | None = UNSET  # Days before due_date; None means "always"
    completed: bool | None = None


class TodoResponse(TodoBase):
    id: int
    recurring_todo_id: int | None = None
    remind_days_before: int | None = None
    completed: bool
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CompletedTodoPage(BaseModel):
    """One page of previously completed todos."""

    items: list[TodoResponse]
    has_more: bool  # True when another page exists beyond this one
    total: int  # Total matches across all pages for the current query (search + filters)


# Recurring Todos


class RecurringTodoBase(BaseModel):
    title: str
    description: str | None = None
    recurrence_type: str  # daily, weekly, monthly, custom
    recurrence_day: int | None = None  # 0-6 for weekly, 1-31 for monthly
    assigned_to: int | None = None
    has_due_date: bool = False
    remind_days_before: int | None = None  # Days before due_date to start LLM reminders
    custom_rule: dict | None = None  # JSON rule for custom recurrence type
    start_date: str | None = None  # YYYY-MM-DD: earliest date the series may fire


class RecurringTodoCreate(RecurringTodoBase):
    pass


class RecurringTodoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    recurrence_type: str | None = None
    recurrence_day: int | None = None
    assigned_to: int | None = UNSET
    has_due_date: bool | None = None
    remind_days_before: int | None = UNSET  # Days before due_date; None means "always"
    active: bool | None = None
    custom_rule: dict | None = UNSET  # UNSET means not changing; None means clear
    start_date: str | None = UNSET  # UNSET means not changing; None means clear


class RecurringTodoResponse(RecurringTodoBase):
    id: int
    active: bool
    last_generated_date: str | None = None
    last_completed_date: str | None = None
    last_completed_at: datetime | None = None
    last_completed_display: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecurrencePreviewRequest(BaseModel):
    """An unsaved recurrence rule, asked what dates it would produce."""

    recurrence_type: str  # daily, weekly, monthly, custom
    recurrence_day: int | None = None
    custom_rule: dict | None = None
    start_date: str | None = None  # YYYY-MM-DD


class RecurrencePreviewResponse(BaseModel):
    """The next few dates that rule lands on, earliest first."""

    occurrences: list[str]  # YYYY-MM-DD


# Shopping List


class ShoppingStoreCreate(BaseModel):
    name: str


class ShoppingStoreUpdate(BaseModel):
    name: str


class ShoppingStoreResponse(BaseModel):
    id: int
    name: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShoppingItemCreate(BaseModel):
    name: str
    note: str | None = None
    store_id: int | None = None  # NULL / omitted means the "Anywhere" catch-all
    store: str | None = None  # Store *name*, for clients that know names but not ids

    @model_validator(mode="after")
    def check_single_store_reference(self):
        """``store_id`` and ``store`` are two spellings of one field, not both."""
        if self.store_id is not None and self.store is not None:
            raise ValueError("Provide either store_id or store, not both")
        return self


class ShoppingItemUpdate(BaseModel):
    name: str | None = None
    note: str | None = UNSET  # None means "clear"; UNSET means "not provided"
    store_id: int | None = UNSET  # None means "Anywhere"; UNSET means "not provided"
    completed: bool | None = None


class ShoppingItemResponse(BaseModel):
    id: int
    name: str
    note: str | None = None
    store_id: int | None = None
    completed: bool
    completed_at: datetime | None = None
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShoppingReorder(BaseModel):
    """The new contents of one store group, in the order they should read.

    A drag expresses both halves of a move at once — which store the item now
    belongs to, and where it sits among that store's items — so one request
    carries both rather than making the client issue a PUT and a reorder and
    hope neither half fails on its own. ``store_id`` is the *destination*;
    dragging across groups is just a reorder whose payload happens to include an
    item that used to live somewhere else.
    """

    store_id: int | None = None  # None is the "Anywhere" catch-all
    item_ids: list[int]


class ShoppingSuggestion(BaseModel):
    """One autocomplete match from the permanent item history."""

    id: int
    name: str
    store_id: int | None = None
    times_added: int

    model_config = ConfigDict(from_attributes=True)


# Meal Plans (stored in dinner_plans table)

MEAL_TYPES = ("Breakfast", "Lunch", "Dinner", "Snacks")


class DinnerPlanBase(BaseModel):
    date: str  # YYYY-MM-DD format
    meal_type: str = "Dinner"  # Breakfast, Lunch, Dinner, Snacks
    plan: str
    attendee_ids: list[int] | None = None  # family_member IDs (who's eating); None = everyone
    cook_id: int | None = None  # family_member ID (who's cooking)
    rating: int | None = None  # 1-5 star rating; null = not yet reviewed
    review: str | None = None  # Free-text review


class DinnerPlanCreate(DinnerPlanBase):
    pass


class DinnerPlanUpdate(BaseModel):
    date: str | None = None
    meal_type: str | None = None
    plan: str | None = None
    attendee_ids: list[int] | None = UNSET  # None means "clear"; UNSET means "not provided"
    cook_id: int | None = UNSET  # None means "clear"; UNSET means "not provided"


class DinnerPlanReviewUpdate(BaseModel):
    """Lightweight schema for submitting/editing a meal review."""

    rating: int | None = None  # 1-5; None means "clear rating"
    review: str | None = None  # Free-text; None means "clear review"


class DinnerPlanResponse(DinnerPlanBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FollowedTeamBase(BaseModel):
    provider: str = "espn"  # espn | mlb
    league: str  # e.g. hockey/nhl, racing/nascar-premier
    team_key: str | None = None  # None for a racing series, which has no team
    label: str
    radio_station: str | None = None
    active: bool = True


class FollowedTeamCreate(FollowedTeamBase):
    pass


class FollowedTeamUpdate(BaseModel):
    provider: str | None = None
    league: str | None = None
    team_key: str | None = UNSET  # None means "clear"; UNSET means "not provided"
    label: str | None = None
    radio_station: str | None = UNSET  # None means "clear"; UNSET means "not provided"
    active: bool | None = None


class FollowedTeamResponse(FollowedTeamBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Calendar events


class EventBase(BaseModel):
    """The shape a form submits.

    Times are **local wall times plus a zone name**, never UTC instants: the
    browser should not be doing timezone arithmetic, and a client that guesses
    wrong produces an event that is quietly an hour out. ``start``/``end`` are
    ``YYYY-MM-DD`` for an all-day event and ``YYYY-MM-DDTHH:MM`` otherwise, and
    an all-day ``end`` is the **inclusive** last day, matching what the field
    is labelled.
    """

    title: str
    description: str | None = None
    location: str | None = None
    all_day: bool = False
    start: str
    end: str | None = None
    tzid: str | None = None  # Defaults to the family's configured zone
    rrule: str | None = None  # RFC 5545 RRULE body; None means a single event
    notify_minutes_before: int | None = None
    attendee_ids: list[int] = []
    calendar_id: int | None = None  # Defaults to the family's first native calendar


class EventCreate(EventBase):
    pass


class EventUpdate(BaseModel):
    """Partial update. ``UNSET`` distinguishes "leave alone" from "clear"."""

    title: str | None = None
    description: str | None = UNSET
    location: str | None = UNSET
    all_day: bool | None = None
    start: str | None = None
    end: str | None = UNSET
    tzid: str | None = None
    rrule: str | None = UNSET  # None clears the recurrence, making it a single event
    notify_minutes_before: int | None = UNSET
    attendee_ids: list[int] | None = None
    calendar_id: int | None = None


class EventOverrideResponse(BaseModel):
    occurrence_date: str
    cancelled: bool
    title: str | None = None
    start_date: str | None = None
    end_date: str | None = None

    model_config = ConfigDict(from_attributes=True)


class EventResponse(BaseModel):
    """The stored series row, which is what the edit form reads.

    Deliberately *not* the same shape as an occurrence: one is the rule, the
    other is a dated instance of it, and collapsing them is how an edit ends up
    applied to the wrong thing.
    """

    id: int
    calendar_id: int
    uid: str
    title: str
    description: str | None = None
    location: str | None = None
    all_day: bool
    start: str  # Local, in the event's own zone
    end: str
    start_date: str
    end_date: str
    tzid: str
    rrule: str | None = None
    series_end_date: str | None = None
    notify_minutes_before: int | None = None
    attendee_ids: list[int] = []
    overrides: list[EventOverrideResponse] = []
    created_at: datetime
    updated_at: datetime


class OccurrenceResponse(BaseModel):
    """One dated instance, which is what every view renders."""

    uid: str
    source: str
    title: str
    description: str = ""
    location: str = ""
    all_day: bool
    start: datetime  # UTC instant
    end: datetime  # UTC instant, exclusive
    start_date: str  # Local, inclusive
    end_date: str  # Local, inclusive
    time_label: str
    end_time_label: str
    dates: list[str]  # Every local date this occurrence covers
    calendar_id: int | None = None
    calendar_label: str = ""
    member: str | None = None
    member_color: str | None = None
    attendees: list[str] = []
    event_id: int | None = None
    occurrence_date: str | None = None
    recurring: bool = False
    editable: bool = False
    notify_minutes_before: int | None = None


class OccurrencePage(BaseModel):
    """Occurrences plus the sources that failed, so a view can say so."""

    occurrences: list[OccurrenceResponse]
    failures: list[str] = []


class EventNotifyRequest(BaseModel):
    occurrence_date: str | None = None  # Defaults to the next upcoming occurrence
    message: str | None = None


class EventNotifyResponse(BaseModel):
    """Per-recipient outcome.

    "It worked" and "four phones buzzed" are different claims, and only this
    shape can tell them apart — an attendee with no Pushover key is reported as
    skipped rather than silently dropped, and one who turned event reminders
    off is reported as muted. The manual notify button is filtered like every
    other push, so it has to be able to say *"sent to Jon · Emma has event
    reminders turned off"*.
    """

    sent: list[str] = []
    skipped: list[str] = []  # no Pushover key
    muted: list[str] = []  # has a key, turned this kind off
    failed: list[str] = []
    error: str | None = None


# Notifications — what Rally sends, and who hears it


class NotificationKindOverview(BaseModel):
    """One row of the read-only *What Rally sends* list.

    ``audience`` is carried rather than derived client-side because it is the
    answer to *"why didn't Jake get that?"*, and that answer belongs on the
    same screen as the question. The three name lists are the state, split the
    way a silent phone actually splits: hearing it, muted it, has no key.
    """

    kind: str
    label: str
    audience: str
    default_on: bool
    settings_key: str | None = None  # The install-wide switch, where it has one
    enabled: bool  # Whether that switch is on; True for a kind with none
    receiving: list[str] = []
    muted: list[str] = []
    no_key: list[str] = []


class NotificationOverviewResponse(BaseModel):
    """Every kind Rally sends, in catalogue order.

    ``token_configured`` sits at the top because it is the first of the five
    gates: with no application token nothing sends at all, and a list of
    carefully configured recipients would otherwise read as working.
    """

    token_configured: bool
    kinds: list[NotificationKindOverview]


# Preparedness — locations


class PrepLocationBase(BaseModel):
    name: str
    sort_order: int = 0


class PrepLocationCreate(PrepLocationBase):
    pass


class PrepLocationUpdate(BaseModel):
    name: str | None = None
    sort_order: int | None = None


class PrepLocationResponse(PrepLocationBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Preparedness — items

PrepRefreshMode = Literal["none", "date", "interval"]


def validate_prep_schedule(mode: str | None, interval: int | None, next_date: str | None) -> None:
    """Enforce the refresh mode/field triangle.

    These are the combinations that would otherwise produce an item which
    silently never notifies — the one failure this feature cannot have.
    """
    if mode is None:
        return
    if mode == "none":
        if next_date or interval:
            raise ValueError(
                "refresh_mode 'none' cannot carry next_refresh_date or refresh_interval_months"
            )
    elif mode == "date":
        if not next_date:
            raise ValueError("refresh_mode 'date' requires next_refresh_date")
        if interval:
            raise ValueError("refresh_mode 'date' cannot carry refresh_interval_months")
    elif mode == "interval":
        if not interval or interval < 1:
            raise ValueError("refresh_mode 'interval' requires refresh_interval_months >= 1")


class PrepItemBase(BaseModel):
    name: str
    quantity: str | None = None
    location_id: int | None = None
    notes: str | None = None
    refresh_mode: PrepRefreshMode = "none"
    refresh_interval_months: int | None = None
    next_refresh_date: str | None = None  # YYYY-MM-DD
    remind_days_before: int | None = None


class PrepItemCreate(PrepItemBase):
    @model_validator(mode="after")
    def check_schedule(self):
        validate_prep_schedule(
            self.refresh_mode, self.refresh_interval_months, self.next_refresh_date
        )
        return self


class PrepItemUpdate(BaseModel):
    """Partial update. Nullable fields use the UNSET sentinel, so an explicit
    ``null`` clears the value while omission leaves it alone."""

    name: str | None = None
    quantity: str | None = UNSET  # None means "clear"; UNSET means "not provided"
    location_id: int | None = UNSET  # None means "Unassigned"
    notes: str | None = UNSET
    refresh_mode: PrepRefreshMode | None = None
    refresh_interval_months: int | None = UNSET
    next_refresh_date: str | None = UNSET  # YYYY-MM-DD
    remind_days_before: int | None = UNSET


class PrepItemResponse(PrepItemBase):
    id: int
    last_refreshed_on: str | None = None
    created_at: datetime
    updated_at: datetime

    # Derived at render time from today's date; never stored.
    status: str = "ok"
    days_until: int | None = None
    location_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PrepItemRefresh(BaseModel):
    """Mark an item refreshed. Defaults to today in the family's timezone."""

    on: str | None = None  # YYYY-MM-DD


# Preparedness — go list


class GoListGroup(BaseModel):
    location_id: int | None
    location_name: str
    items: list[PrepItemResponse]


class GoListResponse(BaseModel):
    generated_on: str
    total_items: int
    groups: list[GoListGroup]


# Preparedness — digest


class PrepDigestItem(BaseModel):
    id: int
    name: str
    location_name: str
    next_refresh_date: str | None
    status: str


class PrepDigestResponse(BaseModel):
    ran_on: str
    dry_run: bool
    sent: bool
    count: int
    items: list[PrepDigestItem]
    sent_to: list[str] = []
    skipped: list[str] = []  # no Pushover key
    muted: list[str] = []  # has a key, turned the digest off
    failed: list[str] = []
    skipped_reason: str | None = None


class PrepNoticeResponse(BaseModel):
    id: int
    item_id: int
    item_name: str | None = None
    refresh_date: str
    sent_on: str
    recipients: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Preparedness — LLM review


class PrepReviewGap(BaseModel):
    item: str
    category: str = ""
    why: str = ""
    priority: str = "medium"


class PrepReviewData(BaseModel):
    assessment: str = ""
    gaps: list[PrepReviewGap] = []
    strengths: list[str] = []
    assumptions: list[str] = []
    notes: str = ""


class PrepReviewResponse(BaseModel):
    id: int
    review: PrepReviewData
    model: str | None = None
    item_count: int
    current_item_count: int
    stale: bool
    created_at: datetime
