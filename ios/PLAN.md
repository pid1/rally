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

- [ ] Xcode project + local Swift package `RallyKit` (models, `APIClient`, view
      models) so most logic is testable with `swift test`, no simulator
- [ ] Generate the client from `ios/openapi.json` (`swift-openapi-generator`).
      **First verify it keeps "omitted" distinct from `null`** for the API's
      `UNSET`-sentinel partial updates; fallback is a hand-written encoder
- [ ] Server-URL onboarding + settings, connection test, unreachable state
- [ ] Device token + person-on-device binding via `/api/devices`
- [ ] Design tokens (grayscale, serif, five member colors, 44pt targets) with a
      test that fails if they drift from `member_colors.py`
- [ ] Navigation: tab bar + "More" for the eight sections
- [ ] `swift test` green; app launches in the simulator against `demo`

## Phase 3 — Screens (one commit each)

- [ ] Shopping: stores, reorder, autocomplete, purchased archive, Siri/App Intent
- [ ] Tasks: recurring templates, completed history
- [ ] Dashboard
- [ ] Calendar: agenda, time grid, month grid, event add/edit/delete, recurrence
      and scope prompts, read-only external events
- [ ] Meal Planner: incl. previous meals, ratings, reviews
- [ ] Notes: incl. previous-notes archive
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
