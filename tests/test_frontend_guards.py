"""Guards for the Electron frontend (Neo design transplant).

- Renderer files are plain scripts behind the window.trace bridge: no
  Electron/Node imports, no backend URLs, no model access.
- No file under electron/src may integrate with Neoma or talk to Ollama
  directly (no /api/chat, /api/generate, :11434); the Python backend owns
  all model communication, and Trace never touches Neoma.
- Fidelity: hearthling.js is Neo's face renderer modulo documented renames.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "electron" / "src"
NEO_FACE = pathlib.Path(r"C:\Users\abood\PycharmProjects\neoma\Neo\renderer\hearthling.js")

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
                    "renderer/neo/hearthling.js", "renderer/neo/leaf.js",
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
    def test_face_is_neos_verbatim(self):
        """hearthling.js == Neo's face renderer modulo documented renames."""
        neo = NEO_FACE.read_text(encoding="utf-8")
        neo = neo.replace("window.Hearthling = api;", "window.TraceFace = api;")
        # header line replaced by transplant note; drop line 1 on both sides
        neo_body = neo.splitlines()[1:]
        trace_body = (SRC / "renderer/neo/hearthling.js").read_text(
            encoding="utf-8").splitlines()[1:]
        self.assertEqual(trace_body, neo_body)


if __name__ == "__main__":
    unittest.main()
