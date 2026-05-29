# LESSONS.md - 누적 교훈 인덱스

> 다음 AI Agent가 같은 실수를 반복하지 않도록 정리한 교훈 카드.
> 본 프로젝트뿐 아니라 유사 LangGraph/LLM 파이프라인 작업 시 참조.

## 인덱스

| ID | 분류 | 요약 |
|----|------|------|
| L-001 | 명세 | 사용자 명세서의 코드 오타는 부록에 명시 보정 |
| L-002 | 워크플로 | 코드 작성 전 명세서 구조적 결함 먼저 검토 |
| L-003 | 커뮤니케이션 | 결정 항목은 번호화 + 옵션 2~4개 제한 |
| L-004 | 호환성 | "OpenAI 호환"은 Structured Outputs까진 보장 안 됨 → fallback 필수 |
| L-005 | 아키텍처 | 결정론 영역과 비결정론 영역은 파일 단위로 물리 분리 |
| L-006 | LangGraph | reducer는 단조 증가에 자연스러움. 스코프 초기화 의도 필드는 우회 설계 |
| L-007 | 환경 | exec 셸 컨텍스트는 .bashrc 자동 로딩 X → GH_TOKEN 명시 export |
| L-008 | 의사결정 | Visibility는 도메인 비밀 유무가 1차 기준 |
| L-009 | 커뮤니케이션 | 사용자 단답(“a”, “c”) 뒤에 추가 지시가 붙는 패턴 주의 |
| L-010 | 호환성 | response_format 의존 제거 → 프롬프트 가드 + extract_json 3단 파서가 GPT-OSS 표준 |

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

**상황:**
gpt-oss를 Ollama/vLLM으로 띄울 때 "OpenAI 호환"이지만 `client.beta.chat.completions.parse` 호환은 별개.

**원칙:**
- `parse` API는 OpenAI 공식 모델 기준 설계 (`response_format` 에 Pydantic 모델 직접 전달)
- 타사 엔진은 `response_format={"type": "json_object"}` 까지만 지원하는 경우 많음
- **코드는 항상 2단 fallback** 구비:
  1. Primary: `client.beta.chat.completions.parse`
  2. Fallback: 일반 `chat.completions.create` + `response_format={"type":"json_object"}` + `model_validate_json`

**적용:** `src/llm.py` `structured_call` 함수.

---

## L-005: 결정론 영역과 LLM 영역의 물리 분리

**상황:**
명세서에서 "결정론 우선"을 강조해도, 코딩 중 노드 안에서 `datetime.strptime` 정도는 무심코 LLM 프롬프트에 섞을 유혹.

**원칙:**
- **파일 단위로 분리**: `utils.py`(Pure Python) ↔ `nodes.py`(LLM 호출 허용)
- `utils.py` 상단에 "LLM 호출 금지" 주석 명시
- 노드 함수 중 LLM 호출 없는 것은 함수명에 `_node` 붙이되 본체에서 LLM 호출 안 함이 명백해야 함
- 코드 리뷰 시 `import openai` 또는 `structured_call` 사용 위치 확인

**적용:** `src/utils.py`, `chrono_sorter_node`, `compiler_node`, `status_formatter_node`.

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

## 신규 교훈 추가 시 규칙

1. ID 부여: 다음 번호 (L-010, L-011, ...)
2. 인덱스 표 상단에 행 추가
3. 상세 카드는 ID 순서대로 본문 하단 추가
4. 분류는 가급적 기존 카테고리 재사용:
   - 명세 / 워크플로 / 커뮤니케이션 / 호환성 / 아키텍처 / LangGraph / 환경 / 의사결정
5. 각 카드 구조: 상황 → 원칙/대응 → 적용 위치 (또는 명령어)

---

_본 문서는 본 프로젝트뿐 아니라 향후 LangGraph/LLM 파이프라인 작업에 참조 가능._
