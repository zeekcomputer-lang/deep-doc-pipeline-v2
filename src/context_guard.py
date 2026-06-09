"""
컨텍스트 예산 관리 모듈.

모든 LLM 호출의 메시지 페이로드가 BUDGET_BYTES(기본 95KB) 미만을 유지하도록 보장.
초과 시 분할/압축 전략을 각 노드에 제공.

손실 최소화 우선순위:
  1순위: 포맷 최적화 (무손실)
  2순위: 부가 컨텍스트 축소 (retry extras)
  3순위: 데이터 분할 + 다회차 처리 (구조적 손실 최소)
  4순위: 추출적 압축 (마지막 수단, 핵심 사실 보존)
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Tuple

BUDGET_BYTES: int = int(os.getenv("LLM_CONTEXT_BUDGET_KB", "95")) * 1024


class ContextBudgetExceeded(Exception):
    """Raised when context budget is exceeded after all trimming attempts."""
    def __init__(self, actual: int, budget: int):
        self.actual = actual
        self.budget = budget
        super().__init__(
            f"Context budget exceeded: {actual:,}B ({actual/1024:.1f}KB) > "
            f"{budget:,}B ({budget/1024:.1f}KB)"
        )


# ─── Measurement ────────────────────────────────────────────

def measure_messages_bytes(messages: list) -> int:
    """UTF-8 byte size of serialized messages list."""
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))


def measure_text_bytes(text: str) -> int:
    """UTF-8 byte size of a single text string."""
    return len(text.encode("utf-8"))


# ─── Budget calculation ────────────────────────────────────

def estimate_guard_overhead(schema: dict) -> int:
    """Estimate byte overhead of JSON guard appended by structured_call."""
    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
    guard_prefix_bytes = 350  # fixed guard text
    return guard_prefix_bytes + len(schema_str.encode("utf-8")) + 128  # margin


def available_data_budget(
    system_prompt: str,
    schema: dict,
    extra_fixed: str = "",
    margin: float = 0.85,
    budget_override: int = 0,
) -> int:
    """Available data budget (bytes) after subtracting system prompt, schema, and guard overhead.

    Args:
        budget_override: if > 0, use this instead of BUDGET_BYTES.
                         Pass effective_budget() for 504-aware calculation.
    """
    base = budget_override if budget_override > 0 else BUDGET_BYTES
    overhead = (
        measure_text_bytes(system_prompt)
        + estimate_guard_overhead(schema)
        + measure_text_bytes(extra_fixed)
        + 512  # role/content JSON wrapper
    )
    return max(int(base * margin) - overhead, 2048)


# ─── Data splitting ─────────────────────────────────────────

def split_items_for_budget(
    items: List[Any],
    format_fn: Callable[[List[Any]], str],
    budget_bytes: int,
) -> List[List[Any]]:
    """Split items into batches that fit within budget_bytes when formatted."""
    if not items:
        return []

    # 전체가 예산 내이면 단일 배치
    total_text = format_fn(items)
    if measure_text_bytes(total_text) <= budget_bytes:
        return [items]

    batches: List[List[Any]] = []
    current_batch: List[Any] = []
    current_bytes = 0

    for item in items:
        item_text = format_fn([item])
        item_bytes = measure_text_bytes(item_text)

        if current_batch and current_bytes + item_bytes > budget_bytes:
            batches.append(current_batch)
            current_batch = [item]
            current_bytes = item_bytes
        else:
            current_batch.append(item)
            current_bytes += item_bytes

    if current_batch:
        batches.append(current_batch)

    return batches


# ─── Retry context trimming ─────────────────────────────────

def trim_retry_context(
    previous_draft: str,
    feedback: str,
    hallucinated_tokens: List[str],
    budget_bytes: int = 20 * 1024,
) -> Tuple[str, str, List[str]]:
    """Trim retry context to fit within budget_bytes."""
    total = (
        measure_text_bytes(previous_draft)
        + measure_text_bytes(feedback)
        + measure_text_bytes(json.dumps(hallucinated_tokens, ensure_ascii=False))
    )

    if total <= budget_bytes:
        return previous_draft, feedback, hallucinated_tokens

    # 1순위: previous_draft 축소
    if len(previous_draft) > 600:
        previous_draft = (
            previous_draft[:300] + "\n...[중략]...\n" + previous_draft[-300:]
        )

    # 2순위: feedback 축소
    if len(feedback) > 500:
        feedback = feedback[:500] + "...[trimmed]"

    # 3순위: hallucinated_tokens 축소
    if len(hallucinated_tokens) > 20:
        hallucinated_tokens = hallucinated_tokens[-20:]

    return previous_draft, feedback, hallucinated_tokens


# ─── Fact-checker cross-validation ──────────────────────────

def cross_check_terms(
    candidates: List[str],
    events: List[Dict],
) -> List[str]:
    """Cross-check candidate hallucinated tokens against all entries.

    v3: knowledge entries use title/description/source_ref/date_hint fields.
    v2 compat: also checks date/issue/action if present.
    Returns truly absent tokens.
    """
    if not candidates or not events:
        return candidates

    all_text = " ".join(
        " ".join(
            str(e.get(k, ""))
            for k in ("title", "description", "source_ref", "date_hint",
                      "date", "issue", "action", "category", "impact_level")
        )
        for e in events
    )
    return [term for term in candidates if term not in all_text]
