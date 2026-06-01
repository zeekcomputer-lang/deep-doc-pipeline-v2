# HANDOFF.md — 다음 AI Agent 인수인계 문서

> **프로젝트:** deep-doc-pipeline (v1.3.1)
> **GitHub:** https://github.com/zeekcomputer-lang/deep-doc-pipeline (PUBLIC)
> **로컬:** `~/.openclaw/workspace/projects/deep-doc-pipeline/`
> **최종 업데이트:** 2026-06-01
> **상태:** v1.3.2 코드 완성 + AST 검증 통과 / **실제 LLM 실행 미수행** (사용자 환경 보류)

---

## §0. 30초 요약

저성능 LLM(gpt-oss 등) 환경에서 **환각을 구조적으로 차단**하며 200건 JSONL → **한글 백서**를 자동 생성하는 **LangGraph 파이프라인**.

**핵심 파이프라인 흐름:**
```
JSONL 문서 → 영어로 추출·분석·집필 → 영문 백서 → 수석 에디터 스타일 한국어 렌더링 → 최종 한글 백서
```

**기술 스택:** 순수 OpenAI SDK + Pydantic 강제 출력 + 다단 자가검증 루프 + Fail-Safe 워터마크.
**규모:** 12 파일 / 2,432줄. AST 문법 검증 전 파일 통과.

---

## §1. 버전 히스토리 (역순)

| 버전 | 커밋 | 날짜 | 핵심 변경 |
|------|------|------|----------|
| **v1.3.2** | `1f17934` | 2026-06-01 | 타임라인 로거 + 실행 통계 + 미사용 코드/주석 정리 |
| v1.3.1 | `94f9128` | 2026-06-01 | 수석 에디터 스타일 가이드 렌더링 + `## 연도` / `### 연도 월:` 구조 검증 |
| **v1.3** | `4d776f1` | 2026-06-01 | EN-only LLM + 백서 전용 + EN→KR 번역 단계 신설 |
| v1.1-r4 | `e037022` | 2026-05-29 | 95KB 컨텍스트 예산 하드리밋 전면 적용 |
| v1.1-r3 | `ec86ac2` | 2026-05-29 | Streaming + Section Chunking (504 타임아웃 해소) |
| v1.1-r2 | `5b2959b` | 2026-05-29 | Rate Limiter (12 RPM / 5 concurrent) + 헤더 인증 |
| v1.1-r1 | `442f9ba` | 2026-05-29 | GPT-OSS 표준 정렬 — response_format 제거, extract_json 3단 파서 |
| v1.1 | `73d6d9c` | 2026-05-27 | 초기 구현 — 구조적 위험 3건 보강 |

### v1.3.1 (현재) — 3대 아키텍처 변경

1. **영어 전용 LLM 출력:** 모든 프롬프트에 `_EN_ENFORCE` 접미사 적용. JSON guard는 언어 중립(한국어 렌더링과 충돌 방지).
2. **백서 전용:** `status_report` 모드 완전 제거 (`route_by_target`, `status_formatter_node`, `--format` CLI 인자).
3. **EN→KR 렌더링:** 단순 번역이 아닌 **수석 에디터 스타일 가이드** 기반 렌더링.
   - `## {year}년` / `### {year}년 X월: [핵심 요약]` 헤딩 구조
   - 통합 서술 + 인라인 KPI 강조(**Bold**) + 평어체(~다, ~함, ~구축됨)
   - 연도별 순차 렌더링 + `previous_context` 연속성
   - 3중 검증: Python 고유명사 + 구조(연/월 헤딩) + LLM 스팟체크

---

## §2. 파일 지도

```
projects/deep-doc-pipeline/
├── SPEC.md                ★ 설계 명세서 v1.1 (314줄, 원래 설계 의도)
├── README.md              실행 가이드 + gpt-oss 엔드포인트 예시
├── HANDOFF.md             ★ 이 문서 (인수인계)
├── LESSONS.md             ★ 누적 교훈 카드 L-001~L-014
├── requirements.txt       openai>=1.50, langgraph>=0.2.40, pydantic>=2.7, python-dotenv
├── .env.example           OPENAI_BASE_URL / OPENAI_MODEL / LLM_MAX_RPM
├── .gitignore             Python 캐시, .env, output.md 제외
├── main.py                argparse(--output만) + recursion_limit=200 + graph.invoke
├── data/
│   └── records.jsonl      15건 / 4개월 (2026-02 ~ 2026-05) 더미
├── scripts/
│   └── gen_dummy.py       시드 고정(42) 결정론적 생성기
└── src/
    ├── schemas.py         Pydantic 스키마 8종 (101줄)
    ├── state.py           GraphState + reducer (53줄)
    ├── context_guard.py   95KB 예산 관리 (165줄)
    ├── llm.py             OpenAI SDK + Rate Limiter + JSON guard (354줄)
    ├── logger.py          타임라인 로거 + 실행 통계 (78줄)
    ├── utils.py           Pure Python 결정론 로직 (265줄)
    ├── nodes.py           20개 노드 + 5개 라우터 (1,190줄)
    └── graph.py           LangGraph 조립 (119줄)
```

---

## §3. 전체 그래프 구조

```
START
 │
load_docs ──fanout──▶ strict_extractor (×N, 병렬 Send)
                          │
                  chrono_sorter (Pure Python)                      ── Phase 1: 추출
                          │
                  ──fanout──▶ period_summarizer (×M, 병렬 Send)
                                  │
                          theme_analyzer                           ── Phase 2: 요약
                                  │
                          draft_planner ◀───┐
                                  │         │ Fail
                          planner_critique ─┘                      ── Phase 3: 기획 루프
                                  │ Pass
                          init_writing
                              ┌───▼───────────────┐
                              │ section_writer ◀─┐ │
                              │       │          │ │ Fail (retry<3)
                              │ fact_checker     │ │               ── Phase 4: 집필 루프
                              │   │  Pass  Fail≥3│ │
                              │ save_section     │ │
                              │   │ save_w/warn ─┘ │
                              └───▼───────────────┘
                          compiler (Pure Python)
                                  │
                          polish (LLM, 사실 변경 금지)
                                  │
                          final_fact_checker                       ── Phase 4: 윤문 검증
                            ┌─────┼─────────┐
                          Pass   Fail<2   Fail≥2
                            │  retry_polish   │
                            ▼       ▼         ▼
                    prepare_translation  fallback_to_compiled
                            │               │
                            └───────┬───────┘
                                    ▼
                    prepare_translation                            ── Phase 5: 렌더링
                            │
                    translate (연도별 렌더링)
                            │
                    translation_checker
                      ┌─────┼──────────┐
                    Pass   Fail<2   Fail≥2
                      │  retry_translate │
                      ▼       ▼         ▼
                     END    translate  fallback_english → END
```

**Phase 5 상세 흐름:**
- `prepare_translation` — 영문 백서 저장 + `extract_proper_nouns()` 고유명사 추출
- `translate` — 연도별 분할 → `_build_render_prompt()` 스타일 가이드 적용 → 한국어 렌더링
- `translation_checker` — 3중 검증 (고유명사 / `## 연도` `### 월` 구조 / LLM 스팟체크)
- Fail-safe: 2회 실패 시 `fallback_english` (영문 원본 보존 + ⚠️ 경고)

---

## §4. 핵심 방어 기제 (4대 구조적 방어)

### 방어 1: Fact-checker 회귀 방지 (v1.1)
- `previous_draft` — 반려 초안을 Negative Example로 주입
- `hallucinated_tokens` — 누적 reducer로 블랙리스트
- `FactCheckResult.hallucinated_terms` — 팩트체커가 정확한 토큰 추출 강제

### 방어 2: Fail-Safe 강제통과 워터마크 (v1.1)
- `save_section_with_warning_node` — 3회 실패 시 ⚠️ 워터마크 삽입
- `unverified_sections` — 감사 로그 + 최종 백서 하단 인덱스 자동 출력

### 방어 3: Compiler → Polish → 2차 Fact-check 분리 (v1.1)
- `compiler_node` — Pure Python 문자열 조립만, LLM 금지
- `polish_node` — "사실 변경 금지" 명시 프롬프트
- `final_fact_checker_node` — 2차 검증, Fail 시 polish 우회

### 방어 4: EN→KR 렌더링 3중 검증 (v1.3.1)
- **Python 고유명사:** `extract_proper_nouns()` → 번역 후 존재 검증 (30% 초과 소실 시 반려)
- **구조 검증:** `validate_korean_structure()` → `## YYYY년` / `### YYYY년 X월:` 헤딩 존재·개수
- **LLM 스팟체크:** 첫 2000자 영한 쌍 비교 (사실 추가/누락/고유명사 변형 검출)

---

## §5. 절대 준수 사항 (수정 금지)

1. **LangChain LLM 래퍼 사용 금지** — 오직 `openai.OpenAI()` 직접 사용.
2. **모든 LLM 응답은 Pydantic 강제** — `structured_call()` → `extract_json()` → `model_validate()`.
3. **`response_format` 인자 사용 금지** — GPT-OSS 미지원. 프롬프트 가드 + 3단 파서로 대체.
4. **Pure Python 영역에 LLM 호출 추가 금지** — `utils.py`, `chrono_sorter_node`, `compiler_node`.
5. **영어 출력 노드는 `_EN_ENFORCE` 필수** — json_guard는 언어 중립. 영어 강제는 노드별.
6. **렌더링 노드는 `_EN_ENFORCE` 미사용** — `translate_node`는 한국어 출력.

---

## §6. 진행 현황

### ✅ 완료 (v1.3.1)
- [x] SPEC v1.0 → v1.1 (구조적 위험 3건 보강)
- [x] Pydantic 스키마 8종 (TranslationCheckResult 포함)
- [x] GraphState (v1.1 필드 + 번역 상태 5필드)
- [x] LLM 클라이언트 (GPT-OSS 표준: extract_json 3단 파서, 3회 재시도, 언어 중립 guard)
- [x] Rate Limiter (12 RPM / 5 동시, 환경변수 조정 가능)
- [x] 504 타임아웃 대응 (Streaming + Section Chunking)
- [x] 95KB 컨텍스트 예산 하드리밋 (context_guard.py + 전 노드 예산 가드)
- [x] **영어 전용 LLM 출력** — 모든 Phase 1~4 프롬프트 영어 강제
- [x] **백서 전용** — status_report 모드 완전 제거
- [x] **EN→KR 수석 에디터 렌더링** — 스타일 가이드 6항목 반영
- [x] **연도별 렌더링** — 다년도 데이터 대응 + previous_context 연속성
- [x] **렌더링 3중 검증** — Python 고유명사 + 구조 + LLM 스팟체크
- [x] 고유명사 추출 (`extract_proper_nouns`, 7패턴 + 필터)
- [x] 한국어 구조 검증 (`validate_korean_structure`)
- [x] 더미 데이터 15건 + 생성기
- [x] main 진입점 + README
- [x] AST 문법 검증 전체 통과
- [x] 타임라인 로거 (`src/logger.py`) + 실행 통계 (소요시간/작업수/LLM호출)
- [x] 미사용 코드/주석 정리 (-97줄), 전체 영어 주석 통일
- [x] README.md v1.3.2 기준 재작성
- [x] GitHub push (`1f17934`)

### ⏸️ 보류 (사용자 환경 측)
- [ ] gpt-oss 엔드포인트(Ollama/vLLM) 기동
- [ ] `pip install -r requirements.txt`
- [ ] `python -m main` 실행 (whitepaper 고정, `--output ./output.md`)
- [ ] **Loop 동작 로그 육안 검증** — fact-checker Fail/Retry, 렌더링 검증 가시화
- [ ] 실행 시 발견되는 이슈 디버깅

### 🟡 향후 보강 후보
- [ ] Pure Python 단위 테스트 (pytest — chrono_sorter, extract_proper_nouns 경계값)
- [ ] `failed_docs` 상태 추가 — 3회 추출 실패 문서 추적 (현재 silent drop)
- [ ] JSONL 무결성 검증 강화 (BOM, 빈 줄 외 케이스)
- [ ] LangGraph SqliteSaver 체크포인트 (200건 규모 Resume 기능)
- [ ] 환각 유발 테스트 시나리오 (의도적 모호 문서 1~2건)
- [ ] `hallucinated_tokens`를 `Dict[int, List[str]]` 키-스코프 구조로 개선

---

## §7. 다음 AI Agent 첫 작업 시나리오

### 시나리오 A: 사용자가 "실행 결과 이상하다"고 보고
1. 사용자 로그 요청 (`[fact_checker] approved=...`, `[translate]`, `[translation_checker]` 라인 위주)
2. 의심 지점:
   - `parse` 실패율 높음 → `[structured_call][retry]` 로그 확인, JSON 모드 미지원 가능성
   - recursion_limit 초과 → `main.py`의 200 상향
   - 영문 백서는 정상인데 한국어 렌더링 실패 → `[translation_checker] REJECTED` 사유 확인
   - 고유명사 대량 누락 → `extract_proper_nouns` 출력 점검, 필터 조정

### 시나리오 B: "200건 실제 데이터로 돌리고 싶다"
1. `./data/records.jsonl`로 교체
2. 노드별 모델 분리 권장 — `.env`의 `EXTRACTOR_MODEL`/`JUDGE_MODEL` 주석 해제
3. `LLM_MAX_RPM` 환경변수로 처리량 조정 (기본 12 RPM → 200건 기준 25~40분)
4. SqliteSaver 도입 권장 — 200건은 1회 실패 시 재실행 비용 큼

### 시나리오 C: "렌더링 스타일 바꿔줘"
1. `src/nodes.py`의 `_build_render_prompt()` 함수가 단일 진실 공급원
2. 스타일 가이드 6항목(헤딩 구조, 통합 서술, KPI 강조, 톤, 고유명사, 경고 처리) 수정
3. `translation_checker_node`의 `validate_korean_structure()` 호출도 헤딩 패턴에 맞춰 조정

### 시나리오 D: "다른 도메인에 재활용하고 싶다"
1. 입력 스키마: `schemas.ExtractedEvent`의 3필드(date/issue/action) 교체
2. `utils.chrono_sort_and_group`은 `date` 필드 의존 — 비시계열이면 그룹핑 키 재설계
3. 렌더링 스타일: `_build_render_prompt()`의 연도/월 구조를 도메인에 맞게 변경

---

## §8. 알려진 한계 / 잠재 이슈

1. **실제 LLM 실행 미검증** — AST 통과만으로는 런타임 오류 가능. 특히:
   - `langgraph.types.Send` import 경로 (버전별 차이)
   - Pydantic v2 `model_json_schema()`가 일부 엔진에서 스키마 거부 가능
   - 한국어 렌더링 프롬프트가 JSON guard와 충돌 가능성 (JSON guard는 언어 중립이지만 일부 모델은 영어 우선 경향)

2. **`hallucinated_tokens` 누적 reducer 부작용** (LESSONS L-006) — 다른 섹션의 정상 토큰이 블랙리스트에 잔존. writer의 `retry==0` 우회로 기능상 문제 없으나 **로그 디버깅 혼선** 가능.

3. **연도별 렌더링의 previous_context** — 마지막 500자를 잘라서 전달. 연도 경계의 서술 연속성이 약할 수 있음. 보강 방안: 연도별 3문장 요약 생성 후 전달.

4. **고유명사 추출 한계** — 순수 regex 기반 휴리스틱. 소문자 고유명사(예: `iPhone`, `gRPC`)는 패턴에 잡히지 않을 수 있음. `extract_proper_nouns` 함수에 패턴 추가 필요.

5. **더미 데이터 15건** — 실제 200건 운영 데이터와 분포 차이 클 수 있음. M(월 수)이 12+ 이면 Send 병렬도 점검.

---

## §9. 빠른 디버깅 체크리스트

| 증상 | 1순위 의심 | 확인 방법 |
|------|----------|----------|
| `ModuleNotFoundError: langgraph.types` | langgraph 버전 | `pip show langgraph` → 0.2.40+ 확인 |
| 모든 노드에서 `structured_call failed` | JSON 파싱 반복 실패 | `[structured_call][retry]` 로그, extract_json 디버깅 |
| `[rate_limiter] ... 대기` 빈번 | RPM 한도 너무 낮음 | `LLM_MAX_RPM` 상향 (엔진 허용 범위 내) |
| recursion_limit exceeded | outline 길이 or 재시도 누적 | `main.py`의 200 → 500 상향 |
| `[translation_checker] REJECTED (proper nouns)` | 고유명사 대량 누락 | 렌더링 프롬프트의 noun_ref 확인, 추출 패턴 보강 |
| `[translation_checker] REJECTED (structure)` | `## 연도` / `### 월` 헤딩 미생성 | 렌더링 프롬프트 Output Instruction 강화 |
| 렌더링은 되는데 톤이 경어체 | 모델이 평어체 지시 무시 | 프롬프트에 "절대 ~습니다/~요 사용 금지" 추가 |
| 빈 final_output | compiler 단계 누락 | `[compiler] sections=0` → save_section 동작 점검 |

---

## §10. 참고 명령어

```bash
# 로컬 진입
cd ~/.openclaw/workspace/projects/deep-doc-pipeline

# 환경 셋업
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # .env 편집 + src/llm.py DEFAULT_HEADERS placeholder 교체

# 데이터 (15건 더미)
python -m scripts.gen_dummy

# 실행 (whitepaper 고정, --format 제거됨)
python -m main
python -m main --output ./my-whitepaper.md

# AST 문법 검증 (코드 수정 후 필수)
python3 -c "import ast; from pathlib import Path; [ast.parse(f.read_text()) for f in Path('.').rglob('*.py')]; print('OK')"

# GitHub 동기화
export GH_TOKEN=$(grep "export GH_TOKEN" ~/.bashrc | cut -d= -f2)
git add -A && git commit -m "..." && git push
```

---

## §11. 다음 AI Agent 첫 5분 체크리스트

- [ ] 이 문서 §0~§4 읽기 (파이프라인 흐름 + 방어 기제 파악)
- [ ] `SPEC.md` §1 (4대 원칙) 읽기
- [ ] `src/nodes.py` Phase 주석 + 라우터 함수 훑기
- [ ] `git log --oneline` 커밋 히스토리 확인
- [ ] 사용자 첫 메시지 → 시나리오 A/B/C/D 분류
- [ ] 코드 수정 시 반드시 AST 검증 후 커밋

---

_본 문서는 다음 AI Agent의 빠른 인계를 위해 작성됨. 추가 작업 시 §1(버전 히스토리), §6(진행 현황), §8(한계)을 갱신할 것._
