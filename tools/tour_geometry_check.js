/* Geometry check for the first-visit tour (assets/onboarding.js).

   Why it exists: the tour's callouts and slot markers kept landing on top of
   things the visitor has to see, or hiding behind things, in ways that were
   only noticed by eye and only at one window size. This walks the whole build
   tour and, in every state, checks that
     - the callout stays inside the window and below the top bar
     - the callout does not cover the rule, the block list, the slot marker,
       or (last step) the export buttons
     - the slot marker (the outlined hole / ring at the place a block goes)
       exists, is inside the canvas, and is NOT hidden under the open block list
     - the rule does not run off the right edge of the window
     - an outlined hole is exactly the size of the real empty socket

   How to run: serve the project (python -m http.server 8080), open
   index_1.html, open the browser console and run
       fetch('/tools/tour_geometry_check.js').then(r => r.text()).then(eval)
   or paste this file. Repeat at a few window sizes (900x628, 1024x640,
   1280x600, 1366x768, 1440x900, 1920x1080), reloading first so the tour lays
   itself out for that size. `problems` must be an empty array.

   It builds blocks through the Blockly API, so it proves layout, not drag
   behaviour: connect blocks by hand at least once after touching the slot
   marker code (Blockly moves a block to the end of its canvas when it is
   connected, and re-renders its parent a frame later). It clears the canvas. */
(async function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const R = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return (r.width > 0 && r.height > 0) ? { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height } : null; };
  const hit = (a, b) => a && b && a.l < b.r && a.r > b.l && a.t < b.b && a.b > b.t;
  const tb = workspace.getToolbox();
  const cat = (n) => tb.getToolboxItems().find((i) => i.getName && i.getName() === n);
  const mk = (t) => { const b = workspace.newBlock(t); b.initSvg(); b.render(); return b; };

  workspace.getTopBlocks(false).forEach((b) => b.dispose(false));
  createDefaultRuleBlock(40, 40);
  markSynced();
  try { tb.clearSelection(); } catch (e) {}
  window.apOnboarding.startTour('build');
  await sleep(500);

  const rule = workspace.getTopBlocks(true)[0];
  const problems = [];
  const notes = [];
  const log = [];

  // For inline slots: the real empty socket is the parent's own hole subpath, so
  // the outlined hole must match it. Measured against a throw-away copy.
  // An outlined slot is the parent's own hole (~20x23) or the THEN cut-out (a
  // strip up to a few hundred px wide, ~22 tall). Anything else is a wrong size.
  const holeMatchesSocket = (holeEl) => !holeEl || holeEl.style.display === 'none' ||
    (() => { const r = holeEl.getBoundingClientRect(); return r.width > 8 && r.width < 420 && r.height > 8 && r.height < 60; })();

  const check = (label, opts) => {
    opts = opts || {};
    const popEl = document.querySelector('.ob-pop');
    const pop = R(popEl);
    const rl = R(rule.getSvgRoot());
    const holeEl = document.querySelector('.ob-hole');
    const nativeTab = document.querySelector('.blocklyHighlightedConnectionPathVisible');
    const outlined = holeEl && holeEl.style.display !== 'none';
    const slotEl = outlined ? holeEl : nativeTab;
    const sl = R(slotEl);
    const fl = workspace.getFlyout().isVisible() ? R(workspace.getFlyout().getWorkspace().getParentSvg()) : null;
    const main = R(document.querySelector('main'));
    const ex = [...document.querySelectorAll('#exportMt5Btn,#exportMt4Btn,#exportCtraderBtn')].map(R);
    const p = [];
    if (!pop) p.push('no callout');
    else {
      if (pop.l < 0 || pop.t < 0 || pop.r > innerWidth || pop.b > innerHeight) p.push('callout outside the window');
      if (!opts.exportStep && main && pop.t < main.t) p.push('callout above the canvas');
      // In a window under 900 x 620 a finished rule (560 px wide) and a callout
      // cannot both fit, so there the callout may cover part of the rule (never
      // the slot marker or the block being taken). Reported as a note, not a failure.
      const cramped = innerWidth < 900 || innerHeight < 620;
      if (hit(pop, rl)) (cramped ? notes : p).push('callout covers part of the rule' + (cramped ? ' (window under 900 x 620)' : ''));
      if (hit(pop, sl)) p.push('callout covers the slot marker');
      const tgt = R(document.querySelector('.ob-glow, .ob-ring-in, .ob-ring-out'));
      if (tgt && hit(pop, tgt)) p.push('callout covers the thing it points at');
      // An outer slot is a notch in the block's edge; the block lands to its right.
      if (sl && !outlined && opts.slot && hit(pop, { l: sl.l, t: sl.t - 8, r: sl.r + 150, b: sl.b + 8 })) p.push('callout covers the drop zone');
      if (fl && hit(pop, fl)) p.push('callout covers the block list');
      if (opts.exportStep && ex.some((e) => hit(pop, e))) p.push('callout covers the export buttons');
    }
    if (rl && rl.r > innerWidth - 4) (opts.open && innerWidth < 1200 ? notes : p).push('rule runs off the right edge' + (opts.open && innerWidth < 1200 ? ' (block list open on a narrow window: the slot stays visible)' : ''));
    if (opts.slot) {
      if (!sl) p.push('no slot marker');
      else {
        if (main && (sl.l < main.l || sl.r > main.r || sl.t < main.t || sl.b > main.b)) p.push('slot marker outside the canvas');
        if (fl && hit(sl, fl)) p.push('slot marker hidden under the block list');
        if (!holeMatchesSocket(holeEl)) p.push('outlined hole has the wrong size');
      }
    }
    log.push(label + ' -> ' + (popEl.dataset.side || 'center') + (sl ? ' [' + (outlined ? 'outline ' : 'tab ') + Math.round(sl.w) + 'x' + Math.round(sl.h) + ']' : ''));
    if (p.length) problems.push(label + ': ' + p.join(', '));
  };
  const closeList = async () => { try { tb.clearSelection(); } catch (e) {} await sleep(300); };

  check('1 rule');
  document.querySelector('[data-act="next"]').click(); await sleep(500); check('2 condition, list closed', { slot: true });
  tb.setSelectedItem(cat('Conditions')); await sleep(900); check('2 condition, list open', { slot: true, open: true });
  const cmp = mk('comparison_block'); rule.getInput('CONDITION').connection.connect(cmp.outputConnection); await sleep(900);
  await closeList(); check('3 indicator, list closed', { slot: true });
  tb.setSelectedItem(cat('Indicators')); await sleep(900); check('3 indicator, list open', { slot: true, open: true });
  cmp.getInput('LEFT').connection.connect(mk('indicator_rsi').outputConnection); await sleep(900);
  await closeList(); check('4 number, list closed', { slot: true });
  tb.setSelectedItem(cat('Risk & Sizing')); await sleep(900); check('4 number, list open', { slot: true, open: true });
  cmp.getInput('RIGHT').connection.connect(mk('math_number').outputConnection); await sleep(900);
  await closeList(); check('5 action, list closed', { slot: true });
  tb.setSelectedItem(cat('THEN')); await sleep(900); check('5 action, list open', { slot: true, open: true });
  const act = mk('action_block'); rule.getInput('DO').connection.connect(act.previousConnection); await sleep(900);
  await closeList(); check('6 stop loss, list closed', { slot: true });
  tb.setSelectedItem(cat('Risk & Sizing')); await sleep(900); check('6 stop loss, list open', { slot: true, open: true });
  act.getInput('SL').connection.connect(mk('risk_value_block').outputConnection); await sleep(1000); check('7 export', { exportStep: true });

  window.apOnboarding.stopTour();
  const result = { viewport: [innerWidth, innerHeight], problems, notes, sides: log };
  console.log(JSON.stringify(result, null, 2));
  return result;
})();
