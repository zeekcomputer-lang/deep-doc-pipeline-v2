"""
중간 산출물 저장/로드 모듈 — v3.0 (step-based output).

실행마다 output/YYYYMMDD_HHMMSS/ 디렉토리를 생성하고,
각 Step 완료 시점의 산출물을 파일로 저장합니다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logger import plog

# ── 모듈 레벨 상태 ──────────────────────────────────────────
_run_dir: Optional[Path] = None


def init_run_dir(base: str = "output") -> Path:
    """실행 디렉토리 생성."""
    global _run_dir
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _run_dir = Path(base) / ts
    _run_dir.mkdir(parents=True, exist_ok=True)
    plog("artifacts", f"run dir: {_run_dir}")
    return _run_dir


def set_run_dir(path: Path) -> None:
    """외부에서 run_dir 직접 설정 (resume 시)."""
    global _run_dir
    _run_dir = path


def get_run_dir() -> Optional[Path]:
    return _run_dir


# ── 저장 함수 ────────────────────────────────────────────────

def save_json(name: str, data: Any) -> None:
    """JSON 아티팩트 저장."""
    if _run_dir is None:
        return
    path = _run_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_text(name: str, text: str) -> None:
    """텍스트 아티팩트 저장."""
    if _run_dir is None:
        return
    path = _run_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── 로드 함수 (resume용) ──────────────────────────────────────

def _load_json_safe(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_text_safe(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def load_run_state(run_dir: str | Path) -> Dict[str, Any]:
    """이전 실행 디렉토리에서 GraphState 복원.

    v3 step-based 산출물 파일명:
      step1_knowledge_base.json, step1_temporal_index.json
      step2_category_analyses.json, step2_narrative_flow.md
      step3_executive_summary.md (+ step3_sections/*.md)
      step4_appendix_timeline.md, step4_final.md
    """
    d = Path(run_dir)
    if not d.is_dir():
        raise FileNotFoundError(f"run directory not found: {d}")

    state: Dict[str, Any] = {
        "raw_docs": [],
        "knowledge_entries": [],
        "knowledge_base": {},
        "temporal_index": [],
        "category_analyses": {},
        "narrative_retry_count": 0,
        "completed_sections": {},
    }

    # Step 1
    kb = _load_json_safe(d / "step1_knowledge_base.json")
    if kb is not None:
        # KB export format has "categories" key
        if isinstance(kb, dict) and "categories" in kb:
            state["knowledge_base"] = kb["categories"]
        else:
            state["knowledge_base"] = kb

    ti = _load_json_safe(d / "step1_temporal_index.json")
    if ti is not None:
        state["temporal_index"] = ti

    # Step 2
    ca = _load_json_safe(d / "step2_category_analyses.json")
    if ca is not None:
        state["category_analyses"] = ca

    nf = _load_text_safe(d / "step2_narrative_flow.md")
    if nf is not None:
        state["narrative_flow"] = nf
        state["is_narrative_approved"] = True

    # Step 2 → Step 3: extract section_plan from narrative_flow JSON
    nf_json = _load_json_safe(d / "step2_narrative_flow.json")
    if nf_json is not None and "section_plan" in nf_json:
        state["executive_sections"] = nf_json["section_plan"]

    # Step 3 — sections
    sec_dir = d / "step3_sections"
    if sec_dir.is_dir():
        sections: Dict[int, str] = {}
        for f in sorted(sec_dir.glob("section_*.md")):
            try:
                idx = int(f.stem.split("_")[1])
                sections[idx] = f.read_text(encoding="utf-8")
            except (ValueError, IndexError):
                pass
        if sections:
            state["completed_sections"] = sections

    es = _load_text_safe(d / "step3_executive_summary.md")
    if es is not None:
        state["executive_summary"] = es

    # Step 4
    at = _load_text_safe(d / "step4_appendix_timeline.md")
    if at is not None:
        state["chronological_appendix"] = at

    final = _load_text_safe(d / "step4_final.md")
    if final is not None:
        state["final_output"] = final

    compiled = _load_text_safe(d / "step4_compiled.md")
    if compiled is not None:
        state["final_compiled"] = compiled

    plog("artifacts", f"loaded from {d}: "
         f"kb_cats={len(state.get('knowledge_base', {}))} "
         f"temporal={len(state.get('temporal_index', []))} "
         f"sections={len(state.get('completed_sections', {}))}")

    return state


def list_runs(base: str = "output") -> list[Path]:
    """output/ 하위 실행 디렉토리 목록 (최신순)."""
    base_dir = Path(base)
    if not base_dir.is_dir():
        return []
    return sorted(
        [d for d in base_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
        reverse=True,
    )
