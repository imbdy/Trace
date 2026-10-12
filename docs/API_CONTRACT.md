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
  "follow_up_of": "inv-abc123 (optional; see POST .../follow-up)"
}
```

- `include_clipboard=false` default; clipboard is read ONLY when `true`.
- `screenshot_mode` must be `active` or `full`.
- `follow_up_of` is normally set for you by the follow-up endpoint; when set,
  the engine answers a question about that investigation instead of
  re-collecting evidence from scratch.

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
  "error": { "code": "...", "message": "..." }
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
for a follow-up (see below).

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

## POST /api/v1/open-file → 200

```json
{"path": "C:/code/myapp/app.py", "line": 42}
```

Opens via OS default handler. `line` is best-effort (editor integration
dependent); response notes this honestly. 404 if file missing.

## GET /api/v1/health · GET /api/v1/config

- health → `{"ok": true, "service": "trace", "time": ...}`
- config → redacted config + cloud note (`has_api_key` boolean only).
