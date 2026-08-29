"""Tests for the shopping list router.

Covers the local-midnight visibility boundary (mirroring test_completed_todos.py),
the 30-day retention purge and its once-per-local-day gate, store CRUD and the
reassign-on-delete behavior, item create/dedupe/update semantics, store-by-name
resolution for scripted clients, the history-backed suggestions endpoints, and
the hand-arranged ordering behind drag-to-reorder.
"""

from datetime import UTC, datetime, timedelta

from rally.models import ShoppingItem, ShoppingItemHistory, ShoppingStore

# Noon UTC on 2026-03-15. In America/Chicago (CDT, UTC-5) local midnight that
# day is 05:00 UTC, so the 04:00/06:00 fixtures below straddle the boundary.
NOON = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
BEFORE_LOCAL_MIDNIGHT = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)  # Mar 14, 23:00 local
AFTER_LOCAL_MIDNIGHT = datetime(2026, 3, 15, 6, 0, tzinfo=UTC)  # Mar 15, 01:00 local


def item_names(payload) -> list[str]:
    return [item["name"] for item in payload]


# --- Visibility boundary -------------------------------------------------------


def test_item_completed_today_stays_on_the_list(
    client, make_shopping_item, frozen_now, local_timezone
):
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item(
        "Coffee beans", completed=True, completed_at=AFTER_LOCAL_MIDNIGHT
    )

    assert item_names(client.get("/api/shopping/items").json()) == ["Coffee beans"]


def test_item_completed_yesterday_is_hidden(
    client, make_shopping_item, frozen_now, local_timezone
):
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item(
        "Coffee beans", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT
    )

    assert client.get("/api/shopping/items").json() == []


def test_include_hidden_returns_previously_purchased_items(
    client, make_shopping_item, frozen_now, local_timezone
):
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item(
        "Coffee beans", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT
    )

    resp = client.get("/api/shopping/items", params={"include_hidden": "true"})
    assert item_names(resp.json()) == ["Coffee beans"]


def test_boundary_uses_configured_timezone_not_utc(
    client, make_shopping_item, frozen_now, local_timezone
):
    """04:00 UTC is still *yesterday* in Chicago but already today in UTC."""
    frozen_now(NOON)
    make_shopping_item(
        "Coffee beans", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT
    )

    # Default (no local_timezone setting) is UTC: the item is "completed today".
    assert item_names(client.get("/api/shopping/items").json()) == ["Coffee beans"]

    local_timezone("America/Chicago")
    assert client.get("/api/shopping/items").json() == []


def test_open_items_sort_before_completed_ones(
    client, make_shopping_item, frozen_now, local_timezone
):
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item("Bought", completed=True, completed_at=AFTER_LOCAL_MIDNIGHT)
    make_shopping_item("Still needed")

    assert item_names(client.get("/api/shopping/items").json()) == [
        "Still needed",
        "Bought",
    ]


def test_completing_stamps_and_uncompleting_clears(
    client, make_shopping_item, frozen_now
):
    frozen_now(NOON)
    item = make_shopping_item("Milk")

    completed = client.put(
        f"/api/shopping/items/{item.id}", json={"completed": True}
    ).json()
    assert completed["completed"] is True
    assert completed["completed_at"] is not None

    reopened = client.put(
        f"/api/shopping/items/{item.id}", json={"completed": False}
    ).json()
    assert reopened["completed"] is False
    assert reopened["completed_at"] is None


def test_recompleting_does_not_restamp(client, make_shopping_item, frozen_now):
    frozen_now(NOON)
    item = make_shopping_item("Milk")

    first = client.put(
        f"/api/shopping/items/{item.id}", json={"completed": True}
    ).json()
    frozen_now(NOON + timedelta(hours=3))
    again = client.put(
        f"/api/shopping/items/{item.id}", json={"completed": True}
    ).json()

    assert again["completed_at"] == first["completed_at"]


# --- Retention purge -----------------------------------------------------------


def test_purge_deletes_items_completed_over_30_days_ago(
    client, db_session, make_shopping_item, frozen_now, local_timezone
):
    local_timezone()
    frozen_now(NOON)
    make_shopping_item(
        "Old milk", completed=True, completed_at=NOON - timedelta(days=31)
    )

    client.get("/api/shopping/items", params={"include_hidden": "true"})

    # Deleted from the database, not merely hidden.
    assert db_session.query(ShoppingItem).count() == 0


def test_purge_keeps_items_completed_within_30_days(
    client, db_session, make_shopping_item, frozen_now, local_timezone
):
    local_timezone()
    frozen_now(NOON)
    make_shopping_item(
        "Recent milk", completed=True, completed_at=NOON - timedelta(days=29)
    )

    client.get("/api/shopping/items", params={"include_hidden": "true"})

    assert db_session.query(ShoppingItem).count() == 1


def test_purge_never_touches_open_items(
    client, db_session, make_shopping_item, frozen_now, local_timezone
):
    """An item nobody has bought in two years is still something the family wants."""
    local_timezone()
    frozen_now(NOON)
    make_shopping_item("Stamps", created_at=NOON - timedelta(days=100))

    client.get("/api/shopping/items")

    assert db_session.query(ShoppingItem).count() == 1


def test_purge_leaves_item_history_intact(
    client,
    db_session,
    make_shopping_item,
    make_item_history,
    frozen_now,
    local_timezone,
):
    """The single most important purge test: splitting the tables is the whole
    reason purchased rows can be deleted at all."""
    local_timezone()
    frozen_now(NOON)
    make_item_history("Old milk", times_added=7)
    make_shopping_item(
        "Old milk", completed=True, completed_at=NOON - timedelta(days=31)
    )

    client.get("/api/shopping/items", params={"include_hidden": "true"})

    assert db_session.query(ShoppingItem).count() == 0
    assert db_session.query(ShoppingItemHistory).one().times_added == 7


def test_purge_runs_at_most_once_per_local_day(
    client, db_session, make_shopping_item, frozen_now, local_timezone
):
    local_timezone()
    frozen_now(NOON)
    make_shopping_item("First", completed=True, completed_at=NOON - timedelta(days=31))
    client.get("/api/shopping/items")

    # A second stale row added after today's purge already ran survives until
    # the local date rolls over — the read path must not take a write lock again.
    make_shopping_item("Second", completed=True, completed_at=NOON - timedelta(days=31))
    client.get("/api/shopping/items")

    assert item_names(
        client.get("/api/shopping/items", params={"include_hidden": "true"}).json()
    ) == ["Second"]


# --- Stores --------------------------------------------------------------------


def test_store_crud(client):
    created = client.post("/api/shopping/stores", json={"name": "Costco"})
    assert created.status_code == 201
    store_id = created.json()["id"]

    client.post("/api/shopping/stores", json={"name": "Aldi"})
    assert item_names(client.get("/api/shopping/stores").json()) == ["Aldi", "Costco"]

    renamed = client.put(
        f"/api/shopping/stores/{store_id}", json={"name": "Costco Business"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Costco Business"

    assert client.delete(f"/api/shopping/stores/{store_id}").status_code == 204
    assert item_names(client.get("/api/shopping/stores").json()) == ["Aldi"]


def test_store_name_conflicts_are_case_insensitive(client, make_store):
    make_store("Costco")
    assert (
        client.post("/api/shopping/stores", json={"name": "costco"}).status_code == 409
    )

    other = client.post("/api/shopping/stores", json={"name": "Aldi"}).json()
    assert (
        client.put(
            f"/api/shopping/stores/{other['id']}", json={"name": "COSTCO"}
        ).status_code
        == 409
    )


def test_renaming_a_store_to_its_own_name_is_allowed(client, make_store):
    store = make_store("Costco")
    resp = client.put(f"/api/shopping/stores/{store.id}", json={"name": "costco"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "costco"


def test_empty_store_name_is_rejected(client):
    assert client.post("/api/shopping/stores", json={"name": "   "}).status_code == 422


def test_deleting_a_store_moves_its_items_to_anywhere(
    client, db_session, make_store, make_shopping_item
):
    store = make_store("Costco")
    item = make_shopping_item("Paper towels", store_id=store.id)

    assert client.delete(f"/api/shopping/stores/{store.id}").status_code == 204

    listed = client.get("/api/shopping/items").json()
    assert item_names(listed) == ["Paper towels"]
    assert listed[0]["store_id"] is None
    db_session.expire_all()
    assert db_session.get(ShoppingItem, item.id).store_id is None


def test_deleting_a_missing_store_is_404(client):
    assert client.delete("/api/shopping/stores/999").status_code == 404


def test_unknown_store_id_on_create_is_rejected(client):
    resp = client.post("/api/shopping/items", json={"name": "Milk", "store_id": 999})
    assert resp.status_code == 422


def test_unknown_store_id_on_update_is_rejected(client, make_shopping_item):
    item = make_shopping_item("Milk")
    assert (
        client.put(f"/api/shopping/items/{item.id}", json={"store_id": 999}).status_code
        == 422
    )


# --- Items ---------------------------------------------------------------------


def test_create_item_defaults(client):
    resp = client.post("/api/shopping/items", json={"name": "  Milk  "})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Milk"  # trimmed
    assert body["note"] is None
    assert body["store_id"] is None
    assert body["completed"] is False
    assert body["completed_at"] is None


def test_whitespace_only_item_name_is_rejected(client):
    assert client.post("/api/shopping/items", json={"name": "   "}).status_code == 422


def test_renaming_an_item_to_whitespace_is_rejected(client, make_shopping_item):
    item = make_shopping_item("Milk")
    assert (
        client.put(f"/api/shopping/items/{item.id}", json={"name": " "}).status_code
        == 422
    )


def test_adding_an_open_duplicate_returns_the_existing_item(client, make_store):
    store = make_store("Costco")
    first = client.post(
        "/api/shopping/items", json={"name": "Milk", "store_id": store.id}
    ).json()

    resp = client.post(
        "/api/shopping/items", json={"name": "  milk ", "store_id": store.id}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == first["id"]


def test_same_name_in_a_different_store_is_a_new_item(client, make_store):
    costco = make_store("Costco")
    aldi = make_store("Aldi")
    client.post("/api/shopping/items", json={"name": "Milk", "store_id": costco.id})

    resp = client.post(
        "/api/shopping/items", json={"name": "Milk", "store_id": aldi.id}
    )
    assert resp.status_code == 201


def test_a_completed_match_does_not_dedupe(client, make_shopping_item, frozen_now):
    """You bought the milk; now you need more milk."""
    frozen_now(NOON)
    make_shopping_item("Milk", completed=True, completed_at=NOON)

    resp = client.post("/api/shopping/items", json={"name": "Milk"})
    assert resp.status_code == 201


def test_note_null_clears_and_omission_leaves_alone(client, make_shopping_item):
    item = make_shopping_item("Milk", note="Whole")

    unchanged = client.put(
        f"/api/shopping/items/{item.id}", json={"name": "Milk"}
    ).json()
    assert unchanged["note"] == "Whole"

    cleared = client.put(f"/api/shopping/items/{item.id}", json={"note": None}).json()
    assert cleared["note"] is None


def test_delete_item(client, make_shopping_item):
    item = make_shopping_item("Milk")
    assert client.delete(f"/api/shopping/items/{item.id}").status_code == 204
    assert client.get("/api/shopping/items").json() == []


# --- Store by name (scripted / voice clients) ----------------------------------


def test_store_name_resolves_case_insensitively(client, make_store):
    store = make_store("Trader Joe's")
    resp = client.post(
        "/api/shopping/items", json={"name": "Milk", "store": "trader joe's"}
    )
    assert resp.status_code == 201
    assert resp.json()["store_id"] == store.id


def test_unknown_store_name_falls_back_to_anywhere(client, db_session):
    """A hard failure mid-dictation is worse than a slightly-misfiled item."""
    resp = client.post("/api/shopping/items", json={"name": "Milk", "store": "Nowhere"})
    assert resp.status_code == 201
    assert resp.json()["store_id"] is None
    assert db_session.query(ShoppingStore).count() == 0


def test_sending_both_store_and_store_id_is_rejected(client, make_store):
    store = make_store("Costco")
    resp = client.post(
        "/api/shopping/items",
        json={"name": "Milk", "store_id": store.id, "store": "Costco"},
    )
    assert resp.status_code == 422


# --- History and suggestions ---------------------------------------------------


def test_create_records_history(client, db_session, make_store):
    store = make_store("Costco")
    client.post("/api/shopping/items", json={"name": "Milk", "store_id": store.id})

    row = db_session.query(ShoppingItemHistory).one()
    assert row.name == "Milk"
    assert row.name_key == "milk"
    assert row.times_added == 1
    assert row.store_id == store.id


def test_second_create_increments_rather_than_inserting(
    client, db_session, make_shopping_item
):
    client.post("/api/shopping/items", json={"name": "Milk"})
    # Complete the first one so the second add isn't deduped away.
    item = db_session.query(ShoppingItem).one()
    client.put(f"/api/shopping/items/{item.id}", json={"completed": True})
    client.post("/api/shopping/items", json={"name": "MILK"})

    row = db_session.query(ShoppingItemHistory).one()
    assert row.times_added == 2
    assert row.name == "MILK"  # display casing follows the latest add


def test_history_store_id_follows_the_most_recent_add(client, db_session, make_store):
    costco = make_store("Costco")
    target = make_store("Target")
    client.post("/api/shopping/items", json={"name": "Milk", "store_id": costco.id})
    client.post("/api/shopping/items", json={"name": "Milk", "store_id": target.id})

    assert db_session.query(ShoppingItemHistory).one().store_id == target.id


def test_dedupe_hit_does_not_increment_history(client, db_session):
    client.post("/api/shopping/items", json={"name": "Milk"})
    client.post("/api/shopping/items", json={"name": "Milk"})

    assert db_session.query(ShoppingItemHistory).one().times_added == 1


def test_renaming_an_item_leaves_history_untouched(client, db_session):
    created = client.post("/api/shopping/items", json={"name": "Mikl"}).json()
    client.put(f"/api/shopping/items/{created['id']}", json={"name": "Milk"})

    row = db_session.query(ShoppingItemHistory).one()
    assert row.name == "Mikl"
    assert row.times_added == 1


def test_suggestions_match_anywhere_in_the_name(client, make_item_history):
    make_item_history("Almond milk")
    resp = client.get("/api/shopping/suggestions", params={"q": "milk"})
    assert item_names(resp.json()) == ["Almond milk"]


def test_prefix_matches_outrank_substring_matches(client, make_item_history):
    make_item_history("Almond milk", times_added=50)
    make_item_history("Milk", times_added=1)

    resp = client.get("/api/shopping/suggestions", params={"q": "mil"})
    assert item_names(resp.json()) == ["Milk", "Almond milk"]


def test_use_count_ranks_within_a_tier(client, make_item_history):
    make_item_history("Milk", times_added=2)
    make_item_history("Milk chocolate", times_added=9)

    resp = client.get("/api/shopping/suggestions", params={"q": "mil"})
    assert item_names(resp.json()) == ["Milk chocolate", "Milk"]


def test_like_wildcards_in_the_query_are_literal(client, make_item_history):
    make_item_history("Milk")
    make_item_history("100% juice")

    assert item_names(
        client.get("/api/shopping/suggestions", params={"q": "%"}).json()
    ) == ["100% juice"]
    assert client.get("/api/shopping/suggestions", params={"q": "_"}).json() == []


def test_empty_query_returns_the_usual_suspects(client, make_item_history):
    make_item_history("Bread", times_added=3)
    make_item_history("Milk", times_added=12)

    assert item_names(client.get("/api/shopping/suggestions").json()) == [
        "Milk",
        "Bread",
    ]


def test_limit_is_honored_and_capped(client, make_item_history):
    for i in range(30):
        make_item_history(f"Item {i:02d}", times_added=30 - i)

    assert len(client.get("/api/shopping/suggestions", params={"limit": 3}).json()) == 3
    assert (
        len(client.get("/api/shopping/suggestions", params={"limit": 100}).json()) == 25
    )


def test_deleting_a_suggestion_leaves_shopping_items_alone(
    client, db_session, make_item_history, make_shopping_item
):
    history = make_item_history("Mikl")
    make_shopping_item("Mikl")

    assert client.delete(f"/api/shopping/suggestions/{history.id}").status_code == 204
    assert db_session.query(ShoppingItemHistory).count() == 0
    assert db_session.query(ShoppingItem).count() == 1


def test_deleting_a_missing_suggestion_is_404(client):
    assert client.delete("/api/shopping/suggestions/999").status_code == 404


# --- Purchased archive ---------------------------------------------------------
#
# The exact complement of the default `/api/shopping/items` listing: what that
# endpoint hides, this one shows, with nothing falling through the gap.


def test_purchased_lists_items_bought_before_today(
    client, make_shopping_item, frozen_now, local_timezone
):
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item(
        "Coffee beans", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT
    )

    assert item_names(client.get("/api/shopping/purchased").json()) == ["Coffee beans"]


def test_purchased_excludes_items_bought_today(
    client, make_shopping_item, frozen_now, local_timezone
):
    """Bought today, so it is still on the shopping list — not yet in the archive."""
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item(
        "Coffee beans", completed=True, completed_at=AFTER_LOCAL_MIDNIGHT
    )

    assert client.get("/api/shopping/purchased").json() == []


def test_purchased_includes_completed_rows_with_no_timestamp(
    client, make_shopping_item, frozen_now, local_timezone
):
    """The complement guarantee: `/items` hides these, so `/purchased` must show
    them or they are invisible in the entire application."""
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item("Ancient milk", completed=True, completed_at=None)

    assert client.get("/api/shopping/items").json() == []
    assert item_names(client.get("/api/shopping/purchased").json()) == ["Ancient milk"]


def test_purchased_excludes_open_items(
    client, make_shopping_item, frozen_now, local_timezone
):
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item("Milk")
    make_shopping_item("Eggs", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT)

    assert item_names(client.get("/api/shopping/purchased").json()) == ["Eggs"]


def test_purchased_orders_most_recent_first(
    client, make_shopping_item, frozen_now, local_timezone
):
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item(
        "Older", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT - timedelta(days=2)
    )
    make_shopping_item("Newer", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT)

    assert item_names(client.get("/api/shopping/purchased").json()) == [
        "Newer",
        "Older",
    ]


def test_purchased_search_matches_name_and_note(
    client, make_shopping_item, frozen_now, local_timezone
):
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item("Eggs", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT)
    make_shopping_item(
        "Bread",
        note="the good EGGY loaf",
        completed=True,
        completed_at=BEFORE_LOCAL_MIDNIGHT,
    )
    make_shopping_item("Milk", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT)

    matches = client.get("/api/shopping/purchased", params={"search": "egg"}).json()
    assert sorted(item_names(matches)) == ["Bread", "Eggs"]


def test_purchased_search_with_no_match_is_empty(
    client, make_shopping_item, frozen_now, local_timezone
):
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item("Eggs", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT)

    assert client.get("/api/shopping/purchased", params={"search": "zzz"}).json() == []


def test_purchased_is_independent_of_include_hidden(
    client, make_shopping_item, frozen_now, local_timezone
):
    """The two endpoints answer different questions; neither changes the other."""
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item("Milk")
    make_shopping_item("Eggs", completed=True, completed_at=BEFORE_LOCAL_MIDNIGHT)

    client.get("/api/shopping/items", params={"include_hidden": "true"})
    assert item_names(client.get("/api/shopping/purchased").json()) == ["Eggs"]
    assert item_names(client.get("/api/shopping/items").json()) == ["Milk"]


# --- Ordering ------------------------------------------------------------------
#
# `sort_order` is per-store and only ever compared, never counted on to be
# contiguous — a cross-store move leaves a gap behind it on purpose.


def test_items_read_in_sort_order_within_a_store(
    client, make_store, make_shopping_item
):
    store = make_store("Costco")
    make_shopping_item("Rotisserie chicken", store_id=store.id, sort_order=2)
    make_shopping_item("Paper towels", store_id=store.id, sort_order=0)
    make_shopping_item("Batteries", store_id=store.id, sort_order=1)

    assert item_names(client.get("/api/shopping/items").json()) == [
        "Paper towels",
        "Batteries",
        "Rotisserie chicken",
    ]


def test_a_new_item_lands_above_the_store_it_joins(
    client, make_store, make_shopping_item
):
    """Adding used to surface at the top by `created_at DESC`; it still does."""
    store = make_store("Costco")
    make_shopping_item("Paper towels", store_id=store.id, sort_order=0)
    make_shopping_item("Batteries", store_id=store.id, sort_order=1)

    client.post("/api/shopping/items", json={"name": "Milk", "store_id": store.id})

    assert item_names(client.get("/api/shopping/items").json())[0] == "Milk"


def test_a_new_item_is_placed_only_against_its_own_store(
    client, make_store, make_shopping_item
):
    """A crowded Costco must not push a first Trader Joe's item off the top."""
    costco = make_store("Costco")
    trader_joes = make_store("Trader Joe's")
    make_shopping_item("Paper towels", store_id=costco.id, sort_order=-20)

    response = client.post(
        "/api/shopping/items", json={"name": "Almond milk", "store_id": trader_joes.id}
    )

    assert response.json()["sort_order"] == 0


def test_purchased_items_sink_below_arranged_ones(
    client, make_store, make_shopping_item, frozen_now, local_timezone
):
    """A position from before the item was ticked off must not float it back up."""
    local_timezone("America/Chicago")
    frozen_now(NOON)
    store = make_store("Costco")
    make_shopping_item(
        "Coffee beans",
        store_id=store.id,
        sort_order=-99,
        completed=True,
        completed_at=AFTER_LOCAL_MIDNIGHT,
    )
    make_shopping_item("Paper towels", store_id=store.id, sort_order=5)

    assert item_names(client.get("/api/shopping/items").json()) == [
        "Paper towels",
        "Coffee beans",
    ]


def test_purchased_items_stay_newest_first_among_themselves(
    client, make_shopping_item, frozen_now, local_timezone
):
    """Their tier falls through to `created_at DESC`, as it did before ordering."""
    local_timezone("America/Chicago")
    frozen_now(NOON)
    make_shopping_item(
        "Older",
        sort_order=0,
        completed=True,
        completed_at=AFTER_LOCAL_MIDNIGHT,
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    make_shopping_item(
        "Newer",
        sort_order=9,
        completed=True,
        completed_at=AFTER_LOCAL_MIDNIGHT,
        created_at=datetime(2026, 3, 10, tzinfo=UTC),
    )

    assert item_names(client.get("/api/shopping/items").json()) == ["Newer", "Older"]


# --- Reorder -------------------------------------------------------------------


def test_reorder_rewrites_the_order_of_a_store(client, make_store, make_shopping_item):
    store = make_store("Costco")
    towels = make_shopping_item("Paper towels", store_id=store.id, sort_order=0)
    batteries = make_shopping_item("Batteries", store_id=store.id, sort_order=1)

    response = client.post(
        "/api/shopping/items/reorder",
        json={"store_id": store.id, "item_ids": [batteries.id, towels.id]},
    )

    assert response.status_code == 200
    assert item_names(response.json()) == ["Batteries", "Paper towels"]
    assert item_names(client.get("/api/shopping/items").json()) == [
        "Batteries",
        "Paper towels",
    ]


def test_reorder_moves_an_item_into_the_destination_store(
    client, make_store, make_shopping_item
):
    """The cross-store drag: one payload carries the move and the position."""
    costco = make_store("Costco")
    trader_joes = make_store("Trader Joe's")
    milk = make_shopping_item("Almond milk", store_id=costco.id, sort_order=0)
    dumplings = make_shopping_item(
        "Frozen dumplings", store_id=trader_joes.id, sort_order=0
    )

    client.post(
        "/api/shopping/items/reorder",
        json={"store_id": trader_joes.id, "item_ids": [milk.id, dumplings.id]},
    )

    moved = next(
        i for i in client.get("/api/shopping/items").json() if i["id"] == milk.id
    )
    assert moved["store_id"] == trader_joes.id
    assert moved["sort_order"] == 0


def test_reorder_can_move_an_item_to_the_catch_all(
    client, make_store, make_shopping_item
):
    store = make_store("Costco")
    stamps = make_shopping_item("Stamps", store_id=store.id, sort_order=0)

    client.post(
        "/api/shopping/items/reorder", json={"store_id": None, "item_ids": [stamps.id]}
    )

    moved = client.get("/api/shopping/items").json()[0]
    assert moved["store_id"] is None


def test_reorder_leaves_the_store_the_item_left_alone(
    client, make_store, make_shopping_item
):
    """The gap is deliberate: positions are compared, never counted."""
    costco = make_store("Costco")
    trader_joes = make_store("Trader Joe's")
    towels = make_shopping_item("Paper towels", store_id=costco.id, sort_order=0)
    batteries = make_shopping_item("Batteries", store_id=costco.id, sort_order=1)
    chicken = make_shopping_item("Rotisserie chicken", store_id=costco.id, sort_order=2)

    client.post(
        "/api/shopping/items/reorder",
        json={"store_id": trader_joes.id, "item_ids": [batteries.id]},
    )

    remaining = [
        i
        for i in client.get("/api/shopping/items").json()
        if i["store_id"] == costco.id
    ]
    assert [i["id"] for i in remaining] == [towels.id, chicken.id]


def test_reorder_with_an_unknown_item_changes_nothing(
    client, make_store, make_shopping_item
):
    """All or nothing — a half-applied order is one the user never asked for."""
    store = make_store("Costco")
    towels = make_shopping_item("Paper towels", store_id=store.id, sort_order=0)
    batteries = make_shopping_item("Batteries", store_id=store.id, sort_order=1)

    response = client.post(
        "/api/shopping/items/reorder",
        json={"store_id": store.id, "item_ids": [batteries.id, 9999, towels.id]},
    )

    assert response.status_code == 404
    assert item_names(client.get("/api/shopping/items").json()) == [
        "Paper towels",
        "Batteries",
    ]


def test_reorder_rejects_an_unknown_store(client, make_shopping_item):
    item = make_shopping_item("Stamps")

    response = client.post(
        "/api/shopping/items/reorder", json={"store_id": 9999, "item_ids": [item.id]}
    )

    assert response.status_code == 422


def test_reorder_keeps_the_first_mention_of_a_repeated_id(
    client, make_store, make_shopping_item
):
    """A duplicate would otherwise assign two positions and let the later win."""
    store = make_store("Costco")
    towels = make_shopping_item("Paper towels", store_id=store.id, sort_order=0)
    batteries = make_shopping_item("Batteries", store_id=store.id, sort_order=1)

    response = client.post(
        "/api/shopping/items/reorder",
        json={
            "store_id": store.id,
            "item_ids": [batteries.id, towels.id, batteries.id],
        },
    )

    assert item_names(response.json()) == ["Batteries", "Paper towels"]


def test_reorder_of_nothing_is_a_no_op(client, make_store, make_shopping_item):
    store = make_store("Costco")
    make_shopping_item("Paper towels", store_id=store.id, sort_order=3)

    response = client.post(
        "/api/shopping/items/reorder", json={"store_id": store.id, "item_ids": []}
    )

    assert response.status_code == 200
    assert response.json() == []
    assert client.get("/api/shopping/items").json()[0]["sort_order"] == 3


def test_reorder_is_idempotent(client, make_store, make_shopping_item):
    store = make_store("Costco")
    towels = make_shopping_item("Paper towels", store_id=store.id, sort_order=0)
    batteries = make_shopping_item("Batteries", store_id=store.id, sort_order=1)
    payload = {"store_id": store.id, "item_ids": [batteries.id, towels.id]}

    client.post("/api/shopping/items/reorder", json=payload)
    client.post("/api/shopping/items/reorder", json=payload)

    assert item_names(client.get("/api/shopping/items").json()) == [
        "Batteries",
        "Paper towels",
    ]


def test_changing_store_through_the_edit_form_replaces_the_item(
    client, make_store, make_shopping_item
):
    """A rank held at the old store means nothing at the new one."""
    costco = make_store("Costco")
    trader_joes = make_store("Trader Joe's")
    make_shopping_item("Almond milk", store_id=trader_joes.id, sort_order=0)
    towels = make_shopping_item("Paper towels", store_id=costco.id, sort_order=7)

    client.put(f"/api/shopping/items/{towels.id}", json={"store_id": trader_joes.id})

    assert item_names(client.get("/api/shopping/items").json()) == [
        "Paper towels",
        "Almond milk",
    ]


def test_editing_an_item_without_moving_it_keeps_its_place(
    client, make_store, make_shopping_item
):
    """Renaming is not a move: only a real store change re-places a row."""
    store = make_store("Costco")
    towels = make_shopping_item("Paper towels", store_id=store.id, sort_order=7)

    client.put(f"/api/shopping/items/{towels.id}", json={"name": "Kitchen roll"})

    assert client.get("/api/shopping/items").json()[0]["sort_order"] == 7
