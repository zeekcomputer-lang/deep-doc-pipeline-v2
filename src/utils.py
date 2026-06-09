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
) -> str:
    """Assemble Executive Summary from completed sections. Pure Python, no LLM."""
    parts: List[str] = ["# Executive Summary\n"]
    for i, item in enumerate(section_plan):
        title = item.get("title", f"섹션 {i}")
        body = completed.get(i, "_(섹션 누락)_")
        parts.append(f"\n## {title}\n\n{body}\n")
    return "".join(parts)


def compile_hybrid_whitepaper(
    executive_summary: str,
    chronological_appendix: str,
    category_warnings: List[str],
) -> str:
    """Final assembly: Executive Summary + Appendix + Audit Log. Pure Python."""
    parts = [executive_summary]

    parts.append("\n\n---\n\n")
    parts.append("# 부록: 월별 상세 타임라인\n\n")
    parts.append(chronological_appendix)

    # Audit log (category warnings only)
    if category_warnings:
        parts.append("\n\n---\n\n### 파이프라인 감사 로그\n\n")
        for w in category_warnings:
            parts.append(f"- {w}\n")

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

# ──────────────────────────────────────────────────────────────
# Proper Noun Extraction
# ──────────────────────────────────────────────────────────────

_COMMON_WORDS: set = {
    "the", "this", "that", "these", "those", "there", "then", "thus",
    "however", "moreover", "furthermore", "therefore", "although",
    "because", "since", "while", "after", "before", "during",
    "until", "unless", "whether", "where", "when", "what", "which",
    "who", "whom", "how", "why", "and", "but", "or", "not", "all",
    "each", "every", "some", "any", "no", "most", "many", "few",
    "several", "both", "other", "another", "such", "new", "old",
    "first", "last", "next", "same", "different", "may", "can",
    "will", "should", "could", "would", "must", "need", "key",
    "section", "summary", "report", "period", "phase", "step",
    "note", "warning", "result", "total", "data", "event", "issue",
    "action", "date", "month", "year", "day", "time", "also",
    "with", "from", "into", "about", "over", "under", "through",
    "between", "against", "without", "within", "along", "across",
    "behind", "beyond", "plus", "except", "for", "was", "were",
    "been", "being", "have", "has", "had", "having", "did", "does",
    "doing", "done", "made", "make", "take", "taken", "took",
    "give", "given", "gave", "set", "put", "keep", "kept", "let",
    "began", "begin", "beginning", "end", "ended", "ending",
    "include", "included", "including", "shown", "show", "showed",
    "based", "focus", "focused", "major", "main", "primary",
    "secondary", "critical", "important", "significant", "successful",
    "comprehensive", "overall", "specific", "particular", "general",
    "additional", "further", "related", "relevant", "ongoing",
    "initial", "final", "previous", "current", "future", "potential",
    "proposed", "required", "necessary", "available", "possible",
    "effective", "various", "certain", "entire", "complete", "full",
    "whole", "target", "source", "original", "following", "above",
    "below", "here", "per", "via", "its", "their", "our", "your",
    "his", "her", "they", "we", "you", "it", "is", "are", "an",
    "of", "in", "on", "at", "to", "by", "as", "if",
}


def extract_proper_nouns(text: str) -> List[str]:
    """Extract candidate proper nouns from text for preservation.

    Heuristic-based — conservative (over-preservation > under-preservation).
    """
    candidates: set = set()

    # 1. Dates (YYYY-MM-DD, YYYY-MM)
    candidates.update(re.findall(r'\d{4}-\d{2}-\d{2}', text))
    candidates.update(re.findall(r'\b\d{4}-\d{2}(?!\d)', text))

    # 2. Acronyms (2+ uppercase, possibly with hyphens/numbers)
    candidates.update(re.findall(r'\b[A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]+)*\b', text))

    # 3. CamelCase words (e.g., GitHub, FastAPI, LangGraph)
    candidates.update(re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', text))

    # 4. Capitalized words mid-sentence
    for m in re.finditer(r'(?<=[a-z,;:]\s)([A-Z][a-z]{2,})', text):
        word = m.group(1)
        if word.lower() not in _COMMON_WORDS:
            candidates.add(word)

    # 5. Multi-word capitalized phrases
    for m in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text):
        phrase = m.group(1)
        words = phrase.split()
        if any(w.lower() not in _COMMON_WORDS for w in words):
            candidates.add(phrase)

    # 6. Numbers with units
    candidates.update(re.findall(r'\d+(?:\.\d+)?\s*(?:%|KB|MB|GB|TB|ms|rpm|RPM)', text))

    # 7. Backtick-quoted tokens
    candidates.update(re.findall(r'`([^`]+)`', text))

    return sorted(c for c in candidates if len(c) >= 2)


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
