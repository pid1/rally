# Demo walkthrough

A script for recording a short tour of Rally — the one embedded at the top of the
[README](../README.md) — against a throwaway instance full of sample data.

Nothing here touches your own data. The demo runs from its own database on its own
port, and you can throw it away and rebuild it between takes.

## Standing it up

```bash
devenv shell    # or: direnv allow
demo
```

Without devenv, the same three steps by hand:

```bash
export RALLY_DB_PATH="$PWD/demo.db" PYTHONPATH="$PWD/src"
rm -f "$RALLY_DB_PATH"
uv run python -c 'from rally.database import init_db; init_db()'
uv run python -m rally.cli
uv run uvicorn rally.main:app --port 8100
```

`demo` deletes and rebuilds `demo.db`, seeds it, and serves Rally on
**http://localhost:8100**. Your `rally.db` and the dev server on port 8000 are
untouched. Ctrl+C stops it; running `demo` again gives you a clean slate, which is
what you want between takes — the tour creates and deletes real records.

The sample family is **Mom, Dad, Emma and Jake**. The data is anchored to the day you
run it, so the calendar always has this week's events in it and the preparedness list
always has something overdue.

### Before you hit record

- Browser at roughly **1280px wide**, no bookmarks bar, no extensions in the toolbar.
- Have a second tab on `http://localhost:8100/dashboard` so you can start clean.
- Don't open **Settings → LLM** on camera unless you want to blur it later. The seeded
  keys are fake, but the habit is worth keeping.

## The script

About five minutes at a normal pace. Each beat is *what you do* and then *what to
say* — paraphrase rather than read it out.

### 1. The briefing (0:00–0:50)

**Do:** open `http://localhost:8100/dashboard`.

**Say:** "This is what my family sees on the kitchen display every morning. Rally
reads our calendars, the forecast and our task lists at 4 AM, and writes one short
plan for the day. The briefing at the top is the part everybody actually reads —
early release today, pack the soccer kit before lunch."

**Do:** scroll slowly through Weather, Today's Schedule, and the task and meal
sections underneath.

**Say:** "Everything below it is where that came from — the weather, everyone's
events, what is due, what's for dinner. Nothing here is typed twice; it is the same
data as the rest of the app."

### 2. The shared calendar (0:50–2:00)

**Do:** click **Calendar**. It opens on the month.

**Say:** "Here is the family calendar. Each person has a colour, and this is
everybody's events in one place — Rally's own, and anything we pull in from Google or
iCloud."

**Do:** point at **Scouts** repeating each Tuesday, the **Camping trip** spanning
three days, and use the **Who** chips to filter to one person, then clear it.

**Say:** "Recurring events, multi-day events, and a filter for when you only care
about one kid's week."

**Do:** click **Add Event**. Title it `Swim lesson`, tick a couple of attendees, set a
reminder of 30 minutes, save.

**Say:** "When I add something, the people on the event get a push — not the whole
household. That distinction is the whole reason anyone keeps notifications turned on."

**Do:** click the new event, then **Edit**, change the time, save. Then delete it.

**Say:** "Editing and cancelling notify the same people, and for a repeating event it
asks whether I mean this one or the whole series."

**Do:** switch the **View** selector to **Day**, then **Agenda**.

**Say:** "Day, week, month or agenda — and the arrows move by whatever you're looking
at."

### 3. Tasks (2:00–2:40)

**Do:** click **Tasks**.

**Say:** "Tasks can belong to a person and have a due date. The ones with the little
loop are recurring — 'take the bins out' regenerates itself every week, so nobody has
to remember to re-add it."

**Do:** tick something off.

**Say:** "Completed tasks stay visible until midnight, so nobody has to wonder whether
it really got done."

### 4. Shopping (2:40–3:20)

**Do:** click **Shopping**. Type `mil` in the add box and let the autocomplete offer
**Milk**; accept it and press Enter. Add one or two more without leaving the field.

**Say:** "The shopping list is built for typing fast. It autocompletes from everything
we've bought before and remembers which shop each thing comes from — so it lands in
the right group without me choosing one."

**Do:** point at the store groups and the store filter chips.

**Say:** "One trip is one screen. And I can add to this by voice from my phone — Siri
posts straight to Rally."

### 5. Preparedness (3:20–4:20)

**Do:** open **Other → Preparedness**.

**Say:** "This is the emergency kit. What we have, where it lives, and when it needs
replacing."

**Do:** point at **Bottled water**, overdue and in red, then **First-aid kit** marked
due soon.

**Say:** "Anything past its date gets flagged here, pushed once a day, and mentioned
in the morning briefing until we deal with it — the push tells you once, the briefing
keeps asking."

**Do:** click **Refreshed** on the bottled water.

**Say:** "When I actually rotate it, one button re-anchors the next date from today —
because a case rotated three weeks late expires three weeks later."

**Do:** click **View go list**.

**Say:** "And this is the printable version, grouped by location in the order you'd
walk it — truck, garage, basement. It exports to Markdown, CSV or PDF for the
folder in the go-bag."

### 6. On a phone (4:20–4:50)

**Do:** open the browser's responsive mode at a phone width, or use your actual phone
on the same network. Load the dashboard, then the calendar.

**Say:** "Every page is built for a phone first and scales up to a wall display.
Because it's greyscale and typographic, it also looks right on e-ink."

### 7. Close (4:50–5:00)

**Say:** "It's self-hosted — one container, one SQLite file, on our own hardware. The
family's schedule never leaves the house except to reach whichever AI provider I've
pointed it at."

## After recording

1. Ctrl+C the `demo` process.
2. Paste the Loom share URL into the **Watch the tour** section of the README,
   replacing the placeholder line and the HTML comment above it.

`demo.db` is gitignored and can be deleted or left alone; the next `demo` run rebuilds
it from scratch.
