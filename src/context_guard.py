"""
컨텍스트 예산 관리 모듈 (v1.1-r4).

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
    """structured_call 내 예산 초과 시 발생 (모든 트리밍 시도 후에도 초과)."""
    def __init__(self, actual: int, budget: int):
        self.actual = actual
        self.budget = budget
        super().__init__(
            f"컨텍스트 예산 초과: {actual:,}B ({actual/1024:.1f}KB) > "
            f"{budget:,}B ({budget/1024:.1f}KB)"
        )


# ─── 측정 ────────────────────────────────────────────────────

def measure_messages_bytes(messages: list) -> int:
    """messages 리스트의 JSON 직렬화 UTF-8 바이트 수."""
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))


def measure_text_bytes(text: str) -> int:
    """단일 텍스트의 UTF-8 바이트 수."""
    return len(text.encode("utf-8"))


def fits_budget(messages: list, budget: int = 0) -> Tuple[bool, int]:
    """예산 내 여부와 실측 바이트 반환. budget=0이면 BUDGET_BYTES 사용."""
    if budget <= 0:
        budget = BUDGET_BYTES
    size = measure_messages_bytes(messages)
    return size <= budget, size


# ─── 예산 계산 ───────────────────────────────────────────────

def estimate_guard_overhead(schema: dict) -> int:
    """structured_call이 system 프롬프트에 부가하는 JSON guard의 바이트 수 추정.
    노드에서 messages 구성 후, structured_call 호출 전 총 예산을 추정할 때 사용.
    """
    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
    guard_prefix_bytes = 350  # 고정 가드 텍스트 (출력 규약 설명)
    return guard_prefix_bytes + len(schema_str.encode("utf-8")) + 128  # 마진


def available_data_budget(
    system_prompt: str,
    schema: dict,
    extra_fixed: str = "",
    margin: float = 0.85,
) -> int:
    """시스템 프롬프트·스키마·가드를 제외한 데이터 가용 예산(bytes).

    Args:
        system_prompt: 노드의 system 메시지 텍스트
        schema: response_model.model_json_schema()
        extra_fixed: 반드시 포함되는 고정 텍스트 (retry extras 등)
        margin: 안전 마진 (기본 85% — JSON wrapper/retry 여유)
    """
    overhead = (
        measure_text_bytes(system_prompt)
        + estimate_guard_overhead(schema)
        + measure_text_bytes(extra_fixed)
        + 512  # role/content JSON wrapper
    )
    return max(int(BUDGET_BYTES * margin) - overhead, 2048)


# ─── 데이터 분할 ─────────────────────────────────────────────

def split_items_for_budget(
    items: List[Any],
    format_fn: Callable[[List[Any]], str],
    budget_bytes: int,
) -> List[List[Any]]:
    """items를 format_fn 적용 시 budget_bytes 내에 맞도록 배치로 분할.

    format_fn(batch) → str 변환 후 바이트 측정.
    단일 아이템이 budget을 초과하면 해당 아이템만으로 배치 구성 (경고 출력).
    """
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


# ─── Retry 컨텍스트 축소 ─────────────────────────────────────

def trim_retry_context(
    previous_draft: str,
    feedback: str,
    hallucinated_tokens: List[str],
    budget_bytes: int = 20 * 1024,
) -> Tuple[str, str, List[str]]:
    """retry 컨텍스트를 budget_bytes 내로 축소.

    축소 우선순위 (덜 중요한 것부터):
      1. previous_draft — 앞뒤 300자만 유지
      2. feedback — 500자 제한
      3. hallucinated_tokens — 최근 20개만
    """
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
        feedback = feedback[:500] + "...[축소됨]"

    # 3순위: hallucinated_tokens 축소
    if len(hallucinated_tokens) > 20:
        hallucinated_tokens = hallucinated_tokens[-20:]

    return previous_draft, feedback, hallucinated_tokens


# ─── 팩트체커 교차 검증 ──────────────────────────────────────

def cross_check_terms(
    candidates: List[str],
    events: List[Dict],
) -> List[str]:
    """후보 환각 토큰이 전체 이벤트 원본에 존재하는지 Python 교차 확인.

    배치 분할 팩트체크 후, 한 배치에서 '확인 불가'로 나온 토큰이
    다른 배치의 이벤트에 실제 존재하는지 문자열 매칭으로 최종 판정.
    존재하지 않는 후보만 반환 (진짜 환각).
    """
    if not candidates or not events:
        return candidates

    all_text = " ".join(
        f"{e.get('date', '')} {e.get('issue', '')} {e.get('action', '')}"
        for e in events
    )
    return [term for term in candidates if term not in all_text]
