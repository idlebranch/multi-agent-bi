from __future__ import annotations

import unittest

from src.state import create_initial_state
from src.workflow import run_graph_once


class FakeGraph:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, initial_state):
        self.calls += 1
        yield {"supervisor": {"iteration": 1, "next_node": "format_answer"}}
        yield {"format_answer": {"final_answer": "done"}}


class WorkflowTests(unittest.TestCase):
    def test_stream_is_consumed_once_and_builds_final_state(self) -> None:
        graph = FakeGraph()
        final_state, trace = run_graph_once(
            graph,
            create_initial_state("question", as_of_date="2026-07-17"),
        )
        self.assertEqual(graph.calls, 1)
        self.assertEqual(final_state["final_answer"], "done")
        self.assertEqual([item["node"] for item in trace], ["supervisor", "format_answer"])
        self.assertGreaterEqual(final_state["total_duration_ms"], 0)


if __name__ == "__main__":
    unittest.main()
