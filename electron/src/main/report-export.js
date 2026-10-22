// Trace report export: turn a RendererStatus-shaped object into a Markdown
// file. Kept verbatim as dependency-free JS (node --test covers it) like
// placement.js / settings.js — main.ts requires it at runtime.
//
// Honesty contract of the Markdown mirror:
// - verified and unverified findings stay in separate sections, never merged;
// - every finding keeps its location line, evidence keeps its source;
// - the diff is copied raw with the verified/unverified label;
// - absent things are omitted (or stated as unknown), never invented.
'use strict';
(function () {
  function fence(content, tag) {
    var body = String(content || '');
    var longest = 0;
    var re = /`+/g;
    var m;
    while ((m = re.exec(body))) {
      if (m[0].length > longest) longest = m[0].length;
    }
    var ticks = '`';
    var n = Math.max(3, longest + 1);
    for (var i = 1; i < n; i++) ticks += '`';
    return ticks + (tag || '') + '\n' + body + '\n' + ticks;
  }

  function pad2(n) { return n < 10 ? '0' + n : String(n); }

  // Contract timestamps are seconds; a stray ms value is clamped, not shown
  // as a date five hundred centuries in the future. Returns local
  // "YYYY-MM-DD HH:MM (iso)" or null when the value is unusable.
  function diagnosisStamp(tsRaw) {
    if (typeof tsRaw !== 'number') return null;
    var ts = tsRaw > 1e12 ? tsRaw : tsRaw * 1000;
    var d = new Date(ts);
    if (isNaN(d.getTime())) return null;
    return pad2(d.getFullYear()) + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) +
      ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) +
      ' (' + d.toISOString() + ')';
  }

  function sanitizeFileBase(id) {
    var base = String(id || '').replace(/[^\w.-]/g, '_');
    return base ? base : 'trace-report';
  }

  function strings(list) {
    if (!Array.isArray(list)) return [];
    return list.filter(function (v) { return typeof v === 'string' && v.trim().length > 0; });
  }

  function locationLine(f) {
    if (!f || typeof f !== 'object') return null;
    var file = String(f.file_path || '').trim();
    var start = typeof f.start_line === 'number' ? f.start_line : null;
    var end = typeof f.end_line === 'number' ? f.end_line : null;
    var loc = end !== null && start !== null && end !== start
      ? file + ':' + start + '-' + end
      : file !== '' && start !== null
        ? file + ':' + start
        : file;
    return loc ? '- Location: ' + loc : null;
  }

  function findingCard(out, f, unverified) {
    if (!f || typeof f !== 'object') return;
    var lines = [];
    var title = String(f.title || '').trim();
    var loc = locationLine(f);
    if (loc) lines.push(loc);
    var explanation = String(f.explanation || '').trim();
    if (explanation) lines.push('- Explanation: ' + explanation);
    var excerpt = String(f.code_excerpt || '').trim();
    if (excerpt) {
      lines.push('', 'Excerpt:', fence(excerpt), '');
    }
    var evidence = String(f.evidence || '').trim();
    if (evidence) {
      lines.push('Evidence:', fence(evidence), '');
    }
    var gitContext = String(f.git_context || '').trim();
    if (gitContext) lines.push('- Git: ' + gitContext);
    if (!lines.length) return;
    out.push('### ' + title + (unverified ? ' (unverified)' : ''), '');
    pushLines(out, lines);
    out.push('');
  }

  function section(out, title, lines) {
    out.push('## ' + title, '');
  }

  function pushLines(out, lines) {
    for (var i = 0; i < lines.length; i++) out.push(lines[i]);
  }

  function reportMarkdown(status) {
    var st = status && typeof status === 'object' ? status : {};
    var rep = st.report && typeof st.report === 'object' ? st.report : null;
    var mode = String(st.investigation_type || 'debug') === 'explain' ? 'Explain' : 'Debug';
    var out = [];
    out.push('# Trace report — ' + sanitizeFileBase(st.investigation_id) + ' (' + mode + ')', '');

    var diagnosed = rep ? diagnosisStamp(rep.created_at) : null;
    if (st.status === 'running') diagnosed = null;
    out.push('Diagnosed: ' + (diagnosed || 'unknown'), '');

    var state = st.status === 'done' ? 'Ready'
      : st.status === 'running' ? 'Working'
        : 'Needs attention';
    out.push('Status: ' + state, '');

    if (st.error && typeof st.error === 'object') {
      var err = st.error;
      out.push('Error: ' + String(err.code || 'unknown') + ' — ' + String(err.message || ''));
      var hint = String(err.hint || '').trim();
      if (hint) out.push('Hint: ' + hint);
      out.push('');
    }

    var verdict = rep ? String(rep.summary || '').trim() : '';
    if (verdict) out.push('Verdict: ' + verdict, '');
    var rootCause = rep ? String(rep.root_cause || '').trim() : '';
    if (rootCause) out.push('Root cause: ' + rootCause, '');
    var confidence = rep ? String(rep.confidence || '').trim() : '';
    var rationale = rep ? String(rep.confidence_rationale || '').trim() : '';
    if (confidence || rationale) {
      out.push('Confidence: ' + [confidence, rationale].filter(function (v) { return v; }).join(' — '), '');
    }

    var findings = rep && Array.isArray(rep.findings) ? rep.findings : [];
    var verified = findings.filter(function (f) {
      return f && typeof f === 'object' && f.verified === true;
    });
    var unverified = findings.filter(function (f) {
      return f && typeof f === 'object' && f.verified !== true;
    });
    if (verified.length) {
      section(out, 'Verified findings (' + verified.length + ')', []);
      for (var i = 0; i < verified.length; i++) findingCard(out, verified[i], false);
    }
    if (unverified.length) {
      section(out, 'Findings we could not verify at the source (' + unverified.length + ')', []);
      for (var j = 0; j < unverified.length; j++) findingCard(out, unverified[j], true);
    }

    var hypotheses = strings(rep && rep.alternative_hypotheses);
    if (hypotheses.length) {
      section(out, 'Alternative hypotheses (listed, not settled)', []);
      for (var h = 0; h < hypotheses.length; h++) out.push('- ' + hypotheses[h]);
      out.push('');
    }
    var moreInfo = strings(rep && rep.needs_more_info);
    if (moreInfo.length) {
      section(out, 'Needs more information', []);
      for (var n = 0; n < moreInfo.length; n++) out.push('- ' + moreInfo[n]);
      out.push('');
    }
    var evidenceSummary = rep ? String(rep.evidence_summary || '').trim() : '';
    if (evidenceSummary) {
      section(out, 'Evidence summary', []);
      out.push(evidenceSummary, '');
    }
    var warnings = strings(rep && rep.warnings);
    if (warnings.length) {
      section(out, 'Warnings', []);
      for (var w = 0; w < warnings.length; w++) out.push('- ' + warnings[w]);
      out.push('');
    }

    var fix = rep && rep.recommended_fix && typeof rep.recommended_fix === 'object'
      ? rep.recommended_fix : null;
    if (fix) {
      var diffVerified = fix.diff_verified === true;
      section(out, 'Proposed patch (' + (diffVerified ? 'verified — safe to apply' : 'unverified — review only') + ')', []);
      var description = String(fix.description || '').trim();
      if (description) out.push(description, '');
      var rationale = String(fix.rationale || '').trim();
      if (rationale) out.push('Rationale: ' + rationale, '');
      var diff = String(fix.diff || '').trim();
      if (diff) out.push(fence(diff, 'diff'), '');
      var applied = rep.fix_applied && typeof rep.fix_applied === 'object' ? rep.fix_applied : null;
      if (applied) {
        var appLines = [];
        appLines.push('- Applied: ' + (applied.applied === true
          ? (applied.dry_run === true ? 'dry run only' : 'yes')
          : 'no'));
        if (Array.isArray(applied.applied_files) && applied.applied_files.length) {
          appLines.push('- Files: ' + applied.applied_files.join(', '));
        }
        if (Array.isArray(applied.backup_paths) && applied.backup_paths.length) {
          appLines.push('- Backups: ' + applied.backup_paths.join(', '));
        }
        var appWarn = strings(applied.warnings);
        for (var aw = 0; aw < appWarn.length; aw++) appLines.push('- Warning: ' + appWarn[aw]);
        if (appLines.length) {
          out.push('Applied:', '');
          pushLines(out, appLines);
          out.push('');
        }
      }
    }

    out.push('—', 'Exported by Trace. This file mirrors the report as shown;',
      'verify code against the repository before applying anything it says.');
    return out.join('\n') + '\n';
  }

  module.exports = { reportMarkdown: reportMarkdown, sanitizeFileBase: sanitizeFileBase, fence: fence, diagnosisStamp: diagnosisStamp };
})();
