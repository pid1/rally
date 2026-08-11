"""The single in-page measurement used by every visual assertion.

One script, evaluated once per page and viewport, returning geometry and
computed styles. Keeping it in one place means the tests all describe the same
rendering rather than each poking at the DOM their own way.
"""

MEASURE_JS = r"""
() => {
  const px = (v) => Math.round(v * 10) / 10;
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
      x: px(r.x), y: px(r.y + window.scrollY), w: px(r.width), h: px(r.height),
      right: px(r.right), bottom: px(r.bottom + window.scrollY),
    };
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const root = getComputedStyle(document.documentElement);
  const token = (name) => root.getPropertyValue(name).trim();
  const remPx = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const toPx = (value) => value.endsWith('rem')
    ? Math.round(parseFloat(value) * remPx * 100) / 100
    : parseFloat(value);

  const out = {};

  out.tokens = {
    space: ['1','2','3','4','5','6','7','8'].map(n => token(`--space-${n}`)),
    text: ['xs','sm','base','lg','xl','display'].map(n => token(`--text-${n}`)),
    textPx: ['xs','sm','base','lg','xl','display'].map(n => toPx(token(`--text-${n}`))),
    colours: ['ink','ink-strong','ink-muted','ink-subtle','rule','rule-subtle',
              'surface','surface-sunken','inverse'].map(n => token(`--${n}`)),
    targetMin: toPx(token('--target-min')),
  };

  out.frame = {
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    viewportHeight: window.innerHeight,
    horizontalOverflow:
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  };

  // ---- page-level blocks that must share a left edge ----
  // Only blocks that sit directly in the page column. Anything nested inside a
  // bordered container is inset on purpose.
  out.leftEdges = {};
  for (const sel of ['.header', 'nav', '.page', '.page-header-row', '.toolbar', 'footer']) {
    const el = document.querySelector(sel);
    if (el && visible(el)) out.leftEdges[sel] = px(el.getBoundingClientRect().x);
  }

  // ---- the first row of real content, for the mobile fold check ----
  const firstContent = document.querySelector(
    '.list-container, #groups-container, .dashboard-grid, .border-container');
  out.firstContent = box(firstContent);

  // ---- page header ----
  const headerRow = document.querySelector('.page-header-row');
  const meta = document.querySelector('.page-header-meta');
  const toolbar = document.querySelector('.toolbar');
  out.pageHeader = headerRow ? {
    row: box(headerRow),
    meta: box(meta),
    hasActions: !!document.querySelector('.page-header-actions'),
  } : null;
  out.toolbarBox = box(toolbar);
  // Distance from the whole header block to the next sibling block.
  const headerBlock = document.querySelector('.page-header');
  if (headerBlock && headerBlock.nextElementSibling) {
    out.headerToNext = px(
      box(headerBlock.nextElementSibling).y - box(headerBlock).bottom);
  }

  // ---- toolbar anatomy ----
  out.toolbar = null;
  if (toolbar) {
    const groups = [];
    for (const g of toolbar.querySelectorAll('.toolbar-group')) {
      const label = g.querySelector('.toolbar-label');
      const chips = Array.from(g.querySelectorAll('.filter-chip'));
      if (!label || !chips.length) continue;
      const lb = box(label), first = box(chips[0]);
      groups.push({
        label: label.textContent.trim(),
        chipCount: chips.length,
        // Inline iff the label's vertical centre falls inside the first chip.
        labelInline: lb.y + lb.h / 2 >= first.y && lb.y + lb.h / 2 <= first.bottom,
      });
    }
    const reset = toolbar.querySelector('.toolbar-reset .btn--quiet');
    out.toolbar = {
      box: box(toolbar),
      groups,
      reset: box(reset),
      resetText: reset ? reset.textContent.trim() : null,
      // The reset must be the toolbar's last child, not a member of a filter group.
      resetIsLastChild:
        !!reset && reset.closest('.toolbar-reset') === toolbar.lastElementChild,
      resetInsideGroup: !!reset && !!reset.closest('.toolbar-group'),
    };
  }

  // ---- interactive targets, measured at their effective hit area ----
  // A checkbox inside a <label> is tapped via the label, so the label is the
  // target; likewise a control wrapped in a purpose-built hit area.
  out.targets = [];
  const sel = 'button, a[href], select, input:not([type=hidden]), [role=button]';
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    // WCAG 2.5.8 exempts a target rendered inline inside a run of prose — a
    // help-text link cannot be 44px tall without wrecking the sentence.
    if (getComputedStyle(el).display === 'inline') continue;
    let hit = el;
    const wrapper = el.closest('label, .todo-checkbox');
    if (wrapper && visible(wrapper)) hit = wrapper;
    const r = hit.getBoundingClientRect();
    out.targets.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 60),
      label: (el.textContent || el.value || '').trim().slice(0, 30),
      w: px(r.width), h: px(r.height),
    });
  }

  // ---- focus rings: focus each kind of control and look for a visible ring ----
  out.focusable = [];
  {
    const seen = new Set();
    for (const el of document.querySelectorAll('button, a[href], input, select, textarea')) {
      // A disabled control cannot take focus, so it has no focus state to check.
      if (!visible(el) || el.disabled) continue;
      const key = (el.className || '').toString() || el.tagName;
      if (seen.has(key)) continue;
      seen.add(key);
      el.focus();
      const cs = getComputedStyle(el);
      const ring = !(cs.outlineStyle === 'none' || parseFloat(cs.outlineWidth) === 0)
        || cs.boxShadow !== 'none';
      out.focusable.push({cls: key.slice(0, 60), tag: el.tagName.toLowerCase(), ring});
      el.blur();
    }
  }

  // ---- computed style census, plus the text/contrast sample ----
  const srgb = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const lum = (rgb) => {
    const m = rgb.match(/\d+(\.\d+)?/g).map(Number);
    return 0.2126 * srgb(m[0]) + 0.7152 * srgb(m[1]) + 0.0722 * srgb(m[2]);
  };
  const opaqueBackground = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const bg = getComputedStyle(n).backgroundColor;
      const m = bg.match(/\d+(\.\d+)?/g);
      if (m && (m.length < 4 || Number(m[3]) > 0.5)) return bg;
    }
    return 'rgb(255, 255, 255)';
  };

  out.fontSizes = {};
  out.colours = {};
  out.contrast = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    out.fontSizes[cs.fontSize] = (out.fontSizes[cs.fontSize] || 0) + 1;
    out.colours[cs.color] = (out.colours[cs.color] || 0) + 1;

    // Only elements holding their own run of real text, so single-glyph
    // decorations (empty stars, dropdown carets) are not treated as copy.
    const own = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3)
      .map(n => n.textContent.trim())
      .join('');
    if (own.length < 3) continue;
    const l1 = lum(cs.color), l2 = lum(opaqueBackground(el));
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    out.contrast.push({
      cls: (el.className || '').toString().slice(0, 50),
      tag: el.tagName.toLowerCase(),
      colour: cs.color,
      fontSize: cs.fontSize,
      ratio: Math.round(ratio * 100) / 100,
      text: own.slice(0, 40),
    });
  }

  // ---- modal chassis ----
  out.modals = [];
  for (const overlay of document.querySelectorAll('.modal-overlay')) {
    const content = overlay.querySelector('.modal-content');
    out.modals.push({
      id: overlay.id,
      hasScroll: !!content.querySelector('.modal-scroll'),
      hasBody: !!content.querySelector('.modal-body'),
      hasTitle: !!content.querySelector('h3'),
    });
  }

  return out;
}
"""
