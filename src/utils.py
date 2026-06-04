"""
Deterministic Pure Python logic. No LLM calls allowed.
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import List, Dict, Any, Set


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PERIOD_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def is_valid_date(s: str) -> bool:
    """Validate YYYY-MM-DD format and actual calendar date."""
    if not isinstance(s, str) or not DATE_PATTERN.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def chrono_sort_and_group(events: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
    """Sort events chronologically and group by YYYY-MM key."""
    valid = [e for e in events if is_valid_date(e.get("date", ""))]
    valid.sort(key=lambda e: e["date"])
    grouped: Dict[str, List[Dict]] = {}
    for ev in valid:
        period = ev["date"][:7]
        grouped.setdefault(period, []).append(ev)
    return grouped


def filter_by_period(grouped: Dict[str, List[Dict]], target_period: str) -> List[Dict]:
    """Return events for target_period only. Primary defense against LLM hallucination."""
    if not PERIOD_PATTERN.match(target_period or ""):
        return []
    return grouped.get(target_period, [])


def validate_outline_periods(outline: List[Dict], grouped: Dict[str, List[Dict]]) -> List[str]:
    """Verify outline target_periods exist in grouped keys. Returns invalid entries."""
    available = set(grouped.keys())
    invalid = []
    for item in outline:
        period = item.get("target_period", "")
        if period not in available:
            invalid.append(f"index={item.get('index')} period={period}")
    return invalid


def compile_sections(outline: List[Dict], completed: Dict[int, str],
                     unverified: List[int]) -> str:
    """Pure Python assembly. No LLM calls. English output for subsequent translation."""
    parts: List[str] = ["# Comprehensive Whitepaper\n"]
    sorted_items = sorted(outline, key=lambda x: x.get("index", 0))
    for item in sorted_items:
        idx = item.get("index")
        title = item.get("title", f"Section {idx}")
        period = item.get("target_period", "")
        body = completed.get(idx, "_(Section missing)_")
        warn = ""
        if idx in unverified:
            warn = (
                "> ⚠️ **Unverified Section** — Automatic fact-check failed 3 times. "
                "Manual data verification required.\n\n"
            )
        parts.append(f"\n## {title}  \n_Target period: {period}_\n\n{warn}{body}\n")
    if unverified:
        parts.append("\n---\n\n### Audit Log\n")
        parts.append(f"- Unverified section indices: {sorted(unverified)}\n")
    return "".join(parts)


def format_events_for_prompt(events: List[Dict]) -> str:
    """Convert event list to prompt-ready text (English labels)."""
    if not events:
        return "(No data)"
    lines = []
    for ev in events:
        lines.append(f"- [{ev['date']}] Issue: {ev['issue']} / Action: {ev['action']}")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────
# Document Splitting (for section-by-section polish/verify/translate)
# ────────────────────────────────────────────────────────────
_SECTION_RE = re.compile(r'(?=\n## )')


def split_compiled_by_section(compiled: str):
    """Split compile_sections output into (doc_header, sections, audit_log).

    Returns:
        doc_header (str): "# Comprehensive Whitepaper\\n" etc.
        sections (List[str]): ["\\n## Title  \\n_Target period: ..._\\n\\nbody\\n", ...]
        audit_log (str): "\\n---\\n\\n### Audit Log\\n..." or ""
    """
    audit = ""
    audit_sep = "\n---\n"
    pos = compiled.rfind(audit_sep)
    if pos >= 0 and "### Audit Log" in compiled[pos:]:
        audit = compiled[pos:]
        compiled = compiled[:pos]

    parts = _SECTION_RE.split(compiled)
    doc_header = parts[0] if parts else ""
    sections = parts[1:] if len(parts) > 1 else []

    return doc_header, sections, audit


def split_section_header_body(section: str):
    """Separate section header (## title + _period_) from body text.

    Returns:
        header (str): "\\n## Title  \\n_Target period: YYYY-MM_\\n\\n"
        body (str): "body text..."
    """
    match = re.search(r'(_Target period:.*?_)\n\n', section)
    if match:
        split_pos = match.end()
        return section[:split_pos], section[split_pos:]
    idx = section.find('\n\n')
    if idx >= 0:
        return section[:idx + 2], section[idx + 2:]
    return section, ""


# ────────────────────────────────────────────────────────────
# Proper Noun Extraction (for translation preservation)
# ────────────────────────────────────────────────────────────

# Common English words that appear capitalized at sentence start
# but are NOT proper nouns. Lowercase for case-insensitive matching.
_COMMON_WORDS: Set[str] = {
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
    """Extract candidate proper nouns from English text for translation preservation.

    Heuristic-based — conservative (may include false positives, which is
    acceptable since over-preservation is safer than under-preservation).
    """
    candidates: set = set()

    # 1. Dates (YYYY-MM-DD, YYYY-MM)
    candidates.update(re.findall(r'\d{4}-\d{2}-\d{2}', text))
    candidates.update(re.findall(r'\b\d{4}-\d{2}(?!\d)', text))

    # 2. Acronyms (2+ uppercase, possibly with hyphens/numbers)
    candidates.update(re.findall(r'\b[A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]+)*\b', text))

    # 3. CamelCase words (e.g., GitHub, FastAPI, LangGraph)
    candidates.update(re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', text))

    # 4. Capitalized words mid-sentence (after lowercase/comma/semicolon)
    for m in re.finditer(r'(?<=[a-z,;:]\s)([A-Z][a-z]{2,})', text):
        word = m.group(1)
        if word.lower() not in _COMMON_WORDS:
            candidates.add(word)

    # 5. Multi-word capitalized phrases (e.g., "Task Scheduler", "Rate Limiter")
    for m in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text):
        phrase = m.group(1)
        words = phrase.split()
        if any(w.lower() not in _COMMON_WORDS for w in words):
            candidates.add(phrase)

    # 6. Numbers with units
    candidates.update(re.findall(r'\d+(?:\.\d+)?\s*(?:%|KB|MB|GB|TB|ms|rpm|RPM)', text))

    # 7. Backtick-quoted tokens (often code/identifiers in markdown)
    candidates.update(re.findall(r'`([^`]+)`', text))

    return sorted(c for c in candidates if len(c) >= 2)


# ────────────────────────────────────────────────────────────
# Year / Period Extraction (for year-by-year translation)
# ────────────────────────────────────────────────────────────

def extract_years_from_content(text: str) -> List[str]:
    """Extract sorted unique years from _Target period: YYYY-MM_ markers.

    Falls back to any YYYY-MM date pattern if no markers found.
    """
    periods = re.findall(r'_Target period:\s*(\d{4})-\d{2}_', text)
    if not periods:
        periods = re.findall(r'\b(\d{4})-\d{2}', text)
    return sorted(set(periods))


def extract_sections_for_year(sections: List[str], year: str) -> List[str]:
    """Filter sections belonging to a specific year based on Target period marker."""
    return [s for s in sections if f'_Target period: {year}-' in s]



