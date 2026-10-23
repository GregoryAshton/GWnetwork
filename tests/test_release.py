"""The slim release database."""
from __future__ import annotations

import sqlite3


def test_export_keeps_current_runs_and_relative_paths(temp_db, tmp_path, monkeypatch):
    from gwnetwork.config import CONFIG
    from gwnetwork.models import AnalysisRun, Event, EventUsage, Mention, Paper
    from gwnetwork.release import export_slim
    ft = tmp_path / "fulltext"
    monkeypatch.setattr(CONFIG, "fulltext_dir", ft)
    with temp_db.session() as s:
        e = Event(canonical_name="GW150914")
        p = Paper(arxiv_id="1602.03837", title="t", abstract="",
                  fulltext_path=str(ft.resolve() / "1602" / "1602.03837.txt"))
        runs = {(stage, cur): AnalysisRun(stage=stage, pipeline_version="x", is_current=cur)
                for stage in ("extract", "classify") for cur in (True, False)}
        s.add_all([e, p, *runs.values()])
        s.flush()
        for cur in (True, False):
            s.add(Mention(paper_id=p.id, event_id=e.id, analysis_run_id=runs["extract", cur].id,
                          matched_text="GW150914", char_start=0, char_end=8))
            s.add(EventUsage(paper_id=p.id, event_id=e.id, engagement="cited_only",
                             analysis_run_id=runs["classify", cur].id, evidence=[]))
    out = tmp_path / "release" / "slim.db"
    stats = export_slim(out, "test", allow_dirty=True)
    assert (stats["mention"], stats["event_usage"], stats["analysis_run"]) == (1, 1, 2)
    con = sqlite3.connect(out)
    assert con.execute("select fulltext_path from paper").fetchone()[0] == "1602/1602.03837.txt"
    assert con.execute("select count(*) from analysis_run where not is_current").fetchone()[0] == 0
    info = dict(con.execute("select key, value from release_info"))
    assert info["release"] == "test" and info["extract_run_id"] and info["classify_run_id"]
