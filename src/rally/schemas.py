"""Rally Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

# Sentinel value to distinguish "field not provided" from "field set to None"
UNSET = object()

# Family Members


class FamilyMemberBase(BaseModel):
    name: str
    color: str = "#333333"


class FamilyMemberCreate(FamilyMemberBase):
    pass


class FamilyMemberUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class FamilyMemberResponse(FamilyMemberBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Calendars


class CalendarBase(BaseModel):
    label: str
    url: str
    family_member_id: int
    owner_email: str | None = None


class CalendarCreate(CalendarBase):
    pass


class CalendarUpdate(BaseModel):
    label: str | None = None
    url: str | None = None
    family_member_id: int | None = None
    owner_email: str | None = None


class CalendarResponse(CalendarBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Settings


class SettingsUpdate(BaseModel):
    """Bulk settings update — key/value pairs."""

    settings: dict[str, str]


class SettingsResponse(BaseModel):
    """All settings as a flat dict."""

    settings: dict[str, str]


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
    due_date: str | None = None  # YYYY-MM-DD format
    assigned_to: int | None = UNSET  # family_members.id; None means "Everyone"
    remind_days_before: int | None = UNSET  # Days before due_date; None means "always"
    completed: bool | None = None


class TodoResponse(TodoBase):
    id: int
    recurring_todo_id: int | None = None
    remind_days_before: int | None = None
    completed: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Recurring Todos


class RecurringTodoBase(BaseModel):
    title: str
    description: str | None = None
    recurrence_type: str  # daily, weekly, monthly
    recurrence_day: int | None = None  # 0-6 for weekly, 1-31 for monthly
    assigned_to: int | None = None
    has_due_date: bool = False
    remind_days_before: int | None = None  # Days before due_date to start LLM reminders


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


class RecurringTodoResponse(RecurringTodoBase):
    id: int
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Dinner Plans


class DinnerPlanBase(BaseModel):
    date: str  # YYYY-MM-DD format
    plan: str
    attendee_ids: list[int] | None = None  # family_member IDs (who's eating); None = everyone
    cook_id: int | None = None  # family_member ID (who's cooking)


class DinnerPlanCreate(DinnerPlanBase):
    pass


class DinnerPlanUpdate(BaseModel):
    date: str | None = None
    plan: str | None = None
    attendee_ids: list[int] | None = UNSET  # None means "clear"; UNSET means "not provided"
    cook_id: int | None = UNSET  # None means "clear"; UNSET means "not provided"


class DinnerPlanResponse(DinnerPlanBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
