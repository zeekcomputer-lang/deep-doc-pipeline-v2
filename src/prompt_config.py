"""
프롬프트 커스텀 설정 — v3.0 (KR-first).
═══════════════════════════════════════════════

이 파일의 값을 수정하여 백서의 톤, 목적, 편향을 조정할 수 있습니다.
수정 후 파이프라인을 다시 실행하면 즉시 반영됩니다.
코드 수정 없이 이 파일만 편집하면 됩니다.

기본값: "카테고리 기반 하이브리드 프로젝트 백서" (중립 객관 톤)

v3 변경: KR-first — 모든 Step에서 한국어로 직접 출력.
         번역 단계 제거. get_translation_context() 삭제.

적용 범위:
  - Step 1 추출 (knowledge_extractor): PURPOSE
  - Step 2 분석 (category_analyzer, narrative_planner): PURPOSE + TONE
  - Step 3 집필 (section_writer): PURPOSE + TONE + AUDIENCE + CUSTOM
  - Step 4 조립 (timeline_formatter): PURPOSE + AUDIENCE
  - 윤문 (polish): 적용하지 않음 (사실 변경 금지 원칙)
"""
from __future__ import annotations


# ════════════════════════════════════════════════════════════════
# 1. 문서 목적 (Document Purpose)
# ════════════════════════════════════════════════════════════════
DOCUMENT_PURPOSE: str = "카테고리 기반 하이브리드 프로젝트 백서"


# ════════════════════════════════════════════════════════════════
# 2. 톤 및 편향 (Tone & Bias Directive)
# ════════════════════════════════════════════════════════════════
# 비워두면("") 중립 객관 톤으로 작성됩니다.
# ⚠️ 편향 설정은 사실을 왜곡하지 않습니다.
#    프롬프트 가드가 knowledge_base 기반 서술을 강제합니다.
TONE_DIRECTIVE: str = ""


# ════════════════════════════════════════════════════════════════
# 3. 대상 독자 (Target Audience)
# ════════════════════════════════════════════════════════════════
# 비워두면("") 일반 독자 대상으로 작성됩니다.
TARGET_AUDIENCE: str = ""


# ════════════════════════════════════════════════════════════════
# 4. 추가 사용자 지시 (Custom Directives)
# ════════════════════════════════════════════════════════════════
# ⚠️ Step 3 집필(section_writer) 단계에만 주입됩니다.
CUSTOM_DIRECTIVES: str = ""


# ════════════════════════════════════════════════════════════════
# 고유명사 원어 보존 가드 (KR-first 전용)
# ════════════════════════════════════════════════════════════════
# 전 LLM 노드 프롬프트에 자동 주입
_KR_PROPER_NOUN_PRESERVE: str = (
    "[고유명사 원어 보존 규칙]\n"
    "기술 고유명사(약어, 제품명, 프레임워크명, 기술 표준명 등)는 원어 그대로 사용하십시오.\n"
    "예: LangGraph, FastAPI, Kubernetes, SQLAlchemy, OpenAI, Pydantic, Docker\n"
    "한국어 번역이나 음차 표기를 하지 마십시오.\n"
)


# ════════════════════════════════════════════════════════════════
# 내부 헬퍼 — 아래 함수들은 수정하지 마십시오.
# ════════════════════════════════════════════════════════════════

def _build_context_block(
    include_purpose: bool = True,
    include_tone: bool = True,
    include_audience: bool = True,
    include_custom: bool = False,
) -> str:
    """노드 프롬프트에 주입할 사용자 컨텍스트 블록 생성 (한국어)."""
    parts: list[str] = []

    if include_purpose and DOCUMENT_PURPOSE:
        parts.append(f"[문서 목적] {DOCUMENT_PURPOSE}")

    if include_tone and TONE_DIRECTIVE:
        parts.append(
            f"[톤 지시] {TONE_DIRECTIVE} "
            "(강조 방향 지시일 뿐, 사실 왜곡은 금지입니다.)"
        )

    if include_audience and TARGET_AUDIENCE:
        parts.append(f"[대상 독자] {TARGET_AUDIENCE}")

    if include_custom and CUSTOM_DIRECTIVES:
        parts.append(f"[추가 지시]\n{CUSTOM_DIRECTIVES}")

    if not parts:
        return ""

    return (
        "\n\n[사용자 컨텍스트]\n"
        + "\n".join(parts)
        + "\n\n"
        + _KR_PROPER_NOUN_PRESERVE
    )


# ── Step별 컨텍스트 조회 함수 ──────────────────────────────────

def get_extraction_context() -> str:
    """Step 1 추출용 (knowledge_extractor). PURPOSE만 주입."""
    return _build_context_block(
        include_purpose=True, include_tone=False,
        include_audience=False, include_custom=False,
    )


def get_analysis_context() -> str:
    """Step 2 분석용 (category_analyzer, narrative_planner). PURPOSE + TONE."""
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=False, include_custom=False,
    )


def get_writing_context() -> str:
    """Step 3 집필용 (section_writer). 전체 항목 주입."""
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=True, include_custom=True,
    )


def get_assembly_context() -> str:
    """Step 4 조립용 (timeline_formatter). PURPOSE + AUDIENCE."""
    return _build_context_block(
        include_purpose=True, include_tone=False,
        include_audience=True, include_custom=False,
    )


def get_proper_noun_guard() -> str:
    """고유명사 보존 가드 텍스트 (polish 등 단독 사용 시)."""
    return _KR_PROPER_NOUN_PRESERVE
