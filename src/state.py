"""
LangGraph global state — v3.0 (KR-first, category-based).
"""
from __future__ import annotations
import operator
from typing import TypedDict, List, Dict, Annotated, Any


def update_dict(a: Dict, b: Dict) -> Dict:
    """Dict reducer — merge keys from b into a."""
    return {**a, **b}


class GraphState(TypedDict, total=False):
    # ── Step 1: Knowledge Structuring ──────────────────────────
    raw_docs: List[Dict[str, Any]]                              # 로드된 원시 문서
    knowledge_entries: Annotated[List[Dict], operator.add]       # LLM 추출된 개별 엔트리
    knowledge_base: Dict[str, List[Dict]]                        # category → entries (집계 완료)
    temporal_index: List[Dict]                                   # 날짜순 정렬 (best-effort)

    # ── Step 2: Narrative Flow ─────────────────────────────────
    category_analyses: Annotated[Dict[str, str], update_dict]    # 카테고리별 심층 분석
    narrative_flow: str                                          # 교차 카테고리 스토리라인
    narrative_feedback: str                                      # 비평 피드백
    is_narrative_approved: bool                                  # 서사 승인 여부
    narrative_retry_count: int                                   # 서사 재시도 횟수

    # ── Step 3: Executive Summary Writing ──────────────────────
    executive_sections: List[Dict]                               # 계획된 섹션 목록
    current_section_index: int                                   # 현재 집필 커서
    current_draft: str                                           # 현재 초안
    previous_draft: str                                          # 직전 반려 초안 (회귀 방지)
    hallucinated_tokens: Annotated[List[str], operator.add]      # 환각 토큰 블랙리스트
    draft_feedback: str                                          # 팩트체크 피드백
    is_draft_approved: bool                                      # 초안 승인 여부
    section_retry_count: int                                     # 섹션 재시도 횟수
    completed_sections: Annotated[Dict[int, str], update_dict]   # idx → 완성 텍스트
    unverified_sections: Annotated[List[int], operator.add]      # Fail-Safe 감사 로그

    # ── Step 4: Hybrid Assembly ────────────────────────────────
    executive_summary: str                                       # 조립된 Executive Summary
    chronological_appendix: str                                  # 시계열 부록
    final_compiled: str                                          # 최종 조합 문서
    final_output: str                                            # 윤문 완료 최종본
