"""
파이프라인 실행 진입점 — v3.0 (KR-first, category-based).

사용법:
    python -m main                              # 전체 실행
    python -m main --skip-fact-check            # 팩트체크 생략
    python -m main --export-kb kb.json          # KB JSON 추출
    python -m main --resume output/... --resume-from step3

사전 준비:
    1. cp .env.example .env (필요 시 수정)
    2. python -m scripts.gen_dummy   # ./data/records.jsonl 없을 때만
    3. pip install -r requirements.txt
"""
from __future__ import annotations
import argparse
import os
import sys
import traceback as _tb
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.graph import build_graph, build_resume_graph
from src.nodes import LOCAL_DATA_PATH, set_skip_fact_check
from src.logger import reset_stats, summary, log_error
from src.llm import reset_504_state, set_default_reasoning
from src.artifacts import init_run_dir, set_run_dir, load_run_state, list_runs, save_json
from src.utils import export_knowledge_base


def parse_args():
    p = argparse.ArgumentParser(
        description="Deep Doc Pipeline v3.0 — Hybrid Whitepaper Generator (KR-first)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "resume 예시:\n"
            "  python -m main --resume output/20260609_150000 --resume-from step3\n"
            "  python -m main --resume output/20260609_150000 --resume-from step4\n"
            "  python -m main --resume output/20260609_150000 --resume-from polish\n"
            "\n"
            "실행 디렉토리 조회:\n"
            "  python -m main --list-runs\n"
        ),
    )
    p.add_argument("--output", default=None,
                   help="최종 백서 저장 경로 (기본: run_dir/step4_final.md)")
    p.add_argument("--export-kb", default=None, metavar="FILE",
                   help="Knowledge Base를 별도 JSON으로 추출")
    p.add_argument("--reasoning", choices=["high", "medium"], default="high",
                   help="LLM 추론 강도 (high: 기본, medium: 빠른 응답)")
    p.add_argument("--skip-fact-check", action="store_true",
                   help="팩트체크/환각 검증 생략 (빠른 실행)")
    p.add_argument("--resume", default=None, metavar="RUN_DIR",
                   help="이전 실행 디렉토리에서 재개")
    p.add_argument("--resume-from", default="step3",
                   choices=["step2", "step3", "step4", "polish"],
                   help="재개 시작 단계 (기본: step3)")
    p.add_argument("--list-runs", action="store_true",
                   help="output/ 하위 실행 디렉토리 목록 표시 후 종료")
    return p.parse_args()


def main():
    args = parse_args()

    # ── --list-runs 모드 ──
    if args.list_runs:
        runs = list_runs()
        if not runs:
            print("실행 기록 없음 (output/ 디렉토리가 비어있거나 없음)")
        else:
            print(f"실행 기록 ({len(runs)}건, 최신순):")
            for r in runs:
                files = list(r.glob("step*"))
                print(f"  {r.name}/  ({len(files)} artifacts)")
        return

    # ── 공통 초기화 ──
    reset_stats()
    reset_504_state()
    set_default_reasoning(args.reasoning)
    set_skip_fact_check(args.skip_fact_check)

    is_resume = args.resume is not None

    # ── Resume 모드 ──
    if is_resume:
        resume_dir = Path(args.resume)
        if not resume_dir.is_dir():
            print(f"[ERROR] 실행 디렉토리 없음: {resume_dir}")
            sys.exit(1)

        initial_state = load_run_state(resume_dir)
        set_run_dir(resume_dir)
        graph = build_resume_graph(args.resume_from)

        print("=" * 70)
        print(f"Deep Doc Pipeline v3.0 — RESUME from {args.resume_from}")
        print(f"실행 디렉토리: {resume_dir}")
        print(f"모델: {os.getenv('OPENAI_MODEL', 'gpt-oss:20b')}")
        skip_fc = "⚠️ 팩트체크 생략" if args.skip_fact_check else "팩트체크 ON"
        print(f"추론: {args.reasoning} | {skip_fc}")
        print("=" * 70)

    # ── 전체 실행 모드 ──
    else:
        if not Path(LOCAL_DATA_PATH).exists():
            print(f"[ERROR] {LOCAL_DATA_PATH} 가 없습니다.")
            print("  먼저 실행: python -m scripts.gen_dummy")
            sys.exit(1)

        run_dir = init_run_dir()

        initial_state = {
            "raw_docs": [],
            "knowledge_entries": [],
            "knowledge_base": {},
            "temporal_index": [],
            "category_analyses": {},
            "completed_sections": {},
            "unverified_sections": [],
            "hallucinated_tokens": [],
            "narrative_retry_count": 0,
            "section_retry_count": 0,
        }
        graph = build_graph()

        print("=" * 70)
        print("Deep Doc Pipeline v3.0 — Hybrid Whitepaper Generator (KR-first)")
        print(f"모델: {os.getenv('OPENAI_MODEL', 'gpt-oss-20b')} @ "
              f"{os.getenv('OPENAI_BASE_URL', 'http://localhost:11434/v1')}")
        skip_fc = "⚠️ 팩트체크 생략" if args.skip_fact_check else "팩트체크 ON"
        print(f"추론: {args.reasoning} | {skip_fc} | 504 2회 초과 시 medium 자동 전환")
        print(f"산출물: {run_dir}/")
        print("=" * 70)

    # ── 그래프 실행 ──
    try:
        final_state = graph.invoke(initial_state, config={"recursion_limit": 200})
    except Exception as _e:
        log_error("graph.invoke", _e, _tb.format_exc())
        print(f"\n[ERROR] 파이프라인 실행 실패: {_e}")
        sys.exit(1)

    final = final_state.get("final_output", "(빈 결과)")

    # ── KB 내보내기 (--export-kb) ──
    if args.export_kb:
        kb = final_state.get("knowledge_base", {})
        if kb:
            kb_export = export_knowledge_base(kb)
            kb_path = Path(args.export_kb)
            kb_path.parent.mkdir(parents=True, exist_ok=True)
            import json
            kb_path.write_text(
                json.dumps(kb_export, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  KB 내보내기 → {kb_path.resolve()}")

    # ── 최종 파일 저장 (--output 지정 시 추가 복사) ──
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(final, encoding="utf-8")
        print(f"  추가 저장 → {out_path.resolve()}")

    # ── 실행 통계 ──
    stats = summary()
    kb = final_state.get("knowledge_base", {})
    ti = final_state.get("temporal_index", [])

    print()
    print("=" * 70)
    print("✅ 파이프라인 완료")
    print("=" * 70)
    print(f"  총 소요 시간 : {stats['elapsed']}")
    print(f"  완료 작업 수 : {stats['nodes']}건")
    print(f"  LLM API 호출 : {stats['llm_calls']}건")
    print("-" * 70)
    if not is_resume:
        cat_counts = {c: len(entries) for c, entries in kb.items()} if kb else {}
        print(f"  지식 엔트리  : {sum(cat_counts.values())}건")
        for cat, cnt in cat_counts.items():
            print(f"    {cat}: {cnt}건")
        print(f"  시간순 인덱스 : {len(ti)}건 (dated: {sum(1 for t in ti if t.get('period') != 'undated')})")
        print(f"  완성 섹션    : {len(final_state.get('completed_sections', {}))}개")
        unv = final_state.get("unverified_sections", [])
        if unv:
            print(f"  ⚠️ 미검증 섹션 : {sorted(unv)}")
    print(f"  최종 백서    : {len(final):,} chars")
    print("=" * 70)


if __name__ == "__main__":
    main()
