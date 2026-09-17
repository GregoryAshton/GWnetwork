"""Build and data provenance shown in the site footer.

Two different timestamps, and the distinction matters: the commit says which
code produced the page, the extract run says how old the data behind it is. A
static export can be months newer than the pipeline run it renders.
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), *args],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001  (git absent, not a repo, timeout)
        return None


@lru_cache(maxsize=1)
def build_info() -> dict:
    sha = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    return {
        "sha": sha,
        "dirty": dirty,
        "commit_date": _git("log", "-1", "--format=%cs"),
        "built_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "repo_url": "https://github.com/GregoryAshton/GWnetwork",
    }


def data_info(s) -> dict:
    """When the data behind the page was last produced."""
    from sqlalchemy import func, select

    from .models import AnalysisRun, Event, Paper

    run = s.scalar(select(AnalysisRun).where(
        AnalysisRun.stage == "extract",
        AnalysisRun.is_current == True))  # noqa: E712
    n_papers = s.scalar(select(func.count()).select_from(Paper)) or 0
    n_fulltext = s.scalar(select(func.count()).select_from(Paper)
                          .where(Paper.fulltext_path.is_not(None))) or 0
    return {
        "extracted_at": run.completed_at.strftime("%Y-%m-%d") if run and run.completed_at else None,
        "pipeline_version": run.pipeline_version if run else None,
        "n_events": s.scalar(select(func.count()).select_from(Event)) or 0,
        "n_papers": n_papers,
        "n_fulltext": n_fulltext,
        "pct_fulltext": round(100 * n_fulltext / n_papers) if n_papers else 0,
    }
