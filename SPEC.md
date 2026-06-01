# 🚀 저성능 모델 극복형 초정밀 심층(Deep & Robust) 문서 분석 파이프라인 명세서

> **문서 버전:** v1.1 (구조적 위험 보강)
> **작성일:** 2026-05-27
> **목적:** 200여 개 JSONL 문서 기반 '현황판/백서' 자동 생성 LangGraph 파이프라인
> **저장 경로:** `projects/deep-doc-pipeline/SPEC.md`
>
> **v1.1 변경 요약 (구조적 위험 3건 보강):**
> - Fact-checker 회귀 방지: 반려 이전 초안 + 환각 토큰 블랙리스트를 재작성 시 명시적으로 주입
> - Fail-Safe 강제통과 시 워터마크 + `unverified_sections` 상태 추가
> - `compiler_node`를 순수 Python 조립으로 한정하고 별도 `polish_node` + 2차 fact-check 도입
>
> **v1.3 변경 요약 (2026-06-01, SPEC 외부 작업 — HANDOFF.md 상세):**
> - 모든 LLM 프롬프트 영어 강제 (EN-only) + `status_report` 모드 제거 (백서 전용)
> - Phase 5 신설: EN→KR 수석 에디터 스타일 렌더링 + 3중 검증 (Python+구조+LLM)
> - 본 SPEC은 v1.1 설계 원본을 보존. v1.3/v1.4 상세는 HANDOFF.md §1·§3·§4 참조
>
> **v1.4 변경 요약 (2026-06-01):**
> - 504 국부 감축: `@retry_on_504` 데코레이터 + `effective_budget()` + `Timeout504Error`
> - user 메시지 절단 금지, 노드 재실행으로 분할 로직 재생성
> - `max_tokens=24,000` + `reasoning_effort="high"` 전역 적용
> - 영문 원본 항상 분리 저장 (`_en.md`)
>
> **v1.5 변경 요약 (2026-06-01):**
> - 비교/검증 루프 제거: `final_fact_checker` + `translation_checker` 루프 전체 삭제
> - 직선 흐름: compiler → polish → prepare_translation → translate → END
> - DOCX 변환 스크립트 추가 (`scripts/md_to_docx.py`)
> - `--reasoning {high,medium}` CLI 인자, 504 2회 초과 시 medium 자동 전환

---

## 1. 핵심 아키텍처 설계 철학 (저성능 모델 방어 4대 원칙)

본 프로젝트는 200여 개의 JSONL 문서를 기반으로 '현황판' 또는 '백서'를 작성합니다.
모델 성능 제약 및 환각(Hallucination)을 극복하기 위해 아래 원칙을 반드시 적용합니다.

1. **극단적 마이크로 태스킹 (Micro-tasking)**
   요약, 목차 구성, 집필, 검수 작업을 절대 하나의 프롬프트에 섞지 않고, 독립된 단일 노드로 완벽히 분리합니다. **(One Node = One Task)**

2. **계층적 컨텍스트 압축 (Hierarchical Chunking)**
   200개의 문서를 한 번에 주지 않습니다. 일/월별 요약 ➔ 전체 흐름 파악 ➔ 특정 목차 전용 데이터 주입 순으로 모델의 인지 부하(Context Window)를 철저히 관리합니다.

3. **가혹한 자가 검증 루프 (Strict Critique & Retry)**
   기획 및 섹션 집필 직후에는 반드시 '팩트체커(Judge) 노드'를 두어 원본 데이터와 대조합니다. 환각이나 포맷 파괴 발견 시 사유를 명시하여 재작성(Loop)을 강제합니다.

4. **결정론적 로직 우선**
   날짜 정렬, 시기별 데이터 묶기, 특정 기간의 데이터 필터링 등 논리적 조작은 절대 LLM에게 맡기지 말고 순수 Python 로직(정규식, datetime 등)으로 구현하여 오류를 원천 차단합니다.

---

## 2. 절대 준수 제약 사항 (Strict Constraints) — ⚠️ 필수

1. **순수 OpenAI SDK 사용**
   LangChain의 `ChatOpenAI` 등 LLM 래퍼(Wrapper) 클래스 사용을 엄격히 금지합니다. 노드 내부에서 `openai.OpenAI()` 클라이언트를 직접 초기화하여 호출하십시오.

2. **구조화된 출력 강제 (Structured Outputs)**
   포맷 붕괴를 막기 위해 정보 추출, 검수 결과, 기획 등 LLM의 모든 응답은 `client.beta.chat.completions.parse` 메서드와 Pydantic 클래스를 결합하여 100% JSON 형태로만 받으십시오.

3. **데이터 소스**
   `./data/records.jsonl` 경로를 코드 최상단에 하드코딩(`LOCAL_DATA_PATH`)하여 로컬 파일을 활용합니다.

---

## 3. 고해상도 전역 상태 스키마 (Deep GraphState Definition)

루프(Loop) 제어와 피드백 추적을 위해 State를 매우 구체적으로 세분화합니다.

```python
import operator
from typing import TypedDict, List, Dict, Annotated, Any

# 딕셔너리 업데이트용 Reducer 함수
def update_dict(a: Dict, b: Dict) -> Dict:
    return {**a, **b}

class GraphState(TypedDict):
    # [1. 초기 입력]
    target_format: str                              # "status_report" 또는 "whitepaper"
    raw_docs: List[Dict[str, Any]]                  # 원본 JSONL 문서 전체

    # [2. Map-Reduce (추출 및 검증)]
    extracted_events: Annotated[List[Dict], operator.add]  # 1차 파싱 성공한 규격화 이벤트 누적

    # [3. 계층적 데이터 압축]
    grouped_chunks: Dict[str, List[Dict]]           # [Python 로직] "YYYY-MM" 기준으로 묶인 원본 데이터
    period_summaries: Annotated[Dict[str, str], update_dict]  # [LLM Map] 각 월별 주요 흐름 2~3줄 요약 모음
    global_theme: str                               # 전체 프로젝트 기간의 거시적 통찰/분석 요약본

    # [4. 백서 기획 트랙 루프 제어]
    outline: List[Dict]                             # 확정된 목차 (각 목차는 다룰 'target_period'를 반드시 포함)
    outline_feedback: str                           # 목차 검수자의 피드백
    is_outline_approved: bool                       # 목차 승인 여부

    # [5. 백서 집필 및 팩트체크 트랙 루프 제어]
    current_section_index: int                      # 현재 집필 중인 목차 인덱스
    current_draft: str                              # 현재 섹션 초안
    previous_draft: str                             # ⭐v1.1 직전 반려 초안 (회귀 방지용 Negative Example)
    hallucinated_tokens: Annotated[List[str], operator.add]  # ⭐v1.1 환각으로 지적된 토큰 누적 블랙리스트
    draft_feedback: str                             # 팩트체커의 피드백 (환각 지적 등)
    is_draft_approved: bool                         # 초안 승인 여부
    section_retry_count: int                        # 무한 루프 방지용 카운터 (최대 3회)
    completed_sections: Annotated[Dict[int, str], update_dict]  # 최종 승인된 섹션들 {index: "content"}
    unverified_sections: Annotated[List[int], operator.add]     # ⭐v1.1 Fail-Safe 강제통과된 섹션 인덱스

    # [6. 최종 산출물]
    final_compiled: str                             # ⭐v1.1 polish 이전 순수 조립본 (디버깅/감사용)
    final_output: str                               # 최종 백서 (polish + 2차 fact-check 통과본)
```

> **⚠️ 원본 명세서 오타 수정:** `return {a, b}` → `return {**a, **b}` (dict merge)

---

## 4. 단계별 심층 노드(Node) 설계도

### Phase 1: 무결성 정보 추출 (Robust Map-Reduce)

- **`load_docs_node`**
  데이터를 읽어 `raw_docs`에 저장.

- **`strict_extractor_node`** (Map Node / `Send` 병렬 처리)
  - 로직: 단일 문서에서 `{"date": "YYYY-MM-DD", "issue": "...", "action": "..."}` 추출.
  - 방어 코드: 노드 내부 파이썬 코드에 `try-except`를 넣어 JSON 파싱 에러 시 **최대 3회 재시도(Retry)** 하는 로직을 자체 구현.

- **`chrono_sorter_node`** (Pure Python — LLM 호출 금지)
  추출된 이벤트를 날짜 오름차순으로 완벽히 정렬하고, `"YYYY-MM"`을 키값으로 하여 `grouped_chunks` 딕셔너리로 분할.

### Phase 2: 마이크로 요약 (Hierarchical Summarization)

> 200개의 추출 데이터를 한 번에 던지면 모델은 반드시 기억을 잃습니다.

- **`period_summarizer_node`** (Map Node / `Send` 병렬 처리)
  `grouped_chunks`의 각 월별 덩어리마다 병렬 실행하여, "해당 월의 핵심 동향 3문장"을 생성하고 `period_summaries`에 저장.

- **`theme_analyzer_node`**
  `period_summaries`들을 모아 읽고, 전체 프로젝트의 성과와 위기 흐름을 관통하는 `global_theme`를 1문단으로 도출.

### Phase 3: 라우터

- **`route_by_target`**
  `target_format`을 검사하여 `"status_report"` 또는 `"whitepaper"` 엣지로 분기.

### Phase 4-A: 현황판 트랙 (Status Report)

- **`status_formatter_node`**
  `grouped_chunks`와 `period_summaries`를 조합하여 깔끔한 마크다운 현황 리포트 생성.

### Phase 4-B: 종합 백서 트랙 — 심층 자가 검증 루프 ⭐️

#### [1단계: 기획 검증 루프]

- **`draft_planner_node`**
  세부 이벤트 대신, `global_theme`와 `period_summaries`만 제공하여 백서의 목차를 기획. Pydantic을 통해 각 목차가 어떤 월(`target_period`)의 데이터를 다룰지 반드시 명시하도록 강제.

- **`planner_critique_node`** (기획 검수자)
  작성된 목차가 시계열 흐름에 맞는지 평가. 실패 시 `outline_feedback`에 이유를 적고 반려하여 재작성(Loop) 유도.

#### [2단계: 컨텍스트 컷오프 및 집필 검증 루프 (Context & Fact-Check Loop)]

- **`init_writing_node`**
  `current_section_index` 및 `section_retry_count`를 0으로 초기화.

- **`context_filter_node`** (Pure Python — **가장 중요한 방어 기제**)
  현재 집필할 목차의 `target_period`를 확인하고, 해당 기간의 데이터만 `grouped_chunks`에서 슬라이싱하여 다음 노드로 넘김. (모델에게 불필요한 기간의 데이터를 주지 않아 환각 원천 차단)

- **`section_writer_node`**
  오직 필터링된 원본 데이터만 주입하여 해당 섹션을 깊이 있게 작성.

- **`section_writer_node`** (⭐v1.1 회귀 방지 강화)
  오직 필터링된 원본 데이터만 주입하여 해당 섹션을 깊이 있게 작성.
  **재작성 시 추가 프롬프트 (`section_retry_count > 0`인 경우):**
  - `previous_draft`를 **반려된 직전 초안 (반복 금지)** 으로 명시 주입
  - `hallucinated_tokens`를 **사용 금지 토큰 리스트**로 명시 주입
  - `draft_feedback`을 **수정 지시사항**으로 주입
  → 동일 환각 반복 차단.

- **`fact_checker_node`** (환각 킬러 / LLM-as-a-Judge) (⭐v1.1 토큰 추출 강화)
  방금 작성된 초안(`current_draft`)과 주입되었던 필터링 원본 데이터를 대조.
  **프롬프트 지시:** *"너는 매우 깐깐한 감사관이다. 초안에 원본 데이터에 없는 고유명사, 날짜, 수치가 하나라도 지어내어(환각) 작성되었다면 무조건 `is_draft_approved=False`를 반환하고 사유를 적어라. 또한 환각으로 판단한 **정확한 토큰들의 리스트**(`hallucinated_terms: List[str]`)를 별도 필드에 추출하여 반환하라."*
  → Pydantic 스키마에 `hallucinated_terms: List[str]` 필드 추가 강제.
  → Fail 시 이 리스트가 `hallucinated_tokens` 상태로 누적.

- **`route_section_draft`** (Conditional Edge) (⭐v1.1 워터마크)
  - **Fail:** `current_draft`를 `previous_draft`로 옮기고 `section_writer_node`로 회귀. `section_retry_count += 1`.
  - **Fail-Safe (`retry_count >= 3`):** 강제 통과시키되,
    1. 섹션 상단에 워터마크 자동 삽입:
       ```
       > ⚠️ **검증 미완료 섹션** — 자동 팩트체크 3회 실패. 원본 데이터 대조 필요.
       > 마지막 반려 사유: {draft_feedback}
       ```
    2. `unverified_sections`에 현재 인덱스 누적.
    3. 다음 섹션으로 진행.
  - **Pass:** 통과된 섹션을 `completed_sections`에 누적 저장. `previous_draft` / `hallucinated_tokens`(섹션 스코프) 초기화 후 다음 목차로 인덱스 변경.

#### [3단계: 조립 및 윤문 분리] (⭐v1.1 신규)

- **`compiler_node`** (Pure Python — **LLM 호출 금지**)
  `completed_sections`를 인덱스 순으로 정렬하여 목차(`outline`) 헤더와 결합. 단순 문자열 조립만 수행. 결과는 `final_compiled`에 저장.
  → LLM이 윤문 단계에서 새 내용을 끼워넣을 위험 원천 차단.

- **`polish_node`** (LLM, ⭐v1.1 신규)
  `final_compiled`를 입력받아 **문장 연결만 매끄럽게 다듬는 윤문 작업**. 프롬프트에 명시:
  *"너는 교정 편집자다. 입력된 본문의 사실 정보(날짜, 고유명사, 수치, 인과관계)는 **단 한 글자도 추가/삭제/수정하지 마라**. 오직 문단 연결어, 호응, 어색한 문장 흐름만 다듬어라. 새로운 정보를 절대 만들지 마라."*

- **`final_fact_checker_node`** (⭐v1.1 신규, 2차 팩트체크)
  `polish_node`의 출력과 `final_compiled`(polish 이전)를 대조. 윤문 과정에서 사실이 변형/추가되었는지 검증.
  - Fail: `polish_node`로 1회 회귀 (최대 2회 재시도, 이후 polish 우회하여 `final_compiled`를 그대로 `final_output`으로 채택).
  - Pass: `final_output` 확정.
  - 최종 결과 하단에 `unverified_sections` 목록을 **감사 로그 섹션**으로 자동 첨부.

---

## 5. 심층 워크플로우 시각화 (State Machine Flow)

```
[ START ]
   │
[ loader ] ──▶ (Map/Send) ──▶ [ strict_extractor (자체 Retry 포함) ] × 200
   │
[ chrono_sorter (Python 로직: 시간순 정렬 및 월별 그룹화) ]
   │
   ├─▶ (Map/Send) ──▶ [ period_summarizer (월별 압축) ] × M
   │
[ theme_analyzer (전체 흐름 도출) ]
   │
[ Router: route_by_target ]
   │
   ├─▶ (status_report) ──▶ [ status_formatter ] ──▶ [ END ]
   │
   └─▶ (whitepaper) ─────▶ [ draft_planner ] ◀──────────┐
                              │                         │ (Fail)
                              ▼                         │
                          [ planner_critique ] ────────┘
                              │ (Pass)
                              ▼
                          [ init_writing ]
                              │
   ┌──────────────────────────▼────────────────────────────┐
   │                                                       │
   │   [ context_filter (Python 로직: 타겟 기간만 컷오프) ] │
   │                       │                               │
   │                       ▼                               │
   │     ┌── [ section_writer ] ◀─────────┐                │
   │     │       │                        │                │
   │  (Next Sec) │                        │                │
   │     │       ▼                        │                │
   │     └── [ fact_checker ] ────────────┘                │
   │             │ (Fail - retry<3: previous_draft +       │
   │             │  hallucinated_tokens 누적 → Retry)       │
   │             │ (Fail - retry>=3: ⚠️ 워터마크 +          │
   │             │  unverified_sections 누적 → 강제통과)    │
   │             ▼ (Pass)                                  │
   └─────── [ 저장 및 인덱스 증가 ] ───────────────────────┘
             │ (모든 목차 완료)
             ▼
        [ compiler (Pure Python: 조립만) ]
             │
             ▼
        [ polish (LLM: 윤문만, 사실 변경 금지) ] ◀───┐
             │                                       │ (Fail, 최대 2회)
             ▼                                       │
        [ final_fact_checker (2차 검증) ] ───────┘
             │ (Pass 또는 최대 재시도 초과 → final_compiled 채택)
             ▼
        [ END ] — 하단에 unverified_sections 감사 로그 자동 첨부
```

---

## 6. Action Items (개발 및 검증 지침)

본 명세서를 숙지한 후 다음 순서대로 작업을 수행.

1. **가상 테스트 환경 구축**
   `./data/records.jsonl`이 없으면, 무작위 날짜(최소 3~4개월에 걸친 시스템 전환 또는 장애 대응 시나리오)가 포함된 더미 JSONL 데이터 **15줄**을 자동 생성하는 파이썬 스크립트를 작성하여 준비.

2. **Pydantic 스키마 선언**
   `parse` API 통신을 위한 추출 구조체, 검수 구조체(승인 여부 boolean 필수 포함, ⭐v1.1: `hallucinated_terms: List[str]` 필드 추가), 기획 구조체를 명확히 선언.

3. **그래프 구현**
   지시된 노드와 조건부 엣지 구축. `context_filter_node`와 루프 제어부에서 무한 루프에 빠지지 않도록 `section_retry_count` 제어 로직을 꼼꼼히 작성. ⭐v1.1: `polish_node` 회귀 카운터(`polish_retry_count`)도 동일 패턴으로 작성.

4. **파이프라인 실행 및 로그 증명**
   `main` 블록에서 `target_format`을 `"whitepaper"`로 설정하여 그래프를 실행. 터미널 출력(`print`)을 통해, **팩트체커가 초안을 반려(Fail)하고 재작성하는 Loop 과정이 정상 동작**하는지 증명하고 최종 마크다운 결과를 출력.
   ⭐v1.1: 추가 증명 사항
   - 재작성 시 `previous_draft` / `hallucinated_tokens`가 프롬프트에 주입되는 로그
   - Fail-Safe 강제통과 워터마크 삽입 로그 (의도적으로 환각 유발 시나리오로 검증)
   - `compiler` → `polish` → `final_fact_checker` 흐름 로그

---

## 부록 A. 원본 명세서 대비 보정 사항

| 항목 | 원본 | 보정 |
|------|------|------|
| `update_dict` 반환 | `return {a, b}` (set literal — 실행 불가) | `return {**a, **b}` (dict merge) |

---

## 부록 B. v1.1 구조적 위험 보강 상세

### B-1. Fact-checker 회귀 위험 (Re-hallucination) 방지

**문제:** 원본 명세서에서는 Fail 시 `section_writer`가 `draft_feedback`만 받고 재작성. 모델은 직전 자신의 환각 문장을 기억하지 못하므로 **동일 환각을 반복 생성**할 수 있음.

**보강:**
1. `previous_draft` 상태 추가 → 반려된 직전 초안을 다음 작성 프롬프트에 **Negative Example**로 명시 주입.
2. `hallucinated_tokens` 상태 추가 (`operator.add` reducer) → 환각으로 지적된 고유명사/숫자 토큰을 누적 블랙리스트로 관리.
3. `fact_checker_node`의 Pydantic 응답 스키마에 `hallucinated_terms: List[str]` 필드 추가 강제 → 토큰 자동 추출.
4. 섹션 통과 시 해당 섹션 스코프의 블랙리스트 초기화 (다른 섹션의 정상 토큰을 잘못 차단하지 않도록).

### B-2. Fail-Safe 강제통과 시 워터마크 + 감사 로그

**문제:** 원본 명세서에서 `retry_count >= 3` 강제 통과는 환각 섞인 초안이 **무표시로 최종 백서에 편입**되는 위험 존재.

**보강:**
1. 강제 통과 시 섹션 상단에 시각적 워터마크 자동 삽입.
2. 마지막 `draft_feedback`(반려 사유)을 워터마크에 포함하여 검토자가 즉시 무엇이 의심스러운지 파악 가능.
3. `unverified_sections` 상태 추가 (`operator.add` reducer) → 최종 백서 하단 감사 로그에 "검증 미완료 섹션 목록" 자동 출력.

### B-3. Compiler 윤문 환각 재주입 방지

**문제:** 원본 명세서의 `compiler_node`는 "조립 + 윤문"을 한 LLM 노드에서 수행. 검수 통과한 섹션을 모은 후 LLM이 윤문 단계에서 **새로운 사실을 만들어낼** 가능성 (특히 "매끄럽게" 다듬으라는 지시는 모델이 빈칸을 채우게 유도).

**보강:** 단일 노드를 3개로 분리.
1. `compiler_node` → **Pure Python**, 목차 헤더 + 본문 단순 조립만. LLM 호출 금지.
2. `polish_node` → LLM, **사실 정보 변경 금지** 명시 프롬프트로 윤문만 수행.
3. `final_fact_checker_node` → 2차 팩트체크로 윤문 과정 검증. Fail 시 polish 우회하여 `final_compiled`(조립본)을 그대로 채택하는 Fail-Safe.

→ 결과: 최악의 경우에도 검수 통과한 본문이 그대로 유지되며, LLM 윤문 실패가 최종 산출물 품질을 훼손하지 못함.

