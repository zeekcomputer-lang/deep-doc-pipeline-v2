# HANDOFF.md — 다음 AI Agent 인수인계 문서

> **프로젝트:** deep-doc-pipeline-v2 (v3.0)
> **GitHub:** https://github.com/zeekcomputer-lang/deep-doc-pipeline-v2 (PUBLIC)
> **로컬:** `~/.openclaw/workspace/projects/deep-doc-pipeline-v2/`
> **최종 업데이트:** 2026-06-11 (v3.1 — 완성 백서 + DOCX 자동화 + 사전 지식 주입)
> **상태:** ✅ **v3.1 구현 완료** — 12 노드 + 2 라우터. 제목+본문+시사점 완성 백서, DOCX 자동 생성. AST 14/14 PASS. LLM 실제 실행은 미수행(사용자 엔드포인트 대기).
> **원본:** deep-doc-pipeline v2.0에서 포크

---

> **⚡ 먼저 `STATUS.md`를 읽으세요** — 현재 상태 스냅샷(구현 완료 범위 / 미수행 작업 / 문서 신뢰도)을 30초 안에 파악.

## §0. 30초 요약

저성능 LLM(gpt-oss) 환경에서 JSONL 원시 문서 → **카테고리 기반 지식 구조화** → **경영진용 한글 백서**를 자동 생성하는 **LangGraph 파이프라인**.

v2는 시간순 우선(Phase 1-5)이었다. v3는 **카테고리 우선 4-Step** 아키텍처:

```
JSONL → 4-카테고리 지식 분류 → 카테고리별 심층 분석 → 경영 요약서 집필
     → 시간순 부록 포맷팅 → 본문+부록 조립 → 윤문 → END
```

**핵심 전환:** 시계열 종속 탈피 + **한국어 직접 출력(KR-first)**. EN-only+번역 패턴 폐기, 번역 소실 원천 차단. 고유명사만 원어 보존.

---

## §1. 버전 히스토리

| 버전 | 커밋 | 핵심 변경 |
|------|------|----------|
| **v3.0** | `1d5d55e`→`cc7c06e` | 카테고리 우선 지식 구조화, 4-Step 하이브리드 백서, 날짜 무관, **KR-first 직접 출력**, fact-checker 완전 제거 |
| v2.0 | `09fda16` | 번역 v2 + prompt_config 커스텀 |
| v1.5 | `50ed4fd` | 경량 워크플로우: 비교/검증 루프 제거 + DOCX 변환 |
| v1.4 | `a6677ea` | 504 국부 감축 + EN-only + best-of-N |

### v2→v3 주요 변경

| 항목 | v2 | v3 |
|------|----|----|
| **설계 철학** | 시간순 우선 (Phase 1-5) | 카테고리 우선 (Step 1-4) |
| **1차 분류** | 월별 그루핑 (`YYYY-MM`) | 4-카테고리 LLM 분류 |
| **분석 단위** | 월별 요약 → 전체 테마 | 카테고리별 심층 분석 → 교차 서사 |
| **기획** | 목차(Outline) 기획 루프 | 인과적 서사 흐름 + 비평 루프 |
| **시간축** | 핵심 구조 | 부록으로 분리 (best-effort) |
| **날짜 의존성** | 필수 (chrono_sorter) | 유연 (temporal_indexer graceful) |
| **출력 언어** | EN-only LLM + 후번역 | **한국어 직접 출력** (고유명사 원어 보존) |
| **출력** | 단일 MD (한글+영문) | 단계별 중간 산출물 + 최종 조립 |

---

## §2. 4-Step 아키텍처 상세

### Step 1 — 지식 구조화 (Knowledge Structuring)

원시 문서 로드 → LLM이 각 문서를 4개 카테고리로 분류 → 집계 → 시간순 인덱스 생성.

| 카테고리 | 설명 |
|---------|------|
| `Architecture_and_Tech` | 아키텍처 설계, 기술 스택, 인프라 결정 |
| `Risk_and_Troubleshooting` | 장애 대응, 리스크 식별, 해결 과정 |
| `Business_and_Feature` | 비즈니스 요구, 기능 구현, 성과 지표 |
| `Lessons_Learned` | 교훈, 회고, 개선 사항 |

산출물: `step1_knowledge_base.json` + `step1_temporal_index.json`
날짜 유연성: 날짜 없는 항목은 `"date": null`로 기록, 부록에서 "날짜 미상" 별도 처리.

### Step 2 — 서사 흐름 (Narrative Flow)

카테고리별 심층 분석(LLM ×4 병렬) → 교차 스토리라인 합성.
인과 체인: `문제 인식 → 핵심 의사결정 → 창출 가치 → 교훈`
비평 루프: `narrative_planner` ⟲ `narrative_critique` (최대 3회)
산출물: `step2_category_analyses.json` + `step2_narrative_flow.md`

### Step 3 — 경영 요약서 집필 (Executive Summary Writing)

2-3페이지 고압축 비즈니스 백서. 집필 루프: `section_writer` → `save_section` → `route_next_section`.
산출물: `step3_executive_summary.md`

### Step 4 — 완성 백서 조립 (Whitepaper Assembly)

제목(H1) + 본문 섹션(H2) + 시사점 섹션 → 윤문 → END → **DOCX 자동 생성**.
(v3.1: 월별 상세 타임라인 부록 제거. compiler가 Pure Python으로 제목+본문+시사점 조립.)
산출물: `step4_compiled.md` + `step4_final.md` + `백서.docx` + `proper_nouns.json`(완성 문서 고유명사 추출)

---

## §3. 전체 그래프 구조

```
START → load_docs → [fanout] knowledge_extractor(×N) → knowledge_aggregator → temporal_indexer
     → [fanout] category_analyzer(×4) → narrative_planner ⟲ narrative_critique
     → init_writing → section_writer → save_section ⟲ route_next_section
     → compiler → polish → END   (종료 후 main.py가 DOCX 자동 생성)
```

### 노드 목록 (13개 + 라우터 2개)

| Step | 노드 | 유형 | 설명 |
|------|------|------|------|
| 1 | `load_docs` | Python | `data/records.jsonl` 로드 |
| 1 | `knowledge_extractor` | LLM ×N | 문서별 카테고리 분류 + 구조화 추출 |
| 1 | `knowledge_aggregator` | Python | 카테고리별 집계 + 균형 경고 |
| 1 | `temporal_indexer` | Python | best-effort 날짜 추출 → 시간순 인덱스 |
| 2 | `category_analyzer` | LLM ×4 | 카테고리별 심층 분석 |
| 2 | `narrative_planner` | LLM | 교차 서사 흐름 설계 |
| 2 | `narrative_critique` | Python+LLM | 서사 구조 검증 (최대 3회) |
| 3 | `init_writing` | Python | 집필 상태 초기화 |
| 3 | `section_writer` | LLM | 서사 기반 섹션 집필 |
| 3 | `save_section` | Python | 완성 섹션 저장 |
| 4 | `compiler` | Python | 제목+본문+시사점 조립 (LLM 금지, 타임라인 부록 없음) |
| 4 | `polish` | LLM | 최종 윤문 |


**라우터:** `route_narrative` · `route_next_section`
**fanout:** `fanout_to_extractor` · `fanout_to_category_analyzer`

---

## §4. 파일 지도 + 출력 구조

```
├── main.py                  실행 진입점
├── requirements.txt         openai, langgraph, pydantic, python-dotenv
├── .env.example             OPENAI_BASE_URL / OPENAI_MODEL / LLM_MAX_RPM
├── data/records.jsonl       입력 JSONL
├── scripts/
│   ├── gen_dummy.py         더미 JSONL 생성기
│   └── md_to_docx.py       MD → DOCX 변환
└── src/
    ├── prompt_config.py     ★ 사용자 커스텀 프롬프트
    ├── schemas.py           Pydantic 스키마 (v3 신규)
    ├── state.py             GraphState v3
    ├── context_guard.py     95KB 예산 관리
    ├── artifacts.py         중간/최종 산출물 저장 + resume 상태 로드 (init_run_dir/save_json/save_text/load_run_state/list_runs)
    ├── llm.py               OpenAI SDK + Rate Limiter + 504
    ├── logger.py            타임라인 로거 + 에러 로그
    ├── utils.py             Pure Python 결정론 로직
    ├── nodes.py             13 노드 + 2 라우터 (v3)
    └── graph.py             build_graph() + build_resume_graph() v3
```

**출력:**
```
output/<timestamp>/
  ├── step1_knowledge_base.json       카테고리별 구조화 지식
  ├── step1_temporal_index.json       best-effort 시간순 인덱스
  ├── step1_raw_entries.json          원시 추출 엔트리 (디버그용)
  ├── step2_category_analyses.json    4개 카테고리 분석 결과
  ├── step2_narrative_flow.md / .json 교차 서사 흐름
  ├── step3_executive_summary.md      경영 요약서
  ├── step4_appendix_timeline.md      시간순 부록
  ├── step4_compiled.md               본문+부록 조립본 (윤문 전)
  └── step4_final.md                  최종 하이브리드 백서 (한국어, 고유명사 원어)
```

---

## §5. 핵심 방어 기제

| # | 위험 | 방어 | 구현 위치 |
|---|------|------|----------|
| 1 | 504 타임아웃 | 국부 감축 (-5KB/step) + reasoning 다운그레이드 | `llm.py` |
| 2 | 95KB 초과 | `effective_budget()` 사전 측정 + 분할/압축 | `context_guard.py` |
| 3 | 고유명사 원어 변형 | `_KR_PROPER_NOUN_PRESERVE` 가드로 전 LLM 노드에 고유명사 원어 유지 강제 | 전 LLM 노드 프롬프트 |
| 4 | 날짜 유연 처리 | `temporal_indexer` graceful — `null` 날짜 허용 | `temporal_indexer` |
| 5 | 카테고리 불균형 | `knowledge_aggregator` 경고 로그 + 비율 보고 | `knowledge_aggregator` |
| 6 | 지식 베이스 내보내기 | `--export-kb` 독립 JSON 추출 | `main.py` |

---

## §6. 절대 준수 사항

v2에서 검증된 8개 제약. v3에서도 **동일 적용**.

1. **순수 OpenAI SDK** — `openai.OpenAI()` 직접 사용. LangChain LLM 래퍼 금지.
2. **Pydantic 강제 출력** — `structured_call()` → `extract_json()` → `model_validate()`.
3. **`response_format` 금지** — GPT-OSS 호환. 프롬프트 가드 + 파서로 JSON 강제.
4. **Pure Python 영역** — `utils.py`, `compiler`, `knowledge_aggregator`, `temporal_indexer`에서 LLM 금지.
5. **한국어 직접 출력 (KR-first)** — 전 Step 한국어 출력, 고유명사만 원어 보존. 번역 단계 없음.
6. **user 메시지 불변** — 절단 금지. 분할로만 해결.
7. **504 국부 감축** — 실패 노드만 축소, 성공 후 원복.
8. **95KB per-call** — `effective_budget()` + `available_data_budget()` 준수.

---

## §7. 프롬프트 커스텀 + 사전 지식 주입

`src/prompt_config.py`만 편집. 주요 필드:

| 필드 | 설명 | 적용 범위 |
|------|------|----------|
| `DOCUMENT_TYPE` | ★ 문서 유형 (whitepaper/executive_brief/postmortem/tech_report/status_update/custom) — 카테고리와 무관하게 목적별 구조 설계 | narrative_planner |
| `DOCUMENT_TYPE_CUSTOM_STRUCTURE` | custom 유형일 때 직접 지정하는 섹션 구성 | narrative_planner |
| `DOCUMENT_TITLE` | 표지 제목 고정 (비우면 LLM 자동 생성) | 최종 문서 H1 |
| `DOCUMENT_PURPOSE` | 문서 목적 (비우면 유형별 기본값) | 전 LLM 노드 |
| `DOMAIN_KNOWLEDGE` | ★ LLM이 모르는 도메인 지식·단계·주의사항 | 전 LLM 노드(추출·분석·집필) |
| `KEY_TERMS` | 용어집 {용어: 정의} | 전 LLM 노드 |
| `INCLUDE_TEMPORAL_CONTEXT` | 시점(날짜) 참고 정보 제공 — 날짜를 참고용으로 제공, 서술에 도움 될 때만 반영(강제 아님, 기본 True) | 분석·집필 |
| `TONE_DIRECTIVE` | 톤 지시 | 분석·집필 |
| `TARGET_AUDIENCE` | 대상 독자 | 집필 |
| `CUSTOM_DIRECTIVES` | 추가 지시 | 집필(section_writer) |

> **사전 지식 주입 목적:** 개발 단계 명칭, 사내 약어, 방법론 등 일반 LLM이 모르거나 오해하기 쉬운 지식을 프롬프트로 주입해 어텐션을 집중시키고 환각을 줄인다.

### 구 Step별 적용 매핑 (참고):

| 설정 | Step 1 (extractor) | Step 2 (analyzer, planner) | Step 3 (writer) | Step 4 (formatter) |
|------|:---:|:---:|:---:|:---:|:---:|
| `DOCUMENT_PURPOSE` | ✓ | ✓ | ✓ | ✓ |
| `TONE_DIRECTIVE` | — | ✓ | ✓ | — |
| `TARGET_AUDIENCE` | — | — | ✓ | ✓ |
| `CUSTOM_DIRECTIVES` | — | — | ✓ | — |

```python
# 예시: 기술 경영진 대상
DOCUMENT_PURPOSE = "프로젝트 기술 의사결정과 비즈니스 성과를 연결하는 경영 백서"
TONE_DIRECTIVE = "객관적이되 성과를 명확히 드러내는 톤"
TARGET_AUDIENCE = "CTO/VP Engineering — 기술 깊이와 비즈니스 임팩트 동시 요구"
CUSTOM_DIRECTIVES = "매 섹션에 '의사결정 근거'와 '정량적 성과' 포함"
```

---

## §8. 실행 방법

```bash
# 셋업
pip install -r requirements.txt
cp .env.example .env                      # OPENAI_BASE_URL, OPENAI_MODEL 편집

# 데이터 생성 + 실행
python -m scripts.gen_dummy
python -m main                            # 기본 (한국어 직접 출력)
python -m main --export-kb kb.json        # 지식 베이스 JSON 내보내기
python -m main --reasoning medium         # 타임아웃 회피
python -m main --resume <dir> --resume-from <step>   # 중단점 재개
python -m main --list-runs                # 이전 실행 목록
```

| 환경변수 | 기본값 | 설명 |
|---------|-------|------|
| `OPENAI_BASE_URL` | — | LLM 엔드포인트 |
| `OPENAI_MODEL` | gpt-oss-20b | 기본 모델 |
| `EXTRACTOR_MODEL` / `JUDGE_MODEL` / `WRITER_MODEL` | (OPENAI_MODEL) | 역할별 분리 (JUDGE=비평) |
| `LLM_MAX_RPM` | 12 | 분당 최대 호출 |
| `LLM_MAX_CONCURRENT` | 5 | 동시 호출 상한 |
| `LLM_CONTEXT_BUDGET_KB` | 95 | per-call 컨텍스트 예산 (KB) |

---

## §9. 다음 AI Agent 시나리오

| 시나리오 | 요청 | 행동 |
|---------|------|------|
| **A** | "v3 노드 구현해라" | **이미 구현 완료.** 변경 요청 시 `SPEC.md` 숙독 → `state.py`/`schemas.py` → `nodes.py` → `graph.py` 순으로 수정 |
| **B** | "파이프라인 실행" | `.env` 셋업 → `gen_dummy` → `python -m main` |
| **C** | "프롬프트 커스텀" | `src/prompt_config.py`만 편집 (§7 참조) |
| **D** | "지식 베이스 내보내기" | `python -m main --export-kb output.json` |
| **E** | "데이터에 날짜 없음" | 정상 — `temporal_indexer`가 graceful 처리. `step1_temporal_index.json` 확인 |
| **F** | "카테고리 추가" | `schemas.py` KnowledgeEntry.category enum + `nodes.py` knowledge_extractor 프롬프트 |
| **G** | "중간부터 재실행" | `python -m main --resume <output_dir> --resume-from <step>` (step 값: `step2`/`step3`/`step4`/`polish`) |

---

## §10. 디버깅 체크리스트 + 첫 5분

### 디버깅

| 증상 | 확인 |
|------|------|
| 504 반복 | `--reasoning medium` 또는 `LLM_CONTEXT_BUDGET_KB` 하향 |
| 카테고리 불균형 | `knowledge_extractor` 프롬프트 점검, 카테고리 정의 조정 |
| 서사 흐름 비어있음 | `step2_category_analyses.json` 확인 — 분석 결과 부재 시 Step 2 재실행 |
| JSON 파싱 실패 | `structured_call` retry 로그, `extract_json` 단계 확인 |
| 날짜 누락 | 정상 동작 — `step1_temporal_index.json`에서 `null` 항목 확인 |
| 부록이 빈약 | `temporal_index` 항목 부족 → 원시 데이터에 시간 단서 부족 |

| 빈 최종 출력 | `compiler` → `completed_sections` 확인 |
| 노드 실패 추적 | `pipeline_error.log` (타임스탬프·노드명·스택트레이스) |

### 첫 5분

- [ ] §0~§3 읽기 (30초 요약 + 아키텍처 + 그래프)
- [ ] `SPEC.md` 설계 명세서 숙독
- [ ] `src/state.py` + `src/schemas.py` 데이터 모델 파악
- [ ] `src/nodes.py` Step 주석 + 라우터 함수 훑기
- [ ] `git log --oneline` 히스토리 확인
- [ ] 사용자 요청 → 시나리오 A~G 분류 (§9)
- [ ] 코드 수정 시 AST 검증 후 커밋
