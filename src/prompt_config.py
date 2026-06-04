"""
프롬프트 커스텀 설정 — 사용자 편집 전용 파일.
═══════════════════════════════════════════════

이 파일의 값을 수정하여 백서의 톤, 목적, 편향을 조정할 수 있습니다.
수정 후 파이프라인을 다시 실행하면 즉시 반영됩니다.
코드 수정 없이 이 파일만 편집하면 됩니다.

기본값: "가독성이 뛰어난 기간별 이벤트 기반 백서" (중립 객관 톤)

적용 범위:
  - 요약 단계 (period_summarizer, theme_analyzer): PURPOSE + TONE
  - 기획 단계 (draft_planner): PURPOSE + AUDIENCE
  - 집필 단계 (section_writer): PURPOSE + TONE + AUDIENCE + CUSTOM
  - 번역 단계 (translate): PURPOSE + TONE + AUDIENCE (한국어)
  - 윤문 단계 (polish): 적용하지 않음 (사실 변경 금지 원칙)
"""
from __future__ import annotations


# ════════════════════════════════════════════════════════════════
# 1. 문서 목적 (Document Purpose)
# ════════════════════════════════════════════════════════════════
# 백서의 전반적인 목적을 정의합니다.
# 이 값은 요약·기획·집필·번역 전 단계의 프롬프트에 주입됩니다.
#
# 커스텀 예시:
#   "경영진 보고용 성과 중심 요약 보고서"
#   "투자자 대상 긍정적 성장 스토리 백서"
#   "리스크 중심의 내부 감사 보고서"
#   "기술 실무자를 위한 상세 프로젝트 이력 문서"
#   "분기별 프로젝트 현황 대시보드 보고서"
#
DOCUMENT_PURPOSE: str = "가독성이 뛰어난 기간별 이벤트 기반 백서"


# ════════════════════════════════════════════════════════════════
# 2. 톤 및 편향 (Tone & Bias Directive)
# ════════════════════════════════════════════════════════════════
# 서술 톤과 사실 해석의 방향성을 지정합니다.
# 비워두면("") 중립 객관 톤으로 작성됩니다.
#
# ⚠️ 편향 설정은 사실을 왜곡하지 않습니다.
#    LLM에게 "강조 방향"을 지시할 뿐, fact_checker가
#    원본 데이터에 없는 사실 추가를 여전히 차단합니다.
#
# 커스텀 예시:
#   "긍정적 성과와 성장세를 강조하되, 사실에 기반할 것"
#   "리스크와 미해결 과제를 우선적으로 부각할 것"
#   "균형 잡힌 시각으로 성과와 과제를 동등하게 다룰 것"
#   "보수적 관점에서 확인된 사실만 서술할 것"
#   "변화와 혁신의 맥락을 부각하여 서술할 것"
#
TONE_DIRECTIVE: str = ""


# ════════════════════════════════════════════════════════════════
# 3. 대상 독자 (Target Audience)
# ════════════════════════════════════════════════════════════════
# 문서의 주요 독자층을 지정합니다.
# 비워두면("") 일반 독자 대상으로 작성됩니다.
#
# 커스텀 예시:
#   "C-레벨 경영진 — 핵심 수치와 의사결정 포인트 중심, 전문 용어 최소화"
#   "기술 실무자 — 상세 기술 내역과 구현 과정 포함"
#   "외부 투자자/파트너 — 비즈니스 임팩트와 성장 지표 중심"
#   "감사/컴플라이언스 팀 — 절차 준수 여부와 증적 중심"
#   "신규 팀원 — 프로젝트 배경과 맥락 설명 포함"
#
TARGET_AUDIENCE: str = ""


# ════════════════════════════════════════════════════════════════
# 4. 추가 사용자 지시 (Custom Directives)
# ════════════════════════════════════════════════════════════════
# 프롬프트에 추가로 삽입할 자유 형식 텍스트입니다.
# 여러 줄 가능. 비워두면("") 무시됩니다.
#
# ⚠️ 집필(section_writer) 단계에만 주입됩니다.
#    요약/기획 단계에는 의도적으로 영향을 주지 않습니다.
#    (요약은 사실 압축이므로 편향 최소화)
#
# 커스텀 예시:
#   "매 섹션 말미에 '시사점' 문단을 추가할 것"
#   "수치 데이터는 반드시 표(table) 형태로 정리할 것"
#   "각 월별 섹션에 전월 대비 변화를 명시할 것"
#   "3줄 이상의 나열은 글머리 기호로 정리할 것"
#   "기술 약어 첫 등장 시 풀네임을 병기할 것"
#
CUSTOM_DIRECTIVES: str = ""


# ════════════════════════════════════════════════════════════════
# 내부 헬퍼 — 아래 함수들은 수정하지 마십시오.
# ════════════════════════════════════════════════════════════════

def _build_context_block(
    include_purpose: bool = True,
    include_tone: bool = True,
    include_audience: bool = True,
    include_custom: bool = False,
    language: str = "en",
) -> str:
    """노드 프롬프트에 주입할 사용자 컨텍스트 블록 생성.

    모든 항목이 비어있으면 빈 문자열을 반환하여
    기존 프롬프트에 영향을 주지 않습니다.
    """
    parts: list[str] = []

    if include_purpose and DOCUMENT_PURPOSE:
        label = "Document Purpose" if language == "en" else "문서 목적"
        parts.append(f"[{label}] {DOCUMENT_PURPOSE}")

    if include_tone and TONE_DIRECTIVE:
        if language == "en":
            parts.append(
                f"[Tone Directive] {TONE_DIRECTIVE} "
                "(This guides emphasis direction only. Do NOT fabricate facts.)"
            )
        else:
            parts.append(
                f"[톤 지시] {TONE_DIRECTIVE} "
                "(강조 방향 지시일 뿐, 사실 왜곡은 금지입니다.)"
            )

    if include_audience and TARGET_AUDIENCE:
        label = "Target Audience" if language == "en" else "대상 독자"
        parts.append(f"[{label}] {TARGET_AUDIENCE}")

    if include_custom and CUSTOM_DIRECTIVES:
        label = "Additional Directives" if language == "en" else "추가 지시"
        parts.append(f"[{label}]\n{CUSTOM_DIRECTIVES}")

    if not parts:
        return ""

    header = "User Context" if language == "en" else "사용자 컨텍스트"
    return f"\n\n[{header}]\n" + "\n".join(parts) + "\n"


# ── 단계별 컨텍스트 조회 함수 ──────────────────────────────────

def get_summary_context() -> str:
    """요약 단계용 (period_summarizer, theme_analyzer).

    PURPOSE + TONE만 주입. AUDIENCE/CUSTOM은 요약에 불필요.
    """
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=False, include_custom=False,
        language="en",
    )


def get_planning_context() -> str:
    """기획 단계용 (draft_planner).

    PURPOSE + AUDIENCE 주입. 목차 구성에 독자 고려 반영.
    TONE은 기획 단계에서 불필요 (집필에서 적용).
    """
    return _build_context_block(
        include_purpose=True, include_tone=False,
        include_audience=True, include_custom=False,
        language="en",
    )


def get_writing_context() -> str:
    """집필 단계용 (section_writer).

    전체 항목 주입. 실제 본문 작성에 모든 설정 반영.
    """
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=True, include_custom=True,
        language="en",
    )


def get_translation_context() -> str:
    """번역 단계용 (translate).

    PURPOSE + TONE + AUDIENCE를 한국어로 주입.
    CUSTOM은 번역 단계에서 제외 (원문 충실 번역 원칙).
    """
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=True, include_custom=False,
        language="ko",
    )
