"""Rally Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


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


class TodoCreate(TodoBase):
    pass


class TodoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: str | None = None  # YYYY-MM-DD format
    assigned_to: int | None = None  # family_members.id
    completed: bool | None = None


class TodoResponse(TodoBase):
    id: int
    completed: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Dinner Plans


class DinnerPlanBase(BaseModel):
    date: str  # YYYY-MM-DD format
    plan: str


class DinnerPlanCreate(DinnerPlanBase):
    pass


class DinnerPlanUpdate(BaseModel):
    date: str | None = None
    plan: str | None = None


class DinnerPlanResponse(DinnerPlanBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
