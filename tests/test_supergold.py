from __future__ import annotations

import csv
import io
import json
from datetime import datetime

import pytest

from gwnetwork import supergold as sg


def _frame(sizes: dict, pairs_per_paper: int = 3) -> list:
    frame = []
    for h, n in sizes.items():
        for i in range(n):
            frame.append({"arxiv_id": f"{h}-{i:04d}", "stratum": h, "lvk": h == "lvk",
                          "year": 2021, "class": "other",
                          "pairs": [{"event": f"GW{k:06d}", "predicted_uses": [],
                                     "predicted_engagement": "cited_only",
                                     "reference_only": False}
                                    for k in range(pairs_per_paper)]})
    return frame


def test_paper_class_takes_the_rarest_product():
    assert sg.paper_class([["published_results"], ["strain_analysis"]]) == "strain"
    assert sg.paper_class([["strain_analysis", "posterior_samples"]]) == "samples"
    assert sg.paper_class([["qualitative"], []]) == "other"
    assert sg.stratum_of(True, 2016, "samples") == "lvk"
    assert sg.stratum_of(False, 2019, "samples") == "samples/pre2020"
    assert sg.stratum_of(False, None, "other") == "other/2020+"


def test_sample_weights_recover_the_frame():
    sizes = {"a": 900, "b": 80, "c": 20}
    s = sg.draw_sample(_frame(sizes), seed=1, n_uniform=10, min_per_stratum=5)
    # Uniform slice: proportional and summing to n_uniform.
    assert {h: st["n_uniform"] for h, st in s["strata"].items()} == {"a": 9, "b": 1, "c": 0}
    assert sum(p["uniform"] for p in s["papers"]) == 10
    # Every stratum at least the minimum; inverse-pi weights sum to the frame.
    assert {h: st["n"] for h, st in s["strata"].items()} == {"a": 9, "b": 5, "c": 5}
    assert sum(1 / p["pi"] for p in s["papers"]) == pytest.approx(1000)
    uni = [p for p in s["papers"] if p["uniform"]]
    assert all(p["pi_uniform"] == pytest.approx(9 / 900 if p["stratum"] == "a" else 1 / 80)
               for p in uni)
    # Same seed, same sample.
    assert s == sg.draw_sample(_frame(sizes), seed=1, n_uniform=10, min_per_stratum=5)


def test_large_papers_are_subsampled_with_the_rate_recorded():
    s = sg.draw_sample(_frame({"a": 3}, pairs_per_paper=30), seed=0, n_uniform=0,
                       min_per_stratum=3, max_pairs=12)
    for p in s["papers"]:
        assert len(p["pairs"]) == 12 and p["n_pairs_in_paper"] == 30
        assert p["pair_pi"] == pytest.approx(12 / 30)


def test_queues_spread_whoever_takes_part():
    from collections import Counter
    ids = [f"p{i}" for i in range(181)]
    qs = sg.make_queues(ids, 50, seed=0)
    assert all(sorted(q) == sorted(ids) for q in qs)
    assert qs == sg.make_queues(ids, 50, seed=0)
    # However many of the tokens are used (handed out in order), about one
    # pass over the papers covers most of them and double-labels about a third.
    for m, k in [(5, 40), (10, 20), (20, 10), (50, 5)]:
        c = Counter(p for q in qs[:m] for p in q[:k])
        assert len(c) >= 0.6 * m * k
        assert 0.25 <= sum(v >= 2 for v in c.values()) / len(c) <= 0.5
    # Reviewer r's 4th paper is reviewer r-1's 1st; the pilot shares nothing.
    assert qs[1][3] == qs[0][0] and qs[5][3] == qs[4][0]


def _fixtures():
    sample = {"gold_version": 3, "guide_version": "3.1", "classify_version": "classify-0.4",
              "classify_run_id": "deadbeef-0000", "release": "v2", "frame_size": 100,
              "strata": {"a": {"N": 90, "n": 2, "n_uniform": 1},
                         "b": {"N": 10, "n": 1, "n_uniform": 0}},
              "papers": [
                  {"arxiv_id": "2401.00001", "stratum": "a", "lvk": False, "year": 2024,
                   "pi": 2 / 90, "uniform": True, "pi_uniform": 1 / 90, "pair_pi": 1.0,
                   "pairs": [{"event": "GW150914", "predicted_uses": ["qualitative"],
                              "predicted_engagement": "qualitative", "reference_only": False},
                             {"event": "GW170817", "predicted_uses": ["published_results"],
                              "predicted_engagement": "published_results",
                              "reference_only": False}]},
                  {"arxiv_id": "2401.00002", "stratum": "a", "lvk": False, "year": 2024,
                   "pi": 2 / 90, "uniform": False, "pi_uniform": None, "pair_pi": 1.0,
                   "pairs": [{"event": "GW150914", "predicted_uses": [],
                              "predicted_engagement": "cited_only", "reference_only": True}]},
                  {"arxiv_id": "1801.00003", "stratum": "b", "lvk": False, "year": 2018,
                   "pi": 1 / 10, "uniform": False, "pi_uniform": None, "pair_pi": 1.0,
                   "pairs": [{"event": "GW170817", "predicted_uses": ["posterior_samples"],
                              "predicted_engagement": "posterior_samples",
                              "reference_only": False}]}],
              "calibration": [{"arxiv_id": "1602.03840", "pairs": [
                  {"event": "GW150914", "uses": ["strain_analysis"],
                   "engagement": "strain_analysis"}]}]}
    assignments = {"calibration": ["1602.03840"], "reviewers": [
        {"token": "aaaa1111", "queue": ["1602.03840", "2401.00001", "2401.00002", "1801.00003"]},
        {"token": "bbbb2222", "queue": ["1602.03840", "2401.00002", "2401.00001", "1801.00003"]}]}
    return sample, assignments


def _payload(tok, aid, pairs, when, **kw):
    return {"v": 1, "token": tok, "arxiv_id": aid, "seconds": 300, "author": False,
            "comment": "", "events_listed": None, "site": "abc1234", "guide": "3.1",
            "gold_version": 3, "submitted_at": when, "skipped": None,
            "pairs": [{"event": e, "uses": u, "confidence": c, "which": ""}
                      for e, u, c in pairs], **kw}


def _csv(payloads) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["received_at", "token", "arxiv_id", "payload"])
    for p in payloads:
        w.writerow([p["submitted_at"], p["token"], p["arxiv_id"], json.dumps(p)])
    return buf.getvalue()


def test_import_validates_dedupes_and_reports(tmp_path):
    from gwnetwork.evalset import load_gold
    sample, assignments = _fixtures()
    subs = [
        _payload("aaaa1111", "1602.03840", [("GW150914", ["strain_analysis"], "clear")],
                 "2026-11-20T10:00:00Z"),
        # Superseded by the resubmission below.
        _payload("aaaa1111", "2401.00001", [("GW150914", ["cited_only"], "clear"),
                                            ("GW170817", ["cited_only"], "clear")],
                 "2026-11-20T10:05:00Z"),
        _payload("aaaa1111", "2401.00001", [("GW150914", ["qualitative"], "clear"),
                                            ("GW170817", ["published_results"], "probably")],
                 "2026-11-20T10:09:00Z", events_listed="GW150914\nGW170817"),
        _payload("bbbb2222", "2401.00001", [("GW150914", ["qualitative"], "ambiguous"),
                                            ("GW170817", ["posterior_samples"], "clear")],
                 "2026-11-20T11:00:00Z"),
        _payload("bbbb2222", "2401.00002", [], "2026-11-20T11:10:00Z", skipped="conflict"),
        _payload("cccc3333", "2401.00001", [("GW150914", ["qualitative"], "clear")],
                 "2026-11-20T11:20:00Z"),                       # unknown token
        _payload("bbbb2222", "2401.00001", [("GW999999", ["qualitative"], "clear")],
                 "2026-11-20T09:00:00Z"),                       # older: ignored
    ]
    sheet = tmp_path / "sheet.csv"
    sheet.write_text(_csv(subs))
    backup = tmp_path / "answers.json"                   # 'download my answers'
    backup.write_text(json.dumps([_payload(
        "bbbb2222", "1801.00003", [("GW170817", ["posterior_samples"], "clear")],
        "2026-11-21T08:00:00Z")]))

    labels, papers = tmp_path / "labels.jsonl", tmp_path / "papers.jsonl"
    stats = sg.import_submissions([sheet, backup], sample, assignments, labels, papers)
    assert stats["unknown token"] == 1 and stats["superseded"] == 2
    rows = [json.loads(l) for l in labels.read_text().splitlines()]
    assert len(rows) == 6 and stats["pairs"] == 6
    a = {(r["reviewer"], r["arxiv_id"], r["event"]): r for r in rows}
    r = a[("aaaa1111", "2401.00001", "GW170817")]
    assert r["engagement"] == "published_results" and r["confidence"] == "probably"
    assert r["predicted_engagement"] == "published_results" and r["stratum"] == "a"
    assert a[("bbbb2222", "2401.00001", "GW150914")]["unsure"] is True
    assert a[("aaaa1111", "1602.03840", "GW150914")]["calibration_engagement"] == "strain_analysis"
    ps = [json.loads(l) for l in papers.read_text().splitlines()]
    assert {p["skipped"] for p in ps} == {None, "conflict"}
    # Labels load like any gold file.
    assert len(load_gold(labels)) == 6

    rep = str(sg.report(sample, [json.loads(l) for l in labels.read_text().splitlines()], ps))
    assert "calibration 1/1" in rep
    assert "pairs compared: 2" in rep                  # double-labelled pairs
    assert "1 paper submissions list events" in rep
    assert "roles:" not in rep


def test_frame_and_site_from_the_database(temp_db, tmp_path):
    from gwnetwork.models import AnalysisRun, Event, EventUsage, Mention, Paper
    from gwnetwork.web.supergold_site import build_site

    text = "Intro. We use GW150914 and its released samples. " + "x" * 2000 + " SECRET-BODY"
    with temp_db.session() as s:
        ev = Event(canonical_name="GW150914")
        x = AnalysisRun(stage="extract", pipeline_version="extract-t", is_current=True)
        c = AnalysisRun(stage="classify", pipeline_version="classify-t", is_current=True)
        s.add_all([ev, x, c])
        s.flush()
        for i, (year, uses, authors) in enumerate([
                (2016, ["posterior_samples"], ["A"]),
                (2022, ["qualitative"], ["B"]),
                (2017, ["strain_analysis"], ["LIGO Scientific Collaboration"])]):
            f = tmp_path / f"{i}.txt"
            f.write_text(text)
            p = Paper(arxiv_id=f"2401.{i:05d}", title=f"Paper {i}", abstract="abs",
                      authors=authors, submitted_at=datetime(year, 5, 1),
                      fulltext_path=str(f), fulltext_source="arxiv_html")
            s.add(p)
            s.flush()
            s.add(Mention(paper_id=p.id, event_id=ev.id, analysis_run_id=x.id,
                          matched_text="GW150914", char_start=14, char_end=22,
                          section="introduction", sentence="We use GW150914."))
            s.add(EventUsage(paper_id=p.id, event_id=ev.id, analysis_run_id=c.id,
                             engagement=uses[0], uses=uses))

    frame, meta = sg.build_frame(cutoff=None)
    assert {p["arxiv_id"]: p["stratum"] for p in frame} == {
        "2401.00000": "samples/pre2020", "2401.00001": "other/2020+", "2401.00002": "lvk"}
    assert meta["classify_version"] == "classify-t"

    sample = {"gold_version": 3, "guide_version": "3.1",
              **sg.draw_sample(frame, n_uniform=1, min_per_stratum=1),
              "calibration": [{"arxiv_id": "2401.00002", "pairs": [
                  {"event": "GW150914", "uses": ["strain_analysis"],
                   "engagement": "strain_analysis"}]}]}
    assignments = {"reviewers": [{"token": "aaaa1111",
                                  "queue": ["2401.00002", "2401.00000", "2401.00001"]}]}
    out = tmp_path / "site"
    stats = build_site(out, endpoint="https://example.invalid/exec",
                       sample=sample, assignments=assignments)
    assert stats["papers"] == 4                        # 3 sampled + 1 calibration
    page = (out / "p" / "2401.00000.html").read_text()
    assert "GW150914" in page and "noindex" in page
    assert "SECRET-BODY" not in page                   # snippets, not the text
    assert '"answers"' in (out / "p" / "2401.00002.html").read_text()
    assert json.loads((out / "q" / "aaaa1111.json").read_text())["queue"][0]["slug"] == "2401.00002"
    assert "example.invalid" in (out / "config.js").read_text()
    for f in ("index.html", "guide.html", "sg.js", "robots.txt"):
        assert (out / f).exists()
