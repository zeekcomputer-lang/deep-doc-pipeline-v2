# STATUS.md — 현재 상태 스냅샷

> **갱신:** 2026-06-11 (v3.1) · **검증자:** OpenClaw 에이전트
> 다음 AI 에이전트의 **최초 진입 문서**. 30초 안에 현재 상태를 파악하고 작업을 시작할 수 있도록 정리.

---

## 한 줄 상태

✅ **v3.1 구현 완료.** 완성 백서(제목+본문+시사점) + DOCX 자동 생성 + 사전 지식 주입. AST 14/14 PASS, 조립→DOCX 검증 통과. LLM 실제 실행만 미수행(사용자 엔드포인트 연결 대기).

### v3.1 주요 변경 (2026-06-11)
- **월별 상세 타임라인 부록 제거** — `timeline_formatter` 노드 삭제. 최종문서는 제목+본문+시사점 구조.
- **완성 백서 양식** — `compile_whitepaper()`: H1 제목 + 본문(H2 섹션 2~4개) + 시사점 섹션.
- **DOCX 자동 생성** — 파이프라인 종료 시 `run_dir/백서.docx` 자동 생성 (표지 제목·네이비 헤딩·양쪽정렬 본문·페이지번호). `--no-docx`로 비활성화.
- **사전 지식 주입** — `prompt_config.py`의 `DOMAIN_KNOWLEDGE`/`KEY_TERMS`가 전 LLM 노드(추출·분석·집필)에 자동 주입. `DOCUMENT_TITLE`로 제목 고정 가능.

---

## 무엇이 끝났나

| 영역 | 상태 | 근거 |
|------|:----:|------|
| 아키텍처 설계 (v3.1) | ✅ | 카테고리 우선 4-Step, KR-first, 완성 백서 |
| 노드 구현 (12 노드 + 2 라우터) | ✅ | timeline_formatter 제거 후 |
| 완성 백서 조립 (제목+본문+시사점) | ✅ | `utils.compile_whitepaper()` |
| DOCX 자동 생성 | ✅ | `md_to_docx.build_whitepaper_docx()`, main.py 연동 |
| 사전 지식 주입 | ✅ | `prompt_config` DOMAIN_KNOWLEDGE/KEY_TERMS |
| 그래프 조립 (정규 + resume) | ✅ | `src/graph.py` build_graph / build_resume_graph |
| 산출물 저장 + resume 상태 로드 | ✅ | `src/artifacts.py` |
| AST 문법 검사 | ✅ | 14/14 PASS |
| 조립→DOCX 검증 | ✅ | 더미 백서 → 37KB DOCX 구조 확인 |
| 더미 데이터 | ✅ | `data/records.jsonl` 15건 |
| **실제 LLM 파이프라인 실행** | ⬜ | `output/` 없음 — 미실행 |
| 출력 품질 검증 | ⬜ | 실행 후 가능 |

---

## 무엇을 해야 하나 (다음 에이전트)

1. **실행하려면:** `.env` 셋업(OPENAI_BASE_URL / OPENAI_MODEL) → `python -m scripts.gen_dummy` → `python -m main`
   - 종료 시 `output/<ts>/백서.docx` 자동 생성 (마크다운은 `step4_final.md`), 그리고 `proper_nouns.json`(완성 문서 고유명사 추출, 재사용용) 동시 생성.
   - 인증은 API 키가 아니라 `src/llm.py` `DEFAULT_HEADERS` 또는 `OPENAI_EXTRA_HEADERS`(JSON) 헤더로 처리.
2. **백서 커스터마이징:** `src/prompt_config.py` 편집
   - `DOCUMENT_TITLE` — 표지 제목 고정 (비우면 LLM 자동 생성)
   - `DOMAIN_KNOWLEDGE` — LLM이 모르는 도메인 지식·단계 정의·주의사항 (전 LLM 노드 주입)
   - `KEY_TERMS` — 용어집 {용어: 정의}
3. **재개 실행:** `python -m main --resume <output_dir> --resume-from {step2|step3|step4|polish}`
4. **DOCX만 재생성:** `python scripts/md_to_docx.py output/<ts>/step4_final.md`

---

## 문서 신뢰도 (2026-06-11 재검증)

| 문서 | 신뢰도 | 비고 |
|------|:------:|------|
| SPEC.md | 🟢 유효 | v3.0 설계와 코드 일치 |
| HANDOFF.md | 🟢 유효 | 상태 필드/파일 지도/CLI 갱신 완료 |
| README.md | 🟢 유효 | 그래프(init_writing)/CLI/출력 목록 갱신 완료 |
| LESSONS.md | 🟢 유효 | L-011~L-021. v3 대체 교훈 명시됨 |

### 이번 재검증에서 수정한 항목 (LESSONS L-021)
- HANDOFF 상태 "구현 대기중" → "구현 완료"
- README/HANDOFF 그래프에 `init_writing` 누락 보강
- `--resume-from translate`(없는 step) → `step2|step3|step4|polish`
- README `OPENAI_API_KEY` 안내 → BASE_URL/MODEL + 헤더 인증
- 파일 지도에 `artifacts.py`, 출력 목록에 `step4_compiled.md`·`step1_raw_entries.json` 추가
- Step 4의 "(선택) EN→KR 번역" 잔류 제거 (KR-first로 번역 단계 없음)

---

## 핵심 사실 (빠른 참조)

- **GitHub:** https://github.com/zeekcomputer-lang/deep-doc-pipeline-v2 (PUBLIC)
- **Python:** `/home/linuxbrew/.linuxbrew/bin/python3` (python-docx 등 설치됨)
- **최종 커밋:** `cc7c06e` (working tree clean)
- **진입 순서:** STATUS.md(본 문서) → HANDOFF.md §0 → SPEC.md → 코드
