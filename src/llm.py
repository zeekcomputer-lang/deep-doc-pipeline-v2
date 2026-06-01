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

from openai import OpenAI
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
def structured_call(
    messages: list,
    response_model: Type[T],
    role: str = "default",
    temperature: float = 0.0,
    max_retries: int = 3,
    stream: bool = False,
    reasoning_effort: str = "high",
) -> T:
    """GPT-OSS 호환 Pydantic 강제 LLM 호출.

    전략:
      - response_format 인자를 사용하지 않음 (GPT-OSS 미지원).
      - Pydantic JSON Schema + 출력 규약을 system 프롬프트에 첨부하여 JSON 응답 유도.
      - 응답은 extract_json() 3단 파서로 추출 후 Pydantic model_validate.
      - 파싱/검증 실패 시 직전 응답을 assistant 메시지로 넘겨 "JSON만 다시 출력" 재요청.
      - 모든 API 호출은 _rate_limiter 를 통해 분당/동시 호출 수 제한.
      - stream=True: SSE 스트리밍으로 첫 토큰 즉시 수신 → 게이트웨이 504 타임아웃 방지.
      - 모든 호출 전 95KB 예산 하드리밋 적용 (retry 누적 시 자동 트리밍).
    """
    model = get_model(role)
    schema = response_model.model_json_schema()

    # 프롬프트 가드: JSON Schema 힌트 + 출력 규약
    json_guard = (
        "\n\n[OUTPUT PROTOCOL — MANDATORY]\n"
        "- Respond with exactly one JSON object only.\n"
        "- No code fences (```), explanations, preambles, postambles, or chain-of-thought.\n"
        "- The first character of your response MUST be '{'.\n"
        "- Use double quotes for all keys and strings. No trailing commas.\n"
        # English enforcement is handled per-node via _EN_ENFORCE, not here.
        # The translation/rendering stage outputs Korean, so this guard stays language-neutral.
        f"\n[JSON Schema — follow this structure exactly]\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )

    # 작업용 메시지 목록 (원본 변경 방지)
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

    for attempt in range(max_retries):
        try:
            # ── 컨텍스트 예산 하드리밋 ──
            payload_bytes = measure_messages_bytes(work_messages)
            if payload_bytes > BUDGET_BYTES:
                # Retry 누적으로 인한 초과: 가장 오래된 retry 쌍 제거
                while (
                    len(work_messages) > 3
                    and payload_bytes > BUDGET_BYTES
                ):
                    # index 2,3 = 가장 오래된 (assistant, user) retry 쌍
                    if (
                        len(work_messages) >= 5
                        and work_messages[2]["role"] == "assistant"
                    ):
                        del work_messages[2:4]
                        payload_bytes = measure_messages_bytes(work_messages)
                    else:
                        break
                if payload_bytes > BUDGET_BYTES:
                    raise ContextBudgetExceeded(payload_bytes, BUDGET_BYTES)
                psub("structured_call", f"retry 컨텍스트 트리밍 후 {payload_bytes/1024:.1f}KB")

            with _rate_limiter:
                if stream:
                    # SSE 스트리밍: 첫 토큰이 도달하면 게이트웨이 read_timeout 리셋
                    response_stream = _client.chat.completions.create(
                        model=model,
                        messages=work_messages,
                        temperature=temperature,
                        reasoning_effort=reasoning_effort,
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
                    )
                    last_raw = response.choices[0].message.content or ""
                    count_llm()

            # extract_json → Pydantic 검증
            parsed_dict = extract_json(last_raw, expect="object")
            return response_model.model_validate(parsed_dict)

        except Exception as e:
            last_err = e
            psub("structured_call", f"retry {attempt + 1}/{max_retries} — {type(e).__name__}: {e}")
            # 재시도: 직전 응답을 보여주고 JSON-only 재요청
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

    raise RuntimeError(
        f"structured_call failed after {max_retries} attempts: {last_err}"
    )
