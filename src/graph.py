"""Production BI workflow with deterministic, bounded orchestration."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.nodes.format_answer import format_answer_node
from src.nodes.schema_linking import schema_linking_node
from src.nodes.sql_execution import sql_execution_node
from src.nodes.sql_generation import sql_generation_node
from src.nodes.sql_review import sql_review_node
from src.nodes.sql_validation import sql_validation_node
from src.policy import build_routing_update
from src.routing import decide_next_node
from src.state import BIAgentState, create_initial_state
from src.workflow import run_graph_once, with_visit_tracking


def supervisor_node(state: BIAgentState) -> dict:
    current_iteration = state.get("iteration", 0) + 1
    decision = decide_next_node({**state, "iteration": current_iteration})
    return build_routing_update(
        state,
        iteration=current_iteration,
        candidate=decision.next_node,
        reason=decision.reason,
        routing_policy="deterministic",
    )


def supervisor_router(state: BIAgentState) -> str:
    return state.get("next_node", "format_answer")


def build_graph():
    graph = StateGraph(BIAgentState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("schema_linking", with_visit_tracking(schema_linking_node, "schema_linking"))
    graph.add_node("sql_generation", with_visit_tracking(sql_generation_node, "sql_generation"))
    graph.add_node("sql_review", with_visit_tracking(sql_review_node, "sql_review"))
    graph.add_node("sql_validation", with_visit_tracking(sql_validation_node, "sql_validation"))
    graph.add_node("sql_execution", with_visit_tracking(sql_execution_node, "sql_execution"))
    graph.add_node("format_answer", with_visit_tracking(format_answer_node, "format_answer"))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        supervisor_router,
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


app = build_graph()


def run_bi_agent(question: str, verbose: bool = True) -> dict:
    final_state, trace = run_graph_once(app, create_initial_state(question))
    if verbose:
        for step in trace:
            print(f"--- node: {step['node']} ---")
            for key, value in step.items():
                if key != "node":
                    print(f"  {key}: {value}")
    return final_state
