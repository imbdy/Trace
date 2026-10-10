"""Guards for the Electron frontend: isolation + independence.

- Renderer files must not import Electron/Node or reach the backend directly;
  they only use the window.traceAPI bridge (see electron/src/preload.ts).
- No file under electron/src may integrate with Neoma or talk to Ollama
  directly (no /api/chat, /api/generate, :11434); the Python backend owns
  all model communication.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "electron" / "src"

NEOMA_SIGNAL = re.compile(
    r"^\s*(import|from)\s+neoma|neoma\s*\.\s*\w|neoma[-_]?(api|client|session)|"
    r"https?://\S*neoma",
    re.IGNORECASE | re.MULTILINE,
)
OLLAMA_SIGNAL = re.compile(
    r"/api/(chat|generate)|127\.0\.0\.1:11434|localhost:11434|"
    r"from ['\"]ollama['\"]|require\(['\"]ollama['\"]\)|new\s+Ollama\s*\("
)


class TestFrontendGuards(unittest.TestCase):
    def test_sources_exist(self):
        for rel in ("main/main.ts", "main/backend.ts", "main/traceApi.ts",
                    "preload.ts", "renderer/character.ts",
                    "renderer/results.ts", "shared/protocol.ts"):
            self.assertTrue((SRC / rel).is_file(), rel)

    def test_no_neoma_integration(self):
        hits = [str(f.relative_to(ROOT)) for f in SRC.rglob("*.ts")
                if NEOMA_SIGNAL.search(f.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])

    def test_no_direct_ollama_access(self):
        hits = [str(f.relative_to(ROOT)) for f in SRC.rglob("*.ts")
                if OLLAMA_SIGNAL.search(f.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [], "only the Python backend may call Ollama")

    def test_renderer_isolation(self):
        for name in ("renderer/character.ts", "renderer/results.ts"):
            text = (SRC / name).read_text(encoding="utf-8")
            self.assertNotIn("from \"electron\"", text, name)
            self.assertNotIn("from 'electron'", text, name)
            self.assertNotIn("require(\"electron\")", text, name)
            self.assertIn("window.traceAPI", text, name)

    def test_renderer_is_import_free(self):
        """Renderer files compile to plain <script> JS (no CJS boilerplate),
        so they run over file:// with nodeIntegration disabled."""
        import re
        for name in ("renderer/character.ts", "renderer/results.ts"):
            text = (SRC / name).read_text(encoding="utf-8")
            code = "\n".join(
                l for l in text.splitlines()
                if not l.strip().startswith("//"))
            self.assertIsNone(
                re.search(r"^\s*import\s+", code, re.MULTILINE), name)
            self.assertIsNone(
                re.search(r"^\s*export\s+", code, re.MULTILINE), name)
            self.assertNotIn("require(", code, name)


if __name__ == "__main__":
    unittest.main()
