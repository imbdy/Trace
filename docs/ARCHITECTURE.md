# Trace Architecture — 5 Separated Components

Data flows strictly top → down. No layer reaches around its neighbour,
except the composition roots, which only wire startup (never business logic).

```
┌─────────────────────────────────────────────────────────┐
│ 1. Trace desktop character  ··· trace_ui/               │
│    your request and character interactions              │
│    companion.py (window) + character.py (motion/stages) │
└──────────────────────────┬──────────────────────────────┘
                           │ plain-language request
                           ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Investigation orchestrator · trace_backend/          │
│    Screenshot · Project context · Logs · Diagnostics    │
│    investigation_engine.py drives: screenshot_service,  │
│    context_collector, project_inspector (+ server.py    │
│    hosts it over loopback HTTP; api_client.py is the    │
│    UI's stdlib-only transport to that HTTP API)         │
└──────────────────────────┬──────────────────────────────┘
                           │ redacted evidence bundle only
                           ▼
┌─────────────────────────────────────────────────────────┐
│ 3. Ollama — gemma4:31b-cloud ··· ollama_client.py       │
│    Reasoning, root-cause analysis, and explanations     │
│    standalone: imports only config + stdlib             │
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
│ 5. Investigation report window · results_window.py      │
│    Cause · Exact lines · Explanation · Proposed fix     │
│    renders result_model view-models; backend reached    │
│    only through the injected API client (open-file)     │
└─────────────────────────────────────────────────────────┘
```

## Dependency rules (enforced by tests/test_layering.py)

| Rule | Meaning |
|---|---|
| Backend never imports UI | `trace_backend/*` has no `trace_ui`/tkinter dependency (except stdlib `tkinter` as a clipboard reader inside `context_collector`, which imports no Trace UI code) |
| L3 standalone | `ollama_client` ← only `config`; no evidence, validator, engine, or UI imports |
| L4 downstream-only | `validator` ← only `project_inspector` + `schemas`; never calls model/orchestrator |
| L2 orchestrates, doesn't host | `investigation_engine` never imports `server`; `server` hosts the engine and never touches evidence/model modules directly |
| Transport is dumb | `api_client` is stdlib-only HTTP (start/stream/cancel/open-file); owns `TERMINAL_STAGES` locally instead of importing character code |
| L1 sees only motion | `companion` ← only `character`; no network, report, or backend imports |
| L5 sees only view-models | `results_window` ← only `result_model`; backend reached via injected client |
| Single bridge | Only composition roots (`trace_ui/app.py` autostart + `main.py` CLI) reference both sides, for startup wiring only |

## Privacy note (unchanged)

`gemma4:31b-cloud` may process submitted context through Ollama's cloud
service — it is not local-only. The orchestrator minimises the bundle
(24k-char budget, downscaled screenshots, tail-only logs) and redacts
secret-like patterns before anything reaches layer 3.
