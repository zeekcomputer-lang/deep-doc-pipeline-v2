# Deep Doc Pipeline — 설계 명세서 v3.1

> **버전:** v3.1 (완성 백서 + DOCX 자동화 + 사전 지식 주입)
> **v3.1 변경 요약:** 월별 상세 타임라인 부록 제거(`timeline_formatter` 삭제) · 최종문서 = 제목+본문+시사점 · DOCX 자동 생성 · 사전 지식 주입(DOMAIN_KNOWLEDGE/KEY_TERMS)
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
| **출력 언어** | EN-only LLM + 후번역(EN→KR) | **한국어 직접 출력** (고유명사만 원어 보존) |

**유지 항목:** 순수 OpenAI SDK, Pydantic 강제 출력, 95KB 하드리밋, 504 국부 감축, user 메시지 불변, `prompt_config.py` 커스텀.
**v2에서 제거:** EN-only LLM 제약 + 번역 노드 2개 (`prepare_translation`, `translate`). 번역 소실 이슈를 원천 차단.

---

## 1. 아키텍처 설계 철학

### 1.1 핵심 설계 원칙 4대 항목

| # | 원칙 | 설명 |
|---|------|------|
| 1 | **카테고리 우선 지식 구조화** | 원시 데이터를 시간이 아닌 비즈니스 의미 축으로 먼저 분류. 날짜 없는 데이터도 처리 가능. |
| 2 | **극단적 마이크로 태스킹** | One Node = One Task. 분류·분석·집필·검수를 하나의 프롬프트에 섞지 않음. |
| 3 | **인과 스토리라인 우선** | 시간순 나열이 아닌 **문제→결정→가치** 서사 구조로 Executive Summary 작성. |
| 4 | **결정론적 로직 우선** | 카테고리 집계·날짜 추출·문서 조립은 Pure Python. LLM에 맡기지 않음. |

### 1.2 추가 설계 원칙

- **한국어 직접 출력 (KR-first)**: 모든 Step에서 한국어로 직접 출력. 고유명사(기술 용어, 제품명, 약어 등)만 원어 보존. 번역 단계 자체를 제거하여 번역 소실 이슈를 원천 차단.
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
5. **한국어 직접 출력 (KR-first)** — 전 Step 한국어 출력. 고유명사(기술 용어·제품명·약어)만 원어 보존. `_KR_PROPER_NOUN_PRESERVE` 가드로 고유명사 원어 유지 강제.

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
    completed_sections: Annotated[Dict[int, str], update_dict]   # idx → 완성 텍스트

    # ── Step 4: Whitepaper Assembly ────────────────────────────
    document_title: str                                          # 백서 표지 제목
    key_implications: List[str]                                  # 핵심 시사점
    executive_summary: str                                       # 조립된 본문(제목+섹션)
    final_compiled: str                                          # 최종 조합 문서(제목+본문+시사점)
    final_output: str                                            # 윤문 완료 최종본

```

**Reducer:** `operator.add` = 리스트 누적 병합 (fanout 수집), `update_dict` = 딕셔너리 키 단위 업데이트.

---

## 5. Pydantic 응답 스키마 (6종 활성 + 1종 비활성)

| 스키마 | 용도 (Step) | 핵심 필드 |
|--------|-------------|----------|
| `KnowledgeEntry` | 문서 1건 분류·추출 (1) | `category`, `title`, `description`, `source_ref`, `date_hint?`, `impact_level` |
| `CategoryAnalysis` | 카테고리별 심층 분석 (2) | `category`, `key_findings[]`, `causal_chain`, `implications` |
| `NarrativeFlow` | 백서 제목+서사+섹션+시사점 (2) | `document_title`, `storyline`, `section_plan[{title, category_refs[], intent}]`, `key_implications[]` |
| `NarrativeCritique` | 서사 검증 (2) | `is_approved`, `feedback` |
| `SectionDraft` | 섹션 본문 집필 (3) | `content` |
| `TimelineEntry` | (v3.1 미사용 — 스키마만 잔존) | `period`, `events[]`, `significance` |
| `PolishedDocument` | 윤문 출력 (4) | `content` |

**공통 규칙:** 모든 스키마는 `src/schemas.py` 정의. LLM 응답 → `extract_json()` → `model_validate()` 3단 파싱. 실패 시 최대 2회 재시도 → `ValidationError`.

---

## 6. 그래프 구조 — 12 노드 + 2 라우터

```
START → load_docs → [fanout] knowledge_extractor(×N) → knowledge_aggregator → temporal_indexer
  → [fanout] category_analyzer(×4) → narrative_planner ⟲ narrative_critique (최대 3회)
  → init_writing → section_writer → save_section ⟲ route_next_section
  → compiler (Pure Python) → polish → END   (종료 후 main.py가 DOCX 자동 생성)
```

> **v2 대비 제거:** `prepare_translation`, `translate` 노드 완전 제거. 전 Step이 한국어로 직접 출력하므로 번역 단계 불필요.



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
| `section_writer` | LLM | `executive_sections[current_section_index]`의 `category_refs`에 해당하는 `knowledge_base` 엔트리만 주입하여 섹션 본문 집필. 95KB 초과 시 엔트리 배치 분할→병합. |
| `save_section` | Python | 완성된 섹션을 `completed_sections[idx]`에 저장. `current_section_index` 증가. |

**라우터 로직 (`route_next_section`):**
```
save_section →
  ├─ current_section_index < len(executive_sections) → section_writer (다음 섹션)
  └─ 모든 섹션 완료 → compiler (Step 4)
```

**prompt_config 연동:** `section_writer` → `get_writing_context()` (PURPOSE + TONE + AUDIENCE + CUSTOM)

### Step 4: Whitepaper Assembly (완성 백서 조립)

제목 + 본문(섹션) + 시사점을 Pure Python으로 조립하고 윤문 후 DOCX로 자동 변환한다.
**(v3.1: 월별 상세 타임라인 부록 제거 — `timeline_formatter` 노드 삭제.)**

| 노드 | 유형 | 설명 |
|------|------|------|
| `compiler` | Python | `document_title`(H1) + `completed_sections`(본문 H2 섹션) + `key_implications`(시사점 섹션) 조립. **LLM 호출 금지.** 타임라인 부록·감사로그는 최종문서에 미포함. |
| `polish` | LLM | 최종 문서의 문체·연결어·일관성 윤문. 섹션별 분할 처리(504 방지). **사실 변경·추가 절대 금지.** |
| (post) `build_whitepaper_docx` | Python | `main.py`가 파이프라인 종료 시 호출. 최종 마크다운 → 세련된 Word 백서(`백서.docx`). `--no-docx`로 비활성화. |

**제목·시사점 생성:** `narrative_planner`(Step 2)가 NarrativeFlow 스키마의 `document_title` + `key_implications`를 함께 생성. `DOCUMENT_TITLE`이 설정되면 그것을 우선 사용.

### 고유명사 원어 보존 전략

KR-first 아키텍처에서 기술 용어·제품명·약어는 원어 보존이 필수이다.

- `_KR_PROPER_NOUN_PRESERVE` 가드를 모든 LLM 노드 프롬프트에 주입
- 대상: 영어 약어(2+대문자), CamelCase 용어, 제품/프레임워크명, 기술 표준명
- 프롬프트 예시: `"기술 고유명사(약어, 제품명, 프레임워크명 등)는 원어 그대로 사용하십시오. 예: LangGraph, FastAPI, Kubernetes"`

---

## 8. prompt_config.py 연동 매트릭스

| 함수 | 호출 노드 | PURPOSE | TONE | AUDIENCE | CUSTOM | 언어 |
|------|----------|---------|------|----------|--------|------|
| `get_extraction_context()` | `knowledge_extractor` | ✅ | — | — | — | KO |
| `get_analysis_context()` | `category_analyzer`, `narrative_planner` | ✅ | ✅ | — | — | KO |
| `get_writing_context()` | `section_writer` | ✅ | ✅ | ✅ | ✅ | KO |
| `get_domain_knowledge()` | 전 LLM 노드 (자동 주입) | DOMAIN_KNOWLEDGE + KEY_TERMS | KO |

> **사전 지식 주입 (v3.1):** `DOMAIN_KNOWLEDGE`(자유 텍스트) + `KEY_TERMS`(용어집 dict)가 `_build_domain_block()`을 통해 추출·분석·집필 노드 system 프롬프트에 자동 주입된다. `DOCUMENT_TITLE`로 표지 제목 고정 가능.

> **시점 본문 반영 (Temporal Anchoring, v3.1):** `INCLUDE_TEMPORAL_CONTEXT=True`(기본)이면 `_build_temporal_block()`이 분석·집필 노드에 "날짜 단서가 있는 사안은 본문 서술에 시점을 녹이라"는 지시를 주입한다. 데이터는 `format_entries_for_prompt`가 `[YYYY-MM-DD]`로 이미 전달. 날짜 없는 항목은 시점을 강제하지 않아 환각 방지. 월별 상세 타임라인 '부록'과는 별개(부록은 제거됨, 이건 본문 서술에 시점을 녹임).

**v2→v3 변경:** `get_summary_context()` → `get_extraction_context()` + `get_analysis_context()` 분리. `get_planning_context()` → `get_analysis_context()`로 통합. `get_translation_context()` 제거 (KR-first로 번역 단계 자체 불필요). 신규: `get_extraction_context()`, `get_assembly_context()`. 전체 언어 KO 통일.

---

## 9. 방어 기제

| # | 위험 | 방어 | 구현 위치 |
|---|------|------|----------|
| 1 | 504 타임아웃 | 국부 감축 (-5KB/step) + reasoning 다운그레이드 + 노드 재실행 | `@retry_on_504` / `llm.py` |
| 2 | 95KB 초과 | `effective_budget()` 사전 측정 + 분할/압축 | `context_guard.py` / 각 노드 |
| 3 | 고유명사 원어 변형 | `_KR_PROPER_NOUN_PRESERVE` 가드로 전 LLM 노드에 고유명사 원어 유지 강제 | 전 LLM 노드 프롬프트 |
| 4 | 날짜 부재 | `temporal_indexer` best-effort: 정규식 폴백 → `"undated"` 그룹 | `temporal_indexer` |
| 5 | 카테고리 불균형 | `knowledge_aggregator`에서 0건 카테고리 경고 + 최종 문서 주석 | `knowledge_aggregator` |
| 6 | Knowledge DB 유실 | Step별 중간 산출물 JSON 파일 자동 저장 | 각 Step 종료 시 |
| 7 | 에러 추적 | `pipeline_error.log` (타임스탬프·노드·스택트레이스) | `logger.py` / `main.py` |

---

## 10. 실행 옵션

### CLI

```bash
python -m main                                        # 기본 (reasoning=high, 한국어 직접 출력)
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
| `JUDGE_MODEL` | (OPENAI_MODEL) | 비평 모델 |
| `LLM_MAX_RPM` | `12` | 분당 최대 호출 |
| `LLM_MAX_CONCURRENT` | `5` | 동시 호출 상한 |
| `LLM_CONTEXT_BUDGET_KB` | `95` | per-call 컨텍스트 예산 (KB) |

---

## 11. 프롬프트 커스텀

`src/prompt_config.py` 파일을 편집하여 백서의 톤·목적·편향을 조정할 수 있다.

| 설정 | 기본값 | 적용 Step |
|------|--------|----------|
| `DOCUMENT_PURPOSE` | "카테고리 기반 하이브리드 프로젝트 백서" | Step 1~4 |
| `TONE_DIRECTIVE` | `""` (중립 객관) | Step 2~3 |
| `TARGET_AUDIENCE` | `""` (일반 독자) | Step 3~4 |
| `CUSTOM_DIRECTIVES` | `""` | Step 3 집필만 |

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
    ├── schemas.py           Pydantic 응답 스키마 7종
    ├── state.py             GraphState v3 + reducer
    ├── context_guard.py     95KB 예산 관리
    ├── llm.py               OpenAI SDK + Rate Limiter + 504 방어
    ├── logger.py            타임라인 로거 + 에러 로그
    ├── utils.py             Pure Python 결정론 로직
    ├── nodes.py             13개 노드 + 2개 라우터
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
  ├── step4_final.md                    # 최종 백서 (한국어, 고유명사 원어 보존)
  ├── 백서.docx                       # 완성 Word 백서 (자동 생성)
  ├── proper_nouns.json                 # 완성 문서에서 추출한 고유명사 (재사용용, terms + 유형별 categories)
  └── pipeline_error.log                # 노드 실패 로그 (있을 경우)
```

**고유명사 추출 (추가 출력 단계):** 파이프라인 종료 후 `main.py`가 최종 문서(`final_output`)에서 `export_proper_nouns()`로 고유명사를 추출해 `proper_nouns.json` 저장. `terms`(평탄 목록) + `categories`(dates/acronyms/camelcase/capitalized/phrases/metrics/code). 기존 파이프라인에 영향 없는 독립 출력 단계. `--proper-nouns <FILE>`로 추가 경로 지정 가능.

**최종 백서 구조:** `# 제목` (H1 표지) → `## 본문 섹션` (1~2p 고압축 비즈니스 서사: 문제→결정→가치, 2~4 섹션) → `## 시사점 및 제언` (핵심 제언 3~5건). 월별 상세 타임라인 부록 없음. 카테고리 균형 경고는 콘솔/로그로만 보고.

**DOCX 변환:** `python scripts/md_to_docx.py output/<timestamp>/step4_final.md`
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
| v3.0 | 2026-06-09 | 카테고리 우선 4-Step 전면 재설계. Knowledge Structuring → Narrative Flow → Executive Summary → Hybrid Assembly. 날짜 비의존성, 중간 산출물 보존, Resume 지원, Knowledge Base 외부 추출 추가. **한국어 직접 출력(KR-first)**: EN-only+번역 패턴 폐기, 번역 노드 제거, 고유명사 원어 보존. |
