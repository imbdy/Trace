# Trace Frontend Communication Contract (v1)

Base URL: `http://127.0.0.1:{port}/api/v1` (loopback only). All bodies are JSON.
CORS: `Access-Control-Allow-Origin: *` (loopback service). Error shape:
`{"error": {"code": "...", "message": "...", "hint": "..."}}`.

## POST /api/v1/investigations → 202

Start an investigation.

Request (`InvestigationRequest`):

```json
{
  "request_text": "App crashes on login (required)",
  "project_dir": "C:/code/myapp (optional; else auto-detect from CWD)",
  "include_screenshot": true,
  "screenshot_mode": "active | full (default active)",
  "include_clipboard": false,
  "editor_selection": "... (optional)",
  "terminal_output": "... (optional, tail kept)",
  "extra_context": "... (optional)",
  "follow_up_of": "inv-abc123 (optional; see POST .../follow-up)",
  "auto_fix": false,
  "run_tests": false,
  "investigation_type": "debug | explain (default debug)"
}
```

- `include_clipboard=false` default; clipboard is read ONLY when `true`.
- `screenshot_mode` must be `active` or `full`.
- `follow_up_of` is normally set for you by the follow-up endpoint; when set,
  the engine answers a question about that investigation instead of
  re-collecting evidence from scratch.
- `auto_fix=true`: "what's wrong, then go fix it" — the engine applies the
  VERIFIED fix diff straight to the files (with `*.trace-bak` backups) and
  reports it in `report.fix_applied`. Unverified diffs are never written.
  The desktop UI then stays quiet (no results window) and just says "fixed".
  Disabled server-wide with `TRACE_ALLOW_FILE_EDITS=0`.
- `run_tests=true`: include the project's allowlisted test output as evidence.
  Execution is opt-in server-wide (`TRACE_ALLOW_TESTS=1`); project scripts are
  never executed.
- `investigation_type="explain"`: explain the code instead of debugging an
  error — the engine skips error extraction and test execution, reads
  candidate files found from the request text, and answers with the
  code-explanation prompt (`root_cause` then describes the code's
  purpose/intent, and no fix is proposed unless there is a clear bug).

Response: `{"investigation_id": "inv-abc123"}`.

Every finished investigation (done, error or cancelled) is also written to the
on-disk history journal, so its verdict survives a backend restart
(`TRACE_HISTORY=0` disables the journal; `TRACE_HISTORY_DIR` relocates it).

## GET /api/v1/investigations/{id} → 200

Poll status/result (`InvestigationStatus`):

```json
{
  "investigation_id": "inv-abc123",
  "status": "running | done | error | cancelled",
  "progress": 0.0,
  "stage": "read_files",
  "report": { "..." (when done) },
  "error": { "code": "...", "message": "..." },
  "investigation_type": "debug | explain (echoed from the request)"
}
```

If the id is not a live job, the journal is consulted: a past verdict is
returned exactly as it was stored (same `report` shape), so history is
recallable after a restart. 404 `not_found` when it is in neither place —
Trace never fabricates a verdict for an unknown id.

Stage names: `receive, screenshot, context, project_root, error_extract,
read_files, diagnostics, model_analysis, validate, report`.

Report (`InvestigationReport`):

```json
{
  "investigation_id": "inv-abc123",
  "summary": "...",
  "root_cause": "...",
  "confidence": "low | medium | high",
  "confidence_rationale": "...",
  "findings": [
    {"title": "...", "file_path": "app.py", "start_line": 4, "end_line": 5,
     "code_excerpt": "    4: ...", "explanation": "...", "evidence": "...",
     "confidence": "medium", "verified": true}
  ],
  "recommended_fix": {"description": "...", "diff": "...", "rationale": "..."},
  "alternative_hypotheses": ["..."],
  "needs_more_info": ["Select the project folder ..."],
  "evidence_summary": "...",
  "warnings": ["Unverified location: ..."],
  "created_at": 1234567890.0,
  "follow_up_of": ""
}
```

`follow_up_of` is `""` for a first-pass report and the parent investigation id
for a follow-up (see below). `fix_applied` is `null` unless `auto_fix` was
requested: `{"applied": true, "applied_files": [...], "backup_paths": [...],
"warnings": [...], "dry_run": false}`.

Rules the frontend can rely on:
- Every `findings[].verified=true` location exists on disk with valid lines
  and a real `code_excerpt`. `verified=false` entries carry an
  `[UNVERIFIED: reason]` marker in `evidence` plus a `warnings` entry.
- Paths/line numbers are never invented; low evidence → `confidence: low`
  + `needs_more_info` instead of fabrication.

## GET /api/v1/investigations → 200

Past investigations, newest first (`?limit=N`, default 50):

```json
{
  "investigations": [
    {"investigation_id": "inv-abc123", "status": "done",
     "created_at": 1234567890.0, "summary": "...", "confidence": "medium",
     "finding_count": 2, "verified_count": 1,
     "project_dir": "C:/code/myapp", "request_text_preview": "App crashes…",
     "follow_up_of": ""}
  ],
  "count": 1
}
```

Empty (`{"investigations": [], "count": 0}`) when history is disabled or
nothing has been recorded yet. `follow_up_of` is the parent id for follow-ups,
otherwise `""`.

## POST /api/v1/investigations/{id}/follow-up → 202

Ask a question about an existing verdict. The parent report is resolved from
the live job first and the journal second, so this also works after a restart.

Request:

```json
{"request_text": "Why does the guard not help? (required)",
 "project_dir": "C:/code/myapp (optional; defaults to the parent's)"}
```

Response: `{"investigation_id": "inv-def456", "follow_up_of": "inv-abc123"}`.

The new investigation streams the usual stages on
`GET /api/v1/investigations/{id}/events` and its report carries
`follow_up_of: "inv-abc123"`. The follow-up re-reads the files the parent
cited and every finding in its answer goes through the same path/line
verification as a first-pass report; when the parent's project cannot be
identified, the report says so in `needs_more_info` and its locations are
labelled `[UNVERIFIED: ...]` rather than asserted.

404 `not_found` when there is no completed report to follow up on; 400
`bad_request` for an empty `request_text`.

## GET /api/v1/investigations/{id}/events (SSE)

`Content-Type: text/event-stream`. Events (`ProgressEvent`):

```
data: {"investigation_id":"inv-...","stage":"read_files","message":"...","progress":0.6,"timestamp":...}

```

Ends with a terminal event (`stage: done|error|cancelled`) then stream close.
Keepalive comments (`: keepalive`) every ~25s.

## POST /api/v1/investigations/{id}/cancel → 200

Cancels a running investigation. 404 unknown id; 409 already finished.

## POST /api/v1/investigations/{id}/apply-fix → 200

Applies the completed report's VERIFIED fix diff to disk (the "go fix that
file" button). Request: `{"dry_run": false}`. Response on success:

```json
{"ok": true, "applied": true, "applied_files": ["app.py"],
 "backup_paths": [".../app.py.trace-bak"], "warnings": []}
```

- 404 when there is no completed report; 409 when the run is still going,
  when no diff was proposed, or when the diff cannot be verified against the
  current files (re-run the investigation instead of force-applying).
- 403 when file edits are disabled (`TRACE_ALLOW_FILE_EDITS=0`).
- Every overwritten file keeps a `*.trace-bak` backup; `dry_run: true`
  verifies without writing.

## POST /api/v1/open-file → 200

```json
{"path": "C:/code/myapp/app.py", "line": 42}
```

Opens via OS default handler. `line` is best-effort (editor integration
dependent); response notes this honestly. 404 if file missing.

## GET /api/v1/health · GET /api/v1/config

- health → `{"ok": true, "service": "trace", "time": ...}`
- config → redacted config + cloud note (`has_api_key` boolean only).

## POST /api/v1/config/test → 200

The settings panel's "Test connection" probe: one minimal `"Say OK"`
round-trip against the provider, nothing else (no project context, no
streaming, no report).

Request — optional, mirrors `POST /config` and is never persisted; omitted
fields fall back to the running configuration:

```json
{"provider": "ollama | openai-compatible",
 "model": "gemma4:31b-cloud (required-ish; falls back to the saved one)",
 "api_base": "https://api.openai.com/v1 (openai-compatible only)",
 "api_key": "optional; falls back to the saved one"}
```

Success:

```json
{"ok": true, "latency_ms": 412}
```

Failure (model unreachable, bad key, wrong model name — always HTTP 200 so
the UI can render the message directly):

```json
{"ok": false, "error": "Cannot reach Ollama at http://127.0.0.1:11434: ..."}
```

400 `bad_request` only for an invalid request body (unknown provider, empty
model, openai-compatible without an http(s) api_base).
