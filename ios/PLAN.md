# Rally for iOS — implementation plan

A fully native SwiftUI client for Rally. Worked on one long-lived branch
(`ios-client`) and landed as a **single PR** once v1 builds and passes in Xcode.

## Decisions

- **Server URL is user-set.** The app has no Tailscale-specific code: enter any base
  URL (`http://rally.<tailnet>.ts.net:8000`, a LAN IP, or public HTTPS). If the
  phone's Tailscale VPN is up, iOS routes the request like any other. Onboarding
  and Settings get a connection test and a "can't reach server" state that hints
  at the VPN.
- **Plain HTTP** is allowed for the personal build (`NSAllowsArbitraryLoads`);
  Tailscale encrypts the transport. Tighten before TestFlight, most likely by
  serving Rally over HTTPS with `tailscale serve`.
- **No auth**, same as the server today.
- **v1 scope: every page.**
- **Distribution:** personal build (Apple ID signing), device + simulator.
  TestFlight later.
- **Layout:** `ios/` in this repo, so an API change and its client change share a diff.

## Phase 1 — Backend groundwork

- [x] `GET /api/dashboard` (snapshot JSON + today's live note), with tests
- [x] `ios/openapi.json` checked in, `openapi` devenv script, and
      `tests/test_openapi.py` failing when it is stale
- [x] `docs/development.md` and `AGENTS.md` updated
- [x] `check` and `uv run pytest` green

## Phase 2 — App skeleton

- [x] Xcode project (generated from `ios/project.yml` with XcodeGen) + local Swift
      package `RallyKit` (`APIClient`, models, persistence, design tokens), testable
      with `swift test`, no simulator
- [x] **Hand-written API layer, not generated.** A generated client turns `nil`
      into an omitted key, so it can never send the explicit `null` that clears a
      field (un-assign a task, clear a due date). `Patch<T>` / `PatchBody` carry
      unset / null / value. `Route` lists every endpoint the app calls and
      `SpecCoverageTests` checks each against `ios/openapi.json`
- [x] Server-URL onboarding + Settings, connection test, unreachable banner
- [x] Device token + person-on-device binding via `/api/devices`
- [x] Design tokens with tests that fail if they drift from `member_colors.py` and
      `static/styles.css`
- [x] Navigation: tab bar (Dashboard, Tasks, Shopping, Calendar) + More (Notes,
      Meal Planner, Preparedness, Settings); screens are placeholders until phase 3
- [x] `swift test` green (24 tests); `RallyUITests` green (4 tests) against `demo`
- [ ] Polish noted for phase 3: large navigation titles and Picker menu rows use the
      system sans rather than the serif; member color dots missing in the owner menu

## Phase 3 — Screens (one commit each)

- [x] Shopping: stores, reorder, autocomplete, purchased archive, Siri/App Intent.
      Deviation: a drag reorders *within* a store (Edit → handles); moving to another
      store is the edit form's Store field, since a SwiftUI `List` cannot drop across
      sections. The Siri action is `AddToShoppingListIntent` ("Add to my shopping list
      in Rally"), which replaces the Shortcuts recipe in `docs/voice-shortcuts.md`
- [x] Tasks: recurring templates (daily/weekly/monthly/Custom… with the server-read preview),
      completed history, assignee chips, sort, swipe to delete
- [x] App icon (`Assets.xcassets/AppIcon`, from the supplied artwork, filled full-bleed)
- [x] Dashboard (native cards; the Daily Note is rendered by `NoteMarkdown`, which mirrors the
      server's tiny renderer — bold, italic, lists, line breaks — rather than a web view)
- [ ] Calendar: agenda, time grid, month grid, event add/edit/delete, recurrence
      and scope prompts, read-only external events
- [ ] Meal Planner: incl. previous meals, ratings, reviews
- [x] Notes: incl. previous-notes archive (adding to a day that has a note switches to it and
      appends what was typed, as on the web; past days are read-only)
- [ ] Preparedness + go list: Refreshed, AI review, export share sheet
- [ ] Settings: family, calendars, LLM/AI settings with history, notifications,
      sports teams, Personal Defaults

## Testing

- Unit tests over recorded JSON fixtures (`swift test`)
- Contract tests of the generated client against a `demo` instance (port 8100) —
  never `rally.db`
- XCUITest smoke test per screen
- Backend: `check` and `uv run pytest` at each commit

## Risks

- **Calendar** is the largest piece (time grid, overlap packing, recurrence
  scopes, moved occurrences). Use the server's `time_label` / `start_form`
  fields rather than redoing timezone maths on the phone.
- **Settings** is wide; use a plain `Form`, skip the web version's polish.
- **`UNSET` partial updates** — see Phase 2. The exported spec drops the sentinel
  default (pydantic warns it is not JSON-serializable), so optional means
  "omit to leave alone"; the client must not encode absent fields as `null`.

## Out of scope for v1

Widgets, offline cache, APNs push (Rally sends Pushover today).
