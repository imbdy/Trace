# 0.4.3

Model connections and interface polish

- Added an in-app model connection panel opened from the compact gear icon.
- Trace can now use Ollama or any OpenAI-compatible `/chat/completions`
  provider, including hosted APIs, local gateways, and custom endpoints.
- API keys stay out of the renderer and are persisted through Electron's
  encrypted storage when the operating system supports it.
- Reworked the chat control into a compact icon-based panel that matches the
  Trace command surface.
- Existing Ollama environment configuration remains supported for source and
  headless/backend-only runs.

# 0.4.2

Stability

- Race-free SSE delivery: atomic event subscription with a guaranteed
  terminal event; listeners that connect mid-run or late never miss the
  final report.
- Live investigations survive transient stream drops: the renderer
  reattaches with backoff, probes status, and cancels cleanly once the
  retry budget is spent. The status line keeps a working stage instead of
  stalling mid-report.
- The in-memory job store evicts finished runs after 100 entries, so
  long sessions no longer grow without bound.
- A cancelled screenshot capture now always disposes its pending window
  image, even when the branch merge never runs.

Backend correctness

- Model-provided report excerpts are capped (400 lines / 8k chars),
  including in findings, so a single block can no longer bloat the
  report or a manual review.
- Malformed model output with the wrong JSON shape (findings that are
  not a list, and friends) now enters the repair loop instead of being
  displayed as "a report".
- Boolean fields and investigation type mapping cannot be fooled by
  creative strings; unknowns fall back to safe defaults.
- The fixer preserves file encoding and line endings end to end;
  repeated fixes rotate the backup once so the first backup stays the
  true original.

Security and trust

- The results window now ships the same strict CSP as the character
  window.
- The results header states when the verdict was issued (provenance
  stamp with exact UTC time on hover).

Interface

- Result tabs follow the WAI-ARIA tabs pattern: roving tabindex,
  Left/Right/Home/End keys, and full tab/panel labelling.
- Finding snippets and the proposed diff carry context into the
  clipboard: excerpts copy with their location line, evidence copies
  with its source, and the full diff copies raw.
- Visible keyboard focus rings for tabs, copy and action buttons.
- History search is debounced; follow-up inputs are labelled for screen
  readers; status, phase and verdict lines announce changes.
- The character window opens from the keyboard (the face is a button:
  Enter or Space; Escape returns focus to the face).
