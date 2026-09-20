/* ============================================================
   FIRST-VISIT TOUR for the builder (index_1.html).

   Why this exists: measured on 2026-09-19, of the visitors who opened the
   builder only ~14% ever tried to export, and 6 of 272 devices came back
   on another day. A first-time visitor lands on an empty "IF (Strategy
   Rule)" block with nothing saying what to do next.

   What it is: a product tour made of callouts that point at the real
   elements, one step at a time:
     - a welcome card (start the tour, load a ready example, or skip). It
       opens by itself on the first visit.
     - a callout on the rule block, then one callout per thing to build.
       For each "build" step it first points at the toolbox category to
       click, then, once that category's list is open, glows the block to
       take from the list and shows the outline of that block in the slot
       it will land in, with the callout under the slot.
     - a callout on the export buttons at the end.
   Build steps complete ONLY when the visitor really connects the block
   (read from the live workspace), so the tour cannot be clicked through.
   "Try an example" loads a finished rule and runs a shorter tour on it.

   Highlights are never boxes drawn over the page (guessed sizes never fit
   Blockly's block shapes). Each is applied to the real thing: a glow that
   follows a block's own outline, an outline inside a toolbox row, a ring
   that follows a button's corners, Blockly's own connection highlight, and
   the slot marker built from the parent block's own geometry (an outlined
   hole, or Blockly's native connection highlight plus a ring).

   Everything is plain DOM + injected CSS, deliberately NOT Tailwind
   utilities: the project ships a precompiled Tailwind build and a class
   missing from it silently styles nothing.

   Depends on window.apBuilder, a small bridge exposed at the end of
   index_1.html's main script. Best-effort throughout: a failure here must
   leave the builder itself untouched.
   ============================================================ */
(function () {
  'use strict';

  const B = window.apBuilder;
  if (!B || !B.workspace) return;
  const ws = B.workspace;
  const STORAGE_KEY = 'algoPuzzle.onboarding.v1';

  /* ---------- persistence ----------
     localStorage can be blocked or throw (private windows, blocked site
     data); the in-memory copy keeps the tour from reopening on every action
     within the same page visit. */
  let memState = {};
  function readState() {
    let stored = {};
    try { stored = JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch (_) {}
    return Object.assign({}, stored, memState);
  }
  function writeState(patch) {
    memState = Object.assign(memState, patch);
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.assign(readState(), patch))); } catch (_) {}
  }
  function track(type, meta) {
    try { B.trackEvent(type, meta); } catch (_) {}
  }

  /* ---------- styles ---------- */
  const css = `
    .ob-glow { animation: ob-glow 1.6s ease-in-out infinite; }
    @keyframes ob-glow {
      0%, 100% { filter: drop-shadow(0 0 2px #34d399) drop-shadow(0 0 6px rgba(52,211,153,0.95)); }
      50% { filter: drop-shadow(0 0 1px rgba(52,211,153,0.55)) drop-shadow(0 0 2px rgba(52,211,153,0.45)); }
    }
    .ob-ring-in { outline: 2px solid #34d399; outline-offset: -2px; animation: ob-ring-in 1.6s ease-in-out infinite; }
    @keyframes ob-ring-in { 0%, 100% { outline-color: #34d399; } 50% { outline-color: rgba(52,211,153,0.6); } }
    .ob-ring-out { animation: ob-ring-out 1.6s ease-in-out infinite; }
    @keyframes ob-ring-out {
      0%, 100% { box-shadow: 0 0 0 3px #34d399, 0 0 16px 4px rgba(52,211,153,0.6); }
      50% { box-shadow: 0 0 0 3px rgba(52,211,153,0.75), 0 0 6px 1px rgba(52,211,153,0.3); }
    }
    .ob-slot { pointer-events: none; }
    .ob-slot .ob-hole {
      fill: rgba(52,211,153,0.28); stroke: #34d399; stroke-width: 2.5; stroke-linejoin: round;
      animation: ob-hole 1.6s ease-in-out infinite;
    }
    @keyframes ob-hole {
      0%, 100% { filter: drop-shadow(0 0 3px #34d399); }
      50% { filter: drop-shadow(0 0 1px rgba(52,211,153,0.4)); }
    }
    /* Blockly's own connection highlight (the tab-shaped notch of an outer slot),
       restyled only while the tour is running so nothing else in the app changes. */
    body.ob-active .blocklyHighlightedConnectionPathVisible {
      stroke: #34d399 !important; stroke-width: 5px !important; stroke-linecap: round;
      animation: ob-hi 1.4s ease-in-out infinite;
    }
    @keyframes ob-hi {
      0%, 100% { filter: drop-shadow(0 0 3px #34d399) drop-shadow(0 0 7px rgba(52,211,153,0.85)); }
      50% { filter: drop-shadow(0 0 1px rgba(52,211,153,0.4)); }
    }

    .ob-backdrop { position: fixed; inset: 0; z-index: 73; background: rgba(3,7,18,0.72); display: none; }

    /* A dotted line from the callout's arrow to its target, drawn only when the
       callout has to stand back from what it points at. */
    .ob-leader { position: fixed; left: 0; top: 0; width: 100%; height: 100%; z-index: 74; pointer-events: none; display: none; }
    .ob-leader line { stroke: #34d399; stroke-width: 2; stroke-dasharray: 1 6; stroke-linecap: round; }
    .ob-leader circle { fill: #34d399; }

    .ob-pop {
      position: fixed; z-index: 75; display: none; box-sizing: border-box;
      width: 332px; max-width: calc(100vw - 20px);
      background: #111827; color: #cbd5e1; border: 1px solid #374151; border-radius: 12px;
      box-shadow: 0 28px 56px -18px rgba(0,0,0,0.75);
      font: 13.5px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      transition: left .18s ease, top .18s ease, opacity .15s ease;
    }
    .ob-pop.no-anim { transition: none; }
    .ob-pop.is-dragging { opacity: 0; pointer-events: none; }
    .ob-pop::before {
      content: ''; position: absolute; width: 16px; height: 16px; box-sizing: border-box;
      background: #111827; border: 1px solid #4b5563; transform: rotate(45deg); display: none;
    }
    .ob-pop[data-side="right"]::before  { display: block; left: -9px;  top: calc(var(--ax, 30px) - 8px);  border-right: 0; border-top: 0; }
    .ob-pop[data-side="left"]::before   { display: block; right: -9px; top: calc(var(--ax, 30px) - 8px);  border-left: 0;  border-bottom: 0; }
    .ob-pop[data-side="bottom"]::before { display: block; top: -9px;   left: calc(var(--ax, 30px) - 8px); border-right: 0; border-bottom: 0; }
    .ob-pop[data-side="top"]::before    { display: block; bottom: -9px; left: calc(var(--ax, 30px) - 8px); border-left: 0;  border-top: 0; }

    .ob-pop--center { left: 50%; top: 50%; transform: translate(-50%, -50%); width: 380px; z-index: 76; transition: none; }
    .ob-prog { height: 3px; background: #1f2937; border-radius: 12px 12px 0 0; overflow: hidden; }
    .ob-prog i { display: block; height: 100%; background: #10b981; transition: width .25s ease; }
    .ob-body { padding: 16px 18px 16px; }
    .ob-count { margin: 0 0 4px; font-size: 12px; color: #34d399; font-weight: 600; }
    .ob-h { margin: 0 0 6px; font-size: 16px; font-weight: 600; color: #f9fafb; line-height: 1.3; }
    .ob-p { margin: 0; color: #b6c2d1; }
    .ob-p b { color: #f9fafb; font-weight: 600; }
    .ob-p + .ob-p { margin-top: 8px; }
    .ob-blk {
      display: inline-block; padding: 0 7px; border-radius: 4px; color: #fff; font-weight: 600; font-size: 12px;
      line-height: 1.6; background: var(--c, #6b7280); box-shadow: inset 0 -2px 0 rgba(0,0,0,0.2);
    }
    .ob-foot { display: flex; align-items: center; gap: 14px; margin-top: 14px; }
    .ob-foot .ob-sp { flex: 1; }
    .ob-btn {
      border: 0; cursor: pointer; font: inherit; font-weight: 600; font-size: 13px; white-space: nowrap;
      background: #10b981; color: #052e21; padding: 8px 16px; border-radius: 7px;
    }
    .ob-btn:hover { background: #34d399; }
    .ob-link {
      border: 0; background: transparent; cursor: pointer; font: inherit; font-size: 13px;
      color: #8b98a9; text-decoration: underline; text-underline-offset: 3px; padding: 0;
    }
    .ob-link:hover { color: #f3f4f6; }
    .ob-art { display: block; margin: 0 0 14px; max-width: 100%; height: auto; }
    @media (max-width: 639px) {
      .ob-pop--center { width: calc(100vw - 24px); }
      .ob-foot { flex-wrap: wrap; gap: 10px 14px; }
      .ob-foot .ob-sp { display: none; }
      .ob-body { padding: 14px 16px; }
    }
    @media (prefers-reduced-motion: reduce) {
      .ob-pop, .ob-prog i { transition: none; }
      .ob-glow, .ob-ring-in, .ob-ring-out, .ob-slot .ob-hole,
      body.ob-active .blocklyHighlightedConnectionPathVisible { animation: none; }
      .ob-glow { filter: drop-shadow(0 0 2px #34d399) drop-shadow(0 0 5px rgba(52,211,153,0.8)); }
      .ob-ring-out { box-shadow: 0 0 0 2px #34d399; }
    }
  `;
  const styleEl = document.createElement('style');
  styleEl.id = 'ob-style';
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  /* ---------- DOM ---------- */
  const SVGNS = 'http://www.w3.org/2000/svg';
  function mk(cls) {
    const n = document.createElement('div');
    n.className = cls;
    document.body.appendChild(n);
    return n;
  }
  const backdrop = mk('ob-backdrop');
  const leader = document.createElementNS(SVGNS, 'svg');
  leader.setAttribute('class', 'ob-leader');
  leader.setAttribute('aria-hidden', 'true');
  leader.innerHTML = '<line x1="0" y1="0" x2="0" y2="0"/><circle cx="0" cy="0" r="4"/>';
  document.body.appendChild(leader);
  const pop = mk('ob-pop');
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-live', 'polite');

  const isMobile = () => window.matchMedia('(max-width: 639px)').matches;
  const wide = () => window.innerWidth >= 900 && !isMobile();

  /* ---------- geometry helpers ---------- */
  function rectOf(el) {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 ? r : null;
  }
  function union(rects) {
    const rs = rects.filter(Boolean);
    if (!rs.length) return null;
    const left = Math.min(...rs.map((r) => r.left)), top = Math.min(...rs.map((r) => r.top));
    const right = Math.max(...rs.map((r) => r.right)), bottom = Math.max(...rs.map((r) => r.bottom));
    return { left, top, right, bottom, width: right - left, height: bottom - top };
  }
  function overlap(a, b) {
    return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  }
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  function bannerH() {
    const bar = document.querySelector('[aria-label="Cookie choices"]');
    return bar ? bar.offsetHeight : 0;
  }
  const mainRect = () => rectOf(document.querySelector('main'));
  const isOff = (r) => { const m = mainRect(); return !!(r && m && !overlap(r, m)); };
  function blockRect(block) {
    try { return rectOf(block && block.getSvgRoot()); } catch (_) { return null; }
  }
  const svgRoot = (block) => { try { return block && block.getSvgRoot(); } catch (_) { return null; } };
  function slotConnection(block, inputName) {
    try { return block.getInput(inputName).connection || null; } catch (_) { return null; }
  }
  function categoryEl(name) {
    return [...document.querySelectorAll('.blocklyToolboxCategory')].find((n) => n.textContent.trim() === name) || null;
  }
  function flyoutInfo() {
    try {
      const fl = ws.getFlyout && ws.getFlyout();
      if (!fl || !fl.isVisible()) return null;
      const tb = ws.getToolbox && ws.getToolbox();
      const sel = tb && tb.getSelectedItem && tb.getSelectedItem();
      return { cat: sel && sel.getName ? sel.getName() : null, fl };
    } catch (_) { return null; }
  }
  function flyoutBlock(fi, type) {
    try { return fi.fl.getWorkspace().getTopBlocks(false).find((x) => x.type === type) || null; } catch (_) { return null; }
  }
  function flyoutRect(fi) {
    try { return rectOf(fi.fl.getWorkspace().getParentSvg()); } catch (_) { return null; }
  }

  /* ---------- highlights: applied to the real thing, never a drawn box ---------- */
  const applied = { glow: new Set(), ringIn: new Set(), ringOut: new Set() };
  let slotG = null;
  let slotKey = '';
  let highlightedConn = null;

  function syncClass(set, list, cls) {
    const want = new Set(list.filter(Boolean));
    set.forEach((el) => { if (!want.has(el)) { el.classList.remove(cls); set.delete(el); } });
    want.forEach((el) => { if (!set.has(el)) { el.classList.add(cls); set.add(el); } });
  }

  /* The slot a block will be dropped into.

     This used to draw the outline of the FUTURE block in the slot. That was
     wrong in size: an empty slot inside the comparison is ~25 px wide, the RSI
     block that will go in is ~118 px, so the outline covered the comparison
     itself and stuck out of it (and the THEN outline hung below the rule).
     Now only the slot is marked, using geometry the parent block already has:
       - an inline slot (LEFT / RIGHT of the comparison) is a hole in its
         parent's own path: that exact hole is outlined;
       - the THEN slot is a cut-out in the rule's path (rounded left end, open
         to the block's right edge): its exact outline is read from that path;
       - an outer value slot (Condition, Stop Loss) is just a tab-shaped notch
         in the block's edge: it gets Blockly's own connection highlight, which
         IS that notch.
     (A round "ping" marker was tried on the THEN slot and looked like a green
     smiley laid over the label; a circle claims no shape, so it fits nothing.)
     It lives in the workspace canvas, so it pans and zooms with the blocks. */
  // Blockly's input kinds (Blockly.inputs.inputTypes in v13): value = 1, statement = 3.
  const T_VALUE = 1, T_STATEMENT = 3;

  /* The empty statement slot, in the parent's own coordinates. The rule's path
     goes: ... V <top> H <x> l -6,4 -3,0 -6,-4 h -7 a 8 8 0 0,0 -8,8 v <h> a 8 8 0 0,0 8,8 H <right> ...
     i.e. along the top edge of the C, the notch, a rounded left end, then back
     along its bottom edge to the block's right edge. That is the cut-out. */
  function statementPath(owner, inputName, conn) {
    try {
      const input = owner.getInput(inputName);
      if (!input || input.type !== T_STATEMENT) return null;
      const d = owner.getSvgRoot().querySelector(':scope > path.blocklyPath').getAttribute('d');
      const re = /V\s+([\d.]+)\s+H\s+([\d.]+)\s+l\s+-6,4\s+-3,0\s+-6,-4\s+h\s+-7\s+a\s+8\s+8\s+0\s+0,0\s+-8,8\s+v\s+([\d.]+)\s+a\s+8\s+8\s+0\s+0,0\s+8,8\s+H\s+([\d.]+)/g;
      const off = conn.getOffsetInBlock();
      let m;
      while ((m = re.exec(d))) {
        const top = +m[1], notchX = +m[2], vv = +m[3], right = +m[4];
        if (Math.abs(top - off.y) > 1.5) continue;   // a different statement input
        const left = notchX - 22;                     // notch (15) + h -7
        return 'M ' + right + ',' + top + ' H ' + left + ' a 8 8 0 0 0 -8,8 v ' + vv + ' a 8 8 0 0 0 8,8 H ' + right + ' z';
      }
      return null;
    } catch (_) { return null; }
  }
  function holePath(owner, inputName) {
    try {
      const input = owner.getInput(inputName);
      if (!input || !input.connection || !owner.getInputsInline() || input.type !== T_VALUE) return null;
      const off = input.connection.getOffsetInBlock();
      const d = owner.getSvgRoot().querySelector(':scope > path.blocklyPath').getAttribute('d');
      // Subpaths after the first are the holes; each starts at "M x,y".
      const subs = d.split(/(?= M )/).slice(1).map((s) => s.trim());
      return subs.find((s) => {
        const m = s.match(/^M\s+(-?[\d.]+),(-?[\d.]+)/);
        return m && Math.abs(+m[1] - off.x) < 1.5 && Math.abs(+m[2] - (off.y - 5)) < 1.5;
      }) || null;
    } catch (_) { return null; }
  }

  function drawSlot(slot) {
    try {
      const { owner, input } = slot;
      const conn = slotConnection(owner, input);
      if (!conn) return null;
      const canvas = ws.getBlockCanvas();
      if (!slotG) {
        slotG = document.createElementNS(SVGNS, 'g');
        slotG.setAttribute('class', 'ob-slot');
        slotG.innerHTML = '<path class="ob-hole"/>';
      }
      const holeEl = slotG.querySelector('.ob-hole');
      // An exact outline where the parent's path has one; otherwise the native tab.
      const outline = holePath(owner, input) || statementPath(owner, input, conn);
      const o = owner.getRelativeToSurfaceXY();
      const key = [outline || '', o.x, o.y].join('|');
      if (key !== slotKey) {
        slotKey = key;
        if (outline) {
          holeEl.setAttribute('d', outline);
          holeEl.setAttribute('transform', 'translate(' + o.x + ',' + o.y + ')');
          holeEl.style.display = '';
        } else {
          holeEl.style.display = 'none';
        }
      }
      if (outline) {
        if (slotG.parentNode !== canvas) canvas.appendChild(slotG);
        // Last child = drawn above the blocks. Blockly moves a block to the end
        // of its canvas whenever it is picked up or connected, so this is
        // re-checked on every pass.
        if (canvas.lastChild !== slotG) canvas.appendChild(slotG);
        highlightConn(null);
        return { el: holeEl, outline: true };
      }
      removeSlot();
      highlightConn(conn);
      const tab = conn.findHighlightSvg && conn.findHighlightSvg();
      return tab ? { el: tab, outline: false } : null;
    } catch (_) { return null; }
  }
  function removeSlot() {
    if (slotG && slotG.parentNode) slotG.parentNode.removeChild(slotG);
    slotKey = '';
  }
  function highlightConn(conn) {
    if (conn === highlightedConn) return;
    try { if (highlightedConn) highlightedConn.unhighlight(); } catch (_) {}
    highlightedConn = conn;
    try { if (conn) conn.highlight(); } catch (_) {}
  }

  /* One call sets everything for the current step; anything not listed is cleared. */
  function setFocus(f) {
    f = f || {};
    syncClass(applied.glow, f.glow || [], 'ob-glow');
    syncClass(applied.ringIn, f.ringIn || [], 'ob-ring-in');
    syncClass(applied.ringOut, f.ringOut || [], 'ob-ring-out');
    if (f.slot) return drawSlot(f.slot);
    highlightConn(null);
    removeSlot();
    return null;
  }
  function clearFocus() { setFocus({}); }

  /* ---------- placing the callout next to its target ---------- */
  /* `inCanvas` keeps the callout inside the canvas area so it never covers
     the top bar; the export step points AT the top bar, so it opts out. */
  /* Tries the callout at its full width and then narrower ones, and settles on
     the first width where it can stand clear of everything; if none can, the
     widest that at least keeps clear of the hard list. A callout squeezed into
     a small window should get smaller, not land on what it points at. */
  function place(target, avoid, prefer, inCanvas, hard) {
    const base = pop.offsetWidth;
    const widths = [base].concat([300, 270].filter((x) => x < base));
    let best = { level: -1, width: base };
    for (const wd of widths) {
      pop.style.width = wd + 'px';
      const level = placeOnce(target, avoid, prefer, inCanvas, hard, true);
      if (level > best.level) best = { level, width: wd };
      if (level === 2) break;
    }
    pop.style.width = best.width + 'px';
    placeOnce(target, avoid, prefer, inCanvas, hard, false);
  }

  function placeOnce(target, avoid, prefer, inCanvas, hard, dry) {
    const M = 10, GAP = 20;
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight - bannerH();
    const mainR = inCanvas === false ? null : mainRect();
    const top0 = mainR ? Math.max(M, mainR.top + 8) : M;
    const cx = (target.left + target.right) / 2, cy = (target.top + target.bottom) / 2;
    const A = (avoid || []).filter(Boolean);
    const boxAt = (x, y) => ({ left: x, top: y, right: x + w, bottom: y + h });
    // Anything the callout must not cover (the rule, the block list): slide the
    // candidate down past it instead of dropping the candidate.
    const pushDown = (x, y) => {
      for (let i = 0; i < 4; i++) {
        const hit = A.find((a) => overlap(boxAt(x, y), a));
        if (!hit) break;
        y = hit.bottom + 14;
      }
      return y;
    };
    const ALL = ['right', 'bottom', 'left', 'top'];
    const candidates = (push, sides) => {
      const out = [];
      (sides || prefer || ALL).forEach((side) => {
        ['center', 'start', 'end'].forEach((al) => {
          let x, y;
          if (side === 'right' || side === 'left') {
            x = side === 'right' ? target.right + GAP : target.left - GAP - w;
            y = al === 'center' ? cy - h / 2 : (al === 'start' ? target.top : target.bottom - h);
          } else {
            y = side === 'bottom' ? target.bottom + GAP : target.top - GAP - h;
            x = al === 'center' ? cx - w / 2 : (al === 'start' ? target.left : target.right - w);
          }
          if (push && side !== 'top') y = pushDown(x, y);
          out.push({ side, x, y });
        });
      });
      return out;
    };
    const fits = (t) => t.x >= M && t.y >= top0 && t.x + w <= vw - M && t.y + h <= vh - M;
    const clash = (t) => A.some((a) => overlap(boxAt(t.x, t.y), a));
    // `hard` is what the callout must NEVER cover: the thing it points at, the
    // slot, the strip a block lands in, the open block list. `avoid` (the rule)
    // is preferred but can give way when the window is too small for everything.
    const H = [target].concat(hard || []).filter(Boolean);
    const clashHard = (t) => H.some((a) => overlap(boxAt(t.x, t.y), a));
    const ok = (t) => fits(t) && !clash(t) && !clashHard(t);
    // Pass 1: a preferred side that already clears everything. Pass 2: the same
    // spots slid down past whatever is in the way. Pass 3: any side. Then, only
    // if the window is too small, give up on the soft avoid list but never on
    // the hard one.
    const raw = candidates(false), pushed = candidates(true);
    const rawAll = candidates(false, ALL), pushedAll = candidates(true, ALL);
    const pick = raw.find(ok) || pushed.find(ok) || rawAll.find(ok) || pushedAll.find(ok) ||
      rawAll.find((t) => fits(t) && !clashHard(t)) || pushedAll.find((t) => fits(t) && !clashHard(t)) ||
      rawAll.find((t) => !clashHard(t)) || raw.find(fits) || raw[0];
    if (dry) return ok(pick) ? 2 : ((fits(pick) && !clashHard(pick)) ? 1 : 0);
    const x = clamp(pick.x, M, Math.max(M, vw - M - w));
    const y = clamp(pick.y, top0, Math.max(top0, vh - M - h));
    const horizontal = pick.side === 'left' || pick.side === 'right';
    const ax = horizontal ? clamp(cy - y, 22, h - 22) : clamp(cx - x, 22, w - 22);
    // First placement after the callout was hidden must not glide in from
    // wherever it last stood (or from the corner).
    const fresh = pop.dataset.placed !== '1';
    if (fresh) pop.classList.add('no-anim');
    pop.style.left = x + 'px';
    pop.style.top = y + 'px';
    pop.style.setProperty('--ax', ax + 'px');
    pop.dataset.side = pick.side;
    if (fresh) {
      pop.dataset.placed = '1';
      void pop.offsetWidth;
      setTimeout(() => pop.classList.remove('no-anim'), 30);
    }
    drawLeader(target, pick.side, x, y, ax);
  }
  function centerPop() {
    pop.style.left = '50%'; pop.style.top = '40%';
    pop.style.transform = 'translateX(-50%)';
    delete pop.dataset.side;
    hideLeader();
  }

  /* Arrow tip -> nearest point of the target; hidden when they already touch. */
  function drawLeader(target, side, x, y, ax) {
    let tx, ty;
    if (side === 'right') { tx = x - 9; ty = y + ax; }
    else if (side === 'left') { tx = x + pop.offsetWidth + 9; ty = y + ax; }
    else if (side === 'bottom') { tx = x + ax; ty = y - 9; }
    else { tx = x + ax; ty = y + pop.offsetHeight + 9; }
    const px = clamp(tx, target.left, target.right), py = clamp(ty, target.top, target.bottom);
    // Only a short bridge: a line that crosses half the screen points at nothing.
    const dist = Math.hypot(px - tx, py - ty);
    if (dist < 16 || dist > 160 || pop.classList.contains('is-dragging')) { hideLeader(); return; }
    const ln = leader.querySelector('line'), c = leader.querySelector('circle');
    ln.setAttribute('x1', tx); ln.setAttribute('y1', ty); ln.setAttribute('x2', px); ln.setAttribute('y2', py);
    c.setAttribute('cx', px); c.setAttribute('cy', py);
    leader.style.display = 'block';
  }
  function hideLeader() { leader.style.display = 'none'; }

  /* ---------- reading the workspace ---------- */
  function ruleState() {
    const rule = ws.getTopBlocks(true).find((b) => b.type === 'trade_if') || null;
    const s = { rule, cond: null, cmp: null, left: null, right: null, action: null, sl: null };
    if (!rule) return s;
    s.cond = rule.getInputTargetBlock('CONDITION');
    if (s.cond && s.cond.type === 'comparison_block') {
      s.cmp = s.cond;
      s.left = s.cmp.getInputTargetBlock('LEFT');
      s.right = s.cmp.getInputTargetBlock('RIGHT');
    }
    s.action = rule.getInputTargetBlock('DO');
    if (s.action && s.action.type === 'action_block') s.sl = s.action.getInputTargetBlock('SL');
    return s;
  }
  /* Untouched canvas: exactly the seeded IF block, nothing connected. */
  function isPristine() {
    const tops = ws.getTopBlocks(false);
    if (tops.length !== 1 || tops[0].type !== 'trade_if') return false;
    const s = ruleState();
    return !s.cond && !s.action;
  }
  /* Nothing worth keeping: an empty canvas or just the untouched starter rule. */
  const isBlank = () => ws.getTopBlocks(false).length === 0 || isPristine();
  const ruleGroupRect = (s) => (s.rule ? blockRect(s.rule) : null);
  const exportRect = () => union((B.exportButtons || []).map(rectOf));

  /* ---------- the steps ---------- */
  const chip = (color, label) => '<span class="ob-blk" style="--c:' + color + '">' + label + '</span>';

  /* "do" steps: the visitor takes a block from a toolbox category and drops
     it into a slot. `socket` says which slot; `done` reads the workspace. */
  const BUILD = [
    { id: 'rule', kind: 'info' },
    {
      id: 'condition', kind: 'do', cat: 'Conditions', type: 'comparison_block',
      title: 'Add a condition',
      done: (s) => !!s.cmp,
      socket: (s) => (s.rule ? { block: s.rule, input: 'CONDITION' } : null),
      closed: 'A rule needs something to check. Click <b>Conditions</b> to open its blocks.',
      open: 'Drag the ' + chip('#d97706', 'A &gt; B') + ' comparison block into the highlighted <b>Condition</b> slot.',
    },
    {
      id: 'indicator', kind: 'do', cat: 'Indicators', type: 'indicator_rsi',
      title: 'Choose an indicator',
      done: (s) => !!s.left,
      socket: (s) => (s.cmp ? { block: s.cmp, input: 'LEFT' } : null),
      closed: 'Indicators turn price history into numbers. Click <b>Indicators</b>.',
      open: 'Drag ' + chip('#7c3aed', 'RSI') + ' into the <b>left</b> side of the comparison. RSI shows whether a market looks overbought or oversold.',
    },
    {
      id: 'number', kind: 'do', cat: 'Risk & Sizing', type: 'math_number',
      title: 'Add a number to compare with',
      done: (s) => !!s.right,
      socket: (s) => (s.cmp ? { block: s.cmp, input: 'RIGHT' } : null),
      closed: 'Now the value to compare it with. Click <b>Risk &amp; Sizing</b>.',
      open: 'Drag the plain ' + chip('#0d9488', '0') + ' number block into the <b>right</b> side. Then click it to type 30 and use the middle dropdown to pick <b>&lt;</b>.',
    },
    {
      id: 'action', kind: 'do', cat: 'THEN', type: 'action_block',
      title: 'Choose what to do',
      done: (s) => !!(s.action && s.action.type === 'action_block'),
      socket: (s) => (s.rule ? { block: s.rule, input: 'DO' } : null),
      closed: 'When the condition is true, the rule places a trade. Click <b>THEN</b>.',
      open: 'Drag ' + chip('#059669', 'Open BUY') + ' into the highlighted <b>THEN</b> slot.',
    },
    {
      id: 'stoploss', kind: 'do', cat: 'Risk & Sizing', type: 'risk_value_block',
      title: 'Protect the trade with a stop loss',
      done: (s) => !!s.sl,
      socket: (s) => (s.action ? { block: s.action, input: 'SL' } : null),
      closed: 'A stop loss closes a losing trade before it costs too much. Click <b>Risk &amp; Sizing</b>.',
      open: 'Drag ' + chip('#0d9488', 'Risk Value') + ' into <b>Stop Loss</b>. It sets the most one trade can lose, for example 30 pips.',
    },
    { id: 'export', kind: 'final' },
  ];
  const EXAMPLE = [
    { id: 'ex-read', kind: 'info' },
    { id: 'ex-change', kind: 'edit' },
    { id: 'export', kind: 'final' },
  ];

  /* ---------- tour state ---------- */
  let view = null;         // null | 'welcome' | 'tour'
  let kind = 'build';      // 'build' | 'example'
  let stage = 0;           // 0 = first info step still showing; 1+ = state-driven
  let exampleEdited = false;
  let exampleBaseline = '';
  let loading = false;
  let pendingNudge = false;
  let finalNudged = false;
  let framedKey = '';
  let fitKey = '';
  let lastKey = '';
  let timer = null;
  const reported = new Set();

  const totalSteps = () => (kind === 'build' ? BUILD.length : EXAMPLE.length);

  /* Every editable value on the canvas, as one string. "Did the visitor
     change something" is answered by comparing this with the string taken
     right after the example loaded, which does not depend on when Blockly
     happens to deliver its change events. */
  function fieldSnapshot() {
    return ws.getAllBlocks(false).map((b) => b.id + ':' + b.inputList
      .reduce((acc, i) => acc.concat(i.fieldRow), [])
      .filter((f) => f.name)
      .map((f) => f.name + '=' + f.getValue()).join(',')).join('|');
  }

  function currentStep(s) {
    if (kind === 'build') {
      if (stage === 0) return { step: BUILD[0], n: 1 };
      for (let i = 1; i < BUILD.length - 1; i++) {
        if (!BUILD[i].done(s)) return { step: BUILD[i], n: i + 1 };
      }
      return { step: BUILD[BUILD.length - 1], n: BUILD.length };
    }
    if (!exampleEdited && !loading && fieldSnapshot() !== exampleBaseline) {
      exampleEdited = true;
      track('guide_step_done', { step: 'change' });
    }
    if (stage === 0) return { step: EXAMPLE[0], n: 1 };
    if (!exampleEdited) return { step: EXAMPLE[1], n: 2 };
    return { step: EXAMPLE[2], n: 3 };
  }

  /* ---------- welcome card ---------- */
  // A miniature of what the tour builds, drawn with the app's own block colours.
  const F = 'font-family="ui-sans-serif,system-ui,sans-serif" font-weight="700" fill="#fff"';
  const ART =
    '<svg class="ob-art" width="252" height="100" viewBox="0 0 252 100" fill="none" aria-hidden="true">' +
    '<rect x="2" y="2" width="248" height="42" rx="9" fill="#d97706"/>' +
    '<rect x="11" y="10" width="92" height="26" rx="6" fill="#7c3aed"/><text x="57" y="28" text-anchor="middle" font-size="14" ' + F + '>RSI</text>' +
    '<rect x="110" y="10" width="40" height="26" rx="6" fill="#fdba74"/><text x="130" y="29" text-anchor="middle" font-size="16" font-weight="700" font-family="ui-sans-serif,system-ui,sans-serif" fill="#7c2d12">&lt;</text>' +
    '<rect x="157" y="10" width="84" height="26" rx="6" fill="#0d9488"/><text x="199" y="28" text-anchor="middle" font-size="14" ' + F + '>30</text>' +
    '<path d="M30 44v10" stroke="#6b7280" stroke-width="2" stroke-dasharray="3 3"/>' +
    '<rect x="2" y="54" width="248" height="44" rx="9" fill="#059669"/>' +
    '<text x="14" y="82" font-size="14" ' + F + '>Open BUY</text>' +
    '<rect x="112" y="62" width="130" height="28" rx="6" fill="#0d9488"/><text x="177" y="81" text-anchor="middle" font-size="13" ' + F + '>Stop Loss 30 pips</text>' +
    '</svg>';

  function resetPop() {
    delete pop.dataset.placed;
    delete pop.dataset.side;
    pop.style.transform = '';
    hideLeader();
  }

  function showWelcome(opts) {
    opts = opts || {};
    stopTour(true);
    view = 'welcome';
    clearFocus();
    resetPop();
    backdrop.style.display = 'block';
    pop.className = 'ob-pop ob-pop--center';
    pop.style.display = 'block';
    pop.style.left = ''; pop.style.top = '';
    pop.setAttribute('aria-label', 'Welcome');
    pop.innerHTML =
      '<div class="ob-body">' + ART +
      '<h2 class="ob-h">' + (opts.note ? 'Nothing to export yet' : 'Build your first strategy') + '</h2>' +
      '<p class="ob-p">' + (opts.note ||
        'Strategies here are made of blocks that snap together. Take the short tour and each step points at exactly where to click, or start from a finished example.') + '</p>' +
      '<div class="ob-foot"><button type="button" class="ob-btn" data-act="tour">Start the tour</button>' +
      '<button type="button" class="ob-link" data-act="example">Load an example instead</button>' +
      '<span class="ob-sp"></span><button type="button" class="ob-link" data-act="skip">Skip</button></div></div>';
    const b = pop.querySelector('[data-act="tour"]');
    if (b) b.focus();
    lastKey = '';
    track('onboarding_shown', { source: opts.source || 'auto' });
  }

  /* ---------- rendering one step ---------- */
  const progressBar = (n) => '<div class="ob-prog"><i style="width:' + Math.round((n / totalSteps()) * 100) + '%"></i></div>';
  const head = (n, title) => '<p class="ob-count">Step ' + n + ' of ' + totalSteps() + '</p><h2 class="ob-h">' + title + '</h2>';
  function foot(nextLabel, off) {
    return '<div class="ob-foot"><button type="button" class="ob-link" data-act="skip">Skip tour</button>' +
      (off ? '<button type="button" class="ob-link" data-act="find">Show me</button>' : '') +
      '<span class="ob-sp"></span>' +
      (nextLabel ? '<button type="button" class="ob-btn" data-act="next">' + nextLabel + '</button>' : '') + '</div>';
  }
  function setPop(key, n, title, bodyHtml, footHtml) {
    if (key === lastKey) return;
    lastKey = key;
    pop.innerHTML = progressBar(n) + '<div class="ob-body">' + head(n, title) + bodyHtml + footHtml + '</div>';
    pop.setAttribute('aria-label', title);
  }

  function render() {
    if (view !== 'tour') return;
    // While a block is being dragged Blockly closes the block list. That must not
    // be read as "list closed" (the callout would jump over to the category and
    // the slot marker would change under the visitor's hand): freeze everything,
    // hide the callout, and leave the slot marker exactly where it is until the drop.
    if (ws.isDragging && ws.isDragging()) {
      pop.classList.add('is-dragging');
      hideLeader();
      return;
    }
    hideLeader(); // place() draws it again when the callout stands back from its target
    let s = ruleState();

    // The rule block is gone (deleted, or the canvas was cleared): the tour
    // starts over from the standard starter rule instead of dead-ending.
    if (!s.rule) {
      if (kind === 'example') { stopTour(); return; }
      B.createDefaultRuleBlock(40, 40);
      pendingNudge = true;
      s = ruleState();
      if (!s.rule) return;
    }
    // The example tour makes no sense once its rule was cleared or replaced.
    if (kind === 'example' && (isPristine() || !ws.getAllBlocks(false).some((b) => b.type === 'math_number'))) {
      stopTour();
      return;
    }
    if (pendingNudge && blockRect(s.rule)) { nudgeCanvas(); pendingNudge = false; }

    const { step, n } = currentStep(s);

    // Report each build step once, when it is left behind.
    if (kind === 'build') {
      BUILD.forEach((st, i) => {
        if (st.kind === 'do' && i + 1 < n && !reported.has(st.id)) {
          reported.add(st.id);
          track('guide_step_done', { step: st.id });
        }
      });
    }

    pop.className = 'ob-pop';
    pop.style.transform = '';
    pop.style.width = '';   // renderDo narrows it when a block list is open on a small window
    pop.style.display = 'block';
    backdrop.style.display = 'none';
    pop.classList.toggle('is-dragging', !!(ws.isDragging && ws.isDragging()));

    if (step.kind === 'info' || step.kind === 'final') return renderInfo(step, n, s);
    if (step.kind === 'edit') return renderEdit(step, n, s);
    return renderDo(step, n, s);
  }

  function renderInfo(step, n, s) {
    let target, title, body, next = 'Next';
    // A block list left open from the previous step would sit behind the highlight.
    try { if (flyoutInfo()) ws.getToolbox().clearSelection(); } catch (_) {}
    if (fitKey !== step.id) { fitKey = step.id; if (fitRule(ruleGroupRect(s))) return; }
    if (step.id === 'rule') {
      target = ruleGroupRect(s);
      title = 'This is your rule';
      body = 'Every strategy is one rule: <b>IF</b> something happens, <b>THEN</b> place a trade. The market and chart timeframe are already chosen, and you can change either from its dropdown.';
    } else if (step.id === 'ex-read') {
      target = ruleGroupRect(s);
      title = 'A finished example';
      body = 'This rule trades <b>EUR/USD</b> on the <b>1 hour</b> chart. When <b>RSI(14)</b> drops below <b>30</b>, it opens a <b>BUY</b> with a <b>30 pip</b> stop loss and a <b>90 pip</b> take profit.';
    } else {
      target = exportRect();
      title = kind === 'build' ? 'Your strategy is ready' : 'Export it';
      body = 'Pick your platform here to download the strategy file: <b>MT5</b>, <b>MT4</b> or <b>cTrader</b>. You will be asked to sign up or log in first.';
      next = 'Got it';
    }
    const off = step.id !== 'export' && isOff(target);
    setPop(step.id + (off ? '|off' : ''), n, title, '<p class="ob-p">' + body + '</p>', foot(next, off));
    // The rule glows along its own outline; the export buttons get a ring that
    // follows their rounded corners.
    setFocus(step.id === 'export' ? { ringOut: B.exportButtons || [] } : { glow: [svgRoot(s.rule)] });
    if (!target) { centerPop(); return; }
    place(target, step.id === 'export' ? [ruleGroupRect(s)] : [], step.id === 'export' ? ['bottom', 'left'] : ['bottom', 'right', 'top'], step.id !== 'export');
    // On a short window there is no room below the rule for the export callout.
    // Rather than cover the finished rule, slide the canvas left, once, so the
    // rule steps out from under it.
    if (step.id === 'export' && !finalNudged) {
      const rr = ruleGroupRect(s);
      const pr = rectOf(pop);
      if (rr && pr && overlap(pr, rr)) {
        finalNudged = true;
        try { ws.scroll(ws.scrollX - Math.max(0, rr.left - 163), ws.scrollY); } catch (_) {}
      }
    }
  }

  function renderEdit(step, n, s) {
    const numBlock = ws.getAllBlocks(false).find((b) => b.type === 'math_number');
    const target = blockRect(numBlock) || ruleGroupRect(s);
    const off = isOff(target);
    setPop(step.id + (off ? '|off' : ''), n, 'Change a value',
      '<p class="ob-p">Click the <b>30</b> and type another number, such as 25. Every value in a block can be edited the same way.</p>', foot(null, off));
    setFocus({ glow: [svgRoot(numBlock)] });
    if (!target) return;
    place(target, [ruleGroupRect(s)], ['right', 'bottom', 'top']);
  }

  function renderDo(step, n, s) {
    const fi = flyoutInfo();
    const open = !!(fi && fi.cat === step.cat);
    const sock = step.socket(s);
    const slot = sock ? { owner: sock.block, input: sock.input } : null;
    const ruleRect = ruleGroupRect(s);
    const off = isOff(ruleRect);
    const mobileClosed = isMobile() && !document.body.classList.contains('toolbox-open');

    // The slot already holds a different block (for example an AND block in the
    // Condition slot): say so plainly instead of pointing at a slot that is full.
    const occupant = step.id === 'condition' && s.cond && !s.cmp ? s.cond : null;
    if (occupant) {
      setPop(step.id + '|taken', n, step.title,
        '<p class="ob-p">This slot already holds a different block. Drag it out onto the block list on the left, or click it and press <b>Delete</b>, then add the comparison block.</p>', foot(null, off));
      setFocus({ glow: [svgRoot(occupant)] });
      const r = blockRect(occupant);
      if (r) place(r, [], ['bottom', 'right', 'top']); else centerPop();
      return;
    }

    if (!open) {
      framedKey = '';
      // Back from an open list (which may have pushed the rule sideways) or a
      // fresh step: make sure the whole rule is inside the canvas, once.
      if (fitKey !== step.id) { fitKey = step.id; if (fitRule(ruleRect)) return; }
      const anchorEl = mobileClosed ? document.getElementById('toolboxToggleBtn') : categoryEl(step.cat);
      const target = rectOf(anchorEl);
      const text = mobileClosed ? 'Tap <b>Blocks</b>, then choose <b>' + step.cat.replace('&', '&amp;') + '</b>.' : step.closed;
      setPop(step.id + '|closed|' + (mobileClosed ? 'm' : 'd') + (off ? '|off' : ''), n, step.title, '<p class="ob-p">' + text + '</p>', foot(null, off));
      // The category (or the Blocks button on a phone) gets a ring that fits it,
      // and the slot it will go into is marked with its own geometry.
      const sm = setFocus(mobileClosed ? { ringOut: [anchorEl], slot } : { ringIn: [anchorEl], slot });
      const smR = sm ? rectOf(sm.el) : null;
      if (target) place(target, [ruleRect], ['right', 'bottom'], true, [smR, dropZoneOf(sm, smR)]); else centerPop();
      return;
    }

    // Category list is open: the block to take glows along its own outline, and
    // the slot it goes into is marked.
    const fb = flyoutBlock(fi, step.type);
    const blockR = blockRect(fb);
    const flyR = flyoutRect(fi);
    const key = step.id + '|open';
    fitKey = '';
    setPop(key + (off ? '|off' : ''), n, step.title, '<p class="ob-p">' + step.open + '</p>', foot(null, off));
    const marked = setFocus({ glow: [svgRoot(fb)], slot });
    let slotR = marked ? rectOf(marked.el) : null;
    // Slide the canvas so the slot is clear of the block list and has room
    // around it; the visitor should never have to hunt for it. The list grows to
    // its full width a moment after it opens, so this is repeated whenever its
    // edge moves (and only then, so it cannot fight the visitor).
    const frameKey = key + '|' + Math.round(flyR ? flyR.right : 0);
    if (slotR && framedKey !== frameKey) {
      framedKey = frameKey;
      frameSlot(slotR, flyR, ruleRect, s.rule);
      slotR = rectOf(marked.el);
    }
    const slotVisible = slotR && !(flyR && overlap(slotR, flyR));
    // On a narrow window a full-width callout would not fit beside a wide block
    // list; make it narrower (down to a readable minimum) instead of covering the list.
    if (flyR) {
      const room = window.innerWidth - flyR.right - 30;
      pop.style.width = Math.max(260, Math.min(332, room)) + 'px';
    }
    const dropZone = dropZoneOf(marked, slotR);
    if (slotVisible) place(slotR, [ruleRect], ['bottom', 'right', 'top'], true, [flyR, dropZone]);
    else if (blockR) place(blockR, [ruleRect], ['right', 'bottom'], true, [flyR]);
    else centerPop();
  }

  /* An outer slot is only a notch in the block's edge: the block lands to the
     RIGHT of it, so that strip must stay clear of the callout too. */
  function dropZoneOf(marked, slotR) {
    if (!marked || marked.outline || !slotR) return null;
    const scale = ws.getScale();
    return { left: slotR.left, top: slotR.top - 8, right: slotR.right + 170 * scale, bottom: slotR.bottom + 8 };
  }

  /* ---------- flows ---------- */

  /* Slide the canvas so a slot is clear of the open block list (it would
     otherwise hide behind it), inside the canvas area, and with room around it
     for the callout. */
  function frameSlot(slotR, flyR, ruleR, ruleBlock) {
    try {
      const m = mainRect();
      if (!m) return;
      const leftBound = (flyR ? flyR.right : m.left + 147) + 28;
      const rightBound = m.right - 48;
      let dx = 0, dy = 0;
      if (ruleR && ruleR.left < leftBound && ruleR.right + (leftBound - ruleR.left) <= rightBound) {
        // The whole rule fits to the right of the block list: put it all there.
        dx = leftBound - ruleR.left;
      } else if (slotR.left < leftBound) dx = leftBound - slotR.left;
      else if (slotR.right > rightBound) dx = rightBound - slotR.right;
      if (slotR.top < m.top + 56) dy = m.top + 56 - slotR.top;
      else if (slotR.bottom > m.bottom - 90) dy = (m.bottom - 90) - slotR.bottom;
      if (dx || dy) {
        const beforeX = ws.scrollX;
        ws.scroll(ws.scrollX + dx, ws.scrollY + dy);
        // Blockly limits how far the view can be scrolled (by the extent of the
        // blocks), so on a narrow window it may fall short of what is needed to
        // clear the block list. Make up the rest by moving the rule itself.
        const shortBy = dx - (ws.scrollX - beforeX);
        if (dx > 0 && shortBy > 1 && ruleBlock) ruleBlock.moveBy(shortBy / ws.getScale(), 0);
      }
    } catch (_) {}
  }

  /* Keep the whole rule inside the canvas: never off the right edge, and never
     with its left end under the toolbox. Returns true if it moved the canvas
     (the caller then waits for the next pass to lay the callout out). */
  function fitRule(ruleR) {
    try {
      const m = mainRect();
      if (!m || !ruleR) return false;
      const minLeft = m.left + 147 + 10, maxRight = m.right - 24;
      let dx = 0;
      if (ruleR.right > maxRight) dx = maxRight - ruleR.right;
      if (ruleR.left + dx < minLeft) dx = minLeft - ruleR.left;
      if (Math.abs(dx) < 2) return false;
      ws.scroll(ws.scrollX + dx, ws.scrollY);
      return true;
    } catch (_) { return false; }
  }

  /* Bring the rule into a good starting position: clear of the toolbox and
     fully inside the canvas. On a wide window it sits to the right of a free
     lane for the callouts; on a narrower one it moves left instead, because a
     finished rule is about 560 px wide and must not run off the right edge. */
  function nudgeCanvas() {
    try {
      const s = ruleState();
      const r = blockRect(s.rule);
      const m = mainRect();
      if (!r || !m) return;
      const need = 560;                       // width of the finished rule
      const lane = m.left + 147 + 392;        // toolbox + callout + gap
      const roomy = wide() && (m.right - lane - 24 >= need);
      const left = roomy ? lane : m.left + 147 + 28;
      ws.scroll(ws.scrollX + (left - r.left), ws.scrollY + (m.top + (roomy ? 70 : 56) - r.top));
    } catch (_) {}
  }

  function startTour(which) {
    kind = which;
    stage = 0;
    exampleEdited = false;
    reported.clear();
    lastKey = '';
    view = 'tour';
    resetPop();
    pendingNudge = which === 'build';
    finalNudged = false;
    framedKey = '';
    fitKey = '';
    writeState({ seen: true });
    backdrop.style.display = 'none';
    document.body.classList.add('ob-active');
    track('guide_started', { path: which });
    clearInterval(timer);
    timer = setInterval(render, 100);
    render();
  }

  function stopTour(silent) {
    clearInterval(timer);
    timer = null;
    if (view === 'tour' || view === 'welcome') view = null;
    document.body.classList.remove('ob-active');
    clearFocus();
    resetPop();
    backdrop.style.display = 'none';
    pop.style.display = 'none';
    pop.innerHTML = '';
    lastKey = '';
    if (!silent) { /* nothing else to tidy */ }
  }

  function finish(path) {
    if (!readState().completed) {
      writeState({ completed: true, seen: true });
      track('guide_completed', { path });
    }
    stopTour();
  }

  function dismiss(where) {
    track('guide_dismissed', { where });
    writeState({ seen: true });
    stopTour();
  }

  function next() {
    const { step } = currentStep(ruleState());
    if (step.kind === 'final') { finish(kind); return; }
    if (stage === 0) { stage = 1; lastKey = ''; render(); }
  }

  /* Clears the canvas down to a single standard starter rule. */
  function resetCanvas() {
    loading = true;
    try {
      ws.getTopBlocks(false).forEach((b) => b.dispose(false));
      B.createDefaultRuleBlock(40, 40);
      B.markFresh();
    } finally {
      setTimeout(() => { loading = false; }, 400);
    }
  }

  /* The tour always starts from one fresh starter rule. An empty canvas just
     gets one; a canvas with real work in it asks first, because the tour
     replaces it. */
  async function beginBuild() {
    if (!isPristine()) {
      if (ws.getTopBlocks(false).length > 0) {
        const ok = await B.confirm('The tour starts from a fresh, empty rule, so it clears what is on the canvas now. Save your strategy first if you want to keep it. Start the tour anyway?');
        if (!ok) return;
      }
      resetCanvas();
    }
    startTour('build');
  }

  function buildBlock(type, fields) {
    const b = ws.newBlock(type);
    Object.keys(fields || {}).forEach((k) => b.setFieldValue(fields[k], k));
    b.initSvg();
    return b;
  }

  async function loadExample() {
    if (!isBlank()) {
      const ok = await B.confirm('Loading the example replaces what is on the canvas now. Save your strategy first if you want to keep it. Load the example anyway?');
      if (!ok) return;
    }
    loading = true;
    try {
      ws.getTopBlocks(false).forEach((b) => b.dispose(false));
      const rule = B.createDefaultRuleBlock(40, 40);
      rule.getInputTargetBlock('TIMEFRAME').setFieldValue('PERIOD_H1', 'TIMEFRAME');

      const cmp = buildBlock('comparison_block', { OP: '<' });
      const rsi = buildBlock('indicator_rsi', { PERIOD: 14 });
      const num = buildBlock('math_number', { NUM: 30 });
      cmp.getInput('LEFT').connection.connect(rsi.outputConnection);
      cmp.getInput('RIGHT').connection.connect(num.outputConnection);
      rule.getInput('CONDITION').connection.connect(cmp.outputConnection);

      const act = buildBlock('action_block', { DIRECTION: 'BUY', LOT: 0.1 });
      const sl = buildBlock('risk_value_block', { VALUE: 30, UNIT: 'PIPS' });
      const tp = buildBlock('risk_value_block', { VALUE: 90, UNIT: 'PIPS' });
      act.getInput('SL').connection.connect(sl.outputConnection);
      act.getInput('TP').connection.connect(tp.outputConnection);
      rule.getInput('DO').connection.connect(act.previousConnection);

      [cmp, rsi, num, act, sl, tp].forEach((b) => b.render && b.render());
      rule.render();
      B.markFresh();
      exampleBaseline = fieldSnapshot();
    } finally {
      // Blockly fires trailing move events a moment after a programmatic
      // build; keep ignoring them so they do not read as a visitor edit.
      setTimeout(() => { loading = false; }, 400);
    }
    track('example_loaded', {});
    startTour('example');
  }

  /* ---------- wiring ---------- */
  pop.addEventListener('click', (e) => {
    const t = e.target.closest('button');
    if (!t) return;
    const act = t.dataset.act;
    if (act === 'tour') beginBuild();
    else if (act === 'example') loadExample();
    else if (act === 'next') next();
    else if (act === 'find') nudgeCanvas();
    else if (act === 'skip') dismiss(view === 'welcome' ? 'welcome' : kind);
  });
  // Escape only closes the welcome card. During the tour it is left alone:
  // Blockly uses it to cancel a field edit, and one stray press must not end
  // the tour.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && view === 'welcome') dismiss('welcome');
  });
  window.addEventListener('resize', () => { if (view === 'tour') render(); });

  // Export buttons: on a blank canvas an export can only fail with an error
  // toast, so offer the way forward instead (not while the tour itself is
  // running, where the normal message is more useful). Any real export click
  // ends the tour. Capture phase on the header so it runs first.
  const header = document.querySelector('header');
  if (header) {
    header.addEventListener('click', (e) => {
      const btn = e.target.closest('#exportMt5Btn, #exportMt4Btn, #exportCtraderBtn');
      if (!btn) return;
      if (view !== 'tour' && isBlank()) {
        e.stopImmediatePropagation();
        e.preventDefault();
        track('export_clicked', { pristine: true });
        showWelcome({ source: 'export', note: 'Build a rule first, or load a finished one, and then export it. The tour shows where everything is.' });
        return;
      }
      if (view === 'tour') finish(kind);
    }, true);

    const guideBtn = document.getElementById('openGuideBtn');
    if (guideBtn) guideBtn.addEventListener('click', () => showWelcome({ source: 'header' }));
  }

  /* ---------- first-visit launch ----------
     Opens by itself the first time someone lands on the builder with nothing
     on the canvas. Not over a password-reset or a return from checkout, which
     already have their own screen. */
  function autoLaunchAllowed() {
    if (view || readState().seen) return false;
    if (!isBlank()) return false;
    if (/type=recovery|access_token/.test(location.hash || '')) return false;
    if (/[?&]checkout=/.test(location.search || '')) return false;
    const reset = document.getElementById('authResetOverlay');
    if (reset && !reset.classList.contains('hidden')) return false;
    return true;
  }
  function maybeShowOnFirstRun() {
    if (autoLaunchAllowed()) showWelcome({ source: 'auto' });
  }
  setTimeout(maybeShowOnFirstRun, 450);

  // Hook so tests and future entry points can open the same flows.
  window.apOnboarding = { showWelcome, startTour, beginBuild, loadExample, isPristine, isBlank, stopTour, next };
})();
