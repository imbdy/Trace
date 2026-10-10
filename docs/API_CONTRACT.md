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
  "extra_context": "... (optional)"
}
```

- `include_clipboard=false` default; clipboard is read ONLY when `true`.
- `screenshot_mode` must be `active` or `full`.

Response: `{"investigation_id": "inv-abc123"}`.

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
  "created_at": 1234567890.0
}
```

Rules the frontend can rely on:
- Every `findings[].verified=true` location exists on disk with valid lines
  and a real `code_excerpt`. `verified=false` entries carry an
  `[UNVERIFIED: reason]` marker in `evidence` plus a `warnings` entry.
- Paths/line numbers are never invented; low evidence → `confidence: low`
  + `needs_more_info` instead of fabrication.

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
