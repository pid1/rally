"""Rally Pydantic schemas."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from rally import markdown, member_colors, member_prefs, notification_prefs, rich_text

# Sentinel value to distinguish "field not provided" from "field set to None"
UNSET = object()


class ArchivePage[T](BaseModel):
    """One page of an archive: completed tasks, previous notes, previous meals,
    purchased items.

    Every archive pages the same way, so they share one shape. ``total`` counts
    matches across every page, which is what the results count reports — a
    per-page count would say "50 matching" no matter how many there are.
    """

    items: list[T]
    has_more: bool  # True when another page exists beyond this one
    total: int  # Total matches across all pages for the current query (search + filters)


# Family Members


def _check_member_color(value: str | None) -> str | None:
    """Reject any color outside the closed palette.

    A format check is not enough here: ``#ffffff`` is well-formed and invisible
    against the page. The palette's promise is that any two members are
    distinguishable on any display Rally runs on, and that only holds while the
    set is closed — so the enum lives at the API boundary rather than in the UI,
    which is not the only writer.
    """
    if value is None:
        return value
    if not member_colors.is_palette_color(value):
        known = ", ".join(member_colors.MEMBER_COLORS)
        raise ValueError(f"Unknown member color: {value}. Known: {known}")
    return value


class FamilyMemberBase(BaseModel):
    name: str
    # Validated on the way *in* (see FamilyMemberCreate / FamilyMemberUpdate),
    # never on the way out. FamilyMemberResponse inherits this, and a response
    # schema that rejected stored data would make one legacy row take down the
    # whole endpoint — including the Settings page that is the only way to
    # repair it. Migration 029 is what moves stored values onto the palette;
    # reads report whatever is actually there.
    color: str = member_colors.DEFAULT_COLOR
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

    @field_validator("color")
    @classmethod
    def check_color(cls, value):
        return _check_member_color(value)

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

    @field_validator("color")
    @classmethod
    def check_color(cls, value):
        return _check_member_color(value)

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


class PurchasedPage(ArchivePage[ShoppingItemResponse]):
    """One page of purchased items, plus the stores the Store chips offer.

    ``stores`` holds chip values — store ids as strings, and ``"anywhere"`` for
    the catch-all — for every store with a purchase matching the current search,
    *ignoring* the store filter. That is what the chips showed when they were
    built from the whole archive in the browser, and answering it here keeps the
    chips from depending on how many pages have been loaded.
    """

    stores: list[str]


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


class MealPlanBase(BaseModel):
    date: str  # YYYY-MM-DD format
    meal_type: str = "Dinner"  # Breakfast, Lunch, Dinner, Snacks
    plan: str
    attendee_ids: list[int] | None = None  # family_member IDs (who's eating); None = everyone
    cook_id: int | None = None  # family_member ID (who's cooking)
    rating: int | None = None  # 1-5 star rating; null = not yet reviewed
    review: str | None = None  # Free-text review


class MealPlanCreate(MealPlanBase):
    pass


class MealPlanUpdate(BaseModel):
    date: str | None = None
    meal_type: str | None = None
    plan: str | None = None
    attendee_ids: list[int] | None = UNSET  # None means "clear"; UNSET means "not provided"
    cook_id: int | None = UNSET  # None means "clear"; UNSET means "not provided"


class MealPlanReviewUpdate(BaseModel):
    """Lightweight schema for submitting/editing a meal review."""

    rating: int | None = None  # 1-5; None means "clear rating"
    review: str | None = None  # Free-text; None means "clear review"


class MealPlanResponse(MealPlanBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Notes

# A `<` that is followed by an optional slash and then an ASCII letter. That is
# what a tag looks like; a comparison is not. Rejecting on the `<` character
# alone would eat "wear layers if temp < 40" and "the 5<6 rule", which is the
# corruption this check exists to prevent rather than cause.
_MARKUP_RE = re.compile(r"<\s*/?[a-zA-Z]")


def _reject_markup(value: str) -> str:
    """Refuse a note containing HTML tags, rather than stripping them.

    Stripping would silently alter what the family wrote and could not be
    undone; refusing costs them one reworded line. The renderer escapes tags
    anyway (``rally.markdown``), so this is the first of two independent
    controls, not the only one.
    """
    if _MARKUP_RE.search(value):
        raise ValueError(
            'Notes can\'t contain HTML tags. Try rewording the part that starts with "<".'
        )
    return value


def _require_body(value: str) -> str:
    """A note with nothing in it is not a note.

    Rejecting it here is what lets the dashboard treat "no row" and "an empty
    row" as one absent state, so its render-or-omit rule has a single condition
    to test.
    """
    if not value or not value.strip():
        raise ValueError("A note needs some text.")
    return value


class NoteBase(BaseModel):
    date: str  # YYYY-MM-DD format
    body: str  # Markdown source as the family typed it

    @field_validator("body")
    @classmethod
    def _validate_body(cls, value: str) -> str:
        return _reject_markup(_require_body(value))


class NoteCreate(NoteBase):
    pass


class NoteUpdate(BaseModel):
    date: str | None = None
    body: str | None = None

    @field_validator("body")
    @classmethod
    def _validate_body(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _reject_markup(_require_body(value))


class NoteResponse(NoteBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def body_html(self) -> str:
        """The rendered note, built server-side so no page needs a renderer.

        Both shapes travel together on purpose: the edit modal loads ``body``
        into its textarea, and the page inserts ``body_html`` directly.
        """
        return markdown.render(self.body)


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
    is labeled.
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
    calendar_id: int | None = None  # None inherits the series' calendar

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
    # What the edit form shows when this occurrence is opened. Distinct from
    # `start_date` (a date alone) and from the event's own values, which name
    # the series and so are wrong for every occurrence after the first.
    start_form: str
    end_form: str
    dates: list[str]  # Every local date this occurrence covers
    calendar_id: int | None = None
    calendar_label: str = ""
    member: str | None = None
    member_color: str | None = None
    attendees: list[str] = []
    event_id: int | None = None
    occurrence_date: str | None = None
    recurring: bool = False
    rrule: str | None = None  # The series' rule, for reading back — never edited here
    # The rule in words, rendered here so the page never has to own a second
    # copy of the vocabulary. Empty when there is no rule, or none Rally can see.
    recurrence_text: str = ""
    editable: bool = False
    notify_minutes_before: int | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def description_html(self) -> str:
        """The description as paragraphs, line breaks and links, built server-side.

        Travels beside ``description`` rather than replacing it: the edit form
        loads the raw text into its textarea, and the detail view inserts this
        directly. Computed here, on the way out, so a description already sitting
        in ``calendar_cache`` is formatted without a resync.
        """
        return rich_text.render_description(self.description, source=self.source)


class OccurrencePage(BaseModel):
    """Occurrences plus the sources that failed, so a view can say so."""

    occurrences: list[OccurrenceResponse]
    failures: list[str] = []


class RecurrenceDescribeRequest(BaseModel):
    """An unsaved rule the form wants read back to it."""

    rrule: str | None = None


class RecurrenceDescribeResponse(BaseModel):
    """The rule in words. Empty when there is no rule to describe."""

    description: str


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
    """Every kind Rally sends, in catalog order.

    ``token_configured`` sits at the top because it is the first of the five
    gates: with no application token nothing sends at all, and a list of
    carefully configured recipients would otherwise read as working.
    """

    token_configured: bool
    kinds: list[NotificationKindOverview]


# Devices, and the behavioral settings answered on one


class DeviceBase(BaseModel):
    """What a browser says about itself.

    ``label`` is the browser's own coarse guess on first contact ("iPhone",
    "Mac") and is there to be corrected. Nothing depends on it being right —
    it exists so the device list answers "which one is that?" instead of
    showing a column of tokens.
    """

    label: str | None = None


class DeviceAnnounce(DeviceBase):
    """A device saying hello, and optionally naming itself.

    The id is in the path rather than the body: the browser minted it and is
    addressing its own record, which is a PUT to a known URL rather than a
    create. Omitting ``label`` leaves a stored name alone, so a page that
    merely says hello cannot overwrite one somebody typed.
    """


class DeviceResponse(DeviceBase):
    id: str
    created_at: datetime
    last_seen_at: datetime
    # How many stored answers this device carries, so "forget this device"
    # can say what it is about to throw away rather than asking somebody to
    # guess.
    answer_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class BehaviorChoice(BaseModel):
    """One answer a behavioral setting accepts."""

    value: str
    label: str


class BehaviorSettingCatalogEntry(BaseModel):
    """One setting, its choices, and what an unanswered device falls back to.

    ``default`` is always ``auto`` — Rally's own rule, the behavior that
    predates this table — and it is carried so a client can mark a dropdown as
    chosen or inherited without holding a second copy of the catalog.
    """

    key: str
    label: str
    description: str
    choices: list[BehaviorChoice]
    default: str


class BehaviorCatalogResponse(BaseModel):
    """Every behavioral setting Rally offers."""

    settings: list[BehaviorSettingCatalogEntry]


class DevicePreferencesResponse(BaseModel):
    """Every member's answers on one device, keyed by family member id.

    Resolved, with the defaults filled in, for the same reason the notification
    preferences are: a client that has to know the defaults to render a
    dropdown is a second place for the defaults to live, and the two will
    disagree. Keys are strings because JSON object keys always are.
    """

    device_id: str
    members: dict[str, dict[str, str]]


class MemberPreferencesUpdate(BaseModel):
    """A partial set of answers for one member on one device.

    Partial: a setting left out keeps its answer, so Settings saves one
    dropdown without sending the others back. An unknown setting or value is a
    422 rather than a stored preference nothing will ever read.
    """

    values: dict[str, str]

    @field_validator("values")
    @classmethod
    def check_values(cls, values):
        """Reject a setting or a value the catalog does not offer.

        Both ways of being wrong are silent if stored: a typo'd key is read by
        nothing and a value outside the choices resolves back to the default.
        Each looks exactly like a preference that quietly stopped working.
        """
        if not values:
            return values

        unknown_keys = sorted(set(values) - set(member_prefs.SETTING_KEYS))
        if unknown_keys:
            known = ", ".join(member_prefs.SETTING_KEYS)
            raise ValueError(f"Unknown setting(s): {', '.join(unknown_keys)}. Known: {known}")

        for key, value in values.items():
            if not member_prefs.is_valid(key, value):
                known = ", ".join(c.value for c in member_prefs.CATALOG_BY_KEY[key].choices)
                raise ValueError(f"Unknown value for {key}: {value}. Known: {known}")
        return values


class MemberPreferencesResponse(BaseModel):
    """One member's resolved answers on one device."""

    device_id: str
    family_member_id: int
    values: dict[str, str]


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


# --- Packing Lists ------------------------------------------------------------------

# How many days ahead a day's packing list is packed: 0 is the day itself. Only
# the daily summary and the pack line act on it; nothing is hidden or locked.
PackDaysBefore = Annotated[int, Field(ge=0)]


def _require_name(value: str) -> str:
    """Trim a name and refuse one that is empty once trimmed."""
    value = (value or "").strip()
    if not value:
        raise ValueError("A name can't be empty.")
    return value


def _require_iso_date(value: str) -> str:
    """Accept exactly ``YYYY-MM-DD``, the shape every date column compares on.

    ``date.fromisoformat`` alone also accepts ``20261003``, which would then
    compare wrongly against every stored string.
    """
    from datetime import date as _date

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise ValueError("Dates are YYYY-MM-DD.")
    _date.fromisoformat(value)
    return value


def _blank_to_none(value: str | None) -> str | None:
    if value is None or value is UNSET:
        return value
    value = value.strip()
    return value or None


class PackingListTemplateCreate(BaseModel):
    """A new template. ``copy_from_template_id`` starts it with a copy of that
    template's items (name, note, owner, bag, in order) and nothing else: the
    name, description and lead time are this body's own. Copying happens only
    here, at creation."""

    name: str
    description: str | None = None
    pack_days_before: PackDaysBefore = 0
    copy_from_template_id: int | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _require_name(value)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class PackingListTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = UNSET  # None means "clear"; UNSET means "not provided"
    pack_days_before: PackDaysBefore | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        return None if value is None else _require_name(value)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class PackingListBagCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _require_name(value)


class PackingListBagUpdate(BaseModel):
    """A partial edit of a bag's household defaults. ``owner_id`` and
    ``parent_bag_id`` use ``UNSET``: left out, they stay; ``null`` clears
    (Everyone, goes in nothing)."""

    name: str | None = None
    owner_id: int | None = UNSET
    parent_bag_id: int | None = UNSET

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        return None if value is None else _require_name(value)


class PackingListBagResponse(BaseModel):
    id: int
    name: str
    owner_id: int | None = None  # The household's; None is Everyone
    parent_bag_id: int | None = None  # The bag it goes in, by default
    item_count: int = 0  # Template items in it, across every packing list

    model_config = ConfigDict(from_attributes=True)


class PackingListBagReadingUpdate(BaseModel):
    """One list's reading of a bag: whose it is and what it goes in there.
    Both fields, since a reading is whole; ``null`` is Everyone, and goes in
    nothing."""

    owner_id: int | None
    parent_bag_id: int | None


class PackingListDayBagUpdate(BaseModel):
    """Grab a bag on one day, or read it differently there. Partial:
    ``owner_id`` and ``parent_bag_id`` use ``UNSET``, and setting either
    writes the day's reading, copying the other from how the bag reads now."""

    checked: bool | None = None
    owner_id: int | None = UNSET
    parent_bag_id: int | None = UNSET


class PackingListBagOnListResponse(BaseModel):
    """A bag as one template or one day reads it, in reading order: outermost
    first, then the bags inside each. ``parent_bag_id`` is always a bag on the
    same list. ``changed`` says this list reads it differently from the level
    above (``Reset`` shows). ``checked`` is grabbed, on a day."""

    id: int
    name: str
    owner_id: int | None = None
    parent_bag_id: int | None = None
    changed: bool = False
    checked: bool = False


class PackingListItemCreate(BaseModel):
    """An item, on a template or on one day.

    The bag comes as a name (``bag``), typed on the item: a name already in
    the household's list, in any case, is that bag, and a new one joins the
    list. ``bag_id`` names one that exists; sending both is a ``422``.
    ``owner_id`` is a family member; ``None`` is Everyone.
    """

    name: str
    note: str | None = None
    owner_id: int | None = None
    bag: str | None = None
    bag_id: int | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _require_name(value)

    @field_validator("note", "bag")
    @classmethod
    def _validate_blank(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @model_validator(mode="after")
    def _one_bag(self):
        if self.bag is not None and self.bag_id is not None:
            raise ValueError("Send a bag by name or by id, not both.")
        return self


class PackingListItemUpdate(BaseModel):
    """A partial edit. ``note``, ``owner_id``, ``bag`` and ``bag_id`` use
    ``UNSET``: left out, they stay; ``null`` clears (Everyone, No bag)."""

    name: str | None = None
    note: str | None = UNSET
    owner_id: int | None = UNSET
    bag: str | None = UNSET
    bag_id: int | None = UNSET

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        return None if value is None else _require_name(value)

    @field_validator("note", "bag")
    @classmethod
    def _validate_blank(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @model_validator(mode="after")
    def _one_bag(self):
        if self.bag is not UNSET and self.bag_id is not UNSET:
            raise ValueError("Send a bag by name or by id, not both.")
        return self


class PackingListTemplateItemResponse(BaseModel):
    id: int
    owner_id: int | None = None
    bag_id: int | None = None
    name: str
    note: str | None = None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class PackingListTemplateReorder(BaseModel):
    """One group of a template, in one view, as it should now read.

    ``view`` says what the groups are — owners or bags — and ``key`` which one
    the items were dropped in (``None`` is Everyone, or No bag). Every listed
    item takes that owner or bag, so dragging into another group is a reorder
    whose payload names an item that used to live elsewhere: the
    ``ShoppingReorder`` contract, one level up.
    """

    view: Literal["owner", "bag"]
    key: int | None = None
    item_ids: list[int]


class PackingListDayItemRef(BaseModel):
    """One item on a day: a template item (``template``) or one the day has
    of its own (``day``). The two kinds come from different tables, so an id
    alone does not say which."""

    source: Literal["template", "day"]
    id: int


class PackingListDayReorder(BaseModel):
    """One group of a day's packing list, in one view, as it should now read.

    ``PackingListTemplateReorder`` for a day: every listed item takes the
    group's owner or bag, on this day only, and the items are dealt back into
    the places they held between them. Duplicates keep their first mention.
    """

    view: Literal["owner", "bag"]
    key: int | None = None
    items: list[PackingListDayItemRef]


class PackingListDayResync(BaseModel):
    """How much of a day to bring back in line with its template.

    ``items`` undoes the day's item edits, removals and additions (and puts a
    bag back inside a bag the day took off, once that bag is back); ``all``
    undoes every way the day differs: those, its order, its bag readings, its
    checks, its label and its lead time.
    """

    scope: Literal["items", "all"]


class PackingListSuggestion(BaseModel):
    """An item name from history, with the owner and bag it last had.

    ``times_added`` is how many past days it was on a packing list."""

    id: int
    name: str
    owner_id: int | None = None
    bag_id: int | None = None
    bag_name: str | None = None
    times_added: int


class PackingListTemplateSummary(BaseModel):
    """A template's row on the Packing Lists page."""

    id: int
    name: str
    description: str | None = None
    pack_days_before: int
    item_count: int
    day_count: int  # Every day it is on, past included — what a delete would convert
    upcoming_days: int  # Days from today on


class PackingListTemplateResponse(PackingListTemplateSummary):
    """A template with its items, in order, and the schedule it repeats on
    (``None`` when it does not)."""

    items: list[PackingListTemplateItemResponse]
    bags: list[PackingListBagOnListResponse] = []
    schedule: PackingListTemplateScheduleResponse | None = None


class PackingListDayCreate(BaseModel):
    """A packing list on a day, in one of three forms:

    * ``packing_list_template_id``: the template itself on that day, kept in
      sync with it. ``pack_days_before`` is optional: left out (or ``None``)
      the day follows the template's, a number is the day's own.
    * ``name``: a one-off, a templateless day with nothing on it yet.
    * ``name`` and ``copy_from_template_id``: a one-off holding a copy of
      that template's items, with no link back.

    A one-off has no template to follow, so it needs its own lead time. A
    template id with a name or a copy, or none of the three, is a ``422`` —
    the ``ShoppingItemCreate`` rule for ``store`` and ``store_id``.
    """

    packing_list_template_id: int | None = None
    name: str | None = None
    copy_from_template_id: int | None = None
    date: str  # YYYY-MM-DD
    label: str | None = None
    pack_days_before: PackDaysBefore | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        return None if value is None else _require_name(value)

    @field_validator("date")
    @classmethod
    def _validate_date(cls, value: str) -> str:
        return _require_iso_date(value)

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @model_validator(mode="after")
    def _one_form(self):
        kept_in_sync = self.packing_list_template_id is not None
        one_off = self.name is not None or self.copy_from_template_id is not None
        if kept_in_sync and one_off:
            raise ValueError("Send packing_list_template_id, or a name for a one-off — not both.")
        if not kept_in_sync and not one_off:
            raise ValueError("Send packing_list_template_id, or a name for a one-off.")
        if one_off and self.name is None:
            raise ValueError("A one-off needs a name.")
        if one_off and self.pack_days_before is None:
            raise ValueError("A one-off needs its own lead time.")
        return self

    @property
    def is_one_off(self) -> bool:
        return self.packing_list_template_id is None


class PackingListDayUpdate(BaseModel):
    # A templateless day's own name; a ``422`` on a templated day, which is
    # called what its template is called.
    name: str | None = None
    date: str | None = None
    label: str | None = UNSET  # None means "clear"; UNSET means "not provided"
    # This day's own lead time. None follows the template again (a 422 on a
    # templateless day, which has none); UNSET leaves it as it is.
    pack_days_before: PackDaysBefore | None = UNSET

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        return None if value is None else _require_name(value)

    @field_validator("date")
    @classmethod
    def _validate_date(cls, value: str | None) -> str | None:
        return None if value is None else _require_iso_date(value)

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class PackingListDaySummary(BaseModel):
    """A day's packing list as a row: what it is, when, and how far along.

    ``name`` and ``description`` are the template's on a templated day and
    the day's own on a templateless one (``packing_list_template_id`` is
    ``None``), so a page reads one pair of fields either way.
    """

    id: int
    packing_list_template_id: int | None = None
    name: str
    description: str | None = None
    date: str
    label: str | None = None
    pack_days_before: int  # In effect for this day: its own, or the template's
    pack_date: str  # The day it should be packed: the date itself, or that many days before
    schedule_id: int | None = None  # The schedule that put it there; None when added by hand
    total: int
    checked: int
    bags_total: int = 0  # Bags on this day: every bag an item is in, and the bags those go in
    bags_checked: int = 0  # Of those, grabbed
    # Items this day differs from its template on: edited, removed or added.
    # Always 0 on a templateless day, which has no template to differ from.
    changed_count: int = 0


class PackingListDayItemResponse(BaseModel):
    """One item as it reads on one day, after the day's own changes.

    ``source`` says which endpoint edits it: ``template`` is a template item
    (``/template-items/{id}``, ``id`` is the template item's), ``day`` is an
    item only this day has (``/day-items/{id}``). ``changed`` marks a template
    item this day has edited, which a later edit to the template no longer
    reaches.
    """

    id: int
    source: Literal["template", "day"]
    owner_id: int | None = None
    bag_id: int | None = None
    name: str
    note: str | None = None
    sort_order: int
    checked: bool
    changed: bool = False


class PackingListDayResponse(PackingListDaySummary):
    """The day's packing list itself: its items as this day has them, with
    checks, and its bags with whether each was grabbed."""

    items: list[PackingListDayItemResponse]
    bags: list[PackingListBagOnListResponse] = []


class PackingListDayItemCreate(PackingListItemCreate):
    """An item only one day has: a name, and optionally a note, owner and bag."""


class PackingListDayItemUpdate(PackingListItemUpdate):
    """Check, edit, or both, for an item on one day — a template item or one
    the day added. Partial, like an item update. An edit changes the item on
    this day only; the template keeps its own."""

    checked: bool | None = None


# The rule shapes ``rally.recurrence`` understands. Validated here rather than
# trusted, because a rule nothing can read would make a schedule that silently
# never puts anything on a day.
RecurrenceType = Literal["daily", "weekly", "monthly", "custom"]


def check_recurrence_rule(
    recurrence_type: str, recurrence_day: int | None, custom_rule: dict | None
):
    if recurrence_type == "weekly" and not (
        recurrence_day is not None and 0 <= recurrence_day <= 6
    ):
        raise ValueError("A weekly schedule needs a day of the week.")
    if recurrence_type == "monthly" and not (
        recurrence_day is not None and 1 <= recurrence_day <= 31
    ):
        raise ValueError("A monthly schedule needs a day of the month.")
    if recurrence_type == "custom":
        freq = (custom_rule or {}).get("freq")
        if freq not in ("daily", "weekly", "monthly"):
            raise ValueError("A custom schedule needs a rule.")
        if freq == "weekly" and not custom_rule.get("weekdays"):
            raise ValueError("A custom weekly schedule needs at least one day of the week.")
        if freq == "monthly" and not (
            (custom_rule.get("mode", "day") == "day" and custom_rule.get("day"))
            or (custom_rule.get("ordinal") and custom_rule.get("weekday") is not None)
        ):
            raise ValueError("A custom monthly schedule needs a day of the month or a weekday.")


def check_date_range(start_date: str | None, end_date: str | None):
    if start_date and end_date and end_date < start_date:
        raise ValueError("The end date can't be before the start date.")


class PackingListTemplateScheduleCreate(BaseModel):
    packing_list_template_id: int
    recurrence_type: RecurrenceType
    recurrence_day: int | None = None  # 0-6 (Monday first) for weekly, 1-31 for monthly
    custom_rule: dict | None = None
    start_date: str | None = None  # YYYY-MM-DD; None means "from today"
    end_date: str | None = None  # YYYY-MM-DD, inclusive; None means "no end"
    label: str | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _validate_dates(cls, value: str | None) -> str | None:
        return _require_iso_date(value) if value else None

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @model_validator(mode="after")
    def _validate_rule(self):
        check_recurrence_rule(self.recurrence_type, self.recurrence_day, self.custom_rule)
        check_date_range(self.start_date, self.end_date)
        return self


class PackingListTemplateScheduleUpdate(BaseModel):
    """A partial edit. The rule is re-checked against the merged schedule."""

    packing_list_template_id: int | None = None
    recurrence_type: RecurrenceType | None = None
    recurrence_day: int | None = UNSET  # None means "clear"; UNSET means "not provided"
    custom_rule: dict | None = UNSET
    start_date: str | None = UNSET
    end_date: str | None = UNSET
    label: str | None = UNSET
    active: bool | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _validate_dates(cls, value: str | None) -> str | None:
        if value is UNSET:
            return value
        return _require_iso_date(value) if value else None

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class PackingListTemplateScheduleResponse(BaseModel):
    id: int
    packing_list_template_id: int
    template_name: str
    recurrence_type: str
    recurrence_day: int | None = None
    custom_rule: dict | None = None
    start_date: str | None = None
    end_date: str | None = None
    label: str | None = None
    active: bool
    last_generated_date: str | None = None


# PackingListTemplateResponse names the schedule before it is defined.
PackingListTemplateResponse.model_rebuild()
