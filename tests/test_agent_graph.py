"""Tests: the agent-graph runtime (nodes, routing, fan-out, guards)."""

import threading
import time
import unittest
from unittest import mock

from trace_backend import agent_graph
from trace_backend.agent_graph import END, Graph, GraphRun

class _State:
    def __init__(self, cancel: threading.Event | None = None):
        self.cancel = cancel
        self.run_trace = None
        self.value: list[str] = []


class TestLinearGraph(unittest.TestCase):
    def test_nodes_run_in_declared_order(self):
        g = Graph("linear")
        g.add_node("a", lambda s: (s.value.append("a"), "b")[1])
        g.add_node("b", lambda s: (s.value.append("b"), None)[1])
        trace = g.run(_State(), "a")
        self.assertEqual(trace.visited, ["a", "b"])
        self.assertEqual(trace.summary(), "linear: a → b")

    def test_node_returning_end_terminates(self):
        g = Graph("term")
        g.add_node("a", lambda s: END)
        g.add_node("b", lambda s: (_ for _ in ()).throw(AssertionError("unreached")))
        trace = g.run(_State(), "a")
        self.assertEqual(trace.visited, ["a"])

    def test_unknown_node_raises(self):
        g = Graph("broken")
        g.add_node("a", lambda s: "missing")
        with self.assertRaises(KeyError):
            g.run(_State(), "a")

    def test_duplicate_node_name_rejected(self):
        g = Graph("dup")
        g.add_node("a", lambda s: None)
        with self.assertRaises(ValueError):
            g.add_node("a", lambda s: None)

    def test_cycle_guard_trips(self):
        g = Graph("loop")
        g.add_node("a", lambda s: "b")
        g.add_node("b", lambda s: "a")
        with self.assertRaises(RuntimeError):
            g.run(_State(), "a", max_steps=4)


class TestRouting(unittest.TestCase):
    def test_conditional_routing_by_state(self):
        g = Graph("route")
        g.add_node("start", lambda s: "good" if s.ready else "bad")
        g.add_node("good", lambda s: (s.value.append("ok"), END)[1])
        g.add_node("bad", lambda s: (s.value.append("no"), END)[1])
        s_ok = _State()
        s_ok.ready = True
        s_no = _State()
        s_no.ready = False
        g.run(s_ok, "start")
        g.run(s_no, "start")
        self.assertEqual(s_ok.value, ["ok"])
        self.assertEqual(s_no.value, ["no"])

    def test_cancel_between_nodes_raises_declared_type(self):
        g = Graph("cancel", cancel_exc_type=KeyboardInterrupt)
        cancel = threading.Event()
        g.add_node("a", lambda s: "b")
        g.add_node("b", lambda s: "c")
        def _set(ev):
            ev.cancel.set()
            return "b"
        g.add_node("arm", _set)
        with self.assertRaises(KeyboardInterrupt):
            g.run(_State(cancel), "arm", cancel=cancel)


class TestParallelFanOut(unittest.TestCase):
    def test_merge_receives_all_branch_results(self):
        g = Graph("fanout", cancel_exc_type=KeyboardInterrupt)
        seen = []

        def a(state):
            seen.append("a")
            return "A"

        def b(state):
            seen.append("b")
            return "B"

        def merge(state, results, timings=None):
            state.value.append(results)
            return END

        g.add_parallel("joint", {"a": a, "b": b}, merge=merge)
        s = _State()
        g.run(s, "joint")
        self.assertEqual(s.value[0]["a"], "A")
        self.assertEqual(s.value[0]["b"], "B")
        self.assertEqual(sorted(seen), ["a", "b"])

    def test_failing_branch_is_isolated_not_fatal(self):
        g = Graph("isolate", cancel_exc_type=KeyboardInterrupt)

        def boom(state):
            raise ValueError("branch exploded")

        def good(state):
            return "fine"

        def merge(state, results, timings=None):
            state.value.append(results)
            return END

        g.add_parallel("joint", {"boom": boom, "good": good}, merge=merge)
        s = _State()
        g.run(s, "joint")
        self.assertIsNone(s.value[0]["boom"])
        self.assertEqual(s.value[0]["good"], "fine")

    def test_cancel_inside_branch_propagates_promptly(self):
        g = Graph("cancel-branch", cancel_exc_type=KeyboardInterrupt)
        cancel = threading.Event()

        import time as _t

        def hang(state):
            while not cancel.is_set():
                _t.sleep(0.01)
            raise KeyboardInterrupt("user cancelled")

        def slow(state):
            _t.sleep(0.35)
            return "done"

        def merge(state, results, timings=None):
            return END

        g.add_parallel("joint", {"hang": hang, "slow": slow}, merge=merge)
        cancel.set()
        with self.assertRaises(KeyboardInterrupt):
            g.run(_State(cancel), "joint", cancel=cancel)

    def test_run_trace_counts_fanouts(self):
        g = Graph("count", cancel_exc_type=KeyboardInterrupt)
        g.add_parallel("joint", {"a": lambda s: "a", "b": lambda s: "b"},
                       merge=lambda s, r, t=None: END)
        s = _State()
        trace = g.run(s, "joint")
        self.assertEqual(trace.parallel_fanouts, 1)
        self.assertIsInstance(trace, GraphRun)

    def test_merge_receives_timings_and_trace_records_them(self):
        import time as _t

        g = Graph("timed", cancel_exc_type=KeyboardInterrupt)
        captured = {}

        def slow(state):
            _t.sleep(0.02)
            return "x"

        def quick(state):
            return "y"

        def merge(state, results, timings):
            captured.update(timings)
            return END

        g.add_parallel("joint", {"slow": slow, "quick": quick}, merge=merge)
        s = _State()
        trace = g.run(s, "joint")
        self.assertEqual(set(captured), {"slow", "quick"})
        self.assertGreaterEqual(captured["slow"], 0.01)
        self.assertGreaterEqual(captured["quick"], 0.0)
        self.assertEqual(set(trace.parallel_timings["joint"]),
                         {"slow", "quick"})
        self.assertIn("joint:", trace.timings_text())

    def test_timings_recorded_for_single_branch_node(self):
        g = Graph("single", cancel_exc_type=KeyboardInterrupt)
        seen = {}

        def only(state):
            return 42

        def merge(state, results, timings):
            seen.update(timings)
            return END

        g.add_parallel("solo", {"only": only}, merge=merge)
        trace = g.run(_State(), "solo")
        self.assertEqual(seen, {"only": trace.parallel_timings["solo"]["only"]})
        self.assertGreaterEqual(seen["only"], 0.0)
        # A single branch is not a fan-out.
        self.assertEqual(trace.parallel_fanouts, 0)


    def test_timed_out_branch_does_not_stall_the_node(self):
        """A branch overrunning the per-branch bound is recorded as failed and
        the node proceeds — the run never waits for a hung worker."""
        g = Graph("timeout", cancel_exc_type=KeyboardInterrupt)
        fin = threading.Event()

        def hang(state):
            time.sleep(2.0)
            fin.set()
            return "late"

        def quick(state):
            return "done"

        def merge(state, results, timings):
            state.value.append(results)
            return END

        g.add_parallel("joint", {"hang": hang, "quick": quick}, merge=merge)
        s = _State()
        with mock.patch.object(agent_graph, "_BRANCH_TIMEOUT_S", 0.2):
            t0 = time.perf_counter()
            g.run(s, "joint")
            elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.0, "node must not wait for the hung branch")
        self.assertIsNone(s.value[0]["hang"])
        self.assertEqual(s.value[0]["quick"], "done")
        # The abandoned worker still finishes; never leave it dangling past
        # the test (the process-kill atexit join would reveal it anyway).
        self.assertTrue(fin.wait(5.0))

    def test_pool_start_failure_runs_branches_sequentially(self):
        """No threads available → sequential fallback, same results, same
        submission order."""
        g = Graph("noseq", cancel_exc_type=KeyboardInterrupt)
        order: list[str] = []

        def a(state):
            order.append("a")
            return "A"

        def b(state):
            order.append("b")
            return "B"

        def merge(state, results, timings):
            state.value.append(results)
            return END

        g.add_parallel("joint", {"a": a, "b": b}, merge=merge)
        s = _State()
        with mock.patch.object(agent_graph, "ThreadPoolExecutor",
                               side_effect=RuntimeError("no threads")):
            g.run(s, "joint")
        self.assertEqual(s.value[0], {"a": "A", "b": "B"})
        self.assertEqual(order, ["a", "b"])


class TestIntrospection(unittest.TestCase):
    def test_describe_lists_node_kinds(self):
        g = Graph("plan", cancel_exc_type=KeyboardInterrupt)
        g.add_node("start", lambda s: "joint")
        g.add_parallel("joint", {"a": lambda s: "A", "b": lambda s: "B"},
                       merge=lambda s, r, t=None: END)
        g.add_node("finish", lambda s: None)
        text = g.describe()
        self.assertIn("plan: 3 nodes (cancel = KeyboardInterrupt)", text)
        self.assertIn("start", text)
        self.assertIn("parallel[a, b]", text)
        self.assertIn("finish", text)


if __name__ == "__main__":
    unittest.main()
