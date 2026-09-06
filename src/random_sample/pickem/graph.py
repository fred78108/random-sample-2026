"""LangGraph StateGraph construction & compilation. See specs/pickem-agent/DESIGN.md §3.2."""

from __future__ import annotations

import sqlite3

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from random_sample.pickem.nodes.context import build_context_node
from random_sample.pickem.nodes.prediction import build_prediction_node
from random_sample.pickem.nodes.ranking import ranking_node
from random_sample.pickem.nodes.reporting import build_reporting_node
from random_sample.pickem.nodes.schedule import build_schedule_node
from random_sample.pickem.nodes.validator import route_after_validate, validator_node
from random_sample.pickem.state import PickemState


def build_graph(conn: sqlite3.Connection) -> CompiledStateGraph:
    graph = StateGraph(PickemState)
    graph.add_node("schedule", build_schedule_node(conn))
    graph.add_node("context", build_context_node(conn))
    graph.add_node("prediction", build_prediction_node(conn))
    graph.add_node("rank", ranking_node)
    graph.add_node("validate", validator_node)
    graph.add_node("report", build_reporting_node(conn))

    graph.add_edge(START, "schedule")
    graph.add_edge("schedule", "context")
    graph.add_edge("context", "prediction")
    graph.add_edge("prediction", "rank")
    graph.add_edge("rank", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {"prediction": "prediction", "rank": "rank", "report": "report"},
    )
    graph.add_edge("report", END)

    return graph.compile()
