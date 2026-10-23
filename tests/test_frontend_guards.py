"""Guards for the Electron frontend (Neo design transplant).

- Renderer files are plain scripts behind the window.trace bridge: no
  Electron/Node imports, no backend URLs, no model access.
- No file under electron/src may integrate with Neoma or talk to Ollama
  directly (no /api/chat, /api/generate, :11434); the Python backend owns
  all model communication, and Trace never touches Neoma.
- Fidelity: hearthling.js is Neo's face renderer modulo documented renames.
  This check needs the sibling Neoma checkout and is skipped unless
  TRACE_NEO_ROOT (or TRACE_NEO_FACE) points at it.
"""

import os
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "electron" / "src"
# Sibling Neoma checkout that hearthling.js was transplanted from. Point at it
# with TRACE_NEO_ROOT (or TRACE_NEO_FACE for a single file) to re-run the
# fidelity check; the test skips when neither is available so the suite runs
# on machines without that checkout.
NEO_ROOT = os.environ.get("TRACE_NEO_ROOT")
NEO_FACE = pathlib.Path(
    os.environ["TRACE_NEO_FACE"] if os.environ.get("TRACE_NEO_FACE")
    else (str(pathlib.Path(NEO_ROOT) / "Neo" / "renderer" / "hearthling.js")
          if NEO_ROOT else
          pathlib.Path.home() / "PycharmProjects" / "neoma" / "Neo"
          / "renderer" / "hearthling.js")
)
HAS_NEO_SOURCE = NEO_FACE.is_file()

NEOMA_SIGNAL = re.compile(
    r"^\s*(import|from)\s+neoma|neoma\s*\.\s*\w|neoma[-_]?(api|client|session)|"
    r"https?://\S*neoma|NEOMA_PORT|openNeoma",
    re.IGNORECASE | re.MULTILINE,
)
OLLAMA_SIGNAL = re.compile(
    r"/api/(chat|generate)|127\.0\.0\.1:11434|localhost:11434|"
    r"from ['\"]ollama['\"]|require\(['\"]ollama['\"]\)|new\s+Ollama\s*\("
)


def code_files():
    return [f for f in SRC.rglob("*") if f.suffix in (".ts", ".js")]


class TestFrontendGuards(unittest.TestCase):
    def test_sources_exist(self):
        for rel in ("main/main.ts", "main/backend.ts", "main/traceApi.ts",
                    "main/placement.js", "main/settings.js",
                    "preload.ts",
                    "renderer/neo/index.html", "renderer/neo/style.css",
                    "renderer/neo/hearthling.js", "renderer/neo/face-preview.html",
                    "renderer/neo/face-preview-frame.html", "renderer/neo/face-preview.css",
                    "renderer/neo/face-preview-frame.js", "renderer/neo/leaf.js",
                    "renderer/neo/shell.js", "renderer/neo/trace.js",
                    "renderer/results.html", "renderer/results.css",
                    "renderer/results.ts", "shared/protocol.ts"):
            self.assertTrue((SRC / rel).is_file(), rel)

    def test_no_neoma_integration(self):
        hits = [str(f.relative_to(ROOT)) for f in code_files()
                if NEOMA_SIGNAL.search(f.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])

    def test_no_direct_ollama_access(self):
        hits = [str(f.relative_to(ROOT)) for f in code_files()
                if OLLAMA_SIGNAL.search(f.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [], "only the Python backend may call Ollama")

    def test_renderer_isolation(self):
        for name in ("renderer/neo/hearthling.js", "renderer/neo/leaf.js",
                     "renderer/neo/shell.js", "renderer/neo/trace.js",
                     "renderer/results.ts"):
            text = (SRC / name).read_text(encoding="utf-8")
            self.assertNotIn("from \"electron\"", text, name)
            self.assertNotIn("from 'electron'", text, name)
            self.assertNotIn("require(\"electron\")", text, name)
            self.assertNotIn("require('electron')", text, name)
        for name in ("renderer/neo/leaf.js", "renderer/neo/shell.js",
                     "renderer/neo/trace.js", "renderer/results.ts"):
            text = (SRC / name).read_text(encoding="utf-8")
            self.assertIn("window.trace", text, name)

    def test_cancel_is_reachable_through_the_driver(self):
        """Cancel must be driven by TraceDriver, not the bridge directly.

        `#ctl-cancel` ships disabled and the ONLY thing that ever enables it is
        TraceShellCtl.setAskEnabled(false) — called solely from
        TraceDriver.setRunning(true). A shell that calls window.trace.cancel
        itself never flips `running`, so the button stays disabled and a live
        investigation cannot be stopped from the UI at all.
        """
        shell = (SRC / "renderer/neo/shell.js").read_text(encoding="utf-8")
        driver = (SRC / "renderer/neo/trace.js").read_text(encoding="utf-8")
        self.assertIn("window.TraceDriver", shell,
                      "shell.js must reach the driver to flip the running state")
        self.assertRegex(
            shell, r"wire\('ctl-cancel'[\s\S]{0,120}?doCancel\(\)",
            "the cancel control must route through the driver")
        self.assertRegex(
            shell, r"doSubmit\(\s*text\s*\)",
            "submit must route through the driver so the busy-guard applies")
        # the driver is what actually owns `running` for both buttons
        self.assertRegex(driver, r"setAskEnabled\(!on\)",
                         "setRunning must toggle ask/cancel via the shell control")

    def test_renderer_scripts_parse(self):
        """Every renderer script must parse: a syntax error kills the whole
        IIFE file silently (no shell = no expansion, no stitch, no verdict)."""
        import shutil
        import subprocess
        node = shutil.which("node")
        self.assertTrue(node, "node required for renderer parse check")
        for name in ("renderer/neo/hearthling.js", "renderer/neo/leaf.js",
                     "renderer/neo/shell.js", "renderer/neo/trace.js"):
            proc = subprocess.run(
                [node, "--check", str(SRC / name)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, f"{name}: {proc.stderr[-500:]}")

    def test_renderer_scripts_are_import_free(self):
        """Plain <script> JS (no CJS/ESM boilerplate): runs over file://
        with nodeIntegration disabled."""
        for name in ("renderer/neo/hearthling.js", "renderer/neo/leaf.js",
                     "renderer/neo/shell.js", "renderer/neo/trace.js"):
            text = (SRC / name).read_text(encoding="utf-8")
            code = "\n".join(
                l for l in text.splitlines()
                if not l.strip().startswith("//"))
            self.assertIsNone(
                re.search(r"^\s*import\s+", code, re.MULTILINE), name)
            self.assertIsNone(
                re.search(r"^\s*export\s+", code, re.MULTILINE), name)
            self.assertNotIn("require(", code, name)


class TestNeoFidelity(unittest.TestCase):
    # Documented Trace patches to Neo's face renderer. The original renderer
    # remains the source of the body, eye, badge, particle, and animation
    # primitives; Trace adds a small expressive layer around those primitives.
    FACE_PATCHES = [
        ("window.Hearthling = api;", "window.TraceFace = api;"),
        ("    const rx = R * 1.10, ry = R * 0.92; // compact and wide: he sits, not floats",
         "    const rx = R * 1.10 * 0.78, ry = R * 0.92; // compact and wide (Trace narrowing): he sits, not floats"),
    ]

    @unittest.skipUnless(HAS_NEO_SOURCE,
                         f"Neoma source not available at {NEO_FACE} "
                         "(set TRACE_NEO_ROOT or TRACE_NEO_FACE to enable)")
    def test_face_is_neos_verbatim(self):
        """Core face primitives stay byte-identical to Neo's renderer."""
        neo = NEO_FACE.read_text(encoding="utf-8")
        for old, new in self.FACE_PATCHES:
            self.assertIn(old, neo, "Neo source changed upstream?")
            neo = neo.replace(old, new)
        trace = (SRC / "renderer/neo/hearthling.js").read_text(encoding="utf-8")

        def function_block(text, name):
            start = text.index("function " + name)
            brace = text.index("{", start)
            depth = 0
            for i in range(brace, len(text)):
                if text[i] == "{": depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0: return text[start:i + 1]
            self.fail("unclosed function: " + name)

        for name in ("rr", "drawEyeShape", "drawBadge", "drawSparks"):
            self.assertEqual(function_block(trace, name), function_block(neo, name), name)

        for name in ("idle", "working", "thinking", "listening", "success",
                     "warning", "error", "backend_unavailable", "cancelled",
                     "sleepy"):
            self.assertRegex(trace, r"\b" + name + r"\s*:")
        # the retired moods must stay retired: one first face + thinking dots
        for gone in ("curious", "skeptical", "confused", "investigating",
                     "reading", "writing", "verifying"):
            self.assertNotRegex(trace, r"\b" + gone + r"\s*:")
        # the thinking face: three pulsing dots, cross-faded, no spark show
        self.assertIn("drawThoughtDots", trace)
        self.assertIn("dotsA", trace)
        self.assertNotIn("emitSparks(5)", trace)
        self.assertIn("expressions: EXPRESSION_NAMES.slice()", trace)
        self.assertIn("setExpression", trace)
        # the retired 'excited' name froze him mid-think once: the driver must
        # only ever pass creature names the face actually defines
        driver = (SRC / "renderer/neo/trace.js").read_text(encoding="utf-8")
        self.assertNotRegex(trace + "\n" + driver, r"['\"]excited['\"]")


if __name__ == "__main__":
    unittest.main()
