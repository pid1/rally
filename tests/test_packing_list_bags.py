"""Bag owners, bags inside bags, and grabbing bags on a day (#262).

A bag reads three ways: as the household has it (Manage Bags), as a template
reads it, and as one day reads it, each winning over the one before — the
overlay items already follow. Which bags are on a list is never stored: it is
every bag an item on the list is in, and every bag those go in. These tests
pin that derivation, the precedence, the grab checks, and the cascades that
SQLite will not do for us.
"""

from datetime import UTC, datetime

import pytest

from rally.models import (
    PackingListDay,
    PackingListDayBag,
    PackingListDayBagCheck,
    PackingListTemplateBag,
)

# Thursday, 1 October 2026, mid-morning in Chicago.
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
SATURDAY = "2026-10-03"
SUNDAY = "2026-10-04"


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _template(client, name="Beach week", **extra):
    return _ok(client.post("/api/packing-list-templates", json={"name": name, **extra}), 201)


def _bag(client, name, **defaults):
    bag = _ok(client.post("/api/packing-list-bags", json={"name": name}), 201)
    if defaults:
        bag = _ok(client.put(f"/api/packing-list-bags/{bag['id']}", json=defaults))
    return bag


def _item(client, template_id, name, **extra):
    return _ok(
        client.post(
            f"/api/packing-list-templates/{template_id}/items", json={"name": name, **extra}
        ),
        201,
    )


def _day(client, template_id, day=SATURDAY):
    return _ok(
        client.post(
            "/api/packing-list-days", json={"packing_list_template_id": template_id, "date": day}
        ),
        201,
    )


def _get_day(client, day_id):
    return _ok(client.get(f"/api/packing-list-days/{day_id}"))


def _get_template(client, template_id):
    return _ok(client.get(f"/api/packing-list-templates/{template_id}"))


def _bags(payload):
    """(name, owner_id, parent name) for each bag, in reading order."""
    names = {b["id"]: b["name"] for b in payload["bags"]}
    return [(b["name"], b["owner_id"], names.get(b["parent_bag_id"])) for b in payload["bags"]]


@pytest.fixture
def beach(client, make_member):
    """Dad's suitcase, Emma's toiletries bag in it, Mom's pool bag, an empty
    cooler; a template with things in the toiletries bag and the pool bag, on
    Saturday."""
    dad, mom, emma = make_member("Dad"), make_member("Mom"), make_member("Emma")
    suitcase = _bag(client, "Suitcase", owner_id=dad.id)
    toiletries = _bag(client, "Toiletries bag", owner_id=emma.id, parent_bag_id=suitcase["id"])
    pool = _bag(client, "Pool bag", owner_id=mom.id)
    cooler = _bag(client, "Cooler")
    template = _template(client)
    toothbrush = _item(client, template["id"], "Toothbrush", bag_id=toiletries["id"])
    towel = _item(client, template["id"], "Towel", bag_id=pool["id"])
    _item(client, template["id"], "Hat")
    day = _day(client, template["id"])
    return {
        "dad": dad,
        "mom": mom,
        "emma": emma,
        "suitcase": suitcase,
        "toiletries": toiletries,
        "pool": pool,
        "cooler": cooler,
        "template": template,
        "toothbrush": toothbrush,
        "towel": towel,
        "day": day,
    }


# --- Household defaults --------------------------------------------------------------


def test_a_bag_has_a_household_owner_and_parent(client, beach):
    listed = {b["name"]: b for b in _ok(client.get("/api/packing-list-bags"))}
    assert listed["Toiletries bag"]["owner_id"] == beach["emma"].id
    assert listed["Toiletries bag"]["parent_bag_id"] == beach["suitcase"]["id"]
    assert listed["Cooler"]["owner_id"] is None
    assert listed["Cooler"]["parent_bag_id"] is None


def test_updating_a_bag_is_partial(client, beach):
    bag_id = beach["toiletries"]["id"]
    renamed = _ok(client.put(f"/api/packing-list-bags/{bag_id}", json={"name": "Wash bag"}))
    assert renamed["owner_id"] == beach["emma"].id
    assert renamed["parent_bag_id"] == beach["suitcase"]["id"]
    cleared = _ok(client.put(f"/api/packing-list-bags/{bag_id}", json={"parent_bag_id": None}))
    assert cleared["name"] == "Wash bag"
    assert cleared["parent_bag_id"] is None


def test_unknown_owner_or_parent_is_422(client, beach):
    url = f"/api/packing-list-bags/{beach['cooler']['id']}"
    assert client.put(url, json={"owner_id": 999}).status_code == 422
    assert client.put(url, json={"parent_bag_id": 999}).status_code == 422


@pytest.mark.parametrize("which", ["suitcase", "toiletries"])
def test_a_bag_cannot_go_inside_itself_or_a_bag_inside_it(client, beach, which):
    response = client.put(
        f"/api/packing-list-bags/{beach['suitcase']['id']}",
        json={"parent_bag_id": beach[which]["id"]},
    )
    assert response.status_code == 422
    assert "inside itself" in response.json()["detail"]


# --- Which bags are on a list --------------------------------------------------------


def test_a_days_bags_are_what_its_items_are_in_and_what_those_go_in(client, beach):
    day = _get_day(client, beach["day"]["id"])
    # The cooler holds nothing, so it is not on the day. The suitcase holds
    # only the toiletries bag, and is. Outermost first, A to Z, then inside.
    assert _bags(day) == [
        ("Pool bag", beach["mom"].id, None),
        ("Suitcase", beach["dad"].id, None),
        ("Toiletries bag", beach["emma"].id, "Suitcase"),
    ]
    assert (day["bags_total"], day["bags_checked"]) == (3, 0)


def test_a_template_lists_its_bags_too(client, beach):
    template = _get_template(client, beach["template"]["id"])
    assert [b["name"] for b in template["bags"]] == ["Pool bag", "Suitcase", "Toiletries bag"]
    assert not any(b["checked"] for b in template["bags"])


def test_nesting_goes_any_depth(client, beach):
    pouch = _bag(client, "Pouch", parent_bag_id=beach["toiletries"]["id"])
    _item(client, beach["template"]["id"], "Floss", bag_id=pouch["id"])
    assert [(n, p) for n, _, p in _bags(_get_day(client, beach["day"]["id"]))] == [
        ("Pool bag", None),
        ("Suitcase", None),
        ("Toiletries bag", "Suitcase"),
        ("Pouch", "Toiletries bag"),
    ]


def test_a_loop_in_stored_readings_reads_each_bag_once(client, db_session, beach):
    """The household puts the toiletries bag in the suitcase; this day puts the
    suitcase in the toiletries bag. Readings at two levels can combine into a
    loop no single write made; reading it must draw each bag once, not hang."""
    db_session.add(
        PackingListDayBag(
            day_id=beach["day"]["id"],
            bag_id=beach["suitcase"]["id"],
            owner_id=beach["dad"].id,
            parent_bag_id=beach["toiletries"]["id"],
        )
    )
    db_session.commit()
    day = _get_day(client, beach["day"]["id"])
    ids = [b["id"] for b in day["bags"]]
    assert len(ids) == len(set(ids)) == 3
    # Both bags in the loop go in nothing here.
    looped = {b["name"]: b["parent_bag_id"] for b in day["bags"]}
    assert looped["Suitcase"] is None and looped["Toiletries bag"] is None


# --- Readings: household, template, day ----------------------------------------------


def test_a_template_reading_reaches_its_days_and_a_day_reading_wins(client, beach):
    template_id, day_id = beach["template"]["id"], beach["day"]["id"]
    pool_id = beach["pool"]["id"]
    other_day = _day(client, template_id, SUNDAY)

    template = _ok(
        client.put(
            f"/api/packing-list-templates/{template_id}/bags/{pool_id}",
            json={"owner_id": beach["dad"].id, "parent_bag_id": beach["suitcase"]["id"]},
        )
    )
    assert [b for b in template["bags"] if b["id"] == pool_id][0]["changed"] is True
    for d in (day_id, other_day["id"]):
        pool = [b for b in _get_day(client, d)["bags"] if b["id"] == pool_id][0]
        assert pool["owner_id"] == beach["dad"].id
        assert pool["parent_bag_id"] == beach["suitcase"]["id"]
        # The day reads the template's reading; it has not changed the bag itself.
        assert pool["changed"] is False

    day = _ok(
        client.put(
            f"/api/packing-list-days/{day_id}/bags/{pool_id}", json={"owner_id": beach["mom"].id}
        )
    )
    pool = [b for b in day["bags"] if b["id"] == pool_id][0]
    # Owner is the day's; the parent was copied from how it read, whole.
    assert (pool["owner_id"], pool["parent_bag_id"], pool["changed"]) == (
        beach["mom"].id,
        beach["suitcase"]["id"],
        True,
    )
    sunday_pool = [b for b in _get_day(client, other_day["id"])["bags"] if b["id"] == pool_id][0]
    assert sunday_pool["owner_id"] == beach["dad"].id

    # Reset on the day: back to the template's reading.
    day = _ok(client.delete(f"/api/packing-list-days/{day_id}/bags/{pool_id}/reading"))
    assert [b for b in day["bags"] if b["id"] == pool_id][0]["owner_id"] == beach["dad"].id
    # Reset on the template: back to the household's.
    _ok(client.delete(f"/api/packing-list-templates/{template_id}/bags/{pool_id}/reading"))
    pool = [b for b in _get_day(client, day_id)["bags"] if b["id"] == pool_id][0]
    assert (pool["owner_id"], pool["parent_bag_id"]) == (beach["mom"].id, None)


def test_a_reading_validates_like_the_household(client, beach):
    template_id, day_id = beach["template"]["id"], beach["day"]["id"]
    suitcase, toiletries = beach["suitcase"]["id"], beach["toiletries"]["id"]
    put_t = f"/api/packing-list-templates/{template_id}/bags/{suitcase}"
    assert client.put(put_t, json={"owner_id": 999, "parent_bag_id": None}).status_code == 422
    assert (
        client.put(put_t, json={"owner_id": None, "parent_bag_id": toiletries}).status_code == 422
    )
    put_d = f"/api/packing-list-days/{day_id}/bags/{suitcase}"
    assert client.put(put_d, json={"parent_bag_id": toiletries}).status_code == 422
    assert client.put(put_d, json={"parent_bag_id": 999}).status_code == 422


def test_a_bag_not_on_the_list_is_404(client, beach):
    cooler = beach["cooler"]["id"]
    assert (
        client.put(
            f"/api/packing-list-templates/{beach['template']['id']}/bags/{cooler}",
            json={"owner_id": None, "parent_bag_id": None},
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/packing-list-days/{beach['day']['id']}/bags/{cooler}", json={"checked": True}
        ).status_code
        == 404
    )


# --- Grabbing --------------------------------------------------------------------------


def test_grabbing_a_bag_counts_on_its_own(client, beach):
    day_id = beach["day"]["id"]
    day = _ok(
        client.put(
            f"/api/packing-list-days/{day_id}/bags/{beach['suitcase']['id']}",
            json={"checked": True},
        )
    )
    assert (day["checked"], day["bags_checked"], day["bags_total"]) == (0, 1, 3)
    day = _ok(
        client.put(
            f"/api/packing-list-days/{day_id}/bags/{beach['suitcase']['id']}",
            json={"checked": False},
        )
    )
    assert day["bags_checked"] == 0


def test_check_all_and_uncheck_all_include_bags(client, beach):
    day_id = beach["day"]["id"]
    day = _ok(client.post(f"/api/packing-list-days/{day_id}/check-all"))
    assert (day["checked"], day["total"], day["bags_checked"], day["bags_total"]) == (3, 3, 3, 3)
    day = _ok(client.post(f"/api/packing-list-days/{day_id}/reset"))
    assert (day["checked"], day["bags_checked"]) == (0, 0)


def test_a_bag_that_leaves_a_day_comes_back_unchecked(client, beach):
    day_id, pool_id = beach["day"]["id"], beach["pool"]["id"]
    towel = beach["towel"]["id"]
    _ok(client.put(f"/api/packing-list-days/{day_id}/bags/{pool_id}", json={"checked": True}))
    _ok(
        client.put(f"/api/packing-list-days/{day_id}/template-items/{towel}", json={"bag_id": None})
    )
    day = _ok(
        client.put(
            f"/api/packing-list-days/{day_id}/template-items/{towel}", json={"bag_id": pool_id}
        )
    )
    assert [b for b in day["bags"] if b["id"] == pool_id][0]["checked"] is False


def test_a_past_day_keeps_its_grab_checks(client, db_session, beach):
    past = PackingListDay(packing_list_template_id=beach["template"]["id"], date="2026-09-30")
    db_session.add(past)
    db_session.flush()
    # A check for a bag that is not on the day: the archive leaves it be.
    db_session.add(PackingListDayBagCheck(day_id=past.id, bag_id=beach["cooler"]["id"]))
    db_session.commit()
    _get_day(client, past.id)
    assert db_session.query(PackingListDayBagCheck).filter_by(day_id=past.id).count() == 1
    base = f"/api/packing-list-days/{past.id}/bags/{beach['pool']['id']}"
    assert client.put(base, json={"checked": True}).status_code == 403
    assert client.delete(f"{base}/reading").status_code == 403
    assert client.post(f"{base}/remove").status_code == 403


# --- Removing a bag from a list -----------------------------------------------------------


def test_removing_a_bag_from_a_day_moves_its_items_to_no_bag_there_only(client, beach):
    day_id, template_id = beach["day"]["id"], beach["template"]["id"]
    suitcase, toiletries = beach["suitcase"]["id"], beach["toiletries"]["id"]
    _ok(client.put(f"/api/packing-list-days/{day_id}/bags/{toiletries}", json={"checked": True}))
    pouch = _bag(client, "Pouch", parent_bag_id=toiletries)
    _ok(
        client.post(
            f"/api/packing-list-days/{day_id}/day-items",
            json={"name": "Floss", "bag_id": pouch["id"]},
        ),
        201,
    )

    day = _ok(client.post(f"/api/packing-list-days/{day_id}/bags/{toiletries}/remove"))
    items = {i["name"]: i for i in day["items"]}
    # Nothing left the day; the toothbrush moved to No bag through a reading.
    assert set(items) == {"Toothbrush", "Towel", "Hat", "Floss"}
    assert (items["Toothbrush"]["bag_id"], items["Toothbrush"]["changed"]) == (None, True)
    # The pouch went in the toiletries bag; on this day it goes in nothing.
    assert [(n, p) for n, _, p in _bags(day)] == [("Pool bag", None), ("Pouch", None)]
    assert suitcase not in {b["id"] for b in day["bags"]}
    # The template, and so its other days, keep the bag.
    template = _get_template(client, template_id)
    assert [i["bag_id"] for i in template["items"] if i["name"] == "Toothbrush"] == [toiletries]


def test_removing_a_bag_from_a_template_reaches_its_days(client, beach):
    template_id = beach["template"]["id"]
    suitcase = beach["suitcase"]["id"]
    _item(client, template_id, "Sandals", bag_id=suitcase)
    template = _ok(client.post(f"/api/packing-list-templates/{template_id}/bags/{suitcase}/remove"))
    assert [i["bag_id"] for i in template["items"] if i["name"] == "Sandals"] == [None]
    # The toiletries bag still holds the toothbrush, and goes in nothing on
    # this template now — so the suitcase is gone from it.
    assert [(n, p) for n, _, p in _bags(template)] == [
        ("Pool bag", None),
        ("Toiletries bag", None),
    ]
    assert [(n, p) for n, _, p in _bags(_get_day(client, beach["day"]["id"]))] == [
        ("Pool bag", None),
        ("Toiletries bag", None),
    ]
    # The household still puts the toiletries bag in the suitcase.
    listed = {b["name"]: b for b in _ok(client.get("/api/packing-list-bags"))}
    assert listed["Toiletries bag"]["parent_bag_id"] == suitcase


# --- Cascades ------------------------------------------------------------------------------


def test_deleting_a_bag_puts_the_bags_inside_it_at_the_top(client, db_session, beach):
    suitcase, toiletries = beach["suitcase"]["id"], beach["toiletries"]["id"]
    day_id, template_id = beach["day"]["id"], beach["template"]["id"]
    _ok(
        client.put(
            f"/api/packing-list-templates/{template_id}/bags/{toiletries}",
            json={"owner_id": None, "parent_bag_id": suitcase},
        )
    )
    _ok(client.put(f"/api/packing-list-days/{day_id}/bags/{suitcase}", json={"checked": True}))
    assert client.delete(f"/api/packing-list-bags/{suitcase}").status_code == 204

    listed = {b["name"]: b for b in _ok(client.get("/api/packing-list-bags"))}
    assert listed["Toiletries bag"]["parent_bag_id"] is None
    assert (
        db_session.query(PackingListTemplateBag).filter_by(bag_id=toiletries).one().parent_bag_id
        is None
    )
    assert db_session.query(PackingListDayBagCheck).filter_by(bag_id=suitcase).count() == 0
    assert [n for n, _, _ in _bags(_get_day(client, day_id))] == ["Pool bag", "Toiletries bag"]


def test_deleting_a_template_keeps_how_its_days_read_their_bags(client, beach):
    template_id, day_id, pool = beach["template"]["id"], beach["day"]["id"], beach["pool"]["id"]
    _ok(
        client.put(
            f"/api/packing-list-templates/{template_id}/bags/{pool}",
            json={"owner_id": beach["dad"].id, "parent_bag_id": None},
        )
    )
    before = _bags(_get_day(client, day_id))
    assert client.delete(f"/api/packing-list-templates/{template_id}").status_code == 204
    assert _bags(_get_day(client, day_id)) == before


def test_copies_bring_the_templates_readings(client, beach):
    template_id, pool = beach["template"]["id"], beach["pool"]["id"]
    _ok(
        client.put(
            f"/api/packing-list-templates/{template_id}/bags/{pool}",
            json={"owner_id": beach["dad"].id, "parent_bag_id": None},
        )
    )
    copy = _ok(
        client.post(
            "/api/packing-list-templates",
            json={"name": "Beach weekend", "copy_from_template_id": template_id},
        ),
        201,
    )
    one_off = _ok(
        client.post(
            "/api/packing-list-days",
            json={
                "name": "Lake",
                "date": SUNDAY,
                "pack_days_before": 0,
                "copy_from_template_id": template_id,
            },
        ),
        201,
    )
    for payload in (copy, one_off):
        assert [b for b in payload["bags"] if b["id"] == pool][0]["owner_id"] == beach["dad"].id


def test_deleting_a_member_makes_their_bags_everyones(client, db_session, beach):
    template_id, day_id = beach["template"]["id"], beach["day"]["id"]
    pool, dad = beach["pool"]["id"], beach["dad"].id
    _ok(
        client.put(
            f"/api/packing-list-templates/{template_id}/bags/{pool}",
            json={"owner_id": dad, "parent_bag_id": None},
        )
    )
    _ok(client.put(f"/api/packing-list-days/{day_id}/bags/{pool}", json={"owner_id": dad}))
    assert client.delete(f"/api/family/{dad}").status_code in (200, 204)
    listed = {b["name"]: b for b in _ok(client.get("/api/packing-list-bags"))}
    assert listed["Suitcase"]["owner_id"] is None
    assert db_session.query(PackingListTemplateBag).one().owner_id is None
    assert db_session.query(PackingListDayBag).one().owner_id is None


def test_deleting_a_day_takes_its_readings_and_grab_checks(client, db_session, beach):
    day_id, pool = beach["day"]["id"], beach["pool"]["id"]
    _ok(client.put(f"/api/packing-list-days/{day_id}/bags/{pool}", json={"checked": True}))
    _ok(client.put(f"/api/packing-list-days/{day_id}/bags/{pool}", json={"owner_id": None}))
    assert client.delete(f"/api/packing-list-days/{day_id}").status_code == 204
    assert db_session.query(PackingListDayBag).count() == 0
    assert db_session.query(PackingListDayBagCheck).count() == 0


# --- Unique by name and owner -------------------------------------------------------------


def test_two_people_can_each_have_a_bag_of_one_name(client, make_member):
    emma, jake = make_member("Emma"), make_member("Jake")
    _bag(client, "Backpack", owner_id=emma.id)
    _bag(client, "Backpack", owner_id=jake.id)
    names = [(b["name"], b["owner_id"]) for b in _ok(client.get("/api/packing-list-bags"))]
    assert sorted(names) == [("Backpack", emma.id), ("Backpack", jake.id)]


def test_one_owner_cannot_have_two_bags_of_one_name(client, make_member):
    emma = make_member("Emma")
    cooler = _bag(client, "Cooler")
    # An ownerless bag of the name already exists, ignoring case.
    response = client.post("/api/packing-list-bags", json={"name": "COOLER"})
    assert response.status_code == 409
    assert response.json()["detail"] == 'There\'s already an unowned bag called "COOLER".'

    backpack = _bag(client, "Backpack", owner_id=emma.id)
    # Renaming or re-owning into another bag's name and owner is a clash too.
    second = _bag(client, "Bookbag", owner_id=emma.id)
    response = client.put(f"/api/packing-list-bags/{second['id']}", json={"name": "backpack"})
    assert response.status_code == 409
    assert response.json()["detail"] == 'Emma already has a bag called "backpack".'
    response = client.put(
        f"/api/packing-list-bags/{cooler['id']}", json={"name": "Backpack", "owner_id": emma.id}
    )
    assert response.status_code == 409
    # Renaming a bag to its own name in other case is not a clash with itself.
    assert (
        client.put(
            f"/api/packing-list-bags/{backpack['id']}", json={"name": "BACKPACK"}
        ).status_code
        == 200
    )


def test_a_typed_bag_is_the_owners_then_the_unowned_then_new(client, make_member):
    emma, jake, mom = make_member("Emma"), make_member("Jake"), make_member("Mom")
    emmas = _bag(client, "Backpack", owner_id=emma.id)
    _bag(client, "Backpack", owner_id=jake.id)
    template = _template(client)

    assert (
        _item(client, template["id"], "Library book", owner_id=emma.id, bag="backpack")["bag_id"]
        == emmas["id"]
    )
    # Nobody's item, and no ownerless Backpack: a new one, with no owner.
    shared = _item(client, template["id"], "Snacks", bag="Backpack")["bag_id"]
    bags = {b["id"]: b for b in _ok(client.get("/api/packing-list-bags"))}
    assert len(bags) == 3
    assert bags[shared]["owner_id"] is None
    # Mom has no Backpack of her own: hers is the ownerless one.
    assert (
        _item(client, template["id"], "Wipes", owner_id=mom.id, bag="Backpack")["bag_id"] == shared
    )

    # Changing the owner and naming the bag in one edit finds the new owner's.
    wipes = [i for i in _get_template(client, template["id"])["items"] if i["name"] == "Wipes"][0]
    moved = _ok(
        client.put(
            f"/api/packing-list-templates/{template['id']}/items/{wipes['id']}",
            json={"owner_id": emma.id, "bag": "Backpack"},
        )
    )
    assert moved["bag_id"] == emmas["id"]


def test_a_list_cannot_read_two_bags_as_one_name_and_owner(client, make_member):
    emma = make_member("Emma")
    emmas_cooler = _bag(client, "Cooler", owner_id=emma.id)
    shared_cooler = _bag(client, "Cooler")
    template = _template(client)
    _item(client, template["id"], "Ice packs", bag_id=shared_cooler["id"])
    _item(client, template["id"], "Juice boxes", bag_id=emmas_cooler["id"])
    day = _day(client, template["id"])

    response = client.put(
        f"/api/packing-list-templates/{template['id']}/bags/{shared_cooler['id']}",
        json={"owner_id": emma.id, "parent_bag_id": None},
    )
    assert response.status_code == 409
    response = client.put(
        f"/api/packing-list-days/{day['id']}/bags/{shared_cooler['id']}",
        json={"owner_id": emma.id},
    )
    assert response.status_code == 409
    # Once Emma's own reads as somebody else's on that day, the shared one can be hers.
    jake = _ok(client.post("/api/family", json={"name": "Jake"}), 201)
    _ok(
        client.put(
            f"/api/packing-list-days/{day['id']}/bags/{emmas_cooler['id']}",
            json={"owner_id": jake["id"]},
        )
    )
    _ok(
        client.put(
            f"/api/packing-list-days/{day['id']}/bags/{shared_cooler['id']}",
            json={"owner_id": emma.id},
        )
    )
