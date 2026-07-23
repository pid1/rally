"""Tests for the family members router — straightforward CRUD + ordering."""

from rally.models import FamilyMember


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
