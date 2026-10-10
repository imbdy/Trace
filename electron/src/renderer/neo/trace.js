// Trace × backend — the investigation driver.
//
// Replaces Neo's session watcher. No sessions, no polling, no WebSockets:
// the main process runs each investigation against the loopback backend and
// pushes real SSE stages + the final report here. This file only translates
// those into the creature's states and the panel's stitch/log/verdict.
// Nothing here invents progress — every lit step arrived from the backend.
'use strict';
(function () {
  var currentId = null;
  var running = false;

  function shell() { return window.TraceShell || null; }
  function panel() { return window.TracePanel || null; }
  function ctl() { return window.TraceShellCtl || null; }

  function setRunning(on) {
    running = on;
    var c = ctl();
    if (c) { try { c.setAskEnabled(!on); } catch (e) {} }
  }

  function shortStageLabel(stage) {
    var map = {
      receive: 'Sending request…',
      screenshot: 'Capturing context…',
      context: 'Capturing context…',
      project_root: 'Finding the project…',
      error_extract: 'Reading the error…',
      read_files: 'Reading source files…',
      diagnostics: 'Running diagnostics…',
      model_analysis: 'Reasoning…',
      validate: 'Verifying references…',
      report: 'Writing it up…',
    };
    return map[stage] || 'Working…';
  }

  function onStage(ev) {
    if (!ev) return;
    // Stale events from a previous run (e.g. after cancel) are ignored.
    if (currentId && ev.investigation_id && ev.investigation_id !== currentId) return;
    var c = shell();
    var p = panel();
    var label = ev.message || shortStageLabel(ev.stage);
    if (p) {
      try {
        if (!p.invId() && ev.investigation_id) p.begin(ev.investigation_id, 'Investigation');
        p.activity({ stage: ev.stage, phase: shortStageLabel(ev.stage), label: label });
      } catch (e) {}
    }
    if (c) {
      try {
        if (ev.stage === 'done') { /* terminal verdict arrives via report */ }
        else if (ev.stage === 'error' || ev.stage === 'cancelled') { /* terminal, via report */ }
        else c.working('Trace', label);
      } catch (e) {}
    }
  }

  function onReport(status) {
    if (!status) return;
    setRunning(false);
    var c = shell();
    var p = panel();
    if (status.status === 'cancelled') {
      if (p) { try { p.finish('cancelled', currentId, 'Investigation'); } catch (e) {} }
      if (c) { try { c.idle('that one was stopped.'); } catch (e) {} }
      currentId = null;
      return;
    }
    if (status.status !== 'done' || !status.report) {
      var msg = (status.error && status.error.message) || 'that one snagged.';
      if (p) { try { p.finish('error', currentId, 'Investigation'); } catch (e) {} }
      if (c) { try { c.error('Hmm', String(msg).slice(0, 90)); } catch (e) {} }
      currentId = null;
      return;
    }
    var rep = status.report;
    var findings = Array.isArray(rep.findings) ? rep.findings : [];
    var verified = findings.filter(function (f) { return f && f.verified; }).length;
    if (p) {
      try {
        p.finish('done', rep.investigation_id || currentId, 'Investigation');
        p.verdict(
          (rep.summary || 'The investigation finished.') +
          (verified ? ' ' + verified + ' verified location' + (verified === 1 ? '' : 's') + '.' : ''),
          rep.investigation_id || currentId, 'Investigation');
      } catch (e) {}
    }
    if (c) {
      try { c.done('Done', verified ? verified + ' verified spot' + (verified === 1 ? '' : 's') + ' — all set.' : 'no solid location — all set.', verified); }
      catch (e) {}
    }
    currentId = null;
  }

  function submit(text) {
    if (running) return Promise.resolve({ ok: false, reason: 'busy' });
    if (!text || !String(text).trim()) return Promise.resolve({ ok: false, reason: 'empty' });
    setRunning(true);
    var c = shell();
    var p = panel();
    if (c) { try { c.working('Trace', 'Sending request…'); } catch (e) {} }
    return window.trace.submit(String(text).slice(0, 8000)).then(function (res) {
      if (!res || res.ok === false) {
        setRunning(false);
        if (c) { try { c.error('Hmm', res && res.reason === 'busy' ? 'one at a time.' : 'could not start that one.'); } catch (e) {} }
        return res;
      }
      currentId = res.id || null;
      if (p) { try { p.begin(currentId, 'Investigation'); } catch (e) {} }
      return res;
    }).catch(function (e) {
      setRunning(false);
      if (c) { try { c.error('Hmm', 'could not start that one.'); } catch (e2) {} }
      return { ok: false, reason: 'error' };
    });
  }

  function cancel() {
    setRunning(false);
    try { if (window.trace && window.trace.cancel) return window.trace.cancel(); } catch (e) {}
    return Promise.resolve({ ok: false });
  }

  function start() {
    try {
      if (window.trace && window.trace.onStage) window.trace.onStage(onStage);
      if (window.trace && window.trace.onReport) window.trace.onReport(onReport);
      if (window.trace && window.trace.onBackend) {
        window.trace.onBackend(function (info) {
          var c = shell();
          if (!c) return;
          try {
            if (!info.ok) c.error('Hmm', String(info.message || 'backend is down.').slice(0, 90));
          } catch (e) {}
        });
      }
    } catch (e) {}
    setRunning(false);
  }

  if (window.TraceShell) {
    start();
  } else {
    var tries = 0;
    var timer = setInterval(function () {
      tries += 1;
      if (window.TraceShell || tries >= 20) {
        clearInterval(timer);
        start();
      }
    }, 500);
  }

  window.TraceDriver = {
    submit: submit,
    cancel: cancel,
    state: function () { return { running: running, id: currentId }; },
  };
})();
