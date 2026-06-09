"""
LangGraph graph assembly — v3.0 (KR-first, category-based).
"""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END

from .state import GraphState
from . import nodes as N


def build_graph():
    g = StateGraph(GraphState)

    # ── Step 1: Knowledge Structuring
    g.add_node("load_docs", N.load_docs_node)
    g.add_node("knowledge_extractor", N.knowledge_extractor_node)
    g.add_node("knowledge_aggregator", N.knowledge_aggregator_node)
    g.add_node("temporal_indexer", N.temporal_indexer_node)

    g.add_edge(START, "load_docs")
    g.add_edge("load_docs", "knowledge_extractor")
    g.add_edge("knowledge_extractor", "knowledge_aggregator")
    g.add_edge("knowledge_aggregator", "temporal_indexer")

    # ── Step 2: Narrative Flow
    g.add_node("category_analyzer", N.category_analyzer_node)
    g.add_node("narrative_planner", N.narrative_planner_node)
    g.add_node("narrative_critique", N.narrative_critique_node)

    g.add_edge("temporal_indexer", "category_analyzer")
    g.add_edge("category_analyzer", "narrative_planner")
    g.add_edge("narrative_planner", "narrative_critique")

    # Step 2 loop: critique → planner or init_writing
    g.add_conditional_edges(
        "narrative_critique",
        N.route_narrative,
        {
            "narrative_planner": "narrative_planner",
            "init_writing": "init_writing",
        },
    )

    # ── Step 3: Executive Summary Writing
    g.add_node("init_writing", N.init_writing_node)
    g.add_node("section_writer", N.section_writer_node)
    g.add_node("fact_checker", N.fact_checker_node)
    g.add_node("retry_section", N.retry_section_node)
    g.add_node("save_section", N.save_section_node)
    g.add_node("save_section_with_warning", N.save_section_with_warning_node)

    g.add_edge("init_writing", "section_writer")
    g.add_edge("section_writer", "fact_checker")

    g.add_conditional_edges(
        "fact_checker",
        N.route_section_draft,
        {
            "retry_section": "retry_section",
            "save_section": "save_section",
            "save_section_with_warning": "save_section_with_warning",
        },
    )
    g.add_edge("retry_section", "section_writer")

    # After save: next section or Step 4
    g.add_conditional_edges(
        "save_section",
        N.route_next_section,
        {"section_writer": "section_writer", "timeline_formatter": "timeline_formatter"},
    )
    g.add_conditional_edges(
        "save_section_with_warning",
        N.route_next_section,
        {"section_writer": "section_writer", "timeline_formatter": "timeline_formatter"},
    )

    # ── Step 4: Hybrid Assembly
    g.add_node("timeline_formatter", N.timeline_formatter_node)
    g.add_node("compiler", N.compiler_node)
    g.add_node("polish", N.polish_node)

    g.add_edge("timeline_formatter", "compiler")
    g.add_edge("compiler", "polish")
    g.add_edge("polish", END)

    return g.compile()


def build_resume_graph(resume_from: str):
    """Resume graph — 특정 Step부터 재실행.

    resume_from:
        "step2" — Step 2부터 (knowledge_base + temporal_index 필요)
        "step3" — Step 3부터 (+ category_analyses + narrative_flow 필요)
        "step4" — Step 4부터 (+ executive_summary 필요)
        "polish" — polish만 (final_compiled 필요)
    """
    g = StateGraph(GraphState)

    if resume_from == "step2":
        g.add_node("category_analyzer", N.category_analyzer_node)
        g.add_node("narrative_planner", N.narrative_planner_node)
        g.add_node("narrative_critique", N.narrative_critique_node)
        g.add_node("init_writing", N.init_writing_node)
        g.add_node("section_writer", N.section_writer_node)
        g.add_node("fact_checker", N.fact_checker_node)
        g.add_node("retry_section", N.retry_section_node)
        g.add_node("save_section", N.save_section_node)
        g.add_node("save_section_with_warning", N.save_section_with_warning_node)
        g.add_node("timeline_formatter", N.timeline_formatter_node)
        g.add_node("compiler", N.compiler_node)
        g.add_node("polish", N.polish_node)

        g.add_edge(START, "category_analyzer")
        g.add_edge("category_analyzer", "narrative_planner")
        g.add_edge("narrative_planner", "narrative_critique")
        g.add_conditional_edges(
            "narrative_critique", N.route_narrative,
            {"narrative_planner": "narrative_planner", "init_writing": "init_writing"},
        )
        g.add_edge("init_writing", "section_writer")
        g.add_edge("section_writer", "fact_checker")
        g.add_conditional_edges(
            "fact_checker", N.route_section_draft,
            {"retry_section": "retry_section", "save_section": "save_section",
             "save_section_with_warning": "save_section_with_warning"},
        )
        g.add_edge("retry_section", "section_writer")
        g.add_conditional_edges(
            "save_section", N.route_next_section,
            {"section_writer": "section_writer", "timeline_formatter": "timeline_formatter"},
        )
        g.add_conditional_edges(
            "save_section_with_warning", N.route_next_section,
            {"section_writer": "section_writer", "timeline_formatter": "timeline_formatter"},
        )
        g.add_edge("timeline_formatter", "compiler")
        g.add_edge("compiler", "polish")
        g.add_edge("polish", END)

    elif resume_from == "step3":
        g.add_node("init_writing", N.init_writing_node)
        g.add_node("section_writer", N.section_writer_node)
        g.add_node("fact_checker", N.fact_checker_node)
        g.add_node("retry_section", N.retry_section_node)
        g.add_node("save_section", N.save_section_node)
        g.add_node("save_section_with_warning", N.save_section_with_warning_node)
        g.add_node("timeline_formatter", N.timeline_formatter_node)
        g.add_node("compiler", N.compiler_node)
        g.add_node("polish", N.polish_node)

        g.add_edge(START, "init_writing")
        g.add_edge("init_writing", "section_writer")
        g.add_edge("section_writer", "fact_checker")
        g.add_conditional_edges(
            "fact_checker", N.route_section_draft,
            {"retry_section": "retry_section", "save_section": "save_section",
             "save_section_with_warning": "save_section_with_warning"},
        )
        g.add_edge("retry_section", "section_writer")
        g.add_conditional_edges(
            "save_section", N.route_next_section,
            {"section_writer": "section_writer", "timeline_formatter": "timeline_formatter"},
        )
        g.add_conditional_edges(
            "save_section_with_warning", N.route_next_section,
            {"section_writer": "section_writer", "timeline_formatter": "timeline_formatter"},
        )
        g.add_edge("timeline_formatter", "compiler")
        g.add_edge("compiler", "polish")
        g.add_edge("polish", END)

    elif resume_from == "step4":
        g.add_node("timeline_formatter", N.timeline_formatter_node)
        g.add_node("compiler", N.compiler_node)
        g.add_node("polish", N.polish_node)

        g.add_edge(START, "timeline_formatter")
        g.add_edge("timeline_formatter", "compiler")
        g.add_edge("compiler", "polish")
        g.add_edge("polish", END)

    elif resume_from == "polish":
        g.add_node("polish", N.polish_node)
        g.add_edge(START, "polish")
        g.add_edge("polish", END)

    else:
        raise ValueError(
            f"Unknown resume_from: {resume_from!r}. "
            f"Use: step2 / step3 / step4 / polish"
        )

    return g.compile()
