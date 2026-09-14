"""The devices router: registration, the device list, and per-device answers.

A device is the closest thing Rally has to a session — one browser, one token
it minted itself, one set of answers about how Rally should behave there. These
cover the API around that: saying hello, being listed, being forgotten, and the
answers that hang off the (person, device) pair.
"""

from rally import member_prefs
from rally.models import Device, MemberPreference

KEY = member_prefs.CALENDAR_DEFAULT_VIEW
AUTO = member_prefs.AUTO
PHONE = "device-phone"
TABLET = "device-tablet"


# --- Registration --------------------------------------------------------------


def test_a_device_registers_itself_with_a_put(client, db_session):
    """No enrollment step: the first request carrying a token creates it."""
    body = client.put(f"/api/devices/{PHONE}", json={"label": "iPhone"}).json()

    assert body["id"] == PHONE
    assert body["label"] == "iPhone"
    assert body["answer_count"] == 0
    assert db_session.query(Device).count() == 1


def test_saying_hello_without_a_label_keeps_the_stored_name(client):
    """A page load on every visit must not clobber a name somebody typed."""
    client.put(f"/api/devices/{PHONE}", json={"label": "iPhone"})
    client.put(f"/api/devices/{PHONE}", json={"label": "Jon's phone"})

    body = client.put(f"/api/devices/{PHONE}", json={}).json()

    assert body["label"] == "Jon's phone"


def test_a_device_can_be_renamed(client):
    client.put(f"/api/devices/{PHONE}", json={"label": "iPhone"})

    body = client.put(f"/api/devices/{PHONE}", json={"label": "Kitchen tablet"}).json()

    assert body["label"] == "Kitchen tablet"


def test_the_list_is_most_recently_seen_first(client):
    client.put(f"/api/devices/{PHONE}", json={"label": "iPhone"})
    client.put(f"/api/devices/{TABLET}", json={"label": "iPad"})

    listed = client.get("/api/devices").json()

    assert [device["id"] for device in listed] == [TABLET, PHONE]


def test_the_list_says_how_many_answers_each_device_carries(client, make_member):
    """So "forget this device" can say what it is about to throw away."""
    member = make_member("Jon")
    client.put(
        f"/api/devices/{TABLET}/preferences/{member.id}", json={"values": {KEY: "agenda:week"}}
    )

    listed = {device["id"]: device for device in client.get("/api/devices").json()}

    assert listed[TABLET]["answer_count"] == 1


# --- Answers -------------------------------------------------------------------


def test_a_device_nobody_has_configured_answers_auto(client, make_member):
    """Which is exactly what Rally did before any of this existed."""
    member = make_member("Jon")

    body = client.get(f"/api/devices/{PHONE}/preferences").json()

    assert body["device_id"] == PHONE
    assert body["members"][str(member.id)] == {KEY: AUTO}


def test_a_device_that_has_never_been_seen_is_not_a_404(client, make_member):
    """A brand new browser asking what to do is the ordinary first request."""
    make_member("Jon")
    assert client.get("/api/devices/never-heard-of-it/preferences").status_code == 200


def test_saving_an_answer_registers_the_device(client, db_session, make_member):
    """Saving a preference is the strongest statement that a device is real."""
    member = make_member("Jon")

    client.put(
        f"/api/devices/{PHONE}/preferences/{member.id}", json={"values": {KEY: "agenda:day"}}
    )

    assert db_session.query(Device).filter(Device.id == PHONE).first() is not None


def test_an_answer_on_one_device_never_reaches_another(client, make_member):
    """The whole point of keying on the device rather than on its width."""
    member = make_member("Jon")

    client.put(
        f"/api/devices/{PHONE}/preferences/{member.id}", json={"values": {KEY: "agenda:day"}}
    )

    phone = client.get(f"/api/devices/{PHONE}/preferences").json()
    tablet = client.get(f"/api/devices/{TABLET}/preferences").json()

    assert phone["members"][str(member.id)][KEY] == "agenda:day"
    assert tablet["members"][str(member.id)][KEY] == AUTO


def test_two_people_share_a_tablet_but_not_its_settings(client, make_member):
    jon = make_member("Jon")
    emma = make_member("Emma")

    client.put(f"/api/devices/{TABLET}/preferences/{jon.id}", json={"values": {KEY: "agenda:week"}})

    body = client.get(f"/api/devices/{TABLET}/preferences").json()

    assert body["members"][str(jon.id)][KEY] == "agenda:week"
    assert body["members"][str(emma.id)][KEY] == AUTO


def test_the_write_returns_the_resolved_answers(client, make_member):
    """So the page never has to know the defaults to render what it just saved."""
    member = make_member("Jon")

    body = client.put(
        f"/api/devices/{PHONE}/preferences/{member.id}", json={"values": {KEY: "agenda:rolling30"}}
    ).json()

    assert body["device_id"] == PHONE
    assert body["family_member_id"] == member.id
    assert body["values"] == {KEY: "agenda:rolling30"}


def test_auto_can_be_chosen_back(client, make_member):
    """ "Let Rally pick" is an answer, not only the absence of one."""
    member = make_member("Jon")
    client.put(
        f"/api/devices/{PHONE}/preferences/{member.id}", json={"values": {KEY: "agenda:day"}}
    )

    body = client.put(
        f"/api/devices/{PHONE}/preferences/{member.id}", json={"values": {KEY: AUTO}}
    ).json()

    assert body["values"][KEY] == AUTO


def test_an_unknown_setting_is_rejected(client, db_session, make_member):
    member = make_member("Jon")

    response = client.put(
        f"/api/devices/{PHONE}/preferences/{member.id}", json={"values": {"nope": "agenda:day"}}
    )

    assert response.status_code == 422
    assert db_session.query(MemberPreference).count() == 0


def test_a_value_outside_the_choices_is_rejected(client, db_session, make_member):
    """Including one the toolbar cannot draw: `calendar:rolling30` is not a
    landing view, because a grid has no rolling thirty days to show."""
    member = make_member("Jon")

    response = client.put(
        f"/api/devices/{PHONE}/preferences/{member.id}",
        json={"values": {KEY: "calendar:rolling30"}},
    )

    assert response.status_code == 422
    assert db_session.query(MemberPreference).count() == 0


def test_saving_for_a_member_who_does_not_exist_is_a_404(client):
    response = client.put(
        f"/api/devices/{PHONE}/preferences/9999", json={"values": {KEY: "agenda:day"}}
    )
    assert response.status_code == 404


# --- Forgetting ----------------------------------------------------------------


def test_forgetting_a_device_takes_its_answers_with_it(client, db_session, make_member):
    """Keeping them would mean a forgotten device that came back found its old
    preferences waiting, which is not what "forget" says."""
    jon = make_member("Jon")
    emma = make_member("Emma")
    client.put(f"/api/devices/{TABLET}/preferences/{jon.id}", json={"values": {KEY: "agenda:week"}})
    client.put(f"/api/devices/{TABLET}/preferences/{emma.id}", json={"values": {KEY: "agenda:day"}})
    client.put(f"/api/devices/{PHONE}/preferences/{jon.id}", json={"values": {KEY: "agenda:day"}})

    assert client.delete(f"/api/devices/{TABLET}").status_code == 204

    assert db_session.query(Device).filter(Device.id == TABLET).first() is None
    assert db_session.query(MemberPreference).count() == 1
    phone = client.get(f"/api/devices/{PHONE}/preferences").json()
    assert phone["members"][str(jon.id)][KEY] == "agenda:day"


def test_forgetting_a_device_rally_never_met_is_not_an_error(client):
    """Idempotent: the point is that it is gone, not that it was there."""
    assert client.delete("/api/devices/never-heard-of-it").status_code == 204


def test_a_forgotten_device_comes_back_with_no_settings(client, make_member):
    """The same browser, the same token, and a clean slate — which is what
    Rally does for a device it has never met."""
    member = make_member("Jon")
    client.put(
        f"/api/devices/{PHONE}/preferences/{member.id}", json={"values": {KEY: "agenda:day"}}
    )
    client.delete(f"/api/devices/{PHONE}")

    client.put(f"/api/devices/{PHONE}", json={"label": "iPhone"})
    body = client.get(f"/api/devices/{PHONE}/preferences").json()

    assert body["members"][str(member.id)][KEY] == AUTO
