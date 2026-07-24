"""Tests for ``GET /api/todos/completed`` — the previously-completed-tasks API.

Covers the keyword search added in #90 plus the pre-existing filtering, sorting,
and pagination behaviour that search rides on.

The completed page shows todos completed *before* today's local-midnight cutoff.
Seed data uses two fixed reference points so tests stay deterministic regardless
of when they run:

- ``PAST`` — a long-ago instant, always before the cutoff (appears on the page);
  this is also the ``make_todo`` factory's default ``completed_at``.
- ``TODAY_NOON`` — noon today (UTC), always within [today-midnight, tomorrow),
  i.e. "completed today", so it is excluded from the page.

Seeding uses the shared ``make_todo`` / ``make_member`` factory fixtures from
conftest.
"""

from datetime import UTC, datetime, timedelta

PAST = datetime(2020, 1, 1, 12, 0, 0)
TODAY_NOON = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=None)


def get_completed(client, **params):
    response = client.get("/api/todos/completed", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_completed_sort_due_soonest(client, make_todo):
    make_todo("Near", due_date="2026-01-01")
    make_todo("Far", due_date="2026-12-01")
    assert titles(get_completed(client, sort="due-soonest")) == ["Near", "Far"]


def test_completed_sort_due_furthest(client, make_todo):
    make_todo("Near", due_date="2026-01-01")
    make_todo("Far", due_date="2026-12-01")
    assert titles(get_completed(client, sort="due-furthest")) == ["Far", "Near"]


def test_completed_sort_by_assignee(client, make_todo, make_member):
    zoe = make_member("Zoe")
    amy = make_member("Amy")
    make_todo("Zoe task", assigned_to=zoe.id)
    make_todo("Amy task", assigned_to=amy.id)
    assert titles(get_completed(client, sort="assignee")) == ["Amy task", "Zoe task"]


def test_completed_sort_newest(client, make_todo):
    make_todo("Old", created_at=datetime(2026, 1, 1))
    make_todo("New", created_at=datetime(2026, 2, 1))
    assert titles(get_completed(client, sort="newest")) == ["New", "Old"]


def test_completed_sort_oldest(client, make_todo):
    make_todo("Old", created_at=datetime(2026, 1, 1))
    make_todo("New", created_at=datetime(2026, 2, 1))
    assert titles(get_completed(client, sort="oldest")) == ["Old", "New"]


def titles(payload) -> list[str]:
    return [item["title"] for item in payload["items"]]


# --- Search (from #90) ---------------------------------------------------------


def test_search_matches_title(client, make_todo):
    make_todo("Allergy shot")
    make_todo("Buy groceries")

    payload = get_completed(client, search="shot")

    assert payload["total"] == 1
    assert titles(payload) == ["Allergy shot"]


def test_search_matches_description(client, make_todo):
    make_todo("Finish reading chapter 3", description="Book club meets next week")
    make_todo("Buy groceries", description="Milk and eggs")

    payload = get_completed(client, search="club")

    assert payload["total"] == 1
    assert titles(payload) == ["Finish reading chapter 3"]


def test_search_is_case_insensitive(client, make_todo):
    make_todo("Allergy SHOT")

    payload = get_completed(client, search="shot")

    assert payload["total"] == 1
    assert titles(payload) == ["Allergy SHOT"]


def test_search_no_match_returns_empty_and_zero_total(client, make_todo):
    make_todo("Allergy shot")

    payload = get_completed(client, search="zzznomatch")

    assert payload["total"] == 0
    assert payload["items"] == []
    assert payload["has_more"] is False


def test_whitespace_only_search_is_treated_as_no_search(client, make_todo):
    make_todo("Allergy shot")
    make_todo("Buy groceries")

    payload = get_completed(client, search="   ")

    assert payload["total"] == 2


def test_search_combined_with_assignee_filter_is_and(client, make_todo, make_member):
    dad = make_member("Dad")
    mom = make_member("Mom")
    make_todo("Allergy shot", assigned_to=dad.id)
    make_todo("Flu shot", assigned_to=mom.id)

    # "shot" matches both todos, but the assignee filter narrows to Dad's only.
    payload = get_completed(client, search="shot", assignee=str(dad.id))

    assert payload["total"] == 1
    assert titles(payload) == ["Allergy shot"]


def test_search_respects_sort(client, make_todo):
    make_todo("Shot A", completed_at=datetime(2021, 1, 1))
    make_todo("Shot B", completed_at=datetime(2022, 1, 1))

    newest = get_completed(client, search="shot", sort="completed-newest")
    oldest = get_completed(client, search="shot", sort="completed-oldest")

    assert titles(newest) == ["Shot B", "Shot A"]
    assert titles(oldest) == ["Shot A", "Shot B"]


def test_total_reflects_all_matches_across_pages(client, make_todo):
    for i in range(55):
        make_todo(f"Shot {i:02d}")

    first = get_completed(client, search="shot", limit=50, offset=0)
    assert first["total"] == 55
    assert len(first["items"]) == 50
    assert first["has_more"] is True

    second = get_completed(client, search="shot", limit=50, offset=50)
    assert second["total"] == 55
    assert len(second["items"]) == 5
    assert second["has_more"] is False


# --- Pre-existing completed-endpoint behaviour ---------------------------------


def test_excludes_todos_completed_today(client, make_todo):
    make_todo("Done long ago", completed_at=PAST)
    make_todo("Done today", completed_at=TODAY_NOON)

    payload = get_completed(client)

    assert titles(payload) == ["Done long ago"]


def test_excludes_incomplete_todos(client, make_todo):
    make_todo("Completed", completed=True, completed_at=PAST)
    make_todo("Still open", completed=False, completed_at=None)

    payload = get_completed(client)

    assert titles(payload) == ["Completed"]


def test_includes_completed_todo_with_null_completed_at(client, make_todo):
    # Pre-migration data: completed but no completed_at. It is hidden from the
    # current-tasks page, so it must appear here rather than nowhere.
    make_todo("Legacy done", completed=True, completed_at=None)

    payload = get_completed(client)

    assert titles(payload) == ["Legacy done"]


def test_filter_unassigned(client, make_todo, make_member):
    dad = make_member("Dad")
    make_todo("Unassigned task", assigned_to=None)
    make_todo("Dad task", assigned_to=dad.id)

    payload = get_completed(client, assignee="unassigned")

    assert payload["total"] == 1
    assert titles(payload) == ["Unassigned task"]


def test_filter_single_member(client, make_todo, make_member):
    dad = make_member("Dad")
    mom = make_member("Mom")
    make_todo("Dad task", assigned_to=dad.id)
    make_todo("Mom task", assigned_to=mom.id)

    payload = get_completed(client, assignee=str(dad.id))

    assert titles(payload) == ["Dad task"]


def test_filter_multiple_members_is_or(client, make_todo, make_member):
    dad = make_member("Dad")
    mom = make_member("Mom")
    kid = make_member("Kid")
    make_todo("Dad task", assigned_to=dad.id)
    make_todo("Mom task", assigned_to=mom.id)
    make_todo("Kid task", assigned_to=kid.id)

    payload = get_completed(client, assignee=[str(dad.id), str(mom.id)])

    assert payload["total"] == 2
    assert set(titles(payload)) == {"Dad task", "Mom task"}


def test_default_sort_is_completed_newest(client, make_todo):
    make_todo("Older", completed_at=datetime(2021, 1, 1))
    make_todo("Newer", completed_at=datetime(2022, 1, 1))

    payload = get_completed(client)

    assert titles(payload) == ["Newer", "Older"]


def test_pagination_limit_offset_and_has_more(client, make_todo):
    # Distinct completion times so ordering is stable across pages.
    base = datetime(2021, 1, 1)
    for i in range(3):
        make_todo(f"Task {i}", completed_at=base + timedelta(days=i))

    first = get_completed(client, limit=2, offset=0)
    assert len(first["items"]) == 2
    assert first["has_more"] is True
    assert first["total"] == 3

    second = get_completed(client, limit=2, offset=2)
    assert len(second["items"]) == 1
    assert second["has_more"] is False
    assert second["total"] == 3
