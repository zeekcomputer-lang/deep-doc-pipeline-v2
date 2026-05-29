"""
결정론적 Pure Python 로직.
LLM 호출 절대 금지. 날짜/필터/조립 등 논리 조작은 모두 여기서.
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import List, Dict, Any


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PERIOD_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def is_valid_date(s: str) -> bool:
    """YYYY-MM-DD 형식 + 실제 유효 날짜인지 검증."""
    if not isinstance(s, str) or not DATE_PATTERN.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def chrono_sort_and_group(events: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
    """
    이벤트 리스트를 날짜순 정렬하고 YYYY-MM 키로 그룹핑.
    유효하지 않은 날짜는 silently drop (extractor 단계에서 이미 검증되어 있어야 함).
    """
    valid = [e for e in events if is_valid_date(e.get("date", ""))]
    valid.sort(key=lambda e: e["date"])
    grouped: Dict[str, List[Dict]] = {}
    for ev in valid:
        period = ev["date"][:7]  # "YYYY-MM"
        grouped.setdefault(period, []).append(ev)
    return grouped


def filter_by_period(grouped: Dict[str, List[Dict]], target_period: str) -> List[Dict]:
    """
    context_filter_node 핵심 로직.
    target_period에 해당하는 이벤트만 반환. LLM 환각 차단의 1차 방어선.
    """
    if not PERIOD_PATTERN.match(target_period or ""):
        return []
    return grouped.get(target_period, [])


def validate_outline_periods(outline: List[Dict], grouped: Dict[str, List[Dict]]) -> List[str]:
    """
    목차의 target_period가 실제 grouped 키에 존재하는지 검증.
    존재하지 않는 기간 리스트 반환 (비어 있으면 정상).
    """
    available = set(grouped.keys())
    invalid = []
    for item in outline:
        period = item.get("target_period", "")
        if period not in available:
            invalid.append(f"index={item.get('index')} period={period}")
    return invalid


def compile_sections(outline: List[Dict], completed: Dict[int, str],
                     unverified: List[int]) -> str:
    """
    Pure Python 조립. LLM 호출 금지.
    목차 순서대로 헤더 + 본문을 단순 결합.
    """
    parts: List[str] = ["# 종합 백서\n"]
    # outline은 사전(dict) 리스트이며 index 키를 가짐
    sorted_items = sorted(outline, key=lambda x: x.get("index", 0))
    for item in sorted_items:
        idx = item.get("index")
        title = item.get("title", f"섹션 {idx}")
        period = item.get("target_period", "")
        body = completed.get(idx, "_(섹션 누락)_")
        warn = ""
        if idx in unverified:
            warn = (
                "> ⚠️ **검증 미완료 섹션** — 자동 팩트체크 3회 실패. 원본 데이터 대조 필요.\n\n"
            )
        parts.append(f"\n## {title}  \n_대상 기간: {period}_\n\n{warn}{body}\n")
    # 감사 로그
    if unverified:
        parts.append("\n---\n\n### 감사 로그\n")
        parts.append(f"- 검증 미완료 섹션 인덱스: {sorted(unverified)}\n")
    return "".join(parts)


def format_events_for_prompt(events: List[Dict]) -> str:
    """이벤트 리스트를 프롬프트용 텍스트로 변환."""
    if not events:
        return "(데이터 없음)"
    lines = []
    for ev in events:
        lines.append(f"- [{ev['date']}] 이슈: {ev['issue']} / 조치: {ev['action']}")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────
# 문서 분할 (504 대응 — 섹션별 윈문/검수용)
# ────────────────────────────────────────────────────────────
_SECTION_RE = re.compile(r'(?=\n## )')


def split_compiled_by_section(compiled: str):
    """compile_sections 결과물을 (문서 헤더, 섹션 리스트, 감사 로그) 로 분리.

    Returns:
        doc_header (str): "# 종합 백서\n" 등 문서 제목부
        sections (List[str]): ["\n## Title  \n_대상 기간: ..._\n\nbody\n", ...]
        audit_log (str): "\n---\n\n### 감사 로그\n..." 또는 ""
    """
    # 감사 로그 분리
    audit = ""
    audit_sep = "\n---\n"
    pos = compiled.rfind(audit_sep)
    if pos >= 0 and "수행감사 로그" in compiled[pos:] or pos >= 0 and "### 감사 로그" in compiled[pos:]:
        audit = compiled[pos:]
        compiled = compiled[:pos]

    parts = _SECTION_RE.split(compiled)
    doc_header = parts[0] if parts else ""
    sections = parts[1:] if len(parts) > 1 else []

    return doc_header, sections, audit


def split_section_header_body(section: str):
    """섹션 텍스트에서 헤더(## 제목 + _대상 기간_)와 본문을 분리.

    Returns:
        header (str): "\n## Title  \n_대상 기간: YYYY-MM_\n\n"
        body (str): "본문 텍스트..."
    """
    # 기간 라인 이후의 첫 빈 줄에서 분리
    match = re.search(r'(_대상 기간:.*?_)\n\n', section)
    if match:
        split_pos = match.end()
        return section[:split_pos], section[split_pos:]
    # 폴백: 첫 번째 빈 줄에서 분리
    idx = section.find('\n\n')
    if idx >= 0:
        return section[:idx + 2], section[idx + 2:]
    return section, ""
