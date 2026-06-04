# Deep Doc Pipeline (v2.0)

JSONL 문서 → 영문 분석·집필 → 한국어 백서 자동 생성 파이프라인.
LangGraph + OpenAI SDK + Pydantic 강제 출력.

## 문서

- **[`HANDOFF.md`](./HANDOFF.md)** — 인수인계 (아키텍처·방어 기제·시나리오)
- **[`SPEC.md`](./SPEC.md)** — 설계 명세서 (v1.1 원본)
- **[`LESSONS.md`](./LESSONS.md)** — 누적 교훈 (L-001~L-020)

## 구조

```
├── main.py                  실행 진입점
├── data/records.jsonl       입력 JSONL (gen_dummy.py로 생성)
├── scripts/
│   ├── gen_dummy.py         더미 데이터 생성기
│   └── md_to_docx.py       마크다운 → DOCX 변환
└── src/
    ├── schemas.py           Pydantic 응답 스키마 (7종)
    ├── state.py             GraphState + reducer
    ├── llm.py               OpenAI SDK 클라이언트 + Rate Limiter
    ├── context_guard.py     95KB 컨텍스트 예산 관리
    ├── logger.py            타임라인 로거 + 실행 통계
    ├── utils.py             Pure Python 결정론 로직
    ├── nodes.py             LangGraph 노드 (17개 + 라우터 5개)
    └── graph.py             그래프 조립
```

## 셋업

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # OPENAI_BASE_URL, OPENAI_MODEL 편집
```

### 인증

`src/llm.py`의 `DEFAULT_HEADERS`에서 필요한 헤더 주석 해제 후 값 입력.
또는 환경변수로 주입:
```bash
export OPENAI_EXTRA_HEADERS='{"Authorization": "Bearer xxx"}'
```

## 실행

```bash
python -m scripts.gen_dummy      # 더미 데이터 생성 (1회)
python -m main                   # 백서 생성 (reasoning=high)
python -m main --reasoning medium  # 서버 타임아웃 회피 우선
python -m main --output out.md   # 출력 경로 지정
```

### 출력 예시

```
[00:00] #1   [load_docs] loaded=15 failed=0
[00:03] #2   [chrono_sorter] events=15 months=["2026-02", ...]
[00:12]      [rate_limiter] 12/min 한도 도달 — 4.2s 대기 (LLM #8)
[01:20] #16  [translate] [2026-03] full-section OK (en=2400 kr=1850 ratio=0.77)
[01:34] #17  [translate] v2.0 complete: years=["2026"] en_chars=12000 kr_chars=9200 ratio=0.77

======================================================================
✅ 파이프라인 완료
======================================================================
  총 소요 시간 : 1분 34초
  완료 작업 수 : 17건
  LLM API 호출 : 22건
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|-------|------|
| `OPENAI_BASE_URL` | — | LLM 엔드포인트 URL |
| `OPENAI_MODEL` | gpt-oss-20b | 기본 모델 |
| `EXTRACTOR_MODEL` | (OPENAI_MODEL) | 추출 전용 모델 |
| `JUDGE_MODEL` | (OPENAI_MODEL) | 팩트체크 전용 모델 |
| `WRITER_MODEL` | (OPENAI_MODEL) | 집필 전용 모델 |
| `LLM_MAX_RPM` | 12 | 분당 최대 호출 수 |
| `LLM_MAX_CONCURRENT` | 5 | 동시 호출 상한 |
| `LLM_CONTEXT_BUDGET_KB` | 95 | per-call 컨텍스트 예산 (KB) |

## 출력 구조

```bash
python -m main --output report.md
# → report.md      한글 백서
# → report_en.md   영문 원본 (항상 생성)

# DOCX 변환 (개별 구동)
python scripts/md_to_docx.py report.md                    # → report.docx
python scripts/md_to_docx.py report.md -o whitepaper.docx  # 출력명 지정
python scripts/md_to_docx.py report.md report_en.md        # 복수 파일 병합
```

## 핵심 방어 기제

| 위험 | 방어 |
|------|------|
| Fact-checker 회귀 | `previous_draft` + `hallucinated_tokens` 블랙리스트 주입 |
| Fail-Safe 강제통과 | ⚠️ 워터마크 삽입 + `unverified_sections` 감사 로그 |
| 번역 콘텐츠 소실 | 섹션별 번역 + 완전성검증(ratio≥0.35) + 문단분할 + 소스데이터 폴백 |
| 고유명사 보존 | `extract_proper_nouns` → 번역 프롬프트에 목록 주입 |
| 504 타임아웃 | 국부 감축(-5KB/step) + 노드 재실행 + 성공 후 원복. user 메시지 절단 금지 |
