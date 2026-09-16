from __future__ import annotations

from gwnetwork.extract.matcher import DesignationMatcher
from gwnetwork.extract.sections import SectionIndex, sentence_around

M = {
    "GW150914": "e-150914",
    "GW190521": "e-190521",
    "GW190521_074359": "e-190521b",
    "GW190814": "e-190814",
    "GW190814_192009": "e-190814b",
    "GW170817": "e-170817",
    "AT2017gfo": "e-170817",
}


def _find(text, mapping=M, ambiguous=None):
    return list(DesignationMatcher(mapping, ambiguous).find(text))


def test_longest_match_wins():
    """The trap: GW190521 and GW190521_074359 are different events."""
    hits = _find("We analyse GW190521_074359 in detail.")
    assert len(hits) == 1
    assert hits[0].text == "GW190521_074359"
    assert hits[0].event_id == "e-190521b"


def test_short_form_still_matches_its_own_event():
    hits = _find("The heaviest binary, GW190521, is notable.")
    assert [h.event_id for h in hits] == ["e-190521"]


def test_both_forms_in_one_sentence():
    hits = _find("We compare GW190814 with GW190814_192009.")
    assert [h.text for h in hits] == ["GW190814", "GW190814_192009"]
    assert hits[0].event_id != hits[1].event_id


def test_word_boundaries():
    assert _find("xGW150914") == []
    assert len(_find("GW150914-like signals")) == 1
    assert len(_find("(GW150914)")) == 1


def test_em_counterpart_alias_resolves_to_same_event():
    hits = _find("The kilonova AT2017gfo followed GW170817.")
    assert {h.event_id for h in hits} == {"e-170817"}


def test_unknown_event_is_surfaced_not_dropped():
    """The discovery channel for events we have never heard of."""
    hits = _find("A candidate GW251231_120000 was reported.")
    assert len(hits) == 1
    assert hits[0].event_id is None
    assert hits[0].text == "GW251231_120000"


def test_unknown_not_double_reported_when_known():
    hits = _find("GW150914 was the first.")
    assert len(hits) == 1


def test_ambiguous_flag_propagates():
    hits = _find("See GW150914.", ambiguous={"GW150914"})
    assert hits[0].is_ambiguous


def test_section_index_and_sentence():
    text = (
        "Title\n\nAbstract\n\nWe report GW150914.\n\n"
        "1. Introduction\n\nMany papers cite GW150914 here.\n\n"
        "2. Methods\n\nWe reanalyse the strain data for GW170817. It follows.\n"
    )
    idx = SectionIndex(text)
    a = text.index("GW150914")
    b = text.index("GW150914", a + 1)
    c = text.index("GW170817")
    assert idx.label_at(a) == "abstract"
    assert idx.label_at(b) == "introduction"
    assert idx.label_at(c) == "methods"
    assert sentence_around(text, c, c + 8) == "We reanalyse the strain data for GW170817."


def test_pdf_mangled_underscore_still_matches():
    """PDF extraction turns GW190521_074359 into 'GW190521 074359'."""
    for sep in [" ", "\n", " ", "-"]:
        hits = _find(f"Candidate GW190521{sep}074359 was analysed.")
        assert len(hits) == 1, sep
        assert hits[0].event_id == "e-190521b", sep


def test_mangled_form_does_not_shadow_the_short_event():
    hits = _find("We compare GW190521 with GW190814 192009 here.")
    assert [h.event_id for h in hits] == ["e-190521", "e-190814b"]


def test_normalise_canonicalises_unknowns():
    from gwnetwork.extract.matcher import normalise
    assert normalise("GW250101 120000") == "GW250101_120000"
    assert normalise("GW250101\n120000") == "GW250101_120000"
    assert normalise("GW250101_120000") == "GW250101_120000"


def test_marginal_candidates_have_no_gw_prefix():
    """GWOSC names 23 marginal candidates without a GW prefix, e.g. 200214_224526."""
    m = {"200214_224526": "e-marginal"}
    for sep in ["_", " ", "\n"]:
        hits = _find(f"The marginal candidate 200214{sep}224526 was rejected.", m)
        assert [h.event_id for h in hits] == ["e-marginal"], sep


def test_normalise_without_gw_prefix():
    from gwnetwork.extract.matcher import normalise
    assert normalise("200214 224526") == "200214_224526"


def test_word_designations_do_not_get_separator_tolerance():
    """GWOSC has an event named `blind_injection`.

    Blanket underscore tolerance made it match the ordinary English phrase
    "blind injection", which appears throughout the GW literature.
    """
    m = {"blind_injection": "e-bd"}
    assert _find("We also demonstrate, through blind injection analyses, that", m) == []
    assert len(_find("The blind_injection challenge was passed.", m)) == 1


def test_digit_separator_tolerance_still_works():
    m = {"GW190521_074359": "e-x"}
    assert len(_find("GW190521 074359", m)) == 1
