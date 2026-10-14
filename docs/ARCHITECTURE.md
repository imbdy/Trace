# Trace Architecture — 5 Separated Components

Data flows strictly top → down. No layer reaches around its neighbour,
except the composition roots, which only wire startup (never business logic).

```
┌─────────────────────────────────────────────────────────┐
│ 1. Trace command bar  ······ electron/src/renderer/neo/ │
│    your request and investigation progress              │
│    hearthling.js face (verbatim Neo primitives) inside  │
│    a custom glass console: shell.js chrome, leaf.js     │
│    progress rail, trace.js driver; composer + stitch +  │
│    verdict. No ambient chatter, no poke/tuck gimmicks.  │
└──────────────────────────┬──────────────────────────────┘
                           │ plain-language request (preload bridge → IPC)
                           ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Investigation orchestrator · trace_backend/          │
│    Screenshot · Project context · Logs · Diagnostics    │
│    investigation_engine.py drives: screenshot_service,  │
│    context_collector, project_inspector (+ server.py    │
│    hosts it over loopback HTTP; electron/src/main/     │
│    traceApi.ts is the UI's transport to that HTTP API)  │
└──────────────────────────┬──────────────────────────────┘
                           │ redacted evidence bundle only
                           ▼
┌─────────────────────────────────────────────────────────┐
│ 3. Ollama — gemma4:31b-cloud ··· ollama_client.py       │
│    Reasoning, root-cause analysis, and explanations     │
│    standalone: imports only config + stdlib. Only the   │
│    Python backend ever contacts Ollama — never Electron │
└──────────────────────────┬──────────────────────────────┘
                           │ unverified model JSON
                           ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Evidence verification ····· validator.py             │
│    Validate files, line numbers, and diagnostics        │
│    downstream-only: never calls the model/orchestrator  │
└──────────────────────────┬──────────────────────────────┘
                           │ verified report
                           ▼
┌─────────────────────────────────────────────────────────┐
│ 5. Investigation results console · renderer/results.*   │
│    Tabbed dev-tool workspace: Answer · Code & Diff ·    │
│    Evidence. Renders backend status JSON; backend is    │
│    reached only through the preload bridge (open-file,  │
│    apply-fix, follow-up, history).                      │
└─────────────────────────────────────────────────────────┘
```

## Dependency rules (enforced by tests/test_layering.py)

| Rule | Meaning |
|---|---|
| Backend never imports UI | `trace_backend/*` has no `trace_ui`/tkinter dependency (except stdlib `tkinter` as a clipboard reader inside `context_collector`, which imports no Trace UI code) |
| L3 standalone | `ollama_client` ← only `config`; no evidence, validator, engine, or UI imports |
| L4 downstream-only | `validator` ← only `project_inspector` + `schemas`; never calls model/orchestrator |
| L2 orchestrates, doesn't host | `investigation_engine` never imports `server`; `server` hosts the engine and never touches evidence/model modules directly (fix + tools stay behind `engine.apply_fix`) |
| Code intelligence is grounded | `code_tools` ← only `project_inspector`; search/outline/tests never execute project scripts (tests run only the allowlisted runner, opt-in) |
| Safe fixer is grounded | `code_fixer` ← only `project_inspector` + `validator`; only verified hunks are written, always with `*.trace-bak` backups |
| Transport is dumb | `traceApi.ts` is fetch-only HTTP (start/stream/cancel/open-file/apply-fix); renderer files are import-free plain scripts behind the preload bridge |
| L1 Face craft, Trace console | face renderer keeps Neo's spring/eye primitives (verbatim fidelity); the console chrome is Trace's own — composer, progress rail, verdict — no backend URLs, no model access, no Neoma (enforced by `tests/test_frontend_guards.py`) |
| L5 renders backend JSON | results renderer builds Diagnosis/Locations/Fix/Evidence from status JSON; incomplete states render as honest banners; Apply-fix calls the backend endpoint |
| Single bridge | Only composition roots (`electron/src/main/main.ts` spawn + IPC, `trace_ui/app.py`, `main.py` CLI) reference both sides, for startup wiring only |

## History & follow-up (recall)

`trace_backend/history.py` is a stdlib-only, dict-based journal: one small JSON
file per finished investigation (atomic temp-file + `os.replace` writes,
newest-first listing, count pruning, corrupt-file tolerant). It imports no other
Trace component on purpose, so a stored verdict stays readable even if the
schema layer changes.

```
server.py ──▶ history.py        (owns the journal; records the final status)
server.py ──▶ engine.follow_up  (prior report as context; same validator gate)
investigation_engine.py  ✗──▶   history  (the engine never reads history)
```

A finished investigation is recallable via `GET /api/v1/investigations/{id}`
and followable via `POST /api/v1/investigations/{id}/follow-up` — see
docs/API_CONTRACT.md. `TRACE_HISTORY=0` turns the journal off; every route then
degrades honestly (empty list, 404) instead of failing.

## Privacy note (unchanged)

`gemma4:31b-cloud` may process submitted context through Ollama's cloud
service — it is not local-only. The orchestrator minimises the bundle
(24k-char budget, downscaled screenshots, tail-only logs) and redacts
secret-like patterns before anything reaches layer 3.
