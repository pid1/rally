"""Rally Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TodoBase(BaseModel):
    title: str
    description: str | None = None
    due_date: str | None = None  # YYYY-MM-DD format


class TodoCreate(TodoBase):
    pass


class TodoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: str | None = None  # YYYY-MM-DD format
    completed: bool | None = None


class TodoResponse(TodoBase):
    id: int
    completed: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


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
