// Trace command-bar shell: face + status + composer + progress.
// Investigation states, the Ask composer and the results window live here.
// Fixed strings only — the creature himself never generates text.
'use strict';
(function () {
  var sill = document.getElementById('sill');
  var wrap = document.getElementById('hearth-wrap');
  var leafEl = document.getElementById('leaf');
  var leafBodyEl = document.getElementById('leaf-body');
  var subEl = document.getElementById('leaf-sub');
  var H = window.TraceFace || null;
  var panel = null;

  var SHADOW_PAD = 28;

  var Sh = {
    open: false,          // panel unfolded?
    chat: false,          // chat surface up (workspace hidden while true)?
    glow: '',             // leaf-glow class
    lastMood: 'idle',
    _settle: null,
    _note: '',            // sticky status line, restored after transient notes
    _noteTimer: null,
  };

  function P() {
    if (!panel) panel = window.TracePanel;
    return panel;
  }
  function concise(s, max) {
    s = String(s || ''); max = max || 90;
    return s.length <= max ? s : s.slice(0, max - 1).trimEnd() + '…';
  }
  function currentMode() {
    try {
      var sel = document.querySelector('#ask-mode input[name="investigation_type"]:checked');
      if (sel && (sel.value === 'debug' || sel.value === 'explain')) return sel.value;
    } catch (e) {}
    return 'debug';
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
  // Explicit final sizes: the panel widens (main re-perches the window at
  // every size, so it stays in the middle), height follows content.
  var OPEN_W = 632;   // 560 panel (#leaf.open) + 44 sill pads + 28 shadow pad
  var CLOSED_W = 408; // 336 panel + 44 sill pads + 28 shadow pad
  function requestFitSettled() {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(requestFit, 380);
  }

  /* ---- transient status line (replaces ambient chatter) ---- */
  function note(line, sticky) {
    if (!subEl) return;
    if (sticky) Sh._note = line;
    subEl.textContent = concise(line, 90);
    clearTimeout(Sh._noteTimer);
    if (!sticky) {
      Sh._noteTimer = setTimeout(function () {
        if (subEl) subEl.textContent = concise(Sh._note || 'ready to debug', 90);
      }, 3600);
    }
  }

  /* ---- central state: creature + head never disagree ---- */
  function say(creature, title, sub, glow) {
    Sh.lastMood = creature;
    Sh.glow = glow || '';
    if (H) { try { H.setState(creature); } catch (e) {} }
    var p = P();
    if (p) { try { p.setHead(title, Sh.glow, sub); } catch (e) {} }
    note(sub, true);
  }

  function clearSettle() {
    clearTimeout(api._settle);
    api._settle = null;
  }

  function syncFaceOpen() {
    if (!wrap) return;
    try {
      wrap.setAttribute('aria-expanded', Sh.open ? 'true' : 'false');
      wrap.setAttribute('aria-label', Sh.open ? 'Trace — workspace open' : 'Trace — open workspace');
    } catch (e) {}
  }

  /* ---- chat surface: inside his window, never a second window ---- */
  function setChatShown(on) {
    Sh.chat = !!on;
    if (leafEl) leafEl.classList.toggle('chat-open', !!on);
    var c = document.getElementById('chat');
    if (c) {
      c.hidden = !on;
      c.setAttribute('aria-hidden', on ? 'false' : 'true');
    }
  }
  function chatBubble(kind, text) {
    var log = document.getElementById('chat-log');
    if (!log) return;
    var m = document.createElement('div');
    m.className = 'msg ' + kind;
    var who = document.createElement('span');
    who.className = 'who';
    who.textContent = kind === 'me' ? 'you' : (kind === 'err' ? 'trace' : 'trace');
    var says = document.createElement('span');
    says.className = 'say';
    says.textContent = String(text == null ? '' : text).slice(0, 4000);
    m.appendChild(who); m.appendChild(says);
    log.appendChild(m);
    scrollChat();
  }
  function scrollChat() {
    var s = document.getElementById('chat-scroll');
    if (s) s.scrollTop = s.scrollHeight;
  }
  function chatHistory() {
    var log = document.getElementById('chat-log');
    if (!log) return [];
    var rows = Array.prototype.slice.call(log.children).filter(function (n) {
      return n && n.classList &&
        (n.classList.contains('trace') || n.classList.contains('me'));
    });
    return rows.slice(-10).map(function (n) {
      var says = n.querySelector('.say');
      return {
        role: n.classList.contains('me') ? 'user' : 'assistant',
        content: says ? String(says.textContent || '') : '',
      };
    });
  }
  function setChatMode(on) {
    setChatShown(!!on);
    if (on) {
      var log = document.getElementById('chat-log');
      if (log && !log.children.length) {
        chatBubble('trace', 'here. what are we looking at?');
      }
      if (!Sh.open) api.expand();
      else requestFit();
      requestFitSettled();
      var inp = document.getElementById('chat-input');
      if (inp) {
        requestAnimationFrame(function () {
          try { inp.focus({ preventScroll: true }); } catch (e) {}
        });
      }
    } else {
      requestFit();
    }
  }

  function setSettingsShown(on) {
    var s = document.getElementById('provider-settings');
    if (!s) return;
    s.hidden = !on;
    s.setAttribute('aria-hidden', on ? 'false' : 'true');
    var b = document.getElementById('leaf-settings');
    if (b) b.setAttribute('aria-pressed', on ? 'true' : 'false');
    if (on) {
      setChatShown(false);
      if (!Sh.open) api.expand();
      var bridge = window.trace;
      testBadge(null); // stale results must not pose as a live verdict
      if (bridge && bridge.getProviderConfig) bridge.getProviderConfig().then(function (c) {
        var kind = document.getElementById('provider-kind');
        var model = document.getElementById('provider-model');
        var base = document.getElementById('provider-base');
        if (kind) kind.value = c.provider === 'openai-compatible' ? 'openai-compatible' : 'ollama';
        if (model) model.value = String(c.ollama_model || '');
        if (base) base.value = String(c.api_base || '');
        providerKindChanged();
        providerStatus(c.has_api_key ? 'key saved' : 'not connected');
      }).catch(function () {});
      requestFitSettled();
    } else requestFit();
  }
  function providerStatus(text) {
    var el = document.getElementById('provider-status');
    if (el) el.textContent = text;
  }
  function providerKindChanged() {
    var kind = document.getElementById('provider-kind');
    var row = document.getElementById('provider-base-row');
    if (row) row.hidden = !kind || kind.value !== 'openai-compatible';
    testBadge(null); // a provider switch invalidates any probe result
  }
  /* ---- connection test: probe the typed-in provider before saving ---- */
  function testBadge(state, text) {
    var el = document.getElementById('provider-test-result');
    if (!el) return;
    if (!state) { el.hidden = true; el.textContent = ''; el.className = ''; return; }
    el.hidden = false;
    el.textContent = text || '';
    el.className = 'test ' + state; // ok | fail | testing (spinner is CSS)
  }
  function testProvider() {
    var kind = document.getElementById('provider-kind');
    var model = document.getElementById('provider-model');
    var base = document.getElementById('provider-base');
    var key = document.getElementById('provider-key');
    var btn = document.getElementById('provider-test');
    if (!kind || !model || !window.trace || !window.trace.testProviderConnection) return;
    if (!String(model.value || '').trim()) { testBadge('fail', '✗ model required'); return; }
    if (btn) btn.disabled = true;
    testBadge('testing', 'testing connection…');
    window.trace.testProviderConnection({
      provider: kind.value, model: model.value,
      api_base: base ? base.value : '', api_key: key ? key.value : ''
    }).then(function (res) {
      if (res && res.ok) {
        testBadge('ok', '✓ Connected (' + Number(res.latency_ms || 0) + 'ms)');
        providerStatus('ready to test again');
      } else {
        testBadge('fail', '✗ ' + ((res && res.error) || 'connection failed'));
        providerStatus('not connected');
      }
    }).catch(function () {
      testBadge('fail', '✗ connection failed');
      providerStatus('not connected');
    }).finally(function () { if (btn) btn.disabled = false; });
  }
  function saveProvider() {
    var kind = document.getElementById('provider-kind');
    var model = document.getElementById('provider-model');
    var base = document.getElementById('provider-base');
    var key = document.getElementById('provider-key');
    var btn = document.getElementById('provider-save');
    if (!kind || !model || !window.trace || !window.trace.configureProvider) return;
    if (!String(model.value || '').trim()) { providerStatus('model required'); return; }
    if (btn) btn.disabled = true;
    providerStatus('connecting…');
    window.trace.configureProvider({ provider: kind.value, model: model.value, api_base: base ? base.value : '', api_key: key ? key.value : '' })
      .then(function (res) {
        if (!res || res.ok === false) { providerStatus('could not connect'); if (res && res.error) note(res.error, false); return; }
        providerStatus('ready');
        if (key) key.value = '';
        note('model connection ready', true);
      }).catch(function () { providerStatus('could not connect'); })
      .finally(function () { if (btn) btn.disabled = false; });
  }

  function expand() {
    if (!Sh.open) {
      Sh.open = true;
      var h0 = 0;
      try { h0 = Math.ceil(sill.getBoundingClientRect().height + SHADOW_PAD); } catch (e0) {}
      if (leafEl) { leafEl.classList.add('open'); }
      if (leafBodyEl) { leafBodyEl.setAttribute('aria-hidden', 'false'); }
      syncFaceOpen();
      if (H) { try { H.wake(); } catch (e) {} }
      try { if (window.trace && window.trace.resize) window.trace.resize(OPEN_W, h0 || 198); } catch (e2) {}
      var ask = document.getElementById('ask-text');
      if (ask) { try { ask.focus({ preventScroll: true }); } catch (e3) {} }
      requestFitSettled();
    }
  }

  function collapse(fromKbd) {
    if (Sh.open) {
      Sh.open = false;
      setChatShown(false);
      setSettingsShown(false);
      if (leafEl) { leafEl.classList.remove('open'); }
      if (leafBodyEl) { leafBodyEl.setAttribute('aria-hidden', 'true'); }
      syncFaceOpen();
      // Esc is a keyboard close: hand focus back to the face so the next
      // Tab keypress re-enters from the top instead of vanishing off-window.
      if (fromKbd && wrap && leafEl &&
          leafEl.contains(document.activeElement) && document.activeElement !== wrap) {
        try { wrap.focus({ preventScroll: true }); } catch (e0) {}
      }
      try {
        var r = sill.getBoundingClientRect(); // reflowed: workspace hidden again
        if (window.trace && window.trace.resize) window.trace.resize(CLOSED_W, Math.ceil(r.height + SHADOW_PAD));
      } catch (e) {}
    }
  }

  var api = {
    idle(msg) {
      // A success/error performance owns a few seconds of stage: stray
      // idle calls must not talk over it.
      if (api._settle) return;
      say('idle', 'Trace', msg || 'ready to debug', '');
    },
    working(title, msg, expression) {
      clearSettle();
      say(expression || 'working', concise(title || 'Trace', 28), msg || 'checking your files…', 'working');
    },
    done(title, msg, findingCount, expression) {
      clearSettle();
      say(expression || 'success', concise(title || 'Done', 28), msg || 'all set.', 'done');
      api._settle = setTimeout(function () {
        api._settle = null;
        if (!Sh.open) api.idle();
      }, 6000);
    },
    error(title, msg) {
      clearSettle();
      say('error', concise(title || 'Hmm', 28), msg || 'that one snagged.', 'error');
      api._settle = setTimeout(function () {
        api._settle = null;
        if (!Sh.open) api.idle();
      }, 6000);
    },
    warning(title, msg) {
      clearSettle();
      say('warning', concise(title || 'Heads up', 28), msg || 'there is something to check.', 'warning');
    },
    backendUnavailable(msg) {
      clearSettle();
      say('backend_unavailable', 'Hmm', msg || 'backend unavailable — could not start it.', 'error');
    },
    cancelled(msg) {
      clearSettle();
      say('cancelled', 'Stopped', msg || 'that one was stopped.', '');
      api._settle = setTimeout(function () {
        api._settle = null;
        if (!Sh.open) api.idle();
      }, 3000);
    },
    toggle() {
      if (Sh.open) api.collapse();
      else api.expand();
    },
    expand: expand,
    collapse: collapse,
    _settle: null,
  };
  window.TraceShell = api;

  /* ---- creature wiring ---- */
  var cv = document.getElementById('hearth');
  if (H && cv) {
    try { H.attach(cv); } catch (e) {}
    if (typeof H.armSleep === 'function') H.armSleep(60000);
  }

  // Eyes track the cursor — the only ambient motion, and it means something.
  window.addEventListener('mousemove', function (e) {
    if (!H || !wrap || typeof H.setCursor !== 'function') return;
    try {
      var r = wrap.getBoundingClientRect();
      var bx = r.left + r.width / 2, by = r.top + r.height / 2;
      H.setCursor((e.clientX - bx) / 220, (e.clientY - by) / 180);
    } catch (err) {}
  }, { passive: true });

  wrap.addEventListener('mousedown', function () {
    if (H) { try { H.poke(0, 0.2); } catch (e) {} }
  });
  wrap.addEventListener('click', function (e) {
    if (e.detail > 1) return;
    if (!Sh.open) api.expand(); // pressing the face opens the workspace
  });
  // Keyboard parity for the face: it is role="button", and since it is a div
  // (not a real <button>) Enter/Space never become clicks on their own.
  wrap.addEventListener('keydown', function (e) {
    if ((e.key === 'Enter' || e.key === ' ') && !Sh.open) {
      e.preventDefault();
      api.expand();
    }
  });
  // The whole collapsed shell opens the workspace: face, title, anywhere on
  // the card. Interactive children (buttons, input) are excluded so typing
  // and controls never misfire; the guard below makes background clicks
  // inert once open, so only fold / Esc collapse.
  if (leafEl) leafEl.addEventListener('click', function (e) {
    if (Sh.open) return;
    if (e.target && e.target.closest && e.target.closest('button, textarea, input, a')) return;
    api.expand();
  });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && H) { try { H.wake(); } catch (e) {} }
  });

  /* ---- composer + controls ---- */
  function wire(id, fn) {
    var b = document.getElementById(id);
    if (b) b.addEventListener('click', function (e) { e.stopPropagation(); fn(); });
  }
  function askText() {
    var t = document.getElementById('ask-text');
    return t ? t.value : '';
  }
  function setAskEnabled(on) {
    var s = document.getElementById('ask-send');
    var c = document.getElementById('ctl-cancel');
    var t = document.getElementById('talk-text');
    var ci = document.getElementById('chat-input');
    var cs = document.getElementById('chat-send');
    if (s) s.disabled = !on;
    if (c) c.disabled = on;
    if (t) t.disabled = !on;
    if (ci) ci.disabled = !on;
    if (cs) cs.disabled = !on;
  }
  // Mode toggle: reflect it in the ambient line so the creature never
  // pretends to debug when the user asked for an explanation (and vice versa).
  try {
    var modeRadios = document.querySelectorAll('#ask-mode input[name="investigation_type"]');
    for (var m = 0; m < modeRadios.length; m++) {
      modeRadios[m].addEventListener('change', function () {
        var mode = currentMode();
        note(mode === 'explain' ? 'ready to explain' : 'ready to debug', true);
      });
    }
  } catch (e) {}
  wire('leaf-fold', function () { api.collapse(); });
  wire('leaf-chat', function () { setChatMode(!Sh.chat); });
  wire('leaf-settings', function () {
    var s = document.getElementById('provider-settings');
    setSettingsShown(!s || s.hidden);
  });
  wire('provider-save', saveProvider);
  wire('provider-test', testProvider);
  var providerKind = document.getElementById('provider-kind');
  if (providerKind) providerKind.addEventListener('change', providerKindChanged);
  wire('chat-send', function () { chatSend(); });
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
    var opts = { autoFix: autoFix, investigation_type: currentMode() };
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
    if (!text) { note('describe the bug first, then Investigate', false); return; }
    try {
      doSubmit(text).then(function (res) {
          if (res && res.ok === false && res.reason === 'busy') note('one at a time — cancel first', false);
          else if (res && res.ok === false) note('could not start that one', false);
          else {
            var t = document.getElementById('ask-text');
            if (t) t.value = '';
          }
        }).catch(function () { note('could not start that one', false); });
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
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        var btn = document.getElementById('ask-send');
        if (btn) btn.click();
      }
      e.stopPropagation();
    });
    askBox.addEventListener('focus', function () {
      if (H) { try { H.wake(); } catch (e) {} }
    });
  }
  // Talk stays in the normal form: Enter here must not expand or fold the
  // workspace, just send through the driver (follow-up if a run exists, an
  // explain ask otherwise).
  var talkBox = document.getElementById('talk-text');
  if (talkBox) {
    talkBox.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        var text = (talkBox.value || '').trim();
        if (!text) return;
        var d = driver();
        if (!d || typeof d.talk !== 'function') { note('could not start that one', false); return; }
        d.talk(text).then(function (res) {
          if (res && res.ok === false && res.reason === 'busy') note('one at a time — cancel first', false);
          else if (res && res.ok === false) note('could not start that one', false);
          else talkBox.value = '';
        }).catch(function () { note('could not start that one', false); });
      }
      e.stopPropagation();
    });
    talkBox.addEventListener('focus', function () {
      if (H) { try { H.wake(); } catch (e) {} }
    });
  }
  // Chat composer: Enter sends, nothing folds, the click guard for
  // interactive children keeps the leaf-level expansion handler off it.
  var chatInput = document.getElementById('chat-input');
  if (chatInput) {
    chatInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); chatSend(); }
      e.stopPropagation();
    });
    chatInput.addEventListener('focus', function () {
      if (H) { try { H.wake(); } catch (e) {} }
    });
  }
  function chatSend() {
    var inp = document.getElementById('chat-input');
    if (!inp) return;
    var text = (inp.value || '').trim();
    if (!text) return;
    var d = driver();
    if (!d || typeof d.chat !== 'function') {
      note('could not start that one', false);
      return;
    }
    chatBubble('me', text);
    inp.value = '';
    d.chat(text, chatHistory()).then(function (res) {
      if (res && res.ok === false) {
        chatBubble('err', res.reason === 'busy'
          ? 'one thing at a time — cancel or wait first.'
          : (res.error || 'could not talk just now.'));
      } else {
        chatBubble('trace', (res && res.reply) || '');
      }
    }).catch(function () { chatBubble('err', 'could not talk just now.'); });
  }

  window.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { api.collapse(true); }
  });

  // boot: settle the window to the perch and greet.
  say('idle', 'Trace', 'ready to debug', '');
  requestFit();
  setTimeout(function () {
    if (H) { try { H.greet(); } catch (e) {} }
  }, 700);

  window.TraceShellCtl = { setAskEnabled: setAskEnabled };
})();
