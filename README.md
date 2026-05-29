# Deep Doc Pipeline (v1.1)

저성능 모델 극복형 초정밀 심층 문서 분석 파이프라인. LangGraph + 순수 OpenAI SDK + Pydantic 강제.

## 문서

- **[`SPEC.md`](./SPEC.md)** — 설계 명세서 (v1.1, 단일 진실 공급원)
- **[`HANDOFF.md`](./HANDOFF.md)** — 다음 AI Agent 인수인계 문서
- **[`LESSONS.md`](./LESSONS.md)** — 누적 교훈 인덱스 (L-001~L-009)

## 구조

```
projects/deep-doc-pipeline/
├── SPEC.md                    # 설계 명세서 (v1.1)
├── README.md                  # 이 문서
├── requirements.txt           # 의존성
├── .env.example               # 환경변수 템플릿
├── main.py                    # 실행 진입점
├── data/
│   └── records.jsonl          # 입력 데이터 (gen_dummy.py로 생성)
├── scripts/
│   └── gen_dummy.py           # 더미 JSONL 15줄 생성기
└── src/
    ├── schemas.py             # Pydantic 응답 스키마
    ├── state.py               # GraphState + reducer
    ├── llm.py                 # 순수 OpenAI SDK 클라이언트 (parse + fallback)
    ├── utils.py               # Pure Python 결정론 로직
    ├── nodes.py               # LangGraph 노드 함수
    └── graph.py               # 그래프 조립
```

## 사전 준비

```bash
cd projects/deep-doc-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env 편집: OPENAI_BASE_URL, OPENAI_MODEL
# 인증 헤더: src/llm.py DEFAULT_HEADERS 의 placeholder 교체
```

### 인증

API Key 환경변수가 아닌 **HTTP 헤더**로 인증합니다.
`src/llm.py`의 `DEFAULT_HEADERS` dict에서 필요한 헤더의 주석을 해제하고 실제 값으로 교체하세요.

```python
# src/llm.py 내
DEFAULT_HEADERS = {
    "Authorization": "Bearer <YOUR_TOKEN_HERE>",
    # ...
}
```

또는 환경변수 `OPENAI_EXTRA_HEADERS`로 JSON 주입:
```bash
export OPENAI_EXTRA_HEADERS='{"Authorization": "Bearer xxx"}'
```

### gpt-oss 엔드포인트 예시

**Ollama (로컬):**
```bash
ollama pull gpt-oss:20b
# .env:
#   OPENAI_BASE_URL=http://localhost:11434/v1
#   OPENAI_MODEL=gpt-oss:20b
```

**vLLM:**
```bash
vllm serve openai/gpt-oss-20b --port 8000
# .env:
#   OPENAI_BASE_URL=http://localhost:8000/v1
#   OPENAI_MODEL=openai/gpt-oss-20b
```

### 호출 제한 (Rate Limiting)

200건 이상 데이터 처리 시 `Send` 병렬 디스패치로 인한 API 폭주를 방지합니다.

| 환경변수 | 기본값 | 설명 |
|---------|-------|------|
| `LLM_MAX_RPM` | 12 | 분당 최대 호출 수 (10~15 권장) |
| `LLM_MAX_CONCURRENT` | 5 | 동시 호출 상한 (스레드 자원 보호) |
| `LLM_CONTEXT_BUDGET_KB` | 95 | per-call 컨텍스트 예산 (KB) |

슬라이딩 윈도우 방식으로 60초 내 호출 수를 제한하며, Semaphore로 동시 요청 수를 추가 제어합니다.
한도 도달 시 자동 대기하므로 품질에는 영향 없으며, 처리 시간만 비례 증가합니다.

## 실행

```bash
# 1) 더미 데이터 생성 (records.jsonl 없을 때 1회)
python -m scripts.gen_dummy

# 2) 백서 생성 (자가 검증 루프 가시화)
python -m main --format whitepaper

# 3) 현황판 생성
python -m main --format status_report
```

## v1.1 핵심 방어 기제

| 위험 | 방어 |
|------|------|
| Fact-checker 회귀 (동일 환각 반복) | `previous_draft` + `hallucinated_tokens` 명시 주입 |
| Fail-Safe 강제통과 무표시 | 워터마크 자동 삽입 + `unverified_sections` 감사 로그 |
| Compiler 윤문 환각 재주입 | `compiler`(Python) → `polish`(LLM, 사실 변경 금지) → `final_fact_checker`(2차 검증) 분리 |

## 로그로 보이는 동작

- `[fact_checker] idx=2 approved=False halluc=['...']` — 환각 감지
- `[section_writer] idx=2 ... retry=1` — Negative Example 주입 재작성
- `[save_section_with_warning] idx=4 FORCE-PASS` — Fail-Safe 강제통과
- `[fallback_to_compiled]` — polish 2차 검증 실패 시 조립본 채택

## 알려진 제약

- gpt-oss는 OpenAI `response_format=json_schema` 호환을 권장하나, 엔진(Ollama 버전 등) 미지원 시 `llm.py`의 JSON 모드 fallback으로 자동 전환.
- 200건 입력 시 API 호출 약 `200(추출) + M(월별) + 1(테마) + (K × 평균 2.x 재시도)` 회 예상. 노드별 모델 분리 권장.
- Rate Limiter(기본 12 RPM)로 인해 200건 기준 전체 처리에 약 25~40분 소요. `LLM_MAX_RPM` 조정 가능.
