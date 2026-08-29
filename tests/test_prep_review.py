"""The LLM preparedness review.

Most of these are groundedness tests. The model is asked what is *absent*,
which is the prompt shape most likely to produce invention, so what it is shown
and what it is told matter more than the plumbing around them.
"""

import json

import pytest

from rally import prep_review
from rally.models import PrepReview
from rally.prep_review import (
    PrepReviewError,
    build_user_prompt,
    gather_inputs,
    normalize,
    run_review,
)

GOOD = {
    "assessment": "A solid water and food base with a clear gap around light.",
    "gaps": [
        {
            "item": "Spare headlamp batteries",
            "category": "Light",
            "why": "x",
            "priority": "high",
        },
        {"item": "Paper maps", "category": "Navigation", "why": "y", "priority": "low"},
    ],
    "strengths": ["Two Sawyer filters"],
    "assumptions": ["No ages were stated in family context"],
    "notes": "",
}


def fake_llm(payload=None, *, raw=None, boom=None):
    """An injectable stand-in for the configured provider."""
    captured = {}

    def call(user_prompt, system_prompt):
        captured["user"] = user_prompt
        captured["system"] = system_prompt
        if boom:
            raise RuntimeError(boom)
        return (raw if raw is not None else json.dumps(payload or GOOD)), "test-model"

    call.captured = captured
    return call


@pytest.fixture
def review_on(make_setting):
    make_setting("prep_review_enabled", "true")


# --- What the model is shown ---------------------------------------------------


class TestGrounding:
    def test_every_item_is_shown(self, db_session, make_prep_location, make_prep_item):
        """No sampling. The model cannot check a list it was not given."""
        loc = make_prep_location("Water")
        for n in range(40):
            make_prep_item(name=f"Item {n}", location_id=loc.id)

        inputs = gather_inputs(db_session)

        assert inputs.item_count == 40
        for n in range(40):
            assert f"Item {n}" in inputs.inventory

    def test_quantity_and_notes_reach_the_prompt(
        self, db_session, make_prep_location, make_prep_item
    ):
        loc = make_prep_location("Water")
        make_prep_item(
            name="Sawyer filters",
            quantity="2",
            notes="Backflush yearly",
            location_id=loc.id,
        )

        inventory = gather_inputs(db_session).inventory

        assert "Sawyer filters" in inventory
        assert "2" in inventory
        assert "Backflush yearly" in inventory

    def test_overdue_items_are_flagged_in_the_inventory(
        self, db_session, make_prep_item, make_setting
    ):
        make_setting("local_timezone", "UTC")
        make_prep_item(
            name="Water drums", refresh_mode="date", next_refresh_date="2020-01-01"
        )

        assert "OVERDUE" in gather_inputs(db_session).inventory

    def test_missing_context_is_labelled_not_omitted(self, db_session, make_prep_item):
        """An absent section lets the model quietly fill the hole."""
        make_prep_item(name="Spork")
        prompt = build_user_prompt(gather_inputs(db_session))

        assert "FAMILY CONTEXT:\n(not recorded)" in prompt
        assert "HOME:\n(not recorded)" in prompt

    def test_context_and_home_reach_the_prompt(
        self, db_session, make_prep_item, make_setting
    ):
        make_setting("family_context", "Rodryk is 3, Soren is an infant.")
        make_setting("home_location", "Highland Village, TX")
        make_prep_item(name="Spork")

        prompt = build_user_prompt(gather_inputs(db_session))

        assert "Rodryk is 3" in prompt
        assert "Highland Village, TX" in prompt

    def test_family_context_history_pointer_is_followed(
        self, db_session, make_prep_item, make_setting
    ):
        """The active context is the snapshot the pointer names, as the generator reads it."""
        from rally.models import AISettingsHistory

        row = AISettingsHistory(field_name="family_context", value="Ages: 3 and infant")
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        make_setting("current_family_context_history_id", str(row.id))
        make_prep_item(name="Spork")

        assert "Ages: 3 and infant" in gather_inputs(db_session).context

    def test_the_system_prompt_states_the_grounding_rules(self):
        p = prep_review.SYSTEM_PROMPT
        assert "ONLY evidence" in p
        assert "Never state or imply the family has something" in p
        assert "Never name a gap that the INVENTORY already covers" in p
        assert "explicitly stated in FAMILY CONTEXT" in p
        assert "assumptions" in p


# --- Normalizing a response ----------------------------------------------------


class TestNormalise:
    def test_drops_gaps_without_an_item(self):
        out = normalize({"gaps": [{"item": ""}, {"item": "Radio"}]})
        assert [g["item"] for g in out["gaps"]] == ["Radio"]

    def test_coerces_an_unknown_priority(self):
        assert (
            normalize({"gaps": [{"item": "X", "priority": "urgent"}]})["gaps"][0][
                "priority"
            ]
            == "medium"
        )

    def test_sorts_high_priority_first(self):
        out = normalize(
            {
                "gaps": [
                    {"item": "Low", "priority": "low"},
                    {"item": "High", "priority": "high"},
                    {"item": "Med", "priority": "medium"},
                ]
            }
        )
        assert [g["item"] for g in out["gaps"]] == ["High", "Med", "Low"]

    def test_caps_a_runaway_response(self):
        out = normalize({"gaps": [{"item": f"g{n}"} for n in range(200)]})
        assert len(out["gaps"]) == prep_review.MAX_GAPS

    def test_ignores_unrecognised_shapes(self):
        out = normalize(
            {"gaps": ["not a dict", {"item": "Radio"}], "strengths": "not a list"}
        )
        assert [g["item"] for g in out["gaps"]] == ["Radio"]
        assert out["strengths"] == []

    def test_rejects_a_non_object(self):
        with pytest.raises(PrepReviewError):
            normalize(["nope"])


# --- Running a review ----------------------------------------------------------


class TestRunReview:
    def test_stores_the_result(self, db_session, make_prep_item, review_on):
        make_prep_item(name="Spork")
        row = run_review(db_session, llm=fake_llm())

        assert row.id
        assert row.model == "test-model"
        assert row.item_count == 1
        assert row.data["gaps"][0]["item"] == "Spare headlamp batteries"
        assert db_session.query(PrepReview).count() == 1

    def test_is_off_by_default(self, db_session, make_prep_item):
        make_prep_item(name="Spork")
        with pytest.raises(PrepReviewError, match="turned off"):
            run_review(db_session, llm=fake_llm())

    def test_refuses_an_empty_inventory(self, db_session, review_on):
        with pytest.raises(PrepReviewError, match="nothing to review"):
            run_review(db_session, llm=fake_llm())

    def test_parses_a_fenced_response(self, db_session, make_prep_item, review_on):
        make_prep_item(name="Spork")
        raw = "Sure!\n```json\n" + json.dumps(GOOD) + "\n```\n"
        row = run_review(db_session, llm=fake_llm(raw=raw))
        assert row.data["assessment"].startswith("A solid water")

    def test_a_non_json_response_is_a_clean_error(
        self, db_session, make_prep_item, review_on
    ):
        make_prep_item(name="Spork")
        with pytest.raises(PrepReviewError, match="did not return JSON"):
            run_review(db_session, llm=fake_llm(raw="I cannot help with that."))

    def test_a_truncated_response_is_a_clean_error(
        self, db_session, make_prep_item, review_on
    ):
        make_prep_item(name="Spork")
        with pytest.raises(PrepReviewError, match="cut off"):
            run_review(db_session, llm=fake_llm(raw='{"assessment": "half'))

    def test_a_provider_failure_is_a_clean_error(
        self, db_session, make_prep_item, review_on
    ):
        make_prep_item(name="Spork")
        with pytest.raises(PrepReviewError, match="review call failed"):
            run_review(db_session, llm=fake_llm(boom="connection reset"))

    def test_nothing_is_stored_when_the_call_fails(
        self, db_session, make_prep_item, review_on
    ):
        make_prep_item(name="Spork")
        with pytest.raises(PrepReviewError):
            run_review(db_session, llm=fake_llm(boom="nope"))
        assert db_session.query(PrepReview).count() == 0


# --- API -----------------------------------------------------------------------


class TestReviewApi:
    def test_404_until_one_has_been_run(self, client):
        assert client.get("/api/preparedness/review").status_code == 404

    def test_reading_never_calls_the_model(
        self, client, db_session, make_prep_item, review_on
    ):
        """A page load must never spend money."""
        make_prep_item(name="Spork")
        run_review(db_session, llm=fake_llm())

        body = client.get("/api/preparedness/review").json()

        assert body["review"]["gaps"][0]["item"] == "Spare headlamp batteries"
        assert body["item_count"] == 1
        assert body["stale"] is False

    def test_staleness_is_reported_after_a_restock(
        self, client, db_session, make_prep_item, review_on
    ):
        make_prep_item(name="Spork")
        run_review(db_session, llm=fake_llm())
        make_prep_item(name="Radio")

        body = client.get("/api/preparedness/review").json()

        assert body["stale"] is True
        assert body["item_count"] == 1
        assert body["current_item_count"] == 2

    def test_post_reports_a_disabled_feature_as_400(self, client, make_prep_item):
        make_prep_item(name="Spork")
        r = client.post("/api/preparedness/review")
        assert r.status_code == 400
        assert "turned off" in r.json()["detail"]

    def test_latest_review_wins(self, db_session, make_prep_item, review_on):
        make_prep_item(name="Spork")
        run_review(db_session, llm=fake_llm())
        run_review(db_session, llm=fake_llm({"assessment": "second", "gaps": []}))

        assert prep_review.latest_review(db_session).data["assessment"] == "second"
