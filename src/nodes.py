"""
Pipeline nodes — v3.0 (KR-first, category-based architecture).
═══════════════════════════════════════════════════════════════

4-Step pipeline: Knowledge Structuring → Narrative Flow → Executive Summary → Hybrid Assembly.

13 nodes + 4 routers.
ALL LLM output is Korean with proper nouns preserved in original language.

Node naming convention:
    <step>_<verb>_node          → LangGraph node function
    route_<decision>            → conditional edge router
"""
from __future__ import annotations

import json
import os
import functools
import traceback as _tb
from pathlib import Path
from typing import Any, Dict, List

from .state import GraphState
from .schemas import (
    KnowledgeEntry,
    CategoryAnalysis,
    SectionPlanItem,
    NarrativeFlow,
    NarrativeCritique,
    SectionDraft,
    PolishedDocument,
)
from .llm import (
    structured_call,
    extract_json,
    Timeout504Error,
    effective_budget,
    effective_max_tokens,
    reset_504_state,
    effective_reasoning,
    get_model,
)
from .context_guard import (
    available_data_budget,
    measure_text_bytes,
    split_items_for_budget,
)
from .prompt_config import (
    get_extraction_context,
    get_analysis_context,
    get_writing_context,
    get_proper_noun_guard,
    get_document_title,
)
from .utils import (
    CATEGORIES,
    normalize_category,
    deduplicate_entries,
    build_knowledge_base,
    check_category_balance,
    build_temporal_index,
    format_entries_for_prompt,
    format_category_entries,
    compile_executive_summary,
    compile_whitepaper,
    split_by_section,
    export_knowledge_base,
)
from .logger import plog, psub, log_error
from .artifacts import save_json, save_text

# ══════════════════════════════════════════════════════════════
# Global Config
# ══════════════════════════════════════════════════════════════

LOCAL_DATA_PATH = "data/records.jsonl"




# ══════════════════════════════════════════════════════════════
# retry_on_504 decorator
# ══════════════════════════════════════════════════════════════

def retry_on_504(fn):
    """LLM 노드 래퍼: Timeout504Error 발생 시 504 상태 리셋 후 노드 전체 재시도 (최대 10회)."""

    @functools.wraps(fn)
    def wrapper(state):
        reset_504_state()
        for attempt in range(11):
            try:
                result = fn(state)
                reset_504_state()
                return result
            except Timeout504Error as e:
                if attempt >= 10:
                    log_error(fn.__name__, e, _tb.format_exc())
                    raise
                plog(fn.__name__, f"504 retry #{attempt + 1}")
        raise RuntimeError("unreachable")

    return wrapper


# ══════════════════════════════════════════════════════════════
#  STEP 1 — Knowledge Structuring
# ══════════════════════════════════════════════════════════════


# ── 1-1  load_docs ───────────────────────────────────────────

def load_docs_node(state: GraphState) -> dict:
    """원시 문서(JSONL/JSON) 로드.

    - JSONL(줄 단위 JSON) 우선 시도
    - 실패 시 JSON 배열 파싱으로 폴백
    - 파싱 불가능한 줄은 경고 후 스킵
    """
    path = Path(LOCAL_DATA_PATH)
    if not path.exists():
        raise FileNotFoundError(f"데이터 파일 없음: {path.resolve()}")

    raw_text = path.read_text(encoding="utf-8")
    docs: List[Dict[str, Any]] = []

    # Strategy 1: JSONL (line-by-line)
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    jsonl_ok = False
    if lines:
        for i, line in enumerate(lines, 1):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    docs.append(obj)
                    jsonl_ok = True
                elif isinstance(obj, list):
                    # First line is a JSON array → fall back to full-parse
                    docs.clear()
                    jsonl_ok = False
                    break
            except json.JSONDecodeError:
                psub("load_docs", f"line {i} 파싱 실패 — 스킵")

    # Strategy 2: JSON array fallback
    if not jsonl_ok:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                docs = [d for d in parsed if isinstance(d, dict)]
            elif isinstance(parsed, dict):
                docs = [parsed]
        except json.JSONDecodeError as e:
            raise ValueError(f"데이터 파일 JSON 파싱 실패: {e}")

    if not docs:
        raise ValueError(f"로드된 문서 0건 — {path} 확인 필요")

    plog("load_docs", f"{len(docs)}건 로드 완료 ({path})")
    return {"raw_docs": docs}


# ── 1-2  knowledge_extractor ─────────────────────────────────

_EXTRACTION_SYSTEM = """\
당신은 프로젝트 문서 분석 전문가입니다.
제공된 원시 문서에서 핵심 정보를 추출하고, 다음 4개 카테고리 중 하나로 분류하십시오:
- Architecture_and_Tech: 기술 스택, 아키텍처 변경, 인프라 최적화
- Risk_and_Troubleshooting: 크리티컬 이슈, 위기 대응, 기술 부채 해결
- Business_and_Feature: 비즈니스 요구사항, 기능 배포, 마일스톤 달성
- Lessons_Learned: 조직적 성장, 교훈, 향후 방향

한국어로 작성하되, 기술 고유명사는 원어 그대로 사용하십시오.
날짜가 명시되어 있으면 date_hint에 기록하고, 불명확하면 null로 두십시오.
"""


@retry_on_504
def knowledge_extractor_node(state: GraphState) -> dict:
    """원시 문서 → 구조화된 KnowledgeEntry 추출 (LLM per doc).

    문서별로 structured_call을 호출하여 KnowledgeEntry를 생성.
    대량 문서 시 예산 내에서 분할 처리.
    """
    raw_docs = state.get("raw_docs", [])
    if not raw_docs:
        plog("knowledge_extractor", "raw_docs 비어있음 — 스킵")
        return {"knowledge_entries": []}

    system_base = _EXTRACTION_SYSTEM + get_extraction_context() + get_proper_noun_guard()
    all_entries: List[Dict[str, Any]] = []
    failed_count = 0

    for i, doc in enumerate(raw_docs):
        doc_text = json.dumps(doc, ensure_ascii=False, indent=1)

        # 문서가 너무 크면 잘라서 전송
        budget = available_data_budget(
            system_base,
            KnowledgeEntry.model_json_schema(),
            budget_override=effective_budget(),
        )
        doc_bytes = measure_text_bytes(doc_text)
        if doc_bytes > budget:
            # Truncate to fit budget (keep beginning which has most info)
            target_chars = int(budget * 0.9)  # leave margin
            doc_text = doc_text[:target_chars] + "\n...[truncated]"
            psub("knowledge_extractor", f"doc[{i}] 절삭: {doc_bytes}B → ~{target_chars}B")

        messages = [
            {"role": "system", "content": system_base},
            {
                "role": "user",
                "content": (
                    f"문서 #{i} (source_ref: records.jsonl:{i}):\n"
                    f"```json\n{doc_text}\n```\n\n"
                    "위 문서에서 KnowledgeEntry를 추출하십시오. "
                    f"source_ref는 \"records.jsonl:{i}\"로 설정하십시오."
                ),
            },
        ]

        try:
            entry: KnowledgeEntry = structured_call(
                messages,
                response_model=KnowledgeEntry,
                role="extractor",
                temperature=0.0,
                reasoning_effort="low",
            )
            entry_dict = entry.model_dump()
            # Ensure source_ref consistency
            entry_dict["source_ref"] = f"records.jsonl:{i}"
            # Normalize category
            entry_dict["category"] = normalize_category(entry_dict.get("category", ""))
            all_entries.append(entry_dict)
        except Exception as e:
            failed_count += 1
            log_error("knowledge_extractor", e, _tb.format_exc())
            psub("knowledge_extractor", f"doc[{i}] 추출 실패: {type(e).__name__}")

        if (i + 1) % 10 == 0:
            psub("knowledge_extractor", f"진행 {i + 1}/{len(raw_docs)}")

    plog(
        "knowledge_extractor",
        f"추출 완료: {len(all_entries)}건 성공, {failed_count}건 실패 "
        f"(총 {len(raw_docs)}건)",
    )

    save_json("step1_raw_entries.json", all_entries)
    return {"knowledge_entries": all_entries}


# ── 1-3  knowledge_aggregator ────────────────────────────────

def knowledge_aggregator_node(state: GraphState) -> dict:
    """추출된 엔트리 → 중복 제거 → 카테고리별 지식 베이스 구축."""
    entries = state.get("knowledge_entries", [])
    if not entries:
        plog("knowledge_aggregator", "knowledge_entries 비어있음 — 빈 KB 반환")
        return {"knowledge_base": {cat: [] for cat in CATEGORIES}}

    entries = deduplicate_entries(entries)
    kb = build_knowledge_base(entries)
    warnings = check_category_balance(kb)

    for w in warnings:
        psub("knowledge_aggregator", f"⚠️ {w}")

    counts = {cat: len(kb[cat]) for cat in CATEGORIES}
    plog(
        "knowledge_aggregator",
        f"KB 구축 완료: {sum(counts.values())}건 (중복 제거 후) | {counts}",
    )

    save_json("step1_knowledge_base.json", export_knowledge_base(kb))
    return {"knowledge_base": kb}


# ── 1-4  temporal_indexer ────────────────────────────────────

def temporal_indexer_node(state: GraphState) -> dict:
    """지식 베이스에서 시간순 인덱스 생성."""
    kb = state.get("knowledge_base", {})
    all_entries: List[Dict] = []
    for cat in CATEGORIES:
        all_entries.extend(kb.get(cat, []))

    ti = build_temporal_index(all_entries)

    dated = sum(1 for e in ti if e.get("period", "undated") != "undated")
    undated = len(ti) - dated
    plog("temporal_indexer", f"시간순 인덱스: 날짜 {dated}건, 미상 {undated}건")

    save_json("step1_temporal_index.json", ti)
    return {"temporal_index": ti}


# ══════════════════════════════════════════════════════════════
#  STEP 2 — Narrative Flow
# ══════════════════════════════════════════════════════════════


# ── 2-1  category_analyzer ───────────────────────────────────

_ANALYSIS_SYSTEM_TEMPLATE = """\
당신은 비즈니스 분석 전문가입니다.
아래 '{category}' 카테고리의 데이터를 심층 분석하십시오.

핵심 발견사항(key_findings), 인과 사슬(causal_chain: 문제→대응→결과), \
비즈니스 시사점(implications)을 도출하십시오.

한국어로 작성하되, 기술 고유명사는 원어 그대로 사용하십시오.
"""


@retry_on_504
def category_analyzer_node(state: GraphState) -> dict:
    """카테고리별 심층 분석 (LLM per category).

    각 카테고리의 엔트리를 분석하여 CategoryAnalysis를 생성.
    빈 카테고리는 경고 텍스트로 대체.
    """
    kb = state.get("knowledge_base", {})
    analyses: Dict[str, str] = {}
    analyses_full: Dict[str, Any] = {}

    for cat in CATEGORIES:
        entries = kb.get(cat, [])

        if not entries:
            warning = f"카테고리 '{cat}' — 데이터 없음. 분석 불가."
            analyses[cat] = warning
            analyses_full[cat] = {"category": cat, "warning": warning}
            psub("category_analyzer", f"⚠️ {cat}: 0건 — 스킵")
            continue

        sys_prompt = (
            _ANALYSIS_SYSTEM_TEMPLATE.format(category=cat)
            + get_analysis_context()
            + get_proper_noun_guard()
        )

        # Format entries and check budget
        entries_text = format_entries_for_prompt(entries)
        budget = available_data_budget(
            sys_prompt,
            CategoryAnalysis.model_json_schema(),
            budget_override=effective_budget(),
        )

        # Split if entries exceed budget
        batches = split_items_for_budget(
            entries,
            format_entries_for_prompt,
            budget,
        )

        batch_findings: List[str] = []
        batch_chains: List[str] = []
        batch_implications: List[str] = []

        for bi, batch in enumerate(batches):
            batch_text = format_entries_for_prompt(batch)
            messages = [
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": (
                        f"[{cat}] 카테고리 데이터 "
                        f"(배치 {bi + 1}/{len(batches)}, {len(batch)}건):\n\n"
                        f"{batch_text}"
                    ),
                },
            ]

            try:
                result: CategoryAnalysis = structured_call(
                    messages,
                    response_model=CategoryAnalysis,
                    role="analyzer",
                    temperature=0.2,
                )
                batch_findings.extend(result.key_findings)
                batch_chains.append(result.causal_chain)
                batch_implications.append(result.implications)
            except Exception as e:
                log_error("category_analyzer", e, _tb.format_exc())
                psub("category_analyzer", f"{cat} 배치 {bi + 1} 실패: {e}")

        # Merge multi-batch results into single analysis text
        analysis_text = _merge_category_analysis(
            cat, batch_findings, batch_chains, batch_implications
        )
        analyses[cat] = analysis_text
        analyses_full[cat] = {
            "category": cat,
            "key_findings": batch_findings,
            "causal_chains": batch_chains,
            "implications": batch_implications,
        }

        psub("category_analyzer", f"{cat}: {len(entries)}건 → 분석 완료")

    plog("category_analyzer", f"전체 카테고리 분석 완료: {len(analyses)}개")

    save_json("step2_category_analyses.json", analyses_full)
    return {"category_analyses": analyses}


def _merge_category_analysis(
    category: str,
    findings: List[str],
    chains: List[str],
    implications: List[str],
) -> str:
    """멀티배치 CategoryAnalysis 결과를 단일 텍스트로 병합."""
    parts = [f"### {category} 분석\n"]

    if findings:
        parts.append("**핵심 발견사항:**")
        for f in findings:
            parts.append(f"- {f}")
        parts.append("")

    if chains:
        parts.append("**인과 사슬:**")
        for c in chains:
            parts.append(c)
        parts.append("")

    if implications:
        parts.append("**비즈니스 시사점:**")
        for imp in implications:
            parts.append(imp)

    return "\n".join(parts)


# ── 2-2  narrative_planner ───────────────────────────────────

_NARRATIVE_SYSTEM = """\
당신은 수석 비즈니스 분석가입니다.
4개 카테고리 분석 결과와 시간순 데이터를 종합하여 경영진용 비즈니스 백서를 설계하십시오.

다음 4가지를 모두 산출하십시오:
1. document_title: 백서 표지 제목 (프로젝트 핵심 주제를 담은 전문적인 한 줄 제목)
2. storyline: 프로젝트를 관통하는 서사 흐름 — 초기 문제 인식 → 핵심 의사결정 → 창출 가치 → 교훈
3. section_plan: 본문 섹션 기획 (2~4개). 각 섹션에 category_refs와 intent 명시
4. key_implications: 문서 마지막 '시사점' 섹션에 들어갈 핵심 제언 3~5개 (향후 방향·권고 중심)

단순한 시간적 나열을 배제하고, 비즈니스 임팩트와 인사이트 중심으로 구성하십시오.
본문은 1~2페이지 분량의 고압축 백서입니다. 섹션 수는 2~4개로 제한하십시오.
"""


@retry_on_504
def narrative_planner_node(state: GraphState) -> dict:
    """교차 카테고리 스토리라인 + 섹션 기획 설계 (LLM).

    모든 카테고리 분석과 시간순 인덱스를 종합하여
    NarrativeFlow(storyline + section_plan)를 생성.
    """
    category_analyses = state.get("category_analyses", {})
    temporal_index = state.get("temporal_index", [])
    narrative_feedback = state.get("narrative_feedback", "")

    sys_prompt = _NARRATIVE_SYSTEM + get_analysis_context() + get_proper_noun_guard()

    # Build analysis summary block
    analyses_block = []
    for cat in CATEGORIES:
        text = category_analyses.get(cat, f"('{cat}' 분석 미완료)")
        analyses_block.append(f"## {cat}\n{text}")
    analyses_text = "\n\n".join(analyses_block)

    # Build temporal summary (top events only to save budget)
    dated_items = [e for e in temporal_index if e.get("period", "undated") != "undated"]
    temporal_summary = format_entries_for_prompt(dated_items[:30])  # top 30
    if len(dated_items) > 30:
        temporal_summary += f"\n... (외 {len(dated_items) - 30}건)"

    user_content = (
        "## 카테고리별 분석 결과\n\n"
        f"{analyses_text}\n\n"
        "## 시간순 주요 이벤트\n\n"
        f"{temporal_summary}"
    )

    # If retrying with feedback, prepend it
    if narrative_feedback:
        user_content = (
            "## ⚠️ 이전 피드백 (반드시 반영하십시오)\n\n"
            f"{narrative_feedback}\n\n"
            "---\n\n"
            + user_content
        )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_content},
    ]

    result: NarrativeFlow = structured_call(
        messages,
        response_model=NarrativeFlow,
        role="analyzer",
        temperature=0.2,
    )

    # Convert section_plan to list of dicts for state
    section_plan_dicts = [sp.model_dump() for sp in result.section_plan]

    # 제목: 사용자 지정(prompt_config) 우선, 없으면 LLM 생성본
    title = get_document_title() or (result.document_title or "").strip()

    plog(
        "narrative_planner",
        f"서사 흐름 설계 완료: {len(result.section_plan)}개 섹션, "
        f"시사점 {len(result.key_implications)}건 | 제목='{title}'",
    )

    save_text("step2_narrative_flow.md", result.storyline)
    save_json(
        "step2_narrative_flow.json",
        {
            "document_title": title,
            "storyline": result.storyline,
            "section_plan": section_plan_dicts,
            "key_implications": result.key_implications,
        },
    )

    return {
        "document_title": title,
        "narrative_flow": result.storyline,
        "executive_sections": section_plan_dicts,
        "key_implications": result.key_implications,
    }


# ── 2-3  narrative_critique ──────────────────────────────────

_CRITIQUE_SYSTEM = """\
당신은 전략 문서 검토 전문가입니다.
아래 서사 흐름과 섹션 기획의 논리적 일관성과 완결성을 평가하십시오.

평가 기준:
1. 인과 사슬이 명확한가 (문제→결정→가치)
2. 섹션 간 중복이나 논리적 비약이 없는가
3. 핵심 카테고리 데이터가 누락 없이 반영되는가

승인하거나, 구체적 개선 피드백을 제시하십시오.
"""


@retry_on_504
def narrative_critique_node(state: GraphState) -> dict:
    """서사 흐름 검증 (Python validation + LLM evaluation).

    1단계: Python — section_plan의 category_refs가 유효한 카테고리인지 확인.
    2단계: LLM — 논리적 일관성, 완결성 평가.
    """
    storyline = state.get("narrative_flow", "")
    sections = state.get("executive_sections", [])
    retry_count = state.get("narrative_retry_count", 0)

    # ── Phase A: Python validation ──
    invalid_refs = []
    for i, sec in enumerate(sections):
        for ref in sec.get("category_refs", []):
            if ref not in CATEGORIES:
                invalid_refs.append(f"섹션 {i}('{sec.get('title', '')}') → '{ref}'")

    if invalid_refs:
        feedback = (
            "Python 검증 실패 — 유효하지 않은 category_refs:\n"
            + "\n".join(f"- {r}" for r in invalid_refs)
            + f"\n\n유효한 카테고리: {', '.join(CATEGORIES)}"
        )
        plog("narrative_critique", f"Python 검증 실패: {len(invalid_refs)}건 잘못된 ref")
        return {
            "is_narrative_approved": False,
            "narrative_feedback": feedback,
            "narrative_retry_count": retry_count + 1,
        }

    # ── Phase B: LLM evaluation ──
    sys_prompt = _CRITIQUE_SYSTEM + get_proper_noun_guard()

    sections_text = []
    for i, sec in enumerate(sections):
        sections_text.append(
            f"{i + 1}. **{sec.get('title', '')}**\n"
            f"   - category_refs: {sec.get('category_refs', [])}\n"
            f"   - intent: {sec.get('intent', '')}"
        )

    user_content = (
        "## 서사 흐름\n\n"
        f"{storyline}\n\n"
        "## 섹션 기획\n\n"
        + "\n".join(sections_text)
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        critique: NarrativeCritique = structured_call(
            messages,
            response_model=NarrativeCritique,
            role="judge",
            temperature=0.0,
        )

        approved = critique.is_approved
        feedback = critique.feedback
    except Exception as e:
        log_error("narrative_critique", e, _tb.format_exc())
        # On LLM failure, be lenient — approve to avoid infinite loop
        approved = True
        feedback = f"LLM 검증 실패 (자동 승인): {type(e).__name__}"

    status = "✅ 승인" if approved else "❌ 반려"
    plog("narrative_critique", f"{status} (retry={retry_count})")

    return {
        "is_narrative_approved": approved,
        "narrative_feedback": feedback,
        "narrative_retry_count": retry_count if approved else retry_count + 1,
    }


# ── Router: route_narrative ──────────────────────────────────

def route_narrative(state: GraphState) -> str:
    """서사 검증 결과에 따른 분기.

    - 승인 → init_writing
    - 반려 & 재시도 < 3 → narrative_planner (재설계)
    - 반려 & 재시도 >= 3 → init_writing (강제 통과)
    """
    if state.get("is_narrative_approved"):
        return "init_writing"
    if state.get("narrative_retry_count", 0) >= 3:
        plog("route_narrative", "3회 실패 → 강제 통과")
        return "init_writing"
    return "narrative_planner"


# ══════════════════════════════════════════════════════════════
#  STEP 3 — Executive Summary Writing
# ══════════════════════════════════════════════════════════════


# ── 3-0  init_writing ────────────────────────────────────────

def init_writing_node(state: GraphState) -> dict:
    """집필 루프 초기화. 섹션 인덱스 리셋."""
    sections = state.get("executive_sections", [])
    plog("init_writing", f"집필 시작: {len(sections)}개 섹션")
    return {
        "current_section_index": 0,
        "current_draft": "",
    }


# ── 3-1  section_writer ─────────────────────────────────────

_WRITER_SYSTEM_TEMPLATE = """\
당신은 수석 비즈니스 라이터입니다.
아래 데이터를 바탕으로 Executive Summary의 한 섹션을 집필하십시오.

섹션: {title}
핵심 메시지: {intent}

[작성 지침]
- 비즈니스 임팩트와 인사이트 중심의 전문적인 톤앤매너를 유지
- 단순한 이벤트 나열이 아닌, 인과관계와 시사점이 드러나도록 서술
- 마크다운 포맷 사용
- 2-3 문단, 고압축
"""


@retry_on_504
def section_writer_node(state: GraphState) -> dict:
    """Executive Summary 섹션 1개 집필 (LLM).

    현재 섹션 기획(title, intent, category_refs)에 따라
    knowledge_base에서 관련 데이터를 추출하여 집필.
    """
    idx = state.get("current_section_index", 0)
    sections = state.get("executive_sections", [])
    kb = state.get("knowledge_base", {})

    if idx >= len(sections):
        plog("section_writer", f"인덱스 {idx} 초과 — 빈 초안 반환")
        return {"current_draft": ""}

    section = sections[idx]
    title = section.get("title", f"섹션 {idx}")
    intent = section.get("intent", "")
    category_refs = section.get("category_refs", [])

    # Gather relevant entries from referenced categories
    relevant_entries: List[Dict] = []
    for ref in category_refs:
        cat = normalize_category(ref)
        relevant_entries.extend(kb.get(cat, []))

    sys_prompt = (
        _WRITER_SYSTEM_TEMPLATE.format(title=title, intent=intent)
        + get_writing_context()
        + get_proper_noun_guard()
    )

    # Build data block
    entries_text = format_entries_for_prompt(relevant_entries) if relevant_entries else "(참조 데이터 없음)"

    # Budget-aware truncation
    budget = available_data_budget(
        sys_prompt,
        SectionDraft.model_json_schema(),
        budget_override=effective_budget(),
    )

    entries_bytes = measure_text_bytes(entries_text)
    if entries_bytes > budget:
        batches = split_items_for_budget(
            relevant_entries,
            format_entries_for_prompt,
            budget,
        )
        entries_text = format_entries_for_prompt(batches[0]) if batches else entries_text
        psub("section_writer", f"데이터 절삭: {entries_bytes}B → ~{measure_text_bytes(entries_text)}B")

    user_content = (
        f"## 참조 데이터 (카테고리: {', '.join(category_refs)})\n\n"
        f"{entries_text}"
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_content},
    ]

    result: SectionDraft = structured_call(
        messages,
        response_model=SectionDraft,
        role="writer",
        temperature=0.3,
    )

    plog(
        "section_writer",
        f"섹션 [{idx}] '{title}' 집필 완료 "
        f"({measure_text_bytes(result.content)}B)",
    )

    return {"current_draft": result.content}


# ── 3-2  save_section ───────────────────────────────────────

def save_section_node(state: GraphState) -> dict:
    """집필된 섹션 저장 및 인덱스 전진."""
    idx = state.get("current_section_index", 0)
    draft = state.get("current_draft", "")
    sections = state.get("executive_sections", [])
    title = sections[idx].get("title", f"섹션 {idx}") if idx < len(sections) else f"섹션 {idx}"

    save_text(f"step3_sections/section_{idx}.md", draft)
    plog("save_section", f"섹션 [{idx}] '{title}' 저장 완료 ✅")

    return {
        "completed_sections": {idx: draft},
        "current_section_index": idx + 1,
        "current_draft": "",
    }


# ── Router: route_next_section ───────────────────────────────

def route_next_section(state: GraphState) -> str:
    """다음 섹션 여부에 따른 분기.

    - 남은 섹션 있음 → section_writer
    - 모든 섹션 완료 → compiler (Step 4 진입)
    """
    idx = state.get("current_section_index", 0)
    total = len(state.get("executive_sections", []))
    if idx < total:
        return "section_writer"
    return "compiler"


# ══════════════════════════════════════════════════════════════
#  STEP 4 — Whitepaper Assembly (제목 + 본문 + 시사점)
# ══════════════════════════════════════════════════════════════
#
# v3.1: 월별 상세 타임라인 부록 제거. timeline_formatter 노드 삭제.
#       compiler가 제목(H1) + 본문 섹션 + 시사점을 Pure Python으로 조립한다.


# ── 4-1  compiler ────────────────────────────────────────────

def compiler_node(state: GraphState) -> dict:
    """제목 + 본문(섹션) + 시사점 조립 (Pure Python, no LLM).

    완성된 비즈니스 백서 형태로 조립. 월별 타임라인 부록 없음.
    감사 로그(카테고리 경고)는 콘솔/로그로만 보고하고 최종 산출물에는 포함하지 않는다.
    """
    document_title = state.get("document_title", "")
    key_implications = state.get("key_implications", [])
    completed = state.get("completed_sections", {})
    sections = state.get("executive_sections", [])
    kb = state.get("knowledge_base", {})

    # 본문(섹션) 조립
    exec_summary = compile_executive_summary(sections, completed)
    save_text("step3_executive_summary.md", exec_summary)
    psub("compiler", f"본문 조립 완료 ({len(completed)}개 섹션)")

    # 카테고리 균형 경고 — 로그로만 보고 (최종 문서 제외)
    for w in check_category_balance(kb):
        psub("compiler", f"⚠️ {w}")

    compiled = compile_whitepaper(
        document_title,
        exec_summary,
        key_implications,
    )

    plog(
        "compiler",
        f"백서 조립 완료: 제목='{document_title or '(자동)'}', "
        f"시사점 {len(key_implications)}건, {measure_text_bytes(compiled)}B",
    )

    save_text("step4_compiled.md", compiled)
    return {
        "executive_summary": exec_summary,
        "final_compiled": compiled,
    }


# ── 4-3  polish ──────────────────────────────────────────────

_POLISH_SYSTEM = """\
당신은 전문 편집자입니다.
아래 텍스트의 문체, 연결어, 일관성을 개선하십시오.

[절대 금지]
- 사실 변경이나 추가
- 섹션 구조 변경
- 고유명사 번역이나 변형

원문의 의미와 사실을 100% 보존하면서 문장 흐름만 다듬으십시오.
"""


@retry_on_504
def polish_node(state: GraphState) -> dict:
    """최종 문서 윤문 (섹션별 분할 처리, LLM).

    504 방지를 위해 섹션별로 분할 윤문 후 재조합.
    사실 변경/구조 변경/고유명사 변형 금지.
    """
    compiled = state.get("final_compiled", "")

    if not compiled.strip():
        plog("polish", "final_compiled 비어있음 — 스킵")
        return {"final_output": compiled}

    sys_prompt = _POLISH_SYSTEM + get_proper_noun_guard()

    # Split into header + sections for safe per-section polish
    header, sections = split_by_section(compiled)

    if not sections:
        # No ## headings found — polish as a single block
        polished = _polish_single_block(sys_prompt, compiled)
        save_text("step4_final.md", polished)
        plog("polish", f"윤문 완료 (단일 블록): {measure_text_bytes(polished)}B")
        return {"final_output": polished}

    polished_parts: List[str] = [header]

    for si, section_text in enumerate(sections):
        section_bytes = measure_text_bytes(section_text)

        # Skip very short sections (likely just a heading)
        if section_bytes < 50:
            polished_parts.append(section_text)
            continue

        polished_section = _polish_single_block(sys_prompt, section_text)
        polished_parts.append(polished_section)
        psub("polish", f"섹션 {si + 1}/{len(sections)} 윤문 완료")

    polished = "".join(polished_parts)

    plog(
        "polish",
        f"윤문 완료: {len(sections)}개 섹션, "
        f"{measure_text_bytes(compiled)}B → {measure_text_bytes(polished)}B",
    )

    save_text("step4_final.md", polished)
    return {"final_output": polished}


def _polish_single_block(sys_prompt: str, text: str) -> str:
    """단일 텍스트 블록 윤문 (내부 헬퍼)."""
    # Check budget — if text is too large, truncate
    budget = available_data_budget(
        sys_prompt,
        PolishedDocument.model_json_schema(),
        budget_override=effective_budget(),
    )
    text_bytes = measure_text_bytes(text)

    if text_bytes > budget:
        # Polish only what fits; append remainder unchanged
        cutoff = int(budget * 0.85)
        head = text[:cutoff]
        tail = text[cutoff:]
        psub("polish", f"블록 절삭: {text_bytes}B → head {cutoff}B + tail")
    else:
        head = text
        tail = ""

    messages = [
        {"role": "system", "content": sys_prompt},
        {
            "role": "user",
            "content": (
                "아래 텍스트를 윤문하십시오. 의미와 사실을 100% 보존하십시오.\n\n"
                f"{head}"
            ),
        },
    ]

    try:
        result: PolishedDocument = structured_call(
            messages,
            response_model=PolishedDocument,
            role="writer",
            temperature=0.1,
            stream=True,
        )
        return result.content + tail
    except Exception as e:
        log_error("polish", e, _tb.format_exc())
        psub("polish", f"윤문 실패 — 원문 유지: {type(e).__name__}")
        return text


# ══════════════════════════════════════════════════════════════
#  Exported node/router registry
# ══════════════════════════════════════════════════════════════

__all__ = [
    # Global config
    "LOCAL_DATA_PATH",
    # Decorator
    "retry_on_504",
    # Step 1: Knowledge Structuring
    "load_docs_node",
    "knowledge_extractor_node",
    "knowledge_aggregator_node",
    "temporal_indexer_node",
    # Step 2: Narrative Flow
    "category_analyzer_node",
    "narrative_planner_node",
    "narrative_critique_node",
    "route_narrative",
    # Step 3: Executive Summary Writing
    "init_writing_node",
    "section_writer_node",
    "save_section_node",
    "route_next_section",
    # Step 4: Whitepaper Assembly
    "compiler_node",
    "polish_node",
]
