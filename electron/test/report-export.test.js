'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const X = require('../src/main/report-export.js');

const doneStatus = {
  investigation_id: 'inv-abc123',
  status: 'done',
  investigation_type: 'debug',
  report: {
    summary: 'The retry loop eats the first error message.',
    root_cause: 'read_lines resets the window on cancel.',
    confidence: 'high',
    confidence_rationale: 'Excerpt and evidence agree with the code.',
    findings: [
      {
        title: 'Retry swallows the first error',
        file_path: 'app/retry.py',
        start_line: 12,
        end_line: 19,
        code_excerpt: 'try:\n    return fn()\nexcept Exception:',
        explanation: 'The first except clause returns before propagating.',
        evidence: 'Traceback shows the error raised inside fn() at retry.py:13.',
        git_context: 'last touched by the parallelism change',
        verified: true,
      },
      {
        title: 'Window reset rumor',
        file_path: 'app/window.py',
        start_line: 3,
        end_line: 3,
        code_excerpt: 'rows = []',
        explanation: 'Possible, not confirmed by a run.',
        evidence: '',
        verified: false,
      },
    ],
    recommended_fix: {
      description: 'Propagate the first error before the cleanup.',
      diff: '--- a/app/retry.py\n+++ b/app/retry.py\n@@ -12,7 +12,7 @@',
      rationale: 'The cleanup cannot know the error shape.',
      diff_verified: true,
    },
  },
};

test('verified and unverified findings stay in separate sections with provenance', () => {
  const md = X.reportMarkdown(doneStatus);
  assert.match(md, /# Trace report — inv-abc123 \(Debug\)/);
  assert.match(md, /Status: Ready/);
  assert.match(md, /## Verified findings \(1\)/);
  assert.match(md, /## Findings we could not verify at the source \(1\)/);
  assert.match(md, /- Location: app\/retry\.py:12-19/);
  assert.match(md, /### Retry swallows the first error$/m);
  assert.match(md, /### Window reset rumor \(unverified\)$/m);
  assert.match(md, /Traceback shows the error raised inside fn\(\)/);
  assert.match(md, /```diff\n--- a\/app\/retry\.py/);
  assert.match(md, /Proposed patch \(verified — safe to apply\)/);
  // unverified finding labels exist; no excerpt invented when absent
  assert.doesNotMatch(md, /- Location: app\/window\.py:3-3/);
  assert.match(md, /- Location: app\/window\.py:3$/m);
});

test('empty optionals are omitted, never invented', () => {
  const md = X.reportMarkdown({
    investigation_id: 'inv-x',
    status: 'error',
    investigation_type: 'debug',
    error: { code: 'ollama_down', message: 'connect ECONNREFUSED', hint: 'Start Ollama.' },
  });
  assert.match(md, /Status: Needs attention/);
  assert.match(md, /Error: ollama_down — connect ECONNREFUSED/);
  assert.match(md, /Hint: Start Ollama\./);
  assert.doesNotMatch(md, /Verdict:/);
  assert.doesNotMatch(md, /## Verified findings/);
  assert.doesNotMatch(md, /Proposed patch/);
  // created_at unknown -> stated unknown, not a fabricated stamp
  assert.match(md, /Diagnosed: unknown/);
});

test('seconds timestamp is localised; a ms value is clamped; a running run is not stamped', () => {
  const later = new Date('2026-10-21T10:00:00Z').getTime() / 1000;
  const md = X.reportMarkdown({ ...doneStatus, report: { ...doneStatus.report, created_at: later } });
  assert.match(md, /Diagnosed: 2026-\d\d-\d\d (?!unknown).+\(2026-10-21T10:00:00\.000Z\)/);
  const mdMs = X.reportMarkdown({ ...doneStatus, report: { ...doneStatus.report, created_at: later * 1000 } });
  assert.doesNotMatch(mdMs, /Diagnosed: unknown/);
  const mdRun = X.reportMarkdown({ ...doneStatus, status: 'running', report: null });
  assert.match(md, /Status: Ready/);
  assert.match(mdRun, /Status: Working/);
  assert.match(mdRun, /Diagnosed: unknown/);
});

test('a code fence inside evidence gets a longer guard fence', () => {
  const md = X.reportMarkdown({
    ...doneStatus,
    report: {
      ...doneStatus.report,
      findings: [
        {
          title: 'Triple tick in trace',
          file_path: 'x.py',
          start_line: 1,
          end_line: 1,
          code_excerpt: 'MAGIC```',
          explanation: '',
          evidence: 'log contains ``` embedded ticks',
          verified: true,
        },
      ],
    },
  });
  assert.doesNotMatch(md, /^```\nMAGIC```\n````?$/m);
  assert.match(md, /^````\nMAGIC```\n````$/m);
});

test('no findings at all ends up with the truthful sections only', () => {
  const md = X.reportMarkdown({
    investigation_id: 'inv-empty!',
    status: 'done',
    investigation_type: 'explain',
    report: { summary: '', root_cause: '', confidence: '', confidence_rationale: '', findings: [], alternative_hypotheses: [], needs_more_info: [], evidence_summary: '', warnings: [] },
  });
  assert.doesNotMatch(md, /## Verified findings/);
  assert.doesNotMatch(md, /## Findings we could not verify/);
  assert.doesNotMatch(md, /## Alternative hypotheses/);
  assert.match(md, /Exported by Trace/);
});

test('file base keeps the id filesystem-safe', () => {
  assert.equal(X.sanitizeFileBase('inv-abc123'), 'inv-abc123');
  assert.equal(X.sanitizeFileBase('id with/slash&stuff'), 'id_with_slash_stuff');
  assert.equal(X.sanitizeFileBase(null), 'trace-report');
});
