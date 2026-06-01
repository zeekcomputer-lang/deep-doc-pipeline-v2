"""
OpenAI SDK LLM client.

JSON output strategy (no response_format — GPT-OSS compatible):
  1) Prompt guard: Pydantic JSON Schema injected into system prompt
  2) extract_json(): 3-stage fallback parser (raw → code fence → brace scan)
  3) Retry: feed previous response back as assistant message for JSON-only retry
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from typing import Any, Dict, Optional, Type, TypeVar

from openai import OpenAI, APITimeoutError, APIStatusError
from pydantic import BaseModel

from .context_guard import (
    BUDGET_BYTES, measure_messages_bytes, ContextBudgetExceeded,
)
from .logger import psub, count_llm, get_llm_count

T = TypeVar("T", bound=BaseModel)

# ──────────────────────────────────────────────────────────────────────────────
# Connection — env vars or hardcode below
# ──────────────────────────────────────────────────────────────────────────────
OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL") or None
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-oss-20b")

# ──────────────────────────────────────────────────────────────────────────────
# Auth headers (injected via OpenAI default_headers)
# ──────────────────────────────────────────────────────────────────────────────
# Uncomment and fill, or use OPENAI_EXTRA_HEADERS env var (JSON string).
DEFAULT_HEADERS: Dict[str, str] = {
    # "Authorization": "Bearer <YOUR_TOKEN>",
}
# Merge from env
_extra = os.getenv("OPENAI_EXTRA_HEADERS")
if _extra:
    try:
        DEFAULT_HEADERS.update(json.loads(_extra))
    except json.JSONDecodeError:
        print(f"[WARN] OPENAI_EXTRA_HEADERS JSON 파싱 실패: {_extra}")

# ──────────────────────────────────────────────────────────────────────────────
# Rate Limiting
# ──────────────────────────────────────────────────────────────────────────────
LLM_MAX_RPM: int = int(os.getenv("LLM_MAX_RPM", "12"))
LLM_MAX_CONCURRENT: int = int(os.getenv("LLM_MAX_CONCURRENT", "5"))

# ──────────────────────────────────────────────────────────────────────────────
# Per-role model override
# ──────────────────────────────────────────────────────────────────────────────
_ROLE_ENV_MAP: Dict[str, str] = {
    "extractor": "EXTRACTOR_MODEL",
    "judge": "JUDGE_MODEL",
    "writer": "WRITER_MODEL",
}


def get_model(role: str = "default") -> str:
    """노드별 모델 분리 지원. 미설정 시 OPENAI_MODEL 폴백."""
    env_key = _ROLE_ENV_MAP.get(role)
    if env_key and os.getenv(env_key):
        return os.getenv(env_key)  # type: ignore[return-value]
    return OPENAI_MODEL


# ──────────────────────────────────────────────────────────────────────────────
# Rate Limiter — 슬라이딩 윈도우 + 동시 요청 제한
# ──────────────────────────────────────────────────────────────────────────────
class RateLimiter:
    """Thread-safe 슬라이딩 윈도우 rate limiter + concurrency semaphore.

    Send API 병렬 디스패치 환경에서 LLM 호출 속도를 제어한다.
    context manager 로 사용:
        with _rate_limiter:
            response = client.chat.completions.create(...)
    """

    def __init__(self, max_per_minute: int, max_concurrent: int):
        self._max_rpm = max_per_minute
        self._window = 60.0
        self._timestamps: deque = deque()
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(max_concurrent)

    def __enter__(self):
        # 1) 동시 요청 상한 대기
        self._semaphore.acquire()
        # 2) 분당 호출 한도 대기
        while True:
            with self._lock:
                now = time.monotonic()
                # 윈도우 밖 타임스탬프 제거
                while self._timestamps and self._timestamps[0] <= now - self._window:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._max_rpm:
                    self._timestamps.append(now)
                    return self
                # 가장 오래된 호출이 윈도우를 벗어날 때까지 대기
                wait = self._timestamps[0] + self._window - now
            # Lock 해제 후 sleep (다른 스레드 차단 방지)
            if wait > 0:
                psub("rate_limiter", f"{self._max_rpm}/min 한도 도달 — {wait:.1f}s 대기 (LLM #{get_llm_count()})")
                time.sleep(wait + 0.1)

    def __exit__(self, *exc):
        self._semaphore.release()
        return False

    def __repr__(self) -> str:
        return f"RateLimiter(rpm={self._max_rpm}, concurrent={self._semaphore._value})"


_rate_limiter = RateLimiter(LLM_MAX_RPM, LLM_MAX_CONCURRENT)


# ──────────────────────────────────────────────────────────────────────────────
# OpenAI 클라이언트
# ──────────────────────────────────────────────────────────────────────────────
def _make_client() -> OpenAI:
    kwargs: Dict[str, Any] = {
        "api_key": "unused",  # 인증은 DEFAULT_HEADERS 로 처리; SDK 필수 인자 충족용
    }
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL
    if DEFAULT_HEADERS:
        kwargs["default_headers"] = DEFAULT_HEADERS
    return OpenAI(**kwargs)


_client: OpenAI = _make_client()


# ──────────────────────────────────────────────────────────────────────────────
# JSON Extractor — GPT-OSS 응답에서 JSON 강제 추출
# ──────────────────────────────────────────────────────────────────────────────
# GPT-OSS 계열은 `response_format={"type":"json_object"}` 인자를 지원하지 않음.
# 따라서 모델 응답에 코드펜스/설명문/잡담이 섞일 수 있으며,
# 아래 extractor 가 다단계 폴백으로 첫 번째 유효 JSON 객체를 추출한다.
#
# 폴백 순서:
#   1) 그대로 json.loads
#   2) ```json ... ``` 또는 ``` ... ``` 코드펜스 내부
#   3) 중괄호 균형 스캔으로 첫 최상위 {...} 블록 추출
#   4) 모두 실패 시 ValueError
_CODE_FENCE = re.compile(
    r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE
)


def _scan_balanced_json(text: str) -> Optional[str]:
    """중괄호/대괄호 균형 스캔으로 첫 최상위 JSON 블록 추출."""
    start = -1
    open_ch = ""
    close_ch = ""
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            open_ch = ch
            close_ch = "}" if ch == "{" else "]"
            break
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(text: str, *, expect: str = "object") -> Any:
    """GPT-OSS 응답에서 JSON 추출. expect='object' | 'array'.
    실패 시 ValueError(원문 일부 포함) 발생.
    """
    if text is None:
        raise ValueError("LLM 응답이 None")
    text = text.strip()
    if not text:
        raise ValueError("LLM 응답이 빈 문자열")

    # 1) 그대로 파싱
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) 코드펜스
    m = _CODE_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3) 균형 스캔
    block = _scan_balanced_json(text)
    if block:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass

    preview = text[:300].replace("\n", "\\n")
    raise ValueError(
        f"LLM 응답에서 JSON 추출 실패 (expect={expect}): {preview!r}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# structured_call — Pydantic 강제 + GPT-OSS 호환 JSON 강제 + Rate Limiting
# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# Output token limit + 504 adaptive reduction
# ──────────────────────────────────────────────────────────────────────────────
# 95KB = 97,280 bytes. At ~4 bytes/token (English), 24,000 tokens ≈ 93.8KB.
MAX_COMPLETION_TOKENS: int = 24_000

# 504 reduction: 5KB per step (≈ 1,280 tokens at 4 bytes/token)
_504_REDUCE_BYTES: int = 5 * 1024
_504_REDUCE_TOKENS: int = 1_280
_504_MAX_STEPS: int = 10           # max 50KB total reduction
_504_MIN_BUDGET: int = 10 * 1024   # never go below 10KB
_504_MIN_TOKENS: int = 1_024       # never go below 1,024 tokens

# Persistent offsets — reduced on 504, persist across calls within a pipeline run.
# This implements "previous step regression": once budget is reduced,
# ALL subsequent LLM calls in the pipeline use the reduced limits.
_504_token_offset: int = 0
_504_budget_offset: int = 0


def reset_504_state() -> None:
    """Reset 504 reduction offsets. Call once at pipeline start."""
    global _504_token_offset, _504_budget_offset
    _504_token_offset = 0
    _504_budget_offset = 0


def _is_504(e: Exception) -> bool:
    """Detect 504 Gateway Timeout or request timeout."""
    if isinstance(e, APITimeoutError):
        return True
    if isinstance(e, APIStatusError) and e.status_code == 504:
        return True
    return False


def _reduce_504() -> None:
    """Globally reduce budget + tokens by 5KB. Persists for all subsequent calls."""
    global _504_token_offset, _504_budget_offset
    _504_token_offset += _504_REDUCE_TOKENS
    _504_budget_offset += _504_REDUCE_BYTES


def effective_budget() -> int:
    """Current effective input budget (BUDGET_BYTES minus 504 reductions)."""
    return max(BUDGET_BYTES - _504_budget_offset, _504_MIN_BUDGET)


def effective_max_tokens(base: int = MAX_COMPLETION_TOKENS) -> int:
    """Current effective output token limit (base minus 504 reductions)."""
    return max(base - _504_token_offset, _504_MIN_TOKENS)


def _trim_longest_user_msg(messages: list, reduce_bytes: int) -> None:
    """Truncate the longest user message content by reduce_bytes."""
    max_idx, max_len = -1, 0
    for i, m in enumerate(messages):
        if m["role"] == "user":
            blen = len(m["content"].encode("utf-8"))
            if blen > max_len:
                max_len = blen
                max_idx = i
    if max_idx >= 0 and max_len > reduce_bytes + 256:
        raw = messages[max_idx]["content"].encode("utf-8")
        cut = max(len(raw) - reduce_bytes, 256)
        messages[max_idx]["content"] = (
            raw[:cut].decode("utf-8", errors="ignore") + "\n[TRUNCATED]"
        )


def structured_call(
    messages: list,
    response_model: Type[T],
    role: str = "default",
    temperature: float = 0.0,
    max_retries: int = 3,
    stream: bool = False,
    reasoning_effort: str = "high",
    max_tokens: int = MAX_COMPLETION_TOKENS,
) -> T:
    """GPT-OSS compatible Pydantic-enforced LLM call with 504 adaptive reduction.

    On 504 timeout:
      1. Globally reduce input budget AND output tokens by 5KB.
      2. Trim the longest user message to fit the new budget.
      3. Retry (up to 10 additional 504-specific retries).
      4. Reductions persist across ALL subsequent calls in the pipeline run
         (\"previous step regression\").
    """
    model = get_model(role)
    schema = response_model.model_json_schema()

    json_guard = (
        "\n\n[OUTPUT PROTOCOL — MANDATORY]\n"
        "- Respond with exactly one JSON object only.\n"
        "- No code fences (```), explanations, preambles, postambles, or chain-of-thought.\n"
        "- The first character of your response MUST be '{'.\n"
        "- Use double quotes for all keys and strings. No trailing commas.\n"
        f"\n[JSON Schema — follow this structure exactly]\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )

    work_messages = list(messages)
    if work_messages and work_messages[0]["role"] == "system":
        work_messages[0] = {
            "role": "system",
            "content": work_messages[0]["content"] + json_guard,
        }
    else:
        work_messages.insert(0, {"role": "system", "content": json_guard})

    last_err: Optional[Exception] = None
    last_raw: str = ""
    json_attempts: int = 0
    _504_attempts: int = 0

    while True:
        # Current effective limits (may have been reduced by prior 504s)
        eff_budget = effective_budget()
        eff_tokens = effective_max_tokens(max_tokens)

        try:
            # ── Budget hard limit ──
            payload_bytes = measure_messages_bytes(work_messages)
            if payload_bytes > eff_budget:
                # Remove oldest retry pairs first
                while len(work_messages) > 3 and payload_bytes > eff_budget:
                    if (
                        len(work_messages) >= 5
                        and work_messages[2]["role"] == "assistant"
                    ):
                        del work_messages[2:4]
                        payload_bytes = measure_messages_bytes(work_messages)
                    else:
                        break
                # Still over: truncate longest user message
                if payload_bytes > eff_budget:
                    excess = payload_bytes - eff_budget + 512
                    _trim_longest_user_msg(work_messages, excess)
                    payload_bytes = measure_messages_bytes(work_messages)
                if payload_bytes > eff_budget:
                    raise ContextBudgetExceeded(payload_bytes, eff_budget)
                psub("structured_call", f"trimmed to {payload_bytes/1024:.1f}KB (budget {eff_budget//1024}KB)")

            with _rate_limiter:
                if stream:
                    response_stream = _client.chat.completions.create(
                        model=model,
                        messages=work_messages,
                        temperature=temperature,
                        reasoning_effort=reasoning_effort,
                        max_tokens=eff_tokens,
                        stream=True,
                    )
                    _chunks: list = []
                    for chunk in response_stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            _chunks.append(chunk.choices[0].delta.content)
                    last_raw = "".join(_chunks)
                    count_llm()
                else:
                    response = _client.chat.completions.create(
                        model=model,
                        messages=work_messages,
                        temperature=temperature,
                        reasoning_effort=reasoning_effort,
                        max_tokens=eff_tokens,
                    )
                    last_raw = response.choices[0].message.content or ""
                    count_llm()

            parsed_dict = extract_json(last_raw, expect="object")
            return response_model.model_validate(parsed_dict)

        except Exception as e:
            # ── 504 Timeout: reduce by 5KB and retry ──
            if _is_504(e) and _504_attempts < _504_MAX_STEPS:
                _504_attempts += 1
                _reduce_504()
                new_budget = effective_budget()
                new_tokens = effective_max_tokens(max_tokens)
                psub("structured_call",
                     f"504 → 5KB 감축 ({_504_attempts}/{_504_MAX_STEPS}): "
                     f"budget {new_budget//1024}KB, tokens {new_tokens}")
                # Trim input to fit new budget
                _trim_longest_user_msg(work_messages, _504_REDUCE_BYTES)
                continue

            # ── JSON parse failure: standard retry ──
            if not _is_504(e) and json_attempts < max_retries:
                json_attempts += 1
                last_err = e
                psub("structured_call",
                     f"retry {json_attempts}/{max_retries} — {type(e).__name__}: {e}")
                work_messages.append({"role": "assistant", "content": last_raw})
                work_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The previous response was not valid JSON or did not match the schema. "
                            "Perform the same task again, outputting exactly one JSON object only. "
                            "No code fences, explanations, or preambles. "
                            f"Schema: {json.dumps(schema, ensure_ascii=False)}"
                        ),
                    }
                )
                continue

            # ── All retries exhausted ──
            last_err = e
            break

    raise RuntimeError(
        f"structured_call failed (json_retries={json_attempts}, "
        f"504_retries={_504_attempts}, budget={effective_budget()//1024}KB, "
        f"tokens={effective_max_tokens(max_tokens)}): {last_err}"
    )
