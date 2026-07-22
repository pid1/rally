"""Tests for ``GET /api/todos/completed`` — the previously-completed-tasks API.

Covers the keyword search added in #90 plus the pre-existing filtering, sorting,
and pagination behaviour that search rides on (this is the endpoint's first
automated coverage).

The completed page shows todos completed *before* today's local-midnight cutoff.
Seed data uses two fixed reference points so tests stay deterministic regardless
of when they run:

- ``PAST`` — a long-ago instant, always before the cutoff (appears on the page).
- ``TODAY_NOON`` — noon today (UTC), always within [today-midnight, tomorrow),
  i.e. "completed today", so it is excluded from the page.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rally.models import FamilyMember, Todo

PAST = datetime(2020, 1, 1, 12, 0, 0)
TODAY_NOON = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=None)


def make_member(db: Session, name: str) -> FamilyMember:
    member = FamilyMember(name=name)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def make_todo(
    db: Session,
    title: str,
    *,
    description: str | None = None,
    completed: bool = True,
    completed_at: datetime | None = PAST,
    assigned_to: int | None = None,
    due_date: str | None = None,
    created_at: datetime | None = None,
) -> Todo:
    todo = Todo(
        title=title,
        description=description,
        completed=completed,
        completed_at=completed_at,
        assigned_to=assigned_to,
        due_date=due_date,
    )
    if created_at is not None:
        todo.created_at = created_at
    db.add(todo)
    db.commit()
    db.refresh(todo)
    return todo


def get_completed(client: TestClient, **params):
    response = client.get("/api/todos/completed", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def titles(payload) -> list[str]:
    return [item["title"] for item in payload["items"]]


# --- Search (from #90) ---------------------------------------------------------


def test_search_matches_title(client, db_session):
    make_todo(db_session, "Allergy shot")
    make_todo(db_session, "Buy groceries")

    payload = get_completed(client, search="shot")

    assert payload["total"] == 1
    assert titles(payload) == ["Allergy shot"]


def test_search_matches_description(client, db_session):
    make_todo(db_session, "Finish reading chapter 3", description="Book club meets next week")
    make_todo(db_session, "Buy groceries", description="Milk and eggs")

    payload = get_completed(client, search="club")

    assert payload["total"] == 1
    assert titles(payload) == ["Finish reading chapter 3"]


def test_search_is_case_insensitive(client, db_session):
    make_todo(db_session, "Allergy SHOT")

    payload = get_completed(client, search="shot")

    assert payload["total"] == 1
    assert titles(payload) == ["Allergy SHOT"]


def test_search_no_match_returns_empty_and_zero_total(client, db_session):
    make_todo(db_session, "Allergy shot")

    payload = get_completed(client, search="zzznomatch")

    assert payload["total"] == 0
    assert payload["items"] == []
    assert payload["has_more"] is False


def test_whitespace_only_search_is_treated_as_no_search(client, db_session):
    make_todo(db_session, "Allergy shot")
    make_todo(db_session, "Buy groceries")

    payload = get_completed(client, search="   ")

    assert payload["total"] == 2


def test_search_combined_with_assignee_filter_is_and(client, db_session):
    dad = make_member(db_session, "Dad")
    mom = make_member(db_session, "Mom")
    make_todo(db_session, "Allergy shot", assigned_to=dad.id)
    make_todo(db_session, "Flu shot", assigned_to=mom.id)

    # "shot" matches both todos, but the assignee filter narrows to Dad's only.
    payload = get_completed(client, search="shot", assignee=str(dad.id))

    assert payload["total"] == 1
    assert titles(payload) == ["Allergy shot"]


def test_search_respects_sort(client, db_session):
    make_todo(db_session, "Shot A", completed_at=datetime(2021, 1, 1))
    make_todo(db_session, "Shot B", completed_at=datetime(2022, 1, 1))

    newest = get_completed(client, search="shot", sort="completed-newest")
    oldest = get_completed(client, search="shot", sort="completed-oldest")

    assert titles(newest) == ["Shot B", "Shot A"]
    assert titles(oldest) == ["Shot A", "Shot B"]


def test_total_reflects_all_matches_across_pages(client, db_session):
    for i in range(55):
        make_todo(db_session, f"Shot {i:02d}")

    first = get_completed(client, search="shot", limit=50, offset=0)
    assert first["total"] == 55
    assert len(first["items"]) == 50
    assert first["has_more"] is True

    second = get_completed(client, search="shot", limit=50, offset=50)
    assert second["total"] == 55
    assert len(second["items"]) == 5
    assert second["has_more"] is False


# --- Pre-existing completed-endpoint behaviour (first coverage) ----------------


def test_excludes_todos_completed_today(client, db_session):
    make_todo(db_session, "Done long ago", completed_at=PAST)
    make_todo(db_session, "Done today", completed_at=TODAY_NOON)

    payload = get_completed(client)

    assert titles(payload) == ["Done long ago"]


def test_excludes_incomplete_todos(client, db_session):
    make_todo(db_session, "Completed", completed=True, completed_at=PAST)
    make_todo(db_session, "Still open", completed=False, completed_at=None)

    payload = get_completed(client)

    assert titles(payload) == ["Completed"]


def test_includes_completed_todo_with_null_completed_at(client, db_session):
    # Pre-migration data: completed but no completed_at. It is hidden from the
    # current-tasks page, so it must appear here rather than nowhere.
    make_todo(db_session, "Legacy done", completed=True, completed_at=None)

    payload = get_completed(client)

    assert titles(payload) == ["Legacy done"]


def test_filter_unassigned(client, db_session):
    dad = make_member(db_session, "Dad")
    make_todo(db_session, "Unassigned task", assigned_to=None)
    make_todo(db_session, "Dad task", assigned_to=dad.id)

    payload = get_completed(client, assignee="unassigned")

    assert payload["total"] == 1
    assert titles(payload) == ["Unassigned task"]


def test_filter_single_member(client, db_session):
    dad = make_member(db_session, "Dad")
    mom = make_member(db_session, "Mom")
    make_todo(db_session, "Dad task", assigned_to=dad.id)
    make_todo(db_session, "Mom task", assigned_to=mom.id)

    payload = get_completed(client, assignee=str(dad.id))

    assert titles(payload) == ["Dad task"]


def test_filter_multiple_members_is_or(client, db_session):
    dad = make_member(db_session, "Dad")
    mom = make_member(db_session, "Mom")
    kid = make_member(db_session, "Kid")
    make_todo(db_session, "Dad task", assigned_to=dad.id)
    make_todo(db_session, "Mom task", assigned_to=mom.id)
    make_todo(db_session, "Kid task", assigned_to=kid.id)

    payload = get_completed(client, assignee=[str(dad.id), str(mom.id)])

    assert payload["total"] == 2
    assert set(titles(payload)) == {"Dad task", "Mom task"}


def test_default_sort_is_completed_newest(client, db_session):
    make_todo(db_session, "Older", completed_at=datetime(2021, 1, 1))
    make_todo(db_session, "Newer", completed_at=datetime(2022, 1, 1))

    payload = get_completed(client)

    assert titles(payload) == ["Newer", "Older"]


def test_pagination_limit_offset_and_has_more(client, db_session):
    # Distinct completion times so ordering is stable across pages.
    base = datetime(2021, 1, 1)
    for i in range(3):
        make_todo(db_session, f"Task {i}", completed_at=base + timedelta(days=i))

    first = get_completed(client, limit=2, offset=0)
    assert len(first["items"]) == 2
    assert first["has_more"] is True
    assert first["total"] == 3

    second = get_completed(client, limit=2, offset=2)
    assert len(second["items"]) == 1
    assert second["has_more"] is False
    assert second["total"] == 3
