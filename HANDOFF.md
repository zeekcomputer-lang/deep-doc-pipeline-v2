# HANDOFF.md — 다음 AI Agent 인수인계 문서

> **프로젝트:** deep-doc-pipeline (v2.0)
> **GitHub:** https://github.com/zeekcomputer-lang/deep-doc-pipeline (PUBLIC)
> **로컬:** `~/.openclaw/workspace/projects/deep-doc-pipeline/`
> **최종 업데이트:** 2026-06-04
> **상태:** v2.0 코드 완성 + AST 검증 통과 / **실제 LLM 실행 미수행** (사용자 환경 보류)

---

## §0. 30초 요약

저성능 LLM(gpt-oss) 환경에서 **환각을 구조적으로 차단**하며 200건 JSONL → **한글 백서**를 자동 생성하는 **LangGraph 파이프라인**.

```
JSONL → 영어로 추출·분석·집필 → 영문 백서 → 한국어 충실 번역 → 최종 한글 백서 + DOCX
```

**규모:** 12 Python 파일 / 3,080줄 (src 2,632 + scripts 316 + main 132). AST 전 파일 통과.

---

## §1. 버전 히스토리

| 버전 | 커밋 | 핵심 변경 |
|------|------|----------|
| **v2.0** | `09fda16` | 번역 v2 + prompt_config 커스텀 + --skip-fact-check + 에러로그 + 리팩토링 |
| v1.5 | `50ed4fd` | 경량 워크플로우: 비교/검증 루프 제거 + DOCX 변환 스크립트 |
| v1.4 | `a6677ea` | 504 국부 감축 + max_tokens + reasoning + 영문 분리 + best-of-N |
| v1.3 | `4d776f1` | EN-only LLM + 백서 전용 + 수석 에디터 렌더링 |
| v1.1 | `73d6d9c` | 초기 구현 — 구조적 위험 3건 보강 |

### v2.0 (현재)

**번역 단계 전면 개선:** v1.5에서 20,000단어→6,000단어 소실 문제 해결.
```
변경: translate_node v1 (Path A: 전체 1회 호출 / Path B: 월별 렌더링)
    → translate_node v2 (항상 섹션별 → 문단별 분할 번역 → 소스데이터 폴백)
```
**핵심 변경:**
- `_build_render_prompt` 제거 → `_build_faithful_translate_prompt` + `_build_section_translate_prompt` 대체
- `_build_korean_gen_prompt`: 번역 실패 시 extracted events로 한글 직접 생성
- `_check_completeness`: 한글/영문 문자 비율 ≥ 0.35 검증
- `_split_into_paragraph_chunks`: 8KB 단위 문단 분할
- 이중 `@retry_on_504` 버그 수정
- LESSONS L-020 추가

### v1.5

**경량화:** 비교/검증 루프 전면 제거 (-8 노드, -322줄).
```
이전: compiler → polish → final_fact_checker ⟲ → prepare → translate → translation_checker ⟲ → END
이후: compiler → polish → prepare_translation → translate → END
```

**추가:** `scripts/md_to_docx.py` — 마크다운 백서 → DOCX 변환 (개별 구동).

**유지되는 검증:** section별 `fact_checker` (원본 데이터 대조, `--skip-fact-check`로 생략 가능), `extract_proper_nouns` (번역 프롬프트에 고유명사 주입).

**LLM 제어:**
- `max_tokens=24,000` (95KB 이내 응답 강제)
- `reasoning_effort`: 기본 "high", 504 2회 초과 시 "medium" 자동 전환, `--reasoning medium` CLI
- temperature: 역할별 차등 (extractor/judge 0.0, writer 0.3, polish 0.1, translate 0.2)
- 504 방어: 국부 감축 (-5KB/step, 최대 10단계), 노드 재실행, 성공 후 원복

**출력:** `output.md` (한글) + `output_en.md` (영문 원본, 항상 생성) + `output.docx` (선택)

---

## §2. 파일 지도

```
├── main.py                  실행 진입점 (132줄)
├── requirements.txt         openai, langgraph, pydantic, python-dotenv, python-docx
├── .env.example             OPENAI_BASE_URL / OPENAI_MODEL / LLM_MAX_RPM
├── pipeline_error.log       노드 단위 실패 로그 (자동 생성, append)
├── data/records.jsonl       입력 JSONL (gen_dummy.py로 생성)
├── scripts/
│   ├── gen_dummy.py         더미 JSONL 15건 생성기 (81줄)
│   └── md_to_docx.py       마크다운 → DOCX 변환 (235줄)
└── src/
    ├── prompt_config.py     ★ 사용자 커스텀 프롬프트 설정 (목적/톤/독자/지시) (191줄)
    ├── schemas.py           Pydantic 스키마 7종 (84줄)
    ├── state.py             GraphState (49줄)
    ├── context_guard.py     95KB 예산 관리 (164줄)
    ├── llm.py               OpenAI SDK + Rate Limiter + 504 방어 (469줄)
    ├── logger.py            타임라인 로거 + 에러 로그 (115줄)
    ├── utils.py             Pure Python 결정론 로직 (233줄)
    ├── nodes.py             17개 노드 + 5개 라우터 (1,236줄)
    └── graph.py             LangGraph 조립 (91줄)
```

---

## §3. 전체 그래프 구조

```
START → load_docs → [fanout] strict_extractor(×N) → chrono_sorter     Phase 1: 추출
     → [fanout] period_summarizer(×M) → theme_analyzer                Phase 2: 요약
     → draft_planner ⟲ planner_critique                               Phase 3: 기획 루프
     → init_writing → section_writer ⟲ fact_checker                   Phase 4: 집필 루프
     → compiler → polish                                              Phase 4: 조립·윤문
     → prepare_translation → translate → END                          Phase 5: 번역
```

**검증 루프:** 기획(outline) + 집필(section) 2곳만 유지. 윤문·번역은 직선.

---

## §4. 핵심 방어 기제

| # | 위험 | 방어 |
|---|------|------|
| 1 | Fact-checker 회귀 | `previous_draft` + `hallucinated_tokens` 블랙리스트 주입 |
| 2 | Fail-Safe 강제통과 | ⚠️ 워터마크 삽입 + `unverified_sections` 감사 로그 |
| 3 | 504 타임아웃 | 국부 감축 (-5KB/step) + reasoning 다운그레이드 + 노드 재실행 |
| 4 | 95KB 초과 방지 | `effective_budget()` 전역 참조 + `available_data_budget` 연동 |
| 5 | 고유명사 보존 | `extract_proper_nouns` → 번역 프롬프트에 목록 주입 |
| 6 | 번역 콘텐츠 소실 | 섹션별 번역 + 완전성검증 + 문단분할 + 소스데이터 폴백 |

---

## §5. 절대 준수 사항

1. LangChain LLM 래퍼 사용 금지 — 오직 `openai.OpenAI()` 직접 사용
2. 모든 LLM 응답은 Pydantic 강제 — `structured_call()` → `extract_json()` → `model_validate()`
3. `response_format` 인자 사용 금지 — 프롬프트 가드 + 3단 파서
4. Pure Python 영역에 LLM 호출 금지 — `utils.py`, `chrono_sorter`, `compiler`
5. 영어 출력 노드는 `_EN_ENFORCE` 필수, 번역 노드는 미사용
6. **user 메시지 절단 금지** — 504는 노드 재실행(분할 로직 재생성)으로 처리
7. **504 감축은 국부적** — 실패 노드만 축소, 성공 후 원복
8. **API 요청 95KB 이하** — `effective_budget()` + `available_data_budget(budget_override=)` 준수

---

## §6. 프롬프트 커스텀 (사용자 편집)

`src/prompt_config.py` 파일만 편집하면 백서의 톤/목적/편향을 조정할 수 있습니다.

| 설정 | 기본값 | 적용 단계 |
|------|--------|----------|
| `DOCUMENT_PURPOSE` | "가독성이 뛰어난 기간별 이벤트 기반 백서" | 요약·기획·집필·번역 |
| `TONE_DIRECTIVE` | "" (중립 객관) | 요약·집필·번역 |
| `TARGET_AUDIENCE` | "" (일반 독자) | 기획·집필·번역 |
| `CUSTOM_DIRECTIVES` | "" | 집필만 |

**커스텀 예시 (긍정 편향 보고서):**
```python
DOCUMENT_PURPOSE = "투자자 대상 성장 스토리 백서"
TONE_DIRECTIVE = "긍정적 성과와 성장세를 강조하되, 사실에 기반할 것"
TARGET_AUDIENCE = "C-레벨 경영진 — 핵심 수치와 의사결정 포인트 중심"
CUSTOM_DIRECTIVES = "매 섹션 말미에 '시사점' 문단 추가"
```

**⚠️ 안전장치:** 편향 설정과 무관하게 `fact_checker`가 원본 데이터 외 사실 추가를 여전히 차단합니다.

---

## §7. 실행 방법

```bash
# 환경 셋업
pip install -r requirements.txt
cp .env.example .env  # OPENAI_BASE_URL, OPENAI_MODEL 편집

# 데이터 생성
python -m scripts.gen_dummy

# 백서 생성
python -m main                         # reasoning=high, 팩트체크 ON (기본)
python -m main --reasoning medium      # 서버 타임아웃 회피 우선
python -m main --skip-fact-check       # 팩트체크/환각검증 생략 (빠른 실행)
python -m main --output report.md      # 출력 경로 지정

# DOCX 변환 (개별 구동)
python scripts/md_to_docx.py output.md                     # → output.docx
python scripts/md_to_docx.py output.md output_en.md        # 한영 병합
python scripts/md_to_docx.py output.md -o whitepaper.docx  # 파일명 지정
```

---

## §8. 다음 AI Agent 시나리오

### A: "실행 결과 이상하다"
→ `[fact_checker]`, `[section_writer]`, `[504_retry]` 로그 확인

### B: "200건 실제 데이터"
→ `.env` 모델 분리, `LLM_MAX_RPM` 조정, `--reasoning medium` 권장

### C: "번역 스타일 변경"
→ `src/nodes.py` `_build_faithful_translate_prompt()` / `_build_section_translate_prompt()` 수정

### D: "검증 루프 복원"
→ git history `a6677ea` (v1.4) 참조. `final_fact_checker`, `translation_checker` 코드 복원 가능

### E: "빠른 실행 / 팩트체크 생략"
→ `python -m main --skip-fact-check` — 집필 루프 1회, LLM ~40% 절감

---

## §9. 디버깅 체크리스트

| 증상 | 확인 |
|------|------|
| 504 반복 | `--reasoning medium` 또는 `LLM_CONTEXT_BUDGET_KB` 하향 |
| JSON 파싱 실패 | `[structured_call] retry` 로그, extract_json 단계 확인 |
| 고유명사 누락 | `extract_proper_nouns` 출력 점검, 패턴 추가 |
| 번역 톤 불일치 | `_build_faithful_translate_prompt()` 스타일 가이드 조정 |
| 번역 콘텐츠 소실 | `_KR_EN_CHAR_RATIO_MIN` 임계값 조정 (default 0.35) / `_PARAGRAPH_CHUNK_MAX_BYTES` 축소 |
| 빈 final_output | `[compiler] sections=0` → save_section 동작 점검 |
| 노드 실패 추적 | `pipeline_error.log` 확인 (타임스탬프·노드명·스택트레이스) |

---

## §10. 첫 5분 체크리스트

- [ ] 이 문서 §0~§4 읽기
- [ ] `src/nodes.py` Phase 주석 + 라우터 함수 훑기
- [ ] `git log --oneline` 히스토리 확인
- [ ] 사용자 첫 메시지 → 시나리오 A/B/C/D 분류
- [ ] 코드 수정 시 AST 검증 후 커밋
- [ ] 번역 콘텐츠 소실 시 LESSONS L-020 참조
