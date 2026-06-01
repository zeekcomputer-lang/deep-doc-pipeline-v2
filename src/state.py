"""
LangGraph global state.
"""
from __future__ import annotations
import operator
from typing import TypedDict, List, Dict, Annotated, Any


def update_dict(a: Dict, b: Dict) -> Dict:
    """Dict reducer — corrected from original spec's {a, b} typo."""
    return {**a, **b}


class GraphState(TypedDict, total=False):
    # [1. Initial Input]
    raw_docs: List[Dict[str, Any]]

    # [2. Map-Reduce (Extraction)]
    extracted_events: Annotated[List[Dict], operator.add]

    # [3. Hierarchical Data Compression]
    grouped_chunks: Dict[str, List[Dict]]
    period_summaries: Annotated[Dict[str, str], update_dict]
    global_theme: str

    # [4. Whitepaper Planning Loop]
    outline: List[Dict]
    outline_feedback: str
    is_outline_approved: bool
    outline_retry_count: int

    # [5. Section Writing and Fact-check Loop]
    current_section_index: int
    current_draft: str
    previous_draft: str                             # v1.1: regression guard
    hallucinated_tokens: Annotated[List[str], operator.add]  # v1.1: banned tokens
    draft_feedback: str
    is_draft_approved: bool
    section_retry_count: int
    completed_sections: Annotated[Dict[int, str], update_dict]
    unverified_sections: Annotated[List[int], operator.add]  # v1.1: audit log

    # [6. Final Assembly (English)]
    final_compiled: str                             # v1.1: pre-polish pure assembly
    final_output: str                               # final result (after translation = Korean)
    polish_retry_count: int

    # [7. Translation (English → Korean)]
    english_output: str
    proper_nouns: List[str]
    translation_retry_count: int
    is_translation_approved: bool
    translation_feedback: str
    translation_candidates: Annotated[List[Dict], operator.add]  # best-of-N tracking
