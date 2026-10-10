# Trace Architecture — 5 Separated Components

Data flows strictly top → down. No layer reaches around its neighbour,
except the composition roots, which only wire startup (never business logic).

```
┌─────────────────────────────────────────────────────────┐
│ 1. Trace desktop character  ··· electron/src/renderer/  │
│    your request and character interactions              │
│    character.* (canvas bar + spring eyes + input panel) │
│    primary frontend is Electron; trace_ui/ (tkinter) is │
│    retained as a zero-dependency fallback               │
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
│ 5. Investigation report window · renderer/results.*     │
│    Cause · Exact lines · Explanation · Proposed fix     │
│    renders backend status JSON; backend reached only    │
│    through the preload bridge (open-file)               │
└─────────────────────────────────────────────────────────┘
```

## Dependency rules (enforced by tests/test_layering.py)

| Rule | Meaning |
|---|---|
| Backend never imports UI | `trace_backend/*` has no `trace_ui`/tkinter dependency (except stdlib `tkinter` as a clipboard reader inside `context_collector`, which imports no Trace UI code) |
| L3 standalone | `ollama_client` ← only `config`; no evidence, validator, engine, or UI imports |
| L4 downstream-only | `validator` ← only `project_inspector` + `schemas`; never calls model/orchestrator |
| L2 orchestrates, doesn't host | `investigation_engine` never imports `server`; `server` hosts the engine and never touches evidence/model modules directly |
| Transport is dumb | `traceApi.ts` is fetch-only HTTP (start/stream/cancel/open-file); renderer files are import-free plain scripts behind the preload bridge |
| L1 sees only motion | character renderer uses the bridge + its own spring/eye code; no backend URLs, no model access (enforced by `tests/test_frontend_guards.py`) |
| L5 renders backend JSON | results renderer builds Diagnosis/Locations/Fix/Evidence from status JSON; incomplete states render as honest banners |
| Single bridge | Only composition roots (`electron/src/main/main.ts` spawn + IPC, `trace_ui/app.py`, `main.py` CLI) reference both sides, for startup wiring only |

## Privacy note (unchanged)

`gemma4:31b-cloud` may process submitted context through Ollama's cloud
service — it is not local-only. The orchestrator minimises the bundle
(24k-char budget, downscaled screenshots, tail-only logs) and redacts
secret-like patterns before anything reaches layer 3.
