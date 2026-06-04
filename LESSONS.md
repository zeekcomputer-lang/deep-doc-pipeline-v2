# LESSONS.md - 누적 교훈 인덱스

> 다음 AI Agent가 같은 실수를 반복하지 않도록 정리한 교훈 카드.
> 본 프로젝트뿐 아니라 유사 LangGraph/LLM 파이프라인 작업 시 참조.

## 인덱스

| ID | 분류 | 요약 |
|----|------|------|
| L-001 | 명세 | 사용자 명세서의 코드 오타는 부록에 명시 보정 |
| L-002 | 워크플로 | 코드 작성 전 명세서 구조적 결함 먼저 검토 |
| L-003 | 커뮤니케이션 | 결정 항목은 번호화 + 옵션 2~4개 제한 |
| L-004 | 호환성 | ~~"OpenAI 호환" fallback~~ → **L-010으로 대체됨** |
| L-005 | 아키텍처 | 결정론 영역과 비결정론 영역은 파일 단위로 물리 분리 |
| L-006 | LangGraph | reducer는 단조 증가에 자연스러움. 스코프 초기화 의도 필드는 우회 설계 |
| L-007 | 환경 | exec 셸 컨텍스트는 .bashrc 자동 로딩 X → GH_TOKEN 명시 export |
| L-008 | 의사결정 | Visibility는 도메인 비밀 유무가 1차 기준 |
| L-009 | 커뮤니케이션 | 사용자 단답(“a”, “c”) 뒤에 추가 지시가 붙는 패턴 주의 |
| L-010 | 호환성 | response_format 의존 제거 → 프롬프트 가드 + extract_json 3단 파서가 GPT-OSS 표준 |
| L-011 | 아키텍처 | 대용량 LLM 컨텍스트 504: Streaming + Section Chunking 이중 방어 |
| L-012 | 아키텍처 | 95KB 하드리밋: 측정→분할→병합→교차검증 파이프라인, 손실 최소화 우선순위 4단계 |
| L-013 | 아키텍처 | EN-only LLM + 후번역: 고유명사 추출 → 완전성검증 → 문단분할 → 소스폴백 (v2.0) |
| L-014 | 렌더링 | 번역 vs 렌더링 트레이드오프 — v2.0에서 충실 번역으로 회귀 (L-020) |
| L-015 | 아키텍처 | json_guard의 언어 강제는 노드별로 분리해야 다국어 출력 공존 가능 |
| L-016 | 운영 | 타임라인 로거는 모듈 레벨로 분리하고, print()를 전수 교체해야 일관성 확보 |
| L-017 | 아키텍처 | 504 감축은 국부적으로: 실패 노드만 축소 → 성공 후 원복. 전역 품질 저하 방지 |
| L-018 | 원칙 | user 메시지는 절대 절단하지 않는다. 노드 재실행으로 분할 로직이 더 작은 청크 생성 |
| L-019 | 워크플로 | 검증 루프는 비용 대비 가치를 평가하라. 핵심 검증(fact_checker)만 유지, 비교 검증은 제거 |
| L-020 | 번역 | 번역 단계 콘텐츠 소실: 출력 토큰 한계 + "렌더링" 프롬프트 → 섹션별 문단 분할 번역으로 해결 |

---

## L-001: 사용자 명세서 코드 오타 처리

**상황:**
원본 명세서 v1.0의 `update_dict` 함수가 `return {a, b}` (set literal - 실행 불가)로 작성되어 있었음.

**잘못된 대응:**
명세서를 그대로 코드에 옮기기 → 런타임 에러.

**올바른 대응:**
1. 명세서 저장 단계에서 오타 발견 시 **부록에 보정 사항 명시**
2. 실제 코드는 의도된 동작(`{**a, **b}`)으로 구현
3. 사용자가 명세서를 다시 봤을 때 보정 이력 추적 가능하도록 표 형태로 정리

**적용:** `SPEC.md` 부록 A.

---

## L-002: 코드 작성 전 명세서 구조적 결함 검토

**상황:**
사용자가 "명세서를 코드로 구현하라"고 요청했을 때, 즉시 코딩 착수 vs 명세서 검토 후 착수 갈림길.

**경험:**
1차 응답에서 **저장만 하고 "개선점 제언"** 옵션 제시 → 사용자가 채택 → 구조적 위험 3건 발견 → v1.1 보강 → 그 후 구현.

만약 v1.0 그대로 구현했다면:
- Fact-checker 회귀 → 무한 루프
- Fail-Safe 강제통과 → 환각 섞인 백서 무표시 출력
- Compiler 윤문 → 새 환각 주입

**원칙:**
> 명세서가 200줄 이상이거나 자가검증 루프를 포함하면, **먼저 검토 옵션을 제시**하고 사용자 허가 후 코딩 착수.

---

## L-003: 결정 항목 번호화

**상황:**
사용자에게 3가지 결정사항(모델/실행여부/경로) 질문 → "1. gpt oss / 2. b / 3. 현재 경로" 단답 응답.

**원칙:**
- 결정 항목은 항상 **번호 + 굵은 글씨**로 정렬
- 옵션은 2~4개로 제한
- 각 옵션의 디폴트 값 명시 (응답 없을 때 자동 진행 기준 제공)
- 답변 즉시 한 줄로 재확인 후 착수

**나쁜 예:**
> "모델은 뭘 쓰실 건가요? 실행도 할까요? 경로는 어디로 할까요?"

**좋은 예:**
> 1. **모델:** (a) gpt-4o-mini (b) gpt-oss (c) 그 외
> 2. **실행:** (a) 실제 실행 (b) 코드 작성만
> 3. **경로:** 현재 위치 그대로 진행 시 응답 불요

---

## L-004: OpenAI 호환 엔드포인트의 Structured Outputs

> **⚠️ L-010에 의해 대체됨.** 현재 코드는 `beta.parse`/`response_format` 일체 미사용.
> 본 교훈은 "OpenAI 호환 ≠ 완전 호환" 인식을 위한 배경 자료로만 유지.

**상황:**
gpt-oss를 Ollama/vLLM으로 띄울 때 "OpenAI 호환"이지만 `client.beta.chat.completions.parse` 호환은 별개.

**원칙:**
- "OpenAI 호환"은 기본 `chat.completions.create`까지만 보장
- `beta.parse`, `response_format`, Structured Outputs는 엔진별로 미지원 가능성 높음
- **현재 접근법 (L-010):** 프롬프트 가드 + `extract_json()` 3단 파서로 JSON 강제

**적용:** L-010 참조. 현재 `src/llm.py`는 `response_format` 인자 전면 제거된 상태.

---

## L-005: 결정론 영역과 LLM 영역의 물리 분리

**상황:**
명세서에서 "결정론 우선"을 강조해도, 코딩 중 노드 안에서 `datetime.strptime` 정도는 무심코 LLM 프롬프트에 섞을 유혹.

**원칙:**
- **파일 단위로 분리**: `utils.py`(Pure Python) ↔ `nodes.py`(LLM 호출 허용)
- `utils.py` 상단에 "LLM 호출 금지" 주석 명시
- 노드 함수 중 LLM 호출 없는 것은 함수명에 `_node` 붙이되 본체에서 LLM 호출 안 함이 명백해야 함
- 코드 리뷰 시 `import openai` 또는 `structured_call` 사용 위치 확인

**적용:** `src/utils.py`, `chrono_sorter_node`, `compiler_node`.

---

## L-006: LangGraph reducer와 스코프 초기화

**상황:**
`hallucinated_tokens: Annotated[List[str], operator.add]` 처럼 누적 reducer로 선언했으나, 섹션 통과 후에는 **다음 섹션을 위해 초기화하고 싶음**. reducer는 단조 증가만 자연스러움.

**우회 방법 (이 프로젝트):**
- writer 노드에서 `section_retry_count == 0` 이면 빈 리스트로 간주
- 부작용: 로그상으로는 누적된 채 보임 (디버깅 혼선)

**더 나은 설계 (v1.2 후보):**
- `Dict[int, List[str]]` 구조로 변경 - 섹션 인덱스를 키로 가짐
- reducer는 `update_dict` 사용
- writer는 `state["hallucinated_tokens"].get(current_idx, [])` 로 조회

**원칙:**
> reducer는 "전체 실행 동안 단조 증가"가 자연스러움. 스코프 초기화 의도가 있는 필드는 **키-스코프 구조**로 설계.

---

## L-007: exec 셸과 .bashrc 자동 로딩

**상황:**
`gh auth status` → 미인증. `source ~/.bashrc` 했으나 다음 `exec` 호출에 GH_TOKEN 안 따라옴.

**원인:**
OpenClaw `exec` 도구는 매 호출마다 새 셸 컨텍스트 생성. `source`로 export한 변수는 **그 호출 내부에서만 유효**.

**해결:**
```bash
# 매번 export를 명시적으로 함께 실행
export GH_TOKEN=<token> && gh ...

# 또는 한 줄에 source + 명령
source ~/.bashrc && gh ...
```

**원칙:**
> 셸 환경변수가 필요한 명령은 **단일 exec 호출 안에 export 포함**시킬 것. 별도 호출 분리 금지.

---

## L-008: Repository Visibility 결정

**상황:**
기존 프로젝트 패턴이 일관되지 않음:
- code-2char-system → PRIVATE
- unique-code-system → PUBLIC
- deep-doc-pipeline → ?

**판단 기준 (우선순위):**
1. **도메인 비밀** (실제 회사명/고객명/내부 로직): 있으면 PRIVATE
2. **OSS 가치**: 일반화 가능한 패턴이면 PUBLIC
3. **사용자 의향**: 명시 없으면 PUBLIC 권장 (포트폴리오 노출)

**적용:**
- deep-doc-pipeline은 일반 LangGraph 패턴 → PUBLIC
- 결정 즉시 사용자에게 한 줄 확인 ("PUBLIC으로 진행했습니다. PRIVATE 전환 필요 시 알려주십시오")

**전환 명령:**
```bash
gh repo edit zeekcomputer-lang/<repo> --visibility private
```

---

## L-009: 사용자 단답 + 추가 지시 패턴

**상황:**
옵션 (A/B/C/D) 제시 → 사용자 "c\nGitHub에 repo로 만들어 업로드하세요" 응답.
"c" 단독 해석하면 "현 상태 종료"인데, 같은 메시지에 추가 지시 있음.

**원칙:**
- 답변 파싱 시 **첫 글자만 보지 말고 전체 메시지 읽기**
- 단답 + 추가 지시 = 옵션 선택 후 새 작업 의뢰 패턴
- 응답 시 두 요소를 모두 다룸:
  1. "옵션 C 선택 - 현 작업 종료 확인"
  2. "추가 지시: GitHub 업로드 진행합니다"

**나쁜 예:**
> "C 선택하셨네요. 종료합니다." (추가 지시 무시)

**좋은 예:**
> "C로 종료 확인. 동시에 GitHub 업로드 진행하겠습니다."

---

## L-010: response_format 의존 제거 — GPT-OSS placeholder 표준

**상황:**
`src/llm.py`가 `client.beta.chat.completions.parse` (Structured Outputs) + `response_format={"type":"json_object"}` 2단 fallback으로 구현되어 있었으나, GPT-OSS 환경에서는 둘 다 미지원.

**잘못된 대응:**
“OpenAI 호환” 표기를 신뢰하고 `beta.parse`/`response_format`을 1차 시도하는 코드 → GPT-OSS에서 즉시 실패.

**올바른 대응 (v1.1-r1):**
1. `response_format` 인자 전면 제거
2. Pydantic JSON Schema를 system 프롬프트에 명시 첨부 (출력 규약 가드)
3. `extract_json()` 3단 폴백 파서 (raw → 코드펜스 → 균형 스컨)
4. 재시도 시 직전 응답을 assistant 메시지로 넘겨 “JSON만 다시 출력하라” 재요청
5. `extract_json()` 결과를 `model_validate()`로 Pydantic 검증 유지

**표준 출처:** `langgraph-excel-categorizer/categorizer.py`의 `llm_chat_json()` + `extract_json()` 패턴.

**원칙:**
> LLM API 호출부를 신규 작성할 때는 반드시 `response_format` 미사용 전제로 설계하고, 프롬프트 가드 + 파서 + 재시도로 JSON을 강제할 것. `response_format`은 보너스이지 필수가 아님.

---

## L-011: 대용량 LLM 컨텍스트 504 타임아웃 대응

**상황:**
200건+ 데이터의 컴파일된 백서(수만 토큰)를 단일 `polish_node` / `final_fact_checker_node`에서 LLM에 전송.
업스트림 게이트웨이의 `proxy_read_timeout` 초과로 504 발생. 타임아웃 시간 증가 불가.

**잘못된 대응:**
- 타임아웃 증가 요청 (서버 정책상 불가능한 경우 많음)
- retry만 증가 (동일 페이로드로 동일 504 반복)

**올바른 대응 (v1.1-r3):**
1. **Streaming (`stream=True`)** — 첫 토큰 즉시 수신으로 게이트웨이 `read_timeout` 리셋. 전체 처리 시간은 동일하나 연결 유지.
2. **Section Chunking** — 문서를 `## 섹션` 단위로 분리하여 개별 API 호출. per-call 컨텍스트 1/K로 축소.
3. 두 전략 병행: Streaming이 `read_timeout` 해소, Chunking이 `total_timeout` 해소.

**구현 노트:**
- 헤더(§제목 + 기간)와 본문을 분리하여 본문만 LLM에 전송 → 헤더 변조 방지
- 감사 로그(§---)는 윈문 대상에서 제외
- `final_fact_checker`: 본문 변경 없는 섹션은 skip → 불필요 API 호출 절감
- 섹션 수 불일치 시 전체 문서 비교로 폴백 (stream 적용)

**원칙:**
> LLM에 대용량 컨텍스트를 보낼 때는 항상 (1) 청크 분할 + (2) 스트리밍을 기본값으로 설계하라.
> 단일 페이로드로 전체 문서를 보내는 설계는 프로덕션에서 반드시 터진다.

**적용:** `src/nodes.py` polish_node, translate_node / `src/llm.py` stream 파라미터 / `src/utils.py` split_compiled_by_section, split_section_header_body

> **참고:** v1.5에서 `final_fact_checker_node` 제거됨. 원칙은 동일하게 적용.

---

## L-012: 95KB 컨텍스트 하드리밋 — 손실 최소화 설계

**상황:**
업스트림 게이트웨이/LLM 서버의 요청 본문 크기 제한(95KB). 모든 structured_call의 메시지 페이로드가 이 한도 미만이어야 함.
200건+ 데이터에서 편중 분포(100건/월) 시 section_writer, fact_checker에서 초과 발생.

**손실 최소화 우선순위 (4단계):**
1. **포맷 최적화** — 무손실. JSON wrapper/guard 오버헤드 최소화.
2. **부가 컨텍스트 축소** — retry extras(previous_draft · feedback · hallucinated_tokens) 절단.
3. **데이터 분할 + 다회차 처리** — 이벤트 배치 분할 → 부분 처리 → LLM 병합. 구조적 손실 최소.
4. **추출적 압축** — 마지막 수단. 핵심 사실 보존하며 텍스트 압축.

**구현 패턴:**

| 노드 | 버짓 초과 시 전략 | 손실 등급 |
|------|----------------|----------|
| strict_extractor | 문서 바이트 절단 + [TRUNCATED] | 4단계 |
| period_summarizer | 배치 분할 → 서브 요약 → LLM 병합 | 3단계 |
| theme_analyzer | 오래된 월부터 순차 제거 | 2단계 |
| draft_planner | 요약 100자 절단 | 2단계 |
| planner_critique | intent 80자 절단 | 2단계 |
| **section_writer** | retry trim → 이벤트 배치 → 부분 초안 → LLM 병합 | 3단계 |
| **fact_checker** | 이벤트 배치 + cross_check_terms() 교차검증 | 3단계 |
| polish | 문단별 분할 윈문 | 3단계 |
| **translate** (v2.0) | 섹션별 → 문단별 분할 → 소스데이터 폴백 | 3단계 |

**팩트체커 교차 검증 패턴 (fact_checker_node):**
이벤트 배치 분할 시, 배치 A에서 "환각"으로 판정된 토큰이 배치 B에는 존재할 수 있음.
→ `cross_check_terms()`: 후보 환각 토큰을 전체 이벤트 원본에 Python 문자열 매칭으로 교차 확인.
어느 배치에도 없는 토큰만 진짜 환각으로 확정. LLM 추가 호출 없이 정확도 보전.

**원칙:**
> 모든 LLM 호출은 "측정 → 가드 → 분할/압축 → 호출" 파이프라인을 따라야 한다.
> 단일 페이로드로 예산을 초과하는 설계는 프로덕션에서 반드시 터진다.
> 손실은 4단계 우선순위를 엄격히 준수하여 최소화하라.

**적용:** `src/context_guard.py` (신규) / `src/llm.py` 예산 하드리밋 / `src/nodes.py` 전 노드 예산 가드

---

## 신규 교훈 추가 시 규칙

1. ID 부여: 다음 번호 (L-021, L-022, ...)
2. 인덱스 표 상단에 행 추가
3. 상세 카드는 ID 순서대로 본문 하단 추가
4. 분류는 가급적 기존 카테고리 재사용:
   - 명세 / 워크플로 / 커뮤니케이션 / 호환성 / 아키텍처 / LangGraph / 환경 / 의사결정
5. 각 카드 구조: 상황 → 원칙/대응 → 적용 위치 (또는 명령어)

---

## L-013: EN-only LLM 출력 + 후번역 패턴

**상황:**
저성능 LLM(gpt-oss)이 한국어로 직접 작성하면 원래도 높은 환각률이 더 상승하고,
영어 학습 데이터가 압도적으로 많은 모델 특성상 출력 품질이 저하됨.

**접근법:**
1. 모든 LLM 프롬프트·JSON guard·재시도 프롬프트를 영어로 강제 (`_EN_ENFORCE` 접미사)
2. 최종 백서가 영어로 완성된 후 번역 단계를 분리하여 EN→KR 변환
3. 번역 단계 방어 (v2.0 기준):
   - **완전성 검증:** 한/영 문자비율 ≥ 0.35 자동 판정
   - **문단 분할:** 완전성 미달 시 8KB 단위 분할 번역
   - **소스데이터 폴백:** 번역 자체 실패 시 extracted events로 한글 직접 생성
4. Fail-safe: 모든 경로 실패 시 영어 원본 보존 (데이터 손실 방지)

**고유명사 추출 전략 (`extract_proper_nouns`):**
- 날짜(YYYY-MM-DD/YYYY-MM), 약어(2+대문자), CamelCase, 문중 대문자, 단위 숫자, 백틱 토큰
- 일반 영어 단어 필터링 (~150단어) 으로 오탐 최소화
- 과다 추출 허용 (over-preserve > under-preserve)

**적용:** `src/nodes.py` Phase 5, `src/utils.py` `extract_proper_nouns`

> **변경 이력:** v1.3 초기 3중 방어(Python검증+구조+LLM스팟체크) → v1.5 translation_checker 제거 → v2.0 완전성검증+문단분할+소스폴백으로 재설계.

---

## L-014: 번역 vs 렌더링 — 트레이드오프

> **⚠️ 주의:** v2.0에서 렌더링 접근법이 충실 번역으로 교체됨. L-020 참조.

**상황:**
v1.3 단순 EN→KR 번역 → v1.4 수석 에디터 렌더링 → v2.0 충실 번역으로 회귀.

**핵심 트레이드오프:**
- **렌더링**(구조 변환+톤+언어 동시): 헤딩 구조 세련되지만, LLM이 "요약/재구성"으로 해석하여 **콘텐츠 소실 위험**
- **충실 번역**(언어만 변환, 1:1 대응): 콘텐츠 보존률 높지만, 헤딩 구조는 별도 처리 필요

**적용:**
- v1.4: `_build_render_prompt()` → 렌더링 (git `a6677ea` 참조)
- v2.0: `_build_faithful_translate_prompt()` → 충실 번역 + `_build_section_translate_prompt()` 헤딩 생성

**원칙:**
> 콘텐츠 보존이 우선이면 "충실 번역". 헤딩/스타일 구조가 우선이면 "렌더링". 둘 다 원하면 문단 단위 충실 번역 + 독립 헤딩 생성.

---

## L-015: json_guard 언어 강제와 다국어 출력 공존

**상황:**
v1.3에서 json_guard에 "All text content MUST be in English" 추가 →
한국어 렌더링(`translate_node`) 시 json_guard의 영어 강제와 충돌 발생.

**잘못된 설계:**
json_guard에 언어 강제를 넣으면 **전체 파이프라인이 단일 언어에 갇힘**.

**올바른 설계 (v1.3.1):**
- json_guard는 **언어 중립** — JSON 형식/스키마만 강제, 언어 미명시
- 영어 강제는 **노드별** `_EN_ENFORCE` 접미사로 적용 (Phase 1~4 노드)
- 한국어 렌더링 노드(`translate_node`)는 `_EN_ENFORCE` 미사용
- 결과: 동일 파이프라인에서 영어 출력 노드와 한국어 렌더링 노드 공존

**원칙:**
> LLM 출력 언어 강제는 글로벌(json_guard)이 아닌 노드별(system prompt)로 적용할 것.
> 다국어 출력이 필요한 파이프라인에서는 글로벌 언어 강제가 단일 장애점이 됨.

**적용:** `src/llm.py` json_guard (언어 중립), `src/nodes.py` `_EN_ENFORCE` (영어 노드용)

---

## L-016: 타임라인 로거 모듈 분리

**상황:**
노드별 `print(f"[tag] msg")` 패턴이 48건+ 산재. Rate limiter 한도 도달 시점, LLM 호출 회수, 전체 소요 시간 등을 파악하려면 로그를 직접 세야 함.

**접근법:**
- `src/logger.py` 신설 — `plog(tag, msg)` / `psub(tag, msg)` / `count_llm()` / `summary()`
- `plog`: `[MM:SS] #N [tag] msg` 포맷 (타임스탬프 + 작업 번호 자동 부여)
- `psub`: 하위 작업 (인덴트, 번호 미부여)
- `count_llm`: 성공적 API 응답마다 호출 — 최종 통계에 반영
- `main.py`에서 `reset_stats()` → `graph.invoke()` → `summary()` 패턴

**교훈:**
- `print()` → 로거 함수 교체는 **전수 교체**해야 일관성 확보. 부분 교체는 혼재.
- 로거 모듈은 본체 로직과 분리 (logger.py 독립). 노드/LLM 코드에 로깅 로직 산재 금지.
- `sed` 일괄 치환 + 멀티라인 print 수동 보정 조합이 가장 효율적.

**적용:** `src/logger.py`, `src/nodes.py` (48건 교체), `src/llm.py` (3건 교체), `main.py` (요약 표)

---

## L-017: 504 감축은 국부적으로

**상황:**
504 방어 초기 설계에서 예산 감축을 전역 persistent로 적용 → 이후 모든 노드가 축소된 예산으로 동작 → 불필요한 품질 저하.

**잘못된 설계:**
```
section_writer 504 → budget 90KB → 성공
fact_checker → budget 90KB (축소된 채) ← 품질 저하
translate → budget 90KB (축소된 채) ← 품질 저하
```

**올바른 설계:**
```
section_writer 504 → budget 90KB → 성공 → reset_504_state()
fact_checker → budget 95KB (원복) ← 품질 유지
translate → budget 95KB (원복) ← 품질 유지
```

**구현:** `@retry_on_504` 데코레이터가 진입/종료 시 `reset_504_state()` 호출.

**원칙:**
> 에러 복구 전략의 영향 범위는 최소화할 것. 전역 상태 변경은 부수효과가 크다.

---

## L-018: user 메시지 불변 원칙

**상황:**
504 방어 초기 설계에서 `_trim_longest_user_msg()`로 user 메시지를 5KB씩 절단하는 로직 도입.
사용자 피드백: user 메시지는 절단하면 안 된다. 노드를 재실행해서 분할 로직이 더 작은 청크를 생성해야 한다.

**504 시 노드 내부 변경점 2가지만:**
1. `effective_max_tokens()` 감소 → 출력 토큰 줄어듬 (응답 크기 제한)
2. `effective_budget()` 감소 → 노드 분할 로직의 if/else 분기가 변경되어 더 많은 배치 생성

**변경되지 않는 것:**
- user 메시지 원본 텍스트
- 이전 노드 출력
- 프롬프트 내용
- temperature, reasoning_effort

**원칙:**
> LLM에 전달되는 데이터는 절대 손실시키지 않는다. 예산 초과는 분할 전략(더 작은 청크, 더 많은 호출)으로 해결한다.

**적용:** `src/llm.py` (`_trim_longest_user_msg` 제거됨), `src/nodes.py` (`@retry_on_504`)

---

## L-019: 검증 루프의 비용 대비 가치 평가

**상황:**
v1.4에서 `final_fact_checker`(polish 비교) + `translation_checker`(영한 비교) 루프가
있었으나, 각각 추가 LLM 호출 2~30회를 소비하면서 파이프라인 시간/비용을 대폭 증가시킴.

**판단 기준:**
- **핵심 검증** (section `fact_checker`): 원본 데이터와 직접 대조 → 환각 차단 핵심 → **유지**
- **비교 검증** (final_fact_checker, translation_checker): 생성물끼리 비교 → 비용 대비 품질 개선 미미 → **제거**

**원칙:**
> 검증 루프를 추가할 때는 "이 검증이 없으면 무엇이 망가지는가?"를 물어라.
> 답이 "품질이 약간 떨어진다"면 제거 후보. "환각이 통과한다"면 유지.

**적용:** v1.5에서 8개 노드 제거 (-322줄)

---

## L-020: 번역 단계 콘텐츠 소실 — 렌더링 vs 충실 번역

**상황:**
v1.5 translate_node에서 영문 20,000단어 → 한글 6,000단어로 소실 (~70% 손실).

**근본 원인 3건:**
1. **이중 `@retry_on_504`**: 데코레이터 2회 중복 적용 버그 (10×10=100회 retry)
2. **출력 토큰 한계**: `max_tokens=24,000`으로 전체 문서 1회 호출 시 한글 출력이 절삭됨
3. **"렌더링" 프롬프트**: LLM이 "rendering"을 "요약·재구성"으로 해석하여 압축

**해결 (v2.0):**
1. Path A(전체 문서 1회 호출) 제거 → 항상 섹션별 처리
2. 섹션 번역 후 완전성 검증 (Korean/English char ratio ≥ 0.35)
3. 미달 시 문단별 분할 번역 (8KB 청크) + 직접 연결 (LLM 병합 없음)
4. 문단 실패 시 파이프라인 이전 단계 산출물(extracted events + period summaries)로 한글 직접 생성
5. 프롬프트를 "렌더링" → "충실 번역"으로 교체 (1:1 문장 대응 강제)

**원칙:**
> LLM에 "렌더링" "재구성" "스타일 적용"을 요청하면 압축이 발생한다. 콘텐츠 보존이 우선이면 "충실 번역" 프롬프트 + 문단 단위 분할을 사용하라.
> 출력 토큰 한계는 큰 문서의 단일 호출 번역을 반드시 무너트린다.

**적용:** `src/nodes.py` Phase 5 v2.0 (Δ1 함수 제거, 5 함수 신규, translate_node 전면 재작성)

---

_본 문서는 본 프로젝트뿐 아니라 향후 LangGraph/LLM 파이프라인 작업에 참조 가능._
