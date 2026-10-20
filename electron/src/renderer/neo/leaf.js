// The paper leaf Trace holds up: live investigation progress + the verdict.
//
// Transplant of Neo's leaf.js: the stitch row, rolling log, verdict block and
// head mechanics are Neo's, kept intact. What changed: the stitch tracks a
// Trace investigation (capture·inspect·analyze·verify·report) lit only by
// real backend SSE stages, and the verdict block opens Trace's results window
// instead of a Neoma approval. No brief, no sessions — Trace investigates.
//
// Fed by trace.js via window.TracePanel.* and by shell.js for chrome.
'use strict';
(function () {
  var el = {
    leaf: document.getElementById('leaf'),
    title: document.getElementById('leaf-title'),
    glow: document.getElementById('leaf-glow'),
    sub: document.getElementById('leaf-sub'),
    live: document.getElementById('live'),
    liveTitle: document.getElementById('live-title'),
    livePhase: document.getElementById('live-phase'),
    stitch: document.getElementById('stitch'),
    log: document.getElementById('live-log'),
    verdict: document.getElementById('verdict'),
    verdictLine: document.getElementById('verdict-line'),
    open: document.getElementById('verdict-open'),
  };

  // Backend SSE stage -> stitch step. Every backend stage is covered; the
  // row lights only for stages actually received, never simulated.
  var STEP_OF = {
    receive: null,
    screenshot: 'capture',
    context: 'capture',
    project_root: 'inspect',
    error_extract: 'analyze',
    read_files: 'inspect',
    diagnostics: 'inspect',
    model_analysis: 'analyze',
    validate: 'verify',
    report: 'report',
    done: 'report',
    error: null,
    cancelled: null,
  };
  var LOG_TAIL = 3;

  var st = { invId: null, title: 'Investigation', log: [], reached: {}, settled: false, phaseStep: null };

  function stepFor(stage) {
    return Object.prototype.hasOwnProperty.call(STEP_OF, stage) ? STEP_OF[stage] : null;
  }
  function concise(s, max) {
    s = String(s == null ? '' : s);
    return s.length <= max ? s : s.slice(0, max - 1).trimEnd() + '…';
  }

  function renderStitch() {
    if (!el.stitch) return;
    var spans = el.stitch.querySelectorAll('.st');
    var order = ['capture', 'inspect', 'analyze', 'verify', 'report'];
    var furthest = -1;
    for (var k = 0; k < order.length; k++) {
      if (st.reached[order[k]] && k > furthest) furthest = k;
    }
    for (var i = 0; i < spans.length; i++) {
      var s = spans[i].getAttribute('data-s');
      var idx = order.indexOf(s);
      var doneOne = s === 'report' ? st.settled : idx < furthest;
      spans[i].classList.toggle('tick', !!doneOne);
      spans[i].classList.toggle('seen', !doneOne && idx <= furthest && furthest >= 0);
      spans[i].classList.toggle('now', !doneOne && s === (st.phaseStep || (furthest >= 0 ? order[furthest] : '')));
    }
  }

  function renderLog() {
    if (!el.log) return;
    var tail = st.log.slice(-LOG_TAIL);
    var sig = tail.join(' ');
    if (el.log.dataset.sig === sig) return;
    el.log.dataset.sig = sig;
    el.log.innerHTML = '';
    for (var i = 0; i < tail.length; i++) {
      var d = document.createElement('div');
      d.className = 'll';
      d.textContent = tail[i];
      el.log.appendChild(d);
    }
  }

  function showLive() { if (el.live) el.live.hidden = false; }
  function hideVerdict() { if (el.verdict) el.verdict.hidden = true; }

  function ensureInv(invId, title) {
    if (st.invId === invId) return;
    api.begin(invId || 'unknown', title);
  }

  var api = {
    begin(invId, title) {
      st.invId = invId;
      st.title = concise(title || 'Investigation', 40);
      st.log = [];
      st.reached = {};
      st.settled = false;
      st.phaseStep = null;
      if (el.liveTitle) el.liveTitle.textContent = st.title;
      if (el.livePhase) el.livePhase.textContent = 'working';
      hideVerdict();
      renderStitch();
      renderLog();
      showLive();
    },
    activity(info) {
      if (!info || !st.invId) return;
      var step = stepFor(info.stage);
      if (step && !st.reached[step]) {
        st.reached[step] = true;
        st.phaseStep = step;
        renderStitch();
      }
      if (info.phase && el.livePhase) el.livePhase.textContent = concise(info.phase, 24);
      if (info.label) {
        if (st.log[st.log.length - 1] !== info.label) st.log.push(info.label);
        if (st.log.length > 40) st.log.splice(0, st.log.length - 40);
        renderLog();
      }
    },
    verdict(line, invId, title) {
      if (invId) ensureInv(invId, title);
      if (!st.invId) return;
      if (el.verdictLine) el.verdictLine.textContent = concise(line || 'The investigation finished.', 160);
      if (el.livePhase) el.livePhase.textContent = 'done';
      if (el.verdict) el.verdict.hidden = false;
      showLive();
    },
    finish(status, invId, title) {
      if (invId) ensureInv(invId, title);
      if (!st.invId) return;
      // Only a completed run settles the report step; a failed one keeps the
      // rail honest (steps reached stay lit, report stays unticked/grey).
      st.settled = status === 'done';
      if (status === 'done') {
        st.phaseStep = 'report';
      } else {
        st.phaseStep = null;
        if (status !== 'error') st.reached = {};
      }
      if (el.livePhase) {
        el.livePhase.textContent =
          status === 'cancelled' ? 'stopped' : status === 'error' ? 'error' : 'done';
      }
      hideVerdict();
      renderStitch();
    },
    invId() { return st.invId; },
    reset() {
      st.invId = null;
      st.log = [];
      st.reached = {};
      st.phaseStep = null;
      st.settled = false;
      hideVerdict();
      if (el.live) el.live.hidden = true;
    },

    sub() { return el.sub ? el.sub.textContent : ''; },

    /* ---- leaf chrome (owned by shell.js, rendered here) ---- */
    setHead(title, glow, sub) {
      if (el.title && title !== undefined) el.title.textContent = concise(title || 'Trace', 40);
      if (el.glow) el.glow.className = glow || '';
      if (el.glow) el.glow.id = 'leaf-glow';
      if (el.sub) el.sub.textContent = concise(sub || '', 90);
    },
  };

  if (el.open) {
    el.open.addEventListener('click', function (e) {
      e.stopPropagation();
      try { if (window.trace && window.trace.showResults) window.trace.showResults(); } catch (err) {}
    });
  }

  window.TracePanel = api;
})();
