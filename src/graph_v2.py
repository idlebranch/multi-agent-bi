"""Legacy experimental graph kept only for historical regression reference.

The public API, web UI, CLI, and launcher use src.graph.app exclusively.
Production recovery is deterministic and bounded by the policy limits.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.nodes.format_answer import format_answer_node
from src.nodes.llm_supervisor import llm_supervisor_node, llm_supervisor_router
from src.nodes.schema_linking import schema_linking_node
from src.nodes.sql_execution import sql_execution_node
from src.nodes.sql_generation import sql_generation_node
from src.nodes.sql_review import sql_review_node
from src.nodes.sql_validation import sql_validation_node
from src.state import BIAgentState, create_initial_state
from src.workflow import run_graph_once, with_visit_tracking


def build_graph_v2():
    graph = StateGraph(BIAgentState)
    graph.add_node("supervisor", llm_supervisor_node)
    graph.add_node("schema_linking", with_visit_tracking(schema_linking_node, "schema_linking"))
    graph.add_node("sql_generation", with_visit_tracking(sql_generation_node, "sql_generation"))
    graph.add_node("sql_review", with_visit_tracking(sql_review_node, "sql_review"))
    graph.add_node("sql_validation", with_visit_tracking(sql_validation_node, "sql_validation"))
    graph.add_node("sql_execution", with_visit_tracking(sql_execution_node, "sql_execution"))
    graph.add_node("format_answer", with_visit_tracking(format_answer_node, "format_answer"))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        llm_supervisor_router,
        {
            "schema_linking": "schema_linking",
            "sql_generation": "sql_generation",
            "sql_review": "sql_review",
            "sql_validation": "sql_validation",
            "sql_execution": "sql_execution",
            "format_answer": "format_answer",
        },
    )
    for node_name in (
        "schema_linking",
        "sql_generation",
        "sql_review",
        "sql_validation",
        "sql_execution",
    ):
        graph.add_edge(node_name, "supervisor")
    graph.add_edge("format_answer", END)
    return graph.compile()


app_v2 = build_graph_v2()


def run_bi_agent_v2(question: str, verbose: bool = True) -> dict:
    final_state, trace = run_graph_once(app_v2, create_initial_state(question))
    if verbose:
        for step in trace:
            print(f"--- node: {step['node']} ---")
            for key, value in step.items():
                if key != "node":
                    print(f"  {key}: {value}")
    return final_state
