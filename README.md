# Trace

Trace is an evidence-first Windows desktop debugging console. Describe a bug,
let Trace collect bounded project evidence, and receive a report whose file
paths and line numbers are checked against the files on disk.

Current release: **0.4.3**.

## What Trace does

Trace combines a floating Electron interface with a local Python backend that
can:

- capture a screenshot when requested by an investigation;
- inspect a selected project with bounded, redacted reads;
- extract traceback and error locations;
- run safe, allowlisted diagnostics;
- ask a configured language model for analysis;
- verify every cited path and line against current files;
- separate verified findings, hypotheses, evidence, and warnings;
- propose a unified diff and optionally apply it after verification and backup.

A model suggestion is not treated as a confirmed finding until the backend can
verify it.

## Interface

1. Open the Trace panel and enter a bug report, traceback, or question.
2. Choose **Debug** for an evidence-backed diagnosis or **Explain** for a plain
   explanation without a proposed fix.
3. Optionally enable **Fix it for me**. Trace writes only after its verification
   gate passes and a `.trace-bak` backup is created.
4. Watch the five-stage activity rail: capture, inspect, analyze, verify, report.
5. Open the results window for **Answer**, **Code & Diff**, and **Evidence**.

The compact chat icon opens a normal conversation in the Trace window. Chat does
not read project files, run diagnostics, or claim that anything was verified.
The gear icon opens **Model connection**. The tray menu can show Trace, open
results, check for updates, and quit. **Tuck away** hides the panel while
leaving Trace available from the tray.

## Model connections

Trace supports Ollama and OpenAI-compatible APIs, including OpenAI, OpenRouter,
Groq, Together, local gateways, and compatible custom services.

Open the gear icon and enter:

| Field | Meaning |
|---|---|
| Provider | Ollama or OpenAI-compatible API |
| Model | Exact model identifier accepted by the provider |
| API base | Provider base URL, such as `https://api.openai.com/v1` |
| API key | Provider bearer token, when required |

The API key travels through the Electron main process to the loopback backend;
the renderer does not receive it. On supported systems, Trace persists the
provider profile using Electron encrypted storage. The key field is cleared
after saving and only a redacted status is shown.

Saving a connection does not make a test generation request. An invalid
endpoint, model, or key is reported when the first chat or investigation runs.

## Install the Windows release

Download the latest installer from the
[GitHub Releases page](https://github.com/imbdy/Trace/releases). The packaged
installer includes the Python backend, so Python is not required for normal use.

After installation, start Trace, open the gear icon, configure a provider, and
submit an investigation.

For local Ollama, install [Ollama](https://ollama.com), start it, and pull a
model:

```powershell
ollama serve
ollama pull gemma4:31b-cloud
```

`gemma4:31b-cloud` is an Ollama cloud model. It is not local-only; submitted
context may be processed by Ollama's cloud service. Configure a local model if
the project cannot leave the machine.

## Run from source

Requirements: Windows for the Electron experience, Node.js 20+, and Python
3.10+.

```powershell
git clone https://github.com/imbdy/Trace.git
cd Trace\electron
npm install
npm start -- --project-dir C:\path\to\your\project
```

The Electron app supervises the Python backend. To run the backend directly:

```powershell
cd C:\path\to\Trace
pip install -e ".[screenshots]"
python main.py --backend-only
```

The backend binds to `http://127.0.0.1:3777` by default. The fallback Python UI
can be started with `python main.py`, or with `python -m trace_ui` when a
backend is already running.

## Investigation pipeline

The backend runs a bounded agent graph:

```text
receive → screenshot → context → project_root → error_extract
        → read_files → diagnostics → model_analysis → validate → report
```

The visible rail groups those stages into capture, inspect, analyze, verify,
and report. Collection branches are bounded and isolated so a slow or missing
diagnostic does not silently become model evidence. Explain mode skips traceback
error extraction and does not propose a code fix.

Reports can include a summary and root cause, verified findings with file/line/
excerpt/evidence/confidence, alternative hypotheses, missing information, a
proposed diff, warnings, collected evidence, the graph path, and provenance.

Follow-up questions from a completed result create a linked investigation and
re-check the cited files.

## Safety and privacy

- The backend accepts loopback connections only; non-loopback bind addresses
  are rejected.
- The renderer cannot access Node.js, Electron, the filesystem, or model APIs
  directly; it uses a narrow preload bridge.
- Project scanning is bounded and excludes common generated, dependency, cache,
  and virtual-environment directories.
- Common API-key, bearer-token, GitHub-token, and Slack-token patterns are
  redacted from model context.
- Screenshots are held in memory and a temporary file during a run; the
  temporary file is removed afterward.
- Auto-fix is failure-closed: no verified diff or no backup means no write.
  Writes are restricted to the project root and use atomic replacement.
- Project scripts are not executed. Test output is opt-in and limited to the
  allowlisted runner detected by Trace.
- Provider privacy and billing follow the selected provider. Cloud models may
  process submitted code, screenshots, and logs.

## Configuration

Desktop users should use Model connection. Environment variables remain useful
for backend-only, CI, and source workflows:

| Variable | Default | Purpose |
|---|---|---|
| `TRACE_PROVIDER` | `ollama` | `ollama` or `openai-compatible` |
| `TRACE_MODEL` | `gemma4:31b-cloud` | Model identifier |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama endpoint |
| `OLLAMA_API_KEY` | unset | Ollama bearer token |
| `TRACE_API_BASE` | unset | OpenAI-compatible API base URL |
| `TRACE_BIND_HOST` | `127.0.0.1` | Backend bind address; loopback only |
| `TRACE_BIND_PORT` | `3777` | Backend port |
| `TRACE_CONNECT_TIMEOUT_S` | `5` | Connection timeout |
| `TRACE_READ_TIMEOUT_S` | `120` | Model response timeout |
| `TRACE_ALLOW_FILE_EDITS` | `1` | Set `0` to disable writes |
| `TRACE_ALLOW_TESTS` | `0` | Set `1` to allow the safe test runner |
| `TRACE_HISTORY` | `1` | Set `0` to disable the investigation journal |
| `TRACE_HISTORY_MAX` | `500` | Maximum journal entries |
| `TRACE_HISTORY_DIR` | platform default | Report journal location |
| `TRACE_PYTHON` | `python` | Python executable used by Electron |

Useful Electron flags are `--project-dir DIR`, `--backend-port N`,
`--user-data-dir DIR`, and `--smoke`.

## Known limitations

- OpenAI-compatible provider calls currently use the non-streaming chat path.
- Provider settings are saved without a test generation request.
- Results history displays the newest 50 entries; the backend journal can retain
  more according to `TRACE_HISTORY_MAX`.
- `Open at line N` delegates to the OS default file handler; line jumping
  depends on editor integration.
- The backend supports dry-run fix responses, but the desktop UI does not yet
  expose a separate dry-run preview button.
- `.trace-bak` files are not cleaned up automatically.
- The published installer currently targets Windows.

## Backend API

The Electron app talks to the loopback API under `/api/v1`:

```text
GET  /health
GET  /config
POST /config
POST /investigations
GET  /investigations
GET  /investigations/{id}
GET  /investigations/{id}/events
POST /investigations/{id}/cancel
POST /investigations/{id}/follow-up
POST /investigations/{id}/apply-fix
POST /open-file
POST /chat
```

See [docs/API_CONTRACT.md](docs/API_CONTRACT.md) for request and response
shapes.

## Development

```powershell
# Python tests
python -m pytest -q

# Electron build and tests
cd electron
npm run build
npm test

# Package a Windows installer
npm run dist:win
```

Repository layout:

```text
trace_backend/          loopback API, model client, agent graph, validation
trace_ui/               dependency-light Python fallback UI
electron/src/main/      window, tray, backend supervisor, IPC
electron/src/renderer/  Trace panel and results workspace
tests/                  Python contracts and integration tests
electron/test/          Electron utility tests
docs/                   API and architecture references
```

Layering and frontend-boundary tests protect the separation between the
renderer, Electron main process, backend, evidence collection, and model client.

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the current release summary and
[CHANGELOG.md](CHANGELOG.md) for version history.
