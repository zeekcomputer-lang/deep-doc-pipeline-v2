# LESSONS.md — Deep Doc Pipeline v3 누적 교훈 인덱스

> 다음 AI Agent가 같은 실수를 반복하지 않도록 정리한 교훈 카드.
> 본 프로젝트뿐 아니라 유사 LangGraph/LLM 파이프라인 작업 시 참조.
>
> **Note:** v2 시절 축적된 교훈(L-011 ~ L-020)은 v3 아키텍처에서도 대부분 유효합니다.
> 504 방어, 95KB 예산 관리 등 핵심 원칙은 v3에 그대로 적용됩니다.
> 
> **v3 KR-first 전환으로 대체된 교훈:**
> - **L-013** (EN-only LLM + 후번역): v3에서 한국어 직접 출력으로 전환. 번역 단계 자체 제거.
> - **L-014** (번역 vs 렌더링 트레이드오프): v3에서 번역 없음. 원천 차단.
> - **L-020** (번역 콘텐츠 소실): v3에서 번역 없음. 원천 차단.
> 
> 새로운 교훈은 L-021부터 추가됩니다.
>
> | ID | 분류 | 요약 |
> |----|------|------|
> | L-021 | 문서 | HANDOFF 상태 필드는 구현 커밋 직후 갱신해야 한다 — "구현 대기" 잔류가 다음 에이전트를 오도 |
> | L-022 | 아키텍처 | 최종 산출물은 "완성 양식"으로 설계하라 — 제목+본문+시사점 구조화 + DOCX 자동화로 사용자 추가 수정 제거 |
> | L-023 | 프롬프트 | 사전 지식 주입(DOMAIN_KNOWLEDGE/KEY_TERMS)으로 LLM 어텐션 집중 + 환각 감소 |
> | L-024 | 조립 | 제목 중복: 조립기가 붙이는 헤딩과 LLM 본문 내 헤딩이 겹친다 — 프롬프트 예방 + 결정론적 후처리 2계층 |
> | L-025 | 프롬프트 | 시점 본문 반영: 데이터에 날짜가 있어도 "서술하라" 지시가 없으면 LLM이 안 녹인다 — 조건부 지시 + 환각 방지 |
> | L-026 | 산출 | 기능 추가는 "독립 출력 단계"로 — 기존 파이프라인/그래프 불변, post-invoke 단계로만 추가 (고유명사 JSON) |

## 인덱스

| ID | 분류 | 요약 |
|----|------|------|
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

> **⚠️ v3 대체:** v3에서 KR-first 아키텍처로 전환. 전 Step 한국어 직접 출력, 번역 단계 제거. 고유명사만 `_KR_PROPER_NOUN_PRESERVE` 가드로 원어 보존.

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

> **⚠️ v3 대체:** v3에서 번역 단계 자체를 제거. 한국어 직접 출력으로 번역/렌더링 트레이드오프 원천 차단.
> 
> ⚠️ 구 v2 주의: v2.0에서 렌더링 접근법이 충실 번역으로 교체됨. L-020 참조.

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
- 한국어 번역 노드(`translate_node`)는 `_EN_ENFORCE` 미사용
- 결과: 동일 파이프라인에서 영어 출력 노드와 한국어 번역 노드 공존

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

> **⚠️ v3 대체:** v3에서 번역 단계 자체를 제거하여 콘텐츠 소실 이슈를 원천 차단. KR-first 아키텍처로 모든 노드가 한국어 직접 출력.

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

## L-021: 문서 상태 필드는 구현과 동기화하라

**증상:** HANDOFF.md 상태가 "v3.0 아키텍처 설계 완료 / 코드 구현 대기중"로 남아 있었으나, 실제 git log는 v3.0 전체 구현(`1d5d55e`) + fact-checker 제거(`8a66212`)까지 완료 상태. 문서만 보면 다음 에이전트가 이미 구현된 노드를 재구현할 위험.

**함께 발견된 잘못된 레퍼런스:**
1. README/HANDOFF 그래프에 `init_writing` 누락
2. `--resume-from translate` (존재하지 않는 step — 실제는 step2/step3/step4/polish)
3. `OPENAI_API_KEY 등 설정` (실제는 BASE_URL/MODEL + 헤더 인증)
4. 파일 지도에 `artifacts.py` 누락, 출력 목록에 `step4_compiled.md` 누락
5. Step 4 설명에 이미 제거된 "(선택) EN→KR 번역" 잔류

**원칙:**
> 핵심 코드(구현/제거) 커밋 직후 반드시 HANDOFF 상태 필드·파일 지도·CLI 예제를 같이 갱신한다. 문서는 코드의 현재 상태를 반영해야 인수인계 가치가 있다.
> 다음 에이전트 진입 시 첫 단계: `git log --oneline` ↔ HANDOFF 상태 필드 교차 확인.

**적용:** HANDOFF.md §헤더+§1+§2+§4+§9, README.md 구조/그래프/CLI/출력, STATUS.md 신규 추가 (2026-06-11)

---

## L-022: 최종 산출물은 "완성 양식"으로 — 제목+본문+시사점 + DOCX 자동화

**배경:** v3.0은 Executive Summary + 월별 상세 타임라인 부록의 "하이브리드" 출력이었다. 그러나 경영진용 보고서는 상세 타임라인이 오히려 가독성을 해치고, 마크다운만 있으면 사용자가 수동으로 Word로 옮겨야 했다.

**변경 (v3.1):**
1. **타임라인 부록 제거** — `timeline_formatter` 노드 삭제, LLM 호출 1종 감소. `temporal_index`는 유지(날짜 컨텍스트용)하되 최종문서엔 미포함.
2. **완성 백서 구조** — `compile_whitepaper()`: H1 제목 + 본문(H2 2~4섹션) + 시사점 섹션. 제목·시사점은 narrative_planner가 한 번에 생성(NarrativeFlow 스키마 확장).
3. **DOCX 자동화** — `build_whitepaper_docx()` 가 표지 제목·네이비 헤딩·양쪽정렬 본문·페이지번호를 적용. main.py가 파이프라인 종료 시 자동 호출.

**원칙:**
> 최종 산출물은 "그대로 제출 가능한" 완성 형태여야 한다. 중간 산출물(JSON/MD)과 최종 산출물(DOCX)을 분리하고, 최종본은 양식·타이포그래피까지 자동화해 사용자 수작업을 0으로 만든다.
> python-docx에서 한글 폰트는 `w:rFonts/w:eastAsia`를 명시해야 적용된다. 명조 설정만으론 한글이 기본 폰트로 새서 보일 수 있다.

**적용:** `utils.compile_whitepaper`, `nodes.compiler_node`(timeline 제거), `scripts/md_to_docx.build_whitepaper_docx`, `main.py`

---

## L-023: 사전 지식 주입으로 LLM 어텐션 집중 + 환각 감소

**문제:** 저성능 LLM(gpt-oss)은 도메인 고유 용어·프로세스·단계를 모르거나 일반 상식으로 잘못 추론한다. 예: "Go-Live"를 단순 배포로 오해, 개발 단계를 임의로 추정.

**해결:** `prompt_config.py`에 `DOMAIN_KNOWLEDGE`(자유 텍스트) + `KEY_TERMS`(용어집 dict) 추가. `_build_domain_block()`이 전 LLM 노드(추출·분석·집필)의 system 프롬프트에 자동 주입.

**원칙:**
> LLM이 모르는 지식은 "추론"에 맡기지 말고 "주입"하라. 용어 정의·단계 목록·강조 관점을 프롬프트 상단에 고정하면 저성능 모델의 일관성과 정확도가 올라간다.

**적용:** `src/prompt_config.py` (DOMAIN_KNOWLEDGE/KEY_TERMS/get_domain_knowledge), 전 LLM 노드 context 함수

---

## L-024: 제목·구분자 중복 — 조립기 헤딩 vs LLM 본문 내 헤딩

**증상:** 최종 백서에서 섹션 제목이 2번씩 출력됨.
```
## 리스크 관리와 대응 체계 강화
## 리스크 관리와 대응 체계 강화
본문...
```

**근본 원인:** `compile_executive_summary`가 결정론적으로 `## {title}`을 붙이는데, `section_writer` LLM이 본문(`content`)에도 같은 제목을 다시 생성. 또한 LLM이 placeholder('## Title')이나 다음 섹션 제목을 미리 쓰는 경우도 존재.

**해결 (2계층 방어):**
1. **예방(프롬프트):** `section_writer` 프롬프트에 "섹션 제목·헤딩 출력 금지, 순수 본문 문단만" 명시. polish 프롬프트에 "헤딩 추가·복제 금지".
2. **방어(결정론적 후처리):** `strip_section_title()` — 본문에서 섹션 제목과 동일하거나 placeholder인 헤딩 제거. `dedup_adjacent_headings()` — 사이에 본문 없이 연속 반복되는 헤딩 병합. 정규화는 공백·구두점·대소문자·trailing # 무시.

**원칙:**
> LLM 출력 구조는 프롬프트로 "요청"하되, 결코 신뢰하지 말고 결정론적 후처리로 "보장"하라. 조립기(Python)와 생성기(LLM)가 둘 다 동일 구조 요소(제목)를 생성할 수 있으면 책임 경계를 명확히 하고(→ 조립기만 제목 담당) 중복 제거 가드를 둔다.

**적용:** `utils.strip_section_title/dedup_adjacent_headings/compile_executive_summary/compile_whitepaper`, `nodes.section_writer`(프롬프트)·`polish`(프롬프트+dedup)

---

## L-025: 시점(날짜) 본문 반영 — 데이터 전달과 지시는 별개

**배경:** v3.1에서 월별 상세 타임라인 '부록'을 제거했다. 그러나 사용자는 "시간이 단서로 주어지면 본문에 시점이 들어가길" 원함. 이건 부록 복원이 아니라 본문 서술에 시점을 녹이는 별개 개념.

**발견:** 데이터는 이미 흐르고 있었다. `format_entries_for_prompt`가 `[2026-02-04] [critical] 제목: 설명` 형태로 날짜를 분석·집필 LLM에 전달 중. **빠진 건 "그 날짜를 서술하라"는 지시뿐이었다.** 모델은 날짜를 볼 수는 있으나 녹일지는 들장날장.

**해결:** `prompt_config.INCLUDE_TEMPORAL_CONTEXT`(기본 True) 추가. True면 `_build_temporal_block()`이 분석·집필 컨텍스트에 시점 서술 규칙 주입. 날짜 단서가 있는 사안만 시점 표기, 없는 사안은 강제하지 않아 환각 방지. `format_entries_for_prompt(show_date=...)`로 데이터 측 노출도 제어 가능.

**원칙:**
> LLM에게 정보를 "보여주는 것"과 "활용하라고 지시하는 것"은 다르다. 데이터 필드가 프롬프트에 있어도, 원하는 출력 행동은 명시적 지시로 요청해야 일관성이 생긴다. 단, 없는 정보를 지어내지 않도록 가드(환각 방지)를 함께 넣는다.

**적용:** `prompt_config`(INCLUDE_TEMPORAL_CONTEXT/_build_temporal_block/get_temporal_directive, analysis·writing context), `utils.format_entries_for_prompt(show_date)`

---

## L-026: 기능 추가는 "독립 출력 단계"로 — 그래프 불변 원칙

**요구:** 완성 문서에서 고유명사를 재추출해 JSON으로 저장. 단, **다른 기능은 절대 건들지 말 것.**

**접근:** LangGraph 그래프·노드·스키마는 일체 수정하지 않고, `main.py`의 `graph.invoke()` **종료 직후(post-invoke)** 에 `final_output`을 입력으로 하는 독립 출력 단계만 추가. 추출 로직은 이미 있던 `extract_proper_nouns`(고유명사 휴리스틱) 재사용.

**구현:**
- `utils.categorize_proper_nouns()` / `export_proper_nouns()` — 순수 추가 (기존 함수 무변경, 삭제 0줄)
- `main.py` — DOCX 생성 블록 뒤에 `save_json("proper_nouns.json", ...)` 추가, `--proper-nouns` 옵션

**원칙:**
> 검증된 파이프라인에 기능을 더할 때는, 내부 그래프를 수정하지 말고 **최종 결과물을 입력으로 받는 독립 후처리 단계**로 붙이라. 회귀 위험 0, 기존 동작 보존, 롤백 용이.

**적용:** `utils.categorize_proper_nouns/export_proper_nouns`, `main.py`(post-invoke 고유명사 출력)

---

_본 문서는 본 프로젝트뿐 아니라 향후 LangGraph/LLM 파이프라인 작업에 참조 가능._
