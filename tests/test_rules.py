from __future__ import annotations

from dataclasses import dataclass

from gwnetwork.classify.rules import classify_engagement
from gwnetwork.models import Engagement


@dataclass
class FakeMention:
    section: str
    sentence: str
    char_start: int = 0
    char_end: int = 8


def test_single_intro_mention_still_goes_to_the_llm():
    """It may be qualitative use, which only reading can tell (classify-0.3)."""
    v = classify_engagement([FakeMention(
        "introduction", "GW150914 revealed that ~30 Msun black holes exist in binaries.")])
    assert v.engagement == Engagement.cited_only.value  # a hint, not a verdict
    assert v.needs_llm is True
    assert v.evidence


def test_strain_language_in_methods_defers_to_llm():
    v = classify_engagement([
        FakeMention("methods", "We reanalyse the strain data for GW170817 from GWOSC."),
    ])
    assert v.engagement == Engagement.strain_analysis.value
    assert v.needs_llm is True


def test_posterior_samples_detected():
    v = classify_engagement([
        FakeMention("data", "We use the released posterior samples for GW190521."),
    ])
    assert v.engagement == Engagement.posterior_samples.value


def test_many_mentions_are_never_settled_by_rule():
    ms = [FakeMention("introduction", "GW150914 again.")] * 6
    assert classify_engagement(ms).needs_llm is True


def test_no_mentions_is_not_sent_to_llm():
    v = classify_engagement([])
    assert v.needs_llm is False and v.engagement is None


def test_evidence_is_always_attached_when_a_call_is_made():
    v = classify_engagement([FakeMention("results", "GW190814 posterior samples were reweighted.")])
    assert v.evidence and "quote" in v.evidence[0]


def test_classify_chunks_large_papers():
    from gwnetwork.classify.runner import MAX_EVENTS_PER_CALL, _chunks

    needs = [(i, []) for i in range(2 * MAX_EVENTS_PER_CALL + 3)]
    chunks = _chunks(needs)
    assert [len(c) for c in chunks] == [MAX_EVENTS_PER_CALL, MAX_EVENTS_PER_CALL, 3]
    assert [x for c in chunks for x in c] == needs
    assert _chunks([]) == []


def test_reference_only_mention_brings_the_citing_sentence():
    """The bibliography entry says nothing about use; the citing sentence does."""
    from gwnetwork.classify.runner import _excerpts

    text = ("Intro. We neglect any stochastic GW background [84]. Fine.\n\n"
            "References\n\n[84]\nLIGO, “GW150914: Implications for the stochastic background”.\n")
    at = text.index("GW150914")
    m = FakeMention("references", "GW150914: Implications for the stochastic background",
                    char_start=at, char_end=at + 8)
    m.event_id = "e1"
    out = _excerpts([m], {"e1": "GW150914"}, text)
    assert out[0]["section"] == "references"
    assert out[1] == {"section": "cites reference [84], which names GW150914",
                      "text": "We neglect any stochastic GW background [84]."}


def test_counterpart_name_is_flagged_and_gw_name_preferred():
    from gwnetwork.classify.runner import _excerpts

    def m(section, matched, sentence):
        x = FakeMention(section, sentence)
        x.event_id, x.matched_text = "e1", matched
        return x

    cps = frozenset({"GRB170817A", "AT2017GFO"})
    ms = [m("results", "GRB 170817A", "The GRB 170817A jet was off-axis."),
          m("results", "GW170817", "GW170817 gives the inclination.")]
    out = _excerpts(ms, {"e1": "GW170817"}, None, cps)
    assert out[0] == {"section": "results", "text": "GW170817 gives the inclination."}
    assert out[1]["section"] == ("results; names the counterpart GRB 170817A, "
                                 "not the GW signal of GW170817")


def test_llm_answers_become_a_set_of_uses_and_a_derived_engagement(temp_db):
    from gwnetwork.classify.runner import _apply_response
    from gwnetwork.models import EventUsage

    item = {"event_name": "GW150914", "strain_analysis": True, "posterior_samples": True,
            "published_results": False, "qualitative": True, "roles": ["single_event"],
            "confidence": 0.9, "evidence": [{"quote": "q"}]}
    with temp_db.session() as s:
        from gwnetwork.models import AnalysisRun, Event, Paper
        ev, p, run = Event(canonical_name="GW150914"), Paper(arxiv_id="1", title="t"), \
            AnalysisRun(stage="classify", pipeline_version="t")
        s.add_all([ev, p, run]); s.flush()
        assert _apply_response(s, run.id, p.id, [(ev.id, [])], {ev.id: "GW150914"},
                               {"events": [item]}) == 1
        s.flush()
        u = s.query(EventUsage).one()
        assert u.uses == ["strain_analysis", "posterior_samples"]   # no qualitative beside data
        assert u.engagement == "strain_analysis"


def test_fulltext_request_lists_aliases_and_marks_counterparts(tmp_path):
    from gwnetwork.classify.runner import _request_for
    from gwnetwork.models import Paper
    f = tmp_path / "p.txt"
    f.write_text("We reanalyse all events in Table 1. GRB 170817A was bright.")
    paper = Paper(arxiv_id="1", title="T", abstract="A", fulltext_path=str(f))
    m = FakeMention("methods", "x")
    req = _request_for(paper, [("e1", [m])], {"e1": "GW170817"}, frozenset(), "fulltext",
                       {"e1": [("GW170817", "gw_name"), ("GRB 170817A", "em_counterpart")]})
    content = req["messages"][0]["content"]
    assert "We reanalyse all events in Table 1." in content
    assert "GRB 170817A [EM counterpart, not the GW signal]" in content


def test_batches_split_under_the_size_limit(monkeypatch):
    import gwnetwork.classify.runner as runner

    created = []

    class Batches:
        def create(self, requests):
            created.append(len(requests)); return type("B", (), {"id": f"b{len(created)}"})()
        def retrieve(self, bid):
            return type("S", (), {"processing_status": "ended"})()
        def results(self, bid):
            return []

    monkeypatch.setattr(runner, "_client", lambda: type("C", (), {
        "messages": type("M", (), {"batches": Batches()})()})())
    monkeypatch.setattr(runner, "BATCH_MAX_BYTES", 2500)
    reqs = [(i, [], lambda: {"model": "m", "max_tokens": 1,
                             "messages": [{"role": "user", "content": "x" * 1000}]})
            for i in range(5)]
    runner._run_batch(reqs, poll_seconds=0)
    assert created == [2, 2, 1]
