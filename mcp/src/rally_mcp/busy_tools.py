"""The busy-only tools: is the family free, and book an appointment, without the calendar.

Built for a phone assistant (an ElevenLabs voice agent) that anyone can call,
and so anyone can try to talk into misbehaving. These tools only ever return
merged free/busy blocks, candidate slots and an opaque confirmation id: no
titles, descriptions, locations, attendees, calendar names or event counts.
That guarantee is enforced by Rally (``/api/busy/...``, see
``rally.routers.busy``), not here, so it holds even if this process or the
agent is compromised. This module only shapes the calls and words the errors
for a voice agent.

Times go in as ISO 8601 with a UTC offset and come back in the family's
local timezone with its offset.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from rally_mcp.rally import RallyClient, RallyError
from rally_mcp.scopes import scoped
from rally_mcp.tools import DESTRUCTIVE, READ_ONLY, WRITE, _tag

# Every busy tool's scope. Each tool also declares it with @scoped, which is
# what rally-mcp enforces; this table is the contract the tests check against.
SCOPES = {
    "check_availability": "calendar:busy",
    "find_free_slots": "calendar:busy",
    "book_appointment": "calendar:busy",
    "cancel_appointment": "calendar:busy",
}

TIME_NOTE = (
    "Times are ISO 8601 with a UTC offset, e.g. 2026-11-05T14:00:00-06:00; results are "
    "in the family's local timezone, with its offset."
)
ISO_WITH_OFFSET = Field(
    max_length=40,
    description="ISO 8601 date-time with a UTC offset, e.g. 2026-11-05T14:00:00-06:00",
)
MEMBERS = Field(
    default=None,
    max_length=10,
    description="Family members' first names to consider; omit for the whole family",
)

# What a voice agent hears when Rally says no. Rally's own messages are fixed
# strings that carry no calendar content; refusals that concern rally-mcp's
# credentials or Rally's internals are not passed through at all.
_REFUSED = "Rally refused this request."
_FAILED = "Rally could not answer just now. Try again later."


async def _call(rally: RallyClient, method: str, path: str, **kwargs):
    try:
        return await rally.request(method, path, **kwargs)
    except RallyError as exc:
        if exc.status in (404, 409, 422, 429, 503):
            raise ToolError(exc.detail) from exc
        if exc.status in (401, 403):
            raise ToolError(_REFUSED) from exc
        raise ToolError(_FAILED) from exc


def _params(**values: Any) -> list[tuple[str, Any]]:
    params: list[tuple[str, Any]] = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, list):
            params.extend((key, item) for item in value)
        elif isinstance(value, bool):
            params.append((key, "true" if value else "false"))
        else:
            params.append((key, value))
    return params


def register(mcp: FastMCP, rally: RallyClient) -> None:
    @mcp.tool(
        annotations=READ_ONLY | {"title": "Check availability"},
        description=(
            "Check when the family is busy or free between two times (at most 60 days apart, "
            "within the next 180 days). Returns only merged busy blocks and the free blocks "
            "between them, on 15-minute boundaries; never what the busy time is. Optionally "
            "limit it to some family members. " + TIME_NOTE
        ),
    )
    @scoped("calendar:busy")
    async def check_availability(
        start: Annotated[str, ISO_WITH_OFFSET],
        end: Annotated[str, ISO_WITH_OFFSET],
        members: Annotated[list[str] | None, MEMBERS] = None,
    ) -> dict:
        _tag("check_availability", locals())
        return await _call(
            rally,
            "GET",
            "/api/busy/availability",
            params=_params(start=start, end=end, members=members),
        )

    @mcp.tool(
        annotations=READ_ONLY | {"title": "Find free slots"},
        description=(
            "Suggest open appointment slots of a given length between two times (at most "
            "60 days apart), earliest first. Slots start on 15-minute boundaries, are never "
            "in the past, and by default fall between 08:00 and 18:00 local time. Constraints: "
            "earliest_time / latest_time (HH:MM, local, 24-hour), weekdays_only, "
            "buffer_minutes kept clear around busy time, max_results (1-20). Returns only "
            "the slots. " + TIME_NOTE
        ),
    )
    @scoped("calendar:busy")
    async def find_free_slots(
        window_start: Annotated[str, ISO_WITH_OFFSET],
        window_end: Annotated[str, ISO_WITH_OFFSET],
        duration_minutes: Annotated[int, Field(ge=15, le=480, description="Slot length")],
        members: Annotated[list[str] | None, MEMBERS] = None,
        earliest_time: Annotated[
            str | None,
            Field(pattern=r"^\d{2}:\d{2}$", description="Earliest local start, HH:MM"),
        ] = None,
        latest_time: Annotated[
            str | None,
            Field(pattern=r"^\d{2}:\d{2}$", description="Latest local end, HH:MM"),
        ] = None,
        weekdays_only: Annotated[bool, Field(description="Skip Saturdays and Sundays")] = False,
        buffer_minutes: Annotated[
            int, Field(ge=0, le=120, description="Minutes kept clear around busy time")
        ] = 0,
        max_results: Annotated[int, Field(ge=1, le=20)] = 5,
    ) -> dict:
        _tag("find_free_slots", locals())
        return await _call(
            rally,
            "GET",
            "/api/busy/free-slots",
            params=_params(
                window_start=window_start,
                window_end=window_end,
                duration_minutes=duration_minutes,
                members=members,
                earliest_time=earliest_time,
                latest_time=latest_time,
                weekdays_only=weekdays_only,
                buffer_minutes=buffer_minutes,
                max_results=max_results,
            ),
        )

    @mcp.tool(
        annotations=WRITE | {"title": "Book appointment"},
        description=(
            "Book an appointment into free time. It goes on the family's calendar marked "
            "pending until someone in the family confirms it, so tell the caller it is "
            "requested, not confirmed. Refused if any of the time is busy. Returns only a "
            "confirmation id (like K7QM-3XPD): read it to the caller, who needs it to cancel. "
            "Give the caller's name and phone number so the family can call back. " + TIME_NOTE
        ),
    )
    @scoped("calendar:busy")
    async def book_appointment(
        start: Annotated[str, ISO_WITH_OFFSET],
        end: Annotated[str, ISO_WITH_OFFSET],
        title: Annotated[
            str, Field(min_length=1, max_length=100, description="What the appointment is")
        ],
        contact_name: Annotated[str | None, Field(max_length=100)] = None,
        contact_phone: Annotated[
            str | None,
            Field(max_length=32, description="Digits, spaces and + ( ) - . only"),
        ] = None,
        notes: Annotated[str | None, Field(max_length=1000)] = None,
        members: Annotated[
            list[str] | None,
            Field(
                default=None,
                max_length=10,
                description="Family members it is for; only their time must be free",
            ),
        ] = None,
    ) -> dict:
        _tag("book_appointment", locals())
        body = {
            "start": start,
            "end": end,
            "title": title,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "notes": notes,
            "members": members,
        }
        body = {k: v for k, v in body.items() if v is not None}
        created = await _call(rally, "POST", "/api/busy/appointments", json=body)
        return {
            "confirmation_id": created["confirmation_id"],
            "status": "pending family confirmation",
        }

    @mcp.tool(
        annotations=DESTRUCTIVE | {"title": "Cancel appointment"},
        description=(
            "Cancel an appointment booked through this line, by its confirmation id, while "
            "it is still pending the family's confirmation. Any other id, including one the "
            "family has already confirmed, is reported as not found."
        ),
    )
    @scoped("calendar:busy")
    async def cancel_appointment(
        confirmation_id: Annotated[
            str, Field(min_length=1, max_length=32, description="The id given at booking")
        ],
    ) -> dict:
        _tag("cancel_appointment", locals())
        return await _call(
            rally,
            "POST",
            "/api/busy/appointments/cancel",
            json={"confirmation_id": confirmation_id},
        )
