# AI Handoff — Trace

This document gives another AI the context needed to start working on this repository safely and effectively.

## 1) What Trace is
- **Product:** Evidence-first desktop debugging companion (Windows-primary), v**0.4.3**.
- **Core behavior:** Accepts a bug report/question, collects bounded/redacted evidence (project files, diagnostics, optional screenshot/context), asks a configured LLM for analysis, then **verifies every cited file/line and diff hunk against disk** before presenting findings. Unverified claims are labeled as hypotheses/warnings, never as confirmed facts.
- **Modes:** `debug` (diagnosis + optional verified fix) and `explain` (code explanation; skips error extraction/test execution and does not propose/apply fixes as part of that flow).
- **UI surfaces:** Electron command bar + results workspace (primary), Python/Tk fallback UI (`trace_ui/`), loopback HTTP backend (`trace_backend/`).
- **Key promise:** “A model suggestion is not treated as a confirmed finding until the backend can verify it.”

Primary references: `README.md`, `docs/ARCHITECTURE.md`, `docs/API_CONTRACT.md`, `CHANGELOG.md`, `RELEASE_NOTES.md`.

## 2) Repository map
```text
trace_backend/          Loopback API, config/schemas, agent graph & engine, collectors, model client, validation, fixer, history, chat
trace_ui/               Dependency-light Python/Tk fallback UI + dumb API client
electron/               Desktop app
  src/main/             Window/tray, backend supervisor, IPC handlers, HTTP client, settings/profile storage
  src/preload.ts        Narrow renderer bridge (only window.trace)
  src/renderer/neo/     Command-bar UI
  src/renderer/         Results workspace (HTML/CSS/TS)
  src/shared/protocol.ts  TS mirror of backend contract/stage labels
  test/                 Node tests for Electron utilities
  scripts/              Build/copy/backend packaging helpers
tests/                  Python pytest suite (contracts, engine/graph, server/SSE, validator/fixer, layering/guards, etc.)
docs/                   API contract, architecture, images
main.py                 Launcher: backend-only or backend + Tk UI
pyproject.toml          Python package (`trace`, trace_backend/trace_ui), extras, script entry
.github/workflows/      test.yml (Windows, py3.10–3.13 + electron tests), release.yml (tag build/publish)
.env                    Local/example env-style config loaded by backend (process env wins); do not commit secrets
```

**Workspace note:** this checkout may contain local artifacts (`.venv/`, `electron/node_modules/`, `electron/dist/`, caches, IDE dirs). Treat them as non-source unless a task explicitly involves build outputs.

## 3) Architecture & data flow
**Strict layering (enforced by tests):**
`Renderer (no Node/fs/model/backend URLs)` → `preload IPC bridge` → `Electron main` → `loopback HTTP backend` → `orchestrator/graph + collectors` → `model provider` → `validator/sanitizer` → `verified report rendered`.

- **Composition/startup roots only** may wire across layers: `electron/src/main/main.ts`, `trace_ui/app.py`, `main.py`.
- **Backend owns reasoning/evidence/verification:** UI must not invent progress, claims, or verification.
- **Agent graph:** explicit stdlib state graph (`trace_backend/agent_graph.py`) with named nodes, conditional routing, bounded repair loop, cycle guard, parallel fan-out/fan-in, per-branch timings/trace recorded into evidence summary.
- **Pipeline stages:** `receive, screenshot, context, project_root, error_extract, read_files, diagnostics, model_analysis, validate, report` (UI groups into capture/inspect/analyze/verify/report).
- **Parallel collection:** `capture = screenshot ^ context`; `collect = diagnostics ^ (opt-in tests) ^ code intelligence`; reads/search helpers use bounded pools with fault isolation and sequential fallback.
- **Follow-ups:** answer against a prior completed report + re-read cited files and re-verify findings (no full recollection).
- **Auto-fix:** only a **verified** unified diff may be applied, inside project root, with `.trace-bak` backups/rotation, atomic writes, preserved encoding/EOL; unverified/no diff/disabled edits => refused honestly.

See: `docs/ARCHITECTURE.md`, `tests/test_layering.py`, `tests/test_frontend_guards.py`, `trace_backend/investigation_engine.py`.

## 4) Backend: key modules
| Module | Responsibility |
|---|---|
| `trace_backend/server.py` | Loopback-only `ThreadingHTTPServer`, job store, SSE event fanout/replay/terminal guarantees, history integration, HTTP routes |
| `trace_backend/investigation_engine.py` | Investigation/follow-up orchestration, graph nodes, prompts, evidence assembly, streaming analysis, report parsing/sanitization, fix application entrypoints |
| `trace_backend/agent_graph.py` | Stdlib graph runtime (nodes/edges, parallel merge, timings, cancel/cycle handling) |
| `trace_backend/schemas.py` | Request/response/progress/report dataclasses, normalization/coercion, stage list, error envelope |
| `trace_backend/config.py` | Env + `.env` loading, defaults/validation (loopback enforced), redacted config |
| `trace_backend/ollama_client.py` | Provider client: Ollama and OpenAI-compatible chat/models/probe, streaming (Ollama), timeout/cancel/error mapping |
| `trace_backend/project_inspector.py` | Root detection, safe/confined reads, excludes/allowlists, secret redaction, error extraction, candidate/import discovery, diagnostics, context bundling |
| `trace_backend/code_tools.py` | Grounded search/symbol/outline/tree/recency/config/annotation/test-failure tools, allowlisted+opt-in test runner |
| `trace_backend/validator.py` | Verify locations/excerpts and fix diffs against disk; sanitize/label reports honestly |
| `trace_backend/code_fixer.py` | Apply verified unified diffs safely (path checks, backups, dry-run, atomic replace) |
| `trace_backend/history.py` | Atomic per-investigation JSON journal, recall/list/prune (best-effort) |
| `trace_backend/chat.py` | Plain conversation (no tools/evidence/verification), bounded history |
| `trace_backend/context_collector.py`, `screenshot_service.py`, `git_context.py` | Local context, screenshot capture/dispose, grounded git recency notes |

## 5) Electron app: key files
| File/area | Responsibility |
|---|---|
| `electron/src/main/main.ts` | App lifecycle, windows/tray, backend ensure/health, IPC handlers, SSE stream consumption/reconnect, report delivery, auto-fix quiet behavior, provider profile storage, auto-update hooks, `--smoke` |
| `electron/src/main/backend.ts` | Spawns/supervises Python backend (`main.py --backend-only` or bundled exe), health checks |
| `electron/src/main/traceApi.ts` | Fetch-only HTTP/SSE client for backend API |
| `electron/src/preload.ts` | Exposes only `window.trace` bridge API/events to renderer |
| `electron/src/shared/protocol.ts` | Contract/stage types and labels mirroring backend |
| `electron/src/renderer/neo/*` | Command-bar UI (chat/investigate, progress, settings entrypoints) |
| `electron/src/renderer/results.ts` (+ html/css) | Defensive rendering of investigation status/report: Answer/Code & Diff/Evidence, history, follow-up, open/apply/save actions |
| `electron/package.json`, `electron/scripts/*` | Build/test/package config (TypeScript, asset copy, NSIS dist) |

**Security posture:** `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, preload-only API, renderer never contacts backend/model directly.

## 6) Python fallback UI
- `trace_ui/app.py` — Tk wiring, optional in-process backend autostart (loopback), worker-thread investigations, UI event pump.
- `trace_ui/api_client.py` — dumb loopback HTTP/SSE client.
- `trace_ui/companion.py`, `results_window.py`, `result_model.py` — presentation/state only; no model reasoning or verification logic.

## 7) API / IPC contract (preserve carefully)
**Backend:** `http://127.0.0.1:{port}/api/v1` (loopback-only). Error shape: `{"error":{"code","message","hint"}}`.

Main endpoints (see `docs/API_CONTRACT.md` for exact shapes):
- `GET /health`, `GET /config`, `POST /config`, `POST /config/test`
- `POST /investigations` → `{investigation_id}` (202)
- `GET /investigations` (history list), `GET /investigations/{id}` (status/report; journal fallback), `GET /investigations/{id}/events` (SSE)
- `POST /investigations/{id}/cancel`, `POST /investigations/{id}/follow-up`, `POST /investigations/{id}/apply-fix`
- `POST /open-file`, `POST /chat`

**Semantics to preserve:** clipboard only when explicitly requested; `auto_fix`/`apply-fix` require verified diffs and allowed edits (plus backups, confined paths); `run_tests` is opt-in server-side and allowlisted; SSE must replay/be race-safe and always deliver terminal completion; history recall is honest (404 when absent), never fabricated; `open-file` line jump is best-effort.

**Electron preload bridge:** renderer actions/events flow only through `window.trace` (`submit`, `followUp`, `chat`, `cancel`, `showResults`, `history/showHistory`, `openFile`, `applyFix`, `saveReport`, provider/config/test calls, `onStage/onReport/onBackend`, resize/hide).

## 8) Configuration & environment
**Desktop users:** configure provider/model/API base/key in the app UI. Keys are kept out of renderer and persisted via Electron encrypted storage when available.

**Backend/env vars (important):**
- `TRACE_PROVIDER` (`ollama` | `openai-compatible`)
- `TRACE_MODEL` (default `gemma4:31b-cloud`)
- `OLLAMA_HOST` (default `http://127.0.0.1:11434`)
- `OLLAMA_API_KEY`, `TRACE_API_BASE`
- `TRACE_BIND_HOST` (must remain loopback), `TRACE_BIND_PORT` (default `3777`)
- `TRACE_CONNECT_TIMEOUT_S`, `TRACE_READ_TIMEOUT_S`
- `TRACE_ALLOW_FILE_EDITS` (default on), `TRACE_ALLOW_TESTS` (default off)
- `TRACE_HISTORY`, `TRACE_HISTORY_MAX`, `TRACE_HISTORY_DIR`
- `TRACE_MAX_ITERATIONS` (graph step budget)
- `TRACE_PYTHON` (Electron backend spawn)

`.env` is loaded by `trace_backend.config` from CWD/repo root candidates; existing process env always wins. **Never commit real API keys/secrets.**

## 9) Safety, privacy, and invariants (do not break)
- **Loopback-only backend**; non-loopback bind must be rejected.
- **Renderer isolation:** no direct Node/fs/model/network-to-backend logic in renderer.
- **Evidence discipline:** bounded reads/scans, excludes for deps/build/venv/caches, secret-pattern redaction before model calls, screenshots disposed.
- **No untrusted execution:** project scripts not executed; tests/diagnostics only allowlisted tools and tests require opt-in.
- **Grounded truth:** findings/locations/excerpts and fix diffs must be verified against disk; unverified items clearly labeled and never auto-applied/written.
- **Safe writes:** apply-fix confined to project root, atomic replace, backups, preserved encoding/EOL, failure-closed behavior.
- **Provider caveat:** cloud models/providers may process submitted context; `gemma4:31b-cloud` is not local-only.

## 10) Build, run, test, release
### Python
```powershell
# install (editable, optional screenshot deps/dev tools)
pip install -e ".[screenshots,dev]"

# backend only
python main.py --backend-only
# or
python -m trace_backend

# fallback UI (autostarts/uses backend per app logic)
python main.py
# or
python -m trace_ui   # expects backend running

# tests
python -m pytest -q
```

### Electron
```powershell
cd electron
npm install
npm start -- --project-dir C:\path\to\project
npm run build
npm test

# package (Windows)
npm run dist:win
```

Useful flags: Python `--backend-only --port --project-dir`; Electron `--project-dir --backend-port --user-data-dir --smoke`.

### CI / release
- `.github/workflows/test.yml`: Windows, pytest on Python 3.10–3.13, Electron utility tests on Node 22.
- `.github/workflows/release.yml`: on `v*` tags, builds bundled backend + NSIS installer, publishes release artifacts/notes.

## 11) Issues backlog (for next AI to work on)
Prioritized, actionable items derived from documented limitations and current implementation. For each: preserve safety/verification contracts, update docs/contracts/types/tests as needed, and keep renderer/backend layering intact.

### P1 — Product/UX gaps
1. **Desktop “dry-run fix preview” in results UI** — ✅ DONE
    - Implemented: `electron/src/renderer/results.ts` `applyBox()` now offers a
      “Preview (dry run)” button beside “Apply fix to files”; it calls the
      existing `window.trace.applyFix(id, true)` bridge path (no new channels)
      and states the outcome exactly as strongly as the backend does
      (applicable files / not-applicable reason / warnings, “No files were
      written”). Unverified patches keep both buttons disabled. README
      limitation bullet removed.

2. **Results history UX: limited visibility/search depth** — ✅ DONE
    - Implemented: results history bar paginates — “Show older” widens the
      window by 50 at a time by passing `limit` through the existing
      `GET /investigations?limit&q` contract (preload/`trace:history` now
      forward the UI’s limit, capped at 5000). Backend search depth is now
      `max(500, limit)` so deep windows also search deeper
      (`trace_backend/server.py` `_history_list`). Short pages hide the
      button; unavailable vs empty states unchanged. Test:
      `tests/test_history.py::test_search_depth_grows_with_limit`.

3. **Open-at-line reliability/editor integration** — ✅ DONE
    - Implemented: `trace_backend/server.py` `open_file_in_editor()` now tries
      (1) `TRACE_EDITOR` (user command template, shlex-split, per-token
      `{path}`/`{line}` substitution, no shell), (2) known editors on PATH
      (VS Code/Insiders, Cursor, Sublime, Zed, Notepad++ — each with a real
      line flag), (3) the OS default handler with the previous honest note.
      Responses carry `editor` + an exact `note`; the results renderer shows
      the backend note verbatim. Windows `.cmd` shims are wrapped for
      CreateProcess. Tests: `tests/test_server_and_screenshots.py`
      `TestOpenFileEditor` (6 cases); layering stdlib set gained `shlex`.

### P2 — Provider/model behavior
4. **OpenAI-compatible streaming chat/completions** — ✅ DONE
   - Implemented: `ollama_client._openai_chat()` now streams SSE deltas
     (`choices[].delta.content`, `[DONE]` sentinel) with the same
     `on_token`/`cancel_event`/deadline semantics as the Ollama NDJSON path;
     `chat()` forwards `stream`/`on_token`/`cancel_event` for compatible
     providers. No engine/SSE/verification changes required (the engine
     already requested streaming ticks). Tests: `tests/test_ollama_client.py`
     `TestOpenAICompatibleStreaming` (5 cases). README limitation removed;
     `docs/ARCHITECTURE.md` updated.

5. **Provider save/test UX clarity (if gaps remain)**
   - **Problem:** Saving provider settings does not run a generation request by design; `/config/test` probe exists. Ensure UX clearly distinguishes “saved” vs “tested/connected” and surfaces probe errors well.
   - **Why:** Reduces confusion when first investigation fails due to bad endpoint/model/key.
   - **Likely files:** `electron/src/renderer/neo/*`, `electron/src/main/main.ts`, `electron/src/main/traceApi.ts`, `docs/API_CONTRACT.md`, `README.md`.
   - **Done when:** Connection state/probe results are explicit and honest in UI; no secrets exposed to renderer; behavior matches docs.

### P3 — Maintenance/safety/packaging
6. **`.trace-bak` lifecycle management**
   - **Problem:** Backup files created by safe fixer/apply-fix are not auto-cleaned.
   - **Why:** Can accumulate over time; cleanup must never risk deleting originals or current files.
   - **Likely files:** `trace_backend/code_fixer.py`, possibly CLI/UI action to list/clean backups, `docs/API_CONTRACT.md`/`README.md` if user-facing.
   - **Done when:** Safe, explicit cleanup strategy (manual command/UI or policy) with clear scoping to project/root and no destructive defaults; tests cover edge cases.

7. **Cross-platform desktop packaging/support**
   - **Problem:** Published/packaged installer currently targets Windows; codebase contains some cross-platform handling but release path is Windows-focused.
   - **Why:** Broader usability (macOS/Linux) if desired.
   - **Likely files:** `electron/package.json` (builder targets), `electron/scripts/*`, `.github/workflows/release.yml`, backend bundling scripts, platform-specific path/open/update behaviors in `electron/src/main/*` and `trace_backend/server.py`.
   - **Done when:** Target platforms build/package in CI with documented install/run steps and no regression to loopback/security behavior.

### How to approach issues
- Start by reproducing/confirming current behavior from code/tests/docs (do not assume).
- Add or update tests first/alongside changes (`tests/*`, `electron/test/*`).
- If API/IPC/schema changes: update `docs/API_CONTRACT.md`, `trace_backend/schemas.py`, `electron/src/shared/protocol.ts`, and all callers.
- Re-run relevant checks: `python -m pytest -q` and/or `cd electron && npm run build && npm test`.

## 12) Working conventions for changes
- **Read before editing:** understand existing contracts, layering, and tests around the area.
- **Preserve honesty & safety invariants** above all (verification, redaction/bounds, confined writes).
- **Do not break contracts** without coordinated updates: `docs/API_CONTRACT.md`, `trace_backend/schemas.py`, `electron/src/shared/protocol.ts`, server/clients/UIs, and tests.
- **Respect layering boundaries**; prefer adding/updating tests (`tests/test_layering.py`, `tests/test_frontend_guards.py`, `tests/test_ui_contract.py`, SSE/server/engine/validator/fixer tests) when behavior/structure changes.
- **Testing guidance:** run targeted tests for touched areas; for backend changes prefer `python -m pytest -q`; for Electron/main/shared/renderer logic changes run `cd electron && npm run build && npm test`.
- **Git:** do not commit or push unless explicitly requested.

## 13) High-impact files to read first (task-dependent)
- **Behavior/contract:** `README.md`, `docs/API_CONTRACT.md`, `docs/ARCHITECTURE.md`
- **Core backend:** `trace_backend/investigation_engine.py`, `trace_backend/server.py`, `trace_backend/agent_graph.py`, `trace_backend/validator.py`, `trace_backend/code_fixer.py`, `trace_backend/ollama_client.py`
- **Desktop path:** `electron/src/main/main.ts`, `electron/src/main/traceApi.ts`, `electron/src/preload.ts`, `electron/src/renderer/results.ts`
- **Fallback UI:** `trace_ui/app.py`, `trace_ui/api_client.py`
- **Packaging/CI:** `pyproject.toml`, `electron/package.json`, `.github/workflows/*.yml`
