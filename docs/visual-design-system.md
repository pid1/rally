# Visual design system: audit and implementation

**Status:** implemented. The audit below is what was found; the sections after
it are the system that was built in response, and the tests that hold it in
place.

Rally's look is deliberate — an editorial, monochrome, serif command center that
reads well on a wall tablet and prints cleanly. That identity is not in
question here and this work did not change it.

What is in question is *consistency*. Every page was built by hand against a
single 1,723-line stylesheet with no shared vocabulary, so the same idea is
expressed a slightly different way on each page. Individually the differences
are small. Together they are why the app feels assembled rather than designed,
and why every new page costs more than the last one.

This document is the audit, the design system built to fix it, and the
enforcement that keeps it from drifting back.

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

Grouped by cause, not by page. This is the state before the rebuild; each
finding names the evidence, and each is closed by the system described after
it. The measurements are quoted throughout so the fixes can be checked against
something specific.

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

**E5. Every modal clipped the left side of its own focus ring.** Found after
the audit, from a screenshot of the Add Item field on Preparedness. `.modal-body`
sets `overflow-y: auto`, which makes `overflow-x` compute to `auto` as well, so
the box clips its children's ink on both sides. Its fields are `width: 100%`
and the ring lands 4px outside their border box (2px offset + 2px outline);
there were 4px of room on the right and 0 on the left. Measured on
`#item-name`: `gapLeft: 0, gapRight: 4` — the ring drew as three sides of a
rectangle. All eight pages that own a modal were affected. E1 could not see it:
it measures visible controls, and a closed modal has none.

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

## The system, as built

Five layers, smallest first. `static/styles.css` is now organised in exactly
this order, so a rule's position tells you its blast radius.

### 1. Tokens

Raw greys stay as private primitives; everything else references a role. Every
token override — the responsive page padding, the touch target — lives in this
layer too, so the scales have one home.

```css
/* space — one 4px-based scale, replacing 18 ad hoc values */
--space-1: 0.25rem;  --space-2: 0.5rem;   --space-3: 0.75rem;  --space-4: 1rem;
--space-5: 1.5rem;   --space-6: 2rem;     --space-7: 3rem;     --space-8: 4rem;

/* type — six steps, replacing 17 declared sizes */
--text-xs: 0.75rem;  --text-sm: 0.875rem; --text-base: 1rem;
--text-lg: 1.25rem;  --text-xl: 1.5rem;   --text-display: 3rem;

/* colour by role, not appearance */
--ink: #1a1a1a;          /* primary text, strong rules   */
--ink-muted: #666666;    /* secondary text — 5.74:1      */
--ink-subtle: #767676;   /* tertiary text — 4.54:1       */
--ink-strong: #000000;   /* emphasis inside body copy    */
--rule: #cccccc;         /* hairlines — never text       */
--rule-subtle: #e5e5e5;
--surface: #ffffff;
--surface-sunken: #f5f5f5;
--inverse: #ffffff;      /* text on --ink                */

--target-min: 2.25rem;   /* 2.75rem under (pointer: coarse) and below 768px */
```

The one substantive colour change: `--ink-subtle` (#767676) replaced
`--light-gray` (#999999) everywhere it carried text, clearing WCAG AA at
4.54:1. #999 no longer exists.

Three steps carry the headings — 1rem, 1.25rem, 1.5rem — so the four unrelated
uppercase sizes collapsed to three related ones (C6): `.shopping-group-name`
at base, `.card-header` and every modal `h3` at lg, page `h2` at xl.

### 2. Layout primitives

- **`.page`** — the content column. Width is owned by `body` alone; the second
  `max-width` that put every desktop page 2px out of line with its own header
  is gone (A1).
- **`.page-header`** — title, actions, and any secondary links as *one* block
  with defined internal spacing. The archive switch and retention note are now
  inside it instead of being loose siblings with their own margins (A2, A3).
  Dashboard and Settings use the same primitives; Settings gained a page-level
  `h2` and its section headings became `h3` (A5).
- **`.stack`** — vertical rhythm via `> * + *`. The space between blocks comes
  from one declaration, tightened one step on mobile (A6).

### 3. Components

The button family collapsed from nine ad hoc classes (`.btn-add`,
`.btn-primary`, `.btn-save`, `.btn-cancel`, `.btn-edit`, `.btn-delete`,
`.btn-ghost`, `.btn-load-more`, `.filter-clear`) to `.btn` plus three
modifiers: `--secondary`, `--quiet`, `--sm`. Save is now the same control
everywhere it appears, including on Settings, where it used to be two different
sizes on one page (D1).

Everything focusable shows a ring: one `:focus-visible` rule in the base layer,
and no component may switch it off (E1). Hit areas are ≥44px wherever a finger
is likely — keyed off `(pointer: coarse)` as well as width, because the wall
tablet is a coarse pointer at desktop size (E2).

### 4. The toolbar

The one deliberate shape change.

- Label placement is explicit — **stacked below 768px, inline at and above it**
  — rather than emergent from flex wrapping. Chip count no longer affects
  layout (B1).
- The toolbar is **one grid**, not one grid per group: `display: contents`
  promotes each group's label and control into it, so every label shares a
  column and every control starts at the same x.
- **"Clear Filters" has a fixed slot**, rendered by the toolbar as its last
  block. It sits at an identical position on all six pages at every width (B2).
- **"Manage stores" moved out of the filter bar** to the page header, where its
  consequence matches its position (B3).

### 5. Documentation

- This file — the tokens, the primitives, the rules.
- **`/styleguide`** — every component and state, rendered from the real
  stylesheet, reading its scales back out of the cascade so it cannot describe
  a scale the CSS no longer has.

## Enforcement

Two suites, cheapest first.

**`tests/test_stylesheet.py`** — static, no browser, runs in the ordinary test
job. Asserts that no selector is declared twice in the same context, that no
rule suppresses the focus ring, and that colours, type sizes and spacing are
taken from tokens rather than typed. It earned its place immediately: it caught
a stale duplicate `.toolbar-search` rule that was silently overriding the new
grid, and a second `:root` block in the same media query.

**`tests/visual/`** — the audit's measuring code turned into assertions, run in
its own CI job against a real browser and a seeded database. Every test names
the finding it protects against:

| Test | Guards |
|---|---|
| page blocks share a left edge | A1 |
| header is the same distance from the next block everywhere | A2, A3 |
| content starts above the fold on a phone | A4 |
| filter labels are placed by rule, not chip count | B1 |
| Clear Filters occupies one fixed slot on every page | B2 |
| type sizes come from the scale | C1 |
| colours come from the palette | C4 |
| text meets WCAG AA contrast | C5 |
| every modal uses the shared chassis | D2 |
| every control shows a focus ring | E1 |
| touch targets meet 44px on a phone | E2 |
| focus rings are not clipped inside modals | E5 |
| no horizontal overflow | — |

They are geometric rather than pixel-based on purpose: when one fails it says
which rule broke and by how much, which a screenshot diff cannot. The suite
skips itself when Playwright is absent, so `uv run pytest` stays fast and
browser-free for anyone not working on the design system.

To run it locally:

```bash
uv sync --group visual
uv run playwright install chromium
uv run pytest tests/visual -v
```

## What changed, measured

| | Before | After |
|---|---|---|
| Distinct rendered font sizes | 11 | 6, all tokens |
| Declared `font-size` values | 17 | 6 tokens |
| Declared spacing values | 18 | 8 tokens |
| Positions of "Clear Filters" across pages | 5 | 1 |
| Label/chip placement rules | emergent, chip-count dependent | 1 per breakpoint |
| Pages with content below the mobile fold | 4 of 8 | 0 of 8 |
| Text below WCAG AA | 10 rules | 0 |
| Controls with no focus ring | 4 | 0 |
| Modals missing the scroll chassis | 6 of 11 | 0 |
| Duplicate rule blocks | 4 | 0, enforced |

## Non-goals

- **No redesign.** The serif, monochrome, editorial identity is the point of
  Rally and did not change. Every change here applies it consistently.
- **No CSS framework, no build step, no JS framework.** Plain CSS with custom
  properties, matching how the app is written.
- **No dark mode.** Role-based tokens make it possible later; shipping it was
  not part of this.

## Decisions taken

- **The density pass tightened spacing and the nav; it did not touch the
  wordmark's identity.** On mobile the four stacked full-width nav buttons
  became a 2×2 grid and the wordmark stepped down one size on the type scale.
  That was enough to bring every page's first content row above the fold, so
  nothing more aggressive was needed.
- **`/styleguide` ships in production.** It is unlinked from the nav, and a
  styleguide that only exists in development stops matching what production
  looks like.
- **No screenshot baselines in the repo.** The geometry assertions and the
  stylesheet lint cover the failures worth catching, and they explain
  themselves when they break. Baselines would add churn and a binary diff for
  every intentional change.
- **The verification dialog keeps a variant.** `.modal-content--narrow` with no
  scroll wrapper: it has no title and no form, just a centred status block. The
  test exempts titleless modals rather than pretending the variant does not
  exist.
- **`/dashboard` still has no `h2`.** The wordmark and today's date above it
  are already the page title. It uses the same `.page`/`.stack` primitives as
  everywhere else; only the redundant heading is omitted.
