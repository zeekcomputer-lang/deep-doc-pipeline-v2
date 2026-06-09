# Deep Doc Pipeline v3

> Raw 프로젝트 데이터 → **하이브리드 백서** (Executive Summary + Chronological Appendix)

[![Origin](https://img.shields.io/badge/fork-deep--doc--pipeline%20v2.0-blue)](https://github.com/zeekcomputer-lang/deep-doc-pipeline)
[![Repo](https://img.shields.io/badge/repo-deep--doc--pipeline--v2-green)](https://github.com/zeekcomputer-lang/deep-doc-pipeline-v2)

## 개요

LangGraph + OpenAI SDK + Pydantic 기반 문서 생성 파이프라인.
JSONL 원본 데이터를 **카테고리별 지식 구조화** → **서사 설계** → **섹션 집필** → **최종 백서**로 변환합니다.

### v3 핵심 변경점

- **카테고리 우선 지식 구조화** — 4개 축으로 분류 후 분석
  - `Architecture_and_Tech` · `Risk_and_Troubleshooting` · `Business_and_Feature` · `Lessons_Learned`
- **날짜 비의존(Date-resilient)** — 명확한 날짜 마커 없이도 동작
- **지식 베이스 내보내기** — JSON / RDB-ready 포맷
- **하이브리드 출력** — Executive Summary(비즈니스 인사이트) + Chronological Appendix(타임라인)
- **한국어 직접 출력 (KR-first)** — 번역 단계 없이 한국어로 직접 생성, 고유명사만 원어 보존
- **커스텀 프롬프트 주입** — `prompt_config.py`에서 도메인별 프롬프트 조정
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
    ├── llm.py               OpenAI SDK 클라이언트
    ├── logger.py            타임라인 로거
    ├── utils.py             순수 Python 유틸리티
    ├── nodes.py             파이프라인 노드
    └── graph.py             LangGraph 그래프 조립
```

## 빠른 시작

```bash
pip install -r requirements.txt
cp .env.example .env          # OPENAI_API_KEY 등 설정
python -m scripts.gen_dummy   # 더미 데이터 생성
python -m main                # 파이프라인 실행
```

### CLI 옵션

```bash
python -m main --export-kb kb.json        # 지식 베이스 JSON 내보내기
python -m main --reasoning medium         # 추론 수준 조절
python -m main --resume output/<dir> --resume-from translate   # 중간 지점부터 재개
```

## 파이프라인 그래프

```
load_docs → knowledge_extractor(×N) → knowledge_aggregator → temporal_indexer
         → category_analyzer(×4) → narrative_planner ⟲ narrative_critique
         → section_writer → save_section ⟲ route_next_section
         → timeline_formatter → compiler → polish → END
```

## 출력물

```
output/<timestamp>/
  ├── step1_knowledge_base.json       지식 베이스
  ├── step2_narrative_flow.md         서사 흐름 설계
  ├── step3_executive_summary.md      Executive Summary
  └── step4_final.md                  최종 백서 (한국어, 고유명사 원어)
```

## 문서

| 파일 | 설명 |
|------|------|
| [SPEC.md](SPEC.md) | v3.0 설계 스펙 |
| [HANDOFF.md](HANDOFF.md) | AI 에이전트 핸드오프 문서 |
| [LESSONS.md](LESSONS.md) | 누적 교훈 카드 (L-011 ~ L-020+) |

## 라이선스

MIT
