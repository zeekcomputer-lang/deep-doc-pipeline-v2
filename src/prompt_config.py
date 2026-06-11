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
# 0-B. ★ 문서 유형 (Document Type) — 산출물 형태 선택
# ════════════════════════════════════════════════════════════════
# 지식 베이스(4개 카테고리로 분류된 지식)를 베이스로, 어떤 형태의 문서를
# 생성할지 선택합니다. 카테고리는 '지식 분류 축'일 뿐, 문서의 섹션 구조와
# 1:1로 대응하지 않습니다. 각 유형은 목적에 맞는 고유한 섹션 구성을 갖습니다.
#
# 지원 유형 (키 지정):
#   "whitepaper"      : 경영진 비즈니스 백서 (기본). 문제→결정→가치→교훈 서사
#   "executive_brief" : 1페이지 임원 요약보고서 (핵심만 압축)
#   "postmortem"      : 장애/회고 보고서 (타임라인·원인·조치·재발방지)
#   "tech_report"     : 기술 현황 보고서 (아키텍처·의사결정·리스크 중심)
#   "status_update"   : 이해관계자 진행 업데이트 (성과·진행·이슈·다음 단계)
#   "custom"          : DOCUMENT_TYPE_CUSTOM_STRUCTURE 에 직접 구조 지정
DOCUMENT_TYPE: str = "whitepaper"

# DOCUMENT_TYPE="custom" 일 때만 사용. 원하는 섹션 구성·흐름을 자유롭게 서술.
DOCUMENT_TYPE_CUSTOM_STRUCTURE: str = ""


# ════════════════════════════════════════════════════════════════
# 1. 문서 목적 (Document Purpose)
# ════════════════════════════════════════════════════════════════
# 비워두면("") DOCUMENT_TYPE에 따른 기본 목적이 사용됩니다.
DOCUMENT_PURPOSE: str = ""


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


# ──────────────────────────────────────────────────────────────
# 문서 유형별 구조 지침 (Document Type Strategy)
# ──────────────────────────────────────────────────────────────
# 각 문서 유형의 (기본 목적, 구조 지침). 구조 지침은 narrative_planner에 주입되어
# 카테고리 경계가 아닌 '문서 목적'에 맞는 섹션 구성을 설계하도록 유도한다.
_DOCUMENT_TYPE_PROFILES: dict[str, tuple[str, str]] = {
    "whitepaper": (
        "프로젝트 수행 경과와 성과를 경영진에게 보고하는 비즈니스 백서",
        "문제 인식 → 핵심 의사결정 → 창출 가치 → 교훈의 서사 흐름을 권장. "
        "서사 단계별로 섹션을 구성하되, 여러 카테고리의 지식을 가로지르며 종합하십시오. "
        "1~2페이지 고압축을 지향하되, 섹션 수는 내용의 풍부함에 맞게 능동적으로 결정하십시오(참고: 보통 3~5개).",
    ),
    "executive_brief": (
        "핵심 의사결정과 성과만 압축한 1페이지 임원 요약보고서",
        "한눈에 핵심을 파악하는 극도로 압축된 구조. "
        "'핵심 요약', '주요 성과/결정', '리스크와 대응' 수준으로 최소화(참고: 보통 1~3개). "
        "각 섹션은 카테고리를 가로지르는 종합 관점으로 서술. 압축이 최우선이므로 섹션을 늘리지 마십시오.",
    ),
    "postmortem": (
        "장애·이슈 대응 경과를 돌아보는 회고(포스트모텀) 보고서",
        "'상황 요약', '근본 원인', '대응과 조치', '재발 방지 대책' 성격의 섹션을 권장(참고: 보통 3~5개). "
        "원인·대응은 여러 카테고리(리스크·아키텍처·교훈)의 지식을 종합해 인과적으로 서술. 섹션 수는 사건 복잡도에 맞게 조절.",
    ),
    "tech_report": (
        "아키텍처·기술 의사결정과 리스크를 정리한 기술 현황 보고서",
        "'기술 개요', '주요 아키텍처 결정', '기술적 리스크와 대응', '기술 부채·교훈' 성격을 권장. "
        "기술 주제별로 섹션을 나누되, 관련 카테고리 지식을 주제 중심으로 재구성. 기술 주제가 많으면 섹션을 늘려도 됩니다.",
    ),
    "status_update": (
        "이해관계자에게 진행 상황을 공유하는 진행 업데이트",
        "'주요 진척·성과', '현재 이슈·리스크', '다음 단계 계획' 성격을 권장(참고: 보통 3개 전후). "
        "시점별 진행을 여러 카테고리를 가로지르며 종합적으로 정리.",
    ),
}


def get_document_type() -> str:
    """현재 문서 유형 키 (소문자 정규화)."""
    return (DOCUMENT_TYPE or "whitepaper").strip().lower()


def get_effective_purpose() -> str:
    """문서 목적 — DOCUMENT_PURPOSE 명시값 우선, 없으면 유형별 기본값."""
    if DOCUMENT_PURPOSE and DOCUMENT_PURPOSE.strip():
        return DOCUMENT_PURPOSE.strip()
    dtype = get_document_type()
    profile = _DOCUMENT_TYPE_PROFILES.get(dtype)
    return profile[0] if profile else "프로젝트 지식 기반 보고서"


def get_document_structure_directive() -> str:
    """narrative_planner에 주입할 문서 유형별 구조 지침.

    핵심: 카테고리는 지식 분류 축일 뿐, 문서 섹션과 1:1로 대응하지 않는다는 점을 명시.
    """
    dtype = get_document_type()

    if dtype == "custom" and DOCUMENT_TYPE_CUSTOM_STRUCTURE.strip():
        structure = DOCUMENT_TYPE_CUSTOM_STRUCTURE.strip()
        type_label = "사용자 정의 문서"
    else:
        profile = _DOCUMENT_TYPE_PROFILES.get(dtype, _DOCUMENT_TYPE_PROFILES["whitepaper"])
        structure = profile[1]
        type_label = dtype

    return (
        "\n\n[문서 유형과 구조 설계]\n"
        f"문서 유형: {type_label}\n"
        f"구조 지침: {structure}\n"
        "\n[★ 카테고리와 섹션의 관계 — 반드시 준수]\n"
        "입력으로 주어진 4개 카테고리(Architecture_and_Tech, Risk_and_Troubleshooting, "
        "Business_and_Feature, Lessons_Learned)는 지식을 정리한 '분류 축'일 뿐입니다.\n"
        "— 문서의 섹션을 카테고리와 1:1로 만들지 마십시오. (예: '아키텍처', '리스크' 식으로 카테고리명을 그대로 섹션 제목으로 쓰지 않음)\n"
        "— 각 섹션은 문서 목적에 맞는 '주제'로 정의하고, 필요한 카테고리의 지식을 여러 개 가로지르며(cross-cutting) 끌어와 서술하십시오.\n"
        "— 하나의 섹션이 여러 카테고리를 참조할 수 있고, 하나의 카테고리가 여러 섹션에 기여할 수도 있습니다.\n"
        "— 섹션의 category_refs에는 그 섹션 서술에 실제로 필요한 카테고리를 (복수 가능) 명시하십시오.\n"
        "\n[★ 문단 구성은 능동적으로 — LLM이 설계]\n"
        "— 섹션 수는 고정되어 있지 않습니다. 내용의 풍부함과 문서 유형에 맞게 적절한 수를 스스로 결정하십시오. "
        "다룰 내용이 풍부하면 섹션을 더 나누고, 단순하면 줄이십시오.\n"
        "— 한 섹션의 내용이 많아 하나의 문단으로 담기 어려우면, 해당 섹션의 subsections에 소제목을 나열해 세분할 수 있습니다. "
        "단순한 섹션은 subsections를 빈 목록([])으로 두십시오. 소제목 사용 여부와 개수도 LLM이 판단합니다.\n"
        "— 권장 섹션 수는 참고일 뿐이며, 문서를 가장 명확하게 전달할 수 있는 구조를 능동적으로 설계하는 것이 최우선입니다.\n"
    )


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

    if include_purpose:
        eff_purpose = get_effective_purpose()
        if eff_purpose:
            parts.append(f"[문서 목적] {eff_purpose}")

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
