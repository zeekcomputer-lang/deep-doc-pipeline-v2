"""
Deterministic Pure Python logic — v3.0 (KR-first, category-based).
No LLM calls allowed in this module.
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple


# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────
CATEGORIES = [
    "Architecture_and_Tech",
    "Risk_and_Troubleshooting",
    "Business_and_Feature",
    "Lessons_Learned",
]

DATE_FULL_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DATE_MONTH_RE = re.compile(r"\d{4}-\d{2}")


# ──────────────────────────────────────────────────────────────
# Date utilities (best-effort, date-resilient)
# ──────────────────────────────────────────────────────────────

def parse_date_hint(hint: Optional[str]) -> Optional[str]:
    """Normalize date_hint to YYYY-MM or None."""
    if not hint or not isinstance(hint, str):
        return None
    hint = hint.strip()
    # YYYY-MM-DD → YYYY-MM
    m = DATE_FULL_RE.match(hint)
    if m:
        try:
            datetime.strptime(m.group(), "%Y-%m-%d")
            return hint[:7]
        except ValueError:
            return None
    # YYYY-MM
    m = DATE_MONTH_RE.match(hint)
    if m:
        return m.group()
    return None


def extract_date_from_text(text: str) -> Optional[str]:
    """Best-effort date extraction from free text. Returns YYYY-MM or None."""
    # Try YYYY-MM-DD first
    m = DATE_FULL_RE.search(text)
    if m:
        try:
            datetime.strptime(m.group(), "%Y-%m-%d")
            return m.group()[:7]
        except ValueError:
            pass
    # Try YYYY-MM
    m = DATE_MONTH_RE.search(text)
    if m:
        return m.group()
    return None


# ──────────────────────────────────────────────────────────────
# Knowledge Base utilities
# ──────────────────────────────────────────────────────────────

def normalize_category(cat: str) -> str:
    """Normalize category string. Returns original if valid, fallback to closest match."""
    if cat in CATEGORIES:
        return cat
    # Case-insensitive match
    for c in CATEGORIES:
        if c.lower() == cat.lower():
            return c
    # Partial match
    for c in CATEGORIES:
        if cat.lower().replace("_", "").replace(" ", "") in c.lower().replace("_", ""):
            return c
    return CATEGORIES[-1]  # fallback: Lessons_Learned


def deduplicate_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate knowledge entries by (title, source_ref) pair."""
    seen = set()
    result = []
    for entry in entries:
        key = (entry.get("title", ""), entry.get("source_ref", ""))
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def build_knowledge_base(entries: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
    """Build category → entries dict from flat entry list."""
    kb: Dict[str, List[Dict]] = {cat: [] for cat in CATEGORIES}
    for entry in entries:
        cat = normalize_category(entry.get("category", ""))
        entry["category"] = cat  # normalize in-place
        kb[cat].append(entry)
    return kb


def check_category_balance(kb: Dict[str, List[Dict]]) -> List[str]:
    """Check for empty categories. Returns list of warning messages."""
    warnings = []
    for cat in CATEGORIES:
        count = len(kb.get(cat, []))
        if count == 0:
            warnings.append(f"카테고리 '{cat}' 항목 0건 — 해당 카테고리 데이터 부재")
    return warnings


def build_temporal_index(entries: List[Dict[str, Any]]) -> List[Dict]:
    """Build chronologically sorted temporal index from knowledge entries.

    Entries without valid dates are grouped under 'undated'.
    """
    dated: List[Dict] = []
    undated: List[Dict] = []

    for entry in entries:
        period = parse_date_hint(entry.get("date_hint"))
        if period is None:
            # Attempt extraction from title/description
            text = f"{entry.get('title', '')} {entry.get('description', '')}"
            period = extract_date_from_text(text)

        item = {
            "period": period or "undated",
            "category": entry.get("category", ""),
            "title": entry.get("title", ""),
            "description": entry.get("description", ""),
            "source_ref": entry.get("source_ref", ""),
            "impact_level": entry.get("impact_level", "medium"),
        }

        if period:
            dated.append(item)
        else:
            undated.append(item)

    dated.sort(key=lambda x: x["period"])
    return dated + undated


# ──────────────────────────────────────────────────────────────
# Knowledge Base formatting for prompts
# ──────────────────────────────────────────────────────────────

def format_entries_for_prompt(entries: List[Dict]) -> str:
    """Format knowledge entries for LLM prompt injection (한국어)."""
    if not entries:
        return "(데이터 없음)"
    lines = []
    for e in entries:
        date = e.get("date_hint") or e.get("period", "")
        date_str = f"[{date}] " if date and date != "undated" else ""
        impact = e.get("impact_level", "")
        lines.append(
            f"- {date_str}[{impact}] {e.get('title', '')}: "
            f"{e.get('description', '')}"
        )
    return "\n".join(lines)


def format_category_entries(
    kb: Dict[str, List[Dict]], category: str
) -> str:
    """Format all entries for a single category."""
    entries = kb.get(category, [])
    return format_entries_for_prompt(entries)


# ──────────────────────────────────────────────────────────────
# Compile / Assembly utilities
# ──────────────────────────────────────────────────────────────

def compile_executive_summary(
    section_plan: List[Dict],
    completed: Dict[int, str],
    unverified: List[int],
) -> str:
    """Assemble Executive Summary from completed sections. Pure Python, no LLM."""
    parts: List[str] = ["# Executive Summary\n"]
    for i, item in enumerate(section_plan):
        title = item.get("title", f"섹션 {i}")
        body = completed.get(i, "_(섹션 누락)_")
        warn = ""
        if i in unverified:
            warn = (
                "> ⚠️ **미검증 섹션** — 팩트체크 3회 실패. "
                "수동 데이터 검증 필요.\n\n"
            )
        parts.append(f"\n## {title}\n\n{warn}{body}\n")
    return "".join(parts)


def compile_hybrid_whitepaper(
    executive_summary: str,
    chronological_appendix: str,
    unverified: List[int],
    category_warnings: List[str],
) -> str:
    """Final assembly: Executive Summary + Appendix + Audit Log. Pure Python."""
    parts = [executive_summary]

    parts.append("\n\n---\n\n")
    parts.append("# 부록: 월별 상세 타임라인\n\n")
    parts.append(chronological_appendix)

    # Audit log
    audit_items = []
    if unverified:
        audit_items.append(f"- 미검증 섹션 인덱스: {sorted(unverified)}")
    for w in category_warnings:
        audit_items.append(f"- {w}")

    if audit_items:
        parts.append("\n\n---\n\n### 파이프라인 감사 로그\n\n")
        parts.append("\n".join(audit_items))
        parts.append("\n")

    return "".join(parts)


# ──────────────────────────────────────────────────────────────
# Document splitting (for section-by-section polish)
# ──────────────────────────────────────────────────────────────
_SECTION_RE = re.compile(r"(?=\n## )")


def split_by_section(compiled: str) -> Tuple[str, List[str]]:
    """Split compiled document into (header, sections).

    Returns:
        header: everything before the first ## heading
        sections: list of section texts (each starts with \\n## )
    """
    parts = _SECTION_RE.split(compiled)
    header = parts[0] if parts else ""
    sections = parts[1:] if len(parts) > 1 else []
    return header, sections


# ──────────────────────────────────────────────────────────────
# Knowledge Base JSON export
# ──────────────────────────────────────────────────────────────

def export_knowledge_base(
    kb: Dict[str, List[Dict]],
    pipeline_version: str = "3.0",
) -> Dict[str, Any]:
    """Format knowledge base for JSON export."""
    category_counts = {cat: len(entries) for cat, entries in kb.items()}
    total = sum(category_counts.values())
    return {
        "metadata": {
            "pipeline_version": pipeline_version,
            "total_entries": total,
            "category_counts": category_counts,
        },
        "categories": kb,
    }
