"""
LangGraph node functions. One Node = One Task principle strictly enforced.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Any

from langgraph.types import Send

from .state import GraphState
from .schemas import (
    ExtractedEvent, PeriodSummary, GlobalTheme,
    Outline, OutlineCritique,
    SectionDraft, FactCheckResult, PolishedDocument,
    TranslationCheckResult,
)
from .llm import structured_call
from .utils import (
    is_valid_date, chrono_sort_and_group, filter_by_period,
    validate_outline_periods, compile_sections, format_events_for_prompt,
    split_compiled_by_section, split_section_header_body,
    extract_proper_nouns,
    extract_years_from_content, extract_sections_for_year,
    count_expected_periods, validate_korean_structure,
)
from .logger import plog, psub
from .context_guard import (
    BUDGET_BYTES, estimate_guard_overhead, available_data_budget,
    split_items_for_budget, trim_retry_context, cross_check_terms,
    measure_messages_bytes,
)


LOCAL_DATA_PATH = "./data/records.jsonl"

# ══════════════════════════════════════════════════════════════
# Common English enforcement suffix appended to key system prompts
# ══════════════════════════════════════════════════════════════
_EN_ENFORCE = (
    " Respond in English only. "
    "Preserve all proper nouns (company names, project names, personal names, "
    "place names, technical terms, abbreviations) in their original form."
)


# ──────────────────────────────────────────────────────────────
# Phase 1: Extraction
# ──────────────────────────────────────────────────────────────
def load_docs_node(state: GraphState) -> Dict[str, Any]:
    """Load JSONL file into raw_docs."""
    docs: List[Dict] = []
    failed = 0
    path = Path(LOCAL_DATA_PATH)
    with path.open("r", encoding="utf-8-sig") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError as e:
                failed += 1
                psub("load_docs", f"line {ln} skipped: {e}")
    plog("load_docs", f"loaded={len(docs)} failed={failed}")
    return {"raw_docs": docs}


def fanout_to_extractor(state: GraphState):
    """Dispatch strict_extractor_node per document via Send API."""
    return [
        Send("strict_extractor", {"doc": d, "doc_index": i})
        for i, d in enumerate(state["raw_docs"])
    ]


def strict_extractor_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract ExtractedEvent from a single document.
    3-retry built into structured_call.
    v1.2: Auto-truncate doc_text if 95KB budget exceeded.
    v1.3: English output enforced.
    """
    doc = payload["doc"]
    idx = payload["doc_index"]
    doc_text = json.dumps(doc, ensure_ascii=False)

    system_content = (
        "You are a document analyst. Extract key facts from the given source document. "
        "The date field MUST be in YYYY-MM-DD format. "
        "NEVER fabricate information not explicitly stated in the source."
        + _EN_ENFORCE
    )
    user_prefix = "Source document:\n"
    user_suffix = "\n\nExtract date/issue/action from the above document."

    def _build_messages(text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"{user_prefix}{text}{user_suffix}"},
        ]

    messages = _build_messages(doc_text)
    guard_overhead = estimate_guard_overhead(ExtractedEvent.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size > BUDGET_BYTES:
        excess = size - BUDGET_BYTES + 512
        doc_bytes = doc_text.encode("utf-8")
        allowed = max(len(doc_bytes) - excess, 256)
        doc_text = doc_bytes[:allowed].decode("utf-8", errors="ignore") + " [TRUNCATED]"
        messages = _build_messages(doc_text)
        psub("extractor", f"doc {idx} truncated: {size/1024:.1f}KB → target fit")

    try:
        ev = structured_call(messages, ExtractedEvent, role="extractor", temperature=0.0)
        if not is_valid_date(ev.date):
            psub("extractor", f"doc {idx} invalid date '{ev.date}' — dropped")
            return {"extracted_events": []}
        return {"extracted_events": [ev.model_dump()]}
    except Exception as e:
        psub("extractor", f"doc {idx} failed after retries: {e}")
        return {"extracted_events": []}


def chrono_sorter_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python sort + monthly grouping."""
    grouped = chrono_sort_and_group(state["extracted_events"])
    plog("chrono_sorter", f"events={len(state['extracted_events'])} months={list(grouped.keys())}")
    return {"grouped_chunks": grouped}


# ──────────────────────────────────────────────────────────────
# Phase 2: Micro Summaries
# ──────────────────────────────────────────────────────────────
def fanout_to_period_summarizer(state: GraphState):
    """Parallel monthly summaries via Send API."""
    return [
        Send("period_summarizer", {"period": p, "events": evs})
        for p, evs in state["grouped_chunks"].items()
    ]


def period_summarizer_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Monthly key trend summary in exactly 3 sentences.
    v1.2: Budget-aware batch splitting + sub-summary merging.
    v1.3: English output enforced.
    """
    period = payload["period"]
    events = payload["events"]

    system_content = (
        "You are a period trend analyst. Summarize the given event list into exactly "
        "3 sentences capturing the key trends. Do NOT add content not present in the events."
        + _EN_ENFORCE
    )
    user_template = "Period: {period}\n\nEvent list:\n{events_text}\n\n3-sentence summary:"

    def _build_messages(events_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_template.format(period=period, events_text=events_text)},
        ]

    events_text = format_events_for_prompt(events)
    messages = _build_messages(events_text)
    guard_overhead = estimate_guard_overhead(PeriodSummary.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size <= BUDGET_BYTES:
        result = structured_call(messages, PeriodSummary, role="default", temperature=0.2)
        plog("period_summarizer", f"{period}: {result.summary[:60]}...")
        return {"period_summaries": {period: result.summary}}

    # Budget exceeded — batch split
    data_budget = available_data_budget(
        system_content,
        PeriodSummary.model_json_schema(),
        extra_fixed=user_template.format(period=period, events_text=""),
    )
    batches = split_items_for_budget(events, format_events_for_prompt, data_budget)
    plog("period_summarizer", f"{period}: budget exceeded ({size/1024:.1f}KB) — {len(batches)} batches")

    sub_summaries: List[str] = []
    for batch in batches:
        batch_text = format_events_for_prompt(batch)
        batch_msgs = _build_messages(batch_text)
        sub = structured_call(batch_msgs, PeriodSummary, role="default", temperature=0.2)
        sub_summaries.append(sub.summary)

    # Merge sub-summaries
    merged_input = "\n".join(f"[Partial summary {i+1}] {s}" for i, s in enumerate(sub_summaries))
    merge_messages = [
        {"role": "system", "content": (
            "You are a summary merger. Combine partial summaries for the same period "
            "into one unified summary without information loss. "
            "Do NOT add content not present in the partial summaries. Exactly 3 sentences."
            + _EN_ENFORCE
        )},
        {"role": "user", "content": (
            f"Period: {period}\n\nPartial summaries:\n{merged_input}\n\n"
            "Unified 3-sentence summary:"
        )},
    ]
    merged = structured_call(merge_messages, PeriodSummary, role="default", temperature=0.2)
    plog("period_summarizer", f"{period}: merged summary: {merged.summary[:60]}...")
    return {"period_summaries": {period: merged.summary}}


def theme_analyzer_node(state: GraphState) -> Dict[str, Any]:
    """Derive overall theme in 1 paragraph.
    v1.2: Drops oldest monthly summaries if budget exceeded.
    v1.3: English output enforced.
    """
    summaries = state["period_summaries"]
    sorted_periods = sorted(summaries.keys())

    system_content = (
        "You are a macro analyst. Given monthly summaries, write exactly 1 paragraph "
        "capturing the overarching insight into the project's performance and risk trajectory. "
        "Do NOT add content not present in the summaries."
        + _EN_ENFORCE
    )

    def _make_joined(periods: list) -> str:
        return "\n".join(f"[{k}] {summaries[k]}" for k in periods)

    def _build_messages(joined: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"Monthly summaries:\n{joined}\n\nOverall theme (1 paragraph):"},
        ]

    active_periods = list(sorted_periods)
    joined = _make_joined(active_periods)
    messages = _build_messages(joined)
    guard_overhead = estimate_guard_overhead(GlobalTheme.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    while size > BUDGET_BYTES and len(active_periods) > 1:
        removed = active_periods.pop(0)
        plog("theme_analyzer", f"budget exceeded — removing oldest period: {removed}")
        joined = _make_joined(active_periods)
        messages = _build_messages(joined)
        size = measure_messages_bytes(messages) + guard_overhead

    result = structured_call(messages, GlobalTheme, role="default", temperature=0.3)
    plog("theme_analyzer", f"theme: {result.theme[:80]}...")
    return {"global_theme": result.theme}


# ──────────────────────────────────────────────────────────────
# Phase 4-B [Step 1]: Planning Validation Loop
# ──────────────────────────────────────────────────────────────
def draft_planner_node(state: GraphState) -> Dict[str, Any]:
    """Plan whitepaper outline from global_theme + period_summaries.
    v1.2: Truncates summaries if budget exceeded.
    v1.3: English output enforced.
    """
    theme = state["global_theme"]
    summaries = state["period_summaries"]
    available_periods = sorted(summaries.keys())
    joined = "\n".join(f"[{k}] {v}" for k, v in sorted(summaries.items()))

    prev_feedback = state.get("outline_feedback", "")
    retry_hint = ""
    if prev_feedback:
        retry_hint = (
            f"\n\n[PREVIOUS OUTLINE REJECTED — address these issues]\n{prev_feedback}\n"
        )

    system_content = (
        "You are a whitepaper planner. Create an outline based on the given theme and "
        "monthly summaries. Each outline item must cover exactly one 'YYYY-MM' period "
        "(target_period). "
        f"Available period keys: {available_periods}\n"
        "Only use periods from this list. Sort in chronological order."
        + _EN_ENFORCE
    )

    def _build_messages(j: str, hint: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": (
                f"Overall theme:\n{theme}\n\nMonthly summaries:\n{j}{hint}\n\n"
                "Create outline:"
            )},
        ]

    messages = _build_messages(joined, retry_hint)
    guard_overhead = estimate_guard_overhead(Outline.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size > BUDGET_BYTES:
        truncated_joined = "\n".join(
            f"[{k}] {v[:100]}..." for k, v in sorted(summaries.items())
        )
        messages = _build_messages(truncated_joined, retry_hint)
        new_size = measure_messages_bytes(messages) + guard_overhead
        plog("draft_planner", f"budget exceeded ({size/1024:.1f}KB → {new_size/1024:.1f}KB) — summaries truncated")

    result = structured_call(messages, Outline, role="default", temperature=0.3)
    items = [it.model_dump() for it in result.items]
    plog("draft_planner", f"outline items={len(items)}")
    return {"outline": items}


def planner_critique_node(state: GraphState) -> Dict[str, Any]:
    """
    Outline review: chronological flow + target_period existence validation.
    Python validates target_period deterministically (blocks LLM hallucination).
    v1.2: Budget check before LLM call, intent truncation if exceeded.
    v1.3: English output enforced.
    """
    outline = state["outline"]
    grouped = state["grouped_chunks"]

    # Python validation 1: target_period existence
    invalid_periods = validate_outline_periods(outline, grouped)
    if invalid_periods:
        msg = f"Non-existent target_period used: {invalid_periods}"
        plog("planner_critique", f"REJECTED (python): {msg}")
        return {
            "is_outline_approved": False,
            "outline_feedback": msg,
            "outline_retry_count": state.get("outline_retry_count", 0) + 1,
        }

    # Python validation 2: chronological order
    periods = [it["target_period"] for it in outline]
    if periods != sorted(periods):
        msg = f"Chronological order violation. Current order: {periods}"
        plog("planner_critique", f"REJECTED (python): {msg}")
        return {
            "is_outline_approved": False,
            "outline_feedback": msg,
            "outline_retry_count": state.get("outline_retry_count", 0) + 1,
        }

    # LLM review: structural reasonableness
    system_content = (
        "You are a strict planning reviewer. Evaluate whether the given outline forms "
        "a natural whitepaper flow. Approve if each section intent is clear and there are "
        "no duplications. If issues exist, provide specific reasons."
        + _EN_ENFORCE
    )

    def _make_outline_text(items: list) -> str:
        return "\n".join(
            f"{it['index']}. [{it['target_period']}] {it['title']} — {it['intent']}"
            for it in items
        )

    def _build_messages(outline_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"Outline:\n{outline_text}\n\nReview result:"},
        ]

    outline_text = _make_outline_text(outline)
    messages = _build_messages(outline_text)
    guard_overhead = estimate_guard_overhead(OutlineCritique.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size > BUDGET_BYTES:
        truncated = [
            {**it, "intent": it["intent"][:80] + "..." if len(it["intent"]) > 80 else it["intent"]}
            for it in outline
        ]
        outline_text = _make_outline_text(truncated)
        messages = _build_messages(outline_text)
        plog("planner_critique", f"budget exceeded ({size/1024:.1f}KB) — outline intent truncated")

    result = structured_call(messages, OutlineCritique, role="judge", temperature=0.0)
    retry = state.get("outline_retry_count", 0) + (0 if result.is_outline_approved else 1)
    plog("planner_critique", f"approved={result.is_outline_approved} retry={retry}")

    # Fail-Safe: force pass after 3 retries
    if not result.is_outline_approved and retry >= 3:
        plog("planner_critique", "FAIL-SAFE: forced pass (3+ retries)")
        return {
            "is_outline_approved": True,
            "outline_feedback": f"[FORCED PASS] {result.feedback}",
            "outline_retry_count": retry,
        }

    return {
        "is_outline_approved": result.is_outline_approved,
        "outline_feedback": result.feedback,
        "outline_retry_count": retry,
    }


def route_outline(state: GraphState) -> str:
    return "init_writing" if state.get("is_outline_approved") else "draft_planner"


# ──────────────────────────────────────────────────────────────
# Phase 4-B [Step 2]: Writing + Fact-check Loop
# ──────────────────────────────────────────────────────────────
def init_writing_node(state: GraphState) -> Dict[str, Any]:
    """Initialize writing loop."""
    return {
        "current_section_index": 0,
        "section_retry_count": 0,
        "previous_draft": "",
        "current_draft": "",
    }


def section_writer_node(state: GraphState) -> Dict[str, Any]:
    """
    v1.2: Injects previous_draft + hallucinated_tokens on rewrite.
          Budget-aware batch splitting with partial draft merging.
    v1.3: English output enforced with proper noun preservation.
    """
    outline = state["outline"]
    idx = state["current_section_index"]
    item = outline[idx]
    period = item["target_period"]
    grouped = state["grouped_chunks"]
    events = filter_by_period(grouped, period)
    retry = state.get("section_retry_count", 0)

    # Step 1: Build retry extras
    extra = ""
    if retry > 0:
        prev = state.get("previous_draft", "")
        bad_tokens = state.get("hallucinated_tokens", [])
        feedback = state.get("draft_feedback", "")
        prev, feedback, bad_tokens = trim_retry_context(
            prev, feedback, bad_tokens, budget_bytes=20 * 1024
        )
        extra = (
            f"\n\n[PREVIOUS REJECTED DRAFT — DO NOT repeat this]\n{prev}\n"
            f"\n[BANNED TOKENS — hallucinated terms not in source]\n{bad_tokens}\n"
            f"\n[REVISION INSTRUCTIONS]\n{feedback}\n"
        )

    system_content = (
        "You are a whitepaper writer. Write the section using ONLY the provided source "
        "event data as evidence. NEVER fabricate proper nouns, dates, or numbers not in "
        "the source. Output markdown body only."
        + _EN_ENFORCE
    )
    user_prefix = (
        f"Section title: {item['title']}\n"
        f"Target period: {period}\n"
        f"Key message: {item['intent']}\n\n"
        f"Source events (use ONLY this data):\n"
    )
    user_suffix = f"{extra}\n\nWrite section body:"

    def _build_messages(events_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"{user_prefix}{events_text}{user_suffix}"},
        ]

    events_text = format_events_for_prompt(events)
    messages = _build_messages(events_text)
    guard_overhead = estimate_guard_overhead(SectionDraft.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size <= BUDGET_BYTES:
        result = structured_call(messages, SectionDraft, role="writer", temperature=0.3)
        plog("section_writer", f"idx={idx} period={period} retry={retry} len={len(result.content)}")
        return {"current_draft": result.content}

    # Budget exceeded — batch split
    data_budget = available_data_budget(
        system_content,
        SectionDraft.model_json_schema(),
        extra_fixed=user_prefix + user_suffix,
    )
    batches = split_items_for_budget(events, format_events_for_prompt, data_budget)
    plog("section_writer", f"idx={idx} budget exceeded ({size/1024:.1f}KB) — {len(batches)} batches")

    partial_system = (
        "You are a whitepaper writer. Write body text covering the key content of the "
        "provided events. Do NOT add information not in the source. "
        "This is a partial batch — content will be merged later."
        + _EN_ENFORCE
    )
    partial_drafts: List[str] = []
    for batch in batches:
        batch_text = format_events_for_prompt(batch)
        batch_msgs = [
            {"role": "system", "content": partial_system},
            {"role": "user", "content": (
                f"Section title: {item['title']}\n"
                f"Target period: {period}\n\n"
                f"Source events (this batch):\n{batch_text}{user_suffix}"
            )},
        ]
        part = structured_call(batch_msgs, SectionDraft, role="writer", temperature=0.3)
        partial_drafts.append(part.content)

    # Merge partial drafts
    merge_input = "\n\n---\n\n".join(
        f"[Partial draft {i+1}]\n{d}" for i, d in enumerate(partial_drafts)
    )
    merge_msgs = [
        {"role": "system", "content": (
            "You are a whitepaper editor. Merge partial drafts for the same section into "
            "one smooth body text. Include all factual information from each partial draft. "
            "Do NOT add new information. Remove duplicates but preserve meaningful details."
            + _EN_ENFORCE
        )},
        {"role": "user", "content": (
            f"Section title: {item['title']}\n\n"
            f"Partial drafts:\n{merge_input}\n\nMerged body:"
        )},
    ]
    merge_guard = estimate_guard_overhead(SectionDraft.model_json_schema())
    merge_size = measure_messages_bytes(merge_msgs) + merge_guard

    if merge_size <= BUDGET_BYTES:
        merged = structured_call(merge_msgs, SectionDraft, role="writer", temperature=0.3)
        content = merged.content
    else:
        plog("section_writer", f"idx={idx} merge also exceeded budget — concatenating")
        content = "\n\n".join(partial_drafts)

    plog("section_writer", f"idx={idx} period={period} retry={retry} len={len(content)}")
    return {"current_draft": content}


def fact_checker_node(state: GraphState) -> Dict[str, Any]:
    """
    v1.2: Mandatory hallucinated_terms extraction.
          Budget-aware batch splitting + cross_check_terms.
    v1.3: English output enforced.
    """
    outline = state["outline"]
    idx = state["current_section_index"]
    item = outline[idx]
    period = item["target_period"]
    grouped = state["grouped_chunks"]
    events = filter_by_period(grouped, period)
    events_text = format_events_for_prompt(events)
    draft = state["current_draft"]

    system_content = (
        "You are a strict auditor. If the draft contains ANY proper noun, date, or number "
        "not present in the source events, you MUST set is_draft_approved=False. "
        "Extract the exact hallucinated tokens into the hallucinated_terms list. "
        "In feedback, specify exactly which parts are problematic."
        + _EN_ENFORCE
    )

    def _build_messages(ev_text: str) -> list:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": (
                f"Source events (ground truth):\n{ev_text}\n\n"
                f"Draft under review:\n{draft}\n\nVerification result:"
            )},
        ]

    messages = _build_messages(events_text)
    guard_overhead = estimate_guard_overhead(FactCheckResult.model_json_schema())
    size = measure_messages_bytes(messages) + guard_overhead

    if size <= BUDGET_BYTES:
        result = structured_call(messages, FactCheckResult, role="judge", temperature=0.0)
        plog("fact_checker", f"idx={idx} approved={result.is_draft_approved} halluc={result.hallucinated_terms[:3]}")
        return {
            "is_draft_approved": result.is_draft_approved,
            "draft_feedback": result.feedback,
            "hallucinated_tokens": result.hallucinated_terms if not result.is_draft_approved else [],
        }

    # Budget exceeded — batch split events (draft kept in each batch)
    data_budget = available_data_budget(
        system_content,
        FactCheckResult.model_json_schema(),
        extra_fixed=f"Source events (ground truth):\n\n\nDraft under review:\n{draft}\n\nVerification result:",
    )
    batches = split_items_for_budget(events, format_events_for_prompt, data_budget)

    batched_system = (
        "You are an auditor. Verify whether the draft content matches the provided event data. "
        "These events are a SUBSET of the full data. If information in the draft is absent "
        "from this batch, record it in hallucinated_terms (it may exist in other batches). "
        "Only set is_draft_approved=False if the draft clearly CONTRADICTS this batch."
        + _EN_ENFORCE
    )

    all_approved = True
    all_feedback: List[str] = []
    all_candidates: List[str] = []

    for batch in batches:
        batch_text = format_events_for_prompt(batch)
        batch_msgs = [
            {"role": "system", "content": batched_system},
            {"role": "user", "content": (
                f"Source events (this batch):\n{batch_text}\n\n"
                f"Draft under review:\n{draft}\n\nVerification result:"
            )},
        ]
        batch_result = structured_call(batch_msgs, FactCheckResult, role="judge", temperature=0.0)
        if not batch_result.is_draft_approved:
            all_approved = False
            all_feedback.append(batch_result.feedback)
        all_candidates.extend(batch_result.hallucinated_terms)

    # Cross-check: only truly absent tokens are hallucinations
    truly_hallucinated = cross_check_terms(all_candidates, events)
    is_approved = len(truly_hallucinated) == 0
    feedback = "; ".join(all_feedback) if all_feedback else "Batch verification passed"
    plog("fact_checker", f"idx={idx} batched: {len(batches)} batches, candidates={len(all_candidates)}, truly_halluc={len(truly_hallucinated)}")

    return {
        "is_draft_approved": is_approved,
        "draft_feedback": feedback,
        "hallucinated_tokens": truly_hallucinated if not is_approved else [],
    }


def route_section_draft(state: GraphState) -> str:
    """
    v1.1 routing:
    - Pass → save_section
    - Fail & retry < 3 → retry_section (rewrite)
    - Fail & retry >= 3 → save_section_with_warning (fail-safe)
    """
    if state.get("is_draft_approved"):
        return "save_section"
    if state.get("section_retry_count", 0) >= 3:
        return "save_section_with_warning"
    return "retry_section"


def retry_section_node(state: GraphState) -> Dict[str, Any]:
    """Prepare rewrite: update previous_draft + increment retry count."""
    return {
        "previous_draft": state.get("current_draft", ""),
        "section_retry_count": state.get("section_retry_count", 0) + 1,
    }


def save_section_node(state: GraphState) -> Dict[str, Any]:
    """Save approved section + advance index + reset scope."""
    idx = state["current_section_index"]
    draft = state["current_draft"]
    plog("save_section", f"idx={idx} APPROVED")
    return {
        "completed_sections": {idx: draft},
        "current_section_index": idx + 1,
        "section_retry_count": 0,
        "previous_draft": "",
    }


def save_section_with_warning_node(state: GraphState) -> Dict[str, Any]:
    """Fail-Safe forced pass: watermark + unverified_sections accumulation."""
    idx = state["current_section_index"]
    draft = state["current_draft"]
    feedback = state.get("draft_feedback", "(no reason recorded)")
    warned = (
        f"> ⚠️ **Unverified Section** — Automatic fact-check failed 3 times.\n"
        f"> Last rejection reason: {feedback}\n\n"
        f"{draft}"
    )
    plog("save_section_with_warning", f"idx={idx} FORCE-PASS")
    return {
        "completed_sections": {idx: warned},
        "unverified_sections": [idx],
        "current_section_index": idx + 1,
        "section_retry_count": 0,
        "previous_draft": "",
    }


def route_next_section(state: GraphState) -> str:
    """All sections done → compiler, otherwise next section."""
    if state["current_section_index"] >= len(state["outline"]):
        return "compiler"
    return "section_writer"


# ──────────────────────────────────────────────────────────────
# Phase 4-B [Step 3]: Assembly → Polish → 2nd Fact-check
# ──────────────────────────────────────────────────────────────
def compiler_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python assembly — no LLM calls."""
    outline = state["outline"]
    completed = state.get("completed_sections", {})
    unverified = state.get("unverified_sections", [])
    compiled = compile_sections(outline, completed, unverified)
    plog("compiler", f"sections={len(completed)} unverified={unverified} len={len(compiled)}")
    return {"final_compiled": compiled, "polish_retry_count": 0}


def polish_node(state: GraphState) -> Dict[str, Any]:
    """Section-by-section polishing + streaming. Prevents 504 on large contexts.
    v1.2: Paragraph-level splitting if section exceeds budget.
    v1.3: English output enforced.
    """
    compiled = state["final_compiled"]
    retry_count = state.get("polish_retry_count", 0)
    doc_header, sections, audit = split_compiled_by_section(compiled)

    if not sections:
        plog("polish", "no sections found — skipping")
        return {"final_output": compiled}

    system_prompt = (
        "You are a proofreading editor. Do NOT add, delete, or modify any factual "
        "information (dates, proper nouns, numbers, causal relationships). "
        "ONLY refine paragraph transitions, coherence, and awkward phrasing. "
        "NEVER fabricate new information. "
        "Maintain markdown structure (headers, lists, blockquote warnings)."
        + _EN_ENFORCE
    )
    guard_overhead = estimate_guard_overhead(PolishedDocument.model_json_schema())

    polished_sections: List[str] = []
    for i, section in enumerate(sections):
        header, body = split_section_header_body(section)
        if not body.strip():
            polished_sections.append(section)
            continue

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Body text:\n{body}\n\nPolished result:"},
        ]
        size = measure_messages_bytes(messages) + guard_overhead

        if size <= BUDGET_BYTES:
            result = structured_call(
                messages, PolishedDocument, role="writer",
                temperature=0.1, stream=True,
            )
            polished_sections.append(header + result.content)
            plog("polish", f"section {i + 1}/{len(sections)} retry={retry_count} len={len(result.content)}")
        else:
            # Section exceeds budget — paragraph-level split
            paragraphs = body.split("\n\n")
            polished_paragraphs: List[str] = []
            for para in paragraphs:
                if not para.strip():
                    polished_paragraphs.append(para)
                    continue
                para_msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Body text:\n{para}\n\nPolished result:"},
                ]
                para_size = measure_messages_bytes(para_msgs) + guard_overhead
                if para_size <= BUDGET_BYTES:
                    para_result = structured_call(
                        para_msgs, PolishedDocument, role="writer",
                        temperature=0.1, stream=True,
                    )
                    polished_paragraphs.append(para_result.content)
                else:
                    polished_paragraphs.append(para)
            polished_body = "\n\n".join(polished_paragraphs)
            polished_sections.append(header + polished_body)
            plog("polish", f"section {i + 1}/{len(sections)} retry={retry_count} paragraphs={len(paragraphs)} (budget exceeded, split)")

    final = doc_header + "".join(polished_sections) + audit
    plog("polish", f"done: sections={len(sections)} total_len={len(final)}")
    return {"final_output": final}


def final_fact_checker_node(state: GraphState) -> Dict[str, Any]:
    """Section-by-section 2nd fact-check + streaming. Prevents 504 on large contexts.
    v1.2: Skips section pairs exceeding budget (auto-approve).
    v1.3: English output enforced.
    """
    original = state["final_compiled"]
    polished = state["final_output"]
    retry_count = state.get("polish_retry_count", 0)

    _, orig_sections, _ = split_compiled_by_section(original)
    _, pol_sections, _ = split_compiled_by_section(polished)

    system_prompt = (
        "You are the final auditor. Compare the original and polished versions. "
        "Verify that no proper nouns, dates, numbers, or facts were added or altered. "
        "Sentence flow changes are allowed; only factual changes count as hallucination."
        + _EN_ENFORCE
    )
    guard_overhead = estimate_guard_overhead(FactCheckResult.model_json_schema())

    # Section count mismatch → full document comparison fallback
    if len(orig_sections) != len(pol_sections) or not orig_sections:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"[ORIGINAL]\n{original}\n\n[POLISHED]\n{polished}\n\nVerification result:"
            )},
        ]
        size = measure_messages_bytes(messages) + guard_overhead
        if size > BUDGET_BYTES:
            plog("final_fact_checker", f"fallback-full budget exceeded ({size/1024:.1f}KB) — auto-approve")
            return {
                "is_draft_approved": True,
                "draft_feedback": f"[Budget exceeded auto-approve] Full comparison not possible ({size/1024:.1f}KB)",
            }
        result = structured_call(messages, FactCheckResult, role="judge",
                                temperature=0.0, stream=True)
        plog("final_fact_checker", f"fallback-full approved={result.is_draft_approved} retry={retry_count}")
        return {
            "is_draft_approved": result.is_draft_approved,
            "draft_feedback": result.feedback,
        }

    # Section-by-section verification
    all_approved = True
    feedback_parts: List[str] = []

    for i, (orig, pol) in enumerate(zip(orig_sections, pol_sections)):
        _, orig_body = split_section_header_body(orig)
        _, pol_body = split_section_header_body(pol)

        if not orig_body.strip() or orig_body.strip() == pol_body.strip():
            continue

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"[ORIGINAL]\n{orig_body}\n\n[POLISHED]\n{pol_body}\n\nVerification result:"
            )},
        ]
        size = measure_messages_bytes(messages) + guard_overhead

        if size > BUDGET_BYTES:
            plog("final_fact_checker", f"section {i + 1} budget exceeded ({size/1024:.1f}KB) — skipped")
            continue

        result = structured_call(messages, FactCheckResult, role="judge",
                                temperature=0.0, stream=True)
        if not result.is_draft_approved:
            all_approved = False
            feedback_parts.append(f"Section {i + 1}: {result.feedback}")
        plog("final_fact_checker", f"section {i + 1}/{len(orig_sections)} approved={result.is_draft_approved}")

    feedback = "; ".join(feedback_parts) if feedback_parts else "All sections verified"
    plog("final_fact_checker", f"overall approved={all_approved} retry={retry_count}")
    return {
        "is_draft_approved": all_approved,
        "draft_feedback": feedback,
    }


def route_final_check(state: GraphState) -> str:
    """Polish verification routing.
    v1.3: Routes to prepare_translation instead of END on approval.
    """
    if state.get("is_draft_approved"):
        return "prepare_translation"
    if state.get("polish_retry_count", 0) >= 2:
        return "fallback_to_compiled"
    return "retry_polish"


def retry_polish_node(state: GraphState) -> Dict[str, Any]:
    return {"polish_retry_count": state.get("polish_retry_count", 0) + 1}


def fallback_to_compiled_node(state: GraphState) -> Dict[str, Any]:
    """Polish bypass — adopt final_compiled as-is."""
    plog("fallback_to_compiled", "polish verification failed — adopting assembly output")
    return {"final_output": state["final_compiled"]}



# ══════════════════════════════════════════════════════════════
# Phase 5: Translation / Rendering (English → Korean)
# ══════════════════════════════════════════════════════════════

def prepare_translation_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python: save English output + extract proper nouns for rendering."""
    english = state["final_output"]
    nouns = extract_proper_nouns(english)
    plog("prepare_translation", f"English output saved ({len(english)} chars), extracted {len(nouns)} proper nouns")
    if nouns:
        psub("prepare_translation", f"sample nouns: {nouns[:10]}")
    return {
        "english_output": english,
        "proper_nouns": nouns,
        "translation_retry_count": 0,
    }


# ──────────────────────────────────────────────────────────────
# Rendering System Prompt Builder
# ──────────────────────────────────────────────────────────────

def _build_render_prompt(
    current_year: str,
    previous_context: str,
    proper_nouns: List[str],
    retry_feedback: str = "",
) -> str:
    """Build the Korean whitepaper rendering system prompt for a specific year.

    Incorporates the senior editor style guide: year/month heading structure,
    integrated narrative, inline KPI emphasis, formal tone (~다/~함/~구축됨).
    """
    noun_ref = "\n".join(f"  - {n}" for n in proper_nouns[:100]) if proper_nouns else "(없음)"

    if previous_context:
        prev_section = (
            f"- 이전 시점의 핵심 흐름: {previous_context}\n"
            " (위 흐름을 바탕으로 이번 연도의 서술을 이어가며 전체 문맥의 연속성을 확보합니다.)"
        )
    else:
        prev_section = "- 첫 연도이므로 이전 맥락 없이 시작합니다."

    retry_hint = ""
    if retry_feedback:
        retry_hint = (
            f"\n\n[이전 렌더링 반려 사유 — 반드시 반영]\n{retry_feedback}\n"
        )

    return (
        "# Role\n"
        "당신은 방대한 데이터를 엮어 유려하고 깊이 있는 공식 비즈니스 백서(Whitepaper)로 "
        "완성하는 수석 에디터입니다.\n"
        f"제공받은 [{current_year}년] 단위의 영문 초안 데이터를 한국어 마크다운 포맷의 "
        "백서로 최종 렌더링하는 임무를 수행합니다.\n\n"
        "# Context\n"
        f"{prev_section}\n\n"
        "# Formatting & Style Guidelines\n\n"
        "1. 헤딩(Heading) 구조 및 텍스트 전개\n"
        f" - 최상위 구분자인 연도는 ## {current_year}년으로 작성합니다.\n"
        f" - 하위 구분자인 월은 ### {current_year}년 X월: [해당 월을 관통하는 핵심 요약 1줄] "
        "형식으로 지정합니다.\n"
        " - 마크다운 헤딩은 오직 ##와 ### 두 가지 수준만 사용합니다.\n"
        " - 월별 상세 내용은 소제목 분리 없이, 주제별로 문단(Paragraph)을 나누어 "
        "논리적인 서술형 텍스트로 전개합니다.\n\n"
        "2. 밀도 있는 통합 서술 (Integrated Narrative)\n"
        " - 제공된 초안의 모든 세부 정보와 배경 상황을 본문 텍스트에 온전히 풀어내어 "
        "백서로서의 깊이를 확보합니다.\n"
        " - 다수의 구체적인 실행 내역이나 팩트를 나열해야 할 경우, 도입 문장 뒤에 "
        "글머리 기호(-)를 연결하여 가독성 있게 정리합니다.\n\n"
        "3. 유동적 KPI의 인라인(Inline) 강조\n"
        " - 연/월별로 불규칙하게 나타나는 성과 지표(KPI) 및 수치 데이터는 본문 서술의 "
        "흐름 속에 자연스럽게 포함합니다.\n"
        " - 핵심 지표명, 수치, 주요 프로젝트명에는 굵은 글씨(Bold)를 적용하여 독자가 "
        "시각적으로 쉽게 인지하도록 강조합니다.\n\n"
        "4. 일관된 Tone & Manner 및 사실 기반 서술\n"
        " - 공식 백서의 신뢰감을 부여하기 위해 객관적이고 명확한 평어체(~다, ~함, ~구축됨)를 "
        "일관되게 적용합니다.\n"
        " - 제공된 영문 초안에 명시된 사실, 날짜, 수치 정보만을 전적으로 활용하여 "
        "문장을 구성합니다.\n"
        " - 영문 초안에 없는 사실, 날짜, 수치를 절대 추가하지 않습니다.\n\n"
        "5. 고유명사 보존\n"
        " - 모든 고유명사(회사명, 프로젝트명, 인명, 기술 용어, 약어)는 원문 그대로 유지합니다.\n"
        " - 음역(transliteration)이나 번역을 하지 않습니다.\n\n"
        "6. 검증 미완료 섹션 경고 처리\n"
        " - 영문 초안에 \"⚠️ **Unverified Section**\"으로 시작하는 경고 블록이 있으면,\n"
        "   해당 경고를 한국어로 번역하여 그대로 유지합니다.\n"
        "   (예: > ⚠️ **검증 미완료 섹션** — 자동 팩트체크 3회 실패. 원본 데이터 대조 필요.)\n\n"
        f"[보존 대상 고유명사 목록]\n{noun_ref}\n\n"
        "# Output Instruction\n"
        f"- 렌더링된 백서 본문은 ## {current_year}년 헤딩으로 시작합니다.\n"
        "- 해당 연도의 마지막 데이터를 서술하는 문장으로 종료합니다."
        f"{retry_hint}"
    )


def translate_node(state: GraphState) -> Dict[str, Any]:
    """Render English whitepaper into styled Korean whitepaper.

    Applies the senior editor style guide:
      - Year/month heading structure (## YYYY년 / ### YYYY년 X월: [요약])
      - Dense integrated narrative with inline KPI emphasis
      - Formal objective tone (~다, ~함, ~구축됨)
      - Fact-only constraint + proper noun preservation

    Multi-year data is rendered year by year with previous_context accumulation
    to maintain narrative continuity across year boundaries.
    """
    english = state["english_output"]
    proper_nouns = state.get("proper_nouns", [])
    retry = state.get("translation_retry_count", 0)
    feedback = state.get("translation_feedback", "")

    # Extract structure from English content
    doc_header, sections, audit = split_compiled_by_section(english)
    years = extract_years_from_content(english)

    if not years:
        years = ["2026"]  # safe fallback

    guard_overhead = estimate_guard_overhead(PolishedDocument.model_json_schema())
    retry_feedback = feedback if retry > 0 else ""

    rendered_parts: List[str] = []
    previous_context = ""

    for yi, year in enumerate(years):
        year_sections = extract_sections_for_year(sections, year)
        if not year_sections:
            continue

        year_text = "\n".join(year_sections)
        system_prompt = _build_render_prompt(
            year, previous_context, proper_nouns, retry_feedback,
        )

        # --- Attempt 1: Full-year rendering ---
        full_msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"# Input Draft Text\n\n{year_text}"},
        ]
        full_size = measure_messages_bytes(full_msgs) + guard_overhead

        if full_size <= BUDGET_BYTES:
            result = structured_call(
                full_msgs, PolishedDocument, role="writer",
                temperature=0.2, stream=True,
            )
            rendered_parts.append(result.content)
            plog("translate", f"year={year} full-render, len={len(result.content)}")
        else:
            # --- Attempt 2: Section-by-section within year ---
            year_rendered: List[str] = [f"## {year}년\n"]
            # Adjust prompt: skip ## year heading since it's manually prepended
            month_prompt = system_prompt.replace(
                f"렌더링된 백서 본문은 ## {year}년 헤딩으로 시작합니다.",
                f"### {year}년 X월 헤딩으로 바로 시작합니다 "
                f"(## {year}년 헤딩은 이미 삽입되어 있으므로 생략).",
            )
            for si, sec in enumerate(year_sections):
                sec_msgs = [
                    {"role": "system", "content": month_prompt},
                    {"role": "user", "content": f"# Input Draft Text\n\n{sec}"},
                ]
                sec_size = measure_messages_bytes(sec_msgs) + guard_overhead

                if sec_size <= BUDGET_BYTES:
                    sec_result = structured_call(
                        sec_msgs, PolishedDocument, role="writer",
                        temperature=0.2, stream=True,
                    )
                    year_rendered.append(sec_result.content)
                else:
                    # Paragraph-level fallback: keep original section
                    year_rendered.append(sec)
                plog("translate", f"year={year} section {si+1}/{len(year_sections)} done")

            rendered_parts.append("\n\n".join(year_rendered))
            plog("translate", f"year={year} section-by-section render")

        # Accumulate previous_context for narrative continuity across years
        if rendered_parts:
            last = rendered_parts[-1]
            previous_context = last[-500:] if len(last) > 500 else last

    # Deterministic audit log translation (no LLM needed)
    kr_audit = ""
    if audit:
        kr_audit = (
            audit
            .replace("### Audit Log", "### 감사 로그")
            .replace("Unverified section indices:", "검증 미완료 섹션 인덱스:")
        )

    final = "\n\n".join(rendered_parts)
    if kr_audit:
        final += "\n" + kr_audit

    plog("translate", f"done: years={years} retry={retry} len={len(final)}")
    return {"final_output": final}


def translation_checker_node(state: GraphState) -> Dict[str, Any]:
    """Verify rendered Korean whitepaper quality.

    Defense layers:
      1. Pure Python: proper noun presence check (reject if >50% missing).
      2. Structural: year/month heading format validation.
      3. LLM spot-check: first year's content for semantic fidelity.

    Every attempt is saved as a candidate for best-of-N fallback.
    """
    english = state["english_output"]
    korean = state["final_output"]
    proper_nouns = state.get("proper_nouns", [])
    retry = state.get("translation_retry_count", 0)

    # --- Proper noun score (used by all layers + candidate tracking) ---
    missing = [n for n in proper_nouns if n not in korean]
    total = max(len(proper_nouns), 1)
    missing_ratio = len(missing) / total
    kept_pct = (1 - missing_ratio) * 100

    # Save this attempt as a candidate regardless of outcome
    candidate = {
        "content": korean,
        "missing_count": len(missing),
        "total_nouns": len(proper_nouns),
        "kept_pct": kept_pct,
        "retry": retry,
    }

    # --- Layer 1: Proper noun check (reject if >50% missing) ---
    if missing_ratio > 0.5:
        msg = f"Too many proper nouns missing ({len(missing)}/{len(proper_nouns)}, kept {kept_pct:.0f}%): {missing[:10]}"
        plog("translation_checker", f"REJECTED (proper nouns, {kept_pct:.0f}% kept): {len(missing)}/{len(proper_nouns)} missing")
        return {
            "is_translation_approved": False,
            "translation_feedback": msg,
            "translation_candidates": [candidate],
        }

    # --- Layer 2: Structural validation ---
    expected_count = count_expected_periods(english)
    struct_ok, struct_msg = validate_korean_structure(korean, expected_count)

    if not struct_ok:
        plog("translation_checker", f"REJECTED (structure, {kept_pct:.0f}% nouns kept): {struct_msg}")
        return {
            "is_translation_approved": False,
            "translation_feedback": struct_msg,
            "translation_candidates": [candidate],
        }
    psub("translation_checker", f"structure: {struct_msg}")

    # --- Layer 3: LLM spot-check ---
    en_sample = english[:2000]
    kr_sample = korean[:2000]

    spot_system = (
        "You are a translation quality auditor. Compare the English source whitepaper "
        "and the rendered Korean whitepaper below. The Korean version uses a different "
        "heading structure (## year / ### month) which is expected and correct. Check for:\n"
        "1. Proper nouns, dates, or numbers that were altered, transliterated, or missing\n"
        "2. Factual information added that is not in the English source\n"
        "3. Factual information from the English source that was omitted\n"
        "4. Tone consistency (formal Korean ~다/~함/~구축됨 expected)\n"
        "Report any issues. Respond in English."
    )
    spot_messages = [
        {"role": "system", "content": spot_system},
        {"role": "user", "content": (
            f"[ENGLISH SOURCE]\n{en_sample}\n\n"
            f"[KOREAN RENDERED]\n{kr_sample}\n\n"
            "Verification result:"
        )},
    ]
    guard_overhead = estimate_guard_overhead(TranslationCheckResult.model_json_schema())
    spot_size = measure_messages_bytes(spot_messages) + guard_overhead

    if spot_size <= BUDGET_BYTES:
        result = structured_call(
            spot_messages, TranslationCheckResult, role="judge", temperature=0.0,
        )
        if not result.is_approved:
            all_missing = list(set(missing + result.missing_terms))
            msg = f"LLM spot-check failed: {result.feedback}. Missing: {all_missing[:10]}"
            plog("translation_checker", f"REJECTED (LLM, {kept_pct:.0f}% nouns kept)")
            return {
                "is_translation_approved": False,
                "translation_feedback": msg,
                "translation_candidates": [candidate],
            }
        psub("translation_checker", f"LLM spot-check passed: {result.feedback[:60]}")
    else:
        psub("translation_checker", "LLM spot-check skipped (budget exceeded)")

    # All checks passed
    plog("translation_checker", f"APPROVED retry={retry} (nouns kept {kept_pct:.0f}%)")
    return {
        "is_translation_approved": True,
        "translation_feedback": f"Rendering approved (nouns kept {kept_pct:.0f}%)",
    }


def route_translation(state: GraphState) -> str:
    """Translation verification routing. Up to 30 retries before fallback."""
    if state.get("is_translation_approved"):
        return "END"
    if state.get("translation_retry_count", 0) >= 30:
        return "fallback_english"
    return "retry_translate"


def retry_translate_node(state: GraphState) -> Dict[str, Any]:
    """Increment retry counter for translation re-attempt."""
    retry = state.get("translation_retry_count", 0) + 1
    plog("retry_translate", f"retrying rendering (attempt {retry}/30)")
    return {"translation_retry_count": retry}


def fallback_english_node(state: GraphState) -> Dict[str, Any]:
    """All retries exhausted — return the top 3 best candidates by proper noun retention.

    If no candidates exist, falls back to English original.
    """
    english = state["english_output"]
    candidates = state.get("translation_candidates", [])
    total_attempts = state.get("translation_retry_count", 0) + 1

    if not candidates:
        plog("fallback_english", "No candidates collected — English original saved separately")
        return {
            "final_output": (
                "> ⚠️ **렌더링 실패** — 한국어 변환 후보 없음. "
                "영문 원본은 _en 파일을 참조하십시오.\n"
            )
        }

    # Sort by missing_count ascending (best = least missing nouns)
    ranked = sorted(candidates, key=lambda c: c["missing_count"])
    top3 = ranked[:3]

    plog("fallback_english",
         f"{total_attempts}회 시도 후 기준 미달 — 상위 {len(top3)}건 반환 "
         f"(best {top3[0]['kept_pct']:.0f}% kept)")

    parts: List[str] = []
    parts.append(
        f"> ⚠️ **렌더링 공지** — {total_attempts}회 시도 후 고유명사 기준 미달. "
        f"상위 {len(top3)}건을 아래 제시합니다.\n"
    )

    for i, c in enumerate(top3, 1):
        parts.append(
            f"\n---\n\n"
            f"## 후보 {i} (고유명사 유지율: {c['kept_pct']:.0f}%, "
            f"누락 {c['missing_count']}/{c['total_nouns']}, "
            f"attempt #{c['retry']})\n\n"
            f"{c['content']}"
        )

    return {"final_output": "\n".join(parts)}
