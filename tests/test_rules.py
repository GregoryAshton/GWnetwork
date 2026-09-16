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


def test_single_intro_mention_is_settled_without_an_llm():
    """The cost lever: this pair must never reach the model."""
    v = classify_engagement([FakeMention("introduction", "Since GW150914 was detected...")])
    assert v.engagement == Engagement.cited_only.value
    assert v.needs_llm is False
    assert v.evidence


def test_strain_language_in_methods_defers_to_llm():
    v = classify_engagement([
        FakeMention("methods", "We reanalyse the strain data for GW170817 from GWOSC."),
    ])
    assert v.engagement == Engagement.strain_reanalysis.value
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
