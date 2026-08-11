# Visual design system: audit and remediation plan

**Status:** proposed. Nothing in this document is implemented yet.

Rally's look is deliberate — an editorial, monochrome, serif command center that
reads well on a wall tablet and prints cleanly. That identity is not in
question here and this plan does not change it.

What is in question is *consistency*. Every page was built by hand against a
single 1,723-line stylesheet with no shared vocabulary, so the same idea is
expressed a slightly different way on each page. Individually the differences
are small. Together they are why the app feels assembled rather than designed,
and why every new page costs more than the last one.

This document is the audit, the design system proposed to fix it, and the
staged plan to get there.

## How this was measured

Every page was loaded in headless Chromium at three widths — 1440×900,
834×1112, 390×844 — and measured in the DOM rather than eyeballed. Screenshots
were captured alongside, but the findings below are geometry, not impressions:
element boxes, computed styles, focus rings after actually focusing, and a
census of every distinct value in use.

Pages: `/dashboard`, `/todo`, `/todo/completed`, `/shopping`,
`/shopping/purchased`, `/dinner-planner`, `/meal-history`, `/settings`.
Modals: all 11, opened and measured at desktop and mobile.

The measuring code is the reason this is worth doing as a *system*: it is
directly reusable as the enforcement mechanism (see "Enforcement" below), so
the audit becomes a regression test rather than a one-time cleanup.

## Findings

Grouped by cause, not by page. Each finding names the evidence.

### A. Page frame and vertical rhythm

**A1. Every content page has a 2px horizontal jog on desktop.**
At ≥1024px the body content box is 804px wide (`max-width: 900px` minus
`padding: 64px 48px`), but `.section-container` caps itself at `max-width:
800px` and centres, insetting 2px on each side. Measured left edges on all six
content pages: `.header`, `nav`, `footer` at x=318; `.section-container`,
`.header-container`, `.filter-toolbar`, `.list-container` at x=320. The rule
under the wordmark is 4px wider than the rule under the page title, on every
page. Two nested max-widths are one too many.

**A2. The gap between the page title and the toolbar is three different sizes.**
Measured from the bottom of the `.header-container` rule to the top of
`.filter-toolbar`: 32px on Meal Planner and Meal History; 61.2px on Tasks,
Completed Tasks and Shopping; 92.3px on Purchased Items. The difference is
whether the page happens to have a `.view-switch` link and a `.page-note`,
which sit as loose siblings contributing their own margins instead of belonging
to a defined header block.

**A3. The `.view-switch` link is visually attached to the wrong thing.**
It sits 36px below the title rule but 0.7px above the toolbar, so "View
completed tasks" reads as part of the filter bar rather than as a page-level
action.

**A4. On mobile, page chrome consumes the entire first screen.**
Y-coordinate of the first content row against an 844px viewport:

| Page | First content row | Share of first screen |
|---|---|---|
| Completed Tasks | 981px | 116% |
| Tasks | 864px | 102% |
| Meal History | 861px | 102% |
| Purchased Items | 830px | 98% |
| Shopping | 760px | 90% |
| Meal Planner | 730px | 86% |
| Dashboard | 687px | 81% |
| Settings | 623px | 74% |

On four of eight pages you must scroll before seeing a single task, item or
meal. This is the "shoves the usable content downwards" complaint, quantified.
The cost is fixed and repeated: 3rem wordmark, subtitle, rule, four stacked
full-width nav buttons, rule, title, rule, toolbar — before any content.

**A5. Two pages opt out of the page-title pattern.** Six pages open with an
`.header-container` (uppercase `h2` + rule). Dashboard and Settings do not.
Settings further wraps each section in `.border-container` (32px padding +
1px border), so its content sits 33px inset from where content sits on every
other page.

**A6. Three different "space between major blocks" values.** 64px between
stacked `.section-container`s, 48px between `.border-container`s, 32px between
everything else — none of them derived from a shared scale.

### B. The filter toolbar

This is the single largest source of visible inconsistency, and it is where the
original report started.

**B1. Whether a filter label sits beside its chips or above them is decided by
chip count, not by design.** `.toolbar-group` is a wrapping flex row; the
`.filter-chips` block is a single flex item, so it drops to its own line the
moment its content no longer fits. At 390px:

| Page | Group | Chips | Label beside chips? |
|---|---|---|---|
| Tasks | Assignee | 5 | no |
| Meal History | Meal Type | 4 | no |
| Meal Planner | Meal Type | 4 | no |
| Meal History | Rating | 3 | **yes** |
| Shopping | Store | 3 | **yes** |

Meal History exhibits both behaviours *in the same toolbar*: "Meal Type" above
its chips, "Rating" beside its chips, "Sort" beside its select. Adding a family
member or a store silently changes the layout of the page.

**B2. "Clear Filters" lands wherever the preceding chips end.** It is a child of
whichever `.toolbar-group` each template happened to put it in, so it has no
fixed slot. Measured x-position and row at 1440px:

| Page | Attached to | x | Row |
|---|---|---|---|
| Tasks | Assignee | 771 | 1 |
| Meal Planner | Meal Type | 732 | 1 |
| Shopping | Store | 654 | 1 |
| Meal History | Rating | 628 | **2** |
| Purchased Items | Store | 384 | 1 |

Between Meal Planner and Meal History — two views of the same feature, reached
from the same nav dropdown — the control changes both column and row. On Meal
History it is stranded mid-row between the Rating chips and the Sort label, so
it reads as if it belongs to Sort.

**B3. `.filter-clear` styles two unrelated kinds of action.** It is both the
reset control ("Clear Filters", "Clear Search") and a navigation control
("Manage stores", which opens a modal). Identical appearance, entirely
different consequence, sitting side by side in the Shopping toolbar.

**B4. Three treatments for "quiet, link-like control".** `.view-switch`
(0.9rem), `.filter-clear` (0.85rem), `.btn-ghost` (0.9rem) — all underlined
grey text, all different padding, no rule for which to use.

**B5. The toolbar is the heaviest block on mobile.** Measured heights at 390px:
110px (Shopping) to 370px (Completed Tasks). The Completed Tasks toolbar alone
is 44% of the viewport.

### C. Tokens: there is no shared vocabulary

**C1. 17 distinct `font-size` declarations**, nine of them inside the
0.7–1.1rem band (0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1, 1.05, 1.1) — differences
too small to read as intentional but each individually specified. Rendered
desktop pages use 11 distinct computed sizes. `:root` declares colours and two
font families; there are no type or spacing tokens at all.

**C2. 18 distinct spacing values**, including the one-offs 5px, 14px, 26px and
28px. There is no 4px or 8px grid to snap to.

**C3. 7 distinct `letter-spacing` values** across an identity that leans
heavily on letter-spacing.

**C4. The palette is named by appearance, not role.** `--charcoal`,
`--dark-gray`, `--medium-gray`, `--light-gray`, `--pale-gray`, `--silver`,
`--off-white`. Nothing in the name says "this is a border", "this is secondary
text", or "this is a sunken surface", so there is no way to reason about a
change, and no path to a dark or e-ink variant.

**C5. `--light-gray` (#999999) fails WCAG AA as a text colour and is used as
one in 10 rules.** Contrast on white is **2.85:1**, below the 4.5:1 minimum for
body text and below even the 3:1 large-text minimum. It carries real
information: `.shopping-completed-at`, `.no-review-text`, `.recurring-paused`,
`.recurring-meta`, `.todo-meta`, `.todo-completed-date`, `.shopping-group-count`.
For reference: `--medium-gray` #666 is 5.74:1 and passes.

**C6. Four sizes for one heading idea.** Uppercase, letter-spaced, serif
headings exist at 1rem (`.shopping-group-name`), 1.1rem (`.card-header`),
1.3rem (`.modal-content h3`) and 1.5rem (`h2`) — four steps with no scale
relating them.

### D. Components

**D1. Two primary buttons, both labelled "Save", both on the Settings page.**
`.btn-save` (padding 10px 24px, 1rem) is used by Settings' nine inline forms;
`.btn-primary` (padding 8px 16px, 0.9rem) is used by all 11 modals — including
the four modals that open *from* Settings. Measured: **80×43 at 16px** versus
**61×37 at 14.4px**. Same word, two controls, one page. Note which one is
correct: `.btn-save` at 43px nearly meets the 44px touch target that
`.btn-primary` misses by seven.

**D2. The modal chassis is split in two.** Five modals (task, recurring, item,
stores) use `.modal-scroll` + `.modal-body` and get the scroll-fade affordance
and a body that flexes. Six (meal, review, member, calendar, team) do not, and
fall back to `.modal-content { overflow: auto }`. At 390px the Add Team modal
fills 89.8% of the viewport and the Add Meal modal 88%, both with no fade to
signal that content continues.

**D3. Two modal id conventions.** `#modal-overlay` (todo, meal edit) versus
namespaced `#item-modal-overlay`, `#review-modal-overlay`, and so on.

**D4. Four paddings for "a box of content".** 16px (`.editable-item`,
`.history-card`), 20px (`.plan-card`), 24px (`.card`), 32px
(`.border-container`).

**D5. Two checkbox appearances.** `.todo-checkbox input` is sized 18×18 with a
`border` declaration that native checkboxes ignore; `.checkbox-label input` is
left at the browser default 13×13 with `accent-color`. Both appear on Settings.

**D6. Body copy is justified on desktop and ragged on mobile.**
`.card-content p` sets `text-align: justify`, overridden to `left` under 768px.
Justified serif text in an 800px measure with no hyphenation produces uneven
word spacing on the Dashboard's summary cards.

### E. Accessibility

**E1. The toolbar has no visible keyboard focus at all.** `.filter-chip`,
`.filter-clear`, `.search-input` and `.search-btn` each set `outline: none` on
`:focus` with no replacement. Verified by focusing each control and reading the
computed style: no outline, no box-shadow, no border change. Every other
control in the app relies on the browser default, so focus styling is both
absent where it is suppressed and undesigned where it is not.

**E2. Interactive targets below 44px are the norm, not the exception.**
Measured at 390px: `.btn-edit` 49×32 (36 instances across the app),
`.filter-chip` ~29px tall, `.filter-clear` 26px, `.view-switch` 24.5px,
and the todo/shopping checkbox 18×18 — which is the primary "check it off"
gesture on a touch device. Form fields inside modals already set
`min-height: 2.75rem` (44px), so the convention exists; it just is not applied
outside forms.

**E3. One default-blue link.** The `forecast.weather.gov` help link on Settings
renders at `rgb(0, 0, 238)` — the only non-grayscale pixel in the app.

**E4. A third font family appears unstyled.** `<code>` is used in seven places
on Settings and renders in the browser's monospace default.

### F. Stylesheet health

**F1. Verbatim duplicate rules.** `footer`, `footer a` and `footer a:hover` are
declared twice, identically (lines 79–98 and 132–151, both under a
`/** Footer Styles **/` banner). `.modal-scroll::after` and
`.nav-dropdown-btn.active` are also declared twice.

**F2. Dead rules.** The entire `.plan-*` family (`.plan-card`, `.plan-date`,
`.plan-content`, `.plan-actions`, `.plan-meta`, `.plan-meta-row`) is unused —
Meal Planner now renders `.editable-item`. Also unused: `.assignee-badge`,
`.todo-meta`, `.member-badge`, `.settings-actions`, `.settings-status`,
`.history-card-meta`, `.form-actions`.

**F3. Organised by page, not by layer.** One flat 1,723-line file where a
global rule and a shopping-only rule sit at the same level, so there is no
signal about blast radius when editing.

## The proposed system

Five layers, smallest first. Each is independently useful.

### 1. Tokens — `static/tokens.css`

Raw greys stay as private primitives; everything else references roles.

```
/* space — one 4px-based scale, replacing 18 ad hoc values */
--space-1: 4px;   --space-2: 8px;   --space-3: 12px;  --space-4: 16px;
--space-5: 24px;  --space-6: 32px;  --space-7: 48px;  --space-8: 64px;

/* type — six steps, replacing 17 sizes */
--text-xs: 0.75rem;  --text-sm: 0.875rem;  --text-base: 1rem;
--text-lg: 1.125rem; --text-xl: 1.5rem;    --text-display: 3rem;

/* colour by role, not appearance */
--ink: #1a1a1a;          /* primary text, strong rules   */
--ink-muted: #666666;    /* secondary text — 5.74:1      */
--ink-subtle: #767676;   /* tertiary text — 4.54:1       */
--rule: #cccccc;         /* hairlines — never text       */
--rule-subtle: #e5e5e5;
--surface: #ffffff;
--surface-sunken: #f5f5f5;
--inverse: #ffffff;      /* text on --ink                */
```

The one substantive colour change: `--ink-subtle` (#767676) replaces
`--light-gray` (#999999) wherever it carries text, clearing WCAG AA at 4.54:1.
#999 survives only as a non-text hairline/disabled affordance.

Rule to enforce: **no token used for text may fall below 4.5:1 on `--surface`.**

### 2. Layout primitives

- `.page` — the single content column. One max-width in the whole app, ending
  the A1 jog by construction.
- `.page-header` — title, actions, optional view-switch and note as *one*
  block with defined internal spacing. Collapses A2, A3 and A5 into one rule
  and makes Dashboard and Settings conform.
- `.stack` — vertical rhythm via `> * + *`, so block spacing comes from one
  place instead of per-component margins (A6).
- `.toolbar` — see below.

### 3. Component contracts

Every component documents its states — default, hover, `:focus-visible`,
active, disabled — and its minimum target size. The button family collapses
from nine ad hoc classes (`.btn-add`, `.btn-primary`, `.btn-save`,
`.btn-cancel`, `.btn-edit`, `.btn-delete`, `.btn-ghost`, `.btn-load-more`,
`.filter-clear`) to four roles: **primary**, **secondary**, **quiet**,
**destructive**, each in one size plus a `--sm` modifier. `.view-switch`
becomes quiet-with-icon rather than its own thing.

Also specified: chip, field, card, modal, list row, section header.

### 4. Toolbar — the one deliberate shape change

- Label placement becomes explicit rather than emergent: **stacked above the
  chips below 768px, inline at and above it**, using a grid rather than flex
  wrapping. Chip count stops affecting layout (B1).
- **"Clear Filters" gets a fixed slot** — the end of the toolbar, right-aligned
  on desktop, full-width row on mobile — identical on every page (B2). It is
  rendered by the toolbar itself, not placed by each template.
- **"Manage stores" moves out of the filter bar** to a secondary action beside
  the page title, where its consequence matches its position (B3).

### 5. Documentation

- `docs/design-system.md` — the tokens, the primitives, the component
  contracts, and the rules ("text is never `--rule`", "targets are ≥44px",
  "spacing comes from the scale").
- A `/styleguide` route rendering every component and every state from the real
  stylesheet, so the documentation cannot drift from the code.

## Enforcement

Playwright, in `tests/visual/`, run in CI as its own job (it needs
`playwright install chromium`). Three layers, cheapest first:

1. **Geometry assertions** — the audit script generalised into tests. These
   catch the class of bug this document is about, and unlike screenshots they
   say *why* they failed:
   - every top-level block shares a left edge, at every width;
   - the label/chip relationship is identical across all pages at a given width;
   - "Clear Filters" occupies the same slot on every page that has one;
   - no interactive target under 44×44 at mobile width;
   - no horizontal overflow at any width;
   - the first content row is above the fold at 390×844.
2. **Token linting** — walk the rendered DOM and fail on any computed
   font-size, colour or spacing outside the token set. This is what stops the
   17-font-size problem from growing back.
3. **Screenshot baselines** — per page × {mobile, desktop}, small pixel
   tolerance, refreshed deliberately in the PR that changes them. These are the
   backstop for what geometry cannot express.

## Delivery

Staged so each step is independently reviewable and shippable, and so the
safety net exists before anything moves.

| Step | Scope | Fixes |
|---|---|---|
| 0 | This document. No code. | — |
| 1 | Playwright harness, CI job, geometry assertions and baselines of the **current** state | — |
| 2 | Tokens + `/styleguide`; mechanical substitution. **No intended visual change** — baselines prove it | C1–C4, C6 |
| 3 | Layout primitives: `.page`, `.page-header`, `.stack` | A1, A2, A3, A5, A6 |
| 4 | Toolbar rebuild | B1–B5 |
| 5 | Button/control consolidation, `:focus-visible`, 44px targets, contrast fix | C5, D1, E1–E4 |
| 6 | Modal chassis unification | D2, D3 |
| 7 | Density pass to reclaim the mobile fold | A4 |
| 8 | Stylesheet split into layers, dead-rule removal | D4, F1–F3 |

Step 1 lands before step 2 on purpose: the mechanical token substitution is
exactly the kind of change that is safe *if* something is watching, and
unreviewable if nothing is.

## Non-goals

- **No redesign.** The serif, monochrome, editorial identity is the point of
  Rally and does not change. Every finding here is about applying it
  consistently.
- **No CSS framework, no build step, no JS framework.** Plain CSS with custom
  properties, matching how the app is written today.
- **No dark mode now.** Role-based tokens make it possible later; shipping it
  is not part of this.
- **No behaviour changes** beyond the two deliberate ones called out in the
  toolbar section.

## Open questions

1. **Density target for A4.** How aggressive should the mobile fold reclamation
   be — tighten spacing only, or also shrink the wordmark and collapse the nav
   into a compact row on small screens? The second reclaims far more but
   touches the most recognisable part of the identity.
2. **Should `/styleguide` ship in production**, or be gated to development?
3. **Screenshot baselines in-repo?** They add roughly 2–4MB and some churn, but
   they are the only check that catches purely visual regressions. Geometry
   assertions and token linting alone would keep the repo lean.
