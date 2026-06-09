# Deep Doc Pipeline — 설계 명세서 v3.0

> **버전:** v3.0 (아키텍처 전면 재설계)
> **최종 갱신:** 2026-06-09
> **목적:** 원시 프로젝트 데이터(JSONL 등) → 카테고리 기반 지식 구조화 → 하이브리드 백서(Executive Summary + 시계열 부록) 자동 생성 LangGraph 파이프라인
> **GitHub:** https://github.com/zeekcomputer-lang/deep-doc-pipeline-v2

---

## 0. v2 → v3 변경 요약

| 항목 | v2.0 (시계열 우선) | v3.0 (카테고리 우선) |
|------|-------------------|---------------------|
| **축** | 시간순 (Phase 1-5: 추출→요약→기획→집필→번역) | 비즈니스 카테고리 4축 (Step 1-4) |
| **지식 구조** | `YYYY-MM` 월별 그루핑 | 4개 카테고리 `knowledge_base` (JSON/RDB-ready) |
| **날짜 의존성** | 필수 (날짜 없으면 정렬 불가) | **날짜 비의존** — `temporal_indexer`가 best-effort 처리 |
| **최종 산출물** | 시계열 서술형 백서 1종 | **하이브리드 백서**: Executive Summary(2-3p) + 시계열 부록 |
| **중간 산출물** | 없음 | Step별 JSON/MD 파일 8종 (디버깅·재사용 가능) |
| **서술 방식** | 기간별 이벤트 나열 | **문제→결정→가치** 인과 스토리라인 |

**유지 항목:** 순수 OpenAI SDK, Pydantic 강제 출력, 95KB 하드리밋, 504 국부 감축, EN-only LLM, user 메시지 불변, 팩트체커 루프, `prompt_config.py` 커스텀.

---

## 1. 아키텍처 설계 철학

### 1.1 핵심 설계 원칙 5대 항목

| # | 원칙 | 설명 |
|---|------|------|
| 1 | **카테고리 우선 지식 구조화** | 원시 데이터를 시간이 아닌 비즈니스 의미 축으로 먼저 분류. 날짜 없는 데이터도 처리 가능. |
| 2 | **극단적 마이크로 태스킹** | One Node = One Task. 분류·분석·집필·검수를 하나의 프롬프트에 섞지 않음. |
| 3 | **인과 스토리라인 우선** | 시간순 나열이 아닌 **문제→결정→가치** 서사 구조로 Executive Summary 작성. |
| 4 | **자가 검증 루프** | 서술·팩트체크 직후 원본 `knowledge_base`와 대조. 환각 발견 시 재작성 강제. |
| 5 | **결정론적 로직 우선** | 카테고리 집계·날짜 추출·문서 조립은 Pure Python. LLM에 맡기지 않음. |

### 1.2 추가 설계 원칙

- **EN-only LLM**: Step 1~4 전체를 영어로 수행. 한국어는 번역 노드에서만 등장.
- **95KB 하드리밋**: 모든 API 호출의 메시지 페이로드가 95KB 미만. 초과 시 분할·압축.
- **504 국부 감축**: 타임아웃 시 실패 노드만 축소(-5KB/step), 성공 후 원복. 전역 품질 저하 방지.
- **user 메시지 불변**: LLM에 전달되는 데이터는 절대 절단하지 않음. 분할로만 해결.
- **날짜 비의존(Date-Resilient)**: 날짜 마커가 불명확해도 파이프라인이 중단되지 않음. `temporal_indexer`가 best-effort 처리.

---

## 2. 절대 준수 제약 사항

1. **순수 OpenAI SDK** — `openai.OpenAI()` 직접 사용. LangChain LLM 래퍼 금지.
2. **Pydantic 강제 출력** — `structured_call()` → `extract_json()` 3단 파서 → `model_validate()`.
3. **`response_format` 인자 금지** — GPT-OSS 호환을 위해 프롬프트 가드 + 파서로 JSON 강제.
4. **Pure Python 영역 분리** — `utils.py`, `compiler`, `temporal_indexer`, `knowledge_aggregator`에서 LLM 호출 금지.
5. **영어/한국어 분리** — `_EN_ENFORCE` 접미사로 Step 1~4 영어 강제. 번역 노드만 한국어 출력.

---

## 3. 4개 비즈니스 카테고리

모든 원시 데이터는 아래 4개 카테고리 중 하나로 분류된다. 분류는 Step 1에서 LLM이 수행하며, Step 2~3에서 카테고리별 분석·서술의 기본 축이 된다.

| 카테고리 ID | 명칭 | 범위 |
|-------------|------|------|
| `Architecture_and_Tech` | 아키텍처·기술 | 기술 스택 변경, 아키텍처 결정, 인프라 최적화, 성능 개선 |
| `Risk_and_Troubleshooting` | 리스크·장애 대응 | 크리티컬 이슈, 위기 대응, 기술 부채 해소, 장애 복구 |
| `Business_and_Feature` | 비즈니스·기능 | 비즈니스 요구사항, 기능 배포, 마일스톤 달성, KPI |
| `Lessons_Learned` | 교훈·성장 | 조직적 성장, 프로세스 개선, 회고, 향후 방향 |

**카테고리 균형 점검:** `knowledge_aggregator`에서 특정 카테고리 항목이 0건이면 경고 로그 출력. 파이프라인은 중단하지 않되, 최종 산출물에 "해당 카테고리 데이터 부재" 주석을 삽입.

---

## 4. GraphState 스키마

```python
class GraphState(TypedDict, total=False):
    # ── Step 1: Knowledge Structuring ──────────────────────────
    raw_docs: List[Dict[str, Any]]                              # 로드된 원시 문서
    knowledge_entries: Annotated[List[Dict], operator.add]       # LLM 추출된 개별 엔트리
    knowledge_base: Dict[str, List[Dict]]                        # category → entries (집계 완료)
    temporal_index: List[Dict]                                   # 날짜순 정렬 (best-effort)

    # ── Step 2: Narrative Flow ─────────────────────────────────
    category_analyses: Annotated[Dict[str, str], update_dict]    # 카테고리별 심층 분석
    narrative_flow: str                                          # 교차 카테고리 스토리라인
    narrative_feedback: str                                      # 비평 피드백
    is_narrative_approved: bool                                  # 서사 승인 여부
    narrative_retry_count: int                                   # 서사 재시도 횟수

    # ── Step 3: Executive Summary Writing ──────────────────────
    executive_sections: List[Dict]                               # 계획된 섹션 목록
    current_section_index: int                                   # 현재 집필 커서
    current_draft: str                                           # 현재 초안
    previous_draft: str                                          # 직전 반려 초안 (회귀 방지)
    hallucinated_tokens: Annotated[List[str], operator.add]      # 환각 토큰 블랙리스트
    draft_feedback: str                                          # 팩트체크 피드백
    is_draft_approved: bool                                      # 초안 승인 여부
    section_retry_count: int                                     # 섹션 재시도 횟수
    completed_sections: Annotated[Dict[int, str], update_dict]   # idx → 완성 텍스트
    unverified_sections: Annotated[List[int], operator.add]      # Fail-Safe 감사 로그

    # ── Step 4: Hybrid Assembly ────────────────────────────────
    executive_summary: str                                       # 조립된 Executive Summary
    chronological_appendix: str                                  # 시계열 부록
    final_compiled: str                                          # 최종 조합 문서
    final_output: str                                            # 윤문 완료 최종본

    # ── Translation (optional) ─────────────────────────────────
    english_output: str                                          # 영문 원본 보존
    proper_nouns: List[str]                                      # 고유명사 목록
```

**Reducer:** `operator.add` = 리스트 누적 병합 (fanout 수집), `update_dict` = 딕셔너리 키 단위 업데이트.

---

## 5. Pydantic 응답 스키마 (10종)

| 스키마 | 용도 (Step) | 핵심 필드 |
|--------|-------------|----------|
| `KnowledgeEntry` | 문서 1건 분류·추출 (1) | `category`, `title`, `description`, `source_ref`, `date_hint?`, `impact_level` |
| `CategoryAnalysis` | 카테고리별 심층 분석 (2) | `category`, `key_findings[]`, `causal_chain`, `implications` |
| `NarrativeFlow` | 교차 스토리라인 (2) | `storyline` (문제→결정→가치), `section_plan[{title, category_refs[], intent}]` |
| `NarrativeCritique` | 서사 검증 (2) | `is_approved`, `feedback` |
| `SectionDraft` | 섹션 본문 집필 (3) | `content` |
| `FactCheckResult` | 팩트체크 결과 (3) | `is_draft_approved`, `feedback`, `hallucinated_terms[]` |
| `TimelineEntry` | 시계열 부록 항목 (4) | `period`, `events[]`, `significance` |
| `PolishedDocument` | 윤문 출력 (4) | `content` |
| `TranslatedDocument` | 번역 출력 (선택) | `content` |
| `ProperNounList` | 고유명사 목록 (선택) | `nouns[]` |

**공통 규칙:** 모든 스키마는 `src/schemas.py` 정의. LLM 응답 → `extract_json()` → `model_validate()` 3단 파싱. 실패 시 최대 2회 재시도 → `ValidationError`.

---

## 6. 그래프 구조 — 15 노드 + 4 라우터

```
START → load_docs → [fanout] knowledge_extractor(×N) → knowledge_aggregator → temporal_indexer
  → [fanout] category_analyzer(×4) → narrative_planner ⟲ narrative_critique (최대 3회)
  → init_writing → section_writer ⟲ fact_checker (--skip-fact-check: 바이패스)
     ├─ retry_section (최대 3회) / save_section / save_section_with_warning (⚠️)
  → timeline_formatter → compiler (Pure Python) → polish
  → (optional) prepare_translation → translate → END
```



---

## 7. 노드별 설계

### Step 1: Knowledge Structuring (지식 구조화)

원시 데이터를 4개 카테고리로 분류하고, JSON/RDB-ready 구조로 변환한다.

| 노드 | 유형 | 설명 |
|------|------|------|
| `load_docs` | Python | `data/records.jsonl` 로드. 유연한 포맷 대응 (JSONL, JSON array). 파싱 실패 건은 skip + 경고 로그. |
| `knowledge_extractor` | LLM ×N | 문서 1건 → `KnowledgeEntry` 추출. 카테고리 배정 + 영향도 평가. 95KB 초과 시 문서 분할. fanout으로 병렬 처리. |
| `knowledge_aggregator` | Python | 추출된 엔트리 병합. 중복 제거 (title + source_ref 기준). `knowledge_base` 딕셔너리 구축 (category → entries). 카테고리별 항목 수 균형 점검 — 0건 카테고리 경고. |
| `temporal_indexer` | Python | Best-effort 날짜 추출: `date_hint` 필드 파싱 → 정규식 폴백 → 불명 시 `"undated"` 그룹. 날짜순 정렬하여 `temporal_index` 생성. **날짜 없는 데이터도 파이프라인 중단 없이 처리.** |

**prompt_config 연동:** `knowledge_extractor` → `get_extraction_context()` (PURPOSE만 주입)

### Step 2: Narrative Flow (서사 흐름)

카테고리별 심층 분석 후 교차 카테고리 스토리라인을 수립한다.

| 노드 | 유형 | 설명 |
|------|------|------|
| `category_analyzer` | LLM ×4 | 카테고리 1개의 `knowledge_base[category]`를 입력받아 `CategoryAnalysis` 생성. `key_findings`, `causal_chain`(원인-결과 사슬), `implications`(비즈니스 시사점) 도출. fanout으로 4개 카테고리 병렬 처리. |
| `narrative_planner` | LLM | 4개 `CategoryAnalysis` + `temporal_index` 종합 → `NarrativeFlow` 생성. **문제→결정→가치** 인과 스토리라인 + Executive Summary의 `section_plan` (섹션별 제목·카테고리 참조·의도) 수립. |
| `narrative_critique` | LLM + Python | **Python 검증:** section_plan의 category_refs가 실제 knowledge_base에 존재하는지 확인. **LLM 검증:** 인과 사슬의 논리적 일관성·완결성 평가. `NarrativeCritique` 반환. 최대 3회 루프 후 강제 통과. |

**prompt_config 연동:**
- `category_analyzer` → `get_analysis_context()` (PURPOSE + TONE)
- `narrative_planner` → `get_analysis_context()` (PURPOSE + TONE)

### Step 3: Executive Summary Writing (집필)

`NarrativeFlow.section_plan`에 따라 2-3페이지 분량의 고압축 비즈니스 백서를 집필한다.

| 노드 | 유형 | 설명 |
|------|------|------|
| `init_writing` | Python | `section_plan`에서 `executive_sections` 초기화. `current_section_index = 0`, 카운터 리셋. |
| `section_writer` | LLM | `executive_sections[current_section_index]`의 `category_refs`에 해당하는 `knowledge_base` 엔트리만 주입하여 섹션 본문 집필. 재작성 시 `previous_draft` + `hallucinated_tokens` 블랙리스트 주입. 95KB 초과 시 엔트리 배치 분할→병합. |
| `fact_checker` | LLM | 초안 vs `knowledge_base` 원본 엔트리 대조. 환각 토큰 추출 필수. `--skip-fact-check` 시 자동 승인 바이패스. |
| `retry_section` | Python | `section_retry_count` 증가. `previous_draft` = `current_draft`. 라우터가 `section_writer`로 복귀. |
| `save_section` | Python | 승인된 섹션 `completed_sections[idx]`에 저장. `current_section_index` 증가. |
| `save_section_with_warning` | Python | 3회 실패 시 ⚠️ 워터마크 삽입 + `unverified_sections`에 idx 기록 후 강제 저장. |

**라우터 로직:**
```
fact_checker 결과 →
  ├─ is_draft_approved=True → save_section
  ├─ section_retry_count < 3 → retry_section → section_writer
  └─ section_retry_count ≥ 3 → save_section_with_warning
save_section →
  ├─ current_section_index < len(executive_sections) → section_writer (다음 섹션)
  └─ 모든 섹션 완료 → timeline_formatter (Step 4)
```

**prompt_config 연동:** `section_writer` → `get_writing_context()` (PURPOSE + TONE + AUDIENCE + CUSTOM)

### Step 4: Hybrid Assembly (하이브리드 조립)

Executive Summary와 시계열 부록을 결합하여 최종 백서를 생성한다.

| 노드 | 유형 | 설명 |
|------|------|------|
| `timeline_formatter` | LLM | `temporal_index`를 입력받아 가독성 높은 시계열 부록(Chronological Appendix)으로 포맷팅. `TimelineEntry` 스키마 기반. `"undated"` 그룹은 별도 "기타 항목" 섹션으로 배치. |
| `compiler` | Python | `completed_sections` → Executive Summary 조립 + `chronological_appendix` 결합. `unverified_sections` 감사 로그 첨부. **LLM 호출 금지.** |
| `polish` | LLM | 최종 문서의 문체·연결어·일관성 윤문. 섹션별 분할 처리(504 방지). **사실 변경·추가 절대 금지.** |

**prompt_config 연동:** `timeline_formatter` → `get_assembly_context()` (PURPOSE + AUDIENCE)

### Translation (선택)

| 노드 | 유형 | 설명 |
|------|------|------|
| `prepare_translation` | Python | 영문 원본 저장 (`english_output`). `extract_proper_nouns()` 실행 → `proper_nouns` 리스트 생성. |
| `translate` | LLM | 항상 섹션별 처리. 3단계 폴백: ① 전체 섹션 충실 번역 (ratio≥0.35 검증) → ② 문단별 8KB 분할 → ③ 소스 데이터로 한글 직접 생성. 고유명사 목록 프롬프트 주입. |

**prompt_config 연동:** `translate` → `get_translation_context()` (PURPOSE + TONE + AUDIENCE, 한국어)

---

## 8. prompt_config.py 연동 매트릭스

| 함수 | 호출 노드 | PURPOSE | TONE | AUDIENCE | CUSTOM | 언어 |
|------|----------|---------|------|----------|--------|------|
| `get_extraction_context()` | `knowledge_extractor` | ✅ | — | — | — | EN |
| `get_analysis_context()` | `category_analyzer`, `narrative_planner` | ✅ | ✅ | — | — | EN |
| `get_writing_context()` | `section_writer` | ✅ | ✅ | ✅ | ✅ | EN |
| `get_assembly_context()` | `timeline_formatter` | ✅ | — | ✅ | — | EN |
| `get_translation_context()` | `translate` | ✅ | ✅ | ✅ | — | KO |

**v2→v3 변경:** `get_summary_context()` → `get_extraction_context()` + `get_analysis_context()` 분리. `get_planning_context()` → `get_analysis_context()`로 통합. 신규: `get_extraction_context()`, `get_assembly_context()`.

---

## 9. 방어 기제

| # | 위험 | 방어 | 구현 위치 |
|---|------|------|----------|
| 1 | Fact-checker 회귀 | `previous_draft` + `hallucinated_tokens` 블랙리스트 누적 | `section_writer`, `retry_section` |
| 2 | Fail-Safe 강제통과 | ⚠️ 워터마크 + `unverified_sections` 감사 로그 | `save_section_with_warning` |
| 3 | 504 타임아웃 | 국부 감축 (-5KB/step) + reasoning 다운그레이드 + 노드 재실행 | `@retry_on_504` / `llm.py` |
| 4 | 95KB 초과 | `effective_budget()` 사전 측정 + 분할/압축 | `context_guard.py` / 각 노드 |
| 5 | 고유명사 소실 | `extract_proper_nouns()` → 번역 프롬프트에 목록 주입 | `utils.py` / `translate` |
| 6 | 번역 콘텐츠 소실 | 섹션별→문단별 분할 + 완전성 검증 + 소스데이터 폴백 | `translate` |
| 7 | 날짜 부재 | `temporal_indexer` best-effort: 정규식 폴백 → `"undated"` 그룹 | `temporal_indexer` |
| 8 | 카테고리 불균형 | `knowledge_aggregator`에서 0건 카테고리 경고 + 최종 문서 주석 | `knowledge_aggregator` |
| 9 | Knowledge DB 유실 | Step별 중간 산출물 JSON 파일 자동 저장 | 각 Step 종료 시 |
| 10 | 에러 추적 | `pipeline_error.log` (타임스탬프·노드·스택트레이스) | `logger.py` / `main.py` |

---

## 10. 실행 옵션

### CLI

```bash
python -m main                                        # 기본 (reasoning=high, 팩트체크 ON, 번역 ON)
python -m main --skip-fact-check                      # 팩트체크/환각검증 생략 (빠른 실행)
python -m main --skip-translation                     # 영문만 출력 (한국어 번역 생략)
python -m main --export-kb kb.json                    # Knowledge Base를 별도 JSON으로 추출
python -m main --reasoning medium                     # 서버 타임아웃 회피 우선 (빠른 실행)
python -m main --resume <dir> --resume-from <step>    # 중간 산출물 디렉토리에서 특정 Step부터 재개
```

### 환경변수

| 변수 | 기본값 | 설명 |
|------|-------|------|
| `OPENAI_BASE_URL` | — | LLM 엔드포인트 (필수) |
| `OPENAI_MODEL` | `gpt-oss-20b` | 기본 모델 |
| `EXTRACTOR_MODEL` | (OPENAI_MODEL) | Step 1 추출 모델 |
| `ANALYZER_MODEL` | (OPENAI_MODEL) | Step 2 분석 모델 |
| `WRITER_MODEL` | (OPENAI_MODEL) | Step 3 집필 모델 |
| `JUDGE_MODEL` | (OPENAI_MODEL) | 팩트체크/비평 모델 |
| `LLM_MAX_RPM` | `12` | 분당 최대 호출 |
| `LLM_MAX_CONCURRENT` | `5` | 동시 호출 상한 |
| `LLM_CONTEXT_BUDGET_KB` | `95` | per-call 컨텍스트 예산 (KB) |

---

## 11. 프롬프트 커스텀

`src/prompt_config.py` 파일을 편집하여 백서의 톤·목적·편향을 조정할 수 있다.

| 설정 | 기본값 | 적용 Step |
|------|--------|----------|
| `DOCUMENT_PURPOSE` | "카테고리 기반 하이브리드 프로젝트 백서" | Step 1~4 + 번역 |
| `TONE_DIRECTIVE` | `""` (중립 객관) | Step 2~3 + 번역 |
| `TARGET_AUDIENCE` | `""` (일반 독자) | Step 3~4 + 번역 |
| `CUSTOM_DIRECTIVES` | `""` | Step 3 집필만 |

**안전장치:** 편향 설정과 무관하게 `fact_checker`가 `knowledge_base` 원본 외 사실 추가를 차단한다.

**커스텀 예시:** `DOCUMENT_PURPOSE = "경영진 보고용 성과·리스크 종합 백서"` / `TONE_DIRECTIVE = "긍정적 성과와 성장세를 강조하되, 사실에 기반할 것"` / `TARGET_AUDIENCE = "C-레벨 경영진"` / `CUSTOM_DIRECTIVES = "매 섹션 말미에 시사점 문단 추가"`

---

## 12. 파일 구조

```
├── main.py                  실행 진입점 + CLI 파서
├── requirements.txt         openai, langgraph, pydantic, python-dotenv, python-docx
├── .env.example             환경변수 템플릿
├── data/records.jsonl       입력 JSONL (또는 JSON array)
├── scripts/
│   ├── gen_dummy.py         더미 JSONL 생성기
│   └── md_to_docx.py       마크다운 → DOCX 변환
└── src/
    ├── prompt_config.py     ★ 사용자 커스텀 프롬프트 설정
    ├── schemas.py           Pydantic 응답 스키마 10종
    ├── state.py             GraphState v3 + reducer
    ├── context_guard.py     95KB 예산 관리
    ├── llm.py               OpenAI SDK + Rate Limiter + 504 방어
    ├── logger.py            타임라인 로거 + 에러 로그
    ├── utils.py             Pure Python 결정론 로직
    ├── nodes.py             15개 노드 + 4개 라우터
    └── graph.py             LangGraph 조립 v3
```

---

## 13. 출력 구조

모든 실행의 산출물은 타임스탬프 디렉토리에 저장된다. 각 Step의 중간 산출물이 JSON/MD로 보존되어 디버깅·재실행·외부 연동에 활용 가능하다.

```
output/<timestamp>/
  ├── step1_knowledge_base.json         # 카테고리별 지식 구조 (JSON/RDB-ready)
  ├── step1_temporal_index.json         # 날짜순 정렬 엔트리 (best-effort)
  ├── step2_category_analyses.json      # 4개 카테고리 심층 분석
  ├── step2_narrative_flow.md           # 교차 카테고리 스토리라인 + section_plan
  ├── step3_executive_summary.md        # Executive Summary 섹션별 원문
  ├── step4_appendix_timeline.md        # 시계열 부록 (Chronological Appendix)
  ├── step4_final_en.md                 # 최종 하이브리드 백서 (영문)
  ├── step4_final_kr.md                 # 최종 하이브리드 백서 (한국어, 번역 시)
  └── pipeline_error.log                # 노드 실패 로그 (있을 경우)
```

**최종 백서 구조:** `Executive Summary` (2-3p 고압축 비즈니스 서사: 문제→결정→가치) → `Appendix: Chronological Timeline` (시계열 원시 이벤트, Undated 별도 섹션) → `§ Pipeline Audit Log` (unverified_sections, 카테고리 균형).

**DOCX 변환:** `python scripts/md_to_docx.py output/<timestamp>/step4_final_en.md`
**KB 추출:** `python -m main --export-kb project_kb.json` (외부 시스템 연동용)

---

## 14. Resume (중단 재개) 흐름

Step 단위 재개로 중간 실패 비용을 최소화한다.

```bash
python -m main --resume output/20260609_143000 --resume-from step3
```

지정 디렉토리에서 이전 Step 산출물을 `GraphState`에 복원 후 해당 Step부터 실행 재개. 누락 파일 시 에러 종료.

| `--resume-from` | 필요 파일 |
|-----------------|----------|
| `step2` | `step1_knowledge_base.json`, `step1_temporal_index.json` |
| `step3` | Step 1 + `step2_category_analyses.json`, `step2_narrative_flow.md` |
| `step4` | Step 1~2 + `step3_executive_summary.md` |
| `translate` | Step 1~3 + `step4_final_en.md` |

---

## 15. Knowledge Base 스키마 상세

`step1_knowledge_base.json` 구조:

```json
{
  "metadata": { "pipeline_version": "3.0", "created_at": "...", "total_entries": 87,
                 "category_counts": { "Architecture_and_Tech": 24, ... } },
  "categories": {
    "Architecture_and_Tech": [
      { "title": "...", "description": "...", "source_ref": "records.jsonl:42",
        "date_hint": "2025-08", "impact_level": "high" }
    ]
  }
}
```

- **`impact_level`:** `"critical"` | `"high"` | `"medium"` | `"low"`
- **`date_hint`:** ISO 형식 선호. 불명확 시 `null` (파이프라인 정상 처리).
- **`source_ref`:** 원본 파일명 + 행 번호. 추적 가능성(traceability) 보장.

---

## 16. 변경 이력

| 버전 | 일자 | 변경 내용 |
|------|------|----------|
| v2.0 | 2026-06-04 | 시계열 5-Phase 파이프라인 (초기 설계) |
| v3.0 | 2026-06-09 | 카테고리 우선 4-Step 전면 재설계. Knowledge Structuring → Narrative Flow → Executive Summary → Hybrid Assembly. 날짜 비의존성, 중간 산출물 보존, Resume 지원, Knowledge Base 외부 추출 추가. |
