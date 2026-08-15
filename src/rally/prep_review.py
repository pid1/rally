"""LLM review of the preparedness inventory.

Answers one question: *what is missing?* — given what the family actually has,
who they are, and where they live.

Two design constraints shape everything here.

**Groundedness.** The model is being asked what is *absent*, which is exactly
the prompt shape that invites invention. It sees the full inventory and is told,
repeatedly and specifically, that the list is the only evidence of what the
family owns. A review that says "you have no water filter" when a Sawyer is
sitting in the Truck group is worse than no review at all — it teaches the
family to ignore the feature. Everything the model does not know goes in
``assumptions`` rather than being guessed.

**Cost.** A review is a real LLM call. It is snapshotted into ``prep_reviews``
and read back on view, following ``DashboardSnapshot``: a page load must never
trigger one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from rally import preparedness
from rally.models import FamilyMember, PrepItem, PrepReview, Setting
from rally.utils.settings import home_location

ENABLED_KEY = "prep_review_enabled"

PRIORITIES = ("high", "medium", "low")

# Caps on what comes back, so one strange response cannot produce a page of
# hundreds of rows. Generous enough that a real review is never truncated.
MAX_GAPS = 25
MAX_LIST = 12


class PrepReviewError(RuntimeError):
    """The review could not be produced. Carries a message fit for the UI."""


@dataclass
class ReviewInputs:
    """Everything the model is shown, kept separable so tests can assert on it."""

    inventory: str
    family: str
    context: str
    home: str
    item_count: int


# ── The prompt ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are reviewing a family's emergency preparedness inventory to identify what is missing.

GROUNDING RULES — these outrank everything else:

1. The INVENTORY section is the ONLY evidence of what this family owns. Read all
   of it before naming anything as missing.
2. Never state or imply the family has something that is not in the INVENTORY.
3. Never name a gap that the INVENTORY already covers. Before listing an item as
   missing, check the whole list for it, including items filed under an
   unexpected location or described in different words. A "Sawyer filter" is a
   water filter; "RESTOP 2" is sanitation; "Mora" is a fixed-blade knife.
4. If a category is partially covered, say what is there and what is thin, rather
   than calling the whole category missing.
5. Use ONLY ages that are explicitly stated in FAMILY CONTEXT. Do not infer an
   age from a name, a role, or the presence of an item. If ages are not stated,
   add an entry to "assumptions" saying so and keep age-specific advice general.
6. Use HOME only if it is provided. If it is absent, add an entry to
   "assumptions" saying so and keep advice climate-neutral.
7. If the inventory is too small or too sparse to review meaningfully, say that
   in "assessment" rather than generating a generic checklist.

SCOPE:

- Suggest categories and items, not brands, and not quantities you cannot derive.
- Do not give medical, dosage, or pharmaceutical advice. "A first aid kit" is a
  gap; what goes in it is not your call.
- You are a second pair of eyes, not an authority. Where an authoritative list
  exists (ready.gov, FEMA, the local emergency management office), it is fine to
  say so in "notes".

OUTPUT: a single JSON object, no prose before or after, no code fence:

{
  "assessment": "2-4 sentences on the overall state of this kit, specific to what you actually saw.",
  "gaps": [
    {
      "item": "Short name of the missing thing",
      "category": "e.g. Water, Food, Medical, Power, Shelter, Documents, Sanitation, Tools",
      "why": "One sentence tied to THIS family — their stated ages, their location, or what the inventory already implies about their plan.",
      "priority": "high" | "medium" | "low"
    }
  ],
  "strengths": ["What this kit genuinely does well, referencing real items"],
  "assumptions": ["Anything you did not know and did not guess"],
  "notes": "Optional closing note, or an empty string"
}

Order "gaps" by priority, highest first. Return an empty "gaps" array if you
genuinely find none — that is a valid and useful answer."""


def build_inventory_text(db: Session) -> tuple[str, int]:
    """The full inventory as text, grouped by location.

    Every item is shown. Sampling or summarising here would directly cause the
    hallucination the grounding rules exist to prevent: the model cannot check a
    list it was not given.
    """
    from rally.golist import build_groups

    groups = build_groups(db)
    if not groups:
        return "The inventory is empty.", 0

    today = preparedness.today_for(db)
    lines: list[str] = []
    count = 0
    for _lid, name, items in groups:
        lines.append(f"{name}:")
        for item in items:
            bits = [f"  - {item.name}"]
            if item.quantity:
                bits.append(f"(qty: {item.quantity})")
            if item.notes:
                bits.append(f"[{item.notes}]")
            status = preparedness.status_of(item, today)
            if status == "overdue":
                bits.append(f"** OVERDUE for refresh since {item.next_refresh_date} **")
            elif status == "due":
                bits.append(f"(refresh due {item.next_refresh_date})")
            lines.append(" ".join(bits))
            count += 1
        lines.append("")

    return "\n".join(lines).strip(), count


def build_family_text(db: Session) -> str:
    members = db.query(FamilyMember).order_by(FamilyMember.name.asc()).all()
    if not members:
        return "No family members are configured."
    return ", ".join(m.name for m in members)


def _family_context(db: Session) -> str:
    """The active family context, via the same history pointer the generator uses."""
    pointer = db.query(Setting).filter(Setting.key == "current_family_context_history_id").first()
    if pointer and pointer.value:
        from rally.models import AISettingsHistory

        try:
            row = (
                db.query(AISettingsHistory)
                .filter(AISettingsHistory.id == int(pointer.value))
                .first()
            )
        except TypeError, ValueError:
            row = None
        if row and row.value:
            return row.value.strip()

    direct = db.query(Setting).filter(Setting.key == "family_context").first()
    return (direct.value or "").strip() if direct else ""


def gather_inputs(db: Session) -> ReviewInputs:
    inventory, count = build_inventory_text(db)
    return ReviewInputs(
        inventory=inventory,
        family=build_family_text(db),
        context=_family_context(db),
        home=home_location(db),
        item_count=count,
    )


def build_user_prompt(inputs: ReviewInputs) -> str:
    """Assemble the review request.

    Sections that have no value are labelled as unknown rather than omitted.
    An absent section would let the model quietly fill the hole; an explicit
    "not recorded" is what the grounding rules point at when they require an
    entry in ``assumptions``.
    """
    context = inputs.context or "(not recorded)"
    home = inputs.home or "(not recorded)"

    return f"""FAMILY MEMBERS:
{inputs.family}

FAMILY CONTEXT:
{context}

HOME:
{home}

INVENTORY ({inputs.item_count} items):
{inputs.inventory}

Review this inventory and identify what is missing. Follow the grounding rules exactly."""


# ── Normalising the response ─────────────────────────────────────────────────


def _clean_str(value, limit: int = 500) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _clean_list(value, limit: int = MAX_LIST) -> list[str]:
    if not isinstance(value, list):
        return []
    out = [_clean_str(v) for v in value]
    return [v for v in out if v][:limit]


def normalise(payload: dict) -> dict:
    """Coerce a model response into the shape the UI renders.

    The model is asked for a specific schema and usually returns it, but the UI
    must not break on the day it does not. Anything unrecognised is dropped
    rather than passed through — a review is read by a person deciding what to
    buy, so a half-parsed field is worse than a missing one.
    """
    if not isinstance(payload, dict):
        raise PrepReviewError("The model did not return a review object.")

    gaps: list[dict] = []
    for raw in payload.get("gaps") or []:
        if not isinstance(raw, dict):
            continue
        item = _clean_str(raw.get("item"), 200)
        if not item:
            continue
        priority = _clean_str(raw.get("priority"), 20).lower()
        gaps.append(
            {
                "item": item,
                "category": _clean_str(raw.get("category"), 60),
                "why": _clean_str(raw.get("why"), 400),
                "priority": priority if priority in PRIORITIES else "medium",
            }
        )
        if len(gaps) >= MAX_GAPS:
            break

    order = {p: i for i, p in enumerate(PRIORITIES)}
    gaps.sort(key=lambda g: order.get(g["priority"], 1))

    return {
        "assessment": _clean_str(payload.get("assessment"), 1500),
        "gaps": gaps,
        "strengths": _clean_list(payload.get("strengths")),
        "assumptions": _clean_list(payload.get("assumptions")),
        "notes": _clean_str(payload.get("notes"), 800),
    }


# ── Running a review ─────────────────────────────────────────────────────────


def review_enabled(db: Session) -> bool:
    row = db.query(Setting).filter(Setting.key == ENABLED_KEY).first()
    return ((row.value or "").strip().lower() if row else "false") == "true"


def _default_llm():
    """Build a callable over Rally's configured provider.

    Reuses ``SummaryGenerator`` rather than opening a second client: it already
    resolves provider, model, API key and the per-provider token budget from DB
    settings with a config.toml fallback, and ``_call_llm`` handles streaming,
    prompt caching and truncation detection.
    """
    from rally.generator.generate import SummaryGenerator

    try:
        generator = SummaryGenerator()
    except Exception as exc:
        raise PrepReviewError(f"No LLM is configured: {exc}") from exc

    def call(user_prompt: str, system_prompt: str) -> tuple[str, str]:
        text = generator._call_llm(user_prompt, system_prompt=system_prompt, label="prep-review")
        return text, getattr(generator, "model", "") or ""

    return call


def run_review(db: Session, *, llm=None) -> PrepReview:
    """Run a review and store it. Raises ``PrepReviewError`` with a UI message."""
    if not review_enabled(db):
        raise PrepReviewError("Preparedness review is turned off in Settings.")

    inputs = gather_inputs(db)
    if inputs.item_count == 0:
        raise PrepReviewError("There is nothing to review yet — add some items first.")

    call = llm or _default_llm()

    try:
        raw, model = call(build_user_prompt(inputs), SYSTEM_PROMPT)
    except PrepReviewError:
        raise
    except Exception as exc:
        raise PrepReviewError(f"The review call failed: {exc}") from exc

    payload = _parse(raw)
    review = PrepReview(data=normalise(payload), model=model or None, item_count=inputs.item_count)
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def _parse(raw: str) -> dict:
    """Parse the model's JSON, tolerating a code fence or surrounding prose."""
    text = (raw or "").strip()
    if not text:
        raise PrepReviewError("The model returned an empty response.")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Same fallback the generator uses: find the outermost balanced object.
    start = text.find("{")
    if start == -1:
        raise PrepReviewError("The model did not return JSON.")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise PrepReviewError("The model's response was not valid JSON.") from exc

    raise PrepReviewError("The model's response was cut off before the JSON ended.")


def latest_review(db: Session) -> PrepReview | None:
    return db.query(PrepReview).order_by(PrepReview.created_at.desc(), PrepReview.id.desc()).first()


def current_item_count(db: Session) -> int:
    return db.query(PrepItem).count()
