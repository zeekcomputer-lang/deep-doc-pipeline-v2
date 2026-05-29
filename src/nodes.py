"""
LangGraph 노드 함수들. One Node = One Task 원칙 엄격 준수.
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
)
from .llm import structured_call
from .utils import (
    is_valid_date, chrono_sort_and_group, filter_by_period,
    validate_outline_periods, compile_sections, format_events_for_prompt,
    split_compiled_by_section, split_section_header_body,
)


LOCAL_DATA_PATH = "./data/records.jsonl"

# ──────────────────────────────────────────────────────────────
# Phase 1: 추출
# ──────────────────────────────────────────────────────────────
def load_docs_node(state: GraphState) -> Dict[str, Any]:
    """JSONL 파일을 읽어 raw_docs에 적재."""
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
                print(f"  [load_docs] line {ln} skipped: {e}")
    print(f"[load_docs] loaded={len(docs)} failed={failed}")
    return {"raw_docs": docs}


def fanout_to_extractor(state: GraphState):
    """Send API로 strict_extractor_node를 문서별 병렬 실행."""
    return [
        Send("strict_extractor", {"doc": d, "doc_index": i})
        for i, d in enumerate(state["raw_docs"])
    ]


def strict_extractor_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    단일 문서에서 ExtractedEvent 추출.
    structured_call 내부에 3회 재시도 내장.
    """
    doc = payload["doc"]
    idx = payload["doc_index"]
    doc_text = json.dumps(doc, ensure_ascii=False)

    messages = [
        {"role": "system", "content": (
            "너는 문서 분석가다. 주어진 원본 문서에서 핵심 사실을 추출하라. "
            "date는 반드시 YYYY-MM-DD 형식. 원본에 명시되지 않은 정보는 절대 만들지 마라."
        )},
        {"role": "user", "content": f"원본 문서:\n{doc_text}\n\n위 문서에서 date/issue/action을 추출하라."},
    ]
    try:
        ev = structured_call(messages, ExtractedEvent, role="extractor", temperature=0.0)
        if not is_valid_date(ev.date):
            print(f"  [extractor] doc {idx} invalid date '{ev.date}' — dropped")
            return {"extracted_events": []}
        return {"extracted_events": [ev.model_dump()]}
    except Exception as e:
        print(f"  [extractor] doc {idx} failed after retries: {e}")
        return {"extracted_events": []}


def chrono_sorter_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python 정렬 + 월별 그룹핑."""
    grouped = chrono_sort_and_group(state["extracted_events"])
    print(f"[chrono_sorter] events={len(state['extracted_events'])} months={list(grouped.keys())}")
    return {"grouped_chunks": grouped}


# ──────────────────────────────────────────────────────────────
# Phase 2: 마이크로 요약
# ──────────────────────────────────────────────────────────────
def fanout_to_period_summarizer(state: GraphState):
    """월별 병렬 요약."""
    return [
        Send("period_summarizer", {"period": p, "events": evs})
        for p, evs in state["grouped_chunks"].items()
    ]


def period_summarizer_node(payload: Dict[str, Any]) -> Dict[str, Any]:
    """월별 핵심 동향 3문장 요약."""
    period = payload["period"]
    events = payload["events"]
    events_text = format_events_for_prompt(events)

    messages = [
        {"role": "system", "content": (
            "너는 시기별 동향 분석가다. 주어진 이벤트 목록을 보고 정확히 3문장으로 핵심 동향을 요약하라. "
            "이벤트에 없는 내용은 추가하지 마라."
        )},
        {"role": "user", "content": f"기간: {period}\n\n이벤트 목록:\n{events_text}\n\n3문장 요약:"},
    ]
    result = structured_call(messages, PeriodSummary, role="default", temperature=0.2)
    print(f"[period_summarizer] {period}: {result.summary[:60]}...")
    return {"period_summaries": {period: result.summary}}


def theme_analyzer_node(state: GraphState) -> Dict[str, Any]:
    """전체 흐름 1문단 도출."""
    summaries = state["period_summaries"]
    joined = "\n".join(f"[{k}] {v}" for k, v in sorted(summaries.items()))
    messages = [
        {"role": "system", "content": (
            "너는 거시 분석가다. 월별 요약을 모아 전체 프로젝트의 성과와 위기 흐름을 관통하는 통찰을 "
            "정확히 1문단으로 작성하라. 요약에 없는 내용은 추가하지 마라."
        )},
        {"role": "user", "content": f"월별 요약:\n{joined}\n\n전체 흐름 1문단:"},
    ]
    result = structured_call(messages, GlobalTheme, role="default", temperature=0.3)
    print(f"[theme_analyzer] theme: {result.theme[:80]}...")
    return {"global_theme": result.theme}


# ──────────────────────────────────────────────────────────────
# Phase 3: 라우터
# ──────────────────────────────────────────────────────────────
def route_by_target(state: GraphState) -> str:
    target = state.get("target_format", "whitepaper")
    return "status_report" if target == "status_report" else "whitepaper"


# ──────────────────────────────────────────────────────────────
# Phase 4-A: 현황판
# ──────────────────────────────────────────────────────────────
def status_formatter_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python 마크다운 조립 — 현황판은 LLM 없이도 충분."""
    grouped = state["grouped_chunks"]
    summaries = state["period_summaries"]
    theme = state.get("global_theme", "")

    parts: List[str] = ["# 현황판 리포트\n\n"]
    parts.append(f"## 전체 흐름\n\n{theme}\n\n")
    parts.append("## 월별 동향\n\n")
    for period in sorted(grouped.keys()):
        parts.append(f"### {period}\n")
        parts.append(f"**요약:** {summaries.get(period, '(요약 없음)')}\n\n")
        parts.append("**이벤트:**\n")
        for ev in grouped[period]:
            parts.append(f"- `{ev['date']}` {ev['issue']} → {ev['action']}\n")
        parts.append("\n")
    return {"final_output": "".join(parts)}


# ──────────────────────────────────────────────────────────────
# Phase 4-B [1단계]: 기획 검증 루프
# ──────────────────────────────────────────────────────────────
def draft_planner_node(state: GraphState) -> Dict[str, Any]:
    """global_theme + period_summaries만으로 목차 기획."""
    theme = state["global_theme"]
    summaries = state["period_summaries"]
    available_periods = sorted(summaries.keys())
    joined = "\n".join(f"[{k}] {v}" for k, v in sorted(summaries.items()))

    prev_feedback = state.get("outline_feedback", "")
    retry_hint = ""
    if prev_feedback:
        retry_hint = (
            f"\n\n[이전 목차 반려 사유 — 반드시 반영]\n{prev_feedback}\n"
        )

    messages = [
        {"role": "system", "content": (
            "너는 백서 기획자다. 주어진 전체 흐름과 월별 요약만으로 백서의 목차를 작성하라. "
            "각 목차 항목은 정확히 하나의 'YYYY-MM' 기간을 다뤄야 한다 (target_period). "
            f"사용 가능한 기간 키: {available_periods}\n"
            "기간은 반드시 이 목록 중에서만 선택하라. 시계열 순서대로 정렬하라."
        )},
        {"role": "user", "content": f"전체 흐름:\n{theme}\n\n월별 요약:\n{joined}{retry_hint}\n\n목차 작성:"},
    ]
    result = structured_call(messages, Outline, role="default", temperature=0.3)
    items = [it.model_dump() for it in result.items]
    print(f"[draft_planner] outline items={len(items)}")
    return {"outline": items}


def planner_critique_node(state: GraphState) -> Dict[str, Any]:
    """
    목차 검수: 시계열 흐름 + target_period 존재성 검증.
    target_period 존재성은 Python으로 결정론적 검증 (LLM 환각 차단).
    """
    outline = state["outline"]
    grouped = state["grouped_chunks"]

    # Python 검증 1: target_period 존재성
    invalid_periods = validate_outline_periods(outline, grouped)
    if invalid_periods:
        msg = f"존재하지 않는 target_period 사용: {invalid_periods}"
        print(f"[planner_critique] REJECTED (python): {msg}")
        return {
            "is_outline_approved": False,
            "outline_feedback": msg,
            "outline_retry_count": state.get("outline_retry_count", 0) + 1,
        }

    # Python 검증 2: 시계열 정렬
    periods = [it["target_period"] for it in outline]
    if periods != sorted(periods):
        msg = f"시계열 순서 위반. 현재 순서: {periods}"
        print(f"[planner_critique] REJECTED (python): {msg}")
        return {
            "is_outline_approved": False,
            "outline_feedback": msg,
            "outline_retry_count": state.get("outline_retry_count", 0) + 1,
        }

    # LLM 검수: 구성의 합리성
    outline_text = "\n".join(
        f"{it['index']}. [{it['target_period']}] {it['title']} — {it['intent']}"
        for it in outline
    )
    messages = [
        {"role": "system", "content": (
            "너는 깐깐한 기획 검수자다. 주어진 목차가 백서로서 자연스러운 흐름인지 평가하라. "
            "각 섹션 의도가 명확하고 중복이 없으면 승인. 문제 있으면 구체적 사유 제시."
        )},
        {"role": "user", "content": f"목차:\n{outline_text}\n\n검수 결과:"},
    ]
    result = structured_call(messages, OutlineCritique, role="judge", temperature=0.0)
    retry = state.get("outline_retry_count", 0) + (0 if result.is_outline_approved else 1)
    print(f"[planner_critique] approved={result.is_outline_approved} retry={retry}")

    # Fail-Safe: 3회 초과 시 강제 통과
    if not result.is_outline_approved and retry >= 3:
        print("[planner_critique] FAIL-SAFE: 강제 통과 (재시도 3회 초과)")
        return {
            "is_outline_approved": True,
            "outline_feedback": f"[강제통과] {result.feedback}",
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
# Phase 4-B [2단계]: 집필 + 팩트체크 루프
# ──────────────────────────────────────────────────────────────
def init_writing_node(state: GraphState) -> Dict[str, Any]:
    """집필 루프 초기화."""
    return {
        "current_section_index": 0,
        "section_retry_count": 0,
        "previous_draft": "",
        "current_draft": "",
    }


def section_writer_node(state: GraphState) -> Dict[str, Any]:
    """
    v1.1: 재작성 시 previous_draft + hallucinated_tokens를 명시 주입.
    """
    outline = state["outline"]
    idx = state["current_section_index"]
    item = outline[idx]
    period = item["target_period"]
    grouped = state["grouped_chunks"]

    # Pure Python 컨텍스트 컷오프
    events = filter_by_period(grouped, period)
    events_text = format_events_for_prompt(events)

    retry = state.get("section_retry_count", 0)
    extra = ""
    if retry > 0:
        prev = state.get("previous_draft", "")
        bad_tokens = state.get("hallucinated_tokens", [])
        feedback = state.get("draft_feedback", "")
        extra = (
            f"\n\n[직전 반려 초안 — 절대 동일하게 작성하지 마라]\n{prev}\n"
            f"\n[사용 금지 토큰 — 원본에 없는 환각]\n{bad_tokens}\n"
            f"\n[수정 지시사항]\n{feedback}\n"
        )

    messages = [
        {"role": "system", "content": (
            "너는 백서 집필자다. 오직 제공된 원본 이벤트 데이터만을 근거로 섹션을 작성하라. "
            "원본에 없는 고유명사, 날짜, 수치를 절대 만들지 마라. 마크다운 본문만 출력."
        )},
        {"role": "user", "content": (
            f"섹션 제목: {item['title']}\n"
            f"대상 기간: {period}\n"
            f"전달 의도: {item['intent']}\n\n"
            f"원본 이벤트 (이 데이터만 사용):\n{events_text}"
            f"{extra}\n\n섹션 본문 작성:"
        )},
    ]
    result = structured_call(messages, SectionDraft, role="writer", temperature=0.3)
    print(f"[section_writer] idx={idx} period={period} retry={retry} len={len(result.content)}")
    return {"current_draft": result.content}


def fact_checker_node(state: GraphState) -> Dict[str, Any]:
    """
    v1.1: hallucinated_terms 강제 추출.
    """
    outline = state["outline"]
    idx = state["current_section_index"]
    item = outline[idx]
    period = item["target_period"]
    grouped = state["grouped_chunks"]
    events = filter_by_period(grouped, period)
    events_text = format_events_for_prompt(events)
    draft = state["current_draft"]

    messages = [
        {"role": "system", "content": (
            "너는 매우 깐깐한 감사관이다. 초안에 원본 데이터에 없는 고유명사, 날짜, 수치가 "
            "하나라도 등장하면 무조건 is_draft_approved=False를 반환하라. "
            "동시에 환각으로 판단되는 정확한 토큰 리스트를 hallucinated_terms 필드에 추출하라. "
            "feedback에는 구체적으로 어느 부분이 문제인지 명시하라."
        )},
        {"role": "user", "content": (
            f"원본 이벤트 (이것만이 진실):\n{events_text}\n\n"
            f"검수 대상 초안:\n{draft}\n\n검수 결과:"
        )},
    ]
    result = structured_call(messages, FactCheckResult, role="judge", temperature=0.0)
    print(f"[fact_checker] idx={idx} approved={result.is_draft_approved} "
          f"halluc={result.hallucinated_terms[:3]}")
    return {
        "is_draft_approved": result.is_draft_approved,
        "draft_feedback": result.feedback,
        "hallucinated_tokens": result.hallucinated_terms if not result.is_draft_approved else [],
    }


def route_section_draft(state: GraphState) -> str:
    """
    v1.1 분기:
    - Pass → save_section
    - Fail & retry < 3 → section_writer (재작성)
    - Fail & retry >= 3 → save_section_with_warning (강제통과)
    """
    if state.get("is_draft_approved"):
        return "save_section"
    if state.get("section_retry_count", 0) >= 3:
        return "save_section_with_warning"
    return "retry_section"


def retry_section_node(state: GraphState) -> Dict[str, Any]:
    """재작성 준비: previous_draft 갱신 + retry count 증가."""
    return {
        "previous_draft": state.get("current_draft", ""),
        "section_retry_count": state.get("section_retry_count", 0) + 1,
    }


def save_section_node(state: GraphState) -> Dict[str, Any]:
    """승인된 섹션 저장 + 인덱스 증가 + 스코프 초기화."""
    idx = state["current_section_index"]
    draft = state["current_draft"]
    print(f"[save_section] idx={idx} APPROVED")
    return {
        "completed_sections": {idx: draft},
        "current_section_index": idx + 1,
        "section_retry_count": 0,
        "previous_draft": "",
        # hallucinated_tokens는 누적 reducer라 섹션별 초기화 불가 — writer 측에서 retry==0이면 무시
    }


def save_section_with_warning_node(state: GraphState) -> Dict[str, Any]:
    """Fail-Safe 강제통과: 워터마크 삽입 + unverified_sections 누적."""
    idx = state["current_section_index"]
    draft = state["current_draft"]
    feedback = state.get("draft_feedback", "(사유 미기록)")
    warned = (
        f"> ⚠️ **검증 미완료 섹션** — 자동 팩트체크 3회 실패.\n"
        f"> 마지막 반려 사유: {feedback}\n\n"
        f"{draft}"
    )
    print(f"[save_section_with_warning] idx={idx} FORCE-PASS")
    return {
        "completed_sections": {idx: warned},
        "unverified_sections": [idx],
        "current_section_index": idx + 1,
        "section_retry_count": 0,
        "previous_draft": "",
    }


def route_next_section(state: GraphState) -> str:
    """모든 섹션 완료 시 compiler로, 아니면 다음 섹션 집필."""
    if state["current_section_index"] >= len(state["outline"]):
        return "compiler"
    return "section_writer"


# ──────────────────────────────────────────────────────────────
# Phase 4-B [3단계]: 조립 → 윤문 → 2차 팩트체크
# ──────────────────────────────────────────────────────────────
def compiler_node(state: GraphState) -> Dict[str, Any]:
    """Pure Python — LLM 호출 금지."""
    outline = state["outline"]
    completed = state.get("completed_sections", {})
    unverified = state.get("unverified_sections", [])
    compiled = compile_sections(outline, completed, unverified)
    print(f"[compiler] sections={len(completed)} unverified={unverified} len={len(compiled)}")
    return {"final_compiled": compiled, "polish_retry_count": 0}


def polish_node(state: GraphState) -> Dict[str, Any]:
    """섹션별 분할 윤문 + 스트리밍. 대용량 컨텍스트 504 타임아웃 방지.

    v1.1-r3: compile_sections 결과를 섹션 단위로 분리하여 개별 윤문.
    각 API 호출의 컨텍스트를 1/K로 축소하고, stream=True로 게이트웨이 read_timeout 리셋.
    """
    compiled = state["final_compiled"]
    retry_count = state.get("polish_retry_count", 0)
    doc_header, sections, audit = split_compiled_by_section(compiled)

    if not sections:
        print("[polish] no sections found — skipping")
        return {"final_output": compiled}

    system_prompt = (
        "너는 교정 편집자다. 입력된 본문의 사실 정보(날짜, 고유명사, 수치, 인과관계)는 "
        "단 한 글자도 추가/삭제/수정하지 마라. 오직 문단 연결어, 호응, 어색한 문장 흐름만 "
        "다듬어라. 새로운 정보를 절대 만들지 마라. "
        "마크다운 구조(헤더, 리스트, 워터마크 블록인용)는 유지하라."
    )

    polished_sections: List[str] = []
    for i, section in enumerate(sections):
        header, body = split_section_header_body(section)
        if not body.strip():
            polished_sections.append(section)
            continue

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"본문:\n{body}\n\n윤문 결과:"},
        ]
        result = structured_call(
            messages, PolishedDocument, role="writer",
            temperature=0.1, stream=True,
        )
        polished_sections.append(header + result.content)
        print(f"[polish] section {i + 1}/{len(sections)} retry={retry_count} "
              f"len={len(result.content)}")

    final = doc_header + "".join(polished_sections) + audit
    print(f"[polish] done: sections={len(sections)} total_len={len(final)}")
    return {"final_output": final}


def final_fact_checker_node(state: GraphState) -> Dict[str, Any]:
    """섹션별 분할 2차 팩트체크 + 스트리밍. 대용량 컨텍스트 504 타임아웃 방지.

    v1.1-r3: 원본과 윤문본을 섹션 단위로 분리하여 개별 검증.
    섹션 수 불일치 시 전체 문서 비교로 폴백 (stream으로 504 방지).
    """
    original = state["final_compiled"]
    polished = state["final_output"]
    retry_count = state.get("polish_retry_count", 0)

    _, orig_sections, _ = split_compiled_by_section(original)
    _, pol_sections, _ = split_compiled_by_section(polished)

    system_prompt = (
        "너는 최종 감사관이다. 원본과 윤문본을 비교하여 "
        "고유명사/날짜/수치/사실이 추가되었거나 변형되었는지 검증하라. "
        "문장 흐름 변화는 허용, 사실 변경만 환각으로 처리하라."
    )

    # 섹션 수 불일치 시 전체 문서 비교 폴백 (stream으로 504 방지)
    if len(orig_sections) != len(pol_sections) or not orig_sections:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"[ORIGINAL]\n{original}\n\n[POLISHED]\n{polished}\n\n검증 결과:"
            )},
        ]
        result = structured_call(messages, FactCheckResult, role="judge",
                                temperature=0.0, stream=True)
        print(f"[final_fact_checker] fallback-full approved={result.is_draft_approved} "
              f"retry={retry_count}")
        return {
            "is_draft_approved": result.is_draft_approved,
            "draft_feedback": result.feedback,
        }

    # 섹션별 개별 검증
    all_approved = True
    feedback_parts: List[str] = []

    for i, (orig, pol) in enumerate(zip(orig_sections, pol_sections)):
        _, orig_body = split_section_header_body(orig)
        _, pol_body = split_section_header_body(pol)

        # 본문 변경 없음 — 검수 불필요
        if not orig_body.strip() or orig_body.strip() == pol_body.strip():
            continue

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"[ORIGINAL]\n{orig_body}\n\n[POLISHED]\n{pol_body}\n\n검증 결과:"
            )},
        ]
        result = structured_call(messages, FactCheckResult, role="judge",
                                temperature=0.0, stream=True)
        if not result.is_draft_approved:
            all_approved = False
            feedback_parts.append(f"섹션{i + 1}: {result.feedback}")
        print(f"[final_fact_checker] section {i + 1}/{len(orig_sections)} "
              f"approved={result.is_draft_approved}")

    feedback = "; ".join(feedback_parts) if feedback_parts else "전 섹션 검증 통과"
    print(f"[final_fact_checker] overall approved={all_approved} retry={retry_count}")
    return {
        "is_draft_approved": all_approved,
        "draft_feedback": feedback,
    }


def route_final_check(state: GraphState) -> str:
    """polish 검증 분기."""
    if state.get("is_draft_approved"):
        return "END"
    if state.get("polish_retry_count", 0) >= 2:
        # Fail-Safe: 폴리시 우회, final_compiled를 최종 결과로 채택
        return "fallback_to_compiled"
    return "retry_polish"


def retry_polish_node(state: GraphState) -> Dict[str, Any]:
    return {"polish_retry_count": state.get("polish_retry_count", 0) + 1}


def fallback_to_compiled_node(state: GraphState) -> Dict[str, Any]:
    """폴리시 우회 — final_compiled 그대로 채택."""
    print("[fallback_to_compiled] polish 검증 실패 — 조립본 그대로 채택")
    return {"final_output": state["final_compiled"]}
