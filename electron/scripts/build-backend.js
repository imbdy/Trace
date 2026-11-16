#!/usr/bin/env node
// Cross-platform PyInstaller build of the bundled backend (trace-backend).
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const out = path.join(__dirname, "..", "resources", "backend");
fs.mkdirSync(out, { recursive: true });

function run(cmd, args) {
  const r = spawnSync(cmd, args, { stdio: "inherit", cwd: root, shell: process.platform === "win32" });
  if (r.error) throw r.error;
  if (r.status !== 0) process.exit(r.status || 1);
}

const py = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const probe = spawnSync(py, ["-m", "PyInstaller", "--version"], { stdio: "ignore", shell: process.platform === "win32" });
if (probe.status !== 0) {
  console.error("PyInstaller is required. Install it with: " + py + " -m pip install pyinstaller");
  process.exit(1);
}

run(py, [
  "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
  "--name", "trace-backend",
  "--distpath", out,
  "--workpath", path.join(out, ".build"),
  "--specpath", path.join(out, ".build"),
  path.join(root, "trace_backend", "standalone.py"),
]);
console.log("Built bundled backend in " + out);
