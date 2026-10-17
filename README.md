# Trace — Independent Debugging Backend

Trace is a standalone desktop debugging companion. It captures a screenshot,
collects local context, inspects project code, runs available diagnostics,
asks an Ollama model for analysis, **verifies every file path/line number
against disk**, and returns a structured report over a documented loopback API.

Absolute independence: no Neoma connection, no session monitoring, no approval
workflows, no reuse of external endpoints. Works when writing code alone.

## Architecture (existing → new)

**Existing repo:** empty PyCharm template (`main.py` hello-world, empty
`pyproject.toml`). No backend, no frontend, no infrastructure to preserve.

**New modules (`trace_backend/`):**

| Module | Responsibility (kept separate per spec) |
|---|---|
| `config.py` | Env-based config; loopback enforcement; redacted API view |
| `schemas.py` | Typed request/report/event dataclasses + JSON |
| `ollama_client.py` | ONLY model communication: timeouts, streaming, cancellation, user-facing errors. Model `gemma4:31b-cloud`, default `OLLAMA_HOST=http://127.0.0.1:11434` |
| `screenshot_service.py` | Active/full capture (mss → Pillow), temp-file lifecycle, graceful failure |
| `context_collector.py` | Active app/title (Win32 best-effort), editor selection, clipboard **only when authorized**, terminal tail |
| `project_inspector.py` | Root detection, safe reads (allowlist + excludes), import following, error parsing, allowlisted diagnostics, budgeted redacted model bundle |
| `validator.py` | Verifies every cited path/line; labels unverified, never invents |
| `investigation_engine.py` | 10-stage pipeline orchestrating the above |
| `server.py` | Loopback-only HTTP API + SSE + cancel + open-file |

Pipeline: `receive → screenshot → context → project_root → error_extract →
read_files → diagnostics → model_analysis → validate → report`.

**Desktop frontend (`electron/`, primary):** Electron + TypeScript + HTML/CSS —
character bar (`src/renderer/neo/`), results window
(`src/renderer/results.*`), main-process backend supervisor + IPC
(`src/main/`), isolated preload bridge (`src/preload.ts`). The system is kept
separated into the five components in `docs/ARCHITECTURE.md` — desktop
character → orchestrator → Ollama `gemma4:31b-cloud` → evidence verification
→ report window. `trace_ui/` (tkinter) is retained as a zero-dependency
fallback: `python main.py` (`--backend-only` for the server alone).
Layering is enforced by `tests/test_layering.py` + `tests/test_frontend_guards.py`.

```powershell
cd electron
npm install
npm run build
npx electron . --project-dir C:\path\to\your\project
```

The app spawns `python main.py --backend-only` itself (loopback only) and
reuses a running backend if one answers health. Requirements: Node 20+,
Python visible as `python` (or set `TRACE_PYTHON`), and
`ollama serve` + `ollama pull gemma4:31b-cloud` for real model analysis.

## Setup

Requires Python ≥ 3.10 (`requires-python` in `pyproject.toml`); CI builds on 3.12.

```powershell
cd Trace
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[screenshots]"
```

Optional screenshot backends: `pip install mss pillow`. Without them,
screenshot capture fails gracefully and investigations continue with
code/diagnostics only.

Ollama:

```powershell
ollama serve
ollama pull gemma4:31b-cloud
```

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama endpoint |
| `TRACE_MODEL` | `gemma4:31b-cloud` | Model name |
| `OLLAMA_API_KEY` | _(unset)_ | Bearer token if endpoint needs auth; never hardcoded |
| `TRACE_BIND_HOST` | `127.0.0.1` | Must stay loopback |
| `TRACE_BIND_PORT` | `3777` | Local port |
| `TRACE_CONNECT_TIMEOUT_S` / `TRACE_READ_TIMEOUT_S` | `5` / `120` | Timeouts |
| `TRACE_ALLOW_FILE_EDITS` | `1` | `0` disables all auto-fix / apply-fix writes |
| `TRACE_ALLOW_TESTS` | `0` | `1` opts in to allowlisted test-runner output |

## Run the backend

```powershell
python -m trace_backend          # serve on http://127.0.0.1:3777
python main.py                   # same (launcher)
```

Health check: `GET /api/v1/health`. Full contract: `docs/API_CONTRACT.md`.

Minimal client:

```powershell
curl -X POST http://127.0.0.1:3777/api/v1/investigations `
  -H "Content-Type: application/json" `
  -d '{"request_text":"App crashes on start","project_dir":"C:/path/to/project","include_screenshot":true}'
# → {"investigation_id":"inv-..."}
curl http://127.0.0.1:3777/api/v1/investigations/inv-...
curl -N http://127.0.0.1:3777/api/v1/investigations/inv-.../events  # SSE
```

## Privacy & security

- Server binds **loopback only** (`127.0.0.1`); non-loopback bind is rejected.
- **Cloud disclosure:** `gemma4:31b-cloud` may process submitted context through **Ollama's cloud service** — it is NOT local-only. The backend minimises payload (24k-char budget, screenshot downscale, tail-only logs) and redacts secret-like patterns, but review what you submit.
- Clipboard read only when `include_clipboard: true`.
- Screenshots live in memory + one temp file, deleted after analysis (`dispose()` / `temporary_capture()`); never retained.
- Excluded from scanning: `.git`, `node_modules`, `__pycache__`, `.venv`/`venv`, `build`/`dist`/`target`, caches, secrets.
- Diagnostics run only allowlisted installed tools (`py_compile`, `ruff`, `mypy`, `tsc`, `eslint`, `cargo check`, `go vet`, `dotnet build`) with timeouts. Project scripts are never executed.
- Safe auto-fix: ask with `auto_fix: true` (or tick "Fix it for me") and Trace applies the VERIFIED patch straight to the file — with a `*.trace-bak` backup per file — instead of popping a results window. Unverified patches are never written (review-only + `Apply fix` button stays disabled). Disable all edits with `TRACE_ALLOW_FILE_EDITS=0`.
- Code intelligence: symbol search, file outlines (AST for Python), complexity notes, and opt-in test output (`run_tests: true` + `TRACE_ALLOW_TESTS=1`, allowlisted runners only) feed the model real definitions instead of guesses.

## Limitations

- Active-window capture is best-effort (cursor-monitor fallback on Windows); minimise Trace itself before capture.
- Vision requires a multimodal-capable Ollama setup; otherwise analysis uses code/diagnostics + error text.
- Model verdicts are **not assumed correct**: confidence + evidence + unverified labels; unknown project → asks for folder instead of inventing.
- `open-file` line navigation depends on OS/editor integration (best-effort).
- Language diagnostics depend on tools installed on the machine.

## Desktop releases

Windows installers are published from tags such as `v0.4.1` at
[github.com/imbdy/Trace/releases](https://github.com/imbdy/Trace/releases).
The installer includes the loopback backend, so Python is not required on the
user's machine. Installed copies check GitHub Releases in the background and
download updates automatically; the update is applied when Trace restarts.

To publish a release after changing the Electron version in
`electron/package.json`:

```powershell
git tag v0.4.1
git push origin v0.4.1
```

The GitHub Actions workflow builds the backend, creates the Windows NSIS
installer, and publishes the release assets.

## Tests

```powershell
python -m unittest discover -s tests -v
```

For the desktop renderer, run `npm test` from `electron` as well.

Covers: file/line verification, report parsing/malformed output, Ollama
failures (refused/timeout/auth/model-missing/cancel/streaming), pipeline
cancellation, missing-project honesty, screenshot temp cleanup, inspector
safety (traversal/excludes/secrets), server loopback + start/poll flow.
