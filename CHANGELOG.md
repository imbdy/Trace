# Changelog

All notable changes to Trace. Versions follow the release commit messages.
Dates are UTC.

## 0.2.2 — 2026-10-20

- Redacted `code intelligence` and test-runner output before the model call,
  closing a hole where raw search hits and TODO lines could reach the
  (cloud) model unredacted.
- Mid-stream Ollama disconnects (`ConnectionResetError`, `IncompleteRead`,
  socket reset after connect) now surface as mapped `OllamaError`, producing
  an honest low-confidence report instead of an internal error.
- Constructed-node parallelism: each branch has a bounded wall-clock wait;
  hung branches no longer stall the node, and a pool-start or partial
  fan-out failure completes remaining branches sequentially instead of
  dropping them.

## 0.2.1 — 2026-10-20

- Live investigation id returned to the renderer via `trace:submit` /
  `trace:follow-up`, re-enabling the stale-event filter between runs.
- Auto-fix ("fix it for me") keeps the desktop quiet as documented, but both
  windows now hear the verdict, so the panel settles instead of shimmering.
- Tray Quit runs full cleanup (`app.quit()`): aborts the run, stops the
  backend child, honors pending updates.
- Results window hardened against malformed report JSON (per-field
  normalization, history-outage state distinct from empty history).

## 0.2 — 2026-10-19

- Agent workflow shipped as an explicit graph: named nodes, conditional
  routes, parallel fan-out/fan-in, a bounded JSON repair re-ask, cycle
  guard, and a visited-node + per-branch timing trace in the report's
  evidence summary.
- Streaming reasoning for `model_analysis` — throttled "thinking ▸"
  stage events replace the silent gap.

## 0.1.x — 2026-10-10 … 2026-10-16

- Initial 10-stage pipeline (receive → screenshot → context → project root
  → error extract → read files → diagnostics → model analysis → validate →
  report) with findings verified against files on disk.
- Loopback-only HTTP API with SSE progress events, cancel, follow-ups,
  apply-fix with `*.trace-bak` backups, and a history journal.
- Neo-derived floating companion UI and the tabbed results workspace
  (Answer / Code & Diff / Evidence).
