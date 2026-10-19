"""Agent-graph runtime for Trace: explicit nodes, conditional routes, parallel
fan-out/fan-in — with zero third-party dependencies.

Trace's agent deliberately is NOT a free-for-all tool-calling loop: evidence
is only useful while it stays disk-verified. This runtime keeps that
discipline while adopting the shape that makes modern agent frameworks
(LangGraph and friends) professional to work with:

  - the pipeline is a declared graph of named nodes over shared typed state,
    instead of a thousand-line if/elif method;
  - nodes route to the next node by name (conditional edges);
  - :meth:`Graph.add_parallel` nodes fan work out to independent branches on
    a bounded thread pool and fan results back in through a merge function,
    with per-branch fault isolation, per-branch timings, and a sequential
    fallback;
  - bounded repair loops (e.g. re-ask the model when it returned malformed
    JSON) are expressed the same way as any other transition;
  - a cycle guard + a visited-node trace + per-branch timings keep every run
    inspectable (the trace lands in the final report's evidence summary).

Out of the box this is a *finite, auditable* graph: a fixed number of steps,
every node ending in a stage event the UI can render, every attribute the
model is allowed to cite backed by a real read. Leave it stdlib-only — the
layering tests enforce that on purpose.

How parallel nodes work
=======================
Read this section and you know everything — you do not need to open the
engine to understand the parallelism.

The mechanics (inside ``add_parallel``)
---------------------------------------
::

        ┌────────── one node, N independent branches ──────────┐
        │  caller thread                    workers            │
        │                                                      │
        │  run(state) ──submit──▶  [branch A][branch B][C]     │
        │        │                 ▲       ▲                   │
        │        │ result collect ─┘       ┘  (bounded pool,   │
        │        ▼                    max 2..8 threads)        │
        │  merge(state, results, timings)                      │
        │        │   ◀─ serial join on the caller thread       │
        │        ▼                                             │
        │  returns next node name (conditional edge)           │
        └──────────────────────────────────────────────────────┘

1.  **Fan-out.** ``add_parallel(name, {"keyA": fnA, "keyB": fnB}, merge)``
    registers ONE node named ``name``. When execution reaches it, each
    branch callable ``fn(state)`` is submitted to a
    ``ThreadPoolExecutor(max_workers=max(2, min(4, len(branches))))`` and
    runs *concurrently* — real parallel collection (a screenshot while the
    window context is gathered; diagnostics while tests run).
2.  **Fault isolation.** A branch that raises is recorded as ``None`` for
    its key — the other branches are unaffected and the pipeline continues.
    Exceptions are deliberately not hidden: the merge (and finally the
    report) turns them into honest warnings. The ONE exception type that is
    never swallowed is the graph's ``cancel_exc_type`` (Trace passes
    ``InvestigationCancelled``): it is re-raised immediately so Cancel is
    prompt, not delayed behind a 120 s model call.
3.  **Fan-in (merge).** When every branch has settled, ``merge(state,
    results, timings)`` runs serialized on the caller thread — the state is
    never mutated from two threads at once. ``results`` maps branch key →
    value (``None`` if that branch failed); ``timings`` maps branch key →
    seconds (faults get a timing too). The merge writes into the shared
    state and returns the name of the next node — that return IS the
    conditional edge leaving the parallel node.
4.  **Accounting.** The timings and the fan-out count are recorded on the
    :class:`GraphRun` trace attached to the state; Trace appends them to the
    final report's evidence summary, so the UI can show "capture:
    screenshot 1.2s · context 0.0s" without any extra machinery.
5.  **Fallbacks, in order.**
    - ``len(branches) == 1`` → runs inline on the caller thread (no pool).
    - The pool itself fails to start → branches run sequentially, in
      submission order, with identical isolation/cancel semantics.
      Parallelism changes speed, never results.

Guarantees worth remembering
----------------------------
- **Submission order is the result order**: branch keys are stable, so
  downstream code (and tests) see deterministic data no matter which
  thread finished first.
- **Between-node cancellation**: ``run`` re-checks the cancel event before
  every node and before every branch future, so a cancelled run stops at
  the next collection point, not when everything happens to finish.
- **The state is single-writer per branch**: branches may read anything on
  the state but should return values rather than mutate drift-prone
  surfaces; the merge is where shared state is written.

Where Trace actually uses it (the whole map — no other files needed)
--------------------------------------------------------------------
``investigation_engine.py`` wires exactly two parallel nodes (this file is
generic; the engine just declares the branches):

=================  ==================================  =====================================
graph node         branches (key → what runs)          merged into (merge fn)
=================  ==================================  =====================================
``capture``        ``screenshot`` → capture_screenshot  `_capture_merge` → ``project_root``
                    (mss; fast, native)
                   ``context``    → collect_local_
                    context (window title, CWD,
                    editor selection; cheap)
``collect``        ``diagnostics``    → collect_diag_   `_collect_merge` → ``analyze``
                    nostics: ruff/mypy/tsc/eslint/
                    cargo-check/go-vet… (subprocesses)
                   ``tests``       → run_project_tests
                    (OPT-IN only; skipped honestly
                    in explain mode)
                   ``intelligence`` → _gather_intel…
                   ligence: parallel keyword searches,
                   symbol lookups, outlines, JSON
                   config checks, TODO/FIXME anchors,
                   bounded PROJECT TREE
=================  ==================================  =====================================

Everything else runs as plain sequential nodes with conditional routing:

``receive → capture_start → capture∥ → project_root → extract (skip ⇐
explain) → read (parallel read batches) → collect∥ → analyze → validate ⇢
repair? (one bounded re-ask on malformed JSON) ⇢ apply_fix? (auto_fix) →
report``

Honest aborts route to ``report`` with a low-confidence verdict; the abort
paths (invalid request, unknown project root, model unreachable) are also
plain nodes. So: two fan-out points, one repair loop, one apply-fix
branch — that is the entire concurrency story of the Trace agent.

Introspection: ``print(graph.describe())`` prints every node and its kind
(no need to read branch code), and ``GraphRun.summary()`` /
``GraphRun.parallel_timings`` report what actually ran, how fast, per run.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

# The conventional terminal node name. Any routing that lands here ends the
# run cleanly; a node returning ``None`` also ends it (that is a legitimate
# way to say "this is the last useful thing to do").
END = "__end__"


@dataclass
class GraphRun:
    """Everything worth knowing about one graph execution."""
    name: str
    visited: list[str] = field(default_factory=list)
    parallel_fanouts: int = 0
    steps_hit_limit: bool = False
    # node-name → {branch-key → seconds}; populated by add_parallel nodes.
    parallel_timings: dict[str, dict[str, float]] = field(default_factory=dict)

    def timings_text(self) -> str:
        """One compact line: every parallel fan-out with branch durations."""
        parts = []
        for node, d in self.parallel_timings.items():
            inner = " · ".join(f"{k} {v:.2f}s" for k, v in d.items())
            parts.append(f"{node}: {inner}")
        return "; ".join(parts)

    def summary(self) -> str:
        path = " → ".join(self.visited) or "(empty)"
        extra = f", {self.parallel_fanouts} parallel fan-out(s)" \
            if self.parallel_fanouts else ""
        if self.steps_hit_limit:
            extra += " (cycle guard tripped)"
        return f"{self.name}: {path}{extra}"


NodeFn = Callable[[Any], str | None]


class Graph:
    """A named-node workflow graph over a shared mutable state object.

    Nodes are callables ``fn(state) -> next_node_name | None | END``.
    Routing is data-driven from the state at each step, which is exactly how
    conditional edges behave in a typed state-graph framework — minus the
    dependency. Raise :class:`CancelledError`-like exceptions from anywhere
    (including inside parallel branches of ``cancel_exc_type``); they
    propagate to the caller instead of being swallowed.
    """

    def __init__(self, name: str,
                 cancel_exc_type: type[BaseException] | None = None):
        self.name = name
        self._nodes: dict[str, NodeFn] = {}
        # node-name → human description ("node" / "parallel: a ∣ b ∣ c"),
        # used by describe() so the workflow can be reviewed without opening
        # the branch implementations.
        self._kinds: dict[str, str] = {}
        # The exception type that means "the user cancelled": parallel
        # branches re-raise it immediately instead of recording an error.
        self._cancel_exc_type = cancel_exc_type

    @property
    def nodes(self) -> list[str]:
        return list(self._nodes)

    def add_node(self, name: str, fn: NodeFn) -> "Graph":
        if name in self._nodes:
            raise ValueError(f"{self.name}: duplicate node {name!r}")
        self._nodes[name] = fn
        self._kinds[name] = "node"
        return self

    # -- parallel fan-out / fan-in ------------------------------------------
    def add_parallel(self, name: str,
                     branches: dict[str, Callable[[Any], Any]],
                     merge: Callable[..., str | None],
                     max_workers: int = 4) -> "Graph":
        """One node that runs independent branches concurrently, then merges.

        ``branches`` map a result key to a callable ``(state) -> value``.
        On a worker error the branch yields ``None`` (fault-isolated) —
        except for ``cancel_exc_type``, which is re-raised so cancellation
        stays prompt. ``merge(state, results, timings)`` runs serially on
        the caller thread and returns the next node name; ``timings`` maps
        each branch key to its seconds. Timings are also recorded on the
        run trace (``state.run_trace.parallel_timings``), so reports can
        show where the wall-clock went. If threads are unavailable (or
        there is a single branch), branches run sequentially: identical
        results and timings, just less speed.
        """
        if not branches:
            self._kinds[name] = "parallel[]"
            def empty(state):
                return merge(state, {}, {})
            self._nodes[name] = empty
            return self
        self._kinds[name] = f"parallel[{', '.join(branches)}]"

        def run_node(state):
            cancel = getattr(state, "cancel", None)
            self._check_cancelled(cancel)
            keyed = list(branches.items())
            results: dict[str, Any] = {}
            timings: dict[str, float] = {}
            run_trace: GraphRun | None = getattr(state, "run_trace", None)

            def _timed(key, fn):
                t0 = time.perf_counter()
                try:
                    return fn(state)
                finally:
                    timings[key] = time.perf_counter() - t0

            if len(keyed) == 1:
                self._check_cancelled(cancel)
                key, fn = keyed[0]
                try:
                    results[key] = _timed(key, fn)
                except BaseException as e:  # noqa: BLE001 - isolated on purpose
                    if self._is_cancel(e):
                        raise
                    results[key] = None
                if run_trace is not None:
                    run_trace.parallel_timings[name] = dict(timings)
                return merge(state, results, timings)

            try:
                with ThreadPoolExecutor(
                        max_workers=max(2, min(max_workers, len(keyed))),
                        thread_name_prefix=f"graph-{name}") as ex:
                    for key, _fn, fut in [(k, fn, ex.submit(_timed, k, fn))
                                          for k, fn in keyed]:
                        self._check_cancelled(cancel)
                        try:
                            results[key] = fut.result(timeout=180)
                        except BaseException as e:  # noqa: BLE001
                            if self._is_cancel(e):
                                raise
                            results[key] = None
                    if run_trace is not None:
                        run_trace.parallel_fanouts += 1
                        run_trace.parallel_timings[name] = dict(timings)
            except BaseException as e:  # noqa: BLE001
                if self._is_cancel(e):
                    raise
                if not results:
                    # Pool could not even start: sequential fallback,
                    # preserving submission order and the same guarantees.
                    for key, fn in keyed:
                        self._check_cancelled(cancel)
                        try:
                            results[key] = _timed(key, fn)
                        except BaseException as be:  # noqa: BLE001
                            if self._is_cancel(be):
                                raise
                            results[key] = None
                    if run_trace is not None:
                        run_trace.parallel_timings[name] = dict(timings)
            return merge(state, results, timings)

        self._nodes[name] = run_node
        return self

    def describe(self) -> str:
        """Human-readable plan: every node and its kind, no source needed."""
        head = f"{self.name}: {len(self._nodes)} nodes"
        if self._cancel_exc_type is not None:
            head += f" (cancel = {self._cancel_exc_type.__name__})"
        lines = [head]
        for node_name in self._nodes:
            lines.append(f"  {node_name:<14} {self._kinds.get(node_name, 'node')}")
        return "\n".join(lines)

    def _is_cancel(self, exc: BaseException) -> bool:
        return (self._cancel_exc_type is not None
                and isinstance(exc, self._cancel_exc_type))

    def _check_cancelled(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            exc = self._cancel_exc_type
            raise (exc or Exception)("cancelled")

    # -- execution -----------------------------------------------------------
    def run(self, state: Any, start: str, *,
            cancel: threading.Event | None = None,
            max_steps: int = 64) -> GraphRun:
        """Walk the graph from ``start`` until a node routes to ``END``/None.

        Cycle-protected (``max_steps``): a mis-routed loop raises instead of
        spinning forever. The visited path is recorded both on the returned
        :class:`GraphRun` and, when present, on ``state.run_trace``.
        """
        trace = GraphRun(name=self.name)
        node = start
        while node is not None and node != END:
            fn = self._nodes.get(node)
            if fn is None:
                raise KeyError(f"{self.name}: unknown node {node!r}")
            trace.visited.append(node)
            if len(trace.visited) > max_steps:
                trace.steps_hit_limit = True
                raise RuntimeError(
                    f"{self.name}: graph exceeded {max_steps} steps "
                    f"(visited {trace.visited})")
            cancel_ev = cancel if cancel is not None \
                else getattr(state, "cancel", None)
            self._check_cancelled(cancel_ev)
            if hasattr(state, "__dict__"):
                state.run_trace = trace
            node = fn(state)
        return trace
