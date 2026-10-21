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
                               "fallback": "cited_only", "roles": ["population", "bogus"]},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/row/0")
    lines = gold.read_text().splitlines()
    assert lines[0] == "# keep me" and json.loads(lines[1]) == a
    saved = json.loads(lines[2])
    assert (saved["engagement"], saved["uses"], saved["roles"]) == ("cited_only", [], ["population"])

    # Several uses at once; Q4 is ignored when a data product is ticked.
    c.post("/row/1", data={"arxiv_id": b["arxiv_id"], "event": "GW150914",
                           "uses": ["published_results", "strain_analysis"],
                           "fallback": "qualitative", "unsure": "1"})
    saved = json.loads(gold.read_text().splitlines()[2])
    assert saved["uses"] == ["strain_analysis", "published_results"]
    assert saved["engagement"] == "strain_analysis" and saved["unsure"] is True

    # No data product and no Q4 answer is refused, not saved as a guess.
    r = c.post("/row/0", data={"arxiv_id": a["arxiv_id"], "event": "GW150914"})
    assert r.status_code == 400

    # A page for a different pair than the file now holds must not write.
    r = c.post("/row/0", data={"arxiv_id": "9999.99999", "event": "GW150914",
                               "fallback": "cited_only"})
    assert r.status_code == 409


def test_compare_reports_per_use_agreement(tmp_path):
    from gwnetwork.evalset import compare

    def write(path, rows):
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    base = {"event": "GW150914", "roles": []}
    ref, other = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    write(ref, [{**base, "arxiv_id": "1", "engagement": "strain_analysis",
                 "uses": ["strain_analysis", "posterior_samples"]},
                {**base, "arxiv_id": "2", "engagement": "cited_only", "uses": []},
                # an old-format row: uses derived from engagement
                {**base, "arxiv_id": "3", "engagement": "qualitative"}])
    write(other, [{**base, "arxiv_id": "1", "engagement": "strain_analysis",
                   "uses": ["strain_analysis"]},
                  {**base, "arxiv_id": "2", "engagement": "qualitative", "uses": ["qualitative"]},
                  {**base, "arxiv_id": "3", "engagement": "qualitative", "uses": ["qualitative"]}])
    ag = compare(ref, other)
    assert ag.n == 3 and ag.engagement_same == 2
    assert ag.use_counts["posterior_samples"] == [0, 0, 1]     # missed by "other"
    assert (ag.fallback_same, ag.fallback_n) == (1, 2)
    assert len(ag.disagreements) == 2


def test_guidelines_quote_the_classifier_questions_verbatim():
    """Humans and the model must answer the same questions."""
    from gwnetwork.classify.prompts import DATA_QUESTIONS, FALLBACK_QUESTION
    from gwnetwork.evalset import GOLD_PATH
    guide = (GOLD_PATH.parent / "GUIDELINES.md").read_text()
    for _, q, detail in DATA_QUESTIONS + [FALLBACK_QUESTION]:
        assert q in guide and detail in guide


def test_cli_loads_every_command():
    """A NameError in one command's signature takes down the whole `gwn` CLI."""
    from typer.testing import CliRunner
    from gwnetwork.cli import app
    for args in (["--help"], ["eval", "compare", "--help"], ["eval", "label", "--help"]):
        assert CliRunner().invoke(app, args).exit_code == 0, args


def test_review_mode_lists_only_disputed_pairs(temp_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import gwnetwork.web.label as label
    _seed(temp_db, n_papers=2)
    row = lambda i, uses, roles: {"arxiv_id": f"2401.0000{i}", "event": "GW150914",
                                  "engagement": "x" if uses is not None else None,
                                  "uses": uses or [], "roles": roles}
    gold, other = tmp_path / "gold.jsonl", tmp_path / "other.jsonl"
    gold.write_text(json.dumps(row(0, ["qualitative"], [])) + "\n" +
                    json.dumps(row(1, ["published_results"], ["cosmology"])) + "\n")
    other.write_text(json.dumps({**row(0, ["qualitative"], []), "note": "same"}) + "\n" +
                     json.dumps({**row(1, ["strain_analysis", "published_results"],
                                       ["cosmology"]), "note": "also reanalyses"}) + "\n")
    monkeypatch.setattr(label, "GOLD_PATH", gold)
    monkeypatch.setattr(label, "COMPARE_PATH", other)
    c = TestClient(label.app)
    page = c.get("/review").text
    assert "1 disputed pair" in page and "also reanalyses" in page and "same" not in page
    assert "Disputed" in c.get("/row/1").text and "Disputed" not in c.get("/row/0").text
