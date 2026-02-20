"""Rally Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


# Family Members


class FamilyMemberBase(BaseModel):
    name: str
    color: str = "#333333"
    calendar_key: str | None = None


class FamilyMemberCreate(FamilyMemberBase):
    pass


class FamilyMemberUpdate(BaseModel):
    name: str | None = None
    color: str | None = None
    calendar_key: str | None = None


class FamilyMemberResponse(FamilyMemberBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


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
