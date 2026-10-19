"""Agent-graph runtime for Trace: explicit nodes, conditional routes, parallel
fan-out/fan-in — with zero third-party dependencies.

Trace's agent deliberately is NOT a free-for-all tool-calling loop: evidence
is only useful while it stays disk-verified. This runtime keeps that
discipline while adopting the shape that makes modern agent frameworks
(LangGraph and friends) professional to work with:

  - the pipeline is a declared graph of named nodes over shared typed state,
    instead of a thousand-line if/elif method;
  - nodes route to the next node by name (conditional edges);
  - a :meth:`Graph.parallel` node fans work out to independent branches on a
    bounded thread pool and fans results back in through a merge function,
    with per-branch fault isolation and a sequential fallback;
  - bounded repair loops (e.g. re-ask the model when it returned malformed
    JSON) are expressed the same way as any other transition;
  - a cycle guard + a visited-node trace keep every run inspectable.

Out of the box this is a *finite, auditable* graph: a fixed number of steps,
every node ending in a stage event the UI can render, every attribute the
model is allowed to cite backed by a real read. Leave it stdlib-only — the
layering tests enforce that on purpose.
"""

from __future__ import annotations

import threading
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
        stays prompt. ``merge(state, results)`` runs serially on the caller
        thread and returns the next node name. If threads are unavailable
        (or there is a single branch), branches run sequentially: identical
        results, just slower.
        """
        if not branches:
            def empty(state):
                return merge(state, {})
            self._nodes[name] = empty
            return self

        def run_node(state):
            cancel = getattr(state, "cancel", None)
            self._check_cancelled(cancel)
            keyed = list(branches.items())
            results: dict[str, Any] = {}
            run_trace: GraphRun | None = getattr(state, "run_trace", None)

            if len(keyed) == 1:
                self._check_cancelled(cancel)
                try:
                    results[keyed[0][0]] = keyed[0][1](state)
                except BaseException as e:  # noqa: BLE001 - isolated on purpose
                    if self._is_cancel(e):
                        raise
                    results[keyed[0][0]] = None
                return merge(state, results)

            try:
                with ThreadPoolExecutor(
                        max_workers=max(2, min(max_workers, len(keyed))),
                        thread_name_prefix=f"graph-{name}") as ex:
                    for key, _fn, fut in [(k, fn, ex.submit(fn, state))
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
            except BaseException as e:  # noqa: BLE001
                if self._is_cancel(e):
                    raise
                if not results:
                    # Pool could not even start: sequential fallback,
                    # preserving submission order and the same guarantees.
                    for key, fn in keyed:
                        self._check_cancelled(cancel)
                        try:
                            results[key] = fn(state)
                        except BaseException as be:  # noqa: BLE001
                            if self._is_cancel(be):
                                raise
                            results[key] = None
            return merge(state, results)

        self._nodes[name] = run_node
        return self

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
