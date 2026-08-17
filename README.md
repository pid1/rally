# Rally

**Your family's command center — one shared plan for the day, on every screen in the house.**

Rally is a self-hosted app for the logistics of a family: who is going where, what
has to get done, what needs buying, and what is in the emergency kit. Every morning
it reads your calendars, the weather forecast and everything on your lists, and
writes one short briefing for the whole family. The rest of the day it is the shared
calendar, the task list, the shopping list and the emergency-stock inventory that
briefing was built from.

It runs on your own hardware. Your family's schedule never leaves your house except
to reach the AI provider you choose.

![The Rally dashboard: a morning briefing, the weather, and today's schedule](docs/screenshots/readme-dashboard.png)

## Watch the tour

<!-- Loom: paste the share URL below, replacing this line and the italic line under it.
     The walkthrough it follows is docs/demo-walkthrough.md. -->

*A recorded walkthrough is on its way here.* Until then, the same tour is written
down in **[docs/demo-walkthrough.md](docs/demo-walkthrough.md)** — and you can run
the demo instance it describes yourself in about a minute.

## What Rally does

### One briefing, every morning

At 4 AM Rally gathers the day's events, the forecast, the tasks that are due and
tonight's dinner, and asks the AI model of your choosing to write the family a short,
plain-language plan: what is happening, what to wear, what to deal with first. The
page is served from a cache, so the kitchen display never sits waiting on an API.

You can also fold in the shopping list, anything overdue in your emergency stock,
tonight's games for the teams you follow, and a STEM idea for the kids — each one a
toggle in Settings.

### A calendar the whole family shares

Rally holds your family's own events and shows them beside the calendars you already
use — Google, iCloud, or any ICS feed. Day, Week, Month and Agenda views, colour-coded
by person, with recurring events and per-occurrence edits ("just this Tuesday" versus
"every Tuesday from now on").

![The Rally calendar in month view, colour-coded by family member](docs/screenshots/readme-calendar.png)

When somebody adds, moves or cancels an event, the people on that event get a push
notification — not the whole household. The same goes for reminders: set a lead time
on an event and only its attendees hear about it.

### Tasks and shopping that survive real life

Tasks can be assigned to a person, given a due date, and set to repeat daily, weekly
or monthly. Completed ones stay visible until midnight, so nobody wonders whether the
bins really did go out.

![The Rally task list with assignees, due dates and recurring tasks](docs/screenshots/readme-tasks.png)

The shopping list is built for typing fast: it autocompletes from everything the
family has bought before, remembers which shop each thing comes from, and groups the
list by shop so one trip is one screen. You can add to it by voice through Siri — see
**[docs/voice-shortcuts.md](docs/voice-shortcuts.md)**.

![The Rally shopping list grouped by store](docs/screenshots/readme-shopping.png)

### Emergency stock you can actually rely on

Track what is in the kit, where it lives, and when it needs replacing — a fixed expiry
date ("this case is stamped 2027-01-01") or a rotation ("swap the water every six
months"). Rally pushes one digest a day covering everything due, keeps mentioning
anything overdue in the morning briefing, and prints a go list grouped by location in
the order you actually walk it.

![The Rally preparedness inventory, grouped by location with refresh status](docs/screenshots/readme-preparedness.png)

### It comes with you

Every page is built for a phone first and scales up to a wall display. The design is
deliberately grayscale and typographic, which means it also looks right on e-ink.

<img src="docs/screenshots/readme-mobile.png" alt="Rally on a phone" width="320">

## Getting started

Rally ships as a Docker container:

```bash
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  --name rally \
  --restart unless-stopped \
  ghcr.io/pid1/rally:latest
```

Open `http://localhost:8000/settings` and fill in your timezone, your family members,
an AI provider key and a weather forecast URL. Everything else is optional and can be
added later.

Full instructions, including what you need before you start:
**[docs/installation.md](docs/installation.md)**.

## Documentation

| Guide | What's in it |
|---|---|
| [Installation](docs/installation.md) | Requirements, Docker deployment, upgrades, environment variables |
| [Configuration](docs/configuration.md) | Settings UI, AI providers, weather, calendars, Pushover notifications |
| [Voice shortcuts](docs/voice-shortcuts.md) | Adding shopping items with Siri and Apple Shortcuts |
| [Backups](docs/backup.md) | Scheduled, client-side-encrypted offsite backup |
| [Development](docs/development.md) | Local setup, commands, tests, database migrations |
| [Demo walkthrough](docs/demo-walkthrough.md) | A seeded demo instance and a script for recording it |
| [Design system](docs/visual-design-system.md) | Typography, spacing, components and how they are enforced |

## Contributing

Pull requests are welcome. Run `check` and the test suite before submitting — see
[docs/development.md](docs/development.md).

If you are an AI agent working in this repository, read
**[AGENTS.md](AGENTS.md)** first.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
