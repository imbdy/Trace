"""Enforced layering test: the 5-component architecture.

Components (see docs/ARCHITECTURE.md):
  1. Desktop character .... trace_ui/character.py, trace_ui/companion.py
  2. Orchestrator .......... trace_backend/investigation_engine.py (+server hosting)
  3. Ollama brain .......... trace_backend/ollama_client.py
  4. Evidence verification  trace_backend/validator.py
  5. Report window ......... trace_ui/result_model.py, trace_ui/results_window.py
  Evidence collectors ...... screenshot_service, context_collector, project_inspector
  Transport ................ trace_ui/api_client.py (stdlib HTTP only)
  Composition roots ........ trace_ui/app.py, main.py (ONLY places allowed to
                             bridge UI and backend for startup wiring)

Uses AST so function-level imports are checked too. No GUI is created.
"""

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STDLIB_TOP = {
    "argparse", "ast", "base64", "concurrent", "contextlib", "copy",
    "ctypes", "dataclasses", "datetime", "enum", "functools", "hashlib",
    "hmac", "html", "http", "io", "itertools", "json", "logging",
    "math", "os", "pathlib", "queue", "random", "re", "shutil", "signal",
    "socket", "sqlite3", "statistics", "string", "subprocess", "sys",
    "tempfile", "textwrap", "threading", "time", "tkinter", "traceback",
    "typing", "urllib", "uuid", "warnings", "weakref",
    "__future__",
}


def file_imports(path: pathlib.Path) -> tuple[set[str], set[str]]:
    """Return (absolute_top_levels, relative_modules) imported by a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    abs_mods: set[str] = set()
    rel_mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                abs_mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                rel_mods.add(node.module or "")
                for a in node.names:
                    if not node.module:
                        rel_mods.add(a.name)
            elif node.module:
                abs_mods.add(node.module.split(".")[0])
    return abs_mods, rel_mods


def check_file(rel: str, *, allow_abs: set[str] = frozenset(),
               allow_rel: set[str] = frozenset(),
               forbid_abs: set[str] = frozenset(),
               forbid_rel: set[str] = frozenset(),
               message: str = "") -> str | None:
    path = ROOT / rel
    abs_mods, rel_mods = file_imports(path)
    third_party_abs = {m for m in abs_mods
                       if m not in STDLIB_TOP and m not in allow_abs}
    bad_rel = {m for m in rel_mods if m not in allow_rel}
    bad_forbid = ({m for m in abs_mods if m in forbid_abs}
                  | {m for m in rel_mods if m in forbid_rel})
    problems = []
    if third_party_abs:
        problems.append(f"third-party/external imports {sorted(third_party_abs)}")
    if bad_rel:
        problems.append(f"sibling imports {sorted(bad_rel)}")
    if bad_forbid:
        problems.append(f"forbidden imports {sorted(bad_forbid)}")
    if problems:
        return f"{rel} [{message}]: " + "; ".join(problems)
    return None


class TestLayering(unittest.TestCase):
    def test_backend_never_imports_ui(self):
        violations = []
        for f in (ROOT / "trace_backend").rglob("*.py"):
            abs_mods, rel_mods = file_imports(f)
            if "trace_ui" in abs_mods:
                violations.append(str(f))
            if f.name != "context_collector.py" and "tkinter" in abs_mods:
                violations.append(f"{f}: tkinter outside clipboard reader")
        self.assertEqual(violations, [])

    def test_model_brain_is_standalone(self):
        self.assertIsNone(check_file(
            "trace_backend/ollama_client.py",
            allow_abs={"trace_backend"}, allow_rel={"config"},
            forbid_rel={"screenshot_service", "context_collector",
                        "project_inspector", "validator",
                        "investigation_engine", "server", "schemas"},
            message="L3 Ollama client must not touch evidence/UI/orchestrator"))

    def test_verification_is_downstream_only(self):
        self.assertIsNone(check_file(
            "trace_backend/validator.py",
            allow_abs={"trace_backend"},
            allow_rel={"project_inspector", "schemas"},
            forbid_rel={"ollama_client", "screenshot_service",
                        "context_collector", "investigation_engine", "server"},
            message="L4 validator must not call the model or orchestrator"))

    def test_orchestrator_does_not_import_server(self):
        self.assertIsNone(check_file(
            "trace_backend/investigation_engine.py",
            allow_abs={"trace_backend"},
            allow_rel={"agent_graph", "config", "context_collector",
                       "ollama_client", "project_inspector", "schemas",
                       "screenshot_service", "validator", "git_context",
                       "code_fixer", "code_tools"},
            forbid_rel={"server", "history"},
            message="L2 orchestrator must not depend on HTTP hosting"))

    def test_server_is_thin_hosting(self):
        self.assertIsNone(check_file(
            "trace_backend/server.py",
            allow_abs={"trace_backend"},
            allow_rel={"config", "history", "investigation_engine", "schemas",
                       "chat"},
            forbid_rel={"ollama_client", "screenshot_service",
                        "project_inspector", "context_collector", "validator",
                        "code_fixer", "code_tools"},
            message="server hosts the engine; evidence/model stay behind it"))

    def test_code_tools_are_downstream_helpers(self):
        self.assertIsNone(check_file(
            "trace_backend/code_tools.py",
            allow_abs={"trace_backend"},
            allow_rel={"project_inspector"},
            forbid_rel={"ollama_client", "investigation_engine", "server",
                        "validator", "history"},
            message="code tools are safe helpers; no model/server coupling"))

    def test_code_fixer_is_grounded(self):
        self.assertIsNone(check_file(
            "trace_backend/code_fixer.py",
            allow_abs={"trace_backend"},
            allow_rel={"project_inspector", "validator", "schemas"},
            forbid_rel={"ollama_client", "investigation_engine", "server",
                        "context_collector", "screenshot_service", "history"},
            message="fixer may only ground diffs against disk"))

    def test_history_journal_is_stdlib_only(self):
        # The journal is dict-based on purpose: it must survive without the
        # schema layer (or any other Trace component) so a stored verdict can
        # always be read back.
        self.assertIsNone(check_file(
            "trace_backend/history.py",
            message="history must not import schemas/engine/server"))

    def test_transport_is_stdlib_only(self):
        self.assertIsNone(check_file(
            "trace_ui/api_client.py",
            message="transport must not import character/UI/backend code"))

    def test_character_logic_is_headless(self):
        for rel in ("trace_ui/character.py", "trace_ui/result_model.py"):
            self.assertIsNone(check_file(rel, message="pure logic, stdlib only"))

    def test_companion_only_sees_character(self):
        self.assertIsNone(check_file(
            "trace_ui/companion.py",
            allow_rel={"character"},
            forbid_rel={"api_client", "result_model", "results_window", "app"},
            message="L1 character must not touch network/report/backend"))

    def test_report_window_only_sees_view_model(self):
        self.assertIsNone(check_file(
            "trace_ui/results_window.py",
            allow_rel={"result_model"},
            forbid_rel={"api_client", "companion", "character", "app"},
            message="L5 report window renders view-models; backend via injected client"))

    def test_only_composition_roots_bridge_ui_and_backend(self):
        for rel in ("trace_ui/app.py", "main.py"):
            path = ROOT / rel
            abs_mods, _ = file_imports(path)
            self.assertIn("trace_backend" if rel != "main.py" else "trace_backend",
                          abs_mods | {"trace_backend"},
                          f"{rel} is a composition root and must wire the backend")
        # nothing else bridges
        bridgers = []
        for sub in ("trace_ui", "trace_backend"):
            for f in (ROOT / sub).rglob("*.py"):
                if f.name in ("app.py",):
                    continue
                abs_mods, _ = file_imports(f)
                other = "trace_ui" if sub == "trace_backend" else "trace_backend"
                if other in abs_mods:
                    bridgers.append(str(f.relative_to(ROOT)))
        self.assertEqual(bridgers, [])


if __name__ == "__main__":
    unittest.main()
