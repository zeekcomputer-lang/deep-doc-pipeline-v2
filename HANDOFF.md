# HANDOFF.md — 다음 AI Agent 인수인계 문서

> **프로젝트:** deep-doc-pipeline (v1.1)
> **GitHub:** https://github.com/zeekcomputer-lang/deep-doc-pipeline (PUBLIC)
> **로컬:** `~/.openclaw/workspace/projects/deep-doc-pipeline/`
> **최종 업데이트:** 2026-05-29
> **상태:** 코드 작성 완료 + 95KB 컨텍스트 예산 가드 전면 적용 / 실행 검증 보류 (사용자 환경 예정)

---

## §0. 30초 요약

저성능 LLM(gpt-oss 등) 환경에서 **환각을 구조적으로 차단**하며 200건 JSONL → 백서/현황판을 자동 생성하는 **LangGraph 파이프라인**. 순수 OpenAI SDK + Pydantic 강제 출력 + 다단 자가검증 루프 + Fail-Safe 워터마크. 11 파일 / ~1700줄. **AST 문법 검증 통과 / 실제 LLM 실행은 미수행**.

**v1.1-r1 (2026-05-29):** `src/llm.py`를 GPT-OSS placeholder 표준으로 리팩터 — `beta.parse` / `response_format` 전면 제거, `extract_json()` 3단 파서 도입, HARDCODE placeholder 패턴 적용. 표준 출처: `langgraph-excel-categorizer/categorizer.py`.

**v1.1-r2 (2026-05-29):** Rate Limiter 도입 + API Key 환경변수 제거 + 헤더 기반 인증 전환.
- `RateLimiter` 클래스: 슬라이딩 윈도우(60s) + Semaphore 이중 제어
- 기본 12 RPM / 5 동시 호출. 환경변수 `LLM_MAX_RPM`, `LLM_MAX_CONCURRENT` 조정 가능
- `OPENAI_API_KEY` 환경변수 제거, `DEFAULT_HEADERS` placeholder로 토큰 전달

다음 작업자가 가장 먼저 할 것:
1. `SPEC.md` §1~6 통독 (15분)
2. `src/nodes.py` 헤더 주석으로 노드 매핑 파악 (10분)
3. 사용자가 gpt-oss 엔드포인트 기동 후 실행 결과 보고 시 디버깅 착수

---

## §1. 파일 지도

```
projects/deep-doc-pipeline/
├── SPEC.md                ★ 설계 명세서 v1.1 (314줄, 단일 진실 공급원)
├── README.md              실행 가이드 + gpt-oss 엔드포인트 예시
├── HANDOFF.md             ★ 이 문서
├── requirements.txt       openai>=1.50, langgraph>=0.2.40, pydantic>=2.7, python-dotenv
├── .env.example           OPENAI_BASE_URL / OPENAI_MODEL / LLM_MAX_RPM
├── .gitignore             Python 캐시, .env, output.md 제외
├── main.py                argparse + recursion_limit=200 + graph.invoke
├── data/
│   └── records.jsonl      15건 / 4개월 (2026-02 ~ 2026-05) 더미
├── scripts/
│   └── gen_dummy.py       시드 고정(42) 결정론적 생성기
└── src/
    ├── schemas.py         7종 Pydantic (hallucinated_terms 강제 포함)
    ├── state.py           GraphState + 3개 reducer (update_dict, operator.add ×2)
    ├── context_guard.py   ★ 95KB 예산 관리 (측정/분할/교차검증)
    ├── llm.py             ★ GPT-OSS 표준 + Rate Limiter + 헤더 인증 + 예산 하드리밋
    ├── utils.py           Pure Python (sort/filter/compile/validate/split)
    ├── nodes.py           15개 노드 + 4개 라우터 (polish/fact_checker 청크드+스트리밍)
    └── graph.py           LangGraph 조립 (Send 병렬 2곳)
```

---

## §2. v1.1 핵심 방어 기제 (반드시 숙지)

원본 명세서(v1.0)에서 **구조적 위험 3건**을 발견하여 v1.1로 보강한 것이 이 프로젝트의 핵심.

### 위험 1: Fact-checker 회귀 (Re-hallucination)
- **문제:** Fail 시 재작성하지만 모델은 직전 환각을 기억 못 하므로 동일 환각 반복 생성.
- **방어:**
  - `state.previous_draft` — 반려된 직전 초안을 Negative Example로 명시 주입
  - `state.hallucinated_tokens` — 누적 reducer로 환각 토큰 블랙리스트 관리
  - `schemas.FactCheckResult.hallucinated_terms` — 필수 필드, 팩트체커가 정확한 토큰 추출 강제
  - `nodes.section_writer_node` — `retry > 0`이면 이 3개를 프롬프트에 명시 주입

### 위험 2: Fail-Safe 강제통과 무표시
- **문제:** `retry_count >= 3` 시 환각 섞인 초안이 무표시로 최종 백서 편입.
- **방어:**
  - `nodes.save_section_with_warning_node` — 워터마크 자동 삽입
  - `state.unverified_sections` — 누적 reducer로 감사 로그
  - `utils.compile_sections` — 최종 결과 하단에 검증 미완료 섹션 인덱스 자동 출력

### 위험 3: Compiler 윤문 환각 재주입
- **문제:** 검수 통과 섹션을 모은 후 LLM 윤문 단계에서 새 사실 창작.
- **방어:** 단일 노드를 3개로 분리
  - `compiler_node` (Pure Python) — 문자열 조립만, LLM 호출 금지
  - `polish_node` (LLM) — "사실 변경 금지" 명시 프롬프트, 문장 연결만 다듬음
  - `final_fact_checker_node` — 2차 검증, Fail 시 polish 우회하여 `final_compiled` 채택

---

## §3. 그래프 구조 (시각화)

```
START
 │
load_docs ──fanout──▶ strict_extractor (×N, 병렬 Send)
                          │
                  chrono_sorter (Pure Python)
                          │
                  ──fanout──▶ period_summarizer (×M, 병렬 Send)
                                  │
                          theme_analyzer
                                  │
                          [route_by_target]
                          /              \
              status_report           whitepaper
                  │                       │
          status_formatter        draft_planner ◀───┐
                  │                       │         │ Fail
                 END               planner_critique ┘
                                          │ Pass
                                  init_writing
                                          │
                              ┌───────────▼───────────┐
                              │   section_writer ◀──┐ │
                              │       │             │ │
                              │   fact_checker      │ │ Fail (retry<3)
                              │       │             │ │  → previous_draft +
                              │       ├─Pass────┐   │ │    hallucinated_tokens
                              │       │         ▼   │ │    명시 주입
                              │   save_section ─────┘ │
                              │       │               │
                              │       ├─Fail(retry≥3)─┤
                              │       ▼               │
                              │  save_with_warning ───┘
                              │       │ (모든 섹션 완료)
                              └───────▼───────────────┘
                                  compiler (Pure Python 조립)
                                          │
                                  polish (LLM, 사실 변경 금지)
                                          │
                                final_fact_checker
                                  ┌───────┼───────┐
                                Pass    Fail<2  Fail≥2
                                  │   retry_polish  │
                                  │       │         ▼
                                  │       ▼   fallback_to_compiled
                                  │     polish        │
                                  ▼                   ▼
                                 END                 END
```

---

## §4. 절대 준수 사항 (수정 금지)

1. **LangChain LLM 래퍼 사용 금지** — `from langchain.chat_models import ChatOpenAI` 같은 import 절대 추가 금지. 오직 `openai.OpenAI()` 직접 사용.
2. **모든 LLM 응답은 Pydantic 강제** — `structured_call()` → `extract_json()` → `model_validate()` 경로. `response_format` 인자 사용 금지 (GPT-OSS 미지원).
3. **`beta.chat.completions.parse` 사용 금지** — v1.1-r1에서 제거됨. GPT-OSS 호환성을 위해 프롬프트 가드 + 3단 파서로 대체.
4. **Pure Python 영역에 LLM 호출 추가 금지** — `utils.py`, `chrono_sorter_node`, `compiler_node`. 이 4곳은 결정론 영역.
5. **`LOCAL_DATA_PATH` 하드코딩 유지** — `nodes.py` 상단. 변경 시 SPEC.md §2-3 동시 갱신.
6. **LLM 호출 표준 출처** — `langgraph-excel-categorizer/categorizer.py`의 HARDCODE placeholder + `extract_json` + 재시도 패턴이 기준. 신규 LLM 호출 시 이 패턴을 따를 것.

---

## §5. 진행 현황

### ✅ 완료
- [x] SPEC v1.0 → v1.1 (구조적 위험 3건 보강)
- [x] Pydantic 스키마 (7종)
- [x] GraphState (v1.1 필드 4종 포함)
- [x] LLM 클라이언트 (GPT-OSS 표준: extract_json 3단 파서 + Pydantic + 3회 재시도)
- [x] LLM 호출부 GPT-OSS placeholder 표준 정렬 (`442f9ba`, 2026-05-29)
- [x] Rate Limiter 도입 (12 RPM / 5 동시, 환경변수 조정 가능)
- [x] API Key 환경변수 제거 → DEFAULT_HEADERS 헤더 인증 전환
- [x] 504 타임아웃 대응: structured_call stream 파라미터 + 섹션별 분할 윈문/검수 (LESSONS L-011)
- [x] 95KB 컨텍스트 예산 하드리밋: context_guard.py + 전 노드 예산 가드 (LESSONS L-012)
- [x] Pure Python 유틸 (sort/filter/compile/validate)
- [x] LangGraph 노드 15개 + 라우터 4개
- [x] 그래프 조립 (Send 병렬 2곳)
- [x] 더미 데이터 생성기 (15건, 4개월 분포 검증 완료)
- [x] main 진입점 + README
- [x] AST 문법 검증 9/9 통과
- [x] GitHub 업로드 (PUBLIC)

### ⏸️ 보류 (사용자 환경 측)
- [ ] gpt-oss 엔드포인트(Ollama/vLLM) 기동
- [ ] `pip install -r requirements.txt`
- [ ] `python -m main --format whitepaper` 실행
- [ ] **Loop 동작 로그 육안 검증** — fact-checker Fail/Retry 가시화
- [ ] 실행 시 발견되는 이슈 디버깅

### 🟡 미반영 권장 보강 (v1.2 후보)
- [ ] Pure Python 단위 테스트 (pytest, chrono_sorter/context_filter 경계값)
- ~~[ ] 병렬도 제어 (asyncio.Semaphore) — 월수 12+ 환경 대비~~ → v1.1-r2 RateLimiter 로 해결
- ~~[ ] polish/final_fact_checker 대용량 컨텍스트 504~~ → v1.1-r3 Streaming + Section Chunking
- [ ] `failed_docs` 상태 추가 — 3회 추출 실패 문서 추적 (현재는 silent drop)
- [ ] JSONL 무결성 검증 강화 (BOM, 빈 줄 외 케이스)
- [ ] LangGraph SqliteSaver 체크포인트 (Resume 기능)
- [ ] 환각 유발 테스트 시나리오 추가 (의도적으로 모호한 문서 1~2건)

---

## §6. 다음 AI Agent 첫 작업 시나리오

### 시나리오 A: 사용자가 "실행 결과 이상하다"고 보고
1. 사용자 로그 요청 (`[fact_checker] approved=...` 라인 위주)
2. 의심 지점:
   - 모델이 한국어 응답을 거부 → `nodes.py`의 시스템 프롬프트에 한국어 응답 강제 추가
   - `parse` 실패율 높음 → `llm.py` fallback 분기 로그 확인, JSON 모드 미지원 엔진일 가능성
   - recursion_limit 초과 → `main.py`의 200 상향, 또는 outline 길이 제한
   - target_period 불일치 → `planner_critique_node`의 Python 검증 로그 확인

### 시나리오 B: "200건 실제 데이터로 돌리고 싶다"
1. `./data/records.jsonl`로 교체
2. **반드시 노드별 모델 분리 권장** — `.env`의 `EXTRACTOR_MODEL`/`JUDGE_MODEL` 주석 해제
3. 비용 추정: `200(추출) + M(월별) + 1(테마) + K×2.x(집필 평균 재시도) + 3(폴리시 라인)` API 호출
4. Rate Limiter 기본 12 RPM → 200건 기준 전체 약 25~40분 소요. `LLM_MAX_RPM` 환경변수로 조정 가능
4. SqliteSaver 도입 권장 (§5 미반영 보강) — 200건은 1회 실패 시 재실행 비용 큼

### 시나리오 C: "v1.2로 보강해줘"
1. §5 미반영 보강 리스트 중 우선순위 확인
2. SPEC.md 부록 B 패턴 따라 v1.2 변경 요약을 SPEC 헤더에 추가
3. 변경 후 AST 검증 필수: `python3 -c "import ast; [ast.parse(open(f).read()) for f in ...]"`

### 시나리오 D: "다른 도메인에 재활용하고 싶다"
1. 입력 스키마 변경: `schemas.ExtractedEvent`의 `date/issue/action` 3필드를 도메인 필드로 교체
2. `utils.chrono_sort_and_group`은 `date` 필드 의존 — 도메인이 시계열 아니면 그룹핑 키 재설계 필요
3. 프롬프트 한국어 → 영어 전환은 `nodes.py`의 모든 `{"role": "system", "content": ...}` 일괄 교체

---

## §7. 교훈 (Lessons Learned)

### L-001: 명세서 v1.0의 `{a, b}` 오타
- **상황:** 사용자가 제공한 명세서에 `update_dict` 함수의 반환문이 `return {a, b}` (set literal, 실행 불가).
- **대응:** 보정 사항을 SPEC.md 부록 A에 명시 + 실제 코드는 `return {**a, **b}` 적용.
- **교훈:** 사용자 명세서를 그대로 옮기지 말 것. 실행 가능성 사전 검증 후 보정 사실을 부록으로 남길 것.

### L-002: "개선점 제언" 단계의 가치
- **상황:** 1차 명세서 저장 후 "구조적 위험 검토 → 보강" 옵션을 제시했더니 사용자가 즉시 채택. v1.0 그대로 구현했다면 **환각 회귀 / 무표시 통과 / 윤문 재오염** 3건 모두 실전에서 터졌을 위험.
- **교훈:** 코드 작성 전 **명세서 자체의 구조적 결함을 먼저 검토**할 것. "구현은 보류, 검토부터" 패턴은 비용 대비 가치 매우 높음.

### L-003: 사용자 결정의 우선순위
- **상황:** "1. gpt oss / 2. b / 3. 현재 경로" 같이 **번호로만 답변**하는 경우 있음.
- **대응:** 결정 사항을 항상 번호로 정렬하여 묻고, 답변 즉시 한 줄로 재확인 후 착수.
- **교훈:** 사용자는 짧은 답을 선호. 결정 항목은 항상 번호화하고, 옵션 2~4개로 제한할 것.

### L-004: gpt-oss 호환성의 미묘함
- **상황:** OpenAI Structured Outputs (`response_format=json_schema`)는 OpenAI 공식 모델 기준. Ollama/vLLM 등에서는 엔진 버전에 따라 지원 여부 다름.
- **대응:** `llm.py`에 **2단 fallback** 구현 — primary는 `client.beta.chat.completions.parse`, fallback은 `response_format={"type":"json_object"}` + manual `model_validate_json`.
- **교훈:** "OpenAI 호환 엔드포인트"라는 표현을 신뢰하지 말고, **Structured Outputs 지원 여부는 별도 호환성 매트릭스**로 다뤄야 함. 코드는 항상 fallback 경로 확보.

### L-005: Pure Python 영역과 LLM 영역의 엄격한 분리
- **상황:** 명세서에서 "결정론적 로직 우선" 원칙을 강조했지만, 실제 코드 작성 시 노드 안에서 datetime 파싱 정도는 무심코 LLM에 맡길 유혹.
- **대응:** `utils.py`를 별도 모듈로 분리하고 **LLM 호출 금지** 주석 박음. `chrono_sorter_node`, `compiler_node`도 동일.
- **교훈:** 결정론 영역과 비결정론 영역을 **파일/모듈 단위**로 물리적 분리할 것. 노드 내부 한 줄짜리 datetime도 LLM으로 빠지면 환각 진입점이 됨.

### L-006: Send API + reducer의 함정
- **상황:** `extracted_events: Annotated[List[Dict], operator.add]` 같은 누적 reducer는 Send 병렬 처리와 잘 맞지만, `hallucinated_tokens`처럼 **섹션별로 초기화하고 싶은 누적 필드**는 reducer만으론 안 됨.
- **대응:** writer 노드에서 `retry == 0`이면 빈 리스트로 간주하는 방식으로 우회. 또는 섹션 인덱스를 키로 갖는 Dict[int, List[str]] 구조로 재설계 가능 (v1.2 후보).
- **교훈:** LangGraph reducer는 "단조 증가"가 자연스러움. **스코프 초기화 의도가 있는 필드는 reducer로 두지 말 것**, 또는 키-스코프 구조로 우회.

### L-007: GitHub 인증 토큰 관리 (사전 셋업의 가치)
- **상황:** `gh auth status`로 시작했으나 미인증 상태. `~/.bashrc`의 `GH_TOKEN` export 라인을 grep으로 찾아 직접 export 후 진행.
- **대응:** **현재 토큰 만료일 2026-06-19** (MEMORY.md 기록). 새 토큰 수신 시 `~/.git-credentials` + `~/.bashrc` 두 곳 갱신.
- **교훈:** `gh` CLI는 환경변수 `GH_TOKEN`이 있으면 자동 인증. `source ~/.bashrc`만으로는 `exec` 셸 컨텍스트에 export가 안 옮겨질 수 있으므로 명시 export 필요.

### L-008: PUBLIC vs PRIVATE 결정의 책임
- **상황:** 기존 프로젝트 패턴이 일관되지 않음 (code-2char-system=PRIVATE, unique-code-system=PUBLIC).
- **대응:** 도메인 비밀 유무로 판단 → 도메인 비밀 없음 → PUBLIC 진행. 사용자가 답변에서 "public 이다" 확인.
- **교훈:** Visibility는 **도메인 비밀 유무가 1차 기준**, 일관성은 2차. 결정 후 즉시 사용자 확인 받을 것.

### L-009: 사용자의 "a", "b", "c" 단답 패턴
- **상황:** 옵션 제시 후 사용자가 "c" 한 글자로 응답. 의미: "(C) 현 상태로 종료, 사용자 환경 실행 결과 보고 대기" + 후속 지시(GitHub 업로드).
- **교훈:** 옵션 답변 후 추가 지시가 같은 메시지에 오는 패턴 있음. **답변 파싱 시 단일 글자만 보지 말고 전체 문장 확인**할 것.

---

## §8. 알려진 한계 / 잠재 이슈

1. **실제 LLM 실행 미검증** — AST 문법은 통과했으나 런타임 오류 가능성 잔존. 특히:
   - `langgraph.types.Send` import 경로 (버전별 차이)
   - `client.beta.chat.completions.parse`의 gpt-oss 엔진 호환성
   - Pydantic v2 `model_json_schema()`가 일부 엔진에서 너무 큰 스키마로 거부될 가능성

2. ~~**200건 입력 시 API 폭주**~~ — **v1.1-r2에서 해결.** `RateLimiter`(기본 12 RPM, 5 동시)로 `structured_call` 내 모든 API 호출 제어. 한도 도달 시 자동 대기 후 재개.

3. **한국어 강제 미명시** — 시스템 프롬프트에 "한국어로 응답하라" 명시 안 됨. gpt-oss는 학습 데이터에 따라 영어로 응답할 수 있음. 필요 시 모든 system 메시지 앞에 추가.

4. **`hallucinated_tokens` 누적 reducer의 부작용** (L-006 참조) — 다른 섹션의 정상 토큰이 의도치 않게 블랙리스트에 잔존할 가능성. writer에서 `retry==0`이면 빈 리스트 취급으로 우회했으나, **로그상으로는 누적된 채 보임** (디버깅 혼선 주의).

5. **`scripts.gen_dummy`로 생성한 15건 데이터 사용** — 실제 200건 운영 데이터와 분포 차이 클 수 있음. M(월 수)이 12 이상이면 `Send` 병렬도 점검 필수.

---

## §9. 빠른 디버깅 체크리스트

| 증상 | 1순위 의심 | 확인 방법 |
|------|----------|----------|
| `ModuleNotFoundError: langgraph.types` | langgraph 버전 | `pip show langgraph` → 0.2.40+ 확인 |
| 모든 노드에서 `structured_call failed` | JSON 파싱 반복 실패 | `[structured_call][retry]` 로그 확인, extract_json 단계별 디버깅 |
| `[rate_limiter] ... 대기` 로그 빈번 | RPM 한도 너무 낮음 | `LLM_MAX_RPM` 상향 (기본 12, 엔진 허용 범위 내) |
| 영어로 응답 | 시스템 프롬프트 | `nodes.py` 모든 system 메시지에 "반드시 한국어로 응답하라" 추가 |
| recursion_limit exceeded | outline 길이 | `main.py`의 200 → 500 상향, 또는 outline 6개 이하 제한 |
| `KeyError: 'target_period'` | planner 환각 | `planner_critique_node`의 Python 검증 로그 확인 → 재시도 카운트 증가 정상 |
| 빈 final_output | compiler 단계 누락 | `[compiler] sections=0` 라인 확인 → save_section 동작 점검 |

---

## §10. 참고 명령어 모음

```bash
# 로컬 진입
cd ~/.openclaw/workspace/projects/deep-doc-pipeline

# 환경 셋업
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # .env 편집 + src/llm.py DEFAULT_HEADERS placeholder 교체

# 데이터 (15건 더미)
python -m scripts.gen_dummy

# 실행
python -m main --format whitepaper
python -m main --format status_report

# AST 문법 검증 (코드 수정 후 필수)
python3 -c "import ast; from pathlib import Path; [ast.parse(f.read_text()) for f in Path('.').rglob('*.py')]; print('OK')"

# GitHub 동기화
git add -A && git commit -m "..." && git push

# 인증 토큰 환경변수 (.bashrc 로딩 안 됐을 때)
export GH_TOKEN=$(grep "export GH_TOKEN" ~/.bashrc | cut -d= -f2)
```

---

## §11. 다음 AI Agent 첫 5분 체크리스트

- [ ] 이 문서(`HANDOFF.md`) §0~3 읽기
- [ ] `SPEC.md` §1 (4대 원칙) + §3 (GraphState) 읽기
- [ ] `src/nodes.py` 헤더 주석 + 라우터 함수 이름들 훑어보기
- [ ] `git log --oneline` 으로 커밋 히스토리 확인
- [ ] 사용자 첫 메시지에서 시나리오 A/B/C/D 중 어디에 해당하는지 분류
- [ ] 코드 수정 시 반드시 AST 검증 후 커밋
- [ ] 사용자에게 한국어로, 사무적 톤, 간결하게 응답 (SOUL.md 준수)

---

_본 문서는 다음 AI Agent의 빠른 인계를 위해 작성됨. 추가 작업 시 §5, §7, §8을 갱신할 것._
