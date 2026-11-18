#!/usr/bin/env node
// Cross-platform PyInstaller build of the bundled backend (trace-backend).
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const out = path.join(__dirname, "..", "resources", "backend");
fs.mkdirSync(out, { recursive: true });

function run(cmd, args) {
  const r = spawnSync(cmd, args, { stdio: "inherit", cwd: root, windowsHide: true });
  if (r.error) throw r.error;
  if (r.status !== 0) process.exit(r.status || 1);
}

function resolvePython() {
  const fromEnv = (process.env.PYTHON || "").trim();
  const extra = (process.env.PYTHON_ARGS || "").trim();
  const extraArgs = extra ? extra.split(/\s+/).filter(Boolean) : [];
  if (fromEnv) {
    // Prefer PYTHON as the executable; use PYTHON_ARGS for additional arguments.
    return { cmd: fromEnv, argsPrefix: extraArgs };
  }
  return { cmd: process.platform === "win32" ? "python" : "python3", argsPrefix: extraArgs };
}

const py = resolvePython();
const probe = spawnSync(py.cmd, [...py.argsPrefix, "-m", "PyInstaller", "--version"], {
  stdio: "ignore",
  cwd: root,
  windowsHide: true,
});
if (probe.status !== 0) {
  console.error("PyInstaller is required. Install it with: " + [py.cmd, ...py.argsPrefix].join(" ") + " -m pip install pyinstaller");
  process.exit(1);
}

run(py.cmd, [
  ...py.argsPrefix,
  "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
  "--name", "trace-backend",
  "--distpath", out,
  "--workpath", path.join(out, ".build"),
  "--specpath", path.join(out, ".build"),
  path.join(root, "trace_backend", "standalone.py"),
]);
console.log("Built bundled backend in " + out);
