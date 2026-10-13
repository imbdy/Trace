// Trace's shell: three modes, one creature. Transplant of Neo's shell.js —
// the modes, murmur discipline, poke arc, tucking, hover intent and debug
// keys are Neo's, kept intact. What changed: Neoma-watching (brief, live
// sessions, approvals, core hooks) is out; investigation states, the Ask
// composer and the results window are in. Fixed strings only — the creature
// himself never generates text.
'use strict';
(function () {
  var sill = document.getElementById('sill');
  var wrap = document.getElementById('hearth-wrap');
  var murmurEl = document.getElementById('murmur');
  var badgeEl = document.getElementById('badge');
  var leafEl = document.getElementById('leaf');
  var leafBodyEl = document.getElementById('leaf-body');
  var debug = document.getElementById('debug');
  var fpsEl = document.getElementById('fps');
  var moodEl = document.getElementById('mood');
  var stateEl = document.getElementById('state');
  var H = window.TraceFace || null;
  var panel = null;

  var SHADOW_PAD = 28;
  var AWAY_MS = 5 * 60 * 1000;
  var MURMUR_GAP_MS = 30 * 1000;   // ambient remarks: rare
  var CLICK_MURMUR_GAP_MS = 6 * 1000; // click answers: prompt but not chatty
  var HOVER_INTENT_MS = 120;       // deliberate hover, not pass-through

  var Sh = {
    open: false,          // leaf unfolded?
    glow: '',              // leaf-glow class
    lastMood: 'idle',
    lastInput: Date.now(),
    lastMurmur: 0,
    lastClickMurmur: 0,
    _settle: null,
  };

  function P() {
    if (!panel) panel = window.TracePanel;
    return panel;
  }
  function concise(s, max) {
    s = String(s || ''); max = max || 90;
    return s.length <= max ? s : s.slice(0, max - 1).trimEnd() + '…';
  }

  /* ---- window geometry: hug the visible sill ---- */
  function requestFit() {
    try {
      if (!(window.trace && window.trace.resize)) return;
      var r = sill.getBoundingClientRect();
      window.trace.resize(Math.ceil(r.width + SHADOW_PAD), Math.ceil(r.height + SHADOW_PAD));
    } catch (e) {}
  }
  var fitTimer = null;
  // Explicit final sizes: the leaf widens (main re-perches the window at
  // every size, so he stays in the middle), height pinned to the collapsed
  // height so expansion never grows downward.
  var OPEN_W = 732;   // 660 leaf + 44 sill pads + 28 shadow pad
  var CLOSED_W = 408; // 336 leaf + 44 sill pads + 28 shadow pad
  function requestFitSettled() {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(requestFit, 380);
  }

  /* ---- murmur: one short line, rarely ---- */
  var murmurTimer = null;
  function murmur(line, force) {
    if (!murmurEl || !line) return;
    var now = Date.now();
    if (!force && now - Sh.lastMurmur < MURMUR_GAP_MS) return;
    Sh.lastMurmur = now;
    murmurEl.textContent = line;
    murmurEl.classList.add('show');
    clearTimeout(murmurTimer);
    murmurTimer = setTimeout(function () { murmurEl.classList.remove('show'); }, 3600);
  }

  // Poke answers, keyed by the creature's arc: startle → annoyed → dizzy.
  var POKE_LINES = {
    startle: ['oh?', 'hm?'],
    annoyed: ['hey.', 'careful.'],
    dizzy: ['okay, okay…'],
  };
  var pokeIx = { startle: 0, annoyed: 0, dizzy: 0 };
  function murmurSoon(line) {
    if (!murmurEl || !line) return;
    var now = Date.now();
    if (now - Sh.lastClickMurmur < CLICK_MURMUR_GAP_MS) return;
    Sh.lastClickMurmur = now;
    Sh.lastMurmur = now; // a click answer counts as his voice for a while
    murmurEl.textContent = line;
    murmurEl.classList.add('show');
    clearTimeout(murmurTimer);
    murmurTimer = setTimeout(function () { murmurEl.classList.remove('show'); }, 3600);
  }
  function clickReact() {
    if (!H || typeof H.pokeReact !== 'function') return;
    untuck();
    var kind;
    try { kind = H.pokeReact(); } catch (e) { return; }
    if (kind === 'busy') return; // success/error owns the stage right now
    var lines = POKE_LINES[kind] || [];
    if (lines.length) {
      var i = pokeIx[kind] || 0;
      pokeIx[kind] = i + 1;
      murmurSoon(lines[i % lines.length]);
    }
    if (kind === 'dizzy') {
      // He overdid it: a beat, then back to whatever is actually true.
      clearTimeout(clickReact._dizzy);
      clickReact._dizzy = setTimeout(function () { if (!Sh.open) api.idle(); }, 2400);
    }
  }

  function setBadge(n) {
    if (!badgeEl) return;
    if (n > 0) {
      badgeEl.textContent = n > 9 ? '9+' : String(n);
      badgeEl.hidden = false;
    } else {
      badgeEl.hidden = true;
    }
  }

  /* ---- tucked / perched presence ----
   * Tucked = shy: he sinks into the box until only the eyes peek over the
   * canvas bottom. Only ever while truly idle, leaf closed, nothing pending
   * — any input, state change, or news untucks him. The window never moves;
   * the canvas clips, so no transparent sprawl and no native resize jank. */
  var TUCK_AFTER_MS = 45 * 1000;
  function setTucked(on) {
    if (Sh.tucked === on) return;
    Sh.tucked = on;
    if (H) { try { H.setPeek(on ? 1 : 0); } catch (e) {} }
  }
  function untuck() { setTucked(false); Sh.lastInput = Date.now(); }
  setInterval(function () {
    if (Sh.tucked || Sh.open) return;
    if (!H || H.state !== 'idle') return;
    if (Date.now() - Sh.lastInput > TUCK_AFTER_MS) setTucked(true);
  }, 5000);

  /* ---- central state: creature + leaf head never disagree ---- */
  function say(creature, title, sub, glow) {
    Sh.lastMood = creature;
    Sh.glow = glow || '';
    if (creature !== 'idle') untuck();
    if (H) { try { H.setState(creature); } catch (e) {} }
    var p = P();
    if (p) { try { p.setHead(title, Sh.glow, sub); } catch (e) {} }
    if (moodEl) moodEl.textContent = creature;
    if (stateEl) stateEl.textContent = (Sh.open ? 'leaf:' : 'sill:') + creature;
  }

  function refreshSubForPause() {
    // Annotate the leaf sub-line while hushed / off.
    try {
      if (window.trace && window.trace.settings) {
        window.trace.settings().then(function (s) {
          var p = P();
          if (!p || !s) return;
          var extra = '';
          if (s.disabled) extra = ' | Trace is off (tray turns him on)';
          else if (s.paused) extra = ' | hushed (' + (s.label || 'paused') + ')';
          else if (s.quiet) extra = ' | quiet hours';
          if (extra) p.setHead(undefined, Sh.glow, p.sub() + extra);
        }).catch(function () {});
      }
    } catch (e) {}
  }

  function clearSettle() {
    clearTimeout(api._settle);
    api._settle = null;
  }

  var api = {
    idle(msg) {
      // A success/error performance owns a few seconds of stage: stray
      // idle calls must not talk over it.
      if (api._settle) return;
      say('idle', 'Trace', msg || 'ready to debug', '');
      setBadge(0);
    },
    listening(msg) {
      say('listening', 'Trace', msg || 'go on — I’m listening', '');
    },
    thinking(msg) {
      say('thinking', 'Trace', msg || 'let me think…', '');
    },
    working(title, msg, expression) {
      clearSettle();
      say(expression || 'working', concise(title || 'Trace', 28), msg || 'checking your files…', 'working');
    },
    done(title, msg, findingCount, expression) {
      clearSettle();
      say(expression || 'success', concise(title || 'Done', 28), msg || 'all set.', 'done');
      murmur('all set.');
      setBadge(typeof findingCount === 'number' ? findingCount : 0);
      api._settle = setTimeout(function () {
        api._settle = null;
        if (!Sh.open) api.idle();
      }, 6000);
    },
    error(title, msg) {
      clearSettle();
      say('error', concise(title || 'Hmm', 28), msg || 'that one snagged.', 'error');
      murmur('hmm. that one snagged.');
      api._settle = setTimeout(function () {
        api._settle = null;
        if (!Sh.open) api.idle();
      }, 6000);
    },
    warning(title, msg) {
      clearSettle();
      say('warning', concise(title || 'Heads up', 28), msg || 'there is something to check.', 'warning');
      murmur(msg || 'there is something to check.');
    },
    backendUnavailable(msg) {
      clearSettle();
      say('backend_unavailable', 'Hmm', msg || 'backend unavailable — could not start it.', 'error');
      murmur('the backend is unavailable.');
    },
    cancelled(msg) {
      clearSettle();
      say('cancelled', 'Stopped', msg || 'that one was stopped.', '');
      murmur(msg || 'that one was stopped.');
      api._settle = setTimeout(function () {
        api._settle = null;
        if (!Sh.open) api.idle();
      }, 3000);
    },
    attention(title, msg) {
      // Results are in: stand at attention, murmur once. The results window
      // itself opens from main — the leaf never forces itself open.
      clearSettle();
      say('listening', concise(title || 'Results are in', 28), msg || 'psst — have a look.', 'attention');
      murmur('psst — results are in.', true);
    },
    badge(n) { setBadge(n); },
    toggle() {
      if (Sh.open) api.collapse();
      else api.expand();
    },
    expand() {
      if (!Sh.open) {
        untuck();
        Sh.open = true;
        var h0 = 0;
        try { h0 = Math.ceil(sill.getBoundingClientRect().height + SHADOW_PAD); } catch (e0) {}
        if (leafEl) { leafEl.classList.add('open'); }
        if (leafBodyEl) { leafBodyEl.setAttribute('aria-hidden', 'false'); }
        if (H) { try { H.wake(); } catch (e) {} }
        try {
          if (window.trace && window.trace.settings) {
            window.trace.settings().then(function (s) {
              if (s && (s.paused || s.quiet || s.disabled)) refreshSubForPause();
            }).catch(function () {});
          }
        } catch (e) {}
        try { if (window.trace && window.trace.resize) window.trace.resize(OPEN_W, h0 || 198); } catch (e2) {}
        var ask = document.getElementById('ask-text');
        if (ask) { try { ask.focus({ preventScroll: true }); } catch (e3) {} }
        requestFitSettled();
      }
    },
    collapse() {
      if (Sh.open) {
        Sh.open = false;
        if (leafEl) { leafEl.classList.remove('open'); }
        if (leafBodyEl) { leafBodyEl.setAttribute('aria-hidden', 'true'); }
        try {
          var r = sill.getBoundingClientRect(); // reflowed: notes hidden again
          if (window.trace && window.trace.resize) window.trace.resize(CLOSED_W, Math.ceil(r.height + SHADOW_PAD));
        } catch (e) {}
      }
    },
    _settle: null,
  };
  window.TraceShell = api;

  function markInput() { Sh.lastInput = Date.now(); }
  function awayLongEnough() { return Date.now() - Sh.lastInput > AWAY_MS; }

  /* ---- creature wiring ---- */
  var cv = document.getElementById('hearth');
  if (H && cv) {
    try { H.attach(cv); } catch (e) {}
    if (typeof H.armSleep === 'function') H.armSleep(60000);
  }

  function gazeFromEvent(e) {
    markInput();
    if (!H || !wrap) return;
    try {
      var r = wrap.getBoundingClientRect();
      var bx = r.left + r.width / 2, by = r.top + r.height / 2;
      H.setCursor((e.clientX - bx) / 220, (e.clientY - by) / 180);
    } catch (err) {}
  }
  window.addEventListener('mousemove', gazeFromEvent, { passive: true });

  // Hover with intent: a deliberate approach perks him up; a pass-through
  // does nothing. Leaving always settles after a short grace.
  var hoverT = null;
  wrap.addEventListener('mouseenter', function () {
    markInput();
    clearTimeout(hoverT);
    hoverT = setTimeout(function () {
      if (H) { try { H.setHover(true); } catch (e) {} }
    }, HOVER_INTENT_MS);
  });
  wrap.addEventListener('mouseleave', function () {
    // Nothing to undo: the hover grow settles back to 1 on its own tween.
    clearTimeout(hoverT);
  });

  // Clicking HIM is a conversation, not a control: a small physical reaction
  // cycling nod → curious → playful, each with a real visual outcome. The
  // leaf is only ever opened deliberately (tab, fold button, esc to close).
  // Double-click opens the results window.
  wrap.addEventListener('mousedown', function () {
    markInput();
    if (H) { try { H.poke(0, 0.2); } catch (e) {} }
  });
  wrap.addEventListener('click', function (e) {
    markInput();
    if (e.detail > 1) return; // double-click handled below
    if (!Sh.open) api.expand(); // pressing him opens the chat beside him
    clickReact();
  });
  wrap.addEventListener('dblclick', function (e) {
    markInput();
    e.preventDefault();
    try { if (window.trace && window.trace.showResults) window.trace.showResults(); } catch (err) {}
  });
  // The whole collapsed shell opens the chat: face, title, anywhere on the
  // card. Interactive children (buttons, input) are excluded so typing and
  // controls never misfire; the guard below makes background clicks inert
  // once open, so only fold / Esc collapse.
  if (leafEl) leafEl.addEventListener('click', function (e) {
    if (Sh.open) return;
    if (e.target && e.target.closest && e.target.closest('button, textarea, input, a')) return;
    markInput();
    api.expand();
  });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) {
      markInput();
      if (H) { try { H.wake(); } catch (e) {} }
    }
  });
  window.addEventListener('focus', function () {
    markInput();
    if (awayLongEnough() && H) { try { H.wake(); } catch (e) {} }
  });

  /* ---- composer + controls ---- */
  function wire(id, fn) {
    var b = document.getElementById(id);
    if (b) b.addEventListener('click', function (e) { e.stopPropagation(); markInput(); fn(); });
  }
  function askText() {
    var t = document.getElementById('ask-text');
    return t ? t.value : '';
  }
  function setAskEnabled(on) {
    var s = document.getElementById('ask-send');
    var c = document.getElementById('ctl-cancel');
    if (s) s.disabled = !on;
    if (c) c.disabled = on;
  }
  wire('leaf-fold', function () { api.collapse(); });
  // Submit/cancel go through TraceDriver (trace.js), never straight to the
  // bridge: the driver owns `running`, which is what enables Cancel while an
  // investigation is in flight. Bypassing it left ctl-cancel permanently
  // disabled, so a live run could not be stopped from the UI.
  function driver() { return window.TraceDriver || null; }
  function doSubmit(text) {
    var d = driver();
    var autoFix = false;
    try {
      var box = document.getElementById('ask-fix');
      autoFix = !!(box && box.checked);
    } catch (e) {}
    var opts = autoFix ? { autoFix: true } : {};
    if (d && typeof d.submit === 'function') return d.submit(text, opts);
    if (window.trace && window.trace.submit) return window.trace.submit(text, opts);
    return Promise.resolve({ ok: false, reason: 'no-bridge' });
  }
  function doCancel() {
    var d = driver();
    if (d && typeof d.cancel === 'function') return d.cancel();
    if (window.trace && window.trace.cancel) return window.trace.cancel();
    return Promise.resolve({ ok: false });
  }
  wire('ask-send', function () {
    var text = (askText() || '').trim();
    if (!text) { murmur('ask me something first.', true); return; }
    try {
      doSubmit(text).then(function (res) {
          if (res && res.ok === false && res.reason === 'busy') murmur('one at a time — cancel first.', true);
          else if (res && res.ok === false) murmur('could not start that one.', true);
          else {
            var t = document.getElementById('ask-text');
            if (t) t.value = '';
          }
        }).catch(function () { murmur('could not start that one.', true); });
    } catch (e) {}
  });
  wire('ctl-cancel', function () {
    try { doCancel(); } catch (e) {}
  });
  wire('ctl-results', function () {
    try { if (window.trace && window.trace.showResults) window.trace.showResults(); } catch (e) {}
  });
  wire('ctl-hide', function () {
    try { if (window.trace && window.trace.hide) window.trace.hide(); } catch (e) {}
  });
  var askBox = document.getElementById('ask-text');
  if (askBox) {
    askBox.addEventListener('keydown', function (e) {
      markInput();
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        var btn = document.getElementById('ask-send');
        if (btn) btn.click();
      }
      e.stopPropagation();
    });
    askBox.addEventListener('focus', function () {
      markInput();
      if (H) { try { H.wake(); } catch (e) {} }
    });
  }

  /* ---- diagnostics keys (only while this window is focused) ---- */
  window.addEventListener('keydown', function (e) {
    markInput();
    if (e.key === 'F3') {
      e.preventDefault();
      if (debug.hidden) debug.hidden = false; else debug.hidden = true;
      return;
    }
    if (e.key === 'Escape') { api.collapse(); return; }
    if (!document.hasFocus()) return;
    if (e.key === '1') api.idle();
    else if (e.key === '2') { if (H) { try { H.blink(); } catch (e2) {} } }
    else if (e.key === '3') api.listening('typing…');
    else if (e.key === '4') api.thinking();
    else if (e.key === '5') api.working('Working…', 'checking your files…');
    else if (e.key === '6') api.done('Done', 'all set.');
    else if (e.key === '7') api.error('Hmm', 'something snagged.');
    else if (e.key === '8') { say('sleeping', 'Trace', 'dozing…', 'sleeping'); }
    else if (e.key === '0') { if (H && typeof H.testSequence === 'function') H.testSequence(function () {}); }
  });

  setInterval(function () {
    if (!debug || debug.hidden) return;
    if (fpsEl && H) fpsEl.textContent = H.fps + ' fps';
    if (stateEl && H) stateEl.textContent = (Sh.open ? 'leaf:' : 'sill:') + H.state;
  }, 500);

  // boot: settle the window to the perch, greet like he means it.
  say('idle', 'Trace', 'ready to debug', '');
  requestFit();
  setTimeout(function () {
    if (H) { try { H.greet(); } catch (e) {} }
    murmur('here. ready.', true);
  }, 700);

  window.TraceShellCtl = { setAskEnabled: setAskEnabled };
})();
