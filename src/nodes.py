"""
LangGraph node functions. One Node = One Task principle strictly enforced.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Dict, List, Any

from langgraph.types import Send

from .state import GraphState
from .schemas import (
    ExtractedEvent, PeriodSummary, GlobalTheme,
    Outline, OutlineCritique,
    SectionDraft, FactCheckResult, PolishedDocument,
)
from .llm import structured_call, Timeout504Error, effective_budget, _504_MAX_STEPS
from .utils import (
    is_valid_date, chrono_sort_and_group, filter_by_period,
    validate_outline_periods, compile_sections, format_events_for_prompt,
    split_compiled_by_section, split_section_header_body,
    extract_proper_nouns,
    extract_years_from_content, extract_sections_for_year,
)
from .logger import plog, psub
from .context_guard import (
    estimate_guard_overhead, available_data_budget,
    split_items_for_budget, trim_retry_context, cross_check_terms,
    measure_messages_bytes,
)
from .prompt_config import (
    get_summary_context, get_planning_context,
    get_writing_context, get_translation_context,
)
import functools


LOCAL_DATA_PATH = "./data/records.jsonl"

# ══════════════════════════════════════════════════════════════
# 504 retry decorator: re-runs the entire node with reduced budget
# ══════════════════════════════════════════════════════════════

def retry_on_504(fn):
    """Decorator: on Timeout504Error, re-run the entire node function.

    504 reduction is LOCAL to this node only:
      - reset_504_state() at entry (clean slate)
      - On 504: structured_call reduces budget, raises Timeout504Error
      - Decorator re-runs the node with reduced effective_budget()
      - On success OR exhaustion: reset_504_state() restores full budget

    Subsequent nodes always start with the original full budget/tokens
    to preserve maximum output quality.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from .llm import reset_504_state
        reset_504_state()  # clean slate for this node
        try:
            for attempt in range(_504_MAX_STEPS):
                try:
                    return fn(*args, **kwargs)
                except Timeout504Error:
                    psub("504_retry",
                         f"{fn.__name__} — node re-run ({attempt + 1}/{_504_MAX_STEPS}), "
                         f"budget now {effective_budget() // 1024}KB")
            return fn(*args, **kwargs)  # final attempt, let exception propagate
        finally:
            reset_504_state()  # always restore full budget for next node
    return wrapper


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


@retry_on_504
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

    if size > effective_budget():
        excess = size - effective_budget() + 512
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


@retry_on_504
def period_summarizer_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Monthly key trend summary in exactly 3 sentences.
    v1.2: Budget-aware batch splitting + sub-summary merging.
    v1.3: English output enforced.
    """
    period = payload["period"]
    events = payload["events"]

    # ── 사용자 커스텀: prompt_config.py의 PURPOSE + TONE이 여기에 주입됩니다 ──
    system_content = (
        "You are a period trend analyst. Summarize the given event list into exactly "
        "3 sentences capturing the key trends. Do NOT add content not present in the events."
        + get_summary_context()
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

    if size <= effective_budget():
        result = structured_call(messages, PeriodSummary, role="default", temperature=0.2)
        plog("period_summarizer", f"{period}: {result.summary[:60]}...")
        return {"period_summaries": {period: result.summary}}

    # Budget exceeded — batch split
    data_budget = available_data_budget(
        system_content,
        PeriodSummary.model_json_schema(),
        extra_fixed=user_template.format(period=period, events_text=""),
        budget_override=effective_budget(),
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


@retry_on_504
def theme_analyzer_node(state: GraphState) -> Dict[str, Any]:
    """Derive overall theme in 1 paragraph.
    v1.2: Drops oldest monthly summaries if budget exceeded.
    v1.3: English output enforced.
    """
    summaries = state["period_summaries"]
    sorted_periods = sorted(summaries.keys())

    # ── 사용자 커스텀: prompt_config.py의 PURPOSE + TONE이 여기에 주입됩니다 ──
    system_content = (
        "You are a macro analyst. Given monthly summaries, write exactly 1 paragraph "
        "capturing the overarching insight into the project's performance and risk trajectory. "
        "Do NOT add content not present in the summaries."
        + get_summary_context()
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

    while size > effective_budget() and len(active_periods) > 1:
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
@retry_on_504
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

    # ── 사용자 커스텀: prompt_config.py의 PURPOSE + AUDIENCE가 여기에 주입됩니다 ──
    system_content = (
        "You are a whitepaper planner. Create an outline based on the given theme and "
        "monthly summaries. Each outline item must cover exactly one 'YYYY-MM' period "
        "(target_period). "
        f"Available period keys: {available_periods}\n"
        "Only use periods from this list. Sort in chronological order."
        + get_planning_context()
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

    if size > effective_budget():
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


@retry_on_504
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

    if size > effective_budget():
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


@retry_on_504
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
        # retry extras 상한: effective_budget의 20% (504 감축 연동)
        retry_budget = max(effective_budget() // 5, 4 * 1024)
        prev, feedback, bad_tokens = trim_retry_context(
            prev, feedback, bad_tokens, budget_bytes=retry_budget
        )
        extra = (
            f"\n\n[PREVIOUS REJECTED DRAFT — DO NOT repeat this]\n{prev}\n"
            f"\n[BANNED TOKENS — hallucinated terms not in source]\n{bad_tokens}\n"
            f"\n[REVISION INSTRUCTIONS]\n{feedback}\n"
        )

    # ── 사용자 커스텀: prompt_config.py의 PURPOSE + TONE + AUDIENCE + CUSTOM이 주입됩니다 ──
    # ── 편향 설정이 있어도 fact_checker가 원본 데이터 외 사실 추가를 차단합니다 ──
    system_content = (
        "You are a whitepaper writer. Write the section using ONLY the provided source "
        "event data as evidence. NEVER fabricate proper nouns, dates, or numbers not in "
        "the source. Output markdown body only."
        + get_writing_context()
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

    if size <= effective_budget():
        result = structured_call(messages, SectionDraft, role="writer", temperature=0.3)
        plog("section_writer", f"idx={idx} period={period} retry={retry} len={len(result.content)}")
        return {"current_draft": result.content}

    # Budget exceeded — batch split
    data_budget = available_data_budget(
        system_content,
        SectionDraft.model_json_schema(),
        extra_fixed=user_prefix + user_suffix,
        budget_override=effective_budget(),
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

    if merge_size <= effective_budget():
        merged = structured_call(merge_msgs, SectionDraft, role="writer", temperature=0.3)
        content = merged.content
    else:
        plog("section_writer", f"idx={idx} merge also exceeded budget — concatenating")
        content = "\n\n".join(partial_drafts)

    plog("section_writer", f"idx={idx} period={period} retry={retry} len={len(content)}")
    return {"current_draft": content}


@retry_on_504
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

    if size <= effective_budget():
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
        budget_override=effective_budget(),
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
# Phase 4-B [Step 3]: Assembly → Polish
# ──────────────────────────────────────────────────────────────
def compiler_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python assembly — no LLM calls."""
    outline = state["outline"]
    completed = state.get("completed_sections", {})
    unverified = state.get("unverified_sections", [])
    compiled = compile_sections(outline, completed, unverified)
    plog("compiler", f"sections={len(completed)} unverified={unverified} len={len(compiled)}")
    return {"final_compiled": compiled}


@retry_on_504
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

        if size <= effective_budget():
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
                if para_size <= effective_budget():
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

    }


# ──────────────────────────────────────────────────────────────
# Phase 5: Translation Prompts and Helpers — v2.0
# ──────────────────────────────────────────────────────────────

# Completeness thresholds
_KR_EN_CHAR_RATIO_MIN = 0.35   # Korean chars / English chars minimum
_PARAGRAPH_CHUNK_MAX_BYTES = 8 * 1024  # 8KB max per paragraph chunk


def _build_faithful_translate_prompt(proper_nouns: List[str]) -> str:
    """Build translation system prompt focused on faithful completeness.

    v2.0: Replaces _build_render_prompt. Emphasis shifted from "rendering"
    (which LLMs interpret as creative rewriting/summarization) to "faithful
    translation" (1:1 sentence correspondence, no compression).
    """
    noun_ref = "\n".join(f"  - {n}" for n in proper_nouns[:100]) if proper_nouns else "(없음)"
    return (
        "당신은 영문 백서를 한국어로 충실하게 번역하는 전문 번역가입니다.\n\n"
        "## 핵심 원칙 (최우선: 완전성)\n"
        "1. **완전 번역**: 원문의 모든 문장을 빠짐없이 번역합니다. "
        "요약·생략·압축·의역 절대 금지.\n"
        "2. **1:1 대응**: 원문 각 문장·항목·수치가 번역문에 반드시 대응되어야 합니다.\n"
        "3. **사실 보존**: 날짜, 수치, 고유명사, 인과관계를 정확히 유지합니다.\n"
        "4. **고유명사**: 회사명, 프로젝트명, 인명, 기술 용어, 약어는 원문 그대로 유지.\n"
        "5. **마크다운 구조 유지**: 리스트(-), 볼드(**), 인용(>), 코드(`) 등을 보존합니다.\n"
        "6. **톤**: 공식 백서 평어체 (~다, ~함, ~구축됨).\n"
        "7. **KPI 강조**: 핵심 지표명, 수치, 주요 프로젝트명에는 **볼드** 적용.\n"
        "8. **경고 블록**: \"⚠️ **Unverified Section**\" → "
        "\"⚠️ **검증 미완료 섹션**\"으로 번역.\n\n"
        f"[보존 대상 고유명사 목록]\n{noun_ref}\n\n"
        "## 출력\n"
        "- 원문의 모든 문장을 한국어로 번역하여 content 필드에 담습니다.\n"
        "- 번역문이 원문보다 짧으면 실패입니다. 원문의 모든 정보를 포함해야 합니다.\n"
        # ── 사용자 커스텀: prompt_config.py의 PURPOSE + TONE + AUDIENCE가 한국어로 주입 ──
        + get_translation_context()
    )


def _build_section_translate_prompt(
    year: str, month: str, proper_nouns: List[str],
    previous_context: str = "",
) -> str:
    """Build section-level translation prompt with heading generation."""
    base = _build_faithful_translate_prompt(proper_nouns)
    heading_instruction = (
        f"\n## 섹션 헤딩 생성\n"
        f"- 번역 출력의 첫 줄은 반드시 다음 형식이어야 합니다:\n"
        f"  ### {year}년 {month}월: [해당 월을 관통하는 핵심 요약 1줄]\n"
        f"- 이 헤딩 뒤에 빈 줄을 두고 번역된 본문을 이어갑니다.\n"
    )
    context_section = ""
    if previous_context:
        context_section = (
            f"\n## 이전 맥락\n"
            f"이전 섹션의 흐름: {previous_context[:300]}\n"
            f"이 흐름을 이어받아 연속성 있게 번역합니다.\n"
        )
    return base + heading_instruction + context_section


def _build_korean_gen_prompt(
    year: str, month: str, events: List[Dict],
    period_summary: str, proper_nouns: List[str],
) -> str:
    """Build prompt for direct Korean generation from source data (fallback path).

    Used when translation fails: generates Korean whitepaper section directly
    from the pipeline's extracted events and period summaries.
    """
    noun_ref = "\n".join(f"  - {n}" for n in proper_nouns[:50]) if proper_nouns else "(없음)"
    events_text = format_events_for_prompt(events)
    return (
        f"당신은 {year}년 {month}월 데이터를 바탕으로 한국어 백서 섹션을 "
        "작성하는 편집자입니다.\n\n"
        "## 원본 데이터\n"
        f"[월간 요약] {period_summary}\n\n"
        f"[상세 이벤트]\n{events_text}\n\n"
        "## 작성 지침\n"
        "1. 제공된 모든 이벤트를 빠짐없이 포함하여 상세한 서술형 한국어 텍스트로 작성.\n"
        "2. 날짜, 수치, 고유명사를 정확히 유지.\n"
        "3. 공식 백서 평어체 (~다, ~함, ~구축됨).\n"
        "4. 핵심 지표/수치는 **볼드**, 다수 항목은 글머리 기호(-) 정리.\n"
        f"5. 첫 줄: ### {year}년 {month}월: [핵심 요약 1줄]\n\n"
        f"[보존 대상 고유명사 목록]\n{noun_ref}\n"
    )


def _split_into_paragraph_chunks(
    text: str, max_bytes: int = _PARAGRAPH_CHUNK_MAX_BYTES,
) -> List[str]:
    """Split text into chunks at paragraph boundaries, respecting byte limit."""
    paragraphs = text.split("\n\n")
    chunks: List[str] = []
    current: List[str] = []
    current_bytes = 0

    for para in paragraphs:
        para_bytes = len(para.encode("utf-8"))
        if current and current_bytes + para_bytes + 2 > max_bytes:
            chunks.append("\n\n".join(current))
            current = [para]
            current_bytes = para_bytes
        else:
            current.append(para)
            current_bytes += para_bytes + 2

    if current:
        chunks.append("\n\n".join(current))

    return chunks if chunks else [text]


def _check_completeness(english: str, korean: str) -> bool:
    """Check if Korean output preserves enough content relative to English input.

    Korean text is character-dense (1 Korean char ≈ 2-3 English chars of meaning).
    A char ratio of 0.35+ typically indicates faithful translation.
    Below 0.35 strongly suggests summarization/truncation.
    """
    en_len = len(english.strip())
    kr_len = len(korean.strip())
    if en_len == 0:
        return True
    return (kr_len / en_len) >= _KR_EN_CHAR_RATIO_MIN


@retry_on_504
def translate_node(state: GraphState) -> Dict[str, Any]:
    """Render English whitepaper into Korean — v2.0 paragraph-chunked faithful translation.

    Key change from v1: ALWAYS section-by-section with paragraph fallback.
    No full-document single-call path. Prevents output token truncation.

    Pipeline per section:
      1. Try full-section faithful translation (single LLM call)
      2. Completeness check (Korean/English char ratio ≥ 0.35)
      3. If incomplete → split into paragraph chunks, translate each individually
      4. If paragraphs fail → generate Korean from source pipeline data (events/summaries)
      5. Direct concatenation (no LLM merge step — prevents compression)
    """
    english = state["english_output"]
    proper_nouns = state.get("proper_nouns", [])
    period_summaries = state.get("period_summaries", {})
    grouped_chunks = state.get("grouped_chunks", {})

    doc_header, sections, audit = split_compiled_by_section(english)
    years = extract_years_from_content(english)
    if not years:
        years = ["2026"]

    guard_overhead = estimate_guard_overhead(PolishedDocument.model_json_schema())

    rendered_parts: List[str] = []
    total_en_chars = 0
    total_kr_chars = 0
    previous_context = ""

    for year in years:
        year_sections = extract_sections_for_year(sections, year)
        if not year_sections:
            continue

        year_rendered: List[str] = [f"## {year}년\n"]

        for si, sec in enumerate(year_sections):
            # ── Parse section metadata ──
            period_match = re.search(r'_Target period:\s*(\d{4}-\d{2})_', sec)
            target_period = period_match.group(1) if period_match else ""
            month_num = (
                str(int(target_period.split("-")[1]))
                if target_period else "?"
            )
            title_match = re.search(r'^## (.+?)(?:\s{2})?$', sec, re.MULTILINE)
            english_title = title_match.group(1).strip() if title_match else ""

            header, body = split_section_header_body(sec)
            if not body.strip():
                year_rendered.append(sec)
                continue

            total_en_chars += len(body)
            korean_body = None

            # ══════════════════════════════════════════════════════
            # Step 1: Full-section faithful translation
            # ══════════════════════════════════════════════════════
            section_prompt = _build_section_translate_prompt(
                year, month_num, proper_nouns, previous_context,
            )
            sec_msgs = [
                {"role": "system", "content": section_prompt},
                {"role": "user", "content": (
                    f"다음 영문 백서 섹션을 한국어로 완전 번역하십시오.\n\n"
                    f"[섹션 제목] {english_title}\n"
                    f"[대상 기간] {year}년 {month_num}월\n\n"
                    f"---\n\n{body}"
                )},
            ]
            sec_size = measure_messages_bytes(sec_msgs) + guard_overhead

            if sec_size <= effective_budget():
                try:
                    result = structured_call(
                        sec_msgs, PolishedDocument, role="writer",
                        temperature=0.2, stream=True,
                    )
                    if _check_completeness(body, result.content):
                        korean_body = result.content
                        # Ensure heading is present
                        if not korean_body.strip().startswith("###"):
                            heading = f"### {year}년 {month_num}월"
                            if english_title:
                                heading += f": {english_title}"
                            korean_body = heading + "\n\n" + korean_body
                        plog("translate",
                             f"[{target_period}] full-section OK "
                             f"(en={len(body)} kr={len(result.content)} "
                             f"ratio={len(result.content)/max(len(body),1):.2f})")
                    else:
                        plog("translate",
                             f"[{target_period}] full-section INCOMPLETE "
                             f"(en={len(body)} kr={len(result.content)} "
                             f"ratio={len(result.content)/max(len(body),1):.2f})"
                             " → paragraph split")
                except Exception as e:
                    plog("translate",
                         f"[{target_period}] full-section error: {e}"
                         " → paragraph split")

            # ══════════════════════════════════════════════════════
            # Step 2: Paragraph-level translation (anti-truncation)
            # ══════════════════════════════════════════════════════
            if korean_body is None:
                chunk_prompt = _build_faithful_translate_prompt(proper_nouns)
                chunks = _split_into_paragraph_chunks(body)
                kr_chunks: List[str] = []
                chunk_failed = False

                for ci, chunk in enumerate(chunks):
                    if not chunk.strip():
                        kr_chunks.append(chunk)
                        continue
                    chunk_msgs = [
                        {"role": "system", "content": chunk_prompt},
                        {"role": "user", "content": (
                            "다음 영문 텍스트를 한국어로 완전 번역하십시오.\n\n"
                            f"{chunk}"
                        )},
                    ]
                    chunk_size = measure_messages_bytes(chunk_msgs) + guard_overhead

                    if chunk_size <= effective_budget():
                        try:
                            cr = structured_call(
                                chunk_msgs, PolishedDocument, role="writer",
                                temperature=0.2, stream=True,
                            )
                            kr_chunks.append(cr.content)
                        except Exception as e:
                            plog("translate",
                                 f"[{target_period}] chunk {ci+1}/{len(chunks)}"
                                 f" error: {e}")
                            chunk_failed = True
                            break
                    else:
                        plog("translate",
                             f"[{target_period}] chunk {ci+1}/{len(chunks)}"
                             " exceeds budget")
                        chunk_failed = True
                        break

                if not chunk_failed and kr_chunks:
                    # Generate heading for paragraph-split path
                    heading_line = f"### {year}년 {month_num}월"
                    if english_title:
                        heading_line += f": {english_title}"
                    korean_body = heading_line + "\n\n" + "\n\n".join(kr_chunks)
                    plog("translate",
                         f"[{target_period}] paragraph-split done "
                         f"({len(chunks)} chunks → "
                         f"en={len(body)} kr={len(korean_body)})")

            # ══════════════════════════════════════════════════════
            # Step 3: Fallback — Korean from source pipeline data
            # ══════════════════════════════════════════════════════
            if korean_body is None:
                events = grouped_chunks.get(target_period, [])
                summary = period_summaries.get(target_period, "")

                if events:
                    gen_prompt = _build_korean_gen_prompt(
                        year, month_num, events, summary, proper_nouns,
                    )
                    gen_msgs = [
                        {"role": "system", "content": gen_prompt},
                        {"role": "user", "content": (
                            "위 데이터를 바탕으로 상세한 한국어 백서 섹션을 작성하십시오."
                        )},
                    ]
                    try:
                        gr = structured_call(
                            gen_msgs, PolishedDocument, role="writer",
                            temperature=0.3,
                        )
                        korean_body = gr.content
                        plog("translate",
                             f"[{target_period}] FALLBACK: Korean from source "
                             f"(events={len(events)} kr={len(korean_body)})")
                    except Exception as e:
                        plog("translate",
                             f"[{target_period}] fallback error: {e} — keeping English")
                        korean_body = (
                            f"### {year}년 {month_num}월: {english_title}\n\n{body}"
                        )
                else:
                    plog("translate",
                         f"[{target_period}] no source data — keeping English")
                    korean_body = (
                        f"### {year}년 {month_num}월: {english_title}\n\n{body}"
                    )

            total_kr_chars += len(korean_body)
            previous_context = korean_body[-500:] if korean_body else ""

            year_rendered.append(korean_body)

        rendered_parts.append("\n\n".join(year_rendered))

    # ── Audit log (deterministic) ──
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

    overall_ratio = total_kr_chars / max(total_en_chars, 1)
    plog("translate",
         f"v2.0 complete: years={years} "
         f"en_chars={total_en_chars} kr_chars={total_kr_chars} "
         f"ratio={overall_ratio:.2f}")

    return {"final_output": final}
