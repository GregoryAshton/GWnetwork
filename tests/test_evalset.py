from __future__ import annotations

import json

from gwnetwork.models import AnalysisRun, Event, Mention, Paper


def _seed(db, n_papers: int):
    with db.session() as s:
        ev = Event(canonical_name="GW150914")
        run = AnalysisRun(stage="extract", pipeline_version="v0", is_current=True)
        s.add_all([ev, run])
        s.flush()
        for i in range(n_papers):
            p = Paper(arxiv_id=f"2401.{i:05d}", title=f"Paper {i}", abstract="x",
                      fulltext_path=f"/tmp/{i}.txt")
            s.add(p)
            s.flush()
            s.add(Mention(paper_id=p.id, event_id=ev.id, analysis_run_id=run.id,
                          matched_text="GW150914", char_start=0, char_end=8,
                          section="introduction", sentence="GW150914 was first."))


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()
            if l.strip() and not l.startswith("#")]


def test_template_appends_and_never_touches_labels(temp_db, tmp_path):
    """Re-running the template must not wipe hand labels (it used to open 'w')."""
    from gwnetwork.evalset import write_template
    _seed(temp_db, n_papers=10)
    gold = tmp_path / "gold.jsonl"
    labelled = {"arxiv_id": "2401.00000", "event": "GW150914",
                "engagement": "cited_only", "roles": []}
    gold.write_text("# header\n" + json.dumps(labelled) + "\n")

    assert write_template(gold, n_papers=3) == 3
    rows = _rows(gold)
    assert rows[0] == labelled
    assert len(rows) == 4
    assert "2401.00000" not in {r["arxiv_id"] for r in rows[1:]}

    # Growing the set draws only papers not already present.
    write_template(gold, n_papers=100)
    ids = [r["arxiv_id"] for r in _rows(gold)]
    assert sorted(ids) == sorted(set(ids)) and len(ids) == 10


def test_labeller_saves_one_line_and_refuses_stale_pages(temp_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import gwnetwork.web.label as label
    _seed(temp_db, n_papers=2)
    gold = tmp_path / "gold.jsonl"
    a = {"arxiv_id": "2401.00000", "event": "GW150914", "engagement": None, "roles": []}
    b = {"arxiv_id": "2401.00001", "event": "GW150914", "engagement": None, "roles": []}
    gold.write_text("# keep me\n" + json.dumps(a) + "\n" + json.dumps(b) + "\n")
    monkeypatch.setattr(label, "GOLD_PATH", gold)
    c = TestClient(label.app)

    assert "GW150914" in c.get("/row/0").text
    assert c.get("/list").status_code == 200
    r = c.post("/row/1", data={"arxiv_id": b["arxiv_id"], "event": "GW150914",
                               "engagement": "cited_only", "roles": ["population", "bogus"]},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/row/0")
    lines = gold.read_text().splitlines()
    assert lines[0] == "# keep me" and json.loads(lines[1]) == a
    assert json.loads(lines[2])["engagement"] == "cited_only"
    assert json.loads(lines[2])["roles"] == ["population"]

    # A page for a different pair than the file now holds must not write.
    r = c.post("/row/0", data={"arxiv_id": "9999.99999", "event": "GW150914",
                               "engagement": "cited_only"})
    assert r.status_code == 409
