# Configuration

Almost everything is configured on the **Settings** page and stored in the database.
A `config.toml` file is supported as a fallback for API keys, calendar URLs and
coordinates; where both exist, the database wins.

Rally verifies LLM, weather, calendar and followed-team settings as you save them,
and shows the result in a modal — a wrong key fails in front of you rather than at
4 AM.

## The AI briefing

Pick a provider in Settings → **LLM**:

- **Anthropic** — an API key and a model. The token budget can be set explicitly or
  resolved from the model's real maximum at save time.
- **OpenAI-compatible** — any endpoint that speaks the OpenAI API, including a local
  server such as LM Studio or Ollama. GLM 4.7 Flash is a good local choice.

Two free-text fields shape what the model writes:

| Field | What it is for |
|---|---|
| **Family context** | Who is in the family, ages, routines, what matters. The model reads this every run |
| **Agent voice** | The tone to write in |

Both are versioned. Every save records a snapshot, and **Version History** rolls
either one back without touching the other. The same applies to the LLM provider,
model and token budget, which are versioned together as one coupled snapshot —
rolling back restores all of it at once.

**Home location** (e.g. "Highland Village, TX") is sent to the model as its own
block, so the briefing can reason about local conditions.

## Weather

Rally reads the National Weather Service DWML feed. Paste your forecast URL into
Settings → **Weather**; the section explains how to find it for your coordinates.
No API key is involved.

## Calendars

Add calendars under Settings → **Calendars**, each linked to a family member so its
events carry that person's colour.

Every family member also gets a **Rally-owned calendar** automatically. Events you
create in Rally live there. External calendars are read-only.

**ICS feed** — for public calendar URLs that need no authentication. Paste the URL.

**Google CalDAV** — uses an app-specific password:

1. Enable 2-Step Verification at [myaccount.google.com/security](https://myaccount.google.com/security)
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Choose "Other" and name it something like `Rally`
4. Copy the 16-character password
5. In Rally, add a calendar of type **Google CalDAV** with your Gmail address and that password

**Apple iCloud CalDAV** — also an app-specific password:

1. Enable Two-Factor Authentication at [appleid.apple.com](https://appleid.apple.com)
2. Go to [appleid.apple.com/account/manage](https://appleid.apple.com/account/manage)
3. Under "Sign-In and Security", choose **App-Specific Passwords**
4. Generate one labelled `Rally` and copy it
5. In Rally, add a calendar of type **Apple iCloud CalDAV** with your Apple ID and that password

External calendars are fetched in the background and served from a cache, so pages
render immediately. `calendar_sync_interval_minutes` (default 15) sets how stale that
cache may get; the calendar page says how old the data is and names any feed it could
not reach.

## Notifications

Rally uses [Pushover](https://pushover.net). Settings → **Notifications** takes the
application token that identifies your install; each family member's own user key
goes on their profile in Settings → **Family**.

A member without a key is simply never notified. That is the default, not an error.

Three things send a push:

- **A reminder lead time** on an event ("30 minutes before")
- **Notify attendees**, sent by hand from an event
- **An automatic notice** when an event is added, changed or removed

All three go to the event's **attendees**, never to the whole household — notifying
four phones about one child's appointment is how a family learns to ignore
notifications.

Preparedness has its own daily digest, with its own time and lead-time settings.

## Optional briefing sections

Each of these is a toggle in Settings:

| Toggle | Default | Effect |
|---|---|---|
| Shopping list in summary | Off | Folds your open shopping list into the morning briefing |
| Overdue stock in summary | On | Mentions preparedness items past their refresh date |
| STEM concept of the day | Off | Adds one age-appropriate STEM idea, never repeating a topic within 60 days |
| Sports watchlist | Off | Tonight's games and notable events for teams you follow |
| AI inventory review | Off | Adds a **Review** button to Preparedness that asks your model what the kit is missing |

## Context files

For file-based setup, copy the examples in the repository root:

- `config.toml.example` → `config.toml` — API keys, calendar URLs, coordinates
- `context.txt.example` → `context.txt` — family context
- `agent_voice.txt.example` → `agent_voice.txt` — voice and tone

The Settings UI is the recommended path; these exist for people who would rather keep
configuration in files.

## Dashboard refresh interval

The dashboard reloads itself every 30 minutes. To change that, edit the timeout at
the bottom of `templates/dashboard.html`:

```javascript
setTimeout(function() { location.reload(); }, 30 * 60 * 1000);
```
