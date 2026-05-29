"""
순수 OpenAI SDK 클라이언트 (LangChain 래퍼 금지).
GPT-OSS 등 OpenAI 호환 엔드포인트 지원.

인증: HTTP 헤더 기반 (DEFAULT_HEADERS). API Key 환경변수 미사용.
호출 제한: 슬라이딩 윈도우 Rate Limiter + 동시 요청 Semaphore.

JSON 강제 전략 (response_format 미사용 — GPT-OSS 미지원):
  1) 프롬프트 가드: Pydantic JSON Schema + 출력 규약 자동 첨부
  2) extract_json(): 3단 폴백 파서 (raw → 코드펜스 → 균형 스캔)
  3) 재시도: 직전 응답을 assistant 메시지로 넘겨 "JSON만 다시 출력" 재요청
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

T = TypeVar("T", bound=BaseModel)

# ──────────────────────────────────────────────────────────────────────────────
# 접속 설정 — HARDCODE placeholder 패턴
# ──────────────────────────────────────────────────────────────────────────────
# 환경변수(OPENAI_BASE_URL / OPENAI_MODEL) 가 설정되어 있으면 그 값이 우선.
# 아래 None / 빈 문자열을 실제 값으로 바꾸면 하드코딩 완료.
# 인증은 DEFAULT_HEADERS 의 토큰 헤더로 처리 (API Key 환경변수 미사용).

# 예) OPENAI_BASE_URL = "https://your-internal-gateway.example.com/v1"
# 예) OPENAI_BASE_URL = "http://localhost:8000/v1"
OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL") or None  # <-- HARDCODE

# 예) OPENAI_MODEL = "gpt-oss-20b"  /  "gpt-oss-120b"  /  "내부-모델-id"
# 본 파이프라인은 GPT-OSS(오픈소스/로컬) 모델 사용을 전제로 함.
# → response_format 인자 미지원. JSON 출력은 프롬프트 + 파서로 강제.
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-oss-20b")  # <-- HARDCODE

# ──────────────────────────────────────────────────────────────────────────────
# 인증 헤더 (OpenAI default_headers 로 주입됨)
# ──────────────────────────────────────────────────────────────────────────────
# [HARDCODE HERE] 아래 dict 의 주석을 해제하고 실제 헤더 값으로 교체하세요.
# 환경변수 OPENAI_EXTRA_HEADERS (JSON 문자열) 를 추가로 병합할 수 있습니다.
DEFAULT_HEADERS: Dict[str, str] = {
    # "Authorization":   "Bearer <YOUR_TOKEN_HERE>",          # 인증 토큰
    # "X-API-Key":       "<YOUR_API_KEY_HERE>",               # 게이트웨이 API Key
    # "X-Project-Id":    "<YOUR_PROJECT_ID>",                 # 프로젝트 식별
    # "X-Tenant":        "<YOUR_TENANT_ID>",                  # 멀티 테넌트
    # "X-Request-Source": "deep-doc-pipeline",                # 호출 출처 태깅
}
# 환경변수 병합 (선택)
_extra = os.getenv("OPENAI_EXTRA_HEADERS")
if _extra:
    try:
        DEFAULT_HEADERS.update(json.loads(_extra))
    except json.JSONDecodeError:
        print(f"[WARN] OPENAI_EXTRA_HEADERS JSON 파싱 실패: {_extra}")

# ──────────────────────────────────────────────────────────────────────────────
# 호출 제한 (Rate Limiting)
# ──────────────────────────────────────────────────────────────────────────────
# 200건 이상 데이터 처리 시 Send 병렬 디스패치로 API 폭주를 방지.
# LLM_MAX_RPM: 분당 최대 호출 수 (기본 12 → 실측 10~15/min 범위)
# LLM_MAX_CONCURRENT: 동시 호출 상한 (스레드 자원 보호)
LLM_MAX_RPM: int = int(os.getenv("LLM_MAX_RPM", "12"))
LLM_MAX_CONCURRENT: int = int(os.getenv("LLM_MAX_CONCURRENT", "5"))

# ──────────────────────────────────────────────────────────────────────────────
# 역할별 모델 분리 (deep-doc-pipeline 고유 요구사항)
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
                print(f"  [rate_limiter] {self._max_rpm}/min 한도 도달 — {wait:.1f}s 대기")
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
) -> T:
    """GPT-OSS 호환 Pydantic 강제 LLM 호출.

    전략:
      - response_format 인자를 사용하지 않음 (GPT-OSS 미지원).
      - Pydantic JSON Schema + 출력 규약을 system 프롬프트에 첨부하여 JSON 응답 유도.
      - 응답은 extract_json() 3단 파서로 추출 후 Pydantic model_validate.
      - 파싱/검증 실패 시 직전 응답을 assistant 메시지로 넘겨 "JSON만 다시 출력" 재요청.
      - 모든 API 호출은 _rate_limiter 를 통해 분당/동시 호출 수 제한.
    """
    model = get_model(role)
    schema = response_model.model_json_schema()

    # 프롬프트 가드: JSON Schema 힌트 + 출력 규약
    json_guard = (
        "\n\n[출력 규약 — 엄수]\n"
        "- 응답은 오직 하나의 JSON object 만 출력한다.\n"
        "- 코드펜스(```), 설명, 머리말/꼬리말, 사고 과정 출력 금지.\n"
        "- 응답의 첫 문자는 '{' 이어야 한다.\n"
        "- 키/문자열은 모두 큰따옴표 사용. 후행 콤마 금지.\n"
        f"\n[JSON Schema — 이 구조를 정확히 따르라]\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
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
            with _rate_limiter:
                response = _client.chat.completions.create(
                    model=model,
                    messages=work_messages,
                    temperature=temperature,
                )
            last_raw = response.choices[0].message.content or ""

            # extract_json → Pydantic 검증
            parsed_dict = extract_json(last_raw, expect="object")
            return response_model.model_validate(parsed_dict)

        except Exception as e:
            last_err = e
            print(
                f"  [structured_call][retry {attempt + 1}/{max_retries}] "
                f"{type(e).__name__}: {e}"
            )
            # 재시도: 직전 응답을 보여주고 JSON-only 재요청
            work_messages.append({"role": "assistant", "content": last_raw})
            work_messages.append(
                {
                    "role": "user",
                    "content": (
                        "직전 응답은 유효한 JSON 이 아니거나 스키마에 맞지 않는다. "
                        "동일 작업을 다시 수행하되, 오직 하나의 JSON object 만 출력하라. "
                        "코드펜스/설명/머리말 모두 금지. "
                        f"스키마: {json.dumps(schema, ensure_ascii=False)}"
                    ),
                }
            )

    raise RuntimeError(
        f"structured_call failed after {max_retries} attempts: {last_err}"
    )
