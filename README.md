# Deep Doc Pipeline v3

> Raw 프로젝트 데이터 → **하이브리드 백서** (Executive Summary + Chronological Appendix)

[![Origin](https://img.shields.io/badge/fork-deep--doc--pipeline%20v2.0-blue)](https://github.com/zeekcomputer-lang/deep-doc-pipeline)
[![Repo](https://img.shields.io/badge/repo-deep--doc--pipeline--v2-green)](https://github.com/zeekcomputer-lang/deep-doc-pipeline-v2)

## 개요

> **상태 (2026-06-11):** ✅ v3.0 전체 구현 완료 · AST 14/14 PASS · 더미 데이터 15건 포함. LLM 실제 실행은 사용자 엔드포인트 연결 후 대기.

LangGraph + OpenAI SDK + Pydantic 기반 문서 생성 파이프라인.
JSONL 원본 데이터를 **카테고리별 지식 구조화** → **서사 설계** → **섹션 집필** → **최종 백서**로 변환합니다.

### v3.1 핵심 변경점

- **완성 백서 출력** — 제목 + 본문(1~2p) + 시사점 구조의 세련된 비즈니스 보고서. 월별 상세 타임라인 부록 제거.
- **DOCX 자동 생성** — 파이프라인 종료 시 Word 문서 자동 생성 (추가 수정 불필요한 완성 양식)
- **사전 지식 주입** — `DOMAIN_KNOWLEDGE`/`KEY_TERMS`로 LLM이 모르는 도메인 지식·단계·용어를 사전 주입 (어텐션 집중 + 환각 감소)
- **카테고리 우선 지식 구조화** — 4개 축(`Architecture_and_Tech` · `Risk_and_Troubleshooting` · `Business_and_Feature` · `Lessons_Learned`)
- **한국어 직접 출력 (KR-first)** — 번역 단계 없이 한국어로 직접 생성, 고유명사만 원어 보존
- **지식 베이스 내보내기** — JSON / RDB-ready 포맷
- **95KB 예산 가드** · **504 방어**

## 프로젝트 구조

```
├── main.py                  엔트리 포인트
├── requirements.txt
├── .env.example
├── data/records.jsonl       입력 데이터
├── scripts/
│   ├── gen_dummy.py         더미 데이터 생성기
│   └── md_to_docx.py       MD → DOCX 변환기
└── src/
    ├── prompt_config.py     ★ 사용자 프롬프트 커스터마이징
    ├── schemas.py           Pydantic 응답 스키마
    ├── state.py             GraphState 정의
    ├── context_guard.py     95KB 예산 관리
    ├── artifacts.py         중간/최종 산출물 저장 + resume 상태 로드
    ├── llm.py               OpenAI SDK 클라이언트
    ├── logger.py            타임라인 로거
    ├── utils.py             순수 Python 유틸리티
    ├── nodes.py             파이프라인 노드 (13 노드 + 2 라우터)
    └── graph.py             LangGraph 그래프 조립 (정규 + resume)
```

## 빠른 시작

```bash
pip install -r requirements.txt
cp .env.example .env          # OPENAI_BASE_URL / OPENAI_MODEL 설정 (인증은 HTTP 헤더로 처리)
python -m scripts.gen_dummy   # 더미 데이터 생성
python -m main                # 파이프라인 실행
```

> 인증: API 키가 아니라 `src/llm.py` 의 `DEFAULT_HEADERS` 또는 `OPENAI_EXTRA_HEADERS` 환경변수(JSON)로 헤더 인증. gpt-oss / Ollama / vLLM 등 OpenAI 호환 엔드포인트 대상.

### CLI 옵션

```bash
python -m main --export-kb kb.json        # 지식 베이스 JSON 내보내기
python -m main --reasoning medium         # 추론 수준 조절 (high|medium)
python -m main --resume output/<dir> --resume-from step3   # 중간 지점부터 재개 (step2|step3|step4|polish)
python -m main --list-runs                # 이전 실행 목록
python -m main --docx report.docx         # DOCX 출력 경로 지정
python -m main --no-docx                  # DOCX 자동 생성 비활성화 (마크다운만)
```

### 백서 커스터마이징 (`src/prompt_config.py`)

```python
DOCUMENT_TITLE = ""                # 표지 제목 (비우면 LLM 자동 생성)
DOCUMENT_PURPOSE = "..."           # 문서 목적
DOMAIN_KNOWLEDGE = """            # ★ LLM이 모르는 사전 지식 주입
- 개발 단계: 기획 → 설계 → 구현 → 안정화 → 운영전환
- 'P99'은 상위 1% 느린 요청의 응답시간을 의미한다
"""
KEY_TERMS = {"Go-Live": "무중단 운영 전환 단계"}   # 용어집
INCLUDE_TEMPORAL_CONTEXT = True    # 날짜 단서가 있는 사안은 본문에 시점 반영 (기본 True)
```

> **시점 반영:** `INCLUDE_TEMPORAL_CONTEXT=True`이면 데이터의 날짜 단서(`date_hint`)가 있는 사안을 본문 서술에 자연스럽게 녹입니다(예: '2026년 2월'). 날짜 없는 사안은 강제하지 않아 환각을 방지합니다. (월별 상세 타임라인 '부록'과는 별개 — 부록은 제거됨.)

## 파이프라인 그래프

```
load_docs → knowledge_extractor(×N) → knowledge_aggregator → temporal_indexer
         → category_analyzer(×4) → narrative_planner ⟲ narrative_critique
         → init_writing → section_writer → save_section ⟲ route_next_section
         → compiler → polish → END   (종료 후 DOCX 자동 생성)
```

## 출력물

```
output/<timestamp>/
  ├── step1_knowledge_base.json       지식 베이스
  ├── step1_temporal_index.json       best-effort 시간순 인덱스
  ├── step2_category_analyses.json    카테고리별 분석
  ├── step2_narrative_flow.md         서사 흐름 설계
  ├── step3_executive_summary.md      본문(섹션 조립)
  ├── step4_compiled.md               제목+본문+시사점 조립본 (윤문 전)
  ├── step4_final.md                  최종 백서 마크다운 (한국어)
  ├── 백서.docx                     ⭐ 완성 Word 백서 (자동 생성)
  └── proper_nouns.json               완성 문서에서 추출한 고유명사 (재사용용, 유형별 분류)
```

## 문서

| 파일 | 설명 |
|------|------|
| [STATUS.md](STATUS.md) | ⭐ 현재 상태 스냅샷 (최초 진입점) |
| [SPEC.md](SPEC.md) | v3.0 설계 스펙 |
| [HANDOFF.md](HANDOFF.md) | AI 에이전트 핸드오프 문서 |
| [LESSONS.md](LESSONS.md) | 누적 교훈 카드 (L-011 ~ L-020) |

## 라이선스

MIT
