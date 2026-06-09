"""
Pydantic schema definitions — v3.0 (KR-first, category-based).

All LLM responses are validated through these schemas via extract_json + model_validate.
모든 스키마의 텍스트 필드는 한국어로 작성되며, 고유명사(기술 용어·제품명·약어)만 원어 보존.
"""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────
# Step 1: Knowledge Structuring
# ──────────────────────────────────────────────────────────────
class KnowledgeEntry(BaseModel):
    """원시 문서 1건에서 추출된 구조화된 지식 엔트리."""
    category: str = Field(
        ...,
        description=(
            "분류 카테고리. 다음 중 하나: "
            "Architecture_and_Tech, Risk_and_Troubleshooting, "
            "Business_and_Feature, Lessons_Learned"
        ),
    )
    title: str = Field(..., description="핵심 사안 제목 (1문장, 한국어)")
    description: str = Field(..., description="상세 설명 (2-3문장, 한국어, 고유명사 원어 보존)")
    source_ref: str = Field(..., description="원본 참조 (예: records.jsonl:42)")
    date_hint: Optional[str] = Field(
        None,
        description="날짜 힌트 (YYYY-MM-DD 또는 YYYY-MM). 불명확 시 null",
    )
    impact_level: str = Field(
        ...,
        description="영향도: critical / high / medium / low",
    )


# ──────────────────────────────────────────────────────────────
# Step 2: Narrative Flow
# ──────────────────────────────────────────────────────────────
class CategoryAnalysis(BaseModel):
    """카테고리 1개에 대한 심층 분석 결과."""
    category: str = Field(..., description="분석 대상 카테고리")
    key_findings: List[str] = Field(
        ..., description="핵심 발견사항 목록 (각 1-2문장, 한국어)",
    )
    causal_chain: str = Field(
        ...,
        description="원인-결과 사슬: 문제 인식 → 대응 → 결과 (한국어)",
    )
    implications: str = Field(
        ..., description="비즈니스 시사점 (1-2문장, 한국어)",
    )


class SectionPlanItem(BaseModel):
    """Executive Summary 섹션 기획 항목."""
    title: str = Field(..., description="섹션 제목 (한국어)")
    category_refs: List[str] = Field(
        ..., description="참조할 카테고리 목록 (1개 이상)",
    )
    intent: str = Field(..., description="섹션의 핵심 메시지 (1문장, 한국어)")


class NarrativeFlow(BaseModel):
    """교차 카테고리 스토리라인 + Executive Summary 섹션 기획."""
    storyline: str = Field(
        ...,
        description=(
            "프로젝트 전체를 관통하는 서사 흐름: "
            "문제 인식 → 핵심 의사결정 → 창출 가치 → 교훈 (한국어)"
        ),
    )
    section_plan: List[SectionPlanItem] = Field(
        ..., description="Executive Summary 섹션 목록 (순서대로)",
    )


class NarrativeCritique(BaseModel):
    """서사 흐름 검증 결과."""
    is_approved: bool = Field(..., description="서사 흐름 승인 여부")
    feedback: str = Field(..., description="반려 사유 또는 승인 코멘트 (한국어)")


# ──────────────────────────────────────────────────────────────
# Step 3: Executive Summary Writing
# ──────────────────────────────────────────────────────────────
class SectionDraft(BaseModel):
    """Executive Summary 섹션 초안."""
    content: str = Field(
        ..., description="섹션 본문 (마크다운, 한국어, 고유명사 원어 보존)",
    )


# ──────────────────────────────────────────────────────────────
# Step 4: Hybrid Assembly
# ──────────────────────────────────────────────────────────────
class TimelineEntry(BaseModel):
    """시계열 부록의 기간별 항목."""
    period: str = Field(
        ..., description="기간 (YYYY-MM 또는 '날짜 미상')",
    )
    events: List[str] = Field(
        ..., description="해당 기간 주요 이벤트 (각 1문장, 한국어)",
    )
    significance: str = Field(
        ..., description="해당 기간의 핵심 의의 (1문장, 한국어)",
    )


class PolishedDocument(BaseModel):
    """윤문 완료 문서."""
    content: str = Field(
        ..., description="윤문된 최종 마크다운 본문 (사실 변경 금지)",
    )
