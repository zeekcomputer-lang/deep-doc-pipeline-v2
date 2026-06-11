"""
프롬프트 커스텀 설정 — v3.1 (KR-first, 완성 백서 + 사전 지식 주입).
═══════════════════════════════════════════════

이 파일의 값을 수정하여 백서의 제목, 톤, 목적, 사전 지식, 편향을 조정할 수 있습니다.
수정 후 파이프라인을 다시 실행하면 즉시 반영됩니다.
코드 수정 없이 이 파일만 편집하면 됩니다.

기본값: "프로젝트 수행 결과 백서" (중립 객관 톤)

v3 변경: KR-first — 모든 Step에서 한국어로 직접 출력. 번역 단계 제거.
v3.1 변경:
  - 월별 상세 타임라인 부록 제거 (제목 + 본문 + 시사점 형태의 완성 백서)
  - 사전 지식 주입(DOMAIN_KNOWLEDGE / KEY_TERMS) — LLM이 모르는 도메인 지식·용어를
    프롬프트로 사전 주입하여 어텐션을 집중시키고 환각을 줄임
  - DOCUMENT_TITLE — 백서 표지 제목 직접 지정 가능

적용 범위:
  - Step 1 추출 (knowledge_extractor): PURPOSE + 사전지식
  - Step 2 분석 (category_analyzer, narrative_planner): PURPOSE + TONE + 사전지식
  - Step 3 집필 (section_writer): PURPOSE + TONE + AUDIENCE + CUSTOM + 사전지식
  - Step 4 조립: (LLM 미사용 — Pure Python)
  - 윤문 (polish): 적용하지 않음 (사실 변경 금지 원칙)
"""
from __future__ import annotations


# ════════════════════════════════════════════════════════════════
# 0. 문서 제목 (Document Title) — 백서 표지에 표시
# ════════════════════════════════════════════════════════════════
# 비워두면("") narrative_planner가 내용 기반으로 제목을 자동 생성합니다.
# 직접 지정하면 해당 제목을 표지에 그대로 사용합니다.
DOCUMENT_TITLE: str = ""


# ════════════════════════════════════════════════════════════════
# 1. 문서 목적 (Document Purpose)
# ════════════════════════════════════════════════════════════════
DOCUMENT_PURPOSE: str = "프로젝트 수행 경과와 성과를 경영진에게 보고하는 비즈니스 백서"


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
# 비워두면("") 일반 경영진 독자 대상으로 작성됩니다.
TARGET_AUDIENCE: str = "경영진 및 의사결정권자"


# ════════════════════════════════════════════════════════════════
# 4. 추가 사용자 지시 (Custom Directives)
# ════════════════════════════════════════════════════════════════
# ⚠️ Step 3 집필(section_writer) 단계에만 주입됩니다.
CUSTOM_DIRECTIVES: str = ""


# ════════════════════════════════════════════════════════════════
# 4-B. 시점(날짜) 참조 정보 제공 (Temporal Reference)
# ════════════════════════════════════════════════════════════════
# True이면, 데이터의 날짜 단서(date_hint)를 참고 정보로 제공하고,
# 서술의 자연스러움을 돕는 경우에만 시점을 포함하도록 LLM에 안내합니다.
# 시점 포함은 강제가 아니며(선택 사항), 인사이트 전달이 우선입니다.
# 날짜 단서가 없는 사안은 시점을 지어내지 않습니다(환각 방지).
#
# - True  : 날짜를 참고 정보로 제공, 필요한 경우에만 본문에 자연스럽게 반영 (기본)
# - False : 시점 안내를 주입하지 않음 (인사이트 중심 서술)
# 월별 상세 타임라인 '부록'과는 별개입니다. 부록은 v3.1에서 제거되었고,
# 이 옵션은 날짜를 참고 정보로 제공하는 기능입니다(강제 아님).
INCLUDE_TEMPORAL_CONTEXT: bool = True


# ════════════════════════════════════════════════════════════════
# 5. ★ 사전 지식 주입 (Domain Knowledge Injection)
# ════════════════════════════════════════════════════════════════
# LLM이 알지 못하는 도메인 지식·배경·고려사항을 사전 주입합니다.
# 전 LLM 노드(추출·분석·집필)에 자동 주입되어 어텐션을 집중시키고
# 잘못된 추론(환각)을 줄입니다.
#
# 작성 팁:
#   - 회사/조직 고유의 약어, 프로세스, 제품 라인업
#   - 프로젝트가 따르는 방법론·단계 정의
#   - 보고서에서 반드시 강조해야 할 관점
#   - 일반 LLM이 오해하기 쉬운 사내 용어
#
# 비워두면("") 주입하지 않습니다.
DOMAIN_KNOWLEDGE: str = ""

# 예시 (그대로 두면 미사용 — 위 DOMAIN_KNOWLEDGE를 채우세요):
_DOMAIN_KNOWLEDGE_EXAMPLE: str = """\
- 본 프로젝트의 개발 단계: 기획(PoC) → 설계(Design) → 구현(Build) → 안정화(Hardening) → 운영전환(Go-Live) 5단계를 따른다.
- '운영전환(Go-Live)'은 단순 배포가 아니라 무중단 전환과 롤백 계획 수립을 포함하는 단계다.
- 'P99 응답시간'은 상위 1% 느린 요청의 응답 시간을 의미하며, 사용자 체감 품질의 핵심 지표다.
- 장애 등급(impact_level)에서 'critical'은 매출 직접 영향, 'high'는 핵심 기능 저하를 의미한다.
"""


# ════════════════════════════════════════════════════════════════
# 6. ★ 핵심 용어집 (Key Terminology Glossary)
# ════════════════════════════════════════════════════════════════
# {용어: 정의} 형태. LLM이 용어를 정확히 이해하고 일관되게 사용하도록 합니다.
# 비워두면({}) 주입하지 않습니다.
KEY_TERMS: dict[str, str] = {}

# 예시 (그대로 두면 미사용 — 위 KEY_TERMS를 채우세요):
_KEY_TERMS_EXAMPLE: dict[str, str] = {
    "Go-Live": "신규 시스템을 실제 운영 환경으로 무중단 전환하는 단계",
    "DLQ": "Dead Letter Queue — 처리 실패 메시지를 격리 보관하는 큐",
    "ISMS": "정보보호 관리체계 — 국내 정보보안 인증 제도",
}


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

def _build_domain_block() -> str:
    """사전 지식 + 용어집 블록 생성 (전 LLM 노드 공통 주입)."""
    parts: list[str] = []

    if DOMAIN_KNOWLEDGE and DOMAIN_KNOWLEDGE.strip():
        parts.append(
            "[사전 주입 지식 — 반드시 숙지하고 반영하십시오]\n"
            + DOMAIN_KNOWLEDGE.strip()
        )

    if KEY_TERMS:
        term_lines = [f"- {k}: {v}" for k, v in KEY_TERMS.items()]
        parts.append(
            "[핵심 용어집 — 아래 정의에 따라 일관되게 사용하십시오]\n"
            + "\n".join(term_lines)
        )

    if not parts:
        return ""

    return "\n\n" + "\n\n".join(parts) + "\n"


def get_domain_knowledge() -> str:
    """사전 지식 + 용어집 블록 (독립 조회용)."""
    return _build_domain_block()


# 시점 참고 정보 텍스트 (강제 아님 — 단순 참조용)
_TEMPORAL_DIRECTIVE: str = (
    "[시점 참고 정보]\n"
    "각 사안의 앞에 표시된 날짜([YYYY-MM-DD] 또는 [YYYY-MM])는 실제 발생 시점으로, 참고용 정보입니다.\n"
    "서술의 자연스러움이나 시간적 선후 관계를 드러내는 데 도움이 되는 경우에만 "
    "본문에 시점(예: '2026년 2월', '도입 초기')을 자연스럽게 포함할 수 있습니다.\n"
    "시점 포함은 필수가 아니며, 비즈니스 인사이트 전달이 우선입니다. 불필요하면 생략해도 됩니다.\n"
    "단, 날짜 단서가 없는 사안은 시점을 임의로 지어내지 마십시오.\n"
)


def _build_temporal_block() -> str:
    """시점 참고 정보 블록 (INCLUDE_TEMPORAL_CONTEXT=True일 때만). 강제 아님."""
    return ("\n\n" + _TEMPORAL_DIRECTIVE) if INCLUDE_TEMPORAL_CONTEXT else ""


def get_temporal_directive() -> str:
    """시점 참고 정보 텍스트 (독립 조회용)."""
    return _build_temporal_block()


def _build_context_block(
    include_purpose: bool = True,
    include_tone: bool = True,
    include_audience: bool = True,
    include_custom: bool = False,
    include_domain: bool = True,
    include_temporal: bool = False,
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

    domain_block = _build_domain_block() if include_domain else ""
    temporal_block = _build_temporal_block() if include_temporal else ""

    if not parts and not domain_block and not temporal_block:
        return ""

    head = ""
    if parts:
        head = "\n\n[사용자 컨텍스트]\n" + "\n".join(parts) + "\n"

    return head + domain_block + temporal_block + "\n" + _KR_PROPER_NOUN_PRESERVE


# ── Step별 컨텍스트 조회 함수 ──────────────────────────────────

def get_extraction_context() -> str:
    """Step 1 추출용 (knowledge_extractor). PURPOSE + 사전지식."""
    return _build_context_block(
        include_purpose=True, include_tone=False,
        include_audience=False, include_custom=False,
        include_domain=True,
    )


def get_analysis_context() -> str:
    """Step 2 분석용 (category_analyzer, narrative_planner). PURPOSE + TONE + 사전지식 + 시점 참고."""
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=False, include_custom=False,
        include_domain=True, include_temporal=True,
    )


def get_writing_context() -> str:
    """Step 3 집필용 (section_writer). 전체 항목 + 사전지식 + 시점 참고."""
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=True, include_custom=True,
        include_domain=True, include_temporal=True,
    )


def get_assembly_context() -> str:
    """Step 4 조립용. PURPOSE + AUDIENCE (현재 Pure Python 조립이라 미사용, 호환 유지)."""
    return _build_context_block(
        include_purpose=True, include_tone=False,
        include_audience=True, include_custom=False,
        include_domain=False,
    )


def get_document_title() -> str:
    """사용자 지정 문서 제목 (비어있으면 자동 생성)."""
    return DOCUMENT_TITLE.strip()


def get_proper_noun_guard() -> str:
    """고유명사 보존 가드 텍스트 (polish 등 단독 사용 시)."""
    return _KR_PROPER_NOUN_PRESERVE
