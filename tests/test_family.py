"""Tests for the family members router — CRUD, ordering, and notifications."""

from rally.models import FamilyMember, MemberNotificationPref
from rally.notification_prefs import EVENT_CHANGE, SHOPPING_ADDED, defaults


def test_create_defaults_color(client):
    body = client.post("/api/family", json={"name": "Dad"}).json()
    assert body["name"] == "Dad"
    assert body["color"] == "#333333"
    assert body["id"] > 0


def test_create_with_color(client):
    body = client.post("/api/family", json={"name": "Mom", "color": "#ff0000"}).json()
    assert body["color"] == "#ff0000"


def test_list_ordered_by_name(client):
    client.post("/api/family", json={"name": "Zoe"})
    client.post("/api/family", json={"name": "Amy"})

    names = [m["name"] for m in client.get("/api/family").json()]

    assert names == ["Amy", "Zoe"]


def test_get_found_and_404(client):
    member = client.post("/api/family", json={"name": "Dad"}).json()

    assert client.get(f"/api/family/{member['id']}").json()["id"] == member["id"]
    assert client.get("/api/family/9999").status_code == 404


def test_update_fields(client):
    member = client.post("/api/family", json={"name": "Dad", "color": "#111111"}).json()

    body = client.put(
        f"/api/family/{member['id']}", json={"name": "Daddy", "color": "#222222"}
    ).json()

    assert body["name"] == "Daddy"
    assert body["color"] == "#222222"


def test_update_404(client):
    assert client.put("/api/family/9999", json={"name": "x"}).status_code == 404


def test_delete(client, db_session):
    member = client.post("/api/family", json={"name": "Dad"}).json()

    assert client.delete(f"/api/family/{member['id']}").status_code == 204
    assert db_session.get(FamilyMember, member["id"]) is None


def test_delete_404(client):
    assert client.delete("/api/family/9999").status_code == 404


# --- Notification preferences --------------------------------------------------


def test_a_new_member_carries_the_resolved_defaults(client):
    """Resolved, so no client has to know what the defaults are."""
    body = client.post("/api/family", json={"name": "Jake"}).json()

    assert body["notifications"] == defaults()


def test_a_new_member_can_be_created_with_preferences(client, db_session):
    body = client.post(
        "/api/family",
        json={"name": "Dad", "notifications": {SHOPPING_ADDED: True}},
    ).json()

    assert body["notifications"][SHOPPING_ADDED] is True
    assert db_session.query(MemberNotificationPref).count() == 1


def test_updating_notifications_is_partial(client):
    member = client.post("/api/family", json={"name": "Emma"}).json()

    client.put(f"/api/family/{member['id']}", json={"notifications": {EVENT_CHANGE: False}})
    body = client.put(
        f"/api/family/{member['id']}", json={"notifications": {SHOPPING_ADDED: True}}
    ).json()

    assert body["notifications"][EVENT_CHANGE] is False
    assert body["notifications"][SHOPPING_ADDED] is True


def test_updating_something_else_leaves_preferences_alone(client):
    member = client.post("/api/family", json={"name": "Emma"}).json()
    client.put(f"/api/family/{member['id']}", json={"notifications": {EVENT_CHANGE: False}})

    body = client.put(f"/api/family/{member['id']}", json={"name": "Em"}).json()

    assert body["notifications"][EVENT_CHANGE] is False


def test_an_unknown_kind_is_rejected_rather_than_stored(client, db_session):
    member = client.post("/api/family", json={"name": "Emma"}).json()

    response = client.put(f"/api/family/{member['id']}", json={"notifications": {"nope": True}})

    assert response.status_code == 422
    assert db_session.query(MemberNotificationPref).count() == 0


def test_creating_with_an_unknown_kind_is_rejected(client, db_session):
    response = client.post("/api/family", json={"name": "Emma", "notifications": {"nope": True}})

    assert response.status_code == 422
    assert db_session.query(FamilyMember).count() == 0


def test_a_member_with_no_key_keeps_the_preferences_they_set(client):
    """Held and explained rather than silently ineffective."""
    member = client.post("/api/family", json={"name": "Jake"}).json()

    body = client.put(
        f"/api/family/{member['id']}", json={"notifications": {SHOPPING_ADDED: True}}
    ).json()

    assert body["pushover_user_key"] is None
    assert body["notifications"][SHOPPING_ADDED] is True


def test_listing_members_carries_their_preferences(client):
    member = client.post("/api/family", json={"name": "Emma"}).json()
    client.put(f"/api/family/{member['id']}", json={"notifications": {EVENT_CHANGE: False}})

    listed = client.get("/api/family").json()

    assert listed[0]["notifications"][EVENT_CHANGE] is False


def test_deleting_a_member_takes_their_preferences_with_them(client, db_session):
    """Nothing enforces the reference, so the rows have to go by hand."""
    member = client.post("/api/family", json={"name": "Emma"}).json()
    client.put(f"/api/family/{member['id']}", json={"notifications": {EVENT_CHANGE: False}})
    assert db_session.query(MemberNotificationPref).count() == 1

    client.delete(f"/api/family/{member['id']}")

    assert db_session.query(MemberNotificationPref).count() == 0
